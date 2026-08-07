#!/bin/sh /etc/rc.common

START=98
STOP=10
USE_PROCD=1
DEPEND=fstab

# Single-source camera stack orchestrator.
#
# restart_cam_stack.sh performs the sequential startup and then execs go2rtc.
# Because it execs, procd tracks go2rtc directly and can respawn the whole
# stack if go2rtc crashes. A respawn re-runs the script, which cleans up stale
# siblings before starting fresh.
#
# Components managed by this service:
#   - cam_app: owns /dev/video0, publishes H264 via /tmp/delivery_socket100.
#   - cam_delivery_bridge.py: fans H264 to /tmp/uvc_fifo (cloud) and
#     /tmp/go2rtc_cam.fifo (LAN).
#   - mjpeg_server.py: RTSP -> MJPEG for Creality LAN app / Fluidd.
#   - /usr/bin/webrtc: cloud camera daemon (started via its stock procd init).
#   - go2rtc: LAN RTSP/WebRTC endpoint (this is the process procd tracks).

RESTART=/usr/local/bin/restart_cam_stack.sh

start_service() {
    if [ ! -x "$RESTART" ]; then
        logger -t go2rtc "ERROR: $RESTART missing"
        return 1
    fi
    procd_open_instance go2rtc
    # Do NOT background here. The script execs go2rtc, which becomes the
    # long-running process that procd supervises.
    procd_set_param command "$RESTART"
    procd_set_param stdout 1
    procd_set_param stderr 1
    # Respawn if go2rtc exits unexpectedly. 3600s timeout, 5s threshold, 5 retries.
    procd_set_param respawn 3600 5 5
    procd_close_instance
}

stop_service() {
    # Stop the cloud webrtc daemon first so it does not hold /tmp/uvc_fifo open.
    /etc/init.d/webrtc stop 2>/dev/null || true

    # Kill every process that belongs to the camera stack.
    # Do NOT kill webrtc_local / webrtc_local_bridge (Creality Print LAN camera on port 8000).
    # Do NOT killall python3 -- that would also kill moonraker.
    for name in cam_app cam_sub_app go2rtc ffmpeg webrtc; do
        # pgrep -x does not work reliably on this build; substring match is safe
        # for these short, unique process names.
        for pid in $(pgrep "$name" 2>/dev/null || true); do
            kill -9 "$pid" 2>/dev/null || true
        done
    done
    for pattern in "/usr/local/bin/cam_delivery_bridge.py" "/usr/local/bin/mjpeg_server.py" "/usr/bin/webrtc"; do
        for pid in $(ps w | grep -E "$pattern" | grep -v grep | awk '{print $1}'); do
            kill -9 "$pid" 2>/dev/null || true
        done
    done

    # Give processes a moment to release sockets/FIFOs.
    sleep 2
}
