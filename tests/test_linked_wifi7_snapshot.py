"""Tests for the serial linked AP/phone Wi-Fi 7 snapshot orchestrator."""
from pathlib import Path
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/linked_wifi7_snapshot.py"


def assoc_ie():
    eht = bytes.fromhex("2300ee6d49f619e7fa0100") + b"\x22" * 9
    return base64.b64encode(
        bytes([0, 4]) + b"Home" + bytes([255, len(eht) + 1, 108]) + eht
    ).decode()


AP_REMOTE = (
    "__CLIENTS__\n"
    + json.dumps({
        "freq": 6135,
        "clients": {
            "02:00:00:00:00:01": {
                "auth": True,
                "assoc": True,
                "authorized": True,
                "preauth": False,
                "wds": False,
                "he": True,
                "mfp": True,
            }
        },
    })
    + "\n__STATUS__\n"
    + json.dumps({
        "status": "ENABLED",
        "bssid": "02:00:00:00:00:02",
        "ssid": "Home",
        "freq": 6135,
    })
    + "\n__BOOT_ID__\nap-boot-1\n__STA_IES__\n"
    + json.dumps({"assoc_ie": assoc_ie()})
    + "\n"
)

FAKE_ADB = """#!/usr/bin/env python3
import sys
cmd = sys.argv[-1]
text = {
 'cat /proc/sys/kernel/random/boot_id': 'phone-boot-1\\n',
 'getprop ro.build.fingerprint': 'Xiaomi/dijun/test\\n',
 'getenforce': 'Enforcing\\n',
 'iw dev wlan0 link': 'Connected to 02:00:00:00:00:02 (on wlan0)\\n'
                      '\\tSSID: Home\\n\\tfreq: 6135\\n'
                      '\\trx bitrate: 5764.8 MBit/s EHT-MCS 12 EHT-NSS 2 320MHz\\n'
                      '\\ttx bitrate: 5764.8 MBit/s\\n',
 'iw phy phy0 info': 'Band 4:\\n\\tEHT Iftypes: managed\\n'
                     '\\t\\tEHT PHY Capabilities (0x6e0000000000000000):\\n'
                     '\\t\\t\\t320MHz in 6GHz Supported\\n'
                     '\\t\\tEHT-MCS Map (BW <= 80) (0x222222):\\n'
                     '\\t\\t\\tRx Max NSS for MCS 0-9: 2\\n'
                     '\\t\\t\\tTx Max NSS for MCS 0-9: 2\\n'
                     '\\t\\t\\tRx Max NSS for MCS 10-11: 2\\n'
                     '\\t\\t\\tTx Max NSS for MCS 10-11: 2\\n'
                     '\\t\\t\\tRx Max NSS for MCS 12-13: 2\\n'
                     '\\t\\t\\tTx Max NSS for MCS 12-13: 2\\n'
                     '\\t\\tEHT-MCS Map (BW = 320) (0x000000):\\n'
                     '\\t\\t\\tRx Max NSS for MCS 0-9: 0\\n'
                     '\\t\\t\\tTx Max NSS for MCS 0-9: 0\\n'
                     '\\t\\t\\tRx Max NSS for MCS 10-11: 0\\n'
                     '\\t\\t\\tTx Max NSS for MCS 10-11: 0\\n'
                     '\\t\\t\\tRx Max NSS for MCS 12-13: 0\\n'
                     '\\t\\t\\tTx Max NSS for MCS 12-13: 0\\n'
                     'Supported extended features:\\n\\t\\t[1,2,3,4,5,6,7,0]\\n',
 'show-mld 255': 'wlan0 driver:\\n\\n'
                 'BSS[0][RF_BAND=3][OMAC=02:00:**:**:**:01][LINK_ID=0]:\\n'
                 '\\tACTIVE/HW_BAND/OMAC_IDX: 1/1/0\\n'
                 '\\tCHANNEL/WIDTH/S1/S2: 37/4/31/0\\n',
 'iw --version': 'iw version 6.17\\n',
 'sha256sum /system/bin/iw': '33dc99bc74b48af217db7f47d955cfc1005f370f4b227425b627909c467ccab6  /system/bin/iw\\n',
 'cmd wifi status': 'Wifi is enabled\\nWifi is connected to "Home"\\n'
                    'WifiInfo: SSID: "Home", BSSID: 02:00:00:00:00:02, '
                    'MAC: 02:00:00:00:00:01, Supplicant state: COMPLETED, '
                    'Wi-Fi standard: 11be, Frequency: 6135MHz\\n',
}.get([k for k in ['cat /proc/sys/kernel/random/boot_id','getprop ro.build.fingerprint','getenforce',
                   'iw dev wlan0 link','iw phy phy0 info','show-mld 255','iw --version',
                   'sha256sum /system/bin/iw','cmd wifi status'] if k in cmd][0], '')
sys.stdout.write(text)
"""


class LinkedSnapshotTests(unittest.TestCase):
    def test_serial_collection_and_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ssh = root / "ssh"
            adb = root / "adb"
            ssh.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" > \"$SSH_CALLS\"\n"
                "cat \"$AP_REMOTE\"\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            adb.write_text(FAKE_ADB, encoding="utf-8")
            adb.chmod(0o755)
            ap_remote = root / "ap-remote"
            ap_remote.write_text(AP_REMOTE, encoding="utf-8")
            out = root / "out"
            env = os.environ.copy()
            env.update({
                "PATH": str(root) + ":/usr/bin:/bin",
                "AP_REMOTE": str(ap_remote),
                "SSH_CALLS": str(root / "ssh-calls"),
            })
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--ap-host",
                    "root@example.test",
                    "--ap-address",
                    "02:00:00:00:00:01",
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            run = json.loads((out / "run.json").read_text())
            compare = json.loads((out / "compare.json").read_text())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(run["status"], "complete")
        self.assertEqual([x["id"] for x in run["steps"]],
                         ["ap_station_ie_recheck", "wifi7_observe",
                          "eht_source_compare"])
        self.assertTrue(run["read_only_sequence"])
        self.assertEqual(compare["result"], "source_mismatch")
        self.assertEqual(compare["runtime"]["frequency_mhz"], 6135)

    def test_phone_failure_is_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ssh = root / "ssh"
            adb = root / "adb"
            ssh.write_text(
                "#!/bin/sh\ncat \"$AP_REMOTE\"\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            adb.write_text(
                FAKE_ADB.replace(
                    "sys.stdout.write(text)",
                    "if 'iw phy phy0 info' in cmd: sys.exit(7)\n"
                    "sys.stdout.write(text)",
                ),
                encoding="utf-8",
            )
            adb.chmod(0o755)
            ap_remote = root / "ap-remote"
            ap_remote.write_text(AP_REMOTE, encoding="utf-8")
            out = root / "out"
            env = os.environ.copy()
            env.update({
                "PATH": str(root) + ":/usr/bin:/bin",
                "AP_REMOTE": str(ap_remote),
            })
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--ap-host",
                    "root@example.test",
                    "--ap-address",
                    "02:00:00:00:00:01",
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            run = json.loads((out / "run.json").read_text())
        self.assertEqual(done.returncode, 2)
        self.assertEqual(run["status"], "incomplete")
        self.assertEqual(run["steps"][1]["rc"], 2)


if __name__ == "__main__":
    unittest.main()
