"""Fixed companion bundle: synthetic bytes and isolated root/service fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests import test_native_update as legacy

SOURCE=Path(__file__).resolve().parents[1]/'deploy/controller_bundle.py'
C=None
if SOURCE.exists():
    spec=importlib.util.spec_from_file_location('controller_bundle_tests',SOURCE)
    C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)

OLD_PAD,NEW_PAD,MEDIA=legacy.elf(b'pad-old'),legacy.elf(b'pad-new'),legacy.elf(b'media-preserved')
SHA=lambda data:hashlib.sha256(data).hexdigest()


class PublishedContract(unittest.TestCase):
    def test_pair_has_one_robotd_identity_and_api43(self):
        self.assertEqual((C.ROBOTD_SHA256,C.ROBOTD_SIZE,C.ROBOTD_SOURCE,C.ROBOTD_BUILD),
                         (legacy.N.REVIEWED_SHA256,legacy.N.REVIEWED_SIZE,legacy.N.SOURCE,legacy.N.BUILD))
        self.assertEqual(legacy.N.IPC_API,43)
        self.assertEqual(C.bundle_manifest('2026.11.4',b'',b'')['components']['robotd']['ipc_api'],43)

    def test_published_padd_bytes_and_source_remain_exact(self):
        self.assertEqual((C.PADD_SHA256,C.PADD_SIZE,C.PADD_SOURCE),
            ('dc1c7043a108964583fc09a11e3d6f87a25e259c346f7ce9154e77500a0881ec',3522648,
             '665613ac628fdc9b1b71f324809f0c16ce2795f2'))

    def test_restore_first_reason_points_to_exact_documented_ui_procedure(self):
        document=Path(__file__).resolve().parents[3]/'docs/native-update-20261104.md'
        self.assertTrue(document.is_file())
        self.assertIn('docs/native-update-20261104.md',C.PREDECESSOR_PROCEDURE)
        self.assertIn('恢复旧配套驱动',C.PREDECESSOR_PROCEDURE)
        self.assertIn('**恢复旧配套驱动**',document.read_text(encoding='utf-8'))


class BundleManifest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(C,'controller companion module must exist')
        patch=mock.patch.multiple(C,ROBOTD_SHA256=SHA(legacy.NEW),ROBOTD_SIZE=len(legacy.NEW),
            PADD_SHA256=SHA(NEW_PAD),PADD_SIZE=len(NEW_PAD),PADD_SOURCE='a'*40,
            SUPPORTED_PADD_SHA256=(SHA(OLD_PAD),))
        patch.start();self.addCleanup(patch.stop)

    def test_distinct_manifest_binds_both_binaries_and_all_fixed_unit_bytes(self):
        manifest=C.bundle_manifest('2026.10.31',legacy.NEW,NEW_PAD)
        self.assertEqual(manifest['schema'],'microduck-controller-payload/1')
        self.assertEqual(set(manifest['components']),{'robotd','padd'})
        self.assertEqual(manifest['components']['padd']['sha256'],SHA(NEW_PAD))
        self.assertEqual(manifest['components']['robotd']['ipc_api'],43)
        self.assertEqual(set(manifest['units']),set(C.UNITS))
        C.validate_bundle(legacy.NEW,NEW_PAD,manifest,'2026.10.31')

    def test_unknown_pins_wrong_bytes_partial_or_altered_manifest_refuse(self):
        manifest=C.bundle_manifest('2026.10.31',legacy.NEW,NEW_PAD)
        for robot,pad,body,version in ((legacy.OLD,NEW_PAD,manifest,'2026.10.31'),
            (legacy.NEW,OLD_PAD,manifest,'2026.10.31'),(legacy.NEW,NEW_PAD,{**manifest,'extra':True},'2026.10.31'),
            (legacy.NEW,NEW_PAD,manifest,'2026.11.1')):
            with self.assertRaises(ValueError):C.validate_bundle(robot,pad,body,version)
        with mock.patch.object(C,'PADD_SHA256',None),self.assertRaises(ValueError):
            C.validate_bundle(legacy.NEW,NEW_PAD,manifest,'2026.10.31')

    def test_units_add_state_group_network_and_external_legacy_voice_guard(self):
        all_units=b'\n'.join(C.UNITS.values()).decode()
        self.assertIn('StateDirectory=padd',all_units)
        self.assertIn('SupplementaryGroups=robot',all_units)
        self.assertIn('AF_NETLINK',all_units)
        self.assertIn('ExecStartPre=/usr/bin/python3 -I '+C.VOICE_GUARD,all_units)
        self.assertNotIn('ExecStart=',all_units)


class VoiceCompatibility(unittest.TestCase):
    def test_external_gate_permission_error_is_not_a_missing_marker(self):
        namespace={'__name__':'fixture_voice_guard'}
        exec(compile(C.VOICE_GUARD_PROGRAM,'external-voice-guard.py','exec'),namespace)
        with mock.patch.object(os,'lstat',side_effect=PermissionError('fixture EACCES')),mock.patch.object(Path,'lstat',side_effect=PermissionError('fixture EACCES')):
            self.assertEqual(namespace['guard']('/unreadable-fixture'),1)

    def test_legacy_suite_is_denied_until_explicit_component_restore(self):
        self.assertIsNotNone(C,'controller companion module must exist')
        old={'files':{'duck-echo/duck_echo.py':'a'*64}}
        modern={'contracts':{'padd_mapping':1},'files':{
            'duck-echo/duck_echo.py':'a'*64,'duck-echo/pad_mapping.py':'b'*64}}
        files={'duck-echo/duck_echo.py':b'echo','duck-echo/pad_mapping.py':b'reader'}
        modern['files']={name:SHA(data) for name,data in files.items()}
        self.assertFalse(C.compatible_suite(old,files))
        self.assertTrue(C.compatible_suite(modern,files))
        self.assertFalse(C.compatible_suite({**modern,'contracts':{'padd_mapping':True}},files))
        self.assertFalse(C.compatible_suite(modern,{**files,'duck-echo/duck_echo.py':b'changed'}))

    @unittest.skipUnless(os.name=='posix','requires real POSIX current symlink')
    def test_actual_external_gate_blocks_downgrade_and_survives_release_switch(self):
        self.assertIsNotNone(C)
        namespace={'__name__':'fixture_voice_guard'}
        exec(compile(C.VOICE_GUARD_PROGRAM,'external-voice-guard.py','exec'),namespace)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            marker=root/C.COMPAT.lstrip('/');marker.parent.mkdir(parents=True)
            old=root/'opt/microduck/software/releases/2026.10.27'
            new=old.with_name('2026.10.31')
            for release in (old,new):release.mkdir(parents=True)
            current=old.parent.parent/'current';current.symlink_to('releases/2026.10.27')
            (old/'suite-version.json').write_text(json.dumps({'version':'2026.10.27','files':{}}))
            self.assertEqual(namespace['guard'](temporary),0)
            marker.write_text(json.dumps({'schema':'microduck-controller-compat/1','required':True}))
            self.assertEqual(namespace['guard'](temporary),1)
            files={name:name.encode() for name in C.VOICE_FILES}
            for name,data in files.items():
                path=new/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            (new/'suite-version.json').write_text(json.dumps({'version':'2026.10.31','contracts':{'padd_mapping':1},
                'files':{name:SHA(data) for name,data in files.items()}}))
            current.unlink();current.symlink_to('releases/2026.10.31')
            self.assertEqual(namespace['guard'](temporary),0)
            current.unlink();current.symlink_to('releases/2026.10.27')
            self.assertEqual(namespace['guard'](temporary),1)
            marker.unlink()
            self.assertEqual(namespace['guard'](temporary),0)

    @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'requires isolated root/nobody POSIX child')
    def test_external_gate_is_traversable_by_actual_unprivileged_service_uid(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);root.chmod(0o755)
            marker=root/C.COMPAT.lstrip('/');marker.parent.mkdir(parents=True);marker.write_bytes(C.COMPAT_BYTES)
            new=root/'opt/microduck/software/releases/2026.10.31';new.mkdir(parents=True)
            files={name:name.encode() for name in C.VOICE_FILES}
            for name,data in files.items():
                path=new/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            metadata={'contracts':{'padd_mapping':1},'files':{n:SHA(b) for n,b in files.items()}}
            suite=new/'suite-version.json';suite.write_text(json.dumps(metadata))
            (new.parent.parent/'current').symlink_to('releases/2026.10.31')
            program=root/'voice-guard.py';program.write_bytes(C.VOICE_GUARD_PROGRAM)
            def unprivileged():os.setgroups([]);os.setgid(65534);os.setuid(65534)
            command=['/usr/bin/python3','-I','-c','import runpy,sys;raise SystemExit(runpy.run_path(sys.argv[1])["guard"](sys.argv[2]))',str(program),temporary]
            self.assertEqual(subprocess.run(command,preexec_fn=unprivileged,capture_output=True).returncode,0)
            marker.parent.chmod(0o700)
            self.assertEqual(subprocess.run(command,preexec_fn=unprivileged,capture_output=True).returncode,1)
            marker.parent.chmod(0o755)
            suite.write_text(json.dumps({'files':{}}))
            self.assertEqual(subprocess.run(command,preexec_fn=unprivileged,capture_output=True).returncode,1)


class BundleWorld(legacy.NativeWorld):
    def __init__(self,root):
        super().__init__(root)
        self.units.update({'microduck-studio':'active','mediad':'active'})
        self.generations={'padd':20,'duck-echo':30,'microduck-studio':40,'mediad':50}
        self.fail_service=None
        self.pad_args=[C.PADD,'--socket','/run/robotd.sock','--config',legacy.H.IMU_CONFIG,
                       '--max-head','2.5','--input-settings','/etc/robot/feetech-ft5/pad-settings.json']
        path=self.path(C.PADD);path.write_bytes(OLD_PAD);path.chmod(0o755)
        self.path('/etc/group').write_text('root:x:0:\nrobot:x:1001:padd,radxa\n')
        self.path('/etc/passwd').write_text('root:x:0:0::/root:/bin/sh\npadd:x:999:1001::/:/bin/false\nradxa:x:1000:1000::/:/bin/sh\n')
        with self.path('/etc/passwd').open('a') as source:source.write('mediad:x:998:998::/:/bin/false\n')
        for unit in self.generations:self.publish_controller(unit)

    def publish_controller(self,unit):
        pid=self.generations[unit];folder=self.path('/proc/'+str(pid));folder.mkdir(parents=True,exist_ok=True)
        target=C.PADD if unit=='padd' else '/opt/robot/feetech-ft5-r5/mediad' if unit=='mediad' else '/usr/bin/python3'
        binary=self.path(target);binary.parent.mkdir(parents=True,exist_ok=True)
        if not binary.exists():binary.write_bytes(MEDIA if unit=='mediad' else b'fixture python')
        if (folder/'exe').exists():(folder/'exe').unlink()
        os.link(binary,folder/'exe')
        args=(self.pad_args if unit=='padd' else [target,'/opt/microduck/software/current/duck-echo/duck_echo.py','daemon','--control-socket','/run/duck-echo/control.sock']
              if unit=='duck-echo' else [target,'-I','run.py','--listen','0.0.0.0:8000','--state-dir','/var/lib/microduck-studio','--log-dir','/var/log/microduck-studio']
              if unit=='microduck-studio' else [target])
        if unit=='microduck-studio':
            cwd=folder/'cwd'
            if not cwd.is_symlink():cwd.symlink_to(self.path('/opt/microduck/software/releases/2026.10.28/studio'),target_is_directory=True)
        (folder/'cmdline').write_bytes(b'\0'.join(a.encode() for a in args)+b'\0')
        (folder/'stat').write_text(str(pid)+' ('+unit+') S '+' '.join(['0']*18+[str(pid)]))
        owned='/etc/systemd/system/'+unit+'.service.d/'+C.DROPIN
        groups='1001' if unit=='padd' or self.path(owned).exists() else '1000'
        uid=999 if unit=='padd' else 998 if unit=='mediad' else 1000
        (folder/'status').write_text(f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nGroups:\t{groups}\n')
        if unit=='padd':
            runtime=self.path('/run/padd/mapping-state.json');runtime.parent.mkdir(parents=True,exist_ok=True)
            # RuntimeDirectoryPreserve may retain new state after restoring old
            # padd, which never publishes this file. Do not let the fixture
            # accidentally make old padd implement the new protocol.
            if binary.read_bytes()==NEW_PAD:
                runtime.write_text(json.dumps({'version':1,'pid':pid,'output_blocked':False,'device':None,'profile':None}));runtime.chmod(0o640)

    def show(self,unit,props):
        raw=super().show(unit,props)
        if unit not in getattr(self,'generations',{}):return raw
        values=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        pid=self.generations[unit];active=self.units[unit]=='active'
        values.update(LoadState='loaded',ActiveState=self.units[unit],SubState='running' if active else 'dead',
            MainPID=str(pid if active else 0),InvocationID='controller-'+str(pid),ExecMainStartTimestampMonotonic=str(pid),
            FragmentPath='/etc/systemd/system/'+unit+'.service',DropInPaths='',StateDirectory='',SupplementaryGroups='',
            RestrictAddressFamilies='AF_UNIX AF_INET AF_INET6')
        if unit=='padd':
            values['ExecStart']='{ path='+C.PADD+' ; argv[]='+' '.join(self.pad_args)+' ; ignore_errors=no ; }'
            values['User']='padd';values['Group']='robot';values['SupplementaryGroups']='input robot'
            values['DropInPaths']='/etc/systemd/system/padd.service.d/zzzz-r17-0151.conf'
        else:values['User']='mediad' if unit=='mediad' else 'radxa'
        owned='/etc/systemd/system/'+unit+'.service.d/'+C.DROPIN
        if self.path(owned).exists():
            values['DropInPaths']+=' '+owned
            if unit=='padd':values['StateDirectory']='padd'
            if unit in ('duck-echo','microduck-studio'):values['SupplementaryGroups']='robot'
            if unit=='mediad':values['RestrictAddressFamilies']+=' AF_NETLINK'
        return '\n'.join(key+'='+value for key,value in values.items())

    def run(self,argv):
        if len(argv)==3 and argv[:2] in (['systemctl','stop'],['systemctl','start']) and argv[2] in getattr(self,'generations',{}):
            unit=argv[2];self.calls.append(argv)
            if argv[1]=='stop':self.units[unit]='inactive';return 0,'',''
            if self.fail_service==unit:self.fail_service=None;return 1,'','injected service start failure'
            self.generations[unit]+=100;self.units[unit]='active';self.publish_controller(unit);return 0,'',''
        return super().run(argv)


class BundleTransaction(unittest.TestCase):
    def fresh(self,bus):
        legacy.NativeTest.fresh(self,bus);bus['homed']=False
    def socket(self,path,payload):
        if payload.get('op')=='native-recovery-readiness':
            intent=json.loads(self.w.path(C.PREPARED).read_text(encoding='utf-8'))
            self.assertEqual(payload,{'op':'native-recovery-readiness','transaction_id':intent['transaction_id']})
            return {'ready':True}
        return legacy.NativeTest.socket(self,path,payload)

    def setUp(self):
        self.assertIsNotNone(C)
        with mock.patch.object(legacy,'NativeWorld',BundleWorld):legacy.NativeTest.setUp(self)
        patch=mock.patch.multiple(C,ROBOTD_SHA256=SHA(legacy.NEW),ROBOTD_SIZE=len(legacy.NEW),
            ROBOTD_BUILD='replica-imu-verify-control.48',
            PADD_SHA256=SHA(NEW_PAD),PADD_SIZE=len(NEW_PAD),PADD_SOURCE='a'*40,SUPPORTED_PADD_SHA256=(SHA(OLD_PAD),),MEDIAD_SHA256=SHA(MEDIA))
        patch.start();self.addCleanup(patch.stop)
        predecessor=mock.patch.object(C,'LEGACY_ROBOTD_SHA256',SHA(legacy.PUBLISHED),create=True)
        predecessor.start();self.addCleanup(predecessor.stop)
        self.w.path(legacy.TARGET).write_bytes(legacy.NEW);self.w.publish_process()
        directory=self.release+'/studio/deploy/native/'
        self.w.path(directory+'padd').write_bytes(NEW_PAD)
        self.w.path(directory+'controller-manifest.json').write_text(json.dumps(C.bundle_manifest('2026.10.28',legacy.NEW,NEW_PAD)))
        for unit in self.w.generations:
            path=self.w.path('/etc/systemd/system/'+unit+'.service');path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[Service]\n')
        path=self.w.path('/etc/systemd/system/padd.service.d/zzzz-r17-0151.conf');path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[Service]\n')
        self.assertTrue(hasattr(C,'handle'),'bundle transaction must exist')
        probe=mock.patch.object(C,'mapping_status',return_value={'ok':True,'version':1,'status':{'output_blocked':False}},create=True)
        probe.start();self.addCleanup(probe.stop)

    def update(self,action='install'):
        self.op=legacy.H.Op(self.ctx,'robotd-update')
        return C.handle(legacy.H,self.op,{'by':'Studio','action':action,**({'confirmed':True} if action!='status' else {})},self.release,legacy.N)

    def journal(self):return json.loads(self.w.path(C.JOURNAL).read_text(encoding='utf-8'))

    def published_terminal(self,phase='completed'):
        # Produce the old .47 journal using the same unchanged transaction
        # schema and service bytes; no fabricated backups or safety state.
        self.w.path(legacy.TARGET).write_bytes(legacy.PUBLISHED);self.w.publish_process()
        directory=self.release+'/studio/deploy/native/'
        def payload(robot):
            self.w.path(directory+'robotd').write_bytes(robot)
            self.w.path(directory+'manifest.json').write_text(json.dumps(legacy.N.native_manifest('2026.10.28',robot)))
            self.w.path(directory+'controller-manifest.json').write_text(json.dumps(C.bundle_manifest('2026.10.28',robot,NEW_PAD)))
        with mock.patch.multiple(legacy.N,REVIEWED_SHA256=SHA(legacy.PUBLISHED),REVIEWED_SIZE=len(legacy.PUBLISHED),
                BUILD='0791fcd-feetech-qinmo-control.47',IPC_API=42),mock.patch.multiple(C,
                ROBOTD_SHA256=SHA(legacy.PUBLISHED),ROBOTD_SIZE=len(legacy.PUBLISHED),ROBOTD_BUILD='0791fcd-feetech-qinmo-control.47'):
            payload(legacy.PUBLISHED)
            self.assertEqual(self.update(),0)
            if phase=='restored':
                # The actual published .47 updater predates restore-origin
                # publication. Keep its terminal fixture byte schema faithful.
                with mock.patch.object(C.BundleTransaction,'begin_restore',return_value=None):
                    self.assertEqual(self.update('restore'),0)
        payload(legacy.NEW)
        return self.journal(),self.w.path(C.JOURNAL).read_bytes()

    def test_published47_completed_is_read_only_and_requires_explicit_restore(self):
        record,raw=self.published_terminal()
        def lifecycle():return [c for c in self.w.calls if c[:2] in (['systemctl','start'],['systemctl','stop'],['systemctl','daemon-reload'])]
        calls=lifecycle()
        self.assertEqual(self.update('status'),0)
        detail=self.ctx.stdout.lines()[-1]['detail']
        self.assertFalse(detail['bundle_current']);self.assertTrue(detail['restorable'])
        self.assertIn('docs/native-update-20261104.md',detail['reason'])
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
        self.assertEqual(lifecycle(),calls)
        with self.assertRaises(legacy.H.Stop) as error:self.update()
        self.assertEqual(error.exception.result,'controller_predecessor_restore_required')
        self.assertIn('.47',str(error.exception))
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
        self.assertEqual(lifecycle(),calls)
        self.assertEqual(self.update('restore'),0)
        self.assertEqual(self.w.path(C.ROBOTD).read_bytes(),legacy.PUBLISHED)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertEqual(self.journal()['phase'],'restored')

    def test_published47_restored_history_is_archived_before_new_bundle_install(self):
        record,raw=self.published_terminal('restored')
        self.assertEqual(self.update('status'),0)
        self.assertFalse(self.ctx.stdout.lines()[-1]['detail']['restorable'])
        self.assertEqual(self.update(),0)
        history=C.STATE+'/'+record['transaction_id']+'/journal.json'
        self.assertEqual(self.w.path(history).read_bytes(),raw)
        self.assertNotEqual(self.journal()['transaction_id'],record['transaction_id'])
        self.assertEqual(self.w.path(C.ROBOTD).read_bytes(),legacy.NEW)
        self.assertEqual(self.update('restore'),0)
        self.assertEqual(self.w.path(C.ROBOTD).read_bytes(),legacy.PUBLISHED)

    def test_published47_unfinished_record_requires_original_signed_updater(self):
        record,_=self.published_terminal()
        for fields in ({'active':True},{'restore_pending':True},{'cleanup_pending':True},{'phase':'recovery_required'}):
            with self.subTest(fields=fields):
                raw=json.dumps({**record,**fields}).encode();self.w.path(C.JOURNAL).write_bytes(raw)
                calls=list(self.w.calls)
                for action in ('status','install','recover','restore'):
                    with self.assertRaises(legacy.H.Stop) as error:self.update(action)
                    self.assertEqual(error.exception.result,'controller_recovery_required')
                    self.assertIn('.47',str(error.exception))
                self.assertEqual(self.w.calls,calls);self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)

    def test_published47_remaining_exclusions_require_original_signed_updater(self):
        _,raw=self.published_terminal()
        transaction=legacy.N.Transaction(legacy.H,self.op,self.release)
        for marker in (legacy.N.ACTIVE,legacy.N.RESTORE_PERMIT,legacy.GUARD,*transaction.controller_guard_paths()):
            with self.subTest(marker=marker):
                path=self.w.path(marker);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'preserve')
                calls=list(self.w.calls)
                with self.assertRaises(legacy.H.Stop) as error:self.update('restore')
                self.assertEqual(error.exception.result,'controller_recovery_required')
                self.assertEqual(self.w.calls,calls);self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
                self.assertEqual(path.read_bytes(),b'preserve');path.unlink()

    def test_owned_published47_restore_interruption_can_recover_with_new_updater(self):
        self.published_terminal()
        original=C.BundleTransaction.write
        def cut(transaction,path,data,*args,**kwargs):
            result=original(transaction,path,data,*args,**kwargs)
            if path==C.JOURNAL and json.loads(data).get('phase')=='recovery_required':raise legacy.PowerCut()
            return result
        with mock.patch.object(C.BundleTransaction,'write',cut),self.assertRaises(legacy.PowerCut):self.update('restore')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.journal()['phase'],'restored')
        self.assertEqual(self.w.path(C.ROBOTD).read_bytes(),legacy.PUBLISHED)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())

    def test_current48_restore_cut_before_protected_commit_can_recover(self):
        self.assertEqual(self.update(),0)
        original=C.BundleTransaction.write
        def cut(transaction,path,data,*args,**kwargs):
            result=original(transaction,path,data,*args,**kwargs)
            if path==C.JOURNAL:
                record=json.loads(data)
                if record.get('phase')=='restored' and record.get('active') is True:raise legacy.PowerCut()
            return result
        with mock.patch.object(C.BundleTransaction,'write',cut),self.assertRaises(legacy.PowerCut):self.update('restore')
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.journal()['phase'],'restored')
        self.assertFalse(self.journal()['active'])
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)

    def test_owned_published47_restore_needs_exact_immutable_terminal_origin(self):
        record,raw=self.published_terminal()
        origin=C.STATE+'/'+record['transaction_id']+'/restore-origin.json'
        self.w.path(origin).write_bytes(raw)
        marked={**record,'phase':'restoring','active':True,'running_verified':False,
                'restore_origin_sha256':SHA(raw),'restore_updater_sha256':C.ROBOTD_SHA256}
        unfinished=json.dumps({**record,'phase':'recovery_required','active':True}).encode()
        for fields,body in (({'restore_origin_sha256':'a'*64},raw),
                            ({'restore_updater_sha256':SHA(legacy.PUBLISHED)},raw),
                            ({'restore_origin_sha256':SHA(unfinished)},unfinished),
                            ({'profile_sha256':'b'*64},raw)):
            with self.subTest(fields=fields):
                self.w.path(origin).write_bytes(body)
                self.w.path(C.JOURNAL).write_text(json.dumps({**marked,**fields}))
                calls=list(self.w.calls)
                with self.assertRaises(legacy.H.Stop):self.update('recover')
                self.assertEqual(self.w.calls,calls)

    def test_owned_published47_restore_never_overwrites_foreign_protected_baseline(self):
        self.published_terminal()
        original=C.BundleTransaction.write
        def cut(transaction,path,data,*args,**kwargs):
            result=original(transaction,path,data,*args,**kwargs)
            if path==C.JOURNAL and json.loads(data).get('phase')=='restoring':raise legacy.PowerCut()
            return result
        with mock.patch.object(C.BundleTransaction,'write',cut),self.assertRaises(legacy.PowerCut):self.update('restore')
        foreign=b'{"robotd":{"MainPID":"999"},"padd":{}}'
        self.w.path(legacy.N.PROTECTED).write_bytes(foreign)
        self.assertEqual(self.update('recover'),5)
        self.assertEqual(self.w.path(legacy.N.PROTECTED).read_bytes(),foreign)
        self.assertTrue(self.journal()['active'])

    def test_owned_published47_restore_write_and_cleanup_boundaries_recover(self):
        self.assert_owned_restore_boundaries(published=True)

    def test_current48_restore_write_and_cleanup_boundaries_recover(self):
        self.assert_owned_restore_boundaries(published=False)

    def assert_owned_restore_boundaries(self,published):
        guards=legacy.N.Transaction(legacy.H,self.op,self.release).controller_guard_paths()
        boundaries=[('origin',None),('journal',('restoring',True)),('journal',('recovery_required',True)),
                    ('journal',('restored',True)),('journal',('restored',False))]
        boundaries += [('write',path) for path in (legacy.N.ACTIVE,legacy.GUARD,legacy.N.RESTORE_PERMIT,
                       legacy.N.PROTECTED,C.ROBOTD,C.PADD)]
        boundaries += [('remove',path) for path in (*C.UNITS,C.VOICE_GUARD,C.COMPAT,C.RUNTIME,
                       legacy.GUARD,legacy.N.ACTIVE,*guards,legacy.N.RESTORE_PERMIT)]
        for kind,boundary in boundaries:
            fixture=BundleTransaction();fixture.setUp()
            try:
                with self.subTest(kind=kind,boundary=boundary):
                    if published:record,_=fixture.published_terminal()
                    else:
                        self.assertEqual(fixture.update(),0)
                        record=fixture.journal()
                    origin=C.STATE+'/'+record['transaction_id']+'/restore-origin.json'
                    write=C.BundleTransaction.write;remove=legacy.H.remove
                    def cut_write(transaction,path,data,*args,**kwargs):
                        result=write(transaction,path,data,*args,**kwargs)
                        cut=(kind=='origin' and path==origin) or (kind=='write' and path==boundary)
                        if kind=='journal' and path==C.JOURNAL:
                            value=json.loads(data);cut=(value.get('phase'),value.get('active'))==boundary
                        if cut:raise legacy.PowerCut()
                        return result
                    def cut_remove(ctx,path):
                        existed=os.path.lexists(ctx.p(path));result=remove(ctx,path)
                        if kind=='remove' and path==boundary and existed:raise legacy.PowerCut()
                        return result
                    with mock.patch.object(C.BundleTransaction,'write',cut_write),mock.patch.object(legacy.H,'remove',cut_remove),self.assertRaises(legacy.PowerCut):
                        fixture.update('restore')
                    self.assertEqual(fixture.update('restore' if kind=='origin' else 'recover'),0)
                    self.assertEqual(fixture.journal()['phase'],'restored');self.assertFalse(fixture.journal()['active'])
                    self.assertEqual(fixture.w.path(C.ROBOTD).read_bytes(),legacy.PUBLISHED if published else legacy.NEW)
                    self.assertEqual(fixture.w.path(C.PADD).read_bytes(),OLD_PAD)
                    self.assertFalse(fixture.w.path(legacy.N.ACTIVE).exists())
                    self.assertFalse(any(fixture.w.path(path).exists() for path in (*C.UNITS,C.VOICE_GUARD,C.COMPAT,*guards)))
            finally:fixture.doCleanups()

    def test_published47_completed_vector_tamper_never_mutates(self):
        _,raw=self.published_terminal();self.w.path(C.ROBOTD).write_bytes(legacy.OLD)
        calls=list(self.w.calls)
        for action in ('status','install','restore'):
            with self.assertRaises(legacy.H.Stop):self.update(action)
        self.assertEqual(self.w.calls,calls);self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)

    def test_published47_cannot_claim_future_or_arbitrary_original_robotd(self):
        record,_=self.published_terminal()
        record['old_sha256']=record['records'][0]['sha256']=SHA(legacy.NEW)
        self.w.path(C.STATE+'/'+record['transaction_id']+'/component-0').write_bytes(legacy.NEW)
        raw=json.dumps(record).encode();self.w.path(C.JOURNAL).write_bytes(raw)
        calls=list(self.w.calls)
        with self.assertRaises(legacy.H.Stop):self.update('status')
        self.assertEqual(self.w.calls,calls);self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)

    def test_published47_archive_power_loss_keeps_old_record_and_retry(self):
        record,raw=self.published_terminal('restored')
        history=C.STATE+'/'+record['transaction_id']+'/journal.json'
        original=C.BundleTransaction.write
        def cut(transaction,path,*args,**kwargs):
            result=original(transaction,path,*args,**kwargs)
            if path==history:raise legacy.PowerCut()
            return result
        with mock.patch.object(C.BundleTransaction,'write',cut),self.assertRaises(legacy.PowerCut):self.update()
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
        self.assertEqual(self.w.path(history).read_bytes(),raw)
        self.assertEqual(self.update(),0)

    def test_published47_foreign_archive_is_never_overwritten(self):
        record,raw=self.published_terminal('restored')
        history=C.STATE+'/'+record['transaction_id']+'/journal.json';self.w.path(history).write_bytes(b'foreign')
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertEqual(self.w.path(history).read_bytes(),b'foreign')
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)

    def test_published47_existing_archive_must_sync_before_new_intent(self):
        record,raw=self.published_terminal('restored')
        directory=C.STATE+'/'+record['transaction_id'];self.w.path(directory+'/journal.json').write_bytes(raw)
        original=legacy.N.Transaction.sync_dir
        def fail(transaction,path):
            if path==directory:raise OSError('archive directory sync failure')
            return original(transaction,path)
        with mock.patch.object(legacy.N.Transaction,'sync_dir',fail),self.assertRaises(OSError):self.update()
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
        self.assertFalse(self.w.path(C.PREPARED).exists());self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())

    def test_new_prepared_intent_after_archived47_restored_record_can_recover(self):
        self.assert_archived47_prepared_recovery(C.PREPARED)

    def test_new_active_publication_after_archived47_restored_record_can_recover(self):
        self.assert_archived47_prepared_recovery(legacy.N.ACTIVE)

    def assert_archived47_prepared_recovery(self,boundary):
        record,raw=self.published_terminal('restored')
        original=C.BundleTransaction.write
        def cut(transaction,path,*args,**kwargs):
            result=original(transaction,path,*args,**kwargs)
            if path==boundary:raise legacy.PowerCut()
            return result
        with mock.patch.object(C.BundleTransaction,'write',cut),self.assertRaises(legacy.PowerCut):self.update()
        self.assertEqual(self.w.path(C.JOURNAL).read_bytes(),raw)
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(C.ROBOTD).read_bytes(),legacy.PUBLISHED)

    def test_robotd_current_still_installs_padd_and_owned_units_then_explicitly_restores(self):
        self.assertEqual(self.update(),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),NEW_PAD)
        self.assertEqual(self.journal()['phase'],'completed')
        for path,body in C.UNITS.items():self.assertEqual(self.w.path(path).read_bytes(),body)
        self.assertTrue(self.w.path(C.COMPAT).exists())
        self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())
        self.assertEqual({p:self.w.path(p).read_bytes() for p in legacy.FILES},self.saved)
        self.assertEqual(self.update('restore'),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertFalse(self.w.path(C.COMPAT).exists())
        for path in C.UNITS:self.assertFalse(self.w.path(path).exists())
        self.assertEqual(self.journal()['phase'],'restored')

    def factory_vector(self):
        self.w.path(C.PADD).write_bytes(NEW_PAD)
        for path,body in {**C.UNITS,C.VOICE_GUARD:C.VOICE_GUARD_PROGRAM,C.COMPAT:C.COMPAT_BYTES}.items():
            target=self.w.path(path);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);target.chmod(0o644)
        for unit in self.w.generations:self.w.publish_controller(unit)

    def test_exact_factory_pair_without_journal_is_current_without_restore_claim(self):
        self.factory_vector()
        self.assertEqual(self.update('status'),0)
        detail=self.ctx.stdout.lines()[-1]['detail']
        self.assertTrue(detail['bundle_current']);self.assertTrue(detail['running_verified'])
        self.assertFalse(detail['restorable']);self.assertFalse(detail['active'])
        self.assertFalse(self.w.path(C.JOURNAL).exists())
        calls=[c for c in self.w.calls if c[:2] in (['systemctl','start'],['systemctl','stop'])]
        self.assertEqual(self.update(),0)
        self.assertFalse(self.w.path(C.JOURNAL).exists())
        self.assertEqual([c for c in self.w.calls if c[:2] in (['systemctl','start'],['systemctl','stop'])],calls)
        with self.assertRaises(legacy.H.Stop):self.update('restore')

    def test_factory_current_requires_exact_support_vector_and_mapping_health(self):
        self.factory_vector()
        path=next(iter(C.UNITS));original=self.w.path(path).read_bytes()
        self.w.path(path).write_bytes(b'foreign')
        self.assertEqual(self.update('status'),0)
        self.assertFalse(self.ctx.stdout.lines()[-1]['detail']['bundle_current'])
        self.w.path(path).write_bytes(original)
        with mock.patch.object(C,'mapping_status',return_value={'ok':False}):
            self.assertEqual(self.update('status'),0)
            self.assertFalse(self.ctx.stdout.lines()[-1]['detail']['bundle_current'])

    def test_partial_bundle_and_unknown_installed_padd_refuse_without_changes(self):
        self.w.path(self.release+'/studio/deploy/native/padd').unlink()
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())
        self.w.path(self.release+'/studio/deploy/native/padd').write_bytes(NEW_PAD)
        self.w.path(C.PADD).write_bytes(legacy.elf(b'unreviewed'));self.w.publish_controller('padd')
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())

    def test_restart_failure_rolls_back_complete_component_vector(self):
        self.w.fail_service='padd'
        self.assertNotEqual(self.update(),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertEqual(self.journal()['phase'],'restored')
        self.assertFalse(self.w.path(C.COMPAT).exists())

    def test_real_relax_clears_homed_but_fresh_unloaded_stationary_state_is_installable(self):
        original=self.w.bus_mutate
        def relaxed(bus):original(bus);bus['homed']=False
        self.w.bus_mutate=relaxed
        self.assertEqual(self.update(),0)

    def test_restore_removes_only_owned_new_runtime_and_preserves_later_profiles(self):
        self.assertEqual(self.update(),0)
        profile=self.w.path('/var/lib/padd/mappings.json');profile.parent.mkdir(parents=True,exist_ok=True)
        profile.write_bytes(b'{"schema":1,"profiles":{"later-user-change":{}}}');profile.chmod(0o600)
        saved=profile.read_bytes()
        self.assertTrue(self.w.path('/run/padd/mapping-state.json').exists())
        self.assertEqual(self.update('restore'),0)
        self.assertFalse(self.w.path('/run/padd/mapping-state.json').exists())
        self.assertEqual(profile.read_bytes(),saved)

    def test_foreign_runtime_refuses_restore_and_keeps_compatibility_protection(self):
        self.assertEqual(self.update(),0)
        runtime=self.w.path('/run/padd/mapping-state.json')
        runtime.write_text(json.dumps({'version':1,'pid':123456,'output_blocked':False}))
        saved=runtime.read_bytes()
        self.assertNotEqual(self.update('restore'),0)
        self.assertEqual(runtime.read_bytes(),saved)
        self.assertTrue(self.w.path(C.COMPAT).exists())
        self.assertTrue(self.journal()['active'])

    def test_journal_cannot_choose_paths_target_hashes_or_process_arguments(self):
        self.assertEqual(self.update(),0)
        valid=self.journal()
        cases=[]
        changed=json.loads(json.dumps(valid));changed['targets'][C.PADD]['sha256']='a'*64;cases.append(changed)
        changed=json.loads(json.dumps(valid));changed['padd_args']=[C.PADD,'--socket','/tmp/foreign'];cases.append(changed)
        changed=json.loads(json.dumps(valid));changed['files'][0]['path']='/tmp/foreign';cases.append(changed)
        changed=json.loads(json.dumps(valid));changed['service_files'][0]['path']='/tmp/foreign';cases.append(changed)
        for index,value in enumerate(cases):
            with self.subTest(case=index):
                self.w.path(C.JOURNAL).write_text(json.dumps(value))
                with self.assertRaises(legacy.H.Stop):self.update('status')
        self.w.path(C.JOURNAL).write_text(json.dumps(valid))

    def test_interrupted_replacement_recovers_every_component(self):
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.install_bytes
        def die(transaction,path,*args,**kwargs):
            original(transaction,path,*args,**kwargs)
            if path==C.PADD:raise PowerLoss()
        with mock.patch.object(C.BundleTransaction,'install_bytes',die),self.assertRaises(PowerLoss):self.update()
        self.assertTrue(self.w.path(legacy.N.ACTIVE).exists())
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertEqual(self.journal()['phase'],'restored')

    def test_status_is_not_available_when_pins_or_payload_are_incomplete(self):
        with mock.patch.object(C,'PADD_SHA256',None):
            self.assertEqual(self.update('status'),0)
            self.assertFalse(self.ctx.stdout.lines()[-1]['detail']['available'])

    def test_completed_vector_tamper_is_never_a_successful_repeat_install(self):
        self.assertEqual(self.update(),0)
        path=next(iter(C.UNITS));self.w.path(path).write_bytes(b'foreign')
        with self.assertRaises(legacy.H.Stop):self.update()

    def test_power_loss_at_first_journal_is_visible_to_old_helper_and_recoverable(self):
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.write
        def die(transaction,path,*args,**kwargs):
            if path==C.JOURNAL:raise PowerLoss()
            return original(transaction,path,*args,**kwargs)
        with mock.patch.object(C.BundleTransaction,'write',die),self.assertRaises(PowerLoss):self.update()
        with self.assertRaises(legacy.H.Stop):legacy.H.native_update_conflict(self.op)
        self.assertEqual(self.update('recover'),0)

    def test_power_loss_after_prepared_before_active_is_guarded_and_recoverable(self):
        from studio import helper
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.write
        def die(transaction,path,*args,**kwargs):
            result=original(transaction,path,*args,**kwargs)
            if path==C.PREPARED:raise PowerLoss()
            return result
        with mock.patch.object(C.BundleTransaction,'write',die),self.assertRaises(PowerLoss):self.update()
        self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())
        self.assertTrue(helper.native_update_state(root=self.tmp)['active'])
        with self.assertRaises(legacy.H.Stop):legacy.H.native_update_conflict(self.op)
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.journal()['phase'],'restored')

    def test_new_unpublished_intent_is_not_hidden_by_previous_restored_journal(self):
        self.assertEqual(self.update(),0);self.assertEqual(self.update('restore'),0)
        old_id=self.journal()['transaction_id']
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.write
        def die(transaction,path,*args,**kwargs):
            result=original(transaction,path,*args,**kwargs)
            if path==C.PREPARED:raise PowerLoss()
            return result
        with mock.patch.object(C.BundleTransaction,'write',die),self.assertRaises(PowerLoss):self.update()
        self.assertEqual(self.update('recover'),0)
        self.assertNotEqual(self.journal()['transaction_id'],old_id)
        self.assertFalse(self.w.path(C.PREPARED).exists())

    def test_each_legacy_cleanup_unlink_boundary_leaves_terminal_or_recoverable_state(self):
        class PowerLoss(BaseException):pass
        paths=(legacy.N.ACTIVE,*legacy.N.Transaction.controller_guard_paths(None),legacy.N.RESTORE_PERMIT)
        for path in paths:
            fixture=BundleTransaction();fixture.setUp();remove=legacy.H.remove
            def die(ctx,target):
                result=remove(ctx,target)
                if target==path:raise PowerLoss()
                return result
            try:
                with self.subTest(path=path):
                    with mock.patch.object(legacy.H,'remove',die),self.assertRaises(PowerLoss):fixture.update()
                    state=fixture.journal()
                    self.assertFalse(state['active'] or state['restore_pending'] or state['cleanup_pending'])
                    self.assertEqual(fixture.update('recover'),0)
                    self.assertFalse(C.BundleTransaction(legacy.H,fixture.op,fixture.release,legacy.N).base.has_exclusion())
            finally:fixture.doCleanups()

    def test_unpublished_intent_needs_fresh_safety_and_exact_unchanged_preimages(self):
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.write
        def die(transaction,path,*args,**kwargs):
            result=original(transaction,path,*args,**kwargs)
            if path==C.PREPARED:raise PowerLoss()
            return result
        with mock.patch.object(C.BundleTransaction,'write',die),self.assertRaises(PowerLoss):self.update()
        original_bus=self.w.bus_mutate
        self.w.bus_mutate=lambda bus:(original_bus(bus),bus.update(policy_enabled=True))
        with self.assertRaises(legacy.H.Stop):self.update('recover')
        self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())
        self.w.bus_mutate=original_bus
        self.w.path(C.PADD).write_bytes(NEW_PAD)
        with self.assertRaises(legacy.H.Stop):self.update('recover')
        self.assertFalse(self.w.path(legacy.N.ACTIVE).exists())
        intent=json.loads(self.w.path(C.PREPARED).read_text());intent['targets'][C.PADD]['sha256']='f'*64
        self.w.path(C.PREPARED).write_text(json.dumps(intent))
        with self.assertRaises(legacy.H.Stop):self.update('recover')

    def test_power_loss_before_terminal_commit_keeps_old_helper_exclusion(self):
        class PowerLoss(BaseException):pass
        original=C.BundleTransaction.save
        def die(transaction,m,phase,**fields):
            if phase=='completed' and fields.get('active') is False:raise PowerLoss()
            return original(transaction,m,phase,**fields)
        with mock.patch.object(C.BundleTransaction,'save',die),self.assertRaises(PowerLoss):self.update()
        self.assertTrue(self.w.path(legacy.N.ACTIVE).exists(),'old helpers cannot read v2 pending flags')
        self.assertTrue(self.journal()['active'])
        self.assertEqual(self.update('recover'),0)

    def test_power_loss_after_terminal_commit_before_cleanup_is_recoverable(self):
        class PowerLoss(BaseException):pass
        with mock.patch.object(legacy.N.Transaction,'remove_controller_exclusion',side_effect=PowerLoss),self.assertRaises(PowerLoss):self.update()
        self.assertFalse(self.journal()['active'])
        self.assertTrue(self.w.path(legacy.N.ACTIVE).exists())
        self.assertEqual(self.update('recover'),0)
        self.assertEqual(self.journal()['phase'],'restored')

    def test_protection_attempts_all_stops_after_robot_and_padd_fail(self):
        self.assertEqual(self.update(),0)
        transaction=C.BundleTransaction(legacy.H,self.op,self.release,legacy.N)
        original=self.w.run;stops=[]
        def fail(argv):
            if argv[:2]==['systemctl','stop']:
                stops.append(argv[2])
                if argv[2] in ('robotd','padd'):return 1,'','injected persistent stop failure'
            return original(argv)
        with mock.patch.object(self.w,'run',fail):self.assertTrue(transaction.protect(self.journal()))
        self.assertEqual(set(stops),{'robotd',*C.SERVICES})
        for unit in ('duck-echo','microduck-studio','mediad'):self.assertEqual(self.w.units[unit],'inactive')
        self.assertTrue(self.w.path(legacy.N.ACTIVE).exists())

    def test_inactive_unknown_or_absent_media_cannot_gain_network_permissions(self):
        self.w.units['mediad']='inactive'
        self.w.path(C.MEDIAD).write_bytes(b'unknown-custom-media')
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path('/etc/systemd/system/mediad.service.d/'+C.DROPIN).exists())

    def test_absent_media_service_refuses_the_fixed_bundle_layout(self):
        original=self.w.show
        def absent(unit,props):
            return 'LoadState=not-found\nActiveState=inactive\nSubState=dead\n' if unit=='mediad' else original(unit,props)
        with mock.patch.object(self.w,'show',absent),self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())

    def test_unknown_media_policy_and_binary_are_refused_without_overwriting_settings(self):
        original=self.w.show
        def custom(unit,props):
            result=original(unit,props)
            return result.replace('RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6','RestrictAddressFamilies=~AF_NETLINK') if unit=='mediad' else result
        with mock.patch.object(self.w,'show',custom),self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())
        self.w.path('/opt/robot/feetech-ft5-r5/mediad').write_bytes(b'unknown');self.w.publish_controller('mediad')
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())

    def test_published_runtime_without_actual_mapping_socket_capability_is_not_completion(self):
        with mock.patch.object(C,'mapping_status',return_value={'ok':True,'version':0}):
            self.assertNotEqual(self.update(),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),OLD_PAD)
        self.assertEqual(self.journal()['phase'],'restored')

    def test_other_python_daemon_cannot_be_mistaken_for_mapping_aware_voice(self):
        self.w.path('/proc/30/cmdline').write_bytes(b'/usr/bin/python3\0/opt/duck-echo/duck_echo.py\0daemon\0')
        with self.assertRaises(legacy.H.Stop):self.update()
        self.assertFalse(self.w.path(C.JOURNAL).exists())

    def test_startup_gate_wrapper_gets_bounded_time_to_exec_padd(self):
        original=C.BundleTransaction.process;seen=[]
        def warming(transaction,unit,*args,**kwargs):
            if unit=='padd' and transaction.read(C.PADD)==NEW_PAD and len(seen)<2:
                seen.append(unit);transaction.refuse('startup gate has not execed padd yet')
            return original(transaction,unit,*args,**kwargs)
        with mock.patch.object(C.BundleTransaction,'process',warming):self.assertEqual(self.update(),0)
        self.assertEqual(len(seen),2)

    def test_first_native_operation_creates_service_lock_but_never_replaces_motion_lock(self):
        self.w.path(legacy.H.SERVICE_LOCK).unlink()
        self.assertEqual(self.update(),0)

    def test_changed_software_native_process_baseline_is_not_overwritten_on_restore(self):
        self.assertEqual(self.update(),0)
        changed=b'{"robotd":{"MainPID":"999"},"padd":{}}'
        self.w.path(legacy.N.PROTECTED).write_bytes(changed)
        self.assertNotEqual(self.update('restore'),0)
        self.assertEqual(self.w.path(legacy.N.PROTECTED).read_bytes(),changed)

    def test_each_component_write_boundary_restores_complete_preimage(self):
        class PowerLoss(BaseException):pass
        for path in C.BundleTransaction.paths(None):
            fixture=BundleTransaction();fixture.setUp()
            original=C.BundleTransaction.install_bytes
            def die(transaction,target,*args,**kwargs):
                original(transaction,target,*args,**kwargs)
                if target==path:raise PowerLoss()
            try:
                with self.subTest(path=path):
                    with mock.patch.object(C.BundleTransaction,'install_bytes',die),self.assertRaises(PowerLoss):fixture.update()
                    self.assertEqual(fixture.update('recover'),0)
                    self.assertEqual(fixture.w.path(C.PADD).read_bytes(),OLD_PAD)
                    self.assertFalse(fixture.w.path(C.COMPAT).exists())
            finally:fixture.doCleanups()

    def test_legacy_entry_dispatches_bundle_and_partial_v2_never_falls_back(self):
        with mock.patch.object(legacy.N,'CONTROLLER_BUNDLE',C,create=True):
            self.assertEqual(legacy.N.handle(legacy.H,self.op,{'by':'Studio','action':'install','confirmed':True},self.release),0)
        self.assertEqual(self.w.path(C.PADD).read_bytes(),NEW_PAD)

    def test_v2_journal_is_visible_to_unprivileged_state_reader_and_old_helper_bridge(self):
        from studio import helper
        class PowerLoss(BaseException):pass
        with mock.patch.object(C.BundleTransaction,'stop_services',side_effect=PowerLoss),self.assertRaises(PowerLoss):self.update()
        result=helper.native_update_state(root=self.tmp)
        self.assertTrue(result['active']);self.assertEqual(result.get('bundle_schema'),C.PAYLOAD_SCHEMA)
        with self.assertRaises(legacy.H.Stop):legacy.H.native_update_conflict(self.op)


class BundleLoader(unittest.TestCase):
    def setUp(self):
        legacy.NativeTest.setUp(self)
    socket=legacy.NativeTest.socket
    fresh=legacy.NativeTest.fresh

    def test_loader_verifies_controller_dependency_and_all_v2_members_before_compile(self):
        helper=self.w.path(self.release+'/studio/deploy/microduck-helper');helper.write_bytes(Path(legacy.H.__file__).read_bytes())
        self.w.path(self.release+'/studio/deploy/native_update.py').write_bytes(legacy.MODULE.read_bytes())
        module=self.w.path(self.release+'/studio/deploy/controller_bundle.py');module.write_bytes(SOURCE.read_bytes())
        self.w.path(self.release+'/studio/deploy/native/padd').write_bytes(NEW_PAD)
        manifest=self.w.path(self.release+'/studio/deploy/native/controller-manifest.json');manifest.write_text('{}')
        for name in C.VOICE_FILES:
            path=self.w.path(self.release+'/'+name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
        names=('studio/deploy/microduck-helper','studio/deploy/native_update.py','studio/deploy/controller_bundle.py',
               'studio/deploy/native/robotd','studio/deploy/native/manifest.json','studio/deploy/native/padd','studio/deploy/native/controller-manifest.json',*C.VOICE_FILES)
        suite=self.w.path(self.release+'/suite-version.json')
        metadata={'version':'2026.10.28','files':{n:SHA(self.w.path(self.release+'/'+n).read_bytes()) for n in names}}
        suite.write_text(json.dumps(metadata))
        try:self.w.path('/opt/microduck/software/current').symlink_to('releases/2026.10.28',target_is_directory=True)
        except OSError:self.skipTest('requires installed-release symlink')
        with mock.patch.object(legacy.H,'__file__',str(helper)):
            with self.assertRaises(legacy.H.Stop):legacy.H.load_native_update(self.op)
            metadata['contracts']={'padd_mapping':1};suite.write_text(json.dumps(metadata))
            loaded,_=legacy.H.load_native_update(self.op)
            self.assertEqual(loaded.CONTROLLER_BUNDLE.PAYLOAD_SCHEMA,C.PAYLOAD_SCHEMA)
            module.write_bytes(b'raise RuntimeError("unverified dependency executed")')
            with self.assertRaises(legacy.H.Stop):legacy.H.load_native_update(self.op)
            module.write_bytes(SOURCE.read_bytes());manifest.unlink()
            with self.assertRaises(legacy.H.Stop):legacy.H.load_native_update(self.op)
