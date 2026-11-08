"""Prepared inode pair proves effect-window ownership; bytes alone do not.

Deleting pair validation would make foreign-inode refusals fail; requiring
applied unconditionally would break actual effect-before-WAL cancellation.
All processes/files belong to fresh test fixtures; no ADB or mocked rename.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class PreparedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="mt6639-pair-build-")
        cls.binary = Path(cls.build.name) / "probe"
        cls.fault = Path(cls.build.name) / "probe-fault"
        common = ["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror", "-O2"]
        for out, flags in ((cls.binary, []), (cls.fault, ["-Dfsync=mt_test_setup_fsync", str(REPO/"tests/native_setup_fault.c")])):
            run = subprocess.run(common+[str(REPO/"native/recovery/probe.c")]+flags+["-o",str(out)], capture_output=True,text=True,timeout=30)
            if run.returncode: raise AssertionError(run.stderr)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mt6639-pair-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.processes = []
        self.addCleanup(self.clean_processes)

    def clean_processes(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=3)
                except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=3)
            p.stdout.close(); p.stderr.close()

    def call(self, command="probe-exchange-recover", *args):
        return subprocess.run([str(self.binary),command,str(self.root),*args],capture_output=True,text=True,timeout=4)

    def start(self, stage="effect"):
        p = subprocess.Popen([str(self.binary),"probe-exchange-run",str(self.root),"2000","4000",stage],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.processes.append(p)
        readable,_,_=select.select([p.stdout],[],[],3)
        self.assertTrue(readable,"no initial event")
        event=json.loads(p.stdout.readline())
        self.assertEqual(event["event"],"ready",event)
        end=time.monotonic()+2
        while time.monotonic()<end:
            path=self.root/"stage"
            if path.exists() and path.read_text()==stage+"\n": return p
            time.sleep(.01)
        self.fail("did not reach diagnostic stage")

    def identities(self):
        return tuple((self.root/n).stat().st_ino for n in ("fixture","prepared"))

    def test_normal_preserves_original_inode_and_prepared_object(self):
        p=self.start("before")
        original=self.identities()
        out,err=p.communicate(timeout=4)
        self.assertEqual(p.returncode,0,(out,err))
        self.assertEqual(self.identities(),original)
        self.assertEqual((self.root/"fixture").read_bytes(),b"baseline\n")
        self.assertEqual((self.root/"prepared").read_bytes(),b"candidate\n")
        self.assertTrue((self.root/"journal").read_text().endswith("closed\n"))

    def test_effect_without_applied_record_restores_exact_prepared_pair(self):
        p=self.start()
        swapped=self.identities()
        original_ino=int((self.root/"resource").read_text().split()[1])
        candidate_ino=int((self.root/"prepared_resource").read_text().split()[1])
        self.assertEqual(swapped,(candidate_ino,original_ino))
        self.assertFalse("applied\n" in (self.root/"journal").read_text().split("intent\n",1)[-1])
        p.terminate()
        out,err=p.communicate(timeout=3)
        self.assertEqual(p.returncode,0,(out,err))
        self.assertEqual(json.loads(out.splitlines()[-1])["recovery"],"restored")
        self.assertEqual(self.identities(),(original_ino,candidate_ino))
        self.assertNotIn("\napplied\n",(self.root/"journal").read_text())

    def test_intent_without_exchange_cancels_without_changing_inode(self):
        p=self.start("intent"); original=self.identities(); p.terminate()
        out,err=p.communicate(timeout=3)
        self.assertEqual(p.returncode,0,(out,err)); self.assertEqual(self.identities(),original)

    def assert_conflict_after_foreign_change(self, stage, name, content):
        p=self.start(stage)
        other="prepared" if name=="fixture" else "fixture"
        other_before=((self.root/other).stat().st_ino,(self.root/other).read_bytes())
        foreign=self.root/"external"
        foreign.write_bytes(content); foreign.chmod(0o600)
        ino=foreign.stat().st_ino
        foreign.replace(self.root/name)
        p.terminate(); out,err=p.communicate(timeout=3)
        self.assertEqual(p.returncode,3,(out,err))
        self.assertEqual((self.root/name).stat().st_ino,ino)
        self.assertEqual((self.root/name).read_bytes(),content)
        self.assertEqual(((self.root/other).stat().st_ino,(self.root/other).read_bytes()),other_before)
        again=self.call(); self.assertEqual(again.returncode,3,again.stdout)

    def assert_no_applied_after_effect_drift(self, name, content):
        p=self.start("effect")
        other="prepared" if name=="fixture" else "fixture"
        other_before=((self.root/other).stat().st_ino,(self.root/other).read_bytes())
        foreign=self.root/"external"
        foreign.write_bytes(content); foreign.chmod(0o600)
        ino=foreign.stat().st_ino
        foreign.replace(self.root/name)
        out,err=p.communicate(timeout=4)
        self.assertEqual(p.returncode,3,(out,err))
        self.assertEqual((self.root/name).stat().st_ino,ino)
        self.assertEqual((self.root/name).read_bytes(),content)
        self.assertEqual(((self.root/other).stat().st_ino,(self.root/other).read_bytes()),other_before)
        self.assertNotIn("\napplied\n",(self.root/"journal").read_text())

    def test_effect_fixture_drift_is_not_recorded_as_applied(self):
        self.assert_no_applied_after_effect_drift("fixture",b"candidate\n")

    def test_effect_prepared_drift_is_not_recorded_as_applied(self):
        self.assert_no_applied_after_effect_drift("prepared",b"baseline\n")

    def test_foreign_same_candidate_bytes_are_not_prepared_ownership(self):
        self.assert_conflict_after_foreign_change("effect","fixture",b"candidate\n")

    def test_foreign_original_bytes_at_other_end_block_restore(self):
        self.assert_conflict_after_foreign_change("effect","prepared",b"baseline\n")

    def test_foreign_prepared_inode_before_effect_is_preserved(self):
        self.assert_conflict_after_foreign_change("before","prepared",b"candidate\n")

    def test_missing_prepared_object_blocks_without_touching_target(self):
        p=self.start(); before=(self.root/"fixture").read_bytes()
        (self.root/"prepared").rename(self.root/"external")
        p.terminate(); out,err=p.communicate(timeout=3)
        self.assertEqual(p.returncode,3,(out,err))
        self.assertEqual((self.root/"fixture").read_bytes(),before)

    def test_hardlinked_prepared_object_blocks_without_overwrite(self):
        p=self.start(); os.link(self.root/"prepared",self.root/"external")
        p.terminate(); out,err=p.communicate(timeout=3)
        self.assertEqual(p.returncode,3,(out,err))
        self.assertEqual((self.root/"fixture").read_bytes(),b"candidate\n")

    def test_wrong_adapter_and_live_transaction_do_not_restore(self):
        p=self.start(); duplicate=self.call()
        self.assertEqual(duplicate.returncode,2,duplicate.stdout)
        p.terminate(); p.communicate(timeout=3)
        wrong=self.call("probe-recover")
        self.assertEqual(wrong.returncode,3,wrong.stdout)
        correct=self.call(); self.assertEqual(correct.returncode,0,correct.stdout)

    def test_partial_prepared_setup_aborts_without_rewriting_objects(self):
        run=subprocess.run([str(self.fault),"probe-exchange-run",str(self.root),"0","1000"],env=dict(os.environ,MT_TEST_FSYNC_AT="13"),capture_output=True,text=True,timeout=3)
        self.assertEqual(run.returncode,-9,(run.stdout,run.stderr))
        before={p.name:(p.stat().st_ino,p.read_bytes()) for p in self.root.iterdir() if p.name!="journal"}
        result=self.call()
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual(json.loads(result.stdout)["recovery"],"not_applied")
        self.assertEqual(before,{p.name:(p.stat().st_ino,p.read_bytes()) for p in self.root.iterdir() if p.name!="journal"})

    def test_closed_transaction_does_not_adopt_same_bytes_new_inode(self):
        run=self.call("probe-exchange-run","0","1000")
        self.assertEqual(run.returncode,0,run.stdout)
        foreign=self.root/"external"; foreign.write_bytes(b"baseline\n"); foreign.chmod(0o600)
        foreign.replace(self.root/"fixture")
        result=self.call(); self.assertEqual(result.returncode,3,result.stdout)


if __name__=="__main__": unittest.main()
