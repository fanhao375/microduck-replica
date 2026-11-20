import asyncio,base64,io,json,pathlib,sys,tempfile,types,unittest,uuid,wave
from unittest.mock import patch, AsyncMock
from studio.tts import TtsService
from studio.ai_connections import AiError
class Response(io.BytesIO):status=200
class Connection:
 lines=[]
 sock=None
 def connect(self):pass
 def __init__(self,*a,**kw):pass
 def request(self,*a,**kw):pass
 def getresponse(self):return Response(b''.join(json.dumps(v).encode()+b'\n' for v in self.lines))
 def close(self):pass
class Pipe:
 closed=False
 def fileno(self):return 123
 def close(self):self.closed=True
class Player:
 def __init__(self,*a,**kw):self.stdin=Pipe();self.returncode=0
 def poll(self):return 0 if self.stdin.closed else None
 def wait(self,timeout=None):return 0
 def terminate(self):self.stdin.close()
 def kill(self):self.stdin.close()
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=TtsService(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def configure(self):self.s.save({'enabled':True,'speaker':'zh_female_vv_uranus_bigtts','api_key':'private-test-key','clear_key':False})
 async def test_chat_speech_failure_preserves_text_and_test_is_silent(self):
  from studio.ai_service import AiService
  service=AiService(self.tmp.name)
  class Store:
   def private(self,*a):return {'name':'test','base_url':'https://example.com','model':'m','auth_mode':'none','revision':1},0
   def finish(self,g,v):return v
  service.store=Store();service.client.complete=lambda *a,**kw:'你好'
  service.tts.start=AsyncMock(side_effect=AiError('tts_busy','正在播放'))
  request={'op':'chat','id':'a'*24,'revision':1,'messages':[{'role':'user','content':'你好'}]}
  result=await service.handle(request)
  self.assertEqual(result['text'],'你好');self.assertEqual(result['speech']['code'],'tts_busy')
  service.tts.start.reset_mock()
  await service.handle({'op':'test','id':'a'*24,'revision':1})
  service.tts.start.assert_not_called()
  with self.assertRaises(AiError):await service.handle({'op':'tts_preview','text':'unexpected'})
 async def test_device_preview_is_audio_only(self):
  self.configure()
  self.s.on_start=AsyncMock()
  with patch.object(self.s,'_stream'):
   await self.s.start('test');await self.s.task
  self.s.on_start.assert_not_awaited()
 async def test_cancel_before_stream_worker_never_contacts_provider(self):
  self.configure()
  self.s.cancel.set()
  with patch('studio.tts.http.client.HTTPSConnection') as network:
   self.s._stream(self.s._read(),'queued',self.s.cancel)
  network.assert_not_called()
 async def test_optional_studio_state_does_not_prevent_startup(self):
  from studio.ai_service import AiService
  service=AiService(None)
  self.assertFalse(service.tools.enabled)
  with self.assertRaises(AiError) as err:
   await service.handle({'op':'tts_get'})
  self.assertEqual(err.exception.status,503)
 async def test_browser_preview_wav_without_device_player(self):
  from studio.ai_service import AiService
  service=AiService(self.tmp.name)
  for provider,endpoint in [('volcano','/api/v3/tts/unidirectional'),('volcano_agent_plan','/api/v3/plan/tts/unidirectional')]:
   self.s.save({'enabled':False,'speaker':'custom_voice','api_key':'private-test-key','clear_key':False,'provider':provider})
   pcm=b'\x00\x01'*480
   Connection.lines=[{'code':0,'data':base64.b64encode(pcm).decode()},{'code':20000000}]
   with patch('studio.tts.http.client.HTTPSConnection',Connection),patch.object(Connection,'request') as request,patch('studio.tts.subprocess.Popen') as player:
    result=await service.handle({'op':'tts_preview'})
   self.assertEqual(request.call_args.args[:2],('POST',endpoint))
   player.assert_not_called()
   self.assertEqual(result['audio']['mime'],'audio/wav')
   with wave.open(io.BytesIO(base64.b64decode(result['audio']['data'])),'rb') as wav:
    self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getframerate()),(1,2,24000))
    self.assertEqual(wav.readframes(480),pcm)
   self.assertFalse(service.tts.preview_busy)
 async def test_browser_preview_errors_and_key_privacy(self):
  with self.assertRaises(AiError):await self.s.preview()
  self.configure()
  for lines in [[],[{'code':45000000,'message':'private-test-key'}],[{'code':0,'data':'bad-base64'}],[{'code':0,'data':'AA== '},{'code':20000000}]]:
   Connection.lines=lines
   with patch('studio.tts.http.client.HTTPSConnection',Connection):
    with self.assertRaises(AiError) as error:await self.s.preview()
   self.assertNotIn('private-test-key',str(error.exception))
   self.assertFalse(self.s.preview_busy)
  self.s.preview_busy=True
  with self.assertRaises(AiError) as error:await self.s.preview()
  self.assertEqual(error.exception.code,'tts_busy')
 async def test_legacy_config_keeps_agent_plan(self):
  self.s.path.write_text(json.dumps({'enabled':True,'speaker':'custom_voice','api_key':'legacy-key'}))
  self.assertEqual(self.s.view()['provider'],'volcano_agent_plan')
  self.s.save({'enabled':True,'speaker':'custom_voice','api_key':'','clear_key':False})
  self.assertEqual(self.s._read()['api_key'],'legacy-key')
 async def test_switch_provider_requires_own_key(self):
  self.configure()
  settings={'enabled':True,'speaker':'custom_voice','api_key':'','clear_key':False,'provider':'volcano'}
  with self.assertRaises(AiError):self.s.save(settings)
  self.assertEqual(self.s.view()['provider'],'volcano_agent_plan')
  self.s.save({**settings,'enabled':False})
  self.assertFalse(self.s.view()['key_configured'])
  self.s.save({**settings,'api_key':'standard-key'})
  self.assertEqual(self.s.view()['provider'],'volcano')
  with self.assertRaises(AiError):self.s.save({**settings,'provider':'unknown'})
 async def test_provider_endpoints(self):
  for provider,endpoint in [('volcano','/api/v3/tts/unidirectional'),('volcano_agent_plan','/api/v3/plan/tts/unidirectional')]:
   self.s.save({'enabled':True,'speaker':'custom_voice','api_key':'test-key','clear_key':False,'provider':provider})
   Connection.lines=[]
   with patch('studio.tts.http.client.HTTPSConnection',Connection),patch.object(Connection,'request') as request:
    await self.s.start('你好');await self.s.task
   self.assertEqual(request.call_args.args[:2],('POST',endpoint))
   self.assertEqual(request.call_args.kwargs['headers']['X-Api-Key'],'test-key')
   self.assertEqual(request.call_args.kwargs['headers']['X-Api-Resource-Id'],'seed-tts-2.0')
   request_id=request.call_args.kwargs['headers']['X-Api-Request-Id']
   self.assertEqual(str(uuid.UUID(request_id)),request_id)
   payload=json.loads(request.call_args.kwargs['body'])
   self.assertEqual(payload['req_params'],{'text':'你好','speaker':'custom_voice','audio_params':{'format':'pcm','sample_rate':24000}})
 async def test_doubao_stream(self):
  self.s.save({'enabled':True,'speaker':'custom_voice','api_key':'speech-key','clear_key':False,'provider':'volcano'})
  Connection.lines=[{'code':0,'data':base64.b64encode(b'\x00'*960).decode()},{'code':20000000}]
  with patch('studio.tts.http.client.HTTPSConnection',Connection),patch('studio.tts.subprocess.Popen',Player),patch('studio.tts.os.set_blocking',create=True),patch('studio.tts.select.select',side_effect=lambda a,b,c,d:([],b,[])),patch('studio.tts.os.write',side_effect=lambda fd,data:len(data)):
   await self.s.start('你好');await self.s.task
  self.assertEqual(self.s.view()['status']['state'],'done')
  self.assertEqual(self.s.view()['status']['bytes'],960)
 async def test_key_and_disabled(self):
  self.assertEqual(await self.s.start('hi',automatic=True),{'started':False,'reason':'disabled'})
  with self.assertRaises(AiError):await self.s.start('hi')
  self.configure();self.assertNotIn('private-test-key',json.dumps(self.s.view()))
  self.s.save({'enabled':True,'speaker':'custom_voice','api_key':'','clear_key':False});self.assertEqual(self.s._read()['api_key'],'private-test-key')
 async def test_stream(self):
  self.configure();Connection.lines=[{'code':0,'data':base64.b64encode(b'\x00'*960).decode()},{'code':20000000}];written=[]
  with patch('studio.tts.http.client.HTTPSConnection',Connection),patch('studio.tts.subprocess.Popen',Player),patch('studio.tts.os.set_blocking',create=True),patch('studio.tts.select.select',side_effect=lambda a,b,c,d:([],b,[])),patch('studio.tts.os.write',side_effect=lambda fd,data:written.append(data) or len(data)):
   await self.s.start('你好');await self.s.task
  self.assertEqual(sum(map(len,written)),960);self.assertEqual(self.s.view()['status']['state'],'done')
 async def test_errors_no_key_leak(self):
  self.configure()
  for lines in [[],[{'code':45000000,'message':'private-test-key'}],[{'code':0,'data':'bad-base64'}]]:
   Connection.lines=lines
   with patch('studio.tts.http.client.HTTPSConnection',Connection):await self.s.start('hi');await self.s.task
   v=self.s.view();self.assertEqual(v['status']['state'],'error');self.assertNotIn('private-test-key',json.dumps(v))
 async def test_chat(self):
  from studio.ai_service import AiService
  s=AiService(self.tmp.name)
  class Store:
   def private(self,*a):return {'name':'t','base_url':'https://example.com','model':'m','auth_mode':'none','revision':1},0
   def finish(self,g,v):return v
  s.store=Store();s.client.complete=lambda *a,**kw:'你好'
  r=await s.handle({'op':'chat','id':'a'*24,'revision':1,'messages':[{'role':'user','content':'你好'}]})
  self.assertEqual(r['text'],'你好');self.assertEqual(r['speech']['reason'],'disabled')
if __name__=='__main__':unittest.main()

