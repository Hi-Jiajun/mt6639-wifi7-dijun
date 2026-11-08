import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLANG = (os.environ.get("MT6639_CLANG") or shutil.which("clang")
         or "clang")

class Plan7AdapterTests(unittest.TestCase):
    def require_sources(self):
        for name in ("native/mlo-gate/plan7.c", "native/mlo-gate/abi.h", "tools/build_mlo_plan7.py"):
            self.assertTrue((ROOT/name).is_file(), "missing plan7 implementation: "+name)

    def test_real_callback_changes_only_plan7_x8(self):
        self.require_sources()
        with tempfile.TemporaryDirectory(prefix="mtmlo-plan7-host-") as folder:
            exe = pathlib.Path(folder) / "harness"
            subprocess.run([CLANG,"-std=c11","-Wall","-Wextra","-Werror","-O2",
                            str(ROOT/"tests/mlo_plan7_harness.c"),"-o",str(exe)], check=True)
            output = subprocess.check_output([str(exe)], text=True)
            self.assertIn("PASS plan7:", output)

    def test_builds_fresh_artifact_without_patching_kcfi(self):
        self.require_sources()
        with tempfile.TemporaryDirectory(prefix="mtmlo-plan7-build-") as folder:
            out = pathlib.Path(folder) / "plan7.ko"
            subprocess.run([sys.executable, str(ROOT/"tools/build_mlo_plan7.py"),
                            "--out", str(out)], check=True, capture_output=True, text=True)
            self.assertGreater(out.stat().st_size, 4096)
            second = subprocess.run([sys.executable, str(ROOT/"tools/build_mlo_plan7.py"),
                                     "--out", str(out)], capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)

if __name__ == "__main__":
    unittest.main()
