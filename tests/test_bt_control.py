"""Run the real Bluetooth control script with fixture-only properties and paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class BluetoothSnapshotTest(unittest.TestCase):
    def test_failed_preparation_is_not_published_and_can_retry(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-control-") as tmp:
            root = Path(tmp)
            module = root / "module"
            shutil.copytree(REPO / "modules/mt6639-bt", module)
            script = module / "bin/control.sh"
            base = root / "state"
            boot = root / "boot"
            boot_id = "11111111-1111-4111-8111-111111111111"
            boot.write_text(boot_id + "\n")
            script.write_text(script.read_text().replace(
                "BASE=/data/adb/mt6639-bt", "BASE=" + str(base)).replace(
                "/proc/sys/kernel/random/boot_id", str(boot)))
            stub = root / "stub"
            stub.mkdir()
            commands = {
                "id": "#!/bin/sh\nif [ \"$1\" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n",
                "getenforce": "#!/bin/sh\necho Enforcing\n",
                "flock": "#!/usr/bin/python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n",
                "getprop": '''#!/bin/sh
case "$1" in
 ro.product.device) echo dijun ;;
 ro.build.fingerprint) echo Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys ;;
 ro.bluetooth.leaudio_offload.supported) echo true ;;
 persist.bluetooth.leaudio_offload.disabled) echo false ;;
 bluetooth.profile.csip.set_coordinator.enabled)
  if [ "${BAD_BASELINE:-0}" = 1 ]; then echo invalid; else cat "$PROPS/$1" 2>/dev/null || echo false; fi ;;
 *) cat "$PROPS/$1" 2>/dev/null || echo false ;;
esac
''',
                "setprop": '#!/bin/sh\necho "$1 $2" >> "$WRITES"\nprintf "%s\\n" "$2" > "$PROPS/$1"\n',
                "sha256sum": '''#!/bin/sh
case "$1" in
 /apex/com.android.bt/*) echo '22f2e2ac8c036f7716e792f1fa54d7f5c3dd53754cb3c01db93ee3981cd34339  fixture.apk' ;;
 *) exec "REAL_SHA" "$@" ;;
esac
'''.replace("REAL_SHA", shutil.which("sha256sum")),
            }
            for name, source in commands.items():
                path = stub / name
                path.write_text(source)
                path.chmod(0o755)
            props = root / "properties"
            props.mkdir()
            writes = root / "writes"
            env = dict(os.environ, PATH=str(stub) + ":" + os.environ["PATH"],
                       PROPS=str(props), WRITES=str(writes))

            def run(op, **extra):
                return subprocess.run(["sh", str(script), op], env=dict(env, **extra),
                                      text=True, capture_output=True, timeout=20)

            failed = run("enable", BAD_BASELINE="1")
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((base / boot_id).exists(), failed.stderr)
            self.assertFalse(writes.exists())
            self.assertTrue(list(base.glob(".prepare-*")))
            enabled = run("enable")
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertEqual((base / boot_id / "state").read_text(), "active\n")
            self.assertEqual(len(writes.read_text().splitlines()), 6)
            restored = run("restore")
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual((base / boot_id / "state").read_text(), "restored\n")
            self.assertTrue(all(p.read_text() == "false\n" for p in props.iterdir()))

    def test_state_root_rejects_symlink_and_file_without_touching_targets(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-root-") as tmp:
            root = Path(tmp)
            module = root / "module"
            shutil.copytree(REPO / "modules/mt6639-bt", module)
            script = module / "bin/control.sh"
            base = root / "state"
            boot = root / "boot"
            boot.write_text("11111111-1111-4111-8111-111111111111\n")
            script.write_text(script.read_text().replace(
                "BASE=/data/adb/mt6639-bt", "BASE=" + str(base)).replace(
                "/proc/sys/kernel/random/boot_id", str(boot)))
            stub = root / "stub"
            stub.mkdir()
            for name, body in {
                "id": '#!/bin/sh\nif [ "$1" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n',
                "getenforce": '#!/bin/sh\necho Enforcing\n',
                "getprop": '#!/bin/sh\necho dijun\n',
            }.items():
                p = stub / name
                p.write_text(body)
                p.chmod(0o755)
            env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")

            target = root / "outside"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            base.symlink_to(target)
            res_sym = subprocess.run(["sh", str(script), "status"], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(res_sym.returncode, 0)
            self.assertIn("state_root_symlink", res_sym.stderr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

            base.unlink()
            base.write_text("regular-file")
            res_file = subprocess.run(["sh", str(script), "status"], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(res_file.returncode, 0)
            self.assertIn("state_root_not_dir", res_file.stderr)
            self.assertEqual(base.read_text(), "regular-file")
