#!/bin/sh
# Build in a new source copy only. Never installs/restarts any device service.
set -eu
[ "$#" = 5 ] || { echo 'usage: build_standalone_hostapd.sh HOSTAPD_SOURCE LIBNL_SOURCE STAGING TOOLCHAIN NEW_OUTPUT' >&2; exit 64; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC=$(CDPATH= cd -- "$1" && pwd)
NL_SRC=$(CDPATH= cd -- "$2" && pwd)
STAGING=$(CDPATH= cd -- "$3" && pwd)
TC=$(CDPATH= cd -- "$4" && pwd)
OUT=$5
[ -f "$SRC/hostapd/Makefile" ] && [ -f "$SRC/src/ap/ieee802_11_eht.c" ] || exit 66
[ ! -e "$OUT" ] || { echo 'REJECT output_exists' >&2; exit 73; }
CC="$TC/bin/aarch64-openwrt-linux-musl-gcc"
if [ ! -x "$CC" ]; then
    set -- "$TC"/bin/aarch64-*-gcc
    [ "$#" = 1 ] && [ -x "$1" ] || { echo 'REJECT ambiguous_or_missing_compiler' >&2; exit 69; }
    CC=$1
fi
PREFIX=${CC%-gcc}
[ -f "$NL_SRC/CMakeLists.txt" ] && [ -f "$STAGING/usr/lib/libcrypto.a" ] &&
    [ -f "$STAGING/usr/lib/libssl.a" ] || { echo 'REJECT static_libraries_missing' >&2; exit 66; }
mkdir -p "$OUT"
OUT=$(CDPATH= cd -- "$OUT" && pwd)
# Explicitly dereference only inside our fresh output; shared build trees stay read-only.
cp -RL "$SRC" "$OUT/src"
cp -RL "$NL_SRC" "$OUT/libnl-src"
cp "$ROOT/native/hostapd/standalone.config" "$OUT/src/hostapd/.config"
{
    echo "source=$SRC"
    echo "staging=$STAGING"
    echo "toolchain=$TC"
    "$CC" --version
    sha256sum "$ROOT/native/hostapd/standalone.config" "$ROOT/native/hostapd/config_check.c" "$0"
} > "$OUT/build-inputs.txt"
find "$OUT/src" -type f \( -name '*.c' -o -name '*.h' -o -name Makefile \) -print0 |
    sort -z | xargs -0 sha256sum > "$OUT/source-before.sha256"
make -C "$OUT/src/hostapd" clean > "$OUT/clean.log" 2>&1
export STAGING_DIR="$STAGING"
PKG_CONFIG_BIN="$(dirname "$STAGING")/host/bin/pkg-config"
[ -x "$PKG_CONFIG_BIN" ] || { echo 'REJECT target_pkg_config_missing' >&2; exit 69; }
cmake -S "$OUT/libnl-src" -B "$OUT/libnl-build" \
    -DCMAKE_C_COMPILER="$CC" -DCMAKE_SYSTEM_NAME=Linux \
    -DCMAKE_SYSTEM_PROCESSOR=aarch64 -DCMAKE_BUILD_TYPE=Release > "$OUT/libnl-configure.log" 2>&1
cmake --build "$OUT/libnl-build" --target nl-tiny-static -j2 > "$OUT/libnl-build.log" 2>&1
cat > "$OUT/target-pkg-config" <<EOF
#!/bin/sh
export PKG_CONFIG_SYSROOT_DIR='$STAGING'
export PKG_CONFIG_LIBDIR='$STAGING/usr/lib/pkgconfig'
unset PKG_CONFIG_PATH
exec '$PKG_CONFIG_BIN' "\$@"
EOF
chmod 700 "$OUT/target-pkg-config"
"$CC" -O2 -Wall -Wextra -Werror -c "$ROOT/native/hostapd/config_check.c" -o "$OUT/config_check.o"
# libnl-tiny and OpenSSL come from the same existing musl target sysroot.
# Static linking avoids Android linker namespaces and unavailable musl loaders.
if ! make -C "$OUT/src/hostapd" -j2 \
    CC="$CC" AR="$PREFIX-ar" \
    EXTRA_CFLAGS="-O2 -D_GNU_SOURCE -I$STAGING/usr/include -I$STAGING/usr/include/libnl-tiny" \
    PKG_CONFIG="$OUT/target-pkg-config" V=1 \
    LDFLAGS="-static -Wl,--wrap=main $OUT/config_check.o -L$OUT/libnl-build -L$STAGING/usr/lib" \
    hostapd > "$OUT/build.log" 2>&1; then
    echo "BUILD_FAILED log=$OUT/build.log" >&2
    exit 70
fi
make -C "$OUT/src/hostapd" -j2 CC="$CC" AR="$PREFIX-ar" \
    EXTRA_CFLAGS="-O2 -D_GNU_SOURCE -I$STAGING/usr/include -I$STAGING/usr/include/libnl-tiny" \
    PKG_CONFIG="$OUT/target-pkg-config" V=1 \
    LDFLAGS="-static -L$OUT/libnl-build -L$STAGING/usr/lib" \
    hostapd_cli >> "$OUT/build.log" 2>&1
mkdir "$OUT/bin"
cp "$OUT/src/hostapd/hostapd" "$OUT/src/hostapd/hostapd_cli" "$OUT/bin/"
"$PREFIX-readelf" -h -l -d "$OUT/bin/hostapd" > "$OUT/hostapd.elf.txt"
grep -q 'AArch64' "$OUT/hostapd.elf.txt" || exit 69
if grep -Eq 'INTERP|\(NEEDED\)' "$OUT/hostapd.elf.txt"; then
    echo 'REJECT dynamically_linked_candidate' >&2
    exit 69
fi
(cd "$OUT/bin" && sha256sum hostapd hostapd_cli > SHA256SUMS)
echo "BUILT $OUT/bin static_aarch64=1 device_install=0 Binder_integration=0"
