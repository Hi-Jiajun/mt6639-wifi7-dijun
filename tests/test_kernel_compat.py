"""Production tuple selection and stock identity gates, no real device I/O."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "modules/mt6639-6g"
ROWS = [line.split() for line in (MODULE / "compatibility.conf").read_text().splitlines()
        if line and not line.startswith("#")]

# These fixtures already model root via id. Model only the host WORK owner
# at the stat boundary too, so public non-root CI keeps exercising production
# mode and its real chmod/mode verification. Native ancestor tests use real IDs.
PRODUCTION_WORK_STAT = '''#!/bin/sh
if [ "$#" = 3 ] && [ "$1" = -c ]; then
    case "$3" in
        "$WORK"|"$WORK"/*)
            case "$2" in
                %u) echo 0; exit 0 ;;
                %u:%a) mode=$(/usr/bin/stat -c %a "$3") || exit 1; echo "0:$mode"; exit 0 ;;
            esac
            ;;
    esac
fi
exec /usr/bin/stat "$@"
'''


class KernelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-compat-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mod = self.root / "module"
        shutil.copytree(MODULE, self.mod)
        self.stub = self.root / "stub"
        self.stub.mkdir()
        self.calls = self.root / "calls"
        self.calls.write_text("")
        scripts = {
            "uname": '#!/bin/sh\necho "$FIXTURE_KERNEL"\n',
            "id": '#!/bin/sh\n[ "$1" = -u ] && echo 0 || echo u:r:ksu:s0\n',
            "sha256sum": '''#!/bin/sh
case "$1" in
    /odm/firmware/wifi.cfg) echo "$FIXTURE_CFG  $1" ;;
    /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko|/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko)
        echo "$FIXTURE_KO  $1" ;;
    *) exec /usr/bin/sha256sum "$@" ;;
esac
''',
            "getenforce": '#!/bin/sh\necho Enforcing\n',
            "stat": PRODUCTION_WORK_STAT,
        }
        for name, body in scripts.items():
            path = self.stub / name
            path.write_text(body)
            path.chmod(0o755)
        self.env = dict(os.environ, MODDIR=str(self.mod), WORK=str(self.root / "work"),
                        PATH=f"{self.stub}:{os.environ['PATH']}")
        self.env.pop("MT6639_TEST_MODE", None)

    def identity(self, row, *, cfg=None, ko=None, kernel=None, action="require_identity"):
        return subprocess.run(
            ["sh", "-c", '. "$MODDIR/bin/lib.sh"; ' + action],
            env=dict(self.env, FIXTURE_KERNEL=kernel or row[1],
                     FIXTURE_CFG=cfg or row[2], FIXTURE_KO=ko or row[3]),
            capture_output=True, text=True, timeout=10)

    def test_known_kernel_pairs_use_exact_production_hashes(self):
        for row in ROWS:
            with self.subTest(row=row[0]):
                result = self.identity(row, action='require_identity && echo "$COMPAT_PROFILE"')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(row[0], result.stdout)

    def test_swapping_known_drivers_between_kernels_is_rejected(self):
        for row, other in ((ROWS[0], ROWS[1]), (ROWS[1], ROWS[0])):
            result = self.identity(row, ko=other[3])
            self.assertEqual(result.returncode, 3)
            self.assertIn("REJECT ko_not_stock", result.stdout)

    def test_reviewed_variant_selects_exact_hash_for_all_later_checks(self):
        row = ROWS[1]
        result = self.identity(row, ko=row[4], action=
                               'require_identity && echo "selected=$STOCK_KO_SHA"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selected=" + row[4], result.stdout)
        # The variant is kernel-bound, not a global list of accepted binaries.
        self.assertEqual(self.identity(ROWS[0], ko=row[4]).returncode, 3)

    def test_malformed_and_duplicate_variant_hashes_reject_table(self):
        table = self.mod / "compatibility.conf"
        original = table.read_text()
        for value in ("unknown", ROWS[1][3]):
            table.write_text(original.replace(ROWS[1][4], value))
            result = self.identity(ROWS[1])
            self.assertEqual(result.returncode, 3)
            self.assertIn("compatibility_table_invalid", result.stdout)

    def test_optional_device_sample_matches_reviewed_variant(self):
        driver = os.environ.get("MT6639_DEVICE_DRIVER")
        cfg = os.environ.get("MT6639_DEVICE_CFG")
        if not driver or not cfg:
            self.skipTest("provide private user driver/config samples")
        self.assertEqual(hashlib.sha256(Path(driver).read_bytes()).hexdigest(), ROWS[1][4])
        self.assertEqual(hashlib.sha256(Path(cfg).read_bytes()).hexdigest(), ROWS[1][2])

    def test_unknown_or_suffix_changed_kernel_is_rejected(self):
        for kernel in ("6.18.21-android17-unknown", ROWS[1][1] + "-custom"):
            result = self.identity(ROWS[1], kernel=kernel)
            self.assertEqual(result.returncode, 3)
            self.assertIn("REJECT kernel_mismatch", result.stdout)

    def test_modified_config_and_unknown_driver_are_rejected(self):
        for overrides, expected in (({"cfg": "0" * 64}, "cfg_not_stock"),
                                    ({"ko": "1" * 64}, "ko_not_stock")):
            result = self.identity(ROWS[1], **overrides)
            self.assertEqual(result.returncode, 3)
            self.assertIn(expected, result.stdout)

    def test_invalid_and_duplicate_table_cannot_silently_select(self):
        table = self.mod / "compatibility.conf"
        original = table.read_text()
        for extra in ("broken row\n", " ".join(ROWS[1]) + "\n",
                      "other " + " ".join(ROWS[0][1:]) + "\n"):
            with self.subTest(extra=extra):
                table.write_text(original + extra)
                result = self.identity(ROWS[1])
                self.assertEqual(result.returncode, 3)
                self.assertIn("compatibility_table_invalid", result.stdout)

    def test_missing_or_symlink_table_is_rejected(self):
        table = self.mod / "compatibility.conf"
        copy = self.root / "table"
        shutil.copyfile(table, copy)
        table.unlink()
        self.assertEqual(self.identity(ROWS[1]).returncode, 3)
        table.symlink_to(copy)
        self.assertEqual(self.identity(ROWS[1]).returncode, 3)

    def test_compat_table_cannot_execute_shell(self):
        marker = self.root / "executed"
        with (self.mod / "compatibility.conf").open("a") as f:
            f.write(f"$(touch {marker})\n")
        self.assertEqual(self.identity(ROWS[1]).returncode, 3)
        self.assertFalse(marker.exists())

    def test_environment_cannot_override_production_profile(self):
        self.env.update(KERNEL_GUARD="unknown", STOCK_KO_SHA="f" * 64,
                        STOCK_CFG_SHA="e" * 64)
        self.assertEqual(self.identity(ROWS[1]).returncode, 0)

    def test_status_uses_new_profile_and_observation_never_enables_radio(self):
        template = self.mod / "module.prop.template"
        shutil.copyfile(self.mod / "module.prop", template)
        # Keep the production status path; no transaction files are present.
        self.env.update(FIXTURE_KERNEL=ROWS[1][1], FIXTURE_CFG=ROWS[1][2], FIXTURE_KO=ROWS[1][3])
        # An absent iw is legitimate. A present one must only observe and fail.
        iw = self.stub / "iw"
        iw.write_text('#!/bin/sh\necho "$*" >> "$CARD_CALLS"\nexit 1\n')
        iw.chmod(0o755)
        self.env["CARD_CALLS"] = str(self.calls)
        result = subprocess.run(["sh", str(self.mod / "bin/status-card.sh"), str(self.mod), "show"],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("适配：os4-stable", result.stdout)
        self.assertIn("内核：匹配", result.stdout)
        self.assertIn("驱动：匹配", result.stdout)
        self.assertIn("事务：空闲", result.stdout)
        self.assertEqual(self.calls.read_text(), "dev\n")

    def test_optional_real_stock_samples_match_production_table(self):
        samples = os.environ.get("MT6639_STOCK_SAMPLES")
        if not samples:
            self.skipTest("set MT6639_STOCK_SAMPLES to the private extracted firmware samples")
        for row, sample in zip(ROWS, ("os4-current", "os4-stable")):
            base = Path(samples) / sample / "analysis"
            for rel, expected in (("odm/firmware/wifi.cfg", row[2]),
                                  ("vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko", row[3])):
                self.assertEqual(hashlib.sha256((base / rel).read_bytes()).hexdigest(), expected)

    def test_original_control_enable_restore_reuses_selected_tuple(self):
        # Reuse the existing lifecycle double, keeping the real entrypoints.
        # Production paths are redirected only at sha256sum/cp I/O boundaries.
        from test_mt6639_control import ControlTests
        variants = [(row, sample) for row, sample in zip(ROWS, ("os4-current", "os4-stable"))]
        variant = ROWS[1][:4]
        variant[3] = ROWS[1][4]
        variants.append((variant, "device"))
        for row, sample in variants:
            with self.subTest(profile=row[0]):
                fixture = ControlTests(methodName="runTest")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                env = fixture.env
                env.pop("MT6639_TEST_MODE")
                cfg, ko = Path(env["CFG"]), Path(env["STOCK_KO"])
                samples = os.environ.get("MT6639_STOCK_SAMPLES")
                if sample == "device" and os.environ.get("MT6639_DEVICE_DRIVER"):
                    shutil.copyfile(os.environ["MT6639_DEVICE_CFG"], cfg)
                    shutil.copyfile(os.environ["MT6639_DEVICE_DRIVER"], ko)
                elif samples and sample != "device":
                    base = Path(samples) / sample / "analysis"
                    shutil.copyfile(base / "odm/firmware/wifi.cfg", cfg)
                    shutil.copyfile(base / "vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko", ko)
                else:
                    # Public CI uses small fixture bytes. Actual stock hashes
                    # and original unedited table are checked independently.
                    table = fixture.mod / "compatibility.conf"
                    text = table.read_text().replace(row[2], hashlib.sha256(cfg.read_bytes()).hexdigest())
                    text = text.replace(row[3], hashlib.sha256(ko.read_bytes()).hexdigest())
                    table.write_text(text)
                stub = fixture.root / "stub"
                helpers = {
                    "stat": PRODUCTION_WORK_STAT,
                    "uname": f'#!/bin/sh\necho "{row[1]}"\n',
                    "sha256sum": '''#!/bin/sh
case "$1" in
    /odm/firmware/wifi.cfg) exec /usr/bin/sha256sum "$FIXTURE_CFG_PATH" ;;
    /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko|/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko)
        exec /usr/bin/sha256sum "$FIXTURE_KO_PATH" ;;
    *) exec /usr/bin/sha256sum "$@" ;;
esac
''',
                    "cp": '''#!/bin/sh
case "$1" in
    /odm/firmware/wifi.cfg) exec /bin/cp "$FIXTURE_CFG_PATH" "$2" ;;
    /vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko|/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko)
        exec /bin/cp "$FIXTURE_KO_PATH" "$2" ;;
    *) exec /bin/cp "$@" ;;
esac
''',
                }
                for name, body in helpers.items():
                    p = stub / name
                    p.write_text(body)
                    p.chmod(0o755)
                # need_native's production path is fixed inside this copied
                # module; reuse the existing bounded executor double there.
                shutil.copyfile(Path(env["NATIVE"]), fixture.mod / "bin/mount-txn")
                env.update(FIXTURE_CFG_PATH=str(cfg), FIXTURE_KO_PATH=str(ko))
                result = fixture.run_entry("bin/control.sh", str(fixture.mod), "enable")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn(f"compatibility profile={row[0]}", result.stdout)
                self.assertEqual((fixture.work / "state").read_text().strip(), "active")
                self.assertTrue((fixture.work / "active_tx").read_text().strip())
                backups = list(fixture.work.glob("backup-*/wlan.ko.stock"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), ko.read_bytes())
                result = fixture.run_entry("bin/control.sh", str(fixture.mod), "disable")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual((fixture.work / "state").read_text().strip(), "restored")
                self.assertEqual((fixture.work / "active_tx").read_text(), "")

    def test_production_restored_checker_uses_selected_new_driver(self):
        row = ROWS[1]
        shutil.copyfile(self.mod / "module.prop", self.mod / "module.prop.template")
        work = self.root / "work"
        work.mkdir()
        boot = self.root / "boot"
        boot.write_text("11111111-1111-4111-8111-111111111111\n")
        mounts = self.root / "mounts"
        mounts.write_text("")
        (work / "apply.log").write_text("service: boot=11111111 ENABLE_6G=1\n")
        (self.mod / "bin/mount-txn").write_text("fixture, never executed\n")
        (self.mod / "bin/native.sh").write_text('''#!/bin/sh
case "$2" in
    status) printf 'state=restored\nactive_tx=none\n' ;;
    inspect) echo '{"country_code":"CN"}' ;;
    *) exit 99 ;;
esac
''')
        entries = []
        for p in sorted(self.mod.rglob("*")):
            if p.is_file() and p.name not in ("module.prop", "SHA256SUMS"):
                entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.mod)}\n")
        (self.mod / "SHA256SUMS").write_text("".join(entries))
        env = dict(self.env, FIXTURE_KERNEL=row[1], FIXTURE_CFG=row[2], FIXTURE_KO=row[3],
                   BOOT_ID_FILE=str(boot), MOUNTINFO_FILE=str(mounts))
        # Only the fixed module root is redirected; production lib/profile
        # selection and the original checker body stay intact in this fixture.
        checker = (self.mod / "bin/post-reboot-check.sh").read_text()
        checker = checker.replace("    MODDIR=/data/adb/modules/mt6639-6g\n",
                                  '    MODDIR=${FIXTURE_MODDIR:?}\n', 1)
        p = self.mod / "bin/post-reboot-check-fixture.sh"
        p.write_text(checker)
        # Fixture wrapper must itself be bound before checking the tree.
        with (self.mod / "SHA256SUMS").open("a") as f:
            f.write(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  bin/post-reboot-check-fixture.sh\n")
        for ko_sha in (row[3], row[4]):
            result = subprocess.run(["sh", str(p), "restored"],
                                    env=dict(env, FIXTURE_MODDIR=str(self.mod), FIXTURE_KO=ko_sha),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("compatibility_profile=os4-stable", result.stdout)
            self.assertIn("RESULT fail=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
