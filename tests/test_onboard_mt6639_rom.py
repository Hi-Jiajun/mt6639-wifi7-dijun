"""Offline unit tests for tools/onboard_mt6639_rom.py."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import onboard_mt6639_rom as ob  # noqa: E402


def _varint(val: int) -> bytes:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_bytes(fn: int, payload: bytes) -> bytes:
    return _varint((fn << 3) | 2) + _varint(len(payload)) + payload


def _pb_varint(fn: int, val: int) -> bytes:
    return _varint((fn << 3) | 0) + _varint(val)


def _minimal_elf64_ko(func_bytes: bytes, rodata_bytes: bytes, relas: list[tuple[int, int, int, int]]) -> bytes:
    """Build a minimal ELF64 AArch64 relocatable object containing TARGET_FUNCTIONS."""
    shstr = b"\x00.text\x00.rodata\x00.rela.text\x00.symtab\x00.strtab\x00.shstrtab\x00"
    strtab = bytearray(b"\x00")
    symtab = bytearray(24)  # STN_UNDEF
    # Section symbols for .text (1) and .rodata (2)
    symtab.extend(struct.pack("<IBBHQQ", 0, ob.STT_SECTION, 0, 1, 0, 0))
    symtab.extend(struct.pack("<IBBHQQ", 0, ob.STT_SECTION, 0, 2, 0, 0))

    text = bytearray()
    for fname in ob.TARGET_FUNCTIONS:
        name_off = len(strtab)
        strtab.extend(fname.encode("ascii") + b"\x00")
        val = len(text)
        text.extend(func_bytes)
        symtab.extend(struct.pack("<IBBHQQ", name_off, 2, 0, 1, val, len(func_bytes)))

    rela_bytes = bytearray()
    for r_offset, r_sym, r_type, r_addend in relas:
        r_info = (r_sym << 32) | r_type
        rela_bytes.extend(struct.pack("<QQq", r_offset, r_info, r_addend))

    # Layout after 64-byte ELF header
    off = 64
    sections = []
    for name, stype, flags, data, link, info, entsize in (
        (".text", 1, 6, bytes(text), 0, 0, 0),
        (".rodata", 1, 2, rodata_bytes, 0, 0, 0),
        (".rela.text", ob.SHT_RELA, 0, bytes(rela_bytes), 4, 1, 24),
        (".symtab", ob.SHT_SYMTAB, 0, bytes(symtab), 5, 3, 24),
        (".strtab", ob.SHT_STRTAB, 0, bytes(strtab), 0, 0, 0),
        (".shstrtab", ob.SHT_STRTAB, 0, shstr, 0, 0, 0),
    ):
        name_off = shstr.index(name.encode("ascii") + b"\x00")
        sections.append((name_off, stype, flags, off, len(data), link, info, entsize, data))
        off += len(data)

    shoff = off
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\x00" * 9,
        1,  # ET_REL
        ob.EM_AARCH64,
        1,
        0,
        0,
        shoff,
        0,
        64,
        0,
        0,
        64,
        len(sections) + 1,
        6,  # .shstrtab index
    )
    blob = bytearray(ehdr)
    for _, _, _, _, _, _, _, _, data in sections:
        blob.extend(data)
    blob.extend(b"\x00" * 64)  # SHT_NULL
    for name_off, stype, flags, soff, ssz, link, info, entsize, _ in sections:
        blob.extend(struct.pack("<IIQQQQIIQQ", name_off, stype, flags, 0, soff, ssz, link, info, 8, entsize))
    return bytes(blob)


class OnboardRomTests(unittest.TestCase):
    def test_validate_wifi_cfg(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wifi.cfg"
            p.write_text("StaEHT 1\nDisallowBand6G 1\nApEHT 0\n", encoding="utf-8")
            res = ob.validate_wifi_cfg(p)
            self.assertTrue(res["compatible_single_line_flip"])
            self.assertEqual(res["disallow_band6g_1_count"], 1)

            p.write_text("StaEHT 1\nDisallowBand6G 1\nDisallowBand6G 1\n", encoding="utf-8")
            self.assertFalse(ob.validate_wifi_cfg(p)["compatible_single_line_flip"])

            p.write_text("StaEHT 1\nDisallowBand6G 0\n", encoding="utf-8")
            self.assertFalse(ob.validate_wifi_cfg(p)["compatible_single_line_flip"])

    def test_inspect_kernel_image_statx_gate(self):
        k_618 = b"junk\x00Linux version 6.18.21-android17-5-g284297031c53-abogki553682993-4k (gcc)\x00"
        r1 = ob.inspect_kernel_image(k_618)
        self.assertTrue(r1["statx_mnt_id_unique_supported"])
        self.assertEqual(r1["kernel_major"], 6)
        self.assertEqual(r1["kernel_minor"], 18)

        k_66 = b"junk\x00Linux version 6.6.118-android15-8-gc44b714366cc-abogki519650608-4k (gcc)\x00"
        r2 = ob.inspect_kernel_image(k_66)
        self.assertFalse(r2["statx_mnt_id_unique_supported"])
        self.assertEqual((r2["kernel_major"], r2["kernel_minor"]), (6, 6))

    def test_compare_ko_functions_normalizes_rodata_strings_across_shifted_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            p1 = Path(td) / "ref.ko"
            p2 = Path(td) / "cand.ko"
            p3 = Path(td) / "bad.ko"
            # Sym 2 is .rodata section; r_offset=4 falls inside first function (wlanCfgInit)
            p1.write_bytes(_minimal_elf64_ko(b"\x01\x02\x03\x04\x05\x06\x07\x08", b"DisallowBand6G\x00", [(4, 2, 275, 0)]))
            p2.write_bytes(_minimal_elf64_ko(b"\x01\x02\x03\x04\x05\x06\x07\x08", b"PADDING\x00DisallowBand6G\x00", [(4, 2, 275, 8)]))
            p3.write_bytes(_minimal_elf64_ko(b"\x01\x02\x03\x04\x05\x06\x07\x09", b"PADDING\x00DisallowBand6G\x00", [(4, 2, 275, 8)]))

            match_res = ob.compare_ko_functions(p1, p2)
            self.assertTrue(match_res["all_16_functions_match"])
            self.assertNotEqual(match_res["reference_sha256"], match_res["candidate_sha256"])

            bad_res = ob.compare_ko_functions(p1, p3)
            self.assertFalse(bad_res["all_16_functions_match"])

    def test_parse_crau_manifest_and_verify_sample_dir(self):
        ext = _pb_varint(1, 10) + _pb_varint(2, 2)
        op = _pb_varint(1, ob.OP_REPLACE_XZ) + _pb_varint(2, 128) + _pb_varint(3, 64) + _pb_bytes(6, ext)
        pinfo = _pb_varint(1, 8192) + _pb_bytes(2, b"\xab" * 32)
        part = _pb_bytes(1, b"odm") + _pb_bytes(7, pinfo) + _pb_bytes(8, op)
        manifest_bytes = _pb_varint(3, 4096) + _pb_bytes(13, part)
        header = struct.pack(">4sQQI", b"CrAU", 2, len(manifest_bytes), 0)
        blob = header + manifest_bytes

        class FakeClient:
            def read_range(self, start: int, length: int) -> bytes:
                return blob[start : start + length]

        parsed = ob.parse_crau_manifest(FakeClient(), 0)
        self.assertEqual(parsed["block_size"], 4096)
        self.assertIn("odm", parsed["partitions"])
        self.assertEqual(parsed["partitions"]["odm"]["operations"][0]["type"], ob.OP_REPLACE_XZ)

        with tempfile.TemporaryDirectory() as td:
            sd = Path(td)
            (sd / "device.txt").write_text("violin\n", encoding="utf-8")
            (sd / "uname.txt").write_text("6.18.21-android17-5-test\n", encoding="utf-8")
            (sd / "wifi.cfg").write_text("DisallowBand6G 1\nStaEHT 1\n", encoding="utf-8")
            ko = sd / "wlan_drv_gen4m_eap_6639.ko"
            ko.write_bytes(_minimal_elf64_ko(b"\x01\x02\x03\x04", b"ok\x00", []))
            (sd / "Bluetooth.apk").write_bytes(b"bt")
            (sd / "framework-tethering.jar").write_bytes(b"tether")
            report = ob.verify_sample_dir(sd, ko)
            self.assertIn("violin-live violin 6.18.21-android17-5-test", report["compat_6g_row"])
            self.assertIn(hashlib.sha256(b"bt").hexdigest(), report["compat_bt_row"])
            self.assertIn(hashlib.sha256(b"tether").hexdigest(), report["compat_hotspot_row"])

    def test_inspect_settings_apk_checks_classes_and_resources(self):
        with tempfile.TemporaryDirectory() as td:
            apk = Path(td) / "Settings.apk"
            with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("classes.dex", b"\x00".join(c.encode("ascii") for c in ob.BADGE_REQUIRED_CLASSES))
                zf.writestr("resources.arsc", b"\x00".join(r.encode("ascii") for r in ob.BADGE_REQUIRED_RESOURCES))
            info = ob.inspect_settings_apk(apk)
            self.assertTrue(info["badge_compatible"])


if __name__ == "__main__":
    unittest.main()
