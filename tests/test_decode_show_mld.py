"""Tests for the read-only MediaTek show-mld width decoder."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/decode_show_mld.py"

FIXTURE = """\
wlan0     driver:
MldLinkMax:2
BSS:1,0,0,0

MldBssInfo[0]:
BSS_BITMAP/OWN_MLD_ADDR:0x01/5e:6d:**:**:**:8c
{
\tBSS[0][RF_BAND=3][OMAC=5e:6d:**:**:**:8c][LINK_ID=0]:
\tACTIVE/HW_BAND/OMAC_IDX: 1/1/0
\tSSID/QBSS/PHY/PWR_STATE: 10 PrivateTest/1/0xc8/1
\tCHANNEL/WIDTH/S1/S2: 37/4/31/0
\tGRP_MLD_ID/OWN_MLD_ID: 0/32

\tBSS[1][RF_BAND=2][OMAC=0c:07:**:**:**:e0][LINK_ID=1]:
\tACTIVE/HW_BAND/OMAC_IDX: 1/1/0
\tSSID/QBSS/PHY/PWR_STATE: 7 PrivateMlo/1/0xf8/1
\tCHANNEL/WIDTH/S1/S2: 36/2/50/0
\tGRP_MLD_ID/OWN_MLD_ID: 0/32
}
"""


class DecodeShowMldTests(unittest.TestCase):
    def run_tool(self, text: str):
        return subprocess.run(
            ["/usr/bin/python3", str(TOOL), "-", "--source-label", "fixture"],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_maps_320_1_and_160_and_omits_ssid(self):
        result = self.run_tool(FIXTURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["schema"], "mt6639-show-mld-v1")
        self.assertEqual(parsed["record_count"], 2)
        first, second = parsed["records"]
        self.assertEqual(first["width_code"], 4)
        self.assertEqual(first["width_enum"], "CW_320_1MHZ")
        self.assertEqual(first["nominal_width_mhz"], 320)
        self.assertTrue(first["s1_consistent_with_width"])
        self.assertEqual(second["width_enum"], "CW_160MHZ")
        self.assertIsNone(second["s1_consistent_with_width"])
        self.assertNotIn("PrivateTest", result.stdout)
        self.assertFalse(parsed["constraints"]["data_plane_proven"])

    def test_width_two_means_160_not_320(self):
        parsed = json.loads(self.run_tool(FIXTURE).stdout)
        self.assertEqual(parsed["records"][1]["nominal_width_mhz"], 160)

    def test_unknown_width_code_fails_closed(self):
        result = self.run_tool(
            "\tBSS[0][RF_BAND=3][OMAC=00:11:**:**:**:33][LINK_ID=0]:\n"
            "\tCHANNEL/WIDTH/S1/S2: 37/6/31/0\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNKNOWN_WIDTH_CODE=6", result.stderr)

    def test_320_2_requires_its_own_s1_centres(self):
        result = self.run_tool(
            "\tBSS[0][RF_BAND=3][OMAC=00:11:**:**:**:33][LINK_ID=0]:\n"
            "\tCHANNEL/WIDTH/S1/S2: 37/5/31/0\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)["records"][0]
        self.assertEqual(record["width_enum"], "CW_320_2MHZ")
        self.assertFalse(record["s1_consistent_with_width"])

    def test_output_file_is_written_atomically(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-show-mld-test-") as td:
            output = Path(td) / "summary.json"
            result = subprocess.run(
                [
                    "/usr/bin/python3",
                    str(TOOL),
                    "-",
                    "--source-label",
                    "fixture",
                    "--output",
                    str(output),
                ],
                input=FIXTURE,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertTrue(output.is_file())
            self.assertFalse(output.with_name(output.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
