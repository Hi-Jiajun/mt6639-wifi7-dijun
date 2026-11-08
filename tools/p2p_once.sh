#!/system/bin/sh
# One non-persistent autonomous GO, then checked teardown. No peer/business claim.
set -u
umask 077
D=${1:-} OP=${2:-} REQUESTED_FREQ=${3:-}
case "$D" in /data/local/tmp/mt6639-p2p-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -Z)" = u:r:ksu:s0 ] &&
    [ "$(getenforce)" = Enforcing ] || exit 77
SELF=$D/p2p_once.sh
if [ -f "$D/go.freq" ] && [ ! -L "$D/go.freq" ]; then
    GO_FREQ=$(cat "$D/go.freq" 2>/dev/null)
else
    GO_FREQ=${REQUESTED_FREQ:-2412}
fi
case "$GO_FREQ" in
    2412) SSID_BASE=DIRECT-mt-Dijun ;;
    6135) SSID_BASE=DIRECT-mt-Dijun-6G ;;
    *) exit 64 ;;
esac
SSID=$SSID_BASE
p2p() { timeout -k 2 6 cmd wifip2p "$@"; }
boot_ok() { [ "$(cat /proc/sys/kernel/random/boot_id)" = "$(cat "$D/boot.before")" ]; }
load_owner_intent() {
    OWNER_INTENT_VALID=0
    OWNER_SSID=
    [ -f "$D/owner.intent" ] && [ ! -L "$D/owner.intent" ] || return 1
    [ "$(stat -c %u "$D/owner.intent")" = 0 ] || return 1
    [ "$(stat -c %a "$D/owner.intent")" = 600 ] || return 1
    [ "$(wc -l < "$D/owner.intent")" -eq 2 ] || return 1
    owner_ssid=$(sed -n 's/^ssid=//p' "$D/owner.intent")
    owner_freq=$(sed -n 's/^frequency=//p' "$D/owner.intent")
    [ "$owner_freq" = "$GO_FREQ" ] || return 1
    suffix=${owner_ssid#"$SSID_BASE-"}
    [ "$suffix" != "$owner_ssid" ] || return 1
    [ "${#suffix}" -eq 8 ] &&
        printf '%s\n' "$suffix" | grep -Eq '^[0-9a-f]{8}$' || return 1
    OWNER_SSID=$owner_ssid
    OWNER_INTENT_VALID=1
}
group_ssid_matches() {
    [ "$(sed -n 's/^network: //p' "$1")" = "$SSID" ]
}
idle() {
    timeout 6 dumpsys wifip2p > "$1" || return 1
    grep -qx ' curState=InactiveState' "$1" && grep -qx 'mGroup null' "$1" &&
        grep -qx 'mDiscoveryStarted false' "$1" && grep -qx 'mListenStarted false' "$1"
}
not_failed() { ! grep -Eq 'FAILED|Exception:|not initialized|UNKNOWN|not available' "$1"; }
recover() {
    exec 9>"$D/recover.lock"
    flock -x 9 9>&9 || return 70
    boot_ok || return 78
    SSID=$SSID_BASE
    load_owner_intent && SSID=$OWNER_SSID
    rc=0
    group_state=not_started
    group_safe=1
    p2p_idle_verified=0
    eht_state=not_requested
    station_state=not_checked
    journal_ok=1
    record_recovery_state() {
        printf 'group_state=%s\ngroup_safe=%s\np2p_idle_verified=%s\neht_state=%s\nstation_state=%s\n' \
            "$group_state" "$group_safe" "$p2p_idle_verified" "$eht_state" \
            "$station_state" > "$D/recovery.resources.tmp" &&
            mv "$D/recovery.resources.tmp" "$D/recovery.resources"
    }
    ensure_private_dir() {
        [ ! -L "$1" ] || return 1
        if [ ! -e "$1" ]; then mkdir "$1" || return 1; fi
        [ -d "$1" ] && [ ! -L "$1" ] &&
            [ "$(stat -c %u "$1")" = 0 ] && [ "$(stat -c %a "$1")" = 700 ]
    }
    group_state=recovery_started
    record_recovery_state || return 74
    if [ -f "$D/init.intent" ]; then
        if p2p get-group-info > "$D/group.recover" 2>&1; then
            snapshot_owned=0
            intent_owned=0
            if [ -s "$D/group.active" ] && [ -s "$D/connection.active" ] &&
                cmp -s "$D/group.active" "$D/group.recover" &&
                group_ssid_matches "$D/group.active" &&
                grep -Eq "frequency:[[:space:]]*$GO_FREQ([[:space:]]|$)" "$D/group.active" &&
                grep -Eq 'networkId:[[:space:]]*-1([[:space:]]|$)' "$D/group.active" &&
                grep -Fq 'groupFormed: true isGroupOwner: true' "$D/connection.active"; then
                snapshot_owned=1
            elif [ "$OWNER_INTENT_VALID" = 1 ] &&
                group_ssid_matches "$D/group.recover" &&
                grep -Eq "frequency:[[:space:]]*$GO_FREQ([[:space:]]|$)" "$D/group.recover" &&
                grep -Eq 'networkId:[[:space:]]*-1([[:space:]]|$)' "$D/group.recover" &&
                p2p get-connection-info > "$D/connection.recover" 2>&1 &&
                grep -Fq 'groupFormed: true isGroupOwner: true' "$D/connection.recover"; then
                intent_owned=1
            fi
            if [ "$snapshot_owned" = 1 ] || [ "$intent_owned" = 1 ]; then
                group_state=remove_pending
                if record_recovery_state; then
                    p2p remove-group > "$D/group.remove" 2>&1 || rc=1
                    not_failed "$D/group.remove" || rc=1
                    group_state=remove_requested
                    if ! record_recovery_state; then
                        group_safe=0
                        journal_ok=0
                        rc=1
                    fi
                else
                    group_state=ownership_verified_but_remove_skipped_journal_failed
                    group_safe=0
                    journal_ok=0
                    rc=1
                fi
            elif grep -qx null "$D/group.recover"; then
                group_state=none
            else
                group_state=foreign_or_unknown
                group_safe=0
                rc=1
                echo 'P2P group unresolved; preserving current group and continuing independent checks' \
                    > "$D/group.unresolved"
            fi
        else
            group_state=query_failed
            group_safe=0
            rc=1
            echo 'P2P group query failed; preserving group state and continuing independent checks' \
                > "$D/group.unresolved"
        fi
        if [ "$group_safe" = 1 ]; then
            n=0
            while [ "$n" -lt 12 ]; do
                idle "$D/p2p.stopping" && break
                sleep 1; n=$((n+1))
            done
            if idle "$D/p2p.after"; then
                p2p_idle_verified=1
            else
                group_safe=0
                rc=1
            fi
            if [ "$p2p_idle_verified" = 1 ]; then
                if p2p get-group-info > "$D/group.after" 2>&1 &&
                    grep -qx null "$D/group.after"; then
                    group_state=group_absence_verified
                else
                    group_safe=0
                    rc=1
                    group_state=group_teardown_unverified
                    echo 'P2P reports a live or unknown group after idle; skipped deinit and global changes' \
                        > "$D/group.unresolved"
                fi
                if [ "$group_safe" = 1 ] &&
                    p2p list-saved-groups > "$D/saved.after" 2>&1 &&
                    cmp -s "$D/saved.before" "$D/saved.after"; then
                    group_state=deinit_pending
                    if record_recovery_state; then
                        if p2p deinit > "$D/deinit" 2>&1 && not_failed "$D/deinit" &&
                            p2p get-state > "$D/channel.after" 2>&1 &&
                            grep -Fq 'P2p client is not initialized' "$D/channel.after"; then
                            group_state=deinit_verified
                        else
                            group_state=deinit_unverified
                            group_safe=0
                            rc=1
                        fi
                    else
                        group_safe=0
                        journal_ok=0
                        rc=1
                        group_state=deinit_skipped_journal_failed
                    fi
                elif [ "$group_safe" = 1 ]; then
                    group_safe=0
                    rc=1
                    group_state=saved_groups_unverified
                fi
            else
                echo 'P2P idle state unresolved; skipped saved-group comparison and deinit' \
                    > "$D/p2p.unresolved"
            fi
        fi
    else
        # No init intent means this runner did not initialize P2P or request a
        # group. Still require an actual idle snapshot before restoring global
        # EHT gates if that earlier stage had already been requested.
        if idle "$D/p2p.before-recover"; then
            p2p_idle_verified=1
        else
            group_safe=0
            rc=1
            group_state=unverified_without_init_intent
            echo 'P2P idle state unresolved without init intent' > "$D/p2p.unresolved"
        fi
    fi
    if ! record_recovery_state; then
        journal_ok=0
        rc=1
    fi
    if [ -f "$D/eht.request" ]; then
        if [ "$group_safe" = 1 ] && [ "$p2p_idle_verified" = 1 ] &&
            [ "$journal_ok" = 1 ]; then
            eht_state=restore_pending
            if record_recovery_state; then
                if sh "$D/p2p_eht_profile.sh" "$D" restore > "$D/eht.restore" 2>&1; then
                    if grep -qx 'eht_state=not_applied' "$D/eht.restore"; then
                        eht_state=not_applied
                    else
                        eht_state=restored_verified
                    fi
                else
                    eht_state=restore_failed
                    rc=1
                fi
            else
                eht_state=restore_skipped_journal_failed
                journal_ok=0
                rc=1
            fi
            if [ "$eht_state" = "restore_skipped_journal_failed" ]; then
                echo 'eht_state=restore_skipped_journal_failed' > "$D/eht.restore"
            fi
        elif [ "$group_safe" != 1 ] || [ "$p2p_idle_verified" != 1 ]; then
            echo 'eht_state=unresolved_p2p_group; no global gate changes attempted' \
                > "$D/eht.restore"
            eht_state=unresolved_p2p_group
            rc=1
        else
            echo 'eht_state=restore_skipped_journal_failed; no global gate changes attempted' \
                > "$D/eht.restore"
            eht_state=restore_skipped_journal_failed
            rc=1
        fi
    fi
    if ! record_recovery_state; then
        journal_ok=0
        rc=1
    fi
    if ensure_private_dir "$D/network-recover" &&
        cp "$D/reconnect.key" "$D/network-recover/reconnect.key"; then
        if sh "$D/twt_recover.sh" check "$D/network-recover" "$(cat "$D/boot.before")" LabNet-6G 6135 02:00:00:00:73:1c 198.51.100.242 /data/adb/modules/mt6639-6g/bin/post-reboot-check.sh > "$D/network.check" 2>&1; then
            station_state=verified
        else
            if [ "$group_safe" = 1 ] && [ "$journal_ok" = 1 ]; then
                if ensure_private_dir "$D/network-select" &&
                    cp "$D/reconnect.key" "$D/network-select/reconnect.key"; then
                    station_state=selection_pending
                    if record_recovery_state; then
                        if sh "$D/twt_recover.sh" select "$D/network-select" "$(cat "$D/boot.before")" LabNet-6G 6135 02:00:00:00:73:1c 198.51.100.242 /data/adb/modules/mt6639-6g/bin/post-reboot-check.sh > "$D/network.select" 2>&1; then
                            station_state=selected_and_verified
                        else
                            station_state=selection_failed
                            rc=1
                        fi
                    else
                        station_state=selection_skipped_journal_failed
                        rc=1
                    fi
                else
                    station_state=selection_dir_unavailable
                    rc=1
                fi
            else
                echo 'station selection deferred because recovery ownership or journal state is unresolved' \
                    > "$D/network.select.deferred"
                if [ "$journal_ok" = 1 ]; then
                    station_state=deferred_p2p_unresolved
                else
                    station_state=deferred_journal_failure
                fi
                rc=1
            fi
        fi
    else
        station_state=recovery_dir_unavailable
        rc=1
    fi
    record_recovery_state || rc=1
    printf '%s\n' "$rc" > "$D/recovery.rc"
    : > "$D/guardian.done"
    return "$rc"
}
case "$OP" in
    inspect)
        idle "$D/p2p.inspect"
        rc=$?
        p2p get-state > "$D/channel.inspect" 2>&1
        echo "idle_rc=$rc"
        cat "$D/channel.inspect"
        exit "$rc" ;;
    guardian)
        echo "$$" > "$D/guardian.pid"
        : > "$D/guardian.ready"
        n=0
        while [ "$n" -lt 65 ] && [ ! -f "$D/cleanup.request" ]; do sleep 1; n=$((n+1)); done
        recover; exit $? ;;
    recover) recover; exit $? ;;
    run|run-eht) ;;
    *) exit 64 ;;
