#!/system/bin/sh
# Read-only device preflight for the MT6639 release candidate.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-release-preflight-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77
[ "$(CDPATH= cd -P -- "$D" && pwd)" = "$D" ] || exit 77

EXPECTED_FP='Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys'
EXPECTED_KSU_MANAGER_VERSION='v3.3.1'
EXPECTED_ROOT_CONTEXT='u:r:ksu:s0'
EXPECTED_CONCURRENCY_VERSION='0.1.0'
fail=0
warn=0
result() {
    level=$1 key=$2 value=$3 detail=${4:-}
    printf '%s\t%s\t%s\t%s\n' "$level" "$key" "$value" "$detail" >> "$D/results.tmp"
    [ "$level" != FAIL ] || fail=$((fail + 1))
    [ "$level" != WARN ] || warn=$((warn + 1))
}
module_version() {
    sed -n 's/^version=//p' "/data/adb/modules/$1/module.prop" 2>/dev/null | head -1
}

[ "$(id -u)" = 0 ] && result PASS root_uid "$(id -u)" || result FAIL root_uid "$(id -u)" expected_0
selinux_status=$(getenforce 2>/dev/null || echo unknown)
selinux_sysfs=$(cat /sys/fs/selinux/enforce 2>/dev/null || echo unknown)
if [ "$selinux_status" = Enforcing ] && [ "$selinux_sysfs" = 1 ]; then
    result PASS selinux dual_enforcing "sysfs=$selinux_sysfs"
else
    result FAIL selinux "$selinux_status" "sysfs=$selinux_sysfs expected_Enforcing_and_1"
fi
root_context=$(cat /proc/self/attr/current 2>/dev/null | tr -d '\000')
[ "$root_context" = "$EXPECTED_ROOT_CONTEXT" ] &&
    result PASS root_context "$root_context" ||
    result FAIL root_context "${root_context:-unknown}" "expected_$EXPECTED_ROOT_CONTEXT"
result INFO boot "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
fp=$(getprop ro.build.fingerprint)
[ "$fp" = "$EXPECTED_FP" ] && result PASS fingerprint exact || result FAIL fingerprint "$fp" mismatch

if manager_path=$(pm path me.weishu.kernelsu 2>/dev/null |
        sed -n 's/^package://p' | head -1) &&
    [ -n "$manager_path" ] && [ -f "$manager_path" ]; then
    manager_version=$(dumpsys package me.weishu.kernelsu 2>/dev/null |
        sed -n 's/^[[:space:]]*versionName=//p' | head -1)
    [ "$manager_version" = "$EXPECTED_KSU_MANAGER_VERSION" ] &&
        result PASS kernelsu_manager "$manager_version" ||
        result FAIL kernelsu_manager "${manager_version:-unknown}" \
            "expected_$EXPECTED_KSU_MANAGER_VERSION"
else
    result FAIL kernelsu_manager missing "expected_$EXPECTED_KSU_MANAGER_VERSION"
fi

if [ -x /data/adb/ksud ]; then
    timeout 6 /data/adb/ksud debug info > "$D/ksud.info" 2>&1 || true
    grep -qx 'lkm: true' "$D/ksud.info" && grep -qx 'late_load: true' "$D/ksud.info" &&
        result PASS kernelsu late_lkm || result WARN kernelsu unverified
else
    result FAIL kernelsu missing expected_/data/adb/ksud
fi
if [ -x /data/adb/ksud ] &&
    timeout 6 /data/adb/ksud feature get 0 > "$D/ksud.su-compat" 2>&1 &&
    grep -Fxq 'Value: 1' "$D/ksud.su-compat" &&
    grep -Fxq 'Status: enabled' "$D/ksud.su-compat"; then
    result PASS kernelsu_su_compat enabled
else
    result FAIL kernelsu_su_compat not_ready expected_su_compat_enabled
fi
su_rc=127
if command -v su > "$D/ksu.su-path" 2>/dev/null; then
    timeout 5 su -c id > "$D/ksu.su-id" 2>&1
    su_rc=$?
elif [ -x /data/adb/ksu/bin/su ]; then
    printf '%s\n' /data/adb/ksu/bin/su > "$D/ksu.su-path"
    timeout 5 /data/adb/ksu/bin/su -c id > "$D/ksu.su-id" 2>&1
    su_rc=$?
else
    printf 'su unavailable in PATH and KernelSU private bin\n' > "$D/ksu.su-id"
fi
# Absolute-path success from an already-root caller still must prove KSU domain.
if [ "$su_rc" = 0 ] &&
    grep -Fq 'uid=0(root)' "$D/ksu.su-id" &&
    grep -Fq 'context=u:r:ksu:s0' "$D/ksu.su-id"; then
    result PASS kernelsu_su_context uid_0_ksu
else
    result FAIL kernelsu_su_context not_ready expected_uid0_and_u:r:ksu:s0
fi

