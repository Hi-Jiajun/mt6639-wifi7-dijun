"""Minimal observation smoke + artifact inspection; complete matrix is deferred."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from test_mlo_observer import CLANG, ROOT, elf_info


class TwtObserveSmoke(unittest.TestCase):
    def test_real_callbacks_and_decoder(self):
        with tempfile.TemporaryDirectory(prefix='twt-observe-') as d:
            exe = Path(d) / 'observe'
            subprocess.run([CLANG, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                            str(ROOT/'tests/twt_observe_harness.c'), '-o', str(exe)], check=True)
            log = subprocess.check_output([str(exe)], text=True)
            self.assertIn('PASS observation:', log)
            self.assertNotIn('aaaaaaaaaaaa', log)
            spec = importlib.util.spec_from_file_location('twt_summary', ROOT/'tools/summarize_twt_observation.py')
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            # This is a host fixture, not a real Accept. Only inject that log
            # into this test to verify evidence combination and output limits.
            r = m.summarize('twtPlannerRxNegoResult: Rx nego id 0 resp=4\n'+log)
            self.assertTrue(r['accepted_parameter_submission_observed'])
            self.assertEqual(r['host'][0]['exponent'], 9)  # differs from requested 8
            self.assertEqual(r['host'][0]['duration_raw'], 32)
            self.assertEqual(r['host'][0]['interval_us'], 65536)
            self.assertEqual(r['returned'][0]['return_status_u32'], 0x103)
            self.assertFalse(r['fw_periodic_execution_verified'])
            self.assertFalse(r['power_saving_verified'])
            self.assertFalse(m.summarize(log)['accepted_parameter_submission_observed'])

    @unittest.skipUnless((ROOT / 'local/evidence/mlo-unpin-20261023d/backup/wlan.stock.ko').is_file(),
                         'private stock .ko fixture is not published')
    def test_observation_artifact(self):
        with tempfile.TemporaryDirectory(prefix='twt-observe-elf-') as d:
            out = Path(d) / 'once.ko'
            subprocess.run([sys.executable, str(ROOT/'tools/build_twt_probe.py'), '--protocol-once',
                            '--observe', '--stock', str(ROOT/'local/evidence/mlo-unpin-20261023d/backup/wlan.stock.ko'),
                            '--out', str(out)], check=True, capture_output=True)
            data, sections, named, symbols = elf_info(out)
            byname = {s[0]: s for s in symbols}
            for name, cfi in (('twt_observe_pre', 0xc073bc77), ('mttwt_pre', 0xc073bc77),
                              ('mttwt_init', 0x6fbb3035), ('mttwt_exit', 0xe5c47d60)):
                s = byname[name]; at = sections[s[1]][4]+s[2]
                self.assertEqual(struct.unpack_from('<I', data, at-4)[0], cfi)
                self.assertEqual(struct.unpack_from('<I', data, at)[0], 0xd503245f)
            self.assertEqual({s[0] for s in symbols if not s[1] and s[0]},
                             {'register_kprobe', 'unregister_kprobe', '_printk', 'copy_from_kernel_nofault'})


if __name__ == '__main__':
    unittest.main()