esac
exec 8>"$D/owner.lock"
flock -n 8 8>&8 || exit 75
(set -C; : > "$D/claimed") 2>/dev/null || exit 73
if [ -e "$D/go.freq" ]; then
    [ ! -L "$D/go.freq" ] && [ "$(cat "$D/go.freq")" = "$GO_FREQ" ] || exit 75
else
    printf '%s\n' "$GO_FREQ" > "$D/go.freq" || exit 74
fi
[ "$(getprop ro.build.fingerprint)" = 'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys' ] || exit 69
h=$(sha256sum /apex/com.android.wifi/javalib/service-wifi.jar | cut -d ' ' -f 1)
[ "$h" = 2acb1ba9ff601be9e3817b28a0538afb41374db078120e6a3d4d104fef7671d6 ] || exit 69
[ -s "$D/go.key" ] && [ -s "$D/reconnect.key" ] || exit 66
if [ "$OP" = run-eht ]; then
    [ -f "$D/p2p_eht_profile.sh" ] && [ ! -L "$D/p2p_eht_profile.sh" ] || exit 66
    : > "$D/eht.request" || exit 74
fi
p2p get-state > "$D/channel.before" 2>&1
grep -Fq 'P2p client is not initialized' "$D/channel.before" || exit 75
idle "$D/p2p.before" || exit 75
cat /proc/sys/kernel/random/boot_id > "$D/boot.before" || exit 74
timeout 5 cmd wifi status > "$D/sta.before" || exit 69
grep -Fxq 'Wifi is connected to "LabNet-6G"' "$D/sta.before" || exit 69
mkdir "$D/backup" || exit 73
for file in /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml /data/misc/apexdata/com.android.wifi/WifiConfigStoreP2p.xml /odm/firmware/wifi.cfg; do
    [ -f "$file" ] || continue
    cp -p "$file" "$D/backup/$(basename "$file")" || exit 74
    h=$(sha256sum "$file" | cut -d ' ' -f 1)
    [ "$h" = "$(sha256sum "$D/backup/$(basename "$file")" | cut -d ' ' -f 1)" ] || exit 74
    echo "$h $file" >> "$D/backup/SHA256-before.txt"
