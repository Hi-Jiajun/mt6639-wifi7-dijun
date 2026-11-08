#!/usr/bin/env python3
"""Verify a read-only MT6639 release-candidate manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

MAX_MANIFEST = 1024 * 1024
MAX_ARTIFACT = 128 * 1024 * 1024


class AuditError(Exception):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def module_prop(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        text = archive.read("module.prop").decode("utf-8")
    except KeyError as error:
        raise AuditError("module.prop missing") from error
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in result:
            raise AuditError("module.prop malformed")
        result[key] = value
    return result


def audit_kernelsu(root: Path, artifact: dict, path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = set(artifact["required_entries"]) - names
        if missing:
            raise AuditError("missing entries: " + ", ".join(sorted(missing)))
        values = module_prop(archive)
    if values.get("id") != artifact["module_id"]:
        raise AuditError("module id mismatch")
    if values.get("version") != artifact["version"]:
        raise AuditError("module version mismatch")
    if values.get("versionCode") != str(artifact["version_code"]):
        raise AuditError("module versionCode mismatch")


def audit_apk(root: Path, artifact: dict, path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        missing = set(artifact["required_entries"]) - set(archive.namelist())
        if missing:
            raise AuditError("missing entries: " + ", ".join(sorted(missing)))
    aapt = subprocess.run(
        ["aapt2", "dump", "badging", str(path)],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if aapt.returncode != 0:
        raise AuditError("aapt2 badging failed")
    package = re.search(r"package: name='([^']+)'", aapt.stdout)
    version_code = re.search(r"versionCode='([^']+)'", aapt.stdout)
    version_name = re.search(r"versionName='([^']+)'", aapt.stdout)
    if not package or package.group(1) != artifact["package"]:
        raise AuditError("APK package mismatch")
    if not version_code or version_code.group(1) != str(artifact["version_code"]):
        raise AuditError("APK versionCode mismatch")
    if not version_name or version_name.group(1) != artifact["version"]:
        raise AuditError("APK versionName mismatch")


def audit(manifest_path: Path, root_override: Path | None = None) -> dict:
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST:
        raise AuditError("manifest too large")
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("schema") != "mt6639-release-candidate-v1":
        raise AuditError("unsupported schema")
    root = root_override.resolve() if root_override else manifest_path.resolve().parents[1]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise AuditError("artifacts missing")
    seen: set[str] = set()
    verified = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise AuditError("artifact shape invalid")
        artifact_id = artifact.get("id")
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in seen:
            raise AuditError("artifact id duplicate or invalid")
        seen.add(artifact_id)
        relative = artifact.get("path")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise AuditError(f"{artifact_id}: unsafe path")
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise AuditError(f"{artifact_id}: file missing")
        if path.stat().st_size > MAX_ARTIFACT:
            raise AuditError(f"{artifact_id}: oversized")
        actual = digest(path)
        if actual != artifact.get("sha256"):
            raise AuditError(f"{artifact_id}: sha256 mismatch")
        kind = artifact.get("kind")
        if kind == "kernelsu_module":
            audit_kernelsu(root, artifact, path)
        elif kind == "lsposed_apk":
            audit_apk(root, artifact, path)
        else:
            raise AuditError(f"{artifact_id}: unsupported kind")
        verified.append({"id": artifact_id, "sha256": actual, "status": artifact.get("status")})
    # The user-facing checksum file and all shipped files must describe the
    # same set as the manifest, including retained historical candidates.
    checksum_entries = {}
    for line in (root / "release/SHA256SUMS").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ((?:artifacts|rollback)/[A-Za-z0-9._-]+)", line)
        if not match or match.group(2) in checksum_entries:
            raise AuditError("release checksum entry malformed or duplicate")
        checksum_entries[match.group(2)] = match.group(1)
    expected_entries = {str(Path(a["path"]).relative_to("release")): a["sha256"] for a in artifacts}
    shipped = {
        str(p.relative_to(root / "release"))
        for directory in ("artifacts", "rollback")
        for p in (root / "release" / directory).iterdir()
        if p.is_file() or p.is_symlink()
    }
    if checksum_entries != expected_entries or set(checksum_entries) != shipped:
        raise AuditError("release checksums, manifest and shipped asset set differ")
    verification_manifests = []
    for reference in manifest.get("verification_manifests", []):
        if not isinstance(reference, dict):
            raise AuditError("verification manifest shape invalid")
        relative = reference.get("path")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise AuditError("verification manifest unsafe path")
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise AuditError(f"verification manifest missing: {relative}")
        actual = digest(path)
        if actual != reference.get("sha256"):
            raise AuditError(f"verification manifest sha256 mismatch: {relative}")
        verification_manifests.append({"path": relative, "sha256": actual})
    return {
        "schema": "mt6639-release-audit-v1",
        "release_id": manifest.get("release_id"),
        "assets_verified": len(verified),
        "release_ready": manifest.get("release_ready") is True and not manifest.get("blockers"),
        "artifacts": verified,
        "verification_manifests": verification_manifests,
        "blockers": manifest.get("blockers", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", help="repository root; defaults to the manifest parent's parent")
    args = parser.parse_args(argv)
    try:
        result = audit(Path(args.manifest), Path(args.root) if args.root else None)
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
