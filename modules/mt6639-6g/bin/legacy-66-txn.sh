#!/system/bin/sh
# Checked shell fallback executor for Linux 6.6.* kernels (HyperOS 3) where
# STATX_MNT_ID_UNIQUE (0x4000) is unavailable and frozen mount-txn refuses.
# Mirrors native.sh / mount-txn status, guardian.log, and recovery contracts.
set -u
umask 077
OP=${1:-}
MODDIR=${2:-}
TX=${3:-}
HOLD_MS=${4:-900000}

[ -n "$MODDIR" ] || exit 64
. "$MODDIR/bin/lib.sh"

valid_tx() {
    case "$1" in
        "$WORK"/transactions/t-*)
            suffix=${1#"$WORK"/transactions/t-}
            case "$suffix" in ''|*[!a-zA-Z0-9-]*) return 1 ;; esac ;;
        *) return 1 ;;
    esac
    [ -d "$1" ] && [ ! -L "$1" ]
}

read_country_baseline() {
    eff=$(wifi get-country-code 2>/dev/null | sed -n 's/^Wifi Country Code = \([A-Z0-9][A-Z0-9]\)$/\1/p' | head -1)
    [ -n "$eff" ] || return 1
    over_line=$(sh_shell 'dumpsys wifi | grep -m1 "mOverrideCountryCode:"' 2>/dev/null | tr -d '\r') || return 1
    over_val=${over_line#*mOverrideCountryCode: }
    case "$over_val" in
        null) country_override=0; override_code= ;;
        [A-Z0-9][A-Z0-9]) country_override=1; override_code=$over_val ;;
        *) return 1 ;;
    esac
    country_code=$eff
    return 0
}

cmd_inspect_wlan() {
    scan=$(read_setting wifi_scan_always_enabled | tr -d '[:space:]') || exit 4
    case "$scan" in 0|1) ;; *) exit 4 ;; esac
    wifi_on=$(read_setting wifi_on | tr -d '[:space:]') || exit 4
    case "$wifi_on" in 0|1) ;; *) exit 4 ;; esac
    read_country_baseline || exit 4
    printf '{"scan":%s,"wifi":%s,"country_override":%s,"override_code":"%s","country_code":"%s","read_only":true}\n' \
        "$scan" "$wifi_on" "$country_override" "$override_code" "$country_code"
}

cmd_inspect_module() {
    if [ -d "$MODDIR" ] && [ ! -L "$MODDIR" ] &&
       [ ! -e "$MODDIR/disable" ] && [ ! -e "$MODDIR/remove" ]; then
        printf '{"status":"module_control_available","module_state":"enabled","read_only":true}\n'
        return 0
    fi
    printf '{"status":"module_control_unavailable","module_state":"disabled_or_missing","read_only":true}\n'
    return 4
}

log_event() {
    printf '{"event":"%s","guardian_pid":%s,"operation":"%s","restoration":"%s"}\n' \
        "$1" "$$" "$2" "$3" >> "$TX/guardian.log"
}

write_candidate() {
    [ -f "$CFG" ] && [ ! -L "$CFG" ] || return 1
    [ "$(file_sha "$CFG")" = "$STOCK_CFG_SHA" ] || return 1
    sed 's/^DisallowBand6G 1$/DisallowBand6G 0/' "$CFG" > "$TX/candidate" || return 1
    lines_stock=$(wc -l < "$CFG" | tr -d ' ')
    lines_cand=$(wc -l < "$TX/candidate" | tr -d ' ')
    old_stock=$(grep -c '^DisallowBand6G 1$' "$CFG" || true)
    old_cand=$(grep -c '^DisallowBand6G 1$' "$TX/candidate" || true)
    new_cand=$(grep -c '^DisallowBand6G 0$' "$TX/candidate" || true)
    [ "$lines_stock" = "$lines_cand" ] && [ "$old_stock" = 1 ] &&
        [ "$old_cand" = 0 ] && [ "$new_cand" = 1 ] || return 1
    chmod 0644 "$TX/candidate" || return 1
    ctx=$(stat -c %C "$CFG" 2>/dev/null || printf 'u:object_r:wlan_fwfile:s0')
    chcon "$ctx" "$TX/candidate" 2>/dev/null || chcon u:object_r:wlan_fwfile:s0 "$TX/candidate" 2>/dev/null || true
}

