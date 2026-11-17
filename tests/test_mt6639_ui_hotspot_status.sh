#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${MT6639_UI_HOTSPOT_TEST_DIR:-"$ROOT/outputs/test-mt6639-ui-hotspot-status"}
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
mkdir -p "$OUT"
javac -encoding UTF-8 -d "$OUT" \
    "$ROOT/modules/mt6639-ui-lsposed/src/com/hiliang/mt6639ui/HotspotStatus.java" \
    "$ROOT/modules/mt6639-ui-lsposed/src/com/hiliang/mt6639ui/HotspotRequestPayload.java" \
    "$ROOT/tests/java/com/hiliang/mt6639ui/HotspotStatusTest.java" \
    "$ROOT/tests/java/com/hiliang/mt6639ui/HotspotRequestPayloadTest.java"
java -cp "$OUT" com.hiliang.mt6639ui.HotspotStatusTest
java -cp "$OUT" com.hiliang.mt6639ui.HotspotRequestPayloadTest

# Execute MainHook's real lifecycle callbacks and parser. Only Android's main
# event queue / Xposed reflection are fixtures; redirect the snapshot path so
# the test neither writes /data nor requires a phone or elevated privileges.
FIXTURE="$ROOT/tests/java/com/hiliang/mt6639ui/refresh-fixture"
MODULE="$ROOT/modules/mt6639-ui-lsposed"
REFRESH="$OUT/refresh"
mkdir -p "$REFRESH/src" "$REFRESH/classes"
SNAPSHOT="$REFRESH/status"
sed "s|/data/misc/mt6639-hotspot/status|$SNAPSHOT|" \
    "$MODULE/src/com/hiliang/mt6639ui/HotspotStatus.java" \
    > "$REFRESH/src/HotspotStatus.java"
javac -encoding UTF-8 -cp "$ANDROID_JAR" -d "$REFRESH/classes" \
    "$FIXTURE/android/os/Handler.java" \
    "$FIXTURE/android/os/Looper.java" \
    "$FIXTURE/android/os/SystemClock.java" \
    "$FIXTURE/android/app/AlertDialog.java" \
    "$FIXTURE/de/robv/android/xposed/XposedHelpers.java" \
    "$FIXTURE/com/hiliang/mt6639ui/UiDependencies.java" \
    "$MODULE/build-support/xposed-api/src/de/robv/android/xposed/XC_MethodHook.java" \
    "$MODULE/build-support/xposed-api/src/de/robv/android/xposed/XposedBridge.java" \
    "$MODULE/build-support/xposed-api/src/de/robv/android/xposed/IXposedHookLoadPackage.java" \
    "$MODULE/build-support/xposed-api/src/de/robv/android/xposed/callbacks/XC_LoadPackage.java" \
    "$REFRESH/src/HotspotStatus.java" \
    "$MODULE/src/com/hiliang/mt6639ui/MainHook.java" \
    "$ROOT/tests/java/com/hiliang/mt6639ui/HotspotRefreshTest.java"
java -cp "$REFRESH/classes:$ANDROID_JAR" \
    com.hiliang.mt6639ui.HotspotRefreshTest "$SNAPSHOT"
