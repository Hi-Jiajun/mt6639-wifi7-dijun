#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
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
OUT="$ROOT/outputs/test-dualsta-gate"
mkdir -p "$OUT"
javac -encoding UTF-8 -source 8 -target 8 -nowarn -cp "$ANDROID_JAR" -d "$OUT" \
    "$ROOT/modules/mt6639-concurrency-lsposed/src/com/hiliang/mt6639concurrency/DualStaGate.java" \
    "$ROOT/tests/java/com/hiliang/mt6639concurrency/DualStaGateTest.java"
java -cp "$OUT:$ANDROID_JAR" com.hiliang.mt6639concurrency.DualStaGateTest
