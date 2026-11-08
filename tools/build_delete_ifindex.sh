#!/bin/sh
set -eu
[ "$#" = 1 ] || { echo 'usage: build_delete_ifindex.sh <new-output-directory>' >&2; exit 64; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=$1
[ ! -e "$OUT" ] || { echo 'REJECT output_exists' >&2; exit 73; }
ZIG=${ZIG:-$(command -v zig || true)}
[ -n "$ZIG" ] && [ -x "$ZIG" ] || { echo 'REJECT zig_compiler_missing (set ZIG=/path/to/zig)' >&2; exit 69; }
PARENT=$(dirname -- "$OUT")
mkdir -p "$PARENT"
PARENT=$(CDPATH= cd -- "$PARENT" && pwd)
OUT=$PARENT/$(basename -- "$OUT")
mkdir "$OUT"
"$ZIG" cc -target aarch64-linux-musl -O2 -static -s \
    "$ROOT/native/netdev/delete_ifindex.c" -o "$OUT/delete_ifindex"
chmod 0755 "$OUT/delete_ifindex"
details=$(file "$OUT/delete_ifindex")
case "$details" in *'ARM aarch64'*'statically linked'*) ;; *)
    echo "REJECT unexpected_helper_format: $details" >&2
    exit 69 ;;
esac
printf '%s\n' "$details"
sha256sum "$OUT/delete_ifindex"
