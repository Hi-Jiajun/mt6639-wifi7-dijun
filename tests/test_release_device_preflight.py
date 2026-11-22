"""Static safety guards for read-only release device preflight."""
from pathlib import Path
import subprocess
import unittest
import tempfile


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/release_device_preflight.sh"


class ReleaseDevicePreflightTests(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_is_read_only(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "setprop", "settings put", "ksud module install",
            "ksud module enable", "ksud module disable", "soft-reboot",
            "am force-stop", "ip link", "iw dev add",
        ):
            self.assertNotIn(forbidden, source)
        for expected in (
            "cmd wifi status", "dumpsys tethering --short",
            "zygisksu/bin/zygiskd status",
            "zygisk_lsposed/lspctl status", "ksud debug info",
            "ksud feature get 0", "su -c id",
            "bluetooth.profile.bap.unicast.client.enabled",
            "com.hiliang.xringroot",
            "c739b07e747b1be2a4f0af9c19eb06674fd895665f9b45a36bc7c5ce6cee9e49",
            "me.weishu.kernelsu",
            "EXPECTED_ROOT_CONTEXT='u:r:ksu:s0'",
            "/sys/fs/selinux/enforce",
            "result PASS kernelsu_su_context uid_0_ksu",
            "EXPECTED_CONCURRENCY_VERSION='0.1.0'",
            "result PASS concurrency_gate_marker absent",
        ):
            self.assertIn(expected, source)

    def test_release_versions_and_path_are_pinned(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mt6639-6g:v0.4.1", source)
        self.assertIn("mt6639-hotspot:v0.3.1", source)
        self.assertIn("mt6639-bt:v0.2.0", source)
        self.assertIn("EXPECTED_KSU_MANAGER_VERSION='v3.3.1'", source)
        self.assertIn("result FAIL kernelsu_su_compat", source)
        self.assertIn("result FAIL concurrency_gate_marker present", source)
        self.assertIn('result PASS "module_${id}_enabled"', source)
        self.assertIn("expected_0.3.0", source)
        self.assertIn("/data/local/tmp/mt6639-release-preflight-*", source)
        self.assertIn('"$(stat -c %a "$D")" = 700', source)

    def test_private_su_fallback_does_not_bypass_domain_requirement(self):
        source = SCRIPT.read_text()
        block = source[source.index("su_rc=127"):source.index("EXPECTED_XRINGROOT_SHA=")]
        for context, expected in (("kernel", "FAIL"), ("ksu", "PASS")):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as td:
                private_su = Path(td) / "su"
                private_su.touch()
                private_su.chmod(0o700)
                fixture = block.replace("/data/adb/ksu/bin/su", str(private_su))
                harness = '''D=$1
command() { return 1; }
timeout() { printf 'uid=0(root) context=u:r:DOMAIN:s0\\n'; }
result() { printf '%s' "$1"; }
'''.replace("DOMAIN", context)
                result = subprocess.run(["sh", "-c", harness + fixture, "sh", td], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual((Path(td) / "ksu.su-path").read_text().strip(), str(private_su))

    def test_tablet_preflight_allows_ksu_fork_and_warns_optional_dijun_checks(self):
        for dev, expect_rc in (("violin", 0), ("dijun", 1)):
            with self.subTest(device=dev), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                stub = root / "stub"
                stub.mkdir()
                d = root / "mt6639-release-preflight-tab"
                d.mkdir(mode=0o700)
                script_copy = root / "preflight.sh"
                txt = SCRIPT.read_text(encoding="utf-8")
                txt = (
                    txt.replace("/data/local/tmp/mt6639-release-preflight-*", str(root / "mt6639-release-preflight-*"))
                    .replace("/sys/fs/selinux/enforce", str(root / "enforce"))
                    .replace("/proc/self/attr/current", str(root / "attr_current"))
                    .replace("/proc/sys/kernel/random/boot_id", str(root / "boot_id"))
                    .replace("/data/adb/ksud", str(stub / "ksud"))
                    .replace("/data/adb/modules/zygisksu/bin/zygiskd", str(stub / "zygiskd"))
                    .replace("/data/adb/modules/zygisk_lsposed/lspctl", str(stub / "lspctl"))
                    .replace("/data/adb/modules/$id", str(root / "modules/$id"))
                    .replace('"/data/adb/modules/$1/module.prop"', f'"{root}/modules/$1/module.prop"')
                    .replace("/data/misc/mt6639-concurrency/gate", str(root / "absent_gate"))
                )
                script_copy.write_text(txt, encoding="utf-8")
                (root / "enforce").write_text("1\n")
                (root / "attr_current").write_text("u:r:ksu:s0\n")
                (root / "boot_id").write_text("11111111-2222-3333-4444-555555555555\n")
                for mod, ver in (("mt6639-6g", "v0.4.1"), ("mt6639-hotspot", "v0.3.1"), ("mt6639-bt", "v0.2.0")):
                    mp = root / "modules" / mod
                    mp.mkdir(parents=True)
                    (mp / "module.prop").write_text(f"id={mod}\nversion={ver}\n")
                helpers = {
                    "id": "#!/bin/sh\necho 0\n",
                    "stat": '#!/bin/sh\ncase "$2" in %u) echo 0 ;; %a) echo 700 ;; esac\n',
                    "getenforce": "#!/bin/sh\necho Enforcing\n",
                    "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
                    "getprop": f'''#!/bin/sh
case "$1" in
  ro.product.device) echo "{dev}" ;;
  ro.build.fingerprint)
    if [ "{dev}" = dijun ]; then
      echo "Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys"
    else
      echo "Xiaomi/{dev}/{dev}:17/CP2A/OS4.0.6.0.XOTCNXM:user/release-keys"
    fi ;;
  bluetooth.profile.*) echo true ;;
esac
''',
                    "pm": '#!/bin/sh\nif [ "$2" = com.rifsxd.ksunext ]; then echo "package:/data/app/ksunext.apk"; exit 0; fi\nexit 1\n',
                    "dumpsys": '''#!/bin/sh
case "$1" in
  package) echo "    versionName=v1.0.9" ;;
  tethering) echo "    Upstream wanted: false" ;;
esac
''',
                    "ksud": '#!/bin/sh\nif [ "$1" = debug ]; then printf "lkm: true\\nlate_load: true\\n"; else printf "Value: 1\\nStatus: enabled\\n"; fi\n',
                    "su": '#!/bin/sh\necho "uid=0(root) context=u:r:ksu:s0"\n',
                    "cmd": '#!/bin/sh\necho "Wifi is enabled"\n',
                    "zygiskd": "#!/bin/sh\necho 'Root: KernelSU'\n",
                    "lspctl": "#!/bin/sh\necho 'System server: connected'\n",
                }
                for name, body in helpers.items():
                    p = stub / name
                    p.write_text(body)
                    p.chmod(0o755)
                res = subprocess.run(
                    ["sh", str(script_copy), str(d)],
                    env={"PATH": f"{stub}:/usr/bin:/bin"},
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(res.returncode, expect_rc, res.stdout + res.stderr)
                if dev == "violin":
                    self.assertIn("WARN\tkernelsu_manager\tcom.rifsxd.ksunext:v1.0.9", res.stdout)
                    self.assertIn("WARN\txringroot_tool\toptional_on_tablet", res.stdout)
                    self.assertIn("WARN\twifi_home_net_6g\tlab_ssid_not_connected", res.stdout)
                    self.assertIn("RESULT fail=0 warn=5", res.stdout)


if __name__ == "__main__":
    unittest.main()
