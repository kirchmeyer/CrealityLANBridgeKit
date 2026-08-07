#!/usr/bin/env bash
# Unified installer for the Creality LAN bridge stack.
#
# Usage:
#   ./install.sh install   [HOST] [USER] [OPTIONS]   # backup stock config, install/enable stack
#   ./install.sh restore   [HOST] [USER]             # restore stock config, disable our stack
#   ./install.sh uninstall [HOST] [USER]             # restore stock config and remove our files
#   ./install.sh sync      [HOST] [USER]             # push only changed files, minimal restart
#   ./install.sh status    [HOST] [USER]             # show sync status and service states
#   ./install.sh cert      [HOST] [USER] [CERT_DIR]  # install/update TLS certificate
#
# Options for install:
#   --public-host HOST      public hostname used by the app (default: $PUBLIC_HOST or printer.lan)
#   --cert-basename NAME    basename of cert/key files in /etc/nginx/conf.d (default: $CERT_BASENAME or self-signed)
#   --ecs-logging 0|1       enable/disable ECS JSON logging (default: 1)
#   --project-name NAME     prefix for backup manifest, service names, status path (default: $PROJECT_NAME or bridge)
#   --status-path PATH      URL path for the status page (default: $STATUS_PATH or $PROJECT_NAME-status)
#   --lan-mode open|proxy   open = plain HTTP LAN endpoints available (default);
#                           proxy = plain HTTP closed, app uses local HTTP proxy
#   --self-signed           generate a self-signed certificate if none exists
#
# Defaults: HOST=printer.lan, USER=root

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${2:-${PRINTER_HOST:-printer.lan}}"
USER="${3:-${PRINTER_USER:-root}}"
TARGET="${USER}@${HOST}"

PUBLIC_HOST="${PUBLIC_HOST:-printer.lan}"
CERT_BASENAME="${CERT_BASENAME:-self-signed}"
ECS_LOGGING="${ECS_LOGGING:-1}"
SELF_SIGNED="${SELF_SIGNED:-0}"
PROJECT_NAME="${PROJECT_NAME:-bridge}"
STATUS_PATH="${STATUS_PATH:-}"
LAN_MODE="${LAN_MODE:-open}"
NGINX_LOG_FORMAT="ecs_access"

# Derived paths are recomputed after CLI/env parsing in apply_derived_paths().
BACKUP_MANIFEST=""
BACKUP_SUFFIX=""

apply_derived_paths() {
    # STATUS_PATH defaults to ${PROJECT_NAME}-status if not otherwise set.
    if [[ -z "$STATUS_PATH" ]]; then
        STATUS_PATH="${PROJECT_NAME}-status"
    fi
    BACKUP_MANIFEST=/etc/${PROJECT_NAME}_backup_manifest.json
    BACKUP_SUFFIX=".bak.${PROJECT_NAME}"
}
apply_derived_paths

# Canonical services we manage.
OUR_SERVICES=(lan_bridge go2rtc status_page creality_mdns webrtc_local_bridge watchdog)
STOCK_SERVICES_TO_DISABLE=(app mjpeg_server)

SSH_KEY="${SSH_KEY:-}"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS+=( -o "IdentityFile=${SSH_KEY}" )
fi

usage() {
    cat <<EOF
Unified installer for the Creality LAN bridge stack.

Usage:
  $0 install   [HOST] [USER] [OPTIONS]  backup stock config, install and enable stack
  $0 restore   [HOST] [USER]            restore stock config, disable our stack
  $0 uninstall [HOST] [USER]            restore stock config and remove our files
  $0 sync      [HOST] [USER]            push only changed files, minimal service restart
  $0 status    [HOST] [USER]            show sync status and running services
  $0 cert      [HOST] [USER] [CERT_DIR] install/update TLS certificate from CERT_DIR

Install options:
  --public-host HOST     public hostname used by the app (default: \$PUBLIC_HOST or printer.lan)
  --cert-basename NAME   basename of cert/key files in /etc/nginx/conf.d (default: \$CERT_BASENAME or self-signed)
  --ecs-logging 0|1      enable/disable ECS JSON logging (default: 1)
  --project-name NAME    prefix for backup manifest, service names, status path (default: \$PROJECT_NAME or bridge)
  --status-path PATH     URL path for the status page (default: \$STATUS_PATH or \$PROJECT_NAME-status)
  --lan-mode open|proxy  open = plain HTTP LAN endpoints available (default);
                         proxy = plain HTTP closed, app uses local HTTP proxy
  --self-signed          generate a self-signed certificate if none exists

Environment defaults:
  PRINTER_HOST (default: printer.lan)
  PRINTER_USER (default: root)
  PUBLIC_HOST (default: printer.lan)
  CERT_BASENAME (default: self-signed)
  ECS_LOGGING (default: 1)
  PROJECT_NAME (default: bridge)
  STATUS_PATH (default: \$PROJECT_NAME-status)
  LAN_MODE (default: open)
  SSH_KEY (default: unset; set to an explicit private key path if needed)
EOF
}

