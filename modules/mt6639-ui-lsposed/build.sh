#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=${1:-"$ROOT/build"}
ANDROID_JAR=${ANDROID_JAR:-}
[ -n "$ANDROID_JAR" ] || {
    echo "ANDROID_JAR must point at platforms/android-*/android.jar" >&2; exit 64; }
[ -f "$ANDROID_JAR" ] || {
    echo 'Android API jar missing; set ANDROID_JAR to the local android.jar' >&2
    exit 66
}

mkdir -p "$OUT/classes" "$OUT/xposed-api"

rm -f \
    "$OUT/classes.jar" \
    "$OUT/base.apk" \
    "$OUT/unsigned.apk" \
    "$OUT/aligned.apk" \
    "$OUT/module.apk" \
    "$OUT/classes.dex"

javac \
    -encoding UTF-8 \
    -source 8 \
    -target 8 \
    -nowarn \
    -d "$OUT/xposed-api" \
    $(find "$ROOT/build-support/xposed-api/src" -name '*.java' -print)

javac \
    -encoding UTF-8 \
    -source 8 \
    -target 8 \
    -nowarn \
    -cp "$ANDROID_JAR:$OUT/xposed-api" \
    -d "$OUT/classes" \
    $(find "$ROOT/src" -name '*.java' -print)

jar cf "$OUT/classes.jar" -C "$OUT/classes" .
d8 --min-api 31 --lib "$ANDROID_JAR" --output "$OUT" "$OUT/classes.jar"

aapt2 link \
    -o "$OUT/base.apk" \
    -I "$ANDROID_JAR" \
    --manifest "$ROOT/AndroidManifest.xml" \
    --min-sdk-version 31 \
    --target-sdk-version 37 \
    -A "$ROOT/assets"

cp "$OUT/base.apk" "$OUT/unsigned.apk"
cp "$OUT/classes.dex" "$OUT/classes.dex.tmp"
mv "$OUT/classes.dex.tmp" "$OUT/classes.dex"
(cd "$OUT" && aapt add unsigned.apk classes.dex >/dev/null)
zipalign -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

if [ ! -f "$OUT/test.keystore" ]; then
    keytool -genkeypair \
        -keystore "$OUT/test.keystore" \
        -alias mod \
        -keyalg RSA \
        -keysize 2048 \
        -validity 10000 \
        -storepass android \
        -keypass android \
        -dname "CN=mt6639-ui, O=local" >/dev/null
fi

apksigner sign \
    --ks "$OUT/test.keystore" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --v2-signing-enabled true \
    --v3-signing-enabled true \
    --out "$OUT/module.apk" \
    "$OUT/aligned.apk"

apksigner verify --print-certs "$OUT/module.apk"
printf 'APK=%s\n' "$OUT/module.apk"
shasum -a 256 "$OUT/module.apk"
