"""Exercise real module entrypoints with only device I/O replaced."""
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "modules/mt6639-6g"


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-control-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mod = self.root / "module"
        shutil.copytree(MODULE, self.mod)
        self.work = self.root / "work"
        self.work.mkdir()
        stub = self.root / "stub"
        stub.mkdir()
        cfg, ko, boot, mounts = (self.root / n for n in ("cfg", "ko", "boot", "mounts"))
        cfg.write_text("CountryCode=CN\nDisallowBand6G 1\n")
        ko.write_text("stock ko\n")
        boot.write_text("11111111-1111-4111-8111-111111111111\n")
        mounts.write_text("")
        helpers = {
            "id": "#!/bin/sh\necho 0\n",
            "flock": "#!/usr/bin/python3\nimport fcntl, sys\ntry: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(1)\n",
            "timeout": "#!/bin/sh\nshift\nexec \"$@\"\n",
            "sleep": "#!/bin/sh\nexit 0\n",
        }
        for name, body in helpers.items():
            path = stub / name
            path.write_text(body)
            path.chmod(0o755)
        native = self.root / "native"
        native.write_text('''#!/bin/sh
# A detached guardian must never inherit the management lock descriptor.
if [ -e /dev/fd/9 ]; then echo inherited-control-lock >&2; exit 44; fi
case "$1" in
inspect-module)
    [ "${FAKE_MODULE_READY:-1}" = 1 ] || { echo '{"module_enabled":false}'; exit 4; }
    echo '{"status":"module_control_available","read_only":true,"module_state":"enabled"}' ;;
inspect-wlan)
    count=$(cat "$READY_COUNT" 2>/dev/null || echo 0)
    count=$((count+1)); echo "$count" >"$READY_COUNT"
    [ "$count" -gt "${FAKE_READY_FAILS:-0}" ] || exit 4
    printf '{"scan":1,"wifi":%s,"read_only":true}\\n' "${FAKE_WIFI:-1}" ;;
run-wlan|run-wlan-persistent)
    printf '%s\\n' "$1" >"$RUN_VERB"
    if [ "${START_BUSY:-0}" = 1 ]; then
        [ "${START_LOCK_FILE:-0}" = 0 ] || : > "$2/guardlock"
        echo '{"status":"busy_or_invalid_lock"}'
        exit 3
    fi
    if [ "${MODULE_START_REJECT:-0}" = 1 ]; then
        echo '{"status":"module_control_unavailable"}'
        exit 4
    fi
    if [ "${PREPARE_FAIL:-0}" = 1 ]; then
        echo '{"event":"terminal","restoration":"no_mount_effects"}' >"$2/guardian.log"
        echo '{"status":"failed","guardian_pid":999999}'
        exit 4
    fi
    printf '%s\\n' '{"event":"terminal","restoration":"restored"}' >"$2/guardian.log"
    echo '{"status":"owned","guardian_pid":999999}' ;;
recover)
    if [ "${RECOVER_BUSY_FAILS:-0}" != 0 ]; then
        count=$(cat "$RECOVER_COUNT" 2>/dev/null || echo 0)
        count=$((count+1))
        echo "$count" >"$RECOVER_COUNT"
        if [ "$count" -le "$RECOVER_BUSY_FAILS" ]; then
            echo '{"status":"busy_or_invalid_lock"}'
            exit 3
        fi
    fi
    [ "${RECOVER_FAIL:-0}" = 0 ] || exit 7
    echo '{"status":"idle","restoration":"restored"}' ;;
*) exit 64 ;;
esac
''')
        native.chmod(0o755)
        self.env = dict(os.environ, MT6639_TEST_MODE="1", WORK=str(self.work),
                        CFG=str(cfg), STOCK_KO=str(ko),
                        STOCK_CFG_SHA=hashlib.sha256(cfg.read_bytes()).hexdigest(),
                        STOCK_KO_SHA=hashlib.sha256(ko.read_bytes()).hexdigest(),
                        BOOT_ID_FILE=str(boot), MOUNTINFO_FILE=str(mounts),
                        INIT_MOUNTINFO_FILE=str(mounts), INIT_CFG_PATH=str(cfg),
                        NATIVE=str(native), READY_COUNT=str(self.root / "ready-count"),
                        RECOVER_COUNT=str(self.root / "recover-count"),
                        RUN_VERB=str(self.root / "run-verb"),
                        PATH=f"{stub}:{os.environ['PATH']}")
        self.env.pop("ENABLE_6G", None)
        self.env.pop("RUN_MODE", None)

    def run_entry(self, name, *args, **env):
        return subprocess.run(["sh", str(self.mod / name), *args],
                              env=dict(self.env, **env), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=20)

    def control(self, command, **env):
        return self.run_entry("bin/control.sh", str(self.mod), command, **env)

    def test_service_starts_default_transaction_and_releases_lock(self):
        result = self.run_entry("service.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())
        with (self.work / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_explicit_off_service_is_idle(self):
        result = self.run_entry("service.sh", ENABLE_6G="0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_service_waits_for_transient_binder_unavailability(self):
        result = self.run_entry("service.sh", FAKE_READY_FAILS="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())

    def test_service_readiness_wait_is_bounded(self):
        result = self.run_entry("service.sh", FAKE_READY_FAILS="99")
        self.assertEqual(result.returncode, 69, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())
        self.assertEqual(Path(self.env["READY_COUNT"]).read_text().strip(), "6")

    def test_service_respects_wifi_off(self):
        result = self.run_entry("service.sh", FAKE_WIFI="0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_disabled_module_rejects_enable(self):
        (self.mod / "disable").touch()
        result = self.control("enable")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_action_restores_existing_transaction(self):
        first = self.run_entry("action.sh")
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())
        second = self.run_entry("action.sh")
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")
        self.assertEqual((self.work / "state").read_text().strip(), "restored")

    def test_uninstall_restores_even_with_remove_marker(self):
        self.assertEqual(self.control("enable").returncode, 0)
        (self.mod / "remove").touch()
        self.assertEqual(self.control("enable").returncode, 3)
        result = self.run_entry("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")

    def test_busy_control_does_not_start_transaction(self):
        with (self.work / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.control("enable")
        self.assertEqual(result.returncode, 75, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_legacy_apply_obeys_management_lock(self):
        with (self.work / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_entry("bin/apply.sh", str(self.mod))
        self.assertEqual(result.returncode, 75, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_legacy_restore_obeys_management_lock(self):
        with (self.work / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_entry("bin/restore.sh", str(self.mod))
        self.assertEqual(result.returncode, 75, result.stdout)

    def test_legacy_native_apply_respects_explicit_off(self):
        result = self.run_entry("bin/apply.sh", str(self.mod), ENABLE_6G="0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_foreign_authority_is_preserved(self):
        authority = self.work / "active_tx"
        authority.write_text("/foreign/transaction\n")
        for op in ("enable", "disable", "toggle"):
            result = self.control(op)
            self.assertNotEqual(result.returncode, 0, (op, result.stdout))
            self.assertEqual(authority.read_text(), "/foreign/transaction\n")

    def test_failed_restore_keeps_recovery_authority(self):
        self.assertEqual(self.control("enable").returncode, 0)
        result = self.control("disable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 90, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())

    def test_wifi_off_never_creates_authority(self):
        result = self.control("enable", FAKE_WIFI="0")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertFalse((self.work / "active_tx").exists())

    def test_proven_unstarted_transaction_can_close(self):
        self.assertEqual(self.control("enable", PREPARE_FAIL="1").returncode, 3)
        self.assertTrue((self.work / "active_tx").read_text().strip())
        result = self.control("disable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")

    def test_parent_lock_refusal_without_effects_can_close(self):
        self.assertEqual(self.control("enable", START_BUSY="1").returncode, 3)
        result = self.control("disable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")

    def test_lock_file_keeps_ambiguous_lock_refusal_blocked(self):
        self.assertEqual(self.control("enable", START_BUSY="1", START_LOCK_FILE="1").returncode, 3)
        result = self.control("disable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 90, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())

    def test_dead_guardian_without_terminal_uses_native_recovery(self):
        self.assertEqual(self.control("enable").returncode, 0)
        tx = Path((self.work / "active_tx").read_text().strip())
        (tx / "guardian.log").write_text("")
        result = self.control("disable")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")

    def test_dead_guardian_native_lock_release_is_retried(self):
        self.assertEqual(self.control("enable").returncode, 0)
        tx = Path((self.work / "active_tx").read_text().strip())
        (tx / "guardian.log").write_text("")
        result = self.control("disable", RECOVER_BUSY_FAILS="2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")
        self.assertEqual((self.work / "state").read_text().strip(), "restored")
        self.assertEqual(Path(self.env["RECOVER_COUNT"]).read_text().strip(), "3")

    def test_lock_retry_exhaustion_retains_authority(self):
        self.assertEqual(self.control("enable").returncode, 0)
        tx = Path((self.work / "active_tx").read_text().strip())
        (tx / "guardian.log").write_text("")
        result = self.control("disable", RECOVER_BUSY_FAILS="99")
        self.assertEqual(result.returncode, 90, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())
        self.assertEqual(Path(self.env["RECOVER_COUNT"]).read_text().strip(), "16")

    def test_crossboot_preserves_authority(self):
        self.assertEqual(self.control("enable").returncode, 0)
        Path(self.env["BOOT_ID_FILE"]).write_text("new-boot\n")
        result = self.control("disable")
        self.assertEqual(result.returncode, 90, result.stdout)
        self.assertTrue((self.work / "active_tx").read_text().strip())

    def old_transaction(self):
        self.assertEqual(self.control("enable").returncode, 0)
        authority = self.work / "active_tx"
        old_tx = authority.read_text()
        Path(self.env["BOOT_ID_FILE"]).write_text("22222222-2222-4222-8222-222222222222\n")
        return authority, old_tx

    def test_default_mode_is_persistent(self):
        result = self.control("enable")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(Path(self.env["RUN_VERB"]).read_text().strip(), "run-wlan-persistent")

    def test_bounded_mode_remains_explicit(self):
        result = self.control("enable", RUN_MODE="bounded")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(Path(self.env["RUN_VERB"]).read_text().strip(), "run-wlan")

    def test_invalid_mode_rejected_before_authority(self):
        result = self.control("enable", RUN_MODE="typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "active_tx").exists())

    def test_expired_sameboot_transaction_can_restart(self):
        self.assertEqual(self.control("enable").returncode, 0)
        old_tx = Path((self.work / "active_tx").read_text().strip())
        # Remove timestamp collision without removing the old transaction.
        moved = old_tx.with_name(old_tx.name + "-expired")
        old_tx.rename(moved)
        (self.work / "active_tx").write_text(str(moved) + "\n")
        result = self.control("enable")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(moved.exists())
        self.assertNotEqual((self.work / "active_tx").read_text().strip(), str(moved))

    def test_crossboot_stock_reconciles_and_archives_without_recover(self):
        authority, old_tx = self.old_transaction()
        result = self.control("enable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotEqual(authority.read_text(), old_tx)
        archives = list((self.work / "retired").glob("*/active_tx"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(), old_tx)
        self.assertTrue(Path(old_tx.strip()).exists())
        self.assertFalse((Path(old_tx.strip()) / "restore-wrapper.log").exists())

    def test_crossboot_rejects_invalid_boot_record(self):
        authority, old_tx = self.old_transaction()
        (Path(old_tx.strip()) / "wrapper-boot").write_text("not-a-uuid\n")
        self.assertNotEqual(self.control("enable").returncode, 0)
        self.assertEqual(authority.read_text(), old_tx)

    def test_crossboot_rejects_symlink_boot_record(self):
        authority, old_tx = self.old_transaction()
        record = Path(old_tx.strip()) / "wrapper-boot"
        saved = record.with_name("boot-saved")
        record.rename(saved)
        record.symlink_to(saved)
        self.assertNotEqual(self.control("enable").returncode, 0)
        self.assertEqual(authority.read_text(), old_tx)

    def test_crossboot_rejects_init_namespace_candidate(self):
        authority, old_tx = self.old_transaction()
        candidate = self.root / "init-cfg"
        candidate.write_text("not-stock\n")
        result = self.control("enable", INIT_CFG_PATH=str(candidate))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(authority.read_text(), old_tx)

    def test_crossboot_rejects_init_target_mount(self):
        authority, old_tx = self.old_transaction()
        mounts = self.root / "init-mounts"
        mounts.write_text(f"1 2 0:1 / {self.env['CFG']} ro - ext4 x ro\n")
        self.assertNotEqual(self.control("enable", INIT_MOUNTINFO_FILE=str(mounts)).returncode, 0)
        self.assertEqual(authority.read_text(), old_tx)

    def test_crossboot_mountinfo_read_error_preserves_authority(self):
        authority, old_tx = self.old_transaction()
        result = self.control("enable", INIT_MOUNTINFO_FILE=str(self.root / "missing"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(authority.read_text(), old_tx)

    def test_unreadable_mountinfo_rejects_new_apply(self):
        result = self.control("enable", MOUNTINFO_FILE=str(self.root / "missing"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "active_tx").exists())

    def test_symlink_pid_authority_preserved(self):
        victim = self.root / "unrelated"
        victim.write_text("must-not-change\n")
        (self.work / "active_pid").symlink_to(victim)
        self.assertNotEqual(self.control("enable").returncode, 0)
        self.assertEqual(victim.read_text(), "must-not-change\n")

    def test_symlink_transaction_root_rejected_before_writing(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        (self.work / "transactions").symlink_to(outside)
        result = self.control("enable")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.work / "active_tx").exists())

    def test_module_preflight_rejection_never_creates_authority(self):
        result = self.control("enable", FAKE_MODULE_READY="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "active_tx").exists())

    def test_module_start_rejection_can_close_without_recovery(self):
        result = self.control("enable", MODULE_START_REJECT="1")
        self.assertEqual(result.returncode, 3, result.stdout)
        tx = Path((self.work / "active_tx").read_text().strip())
        result = self.control("disable", RECOVER_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.work / "active_tx").read_text(), "")
        self.assertTrue(tx.exists())

    def test_module_start_rejection_still_checks_init_stock(self):
        self.assertEqual(self.control("enable", MODULE_START_REJECT="1").returncode, 3)
        previous = (self.work / "active_tx").read_text()
        candidate = self.root / "init-cfg"
        candidate.write_text("not-stock\n")
        self.assertEqual(self.control("disable", INIT_CFG_PATH=str(candidate)).returncode, 90)
        self.assertEqual((self.work / "active_tx").read_text(), previous)


if __name__ == "__main__":
    unittest.main()
