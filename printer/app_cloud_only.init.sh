#!/bin/sh /etc/rc.common
# Cloud-capable subset of the stock app bundle without Monitor or web-server.

START=99
STOP=01
USE_PROCD=1
DEPEND=fstab,mcu_update

BIN_PATH=/usr/bin
APP_LIST="$BIN_PATH/master-server $BIN_PATH/audio-server $BIN_PATH/wifi-server \
          $BIN_PATH/app-server $BIN_PATH/display-server $BIN_PATH/upgrade-server"

USER_DATA_DIR=/mnt/UDISK
DELAY_IMAGE_VIDEO_DIR=$USER_DATA_DIR/creality/userdata/delay_image/video
FRONTEND_DOWNLOADS_DIR=/usr/share/frontend/downloads
HUMBNAIL_DIR=$USER_DATA_DIR/creality/local_gcode/humbnail
ORIGINAL_DIR=/tmp/creality/original
DEFDATA_DIR=/etc/sysConfig/defData
SYSTEM_VERSION_FILE=$USER_DATA_DIR/creality/userdata/config/system_version.json

sync_system_version() {
    [ -f "$SYSTEM_VERSION_FILE" ] || return

    system_version=$(fw_printenv version 2>/dev/null | sed -n 's/^version=//p' | head -n 1)
    case "$system_version" in
        ""|*[!0-9A-Za-z._-]*) return ;;
    esac

    reported_version=$(jsonfilter -i "$SYSTEM_VERSION_FILE" -e '@.sys_version' 2>/dev/null)
    [ "$reported_version" = "$system_version" ] && return

    version_tmp="${SYSTEM_VERSION_FILE}.tmp.$$"
    if sed "s/\(\"sys_version\"[[:space:]]*:[[:space:]]*\"\)[^\"]*\"/\1${system_version}\"/" \
        "$SYSTEM_VERSION_FILE" > "$version_tmp"; then
        cat "$version_tmp" > "$SYSTEM_VERSION_FILE"
    fi
    rm -f "$version_tmp"
}

create_dirs() {
    [ -d "$FRONTEND_DOWNLOADS_DIR" ] || mkdir -p "$FRONTEND_DOWNLOADS_DIR"
    [ -d "$HUMBNAIL_DIR" ] || mkdir -p "$HUMBNAIL_DIR"
    [ -d "$ORIGINAL_DIR" ] || mkdir -p "$ORIGINAL_DIR"
    [ -d "$DELAY_IMAGE_VIDEO_DIR" ] || mkdir -p "$DELAY_IMAGE_VIDEO_DIR"
    if [ -d "$FRONTEND_DOWNLOADS_DIR" ] && [ -d "$HUMBNAIL_DIR" ] \
        && [ -d "$ORIGINAL_DIR" ] && [ -d "$DEFDATA_DIR" ]; then
        ln -sf "$ORIGINAL_DIR" "$FRONTEND_DOWNLOADS_DIR"
        ln -sf "$HUMBNAIL_DIR" "$FRONTEND_DOWNLOADS_DIR"
        ln -sf "$DEFDATA_DIR" "$FRONTEND_DOWNLOADS_DIR"
        ln -sf "$DELAY_IMAGE_VIDEO_DIR" "$FRONTEND_DOWNLOADS_DIR"
    fi
}

start_app() {
    procd_open_instance
    procd_set_param env HOME=/root
    procd_set_param command "$1"
    procd_set_param respawn 3600 5 5
    procd_close_instance
}

start_service() {
    create_dirs
    sync_system_version

    if [ -e /tmp/.stress_test ]; then
        [ -x /usr/bin/factory_stress_test.sh ] && factory_stress_test.sh &
        touch /tmp/load_done
        return
    fi

    for app in $APP_LIST; do
        [ -x "$app" ] && start_app "$app"
    done
}

stop_service() {
    for app in $APP_LIST; do
        killall -9 "${app##*/}" 2>/dev/null || true
    done
}
