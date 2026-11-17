#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT/outputs/test-mt6639-ui-bands"
mkdir -p "$OUT"
javac -encoding UTF-8 -d "$OUT" \
    "$ROOT/modules/mt6639-ui-lsposed/src/com/hiliang/mt6639ui/BandResolver.java" \
    "$ROOT/tests/java/com/hiliang/mt6639ui/BandResolverTest.java"
java -cp "$OUT" com.hiliang.mt6639ui.BandResolverTest
