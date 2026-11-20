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
            response=await self.call_with({'op':'restore','archive':archive,'confirmed':True,'password':'right'})
            self.assertEqual(response.status_code,200)
            run.assert_called_once_with('restore',b'zip')

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
        cases=[({'torque_state':'off'},True),({'torque_state':'on','native':{'torque_state_confirmed':False}},False),
               ({'torque_state':'off','native':{'torque_state_confirmed':True}},False),
               ({'torque_state':'off','power_pending':True},False),
               ({'mode':'motion','phase':'control','policy_enabled':False,'native':{'torque_state_confirmed':False}},True),
               ({'mode':'motion','phase':'control','policy_enabled':True,'native':{'torque_state_confirmed':False}},False),
               ({'mode':'motion','phase':'control','native':{'torque_state_confirmed':None}},False),({},False)]
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
