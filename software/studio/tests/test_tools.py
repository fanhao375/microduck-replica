import asyncio,json,pathlib,sys,tempfile,types,unittest
from studio.ai_tools import RobotTools
from studio.ai_connections import AiError
class Tts:task=None;status={'state':'done'}
class Cmd:
 def __init__(self):self.sent=[];self.mode='motion';self.change=False
 async def call(self,m,p,timeout=2):
  self.sent.append((m,p))
  if m=='robot.busStatus':return {'mode':self.mode,'phase':'control','torque_state':'on','native':{'torque_state_confirmed':True},'policy_motion_enabled':True,'homed':True}
  if m=='robot.policies':return {'mode':'walk','sitting':False}
  if m=='robot.skills':return {'skills':[{'name':'roulade','duration':.2},{'name':'kick_left'}],'built_in':['sit_toggle']}
  if m=='robot.move' and self.change:self.mode='commissioning'
  return {}
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.cmd=Cmd();self.r=RobotTools(self.tmp.name,types.SimpleNamespace(cmd=self.cmd));self.r.save(True)
  import threading,time
  self.r.pad=types.SimpleNamespace(_lk=threading.RLock(),clock=time.monotonic,_pad_attached=lambda n:(False,None),_lease_holder=lambda n:None)
 def tearDown(self):self.tmp.cleanup()
 def call(self,name,args):return {'function':{'name':name,'arguments':json.dumps(args)}}
 async def test_studio_interlocks_block_direct_robot_output(self):
  def busy():raise AiError('motion_blocked','calibration in progress')
  self.r.check_interlocks=busy
  await self.r.start([self.call('duck_sound',{'tag':'chirp'})],{},Tts());await self.r.task
  self.assertEqual(self.r.status['state'],'error')
  self.assertFalse(any(method=='robot.sound' for method,_ in self.cmd.sent))
 async def test_torque_release_before_model_reply_blocks_late_plan(self):
  self.r.pad._relax_at=2
  with self.assertRaises(AiError) as error:
   await self.r.start([self.call('duck_stand',{})],{},Tts(),relax_marker=1)
  self.assertEqual(error.exception.code,'relaxed')
  self.assertEqual(self.cmd.sent,[])
 async def test_inactive_stop_does_not_interrupt_another_driver(self):
  await self.r.stop()
  self.assertEqual(self.cmd.sent,[])
 async def test_conflicting_controller_and_torque_readback_block_actions(self):
  ops=await self.power_fixture()
  self.r.pad._pad_attached=lambda now:(True,'connected')
  await self.r.start([self.call('duck_stand',{})],{},Tts());await self.r.task
  self.assertEqual(ops,[])
  self.r.pad._pad_attached=lambda now:(False,None)
  async def stale_bus(method,params=None):
   return {'mode':'motion','phase':'control','torque_state':'on','native':{'torque_state_confirmed':False}}
  self.r.call=stale_bus
  with self.assertRaises(AiError):await self.r.guard()

 async def test_shake_is_horizontal(self):
  await self.r.start([self.call('duck_shake',{'amplitude':.2,'seconds':.3})],{},Tts());await self.r.task
  heads=[p for m,p in self.cmd.sent if m=='robot.head']
  self.assertTrue(any(abs(p['head_yaw'])>.05 for p in heads))
  self.assertTrue(all(p['head_pitch']==0 and p['head_roll']==0 and p['neck_pitch']==0 for p in heads))
  self.assertEqual(heads[-1]['head_yaw'],0)
  self.assertEqual(self.r.channels('duck_shake'),{'head'})
  self.assertEqual(self.r.status['state'],'done')
 async def test_spin_full_circle_wrap(self):
  import math
  self.r.pad._speed_limits=lambda:{'v':.3,'w':1.5}
  values=iter([(i,math.atan2(math.sin(i*.4),math.cos(i*.4))) for i in range(18)])
  self.r.spin_sample=lambda:next(values)
  await self.r.spin('left',Tts(),0)
  self.assertGreaterEqual(self.r.status['results'][-1]['degrees'],360)
  moves=[p for m,p in self.cmd.sent if m=='robot.move'];self.assertGreater(len(moves),10)
  self.assertTrue(all(p=={'vx':0,'vy':0,'vyaw':1.5} for p in moves))
  self.assertEqual(self.cmd.sent[-1][0],'robot.stop')
 async def test_spin_invalid_imu_stops(self):
  self.r.pad._speed_limits=lambda:{'v':.3,'w':1.5}
  count=0
  def sample():
   nonlocal count
   count+=1
   if count>1:raise AiError('imu_stale','stale')
   return 1,0
  self.r.spin_sample=sample
  with self.assertRaises(AiError):await self.r.spin('right',Tts(),0)
  self.assertEqual(self.cmd.sent[-1][0],'robot.stop')
 async def test_spin_validation(self):
  self.assertEqual(self.r.validate([self.call('duck_spin',{'direction':'right'})],{}),[('duck_spin',{'direction':'right'})])
  with self.assertRaises(AiError):self.r.validate([self.call('duck_spin',{'direction':'forward'})],{})
 async def test_schema_all_runtime_skills(self):
  schemas,skills=await self.r.schemas();self.assertEqual(set(skills),{'roulade','kick_left','sit_toggle'});self.assertIn('duck_skill',[t['function']['name'] for t in schemas])
 async def test_readonly_blocks(self):
  self.cmd.mode='commissioning';await self.r.start([self.call('duck_sound',{'tag':'chirp'})],{},Tts());await self.r.task
  self.assertEqual(self.r.status['state'],'error');self.assertNotIn('robot.sound',[m for m,p in self.cmd.sent])
 async def test_reject_entire_bad_plan(self):
  with self.assertRaises(AiError):await self.r.start([self.call('duck_move',{'vx':999,'vy':0,'vyaw':0,'seconds':1})],{},Tts())
  self.assertEqual(self.cmd.sent,[])
 async def test_mode_change_stops_movement(self):
  self.cmd.change=True;await self.r.start([self.call('duck_move',{'vx':.1,'vy':0,'vyaw':0,'seconds':.3})],{},Tts());await self.r.task
  self.assertEqual(self.r.status['state'],'error');self.assertEqual(self.cmd.sent[-1][0],'robot.stop')
 async def test_stop_pending_speech_never_moves(self):
  t=Tts();t.task=asyncio.create_task(asyncio.sleep(10));await self.r.start([self.call('duck_sound',{'tag':'chirp'})],{},t);await asyncio.sleep(.02);await self.r.stop();t.task.cancel()
  self.assertNotIn('robot.sound',[m for m,p in self.cmd.sent])
 async def test_full_stick_step_one_second(self):
  import threading,time
  self.r.pad=types.SimpleNamespace(_speed_limits=lambda:{'v':.3,'w':1.5},_lk=threading.RLock(),clock=time.monotonic,_pad_attached=lambda n:(False,None),_lease_holder=lambda n:None)
  self.assertEqual(self.r.step_velocity('forward'),{'vx':.3,'vy':0,'vyaw':0,'seconds':1.0})
  start=time.monotonic();await self.r.start([self.call('duck_step',{'direction':'forward'})],{},Tts());await self.r.task
  elapsed=time.monotonic()-start;self.assertGreaterEqual(elapsed,1);self.assertLess(elapsed,1.4)
  frames=[p for m,p in self.cmd.sent if m=='robot.move'];self.assertGreaterEqual(len(frames),9);self.assertTrue(all(p=={'vx':.3,'vy':0,'vyaw':0} for p in frames))
  self.assertEqual(self.cmd.sent[-1][0],'robot.stop');self.assertIn(('robot.pose',{'active':False}),self.cmd.sent)
 async def power_fixture(self):
  self.r.power_tool=types.MethodType(RobotTools.power_tool,self.r)
  cmd=self.cmd;cmd.torque=False;cmd.policy=False;cmd.homed=False;cmd.sitting=False;old=cmd.call
  async def call(m,p,timeout=2):
   if m=='robot.busStatus':return {'mode':cmd.mode,'phase':'control','torque_state':'on' if cmd.torque else 'off','native':{'torque_state_confirmed':cmd.torque},'homed':cmd.homed,'policy_enabled':cmd.policy}
   if m=='robot.policies':return {'sitting':cmd.sitting,'mode':getattr(cmd,'gait','walk')}
   return await old(m,p,timeout)
  cmd.call=call;ops=[]
  def handle(args,by=None,conn=None):
   ops.append(args['op'])
   if args['op']=='stand':cmd.torque=True;cmd.homed=True;cmd.sitting=False
   if args['op']=='drive_mode':cmd.gait=args['expect']
   if args['op']=='walk':cmd.policy=True
   if args['op']=='sit':cmd.sitting=True
   return {'accepted':True}
  import threading,time
  self.r.pad=types.SimpleNamespace(handle=handle,_relax_at=0,_lk=threading.RLock(),clock=time.monotonic,_pad_attached=lambda n:(False,None),_lease_holder=lambda n:None,_speed_limits=lambda:{'v':.3,'w':1.5})
  return ops
 async def test_stand_and_start_from_torque_off(self):
  ops=await self.power_fixture();await self.r.start([self.call('duck_stand',{}),self.call('duck_start_walk',{})],{},Tts());await self.r.task
  self.assertEqual(ops,['stand','walk']);self.assertEqual(self.r.status['state'],'done');self.assertTrue(self.cmd.policy)
  self.assertNotIn('robot.move',[m for m,p in self.cmd.sent])
 async def test_power_tools_blocked_in_calibration_mode(self):
  ops=await self.power_fixture();self.cmd.mode='commissioning';await self.r.start([self.call('duck_stand',{})],{},Tts());await self.r.task
  self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'error')
 async def test_relax_during_voice_cancels_power(self):
  ops=await self.power_fixture();t=Tts();t.task=asyncio.create_task(asyncio.sleep(.05));await self.r.start([self.call('duck_stand',{})],{},t);self.r.pad._relax_at=1;await self.r.task
  self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'error')
 async def test_repeated_start_is_idempotent(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;self.cmd.policy=True
  await self.r.start([self.call('duck_stand',{}),self.call('duck_start_walk',{})],{},Tts());await self.r.task;self.assertEqual(ops,[])
 async def test_nod_and_mouth_run_together(self):
  loop=asyncio.get_running_loop();timeline=[];original=self.cmd.call
  async def call(m,p,timeout=2):
   if m in ('robot.head','robot.mouth'):timeline.append((loop.time(),m,p))
   return await original(m,p,timeout)
  self.cmd.call=call;start=loop.time()
  await self.r.start([self.call('duck_nod',{'amplitude':.15,'seconds':.4}),self.call('duck_mouth',{'opening':.4,'seconds':.4})],{},Tts());await self.r.task
  self.assertLess(loop.time()-start,.7);head=[x for x in timeline if x[1]=='robot.head'];mouth=[x for x in timeline if x[1]=='robot.mouth']
  self.assertLess(abs(head[0][0]-mouth[0][0]),.08);self.assertEqual(head[-1][2]['head_pitch'],0);self.assertEqual(mouth[-1][2]['open'],0);self.assertEqual(self.r.status['state'],'done')
 async def test_sit_from_standing_policy_off(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True
  await self.r.start([self.call('duck_sit',{})],{},Tts());await self.r.task
  self.assertEqual(ops,['walk','sit']);self.assertTrue(self.cmd.sitting);self.assertEqual(self.r.status['state'],'done')
 async def test_already_sitting_does_not_toggle_back(self):
  ops=await self.power_fixture();self.cmd.sitting=True
  await self.r.start([self.call('duck_sit',{})],{},Tts());await self.r.task
  self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'done')
 async def test_legacy_sit_toggle_uses_safe_sit_path(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;self.cmd.policy=True
  await self.r.start([self.call('duck_skill',{'skill':'sit_toggle'})],{'sit_toggle':5},Tts());await self.r.task
  self.assertEqual(ops,['sit']);self.assertEqual(self.r.status['results'][0]['tool'],'duck_sit')
 async def test_negative_ack_is_not_success(self):
  async def refused(*args,**kwargs):return {'accepted':False,'reason':'the policy is not driving'}
  self.cmd.call=refused
  with self.assertRaisesRegex(AiError,'policy is not driving'):await self.r.call('robot.do',{'skill':'sit_toggle'})
 async def test_move_prepares_stand_mode_and_policy_before_velocity(self):
  ops=await self.power_fixture();self.cmd.gait='roller'
  await self.r.start([self.call('duck_move',{'vx':.1,'vy':0,'vyaw':0,'seconds':.2})],{},Tts());await self.r.task
  self.assertEqual(ops,['stand','drive_mode','walk']);self.assertEqual(self.cmd.gait,'walk');self.assertEqual(self.r.status['state'],'done')
  self.assertTrue(any(m=='robot.move' for m,p in self.cmd.sent))
 async def test_face_group_prepares_once_and_preserves_current_gait(self):
  ops=await self.power_fixture();self.cmd.gait='roller'
  await self.r.start([self.call('duck_nod',{'amplitude':.1,'seconds':.2}),self.call('duck_mouth',{'opening':.3,'seconds':.2})],{},Tts());await self.r.task
  self.assertEqual(ops,['stand','walk']);self.assertEqual(self.cmd.gait,'roller');self.assertEqual(self.r.status['state'],'done')
 async def test_skill_catalog_available_while_limp(self):
  await self.power_fixture();schemas,skills=await self.r.schemas();self.assertIn('roulade',skills)
 async def test_refused_prepare_never_sends_velocity(self):
  await self.power_fixture()
  def refuse(*a,**k):return {'accepted':False}
  self.r.pad.handle=refuse
  await self.r.start([self.call('duck_move',{'vx':.1,'vy':0,'vyaw':0,'seconds':.2})],{},Tts());await self.r.task
  self.assertEqual(self.r.status['state'],'error');self.assertFalse(any(m=='robot.move' for m,p in self.cmd.sent))
 async def test_start_walk_alone_also_stands_first(self):
  ops=await self.power_fixture();await self.r.start([self.call('duck_start_walk',{})],{},Tts());await self.r.task
  self.assertEqual(ops,['stand','walk']);self.assertEqual(self.r.status['state'],'done')
 async def test_sit_from_limp_prepares_before_sitting(self):
  ops=await self.power_fixture();await self.r.start([self.call('duck_sit',{})],{},Tts());await self.r.task
  self.assertEqual(ops,['stand','walk','sit']);self.assertTrue(self.cmd.sitting);self.assertEqual(self.r.status['state'],'done')
 async def test_skill_switches_to_roller_and_uses_canonical_controller(self):
  ops=await self.power_fixture();old=self.cmd.call
  async def call(m,p,timeout=2):
   if m=='robot.skills':return {'skills':[{'name':'roller_stand','duration':1}]}
   return await old(m,p,timeout)
  self.cmd.call=call
  await self.r.start([self.call('duck_skill',{'skill':'roller_stand'})],{'roller_stand':.2},Tts());await self.r.task
  self.assertEqual(ops,['stand','drive_mode','walk','skill']);self.assertEqual(self.cmd.gait,'roller');self.assertEqual(self.r.status['state'],'done')
 async def test_other_driver_blocks_automatic_preparation(self):
  ops=await self.power_fixture();self.r.pad._pad_attached=lambda n:(True,None)
  await self.r.start([self.call('duck_step',{'direction':'forward'})],{},Tts());await self.r.task
  self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'error')
 async def test_body_first_plan_keeps_nod_during_speech_and_sit_after(self):
  import threading
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;self.cmd.policy=True
  finished=asyncio.Event();head=asyncio.Event();old=self.cmd.call
  async def call(m,p,timeout=2):
   if m=='robot.head':head.set()
   return await old(m,p,timeout)
  self.cmd.call=call
  t=types.SimpleNamespace(task=asyncio.create_task(finished.wait()),started=threading.Event(),cancel=threading.Event(),status={'state':'playing'});t.started.set()
  try:
   await self.r.start([self.call('duck_sit',{}),self.call('duck_nod',{'amplitude':.1,'seconds':.2})],{},t)
   await asyncio.wait_for(head.wait(),1)
   self.assertFalse(t.task.done());self.assertEqual(ops,[])
   await asyncio.sleep(.25);self.assertEqual(ops,[])
   finished.set();await asyncio.wait_for(self.r.task,1)
   self.assertEqual(ops,['sit']);self.assertEqual(self.r.status['state'],'done')
  finally:finished.set();await t.task
 async def test_speech_cancel_blocks_even_body_preparation(self):
  import threading
  ops=await self.power_fixture();finished=asyncio.Event()
  t=types.SimpleNamespace(task=asyncio.create_task(finished.wait()),started=threading.Event(),cancel=threading.Event(),status={'state':'playing'});t.started.set()
  try:
   await self.r.start([self.call('duck_step',{'direction':'forward'})],{},t)
   await asyncio.sleep(.1);self.assertEqual(ops,[]);self.assertEqual(self.cmd.sent,[])
   t.cancel.set();await asyncio.wait_for(self.r.task,1)
   self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'error')
  finally:finished.set();await t.task
 async def test_body_waits_for_speech_expression_cleanup(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;self.cmd.policy=True;cleaned=[]
  async def finish(tts):
   self.assertTrue(tts.task.done());self.assertEqual(ops,[]);await asyncio.sleep(.05);cleaned.append(True)
  async def release():pass
  self.r.speech_motion=types.SimpleNamespace(finish=finish,release_head=release)
  t=types.SimpleNamespace(task=asyncio.create_task(asyncio.sleep(.1)),status={'state':'playing'})
  await self.r.start([self.call('duck_sit',{})],{},t);await self.r.task
  self.assertEqual(cleaned,[True]);self.assertEqual(ops,['sit'])
 async def test_stand_waits_for_measured_home_not_just_ramp_timer(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;ready=asyncio.Event();old=self.cmd.call
  async def call(m,p,timeout=2):
   r=await old(m,p,timeout)
   if m=='robot.busStatus':r['home_start_guard']={'ready':ready.is_set(),'reason':'not physically home'}
   return r
  self.cmd.call=call
  await self.r.start([self.call('duck_stand',{})],{},Tts());await asyncio.sleep(.15)
  self.assertFalse(self.r.task.done());self.assertEqual(ops,[])
  ready.set();await self.r.task;self.assertEqual(self.r.status['state'],'done')
 async def test_running_policy_does_not_require_home_pose_again(self):
  ops=await self.power_fixture();self.cmd.torque=True;self.cmd.homed=True;self.cmd.policy=True;old=self.cmd.call
  async def call(m,p,timeout=2):
   r=await old(m,p,timeout)
   if m=='robot.busStatus':r['home_start_guard']={'ready':False,'reason':'policy is moving joints'}
   return r
  self.cmd.call=call
  await self.r.start([self.call('duck_stand',{})],{},Tts());await asyncio.wait_for(self.r.task,.5)
  self.assertEqual(ops,[]);self.assertEqual(self.r.status['state'],'done')
if __name__=='__main__':unittest.main()
