#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$#" = 1 ] || { echo 'usage: build_native_tether.sh <new-output-directory>' >&2; exit 64; }
OUT=$1
[ ! -e "$OUT" ] || { echo 'output already exists' >&2; exit 73; }
mkdir -p "$OUT/classes" "$OUT/stubs"
OUT=$(CDPATH= cd -- "$OUT" && pwd)
javac -source 8 -target 8 -nowarn -d "$OUT/stubs" "$ROOT"/native/framework/compile-stubs/android/os/*.java
javac -encoding UTF-8 -source 8 -target 8 -nowarn -cp "$OUT/stubs" -d "$OUT/classes" "$ROOT/native/framework/NativeTether.java"
jar cf "$OUT/classes.jar" -C "$OUT/classes" .
if jar tf "$OUT/classes.jar" | grep -q '^android/'; then
    echo 'REJECT: compile stubs leaked into deliverable' >&2; exit 69
fi
d8 --min-api 33 --output "$OUT" "$OUT/classes.jar"
shasum -a 256 "$OUT/classes.dex"
