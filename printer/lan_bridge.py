#!/usr/bin/env python3
"""Minimal Creality LAN compatibility bridge.

Serves only the endpoints the Creality Print desktop app actually uses:
  GET /info
  GET /protocal.csp
  POST /upload/<file>
  WebSocket status push and control commands

Data sources:
  - /mnt/UDISK/creality/userdata/config/system_config.json (identity)
  - /mnt/UDISK/creality/userdata/config/temperature_info.json (targets)
  - /mnt/UDISK/creality/userdata/config/current_work_info.json (active job)
  - /mnt/UDISK/creality/gui/config/pipe-*.json (live XYZ/fan)
  - Moonraker http://127.0.0.1:7125 (state, temps, progress)

Run directly:
  PUBLIC_HOST=3d.nrvous.io python3 printer/lan_bridge.py
"""

import base64
import glob
import hashlib
import json
import os
import select
import socket
import struct
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MOONRAKER_URL = os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7125").rstrip("/")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").strip()
PUBLIC_SCHEME = os.environ.get("PUBLIC_SCHEME", "http").strip()

DEFAULT_MODEL = "F008"
DEFAULT_CFS_NAME = "Lan Compat CFS"
DEFAULT_MATERIAL_NAME = "Material"
DEFAULT_MATERIAL_COLOR = "#FF0000"

# Optional CFS layout mode:
#   0 (default) = stock multi-box layout; empty boxes are dropped. The LAN UI
#                 only renders the first type:0 box, matching stock behavior.
#   1           = flatten all non-empty CFS boxes into one virtual 8-slot box.
CFS_FLATTEN = os.environ.get("CFS_FLATTEN", "0").strip().lower() in ("1", "true", "yes")


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else (default or {})
    except Exception:
        return default or {}


def _read_keybox(key, default=""):
    try:
        with os.popen(f"/usr/bin/keybox {key} 2>/dev/null") as fh:
            value = fh.read().strip()
        return value if value else default
    except Exception:
        return default


def _read_system_config():
    cfg = _read_json("/mnt/UDISK/creality/userdata/config/system_config.json")
    device_info = cfg.get("device_info", {})
    user_info = cfg.get("user_info", {})
    raw_mac = str(device_info.get("device_mac") or _read_keybox("wifi_mac", ""))
    mac = raw_mac.replace(":", "").replace("-", "").replace(".", "").upper()
    return {
        "model": device_info.get("model_str") or _read_keybox("model", DEFAULT_MODEL),
        "sn": device_info.get("device_sn") or _read_keybox("sn", ""),
        "mac": mac or _read_keybox("wifi_mac", "").replace(":", "").upper(),
        "hostname": user_info.get("host_name") or socket.gethostname() or "K2Plus",
    }


def _derive_ssid(model, mac):
    suffix = mac[-4:] if len(mac) >= 4 else mac
    name = (model or DEFAULT_MODEL).replace(" ", "")
    return f"{name}-{suffix}"


