#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple


def fetch_json(url: str, method: str = "GET", data: bytes = b"", headers: Dict[str, str] = None, timeout: float = 8.0) -> Tuple[int, Any]:
    req = urllib.request.Request(url, data=data if method != "GET" else None, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def assert_keys(obj: Dict[str, Any], keys: List[str], route: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        fail(f"{route} missing keys: {missing}")


def assert_path(obj: Dict[str, Any], path: List[str], route: str) -> Any:
    cur: Any = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            fail(f"{route} missing nested path: {'/'.join(path)}")
        cur = cur[p]
    return cur


def make_multipart(file_name: str, payload: bytes) -> Tuple[str, bytes]:
    boundary = "----lan-bridge-contract-check"
    ctype = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    parts: List[bytes] = []
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(payload)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="root"\r\n\r\n')
    parts.append(b"gcodes\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def check_non_upload_routes(base: str, timeout: float) -> None:
    # /info must match the stock Creality firmware shape exactly.
    status, payload = fetch_json(base + "/info", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/info invalid response")
    for key in ["mac", "model", "sn", "version", "videoPort", "wssPort"]:
        if key not in payload:
            fail(f"/info missing required stock key {key}")
    print("OK: /info stock contract")

    # /protocal.csp is the legacy status endpoint the desktop app polls.
    status, payload = fetch_json(base + "/protocal.csp?fname=Info&opt=main&function=get", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/protocal.csp invalid response")
    for key in ["model", "modelName", "ssid", "mac", "address", "features", "video", "linuxVideoUrl", "state", "deviceState", "webrtcSupport", "deviceType"]:
        if key not in payload:
            fail(f"/protocal.csp missing {key}")
    if not payload.get("video"):
        fail("/protocal.csp did not advertise video capability")
    features = payload.get("features", [])
    if not isinstance(features, list) or not any("videoInfo.video" in str(item) for item in features):
        fail("/protocal.csp missing videoInfo.video feature flag")
    if any("videoInfo.videoEncryption" in str(item) for item in features):
        fail("/protocal.csp should not advertise videoInfo.videoEncryption; the macOS app must use http://{printer}:8000/call/webrtc_local")
    if not payload.get("webrtcSupport"):
        fail("/protocal.csp did not advertise webrtcSupport")
    if payload.get("deviceType") != 0:
        fail("/protocal.csp deviceType must be 0 for the LAN camera path")
    print("OK: /protocal.csp legacy compatibility")


def check_websocket(base: str, timeout: float) -> None:
    import socket, ssl, struct
    scheme, rest = base.split("://", 1)
    host_port = rest.split("/", 1)[0]
    host, port_str = host_port.rsplit(":", 1)
    port = int(port_str)
    if scheme == "https":
        port = 443
    key = base64.b64encode(b"x" * 16).decode()
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    req = (
        f"GET /call/websocket HTTP/1.1\r\nHost: {host}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        sock = socket.socket()
        if scheme == "https":
            ctx = ssl._create_unverified_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.send(req.encode())
        resp = sock.recv(1024).decode(errors="ignore")
        if not resp.startswith("HTTP/1.1 101"):
            fail(f"WebSocket handshake failed: {resp.split(chr(13))[0]}")
        print("OK: WebSocket handshake")
        sock.close()
    except Exception as e:
        fail(f"WebSocket check failed: {e}")


def check_upload_routes(base: str, timeout: float) -> None:
    test_body = b"; contract check\nG28\n"

    boundary, body = make_multipart("contract_check.gcode", test_body)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
    }

    status, payload = fetch_json(base + "/server/files/upload", method="POST", data=body, headers=headers, timeout=max(timeout, 20.0))
    if status not in (200, 201):
        fail(f"/server/files/upload status {status}, expected 200/201")
    if not isinstance(payload, dict):
        fail("/server/files/upload payload is not JSON object")
    if "result" not in payload and "item" not in payload and "error" in payload:
        fail("/server/files/upload returned error payload")
    print("OK: /server/files/upload")

    status, payload = fetch_json(base + "/upload/contract_check.gcode", method="POST", data=body, headers=headers, timeout=max(timeout, 20.0))
    if status not in (200, 201):
        fail(f"/upload/contract_check.gcode status {status}, expected 200/201")
    if not isinstance(payload, dict):
        fail("/upload/contract_check.gcode payload is not JSON object")
    if payload.get("message") not in ("OK", "success") and payload.get("code") not in (0, 200):
        fail("/upload/contract_check.gcode missing expected success markers")
    print("OK: /upload/contract_check.gcode")

    spaced_name = "contract check spaced.gcode"
    encoded_name = urllib.parse.quote(spaced_name)
    status, payload = fetch_json(base + "/upload/" + encoded_name, method="POST", data=test_body, timeout=max(timeout, 20.0))
    if status not in (200, 201):
        fail(f"/upload/{encoded_name} status {status}, expected 200/201")
    status, directory = fetch_json(base + "/server/files/list", timeout=max(timeout, 20.0))
    files = directory.get("result", []) if isinstance(directory, dict) else []
    if not any(item.get("path") == spaced_name for item in files if isinstance(item, dict)):
        fail(f"/upload/{encoded_name} did not store decoded filename {spaced_name!r}")
    print(f"OK: /upload/{encoded_name} decoded filename")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate printer compatibility endpoint contracts.")
    parser.add_argument("--host", required=True, help="Printer host or IP (without scheme).")
    parser.add_argument("--scheme", default="http", choices=["http", "https"], help="URL scheme")
    parser.add_argument("--port", type=int, default=80, help="Port exposed by nginx front door")
    parser.add_argument("--timeout", type=float, default=8.0, help="Request timeout in seconds")
    parser.add_argument("--skip-upload", action="store_true", help="Skip upload endpoint checks")
    args = parser.parse_args()

    base = f"{args.scheme}://{args.host}:{args.port}"
    print(f"Checking base: {base}")

    try:
        check_non_upload_routes(base, args.timeout)
        check_websocket(base, args.timeout)
        if not args.skip_upload:
            check_upload_routes(base, args.timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        fail(f"HTTP error {e.code} at {e.url}: {body[:400]}")
    except urllib.error.URLError as e:
        fail(f"Network error: {e}")

    print("PASS: all requested contract checks succeeded")


if __name__ == "__main__":
    main()
