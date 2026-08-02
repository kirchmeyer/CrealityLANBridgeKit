#!/usr/bin/env python3
import argparse
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
    tests = [
        ("/info", ["mac", "model", "modelName"]),
        ("/api/v1/device/status", ["code", "message", "result"]),
        ("/cxy/v1/status", ["code", "message", "result"]),
    ]

    for route, keys in tests:
        status, payload = fetch_json(base + route, timeout=timeout)
        if status != 200:
            fail(f"{route} status {status}, expected 200")
        if not isinstance(payload, dict):
            fail(f"{route} payload is not a JSON object")
        assert_keys(payload, keys, route)
        print(f"OK: {route}")

    for route in ["/call/webrtc_local", "/api/v1/streams"]:
        status, payload = fetch_json(base + route, timeout=timeout)
        if status != 200:
            fail(f"{route} status {status}, expected 200")
        if not isinstance(payload, dict):
            fail(f"{route} payload is not a JSON object")
        print(f"OK: {route}")

    for route in ["/api/v1/device/status", "/cxy/v1/status"]:
        status, payload = fetch_json(base + route, timeout=timeout)
        if status != 200:
            fail(f"{route} status {status}, expected 200")
        result = assert_path(payload, ["result"], route)
        if not isinstance(result, dict):
            fail(f"{route} result is not an object")
        for key in ["model", "modelName", "machine_name", "machine_type", "name", "address", "mac"]:
            if key not in result:
                fail(f"{route} missing identity field {key}")
        print(f"OK: {route} identity payload")

    status, payload = fetch_json(base + "/info", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/info invalid response")
    if not payload.get("video"):
        fail("/info did not advertise video capability")
    if not isinstance(payload.get("features"), list) or not any("videoEncryption" in str(item) for item in payload.get("features", [])):
        fail("/info missing videoEncryption feature flag")
    print("OK: /info camera metadata")

    status, payload = fetch_json(base + "/protocal.csp?fname=Info&opt=main&function=get", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/protocal.csp invalid response")
    for key in ["model", "ssid", "mac", "address", "features", "video", "linuxVideoUrl"]:
        if key not in payload:
            fail(f"/protocal.csp missing {key}")
    if not payload.get("video"):
        fail("/protocal.csp did not advertise video capability")
    if not isinstance(payload.get("features"), list) or not any("videoEncryption" in str(item) for item in payload.get("features", [])):
        fail("/protocal.csp missing videoEncryption feature flag")
    print("OK: /protocal.csp legacy compatibility")

    status, payload = fetch_json(base + "/machine/system_info", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/machine/system_info invalid response")
    network = assert_path(payload, ["result", "system_info", "network"], "/machine/system_info")
    if not isinstance(network, dict):
        fail("/machine/system_info result/system_info/network is not object")
    print("OK: /machine/system_info")

    status, payload = fetch_json(base + "/machine/multi_machine", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/machine/multi_machine invalid response")
    printers = assert_path(payload, ["result", "multi_printer_info"], "/machine/multi_machine")
    if not isinstance(printers, list) or not printers:
        fail("/machine/multi_machine result/multi_printer_info is empty")
    first = printers[0]
    if not isinstance(first, dict):
        fail("/machine/multi_machine first printer is not object")
    for k in ["ip", "machine_name", "machine_type", "model", "modelName"]:
        if k not in first:
            fail(f"/machine/multi_machine first printer missing {k}")
    print("OK: /machine/multi_machine")

    status, payload = fetch_json(base + "/printer/objects/query", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/printer/objects/query invalid response")
    _ = assert_path(payload, ["result", "status"], "/printer/objects/query")
    print("OK: /printer/objects/query")

    status, payload = fetch_json(base + "/printer/print/start", timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        fail("/printer/print/start invalid response")
    _ = assert_path(payload, ["result", "print_started"], "/printer/print/start")
    print("OK: /printer/print/start")

    for route in ["/printer/print/cancel", "/printer/print/stop", "/printer/cancel", "/printer/emergency_stop"]:
        status, payload = fetch_json(base + route, method="POST", data=b"", headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=max(timeout, 20.0))
        if status not in (200, 201, 202):
            fail(f"{route} status {status}, expected a success status")
        if not isinstance(payload, dict):
            fail(f"{route} payload is not a JSON object")
        print(f"OK: {route}")

    body = json.dumps({"filename": "contract_check.gcode"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    status, payload = fetch_json(base + "/printer/print/start", method="POST", data=body, headers=headers, timeout=max(timeout, 20.0))
    if status != 200 or not isinstance(payload, dict):
        fail("/printer/print/start POST invalid response")
    result_value = payload.get("result")
    if result_value not in ("ok", "OK"):
        fail("/printer/print/start POST did not return Moonraker-style success payload")
    print("OK: /printer/print/start POST")


def check_legacy_probe(base: str, timeout: float) -> None:
    for route in ["/info", "/protocal.csp"]:
        status, payload = fetch_json(base + route, timeout=timeout)
        if status != 200:
            fail(f"legacy probe {route} status {status}, expected 200")
        if not isinstance(payload, dict):
            fail(f"legacy probe {route} payload is not a JSON object")
        if route == "/protocal.csp" and not payload.get("video"):
            fail("legacy probe /protocal.csp did not advertise video capability")
        print(f"OK: legacy probe {route}")


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
        check_legacy_probe(base, args.timeout)
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
