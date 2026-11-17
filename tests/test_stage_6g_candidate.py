"""Tests for the read-only plan and preflight of the 6GHz upgrade staging tool."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest


import pubenv

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage_6g_candidate", REPO / "tools/stage_6g_candidate.py")
STAGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = STAGE
SPEC.loader.exec_module(STAGE)
MANIFEST = REPO / "release/mt6639-candidate-20261026.json"


@unittest.skipUnless(pubenv.candidate_ready(), pubenv.SKIP_RELEASE_ASSETS)
class Stage6gCandidateTests(unittest.TestCase):
    def test_pinned_candidate_is_loaded_and_plan_stops_before_reboot(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        self.assertEqual(candidate.artifact_id, "mt6639-6g")
        self.assertEqual(candidate.version, "v0.3.0")
        plan = STAGE.plan_steps(candidate)
        self.assertEqual(
            [step["id"] for step in plan],
            [
                "verify_device",
                "backup_active_and_runtime",
                "restore_old_transaction",
                "verify_restored_stock",
                "push_candidate",
                "verify_remote_candidate",
                "install_to_modules_update",
                "verify_staged_module",
                "stop_before_reboot",
            ],
        )
        self.assertEqual(plan[-1]["id"], "stop_before_reboot")
        self.assertIn("without rebooting", plan[-1]["command"])
        self.assertFalse(any(step["command"].startswith(("reboot", "ksud soft-reboot"))
                             for step in plan))

    def test_candidate_hash_mismatch_is_rejected(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["artifacts"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="mt6639-stage-test-") as td:
            manifest = Path(td) / "manifest.json"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(STAGE.StageError) as caught:
                STAGE.load_candidate(manifest, REPO)
        self.assertEqual(caught.exception.code, "CANDIDATE_HASH_MISMATCH")

    def test_execute_backs_up_restores_and_stages_without_reboot(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-stage-adb-") as td:
            root = Path(td)
            log = root / "adb.log"
            disabled = root / "disabled"
            staged = root / "staged"
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, pathlib, sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['FAKE_ADB_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if args and args[0]=='push': print('1 file pushed'); sys.exit(0)\n"
                "if cmd is None: sys.exit(64)\n"
                "marker=pathlib.Path(os.environ['FAKE_DISABLE_MARKER'])\n"
                "staged=pathlib.Path(os.environ['FAKE_STAGED_MARKER'])\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print(os.environ.get('FAKE_SU_ID','uid=0(root) context=u:r:ksu:s0'))\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print(os.environ.get('FAKE_SELINUX','Enforcing'))\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.2.6\\nversionCode=8')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('11111111-1111-4111-8111-111111111111')\n"
                "elif cmd=='mktemp -d /data/local/tmp/mt6639-stage-XXXXXX': print('/data/local/tmp/mt6639-stage-AAAAAA')\n"
                "elif cmd.startswith('tar -C /data/adb'): pass\n"
                "elif cmd=='tar -tzf /data/local/tmp/mt6639-stage-AAAAAA/backup.tar.gz': print('modules/mt6639-6g/module.prop\\nmt6639-6g/state')\n"
                "elif cmd.startswith('sha256sum /data/local/tmp/mt6639-stage-AAAAAA/backup.tar.gz'): print('b'*64+'  backup.tar.gz')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g disable'): marker.touch(); print('restored')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g status'):\n"
                "  print('state=restored\\nactive_tx=none' if marker.exists() else 'state=active\\nactive_tx=/data/adb/mt6639-6g/transactions/t-old\\nguardian=alive')\n"
                "elif cmd.startswith('sha256sum /odm/firmware/wifi.cfg'): print('e4dcd846f31077864932b499d462cfd6a8ca1befc6386f502813e0ba053cc154  /odm/firmware/wifi.cfg')\n"
                "elif cmd.startswith('sha256sum /data/local/tmp/mt6639-stage-AAAAAA/candidate.zip'): print(os.environ['FAKE_CANDIDATE_HASH']+'  candidate.zip')\n"
                "elif cmd.startswith('/data/adb/ksud module install '): staged.touch(); print('installed')\n"
                "elif cmd=='cat /data/adb/modules_update/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.3.0\\nversionCode=22')\n"
                "elif cmd.startswith('cd /data/adb/modules_update/mt6639-6g'): print('SHA256SUMS: OK')\n"
                "else: print('unexpected:'+cmd, file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            fake_env = dict(
                os.environ,
                FAKE_ADB_LOG=str(log),
                FAKE_DISABLE_MARKER=str(disabled),
                FAKE_STAGED_MARKER=str(staged),
                FAKE_CANDIDATE_HASH=candidate.sha256,
            )
            result = STAGE.execute(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=fake_env)
            log_text = log.read_text()
        self.assertEqual(result["status"], "staged_requires_reboot")
        records = [json.loads(line) for line in log_text.splitlines()]
        commands = [record[record.index("shell") + 1] for record in records
                    if "shell" in record]
        joined = "\n".join(commands)
        self.assertIn("tar -C /data/adb", joined, joined)
        self.assertIn("control.sh", joined, joined)
        self.assertLess(joined.index("tar -C /data/adb"), joined.index("control.sh"))
        self.assertLess(
            joined.index("tar -tzf /data/local/tmp/mt6639-stage-AAAAAA/backup.tar.gz"),
            joined.index("control.sh"))
        self.assertLess(joined.index("control.sh"), joined.index("ksud module install"))
        self.assertIn("sha256sum -c SHA256SUMS", joined)
        self.assertFalse(any(command.startswith(("reboot", "ksud soft-reboot"))
                             for command in commands))

    def test_probe_is_read_only_and_reports_precondition_failures(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-stage-probe-") as td:
            root = Path(td)
            log = root / "adb.log"
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['FAKE_ADB_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print(os.environ['FAKE_SU_ID'])\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print(os.environ['FAKE_SELINUX'])\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.2.6\\nversionCode=8')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('11111111-1111-4111-8111-111111111111')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            fake_env = dict(
                os.environ,
                FAKE_ADB_LOG=str(log),
                FAKE_SU_ID="uid=0(root) context=u:r:kernel:s0",
                FAKE_SELINUX="Permissive",
            )
            result = STAGE.probe_device(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=fake_env)
            log_text = log.read_text()
        self.assertFalse(result["ready"])
        self.assertEqual(
            result["failures"], ["ksu_context", "selinux_enforcing"])
        records = [json.loads(line) for line in log_text.splitlines()]
        commands = [record[record.index("shell") + 1] for record in records
                    if "shell" in record]
        self.assertFalse(any("control.sh" in command or "ksud module install" in command
                             or command.startswith("tar ") for command in commands))

    def test_postboot_probe_requires_v028_active_transaction_and_missing_sixg(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-postboot-") as td:
            root = Path(td)
            log = root / "adb.log"
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['FAKE_ADB_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print('uid=0(root) context=u:r:ksu:s0')\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print('Enforcing')\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.3.0\\nversionCode=22')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('22222222-2222-4222-8222-222222222222')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g status'): print('state=active\\nactive_tx=/data/adb/mt6639-6g/transactions/t-new\\nguardian=alive')\n"
                "elif cmd=='iw phy': print('Band 1: 2412 MHz\\nBand 2: 5180 MHz')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            fake_env = dict(os.environ, FAKE_ADB_LOG=str(log))
            result = STAGE.postboot_probe(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=fake_env)
            log_text = log.read_text()
        self.assertTrue(result["ready"])
        self.assertTrue(result["active_version_matches"])
        self.assertTrue(result["transaction_active"])
        self.assertEqual(result["sixg_channels"], 0)
        self.assertTrue(result["refresh_required"])
        records = [json.loads(line) for line in log_text.splitlines()]
        commands = [record[record.index("shell") + 1] for record in records
                    if "shell" in record]
        self.assertFalse(any(" refresh" in command or "module install" in command
                             or command.startswith("tar ") for command in commands))

    def test_postboot_probe_rejects_transient_sixg_sampling(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-postboot-jitter-") as td:
            root = Path(td)
            count_file = root / "iw-count"
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, sys\n"
                "args=sys.argv[1:]\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print('uid=0(root) context=u:r:ksu:s0')\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print('Enforcing')\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.3.0\\nversionCode=22')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('22222222-2222-4222-8222-222222222222')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g status'): print('state=active\\nactive_tx=/data/adb/mt6639-6g/transactions/t-new\\nguardian=alive')\n"
                "elif cmd=='iw phy':\n"
                " p=pathlib.Path(os.environ['FAKE_IW_COUNT']); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\n"
                " print('* 6135 MHz' if n==2 else 'Band 2: 5180 MHz')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            result = STAGE.postboot_probe(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=dict(os.environ, FAKE_IW_COUNT=str(count_file)))
        self.assertFalse(result["ready"])
        self.assertEqual(result["sixg_samples"], [0, 1, 0])
        self.assertIsNone(result["sixg_channels"])
        self.assertFalse(result["refresh_required"])
        self.assertIn("iw_phy_unstable", result["failures"])

    def test_postboot_probe_ignores_disabled_sixg_channels(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-postboot-disabled-") as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import sys\n"
                "args=sys.argv[1:]\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print('uid=0(root) context=u:r:ksu:s0')\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print('Enforcing')\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.3.0\\nversionCode=22')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('22222222-2222-4222-8222-222222222222')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g status'): print('state=active\\nactive_tx=/data/adb/mt6639-6g/transactions/t-new\\nguardian=alive')\n"
                "elif cmd=='iw phy': print('    * 6135.0 MHz [37] (disabled)')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            result = STAGE.postboot_probe(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=dict(os.environ))
        self.assertTrue(result["ready"])
        self.assertEqual(result["sixg_samples"], [0, 0, 0])
        self.assertEqual(result["sixg_channels"], 0)
        self.assertTrue(result["refresh_required"])

    def test_postboot_probe_requires_target_6135_not_any_6ghz(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-postboot-other6g-") as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import sys\n"
                "args=sys.argv[1:]\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print('uid=0(root) context=u:r:ksu:s0')\n"
                "elif cmd=='getprop ro.build.fingerprint': print('Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys')\n"
                "elif cmd=='uname -r': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')\n"
                "elif cmd=='getenforce': print('Enforcing')\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.3.0\\nversionCode=22')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('22222222-2222-4222-8222-222222222222')\n"
                "elif cmd.endswith('control.sh /data/adb/modules/mt6639-6g status'): print('state=active\\nactive_tx=/data/adb/mt6639-6g/transactions/t-new\\nguardian=alive')\n"
                "elif cmd=='iw phy': print('    * 5955.0 MHz [1]')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8")
            adb.chmod(0o755)
            result = STAGE.postboot_probe(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=dict(os.environ))
        self.assertTrue(result["ready"])
        self.assertEqual(result["sixg_samples"], [1, 1, 1])
        self.assertEqual(result["sixg_6135_samples"], [0, 0, 0])
        self.assertTrue(result["refresh_required"])

    def test_os4_stable_kernel_and_fingerprint_are_accepted(self):
        candidate = STAGE.load_candidate(MANIFEST, REPO)
        with tempfile.TemporaryDirectory(prefix="mt6639-stage-os4stable-") as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(
                "#!/usr/bin/python3\n"
                "import os, sys\n"
                "args=sys.argv[1:]\n"
                "if args[:2]==['-P','5038']: args=args[2:]\n"
                "if args[:2]==['-s','YOUR_SERIAL']: args=args[2:]\n"
                "cmd=args[1] if args and args[0]=='shell' else None\n"
                "if cmd=='id -u': print('0')\n"
                "elif cmd=='su -c id': print('uid=0(root) context=u:r:ksu:s0')\n"
                "elif cmd=='getprop ro.build.fingerprint': print(os.environ['FAKE_FP'])\n"
                "elif cmd=='uname -r': print(os.environ['FAKE_KERNEL'])\n"
                "elif cmd=='getenforce': print('Enforcing')\n"
                "elif cmd=='cat /data/adb/modules/mt6639-6g/module.prop': print('id=mt6639-6g\\nversion=v0.2.16\\nversionCode=18')\n"
                "elif cmd=='cat /proc/sys/kernel/random/boot_id': print('11111111-1111-4111-8111-111111111111')\n"
                "else: print('unexpected:'+str(cmd), file=sys.stderr); sys.exit(70)\n",
                encoding="utf-8",
            )
            adb.chmod(0o755)
            accepted = STAGE.probe_device(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=dict(
                    os.environ,
                    FAKE_FP="Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.4.0.XODCNXM:user/release-keys",
                    FAKE_KERNEL="6.18.21-android17-5-g284297031c53-abogki553682993-4k",
                ),
            )
            self.assertTrue(accepted["ready"], accepted)
            rejected = STAGE.probe_device(
                candidate, adb=str(adb), serial="YOUR_SERIAL", port=5038,
                env=dict(
                    os.environ,
                    FAKE_FP="Xiaomi/other/other:17/CP2A/OS3.0.0.1:user/release-keys",
                    FAKE_KERNEL="6.18.21-android17-5-unknown",
                ),
            )
            self.assertFalse(rejected["ready"])
            self.assertEqual(rejected["failures"], ["fingerprint", "kernel"])


if __name__ == "__main__":
    unittest.main()
