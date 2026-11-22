#!/usr/bin/env python3
"""Remote tablet single-session orchestrator over USB ADB SSH-tunnel or direct SSH.

Supports Xiaomi Pad 7S Pro (violin), Xiaomi Pad 7 Ultra (jinghu), and Xiaomi 15S Pro (dijun).
Designed for NetEase UU Remote / ToDesk / Sunlogin remote control paired with OpenSSH ADB
forwarding (-R 15037:127.0.0.1:5037) or direct app SSH, enabling both operator and AI
assistant live diagnosis and execution without RustDesk.

Read-only subcommands (`preflight`, `diagnose`, dry-run `stage`/`bounded-trial`/`restore-6g`/
`cleanup`, and `collect-evidence --out`) never mutate radio or module state; mutation requires
explicit `--execute` and never modifies checksummed module files or triggers an OS reboot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "release/mt6639-candidate-20261026.json"
SUPPORTED_DEVICES = {"dijun", "violin", "jinghu"}
PRIMARY_ARTIFACT_IDS = ("mt6639-6g", "mt6639-bt", "mt6639-hotspot", "mt6639-ui-lsposed")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_REL_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
MAX_TIMEOUT = 120.0
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


class SessionError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ssh_command(path: Path) -> list[str]:
    """Validate pinned non-interactive OpenSSH argv JSON configuration."""
    import stat as _stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not _stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise SessionError("SSH_CONFIG_INVALID", "invalid SSH config file")
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise SessionError("SSH_CONFIG_INVALID", "SSH config file oversized")
        argv = json.loads(raw.decode("utf-8"))
    except SessionError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise SessionError("SSH_CONFIG_INVALID", "cannot parse SSH config") from exc
    if (not isinstance(argv, list) or not 2 <= len(argv) <= 32
            or any(not isinstance(v, str) or not v or len(v) > 4096
                   or any(c in v for c in "\x00\r\n") for v in argv)):
        raise SessionError("SSH_CONFIG_INVALID", "invalid SSH argv entries")
    executable = Path(argv[0])
    if (not executable.is_absolute() or executable.name != "ssh"
            or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise SessionError("SSH_CONFIG_INVALID", "invalid ssh executable path")
    options: dict[str, str] = {}
    i = 1
    while i < len(argv) - 1:
        flag = argv[i]
        if flag not in ("-F", "-o", "-i", "-p") or i + 1 >= len(argv) - 1:
            raise SessionError("SSH_CONFIG_INVALID", "unsupported ssh flag")
        val = argv[i + 1]
        key = flag
        if flag == "-o":
            key, sep, val = val.partition("=")
            if not sep:
                raise SessionError("SSH_CONFIG_INVALID", "malformed -o option")
        if key in options:
            raise SessionError("SSH_CONFIG_INVALID", "duplicate ssh option")
        options[key] = val
        i += 2
    required = {"-F", "-i", "-p", "BatchMode", "ConnectTimeout",
                "StrictHostKeyChecking", "UserKnownHostsFile"}
    if (set(options) != required or options["-F"] != "/dev/null"
            or options["BatchMode"] != "yes"
            or options["StrictHostKeyChecking"] != "yes"
            or not re.fullmatch(r"[0-9]{1,5}", options["ConnectTimeout"])
            or not 1 <= int(options["ConnectTimeout"]) <= 60
            or not re.fullmatch(r"[0-9]{1,5}", options["-p"])
            or not 1 <= int(options["-p"]) <= 65535
            or argv[-1].startswith("root@")
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+@(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9a-fA-F:]+\])", argv[-1])):
        raise SessionError("SSH_CONFIG_INVALID", "invalid pinned SSH options or target")
    return argv


class Transport:
    def __init__(self, *, adb: str | None = None, adb_port: int | None = None,
                 serial: str = "", ssh_argv: list[str] | None = None, timeout: float = 20.0):
        self.adb = adb
        self.adb_port = adb_port
        self.serial = serial
        self.ssh_argv = ssh_argv
        self.timeout = timeout

    def _adb_base(self) -> list[str]:
        argv = [self.adb]
        if self.adb_port is not None:
            argv.extend(["-P", str(self.adb_port)])
        argv.extend(["-s", self.serial])
        return argv

    def shell(self, cmd: str, *, timeout: float | None = None,
              input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        t = timeout if timeout is not None else self.timeout
        if self.ssh_argv is not None:
            remote = f"PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin:$PATH; {cmd}"
            argv = list(self.ssh_argv) + [remote]
        else:
            argv = self._adb_base() + ["shell", cmd]
        try:
            if input_bytes is not None:
                return subprocess.run(argv, input=input_bytes, capture_output=True, timeout=t)
            return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=t)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SessionError("TRANSPORT_FAILED", f"transport error: {type(exc).__name__}") from exc

    def su_shell(self, cmd: str, *, timeout: float | None = None,
                 input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        return self.shell("su -c " + shlex.quote(cmd), timeout=timeout, input_bytes=input_bytes)

    def push_file(self, local_path: Path, remote_dest: str, tmp_upload: str,
                  *, timeout: float = 60.0) -> None:
        if not remote_dest.startswith("/data/local/tmp/mt6639-remote-stage-"):
            raise SessionError("UNSAFE_REMOTE_PATH", f"disallowed remote dest: {remote_dest}")
        if self.ssh_argv is not None:
            cp = self.su_shell(
                f"cat > {shlex.quote(remote_dest)} && chmod 0600 {shlex.quote(remote_dest)}",
                timeout=timeout,
                input_bytes=local_path.read_bytes(),
            )
            if cp.returncode != 0:
                raise SessionError("PUSH_FAILED", f"ssh upload failed for {local_path.name}")
            return
        if not tmp_upload.startswith("/data/local/tmp/mt6639-adb-up-"):
            raise SessionError("UNSAFE_REMOTE_PATH", "disallowed adb upload scratch path")
        try:
            cp_push = subprocess.run(
                self._adb_base() + ["push", str(local_path), tmp_upload],
                capture_output=True, text=True, errors="replace", timeout=timeout,
            )
            if cp_push.returncode != 0:
                raise SessionError("PUSH_FAILED", f"adb push failed for {local_path.name}")
            cp_mv = self.su_shell(
                f"cp {shlex.quote(tmp_upload)} {shlex.quote(remote_dest)} && chmod 0600 {shlex.quote(remote_dest)}",
                timeout=timeout,
            )
            if cp_mv.returncode != 0:
                raise SessionError("PUSH_FAILED", f"root stage copy failed for {local_path.name}")
        finally:
            try:
                self.shell(f"rm -f {shlex.quote(tmp_upload)}", timeout=10.0)
            except SessionError:
                pass


def _iter_rows(path: Path) -> list[list[str]]:
    if path.is_symlink() or not path.is_file():
        raise SessionError("COMPAT_TABLE_MISSING", f"missing non-symlink table: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        rows.append(s.split())
    return rows


def load_tables(repo: Path) -> dict:
    sixg_rows = []
    for parts in _iter_rows(repo / "modules/mt6639-6g/compatibility.conf"):
        if len(parts) < 5:
            raise SessionError("COMPAT_TABLE_INVALID", "mt6639-6g table row malformed")
        sixg_rows.append({
            "profile": parts[0],
            "devices": set(parts[1].split(",")),
            "kernel": parts[2],
            "cfg_sha": parts[3],
            "ko_shas": set(parts[4:]),
        })
    bt_rows = []
    for parts in _iter_rows(repo / "modules/mt6639-bt/compatibility.conf"):
        if len(parts) != 3:
            raise SessionError("COMPAT_TABLE_INVALID", "mt6639-bt table row malformed")
        bt_rows.append({
            "profile": parts[0],
            "devices": set(parts[1].split(",")),
            "apk_sha": parts[2],
        })
    hs_rows = {"ko": [], "tethering_jar": []}
    for parts in _iter_rows(repo / "modules/mt6639-hotspot/compatibility.conf"):
        if len(parts) != 3 or parts[0] not in hs_rows:
            raise SessionError("COMPAT_TABLE_INVALID", "mt6639-hotspot table row malformed")
        hs_rows[parts[0]].append({"devices": set(parts[1].split(",")), "sha": parts[2]})
    return {"6g": sixg_rows, "bt": bt_rows, "hotspot": hs_rows}


def load_primary_artifacts(manifest_path: Path, repo: Path) -> list[dict]:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SessionError("MANIFEST_MISSING", f"manifest missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionError("MANIFEST_INVALID", "cannot parse manifest JSON") from exc
    if manifest.get("schema") != "mt6639-release-candidate-v1":
        raise SessionError("MANIFEST_INVALID", "unsupported manifest schema")
    by_id = {
        a.get("id"): a for a in manifest.get("artifacts", [])
        if isinstance(a, dict) and isinstance(a.get("id"), str)
    }
    selected = []
    for aid in PRIMARY_ARTIFACT_IDS:
        art = by_id.get(aid)
        if not art:
            raise SessionError("ARTIFACT_MISSING", f"manifest missing primary artifact {aid}")
        rel = art.get("path", "")
        expected = art.get("sha256", "")
        if (not isinstance(rel, str) or not SAFE_REL_RE.fullmatch(rel)
                or rel.startswith("/") or ".." in Path(rel).parts
                or not SHA256_RE.fullmatch(expected)):
            raise SessionError("MANIFEST_INVALID", f"unsafe manifest entry for {aid}")
        full = repo / rel
        if full.is_symlink() or not full.is_file():
            raise SessionError("ARTIFACT_MISSING", f"artifact file missing: {rel}")
        if full.stat().st_size > MAX_ARTIFACT_BYTES:
            raise SessionError("ARTIFACT_TOO_LARGE", f"artifact too large: {rel}")
        actual = sha256_file(full)
        if actual != expected:
            raise SessionError("ARTIFACT_HASH_MISMATCH", f"sha256 mismatch for {rel}")
        selected.append({
            "id": aid,
            "kind": art.get("kind"),
            "version": art.get("version"),
            "version_code": art.get("version_code"),
            "module_id": art.get("module_id"),
            "package": art.get("package"),
            "path": full,
            "rel_path": rel,
            "sha256": actual,
        })
    return selected


PREFLIGHT_SCRIPT = """
echo "SU_ID=$(id 2>/dev/null || true)"
echo "SELINUX=$(getenforce 2>/dev/null || echo unknown)"
echo "SYSFS_ENFORCE=$(cat /sys/fs/selinux/enforce 2>/dev/null || echo unknown)"
echo "DEVICE=$(getprop ro.product.device 2>/dev/null || echo unknown)"
echo "SDK=$(getprop ro.build.version.sdk 2>/dev/null || echo unknown)"
echo "MI_OS=$(getprop ro.mi.os.version.name 2>/dev/null || echo unknown)"
echo "BASEBAND=$(getprop gsm.version.baseband 2>/dev/null || true)"
echo "KERNEL=$(uname -r 2>/dev/null || echo unknown)"
echo "CFG_SHA=$(sha256sum /odm/firmware/wifi.cfg 2>/dev/null | cut -d ' ' -f 1)"
KO_PATH=/vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko
if [ ! -e "$KO_PATH" ] && [ ! -L "$KO_PATH" ]; then
    KO_PATH=/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko
