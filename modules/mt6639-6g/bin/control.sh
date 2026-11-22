#!/system/bin/sh
# Serialize module UI/service operations; native ownership checks remain final.
set -u
MODDIR=${1:-/data/adb/modules/mt6639-6g}
. "$MODDIR/bin/lib.sh"
operation=${2:-status}
case "$operation" in
    status) exec sh "$MODDIR/bin/native.sh" "$MODDIR" status ;;
    get-country)
        tc=$(read_target_country) || { echo 'REJECT invalid_target_country' >&2; exit 3; }
        echo "TARGET_COUNTRY=$tc"
        exit 0
        ;;
    enable|disable|toggle|refresh|set-country) ;;
    *) echo 'usage: control.sh MODDIR status|enable|disable|toggle|refresh|get-country|set-country [CODE]' >&2; exit 64 ;;
esac
[ "$(id -u)" = 0 ] || { log 'REJECT control_not_root'; exit 3; }
umask 077
exec 9>"$WORK/control.lock" || exit 3
# Android mksh marks exec-opened descriptors close-on-exec. Explicitly pass it
# to flock; the parent retains the locked open file description afterward.
flock -n 9 9>&9 || { log 'REJECT control_busy'; exit 75; }

if [ "$operation" = set-country ]; then
    new_tc=${3:-}
    valid_target_country "$new_tc" || {
        log "REJECT invalid_target_country value=$new_tc"
        exit 64
    }
    tc_tmp=$(mktemp "$WORK/.country.conf.XXXXXX") || exit 74
    chmod 0600 "$tc_tmp" || { rm -f "$tc_tmp"; exit 74; }
    printf 'TARGET_COUNTRY=%s\n' "$new_tc" >"$tc_tmp" || { rm -f "$tc_tmp"; exit 74; }
    mv -f "$tc_tmp" "$COUNTRY_CONF" || { rm -f "$tc_tmp"; exit 74; }
    log "control: set TARGET_COUNTRY=$new_tc"
    echo "TARGET_COUNTRY=$new_tc saved=$COUNTRY_CONF"
    exit 0
fi

if [ "$operation" = toggle ]; then
    # Any nonempty or unusual authority goes through restore validation.
    if [ -s "$ACTIVE_TX_FILE" ] || [ -L "$ACTIVE_TX_FILE" ] || [ -d "$ACTIVE_TX_FILE" ]; then
        operation=disable
    else
        operation=enable
    fi
fi
case "$operation" in
    refresh)
        timeout 90 sh "$MODDIR/bin/refresh-6g.sh" "$MODDIR" 9>&-
        ;;
    enable)
        if [ -e "$MODDIR/disable" ] || [ -e "$MODDIR/remove" ]; then
            log 'REJECT module_disabled_or_removing'; exit 3
        fi
        echo '启用 6GHz 事务（默认持续模式）；操作按钮可恢复。'
        # The detached guardian must not retain the management lock fd.
        ENABLE_6G=1 timeout 180 sh "$MODDIR/bin/native.sh" "$MODDIR" apply 9>&-
        ;;
    disable)
        echo '恢复当前事务；下次 KernelSU service 仍按 config.conf 的默认值执行。'
        timeout 180 sh "$MODDIR/bin/native.sh" "$MODDIR" restore 9>&-
        ;;
esac
rc=$?
[ "$rc" = 0 ] || log "control: operation=$operation failed rc=$rc; recovery authority retained"
# A failed observation must never replace the operation's actual exit code.
sh "$MODDIR/bin/status-card.sh" "$MODDIR" update "$operation" "$rc" 9>&- >/dev/null 2>&1 || :
exit "$rc"
