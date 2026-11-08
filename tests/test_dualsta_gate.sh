#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ANDROID_JAR=${ANDROID_JAR:-}
[ -n "$ANDROID_JAR" ] || {
    echo "ANDROID_JAR must point at platforms/android-*/android.jar" >&2; exit 64; }
OUT="$ROOT/outputs/test-dualsta-gate"
mkdir -p "$OUT"
javac -encoding UTF-8 -source 8 -target 8 -nowarn -cp "$ANDROID_JAR" -d "$OUT" \
    "$ROOT/modules/mt6639-concurrency-lsposed/src/com/hiliang/mt6639concurrency/DualStaGate.java" \
    "$ROOT/tests/java/com/hiliang/mt6639concurrency/DualStaGateTest.java"
java -cp "$OUT:$ANDROID_JAR" com.hiliang.mt6639concurrency.DualStaGateTest
