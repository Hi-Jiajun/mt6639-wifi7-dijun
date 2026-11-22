#!/usr/bin/env python3
"""Assemble a zero-Python portable remote tablet session kit + AI agent bridge.

Produces a self-contained directory containing:
- Verified copies of the 4 candidate artifacts + SHA256SUMS
- On-device POSIX helper `device_tablet_session.sh`
- Zero-Python Windows (`run_session.bat`) and macOS/Linux (`run_session.sh`) runners
- OpenSSH ADB tunnel bridge (`agent_bridge.bat` / `agent_bridge.sh`) + PC diagnostics
  (`diagnose_pc.bat` / `diagnose_pc.sh`) for NetEase UU / ToDesk / Sunlogin remote sessions
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "release/mt6639-candidate-20261026.json"
PRIMARY_IDS = ("mt6639-6g", "mt6639-bt", "mt6639-hotspot", "mt6639-ui-lsposed")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


RUN_SESSION_BAT = r"""@echo off
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
set "ACT=%~1"
if "%ACT%"=="" (
  echo Usage: run_session.bat [preflight^|stage^|bounded-trial^|restore-6g^|test-bt^|restore-bt^|diagnose^|cleanup^|uninstall] [SERIAL]
  exit /b 64
)
set "SER=%~2"
set "ADB_BIN="
if exist "%SCRIPT_DIR%platform-tools\adb.exe" (
  set "ADB_BIN=%SCRIPT_DIR%platform-tools\adb.exe"
) else (
  where adb >nul 2>nul && set "ADB_BIN=adb"
)
if "%ADB_BIN%"=="" (
  echo ERROR: adb not found. Put platform-tools folder here or add adb.exe to PATH.
  exit /b 2
)

"%ADB_BIN%" start-server >nul 2>nul
if "%SER%"=="" (
  for /f "skip=1 tokens=1,2" %%A in ('call "%ADB_BIN%" devices') do (
    if "%%B"=="device" if "!SER!"=="" set "SER=%%A"
  )
)
if "%SER%"=="" (
  echo ERROR: no authorized ADB device found. Check USB cable and tablet USB debugging prompt.
  "%ADB_BIN%" devices
  exit /b 2
)
echo Target device: %SER%

set "DEV_DIR=/data/local/tmp/mt6639-kit-session"
set "DEV_SH=%DEV_DIR%/device_tablet_session.sh"

if "%ACT%"=="stage" (
  "%ADB_BIN%" -s "%SER%" shell "rm -rf %DEV_DIR% 2>/dev/null || su -c 'rm -rf %DEV_DIR%'; mkdir -p -m 0700 %DEV_DIR%" || exit /b 2
  for %%F in (mt6639-6g-v0.4.1.zip mt6639-bt-v0.2.0.zip mt6639-hotspot-v0.3.1.zip mt6639-ui-lsposed-0.3.0.apk SHA256SUMS device_tablet_session.sh) do (
    "%ADB_BIN%" -s "%SER%" push "%SCRIPT_DIR%%%F" "%DEV_DIR%/%%F" || exit /b 2
  )
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chown -R 0:0 %DEV_DIR% && chmod 0700 %DEV_DIR% && chmod 0755 %DEV_SH% && sh %DEV_SH% stage-from-dir %DEV_DIR%'"
  set "RC=!ERRORLEVEL!"
  "%ADB_BIN%" -s "%SER%" shell "su -c 'rm -rf %DEV_DIR%'" >nul 2>nul
  exit /b !RC!
)

"%ADB_BIN%" -s "%SER%" shell "rm -rf %DEV_DIR% 2>/dev/null || su -c 'rm -rf %DEV_DIR%'; mkdir -p -m 0700 %DEV_DIR%" || exit /b 2
"%ADB_BIN%" -s "%SER%" push "%SCRIPT_DIR%device_tablet_session.sh" "%DEV_SH%" >nul || exit /b 2

if "%ACT%"=="preflight" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% preflight'"
) else if "%ACT%"=="bounded-trial" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% bounded-trial'"
) else if "%ACT%"=="restore-6g" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% restore-6g'"
) else if "%ACT%"=="test-bt" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% test-bt'"
) else if "%ACT%"=="restore-bt" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% restore-bt'"
) else if "%ACT%"=="diagnose" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% diagnose'"
) else if "%ACT%"=="cleanup" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% cleanup'"
) else if "%ACT%"=="uninstall" (
  "%ADB_BIN%" -s "%SER%" shell "su -c 'chmod 0755 %DEV_SH% && sh %DEV_SH% cleanup --uninstall'"
) else (
  echo Unknown action: %ACT%
  "%ADB_BIN%" -s "%SER%" shell "rm -rf %DEV_DIR%" >nul 2>nul
  exit /b 64
)
set "RC=!ERRORLEVEL!"
"%ADB_BIN%" -s "%SER%" shell "rm -rf %DEV_DIR% 2>/dev/null || su -c 'rm -rf %DEV_DIR%'" >nul 2>nul
exit /b !RC!
"""


RUN_SESSION_SH = r"""#!/bin/sh
# Zero-Python macOS/Linux host wrapper around device_tablet_session.sh.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "${0%/*}" && pwd)
ACT=${1:-}
SER=${2:-}
ADB_BIN=${ADB_BIN:-$(command -v adb || true)}
if [ -x "$SCRIPT_DIR/platform-tools/adb" ]; then
    ADB_BIN="$SCRIPT_DIR/platform-tools/adb"