unmount_checked() {
    cand_sha=$(file_sha "$TX/candidate")
    mounted=0
    awk -v target="$CFG" '$5 == target { found=1 } END { exit(found ? 0 : 1) }' "$MOUNTINFO_FILE" 2>/dev/null && mounted=1
    if [ "$mounted" = 1 ]; then
        [ -n "$cand_sha" ] && [ "$(file_sha "$CFG")" = "$cand_sha" ] || return 4
        umount "$CFG" 2>/dev/null || umount -l "$CFG" 2>/dev/null || return 4
    fi
    require_unmounted >/dev/null 2>&1 || return 4
    require_unmounted "$INIT_MOUNTINFO_FILE" >/dev/null 2>&1 || return 4
    [ "$(file_sha "$CFG")" = "$STOCK_CFG_SHA" ] || return 4
    [ "$(file_sha "$INIT_CFG_PATH")" = "$STOCK_CFG_SHA" ] || return 4
    return 0
}

wait_network_ready() {
    n=0
    while [ "$n" -lt 45 ]; do
        if wifi status 2>/dev/null | grep -q 'Wifi is connected to'; then
            addr_out=$(timeout 5 ip -4 addr show dev wlan0 2>/dev/null || timeout 5 ip -4 addr show wlan0 2>/dev/null || true)
            if printf '%s\n' "$addr_out" | grep -q ' inet '; then
                return 0
            fi
        fi
        sleep 1
        n=$((n + 1))
    done
    return 1
}

recover_tx() {
    unmount_checked || return 4
    [ -f "$TX/wlan-intent" ] || return 0
    . "$TX/baseline.env" || return 6
    if [ ! -f "$TX/wlan-restored" ]; then
        ok=1
        wifi set-scan-always-available disabled >/dev/null 2>&1 || ok=0
        if [ "${country_mutated:-0}" = 1 ]; then
            if [ "${country_override:-0}" = 1 ]; then
                case "${override_code:-}" in
                    [A-Z][A-Z]) wifi force-country-code enabled "$override_code" >/dev/null 2>&1 || ok=0 ;;
                esac
            else
                case "${baseline_code:-CN}" in
                    [A-Z][A-Z]) wifi force-country-code enabled "${baseline_code:-CN}" >/dev/null 2>&1 || ok=0 ;;
                    *) wifi force-country-code disabled >/dev/null 2>&1 || ok=0 ;;
                esac
            fi
        fi
        wifi set-wifi-enabled disabled >/dev/null 2>&1 || ok=0
        sleep 8
        wifi set-wifi-enabled enabled >/dev/null 2>&1 || ok=0
        sleep 12
        if [ "${country_mutated:-0}" = 1 ] && [ "${country_override:-0}" = 0 ]; then
            wifi force-country-code disabled >/dev/null 2>&1 || ok=0
        fi
        if [ "${scan_before:-1}" = 1 ]; then
            wifi set-scan-always-available enabled >/dev/null 2>&1 || ok=0
        else
            wifi set-scan-always-available disabled >/dev/null 2>&1 || ok=0
        fi
        sh_shell "settings put global wifi_scan_always_enabled ${scan_before:-1}" >/dev/null 2>&1 || ok=0
        wifi start-scan >/dev/null 2>&1 || true
        [ "$ok" = 1 ] || return 6
        printf 'restored\n' > "$TX/wlan-restored" || return 6
    fi
    if [ ! -f "$TX/wlan-net-ready" ]; then
        if [ "${connected_before:-1}" = 0 ] || wait_network_ready; then
            printf 'ready\n' > "$TX/wlan-net-ready" || return 6
        else
            return 7
        fi
    fi
    return 0
}

