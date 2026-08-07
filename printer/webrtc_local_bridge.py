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
import logging
import os
import socket
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

ECS_VERSION = "8.11.0"
PROJECT_NAME = os.environ.get("PROJECT_NAME", "bridge")
SERVICE_NAME = f"{PROJECT_NAME}-webrtc-local-bridge"

BIND = os.environ.get("WEBRTC_LOCAL_BIND", "0.0.0.0")
PORT = int(os.environ.get("WEBRTC_LOCAL_PORT", "8000"))
GO2RTC_URL = os.environ.get("GO2RTC_URL", "http://127.0.0.1:1984/api/webrtc?src=camera")
DEBUG_LOG = os.environ.get("WEBRTC_BRIDGE_DEBUG_LOG", "/tmp/webrtc_local_bridge.log")


class _EcsFormatter(logging.Formatter):
    """Emit log records as Elastic Common Schema (ECS) JSON lines."""

    def format(self, record):
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        doc = {
            "@timestamp": ts,
            "ecs.version": ECS_VERSION,
            "log.level": record.levelname.lower(),
            "message": record.getMessage(),
            "event.dataset": f"{SERVICE_NAME}.log",
            "service.name": SERVICE_NAME,
            "service.version": "1.0.0",
            "host.name": socket.gethostname(),
            "process.pid": record.process,
            "process.thread.id": record.thread,
        }
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            doc["error.type"] = exc_type.__name__ if exc_type else None
            doc["error.message"] = str(exc_value) if exc_value else None
            doc["error.stack_trace"] = "".join(traceback.format_exception(*record.exc_info)).strip() if exc_type else None
        if hasattr(record, "ecs"):
            doc.update(record.ecs)
        return json.dumps(doc, separators=(",", ":"), default=str)


def _configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_EcsFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]

    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger().error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _excepthook


_configure_logging()
logger = logging.getLogger(SERVICE_NAME)


_ECS_DEBUG = os.environ.get("ECS_LOGGING", "1").strip().lower() not in ("0", "false", "off", "no")


def _ecs_debug_doc(msg):
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "@timestamp": ts,
        "ecs.version": ECS_VERSION,
        "log.level": "debug",
        "message": str(msg),
        "event.dataset": f"{SERVICE_NAME}.debug",
        "service.name": SERVICE_NAME,
        "service.version": "1.0.0",
        "host.name": socket.gethostname(),
        "process.pid": os.getpid(),
    }


def debug(msg):
    """Append a debug trace line to DEBUG_LOG (ECS JSON by default)."""
    try:
        line = json.dumps(_ecs_debug_doc(msg), separators=(",", ":"), default=str) if _ECS_DEBUG else str(msg)
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
            offer = self._normalize_offer(offer)
            # Save last normalized offer for diagnostics.
            try:
                with open("/tmp/webrtc_last_offer.sdp", "wb") as fh:
                    fh.write(offer)
            except Exception:
                pass
            answer = self._forward_to_go2rtc(offer)
        except Exception:
            logger.exception("Failed to forward WebRTC offer")
            self._send_text("{}")
            return

        payload = json.dumps(answer).encode("utf-8")
        b64_payload = base64.b64encode(payload).decode("ascii")
        debug(f"answer sdp length={len(answer.get('sdp', ''))}")
        self._send_text(b64_payload)

    def _normalize_offer(self, offer_bytes):
        """Return raw SDP bytes, decoding wrappers and keeping only H264."""
        # The macOS Creality Print native host wraps the SDP in a JSON object
        # and then base64-encodes the whole thing before POSTing.
        text = offer_bytes.decode("utf-8", errors="replace").strip()
        debug(f"offer first bytes: {text[:80]!r}")
        sdp_text = text
        if not sdp_text.startswith("v="):
            # Try base64 decoding (allow whitespace/newlines).
            try:
                cleaned = "".join(text.split())
                decoded = base64.b64decode(cleaned, validate=True)
                decoded_text = decoded.decode("utf-8", errors="replace")
                debug(f"base64 decoded first bytes: {decoded_text[:80]!r}")
                sdp_text = decoded_text
            except Exception:
                pass
        if not sdp_text.startswith("v="):
            # Some hosts send JSON {"sdp":"..."}; extract the sdp field.
            try:
                obj = json.loads(sdp_text)
                if isinstance(obj, dict) and "sdp" in obj:
                    extracted = obj["sdp"]
                    if isinstance(extracted, str):
                        debug(f"extracted SDP first bytes: {extracted[:80]!r}")
                        sdp_text = extracted
            except Exception as exc:
                debug(f"json parse failed: {exc}")
        # go2rtc rejects offers with multiple payload types/codecs. Keep H264
        # payload type 96 only (the app already filters fmtp but leaves the
        # payload type list intact, which confuses go2rtc).
        return self._h264_only_sdp(sdp_text).encode("utf-8")

    @staticmethod
    def _h264_only_sdp(sdp_text):
        lines = sdp_text.splitlines()
        out = []
        for line in lines:
            if line.startswith("m=video"):
                out.append("m=video 9 UDP/TLS/RTP/SAVPF 96")
            elif line.startswith("a=rtpmap:") and not line.startswith("a=rtpmap:96 "):
                continue
            elif line.startswith("a=rtcp-fb:") and not line.startswith("a=rtcp-fb:96 "):
                continue
            elif line.startswith("a=fmtp:") and not line.startswith("a=fmtp:96 "):
                continue
            elif line.startswith("a=ssrc-group:"):
                continue
            elif line.startswith("a=ssrc:"):
                continue
            else:
                out.append(line)
        return "\r\n".join(out) + "\r\n"

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
    logger.info(
        "WebRTC local bridge listening",
        extra={"ecs": {"server.address": BIND, "server.port": PORT, "url.full": GO2RTC_URL}},
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt")
    except Exception:
        logger.exception("Server crashed")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
