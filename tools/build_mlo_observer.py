#!/usr/bin/env python3
"""Compile the read-only observer. Never patch KCFI, deploy, or load it."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reject-opcode", action="store_true", help="build an intentional init-rejection artifact for lifecycle testing")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output already exists; choose a fresh artifact path")
    if not args.out.parent.is_dir():
        parser.error("output parent must already exist")
    clang = (os.environ.get("MT6639_CLANG") or shutil.which("clang")
             or "clang")
    flags = ["--target=aarch64-linux-gnu", "-std=c11", "-O2", "-fsanitize=kcfi",
             "-fsanitize-cfi-icall-experimental-normalize-integers", "-mgeneral-regs-only",
             "-mbranch-protection=bti", "-mno-outline-atomics", "-fno-pic",
             "-fno-asynchronous-unwind-tables", "-fno-unwind-tables", "-fno-addrsig",
             "-Wall", "-Wextra", "-Werror"]
    if args.reject_opcode:
        flags.append("-DMT_EXPECTED_OPCODE=0")
    command = [clang, *flags, "-c", str(ROOT / "native/mlo-gate/observer.c"), "-o", str(args.out)]
    subprocess.run(command, check=True)
    print(f"BUILT {args.out}; mode={'reject-opcode' if args.reject_opcode else 'observe-only'}; no deployment")

if __name__ == "__main__":
    main()
