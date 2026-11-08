#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$#" = 1 ] || { echo 'usage: build_softap_profile.sh <new-output-directory>' >&2; exit 64; }
OUT=$1
[ ! -e "$OUT" ] || { echo 'output already exists' >&2; exit 73; }
mkdir -p "$OUT/classes"
OUT=$(CDPATH= cd -- "$OUT" && pwd)
javac -encoding UTF-8 -source 8 -target 8 -nowarn -d "$OUT/classes" "$ROOT/native/framework/WifiSoftApProfile.java"
jar cf "$OUT/classes.jar" -C "$OUT/classes" .
d8 --min-api 33 --output "$OUT" "$OUT/classes.jar"
shasum -a 256 "$OUT/classes.dex"
