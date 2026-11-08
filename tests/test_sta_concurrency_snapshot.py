"""Static safety guards for the read-only dual-STA concurrency snapshot."""
from pathlib import Path
import subprocess
import unittest
import tempfile


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/sta_concurrency_snapshot.sh"


class ConcurrencySnapshotTests(unittest.TestCase):
    def test_gate_numeric_fields_fail_closed(self):
        source = SCRIPT.read_text(encoding="utf-8")
        parser = source[source.index("gate_marker=absent"):source.index("timeout 8 dumpsys")]
        for expires, now, expected in (
            ("100100", "100000", "armed"),
            ("100000", "100000", "expired"),
            ("220001", "100000", "invalid"),
            ("", "100000", "invalid"),
            ("100100", "", "invalid"),
            ("99999999999999999999999999", "100000", "invalid"),
            ("0100100", "100000", "invalid"),
        ):
            with self.subTest(expires=expires, now=now), tempfile.TemporaryDirectory() as td:
                gate = Path(td) / "gate"
                gate.write_text(f"schema=mt6639-dualsta-gate-v1\nboot=fixture\nexpires_ms={expires}\nlease=0123456789abcdef\n")
                fixture = parser.replace(
                    "gate_now=$(awk '{printf \"%.0f\", $1 * 1000}' /proc/uptime 2>/dev/null)",
                    "gate_now='" + now + "'",
                )
                result = subprocess.run(
                    ["sh", "-c", 'gate=$1; boot=fixture; ' + fixture + '\nprintf "%s" "$gate_state"', "sh", str(gate)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, expected)

    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_read_only_wifi_queries_are_used(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for expected in (
            "dumpsys wifi",
            "cmd wifi status",
            "cmd wifi list-interface-names",
            "cmd wifi query-interface 1000 android STA -new",
            "cmd wifi get-allowed-channel -b 8",
        ):
            self.assertIn(expected, source)
        for forbidden in (
            "set-multi-internet-mode",
            "setup-client-interface",
            "create-group-with-config",
            "set-sta-concurrency",
            "start-scan",
        ):
            self.assertNotIn(forbidden, source)

    def test_summary_exposes_gate_fields_without_task_secrets(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for field in (
            "multi_internet_feature",
            "multi_internet_mode",
            "multi_internet_state",
            "client_mode_manager_instances",
            "secondary_client_mode_managers",
            "same_band_allowed",
            "same_bssid_allowed",
            "concurrency_gate_installed",
            "concurrency_gate_version",
            "concurrency_gate_pinned",
            "concurrency_gate_marker",
            "concurrency_gate_state",
            "concurrency_gate_reason",
            "sta_interface_query",
            "p2p_6g_6135",
            "native_mode_observed=unknown",
        ):
            self.assertIn(field, source)
        self.assertIn('case "$D" in /data/local/tmp/mt6639-sta-concurrency-*', source)
        self.assertIn('"$(stat -c %a "$D")" = 700', source)


if __name__ == "__main__":
    unittest.main()
