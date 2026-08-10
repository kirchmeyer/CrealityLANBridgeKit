#!/bin/sh
# Custom watchdog for the Creality LAN bridge stack.
#
# Strategy:
#   - Use existing procd init scripts for restart; this watchdog only decides
#     WHEN a restart is needed.
#   - Check cheap signals first (process existence, listener ports), then
#     probe functional endpoints.
#   - Log every action and avoid noisy loops.

INTERVAL=${INTERVAL:-30}
PROJECT_NAME=${PROJECT_NAME:-bridge}
LOG_TAG="${PROJECT_NAME}_watchdog"
MAX_LOG_LINES=${MAX_LOG_LINES:-500}

WATCHDOG_LOG=/var/log/${PROJECT_NAME}_watchdog.log
ECS_VERSION="8.11.0"
SERVICE_NAME="${PROJECT_NAME}-watchdog"
SERVICE_VERSION="1.0.0"
ECS_LOGGING=${ECS_LOGGING:-1}

# Minimal JSON string escape for log messages that only contain safe chars.
# Backslash and double-quote are escaped just in case.
json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# Emit a millisecond-precision UTC timestamp.
_utc_timestamp() {
    python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")'
}

# Emit a log line to file and to syslog.
# Usage: log <level> <message>
log() {
    level="$1"
    shift
    msg="$*"
    if [ "$ECS_LOGGING" = "1" ]; then
        ts=$(_utc_timestamp)
        host=$(hostname)
        pid=$$
        escaped=$(json_escape "$msg")
        line=$(printf '{"@timestamp":"%s","ecs.version":"%s","log.level":"%s","message":"%s","event.dataset":"%s.log","service.name":"%s","service.version":"%s","host.name":"%s","process.pid":%s}' \
            "$ts" "$ECS_VERSION" "$level" "$escaped" "$SERVICE_NAME" "$SERVICE_NAME" "$SERVICE_VERSION" "$host" "$pid")
    else
        line="$(date '+%Y-%m-%d %H:%M:%S') [$level] $msg"
    fi
    logger -t "$LOG_TAG" "$line"
    echo "$line" >> "$WATCHDOG_LOG"
    # Rotate log in place to keep the most recent complete lines.
    if [ -f "$WATCHDOG_LOG" ]; then
        lines=$(wc -l < "$WATCHDOG_LOG" 2>/dev/null || echo 0)
        if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
            tail -n "$MAX_LOG_LINES" "$WATCHDOG_LOG" > "${WATCHDOG_LOG}.tmp" && mv "${WATCHDOG_LOG}.tmp" "$WATCHDOG_LOG"
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
    log info "RESTART $svc: $reason"
    /etc/init.d/"$svc" restart >/dev/null 2>&1 || log error "restart failed: $svc"
}

recover_nginx_config() {
    recovery_config="/etc/${PROJECT_NAME}/recovery/nginx.conf"
    [ -s "$recovery_config" ] || return 1

    if grep -q "# LAN bridge front-door config" /etc/nginx/nginx.conf 2>/dev/null \
        && nginx -t >/dev/null 2>&1; then
        return 0
    fi

    cp -f "$recovery_config" /etc/nginx/nginx.conf
    if nginx -t >/dev/null 2>&1; then
        log warning "RESTORE nginx: recovered validated front-door config"
        return 0
    fi

    log error "restore failed: recovered nginx config is invalid"
    return 1
}

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
log info "watchdog started (interval=${INTERVAL}s)"
sleep "$INTERVAL"

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
    if ! is_running "/usr/local/bin/status_page.py"; then
        restart_service status_page "status page not running"
    elif ! is_listening 8765; then
        restart_service status_page "status page port 8765 not listening"
    fi

    # --- nginx front door -----------------------------------------------------
    # Firmware upgrades can restart Monitor, which in turn respawns the stock
    # web-server on nginx's ports.
    if is_running "/usr/bin/Monitor" || is_running "/usr/bin/web-server"; then
        log warning "STOP stock front door: Monitor or web-server is running"
        /etc/init.d/app disable >/dev/null 2>&1 || true
        killall -9 Monitor >/dev/null 2>&1 || true
        killall -9 web-server >/dev/null 2>&1 || true
    fi

    if ! is_running "nginx: master process"; then
        if recover_nginx_config; then
            restart_service nginx "nginx master not running"
        fi
    elif ! is_listening 80; then
        restart_service nginx "nginx port 80 not listening"
    fi

    # --- Creality mDNS announcer ---------------------------------------------
    if ! is_running "/usr/local/bin/creality_mdns_announcer.py"; then
        restart_service creality_mdns "mDNS announcer not running"
    fi

    sleep "$INTERVAL"
done
