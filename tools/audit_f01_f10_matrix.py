#!/usr/bin/env python3
"""Validate the F01-F10 evidence matrix without running device tests."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STATUSES = {"not_started", "partial", "verified", "blocked"}
EXPECTED_IDS = [f"F{value:02d}" for value in range(1, 11)]
MAX_INPUT = 1024 * 1024


class MatrixError(Exception):
    pass


def validate(path: Path, root: Path):
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT:
        raise MatrixError("matrix too large")
    value = json.loads(raw.decode("utf-8"))
    if value.get("schema") != "mt6639-f01-f10-matrix-v1":
        raise MatrixError("matrix schema unsupported")
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 10:
        raise MatrixError("matrix must contain exactly F01-F10")
    ids = [item.get("id") if isinstance(item, dict) else None for item in items]
    if ids != EXPECTED_IDS:
        raise MatrixError("matrix IDs/order must be F01-F10")
    result = []
    for item in items:
        for key in ("title", "status", "required_evidence", "existing_evidence", "blockers"):
            if key not in item:
                raise MatrixError(f"{item['id']}: missing {key}")
        if item["status"] not in STATUSES:
            raise MatrixError(f"{item['id']}: invalid status")
        for key in ("required_evidence", "existing_evidence", "blockers"):
            if not isinstance(item[key], list) or not all(isinstance(v, str) and v for v in item[key]):
                raise MatrixError(f"{item['id']}: {key} invalid")
        for relative in item["existing_evidence"]:
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise MatrixError(f"{item['id']}: unsafe evidence path")
            evidence = root / relative
            if not evidence.is_file() or evidence.is_symlink():
                raise MatrixError(f"{item['id']}: evidence missing {relative}")
        if item["status"] == "verified" and (item["blockers"] or not item["required_evidence"]):
            raise MatrixError(f"{item['id']}: verified item cannot retain blockers")
        result.append({
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "evidence_count": len(item["existing_evidence"]),
            "blocker_count": len(item["blockers"]),
        })
    return {
        "schema": "mt6639-f01-f10-audit-v1",
        "items": result,
        "verified": sum(item["status"] == "verified" for item in items),
        "partial": sum(item["status"] == "partial" for item in items),
        "not_started": sum(item["status"] == "not_started" for item in items),
        "blocked": sum(item["status"] == "blocked" for item in items),
        "acceptance_ready": all(item["status"] == "verified" for item in items),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate(Path(args.matrix), Path(args.root).resolve())
    except (MatrixError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
