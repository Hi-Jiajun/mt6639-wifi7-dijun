"""Tests for the host-side ADB wrapper around the read-only release preflight."""
from pathlib import Path
import json
import contextlib
import importlib.util
import io
from unittest import mock
import os
import subprocess
import sys
import tempfile
import unittest

import pubenv

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools/run_release_device_preflight.py"

BOOT = '11111111-1111-4111-8111-111111111111'
COMPLETE_TSV = 'PASS\troot_uid\tok\t\nPASS\tselinux\tok\t\nPASS\troot_context\tok\t\nPASS\tfingerprint\tok\t\nPASS\tkernelsu_manager\tok\t\nPASS\tkernelsu\tok\t\nPASS\tkernelsu_su_compat\tok\t\nPASS\tkernelsu_su_context\tok\t\nPASS\txringroot_tool\tok\t\nPASS\tmodule_mt6639-6g\tok\t\nPASS\tmodule_mt6639-hotspot\tok\t\nPASS\tmodule_mt6639-bt\tok\t\nPASS\tmodule_mt6639-6g_enabled\tok\t\nPASS\tmodule_mt6639-hotspot_enabled\tok\t\nPASS\tmodule_mt6639-bt_enabled\tok\t\nPASS\tbt_host_profiles\tok\t\nPASS\twifi_home_net_6g\tok\t\nPASS\tmtap0\tok\t\nPASS\ttethering\tok\t\nPASS\tui_lsposed\tok\t\nPASS\tconcurrency_gate\tok\t\nPASS\tconcurrency_gate_marker\tok\t\nPASS\tzygisk_next\tok\t\nPASS\tlsposed\tok\t\nINFO\tboot\t11111111-1111-4111-8111-111111111111\t\n'
from test_stage_6g_candidate import STAGE  # Load the shared transport module.
SPEC = importlib.util.spec_from_file_location('preflight_runner', TOOL)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

FAKE_ADB = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
cmd = args[-1]
if 'mktemp -d' in cmd:
    print('/data/local/tmp/mt6639-release-preflight-abc123')
elif 'push' in args:
    pass
elif cmd.startswith('chmod 0700 '):
    pass
elif cmd == 'id -u':
    print('0')
elif cmd.startswith('sh '):
    pass
elif cmd.startswith('cat '):
    print('PASS\\troot_uid\\tok\\t\\nPASS\\tselinux\\tok\\t\\nPASS\\troot_context\\tok\\t\\nPASS\\tfingerprint\\tok\\t\\nPASS\\tkernelsu_manager\\tok\\t\\nPASS\\tkernelsu\\tok\\t\\nPASS\\tkernelsu_su_compat\\tok\\t\\nPASS\\tkernelsu_su_context\\tok\\t\\nPASS\\txringroot_tool\\tok\\t\\nPASS\\tmodule_mt6639-6g\\tok\\t\\nPASS\\tmodule_mt6639-hotspot\\tok\\t\\nPASS\\tmodule_mt6639-bt\\tok\\t\\nPASS\\tmodule_mt6639-6g_enabled\\tok\\t\\nPASS\\tmodule_mt6639-hotspot_enabled\\tok\\t\\nPASS\\tmodule_mt6639-bt_enabled\\tok\\t\\nPASS\\tbt_host_profiles\\tok\\t\\nPASS\\twifi_home_net_6g\\tok\\t\\nPASS\\tmtap0\\tok\\t\\nPASS\\ttethering\\tok\\t\\nPASS\\tui_lsposed\\tok\\t\\nPASS\\tconcurrency_gate\\tok\\t\\nPASS\\tconcurrency_gate_marker\\tok\\t\\nPASS\\tzygisk_next\\tok\\t\\nPASS\\tlsposed\\tok\\t\\nINFO\\tboot\\t11111111-2222-3333-4444-555555555555\\t\\n', end='')
else:
    sys.exit(2)
