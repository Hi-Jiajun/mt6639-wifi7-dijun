#!/usr/bin/env bash
# One-shot, read-only-ish check of the Wi-Fi settings 6G band badge.
#
# The badge is a self-drawn pill on the row title TextView, so `uiautomator dump`
# cannot see it. This script always takes a screenshot and treats that as the
# verdict; the dump is captured only to prove which rows were on screen.
#
# It restarts com.android.settings and triggers one scan. It does not touch
# SELinux, LSPosed state, country code or the Wi-Fi module.
set -euo pipefail

ADB=${ADB:-outputs/linux-adb-20261026/extracted/platform-tools/adb}
SERIAL=${SERIAL:-YOUR_SERIAL}
OUT=${1:-"outputs/ui-6g-badge-check-$(date +%Y%m%d-%H%M%S)"}
SETTLE=${SETTLE:-12}

mkdir -p "$OUT"
adb_run() { "$ADB" -P 5037 -s "$SERIAL" shell "$@"; }
su_run()  { adb_run "su -c $1"; }

echo "== out: $OUT"

su_run '"/data/adb/modules/zygisk_lsposed/lspctl status"' >"$OUT/lspctl-status.txt" 2>&1 || true
grep -E 'LSPosed|API|Safe mode|System server' "$OUT/lspctl-status.txt" || echo "(lspctl unavailable)"

adb_run 'am force-stop com.android.settings' >/dev/null 2>&1 || true
adb_run 'am start -a android.settings.WIFI_SETTINGS' >/dev/null 2>&1
sleep 3
adb_run 'cmd wifi start-scan' >/dev/null 2>&1 || true
sleep "$SETTLE"

adb_run 'uiautomator dump /sdcard/ui-badge-check.xml' >/dev/null 2>&1 || true
adb_run 'screencap -p /sdcard/ui-badge-check.png' >/dev/null 2>&1
adb_run 'su -c "dmesg | grep -i -E \"avc.*(zn|lspd|lsposed)\" | tail -5"' >"$OUT/dmesg-avc.txt" 2>&1 || true

"$ADB" -P 5037 -s "$SERIAL" pull /sdcard/ui-badge-check.xml "$OUT/ui.xml" >/dev/null 2>&1 || true
"$ADB" -P 5037 -s "$SERIAL" pull /sdcard/ui-badge-check.png "$OUT/screenshot.png" >/dev/null 2>&1 || true

if [[ -f "$OUT/ui.xml" ]]; then
  echo "== rows on screen:"
  grep -o 'text="[^"]*"' "$OUT/ui.xml" | sed 's/text="//;s/"$//' | grep -E 'LabNet|CMCC|2345|DIRECT' | sort -u | sed 's/^/   /'
fi

echo
echo "== verdict (open the screenshot, do not use the dump):"
echo "   PASS  : a row for a 6GHz SSID shows its own 6G pill, and 5GHz rows use the"
echo "           module's 2.4G/5G/6G style instead of the OEM-only '5G' pill."
echo "   FAIL  : LabNet-6G row has no pill while LabNet-5G still shows the OEM '5G' pill."
echo "   file  : $OUT/screenshot.png"
