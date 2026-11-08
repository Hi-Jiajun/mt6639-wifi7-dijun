"""Behavioural tests for tools/compare_icmp_paths.py (pcaps built with the
shared fixture builders from tools/summarize_icmp_pcap.py)."""
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.summarize_icmp_pcap import (  # noqa: E402
    build_ethernet,
    build_icmp_echo,
    build_ipv4,
    build_pcap,
)

TOOL = ROOT / "tools" / "compare_icmp_paths.py"
PHONE_IP = "198.51.100.187"
AP_IP = "198.51.100.242"
WIRED_IP = "198.51.100.60"


class CompareIcmpPathsTest(unittest.TestCase):

    @staticmethod
    def _ip4(s):
        return bytes(int(x) for x in s.split("."))

    def _frame(self, typ, ip_id, icmp_id, seq, payload, src, dst, t=(0, 0), sll=False):
        ip = build_ipv4(
            self._ip4(src),
            self._ip4(dst),
            1,
            build_icmp_echo(typ, icmp_id, seq, payload),
            ip_id,
        )
        if sll:
            data = b"\x00" * 14 + struct.pack("!H", 0x0800) + ip
        else:
            data = build_ethernet(ip)
        return (t[0], t[1], data)

    def _write_pcap(self, records, linktype=1):
        fd, path = tempfile.mkstemp(suffix=".pcap")
        os.close(fd)
        with open(path, "wb") as fh:
            fh.write(build_pcap(records, linktype=linktype))
        return path

    def _run(self, files, expected=6, extra=None):
        cmd = [
            sys.executable, str(TOOL),
            "--phone", files["phone"],
            "--ap", files["ap"],
            "--phone-ip", PHONE_IP,
            "--ap-ip", AP_IP,
            "--wired-ip", WIRED_IP,
            "--expected-count", str(expected),
        ]
        if files.get("wired"):
            cmd += ["--wired", files["wired"]]
        if extra:
            cmd += extra
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))

    def _output(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _session(self, out, cid, target=None):
        matches = [s for s in out["targets"] if s["id"] == cid]
        self.assertEqual(len(matches), 1, out["targets"])
        sess = matches[0]
        if target is not None:
            self.assertEqual(sess["target"], target)
        return sess


class CompleteDualPeerPathTest(CompareIcmpPathsTest):
    """Full phone<->AP echo path, 6 unique seqs, all points observe both ways."""

    def test_ethernet_and_sll_fixtures(self):
        for sll in (False, True):
            recs_ph = []
            recs_ap = []
            for i in range(1, 7):
                recs_ph.append(self._frame(8, 0x1000 + i, 4000, i, b"ABC",
                                           PHONE_IP, AP_IP, (10, i), sll))
                recs_ph.append(self._frame(0, 0x2000 + i, 4000, i, b"ABC",
                                           AP_IP, PHONE_IP, (10, i + 100), sll))
                recs_ap.append(self._frame(8, 0x1000 + i, 4000, i, b"ABC",
                                           PHONE_IP, AP_IP, (10, i + 1), sll))
                recs_ap.append(self._frame(0, 0x2000 + i, 4000, i, b"ABC",
                                           AP_IP, PHONE_IP, (10, i + 101), sll))
            ph = self._write_pcap(recs_ph, linktype=113 if sll else 1)
            ap = self._write_pcap(recs_ap, linktype=113 if sll else 1)
            out = self._output(self._run({"phone": ph, "ap": ap}))
            self.assertTrue(out["passed"], out["quality"])
            sess = self._session(out, 4000, "ap")
            self.assertEqual(sess["unique_phone_requests"], 6)
            self.assertEqual(sess["unique_matched_phone_replies"], 6)
            self.assertEqual(sess["duplicate_phone_requests"], 0)
            self.assertEqual(sess["duplicate_phone_replies"], 0)
            self.assertTrue(sess["coverage_ok"])
            for sq in sess["seqs"]:
                self.assertEqual(sq["missing_forward_capture_points"], [])
                self.assertEqual(sq["missing_return_capture_points"], [])
                self.assertEqual(sq["points"]["phone"]["requests"]["count"], 1)
                self.assertEqual(sq["points"]["phone"]["replies"]["count"], 1)
                self.assertEqual(sq["phone_matched_replies"], 1)
                self.assertIsNotNone(sq.get("rtt_ms"))


class ApReplyMissingAtPhoneTest(CompareIcmpPathsTest):
    """AP-local target: AP emitted the reply (also visible on wired monitor)
    but the phone capture missed it -> phone-side capture gap, not 'no reply'.
    Phone-matched reply coverage must therefore be 0 and passed=False."""

    def test_reply_missing_at_phone_but_seen_elsewhere(self):
        recs_ph = []
        recs_ap = []
        recs_wr = []
        for i in range(1, 7):
            recs_ph.append(self._frame(8, 0x3000 + i, 4100, i, b"AAA",
                                       PHONE_IP, AP_IP, (20, i)))
            recs_ap.append(self._frame(8, 0x3000 + i, 4100, i, b"AAA",
                                       PHONE_IP, AP_IP, (20, i + 1)))
            recs_ap.append(self._frame(0, 0x4000 + i, 4100, i, b"AAA",
                                       AP_IP, PHONE_IP, (20, i + 50)))
            recs_wr.append(self._frame(0, 0x4000 + i, 4100, i, b"AAA",
                                       AP_IP, PHONE_IP, (20, i + 51)))
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        wr = self._write_pcap(recs_wr)
        out = self._output(self._run({"phone": ph, "ap": ap, "wired": wr}))
        sess = self._session(out, 4100, "ap")
        self.assertFalse(out["passed"])
        self.assertFalse(sess["coverage_ok"])
        self.assertEqual(sess["unique_matched_phone_replies"], 0)
        self.assertEqual(sess["ap_replies"]["unique_seqs"], 6)
        self.assertEqual(sess["wired_replies"]["unique_seqs"], 6)
        for sq in sess["seqs"]:
            self.assertIn("phone", sq["missing_return_capture_points"])
            self.assertIs(sq["no_reply_observed_anywhere"], False)
            self.assertGreater(sq["points"]["ap"]["replies"]["count"], 0)


class OffloadAmbiguityTest(CompareIcmpPathsTest):
    """Wired target: request seen at phone and wired but not at the AP.
    This is an AP capture gap or offload, not a verdict of AP-side loss."""

    def test_request_missing_at_ap_is_ambiguous(self):
        recs_ph = []
        recs_ap = []
        recs_wr = []
        for i in range(1, 7):
            recs_ph.append(self._frame(8, 0x5000 + i, 4200, i, b"CCC",
                                       PHONE_IP, WIRED_IP, (30, i)))
            recs_ph.append(self._frame(0, 0x6000 + i, 4200, i, b"CCC",
                                       WIRED_IP, PHONE_IP, (30, i + 200)))
            recs_ap.append(self._frame(0, 0x6000 + i, 4200, i, b"CCC",
                                       WIRED_IP, PHONE_IP, (30, i + 201)))
            recs_wr.append(self._frame(8, 0x5000 + i, 4200, i, b"CCC",
                                       PHONE_IP, WIRED_IP, (30, i + 2)))
            recs_wr.append(self._frame(0, 0x6000 + i, 4200, i, b"CCC",
                                       WIRED_IP, PHONE_IP, (30, i + 202)))
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        wr = self._write_pcap(recs_wr)
        out = self._output(self._run({"phone": ph, "ap": ap, "wired": wr}))
        sess = self._session(out, 4200, "wired")
        self.assertTrue(out["passed"], out["quality"])
        self.assertTrue(sess["coverage_ok"])
        for sq in sess["seqs"]:
            self.assertIn("ap", sq["missing_forward_capture_points"])
            self.assertTrue(sq.get("offload_or_capture_gap_note"))
            self.assertNotIn("wired", sq["missing_forward_capture_points"])


class DuplicateReplyNotMaskTest(CompareIcmpPathsTest):
    """Duplicate replies for seq 1..5 must not hide the missing seq 6 reply."""

    def test_duplicate_replies_do_not_mask_missing_seq(self):
        recs_ph = []
        recs_ap = []
        recs_wr = []
        for i in range(1, 7):
            recs_ph.append(self._frame(8, 0x7000 + i, 4300, i, b"EEE",
                                       PHONE_IP, WIRED_IP, (40, i)))
            recs_ap.append(self._frame(8, 0x7000 + i, 4300, i, b"EEE",
                                       PHONE_IP, WIRED_IP, (40, i + 1)))
            recs_wr.append(self._frame(8, 0x7000 + i, 4300, i, b"EEE",
                                       PHONE_IP, WIRED_IP, (40, i + 2)))
            if i < 6:
                for dup in range(2):
                    recs_ph.append(self._frame(0, 0x8000 + i * 10 + dup, 4300,
                                               i, b"EEE", WIRED_IP, PHONE_IP,
                                               (40, i + 300 + dup)))
                    recs_ap.append(self._frame(0, 0x8000 + i * 10 + dup, 4300,
                                               i, b"EEE", WIRED_IP, PHONE_IP,
                                               (40, i + 301 + dup)))
                    recs_wr.append(self._frame(0, 0x8000 + i * 10 + dup, 4300,
                                               i, b"EEE", WIRED_IP, PHONE_IP,
                                               (40, i + 302 + dup)))
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        wr = self._write_pcap(recs_wr)
        out = self._output(self._run({"phone": ph, "ap": ap, "wired": wr}))
        sess = self._session(out, 4300, "wired")
        self.assertEqual(sess["unique_phone_requests"], 6)
        self.assertEqual(sess["unique_matched_phone_replies"], 5)
        self.assertFalse(sess["coverage_ok"])
        self.assertFalse(out["passed"], out["quality"])
        self.assertTrue(sess["duplicate_replies_cannot_mask_lost_seq"])
        by_seq = {sq["seq"]: sq for sq in sess["seqs"]}
        for i in range(1, 6):
            self.assertEqual(by_seq[i]["points"]["phone"]["replies"]["count"], 2)
            self.assertEqual(by_seq[i]["phone_matched_replies"], 2)
        self.assertIs(by_seq[6]["no_reply_observed_anywhere"], True)


class TwoIcmpIdsNotMixedTest(CompareIcmpPathsTest):
    """Two ping sessions (different ICMP ids) to the same target stay separate."""

    def test_two_ids_do_not_mix(self):
        recs_ph = []
        recs_ap = []
        for i in range(1, 4):
            for cid in (5001, 5002):
                recs_ph.append(self._frame(8, 0x9000 + cid + i, cid, i, b"G",
                                           PHONE_IP, AP_IP, (50, cid + i)))
                recs_ap.append(self._frame(8, 0x9000 + cid + i, cid, i, b"G",
                                           PHONE_IP, AP_IP, (50, cid + i + 1)))
                recs_ph.append(self._frame(0, 0xA000 + cid + i, cid, i, b"G",
                                           AP_IP, PHONE_IP, (50, cid + i + 200)))
                recs_ap.append(self._frame(0, 0xA000 + cid + i, cid, i, b"G",
                                           AP_IP, PHONE_IP, (50, cid + i + 201)))
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        out = self._output(self._run({"phone": ph, "ap": ap}, expected=3))
        self.assertTrue(out["passed"], out["quality"])
        self.assertEqual([s["id"] for s in out["targets"]], [5001, 5002])
        for cid in (5001, 5002):
            sess = self._session(out, cid, "ap")
            self.assertEqual(sess["unique_phone_requests"], 3)
            self.assertEqual(sess["unique_matched_phone_replies"], 3)
            self.assertEqual({sq["seq"] for sq in sess["seqs"]}, {1, 2, 3})


class PayloadMismatchTest(CompareIcmpPathsTest):
    """Same id/seq but mismatched payload: not the same packet, must not count
    toward phone-matched reply coverage or RTT."""

    def test_payload_mismatch_not_counted(self):
        recs_ph = [
            self._frame(8, 0xB001, 6000, 1, b"PAYLOAD-A", PHONE_IP, AP_IP, (60, 1)),
            self._frame(0, 0xC001, 6000, 1, b"REPLY-1", AP_IP, PHONE_IP, (60, 100)),
            self._frame(0, 0xC002, 6000, 1, b"REPLY-2", AP_IP, PHONE_IP, (60, 101)),
        ]
        recs_ap = [
            self._frame(8, 0xB001, 6000, 1, b"PAYLOAD-A", PHONE_IP, AP_IP, (60, 2)),
            self._frame(0, 0xC001, 6000, 1, b"REPLY-1", AP_IP, PHONE_IP, (60, 102)),
            self._frame(0, 0xC002, 6000, 1, b"REPLY-2", AP_IP, PHONE_IP, (60, 103)),
        ]
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        out = self._output(self._run({"phone": ph, "ap": ap}))
        sess = self._session(out, 6000, "ap")
        self.assertFalse(out["passed"])
        self.assertEqual(sess["unique_matched_phone_replies"], 0)
        sq = sess["seqs"][0]
        self.assertIsNone(sq.get("rtt_ms"))
        self.assertTrue(sq["payload_mismatch_with_request"])
        self.assertTrue(sq["points"]["phone"]["replies"]["payload_inconsistent"])


class PhoneSixSentTwoReceivedTest(CompareIcmpPathsTest):
    """Phone sent 6, received 2; AP answered 6 -> must be passed=False with
    phone reply coverage exactly 2 (AP-side counts stay separate)."""

    def test_phone_six_sent_two_received(self):
        recs_ph = []
        recs_ap = []
        for i in range(1, 7):
            recs_ph.append(self._frame(8, 0x2001 + i, 4500, i, b"K",
                                       PHONE_IP, AP_IP, (90, i)))
            recs_ap.append(self._frame(8, 0x2001 + i, 4500, i, b"K",
                                       PHONE_IP, AP_IP, (90, i + 1)))
            recs_ap.append(self._frame(0, 0x3001 + i, 4500, i, b"K",
                                       AP_IP, PHONE_IP, (90, i + 101)))
            if i <= 2:
                recs_ph.append(self._frame(0, 0x3001 + i, 4500, i, b"K",
                                           AP_IP, PHONE_IP, (90, i + 102)))
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        out = self._output(self._run({"phone": ph, "ap": ap}))
        sess = self._session(out, 4500, "ap")
        self.assertFalse(out["passed"])
        self.assertFalse(sess["coverage_ok"])
        self.assertEqual(sess["unique_phone_requests"], 6)
        self.assertEqual(sess["unique_matched_phone_replies"], 2)
        self.assertEqual(sess["phone_replies"]["unique_seqs"], 2)
        self.assertEqual(sess["ap_replies"]["unique_seqs"], 6)
        self.assertEqual(sess["ap_replies"]["packets"], 6)
        self.assertEqual(sess["ap_replies"]["matched_unique_seqs"], 6)


class PathFingerprintTest(CompareIcmpPathsTest):
    def test_same_seq_wrong_payload_is_not_path_presence(self):
        ph = self._write_pcap([
            self._frame(8, 1, 4501, 1, b"right", PHONE_IP, AP_IP, (1, 0)),
            self._frame(0, 2, 4501, 1, b"right", AP_IP, PHONE_IP, (1, 50)),
        ])
        ap = self._write_pcap([
            self._frame(8, 1, 4501, 1, b"wrong", PHONE_IP, AP_IP, (1, 1)),
            self._frame(0, 2, 4501, 1, b"wrong", AP_IP, PHONE_IP, (1, 51)),
        ])
        out = self._output(self._run({"phone": ph, "ap": ap}, expected=1))
        sq = self._session(out, 4501)["seqs"][0]
        self.assertIn("ap", sq["missing_forward_capture_points"])
        self.assertIn("ap", sq["missing_return_capture_points"])
        self.assertEqual(sq["points"]["ap"]["replies"]["matched_count"], 0)
        # Overall passed only concerns phone coverage and capture quality;
        # it does not certify a complete intermediate path.
        self.assertTrue(out["passed"])

    def test_nonpositive_expected_count_is_cli_error(self):
        ph = self._write_pcap([])
        for n in (0, -1):
            self.assertNotEqual(self._run({"phone": ph, "ap": ph}, expected=n).returncode, 0)


class WiredTargetMissingTest(CompareIcmpPathsTest):
    """--wired provided but phone never pinged the wired target: must report
    the missing target and fail, even if AP-target traffic is complete."""

    def test_wired_target_missing_fails(self):
        recs_ph = []
        recs_ap = []
        for i in range(1, 7):
            recs_ph.append(self._frame(8, 0x4001 + i, 4600, i, b"M",
                                       PHONE_IP, AP_IP, (100, i)))
            recs_ph.append(self._frame(0, 0x5001 + i, 4600, i, b"M",
                                       AP_IP, PHONE_IP, (100, i + 200)))
            recs_ap.append(self._frame(8, 0x4001 + i, 4600, i, b"M",
                                       PHONE_IP, AP_IP, (100, i + 1)))
            recs_ap.append(self._frame(0, 0x5001 + i, 4600, i, b"M",
                                       AP_IP, PHONE_IP, (100, i + 201)))
        # wired capture: only a non-IPv4 frame (recorded, but no wired target)
        arp = b"\xff\xff\xff\xff\xff\xff" + b"\x02\x00\x00\x00\x00\x01" \
            + struct.pack("!H", 0x0806) + b"\x00" * 28
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        wr = self._write_pcap([(110, 0, arp)])
        out = self._output(self._run({"phone": ph, "ap": ap, "wired": wr}))
        self.assertFalse(out["passed"])
        self.assertIs(out["quality"]["wired_target_missing"], True)
        sess = self._session(out, 4600, "ap")
        self.assertTrue(sess["coverage_ok"])


class TruncatedPacketCannotPassTest(CompareIcmpPathsTest):
    """snaplen/IP truncation must be counted and never green."""

    def _truncated_frame(self, icmp_id, seq, ip_id=0x6001):
        ip = build_ipv4(
            self._ip4(PHONE_IP),
            self._ip4(AP_IP),
            1,
            build_icmp_echo(8, icmp_id, seq, b"x" * 40),
            ip_id,
        )
        # IP total length claims 20+8+40 bytes, capture drops the last 20
        return (70, 0, build_ethernet(ip)[:-20])

    def test_truncated_ip_not_green(self):
        recs_ph = [self._truncated_frame(4700, 1)]
        recs_ap = [self._truncated_frame(4700, 1, ip_id=0x6002)]
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        out = self._output(self._run({"phone": ph, "ap": ap}))
        self.assertGreater(out["quality"]["snaplen_truncated_ip"]["phone"], 0)
        self.assertFalse(out["passed"])
        sess = self._session(out, 4700, "ap")
        self.assertFalse(sess["coverage_ok"])
        self.assertEqual(sess["unique_phone_requests"], 0)
        self.assertEqual(sess["unique_matched_phone_replies"], 0)


class HardFailuresTest(CompareIcmpPathsTest):
    """Truncated record data and unsupported DLT must exit non-zero."""

    def test_truncated_record_data_exits_nonzero(self):
        good = build_pcap([(0, 0, b"\x00" * 10)])
        bad = bytearray(good)
        bad[32:36] = (100).to_bytes(4, "little")
        fd, path = tempfile.mkstemp(suffix=".pcap")
        os.close(fd)
        with open(path, "wb") as fh:
            fh.write(bytes(bad))
        r = self._run({"phone": path, "ap": path})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error", r.stderr)

    def test_unsupported_dlt_exits_nonzero(self):
        path = self._write_pcap([], linktype=999)
        r = self._run({"phone": path, "ap": path})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error", r.stderr)


class QualityFlagsTest(CompareIcmpPathsTest):
    """orig > incl warns and blocks green; empty/missing-request captures
    cannot fully pass."""

    def test_orig_gt_incl_warning_blocks_green(self):
        recs = [
            self._frame(8, 0xD001, 7000, 1, b"I", PHONE_IP, AP_IP, (70, 1), sll=True),
            self._frame(0, 0xD002, 7000, 1, b"I", AP_IP, PHONE_IP, (70, 100), sll=True),
        ]
        buf = bytearray(build_pcap(recs, linktype=113))
        orig_off = 24 + 12  # record header: sec/frac/incl/orig
        cur = int.from_bytes(buf[orig_off:orig_off + 4], "little")
        buf[orig_off:orig_off + 4] = (cur + 40).to_bytes(4, "little")
        fd, path = tempfile.mkstemp(suffix=".pcap")
        os.close(fd)
        with open(path, "wb") as fh:
            fh.write(bytes(buf))
        out = self._output(self._run({"phone": path, "ap": path}))
        self.assertGreater(out["quality"]["orig_gt_incl"]["phone"], 0)
        self.assertFalse(out["passed"])

    def test_empty_phone_pcap_cannot_pass(self):
        ph = self._write_pcap([])
        ap = self._write_pcap([])
        out = self._output(self._run({"phone": ph, "ap": ap}))
        self.assertFalse(out["passed"])
        self.assertIn("phone", out["quality"]["empty_captures"])

    def test_phone_without_requests_cannot_pass(self):
        recs_ph = [self._frame(0, 0xE001, 8000, 1, b"J",
                               AP_IP, PHONE_IP, (80, 1))]
        recs_ap = [
            self._frame(8, 0xE001, 8000, 1, b"J",
                        PHONE_IP, AP_IP, (80, 2)),
            self._frame(0, 0xE001, 8000, 1, b"J",
                        AP_IP, PHONE_IP, (80, 3)),
        ]
        ph = self._write_pcap(recs_ph)
        ap = self._write_pcap(recs_ap)
        out = self._output(self._run({"phone": ph, "ap": ap}))
        self.assertFalse(out["passed"])
        self.assertIs(out["quality"]["missing_phone_requests"], True)


if __name__ == "__main__":
    unittest.main()
