"""Tests for the read-only AP station association IE recheck tool."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/ap_station_ie_recheck.py"
sys.path.insert(0, str(TOOL.parent))
import ap_station_ie_recheck as recheck


def sample_assoc_ie() -> str:
    ssid = b"\x00\x01X"
    eht_cap = (
        b"\x23\x00"
        + b"\xee\x6d\x49\xf6\x19\xe7\xfa\x01\x00"
        + b"\x22" * 9
    )
    extension = bytes((255, len(eht_cap) + 1, 108)) + eht_cap
    return base64.b64encode(ssid + extension).decode("ascii")


def sample_remote() -> str:
    clients = {
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
    }
    status = {
        "driver": "nl80211",
        "status": "ENABLED",
        "bssid": "02:00:00:00:00:01",
        "ssid": "LabNet-6G",
        "freq": 6135,
        "channel": 37,
        "op_class": 137,
    }
    station_ies = {"assoc_ie": sample_assoc_ie()}
    return (
        "__CLIENTS__\n"
        + json.dumps(clients)
        + "\n__STATUS__\n"
        + json.dumps(status)
        + "\n__BOOT_ID__\n"
        + "ap-boot-1\n"
        + "\n__STA_IES__\n"
        + json.dumps(station_ies)
        + "\n"
    )


class RecheckTests(unittest.TestCase):
    def test_parser_returns_sanitized_summary(self):
        raw, summary = recheck.parse_remote(sample_remote(), "02:00:00:00:00:01")
        self.assertEqual(raw["assoc_ie"], sample_assoc_ie())
        self.assertEqual(summary["frequency_mhz"], 6135)
        self.assertTrue(summary["client_status"]["assoc"])
        self.assertEqual(summary["frequency_mhz"], 6135)
        self.assertEqual(summary["ssid_token"], "sha256:95a67766e8e29dc0")
        self.assertEqual(summary["bssid_token"], "sha256:c27c02f297b7a634")
        self.assertEqual(summary["association"]["eht_mac_capabilities_hex"], "2300")
        self.assertEqual(summary["association"]["mcs_nss_maps"]["= 320"]["12-13"],
                         {"rx": 2, "tx": 2})
        self.assertNotIn('"ssid":', json.dumps(summary))

    def test_missing_station_fails(self):
        with self.assertRaises(recheck.RecheckError):
            recheck.parse_remote(sample_remote(), "02:00:00:00:00:02")

    def test_fake_ssh_uses_only_read_commands_and_new_raw_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ssh = root / "ssh"
            calls = root / "calls"
            sample = root / "sample"
            raw_out = root / "raw.json"
            sample.write_text(sample_remote(), encoding="utf-8")
            ssh.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$@\" > \"$CALLS\"\n"
                "cat \"$SAMPLE\"\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": str(root) + ":/usr/bin:/bin",
                    "CALLS": str(calls),
                    "SAMPLE": str(sample),
                }
            )
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--host",
                    "root@example.test",
                    "--address",
                    "02:00:00:00:00:01",
                    "--raw-out",
                    str(raw_out),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            call_text = calls.read_text(encoding="utf-8")
            summary = json.loads(done.stdout)
            raw_mode = raw_out.stat().st_mode & 0o777
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(raw_mode, 0o600)
        self.assertIn("get_clients", call_text)
        self.assertIn("get_status", call_text)
        self.assertIn("get_sta_ies", call_text)
        self.assertTrue(summary["remote_payload_read_only"])
        for forbidden in (
            "uci set",
            "reload",
            "reboot",
            "del_client",
            "switch_chan",
            "hostapd_cli",
        ):
            self.assertNotIn(forbidden, call_text)


if __name__ == "__main__":
    unittest.main()
