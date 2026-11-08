#!/usr/bin/env python3
"""Build the bare-command TWT bridge probe; never deploy or load it."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--protocol-once", action="store_true", help="fixed Request flow0 plus one DEL; default remains bare-only")
    parser.add_argument("--observe", action="store_true", help="bounded read-only host parameters and FW submit/return records")
    parser.add_argument("--stock", type=Path, help="exact stock WLAN ELF, required with --observe")
    args = parser.parse_args()
    if args.observe:
        if not args.protocol_once or not args.stock:
            parser.error("--observe requires --protocol-once and --stock")
        data = args.stock.read_bytes()
        if hashlib.sha256(data).hexdigest() != "54619c2e9521fc084edd4ec93282715ac10e716ad4ae0a27ec5588fb64e618ea":
            parser.error("stock SHA256 mismatch")
        shoff = struct.unpack_from('<Q', data, 40)[0]
        size, count, names_index = struct.unpack_from('<HHH', data, 58)
        sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff+i*size) for i in range(count)]
        names = sections[names_index]
        strings = data[names[4]:names[4]+names[5]]
        text = next(s for s in sections if strings[s[0]:].split(b'\0', 1)[0] == b'.text')
        for address, expected in ((0x218898, 0xb4000980), (0x218984, 0x94000000), (0x218988, 0x2a0003f3)):
            if struct.unpack_from('<I', data, text[4]+address-text[3])[0] != expected:
                parser.error("observation opcode mismatch at " + hex(address))
    if args.out.exists():
        parser.error("output already exists; choose a fresh artifact path")
    if not args.out.parent.is_dir():
        parser.error("output parent must already exist")
    clang = (os.environ.get("MT6639_CLANG") or shutil.which("clang")
             or "clang")
    flags = ["--target=aarch64-linux-gnu", "-std=c11", "-O2", "-ffreestanding",
             "-fsanitize=kcfi", "-fsanitize-cfi-icall-experimental-normalize-integers",
             "-mgeneral-regs-only", "-mbranch-protection=bti", "-mno-outline-atomics",
             "-fno-pic", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
             "-fno-addrsig", "-fno-stack-protector", "-Wall", "-Wextra", "-Werror"]
    if args.protocol_once:
        flags.append("-DMT_TWT_PROTOCOL=1")
    if args.observe:
        flags.append("-DMT_TWT_OBSERVE=1")
    subprocess.run([clang, *flags, "-c", str(ROOT / "native/twt-bridge/probe.c"),
                    "-o", str(args.out)], check=True)
    mode = "fixed-setup=1 one-del=1" if args.protocol_once else "bare-only=1 one-shot=1 no_setup=1"
    if args.observe:
        mode += " observe=host,submit,return"
    print(f"BUILT {args.out}; {mode}; no deployment")

if __name__ == "__main__":
    main()
