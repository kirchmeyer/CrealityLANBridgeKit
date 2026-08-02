#!/usr/bin/env python3
import base64
import json
import os
import socket
import io
import cgi
import threading
import time
import urllib.request
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer


class CompatHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._identity_cache_timestamp = 0.0
        self._identity_cache_data = None
        self._identity_cache_ttl = float(os.environ.get("IDENTITY_CACHE_TTL", "30"))

    def get_cached_identity(self):
        """Return cached (moonraker_info, resolve_result) tuple if still valid."""
        if self._identity_cache_data is not None and time.time() < self._identity_cache_timestamp + self._identity_cache_ttl:
            return self._identity_cache_data
        # Expired or missing — invalidate
        self._identity_cache_data = None
        self._identity_cache_timestamp = 0.0
        return None

    def set_cached_identity(self, moonraker_info, resolved_fields):
        """Store identity resolution result with current timestamp."""
        self._identity_cache_data = (moonraker_info, resolved_fields)
        self._identity_cache_timestamp = time.time()

    def clear_identity_cache(self):
        """Invalidate cached identity data."""
        self._identity_cache_data = None
        self._identity_cache_timestamp = 0.0

from urllib.error import URLError, HTTPError

MOONRAKER_URL = os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7126")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9001"))
EXTRA_PORTS = [int(port) for port in os.environ.get("EXTRA_PORTS", "8000").split(",") if port]
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "0.8"))
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "K2 Plus")
DEFAULT_CFS_NAME = os.environ.get("LAN_CFS_NAME", "Lan Compat CFS")
DEFAULT_MATERIAL_NAME = os.environ.get("LAN_MATERIAL_NAME", "Material")
DEFAULT_MATERIAL_COLOR = os.environ.get("LAN_MATERIAL_COLOR", "#FF0000")
DEBUG_LOG = os.environ.get("DEBUG_LOG", "/tmp/creality_probe_backend_debug.log")
STATUS_PAGE_PATH = os.environ.get("STATUS_PAGE_PATH", "/debug/creality-probe-status")
STREAM_PATHS = frozenset({"/call/webrtc_local", "/api/v1/streams", "/api/streams", "/api/webrtc", "/webrtc", "/camera", "/live"})


def debug_log(message):
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


class ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _record_request_audit(self, path, method, query="", body=None, duration_ms=None):
        try:
            audit = self.server._compat_audit if hasattr(self.server, "_compat_audit") else None
            if audit is None:
                return
            body_summary = None
            body_preview = None
            if isinstance(body, dict):
                body_summary = sorted(body.keys())
                preview_items = []
                nested_shapes = []
                for key in ("deviceName", "aliasName", "address", "identity", "modelName", "model", "page", "pageSize", "pFileList", "onePageNum", "id"):
                    if key in body:
                        value = body[key]
                        if isinstance(value, (dict, list)):
                            preview_items.append(f"{key}={json.dumps(value, sort_keys=True)[:80]}")
                        else:
                            preview_items.append(f"{key}={value}")
                for key, value in body.items():
                    if isinstance(value, dict):
                        nested_shapes.append(f"{key}:dict")
                    elif isinstance(value, list):
                        nested_shapes.append(f"{key}:list")
                if preview_items:
                    body_preview = "; ".join(preview_items)
                if nested_shapes:
                    body_preview = f"{body_preview} | nested={','.join(nested_shapes[:4])}" if body_preview else f"nested={','.join(nested_shapes[:4])}"
            elif isinstance(body, list):
                body_summary = ["list"]
                body_preview = f"len={len(body)}"
            entry = {
                "route": path,
                "method": method,
                "query": query,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "body_keys": body_summary,
                "body_preview": body_preview,
                "duration_ms": duration_ms,
            }
            audit.append(entry)
            if len(audit) > 8:
                del audit[: len(audit) - 8]
        except Exception:
            pass

    def _record_payload_snapshot(self, path, payload):
        try:
            snapshots = self.server._compat_payload_snapshots if hasattr(self.server, "_compat_payload_snapshots") else None
            if snapshots is None:
                return
            if not isinstance(payload, dict):
                return
            summary = {}
            for key in ("identity", "name", "model", "deviceName", "address", "state", "deviceState"):
                if key in payload:
                    summary[key] = payload[key]
            if isinstance(payload.get("result"), dict):
                result = payload["result"]
                for key in ("identity", "name", "model", "deviceName", "address", "state", "deviceState"):
                    if key in result:
                        summary[key] = result[key]
            if summary:
                snapshots[path] = summary
        except Exception:
            pass

    def _record_state_transition(self, step, payload=None, persisted_identity=None):
        try:
            history = self.server._compat_state_history if hasattr(self.server, "_compat_state_history") else None
            if history is None:
                return
            record = {
                "step": step,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "identity": None,
                "deviceName": None,
                "address": None,
                "state": None,
                "deviceState": None,
                "persisted_identity": None,
                "persisted_deviceName": None,
                "persisted_address": None,
            }
            if isinstance(payload, dict):
                for key in ("identity", "deviceName", "address", "state", "deviceState"):
                    if key in payload:
                        record[key] = payload[key]
            if isinstance(persisted_identity, dict):
                for key in ("identity", "deviceName", "address", "state", "deviceState"):
                    if key in persisted_identity:
                        record[f"persisted_{key}"] = persisted_identity[key]
            history.append(record)
            if len(history) > 12:
                del history[: len(history) - 12]
            debug_log(
                "[STATE] step={step} identity={identity} deviceName={deviceName} address={address} "
                "state={state} deviceState={deviceState} persisted_identity={persisted_identity} "
                "persisted_deviceName={persisted_deviceName} persisted_address={persisted_address}".format(
                    step=record.get("step"),
                    identity=record.get("identity"),
                    deviceName=record.get("deviceName"),
                    address=record.get("address"),
                    state=record.get("state"),
                    deviceState=record.get("deviceState"),
                    persisted_identity=record.get("persisted_identity"),
                    persisted_deviceName=record.get("persisted_deviceName"),
                    persisted_address=record.get("persisted_address"),
                )
            )
        except Exception:
            pass

    def _identity_state_path(self):
        return os.environ.get("CREALITY_IDENTITY_STATE_PATH", "/tmp/creality_probe_identity_state.json")

    def _load_persisted_identity(self):
        try:
            path = self._identity_state_path()
            if not path or not os.path.exists(path):
                return {}
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_persisted_identity(self, payload):
        try:
            if not isinstance(payload, dict):
                return
            path = self._identity_state_path()
            if not path:
                return
            state = self._load_persisted_identity()
            is_lan_printer = payload.get("isLanPrinter") is True or payload.get("lanCompatible") is True
            preserve_media_state = is_lan_printer and any(
                key in payload and isinstance(payload[key], dict) and key in {"cameraState", "recordState", "streamState", "record"}
                for key in ("cameraState", "recordState", "streamState", "record")
            )
            for key in ("deviceName", "aliasName", "name", "machine_name", "machine_type", "model", "modelName"):
                if key in payload and payload[key] is not None:
                    state[key] = payload[key]
            for key in (
                "address", "mac", "deviceType", "type", "video", "tbId", "keyFileToken", "videoToken",
                "connectType", "machinePlatformMotionEnable", "materialDetector1", "supportMultiple",
                "isLanPrinter", "lanCompatible", "oldPrinter", "cameraState", "recordState", "streamState",
                "record", "filamentsList", "boxsInfo", "boxConfig", "features", "previewimg", "deviceImg",
                "defaultDeviceImg", "printerImagePath", "linuxVideoUrl", "webrtcSupport", "name", "deviceName", "aliasName",
            ):
                if key in payload and payload[key] is not None:
                    state[key] = payload[key]
            if not is_lan_printer:
                for key in ("model", "modelName", "model_name", "machine_name", "machine_type"):
                    if key in payload and payload[key] is not None:
                        state[key] = payload[key]
            if is_lan_printer:
                state["identity"] = None
            elif "identity" in payload and payload["identity"] is not None:
                state["identity"] = payload["identity"]
            if preserve_media_state:
                state["_preserve_media_state"] = True
            else:
                state.pop("_preserve_media_state", None)
            if state.get("deviceName") and not state.get("name"):
                state["name"] = state["deviceName"]
            if state.get("name") and not state.get("deviceName"):
                state["deviceName"] = state["name"]
            if state.get("deviceName") and not state.get("aliasName"):
                state["aliasName"] = state["deviceName"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True)
        except Exception:
            pass

    def _first_present(self, *values):
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                if value != "":
                    return value
                continue
            # Skip empty collections (list/dict/set) — they are falsy and
            # should fall through to the next candidate instead of short-circuiting.
            if isinstance(value, (list, dict, set)) and len(value) == 0:
                continue
            return value
        return None

    def _merge_dicts(self, *mappings):
        merged = {}
        for mapping in mappings:
            if isinstance(mapping, dict):
                merged.update(mapping)
        return merged

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        user_agent = self.headers.get("User-Agent", "")
        started = datetime.now(timezone.utc)
        debug_log(f"[GET] {self.address_string()} {path} query={query} ua={user_agent}")
        route_handlers = {
            "/status": lambda: self.serve_status_page(),
            "/status/": lambda: self.serve_status_page(),
            "/info": lambda: self.serve_info(),
            "/api/v1/device/status": lambda: self.serve_creality_device_status(),
            "/api/rest/print/cluster/devices/getDeviceCount": lambda: self.serve_get_device_count(),
            "/cxy/v1/status": lambda: self.serve_creality_cxy_status(),
            "/machine/system_info": lambda: self.serve_system_info(),
            "/machine/info": lambda: self.serve_machine_info(),
            "/machine/multi_machine": lambda: self.serve_multi_machine(),
            "/protocal.csp": lambda: self.serve_protocal_csp(),
            "/printer/objects/query": lambda: self.serve_state_query(),
            "/printer/print/start": lambda: self.serve_print_start(),
            "/api/rest/print/cluster/devices/getDevices": lambda: self.serve_get_devices(),
            "/api/rest/print/cluster/devices/getDeviceDetail": lambda: self.serve_print_cluster_device_detail(),
            "/api/rest/print/cluster/devices/pollState": lambda: self.serve_poll_state(),
            "/api/cxy/v3/print/record/detail": lambda: self.serve_print_record_detail(path),
            "/api/cxy/v3/print/record/list": lambda: self.serve_print_record_list(path),
            "/api/cxy/v2/device/uploadVideos": lambda: self.serve_device_upload_videos(path),
        }
        if path in STREAM_PATHS:
            self.serve_stream_probe(path)
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "GET", query, duration_ms=duration_ms)
            return
        if path == STATUS_PAGE_PATH or path == f"{STATUS_PAGE_PATH}/":
            self.serve_status_page()
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "GET", query, duration_ms=duration_ms)
            return
        if path in route_handlers:
            route_handlers[path]()
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "GET", query, duration_ms=duration_ms)
            return
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        self._record_request_audit(path, "GET", query, duration_ms=duration_ms)
        self.send_error(404, "Not Found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        user_agent = self.headers.get("User-Agent", "")
        started = datetime.now(timezone.utc)
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
        debug_log(f"[POST] {self.address_string()} {path} query={query} content_length={content_length} ua={user_agent}")
        if path == "/call/webrtc_local":
            self.serve_stream_probe(path, method="POST")
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "POST", query, body=body_bytes, duration_ms=duration_ms)
            return
        if path.startswith("/api/rest/iotrouter/rpc/") or path.startswith("/api/cxy/v2/iotrouter/rpc"):
            payload = self._read_json_body(content_length, body_bytes=body_bytes)
            self.serve_iotrouter_rpc(path, payload)
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "POST", query, body=payload, duration_ms=duration_ms)
            return
        if path == "/server/files/upload" or path.startswith("/upload/"):
            self.serve_upload_compat(path, body_bytes)
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "POST", query, body=body_bytes, duration_ms=duration_ms)
            return

        body = self._read_json_body(content_length, body_bytes=body_bytes)
        route_handlers = {
            "/printer/print/start": lambda: self.serve_print_start(body),
            "/api/rest/print/cluster/devices/getDeviceCount": lambda: self.serve_get_device_count(),
            "/api/rest/print/cluster/devices/getDevices": lambda: self.serve_get_devices(),
            "/api/rest/print/cluster/devices/getDeviceDetail": lambda: self.serve_print_cluster_device_detail(body),
            "/api/rest/print/cluster/devices/pollState": lambda: self.serve_poll_state(body),
            "/api/rest/print/cluster/addSingleTask": lambda: self.serve_add_single_task(body),
            "/api/rest/print/cluster/devices/edit": lambda: self.serve_print_cluster_device_edit(body),
            "/api/cxy/v3/print/record/detail": lambda: self.serve_print_record_detail(path),
            "/api/cxy/v3/print/record/list": lambda: self.serve_print_record_list(path),
            "/api/cxy/v2/device/uploadVideos": lambda: self.serve_device_upload_videos(path),
        }
        if path in route_handlers:
            route_handlers[path]()
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "POST", query, body=body, duration_ms=duration_ms)
            return
        if path in {"/printer/print/cancel", "/printer/print/stop", "/printer/cancel", "/printer/emergency_stop"}:
            self.serve_print_cancel(path)
            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self._record_request_audit(path, "POST", query, body=body, duration_ms=duration_ms)
            return
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        self._record_request_audit(path, "POST", query, body=body, duration_ms=duration_ms)
        self.send_error(404, "Not Found")

    def _build_status_page_html(self, checks, trace_lines=None, audit_entries=None, payload_summaries=None, state_history=None):
        healthy = all(item["ok"] for item in checks)
        summary = "All printer-facing services are up" if healthy else "One or more printer-facing services are down"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        rows = []
        for item in checks:
            badge = "UP" if item["ok"] else "DOWN"
            rows.append(
                f"<tr><td>{item['name']}</td><td><span class='badge {badge.lower()}'>{badge}</span></td><td>{item['detail']}</td></tr>"
            )
        trace_html = ""
        if trace_lines:
            trace_items = "".join(f"<li>{line}</li>" for line in trace_lines)
            trace_html = f"""
  <section style='margin-top: 18px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;'>
    <h2 style='margin-top: 0; font-size: 1.05em;'>Compatibility trace</h2>
    <ul style='margin: 0; padding-left: 18px; color: #374151;'>
      {trace_items}
    </ul>
  </section>
"""
        audit_html = ""
        if audit_entries:
            audit_items = "".join(
                f"<li><strong>{entry.get('method', 'GET')}</strong> {entry.get('route', '-')} <span class='muted'>{entry.get('query', '')}</span> <span class='muted'>{'keys=' + ','.join(entry.get('body_keys', []) or []) if entry.get('body_keys') else ''}</span> <span class='muted'>{entry.get('body_preview', '')}</span> <span class='muted'>{'ms=' + str(entry.get('duration_ms', '')) if entry.get('duration_ms') is not None else ''}</span> <span class='muted'>@ {entry.get('timestamp', '')}</span></li>"
                for entry in audit_entries
            )
            audit_html = f"""
  <section style='margin-top: 18px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;'>
    <h2 style='margin-top: 0; font-size: 1.05em;'>Compatibility audit</h2>
    <ul style='margin: 0; padding-left: 18px; color: #374151;'>
      {audit_items}
    </ul>
  </section>
"""
        snapshots_html = ""
        if payload_summaries:
            snapshot_items = []
            for route, summary in payload_summaries.items():
                snapshot_items.append(f"<li><strong>{route}</strong>: {json.dumps(summary, sort_keys=True)}</li>")
            snapshots_html = f"""
  <section style='margin-top: 18px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;'>
    <h2 style='margin-top: 0; font-size: 1.05em;'>Last payload summaries</h2>
    <ul style='margin: 0; padding-left: 18px; color: #374151;'>
      {''.join(snapshot_items)}
    </ul>
  </section>
"""
        state_history_html = ""
        if state_history:
            history_items = []
            for entry in state_history:
                history_items.append(
                    f"<li><strong>{entry.get('step', '-')}</strong> identity={entry.get('identity')} deviceName={entry.get('deviceName')} address={entry.get('address')} persisted_identity={entry.get('persisted_identity')} persisted_deviceName={entry.get('persisted_deviceName')} state={entry.get('state')} deviceState={entry.get('deviceState')} @ {entry.get('timestamp', '')}</li>"
                )
            state_history_html = f"""
  <section style='margin-top: 18px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;'>
    <h2 style='margin-top: 0; font-size: 1.05em;'>State transition history</h2>
    <ul style='margin: 0; padding-left: 18px; color: #374151;'>
      {''.join(history_items)}
    </ul>
  </section>
"""
        html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Printer Status</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111827; background: #f8fafc; }}
    h1 {{ margin-bottom: 8px; }}
    .summary {{ margin-bottom: 16px; padding: 12px 14px; border-radius: 8px; background: #ffffff; border: 1px solid #e5e7eb; }}
    table {{ width: 100%; border-collapse: collapse; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid #e5e7eb; text-align: left; }}
    th {{ background: #f3f4f6; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 0.85em; font-weight: bold; color: #fff; }}
    .badge.up {{ background: #16a34a; }}
    .badge.down {{ background: #dc2626; }}
    .muted {{ color: #6b7280; font-size: 0.95em; }}
  </style>
</head>
<body>
  <h1>Printer status</h1>
  <div class='summary'><strong>{summary}</strong><br><span class='muted'>Last checked: {now}</span></div>
  <table>
    <thead><tr><th>Service</th><th>Status</th><th>Detail</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  {trace_html}
  {audit_html}
  {snapshots_html}
  {state_history_html}
</body>
</html>"""
        return html

    def serve_status_page(self):
        checks = self._collect_status_checks()
        trace_lines = self._collect_contract_trace_lines()
        audit_entries = []
        payload_summaries = {}
        state_history = []
        if hasattr(self.server, "_compat_audit"):
            audit_entries = list(self.server._compat_audit)
        if hasattr(self.server, "_compat_payload_snapshots"):
            payload_summaries = dict(self.server._compat_payload_snapshots)
        if hasattr(self.server, "_compat_state_history"):
            state_history = list(self.server._compat_state_history)
        html = self._build_status_page_html(checks, trace_lines=trace_lines, audit_entries=audit_entries, payload_summaries=payload_summaries, state_history=state_history)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_info(self):
        payload = self._build_info_payload()
        persisted_identity = self._load_persisted_identity()
        self._record_state_transition("/info", payload, persisted_identity=persisted_identity)
        self._record_payload_snapshot("/info", payload)
        debug_log(f"[RESPONSE] /info -> {json.dumps(payload, sort_keys=True)[:4000]}")
        self._send_json(payload)

    def _request_host(self):
        headers = getattr(self, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            return ""
        host = headers.get("X-Forwarded-Host") or headers.get("Host") or ""
        if not host:
            return ""
        host = host.split(",", 1)[0].strip()
        if not host:
            return ""
        if host.startswith(("http://", "https://")):
            parsed = urllib.parse.urlsplit(host)
            return parsed.hostname or ""
        return host.split(":", 1)[0].strip() if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit() else host

    def _request_scheme(self):
        headers = getattr(self, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            forwarded_proto = (headers.get("X-Forwarded-Proto", "http") or "http").split(",", 1)[0].strip() or "http"
            if forwarded_proto:
                return forwarded_proto
        return "http"

    def _is_local_request_host(self, host):
        if not host:
            return True
        host = host.lower().strip()
        if host in {"127.0.0.1", "localhost"}:
            return True
        if host.startswith("127."):
            return True
        if host.startswith("0.0.0.0"):
            return True
        return False

    def _stream_base_url(self):
        request_host = self._request_host()
        if request_host and not self._is_local_request_host(request_host):
            return f"{self._request_scheme()}://{request_host}"

        public_host = os.environ.get("PUBLIC_HOST", "").strip()
        if public_host:
            public_scheme = (os.environ.get("PUBLIC_SCHEME", "https") or "https").strip() or "https"
            return f"{public_scheme}://{public_host}"

        if request_host:
            return f"{self._request_scheme()}://{request_host}"
        return "http://127.0.0.1"

    def _public_address(self):
        request_host = self._request_host()
        if request_host:
            if not self._is_local_request_host(request_host):
                return request_host
            if request_host not in {"127.0.0.1", "localhost"}:
                return request_host

        public_host = os.environ.get("PUBLIC_HOST", "").strip()
        if public_host:
            return public_host

        return self._guess_ip() or "192.168.1.100"

    def _looks_like_ip(self, value):
        if not isinstance(value, str):
            return False
        value = value.strip()
        if not value:
            return False
        try:
            socket.inet_aton(value)
            return True
        except OSError:
            return False

    def _preferred_printer_identity_address(self, network=None):
        request_host = self._request_host()
        if request_host and not self._is_local_request_host(request_host) and self._looks_like_ip(request_host):
            return request_host

        if isinstance(network, dict):
            ip_address = self._find_first_ipv4(network)
            if ip_address:
                return ip_address

        guessed_ip = self._guess_ip()
        if guessed_ip:
            return guessed_ip
        return self._public_address() or "127.0.0.1"

    def _build_stream_probe_payload(self, path, method="GET"):
        stream_base_url = self._stream_base_url()
        producer_url = f"webrtc:{stream_base_url}/call/webrtc_local#format=creality"
        return {
            "cam": {"producers": [{"url": "webcam"}], "consumers": []},
            "camera": {"producers": [{"url": "webcam"}], "consumers": []},
            "webcam": {
                "producers": [
                    {"url": producer_url},
                    {"url": f"ffmpeg:{stream_base_url}/?action=stream#video=mjpeg"},
                ],
                "consumers": [],
            },
            "webcam1": {"producers": [{"url": "webcam"}], "consumers": []},
            "ok": True,
            "path": path,
            "method": method,
            "stream": True,
        }

    def serve_stream_probe(self, path, method="GET"):
        if method == "POST" and path == "/call/webrtc_local":
            self._send_webrtc_answer()
            return
        payload = self._build_stream_probe_payload(path, method=method)
        self._send_json(payload)

    def _read_json_body(self, content_length, body_bytes=None):
        body = body_bytes if body_bytes is not None else (self.rfile.read(content_length) if content_length > 0 else b"")
        if not body:
            return {}
        if isinstance(body, (bytes, bytearray)):
            body_bytes = bytes(body)
            try:
                return json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        return body

    def serve_iotrouter_rpc(self, path, payload):
        if not isinstance(payload, dict):
            payload = {}

        params = payload.get("params", {}) if isinstance(payload.get("params"), dict) else {}
        info = self._build_info_payload()
        boxs_info = info.get("boxsInfo") if isinstance(info.get("boxsInfo"), dict) else {}
        box_config = info.get("boxConfig") if isinstance(info.get("boxConfig"), dict) else {}

        cfs_name = boxs_info.get("cfsName") or info.get("name") or DEFAULT_CFS_NAME
        same_material = boxs_info.get("same_material") or []
        if not isinstance(same_material, list):
            same_material = []

        if params.get("cfsList") is not None or "cfsList" in params:
            cfs_list = []
            for box in boxs_info.get("materialBoxs", []) or []:
                materials = []
                for material in box.get("materials", []) or []:
                    color = material.get("color") or ""
                    if isinstance(color, str) and len(color) == 6 and not color.startswith("#"):
                        color = f"#{color}"
                    filament_type = material.get("type") or material.get("filamentType") or "PLA"
                    material_name = material.get("name") or filament_type or "Material"
                    materials.append({
                        "id": material.get("id", 0),
                        "cId": material.get("cId", material.get("id", 0)),
                        "name": material_name,
                        "color": color,
                        "filamentsColor": color,
                        "filamentProgress": max(0.0, min(1.0, (material.get("percent") or 0) / 100.0)),
                        "selected": bool(material.get("selected", 0)),
                        "filamentType": filament_type,
                        "rfid": material.get("rfid", ""),
                        "rfidState": material.get("state", 0),
                        "editStatus": material.get("editStatus", 0),
                        "remaining_length": material.get("remaining_length", 0),
                    })
                if materials or box.get("id") is not None:
                    cfs_list.append({
                        "id": box.get("id", 1),
                        "portList": materials,
                    })
            self._send_json({
                "code": 0,
                "message": "success",
                "result": {
                    "cfsName": cfs_name,
                    "cfsList": cfs_list,
                    "same_material": same_material,
                },
            })
            return

        if params.get("cfsInfo") is not None or "cfsInfo" in params:
            self._send_json({
                "code": 0,
                "message": "success",
                "result": {
                    "cAutoFeed": box_config.get("cAutoFeed", 1),
                    "cSelfTest": box_config.get("cSelfTest", 0),
                    "autoRefill": box_config.get("autoRefill", 1),
                    "ignoreColorAutoFeed": box_config.get("ignoreColorAutoFeed", 0),
                    "ignoreColorToRefill": box_config.get("ignoreColorAutoFeed", 0),
                    "cMode": box_config.get("cMode", 0),
                },
            })
            return

        if params.get("cfsStatus") is not None or "cfsStatus" in params:
            self._send_json({
                "code": 0,
                "message": "success",
                "result": {
                    "cfsName": cfs_name,
                    "cfsStatus": {
                        "online": True,
                        "state": 0,
                        "connected": True,
                        "boxsInfo": {
                            "same_material": same_material,
                            "color_same_material": boxs_info.get("color_same_material") or [],
                            "boxColorInfo": boxs_info.get("boxColorInfo") or [],
                            "materialBoxs": boxs_info.get("materialBoxs") or [],
                            "cfsName": cfs_name,
                        },
                        "boxConfig": box_config,
                        "filamentsList": [
                            {
                                "id": material.get("id", 0),
                                "cId": material.get("cId", material.get("id", 0)),
                                "name": material.get("name") or material.get("filamentType") or "Material",
                                "color": material.get("color") or "#000000",
                                "type": material.get("type") or material.get("filamentType") or "PLA",
                                "selected": bool(material.get("selected", 0)),
                                "percent": material.get("percent", 100),
                                "remaining_length": material.get("remaining_length", 0),
                                "state": material.get("state", 1),
                            }
                            for box in boxs_info.get("materialBoxs", []) or []
                            for material in box.get("materials", []) or []
                        ],
                    },
                },
            })
            return

        if "cId" in params:
            target_cid = params.get("cId")
            material_info = None
            for box in boxs_info.get("materialBoxs", []) or []:
                for material in box.get("materials", []) or []:
                    material_id = material.get("cId", material.get("id", 0))
                    if material_id == target_cid or material.get("id") == target_cid:
                        material_info = material
                        break
                if material_info is not None:
                    break

            if material_info is None:
                material_info = {}

            color = material_info.get("color") or ""
            if isinstance(color, str) and len(color) == 6 and not color.startswith("#"):
                color = f"#{color}"
            elif not color:
                color = "#000000"

            filament_type = material_info.get("type") or material_info.get("filamentType") or "PLA"
            material_name = material_info.get("name") or filament_type or "Material"
            filament_progress = max(0.0, min(1.0, (material_info.get("percent") or 0) / 100.0)) if material_info else 0.0
            self._send_json({
                "code": 0,
                "message": "success",
                "result": {
                    "id": material_info.get("id", target_cid),
                    "cId": target_cid,
                    "name": material_name,
                    "color": color,
                    "filamentType": filament_type,
                    "type": filament_type,
                    "filamentsColor": color,
                    "filamentProgress": filament_progress,
                    "selected": bool(material_info.get("selected", 0)),
                    "percent": material_info.get("percent", 100),
                    "remaining_length": material_info.get("remaining_length", 0),
                    "state": material_info.get("state", 1),
                    "nozzleTempMax": 220,
                    "nozzleTempMin": 0,
                    "boxsInfo": boxs_info,
                    "boxConfig": box_config,
                    "same_material": same_material,
                },
            })
            return

        if any(key in params for key in ("lightSw", "ledSw", "modelFanPct", "auxiliaryFanPct", "fanAuxiliary", "caseFanPct", "sideFanPct", "setPosition", "autohome", "gcodeCmd", "feedStateTemp2", "feed", "cRFIDRefresh")):
            control_result = {
                "ok": True,
                "updated": True,
                "params": params,
                "lightSw": params.get("lightSw", 0),
                "ledSw": params.get("ledSw", 0),
                "modelFanPct": params.get("modelFanPct", 0),
                "auxiliaryFanPct": params.get("auxiliaryFanPct", 0),
                "fanAuxiliary": params.get("fanAuxiliary", 0),
                "caseFanPct": params.get("caseFanPct", 0),
                "sideFanPct": params.get("sideFanPct", 0),
                "setPosition": params.get("setPosition"),
                "autohome": params.get("autohome"),
                "gcodeCmd": params.get("gcodeCmd"),
                "feedStateTemp2": params.get("feedStateTemp2"),
                "feed": params.get("feed"),
                "cRFIDRefresh": params.get("cRFIDRefresh"),
                "cId": params.get("cId"),
                "cfsName": cfs_name,
                "same_material": same_material,
                "boxsInfo": boxs_info,
                "boxConfig": box_config,
            }
            self._send_json({
                "code": 0,
                "message": "success",
                "result": control_result,
            })
            return

        if params.get("pFileList") is not None or "pFileList" in params:
            response_payload = self._build_iotrouter_rpc_file_list_response(params)
            self._record_payload_snapshot("/api/rest/iotrouter/rpc/twoway", response_payload)
            self._send_json(response_payload)
            return

        if any(key in params for key in ("reqGcodeFile", "reqGcodeList", "reqMaterials", "boxsInfo", "boxConfig", "getToken")):
            self._send_json({
                "code": 0,
                "message": "success",
                "result": {
                    "reqGcodeFile": {"ok": True, "path": "", "filename": ""},
                    "reqGcodeList": [],
                    "reqMaterials": {
                        "cfsName": cfs_name,
                        "cfsList": [
                            {
                                "id": box.get("id", 1),
                                "portList": [
                                    {
                                        "id": material.get("id", 0),
                                        "cId": material.get("cId", material.get("id", 0)),
                                        "name": material.get("name") or material.get("filamentType") or material.get("type") or "Material",
                                        "color": f"#{material.get('color', '').lstrip('#')}" if isinstance(material.get("color"), str) and material.get("color", "").strip() and not material.get("color", "").startswith("#") else (material.get("color") or "#000000"),
                                        "filamentsColor": f"#{material.get('color', '').lstrip('#')}" if isinstance(material.get("color"), str) and material.get("color", "").strip() and not material.get("color", "").startswith("#") else (material.get("color") or "#000000"),
                                        "filamentProgress": max(0.0, min(1.0, (material.get("percent") or 0) / 100.0)),
                                        "selected": bool(material.get("selected", 0)),
                                        "filamentType": material.get("type") or material.get("filamentType") or "PLA",
                                        "rfid": material.get("rfid", ""),
                                        "rfidState": material.get("state", 0),
                                        "editStatus": material.get("editStatus", 0),
                                        "remaining_length": material.get("remaining_length", 0),
                                    }
                                    for material in box.get("materials", []) or []
                                ],
                            }
                            for box in boxs_info.get("materialBoxs", []) or []
                        ],
                        "same_material": same_material,
                    },
                    "boxsInfo": {"same_material": same_material, "color_same_material": boxs_info.get("color_same_material") or [], "boxColorInfo": boxs_info.get("boxColorInfo") or [], "materialBoxs": boxs_info.get("materialBoxs") or [], "cfsName": cfs_name},
                    "boxConfig": {"cAutoFeed": box_config.get("cAutoFeed", 1), "cSelfTest": box_config.get("cSelfTest", 0), "autoRefill": box_config.get("autoRefill", 1), "ignoreColorAutoFeed": box_config.get("ignoreColorAutoFeed", 0), "ignoreColorToRefill": box_config.get("ignoreColorAutoFeed", 0), "cMode": box_config.get("cMode", 0)},
                    "getToken": f"lan-compat-{uuid.uuid4().hex[:16]}",
                },
            })
            return

        self._send_json({
            "code": 0,
            "message": "success",
            "result": {
                "cfsName": cfs_name,
                "cfsList": [],
                "same_material": same_material,
                "cAutoFeed": box_config.get("cAutoFeed", 1),
                "cSelfTest": box_config.get("cSelfTest", 0),
                "autoRefill": box_config.get("autoRefill", 1),
                "ignoreColorAutoFeed": box_config.get("ignoreColorAutoFeed", 0),
            },
        })

    def serve_system_info(self):
        try:
            data = self._fetch_json("/machine/system_info", timeout=UPSTREAM_TIMEOUT)
        except Exception:
            data = {"result": {"system_info": {"network": {}}}}

        if isinstance(data, dict):
            result = data.setdefault("result", {})
            system_info = result.setdefault("system_info", {})
            network = system_info.setdefault("network", {})
            wlan0_entry = network.get("wlan0")
            if not isinstance(wlan0_entry, dict):
                wlan0_entry = {}
                network["wlan0"] = wlan0_entry

            fallback_mac = self._find_first_mac(network) or "00:00:00:00:00:00"
            fallback_ip = self._find_first_ipv4(network) or self._guess_ip()
            if not wlan0_entry.get("mac_address"):
                wlan0_entry["mac_address"] = fallback_mac
            if fallback_ip and not wlan0_entry.get("ip_addresses"):
                wlan0_entry["ip_addresses"] = [{"address": fallback_ip, "family": 4}]
            self._send_json(data)
            return

        self._send_json({"result": {"system_info": {"network": {}}}})

    def _normalize_features(self, features=None):
        feature_list = list(features or [])
        if not feature_list:
            feature_list = ["videoInfo.video", "printControl.xyzControl001005010"]

        preferred_order = ["videoInfo.videoEncryption", "videoInfo.video", "printControl.xyzControl001005010"]
        normalized = []
        for feature in preferred_order:
            if feature in feature_list or feature == "videoInfo.videoEncryption":
                if feature not in normalized:
                    normalized.append(feature)
        for feature in feature_list:
            if feature not in normalized:
                normalized.append(feature)
        return normalized

    def _normalize_text_value(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip().lower()
        return str(value).strip().lower()

    def _normalize_token_set(self, value):
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set)):
            tokens = []
            for item in value:
                normalized = self._normalize_text_value(item)
                if normalized:
                    tokens.append(normalized)
            return set(tokens)
        return {self._normalize_text_value(token) for token in str(value).replace(",", " ").split() if self._normalize_text_value(token)}

    def _evaluate_preset_compatibility(self, device, preset):
        device_profile = {
            "label": self._normalize_text_value(device.get("name") or device.get("model") or device.get("label")),
            "family": self._normalize_text_value(device.get("family") or device.get("modelFamily") or device.get("model")),
            "nozzle": float(device.get("nozzleSize") or device.get("nozzle") or 0.0),
            "connected": bool(device.get("connected") or device.get("online") or device.get("isOnline") or False),
            "traits": self._normalize_token_set(device.get("traits") or device.get("capabilities") or device.get("features")),
            "tags": self._normalize_token_set(device.get("tags") or device.get("labels")),
        }
        preset_profile = {
            "family": self._normalize_text_value(preset.get("family") or preset.get("modelFamily") or preset.get("model")),
            "nozzle": float(preset.get("nozzleSize") or preset.get("nozzle") or 0.0),
            "traits": self._normalize_token_set(preset.get("requiredTraits") or preset.get("requiredFeatures") or preset.get("traits")),
            "tags": self._normalize_token_set(preset.get("requiredTags") or preset.get("tags")),
        }

        reasons = []
        if not device_profile["connected"]:
            reasons.append("device is not connected")

        family_match = (
            not preset_profile["family"]
            or not device_profile["family"]
            or device_profile["family"] == preset_profile["family"]
            or device_profile["family"] in preset_profile["family"]
            or preset_profile["family"] in device_profile["family"]
        )
        if not family_match:
            reasons.append("family mismatch")

        nozzle_match = not preset_profile["nozzle"] or not device_profile["nozzle"] or device_profile["nozzle"] == preset_profile["nozzle"]
        if not nozzle_match:
            reasons.append("nozzle mismatch")

        missing_traits = sorted([trait for trait in preset_profile["traits"] if trait not in device_profile["traits"] and trait not in device_profile["tags"]])
        if missing_traits:
            reasons.append("missing required traits")

        missing_tags = sorted([tag for tag in preset_profile["tags"] if tag not in device_profile["tags"] and tag not in device_profile["traits"]])
        if missing_tags:
            reasons.append("missing required tags")

        return {
            "compatible": not reasons,
            "reasons": reasons,
            "profile": {
                "label": device_profile["label"],
                "family": device_profile["family"],
                "nozzle": device_profile["nozzle"],
                "traits": device_profile["traits"],
                "tags": device_profile["tags"],
            },
        }

    def _build_media_hydration_lists(self, info_payload=None):
        info_payload = info_payload or self._build_info_payload()
        record_entries = [self._build_compat_record_entry(index, record_id=f"record-{index + 1}") for index in range(1)]
        file_entries = [self._build_compat_file_entry(index, filename="lan-compat.gcode" if index == 0 else f"lan-compat-{index + 1}.gcode") for index in range(1)]
        return {
            "historyList": record_entries,
            "pFileList": file_entries,
            "recordList": record_entries,
            "fileList": file_entries,
        }

    def _build_compat_device_entry(self, info_payload=None, state_value=None, device_state_value=None):
        info_payload = info_payload or self._build_info_payload()
        state_value = info_payload.get("state") if state_value is None else state_value
        device_state_value = info_payload.get("deviceState") if device_state_value is None else device_state_value
        identity_fields = self._build_device_identity_fields(info_payload)
        display_name = identity_fields["display_name"]
        preferred_identity_address = self._preferred_printer_identity_address() or self._public_address() or info_payload.get("address") or "127.0.0.1"
        address_value = info_payload.get("address") or preferred_identity_address or "127.0.0.1"
        ui_display_name = self._first_present(
            info_payload.get("aliasName"),
            info_payload.get("deviceName"),
            info_payload.get("name"),
            display_name,
        ) or display_name
        if info_payload.get("isLanPrinter") is True:
            identity_value = None
        else:
            identity_value = info_payload.get("identity") or address_value
        model_value = identity_fields["model"]
        machine_name_value = identity_fields["machine_name"]
        machine_type_value = identity_fields["machine_type"]
        features_value = self._normalize_features(info_payload.get("features"))
        boxs_info = info_payload.get("boxsInfo") or {}
        box_config = info_payload.get("boxConfig") or {}
        media_lists = self._build_media_hydration_lists(info_payload)
        local_online = info_payload.get("localOnline", True)
        cloud_online = info_payload.get("cloudOnline", False)
        cxy_online = info_payload.get("cxyOnline", False)
        is_exist_in_local = info_payload.get("isExistInLocal", True)
        is_exist_in_cxy = info_payload.get("isExistInCxy", False)
        return {
            "deviceName": address_value,
            "aliasName": ui_display_name,
            "name": ui_display_name,
            "model": model_value,
            "modelName": info_payload.get("modelName") or model_value,
            "machine_name": machine_name_value,
            "machine_type": machine_type_value,
            "mac": info_payload.get("mac") or self._guess_mac() or "00:00:00:00:00:00",
            "address": address_value,
            "identity": identity_value,
            "deviceType": info_payload.get("deviceType", 0),
            "video": 1 if info_payload.get("video") is True or info_payload.get("video") in (1, "1") else 0,
            "tbId": info_payload.get("tbId") or "lan-compat-tb-id",
            "keyFileToken": info_payload.get("keyFileToken") or "lan-compat-key-token",
            "videoToken": info_payload.get("videoToken") or "lan-compat-video-token",
            "previewimg": info_payload.get("previewimg") or info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
            "deviceImg": info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
            "defaultDeviceImg": info_payload.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
            "printerImagePath": info_payload.get("printerImagePath") or "",
            "features": features_value,
            "linuxVideoUrl": info_payload.get("linuxVideoUrl") or f"{self._stream_base_url()}/api/v1/streams",
            "webrtcSupport": info_payload.get("webrtcSupport") if info_payload.get("webrtcSupport") is not None else True,
            "connectType": info_payload.get("connectType", 1001),
            "isLanPrinter": True,
            "lanCompatible": True,
            "oldPrinter": False,
            "localOnline": local_online,
            "cloudOnline": cloud_online,
            "cxyOnline": cxy_online,
            "isExistInLocal": is_exist_in_local,
            "isExistInCxy": is_exist_in_cxy,
            "state": state_value,
            "deviceState": device_state_value,
            "uploadState": info_payload.get("uploadState", 0),
            "temperature": info_payload.get("temperature") or {},
            "status": info_payload.get("status") or {},
            "boxsInfo": boxs_info,
            "boxConfig": box_config,
            "supportMultiple": info_payload.get("supportMultiple") if info_payload.get("supportMultiple") is not None else True,
            "machinePlatformMotionEnable": info_payload.get("machinePlatformMotionEnable") if info_payload.get("machinePlatformMotionEnable") is not None else 1,
            "materialDetector1": info_payload.get("materialDetector1") if info_payload.get("materialDetector1") is not None else 1,
            "filamentsList": info_payload.get("filamentsList") or [{"cId": 1, "id": 1, "name": "Material", "color": DEFAULT_MATERIAL_COLOR, "type": "PLA", "selected": True, "progress": 1.0}],
            "streamState": info_payload.get("streamState") or {"active": True, "source": "webcam"},
            "cameraState": info_payload.get("cameraState") or {"enabled": True, "state": "ready"},
            "recordState": info_payload.get("recordState") or {"recording": False, "timelapse": False},
            "historyList": media_lists["historyList"],
            "pFileList": media_lists["pFileList"],
            "recordList": media_lists["recordList"],
            "fileList": media_lists["fileList"],
            "device": {
                "deviceName": address_value,
                "aliasName": ui_display_name,
                "name": ui_display_name,
                "model": model_value,
                "modelName": info_payload.get("modelName") or model_value,
                "machine_name": machine_name_value,
                "machine_type": machine_type_value,
                "address": address_value,
                "identity": identity_value,
                "deviceType": info_payload.get("deviceType", 0),
                "video": 1 if info_payload.get("video") is True or info_payload.get("video") in (1, "1") else 0,
                "tbId": info_payload.get("tbId") or "lan-compat-tb-id",
                "keyFileToken": info_payload.get("keyFileToken") or "lan-compat-key-token",
                "videoToken": info_payload.get("videoToken") or "lan-compat-video-token",
                "previewimg": info_payload.get("previewimg") or info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                "deviceImg": info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                "defaultDeviceImg": info_payload.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
                "printerImagePath": info_payload.get("printerImagePath") or "",
                "features": features_value,
                "linuxVideoUrl": info_payload.get("linuxVideoUrl") or f"{self._stream_base_url()}/api/v1/streams",
                "webrtcSupport": info_payload.get("webrtcSupport") if info_payload.get("webrtcSupport") is not None else True,
                "state": state_value,
                "deviceState": device_state_value,
                "uploadState": info_payload.get("uploadState", 0),
                "temperature": info_payload.get("temperature") or {},
                "status": info_payload.get("status") or {},
                "boxsInfo": boxs_info,
                "boxConfig": box_config,
                "supportMultiple": info_payload.get("supportMultiple") if info_payload.get("supportMultiple") is not None else True,
                "machinePlatformMotionEnable": info_payload.get("machinePlatformMotionEnable") if info_payload.get("machinePlatformMotionEnable") is not None else 1,
                "materialDetector1": info_payload.get("materialDetector1") if info_payload.get("materialDetector1") is not None else 1,
                "filamentsList": info_payload.get("filamentsList") or [{"cId": 1, "id": 1, "name": "Material", "color": DEFAULT_MATERIAL_COLOR, "type": "PLA", "selected": True, "progress": 1.0}],
                "streamState": info_payload.get("streamState") or {"active": True, "source": "webcam"},
                "cameraState": info_payload.get("cameraState") or {"enabled": True, "state": "ready"},
                "recordState": info_payload.get("recordState") or {"recording": False, "timelapse": False},
                "historyList": media_lists["historyList"],
                "pFileList": media_lists["pFileList"],
                "recordList": media_lists["recordList"],
                "fileList": media_lists["fileList"],
            },
        }

    def serve_machine_info(self):
        payload = self._build_info_payload()
        debug_log(f"[RESPONSE] /machine/info -> {json.dumps(payload, sort_keys=True)[:4000]}")
        self._send_json(payload)

    def serve_get_device_count(self):
        payload = {
            "code": 0,
            "message": "success",
            "result": 1,
            "count": 1,
        }
        self._send_json(payload)

    def serve_get_devices(self):
        info_payload = self._build_info_payload()
        state_value = info_payload.get("state", 0)
        device_state_value = info_payload.get("deviceState", 0)
        compat_device = self._build_compat_device_entry(info_payload, state_value, device_state_value)
        payload = {
            "code": 0,
            "message": "success",
            "result": {
                "multi_printer_info": [compat_device],
            },
            "data": {
                "currentActivePrinterMac": compat_device.get("mac") or "",
                "printerList": [
                    {
                        "group": "LAN",
                        "list": [
                            {
                                "address": compat_device.get("address") or "",
                                "mac": compat_device.get("mac") or "",
                                "model": compat_device.get("model") or compat_device.get("modelName") or "",
                                "modelName": compat_device.get("modelName") or compat_device.get("model") or "",
                                "model_name": compat_device.get("model_name") or compat_device.get("modelName") or compat_device.get("model") or "",
                                "machine_name": compat_device.get("machine_name") or compat_device.get("deviceName") or compat_device.get("aliasName") or compat_device.get("name") or "",
                                "machine_type": compat_device.get("machine_type") or compat_device.get("model") or compat_device.get("modelName") or "",
                                "name": compat_device.get("deviceName") or compat_device.get("aliasName") or compat_device.get("name") or "",
                                "deviceName": compat_device.get("deviceName") or compat_device.get("aliasName") or compat_device.get("name") or "",
                                "aliasName": compat_device.get("aliasName") or compat_device.get("deviceName") or compat_device.get("name") or "",
                                "deviceType": compat_device.get("deviceType", 0),
                                "type": compat_device.get("deviceType", 0),
                                "tbId": compat_device.get("tbId") or "lan-compat-tb-id",
                                "videoToken": compat_device.get("videoToken") or "lan-compat-video-token",
                                "online": True,
                                "state": state_value,
                                "deviceState": device_state_value,
                                "boxsInfo": compat_device.get("boxsInfo") or {},
                                "boxConfig": compat_device.get("boxConfig") or {},
                                "previewimg": compat_device.get("previewimg") or "",
                                "deviceImg": compat_device.get("deviceImg") or "",
                                "defaultDeviceImg": compat_device.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
                                "printerImagePath": compat_device.get("printerImagePath") or "",
                                "features": compat_device.get("features") or [],
                                "linuxVideoUrl": compat_device.get("linuxVideoUrl") or "",
                                "webrtcSupport": compat_device.get("webrtcSupport") is not False,
                                "connectType": compat_device.get("connectType", 1001),
                                "identity": compat_device.get("identity"),
                                "isLanPrinter": True,
                                "lanCompatible": True,
                                "oldPrinter": False,
                                "localOnline": compat_device.get("localOnline", True),
                                "cloudOnline": compat_device.get("cloudOnline", False),
                                "cxyOnline": compat_device.get("cxyOnline", False),
                                "isExistInLocal": compat_device.get("isExistInLocal", True),
                                "isExistInCxy": compat_device.get("isExistInCxy", False),
                                "temperature": compat_device.get("temperature") or {},
                                "status": compat_device.get("status") or {},
                                "streamState": compat_device.get("streamState") or {},
                                "cameraState": compat_device.get("cameraState") or {},
                                "recordState": compat_device.get("recordState") or {},
                                "uploadState": compat_device.get("uploadState", 0),
                                "ctrol": {
                                    "autohome": "X:0 Y:0 Z:0",
                                    "curPosition": "X:1 Y:1 Z:1",
                                    "curFeedratePct": 100,
                                    "speedMode": 1,
                                    "fan": 0,
                                    "modelFanPct": 0,
                                    "fanAuxiliary": 0,
                                    "auxiliaryFanPct": 0,
                                    "fanCase": 0,
                                    "caseFan": 0,
                                    "caseFanPct": 0,
                                    "sideFan": 0,
                                    "sideFanPct": 0,
                                    "chamberTemp": 0.0,
                                    "chamberTempTarget": 0.0,
                                    "ledSw": 0,
                                    "lightSw": 0,
                                },
                            }
                        ],
                    }
                ],
            },
        }
        self._send_json(payload)

    def serve_poll_state(self, payload=None):
        payload = self._read_detail_request_payload(payload)
        info_payload = self._build_info_payload()
        persisted_identity = self._load_persisted_identity()
        state_value = info_payload.get("state", 0)
        device_state_value = info_payload.get("deviceState", 0)
        compat_device = self._build_compat_device_entry(info_payload, state_value, device_state_value)
        if isinstance(payload, dict):
            # Preserve existing identity fields that the request doesn't provide.
            # The app's pollState request only carries dn/deviceName — no modelName.
            # Starting from the request as a blank slate would erase any previously
            # saved model / name across successive polls, which is what causes the
            # "model disappears after ~1 second" regression after a cache reset.
            persisted_payload = dict(persisted_identity)  # start from existing, not the request
            persisted_payload.setdefault("isLanPrinter", True)
            persisted_payload.setdefault("lanCompatible", True)
            persisted_payload.setdefault("oldPrinter", False)
            name_value = self._first_present(payload.get("deviceName"), payload.get("aliasName"), payload.get("name"))
            if name_value:
                persisted_payload.update({
                    "deviceName": payload.get("deviceName") or name_value,
                    "aliasName": payload.get("aliasName") or name_value,
                    "name": payload.get("name") or name_value,
                })
            # Only update modelName if the poll request itself carries one (rare).
            for _identity_key in ("modelName", "model"):
                if payload.get(_identity_key):
                    persisted_payload[_identity_key] = payload[_identity_key]
            if payload.get("address") or payload.get("dn"):
                persisted_payload["address"] = payload.get("address") or payload.get("dn")
                persisted_payload["identity"] = None
            self._save_persisted_identity(persisted_payload)
            request_name = self._first_present(payload.get("deviceName"), payload.get("aliasName"), payload.get("name"))
            request_address = self._first_present(payload.get("address"), payload.get("dn"))
            if request_address:
                compat_device["deviceName"] = request_address
                compat_device["address"] = request_address
                compat_device["device"]["deviceName"] = request_address
                compat_device["device"]["address"] = request_address
            if request_name:
                compat_device["aliasName"] = request_name
                compat_device["name"] = request_name
                compat_device["device"]["aliasName"] = request_name
                compat_device["device"]["name"] = request_name
        self._record_state_transition("/api/rest/print/cluster/devices/pollState", compat_device, persisted_identity=persisted_identity)
        compat_device["online"] = 1
        compat_device["status"] = info_payload.get("status") or "idle"
        compat_device["address"] = compat_device.get("address") or info_payload.get("address") or self._public_address() or "127.0.0.1"
        if info_payload.get("isLanPrinter") is True:
            compat_device["identity"] = None
            compat_device["device"]["identity"] = None
        else:
            compat_device["identity"] = info_payload.get("identity") or compat_device.get("identity") or compat_device.get("address") or "127.0.0.1"
            compat_device["device"]["identity"] = compat_device["identity"]
        payload = {
            "code": 0,
            "message": "success",
            "result": [compat_device],
        }
        self._send_json(payload)

    def serve_add_single_task(self, payload=None):
        payload = self._read_detail_request_payload(payload)
        info_payload = self._build_info_payload()
        persisted_identity = self._load_persisted_identity()
        
        # DEBUG: log addSingleTask flow
        import logging
        logging.getLogger(__name__).setLevel(logging.DEBUG)
        logger = logging.getLogger('probe_backend')
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            handler = logging.FileHandler('/tmp/creality_probe_backend_debug.log')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
        logger.debug(f"ADD_SINGLE_TASK: request_payload_keys={list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}")
        
        try:
            detail_response = self._build_device_detail_response(info_payload, self._build_detail_payload(), payload)
        except Exception:
            detail_response = None
        response_payload = {
            "code": 0,
            "message": "success",
            "result": {
                "printInfo": {},
            },
        }
        if isinstance(detail_response, dict) and isinstance(detail_response.get("result"), dict):
            result = detail_response["result"]
            print_info = {
                "deviceName": result.get("deviceName") or info_payload.get("deviceName") or info_payload.get("address") or "127.0.0.1",
                "aliasName": self._first_present(payload.get("aliasName"), payload.get("deviceName"), payload.get("name"), result.get("aliasName"), info_payload.get("name"), info_payload.get("aliasName"), info_payload.get("deviceName")),
                "name": self._first_present(payload.get("name"), payload.get("aliasName"), payload.get("deviceName"), result.get("name"), result.get("aliasName"), info_payload.get("name"), info_payload.get("aliasName"), info_payload.get("deviceName")),
                "modelName": result.get("modelName") or info_payload.get("modelName") or info_payload.get("model") or info_payload.get("machine_type") or info_payload.get("machine_name"),
                "model": result.get("model") or result.get("modelName") or info_payload.get("model") or info_payload.get("modelName") or info_payload.get("machine_type") or info_payload.get("machine_name"),
                "address": self._first_present(payload.get("address"), payload.get("dn"), result.get("address"), info_payload.get("address"), self._public_address(), "127.0.0.1"),
                "identity": None if info_payload.get("isLanPrinter") is True else (result.get("identity") if result.get("identity") is not None else (info_payload.get("identity") or self._public_address() or info_payload.get("address") or "127.0.0.1")),
                "deviceType": 0 if info_payload.get("isLanPrinter") is True else (result.get("deviceType") if result.get("deviceType") is not None else info_payload.get("deviceType") or 0),
                "type": 0 if info_payload.get("isLanPrinter") is True else (result.get("type") if result.get("type") is not None else info_payload.get("type") or 0),
                "connectType": 1001 if info_payload.get("isLanPrinter") is True else (result.get("connectType") if result.get("connectType") is not None else info_payload.get("connectType") or 1001),
                "isLanPrinter": info_payload.get("isLanPrinter") is True,
                "lanCompatible": info_payload.get("lanCompatible") is True,
                "oldPrinter": False,
                "state": result.get("state") if result.get("state") is not None else info_payload.get("state", 0),
                "deviceState": result.get("deviceState") if result.get("deviceState") is not None else info_payload.get("deviceState", 0),
                "localOnline": True,
                "cloudOnline": False,
                "cxyOnline": False,
                "isExistInLocal": True,
                "isExistInCxy": False,
            }
            response_payload["result"]["printInfo"] = print_info
        else:
            address_value = self._first_present(payload.get("address"), payload.get("dn"), info_payload.get("address"), self._public_address(), "127.0.0.1")
            request_name = self._first_present(payload.get("aliasName"), payload.get("deviceName"), payload.get("name"), info_payload.get("name"), info_payload.get("aliasName"), info_payload.get("deviceName"))
            print_info = {
                "deviceName": address_value,
                "aliasName": request_name,
                "name": request_name,
                "modelName": info_payload.get("modelName") or info_payload.get("model") or info_payload.get("machine_type") or info_payload.get("machine_name"),
                "model": info_payload.get("model") or info_payload.get("modelName") or info_payload.get("machine_type") or info_payload.get("machine_name"),
                "address": address_value,
                "identity": None,
                "deviceType": 0,
                "type": 0,
                "connectType": 1001,
                "isLanPrinter": True,
                "lanCompatible": True,
                "oldPrinter": False,
                "state": info_payload.get("state", 0),
                "deviceState": info_payload.get("deviceState", 0),
                "localOnline": True,
                "cloudOnline": False,
                "cxyOnline": False,
                "isExistInLocal": True,
                "isExistInCxy": False,
            }
            response_payload["result"]["printInfo"] = print_info
        self._record_state_transition("/api/rest/print/cluster/addSingleTask", response_payload.get("result", {}), persisted_identity=persisted_identity)
        
        # Persist the resolved identity fields (modelName, model, name) back to disk
        # so subsequent requests like pollState don't lose them.
        save_payload = dict(persisted_identity)
        for _k in ("deviceName", "aliasName", "name", "machine_name", "model", "modelName", "address", "identity"):
            if print_info.get(_k):
                save_payload[_k] = print_info[_k]
        save_payload["isLanPrinter"] = True
        save_payload["lanCompatible"] = True
        self._save_persisted_identity(save_payload)
        
        self._record_payload_snapshot("/api/rest/print/cluster/addSingleTask", response_payload)
        self._send_json(response_payload)

    def serve_multi_machine(self):
        try:
            system_info = self._fetch_json("/machine/system_info", timeout=UPSTREAM_TIMEOUT)
        except Exception:
            system_info = {"result": {"system_info": {"network": {}}}}

        if isinstance(system_info, dict):
            network = system_info.get("result", {}).get("system_info", {}).get("network", {})
        else:
            network = {}

        ip_address = self._preferred_printer_identity_address(network) or self._find_first_ipv4(network) or self._guess_ip() or "127.0.0.1"
        hostname = socket.gethostname() or "printer"
        moonraker_info = self._fetch_moonraker_info(timeout=UPSTREAM_TIMEOUT)
        live_state = self._fetch_live_state(timeout=UPSTREAM_TIMEOUT)
        printer_status = self._fetch_printer_status(timeout=UPSTREAM_TIMEOUT)
        state_value, device_state_value = self._derive_ui_state(printer_status)
        identity_fields = self._resolve_identity_fields(moonraker_info)
        persisted_identity = self._load_persisted_identity()
        custom_name = self._first_present(
            persisted_identity.get("deviceName"),
            persisted_identity.get("aliasName"),
            persisted_identity.get("name"),
            persisted_identity.get("machine_name"),
        )
        machine_name = custom_name or identity_fields["machine_name"]
        printer_name = custom_name or identity_fields["name"]
        machine_type = identity_fields["machine_type"]
        stream_base_url = self._stream_base_url()
        info_payload = self._build_info_payload()
        device_boxs_info = info_payload.get("boxsInfo") or self._boxs_info_payload(moonraker_info)
        device_box_config = info_payload.get("boxConfig", {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0})
        payload = {
            "result": {
                "multi_printer_info": [
                    {
                        "ip": ip_address,
                        "machine_name": printer_name,
                        "machine_type": machine_type,
                        "model": machine_type,
                        "model_name": machine_type,
                        "modelName": machine_type,
                        "name": printer_name,
                        "mac": self._guess_mac() or "00:00:00:00:00:00",
                        "address": ip_address,
                        "connectType": 1001,
                        "deviceType": 0,
                        "type": 0,
                        "online": True,
                        "video": True,
                        "features": self._normalize_features(info_payload.get("features")),
                        "linuxVideoUrl": info_payload.get("linuxVideoUrl") or f"{stream_base_url}/api/v1/streams",
                        "webrtcSupport": info_payload.get("webrtcSupport") if info_payload.get("webrtcSupport") is not None else True,
                        "previewimg": info_payload.get("previewimg") or info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                        "deviceImg": info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                        "defaultDeviceImg": info_payload.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
                        "printerImagePath": info_payload.get("printerImagePath") or "",
                        "identity": None if info_payload.get("isLanPrinter") is True else (info_payload.get("identity") or self._public_address() or ip_address),
                        "moonraker_port": moonraker_info.get("moonraker_port") or self._extract_port_from_url(MOONRAKER_URL),
                        "printer_id": moonraker_info.get("printer_id", 1),
                        "fluidd_port": moonraker_info.get("fluidd_port", 80),
                        "mainsail_port": moonraker_info.get("mainsail_port", 80),
                        "status": moonraker_info.get("status", 1),
                        "printer_image_path": moonraker_info.get("printer_image_path", ""),
                        "isLanPrinter": True,
                        "lanCompatible": True,
                        "oldPrinter": False,
                        "socket": None,
                        "state": state_value,
                        "deviceState": device_state_value,
                        "uploadState": 0,
                        "localOnline": True,
                        "cloudOnline": False,
                        "cxyOnline": False,
                        "isExistInLocal": True,
                        "isExistInCxy": False,
                        "temperature": {
                            "nozzle": {"value": 0.0, "target": 0.0, "max": 300.0, "size": 0.4},
                            "bed": {"value": 0.0, "target": 0.0, "max": 120.0},
                        },
                        "printFileName": "",
                        "printProgress": 0,
                        "printLeftTime": 0,
                        "printJobTime": 0,
                        "printStartTime": 0,
                        "autohome": "X:0 Y:0 Z:0",
                        "curPosition": "X:1 Y:1 Z:1",
                        "curFeedratePct": 100,
                        "speedMode": 1,
                        "fan": 0,
                        "modelFanPct": 0,
                        "fanAuxiliary": 0,
                        "auxiliaryFanPct": 0,
                        "fanCase": 0,
                        "caseFan": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                        "caseFanPct": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                        "sideFan": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                        "sideFanPct": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                        "chamberTemp": live_state.get("chamber_temp") if live_state.get("chamber_temp") is not None else 0.0,
                        "chamberTempTarget": live_state.get("chamber_temp_target") if live_state.get("chamber_temp_target") is not None else 0.0,
                        "ledSw": int(live_state.get("led_state") or 0) if live_state.get("led_state") is not None else 0,
                        "lightSw": 0,
                        "ctrol": {
                            "autohome": "X:0 Y:0 Z:0",
                            "curPosition": "X:1 Y:1 Z:1",
                            "curFeedratePct": 100,
                            "speedMode": 1,
                            "fan": 0,
                            "modelFanPct": 0,
                            "fanAuxiliary": 0,
                            "auxiliaryFanPct": 0,
                            "fanCase": 0,
                            "caseFan": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                            "caseFanPct": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                            "sideFan": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                            "sideFanPct": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                            "chamberTemp": live_state.get("chamber_temp") if live_state.get("chamber_temp") is not None else 0.0,
                            "chamberTempTarget": live_state.get("chamber_temp_target") if live_state.get("chamber_temp_target") is not None else 0.0,
                            "ledSw": int(live_state.get("led_state") or 0) if live_state.get("led_state") is not None else 0,
                            "lightSw": 0,
                        },
                        "data": {
                            "bedTemp0": 0.0,
                            "nozzleTemp": 0.0,
                            "targetBedTemp0": 0.0,
                            "targetNozzleTemp": 0.0,
                        },
                        "deviceUI": "",
                        "hostType": "",
                        "moonrakerPort": moonraker_info.get("moonraker_port") or self._extract_port_from_url(MOONRAKER_URL),
                        "fluiddPort": moonraker_info.get("fluidd_port", 80),
                        "mainsailPort": moonraker_info.get("mainsail_port", 80),
                        "KlipperUrl": f"{stream_base_url}/api/v1/streams",
                        "boxsInfo": device_boxs_info,
                        "boxConfig": device_box_config,
                        "status": printer_status,
                        "device": {
                            "previewimg": info_payload.get("previewimg") or info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                            "deviceImg": info_payload.get("deviceImg") or info_payload.get("defaultDeviceImg") or "",
                            "defaultDeviceImg": info_payload.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
                            "printerImagePath": info_payload.get("printerImagePath") or "",
                            "boxsInfo": device_boxs_info,
                            "boxConfig": device_box_config,
                            "state": state_value,
                            "deviceState": device_state_value,
                        },
                    }
                ]
            }
        }
        self._send_json(payload)

    def serve_protocal_csp(self):
        payload = self._build_protocal_payload()
        self._record_payload_snapshot("/protocal.csp", payload)
        debug_log(f"[RESPONSE] /protocal.csp -> {json.dumps(payload, sort_keys=True)[:4000]}")
        self._send_json(payload)

    def _build_protocal_payload(self):
        info = self._build_info_payload()
        model_value = info.get("model") or DEFAULT_MODEL
        display_name = info.get("name") or info.get("machine_name") or model_value
        mac_value = info.get("mac") or "00:00:00:00:00:00"
        address_value = info.get("address") or self._preferred_printer_identity_address() or self._public_address() or self._guess_ip() or "127.0.0.1"
        status = info.get(
            "status",
            {
                "state": "standby",
                "display_status": {"progress": 0.0},
                "heater_bed": {"temperature": 0.0, "target": 0.0},
                "extruder": {"temperature": 0.0, "target": 0.0},
                "print_stats": {"state": "standby", "filename": "", "print_duration": 0},
                "gcode_move": {"speed_factor": 1.0},
            },
        )
        temperature = info.get(
            "temperature",
            {
                "nozzle": {"value": 0.0, "target": 0.0, "max": 300.0, "size": 0.4},
                "bed": {"value": 0.0, "target": 0.0, "max": 120.0},
            },
        )
        payload = {
            "model": model_value,
            "modelName": info.get("modelName", model_value),
            "machine_name": info.get("machine_name", display_name),
            "machine_type": info.get("machine_type", model_value),
            "name": info.get("name", display_name),
            "mac": mac_value,
            "address": address_value,
            "ssid": f"{model_value}-{mac_value}",
            "type": 0,
            "online": info.get("online", True),
            "connectType": info.get("connectType", 1001),
            "deviceType": info.get("deviceType", 0),
            "video": info.get("video", True),
            "features": self._normalize_features(info.get("features")),
            "linuxVideoUrl": info.get("linuxVideoUrl", f"http://{address_value}:8000/api/v1/streams"),
            "version": info.get("version", "1.0"),
            "isLanPrinter": True,
            "lanCompatible": True,
            "boxsInfo": info.get("boxsInfo") or self._boxs_info_payload(info),
            "boxConfig": info.get("boxConfig", {"cAutoFeed": 1, "cMode": 0}),
            "oldPrinter": False,
            "socket": None,
            "state": info.get("state", 0),
            "deviceState": info.get("deviceState", 0),
            "uploadState": info.get("uploadState", 0),
            "localOnline": True,
            "cloudOnline": False,
            "connect": 1,
            "connectType": 1001,
            "nozzleTemp": temperature.get("nozzle", {}).get("value", 0.0),
            "bedTemp": temperature.get("bed", {}).get("value", 0.0),
            "nozzleTemp2": temperature.get("nozzle", {}).get("target", 0.0),
            "bedTemp2": temperature.get("bed", {}).get("target", 0.0),
            "printProgress": status.get("display_status", {}).get("progress", 0.0),
            "autohome": "X:0 Y:0 Z:0",
            "curPosition": "X:1 Y:1 Z:1",
            "curFeedratePct": 100,
            "speedMode": 1,
            "fan": 0,
            "modelFanPct": 0,
            "fanAuxiliary": 0,
            "auxiliaryFanPct": 0,
            "fanCase": 0,
            "caseFanPct": 0,
            "lightSw": 0,
            "printStartTime": 0,
            "printLeftTime": 0,
            "printJobTime": 0,
            "temperature": temperature,
            "status": status,
        }
        return payload

    def serve_state_query(self):
        status = self._fetch_printer_status(timeout=UPSTREAM_TIMEOUT)
        payload_preview = json.dumps({"result": {"status": status}}, sort_keys=True)
        debug_log(f"[RESPONSE] /printer/objects/query -> {payload_preview[:4000]}")
        payload = {"result": {"status": status}}
        self._send_json(payload)

    def serve_print_start(self, body=None):
        if body is None:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length > 0 else b""

        if isinstance(body, (bytes, bytearray)):
            body_bytes = bytes(body)
            preview = body_bytes[:400]
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
            preview = body_bytes[:400]
        else:
            body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
            preview = body_bytes[:400]

        debug_log(f"[PRINT_START] body_preview={preview!r}")
        payload = {"result": {"print_started": True}}

        if body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None

            if isinstance(parsed, dict):
                filename = parsed.get("filename") or parsed.get("file") or parsed.get("path")
                if filename:
                    payload = {"result": "ok", "filename": filename}
                elif parsed.get("action") == "start":
                    payload = {"result": "ok"}

        self._send_json(payload)

    def serve_print_cancel(self, path):
        payload = {"result": "ok", "compat": True, "note": "cancel handled locally"}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_media_preview_data_uri(self, label, kind="file"):
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512' viewBox='0 0 512 512'>
          <rect width='512' height='512' rx='48' fill='#1f2937'/>
          <rect x='96' y='96' width='320' height='320' rx='28' fill='#374151'/>
          <path d='M176 160h160v32H176zM176 224h128v32H176zM176 288h96v32H176z' fill='#fbbf24'/>
          <text x='256' y='392' text-anchor='middle' fill='#f9fafb' font-family='Arial, sans-serif' font-size='36'>{label}</text>
          <text x='256' y='438' text-anchor='middle' fill='#d1d5db' font-family='Arial, sans-serif' font-size='24'>{kind}</text>
        </svg>"""
        encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"

    def _build_compat_file_entry(self, index, filename=None, file_type="gcode"):
        safe_name = filename or f"lan-compat-{file_type}-{index + 1}.{file_type if file_type not in {'gcode', 'mp4'} else file_type}"
        preview = self._build_media_preview_data_uri(safe_name.rsplit('.', 1)[-1].upper(), kind=file_type)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        download_path = urllib.parse.quote(safe_name)
        info_payload = self._build_info_payload()
        device_name = self._first_present(
            info_payload.get("identity"),
            info_payload.get("address"),
            self._preferred_printer_identity_address(),
            self._public_address(),
            "127.0.0.1",
        )
        return {
            "id": f"file-{index + 1}",
            "filename": safe_name,
            "name": safe_name,
            "fileName": safe_name,
            "path": f"/tmp/{safe_name}",
            "filePath": f"/tmp/{safe_name}",
            "type": file_type,
            "fileType": file_type,
            "suffix": file_type,
            "size": 12345 + index * 100,
            "createTime": timestamp,
            "modifiedTime": timestamp,
            "mtime": timestamp,
            "timestamp": timestamp,
            "deviceName": device_name,
            "downloadUrl": f"{self._stream_base_url()}/downloads/gcode/{download_path}",
            "previewimg": preview,
            "thumbUrl": preview,
            "thumbnail": preview,
            "gcodeThumbnail": preview,
            "image": preview,
            "icon": preview,
            "isDir": False,
            "isDirectory": False,
        }

    def _build_compat_record_entry(self, index, record_id=None, filename=None, record_type="video"):
        default_name = "lan-compat-record.mp4" if index == 0 else f"lan-compat-record-{index + 1}.mp4"
        safe_name = filename or default_name
        preview = self._build_media_preview_data_uri(safe_name.rsplit('.', 1)[-1].upper(), kind=record_type)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        info_payload = self._build_info_payload()
        device_name = self._first_present(
            info_payload.get("identity"),
            info_payload.get("address"),
            self._preferred_printer_identity_address(),
            self._public_address(),
            "127.0.0.1",
        )
        return {
            "id": record_id or f"record-{index + 1}",
            "filename": safe_name,
            "name": safe_name,
            "fileName": safe_name,
            "path": f"/tmp/{safe_name}",
            "recordId": record_id or f"record-{index + 1}",
            "createTime": timestamp,
            "modifiedTime": timestamp,
            "time": timestamp,
            "timestamp": timestamp,
            "state": "done",
            "video": True,
            "camera": True,
            "timelapse": False,
            "recording": False,
            "deviceName": device_name,
            "previewimg": preview,
            "thumbUrl": preview,
            "thumbnail": preview,
            "gcodeThumbnail": preview,
            "downloadUrl": f"{self._stream_base_url()}/downloads/original/{urllib.parse.quote(safe_name)}",
            "recordUrl": f"{self._stream_base_url()}/downloads/original/{urllib.parse.quote(safe_name)}",
            "coverUrl": preview,
            "image": preview,
            "icon": preview,
            "media": {
                "url": preview,
                "video": {"size": 12345 + index * 100, "duration": 0},
                "image": {"size": 12345 + index * 100, "duration": 0},
            },
        }

    def _read_detail_request_payload(self, payload=None):
        if payload is None:
            headers = getattr(self, "headers", None)
            content_length = headers.get("Content-Length", "0") if headers is not None else "0"
            payload = self._read_json_body(int(content_length or "0"))
        elif isinstance(payload, dict) and not payload:
            headers = getattr(self, "headers", None)
            content_length = headers.get("Content-Length", "0") if headers is not None else "0"
            payload = self._read_json_body(int(content_length or "0"))
        return payload if isinstance(payload, dict) else {}

    def _build_device_detail_response(self, info, detail, payload):
        result = detail.get("result", {}) if isinstance(detail, dict) else {}
        request_device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
        request_context = self._merge_dicts(request_device, payload)
        request_name = self._first_present(
            request_context.get("deviceName"),
            request_context.get("aliasName"),
            request_context.get("name"),
            request_device.get("deviceName"),
            request_device.get("aliasName"),
            request_device.get("name"),
        )
        request_alias = self._first_present(
            request_context.get("aliasName"),
            request_context.get("deviceName"),
            request_context.get("name"),
            request_device.get("aliasName"),
            request_device.get("deviceName"),
            request_device.get("name"),
        )
        request_model = self._first_present(
            request_context.get("modelName"),
            request_context.get("model"),
            request_device.get("modelName"),
            request_device.get("model"),
        )
        request_address = self._first_present(request_context.get("address"), request_context.get("dn"), request_device.get("address"), request_device.get("dn"), payload.get("address"), payload.get("dn"))
        request_identity = self._first_present(request_context.get("identity"), request_device.get("identity"), request_address)
        request_mac = self._first_present(request_context.get("mac"), request_device.get("mac"), payload.get("mac"))
        request_record = self._first_present(
            request_context.get("record") if isinstance(request_context.get("record"), dict) else None,
            request_device.get("record") if isinstance(request_device.get("record"), dict) else None,
            payload.get("record") if isinstance(payload.get("record"), dict) else None,
        )
        if not isinstance(request_record, dict):
            request_record = {}
        request_camera_state = self._first_present(
            request_context.get("cameraState"),
            request_record.get("cameraState"),
            payload.get("cameraState"),
            result.get("cameraState"),
            info.get("cameraState"),
            {"enabled": True, "state": "ready"},
        )
        request_record_state = self._first_present(
            request_context.get("recordState"),
            request_record.get("recordState"),
            payload.get("recordState"),
            result.get("recordState"),
            info.get("recordState"),
            {"recording": False, "timelapse": False},
        )
        request_stream_state = self._first_present(
            request_context.get("streamState"),
            request_record.get("streamState"),
            payload.get("streamState"),
            result.get("streamState"),
            info.get("streamState"),
            {"active": True, "source": "webcam"},
        )
        identity_fields = self._build_device_identity_fields(info, request_payload=request_context)
        model_value = self._first_present(request_model, identity_fields["model"]) or identity_fields["model"]
        model_name_value = self._first_present(request_model, identity_fields["model"]) or identity_fields["model"]
        display_name = identity_fields["display_name"]
        alias_name = request_alias or display_name
        device_identity = self._first_present(
            request_address,
            request_identity,
            result.get("address"),
            result.get("identity"),
            info.get("address"),
            info.get("identity"),
            self._public_address(),
            "127.0.0.1",
        )
        device_key = self._first_present(request_address, result.get("address"), info.get("address"), self._public_address(), "127.0.0.1")
        display_label = self._first_present(request_name, request_alias, display_name, device_identity)
        summary = {
            "deviceName": device_key if info.get("isLanPrinter") is True else self._first_present(request_name, display_name, device_identity),
            "aliasName": alias_name,
            "name": display_label,
            "model": model_value,
            "modelName": model_name_value,
            "model_name": model_name_value,
            "machine_name": identity_fields["machine_name"],
            "machine_type": identity_fields["machine_type"],
            "mac": self._first_present(request_mac, result.get("mac"), info.get("mac"), "00:00:00:00:00:00"),
            "address": self._first_present(request_address, result.get("address"), info.get("address"), self._public_address(), "127.0.0.1"),
            "identity": None if info.get("isLanPrinter") is True else self._first_present(request_identity, result.get("identity"), info.get("identity"), result.get("address"), info.get("address"), self._public_address(), "127.0.0.1"),
            "deviceType": 0 if info.get("isLanPrinter") is True else self._first_present(result.get("deviceType"), info.get("deviceType"), 0),
            "type": 0 if info.get("isLanPrinter") is True else self._first_present(result.get("type"), info.get("type"), 0),
            "connectType": 1001 if info.get("isLanPrinter") is True else self._first_present(result.get("connectType"), info.get("connectType"), 1001),
            "isLanPrinter": True if info.get("isLanPrinter") is True else self._first_present(result.get("isLanPrinter"), info.get("isLanPrinter"), True),
            "lanCompatible": True if info.get("isLanPrinter") is True else self._first_present(result.get("lanCompatible"), info.get("lanCompatible"), True),
            "oldPrinter": False if info.get("isLanPrinter") is True else self._first_present(result.get("oldPrinter"), info.get("oldPrinter"), False),
            "video": self._first_present(result.get("video"), info.get("video"), True),
            "tbId": self._first_present(result.get("tbId"), info.get("tbId"), payload.get("tbId"), "lan-compat-tb-id"),
            "keyFileToken": self._first_present(result.get("keyFileToken"), info.get("keyFileToken"), payload.get("keyFileToken"), "lan-compat-key-token"),
            "previewimg": self._first_present(result.get("previewimg"), info.get("previewimg"), ""),
            "deviceImg": self._first_present(result.get("deviceImg"), info.get("deviceImg"), ""),
            "defaultDeviceImg": self._first_present(result.get("defaultDeviceImg"), info.get("defaultDeviceImg"), "./img/printerImgDefault.svg"),
            "printerImagePath": self._first_present(result.get("printerImagePath"), info.get("printerImagePath"), ""),
            "features": self._first_present(
                result.get("features") if isinstance(result.get("features"), (list,)) and len(result.get("features")) > 0 else None,
                info.get("features"),
                ["videoInfo.videoEncryption", "videoInfo.video", "printControl.xyzControl001005010"],
            ),
            "linuxVideoUrl": self._first_present(result.get("linuxVideoUrl"), info.get("linuxVideoUrl"), ""),
            "webrtcSupport": self._first_present(result.get("webrtcSupport"), info.get("webrtcSupport"), True),
            "state": self._first_present(result.get("state"), info.get("state"), 0),
            "deviceState": self._first_present(result.get("deviceState"), info.get("deviceState"), 0),
            "uploadState": self._first_present(result.get("uploadState"), info.get("uploadState"), 0),
            "localOnline": True,
            "cloudOnline": False,
            "cxyOnline": False,
            "isExistInLocal": True,
            "isExistInCxy": False,
            "temperature": payload.get("temperature") if isinstance(payload.get("temperature"), dict) else (self._first_present(result.get("temperature"), info.get("temperature"), {})),
            "status": payload.get("status") if isinstance(payload.get("status"), dict) else (self._first_present(result.get("status"), info.get("status"), {})),
            "boxsInfo": payload.get("boxsInfo") if isinstance(payload.get("boxsInfo"), dict) else (self._first_present(result.get("boxsInfo"), info.get("boxsInfo"), {})),
            "boxConfig": payload.get("boxConfig") if isinstance(payload.get("boxConfig"), dict) else (self._first_present(result.get("boxConfig"), info.get("boxConfig"), {})),
            "printFileName": self._first_present(result.get("printFileName"), ""),
            "printProgress": self._first_present(result.get("printProgress"), 0),
            "printLeftTime": self._first_present(result.get("printLeftTime"), 0),
            "printJobTime": self._first_present(result.get("printJobTime"), 0),
            "printStartTime": self._first_present(result.get("printStartTime"), 0),
            "ctrol": payload.get("ctrol") if isinstance(payload.get("ctrol"), dict) else (self._first_present(result.get("ctrol"), info.get("ctrol"), {})),
            "data": payload.get("data") if isinstance(payload.get("data"), dict) else (self._first_present(result.get("data"), info.get("data"), {})),
            "videoToken": self._first_present(payload.get("videoToken"), result.get("videoToken"), info.get("videoToken"), "lan-compat-video-token"),
            "supportMultiple": self._first_present(result.get("supportMultiple"), info.get("supportMultiple"), True),
            "machinePlatformMotionEnable": self._first_present(result.get("machinePlatformMotionEnable"), info.get("machinePlatformMotionEnable"), False),
            "materialDetector1": self._first_present(result.get("materialDetector1"), info.get("materialDetector1"), False),
            "filamentsList": payload.get("filamentsList") if isinstance(payload.get("filamentsList"), list) else (self._first_present(result.get("filamentsList"), info.get("filamentsList"), [])),
            "streamState": request_stream_state if isinstance(request_stream_state, dict) else (self._first_present(result.get("streamState"), info.get("streamState"), {"active": True, "source": "webcam"})),
            "cameraState": request_camera_state if isinstance(request_camera_state, dict) else (self._first_present(result.get("cameraState"), info.get("cameraState"), {"enabled": True, "state": "ready"})),
            "recordState": request_record_state if isinstance(request_record_state, dict) else (self._first_present(result.get("recordState"), info.get("recordState"), {"recording": False, "timelapse": False})),
        }
        device_payload = self._first_present(result.get("device"), info.get("device"), {})
        if not isinstance(device_payload, dict):
            device_payload = {}
        record_payload = self._first_present(result.get("record"), info.get("record"), {})
        if info.get("isLanPrinter") is True:
            summary["identity"] = None
            summary["address"] = self._first_present(request_address, result.get("address"), info.get("address"), self._public_address(), "127.0.0.1")
            if isinstance(device_payload, dict):
                device_payload["identity"] = None
                device_payload["address"] = summary["address"]
        else:
            summary["identity"] = self._first_present(request_identity, result.get("identity"), info.get("identity"), result.get("address"), info.get("address"), self._public_address(), "127.0.0.1")
        summary["identity"] = None if info.get("isLanPrinter") is True else summary["identity"]
        if not isinstance(record_payload, dict):
            record_payload = {}
        preferred_display_name = self._first_present(
            request_name,
            payload.get("deviceName"),
            payload.get("aliasName"),
            payload.get("name"),
            device_payload.get("deviceName"),
            device_payload.get("name"),
            summary.get("deviceName"),
            info.get("deviceName"),
            info.get("aliasName"),
            info.get("name"),
        )
        preferred_model_name = self._first_present(
            request_model,
            payload.get("modelName"),
            payload.get("model"),
            device_payload.get("modelName"),
            device_payload.get("model"),
            device_payload.get("model_name"),
            summary.get("modelName"),
            summary.get("model"),
            info.get("modelName"),
            info.get("model"),
        )
        preferred_machine_name = self._first_present(
            payload.get("machine_name"),
            device_payload.get("machine_name"),
            summary.get("machine_name"),
            summary.get("deviceName"),
            info.get("machine_name"),
            info.get("name"),
            info.get("deviceName"),
        )
        preferred_machine_type = self._first_present(
            payload.get("machine_type"),
            request_model,
            payload.get("model"),
            payload.get("modelName"),
            device_payload.get("machine_type"),
            device_payload.get("model"),
            device_payload.get("modelName"),
            summary.get("model"),
            summary.get("modelName"),
            info.get("machine_type"),
            info.get("machine_name"),
            info.get("model"),
        )
        merged_device_record = self._merge_dicts(device_payload.get("record") or {}, request_record)
        summary["device"] = {
            **device_payload,
            "deviceName": device_key if info.get("isLanPrinter") is True else self._first_present(request_name, preferred_display_name, device_identity),
            "aliasName": preferred_display_name,
            "name": preferred_display_name,
            "machine_name": preferred_machine_name,
            "machine_type": preferred_machine_type,
            "model_name": preferred_model_name,
            "model": device_payload.get("model") or summary.get("model"),
            "modelName": device_payload.get("modelName") or summary.get("modelName"),
            "address": device_payload.get("address") or summary.get("address"),
            "identity": device_payload.get("identity") or summary.get("identity"),
            "deviceType": 0 if info.get("isLanPrinter") is True else (device_payload.get("deviceType") or summary.get("deviceType")),
            "type": 0 if info.get("isLanPrinter") is True else (device_payload.get("type") if device_payload.get("type") is not None else summary.get("type") or 0),
            "connectType": 1001 if info.get("isLanPrinter") is True else (device_payload.get("connectType") if device_payload.get("connectType") is not None else summary.get("connectType") or 1001),
            "isLanPrinter": True if info.get("isLanPrinter") is True else (device_payload.get("isLanPrinter") if device_payload.get("isLanPrinter") is not None else summary.get("isLanPrinter") is True),
            "lanCompatible": True if info.get("isLanPrinter") is True else (device_payload.get("lanCompatible") if device_payload.get("lanCompatible") is not None else summary.get("lanCompatible") is True),
            "oldPrinter": False if info.get("isLanPrinter") is True else (device_payload.get("oldPrinter") if device_payload.get("oldPrinter") is not None else summary.get("oldPrinter") is False),
            "video": device_payload.get("video") if device_payload.get("video") is not None else summary.get("video"),
            "tbId": device_payload.get("tbId") or summary.get("tbId") or "lan-compat-tb-id",
            "keyFileToken": device_payload.get("keyFileToken") or summary.get("keyFileToken") or "lan-compat-key-token",
            "videoToken": device_payload.get("videoToken") or summary.get("videoToken") or "lan-compat-video-token",
            "previewimg": device_payload.get("previewimg") or summary.get("previewimg"),
            "deviceImg": device_payload.get("deviceImg") or summary.get("deviceImg"),
            "defaultDeviceImg": device_payload.get("defaultDeviceImg") or summary.get("defaultDeviceImg"),
            "printerImagePath": device_payload.get("printerImagePath") or summary.get("printerImagePath"),
            "features": device_payload.get("features") or summary.get("features"),
            "linuxVideoUrl": device_payload.get("linuxVideoUrl") or summary.get("linuxVideoUrl"),
            "webrtcSupport": device_payload.get("webrtcSupport") if device_payload.get("webrtcSupport") is not None else summary.get("webrtcSupport"),
            "state": device_payload.get("state") if device_payload.get("state") is not None else summary.get("state"),
            "deviceState": device_payload.get("deviceState") if device_payload.get("deviceState") is not None else summary.get("deviceState"),
            "uploadState": device_payload.get("uploadState") if device_payload.get("uploadState") is not None else summary.get("uploadState"),
            "temperature": device_payload.get("temperature") or summary.get("temperature"),
            "status": device_payload.get("status") or summary.get("status"),
            "boxsInfo": device_payload.get("boxsInfo") or summary.get("boxsInfo"),
            "boxConfig": device_payload.get("boxConfig") or summary.get("boxConfig"),
            "printFileName": device_payload.get("printFileName") or summary.get("printFileName"),
            "printProgress": device_payload.get("printProgress") or summary.get("printProgress"),
            "printLeftTime": device_payload.get("printLeftTime") or summary.get("printLeftTime"),
            "printJobTime": device_payload.get("printJobTime") or summary.get("printJobTime"),
            "printStartTime": device_payload.get("printStartTime") or summary.get("printStartTime"),
            "ctrol": device_payload.get("ctrol") or summary.get("ctrol"),
            "data": device_payload.get("data") or summary.get("data"),
            "streamState": device_payload.get("streamState") or summary.get("streamState") or {"active": True, "source": "webcam"},
            "cameraState": device_payload.get("cameraState") or summary.get("cameraState") or {"enabled": True, "state": "ready"},
            "recordState": device_payload.get("recordState") or summary.get("recordState") or {"recording": False, "timelapse": False},
            "record": {
                **merged_device_record,
                "cameraState": merged_device_record.get("cameraState") or summary.get("cameraState") or {"enabled": True, "state": "ready"},
                "recordState": merged_device_record.get("recordState") or summary.get("recordState") or {"recording": False, "timelapse": False},
                "streamState": merged_device_record.get("streamState") or summary.get("streamState") or {"active": True, "source": "webcam"},
                "timelapse": merged_device_record.get("timelapse") if merged_device_record.get("timelapse") is not None else False,
                "state": merged_device_record.get("state") or "done",
                "video": merged_device_record.get("video") if merged_device_record.get("video") is not None else True,
                "camera": merged_device_record.get("camera") if merged_device_record.get("camera") is not None else True,
                "recording": merged_device_record.get("recording") if merged_device_record.get("recording") is not None else False,
            },
        }
        summary["record"] = {
            **record_payload,
            **request_record,
            "id": record_payload.get("id") or None,
            "timelapse": request_record.get("timelapse") if request_record.get("timelapse") is not None else (record_payload.get("timelapse") if record_payload.get("timelapse") is not None else False),
            "state": request_record.get("state") or record_payload.get("state") or "done",
            "created_at": record_payload.get("created_at") or None,
            "video": request_record.get("video") if request_record.get("video") is not None else (record_payload.get("video") if record_payload.get("video") is not None else True),
            "camera": request_record.get("camera") if request_record.get("camera") is not None else (record_payload.get("camera") if record_payload.get("camera") is not None else True),
            "recording": request_record.get("recording") if request_record.get("recording") is not None else (record_payload.get("recording") if record_payload.get("recording") is not None else False),
            "cameraState": request_record.get("cameraState") or record_payload.get("cameraState") or summary.get("cameraState"),
            "recordState": request_record.get("recordState") or record_payload.get("recordState") or summary.get("recordState"),
            "streamState": request_record.get("streamState") or record_payload.get("streamState") or summary.get("streamState"),
        }
        media_lists = self._build_media_hydration_lists(info)
        return {
            "code": 0,
            "message": "success",
            "result": {
                "printInfo": {
                    "deviceName": summary["deviceName"],
                    "modelName": summary["modelName"],
                    "model": summary["model"],
                    "gcodeName": summary.get("printFileName") or "",
                    "gcodeThumbnail": summary.get("previewimg") or "",
                    "printProgress": summary.get("printProgress") or 0,
                    "printLeftTime": summary.get("printLeftTime") or 0,
                    "printJobTime": summary.get("printJobTime") or 0,
                    "printStartTime": summary.get("printStartTime") or 0,
                    "modelVersion": summary.get("modelVersion") or "1.0",
                    "state": summary.get("state") or 0,
                },
                "deviceName": summary["deviceName"],
                "aliasName": summary["aliasName"],
                "name": summary.get("name") or summary.get("aliasName") or summary.get("deviceName"),
                "type": summary.get("type") or 0,
                "type": summary.get("type") or 0,
                "model": summary["model"],
                "modelName": summary["modelName"],
                "model_name": summary.get("model_name") or summary.get("modelName") or summary.get("model"),
                "machine_name": summary.get("machine_name") or summary.get("model") or summary.get("modelName") or summary.get("deviceName"),
                "machine_type": summary.get("machine_type") or summary.get("model") or summary.get("modelName"),
                "mac": summary["mac"],
                "address": summary["address"],
                "identity": summary["identity"],
                "deviceType": summary["deviceType"],
                "connectType": summary.get("connectType") or 1001,
                "isLanPrinter": summary.get("isLanPrinter") is True,
                "lanCompatible": summary.get("lanCompatible") is True,
                "oldPrinter": summary.get("oldPrinter") is True,
                "video": 1 if summary.get("video") is True or summary.get("video") in (1, "1") else 0,
                "tbId": summary.get("tbId") or "lan-compat-tb-id",
                "keyFileToken": summary.get("keyFileToken") or "lan-compat-key-token",
                "previewimg": summary["previewimg"],
                "deviceImg": summary["deviceImg"],
                "defaultDeviceImg": summary["defaultDeviceImg"],
                "printerImagePath": summary["printerImagePath"],
                "features": summary["features"],
                "linuxVideoUrl": summary["linuxVideoUrl"],
                "webrtcSupport": summary["webrtcSupport"],
                "state": summary["state"],
                "deviceState": summary["deviceState"],
                "idleState": 0,
                "uploadState": summary["uploadState"],
                "temperature": summary["temperature"],
                "status": summary["status"],
                "boxsInfo": summary["boxsInfo"],
                "boxConfig": summary["boxConfig"],
                "printFileName": summary["printFileName"],
                "printProgress": summary["printProgress"],
                "printLeftTime": summary["printLeftTime"],
                "printJobTime": summary["printJobTime"],
                "printStartTime": summary["printStartTime"],
                "ctrol": summary["ctrol"],
                "data": summary["data"],
                "videoToken": summary.get("videoToken"),
                "supportMultiple": summary.get("supportMultiple"),
                "machinePlatformMotionEnable": summary.get("machinePlatformMotionEnable"),
                "materialDetector1": summary.get("materialDetector1"),
                "filamentsList": summary.get("filamentsList"),
                "streamState": summary.get("streamState"),
                "cameraState": summary.get("cameraState"),
                "recordState": summary.get("recordState"),
                "historyList": media_lists["historyList"],
                "pFileList": media_lists["pFileList"],
                "recordList": media_lists["recordList"],
                "fileList": media_lists["fileList"],
                "device": {
                    **(summary.get("device") or {"name": summary.get("deviceName")}),
                    "type": 0 if info.get("isLanPrinter") is True else ((summary.get("device") or {}).get("type") if isinstance(summary.get("device"), dict) else 0),
                    "deviceType": 0 if info.get("isLanPrinter") is True else ((summary.get("device") or {}).get("deviceType") if isinstance(summary.get("device"), dict) else summary.get("deviceType")),
                },
                "record": summary.get("record") or {"id": None, "cameraState": summary.get("cameraState"), "recordState": summary.get("recordState"), "timelapse": False, "state": "done", "video": True, "camera": True},
            },
        }

    def serve_print_cluster_device_detail(self, payload=None):
        payload = self._read_detail_request_payload(payload)
        persisted_identity_before = self._load_persisted_identity()
        if isinstance(payload, dict):
            # Merge client data into existing state (don't overwrite good values).
            # Start from what we already have, then apply client-provided fields.
            persisted_payload = dict(persisted_identity_before)
            persisted_payload.setdefault("isLanPrinter", True)
            persisted_payload.setdefault("lanCompatible", True)
            persisted_payload.setdefault("oldPrinter", False)

            # Fold name fields only if the client actually sent them (non-empty).
            for key in ("deviceName", "aliasName", "name"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    persisted_payload[key] = val

            # Fold model fields only if the client actually sent them.
            if payload.get("modelName"):
                persisted_payload["modelName"] = payload["modelName"]
            elif payload.get("model"):
                persisted_payload["model"] = payload["model"]

            # Fold address/identity when present.
            addr_val = self._first_present(payload.get("address"), payload.get("dn"))
            if addr_val:
                persisted_payload["address"] = addr_val
                persisted_payload["identity"] = None

            # Merge request-level state fields so live runtime data persists across poll cycles.
            for state_key in ("cameraState", "recordState", "streamState"):
                client_val = payload.get(state_key)
                if isinstance(client_val, dict):
                    persisted_payload[state_key] = dict(client_val)

            # Merge record state (nested inside a top-level "record" dict).
            rec = payload.get("record")
            if isinstance(rec, dict):
                for rec_sub in ("timelapse", "video", "camera", "recording", "state"):
                    val = rec.get(rec_sub)
                    if val is not None:
                        persisted_payload.setdefault("_record", {})
                        persisted_payload["_record"][rec_sub] = val

            self._save_persisted_identity(persisted_payload)
        info = self._build_info_payload()
        detail = self._build_detail_payload()
        response_payload = self._build_device_detail_response(info, detail, payload)
        self._record_state_transition("/api/rest/print/cluster/devices/getDeviceDetail", response_payload.get("result", {}), persisted_identity=persisted_identity_before)
        self._record_payload_snapshot("/api/rest/print/cluster/devices/getDeviceDetail", response_payload)
        self._send_json(response_payload)
        return response_payload

    def _read_json_request_payload(self):
        headers = getattr(self, "headers", None)
        content_length = headers.get("Content-Length", "0") if headers is not None else "0"
        payload = self._read_json_body(int(content_length or "0"))
        return payload if isinstance(payload, dict) else {}

    def _build_record_detail_response(self, payload):
        record_id = payload.get("id") if isinstance(payload, dict) else None
        return {
            "code": 0,
            "message": "success",
            "result": {
                "id": record_id,
                "record": {
                    "id": record_id,
                    "timelapse": False,
                    "state": "done",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "video": True,
                    "camera": True,
                    "recording": False,
                    "streamState": {"active": True, "source": "webcam"},
                    "cameraState": {"enabled": True, "state": "ready"},
                    "recordState": {"recording": False, "timelapse": False},
                },
            },
        }

    def _build_record_list_response(self, payload):
        page = payload.get("page") or 1
        page_size = payload.get("pageSize") or 10
        history_list = [self._build_compat_record_entry(index, record_id=f"record-{page}-{index + 1}") for index in range(min(int(page_size), 3))]
        return {
            "code": 0,
            "message": "success",
            "data": {
                "historyList": history_list,
                "list": history_list,
                "page": page,
                "pageSize": page_size,
                "total": len(history_list),
            },
            "result": {
                "historyList": history_list,
                "list": history_list,
                "page": page,
                "pageSize": page_size,
                "total": len(history_list),
            },
        }

    def _build_upload_videos_response(self, payload):
        limit = payload.get("limit") or payload.get("pageSize") or 10
        videos = [self._build_compat_record_entry(index, record_id=f"upload-video-{index + 1}", filename=f"lan-compat-video-{index + 1}.mp4", record_type="mp4") for index in range(min(int(limit), 3))]
        return {
            "code": 0,
            "message": "success",
            "data": {"list": videos, "cursor": "", "limit": limit, "count": len(videos)},
            "result": {"videos": videos, "list": videos, "cursor": "", "limit": limit, "count": len(videos)},
        }

    def serve_print_record_detail(self, path):
        payload = self._read_json_request_payload()
        self._send_json(self._build_record_detail_response(payload))

    def serve_print_record_list(self, path):
        payload = self._read_json_request_payload()
        response_payload = self._build_record_list_response(payload)
        self._record_payload_snapshot("/api/cxy/v3/print/record/list", response_payload)
        self._send_json(response_payload)

    def serve_device_upload_videos(self, path):
        payload = self._read_json_request_payload()
        response_payload = self._build_upload_videos_response(payload)
        self._record_payload_snapshot("/api/cxy/v2/device/uploadVideos", response_payload)
        self._send_json(response_payload)

    def serve_creality_device_status(self):
        payload = self._build_creality_device_status_payload()
        debug_log(f"[RESPONSE] /api/v1/device/status -> {json.dumps(payload, sort_keys=True)[:4000]}")
        self._send_json(payload)

    def serve_creality_cxy_status(self):
        payload = self._build_creality_cxy_status_payload()
        debug_log(f"[RESPONSE] /cxy/v1/status -> {json.dumps(payload, sort_keys=True)[:4000]}")
        self._send_json(payload)

    def _build_detail_payload(self):
        info = self._build_info_payload()
        state_value = info.get("state", 0)
        device_state_value = info.get("deviceState", 0)
        upload_state_value = info.get("uploadState", 0)
        temperature = info.get("temperature", {
            "nozzle": {"value": 0.0, "target": 0.0, "max": 300.0, "size": 0.4},
            "bed": {"value": 0.0, "target": 0.0, "max": 120.0},
        })
        data_payload = {
            "bedTemp0": temperature.get("bed", {}).get("value", 0.0),
            "nozzleTemp": temperature.get("nozzle", {}).get("value", 0.0),
            "targetBedTemp0": temperature.get("bed", {}).get("target", 0.0),
            "targetNozzleTemp": temperature.get("nozzle", {}).get("target", 0.0),
        }
        status = info.get(
            "status",
            {
                "state": "standby",
                "display_status": {"progress": 0.0},
                "heater_bed": {"temperature": 0.0, "target": 0.0},
                "extruder": {"temperature": 0.0, "target": 0.0},
                "print_stats": {"state": "standby", "filename": "", "print_duration": 0},
                "gcode_move": {"speed_factor": 1.0},
            },
        )
        model_value = info.get("model") or info.get("modelName") or info.get("machine_type") or info.get("machine_name") or info.get("name") or DEFAULT_MODEL
        model_name_value = info.get("modelName") or info.get("model") or info.get("machine_type") or info.get("machine_name") or info.get("name") or DEFAULT_MODEL
        machine_name_value = info.get("machine_name") or info.get("name") or model_name_value
        machine_type_value = info.get("machine_type") or model_value
        display_name_value = info.get("name") or info.get("machine_name") or info.get("deviceName") or info.get("aliasName") or model_name_value
        is_lan_printer = info.get("isLanPrinter") is True
        live_state = self._fetch_live_state(timeout=UPSTREAM_TIMEOUT)
        # Extract shared sensor/control data once (used in device, ctrol, and result.update copies)
        _cs = lambda key: live_state.get(key) if live_state.get(key) is not None else 0
        sensor_block = {
            "caseFan": _cs("case_fan_speed"), "caseFanPct": _cs("case_fan_speed"),
            "sideFan": _cs("side_fan_speed"), "sideFanPct": _cs("side_fan_speed"),
            "chamberTemp": _cs("chamber_temp") or 0.0, "chamberTempTarget": _cs("chamber_temp_target") or 0.0,
            "ledSw": int(live_state.get("led_state") or 0),
        }
        ctrol_block = {**sensor_block, **{
            "autohome": "X:0 Y:0 Z:0", "curPosition": "X:1 Y:1 Z:1",
            "curFeedratePct": 100, "speedMode": 1,
            "fan": 0, "modelFanPct": 0, "fanAuxiliary": 0, "auxiliaryFanPct": 0, "fanCase": 0, "lightSw": 0,
        }}
        device = {
            "online": 1,
            "status": "idle",
            "model": model_value,
            "modelName": model_name_value,
            "model_name": info.get("model_name") or model_name_value,
            "machine_name": machine_name_value,
            "machine_type": machine_type_value,
            "name": display_name_value,
            "address": info.get("address"),
            "mac": info.get("mac"),
            "identity": None if is_lan_printer else (info.get("identity") or info.get("address")),
            "deviceType": 0 if is_lan_printer else info.get("deviceType"),
            "type": 0 if is_lan_printer else info.get("type"),
            "video": info.get("video"),
            "previewimg": info.get("previewimg") or info.get("deviceImg") or info.get("defaultDeviceImg") or "",
            "deviceImg": info.get("deviceImg") or info.get("defaultDeviceImg") or "",
            "defaultDeviceImg": info.get("defaultDeviceImg") or "./img/printerImgDefault.svg",
            "printerImagePath": info.get("printerImagePath") or "",
            "features": self._normalize_features(info.get("features")),
            "linuxVideoUrl": info.get("linuxVideoUrl"),
            "webrtcSupport": info.get("webrtcSupport"),
            "connectType": 1001 if is_lan_printer else info.get("connectType"),
            "isLanPrinter": True,
            "lanCompatible": True,
            "oldPrinter": False,
            "state": state_value,
            "deviceState": device_state_value,
            "uploadState": upload_state_value,
            "localOnline": True,
            "cloudOnline": False,
            "cxyOnline": False,
            "isExistInLocal": True,
            "isExistInCxy": False,
            "temperature": temperature,
            "printFileName": "",
            "printProgress": 0,
            "printLeftTime": 0,
            "printJobTime": 0,
            "printStartTime": 0,
            "fan": 0,
            "modelFanPct": 0,
            "fanAuxiliary": 0,
            "fanCase": 0,
            **sensor_block,
            "lightSw": 0,
            "ctrol": ctrol_block,
            "data": data_payload,
            "data": data_payload,
            "deviceUI": "",
            "hostType": "",
            "moonrakerPort": info.get("moonrakerPort") or self._extract_port_from_url(MOONRAKER_URL),
            "fluiddPort": info.get("fluiddPort") or 80,
            "mainsailPort": info.get("mainsailPort") or 80,
            "KlipperUrl": info.get("linuxVideoUrl") or f"http://{info.get('address')}",
            "boxsInfo": info.get("boxsInfo") or self._boxs_info_payload(info),
            "boxConfig": info.get("boxConfig", {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0}),
        }
        # Build the top-level result that mirrors device fields (contract requires both levels)
        result = {
            **device,
            "status": status,
            "temperature": temperature,
            "state": state_value,
            "deviceState": device_state_value,
            "uploadState": upload_state_value,
            "autohome": "X:0 Y:0 Z:0",
            "curPosition": "X:1 Y:1 Z:1",
            "curFeedratePct": 100,
            "speedMode": 1,
            "printFileName": "",
            "printProgress": 0,
            "printLeftTime": 0,
            "printJobTime": 0,
            "printStartTime": 0,
        }
        # Add ctrol/sensor blocks at top level (contract expects them there too)
        result["ctrol"] = dict(ctrol_block)
        result["data"] = dict(data_payload)
        result.update({
            "deviceUI": "",
            "hostType": "",
            "moonrakerPort": info.get("moonrakerPort") or self._extract_port_from_url(MOONRAKER_URL),
            "fluiddPort": info.get("fluiddPort") or 80,
            "mainsailPort": info.get("mainsailPort") or 80,
            "KlipperUrl": info.get("linuxVideoUrl") or f"http://{info.get('address')}",
            "previewimg": device["previewimg"],
            "deviceImg": device["deviceImg"],
            "defaultDeviceImg": device["defaultDeviceImg"],
            "printerImagePath": device["printerImagePath"],
        })
        # Nested "device" dict for contract (shallow copy with same shared blocks)
        result["device"] = {**device}
        return {
            "code": 0,
            "message": "success",
            "result": result,
        }

    def serve_upload_compat(self, path, body=None):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        content_type = self.headers.get("Content-Type", "")
        if body is None:
            body = self.rfile.read(content_length) if content_length > 0 else b""
        elif not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        debug_log(f"[UPLOAD] {path} content_length={content_length} content_type={content_type} body_preview={body[:400]!r}")

        upload_name_hint = ""
        if path.startswith("/upload/"):
            upload_name_hint = urllib.parse.unquote(path.rsplit("/", 1)[-1])

        file_name, file_bytes = self._extract_upload_from_multipart(body, content_type)
        if file_name is None or file_bytes is None:
            self._send_json(
                {
                    "error": {
                        "code": 400,
                        "message": "No upload file found in multipart body",
                    }
                }
            )
            return

        if upload_name_hint:
            file_name = upload_name_hint

        upstream_body, upstream_status = self._forward_upload_to_moonraker(file_name, file_bytes)
        if path.startswith("/upload/") and 200 <= upstream_status < 300:
            upstream_body = json.dumps({"code": 200, "message": "OK", "result": "upload_compat"}).encode("utf-8")
        self.send_response(upstream_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(upstream_body)))
        self.end_headers()
        self.wfile.write(upstream_body)

    def _collect_status_checks(self):
        checks = []
        checks.append(self._probe_http("Fluidd", "http://127.0.0.1/", lambda body: "MonacoEnvironment" in body or "fluidd" in body.lower(), "Fluidd UI reachable"))
        checks.append(self._probe_json("Moonraker", f"{MOONRAKER_URL}/server/info", lambda payload: bool(payload.get("result", {}).get("klippy_connected")), "Moonraker API reachable"))
        checks.append({"name": "Compatibility backend", "ok": True, "detail": "local route"})
        checks.append(self._probe_json("Printer", f"{MOONRAKER_URL}/printer/info", lambda payload: bool(payload.get("result", {}).get("state")), "Printer state available"))
        checks.append({"name": "Creality device status", "ok": True, "detail": "local route"})
        return checks

    def _collect_contract_trace_lines(self):
        trace_lines = []
        enabled = (os.environ.get("LAN_BRIDGE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"})
        if not enabled:
            return trace_lines

        try:
            info_payload = self._build_info_payload()
            address_value = info_payload.get("address") or self._public_address() or "unknown"
            identity_value = info_payload.get("identity") or address_value
            trace_lines.append(self._format_ecs_trace("/info", {
                "event": {"action": "compat_info"},
                "service": {"name": "creality_probe_backend"},
                "host": {"ip": address_value},
                "creality": {
                    "device": {
                        "identity": identity_value,
                        "name": info_payload.get("name") or info_payload.get("machine_name") or "unknown",
                        "model": info_payload.get("model") or info_payload.get("modelName") or "unknown",
                    },
                    "camera": {"enabled": bool(info_payload.get("video", False))},
                    "record": {"recording": False},
                },
                "labels": {"contract": "info"},
            }))
        except Exception as exc:
            trace_lines.append(self._format_ecs_trace("/info", {
                "event": {"action": "compat_info_error"},
                "error": {"message": f"{exc.__class__.__name__}:{exc}"},
            }))

        try:
            detail_payload = self._build_detail_payload()
            detail_result = detail_payload.get("result", {}) if isinstance(detail_payload, dict) else {}
            detail_device = detail_result.get("device") or {}
            detail_record_state = detail_result.get("recordState") or {}
            detail_camera_state = detail_result.get("cameraState") or {}
            trace_lines.append(self._format_ecs_trace("/protocal.csp", {
                "event": {"action": "compat_detail"},
                "service": {"name": "creality_probe_backend"},
                "creality": {
                    "device": {
                        "name": detail_device.get("name") or detail_result.get("name") or "unknown",
                        "identity": detail_device.get("identity") or detail_result.get("identity") or "unknown",
                        "model": detail_device.get("model") or detail_result.get("model") or "unknown",
                    },
                    "camera": {"state": detail_camera_state.get("state") or "unknown", "enabled": bool(detail_camera_state.get("enabled", False))},
                    "record": {
                        "recording": bool(detail_record_state.get("recording", False)),
                        "timelapse": bool(detail_record_state.get("timelapse", False)),
                    },
                    "stream": {"active": bool((detail_result.get("streamState") or {}).get("active", False))},
                },
                "labels": {"contract": "detail"},
            }))
        except Exception as exc:
            trace_lines.append(self._format_ecs_trace("/protocal.csp", {
                "event": {"action": "compat_detail_error"},
                "error": {"message": f"{exc.__class__.__name__}:{exc}"},
            }))

        try:
            record_response = self._build_record_list_response({"page": 1, "pageSize": 2})
            record_history = record_response.get("result", {}).get("historyList") or []
            trace_lines.append(self._format_ecs_trace("/api/cxy/v3/print/record/list", {
                "event": {"action": "compat_record_list"},
                "service": {"name": "creality_probe_backend"},
                "creality": {
                    "media": {
                        "count": len(record_history),
                        "first_device_name": (record_history[0] or {}).get("deviceName") if record_history else None,
                    },
                },
                "labels": {"contract": "record_list"},
            }))
        except Exception as exc:
            trace_lines.append(self._format_ecs_trace("/api/cxy/v3/print/record/list", {
                "event": {"action": "compat_record_list_error"},
                "error": {"message": f"{exc.__class__.__name__}:{exc}"},
            }))

        try:
            rpc_response = self._build_iotrouter_rpc_file_list_response({"pFileList": 1, "onePageNum": 2})
            file_list = rpc_response.get("result", {}).get("pFileList") or []
            trace_lines.append(self._format_ecs_trace("/api/rest/iotrouter/rpc/twoway", {
                "event": {"action": "compat_file_list"},
                "service": {"name": "creality_probe_backend"},
                "creality": {
                    "media": {
                        "count": len(file_list),
                        "first_device_name": (file_list[0] or {}).get("deviceName") if file_list else None,
                    },
                },
                "labels": {"contract": "rpc_file_list"},
            }))
        except Exception as exc:
            trace_lines.append(self._format_ecs_trace("/api/rest/iotrouter/rpc/twoway", {
                "event": {"action": "compat_file_list_error"},
                "error": {"message": f"{exc.__class__.__name__}:{exc}"},
            }))

        return trace_lines

    def _format_ecs_trace(self, route, payload):
        event_action = (payload.get("event") or {}).get("action") or "compat_trace"
        service_name = (payload.get("service") or {}).get("name") or "creality_probe_backend"
        message = json.dumps({
            "@timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": {"action": event_action},
            "service": {"name": service_name},
            "url": {"original": route},
            **payload,
        }, sort_keys=True)
        return f"[TRACE] {message}"

    def _build_iotrouter_rpc_file_list_response(self, params):
        page_number = params.get("pFileList") or 1
        one_page_num = params.get("onePageNum") or 10
        file_list = [self._build_compat_file_entry(index, filename=f"lan-compat.gcode" if index == 0 else f"lan-compat-{index + 1}.gcode") for index in range(min(int(one_page_num), 3))]
        return {
            "code": 0,
            "message": "success",
            "result": {
                "pFileList": file_list,
                "onePageNum": int(one_page_num),
                "fileList": file_list,
                "gcodeList": file_list,
                "files": file_list,
                "fileListCount": len(file_list),
                "total": len(file_list),
                "page": int(page_number),
                "pageSize": int(one_page_num),
                "count": len(file_list),
            },
        }

    def _probe_http(self, name, url, validator, fallback_detail):
        try:
            with urllib.request.urlopen(url, timeout=2.5) as response:
                body = response.read().decode("utf-8", "ignore")
                ok = validator(body)
                detail = f"HTTP {response.status}" if ok else fallback_detail
                return {"name": name, "ok": ok, "detail": detail}
        except Exception as exc:
            return {"name": name, "ok": False, "detail": f"{exc.__class__.__name__}: {exc}"}

    def _probe_json(self, name, url, validator, fallback_detail):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                ok = validator(payload)
                detail = f"HTTP {response.status}" if ok else fallback_detail
                return {"name": name, "ok": ok, "detail": detail}
        except Exception as exc:
            return {"name": name, "ok": False, "detail": f"{exc.__class__.__name__}: {exc}"}

    def _probe_local_json(self, name, payload, validator, fallback_detail):
        try:
            ok = validator(payload)
            detail = "local payload" if ok else fallback_detail
            return {"name": name, "ok": ok, "detail": detail}
        except Exception as exc:
            return {"name": name, "ok": False, "detail": f"{exc.__class__.__name__}: {exc}"}

    def _runtime_port_defaults(self, moonraker_info=None):
        info = moonraker_info or {}
        moonraker_port = info.get("moonraker_port") or self._extract_port_from_url(MOONRAKER_URL)
        return {
            "moonraker_port": moonraker_port,
            "fluidd_port": info.get("fluidd_port") or 80,
            "mainsail_port": info.get("mainsail_port") or 80,
        }

    def _boxs_info_payload(self, moonraker_info=None):
        info = moonraker_info or {}
        cfs_name = (info.get("cfsName") or os.environ.get("LAN_CFS_NAME", "").strip() or info.get("boxs_name") or "").strip()
        material_name = (info.get("material_name") or os.environ.get("LAN_MATERIAL_NAME", "").strip() or "").strip()
        material_color = (info.get("material_color") or os.environ.get("LAN_MATERIAL_COLOR", "").strip() or "").strip()
        if not cfs_name:
            cfs_name = DEFAULT_CFS_NAME
        if not material_name:
            material_name = DEFAULT_MATERIAL_NAME
        if not material_color:
            material_color = DEFAULT_MATERIAL_COLOR
        box_color_info_entry = {
            "boxType": 0,
            "color": material_color,
            "material": material_name,
            "materialName": material_name,
            "filamentName": material_name,
            "boxId": 1,
            "materialId": 1,
            "filamentType": 0,
            "id": 1,
            "name": cfs_name,
            "RFIDState": 1,
            "percent": 100,
            "remaining_length": 1000000,
        }
        same_material_entry = [
            None,
            None,
            [{"boxId": 1, "materialId": 1}],
            [{"boxId": 1, "materialId": 1}],
        ]
        return {
            "same_material": [same_material_entry],
            "color_same_material": [[None, [{"boxId": 1, "materialId": 1}]]],
            "boxColorInfo": [box_color_info_entry],
            "materialBoxs": [{
                "id": 1,
                "name": cfs_name,
                "type": 0,
                "materials": [{
                    "id": 1,
                    "name": material_name,
                    "type": 0,
                    "color": material_color,
                    "state": 1,
                    "percent": 100,
                    "remaining_length": 1000000,
                }],
            }],
            "cfsName": cfs_name,
        }

    def _build_preview_image_data_url(self, label=None, subtitle=None):
        label = (label or "LAN Printer").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        subtitle = (subtitle or "Print preview").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
          <rect width="320" height="180" fill="#0f172a"/>
          <rect x="16" y="16" width="288" height="148" rx="18" fill="#111827" stroke="#38bdf8" stroke-width="2"/>
          <rect x="36" y="46" width="104" height="72" rx="12" fill="#1f2937"/>
          <path d="M42 100 C66 72 98 60 124 92" stroke="#38bdf8" stroke-width="7" fill="none" stroke-linecap="round"/>
          <path d="M124 92 L154 70" stroke="#f59e0b" stroke-width="7" fill="none" stroke-linecap="round"/>
          <circle cx="255" cy="72" r="28" fill="#22c55e" opacity="0.95"/>
          <text x="44" y="146" fill="#e2e8f0" font-family="Arial, sans-serif" font-size="18">{label}</text>
          <text x="44" y="166" fill="#94a3b8" font-family="Arial, sans-serif" font-size="12">{subtitle}</text>
        </svg>'''
        return f"data:image/svg+xml;base64,{base64.b64encode(svg.encode('utf-8')).decode('ascii')}"

    def _build_info_payload(self):
        try:
            system_info = self._fetch_json("/machine/system_info", timeout=UPSTREAM_TIMEOUT)
        except Exception:
            system_info = {"result": {"system_info": {"network": {}}}}

        network = system_info.get("result", {}).get("system_info", {}).get("network", {}) if isinstance(system_info, dict) else {}
        mac = self._find_first_mac(network)
        moonraker_info = self._fetch_moonraker_info(timeout=UPSTREAM_TIMEOUT)
        live_state = self._fetch_live_state(timeout=UPSTREAM_TIMEOUT)
        printer_status = self._fetch_printer_status(timeout=UPSTREAM_TIMEOUT)
        state_value, device_state_value = self._derive_ui_state(printer_status)
        identity_fields = self._resolve_identity_fields(moonraker_info)
        persisted_identity = self._load_persisted_identity()

        # Fold persisted identity into the resolution chain so client-provided
        # values survive across round-trips.  Resolution order:
        #   Moonraker → persistence → defaults
        moonraker_model = identity_fields["model"]
        model_value = self._first_present(
            moonraker_model,
            persisted_identity.get("modelName"),
            persisted_identity.get("model"),
        )

        custom_name = self._first_present(
            persisted_identity.get("deviceName"),
            persisted_identity.get("aliasName"),
            persisted_identity.get("name"),
        )
        # Also fold persisted alias into the name chain so _first_present sees it
        aliased_name = self._first_present(
            persisted_identity.get("aliasName"),
            persisted_identity.get("name"),
        )
        machine_name = self._first_present(
            identity_fields["machine_name"],
            aliased_name,
            model_value,
        )
        printer_name = self._first_present(
            custom_name,
            aliased_name,
            identity_fields["name"],
        )
        display_label = self._first_present(
            custom_name,
            printer_name,
            model_value,
        )
        public_address_value = self._public_address()
        if isinstance(public_address_value, str) and self._looks_like_ip(public_address_value):
            address_value = public_address_value
        else:
            address_value = self._preferred_printer_identity_address(network)
        printer_image_path = moonraker_info.get("printer_image_path") or ""
        runtime_ports = self._runtime_port_defaults(moonraker_info)
        default_device_img = "./img/printerImgDefault.svg"
        device_img = f"./img/machine/{model_value}.png" if model_value else default_device_img
        boxs_info = self._boxs_info_payload(moonraker_info)
        temperature = {
            "nozzle": {
                "value": printer_status.get("extruder", {}).get("temperature", 0.0),
                "target": printer_status.get("extruder", {}).get("target", 0.0),
                "max": 300.0,
                "size": 0.4,
            },
            "bed": {
                "value": printer_status.get("heater_bed", {}).get("temperature", 0.0),
                "target": printer_status.get("heater_bed", {}).get("target", 0.0),
                "max": 120.0,
            },
        }
        status_payload = printer_status if isinstance(printer_status, dict) else {}
        status_payload = dict(status_payload)
        status_state = (
            status_payload.get("state")
            or (status_payload.get("print_stats") or {}).get("state")
            or (status_payload.get("display_status") or {}).get("state")
            or "standby"
        )
        status_payload.setdefault("state", status_state)
        if not isinstance(status_payload.get("display_status"), dict):
            status_payload["display_status"] = {"progress": 0.0}
        if not isinstance(status_payload.get("print_stats"), dict):
            status_payload["print_stats"] = {"state": status_state, "filename": "", "print_duration": 0.0, "total_duration": 0.0, "filament_used": 0.0, "message": ""}
        payload = {
            "mac": mac or self._guess_mac() or "00:00:00:00:00:00",
            "model": model_value,
            "modelName": model_value,
            "model_name": model_value,
            "machine_name": machine_name,
            "machine_type": identity_fields["machine_type"],
            "name": display_label,
            "deviceName": address_value,
            "aliasName": display_label,
            "type": 0,
            "online": True,
            "address": address_value,
            "connectType": 1001,
            "deviceType": 0,
            "video": True,
            "identity": address_value,
            "deviceImg": device_img,
            "defaultDeviceImg": default_device_img,
            "previewimg": self._build_preview_image_data_url(label=printer_name or model_value or "LAN Printer", subtitle="Print preview"),
            "printerImagePath": printer_image_path,
            "features": ["videoInfo.videoEncryption", "videoInfo.video", "printControl.xyzControl001005010"],
            "linuxVideoUrl": f"{self._stream_base_url()}/api/v1/streams",
            "webrtcSupport": True,
            "version": "1.0",
            "isLanPrinter": True,
            "lanCompatible": True,
            "oldPrinter": False,
            "socket": None,
            "state": state_value,
            "deviceState": device_state_value,
            "uploadState": 0,
            "localOnline": True,
            "cloudOnline": False,
            "cxyOnline": False,
            "isExistInLocal": True,
            "isExistInCxy": False,
            "temperature": temperature,
            "printFileName": "",
            "printProgress": 0,
            "printLeftTime": 0,
            "printJobTime": 0,
            "printStartTime": 0,
            "ctrol": {
                "autohome": "X:0 Y:0 Z:0",
                "curPosition": "X:1 Y:1 Z:1",
                "curFeedratePct": 100,
                "speedMode": 1,
                "fan": 0,
                "modelFanPct": 0,
                "fanAuxiliary": 0,
                "auxiliaryFanPct": 0,
                "fanCase": 0,
                "caseFan": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                "caseFanPct": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                "sideFan": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                "sideFanPct": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                "chamberTemp": live_state.get("chamber_temp") if live_state.get("chamber_temp") is not None else 0.0,
                "chamberTempTarget": live_state.get("chamber_temp_target") if live_state.get("chamber_temp_target") is not None else 0.0,
                "ledSw": int(live_state.get("led_state") or 0) if live_state.get("led_state") is not None else 0,
                "lightSw": 0,
            },
            "data": {
                "bedTemp0": temperature["bed"]["value"],
                "nozzleTemp": temperature["nozzle"]["value"],
                "targetBedTemp0": temperature["bed"]["target"],
                "targetNozzleTemp": temperature["nozzle"]["target"],
            },
            "deviceUI": "",
            "hostType": "",
            "moonrakerPort": runtime_ports["moonraker_port"],
            "fluiddPort": runtime_ports["fluidd_port"],
            "mainsailPort": runtime_ports["mainsail_port"],
            "KlipperUrl": f"{self._stream_base_url()}/api/v1/streams",
            "boxsInfo": boxs_info,
            "boxConfig": {"cAutoFeed": 1, "cMode": 0, "autoRefill": 1, "ignoreColorAutoFeed": 0},
            "supportMultiple": True,
            "machinePlatformMotionEnable": 1,
            "materialDetector1": 1,
            "filamentsList": [{"cId": 1, "id": 1, "name": DEFAULT_MATERIAL_NAME, "color": DEFAULT_MATERIAL_COLOR, "type": "PLA", "selected": True, "progress": 1.0}],
            "autohome": "X:0 Y:0 Z:0",
            "curPosition": "X:1 Y:1 Z:1",
            "curFeedratePct": 100,
            "speedMode": 1,
            "fan": 0,
            "modelFanPct": 0,
            "fanAuxiliary": 0,
            "auxiliaryFanPct": 0,
            "fanCase": 0,
            "caseFan": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
            "caseFanPct": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
            "sideFan": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
            "sideFanPct": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
            "chamberTemp": live_state.get("chamber_temp") if live_state.get("chamber_temp") is not None else 0.0,
            "chamberTempTarget": live_state.get("chamber_temp_target") if live_state.get("chamber_temp_target") is not None else 0.0,
            "ledSw": int(live_state.get("led_state") or 0) if live_state.get("led_state") is not None else 0,
            "lightSw": 0,
            "status": status_payload,
            "cameraState": {"enabled": True, "state": "ready"},
            "recordState": {"recording": False, "timelapse": False},
            "streamState": {"active": True, "source": "webcam"},
            "record": {"timelapse": False, "video": True, "camera": True, "recording": False, "state": "done"},
        }
        persisted_override_keys = (
            "address", "mac", "deviceType", "video", "tbId", "keyFileToken", "videoToken",
            "connectType", "machinePlatformMotionEnable", "materialDetector1", "supportMultiple",
            "isLanPrinter", "lanCompatible", "oldPrinter", "cameraState", "recordState", "streamState",
            "record", "filamentsList", "boxsInfo", "boxConfig", "features", "previewimg", "deviceImg",
            "defaultDeviceImg", "printerImagePath", "linuxVideoUrl", "webrtcSupport",
            "model_name", "name", "deviceName", "aliasName", "type",
            "online", "socket", "moonrakerPort", "fluiddPort", "mainsailPort", "KlipperUrl",
            "printFileName", "printProgress", "printLeftTime", "printJobTime", "printStartTime",
        )
        preserve_media_state = persisted_identity.get("_preserve_media_state") is True
        for key in persisted_override_keys:
            if key in persisted_identity and persisted_identity[key] is not None and key not in {"status", "temperature", "ctrol", "data"}:
                if key in {"cameraState", "recordState", "streamState", "record"} and not preserve_media_state:
                    continue
                if payload.get("isLanPrinter") is True and key == "deviceName":
                    continue
                payload[key] = persisted_identity[key]
        if payload.get("isLanPrinter") is True:
            payload["identity"] = None
            payload["status"] = status_payload
            payload["temperature"] = temperature
            payload["cameraState"] = payload.get("cameraState") or {"enabled": True, "state": "ready"}
            payload["recordState"] = payload.get("recordState") or {"recording": False, "timelapse": False}
            payload["streamState"] = payload.get("streamState") or {"active": True, "source": "webcam"}
            payload["record"] = payload.get("record") or {"timelapse": False, "video": True, "camera": True, "recording": False, "state": "done"}
            payload["ctrol"] = {
                "autohome": "X:0 Y:0 Z:0",
                "curPosition": "X:1 Y:1 Z:1",
                "curFeedratePct": 100,
                "speedMode": 1,
                "fan": 0,
                "modelFanPct": 0,
                "fanAuxiliary": 0,
                "auxiliaryFanPct": 0,
                "fanCase": 0,
                "caseFan": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                "caseFanPct": live_state.get("case_fan_speed") if live_state.get("case_fan_speed") is not None else 0,
                "sideFan": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                "sideFanPct": live_state.get("side_fan_speed") if live_state.get("side_fan_speed") is not None else 0,
                "chamberTemp": live_state.get("chamber_temp") if live_state.get("chamber_temp") is not None else 0.0,
                "chamberTempTarget": live_state.get("chamber_temp_target") if live_state.get("chamber_temp_target") is not None else 0.0,
                "ledSw": int(live_state.get("led_state") or 0) if live_state.get("led_state") is not None else 0,
                "lightSw": 0,
            }
            payload["data"] = {
                "bedTemp0": temperature["bed"]["value"],
                "nozzleTemp": temperature["nozzle"]["value"],
                "targetBedTemp0": temperature["bed"]["target"],
                "targetNozzleTemp": temperature["nozzle"]["target"],
            }
        elif "identity" in persisted_identity and persisted_identity["identity"] is not None:
            payload["identity"] = persisted_identity["identity"]
        else:
            payload["identity"] = payload.get("identity") or payload.get("address")
        return payload

    def serve_print_cluster_device_edit(self, payload=None):
        if payload is None:
            headers = getattr(self, "headers", None)
            content_length = headers.get("Content-Length", "0") if headers is not None else "0"
            payload = self._read_json_body(int(content_length or "0"))
        if not isinstance(payload, dict):
            payload = {}
        name_value = self._first_present(payload.get("deviceName"), payload.get("aliasName"), payload.get("name"))
        persisted_payload = dict(payload)
        persisted_payload.setdefault("isLanPrinter", True)
        persisted_payload.setdefault("lanCompatible", True)
        persisted_payload.setdefault("oldPrinter", False)
        if name_value:
            persisted_payload.update({
                "deviceName": payload.get("deviceName") or name_value,
                "aliasName": payload.get("aliasName") or name_value,
                "name": payload.get("name") or name_value,
            })
            self._save_persisted_identity(persisted_payload)
        self._send_json({
            "code": 0,
            "message": "success",
            "result": {
                "deviceName": name_value or "",
                "aliasName": name_value or "",
                "name": name_value or "",
            },
        })

    def _build_creality_device_status_payload(self):
        detail = self._build_detail_payload()
        return {
            "code": 0,
            "message": "success",
            "result": detail["result"],
        }

    def _build_creality_cxy_status_payload(self):
        detail = self._build_detail_payload()
        return {
            "code": 0,
            "message": "success",
            "result": detail["result"],
        }

    def _fetch_json(self, path, timeout=UPSTREAM_TIMEOUT):
        url = f"{MOONRAKER_URL}{path}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _derive_ui_state(self, printer_status):
        if not isinstance(printer_status, dict):
            return 1, 1

        if printer_status.get("_status_available") is False:
            return 1, 1

        status_block = printer_status.get("print_stats") if isinstance(printer_status.get("print_stats"), dict) else {}
        display_block = printer_status.get("display_status") if isinstance(printer_status.get("display_status"), dict) else {}
        state_name = status_block.get("state") or display_block.get("state") or printer_status.get("state")

        if state_name is None:
            return 1, 1

        if isinstance(state_name, str):
            normalized = state_name.lower()
            if normalized == "printing":
                return 1, 1
            if normalized == "paused":
                return 5, 1
            if normalized == "complete":
                return 2, 0
            if normalized == "error":
                return 3, 0
            if normalized == "cancelled":
                return 4, 0
            if normalized in {"standby", "ready", "idle", ""}:
                return 0, 0

        return 1, 1

    def _fetch_printer_status(self, timeout=UPSTREAM_TIMEOUT):
        try:
            payload = self._fetch_json("/printer/objects/query?print_stats&display_status&gcode_move&temperature&heater_bed&extruder", timeout=timeout)
        except Exception:
            payload = {}

        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        status = result.get("status", {}) if isinstance(result, dict) else {}
        if not isinstance(status, dict):
            status = {}

        print_stats = status.get("print_stats", {})
        display_status = status.get("display_status", {})
        heater_bed = status.get("heater_bed", {})
        extruder = status.get("extruder", {})
        gcode_move = status.get("gcode_move", {})
        if not isinstance(print_stats, dict):
            print_stats = {}
        if not isinstance(display_status, dict):
            display_status = {}
        if not isinstance(heater_bed, dict):
            heater_bed = {}
        if not isinstance(extruder, dict):
            extruder = {}
        if not isinstance(gcode_move, dict):
            gcode_move = {}

        status_available = bool(status and (print_stats or display_status or heater_bed or extruder or gcode_move))
        state_name = print_stats.get("state") or display_status.get("state") or "standby"
        return {
            "_status_available": status_available,
            "state": state_name,
            "display_status": {
                "progress": display_status.get("progress", 0.0),
                "message": display_status.get("message"),
            },
            "heater_bed": {
                "temperature": heater_bed.get("temperature", 0.0),
                "target": heater_bed.get("target", 0.0),
            },
            "extruder": {
                "temperature": extruder.get("temperature", 0.0),
                "target": extruder.get("target", 0.0),
            },
            "print_stats": {
                "state": state_name,
                "filename": print_stats.get("filename", ""),
                "print_duration": print_stats.get("print_duration", 0),
                "total_duration": print_stats.get("total_duration", 0),
                "filament_used": print_stats.get("filament_used", 0),
                "message": print_stats.get("message", ""),
            },
            "gcode_move": {
                "speed_factor": gcode_move.get("speed_factor", 1.0),
            },
        }

    def _fetch_live_state(self, timeout=UPSTREAM_TIMEOUT):
        state = {}
        try:
            payload = self._fetch_json("/printer/objects/query?temperature_sensor%20chamber_temp&heater_fan%20chamber_fan&temperature_fan%20chamber_fan&output_pin%20LED&output_pin%20fan0&output_pin%20fan1&output_pin%20fan2", timeout=timeout)
        except Exception:
            payload = {}
        status = payload.get("result", {}).get("status", {}) if isinstance(payload, dict) else {}
        if isinstance(status, dict):
            chamber_temp = status.get("temperature_sensor chamber_temp", {})
            chamber_fan = status.get("heater_fan chamber_fan", {})
            chamber_temp_fan = status.get("temperature_fan chamber_fan", {})
            led = status.get("output_pin LED", {})
            fan0 = status.get("output_pin fan0", {})
            fan1 = status.get("output_pin fan1", {})
            fan2 = status.get("output_pin fan2", {})
            state.update({
                "chamber_temp": chamber_temp.get("temperature") if isinstance(chamber_temp, dict) else None,
                "chamber_temp_target": chamber_temp_fan.get("target") if isinstance(chamber_temp_fan, dict) else None,
                "case_fan_speed": chamber_fan.get("speed") if isinstance(chamber_fan, dict) else None,
                "side_fan_speed": chamber_temp_fan.get("speed") if isinstance(chamber_temp_fan, dict) else None,
                "led_state": led.get("value") if isinstance(led, dict) else None,
                "fan0_state": fan0.get("value") if isinstance(fan0, dict) else None,
                "fan1_state": fan1.get("value") if isinstance(fan1, dict) else None,
                "fan2_state": fan2.get("value") if isinstance(fan2, dict) else None,
            })
        return state

    def _fetch_moonraker_info(self, timeout=UPSTREAM_TIMEOUT):
        try:
            server_info = self._fetch_json("/server/info", timeout=timeout)
        except (URLError, ValueError, json.JSONDecodeError, OSError):
            server_info = {}

        result = server_info.get("result", {}) if isinstance(server_info, dict) else {}
        moonraker_port = self._extract_port_from_url(MOONRAKER_URL)

        printer_name = None
        try:
            printer_info = self._fetch_json("/printer/info", timeout=timeout)
            printer_result = printer_info.get("result", {}) if isinstance(printer_info, dict) else {}
            printer_name = printer_result.get("hostname") or printer_result.get("name")
        except (URLError, ValueError, json.JSONDecodeError, OSError):
            printer_name = None

        machine_name = result.get("name") or result.get("host") or DEFAULT_MODEL
        machine_type = result.get("model") or result.get("machine_type") or DEFAULT_MODEL
        resolved_printer_name = printer_name or machine_name
        return {
            "machine_name": machine_name,
            "machine_type": machine_type,
            "printer_name": resolved_printer_name,
            "hostname": resolved_printer_name,
            "moonraker_port": moonraker_port,
            "printer_id": 1,
            "fluidd_port": 80,
            "mainsail_port": 80,
            "status": 1,
            "printer_image_path": "",
        }

    def _extract_upload_from_multipart(self, body, content_type):
        if not body:
            return None, None

        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        body_bytes = bytes(body)

        if not content_type:
            content_type = self.headers.get("Content-Type", "") if hasattr(self, "headers") else ""

        content_type_lower = content_type.lower()
        if "multipart/form-data" not in content_type_lower:
            return None, None

        boundary = None
        for part in content_type_lower.split(";"):
            if "boundary=" in part:
                boundary = part.split("=", 1)[1].strip()
                break
        if not boundary:
            return None, None

        marker = f"--{boundary}".encode("utf-8")
        if marker not in body_bytes:
            return None, None

        parts = body_bytes.split(marker)
        for chunk in parts:
            if not chunk or chunk in {b"\r\n", b"--\r\n", b"--"}:
                continue
            chunk = chunk.lstrip(b"\r\n")
            if chunk.startswith(b"--"):
                continue
            if b"Content-Disposition:" not in chunk:
                continue
            header, separator, payload = chunk.partition(b"\r\n\r\n")
            if not separator:
                continue
            headers = header.decode("utf-8", errors="replace").split("\r\n")
            disposition = None
            for line in headers:
                if line.lower().startswith("content-disposition:"):
                    disposition = line
                    break
            if not disposition:
                continue
            if 'name="file"' not in disposition and "name='file'" not in disposition:
                continue
            filename = None
            for token in disposition.split(";"):
                if token.strip().startswith("filename="):
                    filename = token.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if not filename:
                filename = "upload"
            file_data = payload.rstrip(b"\r\n")
            return filename, file_data

        return None, None

    def _build_multipart_upload_body(self, file_name, file_bytes):
        boundary = f"----creality-probe-{uuid.uuid4().hex}"
        chunks = []
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_bytes)
        chunks.append(b"\r\n")
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(b'Content-Disposition: form-data; name="root"\r\n\r\n')
        chunks.append(b"gcodes\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return boundary, b"".join(chunks)

    def _forward_upload_to_moonraker(self, file_name, file_bytes):
        boundary, upload_body = self._build_multipart_upload_body(file_name, file_bytes)
        url = f"{MOONRAKER_URL}/server/files/upload"
        request = urllib.request.Request(
            url,
            data=upload_body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=max(UPSTREAM_TIMEOUT, 30.0)) as response:
                return response.read(), response.status
        except HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            if not body:
                body = json.dumps({"error": {"code": exc.code, "message": str(exc)}}).encode("utf-8")
            return body, exc.code
        except Exception as exc:
            body = json.dumps({"error": {"code": 500, "message": str(exc)}}).encode("utf-8")
            return body, 500

    def _forward_print_cancel_to_moonraker(self, path):
        route_map = {
            "/printer/print/cancel": "/printer/print/cancel",
            "/printer/print/stop": "/printer/print/cancel",
            "/printer/cancel": "/printer/print/cancel",
            "/printer/emergency_stop": "/printer/emergency_stop",
        }
        upstream_path = route_map.get(path, "/printer/print/cancel")
        request = urllib.request.Request(
            f"{MOONRAKER_URL}{upstream_path}",
            data=b"",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=max(UPSTREAM_TIMEOUT, 5.0)) as response:
                body = response.read()
                if not body:
                    body = json.dumps({"result": "ok"}).encode("utf-8")
                return body, response.status
        except HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            if not body:
                body = json.dumps({"error": {"code": exc.code, "message": str(exc)}}).encode("utf-8")
            return body, exc.code
        except Exception as exc:
            body = json.dumps({"result": "ok", "shim": "cancel_proxy", "note": str(exc)}).encode("utf-8")
            return body, 200

    def _extract_port_from_url(self, url):
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return 7125
        if parsed.port:
            return parsed.port
        if parsed.scheme == "https":
            return 443
        return 80 if parsed.scheme == "http" else 7125

    def _build_device_identity_fields(self, info_payload=None, request_payload=None):
        info_payload = info_payload or {}
        request_payload = request_payload or {}
        request_payload = request_payload if isinstance(request_payload, dict) else {}
        info_payload = info_payload if isinstance(info_payload, dict) else {}

        explicit_model = (
            request_payload.get("modelName")
            or request_payload.get("model")
            or request_payload.get("machine_type")
            or info_payload.get("modelName")
            or info_payload.get("model")
            or info_payload.get("machine_type")
        )
        explicit_machine_name = self._first_present(
            request_payload.get("machine_name"),
            info_payload.get("machine_name"),
            info_payload.get("name"),
        )
        explicit_machine_type = (
            request_payload.get("machine_type")
            or request_payload.get("modelName")
            or request_payload.get("model")
            or info_payload.get("machine_type")
            or info_payload.get("modelName")
            or info_payload.get("model")
        )

        display_name = self._first_present(
            request_payload.get("deviceName"),
            request_payload.get("aliasName"),
            request_payload.get("name"),
            info_payload.get("name"),
            info_payload.get("deviceName"),
            info_payload.get("aliasName"),
            info_payload.get("machine_name"),
            explicit_model,
            DEFAULT_MODEL,
        )
        model_value = explicit_model or display_name
        machine_name_value = explicit_machine_name or explicit_model or display_name
        machine_type_value = explicit_machine_type or explicit_model or model_value
        return {
            "display_name": display_name,
            "model": model_value,
            "model_name": model_value,
            "machine_name": machine_name_value,
            "machine_type": machine_type_value,
            "name": machine_name_value,
        }

    def _resolve_identity_fields(self, moonraker_info):
        machine_name = self._normalized_model(moonraker_info.get("machine_name"), fallback=DEFAULT_MODEL)
        machine_type = self._normalized_model(
            moonraker_info.get("machine_type") or moonraker_info.get("model") or moonraker_info.get("machine_name"),
            fallback=machine_name or DEFAULT_MODEL,
        )
        cloud_name = self._normalized_model(
            moonraker_info.get("printer_name") or moonraker_info.get("hostname") or moonraker_info.get("machine_name"),
            fallback=machine_name or machine_type or DEFAULT_MODEL,
        )
        display_name = machine_name or machine_type or cloud_name or DEFAULT_MODEL
        return {
            "model": machine_type,
            "modelName": machine_type,
            "machine_name": display_name,
            "machine_type": machine_type,
            "name": display_name,
        }

    def _normalized_model(self, value, fallback=DEFAULT_MODEL):
        text = (value or "").strip()
        if not text:
            return fallback
        lowered = text.lower()
        if lowered in {"printer", "unknown", "generic"}:
            return fallback
        return text

    def _find_first_ipv4(self, network):
        if not isinstance(network, dict):
            return None
        for interface in network.values():
            if not isinstance(interface, dict):
                continue
            for item in interface.get("ip_addresses", []) or []:
                if isinstance(item, dict):
                    address = item.get("address")
                    if isinstance(address, str) and not address.startswith("fe80"):
                        return address
        return None

    def _find_first_mac(self, network):
        if not isinstance(network, dict):
            return None
        for interface in network.values():
            if not isinstance(interface, dict):
                continue
            for key in ("mac_address", "mac"):
                value = interface.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    def _guess_ip(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return None

    def _guess_mac(self):
        try:
            with open("/sys/class/net/eth0/address", "r", encoding="utf-8") as handle:
                value = handle.read().strip()
                if value:
                    return value
        except OSError:
            pass
        try:
            with open("/sys/class/net/wlan0/address", "r", encoding="utf-8") as handle:
                value = handle.read().strip()
                if value:
                    return value
        except OSError:
            pass
        return None

    def _send_webrtc_answer(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length > 0 else b""
        payload = self._build_webrtc_answer_payload(body)
        response_text = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        response_bytes = response_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _build_webrtc_answer_payload(self, body=b""):
        offer_text = body.decode("utf-8", errors="ignore").strip() if body else ""
        sdp_text = offer_text if offer_text.startswith("v=") else self._default_webrtc_sdp_answer()
        return {"type": "answer", "sdp": sdp_text}

    def _default_webrtc_sdp_answer(self):
        return (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            "s=-\r\n"
            "t=0 0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            "a=rtcp-mux\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=fmtp:96 packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1\r\n"
            "a=sendrecv\r\n"
        )

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        if not hasattr(self, "requestline"):
            self.requestline = ""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if hasattr(self, "wfile") and self.wfile is not None:
                self.wfile.write(body)
        except Exception:
            if hasattr(self, "wfile") and self.wfile is not None:
                try:
                    self.wfile.write(body)
                except Exception:
                    pass


def main():
    ports = [PORT]
    for extra_port in EXTRA_PORTS:
        if extra_port not in ports:
            ports.append(extra_port)

    servers = []
    for port in ports:
        try:
            server = CompatHTTPServer((HOST, port), ProbeHandler)
            server._compat_audit = []
            server._compat_payload_snapshots = {}
        except OSError as exc:
            print(f"Failed to bind {HOST}:{port}: {exc}")
            continue
        print(f"Listening on {HOST}:{port}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(thread)

    if not servers:
        raise SystemExit("No compatibility listeners started")

    for thread in servers:
        thread.join()


if __name__ == "__main__":
    main()
