#!/usr/bin/env python3
"""Parser and read-only boundary tests for wifi7_observe."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/wifi7_observe.py"
sys.path.insert(0, str(TOOL.parent))
import wifi7_observe as obs


class ParserTests(unittest.TestCase):
    def test_link_and_rate(self):
        link = """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: Secret
\tfreq: 6135
\tsignal: -42 dBm
\trx bitrate: 5764.8 MBit/s EHT-MCS 12 EHT-NSS 2 320MHz
\ttx bitrate: 2882.4 MBit/s EHT-MCS 11 EHT-NSS 2 320MHz
"""
        parsed = obs.parse_iw_link(link)
        self.assertTrue(parsed["connected"])
        self.assertEqual(parsed["frequency_mhz"], 6135)
        self.assertEqual(parsed["rx"]["mcs"], 12)
        self.assertEqual(parsed["tx"]["nss"], 2)
        self.assertEqual(parsed["rx"]["width_mhz"], 320)
        self.assertNotIn("Secret", json.dumps(parsed))

    def test_puncturing_feature_bit(self):
        base = ("Band 4:\n\t\t* 5955.0 MHz [1] (12.0 dBm)\n"
                "Supported extended features:\n\t\t* [ FILS_SK_OFFLOAD ]: x 5.5Mbps\n")
        no = obs.parse_iw_phy(base)
        self.assertFalse(no["puncturing_feature"])
        self.assertTrue(no["has_6ghz_frequency"])
        yes = obs.parse_iw_phy(
            "Supported extended features:\n\t\t[199,35,0,156,2,149,113,116,0,1]\n")
        self.assertTrue(yes["puncturing_feature"])

    def test_station_and_mu_counters(self):
        stations = obs.parse_station_dump("""Station aa:bb:cc:dd:ee:ff (on wlan2)
\trx bytes:\t100
\ttx bytes:\t200
\trx duration:\t30
\ttx duration:\t40
\tsignal:\t-41
\trx bitrate:\t5764.8 MBit/s EHT-MCS 12 EHT-NSS 2 320MHz
\ttx bitrate:\t2882.4 MBit/s EHT-MCS 11 EHT-NSS 2 320MHz
""")
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0]["fields"]["rx_bytes"], 100)
        self.assertEqual(stations[0]["rx_rate"]["mcs"], 12)
        counters = obs.parse_tx_stats(
            "Tx multi user MPDU counts: 17\nTx multi user successful MPDU counts: 15\n")
        self.assertEqual(counters["tx_multi_user_mpdu_counts"], 17)
        self.assertEqual(counters["tx_multi_user_successful_mpdu_counts"], 15)

    def test_eht_maps_keep_band_iftype_and_zero_320_nss(self):
        phy = """Band 4:
