"""Work-directory permissions and the original native ancestor check.

Only temporary regular files/directories are used. The C test extracts the
unchanged directory/private_dir functions; it never runs mount or radio code.
"""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

import test_mt6639_control as control_fixture


ROOT = Path(__file__).resolve().parents[1]


class WorkDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = control_fixture.ControlTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work = self.fixture.work

    def source_lib(self, body='echo initialized', **env):
        return subprocess.run(
            ['sh', '-c', '. "$MODDIR/bin/lib.sh"; ' + body],
            env=dict(self.fixture.env, MODDIR=str(self.fixture.mod), **env),
            text=True, capture_output=True, timeout=10)

    def test_new_directory_is_private_with_zero_umask(self):
        self.work.rmdir()
        result = subprocess.run(
            ['sh', '-c', 'umask 000; . "$MODDIR/bin/lib.sh"; umask'],
            env=dict(self.fixture.env, MODDIR=str(self.fixture.mod)),
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '0000')
        self.assertEqual(self.work.stat().st_mode & 0o7777, 0o700)

    def test_existing_writable_directory_is_fixed_without_recursive_changes(self):
        self.work.chmod(0o777)
        record = self.work / 'evidence'
        record.write_text('preserve\n')
        record.chmod(0o640)
        result = self.source_lib()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.work.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(record.stat().st_mode & 0o7777, 0o640)
        self.assertEqual(record.read_text(), 'preserve\n')

    def test_symlink_and_file_are_rejected_before_log_or_state_writes(self):
        self.work.rmdir()
        outside = self.fixture.root / 'outside'
        outside.mkdir(mode=0o755)
        self.work.symlink_to(outside)
        result = self.source_lib()
        self.assertEqual(result.returncode, 3)
        self.assertIn('work_directory_not_private', result.stderr)
        self.assertEqual(outside.stat().st_mode & 0o7777, 0o755)
        self.assertEqual(list(outside.iterdir()), [])
        self.work.unlink()
        self.work.write_text('unrelated')
        self.assertEqual(self.source_lib().returncode, 3)
        self.assertEqual(self.work.read_text(), 'unrelated')

    def test_chmod_failure_blocks_entry(self):
        stub = self.fixture.root / 'stub/chmod'
        stub.write_text('#!/bin/sh\nexit 1\n')
        stub.chmod(0o755)
        result = self.source_lib()
        self.assertEqual(result.returncode, 3)
        self.assertNotIn('initialized', result.stdout)

    @unittest.skipUnless(os.geteuid() == 0, 'owner rejection requires private root-owned fixtures')
    def test_foreign_owner_is_not_chowned_or_chmodded(self):
        self.work.chmod(0o777)
        os.chown(self.work, 1000, 1000)
        self.addCleanup(os.chown, self.work, 0, 0)
        result = self.source_lib()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.work.stat().st_uid, 1000)
        self.assertEqual(self.work.stat().st_mode & 0o7777, 0o777)

    def test_failed_start_record_is_retired_only_after_checked_new_boot(self):
        f = self.fixture
        tx = self.work / 'transactions/t-unstarted'
        tx.mkdir(parents=True, mode=0o700)
        (tx / 'wrapper-boot').write_text(Path(f.env['BOOT_ID_FILE']).read_text())
        (tx / 'apply-wrapper.log').write_text('\n')
        (self.work / 'active_tx').write_text(str(tx) + '\n')
        (self.work / 'active_pid').write_text('')
        (self.work / 'state').write_text('restore_failed\n')
        self.work.chmod(0o777)
        # rc64 with no native journal is not a trustworthy no-effects receipt.
        result = f.control('disable', RECOVER_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.work / 'active_tx').read_text(), str(tx) + '\n')
        Path(f.env['BOOT_ID_FILE']).write_text('22222222-2222-4222-8222-222222222222\n')
        result = f.control('enable', RECOVER_FAIL='1')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(tx.is_dir())
        archived = list((self.work / 'retired').glob('*/active_tx'))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(), str(tx) + '\n')

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which('cc'),
                         'native private chroot fixture requires root and a C compiler')
    def test_original_native_directory_walk_rejects_777_and_accepts_fixed_work(self):
        source = (ROOT / 'native/recovery/mount_txn.c').read_text()
        private = source[source.index('static int private_dir('):source.index('/* Walk every component')]
        walk = source[source.index('static int directory('):source.index('static int valid_path(')]
        c = self.fixture.root / 'guard.c'
        c.write_text('''#define _GNU_SOURCE
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
''' + private + walk + '''
int main(int argc, char **argv) {
    int fd;
    if (argc != 2 || chroot(argv[1]) || chdir("/")) return 99;
    fd = directory("/data/adb/mt6639-6g/transactions/t-fixture");
    if (!private_dir(fd)) return 64;
    close(fd);
    fd = directory("/odm/firmware");
    if (fd < 0) return 65;
    close(fd);
    return 0;
}
''')
        exe = self.fixture.root / 'guard'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)],
                       check=True, capture_output=True, timeout=30)
        jail = self.fixture.root / 'jail'
        tx = jail / 'data/adb/mt6639-6g/transactions/t-fixture'
        tx.mkdir(parents=True)
        (jail / 'odm/firmware').mkdir(parents=True)
        os.chown(jail / 'data', 1000, 1000)
        (jail / 'data').chmod(0o771)
        (jail / 'data/adb').chmod(0o700)
        tx.chmod(0o700)
        work = jail / 'data/adb/mt6639-6g'
        work.chmod(0o777)
        before = subprocess.run([str(exe), str(jail)], timeout=10)
        self.assertEqual(before.returncode, 64)
        result = self.source_lib(WORK=str(work))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = subprocess.run([str(exe), str(jail)], timeout=10)
        self.assertEqual(after.returncode, 0)

    def test_existing_writable_transactions_subdir_is_fixed_or_rejected(self):
        tx_root = self.work / 'transactions'
        tx_root.mkdir(mode=0o777)
        tx_root.chmod(0o777)
        child = tx_root / 't-existing'
        child.mkdir(mode=0o750)
        child.chmod(0o750)
        result = self.source_lib()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tx_root.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(child.stat().st_mode & 0o7777, 0o750)

        shutil.rmtree(tx_root)
        outside = self.fixture.root / 'outside-tx'
        outside.mkdir(mode=0o755)
        tx_root.symlink_to(outside)
        rejected = self.source_lib()
        self.assertEqual(rejected.returncode, 3)
        self.assertIn('work_directory_not_private', rejected.stderr)
        self.assertEqual(outside.stat().st_mode & 0o7777, 0o755)


if __name__ == '__main__':
    unittest.main()
