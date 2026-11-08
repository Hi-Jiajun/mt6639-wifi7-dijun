"""Real process/lock tests for the native T02B fixture-only runtime.

Breaks caught: missing WAL fsync transitions, a second mutator, racing restore
against a live worker, overwritten external state, and a blocked cancellation.
No device access and no mocked locks/processes.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "native/recovery/probe.c"


def setup_header():
    if Path("/proc/sys/kernel/random/boot_id").exists():
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"setup\nprobe_v1\n{boot}\n{os.readlink('/proc/self/ns/mnt')}\n".encode()
    return b"setup\nprobe_v1\nhost_fixture_only\n"


class RecoveryProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="mt6639-probe-build-")
        cls.binary = Path(cls.build.name) / "probe"
        cls.setup_signal_binary = Path(cls.build.name) / "probe-setup-signal"
        if SOURCE.exists():
            result = subprocess.run(
                ["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra",
                 "-Werror", "-O2", str(SOURCE), "-o", str(cls.binary)],
                capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stderr)
            result = subprocess.run(
                ["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra",
                 "-Werror", "-O2", "-Dfsync=mt_test_signal_fsync", str(SOURCE),
                 str(REPO / "tests/native_signal_during_setup.c"),
                 "-o", str(cls.setup_signal_binary)],
                capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.assertTrue(SOURCE.exists(), "native fixture runtime is not implemented")
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-probe-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.children = []
        self.worker_pids = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        # Children are our test-owned processes only. Reap the supervisor before
        # cleaning its temporary files, including assertion-failure paths.
        for proc in self.children:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def run_probe(self, *args):
        return subprocess.run([str(self.binary), *map(str, args)],
                              capture_output=True, text=True, timeout=5)

    def start(self, stage="applied", hold=1500, deadline=3500):
        proc = subprocess.Popen(
            [str(self.binary), "probe-run", str(self.root), str(hold), str(deadline), stage],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        ready, _, _ = select.select([proc.stdout], [], [], 3)
        self.assertTrue(ready, "supervisor did not publish ready within 3 seconds")
        event = json.loads(proc.stdout.readline())
        self.assertEqual(event["event"], "ready")
        self.assertEqual(event["supervisor_pid"], proc.pid)
        worker = event["worker_pid"]
        self.worker_pids.append(worker)
        # Ready means the worker identity is persisted; stage observation proves
        # the side effect is at the requested interleaving, not just spawned.
        end = time.monotonic() + 2
        while time.monotonic() < end:
            stage_file = self.root / "stage"
            if stage_file.exists() and stage_file.read_text() == stage + "\n":
                return proc, worker
            time.sleep(0.01)
        self.fail("worker did not reach diagnostic stage")

    def finish(self, proc, operation):
        out, err = proc.communicate(timeout=4)
        self.assertEqual(proc.returncode, 0, (out, err))
        event = json.loads(out.splitlines()[-1])
        self.assertEqual(event["operation"], operation)
        self.assertEqual(event["recovery"], "restored")
        self.assertEqual((self.root / "fixture").read_bytes(), b"baseline\n")
        self.assertTrue((self.root / "journal").read_text().endswith("closed\n"))
        return event

    def test_normal_transaction_restores_real_bytes_and_closes_wal(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        self.assertEqual((self.root / "journal").read_text(),
                         setup_header().decode() + "before\nintent\napplied\nrestore_intent\nrestored\nclosed\n")

    def test_cancel_while_worker_owns_operation_lock_has_bounded_restore(self):
        proc, _ = self.start()
        proc.terminate()
        self.finish(proc, "cancelled")

    def test_deadline_reaps_worker_before_restore(self):
        proc, _ = self.start(hold=2000, deadline=300)
        self.finish(proc, "deadline")

    def test_worker_sigkill_restores_after_confirmed_reap(self):
        proc, worker = self.start()
        os.kill(worker, signal.SIGKILL)
        self.finish(proc, "worker_died")

    def test_second_transaction_and_recovery_refuse_live_supervisor(self):
        proc, _ = self.start()
        for command in ("probe-run", "probe-recover"):
            args = [command, self.root]
            if command == "probe-run":
                args += [0, 1000]
            other = self.run_probe(*args)
            self.assertEqual(other.returncode, 2, other.stdout + other.stderr)
        proc.terminate()
        self.finish(proc, "cancelled")

    def test_external_bytes_are_preserved_and_never_closed(self):
        proc, _ = self.start()
        (self.root / "fixture").write_bytes(b"external\n")
        proc.terminate()
        out, err = proc.communicate(timeout=4)
        self.assertEqual(proc.returncode, 3, (out, err))
        self.assertEqual((self.root / "fixture").read_bytes(), b"external\n")
        self.assertNotIn("closed\n", (self.root / "journal").read_text())

    def test_missing_applied_record_cannot_claim_effect_ownership(self):
        proc, worker = self.start(stage="effect")
        os.kill(worker, signal.SIGKILL)
        out, err = proc.communicate(timeout=4)
        self.assertEqual(proc.returncode, 3, (out, err))
        self.assertEqual((self.root / "fixture").read_bytes(), b"candidate\n")
        self.assertEqual((self.root / "journal").read_text(), setup_header().decode() + "before\nintent\n")

    def test_intent_without_effect_can_be_verified_and_closed(self):
        proc, worker = self.start(stage="intent")
        os.kill(worker, signal.SIGKILL)
        self.finish(proc, "worker_died")
        self.assertNotIn("applied\n", (self.root / "journal").read_text())

    def test_dead_supervisor_cannot_race_still_running_worker(self):
        proc, _ = self.start(hold=700)
        proc.kill()
        # communicate waits for orphan worker's inherited stdout too; use wait
        # here, then test lock rejection while the known worker is still alive.
        proc.wait(timeout=2)
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 2, other.stdout + other.stderr)
        time.sleep(0.9)
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertEqual((self.root / "fixture").read_bytes(), b"baseline\n")

    def test_torn_journal_blocks_instead_of_guessing_state(self):
        proc, _ = self.start(hold=200)
        proc.kill()
        proc.wait(timeout=2)
        time.sleep(0.35)
        with (self.root / "journal").open("ab") as stream:
            stream.write(b"restore_in")
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 3, other.stdout + other.stderr)
        self.assertEqual((self.root / "fixture").read_bytes(), b"candidate\n")

    def test_recovery_does_not_overwrite_post_restore_external_change(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        (self.root / "fixture").write_bytes(b"external\n")
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 3, other.stdout + other.stderr)
        self.assertEqual((self.root / "fixture").read_bytes(), b"external\n")

    def test_symlink_target_and_insecure_directory_are_rejected(self):
        outside = self.root / "outside"
        outside.write_bytes(b"keep\n")
        (self.root / "fixture").symlink_to(outside)
        result = self.run_probe("probe-run", self.root, 0, 1000)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "symlink refusal emitted no terminal event")
        self.assertEqual(json.loads(result.stdout)["operation"], "startup_failed")
        self.assertEqual(outside.read_bytes(), b"keep\n")
        separate = self.root / "insecure"
        separate.mkdir(mode=0o755)
        result = self.run_probe("probe-run", separate, 0, 1000)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    def test_unsafe_arguments_never_create_transaction(self):
        for args in (("probe-run", self.root, "-1", "1000"),
                     ("probe-run", self.root, "0", "0"),
                     ("probe-run", self.root, "0", "1000", "unknown"),
                     ("anything", self.root)):
            result = self.run_probe(*args)
            self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_replaced_inode_with_same_candidate_bytes_is_not_owned(self):
        proc, _ = self.start()
        substitute = self.root / "substitute"
        substitute.write_bytes(b"candidate\n")
        substitute.chmod(0o600)
        substitute.replace(self.root / "fixture")
        proc.terminate()
        out, err = proc.communicate(timeout=4)
        self.assertEqual(proc.returncode, 3, (out, err))
        self.assertEqual((self.root / "fixture").read_bytes(), b"candidate\n")

    def test_boot_or_namespace_mismatch_blocks_even_matching_resource(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        (self.root / "environment").write_text("probe_v1\ndifferent_boot\n")
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 3, other.stdout + other.stderr)

    def test_restored_record_after_guardian_death_can_close_without_reapplying(self):
        proc, _ = self.start(stage="restored", hold=400)
        self.assertEqual((self.root / "fixture").read_bytes(), b"baseline\n")
        proc.kill()
        proc.wait(timeout=2)
        other = self.run_probe("probe-recover", self.root)
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertEqual((self.root / "fixture").read_bytes(), b"baseline\n")
        self.assertEqual((self.root / "journal").read_text().count("restored\n"), 1)

    def test_hardlinked_fixture_is_not_overwritten(self):
        proc, _ = self.start()
        os.link(self.root / "fixture", self.root / "external-alias")
        proc.terminate()
        out, err = proc.communicate(timeout=4)
        self.assertEqual(proc.returncode, 3, (out, err))
        self.assertEqual((self.root / "external-alias").read_bytes(), b"candidate\n")

    def test_closed_recovery_is_idempotent_without_extra_journal_records(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        old = (self.root / "journal").read_bytes()
        for _ in range(2):
            other = self.run_probe("probe-recover", self.root)
            self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertEqual((self.root / "journal").read_bytes(), old)

    def test_fifo_journal_is_rejected_without_blocking_open(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        (self.root / "journal").unlink()
        os.mkfifo(self.root / "journal", mode=0o600)
        try:
            other = subprocess.run([str(self.binary), "probe-recover", str(self.root)],
                                   capture_output=True, text=True, timeout=0.5)
        except subprocess.TimeoutExpired:
            self.fail("a foreign FIFO journal blocked recovery instead of being refused")
        self.assertEqual(other.returncode, 3, other.stdout + other.stderr)

    def test_reused_directory_has_explicit_failure_result(self):
        proc, _ = self.start(hold=50, deadline=1000)
        self.finish(proc, "normal")
        other = self.run_probe("probe-run", self.root, 0, 1000)
        self.assertEqual(other.returncode, 3, other.stdout + other.stderr)
        self.assertTrue(other.stdout.strip(), "reuse refusal emitted no terminal event")
        self.assertEqual(json.loads(other.stdout), {
            "event": "result", "operation": "startup_failed",
            "recovery": "blocked", "code": 3})

    def test_signal_at_first_setup_fsync_is_caught_before_worker_start(self):
        other = subprocess.run(
            [str(self.setup_signal_binary), "probe-run", str(self.root), "0", "1000"],
            capture_output=True, text=True, timeout=3)
        self.assertEqual(other.returncode, 0, (other.stdout, other.stderr))
        self.assertEqual(json.loads(other.stdout), {
            "event": "result", "operation": "cancelled",
            "recovery": "not_applied", "code": 0})
        self.assertFalse((self.root / "process").exists(), "cancelled setup launched a worker")
        self.assertEqual((self.root / "fixture").read_bytes(), b"baseline\n")
        self.assertEqual((self.root / "journal").read_text(),
                         setup_header().decode() + "abort_intent\naborted\n")


if __name__ == "__main__":
    unittest.main()
