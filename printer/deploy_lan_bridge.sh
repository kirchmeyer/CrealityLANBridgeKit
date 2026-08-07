#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRINTER_HOST="${1:-${PRINTER_HOST:-root@192.168.1.100}}"

echo "Deploying LAN bridge + camera stack to ${PRINTER_HOST}..."

scp -O "${SCRIPT_DIR}/lan_bridge.py" "${PRINTER_HOST}:/tmp/lan_bridge.py"
scp -O "${SCRIPT_DIR}/lan_bridge.init.sh" "${PRINTER_HOST}:/tmp/lan_bridge.init.sh"
scp -O "${SCRIPT_DIR}/mjpeg_server.py" "${PRINTER_HOST}:/tmp/mjpeg_server.py"
scp -O "${SCRIPT_DIR}/mjpeg_server.init.sh" "${PRINTER_HOST}:/tmp/mjpeg_server.init.sh"
scp -O "${SCRIPT_DIR}/restart_cam_stack.sh" "${PRINTER_HOST}:/tmp/restart_cam_stack.sh"
scp -O "${SCRIPT_DIR}/go2rtc_init.sh" "${PRINTER_HOST}:/tmp/go2rtc_init.sh"
scp -O "${SCRIPT_DIR}/cam_delivery_bridge.py" "${PRINTER_HOST}:/tmp/cam_delivery_bridge.py"
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
mv /tmp/restart_cam_stack.sh /usr/local/bin/restart_cam_stack.sh
chmod +x /usr/local/bin/restart_cam_stack.sh
mv /tmp/go2rtc_init.sh /etc/init.d/go2rtc
chmod +x /etc/init.d/go2rtc
mv /tmp/cam_delivery_bridge.py /usr/local/bin/cam_delivery_bridge.py
chmod +x /usr/local/bin/cam_delivery_bridge.py
mv /tmp/creality.lan.locations.conf /etc/nginx/conf.d/creality.lan.locations.conf
mv /tmp/creality.lan.websocket.conf /etc/nginx/conf.d/creality.lan.websocket.conf

# Switch nginx include if still pointing at the old compat file.
sed -i "s|creality.compat.locations.conf|creality.lan.locations.conf|g" /etc/nginx/nginx.conf
# Ensure the dedicated WebSocket port (9999) config is loaded.
grep -q "creality.lan.websocket.conf" /etc/nginx/nginx.conf || sed -i "s|include /etc/nginx/conf.d/ecs-log-format.conf;|include /etc/nginx/conf.d/ecs-log-format.conf;\n    include /etc/nginx/conf.d/creality.lan.websocket.conf;|" /etc/nginx/nginx.conf

# Disable old backend if present and remove its files so it cannot accidentally
# be re-enabled or restarted by other scripts.
if [ -f /etc/init.d/probe_backend ]; then
    /etc/init.d/probe_backend stop 2>/dev/null || true
    /etc/init.d/probe_backend disable 2>/dev/null || true
    rm -f /etc/init.d/probe_backend /usr/local/bin/creality_probe_backend.py \
          /etc/nginx/conf.d/creality.compat.locations.conf
fi
# Remove obsolete cloud-bridge artifacts that have been replaced by
# cam_delivery_bridge.py.
rm -f /usr/local/bin/cloud_webrtc_bridge.py /usr/local/bin/cloud_webrtc_feeder.sh \
      /usr/local/bin/uvc_ffmpeg_feeder.sh /etc/init.d/webrtc_bridge 2>/dev/null || true

# mjpeg_server and webrtc are now managed by the go2rtc wrapper; do not start
# them separately at boot to avoid duplicate or racy camera processes.
if [ -f /etc/init.d/mjpeg_server ]; then
    /etc/init.d/mjpeg_server stop 2>/dev/null || true
    /etc/init.d/mjpeg_server disable 2>/dev/null || true
fi
if [ -f /etc/init.d/webrtc ]; then
    /etc/init.d/webrtc disable 2>/dev/null || true
fi

/etc/init.d/lan_bridge enable
/etc/init.d/lan_bridge restart
/etc/init.d/go2rtc enable
/etc/init.d/go2rtc restart

nginx -t && nginx -s reload

echo "lan_bridge and camera stack deployed and running"
'

echo "Done."
