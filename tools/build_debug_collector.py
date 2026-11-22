#!/usr/bin/env python3
"""Build read-only mt6639-debug-collector KernelSU ZIP and assemble group test pack."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "release/mt6639-candidate-20261026.json"
DEFAULT_GUIDE = REPO_ROOT / "docs/群友自测安装与使用指南-dijun-violin-jinghu.md"
PRIMARY_IDS = ("mt6639-6g", "mt6639-bt", "mt6639-hotspot", "mt6639-ui-lsposed")
COLLECTOR_FILENAME = "mt6639-debug-collector-v0.1.1.zip"
FIXED_ZIP_TIME = (2026, 11, 22, 0, 0, 0)

MODULE_PROP = """id=mt6639-debug-collector
name=MT6639 Debug Log Collector (Read-Only)
version=v0.1.1
versionCode=2
author=mt6639-dijun
description=纯只读诊断与原厂哈希采集模块（无开机脚本、不改系统）。刷入完成或点击「操作」按钮即在 Download 目录生成脱敏日志 mt6639-debug-*.txt。
"""

# KernelSU sources customize.sh in its installer shell: never call `exit` here.
CUSTOMIZE_SH = """#!/system/bin/sh
MODPATH=${MODPATH:-${0%/*}}
chmod 0755 "$MODPATH/action.sh" "$MODPATH/bin/collect.sh" 2>/dev/null || true
sh "$MODPATH/bin/collect.sh" install || true
true
"""

ACTION_SH = """#!/system/bin/sh
MODDIR=${0%/*}
exec sh "$MODDIR/bin/collect.sh" action
"""

COLLECT_SH = r"""#!/system/bin/sh
# Pure read-only sanitized diagnostic collector for MT6639 testers.
# Never modifies /odm, /vendor, KernelSU config.conf, or private keys.
# Never prints raw SSID, passphrases, MAC addresses, IPv4 addresses, or serial numbers.
set -u
umask 022

TRIGGER=${1:-manual}
ADB_BASE=${MT6639_ADB_BASE:-/data/adb}
MISC_BASE=${MT6639_MISC_BASE:-/data/misc}
TMP_BASE=${MT6639_TMP_BASE:-/data/local/tmp}
ODM_CFG=${MT6639_ODM_CFG:-/odm/firmware/wifi.cfg}
MOUNTINFO_FILE=${MT6639_MOUNTINFO:-/proc/1/mountinfo}
SYSFS_ENFORCE_FILE=${MT6639_SYSFS_ENFORCE:-/sys/fs/selinux/enforce}
NET_CLASS_DIR=${MT6639_NET_CLASS:-/sys/class/net}
VENDOR_MOD_DIR=${MT6639_VENDOR_MOD_DIR:-/vendor/lib/modules}
DLKM_MOD_DIR=${MT6639_DLKM_MOD_DIR:-/vendor_dlkm/lib/modules}
BT_APEX_DIR=${MT6639_BT_APEX_DIR:-/apex/com.android.bt/app}
TETHER_JAR_FILE=${MT6639_TETHER_JAR:-/apex/com.android.tethering/javalib/framework-tethering.jar}

redact_stream() {
    sed -E \
        -e 's/([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}/<MAC>/g' \
        -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<IP4>/g' \
        -e 's/(ssid=)[^[:space:]]*/\1<REDACTED>/g' \
        -e 's/((passphrase|sae_password|wpa_passphrase|psk)=).*/\1<REDACTED>/g' \
        -e 's/(Wifi is connected to ")[^"]*(")/\1<REDACTED>\2/g' \
        -e 's/(Connected to ")[^"]*(")/\1<REDACTED>\2/g' \
        -e 's/(serial=)[^[:space:]]*/\1<SERIAL>/g'
}

file_sha() {
    if [ -f "$1" ] && [ ! -L "$1" ]; then
        sha256sum "$1" 2>/dev/null | cut -d ' ' -f 1
    else
        echo missing
    fi
}

resolve_ko() {
    p="$VENDOR_MOD_DIR/wlan_drv_gen4m_eap_6639.ko"
    if [ ! -e "$p" ] && [ ! -L "$p" ]; then
        p="$DLKM_MOD_DIR/wlan_drv_gen4m_eap_6639.ko"
    fi
    printf '%s\n' "$p"
}

resolve_bt_apk() {
    count=0
    found=""
    for c in "$BT_APEX_DIR"/*/Bluetooth.apk "$BT_APEX_DIR/Bluetooth.apk"; do
        [ -f "$c" ] && [ ! -L "$c" ] || continue
        found=$c
        count=$((count + 1))
    done
    [ "$count" = 1 ] && printf '%s\n' "$found" || printf '\n'
}

pick_out_dir() {
    if [ -n "${MT6639_DEBUG_OUT_DIR:-}" ]; then
        printf '%s\n' "$MT6639_DEBUG_OUT_DIR"
        return 0
    fi
    for d in /storage/emulated/0/Download /sdcard/Download /data/media/0/Download; do
        if [ -d "$d" ]; then
            probe="$d/.mt6639_probe_$$"
            if : > "$probe" 2>/dev/null; then
                rm -f "$probe" 2>/dev/null || true
                printf '%s\n' "$d"
                return 0
            fi
        fi
    done
    for d in /data/media/0/Download /data/local/tmp; do
        mkdir -p "$d" 2>/dev/null || true
        if [ -d "$d" ]; then
            printf '%s\n' "$d"
            return 0
        fi
    done
    printf '/data/local/tmp\n'
}

tail_redacted() {
    f=$1
    n=${2:-25}
    if [ -f "$f" ] && [ ! -L "$f" ]; then
        tail -n "$n" "$f" 2>/dev/null | redact_stream
    else
        echo "(absent: $f)"
    fi
}

device=$(getprop ro.product.device 2>/dev/null || echo unknown)
safe_dev=$(printf '%s' "$device" | tr -cd 'A-Za-z0-9_-')
[ -n "$safe_dev" ] || safe_dev=unknown
stamp=$(date +%Y%m%d-%H%M%S 2>/dev/null || date +%s 2>/dev/null || echo 0)
out_dir=$(pick_out_dir)
out_file="$out_dir/mt6639-debug-${safe_dev}-${stamp}.txt"

ko_path=$(resolve_ko)
cfg_sha=$(file_sha "$ODM_CFG")
ko_sha=$(file_sha "$ko_path")
bt_apk=$(resolve_bt_apk)
bt_sha=missing
[ -n "$bt_apk" ] && bt_sha=$(file_sha "$bt_apk")
jar_sha=$(file_sha "$TETHER_JAR_FILE")

wifi_raw=$(cmd wifi status 2>/dev/null || true)
wifi_on=0
case "$wifi_raw" in *"Wifi is enabled"*) wifi_on=1 ;; esac
wifi_conn=0
case "$wifi_raw" in *'Wifi is connected to "'*) wifi_conn=1 ;; esac
country=$(cmd wifi get-country-code 2>/dev/null | tail -n 1 || true)

band_info=$(iw phy 2>/dev/null | awk '
    $1 == "*" && $3 == "MHz" {
        mhz=$2
        sub(/\..*/, "", mhz)
        gsub(/[^0-9]/, "", mhz)
        if (mhz >= 5925 && mhz <= 7125) {
            total++
            if ($0 !~ /\(disabled\)/) enabled++
        }
    }
    END { printf "BAND4_TOTAL=%d\nBAND4_ENABLED=%d\n", total+0, enabled+0 }
