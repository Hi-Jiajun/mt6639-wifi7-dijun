#!/system/bin/sh
# A reversible profile adapter, not a hotspot start/tethering command.
set -u
umask 077
[ "$#" = 2 ] || { echo 'usage: softap_profile.sh TASK inspect|enable|enable-24g|enable-5g|restore' >&2; exit 64; }
D=$1 OP=$2
case "$OP" in inspect|enable|enable-24g|enable-5g|restore) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] || exit 64
D=$(CDPATH= cd -P -- "$D" && pwd) || exit 64
case "$D" in /data/local/tmp/mt6639-ap-*) ;; *) exit 64 ;; esac
[ "$(stat -c %u "$D")" = 0 ] && [ "$(stat -c %a "$D")" = 700 ] || exit 77
[ "$(id -u)" = 0 ] && [ "$(id -Z)" = u:r:ksu:s0 ] && [ "$(getenforce)" = Enforcing ] || exit 77
[ "$(getprop ro.product.device)" = dijun ] || exit 69
[ -f "$D/softap-profile.dex" ] && [ ! -L "$D/softap-profile.dex" ] || exit 66
exec 9>"$D/profile.lock" || exit 70
flock -n 9 9>&9 || exit 75
BOOT=$(cat /proc/sys/kernel/random/boot_id) || exit 70
OVERLAYS='config_wifiSoftapHeSupported config_wifiSoftapIeee80211beSupported'
SYSTEM_KEYS='hotspot_80211ax_support hotspot_80211ax_support_temp'
java() { CLASSPATH="$D/softap-profile.dex" timeout 12 app_process /system/bin WifiSoftApProfile "$@"; }
driver_value() {
    out=$(timeout 4 iwpriv wlan0 driver 'GET_CFG ApEHT') || return 1
    value=$(printf '%s\n' "$out" | sed -n 's/.*driver:[[:space:]]*//p' | tr -d '\r')
    case "$value" in 0x0|0) echo 0 ;; 0x1|1) echo 1 ;; 0x2|2) echo 2 ;; *) return 1 ;; esac
}
driver_set() {
    timeout 4 iwpriv wlan0 driver "SET_CFG ApEHT $1" || return 1
    [ "$(driver_value)" = "$1" ]
}
overlay_value() {
    value=$(printf '%s\n' "$cache" | sed -n "s/^Resource Name: $1, value: //p")
    case "$value" in true|false) printf '%s\n' "$value" ;; '') echo unset ;; *) return 1 ;; esac
}
snapshot_ok() {
    [ "$(cat "$D/profile.boot" 2>/dev/null)" = "$BOOT" ] &&
        (cd "$D" && sha256sum -c before.sha256 >/dev/null 2>&1)
}
ap_disabled() { java inspect > "$D/current.summary" && grep -qx 'ap_state=11' "$D/current.summary"; }

restore() {
    [ -f "$D/profile.ready" ] || { echo 'state=not_applied'; return 0; }
    snapshot_ok || { echo 'REJECT snapshot_or_boot_mismatch' >&2; return 73; }
    ap_disabled || { echo 'REJECT hotspot_must_be_disabled' >&2; return 73; }
    cache=$(timeout 5 cmd wifi get-overlay-config-values) || return 70
    operation=$(cat "$D/profile.operation" 2>/dev/null || printf legacy)
    while read -r key before; do
        current=$(overlay_value "$key") || return 70
        allowed=true
        [ "$key" != config_wifiSoftapIeee80211beSupported ] ||
            [ "$operation" != enable-24g ] || allowed=false
        [ "$current" = "$allowed" ] || [ "$current" = "$before" ] ||
            { echo "REJECT overlay_conflict $key" >&2; return 73; }
    done < "$D/before.overlays"
    if [ -f "$D/driver.intent" ]; then
        current=$(driver_value) || return 70
        [ "$current" = 1 ] || [ "$current" = "$(cat "$D/before.driver")" ] ||
            { echo 'REJECT driver_conflict' >&2; return 73; }
    fi
    if [ -f "$D/system.intent" ]; then
        while read -r key before; do
        current=$(settings --user 0 get system "$key") || return 70
        allowed=$before
        [ "$key" != hotspot_80211ax_support ] || allowed=1
        [ "$key" != hotspot_80211ax_support_temp ] || allowed=0
        [ "$current" = "$before" ] || [ "$current" = "$allowed" ] || { echo "REJECT system_setting_conflict $key" >&2; return 73; }
        done < "$D/before.system"
    fi
    # Complete config comparison is performed by the platform object, not by a
    # redacted dump or a subset of fields. Refuse unrelated user edits.
    failed=0
    java restore "$D" > "$D/config.restore" 2>&1 || failed=1
    while read -r key before; do
        if [ "$before" = unset ]; then
            timeout 5 cmd wifi force-overlay-config-value bool "$key" disabled false || failed=1
        else
            timeout 5 cmd wifi force-overlay-config-value bool "$key" enabled "$before" || failed=1
        fi
    done < "$D/before.overlays"
    if [ -f "$D/system.intent" ]; then
        while read -r key before; do
        if [ "$before" = null ]; then
            settings --user 0 delete system "$key" >/dev/null || failed=1
        else
            settings --user 0 put system "$key" "$before" || failed=1
        fi
        [ "$(settings --user 0 get system "$key")" = "$before" ] || failed=1
        done < "$D/before.system"
    fi
    if [ -f "$D/driver.intent" ]; then driver_set "$(cat "$D/before.driver")" || failed=1; fi
    cache=$(timeout 5 cmd wifi get-overlay-config-values) || return 70
    while read -r key before; do [ "$(overlay_value "$key")" = "$before" ] || failed=1; done < "$D/before.overlays"
    [ "$failed" = 0 ] || { echo 'restore=incomplete snapshots_retained=1' >&2; return 70; }
    printf '%s\n' restored > "$D/profile.state" || return 70
    echo 'state=restored config=exact overlays=original touched_driver_and_system=original'
}

