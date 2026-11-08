#!/system/bin/sh
MODDIR=${0%/*}
value=$(sed -n 's/^ENABLE_LE_AUDIO=\([01]\)$/\1/p' "$MODDIR/config.conf")
case "$value" in
    1) exec sh "$MODDIR/bin/control.sh" enable ;;
    0) exec sh "$MODDIR/bin/control.sh" restore ;;
    *) echo 'REJECT invalid_ENABLE_LE_AUDIO' >&2; exit 64 ;;
esac
