"""Regression tests for the actual Android recovery script, with device I/O stubbed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/twt_recover.sh"
BOOT = "a1a1a1a1-1111-4111-8111-111111111111"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="twt-recovery-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / "task"
        self.task.mkdir(mode=0o700)
        (self.task / "reconnect.key").write_text("TEST-SECRET-DO-NOT-LOG")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "io"
        stub.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
state=pathlib.Path(os.environ['TEST_STATE'])
s=json.loads(state.read_text()) if state.exists() else {'time':0, 'connect':0, 'status':0, 'boot':0}
def save(): state.write_text(json.dumps(s))
def emit(text): print(text)
if name=='id': emit('0')
elif name=='cat' and args==['/proc/sys/kernel/random/boot_id']:
 s['boot']+=1; save()
 emit('changed-boot' if os.environ.get('BOOT_CHANGE') and s['boot']>2 else os.environ['EXPECTED_BOOT'])
elif name=='cat':
 if args==['/proc/modules']:
  if os.environ.get('MODULE_LEFT'): emit('mttwt_once 4096 0 - Live 0')
 else:
  for p in args: sys.stdout.write(pathlib.Path(p).read_text())
elif name=='date':
 s['time']+=5; save(); emit(s['time'])
elif name=='sleep': pass
elif name=='timeout': os.execvp(args[1],args[1:])
elif name=='cmd':
 if args[:2]==['wifi','connect-network']:
  s['connect']+=1; save()
  # A successful command must not itself establish recovery.
  sys.exit(int(os.environ.get('CONNECT_RC','0')))
 if args==['wifi','status']:
  s['status']+=1; save()
  if int(os.environ.get('READY_AFTER','0'))>=s['status']: sys.exit(1)
  emit('Wifi is enabled')
  band=os.environ.get('BAND','6G')
  emit('Wifi is connected to "LabNet-'+band+'"')
  emit('Supplicant state: COMPLETED, Frequency: '+('6135' if band=='6G' else '5180')+'MHz')
elif name=='iw':
 if args[-1]=='info': emit('Interface wlan0'); sys.exit(0)
 band=os.environ.get('BAND','6G')
 emit('Connected to 02:00:00:00:73:'+('1c' if band=='6G' else '1b')+' (on wlan0)')
 emit('\\tSSID: LabNet-'+band)
 emit('\\tfreq: '+('6135.0' if band=='6G' else '5180.0'))
elif name=='ip':
 if 'addr' in args:
  if not os.environ.get('NO_IP'): emit('15: wlan0 inet 198.51.100.187/24 scope global wlan0')
 elif 'route' in args:
  emit('198.51.100.242 dev '+('rmnet0' if os.environ.get('WRONG_ROUTE') else 'wlan0')+' src 198.51.100.187')
 else: emit('15: wlan0: <UP>')
elif name=='ping':
 if os.environ.get('PING_FAIL'): sys.exit(1)
 emit('3 packets transmitted, 3 received, 0% packet loss')
else: sys.exit(64)
''')
        stub.chmod(0o755)
        for name in ("id", "cat", "date", "sleep", "timeout", "cmd", "iw", "ip", "ping"):
            (self.bin / name).symlink_to(stub)
        self.checker = self.root / "checker.sh"
        self.checker.write_text('''#!/bin/sh
if [ "${CHECK_FAIL:-0}" = 1 ]; then echo 'RESULT fail=1'; exit 1; fi
echo 'RESULT fail=0'
''')
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_STATE=str(self.root / "state.json"), EXPECTED_BOOT=BOOT)

    def run_recovery(self, mode="select", **extra):
        result = subprocess.run(["sh", str(SCRIPT), mode, str(self.task), BOOT,
                                 "LabNet-6G", "6135", "02:00:00:00:73:1c",
                                 "198.51.100.242", str(self.checker)],
                                env=dict(self.env, **extra), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertNotIn("TEST-SECRET-DO-NOT-LOG", result.stdout)
        return result

    def assert_failed(self, **extra):
        r = self.run_recovery(**extra)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.task / "verification/recovered").exists())
        self.assertIn("status=failed", (self.task / "verification/result.txt").read_text())
        return r

    def test_success_requires_real_target_and_connects_once(self):
        r = self.run_recovery(READY_AFTER="2")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((self.task / "verification/recovered").exists())
        s = json.loads((self.root / "state.json").read_text())
        self.assertEqual(s["connect"], 1)
        self.assertGreater(s["status"], 2)

    def test_check_is_read_only_and_does_not_need_secret(self):
        (self.task / "reconnect.key").unlink()
        r = self.run_recovery("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads((self.root / "state.json").read_text())["connect"], 0)

    def test_cli_success_on_5g_is_failure(self):
        self.assert_failed(BAND="5G")

    def test_target_without_ip_or_route_is_failure(self):
        for setting in ("NO_IP", "WRONG_ROUTE"):
            with self.subTest(setting=setting):
                # Each transaction owns a fresh evidence directory.
                self.task = self.root / setting
                self.task.mkdir()
                (self.task / "reconnect.key").write_text("TEST-SECRET-DO-NOT-LOG")
                self.assert_failed(**{setting: "1"})

    def test_readiness_timeout_never_connects(self):
        self.assert_failed(READY_AFTER="1000")
        self.assertEqual(json.loads((self.root / "state.json").read_text())["connect"], 0)

    def test_checker_ping_command_and_boot_failures_are_terminal(self):
        for setting in ("CHECK_FAIL", "PING_FAIL", "CONNECT_RC", "BOOT_CHANGE", "MODULE_LEFT"):
            with self.subTest(setting=setting):
                self.task = self.root / setting
                self.task.mkdir()
                (self.task / "reconnect.key").write_text("TEST-SECRET-DO-NOT-LOG")
                self.assert_failed(**{setting: "1"})

    def test_existing_transaction_is_not_overwritten(self):
        self.assertEqual(self.run_recovery().returncode, 0)
        before = (self.task / "verification/result.txt").read_bytes()
        self.assertNotEqual(self.run_recovery().returncode, 0)
        self.assertEqual((self.task / "verification/result.txt").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
