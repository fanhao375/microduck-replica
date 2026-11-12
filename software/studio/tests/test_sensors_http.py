"""Camera/ToF reads use the normal Studio session; no motion or configuration API."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from tests.test_http import Auth, FakeRobotd, FIXTURE, Log, PW, Robotd, WEB, WsClient, build_app, uvicorn
from tests.test_models_http import request_raw


class SensorStub:
    camera_signaling_port = 9443

    async def camera(self):
        return {'source': 'test', 'source_verified': False, 'signaling_port': self.camera_signaling_port,
                'service_state': 'online', 'service_reason': None}

    async def tof(self):
        return {'state': 'unavailable', 'connected': False, 'reason': '未接 ToF'}


class SensorHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Log(Path(self.tmp.name) / 'logs', echo=False, background=False)
        self.auth = Auth(Path(self.tmp.name) / 'state', self.log)
        self.auth.set_password(PW)
        self.fake = FakeRobotd(FIXTURE)
        self.fake_server, port = await self.fake.serve_tcp()
        self.rd = Robotd(f'tcp:127.0.0.1:{port}', f'tcp:127.0.0.1:{port}', self.log)
        self.sensors = SensorStub()
        app = build_app(auth=self.auth, robotd=self.rd, log=self.log, web_dir=WEB,
                        duck={'label': '测试鸭'}, sensors=self.sensors)
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0, ws='wsproto', http='h11',
                                                  lifespan='on', log_level='critical'))
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.origin = f'http://127.0.0.1:{self.port}'

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.task
        self.fake_server.close()
        await self.fake_server.wait_closed()
        self.tmp.cleanup()

    async def call(self, *args, **kwargs):
        return await asyncio.to_thread(request_raw, self.port, *args, **kwargs)

    async def login(self):
        status, headers, _ = await self.call('POST', '/api/login', {'password': PW}, {'Origin': self.origin})
        self.assertEqual(status, 200)
        return {'Cookie': headers['set-cookie'].split(';')[0]}

    async def test_sensor_reads_require_session_when_password_is_set(self):
        for path in ('/api/camera', '/api/tof'):
            status, _, result = await self.call('GET', path)
            self.assertEqual(status, 401)
            self.assertFalse(result['ok'])

    async def test_authenticated_reads_are_truthful_and_not_cached(self):
        headers = await self.login()
        status, response, result = await self.call('GET', '/api/camera', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(response['cache-control'], 'no-store')
        self.assertFalse(result['data']['source_verified'])
        self.assertEqual(result['data']['service_state'], 'online')
        self.assertIsNone(result['data']['service_reason'])
        status, response, result = await self.call('GET', '/api/tof', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['state'], 'unavailable')
        self.assertEqual(response['cache-control'], 'no-store')

    async def test_camera_status_fields_use_normal_readonly_http_response(self):
        from studio.sensors import Sensors
        backend = Sensors(root=self.tmp.name, camera_addr='none')
        self.sensors.camera = backend.camera
        status, response, result = await self.call('GET', '/api/camera', headers=await self.login())
        self.assertEqual(status, 200)
        self.assertEqual(response['cache-control'], 'no-store')
        self.assertEqual(result['data']['service_state'], 'unknown')
        self.assertTrue(result['data']['service_reason'])
        self.assertFalse(result['data']['source_verified'])

    async def test_media_proxy_uses_configured_camera_signaling_port(self):
        cookie = (await self.login())['Cookie'].split('=', 1)[1]
        client = WsClient(self.port, origin=self.origin, cookie=cookie)
        with patch('studio.app.phone_bridge.pump_media', new_callable=AsyncMock) as pump:
            await client.open('/media-ws')
            try:
                self.assertTrue(client.accepted)
                self.assertEqual(pump.await_count, 1)
                self.assertEqual(pump.await_args.kwargs.get('upstream'), 'ws://127.0.0.1:9443')
            finally:
                client.close()

    async def test_no_password_uses_normal_open_access(self):
        self.auth.clear_password()
        status, _, result = await self.call('GET', '/api/camera')
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])

    async def test_service_failure_is_a_json_error(self):
        async def failed():
            raise OSError('sensor read failed')
        self.sensors.tof = failed
        status, _, result = await self.call('GET', '/api/tof', headers=await self.login())
        self.assertEqual(status, 503)
        self.assertFalse(result['ok'])
        self.assertIn('ToF', result['error'])

    async def test_sensor_endpoints_do_not_accept_mutations(self):
        headers = await self.login()
        for path in ('/api/camera', '/api/tof'):
            status, _, _ = await self.call('POST', path, {'op': 'enable'}, headers)
            self.assertEqual(status, 405)

    async def test_missing_optional_backend_has_clear_unavailable_response(self):
        from starlette.requests import Request
        self.auth.clear_password()
        app = build_app(auth=self.auth, robotd=self.rd, log=self.log, web_dir=WEB, duck={'label':'测试鸭'})
        request = Request({'type':'http','method':'GET','path':'/api/tof','headers':[],
                           'client':('127.0.0.1',1),'server':('127.0.0.1',self.port)})
        endpoint = next(route.endpoint for route in app.app.routes if getattr(route,'path',None)=='/api/tof')
        result = await endpoint(request)
        self.assertEqual(result.status_code,503)
        self.assertIn('未开启',result.body.decode())
        self.assertEqual(result.headers['cache-control'],'no-store')


class SensorCli(unittest.TestCase):
    def test_camera_socket_defaults_to_local_mediad_and_can_be_disabled_or_overridden(self):
        from studio.__main__ import parse
        self.assertEqual(getattr(parse([]), 'camera_socket', None), 'unix:/run/mediad/media.sock')
        self.assertEqual(parse(['--camera-socket', 'none']).camera_socket, 'none')
        self.assertEqual(parse(['--camera-socket', 'tcp:127.0.0.1:17443']).camera_socket, 'tcp:127.0.0.1:17443')

    def test_camera_port_bounds_and_explicit_tof_disable(self):
        from studio.__main__ import parse
        options = parse(['--tof','none','--camera-signaling-port','65535'])
        self.assertEqual(options.tof,'none')
        self.assertEqual(options.camera_signaling_port,65535)
        for value in ('0','65536','-1','not-a-port'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse(['--camera-signaling-port',value])
