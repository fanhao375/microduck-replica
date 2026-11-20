import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from contextlib import nullcontext
import unittest
from unittest import mock
import zipfile
from starlette.requests import Request
from studio.auth import COOKIE
from studio.maintenance import make_handler


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    async def call(self, body, *, session=True, password=True, csrf=True, origin=True, ready=True, method='POST'):
        auth=mock.Mock();auth.has_password.return_value=password
        auth.session.side_effect=lambda token: {'csrf':'test'} if token=='fixture-session' else None
        handler=make_handler(auth, lambda r: origin, lambda: {'ready':ready,'error':'busy'})
        raw=json.dumps(body).encode()
        async def receive():return {'type':'http.request','body':raw,'more_body':False}
        headers=[(b'x-csrf',b'test' if csrf else b'wrong')]
        if session:
            headers.append((b'cookie',f'{COOKIE}=fixture-session'.encode()))
        request=Request({'type':'http','method':method,'headers':headers}, receive)
        return await handler(request)

    async def test_auth_csrf_and_confirmation_prevent_helper_execution(self):
        with mock.patch('studio.maintenance.run') as run:
            for options,status in [({'session':False},401),({'method':'GET','session':False},401),
                                   ({'password':False},401),({'origin':False},403),({'csrf':False},403),({},400)]:
                response=await self.call({'op':'shutdown'},**options)
                self.assertEqual(response.status_code,status)
                self.assertEqual(response.headers['cache-control'],'no-store')
            run.assert_not_called()

    async def test_busy_device_blocks_changes_and_archive_size_is_checked_before_helper(self):
        with mock.patch('studio.maintenance.run') as run, mock.patch('studio.maintenance.ARCHIVE_LIMIT',2):
            response=await self.call({'op':'restore','confirmed':True,'archive':'e30='},ready=False)
            self.assertEqual(response.status_code,409)
            response=await self.call({'op':'preview','archive':base64.b64encode(b'123').decode()})
            self.assertEqual(response.status_code,413)
            run.assert_not_called()

    async def test_authenticated_download_sets_private_attachment_headers(self):
        with mock.patch('studio.maintenance.run',return_value=b'fixture ZIP') as run:
            response=await self.call(None,method='GET')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.body,b'fixture ZIP')
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn('attachment',response.headers['content-disposition'])
            run.assert_called_once_with('export')

    async def test_preview_and_shutdown_use_fixed_operations(self):
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            response=await self.call({'op':'preview','archive':base64.b64encode(b'zip').decode()})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('preview',b'zip')
        with mock.patch('studio.maintenance.run',return_value=b'{"ok":true}') as run:
            response=await self.call({'op':'shutdown','confirmed':True})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('shutdown',None)


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
        _,files=self.helper.load(self.archive(name,b'{}'))
        self.assertEqual(files,{name:b'{}'})
        for archive in [self.archive('../escape',b'{}'),self.archive('config/etc/passwd',b'{}'),
                        self.archive(name,b'{}','wrong'),self.archive(name,b'broken')]:
            with self.assertRaises((ValueError,KeyError)):self.helper.load(archive)

    def test_export_respects_import_size_including_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            settings=root/'settings.json'
            settings.write_bytes(b'{}')
            with mock.patch.object(self.helper,'CONFIG',{'config/settings.json':settings}), \
                 mock.patch.object(self.helper,'ROOT',root), mock.patch.object(self.helper,'MAX',100), \
                 mock.patch.object(self.helper.sys,'stdout',buffer=io.BytesIO()) as out, self.assertRaises(ValueError):
                self.helper.export()
            self.assertEqual(out.buffer.getvalue(),b'')

    def test_torque_must_be_off_without_conflicting_or_pending_feedback(self):
        motion={'mode':'motion','phase':'control','policy_enabled':False,
                'native':{'torque_state_confirmed':False,'hd1910':{'restore_pending':False,'temporary_gains_applied':False}}}
        idle={'mode':'commissioning','phase':'ready','torque_state':'off'}
        cases=[(motion,True),(idle,True),({**motion,'policy_enabled':True},False),
               ({**motion,'torque_state':'all_enabled'},False),
               ({**motion,'native':{'torque_state_confirmed':False}},False),
               ({**idle,'phase':'stop_unconfirmed'},False),({**idle,'hold':{'active':True}},False),
               ({**idle,'native':{'torque_state_confirmed':True}},False),({**idle,'power_pending':True},False),
               ({'torque_state':'off'},False),({},False)]
        for bus,expected in cases:
            with self.subTest(bus=bus),mock.patch.object(self.helper,'rpc',return_value=bus):
                self.assertEqual(self.helper.off(),expected)

    def test_private_round_trip_omits_login_state_and_preview_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            name='config/var/lib/microduck-studio/tts-settings.json'
            self.assertIn(name,self.helper.CONFIG)
            self.assertFalse(any(Path(n).name in ('auth.json','sessions.json','password.json') for n in self.helper.CONFIG))
            settings=b'{"api_key":"fixture-only"}'
            (state/'tts-settings.json').write_bytes(settings)
            (state/'sessions.json').write_text('{}')
            with mock.patch.object(self.helper,'CONFIG',{name:state/'tts-settings.json'}), \
                 mock.patch.object(self.helper,'ROOT',state), \
                 mock.patch.object(self.helper.sys,'stdout',buffer=io.BytesIO()) as out:
                self.helper.export()
                archive=out.buffer.getvalue()
                manifest,restored=self.helper.load(archive)
                self.assertEqual(restored,{name:settings})
                self.assertEqual(set(manifest['files']),{name})
            with mock.patch.object(self.helper,'CONFIG',{name:state/'tts-settings.json'}), \
                 mock.patch.object(self.helper.sys,'argv',['helper','preview']), \
                 mock.patch.object(self.helper.sys,'stdin',buffer=io.BytesIO(archive)), \
                 mock.patch.object(self.helper.sys,'stdout',new_callable=io.StringIO) as out:
                self.helper.main()
                self.assertEqual(json.loads(out.getvalue())['files'],[name])
                self.assertNotIn('fixture-only',out.getvalue())

    @unittest.skipUnless(os.name=='posix','POSIX atomic replacement and ownership')
    def test_restore_rolls_back_on_service_setup_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            existing=root/'existing.json'
            new=root/'new.json'
            existing.write_bytes(b'{"old":true}')
            if os.geteuid()==0:
                os.chown(existing,65534,65534)
            original_owner=(existing.stat().st_uid,existing.stat().st_gid)
            existing.chmod(0o640)
            targets={'config/existing.json':existing,'config/new.json':new}
            with mock.patch.object(self.helper,'CONFIG',targets), mock.patch.object(self.helper,'BACKUPS',root), \
                 mock.patch.object(self.helper,'off',return_value=True), \
                 mock.patch.object(self.helper.subprocess,'run',side_effect=OSError('unit unavailable')), \
                 self.assertRaises((ValueError,OSError)):
                self.helper.restore({name:b'{}' for name in targets})
            self.assertEqual(existing.read_bytes(),b'{"old":true}')
            self.assertEqual(existing.stat().st_mode & 0o777,0o640)
            self.assertEqual((existing.stat().st_uid,existing.stat().st_gid),original_owner)
            self.assertFalse(new.exists())

    @unittest.skipUnless(os.name=='posix','POSIX atomic replacement and ownership')
    def test_post_replace_error_rolls_back_the_current_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            existing=root/'settings.json'
            existing.write_bytes(b'{"previous":true}')
            name='config/settings.json'
            original_sync=self.helper.sync_dir
            failed=False
            def fail_once(path):
                nonlocal failed
                if path==root and existing.read_bytes()==b'{}' and not failed:
                    failed=True
                    raise OSError('sync failed')
                original_sync(path)
            with mock.patch.object(self.helper,'CONFIG',{name:existing}), mock.patch.object(self.helper,'BACKUPS',root), \
                 mock.patch.object(self.helper,'off',return_value=True), mock.patch.object(self.helper,'system_command') as queue, \
                 mock.patch.object(self.helper,'sync_dir',side_effect=fail_once), self.assertRaises(OSError):
                self.helper.restore({name:b'{}'})
            self.assertTrue(failed)
            self.assertEqual(existing.read_bytes(),b'{"previous":true}')
            queue.assert_not_called()

    def test_scheduled_actions_recheck_torque_before_systemctl(self):
        for op in ('poweroff-if-relaxed','restart-if-relaxed','restart-tof-if-relaxed'):
            with self.subTest(op=op), mock.patch.object(self.helper.sys,'argv',['helper',op]), \
                 mock.patch.object(self.helper,'device_lock',return_value=nullcontext()), \
                 mock.patch.object(self.helper.os,'sync',create=True), \
                 mock.patch.object(self.helper,'off',return_value=False) as check, \
                 mock.patch.object(self.helper,'system_command') as command:
                with self.assertRaises(ValueError):
                    self.helper.main()
                check.assert_called_once_with()
                command.assert_not_called()

    @unittest.skipUnless(os.name=='posix','POSIX atomic replacement and ownership')
    def test_restore_returns_only_after_backup_and_restart_scheduling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            existing=root/'battery-settings.json'
            existing.write_bytes(b'{"old":true}')
            name='config/battery-settings.json'
            with mock.patch.object(self.helper,'CONFIG',{name:existing}), mock.patch.object(self.helper,'BACKUPS',root), \
                 mock.patch.object(self.helper,'off',return_value=True), mock.patch.object(self.helper,'system_command') as queue:
                result=self.helper.restore({name:b'{"new":true}'})
            self.assertEqual((Path(result['backup'])/name).read_bytes(),b'{"old":true}')
            recovery=json.loads((Path(result['backup'])/'manifest.json').read_text())
            self.assertEqual(recovery['originals'][name]['sha256'],hashlib.sha256(b'{"old":true}').hexdigest())
            self.assertEqual(existing.read_bytes(),b'{"new":true}')
            self.assertIn('--on-active=4s',queue.call_args.args)
            self.assertEqual(queue.call_args.args[-1],'restart-if-relaxed')
