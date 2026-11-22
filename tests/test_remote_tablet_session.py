"""Offline tests for tools/remote_tablet_session.py across SSH-forwarded ADB and SSH."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import pubenv


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/remote_tablet_session.py"


FAKE_ADB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["-P", "15037"]:
    args = args[2:]
if len(args) >= 4 and args[:2] == ["-s", "TAB01"] and args[2] == "push":
    sys.exit(0)
if len(args) >= 4 and args[:2] == ["-s", "TAB01"] and args[2] == "shell":
    cmd = args[3]
else:
    sys.exit(64)
env = os.environ
if "SU_ID=" in cmd:
    print(f"SU_ID={env.get('FAKE_SU_ID', 'uid=0(root) context=u:r:ksu:s0')}")
    print(f"SELINUX={env.get('FAKE_SELINUX', 'Enforcing')}")
    print(f"SYSFS_ENFORCE={env.get('FAKE_SYSFS', '1')}")
    print(f"DEVICE={env.get('FAKE_DEVICE', 'violin')}")
    print(f"SDK={env.get('FAKE_SDK', '37')}")
    print(f"MI_OS={env.get('FAKE_MI_OS', 'OS4.0.6.0.XOTCNXM')}")
    print(f"BASEBAND={env.get('FAKE_BASEBAND', '')}")
    print(f"KERNEL={env.get('FAKE_KERNEL', '6.18.21-android17-5-g284297031c53-abogki553682993-4k')}")
    print("KO_PATH=/vendor_dlkm/lib/modules/wlan_drv_gen4m_eap_6639.ko")
    print(f"CFG_SHA={env.get('FAKE_CFG_SHA', '71e1ebd0fce77dab26ac7339db1e612ed107e7d404667d57e9c008af1e941a89')}")
    print(f"KO_SHA={env.get('FAKE_KO_SHA', 'f226384e73810a639b7fc6a335367cce2345a7726aac3dd66515493952987c22')}")
    print("BT_COUNT=1")
    print(f"BT_APK_SHA={env.get('FAKE_BT_SHA', '1a5ae65ccd8621c03ca5a6a035979eac74025c3ecdda19dd84f1bbd53162983e')}")
    print(f"TETHER_JAR_SHA={env.get('FAKE_JAR_SHA', '0b6f6c859301dae3276b9375c3b75333168dc5b76292ed9da03e29b59fc887bb')}")
    print("BT_OFFLOAD=true")
    print("BT_OFFLOAD_DIS=false")
    print("WIFI_ENABLED=1")
    print(f"WIFI_CONNECTED={env.get('FAKE_WIFI_CONN', '1')}")
    print("KSUD_VER=3.3.1")
    print("KSU_MGR=me.weishu.kernelsu")
    print("ZYGISK_OK=1")
    print("LSPOSED_OK=1")
    print("UI_PKG_VER=0.3.0")
    print("MOD_LIVE_mt6639-6g=v0.4.1")
    print("MOD_UPD_mt6639-6g=absent")
    print("MOD_DIS_mt6639-6g=1")
    print("MOD_LIVE_mt6639-bt=v0.2.0")
    print("MOD_UPD_mt6639-bt=absent")
    print("MOD_DIS_mt6639-bt=0")
    print("MOD_LIVE_mt6639-hotspot=v0.3.1")
    print("MOD_UPD_mt6639-hotspot=absent")
    print("MOD_DIS_mt6639-hotspot=0")
    sys.exit(0)
if "mktemp -d /data/local/tmp/mt6639-remote-stage-" in cmd:
    print("/data/local/tmp/mt6639-remote-stage-test1")
    sys.exit(0)
if "STAGE_OK=1" in cmd:
    with open(env["FAKE_STAGE_CMD"], "w") as fh:
        fh.write(cmd)
    print("STAGE_OK=1")
    sys.exit(0)
if "RESTORE_RC=" in cmd:
    with open(env["FAKE_RESTORE_CMD"], "w") as fh:
        fh.write(cmd)
    print("RESTORE_RC=0")
    print("active_tx=none")
    print("state=restored")
    sys.exit(0)
if "CLEANUP_OK=1" in cmd:
    with open(env["FAKE_CLEANUP_CMD"], "w") as fh:
        fh.write(cmd)
    print("CLEANUP_OK=1")
    sys.exit(0)
if "SIXG_ENABLED_COUNT=" in cmd:
    with open(env["FAKE_TRIAL_CMD"], "w") as fh:
        fh.write(cmd)
    print("VERIFY_SHA_OK=1")
    print("TRIAL_ENABLE_RC=0")
    print("SIXG_ENABLED_COUNT=60")
    print("state=active")
    sys.exit(0)
if "CFG_MOUNT_COUNT=" in cmd:
    print("COUNTRY=Wifi Country Code = CN")
    print("CFG_MOUNT_COUNT=0")
    print("SIX_G_STATE=restored")
    print("SIX_G_ACTIVE_TX=none")
    print("MTAP0=absent")
    print("TETHER_WANTED=false")
    print("HOTSPOT_SNAP=schema=mt6639-hotspot-status-v4;local_ready=1;")
    print("BAND4_TOTAL=60")
    print("BAND4_ENABLED=0")
    print("BT_PROPS_OK=6")
    sys.exit(0)
if "status-card.sh" in cmd:
    print("[事务运行] 适配：violin-os4")
elif "mt6639-bt" in cmd:
    print("state=active")
elif "mt6639-hotspot" in cmd and "control.sh" in cmd:
    print("state=idle")
elif "cat /data/misc/mt6639-hotspot/status" in cmd:
    print("schema=mt6639-hotspot-status-v4")
elif "iw phy" in cmd:
    print("Band 4:\n\t* 5955 MHz [1]\n\t* 6135 MHz [37]\n")
else:
    print("ok")
sys.exit(0)
'''


class RemoteTabletSessionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="mt6639-remote-test-")
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.adb = self.root / "adb"
        self.adb.write_text(FAKE_ADB, encoding="utf-8")
        self.adb.chmod(0o755)
        self.log = self.root / "adb.log"
        self.trial_cmd = self.root / "trial.cmd"
        self.stage_cmd = self.root / "stage.cmd"
        self.restore_cmd = self.root / "restore.cmd"
        self.cleanup_cmd = self.root / "cleanup.cmd"
        self.base_env = dict(
            os.environ,
            FAKE_LOG=str(self.log),
            FAKE_TRIAL_CMD=str(self.trial_cmd),
            FAKE_STAGE_CMD=str(self.stage_cmd),
            FAKE_RESTORE_CMD=str(self.restore_cmd),
            FAKE_CLEANUP_CMD=str(self.cleanup_cmd),
        )

    def run_cli(self, *args, extra_env=None, transport="adb"):
        env = dict(self.base_env, **(extra_env or {}))
        if transport == "adb":
            base = [sys.executable, str(TOOL), "--adb", str(self.adb),
                    "--adb-port", "15037", "--serial", "TAB01", "--repo", str(REPO)]
        else:
            ssh = self.root / "ssh"
            ssh.write_text(
                "#!/usr/bin/env python3\n"
                "import os, subprocess, sys\n"
                "cmd = sys.argv[-1]\n"
                "res = subprocess.run([os.environ['REAL_FAKE_ADB'], '-s', 'TAB01', 'shell', cmd])\n"
                "sys.exit(res.returncode)\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            cfg = self.root / "ssh.json"
            cfg.write_text(json.dumps([
                str(ssh), "-F", "/dev/null", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
                "-o", "UserKnownHostsFile=" + str(self.root / "kh"),
                "-i", str(self.root / "key"), "-p", "12222", "u0_a251@127.0.0.1",
            ]))
            env["REAL_FAKE_ADB"] = str(self.adb)
            base = [sys.executable, str(TOOL), "--ssh-command-json", str(cfg),
                    "--serial", "TAB01", "--repo", str(REPO)]
        return subprocess.run(base + list(args), capture_output=True, text=True, env=env, timeout=20)

    def test_preflight_violin_os4_and_jinghu_os3_over_adb_tunnel_and_ssh(self):
        for tr in ("adb", "ssh"):
            cp = self.run_cli("preflight", "--strict", transport=tr)
            self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
            data = json.loads(cp.stdout)
            self.assertTrue(data["ready"])
            self.assertFalse(data["needs_rom_onboard"])
            self.assertEqual(data["device"], "violin")
            self.assertEqual(data["modules"]["mt6639-6g"]["profile"], "violin-os4")
            self.assertFalse(data["modules"]["mt6639-6g"]["legacy_66_fallback"])
            self.assertFalse(data["has_cellular_baseband"])
            self.assertTrue(data["wifi_enabled"])
            self.assertTrue(data["wifi_connected"])
            self.assertEqual(data["ksud_version"], "3.3.1")
            self.assertTrue(data["zygisk_ok"])
            self.assertTrue(data["lsposed_ok"])

        cp_os3 = self.run_cli(
            "preflight", "--strict",
            extra_env={
                "FAKE_DEVICE": "jinghu",
                "FAKE_SDK": "35",
                "FAKE_MI_OS": "OS3.0.306.0.WOXCNXM",
                "FAKE_KERNEL": "6.6.118-android15-8-gc44b714366cc-abogki519650608-4k",
                "FAKE_CFG_SHA": "8a6d775edd904c2f4f4843640c934c2ee70e205e2b7b370d84d6394a441f992b",
                "FAKE_KO_SHA": "95f302184f013b2f58fbdd80998eb599ee24ae9b83ae296820f195bfab4e2e30",
                "FAKE_BT_SHA": "5813fb9649007857900559e38bb323a951ee00150f21468bc9b131a51e789b86",
                "FAKE_JAR_SHA": "351d2fd9196b59e988ce5a6b8fb1809777ef66e3e2087c3d570c164ba54040e1",
            },
        )
        self.assertEqual(cp_os3.returncode, 0, cp_os3.stderr)
        data_os3 = json.loads(cp_os3.stdout)
        self.assertTrue(data_os3["ready"])
        self.assertTrue(data_os3["modules"]["mt6639-6g"]["legacy_66_fallback"])
        self.assertEqual(data_os3["modules"]["mt6639-bt"]["profile"], "tablet-os3")
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(c[:4] == ["-P", "15037", "-s", "TAB01"] for c in calls))

    def test_unlisted_ota_flags_needs_rom_onboard_and_blocks_execute(self):
        cp = self.run_cli("preflight", extra_env={"FAKE_CFG_SHA": "0" * 64})
        self.assertEqual(cp.returncode, 0)
        data = json.loads(cp.stdout)
        self.assertFalse(data["ready"])
        self.assertTrue(data["needs_rom_onboard"])
        self.assertEqual(data["modules"]["mt6639-6g"]["reason"], "cfg_sha_unlisted")

        cp_exec = self.run_cli("bounded-trial", "--execute", extra_env={"FAKE_CFG_SHA": "0" * 64})
        self.assertEqual(cp_exec.returncode, 2)
        self.assertIn("COMPAT_NOT_READY", cp_exec.stderr)
        self.assertFalse(self.trial_cmd.exists())

    def test_bounded_trial_preserves_sha256_and_collect_evidence_is_mode_0700(self):
        dry = self.run_cli("bounded-trial")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        plan = json.loads(dry.stdout)
        self.assertFalse(plan["executed"])
        self.assertTrue(plan["config_conf_untouched"])
        self.assertTrue(plan["disable_marker_preserved"])
        self.assertEqual(plan["env_override"]["RUN_MODE"], "bounded")
        self.assertFalse(self.trial_cmd.exists())

        live = self.run_cli("bounded-trial", "--execute")
        self.assertEqual(live.returncode, 0, live.stderr)
        res = json.loads(live.stdout)
        self.assertTrue(res["executed"])
        self.assertTrue(res["config_conf_untouched"])
        self.assertTrue(res["verify_module_sha_ok"])
        self.assertTrue(res["disable_marker_restored"])
        self.assertEqual(res["sixg_enabled_channels"], 60)
        cmd_text = self.trial_cmd.read_text()
        self.assertNotIn("config.conf", cmd_text)
        self.assertIn("verify-module.sh", cmd_text)
        self.assertIn("RUN_MODE=bounded", cmd_text)
        self.assertIn("trap restore_disable EXIT", cmd_text)
        self.assertNotIn("reboot", cmd_text)

        ev_dir = self.root / "ev"
        ev = self.run_cli("collect-evidence", "--out", str(ev_dir))
        self.assertEqual(ev.returncode, 0, ev.stderr)
        ev_data = json.loads(ev.stdout)
        self.assertEqual(ev_data["sixg_enabled_channels"], 2)
        self.assertEqual(stat.S_IMODE(ev_dir.stat().st_mode), 0o700)
        for p in ev_dir.iterdir():
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    @unittest.skipUnless(pubenv.candidate_ready(), pubenv.SKIP_RELEASE_ASSETS)
    def test_stage_workflow(self):
        # Stage dry-run + execute over fake ADB
        dry_stage = self.run_cli("stage")
        self.assertEqual(dry_stage.returncode, 0, dry_stage.stderr)
        s_plan = json.loads(dry_stage.stdout)
        self.assertFalse(s_plan["executed"])
        self.assertEqual(len(s_plan["artifacts"]), 4)
        self.assertFalse(self.stage_cmd.exists())

        live_stage = self.run_cli("stage", "--execute")
        self.assertEqual(live_stage.returncode, 0, live_stage.stderr)
        s_res = json.loads(live_stage.stdout)
        self.assertTrue(s_res["executed"])
        self.assertTrue(s_res["requires_single_reboot"])
        self.assertEqual(s_res["sixg_disable_guard"], "present")
        st_cmd = self.stage_cmd.read_text()
        self.assertIn("/data/adb/ksud module install", st_cmd)
        self.assertIn("/data/adb/modules_update/mt6639-6g/disable", st_cmd)
        self.assertIn("pm install -r -S ", st_cmd)
        self.assertIn(" < /data/local/tmp/mt6639-remote-stage-", st_cmd)
        self.assertNotIn("reboot", st_cmd)

    def test_restore_diagnose_and_cleanup_workflows(self):
        # restore-6g warns when 6.18 Wi-Fi is disconnected, succeeds on execute
        plan_r = json.loads(self.run_cli("restore-6g", extra_env={"FAKE_WIFI_CONN": "0"}).stdout)
        self.assertIn("6.18", plan_r["warning"])
        exec_r = json.loads(self.run_cli("restore-6g", "--execute").stdout)
        self.assertEqual(exec_r["status"], "restored")
        self.assertEqual(exec_r["active_tx"], "none")
        self.assertIn("control.sh", self.restore_cmd.read_text())

        # diagnose reports live counts without leaking SSID/IP/serial
        diag = json.loads(self.run_cli("diagnose").stdout)
        self.assertEqual(diag["live"]["band4_total_channels"], 60)
        self.assertEqual(diag["live"]["bt_true_props_count"], 6)

        # cleanup --execute --uninstall calls ksud module uninstall and pm uninstall
        cln = json.loads(self.run_cli("cleanup", "--execute", "--uninstall").stdout)
        self.assertEqual(cln["status"], "uninstalled_and_restored")
        cl_cmd = self.cleanup_cmd.read_text()
        self.assertIn("/data/adb/ksud module uninstall", cl_cmd)
        self.assertIn("pm uninstall com.hiliang.mt6639ui", cl_cmd)


if __name__ == "__main__":
    unittest.main()
