#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODULE="$ROOT/modules/mt6639-ui-lsposed"
OUT=${MT6639_UI_BUILD_DIR:-"$ROOT/outputs/ui-hardening-20261023c/build"}
APK="$OUT/module.apk"

fail() {
    printf 'FAIL %s\n' "$1"
    exit 1
}

apk_entry() {
    /usr/bin/python3 - "$APK" "$1" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    sys.stdout.buffer.write(archive.read(sys.argv[2]))
PY
}

mkdir -p "$OUT"
(cd "$MODULE" && ./build.sh "$OUT" >"$OUT/build.log" 2>&1) || {
    cat "$OUT/build.log"
    fail build
}

[ -f "$APK" ] || fail apk_missing

entry=$(apk_entry assets/xposed_init | tr -d '\r\n')
[ "$entry" = "com.hiliang.mt6639ui.MainHook" ] || fail xposed_init

manifest=$(aapt2 dump xmltree "$APK" --file AndroidManifest.xml)
printf '%s\n' "$manifest" | grep -q 'xposedmodule' || fail xposedmodule
printf '%s\n' "$manifest" | grep -q 'xposeddescription' || fail xposeddescription
printf '%s\n' "$manifest" | grep -q 'xposedminversion' || fail xposedminversion
printf '%s\n' "$manifest" | grep -q 'com.hiliang.mt6639ui' || fail package
! printf '%s\n' "$manifest" | grep -q 'uses-permission' || fail unexpected_permissions

if command -v dexdump >/dev/null 2>&1; then
    apk_entry classes.dex > "$OUT/verify-classes.dex"
    classes=$(dexdump "$OUT/verify-classes.dex" | sed -n "s/.*Class descriptor *: '\([^']*\)'.*/\1/p")
elif command -v droidasc >/dev/null 2>&1; then
    classes=$(droidasc listclass "$APK")
else
    fail dex_class_inspector_missing
fi
printf '%s\n' "$classes" | grep -q 'Lcom/hiliang/mt6639ui/MainHook;' || fail main_hook
printf '%s\n' "$classes" | grep -q 'Lcom/hiliang/mt6639ui/BadgeRenderer;' || fail renderer
printf '%s\n' "$classes" | grep -q 'Lcom/hiliang/mt6639ui/HotspotStatus;' || fail hotspot_status
printf '%s\n' "$classes" | grep -q 'Lcom/hiliang/mt6639ui/HotspotRequestPayload;' || fail hotspot_request_payload
printf '%s\n' "$classes" | grep -q 'Lcom/hiliang/mt6639ui/HotspotRequestWriter;' || fail hotspot_request_writer
! printf '%s\n' "$classes" | grep -q 'Lde/robv/android/xposed/' || fail stub_leak

apksigner verify --verbose "$APK" | grep -q \
    'Verified using v3 scheme (APK Signature Scheme v3): true' \
    || fail signature_v3

javap -classpath "$OUT/xposed-api" -s \
    de.robv.android.xposed.XposedHelpers \
    | grep -q 'Lde/robv/android/xposed/XC_MethodHook$Unhook;' \
    || fail xposed_api_return_type

grep -q 'EXPECTED_FINGERPRINT' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail version_lock
grep -q 'wifi_signal_icon_connected_color' \
    "$MODULE/src/com/hiliang/mt6639ui/BadgeRenderer.java" || fail connected_color
grep -q "wght.*425" \
    "$MODULE/src/com/hiliang/mt6639ui/BadgeRenderer.java" || fail font_weight
grep -q 'MiuiTetherSettings' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail hotspot_hook
grep -q 'AllInOneTetherSettings' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail all_in_one_hotspot_hook
grep -q 'HotspotRequestWriter.submit' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail hotspot_request_action
grep -q 'context.getFilesDir' \
    "$MODULE/src/com/hiliang/mt6639ui/HotspotRequestWriter.java" || fail hotspot_request_private_dir
grep -q 'start-6g-' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail hotspot_width_actions
grep -q 'HOTSPOT_START5G_ACTIONS' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail hotspot_5g_actions
grep -q 'HOTSPOT_C02_KEY' \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" || fail hotspot_c02_action
sh "$ROOT/tests/test_mt6639_ui_bands.sh"
sh "$ROOT/tests/test_mt6639_ui_hotspot_status.sh"

sha256=$(shasum -a 256 "$APK" | awk '{print $1}')
printf 'RESULT=PASS apk_sha256=%s\n' "$sha256"
