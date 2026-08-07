#!/bin/sh /etc/rc.common
# OpenWrt procd init for the RTSP-to-MJPEG camera server.
#
# NOTE: The camera stack is normally started by /etc/init.d/go2rtc, which
# already launches mjpeg_server as part of restart_cam_stack.sh. This init
# script is kept as a manual fallback. It refuses to start a second instance
# if one is already running, so enabling it by accident will not create
# duplicate processes.

START=99
STOP=10
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/mjpeg_server.py
LOG=/var/log/mjpeg_server.log

start_service() {
    if pgrep -f "/usr/local/bin/mjpeg_server.py" >/dev/null 2>&1; then
        logger -t mjpeg_server "already running (managed by go2rtc), skipping start"
        return 0
    fi
    procd_open_instance mjpeg_server
    procd_set_param env HOME=/root \
        PROJECT_NAME="%PROJECT_NAME%" \
        ECS_LOGGING="%ECS_LOGGING%" \
        MJPEG_BIND="${MJPEG_BIND:-127.0.0.1}" \
        MJPEG_PORT="${MJPEG_PORT:-8081}" \
        MJPEG_SOURCE="${MJPEG_SOURCE:-rtsp://127.0.0.1:8554/camera}" \
        MJPEG_WIDTH="${MJPEG_WIDTH:-1280}" \
        MJPEG_HEIGHT="${MJPEG_HEIGHT:-720}" \
        MJPEG_FPS="${MJPEG_FPS:-15}" \
        MJPEG_QUALITY="${MJPEG_QUALITY:-5}"
    procd_set_param command /usr/bin/python3 "$PROG"
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    for pid in $(pgrep -f "/usr/local/bin/mjpeg_server.py" 2>/dev/null || true); do
        kill -9 "$pid" 2>/dev/null || true
    done
}
