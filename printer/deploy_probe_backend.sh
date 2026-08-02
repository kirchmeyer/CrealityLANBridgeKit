#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_SCRIPT="$ROOT_DIR/printer/creality_probe_backend.py"
SOURCE_NGINX_CONF="$ROOT_DIR/printer/nginx.compat.example.conf"

if [[ ! -f "$SOURCE_SCRIPT" ]]; then
    echo "Missing source backend: $SOURCE_SCRIPT"
    exit 1
fi

if [[ ! -f "$SOURCE_NGINX_CONF" ]]; then
    echo "Missing nginx config: $SOURCE_NGINX_CONF"
    exit 1
fi

HOST="${HOST:-192.168.1.100}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_SCRIPT="/usr/local/bin/creality_probe_backend.py"
REMOTE_INIT="/etc/init.d/probe_backend"
REMOTE_LOG="/var/log/creality_probe_backend.log"
REMOTE_NGINX_CONF="/etc/nginx/conf.d/creality.compat.conf"
REMOTE_NGINX_INCLUDE="/etc/nginx/nginx.conf"
TMP_INIT="$(mktemp)"
trap 'rm -f "$TMP_INIT"' EXIT

cat > "$TMP_INIT" <<'EOF'
#!/bin/sh
start() {
    if [ -f /usr/local/bin/creality_probe_backend.py ]; then
        if ps | grep -q '[p]ython3 /usr/local/bin/creality_probe_backend.py'; then
            ps | grep '[p]ython3 /usr/local/bin/creality_probe_backend.py' | awk '{print $1}' | xargs -r kill 2>/dev/null || true
        fi
        if command -v setsid >/dev/null 2>&1; then
            setsid sh -c 'exec python3 /usr/local/bin/creality_probe_backend.py >/var/log/creality_probe_backend.log 2>&1' >/dev/null 2>&1 < /dev/null &
        else
            python3 /usr/local/bin/creality_probe_backend.py >/var/log/creality_probe_backend.log 2>&1 &
        fi
    fi
}

stop() {
    ps | grep '[p]ython3 /usr/local/bin/creality_probe_backend.py' | awk '{print $1}' | xargs -r kill 2>/dev/null || true
}

case "$1" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  *) echo "Usage: $0 {start|stop|restart}"; exit 1 ;;
esac
EOF

ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "mkdir -p /usr/local/bin /etc/init.d /etc/nginx/conf.d"
# Some printer images do not include sftp-server, so stream via ssh instead of scp.
cat "$SOURCE_SCRIPT" | ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "cat > ${REMOTE_SCRIPT}"
cat "$SOURCE_NGINX_CONF" | ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "cat > ${REMOTE_NGINX_CONF}"
cat "$TMP_INIT" | ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "cat > ${REMOTE_INIT}"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "chmod +x ${REMOTE_INIT} ${REMOTE_SCRIPT} && PUBLIC_HOST='${PUBLIC_HOST:-3d.nrvous.io}' PUBLIC_SCHEME='${PUBLIC_SCHEME:-https}' EXTRA_PORTS='8000' ${REMOTE_INIT} restart"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "python3 - <<'PY'
from pathlib import Path
path = Path('/etc/nginx/nginx.conf')
path.write_text('''worker_processes auto;\nevents { worker_connections 1024; }\nhttp {\n    include /etc/nginx/conf.d/*.conf;\n    include mime.types;\n    default_type application/octet-stream;\n    sendfile on;\n    keepalive_timeout 65;\n}\n''')
PY"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${REMOTE_USER}@${HOST}" "(nginx -t 2>/dev/null || true) && (service nginx reload 2>/dev/null || /etc/init.d/nginx reload 2>/dev/null || /etc/init.d/nginx restart 2>/dev/null || true)"