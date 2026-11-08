"""Small integration smoke test: real trial + recovery shell, device I/O stubbed.

This is not a full failure-injection/lifecycle suite. That suite is deferred to
the final test plan, along with real device process and timing validation.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_twt_recovery as recovery

BOOT, ROOT = recovery.BOOT, recovery.ROOT


class TrialSmokeTests(unittest.TestCase):
    def setUp(self):
        recovery.RecoveryTests.setUp(self)
        self.task.chmod(0o700)
        (self.task / "reconnect.key").chmod(0o600)
        for source, target in (("twt_once.sh", "trial.sh"), ("twt_recover.sh", "recover.sh")):
            shutil.copy2(ROOT / "tools" / source, self.task / target)
        (self.task / "once.ko").write_bytes(b"test fixture, not a kernel module")
        backup = self.task / "backup"
        backup.mkdir()
        for name in ("wifi.cfg", "config.conf", "module.prop",
                     "WifiConfigStore-shared.xml", "WifiConfigStore-user0.xml"):
            (backup / name).write_bytes(b"backup")
        self.env["TRIAL_TASK"] = str(self.task)
        io = self.bin / "io"
        code = io.read_text()
        code = code.replace("args=sys.argv[1:]", """args=sys.argv[1:]
task=pathlib.Path(os.environ['TRIAL_TASK'])
post=(task/'cycled').exists()
band=os.environ.get('POST_BAND' if post else 'BAND','6G')
""")
        code = code.replace("if os.environ.get('MODULE_LEFT'):",
                            "if (task/'module-loaded').exists() or os.environ.get('MODULE_LEFT'):")
        code = code.replace("elif name=='sleep': pass", """elif name=='sleep':
 import time
 time.sleep(4 if args==['20'] else 0.01)
""")
        code = code.replace("elif name=='timeout': os.execvp(args[1],args[1:])", """elif name=='timeout':
 if args[0]=='-k': args=args[2:]
 os.execvp(args[1],args[1:])
""")
        code = code.replace("if args==['wifi','status']:", """if args[:2]==['wifi','set-wifi-enabled']:
  with (task/'ops').open('a') as f: f.write('wifi '+args[2]+'\\n')
  if args[2]=='disabled': (task/'radio-down').touch(); (task/'cycled').touch()
  else: (task/'radio-down').unlink(missing_ok=True)
  sys.exit(0)
 if args==['wifi','status']:
  if (task/'radio-down').exists(): emit('Wifi is disabled'); sys.exit(0)
""")
        code = code.replace("  band=os.environ.get('BAND','6G')", "  # band selected by phase")
        code = code.replace(" band=os.environ.get('BAND','6G')", " # band selected by phase")
        code = code.replace("else: sys.exit(64)", """elif name=='stat':
 import stat
 emit('0:'+format(stat.S_IMODE(pathlib.Path(args[-1]).stat().st_mode),'o'))
elif name=='sha256sum':
 import hashlib
 p=args[0]
 if p.endswith('/once.ko'): digest='a68139b1aa1f9e83b241445f41e9d1a142e4826309c8b4301efd82ad2442e61e'
 elif p=='/vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko': digest='54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea'
 elif p=='/sys/kernel/btf/vmlinux': digest='cff622b3a38fce9b608b7063ab93025118fd2afd8d32b187174d7473751d1be2'
 else: digest=hashlib.sha256(pathlib.Path(p).read_bytes() if pathlib.Path(p).exists() else b'backup').hexdigest()
 emit(digest+'  '+p)
elif name=='flock':
 import fcntl
 fcntl.flock(int(args[-1]),fcntl.LOCK_EX | (fcntl.LOCK_NB if '-n' in args else 0))
elif name in ('insmod','rmmod','iwpriv'):
 with (task/'ops').open('a') as f: f.write(name+' '+' '.join(args)+'\\n')
 if name=='insmod': (task/'module-loaded').touch()
 elif name=='rmmod': (task/'module-loaded').unlink()
else: sys.exit(64)
""")
        io.write_text(code)
        for name in ("stat", "sha256sum", "flock", "insmod", "rmmod", "iwpriv"):
            (self.bin / name).symlink_to(io)

    def run_trial(self, **extra):
        result = subprocess.run([
            "sh", str(self.task / "trial.sh"), "run", str(self.task), BOOT,
            "LabNet-6G", "6135", "02:00:00:00:73:1c", "198.51.100.242", str(self.checker),
        ], env=dict(self.env, **extra), capture_output=True, text=True, timeout=40)
        self.assertNotIn("TEST-SECRET-DO-NOT-LOG", result.stdout + result.stderr)
        return result

    def test_one_request_then_recovery_and_normal_unload(self):
        r = self.run_trial()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.task / "recovered").exists())
        ops = (self.task / "ops").read_text()
        self.assertEqual(ops.count('SET_TWT_PARAMS 4 '), 1)
        self.assertEqual(ops.count('SET_TWT_PARAMS 5 '), 1)
        self.assertIn('wifi disabled\nwifi enabled\nrmmod mttwt_once', ops)
        self.assertFalse((self.task / "module-loaded").exists())
        self.assertNotEqual(self.run_trial().returncode, 0)  # same task cannot repeat

    def test_preflight_failure_never_loads_or_requests(self):
        r = self.run_trial(PING_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.task / "ops").exists())
        self.assertFalse((self.task / "recovered").exists())

    def test_wrong_band_after_cli_success_has_no_success_marker(self):
        r = self.run_trial(POST_BAND="5G")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.task / "recovered").exists())
        self.assertIn('status=failed', (self.task / "result.txt").read_text())


if __name__ == '__main__':
    unittest.main()
