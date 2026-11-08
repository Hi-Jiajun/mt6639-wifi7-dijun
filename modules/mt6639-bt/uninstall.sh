#!/system/bin/sh
MODDIR=${0%/*}
exec sh "$MODDIR/bin/control.sh" restore
