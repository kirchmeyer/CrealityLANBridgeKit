#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRINTER_HOST="${1:-root@192.168.1.100}"

echo "Deploying minimal LAN bridge to ${PRINTER_HOST}..."

scp -O "${SCRIPT_DIR}/lan_bridge.py" "${PRINTER_HOST}:/tmp/lan_bridge.py"
scp -O "${SCRIPT_DIR}/lan_bridge.init.sh" "${PRINTER_HOST}:/tmp/lan_bridge.init.sh"
scp -O "${SCRIPT_DIR}/mjpeg_server.py" "${PRINTER_HOST}:/tmp/mjpeg_server.py"
scp -O "${SCRIPT_DIR}/mjpeg_server.init.sh" "${PRINTER_HOST}:/tmp/mjpeg_server.init.sh"
scp -O "${SCRIPT_DIR}/creality.lan.locations.conf" "${PRINTER_HOST}:/tmp/creality.lan.locations.conf"
scp -O "${SCRIPT_DIR}/creality.lan.websocket.conf" "${PRINTER_HOST}:/tmp/creality.lan.websocket.conf"

ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${PRINTER_HOST}" '
set -e
mv /tmp/lan_bridge.py /usr/local/bin/lan_bridge.py
chmod +x /usr/local/bin/lan_bridge.py
mv /tmp/lan_bridge.init.sh /etc/init.d/lan_bridge
chmod +x /etc/init.d/lan_bridge
mv /tmp/mjpeg_server.py /usr/local/bin/mjpeg_server.py
chmod +x /usr/local/bin/mjpeg_server.py
mv /tmp/mjpeg_server.init.sh /etc/init.d/mjpeg_server
chmod +x /etc/init.d/mjpeg_server
mv /tmp/creality.lan.locations.conf /etc/nginx/conf.d/creality.lan.locations.conf
mv /tmp/creality.lan.websocket.conf /etc/nginx/conf.d/creality.lan.websocket.conf

# Switch nginx include if still pointing at the old compat file.
sed -i "s|creality.compat.locations.conf|creality.lan.locations.conf|g" /etc/nginx/nginx.conf
# Ensure the dedicated WebSocket port (9999) config is loaded.
grep -q "creality.lan.websocket.conf" /etc/nginx/nginx.conf || sed -i "s|include /etc/nginx/conf.d/ecs-log-format.conf;|include /etc/nginx/conf.d/ecs-log-format.conf;\n    include /etc/nginx/conf.d/creality.lan.websocket.conf;|" /etc/nginx/nginx.conf

# Disable old backend if present.
if [ -f /etc/init.d/probe_backend ]; then
    /etc/init.d/probe_backend stop 2>/dev/null || true
    /etc/init.d/probe_backend disable 2>/dev/null || true
fi

/etc/init.d/lan_bridge enable
/etc/init.d/lan_bridge restart
/etc/init.d/mjpeg_server enable
/etc/init.d/mjpeg_server restart

nginx -t && nginx -s reload

echo "lan_bridge and mjpeg_server deployed and running"
'

echo "Done."
