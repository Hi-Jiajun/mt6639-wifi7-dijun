"""Tests for zero-Python remote kit builder and device_tablet_session.sh."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import pubenv


REPO = Path(__file__).resolve().parents[1]
DEV_SH = REPO / "tools/device_tablet_session.sh"
KIT_BUILDER = REPO / "tools/build_remote_kit.py"


class RemoteKitTests(unittest.TestCase):
    def test_device_helper_syntax_and_zero_config_mutation_contract(self):
        res = subprocess.run(["sh", "-n", str(DEV_SH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        src = DEV_SH.read_text(encoding="utf-8")
        self.assertNotIn("config.conf", src.split("#!/system/bin/sh", 1)[1].split("cmd_preflight()", 1)[1])
        self.assertIn("verify-module.sh", src)
        self.assertIn("RUN_MODE=bounded", src)
        self.assertIn("/data/adb/modules_update/mt6639-6g/disable", src)

    @unittest.skipUnless(
        pubenv.assets_present(
            "mt6639-6g-v0.4.1.zip",
            "mt6639-bt-v0.2.0.zip",
            "mt6639-hotspot-v0.3.1.zip",
            "mt6639-ui-lsposed-0.3.0.apk",
        ),
        pubenv.SKIP_RELEASE_ASSETS,
    )
    def test_build_remote_kit_checksums_and_run_session_sh_with_fake_adb(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-kit-test-") as td:
            root = Path(td)
            kit_dir = root / "kit"
            res = subprocess.run(
                ["python3", str(KIT_BUILDER), "--out", str(kit_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            summary = json.loads(res.stdout)
            self.assertEqual(summary["schema"], "mt6639-remote-kit-v1")
            self.assertEqual(len(summary["artifacts"]), 4)

            for sh_name in ("run_session.sh", "agent_bridge.sh", "diagnose_pc.sh"):
                syn = subprocess.run(["sh", "-n", str(kit_dir / sh_name)], capture_output=True, text=True)
                self.assertEqual(syn.returncode, 0, f"{sh_name}: {syn.stderr}")

            # Verify no insecure 0777 staging dirs remain
            self.assertNotIn("0777", (kit_dir / "run_session.sh").read_text(encoding="utf-8"))
            self.assertNotIn("0777", (kit_dir / "run_session.bat").read_text(encoding="utf-8"))
            self.assertIn("-R 15037:127.0.0.1:5037", (kit_dir / "agent_bridge.sh").read_text(encoding="utf-8"))
            self.assertIn("-R 15037:127.0.0.1:5037", (kit_dir / "agent_bridge.bat").read_text(encoding="utf-8"))

            # Assert Windows wrappers avoid where "%ADB_BIN%" and keep su -c in one shell token
            for bat_name in ("run_session.bat", "agent_bridge.bat"):
                bat_txt = (kit_dir / bat_name).read_text(encoding="utf-8")
                self.assertNotIn('where "%ADB_BIN%"', bat_txt)
                self.assertIn("where adb", bat_txt)
            self.assertIn('"su -c \'', (kit_dir / "run_session.bat").read_text(encoding="utf-8"))

            # Verify SHA256SUMS inside assembled kit
            chk = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=kit_dir, capture_output=True, text=True)
            self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)

            # Simulate real adbd argument concatenation + strict su -c single-arg parsing
            adb_log = root / "adb.calls"
            su_log = root / "su.calls"
            stub_bin = root / "stub_bin"
            stub_bin.mkdir()
            (stub_bin / "su").write_text(
                f'''#!/usr/bin/env python3
import sys
if len(sys.argv) != 3 or sys.argv[1] != "-c":
    sys.stderr.write(f"BAD_SU_ARGV={{sys.argv!r}}\\n")
    sys.exit(99)
with open({str(su_log)!r}, "a") as fh:
    fh.write(sys.argv[2] + "\\n")
sys.exit(0)
''',
                encoding="utf-8",
            )
            (stub_bin / "su").chmod(0o755)
            fake_adb = root / "adb"
            fake_adb.write_text(
                f'''#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
with open({str(adb_log)!r}, "a") as fh:
    fh.write(" ".join(args) + "\\n")
if args == ["start-server"]:
    sys.exit(0)
if args == ["devices"]:
    print("List of devices attached\\nTAB01\\tdevice\\n")
    sys.exit(0)
if args[:2] == ["-s", "TAB01"] and args[2] == "push":
    sys.exit(0)
if args[:2] == ["-s", "TAB01"] and args[2] == "shell":
    flat_cmd = " ".join(args[3:])
    env = dict(os.environ, PATH={str(stub_bin)!r} + ":" + os.environ.get("PATH", ""))
    # Stub non-root shell commands so real adbd splitting behavior is exercised accurately
    for cmd_name in ("chown", "chmod", "sh"):
        pass
    cp = subprocess.run(["/bin/sh", "-c", flat_cmd.replace("rm -rf", "true").replace("mkdir -p -m 0700", "true")], env=env, capture_output=True, text=True)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr)
        sys.exit(cp.returncode)
    print("STAGE_RESULT=ok")
    sys.exit(0)
sys.exit(64)
''',
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            env = dict(os.environ, ADB_BIN=str(fake_adb))
            for action in ("stage", "preflight", "uninstall"):
                act_res = subprocess.run(["sh", str(kit_dir / "run_session.sh"), action], env=env, capture_output=True, text=True)
                self.assertEqual(act_res.returncode, 0, f"{action}: {act_res.stdout}\n{act_res.stderr}")
            calls_txt = adb_log.read_text()
            su_txt = su_log.read_text()
            self.assertIn("-m 0700 /data/local/tmp/mt6639-kit-session", calls_txt)
            self.assertIn("mt6639-6g-v0.4.1.zip", calls_txt)
            self.assertIn("mt6639-ui-lsposed-0.3.0.apk", calls_txt)
            self.assertIn("chown -R 0:0 /data/local/tmp/mt6639-kit-session && chmod 0700", su_txt)
            self.assertIn("sh /data/local/tmp/mt6639-kit-session/device_tablet_session.sh preflight", su_txt)
            self.assertIn("sh /data/local/tmp/mt6639-kit-session/device_tablet_session.sh cleanup --uninstall", su_txt)

    def test_device_helper_executes_bounded_trial_and_stage_in_sandbox(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-devsh-test-") as td:
            root = Path(td)
            mod6g = root / "modules/mt6639-6g"
            (mod6g / "bin").mkdir(parents=True)
            modbt = root / "modules/mt6639-bt"
            (modbt / "bin").mkdir(parents=True)
            modhs = root / "modules/mt6639-hotspot"
            (modhs / "bin").mkdir(parents=True)
            for d in (mod6g, modhs):
                (d / "SHA256SUMS").write_text("dummy\n")
            upd = root / "modules_update"
            for m in ("mt6639-6g", "mt6639-bt", "mt6639-hotspot"):
                (upd / m).mkdir(parents=True)
                (upd / m / "SHA256SUMS").write_text("dummy\n")

            (mod6g / "config.conf").write_text("ENABLE_6G=${ENABLE_6G:-1}\nRUN_MODE=${RUN_MODE:-persistent}\n")
            before_cfg_sha = hashlib.sha256((mod6g / "config.conf").read_bytes()).hexdigest()
            (mod6g / "disable").write_text("")
            (mod6g / "bin/verify-module.sh").write_text("#!/bin/sh\nexit 0\n")
            (mod6g / "bin/control.sh").write_text(
                '#!/bin/sh\n'
                'echo "CTRL $2 RUN_MODE=${RUN_MODE:-unset} DISABLE_PRESENT=$([ -e "$1/disable" ] && echo 1 || echo 0)" >> "$TEST_CALLS"\n'
                'if [ "$2" = enable ] && [ -e "$1/disable" ]; then exit 3; fi\n'
                'if [ "$2" = status ]; then echo "state=active"; fi\n'
            )
            stub = root / "stub"
            stub.mkdir()
            calls = root / "calls.log"
            for name, body in {
                "id": '#!/bin/sh\nif [ "$1" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n',
                "getenforce": "#!/bin/sh\necho Enforcing\n",
                "ksud": '#!/bin/sh\necho "ksud $*" >> "$TEST_CALLS"\nexit 0\n',
                "pm": '#!/bin/sh\nif [ "$1" = install ]; then n=$(wc -c | tr -d "[:space:]"); echo "pm $* stdin=$n" >> "$TEST_CALLS"; else echo "pm $*" >> "$TEST_CALLS"; fi\nexit 0\n',
                "iw": '#!/bin/sh\nprintf "Band 4:\\n\\t* 5955 MHz [1]\\n\\t* 6135 MHz [37]\\n"\n',
                "sha256sum": '#!/bin/sh\nif [ "$1" = -c ]; then exit 0; fi\nexec /usr/bin/sha256sum "$@"\n',
            }.items():
                p = stub / name
                p.write_text(body)
                p.chmod(0o755)
            (root / "enforce").write_text("1\n")

            script_copy = root / "dev.sh"
            txt = DEV_SH.read_text(encoding="utf-8")
            txt = (
                txt.replace("/data/adb/modules_update", str(upd))
                .replace("/data/adb/modules", str(root / "modules"))
                .replace("/data/adb/ksud", str(stub / "ksud"))
                .replace("/sys/fs/selinux/enforce", str(root / "enforce"))
                .replace("/data/local/tmp/mt6639-kit-", str(root / "kit-"))
            )
            script_copy.write_text(txt, encoding="utf-8")
            stage_dir = root / "kit-1"
            stage_dir.mkdir()
            (stage_dir / "SHA256SUMS").write_text("")
            (stage_dir / "mt6639-ui-lsposed-0.3.0.apk").write_bytes(b"apkbytes")
            env = dict(os.environ, PATH=f"{stub}:/usr/bin:/bin", TEST_CALLS=str(calls))

            res_trial = subprocess.run(["sh", str(script_copy), "bounded-trial"], env=env, capture_output=True, text=True)
            self.assertEqual(res_trial.returncode, 0, res_trial.stdout + res_trial.stderr)
            self.assertIn("TRIAL_ENABLE_RC=0", res_trial.stdout)
            self.assertIn("SIXG_ENABLED_COUNT=2", res_trial.stdout)
            self.assertTrue((mod6g / "disable").is_file())
            self.assertEqual(hashlib.sha256((mod6g / "config.conf").read_bytes()).hexdigest(), before_cfg_sha)

            res_stage = subprocess.run(["sh", str(script_copy), "stage-from-dir", str(stage_dir)], env=env, capture_output=True, text=True)
            self.assertEqual(res_stage.returncode, 0, res_stage.stdout + res_stage.stderr)
            self.assertTrue((upd / "mt6639-6g/disable").is_file())
            self.assertIn("ksud module install", calls.read_text())
            self.assertIn("pm install -r -S 8 stdin=8", calls.read_text())

            # Test direct-to-/data/adb/modules fallback when modules_update does not exist
            shutil.rmtree(upd)
            (mod6g / "disable").unlink()
            res_stage_live = subprocess.run(["sh", str(script_copy), "stage-from-dir", str(stage_dir)], env=env, capture_output=True, text=True)
            self.assertEqual(res_stage_live.returncode, 0, res_stage_live.stdout + res_stage_live.stderr)
            self.assertTrue((mod6g / "disable").is_file())

            res_clean = subprocess.run(["sh", str(script_copy), "cleanup", "--uninstall"], env=env, capture_output=True, text=True)
            self.assertEqual(res_clean.returncode, 0, res_clean.stdout + res_clean.stderr)
            self.assertIn("ksud module uninstall mt6639-6g", calls.read_text())
            self.assertIn("pm uninstall com.hiliang.mt6639ui", calls.read_text())


if __name__ == "__main__":
    unittest.main()
