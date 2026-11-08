"""Tests for the F01-F10 evidence matrix auditor."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import pubenv

REPO = Path(__file__).resolve().parents[1]
MATRIX = REPO / "release/f01-f10-matrix-20261026.json"
TOOL = REPO / "tools/audit_f01_f10_matrix.py"


@unittest.skipUnless(MATRIX.is_file(),
                     "the F01-F10 matrix references private evidence and is not published")
class MatrixTests(unittest.TestCase):
    def run_audit(self, path: Path):
        return subprocess.run([
            sys.executable, str(TOOL), "--matrix", str(path), "--root", str(REPO),
        ], capture_output=True, text=True, timeout=10)

    def test_current_matrix_is_valid_but_not_ready(self):
        done = self.run_audit(MATRIX)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual((result["verified"], result["partial"], result["not_started"]),
                         (0, 9, 1))
        self.assertFalse(result["acceptance_ready"])

    def test_verified_with_blocker_is_rejected(self):
        data = json.loads(MATRIX.read_text(encoding="utf-8"))
        data["items"][0]["status"] = "verified"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "matrix.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            done = self.run_audit(path)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot retain blockers", done.stderr)

    def test_unsafe_evidence_path_is_rejected(self):
        data = json.loads(MATRIX.read_text(encoding="utf-8"))
        data["items"][0]["existing_evidence"][0] = "../outside.md"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "matrix.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            done = self.run_audit(path)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unsafe evidence path", done.stderr)


if __name__ == "__main__":
    unittest.main()
