#!/system/bin/sh
# On-device MT6639 session helper for zero-Python portable remote kits.
# Runs strictly under KernelSU root (uid=0, u:r:ksu:s0) with SELinux Enforcing.
# Never edits checksummed config.conf, never prints SSID/BSSID/MAC/IP, never reboots.
set -u
umask 077

OP=${1:-}
ARG2=${2:-}
ARG3=${3:-}

MOD_6G=/data/adb/modules/mt6639-6g
MOD_BT=/data/adb/modules/mt6639-bt
MOD_HS=/data/adb/modules/mt6639-hotspot

fail() {
    echo "ERROR $1: $2" >&2
    exit "${3:-2}"
}

require_ksu_enforcing() {
    uid=$(id -u 2>/dev/null || echo unknown)
    ctx=$(id -Z 2>/dev/null || cat /proc/self/attr/current 2>/dev/null | tr -d '\000' || echo unknown)
    enf=$(getenforce 2>/dev/null || echo unknown)
    sysfs=$(cat /sys/fs/selinux/enforce 2>/dev/null || echo unknown)
    [ "$uid" = 0 ] || fail NOT_ROOT "requires uid=0 (got $uid)"
    [ "$ctx" = u:r:ksu:s0 ] || fail WRONG_CONTEXT "requires u:r:ksu:s0 (got $ctx)"
    [ "$enf" = Enforcing ] && [ "$sysfs" = 1 ] || fail SELINUX_NOT_ENFORCING "requires dual Enforcing (got $enf/$sysfs)"
}

resolve_ko_path() {
    p=/vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko
    if [ ! -e "$p" ] && [ ! -L "$p" ]; then
        p=/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko
    fi
    printf '%s\n' "$p"
}

