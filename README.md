# Creality LAN Bridge Kit

This repository is a printer-side compatibility layer for the stock Creality Print macOS app. The goal is to keep the app stock, make the printer expose the contract the app already expects, and do it all over HTTPS without breaking either LAN-added or cloud-connected printers.

## What this project does

- Runs a small LAN compatibility backend on the printer at `127.0.0.1:9002`.
- Routes app-facing endpoints such as `/info`, `/protocal.csp`, `/machine/system_info`, `/machine/multi_machine`, `/api/rest/print/cluster/devices/*`, `/api/v1/streams`, and related camera/record routes through nginx to that backend.
- Provides a single-source camera stack that feeds both Creality Cloud and the LAN app from one `cam_app` instance.
- Preserves printer-side behavior for both LAN-added devices and cloud-connected printers without modifying the Creality Print app bundle.
- Terminates TLS on the printer so the public-facing flow can use HTTPS while the stock app continues to work over plain HTTP on the local network.
- Emits ECS-compliant JSON logs from all services for easy aggregation.

Think of it as a compatibility spoke: the printer still talks to Creality Cloud the way it always did, but it also answers the LAN contract the desktop app expects, so you can use the stock app, Home Assistant, Homebridge, or any other LAN consumer at the same time.

## Repository layout

- `printer/lan_bridge.py` — main LAN compatibility backend
- `printer/lan_bridge.init.sh` — OpenWrt procd init for the LAN bridge
- `printer/restart_cam_stack.sh` — orchestrates `cam_app` → delivery bridge → `mjpeg_server` → `go2rtc`
- `printer/go2rtc_init.sh` — OpenWrt procd init that runs the camera stack wrapper
- `printer/creality.lan.locations.conf` — nginx location blocks for the LAN-facing routes
- `printer/creality.lan.websocket.conf` — nginx WebSocket server on port 9999
- `printer/status_page.py` — operational status dashboard at `/${STATUS_PATH}/`
- `printer/watchdog.sh` — custom stack-wide health monitor
- `scripts/endpoint_contract_check.py` — fast contract validation against the printer front door
- `scripts/check_local_remote_sync.py` — compare/deploy repo files to the printer
- `scripts/run_contract_check.sh` — wrapper for the full check
- `scripts/reset_creality_print_cache.sh` — clears cached Creality Print app state when the UI stays stale
- `install.sh` — unified installer with backup, restore, uninstall, and sync
- `docs/PROJECT_SUMMARY.md` — quick mental model and handoff summary
- `docs/INSTALL_AND_RESTORE.md` — detailed install/restore walkthrough
- `docs/ARCHITECTURE.md` — how the pieces fit together

## Configuration defaults

All scripts read environment defaults so you rarely need to repeat arguments:

- `PRINTER_HOST` — printer hostname or IP (default: `printer.lan`)
- `PRINTER_USER` — SSH user (default: `root`)
- `PUBLIC_HOST` — public hostname the app uses (default: `printer.lan`)
- `CERT_BASENAME` — basename of `/etc/nginx/conf.d/*.crt` and `*.key` (default: `self-signed`)
- `ECS_LOGGING` — `1` for ECS JSON logs, `0` for plain text (default: `1`)
- `PROJECT_NAME` — prefix used for backup manifest, service names, and status page path (default: `bridge`)
- `STATUS_PATH` — URL path for the status page (default: `$PROJECT_NAME-status`)

Live testing example values:

- Printer host: `root@192.168.1.100`
- LAN bridge backend: `http://127.0.0.1:9002` (WebSocket fronted by nginx on 9999)
- Moonraker upstream: `http://127.0.0.1:7126`
- Public host used by the app flow: `printer.lan`
- Status page: `http://${PUBLIC_HOST}/${STATUS_PATH}/`
- Camera debug logs: `/tmp/cam_app_solo.log`, `/tmp/cam_delivery_bridge.log`, `/tmp/mjpeg_server_solo.log`, `/tmp/go2rtc_solo.log`
- Service logs: `/var/log/lan_bridge.log`, `/var/log/${PROJECT_NAME}_watchdog.log`, `/var/log/nginx/access.log`

