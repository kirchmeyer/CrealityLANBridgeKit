#!/usr/bin/env bash
set -euo pipefail

APP_JS="/Applications/Creality Print.app/Contents/Resources/web/sendToPrinterPage/assets/Bl5CvdKl.js"

if [[ ! -f "$APP_JS" ]]; then
  echo "App JS not found: $APP_JS"
  exit 1
fi

echo "App bundle checksum:"
shasum "$APP_JS"

echo
echo "Patch markers:"
python3 - <<'PY'
from pathlib import Path
s=Path('/Applications/Creality Print.app/Contents/Resources/web/sendToPrinterPage/assets/Bl5CvdKl.js').read_text(errors='ignore')
checks={
  'start-print send_gcode forced': 't.isGcodeFile)return n.sendGcodeFile(T);return n.sendGcodeFile(T)',
  'send-only send_gcode path': 'setTimeout(()=>{o.sendGcodeFile({address:String(e.selectDevice.address||"").split("(")[0].trim(),plateIndex:z,fileName:w,oldPrinter:e.selectDevice.oldPrinter,moonrakerPort:Number(e.selectDevice.moonrakerPort)||80,uploadTaskId:e.selectDevice.uploadTaskId})},80);e.isOnlySend=!0',
  'address sanitized in jump': 'n.jumpToDeviceDetail(String(E.address||"").split("(")[0].trim(),E.name,!0),e.openModal=!1',
}
for k,v in checks.items():
  print(f'- {k}:', 'OK' if v in s else 'MISSING')
PY
