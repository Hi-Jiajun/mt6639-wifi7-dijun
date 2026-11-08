#!/usr/bin/env python3
"""Decode bounded MT6639 TWT logs; submission is never FW periodic execution."""
import argparse
import json
from pathlib import Path
import re


def summarize(text):
    hosts, submits, returns, chains = [], [], [], []
    accept = bool(re.search(r'twtPlannerRxNegoResult[^\n]*Rx nego id 0 resp=4\b', text))
    for line in text.splitlines():
        if 'mttwt_obs: ' not in line:
            continue
        body = line.split('mttwt_obs: ', 1)[1]
        fields = dict(re.findall(r'(\w+)=([0-9a-f]+)', body))
        if body.startswith('host '):
            p, tsf = bytes.fromhex(fields['param10']), bytes.fromhex(fields['tsf8'])
            if len(p) != 10 or len(tsf) != 8 or p[6] > 31:
                raise ValueError('invalid host observation length/exponent')
            h = dict(index=int(fields['idx']), flow=int(fields['flow']),
                     host_copy_equal=fields['equal'] == '1', allocation_nonnull=fields['alloc'] == '1',
                     flags_raw=list(p[:5]), duration_raw=p[5], exponent=p[6],
                     mantissa=int.from_bytes(p[8:10], 'little'),
                     tsf=int.from_bytes(tsf, 'little'))
            h['interval_us'] = h['mantissa'] << h['exponent']
            hosts.append(h)
        elif body.startswith(('submit ', 'return ')):
            c = bytes.fromhex(fields['cmd21'])
            if len(c) != 21 or c[18] > 31:
                raise ValueError('invalid FW observation length/exponent')
            f = dict(index=c[0], control=c[1], flow=c[3], bss=c[7],
                     duration_raw=c[6], tsf=int.from_bytes(c[8:16], 'little'),
                     mantissa=int.from_bytes(c[16:18], 'little'), exponent=c[18],
                     role_bitmap=c[19], parameter_bitmap=c[20], cmd21=c.hex())
            if body.startswith('submit '):
                f.update(length=int(fields['len']), need_response=int(fields['need_resp']))
                submits.append(f)
            else:
                f['return_status_u32'] = int(fields['rc'])
                f['return_status_hex'] = hex(f['return_status_u32'])
                returns.append(f)
                submit = next((s for s in reversed(submits)
                               if s['cmd21'] == f['cmd21'] and not s.get('return_observed')), None)
                if submit is None:
                    continue
                submit['return_observed'] = True
                host = next((h for h in reversed(hosts)
                             if all(h[k] == f[k] for k in ('index', 'flow', 'duration_raw',
                                                          'tsf', 'mantissa', 'exponent'))
                             and h['host_copy_equal'] and h['allocation_nonnull']), None)
                if host is not None:
                    chains.append(dict(index=f['index'], flow=f['flow'],
                                       parameters_consistent=True,
                                       return_status_u32=f['return_status_u32']))
    return dict(accept_flow0_observed=accept, host=hosts, submit=submits, returned=returns,
                aligned_chains=chains, accepted_parameter_submission_observed=accept and bool(chains),
                fw_periodic_execution_verified=False, power_saving_verified=False,
                limitations=['Log capture window/boot must be verified externally.',
                             'Return status is raw driver status, not a firmware commit acknowledgement.',
                             'Duration unit is not inferred from the host structure alone.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    args = p.parse_args()
    print(json.dumps(summarize(args.log.read_text(errors='replace')), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
