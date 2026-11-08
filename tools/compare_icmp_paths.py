#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-capture ICMP echo path alignment (Python stdlib only).

Aligns classic pcap captures taken at three observation points -- phone
(source of the pings), AP (Wi-Fi access point), and wired (wired-side
monitor) -- for ICMP echo requests/replies between the phone and the AP
itself (AP-local target) or between the phone and a wired target.

Parsing, magic/DLT validation and fixture builders are reused as-is from
tools/summarize_icmp_pcap.py (this tool never modifies that module).

Scientific gate semantics (strict): the trusted success verdict ``passed``
means the PHONE capture itself saw exactly ``expected_count`` unique requests
and exactly ``expected_count`` unique *matched* replies -- where a reply is
matched only when it shares endpoint, ICMP id, seq and payload digest with a
non-truncated phone request -- and the capture quality gates hold (no empty
capture, phone requests present, no IP/snaplen truncation, no orig>incl
warning, and a required wired target is actually pinged when --wired is
given).  Counts from the AP/wired captures are reported as separate fields
and never serve as substitutes for phone-side coverage.  ``passed`` is a
coverage/quality verdict, not a claim about real link-layer loss: missing
observations only prove capture gaps, and offload/capture ambiguity is
labeled explicitly.

Only within-capture timestamps are used (RTT on the phone capture, duplicate
intervals per point); no cross-host clock math is performed.

