#!/system/bin/sh
# Exercise cross-boot wrapper decisions with Android tools and fixture-only I/O.
set -eu
MODDIR=${1:?module scripts directory}
WORK=$(mktemp -d /data/local/tmp/mt6639-reconcile-XXXXXX)
chmod 0700 "$WORK"
export MT6639_TEST_MODE=1 WORK
export CFG=$WORK/cfg STOCK_KO=$WORK/ko BOOT_ID_FILE=$WORK/boot
export MOUNTINFO_FILE=$WORK/mountinfo INIT_MOUNTINFO_FILE=$WORK/init-mountinfo INIT_CFG_PATH=$WORK/cfg
export NATIVE=$WORK/fake-native
printf 'CountryCode=CN\nDisallowBand6G 1\n' >"$CFG"
printf 'stock ko\n' >"$STOCK_KO"
: >"$MOUNTINFO_FILE"
: >"$INIT_MOUNTINFO_FILE"
export STOCK_CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
export STOCK_KO_SHA=$(sha256sum "$STOCK_KO" | cut -d' ' -f1)
oldboot=11111111-1111-4111-8111-111111111111
newboot=22222222-2222-4222-8222-222222222222
tx=$WORK/transactions/t-old-fixture
mkdir -p "$tx"
chmod 0700 "$WORK/transactions" "$tx"
printf '%s\n' "$oldboot" >"$tx/wrapper-boot"
printf '%s\n' "$newboot" >"$BOOT_ID_FILE"
printf '%s\n' "$tx" >"$WORK/active_tx"
echo 999999 >"$WORK/active_pid"
cat >"$NATIVE" <<'NATIVE'
#!/system/bin/sh
case "$1" in
inspect-wlan) echo '{"scan":1,"wifi":1,"read_only":true}';;
inspect-module) echo '{"status":"module_control_available","read_only":true}';;
run-wlan-persistent)
  echo '{"event":"terminal","restoration":"restored"}' >"$2/guardian.log"
  echo '{"status":"owned","guardian_pid":999999}';;
recover) echo ERROR-old-boot-recover-was-called; exit 9;;
*) exit 64;;
esac
NATIVE
chmod 0700 "$NATIVE"
sh "$MODDIR/bin/control.sh" "$MODDIR" enable >"$WORK/first-enable.log"
[ "$(cat "$WORK/active_tx")" != "$tx" ]
archived=$(find "$WORK/retired" -name active_tx -type f)
[ "$(cat "$archived")" = "$tx" ]
[ -d "$tx" ]
[ ! -e "$tx/restore-wrapper.log" ]
echo PASS android-crossboot-retires-stock-authority-without-native-recover

rejects_and_preserves() {
    rc=0
    sh "$MODDIR/bin/control.sh" "$MODDIR" "$1" >"$WORK/rejection.log" 2>&1 || rc=$?
    [ "$rc" != 0 ]
    [ "$(cat "$WORK/active_tx")" = "$tx" ]
    grep -q "$3" "$WORK/rejection.log"
    echo "PASS $2"
}
printf '%s\n' "$tx" >"$WORK/active_tx"
rejects_and_preserves disable android-explicit-crossboot-restore-rejects missing_or_cross_boot_authority
printf 'not-stock\n' >"$WORK/init-candidate"
INIT_CFG_PATH=$WORK/init-candidate rejects_and_preserves enable android-init-nonstock-blocks-retirement init_cfg_not_stock
printf '1 2 0:1 / %s ro - ext4 x ro\n' "$CFG" >"$INIT_MOUNTINFO_FILE"
rejects_and_preserves enable android-init-target-mount-blocks-retirement cfg_already_mounted
: >"$INIT_MOUNTINFO_FILE"
INIT_MOUNTINFO_FILE=$WORK/missing rejects_and_preserves enable android-mountinfo-read-failure-blocks-retirement mountinfo_unreadable
printf 'bad-boot\n' >"$tx/wrapper-boot"
rejects_and_preserves enable android-invalid-boot-blocks-retirement invalid_cross_boot_record
echo "RESULT pass=6 fail=0 fixture=$WORK"
