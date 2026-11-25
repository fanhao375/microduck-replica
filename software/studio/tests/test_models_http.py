"""Model HTTP boundaries: real ASGI/session checks, bounded streamed input and temporary cleanup."""
import asyncio
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_http import Auth, FakeRobotd, FIXTURE, Log, PW, Robotd, WEB, build_app, uvicorn


def request_raw(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        conn.request(method, path, body=data, headers=headers or {}, encode_chunked=isinstance(data, list))
        response = conn.getresponse()
        raw = response.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {'raw': raw.decode(errors='replace')}
        return response.status, dict(response.getheaders()), payload
    finally:
        conn.close()


class ModelService:
    def __init__(self):
        self.imports, self.operations = [], []

    async def view(self):
        return {'items': [], 'slots': [], 'bindings': [], 'buttons': [],
                'capabilities': {'import': True, 'select': True}}

    async def import_package(self, path, name='', kind=None):
        self.imports.append((Path(path), Path(path).read_bytes(), name))
        self.kinds = getattr(self, 'kinds', []) + [kind]
        return await self.view()

    async def handle(self, args, by=None):
        self.operations.append(args)
        return await self.view()


class ModelsHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Log(Path(self.tmp.name) / 'logs', echo=False, background=False)
        self.auth = Auth(Path(self.tmp.name) / 'state', self.log)
        self.auth.set_password(PW)
        self.fake = FakeRobotd(FIXTURE)
        self.fake_server, port = await self.fake.serve_tcp()
        self.rd = Robotd(f'tcp:127.0.0.1:{port}', f'tcp:127.0.0.1:{port}', self.log)
        self.models = ModelService()
        self.pad=self.make_pad()
        app = build_app(auth=self.auth, robotd=self.rd, log=self.log, web_dir=WEB,
                        duck={'label': '测试鸭'}, state_dir=self.tmp.name, models=self.models,pad=self.pad)
        config = uvicorn.Config(app, host='127.0.0.1', port=0, ws='wsproto', http='h11',
                                lifespan='on', log_level='critical')
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        status, headers, _ = await self.call('POST', '/api/login', {'password': PW}, {'Origin': self.origin})
        self.assertEqual(status, 200)
        cookie = headers['set-cookie'].split(';')[0]
        self.headers = {'Cookie': cookie, 'Origin': self.origin}
        status, _, session = await self.call('GET', '/api/session', headers=self.headers)
        self.assertEqual(status, 200)
        self.headers['X-CSRF'] = session['csrf']

    async def asyncTearDown(self):
        if hasattr(self, 'server'):
            self.server.should_exit = True
            await self.task
        self.fake_server.close()
        await self.fake_server.wait_closed()
        self.tmp.cleanup()

    async def call(self, *args, **kwargs):
        return await asyncio.to_thread(request_raw, self.port, *args, **kwargs)

    def make_pad(self):return None

    async def test_session_required_for_library_and_mutations(self):
        for method, path, body in [('GET', '/api/models', None),
                                   ('POST', '/api/models/op', {'op': 'delete', 'id': 'x'}),
                                   ('POST', '/api/models/import?filename=a.zip', b'zip')]:
            status, _, _ = await self.call(method, path, body, {'Origin': self.origin})
            self.assertEqual(status, 401)
        self.assertFalse(self.models.operations or self.models.imports)

    async def test_cross_origin_and_missing_csrf_are_rejected_before_mutation(self):
        for bad in [dict(self.headers, Origin='http://evil.example.com'),
                    {k: v for k, v in self.headers.items() if k != 'X-CSRF'}]:
            for path, body in [('/api/models/op', {'op': 'delete', 'id': 'x'}),
                               ('/api/models/import?filename=a.zip', b'zip')]:
                status, _, _ = await self.call('POST', path, body, bad)
                self.assertEqual(status, 403)
        self.assertFalse(self.models.operations or self.models.imports)

    async def test_read_and_json_operation_return_service_view(self):
        status, _, payload = await self.call('GET', '/api/models', headers=self.headers)
        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'])
        status, _, payload = await self.call('POST', '/api/models/op', {'op': 'rename', 'id': 'x', 'name': '测试'}, self.headers)
        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'])
        self.assertEqual(self.models.operations[-1]['name'], '测试')

    async def test_upload_is_temporary_and_name_is_not_a_path(self):
        status, _, payload = await self.call('POST', '/api/models/import?filename=package.zip&name=%E6%85%A2%E8%B5%B0',
                                             b'package bytes', self.headers)
        self.assertEqual(status, 200, payload)
        path, data, name = self.models.imports[-1]
        self.assertEqual(data, b'package bytes')
        self.assertEqual(name, '慢走')
        self.assertFalse(path.exists())
        status, _, _ = await self.call('POST', '/api/models/import?filename=../../outside.zip', b'zip', self.headers)
        self.assertEqual(status, 400)
        self.assertEqual(len(self.models.imports), 1)

    async def test_action_import_accepts_single_onnx_only_with_kind(self):
        status, _, payload = await self.call('POST', '/api/models/import?filename=bow.onnx&kind=action', b'onnx', self.headers)
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.models.kinds[-1], 'action')
        self.assertFalse(self.models.imports[-1][0].exists())
        status, _, _ = await self.call('POST', '/api/models/import?filename=crouch.zip&kind=action', b'zip', self.headers)
        self.assertEqual(status, 200)
        self.assertEqual(self.models.kinds[-1], 'action')
        for path in ('/api/models/import?filename=bow.onnx', '/api/models/import?filename=a.zip&kind=evil'):
            status, _, _ = await self.call('POST', path, b'x', self.headers)
            self.assertEqual(status, 400)
        self.assertEqual(len(self.models.imports), 2)

    async def test_oversized_and_malformed_requests_never_reach_service(self):
        with mock.patch('studio.app.MODEL_UPLOAD_LIMIT', 8):
            status, _, _ = await self.call('POST', '/api/models/import?filename=a.zip', b'012345678', self.headers)
            self.assertEqual(status, 413)
        for body in [b'not json', b'[]', b'x' * 32769]:
            status, _, _ = await self.call('POST', '/api/models/op', body, self.headers)
            self.assertIn(status, (400, 413))
        self.assertFalse(self.models.operations or self.models.imports)

    async def test_chunked_requests_are_bounded_without_content_length(self):
        with mock.patch('studio.app.MODEL_UPLOAD_LIMIT', 8):
            status, _, _ = await self.call('POST', '/api/models/import?filename=a.zip',
                                          [b'1234', b'56789'], self.headers)
            self.assertEqual(status, 413)
        status, _, _ = await self.call('POST', '/api/models/op', [b'x' * 16384] * 3, self.headers)
        self.assertEqual(status, 413)
        self.assertFalse(self.models.operations or self.models.imports)
        self.assertFalse(list((Path(self.tmp.name) / 'model-uploads').iterdir()))

    async def test_rejected_package_cleans_temporary_file_and_preserves_error(self):
        async def reject(path, name=''):
            self.assertTrue(path.is_file())
            raise ValueError('模型合同不兼容')
        self.models.import_package = reject
        status, headers, payload = await self.call('POST', '/api/models/import?filename=a.tar.gz', b'archive', self.headers)
        self.assertEqual(status, 400)
        self.assertEqual(payload['error'], '模型合同不兼容')
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertFalse(list((Path(self.tmp.name) / 'model-uploads').iterdir()))

    async def test_deep_json_is_rejected_as_invalid_request(self):
        body = b'{"op":' + b'[' * 10000 + b']' * 10000 + b'}'
        status, _, payload = await self.call('POST', '/api/models/op', body, self.headers)
        self.assertEqual(status, 400)
        self.assertFalse(payload['ok'])
        self.assertFalse(self.models.operations)


