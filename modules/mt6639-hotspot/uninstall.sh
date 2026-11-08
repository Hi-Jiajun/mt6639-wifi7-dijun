#!/system/bin/sh
MODDIR=${0%/*}
[ -d "$MODDIR" ] || MODDIR=/data/adb/modules/mt6639-hotspot
sh "$MODDIR/bin/request_worker.sh" "$MODDIR" stop >/dev/null 2>&1 || true
sh "$MODDIR/bin/control.sh" "$MODDIR" stop || true
echo "mt6639-hotspot: stop attempted; preserved /data/adb/mt6639-hotspot for review"