if [ "$OP" = inspect ]; then
    java inspect || exit $?
    value=$(driver_value) || exit 70
    printf 'driver_ApEHT=%s\n' "$value"
    for key in $SYSTEM_KEYS; do echo "$key=$(settings --user 0 get system "$key")"; done
    exit 0
fi
if [ "$OP" = restore ]; then restore; exit $?; fi

[ "$(getprop ro.build.fingerprint)" = 'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys' ] || exit 69
h=$(sha256sum /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko) || exit 69
[ "${h%% *}" = 54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea ] || exit 69
h=$(sha256sum /apex/com.android.wifi/javalib/service-wifi.jar) || exit 69
[ "${h%% *}" = 2acb1ba9ff601be9e3817b28a0538afb41374db078120e6a3d4d104fef7671d6 ] || exit 69
ap_disabled || { echo 'REJECT hotspot_must_be_disabled' >&2; exit 73; }
[ ! -f "$D/profile.ready" ] || { echo 'REJECT one_shot_task_new_directory_required' >&2; exit 73; }
if [ ! -f "$D/profile.ready" ]; then
    [ ! -e "$D/ap.before.parcel" ] || { echo 'REJECT partial_preparation_new_directory_required' >&2; exit 73; }
    prepare=prepare
    [ "$OP" != enable-24g ] || prepare=prepare-24g
    [ "$OP" != enable-5g ] || prepare=prepare-5g
    java "$prepare" "$D" > "$D/config.prepare" 2>&1 || exit 70
    driver_value > "$D/before.driver" || exit 70
    cache=$(timeout 5 cmd wifi get-overlay-config-values) || exit 70
    printf '%s\n' "$cache" > "$D/cache.before" || exit 70
    for key in $OVERLAYS; do
        value=$(overlay_value "$key") || exit 70
        printf '%s %s\n' "$key" "$value" >> "$D/before.overlays" || exit 70
    done
    for key in $SYSTEM_KEYS; do
        value=$(settings --user 0 get system "$key") || exit 70
        case "$value" in 0|1|null) ;; *) exit 73 ;; esac
        printf '%s %s\n' "$key" "$value" >> "$D/before.system" || exit 70
    done
    printf '%s\n' "$BOOT" > "$D/profile.boot" || exit 70
    printf '%s\n' "$OP" > "$D/profile.operation" || exit 70
    (cd "$D" && sha256sum ap.before.parcel ap.applied.parcel before.driver before.overlays before.system profile.boot profile.operation > before.sha256) || exit 70
    snapshot_ok || exit 70
    : > "$D/profile.ready" || exit 70
fi
snapshot_ok || exit 73
printf '%s\n' prepared > "$D/profile.state" || exit 70
trap 'trap - HUP INT TERM; restore; exit 130' HUP INT TERM
failed=0
for key in $OVERLAYS; do
    desired=true
    [ "$key" != config_wifiSoftapIeee80211beSupported ] || [ "$OP" != enable-24g ] || desired=false
    timeout 5 cmd wifi force-overlay-config-value bool "$key" enabled "$desired" || failed=1
done
if [ "$OP" = enable-5g ]; then
    : > "$D/system.intent" || failed=1
    settings --user 0 put system hotspot_80211ax_support 1 || failed=1
    : > "$D/driver.intent" || failed=1
    driver_set 1 || failed=1
fi
# Measured: MIUI decides the SoftAP HE capability from Settings.System when the
# hotspot starts, so a 2.4GHz trial without this leaves ieee80211ax=0 in the
# generated hostapd_ap0.conf and SoftApInfo reports wifiStandard=4 (11n). This
# stays opt-in because it flips a user-visible MIUI toggle; restore puts the
# original value back (delete when it was null) because system.intent records it.
if [ "$OP" = enable-24g ] && [ "${MT6639_C02_FORCE_AX:-0}" = 1 ]; then
    : > "$D/system.intent" || failed=1
    settings --user 0 put system hotspot_80211ax_support 1 || failed=1
fi
java apply "$D" > "$D/config.apply" 2>&1 || failed=1
cache=$(timeout 5 cmd wifi get-overlay-config-values) || failed=1
for key in $OVERLAYS; do
    desired=true
    [ "$key" != config_wifiSoftapIeee80211beSupported ] || [ "$OP" != enable-24g ] || desired=false
    [ "$(overlay_value "$key")" = "$desired" ] || failed=1
done
[ "$OP" != enable-5g ] || [ "$(settings --user 0 get system hotspot_80211ax_support)" = 1 ] || failed=1
[ "$OP" != enable-24g ] || [ "${MT6639_C02_FORCE_AX:-0}" != 1 ] ||
    [ "$(settings --user 0 get system hotspot_80211ax_support)" = 1 ] || failed=1
if [ "$failed" != 0 ]; then restore; exit 70; fi
printf '%s\n' active > "$D/profile.state" || { restore; exit 70; }
trap - HUP INT TERM
if [ "$OP" = enable-24g ]; then
    printf 'state=active profile=24g resource_HE=1 resource_EHT=0 driver_touched=0 saved_config_HE=1 EHT=0 hotspot_not_started=1 ax_setting_forced=%s\n' \
        "$( [ "${MT6639_C02_FORCE_AX:-0}" = 1 ] && echo 1 || echo 0 )"
else
    echo 'state=active profile=5g driver_ApEHT=1 resource_HE=1 resource_EHT=1 saved_config_HE_EHT=1 hotspot_not_started=1'
fi
