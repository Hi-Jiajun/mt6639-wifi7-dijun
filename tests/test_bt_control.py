"""Run the real Bluetooth control script with fixture-only properties and paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]


class BluetoothSnapshotTest(unittest.TestCase):
    def make_fixture(self, tmp: Path, apex_subdir: str = "Bluetooth@CP2A.260605.016"):
        module = tmp / "module"
        shutil.copytree(REPO / "modules/mt6639-bt", module)
        script = module / "bin/control.sh"
        base = tmp / "state"
        boot = tmp / "boot"
        boot_id = "11111111-1111-4111-8111-111111111111"
        boot.write_text(boot_id + "\n")
        apex_app = tmp / "apex_app"
        apk_dir = apex_app / apex_subdir if apex_subdir else apex_app
        apk_dir.mkdir(parents=True)
        (apk_dir / "Bluetooth.apk").write_bytes(b"fixture-apk\n")
        script.write_text(
            script.read_text()
            .replace("BASE=/data/adb/mt6639-bt", "BASE=" + str(base))
            .replace("APEX_APP_ROOT=/apex/com.android.bt/app", "APEX_APP_ROOT=" + str(apex_app))
            .replace("/proc/sys/kernel/random/boot_id", str(boot))
        )
        return module, script, base, boot_id, apex_app

    def test_failed_preparation_is_not_published_and_can_retry(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-control-") as tmp:
            root = Path(tmp)
            _, script, base, boot_id, _ = self.make_fixture(root)
            stub = root / "stub"
            stub.mkdir()
            commands = {
                "id": "#!/bin/sh\nif [ \"$1\" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n",
                "getenforce": "#!/bin/sh\necho Enforcing\n",
                "flock": "#!/usr/bin/python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n",
                "getprop": '''#!/bin/sh
case "$1" in
 ro.product.device) echo "${FIXTURE_DEVICE:-dijun}" ;;
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
 */Bluetooth.apk) echo "${FIXTURE_APK_SHA:-22f2e2ac8c036f7716e792f1fa54d7f5c3dd53754cb3c01db93ee3981cd34339}  fixture.apk" ;;
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

    def test_multi_device_and_apex_validation(self):
        cases = [
            ("dijun", "Bluetooth@CP2A.260605.016", "22f2e2ac8c036f7716e792f1fa54d7f5c3dd53754cb3c01db93ee3981cd34339", True, ""),
            ("violin", "Bluetooth@361419040", "1a5ae65ccd8621c03ca5a6a035979eac74025c3ecdda19dd84f1bbd53162983e", True, ""),
            ("jinghu", "Bluetooth@350910000", "5813fb9649007857900559e38bb323a951ee00150f21468bc9b131a51e789b86", True, ""),
            # Cross-device APK hash must be rejected cleanly.
            ("dijun", "Bluetooth@361419040", "1a5ae65ccd8621c03ca5a6a035979eac74025c3ecdda19dd84f1bbd53162983e", False, "unsupported_bluetooth_apk"),
            # Unknown device rejected at gate before reading properties.
            ("other", "Bluetooth@361419040", "1a5ae65ccd8621c03ca5a6a035979eac74025c3ecdda19dd84f1bbd53162983e", False, "wrong_device"),
        ]
        for device, subdir, apk_sha, ok, err_text in cases:
            with self.subTest(device=device, subdir=subdir, ok=ok), tempfile.TemporaryDirectory(prefix="mt6639-bt-multi-") as tmp:
                root = Path(tmp)
                module, script, base, boot_id, _ = self.make_fixture(root, apex_subdir=subdir)
                stub = root / "stub"
                stub.mkdir()
                commands = {
                    "id": "#!/bin/sh\nif [ \"$1\" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n",
                    "getenforce": "#!/bin/sh\necho Enforcing\n",
                    "flock": "#!/usr/bin/python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n",
                    "getprop": f'''#!/bin/sh
case "$1" in
 ro.product.device) echo "{device}" ;;
 ro.bluetooth.leaudio_offload.supported) echo true ;;
 persist.bluetooth.leaudio_offload.disabled) echo false ;;
 *) cat "$PROPS/$1" 2>/dev/null || echo false ;;
esac
''',
                    "setprop": '#!/bin/sh\nprintf "%s\\n" "$2" > "$PROPS/$1"\n',
                    "sha256sum": f'''#!/bin/sh
case "$1" in
 */Bluetooth.apk) echo "{apk_sha}  fixture.apk" ;;
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
                env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", PROPS=str(props))
                res = subprocess.run(["sh", str(script), "enable"], env=env, text=True, capture_output=True, timeout=20)
                if ok:
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertEqual((base / boot_id / "state").read_text(), "active\n")
                else:
                    self.assertNotEqual(res.returncode, 0)
                    self.assertIn(err_text, res.stderr)

    def test_symlink_or_duplicate_apk_and_malformed_compat_table_rejected(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-edge-") as tmp:
            root = Path(tmp)
            module, script, _, _, apex_app = self.make_fixture(root)
            stub = root / "stub"
            stub.mkdir()
            for name, body in {
                "id": '#!/bin/sh\nif [ "$1" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n',
                "getenforce": '#!/bin/sh\necho Enforcing\n',
                "flock": '#!/usr/bin/python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n',
                "getprop": '''#!/bin/sh
case "$1" in
 ro.product.device) echo violin ;;
 *) echo false ;;
esac
''',
            }.items():
                p = stub / name
                p.write_text(body)
                p.chmod(0o755)
            env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")

            # Multiple Bluetooth.apk files -> unsupported_bluetooth_apex
            (apex_app / "Bluetooth@other").mkdir()
            (apex_app / "Bluetooth@other/Bluetooth.apk").write_bytes(b"dup\n")
            res_dup = subprocess.run(["sh", str(script), "enable"], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(res_dup.returncode, 0)
            self.assertIn("unsupported_bluetooth_apex", res_dup.stderr)
            shutil.rmtree(apex_app / "Bluetooth@other")

            # Symlinked Bluetooth.apk -> unsupported_bluetooth_apex
            apk = apex_app / "Bluetooth@CP2A.260605.016/Bluetooth.apk"
            apk.unlink()
            outside = root / "outside.apk"
            outside.write_bytes(b"sym\n")
            apk.symlink_to(outside)
            res_sym = subprocess.run(["sh", str(script), "enable"], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(res_sym.returncode, 0)
            self.assertIn("unsupported_bluetooth_apex", res_sym.stderr)
            apk.unlink()
            apk.write_bytes(b"fixture-apk\n")

            # Malformed compatibility.conf -> compatibility_table_invalid
            (module / "compatibility.conf").write_text("broken line\n")
            res_bad = subprocess.run(["sh", str(script), "enable"], env=env, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(res_bad.returncode, 0)
            self.assertIn("compatibility_table_invalid", res_bad.stderr)

    def test_reproducible_bt_module_package(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-build-") as tmp:
            z1 = Path(tmp) / "bt1.zip"
            z2 = Path(tmp) / "bt2.zip"
            for z in (z1, z2):
                res = subprocess.run(["sh", str(REPO / "tools/build_bt_module.sh"), str(z)],
                                     capture_output=True, text=True, timeout=20)
                self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(z1.read_bytes(), z2.read_bytes())
            with zipfile.ZipFile(z1) as arc:
                names = set(arc.namelist())
                self.assertIn("customize.sh", names)
                self.assertIn("compatibility.conf", names)
                self.assertIn("version=v0.2.0", arc.read("module.prop").decode())

    def test_state_root_rejects_symlink_and_file_without_touching_targets(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-bt-root-") as tmp:
            root = Path(tmp)
            _, script, base, _, _ = self.make_fixture(root)
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
