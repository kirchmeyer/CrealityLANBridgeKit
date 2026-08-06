#!/bin/sh /etc/rc.common
# OpenWrt procd init for the minimal Creality LAN bridge.

START=96
STOP=10
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/lan_bridge.py
LOG=/var/log/lan_bridge.log

start_service() {
    procd_open_instance lan_bridge
    procd_set_param env HOME=/root PUBLIC_HOST="${PUBLIC_HOST:-3d.nrvous.io}" PUBLIC_SCHEME="${PUBLIC_SCHEME:-http}" MOONRAKER_URL="${MOONRAKER_URL:-http://127.0.0.1:7125}" CFS_FLATTEN="${CFS_FLATTEN:-0}"
    procd_set_param command /usr/bin/python3 "$PROG"
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    pkill -9 -f "python3 /usr/local/bin/lan_bridge.py" 2>/dev/null || true
}
