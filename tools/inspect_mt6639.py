#!/usr/bin/env python3
"""T01 read-only MT6639 evidence collector (host side, standard library only).

Each probe is one bounded `adb shell` call. Raw stdout/stderr/rc/elapsed stay in
the private output directory (0700, files 0600); the printed JSON digest keeps
derived evidence only (hashes, counts, booleans, frequencies), never SSID,
BSSID, MAC, IP or fingerprint text. Empty/missing output is recorded as such
and never reported as hardware absence. This tool gathers T01 evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time

CFG_CANDIDATES = ("/odm/firmware/wifi.cfg", "/odm/etc/wifi/wifi.cfg",
                  "/vendor/etc/wifi/wifi.cfg", "/vendor/firmware/wifi.cfg")
KO_PATH = "/vendor/lib/modules/wlan_drv_gen4m_eap_6639.ko"
MAX_TIMEOUT = 120.0
HELPER_ROOTS = ("/data/local/tmp/", "/data/adb/")
HELPER_RE = re.compile(r"^/data/(?:local/tmp|adb)/[A-Za-z0-9._/-]+$")
ADB_ERR = re.compile(r"^adb: |device offline|device unauthorized"
                     r"|no devices/emulators found|protocol fault|connection reset", re.I | re.M)
MISSING = re.compile(r"no such file or directory|: not found|unknown command"
                     r"|can't find service|inaccessible or not found", re.I)
DUMPSYS_PIPE = (
    "runcon u:r:shell:s0 sh -c '"
    "{ dumpsys wifi; rc=$?; echo dumpsys_rc=$rc; } | "
    'grep -E "mOverrideCountryCode|mDriverCountryCode|firmwareVersion|driverVersion|^dumpsys_rc="; '
    "filter_rc=$?; echo filter_rc=$filter_rc'"
)


def build_probes(helper):
    def p(name, cmd, accept=(0,), marker=None):
        return {"name": name, "cmd": cmd, "accept": accept, "marker": marker}

    def binder(cmd):
        return "runcon u:r:shell:s0 " + cmd

    probes = [
        p("id", "id"), p("prop_model", "getprop ro.product.model"),
        p("prop_device", "getprop ro.product.device"),
        p("prop_fingerprint", "getprop ro.build.fingerprint"),
        p("prop_security_patch", "getprop ro.build.version.security_patch"),
        p("kernel", "uname -a"), p("boot_hardware", "getprop ro.boot.hardware"),
        p("boot_verified", "getprop ro.boot.verifiedbootstate"),
        p("selinux_enforce", "getenforce"),
        p("boot_id", "cat /proc/sys/kernel/random/boot_id"),
        p("mount_namespace_self", "readlink /proc/self/ns/mnt"),
        p("mount_namespace_init", "readlink /proc/1/ns/mnt"),
        p("mountinfo", "cat /proc/self/mountinfo"),
    ]
    for path in CFG_CANDIDATES:
        probes.append(p("cfg_sha256:" + path, "sha256sum " + path))
        probes.append(p("cfg_disallow:" + path,
                        "grep -n -i '^DisallowBand6G' " + path, accept=(0, 1)))
    probes += [
        p("ko_sha256", "sha256sum " + KO_PATH),
        p("wifi_status", binder("cmd wifi status")),
        p("wifi_country_code", binder("cmd wifi get-country-code")),
        p("wifi_allowed_channel_b8", binder("cmd wifi get-allowed-channel -b 8")),
        p("wifi_supported_features", binder("cmd wifi get-wifi-supported-features")),
        p("wifi_wiphy_capabilities", binder("cmd wifi get-device-wiphy-capabilities wlan0")),
        p("wifi_list_networks", binder("cmd wifi list-networks")),
        p("dumpsys_wifi_filtered", DUMPSYS_PIPE, marker=("dumpsys_rc=", "filter_rc=")),
        p("wpa_status", "/vendor/bin/wpa_cli -i wlan0 status"),
        p("wpa_signal_poll", "/vendor/bin/wpa_cli -i wlan0 signal_poll"),
        p("wpa_mlo_status", "/vendor/bin/wpa_cli -i wlan0 mlo_status"),
        p("iw_dev", "iw dev"), p("iw_phy", "iw phy"),
        p("iw_link", "iw dev wlan0 link"), p("ip_addr", "ip addr"),
        p("ip_route4", "ip -4 route show table all dev wlan0"),
        p("ip_route6", "ip -6 route show table all dev wlan0"),
        p("ksu_dir", "ls -ld /data/adb/ksu"),
        p("ksu_ksud", "ls -ld /data/adb/ksud"),
        p("ksu_version", "/data/adb/ksud --version"),
        p("ksu_modules", "ls -1 /data/adb/modules"),
        p("bt_state", binder("settings get global bluetooth_on")),
        p("bt_service", "getprop init.svc.bluetooth"),
    ]
    if helper:
        probes.append(p("wiphy_helper", helper))
    return probes


def classify(rc, out, err, accept, marker):
    blob = err + "\n" + out
    if rc not in accept and ADB_ERR.search(err):
        return "adb_error", "adb transport failure; says nothing about the device"
    if rc not in accept and (rc == 127 or MISSING.search(blob)):
        return "missing", "explicit command/file absence"
    if marker:
        markers = [marker] if isinstance(marker, str) else marker
        for m in markers:
            found = re.search(re.escape(m) + r"(\d+)", out)
            if not found:
                return "failed", f"{m} marker missing; pipeline result untrusted"
            if int(found.group(1)) != 0:
                return "failed", f"{m}{found.group(1)} (inner command failed)"
    if rc in accept:
        if out.strip():
            return "ok", ""
        return "empty", "no output on stdout; not evidence of absence"
    return "failed", "nonzero exit"


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def run_probe(adb, serial, probe, timeout, probes_dir):
    started = time.monotonic()
    timed_out = False
    exec_err = None
    try:
        done = subprocess.run([adb, "-s", serial, "shell", probe["cmd"]],
                              capture_output=True, text=True, errors="replace",
                              timeout=timeout)
        rc, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc, out, err = None, _as_text(exc.stdout), _as_text(exc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        exec_err = str(exc)
        rc, out, err = None, "", f"adb execution error: {type(exc).__name__}"
    elapsed = round(time.monotonic() - started, 3)
    if timed_out:
        status, note = "timeout", f"adb call exceeded {timeout}s; capability unknown"
    elif exec_err is not None:
        status, note = "adb_error", "adb subprocess execution failure; says nothing about the device"
    else:
        status, note = classify(rc, out, err, probe["accept"], probe["marker"])
        if status == "ok" and probe["name"].startswith("wpa_"):
            head = out.strip().splitlines()[0].upper()
            if head.startswith("UNKNOWN COMMAND"):
                status, note = "missing", "wpa_cli: UNKNOWN COMMAND (command/interface absent)"
            elif head.startswith("FAIL"):
                status, note = "failed", "wpa_cli: FAIL with rc 0 (query failed)"
        elif status == "ok" and (probe["name"].startswith("wifi_") or "cmd wifi" in probe["cmd"]):
            if re.search(r"unknown command|can't find service", out, re.I):
                status, note = "missing", "cmd wifi: unknown command or service absent with rc 0"
        elif status == "ok" and probe["name"] == "dumpsys_wifi_filtered":
            matched = [l for l in out.splitlines()
                       if not l.startswith("dumpsys_rc=") and not l.startswith("filter_rc=") and l.strip()]
            if not matched:
                status, note = "empty", "dumpsys wifi produced no matching country or version fields"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", probe["name"])
    _write(os.path.join(probes_dir, stem + ".out"), out)
    _write(os.path.join(probes_dir, stem + ".err"), err)
    return {"name": probe["name"], "cmd": probe["cmd"], "status": status, "rc": rc,
            "elapsed_s": elapsed, "stdout_bytes": len(out.encode()), "note": note,
            "stderr_bytes": len(err.encode()), "raw_stdout": "probes/" + stem + ".out",
            "raw_stderr": "probes/" + stem + ".err", "stdout": out, "stderr": err}


def _write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _one(text):
    stripped = (text or "").strip()
    return stripped.splitlines()[0].strip() if stripped else None


def _sha12(value):
    value = (value or "").strip()
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else None


def _kv(text, key):
    found = re.search(key + r"[=:]\s*([^,\s]+)", text or "")
    return found.group(1) if found else None


def derive(results):
    by = {r["name"]: r for r in results}

    def text(name):
        return by.get(name, {}).get("stdout") or ""

    first = lambda name: _one(text(name))  # noqa: E731
    status = lambda name: by.get(name, {}).get("status")  # noqa: E731
    lines = lambda name: [l for l in text(name).splitlines() if l.strip()]  # noqa: E731

    uname = (_one(text("kernel")) or "").split()
    ns_self, ns_init = _one(text("mount_namespace_self")), _one(text("mount_namespace_init"))
    mountinfo = text("mountinfo").splitlines()
    identity = {
        "model": first("prop_model"), "device": first("prop_device"),
        "fingerprint_sha256_12": _sha12(first("prop_fingerprint")),
        "security_patch": first("prop_security_patch"),
        "kernel_release": uname[2] if len(uname) > 2 else (_one(text("kernel")) or None),
        "boot_hardware": first("boot_hardware"), "boot_id": first("boot_id"),
        "verified_boot_state": first("boot_verified"),
        "selinux_mode": first("selinux_enforce"),
        "mount_namespace_self": ns_self, "mount_namespace_init": ns_init,
        "mount_namespace_same": (ns_self == ns_init) if (ns_self and ns_init) else None,
    }
    minfo_st = status("mountinfo")
    has_minfo = bool(mountinfo and any(l.strip() for l in mountinfo))
    has_cfg_mount = any(" /odm/firmware/wifi.cfg " in " " + line + " " for line in mountinfo)
    mounts = {
        "line_count": len(mountinfo) if minfo_st == "ok" or has_minfo else None,
        "wifi_cfg_entry_count": (sum("wifi.cfg" in line for line in mountinfo)
                                 if minfo_st == "ok" or has_minfo else None),
        "odm_firmware_wifi_cfg_mounted": has_cfg_mount if minfo_st == "ok" else (True if has_cfg_mount else None),
    }
    cfg = {}
    for path in CFG_CANDIDATES:
        sha_line = _one(text("cfg_sha256:" + path)) or ""
        band6 = re.search(r"DisallowBand6G\s+(\S+)", text("cfg_disallow:" + path), re.I)
        cfg[path] = {
            "sha256": sha_line.split()[0] if re.match(r"^[0-9a-f]{64}\b", sha_line) else None,
            "disallow_band6g": band6.group(1) if band6 else None,
            "sha256_status": status("cfg_sha256:" + path),
            "disallow_status": status("cfg_disallow:" + path),
        }
    ko_line = _one(text("ko_sha256")) or ""
    ko = {"path": KO_PATH, "status": status("ko_sha256"),
          "sha256": ko_line.split()[0] if re.match(r"^[0-9a-f]{64}\b", ko_line) else None}
    phy = text("iw_phy")
    bands = sorted({int(b) for b in re.findall(r"^\s*Band (\d+):", phy, re.M)})
    freqs = sorted({int(float(f)) for f in re.findall(r"\*\s+(\d+(?:\.\d+)?)\s+MHz", phy)})
    six = [f for f in freqs if f >= 5925]
    phy_st = status("iw_phy")
    has_six = (4 in bands or bool(six))
    six_present = has_six if phy_st == "ok" else (True if has_six else None)
    iw = {"bands": bands if phy_st == "ok" or bands else None,
          "frequency_count": len(freqs) if phy_st == "ok" or freqs else None,
          "six_ghz_frequency_count": len(six) if phy_st == "ok" or six else None,
          "six_ghz_band_present": six_present,
          "six_ghz_frequency_min": min(six) if six else None,
          "six_ghz_frequency_max": max(six) if six else None,
          "iw_phy_status": phy_st}
    allowed = sorted({int(v) for v in re.findall(r"\b(\d{4})\b", text("wifi_allowed_channel_b8"))
                      if 2400 <= int(v) <= 7125})
    allowed_six = [f for f in allowed if f >= 5925]
    allowed_st = status("wifi_allowed_channel_b8")
    allowed_count = len(allowed) if allowed_st == "ok" else (len(allowed) if allowed else None)
    allowed_six_count = len(allowed_six) if allowed_st == "ok" else (len(allowed_six) if allowed_six else None)
    wifi_text = text("wifi_status")
    wifi_st = status("wifi_status")
    enabled = True if re.search(r"wifi is enabled", wifi_text, re.I) else (
        False if re.search(r"wifi is disabled", wifi_text, re.I) else None)
    ssid_match = re.search(r"SSID:\s*([^,\n]+)", wifi_text)
    ssid_value = ssid_match.group(1).strip() if ssid_match else ""
    if ssid_value.lower() in ("<unknown ssid>", "unknown", "none"):
        ssid_value = ""
    country = re.search(r"(?:country[^:\n=]*[=:]\s*|CountryCode[^:\n=]*[=:]\s*)([A-Za-z]{2})\b",
                        text("wifi_country_code"), re.I)
    if not country:
        country = re.search(r"^\s*([A-Za-z]{2})\s*$", text("wifi_country_code"))
    net_rows = [l.strip() for l in lines("wifi_list_networks") if re.match(r"^\s*\d+\b", l)]
    net_ids = sorted({int(m.group(1)) for l in net_rows if (m := re.match(r"^\s*(\d+)\b", l))})
    net_st = status("wifi_list_networks")
    wifi_saved_count = len(net_ids) if net_st == "ok" else (len(net_ids) if net_ids else None)
    has_wifi_bssid = bool(re.search(r"BSSID:\s*([0-9a-f]{2}:){5}", wifi_text, re.I))
    wifi = {
        "enabled": enabled,
        "country_code": country.group(1).upper() if country else None,
        "frequency_mhz": int(f.group(1)) if (f := re.search(r"[Ff]requency:\s*(\d+)", wifi_text)) else None,
        "ssid_sha256_12": _sha12(ssid_value),
        "bssid_present": has_wifi_bssid if wifi_st == "ok" else (True if has_wifi_bssid else None),
        "supplicant_state": _kv(wifi_text, "Supplicant state"),
        "allowed_channel_b8": {"channel_count": allowed_count,
                               "six_ghz_channel_count": allowed_six_count,
                               "min_mhz": allowed[0] if (allowed_st == "ok" and allowed) else None,
                               "max_mhz": allowed[-1] if (allowed_st == "ok" and allowed) else None,
                               "status": allowed_st},
        "supported_features_line_count": (len(lines("wifi_supported_features"))
                                          if status("wifi_supported_features") == "ok" or lines("wifi_supported_features")
                                          else None),
        "wiphy_capabilities_line_count": (len(lines("wifi_wiphy_capabilities"))
                                          if status("wifi_wiphy_capabilities") == "ok" or lines("wifi_wiphy_capabilities")
                                          else None),
        "saved_network_count": wifi_saved_count,
        "saved_network_block_sha256_12": _sha12("\n".join(sorted(net_rows))) if net_rows else None,
    }
    dumpsys_text = text("dumpsys_wifi_filtered")
    dumpsys = {key: _kv(dumpsys_text, key) for key in
               ("mOverrideCountryCode", "mDriverCountryCode", "firmwareVersion", "driverVersion")}
    inner = re.search(r"dumpsys_rc=(\d+)", dumpsys_text)
    dumpsys["dumpsys_rc"] = int(inner.group(1)) if inner else None
    flt = re.search(r"filter_rc=(\d+)", dumpsys_text)
    dumpsys["filter_rc"] = int(flt.group(1)) if flt else None
    dumpsys["status"] = status("dumpsys_wifi_filtered")
    wpa_kv = dict(re.findall(r"^([A-Za-z_]+)=(.*)$", text("wpa_status"), re.M))
    rssi = dict(re.findall(r"^([A-Za-z_]+)=(.*)$", text("wpa_signal_poll"), re.M)).get("RSSI", "")
    wpa_st = status("wpa_status")
    has_wpa_bssid = bool(wpa_kv.get("bssid"))
    wpa = {"wpa_state": wpa_kv.get("wpa_state") or None,
           "frequency_mhz": int(wpa_kv["freq"]) if wpa_kv.get("freq", "").isdigit() else None,
           "ssid_sha256_12": _sha12(wpa_kv.get("ssid")),
           "bssid_present": has_wpa_bssid if wpa_st == "ok" else (True if has_wpa_bssid else None),
           "rssi_dbm": int(rssi) if rssi.lstrip("-").isdigit() else None,
           "mlo_status_line_count": (len(lines("wpa_mlo_status"))
                                     if status("wpa_mlo_status") == "ok" or lines("wpa_mlo_status")
                                     else None)}
    link_text = text("iw_link")
    link_st = status("iw_link")
    has_conn = bool(re.search(r"Connected to", link_text))
    has_link_bssid = bool(re.search(r"Connected to ([0-9a-f]{2}:){5}", link_text, re.I))
    link = {"connected": has_conn if link_st == "ok" else (True if has_conn else None),
            "frequency_mhz": int(float(f.group(1))) if (f := re.search(r"freq:\s*(\d+(?:\.\d+)?)", link_text)) else None,
            "signal_dbm": int(f.group(1)) if (f := re.search(r"signal:\s*(-?\d+)", link_text)) else None,
            "bssid_present": has_link_bssid if link_st == "ok" else (True if has_link_bssid else None)}
    addr, route4 = text("ip_addr"), text("ip_route4")
    route4_st = status("ip_route4")
    route6_st = status("ip_route6")
    ip_addr_st = status("ip_addr")
    has_def_route = bool(re.search(r"^default\s", route4, re.M))
    ipv4_addrs = re.findall(r"^\s*inet\s", addr, re.M)
    ipv6_addrs = re.findall(r"^\s*inet6\s", addr, re.M)
    r4_lines = lines("ip_route4")
    r6_lines = lines("ip_route6")
    net = {"ipv4_address_count": len(ipv4_addrs) if ip_addr_st == "ok" else (len(ipv4_addrs) if ipv4_addrs else None),
           "ipv6_address_count": len(ipv6_addrs) if ip_addr_st == "ok" else (len(ipv6_addrs) if ipv6_addrs else None),
           "wlan0_route4_line_count": len(r4_lines) if route4_st == "ok" else (len(r4_lines) if r4_lines else None),
           "wlan0_default_route_present": has_def_route if route4_st == "ok" else (True if has_def_route else None),
           "wlan0_route6_line_count": len(r6_lines) if route6_st == "ok" else (len(r6_lines) if r6_lines else None)}
    ksu_dir_st, ksu_ksud_st = status("ksu_dir"), status("ksu_ksud")
    if ksu_dir_st == "ok" or ksu_ksud_st == "ok":
        ksu_present = True
    elif any(st in ("adb_error", "timeout", "failed") for st in (ksu_dir_st, ksu_ksud_st)):
        ksu_present = None
    elif ksu_dir_st == "missing" and ksu_ksud_st == "missing":
        ksu_present = False
    else:
        ksu_present = None
    ksu_mods_st = status("ksu_modules")
    ksu_lines = lines("ksu_modules")
    if ksu_mods_st == "ok":
        ksu_mod_count = len(ksu_lines)
    elif ksu_lines:
        ksu_mod_count = len(ksu_lines)
    elif ksu_mods_st == "missing":
        ksu_mod_count = 0
    else:
        ksu_mod_count = None
    ksu = {"present": ksu_present, "version": first("ksu_version"), "module_count": ksu_mod_count}
    bt = {"bluetooth_on": first("bt_state"), "service_state": first("bt_service")}
    reg6 = {"assessment": "not_assessed",
            "reason": ("T01 gathers prerequisites only. Allowed-channel counts, "
                       "DisallowBand6G values and band registration do not demonstrate "
                       "6 GHz usability: FEM and antenna presence, vendor capability bits "
                       "and AP association evidence are outside this collector."),
            "prerequisites": {
                "six_ghz_band_registered_in_iw_phy": iw["six_ghz_band_present"],
                "allowed_channel_count_b8": allowed_count,
                "allowed_6ghz_channel_count_b8": allowed_six_count,
                "cfg_disallow_band6g": {p: v["disallow_band6g"] for p, v in cfg.items()}}}
    return {"identity": identity, "mounts": mounts, "cfg": cfg, "ko": ko, "iw": iw,
            "wifi": wifi, "dumpsys_wifi_filtered": dumpsys, "wpa": wpa, "link": link,
            "net": net, "ksu": ksu, "bt": bt, "reg6": reg6}


LIMITATIONS = [
    "T01 collects read-only prerequisite evidence only.",
    "No 2.4 GHz or Bluetooth business/throughput evidence is collected.",
    "T02 recovery (restoring stock state after a driver experiment) is not proven here.",
    "empty or missing probe results are recorded as such and never imply hardware absence.",
    "An allowed-channel count plus DisallowBand6G does not make 6 GHz fully usable.",
    "SSID/BSSID/MAC/IP/fingerprint values stay in private raw logs; the digest keeps hashes and booleans.",
]


def fail(message):
    sys.stderr.write("inspect_mt6639: " + message + "\n")
    raise SystemExit(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adb", required=True, help="absolute path to adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", required=True, help="new output directory")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-call seconds")
    parser.add_argument("--wiphy-helper", default=None,
                        help="absolute read-only helper path under /data/local/tmp or /data/adb")
    args = parser.parse_args(argv)

    if not os.path.isabs(args.adb) or not os.access(args.adb, os.X_OK) or not os.path.isfile(args.adb):
        fail("--adb must be an existing executable absolute path")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", args.serial):
        fail("--serial must be a plain adb serial")
    if not (0 < args.timeout <= MAX_TIMEOUT):
        fail(f"--timeout must be greater than 0 and at most {MAX_TIMEOUT:g}")
    if args.wiphy_helper is not None:
        helper = args.wiphy_helper
        normalized = posixpath.normpath(helper)
        if (not HELPER_RE.fullmatch(helper) or ".." in helper.split("/")
                or normalized != helper
                or not any(normalized.startswith(root) for root in HELPER_ROOTS)):
            fail("--wiphy-helper must be an absolute path under /data/local/tmp/ or "
                 "/data/adb/ without shell metacharacters")

    out = os.path.abspath(args.out)
    try:
        os.makedirs(out, mode=0o700, exist_ok=False)
    except FileExistsError:
        fail(f"--out already exists: {out}")
    os.chmod(out, 0o700)
    probes_dir = os.path.join(out, "probes")
    os.makedirs(probes_dir, mode=0o700)
    os.chmod(probes_dir, 0o700)

    results = [run_probe(args.adb, args.serial, probe, args.timeout, probes_dir)
               for probe in build_probes(args.wiphy_helper)]
    counts = {}
    for entry in results:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    digest = {"tool": "inspect_mt6639", "out_dir": out,
              "timeout_s": args.timeout, "probe_counts": counts,
              "probes": [{k: v for k, v in e.items() if k not in ("stdout", "stderr")}
                         for e in results],
              "derived": derive(results), "limitations": LIMITATIONS}
    rendered = json.dumps(digest, ensure_ascii=False, indent=2) + "\n"
    _write(os.path.join(out, "summary.json"), rendered)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
