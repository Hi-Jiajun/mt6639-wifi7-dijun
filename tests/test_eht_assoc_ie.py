"""Tests for the AP-observed EHT association IE decoder."""
from pathlib import Path
import base64
import json
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/decode_eht_assoc_ie.py"
sys.path.insert(0, str(TOOL.parent))
import decode_eht_assoc_ie as decoder


def known_assoc_ie() -> bytes:
    elements = [
        bytes([0, 4]) + b"Test",
        bytes([255, 21, 108]) + bytes.fromhex("2300ee6d49f619e7fa0100") +
        bytes.fromhex("222222222222222222"),
    ]
    return b"".join(elements)


class DecoderTests(unittest.TestCase):
    def test_three_band_mcs_maps(self):
        result = decoder.decode_assoc_ie(known_assoc_ie())
        self.assertEqual(result["eht_mac_capabilities_hex"], "2300")
        self.assertEqual(result["eht_phy_capabilities_hex"], "ee6d49f619e7fa0100")
        self.assertTrue(result["eht_phy_decoded"]["booleans"]["support_320mhz_6ghz"])
        self.assertFalse(result["eht_phy_decoded"]["booleans"]["partial_bw_ul_mu_mimo"])
        self.assertEqual(result["eht_phy_decoded"]["fields"]["beamformee_ss_320mhz"], 3)
        self.assertEqual(result["eht_phy_decoded"]["fields"]["max_nc_minus_one"], 1)
        self.assertEqual(result["mcs_nss_tail_hex"], "222222222222222222")
        self.assertEqual(result["mcs_nss_maps"]["= 320"]["12-13"], {"rx": 2, "tx": 2})
        self.assertNotEqual(result["ssid_token"], "Test")

    def test_duplicate_eht_is_rejected(self):
        eht = bytes([255, 21, 108]) + bytes.fromhex("2300ee6d49f619e7fa0100222222222222222222")
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_assoc_ie(eht + eht)

    def test_20mhz_only_keeps_four_independent_maps(self):
        eht = (
            bytes([255, 16, 108])
            + bytes.fromhex("2300ee6d49f619e7fa0100")
            + bytes.fromhex("11223344")
        )
        maps = decoder.decode_assoc_ie(eht)["mcs_nss_maps"]["= 20"]
        self.assertEqual(maps["0-7"], {"rx": 1, "tx": 1})
        self.assertEqual(maps["8-9"], {"rx": 2, "tx": 2})
        self.assertEqual(maps["10-11"], {"rx": 3, "tx": 3})
        self.assertEqual(maps["12-13"], {"rx": 4, "tx": 4})

    def test_truncated_ie_is_rejected(self):
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_assoc_ie(bytes([0, 9, 65]))

    def test_cli_redacts_ssid(self):
        document = {"assoc_ie": base64.b64encode(known_assoc_ie()).decode()}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ie.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(TOOL), "--json", str(path)],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('"ssid": "Test"', done.stdout)
        self.assertIn('"= 320"', done.stdout)


if __name__ == "__main__":
    unittest.main()
