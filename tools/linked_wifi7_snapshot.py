#!/usr/bin/env python3
"""Run one serial AP-then-phone, read-only Wi-Fi 7 evidence snapshot."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AP_TOOL = REPO / "tools/ap_station_ie_recheck.py"
PHONE_TOOL = REPO / "tools/wifi7_observe.py"
COMPARE_TOOL = REPO / "tools/compare_eht_cap_sources.py"
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def write_new(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def run_step(step_id, command, timeout, logs):
    started = time.monotonic()
    try:
        done = subprocess.run(
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        rc = done.returncode
        stdout, stderr = done.stdout, done.stderr
    except subprocess.TimeoutExpired as error:
        rc = None
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = "timeout"
    elapsed = round(time.monotonic() - started, 3)
    write_new(logs / f"{step_id}.out", stdout)
    write_new(logs / f"{step_id}.err", stderr)
    return {
        "id": step_id,
        "rc": rc,
        "elapsed_s": elapsed,
        "stdout": stdout,
        "stderr": stderr,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ap-host", required=True)
    parser.add_argument("--ap-address", required=True)
    parser.add_argument("--ap-interface", default="phy0.2-ap0")
    parser.add_argument("--adb", required=True)
    parser.add_argument("--adb-port", type=int)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if not MAC_RE.fullmatch(args.ap_address):
        parser.error("--ap-address must be a MAC address")
    if not (1 <= args.timeout <= 120):
        parser.error("--timeout must be 1..120 seconds")
    out = Path(args.out)
    if out.exists():
        parser.error("--out must be a new path")
    out.mkdir(mode=0o700, parents=False)
    logs = out / "logs"
    logs.mkdir(mode=0o700)

    ap_raw = out / "ap-sta-ies.json"
    ap_summary_path = out / "ap-summary.json"
    ap = run_step(
        "ap_station_ie_recheck",
        [
            sys.executable,
            str(AP_TOOL),
            "--host",
            args.ap_host,
            "--interface",
            args.ap_interface,
            "--address",
            args.ap_address,
            "--raw-out",
            str(ap_raw),
            "--timeout",
            str(args.timeout),
        ],
        args.timeout,
        logs,
    )
    steps = [ap]
    if ap["rc"] != 0:
        run = {
            "schema": "mt6639-linked-wifi7-snapshot-v1",
            "status": "failed",
            "failed_step": ap["id"],
            "steps": [{key: ap[key] for key in ("id", "rc", "elapsed_s")}],
            "read_only_sequence": True,
        }
        write_new(out / "run.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(run, ensure_ascii=False, indent=2))
        return 1
    write_new(ap_summary_path, ap["stdout"])

    phone_out = out / "phone"
    phone_command = [
        sys.executable,
        str(PHONE_TOOL),
        "--adb",
        args.adb,
        "--serial",
        args.serial,
        "--out",
        str(phone_out),
        "--timeout",
        str(args.timeout),
    ]
    if args.adb_port is not None:
        phone_command.extend(["--adb-port", str(args.adb_port)])
    phone = run_step(
        "wifi7_observe",
        phone_command,
        args.timeout * 12,
        logs,
    )
    steps.append(phone)

    compare = None
    phone_summary = phone_out / "summary.json"
    if phone_summary.is_file():
        compare = run_step(
            "eht_source_compare",
            [
                sys.executable,
                str(COMPARE_TOOL),
                "--ap-ie-json",
                str(ap_summary_path),
                "--phone-summary-json",
                str(phone_summary),
                "--band",
                "4",
                "--iftype",
                "managed",
            ],
            args.timeout,
            logs,
        )
        steps.append(compare)
        if compare["rc"] == 0:
            write_new(out / "compare.json", compare["stdout"])

    status = "complete"
    if phone["rc"] == 2:
        status = "incomplete"
    elif phone["rc"] != 0 or compare is None or compare["rc"] != 0:
        status = "failed"
    run = {
        "schema": "mt6639-linked-wifi7-snapshot-v1",
        "status": status,
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "steps": [{key: step[key] for key in ("id", "rc", "elapsed_s")}
                  for step in steps],
        "read_only_sequence": True,
        "order": ["AP station IE", "phone observer", "source comparison"],
        "limitations": [
            "Capabilities and current rates are not per-PPDU evidence.",
            "An uncomparable result remains visible when linkage metadata is incomplete.",
            "No wireless write, ADB restart, or AP reconfiguration is performed.",
        ],
    }
    write_new(out / "run.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 2 if status == "incomplete" else 1


if __name__ == "__main__":
    sys.exit(main())
