#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-${PRINTER_HOST:-192.168.1.100}}"
REMOTE_USER="${REMOTE_USER:-${PRINTER_USER:-root}}"

ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" '
set -e
ps | grep -E "go2rtc|stream" | grep -v grep | awk "{print \$1}" | xargs -r kill 2>/dev/null || true
rm -f /tmp/go2rtc* 2>/dev/null || true
'

echo "Stream/proxy state reset requested on ${REMOTE_USER}@${HOST}."
