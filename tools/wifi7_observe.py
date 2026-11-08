#!/usr/bin/env python3
"""Read-only Wi-Fi 7 capability and runtime snapshot.

The phone commands only query state. Optional AP files are supplied by the
operator, so this tool never changes AP configuration. Identifier strings are
hashed unless --reveal-identifiers is explicitly used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import decode_show_mld as show_mld


def _read(path):
    if path is None:
        return None
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _id(value, reveal):
    if value is None:
        return None
    if reveal:
        return value
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _int(text):
    match = re.search(r"-?\d+", text or "")
    return int(match.group()) if match else None


def parse_rate(line):
    if not line:
        return None
    mbps = re.search(r"([0-9.]+)\s+MBit/s", line)
    width = re.search(r"\b(20|40|80|160|320)MHz\b", line)
    mcs = re.search(r"\b(?:EHT|HE)-MCS\s+(\d+)\b", line)
    nss = re.search(r"\b(?:EHT|HE)-NSS\s+(\d+)\b", line)
    return {
        "mbps": float(mbps.group(1)) if mbps else None,
        "mcs": int(mcs.group(1)) if mcs else None,
        "nss": int(nss.group(1)) if nss else None,
        "width_mhz": int(width.group(1)) if width else None,
    }


def parse_iw_link(text, reveal=False):
    text = text or ""
    connected = re.search(r"^Connected to ([0-9a-f:]{17})", text, re.I | re.M)
    result = {
        "connected": bool(connected),
        "bssid": _id(connected.group(1).lower(), reveal) if connected else None,
        "ssid": None,
        "frequency_mhz": None,
        "signal_dbm": None,
        "rx": None,
        "tx": None,
    }
    ssid = re.search(r"^\s*SSID:\s*(.*)$", text, re.M)
    freq = re.search(r"^\s*freq:\s*(\d+)", text, re.M)
    signal = re.search(r"^\s*signal:\s*(-?\d+)", text, re.M)
    rx = re.search(r"^\s*rx bitrate:.*$", text, re.M)
    tx = re.search(r"^\s*tx bitrate:.*$", text, re.M)
    if ssid:
        result["ssid"] = _id(ssid.group(1), reveal)
    if freq:
        result["frequency_mhz"] = int(freq.group(1))
    if signal:
        result["signal_dbm"] = int(signal.group(1))
    result["rx"] = parse_rate(rx.group(0) if rx else "")
    result["tx"] = parse_rate(tx.group(0) if tx else "")
    return result


def parse_wifi_status(text, reveal=False):
    text = text or ""
    result = {
        "enabled": bool(re.search(r"^Wifi is enabled$", text, re.M)),
        "connected_ssid": None,
        "mac": None,
        "standard": None,
        "frequency_mhz": None,
        "rssi_dbm": None,
        "link_speed_mbps": None,
        "supplicant_completed": "Supplicant state: COMPLETED" in text,
    }
    ssid = re.search(r'Wifi is connected to "([^"]+)"', text)
    mac = re.search(
        r"^WifiInfo:.*?\bMAC:\s*([0-9a-f:]{17})",
        text,
        re.I | re.M,
    )
    standard = re.search(r"Wi-Fi standard:\s*([0-9a-z]+)", text, re.I)
    freq = re.search(r"Frequency:\s*(\d+)MHz", text)
    rssi = re.search(r"RSSI:\s*(-?\d+)", text)
    speed = re.search(r"Link speed:\s*([0-9.]+)Mbps", text)
    if ssid:
        result["connected_ssid"] = _id(ssid.group(1), reveal)
    if mac:
        result["mac"] = _id(mac.group(1).lower(), reveal)
    if standard:
        result["standard"] = standard.group(1)
    if freq:
        result["frequency_mhz"] = int(freq.group(1))
    if rssi:
        result["rssi_dbm"] = int(rssi.group(1))
    if speed:
        result["link_speed_mbps"] = float(speed.group(1))
    return result


def _feature_bytes(text):
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if "Supported extended features:" not in line:
            continue
        for candidate in lines[index + 1:index + 8]:
            match = re.search(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]", candidate)
            if match:
                return [int(value.strip()) for value in match.group(1).split(",")]
    return None


def _feature_names(text):
    lines = (text or "").splitlines()
    names = []
    active = False
    for line in lines:
        if "Supported extended features:" in line:
            active = True
            continue
        if active:
            match = re.match(r"^\s*\*\s*\[\s*([A-Z0-9_]+)\s*\]", line)
            if match:
                names.append(match.group(1))
            elif line.strip() and not line.lstrip().startswith("*"):
                active = False
    return names


def parse_eht_bands(text):
    """Preserve EHT PHY capabilities and MCS/NSS maps per band/iftype."""
    result = {}
    band = None
    iftype = None
    phy_active = False
    current_map = None
    for raw in (text or "").splitlines():
        band_match = re.match(r"^\s*Band\s+(\d+):\s*$", raw)
        if band_match:
            band = int(band_match.group(1))
            result.setdefault(band, {})
            iftype = None
            phy_active = False
            current_map = None
            continue
        eht_match = re.match(r"^\s*EHT Iftypes:\s*(AP|managed)\s*$", raw)
        if eht_match and band is not None:
            iftype = eht_match.group(1)
            result[band].setdefault(iftype, {
                "phy_capabilities": [],
                "phy_capabilities_hex": None,
                "mcs_maps": {},
            })
            phy_active = False
            current_map = None
            continue
        if band is None or iftype is None:
            continue
        item = result[band][iftype]
        phy_match = re.match(
            r"^\s*EHT PHY Capabilities \((0x[0-9a-fA-F]+)\):\s*$", raw)
        if phy_match:
            item["phy_capabilities_hex"] = phy_match.group(1).lower()
            phy_active = True
            current_map = None
            continue
        map_match = re.match(
            r"^\s*EHT-MCS Map \(BW (<= 80|= 160|= 320)\) "
            r"\((0x[0-9a-fA-F]+)\):\s*$", raw)
        if map_match:
            width = map_match.group(1)
            current_map = {
                "raw": map_match.group(2).lower(),
                "rx_tx_nss": {},
            }
            item["mcs_maps"][width] = current_map
            phy_active = False
            continue
        nss_match = re.match(
            r"^\s*(Rx|Tx) Max NSS for MCS (\d+-\d+):\s*(\d+)\s*$", raw)
        if current_map is not None and nss_match:
            direction = nss_match.group(1).lower()
            group = nss_match.group(2)
            current_map["rx_tx_nss"].setdefault(group, {})[direction] = int(nss_match.group(3))
            continue
        if phy_active:
            capability = raw.strip()
            if capability and not capability.startswith(("HE ", "VHT ", "HT ")):
                item["phy_capabilities"].append(capability)
            elif not capability:
                phy_active = False
        if re.match(r"^\s*(HE Iftypes:|Band\s+\d+:)", raw):
            iftype = None
            phy_active = False
            current_map = None
    return result


def parse_iw_phy(text):
    text = text or ""
    bands = re.findall(r"^\s*Band\s+(\d+):\s*$", text, re.M)
    freqs = [int(float(value))
             for value in re.findall(r"^\s*\*\s*(\d+(?:\.\d+)?) MHz", text, re.M)]
    features = _feature_bytes(text)
    feature_names = _feature_names(text)
    punct = None
    if features is not None:
        punct = len(features) > 7 and bool(features[7] & 0x40)
    elif feature_names:
        punct = any("PUNCT" in name for name in feature_names)
    return {
        "bands": [int(value) for value in bands],
        "frequency_count": len(freqs),
        "has_6ghz_frequency": any(5925 <= value <= 7125 for value in freqs),
        "eht_supported": "EHT Iftypes:" in text,
        "eht_6ghz_320_supported": "320MHz in 6GHz Supported" in text,
        "eht_by_band": parse_eht_bands(text),
        "extended_features": features,
        "extended_feature_names": feature_names,
        "puncturing_feature": punct,
    }


def parse_station_dump(text, reveal=False):
    stations = []
    chunks = re.split(r"(?=^Station\s+)", text or "", flags=re.M)
    for chunk in chunks:
        head = re.search(r"^Station\s+([0-9a-f:]{17})", chunk, re.I | re.M)
        if not head:
            continue
        rx_rate = re.search(r"^\s*rx bitrate:.*$", chunk, re.M)
        tx_rate = re.search(r"^\s*tx bitrate:.*$", chunk, re.M)
        field = {}
        for key in ("rx bytes", "tx bytes", "rx duration", "tx duration", "signal"):
            match = re.search(r"^\s*" + re.escape(key) + r":\s*(-?\d+)", chunk, re.M)
            field[key.replace(" ", "_")] = int(match.group(1)) if match else None
        stations.append({
            "station": _id(head.group(1).lower(), reveal),
            "fields": field,
            "rx_rate": parse_rate(rx_rate.group(0) if rx_rate else ""),
            "tx_rate": parse_rate(tx_rate.group(0) if tx_rate else ""),
        })
    return stations


def parse_tx_stats(text):
    result = {}
    for line in (text or "").splitlines():
        match = re.match(r"^\s*(Tx\s+multi[ -]user.*?):\s*(\d+)\s*$", line, re.I)
        if match:
            key = re.sub(r"[^a-z0-9]+", "_", match.group(1).lower()).strip("_")
            result[key] = int(match.group(2))
    return result


def parse_show_mld(text):
    try:
        return {
            "status": "decoded",
            **show_mld.decode(text, "phone-show-mld"),
        }
    except show_mld.DecodeError as error:
        return {
            "status": "invalid",
            "error": str(error),
            "constraints": {
                "read_only": True,
                "data_plane_proven": False,
            },
        }


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _run(adb, adb_port, serial, command, timeout, outdir, name):
    started = time.monotonic()
    try:
        adb_cmd = [adb]
        if adb_port is not None:
            adb_cmd.extend(["-P", str(adb_port)])
        adb_cmd.extend(["-s", serial, "shell", command])
        done = subprocess.run(
            adb_cmd,
            capture_output=True, text=True, errors="replace", timeout=timeout)
        rc, stdout, stderr = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        rc = None
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = "timeout"
    _write(outdir / "raw" / (name + ".out"), stdout)
    _write(outdir / "raw" / (name + ".err"), stderr)
    return {"rc": rc, "stdout": stdout, "stderr": stderr,
            "elapsed_s": round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--adb-port", type=int)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--ap-station-dump")
    parser.add_argument("--ap-tx-stats")
    parser.add_argument("--ap-phy")
    parser.add_argument("--reveal-identifiers", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 120:
        parser.error("--timeout must be 0..120 seconds")
    outdir = Path(args.out)
    if outdir.exists():
        parser.error("--out must be a new path")
    outdir.mkdir(mode=0o700, parents=False)
    (outdir / "raw").mkdir(mode=0o700)

    commands = {
        "boot_id": "cat /proc/sys/kernel/random/boot_id",
        "fingerprint": "getprop ro.build.fingerprint",
        "selinux": "getenforce",
        "iw_link": "iw dev wlan0 link",
        "iw_phy": "iw phy phy0 info",
        "show_mld": "/system/bin/timeout 8 /vendor/bin/iwpriv wlan0 driver 'show-mld 255'",
        "iw_version": "iw --version 2>&1",
        "iw_binary_sha256": "sha256sum /system/bin/iw",
        "wifi_status": "runcon u:r:shell:s0 sh -c 'cmd wifi status'",
    }
    runs = {name: _run(args.adb, args.adb_port, args.serial, command, args.timeout, outdir, name)
            for name, command in commands.items()}
    identifier = args.reveal_identifiers
    station = parse_station_dump(_read(args.ap_station_dump), identifier)
    tx_stats = parse_tx_stats(_read(args.ap_tx_stats))
    parsed_show_mld = parse_show_mld(runs["show_mld"]["stdout"])
    complete = all(value["rc"] == 0 for value in runs.values()) and \
        parsed_show_mld["status"] == "decoded"
    summary = {
        "schema": "mt6639-wifi7-observe-v1",
        "status": "complete" if complete else "incomplete",
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "identifiers_revealed": identifier,
        "phone": {
            "boot_id": runs["boot_id"]["stdout"].strip() or None,
            "fingerprint": runs["fingerprint"]["stdout"].strip() or None,
            "selinux": runs["selinux"]["stdout"].strip() or None,
            "iw_version": runs["iw_version"]["stdout"].strip() or None,
            "iw_binary_sha256": runs["iw_binary_sha256"]["stdout"].split()[0]
                if runs["iw_binary_sha256"]["stdout"].split() else None,
            "link": parse_iw_link(runs["iw_link"]["stdout"], identifier),
            "phy": parse_iw_phy(runs["iw_phy"]["stdout"]),
            "show_mld": parsed_show_mld,
            "wifi": parse_wifi_status(runs["wifi_status"]["stdout"], identifier),
        },
        "ap": {
            "phy": parse_iw_phy(_read(args.ap_phy)),
            "stations": station,
            "tx_stats": tx_stats,
            "station_count": len(station),
        },
        "commands": {name: {"rc": value["rc"], "elapsed_s": value["elapsed_s"]}
                     for name, value in runs.items()},
        "limitations": [
            "Capability declarations are not proof of data-plane use.",
            "Station rates are sampled driver state, not per-PPDU MCS.",
            "show-mld width is a host/driver operation field, not per-PPDU bandwidth.",
            "iw phy EHT MCS maps are retained verbatim; a zero map can conflict with the current associated rate and must remain visible.",
            "MU aggregate counters cannot distinguish OFDMA, MRU, or MU-MIMO.",
            "Missing AP files leave AP capability unknown; absence is not unsupported.",
        ],
    }
    summary["incomplete_commands"] = [
        name for name, value in runs.items() if value["rc"] != 0
    ]
    if parsed_show_mld["status"] != "decoded":
        summary["incomplete_commands"].append("show_mld_decode")
    _write(outdir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
