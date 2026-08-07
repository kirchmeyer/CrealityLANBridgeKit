#!/usr/bin/env python3
"""Printer status page for the Creality LAN bridge stack.

Served at /${PROJECT_NAME}-status/ via nginx (path is configurable via the
STATUS_PATH environment variable). The path prefix is deliberately unlikely to
clash with stock Creality, Fluidd, moonraker, or go2rtc routes.

Reflects the current printer-side stack:
  - nginx front door (:80 / :443)
  - lan_bridge (Creality app WebSocket compatibility, 127.0.0.1:9002 -> :9999)
  - Single-source camera stack:
      cam_app -> cam_delivery_bridge -> /tmp/uvc_fifo + /tmp/go2rtc_cam.fifo
      go2rtc (:8554 RTSP, :1984 API) -> mjpeg_server (:8081 MJPEG)
      webrtc_local_bridge (:8000) for Creality Print LAN camera
      /usr/bin/webrtc (/tmp/uvc_fifo consumer) -> stock Creality Cloud camera
  - moonraker (:7125) + klipper
  - app_cloud_only (stock app minus web-server; stock /etc/init.d/app disabled)

Logs are emitted as ECS-compliant JSON lines to stdout.
"""
import concurrent.futures
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
import urllib.parse
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ECS_VERSION = "8.11.0"
PROJECT_NAME = os.environ.get("PROJECT_NAME", "bridge")
STATUS_PATH = os.environ.get("STATUS_PATH", f"{PROJECT_NAME}-status")
SERVICE_NAME = f"{PROJECT_NAME}-status-page"

PORT = int(os.environ.get("STATUS_PORT", "8765"))
BIND = os.environ.get("STATUS_BIND", "127.0.0.1")

# Chamber/camera light control. Defaults are for the Creality K2 Plus; adapt
# LIGHT_ON_GCODE/LIGHT_OFF_GCODE for other printers or set LIGHT_MOONRAKER_URL
# to a custom endpoint.
LIGHT_ON_GCODE = os.environ.get("LIGHT_ON_GCODE", "SET_PIN PIN=LED VALUE=1")
LIGHT_OFF_GCODE = os.environ.get("LIGHT_OFF_GCODE", "SET_PIN PIN=LED VALUE=0")
LIGHT_MOONRAKER_URL = os.environ.get("LIGHT_MOONRAKER_URL", "http://127.0.0.1:7125")
LIGHT_QUERY_OBJECT = os.environ.get("LIGHT_QUERY_OBJECT", "output_pin LED")
NGINX_CERT_DIR = os.environ.get("NGINX_CERT_DIR", "/etc/nginx/conf.d")
NGINX_CERT_BASENAME = os.environ.get("NGINX_CERT_BASENAME", "self-signed")


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


def run(cmd, timeout=1):
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception as exc:
        return f"error: {exc}"


def is_listening(port, proto="tcp"):
    # Pure-python connect probe: avoids the fork overhead of netstat/grep on a
    # busy embedded system. All ports we probe are TCP services.
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.25)
        s.close()
        return True
    except Exception:
        return False


