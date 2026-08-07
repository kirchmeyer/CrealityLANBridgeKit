#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_SCRIPT="$ROOT_DIR/scripts/deploy_mdns_announcer.py"

if [[ ! -f "$SOURCE_SCRIPT" ]]; then
    echo "Missing deploy script: $SOURCE_SCRIPT"
    exit 1
fi

python3 "$SOURCE_SCRIPT" "$@"
