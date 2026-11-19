import base64
import hashlib
import importlib.machinery
import importlib.util
import io
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

    def test_torque_must_be_off_without_conflicting_or_pending_feedback(self):
        cases=[({'torque_state':'off'},True),({'torque_state':'on','native':{'torque_state_confirmed':False}},False),
               ({'torque_state':'off','native':{'torque_state_confirmed':True}},False),
               ({'torque_state':'off','power_pending':True},False),({},False)]
        for bus,expected in cases:
            with self.subTest(bus=bus),mock.patch.object(self.helper,'rpc',return_value=bus):
                self.assertEqual(self.helper.off(),expected)