def is_enabled(service):
    # Fast path: OpenWrt enables services by symlinking /etc/rc.d/S##name.
    # Falling back to init.d is ~0.5s per call, so avoid it on the hot path.
    try:
        import glob
        return bool(glob.glob(f"/etc/rc.d/S*{service}"))
    except Exception:
        rc = subprocess.run(
            f"/etc/init.d/{service} enabled",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        return rc == 0


def is_running(pattern):
    # pgrep is compiled and ~5-10x faster than scanning /proc from Python on
    # this embedded host. Fall back to /proc scan if pgrep is missing.
    try:
        rc = subprocess.run(
            ["pgrep", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).returncode
        return rc == 0
    except Exception:
        pass
    try:
        regex = re.compile(pattern)
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline = Path(entry.path, "cmdline").read_text(errors="replace").replace("\x00", " ")
                if regex.search(cmdline):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        days, rem = divmod(int(secs), 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        return f"{days}d {hours}h {mins}m"
    except Exception:
        return "unknown"


def get_load():
    try:
        with open("/proc/loadavg") as f:
            return f.read().split()[0]
    except Exception:
        return "?"


def get_tail_lines(path, n=20):
    try:
        p = Path(path)
        if not p.exists():
            return ""
        text = p.read_text(errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-n:])
    except Exception as exc:
        return f"error reading {path}: {exc}"


def get_logread_tail(tag, n=20):
    try:
        return run(f"logread -e {tag} 2>/dev/null | tail -{n}")
    except Exception as exc:
        return f"error reading logread {tag}: {exc}"


def get_best_tail(preferred_path, fallback_paths=None, logread_tag=None, n=20):
    """Return the tail of the first readable log file, or logread fallback."""
    candidates = []
    if preferred_path:
        candidates.append(preferred_path)
    if fallback_paths:
        candidates.extend(fallback_paths)
    for path in candidates:
        text = get_tail_lines(path, n=n)
        if text and not text.startswith("error reading"):
            return text
    if logread_tag:
        text = get_logread_tail(logread_tag, n=n)
        if text and not text.startswith("error reading"):
            return text
    return "(no log output available)"


# Simple TTL cache for status collection so concurrent/tab requests don't
# stack up slow subprocess calls.
_status_cache = {"data": None, "expires": 0.0, "ttl": 8.0}
_quick_cache = {"data": None, "expires": 0.0, "ttl": 5.0}

# In-memory light state. We assume the light starts in an unknown state and
# track the last command issued. Moonraker does not expose a generic LED
# object name across Creality boards, so we drive it via gcode by default.
_light_state = {"state": "unknown", "since": datetime.now(timezone.utc).isoformat()}


def _query_light_state():
    """Read the actual LED output_pin value from Moonraker and update _light_state."""
    import urllib.request
    url = f"{LIGHT_MOONRAKER_URL}/printer/objects/query?{urllib.parse.quote(LIGHT_QUERY_OBJECT)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            payload = json.loads(r.read().decode("utf-8"))
        result = payload.get("result", {})
        status = result.get("status", {})
        pin_obj = status.get(LIGHT_QUERY_OBJECT, {})
        value = float(pin_obj.get("value", 0.0) if isinstance(pin_obj, dict) else 0.0)
        _light_state["state"] = "on" if value > 0.01 else "off"
        _light_state["since"] = datetime.now(timezone.utc).isoformat()
        return True
    except Exception:
        return False


def _parse_cert_pem(pem_text):
    """Extract subject, issuer, and notAfter from a PEM certificate without
    requiring cryptography or Python 3.10+ ssl APIs.

    Parses the first certificate only. Subject/Issuer are read from the DER
    as RDN sequences; notAfter is read from the validity UTCTime/GeneralizedTime.
    """
    import base64
    import datetime as dt
    import re

    start = pem_text.find("-----BEGIN CERTIFICATE-----")
    end = pem_text.find("-----END CERTIFICATE-----")
    if start == -1 or end == -1:
        raise ValueError("no PEM certificate block found")
    b64 = "".join(
        line.strip()
        for line in pem_text[start:end].splitlines()
        if not line.startswith("-----")
    )
    der = base64.b64decode(b64)

    # ASN.1 TLV reader
    def _read_tlv(data, pos):
        if pos >= len(data):
            raise ValueError("truncated DER")
        tag = data[pos]
        pos += 1
        length = data[pos]
        pos += 1
        if length & 0x80:
            num_bytes = length & 0x7F
            length = int.from_bytes(data[pos:pos + num_bytes], "big")
            pos += num_bytes
        return tag, data[pos:pos + length], pos + length

    # Split top-level SEQUENCE { tbsCertificate, sigAlg, sig }
    _, cert_value, _ = _read_tlv(der, 0)
    # tbsCertificate is the first child
    _, tbs, _ = _read_tlv(cert_value, 0)

    # Walk tbsCertificate children. Fields of interest:
    #   [0] version (optional)
    #   [1] serialNumber
    #   [2] signature
    #   [3] issuer  (Name)
    #   [4] validity
    #   [5] subject (Name)
    pos = 0
    children = []
    while pos < len(tbs):
        tag, value, nxt = _read_tlv(tbs, pos)
        children.append((tag, value))
        pos = nxt

    def _first_attr_value(name_der, oid_bytes):
        # Name is SEQUENCE OF SET OF SEQUENCE { OID, value }.
        # Scan the DER for the OID bytes and return the following value.
        idx = 0
        while idx < len(name_der):
            if name_der[idx:idx + len(oid_bytes)] == oid_bytes:
                after = idx + len(oid_bytes)
                # value usually UTF8String (0x0c) or PrintableString (0x13)
                val_tag, val, _ = _read_tlv(name_der, after)
                if val_tag in (0x0C, 0x13, 0x14, 0x16, 0x1A, 0x1B):
                    return val.decode("utf-8", errors="replace")
                return val.decode("latin-1", errors="replace")
            idx += 1
        return None

    CN_OID = bytes([0x06, 0x03, 0x55, 0x04, 0x03])
    O_OID = bytes([0x06, 0x03, 0x55, 0x04, 0x0A])

    def _format_name(name_der):
        cn = _first_attr_value(name_der, CN_OID)
        o = _first_attr_value(name_der, O_OID)
        parts = []
        if cn:
            parts.append(f"CN={cn}")
        if o:
            parts.append(f"O={o}")
        return ", ".join(parts) if parts else "unknown"

    subject = children[5][1] if len(children) > 5 else b""
    issuer = children[3][1] if len(children) > 3 else b""

    # Validity is the fourth field (index 4). It contains two time values.
    not_after = None
    if len(children) > 4 and children[4][0] == 0x30:
        validity = children[4][1]
        vpos = 0
        _, _, vpos = _read_tlv(validity, vpos)  # notBefore
        _, na_value, _ = _read_tlv(validity, vpos)  # notAfter
        ts = na_value.decode("ascii")
        if len(ts) == 13:
            year = int(ts[0:2])
            year += 2000 if year < 50 else 1900
            not_after = dt.datetime(year, int(ts[2:4]), int(ts[4:6]), int(ts[6:8]), int(ts[8:10]), int(ts[10:12]), tzinfo=dt.timezone.utc)
        elif len(ts) >= 15:
            not_after = dt.datetime(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]), int(ts[8:10]), int(ts[10:12]), int(ts[12:14]), tzinfo=dt.timezone.utc)

    return {
        "subject": _format_name(subject),
        "issuer": _format_name(issuer),
        "not_after": not_after.isoformat() if not_after else None,
        "days_left": max(0, (not_after - dt.datetime.now(dt.timezone.utc)).days) if not_after else None,
    }


def _cert_info():
    """Return basic info about the currently installed nginx TLS certificate."""
    crt = Path(f"{NGINX_CERT_DIR}/{NGINX_CERT_BASENAME}.crt")
    if not crt.exists():
        return {"present": False, "subject": None, "issuer": None, "not_after": None, "days_left": None}
    try:
        info = _parse_cert_pem(crt.read_text())
        info["present"] = True
        return info
    except Exception as exc:
        return {"present": True, "error": str(exc), "subject": None, "issuer": None, "not_after": None, "days_left": None}


def _set_light(state):
    """Send a light on/off command through Moonraker's gcode/script endpoint.

    Returns (ok, detail). state must be "on" or "off".
    """
    import urllib.request
    gcode = LIGHT_ON_GCODE if state == "on" else LIGHT_OFF_GCODE
    url = f"{LIGHT_MOONRAKER_URL}/printer/gcode/script"
    body = json.dumps({"script": gcode}).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            _ = r.read()
        # Best-effort re-read the real hardware state; don't fail the toggle if
        # the query fails.
        _query_light_state()
        return True, f"sent {gcode}"
    except Exception as exc:
        return False, str(exc)


