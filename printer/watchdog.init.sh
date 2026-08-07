#!/bin/sh /etc/rc.common
# OpenWrt procd init for a small custom watchdog for the bridge stack.
#
# This is NOT a replacement for per-service procd respawn; it is a safety net
# for processes that are hard to supervise as a single unit (camera stack
# siblings) or for services that do not have their own init script.
#
# The watchdog runs a shell loop in the background that periodically checks
# critical endpoints and process patterns. When it detects a problem it logs
# the event and asks procd to restart the affected service rather than trying
# to repair the world itself.

START=99
STOP=05
USE_PROCD=1
DEPEND=fstab

PROG=/usr/local/bin/watchdog.sh

start_service() {
    [ -x "$PROG" ] || { logger -t %PROJECT_NAME%_watchdog "missing $PROG"; return 1; }
    procd_open_instance watchdog
    procd_set_param env HOME=/root PATH="/usr/sbin:/usr/bin:/sbin:/bin" PROJECT_NAME="%PROJECT_NAME%" ECS_LOGGING="%ECS_LOGGING%"
    procd_set_param command /bin/sh "$PROG"
    procd_set_param stdout 1
    procd_set_param stderr 1
    # Bounded respawn: if the watchdog itself is crashing, stop flapping.
    procd_set_param respawn 3600 5 3
    procd_close_instance
}

stop_service() {
    for pid in $(pgrep -f "$PROG" 2>/dev/null || true); do
        [ "$$" != "$pid" ] && kill -9 "$pid" 2>/dev/null || true
    done
}
