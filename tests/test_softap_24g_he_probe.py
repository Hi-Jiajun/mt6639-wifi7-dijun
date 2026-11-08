"""Guards for the 2.4GHz HE radio-capability probe."""
from pathlib import Path
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tools/softap_24g_he_probe.sh"


class SoftAp24gHeProbeTests(unittest.TestCase):
    def setUp(self):
        self.source = PROBE.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        self.assertTrue(PROBE.is_file())
        result = subprocess.run(["sh", "-n", str(PROBE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bounded_to_a_dedicated_task_dir(self):
        self.assertIn('case "$D" in /data/local/tmp/mt6639-probe-24g-*) ;; *) exit 64 ;; esac',
                      self.source)
        self.assertIn('[ "$(id -u)" = 0 ] && [ "$(id -Z)" = u:r:ksu:s0 ] || exit 77', self.source)
        self.assertIn('[ "$(getenforce)" = Enforcing ] || exit 77', self.source)

    def test_refuses_to_run_beside_other_aps(self):
        for marker in ("native_session_exists", "mtap0_exists", "framework_ap_present"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_pins_the_module_hostapd_binaries(self):
        self.assertIn("15023e3bfe6b08415ebcc92479c0cd4fb14adb57b9010266af73bcc1bdc7bc2b",
                      self.source)
        self.assertIn("2da7d467b7b2628c4da40fb5ccc101b17f8b279bd9c038c32e4ae6e5b2924a42",
                      self.source)

    def test_config_carries_an_explicit_cipher(self):
        # Regression guard: with wpa=2/SAE but no rsn_pairwise hostapd logs
        # "requires CCMP/GCMP to be enabled, disabling HE capabilities" and the
        # probe would silently measure an 11g BSS.
        self.assertIn("printf 'rsn_pairwise=CCMP\\n'", self.source)
        self.assertIn("ieee80211ax=1", self.source)
        self.assertIn("hw_mode=g", self.source)

    def test_cleanup_is_identity_checked_and_reports_residue(self):
        self.assertIn('readlink "/proc/$pid/exe"', self.source)
        self.assertIn('iw dev "$IF" del', self.source)
        self.assertIn("iface_after=", self.source)
        self.assertIn("hostapd_after=", self.source)

    def test_reports_a_machine_readable_verdict(self):
        self.assertIn("PROBE_RESULT=radio_reports_2ghz_he", self.source)
        self.assertIn("PROBE_RESULT=failed", self.source)
        self.assertIn("grep -Fxq 'ieee80211ax=1'", self.source)


if __name__ == "__main__":
    unittest.main()
