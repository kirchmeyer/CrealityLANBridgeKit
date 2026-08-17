#!/usr/bin/env python3
"""Compare local repo files with their deployed copies on the printer.

Reports drift (missing files, checksum mismatches) and can optionally push
only the files that differ. This makes deployments idempotent and gives a
single place to verify that the repo matches the live printer.

Usage:
    python3 scripts/check_local_remote_sync.py [--host HOST] [--user USER] [--sync]

Exit codes:
    0  all tracked files match (or sync succeeded)
    1  drift detected or an error occurred
"""
import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

# Canonical mapping: local repo path -> remote printer path.
# Keep this in sync with the deploy scripts.
FILE_MAP = {
    "printer/app_cloud_only.init.sh": "/etc/init.d/app_cloud_only",
    "printer/fix_moonraker_reserved_path.sh": "/usr/local/bin/fix_moonraker_reserved_path.sh",
    "printer/lan_bridge.py": "/usr/local/bin/lan_bridge.py",
    "printer/lan_bridge.init.sh": "/etc/init.d/lan_bridge",
    "printer/mjpeg_server.py": "/usr/local/bin/mjpeg_server.py",
    "printer/mjpeg_server.init.sh": "/etc/init.d/mjpeg_server",
    "printer/restart_cam_stack.sh": "/usr/local/bin/restart_cam_stack.sh",
    "printer/go2rtc_init.sh": "/etc/init.d/go2rtc",
    "printer/cam_delivery_bridge.py": "/usr/local/bin/cam_delivery_bridge.py",
    "printer/creality.lan.locations.conf": "/etc/nginx/conf.d/creality.lan.locations.conf",
    "printer/creality.lan.websocket.conf": "/etc/nginx/conf.d/creality.lan.websocket.conf",
    "printer/status_page.py": "/usr/local/bin/status_page.py",
    "printer/status_page.init.sh": "/etc/init.d/status_page",
    "printer/creality_mdns_announcer.py": "/usr/local/bin/creality_mdns_announcer.py",
    "printer/creality_mdns.init.sh": "/etc/init.d/creality_mdns",
    "printer/webrtc_local_bridge.py": "/usr/local/bin/webrtc_local_bridge.py",
    "printer/webrtc_local_bridge.init.sh": "/etc/init.d/webrtc_local_bridge",
    "printer/webrtc_local_wrapper.sh": "/usr/bin/webrtc_local",
    "printer/webrtc.init.sh": "/etc/init.d/webrtc",
    "printer/watchdog.sh": "/usr/local/bin/watchdog.sh",
    "printer/watchdog.init.sh": "/etc/init.d/watchdog",
    "printer/nginx.frontdoor.conf": "/etc/nginx/nginx.conf",
    "printer/nginx.ecs-log-format.conf": "/etc/nginx/conf.d/ecs-log-format.conf",
}

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Diff:
    local_path: str
    remote_path: str
    local_digest: Optional[str]
    remote_digest: Optional[str]
    status: str  # "match", "missing_remote", "missing_local", "mismatch"


SSH_KEY = os.environ.get("SSH_KEY", "")


def _ssh_base() -> list[str]:
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
    ]
    if SSH_KEY:
        cmd.extend(["-o", f"IdentityFile={SSH_KEY}"])
    return cmd


def _run_ssh(host: str, user: str, cmd: str) -> subprocess.CompletedProcess:
    target = f"{user}@{host}"
    return subprocess.run(
        _ssh_base() + [target, cmd],
        capture_output=True,
        text=True,
    )


def _local_digest(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"ERROR reading {path}: {exc}", file=sys.stderr)
        return None


def _remote_digests(host: str, user: str, remote_paths: list[str]) -> dict[str, Optional[str]]:
    """Fetch sha256 digests for many remote paths in one SSH call."""
    # Build a shell script that prints path<SPACE>digest or path<SPACE>MISSING.
    quoted = " ".join(f"'{p}'" for p in remote_paths)
    cmd = (
        "for p in " + quoted + "; do "
        "if [ -f \"$p\" ]; then printf '%s %s\\n' \"$p\" \"$(sha256sum \"$p\" | awk '{print $1}')\"; "
        "else printf '%s MISSING\\n' \"$p\"; fi; "
        "done"
    )
    result = _run_ssh(host, user, cmd)
    if result.returncode != 0:
        print(f"ERROR: remote digest command failed: {result.stderr.strip()}", file=sys.stderr)
        return {p: None for p in remote_paths}

    out: dict[str, Optional[str]] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        path, digest = parts
        out[path] = None if digest == "MISSING" else digest
    return out


def _sync_file(host: str, user: str, local_path: str, remote_path: str) -> bool:
    target = f"{user}@{host}:{remote_path}"
    scp_cmd = [
        "scp",
        "-O",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
    ]
    if SSH_KEY:
        scp_cmd.extend(["-o", f"IdentityFile={SSH_KEY}"])
    scp_cmd.extend([local_path, target])
    result = subprocess.run(
        scp_cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAIL: scp {local_path} -> {target}: {result.stderr.strip()}")
        return False
    print(f"  synced {local_path} -> {remote_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local repo files with deployed printer files.")
    parser.add_argument("--host", default=os.environ.get("PRINTER_HOST", "192.168.1.100"), help="Printer IP or hostname")
    parser.add_argument("--user", default=os.environ.get("PRINTER_USER", "root"), help="SSH user")
    parser.add_argument("--sync", action="store_true", help="Push files that differ or are missing on the printer")
    parser.add_argument("--local-dir", default=ROOT_DIR, help="Base directory for local files (default: repo root)")
    args = parser.parse_args()

    local_dir = os.path.abspath(args.local_dir)

    remote_paths = list(FILE_MAP.values())
    remote_digests = _remote_digests(args.host, args.user, remote_paths)

    diffs: list[Diff] = []
    for rel_local, remote in FILE_MAP.items():
        local = os.path.join(local_dir, rel_local)
        local_d = _local_digest(local)
        remote_d = remote_digests.get(remote)

        if local_d is None:
            status = "missing_local"
        elif remote_d is None:
            status = "missing_remote"
        elif local_d != remote_d:
            status = "mismatch"
        else:
            status = "match"
        diffs.append(Diff(rel_local, remote, local_d, remote_d, status))

    mismatches = [d for d in diffs if d.status != "match"]
    if not mismatches:
        print(f"OK: all {len(diffs)} tracked files match on {args.host}")
        return 0

    print(f"DRIFT: {len(mismatches)} of {len(diffs)} tracked files differ on {args.host}")
    for d in mismatches:
        if d.status == "missing_local":
            print(f"  MISSING_LOCAL  {d.local_path} -> {d.remote_path}")
        elif d.status == "missing_remote":
            print(f"  MISSING_REMOTE {d.local_path} -> {d.remote_path}")
        elif d.status == "mismatch":
            print(f"  MISMATCH       {d.local_path} -> {d.remote_path}")

    if not args.sync:
        print("\nRun with --sync to push differing/missing files to the printer.")
        return 1

    print("\nSyncing...")
    ok = 0
    failed = 0
    skipped = 0
    for d in mismatches:
        if d.status == "missing_local":
            print(f"  SKIP: cannot sync missing local file {d.local_path}")
            skipped += 1
            continue
        if _sync_file(args.host, args.user, os.path.join(local_dir, d.local_path), d.remote_path):
            ok += 1
        else:
            failed += 1

    print(f"\nSync result: {ok} pushed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
