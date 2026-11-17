"""Static and parser checks for the bounded dual-STA LSPosed gate."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest


import pubenv

REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "modules/mt6639-concurrency-lsposed"
APK = REPO / "outputs/concurrency-gate-build-20261026-1/module.apk"
GATE = REPO / "tools/dualsta_gate.sh"
RUNNER = REPO / "tools/sta_multiinternet_once.sh"


def extract_function(path, name):
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"{name}() {{")
    depth = 0
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line.endswith("() {") or line == f"{name}() {{":
            depth += 1
        elif line == "}":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start:i + 1])
    raise AssertionError(f"could not extract {name}()")


class DualStaGateModuleTests(unittest.TestCase):
    @unittest.skipUnless(pubenv.JAVAC and pubenv.ANDROID_JAR,
                         "javac and an Android platform jar are required")
    def test_parser_and_shell_syntax(self):
        subprocess.run(["sh", str(REPO / "tests/test_dualsta_gate.sh")], check=True)
        for script in (MOD / "build.sh", GATE, RUNNER):
            result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_is_scoped_and_does_not_call_vendor_setter(self):
        source = (MOD / "src/com/hiliang/mt6639concurrency/DualStaGateHook.java").read_text()
        self.assertIn('PACKAGE = "android"', source)
        self.assertIn('"com.android.server.wifi.WifiOptimizationStub"', source)
        self.assertIn('"isNativeModeActive"', source)
        self.assertIn("DualStaGate.active()", source)
        for forbidden in ("android.net.wifi.cts", "setStaConcurrency", "create-group", "Runtime"):
            self.assertNotIn(forbidden, source)

    def test_runner_arms_and_disarms_gate_on_recovery(self):
        runner = RUNNER.read_text()
        self.assertIn('GATE=$D/dualsta_gate.sh', runner)
        self.assertIn('sh "$GATE" "$D" arm 60', runner)
        self.assertIn('sh "$GATE" "$D" disarm', runner)
        self.assertLess(runner.index(': > "$D/mode.intent"'),
                        runner.index('sh "$GATE" "$D" arm 60'))

    @unittest.skipUnless(APK.is_file() and pubenv.AAPT2 and pubenv.DROIDASC,
                         "built module APK, aapt2 or droidasc unavailable")
    def test_apk_structure_and_hash(self):
        self.assertTrue(APK.is_file())
        manifest = subprocess.run(
            ["aapt2", "dump", "xmltree", str(APK), "--file", "AndroidManifest.xml"],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("com.hiliang.mt6639concurrency", manifest)
        self.assertIn("xposedmodule", manifest)
        self.assertNotIn("uses-permission", manifest)
        classes = subprocess.run(["droidasc", "listclass", str(APK)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn("Lcom/hiliang/mt6639concurrency/DualStaGateHook;", classes)
        self.assertIn("Lcom/hiliang/mt6639concurrency/DualStaGate;", classes)
        self.assertNotIn("Lde/robv/android/xposed/", classes)
        self.assertEqual(
            hashlib.sha256(APK.read_bytes()).hexdigest(),
            "0dba2dc437cb967e04c4caad1fe42e1ff9787cba0deb24e117037064f0346c4c",
        )

    def test_status_distinguishes_disarmed_expired_invalid_and_armed(self):
        source = GATE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="dualsta-status-") as td:
            root = Path(td)
            task = root / "mt6639-sta-test"
            base = root / "base"
            task.mkdir(mode=0o700)
            base.mkdir(mode=0o700)
            boot_file = root / "boot"
            uptime_file = root / "uptime"
            boot = "11111111-2222-3333-4444-555555555555"
            boot_file.write_text(boot + "\n")
            uptime_file.write_text("100.000 0.00\n")
            harness = root / "gate.sh"
            source = source.replace(
                "/data/local/tmp/mt6639-sta-", str(root / "mt6639-sta-")
            ).replace(
                "BASE=/data/misc/mt6639-concurrency", f"BASE={base}"
            ).replace(
                "/proc/sys/kernel/random/boot_id", str(boot_file)
            ).replace(
                "/proc/uptime", str(uptime_file)
            ).replace(
                '[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] &&\n'
                '    [ "$(stat -c %a "$D")" = 700 ] && [ "$(id -u)" = 0 ] || exit 77',
                '[ -d "$D" ] && [ ! -L "$D" ] && [ "$(id -u)" = "$(id -u)" ] || exit 77',
            )
            harness.write_text(source, encoding="utf-8")
            harness.chmod(0o700)
            marker = base / "gate"

            def status():
                done = subprocess.run(
                    ["sh", str(harness), str(task), "status"],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                return done.stdout.strip()

            self.assertEqual(status(), "state=disarmed")
            marker.write_text(
                "schema=mt6639-dualsta-gate-v1\n"
                f"boot={boot}\nexpires_ms=150000\nlease=0123456789abcdef\n"
            )
            self.assertTrue(status().startswith("state=armed "))
            marker.write_text(
                "schema=mt6639-dualsta-gate-v1\n"
                f"boot={boot}\nexpires_ms=90000\nlease=0123456789abcdef\n"
            )
            self.assertIn("state=expired reason=deadline", status())
            marker.write_text(
                "schema=mt6639-dualsta-gate-v1\n"
                f"boot={boot}\nexpires_ms=250001\nlease=0123456789abcdef\n"
            )
            self.assertIn("state=invalid reason=remaining_ttl_too_long", status())
            marker.write_text("broken\n")
            self.assertIn("state=invalid reason=field_count", status())


class RunnerRecoveryTests(unittest.TestCase):
    RECOVER = extract_function(RUNNER, "recover")

    def run_recovery(self, current_mode, *, requests="", mode_enable=None,
                     request_intent=True, mode_intent=True):
        with tempfile.TemporaryDirectory(prefix="sta-runner-recovery-") as td:
            root = Path(td)
            task = root / "task"
            task.mkdir(mode=0o700)
            if request_intent:
                (task / "request.intent").touch()
            if mode_intent:
                (task / "mode.intent").touch()
            if mode_enable is not None:
                (task / "mode.enable").write_text(mode_enable)
            requests_file = root / "requests"
            requests_file.write_text(requests)
            calls = root / "calls"
            harness = root / "recover.sh"
            harness.write_text(
                "#!/bin/sh\n"
                "set -u\n"
                "D=\"$TASK_DIR\"\n"
                "REQUEST_KEY=NoSsid_2g_internet\n"
                "GATE=$D/dualsta_gate.sh\n"
                "boot_ok() { return 0; }\n"
                "flock() { return 0; }\n"
                "wifi() {\n"
                "  printf 'wifi %s\\n' \"$*\" >> \"$CALLS\"\n"
                "  case \"$1\" in\n"
                "    list-requests) cat \"$REQUESTS_FILE\" ;;\n"
                "    remove-request) : > \"$REQUESTS_FILE\" ;;\n"
                "  esac\n"
                "}\n"
                "mode() {\n"
                "  printf 'mode %s\\n' \"$*\" >> \"$CALLS\"\n"
                "  case \"$1\" in\n"
                "    inspect) echo \"before_mode=$CURRENT_MODE\" ;;\n"
                "    compare-set) return 0 ;;\n"
                "    selection-restore) return 0 ;;\n"
                "  esac\n"
                "}\n"
                "sh() { printf 'helper %s %s\\n' \"$1\" \"$2\" >> \"$CALLS\"; }\n"
                + self.RECOVER + "\n"
                "recover\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update({
                "TASK_DIR": str(task),
                "CALLS": str(calls),
                "REQUESTS_FILE": str(requests_file),
                "CURRENT_MODE": str(current_mode),
            })
            done = subprocess.run(
                ["sh", str(harness)], capture_output=True, text=True,
                env=env, timeout=10,
            )
            call_text = calls.read_text() if calls.exists() else ""
            restore = (task / "mode.restore").read_text() if (task / "mode.restore").exists() else ""
            return done, call_text, restore

    def test_mode_two_is_restored_only_with_complete_setter_readback(self):
        done, calls, _ = self.run_recovery(
            2, mode_enable="before_mode=0\nafter_mode=2\n", request_intent=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("mode compare-set 2 0", calls)

    def test_ambiguous_mode_two_is_preserved(self):
        done, calls, restore = self.run_recovery(
            2, requests="", mode_enable=None, request_intent=True)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("mode compare-set 2 0", calls)
        self.assertIn("mode_restore_ownership_unverified", restore)

    def test_live_owned_request_proves_mode_ownership(self):
        done, calls, _ = self.run_recovery(
            2, requests="NoSsid_2g_internet active\n",
            mode_enable=None, request_intent=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("wifi remove-request NoSsid_2g_internet", calls)
        self.assertIn("mode compare-set 2 0", calls)


if __name__ == "__main__":
    unittest.main()
