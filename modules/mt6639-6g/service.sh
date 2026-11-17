#!/system/bin/sh
# KernelSU 装载入口：默认持续启用，显式 0 时保持关闭。
# 旧事务先由原生包装器严格核对；跨 boot 只归档已验证回到原厂的记录。
MODDIR=${0%/*}
[ -d "$MODDIR" ] || MODDIR=/data/adb/modules/mt6639-6g

. "$MODDIR/bin/lib.sh"
[ -f "$MODDIR/config.conf" ] && . "$MODDIR/config.conf"
sh "$MODDIR/bin/status-card.sh" "$MODDIR" update >/dev/null 2>&1 || :

if [ "${ENABLE_6G:-0}" != 1 ]; then
    log "service: boot=$(boot_id | cut -c1-8) ENABLE_6G=0, nothing to do"
    exit 0
fi

log "service: boot=$(boot_id | cut -c1-8) ENABLE_6G=1"

# A soft reboot restarts system_server but keeps this kernel boot, the
# transaction, its guardian and the cfg bind. The Wi-Fi country override is
# system_server memory, so 6GHz falls back to disabled without any state change
# to notice. Start the bounded watchdog before the (possibly blocking) enable.
sh "$MODDIR/bin/softreboot-watch.sh" "$MODDIR" start >/dev/null 2>&1 ||
    log 'service: softreboot watchdog unavailable; manual refresh still required after a soft reboot'

attempt=0
while [ "$attempt" -lt 6 ]; do
    attempt=$((attempt+1))
    if ready=$(timeout 15 sh "$MODDIR/bin/native.sh" "$MODDIR" ready 2>&1); then
        case "$ready" in
            *'"wifi":1,'*) exec sh "$MODDIR/bin/control.sh" "$MODDIR" enable ;;
            *'"wifi":0,'*) log 'service: Wi-Fi is off; leave it off'; exit 0 ;;
        esac
    fi
    [ "$attempt" = 6 ] || sleep 5
done
log 'service: Wi-Fi service unavailable after bounded wait; no transaction started'
exit 69
