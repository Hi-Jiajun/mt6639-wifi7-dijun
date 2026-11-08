#!/system/bin/sh
# Nonpersistent properties only. Never toggles Bluetooth, changes pairings,
# or overrides controller capability bits. Records are retained outside module.
set -u
umask 077
MODDIR=$(CDPATH= cd -- "${0%/*}/.." && pwd) || exit 70
operation=${1:-status}
case "$operation" in status|enable|restore|toggle) ;; *) echo 'usage: control.sh status|enable|restore|toggle' >&2; exit 64 ;; esac
reject() { echo "REJECT $*" >&2; exit 69; }
[ "$(id -u)" = 0 ] || reject not_root
[ "$(id -Z)" = u:r:ksu:s0 ] || reject not_kernelsu_context
[ "$(getenforce)" = Enforcing ] || reject selinux_not_enforcing
[ "$(getprop ro.product.device)" = dijun ] || reject wrong_device
BOOT=$(cat /proc/sys/kernel/random/boot_id) || exit 70
case "$BOOT" in ''|*[!a-f0-9-]*) reject invalid_boot_id ;; esac
BASE=/data/adb/mt6639-bt
[ ! -L "$BASE" ] || reject state_root_symlink
mkdir -p "$BASE" || exit 70
chmod 700 "$BASE" || exit 70
exec 9>"$BASE/control.lock" || exit 70
flock -n 9 9>&9 || reject busy
STATE=$BASE/$BOOT
[ ! -L "$STATE" ] || reject state_symlink
KEYS='bluetooth.profile.bap.unicast.client.enabled
bluetooth.profile.bap.broadcast.source.enabled
bluetooth.profile.bap.broadcast.assist.enabled
bluetooth.profile.csip.set_coordinator.enabled
bluetooth.profile.vcp.controller.enabled
bluetooth.profile.mcp.server.enabled'

valid_key() {
    case "$1" in
        bluetooth.profile.bap.unicast.client.enabled|bluetooth.profile.bap.broadcast.source.enabled|bluetooth.profile.bap.broadcast.assist.enabled|bluetooth.profile.csip.set_coordinator.enabled|bluetooth.profile.vcp.controller.enabled|bluetooth.profile.mcp.server.enabled) return 0 ;;
        *) return 1 ;;
    esac
}

verify_snapshot() {
    [ -f "$STATE/before.tsv" ] && [ ! -L "$STATE/before.tsv" ] || return 1
    [ -f "$STATE/SHA256-before.txt" ] && [ ! -L "$STATE/SHA256-before.txt" ] || return 1
    # Compare a fixed filename; never run filenames supplied by a state file.
    expected=$(cat "$STATE/SHA256-before.txt") || return 1
    actual=$(sha256sum "$STATE/before.tsv") || return 1
    [ "${actual%% *}" = "$expected" ] || return 1
    count=0
    seen=' '
    while read -r key value extra; do
        valid_key "$key" || return 1
        [ -z "${extra:-}" ] || return 1
        case "$value" in true|false) ;; *) return 1 ;; esac
        case "$seen" in *" $key "*) return 1 ;; esac
        seen="$seen$key "
        count=$((count+1))
    done < "$STATE/before.tsv"
    [ "$count" = 6 ]
}

write_bool() {
    setprop "$1" "$2" || return 1
    [ "$(getprop "$1")" = "$2" ]
}

restore() {
    [ -d "$STATE" ] || { echo 'state=idle'; return 0; }
    verify_snapshot || { echo 'REJECT invalid_snapshot' >&2; return 70; }
    # Check all values before changing any; do not overwrite foreign values.
    while read -r key value; do
        current=$(getprop "$key")
        if [ "$current" != "$value" ] && [ "$current" != true ]; then
            echo "REJECT foreign_value $key" >&2
            return 73
        fi
    done < "$STATE/before.tsv"
    failed=0
    while read -r key value; do
        [ "$(getprop "$key")" = "$value" ] || write_bool "$key" "$value" || failed=1
    done < "$STATE/before.tsv"
    [ "$failed" = 0 ] || { echo 'restore=incomplete snapshot_retained=1' >&2; return 70; }
    printf '%s\n' restored > "$STATE/state" || return 70
    echo 'state=restored properties=6 bluetooth_cycle_required_if_already_on=1'
}

