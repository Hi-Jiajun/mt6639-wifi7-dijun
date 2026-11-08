#!/system/bin/sh
# One bounded, credential-free multi-AP request for a saved 2.4GHz network. This is not
# a persistent policy. The private task directory must contain sta_mode.sh and
# sta-mode.dex; output must remain shell_data_file for Binder shell commands.
set -u
umask 077
D=${1:-}
ACTION=${2:-}
case "$D" in /data/local/tmp/mt6639-sta-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(readlink -f "$D")" = "$D" ] &&
    [ "$(stat -c %u "$D")" = 0 ] && [ "$(stat -c %a "$D")" = 700 ] &&
    [ "$(id -u)" = 0 ] || exit 77
SELF=$D/sta_multiinternet_once.sh
MODE=$D/sta_mode.sh
GATE=$D/dualsta_gate.sh
for helper in "$SELF" "$MODE" "$GATE" "$D/sta-mode.dex"; do
    [ -f "$helper" ] && [ ! -L "$helper" ] && [ "$(stat -c %u "$helper")" = 0 ] || exit 77
done
REQUEST_KEY=NoSsid_2g_internet

wifi() { timeout -k 2 8 cmd wifi "$@"; }
mode() { sh "$MODE" "$@"; }
boot_ok() { [ "$(cat /proc/sys/kernel/random/boot_id)" = "$(cat "$D/boot.before")" ]; }

recover() {
    exec 9>"$D/recover.lock"
    flock -x 9 9>&9 || return 1
    boot_ok || { echo boot_changed >&2; return 78; }
    rc=0
    request_owned=0
    if [ -f "$D/request.intent" ]; then
        wifi list-requests > "$D/requests.recover" 2>&1 || rc=1
        if grep -q "^$REQUEST_KEY[[:space:]]" "$D/requests.recover"; then
            request_owned=1
            wifi remove-request "$REQUEST_KEY" > "$D/request.remove" 2>&1 || rc=1
        fi
        wifi list-requests > "$D/requests.after" 2>&1 || rc=1
        grep -q "^$REQUEST_KEY[[:space:]]" "$D/requests.after" && rc=1
    fi
    if [ -f "$D/mode.intent" ]; then
        if mode inspect > "$D/mode.recover" 2>&1; then
            current=$(sed -n 's/^before_mode=//p' "$D/mode.recover")
            case $current in
                2)
                    if [ "$request_owned" = 1 ] ||
                        { [ -s "$D/mode.enable" ] &&
                          grep -Fxq 'before_mode=0' "$D/mode.enable" &&
                          grep -Fxq 'after_mode=2' "$D/mode.enable"; }; then
                        mode compare-set 2 0 > "$D/mode.restore" 2>&1 || rc=1
                    else
                        echo mode_restore_ownership_unverified > "$D/mode.restore"
                        rc=1
                    fi
                    ;;
                0) echo already_original > "$D/mode.restore" ;;
                *) echo foreign_mode > "$D/mode.restore"; rc=1 ;;
            esac
        else rc=1; fi
    fi
    if [ -f "$D/selection.before.parcel" ]; then
        mode selection-restore "$D" > "$D/selection.restore" 2>&1 || rc=1
    fi
    if [ -f "$D/gate.intent" ]; then
        sh "$GATE" "$D" disarm > "$D/gate.disarm" 2>&1 || rc=1
    fi
    echo "$rc" > "$D/recovery.rc"
    return "$rc"
}

case $ACTION in
    guardian)
        echo "$$" > "$D/guardian.pid"
        : > "$D/guardian.ready"
        n=0
        while [ "$n" -lt 40 ] && [ ! -f "$D/cleanup.request" ]; do
            sleep 1
            n=$((n + 1))
        done
        recover
        exit $?
        ;;
    recover) recover; exit $? ;;
    run) ;;
    *) exit 64 ;;
esac

