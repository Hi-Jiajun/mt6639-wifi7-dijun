#!/system/bin/sh
# KernelSU Manager action button: start when idle, restore when recorded.
MODDIR=${0%/*}
exec sh "$MODDIR/bin/control.sh" "$MODDIR" toggle