class MappingModelsHttp(ModelsHttp):
    def make_pad(self):
        from tests.test_pad import P,FakeConfigd,FakeBt,FakeTap,FakeRobot,FAST
        return P.PadOps(self.log,configd=FakeConfigd(),bt=FakeBt(),tap=FakeTap(),robot=FakeRobot(),
                       waits=FAST,read_text=lambda path:'',exists=lambda path:False,state_dir=self.tmp.name)

    async def test_mapping_blocks_model_writes_and_imports_but_allows_library_reads(self):
        with self.pad.mapping._lk:self.pad.mapping._state={'output_blocked':True}
        for path,body in [('/api/models/op',{'op':'select','id':'x','slot':'walk'}),
                          ('/api/models/import?filename=a.zip',b'archive')]:
            status,_,payload=await self.call('POST',path,body,self.headers)
            self.assertEqual(status,409,payload);self.assertFalse(payload['ok'])
        self.assertFalse(self.models.operations or self.models.imports)
        status,_,_=await self.call('GET','/api/models',headers=self.headers);self.assertEqual(status,200)

    async def test_model_transaction_blocks_mapping_before_native_begin(self):
        from studio.pad_mapping import MappingError
        self.models._lock=asyncio.Lock()
        async with self.models._lock:
            with self.assertRaises(MappingError):self.pad.mapping_guard()
        self.pad.mapping_guard()
