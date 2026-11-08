#!/usr/bin/env python3
"""Collect one AP station association IE through read-only hostapd ubus calls."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from decode_eht_assoc_ie import DecodeError, decode_assoc_ie

MAX_INPUT = 512 * 1024
MAX_ASSOC_IE = 8192
INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
REMOTE = r"""
set -eu
echo __CLIENTS__
ubus call hostapd.INTERFACE_NAME get_clients
echo __STATUS__
ubus call hostapd.INTERFACE_NAME get_status
echo __BOOT_ID__
cat /proc/sys/kernel/random/boot_id
echo __STA_IES__
ubus call hostapd.INTERFACE_NAME get_sta_ies '{"address":"STATION_ADDRESS"}'
"""


class RecheckError(Exception):
    pass


def token(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def section(text: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\n(.*?)(?=^__[A-Z_]+__$|\Z)",
        text,
        re.S | re.M,
    )
    return match.group(1).strip() if match else ""


def parse_remote(text: str, address: str):
    clients = json.loads(section(text, "__CLIENTS__"))
    status = json.loads(section(text, "__STATUS__"))
    host_boot_id = section(text, "__BOOT_ID__")
    station_ies = json.loads(section(text, "__STA_IES__"))
    if not isinstance(clients, dict):
        raise RecheckError("get_clients root must be an object")
    station_map = clients.get("clients")
    if not isinstance(station_map, dict):
        raise RecheckError("get_clients station map missing")
    if not isinstance(status, dict):
        raise RecheckError("get_status root must be an object")
    if not host_boot_id or "\n" in host_boot_id:
        raise RecheckError("AP boot id missing")
    status_state = status.get("status")
    bssid = status.get("bssid")
    ssid = status.get("ssid")
    frequency = status.get("freq")
    if status_state != "ENABLED":
        raise RecheckError("AP BSS is not ENABLED")
    if not isinstance(bssid, str) or not MAC_RE.fullmatch(bssid):
        raise RecheckError("AP bssid invalid")
    if not isinstance(ssid, str) or not ssid:
        raise RecheckError("AP ssid missing")
    if isinstance(frequency, bool) or not isinstance(frequency, int):
        raise RecheckError("AP frequency invalid")
    clients_frequency = clients.get("freq")
    if clients_frequency is not None and clients_frequency != frequency:
        raise RecheckError("get_clients/get_status frequency mismatch")
    wanted = address.lower()
    station = next(
        (value for key, value in station_map.items()
         if isinstance(key, str) and key.lower() == wanted),
        None,
    )
    if not isinstance(station, dict):
        raise RecheckError("station is not currently present")
    if not isinstance(station_ies, dict):
        raise RecheckError("get_sta_ies root must be an object")
    encoded = station_ies.get("assoc_ie")
    if not isinstance(encoded, str) or not encoded:
        raise RecheckError("assoc_ie missing")
    try:
        ie_bytes = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise RecheckError("assoc_ie is not valid base64") from error
    if len(ie_bytes) > MAX_ASSOC_IE:
        raise RecheckError("assoc_ie too large")
    try:
        decoded = decode_assoc_ie(ie_bytes)
    except DecodeError as error:
        raise RecheckError(str(error)) from error
    status = {
        key: station.get(key)
        for key in ("auth", "assoc", "authorized", "preauth", "wds", "he", "mfp")
    }
    for key in ("auth", "assoc", "authorized"):
        if not isinstance(status[key], bool):
            raise RecheckError(f"station {key} must be boolean")
    raw_document = dict(station_ies)
    return raw_document, {
        "schema": "mt6639-ap-station-ie-recheck-v1",
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host_boot_id": host_boot_id,
        "station_token": token(wanted),
        "ssid_token": token(ssid),
        "bssid_token": token(bssid.lower()),
        "frequency_mhz": frequency,
        "client_status": status,
        "association": decoded,
        "remote_payload_read_only": True,
        "ssh_local_side_effects": "SSH user config and known_hosts may be read or updated",
        "limitations": [
            "This is an AP-hostapd peer observation, not an over-the-air capture.",
            "Association capabilities do not prove per-PPDU MCS or puncturing.",
            "A successful recheck does not alter or validate the wireless link.",
        ],
    }


def write_new_json(path: Path, value) -> bytes:
    if path.exists() or path.is_symlink():
        raise RecheckError(f"{path}: output already exists")
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="root@ap-address or SSH host alias")
    parser.add_argument("--interface", default="phy0.2-ap0")
    parser.add_argument("--address", required=True)
    parser.add_argument("--raw-out", required=True, help="new private path for hostapd get_sta_ies JSON")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    if args.host.startswith("-") or not HOST_RE.fullmatch(args.host):
        print("FAIL invalid host", file=sys.stderr)
        return 1
    if not INTERFACE_RE.fullmatch(args.interface):
        print("FAIL invalid interface", file=sys.stderr)
        return 1
    if not MAC_RE.fullmatch(args.address):
        print("FAIL invalid station address", file=sys.stderr)
        return 1
    if not (1 <= args.timeout <= 60):
        print("FAIL invalid timeout", file=sys.stderr)
        return 1
    remote = (
        REMOTE.replace("INTERFACE_NAME", args.interface)
        .replace("STATION_ADDRESS", args.address.lower())
    )
    try:
        done = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                args.host,
                remote,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print("FAIL ssh timeout", file=sys.stderr)
        return 1
    if done.returncode != 0:
        print(f"FAIL ssh rc={done.returncode}: {done.stderr.strip()}", file=sys.stderr)
        return 1
    try:
        raw, summary = parse_remote(done.stdout, args.address)
        raw_bytes = write_new_json(Path(args.raw_out), raw)
        summary["raw_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (RecheckError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