## Printer compatibility

This project was developed and tested on a **Creality K2 Plus**. It may work on other Creality printers that run a similar OpenWrt + Klipper/Moonraker stack, but compatibility with other models is **unknown** and **not guaranteed**. The LAN protocol contract, camera pipeline, and GPIO/light pin names differ between models.

If you try this on another printer, treat it as experimental:

- Verify `/info`, `/protocal.csp`, and `/server/info` responses match what the stock Creality Print app expects for your model.
- Check that Moonraker is reachable on the expected host/port.
- Adapt `LIGHT_ON_GCODE`/`LIGHT_OFF_GCODE` (or `LIGHT_MOONRAKER_URL`) if the LED/chamber light is controlled differently on your board. The default commands are `SET_PIN PIN=LED VALUE=1` and `SET_PIN PIN=LED VALUE=0`.

Use at your own risk. This is an unofficial compatibility layer, not a Creality-supported solution.

## Quick start

Set defaults once, then run without arguments:

```bash
export PRINTER_HOST=192.168.1.100
export PRINTER_USER=root
export PUBLIC_HOST=printer.lan
export CERT_BASENAME=self-signed

./install.sh install
./scripts/run_contract_check.sh
./install.sh status
```

You can still pass explicit arguments when needed:

```bash
./install.sh install 192.168.1.100 root --public-host printer.lan --cert-basename self-signed
./install.sh cert 192.168.1.100 root ./certs
```

### Root access

The installer must run as `root` on the printer because it writes to `/etc/nginx`, `/etc/init.d`, `/usr/local/bin`, and disables the stock Creality `app` service. Use the default `PRINTER_USER=root` (or pass `root` as the second argument). Some printers ship with SSH disabled; enable it in the printer's own settings or with Creality's documented procedure before running `install.sh`.

If the app still shows stale or incorrect printer state, reset the local Creality Print cache:

```bash
./scripts/reset_creality_print_cache.sh --yes --no-launch
```

## TLS certificates

The installer needs a certificate/key pair on the printer at `/etc/nginx/conf.d/${CERT_BASENAME}.crt` and `/etc/nginx/conf.d/${CERT_BASENAME}.key`. Pick the path that matches your situation.

### I already have a certificate

Put the files in a local directory (for example `./certs`) with the same basename, then install them:

```bash
ls ./certs
# printer.lan.crt  printer.lan.key

export CERT_BASENAME=printer.lan
./install.sh cert ./certs
```

This copies them to the printer and reloads nginx. Renewals use the same command.

### I want to provide a self-signed certificate

Generate one locally with `openssl` and install it the same way:

```bash
mkdir -p ./certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -subj "/CN=${PUBLIC_HOST}" \
  -keyout ./certs/${CERT_BASENAME}.key \
  -out ./certs/${CERT_BASENAME}.crt

./install.sh cert ./certs
```

### I want the installer to generate a self-signed certificate

Use `--self-signed` during install:

```bash
./install.sh install --self-signed
```

The installer will create the certificate on your local machine and copy it to the printer. The printer does not have `openssl`, so generation happens locally.

### What to do with a self-signed certificate

Browsers and the Creality Print app will not trust a self-signed certificate by default. Options:

- Install the generated `.crt` file into your Mac's System keychain as a trusted root certificate, then it will be trusted by Safari and the app's WebKit view.
- For the desktop app, adding the printer by IP (`http://192.168.1.100`) still uses plain HTTP on the LAN and does not require a trusted cert.
- For Home Assistant or Homebridge, most integrations allow you to disable TLS verification for local camera feeds, or you can install the `.crt` as trusted on the machine running the integration.
- The safest long-term option is to use a real certificate from a public CA, for example via Let's Encrypt DNS validation if your DNS host supports it.

### What if I can't install a certificate on the printer?

The Creality Print desktop app adds LAN printers by IP and uses plain HTTP. If you cannot put a certificate on the printer itself, you can run a small local HTTPS proxy on your PC/Mac or home server instead:

```bash
python3 scripts/local_https_proxy.py \
  --upstream http://${PRINTER_HOST}:80 \
  --listen 127.0.0.1:8443 \
  --cert ./certs/${CERT_BASENAME}.crt \
  --key ./certs/${CERT_BASENAME}.key
```

