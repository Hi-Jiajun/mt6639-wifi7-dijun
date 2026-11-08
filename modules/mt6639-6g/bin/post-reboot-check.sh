#!/system/bin/sh
# Read-only snapshot check. A pass alone does not prove a reboot occurred.
set -u
MODE=${1:-active}
case "$MODE" in active|restored) ;; *) echo 'usage: post-reboot-check.sh [active|restored]'; exit 64 ;; esac
MODDIR=/data/adb/modules/mt6639-6g
WORK=/data/adb/mt6639-6g
CFG=/odm/firmware/wifi.cfg
KO=/vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko
CFG_SHA=e4dcd846f31077864932b499d462cfd6a8ca1befc6386f502813e0ba053cc154
KO_SHA=54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea
fail=0
say() { echo "$*"; }
bad() { echo "FAIL $*"; fail=$((fail+1)); }

boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
boot8=$(printf '%s' "$boot" | cut -c1-8)
say "boot=$boot"

[ "$(id -u)" = 0 ] && say "root=uid0" || bad "root_uid_not_0"
ctx=$(cat /proc/self/attr/current 2>/dev/null | tr -d '\000') || true
say "selinux_context=$ctx"
[ "$(getenforce 2>/dev/null)" = Enforcing ] && say "selinux=enforcing" || bad "selinux_not_enforcing"

if [ -d "$MODDIR" ]; then
    say "active_module_path=$MODDIR"
else
    bad "module_not_active_path"
fi
if [ -f "$MODDIR/module.prop" ]; then
    ver=$(grep '^version=' "$MODDIR/module.prop" | cut -d= -f2-)
    say "module_version=$ver"
    # Never pin a literal version here: this checker is also consumed by the
    # hotspot module as its station checker, so a version bump in the 6GHz module
    # used to make every hotspot teardown report recovery_rc=1. Verify the module
    # tree against its own SHA256SUMS instead — it is stronger and version-agnostic.
    [ -f "$MODDIR/SHA256SUMS" ] || bad "module_sums_missing"
    if [ -f "$MODDIR/SHA256SUMS" ]; then
        ( cd "$MODDIR" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) ||
            bad "module_sums_mismatch"
    fi
else
    bad "module_prop_missing"
fi

cfg=$(sha256sum "$CFG" 2>/dev/null | cut -d' ' -f1)
ko=$(sha256sum "$KO" 2>/dev/null | cut -d' ' -f1)
say "cfg_sha=$cfg"
say "ko_sha=$ko"
[ "$ko" = "$KO_SHA" ] || bad "ko_not_stock"

if [ "$MODE" = active ]; then
    backup=$WORK/backup-$boot/wifi.cfg.stock
    [ "$(sha256sum "$backup" 2>/dev/null | cut -d' ' -f1)" = "$CFG_SHA" ] || bad "backup_not_stock"
    expected=$(sed 's/^DisallowBand6G 1$/DisallowBand6G 0/' "$backup" 2>/dev/null | sha256sum | cut -d' ' -f1)
    [ "$cfg" = "$expected" ] || bad "cfg_not_expected_candidate"
    grep -q " $CFG " /proc/self/mountinfo 2>/dev/null || bad "cfg_mount_missing"
    [ ! -e "$MODDIR/disable" ] && [ ! -e "$MODDIR/remove" ] || bad "module_disabled_or_removing"
    runtime=$(sh "$MODDIR/bin/native.sh" "$MODDIR" status) || bad "native_status_failed"
    say "$runtime"
    printf '%s\n' "$runtime" | grep -qx 'guardian=alive' || bad "guardian_not_alive"
    printf '%s\n' "$runtime" | grep -qx 'state=active' || bad "state_not_active"
    tx=$(cat "$WORK/active_tx" 2>/dev/null)
    [ "$(cat "$tx/wrapper-boot" 2>/dev/null)" = "$boot" ] || bad "transaction_boot_mismatch"
    grep -q '"event":"persistent-hold"' "$tx/guardian.log" 2>/dev/null || bad "persistent_hold_not_observed"
else
    [ "$cfg" = "$CFG_SHA" ] || bad "cfg_not_stock"
    grep -q " $CFG " /proc/self/mountinfo 2>/dev/null
    case "$?" in 0) bad "cfg_still_mounted" ;; 1) say "cfg_mount=none" ;; *) bad "mountinfo_unreadable" ;; esac
    runtime=$(sh "$MODDIR/bin/native.sh" "$MODDIR" status) || bad "native_status_failed"
    say "$runtime"
    printf '%s\n' "$runtime" | grep -qx 'active_tx=none' || bad "active_transaction_present_or_invalid"
fi

enabled=$(grep '^ENABLE_6G=' "$MODDIR/config.conf" 2>/dev/null | head -1)
say "config=$enabled"
case "$enabled" in
    'ENABLE_6G=${ENABLE_6G:-1}') ;;
    *) bad "default_on_config_mismatch" ;;
esac
grep -qx 'RUN_MODE=${RUN_MODE:-persistent}' "$MODDIR/config.conf" || bad "default_persistent_config_mismatch"

if grep -q "service: boot=$boot8 ENABLE_6G=1" "$WORK/apply.log" 2>/dev/null; then
    say "service_default_on=observed_this_boot"
else
    bad "service_default_on_not_observed"
fi

if [ -x "$MODDIR/bin/mount-txn" ] || [ -f "$MODDIR/bin/mount-txn" ]; then
    native_info=$(sh "$MODDIR/bin/native.sh" "$MODDIR" inspect 2>&1) || bad "native_inspect_failed"
    echo "$native_info"
    if [ "$MODE" = active ]; then country=US; else country=CN; fi
    printf '%s\n' "$native_info" | grep -q "\"country_code\":\"$country\"" && say "country=$country" || bad "country_unexpected"
else
    bad "native_binary_missing"
fi

echo "RESULT fail=$fail"
[ "$fail" = 0 ]
