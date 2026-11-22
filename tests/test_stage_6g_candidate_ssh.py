"""Offline SSH transport regressions: execute the generated gate in a host fixture."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import pubenv
from test_stage_6g_candidate import STAGE, MANIFEST, REPO

SERIAL = 'YOUR_SERIAL'
BOOT = '11111111-1111-4111-8111-111111111111'

# ssh launches a real shell; only Android absolute paths and commands are mapped
# to this private fixture. su changes the fixture identity, never the host UID.
DISPATCH = r'''#!/usr/bin/python3
import hashlib,json,os,pathlib,subprocess,sys,time
name=pathlib.Path(sys.argv[0]).name
root=pathlib.Path(os.environ['FIXTURE'])
args=sys.argv[1:]
def log(kind, value):
 with (root/'log').open('a') as f: f.write(json.dumps([kind,value])+'\n')
def fail(): sys.exit(70)
def normal(s): return s.replace(str(root/'android'), '')
if name=='ssh':
 log('ssh',len(args))
 if os.environ.get('TIMEOUT'): time.sleep(3); sys.exit(255)
 if os.environ.get('SSH_FAIL'): print('SECRET private-key token=secret',file=sys.stderr); sys.exit(255)
 command=args[-1].replace('/system/bin/sh','/bin/sh').replace('/system/bin/su',str(root/'bin/su'))
 command=command.replace('/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin',str(root/'bin')+':/usr/bin:/bin')
 command=command.replace('/data/',str(root/'android')+'/data/')
 sys.exit(subprocess.call(command,shell=True,executable='/bin/sh'))
if name=='su':
 if args[:1]!=['-c'] or len(args)!=2: fail()
 env=dict(os.environ,IN_KSU='1')
 sys.exit(subprocess.call(args[1],shell=True,executable='/bin/sh',env=env))
ksu=os.environ.get('IN_KSU')=='1'
changed=(root/'disabled').exists()
if name=='id':
 if args==['-u']: print(os.environ.get('ROOT_UID','0') if ksu else ('0' if changed and os.environ.get('SOURCE_AFTER_RESTORE') else os.environ.get('SOURCE_UID','10251')))
 elif args==['-Z']: print(os.environ.get('ROOT_CTX','u:r:ksu:s0') if ksu else os.environ.get('SOURCE_CTX','u:r:untrusted_app_32:s0:c1,c2'))
 else: print('uid=0(root) context='+os.environ.get('ROOT_CTX','u:r:ksu:s0'))
 sys.exit(0)
# Read-only identities may be queried before su; all payloads must be in KSU.
if name=='getprop':
 print(('changed' if changed and os.environ.get('SERIAL_AFTER_RESTORE') else os.environ.get('SERIAL', 'YOUR_SERIAL')) if args==['ro.serialno'] else 'Xiaomi/dijun/dijun:17/CP2A.260605.016/OS4.0.0.19.XODCNXM:user/release-keys'); sys.exit(0)
if name=='cat' and args==['/proc/sys/kernel/random/boot_id']:
 p=root/'boot-reads'; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))
 print('22222222-2222-4222-8222-222222222222' if changed and os.environ.get('BOOT_AFTER_RESTORE') or n>=int(os.environ.get('BOOT_SWITCH','9999')) else os.environ.get('BOOT','11111111-1111-4111-8111-111111111111')); sys.exit(0)
if not ksu: log('bare-root', [name,args]); fail()
log('payload',[name,list(map(normal,args))])
if name=='uname': print('6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k')
elif name=='getenforce': print('Permissive' if changed and os.environ.get('PERMISSIVE_AFTER_RESTORE') else os.environ.get('SELINUX','Enforcing'))
elif name=='cat':
 if args==['/sys/fs/selinux/enforce']: print(os.environ.get('ENFORCE','1'))
 elif args and args[0].endswith('module.prop'):
  print('id=mt6639-6g\nversion='+('v0.4.1\nversionCode=24' if 'modules_update' in args[0] else 'v0.2.6\nversionCode=8'))
 elif not args:
  data=sys.stdin.buffer.read(); sys.stdout.buffer.write(b'corrupted' if os.environ.get('CORRUPT_UPLOAD') else data)
 else: fail()
elif name=='mktemp':
 p=root/'android/data/local/tmp/mt6639-stage-AAAAAA'; p.mkdir(parents=True); print('/data/local/tmp/mt6639-stage-AAAAAA')
elif name=='tar':
 if args[0]=='-tzf': print('modules/mt6639-6g/module.prop\nmt6639-6g/state')
elif name=='sha256sum':
 if args[0]=='-c': print('SHA256SUMS: OK')
 elif args[0].endswith('candidate.zip'): print(hashlib.sha256(pathlib.Path(args[0]).read_bytes()).hexdigest()+'  candidate.zip')
 elif args[0].endswith('backup.tar.gz'): print('b'*64+'  backup.tar.gz')
 elif args[0]=='/odm/firmware/wifi.cfg': print('e4dcd846f31077864932b499d462cfd6a8ca1befc6386f502813e0ba053cc154  wifi.cfg')
 else: fail()
elif name=='sh':
 if args[-1]=='disable': (root/'disabled').touch()
 elif args[-1]=='status': print('state=restored\nactive_tx=none' if (root/'disabled').exists() else 'state=active\nactive_tx=/data/adb/mt6639-6g/transactions/t-old\nguardian=alive')
 else: fail()
elif name=='ksud':
 (root/'installed').touch()
 if os.environ.get('INSTALL_FAIL'): sys.exit(81)
else: fail()
'''

@unittest.skipUnless(pubenv.candidate_ready(), pubenv.SKIP_RELEASE_ASSETS)
class SSHStageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='stage-ssh-offline-'); self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name); (self.root/'bin').mkdir()
  dispatcher=self.root/'dispatch'; dispatcher.write_text(DISPATCH); dispatcher.chmod(0o755)
  for name in ('ssh','su','id','getprop','cat','uname','getenforce','mktemp','tar','sha256sum','sh'):
   (self.root/'bin'/name).symlink_to(dispatcher)
  # Absolute ksud is mapped by ssh into the private Android tree.
  ksud=self.root/'android/data/adb/ksud'; ksud.parent.mkdir(parents=True); ksud.symlink_to(dispatcher)
  (self.root/'android/data/adb/modules_update/mt6639-6g').mkdir(parents=True)
  self.config=self.root/'command.json'
  self.argv=[str(self.root/'bin/ssh'),'-F','/dev/null','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(self.root/'known_hosts'),'-i',str(self.root/'private-key'),'-p','8022','u0_a251@198.51.100.191']
  self.config.write_text(json.dumps(self.argv)); self.env=dict(os.environ,FIXTURE=str(self.root))
  self.candidate=STAGE.load_candidate(MANIFEST,REPO)
 def run_stage(self, **env):
  return STAGE.execute(self.candidate,serial=SERIAL,ssh_command_json=self.config,env=dict(self.env,**env))
 def records(self):
  p=self.root/'log'; return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
 def assert_no_mutation(self):
  self.assertFalse((self.root/'installed').exists()); self.assertFalse((self.root/'disabled').exists())
  self.assertFalse(any(v[0]=='payload' and v[1][0] in ('mktemp','tar','ksud') for v in self.records()))
 def test_default_plan_makes_no_ssh_calls(self):
  with contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(STAGE.main(['--manifest',str(MANIFEST),'--root',str(REPO),'--ssh-command-json',str(self.config)]),0)
  self.assertEqual(self.records(),[])
 def test_complete_flow_streams_binary_stops_before_reboot(self):
  result=self.run_stage(); self.assertEqual(result['status'],'staged_requires_reboot')
  data=(self.root/'android/data/local/tmp/mt6639-stage-AAAAAA/candidate.zip').read_bytes()
  self.assertEqual(data,self.candidate.path.read_bytes()); self.assertIn(b'\x00',data)
  records=self.records(); self.assertFalse(any(r[0]=='bare-root' for r in records))
  payload=[r[1][0] for r in records if r[0]=='payload']
  self.assertLess(payload.index('tar'),payload.index('sh')); self.assertLess(payload.index('sh'),payload.index('ksud'))
  self.assertFalse(any('reboot' in str(r) or 'refresh' in str(r) for r in records))
 def test_wrong_identity_serial_boot_and_selinux_stop_before_mutation(self):
  for change in ({'SOURCE_UID':'0'},{'SOURCE_UID':'2000'},{'SOURCE_CTX':'u:r:kernel:s0'},{'SOURCE_CTX':'u:r:untrusted_app_bad:s0'},{'ROOT_UID':'10000'}, {'ROOT_CTX':'u:r:kernel:s0'},{'SERIAL':'wrong'}, {'BOOT':'bad'}, {'BOOT_SWITCH':'3'},{'SELINUX':'Permissive'},{'ENFORCE':'0'}):
   with self.subTest(change=change):
    for p in ('log','boot-reads'):
     path=self.root/p
     if path.exists(): path.unlink()
    with self.assertRaises(STAGE.StageError): self.run_stage(**change)
    self.assert_no_mutation()
 def test_probe_reports_permissive_and_old_version(self):
  result=STAGE.probe_device(self.candidate,serial=SERIAL,ssh_command_json=self.config,env=dict(self.env,SELINUX='Permissive',ENFORCE='0'))
  self.assertFalse(result['ready']); self.assertEqual(result['selinux'],'Permissive'); self.assertEqual(result['current_module_version'],'v0.2.6'); self.assert_no_mutation()
 def test_config_rejects_unsafe_argv_without_disclosing_secrets(self):
  bads=[['/bin/echo'],self.argv+['id'],[s.replace('StrictHostKeyChecking=yes','StrictHostKeyChecking=no') for s in self.argv],self.argv[:-1]+['-o','ProxyCommand=SECRET',self.argv[-1]],self.argv[:-1]+['root@198.51.100.191']]
  for argv in bads:
   with self.subTest(argv=argv):
    self.config.write_text(json.dumps(argv))
    with self.assertRaises(STAGE.StageError) as caught: STAGE.load_ssh_command(self.config)
    self.assertNotIn('SECRET',str(caught.exception)); self.assertNotIn('private-key',str(caught.exception))
  for raw in ('{}','[true]', '['+' '*17000+']'):
   self.config.write_text(raw)
   with self.assertRaises(STAGE.StageError): STAGE.load_ssh_command(self.config)
  self.config.write_text(json.dumps(self.argv)); link=self.root/'symlink.json'; link.symlink_to(self.config)
  with self.assertRaises(STAGE.StageError): STAGE.load_ssh_command(link)
 def test_config_numeric_fields_are_ascii_and_bounded(self):
  port_index=self.argv.index('-p')+1
  for value in ('1','65535'):
   with self.subTest(valid_port=value):
    argv=list(self.argv); argv[port_index]=value
    self.config.write_text(json.dumps(argv))
    self.assertIn(value,STAGE.load_ssh_command(self.config))
  for value in ('²','٨٠٢٢','８０２２','0','65536','999999','9'*4096,'+22','-1','22 ',' 22','2.2',''):
   with self.subTest(invalid_port=value[:20]):
    argv=list(self.argv); argv[port_index]=value
    self.config.write_text(json.dumps(argv))
    with self.assertRaises(STAGE.StageError) as caught: STAGE.load_ssh_command(self.config)
    self.assertEqual(caught.exception.code,'SSH_CONFIG_INVALID')
  for value in ('ConnectTimeout=²','ConnectTimeout=５','ConnectTimeout=05','ConnectTimeout=5 '):
   with self.subTest(invalid_timeout=value):
    argv=[value if item=='ConnectTimeout=5' else item for item in self.argv]
    self.config.write_text(json.dumps(argv))
    with self.assertRaises(STAGE.StageError): STAGE.load_ssh_command(self.config)
  for value in ('u0_a²@198.51.100.191','u0_a２５１@198.51.100.191'):
   with self.subTest(invalid_app_user=value):
    self.config.write_text(json.dumps(self.argv[:-1]+[value]))
    with self.assertRaises(STAGE.StageError): STAGE.load_ssh_command(self.config)
  self.assertEqual(self.records(),[])
 def test_each_operation_rechecks_identity_boot_serial_and_selinux(self):
  for change in ('SOURCE_AFTER_RESTORE','SERIAL_AFTER_RESTORE','BOOT_AFTER_RESTORE','PERMISSIVE_AFTER_RESTORE'):
   with self.subTest(change=change):
    marker=self.root/'disabled'
    if marker.exists(): marker.unlink()
    remote=self.root/'android/data/local/tmp/mt6639-stage-AAAAAA'
    if remote.exists(): remote.rmdir()
    with self.assertRaises(STAGE.StageError): self.run_stage(**{change:'1'})
    self.assertTrue(marker.exists())
    self.assertFalse((self.root/'installed').exists())
    self.assertFalse((remote/'candidate.zip').exists())
 def test_upload_gate_blocks_before_redirection(self):
  transport=STAGE.SSHTransport(STAGE.load_ssh_command(self.config),SERIAL,self.env)
  transport.shell('id -u'); transport.enforcing_required=True
  target=self.root/'sentinel'; target.write_bytes(b'keep')
  transport.env=dict(self.env,SOURCE_UID='0')
  with self.assertRaises(STAGE.StageError): transport.push(self.candidate.path,str(target))
  self.assertEqual(target.read_bytes(),b'keep')
 def test_corrupt_stream_remote_hash_stops_before_install(self):
  with self.assertRaises(STAGE.StageError) as caught: self.run_stage(CORRUPT_UPLOAD='1')
  self.assertEqual(caught.exception.code,'REMOTE_HASH_MISMATCH')
  self.assertFalse((self.root/'installed').exists())
 def test_payload_exit_matching_gate_code_reports_unknown_state(self):
  with self.assertRaises(STAGE.StageError) as caught: self.run_stage(INSTALL_FAIL='1')
  self.assertEqual(caught.exception.code,'SSH_STATE_UNCONFIRMED')
  self.assertTrue((self.root/'installed').exists())
 def test_transport_failure_redacts_stderr_and_does_not_retry(self):
  with self.assertRaises(STAGE.StageError) as caught: self.run_stage(SSH_FAIL='1')
  self.assertEqual(caught.exception.code,'SSH_STATE_UNCONFIRMED')
  self.assertNotIn('SECRET',str(caught.exception)); self.assertEqual(len(self.records()),1)
  self.assert_no_mutation()
 def test_cli_requires_serial_and_rejects_conflicting_transports(self):
  base=['--manifest',str(MANIFEST),'--root',str(REPO),'--ssh-command-json',str(self.config)]
  for extra in (['--probe'],['--adb','/bin/false'],['--adb-port','5038']):
   with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): STAGE.main(base+extra)
  self.assertEqual(self.records(),[])
 def test_timeout_no_retry_and_unknown_state(self):
  transport=STAGE.SSHTransport(STAGE.load_ssh_command(self.config),SERIAL,dict(self.env,TIMEOUT='1'))
  with self.assertRaises(STAGE.StageError) as caught: transport.shell('id -u',timeout=0.05)
  self.assertEqual(caught.exception.code,'SSH_STATE_UNCONFIRMED'); self.assertEqual(len(self.records()),1); self.assert_no_mutation()

if __name__=='__main__': unittest.main()
