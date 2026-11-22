#!/system/bin/sh
MODDIR=${0%/*}
[ -d "$MODDIR" ] || MODDIR=/data/adb/modules/mt6639-hotspot
. "$MODDIR/config.conf"
sh "$MODDIR/bin/control.sh" "$MODDIR" reconcile >/dev/null 2>&1 || true
# Publish a current-boot read-only snapshot even when automatic start is off.
sh "$MODDIR/bin/status_snapshot.sh" "$MODDIR" init >/dev/null 2>&1 || true
if [ "${ENABLE_SETTINGS_BRIDGE:-1}" = 1 ] &&
    [ ! -e "$MODDIR/disable" ] && [ ! -e "$MODDIR/remove" ]; then
    sh "$MODDIR/bin/request_worker.sh" "$MODDIR" start >/dev/null 2>&1 ||
        echo "mt6639-hotspot: settings bridge failed to start" >&2
fi
if [ "${ENABLE_AT_BOOT:-0}" != 1 ]; then
    echo "mt6639-hotspot: ENABLE_AT_BOOT=0; manual action required"
    exit 0
fi
exec sh "$MODDIR/bin/control.sh" "$MODDIR" start
