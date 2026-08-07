#!/bin/sh /etc/rc.common

START=96
STOP=14
USE_PROCD=1
DEPEND=fstab
PROG=/usr/local/bin/webrtc_local_bridge.py

_kill_bridge() {
    # Kill the Python bridge and any wrapper shells (sleeping or starting).
    for pid in $(pgrep -f "/usr/local/bin/webrtc_local_bridge.py" 2>/dev/null); do
        [ "$$" != "$pid" ] && kill -9 "$pid" 2>/dev/null || true
    done
    for pid in $(pgrep -f "/usr/bin/webrtc_local" 2>/dev/null); do
        [ "$$" != "$pid" ] && kill -9 "$pid" 2>/dev/null || true
    done
    # Wait for port 8000 to be released (ss may be missing on this build).
    for _ in $(seq 1 20); do
        if ! netstat -tln 2>/dev/null | grep -q ":8000 "; then
            break
        fi
        sleep 0.2
    done
}

start_service() {
    _kill_bridge
    # Install the wrapper that coordinates with the stock app-server so only
    # one bridge instance ever binds port 8000.
    if [ -f /usr/local/bin/webrtc_local_wrapper.sh ]; then
        cp -f /usr/local/bin/webrtc_local_wrapper.sh /usr/bin/webrtc_local
        chmod +x /usr/bin/webrtc_local
    fi
    procd_open_instance webrtc_local_bridge
    procd_set_param env PROJECT_NAME="%PROJECT_NAME%" ECS_LOGGING="%ECS_LOGGING%"
    procd_set_param command /usr/bin/webrtc_local
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn 3600 5 5
    procd_close_instance
}

stop_service() {
    _kill_bridge
}

service_triggers() {
    procd_add_reload_trigger "webrtc_local_bridge"
}
