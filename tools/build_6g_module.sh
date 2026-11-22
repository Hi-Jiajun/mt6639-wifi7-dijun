#!/bin/sh
# Build the current-source 6GHz module as a new ZIP; never install or restart.
set -eu
[ "$#" = 1 ] || { echo 'usage: build_6g_module.sh OUTPUT_ZIP' >&2; exit 64; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE=$ROOT/modules/mt6639-6g
NATIVE=${MT6639_MOUNT_TXN:-$SOURCE/bin/mount-txn}
OUT=$1
[ ! -e "$OUT" ] || { echo 'REJECT output_exists' >&2; exit 73; }
[ -f "$SOURCE/module.prop" ] && [ -f "$NATIVE" ] || exit 66
[ "$(sha256sum "$NATIVE" | awk '{print $1}')" = \
    66646480d4421b48f11defbd371338dbf6ef5c45ddb1d0828497e556a6d4b19b ] ||
    { echo 'REJECT mount_txn_hash' >&2; exit 69; }

PARENT=$(dirname -- "$OUT")
mkdir -p "$PARENT"
PARENT=$(CDPATH= cd -- "$PARENT" && pwd)
OUT=$PARENT/$(basename -- "$OUT")
TMP=$(mktemp -d "$PARENT/.mt6639-6g.XXXXXX")
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -r "$TMP"' EXIT HUP INT TERM
mkdir "$TMP/module"
cp -R "$SOURCE"/. "$TMP/module/"
cp "$NATIVE" "$TMP/module/bin/mount-txn"
cp "$SOURCE/module.prop" "$TMP/module/module.prop.template"

chmod 0755 "$TMP/module/customize.sh" "$TMP/module/action.sh" "$TMP/module/service.sh" \
    "$TMP/module/uninstall.sh" "$TMP/module/bin/apply.sh" \
    "$TMP/module/bin/check.sh" "$TMP/module/bin/control.sh" \
    "$TMP/module/bin/lib.sh" "$TMP/module/bin/mount-txn" \
    "$TMP/module/bin/native.sh" "$TMP/module/bin/post-reboot-check.sh" \
    "$TMP/module/bin/refresh-6g.sh" "$TMP/module/bin/restore.sh" \
    "$TMP/module/bin/softreboot-watch.sh" "$TMP/module/bin/status-card.sh" \
    "$TMP/module/bin/verify-module.sh" "$TMP/module/bin/compat.sh" \
    "$TMP/module/bin/legacy-66-txn.sh"
chmod 0644 "$TMP/module/module.prop" "$TMP/module/module.prop.template" "$TMP/module/config.conf" \
    "$TMP/module/compatibility.conf" \
    "$TMP/module/README.md" "$TMP/module/src/nl80211-wiphy-read.c"

if find "$TMP/module" -type l | grep -q .; then
    echo 'REJECT symlink_in_module' >&2
    exit 69
fi
if grep -R -n -E 'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|(gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{24,}' \
    "$TMP/module" >/dev/null 2>&1; then
    echo 'REJECT credential_pattern_in_module' >&2
    exit 69
fi

(
    cd "$TMP/module"
    find . -type f ! -name SHA256SUMS ! -name module.prop ! -name customize.sh ! -name README.md -print | LC_ALL=C sort |
        sed 's#^\./##' | xargs sha256sum > SHA256SUMS
)
source_tree_sha256=$(
    cd "$SOURCE"
    find . -type f -print | LC_ALL=C sort |
        while IFS= read -r file; do sha256sum "$file"; done |
        sha256sum | awk '{print $1}'
)
{
    echo "source_tree_sha256=$source_tree_sha256"
    echo "mount_txn_sha256=66646480d4421b48f11defbd371338dbf6ef5c45ddb1d0828497e556a6d4b19b"
    echo "release_status=candidate_uninstalled"
} > "$TMP/module/ASSET-PROVENANCE.txt"

# Normalize timestamps for a reproducible archive identity.
find "$TMP/module" -exec touch -t 202610260000 {} +
(
    cd "$TMP/module"
    # Strip the "./" prefix: Info-ZIP drops it on some builds and keeps it on
    # others, and the module loader plus the stage tooling expect bare names.
    find . -type f -print | LC_ALL=C sort | sed 's#^\./##' |
        zip -q -X -@ "$TMP/module.zip"
)
mv "$TMP/module.zip" "$OUT"
trap - EXIT HUP INT TERM
chmod -R u+w "$TMP"
rm -r "$TMP"
echo "BUILT $OUT size=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT")"
sha256sum "$OUT"
