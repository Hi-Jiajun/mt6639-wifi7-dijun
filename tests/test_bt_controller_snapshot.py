"""Static safety checks for read-only Bluetooth controller snapshots."""
from pathlib import Path
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/bt_controller_snapshot.sh"


class BluetoothControllerSnapshotTests(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_read_only_commands_are_used(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for expected in ("dumpsys bluetooth_manager", "getprop", "settings get global bluetooth_on"):
            self.assertIn(expected, source)
        for forbidden in (
            "svc bluetooth",
            "cmd bluetooth_manager enable",
            "cmd bluetooth_manager disable",
            "setprop",
            "settings put",
        ):
            self.assertNotIn(forbidden, source)

    def test_64_bit_feature_decode_splits_32_bit_halves(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("high_hex=${hex%????????}", source)
        self.assertIn("low_hex=${hex#????????}", source)
        self.assertIn('if [ "$shift_value" -lt 32 ]', source)
        for field in (
            "cis_central",
            "cis_peripheral",
            "iso_broadcaster",
            "sync_receiver",
            "pawr_advertiser",
            "pawr_scanner",
            "channel_sounding",
        ):
            self.assertIn(field, source)


if __name__ == "__main__":
    unittest.main()
