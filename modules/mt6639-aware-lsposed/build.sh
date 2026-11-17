#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=${1:-"$ROOT/build"}
ANDROID_JAR=${ANDROID_JAR:-}
if [ -z "$ANDROID_JAR" ]; then
    sdk_root=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
    if [ -n "$sdk_root" ] && [ -d "$sdk_root/platforms" ]; then
        ANDROID_JAR=$(ls -1 "$sdk_root"/platforms/android-*/android.jar 2>/dev/null | sort -V | tail -1 || true)
    fi
fi
[ -n "$ANDROID_JAR" ] && [ -f "$ANDROID_JAR" ] || {
    echo "ANDROID_JAR must point at platforms/android-*/android.jar (or set ANDROID_HOME)" >&2
    exit 64
}
STUBS="$ROOT/../mt6639-ui-lsposed/build-support/xposed-api/src"
# Put javac/d8/aapt2/zipalign/apksigner on PATH yourself.

[ ! -e "$OUT/module.apk" ] || { echo 'output already contains module.apk' >&2; exit 73; }
mkdir -p "$OUT/classes" "$OUT/xposed-api"

javac -encoding UTF-8 -source 8 -target 8 -nowarn \
    -d "$OUT/xposed-api" $(find "$STUBS" -name '*.java' -print)
javac -encoding UTF-8 -source 8 -target 8 -nowarn \
    -cp "$ANDROID_JAR:$OUT/xposed-api" -d "$OUT/classes" \
    $(find "$ROOT/src" -name '*.java' -print)
jar cf "$OUT/classes.jar" -C "$OUT/classes" .
d8 --min-api 31 --lib "$ANDROID_JAR" --output "$OUT" "$OUT/classes.jar"

aapt2 link -o "$OUT/base.apk" -I "$ANDROID_JAR" \
    --manifest "$ROOT/AndroidManifest.xml" \
    --min-sdk-version 31 --target-sdk-version 37 -A "$ROOT/assets"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && aapt add unsigned.apk classes.dex >/dev/null)
zipalign -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

if [ ! -f "$OUT/test.keystore" ]; then
    keytool -genkeypair -keystore "$OUT/test.keystore" -alias mod \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=mt6639-aware, O=local" >/dev/null
fi
apksigner sign --ks "$OUT/test.keystore" --ks-pass pass:android --key-pass pass:android \
    --v2-signing-enabled true --v3-signing-enabled true \
    --out "$OUT/module.apk" "$OUT/aligned.apk"
apksigner verify --print-certs "$OUT/module.apk"
printf 'APK=%s\n' "$OUT/module.apk"
shasum -a 256 "$OUT/module.apk"