Then point other tools at `https://<proxy-host>:8443/...`. The Creality app still talks to `http://${PRINTER_HOST}:80` directly. This is a fallback, not a security upgrade: traffic between the proxy and the printer remains plain HTTP.

## LAN modes: open vs proxy

The installer supports two mutually exclusive LAN modes via `--lan-mode`:

| Mode | What happens on port 80/81 | Use when |
|------|-----------------------------|----------|
| `open` (default) | The full LAN-app contract (`/info`, `/protocal.csp`, `/call/*`, uploads, camera, etc.) is available over plain HTTP on ports 80 and 81. | The Creality app adds the printer by IP, Home Assistant/Homebridge talk directly to the printer on HTTP, or you just want the easiest setup. |
| `proxy` | Plain HTTP on ports 80 and 81 redirects everything to HTTPS. | You want to force HTTPS for the LAN flow and you are running the [local HTTPS proxy](#local-https-proxy) on the client that runs Creality Print. |

In `proxy` mode the printer itself still terminates TLS on port 443, but the desktop app cannot add the printer by IP because the app only speaks plain HTTP. You point the app at the local proxy's HTTPS address (`https://127.0.0.1:8443` or similar) instead of the printer's IP.

Examples:

```bash
# Open LAN: desktop app adds printer by IP over HTTP
./install.sh install 192.168.1.100 root --lan-mode open

# Proxy LAN: desktop app points at a local HTTPS proxy
./install.sh install 192.168.1.100 root --lan-mode proxy --public-host printer.lan
python3 scripts/local_https_proxy.py \
  --upstream https://printer.lan:443 \
  --listen 127.0.0.1:8443 \
  --cert ./certs/printer.lan.crt \
  --key ./certs/printer.lan.key
```

## Installer commands

| Command | Purpose |
|---------|---------|
| `./install.sh install [HOST] [USER]` | Backup stock config, deploy files, enable services, restart stack |
| `./install.sh sync [HOST] [USER]` | Push only changed files and restart services |
| `./install.sh status [HOST] [USER]` | Show file sync status and service states |
| `./install.sh restore [HOST] [USER]` | Restore stock nginx/services, disable our stack |
| `./install.sh uninstall [HOST] [USER]` | Restore stock config and remove our files |

## What to inspect when something breaks

### On the printer

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 120 /var/log/nginx/access.log | jq . 2>/dev/null | tail -n 40'
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 120 /var/log/lan_bridge.log | jq . 2>/dev/null | tail -n 40'
ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.100 'tail -n 120 /var/log/${PROJECT_NAME}_watchdog.log | jq . 2>/dev/null | tail -n 40'
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

## Camera URLs for Home Assistant, Homebridge, and other integrations

Because the camera stack is exposed through nginx, you can point other tools at it without running anything extra:

| Feed | URL |
|------|-----|
| MJPEG over HTTP | `http://${PRINTER_HOST}/camera.mjpeg` |
| Single JPEG snapshot | `http://${PRINTER_HOST}/camera.jpeg` |
| go2rtc MJPEG | `http://${PRINTER_HOST}/webcam/stream.mjpg` |
| go2rtc JPEG snapshot | `http://${PRINTER_HOST}/webcam/cam.jpg` |
| RTSP (direct from go2rtc) | `rtsp://${PRINTER_HOST}:8554/camera` |
| WebRTC answer | `https://${PUBLIC_HOST}/call/webrtc_local` |

For HTTPS versions, replace `http://${PRINTER_HOST}` with `https://${PUBLIC_HOST}` and note that self-signed certs will need to be trusted or verification disabled in the consuming tool.

## Helpful reference files

- The stock Creality Print macOS app bundle under `/Applications/Creality Print.app/Contents/Resources/web` can be inspected to understand the contract the app expects. This project treats the app bundle as a reference only; it is never modified.
- Set `LAN_BRIDGE_DEBUG=1` in `/etc/init.d/lan_bridge` to trace payloads from the LAN bridge.

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities and the assumptions this project makes about printer access.

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
