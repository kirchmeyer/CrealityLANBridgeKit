# Captured LAN Protocol Contract

Source: live capture from Creality Print macOS app → instrumented `creality_probe_backend.py` on `192.168.1.100`, saved at `snapshots/20260804_153740/creality_probe_backend_debug.log`.

This is the **minimal set of endpoints and messages** the stock desktop app actually uses when the printer is added by IP on the LAN. Everything else in the old backend appears to be unused or dead code.

---

## 1. Discovery / Add-by-IP handshake

### `GET /info`

The app first requests `/info` (plain HTTP on port 80, or HTTPS on 443). It must return a JSON object matching the stock Creality firmware shape.

Expected fields (case-sensitive keys, values from stock firmware / system config):

```json
{
  "mac": "A1B2C3D4E5F6",
  "model": "F008",
  "sn": "347344280F3045",
  "version": "1.0.0",
  "videoPort": 443,
  "wssPort": 443
}
```

Notes:
- `mac` is the **compact** Wi-Fi MAC (no colons/dashes), uppercase in stock responses.
- `model` is the internal model code (`model_str` from `system_config.json`).
- `sn` is the device serial (`device_sn` from `system_config.json`).
- `videoPort` and `wssPort` tell the app which port to use for video and WebSocket.

Source files on printer:
- `/mnt/UDISK/creality/userdata/config/system_config.json` (`device_info.model_str`, `device_info.device_sn`, `device_info.device_mac`)
- `/usr/bin/keybox` fallback (`model`, `sn`, `wifi_mac`)

---

## 2. Status polling

### `GET /protocal.csp?fname=Info&opt=main&function=get`
### `GET /protocal.csp?fname=net&opt=iot_conf&function=set&ReqPrinterPara=1`

Both query variants return the same payload. The app polls this endpoint repeatedly.

Exact captured standby response:

```json
{
  "TotalLayer": "",
  "address": "3d.nrvous.io",
  "autohome": 0,
  "bedTemp": 50.0,
  "bedTemp2": 50.0,
  "cloudOnline": false,
  "connect": 1,
  "connectType": 1001,
  "curFeedratePct": 100,
  "curPosition": "X:175.0 Y:175.0 Z:10.021",
  "deviceState": 0,
  "deviceType": 0,
  "fan": 0,
  "features": ["videoInfo.videoEncryption", "videoInfo.video"],
  "isLanPrinter": true,
  "lanCompatible": true,
  "layer": "",
  "linuxVideoUrl": "http://3d.nrvous.io:8000/call/webrtc_local",
  "localOnline": true,
  "mac": "A1B2C3D4E5F6",
  "mcu_is_print": 0,
  "model": "F008",
  "modelFanPct": 0,
  "modelName": "F008",
  "modelVersion": "1.0.0",
  "nozzleTemp": 139.0,
  "nozzleTemp2": 170.0,
  "oldPrinter": false,
  "online": true,
  "print": "",
  "printJobTime": 0,
  "printLeftTime": 0,
  "printProgress": 0,
  "printStartTime": 0,
  "socket": null,
  "ssid": "K2Plus-ABCD",
  "state": 0,
  "type": 0,
  "uploadState": 0,
  "version": "1.0.0",
  "video": 1
}
```

Field count: 41.

Key semantics observed in app bundle:
- `state` drives the printer-card text:
  - `0` → Idle
  - `1` → Printing
  - `2` → Printing Complete
  - `3` → Printing Failed
  - `4` → Print Abort
  - `5` → Printing Paused
- `deviceState`: `0` idle/standby, non-zero for active/self-check/upgrade states.
- `uploadState`: `0` idle, `2` Sending, `5` Waiting.
- `oldPrinter`: `false` for K2 Plus path; `true` would switch the app to a different legacy contract.
- `type`: `0` for this LAN printer.
- `connectType`: `1001` observed for LAN.

Data sources:
- Identity: `system_config.json` / `keybox`
- Live temps / progress: Moonraker `/printer/objects/query?print_stats&display_status&gcode_move&temperature&heater_bed&extruder`
- Targets: `/mnt/UDISK/creality/userdata/config/temperature_info.json`
- Active job filename/start: `/mnt/UDISK/creality/userdata/config/current_work_info.json`
- Live XYZ / fan: stock GUI pipe `/mnt/UDISK/creality/gui/config/pipe-*.json` (`code: key706`)

---

## 3. Live WebSocket channel

### Connection

The app opens a WebSocket on the `wssPort` returned by `/info` (443 in capture). The upgrade is a standard `Upgrade: websocket` handshake on `/`.

### Incoming messages from app

All observed messages use this JSON-RPC-like envelope:

```json
{"method": "get", "params": { ... }}
```

Observed `params` keys (sometimes grouped, sometimes sent alone):

1. Initial bundle:
   - `reqGcodeFile`
   - `reqGcodeList`
   - `reqMaterials`
   - `boxsInfo`
   - `boxConfig`
   - `getToken`

2. Material-bin query:
   - `materialBinStatus` with `{ "addr": -1 }`

3. Later individual requests:
   - `reqPrintObjects`
   - `reqHistory`
   - `reqElapseVideoList`

The current backend ignores the specific `params` and just pushes a generic status object every 2 s. The app appears to accept this and shows an idle card.

### Outgoing messages to app

The backend currently sends the unwrapped `result` object from the detail payload, plus a `timeStamp`:

```json
{
  "online": 1,
  "status": { ... moonraker status block ... },
  "model": "K2 Plus",
  "modelName": "K2 Plus",
  ...
  "boxsInfo": { ... },
  "boxConfig": { ... },
  "device": { ... duplicate of above fields ... },
  "timeStamp": "2026-08-05T..."
}
```

See the captured detail payload in `snapshots/20260804_153740/creality_probe_backend_debug.log` or request `http://127.0.0.1:9001/cxy/v1/status` when the backend is running.

Open questions for the minimal backend:
- Does the app need actual `reqGcodeList` / `reqHistory` / `reqElapseVideoList` data inside the WS response, or is the periodic status object sufficient?
- The old backend returns generic status; if the app file/history panels are empty, we may need to echo back keyed results.

---

## 4. Endpoints NOT required for basic LAN status

The following routes are implemented by the old backend but were **never hit** during the capture:

- `/api/v1/device/status`
- `/api/rest/print/cluster/...`
- `/cxy/v1/status`
- `/machine/system_info`
- `/machine/info`
- `/printer/objects/query`
- `/printer/print/start`
- `/api/cxy/v3/print/record/...`
- `/server/files/upload`
- `/downloads/original/current_print_image.png`
- `/call/webrtc_local` (handled by separate `webrtc_local_bridge.py` on port 8000)

For a minimal replacement, we should start with only:

1. `GET /info`
2. `GET /protocal.csp`
3. WebSocket status push

Then add routes only when a specific app feature fails.

---

## 5. Camera / video

The app requests video from `linuxVideoUrl`. In the captured contract this points to `http://3d.nrvous.io:8000/call/webrtc_local`. That path is already served by `printer/webrtc_local_bridge.py` (go2rtc adapter) and is outside the scope of the status backend.

---

## 6. Reproducing the capture

```bash
# On the Mac, clear app cache and add printer by IP:
./scripts/reset_creality_print_cache.sh --yes --no-launch
# Then open Creality Print and add 192.168.1.100 manually.

# On the printer, watch the backend log:
ssh root@192.168.1.100 'tail -f /tmp/creality_probe_backend_debug.log'
```
