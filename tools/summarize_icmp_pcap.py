#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only classic pcap ICMP echo summarizer (Python stdlib only).

Parses classic pcap files produced by tcpdump (little/big-endian, micro- and
nanosecond time magics) for IPv4 ICMP echo requests/replies.  Supports
DLT_EN10MB (1) with optional 802.1Q/QinQ VLAN tags, Linux cooked SLL (113),
SLL2 (276) and RAW (101).  Emits one JSON object per input file on stdout.

Groups echo packets by (src, dst, ICMP type, id, seq).  Reports the overall
request/reply packet counts and unique-group count, then lists only duplicate
groups (group packet count > 1) with their IP IDs, arrival times relative to
the first packet of the group in ms, and whether the entire ICMP payload bytes
are identical across the group.
"""

import argparse
import json
import struct
import sys


# Supported classic pcap magics -> (struct endian prefix, time unit)
_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", "microseconds"),
    b"\xa1\xb2\xc3\xd4": (">", "microseconds"),
    b"\x4d\x3c\xb2\xa1": ("<", "nanoseconds"),
    b"\xa1\xb2\x3c\x4d": (">", "nanoseconds"),
}

_DLT_NAME = {
    1: "EN10MB",
    101: "RAW",
    113: "SLL",
    276: "SLL2",
}

_ETHERTYPE_IPV4 = 0x0800
_VLAN_TAGS = (0x8100, 0x88A8, 0x9100)

_TYPE_ECHO_REQUEST = 8
_TYPE_ECHO_REPLY = 0


class PcapError(Exception):
    pass


def _fmt_ip(b):
    return ".".join(str(x) for x in b)


def _csum(data):
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


# --- synthetic fixture builders (used by --self-test only) ---------------

def build_ipv4(src, dst, proto, payload, ip_id, frag=0x4000, ttl=64):
    total = 20 + len(payload)
    hdr = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total, ip_id, frag, ttl, proto, 0, src, dst,
    )
    chk = _csum(hdr)
    hdr = hdr[:10] + struct.pack("!H", chk) + hdr[12:]
    return hdr + payload


def build_icmp_echo(icmp_type, icmp_id, seq, payload=b""):
    body = struct.pack("!BBHHH", icmp_type, 0, 0, icmp_id, seq) + payload
    chk = _csum(body)
    body = body[:2] + struct.pack("!H", chk) + body[4:]
    return body


def build_ethernet(
    payload,
    ethertype=0x0800,
    src_mac=b"\x02\x00\x00\x00\x00\x01",
    dst_mac=b"\x02\x00\x00\x00\x00\x02",
):
    return dst_mac + src_mac + struct.pack("!H", ethertype) + payload


def build_pcap(records, linktype=1, endian="<"):
    """records: list of (ts_sec, ts_usec, data)."""
    out = bytearray()
    out += struct.pack(endian + "IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for sec, usec, data in records:
        out += struct.pack(endian + "IIII", sec, usec, len(data), len(data))
        out += data
    return bytes(out)


# --- pcap parsing --------------------------------------------------------

def parse_pcap(buf):
    if len(buf) < 24:
        raise PcapError("file too short for pcap global header")
    magic = buf[:4]
    if magic not in _MAGICS:
        raise PcapError("unrecognized pcap magic %r" % magic)
    endian, unit = _MAGICS[magic]
    v_maj, v_min, zone, sig, snaplen, network = struct.unpack(
        endian + "HHiIII", buf[4:24]
    )
    if network not in _DLT_NAME:
        raise PcapError("unsupported linktype %r" % network)
    return endian, unit, network


def unwrap_link(data, linktype):
    """Return (ethertype_or_None, payload_offset); None if undecodable."""
    n = len(data)
    if linktype == 1:  # EN10MB
        if n < 14:
            return None
        et = int.from_bytes(data[12:14], "big")
        off = 14
    elif linktype == 113:  # Linux cooked SLL v1
        if n < 16:
            return None
        et = int.from_bytes(data[14:16], "big")
        off = 16
    elif linktype == 276:  # Linux cooked SLL v2
        if n < 20:
            return None
        et = int.from_bytes(data[0:2], "big")
        off = 20
    elif linktype == 101:  # RAW IPv4
        return None, 0
    else:
        return None
    depth = 0
    while et in _VLAN_TAGS and depth < 8:
        if n - off < 4:
            return None
        et = int.from_bytes(data[off + 2:off + 4], "big")
        off += 4
        depth += 1
    return et, off


def parse_ipv4(data, start):
    n = len(data)
    if n - start < 20:
        return None
    b0 = data[start]
    if (b0 >> 4) != 4:
        return None
    ihl = (b0 & 0x0F) * 4
    if ihl < 20:
        return None
    total = int.from_bytes(data[start + 2:start + 4], "big")
    frag = int.from_bytes(data[start + 6:start + 8], "big")
    if (frag & 0x1FFF) or (frag & 0x2000):
        return None  # fragment: never mistake for ICMP
    if total < ihl or total > n - start:
        return None  # truncated / bad total length
    return {
        "src": data[start + 12:start + 16],
        "dst": data[start + 16:start + 20],
        "proto": data[start + 9],
        "hdro": start + ihl,
        "end": start + total,
    }


def decode_record(data, linktype):
    ul = unwrap_link(data, linktype)
    if ul is None:
        return None
    et, off = ul
    if et is not None and et != _ETHERTYPE_IPV4:
        return None
    ip = parse_ipv4(data, off)
    if ip is None or ip["proto"] != 1:
        return None
    need = ip["hdro"] + 8
    if need > ip["end"] or need > len(data):
        return None
    icmp_type = data[ip["hdro"]]
    if icmp_type not in (_TYPE_ECHO_REQUEST, _TYPE_ECHO_REPLY) or data[ip["hdro"] + 1] != 0:
        return None
    icmp_id = int.from_bytes(data[ip["hdro"] + 4:ip["hdro"] + 6], "big")
    seq = int.from_bytes(data[ip["hdro"] + 6:ip["hdro"] + 8], "big")
    payload = data[need:ip["end"]]
    key = (
        _fmt_ip(ip["src"]),
        _fmt_ip(ip["dst"]),
        icmp_type,
        icmp_id,
        seq,
    )
    return key, int.from_bytes(data[off + 4:off + 6], "big"), payload


def summarize(buf, name):
    endian, unit, network = parse_pcap(buf)
    rows = []  # (absolute_time, key, ip_id, payload)
    captured = 0
    ignored = 0
    pos = 24
    frac_div = 1e9 if unit == "nanoseconds" else 1e6
    while pos < len(buf):
        if len(buf) - pos < 16:
            raise PcapError("truncated record header at offset %d" % pos)
        sec, frac, incl, orig = struct.unpack(endian + "IIII", buf[pos:pos + 16])
        pos += 16
        if incl > len(buf) - pos:
            raise PcapError("truncated record data at offset %d" % (pos - 16))
        captured += 1
        data = buf[pos:pos + incl]
        pos += incl
        t = sec + (frac / frac_div)
        r = decode_record(data, network)
        if r is None:
            ignored += 1
        else:
            rows.append((t, r[0], r[1], r[2]))

    groups = {}
    for t, key, ip_id, payload in rows:
        groups.setdefault(key, []).append((t, ip_id, payload))

    requests = sum(len(v) for k, v in groups.items() if k[2] == _TYPE_ECHO_REQUEST)
    replies = sum(len(v) for k, v in groups.items() if k[2] == _TYPE_ECHO_REPLY)

    duplicate_groups = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        first_t = items[0][0]
        relative = [round((t - first_t) * 1000.0, 3) for t, _, _ in items]
        ip_ids = [ip_id for _, ip_id, _ in items]
        payloads = [payload for _, _, payload in items]
        duplicate_groups.append(
            {
                "src": key[0],
                "dst": key[1],
                "icmp_type": key[2],
                "id": key[3],
                "seq": key[4],
                "count": len(items),
                "ip_ids": ip_ids,
                "relative_arrival_ms": relative,
                "payload_identical": len(set(payloads)) == 1,
            }
        )

    duplicate_groups.sort(
        key=lambda d: (d["src"], d["dst"], d["icmp_type"], d["id"], d["seq"])
    )

    return {
        "input": name,
        "linktype": _DLT_NAME[network],
        "endian": endian,
        "timestamp_unit": unit,
        "captured": captured,
        "ignored": ignored,
        "icmp_echo_requests": requests,
        "icmp_echo_replies": replies,
        "unique_groups": len(groups),
        "duplicate_groups": duplicate_groups,
    }


# --- self-test -----------------------------------------------------------

def self_test():
    def frame(icmp_type, ip_id, icmp_id, seq, payload=b"", t=(0, 0)):
        ip = build_ipv4(
            b"\x0a\x00\x00\x01",
            b"\x0a\x00\x00\x02",
            1,
            build_icmp_echo(icmp_type, icmp_id, seq, payload),
            ip_id,
        )
        return (t[0], t[1], build_ethernet(ip))

    recs = [
        frame(8, 0x1111, 1000, 1, b"hello"),
        # two byte-identical replies: same id/seq/IP ID
        frame(0, 0x2222, 1000, 1, b"world"),
        frame(0, 0x2222, 1000, 1, b"world"),
        # distinct seq -> its own group, not a duplicate
        frame(0, 0x3333, 1000, 2, b"other"),
    ]
    res = summarize(build_pcap(recs), "selftest")

    assert res["icmp_echo_requests"] == 1, res
    assert res["icmp_echo_replies"] == 3, res
    assert res["unique_groups"] == 3, res
    dups = res["duplicate_groups"]
    assert len(dups) == 1, dups
    d = dups[0]
    assert d["icmp_type"] == 0 and d["id"] == 1000 and d["seq"] == 1, d
    assert d["count"] == 2, d
    assert d["payload_identical"] is True, d
    assert d["ip_ids"] == [0x2222, 0x2222], d
    assert d["relative_arrival_ms"][0] == 0.0, d
    for endian in ('<', '>'):
        assert summarize(build_pcap(recs, endian=endian), 'endian')["duplicate_groups"] == dups
    no_icmp = build_ethernet(build_ipv4(b'\x0a\x00\x00\x01', b'\x0a\x00\x00\x02', 1, b'', 1))
    assert summarize(build_pcap([(0,0,no_icmp)]), 'short-ip')["ignored"] == 1

    def expect_err(buf, desc):
        try:
            summarize(buf, "bad")
        except PcapError:
            return
        raise AssertionError("expected PcapError for " + desc)

    expect_err(b"XXXX" + b"\x00" * 20, "bad magic")

    good = build_pcap([(0, 0, b"\x00" * 10)])
    bad = bytearray(good)
    bad[32:36] = (100).to_bytes(4, "little")  # incl_len > remaining bytes
    expect_err(bytes(bad), "truncated record data")

    expect_err(build_pcap([], linktype=999), "unsupported linktype")

    print("SELF-TEST PASS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Summarize ICMP echo duplicates in classic pcap files."
    )
    ap.add_argument("pcaps", nargs="*", metavar="PCAP", help="input pcap paths")
    ap.add_argument("--self-test", action="store_true", help="run in-memory self-test")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.pcaps:
        ap.error("at least one pcap path is required (or --self-test)")

    rc = 0
    for path in args.pcaps:
        try:
            with open(path, "rb") as f:
                buf = f.read()
            print(json.dumps(summarize(buf, path), ensure_ascii=False, sort_keys=True))
        except (PcapError, OSError) as exc:
            rc = 1
            print(
                json.dumps({"input": path, "error": str(exc)}, ensure_ascii=False),
                file=sys.stderr,
            )
    return rc


if __name__ == "__main__":
    sys.exit(main())
