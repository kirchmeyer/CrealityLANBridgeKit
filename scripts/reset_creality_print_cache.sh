#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Creality Print"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Library/Application Support/creality_print_cache_backup}"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TS"
AUTO_YES=0
LAUNCH_AFTER_RESET=1

usage() {
  cat <<'EOF'
Usage: ./scripts/reset_creality_print_cache.sh [--yes] [--no-launch]

Options:
  --yes          skip the confirmation prompt
  --no-launch    do not relaunch Creality Print after resetting state
  --help         show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      AUTO_YES=1
      ;;
    --no-launch)
      LAUNCH_AFTER_RESET=0
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

mkdir -p "$BACKUP_DIR"

echo "This will:"
echo "  1) quit $APP_NAME if it is running"
echo "  2) back up the LAN/device cache state to $BACKUP_DIR"
echo "  3) reset the saved printer/device cache so the app rehydrates from the live backend"
echo "  4) relaunch the app unless --no-launch is supplied"
echo

if [[ "$AUTO_YES" -ne 1 ]]; then
  read -r -p "Continue? [y/N] " answer
  case "$answer" in
    [Yy]|[Yy][Ee][Ss]) ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
fi

# Quit the app if it is running.
osascript -e 'tell application "Creality Print" to quit' 2>/dev/null || true
sleep 1
pkill -f "Creality Print" 2>/dev/null || true

# Locate the app bundle if present.
APP_PATH=""
for candidate in \
  "/Applications/Creality Print.app" \
  "/Applications/CrealityPrint.app" \
  "/Applications/Creality Print*.app"; do
  if [ -d "$candidate" ]; then
    APP_PATH="$candidate"
    break
  fi
done

if [ -z "$APP_PATH" ]; then
  found=$(find /Applications -maxdepth 1 -type d -iname '*creality*print*.app' 2>/dev/null | head -n 1 || true)
  if [ -n "$found" ]; then
    APP_PATH="$found"
  fi
fi

if [ -z "$APP_PATH" ]; then
  echo "Could not locate Creality Print.app automatically."
  echo "Set APP_PATH in the script or install the app first."
  exit 1
fi

echo "Using app: $APP_PATH"

APP_SUPPORT_ROOT=""
for candidate in \
  "$HOME/Library/Application Support/Creality/Creality Print/7.0" \
  "$HOME/Library/Application Support/Creality/Creality Print"; do
  if [ -d "$candidate" ]; then
    APP_SUPPORT_ROOT="$candidate"
    break
  fi
done

if [ -z "$APP_SUPPORT_ROOT" ]; then
  echo "Could not locate Creality Print app support data automatically."
  exit 1
fi

backup_and_remove() {
  local target="$1"
  local rel_target
  rel_target="${target#$HOME/}"

  if [ -e "$target" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$rel_target")"
    cp -R "$target" "$BACKUP_DIR/$rel_target"
    rm -rf "$target"
    echo "Backed up and removed: $target"
  fi
}

reset_device_info() {
  local target="$1"
  backup_and_remove "$target"
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<'JSON'
{
  "current_device": null,
  "groups": []
}
JSON
  echo "Reset: $target"
}

reset_device_info "$APP_SUPPORT_ROOT/deviceInfo.json"

while IFS= read -r -d '' target; do
  backup_and_remove "$target"
done < <(find "$APP_SUPPORT_ROOT/user" -type f -name 'local_device' -print0 2>/dev/null)

echo "Cleared local Creality Print device cache state."
echo "Backup location: $BACKUP_DIR"

if [[ "$LAUNCH_AFTER_RESET" -eq 1 ]]; then
  echo "Launching $APP_NAME..."
  open -a "$APP_PATH"
fi

echo "Done."
