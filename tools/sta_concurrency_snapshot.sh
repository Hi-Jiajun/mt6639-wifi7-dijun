#!/system/bin/sh
# Read-only C01/C02/C03 concurrency gate snapshot. Never creates interfaces.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-sta-concurrency-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77
[ "$(CDPATH= cd -P -- "$D" && pwd)" = "$D" ] || exit 77

boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || exit 70
[ -n "$boot" ] || exit 70
printf '%s\n' "$boot" > "$D/boot.snapshot" || exit 74
printf '%s\n' "$(getenforce 2>/dev/null || echo unknown)" > "$D/selinux" || exit 74
cat /proc/self/attr/current 2>/dev/null | tr -d '\000' > "$D/context" || exit 74
getprop ro.build.fingerprint > "$D/fingerprint" 2>/dev/null || exit 74

concurrency_installed=0
concurrency_version=unknown
concurrency_pinned=0
if pm path com.hiliang.mt6639concurrency > "$D/concurrency.path" 2>&1 &&
    grep -q '^package:' "$D/concurrency.path"; then
    concurrency_installed=1
    concurrency_version=$(dumpsys package com.hiliang.mt6639concurrency 2>/dev/null |
        sed -n 's/^[[:space:]]*versionName=//p' | head -1)
    [ "$concurrency_version" = 0.1.0 ] && concurrency_pinned=1
fi
gate=/data/misc/mt6639-concurrency/gate
gate_marker=absent
gate_state=disarmed
gate_reason=none
if [ -e "$gate" ] || [ -L "$gate" ]; then
    gate_marker=present
    if [ -L "$gate" ] || [ ! -f "$gate" ] ||
        [ "$(wc -l < "$gate" 2>/dev/null)" -ne 4 ]; then
        gate_state=invalid
        gate_reason=marker_shape
    else
        gate_schema=$(sed -n 's/^schema=//p' "$gate")
        gate_boot=$(sed -n 's/^boot=//p' "$gate")
        gate_expires=$(sed -n 's/^expires_ms=//p' "$gate")
        gate_lease=$(sed -n 's/^lease=//p' "$gate")
        gate_now=$(awk '{printf "%.0f", $1 * 1000}' /proc/uptime 2>/dev/null)
        # Canonical decimal values bounded before any shell arithmetic.
        if ! printf '%s\n' "$gate_expires" | grep -Eq '^(0|[1-9][0-9]{0,14})$' ||
            ! printf '%s\n' "$gate_now" | grep -Eq '^(0|[1-9][0-9]{0,14})$'; then
            gate_state=invalid
            gate_reason=field_value
        else
                if [ "$gate_schema" != mt6639-dualsta-gate-v1 ] ||
                    ! printf '%s\n' "$gate_lease" | grep -Eq '^[0-9a-f]{16}$'; then
                    gate_state=invalid
                    gate_reason=field_value
                elif [ "$gate_boot" != "$boot" ]; then
                    gate_state=expired
                    gate_reason=boot_mismatch
                elif [ "$gate_expires" -le "$gate_now" ]; then
                    gate_state=expired
                    gate_reason=deadline
                elif [ $((gate_expires - gate_now)) -gt 120000 ]; then
                    gate_state=invalid
                    gate_reason=remaining_ttl_too_long
                else
                    gate_state=armed
                    gate_reason=none
                fi
        fi
    fi
fi

timeout 8 dumpsys wifi > "$D/dumpsys-wifi.raw" 2>&1 || exit 69
timeout 5 cmd wifi status > "$D/wifi-status" 2>&1 || exit 69
timeout 5 cmd wifi list-interface-names > "$D/interface-names" 2>&1 || exit 69
timeout 5 cmd wifi query-interface 1000 android STA -new > "$D/query-sta" 2>&1 || exit 69
timeout 5 cmd wifi query-interface 1000 android AP -new > "$D/query-ap" 2>&1 || exit 69
timeout 5 cmd wifi query-interface 1000 android DIRECT -new > "$D/query-direct" 2>&1 || exit 69
timeout 5 cmd wifi get-allowed-channel -b 8 > "$D/allowed-6g" 2>&1 || exit 69
timeout 5 iw dev > "$D/iw-dev" 2>&1 || exit 69
timeout 5 ip -4 -o addr show > "$D/ipv4" 2>&1 || exit 69

