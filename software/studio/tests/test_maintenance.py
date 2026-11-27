import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import json
from pathlib import Path
import unittest
from unittest import mock
import zipfile
from starlette.requests import Request
from studio.maintenance import make_handler


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    async def call(self, body, *, session=True, password=True, csrf=True):
        auth=mock.Mock();auth.has_password.return_value=password
        handler=make_handler(auth, lambda r: ('token', {'csrf':'test'} if session else None), lambda r: True)
        raw=json.dumps(body).encode()
        async def receive():return {'type':'http.request','body':raw,'more_body':False}
        request=Request({'type':'http','method':'POST','headers':[(b'x-csrf',b'test' if csrf else b'wrong')]}, receive)
        return await handler(request)

    async def test_auth_csrf_and_confirmation_prevent_helper_execution(self):
        with mock.patch('studio.maintenance.run') as run:
            for options,status in [({'session':False},401),({'password':False},401),({'csrf':False},403),({},400)]:
                response=await self.call({'op':'shutdown'},**options)
                self.assertEqual(response.status_code,status)
                self.assertEqual(response.headers['cache-control'],'no-store')
            run.assert_not_called()

    async def test_preview_and_shutdown_use_fixed_operations(self):
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            response=await self.call({'op':'preview','archive':base64.b64encode(b'zip').decode()})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('preview',b'zip')
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            response=await self.call({'op':'shutdown','confirmed':True})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('shutdown',None)

    async def call_with(self, body, *, check_password=True, blocker=None):
        auth=mock.Mock();auth.has_password.return_value=True;auth.check_password.return_value=check_password
        handler=make_handler(auth, lambda r: ('token', {'csrf':'test'}), lambda r: True, blocker)
        raw=json.dumps(body).encode()
        async def receive():return {'type':'http.request','body':raw,'more_body':False}
        return await handler(Request({'type':'http','method':'POST','headers':[(b'x-csrf',b'test')]}, receive))

    async def test_restore_needs_the_password_again(self):
        archive=base64.b64encode(b'zip').decode()
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            response=await self.call_with({'op':'restore','archive':archive,'confirmed':True,'password':'wrong'},check_password=False)
            self.assertEqual(response.status_code,400)
            run.assert_not_called()
            response=await self.call_with({'op':'restore','archive':archive,'confirmed':True,'password':'right','request_id':'a'*32})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('restore',b'a'*32+b'\nzip')

    async def test_update_or_calibration_blocks_restore_and_shutdown(self):
        archive=base64.b64encode(b'zip').decode()
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            for body in [{'op':'shutdown','confirmed':True},{'op':'restore','archive':archive,'confirmed':True,'password':'x'}]:
                response=await self.call_with(body,blocker=lambda:'正在更新软件')
                self.assertEqual(response.status_code,400)
            run.assert_not_called()


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'deploy/microduck-maintenance'
        loader=importlib.machinery.SourceFileLoader('maintenance_helper_test',str(path))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        self.helper=importlib.util.module_from_spec(spec);loader.exec_module(self.helper)

    def archive(self, name, data, digest=None):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('manifest.json',json.dumps({'format':'microduck-backup/v1',
                'files':{name:digest or hashlib.sha256(data).hexdigest()}}))
            z.writestr(name,data)
        return buffer.getvalue()

    def test_restore_whitelist_hash_and_path_validation(self):
        name=next(n for n in self.helper.CONFIG if n.endswith('battery-settings.json'))
        _,files,skipped=self.helper.load(self.archive(name,b'{}'))
        self.assertEqual(files,{name:b'{}'})
        self.assertEqual(skipped,[])
        for archive in [self.archive('../escape',b'{}'),self.archive('config/etc/passwd',b'{}'),
                        self.archive(name,b'{}','wrong'),self.archive(name,b'broken')]:
            with self.assertRaises((ValueError,KeyError)):self.helper.load(archive)

    def test_torque_must_be_off_without_conflicting_or_pending_feedback(self):
        hd = {'restore_pending':False,'temporary_gains_applied':False}
        native = {'torque_state_confirmed':False,'hd1910':hd}
        motion = {'mode':'motion','phase':'control','policy_enabled':False,'native':native}
        commissioning = {'mode':'commissioning','phase':'ready','torque_state':'off'}
        cases = [(motion, True), (commissioning, True), ({'torque_state':'off'}, False),
                 ({**commissioning, 'phase':'stop_unconfirmed'}, False),
                 ({**commissioning, 'power_pending':True}, False), ({},False),
                 ({**motion,'torque_state':'on'},False), ({**motion,'policy_enabled':True},False),
                 ({**motion,'torque_state':'off','native':{}},False),
                 ({**motion,'native':{'torque_state_confirmed':False}},True)]
        for flag in hd:
            for value in (True, None, 0):
                cases.append(({**motion,'native':{**native,'hd1910':{**hd,flag:value}}}, False))
        for bus,expected in cases:
            with self.subTest(bus=bus),mock.patch.object(self.helper,'rpc',return_value=bus):
                self.assertEqual(self.helper.off(),expected)

    def test_camera_overlay_is_exported_but_never_restored(self):
        camera=self.helper.CAMERA
        z=io.BytesIO()
        data=bytes([0xd0,0x0d,0xfe,0xed])+b'0'*60
        name=next(n for n in self.helper.CONFIG if n.endswith('battery-settings.json'))
        with zipfile.ZipFile(z,'w') as a:
            a.writestr('manifest.json',json.dumps({'format':'microduck-backup/v1','files':{camera:hashlib.sha256(data).hexdigest(),name:hashlib.sha256(b'{}').hexdigest()}}))
            a.writestr(camera,data);a.writestr(name,b'{}')
        _,files,skipped=self.helper.load(z.getvalue())
        self.assertEqual(files,{name:b'{}'})
        self.assertEqual(skipped,[camera])

    def test_robotd_toml_cannot_add_new_keys(self):
        with self.assertRaises(ValueError):
            self.helper.keys_subset({'a':{'b':1,'new':2}},{'a':{'b':0}})
        with self.assertRaises(ValueError):
            self.helper.keys_subset({'a':1},{'x':1})
        self.helper.keys_subset({'a':{'b':3}},{'a':{'b':0,'c':1},'z':1})

    def test_missing_robotd_schema_blocks_restore(self):
        name=next(n for n in self.helper.CONFIG if n.endswith('robotd.toml'))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(self.helper.CONFIG,{name:Path(tmp)/'missing.toml'}):
            with self.assertRaisesRegex(ValueError,'robotd.toml'):
                self.helper.load(self.archive(name,b'x = 7'))

    @unittest.skipUnless(os.name=='posix','Linux fixed-path I/O')
    def test_read_config_never_follows_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=Path(tmp)/'original'
            original.write_bytes(b'{}')
            symbolic=Path(tmp)/'state.json'
            symbolic.symlink_to(original)
            with self.assertRaises(OSError):self.helper.read_config(symbolic)
            os.link(original,Path(tmp)/'hardlinked')
            with self.assertRaises(ValueError):self.helper.read_config(original)

    def test_existing_sudo_launcher_dispatches_only_the_fixed_preview(self):
        from studio import maintenance
        with mock.patch.object(maintenance.subprocess,'run') as command:
            command.return_value.returncode=0
            maintenance.run('preview',b'data')
            self.assertEqual(command.call_args.args[0],['/usr/bin/sudo','-n','/usr/local/sbin/microduck-helper','maintenance-preview'])
            self.assertEqual(command.call_args.kwargs['input'],b'data')

    def test_final_safety_recheck_and_dispatch_failure_restore_original_config(self):
        from contextlib import ExitStack
        import types
        name='config/var/lib/microduck-studio/battery-settings.json'
        for failure in ('off', 'restart', 'studio', 'update', 'health'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp, ExitStack() as mocks:
                root=Path(temp); backups=root/'backup';backups.mkdir()
                config=root/'battery.json';config.write_bytes(b'{"old":true}')
                applied=b'{"new":true}'
                archive=self.archive(name,applied)
                stdin=types.SimpleNamespace(buffer=io.BytesIO(archive))
                stopped=set();jobs=[]
                def unit_state(unit):
                    if unit=='gamesir-xboxd.service':return 'absent'
                    return 'inactive' if unit in stopped else 'active'
                def systemctl(*args):
                    jobs.append((args,config.read_bytes()))
                    if args[0]=='stop':stopped.add(args[1])
                    if args[0]=='start':stopped.discard(args[1])
                    if args==('restart','robotd.service') and failure=='restart' and config.read_bytes()==applied:
                        raise OSError('restart rejected')
                    if args==('restart','microduck-studio.service') and failure=='studio' and config.read_bytes()==applied:
                        raise OSError('studio restart rejected')
                def healthy():
                    if failure=='health' and config.read_bytes()==applied:raise ValueError('unhealthy robotd')
                marker=root/'installing'
                def sync():
                    if failure=='update':marker.touch()
                m=self.helper
                for attr,val in [('UPDATING',marker),('studio_ready',lambda: None),('unit_state',unit_state),
                                 ('systemctl',systemctl),('verify_restarted',healthy),('CONFIG',{name:config})]:
                    mocks.enter_context(mock.patch.object(m,attr,val))
                mocks.enter_context(mock.patch.object(m.os,'sync',sync))
                mocks.enter_context(mock.patch.object(m.sys,'stdin',stdin))
                mocks.enter_context(mock.patch.object(m.sys,'argv',['helper','restore']))
                tempdir=tempfile.mkdtemp
                mocks.enter_context(mock.patch.object(m.tempfile,'mkdtemp',side_effect=lambda prefix,dir:tempdir(prefix=prefix,dir=backups)))
                mocks.enter_context(mock.patch.object(m,'off',side_effect=[True, failure!='off', True]))
                with self.assertRaises((OSError,ValueError)):m.main()
                self.assertEqual(config.read_bytes(),b'{"old":true}')
                self.assertEqual(stopped,set())
                restarts=[item for item in jobs if item[0][0]=='restart']
                if failure in ('off','update'):self.assertEqual(restarts,[])
                else:self.assertIn((('restart','robotd.service'),b'{"old":true}'),restarts)

    def test_shutdown_checks_again_after_sync_and_restores_controller_on_rejection(self):
        from contextlib import ExitStack
        for final,accepted in [(False,False),(True,True)]:
            with self.subTest(final=final), tempfile.TemporaryDirectory() as tmp, ExitStack() as mocks:
                m=self.helper
                stopped=set();ops=[]
                def ctl(*args):
                    ops.append(args)
                    if args[0]=='stop':stopped.update(args[1:])
                    if args[0]=='start':stopped.difference_update(args[1:])
                    if args[-1]=='poweroff':
                        self.assertEqual(stopped,{'padd.service'})
                        self.assertEqual(probe.call_count,2)
                def state(unit):
                    if unit=='gamesir-xboxd.service':return 'absent'
                    return 'inactive' if unit in stopped else 'active'
                for attr,val in [('UPDATING',Path(tmp)/'installing'),('systemctl',ctl),('unit_state',state),
                                 ('studio_ready',lambda: None),('rpc',lambda method: {})]:
                    mocks.enter_context(mock.patch.object(m,attr,val))
                mocks.enter_context(mock.patch.object(m.os,'sync'))
                mocks.enter_context(mock.patch.object(m.sys,'argv',['helper','shutdown']))
                probe=mocks.enter_context(mock.patch.object(m,'off',side_effect=[True,final]))
                if accepted:
                    with mock.patch('builtins.print'):m.main()
                    self.assertEqual(ops[-1],('--no-block','poweroff'))
                else:
                    with self.assertRaises(ValueError):m.main()
                    self.assertNotIn(('--no-block','poweroff'),ops)
                    self.assertEqual(stopped,set())

    @unittest.skipUnless(os.name=='posix','requires flock')
    def test_held_root_motion_lock_blocks_studio_until_release(self):
        import fcntl
        from studio.updates import Updates
        with tempfile.TemporaryDirectory() as tmp:
            updates=Updates(marker=Path(tmp)/'installing',native_state=lambda:{})
            with (Path(tmp)/'motion.lock').open('wb') as gate:
                self.assertFalse(updates.installing())
                fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertTrue(updates.installing())
            self.assertFalse(updates.installing())

    @unittest.skipUnless(os.name=='posix','Linux service users')
    def test_new_files_are_readable_by_the_stock_consumers(self):
        import grp,pwd,types
        robot_gid=981;padd_uid=os.getuid();padd_gid=os.getgid()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(grp,'getgrnam',return_value=types.SimpleNamespace(gr_gid=robot_gid)), mock.patch.object(pwd,'getpwnam',return_value=types.SimpleNamespace(pw_uid=padd_uid,pw_gid=padd_gid)):
            profile=Path(tmp)/'padd'/'mappings.json'
            self.assertEqual(self.helper.new_metadata('config/var/lib/padd/mappings.json',profile),(0o600,(padd_uid,padd_gid)))
            self.assertEqual(profile.parent.stat().st_uid,padd_uid)
            for name in ('hd1910-calibration.toml','hd1910-calibration.saved.toml','pad-settings.json'):
                path=Path('/etc/robot/feetech-ft5')/name
                self.assertEqual(self.helper.new_metadata('config'+str(path),path),(0o644,(0,robot_gid)))

    def test_receipt_survives_web_restart_and_refuses_replay(self):
        import types
        key=b'a'*32
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(self.helper,'RESULTS',Path(tmp)/'receipts'):
            def call():
                with mock.patch.object(self.helper.sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(key+b'\n'))):
                    return self.helper.restore_with_receipt()
            path=Path(tmp)/'receipts'/(key.decode()+'.json')
            def restarted():
                self.assertEqual(json.loads(path.read_bytes())['phase'],'running')
                return {'ok':True,'restored':1,'backup':'/var/backups/example'}
            with mock.patch.object(self.helper,'main',side_effect=restarted) as action:
                mask=os.umask(0o077)
                try:self.assertEqual(call()['phase'],'completed')
                finally:os.umask(mask)
                if os.name=='posix':
                    self.assertEqual(path.parent.stat().st_mode&0o777,0o755)
                    self.assertEqual(path.stat().st_mode&0o777,0o644)
                with self.assertRaises(ValueError):call()
                self.assertEqual(action.call_count,1)
            self.assertEqual(json.loads(path.read_bytes())['restored'],1)
            path.unlink()
            with mock.patch.object(self.helper,'main',side_effect=ValueError('dispatch failed')):
                with self.assertRaises(ValueError):call()
            self.assertEqual(json.loads(path.read_bytes())['phase'],'failed')

    def test_status_query_reads_authenticated_matching_receipt(self):
        import asyncio
        from studio import maintenance
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(maintenance,'RESULTS',Path(tmp)):
            token='e'*32
            (Path(tmp)/(token+'.json')).write_text(json.dumps({'id':token,'phase':'completed','restored':2}))
            auth=mock.Mock()
            handler=maintenance.make_handler(auth,lambda r:(None,{'csrf':'ok'}),lambda r:True)
            def query(key):
                return asyncio.run(handler(Request({'type':'http','method':'GET','headers':[],'query_string':('restore='+key).encode()})))
            auth.has_password.return_value=False
            self.assertEqual(query(token).status_code,401)
            auth.has_password.return_value=True
            self.assertEqual(json.loads(query(token).body)['restored'],2)
            self.assertEqual(json.loads(query('f'*32).body)['phase'],'unknown')
            self.assertEqual(query('../x').status_code,400)

    def test_restart_transport_loss_keeps_receipt_lookup_available(self):
        import asyncio
        from studio import maintenance
        for code,payload in [(-15,b''),(1,b''),(1,b'helper disconnected')]:
            with self.subTest(code=code), mock.patch.object(maintenance.subprocess,'run') as command:
                command.return_value.returncode=code;command.return_value.stdout=payload
                with self.assertRaises(ConnectionError):maintenance.run('restore',b'queued')


    def test_post_restart_requires_live_bus_rpc_but_never_requires_attached_hardware(self):
        m=self.helper
        with mock.patch.object(m,'unit_state',return_value='active'), mock.patch.object(m.time,'sleep'), mock.patch.object(m.time,'monotonic',side_effect=[0,0.1,0.8]), mock.patch.object(m,'rpc',return_value={'mode':'motion','phase':'waiting_for_bus'}) as rpc:
            m.verify_restarted(timeout=5)
            self.assertEqual(rpc.call_count,2)
        for bad in ({'mode':'motion','phase':'configuration_error'},{}):
            with self.subTest(bus=bad), mock.patch.object(m,'rpc',return_value=bad):
                with self.assertRaises(ValueError):m.verify_restarted(timeout=0)
