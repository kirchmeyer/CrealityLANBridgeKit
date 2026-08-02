#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/Applications/Creality Print.app/Contents/Resources/web"
TARGET_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/tools/creality_repro/snapshots"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$TARGET_ROOT/$TS"

SEND_JS="$APP_ROOT/sendToPrinterPage/assets/Bl5CvdKl.js"
DEVICE_JS="$APP_ROOT/deviceMgr/assets/C_bZROdP.js"

mkdir -p "$OUT_DIR/sendToPrinterPage/assets"
mkdir -p "$OUT_DIR/deviceMgr/assets"

cp "$SEND_JS" "$OUT_DIR/sendToPrinterPage/assets/Bl5CvdKl.js"
if [[ -f "$DEVICE_JS" ]]; then
  cp "$DEVICE_JS" "$OUT_DIR/deviceMgr/assets/C_bZROdP.js"
fi

{
  echo "timestamp=$TS"
  echo "source_send_js=$SEND_JS"
  echo "source_device_js=$DEVICE_JS"
  echo
  echo "checksums:"
  shasum "$OUT_DIR/sendToPrinterPage/assets/Bl5CvdKl.js"
  if [[ -f "$OUT_DIR/deviceMgr/assets/C_bZROdP.js" ]]; then
    shasum "$OUT_DIR/deviceMgr/assets/C_bZROdP.js"
  fi
} > "$OUT_DIR/manifest.txt"

echo "Export complete: $OUT_DIR"
echo "Manifest: $OUT_DIR/manifest.txt"
