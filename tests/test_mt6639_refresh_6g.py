"""Behavior tests for bounded 6GHz refresh after a userspace rebuild."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "modules/mt6639-6g"


class Refresh6gTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-refresh-6g-")
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
        self.mounts = root / "mountinfo"
        self.boot_id = "11111111-1111-4111-8111-111111111111"
        self.boot.write_text(self.boot_id + "\n")
        self.ko.write_text("stock ko\n")
        self.cfg.write_text("CountryCode=CN\nDisallowBand6G 0\n")
        self.mounts.write_text("")
        self.calls = root / "calls"
        self.sixg_enabled = root / "sixg-enabled"
        self.native_status = root / "native-status"
        self.native_ready = root / "native-ready"
        self.country_file = root / "country"
        self.country_file.write_text("CN\n")
        self._install_stubs()

    def _write_stub(self, name: str, body: str):
        path = self.stub / name
        path.write_text(body)
        path.chmod(0o755)

    def _install_stubs(self):
        self._write_stub(
            "id",
            "#!/bin/sh\n"
            "[ \"$1\" = -u ] && { echo 0; exit 0; }\n"
            "[ \"$1\" = -Z ] && { echo \"${MT6639_TEST_ID_CONTEXT:-u:r:ksu:s0}\"; exit 0; }\n"
            "exec /usr/bin/id \"$@\"\n",
        )
        self._write_stub(
            "getenforce",
            "#!/bin/sh\necho \"${MT6639_TEST_ENFORCE:-Enforcing}\"\n",
        )
        self._write_stub(
            "flock",
            "#!/usr/bin/python3\n"
            "import fcntl, sys\n"
            "fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
        )
        self._write_stub("timeout", "#!/bin/sh\nshift\nexec \"$@\"\n")
        self._write_stub(
            "sleep",
            "#!/bin/sh\n"
            "if [ -n \"${MT6639_TEST_SIGNAL_ON_SLEEP:-}\" ]; then\n"
            "  kill -TERM \"$PPID\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_stub("runcon", "#!/bin/sh\nshift\nexec \"$@\"\n")
        self._write_stub(
            "settings",
            "#!/bin/sh\n"
            "echo \"settings $*\" >> \"$MT6639_TEST_CALLS\"\n"
            "if [ \"$1\" = get ]; then\n"
            "  case \"$3\" in wifi_scan_always_enabled) echo 1 ;; wifi_on) echo 1 ;; "
            "*) exit 3 ;; esac\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_stub(
            "cmd",
            "#!/bin/sh\n"
            "[ \"$1\" = wifi ] || exit 3\n"
            "shift\n"
            "echo \"wifi $*\" >> \"$MT6639_TEST_CALLS\"\n"
            "if [ \"$*\" = 'force-country-code enabled US' ]; then echo US > \"$MT6639_TEST_COUNTRY_FILE\"; fi\n"
            "if [ \"$*\" = 'force-country-code enabled CN' ]; then echo CN > \"$MT6639_TEST_COUNTRY_FILE\"; fi\n"
            "if [ \"$*\" = 'force-country-code disabled' ]; then echo CN > \"$MT6639_TEST_COUNTRY_FILE\"; fi\n"
            "if [ \"$*\" = 'get-country-code' ]; then echo \"Wifi Country Code = $(cat \"$MT6639_TEST_COUNTRY_FILE\")\"; fi\n"
            "if [ \"${MT6639_TEST_FAIL_WIFI:-}\" = \"$*\" ]; then\n"
            "  if [ -n \"${MT6639_TEST_FAIL_ONCE:-}\" ]; then\n"
            "    [ -e \"$MT6639_TEST_FAIL_ONCE\" ] || { : > \"$MT6639_TEST_FAIL_ONCE\"; exit 7; }\n"
            "  else\n"
            "    exit 7\n"
            "  fi\n"
            "fi\n"
            "if [ \"$*\" = \"set-wifi-enabled enabled\" ]; then\n"
            "  : > \"$MT6639_TEST_SIXG\"\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_stub(
            "iw",
            "#!/bin/sh\n"
            "show=0\n"
            "if [ \"${MT6639_TEST_OTHER_6G:-0}\" = 1 ]; then\n"
            "  echo '    * 5955.0 MHz [1]'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${MT6639_TEST_DISABLED_ONLY:-0}\" = 1 ]; then\n"
            "  echo '    * 6135.0 MHz [37] (disabled)'\n"
            "  exit 0\n"
            "fi\n"
            "if [ -n \"${MT6639_TEST_SIXG_AFTER:-}\" ]; then\n"
            "  count=$(cat \"$MT6639_TEST_IW_COUNT\" 2>/dev/null || echo 0)\n"
            "  count=$((count+1))\n"
            "  echo \"$count\" > \"$MT6639_TEST_IW_COUNT\"\n"
            "  [ \"$count\" -lt \"$MT6639_TEST_SIXG_AFTER\" ] || show=1\n"
            "elif [ -e \"$MT6639_TEST_SIXG\" ]; then show=1\n"
            "fi\n"
            "if [ \"$show\" = 1 ]; then\n"
            "  echo '    * 6135.0 MHz [37]'\n"
            "fi\n",
        )

    def _activate_transaction(self):
        tx = self.work / "transactions/t-<BOOT_ID>-<TS>-XXXXXX"
        tx.mkdir(parents=True)
        candidate = tx / "candidate"
        candidate.write_bytes(self.cfg.read_bytes())
        (tx / "wrapper-boot").write_text(self.boot_id + "\n")
        (tx / "guardian.log").write_text(
            '{"event":"persistent-hold","guardian_pid":4242}\n')
        (self.work / "active_tx").write_text(str(tx) + "\n")
        (self.work / "active_pid").write_text("4242\n")
        (self.work / "state").write_text("active\n")
        self.mounts.write_text(
            f"999 1 0:1 {candidate} {self.cfg} rw - x y rw\n")
        self.native_status.write_text(
            f"boot={self.boot_id}\n"
            "enabled_config=1\n"
            "state=active\n"
            f"active_tx={tx}\n"
            "guardian_pid=4242\n"
            "guardian=alive\n")
        self.native_ready.write_text(
            '{"scan":1,"wifi":1,"country_override":0,'
            '"override_code":"","country_code":"CN","read_only":true}\n')

    def control(self, operation: str, **extra_env):
        env = dict(
            os.environ,
            MT6639_TEST_MODE="1",
            MT6639_TEST_CALLS=str(self.calls),
            MT6639_TEST_SIXG=str(self.sixg_enabled),
            MT6639_TEST_STATUS_FILE=str(self.native_status),
            MT6639_TEST_READY_FILE=str(self.native_ready),
            MT6639_TEST_COUNTRY_FILE=str(self.country_file),
            WORK=str(self.work),
            CFG=str(self.cfg),
            STOCK_KO=str(self.ko),
            STOCK_CFG_SHA=hashlib.sha256(self.cfg.read_bytes()).hexdigest(),
            STOCK_KO_SHA=hashlib.sha256(self.ko.read_bytes()).hexdigest(),
            BOOT_ID_FILE=str(self.boot),
            MOUNTINFO_FILE=str(self.mounts),
            INIT_MOUNTINFO_FILE=str(self.mounts),
            INIT_CFG_PATH=str(self.cfg),
            NATIVE=str(self.root / "unused-native"),
            PATH=f"{self.stub}:{os.environ['PATH']}",
        )
        env.update(extra_env)
        return subprocess.run(
            ["sh", str(self.mod / "bin/control.sh"), str(self.mod), operation],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )

    def mutation_calls(self):
        if not self.calls.exists():
            return []
        return [
            line for line in self.calls.read_text().splitlines()
            if line.startswith("wifi set-")
            or line.startswith("wifi force-country-code")
            or line.startswith("settings put ")
        ]

    def test_missing_sixg_triggers_one_bounded_wifi_rebuild(self):
        self._activate_transaction()
        result = self.control("refresh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            self.mutation_calls(),
            [
                "wifi set-scan-always-available disabled",
                "wifi force-country-code enabled US",
                "wifi set-wifi-enabled disabled",
                "wifi set-wifi-enabled enabled",
                "settings put global wifi_scan_always_enabled 1",
            ],
        )
        self.assertIn("RESULT refresh=registered", result.stdout)

    def test_failed_enable_restores_scan_and_keeps_transaction_active(self):
        self._activate_transaction()
        result = self.control(
            "refresh", MT6639_TEST_FAIL_WIFI="set-wifi-enabled enabled")
        self.assertEqual(result.returncode, 90, result.stdout)
        self.assertEqual(
            self.mutation_calls(),
            [
                "wifi set-scan-always-available disabled",
                "wifi force-country-code enabled US",
                "wifi set-wifi-enabled disabled",
                "wifi set-wifi-enabled enabled",
                "wifi set-wifi-enabled enabled",
                "wifi force-country-code disabled",
                "settings put global wifi_scan_always_enabled 1",
            ],
        )
        self.assertEqual((self.work / "state").read_text().strip(), "active")

    def test_transient_enable_failure_is_retried_once(self):
        self._activate_transaction()
        result = self.control(
            "refresh",
            MT6639_TEST_FAIL_WIFI="set-wifi-enabled enabled",
            MT6639_TEST_FAIL_ONCE=str(self.root / "enable-failed-once"),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            self.mutation_calls(),
            [
                "wifi set-scan-always-available disabled",
                "wifi force-country-code enabled US",
                "wifi set-wifi-enabled disabled",
                "wifi set-wifi-enabled enabled",
                "wifi set-wifi-enabled enabled",
                "settings put global wifi_scan_always_enabled 1",
            ],
        )
        self.assertIn("RESULT refresh=registered", result.stdout)

    def test_existing_sixg_is_read_only(self):
        self._activate_transaction()
        self.sixg_enabled.touch()
        result = self.control("refresh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RESULT refresh=already_registered", result.stdout)
        self.assertEqual(self.mutation_calls(), [])

    def test_disabled_sixg_channels_do_not_count_as_registered(self):
        self._activate_transaction()
        result = self.control("refresh", MT6639_TEST_DISABLED_ONLY="1")
        self.assertEqual(result.returncode, 4, result.stdout)
        self.assertIn("rebuilding Wi-Fi target_6135=0", result.stdout)
        self.assertIn("RESULT refresh=not_registered", result.stdout)
        self.assertTrue(self.mutation_calls())

    def test_other_enabled_6g_channel_does_not_satisfy_6135_target(self):
        self._activate_transaction()
        result = self.control("refresh", MT6639_TEST_OTHER_6G="1")
        self.assertEqual(result.returncode, 4, result.stdout)
        self.assertIn("target_6135=0", result.stdout)
        self.assertIn("RESULT refresh=not_registered", result.stdout)
        self.assertTrue(self.mutation_calls())

    def test_refresh_reapplies_us_country_before_wifi_cycle(self):
        self._activate_transaction()
        result = self.control("refresh")
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.mutation_calls()
        self.assertLess(
            calls.index("wifi force-country-code enabled US"),
            calls.index("wifi set-wifi-enabled disabled"))
        self.assertEqual(self.country_file.read_text().strip(), "US")

    def test_refresh_failure_restores_original_country_override(self):
        self._activate_transaction()
        result = self.control("refresh", MT6639_TEST_OTHER_6G="1")
        self.assertEqual(result.returncode, 4, result.stdout)
        calls = self.mutation_calls()
        self.assertIn("wifi force-country-code enabled US", calls)
        self.assertEqual(calls[-1], "wifi force-country-code disabled")
        self.assertEqual(self.country_file.read_text().strip(), "CN")

    def test_refresh_rejects_kernel_domain_without_wireless_changes(self):
        self._activate_transaction()
        result = self.control("refresh", MT6639_TEST_ID_CONTEXT="u:r:kernel:s0")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("ksu_context_required", result.stdout)
        self.assertEqual(self.mutation_calls(), [])

    def test_refresh_rejects_permissive_without_wireless_changes(self):
        self._activate_transaction()
        result = self.control("refresh", MT6639_TEST_ENFORCE="Permissive")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("enforcing_required", result.stdout)
        self.assertEqual(self.mutation_calls(), [])

    def test_cross_boot_transaction_is_refused_without_wifi_changes(self):
        self._activate_transaction()
        (self.work / "transactions/t-<BOOT_ID>-<TS>-XXXXXX/wrapper-boot").write_text(
            "22222222-2222-4222-8222-222222222222\n")
        result = self.control("refresh")
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("transaction_boot_mismatch", result.stdout)
        self.assertEqual(self.mutation_calls(), [])

    def test_sixg_registration_waits_for_bounded_driver_latency(self):
        self._activate_transaction()
        result = self.control(
            "refresh",
            MT6639_TEST_SIXG_AFTER="4",
            MT6639_TEST_IW_COUNT=str(self.root / "iw-count"),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RESULT refresh=registered", result.stdout)
        self.assertEqual(
            (self.root / "iw-count").read_text().strip(),
            "4",
        )

    def test_termination_after_wifi_off_restores_radio_and_scan(self):
        self._activate_transaction()
        result = self.control(
            "refresh", MT6639_TEST_SIGNAL_ON_SLEEP="1")
        self.assertEqual(result.returncode, 143, result.stdout)
        self.assertEqual(
            self.mutation_calls(),
            [
                "wifi set-scan-always-available disabled",
                "wifi force-country-code enabled US",
                "wifi set-wifi-enabled disabled",
                "wifi set-wifi-enabled enabled",
                "wifi force-country-code disabled",
                "settings put global wifi_scan_always_enabled 1",
            ],
        )
        self.assertEqual((self.work / "state").read_text().strip(), "active")


if __name__ == "__main__":
    unittest.main()
