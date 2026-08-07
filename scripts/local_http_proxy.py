#!/usr/bin/env python3
"""Optional local HTTP-to-HTTPS proxy for the Creality LAN printer.

The Creality Print desktop app adds printers by IP and only talks to them over
plain HTTP. This helper lets you point the app at an HTTPS printer anyway, by
running a small proxy on the same Mac/PC that:

  1. Accepts plain HTTP from the Creality Print app (or any other HTTP client).
  2. Forwards the request to the printer over HTTPS.
  3. Returns the printer's response back over plain HTTP.

It is intended to run on the client machine, not the printer. You give the
desktop app the proxy's local HTTP address (for example
http://127.0.0.1:8080) instead of http://192.168.1.100, and the proxy
handles the TLS connection to the real printer.

Common mode:

  * The printer is installed in ``proxy`` LAN mode
    (``./install.sh install --lan-mode proxy``) and only serves HTTPS. The
    proxy forwards to ``https://<printer>:443`` while the desktop app points at
    the proxy's plain HTTP address. If the printer's certificate is self-signed
    or not trusted by the client machine, the proxy accepts it by default;
    use ``--verify-upstream`` to enforce verification.

Security notes:

  * Traffic between the Creality Print app and this proxy is plain HTTP. Run
    the proxy on ``127.0.0.1`` (the default) so only local processes can reach
    it.
  * Traffic between this proxy and the printer is HTTPS. Verification of the
    printer's certificate is disabled by default because self-signed certs are
    common on LANs; use ``--verify-upstream`` when you have a trusted cert.
  * If you expose this proxy to other devices, anyone who can reach it can
    relay traffic to your printer; run it on localhost unless you understand
    the risks.

Usage:

  # Forward http://127.0.0.1 -> https://printer.lan:443
  # (listening on port 80 requires root/privileges on macOS)
  python3 scripts/local_http_proxy.py \
      --upstream https://printer.lan:443 \
      --listen 127.0.0.1:80

  # Add the printer in Creality Print as http://127.0.0.1

Environment:
  UPSTREAM            default upstream base URL
  LISTEN              default listen address
  VERIFY_UPSTREAM     set to 1 to verify the printer's TLS certificate
"""
import argparse
import http.client
import http.server
import logging
import os
import select
import socket
import ssl
import sys
import threading
import urllib.parse

LOGGER = logging.getLogger("local_http_proxy")


def parse_args():
    p = argparse.ArgumentParser(description="Local HTTP-to-HTTPS proxy for a Creality LAN printer")
    p.add_argument("--upstream", default=os.environ.get("UPSTREAM", "https://printer.lan:443"), help="Upstream printer HTTPS URL")
    p.add_argument("--listen", default=os.environ.get("LISTEN", "127.0.0.1:80"), help="Address:port to listen on for plain HTTP")
    p.add_argument("--verify-upstream", action="store_true", default=os.environ.get("VERIFY_UPSTREAM", "0") == "1", help="Verify the upstream printer's TLS certificate")
    return p.parse_args()


def split_url(url):
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (80 if scheme == "http" else 443)
    path = parsed.path or "/"
    return scheme, host, port, path


def relay_sockets(client_sock, upstream_sock, idle_timeout=60):
    """Bidirectionally relay two sockets until both close or timeout."""
    sockets = [client_sock, upstream_sock]
    try:
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, idle_timeout)
            if exceptional:
                break
            if not readable:
                continue
            for src, dst in [(client_sock, upstream_sock), (upstream_sock, client_sock)]:
                if src in readable:
                    try:
                        data = src.recv(8192)
                    except OSError:
                        data = b""
                    if not data:
                        return
                    try:
                        dst.sendall(data)
                    except OSError:
                        return
    finally:
        for s in sockets:
            try:
                s.close()
            except OSError:
                pass


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_scheme = "https"
    upstream_host = "printer.lan"
    upstream_port = 443
    verify_upstream = False

    def log_message(self, fmt, *args):
        LOGGER.info(fmt % args)

    def _copy_headers(self, exclude=None):
        exclude = set(exclude or [])
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in exclude:
                continue
            headers[key] = value
        return headers

    def _upstream_host_header(self):
        """Return the Host header value to send upstream."""
        if self.upstream_port == 443 and self.upstream_scheme == "https":
            return self.upstream_host
        if self.upstream_port == 80 and self.upstream_scheme == "http":
            return self.upstream_host
        return f"{self.upstream_host}:{self.upstream_port}"

    def _rewrite_url(self, path):
        return f"{self.upstream_scheme}://{self.upstream_host}:{self.upstream_port}{path}"

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PUT(self):
        self._forward("PUT")

    def do_DELETE(self):
        self._forward("DELETE")

    def do_OPTIONS(self):
        self._forward("OPTIONS")

    def do_HEAD(self):
        self._forward("HEAD")

    def _forward(self, method):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else None

        headers = self._copy_headers(exclude={"host", "content-length"})
        headers["Host"] = self._upstream_host_header()
        if body is not None:
            headers["Content-Length"] = str(len(body))

        try:
            if self.upstream_scheme == "https":
                ctx = ssl.create_default_context()
                if not self.verify_upstream:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(self.upstream_host, self.upstream_port, timeout=30, context=ctx)
            else:
                conn = http.client.HTTPConnection(self.upstream_host, self.upstream_port, timeout=30)
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:
            LOGGER.error("upstream error: %s", exc)
            self.send_error(502, f"upstream error: {exc}")
            return

        # If this is a WebSocket upgrade request and the upstream agrees, switch
        # to raw socket relaying.
        upgrade = resp.getheader("Upgrade", "").lower()
        if upgrade == "websocket":
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                self.send_header(key, value)
            self.end_headers()
            client_sock = self.connection
            upstream_sock = conn.sock
            relay_sockets(client_sock, upstream_sock)
            return

        self.send_response(resp.status, resp.reason)
        for key, value in resp.getheaders():
            # Skip hop-by-hop headers that http.server should manage.
            if key.lower() in {"transfer-encoding", "connection", "keep-alive"}:
                continue
            self.send_header(key, value)
        self.end_headers()
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    upstream_scheme, upstream_host, upstream_port, _ = split_url(args.upstream)
    listen_host, listen_port = args.listen.rsplit(":", 1)
    listen_port = int(listen_port)

    if upstream_scheme != "https":
        LOGGER.warning("upstream is %s; this proxy is intended to forward to HTTPS", args.upstream)

    ProxyHandler.upstream_scheme = upstream_scheme
    ProxyHandler.upstream_host = upstream_host
    ProxyHandler.upstream_port = upstream_port
    ProxyHandler.verify_upstream = args.verify_upstream

    server = http.server.ThreadingHTTPServer((listen_host, listen_port), ProxyHandler)

    LOGGER.info("local HTTP proxy listening on http://%s:%s -> %s", listen_host, listen_port, args.upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