mode=$(sed -n 's/^WifiMultiInternetMode \([0-9][0-9]*\)$/\1/p' "$D/dumpsys-wifi.raw" | tail -1)
state=$(sed -n 's/^mMultiInternetConnectionState \([0-9][0-9]*\)$/\1/p' "$D/dumpsys-wifi.raw" | tail -1)
primary_rec=$(grep 'role=ROLE_CLIENT_PRIMARY' "$D/wifi-status" | tail -1)
if printf '%s\n' "$primary_rec" | grep -q 'iface=wlan0 role=ROLE_CLIENT_PRIMARY'; then
    primary=wlan0
else
    primary=unknown
fi
current_cmms=$(grep -c 'ClientModeManager instance:' "$D/wifi-status" | tr -d ' ')
secondary_cmms=$(grep -c 'role=ROLE_CLIENT_SECONDARY' "$D/wifi-status" | tr -d ' ')
same_band=$(sed -n 's/^mIsMultiInternetSameBandConnectionAllowed=\([a-z]*\)$/\1/p' "$D/dumpsys-wifi.raw" | tail -1)
same_bssid=$(sed -n 's/^mIsMultiInternetSameBssidConnectionAllowed=\([a-z]*\)$/\1/p' "$D/dumpsys-wifi.raw" | tail -1)
multi_resource=$(grep -m1 'Resource Name: config_wifiMultiStaMultiInternetConcurrencyEnabled, value:' "$D/dumpsys-wifi.raw")
restricted_resource=$(grep -m1 'Resource Name: config_wifiMultiStaRestrictedConcurrencyEnabled, value:' "$D/dumpsys-wifi.raw")
bridged_ap_resource=$(grep -m1 'Resource Name: config_wifiStaWithBridgedSoftApConcurrencySupported, value:' "$D/dumpsys-wifi.raw")

if grep -q 'WIFI_FEATURE_ADDITIONAL_STA_MULTI_INTERNET' "$D/dumpsys-wifi.raw"; then
    multi_feature=true
else
    multi_feature=false
fi
if grep -q 'WIFI_FEATURE_DUAL_BAND_SIMULTANEOUS' "$D/dumpsys-wifi.raw"; then
    dbs_feature=true
else
    dbs_feature=false
fi
if grep -q 'WIFI_FEATURE_AP_STA' "$D/dumpsys-wifi.raw"; then
    ap_sta_feature=true
else
    ap_sta_feature=false
fi
if grep -q '^Interface STA can be created' "$D/query-sta"; then
    sta_query=creatable
elif grep -q "Can't create interface: STA" "$D/query-sta"; then
    sta_query=blocked
else
    sta_query=unknown
fi
if grep -Eq '(^|[[:space:]])6135([[:space:]]|$)' "$D/allowed-6g"; then
    p2p_6g_6135=allowed
else
    p2p_6g_6135=not_listed
fi

{
    echo "boot=$boot"
    echo "selinux=$(cat "$D/selinux")"
    echo "context=$(cat "$D/context")"
    echo "fingerprint=$(cat "$D/fingerprint")"
    echo "concurrency_gate_installed=$concurrency_installed"
    echo "concurrency_gate_version=$concurrency_version"
    echo "concurrency_gate_pinned=$concurrency_pinned"
    echo "concurrency_gate_marker=$gate_marker"
    echo "concurrency_gate_state=$gate_state"
    echo "concurrency_gate_reason=$gate_reason"
    echo "multi_internet_feature=$multi_feature"
    echo "dbs_feature=$dbs_feature"
    echo "ap_sta_feature=$ap_sta_feature"
    echo "multi_internet_mode=${mode:-unknown}"
    echo "multi_internet_state=${state:-unknown}"
    echo "primary_cmm=${primary}"
    echo "client_mode_manager_instances=$current_cmms"
    echo "secondary_client_mode_managers=$secondary_cmms"
    echo "same_band_allowed=${same_band:-unknown}"
    echo "same_bssid_allowed=${same_bssid:-unknown}"
    printf '%s\n' "$multi_resource"
    printf '%s\n' "$restricted_resource"
    printf '%s\n' "$bridged_ap_resource"
    echo "sta_interface_query=$sta_query"
    echo "p2p_6g_6135=$p2p_6g_6135"
    echo "native_mode_observed=unknown"
} > "$D/summary.tmp" || exit 74
mv "$D/summary.tmp" "$D/summary" || exit 74
cat "$D/summary"
echo RESULT=snapshot_complete
