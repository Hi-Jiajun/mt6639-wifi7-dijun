"""Behavior tests for separating feature enumeration from single queries."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rtt_nan_report", REPO / "tools/rtt_nan_report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def fixture(root: Path, *, feature_name: str | None,
            aware_query: bool, rtt_query: bool):
    (root / "dumpsys-wifi.raw").write_text(
        "SupportedFeatures: [WIFI_FEATURE_AWARE, WIFI_FEATURE_INFRA]\n"
        "Connection state: STA=true, P2P=false, Aware=false\n"
        "mIs11mcSupported=false, mIs11azSupported=false\n",
        encoding="utf-8",
    )
    (root / "features").write_text(
        ("feature:" + feature_name + "\n") if feature_name else "feature:android.hardware.wifi\n",
        encoding="utf-8",
    )
    (root / "feature-aware.query").write_text(
        "true\n" if aware_query else "false\n", encoding="utf-8")
    (root / "feature-rtt.query").write_text(
        "true\n" if rtt_query else "false\n", encoding="utf-8")
    (root / "service-list").write_text("", encoding="utf-8")
    (root / "iw-phy").write_text("no fine timing fields\n", encoding="utf-8")


class RttNanReportTests(unittest.TestCase):
    def test_enumeration_absence_does_not_override_single_true(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture(root, feature_name=None, aware_query=True, rtt_query=False)
            result = REPORT.summarize(root)
        self.assertFalse(result["aware_framework_feature_enum"])
        self.assertTrue(result["aware_single_feature_query"])
        self.assertFalse(result["rtt_single_feature_query"])

    def test_enumeration_presence_does_not_override_single_false(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture(root, feature_name="android.hardware.wifi.aware",
                    aware_query=False, rtt_query=False)
            result = REPORT.summarize(root)
        self.assertTrue(result["aware_framework_feature_enum"])
        self.assertFalse(result["aware_single_feature_query"])

    @unittest.skipUnless((REPO / "outputs/rtt-nan-snapshot-20261026-3").is_dir(),
                         "device snapshot is private evidence")
    def test_current_oem_sample_keeps_both_false(self):
        result = REPORT.summarize(
            REPO / "outputs/rtt-nan-snapshot-20261026-3")
        self.assertFalse(result["aware_framework_feature_enum"])
        self.assertFalse(result["aware_single_feature_query"])
        self.assertFalse(result["rtt_framework_feature_enum"])
        self.assertFalse(result["rtt_single_feature_query"])


if __name__ == "__main__":
    unittest.main()
