#!/system/bin/sh
# Android /proc contract test. Reads module scripts; all state is fixture-owned.
set -eu
MODDIR=${1:?module scripts directory}
NATIVE=${2:?test-only sleeper binary}
case "$NATIVE" in /data/local/tmp/mt6639-*/mt6639-guardian_identity_fixture) ;; *) exit 64 ;; esac
WORK=$(mktemp -d /data/local/tmp/mt6639-guardian-XXXXXX)
pid=
cleanup() { if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi; }
trap cleanup EXIT INT TERM
chmod 0700 "$WORK"
mkdir -p "$WORK/transactions/t-own" "$WORK/transactions/t-other"
tx=$WORK/transactions/t-own
other=$WORK/transactions/t-other
export MT6639_TEST_MODE=1 WORK NATIVE
export CFG=$WORK/cfg STOCK_KO=$WORK/ko BOOT_ID_FILE=$WORK/boot
export MOUNTINFO_FILE=$WORK/mountinfo INIT_MOUNTINFO_FILE=$WORK/mountinfo INIT_CFG_PATH=$WORK/cfg
printf 'CountryCode=CN\nDisallowBand6G 1\n' >"$CFG"
printf 'stock ko\n' >"$STOCK_KO"
cat /proc/sys/kernel/random/boot_id >"$BOOT_ID_FILE"
cp "$BOOT_ID_FILE" "$tx/wrapper-boot"
cp "$BOOT_ID_FILE" "$other/wrapper-boot"
: >"$MOUNTINFO_FILE"
export STOCK_CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
export STOCK_KO_SHA=$(sha256sum "$STOCK_KO" | cut -d' ' -f1)
printf '%s\n' "$tx" >"$WORK/active_tx"
echo active >"$WORK/state"
"$NATIVE" run-wlan-persistent "$tx" &
pid=$!
printf '%s\n' "$pid" >"$WORK/active_pid"
# Wait for the child's exec, not for arbitrary wall time.
attempt=0
while [ "$(readlink /proc/$pid/exe 2>/dev/null)" != "$NATIVE" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ] || exit 1
    sleep 0.05
done
sh "$MODDIR/bin/native.sh" "$MODDIR" status >"$WORK/status-own"
grep -qx guardian=alive "$WORK/status-own"
echo PASS same-binary-same-transaction-recognized
sh "$MODDIR/bin/control.sh" "$MODDIR" enable >"$WORK/enable"
grep -q 'native already active' "$WORK/enable"
[ "$(cat "$WORK/active_tx")" = "$tx" ]
[ "$(cat "$WORK/active_pid")" = "$pid" ]
echo PASS live-guardian-enable-is-idempotent
printf '%s\n' "$other" >"$WORK/active_tx"
sh "$MODDIR/bin/native.sh" "$MODDIR" status >"$WORK/status-other"
grep -qx guardian=not_running "$WORK/status-other"
kill -0 "$pid"
echo PASS same-binary-different-transaction-not-recognized
echo "RESULT pass=3 fail=0 fixture=$WORK"
