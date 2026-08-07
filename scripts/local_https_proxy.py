#!/usr/bin/env python3
"""Optional local HTTPS proxy for the Creality LAN printer.

The Creality Print desktop app adds printers by IP and only talks to them over
plain HTTP. This helper lets you point the app at an HTTPS URL anyway, by
running a small proxy on the same Mac/PC that:

  1. Accepts HTTPS from the Creality Print app (or any other HTTPS client).
  2. Forwards the request to the printer over HTTP or HTTPS.
  3. Returns the printer's response back over HTTPS.

It is intended to run on the client machine, not the printer. You give the
desktop app the proxy's local HTTPS address (for example
https://printer.lan:8443) instead of http://192.168.1.100, and the proxy
handles the back-and-forth to the real printer.

Two common modes:

  * Open printer mode: the printer still accepts plain HTTP on port 80. The
    proxy forwards to http://<printer>:80. This is useful for testing or when
    you want a local TLS entry point without closing the printer's HTTP door.
  * Proxy printer mode (installed with ./install.sh install --lan-mode proxy):
    the printer closes plain HTTP and only serves HTTPS. The proxy forwards to
    https://<printer>:443 and the desktop app is configured to point at the
    proxy's local HTTPS address.

Security notes:

  * In open mode the hop between this proxy and the printer is plain HTTP.
  * In proxy mode traffic is encrypted on every hop, provided the printer has
    a valid or trusted certificate. Self-signed printer certificates are
    accepted by default because this is intended for LAN use; use
    --verify-upstream to enforce verification.
  * The preferred path is still TLS termination directly on the printer via
    install.sh / install.sh cert, because that keeps traffic encrypted across
    the LAN without requiring a client-side proxy.
  * If you expose this proxy to other devices, anyone who can reach it can
    relay traffic to your printer; run it on localhost unless you understand
    the risks.

Usage:

  # Open mode: forward https://printer.lan:8443 -> http://192.168.1.100:80
  python3 scripts/local_https_proxy.py \
      --upstream http://192.168.1.100:80 \
      --listen 127.0.0.1:8443 \
      --cert ./certs/printer.lan.crt \
      --key ./certs/printer.lan.key

  # Proxy mode: forward https://printer.lan:8443 -> https://192.168.1.100:443
  python3 scripts/local_https_proxy.py \
      --upstream https://192.168.1.100:443 \
      --listen 127.0.0.1:8443 \
      --cert ./certs/printer.lan.crt \
      --key ./certs/printer.lan.key

  # Add the printer in Creality Print as https://printer.lan:8443

Environment:
  UPSTREAM            default upstream base URL
  LISTEN              default listen address
  CERT_FILE           default certificate path
  KEY_FILE            default private key path
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

LOGGER = logging.getLogger("local_https_proxy")


def parse_args():
    p = argparse.ArgumentParser(description="Local HTTPS proxy for a Creality LAN printer")
    p.add_argument("--upstream", default=os.environ.get("UPSTREAM", "http://192.168.1.100:80"), help="Upstream printer URL (http or https)")
    p.add_argument("--listen", default=os.environ.get("LISTEN", "127.0.0.1:8443"), help="Address:port to listen on")
    p.add_argument("--cert", default=os.environ.get("CERT_FILE", ""), help="TLS certificate file")
    p.add_argument("--key", default=os.environ.get("KEY_FILE", ""), help="TLS private key file")
    p.add_argument("--self-signed", action="store_true", help="Generate a self-signed cert on the fly (development only)")
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
    upstream_scheme = "http"
    upstream_host = "127.0.0.1"
    upstream_port = 80
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
        if self.upstream_port == 80 and self.upstream_scheme == "http":
            return self.upstream_host
        if self.upstream_port == 443 and self.upstream_scheme == "https":
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
        url = self._rewrite_url(self.path)
        parsed = urllib.parse.urlparse(url)

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


def make_self_signed_cert(hostname="localhost"):
    """Generate a temporary self-signed cert and return (cert_path, key_path)."""
    import tempfile
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime as dt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.utcnow() - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )

    tmpdir = tempfile.mkdtemp()
    cert_path = os.path.join(tmpdir, "cert.pem")
    key_path = os.path.join(tmpdir, "key.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    _, upstream_host, upstream_port, _ = split_url(args.upstream)
    listen_host, listen_port = args.listen.rsplit(":", 1)
    listen_port = int(listen_port)

    cert_file = args.cert
    key_file = args.key
    if args.self_signed:
        try:
            cert_file, key_file = make_self_signed_cert(listen_host)
        except ImportError:
            LOGGER.error("--self-signed requires the 'cryptography' package (pip install cryptography)")
            sys.exit(1)
    elif not cert_file or not key_file:
        LOGGER.error("--cert and --key are required unless --self-signed is used")
        sys.exit(1)

    ProxyHandler.upstream_scheme = upstream_scheme
    ProxyHandler.upstream_host = upstream_host
    ProxyHandler.upstream_port = upstream_port
    ProxyHandler.verify_upstream = args.verify_upstream

    server = http.server.ThreadingHTTPServer((listen_host, listen_port), ProxyHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    LOGGER.info("local HTTPS proxy listening on https://%s:%s -> %s", listen_host, listen_port, args.upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("shutting down")


if __name__ == "__main__":
    main()
