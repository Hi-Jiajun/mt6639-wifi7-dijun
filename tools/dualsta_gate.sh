#!/system/bin/sh
# Short-lived marker read by the bounded dual-STA LSPosed test module.
set -u
umask 022
D=${1:-}
OP=${2:-status}
TTL=${3:-60}
case "$D" in /data/local/tmp/mt6639-sta-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77
D=$(CDPATH= cd -P -- "$D" && pwd) || exit 64

BASE=/data/misc/mt6639-concurrency
MARKER=$BASE/gate
TMP=$BASE/.gate.$$
LEASE_FILE=$D/gate.lease
INTENT=$D/gate.intent
lease_now() { cat "$MARKER" 2>/dev/null | sed -n 's/^lease=//p'; }
now_ms() { awk '{printf "%.0f", $1 * 1000}' /proc/uptime; }
marker_status() {
    if [ ! -e "$MARKER" ] && [ ! -L "$MARKER" ]; then
        echo state=disarmed
        return 0
    fi
    if [ -L "$MARKER" ] || [ ! -f "$MARKER" ]; then
        echo state=invalid reason=marker_not_regular
        return 0
    fi
    [ "$(wc -l < "$MARKER" 2>/dev/null)" -eq 4 ] || {
        echo state=invalid reason=field_count
        return 0
    }
    schema=$(sed -n 's/^schema=//p' "$MARKER")
    boot=$(sed -n 's/^boot=//p' "$MARKER")
    expires=$(sed -n 's/^expires_ms=//p' "$MARKER")
    lease=$(sed -n 's/^lease=//p' "$MARKER")
    [ "$schema" = mt6639-dualsta-gate-v1 ] &&
        printf '%s\n' "$lease" | grep -Eq '^[0-9a-f]{16}$' &&
        case "$expires" in ''|*[!0-9]*) false ;; *) true ;; esac || {
            echo state=invalid reason=field_value
            return 0
        }
    current_boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || {
        echo state=invalid reason=boot_unreadable
        return 0
    }
    if [ "$boot" != "$current_boot" ]; then
        echo "state=expired reason=boot_mismatch boot=$boot expires_ms=$expires lease=$lease"
        return 0
    fi
    current_ms=$(now_ms 2>/dev/null) || {
        echo state=invalid reason=uptime_unreadable
        return 0
    }
    if [ "$expires" -le "$current_ms" ]; then
        echo "state=expired reason=deadline boot=$boot expires_ms=$expires lease=$lease"
    elif [ $((expires - current_ms)) -gt 120000 ]; then
        echo "state=invalid reason=remaining_ttl_too_long boot=$boot expires_ms=$expires lease=$lease"
    else
        echo "state=armed boot=$boot expires_ms=$expires lease=$lease"
    fi
}
cleanup_tmp() { rm -f "$TMP"; }

case "$OP" in
    status)
        marker_status
        exit 0
        ;;
    arm|disarm) ;;
    *) exit 64 ;;
esac

if [ "$OP" = arm ]; then
    [ "$(id -Z)" = u:r:ksu:s0 ] && [ "$(getenforce)" = Enforcing ] || exit 77
    case "$TTL" in ''|*[!0-9]*) exit 64 ;; esac
    [ "$TTL" -ge 30 ] && [ "$TTL" -le 120 ] || exit 64
    [ ! -e "$MARKER" ] && [ ! -L "$MARKER" ] || { echo REJECT gate_already_armed >&2; exit 75; }
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) && [ -n "$boot" ] || exit 70
    current_ms=$(now_ms) || exit 70
    expires_ms=$((current_ms + TTL * 1000))
    lease=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n') || exit 70
    case "$lease" in [0-9a-f][0-9a-f]*) [ "${#lease}" = 16 ] || exit 70 ;; *) exit 70 ;; esac
    [ ! -e "$BASE" ] || { [ -d "$BASE" ] && [ ! -L "$BASE" ] &&
        [ "$(stat -c %u "$BASE")" = 0 ] || exit 77; }
    [ -d "$BASE" ] || mkdir "$BASE" || exit 74
    chmod 0755 "$BASE" || exit 74
    printf '%s\n' "$lease" > "$LEASE_FILE" || exit 74
    : > "$INTENT" || exit 74
    trap cleanup_tmp EXIT HUP INT TERM
    printf 'schema=mt6639-dualsta-gate-v1\nboot=%s\nexpires_ms=%s\nlease=%s\n' \
        "$boot" "$expires_ms" "$lease" > "$TMP" || exit 74
    chmod 0644 "$TMP" || exit 74
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -F "$TMP" >/dev/null 2>&1 || exit 77
    fi
    mv "$TMP" "$MARKER" || exit 74
    trap - EXIT HUP INT TERM
    echo "state=armed boot=$boot expires_ms=$expires_ms lease=$lease"
    exit 0
fi

[ -f "$LEASE_FILE" ] && [ ! -L "$LEASE_FILE" ] || { echo REJECT lease_record_missing >&2; exit 73; }
recorded=$(cat "$LEASE_FILE" 2>/dev/null) || exit 70
current=$(lease_now)
if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then
    [ -f "$MARKER" ] && [ ! -L "$MARKER" ] && [ "$current" = "$recorded" ] ||
        { echo REJECT gate_owner_mismatch >&2; exit 73; }
    rm -f "$MARKER" || exit 74
fi
rm -f "$LEASE_FILE" "$INTENT" || exit 74
echo state=disarmed