EXPECTED_XRINGROOT_SHA=c739b07e747b1be2a4f0af9c19eb06674fd895665f9b45a36bc7c5ce6cee9e49
EXPECTED_XRINGROOT_SHORT=$(printf '%s\n' "$EXPECTED_XRINGROOT_SHA" | cut -c1-16)
if xringroot_path=$(pm path com.hiliang.xringroot 2>/dev/null |
        sed -n 's/^package://p' | head -1) &&
    [ -n "$xringroot_path" ] && [ -f "$xringroot_path" ]; then
    xringroot_sha=$(sha256sum "$xringroot_path" 2>/dev/null | awk '{print $1}')
    xringroot_short=$(printf '%s\n' "${xringroot_sha:-unreadable}" | cut -c1-16)
    [ "$xringroot_sha" = "$EXPECTED_XRINGROOT_SHA" ] &&
        result PASS xringroot_tool "$xringroot_short" ||
        result FAIL xringroot_tool "$xringroot_short" "expected_$EXPECTED_XRINGROOT_SHORT"
else
    result FAIL xringroot_tool missing "expected_$EXPECTED_XRINGROOT_SHORT"
fi

for pair in 'mt6639-6g:v0.3.0' 'mt6639-hotspot:v0.2.22' 'mt6639-bt:v0.1.2'; do
    id=${pair%%:*}
    expected=${pair#*:}
    actual=$(module_version "$id")
    if [ "$actual" = "$expected" ]; then
        result PASS "module_$id" "$actual"
    else
        result FAIL "module_$id" "${actual:-missing}" "expected_$expected"
    fi
done

for id in mt6639-6g mt6639-hotspot mt6639-bt; do
    module_dir=/data/adb/modules/$id
    if [ -d "$module_dir" ] && [ ! -e "$module_dir/disable" ] &&
        [ ! -e "$module_dir/remove" ]; then
        result PASS "module_${id}_enabled" true
    else
        result FAIL "module_${id}_enabled" false "expected_enabled"
    fi
done

bt_bad=
for key in \
    bluetooth.profile.bap.unicast.client.enabled \
    bluetooth.profile.bap.broadcast.source.enabled \
    bluetooth.profile.bap.broadcast.assist.enabled \
    bluetooth.profile.csip.set_coordinator.enabled \
    bluetooth.profile.vcp.controller.enabled \
    bluetooth.profile.mcp.server.enabled; do
    [ "$(getprop "$key")" = true ] || bt_bad="${bt_bad}${bt_bad:+,}$key"
done
[ -z "$bt_bad" ] &&
    result PASS bt_host_profiles all_true ||
    result FAIL bt_host_profiles not_true "$bt_bad"

if timeout 8 cmd wifi status > "$D/wifi.status" 2>&1 &&
    grep -Fxq 'Wifi is connected to "LabNet-6G"' "$D/wifi.status" &&
    grep -Fq 'Supplicant state: COMPLETED' "$D/wifi.status" &&
    grep -Fq 'Frequency: 6135MHz' "$D/wifi.status"; then
    result PASS wifi_home_net_6g completed_6135
else
    result FAIL wifi_home_net_6g not_ready
fi

[ ! -e /sys/class/net/mtap0 ] && result PASS mtap0 absent || result FAIL mtap0 present
if timeout 5 dumpsys tethering --short > "$D/tethering.status" 2>&1 &&
    grep -Fxq '    Upstream wanted: false' "$D/tethering.status"; then
    result PASS tethering idle
else
    result FAIL tethering not_idle
fi

if pm path com.hiliang.mt6639ui > "$D/ui.path" 2>&1; then
    value=$(dumpsys package com.hiliang.mt6639ui 2>/dev/null |
        sed -n 's/^[[:space:]]*versionName=//p' | head -1)
    [ "$value" = 0.2.7 ] && result PASS ui_lsposed "$value" || result WARN ui_lsposed "${value:-unknown}" expected_0.2.7
else
    result WARN ui_lsposed missing
fi
if pm path com.hiliang.mt6639concurrency > "$D/concurrency.path" 2>&1 &&
    grep -q '^package:' "$D/concurrency.path"; then
    concurrency_version=$(dumpsys package com.hiliang.mt6639concurrency 2>/dev/null |
        sed -n 's/^[[:space:]]*versionName=//p' | head -1)
    [ "$concurrency_version" = "$EXPECTED_CONCURRENCY_VERSION" ] &&
        result PASS concurrency_gate "$concurrency_version" ||
        result FAIL concurrency_gate "${concurrency_version:-unknown}" \
            "expected_$EXPECTED_CONCURRENCY_VERSION"
else
    result WARN concurrency_gate not_installed
fi
if [ ! -e /data/misc/mt6639-concurrency/gate ] &&
    [ ! -L /data/misc/mt6639-concurrency/gate ]; then
    result PASS concurrency_gate_marker absent
else
    result FAIL concurrency_gate_marker present expected_absent
fi

if timeout 5 /data/adb/modules/zygisksu/bin/zygiskd status > "$D/zygisk-next.status" 2>&1 &&
    ! grep -Eq 'failed to connect|Could not connect|^err:' "$D/zygisk-next.status"; then
    result PASS zygisk_next service_connected
else
    result FAIL zygisk_next service_unavailable
fi

timeout 5 /data/adb/modules/zygisk_lsposed/lspctl status > "$D/lsposed.status" 2>&1 || true
grep -qx 'System server: connected' "$D/lsposed.status" &&
    result PASS lsposed system_server_connected || result FAIL lsposed system_server_not_connected

mv "$D/results.tmp" "$D/results.tsv" || exit 74
cat "$D/results.tsv"
echo "RESULT fail=$fail warn=$warn"
[ "$fail" = 0 ]