exec 8>"$D/owner.lock"
flock -n 8 8>&8 || exit 75
(set -C; : > "$D/attempt") 2>/dev/null || exit 73
[ "$(getprop ro.product.device)" = dijun ] && [ "$(getenforce)" = Enforcing ] || exit 78
[ "$(uname -r)" = 6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k ] || exit 78
ko_sha=$(sha256sum /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko | cut -d ' ' -f 1)
[ "$ko_sha" = 54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea ] || exit 78
id > "$D/id.before"
grep -q 'context=u:r:ksu:s0' "$D/id.before" || exit 77
cat /proc/sys/kernel/random/boot_id > "$D/boot.before"
mode inspect > "$D/mode.before" 2>&1 || exit 69
grep -qx 'multi_internet_supported=true' "$D/mode.before" || exit 69
grep -qx 'dbs_supported=true' "$D/mode.before" || exit 69
grep -qx 'before_mode=0' "$D/mode.before" || exit 78
wifi list-requests > "$D/requests.before" 2>&1 || exit 69
grep -qx 'No active requests' "$D/requests.before" || exit 75
wifi status > "$D/status.before" 2>&1 || exit 69
grep -q 'Supplicant state: COMPLETED' "$D/status.before" || exit 69

mkdir "$D/backup" || exit 73
for file in /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml /odm/firmware/wifi.cfg; do
    [ -f "$file" ] || exit 66
    dest=$D/backup/$(basename "$file")
    cp -p "$file" "$dest" || exit 74
    original=$(sha256sum "$file" | cut -d ' ' -f 1)
    saved=$(sha256sum "$dest" | cut -d ' ' -f 1)
    [ "$original" = "$saved" ] || exit 74
    echo "$original $file" >> "$D/backup/SHA256-before.txt"
done
sha256sum "$D/sta-mode.dex" "$MODE" "$GATE" "$SELF" >> "$D/backup/SHA256-before.txt" || exit 74

nohup sh "$SELF" "$D" guardian > "$D/guardian.log" 2>&1 8>&8 < /dev/null &
guard=$!
n=0
while [ ! -f "$D/guardian.ready" ] && [ "$n" -lt 5 ]; do sleep 1; n=$((n + 1)); done
[ -f "$D/guardian.ready" ] && kill -0 "$guard" || exit 70
trap ': > "$D/cleanup.request"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

exec 9>"$D/recover.lock"
flock -x 9 9>&9 || exit 75
[ ! -f "$D/recovery.rc" ] && kill -0 "$guard" || exit 70
: > "$D/selection.intent"
if ! mode selection-apply "$D" > "$D/selection.apply" 2>&1; then
    flock -u 9 9>&9
    exec 9>&-
    echo 1 > "$D/action.rc"
    exit 71
fi
: > "$D/mode.intent"
# Arm only after all state/ownership snapshots exist. The guardian disarms it
# even when later framework steps fail or the runner is interrupted.
if ! sh "$GATE" "$D" arm 60 > "$D/gate.arm" 2>&1; then
    action_rc=1
    flock -u 9 9>&9
    exec 9>&-
    echo "$action_rc" > "$D/action.rc"
    exit 71
fi
# Mode 1 only considers the primary SSID/networkId. Split SSIDs require mode 2.
if mode compare-set 0 2 > "$D/mode.enable" 2>&1; then
    : > "$D/request.intent"
    wifi add-request -s -d 2 -i > "$D/request.add" 2>&1
    action_rc=$?
    flock -u 9 9>&9
    exec 9>&-
    if [ "$action_rc" = 0 ]; then
        n=0
        while [ "$n" -lt 6 ] && [ ! -f "$D/recovery.rc" ]; do
            wifi status > "$D/status.$n" 2>&1
            ip -4 addr > "$D/addr.$n" 2>&1
            ip -4 route show table all > "$D/route.$n" 2>&1
            sleep 2
            n=$((n + 1))
        done
        ip -4 -o addr show > "$D/addr.short" 2>&1
        for iface in $(awk '$2 ~ /^wlan[0-9]+$/ {print $2}' "$D/addr.short"); do
            timeout -k 1 4 ping -I "$iface" -c 2 -W 1 198.51.100.242 > "$D/ping.$iface" 2>&1
        done
    fi
else
    action_rc=1
    flock -u 9 9>&9
    exec 9>&-
fi
echo "$action_rc" > "$D/action.rc"
: > "$D/cleanup.request"
n=0
while kill -0 "$guard" 2>/dev/null && [ "$n" -lt 30 ]; do sleep 1; n=$((n + 1)); done
wait "$guard"
guard_rc=$?
echo "$guard_rc" > "$D/guardian.rc"
wifi status > "$D/status.after" 2>&1 || exit 71
mode inspect > "$D/mode.after" 2>&1 || exit 71
grep -qx 'before_mode=0' "$D/mode.after" || exit 71
[ "$action_rc" = 0 ] && [ "$guard_rc" = 0 ] && [ "$(cat "$D/recovery.rc")" = 0 ] || exit 71
echo RESULT=trial_finished_network_evidence_requires_review
