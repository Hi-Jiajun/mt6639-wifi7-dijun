#!/system/bin/sh
# Keep recovery records outside the module directory, including on failure.
MODDIR=${0%/*}
exec sh "$MODDIR/bin/control.sh" "$MODDIR" disable
