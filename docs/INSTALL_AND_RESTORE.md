# Creality LAN Bridge — Install & Restore Guide

This project makes a Creality K-series printer (running Klipper/Moonraker on OpenWrt) expose the same LAN contract that the stock Creality Print desktop app expects. The desktop app remains unmodified.

> **Compatibility**: developed and tested on the **Creality K2 Plus**. Other Creality models may use a different LAN contract, camera pipeline, or LED pin naming. Use on other printers at your own risk; the core logic will likely need small adaptations.

> **Goal**: from a stock printer with network access, run the unified installer, reset the desktop app cache on macOS, then open Creality Print and use the printer like a stock LAN printer.

---

## 1. What this does (the 30-second version)

The Creality Print desktop app normally talks to a stock Creality printer over HTTP/WebSocket. This project adds a compatibility layer **on the printer** so it answers those same calls, translating them into Moonraker/Klipper commands.

The app sees:
- Device info, status, temperatures, progress
- A working camera feed
- CFS/AMS filament boxes
- Start print, pause, cancel
- LED, fans, speed, temperatures

The printer runs:
- `lan_bridge.py` — the compatibility backend
- `nginx` — front-door routing and camera proxying
- `go2rtc` + `cam_app` + `mjpeg_server.py` — single-source camera stack
- `webrtc_local_bridge.py` — WebRTC answer adapter for the LAN app camera
- `${PROJECT_NAME}_status_page.py` — operational status dashboard
- `${PROJECT_NAME}_watchdog.sh` — custom stack-wide health monitor
- ECS-compliant JSON logging from all services

---

## 2. Prerequisites

### On the printer (target)
- OpenWrt-based Creality printer (K2 Plus in this project)
- SSH access as `root`
- Klipper + Moonraker already running
- Network reachable from your PC/Mac
- `root` is required: the installer writes to `/etc/nginx`, `/etc/init.d`, `/usr/local/bin`, and disables the stock Creality `app` service. If your printer has SSH disabled, enable it through the printer's own settings or Creality's documented procedure first.

### On your PC/Mac (control)
- A terminal
- `ssh`, `scp`, and `git`
- This repo cloned somewhere convenient

---

## 3. One-button install

All scripts read environment defaults so you rarely need to repeat arguments:

```bash
export PRINTER_HOST=192.168.1.100   # printer IP or hostname
export PRINTER_USER=root             # SSH user
export PUBLIC_HOST=printer.lan       # hostname the app uses
export CERT_BASENAME=self-signed     # basename of /etc/nginx/conf.d/*.crt / *.key
export ECS_LOGGING=1                 # 1 = ECS JSON logs, 0 = plain text
export PROJECT_NAME=bridge           # prefix for backup manifest, services, status path
export STATUS_PATH=bridge-status     # URL path for the status page
```

From the repo root on your PC/Mac:

```bash
# 1. Install or update the printer-side stack
./install.sh install

# 2. Reset the desktop app cache (macOS)
./scripts/reset_creality_print_cache.sh --yes --no-launch

# 3. Open Creality Print and add the printer by IP
```

You can still pass explicit arguments and options:

```bash
./install.sh install 192.168.1.100 root \
  --public-host printer.lan \
  --cert-basename self-signed \
  --ecs-logging 1
```

### LAN mode

Choose how the printer handles plain HTTP for the LAN-app flow:

```bash
# open (default): the Creality app adds the printer by IP and uses plain HTTP
./install.sh install 192.168.1.100 root --lan-mode open

# proxy: plain HTTP redirects to HTTPS; point the desktop app at the local proxy
./install.sh install 192.168.1.100 root --lan-mode proxy --public-host printer.lan
python3 scripts/local_https_proxy.py \
  --upstream https://printer.lan:443 \
  --listen 127.0.0.1:8443 \
  --cert ./certs/printer.lan.crt \
  --key ./certs/printer.lan.key
```

See [README.md](../README.md) for more on the local HTTPS proxy.

### What `install.sh install` does

1. Backs up stock config (`/etc/nginx/nginx.conf`, selected `/etc/init.d/*`) to `/etc/${PROJECT_NAME}_backup_manifest.json` on the printer.
2. Syncs the canonical files from this repo to the printer using `scripts/check_local_remote_sync.py`.
3. Sets executable permissions on scripts and init files.
4. Disables the stock Creality `app` service (so it does not reclaim ports 80/443).
5. Enables and restarts our services:
   - `lan_bridge`, `go2rtc`, `status_page`, `creality_mdns`, `webrtc_local_bridge`, `watchdog`
6. Reloads nginx and runs the endpoint contract check.

### Re-install after local changes

```bash
./install.sh sync
```

This pushes only files that differ and restarts services.

### TLS certificates

The installer needs a certificate/key pair on the printer at `/etc/nginx/conf.d/${CERT_BASENAME}.crt` and `/etc/nginx/conf.d/${CERT_BASENAME}.key`.

#### If you already have a certificate

Place the files in `./certs/${CERT_BASENAME}.crt` and `./certs/${CERT_BASENAME}.key`, then install them:

```bash
./install.sh cert 192.168.1.100 root ./certs
```

This copies them to the printer and reloads nginx. Re-run the same command later to renew.

#### If you want to provide a self-signed certificate

Generate it locally and install it the same way:

