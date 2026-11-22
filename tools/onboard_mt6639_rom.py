#!/usr/bin/env python3
"""Offline ROM / device-sample onboarding and 16-function ELF64 verifier for MT6639.

Supports:
1. Listing and selectively reconstructing partitions from Android OTA `.zip` URLs
   (`payload.bin` CrAU v2) using HTTP Range requests with Xiaomi CDN fallbacks.
2. Inspecting Android `boot.img` kernel release (`uname -r`) and `STATX_MNT_ID_UNIQUE`
   capability clues, plus extracting target files from EROFS/APEX images via `erofs-utils`.
3. Comparing candidate `wlan_drv_gen4m_eap_6639.ko` ELF64 modules against a reviewed
   reference across the 16 configuration, capability, probe, and regulatory functions.
4. Validating `--sample-dir` live device exports in <60s and formatting `compatibility.conf`
   rows for `mt6639-6g`, `mt6639-bt`, and `mt6639-hotspot`.
"""
from __future__ import annotations

import argparse
import bisect
import bz2
from collections import OrderedDict
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    from compression import zstd as _py_zstd  # Python 3.14+
except ImportError:
    _py_zstd = None


TARGET_FUNCTIONS = (
    "wlanCfgInit",
    "wlanCfgParse",
    "wlanCfgGetUint32",
    "wlanGetConfig",
    "wlanGetParseConfig",
    "nicCfgChipCap6GCap",
    "wlanProbe",
    "wlanRemove",
    "wlanFuncOn",
    "wlanFuncOff",
    "wlanFuncOnImpl",
    "wlanFuncOffImpl",
    "kalRequestFirmware",
    "rlmDomainIsLegalChannel",
    "wlanInitFeatureOptionImpl",
    "wlanLoadDefaultCustomerSetting",
)

BADGE_REQUIRED_RESOURCES = (
    "wifi_band",
    "band_wifi_5g",
    "band_wifi_5g_connected",
    "band_wifi_24g",
    "band_wifi_24g_connected",
    "wifi_signal_icon_color",
    "wifi_signal_icon_connected_color",
)

BADGE_REQUIRED_CLASSES = (
    "com/android/settings/wifi/MiuiWifiEntryPreference",
    "com/android/settings/wifi/MiuiAccessPointPreference",
    "com/android/settings/MiuiTetherSettings",
    "com/android/settings/AllInOneTetherSettings",
)

DEFAULT_CDNS = (
    "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com",
    "https://cdnorg.d.miui.com",
    "https://bn.d.miui.com",
    "https://bigota.d.miui.com",
)

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_ZERO = 6
OP_REPLACE_XZ = 8
OP_REPLACE_ZSTD = 14

EM_AARCH64 = 183
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
STT_SECTION = 3


