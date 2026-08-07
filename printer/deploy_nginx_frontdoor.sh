#!/bin/sh
set -e

HOST=${1:-root@192.168.1.100}
CONF_SRC=printer/nginx.nrvous.conf
LOCATIONS_SRC=printer/creality.lan.locations.conf
WS_SRC=printer/creality.lan.websocket.conf
ECS_LOG_SRC=printer/nginx.ecs-log-format.conf
LAN_BRIDGE_INIT_SRC=printer/lan_bridge.init.sh
STATUS_INIT_SRC=printer/nrvous_status_page.init.sh

echo "==> Installing nginx front-door config to $HOST"
python3 scripts/pyput.py "$CONF_SRC" "${HOST}:/etc/nginx/nginx.conf"
python3 scripts/pyput.py "$LOCATIONS_SRC" "${HOST}:/etc/nginx/conf.d/creality.lan.locations.conf"
python3 scripts/pyput.py "$WS_SRC" "${HOST}:/etc/nginx/conf.d/creality.lan.websocket.conf"
python3 scripts/pyput.py "$ECS_LOG_SRC" "${HOST}:/etc/nginx/conf.d/ecs-log-format.conf"
python3 scripts/pyput.py "$LAN_BRIDGE_INIT_SRC" "${HOST}:/etc/init.d/lan_bridge"
python3 scripts/pyput.py "$STATUS_INIT_SRC" "${HOST}:/etc/init.d/nrvous_status_page"

ssh "$HOST" '
    mkdir -p /etc/nginx/conf.d
    if [ ! -f /etc/nginx/conf.d/nrvous.io.crt ] || [ ! -f /etc/nginx/conf.d/nrvous.io.key ]; then
        echo "ERROR: nrvous.io.crt or nrvous.io.key missing in /etc/nginx/conf.d/"
        exit 1
    fi

    nginx -t || { echo "nginx config test failed"; exit 1; }

    # Stop and disable the whole Creality app service bundle so web-server
    # releases 80/443 and does not reclaim them on reboot. This also stops
    # master/app/display/audio/wifi/upgrade servers.
    /etc/init.d/app stop 2>/dev/null || true
    /etc/init.d/app disable 2>/dev/null || true
    killall -9 web-server 2>/dev/null || true
    sleep 1

    /etc/init.d/nginx restart
    sleep 1

    # Ensure the LAN bridge and status page are installed, enabled, and running.
    # Disable the old probe backend if it is still present.
    chmod +x /etc/init.d/lan_bridge /etc/init.d/nrvous_status_page 2>/dev/null || true
    if [ -f /etc/init.d/probe_backend ]; then
        /etc/init.d/probe_backend stop 2>/dev/null || true
        /etc/init.d/probe_backend disable 2>/dev/null || true
    fi
    /etc/init.d/lan_bridge enable 2>/dev/null || true
    /etc/init.d/lan_bridge restart 2>/dev/null || true
    /etc/init.d/nrvous_status_page enable 2>/dev/null || true
    /etc/init.d/nrvous_status_page restart 2>/dev/null || true
    sleep 1

    echo "==> Listeners after switch:"
    netstat -tlnp 2>/dev/null | grep -E ":80 |:443 |:4408" || true

    echo "==> Test HTTP /call/webrtc_local:"
    export PATH="/usr/local/bin:$PATH"
    pyfetch http://127.0.0.1/call/webrtc_local 2>&1 | head -6 || true

    echo "==> Test HTTPS /call/webrtc_local:"
    pyfetch https://127.0.0.1/call/webrtc_local 2>&1 | head -6 || true
'

echo "==> Done"
