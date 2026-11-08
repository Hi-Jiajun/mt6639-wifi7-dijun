#!/usr/bin/env python3
"""Behavioural tests for tools/inspect_mt6639.py using a fake adb executable."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "inspect_mt6639.py"

FAKE_ADB = '''#!/usr/bin/env python3
import json, os, sys, time
rules = json.load(open(os.environ["FAKE_ADB_RULES"]))
with open(os.environ["FAKE_ADB_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
cmd = sys.argv[-1] if len(sys.argv) > 1 else ""
for rule in rules:
    if rule["match"] in cmd:
        time.sleep(rule.get("sleep", 0))
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))
sys.exit(0)
'''

MUTATION = re.compile(
    r"\b(insmod|rmmod|modprobe|mount|umount|setprop|reboot|ping|nmcli)\b"
    r"|settings put|svc wifi (enable|disable)"
    r"|wpa_cli .*\b(scan|scan_results|select_network|enable_network|add_network"
    r"|set_network|save_config|reconnect|disconnect)\b"
    r"|cmd wifi (start-scan|connect-network|set-wifi-enabled|add-network|forget-network)"
)


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.adb = self.tmp / "fake-adb"
        self.adb.write_text(FAKE_ADB)
        self.adb.chmod(0o755)
        self.rules_path = self.tmp / "rules.json"
        self.log = self.tmp / "adb.log"
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, rules):
        self.rules_path.write_text(json.dumps(rules))
        return {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "FAKE_ADB_RULES": str(self.rules_path),
            "FAKE_ADB_LOG": str(self.log),
        }

    def run_tool(self, rules, *extra, out=None, adb=None):
        args = [sys.executable, str(TOOL), "--adb", str(adb or self.adb),
                "--serial", "FAKESERIAL", "--out", str(out or self.out)]
        args.extend(extra)
        return subprocess.run(args, capture_output=True, text=True,
                              env=self.env(rules), timeout=180)

    def digest(self, cp):
        return json.loads(cp.stdout)

    @staticmethod
    def status_of(digest, name):
        return {p["name"]: p for p in digest["probes"]}[name]["status"]

    def adb_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def test_collects_evidence_and_redacts_stdout(self):
        rules = [
            {"match": "iw phy", "stdout":
                "Wiphy phy0\n\tBand 1:\n\t\t* 2412 MHz [1] (20.0 dBm)\n"
                "\tBand 2:\n\t\t* 5180 MHz [36] (20.0 dBm)\n"
                "\tBand 4:\n\t\t* 5955.0 MHz [1] (12.0 dBm)\n"
                "\t\t* 6115.0 MHz [33] (12.0 dBm)\n"},
            {"match": "cmd wifi status", "stdout":
                "Wifi is enabled\nmWifiInfo SSID: MySecretSSID, BSSID: aa:bb:cc:dd:ee:ff, "
                "MAC: 11:22:33:44:55:66, IP: /198.51.100.77, Frequency: 5955, "
                "Supplicant state: COMPLETED\n"},
            {"match": "wpa_cli -i wlan0 status", "stdout":
                "bssid=aa:bb:cc:dd:ee:ff\nfreq=5955\nssid=MySecretSSID\n"
                "wpa_state=COMPLETED\n"},
            {"match": "ip addr", "stdout":
                "2: wlan0: <BROADCAST>\n    inet 198.51.100.77/24 brd 198.51.100.255 scope global wlan0\n"},
            {"match": "cmd wifi get-allowed-channel -b 8", "stdout":
                "Band 2: 2412 2437\nBand 4: 5955 6115\n"},
            {"match": "random/boot_id", "stdout":
                "b2b2b2b2-2222-4222-8222-222222222222\n"},
            {"match": "/proc/1/ns/mnt", "stdout": "mnt:[4026531840]\n"},
            {"match": "/proc/self/ns/mnt", "stdout": "mnt:[4026531840]\n"},
            {"match": "sha256sum", "stdout":
                "54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea  /x\n"},
            {"match": "DisallowBand6G' /odm/firmware", "stdout": "130:DisallowBand6G 1\n"},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)

        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        summary = self.out / "summary.json"
        self.assertEqual(stat.S_IMODE(summary.stat().st_mode), 0o600)
        raw_files = sorted((self.out / "probes").iterdir())
        self.assertTrue(raw_files)
        for path in raw_files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

        for secret in ("MySecretSSID", "198.51.100.77", "aa:bb:cc", "11:22:33"):
            self.assertNotIn(secret, cp.stdout)
            self.assertNotIn(secret, summary.read_text())
        self.assertNotIn("MySecretSSID", json.dumps(digest["probes"]))

        derived = digest["derived"]
        self.assertEqual(derived["identity"]["boot_id"],
                         "b2b2b2b2-2222-4222-8222-222222222222")
        self.assertTrue(derived["identity"]["mount_namespace_same"])
        self.assertEqual(derived["identity"]["mount_namespace_self"], "mnt:[4026531840]")
        self.assertTrue(derived["iw"]["six_ghz_band_present"])
        self.assertEqual(derived["iw"]["six_ghz_frequency_count"], 2)
        self.assertIsNotNone(derived["wifi"]["ssid_sha256_12"])
        self.assertEqual(derived["wifi"]["frequency_mhz"], 5955)
        self.assertEqual(derived["wpa"]["wpa_state"], "COMPLETED")
        self.assertEqual(derived["wpa"]["frequency_mhz"], 5955)
        self.assertEqual(derived["reg6"]["assessment"], "not_assessed")
        self.assertIn("not", derived["reg6"]["reason"].lower())
        self.assertEqual(derived["cfg"]["/odm/firmware/wifi.cfg"]["disallow_band6g"], "1")

        wifi_raw = (self.out / "probes" / "wifi_status.out").read_text()
        self.assertIn("MySecretSSID", wifi_raw)

    def test_status_classification(self):
        rules = [
            {"match": "getenforce", "stderr": "Permission denied\n", "exit": 1},
            {"match": "/vendor/bin/wpa_cli -i wlan0 mlo_status", "stdout": ""},
            {"match": "/vendor/bin/wpa_cli -i wlan0 status", "stdout": "FAIL\n"},
            {"match": "/vendor/bin/wpa_cli -i wlan0 signal_poll",
             "stdout": "UNKNOWN COMMAND\n"},
            {"match": "iw dev wlan0 link", "stdout": "Not connected.\n"},
            {"match": "cmd wifi get-wifi-supported-features", "stderr":
                "Unknown command: get-wifi-supported-features\n", "exit": 255},
            {"match": "/data/adb/ksud --version", "stderr":
                "sh: /data/adb/ksud: not found\n", "exit": 127},
            {"match": "ls -1 /data/adb/modules", "stdout": "mod_a\nmod_b\n", "exit": 3},
            {"match": "ip -4 route show table all dev wlan0",
             "stderr": "adb: device offline\n", "exit": 1},
            {"match": "getprop ro.build.fingerprint", "stdout": "Xiaomi/dijun:14/UKQ1\n"},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)

        self.assertEqual(self.status_of(digest, "selinux_enforce"), "failed")
        self.assertEqual(self.status_of(digest, "wpa_mlo_status"), "empty")
        self.assertEqual(self.status_of(digest, "wpa_status"), "failed")
        self.assertEqual(self.status_of(digest, "wpa_signal_poll"), "missing")
        self.assertEqual(self.status_of(digest, "iw_link"), "ok")
        self.assertEqual(self.status_of(digest, "wifi_supported_features"), "missing")
        self.assertEqual(self.status_of(digest, "ksu_version"), "missing")
        self.assertEqual(self.status_of(digest, "ksu_modules"), "failed")
        self.assertEqual(self.status_of(digest, "ip_route4"), "adb_error")
        self.assertEqual(self.status_of(digest, "prop_fingerprint"), "ok")
        self.assertEqual(digest["probe_counts"]["missing"], 3)
        self.assertNotIn("ok", [self.status_of(digest, "wpa_status"),
                                self.status_of(digest, "wpa_signal_poll")])

        partial = (self.out / "probes" / "ksu_modules.out").read_text()
        self.assertIn("mod_a", partial)
        entry = {p["name"]: p for p in digest["probes"]}["ksu_modules"]
        self.assertEqual(entry["rc"], 3)
        self.assertEqual(entry["stdout_bytes"], len("mod_a\nmod_b\n"))

    def test_timeout_is_reported_not_unsupported(self):
        rules = [{"match": "uname -a", "stdout": "Linux\n", "sleep": 5}]
        cp = self.run_tool(rules, "--timeout", "1")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)
        entry = {p["name"]: p for p in digest["probes"]}["kernel"]
        self.assertEqual(entry["status"], "timeout")
        self.assertGreaterEqual(entry["elapsed_s"], 0.9)
        self.assertLess(entry["elapsed_s"], 4.0)
        self.assertNotIn("unsupported", json.dumps(digest).lower())

    def test_existing_out_dir_rejected(self):
        self.out.mkdir()
        cp = self.run_tool([])
        self.assertEqual(cp.returncode, 2)
        self.assertIn("exist", cp.stderr.lower())
        self.assertEqual(self.adb_calls(), [])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_invalid_timeout_and_adb_rejected(self):
        for value in ("0", "-3", "999", "abc"):
            cp = self.run_tool([], "--timeout", value)
            self.assertEqual(cp.returncode, 2, value)
        cp = self.run_tool([], adb=Path("adb"))
        self.assertEqual(cp.returncode, 2)
        cp = self.run_tool([], adb=self.tmp / "does-not-exist")
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(self.adb_calls(), [])

    def test_wiphy_helper_path_allowlist(self):
        for bad in ("/data/local/tmp/../../etc/passwd", "/system/bin/id",
                    "/data/adb/x;rm -rf /", "/data/adb/a b", "data/adb/helper",
                    "/data/local/tmp/./x", "/data/adb/../local/tmp/x",
                    "/data/local//tmp/x", "/data/local/tmp/x/"):
            cp = self.run_tool([], "--wiphy-helper", bad)
            self.assertEqual(cp.returncode, 2, bad)
        self.assertEqual(self.adb_calls(), [])

        cp = self.run_tool([], "--wiphy-helper", "/data/local/tmp/wiphy_read")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        calls = self.adb_calls()
        self.assertIn("/data/local/tmp/wiphy_read", [c[-1] for c in calls])
        self.assertEqual(self.status_of(self.digest(cp), "wiphy_helper"), "empty")

    def test_no_mutating_device_commands(self):
        cp = self.run_tool([{"match": "iw phy", "stdout": "Wiphy phy0\n"}])
        self.assertEqual(cp.returncode, 0, cp.stderr)
        calls = self.adb_calls()
        self.assertGreater(len(calls), 25)
        for argv in calls:
            self.assertEqual(argv[0], "-s")
            self.assertEqual(argv[1], "FAKESERIAL")
            self.assertEqual(argv[2], "shell")
            self.assertIsNone(MUTATION.search(argv[-1]), argv[-1])
        joined = "\n".join(c[-1] for c in calls)
        for name in ("cmd wifi status", "cmd wifi get-country-code",
                     "cmd wifi get-allowed-channel -b 8",
                     "cmd wifi get-wifi-supported-features",
                     "cmd wifi get-device-wiphy-capabilities wlan0",
                     "cmd wifi list-networks",
                     "/vendor/bin/wpa_cli -i wlan0 status",
                     "/vendor/bin/wpa_cli -i wlan0 signal_poll",
                     "/vendor/bin/wpa_cli -i wlan0 mlo_status", "iw dev", "iw phy",
                     "iw dev wlan0 link", "ip addr",
                     "ip -4 route show table all dev wlan0",
                     "ip -6 route show table all dev wlan0",
                     "cat /proc/sys/kernel/random/boot_id",
                     "readlink /proc/1/ns/mnt", "readlink /proc/self/ns/mnt",
                     "cat /proc/self/mountinfo", "/data/adb/ksud --version",
                     "/data/adb/modules"):
            self.assertIn(name, joined)
        for binder in ("cmd wifi status", "cmd wifi get-country-code",
                       "cmd wifi get-allowed-channel -b 8",
                       "cmd wifi get-wifi-supported-features",
                       "cmd wifi get-device-wiphy-capabilities wlan0",
                       "cmd wifi list-networks"):
            self.assertIn("runcon u:r:shell:s0 " + binder, joined)
        self.assertIn("runcon u:r:shell:s0 settings get global bluetooth_on", joined)

    def test_dumpsys_filtered_pipeline_keeps_inner_rc(self):
        rules = [{"match": "dumpsys wifi", "stdout":
                  "mDriverCountryCode: CN\nfirmwareVersion=1.2\ndumpsys_rc=7\nfilter_rc=0\n"}]
        cp = self.run_tool(rules)
        digest = self.digest(cp)
        entry = {p["name"]: p for p in digest["probes"]}["dumpsys_wifi_filtered"]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("dumpsys_rc=7", entry["note"])
        raw = (self.out / "probes" / "dumpsys_wifi_filtered.out").read_text()
        self.assertNotIn("SSID", raw)
        self.assertIn("firmwareVersion=1.2", raw)

        self.out = self.tmp / "out2"
        self.log.unlink()
        rules[0]["stdout"] = "mOverrideCountryCode: CN\ndumpsys_rc=0\nfilter_rc=0\n"
        cp = self.run_tool(rules)
        self.assertEqual(
            self.status_of(self.digest(cp), "dumpsys_wifi_filtered"), "ok")

        self.out = self.tmp / "out3"
        self.log.unlink()
        rules[0]["stdout"] = "mDriverCountryCode: CN\nfilter_rc=0\n"
        cp = self.run_tool(rules)
        entry = {p["name"]: p for p in self.digest(cp)["probes"]}["dumpsys_wifi_filtered"]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("marker missing", entry["note"])

        self.out = self.tmp / "out4"
        self.log.unlink()
        rules[0]["stdout"] = "dumpsys_rc=0\nfilter_rc=0\n"
        cp = self.run_tool(rules)
        entry = {p["name"]: p for p in self.digest(cp)["probes"]}["dumpsys_wifi_filtered"]
        self.assertEqual(entry["status"], "empty")
        self.assertIn("no matching country or version fields", entry["note"])

    def test_wifi_country_code_equals_format(self):
        rules = [
            {"match": "cmd wifi get-country-code", "stdout": "Wifi Country Code = CN\n"},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)
        self.assertEqual(digest["derived"]["wifi"]["country_code"], "CN")

    def test_wifi_list_networks_counts_distinct_ids(self):
        rules = [
            {"match": "cmd wifi list-networks", "stdout":
             "Network Id / SSID / Security type\n"
             "0 One wpa3-sae\n"
             "1 Two wpa2-psk\n"
             "1 Two wpa3-sae\n"},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)
        self.assertEqual(digest["derived"]["wifi"]["saved_network_count"], 2)

    def test_failed_probes_do_not_produce_false_negative_facts(self):
        rules = [
            {"match": "cat /proc/self/mountinfo", "stderr": "Permission denied\n", "exit": 1},
            {"match": "iw phy", "stderr": "nl80211 not found\n", "exit": 1},
            {"match": "ls -ld /data/adb/ksu", "stderr": "Permission denied\n", "exit": 1},
            {"match": "ls -ld /data/adb/ksud", "stderr": "Permission denied\n", "exit": 1},
            {"match": "ip -4 route show table all dev wlan0", "stderr": "adb: device offline\n", "exit": 1},
            {"match": "iw dev wlan0 link", "stderr": "command failed\n", "exit": 1},
            {"match": "cmd wifi get-allowed-channel -b 8", "stderr": "Failure calling service\n", "exit": 255},
            {"match": "cmd wifi status", "stderr": "Permission denied\n", "exit": 1},
            {"match": "/vendor/bin/wpa_cli -i wlan0 status", "stdout": "FAIL\n"},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        derived = self.digest(cp)["derived"]
        self.assertIsNone(derived["mounts"]["odm_firmware_wifi_cfg_mounted"])
        self.assertIsNone(derived["iw"]["six_ghz_band_present"])
        self.assertIsNone(derived["reg6"]["prerequisites"]["six_ghz_band_registered_in_iw_phy"])
        self.assertIsNone(derived["ksu"]["present"])
        self.assertIsNone(derived["net"]["wlan0_default_route_present"])
        self.assertIsNone(derived["link"]["connected"])
        self.assertIsNone(derived["wifi"]["allowed_channel_b8"]["channel_count"])
        self.assertIsNone(derived["wifi"]["allowed_channel_b8"]["six_ghz_channel_count"])
        self.assertIsNone(derived["reg6"]["prerequisites"]["allowed_channel_count_b8"])
        self.assertIsNone(derived["reg6"]["prerequisites"]["allowed_6ghz_channel_count_b8"])
        self.assertIsNone(derived["wifi"]["bssid_present"])
        self.assertIsNone(derived["wpa"]["bssid_present"])
        self.assertIsNone(derived["link"]["bssid_present"])
        self.assertIsNone(derived["net"]["wlan0_route4_line_count"])

    def test_cmd_wifi_unknown_command_rc0_is_missing(self):
        rules = [
            {"match": "cmd wifi get-wifi-supported-features",
             "stdout": "Unknown command: get-wifi-supported-features\n", "exit": 0},
        ]
        cp = self.run_tool(rules)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        digest = self.digest(cp)
        self.assertEqual(self.status_of(digest, "wifi_supported_features"), "missing")

    def test_adb_oserror_recorded_as_adb_error(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inspect_mt6639", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p = mod.build_probes(None)[0]
        res = mod.run_probe("/nonexistent/adb", "FAKESERIAL", p, 10.0, str(self.tmp))
        self.assertEqual(res["status"], "adb_error")
        self.assertIn("adb subprocess execution failure", res["note"])

    def test_dumpsys_streaming_pipeline_large_dump_no_arg_max(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inspect_mt6639", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        dumpsys_bin = self.tmp / "dumpsys"
        dumpsys_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('irrelevant wifi log line\\n' * 50000)\n"
            "sys.stdout.write('mOverrideCountryCode: CN\\n')\n"
            "sys.stdout.write('firmwareVersion=1.23.4\\n')\n"
            "sys.stdout.write('more irrelevant wifi log line\\n' * 10000)\n"
            "sys.exit(0)\n"
        )
        dumpsys_bin.chmod(0o755)

        runcon_bin = self.tmp / "runcon"
        runcon_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "os.execvp(sys.argv[2], sys.argv[2:])\n"
        )
        runcon_bin.chmod(0o755)

        printf_bin = self.tmp / "printf"
        printf_bin.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "total_len = sum(len(a) for a in sys.argv[1:])\n"
            "if total_len > 65536:\n"
            "    sys.stderr.write('printf: Argument list too long\\n')\n"
            "    sys.exit(1)\n"
            "sys.stdout.write(' '.join(sys.argv[1:]) + '\\n')\n"
        )
        printf_bin.chmod(0o755)

        env = dict(os.environ, PATH=f"{self.tmp}:{os.environ.get('PATH', '')}")
        cmd = mod.DUMPSYS_PIPE
        cp = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
        self.assertNotIn("Argument list too long", cp.stderr)
        self.assertIn("mOverrideCountryCode: CN", cp.stdout)
        self.assertIn("firmwareVersion=1.23.4", cp.stdout)
        self.assertIn("dumpsys_rc=0", cp.stdout)
        self.assertIn("filter_rc=0", cp.stdout)
        self.assertLess(len(cp.stdout), 500)

    def test_derive_b1_b2_gating_and_partial_preservation(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inspect_mt6639", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Case 1: failed probe with only lower-band frequencies (B1)
        res1 = [{"name": "wifi_allowed_channel_b8", "status": "failed",
                 "stdout": "Band 2: 2412 2437\n"}]
        d1 = mod.derive(res1)
        self.assertEqual(d1["wifi"]["allowed_channel_b8"]["channel_count"], 2)
        self.assertIsNone(d1["wifi"]["allowed_channel_b8"]["six_ghz_channel_count"])
        self.assertIsNone(d1["wifi"]["allowed_channel_b8"]["min_mhz"])
        self.assertIsNone(d1["wifi"]["allowed_channel_b8"]["max_mhz"])
        self.assertIsNone(d1["reg6"]["prerequisites"]["allowed_6ghz_channel_count_b8"])
        self.assertEqual(d1["reg6"]["prerequisites"]["allowed_channel_count_b8"], 2)

        # Case 2: timeout probe with only lower-band frequencies (B1)
        res2 = [{"name": "wifi_allowed_channel_b8", "status": "timeout",
                 "stdout": "Band 2: 2412\n"}]
        d2 = mod.derive(res2)
        self.assertIsNone(d2["wifi"]["allowed_channel_b8"]["six_ghz_channel_count"])
        self.assertIsNone(d2["wifi"]["allowed_channel_b8"]["min_mhz"])
        self.assertIsNone(d2["reg6"]["prerequisites"]["allowed_6ghz_channel_count_b8"])

        # Case 3: failed probe with partial positive 6 GHz channel preserved (B1)
        res3 = [{"name": "wifi_allowed_channel_b8", "status": "failed",
                 "stdout": "Band 2: 2412\nBand 4: 5955\n"}]
        d3 = mod.derive(res3)
        self.assertEqual(d3["wifi"]["allowed_channel_b8"]["channel_count"], 2)
        self.assertEqual(d3["wifi"]["allowed_channel_b8"]["six_ghz_channel_count"], 1)
        self.assertIsNone(d3["wifi"]["allowed_channel_b8"]["min_mhz"])
        self.assertEqual(d3["reg6"]["prerequisites"]["allowed_6ghz_channel_count_b8"], 1)

        # Case 4: successful empty-per-mode headers remain count 0 / status ok (B1)
        res4 = [{"name": "wifi_allowed_channel_b8", "status": "ok",
                 "stdout": "Mode 0:\nMode 1:\n"}]
        d4 = mod.derive(res4)
        self.assertEqual(d4["wifi"]["allowed_channel_b8"]["channel_count"], 0)
        self.assertEqual(d4["wifi"]["allowed_channel_b8"]["six_ghz_channel_count"], 0)
        self.assertEqual(d4["reg6"]["prerequisites"]["allowed_channel_count_b8"], 0)
        self.assertEqual(d4["reg6"]["prerequisites"]["allowed_6ghz_channel_count_b8"], 0)

        # Case 5: empty / failed ip and route probes -> None (B2)
        res5 = [
            {"name": "ip_addr", "status": "failed", "stdout": ""},
            {"name": "ip_route4", "status": "failed", "stdout": ""},
            {"name": "ip_route6", "status": "failed", "stdout": ""},
        ]
        d5 = mod.derive(res5)
        self.assertIsNone(d5["net"]["ipv4_address_count"])
        self.assertIsNone(d5["net"]["ipv6_address_count"])
        self.assertIsNone(d5["net"]["wlan0_route4_line_count"])
        self.assertIsNone(d5["net"]["wlan0_route6_line_count"])

        # Case 6: partial only IPv4 doesn't fabricate IPv6 zero (B2)
        res6 = [{"name": "ip_addr", "status": "failed",
                 "stdout": "2: wlan0:\n    inet 198.51.100.50/24 scope global wlan0\n"}]
        d6 = mod.derive(res6)
        self.assertEqual(d6["net"]["ipv4_address_count"], 1)
        self.assertIsNone(d6["net"]["ipv6_address_count"])

        # Case 7: successful ok ip and route probes (B2)
        res7 = [
            {"name": "ip_addr", "status": "ok",
             "stdout": "2: wlan0:\n    inet 198.51.100.50/24 scope global wlan0\n"},
            {"name": "ip_route4", "status": "ok",
             "stdout": "default via 198.51.100.1 dev wlan0\n"},
            {"name": "ip_route6", "status": "ok", "stdout": ""},
        ]
        d7 = mod.derive(res7)
        self.assertEqual(d7["net"]["ipv4_address_count"], 1)
        self.assertEqual(d7["net"]["ipv6_address_count"], 0)
        self.assertEqual(d7["net"]["wlan0_route4_line_count"], 1)
        self.assertEqual(d7["net"]["wlan0_route6_line_count"], 0)


if __name__ == "__main__":
    unittest.main()
