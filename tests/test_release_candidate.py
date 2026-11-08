"""Tests for the read-only MT6639 release-candidate auditor."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
import importlib.util
from unittest.mock import patch


import pubenv

REPO = Path(__file__).resolve().parents[1]
AUDITOR = REPO / "tools/audit_release_candidate.py"
MANIFEST = REPO / "release/mt6639-candidate-20261026.json"


@unittest.skipUnless(pubenv.candidate_ready() and pubenv.AAPT2,
                     "candidate bundle or aapt2 missing")
class ReleaseCandidateTests(unittest.TestCase):
    def test_checksum_list_drift_is_rejected_by_candidate_auditor(self):
        spec = importlib.util.spec_from_file_location("release_candidate_auditor", AUDITOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = Path.read_text
        for suffix in ("\n", "duplicate"):
            def drift(path, *args, **kwargs):
                text = original(path, *args, **kwargs)
                if path == REPO / "release/SHA256SUMS":
                    first = text.splitlines()[0]
                    return text + first + "\n" if suffix == "duplicate" else text.replace(first, "0" * 64 + first[64:], 1)
                return text
            with self.subTest(suffix=suffix), patch.object(Path, "read_text", drift):
                with self.assertRaises(module.AuditError):
                    module.audit(MANIFEST, REPO)

    def run_auditor(self, manifest: Path, root: Path | None = None):
        command = ["/usr/bin/python3", str(AUDITOR), "--manifest", str(manifest)]
        if root is not None:
            command.extend(["--root", str(root)])
        return subprocess.run(
            command,
            capture_output=True, text=True, timeout=30,
        )

    def test_current_manifest_assets_pass_but_release_is_blocked(self):
        result = self.run_auditor(MANIFEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(parsed["assets_verified"], len(manifest["artifacts"]))
        self.assertFalse(parsed["release_ready"])
        self.assertGreaterEqual(len(parsed["blockers"]), 5)
        self.assertEqual(len(parsed["verification_manifests"]), 2)

    def test_hash_tampering_fails(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["artifacts"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="mt6639-release-test-") as td:
            root = Path(td) / "release"
            root.mkdir()
            path = root / "manifest.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_auditor(path, REPO)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256 mismatch", result.stderr)

    def test_unsafe_artifact_path_fails(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["artifacts"][0]["path"] = "../outside.zip"
        with tempfile.TemporaryDirectory(prefix="mt6639-release-test-") as td:
            root = Path(td) / "release"
            root.mkdir()
            path = root / "manifest.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_auditor(path, REPO)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe path", result.stderr)


if __name__ == "__main__":
    unittest.main()
