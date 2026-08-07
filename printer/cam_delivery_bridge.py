#!/usr/bin/env python3
"""Fan-out H264 bridge from Creality deliveryStation to cloud + LAN.

cam_app publishes H264 Annex-B NAL units via the Unix socket
/tmp/delivery_socket100. This bridge subscribes to that topic and copies
every received NAL to:

- /tmp/uvc_fifo          -> /usr/bin/webrtc (Creality Cloud camera)
- /tmp/go2rtc_cam.fifo   -> go2rtc (LAN RTSP/WebRTC for Fluidd/Homebridge)

By making cam_app the single owner of /dev/video0 and pulling frames from its
deliveryStation output, both the Creality Cloud path and the LAN path get the
same live H264 feed without contending for the V4L2 device.
"""
import json
import logging
import os
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

ECS_VERSION = "8.11.0"
PROJECT_NAME = os.environ.get("PROJECT_NAME", "bridge")
SERVICE_NAME = f"{PROJECT_NAME}-cam-delivery-bridge"


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

DELIVERY_PATH = "/tmp/delivery_socket100"
# Subscription request observed from /usr/bin/webrtc client -> cam_app server.
# Format: two back-to-back registration messages for topic 0x1f7 (503).
SUBSCRIBE_REQ = bytes.fromhex(
    "0400000000000100f70100000800000001000100f701000010270000"
)
LAN_FIFO = "/tmp/go2rtc_cam.fifo"
CLOUD_FIFO = "/tmp/uvc_fifo"
CAMERA_MARKER = "/tmp/camera_main"


def _declare_camera_online():
    """Create the stock camera-ready marker and keep ubus camera_main online."""
    try:
        with open(CAMERA_MARKER, "a"):
            os.utime(CAMERA_MARKER, None)
    except Exception:
        pass
    try:
        subprocess.run(
            ["ubus", "call", "camera", "set_state", '{"type":0,"online":1}'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        pass


def _camera_heartbeat():
    while True:
        _declare_camera_online()
        time.sleep(10)


def _ensure_fifo(path):
    """Create path as a FIFO, replacing any stale non-FIFO file."""
    try:
        if os.path.exists(path):
            if not stat.S_ISFIFO(os.stat(path).st_mode):
                os.remove(path)
        os.mkfifo(path)
    except Exception:
        logger.exception("fifo setup failed", extra={"ecs": {"file.path": path}})


def _open_fifo(path):
    """Open a FIFO read-write + non-blocking.

    O_RDWR makes this process count as a reader, so writes never block waiting
    for a consumer to connect. Non-blocking lets us drop frames if a consumer's
    buffer is full instead of stalling the whole bridge.
    """
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    return os.fdopen(fd, "wb", 0)


def _write_all(fh, data):
    """Best-effort write; ignore broken pipes / full buffers."""
    try:
        fh.write(data)
    except BrokenPipeError:
        pass
    except BlockingIOError:
        pass


def delivery_loop(lan_fh, cloud_fh):
    """Connect to deliveryStation, parse length-prefixed messages, fan out to FIFOs."""
    buf = b""
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(DELIVERY_PATH)
            s.sendall(SUBSCRIBE_REQ)
            logger.info("Subscribed to delivery socket")
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 8:
                    length = struct.unpack("<I", buf[:4])[0]
                    if length > 2_000_000 or length < 0:
                        logger.warning("Bad delivery message length", extra={"ecs": {"error.message": str(length)}})
                        buf = b""
                        break
                    total = length + 8
                    if len(buf) < total:
                        break
                    payload = buf[8:total]
                    buf = buf[total:]
                    _write_all(lan_fh, payload)
                    _write_all(cloud_fh, payload)
        except Exception:
            logger.exception("Delivery loop error")
        time.sleep(2)


def main():
    _ensure_fifo(LAN_FIFO)
    _ensure_fifo(CLOUD_FIFO)
    _declare_camera_online()
    threading.Thread(target=_camera_heartbeat, daemon=True).start()
    logger.info(
        "Camera delivery bridge starting",
        extra={"ecs": {"file.path": f"{LAN_FIFO},{CLOUD_FIFO}"}},
    )

    # Both FIFOs are opened O_RDWR so consumers can connect/disconnect without
    # stalling the bridge. The bridge never reads from these fds, so the actual
    # consumers (go2rtc for LAN, /usr/bin/webrtc for cloud) get every frame.
    try:
        with _open_fifo(LAN_FIFO) as lan_fh, _open_fifo(CLOUD_FIFO) as cloud_fh:
            delivery_loop(lan_fh, cloud_fh)
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt")
    except Exception:
        logger.exception("Delivery bridge crashed")


if __name__ == "__main__":
    main()
