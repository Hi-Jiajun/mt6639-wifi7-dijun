#!/usr/bin/env python3
"""Offline, advisory-only T02 recovery decisions. Never executes a recovery.

Inputs are assertions from a future journal writer/observer, not proof of
durability, exclusion or process fencing. Do not execute cached advice.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys


class InputError(ValueError):
    """Invalid evidence schema. Messages never include raw input values."""


MAX_FILE_BYTES = 2 * 1024 * 1024
TARGET = '/odm/firmware/wifi.cfg'
TOKEN = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)\Z')
MOUNT_KEYS = {'mount_id', 'major_minor', 'root', 'mount_point',
              'stat_dev', 'stat_ino', 'sha256'}
SETTINGS = {'wifi_enabled', 'scan_always_enabled', 'country_override'}


def require(condition):
    if not condition:
        raise InputError('invalid_evidence_schema')


def fields(obj, keys):
    require(isinstance(obj, dict) and set(obj) == set(keys))


def nonempty(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096
            and not any(ord(c) < 32 for c in value))


def identifier(value):
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def process_identity(obj):
    fields(obj, {'pid', 'starttime'})
    require(type(obj['pid']) is int and obj['pid'] > 0)
    require(isinstance(obj['starttime'], str)
            and DECIMAL.fullmatch(obj['starttime']) is not None)
    return obj['pid'], obj['starttime']


def process_list(items):
    require(isinstance(items, list) and len(items) <= 65536)
    identities = [process_identity(item) for item in items]
    # Two records with the same PID cannot be a single coherent inventory.
    require(len({pid for pid, _ in identities}) == len(identities))
    return set(identities)


def mount_identity(value):
    fields(value, MOUNT_KEYS)
    require(type(value['mount_id']) is int and value['mount_id'] > 0)
    require(isinstance(value['major_minor'], str)
            and re.fullmatch(r'(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*)',
                             value['major_minor']) is not None)
    for key in ['root', 'mount_point']:
        require(nonempty(value[key]) and value[key].startswith('/'))
    for key in ['stat_dev', 'stat_ino']:
        require(isinstance(value[key], str)
                and DECIMAL.fullmatch(value[key]) is not None)
    require(value['stat_ino'] != '0')
    require(isinstance(value['sha256'], str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None)


def setting_value(name, value):
    require(isinstance(value, str))
    if name == 'country_override':
        require(value == 'null' or re.fullmatch('[A-Z]{2}', value) is not None)
    else:
        require(value in {'0', '1'})


def validate_journal(journal):
    fields(journal, {'schema', 'transaction', 'phase', 'boot_id', 'mount_namespace',
                     'owner', 'children', 'operations'})
    require(type(journal['schema']) is int and journal['schema'] == 1)
    require(identifier(journal['transaction']))
    require(isinstance(journal['phase'], str) and journal['phase'] in {'open', 'closed'})
    require(nonempty(journal['boot_id']) and nonempty(journal['mount_namespace']))
    owner = process_identity(journal['owner'])
    children = process_list(journal['children'])
    require(all(pid != owner[0] for pid, _ in children))
    operations = journal['operations']
    require(isinstance(operations, list) and 1 <= len(operations) <= 128)
    ids = set()
    for op in operations:
        fields(op, {'id', 'kind', 'state', 'before', 'after'})
        require(identifier(op['id']) and op['id'] not in ids)
        ids.add(op['id'])
        require(isinstance(op['state'], str) and op['state'] in {'intent', 'applied', 'restored'})
        if journal['phase'] == 'closed':
            require(op['state'] == 'restored')
        if op['kind'] == 'setting':
            require(op['id'] in SETTINGS)
            setting_value(op['id'], op['before'])
            setting_value(op['id'], op['after'])
        elif op['kind'] == 'mount':
            require(op['id'] == 'wifi_cfg')
            mount_identity(op['before'])
            point = op['before']['mount_point']
            # Preflight forbids an existing exact-target mount; a bind must
            # acquire a new mount ID, never claim the original partition mount.
            require(point != TARGET and TARGET.startswith(point.rstrip('/') + '/'))
            if op['after'] is None:
                # A verified never-applied intent can be closed/restored
                # without inventing an observed post-mount identity.
                require(op['state'] in {'intent', 'restored'})
            else:
                mount_identity(op['after'])
                require(op['after']['mount_point'] == TARGET)
                require(op['after']['mount_id'] != op['before']['mount_id'])
        else:
            raise InputError('invalid_evidence_schema')
        require(op['before'] != op['after'])
    return {owner} | children


def validate_snapshot(snapshot):
    fields(snapshot, {'schema', 'transaction', 'boot_id_before', 'boot_id_after',
                      'mount_namespace_before', 'mount_namespace_after',
                      'exclusive_window', 'lock_acquired', 'process_inventory_complete',
                      'processes', 'resources'})
    require(type(snapshot['schema']) is int and snapshot['schema'] == 1)
    require(identifier(snapshot['transaction']))
    for name in ['boot_id_before', 'boot_id_after', 'mount_namespace_before',
                 'mount_namespace_after']:
        require(nonempty(snapshot[name]))
    for name in ['exclusive_window', 'lock_acquired', 'process_inventory_complete']:
        require(type(snapshot[name]) is bool)
    require(isinstance(snapshot['resources'], dict))
    return process_list(snapshot['processes'])


def resource_decision(op, evidence):
    result = {'id': op['id'], 'decision': 'blocked', 'reason': 'EVIDENCE_UNAVAILABLE'}
    if not isinstance(evidence, dict) or set(evidence) != {'status', 'value'}:
        return result
    if evidence['status'] != 'ok':
        return result
    current = evidence['value']
    try:
        if op['kind'] == 'mount':
            mount_identity(current)
        else:
            setting_value(op['id'], current)
    except InputError:
        return result
    if current == op['before']:
        result.update(decision='already_baseline', reason='BASELINE_MATCH')
    elif op['state'] == 'restored':
        result['reason'] = 'RESTORED_RECORD_CONFLICT'
    elif op['state'] == 'intent' or op['after'] is None:
        result['reason'] = 'OWNERSHIP_UNCONFIRMED'
    elif current == op['after']:
        result.update(decision='restore_required', reason='AFTER_MATCH')
    else:
        result['reason'] = 'EXTERNAL_CONFLICT'
    return result


def plan_recovery(journal, snapshot):
    """Return non-executable advice for validated, caller-supplied observations.

    A clean result covers recorded resources only. It proves neither that the
    input producer is truthful nor that WLAN/L3 service has been restored.
    """
    owned = validate_journal(journal)
    live = validate_snapshot(snapshot)
    result = dict(schema=1, transaction=journal['transaction'], status='blocked',
                  execution_authorized=False, reasons=[], resources=[])
    reasons = result['reasons']
    if snapshot['transaction'] != journal['transaction']:
        reasons.append('TRANSACTION_MISMATCH')
    if not snapshot['boot_id_before'] == snapshot['boot_id_after'] == journal['boot_id']:
        reasons.append('BOOT_CHANGED')
    if not (snapshot['mount_namespace_before'] == snapshot['mount_namespace_after']
            == journal['mount_namespace']):
        reasons.append('MOUNT_NAMESPACE_CHANGED')
    for name, code in [('exclusive_window', 'EXCLUSIVE_WINDOW_REQUIRED'),
                       ('lock_acquired', 'LOCK_REQUIRED'),
                       ('process_inventory_complete', 'PROCESS_INVENTORY_INCOMPLETE')]:
        if not snapshot[name]:
            reasons.append(code)
    if owned & live:
        reasons.append('OWNED_PROCESS_ALIVE')
    expected = {op['id'] for op in journal['operations']}
    if set(snapshot['resources']) - expected:
        reasons.append('UNEXPECTED_RESOURCE')
    if reasons:
        return result
    result['resources'] = [resource_decision(op, snapshot['resources'].get(op['id']))
                           for op in journal['operations']]
    if any(r['decision'] == 'blocked' for r in result['resources']):
        reasons.append('RESOURCE_BLOCKED')
    elif any(r['decision'] == 'restore_required' for r in result['resources']):
        result['status'] = 'restore_required'
    elif journal['phase'] != 'closed':
        reasons.append('JOURNAL_OPEN')
    else:
        result['status'] = 'clean'
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _invalid_constant(_):
    raise InputError('invalid_json_constant')


def read_json(path):
    # Nonblocking open avoids a named pipe masquerading as a local evidence file.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        data = stream.read(MAX_FILE_BYTES + 1)
    require(len(data) <= MAX_FILE_BYTES)
    return json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def invalid_result():
    return dict(schema=1, status='invalid_input', execution_authorized=False,
                reasons=['INVALID_INPUT'], resources=[])


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps(invalid_result(), sort_keys=True))
        raise SystemExit(64)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--journal', required=True)
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args(argv)
    try:
        result = plan_recovery(read_json(args.journal), read_json(args.snapshot))
    except (InputError, OSError, ValueError, TypeError, RecursionError):
        print(json.dumps(invalid_result(), sort_keys=True))
        return 64
    print(json.dumps(result, sort_keys=True))
    return {'clean': 0, 'restore_required': 2, 'blocked': 3}[result['status']]


if __name__ == '__main__':
    sys.exit(main())
