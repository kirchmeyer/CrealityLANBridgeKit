# Project Summary

This repository is a printer-side compatibility layer for the stock Creality Print macOS app. The core idea is simple:

- Keep the app stock.
- Make the printer expose the routes and payloads that the app already expects.
- Use nginx and a small Python compatibility backend on the printer to bridge the gap.

> **Compatibility**: this was developed and tested on a **Creality K2 Plus**. Other Creality printers may expose a different LAN protocol contract, camera pipeline, or LED control. It can probably be adapted, but that work has not been done and is not guaranteed. Use at your own risk.

## Mental model

The app is not being patched. Instead, the printer is made to behave like a compatible Creality endpoint.

The working path is:

1. Inspect the stock app’s expected contract from the bundled web assets.
2. Mirror the important routes and payload shapes on the printer.
3. Verify the frontend-facing endpoints with contract checks.
4. If the app still shows stale state, clear its local cache and rehydrate from the live printer.

## The important moving pieces

- Printer-side LAN compatibility backend: `printer/lan_bridge.py` (binds `127.0.0.1:9002`)
- Camera stack orchestrator: `printer/restart_cam_stack.sh` + `printer/go2rtc_init.sh`
- Single-source H264 fan-out: `printer/cam_delivery_bridge.py`
- RTSP → MJPEG server: `printer/mjpeg_server.py`
- Printer route templates: `printer/creality.lan.locations.conf` + `printer/creality.lan.websocket.conf`
- WebRTC answer adapter: `printer/webrtc_local_bridge.py`
- Operational status page: `printer/status_page.py` (`/${STATUS_PATH}/`)
- Stack health monitor: `printer/watchdog.sh`
- Unified installer: `install.sh` (install/restore/uninstall/sync/status)
- Idempotent file sync: `scripts/check_local_remote_sync.py`
- Contract checks: `scripts/endpoint_contract_check.py` and `scripts/run_contract_check.sh`
- Local app bundle source tree: `/Applications/Creality Print.app/Contents/Resources/web`
- Local app state cache: `~/Library/Application Support/Creality/Creality Print/7.0/`
- Snapshot reference: `snapshots/20260729_181350/`

## Why this approach worked quickly

The fastest breakthroughs came from combining four things:

- Live printer logs from nginx and the compatibility backend.
- The app bundle source tree for the stock app, which revealed the exact contract the app was expecting.
- Contract-check scripts that let changes be verified without guessing.
- A cache-reset step for the stock app whenever behavior changed, because the app otherwise reuses stale device state.

## Current printer-specific context

Environment defaults (set once instead of repeating arguments):

- `PRINTER_HOST` — printer hostname or IP (default: `printer.lan`)
- `PRINTER_USER` — SSH user (default: `root`)
- `PUBLIC_HOST` — public hostname used by the app flow (default: `printer.lan`)
- `CERT_BASENAME` — basename of `/etc/nginx/conf.d/*.crt` and `*.key` (default: `self-signed`)
- `ECS_LOGGING` — `1` for ECS JSON logs, `0` for plain text (default: `1`)

Live testing values:

- Target printer: `root@192.168.1.100`
- LAN bridge backend bind: `127.0.0.1:9002` (WebSocket fronted by nginx on 9999)
- Moonraker upstream: `http://127.0.0.1:7126`
- Public host used by the app flow: `printer.lan`
- Operational status page: `http://${PUBLIC_HOST}/${STATUS_PATH}/`
- Backup manifest on printer: `/etc/${PROJECT_NAME}_backup_manifest.json`
- Camera debug logs: `/tmp/cam_app_solo.log`, `/tmp/cam_delivery_bridge.log`, `/tmp/mjpeg_server_solo.log`, `/tmp/go2rtc_solo.log`
- Service logs: `/var/log/lan_bridge.log`, `/var/log/${PROJECT_NAME}_watchdog.log`, `/var/log/nginx/access.log`
- All service logs are ECS-compliant JSON lines by default.

## Pitfalls to avoid

- Do not patch the Creality Print app bundle unless the user explicitly approves that fallback path.
- Do not assume one endpoint is enough; the app often needs coordinated support across info, detail, poll-state, media, record, and camera routes.
- Always clear the app cache after a significant payload-shape change, otherwise the app may continue using stale cached state.
- Keep the compatibility changes focused on contract matching; over-broad nginx rules are harder to reason about.

## Good first moves when picking this up again

```bash
export PRINTER_HOST=192.168.1.100
export PRINTER_USER=root
export PUBLIC_HOST=printer.lan
export CERT_BASENAME=self-signed
export ECS_LOGGING=1
```

1. Run `./install.sh install` to deploy or update the stack.
2. Run `./scripts/run_contract_check.sh`.
3. Run `./install.sh status` to see file sync and service health.
4. Inspect the camera logs and the printer’s JSON logs (`/var/log/lan_bridge.log`, `/var/log/${PROJECT_NAME}_watchdog.log`, `/var/log/nginx/access.log`).
5. Compare the live payloads to the stock app bundle expectations.
6. Clear the Creality Print app cache if the UI still looks stale.

## Known issues / what is still not quite right

- The printer-side compatibility layer is now much closer to the app’s expectation, but some UI behavior can still feel inconsistent until the app cache is cleared and the printer is re-queried.
- The detail/media/record routes are covered, but the remaining risk is that one route still behaves slightly differently from the stock app’s expectation even when the main payloads look correct.
- The project has already tried the fastest recovery path: redeploy the backend, re-check the live response bodies, and reset the app cache. Those steps are usually the quickest way to separate a route mismatch from a stale-client problem.