resolve_bt_apk() {
    count=0
    found=""
    for c in /apex/com.android.bt/app/*/Bluetooth.apk /apex/com.android.bt/app/Bluetooth.apk; do
        [ -f "$c" ] && [ ! -L "$c" ] || continue
        found=$c
        count=$((count + 1))
    done
    [ "$count" = 1 ] && printf '%s\n' "$found" || printf '\n'
}

cmd_preflight() {
    dev=$(getprop ro.product.device 2>/dev/null || echo unknown)
    sdk=$(getprop ro.build.version.sdk 2>/dev/null || echo unknown)
    mios=$(getprop ro.mi.os.version.name 2>/dev/null || echo unknown)
    kern=$(uname -r 2>/dev/null || echo unknown)
    uid=$(id -u 2>/dev/null || echo unknown)
    ctx=$(id -Z 2>/dev/null || cat /proc/self/attr/current 2>/dev/null | tr -d '\000' || echo unknown)
    enf=$(getenforce 2>/dev/null || echo unknown)
    sysfs=$(cat /sys/fs/selinux/enforce 2>/dev/null || echo unknown)
    ko_path=$(resolve_ko_path)
    cfg_sha=$(sha256sum /odm/firmware/wifi.cfg 2>/dev/null | cut -d ' ' -f 1)
    ko_sha=$(sha256sum "$ko_path" 2>/dev/null | cut -d ' ' -f 1)
    bt_apk=$(resolve_bt_apk)
    bt_sha=""
    [ -n "$bt_apk" ] && bt_sha=$(sha256sum "$bt_apk" 2>/dev/null | cut -d ' ' -f 1)
    jar_sha=$(sha256sum /apex/com.android.tethering/javalib/framework-tethering.jar 2>/dev/null | cut -d ' ' -f 1)
    wifi_raw=$(runcon u:r:shell:s0 sh -c 'cmd wifi status' 2>/dev/null || true)
    wifi_en=0; case "$wifi_raw" in *"Wifi is enabled"*) wifi_en=1 ;; esac
    wifi_conn=0; case "$wifi_raw" in *'Wifi is connected to "'*) wifi_conn=1 ;; esac
    ksud_ver=missing; [ -x /data/adb/ksud ] && ksud_ver=$(/data/adb/ksud --version 2>/dev/null | head -1 || echo present)
    zyg_ok=0
    if [ -x /data/adb/modules/zygisksu/bin/zygiskd ] && timeout 4 /data/adb/modules/zygisksu/bin/zygiskd status 2>/dev/null | grep -q 'Root:'; then
        zyg_ok=1
    fi
    lsp_ok=0
    if [ -x /data/adb/modules/zygisk_lsposed/lspctl ] && timeout 4 /data/adb/modules/zygisk_lsposed/lspctl status 2>/dev/null | grep -qx 'System server: connected'; then
        lsp_ok=1
    fi
    echo "DEVICE=$dev"
    echo "SDK=$sdk"
    echo "MI_OS=$mios"
    echo "KERNEL=$kern"
    echo "UID=$uid"
    echo "CONTEXT=$ctx"
    echo "SELINUX=$enf"
    echo "SYSFS_ENFORCE=$sysfs"
    echo "KSUD=$ksud_ver"
    echo "ZYGISK_OK=$zyg_ok"
    echo "LSPOSED_OK=$lsp_ok"
    echo "WIFI_ENABLED=$wifi_en"
    echo "WIFI_CONNECTED=$wifi_conn"
    echo "CFG_SHA=$cfg_sha"
    echo "KO_SHA=$ko_sha"
    echo "BT_APK_SHA=$bt_sha"
    echo "TETHER_JAR_SHA=$jar_sha"
    echo "BT_OFFLOAD=$(getprop ro.bluetooth.leaudio_offload.supported 2>/dev/null || true)"
    echo "BT_OFFLOAD_DIS=$(getprop persist.bluetooth.leaudio_offload.disabled 2>/dev/null || true)"
    for m in mt6639-6g mt6639-bt mt6639-hotspot; do
        lv=$(sed -n 's/^version=//p' "/data/adb/modules/$m/module.prop" 2>/dev/null | head -1)
        uv=$(sed -n 's/^version=//p' "/data/adb/modules_update/$m/module.prop" 2>/dev/null | head -1)
        dis=0; [ -e "/data/adb/modules/$m/disable" ] && dis=1
        echo "MOD_${m}_LIVE=${lv:-absent}"
        echo "MOD_${m}_STAGED=${uv:-absent}"
        echo "MOD_${m}_DISABLE=$dis"
    done
}

cmd_stage_from_dir() {
    stage_dir=$ARG2
    case "$stage_dir" in
        /data/local/tmp/mt6639-kit-*) ;;
        *) fail BAD_ARG "usage: stage-from-dir /data/local/tmp/mt6639-kit-..." 64 ;;
    esac
    require_ksu_enforcing
    [ -x /data/adb/ksud ] || fail KSUD_MISSING "/data/adb/ksud not found"
    [ -d "$stage_dir" ] && [ ! -L "$stage_dir" ] || fail STAGE_MISSING "missing stage dir"
    [ -f "$stage_dir/SHA256SUMS" ] || fail SUMS_MISSING "missing SHA256SUMS"
    (cd "$stage_dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail SHA_MISMATCH "kit artifact checksum mismatch"

    /data/adb/ksud module install "$stage_dir/mt6639-6g-v0.4.1.zip" || fail INSTALL_6G "ksud install mt6639-6g failed"
    /data/adb/ksud module install "$stage_dir/mt6639-bt-v0.2.0.zip" || fail INSTALL_BT "ksud install mt6639-bt failed"
    /data/adb/ksud module install "$stage_dir/mt6639-hotspot-v0.3.1.zip" || fail INSTALL_HS "ksud install mt6639-hotspot failed"

    for m in mt6639-6g mt6639-hotspot; do
        if [ -f "/data/adb/modules_update/$m/SHA256SUMS" ]; then
            (cd "/data/adb/modules_update/$m" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail UPD_SHA "modules_update/$m checksum failed"
        elif [ -f "/data/adb/modules/$m/SHA256SUMS" ]; then
            (cd "/data/adb/modules/$m" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail UPD_SHA "modules/$m checksum failed"
        else
            fail UPD_SHA "installed $m SHA256SUMS not found"
        fi
    done
    guard_set=0
    if [ -d /data/adb/modules_update/mt6639-6g ]; then
        : > /data/adb/modules_update/mt6639-6g/disable || fail GUARD_FAIL "cannot set modules_update disable guard"
        guard_set=1
    fi
    if [ -d "$MOD_6G" ]; then
        : > "$MOD_6G/disable" || fail GUARD_FAIL "cannot set modules disable guard"
        guard_set=1
    fi
    [ "$guard_set" = 1 ] || fail GUARD_FAIL "mt6639-6g target dir missing after ksud install"
    apk_path="$stage_dir/mt6639-ui-lsposed-0.3.0.apk"
    apk_size=$(wc -c < "$apk_path" | tr -d '[:space:]')
    case "$apk_size" in ''|0|*[!0-9]*) fail APK_INSTALL "invalid ui apk size" ;; esac
    pm install -r -S "$apk_size" < "$apk_path" || fail APK_INSTALL "pm install mt6639-ui-lsposed failed"
    echo "STAGE_RESULT=ok"
    echo "NEXT_ACTION=enable com.android.settings in LSPosed Manager, then reboot once to activate modules_update"
}

cmd_bounded_trial() {
    require_ksu_enforcing
    [ -d "$MOD_6G" ] || fail MODULE_NOT_ACTIVE "$MOD_6G missing; reboot once after staging"
    sh "$MOD_6G/bin/verify-module.sh" "$MOD_6G" || fail MODULE_SHA_BROKEN "mt6639-6g SHA256SUMS verification failed"
    restore_disable() { : > "$MOD_6G/disable"; }
    trap restore_disable EXIT HUP INT TERM
    rm -f "$MOD_6G/disable"
    RUN_MODE=bounded sh "$MOD_6G/bin/control.sh" "$MOD_6G" enable
    rc=$?
    restore_disable
    trap - EXIT HUP INT TERM
    sh "$MOD_6G/bin/verify-module.sh" "$MOD_6G" || fail MODULE_SHA_BROKEN "post-enable SHA256SUMS check failed"
    sixg_cnt=$(iw phy 2>/dev/null | grep -E '^\s*\* [0-9.]+ MHz' | awk '$2 >= 5925 && $2 <= 7125 && $0 !~ /\(disabled\)/ { n++ } END { print n+0 }')
    echo "TRIAL_ENABLE_RC=$rc"
    echo "SIXG_ENABLED_COUNT=$sixg_cnt"
    sh "$MOD_6G/bin/control.sh" "$MOD_6G" status
    exit "$rc"
}

cmd_restore_6g() {
    require_ksu_enforcing
    [ -d "$MOD_6G" ] || fail MODULE_NOT_ACTIVE "$MOD_6G missing"
    sh "$MOD_6G/bin/control.sh" "$MOD_6G" disable
    rc=$?
    : > "$MOD_6G/disable"
    echo "RESTORE_6G_RC=$rc"
    sh "$MOD_6G/bin/control.sh" "$MOD_6G" status
    exit "$rc"
}

cmd_test_bt() {
    require_ksu_enforcing
    [ -d "$MOD_BT" ] || fail MODULE_NOT_ACTIVE "$MOD_BT missing"
    sh "$MOD_BT/bin/control.sh" enable || exit $?
    sh "$MOD_BT/bin/control.sh" status
}

cmd_restore_bt() {
    require_ksu_enforcing
    [ -d "$MOD_BT" ] || fail MODULE_NOT_ACTIVE "$MOD_BT missing"
    sh "$MOD_BT/bin/control.sh" restore || exit $?
    sh "$MOD_BT/bin/control.sh" status
}

cmd_diagnose() {
    cmd_preflight
    echo "COUNTRY=$(runcon u:r:shell:s0 sh -c 'cmd wifi get-country-code' 2>/dev/null | tail -1)"
    echo "CFG_MOUNT_COUNT=$(grep -c ' /odm/firmware/wifi.cfg ' /proc/1/mountinfo 2>/dev/null || echo 0)"
    echo "SIX_G_STATE=$(cat /data/adb/mt6639-6g/state 2>/dev/null || echo absent)"
    echo "SIX_G_ACTIVE_TX=$([ -s /data/adb/mt6639-6g/active_tx ] && echo present || echo none)"
    echo "MTAP0=$([ -e /sys/class/net/mtap0 ] && echo present || echo absent)"
    echo "TETHER_WANTED=$(dumpsys tethering --short 2>/dev/null | sed -n 's/^[[:space:]]*Upstream wanted:[[:space:]]*//p' | head -1)"
    echo "HOTSPOT_SNAP=$(tr '\n' ';' < /data/misc/mt6639-hotspot/status 2>/dev/null || echo absent)"
    echo "BAND4_TOTAL=$(iw phy 2>/dev/null | grep -E '^\s*\* [0-9.]+ MHz' | awk '$2 >= 5925 && $2 <= 7125 { n++ } END { print n+0 }')"
    echo "BAND4_ENABLED=$(iw phy 2>/dev/null | grep -E '^\s*\* [0-9.]+ MHz' | awk '$2 >= 5925 && $2 <= 7125 && $0 !~ /\(disabled\)/ { n++ } END { print n+0 }')"
}

