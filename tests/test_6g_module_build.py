"""Reproducible build checks for the current-source 6GHz module."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile


REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "tools/build_6g_module.sh"
MODULE = REPO / "modules/mt6639-6g"
SHIPPED = REPO / "release/artifacts/mt6639-6g-v0.3.2-dijun.1.zip"
NATIVE = Path(os.environ.get("MT6639_MOUNT_TXN", MODULE / "bin/mount-txn"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def info_zip_available() -> bool:
    """Byte-identical rebuilds are only promised with the original packer."""
    try:
        result = subprocess.run(["zip", "-v"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return "Info-ZIP" in (result.stdout + result.stderr)


def members(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            (name[2:] if name.startswith("./") else name):
                hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if not name.endswith("/")
        }


class SixGModuleBuildTests(unittest.TestCase):
    def build(self, output: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(BUILDER), str(output)],
            capture_output=True, text=True, timeout=30,
        )

    @unittest.skipUnless(NATIVE.is_file(),
                         "the pinned mount-txn binary is not published in the tree")
    def test_two_builds_are_reproducible_and_complete(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-6g-build-test-") as td:
            first = Path(td) / "first.zip"
            second = Path(td) / "second.zip"
            for output in (first, second):
                result = self.build(output)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(),
                             hashlib.sha256(second.read_bytes()).hexdigest())
            # Two builds of the same source are byte-identical. The absolute
            # hash is only comparable against the shipped artifact: Info-ZIP
            # version differences change the container bytes, so content
            # identity (member set + per-member hashes) is the portable claim.
            if SHIPPED.is_file():
                self.assertEqual(members(first), members(SHIPPED))
            with zipfile.ZipFile(first) as archive:
                content = {
                    (n[2:] if n.startswith("./") else n): archive.read(n)
                    for n in archive.namelist() if not n.endswith("/")
                }
            for required in (
                "module.prop", "module.prop.template", "action.sh", "service.sh", "uninstall.sh",
                "config.conf", "compatibility.conf", "bin/compat.sh", "bin/mount-txn", "bin/native.sh",
                "bin/restore.sh", "bin/post-reboot-check.sh",
                "bin/refresh-6g.sh", "bin/status-card.sh", "bin/verify-module.sh",
                "src/nl80211-wiphy-read.c", "SHA256SUMS", "ASSET-PROVENANCE.txt",
            ):
                self.assertIn(required, content)
            prop = content["module.prop"].decode()
            self.assertIn("version=v0.3.2-dijun.1", prop)
            self.assertIn("versionCode=21", prop)
            self.assertEqual(content["module.prop"], content["module.prop.template"])
            sums = content["SHA256SUMS"].decode()
            self.assertIn("bin/native.sh", sums)
            self.assertIn("bin/restore.sh", sums)
            self.assertIn("bin/refresh-6g.sh", sums)
            paths = [line.split(None, 1)[1] for line in sums.splitlines()]
            self.assertNotIn("module.prop", paths)
            self.assertIn("module.prop.template", paths)
            provenance = content["ASSET-PROVENANCE.txt"].decode()
            self.assertIn("source_tree_sha256=", provenance)
            self.assertNotIn("source_commit=", provenance)

    def test_source_fail_closed_recovery_changes_are_packaged(self):
        native = (MODULE / "bin/native.sh").read_text()
        restore = (MODULE / "bin/restore.sh").read_text()
        self.assertIn("busy_or_invalid_lock", native)
        self.assertIn("native_recover_retry", native)
        self.assertIn('[ "$attempt" -lt 15 ]', native)
        self.assertIn("mountinfo_unreadable", restore)
        self.assertIn("mounted_rc", restore)


if __name__ == "__main__":
    unittest.main()