' || printf 'BAND4_TOTAL=0\nBAND4_ENABLED=0\n')

boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)
baseband=$(getprop gsm.version.baseband 2>/dev/null || true)
has_bb=0
[ -n "$baseband" ] && has_bb=1
ksud_ver=missing
[ -x "$ADB_BASE/ksud" ] && ksud_ver=$("$ADB_BASE/ksud" --version 2>/dev/null | head -n 1 || echo present)
zyg_ok=0
if [ -x "$ADB_BASE/modules/zygisksu/bin/zygiskd" ] && timeout 4 "$ADB_BASE/modules/zygisksu/bin/zygiskd" status 2>/dev/null | grep -q 'Root:'; then
    zyg_ok=1
elif grep -q 'zygote' "$ADB_BASE/modules/zygisksu/module.prop" 2>/dev/null || { [ -x "$ADB_BASE/modules/zygisk_lsposed/lspctl" ] && timeout 4 "$ADB_BASE/modules/zygisk_lsposed/lspctl" status 2>/dev/null | grep -qx 'System server: connected'; }; then
    zyg_ok=1
fi
ui_ver=$(dumpsys package com.hiliang.mt6639ui 2>/dev/null | grep -m1 'versionName=' | tr -d '\r' | sed 's/^[[:space:]]*//' || true)
[ -n "$ui_ver" ] || ui_ver=not_installed

ap_key="$ADB_BASE/mt6639-hotspot/private/ap.key"
case "$ADB_BASE" in
    /data/adb) expected_key_uid=0 ;;
    *) expected_key_uid=$(stat -c %u "$ADB_BASE" 2>/dev/null || echo 0) ;;
