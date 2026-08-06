#!/bin/sh /etc/rc.common
# OpenWrt procd init for the RTSP-to-MJPEG camera server.
# Runs after go2rtc (START=98) so the RTSP source is available.

START=99
STOP=10
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/mjpeg_server.py
LOG=/var/log/mjpeg_server.log

start_service() {
    procd_open_instance mjpeg_server
    procd_set_param env HOME=/root \
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
    ps | awk '/python3 \/usr\/local\/bin\/mjpeg_server.py/{print $1}' | xargs -r kill -9 2>/dev/null || true
}
