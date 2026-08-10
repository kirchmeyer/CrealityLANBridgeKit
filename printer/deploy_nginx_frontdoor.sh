#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=${1:-${PRINTER_USER:-root}@${PRINTER_HOST:-192.168.1.100}}

if [[ "$TARGET" == *@* ]]; then
    REMOTE_USER=${TARGET%%@*}
    REMOTE_HOST=${TARGET#*@}
else
    REMOTE_USER=${PRINTER_USER:-root}
    REMOTE_HOST=$TARGET
fi

echo "==> Deploying rendered nginx front door to ${REMOTE_USER}@${REMOTE_HOST}"
"${ROOT_DIR}/install.sh" sync "$REMOTE_HOST" "$REMOTE_USER"
