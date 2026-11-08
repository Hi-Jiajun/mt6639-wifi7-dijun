"""Availability gates for the public mirror.

Release binaries live in GitHub Releases instead of the tree, and the Android
toolchain is only needed by a few APK checks.  These helpers let the affected
cases skip with an explicit reason instead of failing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RELEASE = REPO / "release"
ARTIFACTS = RELEASE / "artifacts"
SUMS = RELEASE / "SHA256SUMS"
CANDIDATE = RELEASE / "mt6639-candidate-20261026.json"
MATRIX = RELEASE / "f01-f10-matrix-20261026.json"

AAPT2 = shutil.which("aapt2")
JAVAC = shutil.which("javac")


def _android_jar() -> Path | None:
    env = os.environ.get("ANDROID_JAR")
    if env and Path(env).is_file():
        return Path(env)
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if home:
        for path in sorted((Path(home) / "platforms").glob("android-*/android.jar")):
            return path
    return None


ANDROID_JAR = _android_jar()


def asset(name: str) -> Path:
    return ARTIFACTS / name


def assets_present(*names: str) -> bool:
    return bool(names) and all((ARTIFACTS / name).is_file() for name in names)


def listed_assets() -> list[str]:
    if not SUMS.is_file():
        return []
    return [line.split("  ", 1)[1] for line in SUMS.read_text(encoding="ascii").splitlines()
            if "  " in line]


def all_release_assets() -> bool:
    names = listed_assets()
    return bool(names) and all((RELEASE / name).is_file() for name in names)


def candidate_ready() -> bool:
    return CANDIDATE.is_file() and assets_present("mt6639-6g-v0.2.16.zip")


SKIP_RELEASE_ASSETS = "release binaries are not in the tree; download them from Releases"
