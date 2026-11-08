#!/usr/bin/env python3
"""Decode MediaTek gen4m `show-mld` channel-width fields without wireless writes."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


WIDTH_CODES = {
    0: ("CW_20_40MHZ", None, "20 or 40 MHz; code 0 does not distinguish them"),
    1: ("CW_80MHZ", 80, "80 MHz"),
    2: ("CW_160MHZ", 160, "160 MHz"),
    3: ("CW_80P80MHZ", None, "80+80 MHz"),
    4: ("CW_320_1MHZ", 320, "320 MHz channelization 1"),
    5: ("CW_320_2MHZ", 320, "320 MHz channelization 2"),
}

S1_FOR_320 = {
    4: {31, 95, 159},
    5: {63, 127, 191},
}

BSS_HEADER = re.compile(
    r"^\s*BSS\[(?P<bss_index>\d+)\]\[RF_BAND=(?P<rf_band>\d+)\]"
    r"\[OMAC=(?P<omac>[^\]]+)\]\[LINK_ID=(?P<link_id>\d+)\]:\s*$"
)
ACTIVE = re.compile(
    r"^\s*ACTIVE/HW_BAND/OMAC_IDX:\s*"
    r"(?P<active>\d+)/(?P<hw_band>\d+)/(?P<omac_idx>\d+)\s*$"
)
CHANNEL = re.compile(
    r"^\s*CHANNEL/WIDTH/S1/S2:\s*"
    r"(?P<channel>\d+)/(?P<width>\d+)/(?P<s1>\d+)/(?P<s2>\d+)\s*$"
)


class DecodeError(Exception):
    pass


def decode(text: str, source_label: str = "stdin") -> dict:
    records: list[dict] = []
    current: dict | None = None

    for line in text.splitlines():
        header = BSS_HEADER.match(line)
        if header:
            current = {
                "bss_index": int(header.group("bss_index")),
                "rf_band": int(header.group("rf_band")),
                "omac": header.group("omac"),
                "link_id": int(header.group("link_id")),
            }
            records.append(current)
            continue

        if current is None:
            continue

        active = ACTIVE.match(line)
        if active:
            current["active"] = int(active.group("active"))
            current["hw_band"] = int(active.group("hw_band"))
            current["omac_index"] = int(active.group("omac_idx"))
            continue

        channel = CHANNEL.match(line)
        if channel:
            current["primary_channel"] = int(channel.group("channel"))
            current["width_code"] = int(channel.group("width"))
            current["center_s1"] = int(channel.group("s1"))
            current["center_s2"] = int(channel.group("s2"))

    if not records:
        raise DecodeError("NO_BSS_RECORD")

    normalized: list[dict] = []
    for record in records:
        label = f"BSS[{record['bss_index']}]"
        if "width_code" not in record:
            raise DecodeError(f"{label}:MISSING_CHANNEL_WIDTH")

        width_code = record["width_code"]
        if width_code not in WIDTH_CODES:
            raise DecodeError(f"{label}:UNKNOWN_WIDTH_CODE={width_code}")
        enum_name, nominal_mhz, width_note = WIDTH_CODES[width_code]
        expected_s1 = S1_FOR_320.get(width_code)
        s1_consistent = None
        if expected_s1 is not None:
            s1_consistent = record["center_s1"] in expected_s1

        normalized.append({
            **record,
            "width_enum": enum_name,
            "nominal_width_mhz": nominal_mhz,
            "width_note": width_note,
            "s1_consistent_with_width": s1_consistent,
        })

    return {
        "schema": "mt6639-show-mld-v1",
        "source": source_label,
        "record_count": len(normalized),
        "records": normalized,
        "constraints": {
            "read_only": True,
            "data_plane_proven": False,
            "note": (
                "These are host/driver operation fields. They do not prove the "
                "bandwidth or MCS used by any individual data PPDU."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="show-mld text file or - for stdin")
    parser.add_argument("--source-label", help="non-sensitive source label stored in the JSON")
    parser.add_argument("--output", help="write JSON atomically through a sibling temporary file")
    args = parser.parse_args(argv)

    try:
        if args.input == "-":
            text = sys.stdin.read()
            default_label = "stdin"
        else:
            path = Path(args.input)
            text = path.read_text(encoding="utf-8")
            default_label = path.name
        result = decode(text, args.source_label or default_label)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output)
            if not output.parent.is_dir():
                raise DecodeError("OUTPUT_PARENT_MISSING")
            temporary = output.with_name(output.name + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output)
        else:
            sys.stdout.write(rendered)
    except (DecodeError, OSError, UnicodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
