#!/bin/sh
# Re-enable the stock Creality app services and restore the original nginx config.
# This undoes printer/deploy_nginx_frontdoor.sh so web-server reclaims ports 80/443.
set -e

HOST=${1:-root@192.168.1.100}

echo "==> Restoring stock app service and nginx config on $HOST"

ssh "$HOST" '
    # Re-enable stock app service (starts web-server on 80/443 on next boot).
    /etc/init.d/app enable 2>/dev/null || true

    # Disable our add-on services so they do not fight web-server.
    /etc/init.d/lan_bridge disable 2>/dev/null || true
    /etc/init.d/lan_bridge stop 2>/dev/null || true
    /etc/init.d/go2rtc disable 2>/dev/null || true
    /etc/init.d/go2rtc stop 2>/dev/null || true
    /etc/init.d/nrvous_status_page disable 2>/dev/null || true
    /etc/init.d/nrvous_status_page stop 2>/dev/null || true

    # Restore original nginx config if backup exists.
    if [ -f /etc/nginx/nginx.conf.bak ]; then
        cp /etc/nginx/nginx.conf.bak /etc/nginx/nginx.conf
        echo "Restored /etc/nginx/nginx.conf from .bak"
    fi
    # Remove our add-on nginx includes.
    rm -f /etc/nginx/conf.d/creality.compat.locations.conf \
          /etc/nginx/conf.d/creality.lan.locations.conf \
          /etc/nginx/conf.d/creality.lan.websocket.conf \
          /etc/nginx/conf.d/ecs-log-format.conf

    # Restart nginx on its original port 4408.
    /etc/init.d/nginx restart
    sleep 1

    # Start stock app services (web-server on 80/443).
    /etc/init.d/app start 2>/dev/null || true
    sleep 1

    echo "==> Listeners after restore:"
    netstat -tlnp 2>/dev/null | grep -E ":80 |:443 |:4408" || true
'

echo "==> Done"
