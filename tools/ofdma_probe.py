#!/usr/bin/env python3
"""Inventory OFDMA observation interfaces. Default: print plan, contact no device.

--collect runs only the fixed read commands below. It never sends an OID,
attaches a probe, reads trace_pipe, changes Wi-Fi, or generates test traffic.
Artifacts are written on the host, in a new directory. No RU usage is inferred.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "wlan_drv_gen4m_eap_6639"
DEFAULT_ADB = ROOT / "outputs/linux-adb-20261026/extracted/platform-tools/adb"
SYMBOLS = (
    "nicRxProcessRxv", "nicRxProcessRxvLinkStats",
    "asicConnac3xRxProcessRxvforMSP", "asicConnac3xRxPerfIndProcessRXV",
    "nic_rxd_v3_fill_rfb", "nic_rxd_v3_fill_radiotap",
    "mt6639_get_rx_link_stats", "mt6639_get_rx_rate_info",
    "wlanoidQueryStaStatistics", "nicCmdEventQueryStaStatistics",
    "nicUniEventAllStatsOneCmd", "priv_get_ndis",
)
# Literal commands only: no operator-supplied shell snippet, OID, or probe spec.
READ_COMMANDS = (
    ("identity", "id"),
    ("boot_id", "cat /proc/sys/kernel/random/boot_id"),
    ("kernel", "uname -r"),
    ("selinux", "cat /sys/fs/selinux/enforce"),
    ("kptr_restrict", "cat /proc/sys/kernel/kptr_restrict"),
    ("kallsyms", "cat /proc/kallsyms"),
    ("module_sections", f"ls -la /sys/module/{MODULE}/sections"),
    ("tracefs_inventory", "ls -ld /sys/kernel/tracing /sys/kernel/tracing/kprobe_events"),
    ("available_events", "cat /sys/kernel/tracing/available_events"),
    ("available_filter_functions", "cat /sys/kernel/tracing/available_filter_functions"),
    ("current_tracer", "cat /sys/kernel/tracing/current_tracer"),
    ("tracing_on", "cat /sys/kernel/tracing/tracing_on"),
    ("iwpriv_ioctl_list", "/vendor/bin/iwpriv wlan0"),
)


def adb_argv(adb: str, serial: str, command: str) -> list[str]:
    # adb shell joins remote argv: pass a single remote command with the entire
    # su -c argument quoted, so every read executes in the same root context.
    return [adb, "-s", serial, "shell", "su -c " + shlex.quote(command)]


def parse_symbols(text: str, module: str = MODULE) -> dict:
    found = {}
    module_count = 0
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]+)\s+([A-Za-z?])\s+(\S+)(?:\s+\[([^]]+)\])?", line.strip())
        if not match:
            continue
        address, kind, name, owner = match.groups()
        if owner == module:
            module_count += 1
        if name in SYMBOLS and owner == module:
            # Do not expose raw kernel addresses in the derived report.
            found[name] = {"module": owner, "type": kind,
                           "address_visible": int(address, 16) != 0}
    return {"module_symbol_count": module_count, "candidates": found,
            "not_observed": [name for name in SYMBOLS if name not in found],
            "warning": "symbol presence does not establish probeability or RXV layout"}


def parse_iwpriv(text: str) -> dict:
    wrappers = re.findall(r"^\s*(\w+)\s+\(([0-9a-fA-F]+)\)\s*:", text, re.M)
    return {"ioctl_wrappers": [{"name": n, "ioctl_hex": "0x" + v.lower()} for n, v in wrappers],
            "oid_numbers": None,
            "warning": "SIOCGIWPRIV lists ioctl wrappers, not accepted numeric OIDs or safe payload layouts"}


def run_read(argv: list[str], timeout: float) -> dict:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        return {"returncode": done.returncode,
                "stdout": done.stdout.decode("utf-8", "replace"),
                "stderr": done.stderr.decode("utf-8", "replace"), "timeout": False}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": None, "stdout": (exc.stdout or b"").decode("utf-8", "replace"),
                "stderr": (exc.stderr or b"").decode("utf-8", "replace"), "timeout": True}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc), "timeout": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default; no subprocess or output-file writes")
    mode.add_argument("--collect", action="store_true", help="execute fixed read inventory; no OID/probe writes")
    parser.add_argument("--adb", default=str(DEFAULT_ADB))
    parser.add_argument("--serial", default="YOUR_SERIAL")
    parser.add_argument("--out", type=Path, help="fresh host artifact directory; required with --collect")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be in (0, 60]")
    plan = [{"id": name, "remote_read": command,
             "argv": adb_argv(args.adb, args.serial, command)} for name, command in READ_COMMANDS]
    report = {"schema": "mt6639-ofdma-interface-probe-v1", "mode": "collect" if args.collect else "dry-run",
              "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "plan": plan,
              "evidence": {"ofdma_usage": "not_measured", "mru_usage": "not_measured"},
              "side_effects": ["collect: host output files; adb may start its host server",
                               "device: read calls / SIOCGIWPRIV ioctl listing only"],
              "forbidden_actions": ["send OID", "attach kprobe", "write tracefs/sysctl", "read trace_pipe",
                                    "mount", "load module", "change Wi-Fi", "scan", "generate traffic"]}
    if not args.collect:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.out is None:
        parser.error("--collect requires --out")
    if not Path(args.adb).is_file():
        parser.error("--adb must name an existing executable file")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"cannot create fresh --out: {exc}")
    results = {}
    for item in plan:
        result = run_read(item["argv"], args.timeout)
        for stream in ("stdout", "stderr"):
            data = result[stream].encode("utf-8")
            filename = item["id"] + "." + stream + ".txt"
            (args.out / filename).write_bytes(data)
            result[stream + "_artifact"] = {"path": filename, "sha256": hashlib.sha256(data).hexdigest(),
                                            "bytes": len(data)}
        results[item["id"]] = result
        if item["id"] == "identity" and (result["returncode"] != 0 or not re.search(r"\buid=0\(", result["stdout"])):
            report["stopped_reason"] = "root identity unconfirmed; no subsequent read executed"
            break
    report["symbols"] = (parse_symbols(results["kallsyms"]["stdout"])
                         if results.get("kallsyms", {}).get("returncode") == 0 else None)
    report["iwpriv"] = (parse_iwpriv(results["iwpriv_ioctl_list"]["stdout"])
                        if results.get("iwpriv_ioctl_list", {}).get("returncode") == 0 else None)
    events = results.get("available_events", {})
    report["tracefs"] = {
        "event_list_readable": events.get("returncode") == 0,
        "wlan_event_names": [line for line in events.get("stdout", "").splitlines()
                             if re.search(r"wlan|wifi|80211|rxv|ofdma|muru", line, re.I)],
        "function_filter_readable": results.get("available_filter_functions", {}).get("returncode") == 0,
        "probe_attachment": "not_attempted", "ru_layout": "not_validated"}
    report["failed_reads"] = [name for name, value in results.items() if value["returncode"] != 0]
    report["collection_status"] = "partial" if report["failed_reads"] or "stopped_reason" in report else "complete"
    report["results"] = {name: {k: v for k, v in value.items() if k not in ("stdout", "stderr")}
                         for name, value in results.items()}
    (args.out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"collection_status": report["collection_status"], "failed_reads": report["failed_reads"],
                      "summary": str((args.out / "summary.json").resolve()), "evidence": report["evidence"]}, indent=2))
    return 2 if report["collection_status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