fi
[ -n "$ADB_BIN" ] || { echo "ERROR: adb not found in PATH or $SCRIPT_DIR/platform-tools" >&2; exit 2; }
[ -n "$ACT" ] || {
    echo "usage: run_session.sh preflight|stage|bounded-trial|restore-6g|test-bt|restore-bt|diagnose|cleanup|uninstall [SERIAL]" >&2
    exit 64
}

"$ADB_BIN" start-server >/dev/null 2>&1 || true
if [ -z "$SER" ]; then
    SER=$("$ADB_BIN" devices | awk 'NR>1 && $2=="device" { print $1; exit }')
fi
[ -n "$SER" ] || { echo "ERROR: no authorized ADB device found" >&2; "$ADB_BIN" devices; exit 2; }
echo "Target device: $SER"

DEV_DIR="/data/local/tmp/mt6639-kit-session"
DEV_SH="$DEV_DIR/device_tablet_session.sh"

case "$ACT" in
    stage)
        "$ADB_BIN" -s "$SER" shell "rm -rf $DEV_DIR 2>/dev/null || su -c 'rm -rf $DEV_DIR'; mkdir -p -m 0700 $DEV_DIR"
        for f in mt6639-6g-v0.4.1.zip mt6639-bt-v0.2.0.zip mt6639-hotspot-v0.3.1.zip mt6639-ui-lsposed-0.3.0.apk SHA256SUMS device_tablet_session.sh; do
            "$ADB_BIN" -s "$SER" push "$SCRIPT_DIR/$f" "$DEV_DIR/$f"
        done
        rc=0
        "$ADB_BIN" -s "$SER" shell "su -c 'chown -R 0:0 $DEV_DIR && chmod 0700 $DEV_DIR && chmod 0755 $DEV_SH && sh $DEV_SH stage-from-dir $DEV_DIR'" || rc=$?
        "$ADB_BIN" -s "$SER" shell "su -c 'rm -rf $DEV_DIR'" >/dev/null 2>&1 || true
        exit "$rc"
        ;;
    preflight|bounded-trial|restore-6g|test-bt|restore-bt|diagnose|cleanup)
        "$ADB_BIN" -s "$SER" shell "rm -rf $DEV_DIR 2>/dev/null || su -c 'rm -rf $DEV_DIR'; mkdir -p -m 0700 $DEV_DIR"
        "$ADB_BIN" -s "$SER" push "$SCRIPT_DIR/device_tablet_session.sh" "$DEV_SH" >/dev/null
        rc=0
        "$ADB_BIN" -s "$SER" shell "su -c 'chmod 0755 $DEV_SH && sh $DEV_SH $ACT'" || rc=$?
        "$ADB_BIN" -s "$SER" shell "rm -rf $DEV_DIR 2>/dev/null || su -c 'rm -rf $DEV_DIR'" >/dev/null 2>&1 || true
        exit "$rc"
        ;;
    uninstall)
        "$ADB_BIN" -s "$SER" shell "rm -rf $DEV_DIR 2>/dev/null || su -c 'rm -rf $DEV_DIR'; mkdir -p -m 0700 $DEV_DIR"
        "$ADB_BIN" -s "$SER" push "$SCRIPT_DIR/device_tablet_session.sh" "$DEV_SH" >/dev/null
        rc=0
        "$ADB_BIN" -s "$SER" shell "su -c 'chmod 0755 $DEV_SH && sh $DEV_SH cleanup --uninstall'" || rc=$?
        "$ADB_BIN" -s "$SER" shell "rm -rf $DEV_DIR 2>/dev/null || su -c 'rm -rf $DEV_DIR'" >/dev/null 2>&1 || true
        exit "$rc"
        ;;
    *)
        echo "ERROR: unknown action $ACT" >&2
        exit 64
        ;;
esac
"""


AGENT_BRIDGE_BAT = r"""@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "SSH_HOST=%~1"
set "SSH_PORT=%~2"
set "SSH_USER=%~3"

if "%SSH_HOST%"=="" set /p "SSH_HOST=Enter operator SSH host or IP: "
if "%SSH_PORT%"=="" set "SSH_PORT=22"
if "%SSH_USER%"=="" set "SSH_USER=adbbridge"

