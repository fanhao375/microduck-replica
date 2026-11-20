"""Small speech expressions, gated by live motor state and control ownership."""
import asyncio,random
from .pad import _torque_on

ZERO_HEAD={'neck_pitch':0,'head_pitch':0,'head_yaw':0,'head_roll':0}
class SpeechMotion:
 def __init__(self,tools):
  self.tools=tools;self.task=None;self.head_owned=False;self.mouth_owned=False;self.audio_task=None;self.head_ready=False
 async def stop(self):
  if self.task and not self.task.done():
   self.task.cancel()
   try:await self.task
   except asyncio.CancelledError:pass
 async def start(self,tts):
  await self.stop()
  if not self.tools.enabled:return
  self.audio_task=tts.task
  self.task=asyncio.create_task(self._run(tts,tts.cancel,tts.playback,tts.task,getattr(self.tools.pad,'_relax_at',0)))
 async def finish(self,tts):
  # Audio completion is not expression completion: wait for mouth close/head reset as well.
  if self.audio_task is tts.task and self.task and self.task is not asyncio.current_task():
   await self.task
 async def ready(self,marker,cleanup=False):
  if (not cleanup and not self.tools.enabled) or self.tools.rd is None:return False
  if getattr(self.tools.pad,'_relax_at',0)>marker:return False
  b=await self.tools.call('robot.busStatus')
  if b.get('mode')!='motion' or b.get('phase')!='control' or b.get('homed') is not True:return False
  if not _torque_on(b):return False
  await self.tools.check_driver()
  self.head_ready=b.get('policy_motion_enabled',b.get('policy_enabled')) is True
  return (cleanup or self.tools.enabled) and getattr(self.tools.pad,'_relax_at',0)<=marker
 async def release_head(self):
  if self.head_owned:
   self.head_owned=False
   await self.tools.call('robot.head',ZERO_HEAD)
 async def _run(self,tts,cancel,playback,audio_task,marker):
  start=None;next_head=0;roll=0
  try:
   while self.tools.enabled and not audio_task.done() and not cancel.is_set():
    if not playback.is_set():await asyncio.sleep(.05);continue
    now=asyncio.get_running_loop().time()
    if start is None:start=now;next_head=now+random.uniform(.2,.5)
    if not await self.ready(marker):
     if getattr(self.tools.pad,'_relax_at',0)>marker:break
     await asyncio.sleep(.1);continue
    channels=getattr(self.tools,'active_channels',set())
    if 'mouth' not in channels:
     # One full open/close cycle each second; refresh at 10 Hz.
     opening=.35 if (now-start)%1.0<.5 else 0
     self.mouth_owned=True
     await self.tools.call('robot.mouth',{'open':opening})
    if self.head_ready and 'head' not in channels:
     if now>=next_head:
      roll=random.choice([-1,1])*random.uniform(.06,.10);next_head=now+random.uniform(1,2)
     if roll:
      self.head_owned=True
      await self.tools.call('robot.head',{**ZERO_HEAD,'head_yaw':roll})
    await asyncio.sleep(.1)
  except asyncio.CancelledError:raise
  except Exception:pass  # A refusal must never affect audio playback or enable motors.
  finally:
   try:
    if (self.mouth_owned or self.head_owned) and await self.ready(marker,cleanup=True):
     channels=getattr(self.tools,'active_channels',set())
     if self.mouth_owned and 'mouth' not in channels:
      await self.tools.call('robot.mouth',{'open':0})
     if self.head_owned and 'head' not in channels:await self.release_head()
   except Exception:pass
   self.mouth_owned=False;self.head_owned=False
