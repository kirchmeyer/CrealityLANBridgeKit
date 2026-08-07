#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-192.168.1.100}"
PORT="${PORT:-80}"
REMOTE_USER="${REMOTE_USER:-root}"

if [[ ! -x "$ROOT_DIR/printer/deploy_lan_bridge.sh" ]]; then
  echo "Missing deploy script: $ROOT_DIR/printer/deploy_lan_bridge.sh"
  exit 1
fi

if [[ ! -f "$ROOT_DIR/scripts/endpoint_contract_check.py" ]]; then
  echo "Missing contract check script: $ROOT_DIR/scripts/endpoint_contract_check.py"
  exit 1
fi

echo "[reapply] Deploying printer-side LAN bridge + camera stack to ${REMOTE_USER}@${HOST}"
"$ROOT_DIR/printer/deploy_lan_bridge.sh" "${REMOTE_USER}@${HOST}"

echo

echo "[reapply] Ensuring nginx HTTP server also listens on port 81 (Creality desktop fallback)"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" '
  if ! grep -q "listen 81;" /etc/nginx/nginx.conf; then
    sed -i "s/listen 80 default_server;/listen 80 default_server;\n        listen 81;/" /etc/nginx/nginx.conf
    echo "[reapply] Added listen 81; to /etc/nginx/nginx.conf"
  else
    echo "[reapply] listen 81; already present"
  fi
  nginx -t && (/etc/init.d/nginx reload || service nginx reload)
'

echo

echo "[reapply] Verifying printer-facing compatibility routes"
python3 "$ROOT_DIR/scripts/endpoint_contract_check.py" --host "$HOST" --port "$PORT" --skip-upload

echo

echo "[reapply] If the front-door nginx routes still return 502, run:"
echo "[reapply]   ssh ${REMOTE_USER}@${HOST} 'nginx -t && (service nginx reload || /etc/init.d/nginx reload || /etc/init.d/nginx restart)'"
echo

echo "[reapply] Printer-side stack deployment complete."
echo "[reapply] No client patching required for this recovery path."
