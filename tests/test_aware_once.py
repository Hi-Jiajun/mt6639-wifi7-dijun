"""Safety checks for the bounded Aware attach/teardown runner."""
from pathlib import Path
import subprocess
import unittest
import tempfile


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/aware_once.sh"


class AwareOnceTests(unittest.TestCase):
    def test_empty_or_duplicate_sta_fields_are_not_unchanged(self):
        source = SCRIPT.read_text()
        start = source.index("sta_matches() {")
        function = source[start:source.index("\n}\n", start) + 2]
        valid = 'Wifi is connected to "fixture"\nBSSID: 00:11:22:33:44:55\nFrequency: 6135\n'
        for baseline, active, expected in ((valid, valid, 0), ("", "", 1), (valid, "", 1), (valid + "Frequency: 6135\n", valid, 1)):
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "sta.before").write_text(baseline)
                (Path(td) / "sta.after").write_text(active)
                result = subprocess.run(["sh", "-c", 'D=$1; ' + function + '\nsta_matches "$D/sta.after"', "sh", td])
                self.assertEqual(result.returncode, expected)

    def test_failed_teardown_keeps_cleanup_armed(self):
        source = SCRIPT.read_text()
        body = source[source.index("attached=0"):]
        for failures, attempts, rc, summary in ((0, 1, 0, True), (1, 2, 72, False), (99, 2, 73, False)):
            self._check_teardown_case(failures, attempts, rc, summary)

    def _check_teardown_case(self, failures, attempts, rc, summary):
        source = SCRIPT.read_text()
        body = source[source.index("attached=0"):]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sta.before").write_text('Wifi is connected to "fixture"\nBSSID: 00:11:22:33:44:55\nFrequency: 6135\n')
            harness = '''D=$1; attempts=0
sleep() { :; }
iw() { :; }
timeout() {
 case "$2:$3:$4" in
  cmd:wifi:aware-teardown)
   attempts=$((attempts + 1)); echo "$attempts" > "$D/attempts"
   [ "$attempts" -gt 1 ]; return $? ;;
  cmd:wifi:aware-attach) return 0 ;;
  service:list:*) echo 'wifiaware: [android.net.wifi.aware.IWifiAwareManager]' ;;
  dumpsys:wifi:*)
   if [ "$attempts" = 0 ]; then echo 'Connection state: Aware=true'; else echo 'Connection state: Aware=false'; fi ;;
  cmd:wifi:status) cat "$D/sta.before" ;;
  *) return 99 ;;
 esac
}
'''
            result = subprocess.run(["sh", "-c", harness.replace("-gt 1", "-gt " + str(failures)) + body, "sh", td], capture_output=True, text=True)
            self.assertEqual(result.returncode, rc, result.stderr)
            self.assertEqual((root / "attempts").read_text().strip(), str(attempts))
            self.assertEqual((root / "summary").exists(), summary)
            self.assertEqual((root / "cleanup.rc").read_text().strip(), "1" if failures == 99 else "0")
    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_attach_and_teardown_are_used(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("cmd wifi aware-attach", source)
        self.assertIn("cmd wifi aware-teardown", source)
        self.assertIn("cmd package has-feature android.hardware.wifi.aware 0", source)
        self.assertIn("aware_feature_query_false", source)
        self.assertIn("aware_already_active_or_unknown", source)
        self.assertIn("aware_false_not_restored", source)
        self.assertIn("feature_visible=true", source)
        self.assertIn("sta_unchanged=true", source)
        for forbidden in (
            "aware-publish", "aware-subscribe", "aware-request-network",
            "aware-initiate-pairing-request", "aware-clean-paired-device",
            "start-scan", "set-multi-internet-mode",
        ):
            self.assertNotIn(forbidden, source)

    def test_authority_and_cleanup_are_strict(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('case "$D" in /data/local/tmp/mt6639-aware-*', source)
        self.assertIn('[ "$(id -Z)" = u:r:ksu:s0 ]', source)
        self.assertIn('[ "$(getenforce)" = Enforcing ]', source)
        self.assertIn("trap finish EXIT", source)
        self.assertIn("timeout 10 cmd wifi aware-teardown", source)


if __name__ == "__main__":
    unittest.main()
