#!/system/bin/sh
# Read-only RTT/NAN capability snapshot. Never attaches Aware or creates an interface.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-rtt-nan-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77
[ "$(CDPATH= cd -P -- "$D" && pwd)" = "$D" ] || exit 77

boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || exit 70
[ -n "$boot" ] || exit 70
printf '%s\n' "$boot" > "$D/boot" || exit 74
printf '%s\n' "$(getenforce 2>/dev/null || echo unknown)" > "$D/selinux" || exit 74
cat /proc/self/attr/current 2>/dev/null | tr -d '\000' > "$D/context" || exit 74

timeout 8 dumpsys wifi > "$D/dumpsys-wifi.raw" 2>&1 || exit 69
timeout 5 cmd wifi help > "$D/wifi-help" 2>&1 || true
timeout 5 service list > "$D/service-list" 2>&1 || exit 69
timeout 5 pm list features > "$D/features" 2>&1 || exit 69
timeout 5 cmd package has-feature android.hardware.wifi.aware 0 \
    > "$D/feature-aware.query" 2>&1 || true
timeout 5 cmd package has-feature android.hardware.wifi.rtt 0 \
    > "$D/feature-rtt.query" 2>&1 || true
timeout 5 iw phy > "$D/iw-phy" 2>&1 || exit 69

supported=$(sed -n 's/^SupportedFeatures: \[\(.*\)\]$/\1/p' "$D/dumpsys-wifi.raw" | tail -1)
has_feature() {
    printf '%s\n' "$supported" | tr ',' '\n' | sed 's/^ //;s/ $//' |
        grep -Fqx "$1"
}
has_feature WIFI_FEATURE_AWARE && aware_hal=1 || aware_hal=0
has_feature WIFI_FEATURE_RTT && rtt_hal=1 || rtt_hal=0
has_feature WIFI_FEATURE_11MC && rtt_11mc_hal=1 || rtt_11mc_hal=0

grep -q 'wifiaware: \[android.net.wifi.aware.IWifiAwareManager\]' "$D/service-list" &&
    aware_binder=1 || aware_binder=0
grep -q 'wifirtt: \[' "$D/service-list" && rtt_binder=1 || rtt_binder=0
grep -Fqx 'feature:android.hardware.wifi.aware' "$D/features" &&
    aware_framework=1 || aware_framework=0
grep -Fqx 'feature:android.hardware.wifi.rtt' "$D/features" &&
    rtt_framework=1 || rtt_framework=0
aware_single=$(tr -d '[:space:]' < "$D/feature-aware.query")
rtt_single=$(tr -d '[:space:]' < "$D/feature-rtt.query")
[ "$aware_single" = true ] || aware_single=false
[ "$rtt_single" = true ] || rtt_single=false

aware_state=$(sed -n 's/^Connection state:.*Aware=\([^,]*\).*$/\1/p' \
    "$D/dumpsys-wifi.raw" | tail -1)
connected_11mc=$(sed -n 's/.*mIs11mcSupported=\([^,]*\).*/\1/p' \
    "$D/dumpsys-wifi.raw" | tail -1)
connected_11az=$(sed -n 's/.*mIs11azSupported=\([^,]*\).*/\1/p' \
    "$D/dumpsys-wifi.raw" | tail -1)
grep -qiE 'RTT|FTM|fine.?timing' "$D/iw-phy" && phy_rtt_terms=1 || phy_rtt_terms=0
grep -q '^mRttMetrics:' "$D/dumpsys-wifi.raw" && rtt_metrics=1 || rtt_metrics=0
grep -q '^mWifiAwareMetrics:' "$D/dumpsys-wifi.raw" && aware_metrics=1 || aware_metrics=0

{
    echo "boot=$boot"
    echo "selinux=$(cat "$D/selinux")"
    echo "context=$(cat "$D/context")"
    echo "supported_features=$supported"
    echo "aware_hal_feature=$aware_hal"
    echo "rtt_hal_feature=$rtt_hal"
    echo "rtt_11mc_hal_feature=$rtt_11mc_hal"
    echo "aware_binder=$aware_binder"
    echo "rtt_binder=$rtt_binder"
    echo "aware_framework_feature=$aware_framework"
    echo "rtt_framework_feature=$rtt_framework"
    echo "aware_single_feature_query=$aware_single"
    echo "rtt_single_feature_query=$rtt_single"
    echo "aware_connection_state=${aware_state:-unknown}"
    echo "connected_11mc_supported=${connected_11mc:-unknown}"
    echo "connected_11az_supported=${connected_11az:-unknown}"
    echo "iw_phy_rtt_terms=$phy_rtt_terms"
    echo "rtt_metrics_present=$rtt_metrics"
    echo "aware_metrics_present=$aware_metrics"
} > "$D/summary.tmp" || exit 74
mv "$D/summary.tmp" "$D/summary" || exit 74
cat "$D/summary"
echo RESULT=snapshot_complete
