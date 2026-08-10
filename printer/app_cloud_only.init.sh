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
    procd_close_instance
}

start_service() {
    create_dirs

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
