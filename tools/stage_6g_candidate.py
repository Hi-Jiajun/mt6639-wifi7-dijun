#!/usr/bin/env python3
"""Plan and safely stage the pinned MT6639 6GHz KernelSU candidate.

The default mode is read-only: it validates the release manifest and prints the
ordered staging plan. Device mutation requires an explicit --execute flag.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import zipfile


MAX_MANIFEST = 1024 * 1024
MAX_ARTIFACT = 128 * 1024 * 1024
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
REMOTE_TASK = re.compile(r"^/data/local/tmp/mt6639-stage-[A-Za-z0-9]+$")
EXPECTED_FINGERPRINT = (
    "Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys"
)
EXPECTED_KERNEL = "6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k"
STOCK_CFG_SHA = "e4dcd846f31077864932b499d462cfd6a8ca1befc6386f502813e0ba053cc154"


class StageError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Candidate:
    artifact_id: str
    version: str
    version_code: int
    sha256: str
    path: Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def module_prop(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        text = archive.read("module.prop").decode("utf-8")
    except (KeyError, UnicodeDecodeError) as error:
        raise StageError("MODULE_PROP_INVALID", "module.prop missing or not UTF-8") from error
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise StageError("MODULE_PROP_INVALID", "module.prop malformed")
        values[key] = value
    return values


def load_candidate(manifest_path: Path, root: Path) -> Candidate:
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST:
        raise StageError("MANIFEST_TOO_LARGE", "release manifest exceeds size limit")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StageError("MANIFEST_INVALID", "release manifest is not valid UTF-8 JSON") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != "mt6639-release-candidate-v1":
        raise StageError("MANIFEST_INVALID", "unsupported release manifest schema")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise StageError("MANIFEST_INVALID", "artifacts missing")
    matches = [item for item in artifacts
               if isinstance(item, dict) and item.get("id") == "mt6639-6g"]
    if len(matches) != 1:
        raise StageError("CANDIDATE_MISSING", "exactly one mt6639-6g artifact is required")
    artifact = matches[0]
    relative = artifact.get("path")
    expected_hash = artifact.get("sha256")
    if (not isinstance(relative, str) or not SAFE_PATH.fullmatch(relative)
            or relative.startswith("/") or ".." in Path(relative).parts):
        raise StageError("CANDIDATE_PATH_INVALID", "candidate path is unsafe")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise StageError("CANDIDATE_HASH_INVALID", "candidate SHA256 is invalid")
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise StageError("CANDIDATE_MISSING", "candidate is not a regular non-symlink file")
    if path.stat().st_size > MAX_ARTIFACT:
        raise StageError("CANDIDATE_TOO_LARGE", "candidate exceeds size limit")
    actual_hash = digest(path)
    if actual_hash != expected_hash:
        raise StageError("CANDIDATE_HASH_MISMATCH", "candidate SHA256 differs from manifest")
    try:
        with zipfile.ZipFile(path) as archive:
            values = module_prop(archive)
    except zipfile.BadZipFile as error:
        raise StageError("CANDIDATE_ZIP_INVALID", "candidate is not a valid ZIP") from error
    expected_values = {
        "id": artifact.get("module_id"),
        "version": artifact.get("version"),
        "versionCode": str(artifact.get("version_code")),
    }
    for key, expected in expected_values.items():
        if values.get(key) != expected:
            raise StageError("CANDIDATE_METADATA_MISMATCH", f"module.prop {key} differs")
    return Candidate(
        artifact_id="mt6639-6g",
        version=str(artifact["version"]),
        version_code=int(artifact["version_code"]),
        sha256=actual_hash,
        path=path,
    )


def plan_steps(candidate: Candidate) -> list[dict[str, str]]:
    return [
        {"id": "verify_device",
         "command": "verify uid0, fingerprint, kernel, KernelSU and current module"},
        {"id": "backup_active_and_runtime",
         "command": "tar /data/adb/modules/mt6639-6g and /data/adb/mt6639-6g into a root-only task directory"},
        {"id": "restore_old_transaction",
         "command": "control.sh disable with a bounded timeout"},
        {"id": "verify_restored_stock",
         "command": "require state=restored, active_tx=none and stock wifi.cfg"},
        {"id": "push_candidate",
         "command": f"push {candidate.path.name} to the root-only task directory"},
        {"id": "verify_remote_candidate",
         "command": f"require remote SHA256 {candidate.sha256}"},
        {"id": "install_to_modules_update",
         "command": "ksud module install REMOTE_CANDIDATE"},
        {"id": "verify_staged_module",
         "command": "verify modules_update version, versionCode and SHA256SUMS"},
        {"id": "stop_before_reboot",
         "command": "return staged_requires_reboot without rebooting"},
    ]


def run_adb(adb: str, serial: str, port: int, args: list[str],
            env: dict[str, str], timeout: int = 30) -> subprocess.CompletedProcess:
    command = [adb, "-P", str(port), "-s", serial] + args
    try:
        return subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StageError("ADB_EXECUTION_FAILED", "adb command failed or timed out") from error


def require_ok(result: subprocess.CompletedProcess, code: str) -> str:
    if result.returncode != 0:
        raise StageError(code, result.stderr.strip() or "adb command returned nonzero")
    return result.stdout.strip()


BOOT_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ANDROID_PATH = "/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin"


def load_ssh_command(path: Path) -> list[str]:
    """Accept only the pinned, non-interactive direct OpenSSH argv schema.

    Do not read the private key. No ssh config, proxy, forwarding, remote
    command or option outside this narrow schema is accepted.
    """
    def invalid() -> StageError:
        return StageError("SSH_CONFIG_INVALID", "invalid pinned SSH command configuration")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise invalid()
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise invalid()
        argv = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as error:
        raise invalid() from error
    if (not isinstance(argv, list) or not 2 <= len(argv) <= 32
            or any(not isinstance(v, str) or not v or len(v) > 4096
                   or any(c in v for c in "\x00\r\n") for v in argv)):
        raise invalid()
    executable = Path(argv[0])
    if (not executable.is_absolute() or executable.name != "ssh"
            or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise invalid()
    options: dict[str, str] = {}
    i = 1
    while i < len(argv) - 1:
        flag = argv[i]
        if flag not in ("-F", "-o", "-i", "-p") or i + 1 >= len(argv) - 1:
            raise invalid()
        value = argv[i + 1]
        key = flag
        if flag == "-o":
            key, sep, value = value.partition("=")
            if not sep:
                raise invalid()
        if key in options:
            raise invalid()
        options[key] = value
        i += 2
    required = {"-F", "-i", "-p", "BatchMode", "ConnectTimeout",
                "StrictHostKeyChecking", "UserKnownHostsFile"}
    if (set(options) != required or options["-F"] != "/dev/null"
            or options["BatchMode"] != "yes"
            or options["ConnectTimeout"] != "5"
            or options["StrictHostKeyChecking"] != "yes"
            or not re.fullmatch(r"[0-9]{1,5}", options["-p"])
            or not 1 <= int(options["-p"]) <= 65535
            or not re.fullmatch(r"u0_a[0-9]+@(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9a-fA-F:]+\])", argv[-1])):
        raise invalid()
    for key in ("-i", "UserKnownHostsFile"):
        value = options[key]
        if (not Path(value).is_absolute() or value == "/dev/null"
                or any(c.isspace() for c in value) or any(c in value for c in "%~")):
            raise invalid()
    # These override agent/keyboard-interactive defaults even with -F /dev/null.
    return argv[:-1] + ["-T", "-o", "PasswordAuthentication=no", "-o",
                       "KbdInteractiveAuthentication=no", "-o", "IdentitiesOnly=yes",
                       "-o", "IdentityAgent=none", "-o", "ConnectionAttempts=1",
                       "-o", "ControlMaster=no", "-o", "ControlPath=none",
                       "-o", "GlobalKnownHostsFile=/dev/null", argv[-1]]


class ADBTransport:
    def __init__(self, adb: str, serial: str, port: int, env: dict[str, str]):
        self.adb, self.serial, self.port, self.env = adb, serial, port, env

    def shell(self, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return run_adb(self.adb, self.serial, self.port,
                       ["shell", command], self.env, timeout)

    def push(self, source: Path, target: str) -> subprocess.CompletedProcess:
        return run_adb(self.adb, self.serial, self.port,
                       ["push", str(source), target], self.env, 90)


class SSHTransport:
    """Ordinary Android app SSH -> system su -> KSU, for every operation.

    A lost connection may follow a completed write. Never retry/replay it;
    report unknown device state and require a fresh operator assessment.
    """
    def __init__(self, command: list[str], serial: str, env: dict[str, str]):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", serial):
            raise StageError("DEVICE_INVALID", "invalid expected device serial")
        self.command, self.serial, self.env = command, serial, env
        self.boot_id: str | None = None
        self.enforcing_required = False

    def wrapper(self, command: str) -> str:
        expected_boot = shlex.quote(self.boot_id or "")
        expected_serial = shlex.quote(self.serial)
        # Source identity is tested before su, and the boot is checked again
        # inside KSU. All commands (including streamed cat) share this gate.
        common = f"""PATH={ANDROID_PATH}; export PATH