done
sha256sum "$SELF" "$D/twt_recover.sh" >> "$D/backup/SHA256-before.txt" || exit 74
if [ -f "$D/eht.request" ]; then
    sha256sum "$D/p2p_eht_profile.sh" >> "$D/backup/SHA256-before.txt" || exit 74
fi
nohup sh "$SELF" "$D" guardian > "$D/guardian.log" 2>&1 8>&8 < /dev/null &
guard=$!
n=0
while [ ! -f "$D/guardian.ready" ] && [ "$n" -lt 5 ]; do sleep 1; n=$((n+1)); done
[ -f "$D/guardian.ready" ] && kill -0 "$guard" || exit 70
trap ': > "$D/cleanup.request"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
exec 9>"$D/recover.lock"
flock -x 9 9>&9 || exit 75
boot_ok && kill -0 "$guard" || exit 78
if [ -f "$D/eht.request" ]; then
    if ! sh "$D/p2p_eht_profile.sh" "$D" enable > "$D/eht.enable" 2>&1; then
        flock -u 9 9>&9
        exec 9>&-
        : > "$D/cleanup.request"
        wait "$guard"
        exit 70
    fi
fi
: > "$D/init.intent"
p2p init > "$D/init" 2>&1
rc=$?
action_rc=1
if [ "$rc" = 0 ] && not_failed "$D/init"; then
    if p2p list-saved-groups > "$D/saved.before" 2>&1 && not_failed "$D/saved.before"; then
        action_rc=0
        if [ -e "$D/owner.intent" ]; then
            load_owner_intent && SSID=$OWNER_SSID || action_rc=1
        else
            nonce=$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n')
            printf '%s\n' "$nonce" | grep -Eq '^[0-9a-f]{8}$' || action_rc=1
            if [ "$action_rc" = 1 ]; then
                SSID=$SSID_BASE
            else
                SSID="$SSID_BASE-$nonce"
                printf 'ssid=%s\nfrequency=%s\n' "$SSID" "$GO_FREQ" > "$D/owner.intent.tmp" &&
                    mv "$D/owner.intent.tmp" "$D/owner.intent" || action_rc=1
            fi
        fi
        if [ "$action_rc" = 0 ]; then
            # Intent preparation succeeded; creation must succeed independently.
            action_rc=1
            pass=$(cat "$D/go.key")
            : > "$D/group.intent"
            # The OEM shell passes this integer directly to Builder. MHz avoids its
            # misleading 2/5 help text and makes the desired band unambiguous.
            p2p create-group-with-config "$SSID" "$pass" "$GO_FREQ" false > "$D/create" 2>&1
            create_rc=$?
            unset pass
            if [ "$create_rc" = 0 ] && not_failed "$D/create"; then action_rc=0; fi
        fi
    fi