set "ADB_BIN="
if exist "%SCRIPT_DIR%platform-tools\adb.exe" (
  set "ADB_BIN=%SCRIPT_DIR%platform-tools\adb.exe"
) else (
  where adb >nul 2>nul && set "ADB_BIN=adb"
)
if "%ADB_BIN%"=="" (
  echo ERROR: adb not found.
  exit /b 2
)
where ssh >nul 2>nul
if errorlevel 1 (
  echo ERROR: Windows OpenSSH Client not found. Enable OpenSSH Client in Windows Optional Features.
  exit /b 2
)

echo [1/3] Starting local ADB server on 127.0.0.1:5037...
"%ADB_BIN%" start-server
"%ADB_BIN%" devices

echo [2/3] Bridging 127.0.0.1:5037 -> remote %SSH_HOST%:15037 via OpenSSH...
echo       Keep this window open during the AI live debugging session.
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R 15037:127.0.0.1:5037 -p "%SSH_PORT%" "%SSH_USER%@%SSH_HOST%"
"""


AGENT_BRIDGE_SH = r"""#!/bin/sh
# Bridge this PC's local ADB server (127.0.0.1:5037) to operator host 127.0.0.1:15037 over OpenSSH.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "${0%/*}" && pwd)
SSH_HOST=${1:-}
SSH_PORT=${2:-22}
SSH_USER=${3:-adbbridge}
[ -n "$SSH_HOST" ] || { echo "usage: agent_bridge.sh <OPERATOR_SSH_HOST> [SSH_PORT=22] [SSH_USER=adbbridge]" >&2; exit 64; }

ADB_BIN=${ADB_BIN:-$(command -v adb || true)}
if [ -x "$SCRIPT_DIR/platform-tools/adb" ]; then
    ADB_BIN="$SCRIPT_DIR/platform-tools/adb"
fi
[ -n "$ADB_BIN" ] || { echo "ERROR: adb not found" >&2; exit 2; }
command -v ssh >/dev/null 2>&1 || { echo "ERROR: ssh client not found" >&2; exit 2; }

"$ADB_BIN" start-server
"$ADB_BIN" devices
echo "Bridging 127.0.0.1:5037 -> $SSH_HOST:15037 (keep open during remote AI session)..."
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -R 15037:127.0.0.1:5037 -p "$SSH_PORT" "${SSH_USER}@${SSH_HOST}"
"""


DIAGNOSE_PC_BAT = r"""@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "OUT=%SCRIPT_DIR%pc_diag.txt"
set "ADB_BIN=adb"
if exist "%SCRIPT_DIR%platform-tools\adb.exe" set "ADB_BIN=%SCRIPT_DIR%platform-tools\adb.exe"
(
  echo === DATE ===
  echo %DATE% %TIME%
  echo === ADB VERSION ===
  "%ADB_BIN%" version 2>&1
  echo === ADB DEVICES ===
  "%ADB_BIN%" start-server 2>&1
  "%ADB_BIN%" devices -l 2>&1
  echo === PORT 5037 ===
  netstat -ano | findstr :5037 2>&1
  echo === OPENSSH ===
  ssh -V 2>&1
  echo === ANDROID USB DEVICES ===
  pnputil /enum-devices /class AndroidUsbDeviceClass /connected 2>&1
) > "%OUT%"
type "%OUT%"
echo Saved PC diagnostic report to: %OUT%
"""


DIAGNOSE_PC_SH = r"""#!/bin/sh
# Collect USB/ADB/OpenSSH diagnostics from a macOS/Linux tester workstation.
SCRIPT_DIR=$(CDPATH= cd -- "${0%/*}" && pwd)
OUT="$SCRIPT_DIR/pc_diag.txt"
ADB_BIN=${ADB_BIN:-$(command -v adb || true)}
if [ -x "$SCRIPT_DIR/platform-tools/adb" ]; then
    ADB_BIN="$SCRIPT_DIR/platform-tools/adb"
fi
{
    echo "=== UNAME ==="
    uname -a
    echo "=== ADB VERSION ==="
    [ -n "$ADB_BIN" ] && "$ADB_BIN" version || echo "adb missing"
    echo "=== ADB DEVICES ==="
    if [ -n "$ADB_BIN" ]; then
        "$ADB_BIN" start-server 2>&1 || true
        "$ADB_BIN" devices -l 2>&1 || true
    fi
    echo "=== PORT 5037 ==="
    ss -ltn 2>/dev/null | grep 5037 || netstat -an 2>/dev/null | grep 5037 || true
    echo "=== OPENSSH ==="
    ssh -V 2>&1 || true
} > "$OUT" 2>&1
cat "$OUT"
echo "Saved PC diagnostic report to: $OUT"
"""


KIT_README = """# MT6639 平板远程实测便携套件（免 Python）

