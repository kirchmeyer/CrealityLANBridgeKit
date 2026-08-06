#!/usr/bin/env python3
"""Bridge Creality's /call/webrtc_local contract to go2rtc WebRTC.

The stock Creality Print app (LAN/local mode) expects:

    POST http://{printer}:8000/call/webrtc_local
    Content-Type: plain/text
    <SDP offer>

    200 OK
    text/plain
    <base64({"type":"answer","sdp":"..."})>

This tiny server listens on port 8000, forwards the SDP offer to go2rtc's
/api/webrtc?src=camera endpoint, and returns the answer in the format the
app expects. No client patching required.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BIND = os.environ.get("WEBRTC_LOCAL_BIND", "0.0.0.0")
PORT = int(os.environ.get("WEBRTC_LOCAL_PORT", "8000"))
GO2RTC_URL = os.environ.get("GO2RTC_URL", "http://127.0.0.1:1984/api/webrtc?src=camera")
DEBUG_LOG = os.environ.get("WEBRTC_BRIDGE_DEBUG_LOG", "/tmp/webrtc_local_bridge.log")


def debug(msg):
    line = f"{msg}"
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        debug(fmt % args)

    def _send_text(self, body, status=200):
        if isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_cors_options(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._send_cors_options()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        debug(f"[GET] {path}")
        if path == "/call/webrtc_local":
            # Some clients probe first; return an empty JSON body like stock.
            self._send_text("{}")
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        offer = self.rfile.read(content_length) if content_length > 0 else b""
        debug(f"[POST] {path} len={content_length}")

        if path != "/call/webrtc_local":
            self.send_error(404, "Not Found")
            return

        if not offer:
            debug("empty offer; returning empty answer")
            self._send_text("{}")
            return

        try:
            answer = self._forward_to_go2rtc(offer)
        except Exception as exc:
            debug(f"forward error: {exc}")
            self._send_text("{}")
            return

        payload = json.dumps(answer).encode("utf-8")
        b64_payload = base64.b64encode(payload).decode("ascii")
        debug(f"answer sdp length={len(answer.get('sdp', ''))}")
        self._send_text(b64_payload)

    def _forward_to_go2rtc(self, offer_bytes):
        headers = {"Content-Type": "plain/text"}
        req = urllib.request.Request(
            GO2RTC_URL,
            data=offer_bytes,
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            # go2rtc returns the raw SDP answer as text/plain.
            sdp = resp.read().decode("utf-8", errors="replace")
        # The app expects {"type":"answer","sdp":"..."}
        return {"type": "answer", "sdp": sdp}


class ReuseAddrServer(HTTPServer):
    allow_reuse_address = True


def main():
    server = ReuseAddrServer((BIND, PORT), BridgeHandler)
    debug(f"webrtc_local_bridge listening on {BIND}:{PORT} -> {GO2RTC_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