ssh_cmd() {
    ssh "${SSH_OPTS[@]}" "$TARGET" "$@"
}

log() {
    echo "[install] $*"
}

fail() {
    echo "[install] ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Backup / restore primitives
# ---------------------------------------------------------------------------

backup_stock_config() {
    log "checking stock configuration backups on ${TARGET}"
    ssh_cmd '
        set -e
        mkdir -p /etc/'"$PROJECT_NAME"'
        if [ -f "'"$BACKUP_MANIFEST"'" ]; then
            echo "backup manifest already exists; skipping backup"
            exit 0
        fi

        # If nginx is already our config, prefer an existing stock .bak if present.
        nginx_orig=/etc/nginx/nginx.conf
        nginx_backup="${nginx_orig}'"$BACKUP_SUFFIX"'"
        if grep -q "# PROJECT_NAME=" "$nginx_orig" 2>/dev/null && [ -f /etc/nginx/nginx.conf.bak ]; then
            echo "detected existing '"$PROJECT_NAME"' nginx config; using /etc/nginx/nginx.conf.bak as stock backup"
            cp -f /etc/nginx/nginx.conf.bak "$nginx_backup"
        else
            cp -f "$nginx_orig" "$nginx_backup"
        fi

        echo "{" > "'"$BACKUP_MANIFEST"'"
        echo "  \"created\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"," >> "'"$BACKUP_MANIFEST"'"
        echo "  \"files\": {" >> "'"$BACKUP_MANIFEST"'"
        first=1
        printf "    \"%s\": \"%s\"" "$nginx_orig" "$nginx_backup" >> "'"$BACKUP_MANIFEST"'"
        first=0
        for f in /etc/init.d/app /etc/init.d/webrtc /etc/init.d/mjpeg_server; do
            if [ -f "$f" ]; then
                backup="${f}'"$BACKUP_SUFFIX"'"
                cp -f "$f" "$backup"
                [ "$first" -eq 1 ] || echo "," >> "'"$BACKUP_MANIFEST"'"
                printf "    \"%s\": \"%s\"" "$f" "$backup" >> "'"$BACKUP_MANIFEST"'"
                first=0
            fi
        done
        echo "" >> "'"$BACKUP_MANIFEST"'"
        echo "  }" >> "'"$BACKUP_MANIFEST"'"
        echo "}" >> "'"$BACKUP_MANIFEST"'"
        echo "created backup manifest '"$BACKUP_MANIFEST"'"
    '
}

restore_stock_config() {
    log "restoring stock configuration on ${TARGET}"
    ssh_cmd '
        set -e
        python3 - <<"PY"
import json, os, shutil, sys

manifest_path = "'"$BACKUP_MANIFEST"'"
backup_suffix = "'"$BACKUP_SUFFIX"'"

# Prefer the backup manifest if it exists; otherwise fall back to /rom originals.
if os.path.isfile(manifest_path):
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    files = manifest.get("files", {})
else:
    print("WARNING: no backup manifest found; falling back to /rom stock files")
    files = {
        "/etc/nginx/nginx.conf": "/rom/etc/nginx/nginx.conf",
        "/etc/init.d/app": "/rom/etc/init.d/app",
        "/etc/init.d/webrtc": "/rom/etc/init.d/webrtc",
    }

for original, backup in files.items():
    src = backup
    if not os.path.isfile(src):
        # If our backup is missing but /rom has a stock copy, use that.
        rom_src = f"/rom{original}"
        if os.path.isfile(rom_src):
            src = rom_src
        else:
            print(f"WARNING: cannot restore {original}: neither {backup} nor {rom_src} exists", file=sys.stderr)
            continue
    try:
        shutil.copy2(src, original)
        print(f"restored {original} from {src}")
    except FileNotFoundError as e:
        print(f"WARNING: missing source for {original}: {e}", file=sys.stderr)
PY
        rm -f /etc/nginx/conf.d/creality.compat.locations.conf \
              /etc/nginx/conf.d/creality.lan.locations.conf \
              /etc/nginx/conf.d/creality.lan.websocket.conf \
              /etc/nginx/conf.d/ecs-log-format.conf
    '
}

remove_backup_manifest() {
    ssh_cmd "rm -f ${BACKUP_MANIFEST}"
}

# ---------------------------------------------------------------------------
# File deployment
# ---------------------------------------------------------------------------

apply_config_templates() {
    log "applying configuration templates (public_host=${PUBLIC_HOST}, cert=${CERT_BASENAME}, ecs=${ECS_LOGGING}, lan_mode=${LAN_MODE})"
    NGINX_LOG_FORMAT="$([ "$ECS_LOGGING" = "1" ] && echo "ecs_access" || echo "plain_access")"
    STAGE_DIR="${SCRIPT_DIR}/.install-staging"
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR/printer"
    # Copy all printer files into the staging directory so we can substitute
    # placeholders without mutating the repo originals.
    cp -r "${SCRIPT_DIR}/printer" "$STAGE_DIR/"
    for f in printer/lan_bridge.init.sh printer/status_page.init.sh printer/watchdog.init.sh printer/go2rtc_init.sh printer/mjpeg_server.init.sh printer/creality_mdns.init.sh printer/webrtc_local_bridge.init.sh printer/nginx.frontdoor.conf printer/nginx.http.open.conf printer/nginx.http.proxy.conf; do
        local staged="${STAGE_DIR}/${f}"
        sed -i.bak -e "s|%PUBLIC_HOST%|${PUBLIC_HOST}|g" \
            -e "s|%CERT_BASENAME%|${CERT_BASENAME}|g" \
            -e "s|%NGINX_LOG_FORMAT%|${NGINX_LOG_FORMAT}|g" \
            -e "s|%ECS_LOGGING%|${ECS_LOGGING}|g" \
            -e "s|%STATUS_PATH%|${STATUS_PATH}|g" \
            -e "s|%PROJECT_NAME%|${PROJECT_NAME}|g" \
            -e "s|%LAN_MODE%|${LAN_MODE}|g" \
            "$staged"
        rm -f "$staged.bak"
    done

    # Select the HTTP server block based on LAN_MODE. In "open" mode the
    # Creality desktop app can add the printer by IP and use plain HTTP. In
    # "proxy" mode plain HTTP is closed and the app must be pointed at the
    # local HTTP proxy running on the client machine.
    local nginx_staged="${STAGE_DIR}/printer/nginx.frontdoor.conf"
    local http_fragment="${STAGE_DIR}/printer/nginx.http.${LAN_MODE}.conf"
    if [[ ! -f "$http_fragment" ]]; then
        fail "unknown LAN_MODE: ${LAN_MODE} (expected open or proxy)"
    fi
    python3 - <<PY
import pathlib
nginx = pathlib.Path("${nginx_staged}")
fragment = pathlib.Path("${http_fragment}")
nginx.write_text(nginx.read_text().replace("# LAN_MODE_HTTP_SERVER_PLACEHOLDER", fragment.read_text()))
PY
    rm -f "$http_fragment"
}

sync_files() {
    log "syncing files to ${TARGET}"
    python3 "${SCRIPT_DIR}/scripts/check_local_remote_sync.py" --host "$HOST" --user "$USER" --sync --local-dir "$STAGE_DIR"
}

set_permissions() {
    log "setting executable permissions on ${TARGET}"
    ssh_cmd '
        set -e
        chmod +x /usr/local/bin/lan_bridge.py \
                  /usr/local/bin/mjpeg_server.py \
                  /usr/local/bin/cam_delivery_bridge.py \
                  /usr/local/bin/status_page.py \
                  /usr/local/bin/creality_mdns_announcer.py \
                  /usr/local/bin/webrtc_local_bridge.py \
                  /usr/local/bin/watchdog.sh \
                  /usr/local/bin/restart_cam_stack.sh
        chmod +x /etc/init.d/lan_bridge \
                  /etc/init.d/go2rtc \
                  /etc/init.d/mjpeg_server \
                  /etc/init.d/status_page \
                  /etc/init.d/creality_mdns \
                  /etc/init.d/webrtc_local_bridge \
                  /etc/init.d/watchdog
    '
}

# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

install_services() {
    log "configuring services on ${TARGET}"
    ssh_cmd '
        set -e

        # Install the webrtc_local wrapper that coordinates with the stock app-server.
        if [ -f /usr/local/bin/webrtc_local_wrapper.sh ]; then
            cp -f /usr/local/bin/webrtc_local_wrapper.sh /usr/bin/webrtc_local
            chmod +x /usr/bin/webrtc_local
        fi

        # Disable old probe backend if present.
        if [ -f /etc/init.d/probe_backend ]; then
            /etc/init.d/probe_backend stop 2>/dev/null || true
            /etc/init.d/probe_backend disable 2>/dev/null || true
            rm -f /etc/init.d/probe_backend /usr/local/bin/creality_probe_backend.py \
                  /etc/nginx/conf.d/creality.compat.locations.conf
        fi

        # Remove obsolete cloud-bridge artifacts.
        rm -f /usr/local/bin/cloud_webrtc_bridge.py \
              /usr/local/bin/cloud_webrtc_feeder.sh \
              /usr/local/bin/uvc_ffmpeg_feeder.sh \
              /etc/init.d/webrtc_bridge 2>/dev/null || true

        # Stop and disable the stock app bundle so it does not reclaim 80/443.
        /etc/init.d/app stop 2>/dev/null || true
        /etc/init.d/app disable 2>/dev/null || true
        killall -9 web-server 2>/dev/null || true

        # mjpeg_server and stock webrtc are managed by go2rtc wrapper; disable at boot.
        if [ -f /etc/init.d/mjpeg_server ]; then
            /etc/init.d/mjpeg_server stop 2>/dev/null || true
            /etc/init.d/mjpeg_server disable 2>/dev/null || true
        fi
        if [ -f /etc/init.d/webrtc ]; then
            /etc/init.d/webrtc disable 2>/dev/null || true
        fi

        # Disable legacy example-named services if migrating from an older install.
        # These names are just one possible value of PROJECT_NAME from a prior install.
        for legacy in example_status_page example_watchdog; do
            if [ -f /etc/init.d/$legacy ]; then
                /etc/init.d/$legacy stop 2>/dev/null || true
                /etc/init.d/$legacy disable 2>/dev/null || true
                rm -f /etc/init.d/$legacy
            fi
        done

        # Enable our services.
        /etc/init.d/lan_bridge enable
        /etc/init.d/go2rtc enable
        /etc/init.d/status_page enable
        /etc/init.d/creality_mdns enable
        /etc/init.d/webrtc_local_bridge enable
        /etc/init.d/watchdog enable
    '
}

restart_our_stack() {
    log "restarting services on ${TARGET}"
    ssh_cmd '
        set -e
        /etc/init.d/nginx restart
        nginx -t
        /etc/init.d/lan_bridge restart
        /etc/init.d/go2rtc restart
        /etc/init.d/status_page restart
        /etc/init.d/creality_mdns restart
        /etc/init.d/webrtc_local_bridge restart
        /etc/init.d/watchdog restart
        sleep 2
    '
}

stop_and_disable_our_stack() {
    log "stopping and disabling our stack on ${TARGET}"
    local svc
    for svc in "${OUR_SERVICES[@]}"; do
        ssh_cmd "/etc/init.d/${svc} stop 2>/dev/null || true; /etc/init.d/${svc} disable 2>/dev/null || true"
    done
}

restore_stock_services() {
    log "restoring stock services on ${TARGET}"
    ssh_cmd '
        set -e
        /etc/init.d/app enable
        /etc/init.d/app start 2>/dev/null || true
        /etc/init.d/nginx restart
        sleep 1
    '
}

remove_our_files() {
    log "removing our files from ${TARGET}"
    ssh_cmd '
        set -e
        for f in /usr/local/bin/lan_bridge.py \
                 /usr/local/bin/mjpeg_server.py \
                 /usr/local/bin/cam_delivery_bridge.py \
                 /usr/local/bin/status_page.py \
                 /usr/local/bin/creality_mdns_announcer.py \
                 /usr/local/bin/webrtc_local_bridge.py \
                 /usr/local/bin/watchdog.sh \
                 /usr/local/bin/restart_cam_stack.sh; do
            rm -f "$f"
        done
        for f in /etc/init.d/lan_bridge \
                 /etc/init.d/go2rtc \
                 /etc/init.d/mjpeg_server \
                 /etc/init.d/status_page \
                 /etc/init.d/creality_mdns \
                 /etc/init.d/webrtc_local_bridge \
                 /etc/init.d/watchdog; do
            rm -f "$f"
        done
        rm -f /usr/bin/webrtc_local \
              /etc/nginx/conf.d/creality.lan.locations.conf \
              /etc/nginx/conf.d/creality.lan.websocket.conf \
              /etc/nginx/conf.d/ecs-log-format.conf

        # Legacy example-named files from older installs.
        # These names are just one possible value of PROJECT_NAME from a prior install.
        rm -f /usr/local/bin/example_status_page.py \
              /usr/local/bin/example_watchdog.sh \
              /etc/init.d/example_status_page \
              /etc/init.d/example_watchdog
    '
}

# ---------------------------------------------------------------------------
# Certificate management
# ---------------------------------------------------------------------------

ensure_certificate() {
    log "checking TLS certificate on ${TARGET}"
    local has_cert
    has_cert=$(ssh_cmd 'basename="'"$CERT_BASENAME"'"; crt=/etc/nginx/conf.d/$basename.crt; key=/etc/nginx/conf.d/$basename.key; if [ -f "$crt" ] && [ -f "$key" ]; then echo yes; else echo no; fi')
    if [[ "$has_cert" == "yes" ]]; then
        log "certificate ${CERT_BASENAME} already installed"
        return 0
    fi
    if [[ "$SELF_SIGNED" != "1" ]]; then
        fail "certificate not found: /etc/nginx/conf.d/${CERT_BASENAME}.crt / ${CERT_BASENAME}.key
Run with --self-signed to generate one, or use ./install.sh cert [HOST] [USER] [CERT_DIR] to install real certs."
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        fail "openssl is required to generate a self-signed certificate locally"
    fi

    local tmpdir cert_dir
    tmpdir=$(mktemp -d)
    cert_dir="$tmpdir/$CERT_BASENAME"
    mkdir -p "$cert_dir"
    log "generating self-signed certificate for ${PUBLIC_HOST}"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -subj "/CN=${PUBLIC_HOST}" \
        -keyout "$cert_dir/${CERT_BASENAME}.key" \
        -out "$cert_dir/${CERT_BASENAME}.crt" 2>/dev/null

    log "installing generated certificate on ${TARGET}"
    scp -O "${SSH_OPTS[@]}" "$cert_dir/${CERT_BASENAME}.crt" "$TARGET:/etc/nginx/conf.d/${CERT_BASENAME}.crt"
    scp -O "${SSH_OPTS[@]}" "$cert_dir/${CERT_BASENAME}.key" "$TARGET:/etc/nginx/conf.d/${CERT_BASENAME}.key"
    ssh_cmd 'chmod 600 /etc/nginx/conf.d/'"${CERT_BASENAME}"'.key'
    rm -rf "$tmpdir"
    log "self-signed certificate installed"
}

cmd_cert() {
    if [[ ! -d "$CERT_DIR" ]]; then
        fail "certificate directory not found: $CERT_DIR"
    fi
    local cert_dir
    cert_dir="$(cd "$CERT_DIR" && pwd)"
    if [[ ! -f "$cert_dir/${CERT_BASENAME}.crt" ]] || [[ ! -f "$cert_dir/${CERT_BASENAME}.key" ]]; then
        fail "missing certificate files in $cert_dir: ${CERT_BASENAME}.crt / ${CERT_BASENAME}.key"
    fi
    log "installing certificate from $cert_dir to ${TARGET}"
    scp -O "${SSH_OPTS[@]}" "$cert_dir/${CERT_BASENAME}.crt" "$TARGET:/etc/nginx/conf.d/${CERT_BASENAME}.crt"
    scp -O "${SSH_OPTS[@]}" "$cert_dir/${CERT_BASENAME}.key" "$TARGET:/etc/nginx/conf.d/${CERT_BASENAME}.key"
    ssh_cmd 'chmod 600 /etc/nginx/conf.d/'"${CERT_BASENAME}"'.key; nginx -t && (/etc/init.d/nginx reload || /etc/init.d/nginx restart)'
    log "certificate installed and nginx reloaded"
}

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

verify_endpoints() {
    log "running endpoint contract check"
    if [[ "$LAN_MODE" == "proxy" ]]; then
        # In proxy mode plain HTTP redirects to HTTPS; verify via HTTPS using the
        # public host (certificate may be self-signed, so hostname verification
        # is skipped). The desktop app uses a local HTTP proxy in this mode.
        python3 "${SCRIPT_DIR}/scripts/endpoint_contract_check.py" --host "$PUBLIC_HOST" --scheme https --port 443 --skip-upload
    else
        python3 "${SCRIPT_DIR}/scripts/endpoint_contract_check.py" --host "$HOST" --port 80 --skip-upload
    fi
}

show_status() {
    log "sync status:"
    python3 "${SCRIPT_DIR}/scripts/check_local_remote_sync.py" --host "$HOST" --user "$USER" --local-dir "$STAGE_DIR"
    log "service states:"
    ssh_cmd '
        for svc in lan_bridge go2rtc status_page creality_mdns webrtc_local_bridge watchdog app nginx; do
            if [ -f "/etc/init.d/$svc" ]; then
                status=$(/etc/init.d/"$svc" enabled 2>/dev/null && echo enabled || echo disabled)
                pid=$(pgrep -f "$svc" 2>/dev/null | head -1 || true)
                printf "%-24s %-10s %s\n" "$svc" "$status" "${pid:--}"
            fi
        done
        echo
        echo "listeners:"
        netstat -tlnp 2>/dev/null | grep -E ":80 |:443 |:4408 |:8000 |:8081 |:8554 |:8765 |:9002 " || true
    '
}

# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

cmd_install() {
    log "installing ${PROJECT_NAME} bridge stack on ${TARGET}"
    backup_stock_config
    apply_config_templates
    ensure_certificate
    sync_files
    set_permissions
    install_services
    restart_our_stack
    verify_endpoints
    log "install complete"
}

cmd_restore() {
    log "restoring stock Creality stack on ${TARGET}"
    stop_and_disable_our_stack
    restore_stock_config
    restore_stock_services
    log "restore complete (our files are still installed; run uninstall to remove them)"
}

cmd_uninstall() {
    log "uninstalling ${PROJECT_NAME} bridge stack from ${TARGET}"
    stop_and_disable_our_stack
    restore_stock_config
    remove_our_files
    restore_stock_services
    remove_backup_manifest
    log "uninstall complete"
}

cmd_sync() {
    log "syncing files only on ${TARGET}"
    apply_config_templates
    sync_files
    set_permissions
    restart_our_stack
    log "sync complete"
}

cmd_status() {
    apply_config_templates
    show_status
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_common_args() {
    # Remaining args after HOST and USER are options. Caller has set HOST, USER, TARGET already.
    : # no-op placeholder
}

parse_options() {
    # Parse options shared by install, sync, and status.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --public-host)
                PUBLIC_HOST="$2"
                shift 2
                ;;
            --cert-basename)
                CERT_BASENAME="$2"
                shift 2
                ;;
            --ecs-logging)
                ECS_LOGGING="$2"
                if [[ "$ECS_LOGGING" != "0" && "$ECS_LOGGING" != "1" ]]; then
                    fail "--ecs-logging must be 0 or 1"
                fi
                shift 2
                ;;
            --self-signed)
                SELF_SIGNED=1
                shift
                ;;
            --project-name)
                PROJECT_NAME="$2"
                apply_derived_paths
                shift 2
                ;;
            --status-path)
                STATUS_PATH="$2"
                apply_derived_paths
                shift 2
                ;;
            --lan-mode)
                LAN_MODE="$2"
                if [[ "$LAN_MODE" != "open" && "$LAN_MODE" != "proxy" ]]; then
                    fail "--lan-mode must be open or proxy"
                fi
                shift 2
                ;;
            *)
                fail "unknown option: $1"
                ;;
        esac
    done
}

parse_install_args() {
    shift 3  # drop command, host, user
    parse_options "$@"
}

parse_sync_args() {
    shift 3  # drop command, host, user
    parse_options "$@"
}

parse_status_args() {
    shift 3  # drop command, host, user
    parse_options "$@"
}

parse_cert_args() {
    HOST="${2:-${PRINTER_HOST:-printer.lan}}"
    USER="${3:-${PRINTER_USER:-root}}"
    TARGET="${USER}@${HOST}"
    CERT_DIR="${4:-./certs}"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMAND="${1:-}"
case "$COMMAND" in
    install)
        parse_install_args "$@"
        cmd_install
        ;;
    restore)
        cmd_restore
        ;;
    uninstall)
        cmd_uninstall
        ;;
    sync)
        parse_sync_args "$@"
        cmd_sync
        ;;
    status)
        parse_status_args "$@"
        cmd_status
        ;;
    cert)
        parse_cert_args "$@"
        cmd_cert
        ;;
    -h|--help|help|"")
        usage
        exit 0
        ;;
    *)
        echo "Unknown command: $COMMAND" >&2
        usage
        exit 1
        ;;
esac
