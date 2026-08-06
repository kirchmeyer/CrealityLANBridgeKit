# Creality LAN Bridge — Install & Restore Guide

This project makes a Creality K-series printer (running Klipper/Moonraker on OpenWrt) expose the same LAN contract that the stock Creality Print desktop app expects. The desktop app remains unmodified.

> **Goal**: from a stock printer with network access, run a single deploy script on the printer and one cache-reset helper on macOS. Then open Creality Print and use the printer like a stock LAN printer.

---

## 1. What this does (the 30-second version)

The Creality Print desktop app normally talks to a stock Creality printer over HTTP/WebSocket. This project adds a tiny compatibility layer **on the printer** so it answers those same calls, but translates them into Moonraker/Klipper commands.

The app sees:
- Device info, status, temperatures, progress
- A working camera feed
- CFS/AMS filament boxes
- Start print, pause, cancel
- LED, fans, speed, temperatures

The printer runs:
- `lan_bridge.py` — the compatibility backend
- `nginx` — front-door routing and camera proxying
- `go2rtc` — camera source
- `webrtc_local_bridge.py` — WebRTC answer adapter (optional, used by some app versions)

---

## 2. Prerequisites

### On the printer (target)
- OpenWrt-based Creality printer (K2 Plus in this project)
- SSH access as `root`
- Klipper + Moonraker already running
- Network reachable from your PC/Mac

### On your PC/Mac (control)
- A terminal
- `ssh` and `git`
- This repo cloned somewhere convenient

---

## 3. One-button install

From the repo root on your PC/Mac:

```bash
# 1. Deploy the printer-side stack
./printer/deploy_lan_bridge.sh

# 2. Reset the desktop app cache (macOS)
./scripts/reset_creality_print_cache.sh --yes --no-launch

# 3. Open Creality Print and add the printer by IP
```

That is the normal happy path. The deploy script copies files, installs OpenWrt init scripts, starts services, and reloads nginx.

### What `deploy_lan_bridge.sh` actually does

1. Copies `printer/lan_bridge.py` to `/usr/local/bin/lan_bridge.py`
2. Installs `/etc/init.d/lan_bridge`
3. Installs nginx configs:
   - `/etc/nginx/conf.d/creality.lan.locations.conf`
   - `/etc/nginx/conf.d/creality.lan.websocket.conf`
4. Reloads nginx
5. Starts/enables `lan_bridge`

### Optional: WebRTC frontdoor adapter

If your app version uses `http://{printer}:8000/call/webrtc_local` for camera:

```bash
./printer/deploy_webrtc_bridge.sh
```

---

## 4. Verify it worked

```bash
# Check all endpoints answer correctly
./scripts/run_contract_check.sh 192.168.1.100 80

# Inspect the live CFS/AMS payload
ssh root@192.168.1.100 'python3 - <<"PY"
import sys, json
sys.path.insert(0, "/usr/local/bin")
import lan_bridge
boxs = lan_bridge._build_detail_payload()["result"]["boxsInfo"]
print("boxes:", len(boxs["materialBoxs"]))
for b in boxs["materialBoxs"]:
    print(" ", b["id"], b["name"], "slots:", len(b["materials"]))
PY'

# Check camera frames
curl -s http://192.168.1.100/camera.jpeg | head -c 50
```

---

## 5. If something breaks — restore points

### Soft recovery (services only)

```bash
ssh root@192.168.1.100 '
  /etc/init.d/lan_bridge restart
  /etc/init.d/moonraker restart
  /etc/init.d/klipper restart
  /etc/init.d/nginx reload
'
```

### Hard recovery (re-deploy from repo)

```bash
./printer/deploy_lan_bridge.sh
```

### Desktop app acting stale

```bash
./scripts/reset_creality_print_cache.sh --yes --no-launch
```

Then relaunch Creality Print.

### Full rollback to stock-ish

The deploy only adds files; it does not overwrite your Klipper config. To remove the bridge:

```bash
ssh root@192.168.1.100 '
  /etc/init.d/lan_bridge disable
  /etc/init.d/lan_bridge stop
  rm -f /etc/init.d/lan_bridge /usr/local/bin/lan_bridge.py
  rm -f /etc/nginx/conf.d/creality.lan.locations.conf
  rm -f /etc/nginx/conf.d/creality.lan.websocket.conf
  /etc/init.d/nginx reload
'
```

---

## 6. Important environment variables

Set in `/etc/init.d/lan_bridge`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Klipper/Moonraker upstream |
| `PUBLIC_HOST` | `3d.nrvous.io` | Hostname the app uses from outside |
| `PUBLIC_SCHEME` | `http` | `http` or `https` |
| `CFS_FLATTEN` | `0` | `1` merges all CFS boxes into one 8-slot box |

Edit the init script and restart `lan_bridge` to change them:

```bash
ssh root@192.168.1.100 'vi /etc/init.d/lan_bridge && /etc/init.d/lan_bridge restart'
```

---

## 7. Known limitations

- **mDNS scan discovery** from Creality Print is not yet fully emulated; add the printer by IP.
- **Creality Cloud mobile camera** uses Creality's cloud tunnel and is out of scope for this LAN-only bridge.
- **LED pin scale hazard**: sending `SET_PIN PIN=LED VALUE=255` directly to Klipper crashes it because the `[output_pin LED]` scale is `1.0`. The bridge clamps to `0.0-1.0`, but avoid direct G-code values > 1.

---

## 8. File map

| File | What it is |
|------|------------|
| `printer/lan_bridge.py` | The main compatibility backend |
| `printer/lan_bridge.init.sh` | OpenWrt init script |
| `printer/deploy_lan_bridge.sh` | One-button printer deploy |
| `printer/creality.lan.locations.conf` | nginx HTTP locations |
| `printer/creality.lan.websocket.conf` | nginx WebSocket/camera server |
| `printer/webrtc_local_bridge.py` | WebRTC answer adapter for the app |
| `scripts/run_contract_check.sh` | Endpoint contract validator |
| `scripts/reset_creality_print_cache.sh` | macOS app cache reset |
| `docs/SESSION_HANDOFF.md` | Developer/recovery notes |
| `docs/LAN_PROTOCOL_CONTRACT.md` | API contract reference |

---

## 9. Quick reference diagram

See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for a full diagram and data flow.