run_guardian() {
    mode=$1
    exec 8>"$TX/guardlock" || exit 3
    flock -n 8 8>&8 || exit 3
    stopping=0
    trap 'stopping=1' TERM INT
    trap '' HUP PIPE

    . "$TX/baseline.env" || exit 4
    mount --bind "$TX/candidate" "$CFG" || {
        log_event terminal mount_failed no_mount_effects
        printf 'failed\n' > "$TX/ready.err"
        exit 4
    }
    mount -o remount,bind,ro "$CFG" 2>/dev/null || true
    if [ "$(file_sha "$INIT_CFG_PATH")" != "$(file_sha "$TX/candidate")" ]; then
        unmount_checked || true
        log_event terminal bind_not_visible_to_init restored
        printf 'failed\n' > "$TX/ready.err"
        exit 4
    fi
    printf 'published\n' > "$TX/prepared"
    log_event published published pending

    printf 'intent\n' > "$TX/wlan-intent"
    apply_ok=1
    wifi set-scan-always-available disabled >/dev/null 2>&1 || apply_ok=0
    if [ "${country_mutated:-0}" = 1 ] && [ -n "${target_country:-}" ]; then
        wifi force-country-code enabled "$target_country" >/dev/null 2>&1 || apply_ok=0
    fi
    wifi set-wifi-enabled disabled >/dev/null 2>&1 || apply_ok=0
    sleep 8
    wifi set-wifi-enabled enabled >/dev/null 2>&1 || apply_ok=0
    sleep 12

    if [ "$apply_ok" != 1 ]; then
        printf 'failed\n' > "$TX/ready.err"
        recover_tx
        rc=$?
        log_event terminal wlan_apply_failed "$( [ "$rc" = 0 ] && echo restored || echo blocked )"
        exit 4
    fi

    : > "$TX/ready.ok"
    op_reason=cancelled
    if [ "$mode" = persistent ]; then
        log_event persistent-hold module_lifecycle_watch pending
        while [ "$stopping" = 0 ]; do
            if [ -f "$TX/cancel" ]; then op_reason=cancelled; break; fi
            if [ ! -d "$MODDIR" ] || [ -L "$MODDIR" ]; then op_reason=module_directory_changed; break; fi
            if [ -e "$MODDIR/disable" ]; then op_reason=module_disabled; break; fi
            if [ -e "$MODDIR/remove" ]; then op_reason=module_removed; break; fi
            sleep 1
        done
    else
        hold_s=$((HOLD_MS / 1000))
        [ "$hold_s" -gt 0 ] || hold_s=1
        elapsed=0
        op_reason=deadline
        while [ "$stopping" = 0 ] && [ "$elapsed" -lt "$hold_s" ]; do
            if [ -f "$TX/cancel" ]; then op_reason=cancelled; break; fi
            sleep 1
            elapsed=$((elapsed + 1))
        done
    fi

    recover_tx
    rc=$?
    case "$rc" in
        0) rest=restored ;;
        6) rest=settings_failed ;;
        7) rest=network_not_ready ;;
        *) rest=blocked ;;
    esac
    log_event terminal "$op_reason" "$rest"
    [ "$rc" = 0 ] && exit 0 || exit "$rc"
}

