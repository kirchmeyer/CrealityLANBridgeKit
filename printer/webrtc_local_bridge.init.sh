#!/bin/sh /etc/rc.common

START=96
STOP=14
USE_PROCD=1
DEPEND=fstab
PROG=/usr/local/bin/webrtc_local_bridge.py

start_service() {
    procd_open_instance webrtc_local_bridge
    procd_set_param command /usr/bin/python3 $PROG
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    killall -9 webrtc_local_bridge python3 2>/dev/null || true
}