```bash
mkdir -p ./certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -subj "/CN=${PUBLIC_HOST}" \
  -keyout ./certs/${CERT_BASENAME}.key \
  -out ./certs/${CERT_BASENAME}.crt

./install.sh cert 192.168.1.100 root ./certs
```

#### If you want the installer to generate a self-signed certificate

Use `--self-signed` during install:

```bash
./install.sh install --self-signed
```

`openssl` must be available on your PC/Mac because the printer does not include it. The installer generates the cert locally and copies it to the printer.

#### Using a self-signed certificate

Browsers and the Creality Print app will not trust it by default. Options:

- Trust the generated `.crt` in your Mac System keychain so Safari and the app's WebKit view accept it.
- Add the printer by IP in Creality Print (`http://192.168.1.100`) to stay on plain HTTP on the LAN.
- For Home Assistant, Homebridge, or other integrations, disable TLS verification for local feeds or install the `.crt` as trusted on the consuming host.
- For the simplest long-term setup, use a real certificate from a public CA (for example, Let's Encrypt with DNS validation).

#### Fallback: local HTTPS proxy on your PC/Mac

The Creality Print desktop app only supports plain HTTP for LAN-added printers. If you cannot install a certificate on the printer, you can terminate TLS on your own machine and forward to the printer over HTTP:

```bash
python3 scripts/local_https_proxy.py \
  --upstream http://${PRINTER_HOST}:80 \
  --listen 127.0.0.1:8443 \
  --cert ./certs/${CERT_BASENAME}.crt \
  --key ./certs/${CERT_BASENAME}.key
```

This is useful for public reverse-proxy bridges or integrations that require HTTPS, but it is not end-to-end encryption: the hop from the proxy to the printer is still plain HTTP.

---

## 4. Verify it worked

```bash
# Check all endpoints answer correctly
./scripts/run_contract_check.sh

# Check file sync and service state
./install.sh status

# Open the operational status page
open "http://${PUBLIC_HOST}/${STATUS_PATH}/"

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
  /etc/init.d/go2rtc restart
  /etc/init.d/status_page restart
  /etc/init.d/creality_mdns restart
  /etc/init.d/webrtc_local_bridge restart
  /etc/init.d/watchdog restart
  /etc/init.d/nginx reload
'
```

### Re-apply from repo

```bash
./install.sh install 192.168.1.100 root
```

### Desktop app acting stale

```bash
./scripts/reset_creality_print_cache.sh --yes --no-launch
```

Then relaunch Creality Print.

### Restore stock Creality stack

```bash
./install.sh restore 192.168.1.100 root
```

This restores the backed-up stock config, disables our services, and re-enables the stock `app` service. Our files remain installed so you can re-run `install.sh install` later.

### Fully uninstall

```bash
./install.sh uninstall 192.168.1.100 root
```

This restores stock config, removes our files, and re-enables the stock `app` service.

---

## 6. Important environment variables

Set in `/etc/init.d/lan_bridge`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Klipper/Moonraker upstream |
| `PUBLIC_HOST` | `printer.lan` | Hostname the app uses from outside |
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

## 8. Camera URLs for other integrations

Because the camera stack is exposed through nginx, you can use the same feeds in Home Assistant, Homebridge, or any other MJPEG/RTSP consumer:

| Feed | URL |
|------|-----|
| MJPEG over HTTP | `http://${PRINTER_HOST}/camera.mjpeg` |
| Single JPEG snapshot | `http://${PRINTER_HOST}/camera.jpeg` |
| go2rtc MJPEG | `http://${PRINTER_HOST}/webcam/stream.mjpg` |
| go2rtc JPEG snapshot | `http://${PRINTER_HOST}/webcam/cam.jpg` |
| RTSP (direct from go2rtc) | `rtsp://${PRINTER_HOST}:8554/camera` |
| WebRTC answer | `https://${PUBLIC_HOST}/call/webrtc_local` |

For HTTPS versions, replace `http://${PRINTER_HOST}` with `https://${PUBLIC_HOST}` and trust or disable verification for self-signed certs in the consuming tool.

## 9. File map

| File | What it is |
|------|------------|
| `install.sh` | Unified installer/restore/uninstall entry point |
| `printer/lan_bridge.py` | The main compatibility backend |
| `printer/lan_bridge.init.sh` | OpenWrt init script |
| `printer/creality.lan.locations.conf` | nginx HTTP locations |
| `printer/creality.lan.websocket.conf` | nginx WebSocket/camera server |
| `printer/webrtc_local_bridge.py` | WebRTC answer adapter for the app |
| `printer/${PROJECT_NAME}_status_page.py` | Operational status dashboard |
| `printer/${PROJECT_NAME}_watchdog.sh` | Stack-wide health monitor |
| `scripts/check_local_remote_sync.py` | Idempotent file sync to the printer |
| `scripts/run_contract_check.sh` | Endpoint contract validator |
| `scripts/reset_creality_print_cache.sh` | macOS app cache reset |
| `docs/SESSION_HANDOFF.md` | Developer/recovery notes |
| `docs/LAN_PROTOCOL_CONTRACT.md` | API contract reference |
| `docs/ARCHITECTURE.md` | System diagram and data flow |

---

## 10. Quick reference diagram

See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for a full diagram and data flow.
