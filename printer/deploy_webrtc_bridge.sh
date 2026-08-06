#!/bin/sh
set -e

HOST=${1:-root@192.168.1.100}
BRIDGE_SRC=printer/webrtc_local_bridge.py
INIT_SRC=printer/webrtc_local_bridge.init.sh

echo "==> Installing webrtc_local_bridge to $HOST"
python3 scripts/pyput.py "$BRIDGE_SRC" "${HOST}:/usr/local/bin/webrtc_local_bridge.py"
python3 scripts/pyput.py "$INIT_SRC" "${HOST}:/etc/init.d/webrtc_local_bridge"

ssh "$HOST" '
    chmod +x /usr/local/bin/webrtc_local_bridge.py
    chmod +x /etc/init.d/webrtc_local_bridge
    /etc/init.d/webrtc_local_bridge enable
    /etc/init.d/webrtc_local_bridge stop 2>/dev/null || true
    # Make sure stock webrtc_local is gone so we can bind port 8000
    killall -9 webrtc_local 2>/dev/null || true
    sleep 1
    /etc/init.d/webrtc_local_bridge start
    sleep 1
    echo "==> Port 8000 listeners:"
    netstat -tlnp 2>/dev/null | grep ":8000 " || true
    echo "==> Bridge log tail:"
    tail -n 5 /tmp/webrtc_local_bridge.log 2>/dev/null || true
'

echo "==> Done"