\t\tHE Iftypes: managed
\t\tEHT Iftypes: managed
\t\t\tEHT PHY Capabilities (0x6e0000000000000000):
\t\t\t\t320MHz in 6GHz Supported
\t\t\t\tSU Beamformer
\t\t\tEHT-MCS Map (BW <= 80) (0x222222):
\t\t\t\tRx Max NSS for MCS 0-9: 2
\t\t\t\tTx Max NSS for MCS 0-9: 2
\t\t\t\tRx Max NSS for MCS 12-13: 2
\t\t\t\tTx Max NSS for MCS 12-13: 2
\t\t\tEHT-MCS Map (BW = 320) (0x000000):
\t\t\t\tRx Max NSS for MCS 0-9: 0
\t\t\t\tTx Max NSS for MCS 0-9: 0
\t\t\t\tRx Max NSS for MCS 12-13: 0
\t\t\t\tTx Max NSS for MCS 12-13: 0
"""
        parsed = obs.parse_iw_phy(phy)
        managed = parsed["eht_by_band"][4]["managed"]
        self.assertEqual(managed["phy_capabilities_hex"], "0x6e0000000000000000")
        self.assertIn("320MHz in 6GHz Supported", managed["phy_capabilities"])
        self.assertEqual(managed["mcs_maps"]["<= 80"]["rx_tx_nss"]["12-13"],
                         {"rx": 2, "tx": 2})
        self.assertEqual(managed["mcs_maps"]["= 320"]["raw"], "0x000000")
        self.assertEqual(managed["mcs_maps"]["= 320"]["rx_tx_nss"]["12-13"],
                         {"rx": 0, "tx": 0})


FAKE_ADB = """#!/usr/bin/env python3
import sys
cmd = sys.argv[-1]
text = {
 'boot_id': 'boot-1\\n',
 'fingerprint': 'Xiaomi/dijun/test\\n',
 'getenforce': 'Enforcing\\n',
 'iw dev wlan0 link': 'Connected to aa:bb:cc:dd:ee:ff (on wlan0)\\n\\tSSID: Secret\\n\\tfreq: 6135\\n',
 'iw phy phy0 info': 'Supported extended features:\\n\\t\\t[1,2,3,4,5,6,7,0]\\n',
 'show-mld 255': 'wlan0 driver:\\n\\n'
                 'BSS[0][RF_BAND=3][OMAC=5e:6d:**:**:**:8c][LINK_ID=0]:\\n'
                 '\\tACTIVE/HW_BAND/OMAC_IDX: 1/1/0\\n'
                 '\\tCHANNEL/WIDTH/S1/S2: 37/4/31/0\\n',
 'iw --version': 'iw version 6.17\\n',
 'sha256sum /system/bin/iw': '33dc99bc74b48af217db7f47d955cfc1005f370f4b227425b627909c467ccab6  /system/bin/iw\\n',
 'cmd wifi status': 'Wifi is enabled\\nWifi is connected to \"Secret\"\\n'
                   'WifiInfo: SSID: \"Secret\", BSSID: aa:bb:cc:dd:ee:ff, '
                   'MAC: 02:00:00:00:00:01, Supplicant state: COMPLETED, '
                   'Wi-Fi standard: 11be, Frequency: 6135MHz\\n',
}.get([k for k in ['boot_id','fingerprint','getenforce','iw dev wlan0 link','iw phy phy0 info','show-mld 255','iw --version','sha256sum /system/bin/iw','cmd wifi status'] if k in cmd][0], '')
sys.stdout.write(text)
"""


class CliTests(unittest.TestCase):
    def test_cli_only_queries_and_redacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(FAKE_ADB)
            adb.chmod(0o755)
            out = root / "out"
            done = subprocess.run([
                sys.executable, str(TOOL), "--adb", str(adb),
                "--serial", "SERIAL", "--out", str(out),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
            summary = json.loads((out / "summary.json").read_text())
            self.assertTrue(summary["phone"]["link"]["connected"])
            self.assertEqual(summary["phone"]["iw_version"], "iw version 6.17")
            self.assertEqual(summary["phone"]["iw_binary_sha256"],
                             "33dc99bc74b48af217db7f47d955cfc1005f370f4b227425b627909c467ccab6")
            record = summary["phone"]["show_mld"]["records"][0]
            self.assertEqual(record["width_enum"], "CW_320_1MHZ")
            self.assertTrue(record["s1_consistent_with_width"])
            self.assertEqual(summary["status"], "complete")
            self.assertIsNotNone(summary["phone"]["wifi"]["mac"])
            self.assertNotIn("Secret", (out / "summary.json").read_text())
            self.assertEqual((out / "summary.json").stat().st_mode & 0o777, 0o600)

    def test_cli_fails_closed_when_a_query_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(
                FAKE_ADB.replace(
                    "sys.stdout.write(text)",
                    "if 'iw phy phy0 info' in cmd: sys.exit(7)\n"
                    "sys.stdout.write(text)",
                )
            )
            adb.chmod(0o755)
            out = root / "out"
            done = subprocess.run([
                sys.executable, str(TOOL), "--adb", str(adb),
                "--serial", "SERIAL", "--out", str(out),
            ], capture_output=True, text=True, timeout=30)
            summary = json.loads((out / "summary.json").read_text())
        self.assertEqual(done.returncode, 2)
        self.assertEqual(summary["status"], "incomplete")
        self.assertIn("iw_phy", summary["incomplete_commands"])


if __name__ == "__main__":
    unittest.main()
