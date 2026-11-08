#!/usr/bin/env python3
"""Compare AP-observed association EHT maps with phone nl80211 phy maps."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from decode_eht_assoc_ie import DecodeError, decode_nss_map, decode_phy_capabilities

MAX_INPUT = 1024 * 1024
MAX_WINDOW_S = 300
MCS_GROUPS = ("0-9", "10-11", "12-13")
WIDTHS = ("<= 80", "= 160", "= 320", "= 20")


class CompareError(Exception):
    pass


def token(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def read_json(path: Path):
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT:
        raise CompareError(f"{path.name}: input too large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise CompareError(f"{path.name}: root must be an object")
    return value


def normalize_map(value, source: str):
    if not isinstance(value, dict):
        raise CompareError(f"{source}: map must be an object")
    result = {}
    for width in WIDTHS:
        if width not in value:
            continue
        groups = value[width]
        if not isinstance(groups, dict):
            raise CompareError(f"{source}: {width} groups invalid")
        result[width] = {}
        for group in MCS_GROUPS:
            item = groups.get(group)
            if not isinstance(item, dict):
                raise CompareError(f"{source}: {width}/{group} missing")
            rx, tx = item.get("rx"), item.get("tx")
            if isinstance(rx, bool) or not isinstance(rx, int) or rx < 0:
                raise CompareError(f"{source}: {width}/{group} rx invalid")
            if isinstance(tx, bool) or not isinstance(tx, int) or tx < 0:
                raise CompareError(f"{source}: {width}/{group} tx invalid")
            result[width][group] = {"rx": rx, "tx": tx}
    if not result:
        raise CompareError(f"{source}: no supported width maps")
    return result


def parse_time(value):
    if not isinstance(value, str):
        raise CompareError("timestamp missing")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CompareError("timestamp invalid") from error
    if parsed.tzinfo is None:
        raise CompareError("timestamp must include timezone")
    return parsed


def validate_derived_fields(ap: dict, phone_phy_item: dict):
    phy_hex = ap.get("eht_phy_capabilities_hex")
    if not isinstance(phy_hex, str):
        raise CompareError("AP EHT PHY capabilities missing")
    try:
        ap_phy = decode_phy_capabilities(bytes.fromhex(phy_hex))
    except (DecodeError, ValueError) as error:
        raise CompareError("AP EHT PHY capabilities invalid") from error
    claimed_phy = ap.get("eht_phy_decoded")
    if isinstance(claimed_phy, dict) and claimed_phy != ap_phy:
        raise CompareError("AP decoded PHY capabilities disagree with raw hex")
    tail_hex = ap.get("mcs_nss_tail_hex")
    if isinstance(tail_hex, str):
        try:
            tail = bytes.fromhex(tail_hex)
            if len(tail) == 4:
                expected = {"= 20": decode_nss_map(
                    tail, ("0-7", "8-9", "10-11", "12-13"))}
            elif len(tail) == 9:
                expected = {
                    "<= 80": decode_nss_map(tail[0:3]),
                    "= 160": decode_nss_map(tail[3:6]),
                    "= 320": decode_nss_map(tail[6:9]),
                }
            else:
                raise CompareError("AP EHT MCS tail length unsupported")
        except (DecodeError, ValueError) as error:
            raise CompareError("AP EHT MCS tail invalid") from error
        if normalize_map(ap.get("mcs_nss_maps"), "ap.mcs_nss_maps") != expected:
            raise CompareError("AP MCS maps disagree with raw tail")
    phone_phy_hex = phone_phy_item.get("phy_capabilities_hex")
    if not isinstance(phone_phy_hex, str) or not phone_phy_hex.startswith("0x"):
        raise CompareError("phone EHT PHY capabilities missing")
    try:
        phone_phy = decode_phy_capabilities(bytes.fromhex(phone_phy_hex[2:]))
    except (DecodeError, ValueError) as error:
        raise CompareError("phone EHT PHY capabilities invalid") from error
    for width, item in phone_phy_item.get("mcs_maps", {}).items():
        raw = item.get("raw") if isinstance(item, dict) else None
        groups = item.get("rx_tx_nss") if isinstance(item, dict) else None
        if not isinstance(raw, str) or not raw.startswith("0x") or not isinstance(groups, dict):
            continue
        try:
            expected = decode_nss_map(bytes.fromhex(raw[2:]))
        except (DecodeError, ValueError) as error:
            raise CompareError(f"phone {width} MCS map invalid") from error
        if groups != expected:
            raise CompareError(f"phone {width} MCS map disagrees with raw hex")
    return ap_phy, phone_phy


def linkage(ap: dict, phone: dict, max_window_s: int):
    reasons = []
    phone_body = phone.get("phone")
    if not isinstance(phone_body, dict):
        return {"reasons": ["phone body missing"]}, phone_body
    wifi = phone_body.get("wifi")
    link = phone_body.get("link")
    if not isinstance(wifi, dict) or not isinstance(link, dict):
        return {"reasons": ["phone wifi/link missing"]}, phone_body
    if phone.get("status") != "complete":
        reasons.append("phone status is not complete")
    ap_station = ap.get("station_token")
    phone_station = wifi.get("mac")
    ap_ssid = ap.get("ssid_token")
    phone_ssid = wifi.get("connected_ssid")
    ap_bssid = ap.get("bssid_token")
    phone_bssid = link.get("bssid")
    ap_freq = ap.get("frequency_mhz")
    phone_wifi_freq = wifi.get("frequency_mhz")
    phone_link_freq = link.get("frequency_mhz")
    if not isinstance(ap.get("host_boot_id"), str) or not ap.get("host_boot_id"):
        reasons.append("AP boot id missing")
    if not isinstance(phone_body.get("boot_id"), str) or not phone_body.get("boot_id"):
        reasons.append("phone boot id missing")
    if not isinstance(ap_station, str) or not isinstance(phone_station, str):
        reasons.append("station identity missing")
    elif ap_station != phone_station:
        reasons.append("station identity mismatch")
    if not isinstance(ap_ssid, str) or not isinstance(phone_ssid, str):
        reasons.append("SSID identity missing")
    elif ap_ssid != phone_ssid:
        reasons.append("SSID identity mismatch")
    association = ap.get("association")
    if isinstance(association, dict) and association.get("ssid_token") != ap_ssid:
        reasons.append("AP association SSID token mismatch")
    if not isinstance(ap_bssid, str) or not isinstance(phone_bssid, str):
        reasons.append("BSSID identity missing")
    elif ap_bssid != phone_bssid:
        reasons.append("BSSID identity mismatch")
    freqs = [ap_freq, phone_wifi_freq, phone_link_freq]
    if any(not isinstance(value, int) for value in freqs):
        reasons.append("frequency linkage missing")
    elif len(set(freqs)) != 1:
        reasons.append("frequency mismatch")
    ap_time = phone_time = None
    try:
        ap_time = parse_time(ap.get("collected_at"))
        phone_time = parse_time(phone.get("collected_at"))
    except CompareError as error:
        reasons.append(str(error))
    window = None
    if ap_time is not None and phone_time is not None:
        window = abs((ap_time - phone_time).total_seconds())
        if window > max_window_s:
            reasons.append(f"collection window exceeds {max_window_s}s")
    return {
        "reasons": reasons,
        "station_token": ap_station,
        "ssid_token": ap_ssid,
        "bssid_token": ap_bssid,
        "frequency_mhz": ap_freq,
        "ap_collected_at": ap.get("collected_at"),
        "phone_collected_at": phone.get("collected_at"),
        "window_s": window,
    }, phone_body


def compare(ap: dict, phone: dict, band: int, iftype: str, max_window_s: int = MAX_WINDOW_S):
    envelope = ap
    if ap.get("schema") == "mt6639-ap-station-ie-recheck-v1":
        nested = ap.get("association")
        if not isinstance(nested, dict):
            raise CompareError("AP recheck association missing")
        ap = nested
    if ap.get("schema") != "mt6639-eht-assoc-ie-v1":
        raise CompareError("AP IE schema unsupported")
    if phone.get("schema") != "mt6639-wifi7-observe-v1":
        raise CompareError("phone summary schema unsupported")
    ap_maps = normalize_map(ap.get("mcs_nss_maps"), "ap.mcs_nss_maps")
    eht_bands = phone.get("phone", {}).get("phy", {}).get("eht_by_band", {})
    if not isinstance(eht_bands, dict):
        raise CompareError("phone eht_by_band missing")
    band_item = eht_bands.get(str(band))
    if not isinstance(band_item, dict):
        raise CompareError(f"phone band {band} missing")
    iftype_item = band_item.get(iftype)
    if not isinstance(iftype_item, dict):
        raise CompareError(f"phone {iftype} iftype missing")
    phy_maps = iftype_item.get("mcs_maps")
    if not isinstance(phy_maps, dict):
        raise CompareError("phone mcs_maps missing")
    ap_phy, phone_phy = validate_derived_fields(ap, iftype_item)
    normalized_phy = {}
    for width, item in phy_maps.items():
        if width not in WIDTHS or not isinstance(item, dict):
            continue
        groups = item.get("rx_tx_nss")
        if not isinstance(groups, dict):
            continue
        normalized_phy[width] = groups
    widths = sorted(set(ap_maps) | set(normalized_phy))
    differences = []
    compared = 0
    for width in widths:
        ap_width = ap_maps.get(width)
        phy_width = normalized_phy.get(width)
        if ap_width is None or phy_width is None:
            differences.append({
                "width": width,
                "kind": "map_missing",
                "ap_present": ap_width is not None,
                "phy_present": phy_width is not None,
            })
            continue
        for group in MCS_GROUPS:
            compared += 1
            left = ap_width[group]
            right = phy_width.get(group)
            if left != right:
                differences.append({
                    "width": width,
                    "mcs": group,
                    "kind": "nss_mismatch",
                    "association": left,
                    "nl80211": right,
                })
    ap_booleans = ap_phy["booleans"]
    phone_booleans = phone_phy["booleans"]
    association_only = sorted(
        name for name, value in ap_booleans.items() if value and not phone_booleans.get(name))
    nl80211_only = sorted(
        name for name, value in phone_booleans.items() if value and not ap_booleans.get(name))
    scalar_mismatch = {
        name: {"association": value, "nl80211": phone_phy["fields"].get(name)}
        for name, value in ap_phy["fields"].items()
        if value != phone_phy["fields"].get(name)
    }
    phy_differences = {
        "association_only_set": association_only,
        "nl80211_only_set": nl80211_only,
        "scalar_mismatch": scalar_mismatch,
    }
    has_phy_mismatch = bool(association_only or nl80211_only or scalar_mismatch)
    wifi = phone.get("phone", {}).get("wifi", {})
    link, _ = linkage(envelope, phone, max_window_s)
    reasons = link["reasons"]
    return {
        "schema": "mt6639-eht-source-compare-v1",
        "result": "uncomparable" if reasons else (
            "consistent" if not differences and not has_phy_mismatch
            else "source_mismatch"),
        "band": band,
        "iftype": iftype,
        "groups_compared": compared,
        "differences": differences,
        "phy_differences": phy_differences,
        "association_ssid_token": ap.get("ssid_token"),
        "linkage": link,
        "uncomparable_reasons": reasons,
        "runtime": {
            "standard": wifi.get("standard"),
            "frequency_mhz": wifi.get("frequency_mhz"),
            "supplicant_completed": wifi.get("supplicant_completed"),
            "link_speed_mbps": wifi.get("link_speed_mbps"),
        },
        "limitations": [
            "A source mismatch is not proof that either source lies about the current PPDU.",
            "Association capability maps do not prove MCS12/13 were selected for data frames.",
            "The comparison does not infer OFDMA, MRU, MU-MIMO, or puncturing usage.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ap-ie-json", required=True)
    parser.add_argument("--phone-summary-json", required=True)
    parser.add_argument("--band", required=True, type=int)
    parser.add_argument("--iftype", choices=("AP", "managed"), default="managed")
    parser.add_argument("--max-window-s", type=int, default=MAX_WINDOW_S)
    args = parser.parse_args(argv)
    if not (1 <= args.max_window_s <= 3600):
        parser.error("--max-window-s must be 1..3600")
    try:
        result = compare(
            read_json(Path(args.ap_ie_json)),
            read_json(Path(args.phone_summary_json)),
            args.band,
            args.iftype,
            args.max_window_s,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CompareError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
