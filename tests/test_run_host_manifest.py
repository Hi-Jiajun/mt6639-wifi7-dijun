"""Tests for the bounded repository host-test manifest runner."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/run_host_test_manifest.py"


class HostManifestTests(unittest.TestCase):
    def run_runner(self, manifest: dict, out: Path):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, dir="/tmp") as handle:
            json.dump(manifest, handle)
            path = handle.name
        try:
            return subprocess.run([
                sys.executable, str(TOOL), "--manifest", path, "--out", str(out),
            ], capture_output=True, text=True, timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_single_test_and_summary(self):
        manifest = {
            "schema": "mt6639-host-test-manifest-v1",
            "tests": [{
                "id": "release",
                "command": ["/usr/bin/python3", "tests/test_ap_station_ie_recheck.py"],
                "timeout_s": 20,
            }],
        }
        with tempfile.TemporaryDirectory() as td:
            done = self.run_runner(manifest, Path(td) / "result")
            self.assertEqual(done.returncode, 0, done.stderr)
            summary = json.loads((Path(td) / "result/summary.json").read_text())
        self.assertEqual(summary["tests"], 1)
        self.assertEqual(summary["passed"], 1)

    def test_disallowed_interpreter_is_rejected(self):
        manifest = {
            "schema": "mt6639-host-test-manifest-v1",
            "tests": [{
                "id": "bad",
                "command": ["/bin/sh", "tests/test_ap_station_ie_recheck.py"],
                "timeout_s": 20,
            }],
        }
        with tempfile.TemporaryDirectory() as td:
            done = self.run_runner(manifest, Path(td) / "result")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("interpreter not allowed", done.stderr)

    def test_duplicate_ids_are_rejected(self):
        item = {
            "id": "same",
            "command": ["/usr/bin/python3", "tests/test_ap_station_ie_recheck.py"],
            "timeout_s": 20,
        }
        manifest = {"schema": "mt6639-host-test-manifest-v1", "tests": [item, item]}
        with tempfile.TemporaryDirectory() as td:
            done = self.run_runner(manifest, Path(td) / "result")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("duplicate", done.stderr)


if __name__ == "__main__":
    unittest.main()
