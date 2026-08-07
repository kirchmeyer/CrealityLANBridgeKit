# Creality LAN Bridge Kit

This repository is a printer-side compatibility layer for the stock Creality Print macOS app. The goal is to keep the app stock and make the printer expose the contract the app already expects.

## What this project does

- Runs a small LAN compatibility backend on the printer at 127.0.0.1:9002.
- Routes app-facing endpoints such as `/info`, `/protocal.csp`, `/machine/system_info`, `/machine/multi_machine`, `/api/rest/print/cluster/devices/*`, `/api/v1/streams`, and related camera/record routes through nginx to that backend.
- Provides a single-source camera stack that feeds both Creality Cloud and the LAN app from one `cam_app` instance.
- Preserves printer-side behavior for LAN-added devices without modifying the Creality Print app bundle.

## Repository layout

- printer/lan_bridge.py — main LAN compatibility backend
- printer/deploy_lan_bridge.sh — deploys the LAN bridge, camera stack, and nginx config on the printer
- printer/lan_bridge.init.sh — OpenWrt procd init for the LAN bridge
- printer/restart_cam_stack.sh — orchestrates cam_app → delivery bridge → mjpeg_server → go2rtc
- printer/go2rtc_init.sh — OpenWrt procd init that runs the camera stack wrapper
- printer/creality.lan.locations.conf — nginx location blocks for the LAN-facing routes
- printer/creality.lan.websocket.conf — nginx WebSocket server on port 9999
- scripts/endpoint_contract_check.py — fast contract validation against the printer front door
- scripts/run_contract_check.sh — wrapper for the full check
- scripts/reset_creality_print_cache.sh — clears cached Creality Print app state when the UI stays stale
- snapshots/20260729_181350/ — reference snapshot of the stock app bundle assets used during reverse engineering
- docs/PROJECT_SUMMARY.md — quick mental model and handoff summary
- docs/SESSION_HANDOFF.md — practical operational notes for the next session

## Printer-specific defaults

These are the values that were used for the live testing flow:

- Printer host: root@192.168.1.100
- LAN bridge backend: http://127.0.0.1:9002 (WebSocket fronted by nginx on 9999)
- Moonraker upstream: http://127.0.0.1:7126
- Public host used by the app flow: 3d.nrvous.io
- Camera debug logs: `/tmp/cam_app_solo.log`, `/tmp/cam_delivery_bridge.log`, `/tmp/mjpeg_server_solo.log`, `/tmp/go2rtc_solo.log`

## Quick start

1. Deploy the LAN bridge and camera stack to the printer

```bash
./printer/deploy_lan_bridge.sh root@192.168.1.100
```

2. Validate the printer-facing contract

```bash
python3 scripts/endpoint_contract_check.py --host 192.168.1.100 --port 80 --skip-upload
```

Or run the broader wrapper:

```bash
./scripts/run_contract_check.sh 192.168.1.100 80
```

3. If the app still shows stale or incorrect printer state, reset the local Creality Print cache

```bash
./scripts/reset_creality_print_cache.sh --yes --no-launch
```

## What to inspect when something breaks

### On the printer

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 120 /var/log/nginx/access.log'
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 120 /var/log/nginx/upload-access.log'
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'logread | tail -n 80'
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 40 /tmp/cam_app_solo.log /tmp/cam_delivery_bridge.log /tmp/mjpeg_server_solo.log /tmp/go2rtc_solo.log 2>/dev/null'
```

### On the Mac app side

The stock app bundle lives here:

```bash
/Applications/Creality\ Print.app/Contents/Resources/web
```

The app’s saved state for this project was inspected under:

```bash
~/Library/Application\ Support/Creality/Creality\ Print/7.0/
```

## Helpful reference files

- The stock app bundle under /Applications/Creality Print.app/Contents/Resources/web was used to reverse engineer the contract the app expects.
- The snapshot directory snapshots/20260729_181350/ contains the bundled assets that were used as a reference for the payload and route shapes.
- Set `LAN_BRIDGE_DEBUG=1` in `/etc/init.d/lan_bridge` to trace payloads from the LAN bridge.

## Pitfalls

- Do not patch the Creality Print app bundle unless the user explicitly approves that fallback path.
- The app is very sensitive to payload shape and route behavior; a mismatch can look like a UI issue.
- Clearing the local app cache is often necessary after a significant backend change because the app can keep stale device state.
- Keep the compatibility logic focused on contract matching and route normalization; broad nginx changes can become hard to reason about.

## Known issues and remaining friction

- The compatibility backend is now aligned with the main app-facing contract, but the stock app can still appear inconsistent until the local cache is cleared and the printer is re-queried.
- The most sensitive remaining risk is route-level mismatch in detail/media/record flows, even when the main info and state payloads look correct.
- The most effective mitigation tried so far is: redeploy the backend, inspect the live response body from the relevant routes, and reset the Creality Print app cache before judging the result.

## Philosophy

- Prefer printer-side compatibility as the stable long-term path.
- Keep the stock macOS app unchanged.
- Reproduce and verify with live printer responses before assuming the issue is solved.
