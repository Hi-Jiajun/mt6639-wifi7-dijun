#!/usr/bin/env python3
"""Summarize a read-only RTT/NAN snapshot without changing device state."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_FILE = 8 * 1024 * 1024
SERVICE_AWARE = re.compile(
    r"^\s*\d+\s+wifiaware: \[android\.net\.wifi\.aware\.IWifiAwareManager\]$",
    re.MULTILINE,
)
SERVICE_RTT = re.compile(r"^\s*\d+\s+wifirtt: \[", re.MULTILINE)


def read_bounded(path: Path) -> str:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE:
        raise ValueError(f"unsafe or missing file: {path.name}")
    return path.read_text(encoding="utf-8", errors="replace")


def query_value(path: Path) -> bool:
    return read_bounded(path).strip() == "true"


def summarize(directory: Path) -> dict:
    dumpsys = read_bounded(directory / "dumpsys-wifi.raw")
    features = set(
        line.removeprefix("feature:")
        for line in read_bounded(directory / "features").splitlines()
        if line.startswith("feature:")
    )
    supported_line = next(
        (line.removeprefix("SupportedFeatures: ").strip("[]")
         for line in dumpsys.splitlines()
         if line.startswith("SupportedFeatures: ")),
        "",
    )
    supported = {item.strip() for item in supported_line.split(",") if item.strip()}
    service = read_bounded(directory / "service-list")
    phy = read_bounded(directory / "iw-phy")
    aware_state = re.findall(r"^Connection state: .*Aware=([^\r\n,]+).*$",
                             dumpsys, re.MULTILINE)
    s11mc = re.findall(r"mIs11mcSupported=([^,]+)", dumpsys)
    s11az = re.findall(r"mIs11azSupported=([^,]+)", dumpsys)
    return {
        "schema": "mt6639-rtt-nan-report-v1",
        "aware_hal_feature": "WIFI_FEATURE_AWARE" in supported,
        "rtt_hal_feature": "WIFI_FEATURE_RTT" in supported,
        "rtt_11mc_hal_feature": "WIFI_FEATURE_11MC" in supported,
        "aware_binder": bool(SERVICE_AWARE.search(service)),
        "rtt_binder": bool(SERVICE_RTT.search(service)),
        "aware_framework_feature_enum": "android.hardware.wifi.aware" in features,
        "rtt_framework_feature_enum": "android.hardware.wifi.rtt" in features,
        "aware_single_feature_query": query_value(directory / "feature-aware.query"),
        "rtt_single_feature_query": query_value(directory / "feature-rtt.query"),
        "aware_connection_state": aware_state[-1].strip() if aware_state else "unknown",
        "connected_11mc_supported": s11mc[-1].strip() if s11mc else "unknown",
        "connected_11az_supported": s11az[-1].strip() if s11az else "unknown",
        "iw_phy_rtt_terms": bool(re.search(
            r"RTT|FTM|fine.?timing", phy, re.IGNORECASE)),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        result = summarize(Path(args.dir))
    except (OSError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        if path.exists():
            parser.error("--out must be a new path")
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
