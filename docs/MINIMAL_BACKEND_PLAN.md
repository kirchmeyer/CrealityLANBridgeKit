# Plan: Minimal LAN Compatibility Backend

Goal: replace the large, unvetted `creality_probe_backend.py` with a small, stock-grounded compatibility layer that serves only what the Creality Print desktop app actually uses, while preserving Moonraker/Fluidd access and leaving cloud support intact.

Printer target: `root@192.168.1.100` (Creality K2 Plus, OpenWrt, nginx 1.19.6, Python 3.9.12).

---

## Phase 0 — Capture complete (done)

- [x] Confirmed stock `web-server` only implements `/info`; all other app paths 404.
- [x] Captured exact desktop app LAN protocol:
  - `GET /info`
  - `GET /protocal.csp`
  - WebSocket `{"method":"get", "params":{...}}`
- [x] Documented contract in `docs/LAN_PROTOCOL_CONTRACT.md`.

---

## Phase 1 — Revert to stock and survey

1. Save current `/etc/nginx/nginx.conf`, `/etc/nginx/conf.d/*`, and init scripts to a snapshot.
2. Stop the instrumented `creality_probe_backend.py` and custom mDNS announcer.
3. Restore stock init scripts (remove `probe_backend`, `mdns_announcer`, `webrtc_local_bridge` overrides if any).
4. Reboot or restart services so only stock firmware is running.
5. Verify:
   - `/info` returns stock 6-field JSON from `/rom/usr/bin/web-server`.
   - `/protocal.csp` returns 404 (confirms backend is needed).
   - Moonraker/Fluidd are reachable on their native ports.
   - Cloud services still start (check `logread`, `ps`, outbound connections).

6. Investigate stock app-server/master-server:
   - List ports bound by stock servers (`netstat -tlnp`).
   - Check if any stock process answers `/protocal.csp` on localhost.
   - If yes, we may route to it instead of writing a backend.

---

## Phase 2 — Design minimal backend

If stock cannot satisfy `/protocal.csp`, build a new Python server (`printer/lan_bridge.py`) with these constraints:

- Single file, < 500 lines.
- Binds `127.0.0.1:9001`.
- Serves only:
  - `GET /info` → stock 6-field JSON.
  - `GET /protocal.csp` → 41-field status payload (see contract doc).
  - WebSocket upgrade on `/` → periodic detail/status object.
- Reads data only from authoritative sources:
  - `/mnt/UDISK/creality/userdata/config/system_config.json`
  - `/mnt/UDISK/creality/userdata/config/temperature_info.json`
  - `/mnt/UDISK/creality/userdata/config/current_work_info.json`
  - `/mnt/UDISK/creality/gui/config/pipe-*.json`
  - Moonraker on `http://127.0.0.1:7125`
- No cloud/REST/cluster/iotrouter routes unless proven required.
- No persistence/identity caching unless proven required.
- No fabricated material/box data unless required for print-job flow.

Open design questions:
- Does the WebSocket need to echo keyed results (`reqGcodeList`, `reqHistory`, etc.) or is the generic status push sufficient?
- What is the minimum viable `boxsInfo`/`boxConfig` shape for the CFS/Material panels?
- Does `/protocal.csp` need different responses for the two query variants?

---

## Phase 3 — Integrate with nginx and Fluidd

1. Keep nginx as the front door on ports 80/443.
2. Route Creality app paths to `lan_bridge.py`:
   - `/info`
   - `/protocal.csp`
   - WebSocket upgrade (`/`)
3. Route `/webcam/` and `/webcam/api/` to go2rtc/Moonraker webcam.
4. Route everything else to Fluidd/Moonraker.
5. Keep `webrtc_local_bridge.py` on port 8000 for video (unchanged).
6. Write a clean init script `/etc/init.d/lan_bridge` and disable the old `probe_backend` init.

---

## Phase 4 — mDNS discovery

1. Confirm the stock `/rom/usr/bin/mdns` announces `_Creality-$SN._udp.local` on port 5353.
2. If discovery across VLANs remains broken, decide between:
   - A small reflector/repeater on the IoT VLAN.
   - The existing `creality_mdns_announcer.py` if its format matches stock.
3. Keep the announcer minimal and optional; add-by-IP must always work first.

---

## Phase 5 — Validation

1. Endpoint checks:
   ```bash
   python3 scripts/endpoint_contract_check.py --host 192.168.1.100 --port 80 --skip-upload
   ```
2. App behavior:
   - Clear cache, add by IP, confirm idle state and live temps.
   - Start a print job, confirm state changes to Printing and progress updates.
3. Fluidd check:
   - Open `https://printer.lan` and confirm full Moonraker/Fluidd UI works.
4. Cloud check (if applicable):
   - Verify stock cloud services can still register/heartbeat.

---

## Phase 6 — Cleanup and handoff

1. Remove or archive old `creality_probe_backend.py` if the new backend replaces it fully.
2. Update `docs/SESSION_HANDOFF.md` with the new service names and commands.
3. Create a new snapshot of the known-good state.
4. Update reset/reapply scripts to use `lan_bridge` instead of `probe_backend`.

---

## Decision tree

```
Stock web-server implements /info only
        │
        ▼
Can stock app-server/master-server answer /protocal.csp?
        │
        ├── YES ──► route nginx /protocal.csp to stock localhost port
        │            (smallest possible change)
        │
        └── NO ───► build printer/lan_bridge.py
                     serve /info, /protocal.csp, WebSocket
                     preserve Fluidd/Moonraker via nginx
                     keep/replace mDNS announcer only if needed
```

---

## Risks

- **Cloud break**: if stock `app-server`/`master-server` need to bind port 80/443 or talk inbound, nginx front-door may interfere. We will test before declaring done.
- **WebSocket contract mismatch**: app may require keyed responses for file/history requests; we will test and add only the needed keys.
- **State staleness**: `current_work_info.json` / Moonraker can get out of sync; the new backend must handle both and prefer live Moonraker state.
- **Video**: camera path is served by `webrtc_local_bridge.py`; any nginx rewrite changes must not break it.
