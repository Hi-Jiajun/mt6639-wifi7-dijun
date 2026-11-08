#!/system/bin/sh
# One bounded Aware attach/teardown probe. No publish, subscribe, pairing or NDP.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-aware-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] &&
    [ "$(id -Z)" = u:r:ksu:s0 ] || exit 77
[ "$(getenforce)" = Enforcing ] || exit 77
[ "$(getprop ro.build.fingerprint)" = \
    'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys' ] ||
    exit 69
[ "$(CDPATH= cd -P -- "$D" && pwd)" = "$D" ] || exit 77

timeout 5 service list > "$D/service.before" 2>&1 || exit 69
grep -q 'wifiaware: \[android.net.wifi.aware.IWifiAwareManager\]' \
    "$D/service.before" || { echo 'REJECT wifiaware_binder_missing' >&2; exit 76; }
timeout 5 cmd package has-feature android.hardware.wifi.aware 0 \
    > "$D/feature.query" 2>&1 ||
    { echo 'REJECT aware_feature_query_false' >&2; exit 76; }
grep -Fqx 'true' "$D/feature.query" ||
    { echo 'REJECT aware_feature_query_invalid' >&2; exit 76; }
timeout 8 dumpsys wifi > "$D/wifi.before" 2>&1 || exit 69
timeout 5 cmd wifi status > "$D/sta.before" 2>&1 || exit 69
grep -q '^Connection state: .*Aware=false' "$D/wifi.before" ||
    { echo 'REJECT aware_already_active_or_unknown' >&2; exit 75; }
iw dev > "$D/interfaces.before" 2>&1 || exit 69

attached=0
sta_matches() {
    for key in 'Wifi is connected to' 'BSSID:' 'Frequency:'; do
        [ "$(grep -Fc "$key" "$D/sta.before")" = 1 ] &&
            [ "$(grep -Fc "$key" "$1")" = 1 ] || return 1
        before=$(grep -F "$key" "$D/sta.before")
        after=$(grep -F "$key" "$1")
        [ -n "$before" ] && [ "$before" = "$after" ] || return 1
    done
}
cleanup() {
    if [ "$attached" = 1 ]; then
        timeout 10 cmd wifi aware-teardown > "$D/aware.teardown.cleanup" 2>&1 || return 1
        timeout 8 dumpsys wifi > "$D/wifi.cleanup" 2>&1 || return 1
        grep -q '^Connection state: .*Aware=false' "$D/wifi.cleanup" || return 1
        timeout 5 cmd wifi status > "$D/sta.cleanup" 2>&1 || return 1
        sta_matches "$D/sta.cleanup" || return 1
        attached=0
    fi
}
finish() {
    exit_rc=$?
    trap - EXIT
    if cleanup; then
        printf '0\n' > "$D/cleanup.rc"
    else
        printf '1\n' > "$D/cleanup.rc"
        echo 'REJECT aware_cleanup_unverified' >&2
        exit_rc=73
    fi
    exit "$exit_rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
sta_matches "$D/sta.before" || { echo 'REJECT sta_baseline_incomplete' >&2; exit 69; }

first=$(date +%s)
attached=1
timeout 20 cmd wifi aware-attach > "$D/aware.attach" 2>&1
attach_rc=$?
[ "$attach_rc" = 0 ] || { echo "REJECT aware_attach_rc=$attach_rc" >&2; exit 70; }
sleep 5

timeout 5 service list > "$D/service.active" 2>&1 || exit 69
grep -q 'wifiaware: \[android.net.wifi.aware.IWifiAwareManager\]' \
    "$D/service.active" || { echo 'REJECT binder_lost_after_attach' >&2; exit 71; }
timeout 8 dumpsys wifi > "$D/wifi.active" 2>&1 || exit 69
grep -q '^Connection state: .*Aware=true' "$D/wifi.active" ||
    { echo 'REJECT aware_true_not_observed' >&2; exit 71; }
timeout 5 cmd wifi status > "$D/sta.active" 2>&1 || exit 69
iw dev > "$D/interfaces.active" 2>&1 || exit 69

timeout 10 cmd wifi aware-teardown > "$D/aware.teardown" 2>&1
teardown_rc=$?
[ "$teardown_rc" = 0 ] || { echo "REJECT aware_teardown_rc=$teardown_rc" >&2; exit 72; }
sleep 2
timeout 8 dumpsys wifi > "$D/wifi.after" 2>&1 || exit 69
timeout 5 cmd wifi status > "$D/sta.after" 2>&1 || exit 69
grep -q '^Connection state: .*Aware=false' "$D/wifi.after" ||
    { echo 'REJECT aware_false_not_restored' >&2; exit 73; }

sta_matches "$D/sta.after" || { echo 'REJECT sta_changed_or_incomplete' >&2; exit 73; }
attached=0

elapsed=$(( $(date +%s) - first ))
printf 'schema=mt6639-aware-once-v1\nfeature_visible=true\nattach_rc=%s\nteardown_rc=%s\nelapsed_s=%s\naware_before=false\naware_active=true\naware_after=false\nsta_unchanged=true\npublish_subscribe=not_run\ndatapath=not_run\n' \
    "$attach_rc" "$teardown_rc" "$elapsed" > "$D/summary" || exit 74
cat "$D/summary"
echo RESULT=aware_attach_teardown_complete
