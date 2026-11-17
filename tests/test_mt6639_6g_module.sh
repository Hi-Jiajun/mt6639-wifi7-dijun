#!/bin/sh
# Host-side behaviour tests for modules/mt6639-6g.
# Uses stubs for wifi/settings/mount/chcon/runcon so nothing real is touched.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MOD=$ROOT/modules/mt6639-6g
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
pass=0
fail=0

ok() { pass=$((pass+1)); echo "PASS $1"; }
bad() { fail=$((fail+1)); echo "FAIL $1"; }

# --- build a fake environment -------------------------------------------
STUB=$TMP/stub
mkdir -p "$STUB" "$TMP/work" "$TMP/etc"
CFG=$TMP/etc/wifi.cfg
KO=$TMP/etc/wlan.ko
cat > "$CFG" <<'EOF'
CountryCode=CN
DisallowP2PAcs6G 1
DisallowBand6G 1
DisallowAcs6G 1
EOF
printf 'fake-ko\n' > "$KO"
CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
KO_SHA=$(sha256sum "$KO" | cut -d' ' -f1)

cat > "$STUB/wifi" <<'EOF'
#!/bin/sh
echo "wifi $*" >> "$MT6639_TEST_CALLS"
exit 0
EOF
cat > "$STUB/settings" <<'EOF'
#!/bin/sh
echo "settings $*" >> "$MT6639_TEST_CALLS"
if [ "$1" = get ]; then
    if [ "$3" = wifi_scan_always_enabled ]; then echo "${MT6639_TEST_SCAN_VALUE:-1}"; else echo "${MT6639_TEST_WIFI_VALUE:-1}"; fi
    exit "${MT6639_TEST_GET_RC:-0}"
fi
exit 0
EOF
cat > "$STUB/runcon" <<'EOF'
#!/bin/sh
shift
exec "$@"
EOF
cat > "$STUB/cmd" <<'EOF'
#!/bin/sh
[ "$1" = wifi ] || exit 3
shift
echo "wifi $*" >> "$MT6639_TEST_CALLS"
if [ "${MT6639_TEST_FAIL_WIFI:-}" = "$*" ]; then
    if [ -z "${MT6639_TEST_FAIL_ONCE:-}" ] || [ ! -e "$MT6639_TEST_FAIL_ONCE" ]; then
        [ -z "${MT6639_TEST_FAIL_ONCE:-}" ] || : > "$MT6639_TEST_FAIL_ONCE"
        exit 7
    fi
