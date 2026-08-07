#!/usr/bin/env bash
set -euo pipefail
#
# Deploy the raw-socket Creality mDNS announcer and its init script.
# This announcer does NOT conflict with nginx; it only uses UDP port 5353
# and advertises the HTTP service on port 80 (or MDNS_SERVICE_PORT).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ANNOUNCER="$ROOT_DIR/printer/creality_mdns_announcer.py"
SOURCE_INIT="$ROOT_DIR/printer/creality_mdns.init.sh"

if [[ ! -f "$SOURCE_ANNOUNCER" ]]; then
    echo "Missing source announcer: $SOURCE_ANNOUNCER"
    exit 1
fi

if [[ ! -f "$SOURCE_INIT" ]]; then
    echo "Missing source init: $SOURCE_INIT"
    exit 1
fi

HOST="${HOST:-${PRINTER_HOST:-192.168.1.100}}"
REMOTE_USER="${REMOTE_USER:-${PRINTER_USER:-root}}"
REMOTE_ANNOUNCER="/usr/local/bin/creality_mdns_announcer.py"
REMOTE_INIT="/etc/init.d/creality_mdns"

echo "==> Deploying mDNS announcer..."
cat "$SOURCE_ANNOUNCER" | ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "cat > ${REMOTE_ANNOUNCER}"
cat "$SOURCE_INIT"      | ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "cat > ${REMOTE_INIT}"

ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "
    chmod +x ${REMOTE_INIT} ${REMOTE_ANNOUNCER}
    ${REMOTE_INIT} enable
    ${REMOTE_INIT} restart
    sleep 1
    pgrep -a -f creality_mdns_announcer.py || echo 'WARNING: announcer not running'
"

echo "==> mDNS announcer deployed."
echo ""
echo "Verify with:"
echo "  ssh ${REMOTE_USER}@${HOST} 'pgrep -a -f creality_mdns_announcer.py'"
echo "  dns-sd -B '_Creality-\$(ssh ${REMOTE_USER}@${HOST} keybox -r sn | sed \"s/.*= //\")._udp'"