esac
ap_key_ok=0
if [ -d "$ADB_BASE/mt6639-hotspot/private" ] && [ ! -L "$ADB_BASE/mt6639-hotspot/private" ] &&
   [ -f "$ap_key" ] && [ ! -L "$ap_key" ] &&
   [ "$(stat -c %u "$ap_key" 2>/dev/null || echo -1)" = "$expected_key_uid" ] &&
   [ "$(stat -c %a "$ap_key" 2>/dev/null || echo 0)" = 600 ]; then
    ap_key_ok=1
fi

latest_tx=""
for tx_cand in $(ls -1dt "$ADB_BASE/mt6639-6g/transactions/t-"* 2>/dev/null | head -n 1 || true); do
    latest_tx=$tx_cand
done

latest_ap=""
for ap_cand in $(ls -1dt "$TMP_BASE/mt6639-native-ap-"* 2>/dev/null | head -n 1 || true); do
    latest_ap=$ap_cand
done

{
    echo "# MT6639 Sanitized Debug Report (Privacy Redacted)"
    echo "=== BASIC_ENV ==="
    echo "TRIGGER=$TRIGGER"
    echo "UTC_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
    echo "DEVICE=$device"
    echo "MODEL=$(getprop ro.product.model 2>/dev/null || echo unknown)"
    echo "MI_OS=$(getprop ro.mi.os.version.name 2>/dev/null || echo unknown)"
    echo "BUILD_INC=$(getprop ro.build.version.incremental 2>/dev/null || echo unknown)"
    echo "SDK=$(getprop ro.build.version.sdk 2>/dev/null || echo unknown)"
    echo "KERNEL=$(uname -r 2>/dev/null || echo unknown)"
    echo "UID=$(id -u 2>/dev/null || echo unknown)"
    echo "CONTEXT=$(id -Z 2>/dev/null || echo unknown)"
    echo "SELINUX=$(getenforce 2>/dev/null || echo unknown)"
    echo "SYSFS_ENFORCE=$(cat "$SYSFS_ENFORCE_FILE" 2>/dev/null || echo unknown)"
    echo "HAS_CELLULAR_BASEBAND=$has_bb"
    echo "KSUD_VER=$ksud_ver"
    echo "ZYGISK_OK=$zyg_ok"
    echo "UI_APK=$ui_ver"
    echo "--- LSPosed status ---"
    if [ -x "$ADB_BASE/modules/zygisk_lsposed/lspctl" ]; then
        timeout 4 "$ADB_BASE/modules/zygisk_lsposed/lspctl" status 2>&1 | grep -E 'LSPosed|API|Safe mode|System server' || echo "(lspctl no match)"
    else
        echo "(lspctl missing)"
    fi

    echo
    echo "=== STOCK_COMPONENT_SHA256 ==="
    echo "WIFI_CFG_PATH=$ODM_CFG"
    echo "WIFI_CFG_SHA=$cfg_sha"
    echo "WLAN_KO_PATH=$ko_path"
    echo "WLAN_KO_SHA=$ko_sha"
    echo "BT_APK_PATH=${bt_apk:-missing}"
    echo "BT_APK_SHA=$bt_sha"
    echo "TETHER_JAR_PATH=$TETHER_JAR_FILE"
    echo "TETHER_JAR_SHA=$jar_sha"
    cfg_mount_cnt=$(grep -c ' /odm/firmware/wifi\.cfg ' "$MOUNTINFO_FILE" 2>/dev/null || true)
    echo "CFG_MOUNT_COUNT=${cfg_mount_cnt:-0}"

    echo
    echo "=== MODULE_STATES ==="
    for m in mt6639-6g mt6639-bt mt6639-hotspot; do
        mdir="$ADB_BASE/modules/$m"
        udir="$ADB_BASE/modules_update/$m"
        lv=$(sed -n 's/^version=//p' "$mdir/module.prop" 2>/dev/null | head -n 1)
        lvc=$(sed -n 's/^versionCode=//p' "$mdir/module.prop" 2>/dev/null | head -n 1)
        uv=$(sed -n 's/^version=//p' "$udir/module.prop" 2>/dev/null | head -n 1)
        dis=0; [ -e "$mdir/disable" ] && dis=1
        rem=0; [ -e "$mdir/remove" ] && rem=1
        sha_res=missing
        if [ "$m" = mt6639-6g ] && [ -f "$mdir/bin/verify-module.sh" ]; then
            sh "$mdir/bin/verify-module.sh" "$mdir" >/dev/null 2>&1 && sha_res=ok || sha_res=mismatch
        elif [ -f "$mdir/SHA256SUMS" ]; then
            (cd "$mdir" && sha256sum -c SHA256SUMS >/dev/null 2>&1) && sha_res=ok || sha_res=mismatch
        fi
        echo "MOD_${m}_LIVE=${lv:-absent}(${lvc:-0}) STAGED=${uv:-absent} DISABLE=$dis REMOVE=$rem SHA=$sha_res"
    done
    echo "SIXG_PROP_DESC=$(sed -n 's/^description=//p' "$ADB_BASE/modules/mt6639-6g/module.prop" 2>/dev/null | head -n 1 | redact_stream)"

    echo
    echo "=== WIFI_6G_STATE ==="
    echo "WIFI_ON=$wifi_on"
    echo "WIFI_CONNECTED=$wifi_conn"
    echo "COUNTRY=$(printf '%s' "$country" | redact_stream)"
    if [ -f "$ADB_BASE/mt6639-6g/country.conf" ] && [ ! -L "$ADB_BASE/mt6639-6g/country.conf" ]; then
        echo "TARGET_COUNTRY_CONF=$(tr -d '\r\n' < "$ADB_BASE/mt6639-6g/country.conf" 2>/dev/null | redact_stream)"
    else
        echo "TARGET_COUNTRY_CONF=default:auto"
    fi
    printf '%s\n' "$band_info"
    echo "SIXG_STATE=$(cat "$ADB_BASE/mt6639-6g/state" 2>/dev/null || echo absent)"
    active_tx=$(cat "$ADB_BASE/mt6639-6g/active_tx" 2>/dev/null || echo none)
    echo "SIXG_ACTIVE_TX=${active_tx##*/}"
    echo "--- mt6639-6g/apply.log (tail 30) ---"
    tail_redacted "$ADB_BASE/mt6639-6g/apply.log" 30
    if [ -n "$latest_tx" ]; then
        echo "--- latest tx (${latest_tx##*/}) guardian.log ---"
        tail_redacted "$latest_tx/guardian.log" 20
        echo "--- latest tx guardian-stderr.log ---"
        tail_redacted "$latest_tx/guardian-stderr.log" 20
    fi

    echo
    echo "=== BT_LE_AUDIO_STATE ==="
    echo "bluetooth_on=$(settings get global bluetooth_on 2>/dev/null || echo unknown)"
    echo "ro.bluetooth.leaudio_offload.supported=$(getprop ro.bluetooth.leaudio_offload.supported 2>/dev/null || true)"
    echo "persist.bluetooth.leaudio_offload.disabled=$(getprop persist.bluetooth.leaudio_offload.disabled 2>/dev/null || true)"
    echo "bt_module_boot_state=$(cat "$ADB_BASE/mt6639-bt/$boot/state" 2>/dev/null || echo absent)"
    for k in \
        bluetooth.profile.bap.unicast.client.enabled \
        bluetooth.profile.bap.broadcast.source.enabled \
        bluetooth.profile.bap.broadcast.assist.enabled \
        bluetooth.profile.csip.set_coordinator.enabled \
        bluetooth.profile.vcp.controller.enabled \
        bluetooth.profile.mcp.server.enabled; do
        echo "$k=$(getprop "$k" 2>/dev/null || true)"
    done

    echo
    echo "=== HOTSPOT_STATE ==="
    echo "ap_key_valid_0600=$ap_key_ok"
    echo "mtap0_present=$([ -e "$NET_CLASS_DIR/mtap0" ] && echo 1 || echo 0)"
    echo "hotspot_state_file=$(cat "$ADB_BASE/mt6639-hotspot/state" 2>/dev/null || echo absent)"
    echo "--- status-v4 snapshot ---"
    if [ -f "$MISC_BASE/mt6639-hotspot/status" ]; then
        redact_stream < "$MISC_BASE/mt6639-hotspot/status"
    else
        echo "(absent)"
    fi
    echo "--- dumpsys tethering --short (filtered) ---"
    timeout 4 dumpsys tethering --short 2>/dev/null | grep -E 'mtap0 - |Upstream wanted:' | redact_stream || echo "(unavailable)"
    echo "--- mt6639-hotspot/control.log (tail 25) ---"
    tail_redacted "$ADB_BASE/mt6639-hotspot/control.log" 25
    if [ -n "$latest_ap" ]; then
        echo "--- latest ap task (${latest_ap##*/}) ---"
        for rf in action.rc recovery.rc failure upstream.mode tether.scope; do
            [ -f "$latest_ap/$rf" ] && echo "$rf=$(tr '\n' ' ' < "$latest_ap/$rf" | redact_stream)"
        done
        if [ -s "$latest_ap/status.active" ]; then
            grep -E '^(state|freq|channel|ieee80211|eht_oper_chwidth)=' "$latest_ap/status.active" || true
        fi
        tail_redacted "$latest_ap/session.log" 25
    fi

    echo
    echo "=== AVC_SUMMARY ==="
    dmesg 2>/dev/null | grep -iE 'avc:.*(mt6639|hostapd|zn|lspd|lsposed)' | tail -n 20 | redact_stream || echo "(dmesg unavailable)"
} > "$out_file" 2>&1

