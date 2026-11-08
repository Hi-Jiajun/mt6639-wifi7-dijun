"""Behavior tests for the 6GHz soft-reboot watchdog (system_server restart)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "modules/mt6639-6g"


class SoftRebootWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-softreboot-watch-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
        self.mod = root / "module"
        shutil.copytree(MODULE, self.mod)
        self.work = root / "work"
        self.work.mkdir()
        self.stub = root / "stub"
        self.stub.mkdir()
        self.cfg = root / "wifi.cfg"
        self.ko = root / "wlan.ko"
        self.boot = root / "boot"
        self.boot_id = "11111111-1111-4111-8111-111111111111"
        self.boot.write_text(self.boot_id + "\n")
        self.ko.write_text("stock ko\n")
        self.cfg.write_text("CountryCode=CN\nDisallowBand6G 0\n")
        self.calls = root / "calls"
        self.calls.write_text("")
        self.ss = root / "system-server-pid"
        self.ss.write_text("500\n")
        self._install_stubs()

    def _write_stub(self, name: str, body: str):
        path = self.stub / name
        path.write_text(body)
        path.chmod(0o755)

    def _install_stubs(self):
        self._write_stub(
            "flock",
            "#!/usr/bin/python3\n"
            "import fcntl, sys\n"
            "fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
        )
        self._write_stub("timeout", "#!/bin/sh\nshift\nexec \"$@\"\n")
        self._write_stub("sleep", "#!/bin/sh\nexit 0\n")
        self._write_stub("runcon", "#!/bin/sh\nshift\nexec \"$@\"\n")
        self._write_stub(
            "settings",
            "#!/bin/sh\n"
            "echo \"settings $*\" >> \"$MT6639_TEST_CALLS\"\n"
            "echo \"${MT6639_TEST_SETTINGS_WIFI_ON:-1}\"\n"
            "exit 0\n",
        )
        self._write_stub(
            "iw",
            "#!/bin/sh\n"
            "echo \"iw $*\" >> \"$MT6639_TEST_CALLS\"\n"
            "if [ \"${MT6639_TEST_6135:-present}\" = present ]; then\n"
            "  echo '    * 6135.0 MHz [37]'\n"
            "else\n"
            "  echo '    * 6135.0 MHz [37] (disabled)'\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_stub(
            "cmd",
            "#!/bin/sh\n"
            "[ \"$1\" = wifi ] || exit 3\n"
            "shift\n"
            "echo \"cmd wifi $*\" >> \"$MT6639_TEST_CALLS\"\n"
            "if [ \"$*\" = status ]; then\n"
            "  echo \"${MT6639_TEST_WIFI_STATUS:-Wifi is enabled}\"\n"
            "fi\n"
            "exit 0\n",
        )

    def _state(self, boot: str, ss: str, attempts: int):
        watch = self.work / "softreboot-watch"
        watch.mkdir(exist_ok=True)
        (watch / "state").write_text(
            f"boot={boot}\nss={ss}\nattempts={attempts}\n")

    def _run(self, *, iterations: int = 1, extra_env: dict | None = None):
        env = dict(
            os.environ,
            MT6639_TEST_MODE="1",
            MT6639_TEST_CALLS=str(self.calls),
            MT6639_WATCH_ITER=str(iterations),
            MT6639_WATCH_INTERVAL="0",
            MT6639_WATCH_CHECK_INTERVAL="0",
            MT6639_TEST_REFRESH_CMD=f"echo refresh >> '{self.calls}'",
            SYSTEM_SERVER_PID_FILE=str(self.ss),
            WORK=str(self.work),
            CFG=str(self.cfg),
            STOCK_KO=str(self.ko),
            STOCK_CFG_SHA=hashlib.sha256(self.cfg.read_bytes()).hexdigest(),
            STOCK_KO_SHA=hashlib.sha256(self.ko.read_bytes()).hexdigest(),
            BOOT_ID_FILE=str(self.boot),
            PATH=f"{self.stub}:{os.environ['PATH']}",
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["/bin/sh", str(self.mod / "bin/softreboot-watch.sh"),
             str(self.mod), "run"],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def _log(self) -> str:
        return (self.work / "softreboot-watch/watch.log").read_text()

    def _refreshes(self) -> int:
        return self.calls.read_text().count("refresh")

    def test_first_observation_records_baseline(self):
        self._run()
        self.assertEqual(self._refreshes(), 0)
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn(f"boot={self.boot_id}", state)
        self.assertIn("ss=500", state)
        self.assertIn("attempts=0", state)

    def test_soft_reboot_with_missing_6135_refreshes_once(self):
        self._state(self.boot_id, "400", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent"})
        self.assertEqual(self._refreshes(), 1)
        log = self._log()
        self.assertIn("soft_reboot old_ss=400 new_ss=500", log)
        self.assertIn("refresh rc=0 attempt=1/2", log)
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn("ss=500", state)
        # A successful refresh clears the budget so a later soft reboot in the
        # same kernel boot (LSPosed recovery also restarts zygote) still works.
        self.assertIn("attempts=0", state)

    def test_a_failed_refresh_keeps_the_attempt(self):
        self._state(self.boot_id, "400", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent",
                             "MT6639_TEST_REFRESH_CMD": "exit 5"})
        self.assertIn("refresh rc=5 attempt=1/2", self._log())
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn("attempts=1", state)

    def test_soft_reboot_with_registered_6135_does_nothing(self):
        self._state(self.boot_id, "400", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "present"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("target_6135_registered", self._log())

    def test_periodic_pass_repairs_a_silent_loss_without_a_soft_reboot(self):
        # system_server is unchanged here: only the integrity pass can notice it.
        self._state(self.boot_id, "500", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent",
                             "MT6639_WATCH_CHECK_INTERVAL": "1"})
        self.assertEqual(self._refreshes(), 1)
        self.assertIn("periodic: refresh rc=0", self._log())
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn("attempts=0", state)

    def test_periodic_pass_stays_quiet_when_6135_is_registered(self):
        self._state(self.boot_id, "500", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "present",
                             "MT6639_WATCH_CHECK_INTERVAL": "1"})
        self.assertEqual(self._refreshes(), 0)

    def test_refresh_never_runs_when_wifi_is_off(self):
        self._state(self.boot_id, "400", 0)
        (self.work / "state").write_text("active\n")
        self._run(
            extra_env={"MT6639_TEST_6135": "absent",
                       "MT6639_TEST_SETTINGS_WIFI_ON": "0"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("wifi_off", self._log())
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn("attempts=0", state)

    def test_refresh_skipped_without_an_active_transaction(self):
        self._state(self.boot_id, "400", 0)
        self._run(extra_env={"MT6639_TEST_6135": "absent"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("no_active_transaction", self._log())

    def test_attempts_are_bounded(self):
        self._state(self.boot_id, "400", 2)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("suppressed attempts=2", self._log())

    def test_waits_for_the_wifi_service_instead_of_burning_an_attempt(self):
        self._state(self.boot_id, "400", 0)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent",
                             "MT6639_TEST_WIFI_STATUS": "Wifi is disabled"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("wifi_service_not_ready", self._log())
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn("attempts=0", state)

    def test_cold_boot_resets_attempts_instead_of_refreshing(self):
        self._state("22222222-2222-4222-8222-222222222222", "400", 2)
        (self.work / "state").write_text("active\n")
        self._run(extra_env={"MT6639_TEST_6135": "absent"})
        self.assertEqual(self._refreshes(), 0)
        self.assertIn("boot_changed", self._log())
        state = (self.work / "softreboot-watch/state").read_text()
        self.assertIn(f"boot={self.boot_id}", state)
        self.assertIn("attempts=0", state)


if __name__ == "__main__":
    unittest.main()
