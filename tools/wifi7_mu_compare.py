#!/usr/bin/env python3
"""Compare two offline AP snapshots for conservative Wi-Fi MU evidence.

This tool reads only files named by a JSON manifest. It never connects to a
phone/AP, generates traffic, or changes wireless configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
PHY_RE = re.compile(r"^\s*Phy\s+([^,\s]+),\s*Phy\s+band\s+(\d+)\s*$", re.I)
COUNTER_LINE_RE = re.compile(r"^\s*Tx\s+multi[ -]user\s+(.+?)\s*:\s*(.*?)\s*$", re.I)
STATION_RE = re.compile(r"^Station\s+([0-9a-fA-F:]{17})(?:\s+\(on\s+([^)]*)\))?\s*$")
KNOWN_COUNTERS = {
    "mpdu counts": "mu_mpdu",
    "successful mpdu counts": "mu_successful_mpdu",
    "beamforming counts": "mu_beamforming",
}


class InputError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def token(value: str | None) -> str | None:
    if value is None:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def parse_station_dump(text: str, logical_name: str) -> dict[str, dict[str, object]]:
    if text.count("sample_epoch=") > 1:
        raise InputError("MULTIPLE_SNAPSHOTS", f"{logical_name}: multiple sample_epoch blocks")
    stations: dict[str, dict[str, object]] = {}
    current_mac: str | None = None
    current: dict[str, object] | None = None
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        header = STATION_RE.match(line)
        if re.match(r"^\s*Station\b", line, re.I) and not header:
            raise InputError("INVALID_STATION_HEADER", f"{logical_name}: malformed station header at line {line_no}")
        if header:
            if current_mac is not None:
                stations[current_mac] = current or {}
            current_mac = header.group(1).lower()
            if not MAC_RE.fullmatch(current_mac) or current_mac in stations:
                raise InputError("DUPLICATE_OR_INVALID_STATION", f"{logical_name}: invalid/duplicate station at line {line_no}")
            current = {"interface": header.group(2), "line": line_no}
            seen = set()
            continue
        if current is None:
            continue
        field_match = re.match(r"^\s*(tx bytes|rx bytes|tx duration|rx duration)\s*:(.*)$", line, re.I)
        if not field_match:
            continue
        name = field_match.group(1).lower().replace(" ", "_")
        value = field_match.group(2).strip()
        if name in seen:
            raise InputError("DUPLICATE_STATION_FIELD", f"{logical_name}: duplicate {name} at line {line_no}")
        seen.add(name)
        if name.endswith("_duration"):
            parsed = re.fullmatch(r"(\d+)\s+us", value, re.I)
            if not parsed:
                raise InputError("INVALID_STATION_FIELD", f"{logical_name}: invalid {name} at line {line_no}")
            current[name] = int(parsed.group(1))
        else:
            if not re.fullmatch(r"\d+", value):
                raise InputError("INVALID_STATION_FIELD", f"{logical_name}: invalid {name} at line {line_no}")
            current[name] = int(value)
    if current_mac is not None:
        stations[current_mac] = current or {}
    if not stations:
        raise InputError("STATIONS_EMPTY", f"{logical_name}: no station records")
    return stations


def parse_phy_counters(text: str, logical_name: str) -> dict[tuple[str, int], dict[str, int]]:
    if text.count("sample_epoch=") > 1:
        raise InputError("MULTIPLE_SNAPSHOTS", f"{logical_name}: multiple sample_epoch blocks")
    scopes: dict[tuple[str, int], dict[str, int]] = {}
    current_scope: tuple[str, int] | None = None
    for line_no, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*Phy\b", line, re.I):
            header = PHY_RE.match(line)
            if not header:
                current_scope = None
                raise InputError("INVALID_PHY_SCOPE", f"{logical_name}: malformed PHY header at line {line_no}")
            current_scope = (header.group(1), int(header.group(2)))
            if current_scope in scopes:
                raise InputError("DUPLICATE_PHY_SCOPE", f"{logical_name}: duplicate PHY scope at line {line_no}")
            scopes[current_scope] = {}
            continue
        header = PHY_RE.match(line)
        if header:
            current_scope = (header.group(1), int(header.group(2)))
            if current_scope in scopes:
                raise InputError("DUPLICATE_PHY_SCOPE", f"{logical_name}: duplicate PHY scope at line {line_no}")
            scopes[current_scope] = {}
            continue
        if not re.match(r"^\s*Tx\s+multi[ -]user\b", line, re.I):
            continue
        match = COUNTER_LINE_RE.match(line)
        if not match:
            raise InputError("INVALID_MU_COUNTER", f"{logical_name}: malformed MU counter at line {line_no}")
        label = re.sub(r"\s+", " ", match.group(1).strip().lower())
        if label not in KNOWN_COUNTERS:
            continue
        if current_scope is None:
            raise InputError("COUNTER_SCOPE_UNKNOWN", f"{logical_name}: MU counter has no PHY header")
        key = KNOWN_COUNTERS[label]
        if key in scopes[current_scope]:
            raise InputError("DUPLICATE_MU_COUNTER", f"{logical_name}: duplicate {key} at line {line_no}")
        if not re.fullmatch(r"\d+", match.group(2)):
            raise InputError("INVALID_MU_COUNTER", f"{logical_name}: invalid {key} at line {line_no}")
        scopes[current_scope][key] = int(match.group(2))
    return scopes


def read_input(base: Path, value: object, logical_name: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value:
        raise InputError("MANIFEST_FIELD_MISSING", f"{logical_name}: path missing")
    given = Path(value)
    path = given if given.is_absolute() else base / given
    if path.is_symlink() or not path.is_file():
        raise InputError("INPUT_NOT_REGULAR", f"{logical_name}: not a regular non-symlink file")
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise InputError("INPUT_TOO_LARGE", f"{logical_name}: exceeds input size limit")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("INPUT_ENCODING", f"{logical_name}: input is not UTF-8") from exc
    except OSError as exc:
        raise InputError("INPUT_IO", f"{logical_name}: input could not be read") from exc
    if "\ufffd" in text:
        raise InputError("INPUT_REPLACEMENT_CHAR", f"{logical_name}: replacement character indicates damaged decoding")
    return text, hashlib.sha256(raw).hexdigest()


def counter_delta(before: dict[str, int], after: dict[str, int], key: str) -> dict[str, object]:
    old, new = before.get(key), after.get(key)
    if old is None or new is None:
        return {"before": old, "after": new, "delta": None, "status": "missing"}
    if new < old:
        return {"before": old, "after": new, "delta": None, "status": "reset_or_wrap"}
    return {"before": old, "after": new, "delta": new - old, "status": "comparable"}


def blocked_delta(before: dict[str, int], after: dict[str, int], key: str, status: str) -> dict[str, object]:
    return {"before": before.get(key), "after": after.get(key), "delta": None, "status": status}


def parse_monotonic_ns(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError("TIME_INVALID", f"{label}: expected non-negative monotonic nanoseconds")
    return value


def validate_manifest(manifest: object) -> tuple[dict, dict, dict, list[dict], list[str], dict[str, list[str]]]:
    if not isinstance(manifest, dict) or manifest.get("schema") != "mt6639-wifi7-mu-window-v1":
        raise InputError("SCHEMA_UNSUPPORTED", "manifest schema must be mt6639-wifi7-mu-window-v1")
    before, after, scope = manifest.get("before"), manifest.get("after"), manifest.get("scope")
    if not all(isinstance(item, dict) for item in (before, after, scope)):
        raise InputError("MANIFEST_SHAPE", "manifest requires before, after, and scope objects")
    if manifest.get("clock_id") != "AP_BOOTTIME_NS":
        raise InputError("CLOCK_UNSUPPORTED", "clock_id must be AP_BOOTTIME_NS")
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise InputError("POLICY_MISSING", "policy object is required")
    if set(policy) != {"max_sample_span_ms", "max_outer_window_ms"}:
        raise InputError("POLICY_INVALID", "policy contains missing or unsupported fields")
    max_sample_ms, max_window_ms = policy.get("max_sample_span_ms"), policy.get("max_outer_window_ms")
    for value, name, maximum in ((max_sample_ms, "max_sample_span_ms", 60000),
                                 (max_window_ms, "max_outer_window_ms", 900000)):
        if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= maximum):
            raise InputError("POLICY_INVALID", f"policy.{name} must be within 1..{maximum}")

    shared_keys = ("ap_id", "boot_id", "driver_epoch_id", "counter_epoch_id", "bss_id", "bssid",
                   "interface", "frequency_mhz", "width_mhz", "phy_name", "band_index")
    identity_keys = ("ap_id", "boot_id", "driver_epoch_id", "counter_epoch_id", "bss_id", "bssid", "interface", "phy_name")
    comparison_issues: list[str] = []
    target_issues: dict[str, list[str]] = {}
    for key in shared_keys:
        left, right = before.get(key), after.get(key)
        if key in identity_keys and (not isinstance(left, str) or not left.strip() or
                                     not isinstance(right, str) or not right.strip()):
            raise InputError("IDENTITY_INVALID", f"before/after {key} must be non-empty strings")
        if left in (None, "") or right in (None, ""):
            raise InputError("IDENTITY_MISSING", f"before/after {key} is required")
        if left != right:
            comparison_issues.append("EPOCH_CHANGED" if key in identity_keys else "BSS_OR_SCOPE_CHANGED")
    if not MAC_RE.fullmatch(str(before["bssid"])) or not MAC_RE.fullmatch(str(after["bssid"])):
        raise InputError("BSSID_INVALID", "BSSID must be a MAC address")
    frequency, width, band_index = before["frequency_mhz"], before["width_mhz"], before["band_index"]
    if isinstance(frequency, bool) or not isinstance(frequency, int) or not (5925 <= frequency <= 7125):
        raise InputError("NOT_6GHZ", "frequency_mhz must be within 5925..7125")
    if isinstance(width, bool) or not isinstance(width, int) or width not in (20, 40, 80, 160, 320):
        raise InputError("WIDTH_INVALID", "width_mhz must be 20, 40, 80, 160, or 320")
    other_frequency, other_width, other_band = after["frequency_mhz"], after["width_mhz"], after["band_index"]
    if isinstance(other_frequency, bool) or not isinstance(other_frequency, int) or not (5925 <= other_frequency <= 7125):
        raise InputError("NOT_6GHZ", "after.frequency_mhz must be within 5925..7125")
    if isinstance(other_width, bool) or not isinstance(other_width, int) or other_width not in (20, 40, 80, 160, 320):
        raise InputError("WIDTH_INVALID", "after.width_mhz must be 20, 40, 80, 160, or 320")
    if isinstance(other_band, bool) or not isinstance(other_band, int) or other_band < 0:
        raise InputError("PHY_INVALID", "after.band_index must be a non-negative integer")
    if not isinstance(before["interface"], str) or not before["interface"]:
        raise InputError("INTERFACE_INVALID", "interface must be non-empty")
    if not isinstance(before["phy_name"], str) or not before["phy_name"]:
        raise InputError("PHY_INVALID", "phy_name must be non-empty")
    if isinstance(band_index, bool) or not isinstance(band_index, int) or band_index < 0:
        raise InputError("PHY_INVALID", "band_index must be a non-negative integer")
    if not isinstance(manifest.get("bss_id"), str) or not manifest["bss_id"].strip():
        raise InputError("BSS_ID_MISSING", "manifest bss_id must be non-empty")
    if before.get("bss_id") != manifest.get("bss_id") or after.get("bss_id") != manifest.get("bss_id"):
        comparison_issues.append("BSS_ID_CHANGED")
    scope_binding_matches = all(
        sample.get(key) == scope.get(key)
        for sample in (before, after)
        for key in ("interface", "phy_name", "band_index")
    )
    if not scope_binding_matches:
        comparison_issues.append("PHY_BSS_BINDING_CHANGED")
    binding = scope.get("binding_evidence_ref")
    if not isinstance(binding, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", binding):
        raise InputError("PHY_BSS_BINDING_MISSING", "scope.binding_evidence_ref is required")

    targets = manifest.get("eht_clients")
    if not isinstance(targets, list) or len(targets) < 2:
        raise InputError("TARGETS_INCOMPLETE", "at least two EHT clients are required")
    seen_macs, seen_clients, seen_mlds = set(), set(), set()
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("mac"), str) or not MAC_RE.fullmatch(target["mac"]):
            raise InputError("TARGET_MAC_INVALID", "each EHT client requires a valid MAC")
        if target.get("single_link") is not True:
            raise InputError("MLO_TARGET_UNSUPPORTED", "each target must explicitly be a single-link client")
        if "mld_id" not in target:
            raise InputError("MLD_STATUS_MISSING", "each target must explicitly include mld_id or null")
        if target.get("mld_id") is not None:
            raise InputError("MLO_TARGET_UNSUPPORTED", "MLO/link identities are not supported by v1")
        client_id = target.get("client_id")
        evidence_ref = target.get("evidence_ref")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", client_id) or client_id in seen_clients:
            raise InputError("CLIENT_ID_INVALID", "target client_id must be unique and opaque")
        if not isinstance(evidence_ref, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", evidence_ref):
            raise InputError("EHT_EVIDENCE_MISSING", "target evidence_ref must be a logical reference")
        mac = target["mac"].lower()
        if mac in seen_macs:
            raise InputError("DUPLICATE_TARGET", "duplicate EHT client MAC")
        seen_macs.add(mac)
        seen_clients.add(client_id)
        if client_id in seen_mlds:
            raise InputError("DUPLICATE_MLD", "targets must be independent clients")
        seen_mlds.add(client_id)
        target["mac"] = mac

    spans = {}
    for label, sample in (("before", before), ("after", after)):
        sources = sample.get("sources")
        if not isinstance(sources, dict):
            raise InputError("SOURCE_METADATA_MISSING", f"{label}: sources object is required")
        if set(sources) != {"station_dump", "tx_stats"}:
            raise InputError("SOURCE_METADATA_INVALID", f"{label}: expected exactly station_dump and tx_stats")
        sample_times = []
        parsed_sources = {}
        for key in ("station_dump", "tx_stats"):
            source = sources.get(key)
            if not isinstance(source, dict) or isinstance(source.get("rc"), bool) or not isinstance(source.get("rc"), int) or source.get("rc") != 0:
                raise InputError("SOURCE_COMMAND_FAILED", f"{label}.{key}: command rc must be zero")
            if set(source) != {"path", "rc", "clock_id", "run_id", "started_ns", "completed_ns"}:
                raise InputError("SOURCE_METADATA_INVALID", f"{label}.{key}: unsupported or missing source fields")
            if source.get("clock_id") != manifest["clock_id"] or not isinstance(source.get("run_id"), str) or not source["run_id"]:
                raise InputError("SOURCE_CLOCK_MISMATCH", f"{label}.{key}: clock/run identity missing")
            start = parse_monotonic_ns(source.get("started_ns"), f"{label}.{key}.started_ns")
            end = parse_monotonic_ns(source.get("completed_ns"), f"{label}.{key}.completed_ns")
            if end < start:
                raise InputError("SOURCE_TIME_INVALID", f"{label}.{key}: end precedes start")
            sample_times.extend((start, end))
            parsed_sources[key] = source
        if parsed_sources["station_dump"]["run_id"] != parsed_sources["tx_stats"]["run_id"]:
            raise InputError("SOURCE_RUN_MISMATCH", f"{label}: station and MU sources are not from the same capture run")
        span = max(sample_times) - min(sample_times)
        if span > max_sample_ms * 1_000_000:
            raise InputError("SAMPLE_SPAN_EXCEEDED", f"{label}: source capture span exceeds policy")
        spans[label] = (min(sample_times), max(sample_times))
        association_map = sample.get("association_generation")
        if not isinstance(association_map, dict):
            raise InputError("ASSOCIATION_GENERATION_MISSING", f"{label}: association_generation map required")
    if before["boot_id"] == after["boot_id"]:
        if spans["after"][0] <= spans["before"][1]:
            raise InputError("WINDOW_ORDER_INVALID", "all before sources must finish before after sources start")
        if spans["after"][1] - spans["before"][0] > max_window_ms * 1_000_000:
            raise InputError("WINDOW_SPAN_EXCEEDED", "outer observation window exceeds policy")

    for target in targets:
        mac = target["mac"]
        first = before["association_generation"].get(mac)
        second = after["association_generation"].get(mac)
        if not isinstance(first, str) or not first or not isinstance(second, str) or not second:
            raise InputError("ASSOCIATION_GENERATION_MISSING", "target association generation is missing")
        if first != second:
            target_issues[mac] = ["ASSOCIATION_CHANGED"]
    return before, after, scope, targets, sorted(set(comparison_issues)), target_issues


def analyze(manifest: dict, manifest_path: Path) -> dict[str, object]:
    before, after, scope, targets, manifest_issues, target_issues = validate_manifest(manifest)
    base = manifest_path.parent
    parsed = {}
    source_info = []
    incomplete_reasons = list(manifest_issues)
    for label, sample in (("before", before), ("after", after)):
        source_data = {}
        for source_key, parser_fn in (("station_dump", parse_station_dump), ("tx_stats", parse_phy_counters)):
            source = sample["sources"][source_key]
            logical = f"{label}.{source_key}"
            text, sha = read_input(base, source.get("path"), logical)
            parsed_source = parser_fn(text, logical)
            source_data[source_key] = parsed_source
            source_info.append({"logical_name": logical, "sha256": sha,
                                "selected_scope": sample["interface"] if source_key == "station_dump" else
                                {"phy_name": sample["phy_name"], "band_index": sample["band_index"]},
                                "run_id_token": token(source["run_id"])})
        counters = source_data["tx_stats"].get((sample["phy_name"], sample["band_index"]))
        if counters is None:
            counters = {}
            incomplete_reasons.append(f"{label.upper()}_PHY_SCOPE_MISSING")
        parsed[label] = {"stations": source_data["station_dump"], "counters": counters}

    stations_out = []
    active_count = 0
    sta_scope_keys = ("ap_id", "boot_id", "driver_epoch_id", "counter_epoch_id", "bss_id", "bssid",
                      "interface", "frequency_mhz", "width_mhz")
    sta_scope_matches = (all(before.get(key) == after.get(key) for key in sta_scope_keys) and
                         before.get("interface") == after.get("interface") == scope["interface"] and
                         before.get("phy_name") == after.get("phy_name") == scope["phy_name"] and
                         before.get("band_index") == after.get("band_index") == scope["band_index"])
    for target in targets:
        mac = target["mac"]
        left = parsed["before"]["stations"].get(mac)
        right = parsed["after"]["stations"].get(mac)
        if left is None or right is None:
            stations_out.append({"client_token": token(target["client_id"]), "station_token": token(mac),
                                 "association_state": "not_present_in_both", "active_downlink": None,
                                 "eht_basis": "manifest_with_evidence_reference"})
            incomplete_reasons.append("TARGET_STATION_MISSING")
            continue
        association_changed = bool(target_issues.get(mac))
        interface_matches = left.get("interface") == before["interface"] and right.get("interface") == after["interface"]
        if association_changed:
            delta_status = "association_changed"
            incomplete_reasons.extend(target_issues[mac])
        elif not sta_scope_matches or not interface_matches:
            delta_status = "scope_changed"
            incomplete_reasons.append("STA_SCOPE_CHANGED")
        else:
            delta_status = None
        if delta_status:
            tx_bytes = blocked_delta(left, right, "tx_bytes", delta_status)
            tx_duration = blocked_delta(left, right, "tx_duration", delta_status)
            rx_bytes = blocked_delta(left, right, "rx_bytes", delta_status)
            rx_duration = blocked_delta(left, right, "rx_duration", delta_status)
        else:
            tx_bytes = counter_delta(left, right, "tx_bytes")
            tx_duration = counter_delta(left, right, "tx_duration")
            rx_bytes = counter_delta(left, right, "rx_bytes")
            rx_duration = counter_delta(left, right, "rx_duration")
        if tx_bytes["status"] != "comparable" or tx_duration["status"] != "comparable":
            incomplete_reasons.append("STA_COUNTER_INCOMPARABLE")
            is_active = None
        else:
            is_active = int(tx_bytes["delta"]) > 0 and int(tx_duration["delta"]) > 0
            active_count += int(is_active)
        stations_out.append({"client_token": token(target["client_id"]), "station_token": token(mac),
                             "association_state": "association_changed" if association_changed else "present_same_generation",
                             "eht_basis": "manifest_with_evidence_reference",
                             "tx_bytes": tx_bytes, "tx_duration_us": tx_duration,
                             "rx_bytes": rx_bytes, "rx_duration_us": rx_duration,
                             "active_downlink": is_active})

    mu_epoch_matches = all(before.get(key) == after.get(key) for key in
                           ("ap_id", "boot_id", "driver_epoch_id", "counter_epoch_id"))
    mu_scope_matches = (before.get("phy_name") == after.get("phy_name") == scope["phy_name"] and
                        before.get("band_index") == after.get("band_index") == scope["band_index"])
    mu_status = "comparable" if mu_epoch_matches and mu_scope_matches else (
        "epoch_changed" if not mu_epoch_matches else "scope_changed")
    if mu_status != "comparable":
        incomplete_reasons.append("MU_EPOCH_OR_SCOPE_CHANGED")
    mu = {key: (counter_delta(parsed["before"]["counters"], parsed["after"]["counters"], key)
                if mu_status == "comparable" else
                blocked_delta(parsed["before"]["counters"], parsed["after"]["counters"], key, mu_status))
          for key in ("mu_mpdu", "mu_successful_mpdu", "mu_beamforming")}
    if mu["mu_mpdu"]["status"] != "comparable" or mu["mu_successful_mpdu"]["status"] != "comparable":
        incomplete_reasons.append("MU_COUNTER_INCOMPARABLE")
    if mu["mu_successful_mpdu"]["status"] == "comparable" and mu["mu_mpdu"]["status"] == "comparable" and \
            int(mu["mu_successful_mpdu"]["delta"]) > 0 and int(mu["mu_mpdu"]["delta"]) == 0:
        incomplete_reasons.append("COUNTER_UPDATE_INCONSISTENT")

    mu_tx = None if mu["mu_mpdu"]["status"] != "comparable" else int(mu["mu_mpdu"]["delta"]) > 0
    mu_success = None if mu["mu_successful_mpdu"]["status"] != "comparable" else int(mu["mu_successful_mpdu"]["delta"]) > 0
    if incomplete_reasons:
        result, zh = "incomplete", "部分输入或计数不可比较；保留已解析事实，但不能作完整 MU 判定。"
        reasons = sorted(set(incomplete_reasons))
    elif active_count >= 2 and mu_tx and mu_success:
        result, zh = "mu_initial_evidence", "所选 PHY 出现成功 MU 发送计数增量，至少两个目标 EHT STA 在观察窗各有下行活动；这是 PHY 级 MU 初证，未确认目标 STA 属于同一 MU PPDU。"
        reasons = []
    else:
        result, zh = "not_triggered", "该观察窗未达到双目标下行活动与成功 MU 计数同时增长的初证门槛。"
        reasons = []
        if active_count < 2:
            reasons.append("FEWER_THAN_TWO_ACTIVE_TARGETS")
        if mu_tx is False:
            reasons.append("NO_MU_MPDU_DELTA")
        elif mu_success is False:
            reasons.append("NO_SUCCESSFUL_MU_DELTA")

    b_start, b_end = min(s["started_ns"] for s in before["sources"].values()), max(s["completed_ns"] for s in before["sources"].values())
    a_start, a_end = min(s["started_ns"] for s in after["sources"].values()), max(s["completed_ns"] for s in after["sources"].values())
    same_boot = before["boot_id"] == after["boot_id"]
    return {
        "schema": "mt6639-wifi7-mu-compare-v1", "result": result, "result_zh": zh,
        "reason_codes": reasons, "parse_complete": True,
        "preconditions_complete": not incomplete_reasons,
        "scope": {"ap_token": token(str(before["ap_id"])), "bss_token": token(str(before["bss_id"])),
                  "interface": before["interface"], "frequency_mhz": before["frequency_mhz"],
                  "width_mhz": before["width_mhz"], "phy_name": scope["phy_name"], "band_index": scope["band_index"],
                  "binding_evidence_ref_token": token(scope["binding_evidence_ref"])},
        "window": {"clock_id": manifest["clock_id"], "before_span_ns": [b_start, b_end],
                   "after_span_ns": [a_start, a_end], "common_inner_window_ns": [b_end, a_start] if same_boot else None,
                   "outer_window_ns": [b_start, a_end] if same_boot else None,
                   "policy": {"max_sample_span_ms": manifest["policy"]["max_sample_span_ms"],
                              "max_outer_window_ms": manifest["policy"]["max_outer_window_ms"]}},
        "mu": mu, "stations": stations_out, "active_target_count": active_count,
        "facts": {"phy_mu_tx_observed": mu_tx, "phy_mu_success_observed": mu_success,
                  "multiple_target_downlink_activity_observed": active_count >= 2},
        "attribution": "phy_aggregate_only", "mu_type": "undetermined", "sources": source_info,
        "limitations": ["PHY aggregate MU counters cannot identify the participating STA set or prove a shared MU PPDU.",
                        "The evidence does not distinguish OFDMA, MRU, or MU-MIMO and does not prove EHT-only MU traffic.",
                        "A not_triggered result applies only to this observation window; it does not show unsupported capability.",
                        "Manifest EHT and PHY-to-BSS references are operator-supplied provenance, not independently decoded by this tool."],
    }


def output_json(result: dict, out_path: Path | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if out_path is None:
        sys.stdout.write(encoded)
        return
    if out_path.exists() or out_path.is_symlink():
        raise OSError("output already exists")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="offline JSON capture manifest")
    parser.add_argument("--out", help="new JSON output path; default is stdout")
    args = parser.parse_args(argv)
    output_path = Path(args.out) if args.out else None
    try:
        manifest_path = Path(args.manifest)
        raw = manifest_path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise InputError("MANIFEST_TOO_LARGE", "manifest exceeds input size limit")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise InputError("MANIFEST_ENCODING", "manifest is not UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise InputError("MANIFEST_JSON_INVALID", "manifest is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise InputError("MANIFEST_SHAPE", "manifest root must be an object")
        result = analyze(manifest, manifest_path)
        output_json(result, output_path)
        return 0 if result["result"] != "incomplete" else 2
    except InputError as exc:
        result = {"schema": "mt6639-wifi7-mu-compare-v1", "result": "incomplete",
                  "result_zh": "输入或前置条件不完整，不能作完整 MU 判定。",
                  "reason_codes": [exc.code], "detail": exc.detail, "parse_complete": False,
                  "preconditions_complete": False, "attribution": "phy_aggregate_only", "mu_type": "undetermined"}
        try:
            output_json(result, output_path)
        except OSError:
            print("ERROR output_write_failed", file=sys.stderr)
            return 1
        return 2
    except OSError:
        print("ERROR local_io_failed", file=sys.stderr)
        return 1
    except (TypeError, ValueError, KeyError):
        print("ERROR malformed_manifest", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