Exit status: the raw JSON report exits 0 even when ``passed`` is false;
non-zero is reserved for CLI/parse failures (unreadable files, unsupported
linktypes, structurally incomplete/truncated pcap records).
"""

import argparse
import hashlib
import json
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TOOLS_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.summarize_icmp_pcap import (  # noqa: E402
    PcapError,
    _DLT_NAME,
    parse_pcap,
    unwrap_link,
)

_ETHERTYPE_IPV4 = 0x0800
_TYPE_ECHO_REQUEST = 8
_TYPE_ECHO_REPLY = 0


def _fmt_ip(b):
    return ".".join(str(x) for x in b)


def _ip4(s):
    return bytes(int(x) for x in s.split("."))


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


class CaptureError(Exception):
    pass


def _parse_ip_tolerant(data, start):
    """Mirror of summarize.parse_ipv4 that survives snaplen truncation after
    the IP header: it reports `truncated` instead of refusing the record."""
    n = len(data)
    if n - start < 20:
        return None
    b0 = data[start]
    if (b0 >> 4) != 4:
        return None
    ihl = (b0 & 0x0F) * 4
    if ihl < 20 or ihl > n - start:
        return None
    total = int.from_bytes(data[start + 2:start + 4], "big")
    frag = int.from_bytes(data[start + 6:start + 8], "big")
    if (frag & 0x1FFF) or (frag & 0x2000):
        return {"fragment": True}
    if total < ihl:
        return None
    return {
        "src": data[start + 12:start + 16],
        "dst": data[start + 16:start + 20],
        "proto": data[start + 9],
        "hdro": start + ihl,
        "end": start + total,
        "truncated": total > n - start,
    }


def _classify_frame(data, linktype, t, incl, orig):
    base = {"t": t, "incl": incl, "orig": orig}
    ul = unwrap_link(data, linktype)
    if ul is None:
        return dict(base, kind="bad_link")
    et, off = ul
    if et is not None and et != _ETHERTYPE_IPV4:
        return dict(base, kind="non_ipv4")
    ip = _parse_ip_tolerant(data, off)
    if ip is None:
        return dict(base, kind="bad_ip")
    if ip.get("fragment"):
        return dict(base, kind="fragment")
    if ip["proto"] != 1:
        return dict(base, kind="non_icmp")
    avail_end = min(ip["end"], len(data))
    if ip["hdro"] + 8 > avail_end:
        return dict(base, kind="icmp_truncated",
                    truncated_ip=ip["truncated"])
    icmp_type = data[ip["hdro"]]
    icmp_code = data[ip["hdro"] + 1]
    if icmp_type not in (_TYPE_ECHO_REQUEST, _TYPE_ECHO_REPLY) or icmp_code != 0:
        return dict(base, kind="icmp_other", truncated_ip=ip["truncated"])
    return dict(
        base,
        kind="echo_request" if icmp_type == _TYPE_ECHO_REQUEST else "echo_reply",
        src=_fmt_ip(ip["src"]),
        dst=_fmt_ip(ip["dst"]),
        ip_id=int.from_bytes(data[off + 4:off + 6], "big"),
        icmp_id=int.from_bytes(data[ip["hdro"] + 4:ip["hdro"] + 6], "big"),
        seq=int.from_bytes(data[ip["hdro"] + 6:ip["hdro"] + 8], "big"),
        payload=data[ip["hdro"] + 8:avail_end],
        truncated_ip=ip["truncated"],
    )


def parse_capture(path):
    try:
        with open(path, "rb") as fh:
            buf = fh.read()
    except OSError as exc:
        raise CaptureError("cannot read %s: %s" % (path, exc))
    endian, unit, network = parse_pcap(buf)  # raises PcapError on magic/DLT
    frames = []
    captured = 0
    orig_gt_incl = 0
    pos = 24
    frac_div = 1e9 if unit == "nanoseconds" else 1e6
    while pos < len(buf):
        if len(buf) - pos < 16:
            raise CaptureError("truncated record header at offset %d" % pos)
        sec, frac, incl, orig = struct.unpack(endian + "IIII", buf[pos:pos + 16])
        pos += 16
        if incl > len(buf) - pos:
            raise CaptureError("truncated record data at offset %d" % (pos - 16))
        if orig > incl:
            orig_gt_incl += 1
        captured += 1
        data = buf[pos:pos + incl]
        pos += incl
        t = sec + (frac / frac_div)
        frames.append(_classify_frame(data, network, t, incl, orig))

    ignored = {
        "non_ipv4": 0,
        "bad_ip": 0,
        "fragments": 0,
        "non_icmp": 0,
        "icmp_truncated": 0,
        "icmp_other": 0,
        "bad_link": 0,
    }
    echo_requests = 0
    echo_replies = 0
    snaplen_truncated_ip = 0
    for f in frames:
        kind = f["kind"]
        if f.get("truncated_ip"):
            snaplen_truncated_ip += 1
        if kind == "fragment":
            ignored["fragments"] += 1
        elif kind in ignored:
            ignored[kind] += 1
        elif kind == "echo_request":
            echo_requests += 1
        elif kind == "echo_reply":
            echo_replies += 1
    return {
        "file": path,
        "linktype": _DLT_NAME[network],
        "endian": endian,
        "timestamp_unit": unit,
        "captured": captured,
        "orig_gt_incl": orig_gt_incl,
        "snaplen_truncated_ip": snaplen_truncated_ip,
        "ignored": ignored,
        "echo_requests": echo_requests,
        "echo_replies": echo_replies,
        "frames": frames,
    }


def _summarize_kind(frames):
    if not frames:
        return {
            "count": 0,
            "ip_ids": [],
            "payload_digests": [],
            "duplicate_intervals_ms": [],
            "ip_truncated_count": 0,
            "payload_inconsistent": False,
        }
    ts = sorted(f["t"] for f in frames)
    digests = sorted({_digest(f["payload"]) for f in frames})
    return {
        "count": len(frames),
        "ip_ids": [f["ip_id"] for f in frames],
        "payload_digests": digests,
        "duplicate_intervals_ms": [
            round((ts[i + 1] - ts[i]) * 1000.0, 3)
            for i in range(len(ts) - 1)
        ],
        "ip_truncated_count": sum(1 for f in frames if f.get("truncated_ip")),
        "payload_inconsistent": len(digests) > 1,
    }


def _dir_summary(frames, ref_fp=None):
    """Direction summary at one capture point.  `ref_fp` is a dict of
    seq -> set(payload digests) defining the fingerprint group this direction
    must belong to in order to count as matched (no other payload may pose as
    path presence)."""
    seqs_all = {f["seq"] for f in frames}
    good = [f for f in frames if not f.get("truncated_ip")]
    seqs_good = {f["seq"] for f in good}
    matched = 0
    matched_seqs = set()
    if ref_fp is not None:
        for f in good:
            if f["seq"] in ref_fp and _digest(f["payload"]) in ref_fp[f["seq"]]:
                matched += 1
                matched_seqs.add(f["seq"])
    return {
        "packets": len(frames),
        "unique_seqs": len(seqs_all),
        "good_unique_seqs": len(seqs_good),
        "matched_packets": matched,
        "matched_unique_seqs": len(matched_seqs),
    }


def analyze(files, cfg):
    caps = {}
    for name in ("phone", "ap", "wired"):
        if files.get(name):
            caps[name] = parse_capture(files[name])

    quality = {
        "empty_captures": [],
        "missing_phone_requests": True,
        "unexpected_phone_request_targets": [],
        "wired_target_missing": False,
        "orig_gt_incl": {},
        "snaplen_truncated_ip": {},
        "passed_meaning": (
            "passed=手机端 request 与匹配回包（同 id/seq/端点/payload 且未截断）"
            "唯一覆盖达到 expected-count，且捕获质量门通过：无空捕获、无缺失 phone 请求、"
            "无 IP/snaplen 截断、无 orig>incl 警告、要求 wired 目标时确有 wired 目标请求。"
            "raw 数据报告 exit 0；CLI parse/DLT/记录截断错误仍非零。"
        ),
        "tcpdump_drops_reminder": (
            "请另行核对各端 tcpdump/抓包工具的 dropped 计数与抓包时间窗覆盖；"
            "本报告只反映已捕获记录的对照，无法证明真实链路丢包。"
        ),
    }
    for name, cap in caps.items():
        quality["orig_gt_incl"][name] = cap["orig_gt_incl"]
        quality["snaplen_truncated_ip"][name] = cap["snaplen_truncated_ip"]
        if cap["captured"] == 0:
            quality["empty_captures"].append(name)

    if "phone" in caps:
        phone_req_any = [
            f for f in caps["phone"]["frames"]
            if f["kind"] == "echo_request" and f["src"] == cfg["phone_ip"]
        ]
        quality["missing_phone_requests"] = not phone_req_any
        for f in phone_req_any:
            if f["dst"] not in (cfg["ap_ip"], cfg["wired_ip"]) and \
                    f["dst"] not in quality["unexpected_phone_request_targets"]:
                quality["unexpected_phone_request_targets"].append(f["dst"])

    sessions = {}
    for point_name, cap in caps.items():
        for f in cap["frames"]:
            kind = f["kind"]
            if kind == "echo_request" and f["src"] == cfg["phone_ip"] and \
                    f["dst"] in (cfg["ap_ip"], cfg["wired_ip"]):
                sid = (f["dst"], f["icmp_id"])
                sess = sessions.setdefault(
                    sid,
                    {"target": "ap" if f["dst"] == cfg["ap_ip"] else "wired",
                     "id": f["icmp_id"], "destination_ip": f["dst"],
                     "seqs": {}},
                )
                sess["seqs"].setdefault(f["seq"], {}).setdefault(
                    point_name, {"requests": [], "replies": []}
                )["requests"].append(f)
            elif kind == "echo_reply" and f["dst"] == cfg["phone_ip"] and \
                    f["src"] in (cfg["ap_ip"], cfg["wired_ip"]):
                sid = (f["src"], f["icmp_id"])
                sess = sessions.setdefault(
                    sid,
                    {"target": "ap" if f["src"] == cfg["ap_ip"] else "wired",
                     "id": f["icmp_id"], "destination_ip": f["src"],
                     "seqs": {}},
                )
                sess["seqs"].setdefault(f["seq"], {}).setdefault(
                    point_name, {"requests": [], "replies": []}
                )["replies"].append(f)

    if files.get("wired") and not any(
        f["dst"] == cfg["wired_ip"] and not f.get("truncated_ip")
        for f in phone_req_any
    ):
        quality["wired_target_missing"] = True

    expected = cfg["expected_count"]
    target_entries = []
    for (dst, cid), sess in sorted(sessions.items()):
        exp_points = ["phone", "ap"] if sess["target"] == "ap" else \
            ["phone", "ap", "wired"]
        pt_req = {}
        pt_rep = {}
        for seq, raw in sess["seqs"].items():
            for point, pr in raw.items():
                pt_req.setdefault(point, []).extend(pr["requests"])
                pt_rep.setdefault(point, []).extend(pr["replies"])
        obs_points = list(exp_points) + [
            p for p in sorted(pt_req)
            if p not in exp_points and (pt_req[p] or pt_rep.get(p))
        ]

        phone_reqs = pt_req.get("phone", [])
        phone_good_reqs = [f for f in phone_reqs if not f.get("truncated_ip")]
        req_fp = {}
        for f in phone_good_reqs:
            req_fp.setdefault(f["seq"], set()).add(_digest(f["payload"]))
        phone_good_rep = [
            f for f in pt_rep.get("phone", [])
            if not f.get("truncated_ip")
        ]
        matched_rep_by_seq = {}
        for f in phone_good_rep:
            if f["seq"] in req_fp and _digest(f["payload"]) in req_fp[f["seq"]]:
                matched_rep_by_seq.setdefault(f["seq"], []).append(f)
        unique_phone = sorted(req_fp)
        unique_matched = sorted(matched_rep_by_seq)
        seq_entries = []
        for seq in sorted(sess["seqs"]):
            raw = sess["seqs"][seq]
            points = {}
            for point in obs_points:
                pr = raw.get(point, {"requests": [], "replies": []})
                points[point] = {
                    "requests": _summarize_kind(pr["requests"]),
                    "replies": _summarize_kind(pr["replies"]),
                }
                for direction in ("requests", "replies"):
                    points[point][direction]["matched_count"] = _dir_summary(
                        pr[direction], req_fp
                    )["matched_packets"]
            missing_fwd = [
                p for p in exp_points
                if points[p]["requests"]["matched_count"] == 0
            ]
            missing_ret = [
                p for p in exp_points
                if points[p]["replies"]["matched_count"] == 0
            ]
            any_reply = any(points[p]["replies"]["count"] for p in exp_points)
            entry = {
                "seq": seq,
                "points": points,
                "missing_forward_capture_points": missing_fwd,
                "missing_return_capture_points": missing_ret,
                "no_reply_observed_anywhere": not any_reply,
                "phone_matched_replies": len(matched_rep_by_seq.get(seq, [])),
            }
            mreq = [f for f in phone_good_reqs if f["seq"] == seq]
            mrep = matched_rep_by_seq.get(seq, [])
            if mreq and mrep:
                t_req = min(f["t"] for f in mreq)
                t_rep = min(f["t"] for f in mrep)
                entry["rtt_ms"] = round((t_rep - t_req) * 1000.0, 3)

            all_req_digests = set()
            all_rep_digests = set()
            for p in exp_points:
                all_req_digests.update(points[p]["requests"]["payload_digests"])
                all_rep_digests.update(points[p]["replies"]["payload_digests"])
            if all_req_digests and all_rep_digests and \
                    not (all_rep_digests & all_req_digests):
                entry["payload_mismatch_with_request"] = True
            if any(points[p]["replies"]["payload_inconsistent"] for p in exp_points):
                entry["duplicate_payload_inconsistent"] = True

            note = None
            if sess["target"] == "wired":
                if points["phone"]["requests"]["matched_count"] and \
                        points["wired"]["requests"]["matched_count"] and \
                        points["ap"]["requests"]["matched_count"] == 0:
                    note = ("phone 与 wired 均观测到 request 而 AP 未观测："
                            "可能是 AP 侧抓包缺失或 offload 旁路，不能直接判 AP 丢包")
                elif points["phone"]["replies"]["matched_count"] and \
                        points["wired"]["replies"]["matched_count"] and \
                        points["ap"]["replies"]["matched_count"] == 0:
                    note = ("phone 与 wired 均观测到 reply 而 AP 未观测："
                            "可能是 AP 侧抓包缺失或 offload 旁路，不能直接判 AP 丢包")
            if note:
                entry["offload_or_capture_gap_note"] = note
            seq_entries.append(entry)

        phone_requests = _dir_summary(phone_reqs)
        phone_replies = _dir_summary(pt_rep.get("phone", []), req_fp)
        ap_requests = _dir_summary(pt_req.get("ap", []), req_fp)
        ap_replies = _dir_summary(pt_rep.get("ap", []), req_fp)
        wired_requests = _dir_summary(pt_req.get("wired", []), req_fp)
        wired_replies = _dir_summary(pt_rep.get("wired", []), req_fp)
        truncated_frames = [
            f
            for frames in (*pt_req.values(), *pt_rep.values())
            for f in frames
            if f.get("truncated_ip")
        ]
        coverage_ok = (
            len(unique_phone) == expected and
            len(unique_matched) == expected
        )
        target_entries.append({
            "target": sess["target"],
            "destination_ip": sess["destination_ip"],
            "id": sess["id"],
            "expected_count": expected,
            "unique_phone_requests": len(unique_phone),
            "unique_matched_phone_replies": len(unique_matched),
            "phone_requests": phone_requests,
            "phone_replies": phone_replies,
            "duplicate_phone_requests": (
                phone_requests["packets"] - phone_requests["unique_seqs"]
            ),
            "duplicate_phone_replies": (
                phone_replies["packets"] - phone_replies["unique_seqs"]
            ),
            "ap_requests": ap_requests,
            "ap_replies": ap_replies,
            "wired_requests": wired_requests,
            "wired_replies": wired_replies,
            "coverage_ok": coverage_ok,
            "duplicate_replies_cannot_mask_lost_seq": (
                phone_replies["packets"] >= expected and
                len(unique_matched) < expected
            ),
            "truncated_frames": len(truncated_frames),
            "seqs": seq_entries,
        })

    passed = (
        not quality["empty_captures"] and
        not quality["missing_phone_requests"] and
        not quality["wired_target_missing"] and
        all(e["coverage_ok"] for e in target_entries) and
        all(cap["orig_gt_incl"] == 0 for cap in caps.values()) and
        all(cap["snaplen_truncated_ip"] == 0 for cap in caps.values())
    )
    return {
        "tool": "compare_icmp_paths",
        "arguments": {
            "phone": files.get("phone"),
            "ap": files.get("ap"),
            "wired": files.get("wired"),
            "phone_ip": cfg["phone_ip"],
            "ap_ip": cfg["ap_ip"],
            "wired_ip": cfg["wired_ip"],
            "expected_count": expected,
        },
        "captures": {
            name: {k: v for k, v in cap.items() if k != "frames"}
            for name, cap in caps.items()
        },
        "targets": target_entries,
        "quality": quality,
        "passed": passed,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Align ICMP echo captures across phone/AP/wired pcaps."
    )
    ap.add_argument("--phone", required=True, help="phone-side pcap")
    ap.add_argument("--ap", required=True, help="AP-side pcap")
    ap.add_argument("--wired", default=None, help="wired-side pcap (optional)")
    ap.add_argument("--phone-ip", required=True, help="phone IP")
    ap.add_argument("--ap-ip", required=True, help="AP IP")
    ap.add_argument("--wired-ip", required=True, help="wired IP")
    ap.add_argument("--expected-count", type=int, default=6,
                    help="expected unique pings per target session")
    args = ap.parse_args(argv)
    if args.expected_count <= 0:
        ap.error("--expected-count must be positive")

    files = {
        "phone": args.phone,
        "ap": args.ap,
        "wired": args.wired,
    }
    cfg = {
        "phone_ip": args.phone_ip,
        "ap_ip": args.ap_ip,
        "wired_ip": args.wired_ip,
        "expected_count": args.expected_count,
    }
    try:
        report = analyze(files, cfg)
    except (PcapError, CaptureError) as exc:
        print(json.dumps({
            "error": str(exc),
            "files": files,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
