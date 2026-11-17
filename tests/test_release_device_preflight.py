"""Static safety guards for read-only release device preflight."""
from pathlib import Path
import subprocess
import unittest
import tempfile


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/release_device_preflight.sh"


class ReleaseDevicePreflightTests(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_is_read_only(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "setprop", "settings put", "ksud module install",
            "ksud module enable", "ksud module disable", "soft-reboot",
            "am force-stop", "ip link", "iw dev add",
        ):
            self.assertNotIn(forbidden, source)
        for expected in (
            "cmd wifi status", "dumpsys tethering --short",
            "zygisksu/bin/zygiskd status",
            "zygisk_lsposed/lspctl status", "ksud debug info",
            "ksud feature get 0", "su -c id",
            "bluetooth.profile.bap.unicast.client.enabled",
            "com.hiliang.xringroot",
            "c739b07e747b1be2a4f0af9c19eb06674fd895665f9b45a36bc7c5ce6cee9e49",
            "me.weishu.kernelsu",
            "EXPECTED_ROOT_CONTEXT='u:r:ksu:s0'",
            "/sys/fs/selinux/enforce",
            "result PASS kernelsu_su_context uid_0_ksu",
            "EXPECTED_CONCURRENCY_VERSION='0.1.0'",
            "result PASS concurrency_gate_marker absent",
        ):
            self.assertIn(expected, source)

    def test_release_versions_and_path_are_pinned(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mt6639-6g:v0.3.0", source)
        self.assertIn("mt6639-hotspot:v0.2.22", source)
        self.assertIn("mt6639-bt:v0.1.2", source)
        self.assertIn("EXPECTED_KSU_MANAGER_VERSION='v3.3.1'", source)
        self.assertIn("result FAIL kernelsu_su_compat", source)
        self.assertIn("result FAIL concurrency_gate_marker present", source)
        self.assertIn('result PASS "module_${id}_enabled"', source)
        self.assertIn("expected_0.2.7", source)
        self.assertIn("/data/local/tmp/mt6639-release-preflight-*", source)
        self.assertIn('"$(stat -c %a "$D")" = 700', source)

    def test_private_su_fallback_does_not_bypass_domain_requirement(self):
        source = SCRIPT.read_text()
        block = source[source.index("su_rc=127"):source.index("EXPECTED_XRINGROOT_SHA=")]
        for context, expected in (("kernel", "FAIL"), ("ksu", "PASS")):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as td:
                private_su = Path(td) / "su"
                private_su.touch()
                private_su.chmod(0o700)
                fixture = block.replace("/data/adb/ksu/bin/su", str(private_su))
                harness = '''D=$1
command() { return 1; }
timeout() { printf 'uid=0(root) context=u:r:DOMAIN:s0\\n'; }
result() { printf '%s' "$1"; }
'''.replace("DOMAIN", context)
                result = subprocess.run(["sh", "-c", harness + fixture, "sh", td], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual((Path(td) / "ksu.su-path").read_text().strip(), str(private_su))


if __name__ == "__main__":
    unittest.main()
