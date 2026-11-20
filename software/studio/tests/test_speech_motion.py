import asyncio,pathlib,sys,types,unittest,threading
from unittest.mock import patch
from studio.ai_tools import RobotTools
from studio.speech_motion import SpeechMotion
class Tests(unittest.IsolatedAsyncioTestCase):
 def make(self):
  tools=types.SimpleNamespace(rd=True,pad=None,task=None,sent=[],speech_mouth_reserved=False,torque=True)
  async def call(m,p=None):
   if m=='robot.busStatus':return {'mode':'motion','phase':'control','homed':True,'torque_state':'on' if tools.torque else 'off','policy_enabled':getattr(tools,'policy',True)}
   tools.sent.append((asyncio.get_running_loop().time(),m,p));return {}
  async def check():pass
  tools.call=call;tools.check_driver=check
  tts=types.SimpleNamespace(cancel=threading.Event(),playback=threading.Event(),task=None)
  return tools,tts,SpeechMotion(tools)
 async def test_one_cycle_per_second_and_reset(self):
  tools,tts,motion=self.make();tts.task=asyncio.create_task(asyncio.sleep(2.2));tts.playback.set()
  with patch('studio.speech_motion.random.uniform',side_effect=lambda a,b:a):
   await motion.start(tts);await tts.task;await motion.task
  mouth=[(t,p['open']) for t,m,p in tools.sent if m=='robot.mouth'];changes=[]
  for t,v in mouth:
   if not changes or changes[-1][1]!=v:changes.append((t,v))
  self.assertGreaterEqual(len(changes),5);self.assertTrue(all(.4<changes[i][0]-changes[i-1][0]<.7 for i in range(1,4)));self.assertEqual(mouth[-1][1],0)
  heads=[p for _,m,p in tools.sent if m=='robot.head'];self.assertTrue(heads);self.assertTrue(all(abs(p['head_yaw'])<=.1 for p in heads));self.assertEqual(heads[-1]['head_yaw'],0)
 async def test_no_audio_or_torque_no_expression(self):
  for playback in [False,True]:
   tools,tts,motion=self.make();tools.torque=False
   if playback:tts.playback.set()
   tts.task=asyncio.create_task(asyncio.sleep(.15));await motion.start(tts);await tts.task;await motion.task;self.assertEqual(tools.sent,[])
 async def test_reserved_mouth_and_head_not_overwritten(self):
  tools,tts,motion=self.make();tools.active_channels={'head','mouth'};tools.task=asyncio.create_task(asyncio.sleep(.5));tts.task=asyncio.create_task(asyncio.sleep(.15));tts.playback.set();await motion.start(tts);await tts.task;await motion.task;tools.task.cancel();self.assertEqual(tools.sent,[])
 async def test_sound_waits_until_audio_finishes(self):
  import tempfile,json
  with tempfile.TemporaryDirectory() as d:
   r=RobotTools(d,True);r.enabled=True;sent=[]
   async def call(m,p=None):
    if m=='robot.busStatus':return {'mode':'motion','phase':'control','torque_state':'on'}
    sent.append(m);return {}
   r.call=call;tts=types.SimpleNamespace(task=asyncio.create_task(asyncio.sleep(.2)),started=threading.Event(),cancel=threading.Event(),status={'state':'playing'});tts.started.set()
   await r.start([{'function':{'name':'duck_sound','arguments':json.dumps({'tag':'chirp'})}}],{},tts);await r.task
   self.assertIn('robot.sound',sent);self.assertTrue(tts.task.done())
 async def test_head_tool_does_not_block_speech_mouth(self):
  tools,tts,motion=self.make();tools.active_channels={'head'};tts.task=asyncio.create_task(asyncio.sleep(.25));tts.playback.set();await motion.start(tts);await tts.task;await motion.task
  self.assertTrue(any(m=='robot.mouth' for _,m,p in tools.sent));self.assertFalse(any(m=='robot.head' for _,m,p in tools.sent))
 async def test_mouth_tool_does_not_block_speech_head(self):
  tools,tts,motion=self.make();tools.active_channels={'mouth'};tts.task=asyncio.create_task(asyncio.sleep(2.2));tts.playback.set()
  with patch('studio.speech_motion.random.uniform',side_effect=lambda a,b:a):await motion.start(tts);await tts.task;await motion.task
  self.assertTrue(any(m=='robot.head' for _,m,p in tools.sent));self.assertFalse(any(m=='robot.mouth' for _,m,p in tools.sent))
 async def test_short_speech_moves_head_and_mouth_together(self):
  tools,tts,motion=self.make();tts.task=asyncio.create_task(asyncio.sleep(.8));tts.playback.set()
  with patch('studio.speech_motion.random.uniform',side_effect=lambda a,b:a):await motion.start(tts);await tts.task;await motion.task
  self.assertTrue(any(m=='robot.head' and p['head_yaw']!=0 for _,m,p in tools.sent));self.assertTrue(any(m=='robot.mouth' and p['open']>0 for _,m,p in tools.sent))
 async def test_home_hold_head_is_prepared_before_expression(self):
  tools,tts,motion=self.make();tools.policy=False;tools.enabled=True;prepared=[]
  async def prepare(marker):prepared.append(True);tools.policy=True;return True
  tools.prepare_speech_head=prepare;tts.task=asyncio.create_task(asyncio.sleep(.8));tts.playback.set()
  with patch('studio.speech_motion.random.uniform',side_effect=lambda a,b:a):await motion.start(tts);await tts.task;await motion.finish(tts)
  self.assertEqual(prepared,[True]);self.assertTrue(any(m=='robot.head' and p['head_yaw']!=0 for _,m,p in tools.sent))
 async def test_no_policy_never_sends_ineffective_head_frames(self):
  tools,tts,motion=self.make();tools.policy=False;tts.task=asyncio.create_task(asyncio.sleep(.8));tts.playback.set()
  await motion.start(tts);await tts.task;await motion.task
  self.assertFalse(any(m=='robot.head' for _,m,p in tools.sent));self.assertTrue(any(m=='robot.mouth' for _,m,p in tools.sent))
if __name__=='__main__':unittest.main()
