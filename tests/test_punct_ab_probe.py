#!/usr/bin/env python3
"""Parsers for tools/punct_ab_probe.py, checked against real `iw scan` shapes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import punct_ab_probe as probe  # noqa: E402

BSSID = "02:00:00:00:77:7c"

SCAN_PUNCTURED = f"""BSS {BSSID}(on wlan0) -- associated
\tfreq: 6295.0
\tsignal: -74.00 dBm
\tSSID: LabNet-6G
\tHE Operation:
\t\tChannel Width: 80+80 or 160 MHz
\t\tCenter Frequency Segment 0: 71
\tEHT Operation:
\t\tEHT Operation Parameters: (0x43)
\t\t\tGroup Addressed BU Indication Exponent: 0x0
\t\tBasic EHT-MCS And Nss Set: 0x11000000
\t\tEHT Operation Info: 0x044f5f00f0
\t\t\tChannel Width: 320 MHz
\t\t\tCenter Frequency Segment 0: 79
\t\t\tCenter Frequency Segment 1: 95
\t\t\tDisabled Subchannel Bitmap: 0x00f0
\tWMM:\t * Parameter version 1
BSS 02:00:00:00:73:1c(on wlan0)
\tfreq: 6135.0
\tsignal: -44.00 dBm
\tSSID: LabNet-6G
"""

SCAN_PLAIN = f"""BSS {BSSID}(on wlan0)
\tfreq: 6295.0
\tsignal: -74.00 dBm
\tSSID: LabNet-6G
\tEHT Operation:
\t\tEHT Operation Parameters: (0x41)
\t\t\tGroup Addressed BU Indication Exponent: 0x0
\t\tBasic EHT-MCS And Nss Set: 0x11000000
\t\tEHT Operation Info: 0x044f5f
\t\t\tChannel Width: 320 MHz
\t\t\tCenter Frequency Segment 0: 79
\t\t\tCenter Frequency Segment 1: 95
\tWMM:\t * Parameter version 1
"""

LINK = """Connected to 02:00:00:00:77:7c (on wlan0)
\tSSID: LabNet-6G
\tfreq: 6295.0
\tsignal: -75 dBm
\trx bitrate: 5764.8 MBit/s 320MHz
\ttx bitrate: 5764.8 MBit/s
"""


class PunctProbeTests(unittest.TestCase):
    def test_punctured_scan_reports_bitmap(self):
        got = probe.parse_eht_operation(SCAN_PUNCTURED, BSSID)
        self.assertTrue(got["found"])
        self.assertEqual(got["eht_params"], 0x43)
        self.assertTrue(got["bitmap_present"])
        self.assertEqual(got["disabled_subchannel_bitmap"], "00f0")
        # the 320MHz/79 lines must come from EHT Operation, not the HE block
        self.assertEqual(got["channel_width"], "320 MHz")
        self.assertEqual(got["ccfs0"], 79)

    def test_plain_scan_has_no_bitmap(self):
        got = probe.parse_eht_operation(SCAN_PLAIN, BSSID)
        self.assertEqual(got["eht_params"], 0x41)
        self.assertFalse(got["bitmap_present"])
        self.assertIsNone(got["disabled_subchannel_bitmap"])

    def test_unknown_bssid_is_not_found(self):
        self.assertEqual(probe.parse_eht_operation(SCAN_PLAIN, "00:00:00:00:00:01"), {"found": False})

    def test_link_parse(self):
        got = probe.parse_link(LINK)
        self.assertTrue(got["connected"])
        self.assertEqual(got["bssid"], BSSID)
        self.assertEqual(got["freq_mhz"], 6295.0)
        self.assertEqual(got["width_mhz"], 320)
        self.assertEqual(got["signal_dbm"], -75.0)

    def test_link_parse_disconnected(self):
        self.assertFalse(probe.parse_link("Not connected.\n")["connected"])


if __name__ == "__main__":
    unittest.main()
