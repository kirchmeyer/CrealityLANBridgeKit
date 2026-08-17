#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=${1:-${PRINTER_USER:-root}@${PRINTER_HOST:-192.168.1.100}}

if [[ -z "${CERT_BASENAME:-}" ]]; then
    echo "ERROR: set CERT_BASENAME to the certificate basename installed on the printer" >&2
    exit 1
fi

if [[ "$TARGET" == *@* ]]; then
    REMOTE_USER=${TARGET%%@*}
    REMOTE_HOST=${TARGET#*@}
else
    REMOTE_USER=${PRINTER_USER:-root}
    REMOTE_HOST=$TARGET
fi

echo "==> Deploying rendered nginx front door to ${REMOTE_USER}@${REMOTE_HOST}"
"${ROOT_DIR}/install.sh" sync "$REMOTE_HOST" "$REMOTE_USER" \
    --cert-basename "$CERT_BASENAME" \
    --public-host "${PUBLIC_HOST:-printer.lan}" \
    --lan-mode "${LAN_MODE:-open}" \
    --ecs-logging "${ECS_LOGGING:-1}"