serial=$(getprop ro.serialno)
[ "$serial" = {expected_serial} ] || exit 85
boot=$(cat /proc/sys/kernel/random/boot_id)
[ -n "$boot" ] || exit 86
expected_boot={expected_boot}
[ -z "$expected_boot" ] || [ "$boot" = "$expected_boot" ] || exit 86
"""
        root = f"""set -eu
PATH={ANDROID_PATH}; export PATH
[ "$(id -u)" = 0 ] || exit 83
[ "$(id -Z)" = u:r:ksu:s0 ] || exit 84
{common}"""
        if self.enforcing_required:
            root += '[ "$(getenforce)" = Enforcing ] && [ "$(cat /sys/fs/selinux/enforce)" = 1 ] || exit 87\n'
        root += 'printf "__MT6639_GATE__ %s %s\\n" "$boot" "$serial"\n' + command
        source = f"""set -eu
PATH={ANDROID_PATH}; export PATH
uid=$(id -u)
case "$uid" in ''|*[!0-9]*) exit 81;; esac
[ "${{#uid}}" -le 9 ] && [ "$uid" -ge 10000 ] || exit 81
ctx=$(id -Z)
domain=${{ctx#u:r:}}
[ "$domain" != "$ctx" ] || exit 82
domain=${{domain%%:*}}
case "$domain" in
untrusted_app) ;;
untrusted_app_*) suffix=${{domain#untrusted_app_}}; case "$suffix" in ''|*[!0-9]*) exit 82;; esac;;
*) exit 82;;
esac
case "$ctx" in u:r:"$domain":s0|u:r:"$domain":s0:*) ;; *) exit 82;; esac
{common}exec /system/bin/su -c {shlex.quote('/system/bin/sh -c ' + shlex.quote(root))}
"""
        return "/system/bin/sh -c " + shlex.quote(source)

    def _run(self, command: str, timeout: int, stdin=None, *,
             _initial: bool = False) -> subprocess.CompletedProcess:
        if self.boot_id is None and not _initial:
            # Pin a legal boot using a read-only no-op before any payload.
            self._run(":", timeout, subprocess.DEVNULL, _initial=True)
        try:
            result = subprocess.run(
                self.command + [self.wrapper(command)], stdin=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StageError("SSH_STATE_UNCONFIRMED",
                             "SSH transport failed or timed out; device state unconfirmed; no retry") from error
        if result.returncode != 0:
            code = {81: "SSH_SOURCE_UID_INVALID", 82: "SSH_SOURCE_CONTEXT_INVALID",
                    83: "ROOT_REQUIRED", 84: "KSU_REQUIRED", 85: "DEVICE_INVALID",
                    86: "BOOT_ID_INVALID", 87: "SELINUX_INVALID"}.get(result.returncode)
            if code and not result.stdout.startswith(b"__MT6639_GATE__ "):
                raise StageError(code, "SSH identity/device/boot/SELinux gate rejected operation")
            # Do not disclose ssh stderr (which can include key paths or options).
            raise StageError("SSH_STATE_UNCONFIRMED",
                             "SSH command failed; device state unconfirmed; no retry")
        output = result.stdout.decode("utf-8", errors="replace")
        header, separator, payload = output.partition("\n")
        fields = header.split()
        if (not separator or len(fields) != 3 or fields[0] != "__MT6639_GATE__"
                or not BOOT_UUID.fullmatch(fields[1]) or fields[2] != self.serial
                or (self.boot_id is not None and fields[1] != self.boot_id)):
            raise StageError("SSH_GATE_INVALID", "SSH gate metadata invalid or changed")
        self.boot_id = fields[1]
        return subprocess.CompletedProcess([], 0, payload, "")

    def shell(self, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return self._run(command, timeout, stdin=subprocess.DEVNULL)

    def push(self, source: Path, target: str) -> subprocess.CompletedProcess:
        if not self.boot_id or not self.enforcing_required:
            raise StageError("SSH_GATE_INVALID", "upload requires a pinned boot and execution gate")
        with source.open("rb") as stream:
            return self._run("umask 077; cat > " + shlex.quote(target), 90, stream)


def device_transport(*, adb: str | None, serial: str, port: int,
                     env: dict[str, str], ssh_command_json: Path | None):
    if bool(adb) == bool(ssh_command_json):
        raise StageError("TRANSPORT_INVALID", "select exactly one ADB or SSH transport")
    if ssh_command_json:
        return SSHTransport(load_ssh_command(Path(ssh_command_json)), serial, env)
    return ADBTransport(adb, serial, port, env)


def probe_device(candidate: Candidate, *, adb: str | None = None, serial: str, port: int = 5038,
                 env: dict[str, str] | None = None, ssh_command_json: Path | None = None,
                 _transport=None) -> dict[str, object]:
    env = env or os.environ.copy()
    transport = _transport or device_transport(adb=adb, serial=serial, port=port,
        env=env, ssh_command_json=ssh_command_json)
    values: dict[str, str] = {}
    failures: list[str] = []

    def read(name: str, command: str) -> str:
        result = transport.shell(command)
        if result.returncode != 0:
            failures.append(name + "_unreadable")
            return ""
        values[name] = result.stdout.strip()
        return values[name]

    uid = read("uid", "id -u")
    if uid != "0":
        failures.append("root_uid")
    ksu_id = read("ksu_id", "id" if isinstance(transport, SSHTransport) else "su -c id")
    if "context=u:r:ksu:s0" not in ksu_id:
        failures.append("ksu_context")
    fingerprint = read("fingerprint", "getprop ro.build.fingerprint")
    if fingerprint != EXPECTED_FINGERPRINT:
        failures.append("fingerprint")
    kernel = read("kernel", "uname -r")
    if kernel != EXPECTED_KERNEL:
        failures.append("kernel")
    selinux = read("selinux", "getenforce")
    if selinux != "Enforcing":
        failures.append("selinux_enforcing")
    if isinstance(transport, SSHTransport):
        if read("selinux_enforce", "cat /sys/fs/selinux/enforce") != "1":
            failures.append("selinux_enforce")
    prop = read("module_prop", "cat /data/adb/modules/mt6639-6g/module.prop")
    if "id=mt6639-6g" not in prop or "version=" not in prop:
        failures.append("current_module")
    boot_id = read("boot_id", "cat /proc/sys/kernel/random/boot_id")
    if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            boot_id):
        failures.append("boot_id")
    return {
        "schema": "mt6639-stage-6g-probe-v1",
        "ready": not failures,
        "failures": failures,
        "uid": uid,
        "ksu_id": ksu_id,
        "fingerprint": fingerprint,
        "kernel": kernel,
        "selinux": selinux,
        "boot_id": boot_id,
        "current_module_version": next(
            (line.removeprefix("version=") for line in prop.splitlines()
             if line.startswith("version=")), None),
        "candidate_version": candidate.version,
        "candidate_sha256": candidate.sha256,
    }


def postboot_probe(candidate: Candidate, *, adb: str | None = None, serial: str, port: int = 5038,
                   env: dict[str, str] | None = None, ssh_command_json: Path | None = None) -> dict[str, object]:
    env = env or os.environ.copy()
    transport = device_transport(adb=adb, serial=serial, port=port,
        env=env, ssh_command_json=ssh_command_json)
    probe = probe_device(candidate, adb=adb, serial=serial, port=port, env=env,
                         _transport=transport)
    failures = list(probe["failures"])

    def shell(command: str) -> subprocess.CompletedProcess:
        return transport.shell(command)

    prop_result = shell("cat /data/adb/modules/mt6639-6g/module.prop")
    prop = prop_result.stdout if prop_result.returncode == 0 else ""
    version = next(
        (line.removeprefix("version=") for line in prop.splitlines()
         if line.startswith("version=")), None)
    version_code = next(
        (line.removeprefix("versionCode=") for line in prop.splitlines()
         if line.startswith("versionCode=")), None)
    active_matches = (
        version == candidate.version and version_code == str(candidate.version_code))
    if not active_matches:
        failures.append("active_version_mismatch")

    status_result = shell(
        "sh /data/adb/modules/mt6639-6g/bin/control.sh "
        "/data/adb/modules/mt6639-6g status")
    status = status_result.stdout if status_result.returncode == 0 else ""
    state_active = "state=active" in status
    guardian_alive = "guardian=alive" in status
    active_tx = next(
        (line.removeprefix("active_tx=") for line in status.splitlines()
         if line.startswith("active_tx=")), "")
    transaction_active = (
        state_active and guardian_alive and active_tx.startswith(
            "/data/adb/mt6639-6g/transactions/t-"))
    if not transaction_active:
        failures.append("active_transaction_missing")

    sixg_samples: list[int | None] = []
    target_samples: list[int | None] = []
    for _ in range(3):
        phy_result = shell("iw phy")
        if phy_result.returncode != 0:
            sixg_samples.append(None)
            target_samples.append(None)
            failures.append("iw_phy_unreadable")
            break
        channels = []
        targets = []
        for line in phy_result.stdout.splitlines():
            if "(disabled)" in line:
                continue
            for value in re.findall(r"(?<!\d)(\d{4,5})(?!\d)", line):
                number = int(value)
                if 5925 <= number <= 7125:
                    channels.append(number)
                    if number == 6135:
                        targets.append(number)
        sixg_samples.append(len(set(channels)))
        target_samples.append(len(set(targets)))
    if None in sixg_samples:
        sixg_channels = None
    elif all(sample == 0 for sample in sixg_samples):
        sixg_channels = 0
    elif all(isinstance(sample, int) and sample > 0 for sample in sixg_samples):
        sixg_channels = min(sample for sample in sixg_samples if sample is not None)
    else:
        sixg_channels = None
        failures.append("iw_phy_unstable")
    if None in target_samples:
        sixg_6135_channels = None
    elif all(sample == 0 for sample in target_samples):
        sixg_6135_channels = 0
    elif all(isinstance(sample, int) and sample > 0 for sample in target_samples):
        sixg_6135_channels = min(
            sample for sample in target_samples if sample is not None)
    else:
        sixg_6135_channels = None
        failures.append("iw_phy_6135_unstable")
    refresh_required = (
        not failures and active_matches and transaction_active
        and sixg_6135_channels == 0)
    return {
        "schema": "mt6639-stage-6g-postboot-probe-v1",
        "ready": not failures,
        "failures": failures,
        "active_version": version,
        "active_version_code": int(version_code) if version_code and version_code.isdigit() else None,
        "active_version_matches": active_matches,
        "transaction_active": transaction_active,
        "sixg_channels": sixg_channels,
        "sixg_samples": sixg_samples,
        "sixg_6135_channels": sixg_6135_channels,
        "sixg_6135_samples": target_samples,
        "refresh_required": refresh_required,
        "candidate_version": candidate.version,
        "candidate_sha256": candidate.sha256,
    }


def execute(candidate: Candidate, *, adb: str | None = None, serial: str, port: int = 5038,
            env: dict[str, str] | None = None, ssh_command_json: Path | None = None) -> dict[str, object]:
    env = env or os.environ.copy()

    def shell(command: str, code: str, timeout: int = 30) -> str:
        return require_ok(transport.shell(command, timeout), code)

    transport = device_transport(adb=adb, serial=serial, port=port,
        env=env, ssh_command_json=ssh_command_json)
    probe = probe_device(candidate, adb=adb, serial=serial, port=port, env=env,
                         _transport=transport)
    if not probe["ready"]:
        first = str(probe["failures"][0])
        code = {
            "root_uid": "ROOT_REQUIRED",
            "ksu_context": "KSU_REQUIRED",
            "fingerprint": "DEVICE_INVALID",
            "kernel": "DEVICE_INVALID",
            "selinux_enforcing": "SELINUX_INVALID",
            "selinux_enforce": "SELINUX_INVALID",
            "current_module": "CURRENT_MODULE_INVALID",
            "boot_id": "BOOT_ID_INVALID",
        }.get(first, "DEVICE_PROBE_FAILED")
        raise StageError(code, "execution preconditions are not satisfied")

    if isinstance(transport, SSHTransport):
        transport.enforcing_required = True

    remote = shell(
        "mktemp -d /data/local/tmp/mt6639-stage-XXXXXX", "REMOTE_TASK_FAILED")
    if not REMOTE_TASK.fullmatch(remote):
        raise StageError("REMOTE_TASK_INVALID", "remote task path is unsafe")
    backup = remote + "/backup.tar.gz"
    shell(
        f"tar -C /data/adb -czf {backup} modules/mt6639-6g mt6639-6g",
        "BACKUP_FAILED", timeout=60)
    backup_hash = shell(f"sha256sum {backup}", "BACKUP_HASH_FAILED")
    if not re.match(r"^[0-9a-f]{64}\s+", backup_hash):
        raise StageError("BACKUP_HASH_FAILED", "backup SHA256 is invalid")
    backup_listing = shell(
        f"tar -tzf {backup}", "BACKUP_VERIFY_FAILED", timeout=60)
    if "modules/mt6639-6g/module.prop" not in backup_listing.splitlines():
        raise StageError(
            "BACKUP_VERIFY_FAILED", "backup does not contain the active module")

    shell(
        "sh /data/adb/modules/mt6639-6g/bin/control.sh "
        "/data/adb/modules/mt6639-6g disable",
        "RESTORE_FAILED", timeout=200)
    status = shell(
        "sh /data/adb/modules/mt6639-6g/bin/control.sh "
        "/data/adb/modules/mt6639-6g status",
        "RESTORE_STATUS_FAILED")
    if "state=restored" not in status or "active_tx=none" not in status:
        raise StageError("RESTORE_INCOMPLETE", "old transaction is not fully restored")
    cfg_hash = shell("sha256sum /odm/firmware/wifi.cfg", "STOCK_CFG_CHECK_FAILED")
    if not cfg_hash.startswith(STOCK_CFG_SHA + " "):
        raise StageError("STOCK_CFG_MISMATCH", "wifi.cfg did not return to stock")

    remote_candidate = remote + "/candidate.zip"
    require_ok(transport.push(candidate.path, remote_candidate), "CANDIDATE_PUSH_FAILED")
    remote_hash = shell(
        f"sha256sum {remote_candidate}", "REMOTE_HASH_FAILED", timeout=60)
    if not remote_hash.startswith(candidate.sha256 + " "):
        raise StageError("REMOTE_HASH_MISMATCH", "remote candidate SHA256 differs")
    shell(
        f"/data/adb/ksud module install {remote_candidate}",
        "MODULE_INSTALL_FAILED", timeout=120)
    staged_prop = shell(
        "cat /data/adb/modules_update/mt6639-6g/module.prop",
        "STAGED_MODULE_INVALID")
    for expected in (
            "id=mt6639-6g", f"version={candidate.version}",
            f"versionCode={candidate.version_code}"):
        if expected not in staged_prop:
            raise StageError("STAGED_MODULE_INVALID", "staged module metadata differs")
    shell(
        "cd /data/adb/modules_update/mt6639-6g && sha256sum -c SHA256SUMS",
        "STAGED_HASH_FAILED", timeout=60)
    return {
        "schema": "mt6639-stage-6g-result-v1",
        "status": "staged_requires_reboot",
        "candidate_sha256": candidate.sha256,
        "candidate_version": candidate.version,
        "remote_directory": remote,
        "backup_sha256": backup_hash.split()[0],
        "reboot_performed": False,
        "refresh_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", default=".")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--postboot-probe", action="store_true")
    transport_arg = parser.add_mutually_exclusive_group()
    transport_arg.add_argument("--adb")
    transport_arg.add_argument("--ssh-command-json", type=Path)
    parser.add_argument("--adb-port", type=int)
    parser.add_argument("--serial")
    args = parser.parse_args(argv)
    if args.execute or args.probe or args.postboot_probe:
        if not (args.adb or args.ssh_command_json) or not args.serial:
            parser.error("device mode requires --serial and either --adb or --ssh-command-json")
    if args.ssh_command_json and args.adb_port is not None:
        parser.error("--adb-port cannot be combined with --ssh-command-json")
    try:
        if args.ssh_command_json:
            load_ssh_command(args.ssh_command_json)
        candidate = load_candidate(Path(args.manifest), Path(args.root).resolve())
        if args.execute:
            result = execute(
                candidate, adb=args.adb, serial=args.serial, port=args.adb_port if args.adb_port is not None else 5038,
                ssh_command_json=args.ssh_command_json)
        elif args.probe:
            result = probe_device(
                candidate, adb=args.adb, serial=args.serial, port=args.adb_port if args.adb_port is not None else 5038,
                ssh_command_json=args.ssh_command_json)
        elif args.postboot_probe:
            result = postboot_probe(
                candidate, adb=args.adb, serial=args.serial, port=args.adb_port if args.adb_port is not None else 5038,
                ssh_command_json=args.ssh_command_json)
        else:
            result = {
                "schema": "mt6639-stage-6g-plan-v1",
                "candidate": asdict(candidate) | {"path": str(candidate.path)},
                "steps": plan_steps(candidate),
                "execution_mode": "plan_only",
            }
    except (StageError, OSError) as error:
        if isinstance(error, StageError):
            print(f"FAIL {error.code} {error.detail}", file=sys.stderr)
        else:
            print("FAIL LOCAL_IO", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if (args.probe or args.postboot_probe) and result.get("ready") is not True:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
