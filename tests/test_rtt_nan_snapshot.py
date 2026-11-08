"""Static safety and parser checks for the read-only RTT/NAN snapshot."""
from pathlib import Path
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/rtt_nan_snapshot.sh"


class RttNanSnapshotTests(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_read_only_sources_are_used(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for expected in (
            "dumpsys wifi", "cmd wifi help", "service list",
            "pm list features", "iw phy",
            "cmd package has-feature android.hardware.wifi.aware 0",
            "cmd package has-feature android.hardware.wifi.rtt 0",
            "WIFI_FEATURE_AWARE", "WIFI_FEATURE_RTT",
            "mIs11mcSupported", "mIs11azSupported",
        ):
            self.assertIn(expected, source)
        for forbidden in (
            "aware-attach", "aware-publish", "aware-subscribe",
            "aware-request-network", "aware-teardown",
            "query-interface", "start-scan", "set-multi-internet-mode",
        ):
            self.assertNotIn(forbidden, source)

    def test_paths_and_summary_fields_are_strict(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('case "$D" in /data/local/tmp/mt6639-rtt-nan-*', source)
        self.assertIn('"$(stat -c %a "$D")" = 700', source)
        for field in (
            "aware_hal_feature", "rtt_hal_feature", "aware_binder",
            "rtt_binder", "aware_framework_feature", "rtt_framework_feature",
            "aware_single_feature_query", "rtt_single_feature_query",
            "connected_11mc_supported", "connected_11az_supported",
            "iw_phy_rtt_terms",
        ):
            self.assertIn(field, source)


if __name__ == "__main__":
    unittest.main()
