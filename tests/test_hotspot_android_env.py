"""The KernelSU entry points start with only PATH; app_process needs more."""
from pathlib import Path
import subprocess
import unittest


REPO = Path(__file__).resolve().parents[1]
ENV_HELPER = REPO / "modules/mt6639-hotspot/bin/android-env.sh"
CONTROL = REPO / "modules/mt6639-hotspot/bin/control.sh"
WORKER = REPO / "modules/mt6639-hotspot/bin/request_worker.sh"
RUNNER = REPO / "tools/softap_native_once.sh"
BUILD = REPO / "tools/build_hotspot_module.sh"


class AndroidEnvHelperTests(unittest.TestCase):
    def test_helper_is_valid_shell(self):
        self.assertTrue(ENV_HELPER.is_file())
        result = subprocess.run(["sh", "-n", str(ENV_HELPER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_values_come_from_system_server_when_absent(self):
        helper = ENV_HELPER.read_text(encoding="utf-8")
        self.assertIn("pidof system_server", helper)
        self.assertIn('/proc/$pid/environ', helper)
        # never override an inherited value
        self.assertIn('eval "current=\\${$name:-}"', helper)
        self.assertIn('if [ -z "$current" ]; then', helper)
        for name in ("ANDROID_DATA", "ANDROID_ROOT", "BOOTCLASSPATH",
                     "DEX2OATBOOTCLASSPATH", "ANDROID_ART_ROOT"):
            with self.subTest(name=name):
                self.assertIn(f"android_env_export {name}", helper)

    def test_readiness_gate_requires_bootclasspath(self):
        helper = ENV_HELPER.read_text(encoding="utf-8")
        self.assertIn("android_env_ready() {", helper)
        self.assertIn('[ -n "${BOOTCLASSPATH:-}" ] && [ -n "${ANDROID_DATA:-}" ]', helper)


class EntryPointTests(unittest.TestCase):
    def test_control_and_worker_pull_in_the_helper(self):
        control = CONTROL.read_text(encoding="utf-8")
        worker = WORKER.read_text(encoding="utf-8")
        self.assertIn('. "$MODDIR/bin/android-env.sh"', control)
        self.assertIn('. "$MODDIR/bin/android-env.sh"', worker)

    def test_start_and_c02_fail_closed_without_the_environment(self):
        control = CONTROL.read_text(encoding="utf-8")
        self.assertEqual(control.count("android_env_ready ||"), 2, control)
        self.assertIn("REJECT android_env_unavailable", control)
        start = control[control.index("start_session() {"):control.index("run_c02_24g() {")]
        # inside start_session the gate must precede any state publication or
        # staging, otherwise a broken environment would leave a half session
        self.assertIn("REJECT android_env_unavailable", start)
        self.assertLess(start.index("REJECT android_env_unavailable"),
                        start.index('publish_status starting'))
        self.assertLess(start.index("REJECT android_env_unavailable"),
                        start.index("mktemp -d"))
        c02 = control[control.index("run_c02_24g() {"):]
        self.assertLess(c02.index("REJECT android_env_unavailable"),
                        c02.index("softap_24g_once.sh"))

    def test_runner_always_resources_the_module_helper(self):
        # A long-lived worker survives a soft reboot; the runner must refresh
        # the Android runtime env instead of trusting an inherited one.
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("/data/adb/modules/mt6639-hotspot/bin/android-env.sh", runner)
        self.assertNotIn('[ -z "${BOOTCLASSPATH:-}" ]', runner)

    def test_helper_refreshes_when_system_server_changed(self):
        helper = ENV_HELPER.read_text(encoding="utf-8")
        self.assertIn("MT6639_RUNTIME_SERVER", helper)
        self.assertIn('unset BOOTCLASSPATH DEX2OATBOOTCLASSPATH ANDROID_DATA', helper)
        self.assertLess(helper.index("MT6639_RUNTIME_SERVER"),
                        helper.index("android_env_export ANDROID_DATA"))

    def test_runner_names_empty_app_process_output(self):
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("framework_inspect_empty", runner)
        self.assertIn("framework_ap_state_not_idle", runner)
        self.assertIn("framework_inspect_failed", runner)
        # the empty-output check must precede the ap_state assertion
        self.assertLess(runner.index("framework_inspect_empty"),
                        runner.index("framework_ap_state_not_idle"))

    def test_build_ships_the_helper_executable(self):
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn('"$TMP/module/bin/android-env.sh"', build)


if __name__ == "__main__":
    unittest.main()
