#!/usr/bin/env python3
"""Collect one puncturing A/B sample from the phone (STA side).

One run = one condition ("puncturing on" or "puncturing off"). The caller runs it
twice per AP state and compares the two summaries; the tool never decides whether
puncturing works, it only records what the phone can observe.

Usage:
  python3 tools/punct_ab_probe.py --label off --bssid 02:00:00:00:77:7c \
      --psk-file /path/to/psk-file --out outputs/punct-ab-<stamp>/off

  # read-only smoke test on the current link, no association change:
  python3 tools/punct_ab_probe.py --label smoke --no-connect --skip-iperf \
      --out /tmp/punct-ab-smoke
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ADB = "outputs/linux-adb-20261026/extracted/platform-tools/adb"
DEFAULT_SERIAL = "YOUR_SERIAL"


class ProbeError(Exception):
    pass


def run(cmd: list[str], timeout: int = 60, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise ProbeError(f"command failed rc={proc.returncode}: {' '.join(cmd)}\n{proc.stderr[-400:]}")
    return proc.stdout


def su(adb: list[str], script: str, timeout: int = 60, check: bool = True) -> str:
    # `ping` exits 1 when any packet is lost and `iperf3` exits non-zero on a
    # failed run, so both are collected with check=False and judged by parsing.
    return run(adb + ["shell", "su -c " + json.dumps(script)], timeout=timeout, check=check)


def parse_link(text: str) -> dict:
    out: dict = {"connected": False}
    m = re.search(r"Connected to ([0-9a-f:]{17})", text)
    if not m:
        out["raw"] = text.strip()[:400]
        return out
    out["connected"] = True
    out["bssid"] = m.group(1)
    for key, pattern in (
        ("freq_mhz", r"freq:\s*([0-9.]+)"),
        ("signal_dbm", r"signal:\s*(-?[0-9.]+)"),
        ("rx_bitrate", r"rx bitrate:\s*([0-9.]+)"),
        ("tx_bitrate", r"tx bitrate:\s*([0-9.]+)"),
    ):
        hit = re.search(pattern, text)
        if hit:
            out[key] = float(hit.group(1))
    width = re.search(r"([0-9]+)MHz", text)
    if width:
        out["width_mhz"] = int(width.group(1))
    ssid = re.search(r"SSID:\s*(.+)", text)
    if ssid:
        out["ssid"] = ssid.group(1).strip()
    return out


def parse_eht_operation(scan_text: str, bssid: str) -> dict:
    """Return the EHT Operation fields the phone can see for one BSS."""
    block = ""
    heads = list(re.finditer(r"^BSS ([0-9a-f:]{17})", scan_text, re.M))
    for index, head in enumerate(heads):
        if head.group(1) != bssid:
            continue
        end = heads[index + 1].start() if index + 1 < len(heads) else len(scan_text)
        block = scan_text[head.start():end]
        break
    if not block:
        return {"found": False}
    # An EHT BSS also carries an HE Operation block with its own "Channel Width"
    # and CCFS lines, so scope the parse to the EHT Operation section.
    section = re.search(r"EHT Operation:.*?(?=\n\t[A-Z][A-Za-z ]+:|\Z)", block, re.S)
    section_text = section.group(0) if section else block
    params = re.search(r"EHT Operation Parameters:\s*\(0x([0-9a-fA-F]+)\)", section_text)
    bitmap = re.search(r"Disabled Subchannel Bitmap:\s*0x([0-9a-fA-F]+)", section_text)
    width = re.search(r"Channel Width:\s*(.+)", section_text)
    ccfs0 = re.search(r"Center Frequency Segment 0:\s*(\d+)", section_text)
    signal = re.search(r"signal:\s*(-?[0-9.]+)", block)
    value = int(params.group(1), 16) if params else None
    return {
        "found": True,
        "eht_params": value,
        "bitmap_present": bool(value is not None and value & 0x02),
        "disabled_subchannel_bitmap": bitmap.group(1) if bitmap else None,
        "channel_width": width.group(1).strip() if width else None,
        "ccfs0": int(ccfs0.group(1)) if ccfs0 else None,
        "scan_signal_dbm": float(signal.group(1)) if signal else None,
    }


def parse_ping(text: str) -> dict:
    out = {"raw_tail": text.strip().splitlines()[-1] if text.strip() else ""}
    loss = re.search(r"([0-9.]+)% packet loss", text)
    if loss:
        out["loss_pct"] = float(loss.group(1))
    dup = re.search(r"\+(\d+) duplicates", text)
    out["duplicates"] = int(dup.group(1)) if dup else 0
    rtt = re.search(r"rtt min/avg/max/mdev = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)", text)
    if rtt:
        out["rtt_ms"] = {"min": float(rtt.group(1)), "avg": float(rtt.group(2)), "max": float(rtt.group(3))}
    return out


def parse_iperf(payload: str, reverse: bool) -> dict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"error": "non-json", "raw": payload.strip()[:300]}
    if data.get("error"):
        return {"error": data["error"]}
    end = data.get("end", {})
    key = "sum_received" if reverse else "sum_sent"
    block = end.get(key) or end.get("sum") or {}
    return {
        "gbps": round(block.get("bits_per_second", 0) / 1e9, 4),
        "retransmits": block.get("retransmits"),
        "intervals_gbps": [round(i["sum"]["bits_per_second"] / 1e9, 3) for i in data.get("intervals", [])],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="collect one puncturing A/B sample from the phone")
    parser.add_argument("--label", required=True, help="condition label, e.g. on/off")
    parser.add_argument("--out", required=True, help="output directory for this sample")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--bssid", default="02:00:00:00:77:7c")
    parser.add_argument("--ssid", default="LabNet-6G")
    parser.add_argument("--security", default="wpa3", choices=["open", "owe", "wpa2", "wpa3", "wep"])
    parser.add_argument("--psk-file", default=os.environ.get("PUNCT_AB_PSK_FILE", ""))
    parser.add_argument("--psk", default=os.environ.get("PUNCT_AB_PSK", ""))
    parser.add_argument("--server", default="198.51.100.60")
    parser.add_argument("--port", type=int, default=5201)
    parser.add_argument("--streams", type=int, default=4)
    parser.add_argument("--seconds", type=int, default=6)
    parser.add_argument("--connect-timeout", type=int, default=20)
    parser.add_argument("--no-connect", action="store_true", help="keep the current link; read-only")
    parser.add_argument("--skip-iperf", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_connect and not (args.psk or args.psk_file):
        parser.error("--psk-file/--psk required unless --no-connect is used")

    psk = args.psk
    if not psk and args.psk_file:
        psk = Path(args.psk_file).expanduser().read_text(encoding="utf-8").strip()

    adb = [args.adb, "-P", "5037", "-s", args.serial]
    out_dir = Path(args.out)
    connect_cmd = [
        "cmd", "wifi", "connect-network", args.ssid, args.security, psk or "",
        "-b", args.bssid,
    ]
    if args.dry_run:
        print("dry-run connect:", " ".join(connect_cmd[:-3] + ["<psk>", "-b", args.bssid]))
        print("dry-run out:", out_dir)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"label": args.label, "bssid": args.bssid, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    if not args.no_connect:
        run(adb + ["shell", " ".join(f"'{p}'" if p == psk else p for p in connect_cmd)], timeout=45)
        deadline = time.time() + args.connect_timeout
        link = ""
        while time.time() < deadline:
            link = su(adb, "iw dev wlan0 link")
            if f"Connected to {args.bssid}".lower() in link.lower():
                break
            time.sleep(2)
        # Both APs share one SSID, so once the framework is associated to the
        # other BSS it treats connect-network as a no-op instead of a re-pin.
        # A radio toggle makes it re-elect the strongest BSS of that SSID.
        if f"connected to {args.bssid}".lower() not in link.lower():
            run(adb + ["shell", "svc wifi disable"], timeout=30)
            time.sleep(3)
            run(adb + ["shell", "svc wifi enable"], timeout=30)
            deadline = time.time() + args.connect_timeout
            while time.time() < deadline:
                link = su(adb, "iw dev wlan0 link")
                if f"Connected to {args.bssid}".lower() in link.lower():
                    break
                time.sleep(2)
        summary["linked_to_target"] = f"Connected to {args.bssid}".lower() in link.lower()
    else:
        summary["linked_to_target"] = None

    dmesg_before = len(su(adb, "dmesg").splitlines())

    link_text = su(adb, "iw dev wlan0 link")
    (out_dir / "link.txt").write_text(link_text, encoding="utf-8")
    summary["link"] = parse_link(link_text)

    station = su(adb, "iw dev wlan0 station dump")
    (out_dir / "station-dump.txt").write_text(station, encoding="utf-8")

    scan = su(adb, "iw dev wlan0 scan", timeout=90)
    (out_dir / "scan.txt").write_text(scan, encoding="utf-8")
    summary["eht_operation"] = parse_eht_operation(scan, args.bssid)

    ping_text = su(adb, f"ping -c 20 -i 0.2 {args.server}", check=False)
    (out_dir / "ping.txt").write_text(ping_text, encoding="utf-8")
    summary["ping"] = parse_ping(ping_text)

    if not args.skip_iperf:
        base = f"/system/bin/iperf3 -c {args.server} -p {args.port} -t {args.seconds} -P {args.streams}"
        down = su(adb, base + " -R -J", timeout=args.seconds + 40, check=False)
        up = su(adb, base + " -J", timeout=args.seconds + 40, check=False)
        (out_dir / "iperf3-downlink.json").write_text(down, encoding="utf-8")
        (out_dir / "iperf3-uplink.json").write_text(up, encoding="utf-8")
        summary["iperf3"] = {"downlink": parse_iperf(down, True), "uplink": parse_iperf(up, False)}

    dmesg_lines = su(adb, "dmesg").splitlines()
    (out_dir / "dmesg-tail.txt").write_text("\n".join(dmesg_lines[dmesg_before:]), encoding="utf-8")
    punct = [ln for ln in dmesg_lines[dmesg_before:] if re.search(r"punctur|pp_ctrl|pp_bitmap|pp_mgmt|automod", ln, re.I)]
    # the ring buffer can rotate between the two reads, so never report a negative delta
    summary["dmesg_new_lines"] = max(0, len(dmesg_lines) - dmesg_before)
    summary["dmesg_punct_hits"] = punct[:20]

    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ProbeError as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        sys.exit(1)
