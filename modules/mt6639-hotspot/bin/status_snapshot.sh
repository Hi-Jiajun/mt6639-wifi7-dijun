#!/system/bin/sh
# Publish a non-sensitive, read-only hotspot state snapshot for Settings.
set -u
umask 022
MODDIR=${1:-/data/adb/modules/mt6639-hotspot}
OP=${2:-publish}
case "$MODDIR" in /data/adb/modules/mt6639-hotspot) ;; *) exit 64 ;; esac
[ "$(id -u)" = 0 ] || exit 77
# Public snapshot path: /data/misc/mt6639-hotspot/status
STATUS_DIR=/data/misc/mt6639-hotspot
STATUS_FILE=$STATUS_DIR/status
STATUS_TMP=$STATUS_DIR/.status.$$
trap 'rm -f "$STATUS_TMP"' EXIT HUP INT TERM

case "$OP" in
    init)
        if [ ! -d "$MODDIR" ] || [ -L "$MODDIR" ] ||
            [ -e "$MODDIR/disable" ] || [ -e "$MODDIR/remove" ]; then
            state=disabled
        else
            state=idle
        fi
        band=none
        width=none
        ;;
    publish)
        state=${3:-unresolved}
        band=${4:-none}
        width=${5:-none}
        ;;
    *) exit 64 ;;
esac
case "$state" in idle|starting|active|stopping|unresolved|disabled) ;; *) exit 64 ;; esac
case "$band" in none|2g|5g|6g) ;; *) exit 64 ;; esac
case "$width" in none|20|40|80|160|320) ;; *) exit 64 ;; esac

boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) && [ -n "$boot" ] || exit 70
private_file_ok() {
    path=$1
    [ -f "$path" ] && [ ! -L "$path" ] &&
        [ "$(stat -c %u "$path")" = 0 ] &&
        [ "$(stat -c %a "$path")" = 600 ]
}
private=/data/adb/mt6639-hotspot/private
ap_key=0
reconnect_6g=0
reconnect_2g=0
private_file_ok "$private/ap.key" && ap_key=1
private_file_ok "$private/reconnect.key" && reconnect_6g=1
private_file_ok "$private/reconnect-2g.key" && reconnect_2g=1
enforcing=0
[ "$(getenforce 2>/dev/null)" = Enforcing ] && enforcing=1
ready_6g=0
ready_5g=0
ready_2g=0
local_ready=0
if [ "$ap_key" = 1 ] && [ "$enforcing" = 1 ]; then
    wifi_status=$(timeout 5 cmd wifi status 2>/dev/null || true)
    if printf '%s\n' "$wifi_status" | grep -Fq 'Wifi is connected to "' &&
        printf '%s\n' "$wifi_status" | grep -Fq 'Supplicant state: COMPLETED'; then
        sta_freq=$(printf '%s\n' "$wifi_status" |
            sed -n 's/.*Frequency: \([0-9][0-9]*\)MHz.*/\1/p' | head -1)
        case "$sta_freq" in ''|*[!0-9]*) sta_freq=0 ;; esac
        if [ "$sta_freq" -ge 5950 ] && [ "$sta_freq" -le 7125 ]; then
            ready_6g=1
            ready_2g=1
        elif [ "$sta_freq" -ge 2400 ] && [ "$sta_freq" -le 2484 ]; then
            ready_5g=1
        elif [ "$sta_freq" -ge 5150 ] && [ "$sta_freq" -le 5895 ]; then
            ready_2g=1
        fi
    elif printf '%s\n' "$wifi_status" | grep -Fxq 'Wifi is enabled'; then
        ready_6g=1
        ready_5g=1
        ready_2g=1
        local_ready=1
    fi
fi
[ -d /data/misc ] && [ ! -L /data/misc ] || exit 77
if [ ! -e "$STATUS_DIR" ]; then
    mkdir "$STATUS_DIR" || exit 74
fi
[ -d "$STATUS_DIR" ] && [ ! -L "$STATUS_DIR" ] &&
    [ "$(stat -c %u "$STATUS_DIR")" = 0 ] || exit 77
chmod 0755 "$STATUS_DIR" || exit 74

printf 'schema=mt6639-hotspot-status-v4\nstate=%s\nband=%s\nwidth=%s\nboot=%s\nap_key=%s\nreconnect_6g=%s\nreconnect_2g=%s\nenforcing=%s\nready_6g=%s\nready_5g=%s\nready_2g=%s\nlocal_ready=%s\n' \
    "$state" "$band" "$width" "$boot" "$ap_key" "$reconnect_6g" \
    "$reconnect_2g" "$enforcing" "$ready_6g" "$ready_5g" "$ready_2g" "$local_ready" > "$STATUS_TMP" || exit 74
chmod 0644 "$STATUS_TMP" || exit 74
if command -v restorecon >/dev/null 2>&1; then
    restorecon -F "$STATUS_TMP" >/dev/null 2>&1 || exit 77
fi
mv "$STATUS_TMP" "$STATUS_FILE" || exit 74
trap - EXIT HUP INT TERM
echo "snapshot=$STATUS_FILE state=$state band=$band width=$width boot=$boot ap_key=$ap_key reconnect_6g=$reconnect_6g reconnect_2g=$reconnect_2g enforcing=$enforcing ready_6g=$ready_6g ready_5g=$ready_5g ready_2g=$ready_2g local_ready=$local_ready"
