"""Static and packaging checks for the explicit KernelSU hotspot module."""
from pathlib import Path
import hashlib
import fcntl
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "modules/mt6639-hotspot"
DEX_PROFILE = REPO / "outputs/softap-profile-build-20261026-1/classes.dex"
DEX_TETHER = REPO / "outputs/native-tether-build-v030-fixed/classes.dex"
HOSTAPD_DIR = REPO / "outputs/hostapd-standalone-20261025-1/bin"
DELETE_IFINDEX = REPO / "outputs/native-ifindex-delete-build-20261026-5/delete_ifindex"
BOOT_ID = "11111111-2222-3333-4444-555555555555"


class HotspotModuleTests(unittest.TestCase):
    def test_recovery_treats_an_already_exited_runner_as_teardown(self):
        # A runner that is already gone must not make a complete teardown look
        # failed: the controller used to archive that failure and replay it.
        src = (REPO / "tools/softap_native_once.sh").read_text()
        self.assertIn("terminal_after_native_exit", src)
        self.assertIn('"$interface_state" = absent', src)
        self.assertIn('"$station_state" = verified', src)
        self.assertIn('"$driver_state" = restored', src)

    def test_shell_syntax_and_safe_defaults(self):
        scripts = [
            MOD / "action.sh",
            MOD / "customize.sh",
            MOD / "service.sh",
            MOD / "uninstall.sh",
            MOD / "bin/control.sh",
            MOD / "bin/status_snapshot.sh",
            MOD / "bin/request_worker.sh",
            MOD / "bin/softap_24g_once.sh",
            REPO / "tools/softap_native_once.sh",
            REPO / "tools/build_hotspot_module.sh",
            REPO / "tools/build_delete_ifindex.sh",
        ]
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        config = (MOD / "config.conf").read_text()
        self.assertIn("ENABLE_AT_BOOT=${ENABLE_AT_BOOT:-0}", config)
        self.assertIn("ENABLE_SETTINGS_BRIDGE=${ENABLE_SETTINGS_BRIDGE:-1}", config)
        service = (MOD / "service.sh").read_text()
        self.assertIn('!= 1', service)
        self.assertIn("request_worker.sh", service)
        control = (MOD / "bin/control.sh").read_text()
        self.assertIn("REJECT enforcing_required", control)
        self.assertIn("cleanup.request", control)
        self.assertIn("recovery.rc", control)
        self.assertIn("no_effect_terminal", control)
        self.assertIn("action_rc=$action_rc", control)
        self.assertIn("RUN_START_FILE", control)
        self.assertIn("guardian_process_without_record", control)
        self.assertIn("session_cross_boot", control)
        self.assertIn("native_alive", control)
        self.assertIn("status.recheck", control)
        self.assertIn("invoking checked recover", control)
        self.assertIn("discard_staged_task", control)
        self.assertIn("stage_native_task", control)
        self.assertIn("abort_staged_start", control)
        self.assertIn("staged_task_cleanup_failed", control)
        self.assertNotIn("rm -rf", control)
        self.assertNotIn("su -c", control)
        self.assertIn("delete_ifindex", control)
        self.assertIn("publish_status", control)
        self.assertIn("publish_status idle none none", control)
        self.assertIn("5g:20|5g:40|5g:80", control)
        self.assertIn("reconnect-2g.key", control)
        self.assertIn("2g:20|5g:20|5g:40|5g:80", control)
        # A fail-closed start must not pin the public snapshot to "unresolved".
        self.assertIn("if session_task >/dev/null; then", control)
        self.assertIn("c02-24g", control)
        self.assertIn("action.lock", control)
        c02 = (MOD / "bin/softap_24g_once.sh").read_text()
        self.assertIn('run-24g', c02)
        self.assertIn('REJECT native_session_exists', c02)
        self.assertIn('REJECT mtap0_exists', c02)
        self.assertIn('reconnect.key', c02)
        self.assertNotIn('/data/adb/modules/mt6639-hotspot/private/ap.key', c02)
        snapshot = (MOD / "bin/status_snapshot.sh").read_text()
        self.assertIn("/data/misc/mt6639-hotspot/status", snapshot)
        self.assertIn("mt6639-hotspot-status-v4", snapshot)
        for field in (
            "ap_key", "reconnect_6g", "reconnect_2g", "enforcing",
            "ready_6g", "ready_5g", "ready_2g", "local_ready", "private_file_ok",
        ):
            self.assertIn(field, snapshot)
        self.assertIn("restorecon -F", snapshot)
        self.assertIn("idle|starting|active|stopping|unresolved|disabled", snapshot)
        self.assertIn("none|2g|5g|6g", snapshot)
        service = (MOD / "service.sh").read_text()
        self.assertIn('"$MODDIR" init', service)
        worker = (MOD / "bin/request_worker.sh").read_text()
        self.assertIn("mt6639-hotspot-request-v1", worker)
        self.assertIn("recover_claimed", worker)
        self.assertIn(': > "$claimed.execute"', worker)
        self.assertLess(worker.index(': > "$claimed.execute"'),
                        worker.index('case "$REQUEST_OP" in'))
        self.assertIn("start:6g:20|start:6g:40|start:6g:80", worker)
        self.assertIn("start:6g:160|start:6g:320", worker)
        self.assertIn("start:5g:20|start:5g:40|start:5g:80", worker)
        self.assertIn("start:2g:20", worker)
        self.assertIn("start-local:6g:20", worker)
        self.assertIn("c02-24g:6g:20", worker)
        self.assertIn('"$MODDIR" c02-24g', worker)
        self.assertIn('start "$REQUEST_BAND" "$REQUEST_WIDTH"', worker)
        self.assertIn('start-local "$REQUEST_BAND" "$REQUEST_WIDTH"', worker)
        self.assertIn('"$MODDIR" stop', worker)
        self.assertIn("EXPECTED_UID=1000", worker)
        self.assertIn("MAX_AGE_SECONDS=60", worker)
        self.assertIn("request.*.req", worker)
        self.assertIn(
            "/data/user/0/com.android.settings/files/mt6639-hotspot", worker
        )
        self.assertIn("request_dir_ready", worker)
        self.assertNotIn("eval ", worker)
        self.assertNotIn("ksud ", worker)
        self.assertNotIn("su -c", worker)

    def test_runner_hold_and_module_disable_guard(self):
        runner = (REPO / "tools/softap_native_once.sh").read_text()
        self.assertIn("GUARDIAN_HOLD_SECONDS=${GUARDIAN_HOLD_SECONDS:-90}", runner)
        self.assertIn("MODULE_GUARD_DIR", runner)
        self.assertIn("mt6639-hotspot", runner)
        self.assertIn('"$n" -lt "$GUARDIAN_HOLD_SECONDS"', runner)
        self.assertIn('"$action_rc" = 0 ] || : > "$D/cleanup.request"', runner)
        self.assertIn('guardian.pid', runner)
        self.assertIn('guardian.start', runner)
        self.assertIn("recover|guardian", runner)
        self.assertIn("UPSTREAM_SSID=LabNet-2.4G", runner)
        self.assertIn("UPSTREAM_FREQ=2412", runner)
        self.assertIn("UPSTREAM_BSSID=02:00:00:00:73:1a", runner)
        self.assertIn("REJECT_5GHz_160MHz_DFS_not_implemented", runner)
        self.assertIn('grep -Fxq "Wifi is connected to \\"$UPSTREAM_SSID\\""', runner)

    def test_no_credentials_or_private_files_in_source(self):
        forbidden = {"ap.key", "reconnect.key"}
        for path in MOD.rglob("*"):
            if path.is_file():
                self.assertNotIn(path.name, forbidden)
                data = path.read_bytes()
                self.assertNotIn(b"BEGIN PRIVATE KEY", data)

    def test_staged_abort_preserves_session_not_created_by_attempt(self):
        control = (MOD / "bin/control.sh").read_text()
        start = control.index("abort_staged_start() {")
        function = control[start:control.index("\n}\n", start) + 2]
        for owned in (False, True):
            with self.subTest(owned=owned), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                session = root / "session"
                session.mkdir()
                task = root / "task"
                task.mkdir()
                harness = '''set -u
D=$1; SESSION=$D/session
log() { :; }
clear_session() { echo cleared > "$D/cleared"; }
discard_staged_task() { echo discarded > "$D/discarded"; }
'''
                result = subprocess.run(
                    ["sh", "-c", harness + function + '\nabort_staged_start "$D/task" fixture 73 ' + ("1" if owned else "0"), "sh", td],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertEqual((root / "cleared").exists(), owned)
                self.assertTrue((root / "discarded").exists())

    def test_worker_busy_does_not_recover_or_process_claims(self):
        worker = (MOD / "bin/request_worker.sh").read_text()
        dispatch = worker[worker.rindex('\ncase "$OP" in'):]
        for op in ("run", "once"):
            with self.subTest(op=op), tempfile.TemporaryDirectory() as td:
                harness = '''set -u
OP=$1; D=$2; BRIDGE=$D
prepare_private() { echo prepare >> "$D/calls"; }
worker_lock() { echo locked >> "$D/calls"; return 75; }
recover_claimed() { echo recover >> "$D/calls"; }
run_loop() { echo run >> "$D/calls"; }
process_one() { echo process >> "$D/calls"; }
'''
                result = subprocess.run(["sh", "-c", harness + dispatch, "sh", op, td], capture_output=True, text=True)
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual((Path(td) / "calls").read_text().splitlines(), ["prepare", "locked"])

    def test_worker_lock_rejects_live_owner_then_accepts_release(self):
        worker = (MOD / "bin/request_worker.sh").read_text()
        start = worker.index("worker_lock() {")
        function = worker[start:worker.index("\n}\n", start) + 2]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "flock"
            shim.write_text('#!/usr/bin/python3\nimport fcntl,sys\ntry: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept OSError: sys.exit(75)\n')
            shim.chmod(0o755)
            env = dict(os.environ, PATH=td + ":/usr/bin:/bin")
            with (root / "worker.lock").open("w") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for locked, expected in ((True, 75), (False, 0)):
                    if not locked:
                        fcntl.flock(owner, fcntl.LOCK_UN)
                    result = subprocess.run(["sh", "-c", 'BRIDGE=$1; ' + function + '\nworker_lock', "sh", td], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_staged_task_cleanup_is_bounded_and_fail_closed(self):
        control = (MOD / "bin/control.sh").read_text(encoding="utf-8")
        start = control.index("discard_staged_task() {")
        end = control.index("\n}\n\nstage_native_task()", start) + 2
        function = control[start:end]
        with tempfile.TemporaryDirectory(prefix="mt6639-stage-clean-") as td:
            root = Path(td)
            allowed = str(root / "mt6639-native-ap-")
            function = function.replace(
                "/data/local/tmp/mt6639-native-ap-", allowed
            )
            harness = root / "cleanup.sh"
            harness.write_text(
                "#!/bin/sh\n"
                "set -u\n"
                "TASK_FILE=$TEST_ROOT/session-task\n"
                "stat() { case \"$2\" in %u) echo 0 ;; %a) echo 700 ;; *) return 64 ;; esac; }\n"
                + function
                + "\ndiscard_staged_task \"$1\"\n",
                encoding="utf-8",
            )
            task = root / "mt6639-native-ap-test"
            task.mkdir(mode=0o700)
            known = [
                "softap_native_once.sh", "softap_native_config.sh", "hostapd",
                "hostapd_cli", "delete_ifindex", "native-tether.dex",
                "softap-profile.dex", "twt_recover.sh", "compatibility.conf", "ap.key",
                "reconnect.key", "ap.conf", ".stage-error",
            ]
            for name in known:
                (task / name).write_text("fixture\n")
            env = os.environ.copy()
            env["TEST_ROOT"] = str(root)
            unknown = task / "unexpected"
            unknown.write_text("must survive\n")
            failed = subprocess.run(
                ["sh", str(harness), str(task)], capture_output=True,
                text=True, env=env, timeout=10,
            )
            self.assertEqual(failed.returncode, 76)
            self.assertTrue(unknown.is_file())
            for name in known:
                self.assertFalse((task / name).exists(), name)
            unknown.unlink()
            cleaned = subprocess.run(
                ["sh", str(harness), str(task)], capture_output=True,
                text=True, env=env, timeout=10,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertFalse(task.exists())

    def test_interrupted_claims_are_quarantined_without_replay(self):
        worker = (MOD / "bin/request_worker.sh").read_text(encoding="utf-8")
        start = worker.index("recover_claimed() {")
        end = worker.index("\n}\nvalid_nonce()", start) + 2
        function = worker[start:end]
        with tempfile.TemporaryDirectory(prefix="mt6639-claim-recovery-") as td:
            root = Path(td)
            claim = root / "claimed"
            rejected = root / "rejected"
            claim.mkdir()
            rejected.mkdir()
            (claim / "100.req").write_text("claimed\n")
            (claim / "100.req.execute").write_text("intent\n")
            (claim / "101.req.execute").write_text("orphan\n")
            harness = root / "recover.sh"
            harness.write_text(
                "#!/bin/sh\n"
                "set -u\n"
                "CLAIM=$TEST_ROOT/claimed\n"
                "REJECTED=$TEST_ROOT/rejected\n"
                "RESULT=$TEST_ROOT/result\n"
                "ENTRY_SEQ=0\n"
                "date() { echo 1700000000; }\n"
                "record_result() { printf 'result=%s\\nnonce=%s\\nrc=%s\\n' "
                "\"$1\" \"$2\" \"$3\" > \"$RESULT\"; }\n"
                + function
                + "\nrecover_claimed\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["TEST_ROOT"] = str(root)
            done = subprocess.run(
                ["sh", str(harness)], capture_output=True, text=True,
                env=env, timeout=10,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(list(claim.iterdir()), [])
            names = {path.name for path in rejected.iterdir()}
            self.assertTrue(any(name.endswith(".interrupted_claim") for name in names))
            self.assertTrue(any(name.endswith(".interrupted_claim.execute") for name in names))
            self.assertTrue(any(name.endswith(".orphan_execute") for name in names))
            result = (root / "result").read_text()
            self.assertIn("result=interrupted_claim\n", result)
            self.assertIn("rc=255\n", result)

    @unittest.skipUnless(
        DEX_PROFILE.is_file() and DEX_TETHER.is_file() and DELETE_IFINDEX.is_file() and
        (HOSTAPD_DIR / "hostapd").is_file() and (HOSTAPD_DIR / "hostapd_cli").is_file(),
        "pinned candidate artifacts are unavailable",
    )
    def test_reproducible_module_package(self):
        with tempfile.TemporaryDirectory(prefix="mt6639-hotspot-package-") as td:
            outputs = [Path(td) / "mt6639-hotspot-1.zip", Path(td) / "mt6639-hotspot-2.zip"]
            for out in outputs:
                result = subprocess.run([
                    "sh", str(REPO / "tools/build_hotspot_module.sh"),
                    str(HOSTAPD_DIR), str(DEX_TETHER), str(DEX_PROFILE),
                    str(DELETE_IFINDEX), str(out),
                ], capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(out.is_file())
            self.assertEqual(
                hashlib.sha256(outputs[0].read_bytes()).hexdigest(),
                hashlib.sha256(outputs[1].read_bytes()).hexdigest(),
            )
            out = outputs[0]
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                for required in {
                    "module.prop", "config.conf", "compatibility.conf", "customize.sh", "action.sh", "service.sh",
                    "bin/control.sh", "bin/hostapd", "bin/hostapd_cli",
                    "bin/status_snapshot.sh",
                    "bin/request_worker.sh",
                    "bin/softap_once.sh", "bin/softap_profile.sh",
                    "bin/softap_24g_once.sh",
                    "bin/native-tether.dex", "bin/softap-profile.dex",
                    "bin/delete_ifindex",
                    "bin/softap_native_once.sh", "bin/softap_native_config.sh",
                    "bin/twt_recover.sh", "SHA256SUMS", "ASSET-PROVENANCE.txt",
                }:
                    self.assertIn(required, names)
                self.assertIn("version=v0.3.1", archive.read("module.prop").decode())
                self.assertFalse(any(name.endswith(("ap.key", "reconnect.key")) for name in names))
                self.assertFalse(any(name.startswith(("outputs/", "local/")) for name in names))
                sums = archive.read("SHA256SUMS").decode()
                self.assertIn("compatibility.conf", sums)
                self.assertIn("bin/hostapd", sums)
                self.assertIn("bin/control.sh", sums)
                self.assertIn("bin/status_snapshot.sh", sums)
                cust = archive.read("customize.sh").decode()
                for required_exec in (
                    "bin/control.sh", "bin/request_worker.sh", "bin/android-env.sh",
                    "bin/delete_ifindex", "bin/hostapd", "bin/hostapd_cli",
                ):
                    self.assertIn(required_exec, cust)
                nt_src = (REPO / "native/framework/NativeTether.java").read_text(encoding="utf-8")
                self.assertIn("connectivityScope", nt_src)
                self.assertNotIn("call(builder, \"setConnectivityScope\"", nt_src)

    @staticmethod
    def write_worker_stat_fixture(bindir):
        stat_shim = bindir / "stat"
        stat_shim.write_text(
            '#!/bin/sh\n'
            'if [ "$2" = %C ]; then\n'
            '  echo u:object_r:system_app_data_file:s0; exit 0\nfi\n'
            '# Linux keeps its actual stat semantics; BSD needs format translation.\n'
            'if /usr/bin/stat -c %u "$3" >/dev/null 2>&1; then\n'
            '  exec /usr/bin/stat "$@"\nfi\n'
            'case "$2" in\n'
            '  %u) exec /usr/bin/stat -f %u "$3" ;;\n'
            '  %g) exec /usr/bin/stat -f %g "$3" ;;\n'
            '  %a) exec /usr/bin/stat -f %Lp "$3" ;;\n'
            '  %s) exec /usr/bin/stat -f %z "$3" ;;\n'
            '  %Y) exec /usr/bin/stat -f %m "$3" ;;\n'
            '  *) exit 64 ;;\nesac\n',
            encoding="utf-8",
        )
        stat_shim.chmod(0o755)

    def run_worker_fixture(
            self, payload, mode=0o600, filename_nonce="a" * 32,
            invalid_first=False, hostile_first=False, missing_work=False, service_start=False
    ):
        td = tempfile.TemporaryDirectory(prefix="mt6639-hotspot-bridge-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        module = root / "module"
        work = root / "work"
        public = root / "public"
        bindir = root / "bin"
        module.joinpath("bin").mkdir(parents=True)
        if not missing_work:
            work.mkdir(mode=0o700)
        public.mkdir()
        bindir.mkdir()
        log = root / "control.log"

        control = module / "bin/control.sh"
        control.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MT6639_TEST_LOG"\nexit 0\n',
            encoding="utf-8",
        )
        control.chmod(0o755)
        self.write_worker_stat_fixture(bindir)
        cat_shim = bindir / "cat"
        cat_shim.write_text(
            f'#!/bin/sh\nif [ "$1" = /proc/sys/kernel/random/boot_id ]; then\n'
            f'  echo {BOOT_ID}\n  exit 0\nfi\nexec /bin/cat "$@"\n',
            encoding="utf-8",
        )
        cat_shim.chmod(0o755)
        chown_shim = bindir / "chown"
        chown_shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        chown_shim.chmod(0o755)
        flock_shim = bindir / "flock"
        flock_shim.write_text(
            '#!/usr/bin/python3\nimport fcntl, sys\n'
            'try: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
            'except OSError: sys.exit(75)\n', encoding="utf-8",
        )
        flock_shim.chmod(0o755)

        worker = (MOD / "bin/request_worker.sh").read_text(encoding="utf-8")
        worker = worker.replace(
            "/data/adb/modules/mt6639-hotspot", str(module)
        ).replace(
            "/data/adb/mt6639-hotspot", str(work)
        ).replace(
            "/data/user/0/com.android.settings/files/mt6639-hotspot", str(public)
        ).replace(
            "EXPECTED_UID=1000", f"EXPECTED_UID={os.getuid()}"
        ).replace(
            "EXPECTED_GID=1000", f"EXPECTED_GID={os.getgid()}"
        ).replace(
            '[ "$(stat -c %u "$d")" = 0 ]',
            f'[ "$(stat -c %u "$d")" = {os.getuid()} ]',
        ).replace(
            '[ "$(stat -c %u "$REQUEST_DIR")" = "$EXPECTED_UID" ]',
            f'[ "$(stat -c %u "$REQUEST_DIR")" = "{os.getuid()}" ]',
        ).replace(
            '[ "$(stat -c %g "$REQUEST_DIR")" = "$EXPECTED_GID" ]',
            f'[ "$(stat -c %g "$REQUEST_DIR")" = "{os.getgid()}" ]',
        )
        for variable in ("work_parent", "WORK"):
            worker = worker.replace(
                f'[ "$(stat -c %u "${variable}")" = 0 ]',
                f'[ "$(stat -c %u "${variable}")" = {os.getuid()} ]',
            )
        worker_path = module / "bin/request_worker.sh"
        worker_path.write_text(worker, encoding="utf-8")
        request = public / f"request.{filename_nonce}.req"
        request.write_text(payload, encoding="utf-8")
        public.chmod(0o700)
        request.chmod(mode)
        if invalid_first:
            invalid = public / "request.0.req"
            invalid.write_text("not-a-valid-request\n", encoding="utf-8")
            invalid.chmod(0o600)
        if hostile_first:
            invalid_name = public / "request.0.req"
            invalid_name.write_text("not-a-valid-request\n", encoding="utf-8")
            invalid_name.chmod(0o600)
            dangling = public / ("request." + "0" * 32 + ".req")
            os.symlink(root / "missing-target", dangling)
            bad_content = public / ("request." + "1" * 32 + ".req")
            bad_content.write_text("garbage\n", encoding="utf-8")
            bad_content.chmod(0o600)
        env = os.environ.copy()
        env["PATH"] = f"{bindir}:/usr/bin:/bin"
        env["MT6639_TEST_LOG"] = str(log)
        if service_start:
            (module / "config.conf").write_text((MOD / "config.conf").read_text())
            (module / "service.sh").write_text((MOD / "service.sh").read_text())
            (module / "bin/status_snapshot.sh").write_text("#!/bin/sh\nexit 0\n")
            command = ["sh", str(module / "service.sh")]
        else:
            command = ["sh", str(worker_path), str(module), "once"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=10, env=env)
            if service_start:
                status = subprocess.run(
                    ["sh", str(worker_path), str(module), "status"],
                    capture_output=True, text=True, timeout=10, env=env,
                )
                self.assertIn("worker=alive", status.stdout, status.stderr)
                self.assertNotIn("settings bridge failed to start", done.stderr)
                # Startup publishes liveness before the queued request is consumed.
                for _ in range(100):
                    if (work / "bridge/last-result").is_file():
                        break
                    time.sleep(0.02)
        finally:
            if service_start:
                subprocess.run(
                    ["sh", str(worker_path), str(module), "stop"],
                    capture_output=True, text=True, timeout=10, env=env,
                )
        return done, root, log

    @staticmethod
    def request_payload(op="start", band="6g", width="20", nonce="a" * 32):
        return (
            "schema=mt6639-hotspot-request-v1\n"
            f"op={op}\n"
            f"band={band}\n"
            f"width={width}\n"
            f"boot={BOOT_ID}\n"
            f"nonce={nonce}\n"
        )

    def test_worker_initializes_missing_private_work(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload("stop"), missing_work=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        for relative in ("work", "work/bridge", "work/bridge/claimed", "work/bridge/rejected"):
            directory = root / relative
            self.assertTrue(directory.is_dir(), relative)
            self.assertFalse(directory.is_symlink(), relative)
            self.assertEqual(directory.stat().st_uid, os.getuid(), relative)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700, relative)
        self.assertTrue(log.read_text().strip().endswith("stop"))
        self.assertIn("result=stop:6g:20\n", (root / "work/bridge/last-result").read_text())

    def test_service_initializes_missing_private_work_and_starts_worker(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload("stop"), missing_work=True, service_start=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ENABLE_AT_BOOT=0", done.stdout)
        self.assertTrue(log.read_text().strip().endswith("stop"))
        self.assertIn("result=stop:6g:20\n", (root / "work/bridge/last-result").read_text())

    def test_private_work_rejects_unsafe_existing_paths(self):
        worker = (MOD / "bin/request_worker.sh").read_text()
        start = worker.index("prepare_private() {")
        function = worker[start:worker.index("\n}\n", start) + 2]
        # Adapt the root identity only; uid/mode/type still come from real stat.
        mapped_function = function
        for variable in ("work_parent", "WORK", "d"):
            mapped_function = mapped_function.replace(
                f'[ "$(stat -c %u "${variable}")" = 0 ]',
                f'[ "$(stat -c %u "${variable}")" = {os.getuid()} ]',
            )
        for unsafe in ("file", "symlink", "dangling_symlink", "wrong_mode", "parent_symlink"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                parent = root / "parent"
                parent.mkdir()
                work = parent / "work"
                target = root / "target"
                target.mkdir(mode=0o755)
                target.chmod(0o755)
                sentinel = target / "sentinel"
                sentinel.write_text("untouched")
                if unsafe == "file":
                    work.write_text("untouched")
                elif unsafe == "symlink":
                    work.symlink_to(target, target_is_directory=True)
                elif unsafe == "dangling_symlink":
                    work.symlink_to(root / "absent", target_is_directory=True)
                elif unsafe == "wrong_mode":
                    work.mkdir(mode=0o755)
                    work.chmod(0o755)
                else:
                    parent.rmdir()
                    parent.symlink_to(target, target_is_directory=True)
                before = work.lstat() if unsafe != "parent_symlink" else parent.lstat()
                bindir = root / "bin"
                bindir.mkdir()
                self.write_worker_stat_fixture(bindir)
                env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
                result = subprocess.run(
                    ["sh", "-c", 'WORK=$1; BRIDGE=$WORK/bridge; CLAIM=$BRIDGE/claimed; '
                     'REJECTED=$BRIDGE/rejected; ' + mapped_function + "\nprepare_private", "sh", str(work)],
                    capture_output=True, text=True, env=env,
                )
                self.assertEqual(result.returncode, 77, result.stderr)
                after = work.lstat() if unsafe != "parent_symlink" else parent.lstat()
                self.assertEqual((before.st_uid, before.st_mode), (after.st_uid, after.st_mode))
                self.assertFalse((work / "bridge").exists())
                self.assertEqual(sentinel.read_text(), "untouched")
                self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_private_work_rejects_non_root_owner_without_chmod(self):
        worker = (MOD / "bin/request_worker.sh").read_text()
        start = worker.index("prepare_private() {")
        function = worker[start:worker.index("\n}\n", start) + 2]
        # /tmp is root-owned; the temporary WORK keeps its real unprivileged uid.
        with tempfile.TemporaryDirectory(dir=Path("/tmp").resolve()) as td:
            work = Path(td)
            if os.getuid() == 0:
                os.chown(work, 1, -1)
            self.assertEqual(work.parent.stat().st_uid, 0)
            self.assertNotEqual(work.stat().st_uid, 0)
            work.chmod(0o755)
            before = work.stat()
            bindir = work / "bin"
            bindir.mkdir()
            self.write_worker_stat_fixture(bindir)
            env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
            result = subprocess.run(
                ["sh", "-c", 'WORK=$1; BRIDGE=$WORK/bridge; CLAIM=$BRIDGE/claimed; '
                 'REJECTED=$BRIDGE/rejected; ' + function + "\nprepare_private", "sh", td],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 77, result.stderr)
            after = work.stat()
            self.assertEqual((before.st_uid, before.st_mode), (after.st_uid, after.st_mode))
            self.assertFalse((work / "bridge").exists())

    def test_private_work_rejects_non_root_parent_before_creation(self):
        worker = (MOD / "bin/request_worker.sh").read_text()
        start = worker.index("prepare_private() {")
        function = worker[start:worker.index("\n}\n", start) + 2]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent = root / "parent"
            parent.mkdir(mode=0o755)
            if os.getuid() == 0:
                os.chown(parent, 1, -1)
            self.assertNotEqual(parent.stat().st_uid, 0)
            work = parent / "work"
            before = parent.stat()
            bindir = root / "bin"
            bindir.mkdir()
            self.write_worker_stat_fixture(bindir)
            env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
            result = subprocess.run(
                ["sh", "-c", 'WORK=$1; BRIDGE=$WORK/bridge; CLAIM=$BRIDGE/claimed; '
                 'REJECTED=$BRIDGE/rejected; ' + function + "\nprepare_private", "sh", str(work)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 77, result.stderr)
            self.assertEqual((before.st_uid, before.st_mode),
                             (parent.stat().st_uid, parent.stat().st_mode))
            self.assertFalse(work.exists())

    def test_bridge_dispatches_only_fixed_start_and_stop(self):
        cases = [
            ("start", "6g", width, f"start 6g {width}")
            for width in ("20", "40", "80", "160", "320")
        ] + [
            ("start", "5g", width, f"start 5g {width}")
            for width in ("20", "40", "80")
        ] + [
            ("start", "2g", "20", "start 2g 20"),
            ("start-local", "6g", "20", "start-local 6g 20"),
            ("start-local", "5g", "80", "start-local 5g 80"),
            ("start-local", "2g", "20", "start-local 2g 20"),
        ] + [
            ("stop", "6g", "20", "stop"),
            ("c02-24g", "6g", "20", "c02-24g"),
        ]
        for op, band, width, expected in cases:
            with self.subTest(op=op, band=band, width=width):
                done, root, log = self.run_worker_fixture(
                    self.request_payload(op, band, width)
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertTrue(log.read_text().strip().endswith(expected))
                result = (root / "work/bridge/last-result").read_text()
                self.assertIn(f"result={op}:{band}:{width}\n", result)
                self.assertIn("rc=0\n", result)

    def test_bridge_rejects_invalid_nonce_without_dispatch(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload(nonce="b" * 32),
            filename_nonce="a" * 32,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(log.exists())
        result = (root / "work/bridge/last-result").read_text()
        self.assertIn("result=rejected_nonce", result)

    def test_bridge_rejects_wrong_mode_without_dispatch(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload(),
            mode=0o644,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(log.exists())
        result = (root / "work/bridge/last-result").read_text()
        self.assertIn("result=rejected_metadata", result)

    def test_invalid_queue_head_does_not_starve_valid_stop(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload("stop"),
            invalid_first=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(log.read_text().strip().endswith("stop"))
        self.assertFalse((root / "public/request.0.req").exists())
        self.assertTrue(any(
            path.name.endswith(".invalid_name")
            for path in (root / "work/bridge/rejected").iterdir()
        ))

    def test_multiple_hostile_entries_do_not_delay_valid_stop(self):
        done, root, log = self.run_worker_fixture(
            self.request_payload("stop"),
            hostile_first=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(log.read_text().strip().endswith("stop"))
        rejected = {path.name.split(".", 1)[1]
                    for path in (root / "work/bridge/rejected").iterdir()}
        self.assertIn("invalid_name", rejected)
        self.assertIn("nonregular", rejected)
        self.assertIn("invalid", rejected)

    def test_control_work_directory_rejects_symlink_and_non_root(self):
        control = (MOD / "bin/control.sh").read_text(encoding="utf-8")
        start = control.index("prepare_work_directory() {")
        function = control[start:control.index("\n}\n", start) + 2]
        with tempfile.TemporaryDirectory(prefix="mt6639-hotspot-workdir-") as td:
            root = Path(td)
            parent = root / "parent"
            parent.mkdir(mode=0o755)
            target = root / "target"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            sentinel = target / "keep"
            sentinel.write_text("untouched", encoding="utf-8")

            bindir = root / "bin"
            bindir.mkdir()
            stat_shim = bindir / "stat"
            stat_shim.write_text(
                '#!/bin/sh\n'
                'if [ "${FAKE_NON_ROOT_TARGET:-}" = "$3" ]; then\n'
                '  case "$2" in %u) echo 1000; exit 0 ;; %u:%a) echo 1000:700; exit 0 ;; esac\nfi\n'
                'case "$2" in\n'
                '  %u) echo 0 ;;\n'
                '  %u:%a) mode=$(/usr/bin/stat -c %a "$3") || exit 1; echo "0:$mode" ;;\n'
                '  *) exec /usr/bin/stat "$@" ;;\n'
                'esac\n',
                encoding="utf-8",
            )
            stat_shim.chmod(0o755)
            env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")

            def run_prepare(work_path, **extra):
                return subprocess.run(
                    ["sh", "-c", f'set -u\nWORK=$1\n{function}\nprepare_work_directory', "sh", str(work_path)],
                    capture_output=True, text=True, env=dict(env, **extra), timeout=10,
                )

            # 1. Symlink $WORK is rejected without modifying target
            work = parent / "work"
            work.symlink_to(target, target_is_directory=True)
            res = run_prepare(work)
            self.assertEqual(res.returncode, 77, res.stderr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertEqual(sentinel.read_text(), "untouched")
            work.unlink()

            # 2. Regular file $WORK is rejected
            work.write_text("not-dir", encoding="utf-8")
            self.assertEqual(run_prepare(work).returncode, 77)
            work.unlink()

            # 3. Non-root owner on existing $WORK or parent is rejected
            work.mkdir(mode=0o777)
            work.chmod(0o777)
            self.assertEqual(run_prepare(work, FAKE_NON_ROOT_TARGET=str(work)).returncode, 77)
            self.assertEqual(work.stat().st_mode & 0o777, 0o777)
            self.assertEqual(run_prepare(work, FAKE_NON_ROOT_TARGET=str(parent)).returncode, 77)

            # 4. Root-owned 0777 $WORK is tightened to 0700 without touching child
            child = work / "child"
            child.write_text("keep", encoding="utf-8")
            child.chmod(0o644)
            ok = run_prepare(work)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(work.stat().st_mode & 0o777, 0o700)
            self.assertEqual(child.stat().st_mode & 0o777, 0o644)

    def _run_native_gate_fixture(
            self, *, device="violin", ko_sha="f226384e73810a639b7fc6a335367cce2345a7726aac3dd66515493952987c22",
            jar_sha="0b6f6c859301dae3276b9375c3b75333168dc5b76292ed9da03e29b59fc887bb",
            band="6g", width="20", upstream_mode="auto", wifi_status="Wifi is enabled\n",
            ip_addr="", ip_link="", baseband=""
    ):
        td = tempfile.TemporaryDirectory(prefix="mt6639-hotspot-gate-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        task = root / "mt6639-native-ap-test"
        task.mkdir(mode=0o700)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copyfile(MOD / "compatibility.conf", task / "compatibility.conf")
        (task / "ap.key").write_text("fixture-only-password\n")
        (task / "ap.conf").write_text("interface=mtap0\n")
        shutil.copyfile(REPO / "tools/softap_native_config.sh", task / "softap_native_config.sh")
        (task / "softap_native_config.sh").chmod(0o700)
        for dummy in ("hostapd_cli", "delete_ifindex", "softap-profile.dex", "native-tether.dex"):
            (task / dummy).write_bytes(b"fixture")
        # Stop right after recording reachability at hostapd --mt6639-check-config once all preflight + upstream gates pass.
        hostapd = task / "hostapd"
        hostapd.write_text('#!/bin/sh\n: > "${1:+${0%/*}/reached_hostapd}"\nexit 99\n')
        hostapd.chmod(0o700)
        runner = task / "softap_native_once.sh"
        src = (REPO / "tools/softap_native_once.sh").read_text(encoding="utf-8")
        src = src.replace("/data/local/tmp/mt6639-native-ap-", str(root / "mt6639-native-ap-"))
        runner.write_text(src, encoding="utf-8")
        runner.chmod(0o700)
        # Recompute dynamic pinned hashes inside sha256sum stub from actual repo files.
        native_dex_sha = "de826266991f4715b57274575c3244e3a0d43d8697cf260979569c0ab04d322c"
        shims = {
            "id": '#!/bin/sh\nif [ "$1" = -Z ]; then echo u:r:ksu:s0; else echo 0; fi\n',
            "getenforce": "#!/bin/sh\necho Enforcing\n",
            "flock": "#!/usr/bin/python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n",
            "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
            "stat": '''#!/bin/sh
case "$2" in
  %u) echo 0 ;;
  %a) exec /usr/bin/stat -c %a "$3" ;;
  *) exec /usr/bin/stat "$@" ;;
esac
''',
            "getprop": f'''#!/bin/sh
case "$1" in
  ro.product.device) echo "{device}" ;;
  gsm.version.baseband) echo "{baseband}" ;;
  init.svc.hostapd) echo "" ;;
  *) echo "" ;;
esac
''',
            "app_process": '''#!/bin/sh
case "$*" in
  *"WifiSoftApProfile inspect"*) echo "ap_state=11" ;;
  *"NativeTether inspect"*) echo "downstream_type=VIRTUAL interface=mtap0 scopes=GLOBAL,LOCAL" ;;
esac
''',
            "dumpsys": '''#!/bin/sh
printf "    Upstream wanted: false\\n    Current upstream interface(s): null\\n"
''',
            "cmd": '''#!/bin/sh
printf "%s" "$FIXTURE_WIFI_STATUS"
''',
            "ip": '''#!/bin/sh
case "$*" in
  *"-4 addr show"*) printf "%s" "$FIXTURE_IP_ADDR" ;;
  *"link show"*) printf "%s" "$FIXTURE_IP_LINK" ;;
esac
''',
            "sha256sum": f'''#!/bin/sh
case "$1" in
  */wlan_drv_gen4m_eap_6639.ko) echo "{ko_sha}  ko" ;;
  */framework-tethering.jar) echo "{jar_sha}  jar" ;;
  */hostapd) echo "15023e3bfe6b08415ebcc92479c0cd4fb14adb57b9010266af73bcc1bdc7bc2b  hostapd" ;;
  */hostapd_cli) echo "2da7d467b7b2628c4da40fb5ccc101b17f8b279bd9c038c32e4ae6e5b2924a42  hostapd_cli" ;;
  */delete_ifindex) echo "d20169f8733dc3bd2813806b3deda79e84b497e40e4a0f82bb97cd585844d655  del" ;;
  */softap-profile.dex) echo "7ff17f05008e77535dacac13c5c2f216c664bc26403d6cbfe7da7fb9b7c04017  p.dex" ;;
  */native-tether.dex) echo "{native_dex_sha}  n.dex" ;;
  */softap_native_config.sh) echo "7488253f5f93718f196033aed6ed8b6ead27b300fd2b0a07c6318b64cb73dfca  cfg" ;;
  *) exec /usr/bin/sha256sum "$@" ;;
esac
''',
            "sh": f'''#!/bin/sh
case "$1" in
  */softap_native_config.sh) exit 0 ;;
  *) exec /bin/sh "$@" ;;
esac
''',
        }
        for name, body in shims.items():
            p = bindir / name
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)
        env = dict(
            os.environ,
            PATH=f"{bindir}:/usr/bin:/bin",
            MT6639_HOTSPOT_UPSTREAM=upstream_mode,
            FIXTURE_WIFI_STATUS=wifi_status,
            FIXTURE_IP_ADDR=ip_addr,
            FIXTURE_IP_LINK=ip_link,
        )
        res = subprocess.run(
            ["/bin/sh", str(runner), str(task), "run-tether", band, width],
            env=env, capture_output=True, text=True, timeout=15,
        )
        return res, task

    def test_multi_device_compat_and_tablet_upstream_resolution(self):
        # 1. Pure Wi-Fi tablet (violin OS4, no baseband, no eth/rmnet, disconnected STA) -> auto = local
        res, task = self._run_native_gate_fixture()
        self.assertTrue((task / "reached_hostapd").is_file(), (res.returncode, res.stderr + res.stdout))
        self.assertEqual((task / "upstream.mode").read_text().strip(), "local")
        self.assertEqual((task / "tether.scope").read_text().strip(), "local")

        # 2. Jinghu OS3 with USB Ethernet uplink -> auto = wired, scope = global
        res_w, task_w = self._run_native_gate_fixture(
            device="jinghu",
            ko_sha="95f302184f013b2f58fbdd80998eb599ee24ae9b83ae296820f195bfab4e2e30",
            jar_sha="351d2fd9196b59e988ce5a6b8fb1809777ef66e3e2087c3d570c164ba54040e1",
            ip_addr="5: eth0: <UP>\n    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0\n",
        )
        self.assertTrue((task_w / "reached_hostapd").is_file(), (res_w.returncode, res_w.stderr + res_w.stdout))
        self.assertEqual((task_w / "upstream.mode").read_text().strip(), "wired")
        self.assertEqual((task_w / "tether.scope").read_text().strip(), "global")

        # 3. Arbitrary 6GHz STA without reconnect.key -> auto = wifi-any
        res_6, task_6 = self._run_native_gate_fixture(
            wifi_status='Wifi is enabled\nWifi is connected to "Guest-6G"\nSupplicant state: COMPLETED\nFrequency: 6135MHz\n',
        )
        self.assertTrue((task_6 / "reached_hostapd").is_file(), (res_6.returncode, res_6.stderr + res_6.stdout))
        self.assertEqual((task_6 / "upstream.mode").read_text().strip(), "wifi-any")
        self.assertEqual((task_6 / "tether.scope").read_text().strip(), "global")

        # 4. 5GHz AP rejects 5GHz STA (sta_band_incompatible_with_5g), accepts 2.4GHz STA
        bad_5g, bad_task = self._run_native_gate_fixture(
            band="5g",
            wifi_status='Wifi is enabled\nWifi is connected to "Any5G"\nSupplicant state: COMPLETED\nFrequency: 5180MHz\n',
        )
        self.assertEqual(bad_5g.returncode, 69)
        self.assertFalse((bad_task / "reached_hostapd").exists())
        self.assertIn("sta_band_incompatible_with_5g", (bad_task / "failure").read_text())

        ok_5g, ok_task = self._run_native_gate_fixture(
            band="5g",
            wifi_status='Wifi is enabled\nWifi is connected to "Any24G"\nSupplicant state: COMPLETED\nFrequency: 2437MHz\n',
        )
        self.assertTrue((ok_task / "reached_hostapd").is_file())
        self.assertEqual((ok_task / "upstream.mode").read_text().strip(), "wifi-any")

        # 5. Unreviewed .ko or framework-tethering.jar hash fails before hostapd
        bad_ko, bad_ko_task = self._run_native_gate_fixture(ko_sha="0" * 64)
        self.assertEqual(bad_ko.returncode, 69)
        self.assertFalse((bad_ko_task / "reached_hostapd").exists())
        bad_jar, bad_jar_task = self._run_native_gate_fixture(jar_sha="0" * 64)
        self.assertEqual(bad_jar.returncode, 69)
        self.assertFalse((bad_jar_task / "reached_hostapd").exists())
        # Cross-device .ko (dijun hash on violin) must be rejected too
        cross_ko, cross_task = self._run_native_gate_fixture(
            device="violin",
            ko_sha="54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea",
        )
        self.assertEqual(cross_ko.returncode, 69)
        self.assertFalse((cross_task / "reached_hostapd").exists())

    def test_status_snapshot_v4_dynamic_readiness(self):
        td = tempfile.TemporaryDirectory(prefix="mt6639-snapshot-v4-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        mod = root / "mod"
        shutil.copytree(MOD, mod)
        private = root / "private"
        private.mkdir(mode=0o700)
        (private / "ap.key").write_text("secret\n")
        (private / "ap.key").chmod(0o600)
        misc = root / "misc"
        misc.mkdir(mode=0o755)
        boot = root / "boot"
        boot.write_text(BOOT_ID + "\n")
        bindir = root / "bin"
        bindir.mkdir()
        snap = mod / "bin/status_snapshot.sh"
        src = (MOD / "bin/status_snapshot.sh").read_text()
        src = (
            src.replace("/data/adb/modules/mt6639-hotspot", str(mod))
            .replace("/data/adb/mt6639-hotspot/private", str(private))
            .replace("/data/misc", str(misc))
            .replace("/proc/sys/kernel/random/boot_id", str(boot))
        )
        snap.write_text(src)
        for name, body in {
            "id": "#!/bin/sh\necho 0\n",
            "getenforce": "#!/bin/sh\necho Enforcing\n",
            "stat": '#!/bin/sh\ncase "$2" in %u) echo 0 ;; %a) exec /usr/bin/stat -c %a "$3" ;; esac\n',
            "cmd": '#!/bin/sh\nprintf "%s" "$FIXTURE_WIFI"\n',
        }.items():
            p = bindir / name
            p.write_text(body)
            p.chmod(0o755)
        cases = [
            # (wifi_status, ready_6g, ready_5g, ready_2g, local_ready)
            ("Wifi is enabled\n", "1", "1", "1", "1"),
            ('Wifi is enabled\nWifi is connected to "Guest"\nSupplicant state: COMPLETED\nFrequency: 6135MHz\n', "1", "0", "1", "0"),
            ('Wifi is enabled\nWifi is connected to "Guest2"\nSupplicant state: COMPLETED\nFrequency: 2412MHz\n', "0", "1", "0", "0"),
            ('Wifi is enabled\nWifi is connected to "Guest5"\nSupplicant state: COMPLETED\nFrequency: 5180MHz\n', "0", "0", "1", "0"),
            ("Wifi is disabled\n", "0", "0", "0", "0"),
        ]
        for wifi_out, r6, r5, r2, rl in cases:
            with self.subTest(wifi=wifi_out.strip()):
                env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin", FIXTURE_WIFI=wifi_out)
                res = subprocess.run(["sh", str(snap), str(mod), "init"], env=env, capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stderr)
                content = (misc / "mt6639-hotspot/status").read_text()
                self.assertIn("schema=mt6639-hotspot-status-v4\n", content)
                self.assertIn("reconnect_6g=0\n", content)
                self.assertIn(f"ready_6g={r6}\n", content)
                self.assertIn(f"ready_5g={r5}\n", content)
                self.assertIn(f"ready_2g={r2}\n", content)
                self.assertIn(f"local_ready={rl}\n", content)

    def test_cross_boot_reconcile_and_worker_idle_snapshot_refresh(self):
        td = tempfile.TemporaryDirectory(prefix="mt6639-hs-reconcile-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        mod = root / "mod"
        shutil.copytree(MOD, mod)
        work = root / "work"
        session = work / "session"
        session.mkdir(parents=True)
        task_dir = root / "task-old"
        task_dir.mkdir()
        (session / "task").write_text(str(task_dir) + "\n")
        (session / "boot").write_text("22222222-2222-4222-8222-222222222222\n")
        (work / "state").write_text("active\n")
        misc = root / "misc"
        misc.mkdir(parents=True)
        boot_f = root / "boot_id"
        boot_f.write_text(BOOT_ID + "\n")
        mtap0 = root / "mtap0"

        ctrl_src = (MOD / "bin/control.sh").read_text()
        ctrl_src = (
            ctrl_src.replace("case \"$MODDIR\" in /data/adb/modules/mt6639-hotspot) ;; *) exit 64 ;; esac", "")
            .replace("WORK=/data/adb/mt6639-hotspot", f"WORK={work}")
            .replace("/data/local/tmp/mt6639-native-ap-", str(root / "task-"))
            .replace("/proc/sys/kernel/random/boot_id", str(boot_f))
            .replace("/sys/class/net/mtap0", str(mtap0))
            .replace("for p in /proc/[0-9]*;", "for p in ;")
            .replace("[ \"$(id -u)\" = 0 ] && [ \"$(id -Z)\" = u:r:ksu:s0 ] || exit 77", "")
        )
        ctrl_test = mod / "bin/control.sh"
        ctrl_test.write_text(ctrl_src)
        snap_src = (MOD / "bin/status_snapshot.sh").read_text()
        snap_src = (
            snap_src.replace("case \"$MODDIR\" in /data/adb/modules/mt6639-hotspot) ;; *) exit 64 ;; esac", "")
            .replace("[ \"$(id -u)\" = 0 ] || exit 77", "")
            .replace("/data/adb/mt6639-hotspot/private", str(work / "private"))
            .replace("/data/misc", str(misc))
            .replace("/proc/sys/kernel/random/boot_id", str(boot_f))
        )
        (mod / "bin/status_snapshot.sh").write_text(snap_src)

        bindir = root / "bin"
        bindir.mkdir()
        for name, body in {
            "stat": '#!/bin/sh\ncase "$2" in %u) echo 0 ;; %a) exec /usr/bin/stat -c %a "$3" ;; %u:%a) printf \'0:%s\\n\' "$(/usr/bin/stat -c %a "$3")" ;; esac\n',
            "getenforce": "#!/bin/sh\necho Enforcing\n",
            "cmd": "#!/bin/sh\necho 'Wifi is enabled'\n",
            "dumpsys": "#!/bin/sh\necho '    Upstream wanted: false'\n",
            "restorecon": "#!/bin/sh\nexit 0\n",
        }.items():
            p = bindir / name
            p.write_text(body)
            p.chmod(0o755)
        env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")

        # If mtap0 is still present across boot, reconcile rejects fail-closed (rc=78) and keeps authority
        mtap0.write_text("")
        res_busy = subprocess.run(["sh", str(ctrl_test), str(mod), "reconcile"], env=env, capture_output=True, text=True)
        self.assertEqual(res_busy.returncode, 78, res_busy.stdout + res_busy.stderr)
        self.assertTrue((session / "task").exists())

        # Once proven idle across boot, reconcile archives old session to history/ and restores idle snapshot
        mtap0.unlink()
        res_ok = subprocess.run(["sh", str(ctrl_test), str(mod), "reconcile"], env=env, capture_output=True, text=True)
        self.assertEqual(res_ok.returncode, 0, res_ok.stdout + res_ok.stderr)
        self.assertFalse(session.exists())
        self.assertEqual((work / "state").read_text().strip(), "idle")
        self.assertEqual(len(list((work / "history").glob("*/task"))), 1)
        self.assertIn("state=idle\n", (misc / "mt6639-hotspot/status").read_text())

        # Verify request_worker.sh has refresh_idle_snapshot wired under action.lock
        worker_src = (MOD / "bin/request_worker.sh").read_text()
        self.assertIn("refresh_idle_snapshot()", worker_src)
        self.assertIn('flock -n 9 9>&9', worker_src)


if __name__ == "__main__":
    unittest.main()
