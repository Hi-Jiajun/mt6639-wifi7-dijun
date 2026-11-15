#!/system/bin/sh
# 只读检查：当前状态、版本锁、是否挂载、原厂哈希是否完好。
# 只输出事实，不做任何修改。
set -u
MODDIR=${1:-${MODDIR:-/data/adb/modules/mt6639-6g}}
. "$MODDIR/bin/lib.sh"

echo "boot=$(boot_id)"
echo "kernel=$(uname -r)"
echo "compatibility_profile=$COMPAT_PROFILE"
echo "compatibility_error=${COMPAT_ERROR:-none}"
echo "ko_path=$STOCK_KO"
echo "uid=$(id -u)"
echo "enforce=$(cat /sys/fs/selinux/enforce 2>/dev/null)"
echo "cfg_sha=$(file_sha "$CFG")"
echo "cfg_stock=$([ "$(file_sha "$CFG")" = "$STOCK_CFG_SHA" ] && echo yes || echo no)"
echo "ko_sha=$(file_sha "$STOCK_KO")"
echo "ko_stock=$([ "$(file_sha "$STOCK_KO")" = "$STOCK_KO_SHA" ] && echo yes || echo no)"
echo "bind_mounted=$(grep -c " $CFG " "$MOUNTINFO_FILE" 2>/dev/null)"
echo "state=$(cat "$STATE" 2>/dev/null || echo none)"
echo "enabled_config=$(grep -E '^ENABLE_6G=' "$MODDIR/config.conf" 2>/dev/null | sed 's/.*:-\([0-9]*\)}.*/\1/; s/^ENABLE_6G=//')"
echo "country=$(wifi get-country-code 2>&1 | tr -d '\r')"
echo "wifi_enabled=$(sh_shell 'settings get global wifi_on' | tr -d '\r')"
echo "scan_always=$(sh_shell 'settings get global wifi_scan_always_enabled' | tr -d '\r')"
active=$(cat "$ACTIVE_TX_FILE" 2>/dev/null)
echo "active_tx=${active:-none}"
if [ -f "$NATIVE" ]; then
    sh "$MODDIR/bin/native.sh" "$MODDIR" inspect 2>/dev/null || echo 'native_inspect=FAILED'
else
    echo 'native_inspect=missing'
fi
