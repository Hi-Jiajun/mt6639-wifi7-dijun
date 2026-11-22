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
DEVICE=$(getprop ro.product.device)
case "$DEVICE" in dijun|violin|jinghu) ;; *) reject wrong_device ;; esac
APEX_APP_ROOT=/apex/com.android.bt/app
BOOT=$(cat /proc/sys/kernel/random/boot_id) || exit 70
case "$BOOT" in ''|*[!a-f0-9-]*) reject invalid_boot_id ;; esac
BASE=/data/adb/mt6639-bt
[ ! -L "$BASE" ] || reject state_root_symlink
[ ! -e "$BASE" ] || [ -d "$BASE" ] || reject state_root_not_dir
(umask 077; mkdir -p -m 0700 "$BASE") || exit 70
[ -d "$BASE" ] && [ ! -L "$BASE" ] || reject state_root_not_dir
case "$BASE" in
    /data/adb/*) expected_uid=0 ;;
    *) expected_uid=$(stat -c %u "$MODDIR" 2>/dev/null) || exit 70 ;;
esac
[ "$(stat -c %u "$BASE" 2>/dev/null)" = "$expected_uid" ] || reject state_root_foreign_owner
chmod 0700 "$BASE" || exit 70
[ ! -L "$BASE" ] && [ "$(stat -c '%u:%a' "$BASE" 2>/dev/null)" = "$expected_uid:700" ] || reject state_root_mode
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

resolve_bluetooth_apk() {
    [ -d "$APEX_APP_ROOT" ] && [ ! -L "$APEX_APP_ROOT" ] || return 1
    found=
    count=0
    for candidate in "$APEX_APP_ROOT"/*/Bluetooth.apk "$APEX_APP_ROOT"/Bluetooth.apk; do
        [ -e "$candidate" ] || [ -L "$candidate" ] || continue
        [ -f "$candidate" ] && [ ! -L "$candidate" ] || return 1
        parent_dir=${candidate%/*}
        [ -d "$parent_dir" ] && [ ! -L "$parent_dir" ] || return 1
        found=$candidate
        count=$((count + 1))
    done
    [ "$count" = 1 ] || return 1
    printf '%s\n' "$found"
}

check_bt_compatibility() {
    table=$MODDIR/compatibility.conf
    [ -f "$table" ] && [ ! -L "$table" ] || reject compatibility_table_invalid
    actual_sha=$1
    bt_profile=$(awk -v device="$DEVICE" -v actual="$actual_sha" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        NF != 3 { invalid=1; next }
        $1 !~ /^[a-z0-9][a-z0-9-]*$/ { invalid=1; next }
        length($3) != 64 || $3 ~ /[^0-9a-f]/ { invalid=1; next }
        seen_id[$1]++ { invalid=1; next }
        {
            n_devs = split($2, devs, ",")
            if (n_devs < 1) { invalid=1; next }
            delete row_devs
            dev_match = 0
            for (d = 1; d <= n_devs; d++) {
                if (devs[d] !~ /^(dijun|violin|jinghu)$/ || row_devs[devs[d]]++) invalid=1
                if (seen_dev_hash[devs[d], $3]++) invalid=1
                if (devs[d] == device) dev_match = 1
            }
            if (dev_match) {
                dev_rows++
                if ($3 == actual) { matched=$1; found++ }
            }
        }
        END {
            if (invalid) exit 4
            if (dev_rows < 1) exit 5
            if (found != 1) exit 6
            print matched
        }
    ' "$table")
    rc=$?
    case "$rc" in
        0) ;;
        4) reject compatibility_table_invalid ;;
        5) reject wrong_device ;;
        *) reject unsupported_bluetooth_apk ;;
    esac
}

[ ! -e "$MODDIR/disable" ] && [ ! -e "$MODDIR/remove" ] || reject module_disabled_or_removing
bt_apk=$(resolve_bluetooth_apk) || reject unsupported_bluetooth_apex
apk_hash=$(sha256sum "$bt_apk" 2>/dev/null) || reject unsupported_bluetooth_apex
check_bt_compatibility "${apk_hash%% *}"
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