fi
echo "KO_PATH=$KO_PATH"
echo "KO_SHA=$(sha256sum "$KO_PATH" 2>/dev/null | cut -d ' ' -f 1)"
BT_APK=""
BT_COUNT=0
for c in /apex/com.android.bt/app/*/Bluetooth.apk /apex/com.android.bt/app/Bluetooth.apk; do
    [ -f "$c" ] && [ ! -L "$c" ] || continue
    BT_APK=$c
    BT_COUNT=$((BT_COUNT + 1))
done
echo "BT_COUNT=$BT_COUNT"
if [ "$BT_COUNT" = 1 ]; then
    echo "BT_APK_SHA=$(sha256sum "$BT_APK" 2>/dev/null | cut -d ' ' -f 1)"
else
    echo "BT_APK_SHA="
fi
echo "TETHER_JAR_SHA=$(sha256sum /apex/com.android.tethering/javalib/framework-tethering.jar 2>/dev/null | cut -d ' ' -f 1)"
echo "BT_OFFLOAD=$(getprop ro.bluetooth.leaudio_offload.supported 2>/dev/null || true)"
echo "BT_OFFLOAD_DIS=$(getprop persist.bluetooth.leaudio_offload.disabled 2>/dev/null || true)"
WIFI_RAW=$(runcon u:r:shell:s0 sh -c 'cmd wifi status' 2>/dev/null || true)
case "$WIFI_RAW" in
    *"Wifi is enabled"*) echo "WIFI_ENABLED=1" ;;
    *) echo "WIFI_ENABLED=0" ;;
esac
case "$WIFI_RAW" in
    *'Wifi is connected to "'*) echo "WIFI_CONNECTED=1" ;;
    *) echo "WIFI_CONNECTED=0" ;;
esac
KSUD_VER=missing
if [ -x /data/adb/ksud ]; then
    KSUD_VER=$(/data/adb/ksud --version 2>/dev/null | head -1 || echo present)
fi
echo "KSUD_VER=$KSUD_VER"
KSU_MGR=none
for p in me.weishu.kernelsu com.rifsxd.ksunext com.sukisu.ultra; do
    if pm path "$p" >/dev/null 2>&1; then
        KSU_MGR=$p
        break
    fi
done
echo "KSU_MGR=$KSU_MGR"
ZYG_OK=0
if [ -x /data/adb/modules/zygisksu/bin/zygiskd ] &&
   timeout 4 /data/adb/modules/zygisksu/bin/zygiskd status 2>/dev/null | grep -q 'Root:'; then
    ZYG_OK=1
fi
echo "ZYGISK_OK=$ZYG_OK"
LSP_OK=0
if [ -x /data/adb/modules/zygisk_lsposed/lspctl ] &&
   timeout 4 /data/adb/modules/zygisk_lsposed/lspctl status 2>/dev/null | grep -qx 'System server: connected'; then
    LSP_OK=1
fi
echo "LSPOSED_OK=$LSP_OK"
UI_VER=$(dumpsys package com.hiliang.mt6639ui 2>/dev/null | sed -n 's/^[[:space:]]*versionName=//p' | head -1)
echo "UI_PKG_VER=$UI_VER"
for m in mt6639-6g mt6639-bt mt6639-hotspot; do
    live_v=$(sed -n 's/^version=//p' "/data/adb/modules/$m/module.prop" 2>/dev/null | head -1)
    upd_v=$(sed -n 's/^version=//p' "/data/adb/modules_update/$m/module.prop" 2>/dev/null | head -1)
    dis_v=0
    [ -e "/data/adb/modules/$m/disable" ] && dis_v=1
    echo "MOD_LIVE_${m}=${live_v:-absent}"
    echo "MOD_UPD_${m}=${upd_v:-absent}"
    echo "MOD_DIS_${m}=$dis_v"
done
""".strip()


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _eval_preflight_kv(kv: dict[str, str], tables: dict) -> dict:
    su_id = kv.get("SU_ID", "")
    ksu_ok = ("uid=0(root)" in su_id and "context=u:r:ksu:s0" in su_id)
    selinux_ok = (kv.get("SELINUX") == "Enforcing" and kv.get("SYSFS_ENFORCE") == "1")
    device = kv.get("DEVICE", "unknown")
    kernel = kv.get("KERNEL", "unknown")
    cfg_sha = kv.get("CFG_SHA", "")
    ko_sha = kv.get("KO_SHA", "")
    bt_sha = kv.get("BT_APK_SHA", "")
    jar_sha = kv.get("TETHER_JAR_SHA", "")

    if device not in SUPPORTED_DEVICES:
        sixg_status = {"status": "blocked", "profile": None, "reason": "wrong_device"}
        bt_status = {"status": "blocked", "profile": None, "reason": "wrong_device"}
        hs_status = {"status": "blocked", "reason": "wrong_device"}
    else:
        matched_6g = [
            r for r in tables["6g"]
            if device in r["devices"] and r["kernel"] == kernel
        ]
        if not matched_6g:
            sixg_status = {"status": "needs_rom_onboard", "profile": None, "reason": "kernel_unlisted"}
        else:
            row = matched_6g[0]
            if cfg_sha != row["cfg_sha"]:
                sixg_status = {"status": "needs_rom_onboard", "profile": row["profile"], "reason": "cfg_sha_unlisted"}
            elif ko_sha not in row["ko_shas"]:
                sixg_status = {"status": "needs_rom_onboard", "profile": row["profile"], "reason": "ko_sha_unlisted"}
            else:
                sixg_status = {
                    "status": "ready",
                    "profile": row["profile"],
                    "legacy_66_fallback": kernel.startswith("6.6."),
                    "reason": "matched",
                }

        matched_bt = [r for r in tables["bt"] if device in r["devices"] and r["apk_sha"] == bt_sha]
        if matched_bt and kv.get("BT_OFFLOAD") == "true" and kv.get("BT_OFFLOAD_DIS") == "false":
            bt_status = {"status": "ready", "profile": matched_bt[0]["profile"], "reason": "matched"}
        elif not matched_bt:
            bt_status = {"status": "needs_rom_onboard", "profile": None, "reason": "bt_apk_sha_unlisted"}
        else:
            bt_status = {"status": "blocked", "profile": matched_bt[0]["profile"], "reason": "leaudio_offload_props_not_ready"}

        ko_ok = any(device in r["devices"] and r["sha"] == ko_sha for r in tables["hotspot"]["ko"])
        jar_ok = any(device in r["devices"] and r["sha"] == jar_sha for r in tables["hotspot"]["tethering_jar"])
        if ko_ok and jar_ok:
            hs_status = {"status": "ready", "reason": "matched"}
        else:
            missing_parts = ([] if ko_ok else ["ko_sha"]) + ([] if jar_ok else ["tethering_jar_sha"])
            hs_status = {"status": "needs_rom_onboard", "reason": "+".join(missing_parts) + "_unlisted"}

    modules = {
        "mt6639-6g": sixg_status,
        "mt6639-bt": bt_status,
        "mt6639-hotspot": hs_status,
    }
    needs_onboard = any(m["status"] == "needs_rom_onboard" for m in modules.values())
    ready = (
        ksu_ok and selinux_ok
        and all(m["status"] == "ready" for m in modules.values())
    )
    installed_state = {
        m: {
            "live": kv.get(f"MOD_LIVE_{m}", "absent"),
            "staged": kv.get(f"MOD_UPD_{m}", "absent"),
            "disabled": kv.get(f"MOD_DIS_{m}") == "1",
        }
        for m in ("mt6639-6g", "mt6639-bt", "mt6639-hotspot")
    }
    return {
        "schema": "mt6639-remote-preflight-v1",
        "device": device,
        "sdk_int": int(kv["SDK"]) if kv.get("SDK", "").isdigit() else None,
        "hyperos_version": kv.get("MI_OS"),
        "kernel_release": kernel,
        "has_cellular_baseband": bool(kv.get("BASEBAND")),
        "ksu_enforcing_ok": ksu_ok and selinux_ok,
        "ksu_ok": ksu_ok,
        "ksud_version": kv.get("KSUD_VER", "missing"),
        "ksu_manager": kv.get("KSU_MGR", "none"),
        "selinux": kv.get("SELINUX"),
        "sysfs_enforce": kv.get("SYSFS_ENFORCE"),
        "wifi_enabled": kv.get("WIFI_ENABLED") == "1",
        "wifi_connected": kv.get("WIFI_CONNECTED") == "1",
        "zygisk_ok": kv.get("ZYGISK_OK") == "1",
        "lsposed_ok": kv.get("LSPOSED_OK") == "1",
        "ui_apk_version": kv.get("UI_PKG_VER") or None,
        "installed_state": installed_state,
        "hashes": {
            "wifi_cfg_sha256": cfg_sha if SHA256_RE.fullmatch(cfg_sha) else None,
            "ko_path": kv.get("KO_PATH"),
            "ko_sha256": ko_sha if SHA256_RE.fullmatch(ko_sha) else None,
            "bt_apk_sha256": bt_sha if SHA256_RE.fullmatch(bt_sha) else None,
            "tethering_jar_sha256": jar_sha if SHA256_RE.fullmatch(jar_sha) else None,
        },
        "modules": modules,
        "needs_rom_onboard": needs_onboard,
        "ready": ready,
        "next_action": (
            "run python3 tools/onboard_mt6639_rom.py to onboard new ROM hashes before staging modules"
            if needs_onboard else
            ("proceed_to_stage_or_bounded_trial" if ready else "resolve_root_or_selinux_blockers")
        ),
    }


def run_preflight(transport: Transport, repo: Path) -> dict:
    tables = load_tables(repo)
    cp = transport.su_shell(PREFLIGHT_SCRIPT)
    if cp.returncode != 0:
        raise SessionError("PREFLIGHT_FAILED", cp.stderr.strip() or "su preflight command failed")
    return _eval_preflight_kv(_parse_kv(cp.stdout), tables)


def plan_stage(preflight: dict, artifacts: list[dict]) -> dict:
    return {
        "schema": "mt6639-remote-stage-v1",
        "executed": False,
        "preflight_ready": preflight["ready"],
        "ksud_version": preflight["ksud_version"],
        "artifacts": [
            {
                "id": a["id"],
                "kind": a["kind"],
                "version": a["version"],
                "version_code": a["version_code"],
                "sha256": a["sha256"],
            }
            for a in artifacts
        ],
        "steps": [
            "verify uid=0 u:r:ksu:s0, dual SELinux Enforcing, and compatibility hashes",
            "verify local artifact SHA-256 against manifest",
            "push all 4 artifacts to root-only /data/local/tmp/mt6639-remote-stage-* and re-verify remote SHA-256",
            "install mt6639-6g, mt6639-bt, and mt6639-hotspot via /data/adb/ksud module install",
            "verify SHA256SUMS inside /data/adb/modules_update/mt6639-6g and mt6639-hotspot",
            "touch disable marker on /data/adb/modules_update/mt6639-6g (and existing live dir) so activation reboot stays stock until bounded-trial",
            "install mt6639-ui-lsposed-0.3.0.apk via pm install -r -S <size> stdin stream and instruct single activation reboot",
        ],
    }


def execute_stage(transport: Transport, repo: Path, manifest_path: Path) -> dict:
    artifacts = load_primary_artifacts(manifest_path, repo)
    preflight = run_preflight(transport, repo)
    if not preflight["ksu_enforcing_ok"]:
        raise SessionError("GATE_FAILED", "requires u:r:ksu:s0 and dual SELinux Enforcing")
    if not preflight["ready"]:
        raise SessionError("COMPAT_NOT_READY", preflight["next_action"])
    if preflight["ksud_version"] == "missing":
        raise SessionError("KSUD_MISSING", "/data/adb/ksud not found or not executable")

    cp_dir = transport.su_shell("umask 077 && mktemp -d /data/local/tmp/mt6639-remote-stage-XXXXXX")
    stage_dir = cp_dir.stdout.strip()
    if cp_dir.returncode != 0 or not stage_dir.startswith("/data/local/tmp/mt6639-remote-stage-"):
        raise SessionError("STAGE_DIR_FAILED", "cannot create root-only remote staging dir")

    try:
        staged_paths = {}
        for idx, art in enumerate(artifacts):
            remote_file = f"{stage_dir}/{art['path'].name}"
            tmp_up = f"/data/local/tmp/mt6639-adb-up-{os.getpid()}-{idx}-{art['path'].name}"
            transport.push_file(art["path"], remote_file, tmp_up, timeout=90.0)
            staged_paths[art["id"]] = (remote_file, art["sha256"])

        sha_checks = " && ".join(
            f"[ \"$(sha256sum {shlex.quote(path)} | cut -d ' ' -f 1)\" = {shlex.quote(h)} ]"
            for path, h in staged_paths.values()
        )
        cp_sha = transport.su_shell(sha_checks, timeout=30.0)
        if cp_sha.returncode != 0:
            raise SessionError("REMOTE_SHA_MISMATCH", "staged file SHA-256 mismatch on device")

        ui_apk_size = next(a["path"].stat().st_size for a in artifacts if a["id"] == "mt6639-ui-lsposed")
        ui_apk_path = shlex.quote(staged_paths["mt6639-ui-lsposed"][0])
        cmds = [
            f"/data/adb/ksud module install {shlex.quote(staged_paths['mt6639-6g'][0])}",
            f"/data/adb/ksud module install {shlex.quote(staged_paths['mt6639-bt'][0])}",
            f"/data/adb/ksud module install {shlex.quote(staged_paths['mt6639-hotspot'][0])}",
            "for m in mt6639-6g mt6639-hotspot; do "
            "if [ -f \"/data/adb/modules_update/$m/SHA256SUMS\" ]; then "
            "(cd \"/data/adb/modules_update/$m\" && sha256sum -c SHA256SUMS >/dev/null) || exit 71; "
            "elif [ -f \"/data/adb/modules/$m/SHA256SUMS\" ]; then "
            "(cd \"/data/adb/modules/$m\" && sha256sum -c SHA256SUMS >/dev/null) || exit 71; "
            "else exit 71; fi; done",
            "guard_set=0; "
            "if [ -d /data/adb/modules_update/mt6639-6g ]; then : > /data/adb/modules_update/mt6639-6g/disable && guard_set=1; fi; "
            "if [ -d /data/adb/modules/mt6639-6g ]; then : > /data/adb/modules/mt6639-6g/disable && guard_set=1; fi; "
            "[ \"$guard_set\" = 1 ] || exit 73",
            f"pm install -r -S {ui_apk_size} < {ui_apk_path}",
            "echo STAGE_OK=1",
        ]
        cp_inst = transport.su_shell(" && ".join(cmds), timeout=90.0)
        if cp_inst.returncode != 0 or "STAGE_OK=1" not in cp_inst.stdout:
            raise SessionError("INSTALL_FAILED", (cp_inst.stderr or cp_inst.stdout).strip() or "ksud/pm install failed")
    finally:
        try:
            transport.su_shell(f"rm -rf {shlex.quote(stage_dir)}", timeout=15.0)
        except SessionError:
            pass

    return {
        "schema": "mt6639-remote-stage-v1",
        "executed": True,
        "device": preflight["device"],
        "requires_single_reboot": True,
        "sixg_disable_guard": "present",
        "artifacts": [
            {"id": a["id"], "version": a["version"], "sha256": a["sha256"]}
            for a in artifacts
        ],
        "next_action": "enable com.android.settings scope in LSPosed Manager, then perform one standard reboot (reactivate KernelSU if late-load) before bounded-trial",
    }


def plan_bounded_trial(preflight: dict) -> dict:
    return {
        "schema": "mt6639-remote-bounded-trial-v1",
        "executed": False,
        "preflight_ready": preflight["ready"],
        "sixg_module_ready": preflight["modules"]["mt6639-6g"]["status"] == "ready",
        "ksu_enforcing_ok": preflight["ksu_enforcing_ok"],
        "config_conf_untouched": True,
        "disable_marker_preserved": True,
        "env_override": {
            "RUN_MODE": "bounded",
            "hold_seconds": 900,
        },
        "steps": [
            "verify preflight u:r:ksu:s0, SELinux Enforcing, live /data/adb/modules/mt6639-6g, and intact SHA256SUMS",
            "temporarily clear mt6639-6g/disable only for control.sh enable and immediately re-create it in trap so cold reboot stays 100% stock",
            "invoke RUN_MODE=bounded sh control.sh enable without touching checksummed config.conf",
            "re-verify SHA256SUMS and observe iw phy Band 4 6GHz enabled channel count",
        ],
    }


def execute_bounded_trial(transport: Transport, repo: Path) -> dict:
    preflight = run_preflight(transport, repo)
    if not preflight["ksu_enforcing_ok"]:
        raise SessionError("GATE_FAILED", "requires u:r:ksu:s0 and dual SELinux Enforcing")
    if preflight["modules"]["mt6639-6g"]["status"] != "ready":
        raise SessionError("COMPAT_NOT_READY", "mt6639-6g hashes not ready; run preflight/onboard first")
    moddir = "/data/adb/modules/mt6639-6g"
    # Never write config.conf (which breaks verify-module.sh SHA256SUMS and post-reboot-check.sh).
    # Temporarily clear disable only to satisfy control.sh enable, pass RUN_MODE=bounded via env,
    # and restore the disable marker immediately in trap; bounded guardians do not watch disable.
    cmd = (
        f"MOD={moddir}; "
        "[ -d \"$MOD\" ] || { echo 'MISSING_LIVE_MODULE=1'; exit 71; }; "
        "sh \"$MOD/bin/verify-module.sh\" \"$MOD\" || { echo 'MODULE_SHA_BROKEN=1'; exit 72; }; "
        "restore_disable() { : > \"$MOD/disable\"; }; "
        "trap restore_disable EXIT HUP INT TERM; "
        "rm -f \"$MOD/disable\"; "
        "RUN_MODE=bounded sh \"$MOD/bin/control.sh\" \"$MOD\" enable; rc=$?; "
        "restore_disable; "
        "trap - EXIT HUP INT TERM; "
        "sh \"$MOD/bin/verify-module.sh\" \"$MOD\" && echo 'VERIFY_SHA_OK=1' || echo 'VERIFY_SHA_OK=0'; "
        "echo \"TRIAL_ENABLE_RC=$rc\"; "
        "iw phy 2>/dev/null | grep -E '^\\s*\\* [0-9.]+ MHz' | "
        "awk '$2 >= 5925 && $2 <= 7125 && $0 !~ /\\(disabled\\)/ { n++ } END { print \"SIXG_ENABLED_COUNT=\" (n+0) }'; "
        "sh \"$MOD/bin/control.sh\" \"$MOD\" status; "
        "exit \"$rc\""
    )
    cp = transport.su_shell(cmd, timeout=90.0)
    sixg_count = 0
    enable_rc = cp.returncode
    verify_sha_ok = False
    for line in cp.stdout.splitlines():
        if line.startswith("SIXG_ENABLED_COUNT="):
            sixg_count = int(line.split("=", 1)[1].strip() or "0")
        elif line.startswith("TRIAL_ENABLE_RC="):
            enable_rc = int(line.split("=", 1)[1].strip() or str(cp.returncode))
        elif line.strip() == "VERIFY_SHA_OK=1":
            verify_sha_ok = True
        elif line.strip() == "MISSING_LIVE_MODULE=1":
            raise SessionError("MODULE_NOT_ACTIVE", "mt6639-6g is not live under /data/adb/modules (stage + 1 reboot required)")
    return {
        "schema": "mt6639-remote-bounded-trial-v1",
        "executed": True,
        "enable_rc": enable_rc,
        "sixg_enabled_channels": sixg_count,
        "config_conf_untouched": True,
        "verify_module_sha_ok": verify_sha_ok,
        "disable_marker_restored": True,
        "hold_seconds": 900,
        "status": "active_bounded" if enable_rc == 0 else "failed",
    }


def plan_restore_6g(preflight: dict) -> dict:
    is_618_disconnected = (
        preflight["kernel_release"].startswith("6.18.")
        and not preflight["wifi_connected"]
    )
    return {
        "schema": "mt6639-remote-restore-6g-v1",
        "executed": False,
        "kernel_release": preflight["kernel_release"],
        "wifi_connected": preflight["wifi_connected"],
        "warning": (
            "6.18 mount-txn requires associated Wi-Fi during disable to avoid 45s rc=7 wait; connect a router before restoring on 6.18"
            if is_618_disconnected else None
        ),
        "steps": [
            "run sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g disable",
            "ensure /data/adb/modules/mt6639-6g/disable guard remains set",
            "verify active_tx=none, state=restored, and stock /odm/firmware/wifi.cfg SHA-256",
        ],
    }


def execute_restore_6g(transport: Transport, repo: Path) -> dict:
    preflight = run_preflight(transport, repo)
    if not preflight["ksu_enforcing_ok"]:
        raise SessionError("GATE_FAILED", "requires u:r:ksu:s0 and dual SELinux Enforcing")
    moddir = "/data/adb/modules/mt6639-6g"
    cmd = (
        f"MOD={moddir}; "
        "sh \"$MOD/bin/control.sh\" \"$MOD\" disable; rc=$?; "
        ": > \"$MOD/disable\"; "
        "echo \"RESTORE_RC=$rc\"; "
        "sh \"$MOD/bin/control.sh\" \"$MOD\" status; "
        "exit \"$rc\""
    )
    cp = transport.su_shell(cmd, timeout=90.0)
    kv = _parse_kv(cp.stdout)
    rc = int(kv.get("RESTORE_RC", str(cp.returncode)))
    return {
        "schema": "mt6639-remote-restore-6g-v1",
        "executed": True,
        "restore_rc": rc,
        "active_tx": kv.get("active_tx"),
        "disable_marker_present": True,
        "status": "restored" if rc == 0 else "restore_failed",
    }


def run_diagnose(transport: Transport, repo: Path) -> dict:
    preflight = run_preflight(transport, repo)
    diag_cmd = (
        "echo \"COUNTRY=$(runcon u:r:shell:s0 sh -c 'cmd wifi get-country-code' 2>/dev/null | tail -1)\"; "
        "echo \"CFG_MOUNT_COUNT=$(grep -c ' /odm/firmware/wifi.cfg ' /proc/1/mountinfo 2>/dev/null || echo 0)\"; "
        "echo \"SIX_G_STATE=$(cat /data/adb/mt6639-6g/state 2>/dev/null || echo absent)\"; "
        "echo \"SIX_G_ACTIVE_TX=$([ -s /data/adb/mt6639-6g/active_tx ] && echo present || echo none)\"; "
        "echo \"MTAP0=$([ -e /sys/class/net/mtap0 ] && echo present || echo absent)\"; "
        "echo \"TETHER_WANTED=$(dumpsys tethering --short 2>/dev/null | sed -n 's/^[[:space:]]*Upstream wanted:[[:space:]]*//p' | head -1)\"; "
        "echo \"HOTSPOT_SNAP=$(tr '\\n' ';' < /data/misc/mt6639-hotspot/status 2>/dev/null || echo absent)\"; "
        "echo \"BAND4_TOTAL=$(iw phy 2>/dev/null | grep -E '^\\s*\\* [0-9.]+ MHz' | awk '$2 >= 5925 && $2 <= 7125 { n++ } END { print n+0 }')\"; "
        "echo \"BAND4_ENABLED=$(iw phy 2>/dev/null | grep -E '^\\s*\\* [0-9.]+ MHz' | awk '$2 >= 5925 && $2 <= 7125 && $0 !~ /\\(disabled\\)/ { n++ } END { print n+0 }')\"; "
        "echo \"BT_PROPS_OK=$(for k in bluetooth.profile.bap.unicast.client.enabled bluetooth.profile.bap.broadcast.source.enabled bluetooth.profile.bap.broadcast.assist.enabled bluetooth.profile.csip.set_coordinator.enabled bluetooth.profile.vcp.controller.enabled bluetooth.profile.mcp.server.enabled; do getprop \"$k\"; done | grep -cx true || echo 0)\""
    )
    cp = transport.su_shell(diag_cmd, timeout=30.0)
    kv = _parse_kv(cp.stdout)
    return {
        "schema": "mt6639-remote-diagnose-v1",
        "preflight": preflight,
        "live": {
            "wifi_country_line": kv.get("COUNTRY", ""),
            "cfg_init_mount_count": int(kv.get("CFG_MOUNT_COUNT", "0") or "0"),
            "sixg_state": kv.get("SIX_G_STATE", "absent"),
            "sixg_active_tx": kv.get("SIX_G_ACTIVE_TX", "none"),
            "band4_total_channels": int(kv.get("BAND4_TOTAL", "0") or "0"),
            "band4_enabled_channels": int(kv.get("BAND4_ENABLED", "0") or "0"),
            "mtap0": kv.get("MTAP0", "absent"),
            "tethering_upstream_wanted": kv.get("TETHER_WANTED", ""),
            "hotspot_snapshot": kv.get("HOTSPOT_SNAP", "absent"),
            "bt_true_props_count": int(kv.get("BT_PROPS_OK", "0") or "0"),
        },
    }


