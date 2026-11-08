#!/system/bin/sh
# Runtime driver gates only. EHT negotiation and peer traffic are separate checks.
set -u
umask 077
D=${1:-} OP=${2:-}
case "$D" in /data/local/tmp/mt6639-p2p-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] &&
    [ "$(id -Z)" = u:r:ksu:s0 ] && [ "$(getenforce)" = Enforcing ] || exit 77
case "$OP" in inspect|enable|restore) ;; *) exit 64 ;; esac
cfg() {
    out=$(timeout 5 iwpriv wlan0 driver "GET_CFG $1") || return 1
    value=$(printf '%s\n' "$out" | sed -n 's/.*driver:[[:space:]]*//p' | tr -d '\r')
    case "$value" in 0|0x0) echo 0 ;; 1|0x1) echo 1 ;; *) return 1 ;; esac
}
host_dump() { timeout 5 iwpriv wlan0 driver 'show-mld 255'; }
host_value() {
    value=$(printf '%s\n' "$host" | sed -n "s/^$1://p" | tr -d '\r')
    case "$value" in 0|1) printf '%s\n' "$value" ;; *) return 1 ;; esac
}
read_values() {
    host=$(host_dump) || return 1
    for key in P2pGoEHT P2pGcEHT; do
        c=$(cfg "$key") && h=$(host_value "$key") && [ "$c" = "$h" ] || return 1
        printf '%s %s\n' "$key" "$c"
    done
}
idle() {
    state=$(timeout 5 dumpsys wifip2p) || return 1
    for line in ' curState=InactiveState' 'mGroup null' 'mDiscoveryStarted false' 'mListenStarted false'; do
        printf '%s\n' "$state" | grep -Fxq "$line" || return 1
    done
}
boot_ok() { [ "$(cat /proc/sys/kernel/random/boot_id)" = "$(cat "$D/boot.before")" ]; }
verify_snapshot() {
    boot_ok && (cd "$D" && sha256sum -c eht.before.sha256 >/dev/null 2>&1)
}
restore() {
    [ -f "$D/eht.intent" ] || { echo 'eht_state=not_applied'; return 0; }
    verify_snapshot && idle || return 73
    read_values > "$D/eht.current" || return 70
    while read -r key before; do
        current=$(sed -n "s/^$key //p" "$D/eht.current")
        [ "$current" = "$before" ] || [ "$current" = 1 ] || return 73
    done < "$D/eht.before"
    failed=0
    while read -r key before; do
        timeout 5 iwpriv wlan0 driver "SET_CFG $key $before" >> "$D/eht.restore.log" 2>&1 || failed=1
    done < "$D/eht.before"
    read_values > "$D/eht.after" || failed=1
    cmp -s "$D/eht.before" "$D/eht.after" || failed=1
    [ "$failed" = 0 ] || return 70
    echo restored > "$D/eht.state"
    echo 'eht_state=restored config_and_host=original'
}
exec 7>"$D/eht.lock"
flock -n 7 7>&7 || exit 75
if [ "$OP" = inspect ]; then read_values; exit $?; fi
if [ "$OP" = restore ]; then restore; exit $?; fi
[ "$(getprop ro.build.fingerprint)" = 'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys' ] || exit 69
hash=$(sha256sum /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko) || exit 69
[ "${hash%% *}" = 54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea ] || exit 69
boot_ok && idle || exit 73
# Only the owning, bounded runner may apply the gates. Its recovery calls this
# helper after normal group teardown. No free-standing persistent enable mode.
[ -f "$D/guardian.ready" ] && [ -f "$D/eht.request" ] &&
    [ ! -e "$D/eht.intent" ] && [ ! -e "$D/eht.before" ] || exit 73
read_values > "$D/eht.before" || exit 70
(cd "$D" && sha256sum eht.before boot.before > eht.before.sha256) || exit 70
verify_snapshot || exit 73
: > "$D/eht.intent" || exit 74
failed=0
for key in P2pGoEHT P2pGcEHT; do
    timeout 5 iwpriv wlan0 driver "SET_CFG $key 1" >> "$D/eht.apply.log" 2>&1 || failed=1
done
read_values > "$D/eht.applied" || failed=1
printf 'P2pGoEHT 1\nP2pGcEHT 1\n' > "$D/eht.expected"
cmp -s "$D/eht.applied" "$D/eht.expected" || failed=1
if [ "$failed" != 0 ]; then restore; exit 70; fi
echo active > "$D/eht.state"
echo 'eht_state=active config_and_host_GO=1 config_and_host_GC=1 negotiation=unverified'
