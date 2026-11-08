"""Verify the diagnostic bridge without loading a module or sending TWT."""
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

from test_mlo_observer import CLANG, ROOT, VERMAGIC, elf_info

class TwtBridgeTests(unittest.TestCase):
    def test_protocol_fixed_tuple_and_one_teardown_only(self):
        with tempfile.TemporaryDirectory(prefix="mttwt-protocol-") as folder:
            exe = pathlib.Path(folder) / "harness"
            subprocess.run([CLANG, "-DMT_TWT_PROTOCOL=1", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            str(ROOT / "tests/twt_bridge_harness.c"), "-o", str(exe)], check=True)
            self.assertIn("PASS TWT bridge:", subprocess.check_output([str(exe)], text=True))

    def test_actual_callback_and_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix="mttwt-host-") as folder:
            exe = pathlib.Path(folder) / "harness"
            subprocess.run([CLANG, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            str(ROOT / "tests/twt_bridge_harness.c"), "-o", str(exe)], check=True)
            self.assertIn("PASS TWT bridge:", subprocess.check_output([str(exe)], text=True))

    def test_kernel_artifact_keeps_cfi_and_only_expected_imports(self):
        with tempfile.TemporaryDirectory(prefix="mttwt-elf-") as folder:
            out = pathlib.Path(folder) / "probe.ko"
            cmd = [sys.executable, str(ROOT / "tools/build_twt_probe.py"), "--out", str(out)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            data, sections, named, symbols = elf_info(out)
            byname = {s[0]: s for s in symbols}
            for name, typeid in (("mttwt_init", 0x6fbb3035), ("mttwt_exit", 0xe5c47d60),
                                 ("mttwt_pre", 0xc073bc77)):
                sym = byname[name]
                offset = sections[sym[1]][4] + sym[2]
                self.assertEqual(struct.unpack_from("<I", data, offset - 4)[0], typeid)
                self.assertEqual(struct.unpack_from("<I", data, offset)[0], 0xd503245f)
            self.assertEqual({s[0] for s in symbols if s[1] == 0 and s[0]},
                             {"register_kprobe", "unregister_kprobe", "_printk"})
            sec = named[".modinfo"]; meta = data[sec[4]:sec[4] + sec[5]]
            self.assertIn(VERMAGIC, meta)
            self.assertIn(b"name=mttwt_probe\0", meta)
            self.assertIn(b"probe-only; no Setup or DEL\0", meta)
            sec = named[".gnu.linkonce.this_module"]
            self.assertEqual((sec[5], sec[8]), (0x680, 64))
            rel = named[".rela.gnu.linkonce.this_module"]
            self.assertEqual({struct.unpack_from("<Q", data, off)[0]
                              for off in range(rel[4], rel[4] + rel[5], 24)}, {0x178, 0x600})
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(out.read_bytes(), data)

if __name__ == "__main__":
    unittest.main()
