"""Real HTTP authorization, private keys, bounded input and revision binding."""
import asyncio
import json
import tempfile
import unittest
import io
import zipfile
from pathlib import Path
from unittest import mock

from tests.test_http import Auth, FakeRobotd, FIXTURE, Log, PW, Robotd, WEB, build_app, uvicorn
from tests.test_models_http import request_raw


class AiHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Log(Path(self.tmp.name)/'logs', echo=False, background=False)
        self.auth = Auth(Path(self.tmp.name)/'state', self.log)
        self.auth.set_password(PW)
        self.fake = FakeRobotd(FIXTURE)
        self.fake_server, port = await self.fake.serve_tcp()
        self.rd = Robotd(f'tcp:127.0.0.1:{port}', f'tcp:127.0.0.1:{port}', self.log)
        app = build_app(auth=self.auth, robotd=self.rd, log=self.log, web_dir=WEB,
                        duck={'label':'测试鸭'}, state_dir=self.tmp.name)
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0, ws='wsproto',
            http='h11', lifespan='on', log_level='critical'))
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        _, headers, _ = await self.call('POST','/api/login',{'password':PW},{'Origin':self.origin})
        self.headers = {'Cookie':headers['set-cookie'].split(';')[0], 'Origin':self.origin}
        _,_,session = await self.call('GET','/api/session',headers=self.headers)
        self.headers['X-CSRF'] = session['csrf']

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.task
        self.fake_server.close()
        await self.fake_server.wait_closed()
        self.tmp.cleanup()

    async def call(self,*args,**kwargs):
        return await asyncio.to_thread(request_raw,self.port,*args,**kwargs)

    async def save(self,**fields):
        _,_,payload = await self.call('POST','/api/ai',{'op':'save','connection':{
            'name':'test','provider':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
            'model':'manual','auth_mode':'bearer','api_key':'test-private-key',**fields}},self.headers)
        return payload

    async def test_authorization_csrf_origin_password_and_no_store(self):
        for method in ('GET','POST'):
            status,headers,_ = await self.call(method,'/api/ai',{'op':'select','id':None} if method=='POST' else None)
            self.assertEqual(status,401)
            self.assertEqual(headers['cache-control'],'no-store')
        for headers in ({**self.headers,'Origin':'http://evil.example'},
                        {k:v for k,v in self.headers.items() if k != 'X-CSRF'}):
            status,_,_ = await self.call('POST','/api/ai',{'op':'select','id':None},headers)
            self.assertEqual(status,403)
        self.auth.clear_password()
        status,_,data = await self.call('GET','/api/ai',headers=self.headers)
        self.assertEqual(status,403)
        self.assertEqual(data['code'],'password_required')

    async def test_all_ai_responses_are_uncacheable_even_wrong_method_or_host(self):
        for method,headers in [('PUT',self.headers),('GET',{'Host':'evil.example'})]:
            status,headers,_ = await self.call(method,'/api/ai',headers=headers)
            self.assertIn(status,(405,421))
            self.assertEqual(headers.get('cache-control'),'no-store')

    async def test_drafts_crud_no_cloud_or_motion_and_secret_never_echoed(self):
        with mock.patch('studio.ai_client.CompatibleClient.complete') as complete:
            data = await self.save(model='')
            self.assertNotIn('test-private-key',json.dumps(data))
            ident = data['saved_id']
            item = data['connections'][0]
            status,_,_ = await self.call('POST','/api/ai',{'op':'test','id':ident,'revision':item['revision']},self.headers)
            self.assertEqual(status,400)
            status,_,_ = await self.call('POST','/api/ai',{'op':'select','id':ident},self.headers)
            self.assertEqual(status,400)
            status,_,data = await self.call('POST','/api/ai',{'op':'delete','id':ident},self.headers)
            self.assertEqual(status,200)
            self.assertIsNone(data['current_id'])
            complete.assert_not_called()
        self.assertFalse(any(method in ('robot.enable','robot.init','robot.head','robot.pose','robot.move')
            for _,method in self.fake.calls))

    async def test_diagnostics_and_logs_exclude_credentials_and_configuration_file(self):
        await self.save()
        status,_,data = await self.call('GET','/api/logs',headers=self.headers)
        self.assertEqual(status,200)
        self.assertNotIn('test-private-key',json.dumps(data))
        # The diagnostic archive contains an explicit whitelist, not the state directory.
        from studio.app import _diag_zip
        archive = _diag_zip(self.log, {'safe':'snapshot'}, {'safe':'raw'})
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            self.assertFalse(any('ai-connections' in name for name in bundle.namelist()))
            for name in bundle.namelist():
                self.assertNotIn(b'test-private-key',bundle.read(name))

    async def test_test_chat_and_stale_completion(self):
        data = await self.save()
        item = data['connections'][0]
        body = {'op':'test','id':item['id'],'revision':item['revision']}
        with mock.patch('studio.ai_client.CompatibleClient.complete',return_value='echo test-private-key') as complete:
            status,headers,result = await self.call('POST','/api/ai',body,self.headers)
            self.assertEqual(status,200,result)
            self.assertEqual(headers['cache-control'],'no-store')
            self.assertNotIn('test-private-key',json.dumps(result))
            self.assertEqual(complete.call_args.args[0]['model'],'manual')
            self.assertEqual(complete.call_args.kwargs['max_tokens'],64)
            status,_,result = await self.call('POST','/api/ai',{**body,'op':'chat','messages':[{'role':'user','content':'hi'}]},self.headers)
            self.assertEqual(status,200,result)
        # Change the on-disk config from a second store while the mock request is in flight.
        def change(*args,**kwargs):
            from studio.ai_connections import Connections
            Connections(self.tmp.name).save({k:v for k,v in {**item,'model':'changed'}.items()
                if k not in ('ready','missing','key_configured','revision')})
            return 'old result'
        with mock.patch('studio.ai_client.CompatibleClient.complete',side_effect=change):
            status,_,result = await self.call('POST','/api/ai',body,self.headers)
            self.assertEqual(status,409,result)
            self.assertNotIn('old result',json.dumps(result))

    async def test_invalid_bounded_json_and_unknown_fields(self):
        for body in (b'[]',b'broken',b'x'*65537, {'op':'save','connection':{},'surprise':'x'},
                     {'op':'chat','id':'x','revision':True,'messages':[]}):
            status,_,result = await self.call('POST','/api/ai',body,self.headers)
            self.assertIn(status,(400,404,413))
            self.assertFalse(result['ok'])

    async def test_camera_is_authorized_for_one_turn_and_checks_revision_before_upload(self):
        item=(await self.save())['connections'][0]
        body={'op':'chat','id':item['id'],'revision':item['revision'],'messages':[{'role':'user','content':'look'}]}
        call={'function':{'name':'duck_camera','arguments':'{}'}}
        with mock.patch('studio.ai_client.CompatibleClient.complete',return_value='hello') as model, \
             mock.patch('studio.camera_capture.capture') as capture:
            status,_,_=await self.call('POST','/api/ai',body,self.headers)
            self.assertEqual(status,200)
            self.assertNotIn('tools',model.call_args.kwargs)
            capture.assert_not_called()
            model.side_effect=[{'text':'looking','tool_calls':[call]},'seen']
            capture.return_value='data:image/png;base64,TEST'
            status,_,result=await self.call('POST','/api/ai',{**body,'allow_camera':True},self.headers)
            self.assertEqual(status,200,result)
            self.assertEqual(result['text'],'seen')
            self.assertEqual(model.call_args.kwargs['camera_image'],capture.return_value)
            self.assertEqual(capture.call_count,1)
            status,_,_=await self.call('POST','/api/ai',{**body,'allow_camera':'yes'},self.headers)
            self.assertEqual(status,400)
            model.reset_mock();model.side_effect=[{'text':'looking','tool_calls':[call]}]
            from studio.ai_connections import Connections
            capture.side_effect=lambda *_:Connections(self.tmp.name).delete(item['id'])
            status,_,result=await self.call('POST','/api/ai',{**body,'allow_camera':True},self.headers)
            self.assertEqual((status,result['code']),(409,'stale'))
            self.assertEqual(model.call_count,1)

    async def test_tts_preview_authorization_and_saved_key_privacy(self):
        with mock.patch('studio.tts.TtsService.preview', new_callable=mock.AsyncMock,
                        return_value={'mime':'audio/wav','data':'mock-audio'}) as preview:
            status,_,_ = await self.call('POST','/api/ai',{'op':'tts_preview'})
            self.assertEqual(status,401)
            status,_,_ = await self.call('POST','/api/ai',{'op':'tts_preview'},
                {k:v for k,v in self.headers.items() if k != 'X-CSRF'})
            self.assertEqual(status,403)
            preview.assert_not_called()
            status,headers,result = await self.call('POST','/api/ai',{'op':'tts_preview'},self.headers)
            self.assertEqual(status,200)
            self.assertEqual(headers['cache-control'],'no-store')
            self.assertEqual(result['audio']['mime'],'audio/wav')
        settings={'enabled':False,'provider':'volcano','speaker':'custom_voice',
                  'api_key':'private-tts-key','clear_key':False}
        with mock.patch('studio.tts.http.client.HTTPSConnection') as network:
            status,_,result = await self.call('POST','/api/ai',{'op':'tts_save','settings':settings},self.headers)
            self.assertEqual(status,200)
            self.assertNotIn('private-tts-key',json.dumps(result))
            network.assert_not_called()
        from studio.app import _diag_zip
        with zipfile.ZipFile(io.BytesIO(_diag_zip(self.log, {}, {}))) as bundle:
            self.assertFalse(any('tts-settings' in name for name in bundle.namelist()))
            for name in bundle.namelist():
                self.assertNotIn(b'private-tts-key',bundle.read(name))


if __name__ == '__main__':
    unittest.main()
