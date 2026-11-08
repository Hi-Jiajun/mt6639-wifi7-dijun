#!/system/bin/sh
# Run from a root shell. Keep the helper outside adb_data_file: Binder/ART require
# the normal shell execution path. No service restart, interface creation or cfg write.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo requires_ksu_root >&2; exit 77; }
id | grep -q 'context=u:r:ksu:s0' || { echo requires_ksu_context >&2; exit 77; }
[ "$(getenforce)" = Enforcing ] || { echo requires_enforcing >&2; exit 78; }
[ "$(getprop ro.product.device)" = dijun ] || { echo wrong_device >&2; exit 78; }
[ -f "$HERE/sta-mode.dex" ] || { echo missing_sta_mode_dex >&2; exit 66; }
case ${1:-} in
    inspect|capabilities|selection-inspect) [ "$#" = 1 ] || exit 64 ;;
    compare-set) [ "$#" = 3 ] || exit 64 ;;
    selection-apply|selection-restore) [ "$#" = 2 ] || exit 64 ;;
    *) echo 'usage: sta_mode.sh inspect | capabilities | compare-set <expected 0..2> <target 0..2> | selection-inspect | selection-apply/selection-restore <task-dir>' >&2; exit 64 ;;
esac
exec timeout -k 2 12 env CLASSPATH="$HERE/sta-mode.dex:/apex/com.android.wifi/javalib/framework-wifi.jar" \
    /system/bin/app_process /system/bin WifiStaMode "$@"