class OnboardError(RuntimeError):
    """Explicit failure during ROM extraction or static compatibility verification."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _decompress_zstd(data: bytes) -> bytes:
    if _py_zstd is not None:
        return _py_zstd.decompress(data)
    proc = subprocess.run(["zstd", "-d", "-c"], input=data, capture_output=True, check=False)
    if proc.returncode != 0:
        raise OnboardError(f"zstd decompression failed: {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout


def _decompress_lz4(data: bytes) -> bytes:
    for args in (["lz4", "-d", "-c"],):
        proc = subprocess.run(args, input=data, capture_output=True, check=False)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    raise OnboardError("lz4 decompression failed")


def candidate_urls(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return [url]
    rel = parsed.path.lstrip("/")
    urls = []
    for base in DEFAULT_CDNS:
        candidate = f"{base}/{rel}"
        if candidate not in urls:
            urls.append(candidate)
    if url not in urls:
        urls.append(url)
    return urls


class RangeClient:
    """Bounded HTTP Range reader with automatic Xiaomi CDN failover."""

    def __init__(self, url: str, timeout: float = 30.0):
        self.urls = candidate_urls(url)
        self.active_url: str | None = None
        self.total_size: int | None = None
        self.timeout = timeout

    def _probe(self) -> None:
        if self.active_url is not None and self.total_size is not None:
            return
        last_err: Exception | None = None
        for candidate in self.urls:
            req = urllib.request.Request(
                candidate,
                headers={"Range": "bytes=0-0", "User-Agent": "curl/8.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    cr = resp.headers.get("Content-Range", "")
                    if resp.status == 206 and "/" in cr:
                        self.total_size = int(cr.rsplit("/", 1)[1])
                        self.active_url = candidate
                        return
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last_err = exc
        raise OnboardError(f"All HTTP Range mirrors failed for {self.urls[0]}: {last_err}")

    def read_range(self, start: int, length: int) -> bytes:
        if length == 0:
            return b""
        self._probe()
        assert self.active_url is not None and self.total_size is not None
        end = min(start + length - 1, self.total_size - 1)
        if start < 0 or start > end:
            raise OnboardError(f"Invalid range start={start} length={length} total={self.total_size}")
        expected = end - start + 1
        last_err: Exception | None = None
        for url in [self.active_url] + [u for u in self.urls if u != self.active_url]:
            for _ in range(3):
                req = urllib.request.Request(
                    url,
                    headers={"Range": f"bytes={start}-{end}", "User-Agent": "curl/8.0"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=max(self.timeout, 120.0)) as resp:
                        if resp.status != 206:
                            raise OnboardError(f"Expected 206 Partial Content, got {resp.status}")
                        buf = resp.read()
                        if len(buf) != expected:
                            raise OnboardError(f"Short read {len(buf)} != {expected}")
                        self.active_url = url
                        return buf
                except (OSError, urllib.error.URLError, OnboardError) as exc:
                    last_err = exc
        raise OnboardError(f"Range fetch failed ({start}+{length}): {last_err}")


def locate_payload_in_remote_zip(client: RangeClient) -> tuple[int, int]:
    client._probe()
    assert client.total_size is not None
    tail_len = min(client.total_size, 65560)
    tail_off = client.total_size - tail_len
    tail = client.read_range(tail_off, tail_len)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(tail):
        raise OnboardError("ZIP EOCD signature not found")
    cd_size, cd_off = struct.unpack("<II", tail[eocd + 12 : eocd + 20])
    if cd_size == 0xFFFFFFFF or cd_off == 0xFFFFFFFF:
        loc = tail.rfind(b"PK\x06\x07")
        if loc < 0 or loc + 20 > len(tail):
            raise OnboardError("ZIP64 EOCD locator missing")
        eocd64_off = struct.unpack("<Q", tail[loc + 8 : loc + 16])[0]
        e64 = client.read_range(eocd64_off, 56)
        if e64[:4] != b"PK\x06\x06":
            raise OnboardError("Invalid ZIP64 EOCD signature")
        cd_size, cd_off = struct.unpack("<QQ", e64[40:56])
    cd = client.read_range(cd_off, cd_size)
    pos = 0
    while pos + 46 <= len(cd):
        if cd[pos : pos + 4] != b"PK\x01\x02":
            raise OnboardError("Invalid central directory entry")
        comp = struct.unpack("<H", cd[pos + 10 : pos + 12])[0]
        comp_sz, uncomp_sz = struct.unpack("<II", cd[pos + 20 : pos + 28])
        nlen, xlen, clen = struct.unpack("<HHH", cd[pos + 28 : pos + 34])
        lhoff = struct.unpack("<I", cd[pos + 42 : pos + 46])[0]
        name = cd[pos + 46 : pos + 46 + nlen].decode("utf-8", "replace")
        extra = cd[pos + 46 + nlen : pos + 46 + nlen + xlen]
        if comp_sz == 0xFFFFFFFF or uncomp_sz == 0xFFFFFFFF or lhoff == 0xFFFFFFFF:
            ep = 0
            while ep + 4 <= len(extra):
                eid, esz = struct.unpack("<HH", extra[ep : ep + 4])
                ed = extra[ep + 4 : ep + 4 + esz]
                eo = 0
                if eid == 1:
                    if uncomp_sz == 0xFFFFFFFF:
                        uncomp_sz = struct.unpack("<Q", ed[eo : eo + 8])[0]
                        eo += 8
                    if comp_sz == 0xFFFFFFFF:
                        comp_sz = struct.unpack("<Q", ed[eo : eo + 8])[0]
                        eo += 8
                    if lhoff == 0xFFFFFFFF:
                        lhoff = struct.unpack("<Q", ed[eo : eo + 8])[0]
                ep += 4 + esz
        if name == "payload.bin":
            if comp != 0:
                raise OnboardError(f"payload.bin is compressed (method={comp}), expected stored (0)")
            lh = client.read_range(lhoff, 30)
            if lh[:4] != b"PK\x03\x04":
                raise OnboardError("Invalid payload.bin local header")
            ln, lx = struct.unpack("<HH", lh[26:30])
            return lhoff + 30 + ln + lx, uncomp_sz
        pos += 46 + nlen + xlen + clen
    raise OnboardError("payload.bin not found in OTA archive")


def _read_varint(buf: bytes, idx: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        b = buf[idx]
        idx += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, idx
        shift += 7


def _iter_pb_fields(buf: bytes):
    idx = 0
    n = len(buf)
    while idx < n:
        tag, idx = _read_varint(buf, idx)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            val, idx = _read_varint(buf, idx)
        elif wt == 1:
            val, idx = buf[idx : idx + 8], idx + 8
        elif wt == 2:
            size, idx = _read_varint(buf, idx)
            val, idx = buf[idx : idx + size], idx + size
        elif wt == 5:
            val, idx = buf[idx : idx + 4], idx + 4
        else:
            raise OnboardError(f"Unsupported protobuf wire type {wt}")
        yield fn, wt, val


def _parse_extent(buf: bytes) -> tuple[int, int]:
    start_block = 0
    num_blocks = 0
    for fn, _, val in _iter_pb_fields(buf):
        if fn == 1:
            start_block = val
        elif fn == 2:
            num_blocks = val
    return start_block, num_blocks


def _parse_install_op(buf: bytes) -> dict:
    op = {"type": 0, "data_offset": 0, "data_length": 0, "dst_extents": [], "data_sha256": None}
    for fn, _, val in _iter_pb_fields(buf):
        if fn == 1:
            op["type"] = val
        elif fn == 2:
            op["data_offset"] = val
        elif fn == 3:
            op["data_length"] = val
        elif fn == 6:
            op["dst_extents"].append(_parse_extent(val))
        elif fn == 8:
            op["data_sha256"] = val.hex()
    return op


def parse_crau_manifest(client: RangeClient, payload_off: int) -> dict:
    hdr = client.read_range(payload_off, 24)
    magic, version, manifest_size, sig_size = struct.unpack(">4sQQI", hdr)
    if magic != b"CrAU" or version != 2:
        raise OnboardError(f"Unsupported payload header magic={magic!r} version={version}")
    manifest_bytes = client.read_range(payload_off + 24, manifest_size)
    data_base = payload_off + 24 + manifest_size + sig_size

    block_size = 4096
    partitions: dict[str, dict] = {}
    for fn, _, val in _iter_pb_fields(manifest_bytes):
        if fn == 3:
            block_size = val
        elif fn == 13:
            pname = ""
            psize = 0
            phash = ""
            ops = []
            for pfn, _, pval in _iter_pb_fields(val):
                if pfn == 1:
                    pname = pval.decode("utf-8", "replace")
                elif pfn == 7:
                    for sfn, _, sval in _iter_pb_fields(pval):
                        if sfn == 1:
                            psize = sval
                        elif sfn == 2:
                            phash = sval.hex()
                elif pfn == 8:
                    ops.append(_parse_install_op(pval))
            partitions[pname] = {
                "name": pname,
                "size": psize,
                "sha256": phash,
                "operations": ops,
            }
    return {"block_size": block_size, "data_base": data_base, "partitions": partitions}


def _decode_op_bytes(op_type: int, raw: bytes) -> bytes:
    if op_type == OP_REPLACE:
        return raw
    if op_type == OP_REPLACE_BZ:
        return bz2.decompress(raw)
    if op_type == OP_REPLACE_XZ:
        return lzma.decompress(raw)
    if op_type == OP_REPLACE_ZSTD:
        return _decompress_zstd(raw)
    raise OnboardError(f"Unsupported payload InstallOperation type={op_type}")


def extract_partition_image(
    client: RangeClient, manifest: dict, partition_name: str, out_path: Path
) -> Path:
    parts = manifest["partitions"]
    if partition_name not in parts:
        raise OnboardError(f"Partition {partition_name!r} absent in manifest")
    part = parts[partition_name]
    block_size = manifest["block_size"]
    data_base = manifest["data_base"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".img.tmp")
    ops = part["operations"]
    ops_sorted = sorted(ops, key=lambda o: (o["data_offset"], o["data_length"]))

    with tmp_path.open("wb") as fh:
        fh.truncate(part["size"])
        # Coalesce contiguous nonzero payload blobs into ~16MB HTTP Range batches.
        batch: list[dict] = []
        batch_start = 0
        batch_end = 0

        def flush_batch() -> None:
            if not batch:
                return
            blob = client.read_range(data_base + batch_start, batch_end - batch_start)
            for item in batch:
                rel = item["data_offset"] - batch_start
                raw = blob[rel : rel + item["data_length"]]
                if item["data_sha256"] and sha256_bytes(raw) != item["data_sha256"]:
                    raise OnboardError(f"{partition_name}: payload op sha256 mismatch")
                decoded = _decode_op_bytes(item["type"], raw)
                pos = 0
                for start_blk, num_blks in item["dst_extents"]:
                    chunk_len = num_blks * block_size
                    fh.seek(start_blk * block_size)
                    fh.write(decoded[pos : pos + chunk_len])
                    pos += chunk_len
            batch.clear()

        for op in ops_sorted:
            if op["type"] == OP_ZERO:
                continue
            off = op["data_offset"]
            length = op["data_length"]
            if not batch:
                batch_start = off
                batch_end = off + length
                batch.append(op)
            elif off == batch_end and (off + length - batch_start) <= 16 * 1024 * 1024:
                batch_end = off + length
                batch.append(op)
            else:
                flush_batch()
                batch_start = off
                batch_end = off + length
                batch.append(op)
        flush_batch()

    if part["sha256"] and sha256_file(tmp_path) != part["sha256"]:
        raise OnboardError(f"{partition_name}: reconstructed image sha256 mismatch")
    tmp_path.replace(out_path)
    return out_path


def _find_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    repo_tool = Path(__file__).resolve().parents[1] / "local/toolchain/bin" / name
    if repo_tool.is_file():
        return str(repo_tool)
    raise OnboardError(f"Required tool missing: {name}")


def dump_erofs_list(img_path: Path, inner_path: str, env: dict[str, str] | None = None) -> list[str]:
    dump_bin = _find_tool("dump.erofs")
    proc = subprocess.run(
        [dump_bin, "--ls", f"--path={inner_path}", str(img_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    if proc.returncode != 0:
        return []
    entries = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            name = parts[-1]
            if name not in (".", ".."):
                entries.append(name)
    return entries


def dump_erofs_file(
    img_path: Path, inner_path: str, dest: Path, env: dict[str, str] | None = None
) -> bool:
    dump_bin = _find_tool("dump.erofs")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        proc = subprocess.run(
            [dump_bin, f"--path={inner_path}", "--cat", str(img_path)],
            stdout=fh, stderr=subprocess.PIPE, env=env, check=False,
        )
    if proc.returncode != 0 or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return False
    return True


class LazyPartitionServer:
    """Serve on-demand CrAU partition byte ranges to `dump.erofs` via `crau_preload.so`."""

    def __init__(self, client: RangeClient, manifest: dict, partition_name: str):
        parts = manifest["partitions"]
        if partition_name not in parts:
            raise OnboardError(f"Partition {partition_name!r} absent in manifest")
        self.client = client
        self.block_size = manifest["block_size"]
        self.data_base = manifest["data_base"]
        self.part = parts[partition_name]
        self.size = self.part["size"]
        self.ops = self.part["operations"]
        self.starts: list[int] = []
        self.extents: list[tuple[int, int, int, int]] = []
        for idx, op in enumerate(self.ops):
            if op["type"] == OP_ZERO:
                continue
            pos = 0
            for start_blk, num_blks in op["dst_extents"]:
                b_start = start_blk * self.block_size
                b_len = num_blks * self.block_size
                self.extents.append((b_start, b_start + b_len, idx, pos))
                pos += b_len
        self.extents.sort()
        self.starts = [e[0] for e in self.extents]
        self.cache: OrderedDict[int, bytes] = OrderedDict()
        self.lock = threading.Lock()
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sock_path = ""
        self.preload_path = str(Path(__file__).resolve().parents[1] / "local/toolchain/crau_preload.so")

    def _get_op_bytes(self, op_idx: int) -> bytes:
        with self.lock:
            if op_idx in self.cache:
                self.cache.move_to_end(op_idx)
                return self.cache[op_idx]
        op = self.ops[op_idx]
        raw = self.client.read_range(self.data_base + op["data_offset"], op["data_length"])
        if op["data_sha256"] and sha256_bytes(raw) != op["data_sha256"]:
            raise OnboardError(f"op {op_idx} sha256 mismatch")
        decoded = _decode_op_bytes(op["type"], raw)
        with self.lock:
            self.cache[op_idx] = decoded
            if len(self.cache) > 48:
                self.cache.popitem(last=False)
        return decoded

    def read_at(self, offset: int, count: int) -> bytes:
        if offset >= self.size or count <= 0:
            return b""
        end = min(offset + count, self.size)
        out = bytearray()
        cur = offset
        while cur < end:
            idx = bisect.bisect_right(self.starts, cur) - 1
            if idx >= 0 and self.extents[idx][0] <= cur < self.extents[idx][1]:
                b_start, b_end, op_idx, op_off = self.extents[idx]
                take = min(end, b_end) - cur
                op_bytes = self._get_op_bytes(op_idx)
                rel = op_off + (cur - b_start)
                out.extend(op_bytes[rel : rel + take])
                cur += take
            else:
                nxt = self.extents[idx + 1][0] if idx + 1 < len(self.extents) else end
                gap = min(end, nxt) - cur
                out.extend(b"\x00" * gap)
                cur += gap
        return bytes(out)

    def _serve_conn(self, conn: socket.socket) -> None:
        with conn:
            while not self._stop.is_set():
                hdr = b""
                while len(hdr) < 16:
                    chunk = conn.recv(16 - len(hdr))
                    if not chunk:
                        return
                    hdr += chunk
                offset, count = struct.unpack("<QQ", hdr)
                data = self.read_at(offset, count)
                conn.sendall(data)

    def _accept_loop(self) -> None:
        assert self._sock is not None
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._serve_conn, args=(conn,), daemon=True)
            t.start()

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.preload_path
        env["CRAU_SOCK"] = self.sock_path
        env["CRAU_SIZE"] = str(self.size)
        env["CRAU_VIRTUAL_PATH"] = "/virtual.img"
        return env

    def list_dir(self, inner_path: str) -> list[str]:
        return dump_erofs_list(Path("/virtual.img"), inner_path, env=self.env())

    def extract_file(self, inner_path: str, dest: Path) -> bool:
        return dump_erofs_file(Path("/virtual.img"), inner_path, dest, env=self.env())

    def __enter__(self) -> "LazyPartitionServer":
        if not Path(self.preload_path).is_file():
            raise OnboardError(f"Missing {self.preload_path}")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="mt6639-crau-")
        self.sock_path = str(Path(self._tmpdir.name) / "crau.sock")
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.sock_path)
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


def extract_erofs_tree(img_path: Path, out_dir: Path) -> None:
    fsck_bin = _find_tool("fsck.erofs")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [fsck_bin, f"--extract={out_dir}", "--overwrite", str(img_path)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise OnboardError(f"fsck.erofs failed on {img_path}: {proc.stderr}")


def decompress_kernel_from_boot(boot_img: Path) -> bytes:
    data = boot_img.read_bytes()
    if data[:8] != b"ANDROID!":
        raise OnboardError(f"Invalid boot image magic in {boot_img}")
    kernel_size = struct.unpack("<I", data[8:12])[0]
    header_version = struct.unpack("<I", data[40:44])[0]
    if header_version >= 3:
        page_size = 4096
    else:
        page_size = struct.unpack("<I", data[36:40])[0] or 4096
    kernel_off = page_size
    blob = data[kernel_off : kernel_off + kernel_size]
    if blob[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(blob)
    if blob[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18"):
        return _decompress_lz4(blob)
    if blob[:4] == b"\x28\xb5\x2f\xfd":
        return _decompress_zstd(blob)
    if blob[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(blob)
    return blob


def inspect_kernel_image(kernel_bytes: bytes) -> dict:
    m = re.search(rb"Linux version ([0-9]+\.[0-9]+\.[0-9]+[^\s\x00]*)", kernel_bytes)
    if not m:
        raise OnboardError("Could not locate Linux version banner in kernel image")
    release = m.group(1).decode("ascii", "replace")
    ver_match = re.match(r"^(\d+)\.(\d+)\.", release)
    major, minor = (int(ver_match.group(1)), int(ver_match.group(2))) if ver_match else (0, 0)
    # Linux 6.8 introduced STATX_MNT_ID_UNIQUE (0x4000) upstream.
    # For <6.8 kernels, probe kallsyms/symbol strings for backport clues without guessing.
    has_symbol_hint = b"mnt_id_unique" in kernel_bytes or b"STATX_MNT_ID_UNIQUE" in kernel_bytes
    statx_mnt_id_unique_likely = (major, minor) >= (6, 8) or has_symbol_hint
    return {
        "kernel_release": release,
        "kernel_major": major,
        "kernel_minor": minor,
        "statx_mnt_id_unique_supported": statx_mnt_id_unique_likely,
        "statx_evidence": "kernel>=6.8" if (major, minor) >= (6, 8) else (
            "symbol_hint" if has_symbol_hint else "pre_6.8_no_unique_mnt_id_clue"
        ),
    }


def extract_apex_member(apex_path: Path, member_glob: str, out_file: Path) -> bool:
    """Extract a file matching `member_glob` inside a `.apex` or `.capex` archive."""
    with tempfile.TemporaryDirectory(prefix="mt6639-apex-") as td:
        tmp = Path(td)
        work_apex = apex_path
        if apex_path.suffix == ".capex":
            with zipfile.ZipFile(apex_path) as zf:
                if "original_apex" not in zf.namelist():
                    return False
                work_apex = tmp / "original.apex"
                with zf.open("original_apex") as src, work_apex.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        with zipfile.ZipFile(work_apex) as zf:
            if "apex_payload.img" not in zf.namelist():
                return False
            payload_img = tmp / "apex_payload.img"
            with zf.open("apex_payload.img") as src, payload_img.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        magic = payload_img.open("rb").read(1030)
        if len(magic) >= 1028 and magic[1024:1028] == b"\xe2\xe1\xf5\xe0":
            extracted = tmp / "payload"
            extract_erofs_tree(payload_img, extracted)
            matches = sorted(extracted.glob(member_glob))
            if not matches:
                return False
            out_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(matches[0], out_file)
            return True
        # Fallback for ext4 apex_payload.img via debugfs/7z if present
        proc = subprocess.run(["7z", "x", "-y", f"-o{tmp / 'payload'}", str(payload_img)],
                              capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            matches = sorted((tmp / "payload").glob(member_glob))
            if matches:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(matches[0], out_file)
                return True
    return False


def _read_cstr(buf: bytes, off: int) -> str:
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    return buf[off:end].decode("utf-8", "replace")


class Elf64Relocatable:
    """Minimal ELF64 little-endian AArch64 parser for 16-function static checks."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()
        if self.data[:5] != b"\x7fELF\x02" or self.data[5] != 1:
            raise OnboardError(f"{path} is not ELF64 little-endian")
        e_machine = struct.unpack("<H", self.data[18:20])[0]
        e_shoff = struct.unpack("<Q", self.data[40:48])[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack("<HHH", self.data[58:64])
        if e_machine != EM_AARCH64:
            raise OnboardError(f"{path} e_machine={e_machine}, expected AArch64 ({EM_AARCH64})")
        self.sections: list[dict] = []
        for idx in range(e_shnum):
            off = e_shoff + idx * e_shentsize
            sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, _, sh_entsize = struct.unpack(
                "<IIQQQQIIQQ", self.data[off : off + 64]
            )
            self.sections.append({
                "index": idx, "name_off": sh_name, "type": sh_type, "flags": sh_flags,
                "offset": sh_offset, "size": sh_size, "link": sh_link, "info": sh_info,
                "entsize": sh_entsize, "name": "",
            })
        shstr = self.section_bytes(e_shstrndx)
        for sec in self.sections:
            sec["name"] = _read_cstr(shstr, sec["name_off"])
        self.by_name = {s["name"]: s for s in self.sections if s["name"]}

    def section_bytes(self, idx: int) -> bytes:
        s = self.sections[idx]
        return self.data[s["offset"] : s["offset"] + s["size"]]

    def parse_symbols(self) -> tuple[list[dict], dict[str, dict]]:
        symtab = next((s for s in self.sections if s["type"] == SHT_SYMTAB), None)
        if symtab is None:
            raise OnboardError("ELF missing .symtab")
        strtab = self.section_bytes(symtab["link"])
        raw = self.section_bytes(symtab["index"])
        entsize = symtab["entsize"] or 24
        symbols = []
        by_name: dict[str, dict] = {}
        for off in range(0, len(raw), entsize):
            st_name, st_info, _, st_shndx, st_value, st_size = struct.unpack(
                "<IBBHQQ", raw[off : off + 24]
            )
            name = _read_cstr(strtab, st_name)
            sym = {
                "name": name,
                "type": st_info & 0xF,
                "bind": st_info >> 4,
                "shndx": st_shndx,
                "value": st_value,
                "size": st_size,
            }
            symbols.append(sym)
            if name and st_size > 0 and name not in by_name:
                by_name[name] = sym
        return symbols, by_name

    def normalized_function_relas(self, sym: dict, symbols: list[dict]) -> list[tuple]:
        rela_sec = next(
            (s for s in self.sections if s["type"] == SHT_RELA and s["info"] == sym["shndx"]),
            None,
        )
        if rela_sec is None:
            return []
        raw = self.section_bytes(rela_sec["index"])
        rodata_sec = self.by_name.get(".rodata")
        rodata_bytes = self.section_bytes(rodata_sec["index"]) if rodata_sec else b""
        rodata_idx = rodata_sec["index"] if rodata_sec else -1
        start = sym["value"]
        end = start + sym["size"]
        out = []
        for off in range(0, len(raw), 24):
            r_offset, r_info, r_addend = struct.unpack("<QQq", raw[off : off + 24])
            if not (start <= r_offset < end):
                continue
            r_sym = r_info >> 32
            r_type = r_info & 0xFFFFFFFF
            target = symbols[r_sym]
            if target["type"] == STT_SECTION:
                sec_name = self.sections[target["shndx"]]["name"]
                if target["shndx"] == rodata_idx and 0 <= r_addend < len(rodata_bytes):
                    resolved = ("rodata_str", _read_cstr(rodata_bytes, r_addend))
                else:
                    resolved = ("section", sec_name, r_addend)
            else:
                resolved = ("symbol", target["name"], r_addend)
            out.append((r_offset - start, r_type, resolved))
        out.sort()
        return out


def compare_ko_functions(ref_path: Path, cand_path: Path) -> dict:
    ref = Elf64Relocatable(ref_path)
    cand = Elf64Relocatable(cand_path)
    ref_syms, ref_by_name = ref.parse_symbols()
    cand_syms, cand_by_name = cand.parse_symbols()

    results = {}
    all_ok = True
    for fname in TARGET_FUNCTIONS:
        rs = ref_by_name.get(fname)
        cs = cand_by_name.get(fname)
        if not rs or not cs:
            results[fname] = {
                "present_in_ref": bool(rs),
                "present_in_cand": bool(cs),
                "instructions_equal": False,
                "relocations_equal": False,
            }
            all_ok = False
            continue
        rbytes = ref.section_bytes(rs["shndx"])[rs["value"] : rs["value"] + rs["size"]]
        cbytes = cand.section_bytes(cs["shndx"])[cs["value"] : cs["value"] + cs["size"]]
        r_relas = ref.normalized_function_relas(rs, ref_syms)
        c_relas = cand.normalized_function_relas(cs, cand_syms)
        ins_eq = rbytes == cbytes
        rel_eq = r_relas == c_relas
        if not (ins_eq and rel_eq):
            all_ok = False
        results[fname] = {
            "instructions_equal": ins_eq,
            "relocations_equal": rel_eq,
            "ref_size": rs["size"],
            "cand_size": cs["size"],
            "ref_reloc_count": len(r_relas),
            "cand_reloc_count": len(c_relas),
            "bytes_sha256": sha256_bytes(cbytes),
        }
    return {
        "reference_sha256": sha256_file(ref_path),
        "candidate_sha256": sha256_file(cand_path),
        "all_16_functions_match": all_ok,
        "functions": results,
    }


def validate_wifi_cfg(cfg_path: Path) -> dict:
    raw = cfg_path.read_bytes()
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    disallow_1 = sum(1 for ln in lines if ln.strip() == "DisallowBand6G 1")
    disallow_0 = sum(1 for ln in lines if ln.strip() == "DisallowBand6G 0")
    sta_eht = [ln.strip() for ln in lines if ln.strip().startswith("StaEHT ")]
    ap_eht = [ln.strip() for ln in lines if ln.strip().startswith("ApEHT ")]
    ok = disallow_1 == 1 and disallow_0 == 0
    return {
        "sha256": sha256_bytes(raw),
        "size": len(raw),
        "line_count": len(lines),
        "disallow_band6g_1_count": disallow_1,
        "disallow_band6g_0_count": disallow_0,
        "sta_eht": sta_eht,
        "ap_eht": ap_eht,
        "compatible_single_line_flip": ok,
    }


def inspect_settings_apk(apk_path: Path) -> dict:
    with zipfile.ZipFile(apk_path) as zf:
        dex_names = [n for n in zf.namelist() if n.endswith(".dex")]
        dex_blob = b"".join(zf.read(n) for n in dex_names)
        arsc_blob = zf.read("resources.arsc") if "resources.arsc" in zf.namelist() else b""
    classes = {cls: (cls.encode("ascii") in dex_blob) for cls in BADGE_REQUIRED_CLASSES}
    resources = {res: (res.encode("ascii") in arsc_blob) for res in BADGE_REQUIRED_RESOURCES}
    return {
        "sha256": sha256_file(apk_path),
        "classes": classes,
        "resources": resources,
        "badge_compatible": classes["com/android/settings/wifi/MiuiWifiEntryPreference"] and all(resources.values()),
    }


def verify_sample_dir(sample_dir: Path, ref_ko: Path) -> dict:
    device = (sample_dir / "device.txt").read_text(encoding="utf-8").strip() if (sample_dir / "device.txt").is_file() else "unknown"
    kernel = (sample_dir / "uname.txt").read_text(encoding="utf-8").strip() if (sample_dir / "uname.txt").is_file() else "unknown"
    cfg = sample_dir / "wifi.cfg"
    ko = sample_dir / "wlan_drv_gen4m_eap_6639.ko"
    bt_apk = sample_dir / "Bluetooth.apk"
    tether_jar = sample_dir / "framework-tethering.jar"

    report: dict = {"device": device, "kernel_release": kernel}
    if cfg.is_file():
        report["wifi_cfg"] = validate_wifi_cfg(cfg)
    if ko.is_file():
        report["ko"] = compare_ko_functions(ref_ko, ko)
    if bt_apk.is_file():
        report["bluetooth_apk_sha256"] = sha256_file(bt_apk)
    if tether_jar.is_file():
        report["framework_tethering_jar_sha256"] = sha256_file(tether_jar)

    if "wifi_cfg" in report and "ko" in report and device != "unknown" and kernel != "unknown":
        profile = f"{device}-live"
        report["compat_6g_row"] = (
            f"{profile} {device} {kernel} {report['wifi_cfg']['sha256']} {report['ko']['candidate_sha256']}"
        )
    if "bluetooth_apk_sha256" in report and device != "unknown":
        report["compat_bt_row"] = f"{device}-bt {device} {report['bluetooth_apk_sha256']}"
    if "ko" in report and "framework_tethering_jar_sha256" in report and device != "unknown":
        report["compat_hotspot_row"] = (
            f"{device}-hotspot {device} {report['ko']['candidate_sha256']} {report['framework_tethering_jar_sha256']}"
        )
    return report


def cmd_list_partitions(args: argparse.Namespace) -> int:
    client = RangeClient(args.url)
    off, size = locate_payload_in_remote_zip(client)
    manifest = parse_crau_manifest(client, off)
    out = {
        "url": client.active_url,
        "payload_offset": off,
        "payload_size": size,
        "block_size": manifest["block_size"],
        "partitions": {
            name: {
                "size_bytes": p["size"],
                "size_mib": round(p["size"] / 1048576, 2),
                "download_bytes": sum(op["data_length"] for op in p["operations"]),
                "download_mib": round(sum(op["data_length"] for op in p["operations"]) / 1048576, 2),
                "op_types": sorted({op["type"] for op in p["operations"]}),
            }
            for name, p in sorted(manifest["partitions"].items())
        },
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_extract_rom(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = RangeClient(args.url)
    off, _ = locate_payload_in_remote_zip(client)
    manifest = parse_crau_manifest(client, off)

    wanted = [p.strip() for p in args.partitions.split(",") if p.strip()]
    summary: dict = {"url": client.active_url, "partitions": {}}
    for pname in wanted:
        if pname not in manifest["partitions"]:
            continue
        img_path = out_dir / "images" / f"{pname}.img"
        extract_partition_image(client, manifest, pname, img_path)
        pinfo: dict = {"image": str(img_path), "size": img_path.stat().st_size}
        if pname == "boot":
            kbytes = decompress_kernel_from_boot(img_path)
            pinfo["kernel"] = inspect_kernel_image(kbytes)
        else:
            part_out = out_dir / "fs" / pname
            extract_erofs_tree(img_path, part_out)
            pinfo["extracted"] = str(part_out)
        summary["partitions"][pname] = pinfo
    if args.keep_images is False:
        shutil.rmtree(out_dir / "images", ignore_errors=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_verify_ko(args: argparse.Namespace) -> int:
    report = compare_ko_functions(Path(args.reference), Path(args.candidate))
    print(json.dumps(report, indent=2))
    return 0 if report["all_16_functions_match"] else 2


def cmd_sample_dir(args: argparse.Namespace) -> int:
    report = verify_sample_dir(Path(args.sample_dir), Path(args.reference))
    print(json.dumps(report, indent=2))
    return 0


def _parse_props(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    if not path.is_file():
        return props
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        props[k.strip()] = v.strip()
    return props


def cmd_inspect_rom_targets(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ext = out_dir / "extracted"
    ext.mkdir(parents=True, exist_ok=True)
    client = RangeClient(args.url)
    off, _ = locate_payload_in_remote_zip(client)
    manifest = parse_crau_manifest(client, off)

    report: dict = {"url": client.active_url, "out_dir": str(out_dir)}
    summary_file = out_dir / "summary.json"
    if summary_file.is_file():
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        report["boot"] = summary.get("partitions", {}).get("boot", {}).get("kernel")

    ko_candidates = list((out_dir / "fs/vendor_dlkm").glob("lib/modules/wlan_drv_gen4m*.ko"))
    if ko_candidates and args.reference:
        report["ko"] = compare_ko_functions(Path(args.reference), ko_candidates[0])
        report["ko_path_in_rom"] = str(ko_candidates[0].relative_to(out_dir / "fs/vendor_dlkm"))

    # 1. odm -> wifi.cfg + build.prop
    with LazyPartitionServer(client, manifest, "odm") as srv:
        cfg_dest = ext / "odm/firmware/wifi.cfg"
        if srv.extract_file("/firmware/wifi.cfg", cfg_dest):
            report["wifi_cfg"] = validate_wifi_cfg(cfg_dest)
        srv.extract_file("/etc/build.prop", ext / "odm/etc/build.prop")

    # 2. vendor -> build.prop (check Bluetooth LE Audio offload props)
    with LazyPartitionServer(client, manifest, "vendor") as srv:
        vprop = ext / "vendor/build.prop"
        if srv.extract_file("/build.prop", vprop) or srv.extract_file("/etc/build.prop", vprop):
            props = _parse_props(vprop)
            report["vendor_bt_props"] = {
                k: props.get(k)
                for k in (
                    "ro.bluetooth.leaudio_offload.supported",
                    "persist.bluetooth.leaudio_offload.disabled",
                    "ro.product.vendor.device",
                )
            }

    # 3. system -> com.android.bt* & com.android.tethering* APEX
    with LazyPartitionServer(client, manifest, "system") as srv:
        apex_dir = "/system/apex"
        entries = srv.list_dir(apex_dir)
        if not entries:
            apex_dir = "/apex"
            entries = srv.list_dir(apex_dir)
        bt_apex = next((e for e in sorted(entries) if e.startswith("com.android.bt") and (e.endswith(".apex") or e.endswith(".capex"))), None)
        tether_apex = next((e for e in sorted(entries) if e.startswith("com.android.tethering") and (e.endswith(".apex") or e.endswith(".capex"))), None)
        if bt_apex:
            local_bt = ext / "apex" / bt_apex
            if srv.extract_file(f"{apex_dir}/{bt_apex}", local_bt):
                bt_apk = ext / "bt/Bluetooth.apk"
                if extract_apex_member(local_bt, "app/**/*.apk", bt_apk):
                    report["bluetooth_apk"] = {"apex": bt_apex, "sha256": sha256_file(bt_apk)}
        if tether_apex:
            local_tether = ext / "apex" / tether_apex
            if srv.extract_file(f"{apex_dir}/{tether_apex}", local_tether):
                tjar = ext / "tethering/framework-tethering.jar"
                if extract_apex_member(local_tether, "javalib/framework-tethering.jar", tjar):
                    report["framework_tethering_jar"] = {"apex": tether_apex, "sha256": sha256_file(tjar)}

    # 4. system_ext / product -> Settings.apk
    settings_apk = ext / "settings/Settings.apk"
    for part_name, paths in (
        ("system_ext", ("/priv-app/Settings/Settings.apk", "/priv-app/MiuiSettings/MiuiSettings.apk")),
        ("product", ("/priv-app/Settings/Settings.apk", "/priv-app/MiuiSettings/MiuiSettings.apk")),
    ):
        if settings_apk.is_file():
            break
        if part_name not in manifest["partitions"]:
            continue
        with LazyPartitionServer(client, manifest, part_name) as srv:
            for p in paths:
                if srv.extract_file(p, settings_apk):
                    report["settings_apk"] = {"partition": part_name, "path": p, **inspect_settings_apk(settings_apk)}
                    break

    (ext / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-partitions")
    p_list.add_argument("--url", required=True)
    p_list.set_defaults(func=cmd_list_partitions)

    p_ext = sub.add_parser("extract-rom")
    p_ext.add_argument("--url", required=True)
    p_ext.add_argument("--partitions", default="boot,odm,vendor_dlkm")
    p_ext.add_argument("--out-dir", required=True)
    p_ext.add_argument("--keep-images", action="store_true")
    p_ext.set_defaults(func=cmd_extract_rom)

    p_ins = sub.add_parser("inspect-rom-targets")
    p_ins.add_argument("--url", required=True)
    p_ins.add_argument("--out-dir", required=True)
    p_ins.add_argument("--reference")
    p_ins.set_defaults(func=cmd_inspect_rom_targets)

    p_ko = sub.add_parser("verify-ko")
    p_ko.add_argument("--reference", required=True)
    p_ko.add_argument("--candidate", required=True)
    p_ko.set_defaults(func=cmd_verify_ko)

    p_sd = sub.add_parser("sample-dir")
    p_sd.add_argument("--sample-dir", required=True)
    p_sd.add_argument("--reference", required=True)
    p_sd.set_defaults(func=cmd_sample_dir)

    args = parser.parse_args()
    try:
        return args.func(args)
    except OnboardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