def plan_cleanup(*, uninstall: bool) -> dict:
    steps = [
        "stop active mt6639-hotspot session if present and wait for mtap0 absent",
        "restore mt6639-bt properties if active",
        "restore mt6639-6g transaction and verify /odm/firmware/wifi.cfg matches stock SHA-256",
        "create disable marker on /data/adb/modules/{mt6639-6g,mt6639-bt,mt6639-hotspot}",
    ]
    if uninstall:
        steps.extend([
            "run /data/adb/ksud module uninstall for mt6639-6g, mt6639-bt, and mt6639-hotspot",
            "uninstall com.hiliang.mt6639ui via pm uninstall",
        ])
    return {
        "schema": "mt6639-remote-cleanup-v1",
        "executed": False,
        "uninstall": uninstall,
        "steps": steps,
    }


def execute_cleanup(transport: Transport, repo: Path, *, uninstall: bool) -> dict:
    preflight = run_preflight(transport, repo)
    if not preflight["ksu_enforcing_ok"]:
        raise SessionError("GATE_FAILED", "requires u:r:ksu:s0 and dual SELinux Enforcing")
    cmds = [
        "if [ -f /data/adb/modules/mt6639-hotspot/bin/control.sh ]; then "
        "sh /data/adb/modules/mt6639-hotspot/bin/control.sh /data/adb/modules/mt6639-hotspot stop >/dev/null 2>&1 || true; "
        "fi",
        "if [ -f /data/adb/modules/mt6639-bt/bin/control.sh ]; then "
        "sh /data/adb/modules/mt6639-bt/bin/control.sh restore >/dev/null 2>&1 || true; "
        "fi",
        "if [ -f /data/adb/modules/mt6639-6g/bin/control.sh ]; then "
        "sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g disable || exit 71; "
        "fi",
        "for m in mt6639-6g mt6639-bt mt6639-hotspot; do "
        "if [ -d \"/data/adb/modules/$m\" ]; then : > \"/data/adb/modules/$m/disable\"; fi; "
        "done",
    ]
    if uninstall:
        cmds.extend([
            "for m in mt6639-hotspot mt6639-bt mt6639-6g; do "
            "if [ -d \"/data/adb/modules/$m\" ] || [ -d \"/data/adb/modules_update/$m\" ]; then "
            "/data/adb/ksud module uninstall \"$m\" || exit 72; "
            "fi; done",
            "pm uninstall com.hiliang.mt6639ui >/dev/null 2>&1 || true",
        ])
    cmds.append("echo CLEANUP_OK=1")
    cp = transport.su_shell(" && ".join(cmds), timeout=90.0)
    if cp.returncode != 0 or "CLEANUP_OK=1" not in cp.stdout:
        raise SessionError("CLEANUP_FAILED", (cp.stderr or cp.stdout).strip() or "cleanup failed")
    return {
        "schema": "mt6639-remote-cleanup-v1",
        "executed": True,
        "uninstall": uninstall,
        "status": "uninstalled_and_restored" if uninstall else "restored_and_disabled",
    }


