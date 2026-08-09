# Camera Stack Handoff — 2026-08-06

## Status
All camera paths verified working:
- ✅ Fluidd / Homebridge / [matterbridge-rtsp-camera](https://github.com/kirchmeyer/matterbridge-rtsp-camera) / LAN printers (MJPEG + RTSP/WebRTC via go2rtc)
- ✅ Creality Cloud (both iOS and macOS apps)
- ✅ Creality Print LAN printer mode

## Root cause that was blocking cloud
`/usr/bin/webrtc` was holding a stale `/tmp/uvc_fifo (deleted)` fd. The stock `Monitor` watchdog and `/etc/init.d/webrtc` could start the cloud daemon before our FIFO writer was ready, and the FIFO was being unlinked/re-created underneath the daemon, so it read from a dead inode.

Fix: patched `/etc/init.d/webrtc` to ensure `/tmp/uvc_fifo` exists **before** launching `/usr/bin/webrtc`, and changed our orchestration so it never deletes `/tmp/uvc_fifo` while the daemon holds it open.

## Final data flow
```
/dev/video0
    └── /usr/bin/cam_app -i /dev/video0 -t main_cam
            └── /tmp/delivery_socket100 (H264 Annex-B NALs)
                    └── /usr/local/bin/cam_delivery_bridge.py
                            ├── /tmp/uvc_fifo  ──> /usr/bin/webrtc  ──> Creality Cloud
                            └── /tmp/go2rtc_cam.fifo ──> /usr/bin/go2rtc
                                    ├── RTSP/WebRTC ──> Fluidd / Homebridge / [matterbridge-rtsp-camera](https://github.com/kirchmeyer/matterbridge-rtsp-camera)
                                    └── RTSP ──> /usr/local/bin/mjpeg_server.py
                                            └── MJPEG endpoints /camera.mjpeg, /webcam/stream.mjpg
```

## Files changed on printer
| File | Purpose |
|------|---------|
| `/etc/init.d/webrtc` | Now creates `/tmp/uvc_fifo` before starting cloud daemon. Stock backup at `/etc/init.d/webrtc.stock.bak`. |
| `/etc/init.d/go2rtc` | Procd wrapper that backgrounds `/usr/local/bin/restart_cam_stack.sh`. |
| `/usr/local/bin/restart_cam_stack.sh` | Single-source orchestration with file locking and duplicate cleanup. |
| `/usr/local/bin/cam_delivery_bridge.py` | Subscribes to deliveryStation and fans H264 to cloud + LAN FIFOs. |
| `/usr/local/bin/mjpeg_server.py` | Persistent MJPEG server from go2rtc RTSP. |
| `/usr/local/bin/webrtc_local_bridge.py` | Creality Print LAN mode (port 8000) — unchanged. |
| `/etc/go2rtc.yaml` | go2rtc reads `/tmp/go2rtc_cam.fifo` and exposes RTSP/WebRTC. |

## Recovery commands
```sh
# Quick restart of the whole camera stack
/etc/init.d/go2rtc restart

# Check if all expected processes are running
ps w | grep -E "cam_app|cam_delivery|go2rtc|/usr/bin/webrtc|mjpeg_server|ffmpeg" | grep -v grep

# Verify cloud webrtc has outbound connections
netstat -tnp | grep webrtc

# If cloud still fails after a crash/reboot, check the FIFO fd is not "(deleted)"
ls -l /proc/$(pidof webrtc)/fd/ | grep uvc_fifo
```

## Relevant logs
- Cloud webrtc: `/mnt/UDISK/creality/userdata/log/webrtc.log`
- Stack startup: `/tmp/cam_stack_start.log`
- Bridge: `/tmp/cam_delivery_bridge.log`
- go2rtc: `/tmp/go2rtc_solo.log`
- mjpeg_server: `/tmp/mjpeg_server_solo.log`
- Watchdog restarts: `/mnt/UDISK/creality/userdata/log/Monitor.log`

## Notes
- `/etc/init.d/webrtc` is enabled again so the stock procd watchdog/Monitor path stays intact; it just creates the FIFO first.
- `/usr/bin/webrtc` is started with the correct env argument (`2` for production Creality Cloud) by the stock init.
- LAN-only path (`webrtc_local_bridge` on port 8000) is intentionally left alone; it is managed by `/etc/init.d/webrtc_local_bridge`.