if [ -z "${MT6639_DEBUG_OUT_DIR:-}" ]; then
    chown 1023:1023 "$out_file" 2>/dev/null || true
    chmod 0664 "$out_file" 2>/dev/null || true
fi

echo "----------------------------------------"
echo "MT6639 脱敏诊断日志已生成："
echo "  $out_file"
echo "请在「文件管理 -> Download(下载)」中找到此文件并发送排查。"
echo "关键摘要：DEVICE=$device MI_OS=$(getprop ro.mi.os.version.name 2>/dev/null || echo unknown) SELINUX=$(getenforce 2>/dev/null || echo unknown)"
echo "$band_info"
echo "----------------------------------------"
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_collector_zip(out_zip: Path) -> str:
    entries: list[tuple[str, bytes, int]] = [
        ("module.prop", MODULE_PROP.encode("utf-8"), 0o100644),
        ("customize.sh", CUSTOMIZE_SH.encode("utf-8"), 0o100755),
        ("action.sh", ACTION_SH.encode("utf-8"), 0o100755),
        ("bin/collect.sh", COLLECT_SH.encode("utf-8"), 0o100755),
    ]
    sums = "\n".join(f"{sha256_bytes(data)}  {name}" for name, data, _ in entries) + "\n"
    entries.append(("SHA256SUMS", sums.encode("ascii"), 0o100644))
    entries.sort(key=lambda item: item[0])

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data, mode in entries:
            zi = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            zi.create_system = 3  # UNIX permissions preserved for unzip/ksud
            zi.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(zi, data)
    return sha256_file(out_zip)