def collect_evidence(transport: Transport, out_dir: Path) -> dict:
    if out_dir.exists():
        raise SessionError("OUT_EXISTS", f"--out already exists: {out_dir}")
    os.makedirs(out_dir, mode=0o700, exist_ok=False)
    os.chmod(out_dir, 0o700)
    probes = {
        "mt6639_6g_status": "sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g status",
        "mt6639_6g_card": "sh /data/adb/modules/mt6639-6g/bin/status-card.sh /data/adb/modules/mt6639-6g show",
        "mt6639_bt_status": "sh /data/adb/modules/mt6639-bt/bin/control.sh status",
        "mt6639_hotspot_status": "sh /data/adb/modules/mt6639-hotspot/bin/control.sh /data/adb/modules/mt6639-hotspot status",
        "hotspot_snapshot": "cat /data/misc/mt6639-hotspot/status 2>/dev/null || true",
        "iw_phy_summary": "iw phy 2>/dev/null | grep -E 'Band [0-9]+:|\\* [0-9.]+ MHz' || true",
        "tethering_short": "dumpsys tethering --short 2>/dev/null | grep -E 'Upstream wanted:|Current upstream|mtap0 - ' || true",
    }
    captured = {}
    for name, cmd in probes.items():
        cp = transport.su_shell(cmd)
        target = out_dir / f"{name}.txt"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(cp.stdout)
        os.chmod(target, 0o600)
        captured[name] = {"rc": cp.returncode, "bytes": len(cp.stdout.encode("utf-8")), "file": target.name}
    phy_text = (out_dir / "iw_phy_summary.txt").read_text(encoding="utf-8")
    enabled_6g = sum(
        1 for line in phy_text.splitlines()
        if "* " in line and " MHz" in line and "(disabled)" not in line
        and (m := re.search(r"\*\s+([0-9]+)", line)) and 5925 <= int(m.group(1)) <= 7125
    )
    summary = {
        "schema": "mt6639-remote-evidence-v1",
        "out_dir": str(out_dir),
        "sixg_enabled_channels": enabled_6g,
        "artifacts": captured,
    }
    summary_path = out_dir / "summary.json"
    fd = os.open(summary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    os.chmod(summary_path, 0o600)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adb", default=None, help="absolute path to adb binary")
    parser.add_argument("--adb-port", type=int, default=None,
                        help="ADB server port (e.g. 15037 via SSH -R tunnel from NetEase UU / ToDesk PC)")
    parser.add_argument("--ssh-command-json", default=None,
                        help="pinned SSH transport JSON command argv")
    parser.add_argument("--serial", required=True, help="target device serial")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repository root containing modules/")
    parser.add_argument("--timeout", type=float, default=20.0)
    sub = parser.add_subparsers(dest="subcommand", required=True)
    p_pre = sub.add_parser("preflight", help="read-only multi-module device compatibility preflight")
    p_pre.add_argument("--strict", action="store_true", help="exit nonzero unless preflight ready=true")
    p_stage = sub.add_parser("stage", help="standard KernelSU install (with pre-locked 6g disable) + UI APK")
    p_stage.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="candidate manifest path")
    p_stage.add_argument("--execute", action="store_true", help="push and stage modules/APK on device")
    p_trial = sub.add_parser("bounded-trial", help="non-persistent 15-min bounded 6GHz trial")
    p_trial.add_argument("--execute", action="store_true", help="run transient bounded enable on device")
    p_r6g = sub.add_parser("restore-6g", help="checked restore of active 6GHz transaction with disable guard")
    p_r6g.add_argument("--execute", action="store_true", help="run restore on device")
    sub.add_parser("diagnose", help="read-only sanitized diagnostic snapshot")
    p_ev = sub.add_parser("collect-evidence", help="collect read-only module/radio status into 0700 dir")
    p_ev.add_argument("--out", required=True, help="new output directory")
    p_clean = sub.add_parser("cleanup", help="restore all modules and optionally ksud-uninstall")
    p_clean.add_argument("--execute", action="store_true", help="run restore/disable/uninstall on device")
    p_clean.add_argument("--uninstall", action="store_true", help="also invoke ksud module uninstall and pm uninstall UI APK")

    args = parser.parse_args(argv)
    try:
        if bool(args.adb) == bool(args.ssh_command_json):
            raise SessionError("INVALID_ARGS", "specify exactly one of --adb or --ssh-command-json")
        if not SERIAL_RE.fullmatch(args.serial):
            raise SessionError("INVALID_SERIAL", "invalid adb serial")
        if not (0 < args.timeout <= MAX_TIMEOUT):
            raise SessionError("INVALID_TIMEOUT", "timeout out of bounds")
        ssh_argv = None
        if args.ssh_command_json:
            if args.adb_port is not None:
                raise SessionError("INVALID_ARGS", "--adb-port cannot combine with --ssh-command-json")
            ssh_argv = load_ssh_command(Path(args.ssh_command_json))
        else:
            if not os.path.isabs(args.adb) or not os.path.isfile(args.adb) or not os.access(args.adb, os.X_OK):
                raise SessionError("INVALID_ADB", "--adb must be an existing executable absolute path")
            if args.adb_port is not None and not (1 <= args.adb_port <= 65535):
                raise SessionError("INVALID_PORT", "--adb-port out of bounds")

        transport = Transport(
            adb=args.adb, adb_port=args.adb_port, serial=args.serial,
            ssh_argv=ssh_argv, timeout=args.timeout,
        )
        repo = Path(args.repo).resolve()
        if args.subcommand == "preflight":
            out = run_preflight(transport, repo)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if (not args.strict or out["ready"]) else 3
        if args.subcommand == "stage":
            mpath = Path(args.manifest).resolve()
            if not args.execute:
                artifacts = load_primary_artifacts(mpath, repo)
                out = plan_stage(run_preflight(transport, repo), artifacts)
            else:
                out = execute_stage(transport, repo, mpath)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.subcommand == "bounded-trial":
            if not args.execute:
                out = plan_bounded_trial(run_preflight(transport, repo))
            else:
                out = execute_bounded_trial(transport, repo)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.subcommand == "restore-6g":
            if not args.execute:
                out = plan_restore_6g(run_preflight(transport, repo))
            else:
                out = execute_restore_6g(transport, repo)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.subcommand == "diagnose":
            out = run_diagnose(transport, repo)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.subcommand == "collect-evidence":
            out = collect_evidence(transport, Path(args.out).resolve())
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.subcommand == "cleanup":
            if not args.execute:
                out = plan_cleanup(uninstall=args.uninstall)
            else:
                out = execute_cleanup(transport, repo, uninstall=args.uninstall)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
    except SessionError as err:
        print(f"ERROR {err.code}: {err.detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
