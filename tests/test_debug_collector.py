"""Tests for read-only MT6639 debug collector ZIP and group test pack."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import pubenv


REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "tools/build_debug_collector.py"


class DebugCollectorTests(unittest.TestCase):
    def test_deterministic_collector_zip_permissions_and_syntax(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-collector-zip-test-") as td:
            root = Path(td)
            z1 = root / "first.zip"
            z2 = root / "second.zip"
            r1 = subprocess.run(["python3", str(BUILDER), "--zip-only", str(z1)], capture_output=True, text=True)
            r2 = subprocess.run(["python3", str(BUILDER), "--zip-only", str(z2)], capture_output=True, text=True)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)

            summary = json.loads(r1.stdout)
            self.assertEqual(summary["schema"], "mt6639-debug-collector-zip-v1")
            expected_sha = "94a0881423d0764d15d28ee4a3fe5eb52b3c5ff186e3c1ec6603b2364689609e"
            self.assertEqual(summary["sha256"], expected_sha)
            self.assertEqual(hashlib.sha256(z1.read_bytes()).hexdigest(), expected_sha)
            self.assertEqual(hashlib.sha256(z2.read_bytes()).hexdigest(), expected_sha)

            ext = root / "extracted"
            with zipfile.ZipFile(z1) as zf:
                zf.extractall(ext)
                for member in ("action.sh", "bin/collect.sh", "customize.sh"):
                    info = zf.getinfo(member)
                    self.assertEqual((info.external_attr >> 16) & 0o777, 0o755)
            chk_in = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=ext, capture_output=True, text=True)
            self.assertEqual(chk_in.returncode, 0, chk_in.stdout + chk_in.stderr)

            # KernelSU sources customize.sh: verify no bare exit statements and clean sh syntax
            cust_src = (ext / "customize.sh").read_text(encoding="utf-8")
            self.assertNotIn("\nexit", "\n" + cust_src)
            for script in ("customize.sh", "action.sh", "bin/collect.sh"):
                syn = subprocess.run(["sh", "-n", str(ext / script)], capture_output=True, text=True)
                self.assertEqual(syn.returncode, 0, f"{script}: {syn.stderr}")

    @unittest.skipUnless(
        pubenv.assets_present(
            "mt6639-6g-v0.4.1.zip",
            "mt6639-bt-v0.2.0.zip",
            "mt6639-hotspot-v0.3.1.zip",
            "mt6639-ui-lsposed-0.3.0.apk",
        ),
        pubenv.SKIP_RELEASE_ASSETS,
    )
    def test_group_pack_checksums_and_guide(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-collector-pack-test-") as td:
            pack = Path(td) / "pack"
            res = subprocess.run(["python3", str(BUILDER), "--pack-dir", str(pack)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

            summary = json.loads(res.stdout)
            self.assertEqual(summary["schema"], "mt6639-group-test-pack-v1")
            self.assertTrue(summary["guide_included"])
            self.assertEqual(len(summary["packages"]), 5)
            self.assertTrue((pack / "安装与测试指南.md").is_file())
            self.assertTrue((pack / "mt6639-debug-collector-v0.1.1.zip").is_file())

            chk = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=pack, capture_output=True, text=True)
            self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)

    def test_collect_sh_exports_hashes_and_strictly_redacts_secrets(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-collect-run-") as td:
            root = Path(td)
            collector_zip = root / "collector.zip"
            ext = root / "ext"
            out_dir = root / "out"
            out_dir.mkdir()
            subprocess.run(["python3", str(BUILDER), "--zip-only", str(collector_zip)], check=True, capture_output=True)
            with zipfile.ZipFile(collector_zip) as zf:
                zf.extractall(ext)

            adb_base = root / "adb"
            misc_base = root / "misc"
            tmp_base = root / "tmp"
            (adb_base / "modules/mt6639-6g/bin").mkdir(parents=True)
            (adb_base / "modules/mt6639-bt").mkdir(parents=True)
            (adb_base / "modules/mt6639-hotspot").mkdir(parents=True)
            (adb_base / "mt6639-6g/transactions/t-1").mkdir(parents=True)
            (adb_base / "mt6639-hotspot/private").mkdir(parents=True)
            (misc_base / "mt6639-hotspot").mkdir(parents=True)
            (tmp_base / "mt6639-native-ap-1").mkdir(parents=True)

            (adb_base / "modules/mt6639-6g/module.prop").write_text("version=v0.4.1\nversionCode=24\ndescription=[已停止] Connected to \"SecretCardSSID\" aa:bb:cc:dd:ee:ff\n")
            (adb_base / "modules/mt6639-6g/bin/verify-module.sh").write_text("#!/bin/sh\nexit 0\n")
            (adb_base / "modules/mt6639-6g/bin/verify-module.sh").chmod(0o755)
            (adb_base / "modules/mt6639-6g/SHA256SUMS").write_text(
                hashlib.sha256((adb_base / "modules/mt6639-6g/module.prop").read_bytes()).hexdigest() + "  module.prop\n"
            )
            (adb_base / "mt6639-6g/state").write_text("active\n")
            (adb_base / "mt6639-6g/country.conf").write_text("TARGET_COUNTRY=JP\n")
            (adb_base / "mt6639-6g/apply.log").write_text("2026-11-22 ssid=LeakRouter1 mac=aa:bb:cc:dd:ee:ff ip=198.51.100.88\n")
            (adb_base / "mt6639-hotspot/private/ap.key").write_text("PrivateHotspotKey123\n")
            (adb_base / "mt6639-hotspot/private/ap.key").chmod(0o600)
            (misc_base / "mt6639-hotspot/status").write_text("schema=mt6639-hotspot-status-v4\nstate=idle\n")
            (tmp_base / "mt6639-native-ap-1/session.log").write_text("sae_password=SuperSecretPass99\n10.0.0.42 ready\n")

            cfg_file = root / "wifi.cfg"
            ko_dir = root / "vendor_mod"
            bt_dir = root / "bt_apex/com.android.bt"
            tether_jar = root / "framework-tethering.jar"
            ko_dir.mkdir()
            bt_dir.mkdir(parents=True)
            cfg_file.write_bytes(b"cfg-sample")
            (ko_dir / "wlan_drv_gen4m_eap_6639.ko").write_bytes(b"ko-sample")
            (bt_dir / "Bluetooth.apk").write_bytes(b"bt-sample")
            tether_jar.write_bytes(b"jar-sample")
            mountinfo = root / "mountinfo"
            mountinfo.write_text("1 1 0:1 / / ro\n")
            sysfs_enf = root / "enforce"
            sysfs_enf.write_text("1\n")

            stub = root / "stub"
            stub.mkdir()
            for name, body in {
                "getprop": '#!/bin/sh\ncase "$1" in ro.product.device) echo violin;; ro.product.model) echo Pad7S;; ro.mi.os.version.name) echo OS4.0.6.0.XOTCNXM;; ro.build.version.sdk) echo 37;; *) echo "";; esac\n',
                "getenforce": "#!/bin/sh\necho Enforcing\n",
                "id": '#!/bin/sh\nif [ "$1" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n',
                "cmd": '#!/bin/sh\nif [ "$1" = wifi ] && [ "$2" = status ]; then echo "Wifi is enabled"; echo "Wifi is connected to \\"MySecretHomeSSID\\""; elif [ "$1" = wifi ] && [ "$2" = get-country-code ]; then echo "Wifi Country Code = CN"; fi\n',
                "iw": '#!/bin/sh\nprintf "Band 4:\\n\\t* 5955.0 MHz [1]\\n\\t* 6135.0 MHz [37] (disabled)\\n"\n',
                "dumpsys": "#!/bin/sh\necho \"    Upstream wanted: false\"\n",
                "dmesg": "#!/bin/sh\necho \"avc: denied on 198.51.100.88 aa:bb:cc:dd:ee:ff mt6639\"\n",
                "settings": "#!/bin/sh\necho 1\n",
            }.items():
                p = stub / name
                p.write_text(body)
                p.chmod(0o755)

            env = {
                "PATH": f"{stub}:/usr/bin:/bin",
                "MT6639_DEBUG_OUT_DIR": str(out_dir),
                "MT6639_ADB_BASE": str(adb_base),
                "MT6639_MISC_BASE": str(misc_base),
                "MT6639_TMP_BASE": str(tmp_base),
                "MT6639_ODM_CFG": str(cfg_file),
                "MT6639_VENDOR_MOD_DIR": str(ko_dir),
                "MT6639_DLKM_MOD_DIR": str(ko_dir),
                "MT6639_BT_APEX_DIR": str(root / "bt_apex"),
                "MT6639_TETHER_JAR": str(tether_jar),
                "MT6639_MOUNTINFO": str(mountinfo),
                "MT6639_SYSFS_ENFORCE": str(sysfs_enf),
            }
            res = subprocess.run(["sh", str(ext / "bin/collect.sh"), "action"], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

            reports = list(out_dir.glob("mt6639-debug-violin-*.txt"))
            self.assertEqual(len(reports), 1, list(out_dir.iterdir()))
            report = reports[0].read_text(encoding="utf-8")

            self.assertIn(f"WIFI_CFG_SHA={hashlib.sha256(b'cfg-sample').hexdigest()}", report)
            self.assertIn(f"WLAN_KO_SHA={hashlib.sha256(b'ko-sample').hexdigest()}", report)
            self.assertIn(f"BT_APK_SHA={hashlib.sha256(b'bt-sample').hexdigest()}", report)
            self.assertIn(f"TETHER_JAR_SHA={hashlib.sha256(b'jar-sample').hexdigest()}", report)
            self.assertIn("BAND4_TOTAL=2", report)
            self.assertIn("BAND4_ENABLED=1", report)
            self.assertIn("WIFI_CONNECTED=1", report)
            self.assertIn("TARGET_COUNTRY_CONF=TARGET_COUNTRY=JP", report)
            self.assertIn("MOD_mt6639-6g_LIVE=v0.4.1(24)", report)
            self.assertIn("SHA=ok", report)
            self.assertIn("mt6639-6g/apply.log", report)
            self.assertIn("ap_key_valid_0600=1", report)
            self.assertIn("CFG_MOUNT_COUNT=0\n\n", report)

            for secret in (
                "MySecretHomeSSID",
                "SecretCardSSID",
                "LeakRouter1",
                "aa:bb:cc:dd:ee:ff",
                "198.51.100.88",
                "10.0.0.42",
                "SuperSecretPass99",
                "PrivateHotspotKey123",
            ):
                self.assertNotIn(secret, report, f"secret leaked: {secret}")


if __name__ == "__main__":
    unittest.main()