fi
flock -u 9 9>&9
exec 9>&-
if [ "$action_rc" = 0 ]; then
    sleep 5
    p2p get-group-info > "$D/group.active" 2>&1 || action_rc=1
    p2p get-connection-info > "$D/connection.active" 2>&1 || action_rc=1
    timeout 6 iw dev > "$D/interfaces.active" 2>&1
    timeout 6 ip -4 addr > "$D/addr.active" 2>&1
    timeout 6 cmd wifi status > "$D/sta.active" 2>&1
    if [ -f "$D/eht.request" ]; then
        timeout 5 iwpriv wlan0 driver 'show-mld 255' > "$D/eht.host.active" 2>&1
        timeout 5 dumpsys wifip2p > "$D/p2p.active" 2>&1
    fi
    group_ssid_matches "$D/group.active" &&
        grep -Eq "frequency:[[:space:]]*$GO_FREQ([[:space:]]|$)" "$D/group.active" &&
        grep -Eq 'networkId:[[:space:]]*-1([[:space:]]|$)' "$D/group.active" || action_rc=1
    grep -Fq 'groupFormed: true isGroupOwner: true' "$D/connection.active" || action_rc=1
fi
printf '%s\n' "$action_rc" > "$D/action.rc"
: > "$D/cleanup.request"
wait "$guard"
guard_rc=$?
[ "$action_rc" = 0 ] && [ "$guard_rc" = 0 ] && [ "$(cat "$D/recovery.rc")" = 0 ] || exit 71
if [ -f "$D/eht.request" ]; then
    echo "RESULT=GO_created ssid=$SSID frequency_mhz=$GO_FREQ driver_EHT_gates_restored peer_EHT_business_unverified"
else
    echo "RESULT=GO_created_and_removed ssid=$SSID frequency_mhz=$GO_FREQ peer_business_unverified"
fi
