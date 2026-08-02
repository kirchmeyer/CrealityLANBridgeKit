#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <snapshot_dir>"
  echo "Example: $0 tools/creality_repro/snapshots/20260729_180000"
  exit 1
fi

SNAPSHOT_DIR="$1"
if [[ ! -d "$SNAPSHOT_DIR" ]]; then
  echo "Snapshot directory not found: $SNAPSHOT_DIR"
  exit 1
fi

APP_ROOT="/Applications/Creality Print.app/Contents/Resources/web"
SEND_DST="$APP_ROOT/sendToPrinterPage/assets/Bl5CvdKl.js"
DEVICE_DST="$APP_ROOT/deviceMgr/assets/C_bZROdP.js"

SEND_SRC="$SNAPSHOT_DIR/sendToPrinterPage/assets/Bl5CvdKl.js"
DEVICE_SRC="$SNAPSHOT_DIR/deviceMgr/assets/C_bZROdP.js"

if [[ ! -f "$SEND_SRC" ]]; then
  echo "Missing required file in snapshot: $SEND_SRC"
  exit 1
fi

BACKUP_TS="$(date +%Y%m%d_%H%M%S)"
cp "$SEND_DST" "$SEND_DST.pre_restore_$BACKUP_TS"
cp "$SEND_SRC" "$SEND_DST"

echo "Restored: $SEND_DST"
echo "Backup:   $SEND_DST.pre_restore_$BACKUP_TS"

if [[ -f "$DEVICE_SRC" && -f "$DEVICE_DST" ]]; then
  cp "$DEVICE_DST" "$DEVICE_DST.pre_restore_$BACKUP_TS"
  cp "$DEVICE_SRC" "$DEVICE_DST"
  echo "Restored: $DEVICE_DST"
  echo "Backup:   $DEVICE_DST.pre_restore_$BACKUP_TS"
fi

echo "Restoration complete; leaving the stock snapshot contents in place."
echo "Post-restore checksums:"
shasum "$SEND_DST"
if [[ -f "$DEVICE_DST" ]]; then
  shasum "$DEVICE_DST"
fi