def _service_status(name, pattern, svc):
    return name, {"enabled": is_enabled(svc), "running": is_running(pattern)}


def _listener_status(label, port):
    return label, is_listening(port)


def _log_tail_status(key, path_or_tuple, n):
    logread_tag = None
    if isinstance(path_or_tuple, tuple):
        preferred, fallbacks = path_or_tuple[0], list(path_or_tuple[1:])
        # Optional trailing logread tag: last element can be a non-path string
        # starting with "logread:".
        if fallbacks and fallbacks[-1].startswith("logread:"):
            logread_tag = fallbacks.pop().split(":", 1)[1]
        return key, get_best_tail(preferred, fallbacks, logread_tag=logread_tag, n=n)
    if isinstance(path_or_tuple, str) and path_or_tuple.startswith("logread:"):
        return key, get_logread_tail(path_or_tuple.split(":", 1)[1], n=n)
    return key, get_best_tail(path_or_tuple, logread_tag=None, n=n)


def collect_status():
    now_ts = datetime.now(timezone.utc).timestamp()
    if _status_cache["data"] is not None and now_ts < _status_cache["expires"]:
        return _status_cache["data"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = socket.gethostname()

    service_specs = [
        ("nginx", "nginx", "nginx"),
        ("lan_bridge", "python3 /usr/local/bin/lan_bridge.py", "lan_bridge"),
        ("app_cloud_only", "/usr/bin/app-server", "app_cloud_only"),
        ("Monitor (disabled)", "/usr/bin/Monitor", "app"),
        ("go2rtc", "go2rtc", "go2rtc"),
        ("cam_app", "/usr/bin/cam_app", "go2rtc"),
        ("cam_delivery_bridge", "python3 /usr/local/bin/cam_delivery_bridge.py", "go2rtc"),
        ("webrtc_local_bridge", "python3 /usr/local/bin/webrtc_local_bridge.py", "webrtc_local_bridge"),
        ("webrtc (cloud, manual)", "/usr/bin/webrtc", "webrtc"),
        ("mjpeg_server", "python3 /usr/local/bin/mjpeg_server.py", "mjpeg_server"),
        ("moonraker", "moonraker.py", "moonraker"),
        ("klipper", "klippy.py", "klipper"),
    ]
    listener_specs = [
        ("80 (nginx HTTP)", 80),
        ("443 (nginx HTTPS)", 443),
        ("9999 (lan_bridge WS)", 9999),
        ("7125 (moonraker)", 7125),
        ("7130 (Fluidd WSS fallback)", 7130),
        ("8080 (nginx MJPEG fallback)", 8080),
        ("8081 (mjpeg_server)", 8081),
        ("9002 (lan_bridge)", 9002),
        ("1984 (go2rtc HTTP)", 1984),
        ("8554 (go2rtc RTSP)", 8554),
        ("8000 (webrtc_local_bridge)", 8000),
    ]
    log_specs = [
        # procd captures lan_bridge stdout/stderr to logread; keep a file
        # fallback in case that ever changes.
        ("lan_bridge_tail", ("/var/log/lan_bridge.log", "logread:lan_bridge")),
        ("mjpeg_server_tail", "/tmp/mjpeg_server_solo.log"),
        ("go2rtc_tail", "/tmp/go2rtc_solo.log"),
        ("webrtc_tail", ("/mnt/UDISK/creality/userdata/log/webrtc.log", "/tmp/webrtc_solo.log")),
        ("monitor_tail", "/mnt/UDISK/creality/userdata/log/Monitor.log"),
    ]

    # Run service, listener, and log probes in parallel; the slowest bucket
    # dominates instead of the sum.
    services, listeners, logs = {}, {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        svc_futs = [ex.submit(_service_status, *spec) for spec in service_specs]
        lst_futs = [ex.submit(_listener_status, *spec) for spec in listener_specs]
        log_futs = [ex.submit(_log_tail_status, key, tag, 15) for key, tag in log_specs]
        for fut in concurrent.futures.as_completed(svc_futs):
            name, info = fut.result()
            services[name] = info
        for fut in concurrent.futures.as_completed(lst_futs):
            label, ok = fut.result()
            listeners[label] = ok
        for fut in concurrent.futures.as_completed(log_futs):
            key, text = fut.result()
            logs[key] = text

    cert = _cert_info()
    status = {
        "hostname": hostname,
        "timestamp": now,
        "uptime": get_uptime(),
        "load": get_load(),
        "services": services,
        "listeners": listeners,
        "logs": logs,
        "cert": cert,
    }
    _status_cache["data"] = status
    _status_cache["expires"] = datetime.now(timezone.utc).timestamp() + _status_cache["ttl"]
    return status


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{project_name} status</title>
<style>
:root {{ color-scheme: dark; --bg: #0d1117; --surface: #161b22; --border: #30363d; --muted: #8b949e; --text: #c9d1d9; --accent: #58a6ff; --accent-2: #79c0ff; --ok: #238636; --fail: #da3633; --warn: #9e6a03; --btn: #1f6feb; --btn-hover: #388bfd; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); margin:0; padding:1rem; line-height:1.5; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
header {{ margin-bottom: 1.25rem; padding-bottom: .75rem; border-bottom: 1px solid var(--border); display:flex; flex-wrap:wrap; align-items:baseline; gap:.5rem .75rem; }}
h1 {{ color: var(--accent); margin:0; font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }}
.sub {{ color: var(--muted); margin:0; font-size: .9rem; }}
.grid {{ display:grid; grid-template-columns: repeat(12, 1fr); gap:1rem; align-items: stretch; }}
.card {{ background: var(--surface); border:1px solid var(--border); border-radius:10px; padding:1rem; min-width:0; overflow:hidden; display:flex; flex-direction:column; grid-column: span 12; }}
.card h2 {{ margin:0 0 .75rem; font-size:1rem; color: var(--accent-2); font-weight: 600; flex-shrink:0; display:flex; align-items:center; gap:.5rem; }}
.card h2::before {{ content:""; display:inline-block; width:5px; height:16px; background: var(--accent); border-radius:3px; }}
@media (min-width: 640px) {{ .card.half {{ grid-column: span 6; }} .card.third {{ grid-column: span 4; }} }}
@media (min-width: 1024px) {{ .card.third {{ grid-column: span 4; }} .card.half {{ grid-column: span 6; }} }}
table {{ width:100%; border-collapse: collapse; table-layout: fixed; font-size: .9rem; }}
th, td {{ text-align:left; padding:.5rem .6rem; overflow-wrap:anywhere; word-break:break-word; vertical-align: middle; }}
th {{ color: var(--muted); font-weight:600; border-bottom:1px solid var(--border); font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }}
td {{ border-bottom:1px solid #21262d; }}
tr:last-child td {{ border-bottom:none; }}
tr:hover td {{ background: rgba(88,166,255,0.04); }}
.services td:nth-child(1), .services th:nth-child(1) {{ width: 100%; }}
.services td:nth-child(2), .services th:nth-child(2),
.services td:nth-child(3), .services th:nth-child(3) {{ width: 4.5rem; white-space: nowrap; text-align: center; }}
.services td:nth-child(2) .badge, .services td:nth-child(3) .badge {{ display: inline-flex; align-items: center; justify-content: center; min-width: 3.6rem; }}
.listeners td:nth-child(1), .listeners th:nth-child(1) {{ width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .82rem; }}
.listeners td:nth-child(2), .listeners th:nth-child(2) {{ width: 4.5rem; white-space: nowrap; text-align: right; }}
.listeners td:nth-child(2) .badge {{ display: inline-flex; align-items: center; justify-content: center; min-width: 3.6rem; }}
.quick-checks td:nth-child(1), .quick-checks th:nth-child(1) {{ width: 1%; white-space: nowrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .82rem; color: var(--muted); }}
.quick-checks td:nth-child(2), .quick-checks th:nth-child(2) {{ width: 100%; text-align: right; }}
.badge {{ display:inline-block; padding:.2rem .55rem; border-radius:999px; font-size:.72rem; font-weight:700; white-space:nowrap; line-height:1; }}
.ok {{ background: var(--ok); color:#fff; }}
.fail {{ background: var(--fail); color:#fff; }}
.warn {{ background: var(--warn); color:#fff; }}
pre {{ background: var(--bg); border:1px solid var(--border); border-radius:8px; padding:.75rem; overflow:auto; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size:.8rem; max-height:14rem; white-space:pre-wrap; word-break:break-word; flex-grow:1; margin:0; }}
.footer {{ margin-top:2rem; color: var(--muted); font-size:.8rem; text-align:center; }}
.note {{ color: var(--muted); font-size:.85rem; background: var(--surface); border:1px solid var(--border); border-radius:8px; padding:.85rem; line-height:1.55; }}
.note code {{ background: rgba(110,118,129,0.25); padding:.1rem .3rem; border-radius:4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size:.8rem; color: var(--text); }}
.cert-ok {{ color: var(--ok); font-weight: 600; }}
.cert-warn {{ color: var(--warn); font-weight: 600; }}
.cert-fail {{ color: var(--fail); font-weight: 600; }}
.btn-group {{ display:flex; gap:.5rem; flex-wrap: wrap; }}
input[type="file"] {{ color: var(--text); font-size: .85rem; }}
.upload-row {{ display:flex; gap:.5rem; align-items: center; flex-wrap: wrap; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .85rem; }}
.btn {{ background: var(--btn); color:#fff; border:1px solid var(--btn-hover); border-radius:6px; padding:.45rem 1rem; font-size:.9rem; font-weight: 500; cursor:pointer; transition: background .15s ease; }}
.btn:hover {{ background: var(--btn-hover); }}
.btn:disabled {{ opacity:.6; cursor:not-allowed; }}
.led-row {{ display:flex; align-items:center; gap:1rem; flex-wrap: wrap; margin-bottom:.75rem; }}
.led-row .sub {{ margin:0; }}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>🖨️ {project_name} status</h1>
  <p class="sub">{hostname} · {timestamp} · uptime {uptime} · load {load}</p>
</header>
<div class="grid">
  <div class="card half">
    <h2>Services</h2>
    <table class="services">
      <tr><th>Name</th><th>Enabled</th><th>Running</th></tr>
      {services_rows}
    </table>
  </div>
  <div class="card half">
    <h2>Listeners</h2>
    <table class="listeners">
      <tr><th>Port</th><th>State</th></tr>
      {listeners_rows}
    </table>
  </div>
  <div class="card">
    <h2>Quick checks</h2>
    <table class="quick-checks">
      <tr><td>HTTP /camera.mjpeg</td><td>{camera_mjpeg_http}</td></tr>
      <tr><td>HTTP /webcam/cam.jpg</td><td>{webcam_cam_jpg_http}</td></tr>
      <tr><td>HTTP /webcam/stream.mjpg</td><td>{webcam_stream_http}</td></tr>
      <tr><td>WS :9999 upgrade</td><td>{ws_upgrade}</td></tr>
      <tr><td>go2rtc /api/streams</td><td>{streams_http}</td></tr>
      <tr><td>go2rtc /webcam/api/ws</td><td>{go2rtc_ws_http}</td></tr>
      <tr><td>/info</td><td>{info_http}</td></tr>
      <tr><td>/protocal.csp</td><td>{protocal_http}</td></tr>
      <tr><td>/server/info</td><td>{server_info}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Chamber / camera LED</h2>
    <div class="led-row">
      <p class="sub">State: <span id="light-state" class="badge warn">checking...</span></p>
      <div class="btn-group">
        <button class="btn" id="light-on" onclick="setLight('on')">Light on</button>
        <button class="btn" id="light-off" onclick="setLight('off')">Light off</button>
      </div>
    </div>
    <p id="light-detail" class="note" style="display:none;"></p>
    <p class="note">Simple REST: <code>GET /{status_path}/api/light/simple</code> returns <code>on</code>/<code>off</code>/<code>unknown</code>. <code>GET /{status_path}/api/light/set?state=on</code> toggles it. Homebridge example in docs.</p>
  </div>
  <div class="card">
    <h2>TLS certificate</h2>
    <div id="cert-info">
      <p class="sub"><strong class="mono">{cert_subject}</strong><br>Issuer: <span class="mono">{cert_issuer}</span><br>Expires: {cert_not_after} · {cert_days_left} days left</p>
      <p class="sub {cert_status_class}">{cert_status_text}</p>
    </div>
    <div class="upload-row" style="margin-top:.75rem;">
      <label for="cert-file" class="btn" style="display:inline-block;">Choose cert + key archive</label>
      <input type="file" id="cert-file" accept=".zip,.tar,.tar.gz,.tgz" style="display:none;" onchange="document.getElementById('cert-filename').textContent = this.files[0]?.name || '';">
      <span id="cert-filename" class="mono"></span>
      <button class="btn" id="cert-upload" onclick="uploadCert()">Install</button>
    </div>
    <p id="cert-detail-note" class="note" style="margin-top:.75rem; display:none;"></p>
    <p class="note" style="margin-top:.75rem;">Upload a ZIP or tarball containing <code>&lt;basename&gt;.crt</code> and <code>&lt;basename&gt;.key</code>, where <code>&lt;basename&gt;</code> matches the configured certificate name. After install, nginx reloads automatically. You can still manage certs via SSH with <code>./install.sh cert ./certs</code>.</p>
  </div>
  <div class="card third">
    <h2>lan_bridge log tail</h2>
    <pre>{lan_bridge_tail}</pre>
  </div>
  <div class="card third">
    <h2>mjpeg_server log tail</h2>
    <pre>{mjpeg_server_tail}</pre>
  </div>
  <div class="card third">
    <h2>go2rtc log tail</h2>
    <pre>{go2rtc_tail}</pre>
  </div>
  <div class="card half">
    <h2>webrtc log tail</h2>
    <pre>{webrtc_tail}</pre>
  </div>
  <div class="card half">
    <h2>Monitor log tail</h2>
    <pre>{monitor_tail}</pre>
  </div>
</div>
<p class="note"><strong>Note:</strong> The stock <code>/etc/init.d/app</code> is disabled so only <code>/etc/init.d/app_cloud_only</code> starts at boot. The <code>/usr/bin/Monitor</code> process (which respawned <code>web-server</code>, <code>display-server</code>, and <code>webrtc_local</code>) has been stopped and did not respawn. "manual" services are started by our camera-stack init even though their stock init script is disabled at boot. The camera stack uses a single <code>cam_app</code> source; the separate <code>cloud_webrtc_bridge.py</code> feeder has been retired.</p>
<p class="footer">Served by {project_name} status page on {bind}:{port}</p>
</div>
<script>
const API = window.location.pathname.replace(/\/$/, '') + '/api/light';
async function refreshLight() {{
  try {{
    const r = await fetch(API);
    const j = await r.json();
    const el = document.getElementById('light-state');
    el.textContent = j.state;
    el.className = 'badge ' + (j.state === 'on' ? 'ok' : j.state === 'off' ? 'fail' : 'warn');
  }} catch (e) {{
    document.getElementById('light-state').textContent = 'unknown';
  }}
}}
async function setLight(state) {{
  const detail = document.getElementById('light-detail');
  document.getElementById('light-on').disabled = true;
  document.getElementById('light-off').disabled = true;
  detail.style.display = 'block';
  try {{
    const r = await fetch(API, {{ method: 'POST', headers: {{'Content-Type':'application/x-www-form-urlencoded'}}, body: 'state=' + state }});
    const j = await r.json();
    detail.textContent = j.ok ? ('OK: ' + j.detail) : ('Error: ' + j.detail);
    await refreshLight();
  }} catch (e) {{
    detail.textContent = 'Error: ' + e;
  }} finally {{
    document.getElementById('light-on').disabled = false;
    document.getElementById('light-off').disabled = false;
  }}
}}
refreshLight();
refreshCert();

async function refreshCert() {{
  try {{
    const r = await fetch(API.replace('/api/light', '/api/cert'));
    const c = await r.json();
    const el = document.getElementById('cert-info');
    if (!c.present) {{
      el.innerHTML = '<p class="sub cert-fail">No certificate found at <span class="mono">{cert_path}</span>.</p>';
      return;
    }}
    if (c.error) {{
      el.innerHTML = '<p class="sub cert-fail">Error reading certificate: ' + escapeHtml(c.error) + '</p>';
      return;
    }}
    const statusClass = c.days_left === null ? 'cert-warn' : c.days_left < 7 ? 'cert-fail' : c.days_left < 30 ? 'cert-warn' : 'cert-ok';
    const daysText = c.days_left === null ? '' : ' · ' + c.days_left + ' day' + (c.days_left === 1 ? '' : 's') + ' left';
    el.innerHTML = '<p class="sub"><strong>' + escapeHtml(c.subject || 'unknown') + '</strong><br>Issuer: ' + escapeHtml(c.issuer || 'unknown') + '<br>Expires: ' + escapeHtml(c.not_after || 'unknown') + daysText + '</p><p class="sub ' + statusClass + '">' + (c.days_left === null ? 'Unable to compute expiry' : c.days_left < 0 ? 'Expired' : c.days_left < 7 ? 'Expires soon' : 'Certificate OK') + '</p>';
  }} catch (e) {{
    document.getElementById('cert-info').innerHTML = '<p class="sub cert-fail">Error loading certificate info: ' + escapeHtml(e.message || e) + '</p>';
  }}
}}

async function uploadCert() {{
  const fileInput = document.getElementById('cert-file');
  const detail = document.getElementById('cert-detail-note');
  const btn = document.getElementById('cert-upload');
  if (!fileInput.files.length) {{
    detail.style.display = 'block';
    detail.textContent = 'Please choose a cert archive first.';
    return;
  }}
  btn.disabled = true;
  btn.textContent = 'Uploading...';
  detail.style.display = 'block';
  detail.textContent = 'Uploading...';
  try {{
    const body = new FormData();
    body.append('archive', fileInput.files[0]);
    const r = await fetch(API.replace('/api/light', '/api/cert'), {{ method: 'POST', body }});
    const j = await r.json();
    detail.textContent = j.ok ? ('OK: ' + j.detail) : ('Error: ' + j.detail);
    if (j.ok) setTimeout(() => location.reload(), 1200);
  }} catch (e) {{
    detail.textContent = 'Upload failed: ' + e;
  }} finally {{
    btn.disabled = false;
    btn.textContent = 'Install';
  }}
}}

function escapeHtml(str) {{
  if (str === null || str === undefined) return '';
  return String(str).replace(/[&<>"']/g, function(m) {{ return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]; }});
}}
</script>
</body>
</html>"""


def check_url(url, host=None, timeout=2):
    import ssl
    import urllib.request
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, method="GET")
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            ctype = r.headers.get("Content-Type", "unknown")
            return f'<span class="badge ok">HTTP {r.status} {ctype}</span>'
    except Exception as exc:
        return f'<span class="badge fail">{exc}</span>'


def check_websocket_upgrade(url, host=None, timeout=2):
    """Probe a WebSocket endpoint by performing the handshake manually.

    Works for ws:// and http:// URLs that expect an Upgrade handshake.
    Returns an ok badge only on HTTP 101 Switching Protocols.
    """
    import urllib.request
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme
    netloc = parsed.netloc or host or "127.0.0.1"
    path = parsed.path or "/"
    if scheme not in ("ws", "wss", "http", "https"):
        return '<span class="badge fail">unsupported scheme</span>'
    use_ssl = scheme in ("wss", "https")
    host_header = host or netloc
    try:
        # Strip :port for Host header if netloc was used.
        bare_host = host_header.split(":")[0]
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {bare_host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
        request_bytes = ("\r\n".join(lines)).encode("latin-1")
        port = parsed.port or (443 if use_ssl else 80)
        s = socket.create_connection((bare_host, port), timeout=timeout)
        if use_ssl:
            import ssl
            s = ssl.create_default_context().wrap_socket(s, server_hostname=bare_host)
        s.sendall(request_bytes)
        response = s.recv(4096).decode("latin-1", errors="replace")
        s.close()
        status_line = response.split("\r\n", 1)[0]
        parts = status_line.split()
        if len(parts) >= 2 and parts[1] == "101":
            return f'<span class="badge ok">{status_line}</span>'
        return f'<span class="badge warn">{status_line}</span>'
    except Exception as exc:
        return f'<span class="badge fail">{exc}</span>'


def _run_quick_checks():
    """Hit a handful of public endpoints in parallel and return HTML badge strings."""
    import functools
    now_ts = datetime.now(timezone.utc).timestamp()
    if _quick_cache["data"] is not None and now_ts < _quick_cache["expires"]:
        return _quick_cache["data"]

    checks = {
        "camera_mjpeg_http": functools.partial(
            check_url, "http://127.0.0.1/camera.mjpeg", timeout=1
        ),
        "webcam_cam_jpg_http": functools.partial(
            check_url, "http://127.0.0.1/webcam/cam.jpg", timeout=1
        ),
        "webcam_stream_http": functools.partial(
            check_url, "http://127.0.0.1/webcam/stream.mjpg", timeout=1
        ),
        "ws_upgrade": functools.partial(
            check_websocket_upgrade, "ws://127.0.0.1:9999", timeout=1
        ),
        "streams_http": functools.partial(
            check_url,
            "http://127.0.0.1:1984/api/streams",
            timeout=1,
        ),
        "go2rtc_ws_http": functools.partial(
            check_websocket_upgrade,
            "ws://127.0.0.1/webcam/api/ws",
            timeout=1,
        ),
        "info_http": functools.partial(check_url, "http://127.0.0.1/info", timeout=1),
        "protocal_http": functools.partial(
            check_url, "http://127.0.0.1/protocal.csp", timeout=1
        ),
        "server_info": functools.partial(
            check_url, "http://127.0.0.1/server/info", timeout=1
        ),
    }
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as ex:
        futures = {ex.submit(fn): name for name, fn in checks.items()}
        # Use a generous overall timeout but return whatever completed so a
        # single slow MJPEG endpoint cannot abort the whole status page.
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=5):
                results[futures[fut]] = fut.result()
        except concurrent.futures.TimeoutError:
            pass
        for fut, name in futures.items():
            if name not in results:
                if fut.done():
                    try:
                        results[name] = fut.result()
                    except Exception as exc:
                        results[name] = f'<span class="badge fail">{exc}</span>'
                else:
                    results[name] = '<span class="badge warn">timeout</span>'
    _quick_cache["data"] = results
    _quick_cache["expires"] = datetime.now(timezone.utc).timestamp() + _quick_cache["ttl"]
    return results


def check_url_post_webrtc(url, host=None):
    import urllib.request
    import base64
    import json
    offer = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 127.0.0.1\r\n"
        "s=-\r\n"
        "t=0 0\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=rtcp:9 IN IP4 0.0.0.0\r\n"
        "a=ice-ufrag:abc123\r\n"
        "a=ice-pwd:def45678901234567890\r\n"
        "a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\r\n"
        "a=setup:actpass\r\n"
        "a=mid:0\r\n"
        "a=sendrecv\r\n"
        "a=rtcp-mux\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1\r\n"
    )
    try:
        req = urllib.request.Request(url, data=offer.encode(), method="POST", headers={"Content-Type": "plain/text"})
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            payload = json.loads(base64.b64decode(body))
            if payload.get("type") == "answer" and len(payload.get("sdp", "")) > 100:
                return '<span class="badge ok">answer SDP</span>'
            return '<span class="badge warn">unexpected payload</span>'
    except Exception as exc:
        return f'<span class="badge fail">{exc}</span>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Emit ECS-compliant access log entry.
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except Exception:
            status = 0
        ecs = {
            "event.category": ["web"],
            "event.kind": "event",
            "event.outcome": "success" if 200 <= status < 400 else "failure",
            "http.request.method": self.command,
            "http.response.status_code": status,
            "url.path": self.path,
            "url.original": self.path,
            "source.ip": self.client_address[0] if self.client_address else None,
            "source.port": self.client_address[1] if self.client_address else None,
            "destination.address": BIND,
            "destination.port": PORT,
            "http.request.bytes": int(self.headers.get("Content-Length", 0)),
        }
        logger.info(f"{self.command} {self.path} {status}", extra={"ecs": ecs})

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before we finished sending; not a server bug.
            logger.debug("Client disconnected mid-request", exc_info=True)

    def _json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(obj, indent=2).encode())

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _text(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def _read_post_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length).decode() if length > 0 else ""

    def _read_multipart_archive(self):
        """Extract the first file upload from a multipart body and return (filename, bytes)."""
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/"):
            return None, None
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            return None, None
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        boundary_b = ("--" + boundary).encode()
        parts = body.split(boundary_b)
        for part in parts:
            part = part.lstrip(b"\r\n")
            if b"\r\n\r\n" not in part:
                continue
            header, data = part.split(b"\r\n\r\n", 1)
            disp = header.decode("latin-1")
            if "filename=" in disp and "name=\"archive\"" in disp:
                filename = re.search(r'filename="([^"]+)"', disp)
                filename = filename.group(1) if filename else "archive"
                data = data.rstrip(b"\r\n")
                if data.endswith(b"--"):
                    data = data[:-2]
                return filename, data
        return None, None

    def _install_cert_archive(self, filename, data):
        """Save uploaded archive, extract .crt/.key matching basename, reload nginx."""
        crt_name = f"{NGINX_CERT_BASENAME}.crt"
        key_name = f"{NGINX_CERT_BASENAME}.key"
        tmpdir = tempfile.mkdtemp(prefix="cert_upload_")
        try:
            archive_path = Path(tmpdir) / filename
            archive_path.write_bytes(data)
            extract_dir = Path(tmpdir) / "extracted"
            extract_dir.mkdir()

            lower = filename.lower()
            if lower.endswith(".zip"):
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(extract_dir)
            elif lower.endswith((".tar", ".tar.gz", ".tgz")):
                mode = "r:gz" if lower.endswith((".tar.gz", ".tgz")) else "r"
                with tarfile.open(archive_path, mode) as tf:
                    tf.extractall(extract_dir)
            else:
                return False, "unsupported archive format (use .zip, .tar, .tar.gz, .tgz)"

            crt_files = list(extract_dir.rglob(crt_name))
            key_files = list(extract_dir.rglob(key_name))
            if not crt_files:
                return False, f"{crt_name} not found in archive"
            if not key_files:
                return False, f"{key_name} not found in archive"

            shutil.copy2(crt_files[0], Path(NGINX_CERT_DIR) / crt_name)
            shutil.copy2(key_files[0], Path(NGINX_CERT_DIR) / key_name)
            subprocess.run(["chmod", "600", str(Path(NGINX_CERT_DIR) / key_name)], check=True)
            result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
            if result.returncode != 0:
                return False, f"nginx config test failed: {result.stderr.strip()}"
            subprocess.run(["/etc/init.d/nginx", "reload"], check=True)
            return True, "certificate installed and nginx reloaded"
        except subprocess.CalledProcessError as exc:
            return False, f"command failed: {exc}"
        except Exception as exc:
            return False, str(exc)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def do_POST(self):
        status_prefix = f"/{STATUS_PATH}"
        if self.path.startswith(f"{status_prefix}/api/light"):
            body = self._read_post_body()
            content_type = self.headers.get("Content-Type", "")
            state = None
            if "application/json" in content_type:
                try:
                    state = json.loads(body).get("state")
                except Exception:
                    pass
            if state is None:
                for part in body.split("&"):
                    if part.startswith("state="):
                        state = urllib.parse.unquote(part.split("=", 1)[1])
                        break
            if state not in ("on", "off"):
                self.send_error(400, f"invalid state {state!r}; use on or off")
                return
            ok, detail = _set_light(state)
            self._json({"ok": ok, "state": _light_state["state"], "detail": detail})
            return
        if self.path.startswith(f"{status_prefix}/api/cert"):
            filename, data = self._read_multipart_archive()
            if data is None:
                self.send_error(400, "expected multipart file upload named 'archive'")
                return
            ok, detail = self._install_cert_archive(filename, data)
            self._json({"ok": ok, "detail": detail})
            return
        self.send_error(404)

    def do_GET(self):
        status_prefix = f"/{STATUS_PATH}"
        path_only = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if path_only == f"{status_prefix}/api/light":
            _query_light_state()
            self._json({"state": _light_state["state"], "since": _light_state["since"]})
            return
        if path_only == f"{status_prefix}/api/light/simple":
            _query_light_state()
            self._text(_light_state["state"])
            return
        if path_only == f"{status_prefix}/api/light/set":
            params = urllib.parse.parse_qs(query)
            state = params.get("state", [None])[0]
            if state not in ("on", "off"):
                self.send_error(400, "invalid state; use ?state=on or ?state=off")
                return
            ok, detail = _set_light(state)
            self._json({"ok": ok, "state": _light_state["state"], "detail": detail})
            return
        if path_only == f"{status_prefix}/api/status.json":
            self._json(collect_status())
            return
        if path_only == f"{status_prefix}/api/cert":
            self._json(_cert_info())
            return
        if path_only in (f"{status_prefix}/", status_prefix):
            # Collect service/listener state and probe public endpoints in
            # parallel; the slowest path should dominate, not the sum.
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                data_fut = ex.submit(collect_status)
                quick_fut = ex.submit(_run_quick_checks)
                data = data_fut.result()
                quick_results = quick_fut.result()

            ok_badge = '<span class="badge ok">{}</span>'
            fail_badge = '<span class="badge fail">{}</span>'
            services_rows = "\n".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    name,
                    ok_badge.format("enabled") if info["enabled"] else fail_badge.format("disabled"),
                    ok_badge.format("running") if info["running"] else fail_badge.format("down"),
                )
                for name, info in data["services"].items()
            )
            listeners_rows = "\n".join(
                "<tr><td>{}</td><td>{}</td></tr>".format(
                    port,
                    ok_badge.format("listening") if ok else fail_badge.format("down"),
                )
                for port, ok in data["listeners"].items()
            )
            camera_mjpeg_http = quick_results.get("camera_mjpeg_http", "<span class=\"badge fail\">missing</span>")
            webcam_cam_jpg_http = quick_results.get("webcam_cam_jpg_http", "<span class=\"badge fail\">missing</span>")
            webcam_stream_http = quick_results.get("webcam_stream_http", "<span class=\"badge fail\">missing</span>")
            ws_upgrade = quick_results.get("ws_upgrade", "<span class=\"badge fail\">missing</span>")
            streams_http = quick_results.get("streams_http", "<span class=\"badge fail\">missing</span>")
            go2rtc_ws_http = quick_results.get("go2rtc_ws_http", "<span class=\"badge fail\">missing</span>")
            info_http = quick_results.get("info_http", "<span class=\"badge fail\">missing</span>")
            protocal_http = quick_results.get("protocal_http", "<span class=\"badge fail\">missing</span>")
            server_info = quick_results.get("server_info", "<span class=\"badge fail\">missing</span>")
            cert = data["cert"]
            cert_status_class = "cert-warn"
            cert_status_text = "Unknown"
            if not cert.get("present"):
                cert_status_class = "cert-fail"
                cert_status_text = "Missing"
            elif cert.get("error"):
                cert_status_class = "cert-fail"
                cert_status_text = "Error"
            elif cert.get("days_left") is not None:
                if cert["days_left"] < 0:
                    cert_status_class, cert_status_text = "cert-fail", "Expired"
                elif cert["days_left"] < 7:
                    cert_status_class, cert_status_text = "cert-fail", "Expires soon"
                elif cert["days_left"] < 30:
                    cert_status_class, cert_status_text = "cert-warn", "Expires soon"
                else:
                    cert_status_class, cert_status_text = "cert-ok", "OK"
            html = HTML_TEMPLATE.format(
                hostname=data["hostname"],
                timestamp=data["timestamp"],
                uptime=data["uptime"],
                load=data["load"],
                services_rows=services_rows,
                listeners_rows=listeners_rows,
                camera_mjpeg_http=camera_mjpeg_http,
                webcam_cam_jpg_http=webcam_cam_jpg_http,
                webcam_stream_http=webcam_stream_http,
                ws_upgrade=ws_upgrade,
                streams_http=streams_http,
                go2rtc_ws_http=go2rtc_ws_http,
                info_http=info_http,
                protocal_http=protocal_http,
                server_info=server_info,
                lan_bridge_tail=data["logs"]["lan_bridge_tail"],
                mjpeg_server_tail=data["logs"]["mjpeg_server_tail"],
                go2rtc_tail=data["logs"]["go2rtc_tail"],
                webrtc_tail=data["logs"]["webrtc_tail"],
                monitor_tail=data["logs"]["monitor_tail"],
                bind=BIND,
                port=PORT,
                project_name=PROJECT_NAME,
                status_path=STATUS_PATH,
                cert_basename=NGINX_CERT_BASENAME,
                cert_path=f"{NGINX_CERT_DIR}/{NGINX_CERT_BASENAME}.crt",
                cert_status_class=cert_status_class,
                cert_status_text=cert_status_text,
                cert_subject=cert.get("subject") or "unknown",
                cert_issuer=cert.get("issuer") or "unknown",
                cert_not_after=cert.get("not_after") or "unknown",
                cert_days_left=str(cert.get("days_left")) if cert.get("days_left") is not None else "unknown",
            )
            self._html(html)
            return
        self.send_error(404)


class ReuseAddrThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    # Pre-warm the status cache in a background thread so the first HTTP
    # request doesn't pay the full probe cost.
    def _warm():
        try:
            collect_status()
        except Exception:
            logger.warning("Cache warm-up failed", exc_info=True)
        try:
            _query_light_state()
        except Exception:
            logger.warning("Light state warm-up failed", exc_info=True)
    threading.Thread(target=_warm, daemon=True).start()
    server = ReuseAddrThreadingHTTPServer((BIND, PORT), Handler)
    logger.info(f"{PROJECT_NAME} status page listening on http://{BIND}:{PORT}/{STATUS_PATH}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
