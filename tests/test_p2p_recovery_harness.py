"""Exercise recover() with deterministic shell stubs; never calls Android or Wi-Fi."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tools/p2p_once.sh").read_text(encoding="utf-8")


def recover_function():
    lines = SOURCE.splitlines()
    start = next(i for i, line in enumerate(lines) if line == "recover() {")
    depth = 0
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line.endswith("() {") or line == "recover() {":
            depth += 1
        elif line == "}":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start:i + 1])
    raise AssertionError("could not extract recover() shell function")


def global_function(name):
    lines = SOURCE.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"{name}() {{")
    depth = 0
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line.endswith("() {") or line == f"{name}() {{":
            depth += 1
        elif line == "}":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start:i + 1])
    raise AssertionError(f"could not extract {name}() shell function")


LOAD_OWNER_INTENT = global_function("load_owner_intent")
RECOVER = recover_function()


HARNESS = r'''#!/bin/sh
umask 077
D="$TASK_DIR"
GO_FREQ=${GO_FREQ:-2412}
case "$GO_FREQ" in
    2412) SSID_BASE=DIRECT-mt-Dijun ;;
    6135) SSID_BASE=DIRECT-mt-Dijun-6G ;;
esac
SSID=$SSID_BASE
boot_ok() { return 0; }
flock() { return 0; }
idle() { printf 'inactive\n' > "$1"; [ "$IDLE_OK" = 1 ]; }
sleep() { :; }
raw_stat() {
    case "$1" in
        u) command stat -c %u "$2" 2>/dev/null || command stat -f %u "$2" ;;
        a) command stat -c %a "$2" 2>/dev/null || command stat -f %Lp "$2" ;;
    esac
}
stat() {
    if [ "$1" = -c ]; then
        case "$2" in
            %u) case "$3" in "$TASK_DIR"/*) printf '0\n' ;; *) raw_stat u "$3" ;; esac ;;
            %a) raw_stat a "$3" ;;
            *) return 64 ;;
        esac
    else
        command stat "$@"
    fi
}
not_failed() { ! grep -Eq 'FAILED|Exception:|not initialized|UNKNOWN|not available' "$1"; }
p2p() {
    printf 'p2p %s\n' "$*" >> "$CALLS"
    case "$*" in
        'get-group-info') printf '%s' "$GROUP_INFO"; return "${GROUP_QUERY_RC:-0}" ;;
        'get-connection-info') printf '%s' "$CONNECTION_INFO"; return "${CONNECTION_QUERY_RC:-0}" ;;
        'remove-group')
            rc=${REMOVE_RC:-0}
            [ "$rc" != 0 ] || GROUP_INFO=null
            return "$rc" ;;
        'list-saved-groups') printf 'saved-groups\n'; return "${SAVED_RC:-0}" ;;
        'deinit') return "${DEINIT_RC:-0}" ;;
        'get-state') printf 'P2p client is not initialized\n' ;;
    esac
}
sh() {
    action=$2
    [ "$1" = "$D/p2p_eht_profile.sh" ] && action=$3
    printf 'helper %s %s\n' "$1" "$action" >> "$CALLS"
    case "$1:$2:$3" in
        "$D/p2p_eht_profile.sh:$D:restore")
            [ "${EHT_RESTORE_RC:-0}" = 0 ] || return "$EHT_RESTORE_RC"
            printf 'eht_state=restored\n' ;;
        "$D/twt_recover.sh:check:"*) return "${CHECK_RC:-0}" ;;
        "$D/twt_recover.sh:select:"*) return "${SELECT_RC:-0}" ;;
        *) return 64 ;;
    esac
}
mv() {
    if [ "${JOURNAL_FAIL:-0}" = 1 ] && [ "${1:-}" = "$D/recovery.resources.tmp" ]; then
        return 1
    fi
    command mv "$@"
}
''' + LOAD_OWNER_INTENT + "\n" + global_function("group_ssid_matches") + "\n" + RECOVER + r'''
recover
rc=$?
if [ "${RUN_TWICE:-0}" = 1 ]; then
    recover
    second_rc=$?
    [ "$rc" = 0 ] || exit "$rc"
    exit "$second_rc"
fi
exit "$rc"
'''


class RecoveryHarnessTests(unittest.TestCase):
    def run_recovery(self, group_info, *, idle_ok, check_rc=0, query_rc=0,
                     connection_rc=0,
                     remove_rc=0, select_rc=0, deinit_rc=0, saved_rc=0, eht_restore_rc=0,
                     journal_fail=False, run_twice=False,
                     with_identity_snapshot=False, with_owner_intent=False,
                     owner_ssid="", owner_freq=None,
                     with_eht=True, go_freq=2412):
        temp = tempfile.TemporaryDirectory(prefix="p2p-recovery-harness-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        task = root / "task"
        task.mkdir(mode=0o700)
        (task / "init.intent").touch()
        (task / "boot.before").write_text("boot-test\n")
        (task / "reconnect.key").write_text("private-test-only\n")
        (task / "saved.before").write_text("saved-groups\n")
        if with_eht:
            (task / "eht.request").touch()
        group_name = "DIRECT-mt-Dijun-6G" if go_freq == 6135 else "DIRECT-mt-Dijun"
        group = f"network: {group_name}\nfrequency: {go_freq}\nnetworkId: -1\n"
        if with_identity_snapshot:
            (task / "group.active").write_text(group)
            (task / "connection.active").write_text(
                "groupFormed: true isGroupOwner: true\n")
        if with_owner_intent:
            (task / "owner.intent").write_text(
                f"ssid={owner_ssid}\nfrequency={owner_freq or go_freq}\n")
            (task / "owner.intent").chmod(0o600)
        harness = root / "recover.sh"
        harness.write_text(HARNESS, encoding="utf-8")
        harness.chmod(0o700)
        calls = root / "calls.txt"
        env = os.environ.copy()
        env.update({
            "TASK_DIR": str(task),
            "CALLS": str(calls),
            "GROUP_INFO": group_info,
            "IDLE_OK": "1" if idle_ok else "0",
            "CHECK_RC": str(check_rc),
            "GROUP_QUERY_RC": str(query_rc),
            "CONNECTION_INFO": "groupFormed: true isGroupOwner: true\n",
            "CONNECTION_QUERY_RC": str(connection_rc),
            "SELECT_RC": "0",
            "GO_FREQ": str(go_freq),
        })
        env["SELECT_RC"] = str(select_rc)
        env["REMOVE_RC"] = str(remove_rc)
        env["DEINIT_RC"] = str(deinit_rc)
        env["SAVED_RC"] = str(saved_rc)
        env["EHT_RESTORE_RC"] = str(eht_restore_rc)
        env["JOURNAL_FAIL"] = "1" if journal_fail else "0"
        env["RUN_TWICE"] = "1" if run_twice else "0"
        done = subprocess.run(["/bin/sh", str(harness)], env=env,
                              capture_output=True, text=True, timeout=10)
        return task, calls.read_text() if calls.exists() else "", done

    def test_foreign_group_is_preserved_and_sta_is_only_checked(self):
        task, calls, done = self.run_recovery(
            "network: someone-else\nfrequency: 5180\nnetworkId: 7\n",
            idle_ok=False, check_rc=0)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " check", calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        state = (task / "recovery.resources").read_text()
        self.assertIn("group_state=foreign_or_unknown", state)
        self.assertIn("eht_state=unresolved_p2p_group", state)
        self.assertIn("station_state=verified", state)

    def test_same_ssid_without_matching_saved_identity_is_not_removed(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 5180\nnetworkId: -1\n",
            idle_ok=False, check_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        state = (task / "recovery.resources").read_text()
        self.assertIn("group_state=foreign_or_unknown", state)
        self.assertIn("station_state=deferred_p2p_unresolved", state)

    def test_exact_owned_snapshot_allows_ordered_recovery(self):
        group = "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n"
        task, calls, done = self.run_recovery(
            group, idle_ok=True, check_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("remove-group", calls)
        self.assertIn("deinit", calls)
        self.assertIn("helper " + str(task / "p2p_eht_profile.sh") + " restore", calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " check", calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        state = (task / "recovery.resources").read_text()
        self.assertIn("group_state=deinit_verified", state)
        self.assertIn("eht_state=restored_verified", state)
        self.assertIn("station_state=selected_and_verified", state)

    def test_query_failure_preserves_group_and_checks_sta_read_only(self):
        task, calls, done = self.run_recovery(
            "partial output\n", idle_ok=False, check_rc=0, query_rc=1)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " check", calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        self.assertIn("group_state=query_failed", (task / "recovery.resources").read_text())
        self.assertIn("station_state=verified", (task / "recovery.resources").read_text())

    def test_missing_owner_snapshot_preserves_matching_live_group(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=False, check_rc=0)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("deinit", calls)
        self.assertIn("group_state=foreign_or_unknown", (task / "recovery.resources").read_text())

    def test_unique_owner_intent_allows_recovery_before_active_snapshot(self):
        ssid = "DIRECT-mt-Dijun-6G-deadbeef"
        group = f"network: {ssid}\nfrequency: 6135\nnetworkId: -1\n"
        task, calls, done = self.run_recovery(
            group, idle_ok=True, check_rc=0, with_owner_intent=True,
            owner_ssid=ssid, go_freq=6135)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("p2p get-connection-info", calls)
        self.assertIn("remove-group", calls)
        self.assertIn("deinit", calls)
        self.assertIn("group_state=deinit_verified",
                      (task / "recovery.resources").read_text())

    def test_unique_owner_intent_does_not_claim_different_group(self):
        group = "network: DIRECT-mt-Dijun-6G-feedface\nfrequency: 6135\nnetworkId: -1\n"
        task, calls, done = self.run_recovery(
            group, idle_ok=False, check_rc=0, with_owner_intent=True,
            owner_ssid="DIRECT-mt-Dijun-6G-deadbeef", go_freq=6135)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("deinit", calls)
        self.assertIn("group_state=foreign_or_unknown",
                      (task / "recovery.resources").read_text())

    def test_owner_ssid_substring_or_unrelated_field_cannot_claim_group(self):
        ssid = "DIRECT-mt-Dijun-deadbeef"
        for network in (ssid + "extra", "prefix-" + ssid, "foreign\nnote: " + ssid):
            with self.subTest(network=network):
                group = f"network: {network}\nfrequency: 2412\nnetworkId: -1\n"
                task, calls, done = self.run_recovery(
                    group, idle_ok=False, check_rc=0,
                    with_owner_intent=True, owner_ssid=ssid)
                self.assertEqual(done.returncode, 1)
                self.assertNotIn("remove-group", calls)
                self.assertNotIn("deinit", calls)

    def test_owner_intent_connection_owner_check_failure_preserves_group(self):
        ssid = "DIRECT-mt-Dijun-deadbeef"
        group = f"network: {ssid}\nfrequency: 2412\nnetworkId: -1\n"
        task, calls, done = self.run_recovery(
            group, idle_ok=False, check_rc=0, connection_rc=1,
            with_owner_intent=True, owner_ssid=ssid)
        self.assertEqual(done.returncode, 1)
        self.assertIn("p2p get-connection-info", calls)
        self.assertNotIn("remove-group", calls)
        self.assertNotIn("deinit", calls)

    def test_owned_group_idle_failure_blocks_global_restore_and_reselection(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=False, check_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("remove-group", calls)
        self.assertNotIn("deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " check", calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        self.assertIn("eht_state=unresolved_p2p_group", (task / "recovery.resources").read_text())

    def test_saved_group_snapshot_failure_blocks_deinit_and_global_changes(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, saved_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        self.assertIn("group_state=saved_groups_unverified", (task / "recovery.resources").read_text())

    def test_deinit_failure_blocks_eht_restore_and_station_reselection(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, deinit_rc=1, check_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        state = (task / "recovery.resources").read_text()
        self.assertIn("group_state=deinit_unverified", state)
        self.assertIn("eht_state=unresolved_p2p_group", state)

    def test_remove_failure_with_live_group_report_blocks_deinit_and_globals(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, remove_rc=1, check_rc=1,
            with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("p2p get-group-info", calls)
        self.assertNotIn("p2p deinit", calls)
        self.assertNotIn("helper " + str(task / "p2p_eht_profile.sh"), calls)
        self.assertNotIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        state = (task / "recovery.resources").read_text()
        self.assertIn("group_state=group_teardown_unverified", state)
        self.assertIn("station_state=deferred_p2p_unresolved", state)

    def test_eht_restore_failure_is_recorded_and_sta_reselection_can_finish(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, check_rc=1, eht_restore_rc=1,
            with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("helper " + str(task / "p2p_eht_profile.sh") + " restore", calls)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        self.assertIn("eht_state=restore_failed", (task / "recovery.resources").read_text())

    def test_owned_6ghz_go_is_removed_before_restore(self):
        group = "network: DIRECT-mt-Dijun-6G\nfrequency: 6135\nnetworkId: -1\n"
        task, calls, done = self.run_recovery(
            group, idle_ok=True, check_rc=0, with_identity_snapshot=True, go_freq=6135)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("remove-group", calls)
        self.assertIn("group_state=deinit_verified", (task / "recovery.resources").read_text())

    def test_initial_journal_write_failure_stops_before_commands(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, journal_fail=True, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 74)
        self.assertEqual(calls, "")
        self.assertFalse((task / "recovery.resources").exists())

    def test_station_selection_failure_is_distinctly_recorded(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, check_rc=1, select_rc=1, with_identity_snapshot=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("helper " + str(task / "twt_recover.sh") + " select", calls)
        self.assertIn("station_state=selection_failed", (task / "recovery.resources").read_text())

    def test_recovery_can_be_repeated_after_owned_group_is_removed(self):
        task, calls, done = self.run_recovery(
            "network: DIRECT-mt-Dijun\nfrequency: 2412\nnetworkId: -1\n",
            idle_ok=True, run_twice=True, with_identity_snapshot=True,
            with_eht=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(calls.count("p2p remove-group\n"), 1)
        self.assertEqual(calls.count("p2p get-group-info\n"), 4)
        self.assertEqual(calls.count("helper " + str(task / "twt_recover.sh") + " check\n"), 2)
        self.assertIn("group_state=deinit_verified", (task / "recovery.resources").read_text())


class P2pCreatePathTests(unittest.TestCase):
    def test_create_requires_valid_intent_and_reports_command_failure(self):
        block = SOURCE[SOURCE.index(': > "$D/init.intent"'):SOURCE.index('flock -u 9', SOURCE.index(': > "$D/init.intent"'))]
        for existing, valid, nonce, create_rc, calls, expected in (
            (False, True, "1234abcd", 0, 1, "0"),
            (True, True, "1234abcd", 0, 1, "0"),
            (True, False, "1234abcd", 0, 0, "1"),
            (False, True, "invalid", 0, 0, "1"),
            (False, True, "1234abcd", 1, 1, "1"),
            (False, True, "1234abcd", 2, 1, "1"),
        ):
            with self.subTest(existing=existing, valid=valid, nonce=nonce, create_rc=create_rc), tempfile.TemporaryDirectory() as td:
                task = Path(td)
                (task / "go.key").write_text("fixture-key")
                if existing:
                    (task / "owner.intent").write_text("fixture")
                stub = '''
set -u
umask 077
D=$1; SSID_BASE=DIRECT-mt-Dijun; SSID=$SSID_BASE; GO_FREQ=2412
load_owner_intent() { OWNER_SSID=DIRECT-mt-Dijun-1234abcd; return VALID; }
od() { printf '%s' NONCE; }
not_failed() { ! grep -q FAILED "$1"; }
p2p() {
    if [ "$1" = create-group-with-config ]; then
        [ -f "$D/owner.intent" ] || exit 99
        printf 'create\\n' >> "$D/calls"
        if [ CREATE_RC = 2 ]; then echo FAILED; return 0; fi
        return CREATE_RC
    fi
    return 0
}
'''.replace("VALID", "0" if valid else "1").replace("NONCE", nonce).replace("CREATE_RC", str(create_rc))
                done = subprocess.run(["sh", "-c", stub + block + '\nprintf "%s" "$action_rc"', "sh", td], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, expected)
                call_file = task / "calls"
                self.assertEqual(len(call_file.read_text().splitlines()) if call_file.exists() else 0, calls)


if __name__ == "__main__":
    unittest.main()
