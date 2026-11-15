#!/system/bin/sh
# Serialize module UI/service operations; native ownership checks remain final.
set -u
MODDIR=${1:-/data/adb/modules/mt6639-6g}
. "$MODDIR/bin/lib.sh"
operation=${2:-status}
case "$operation" in
    status) exec sh "$MODDIR/bin/native.sh" "$MODDIR" status ;;
    enable|disable|toggle|refresh) ;;
    *) echo 'usage: control.sh MODDIR status|enable|disable|toggle|refresh' >&2; exit 64 ;;
esac
[ "$(id -u)" = 0 ] || { log 'REJECT control_not_root'; exit 3; }
umask 077
exec 9>"$WORK/control.lock" || exit 3
# Android mksh marks exec-opened descriptors close-on-exec. Explicitly pass it
# to flock; the parent retains the locked open file description afterward.
flock -n 9 9>&9 || { log 'REJECT control_busy'; exit 75; }

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
