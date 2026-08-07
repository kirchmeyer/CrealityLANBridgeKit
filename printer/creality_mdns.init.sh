#!/bin/sh /etc/rc.common
#
# mDNS announcer init for the Creality LAN bridge.
# Announces _Creality-{SN}._udp.local with the printer's real SN/MAC/Model.
# MDNS_SERVICE_PORT is the TCP port advertised in the SRV record
# (where /info, /protocal.csp, etc. are served); defaults to 80.

USE_PROCD=1
START=90
STOP=15

PROG=/usr/local/bin/creality_mdns_announcer.py

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/python3 $PROG
    procd_set_param env MDNS_SERVICE_PORT=80
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn
    procd_close_instance
}

stop_service() {
    # Only kill our announcer, not every python3 (moonraker runs under python3).
    killall -9 creality_mdns_announcer.py 2>/dev/null || true
}
