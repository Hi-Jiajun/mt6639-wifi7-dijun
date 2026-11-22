#!/system/bin/sh
# KernelSU/Magisk module installer hook. ZIP extraction commonly normalizes
# executable bits to 0644, which prevents action/service scripts from executing directly.
MODPATH=${MODPATH:-${0%/*}}
for f in action.sh service.sh uninstall.sh bin/control.sh; do
    [ -f "$MODPATH/$f" ] && chmod 0755 "$MODPATH/$f"
done
true
