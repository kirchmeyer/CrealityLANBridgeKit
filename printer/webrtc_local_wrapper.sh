#!/bin/sh
# Wrapper installed as /usr/bin/webrtc_local on the printer.
# The stock Creality app-server respawns this binary.  Coordinate with the
# init script so only one webrtc_local_bridge.py instance ever binds port 8000.
LOCKFILE=/var/run/webrtc_local_bridge.lock

# Claim the lock.  flock is part of busybox on this build.
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    # Another wrapper already holds the lock; sleep forever.
    while true; do sleep 3600; done
fi

# With the lock held, check if port 8000 is already bound.
if netstat -tln 2>/dev/null | grep -q ":8000 "; then
    # Bridge already running; release lock and sleep forever.
    flock -u 200
    while true; do sleep 3600; done
fi

# No bridge running and we hold the lock; start it.  The exec keeps the lock
# held for the lifetime of the bridge process.
exec /usr/bin/python3 /usr/local/bin/webrtc_local_bridge.py
