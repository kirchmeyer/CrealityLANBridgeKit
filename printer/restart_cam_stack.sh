#!/bin/sh
set -u
log() { echo "[$(date +%H:%M:%S)] $*"; }

# Avoid flock entirely: on this system a stale lock held by a crashed/zombified
# parent can never be released, causing every subsequent restart to hang. Use a
# simple exclusive PID marker instead. If an old marker exists but its PID is
# gone, we take over. We still stop any existing camera processes before
# starting new ones, so overlap is harmless.
LOCKFILE=/tmp/restart_cam_stack.pid
cleanup_lock() { rm -f "$LOCKFILE"; }
trap cleanup_lock EXIT
if [ -f "$LOCKFILE" ]; then
    oldpid=$(cat "$LOCKFILE" 2>/dev/null) || oldpid=""
    if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
        log "another restart (pid=$oldpid) is active; waiting for it"
        for _ in $(seq 1 60); do
            if ! kill -0 "$oldpid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$oldpid" 2>/dev/null; then
            log "ERROR: timeout waiting for pid $oldpid"
            exit 1
        fi
    fi
fi
echo $$ > "$LOCKFILE"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_wait_for_exit() {
    # Wait up to $2 seconds for all PIDs in $1 to disappear.
    local pids="$1"
    local timeout="${2:-10}"
    local i pid still_alive
    for i in $(seq 1 "$timeout"); do
        still_alive=""
        for pid in $pids; do
            if kill -0 "$pid" 2>/dev/null; then
                still_alive="$still_alive $pid"
            fi
        done
        # Trim leading space and test emptiness.
        case "$still_alive" in
            ""|" ") return 0 ;;
        esac
        sleep 1
    done
    return 1
}

_kill_pids() {
    local pids="$1"
    local timeout="${2:-5}"
    local pid
    for pid in $pids; do
        kill -9 "$pid" 2>/dev/null || true
    done
    _wait_for_exit "$pids" "$timeout" || true
}

_kill_by_pattern() {
    local pattern="$1"
    local pids=""
    local pid
    for pid in $(ps w | grep -E "$pattern" | grep -v grep | awk '{print $1}'); do
        pids="$pids $pid"
    done
    if [ -n "${pids# }" ]; then
        _kill_pids "$pids" 5
    fi
}

_kill_by_name() {
    local name="$1"
    local pids=""
    local pid
    # Do not use pgrep -x: on this OpenWrt build it sees the full path
    # (/usr/bin/go2rtc) and exact matching fails.
    for pid in $(pgrep "$name" 2>/dev/null || true); do
        pids="$pids $pid"
    done
    if [ -n "${pids# }" ]; then
        _kill_pids "$pids" 5
    fi
}

# ---------------------------------------------------------------------------
# Stop / clean
# ---------------------------------------------------------------------------

log "stop stock cloud webrtc service"
/etc/init.d/webrtc stop 2>/dev/null || true

log "kill camera processes"
# Do NOT kill webrtc_local / webrtc_local_bridge (Creality Print LAN camera on port 8000).
# Do NOT killall python3 -- that would also kill moonraker.
# Kill by name first, then by full command pattern, and wait for them to die.
for name in cam_app cam_sub_app go2rtc ffmpeg webrtc; do
    _kill_by_name "$name" || true
done
for pattern in "/usr/local/bin/cam_delivery_bridge.py" "/usr/bin/go2rtc" "/usr/local/bin/mjpeg_server.py" "/usr/bin/webrtc"; do
    _kill_by_pattern "$pattern" || true
done

# Remove stale IPC. Do NOT remove /tmp/uvc_fifo here: the stock Monitor watchdog
# may have started /usr/bin/webrtc before this script runs. Deleting the FIFO
# while webrtc holds it open leaves webrtc with a stale "(deleted)" fd. The
# bridge will create the FIFO if missing, or open the existing one.
rm -f /tmp/delivery_socket100 /tmp/go2rtc_cam.fifo
rm -f /var/run/cam_delivery_bridge.pid /var/run/main-video0.pid /var/run/main-video0_webrtc_local.pid
rm -f /tmp/lock/procd_go2rtc.lock

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

log "start cam_app"
/usr/bin/cam_app -i /dev/video0 -t main_cam >/tmp/cam_app_solo.log 2>&1 &
CAM_PID=$!

# Wait for delivery socket with a timeout.
for _ in $(seq 1 20); do
    if [ -S /tmp/delivery_socket100 ]; then
        break
    fi
    sleep 0.5
done
if ! [ -S /tmp/delivery_socket100 ]; then
    log "ERROR: delivery socket not created"
    cat /tmp/cam_app_solo.log
    exit 1
fi
log "cam_app pid=$CAM_PID socket ok"

log "start delivery bridge (cloud + LAN FIFOs)"
/usr/bin/python3 /usr/local/bin/cam_delivery_bridge.py >/tmp/cam_delivery_bridge.log 2>&1 &
BRIDGE_PID=$!

# Give the bridge a moment to create both FIFOs.
for _ in $(seq 1 10); do
    if [ -p /tmp/go2rtc_cam.fifo ] && [ -p /tmp/uvc_fifo ]; then
        break
    fi
    sleep 0.5
done
if ! [ -p /tmp/go2rtc_cam.fifo ]; then
    log "ERROR: LAN FIFO not created"
    exit 1
fi
if ! [ -p /tmp/uvc_fifo ]; then
    log "ERROR: cloud FIFO not created"
    exit 1
fi
log "delivery bridge pid=$BRIDGE_PID fifos ok"

log "start mjpeg_server"
/usr/bin/python3 /usr/local/bin/mjpeg_server.py >/tmp/mjpeg_server_solo.log 2>&1 &
MJPEG_PID=$!
sleep 1
if ! kill -0 "$MJPEG_PID" 2>/dev/null; then
    log "ERROR: mjpeg_server exited early"
    cat /tmp/mjpeg_server_solo.log
    exit 1
fi
log "mjpeg_server pid=$MJPEG_PID"

log "start stock cloud webrtc via procd"
# Keep the stock init disabled at boot so our go2rtc init controls startup order,
# but start it now so app-server can signal it when a cloud camera is requested.
/etc/init.d/webrtc start || true

log "exec go2rtc (procd will track this process)"
# Replace this script with go2rtc so procd sees the real daemon and can respawn
# it if it crashes. A respawn will re-run this whole script, giving us a clean
# restart.
exec /usr/bin/go2rtc -config /etc/go2rtc.yaml >/tmp/go2rtc_solo.log 2>&1
