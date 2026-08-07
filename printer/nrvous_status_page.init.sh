#!/bin/sh /etc/rc.common
# OpenWrt procd init for the nrvous bridge status page.

START=97
STOP=11
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/nrvous_status_page.py

start_service() {
    procd_open_instance nrvous_status_page
    procd_set_param env HOME=/root NRVOUS_STATUS_BIND="127.0.0.1" NRVOUS_STATUS_PORT="8765"
    procd_set_param command /usr/bin/python3 "$PROG"
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    pkill -9 -f "python3 /usr/local/bin/nrvous_status_page.py" 2>/dev/null || true
}