status() {
    marker=idle
    [ ! -f "$STATE/state" ] || marker=$(cat "$STATE/state")
    echo "state=$marker boot=$BOOT"
    echo "bluetooth_on=$(settings get global bluetooth_on)"
    for key in $KEYS; do printf '%s=%s\n' "$key" "$(getprop "$key")"; done
    if [ -d "$STATE" ]; then verify_snapshot || return 70; fi
}

if [ "$operation" = toggle ]; then
    if [ -f "$STATE/state" ]; then
        case "$(cat "$STATE/state")" in active|prepared) operation=restore ;; restored) operation=enable ;; *) reject unknown_state ;; esac
    else
        operation=enable
    fi
fi
case "$operation" in
    status) status; exit $? ;;
    restore) restore; exit $? ;;
esac

[ ! -e "$MODDIR/disable" ] && [ ! -e "$MODDIR/remove" ] || reject module_disabled_or_removing
[ "$(getprop ro.build.fingerprint)" = 'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys' ] || reject unsupported_build
apk_hash=$(sha256sum /apex/com.android.bt/app/Bluetooth@CP2A.260605.016/Bluetooth.apk 2>/dev/null) || reject unsupported_bluetooth_apex
[ "${apk_hash%% *}" = 22f2e2ac8c036f7716e792f1fa54d7f5c3dd53754cb3c01db93ee3981cd34339 ] || reject unsupported_bluetooth_apk
[ "$(getprop ro.bluetooth.leaudio_offload.supported)" = true ] || reject no_leaudio_offload_baseline
[ "$(getprop persist.bluetooth.leaudio_offload.disabled)" = false ] || reject offload_disabled
if [ -d "$STATE" ]; then
    verify_snapshot || reject invalid_snapshot
    case "$(cat "$STATE/state" 2>/dev/null)" in
        active)
            for key in $KEYS; do [ "$(getprop "$key")" = true ] || reject active_property_conflict; done
            echo 'state=active idempotent=1'; exit 0 ;;
        restored)
            while read -r key value; do [ "$(getprop "$key")" = "$value" ] || reject restored_baseline_changed; done < "$STATE/before.tsv" ;;
        *) reject incomplete_transaction_use_restore ;;
    esac
else
    # Publish only a fully verified snapshot. A killed/failed preparation leaves
    # a private staging directory, never a broken authority for this boot.
    final_state=$STATE
    STATE=$(mktemp -d "$BASE/.prepare-$BOOT-XXXXXX") || exit 70
    for key in $KEYS; do
        value=$(getprop "$key")
        case "$value" in true|false) ;; *) reject "unexpected_baseline $key" ;; esac
        printf '%s %s\n' "$key" "$value" >> "$STATE/before.tsv.tmp" || exit 70
    done
    mv "$STATE/before.tsv.tmp" "$STATE/before.tsv" || exit 70
    hash=$(sha256sum "$STATE/before.tsv") || exit 70
    printf '%s\n' "${hash%% *}" > "$STATE/SHA256-before.txt" || exit 70
    verify_snapshot || reject snapshot_verification_failed
    printf '%s\n' prepared > "$STATE/state" || exit 70
    [ ! -e "$final_state" ] && [ ! -L "$final_state" ] || reject state_appeared
    mv "$STATE" "$final_state" || exit 70
    STATE=$final_state
fi
# Prepared intent is retained if the caller is killed between property writes.
printf '%s\n' prepared > "$STATE/state" || exit 70
trap 'trap - HUP INT TERM; restore; exit 130' HUP INT TERM
for key in $KEYS; do
    if ! write_bool "$key" true; then
        echo "apply=failed key=$key restoring=1" >&2
        restore
        exit 70
    fi
done
printf '%s\n' active > "$STATE/state" || { restore; exit 70; }
trap - HUP INT TERM
echo 'state=active properties=6 bluetooth_not_restarted=1'
echo 'Host profiles adopt these settings on the next normal Bluetooth enable.'
