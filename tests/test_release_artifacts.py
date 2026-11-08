"""Verify the repository-local release artifact bundle."""
from pathlib import Path
import hashlib
import json
import re
import unittest


import pubenv

REPO = Path(__file__).resolve().parents[1]
RELEASE = REPO / "release"
SUMS = RELEASE / "SHA256SUMS"
MANIFEST = RELEASE / "mt6639-candidate-20261026.json"
LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


@unittest.skipUnless(pubenv.all_release_assets(), pubenv.SKIP_RELEASE_ASSETS)
class ReleaseArtifactTests(unittest.TestCase):
    def test_sha256sums_are_safe_and_exact(self):
        entries = {}
        for line in SUMS.read_text(encoding="ascii").splitlines():
            match = LINE.fullmatch(line)
            self.assertIsNotNone(match, line)
            expected, relative = match.groups()
            self.assertNotIn("..", Path(relative).parts)
            self.assertFalse(Path(relative).is_absolute())
            path = RELEASE / relative
            self.assertTrue(path.is_file(), relative)
            self.assertFalse(path.is_symlink(), relative)
            self.assertEqual(digest(path), expected, relative)
            entries[relative] = expected

        bundled = {
            str(path.relative_to(RELEASE))
            for path in (RELEASE / "artifacts").iterdir()
            if path.is_file()
        } | {
            str(path.relative_to(RELEASE))
            for path in (RELEASE / "rollback").iterdir()
            if path.is_file()
        }
        self.assertEqual(set(entries), bundled)

    def test_manifest_assets_match_bundle(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertFalse(manifest["release_ready"])
        self.assertTrue(manifest["blockers"])
        actual = {}
        for artifact in manifest["artifacts"]:
            path = REPO / artifact["path"]
            self.assertTrue(path.is_file(), artifact["path"])
            self.assertEqual(digest(path), artifact["sha256"], artifact["id"])
            actual[str(path.relative_to(RELEASE))] = artifact["sha256"]
        sums = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in SUMS.read_text(encoding="ascii").splitlines()
        }
        self.assertEqual(actual, sums)


if __name__ == "__main__":
    unittest.main()