cmd_run() {
    mode=$1
    valid_tx "$TX" || exit 64
    exec 7>"$TX/launch.lock" || exit 3
    flock -n 7 7>&7 || { printf '{"status":"busy_or_invalid_lock"}\n'; exit 3; }
    if [ -e "$TX/guardlock" ] && ! (exec 8<>"$TX/guardlock" && flock -n 8 8>&8) 2>/dev/null; then
        printf '{"status":"busy_or_invalid_lock"}\n'
        exit 3
    fi
    if [ "$mode" = persistent ] && ! cmd_inspect_module >/dev/null 2>&1; then
        printf '{"status":"module_control_unavailable"}\n'
        exit 4
    fi
    require_identity >/dev/null 2>&1 || exit 4
    require_unmounted >/dev/null 2>&1 || exit 4
    scan=$(read_setting wifi_scan_always_enabled | tr -d '[:space:]') || exit 4
    case "$scan" in 0|1) ;; *) exit 4 ;; esac
    wifi_on=$(read_setting wifi_on | tr -d '[:space:]') || exit 4
    [ "$wifi_on" = 1 ] || exit 4
    read_country_baseline || exit 4
    pref=${MT6639_TARGET_COUNTRY:-}
    [ -n "$pref" ] || pref=$(read_target_country) || exit 4
    resolve_target_country "$pref" "$country_override" "$override_code" "$country_code" || exit 4

    connected_before=0
    if wifi status 2>/dev/null | grep -q 'Wifi is connected to'; then
        connected_before=1
    fi

    printf 'legacy-66\n' > "$TX/legacy-66" || exit 4
    {
        printf 'scan_before=%s\n' "$scan"
        printf 'country_override=%s\n' "$country_override"
        printf 'override_code=%s\n' "$override_code"
        printf 'baseline_code=%s\n' "$country_code"
        printf 'country_mutated=%s\n' "$RESOLVED_MUTATE"
        printf 'target_country=%s\n' "$RESOLVED_TARGET"
        printf 'connected_before=%s\n' "$connected_before"
    } > "$TX/baseline.env" || exit 4
    write_candidate || exit 4
    : > "$TX/guardian.log" || exit 4

    setsid sh "$MODDIR/bin/legacy-66-txn.sh" guardian "$MODDIR" "$TX" "$HOLD_MS" "$mode" \
        >> "$TX/guardian-stderr.log" 2>&1 7>&- 8>&- 9>&- < /dev/null &
    gpid=$!
    n=0
    while [ "$n" -lt 40 ]; do
        if [ -f "$TX/ready.ok" ]; then
            if [ "$mode" = persistent ]; then
                printf '{"status":"owned","mode":"persistent","guardian_pid":%s}\n' "$gpid"
            else
                printf '{"status":"owned","guardian_pid":%s}\n' "$gpid"
            fi
            return 0
        fi
        [ ! -f "$TX/ready.err" ] || break
        kill -0 "$gpid" 2>/dev/null || break
        sleep 1
        n=$((n + 1))
    done
    printf '{"status":"handshake_failed","guardian_pid":%s,"guardian_may_be_active":true}\n' "$gpid"
    return 4
}

case "$OP" in
    inspect)
        printf '{"uid":%s,"boot":"%s","legacy_66":true}\n' "$(id -u)" "$(boot_id)"
        ;;
    inspect-wlan) cmd_inspect_wlan ;;
    inspect-module) cmd_inspect_module ;;
    run-wlan) cmd_run bounded ;;
    run-wlan-persistent) cmd_run persistent ;;
    guardian)
        run_guardian "${5:-bounded}"
        ;;
    cancel)
        valid_tx "$TX" || exit 64
        printf 'cancel\n' > "$TX/cancel" || exit 4
        printf '{"status":"cancel_requested"}\n'
        ;;
    recover)
        valid_tx "$TX" || exit 64
        exec 8>"$TX/guardlock" || exit 3
        flock -n 8 8>&8 || { printf '{"status":"busy_or_invalid_lock"}\n'; exit 3; }
        recover_tx
        rc=$?
        if [ "$rc" = 0 ]; then
            printf '{"status":"idle","restoration":"restored"}\n'
            exit 0
        fi
        [ "$rc" = 7 ] && printf '{"status":"network_not_ready"}\n' || printf '{"status":"blocked"}\n'
        exit "$rc"
        ;;
    *) exit 64 ;;
esac
