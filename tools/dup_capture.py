#!/usr/bin/env python3
"""F03 phone-pcap / wired-pcap / ping correlation. Dry-run by default.

stdlib only; no monitor mode, configuration writes, package installs or restarts.
Normal wlan0 tcpdump observes decapsulated IP, never 802.11 Retry/SeqCtrl.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.compare_icmp_paths import CaptureError, parse_capture  # noqa: E402
from tools.summarize_icmp_pcap import (  # noqa: E402
    PcapError, build_ethernet, build_icmp_echo, build_ipv4, build_pcap,
)

DEFAULT_ADB = "outputs/linux-adb-20261026/extracted/platform-tools/adb"
DEFAULT_SERIAL = "YOUR_SERIAL"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds")


def packet_view(f):
    return {"epoch_s": f["t"], "utc": iso(f["t"]), "ip_id": f["ip_id"]}


def ping_parse(text):
    events = []
    for line in text.splitlines():
        seq = re.search(r"icmp_seq[= ](\d+)", line)
        # Exclude timeouts / unreachable diagnostics from application delivery.
        if not seq or "bytes from" not in line:
            continue
        ts = re.match(r"\[(\d+(?:\.\d+)?)\]", line)
        rtt = re.search(r"time([=<])\s*([\d.]+)\s*ms", line)
        events.append({"seq": int(seq[1]), "dup": "DUP" in line.upper(),
                       "epoch_s": float(ts[1]) if ts else None,
                       "rtt_ms": float(rtt[2]) if rtt else None,
                       "rtt_less_than": bool(rtt and rtt[1] == "<"), "line": line})
    summary = re.search(r"(\d+) packets transmitted,\s*(\d+)(?: packets)? received", text)
    loss = re.search(r"([\d.]+)% packet loss", text)
    dup = re.search(r"\+?(\d+) duplicates", text)
    return {"transmitted": int(summary[1]) if summary else None,
            "received_unique": int(summary[2]) if summary else None,
            "loss_pct": float(loss[1]) if loss else None,
            "duplicates": int(dup[1]) if dup else sum(e["dup"] for e in events),
            "events": events}


def tcpdump_stats(text):
    dropped = re.findall(r"(\d+) packets dropped by kernel", text)
    captured = re.findall(r"(\d+) packets captured", text)
    return {"listening": "listening on" in text,
            "dropped_by_kernel": int(dropped[-1]) if dropped else None,
            "captured_footer": int(captured[-1]) if captured else None}


def retry_delta(before, after):
    def counters(text):
        out = {}
        for key in ("tx packets", "tx retries", "tx failed", "rx packets"):
            hit = re.search(r"^\s*" + key + r":\s*(\d+)\s*$", text, re.M)
            if hit:
                out[key] = int(hit[1])
        return out
    a, b = counters(before), counters(after)
    delta = {k: b[k] - a[k] for k in a.keys() & b.keys()}
    valid = "tx packets" in delta and "tx retries" in delta and all(v >= 0 for v in delta.values())
    ratio = delta["tx retries"] / delta["tx packets"] if valid and delta["tx packets"] > 0 else None
    return {"before": a, "after": b, "delta": delta, "valid": valid,
            "retries_per_tx_packet": ratio,
            "meaning": "AP→STA retry attempts / tx packets; not a probability, may exceed 1; not uplink retries"}


def correlate(phone, wired, ping, phone_ip, target, icmp_id=None):
    """Match endpoints, id, seq AND complete payload; never compare host clocks."""
    groups = defaultdict(lambda: {k: [] for k in ("phone_request", "wired_request", "wired_reply", "phone_reply")})
    for point, cap in (("phone", phone), ("wired", wired)):
        for f in cap["frames"]:
            if f.get("truncated_ip") or f["kind"] not in ("echo_request", "echo_reply"):
                continue
            request = f["kind"] == "echo_request"
            endpoints = (f["src"], f["dst"]) if request else (f["dst"], f["src"])
            if endpoints != (phone_ip, target) or (icmp_id is not None and f["icmp_id"] != icmp_id):
                continue
            key = (f["icmp_id"], f["seq"], hashlib.sha256(f["payload"]).hexdigest())
            groups[key][point + ("_request" if request else "_reply")].append(f)
    rows = []
    for (ident, seq, digest), obs in sorted(groups.items()):
        counts = {k: len(v) for k, v in obs.items()}
        pq, wq, wr, pr = (counts[k] for k in ("phone_request", "wired_request", "wired_reply", "phone_reply"))
        findings = []
        if pq > 1:
            findings.append("duplicate_request_at_phone_observation")
        if wq > pq and pq:
            findings.append("request_multiplicity_increased_phone_to_wired")
        if wr > wq and wq:
            findings.append("reply_multiplicity_increased_at_wired_observation")
        if pr > wr and wr:
            findings.append("reply_multiplicity_increased_wired_to_phone")
        if wq > 1 and wr > 1:
            findings.append("multiple_replies_may_follow_multiple_requests")
        if pq and not wq:
            findings.append("request_observation_gap_phone_to_wired")
        if wq and not wr:
            findings.append("reply_observation_gap_at_wired")
        if wr and not pr:
            findings.append("reply_observation_gap_wired_to_phone")
        if 0 < wq < pq or 0 < pr < wr:
            findings.append("fewer_copies_at_next_observation")
        if not pq:
            findings.append("no_matching_phone_request_or_payload_mismatch")
        all_times = {k: [packet_view(f) for f in sorted(v, key=lambda f: f["t"])] for k, v in obs.items()}
        intervals = {k: [round((v[i]["epoch_s"] - v[i - 1]["epoch_s"]) * 1000, 6)
                         for i in range(1, len(v))] for k, v in all_times.items() if len(v) > 1}
        phone_q = sorted(obs["phone_request"], key=lambda f: f["t"])
        # RTT is valid only inside one capture clock; ambiguous requests stay unlabeled.
        rtts = [round((f["t"] - phone_q[0]["t"]) * 1000, 6) for f in obs["phone_reply"]
                if len(phone_q) == 1 and f["t"] >= phone_q[0]["t"]]
        rows.append({"icmp_id": ident, "seq": seq, "payload_sha256": digest,
                     "counts": counts, "packets": all_times, "duplicate_intervals_ms": intervals,
                     "phone_rtt_ms": rtts, "findings": findings})
    by_seq = defaultdict(list)
    for row in rows:
        if row["counts"]["phone_request"]:
            by_seq[row["seq"]].append(row)
    app_by_seq = defaultdict(list)
    for event in ping["events"]:
        app_by_seq[event["seq"]].append(event)
    for row in rows:
        candidates = by_seq[row["seq"]]
        events = app_by_seq[row["seq"]] if len(candidates) == 1 and row in candidates else []
        row["ping_events"] = events
        if row["counts"]["phone_reply"] and not events:
            row["findings"].append("reply_observed_at_phone_but_no_matching_ping_line")
        if any(e["dup"] for e in events) and row["counts"]["phone_reply"] <= 1:
            row["findings"].append("ping_dup_without_multiple_matching_phone_reply_records")
    dup_events = []
    for e in ping["events"]:
        if e["dup"]:
            candidates = by_seq[e["seq"]]
            dup_events.append(dict(e, matching_groups=[{"icmp_id": r["icmp_id"], "payload_sha256": r["payload_sha256"]}
                                                      for r in candidates],
                                   association="unique" if len(candidates) == 1 else "ambiguous_or_missing"))
    ids = sorted({r["icmp_id"] for r in rows if r["counts"]["phone_request"]})
    totals = {k: sum(r["counts"][k] for r in rows) for k in ("phone_request", "wired_request", "wired_reply", "phone_reply")}
    return {"sessions": ids, "totals": totals,
            "packet_count_differences": {"wired_request_minus_phone_request": totals["wired_request"] - totals["phone_request"],
                                         "wired_reply_minus_wired_request": totals["wired_reply"] - totals["wired_request"],
                                         "phone_reply_minus_wired_reply": totals["phone_reply"] - totals["wired_reply"]},
            "duplicate_groups": [r for r in rows if max(r["counts"].values()) > 1],
            "ping_dup_events": dup_events, "sequences": rows}


def analyze(out, phone_ip, target, icmp_id=None, loss_threshold=5.0, retry_threshold=0.2):
    caps = {point: parse_capture(str(out / (point + ".pcap"))) for point in ("phone", "wired")}
    ping = ping_parse((out / "ping.txt").read_text(errors="replace"))
    result = correlate(caps["phone"], caps["wired"], ping, phone_ip, target, icmp_id)
    issues = []
    qualities = {}
    for point, cap in caps.items():
        log_path = out / (point + "-tcpdump.log")
        stats = tcpdump_stats(log_path.read_text(errors="replace") if log_path.exists() else "")
        qualities[point] = dict(stats, captured_records=cap["captured"], linktype=cap["linktype"],
                               orig_gt_incl=cap["orig_gt_incl"], truncated_ip=cap["snaplen_truncated_ip"])
        if not cap["captured"] or cap["orig_gt_incl"] or cap["snaplen_truncated_ip"]:
            issues.append(point + ":empty_or_truncated_capture")
        if stats["dropped_by_kernel"] != 0 or not stats["listening"]:
            issues.append(point + ":tcpdump_start_or_zero_drop_footer_unverified")
        if stats["captured_footer"] != cap["captured"]:
            issues.append(point + ":pcap_count_differs_from_tcpdump_footer")
    if ping["transmitted"] is None or ping["loss_pct"] is None:
        issues.append("ping_summary_missing")
    if len(result["sessions"]) != 1:
        issues.append("phone_request_icmp_session_missing_or_ambiguous")
    reqs = [r for r in result["sequences"] if r["counts"]["phone_request"]]
    if ping["transmitted"] is not None and len(reqs) != ping["transmitted"]:
        issues.append("phone_unique_request_coverage_differs_from_ping_transmitted")
    if ping["duplicates"] != len(result["ping_dup_events"]):
        issues.append("ping_duplicate_lines_differ_from_summary")
    if ping["received_unique"] is not None and len({e["seq"] for e in ping["events"]}) != ping["received_unique"]:
        issues.append("ping_unique_receive_lines_differ_from_summary")
    if any(e["association"] != "unique" for e in result["ping_dup_events"]):
        issues.append("ping_dup_session_association_ambiguous")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if manifest.get("collection_errors"):
        issues.extend(manifest["collection_errors"])
    expected_count = manifest.get("commands", {}).get("ping_count")
    if expected_count is not None and ping["transmitted"] != expected_count:
        issues.append("ping_transmitted_differs_from_planned_count")
    if "ping_returncode" in manifest and manifest["ping_returncode"] not in (0, 1):
        issues.append("ping_command_failed")
    before, after = out / "ap-before.txt", out / "ap-after.txt"
    retry = retry_delta(before.read_text(errors="replace"), after.read_text(errors="replace")) if before.exists() and after.exists() else None
    signals = []
    if ping["loss_pct"] is not None and ping["loss_pct"] > loss_threshold:
        signals.append("ping_loss_above_threshold")
    if ping["duplicates"] or result["duplicate_groups"]:
        signals.append("duplicate_observed")
    if any("gap" in finding or "mismatch" in finding
           for row in result["sequences"] for finding in row["findings"]):
        signals.append("packet_or_application_observation_gap")
    if retry and retry["retries_per_tx_packet"] is not None and retry["retries_per_tx_packet"] >= retry_threshold:
        signals.append("ap_downlink_retries_above_threshold")
    max_rtt = max((v for r in result["sequences"] for v in r["phone_rtt_ms"]), default=None)
    if max_rtt is not None and max_rtt >= 200:
        signals.append("phone_rtt_at_least_200ms")
    return {"schema": "f03-dup-capture-v1", "phone_ip": phone_ip, "target": target,
            "quality": {"valid": not issues, "issues": issues, "captures": qualities},
            "window": {"status": "invalid" if issues else "anomalous" if signals else "no_observed_anomaly",
                       "signals": signals, "loss_threshold_pct_strictly_above": loss_threshold,
                       "retry_attempts_per_tx_packet_threshold_at_least": retry_threshold,
                       "ap_retry_coverage": "available" if retry and retry["retries_per_tx_packet"] is not None else "unknown",
                       "acceptance": "diagnostic window only; never a F03 acceptance verdict"},
            "ping": ping, "ap_retries": retry, "correlation": result,
            "evidence": {"l3_multiple_copies_observed": bool(result["duplicate_groups"]),
                         "ieee80211_retry": "unobservable_without_80211_headers",
                         "discard_localization": "observation_gaps_only_not_proven_discard"},
            "limits": ["wlan0 and wired captures expose L3 copies, not 802.11 Retry/SeqCtrl; air retry unobservable",
                       "multiplicity changes localize observation boundaries, not a unique device or actual discard point",
                       "missing packets remain capture/offload/stack/path candidates even with zero kernel drops",
                       "no cross-host timestamp subtraction or clock synchronization assumption",
                       "AP counters cover other traffic to this STA and only AP downlink retries"]}


def supervisor(command, duration, pidfile=None):
    # Self-owned PID only. The watchdog bounds each remote capture even if the
    # local client disappears; traps handle a normal remote disconnect.
    return ("child=; timer=; cleanup() { "
            '[ -z "$child" ] || kill -INT "$child" 2>/dev/null || :; '
            '[ -z "$timer" ] || kill "$timer" 2>/dev/null || :; }; '
            "trap cleanup EXIT; trap 'exit 130' HUP INT TERM; "
            + command + ' & child=$!; '
            + ("printf '%s\\n' \"$child\" > " + shlex.quote(pidfile) + "; " if pidfile else "")
            + '( sleeper=; '
            + "trap '[ -z \"$sleeper\" ] || kill \"$sleeper\" 2>/dev/null; exit 0' HUP INT TERM; "
            + 'sleep ' + str(duration) + ' & sleeper=$!; wait "$sleeper"; '
            + 'kill -INT "$child" 2>/dev/null || : ) & timer=$!; '
            'wait "$child"; rc=$?; cleanup; wait "$timer" 2>/dev/null || :; '
            'trap - EXIT HUP INT TERM; exit "$rc"')


def adb_cmd(args):
    return [args.adb] + (["-s", args.serial] if args.serial else [])


def phone_cmd(args, script, binary=False):
    return adb_cmd(args) + ["exec-out" if binary else "shell", "su -c " + shlex.quote(script)]


def ssh_cmd(host, script):
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2", host, script]


def status_script(pidfile, pcap=None, logfile=None):
    script = ("pid=$(cat " + shlex.quote(pidfile) + " 2>/dev/null); alive=0; "
              'if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then alive=1; fi; '
              "printf 'capture_alive=%s\\npid=%s\\n' \"$alive\" \"$pid\"; ")
    if pcap:
        script += ("bytes=0; if [ -f " + shlex.quote(pcap) + " ]; then bytes=$(wc -c < "
                   + shlex.quote(pcap) + "); fi; printf 'pcap_bytes=%s\\n' \"$bytes\"; ")
    if logfile:
        script += "printf 'log_tail:\\n'; tail -n 8 " + shlex.quote(logfile) + " 2>/dev/null || :"
    return script


def stop_script(pidfile):
    return ("pid=$(cat " + shlex.quote(pidfile) + " 2>/dev/null); "
            'if [ -n "$pid" ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = tcpdump ]; '
            'then kill -INT "$pid" 2>/dev/null || :; fi')


def commands(args, phone_ip, wired_interface):
    duration = math.ceil(args.duration) + (60 if args.ap_host else 30)
    bpf = "icmp and host " + phone_ip + " and host " + args.target
    token = "f03-dup-" + uuid.uuid4().hex
    phone_dir, wired_dir = "/data/local/tmp/" + token, "/tmp/" + token
    phone_pcap, phone_log, phone_pid = (phone_dir + "/" + n for n in ("phone.pcap", "tcpdump.log", "tcpdump.pid"))
    wired_pid = wired_dir + "/tcpdump.pid"
    phone_tcp = (shlex.join(["/system/bin/tcpdump", "-n", "-U", "-s", "0", "-i", args.phone_interface,
                             "-w", phone_pcap, bpf]) + " > /dev/null 2> " + shlex.quote(phone_log))
    wired_tcp = shlex.join([args.wired_tcpdump, "-n", "-U", "-s", "0", "-i", wired_interface, "-w", "-", bpf])
    wired_script = "umask 077; mkdir " + shlex.quote(wired_dir) + " || exit; " + supervisor(wired_tcp, duration, wired_pid)
    def wired_remote(script):
        if not args.wired_no_sudo:
            script = "sudo -n sh -c " + shlex.quote(script)
        return ssh_cmd(args.wired_host, script)
    count = max(1, math.ceil(args.duration / args.interval))
    ping = ["ping"] + ([] if args.no_ping_timestamps else ["-D"])
    ping += ["-n", "-I", args.phone_interface, "-i", str(args.interval), "-c", str(count), "-W", "2", args.target]
    phone_script = "umask 022; mkdir " + shlex.quote(phone_dir) + " || exit; " + supervisor(phone_tcp, duration, phone_pid)
    out = args.out or Path("OUTPUT_DIR")
    return {"phone_capture": phone_cmd(args, phone_script),
            "wired_capture": wired_remote(wired_script),
            "phone_status": phone_cmd(args, status_script(phone_pid, phone_pcap, phone_log)),
            "wired_status": wired_remote(status_script(wired_pid)),
            "phone_stop": phone_cmd(args, stop_script(phone_pid)),
            "wired_stop": wired_remote(stop_script(wired_pid)),
            "phone_pull_pcap": adb_cmd(args) + ["pull", phone_pcap, str(out / "phone.pcap")],
            "phone_pull_log": adb_cmd(args) + ["pull", phone_log, str(out / "phone-tcpdump.log")],
            "phone_cleanup": phone_cmd(args, "rm -f " + shlex.join([phone_pcap, phone_log, phone_pid])
                                        + "; rmdir " + shlex.quote(phone_dir)),
            "wired_cleanup": wired_remote("rm -f " + shlex.quote(wired_pid) + "; rmdir " + shlex.quote(wired_dir)),
            "phone_remote_directory": phone_dir,
            "ping": phone_cmd(args, shlex.join(ping)), "capture_duration_s": duration, "ping_count": count}


def parse_status(text):
    alive = re.search(r"^capture_alive=(\d+)", text, re.M)
    size = re.search(r"^pcap_bytes=\s*(\d+)", text, re.M)
    pid = re.search(r"^pid=(\d+)", text, re.M)
    return {"capture_alive": bool(alive and alive[1] == "1"), "pid": int(pid[1]) if pid else None,
            "pcap_bytes": int(size[1]) if size else 0, "remote_log_tail": text[-2000:]}


def capture_ready(state):
    return (state["capture_alive"] and state["client_returncode"] is None
            and state["pcap_bytes"] >= 24 and state.get("status_returncode", 0) == 0)


def readiness_snapshot(plan, processes, out, deadline=None):
    states = {}
    for point, proc in processes:
        try:
            timeout = min(3, max(0.01, deadline - time.monotonic())) if deadline else 3
            r = subprocess.run(plan[point + "_status"], capture_output=True, text=True, timeout=timeout)
            state = parse_status(r.stdout)
            state["status_returncode"] = r.returncode
            state["status_stderr_tail"] = r.stderr[-500:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            state = {"capture_alive": False, "pcap_bytes": 0, "status_error": str(exc)}
        state["client_returncode"] = proc.poll()
        if point == "wired":
            pcap = out / "wired.pcap"
            state["pcap_bytes"] = pcap.stat().st_size if pcap.exists() else 0
        local_log = out / ("phone-capture-client.log" if point == "phone" else "wired-tcpdump.log")
        state["client_log_tail"] = local_log.read_text(errors="replace")[-2000:] if local_log.exists() else ""
        state["ready"] = capture_ready(state)
        states[point] = state
    return states


def checked(cmd, timeout=15):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise CaptureError(f"command failed ({result.returncode}): {shlex.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def ap_snapshot(args, out, when):
    if not args.ap_host:
        return
    iface, mac = shlex.quote(args.ap_interface), shlex.quote(args.station_mac)
    script = (f"printf '== iw station ==\\n'; iw dev {iface} station get {mac}; "
              f"printf '== hostapd station ==\\n'; hostapd_cli -i {iface} sta {mac}; "
              f"printf '== bridge counters ==\\n'; bridge -s link show; "
              f"printf '== link counters ==\\n'; ip -s link show dev {iface}")
    # Partial evidence is retained; unavailable hostapd_cli is not an invitation to install it.
    p = subprocess.run(ssh_cmd(args.ap_host, script), capture_output=True, text=True, timeout=15)
    (out / ("ap-" + when + ".txt")).write_text(p.stdout + "\nSTDERR:\n" + p.stderr)


def collect(args, out):
    if out.exists():
        raise CaptureError("output directory already exists; use a fresh --out to preserve evidence")
    # Preflight reads only; must identify one real wired interface, never `any`.
    addr = checked(phone_cmd(args, "ip -o -4 addr show dev " + shlex.quote(args.phone_interface)))
    ips = re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)/", addr)
    phone_ip = args.phone_ip or (ips[0] if len(ips) == 1 else None)
    if phone_ip is None or phone_ip not in ips:
        raise CaptureError("cannot identify phone IPv4 on requested interface; supply --phone-ip")
    iface = args.wired_interface
    if not iface:
        route = checked(ssh_cmd(args.wired_host, "ip -o route get " + phone_ip))
        match = re.search(r"\bdev (\S+)", route)
        iface = match[1] if match else None
    if not iface or iface in ("any", "lo") or not re.fullmatch(r"[A-Za-z0-9_.:@-]+", iface):
        raise CaptureError("use one concrete wired interface (not any/lo)")
    wired_addr = checked(ssh_cmd(args.wired_host, "ip -o -4 addr show dev " + shlex.quote(iface)))
    if args.target not in re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)/", wired_addr):
        raise CaptureError("target must be a local address on the wired capture interface")
    plan = commands(args, phone_ip, iface)
    out.mkdir(parents=True)
    manifest = {"started_utc": iso(time.time()), "phone_ip": phone_ip, "target": args.target,
                "wired_interface": iface, "commands": plan, "collection_errors": []}
    processes = []
    files = []
    try:
        for point in ("phone", "wired"):
            if point == "phone":
                log = (out / "phone-capture-client.log").open("wb")
                files.append(log)
                p = subprocess.Popen(plan["phone_capture"], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            else:
                data = (out / "wired.pcap").open("wb")
                log = (out / "wired-tcpdump.log").open("wb")
                files.extend([data, log])
                p = subprocess.Popen(plan["wired_capture"], stdout=data, stderr=log, stdin=subprocess.DEVNULL)
            processes.append((point, p))
        deadline = time.monotonic() + 10
        while True:
            states = readiness_snapshot(plan, processes, out, deadline)
            manifest["capture_readiness"] = states
            if all(s["ready"] for s in states.values()):
                break
            if any(s["client_returncode"] is not None for s in states.values()) or time.monotonic() >= deadline:
                raise CaptureError("captures not ready before the 10s readiness deadline (required: live tcpdump + live client + pcap header >=24 bytes); "
                                   "ping not started; " + json.dumps(states, ensure_ascii=False))
            time.sleep(0.1)
        ap_snapshot(args, out, "before")
        manifest["ping_started_utc"] = iso(time.time())
        with (out / "ping.txt").open("wb") as ping_file:
            ping = subprocess.run(plan["ping"], stdout=ping_file, stderr=subprocess.STDOUT,
                                  timeout=args.duration + 8)
        manifest["ping_returncode"] = ping.returncode
        manifest["ping_finished_utc"] = iso(time.time())
        ap_snapshot(args, out, "after")
        for point, proc in processes:
            if proc.poll() is None:
                checked(plan[point + "_stop"], timeout=5)
        for point, p in processes:
            rc = p.wait(timeout=10)
            manifest[point + "_capture_returncode"] = rc
            if rc:
                manifest["collection_errors"].append(point + ":capture_process_nonzero")
    except (CaptureError, subprocess.TimeoutExpired, OSError, KeyboardInterrupt) as exc:
        manifest["collection_errors"].append(str(exc))
    finally:
        for point, p in processes:
            if p.poll() is None:
                try:
                    checked(plan[point + "_stop"], timeout=5)
                    p.wait(timeout=5)
                except (CaptureError, OSError, subprocess.TimeoutExpired) as exc:
                    manifest["collection_errors"].append(point + ":stop:" + str(exc))
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()
        for f in files:
            f.close()
        # Pull complete files only after tcpdump/client stop. Never route pcap
        # bytes through su stdout; Android su diagnostics can corrupt that stream.
        pulled = True
        for key in ("phone_pull_pcap", "phone_pull_log"):
            try:
                checked(plan[key], timeout=30)
            except (CaptureError, OSError, subprocess.TimeoutExpired) as exc:
                pulled = False
                manifest["collection_errors"].append(key + ":" + str(exc))
        try:
            phone_end = parse_status(checked(plan["phone_status"], timeout=5))
            manifest["phone_capture_end_status"] = phone_end
            if phone_end["capture_alive"]:
                pulled = False
                manifest["collection_errors"].append("phone:tcpdump_still_alive_after_stop; remote files retained")
        except (CaptureError, OSError, subprocess.TimeoutExpired) as exc:
            pulled = False
            manifest["collection_errors"].append("phone:end_status_unverified:" + str(exc))
        if pulled:
            try:
                checked(plan["phone_cleanup"], timeout=5)
                manifest["phone_remote_cleanup"] = "removed_owned_files_and_directory"
            except (CaptureError, OSError, subprocess.TimeoutExpired) as exc:
                manifest["phone_remote_cleanup"] = str(exc)
        else:
            manifest["phone_remote_cleanup"] = "retained_for_recovery:" + plan["phone_remote_directory"]
        try:
            checked(plan["wired_cleanup"], timeout=5)
        except (CaptureError, OSError, subprocess.TimeoutExpired) as exc:
            manifest["wired_remote_cleanup"] = str(exc)
        manifest["finished_utc"] = iso(time.time())
        manifest["files"] = {f.name: {"bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                             for f in out.iterdir() if f.is_file() and f.name != "manifest.json"}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if manifest["collection_errors"] and not (out / "ping.txt").exists():
        raise CaptureError("capture incomplete: " + "; ".join(manifest["collection_errors"]))
    return phone_ip


def selftest():
    src, dst = "198.51.100.123", "198.51.100.60"
    src_b, dst_b = ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed

    def frame(seq, reply=False, payload=None):
        payload = payload if payload is not None else ("fixture-%d" % seq).encode()
        return build_ethernet(build_ipv4(dst_b if reply else src_b, src_b if reply else dst_b, 1,
                                        build_icmp_echo(0 if reply else 8, 42, seq, payload), 700 + seq))

    def writecap(path, entries, offset=0, dlt=1, endian="<"):
        records = []
        for ts, seq, reply, payload in entries:
            data = frame(seq, reply, payload)
            if dlt == 113:
                data = b"\0" * 14 + b"\x08\x00" + data[14:]
            records.append((1000 + offset, ts, data))
        path.write_bytes(build_pcap(records, linktype=dlt, endian=endian))

    cases = 0
    # A header is sufficient before the first packet; stderr banner is not a gate.
    state = parse_status("capture_alive=1\npid=123\npcap_bytes=24\nlog_tail:\n")
    state["client_returncode"] = None
    assert capture_ready(state)
    assert not capture_ready(dict(state, pcap_bytes=23))
    assert not capture_ready(dict(state, capture_alive=False))
    assert not capture_ready(dict(state, client_returncode=1))
    assert parse_status("capture_alive=0\npcap_bytes=111\nlog_tail:\npermission denied")["remote_log_tail"].endswith("permission denied")
    cases += 5
    with tempfile.TemporaryDirectory(prefix="dup-capture-selftest-") as td:
        out = Path(td)
        # seq1 clean; seq2 extra downlink copy; seq3 duplicates before wired;
        # seq4 reply seen wired but missing phone; seq5 lacks wired request;
        # seq6 server-side multiplicity; seq7 corrupted payload must not match.
        pq = [(n * 10000, n, False, None) for n in range(1, 8)]
        wq = [x for x in pq if x[1] != 5] + [(31000, 3, False, None)]
        wr = [(n * 10000 + 1000, n, True, b"wrong" if n == 7 else None) for n in range(1, 8) if n != 5]
        wr += [(32000, 3, True, None), (62000, 6, True, None)]
        pr = [x for x in wr if x[1] != 4] + [(23000, 2, True, None)]
        writecap(out / "phone.pcap", pq + pr)
        # Huge clock skew + a different link format/endian must not affect joins.
        writecap(out / "wired.pcap", wq + wr, offset=700, dlt=113, endian=">")
        for point, total in (("phone", len(pq + pr)), ("wired", len(wq + wr))):
            (out / (point + "-tcpdump.log")).write_text(f"listening on eth0\n{total} packets captured\n0 packets dropped by kernel\n")
        (out / "ping.txt").write_text("".join(f"[1000.02] 64 bytes from 198.51.100.60: icmp_seq={n} ttl=64 time=1 ms\n" for n in (1, 2, 3, 6, 7))
                                      + "[1000.023] 64 bytes from 198.51.100.60: icmp_seq=2 ttl=64 time=3 ms (DUP!)\n"
                                      "7 packets transmitted, 5 received, +1 duplicates, 28.57% packet loss\n")
        report = analyze(out, src, dst)
        rows = report["correlation"]["sequences"]
        def row(seq):
            return next(r for r in rows if r["seq"] == seq and r["counts"]["phone_request"])
        expected = {2: "reply_multiplicity_increased_wired_to_phone",
                    3: "request_multiplicity_increased_phone_to_wired",
                    4: "reply_observation_gap_wired_to_phone", 5: "request_observation_gap_phone_to_wired",
                    6: "reply_multiplicity_increased_at_wired_observation"}
        for seq, finding in expected.items():
            assert finding in row(seq)["findings"], (seq, row(seq))
            cases += 1
        assert row(7)["counts"]["wired_reply"] == 0  # payload mismatch
        assert report["quality"]["valid"] and report["window"]["status"] == "anomalous"
        assert report["correlation"]["ping_dup_events"][0]["association"] == "unique"
        assert len(row(2)["packets"]["phone_reply"]) == 2
        assert row(2)["packets"]["wired_reply"][0]["epoch_s"] > 1700
        cases += 5
        assert not row(1)["findings"]
        ambiguous_phone = parse_capture(str(out / "phone.pcap"))
        extra = dict(next(f for f in ambiguous_phone["frames"] if f.get("seq") == 2 and f["kind"] == "echo_request"), icmp_id=43)
        ambiguous_phone["frames"].append(extra)
        ambiguous = correlate(ambiguous_phone, parse_capture(str(out / "wired.pcap")),
                              report["ping"], src, dst)
        assert ambiguous["ping_dup_events"][0]["association"] == "ambiguous_or_missing"
        cases += 2
        (out / "phone-tcpdump.log").write_text("listening on wlan0\n1 packets dropped by kernel\n")
        assert not analyze(out, src, dst)["quality"]["valid"]
        cases += 1
        assert retry_delta("tx packets: 10\ntx retries: 2\n", "tx packets: 20\ntx retries: 6\n")["retries_per_tx_packet"] == 0.4
        assert not retry_delta("tx packets: 20\ntx retries: 6\n", "tx packets: 2\ntx retries: 1\n")["valid"]
        assert ping_parse("64 bytes from x: icmp_seq=8 time<1 ms (DUP!)")["events"][0]["rtt_less_than"]
        cases += 3
        broken = (out / "wired.pcap").read_bytes()[:-1]
        (out / "broken.pcap").write_bytes(broken)
        try:
            parse_capture(str(out / "broken.pcap"))
        except (CaptureError, PcapError):
            cases += 1
        else:
            raise AssertionError("truncated pcap accepted")
        (out / "broken.pcap").write_bytes(b"not-a-pcap" * 4)
        try:
            parse_capture(str(out / "broken.pcap"))
        except PcapError:
            cases += 1
        else:
            raise AssertionError("invalid pcap magic accepted")
        clean = [(10000, 1, False, None), (12000, 1, True, None)]
        for point in ("phone", "wired"):
            writecap(out / (point + ".pcap"), clean)
            (out / (point + "-tcpdump.log")).write_text("listening on eth0\n2 packets captured\n0 packets dropped by kernel\n")
        (out / "ping.txt").write_text("64 bytes from 198.51.100.60: icmp_seq=1 time=2 ms\n1 packets transmitted, 1 received, 0% packet loss\n")
        clean_report = analyze(out, src, dst)
        assert clean_report["quality"]["valid"]
        assert clean_report["window"]["status"] == "no_observed_anomaly"
        assert clean_report["window"]["ap_retry_coverage"] == "unknown"
        cases += 1
    print(json.dumps({"selftest": "PASS", "assertion_groups": cases,
                      "coverage": ["L3 request/reply DUP boundaries", "loss observation gaps", "payload mismatch",
                                   "ping DUP seq association and timestamps", "clock skew", "SLL big-endian",
                                   "tcpdump drops", "retry counter reset", "truncated pcap",
                                   "header-only readiness without banner", "dead process/short header diagnostics"]}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="opt in to a bounded read-only live capture")
    mode.add_argument("--dry-run", action="store_true", help="print commands only (default)")
    mode.add_argument("--selftest", action="store_true", help="synthetic pcap tests; no devices or network")
    mode.add_argument("--analyze", type=Path, help="analyze an existing capture directory offline")
    p.add_argument("--out", type=Path, help="new output directory (required for --execute)")
    p.add_argument("--adb", default=DEFAULT_ADB)
    p.add_argument("--serial", default=DEFAULT_SERIAL)
    p.add_argument("--phone-ip", help="otherwise discover IPv4 on phone interface during execution")
    p.add_argument("--phone-interface", default="wlan0")
    p.add_argument("--wired-host", default="root@198.51.100.60")
    p.add_argument("--wired-interface", help="otherwise discover via route to phone; any/lo forbidden")
    p.add_argument("--wired-tcpdump", default="tcpdump", help="remote executable path, not a shell prefix")
    sudo_mode = p.add_mutually_exclusive_group()
    sudo_mode.add_argument("--wired-no-sudo", action="store_true", dest="wired_no_sudo", default=True,
                           help="run directly with existing permission (default: root SSH)")
    sudo_mode.add_argument("--wired-sudo", action="store_false", dest="wired_no_sudo",
                           help="opt in to sudo -n for a different non-root host that has sudo")
    p.add_argument("--target", default="198.51.100.60", help="IPv4 local to the wired capture interface")
    p.add_argument("--duration", type=float, default=60, help="ping sending window; pcap adds 30s (60s with AP snapshots)")
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--no-ping-timestamps", action="store_true", help="omit -D if phone ping lacks it; pcap timestamps retained")
    p.add_argument("--icmp-id", type=int, help="offline selection when other ping sessions were present")
    p.add_argument("--ap-host", help="optional SSH host for read-only before/after AP counters")
    p.add_argument("--ap-interface")
    p.add_argument("--station-mac", help="phone station MAC as seen by AP (may be randomized)")
    p.add_argument("--loss-threshold", type=float, default=5.0)
    p.add_argument("--retry-threshold", type=float, default=0.2)
    args = p.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 3600 or not math.isfinite(args.interval) or not 0.05 <= args.interval <= 10:
        p.error("duration must be 1..3600s and interval 0.05..10s")
    if not 0 <= args.loss_threshold <= 100 or not math.isfinite(args.retry_threshold) or args.retry_threshold < 0:
        p.error("invalid loss/retry threshold")
    for value in (args.target, args.phone_ip):
        if value:
            ipaddress.IPv4Address(value)
    if args.ap_host and (not args.ap_interface or not args.station_mac):
        p.error("--ap-host requires --ap-interface and --station-mac")
    if args.station_mac and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", args.station_mac):
        p.error("invalid station MAC")
    if args.wired_interface in ("any", "lo"):
        p.error("one concrete wired interface required; any/lo forbidden")
    if args.analyze:
        out = args.analyze
        mf = out / "manifest.json"
        manifest = json.loads(mf.read_text()) if mf.exists() else {}
        phone_ip = args.phone_ip or manifest.get("phone_ip")
        target = manifest.get("target", args.target)
        if not phone_ip:
            p.error("offline analysis needs --phone-ip or manifest.json")
    elif args.execute:
        if not args.out:
            p.error("--execute requires a fresh --out directory")
        out, target = args.out, args.target
        phone_ip = collect(args, out)
    else:
        plan = commands(args, args.phone_ip or "PHONE_IPV4", args.wired_interface or "WIRED_ROUTE_INTERFACE")
        print(json.dumps({"mode": "dry-run", "device_or_network_calls": 0,
                          "preflight": ["discover/verify phone IPv4", "discover/verify concrete wired interface and target address"],
                          "commands": {k: shlex.join(v) if isinstance(v, list) else v for k, v in plan.items()},
                          "ap_counters": "optional before/after iw / hostapd_cli / bridge / ip reads" if args.ap_host else "not configured",
                          "out": str(args.out) if args.out else None}, ensure_ascii=False, indent=2))
        return 0
    report = analyze(out, phone_ip, target, args.icmp_id, args.loss_threshold, args.retry_threshold)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str((out / "report.json").resolve()), "quality": report["quality"],
                      "window": report["window"], "totals": report["correlation"]["totals"]}, ensure_ascii=False, indent=2))
    return 0 if report["quality"]["valid"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, PcapError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
