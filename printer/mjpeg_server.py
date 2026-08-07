#!/usr/bin/env python3
"""Persistent MJPEG HTTP server for Creality camera.

Runs ffmpeg with -c:v copy and fans out MJPEG frames to multiple HTTP clients.
This works around ffmpeg's -listen mode which exits after a single connection.
"""
import http.server
import json
import logging
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

ECS_VERSION = "8.11.0"
PROJECT_NAME = os.environ.get("PROJECT_NAME", "bridge")
SERVICE_NAME = f"{PROJECT_NAME}-mjpeg-server"


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
    use_ecs = os.environ.get("ECS_LOGGING", "1").strip().lower() not in ("0", "false", "off", "no")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_EcsFormatter() if use_ecs else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]

    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger().error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _excepthook


_configure_logging()
logger = logging.getLogger(SERVICE_NAME)

BIND = os.environ.get("MJPEG_BIND", "127.0.0.1")
PORT = int(os.environ.get("MJPEG_PORT", "8081"))
SOURCE = os.environ.get("MJPEG_SOURCE", "rtsp://127.0.0.1:8554/camera")
DEVICE = os.environ.get("MJPEG_DEVICE", "")
WIDTH = int(os.environ.get("MJPEG_WIDTH", "1280"))
HEIGHT = int(os.environ.get("MJPEG_HEIGHT", "720"))
FPS = int(os.environ.get("MJPEG_FPS", "15"))
QUALITY = int(os.environ.get("MJPEG_QUALITY", "5"))

# Shared state
frame_lock = threading.Lock()
latest_frame = b""
frame_cond = threading.Condition(frame_lock)
clients_lock = threading.Lock()
client_count = 0


def find_jpeg_frames(stream):
    """Yield complete JPEG frames from a byte stream."""
    buf = b""
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            soi = buf.find(b"\xff\xd8")
            if soi == -1:
                buf = b""
                break
            eoi = buf.find(b"\xff\xd9", soi + 2)
            if eoi == -1:
                buf = buf[soi:]
                break
            frame = buf[soi:eoi + 2]
            buf = buf[eoi + 2:]
            yield frame


def ffmpeg_reader():
    global latest_frame
    # Prefer a direct v4l2 MJPEG device if MJPEG_DEVICE is set; otherwise
    # transcode the go2rtc RTSP H264 stream so the camera can be shared
    # between Homebridge (H264) and LAN app / Fluidd (MJPEG).
    if DEVICE:
        cmd = [
            "/usr/bin/ffmpeg",
            "-f", "v4l2",
            "-input_format", "mjpeg",
            "-video_size", f"{WIDTH}x{HEIGHT}",
            "-framerate", str(FPS),
            "-i", DEVICE,
            "-c:v", "copy",
            "-f", "mjpeg",
            "-q:v", str(QUALITY),
            "-",
        ]
    else:
        cmd = [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", SOURCE,
            "-c:v", "mjpeg",
            "-f", "mjpeg",
            "-q:v", str(QUALITY),
            "-",
        ]
    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            for frame in find_jpeg_frames(proc.stdout):
                with frame_cond:
                    latest_frame = frame
                    frame_cond.notify_all()
            proc.wait()
        except Exception:
            logger.exception("ffmpeg reader error")
        time.sleep(1)


class MJPEGHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress request logging
        pass

    def do_GET(self):
        global client_count
        # Accept exact paths or paths with query strings (e.g. Fluidd's
        # cacheBust parameter) so callers don't have to strip them first.
        if not (self.path.startswith("/cam.mjpg") or self.path.startswith("/cam.jpg")):
            self.send_error(404)
            return

        # Single-frame request vs stream
        accept = self.headers.get("Accept", "")
        is_snapshot = self.path.startswith("/cam.jpg") or ("multipart" not in accept and "image" in accept)
        if is_snapshot:
            with frame_lock:
                frame = latest_frame
            if not frame:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(frame)
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()

        with clients_lock:
            client_count += 1
        try:
            with frame_cond:
                # Start from most recent frame
                frame = latest_frame
            while True:
                if frame:
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                with frame_cond:
                    frame_cond.wait(timeout=5.0)
                    frame = latest_frame
        finally:
            with clients_lock:
                client_count -= 1

    def do_HEAD(self):
        if not self.path.startswith("/cam.mjpg"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()


class ReusableTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    reader = threading.Thread(target=ffmpeg_reader, daemon=True)
    reader.start()

    # Wait for first frame
    deadline = time.time() + 10
    while time.time() < deadline and not latest_frame:
        time.sleep(0.1)

    server = ReusableTCPServer((BIND, PORT), MJPEGHandler)
    logger.info(
        "MJPEG server listening",
        extra={"ecs": {"server.address": BIND, "server.port": PORT}},
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt")
    except Exception:
        logger.exception("Server crashed")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
