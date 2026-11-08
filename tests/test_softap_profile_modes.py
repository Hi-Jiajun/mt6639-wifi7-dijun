"""Static checks for recoverable 2.4GHz/non-EHT SoftAP additions."""
from pathlib import Path
import hashlib
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]
JAVA = REPO / "native/framework/WifiSoftApProfile.java"
PROFILE = REPO / "tools/softap_profile.sh"
ONCE = REPO / "tools/softap_once.sh"
DEX = REPO / "outputs/softap-profile-build-20261026-1/classes.dex"


class SoftApProfileModeTests(unittest.TestCase):
    def test_shell_syntax(self):
        for script in (PROFILE, ONCE):
            result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_24ghz_is_he_only_and_does_not_touch_ap_eht_driver_gate(self):
        java = JAVA.read_text(encoding="utf-8")
        self.assertIn('args[0].equals("prepare-24g")', java)
        self.assertIn('!args[0].equals("prepare-24g")', java)
        self.assertIn('call(builder, "setBand", new Class<?>[]{int.class}, 1)', java)
        profile = PROFILE.read_text(encoding="utf-8")
        self.assertIn("enable-24g", profile)
        self.assertIn('"$key" != config_wifiSoftapIeee80211beSupported', profile)
        self.assertIn('if [ "$OP" = enable-5g ]; then', profile)
        self.assertIn('driver.intent', profile)

    def test_runner_checks_24ghz_11ax_observably(self):
        once = ONCE.read_text(encoding="utf-8")
        self.assertIn("run-24g|run-5g", once)
        self.assertIn("profile_mode=enable-24g", once)
        self.assertIn("expected_standard=6", once)
        self.assertIn("case \"$frequency\" in 2[0-5][0-9][0-9])", once)

    def test_24ghz_ax_setting_is_opt_in_and_reversible(self):
        """MIUI reads Settings.System at start time; forcing it stays opt-in."""
        profile = PROFILE.read_text(encoding="utf-8")
        gate = '[ "$OP" = enable-24g ] && [ "${MT6639_C02_FORCE_AX:-0}" = 1 ]'
        self.assertIn(gate, profile)
        block = profile[profile.index(gate):]
        # the forced value has to be recorded before restore can undo it
        self.assertIn(': > "$D/system.intent"', block[:400])
        self.assertIn('settings --user 0 put system hotspot_80211ax_support 1', block[:400])
        # null must be deleted, not written back as the string "null"
        self.assertIn('if [ "$before" = null ]; then', profile)
        self.assertIn('settings --user 0 delete system "$key"', profile)
        control = (REPO / "modules/mt6639-hotspot/bin/control.sh").read_text(encoding="utf-8")
        self.assertIn("MT6639_C02_FORCE_AX=${MT6639_C02_FORCE_AX:-0}", control)

    @unittest.skipUnless(DEX.is_file(), "rebuilt profile dex is a local build product")
    def test_rebuilt_profile_dex_matches_pinned_hash(self):
        self.assertTrue(DEX.is_file())
        digest = hashlib.sha256(DEX.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "7ff17f05008e77535dacac13c5c2f216c664bc26403d6cbfe7da7fb9b7c04017",
        )


if __name__ == "__main__":
    unittest.main()
