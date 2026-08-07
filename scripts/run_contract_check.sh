#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_SCRIPT="$ROOT_DIR/scripts/endpoint_contract_check.py"

if [[ ! -f "$PY_SCRIPT" ]]; then
  echo "Missing script: $PY_SCRIPT"
  exit 1
fi

HOST="${1:-${PRINTER_HOST:-192.168.1.100}}"
PORT="${2:-80}"

if [[ -z "$HOST" ]]; then
  echo "Usage: PRINTER_HOST=<host_or_ip> $0 [printer_host_or_ip] [port]"
  echo "Example: $0 192.168.1.100 80"
  exit 1
fi

python3 "$PY_SCRIPT" --host "$HOST" --port "$PORT"
