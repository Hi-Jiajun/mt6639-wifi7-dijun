#!/bin/sh
# Build the current-source Bluetooth LE Audio host profile module as a reproducible ZIP.
set -eu
[ "$#" = 1 ] || { echo 'usage: build_bt_module.sh OUTPUT_ZIP' >&2; exit 64; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE=$ROOT/modules/mt6639-bt
OUT=$1
[ ! -e "$OUT" ] || { echo 'REJECT output_exists' >&2; exit 73; }
[ -f "$SOURCE/module.prop" ] && [ -f "$SOURCE/compatibility.conf" ] || exit 66

PARENT=$(dirname -- "$OUT")
mkdir -p "$PARENT"
PARENT=$(CDPATH= cd -- "$PARENT" && pwd)
OUT=$PARENT/$(basename -- "$OUT")
TMP=$(mktemp -d "$PARENT/.mt6639-bt.XXXXXX")
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -r "$TMP"' EXIT HUP INT TERM
mkdir "$TMP/module"
cp -R "$SOURCE"/. "$TMP/module/"

chmod 0755 "$TMP/module/customize.sh" "$TMP/module/action.sh" "$TMP/module/service.sh" \
    "$TMP/module/uninstall.sh" "$TMP/module/bin/control.sh"
chmod 0644 "$TMP/module/module.prop" "$TMP/module/config.conf" \
    "$TMP/module/compatibility.conf" "$TMP/module/README.md" "$TMP/module/skip_mount"

if find "$TMP/module" -type l | grep -q .; then
    echo 'REJECT symlink_in_module' >&2
    exit 69
fi
if grep -R -n -E 'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|(gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{24,}' \
    "$TMP/module" >/dev/null 2>&1; then
    echo 'REJECT credential_pattern_in_module' >&2
    exit 69
fi

find "$TMP/module" -exec touch -t 202610260000 {} +
(
    cd "$TMP/module"
    find . -type f -print | LC_ALL=C sort | sed 's#^\./##' |
        zip -q -X -@ "$TMP/module.zip"
)
mv "$TMP/module.zip" "$OUT"
trap - EXIT HUP INT TERM
chmod -R u+w "$TMP"
rm -r "$TMP"
echo "BUILT $OUT size=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT")"
sha256sum "$OUT"