fi
exit 0
EOF
cat > "$STUB/sleep" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$STUB/mount" <<'EOF'
#!/bin/sh
echo "mount $*" >> "$MT6639_TEST_CALLS"
# mount --bind SRC TGT -> simulate by copying content so hashes match
cp "$2" "$3" || exit 1
echo "999 1 0:1 $3 $3 rw - x y rw" > "$MOUNTINFO_FILE"
exit 0
EOF
cat > "$STUB/umount" <<'EOF'
#!/bin/sh
echo "umount $*" >> "$MT6639_TEST_CALLS"
# simulate unmount: restore the stock content that the test saved
[ -f "$MT6639_TEST_STOCK" ] && cp "$MT6639_TEST_STOCK" "$1" || exit 1
: > "$MOUNTINFO_FILE"
exit 0
EOF
cat > "$STUB/timeout" <<'EOF'
#!/bin/sh
shift
exec "$@"
EOF
cat > "$STUB/chcon" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$STUB/id" <<'EOF'
#!/bin/sh
[ "$1" = -u ] && { echo 0; exit 0; }
exec /usr/bin/id "$@"
EOF
cat > "$STUB/uname" <<'EOF'
#!/bin/sh
echo "6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k"
EOF
chmod +x "$STUB"/*
export PATH="$STUB:$PATH"
export MT6639_TEST_CALLS=$TMP/calls.log
export MT6639_TEST_STOCK=$TMP/stock.cfg
: > "$MT6639_TEST_CALLS"

run_case() { # name expected_rc script...
    name=$1; want=$2; shift 2
    if "$@" >"$TMP/out" 2>&1; then rc=0; else rc=$?; fi
    if [ "$rc" = "$want" ]; then ok "$name (rc=$rc)"; else
        bad "$name (rc=$rc want=$want)"; sed 's/^/    /' "$TMP/out"; fi
}

common_env() {
    # every case starts from a pristine stock cfg/ko
    cat > "$CFG" <<'EOF'
CountryCode=CN
DisallowP2PAcs6G 1
DisallowBand6G 1
DisallowAcs6G 1
EOF
    printf 'fake-ko\n' > "$KO"
    CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
    KO_SHA=$(sha256sum "$KO" | cut -d' ' -f1)
    export MT6639_TEST_MODE=1
    export CFG KO STOCK_KO=$KO STOCK_CFG_SHA=$CFG_SHA STOCK_KO_SHA=$KO_SHA
    export WORK=$TMP/work MODDIR=$MOD
    export NATIVE_EXECUTOR=0
    # host has no /proc; point the three read-only paths at fixtures
    printf 'testboot-0001\n' > "$TMP/boot_id"
    : > "$TMP/mountinfo"
    export BOOT_ID_FILE=$TMP/boot_id MOUNTINFO_FILE=$TMP/mountinfo INIT_CFG_PATH=$CFG
}

# --- 1. explicit off does nothing --------------------------------------
common_env
cp "$MOD/config.conf" "$TMP/config.orig"
run_case "explicit-off apply exits 0" 0 env ENABLE_6G=0 sh "$MOD/bin/apply.sh" "$MOD"
grep -q 'apply skipped' "$TMP/work/apply.log" && ok "explicit-off logs skip" || bad "explicit-off logs skip"
grep -q '^mount ' "$MT6639_TEST_CALLS" && bad "explicit-off must not mount" || ok "explicit-off no mount"

# --- 2. identity lock rejects bad cfg ----------------------------------
common_env
printf 'tampered\n' > "$CFG"
run_case "reject non-stock cfg" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q 'cfg_not_stock' "$TMP/work/apply.log" && ok "reject logs cfg_not_stock" || bad "reject logs cfg_not_stock"

# --- 3. identity lock rejects bad ko -----------------------------------
common_env
cat > "$CFG" <<'EOF'
CountryCode=CN
DisallowP2PAcs6G 1
DisallowBand6G 1
DisallowAcs6G 1
EOF
printf 'different-ko\n' > "$KO"
run_case "reject non-stock ko" 3 env ENABLE_6G=1 STOCK_KO_SHA=$KO_SHA sh "$MOD/bin/apply.sh" "$MOD"
grep -q 'ko_not_stock' "$TMP/work/apply.log" && ok "reject logs ko_not_stock" || bad "reject logs ko_not_stock"

# --- 4. happy path produces exactly one-line diff ---------------------
common_env
printf 'fake-ko\n' > "$KO"
: > "$MT6639_TEST_CALLS"
cat > "$TMP/work/nl80211-wiphy-read" <<'EOF'
#!/bin/sh
echo 'frequency band_enum=3 mhz=6135 disabled=1 no_ir=0 max_tx_mbm=0 power_present=1'
echo 'summary band_enum=3 frequencies=1 disabled=1 no_ir=0'
echo 'dump_complete messages=1'
EOF
chmod +x "$TMP/work/nl80211-wiphy-read"
run_case "apply with single-line change" 0 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q '6g_lines=1' "$TMP/out" && ok "reports registered frequency even when disabled" || bad "reports registered frequency even when disabled"
grep -q '^mount ' "$MT6639_TEST_CALLS" && ok "apply mounts" || bad "apply mounts"
if grep -q '^DisallowBand6G 0$' "$CFG"; then ok "candidate flipped DisallowBand6G"; else bad "candidate flipped"; fi
[ "$(grep -c '^DisallowBand6G ' "$CFG")" = 1 ] && ok "only one DisallowBand6G line" || bad "only one line"
[ "$(grep -c '^DisallowAcs6G 1$' "$CFG")" = 1 ] && ok "Acs6G untouched" || bad "Acs6G untouched"
[ "$(grep -c '^DisallowP2PAcs6G 1$' "$CFG")" = 1 ] && ok "P2PAcs6G untouched" || bad "P2PAcs6G untouched"

# --- 5. multi-line cfg is refused -------------------------------------
common_env
cat > "$CFG" <<'EOF'
CountryCode=CN
DisallowBand6G 1
DisallowBand6G 1
EOF
export STOCK_CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
run_case "reject multi-line candidate" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q 'candidate_check' "$TMP/work/apply.log" && ok "reject logs candidate_check" || bad "reject logs candidate_check"

# --- 6. check.sh reports facts ----------------------------------------
common_env
run_case "check.sh runs" 0 sh "$MOD/bin/check.sh" "$MOD"
grep -q '^boot=' "$TMP/out" && ok "check reports boot" || bad "check reports boot"
grep -q '^cfg_stock=' "$TMP/out" && ok "check reports cfg_stock" || bad "check reports cfg_stock"

# --- 7. restore is idempotent -----------------------------------------
common_env
: > "$MT6639_TEST_CALLS"
# cfg already holds the stock content in this fixture
run_case "restore with nothing mounted" 0 sh "$MOD/bin/restore.sh" "$MOD"
if awk '
 /^wifi set-scan-always-available disabled$/ { a=NR }
 /^wifi set-wifi-enabled disabled$/ { b=NR }
 /^wifi set-wifi-enabled enabled$/ { c=NR }
 /^settings put global wifi_scan_always_enabled 1$/ { d=NR }
 END { exit !(a && a<b && b<c && c<d) }
' "$MT6639_TEST_CALLS"; then
    ok "restore disables scanning until Wi-Fi has restarted"
else
    bad "restore disables scanning until Wi-Fi has restarted"
fi
grep -q 'not mounted' "$TMP/work/apply.log" && ok "restore logs not mounted" || bad "restore logs not mounted"
grep -q '^umount ' "$MT6639_TEST_CALLS" && bad "restore must not umount when clean" || ok "restore no umount when clean"

# --- 8. restore undoes an active mount ---------------------------------
common_env
printf 'fake-ko\n' > "$KO"
cat > "$CFG" <<'EOF'
CountryCode=CN
DisallowP2PAcs6G 1
DisallowBand6G 1
DisallowAcs6G 1
EOF
export STOCK_CFG_SHA=$(sha256sum "$CFG" | cut -d' ' -f1)
env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD" >/dev/null 2>&1
# keep the stock copy for the umount stub to restore
cp "$TMP/work/wifi.cfg.stock" "$MT6639_TEST_STOCK"
# simulate that the cfg is currently the experimental content
cp "$TMP/work/wifi-6g.cfg" "$CFG"
# fake mountinfo showing the bind, so restore takes the unmount branch
echo "999 1 0:1 $CFG $CFG rw - x y rw" > "$TMP/mountinfo"
run_case "restore after active apply" 0 sh "$MOD/bin/restore.sh" "$MOD"
grep -q '^umount ' "$MT6639_TEST_CALLS" && ok "restore unmounts" || bad "restore unmounts"

# --- 9. command failures must not produce success states ---------------
common_env
export MT6639_TEST_FAIL_WIFI='set-wifi-enabled enabled'
run_case "apply command failure and failed rollback return 90" 90 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
[ "$(cat "$WORK/state")" != active ] && ok "failed apply not active" || bad "failed apply not active"
unset MT6639_TEST_FAIL_WIFI

common_env
cp "$CFG" "$MT6639_TEST_STOCK"
export MT6639_TEST_FAIL_WIFI='set-wifi-enabled enabled'
export MT6639_TEST_FAIL_ONCE=$TMP/failed-once
run_case "transient apply failure restores and preserves error" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
[ "$(cat "$WORK/state")" = restored ] && ok "transient failure rollback completes" || bad "transient failure rollback completes"
[ "$(sha256sum "$CFG" | cut -d' ' -f1)" = "$CFG_SHA" ] && [ ! -s "$MOUNTINFO_FILE" ] && ok "rollback restores file and removes fixture mount" || bad "rollback restores file and removes fixture mount"
unset MT6639_TEST_FAIL_WIFI MT6639_TEST_FAIL_ONCE

common_env
export MT6639_TEST_FAIL_WIFI='set-scan-always-available disabled'
run_case "restore command failure returns 90" 90 sh "$MOD/bin/restore.sh" "$MOD"
[ "$(cat "$WORK/state")" = restore_failed ] && ok "failed restore retains failed state" || bad "failed restore retains failed state"
unset MT6639_TEST_FAIL_WIFI

# --- 10. original scan=0 is restored, never forced to 1 ----------------
common_env
export MT6639_TEST_SCAN_VALUE=0
run_case "apply accepts initial scan disabled" 0 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
cp "$WORK/wifi.cfg.stock" "$MT6639_TEST_STOCK"
echo "999 1 0:1 $CFG $CFG rw - x y rw" > "$TMP/mountinfo"
: > "$MT6639_TEST_CALLS"
run_case "restore initial scan disabled" 0 sh "$MOD/bin/restore.sh" "$MOD"
grep -q '^settings put global wifi_scan_always_enabled 0$' "$MT6639_TEST_CALLS" && ok "restores scan 0" || bad "restores scan 0"
grep -q '^settings put global wifi_scan_always_enabled 1$' "$MT6639_TEST_CALLS" && bad "must not force scan 1" || ok "does not force scan 1"
unset MT6639_TEST_SCAN_VALUE

# --- 11. successful command is insufficient if readback differs --------
common_env
printf '1\n' > "$WORK/scan_before"
export MT6639_TEST_SCAN_VALUE=0
run_case "restore rejects scan readback mismatch" 90 sh "$MOD/bin/restore.sh" "$MOD"
unset MT6639_TEST_SCAN_VALUE

# --- 12. refusal paths have no radio or mount side effects -------------
common_env
export MT6639_TEST_WIFI_VALUE=0
run_case "restore rejects Wi-Fi readback mismatch" 90 sh "$MOD/bin/restore.sh" "$MOD"
unset MT6639_TEST_WIFI_VALUE

common_env
printf 'different-boot\n' > "$WORK/boot_id"
: > "$MT6639_TEST_CALLS"
run_case "restore refuses cross boot" 90 sh "$MOD/bin/restore.sh" "$MOD"
[ ! -s "$MT6639_TEST_CALLS" ] && ok "cross-boot refusal does not change resources" || bad "cross-boot refusal does not change resources"

common_env
export MT6639_TEST_SCAN_VALUE=null
: > "$MT6639_TEST_CALLS"
run_case "apply refuses unknown scan baseline" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q '^mount ' "$MT6639_TEST_CALLS" && bad "unknown baseline must not mount" || ok "unknown baseline does not mount"
unset MT6639_TEST_SCAN_VALUE

common_env
export MT6639_TEST_WIFI_VALUE=0
: > "$MT6639_TEST_CALLS"
run_case "apply refuses initial Wi-Fi off" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q '^mount ' "$MT6639_TEST_CALLS" && bad "initial Wi-Fi off must not mount" || ok "initial Wi-Fi off does not mount"
unset MT6639_TEST_WIFI_VALUE

common_env
IDLE=$TMP/idle
mkdir "$IDLE"
: > "$MT6639_TEST_CALLS"
run_case "idle restore is a true no-op" 0 env WORK="$IDLE" sh "$MOD/bin/restore.sh" "$MOD"
[ ! -s "$MT6639_TEST_CALLS" ] && ok "idle restore does not change resources" || bad "idle restore does not change resources"

# --- 13. valid-looking output does not excuse a failed read ------------
common_env
export MT6639_TEST_GET_RC=7
: > "$MT6639_TEST_CALLS"
run_case "apply rejects valid text from failed baseline read" 3 env ENABLE_6G=1 sh "$MOD/bin/apply.sh" "$MOD"
grep -q '^mount ' "$MT6639_TEST_CALLS" && bad "failed baseline read must not mount" || ok "failed baseline read does not mount"
printf '1\n' > "$WORK/scan_before"
run_case "restore rejects valid text from failed readback" 90 sh "$MOD/bin/restore.sh" "$MOD"
[ "$(cat "$WORK/state")" = restore_failed ] && ok "failed readback not restored" || bad "failed readback not restored"
unset MT6639_TEST_GET_RC

common_env
export MOUNTINFO_FILE=$TMP/missing-mountinfo
: > "$MT6639_TEST_CALLS"
run_case "idle restore rejects unreadable mountinfo" 90 sh "$MOD/bin/restore.sh" "$MOD"
[ ! -s "$MT6639_TEST_CALLS" ] && ok "unreadable mountinfo causes no mutations" || bad "unreadable mountinfo causes no mutations"

echo
echo "RESULT pass=$pass fail=$fail"
[ "$fail" = 0 ]
