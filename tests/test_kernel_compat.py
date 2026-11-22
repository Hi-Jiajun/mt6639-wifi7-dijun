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
            "getprop": '#!/bin/sh\necho "${FIXTURE_DEVICE:-dijun}"\n',
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

    def identity(self, row, *, device=None, cfg=None, ko=None, kernel=None, action="require_identity"):
        return subprocess.run(
            ["sh", "-c", '. "$MODDIR/bin/lib.sh"; ' + action],
            env=dict(self.env, FIXTURE_DEVICE=device or row[1].split(",")[0],
                     FIXTURE_KERNEL=kernel or row[2],
                     FIXTURE_CFG=cfg or row[3], FIXTURE_KO=ko or row[4]),
            capture_output=True, text=True, timeout=10)

    def test_known_kernel_pairs_use_exact_production_hashes(self):
        for row in ROWS:
            with self.subTest(row=row[0]):
                result = self.identity(row, action='require_identity && echo "$COMPAT_PROFILE"')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(row[0], result.stdout)

    def test_swapping_known_drivers_between_kernels_is_rejected(self):
        for row, other in ((ROWS[0], ROWS[1]), (ROWS[1], ROWS[0])):
            result = self.identity(row, ko=other[4])
            self.assertEqual(result.returncode, 3)
            self.assertIn("REJECT ko_not_stock", result.stdout)

    def test_reviewed_variant_selects_exact_hash_for_all_later_checks(self):
        row = ROWS[1]
        result = self.identity(row, ko=row[5], action=
                               'require_identity && echo "selected=$STOCK_KO_SHA"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selected=" + row[5], result.stdout)
        # The variant is kernel-bound, not a global list of accepted binaries.
        self.assertEqual(self.identity(ROWS[0], ko=row[5]).returncode, 3)

    def test_malformed_and_duplicate_variant_hashes_reject_table(self):
        table = self.mod / "compatibility.conf"
        original = table.read_text()
        for value in ("unknown", ROWS[1][4]):
            table.write_text(original.replace(ROWS[1][5], value))
            result = self.identity(ROWS[1])
            self.assertEqual(result.returncode, 3)
            self.assertIn("compatibility_table_invalid", result.stdout)

    def test_optional_device_sample_matches_reviewed_variant(self):
        driver = os.environ.get("MT6639_DEVICE_DRIVER")
        cfg = os.environ.get("MT6639_DEVICE_CFG")
        if not driver or not cfg:
            self.skipTest("provide private user driver/config samples")
        self.assertEqual(hashlib.sha256(Path(driver).read_bytes()).hexdigest(), ROWS[1][5])
        self.assertEqual(hashlib.sha256(Path(cfg).read_bytes()).hexdigest(), ROWS[1][3])

    def test_unknown_or_suffix_changed_kernel_is_rejected(self):
        for kernel in ("6.18.21-android17-unknown", ROWS[1][2] + "-custom"):
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
        self.env.update(FIXTURE_DEVICE=ROWS[1][1], FIXTURE_KERNEL=ROWS[1][2],
                        FIXTURE_CFG=ROWS[1][3], FIXTURE_KO=ROWS[1][4])
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
        for row, sample in zip(ROWS[:2], ("os4-current", "os4-stable")):
            base = Path(samples) / sample / "analysis"
            for rel, expected in (("odm/firmware/wifi.cfg", row[3]),
                                  ("vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko", row[4])):
                self.assertEqual(hashlib.sha256((base / rel).read_bytes()).hexdigest(), expected)

    def test_original_control_enable_restore_reuses_selected_tuple(self):
        # Reuse the existing lifecycle double, keeping the real entrypoints.
        # Production paths are redirected only at sha256sum/cp I/O boundaries.
        from test_mt6639_control import ControlTests
        variants = [(row, sample) for row, sample in zip(ROWS[:2], ("os4-current", "os4-stable"))]
        variant = ROWS[1][:5]
        variant[4] = ROWS[1][5]
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
                    text = table.read_text().replace(row[3], hashlib.sha256(cfg.read_bytes()).hexdigest())
                    text = text.replace(row[4], hashlib.sha256(ko.read_bytes()).hexdigest())
                    table.write_text(text)
                stub = fixture.root / "stub"
                helpers = {
                    "stat": PRODUCTION_WORK_STAT,
                    "getprop": f'#!/bin/sh\necho "{row[1].split(",")[0]}"\n',
                    "uname": f'#!/bin/sh\necho "{row[2]}"\n',
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
        env = dict(self.env, FIXTURE_DEVICE=row[1], FIXTURE_KERNEL=row[2], FIXTURE_CFG=row[3], FIXTURE_KO=row[4],
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
        for ko_sha in (row[4], row[5]):
            result = subprocess.run(["sh", str(p), "restored"],
                                    env=dict(env, FIXTURE_MODDIR=str(self.mod), FIXTURE_KO=ko_sha),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("compatibility_profile=os4-stable", result.stdout)
            self.assertIn("RESULT fail=0", result.stdout)

    def test_shared_os4_gki_kernel_disambiguates_dijun_violin_jinghu_by_device(self):
        dijun_row, violin_row, jinghu_row = ROWS[1], ROWS[2], ROWS[3]
        self.assertEqual(dijun_row[2], violin_row[2])
        self.assertEqual(dijun_row[2], jinghu_row[2])
        for row in (dijun_row, violin_row, jinghu_row):
            res = self.identity(row, action='require_identity && echo "$COMPAT_PROFILE"')
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn(row[0], res.stdout)
        # Mixing violin firmware/driver under dijun or jinghu device fails closed.
        for dev in ("dijun", "jinghu"):
            bad_cfg = self.identity(violin_row, device=dev, ko=dijun_row[4] if dev == "dijun" else jinghu_row[4])
            self.assertEqual(bad_cfg.returncode, 3)
            self.assertIn("REJECT cfg_not_stock", bad_cfg.stdout)
            bad_ko = self.identity(violin_row, device=dev, cfg=dijun_row[3] if dev == "dijun" else jinghu_row[3])
            self.assertEqual(bad_ko.returncode, 3)
            self.assertIn("REJECT ko_not_stock", bad_ko.stdout)

    def test_unknown_device_is_rejected_and_status_card_reports_wrong_device(self):
        row = ROWS[2]
        res = self.identity(row, device="other")
        self.assertEqual(res.returncode, 3)
        self.assertIn("REJECT wrong_device", res.stdout)
        template = self.mod / "module.prop.template"
        shutil.copyfile(self.mod / "module.prop", template)
        env = dict(self.env, FIXTURE_DEVICE="other", FIXTURE_KERNEL=row[2],
                   FIXTURE_CFG=row[3], FIXTURE_KO=row[4])
        card = subprocess.run(["sh", str(self.mod / "bin/status-card.sh"), str(self.mod), "show"],
                              env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(card.returncode, 0, card.stderr)
        self.assertIn("[设备不匹配]", card.stdout)

    def test_legacy_66_kernel_dispatches_shell_fallback_enable_and_disable(self):
        from test_mt6639_control import ControlTests
        row = ROWS[4]
        self.assertTrue(row[2].startswith("6.6."))
        fixture = ControlTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        env = fixture.env
        stock_cfg = fixture.root / "stock.cfg"
        cfg = Path(env["CFG"])
        shutil.copyfile(cfg, stock_cfg)
        stub = fixture.root / "stub"
        mounts = Path(env["MOUNTINFO_FILE"])
        # Prove bin/mount-txn is never touched or executed on Linux 6.6.*.
        missing_native = fixture.root / "absent-mount-txn"
        env["NATIVE"] = str(missing_native)
        env["MT6639_LEGACY_66"] = "1"
        env["KERNEL_GUARD"] = row[2]
        env["FIXTURE_STOCK_CFG"] = str(stock_cfg)
        env["FIXTURE_MOUNTS"] = str(mounts)
        helpers = {
            "uname": f'#!/bin/sh\necho "{row[2]}"\n',
            "getprop": f'#!/bin/sh\necho "{row[1]}"\n',
            "chcon": "#!/bin/sh\nexit 0\n",
            "sleep": '''#!/bin/sh
case "${1:-1}" in
    1) exec /bin/sleep 0.01 ;;
    *) exit 0 ;;
esac
''',
            "ip": '''#!/bin/sh
if [ "${FIXTURE_NO_IP:-0}" = 1 ]; then
    exit 0
fi
echo '2: wlan0    inet 198.51.100.50/24 brd 198.51.100.255 scope global wlan0'
''',
            "runcon": '''#!/bin/sh
if [ "$1" = "u:r:shell:s0" ] && [ "$2" = sh ] && [ "$3" = -c ]; then
    cmd=$4
    case "$cmd" in
        *dumpsys*mOverrideCountryCode*)
            echo "  mOverrideCountryCode: null"
            exit 0 ;;
        *settings*wifi_scan_always_enabled*)
            echo 1
            exit 0 ;;
        *settings*wifi_on*)
            echo 1
            exit 0 ;;
        *settings*put*)
            exit 0 ;;
        *"cmd wifi"*"get-country-code"*)
            echo "Wifi Country Code = CN"
            exit 0 ;;
        *"cmd wifi"*"status"*)
            if [ "${FIXTURE_WIFI_CONNECTED:-1}" = 1 ]; then
                echo 'Wifi is connected to "MockSSID"'
            else
                echo 'Wifi is enabled'
            fi
            exit 0 ;;
        *"cmd wifi"*)
            exit 0 ;;
    esac
fi
exit 0
''',
            "mount": '''#!/bin/sh
if [ "$1" = --bind ]; then
    cp "$2" "$3" || exit 1
    printf '9 1 0:1 /candidate %s rw - tmpfs tmpfs rw\n' "$3" > "$FIXTURE_MOUNTS"
    exit 0
fi
exit 0
''',
            "umount": '''#!/bin/sh
target=${1:-}
[ "$target" != -l ] || target=$2
cp "$FIXTURE_STOCK_CFG" "$target" || exit 1
: > "$FIXTURE_MOUNTS"
exit 0
''',
        }
        for name, body in helpers.items():
            p = stub / name
            p.write_text(body)
            p.chmod(0o755)
        enabled = fixture.control("enable")
        self.assertEqual(enabled.returncode, 0, enabled.stdout)
        tx = Path((fixture.work / "active_tx").read_text().strip())
        self.assertTrue((tx / "legacy-66").is_file())
        self.assertEqual(cfg.read_text(), "CountryCode=CN\nDisallowBand6G 0\n")
        self.assertEqual((fixture.work / "state").read_text().strip(), "active")
        disabled = fixture.control("disable")
        self.assertEqual(disabled.returncode, 0, disabled.stdout)
        self.assertEqual((fixture.work / "state").read_text().strip(), "restored")
        self.assertEqual((fixture.work / "active_tx").read_text(), "")
        self.assertEqual(cfg.read_text(), "CountryCode=CN\nDisallowBand6G 1\n")
        self.assertIn('"restoration":"restored"', (tx / "guardian.log").read_text())

        # Baseline-aware restore when Wi-Fi is enabled but disconnected (pure local tablet baseline).
        env["FIXTURE_WIFI_CONNECTED"] = "0"
        env["FIXTURE_NO_IP"] = "1"
        en_disc = fixture.control("enable")
        self.assertEqual(en_disc.returncode, 0, en_disc.stdout)
        tx2 = Path((fixture.work / "active_tx").read_text().strip())
        self.assertIn("connected_before=0", (tx2 / "baseline.env").read_text())
        dis_disc = fixture.control("disable")
        self.assertEqual(dis_disc.returncode, 0, dis_disc.stdout)
        self.assertEqual((fixture.work / "state").read_text().strip(), "restored")
        self.assertIn('"restoration":"restored"', (tx2 / "guardian.log").read_text())
        self.assertTrue((tx2 / "wlan-net-ready").is_file())

    def test_country_option_persistence_preserve_6g_region_and_exact_restore(self):
        from test_mt6639_control import ControlTests
        row = ROWS[4]
        fixture = ControlTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        env = fixture.env
        stock_cfg = fixture.root / "stock.cfg"
        shutil.copyfile(Path(env["CFG"]), stock_cfg)
        stub = fixture.root / "stub"
        wifi_log = fixture.root / "wifi.calls"
        mounts = Path(env["MOUNTINFO_FILE"])
        env["NATIVE"] = str(fixture.root / "absent-mount-txn")
        env["MT6639_LEGACY_66"] = "1"
        env["KERNEL_GUARD"] = row[2]
        env["FIXTURE_STOCK_CFG"] = str(stock_cfg)
        env["FIXTURE_MOUNTS"] = str(mounts)
        env["FIXTURE_WIFI_LOG"] = str(wifi_log)
        for name, body in {
            "uname": f'#!/bin/sh\necho "{row[2]}"\n',
            "getprop": f'#!/bin/sh\necho "{row[1]}"\n',
            "chcon": "#!/bin/sh\nexit 0\n",
            "sleep": '''#!/bin/sh
case "${1:-1}" in
    1) exec /bin/sleep 0.02 ;;
    *) exit 0 ;;
esac
''',
            "ip": "#!/bin/sh\necho '2: wlan0    inet 198.51.100.50/24 scope global wlan0'\n",
            "mount": '#!/bin/sh\nif [ "$1" = --bind ]; then cp "$2" "$3" && printf "9 1 0:1 /c %s rw - tmpfs tmpfs rw\\n" "$3" > "$FIXTURE_MOUNTS"; fi\n',
            "umount": '#!/bin/sh\nt=${1:-}; [ "$t" != -l ] || t=$2; cp "$FIXTURE_STOCK_CFG" "$t" && : > "$FIXTURE_MOUNTS"\n',
            "runcon": '''#!/bin/sh
cmd=$4
case "$cmd" in
    *dumpsys*mOverrideCountryCode*) echo "  mOverrideCountryCode: ${FIXTURE_OVERRIDE:-null}"; exit 0 ;;
    *settings*wifi_scan_always_enabled*|*settings*wifi_on*) echo 1; exit 0 ;;
    *settings*put*) exit 0 ;;
    *"cmd wifi"*"get-country-code"*) echo "Wifi Country Code = ${FIXTURE_COUNTRY:-CN}"; exit 0 ;;
    *"cmd wifi"*"status"*) echo 'Wifi is connected to "MockSSID"'; exit 0 ;;
    *"cmd wifi"*) echo "$cmd" >> "$FIXTURE_WIFI_LOG"; exit 0 ;;
esac
exit 0
''',
        }.items():
            p = stub / name
            p.write_text(body)
            p.chmod(0o755)

        # Reject invalid/CN target country, accept JP -> persisted 0600 country.conf
        self.assertEqual(fixture.run_entry("bin/control.sh", str(fixture.mod), "set-country", "CN").returncode, 64)
        self.assertEqual(fixture.run_entry("bin/control.sh", str(fixture.mod), "set-country", "JP").returncode, 0)
        self.assertIn("TARGET_COUNTRY=JP", fixture.control("get-country").stdout)
        self.assertEqual((fixture.work / "country.conf").stat().st_mode & 0o777, 0o600)

        # Case 1: baseline=TW override under target JP -> applies JP, restores exact override TW
        env["FIXTURE_COUNTRY"] = "TW"
        env["FIXTURE_OVERRIDE"] = "TW"
        wifi_log.write_text("")
        en1 = fixture.control("enable")
        self.assertEqual(en1.returncode, 0, en1.stdout)
        tx_tw = Path((fixture.work / "active_tx").read_text().strip())
        self.assertIn("target_country=JP", (tx_tw / "baseline.env").read_text())
        dis1 = fixture.control("disable")
        self.assertEqual(dis1.returncode, 0, dis1.stdout)
        calls_tw = wifi_log.read_text()
        self.assertIn("force-country-code' 'enabled' 'JP'", calls_tw)
        self.assertIn("force-country-code' 'enabled' 'TW'", calls_tw)
        self.assertNotIn("'CN'", calls_tw)

        # Case 2: baseline=00 (no override) under target auto -> applies US, restores disabled
        (fixture.work / "country.conf").unlink()
        env["FIXTURE_COUNTRY"] = "00"
        env["FIXTURE_OVERRIDE"] = "null"
        wifi_log.write_text("")
        en2 = fixture.control("enable")
        self.assertEqual(en2.returncode, 0, en2.stdout)
        dis2 = fixture.control("disable")
        self.assertEqual(dis2.returncode, 0, dis2.stdout)
        calls_00 = wifi_log.read_text()
        self.assertIn("force-country-code' 'enabled' 'US'", calls_00)
        self.assertIn("force-country-code' 'disabled'", calls_00)

        # Case 3: third-party hook already set US under auto -> never calls force-country-code
        env["FIXTURE_COUNTRY"] = "US"
        env["FIXTURE_OVERRIDE"] = "null"
        wifi_log.write_text("")
        en3 = fixture.control("enable")
        self.assertEqual(en3.returncode, 0, en3.stdout)
        tx_us = Path((fixture.work / "active_tx").read_text().strip())
        self.assertIn("country_mutated=0", (tx_us / "baseline.env").read_text())
        dis3 = fixture.control("disable")
        self.assertEqual(dis3.returncode, 0, dis3.stdout)
        self.assertNotIn("force-country-code", wifi_log.read_text())


if __name__ == "__main__":
    unittest.main()
