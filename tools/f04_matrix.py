#!/usr/bin/python3
"""F04 performance matrix. Default dry-run has no subprocesses or file writes."""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import math
import re
import shlex
import statistics
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADB = ROOT / "outputs/linux-adb-20261026/extracted/platform-tools/adb"
MARKER = "---F04-POWER-SAVE---"
LINK_COMMAND = f"iw dev wlan0 link && printf '\\n{MARKER}\\n' && iw dev wlan0 get power_save"
MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")


class InvalidWindow(ValueError):
    pass


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def dump(path, data):
    # A fresh output directory belongs exclusively to this invocation.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def adb_command(args, command):
    return [args.adb, "-s", args.serial, "shell", "su -c " + shlex.quote(command)]


def ssh_command(args, command):
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "root@" + args.server, command]


def parse_link(text):
    bssid = re.search(r"Connected to\s+([0-9a-fA-F:]{17})\b", text)
    # iw prints "freq: 6135.0" for channels whose centre is a whole number of
    # MHz, so the decimal part must be accepted; integer-only parsing made every
    # real link look like a frequency mismatch.
    freq = re.search(r"^\s*freq:\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, re.M)
    power = re.search(r"^\s*Power save:\s*(on|off)\s*$", text, re.M | re.I)
    widths = sorted({int(x) for x in re.findall(r"\b(20|40|80|160|320)\s*MHz\b", text, re.I)})
    return {"bssid": bssid.group(1).lower() if bssid else None,
            "frequency_mhz": int(float(freq.group(1))) if freq else None,
            "power_save": power.group(1).lower() if power else None,
            "rate_widths_mhz": widths}


def link_errors(link, args, require_off=True):
    errors = []
    if link["bssid"] != args.bssid:
        errors.append("BSSID mismatch or disconnected")
    if link["frequency_mhz"] != args.frequency:
        errors.append("frequency mismatch or missing")
    if require_off and link["power_save"] != "off":
        errors.append("power_save is not confirmed off")
    if not require_off and link["power_save"] not in ("on", "off"):
        errors.append("power_save state missing")
    # iw link reports per-rate width; it is recorded, not treated as proof of
    # the AP's configured channel width. Narrower per-rate PPDUs are possible.
    return errors


def number(obj, key, minimum=0):
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise InvalidWindow(f"missing/invalid iperf field: {key}")
    return value


def parse_iperf(text, protocol, duration):
    try:
        result = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise InvalidWindow("iperf output is not a complete JSON object") from exc
    if not isinstance(result, dict) or result.get("error"):
        raise InvalidWindow("iperf reported an error")
    end = result.get("end", {})
    if not isinstance(end, dict):
        raise InvalidWindow("iperf end aggregate is not an object")
    # Do not fall back to end.sum: several versions give sender throughput in
    # that field even when its loss counters describe the receiver.
    received, sent = end.get("sum_received"), end.get("sum_sent")
    if not isinstance(received, dict) or not isinstance(sent, dict):
        raise InvalidWindow("receiver/sender aggregates missing; unsupported iperf JSON")
    seconds = number(received, "seconds", 0.001)
    if seconds < duration * 0.95 or seconds > duration + 15:
        raise InvalidWindow("receiver duration outside requested window")
    measured = {"receiver_gbps": number(received, "bits_per_second") / 1e9,
                "sender_gbps": number(sent, "bits_per_second") / 1e9,
                "receiver_seconds": seconds, "receiver_bytes": number(received, "bytes")}
    if protocol == "tcp":
        measured["retransmits"] = number(sent, "retransmits")
    else:
        measured.update({"lost_packets": number(received, "lost_packets"),
                         "packets": number(received, "packets", 1),
                         "loss_percent": number(received, "lost_percent"),
                         "jitter_ms": number(received, "jitter_ms")})
        if measured["loss_percent"] > 100 or measured["lost_packets"] > measured["packets"]:
            raise InvalidWindow("invalid UDP loss counters")
    return measured


def rates(value):
    result = value.split(",")
    if not result or any(not re.fullmatch(r"[1-9]\d*(?:\.\d+)?[KMG]", x) for x in result):
        raise argparse.ArgumentTypeError("comma-separated positive rates, e.g. 500M,1G,1.5G,2G")
    return result


def matrix(args, run_token="plan"):
    tests = []
    for iteration in range(1, args.tcp_rounds + 1):
        for direction in ("down", "up"):
            tests.append({"id": f"tcp-{direction}-{iteration}", "protocol": "tcp",
                          "direction": direction, "round": iteration, "streams": args.streams})
    for direction, steps in (("up", args.udp_up), ("down", args.udp_down)):
        for rate in steps:
            tests.append({"id": f"udp-{direction}-{rate}", "protocol": "udp",
                          "direction": direction, "target": rate, "streams": 1})
    for test in tests:
        client_token = "f04-" + run_token + "-" + test["id"]
        test["client_token"] = client_token
        test["phone_pidfile"] = "/data/local/tmp/" + client_token + ".pid"
        command = ["/system/bin/iperf3", "-c", args.server, "-p", str(args.port),
                   "-t", str(args.duration), "-P", str(test["streams"]), "-J", "-T", client_token]
        if test["direction"] == "down":
            command.append("-R")
        if test["protocol"] == "udp":
            command.extend(["-u", "-b", test["target"], "-l", str(args.udp_length)])
            if test["direction"] == "down" and args.scatter_buffer:
                command.extend(["-w", "4M"])
        # The title is an ownership token in /proc/PID/cmdline. Keep a PID so
        # abort can stop the phone client even if killing local adb is insufficient.
        remote = ("printf '%s\\n' $$ > " + shlex.quote(test["phone_pidfile"]) +
                  " && exec " + shlex.join(command))
        test["argv"] = adb_command(args, remote)
    return tests


class Runner:
    def run(self, argv, timeout):
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
            return {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        except subprocess.TimeoutExpired as exc:
            return {"returncode": None, "stdout": str(exc.stdout or ""), "stderr": "command timeout"}
        except OSError as exc:
            return {"returncode": None, "stdout": "", "stderr": str(exc)}

    def start(self, argv, stdout, stderr):
        return subprocess.Popen(argv, stdout=stdout, stderr=stderr)

    def sleep(self, seconds):
        time.sleep(seconds)

    def clock(self):
        return time.monotonic()


class Experiment:
    def __init__(self, args, runner=None):
        self.args, self.runner = args, runner or Runner()
        self.run_token = uuid.uuid4().hex
        self.tests = matrix(args, self.run_token)
        self.sequence = 0
        self.report = {"schema": "mt6639-f04-matrix-v1", "created_utc": utc(),
                       "validity": "incomplete", "formal_parameters": args.duration == 60 and args.tcp_rounds == 3 and args.streams == 4,
                       "anchors": {"bssid": args.bssid, "frequency_mhz": args.frequency,
                                   "ap_configured_width_mhz": args.width, "ap_record": args.ap_anchor,
                                   "server": args.server, "port": args.port, "serial": args.serial},
                       "parameters": {"duration_seconds": args.duration, "tcp_streams": args.streams,
                                      "tcp_rounds": args.tcp_rounds, "udp_streams": 1,
                                      "udp_payload_bytes": args.udp_length, "poll_seconds": args.poll_seconds,
                                      "udp_down_socket_buffer": "4M" if args.scatter_buffer else None,
                                      "udp_loss_threshold_percent": args.loss_threshold},
                       "rounds": [], "cleanup": []}
        self.original_power = None
        self.power_may_change = False
        self.server_may_start = False
        self.remote_dir = "/tmp/f04-matrix-" + self.run_token
        self.report["remote_artifact_directory"] = self.remote_dir

    def command(self, label, argv, timeout=15, required=True):
        self.sequence += 1
        prefix = f"{self.sequence:04d}-{label}"
        result = self.runner.run(argv, timeout)
        for stream in ("stdout", "stderr"):
            (self.args.out / (prefix + "." + stream + ".txt")).write_text(result[stream])
        dump(self.args.out / (prefix + ".command.json"), {"utc": utc(), "argv": argv, **result})
        if required and result["returncode"] != 0:
            raise InvalidWindow(f"{label} failed; see {prefix} artifacts")
        return result

    def snapshot(self, label, require_off=True):
        result = self.command(label, adb_command(self.args, LINK_COMMAND), required=False)
        link = parse_link(result["stdout"])
        link.update({"utc": utc(), "label": label, "command_returncode": result["returncode"]})
        errors = link_errors(link, self.args, require_off)
        if result["returncode"] != 0 or MARKER not in result["stdout"]:
            errors.append("link/power command failed or output incomplete")
        link["errors"] = errors
        return link

    def assert_link(self, snapshot):
        if snapshot["errors"]:
            raise InvalidWindow("; ".join(snapshot["errors"]))

    def setup(self):
        initial = self.snapshot("initial", require_off=False)
        self.report["initial_link"] = initial
        self.assert_link(initial)
        self.original_power = initial["power_save"]
        self.command("phone-environment", adb_command(self.args,
                     "id && /system/bin/iperf3 --version && cat /proc/sys/net/core/rmem_max && cat /proc/sys/net/core/wmem_max"))
        # Save/read back original power state before making the sole phone setting change.
        self.power_may_change = True
        self.command("power-save-off", adb_command(self.args, "iw dev wlan0 set power_save off"))
        fixed = self.snapshot("fixed-power")
        self.report["fixed_link"] = fixed
        self.assert_link(fixed)
        d = shlex.quote(self.remote_dir)
        startup = (f"set -eu; command -v iperf3; mkdir {d}; "
                   f"nohup iperf3 -s -p {self.args.port} --logfile {d}/server.log "
                   f">{d}/launcher.log 2>&1 </dev/null & echo $! >{d}/pid; "
                   f"sleep 1; kill -0 $(cat {d}/pid); "
                   f"test -z \"$(cat {d}/launcher.log)\"; "
                   f"test -f {d}/server.log; "
                   f"! grep -q 'error' {d}/server.log")
        self.server_may_start = True
        self.command("server-start", ssh_command(self.args, startup), timeout=20)
        self.command("server-environment", ssh_command(self.args,
                     "iperf3 --version; cat /proc/sys/net/core/rmem_max; cat /proc/sys/net/core/wmem_max"))

    def run_round(self, test):
        row = {k: v for k, v in test.items() if k != "argv"}
        row.update({"validity": "incomplete", "started_utc": utc(), "snapshots": []})
        self.report["rounds"].append(row)
        process = None
        try:
            before = self.snapshot(test["id"] + "-before")
            row["snapshots"].append(before)
            self.assert_link(before)
            with (self.args.out / (test["id"] + ".json")).open("w") as stdout, \
                    (self.args.out / (test["id"] + ".stderr.txt")).open("w") as stderr:
                dump(self.args.out / (test["id"] + ".command.json"), {"utc": utc(), "argv": test["argv"]})
                process = self.runner.start(test["argv"], stdout, stderr)
                deadline = self.runner.clock() + self.args.duration + 30
                while process.poll() is None:
                    self.runner.sleep(self.args.poll_seconds)
                    snapshot = self.snapshot(test["id"] + "-during")
                    row["snapshots"].append(snapshot)
                    self.assert_link(snapshot)
                    if self.runner.clock() > deadline:
                        raise InvalidWindow("iperf process exceeded duration + 30 seconds")
                if process.wait() != 0:
                    raise InvalidWindow("iperf client failed; see JSON/stderr artifacts")
            after = self.snapshot(test["id"] + "-after")
            row["snapshots"].append(after)
            self.assert_link(after)
            row["metrics"] = parse_iperf((self.args.out / (test["id"] + ".json")).read_text(),
                                         test["protocol"], self.args.duration)
            row["validity"] = "valid"
            row["quality"] = ("loss_above_threshold" if test["protocol"] == "udp" and
                              row["metrics"]["loss_percent"] > self.args.loss_threshold else "measured")
        except BaseException as exc:
            row.update({"validity": "invalid", "reason": str(exc) or type(exc).__name__})
            raise
        finally:
            if process is not None and row["validity"] == "invalid":
                pidfile = shlex.quote(test["phone_pidfile"])
                command = (f"if test -f {pidfile}; then p=$(cat {pidfile}); "
                           "case \"$p\" in ''|*[!0-9]*) exit 2;; esac; "
                           "if test -r /proc/$p/cmdline; then "
                           "case \"$(tr '\\000' ' ' </proc/$p/cmdline)\" in "
                           f"*{shlex.quote(test['client_token'])}*) kill \"$p\";; "
                           "*) echo 'PID no longer matches owned client' >&2; exit 2;; esac; fi; fi")
                result = self.command("stop-owned-client", adb_command(self.args, command), required=False)
                self.report["cleanup"].append({"action": "stop_owned_client", "round": test["id"],
                                              "ok": result["returncode"] == 0})
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            row["ended_utc"] = utc()
            self.checkpoint()

    def cleanup(self):
        if self.server_may_start:
            # Only stop our server, identified by this unique logfile argument.
            # Leave remote logs for audit; do not touch an existing listener.
            d = shlex.quote(self.remote_dir)
            command = (f"d={d}; if test -f \"$d/pid\"; then p=$(cat \"$d/pid\"); "
                       "case \"$p\" in ''|*[!0-9]*) exit 2;; esac; "
                       "if test -r /proc/$p/cmdline; then "
                       "case \"$(tr '\\000' ' ' </proc/$p/cmdline)\" in "
                       "*\"$d/server.log\"*) kill \"$p\";; "
                       "*) echo 'PID no longer matches owned server' >&2; exit 2;; esac; fi; fi; "
                       "if test -f \"$d/server.log\"; then cat \"$d/server.log\"; fi; "
                       "if test -f \"$d/launcher.log\"; then cat \"$d/launcher.log\"; fi")
            result = self.command("server-stop-and-log", ssh_command(self.args, command), required=False)
            self.report["cleanup"].append({"action": "stop_owned_server", "ok": result["returncode"] == 0})
        if self.power_may_change and self.original_power:
            result = self.command("restore-power", adb_command(self.args,
                                  "iw dev wlan0 set power_save " + self.original_power), required=False)
            readback = self.command("restore-power-readback", adb_command(self.args,
                                   "iw dev wlan0 get power_save"), required=False)
            restored = parse_link(readback["stdout"])["power_save"]
            self.report["cleanup"].append({"action": "restore_power_save", "expected": self.original_power,
                                          "observed": restored, "ok": result["returncode"] == 0 and
                                          readback["returncode"] == 0 and restored == self.original_power})

    def checkpoint(self):
        self.report["planned_round_count"] = len(self.tests)
        self.report["completed_round_count"] = sum(r["validity"] == "valid" for r in self.report["rounds"])
        self.report["groups"] = summarize(self.report["rounds"], self.report["validity"] == "valid")
        dump(self.args.out / "summary.json", self.report)
        (self.args.out / "summary.md").write_text(markdown(self.report))

    def run(self):
        self.checkpoint()
        try:
            self.setup()
            for test in self.tests:
                self.run_round(test)
            self.report["validity"] = "valid"
        except (InvalidWindow, OSError, KeyboardInterrupt) as exc:
            self.report.update({"validity": "invalid", "reason": str(exc) or type(exc).__name__})
        finally:
            try:
                self.cleanup()
            except (OSError, KeyboardInterrupt) as exc:
                self.report["cleanup"].append({"action": "cleanup", "ok": False, "reason": str(exc)})
            if any(not x["ok"] for x in self.report["cleanup"]):
                self.report["cleanup_ok"] = False
            else:
                self.report["cleanup_ok"] = True
            self.report["ended_utc"] = utc()
            self.checkpoint()
        return 0 if self.report["validity"] == "valid" and self.report["cleanup_ok"] else 2


def summarize(rounds, whole_valid):
    groups = {}
    for row in rounds:
        if row["validity"] != "valid":
            continue
        key = row["protocol"] + "-" + row["direction"] + ("-" + row["target"] if "target" in row else "")
        groups.setdefault(key, []).append(row["metrics"])
    result = {}
    for key, rows in groups.items():
        values = [r["receiver_gbps"] for r in rows]
        group = {"n": len(rows), "receiver_gbps_mean": statistics.mean(values),
                 "receiver_gbps_min": min(values), "receiver_gbps_max": max(values),
                 "eligible_for_conclusion": whole_valid}
        if "retransmits" in rows[0]:
            retransmits = [r["retransmits"] for r in rows]
            group.update({"retransmits_total": sum(retransmits),
                          "retransmits_mean": statistics.mean(retransmits),
                          "retransmits_min": min(retransmits), "retransmits_max": max(retransmits)})
        else:
            # Packet-weighted loss, not the arithmetic average of percentages.
            group.update({"lost_packets": sum(r["lost_packets"] for r in rows),
                          "packets": sum(r["packets"] for r in rows),
                          "loss_percent_mean": statistics.mean(r["loss_percent"] for r in rows),
                          "loss_percent_min": min(r["loss_percent"] for r in rows),
                          "loss_percent_max": max(r["loss_percent"] for r in rows)})
            group["packet_weighted_loss_percent"] = 100 * group["lost_packets"] / group["packets"]
        result[key] = group
    return result


def markdown(report):
    lines = ["# F04 性能矩阵", "", f"- 整段有效性：**{report['validity']}**",
             f"- 标准参数（60 秒 / TCP 4 流 / 3 轮）：{report['formal_parameters']}",
             f"- 完成：{report['completed_round_count']}/{report['planned_round_count']}",
             f"- AP 锚点（人工记录）：{report['anchors']['ap_record']}",
             f"- BSSID / 频点 / AP 配置宽度：{report['anchors']['bssid']} / "
             f"{report['anchors']['frequency_mhz']} MHz / {report['anchors']['ap_configured_width_mhz']} MHz",
             f"- 中止原因：{report.get('reason', '无')}", "",
             "整段 invalid 时，下列已完成轮次仅供排错，不进入正式均值或 160/320 对照。",
             "区间为样本最小值—最大值；不是置信区间。UDP 台阶默认每档一次。", "",
             "| 组 | n | 接收均值 Gbps | 范围 Gbps | 重传合计 / 丢包 |", "| --- | --- | --- | --- | --- |"]
    for name, group in report["groups"].items():
        extra = (str(group["retransmits_total"]) + " retransmits" if "retransmits_total" in group else
                 f"{group['lost_packets']}/{group['packets']} ({group['loss_percent_mean']:.3f}% reported)")
        lines.append(f"| {name} | {group['n']} | {group['receiver_gbps_mean']:.3f} | "
                     f"{group['receiver_gbps_min']:.3f}—{group['receiver_gbps_max']:.3f} | {extra} |")
    lines.extend(["", "| 轮次 | 有效性 | 质量标记 | 原因 |", "| --- | --- | --- | --- |"])
    for row in report["rounds"]:
        reason = row.get("reason", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['id']} | {row['validity']} | {row.get('quality', '')} | {reason} |")
    lines.extend(["", "清理记录：`" + json.dumps(report["cleanup"], ensure_ascii=False) + "`", "",
                  "链路以开测前、运行中轮询和结束后读回为证；采样间隔内的短暂漫游不能完全排除。",
                  "本工具不测 MCS/RU 使用率，不把 UDP 丢包单独归因为空口损失。", ""])
    return "\n".join(lines)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: print plan, no device or host writes")
    mode.add_argument("--execute", action="store_true", help="generate traffic and temporarily disable phone power save")
    mode.add_argument("--selftest", action="store_true", help="fake runner and fixture outputs only; no devices")
    p.add_argument("--adb", default=str(DEFAULT_ADB))
    p.add_argument("--serial", default="YOUR_SERIAL")
    p.add_argument("--server", default="198.51.100.60", help="IPv4 address; SSH root and iperf endpoint")
    p.add_argument("--port", type=int, default=5201)
    p.add_argument("--bssid", default="02:00:00:00:73:1c")
    p.add_argument("--frequency", type=int, default=6135)
    p.add_argument("--width", type=int, choices=(160, 320), default=320, help="AP configured width anchor, not a command")
    p.add_argument("--ap-anchor", default="", help="AP session record / config artifact; required for execute")
    p.add_argument("--out", type=Path, help="fresh host output directory; required for execute")
    p.add_argument("--duration", type=int, default=60, help="seconds per test; non-60 is nonformal")
    p.add_argument("--tcp-rounds", type=int, default=3)
    p.add_argument("--streams", type=int, default=4, help="TCP only; UDP uses one stream and an aggregate -b target")
    p.add_argument("--udp-up", type=rates, default=rates("500M,1G,1.5G,2G"))
    p.add_argument("--udp-down", type=rates, default=rates("1G,2G,3G,4G"))
    p.add_argument("--udp-length", type=int, default=1200)
    p.add_argument("--scatter-buffer", action="store_true",
                   help="opt in to UDP downlink -w 4M; the phone's iperf 3.6+ aborts with "
                        "'unable to read from stream socket: Try again', so this is off by default")
    p.add_argument("--no-scatter-buffer", action="store_true",
                   help="accepted for old command lines; -w 4M is already off by default")
    p.add_argument("--poll-seconds", type=float, default=5)
    p.add_argument("--loss-threshold", type=float, default=1.0, help="UDP quality flag threshold in percent; not a validity gate")
    return p


def selftest():
    good = "Connected to 02:00:00:00:73:1c (on wlan0)\n freq: 6135\n rx bitrate: 5764.8 MBit/s 320MHz\n" + MARKER + "\nPower save: off\n"
    # Regression: real `iw` prints "freq: 6295.0"; an integer-only regex made
    # every genuine 6 GHz link fail the anchor check.
    for sample, expected in (("freq: 6295.0", 6295), ("\tfreq: 6135.0", 6135), ("freq: 6135", 6135)):
        parsed = parse_link("Connected to 02:00:00:00:77:7c (on wlan0)\n" + sample + "\n")
        assert parsed["frequency_mhz"] == expected, (sample, parsed)
    def fixture(protocol, duration=60):
        receiver = {"seconds": duration, "bits_per_second": 900_000_000, "bytes": 6_750_000_000,
                    "lost_packets": 100, "packets": 1000, "lost_percent": 10, "jitter_ms": 0.02}
        return json.dumps({"end": {"sum": {"bits_per_second": 4e9}, "sum_received": receiver,
                                    "sum_sent": {"bits_per_second": 1e9, "retransmits": 17}}})

    class FakeProcess:
        def __init__(self, stdout, protocol, fail=False):
            stdout.write(fixture(protocol))
            stdout.flush()
            self.polls, self.stopped, self.fail = 0, False, fail
        def poll(self):
            self.polls += 1
            return (1 if self.fail else 0) if self.stopped or self.polls > 1 else None
        def wait(self, timeout=None):
            self.stopped = True
            return 1 if self.fail else 0
        def terminate(self):
            self.stopped = True
        kill = terminate

    class FakeRunner(Runner):
        def __init__(self, bad_at=None, bad_kind="bssid", fail_iperf=False, initial_power="off", fail_server=False):
            self.snapshots = self.started = self.now = 0
            self.bad_at, self.bad_kind, self.fail_iperf = bad_at, bad_kind, fail_iperf
            self.power, self.fail_server = initial_power, fail_server
            self.commands = []
        def run(self, argv, timeout):
            self.commands.append(argv)
            command = argv[-1]
            output = ""
            if MARKER in command:
                self.snapshots += 1
                output = good.replace("Power save: off", "Power save: " + self.power)
                if self.snapshots == self.bad_at:
                    if self.bad_kind == "bssid":
                        output = good.replace("02:00:00:00:73:1c", "02:00:00:00:77:7b")
                    elif self.bad_kind == "freq":
                        output = good.replace("6135", "5500")
                    elif self.bad_kind == "power":
                        output = good.replace("Power save: off", "Power save: on")
                    elif self.bad_kind == "disconnected":
                        output = "Not connected.\n" + MARKER + "\nPower save: off\n"
            elif "set power_save" in command:
                self.power = re.search(r"set power_save (on|off)", command).group(1)
            elif "get power_save" in command:
                output = "Power save: " + self.power + "\n"
            elif "nohup iperf3" in command and self.fail_server:
                return {"returncode": 1, "stdout": "", "stderr": "Address already in use"}
            return {"returncode": 0, "stdout": output, "stderr": ""}
        def start(self, argv, stdout, stderr):
            self.started += 1
            return FakeProcess(stdout, "udp" if " -u " in argv[-1] else "tcp", self.fail_iperf)
        def sleep(self, seconds):
            self.now += seconds
        def clock(self):
            return self.now

    count = 0
    def check(condition, label):
        nonlocal count
        if not condition:
            raise AssertionError(label)
        count += 1

    with mock.patch("subprocess.run", side_effect=AssertionError("real subprocess forbidden in selftest")), \
            mock.patch("subprocess.Popen", side_effect=AssertionError("real process forbidden in selftest")), \
            tempfile.TemporaryDirectory(prefix="f04-selftest-") as temporary:
        args = parser().parse_args([])
        tests = matrix(args)
        check(len(tests) == 14, "14 default rounds")
        check(sum(" -P 4 " in t["argv"][-1] for t in tests) == 6, "4 streams only for TCP")
        check(all(" -w " not in t["argv"][-1] for t in tests), "no socket buffer by default (iperf 3.6+ aborts)")
        args.scatter_buffer = True
        check(sum(" -w 4M" in t["argv"][-1] for t in matrix(args)) == 4, "buffer only for UDP down")
        args.scatter_buffer = False
        for protocol in ("tcp", "udp"):
            metrics = parse_iperf(fixture(protocol), protocol, 60)
            check(metrics["receiver_gbps"] == 0.9, "receive throughput ignores sum")
        check(parse_iperf(fixture("udp"), "udp", 60)["loss_percent"] == 10, "receiver loss")
        for bad in ("{", '{"error":"socket buffer not set correctly"}', '{"end":[]}',
                    '{"end":{"sum":{"bits_per_second":4e9}}}', fixture("tcp", 5)):
            try:
                parse_iperf(bad, "tcp", 60)
            except InvalidWindow:
                check(True, "reject invalid JSON, errors, sender-only and short test")
            else:
                check(False, "bad iperf accepted")
        for name, fake in [("healthy", FakeRunner()), ("healthy-power-on", FakeRunner(initial_power="on")),
                           ("initial-mismatch", FakeRunner(1)), ("server-start-failure", FakeRunner(fail_server=True)),
                           ("before-roam", FakeRunner(3)), ("during-roam", FakeRunner(4)),
                           ("late-roam", FakeRunner(7)),
                           ("after-roam", FakeRunner(5)), ("frequency", FakeRunner(4, "freq")),
                           ("power", FakeRunner(4, "power")), ("disconnect", FakeRunner(4, "disconnected")),
                           ("iperf-failure", FakeRunner(fail_iperf=True))]:
            args.out = Path(temporary) / name
            args.out.mkdir()
            experiment = Experiment(args, fake)
            code = experiment.run()
            saved = json.loads((args.out / "summary.json").read_text())
            if name.startswith("healthy"):
                check(code == 0 and saved["validity"] == "valid" and fake.started == 14, "full matrix")
                check(saved["groups"]["tcp-down"]["retransmits_total"] == 51, "TCP retransmit aggregation")
                check(saved["rounds"][-1]["quality"] == "loss_above_threshold", "loss quality distinct from validity")
                check(fake.power == ("on" if name.endswith("power-on") else "off"), "restore original power state")
            else:
                check(code == 2 and saved["validity"] == "invalid", name + " invalidates whole window")
                check(fake.started <= (2 if name == "late-roam" else 1), name + " prevents later rounds")
                check(not any(g["eligible_for_conclusion"] for g in saved["groups"].values()), name + " rejects partial conclusions")
                if saved["rounds"]:
                    check(saved["rounds"][-1]["validity"] == "invalid", name + " marks active round")
            check((args.out / "summary.md").is_file(), name + " markdown saved")
        print(f"PASS selftest: {count} assertions; fake ADB/iperf only; real subprocess calls blocked")
    return 0


def main():
    p = parser()
    args = p.parse_args()
    if args.selftest:
        return selftest()
    if not MAC.fullmatch(args.bssid):
        p.error("invalid --bssid")
    args.bssid = args.bssid.lower()
    try:
        ipaddress.IPv4Address(args.server)
    except ValueError:
        p.error("--server must be an IPv4 address")
    if not (1 <= args.port <= 65535 and 1 <= args.duration <= 3600 and 1 <= args.tcp_rounds <= 20
            and 1 <= args.streams <= 32 and 1 <= args.udp_length <= 1400
            and math.isfinite(args.poll_seconds) and 1 <= args.poll_seconds <= 10
            and math.isfinite(args.loss_threshold) and 0 <= args.loss_threshold <= 100
            and 5925 <= args.frequency <= 7125):
        p.error("invalid port, duration, rounds, streams, UDP length, polling, loss threshold or 6 GHz frequency")
    if len(args.udp_up) != len(set(args.udp_up)) or len(args.udp_down) != len(set(args.udp_down)):
        p.error("duplicate UDP rate would overwrite an artifact")
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "expected_minimum_traffic_seconds": len(matrix(args)) * args.duration,
                          "anchors": {"bssid": args.bssid, "frequency_mhz": args.frequency,
                                      "ap_configured_width_mhz": args.width, "ap_record": args.ap_anchor},
                          "plan": matrix(args), "during_execute": ["save power state, set off, read back",
                          "temporary SSH iperf server; stop only owned PID", "check link before/during/after each round",
                          "abort and invalidate whole window on mismatch", "restore original power state"],
                          "no_subprocesses_or_file_writes": True}, ensure_ascii=False, indent=2))
        return 0
    if args.out is None or not args.ap_anchor.strip():
        p.error("--execute requires fresh --out and --ap-anchor from the AP session")
    if not Path(args.adb).is_file():
        p.error("ADB binary missing")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        p.error(f"cannot create fresh --out: {exc}")
    experiment = Experiment(args)
    code = experiment.run()
    print(f"{experiment.report['validity']}: {args.out.resolve() / 'summary.md'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