def build_group_pack(manifest_path: Path, pack_dir: Path, repo: Path, guide_path: Path) -> dict:
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, mode=0o755)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in manifest["artifacts"]}

    sums_lines = []
    packaged = []
    for aid in PRIMARY_IDS:
        art = by_id[aid]
        src = repo / art["path"]
        if not src.is_file() or src.is_symlink():
            raise SystemExit(f"ERROR: missing primary artifact {src}")
        actual = sha256_file(src)
        if actual != art["sha256"]:
            raise SystemExit(f"ERROR: sha256 mismatch for {src}")
        dst = pack_dir / src.name
        shutil.copyfile(src, dst)
        dst.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        sums_lines.append(f"{actual}  {src.name}")
        packaged.append({"id": aid, "file": src.name, "sha256": actual, "version": art["version"]})

    collector_zip = pack_dir / COLLECTOR_FILENAME
    collector_sha = build_collector_zip(collector_zip)
    collector_zip.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    sums_lines.append(f"{collector_sha}  {COLLECTOR_FILENAME}")
    packaged.append({
        "id": "mt6639-debug-collector",
        "file": COLLECTOR_FILENAME,
        "sha256": collector_sha,
        "version": "0.1.1",
    })

    guide_copied = False
    if guide_path.is_file() and not guide_path.is_symlink():
        guide_dst = pack_dir / "安装与测试指南.md"
        shutil.copyfile(guide_path, guide_dst)
        guide_dst.chmod(0o644)
        guide_copied = True

    (pack_dir / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="ascii", newline="\n")
    (pack_dir / "SHA256SUMS").chmod(0o644)
    return {
        "schema": "mt6639-group-test-pack-v1",
        "pack_dir": str(pack_dir),
        "guide_included": guide_copied,
        "packages": packaged,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--pack-dir", default=str(REPO_ROOT / "outputs/mt6639-group-test-pack"))
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--guide", default=str(DEFAULT_GUIDE))
    parser.add_argument("--zip-only", default=None)
    args = parser.parse_args(argv)
    if args.zip_only is not None:
        zip_path = Path(args.zip_only).resolve()
        sha = build_collector_zip(zip_path)
        print(json.dumps({
            "schema": "mt6639-debug-collector-zip-v1",
            "zip": str(zip_path),
            "sha256": sha,
        }, ensure_ascii=False, indent=2))
        return 0
    summary = build_group_pack(
        Path(args.manifest).resolve(),
        Path(args.pack_dir).resolve(),
        Path(args.repo).resolve(),
        Path(args.guide).resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
