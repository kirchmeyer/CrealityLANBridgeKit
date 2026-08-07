#!/bin/sh /etc/rc.common
# OpenWrt procd init for the bridge status page.

START=97
STOP=11
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/status_page.py

start_service() {
    procd_open_instance status_page
    procd_set_param env HOME=/root PROJECT_NAME="%PROJECT_NAME%" STATUS_PATH="%STATUS_PATH%" STATUS_BIND="127.0.0.1" STATUS_PORT="8765"
    procd_set_param command /usr/bin/python3 "$PROG"
    procd_set_param stdout 1
    procd_set_param stderr 1
    # Bounded respawn: if the page crashes repeatedly, stop flapping.
    procd_set_param respawn
    procd_set_param respawn_threshold 3600
    procd_set_param respawn_timeout 5
    procd_set_param respawn_retry 3
    procd_close_instance
}

stop_service() {
    for pid in $(pgrep -f "python3 /usr/local/bin/status_page.py" 2>/dev/null || true); do
        [ "$$" != "$pid" ] && kill -9 "$pid" 2>/dev/null || true
    done
}