"""


class ReleaseDevicePreflightRunnerTests(unittest.TestCase):
    def test_successful_fake_adb_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(FAKE_ADB, encoding="utf-8")
            adb.chmod(0o755)
            out = root / "out"
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--allow-start-adb",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            run = json.loads((out / "run.json").read_text())
            results = (out / "results.tsv").read_text()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(run["status"], "complete")
        self.assertTrue(run["read_only_preflight"])
        self.assertEqual(run["boot_id"], "11111111-2222-3333-4444-555555555555")
        self.assertIsNone(run["failure_reason"])
        self.assertIn("PASS\troot_uid", results)

    def test_unchanged_previous_boot_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(FAKE_ADB, encoding="utf-8")
            adb.chmod(0o755)
            out = root / "out"
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--allow-start-adb",
                    "--previous-boot-id",
                    "11111111-2222-3333-4444-555555555555",
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            run = json.loads((out / "run.json").read_text())
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failure_reason"], "boot_id_unchanged")
        self.assertFalse(run["fresh_boot"])

    def test_unsafe_remote_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(
                FAKE_ADB.replace(
                    "/data/local/tmp/mt6639-release-preflight-abc123",
                    "/tmp/not-allowed",
                ),
                encoding="utf-8",
            )
            adb.chmod(0o755)
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--allow-start-adb",
                    "--out",
                    str(root / "out"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unsafe remote path", done.stderr)

    def test_missing_daemon_does_not_start_adb(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adb = root / "adb"
            adb.write_text(FAKE_ADB, encoding="utf-8")
            adb.chmod(0o755)
            done = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--adb",
                    str(adb),
                    "--serial",
                    "SERIAL",
                    "--adb-port",
                    "59876",
                    "--out",
                    str(root / "out"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("adb daemon is not already listening", done.stderr)

@unittest.skipUnless(pubenv.candidate_ready(), pubenv.SKIP_RELEASE_ASSETS)
class PreflightSSHTests(unittest.TestCase):
    def setUp(self):
        from test_stage_6g_candidate_ssh import SSHStageTests, DISPATCH
        SSHStageTests.setUp(self)
        dispatcher = DISPATCH.replace(".replace('/system/bin/sh','/bin/sh')", ".replace('/system/bin/sh',str(root/'bin/sh'))")
        dispatcher = dispatcher.replace("ksu=os.environ.get('IN_KSU')=='1'", r'''
if name=='sh' and args[:1]==['-c']:
 if args[1].startswith('#!'):
  if os.environ.get('IN_KSU')!='1': fail()
  log('preflight',normal(args[-1]))
  target=pathlib.Path(args[-1]); target.mkdir(parents=True,exist_ok=True)
  if not os.environ.get('NO_RESULTS'): (target/'results.tsv').write_text(os.environ['PREFLIGHT_TSV'])
  if os.environ.get('PREFLIGHT_EXTRA_STDOUT'): print(os.environ['PREFLIGHT_EXTRA_STDOUT'])
  sys.exit(int(os.environ.get('PREFLIGHT_RC','0')))
 sys.exit(subprocess.call(['/bin/sh']+args))
ksu=os.environ.get('IN_KSU')=='1'
''')
        dispatcher = dispatcher.replace('mt6639-stage-AAAAAA','mt6639-release-preflight-AAAAAA')
        dispatcher = dispatcher.replace("else: fail()\nelif name=='mktemp':", "elif args and args[0].endswith('/results.tsv'): sys.stdout.write(pathlib.Path(args[0]).read_text())\n else: fail()\nelif name=='mktemp':")
        (self.root/'dispatch').write_text(dispatcher)
        self.env.update(PREFLIGHT_TSV=COMPLETE_TSV)
        self.out=self.root/'out'

    def records(self):
        from test_stage_6g_candidate_ssh import SSHStageTests
        return SSHStageTests.records(self)

    def run_preflight(self, extra=(), **env):
        from test_stage_6g_candidate_ssh import SERIAL
        stdout,stderr=io.StringIO(),io.StringIO()
        with mock.patch.dict(os.environ,dict(self.env,**env),clear=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc=RUNNER.main(['--ssh-command-json',str(self.config),'--serial',SERIAL,'--out',str(self.out),*extra])
        return rc,json.loads((self.out/'run.json').read_text()),stdout.getvalue()+stderr.getvalue()

    def test_complete_uses_shared_real_gate_and_fixed_script(self):
        rc,run,_=self.run_preflight()
        self.assertEqual(rc,0)
        self.assertEqual(run['execution_mode'],'ssh_app_ksu')
        self.assertIsNone(run['adb_returncode'])
        self.assertEqual(run['preflight_returncode'],0)
        self.assertEqual(run['boot_id'],BOOT)
        self.assertEqual(run['script_size_bytes'],RUNNER.SCRIPT.stat().st_size)
        self.assertEqual(len(run['script_sha256']),64)
        self.assertFalse(any(r[0]=='bare-root' for r in self.records()))
        self.assertEqual(sum(r[0]=='preflight' for r in self.records()),1)
        self.assertEqual(sum(r[0]=='ssh' for r in self.records()),4)

    def test_permissive_and_old_versions_collect_failed_evidence(self):
        tsv=COMPLETE_TSV.replace('PASS\tselinux\tok','FAIL\tselinux\tPermissive').replace('PASS\tmodule_mt6639-6g\tok','FAIL\tmodule_mt6639-6g\tv0.2.6')
        rc,run,_=self.run_preflight(SELINUX='Permissive',ENFORCE='0',PREFLIGHT_TSV=tsv,PREFLIGHT_RC='1')
        self.assertEqual(rc,1)
        self.assertEqual(run['failure_reason'],'preflight_failed')
        self.assertEqual(run['preflight_returncode'],1)
        self.assertEqual(run['failed_rows'],2)
        self.assertEqual((self.out/'results.tsv').read_text(),tsv)

    def test_wrong_identity_serial_boot_stops_before_remote_report(self):
        for change in ({'SOURCE_UID':'0'},{'SOURCE_UID':'2000'},{'SOURCE_CTX':'u:r:kernel:s0'},{'ROOT_CTX':'u:r:kernel:s0'},{'SERIAL':'wrong'},{'BOOT':'bad'},{'BOOT_SWITCH':'3'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                self.out=Path(td)/'out'
                for name in ('log','boot-reads'):
                    p=self.root/name
                    if p.exists(): p.unlink()
                rc,run,_=self.run_preflight(**change)
                self.assertEqual(rc,1)
                self.assertEqual(run['status'],'failed')
                self.assertFalse(any(r[0]=='preflight' for r in self.records()))

    def test_timeout_and_connection_failure_are_unconfirmed_no_retry_redacted(self):
        for change in ({'TIMEOUT':'1'},{'SSH_FAIL':'1'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                self.out=Path(td)/'out'
                log=self.root/'log'
                if log.exists(): log.unlink()
                rc,run,output=self.run_preflight(extra=['--timeout','1'],**change)
                self.assertEqual(rc,1)
                self.assertEqual(run['status'],'unconfirmed')
                self.assertEqual(run['failure_reason'],'SSH_STATE_UNCONFIRMED')
                self.assertEqual(len(self.records()),1)
                all_output=output+''.join(p.read_text() for p in self.out.rglob('*') if p.is_file())
                self.assertNotIn('SECRET',all_output)
                self.assertNotIn('private-key',all_output)

    def test_invalid_results_do_not_pass(self):
        cases=[(COMPLETE_TSV.replace('PASS\troot_uid','WARN\troot_uid'), 'results_incomplete_or_invalid'),('', 'results_incomplete_or_invalid'),('INFO\tboot\t'+BOOT+'\t\n','results_incomplete_or_invalid'),(COMPLETE_TSV+'PASS\troot_uid\tok\t\n','results_incomplete_or_invalid'),(COMPLETE_TSV.replace('PASS\tlsposed\tok\t\n',''),'results_incomplete_or_invalid'),(COMPLETE_TSV.replace(BOOT,'22222222-2222-4222-8222-222222222222'),'boot_id_transport_mismatch'),(COMPLETE_TSV+'INFO\tboot\t'+BOOT+'\t\n','boot_id_missing_or_ambiguous')]
        for tsv,reason in cases:
            with self.subTest(reason=reason,tsv=tsv[:30]), tempfile.TemporaryDirectory() as td:
                self.out=Path(td)/'out'
                remote=self.root/'android/data/local/tmp/mt6639-release-preflight-AAAAAA'
                if remote.exists():
                    for p in remote.iterdir(): p.unlink()
                    remote.rmdir()
                rc,run,_=self.run_preflight(PREFLIGHT_TSV=tsv)
                self.assertEqual(rc,1)
                self.assertEqual(run['failure_reason'],reason)

    def test_same_boot_does_not_count_as_cold_boot(self):
        rc,run,_=self.run_preflight(extra=['--previous-boot-id',BOOT])
        self.assertEqual(rc,1)
        self.assertEqual(run['failure_reason'],'boot_id_unchanged')
        self.assertFalse(run['fresh_boot'])

    def test_missing_results_is_not_success(self):
        rc,run,_=self.run_preflight(NO_RESULTS='1',PREFLIGHT_RC='74')
        self.assertEqual(rc,1)
        self.assertEqual(run['status'],'unconfirmed')
        self.assertEqual(sum(r[0]=='preflight' for r in self.records()),1)


    def test_cross_boot_before_collection_stops_without_replay(self):
        rc,run,_=self.run_preflight(BOOT_SWITCH='7')
        self.assertEqual(rc,1)
        self.assertEqual(run['failure_reason'],'BOOT_ID_INVALID')
        self.assertEqual(sum(r[0]=='preflight' for r in self.records()),1)
        self.assertEqual(sum(r[0]=='ssh' for r in self.records()),4)
        self.assertFalse((self.out/'results.tsv').exists())

    def test_duplicate_exit_protocol_stops_before_collection(self):
        rc,run,_=self.run_preflight(PREFLIGHT_EXTRA_STDOUT='__MT6639_PREFLIGHT_EXIT__ 0')
        self.assertEqual(rc,1)
        self.assertEqual(run['status'],'unconfirmed')
        self.assertEqual(sum(r[0]=='ssh' for r in self.records()),3)
        self.assertFalse((self.out/'results.tsv').exists())

    def test_nonzero_script_with_all_pass_rows_still_fails(self):
        rc,run,_=self.run_preflight(PREFLIGHT_RC='74')
        self.assertEqual(rc,1)
        self.assertEqual(run['preflight_returncode'],74)
        self.assertEqual(run['failure_reason'],'preflight_failed')
        self.assertEqual((self.out/'results.tsv').read_text(),COMPLETE_TSV)

    def test_bad_config_and_cli_conflicts_start_no_transports(self):
        for extra in (['--adb','/bin/false'],['--adb-port','5038'],['--allow-start-adb']):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_preflight(extra=extra)
        self.config.write_text('["SECRET"]')
        rc,run,output=self.run_preflight()
        self.assertEqual(rc,1)
        self.assertEqual(run['failure_reason'],'SSH_CONFIG_INVALID')
        self.assertNotIn('SECRET',output)
        self.assertEqual(self.records(),[])


class PreflightADBPortTests(unittest.TestCase):
    def test_default_listener_is_pinned_to_every_adb_operation(self):
        for available,expected in (({5037,5038},5037),({5038},5038)):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as td:
                ports=[]
                def connect(address,timeout):
                    if address[1] not in available: raise OSError()
                    return contextlib.nullcontext()
                def adb(executable,serial,port,args,timeout):
                    ports.append(port)
                    stdout='/data/local/tmp/mt6639-release-preflight-abc123' if args[-1].startswith('mktemp') else '0' if args[-1]=='id -u' else COMPLETE_TSV if args[-1].startswith('cat ') else ''
                    return subprocess.CompletedProcess([],0,stdout,'')
                with mock.patch.object(RUNNER.socket,'create_connection',side_effect=connect), mock.patch.object(RUNNER,'resolve_adb',return_value='/fixture/adb'), mock.patch.object(RUNNER,'run_adb',side_effect=adb),contextlib.redirect_stdout(io.StringIO()):
                    rc=RUNNER.main(['--serial','SERIAL','--out',str(Path(td)/'out')])
                self.assertEqual(rc,0)
                self.assertEqual(ports,[expected]*6)


if __name__ == "__main__":
    unittest.main()
