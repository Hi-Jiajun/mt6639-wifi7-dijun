"""Offline recovery decisions; fixtures contain no real device/network data."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'tools/plan_mt6639_recovery.py'
if TOOL.exists():
    spec = importlib.util.spec_from_file_location('recovery', TOOL)
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
else:
    recovery = None


def fixture():
    before = dict(mount_id=17, major_minor='253:4', root='/',
                  mount_point='/odm', stat_dev='64772', stat_ino='501',
                  sha256='a' * 64)
    after = dict(mount_id=93, major_minor='253:9', root='/fixture/candidate',
                 mount_point='/odm/firmware/wifi.cfg', stat_dev='64777',
                 stat_ino='901', sha256='b' * 64)
    journal = dict(schema=1, transaction='fixture-01', phase='closed', boot_id='boot-fixture',
                   mount_namespace='mnt:[100]', owner=dict(pid=42, starttime='123'),
                   children=[dict(pid=43, starttime='124')], operations=[
                       dict(id='wifi_cfg', kind='mount', state='restored',
                            before=before, after=after),
                       dict(id='country_override', kind='setting', state='restored',
                            before='null', after='US'),
                   ])
    snapshot = dict(schema=1, transaction='fixture-01',
                    boot_id_before='boot-fixture', boot_id_after='boot-fixture',
                    mount_namespace_before='mnt:[100]', mount_namespace_after='mnt:[100]',
                    exclusive_window=True, lock_acquired=True,
                    process_inventory_complete=True, processes=[], resources={
                        'wifi_cfg': dict(status='ok', value=copy.deepcopy(before)),
                        'country_override': dict(status='ok', value='null'),
                    })
    return journal, snapshot


class RecoveryPlanTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(recovery, 'missing recovery planner implementation')
        self.j, self.s = fixture()

    def plan(self, status):
        before = copy.deepcopy((self.j, self.s))
        result = recovery.plan_recovery(self.j, self.s)
        self.assertEqual(result['status'], status)
        self.assertIs(result['execution_authorized'], False)
        self.assertEqual((self.j, self.s), before, 'planner mutated caller input')
        return result

    def test_baseline_is_clean_without_repeated_restoration(self):
        result = self.plan('clean')
        self.assertEqual([r['decision'] for r in result['resources']], ['already_baseline'] * 2)

    def test_full_after_identity_proposes_restore_not_execution(self):
        self.j['phase'] = 'open'
        for op in self.j['operations']:
            op['state'] = 'applied'
            self.s['resources'][op['id']]['value'] = copy.deepcopy(op['after'])
        result = self.plan('restore_required')
        self.assertEqual([r['decision'] for r in result['resources']], ['restore_required'] * 2)

    def test_external_setting_and_same_hash_foreign_mount_block(self):
        self.s['resources']['country_override']['value'] = 'JP'
        self.plan('blocked')
        self.s['resources']['country_override']['value'] = 'null'
        self.s['resources']['wifi_cfg']['value'] = copy.deepcopy(self.j['operations'][0]['after'])
        for key, foreign in [('stat_ino', '902'), ('mount_id', 94),
                             ('root', '/foreign'), ('major_minor', '253:10')]:
            with self.subTest(key=key):
                original = self.s['resources']['wifi_cfg']['value'][key]
                self.s['resources']['wifi_cfg']['value'][key] = foreign
                self.plan('blocked')
                self.s['resources']['wifi_cfg']['value'][key] = original

    def test_intent_crash_windows_do_not_guess_mount_ownership(self):
        self.j['phase'] = 'open'
        op = self.j['operations'][0]
        op['state'] = 'intent'
        after = op['after']
        op['after'] = None
        result = self.plan('blocked')  # resource baseline is not a closed transaction
        self.assertEqual(result['reasons'], ['JOURNAL_OPEN'])
        self.s['resources']['wifi_cfg']['value'] = after
        self.plan('blocked')  # side effect possible, actual mount identity unrecorded
        op['after'] = after
        self.plan('blocked')  # intent is not confirmation that this writer applied it
        op['state'] = 'applied'
        self.plan('restore_required')

    def test_intended_setting_does_not_prove_ownership_even_with_global_gates(self):
        self.j['phase'] = 'open'
        self.j['operations'][1]['state'] = 'intent'
        self.s['resources']['country_override']['value'] = 'US'
        result = self.plan('blocked')
        self.assertEqual(result['resources'][1]['reason'], 'OWNERSHIP_UNCONFIRMED')
        self.s['exclusive_window'] = False
        self.plan('blocked')

    def test_boot_namespace_and_transaction_changes_block(self):
        for key, value in [('boot_id_before', 'other-boot'), ('boot_id_after', 'other-boot'),
                           ('mount_namespace_before', 'mnt:[200]'),
                           ('mount_namespace_after', 'mnt:[200]'),
                           ('transaction', 'other-txn')]:
            with self.subTest(key=key):
                old = self.s[key]
                self.s[key] = value
                self.plan('blocked')
                self.s[key] = old
        self.s['boot_id_before'] = self.s['boot_id_after'] = 'new-boot'
        self.plan('blocked')  # baseline values alone do not prove cross-boot recovery

    def test_missing_or_false_global_gates_never_mean_quiescent(self):
        for key in ['exclusive_window', 'lock_acquired', 'process_inventory_complete']:
            with self.subTest(key=key):
                self.s[key] = False
                self.plan('blocked')
                del self.s[key]
                with self.assertRaises(recovery.InputError):
                    recovery.plan_recovery(self.j, self.s)
                self.s[key] = True

    def test_live_owner_or_child_blocks_but_reused_pid_is_not_killed(self):
        for process in [self.j['owner'], *self.j['children']]:
            self.s['processes'] = [copy.deepcopy(process)]
            self.plan('blocked')
            self.s['processes'][0]['starttime'] = '999'
            self.plan('clean')

    def test_restored_record_does_not_overwrite_later_changes(self):
        self.j['operations'][0]['state'] = 'restored'
        self.plan('clean')
        self.s['resources']['wifi_cfg']['value'] = copy.deepcopy(self.j['operations'][0]['after'])
        self.plan('blocked')

    def test_failed_empty_and_missing_resource_evidence_block(self):
        for status, value in [('failed', 'null'), ('empty', None), ('timeout', 'US'),
                              ('ok', None), ('ok', {}), ('unknown', None)]:
            with self.subTest(status=status, value=value):
                self.s['resources']['country_override'] = dict(status=status, value=value)
                self.plan('blocked')
        del self.s['resources']['country_override']
        self.plan('blocked')

    def test_malformed_and_ambiguous_logs_are_rejected(self):
        mutations = [
            lambda j: j.update(phase='guess'),
            lambda j: j.update(schema=True),
            lambda j: j.update(schema=2),
            lambda j: j.update(transaction='bad / private'),
            lambda j: j['owner'].update(pid=True),
            lambda j: j['owner'].update(starttime=123),
            lambda j: j['children'].append(copy.deepcopy(j['owner'])),
            lambda j: j['operations'].append(copy.deepcopy(j['operations'][0])),
            lambda j: j['operations'][0].update(kind='command'),
            lambda j: j['operations'][0].update(state='guess'),
            lambda j: (j.update(phase='open'), j['operations'][0].update(state='applied', after=None)),
            lambda j: j['operations'][0]['after'].update(sha256='bad'),
            lambda j: j['operations'][1].update(before=None),
            lambda j: j['operations'][1].update(after='null'),
            lambda j: j['operations'][1].update(id='arbitrary_setting'),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                j = copy.deepcopy(self.j)
                mutate(j)
                with self.assertRaises(recovery.InputError):
                    recovery.plan_recovery(j, self.s)

    def test_boolean_numeric_process_and_snapshot_types_are_rejected(self):
        for key, val in [('schema', True), ('processes', None),
                         ('lock_acquired', 1), ('resources', [])]:
            with self.subTest(key=key):
                self.s[key] = val
                with self.assertRaises(recovery.InputError):
                    recovery.plan_recovery(self.j, self.s)
                self.j, self.s = fixture()

    def test_setting_switch_values_are_not_truthiness_cast(self):
        self.j['operations'] = [dict(id='wifi_enabled', kind='setting', state='restored',
                                    before='0', after='1')]
        self.s['resources'] = {'wifi_enabled': dict(status='ok', value='0')}
        self.plan('clean')
        self.j['phase'] = 'open'
        self.j['operations'][0]['state'] = 'applied'
        self.s['resources']['wifi_enabled']['value'] = '1'
        self.plan('restore_required')
        self.j['operations'][0]['before'] = 'off'
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)

    def test_global_conflicts_do_not_offer_partial_restore_advice(self):
        self.s['resources']['country_override']['value'] = 'US'
        self.s['processes'] = [copy.deepcopy(self.j['owner'])]
        result = self.plan('blocked')
        self.assertEqual(result['resources'], [])
        self.assertEqual(result['reasons'], ['OWNED_PROCESS_ALIVE'])

    def test_resource_conflict_blocks_transaction_even_if_another_needs_restore(self):
        self.j['phase'] = 'open'
        self.j['operations'][0]['state'] = 'applied'
        self.s['resources']['wifi_cfg']['value'] = copy.deepcopy(self.j['operations'][0]['after'])
        self.s['resources']['country_override']['value'] = 'JP'
        result = self.plan('blocked')
        self.assertEqual(result['reasons'], ['RESOURCE_BLOCKED'])
        self.assertEqual([r['decision'] for r in result['resources']], ['restore_required', 'blocked'])

    def test_inventory_duplicate_pid_and_extra_resource_fail_closed(self):
        self.s['resources']['external_setting'] = dict(status='ok', value='0')
        result = self.plan('blocked')
        self.assertEqual(result['reasons'], ['UNEXPECTED_RESOURCE'])
        del self.s['resources']['external_setting']
        self.s['processes'] = [dict(pid=42, starttime='123'), dict(pid=42, starttime='999')]
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)

    def test_empty_journal_cannot_vacuously_claim_recovery(self):
        self.j['operations'] = []
        self.s['resources'] = {}
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)

    def test_unclosed_baseline_requires_separate_close_and_verification(self):
        self.j['phase'] = 'open'
        result = self.plan('blocked')
        self.assertEqual(result['reasons'], ['JOURNAL_OPEN'])
        self.assertEqual([r['decision'] for r in result['resources']], ['already_baseline'] * 2)
        self.j['phase'] = 'closed'
        self.plan('clean')

    def test_closed_journal_with_unrestored_operation_is_inconsistent(self):
        self.j['operations'][0]['state'] = 'applied'
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)

    def test_verified_never_applied_mount_can_close_without_fabricated_after(self):
        self.j['operations'][0]['after'] = None
        self.plan('clean')  # closed/restored declaration, verified baseline, no bind happened
        self.s['resources']['wifi_cfg']['value'] = copy.deepcopy(fixture()[0]['operations'][0]['after'])
        self.plan('blocked')

    def test_namespace_is_opaque_not_numeric_normalized(self):
        self.s['mount_namespace_before'] = self.s['mount_namespace_after'] = '100'
        result = self.plan('blocked')
        self.assertEqual(result['reasons'], ['MOUNT_NAMESPACE_CHANGED'])

    def test_mount_journal_cannot_claim_partition_mount_as_owned_bind(self):
        op = self.j['operations'][0]
        op['after']['mount_id'] = op['before']['mount_id']
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)
        op['after']['mount_id'] = 93
        op['before']['mount_point'] = '/odm/firmware/wifi.cfg'
        with self.assertRaises(recovery.InputError):
            recovery.plan_recovery(self.j, self.s)


class RecoveryCliTest(unittest.TestCase):
    def run_cli(self, j, s):
        with tempfile.TemporaryDirectory() as d:
            journal, snapshot = Path(d) / 'journal.json', Path(d) / 'snapshot.json'
            journal.write_text(json.dumps(j))
            snapshot.write_text(json.dumps(s))
            return subprocess.run([sys.executable, str(TOOL), '--journal', str(journal),
                                   '--snapshot', str(snapshot)], capture_output=True, text=True,
                                  timeout=5)

    def test_cli_status_exit_codes_and_no_sensitive_values(self):
        self.assertTrue(TOOL.exists(), 'missing recovery planner CLI')
        j, s = fixture()
        for status, code, value in [('clean', 0, 'null'), ('restore_required', 2, 'US'),
                                     ('blocked', 3, 'JP')]:
            if status != 'clean':
                j['phase'] = 'open'
                j['operations'][1]['state'] = 'applied'
            s['resources']['country_override']['value'] = value
            proc = self.run_cli(j, s)
            self.assertEqual(proc.returncode, code, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result['status'], status)
            self.assertIs(result['execution_authorized'], False)
            self.assertNotIn('/fixture/candidate', proc.stdout + proc.stderr)
            self.assertNotIn('"US"', proc.stdout + proc.stderr)

    def test_invalid_file_content_returns_64_without_echoing_input(self):
        self.assertTrue(TOOL.exists(), 'missing recovery planner CLI')
        j, s = fixture()
        j['transaction'] = 'PRIVATE_SECRET/DO_NOT_PRINT'
        proc = self.run_cli(j, s)
        self.assertEqual(proc.returncode, 64)
        self.assertNotIn('PRIVATE_SECRET', proc.stdout + proc.stderr)
        self.assertIs(json.loads(proc.stdout)['execution_authorized'], False)

    def test_ambiguous_or_invalid_json_is_rejected_without_traceback(self):
        self.assertTrue(TOOL.exists(), 'missing recovery planner CLI')
        with tempfile.TemporaryDirectory() as d:
            jpath, spath = Path(d) / 'journal.json', Path(d) / 'snapshot.json'
            j, s = fixture()
            spath.write_text(json.dumps(s))
            payloads = ['{"schema":1,"schema":2}', 'NaN', '{not-json',
                        json.dumps(j) + ' private-tail', 'x' * (2 * 1024 * 1024 + 1)]
            for payload in payloads:
                with self.subTest(size=len(payload)):
                    jpath.write_text(payload)
                    proc = subprocess.run([sys.executable, str(TOOL), '--journal', str(jpath),
                                           '--snapshot', str(spath)], capture_output=True,
                                          text=True, timeout=5)
                    self.assertEqual(proc.returncode, 64)
                    self.assertEqual(json.loads(proc.stdout)['reasons'], ['INVALID_INPUT'])
                    self.assertEqual(proc.stderr, '')

    def test_missing_input_file_and_fifo_are_bounded_input_errors(self):
        self.assertTrue(TOOL.exists(), 'missing recovery planner CLI')
        import os
        with tempfile.TemporaryDirectory() as d:
            fifo = Path(d) / 'fifo'
            os.mkfifo(fifo)
            for path in [Path(d) / 'does-not-exist', fifo, Path(d)]:
                proc = subprocess.run([sys.executable, str(TOOL), '--journal', str(path),
                                       '--snapshot', str(path)], capture_output=True,
                                      text=True, timeout=5)
                self.assertEqual(proc.returncode, 64)
                self.assertEqual(json.loads(proc.stdout)['status'], 'invalid_input')
                self.assertEqual(proc.stderr, '')


if __name__ == '__main__':
    unittest.main()
