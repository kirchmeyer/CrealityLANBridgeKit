#!/bin/sh
# Custom watchdog for the nrvous.io Creality LAN bridge stack.
#
# Strategy:
#   - Use existing procd init scripts for restart; this watchdog only decides
#     WHEN a restart is needed.
#   - Check cheap signals first (process existence, listener ports), then
#     probe functional endpoints.
#   - Log every action and avoid noisy loops.

INTERVAL=${INTERVAL:-30}
LOG_TAG="nrvous_watchdog"
MAX_LOG_BYTES=${MAX_LOG_BYTES:-65536}

WATCHDOG_LOG=/var/log/nrvous_watchdog.log

log() {
    msg="$(date '+%Y-%m-%d %H:%M:%S') $*"
    logger -t "$LOG_TAG" "$*"
    echo "$msg" >> "$WATCHDOG_LOG"
    # Rotate tiny log in place to avoid unbounded growth.
    if [ -f "$WATCHDOG_LOG" ]; then
        sz=$(stat -c%s "$WATCHDOG_LOG" 2>/dev/null || echo 0)
        if [ "$sz" -gt "$MAX_LOG_BYTES" ]; then
            tail -c 16384 "$WATCHDOG_LOG" > "${WATCHDOG_LOG}.tmp" && mv "${WATCHDOG_LOG}.tmp" "$WATCHDOG_LOG"
        fi
    fi
}

is_running() {
    pgrep -f "$1" >/dev/null 2>&1
}

is_listening() {
    # BusyBox netstat -tln output format: tcp ... 0.0.0.0:PORT ...
    netstat -tln 2>/dev/null | grep -q ":$1 "
}

restart_service() {
    svc="$1"
    reason="$2"
    log "RESTART $svc: $reason"
    /etc/init.d/"$svc" restart >/dev/null 2>&1 || log "ERROR: $svc restart failed"
}

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
log "watchdog started (interval=${INTERVAL}s)"

while true; do
    # --- Camera stack ---------------------------------------------------------
    # go2rtc is the procd-tracked process for the whole camera stack. If it is
    # gone, ask procd to restart the stack instead of piecing siblings back
    # together ourselves.
    if ! is_running "/usr/bin/go2rtc"; then
        restart_service go2rtc "go2rtc not running"
    elif ! is_listening 8554 || ! is_listening 1984; then
        restart_service go2rtc "go2rtc ports not listening (8554/1984)"
    fi

    # mjpeg_server is started by restart_cam_stack.sh, not its own procd
    # instance, so procd won't notice if it dies.
    if ! is_running "/usr/local/bin/mjpeg_server.py"; then
        restart_service go2rtc "mjpeg_server not running"
    elif ! is_listening 8081; then
        restart_service go2rtc "mjpeg_server port 8081 not listening"
    fi

    # cam_delivery_bridge fans H264 from cam_app to both FIFOs.
    if ! is_running "/usr/local/bin/cam_delivery_bridge.py"; then
        restart_service go2rtc "cam_delivery_bridge not running"
    fi

    # cam_app owns /dev/video0 and creates /tmp/delivery_socket100.
    if ! is_running "/usr/bin/cam_app" || [ ! -S /tmp/delivery_socket100 ]; then
        restart_service go2rtc "cam_app missing or delivery socket gone"
    fi

    # --- LAN bridge -----------------------------------------------------------
    if ! is_running "/usr/local/bin/lan_bridge.py"; then
        restart_service lan_bridge "lan_bridge not running"
    elif ! is_listening 9002; then
        restart_service lan_bridge "lan_bridge port 9002 not listening"
    fi

    # --- WebRTC local bridge for Creality Print LAN camera --------------------
    if ! is_running "/usr/local/bin/webrtc_local_bridge.py"; then
        restart_service webrtc_local_bridge "webrtc_local_bridge not running"
    elif ! is_listening 8000; then
        restart_service webrtc_local_bridge "webrtc_local_bridge port 8000 not listening"
    fi

    # --- Status page ----------------------------------------------------------
    if ! is_running "/usr/local/bin/nrvous_status_page.py"; then
        restart_service nrvous_status_page "status page not running"
    elif ! is_listening 8765; then
        restart_service nrvous_status_page "status page port 8765 not listening"
    fi

    # --- nginx front door -----------------------------------------------------
    if ! is_running "nginx: master process"; then
        restart_service nginx "nginx master not running"
    elif ! is_listening 80; then
        restart_service nginx "nginx port 80 not listening"
    fi

    # --- Creality mDNS announcer ---------------------------------------------
    if ! is_running "/usr/local/bin/creality_mdns_announcer.py"; then
        restart_service creality_mdns "mDNS announcer not running"
    fi

    sleep "$INTERVAL"
done
