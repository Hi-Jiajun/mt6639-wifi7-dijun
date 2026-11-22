#!/usr/bin/env python3
"""Run the fixed wireless-state preflight through ADB or gated app SSH."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from stage_6g_candidate import SSHTransport, StageError, load_ssh_command

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/release_device_preflight.sh"
REMOTE_RE = re.compile(r"^/data/local/tmp/mt6639-release-preflight-[A-Za-z0-9._-]+$")
BOOT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# One row for every check emitted by the fixed script, including loop expansions.
REQUIRED_CHECKS = frozenset((
    "root_uid selinux root_context fingerprint kernelsu_manager kernelsu "
    "kernelsu_su_compat kernelsu_su_context xringroot_tool module_mt6639-6g "
    "module_mt6639-hotspot module_mt6639-bt module_mt6639-6g_enabled "
    "module_mt6639-hotspot_enabled module_mt6639-bt_enabled bt_host_profiles "
    "wifi_home_net_6g mtap0 tethering ui_lsposed concurrency_gate "
    "concurrency_gate_marker zygisk_next lsposed"
).split())
EXIT_MARKER = "__MT6639_PREFLIGHT_EXIT__ "


class PreflightError(Exception):
    pass


def resolve_adb(explicit: str | None) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    candidate = shutil.which("adb")
    if candidate:
        return candidate
    candidate = Path.home() / ".local/opt/android/platform-tools/adb"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def existing_adb_port(port: int | None) -> int | None:
    for candidate in ([port] if port is not None else [5037, 5038]):
        try:
            with socket.create_connection(("127.0.0.1", candidate), timeout=0.2):
                return candidate
        except OSError:
            continue
    return None


def adb_daemon_listening(port: int | None) -> bool:
    return existing_adb_port(port) is not None


def run_adb(adb: str, serial: str, port: int | None, args, timeout):
    command = [adb]
    if port is not None:
        command += ["-P", str(port)]
    return subprocess.run(command + ["-s", serial] + args, capture_output=True,
                          text=True, errors="replace", timeout=timeout)


def write_new(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def validate_results(tsv: str, rc: int, previous: str | None,
                     pinned_boot: str | None) -> tuple[str | None, str | None, int]:
    rows = [line.split("\t") for line in tsv.splitlines()]
    failed = sum(row[0] == "FAIL" for row in rows)
    boots = [r[2] for r in rows if len(r) == 4 and r[:2] == ["INFO", "boot"]]
    boot = boots[0] if len(boots) == 1 else None
    if len(boots) > 1 or (boot is not None and not BOOT_ID_RE.fullmatch(boot)):
        return "boot_id_missing_or_ambiguous", None, failed
    keys = [r[1] for r in rows if len(r) == 4 and r[0] in {"PASS", "FAIL", "WARN"}]
    if (any(len(r) != 4 or not r[2] for r in rows)
            or any(r[0] == "WARN" and r[1] not in {
                "kernelsu", "kernelsu_manager", "xringroot_tool",
                "wifi_home_net_6g", "ui_lsposed", "concurrency_gate",
            }
                   for r in rows if len(r) == 4)
            or len(rows) != len(REQUIRED_CHECKS) + 1
            or set(keys) != REQUIRED_CHECKS or len(keys) != len(REQUIRED_CHECKS)):
        return "results_incomplete_or_invalid", boot, failed
    if not boot:
        return "boot_id_missing_or_ambiguous", None, failed
    if pinned_boot is not None and boot != pinned_boot:
        return "boot_id_transport_mismatch", boot, failed
    if previous is not None and boot == previous:
        return "boot_id_unchanged", boot, failed
    return ("preflight_failed" if rc != 0 or failed else None), boot, failed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", help="adb executable; defaults to PATH or local platform-tools")
    parser.add_argument("--adb-port", type=int)
    parser.add_argument("--ssh-command-json", help="pinned direct app SSH argv JSON")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--allow-start-adb", action="store_true",
                        help="allow adb to start a daemon; default requires an existing listener")
    parser.add_argument("--previous-boot-id", help="require a different collected boot ID")
    args = parser.parse_args(argv)
    if args.ssh_command_json and (args.adb is not None or args.adb_port is not None or args.allow_start_adb):
        parser.error("--ssh-command-json conflicts with all ADB options")
    if not (1 <= args.timeout <= 120):
        parser.error("--timeout must be 1..120 seconds")
    if args.adb_port is not None and not 1 <= args.adb_port <= 65535:
        parser.error("--adb-port must be 1..65535")
    if args.previous_boot_id is not None and not BOOT_ID_RE.fullmatch(args.previous_boot_id):
        parser.error("--previous-boot-id must be a lowercase Linux boot UUID")
    out = Path(args.out)
    if out.exists():
        parser.error("--out must be a new path")
    out.mkdir(mode=0o700, parents=False)
    logs = out / "logs"
    logs.mkdir(mode=0o700)
    run = {
        "schema": "mt6639-release-device-preflight-run-v1",
        "status": "failed",
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "remote_directory": None, "remote_uid": None,
        "execution_mode": "ssh_app_ksu" if args.ssh_command_json else None,
        "adb_returncode": None, "preflight_returncode": None,
        "failed_rows": 0, "boot_id": None, "fresh_boot": None,
        "failure_reason": None, "results_path": None,
        "read_only_preflight": True,
        "note": "Read-only refers to wireless state checks; the fixed script writes task-local evidence files in a private remote report directory.",
    }
    transport = None
    try:
        script = SCRIPT.read_bytes()
        run.update(script_sha256=hashlib.sha256(script).hexdigest(), script_size_bytes=len(script))
        if args.ssh_command_json:
            transport = SSHTransport(load_ssh_command(Path(args.ssh_command_json)), args.serial, dict(os.environ))
            # Every call uses the shared source/app -> su -> KSU and boot gate.
            # Do not enable the install-only Enforcing/upload gate: failures
            # under Permissive must remain collectable evidence.
            remote = transport.shell("umask 077; mktemp -d /data/local/tmp/mt6639-release-preflight-XXXXXX", args.timeout).stdout.strip()
            if not REMOTE_RE.fullmatch(remote):
                raise PreflightError("unsafe remote path")
            run.update(remote_directory=remote, remote_uid="0")
            command = (
                "set +e\n/system/bin/sh -c " + shlex.quote(script.decode("utf-8"))
                + " -- " + shlex.quote(remote)
                + '\npreflight_rc=$?\nprintf "\\n' + EXIT_MARKER + '%s\\n" "$preflight_rc"\nexit 0'
            )
            output = transport.shell(command, args.timeout).stdout
            lines = output.splitlines()
            statuses = [line for line in lines if line.startswith(EXIT_MARKER)]
            if (len(statuses) != 1 or not lines or statuses[0] != lines[-1]
                    or not re.fullmatch(re.escape(EXIT_MARKER) + r"(?:0|[1-9][0-9]{0,2})", statuses[0])):
                raise StageError("SSH_STATE_UNCONFIRMED", "preflight exit status missing or invalid; no retry")
            rc = int(statuses[0][len(EXIT_MARKER):])
            if rc > 255:
                raise StageError("SSH_STATE_UNCONFIRMED", "preflight exit status invalid; no retry")
            run["preflight_returncode"] = rc
            tsv = transport.shell("cat " + shlex.quote(remote + "/results.tsv"), args.timeout).stdout
        else:
            adb = resolve_adb(args.adb)
            if adb is None:
                raise PreflightError("adb executable not found")
            port = existing_adb_port(args.adb_port)
            if port is None:
                if not args.allow_start_adb:
                    raise PreflightError("adb daemon is not already listening")
                port = args.adb_port if args.adb_port is not None else 5037
            run["adb_port"] = port

            def call(name, adb_args):
                result = run_adb(adb, args.serial, port, adb_args, args.timeout)
                write_new(logs / (name + ".out"), result.stdout)
                write_new(logs / (name + ".err"), result.stderr)
                return result

            def require(name, adb_args):
                result = call(name, adb_args)
                if result.returncode != 0:
                    raise PreflightError(name + " failed")
                return result

            remote = require("mktemp", ["shell", "mktemp -d /data/local/tmp/mt6639-release-preflight-XXXXXX"]).stdout.strip()
            if not REMOTE_RE.fullmatch(remote):
                raise PreflightError("unsafe remote path")
            run["remote_directory"] = remote
            require("push", ["push", str(SCRIPT), remote + "/run.sh"])
            require("chmod", ["shell", "chmod 0700 " + remote + "/run.sh"])
            uid = require("uid", ["shell", "id -u"]).stdout.strip()
            run["remote_uid"] = uid
            script_command = "sh " + remote + "/run.sh " + remote
            run["execution_mode"] = "adb_root" if uid == "0" else "su_c"
            ran = call("run", ["shell", script_command] if uid == "0" else ["shell", "su", "-c", script_command])
            rc = ran.returncode
            run.update(adb_returncode=rc, preflight_returncode=rc)
            tsv = require("results", ["shell", "cat " + remote + "/results.tsv"]).stdout
        write_new(out / "results.tsv", tsv)
        reason, boot, failed = validate_results(tsv, rc, args.previous_boot_id,
                                                transport.boot_id if transport else None)
        run.update(status="failed" if reason else "complete", failure_reason=reason,
                   boot_id=boot, failed_rows=failed, results_path="results.tsv",
                   fresh_boot=boot != args.previous_boot_id if boot and args.previous_boot_id else None)
    except StageError as error:
        run.update(status="unconfirmed" if error.code == "SSH_STATE_UNCONFIRMED" else "failed",
                   failure_reason=error.code)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        run.update(status="unconfirmed", failure_reason="transport_or_local_io_unconfirmed")
    except PreflightError as error:
        run["failure_reason"] = str(error)
    write_new(out / "run.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if run["status"] != "complete":
        print("FAIL " + str(run["failure_reason"]), file=sys.stderr)
    return 0 if run["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
