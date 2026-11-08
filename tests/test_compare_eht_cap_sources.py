"""Tests for AP association IE vs phone nl80211 EHT map comparison."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/compare_eht_cap_sources.py"
sys.path.insert(0, str(TOOL.parent))
import compare_eht_cap_sources as compare_tool


def ap_doc():
    return {
        "schema": "mt6639-eht-assoc-ie-v1",
        "ssid_token": "sha256:test",
        "eht_phy_capabilities_hex": "ee6d49f619e7fa0100",
        "mcs_nss_maps": {
            "<= 80": {"0-9": {"rx": 2, "tx": 2}, "10-11": {"rx": 2, "tx": 2},
                      "12-13": {"rx": 2, "tx": 2}},
            "= 320": {"0-9": {"rx": 2, "tx": 2}, "10-11": {"rx": 2, "tx": 2},
                      "12-13": {"rx": 2, "tx": 2}},
        },
    }


def ap_envelope():
    return {
        "schema": "mt6639-ap-station-ie-recheck-v1",
        "collected_at": "2026-10-26T03:00:00+00:00",
        "host_boot_id": "ap-boot-1",
        "station_token": "sha256:station",
        "ssid_token": "sha256:test",
        "bssid_token": "sha256:bssid",
        "frequency_mhz": 6135,
        "association": ap_doc(),
    }


def phone_doc(phy_320=0):
    return {
        "schema": "mt6639-wifi7-observe-v1",
        "status": "complete",
        "collected_at": "2026-10-26T03:00:01+00:00",
        "phone": {
            "boot_id": "phone-boot-1",
            "link": {
                "bssid": "sha256:bssid",
                "ssid": "sha256:test",
                "frequency_mhz": 6135,
            },
            "wifi": {"standard": "11be", "frequency_mhz": 6135,
                     "connected_ssid": "sha256:test", "mac": "sha256:station",
                     "supplicant_completed": True, "link_speed_mbps": 5764.0},
            "phy": {"eht_by_band": {"4": {"managed": {"mcs_maps": {
                "<= 80": {"rx_tx_nss": {
                    "0-9": {"rx": 2, "tx": 2}, "10-11": {"rx": 2, "tx": 2},
                    "12-13": {"rx": 2, "tx": 2}}},
                "= 320": {"rx_tx_nss": {
                    "0-9": {"rx": phy_320, "tx": phy_320},
                    "10-11": {"rx": phy_320, "tx": phy_320},
                    "12-13": {"rx": phy_320, "tx": phy_320}}},
            }, "phy_capabilities_hex": "0x6e0000000000000000"}}}},
        },
    }


class CompareTests(unittest.TestCase):
    def test_mismatch_is_structured(self):
        result = compare_tool.compare(ap_envelope(), phone_doc(0), 4, "managed")
        self.assertEqual(result["result"], "source_mismatch")
        self.assertEqual(result["groups_compared"], 6)
        self.assertEqual(len([d for d in result["differences"] if d["width"] == "= 320"]), 3)
        self.assertIn("beamformee_ss_320mhz",
                      result["phy_differences"]["scalar_mismatch"])
        self.assertIn("max_nc_minus_one", result["phy_differences"]["scalar_mismatch"])
        self.assertEqual(result["runtime"]["frequency_mhz"], 6135)

    def test_consistent_maps(self):
        result = compare_tool.compare(ap_envelope(), phone_doc(2), 4, "managed")
        self.assertEqual(result["result"], "source_mismatch")
        self.assertEqual(result["differences"], [])
        self.assertTrue(result["phy_differences"]["association_only_set"])

    def test_recheck_envelope_is_accepted(self):
        result = compare_tool.compare(ap_envelope(), phone_doc(0), 4, "managed")
        self.assertEqual(result["result"], "source_mismatch")
        self.assertEqual(result["association_ssid_token"], "sha256:test")
        self.assertEqual(len(result["differences"]), 3)

    def test_missing_linkage_is_uncomparable(self):
        envelope = ap_envelope()
        envelope.pop("station_token")
        result = compare_tool.compare(envelope, phone_doc(0), 4, "managed")
        self.assertEqual(result["result"], "uncomparable")
        self.assertIn("station identity missing", result["uncomparable_reasons"])

    def test_station_and_window_mismatch_is_uncomparable(self):
        envelope = ap_envelope()
        phone = phone_doc(0)
        phone["phone"]["wifi"]["mac"] = "sha256:other-station"
        phone["collected_at"] = "2026-10-26T03:10:00+00:00"
        result = compare_tool.compare(envelope, phone, 4, "managed")
        self.assertEqual(result["result"], "uncomparable")
        self.assertIn("station identity mismatch", result["uncomparable_reasons"])
        self.assertIn("collection window exceeds 300s",
                      result["uncomparable_reasons"])

    def test_derived_maps_must_match_raw_tail(self):
        envelope = ap_envelope()
        envelope["association"]["mcs_nss_tail_hex"] = "222222222222222222"
        envelope["association"]["mcs_nss_maps"]["= 320"]["12-13"] = {"rx": 1, "tx": 1}
        with self.assertRaises(compare_tool.CompareError):
            compare_tool.compare(envelope, phone_doc(0), 4, "managed")

    def test_cli_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory() as td:
            ap = Path(td) / "ap.json"
            phone = Path(td) / "phone.json"
            ap.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            phone.write_text(json.dumps(phone_doc()), encoding="utf-8")
            done = subprocess.run([
                sys.executable, str(TOOL), "--ap-ie-json", str(ap),
                "--phone-summary-json", str(phone), "--band", "4",
            ], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("schema unsupported", done.stderr)


if __name__ == "__main__":
    unittest.main()
