#!/bin/sh /etc/rc.common
# OpenWrt procd init for the Creality mDNS announcer.
# Mirrors the stock /rom/etc/init.d/mdns contract:
#   service _Creality-{SN}._udp.local, SRV port 5353, truncated TXT records.

USE_PROCD=1
START=90
STOP=15

PROG=/usr/local/bin/creality_mdns_announcer.py

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/python3 $PROG
    # MDNS_SERVICE_PORT defaults to 5353 to match stock /rom/usr/bin/mdns.
    procd_set_param env MDNS_SERVICE_PORT=5353
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    # Only kill our announcer, not every python3 process.
    killall -9 creality_mdns_announcer.py 2>/dev/null || true
}
