"""Safety and packaging checks for the experimental Wi-Fi Aware service bridge."""
from pathlib import Path
import hashlib
import re
import subprocess
import unittest
import zipfile


import pubenv

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "modules/mt6639-aware-lsposed"
APK = REPO / "release/artifacts/mt6639-aware-service-0.1.3.apk"


class AwareServiceModuleTests(unittest.TestCase):
    @unittest.skipUnless(APK.is_file(), "Aware service candidate APK is a Release asset")
    def test_fingerprint_matches_device_preflight(self):
        source = (MODULE / "src/com/hiliang/mt6639aware/AwareServiceHook.java").read_text()
        preflight = (REPO / "tools/release_device_preflight.sh").read_text()
        expected = re.search(r"EXPECTED_FP='([^']+)'", preflight).group(1)
        locked = re.search(r'EXPECTED_FINGERPRINT\s*=\s*"([^"]+)"', source).group(1)
        self.assertEqual(locked, expected)
        with zipfile.ZipFile(APK) as archive:
            dex = archive.read("classes.dex")
            self.assertIn(expected.encode(), dex)
            self.assertNotIn(expected.replace("XODCNXM", "XODCNM").encode(), dex)

    def test_source_is_exception_isolated_and_passive(self):
        source = (MODULE / "src/com/hiliang/mt6639aware/AwareServiceHook.java").read_text()
        for expected in (
            '"android".equals(lpparam.packageName)',
            "sealStartedServices",
            '"wifiaware"',
            '"com.android.server.wifi.aware.WifiAwareService"',
            '"com.android.server.pm.PackageManagerService"',
            '"hasSystemFeature", String.class, int.class',
            '"android.hardware.wifi.aware"',
            "phase500Ok",
            "phase1000Ok",
            '"startService"',
            '"onBootPhase", int.class',
            "failure isolated",
            "param.setResult(null)",
            "catch (Throwable error)",
        ):
            self.assertIn(expected, source)
        for forbidden in (
            "aware-attach", "aware-publish", "aware-subscribe",
            "requestNetwork", "setWifiEnabled", "startScan",
        ):
            self.assertNotIn(forbidden, source)

    @unittest.skipUnless(APK.is_file() and pubenv.AAPT2 and pubenv.DROIDASC,
                         "candidate APK, aapt2 or droidasc unavailable")
    def test_apk_scope_entry_and_hash(self):
        manifest = subprocess.run(
            ["aapt2", "dump", "xmltree", str(APK), "--file", "AndroidManifest.xml"],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("com.hiliang.mt6639aware", manifest)
        self.assertIn("xposedmodule", manifest)
        self.assertNotIn("uses-permission", manifest)
        entry = subprocess.run(
            ["unzip", "-p", str(APK), "assets/xposed_init"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(entry, "com.hiliang.mt6639aware.AwareServiceHook")
        classes = subprocess.run(
            ["droidasc", "listclass", str(APK)],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("Lcom/hiliang/mt6639aware/AwareServiceHook;", classes)
        self.assertNotIn("Lde/robv/android/xposed/", classes)
        self.assertEqual(
            hashlib.sha256(APK.read_bytes()).hexdigest(),
            "42a75c62aa4dc00f310611eac74ccc7addf2e6ece4782287cf3c66fe410e86e7",
        )


if __name__ == "__main__":
    unittest.main()
