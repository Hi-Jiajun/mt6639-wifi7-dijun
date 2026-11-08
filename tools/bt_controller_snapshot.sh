#!/system/bin/sh
# Read-only Bluetooth controller/ISO/profile snapshot. Never toggles Bluetooth.
set -u
umask 077
D=${1:-}
case "$D" in /data/local/tmp/mt6639-bt-controller-*) ;; *) exit 64 ;; esac
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&
    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77
[ "$(CDPATH= cd -P -- "$D" && pwd)" = "$D" ] || exit 77

timeout 10 dumpsys bluetooth_manager > "$D/dumpsys-bluetooth.raw" 2>&1 || exit 69
timeout 6 getprop > "$D/properties.raw" 2>&1 || exit 69
timeout 5 settings get global bluetooth_on > "$D/bluetooth-on" 2>&1 || exit 69

hci=$(sed -n 's/^[[:space:]]*hci_version: [^ ]*(\([^)]*\)).*/\1/p' "$D/dumpsys-bluetooth.raw" | head -1)
lmp=$(sed -n 's/^[[:space:]]*lmp_version: [^ ]*(\([^)]*\)).*/\1/p' "$D/dumpsys-bluetooth.raw" | head -1)
local_features=$(sed -n '/local_supported_features:/,/local_supported_commands:/{s/^[[:space:]]*page0: //p;}' "$D/dumpsys-bluetooth.raw" | head -1)
le_features=$(sed -n '/le_local_supported_features:/,/local_supported_commands:/{s/^[[:space:]]*page0: //p;}' "$D/dumpsys-bluetooth.raw" | head -1)
iso_packet_len=$(sed -n 's/^[[:space:]]*iso_data_packet_length: //p' "$D/dumpsys-bluetooth.raw" | head -1)
iso_packet_count=$(sed -n 's/^[[:space:]]*total_num_iso_data_packets: //p' "$D/dumpsys-bluetooth.raw" | head -1)
adv_sets=$(sed -n 's/^[[:space:]]*le_number_supported_advertising_sets: //p' "$D/dumpsys-bluetooth.raw" | head -1)
periodic_list=$(sed -n 's/^[[:space:]]*le_periodic_advertiser_list_size: //p' "$D/dumpsys-bluetooth.raw" | head -1)

case "$local_features" in 0x*) ;; *) local_features=unknown ;; esac
case "$le_features" in 0x*) ;; *) le_features=unknown ;; esac
decode_bit() {
    raw=$1 shift_value=$2
    case "$raw" in 0x*) ;; *) echo unknown; return ;; esac
    hex=${raw#0x}
    while [ "${#hex}" -lt 16 ]; do hex=0$hex; done
    high_hex=${hex%????????}
    low_hex=${hex#????????}
    if [ "$shift_value" -lt 32 ]; then
        echo $(( (0x$low_hex >> shift_value) & 1 ))
    else
        echo $(( (0x$high_hex >> (shift_value - 32)) & 1 ))
    fi
}

le2m=$(decode_bit "$le_features" 8)
coded=$(decode_bit "$le_features" 11)
ext_adv=$(decode_bit "$le_features" 12)
periodic_adv=$(decode_bit "$le_features" 13)
cis_central=$(decode_bit "$le_features" 28)
cis_peripheral=$(decode_bit "$le_features" 29)
iso_broadcaster=$(decode_bit "$le_features" 30)
sync_receiver=$(decode_bit "$le_features" 31)
iso_channels=$(decode_bit "$le_features" 32)
pawr_adv=$(decode_bit "$le_features" 43)
pawr_scan=$(decode_bit "$le_features" 44)
channel_sounding=$(decode_bit "$le_features" 46)

sed -n '/^Enabled Profile Services:/,/^$/p' "$D/dumpsys-bluetooth.raw" > "$D/enabled-profiles" || exit 69
sed -n '/^  ISO Manager:/,/^  ----------------/p' "$D/dumpsys-bluetooth.raw" > "$D/iso-manager" || exit 69
grep -E '^\[(ro|persist|vendor)\.bluetooth\.|^\[bluetooth\.profile\.' "$D/properties.raw" > "$D/bluetooth-properties" || true

{
    echo "bluetooth_on=$(cat "$D/bluetooth-on")"
    echo "hci_version=${hci:-unknown}"
    echo "lmp_version=${lmp:-unknown}"
    echo "local_supported_features_page0=$local_features"
    echo "le_local_supported_features_page0=$le_features"
    echo "iso_data_packet_length=${iso_packet_len:-unknown}"
    echo "total_num_iso_data_packets=${iso_packet_count:-unknown}"
    echo "le_number_supported_advertising_sets=${adv_sets:-unknown}"
    echo "le_periodic_advertiser_list_size=${periodic_list:-unknown}"
    echo "le_2m_phy=$le2m"
    echo "le_coded_phy=$coded"
    echo "le_extended_advertising=$ext_adv"
    echo "le_periodic_advertising=$periodic_adv"
    echo "cis_central=$cis_central"
    echo "cis_peripheral=$cis_peripheral"
    echo "iso_broadcaster=$iso_broadcaster"
    echo "sync_receiver=$sync_receiver"
    echo "iso_channels_bit32=$iso_channels"
    echo "pawr_advertiser=$pawr_adv"
    echo "pawr_scanner=$pawr_scan"
    echo "channel_sounding=$channel_sounding"
} > "$D/summary.tmp" || exit 74
mv "$D/summary.tmp" "$D/summary" || exit 74
cat "$D/summary"
echo "enabled_profiles=$D/enabled-profiles"
echo "iso_manager=$D/iso-manager"
echo "bluetooth_properties=$D/bluetooth-properties"
echo RESULT=snapshot_complete