def _guess_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("1.1.1.1", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _public_address():
    if PUBLIC_HOST:
        return PUBLIC_HOST
    try:
        info = _fetch_json("/machine/system_info")
        network = info.get("result", {}).get("system_info", {}).get("network", {})
        for iface, data in network.items():
            if not isinstance(data, dict):
                continue
            for entry in data.get("ip_addresses", []):
                addr = entry.get("address", "")
                if addr and not addr.startswith("127.") and ":" not in addr:
                    return addr
    except Exception:
        pass
    return _guess_ip()


def _fetch_json(path, timeout=5.0):
    url = f"{MOONRAKER_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_printer_status(timeout=5.0):
    try:
        payload = _fetch_json(
            "/printer/objects/query?print_stats&display_status&gcode_move&heater_bed&extruder&output_pin%20LED",
            timeout=timeout,
        )
    except Exception:
        payload = {}
    result = payload.get("result", {})
    status = result.get("status", {})
    print_stats = status.get("print_stats", {}) or {}
    display_status = status.get("display_status", {}) or {}
    heater_bed = status.get("heater_bed", {}) or {}
    extruder = status.get("extruder", {}) or {}
    gcode_move = status.get("gcode_move", {}) or {}
    state_name = print_stats.get("state") or display_status.get("state") or "standby"
    output_pin = status.get("output_pin LED", {}) or {}
    led_value = float(output_pin.get("value", 0.0))
    return {
        "state": state_name,
        "display_status": {"progress": display_status.get("progress", 0.0), "message": display_status.get("message")},
        "heater_bed": {"temperature": heater_bed.get("temperature", 0.0), "target": heater_bed.get("target", 0.0)},
        "extruder": {"temperature": extruder.get("temperature", 0.0), "target": extruder.get("target", 0.0)},
        "print_stats": {
            "state": state_name,
            "filename": print_stats.get("filename", ""),
            "print_duration": print_stats.get("print_duration", 0.0),
            "total_duration": print_stats.get("total_duration", 0.0),
            "filament_used": print_stats.get("filament_used", 0.0),
        },
        "gcode_move": {"speed_factor": gcode_move.get("speed_factor", 1.0)},
        "led": led_value,
    }


# Simple in-memory caches for file/history lists so we do not hammer Moonraker
# on every 2-second WebSocket push.
_FILE_CACHE = {"ts": 0.0, "data": []}
_HISTORY_CACHE = {"ts": 0.0, "data": []}
_CACHE_TTL = 15.0
_FILE_LIST_LIMIT = 100
_HISTORY_LIMIT = 50


def _fetch_files_list():
    now = datetime.now(timezone.utc).timestamp()
    if now - _FILE_CACHE["ts"] < _CACHE_TTL and _FILE_CACHE["data"]:
        return _FILE_CACHE["data"]
    try:
        payload = _fetch_json("/server/files/list?root=gcodes", timeout=5.0)
        files = payload.get("result", [])
    except Exception:
        files = []
    _FILE_CACHE["ts"] = now
    _FILE_CACHE["data"] = files
    return files


def _fetch_history_list():
    now = datetime.now(timezone.utc).timestamp()
    if now - _HISTORY_CACHE["ts"] < _CACHE_TTL and _HISTORY_CACHE["data"]:
        return _HISTORY_CACHE["data"]
    try:
        payload = _fetch_json(f"/server/history/list?limit={_HISTORY_LIMIT}", timeout=5.0)
        jobs = payload.get("result", {}).get("jobs", [])
    except Exception:
        jobs = []
    _HISTORY_CACHE["ts"] = now
    _HISTORY_CACHE["data"] = jobs
    return jobs


def _read_local_gcode_file_info():
    try:
        with open("/mnt/UDISK/creality/local_gcode/local_gcode_file_info.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _build_ret_gcode_file_info2():
    return _read_local_gcode_file_info()


def _build_ret_gcode_file_info3():
    files = _fetch_files_list()
    entries = []
    for f in files[:_FILE_LIST_LIMIT]:
        name = f.get("path", "")
        size = int(f.get("size", 0))
        ts = int(f.get("modified", 0))
        if not name:
            continue
        entries.append(f"{name}:{size}:{ts}")
    return {"fileInfo": ";".join(entries) + ";" if entries else ""}


def _build_history_list():
    jobs = _fetch_history_list()
    out = []
    for job in jobs:
        out.append({
            "id": str(job.get("job_id", "")),
            "filename": str(job.get("filename", "")),
            "status": str(job.get("status", "")),
            "starttime": int(job.get("start_time", 0)),
            "usagetime": int(job.get("total_duration", 0)),
        })
    return out


def _read_delay_image_info():
    try:
        with open("/mnt/UDISK/creality/userdata/delay_image/delay_image_info.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data.get("list", [])
    except Exception:
        pass
    return []


def _build_elapse_video_list():
    # The printer stores timelapse metadata in delay_image_info.json.
    # Expose the entries so the app's Timelapse tab can render them.
    entries = []
    for item in _read_delay_image_info():
        name = item.get("gcodename", "")
        if not name:
            continue
        video_path = item.get("video", "")
        cover_path = item.get("cover", "")
        entries.append({
            "id": str(item.get("id", "")),
            "name": name,
            "video": video_path.split("/")[-1] if video_path else "",
            "cover": cover_path.split("/")[-1] if cover_path else "",
            "size": int(item.get("size", 0)),
            "duration": int(item.get("duration", 0)),
            "starttime": int(item.get("starttime", 0)),
            "printtime": int(item.get("printtime", 0)),
        })
    return entries


def _map_state(state_name):
    if not isinstance(state_name, str):
        return 0
    s = state_name.lower()
    if s == "printing":
        return 1
    if s == "paused":
        return 5
    if s == "error":
        return 3
    if s in ("complete", "cancelled"):
        return 4
    return 0


def _read_pipe_data(code):
    try:
        pipes = sorted(glob.glob("/mnt/UDISK/creality/gui/config/pipe-*.json"), key=os.path.getmtime, reverse=True)
        if not pipes:
            return None
        with open(pipes[0], "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        for part in reversed(text.split("\x03")):
            part = part.strip()
            if not part:
                continue
            try:
                obj = json.loads(part)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("code") == code:
                return obj
    except Exception:
        pass
    return None


def _read_temperature_info():
    return _read_json("/mnt/UDISK/creality/userdata/config/temperature_info.json")


def _read_current_work_info():
    return _read_json("/mnt/UDISK/creality/userdata/config/current_work_info.json")


def _make_multipart(file_name, payload):
    boundary = "----lan-bridge-upload"
    parts = []
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(payload)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="root"\r\n\r\n')
    parts.append(b"gcodes\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def _send_gcode(script, timeout=10.0):
    """Send a raw G-code script to Moonraker."""
    if not script or not script.strip():
        return {"ok": False, "error": "empty script"}
    try:
        body = json.dumps({"script": script.strip()}).encode("utf-8")
        req = urllib.request.Request(
            f"{MOONRAKER_URL}/printer/gcode/script",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _debug_log(line):
    try:
        with open("/tmp/lan_bridge_debug.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    except Exception:
        pass


def _handle_set_command(params):
    """Translate Creality Print control commands to Moonraker G-code.

    The desktop app sends {"method":"set","params":{...}} on the WebSocket
    for temperature, fan, LED, speed and motion controls.
    """
    commands = []

    # Temperature targets
    if "nozzleTempControl" in params:
        try:
            temp = int(float(params["nozzleTempControl"]))
            commands.append(f"M104 S{temp}")
        except (TypeError, ValueError):
            pass

    if "bedTempControl" in params:
        bed = params["bedTempControl"]
        if isinstance(bed, dict):
            val = bed.get("val")
        else:
            val = bed
        try:
            temp = int(float(val))
            commands.append(f"M140 S{temp}")
        except (TypeError, ValueError):
            pass

    if "boxTempControl" in params:
        try:
            temp = int(float(params["boxTempControl"]))
            commands.append(f"M141 S{temp}")
        except (TypeError, ValueError):
            pass

    # Printing speed override
    if "setFeedratePct" in params:
        try:
            pct = int(float(params["setFeedratePct"]))
            commands.append(f"M220 S{pct}")
        except (TypeError, ValueError):
            pass
    # Creality cloud/mobile path sends speedMode:1 for the 25 % "Silent" preset.
    if "speedMode" in params:
        try:
            mode = int(float(params["speedMode"]))
            if mode == 1:
                commands.append("M220 S25")
        except (TypeError, ValueError):
            pass

    # Motion controls
    if "autohome" in params:
        axes = str(params["autohome"]).strip()
        commands.append(f"G28 {axes}".strip())

    if "setPosition" in params:
        move = str(params["setPosition"]).strip()
        if move:
            commands.append(f"G1 {move}")

    # Raw G-code passthrough
    if "gcodeCmd" in params:
        cmd = str(params["gcodeCmd"]).strip()
        if cmd:
            commands.append(cmd)

    # Fan / LED toggles. Values from the app may be 0/1, 0-100 %, or 0-255;
    # output_pin expects 0.0-1.0.
    def _pin_value(v):
        try:
            fv = float(v)
            if fv > 100.0:
                fv = fv / 255.0
            elif fv > 1.0:
                fv = fv / 100.0
            return max(0.0, min(1.0, fv))
        except (TypeError, ValueError):
            return 0.0

    if "fan" in params:
        commands.append(f"SET_PIN PIN=fan0 VALUE={_pin_value(params['fan']):.3f}")
    if "fanAuxiliary" in params:
        commands.append(f"SET_PIN PIN=fan1 VALUE={_pin_value(params['fanAuxiliary']):.3f}")
    if "fanCase" in params:
        commands.append(f"SET_PIN PIN=fan2 VALUE={_pin_value(params['fanCase']):.3f}")
    if "lightSw" in params:
        commands.append(f"SET_PIN PIN=LED VALUE={_pin_value(params['lightSw']):.3f}")
    if "ledSw" in params:
        commands.append(f"SET_PIN PIN=LED VALUE={_pin_value(params['ledSw']):.3f}")
    if "led" in params:
        commands.append(f"SET_PIN PIN=LED VALUE={_pin_value(params['led']):.3f}")

    # Job controls
    if "stop" in params and params["stop"]:
        commands.append("M104 S0\nM140 S0\nM220 S100\nM107")
        commands.append("CANCEL_PRINT")
    if "pause" in params:
        if params["pause"]:
            commands.append("PAUSE")
        else:
            commands.append("RESUME")

    results = []
    for cmd in commands:
        results.append(_send_gcode(cmd))

    # Multi-color / start-print command from the send-to-printer dialog.
    if "multiColorPrint" in params:
        mcp = params["multiColorPrint"]
        if isinstance(mcp, dict):
            gcode_path = str(mcp.get("gcode") or "")
            filename = gcode_path.split("/")[-1]
            if filename:
                results.append(_start_print(filename))

    # CFS load/unload command sent from the filament screen.
    if "feedInOrOut" in params:
        feed = params["feedInOrOut"]
        if isinstance(feed, dict):
            box_id = feed.get("boxId")
            material_id = feed.get("materialId")
            is_feed = feed.get("isFeed")
            # The native Creality load/unload macros are conventionally named
            # CFS_LOAD and CFS_UNLOAD. Fallback to raw tool change if absent.
            macro = "CFS_LOAD" if is_feed else "CFS_UNLOAD"
            commands.append(f"{macro} BOX={box_id} SLOT={material_id}")

    # Color-match mapping is informational; the printer firmware consumes it
    # before the multiColorPrint start command, so we just acknowledge it.
    if "colorMatch" in params:
        results.append({"ok": True, "command": "colorMatch", "note": "acknowledged"})

    if not results:
        return {"code": 0, "message": "success", "result": {"ok": True, "params": params}}

    ok = all(r.get("ok") for r in results)
    return {
        "code": 0 if ok else 1,
        "message": "success" if ok else "partial failure",
        "result": {"ok": ok, "params": params, "commands": commands, "details": results},
    }


def _start_print(filename):
    """Ask Moonraker to start printing a file from the gcodes root."""
    try:
        body = json.dumps({"filename": filename}).encode("utf-8")
        req = urllib.request.Request(
            f"{MOONRAKER_URL}/printer/print/start",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            resp.read()
        return {"ok": True, "command": "START_PRINT", "filename": filename}
    except Exception as e:
        return {"ok": False, "command": "START_PRINT", "filename": filename, "error": str(e)}


def _read_material_box_info():
    return _read_json("/mnt/UDISK/creality/userdata/box/material_box_info.json")


def _read_box_config():
    return _read_json("/mnt/UDISK/creality/userdata/box/material_box_config.json")


def _boxs_info_payload():
    """Build the boxsInfo object the app's filament / print dialogs expect.

    Reads the native Creality CFS/AMS state from material_box_info.json.

    Default behavior matches the stock Creality LAN UI: each physical CFS box is
    preserved as its own type:0 materialBox, and empty boxes are dropped so the
    UI does not draw phantom CFS rows.

    Set CFS_FLATTEN=1 to merge all non-empty CFS boxes into a single virtual
    box with stable slot indices. This is useful if you want to see every slot
    in the app's single-box view, but it changes the visual layout.
    """
    data = _read_material_box_info()
    material = data.get("Material", {}) if isinstance(data, dict) else {}
    boxes = material.get("info", [])
    if not isinstance(boxes, list) or not boxes:
        return _boxs_info_placeholder()

    # Drop truly empty boxes so the UI does not draw phantom CFS rows.
    non_empty_boxes = [
        box for box in boxes
        if isinstance(box, dict) and isinstance(box.get("list"), list) and len(box["list"]) > 0
    ]
    if not non_empty_boxes:
        return _boxs_info_placeholder()

    material_boxes = []
    box_color_info = []
    slot_lookup = {}  # composite id (e.g. "T1A") -> slot data

    def _make_slot_entry(slot, box_id, box_name, slot_index):
        """Create a slot entry and its boxColorInfo entry."""
        slot_letter = str(slot.get("materialId") or "")
        if len(slot_letter) == 1 and "A" <= slot_letter <= "Z":
            letter_id = ord(slot_letter) - ord("A")
        else:
            try:
                letter_id = int(slot_letter)
            except (TypeError, ValueError):
                letter_id = slot_index
        composite = f"{box_name}{slot_letter}"

        color_raw = str(slot.get("color") or "")
        if color_raw.startswith("0") and len(color_raw) == 7:
            color = "#" + color_raw[1:]
        elif color_raw.startswith("#0") and len(color_raw) == 8:
            color = "#" + color_raw[2:]
        elif not color_raw.startswith("#"):
            color = "#" + color_raw.lstrip("0") or DEFAULT_MATERIAL_COLOR
        else:
            color = color_raw

        try:
            remain = int(str(slot.get("remainLen") or "0"))
        except (TypeError, ValueError):
            remain = 0
        try:
            total = int(str(slot.get("filamentLen") or "0")) or remain or 1
        except (TypeError, ValueError):
            total = remain or 1
        percent = int(round(remain / total * 100)) if total else 100

        state = int(slot.get("rfid", 0)) or int(slot.get("state", 0)) or 2
        material_type = str(slot.get("materialType") or slot.get("type") or DEFAULT_MATERIAL_NAME)
        filament_name = str(slot.get("name") or material_type)
        brand = str(slot.get("brand") or "Creality")

        entry = {
            "id": letter_id,
            "cId": composite,
            "boxId": box_id,
            "boxType": 0,
            "materialId": letter_id,
            "name": filament_name,
            "filamentName": filament_name,
            "color": color,
            "type": material_type,
            "filamentType": material_type,
            "state": state,
            "RFIDState": state,
            "percent": percent,
            "remaining_length": remain,
            "vendor": brand,
            "brand": brand,
            "minTemp": slot.get("minTemp", 190),
            "maxTemp": slot.get("maxTemp", 240),
            "diameter": slot.get("diameter", "1.75"),
            "selected": 0,
        }
        box_color_entry = {
            "boxType": 0,
            "color": color,
            "boxId": box_id,
            "materialId": letter_id,
            "filamentType": material_type,
            "filamentName": filament_name,
            "RFIDState": state,
            "percent": percent,
            "remaining_length": remain,
            "type": material_type,
            "id": letter_id,
            "name": filament_name,
            "cId": composite,
            "selected": 0,
        }
        return entry, box_color_entry, composite

    if CFS_FLATTEN:
        flat_slot_index = 0
        flat_materials = []
        for box in non_empty_boxes:
            box_name = str(box.get("boxID") or "CFS")
            for slot_index, slot in enumerate(box.get("list", [])):
                entry, box_color_entry, composite = _make_slot_entry(slot, 1, box_name, slot_index)
                entry["id"] = flat_slot_index
                entry["boxId"] = 1
                entry["materialId"] = flat_slot_index
                box_color_entry["id"] = flat_slot_index
                box_color_entry["boxId"] = 1
                box_color_entry["materialId"] = flat_slot_index
                flat_materials.append(entry)
                slot_lookup[composite] = entry
                box_color_info.append(box_color_entry)
                flat_slot_index += 1
        material_boxes.append({
            "id": 1,
            "name": DEFAULT_CFS_NAME,
            "type": 0,
            "state": 0,
            "humidity": 0,
            "materials": flat_materials,
        })
    else:
        for box_index, box in enumerate(non_empty_boxes):
            box_id = box_index + 1
            box_name = str(box.get("boxID") or f"T{box_id}")
            materials = []
            for slot_index, slot in enumerate(box.get("list", [])):
                entry, box_color_entry, composite = _make_slot_entry(slot, box_id, box_name, slot_index)
                materials.append(entry)
                slot_lookup[composite] = entry
                box_color_info.append(box_color_entry)
            material_boxes.append({
                "id": box_id,
                "name": box_name,
                "type": 0,
                "state": 0,
                "humidity": 0,
                "materials": materials,
            })

    # Build same_material / color_same_material groupings from native data.
    # The LAN UI (deviceType==0) expects:
    #   same_material[n] = [filamentId, color, [slot_refs...], materialType]
    #   color_same_material[n] = [color, [slot_refs...]]
    # where each slot_ref is {boxId, materialId} and may include color.
    same_material = []
    color_same_material = []
    native_same = material.get("same_material", [])
    if isinstance(native_same, list):
        for group in native_same:
            if not isinstance(group, (list, tuple)) or len(group) < 4:
                continue
            slots = group[2] if isinstance(group[2], list) else []
            filament_id = group[0] if isinstance(group[0], str) else None
            color_raw = str(group[1]) if len(group) > 1 else ""
            material_type = group[3] if isinstance(group[3], str) else ""
            refs = []
            color_refs = []
            for comp in slots:
                info = slot_lookup.get(comp)
                if info:
                    ref = {"boxId": info["boxId"], "materialId": info["id"]}
                    refs.append(ref)
                    color_refs.append({**ref, "color": info.get("color", color_raw)})
            if refs:
                same_material.append([filament_id, color_raw, refs, material_type])
                color_same_material.append([color_raw, color_refs])
    if not same_material:
        target = material_boxes[0]["materials"]
        for info in target:
            ref = {"boxId": info["boxId"], "materialId": info["id"]}
            same_material.append([None, info["color"], [ref], info["type"]])
            color_same_material.append([info["color"], [{**ref, "color": info["color"]}]])

    return {
        "same_material": same_material,
        "color_same_material": color_same_material,
        "boxColorInfo": box_color_info,
        "materialBoxs": material_boxes,
        "cfsName": material.get("cfsName") or "CFS",
    }


def _boxs_info_placeholder():
    """Minimal fallback boxsInfo when native CFS data is unavailable."""
    box_color_info = {
        "boxType": 0,
        "color": DEFAULT_MATERIAL_COLOR,
        "material": DEFAULT_MATERIAL_NAME,
        "materialName": DEFAULT_MATERIAL_NAME,
        "filamentName": DEFAULT_MATERIAL_NAME,
        "boxId": 1,
        "materialId": 0,
        "filamentType": DEFAULT_MATERIAL_NAME,
        "id": 0,
        "name": DEFAULT_CFS_NAME,
        "cId": "T1A",
        "RFIDState": 2,
        "percent": 100,
        "remaining_length": 1000000,
        "selected": 0,
    }
    ref = {"boxId": 1, "materialId": 0}
    same_material = [[None, DEFAULT_MATERIAL_COLOR, [ref], DEFAULT_MATERIAL_NAME]]
    color_same_material = [[DEFAULT_MATERIAL_COLOR, [{**ref, "color": DEFAULT_MATERIAL_COLOR}]]]
    return {
        "same_material": same_material,
        "color_same_material": color_same_material,
        "boxColorInfo": [box_color_info],
        "materialBoxs": [{
            "id": 1,
            "name": DEFAULT_CFS_NAME,
            "type": 0,
            "state": 0,
            "humidity": 0,
            "materials": [{
                "id": 0,
                "cId": "T1A",
                "boxId": 1,
                "boxType": 0,
                "materialId": 0,
                "name": DEFAULT_MATERIAL_NAME,
                "filamentName": DEFAULT_MATERIAL_NAME,
                "color": DEFAULT_MATERIAL_COLOR,
                "type": DEFAULT_MATERIAL_NAME,
                "filamentType": DEFAULT_MATERIAL_NAME,
                "state": 2,
                "RFIDState": 2,
                "percent": 100,
                "remaining_length": 1000000,
                "selected": 0,
            }],
        }],
        "cfsName": DEFAULT_CFS_NAME,
    }


def _build_info_payload():
    identity = _read_system_config()
    model = identity.get("model") or DEFAULT_MODEL
    mac = (identity.get("mac") or "").upper()
    address = _public_address() or _guess_ip()
    name = identity.get("hostname") or _derive_ssid(model, mac)
    return {
        "mac": mac,
        "model": model,
        "modelName": model,
        "name": name,
        "deviceName": name,
        "aliasName": name,
        "address": address,
        "sn": identity.get("sn", ""),
        "version": "1.0.0",
        "videoPort": 443,
        "wssPort": 443,
        "connectType": 1001,
        "oldPrinter": False,
        "isLanPrinter": True,
        "lanCompatible": True,
    }


def _build_protocal_payload():
    identity = _read_system_config()
    model = identity.get("model") or DEFAULT_MODEL
    mac = (identity.get("mac") or "").upper()
    hostname = identity.get("hostname") or _derive_ssid(model, mac)
    address = _public_address() or _guess_ip()

    status = _fetch_printer_status()
    print_stats = status.get("print_stats", {})
    display_status = status.get("display_status", {})
    gcode_move = status.get("gcode_move", {})
    extruder = status.get("extruder", {})
    heater_bed = status.get("heater_bed", {})
    state_int = _map_state(status.get("state", "standby"))

    pipe = _read_pipe_data("key706") or {}
    pipe_data = pipe.get("data", {}) if isinstance(pipe, dict) else {}

    temp_info = _read_temperature_info()
    work_info = _read_current_work_info()

    nozzle_temp = float(pipe_data.get("hot_end_temp") or extruder.get("temperature") or 0.0)
    bed_temp = float(pipe_data.get("hot_bed_temp") or heater_bed.get("temperature") or 0.0)
    nozzle_target = float(temp_info.get("extruder") or extruder.get("target") or 0.0)
    bed_target = float(temp_info.get("bed") or heater_bed.get("target") or 0.0)

    progress = float(display_status.get("progress") or 0.0)
    progress_pct = int(progress * 100)
    print_duration = float(print_stats.get("print_duration") or 0.0)
    print_left_time = 0
    if progress_pct > 0:
        total_time = print_duration / (progress_pct / 100.0)
        print_left_time = int(max(0.0, total_time - print_duration))

    print_start_time = 0
    work_file = ""
    if state_int == 1:
        start_ms = work_info.get("start_time")
        if isinstance(start_ms, (int, float)) and start_ms > 1e9:
            print_start_time = int(start_ms)
        work_file = work_info.get("work_file") or print_stats.get("filename") or ""

    xyz = pipe_data.get("xyz") or [0, 0, 0]
    if not isinstance(xyz, (list, tuple)) or len(xyz) < 3:
        xyz = [0, 0, 0]
    xyz_mm = [v / 1000.0 if isinstance(v, (int, float)) and abs(v) > 1000 else v for v in xyz]

    fan_duty = float(pipe_data.get("model_fan") or 0.0)
    fan_pct = int(round(fan_duty * 100)) if fan_duty <= 1.0 else int(round(fan_duty))

    return {
        "model": model,
        "modelName": model,
        "name": hostname,
        "deviceName": hostname,
        "aliasName": hostname,
        "mac": mac,
        "address": address,
        "ssid": hostname,
        "type": 0,
        "online": True,
        "connect": 1,
        "connectType": 1001,
        "deviceType": 0,
        "video": 1,
        "features": ["videoInfo.video"],
        "linuxVideoUrl": f"http://{address}:80/camera.jpeg",
        "webrtcSupport": False,
        "version": "1.0.0",
        "isLanPrinter": True,
        "lanCompatible": True,
        "oldPrinter": False,
        "socket": None,
        "state": state_int,
        "deviceState": 1 if state_int in (1, 5) else 0,
        "uploadState": 0,
        "localOnline": True,
        "cloudOnline": False,
        "nozzleTemp": nozzle_temp,
        "bedTemp": bed_temp,
        "nozzleTemp2": nozzle_target,
        "bedTemp2": bed_target,
        "printProgress": progress_pct,
        "printLeftTime": print_left_time,
        "printJobTime": int(print_duration),
        "printStartTime": print_start_time,
        "curFeedratePct": int(round(float(gcode_move.get("speed_factor", 1.0)) * 100)),
        "fan": fan_pct,
        "modelFanPct": fan_pct,
        "autohome": 0,
        "curPosition": f"X:{xyz_mm[0]} Y:{xyz_mm[1]} Z:{xyz_mm[2]}",
        "print": work_file,
        "mcu_is_print": 1 if state_int == 1 else 0,
        "layer": "",
        "TotalLayer": "",
        "modelVersion": "1.0.0",
    }


def _build_detail_payload():
    protocal = _build_protocal_payload()
    status = _fetch_printer_status()
    boxs_info = _boxs_info_payload()
    box_cfg = _read_box_config()
    box_config = {
        "cAutoFeed": 1 if box_cfg.get("filamentAutoRefill") else 0,
        "cSelfTest": 0,
        "cMode": 0,
        "autoRefill": 1 if box_cfg.get("filamentAutoRefill") else 0,
        "ignoreColorAutoFeed": 0,
    }

    temperature = {
        "nozzle": {"value": status.get("extruder", {}).get("temperature", 0.0), "target": status.get("extruder", {}).get("target", 0.0), "max": 300.0, "size": 0.4},
        "bed": {"value": status.get("heater_bed", {}).get("temperature", 0.0), "target": status.get("heater_bed", {}).get("target", 0.0), "max": 120.0},
    }

    device = {
        "online": 1,
        "status": "idle" if protocal["state"] == 0 else "busy",
        "model": protocal["model"],
        "modelName": protocal["modelName"],
        "model_name": protocal["modelName"],
        "machine_name": protocal["modelName"],
        "machine_type": protocal["modelName"],
        "name": protocal["ssid"],
        "address": protocal["address"],
        "mac": protocal["mac"],
        "identity": None,
        "deviceType": 0,
        "type": 0,
        "video": True,
        "previewimg": "",
        "deviceImg": f"./img/machine/{protocal['model']}.png",
        "defaultDeviceImg": "./img/printerImgDefault.svg",
        "printerImagePath": "",
        "features": protocal["features"],
        "linuxVideoUrl": protocal["linuxVideoUrl"],
        "webrtcSupport": False,
        "connectType": 1001,
        "isLanPrinter": True,
        "lanCompatible": True,
        "oldPrinter": False,
        "modelVersion": protocal["modelVersion"],
        "version": protocal["version"],
        "state": protocal["state"],
        "deviceState": protocal["deviceState"],
        "uploadState": 0,
        "localOnline": True,
        "cloudOnline": False,
        "cxyOnline": False,
        "isExistInLocal": True,
        "isExistInCxy": False,
        "temperature": temperature,
        "printFileName": protocal["print"],
        "printProgress": protocal["printProgress"] / 100.0,
        "printLeftTime": protocal["printLeftTime"],
        "printJobTime": protocal["printJobTime"],
        "printStartTime": protocal["printStartTime"],
        "nozzleTemp": temperature["nozzle"]["value"],
        "nozzleTemp2": temperature["nozzle"]["target"],
        "bedTemp": temperature["bed"]["value"],
        "bedTemp2": temperature["bed"]["target"],
        "fan": 0,
        "modelFanPct": 0,
        "fanAuxiliary": 0,
        "fanCase": 0,
        "caseFan": 0,
        "caseFanPct": 0,
        "sideFan": 0,
        "sideFanPct": 0,
        "chamberTemp": 0,
        "chamberTempTarget": 0,
        "ledSw": 1 if status.get("led", 0.0) > 0.01 else 0,
        "lightSw": 1 if status.get("led", 0.0) > 0.01 else 0,
        "ctrol": {
            "autohome": "X:0 Y:0 Z:0",
            "curPosition": protocal["curPosition"],
            "curFeedratePct": protocal["curFeedratePct"],
            "speedMode": 1 if protocal["curFeedratePct"] == 25 else 0,
            "fan": 0,
            "modelFanPct": 0,
            "fanAuxiliary": 0,
            "auxiliaryFanPct": 0,
            "fanCase": 0,
            "caseFan": 0,
            "caseFanPct": 0,
            "sideFan": 0,
            "sideFanPct": 0,
            "chamberTemp": 0,
            "chamberTempTarget": 0,
            "ledSw": 1 if status.get("led", 0.0) > 0.01 else 0,
            "lightSw": 1 if status.get("led", 0.0) > 0.01 else 0,
        },
        "data": {
            "bedTemp0": temperature["bed"]["value"],
            "nozzleTemp": temperature["nozzle"]["value"],
            "targetBedTemp0": temperature["bed"]["target"],
            "targetNozzleTemp": temperature["nozzle"]["target"],
        },
        "deviceUI": "",
        "hostType": "",
        "moonrakerPort": int(MOONRAKER_URL.rsplit(":", 1)[-1]),
        "fluiddPort": 80,
        "mainsailPort": 80,
        "KlipperUrl": protocal["linuxVideoUrl"],
        "boxsInfo": boxs_info,
        "boxConfig": box_config,
    }

    result = {**device}
    # The device list page uses `i.hostname` to set/update the saved name.
    result["hostname"] = protocal["ssid"]
    result["status"] = status
    result["temperature"] = temperature
    result["autohome"] = "X:0 Y:0 Z:0"
    result["curPosition"] = protocal["curPosition"]
    result["curFeedratePct"] = protocal["curFeedratePct"]
    result["speedMode"] = 1 if protocal["curFeedratePct"] == 25 else 0
    result["ctrol"] = dict(device["ctrol"])
    result["data"] = dict(device["data"])
    result["device"] = device
    # Data for the Local Files / Records / Timelapse tabs.
    # retGcodeFileInfo2 carries native thumbnails/paths and must take precedence.
    # Do NOT emit retGcodeFileInfo3; the app's Local Files tab falls back to it
    # and overwrites the richer retGcodeFileInfo2 list with thumbnail-less entries.
    result["retGcodeFileInfo2"] = _build_ret_gcode_file_info2()
    result["historyList"] = _build_history_list()
    result["elapseVideoList"] = _build_elapse_video_list()
    return {"code": 0, "message": "success", "result": result}


class LanBridgeHandler(BaseHTTPRequestHandler):
    WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    server_version = "LanBridge/0.1"

    def log_message(self, fmt, *args):
        # Suppress noisy request logging; rely on nginx logs.
        pass

    def do_GET(self):
        _debug_log(f"HTTP_GET {self.path}")
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.serve_websocket()
            return
        path = self.path.split("?", 1)[0]
        if path == "/info":
            self.send_json(_build_info_payload())
        elif path == "/protocal.csp":
            self.send_json(_build_protocal_payload())
        elif path == "/status":
            self.send_json({"ok": True, "backend": "lan_bridge"})
        else:
            self.send_error(404)

    def do_POST(self):
        _debug_log(f"HTTP_POST {self.path}")
        path = self.path.split("?", 1)[0]
        if path.startswith("/upload/"):
            self._handle_upload(path)
            return
        self.send_error(404)

    def _handle_upload(self, path):
        try:
            file_name = path.split("/", 2)[-1]
            if not file_name:
                self.send_error(400, "missing filename")
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length) if length > 0 else b""

            boundary, body = _make_multipart(file_name, payload)
            url = f"{MOONRAKER_URL}/server/files/upload"
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                # The Creality native uploader expects a bare {code:200, message:"OK"}
                # response even though Moonraker returns a different payload.
                ok_body = json.dumps({"code": 200, "message": "OK"}, ensure_ascii=False).encode("utf-8")
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(ok_body)))
                self.end_headers()
                self.wfile.write(ok_body)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, str(e))

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return
        sock = self.request
        accept = base64.b64encode(hashlib.sha1((key + self.WS_GUID).encode()).digest()).decode()
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        try:
            sock.sendall(response.encode("utf-8"))
        except Exception:
            return

        stop_event = threading.Event()

        def reader():
            try:
                while not stop_event.is_set():
                    frame = self._read_frame(sock, timeout=2.0)
                    if frame is None:
                        continue
                    opcode, data = frame
                    if opcode == 8:
                        stop_event.set()
                        break
                    if opcode == 1 and data:
                        try:
                            msg = json.loads(data.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        # The app sends {"method":"get", "params":{...}}.
                        # We respond with the current status regardless of params.
                        _debug_log(f"WS_RECV {data.decode('utf-8', errors='replace')}")
                        if isinstance(msg, dict):
                            method = msg.get("method")
                            if method == "get":
                                self._send_text(sock, self._status_json())
                            elif method == "set":
                                result = _handle_set_command(msg.get("params") or {})
                                _debug_log(f"WS_SET_RESULT {result}")
                                self._send_text(sock, json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
            finally:
                stop_event.set()

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        try:
            while not stop_event.is_set():
                self._send_text(sock, self._status_json())
                stop_event.wait(2.0)
        except Exception:
            pass
        finally:
            stop_event.set()
            reader_thread.join(timeout=2.0)
            try:
                sock.close()
            except Exception:
                pass

    def _status_json(self):
        detail = _build_detail_payload()
        result = detail.get("result", {}) if isinstance(detail, dict) else {}
        result["timeStamp"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(result, ensure_ascii=False)

    def _send_text(self, sock, text):
        data = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, "big"))
        sock.sendall(bytes(header) + data)

    def _read_frame(self, sock, timeout=2.0):
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            return None
        header = sock.recv(2)
        if len(header) < 2:
            raise ConnectionResetError("short ws header")
        b1, b2 = header
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", sock.recv(8))[0]
        if masked:
            mask = sock.recv(4)
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                raise ConnectionResetError("short ws payload")
            payload += chunk
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload


def main():
    host = os.environ.get("LAN_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("LAN_BRIDGE_PORT", "9002"))
    server = ThreadingHTTPServer((host, port), LanBridgeHandler)
    print(f"lan_bridge listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
