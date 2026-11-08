#!/usr/bin/env python3
"""Run a bounded, repository-local host test manifest with per-test logs."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MAX_MANIFEST = 512 * 1024
ALLOWED_INTERPRETERS = {"/usr/bin/python3", "sh"}
ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class ManifestError(Exception):
    pass


def validate(manifest_path: Path, repo: Path):
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST:
        raise ManifestError("manifest too large")
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("schema") != "mt6639-host-test-manifest-v1":
        raise ManifestError("unsupported manifest schema")
    tests = manifest.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ManifestError("tests missing")
    seen = set()
    validated = []
    for item in tests:
        if not isinstance(item, dict):
            raise ManifestError("test item invalid")
        test_id = item.get("id")
        if not isinstance(test_id, str) or not ID_RE.fullmatch(test_id) or test_id in seen:
            raise ManifestError("test id invalid or duplicate")
        seen.add(test_id)
        command = item.get("command")
        if not isinstance(command, list) or not command or not all(
                isinstance(value, str) and value for value in command):
            raise ManifestError(f"{test_id}: command invalid")
        if command[0] not in ALLOWED_INTERPRETERS:
            raise ManifestError(f"{test_id}: interpreter not allowed")
        if len(command) < 2:
            raise ManifestError(f"{test_id}: script missing")
        script = (repo / command[1]).resolve()
        try:
            script.relative_to(repo)
        except ValueError as error:
            raise ManifestError(f"{test_id}: script escapes repository") from error
        if not script.is_file() or script.is_symlink():
            raise ManifestError(f"{test_id}: script missing or symlink")
        timeout = item.get("timeout_s")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not (1 <= timeout <= 300):
            raise ManifestError(f"{test_id}: timeout invalid")
        validated.append({
            "id": test_id,
            "command": [command[0], str(script)] + command[2:],
            "timeout_s": timeout,
        })
    return validated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    out = Path(args.out).resolve()
    if out.exists():
        print("FAIL output path must be new", file=sys.stderr)
        return 1
    try:
        tests = validate(Path(args.manifest).resolve(), repo)
    except (ManifestError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    out.mkdir(mode=0o700)
    results = []
    failed = 0
    started = dt.datetime.now(dt.timezone.utc)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MT6639_UI_BUILD_DIR"] = str(out / "ui-build")
    for item in tests:
        begin = time.monotonic()
        try:
            done = subprocess.run(
                item["command"], cwd=repo, env=env, capture_output=True,
                text=True, errors="replace", timeout=item["timeout_s"],
            )
            rc = done.returncode
            stdout, stderr = done.stdout, done.stderr
        except subprocess.TimeoutExpired as error:
            rc = None
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = "timeout"
        elapsed = round(time.monotonic() - begin, 3)
        log = stdout + ("\n" if stdout and not stdout.endswith("\n") else "")
        if stderr:
            log += "[stderr]\n" + stderr + ("\n" if not stderr.endswith("\n") else "")
        (out / f"{item['id']}.log").write_text(log, encoding="utf-8")
        passed = rc == 0
        failed += 0 if passed else 1
        results.append({
            "id": item["id"],
            "rc": rc,
            "passed": passed,
            "elapsed_s": elapsed,
            "timeout_s": item["timeout_s"],
        })
        print(("PASS" if passed else "FAIL") + f" {item['id']} rc={rc} elapsed={elapsed:.3f}s")
    summary = {
        "schema": "mt6639-host-test-result-v1",
        "started_at": started.isoformat(),
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tests": len(results),
        "passed": len(results) - failed,
        "failed": failed,
        "results": results,
    }
    fd = os.open(out / "summary.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"RESULT passed={summary['passed']} failed={summary['failed']} tests={summary['tests']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