cmd_cleanup() {
    require_ksu_enforcing
    if [ -f "$MOD_HS/bin/control.sh" ]; then
        sh "$MOD_HS/bin/control.sh" "$MOD_HS" stop >/dev/null 2>&1 || true
    fi
    if [ -f "$MOD_BT/bin/control.sh" ]; then
        sh "$MOD_BT/bin/control.sh" restore >/dev/null 2>&1 || true
    fi
    if [ -f "$MOD_6G/bin/control.sh" ]; then
        sh "$MOD_6G/bin/control.sh" "$MOD_6G" disable || fail RESTORE_FAIL "6G restore failed"
    fi
    for m in mt6639-6g mt6639-bt mt6639-hotspot; do
        [ -d "/data/adb/modules/$m" ] && : > "/data/adb/modules/$m/disable"
    done
    if [ "$ARG2" = "--uninstall" ]; then
        for m in mt6639-hotspot mt6639-bt mt6639-6g; do
            if [ -d "/data/adb/modules/$m" ] || [ -d "/data/adb/modules_update/$m" ]; then
                /data/adb/ksud module uninstall "$m" || fail UNINSTALL_FAIL "ksud uninstall $m failed"
            fi
        done
        pm uninstall com.hiliang.mt6639ui >/dev/null 2>&1 || true
    fi
    echo "CLEANUP_RESULT=ok uninstall=$([ "$ARG2" = --uninstall ] && echo 1 || echo 0)"
}

case "$OP" in
    preflight) cmd_preflight ;;
    stage-from-dir) cmd_stage_from_dir ;;
    bounded-trial) cmd_bounded_trial ;;
    restore-6g) cmd_restore_6g ;;
    test-bt) cmd_test_bt ;;
    restore-bt) cmd_restore_bt ;;
    diagnose) cmd_diagnose ;;
    cleanup) cmd_cleanup ;;
    *)
        echo "usage: device_tablet_session.sh preflight|stage-from-dir DIR|bounded-trial|restore-6g|test-bt|restore-bt|diagnose|cleanup [--uninstall]" >&2
        exit 64
        ;;
esac
