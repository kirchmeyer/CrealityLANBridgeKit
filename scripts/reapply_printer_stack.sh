#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-192.168.1.100}"
PORT="${PORT:-80}"
REMOTE_USER="${REMOTE_USER:-root}"

if [[ ! -x "$ROOT_DIR/printer/deploy_probe_backend.sh" ]]; then
  echo "Missing deploy script: $ROOT_DIR/printer/deploy_probe_backend.sh"
  exit 1
fi

if [[ ! -f "$ROOT_DIR/scripts/endpoint_contract_check.py" ]]; then
  echo "Missing contract check script: $ROOT_DIR/scripts/endpoint_contract_check.py"
  exit 1
fi

echo "[reapply] Deploying printer-side compatibility stack to ${REMOTE_USER}@${HOST}"
HOST="$HOST" REMOTE_USER="$REMOTE_USER" "$ROOT_DIR/printer/deploy_probe_backend.sh"

echo

echo "[reapply] Verifying printer-facing compatibility routes"
python3 "$ROOT_DIR/scripts/endpoint_contract_check.py" --host "$HOST" --port "$PORT" --skip-upload

echo

echo "[reapply] If the front-door nginx routes still return 502, run:"
echo "[reapply]   ssh ${REMOTE_USER}@${HOST} 'nginx -t && (service nginx reload || /etc/init.d/nginx reload || /etc/init.d/nginx restart)'"
echo

echo "[reapply] Printer-side stack deployment complete."
echo "[reapply] No client patching required for this recovery path."
