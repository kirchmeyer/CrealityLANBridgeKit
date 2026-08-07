#!/bin/sh
set -e

HOST=${1:-root@192.168.1.100}

echo "==> Deploying nrvous status page to $HOST"
python3 scripts/pyput.py printer/nrvous_status_page.py "${HOST}:/usr/local/bin/nrvous_status_page.py"
python3 scripts/pyput.py printer/nrvous_status_page.init.sh "${HOST}:/etc/init.d/nrvous_status_page"

ssh "$HOST" '
    chmod +x /usr/local/bin/nrvous_status_page.py /etc/init.d/nrvous_status_page
    /etc/init.d/nrvous_status_page enable
    /etc/init.d/nrvous_status_page restart
    sleep 1
    echo "==> Listeners:"
    netstat -tlnp 2>/dev/null | grep ":8765" || true
    echo "==> Smoke test:"
    export PATH="/usr/local/bin:$PATH"
    pyfetch http://127.0.0.1:8765/nrvous-status/ 2>&1 | head -6 || true
'

echo "==> Done"
