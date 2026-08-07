#!/usr/bin/env python3
"""Patch the Creality Print desktop cache to use the new-printer LAN path.

The desktop app sometimes stores a discovered/re-added printer as
oldPrinter=true, which forces it onto the legacy RTSP camera path and
prevents the WebSocket-driven tabs from updating.  This script rewrites the
local cache entry for the target printer to oldPrinter=false with the full
MAC and correct ports.

Use when the app is not running, or let the script quit it first.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

APP_NAME = "Creality Print"
DEFAULT_HOST = os.environ.get("PRINTER_HOST", "192.168.1.100")
DEFAULT_MAC = os.environ.get("PRINTER_MAC", "A1B2C3D4E5F6")
DEFAULT_MODEL = os.environ.get("PRINTER_MODEL", "F008")


def app_support_root():
    candidates = [
        os.path.expanduser("~/Library/Application Support/Creality/Creality Print/7.0"),
        os.path.expanduser("~/Library/Application Support/Creality/Creality Print"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    # Create the 7.0 path if nothing exists.
    os.makedirs(candidates[0], exist_ok=True)
    return candidates[0]


def quit_app():
    subprocess.run(["osascript", "-e", f'tell application "{APP_NAME}" to quit'], capture_output=True)
    subprocess.run(["pkill", "-f", f"{APP_NAME}"], capture_output=True)


def backup(path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.backup.{ts}"
    shutil.copy2(path, bak)
    return bak


def patch_device_info(path, args):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {"current_device": None, "groups": []}

    entry = None
    for group in data.get("groups", []):
        for dev in group.get("list", []):
            if dev.get("address") == args.host:
                entry = dev
                break
        if entry:
            break

    if entry is None:
        if not data.get("groups"):
            data["groups"] = [{"group": "New Group1", "list": []}]
        entry = {}
        data["groups"][0]["list"].append(entry)

    entry.update({
        "address": args.host,
        "mac": args.mac,
        "model": args.model,
        "modelName": args.model,
        "name": args.name or f"K2Plus-{args.mac[-4:]}",
        "type": 3,
        "deviceType": 0,
        "connectType": 1001,
        "online": True,
        "oldPrinter": False,
        "webrtcSupport": 1,
        "moonrakerPort": args.moonraker_port,
        "fluiddPort": args.fluidd_port,
        "mainsailPort": args.mainsail_port,
        "localOnline": True,
        "cloudOnline": False,
    })

    data["current_device"] = {"mac": args.mac}

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def remove_local_device_files(root):
    user_dir = os.path.join(root, "user")
    if not os.path.isdir(user_dir):
        return
    for dirpath, _dirnames, filenames in os.walk(user_dir):
        for fn in filenames:
            if fn == "local_device":
                path = os.path.join(dirpath, fn)
                print(f"Removing stale native cache: {path}")
                os.remove(path)


def main():
    parser = argparse.ArgumentParser(description="Patch Creality Print cache for LAN bridge")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Printer LAN IP")
    parser.add_argument("--mac", default=DEFAULT_MAC, help="Full printer MAC")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Printer model string")
    parser.add_argument("--name", default="", help="Display name")
    parser.add_argument("--moonraker-port", type=int, default=7125)
    parser.add_argument("--fluidd-port", type=int, default=80)
    parser.add_argument("--mainsail-port", type=int, default=80)
    parser.add_argument("--no-quit", action="store_true", help="Do not quit the app first")
    parser.add_argument("--launch", action="store_true", help="Launch Creality Print after patching")
    args = parser.parse_args()

    root = app_support_root()
    device_info = os.path.join(root, "deviceInfo.json")

    if not args.no_quit:
        quit_app()
        # Give the app a moment to release the files.
        import time
        time.sleep(1)

    if os.path.exists(device_info):
        bak = backup(device_info)
        print(f"Backed up {device_info} -> {bak}")

    os.makedirs(root, exist_ok=True)
    patch_device_info(device_info, args)
    remove_local_device_files(root)
    print(f"Patched {device_info} for {args.host} (oldPrinter=false, mac={args.mac})")

    if args.launch:
        subprocess.run(["open", "-a", APP_NAME])

    return 0


if __name__ == "__main__":
    sys.exit(main())
