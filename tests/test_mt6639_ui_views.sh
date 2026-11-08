#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT/outputs/test-mt6639-ui-views"
AJ=${ANDROID_JAR:-}
[ -n "$AJ" ] || {
    echo "ANDROID_JAR must point at platforms/android-*/android.jar" >&2; exit 64; }
# Put aapt2/zipalign/apksigner/adb on PATH yourself.
export PATH
mkdir -p "$OUT/classes"
javac -encoding UTF-8 -source 8 -target 8 -nowarn -cp "$AJ" -d "$OUT/classes" \
    "$ROOT/modules/mt6639-ui-lsposed/src/com/hiliang/mt6639ui/RowState.java" \
    "$ROOT/modules/mt6639-ui-lsposed/src/com/hiliang/mt6639ui/UiPolicy.java" \
    "$ROOT/tests/device/ui/RowStateInstrumentation.java"
jar cf "$OUT/classes.jar" -C "$OUT/classes" .
d8 --min-api 31 --lib "$AJ" --output "$OUT" "$OUT/classes.jar"
aapt2 link -o "$OUT/base.apk" -I "$AJ" --manifest "$ROOT/tests/device/ui/AndroidManifest.xml"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && aapt add unsigned.apk classes.dex >/dev/null)
zipalign -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
if [ ! -f "$OUT/test.keystore" ]; then
    keytool -genkeypair -keystore "$OUT/test.keystore" -alias test -keyalg RSA \
        -keysize 2048 -validity 10000 -storepass android -keypass android \
        -dname 'CN=MT6639 View Tests' >/dev/null 2>&1
fi
apksigner sign --ks "$OUT/test.keystore" --ks-pass pass:android --key-pass pass:android \
    --out "$OUT/tests.apk" "$OUT/aligned.apk"
apksigner verify "$OUT/tests.apk"
if [ "${1:-}" != "--device" ]; then
    printf 'RESULT=BUILT apk=%s\n' "$OUT/tests.apk"
    exit 0
fi
export ADB_LIBUSB=1
adb -P 5038 -s YOUR_SERIAL push "$OUT/tests.apk" /data/local/tmp/mt6639-ui-view-tests.apk
adb -P 5038 -s YOUR_SERIAL shell \
    "su -c 'pm install -r -t /data/local/tmp/mt6639-ui-view-tests.apk'"
adb -P 5038 -s YOUR_SERIAL shell timeout 30 am instrument -w \
    com.hiliang.mt6639uitest/com.hiliang.mt6639ui.RowStateInstrumentation > "$OUT/result.txt"
cat "$OUT/result.txt"
grep -q 'RESULT=PASS android_view_cases=' "$OUT/result.txt"
