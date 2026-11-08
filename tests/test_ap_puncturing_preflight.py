"""Tests for read-only AP puncturing preflight parsing and command boundary."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/ap_puncturing_preflight.py"
sys.path.insert(0, str(TOOL.parent))
import ap_puncturing_preflight as preflight


SAMPLE = '''DISTRIB_ID='ImmortalWrt'
DISTRIB_RELEASE='20261021-test'
DISTRIB_REVISION='rev-test'
DISTRIB_DESCRIPTION='Test AP'
__KERNEL__
6.18.55
__PHY__
Band 4:
\tEHT Iftypes: AP
\t\t320MHz in 6GHz Supported
Supported extended features:
\t* [ RRM ]: RRM
__MODULE__
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  /lib/modules/6.18.55/mt7996e.ko
__MODINFO__
filename: /lib/modules/6.18.55/mt7996e.ko
depends: mt76,mac80211,cfg80211
vermagic: 6.18.55 SMP mod_unload aarch64
__PROC_VERSION__
Linux version 6.18.55 (builder@test) #1 SMP
__MODULE_LOADED__
mt7996e 123456 0 - Live 0x0000000000000000
__FIRMWARE__
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  /lib/firmware/mediatek/mt7996/mt7996_wm.bin
'''


class PreflightTests(unittest.TestCase):
    def test_parser_reports_no_punct(self):
        result = preflight.parse_output(SAMPLE)
        self.assertEqual(result["release"]["distribution"], "ImmortalWrt")
        self.assertEqual(result["kernel"], "6.18.55")
        self.assertTrue(result["eht_supported"])
        self.assertTrue(result["six_ghz_320_supported"])
        self.assertFalse(result["puncturing_feature"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["module_sha256"], "a" * 64)
        self.assertEqual(result["firmware_sha256"], "b" * 64)
        self.assertEqual(result["module_vermagic"], "6.18.55 SMP mod_unload aarch64")
        self.assertTrue(result["module_loaded"])
        self.assertIn("Linux version 6.18.55", result["kernel_build"])

    def test_missing_feature_list_is_tri_state_unknown(self):
        result = preflight.parse_output(
            "__KERNEL__\n6.18.55\n__PHY__\nBand 4:\n\tEHT Iftypes: AP\n"
            "\t\t320MHz in 6GHz Supported\n__MODULE__\n\n__MODINFO__\n"
            "filename: /lib/modules/6.18.55/mt7996e.ko\n"
            "vermagic: 6.18.55 SMP mod_unload aarch64\n__PROC_VERSION__\n"
            "Linux version 6.18.55\n__MODULE_LOADED__\n"
            "mt7996e 1 0 - Live 0x0\n__FIRMWARE__\n"
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["puncturing_feature"])
        self.assertIn("Supported extended features section missing",
                      result["blocked_reasons"])

    def test_punct_requires_complete_context(self):
        sample = SAMPLE.replace(
            "\t* [ RRM ]: RRM",
            "\t* [ RRM ]: RRM\n\t* [ PUNCT ]: puncturing",
        )
        result = preflight.parse_output(sample)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["puncturing_feature"])

    def test_fake_ssh_uses_read_only_script(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ssh = root / "ssh"
            calls = root / "calls"
            ssh.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CALLS\"\n"
                "cat \"$SAMPLE\"\n", encoding="utf-8")
            ssh.chmod(0o755)
            sample = root / "sample"
            sample.write_text(SAMPLE, encoding="utf-8")
            env = os.environ.copy()
            env.update({"PATH": str(root) + ":/usr/bin:/bin",
                        "CALLS": str(calls), "SAMPLE": str(sample)})
            done = subprocess.run([
                sys.executable, str(TOOL), "--host", "root@example.test",
            ], capture_output=True, text=True, timeout=10, env=env)
            call_text = calls.read_text()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("BatchMode=yes", call_text)
        self.assertIn("iw phy phy0 info", call_text)
        self.assertIn("sha256sum", call_text)
        self.assertIn("modinfo", call_text)
        for forbidden in ("uci set", "reboot", "hostapd", "wifi reload"):
            self.assertNotIn(forbidden, call_text)


if __name__ == "__main__":
    unittest.main()
