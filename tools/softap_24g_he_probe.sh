#!/system/bin/sh
# One-shot 2.4GHz HE capability probe.
#
# The framework path cannot answer "does this radio do 2.4GHz HE": SoftApInfo
# reports wifiStandard=4 whether or not ieee80211ax reached hostapd, and the
# vendor hostapd's control socket is not reachable from the ksu domain. This
# probe starts a minimal BSS with the module's own pinned hostapd on 2.4GHz and
# reads that hostapd's own STATUS, so radio capability and HAL reporting are
# separated. It never touches the STA, Tethering or the framework SoftAP state.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-probe-24g-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] || exit 77
[ "$(id -u)" = 0 ] && [ "$(id -Z)" = u:r:ksu:s0 ] || exit 77
[ "$(getenforce)" = Enforcing ] || exit 77

MOD=/data/adb/modules/mt6639-hotspot
[ -d "$MOD" ] && [ ! -L "$MOD" ] || exit 66
if [ -f "$MOD/bin/android-env.sh" ] && [ ! -L "$MOD/bin/android-env.sh" ]; then
    . "$MOD/bin/android-env.sh"
fi

IF=mtap0
HOSTAPD=$MOD/bin/hostapd
CLI=$MOD/bin/hostapd_cli
KEYF=$D/ap.key
CHANNEL=6
FREQ=2437

[ -s "$KEYF" ] && [ ! -L "$KEYF" ] && [ "$(stat -c %u "$KEYF")" = 0 ] &&
    [ "$(stat -c %a "$KEYF")" = 600 ] || exit 66
for pair in "$HOSTAPD:15023e3bfe6b08415ebcc92479c0cd4fb14adb57b9010266af73bcc1bdc7bc2b" \
    "$CLI:2da7d467b7b2628c4da40fb5ccc101b17f8b279bd9c038c32e4ae6e5b2924a42"; do
    file=${pair%%:*}
    want=${pair##*:}
    got=$(sha256sum "$file" 2>/dev/null) || exit 69
    [ "${got%% *}" = "$want" ] || exit 69
done

# Stay out of the way of a live native session or the framework's own AP.
[ ! -e /data/adb/mt6639-hotspot/session/task ] ||
    { echo 'REJECT native_session_exists' >&2; exit 73; }
[ ! -e "/sys/class/net/$IF" ] || { echo 'REJECT mtap0_exists' >&2; exit 73; }
[ ! -e /sys/class/net/ap0 ] || { echo 'REJECT framework_ap_present' >&2; exit 73; }

cleanup() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [ -f "$D/hostapd.pid" ]; then
        pid=$(cat "$D/hostapd.pid" 2>/dev/null)
        case "$pid" in ''|*[!0-9]*) ;; *)
            [ -d "/proc/$pid" ] &&
                [ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$HOSTAPD" ] &&
                kill -TERM "$pid" 2>/dev/null
            n=0
            while [ -d "/proc/$pid" ] && [ "$n" -lt 8 ]; do sleep 1; n=$((n + 1)); done
            ;;
        esac
    fi
    if [ -e "/sys/class/net/$IF" ]; then
        timeout 5 iw dev "$IF" del > "$D/interface.remove" 2>&1
    fi
    {
        printf 'iface_after=%s\n' "$( [ -e "/sys/class/net/$IF" ] && echo present || echo absent )"
        printf 'hostapd_after=%s\n' "$( [ -d "/proc/$(cat "$D/hostapd.pid" 2>/dev/null)" ] && echo alive || echo gone )"
    } >> "$D/probe.result" 2>/dev/null
    exit "$rc"
}
trap cleanup EXIT HUP INT TERM

KEY=$(cat "$KEYF")
{
    printf 'interface=%s\n' "$IF"
    printf 'driver=nl80211\n'
    printf 'ctrl_interface=%s/ctrl\n' "$D"
    printf 'ssid=MT6639-Probe-24g-HE\n'
    printf 'hw_mode=g\n'
    printf 'channel=%s\n' "$CHANNEL"
    printf 'ieee80211n=1\n'
    printf 'ieee80211ax=1\n'
    printf 'wpa=2\n'
    printf 'wpa_key_mgmt=SAE\n'
    # Without an explicit cipher hostapd refuses HT/HE on WPA2/WPA3
    # ("requires CCMP/GCMP to be enabled, disabling HE capabilities") and the
    # probe would silently measure a plain 11g BSS.
    printf 'rsn_pairwise=CCMP\n'
    printf 'ieee80211w=2\n'
    printf 'sae_pwe=1\n'
    printf 'wpa_passphrase=%s\n' "$KEY"
} > "$D/ap.conf" || exit 74
chmod 0600 "$D/ap.conf"

timeout 5 iw phy phy0 interface add "$IF" type __ap > "$D/interface.create" 2>&1 ||
    { printf 'interface_create_failed\n' > "$D/failure"; exit 69; }
cat "/sys/class/net/$IF/ifindex" > "$D/interface.index" 2>/dev/null

nohup "$HOSTAPD" -ddd "$D/ap.conf" > "$D/native.log" 2>&1 < /dev/null &
echo "$!" > "$D/hostapd.pid"

status=
n=0
while [ "$n" -lt 8 ]; do
    if timeout 4 "$CLI" -p "$D/ctrl" -i "$IF" status > "$D/status.active" 2>&1 &&
        grep -Fxq 'state=ENABLED' "$D/status.active"; then
        status=enabled
        break
    fi
    kill -0 "$(cat "$D/hostapd.pid")" 2>/dev/null || { status=exited; break; }
    sleep 1
    n=$((n + 1))
done

{
    printf 'status=%s\n' "${status:-timeout}"
    for key in state freq channel hw_mode ieee80211n ieee80211ac ieee80211ax \
        ieee80211be secondary_channel he_oper_chwidth; do
        line=$(grep -m 1 "^$key=" "$D/status.active" 2>/dev/null)
        [ -n "$line" ] && printf '%s\n' "$line"
    done
    printf 'log_ax_mentions=%s\n' "$(grep -ci '802\.11ax' "$D/native.log" 2>/dev/null || echo 0)"
} > "$D/probe.result"

cat "$D/probe.result"
[ "$status" = enabled ] &&
    grep -Fxq 'ieee80211ax=1' "$D/status.active" &&
    grep -Fxq "freq=$FREQ" "$D/status.active" ||
    { echo 'PROBE_RESULT=failed' >&2; exit 71; }
echo 'PROBE_RESULT=radio_reports_2ghz_he'
