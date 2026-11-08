#!/usr/bin/env python3
"""Read-only XR1710G/MT7996 puncturing capability preflight over SSH."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

REMOTE = r'''
set -eu
cat /etc/openwrt_release 2>/dev/null || true
echo __KERNEL__
uname -r
echo __PHY__
iw phy PHY_NAME info
echo __MODULE__
sha256sum "/lib/modules/$(uname -r)/mt7996e.ko"
echo __MODINFO__
if command -v modinfo >/dev/null 2>&1; then modinfo mt7996e 2>/dev/null || true; fi
echo __PROC_VERSION__
cat /proc/version
echo __MODULE_LOADED__
grep '^mt7996e ' /proc/modules || true
echo __FIRMWARE__
sha256sum /lib/firmware/mediatek/mt7996/mt7996_wm.bin
'''


def section(text: str, name: str) -> str:
    match = re.search(rf"__{re.escape(name)}__\n(.*?)(?=^__[A-Z_]+__$|\Z)",
                      text, re.S | re.M)
    return match.group(1).strip() if match else ""


def section_count(text: str, name: str) -> int:
    return len(re.findall(rf"^__{re.escape(name)}__\s*$", text, re.M))


def parse_release(text: str):
    values = dict(re.findall(r"^([A-Z_]+)='?([^'\n]*)'?$", text, re.M))
    return {
        "distribution": values.get("DISTRIB_ID"),
        "release": values.get("DISTRIB_RELEASE"),
        "revision": values.get("DISTRIB_REVISION"),
        "description": values.get("DISTRIB_DESCRIPTION"),
    }


def first_hash(text: str):
    match = re.search(r"^([0-9a-f]{64})\s+", text, re.M)
    return match.group(1) if match else None


def modinfo_fields(text: str):
    values = {}
    for line in text.splitlines():
        match = re.match(r"^(filename|depends|vermagic):\s*(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def target_band(text: str, band: int = 4):
    match = re.search(
        rf"^\s*Band\s+{band}:\s*$\n(.*?)(?=^\s*Band\s+\d+:\s*$|\Z)",
        text,
        re.S | re.M,
    )
    return match.group(1) if match else None


def extended_feature_names(phy: str):
    lines = phy.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "Supported extended features:":
            continue
        names = []
        for candidate in lines[index + 1:]:
            match = re.match(r"^\s*\*\s*\[\s*([A-Z0-9_]+)\s*\]", candidate)
            if match:
                names.append(match.group(1))
                continue
            if candidate.strip():
                break
        return names
    return None


def parse_output(text: str):
    phy = section(text, "PHY")
    release = parse_release(text.split("__KERNEL__", 1)[0])
    kernel = section(text, "KERNEL")
    modinfo = modinfo_fields(section(text, "MODINFO"))
    proc_version = section(text, "PROC_VERSION")
    module_loaded_text = section(text, "MODULE_LOADED")
    band = target_band(phy)
    feature_names = extended_feature_names(phy)
    reasons = []
    for name in ("KERNEL", "PHY", "MODULE", "MODINFO", "PROC_VERSION",
                 "MODULE_LOADED", "FIRMWARE"):
        if section_count(text, name) != 1:
            reasons.append(f"{name.lower()} section must appear exactly once")
    if not all(release.values()):
        reasons.append("release fields incomplete")
    if not kernel or "\n" in kernel:
        reasons.append("kernel value incomplete")
    if first_hash(section(text, "MODULE")) is None:
        reasons.append("module hash incomplete")
    if not modinfo.get("filename"):
        reasons.append("modinfo filename missing")
    if not modinfo.get("vermagic"):
        reasons.append("modinfo vermagic missing")
    elif kernel and modinfo["vermagic"].split()[0] != kernel:
        reasons.append("module vermagic/kernel mismatch")
    if not proc_version:
        reasons.append("kernel build string missing")
    module_loaded = bool(module_loaded_text)
    if not module_loaded:
        reasons.append("mt7996e is not loaded")
    if first_hash(section(text, "FIRMWARE")) is None:
        reasons.append("firmware hash incomplete")
    if band is None:
        reasons.append("Band 4 section missing")
        band = ""
    if "EHT Iftypes:" not in band:
        reasons.append("Band 4 EHT Iftypes missing")
    if "320MHz in 6GHz Supported" not in band:
        reasons.append("Band 4 6GHz 320MHz support missing")
    if feature_names is None:
        reasons.append("Supported extended features section missing")
    elif not feature_names:
        reasons.append("Supported extended features list empty")
    complete = not reasons
    punct = (any(name == "PUNCT" for name in feature_names)
             if feature_names is not None else None)
    return {
        "schema": "mt6639-ap-puncturing-preflight-v1",
        "status": "complete" if complete else "incomplete",
        "blocked_reasons": reasons,
        "target_band": 4,
        "release": release,
        "kernel": kernel or None,
        "kernel_build": proc_version or None,
        "module_filename": modinfo.get("filename"),
        "module_vermagic": modinfo.get("vermagic"),
        "module_depends": modinfo.get("depends"),
        "module_loaded": module_loaded,
        "module_sha256": first_hash(section(text, "MODULE")),
        "firmware_sha256": first_hash(section(text, "FIRMWARE")),
        "eht_supported": "EHT Iftypes:" in band if band else None,
        "six_ghz_320_supported": "320MHz in 6GHz Supported" in band if band else None,
        "puncturing_feature": punct,
        "extended_feature_count": len(feature_names) if feature_names is not None else None,
        "extended_features": feature_names if feature_names is not None else None,
        "remote_payload_read_only": True,
        "ssh_local_side_effects": "SSH user config and known_hosts may be read or updated",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="root@ap-address or SSH host alias")
    parser.add_argument("--phy", default="phy0")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    if args.host.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]+", args.host):
        print("FAIL invalid host", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.phy):
        print("FAIL invalid phy", file=sys.stderr)
        return 1
    if not (1 <= args.timeout <= 60):
        print("FAIL invalid timeout", file=sys.stderr)
        return 1
    remote = REMOTE.replace("PHY_NAME", args.phy)
    try:
        done = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             args.host, remote],
            capture_output=True, text=True, errors="replace",
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print("FAIL ssh timeout", file=sys.stderr)
        return 1
    if done.returncode != 0:
        print(f"FAIL ssh rc={done.returncode}: {done.stderr.strip()}", file=sys.stderr)
        return 1
    result = parse_output(done.stdout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
