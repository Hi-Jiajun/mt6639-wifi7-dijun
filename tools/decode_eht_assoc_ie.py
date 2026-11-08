#!/usr/bin/env python3
"""Decode EHT Capabilities from an offline hostapd get_sta_ies JSON file."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

MAX_INPUT = 256 * 1024
MAX_ASSOC_IE = 8192


class DecodeError(Exception):
    pass


def token(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def decode_nss_map(raw: bytes, groups=("0-9", "10-11", "12-13")):
    if len(raw) != len(groups):
        raise DecodeError("EHT MCS map length does not match groups")
    return {
        group: {"rx": raw[index] & 0xf, "tx": raw[index] >> 4}
        for index, group in enumerate(groups)
    }


PHY_BITS = (
    ("support_320mhz_6ghz", 0, 1),
    ("support_242_tone_wider_than_20mhz", 0, 2),
    ("ndp_4x_eht_ltf_3_2us_gi", 0, 3),
    ("partial_bw_ul_mu_mimo", 0, 4),
    ("su_beamformer", 0, 5),
    ("su_beamformee", 0, 6),
    ("ng16_su_feedback", 0, 25),
    ("ng16_mu_feedback", 0, 26),
    ("codebook_4_2_su_feedback", 0, 27),
    ("codebook_7_5_mu_feedback", 0, 28),
    ("triggered_su_bf_feedback", 0, 29),
    ("triggered_mu_bf_partial_bw_feedback", 0, 30),
    ("triggered_cqi_feedback", 0, 31),
    ("partial_bw_dl_mu_mimo", 1, 0),
    ("psr_based_sr", 1, 1),
    ("power_boost_factor", 1, 2),
    ("eht_mu_ppdu_4x_eht_ltf_0_8us_gi", 1, 3),
    ("non_triggered_cqi_feedback", 1, 8),
    ("tx_1024_4096_qam_below_242_tone_ru", 1, 9),
    ("rx_1024_4096_qam_below_242_tone_ru", 1, 10),
    ("ppe_thresholds_present", 1, 11),
    ("eht_dup_6ghz", 1, 23),
    ("rx_ndp_wider_bandwidth_20mhz", 1, 24),
    ("non_ofdma_ul_mu_mimo_80mhz", 1, 25),
    ("non_ofdma_ul_mu_mimo_160mhz", 1, 26),
    ("non_ofdma_ul_mu_mimo_320mhz", 1, 27),
    ("mu_beamformer_80mhz", 1, 28),
    ("mu_beamformer_160mhz", 1, 29),
    ("mu_beamformer_320mhz", 1, 30),
    ("tb_sounding_feedback_rate_limit", 1, 31),
)
PHY_FIELDS = (
    ("beamformee_ss_80mhz", 0, 7, 0x7),
    ("beamformee_ss_160mhz", 0, 10, 0x7),
    ("beamformee_ss_320mhz", 0, 13, 0x7),
    ("sounding_dimensions_80mhz", 0, 16, 0x7),
    ("sounding_dimensions_160mhz", 0, 19, 0x7),
    ("sounding_dimensions_320mhz", 0, 22, 0x7),
    ("max_nc_minus_one", 1, 4, 0xf),
    ("common_nominal_packet_padding", 1, 12, 0x3),
    ("maximum_supported_eht_ltfs", 1, 14, 0x1f),
    ("mcs15_mru_support", 1, 19, 0xf),
)


def decode_phy_capabilities(raw: bytes):
    if len(raw) != 9:
        raise DecodeError("EHT PHY capabilities must contain 9 bytes")
    words = [int.from_bytes(raw[offset:offset + 4], "little")
             for offset in (0, 4)]
    booleans = {
        name: bool(words[word] & (1 << bit))
        for name, word, bit in PHY_BITS
    }
    fields = {
        name: (words[word] >> shift) & mask
        for name, word, shift, mask in PHY_FIELDS
    }
    return {
        "word1_hex": f"0x{words[0]:08x}",
        "word2_hex": f"0x{words[1]:08x}",
        "trailing_byte_hex": f"0x{raw[8]:02x}",
        "booleans": booleans,
        "fields": fields,
    }


def parse_ies(data: bytes):
    index = 0
    elements = []
    while index < len(data):
        if index + 2 > len(data):
            raise DecodeError(f"truncated element header at byte {index}")
        element_id, length = data[index], data[index + 1]
        if index + 2 + length > len(data):
            raise DecodeError(f"truncated element {element_id} at byte {index}")
        elements.append((element_id, data[index + 2:index + 2 + length]))
        index += 2 + length
    return elements


def decode_assoc_ie(raw: bytes):
    elements = parse_ies(raw)
    ssid = None
    eht_cap = None
    for element_id, payload in elements:
        if element_id == 0 and ssid is None:
            ssid = payload.decode("utf-8", errors="replace")
        if element_id == 255 and payload and payload[0] == 108:
            if eht_cap is not None:
                raise DecodeError("duplicate EHT Capabilities extension")
            eht_cap = payload[1:]
    if eht_cap is None:
        raise DecodeError("EHT Capabilities extension 108 missing")
    if len(eht_cap) < 11:
        raise DecodeError("EHT Capabilities shorter than MAC+PHY fields")
    mac_cap = eht_cap[:2]
    phy_cap = eht_cap[2:11]
    mcs_tail = eht_cap[11:]
    maps = {}
    if len(mcs_tail) == 4:
        maps["= 20"] = decode_nss_map(
            mcs_tail,
            ("0-7", "8-9", "10-11", "12-13"),
        )
    elif len(mcs_tail) == 9:
        for name, start in (("<= 80", 0), ("= 160", 3), ("= 320", 6)):
            maps[name] = decode_nss_map(mcs_tail[start:start + 3])
    else:
        raise DecodeError(f"unsupported EHT MCS tail length {len(mcs_tail)}")
    return {
        "schema": "mt6639-eht-assoc-ie-v1",
        "ssid_token": token(ssid) if ssid is not None else None,
        "element_count": len(elements),
        "eht_extension_id": 108,
        "eht_mac_capabilities_hex": mac_cap.hex(),
        "eht_phy_capabilities_hex": phy_cap.hex(),
        "eht_phy_decoded": decode_phy_capabilities(phy_cap),
        "mcs_nss_tail_hex": mcs_tail.hex(),
        "mcs_nss_maps": maps,
        "limitations": [
            "Association capability declarations do not prove per-PPDU MCS use.",
            "The AP-provided association IE is a peer observation, not a over-the-air frame capture.",
            "This decoder does not reinterpret nl80211 phy capability output.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, help="hostapd get_sta_ies JSON")
    parser.add_argument("--reveal-ssid", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.json).read_bytes()
        if len(raw) > MAX_INPUT:
            raise DecodeError("input too large")
        document = json.loads(raw.decode("utf-8"))
        value = document.get("assoc_ie")
        if not isinstance(value, str) or not value:
            raise DecodeError("assoc_ie missing")
        encoded = base64.b64decode(value, validate=True)
        if len(encoded) > MAX_ASSOC_IE:
            raise DecodeError("association IE too large")
        result = decode_assoc_ie(encoded)
        if args.reveal_ssid:
            for element_id, payload in parse_ies(encoded):
                if element_id == 0:
                    result["ssid"] = payload.decode("utf-8", errors="replace")
                    break
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DecodeError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
