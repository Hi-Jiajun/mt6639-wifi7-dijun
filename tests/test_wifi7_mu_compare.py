"""Offline behavior tests for conservative Wi-Fi 7 MU comparison."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wifi7_mu_compare", REPO / "tools/wifi7_mu_compare.py")
MU = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MU)

BOOT = "11111111-1111-4111-8111-111111111111"
MAC1 = "02:00:00:00:00:01"
MAC2 = "02:00:00:00:00:02"


def stations(tx1: int, tx2: int, duration: int = 1000) -> str:
    return (
        "sample_epoch=synthetic\n"
        f"Station {MAC1} (on wlan0)\n"
        f"        tx bytes:{tx1}\n"
        "        rx bytes:100\n"
        f"        tx duration:{duration} us\n"
        "        rx duration:1000 us\n"
        f"Station {MAC2} (on wlan0)\n"
        f"        tx bytes:{tx2}\n"
        "        rx bytes:100\n"
        f"        tx duration:{duration} us\n"
        "        rx duration:1000 us\n"
    )


def tx_stats(mpdu: int, successful: int) -> str:
    return (
        "sample_epoch=synthetic\n"
        "Phy phy0, Phy band 3\n"
        f"Tx multi-user MPDU counts: {mpdu}\n"
        f"Tx multi-user successful MPDU counts: {successful}\n"
        "Tx multi-user beamforming counts: 0\n"
    )


def manifest(root: Path, *, after_boot: str = BOOT) -> dict:
    paths = {
        "before.stations": stations(100, 200),
        "before.tx": tx_stats(10, 8),
        "after.stations": stations(200, 300, duration=1100),
        "after.tx": tx_stats(20, 18),
    }
    for name, text in paths.items():
        (root / name).write_text(text, encoding="utf-8")

    def sample(prefix: str, boot: str, start: int) -> dict:
        run_id = f"{prefix}-run"
        return {
            "ap_id": "ap-synthetic",
            "boot_id": boot,
            "driver_epoch_id": "driver-1",
            "counter_epoch_id": "counter-1",
            "bss_id": "bss-1",
            "bssid": "02:00:00:00:00:aa",
            "interface": "wlan0",
            "frequency_mhz": 6135,
            "width_mhz": 320,
            "phy_name": "phy0",
            "band_index": 3,
            "sources": {
                "station_dump": {
                    "path": f"{prefix}.stations",
                    "rc": 0,
                    "clock_id": "AP_BOOTTIME_NS",
                    "run_id": run_id,
                    "started_ns": start,
                    "completed_ns": start + 100,
                },
                "tx_stats": {
                    "path": f"{prefix}.tx",
                    "rc": 0,
                    "clock_id": "AP_BOOTTIME_NS",
                    "run_id": run_id,
                    "started_ns": start + 100,
                    "completed_ns": start + 200,
                },
            },
            "association_generation": {MAC1: "assoc-1", MAC2: "assoc-2"},
        }

    return {
        "schema": "mt6639-wifi7-mu-window-v1",
        "clock_id": "AP_BOOTTIME_NS",
        "policy": {"max_sample_span_ms": 1000, "max_outer_window_ms": 10000},
        "bss_id": "bss-1",
        "scope": {
            "interface": "wlan0",
            "phy_name": "phy0",
            "band_index": 3,
            "binding_evidence_ref": "synthetic/phy-bss-binding",
        },
        "before": sample("before", BOOT, 1000),
        "after": sample("after", after_boot, 3000),
        "eht_clients": [
            {
                "client_id": "client-a",
                "mac": MAC1,
                "single_link": True,
                "mld_id": None,
                "evidence_ref": "synthetic/client-a-eht",
            },
            {
                "client_id": "client-b",
                "mac": MAC2,
                "single_link": True,
                "mld_id": None,
                "evidence_ref": "synthetic/client-b-eht",
            },
        ],
    }


class Wifi7MuCompareTests(unittest.TestCase):
    def test_two_active_clients_and_mu_growth_is_initial_evidence(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-mu-") as td:
            root = Path(td)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest(root)), encoding="utf-8")
            result = MU.analyze(json.loads(path.read_text()), path)
        self.assertEqual(result["result"], "mu_initial_evidence")
        self.assertEqual(result["active_target_count"], 2)
        self.assertTrue(result["facts"]["phy_mu_success_observed"])
        self.assertEqual(result["attribution"], "phy_aggregate_only")
        self.assertEqual(result["mu_type"], "undetermined")

    def test_cross_boot_does_not_form_a_common_window(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-mu-") as td:
            root = Path(td)
            path = root / "manifest.json"
            path.write_text(json.dumps(
                manifest(root, after_boot="22222222-2222-4222-8222-222222222222")),
                encoding="utf-8")
            result = MU.analyze(json.loads(path.read_text()), path)
        self.assertEqual(result["result"], "incomplete")
        self.assertIn("EPOCH_CHANGED", result["reason_codes"])
        self.assertIsNone(result["window"]["common_inner_window_ns"])
        self.assertIsNone(result["window"]["outer_window_ns"])

    def test_malformed_station_header_is_not_attributed_to_previous_client(self):
        broken = stations(100, 200).replace(
            f"Station {MAC2} (on wlan0)", "Station malformed")
        with self.assertRaises(MU.InputError) as caught:
            MU.parse_station_dump(broken, "synthetic.stations")
        self.assertEqual(caught.exception.code, "INVALID_STATION_HEADER")


if __name__ == "__main__":
    unittest.main()
