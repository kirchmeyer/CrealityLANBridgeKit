#!/usr/bin/env python3
"""Deploy and start the mDNS announcer on the printer.

Replaces the broken shell version to avoid macOS bash 3.2 heredoc/quoting issues.
Per documented lessons: SSH directly to printer, use Python there to generate files.
"""

import argparse
import os
import re
import subprocess
import sys
import time


def run(cmd, **kwargs):
    """Run a command and return the result."""
    # Always use list form of subprocess - no shell quoting issues
    check = kwargs.pop("check", True)
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)
    if not check and r.returncode != 0:
        return False, r
    return r


def detect_port(host: str, user: str) -> int:
    """Detect the mDNS port from printer's nginx config."""
    # grep across all nginx configs, get first listen line
    result = run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"{user}@{host}",
         "grep -m1 'listen' /etc/nginx/*.conf 2>/dev/null | head -1 || true"],
        check=False,
    )
    if not result:
        return 4408

    listen_line = result.stdout.strip()
    match = re.search(r"listen\s+(\d+)", listen_line)
    if match:
        return int(match.group(1))
    return 4408


def create_init_content(port: int) -> str:
    """Generate the /etc/init.d/creality_mdns init script content.

    The target printer runs OpenWrt with procd (not sysvinit).
    BusyBox on this device has NO setsid, no nohup — must use procd semantics.
    """
    return f"""#!/bin/sh /etc/rc.common
USE_PROCD=1
START=90
STOP=15

start_service() {{
    procd_open_instance
    procd_set_param command /usr/bin/python3 /usr/local/bin/creality_mdns_announcer.py
    procd_set_param env MDNS_PORT={port}
    procd_set_param respawn
    procd_close_instance
}}

stop_service() {{
    procd_kill $(pidof python3) TERM >/dev/null 2>&1 || true
}}
"""


def deploy(host: str, user: str, port: int) -> bool:
    """Deploy mDNS announcer to the printer."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    source_script = os.path.join(root_dir, "creality_mdns_announcer.py")

    if not os.path.exists(source_script):
        print(f"Error: Source script not found: {source_script}")
        return False

    remote_script = "/usr/local/bin/creality_mdns_announcer.py"
    remote_init = "/etc/init.d/creality_mdns"
    remote_log = "/var/log/creality_mdns_announcer.log"

    print(f"Deploying mDNS announcer to {user}@{host}:{port}")

    # 1. Create directories
    run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         "mkdir -p /usr/local/bin /etc/init.d"],
        check=False,
    )

    # 2. Deploy the mDNS announcer Python script (as stdin to remote cat)
    with open(source_script, "r") as f:
        source_content = f.read()

    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         f"cat > {remote_script}"],
        input=source_content, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        print(f"Error deploying mDNS announcer: {proc.stderr}")
        return False

    # 3. Write init script via ssh (stdin to remote cat)
    init_content = create_init_content(port)
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         f"cat > {remote_init}"],
        input=init_content, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        print(f"Error writing init script: {proc.stderr}")
        return False

    # 4. Make executable
    run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         f"chmod +x {remote_init} {remote_script}"],
        check=False,
    )

    # 5. Start the service
    print("Starting mDNS announcer...")
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         f"/{remote_init.lstrip('/')} start"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        print(f"Warning: init script returned {proc.returncode}: {proc.stderr}")

    # 6. Verify it's running
    time.sleep(2)
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"{user}@{host}",
         "ps | grep -q '[c]reality_mdns_announcer.py'"],
        capture_output=True, text=True, check=False,
    )

    if proc.returncode == 0:
        print(f"\nmDNS announcer is running on port {port}")
        print(f"Check status: ssh {user}@{host} '/etc/init.d/creality_mdns start'")
        print(f"Logs: ssh {user}@{host} 'tail -f {remote_log}'")
        return True
    else:
        print(f"\nWARNING: mDNS announcer may not have started. Check logs:")
        print(f"  ssh {user}@{host} tail {remote_log}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Deploy mDNS announcer to the printer")
    parser.add_argument("--host", default=os.environ.get("PRINTER_HOST", "192.168.1.100"),
                        help="Printer hostname/IP (default: $PRINTER_HOST or 192.168.1.100)")
    parser.add_argument("--port", type=int, default=None,
                        help="mDNS port (auto-detected from printer if not specified)")
    args = parser.parse_args()

    host = args.host
    user = os.environ.get("REMOTE_USER", "root")

    port = args.port if args.port else detect_port(host, user)

    success = deploy(host, user, port)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