适用机型：小米平板 7S Pro (`violin`) / 小米平板 7 Ultra (`jinghu`)（HyperOS 3 / 4，KernelSU + Enforcing）。

## 包含文件
- `mt6639-6g-v0.4.1.zip` / `mt6639-bt-v0.2.0.zip` / `mt6639-hotspot-v0.3.1.zip` / `mt6639-ui-lsposed-0.3.0.apk`
- `SHA256SUMS`：四个候选包的校验清单
- `device_tablet_session.sh`：平板端纯 shell 执行脚本（不修改 `config.conf`、不输出 SSID/密码/MAC/IP、不自动重启）
- `run_session.bat` / `run_session.sh`：持机人电脑端免 Python USB ADB 控制脚本（推荐通过网易 UU 远程 / ToDesk / 向日葵直传后执行）
- `agent_bridge.bat` / `agent_bridge.sh`：使用系统自带 OpenSSH 将远端 USB ADB (`127.0.0.1:5037`) 安全反向映射至操控端 `127.0.0.1:15037`，供 AI 助手直接实时执行诊断与实测
- `diagnose_pc.bat` / `diagnose_pc.sh`：若远端电脑 ADB/USB 识别异常，一键生成本地环境诊断信息

## 标准单次实测顺序
1. `run_session.bat preflight`（核对 KernelSU `u:r:ksu:s0`、Enforcing 与官方固件哈希）
2. `run_session.bat stage`（标准 `ksud module install` 安装 3 模块 + 预置 `mt6639-6g/disable` 防重启自启锁 + 安装 UI APK）
3. 在平板 LSPosed 勾选 `com.android.settings` 后，**正常重启 1 次**使模块生效（若为重启掉 Root 方案需重激活一次 KernelSU）
4. 连着家里常规 Wi-Fi：`run_session.bat bounded-trial`（15 分钟有界试用，测完跑 `run_session.bat restore-6g` 验证干净回滚）
5. `run_session.bat test-bt` 与重启开关蓝牙测试 LE Audio 6 项 Host Profile（可 `restore-bt` 恢复）
6. 热点 UI 测试：前往「设置 -> 更多连接方式 -> 个人热点」，先测连路由器下的 `wifi-any`，最后再断路由器测 `local` 本地直连热点
7. 若测试后恢复原厂留用清理：`run_session.bat uninstall`
"""


def build_kit(manifest_path: Path, out_dir: Path, repo: Path) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, mode=0o755)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in manifest["artifacts"]}

    sums_lines = []
    copied = []
    for aid in PRIMARY_IDS:
        art = by_id[aid]
        src = repo / art["path"]
        if not src.is_file() or src.is_symlink():
            raise SystemExit(f"ERROR: missing artifact {src}")
        actual = sha256_file(src)
        if actual != art["sha256"]:
            raise SystemExit(f"ERROR: sha256 mismatch for {src}")
        dst = out_dir / src.name
        shutil.copyfile(src, dst)
        dst.chmod(0o644)
        sums_lines.append(f"{actual}  {src.name}")
        copied.append({"id": aid, "file": src.name, "sha256": actual})

    dev_sh = repo / "tools/device_tablet_session.sh"
    dev_dst = out_dir / "device_tablet_session.sh"
    shutil.copyfile(dev_sh, dev_dst)
    dev_dst.chmod(0o755)
    dev_sha = sha256_file(dev_dst)
    sums_lines.append(f"{dev_sha}  device_tablet_session.sh")

    (out_dir / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="ascii", newline="\n")
    (out_dir / "SHA256SUMS").chmod(0o644)

    scripts_crlf = {
        "run_session.bat": RUN_SESSION_BAT,
        "agent_bridge.bat": AGENT_BRIDGE_BAT,
        "diagnose_pc.bat": DIAGNOSE_PC_BAT,
    }
    for name, body in scripts_crlf.items():
        p = out_dir / name
        p.write_bytes(body.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        p.chmod(0o755)

    scripts_lf = {
        "run_session.sh": RUN_SESSION_SH,
        "agent_bridge.sh": AGENT_BRIDGE_SH,
        "diagnose_pc.sh": DIAGNOSE_PC_SH,
        "README.txt": KIT_README,
    }
    for name, body in scripts_lf.items():
        p = out_dir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755 if name.endswith(".sh") else 0o644)

    return {
        "schema": "mt6639-remote-kit-v1",
        "out_dir": str(out_dir),
        "artifacts": copied,
        "device_helper_sha256": dev_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(REPO_ROOT / "outputs/mt6639-tablet-remote-kit"))
    parser.add_argument("--repo", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    summary = build_kit(Path(args.manifest).resolve(), Path(args.out).resolve(), Path(args.repo).resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
