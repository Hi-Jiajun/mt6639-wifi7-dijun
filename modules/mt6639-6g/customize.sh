#!/system/bin/sh
# KernelSU/Magisk module installer hook. ZIP extraction commonly normalizes
# executable bits to 0644; restore 0755 on module hooks and tools.
MODPATH=${MODPATH:-${0%/*}}
for f in action.sh service.sh uninstall.sh \
    bin/apply.sh bin/check.sh bin/compat.sh bin/control.sh \
    bin/legacy-66-txn.sh bin/lib.sh bin/mount-txn bin/native.sh \
    bin/post-reboot-check.sh bin/refresh-6g.sh bin/restore.sh \
    bin/softreboot-watch.sh bin/status-card.sh bin/verify-module.sh; do
    [ -f "$MODPATH/$f" ] && chmod 0755 "$MODPATH/$f"
done
true
