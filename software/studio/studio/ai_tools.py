"""Model-selected robot tools. Only explicit schemas; never arbitrary RPC or shell."""
import asyncio,json,math,os,re,tempfile,time
from pathlib import Path
from .ai_connections import AiError
from .pad import _torque_on
SOUNDS=['alarm','greet','inquire','peck','chirp','coo','wheee']

def function(name,description,properties,required):
 return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}
def number(lo,hi):return {'type':'number','minimum':lo,'maximum':hi}

class RobotTools:
 def __init__(self,state_dir,robotd=None,pad=None):
  self.active_channels=set()
  self.check_interlocks=None
  self.pad=pad
  self.path=Path(state_dir)/'ai-tools.json' if state_dir else None;self.rd=robotd;self.task=None;self.status={'state':'idle','results':[]};self.enabled=self._load()
 def _load(self):
  if self.path is None:return False
  try:return json.loads(self.path.read_text())['enabled'] is True
  except FileNotFoundError:return False
  except Exception:return False
 def view(self):return {'enabled':self.enabled,**self.status}
 def save(self,enabled):
  if type(enabled) is not bool:raise AiError('validation','动作开关须为布尔值')
  if self.path is None:raise AiError('state','动作设置存储目录尚未配置',503)
  self.path.parent.mkdir(parents=True,exist_ok=True);fd,p=tempfile.mkstemp(dir=self.path.parent,prefix='.ai-tools-')
  try:
   with os.fdopen(fd,'w') as f:os.chmod(p,0o600);json.dump({'enabled':enabled},f);f.flush();os.fsync(f.fileno())
   os.replace(p,self.path)
  finally:
   if os.path.exists(p):os.unlink(p)
  self.enabled=enabled
  if not enabled and self.task and not self.task.done():self.task.cancel()
  return self.view()
 async def call(self,method,params=None):
  if self.rd is None:raise AiError('robot_unavailable','机器人未连接')
  # Studio's existing locked command connection preserves protocol and deadlines.
  result=await self.rd.cmd.call(method,params or {},timeout=2.0)
  if isinstance(result,dict) and result.get('accepted') is False:
   raise AiError('robot_refused',str(result.get('reason') or '固件拒绝了动作')[:240])
  return result
 async def guard(self,require_torque=True):
  b=await self.call('robot.busStatus')
  if not self.enabled:raise AiError('tools_disabled','AI 动作控制已关闭')
  if b.get('mode')!='motion' or b.get('phase')!='control':raise AiError('motion_mode','当前不是运动控制模式，未执行动作')
  if require_torque and not _torque_on(b):raise AiError('torque_off','当前未使能或准备状态已改变，动作已取消')
  return b
 async def catalog(self):
  if not self.enabled:return [],{}
  try:
   await self.guard(require_torque=False);r=await self.call('robot.skills')
   durations={x['name']:min(20,max(1,float(x.get('duration') or 5))) for x in r.get('skills',[]) if isinstance(x,dict) and isinstance(x.get('name'),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',x['name'])}
   for x in r.get('built_in',[]):
    if isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',x):durations.setdefault(x,5)
   return list(durations),durations
  except Exception:return [],{}
 async def schemas(self,allow_camera=False):
  camera=function('duck_camera','获取摄像头当前图像。用户询问眼前景物、物体、颜色或需要视觉判断时调用；系统会把实时图片传给模型。',{},[])
  visual=[camera] if allow_camera else []
  if not self.enabled:return visual,{}
  skills,durations=await self.catalog()
  tools=[function('duck_move','短时行走或转向。vx前进、vy左移、vyaw左转（负为右转），结束自动停止。自动检查站姿、切到走路模型并开启策略后执行。',{'vx':number(-.15,.15),'vy':number(-.1,.1),'vyaw':number(-.6,.6),'seconds':number(.2,3)},['vx','vy','vyaw','seconds']),function('duck_sound','播放鸭叫声音，在语音回复结束后执行。',{'tag':{'type':'string','enum':SOUNDS}},['tag']),function('duck_stop','停止行走，不卸力。',{},[])]
  tools.extend([function('duck_stand','站起来：运动模式下即使已卸力、策略未开启也可调用；自动沿用中控站立流程，并等站稳。已站立则不重复动作。',{},[]),function('duck_start_walk','开始走：开启当前走路模型的策略，等同中控开始走按钮，不发送前进速度。会自动站起并切到走路模型。',{},[])])
  tools.append(function('duck_spin','原地转完整一圈360度，模拟摇杆持续转向并用IMU累计实际角度，到达后自动停止。转圈必须用此工具，不要用duck_move猜时间。',{'direction':{'type':'string','enum':['left','right']}},['direction']))
  tools.append(function('duck_step','用户说往前走一步/走一小步时优先用此工具。等同网页摇杆向指定方向推到底保持1秒，再回中；不需要自行猜测速度。',{'direction':{'type':'string','enum':['forward','backward','left','right','turn_left','turn_right']}},['direction']))
  tools.append(function('duck_sit','坐下：已坐下时不重复切换；站着但策略关闭时先开启坐站策略再坐下，并读回确认。未就绪时自动沿用中控站立和策略准备流程。',{},[]))
  skill_choices=[n for n in skills if n!='sit_toggle']
  if skill_choices:tools.append(function('duck_skill','执行当前固件提供的动作。roulade为前滚翻，kick_left/right踢腿；只从当前枚举选择。坐下请用duck_sit。',{'skill':{'type':'string','enum':skill_choices}},['skill']))
  tools.append(function('duck_shake','摇头：左右水平转头表示否定，只转head_yaw，不歪头、不侧倾；每秒一次，可与张嘴同时执行。',{'amplitude':number(.05,.25),'seconds':number(.2,3)},['amplitude','seconds']))
  tools.append(function('duck_nod','点头：每秒一次完整点头，可与duck_mouth同时执行。',{'amplitude':number(.05,.25),'seconds':number(.2,3)},['amplitude','seconds']))
  tools.extend([function('duck_head','短时转头，弧度，正yaw向左。',{'yaw':number(-.6,.6),'pitch':number(-.3,.3),'seconds':number(.2,2)},['yaw','pitch','seconds']),function('duck_mouth','短时张嘴，0关闭到1全开。',{'opening':number(0,1),'seconds':number(.2,2)},['opening','seconds'])])
  return visual+tools,durations
 def validate(self,calls,skills):
  if not isinstance(calls,list) or len(calls)>3:raise AiError('tool_plan','每次最多执行3个动作')
  good=[]
  specs={'duck_move':{'vx':(-.15,.15),'vy':(-.1,.1),'vyaw':(-.6,.6),'seconds':(.2,3)},'duck_shake':{'amplitude':(.05,.25),'seconds':(.2,3)},'duck_nod':{'amplitude':(.05,.25),'seconds':(.2,3)},'duck_head':{'yaw':(-.6,.6),'pitch':(-.3,.3),'seconds':(.2,2)},'duck_mouth':{'opening':(0,1),'seconds':(.2,2)}}
  for c in calls:
   f=c.get('function',{});name=f.get('name');a=json.loads(f.get('arguments','{}'))
   if not isinstance(a,dict):raise AiError('tool_plan','动作参数不正确')
   if name in specs:
    if set(a)!=set(specs[name]) or any(type(a[k]) not in (int,float) or not math.isfinite(a[k]) or not lo<=a[k]<=hi for k,(lo,hi) in specs[name].items()):raise AiError('tool_plan','动作参数超出范围')
   elif name=='duck_spin':
    if set(a)!={'direction'} or a['direction'] not in ('left','right'):raise AiError('tool_plan','不支持该转圈方向')
   elif name=='duck_step':
    if set(a)!={'direction'} or a['direction'] not in ['forward','backward','left','right','turn_left','turn_right']:raise AiError('tool_plan','不支持该步进方向')
   elif name=='duck_skill':
    if set(a)!={'skill'} or a['skill'] not in skills:raise AiError('tool_plan','固件不支持该动作')
   elif name=='duck_sound':
    if set(a)!={'tag'} or a['tag'] not in SOUNDS:raise AiError('tool_plan','不支持该声音')
   elif name not in ('duck_stop','duck_stand','duck_start_walk','duck_sit') or a:raise AiError('tool_plan','不支持该工具')
   good.append((name,a))
  return good
 def step_velocity(self,direction):
  if self.pad is None:raise AiError('pad_unavailable','摇杆速度配置尚未就绪')
  lim=self.pad._speed_limits()
  v,w=lim['v'],lim['w']
  if not all(type(x) in (int,float) and math.isfinite(x) and 0<x<=3 for x in (v,w)):raise AiError('limits','摇杆速度配置无效')
  vx,vy,yaw={'forward':(v,0,0),'backward':(-v,0,0),'left':(0,v,0),'right':(0,-v,0),'turn_left':(0,0,w),'turn_right':(0,0,-w)}[direction]
  return {'vx':vx,'vy':vy,'vyaw':yaw,'seconds':1.0}
 async def check_driver(self):
  if self.pad is None:raise AiError('pad_unavailable','中控动作服务未就绪')
  if self.check_interlocks:self.check_interlocks()
  with self.pad._lk:
   now=self.pad.clock()
   for check in ('_base_block','_relax_block'):
    blocked=getattr(self.pad,check,lambda _:None)(now)
    if blocked:raise AiError('motion_blocked',blocked)
   motion=getattr(self.pad,'motion_studio',None)
   if motion and (motion.session or motion.state!='idle'):
    raise AiError('motion_busy','请先结束中控动作会话')
   if self.pad._pad_attached(now)[0] is not False:
    raise AiError('pad_conflict','请先确认并断开实体手柄')
   if self.pad._lease_holder(now) is not None:
    raise AiError('web_conflict','另一个页面正在推摇杆，请先松开')
 async def start(self,calls,skills,tts,relax_marker=None):
  if not self.enabled:raise AiError('tools_disabled','AI 动作控制未开启')
  if self.task and not self.task.done():raise AiError('tools_busy','上一组动作还未结束')
  plan=self.validate(calls,skills)
  self.status={'state':'waiting_speech','results':[]}
  if relax_marker is None:relax_marker=getattr(self.pad,'_relax_at',0)
  self.check_relax(relax_marker)
  self.task=asyncio.create_task(self._run(plan,skills,tts,relax_marker))
  return {'accepted':True,'count':len(plan)}
 async def stop(self):
  if self.task and not self.task.done():
   self.task.cancel()
   try:await self.task
   except asyncio.CancelledError:pass
  self.status.pop('error',None)
  self.status['state']='stopped';return self.view()
 def check_relax(self,marker):
  if not self.enabled:raise AiError('tools_disabled','AI 动作控制已关闭')
  if getattr(self.pad,'_relax_at',0)>marker:raise AiError('relaxed','本轮期间发生卸力，后续动作已取消')
 async def power_tool(self,name,marker):
  if self.pad is None:raise AiError('pad_unavailable','中控动作服务未就绪')
  await self.check_driver()
  self.check_relax(marker);b=await self.guard(require_torque=False)
  pol=await self.call('robot.policies')
  on=b.get('policy_motion_enabled',b.get('policy_enabled')) is True
  if name=='duck_start_walk' and on:return {'already':True}
  standing=(_torque_on(b)) and b.get('homed') is True and pol.get('sitting') is False
  if name=='duck_stand' and standing:
   # homed only confirms the ramp timer, not measured arrival. Never report standing early.
   deadline=asyncio.get_running_loop().time()+15
   while not on and (b.get('home_start_guard') or {}).get('ready') is False:
    self.check_relax(marker)
    if asyncio.get_running_loop().time()>=deadline:raise AiError('home_unconfirmed','尚未实际站稳：'+str((b.get('home_start_guard') or {}).get('reason','HOME 未就绪')))
    await asyncio.sleep(.1);b=await self.guard();pol=await self.call('robot.policies')
    if b.get('homed') is not True or pol.get('sitting') is not False:raise AiError('state_changed','等待站稳时状态已改变，动作已取消')
    on=b.get('policy_motion_enabled',b.get('policy_enabled')) is True
   return {'already':True}
  op='stand' if name=='duck_stand' else 'walk'
  await self.wait_pad_ready({'op':op},marker)
  self.check_relax(marker)
  await self.check_driver()
  try:result=await asyncio.to_thread(self.pad.handle,{'op':op},by='AI 语音',conn=None)
  except Exception as e:raise AiError('power_refused',str(e)[:240]) from None
  if not isinstance(result,dict) or result.get('accepted') is not True:raise AiError('power_refused','中控未确认接受操作，后续动作已取消')
  deadline=asyncio.get_running_loop().time()+15
  while asyncio.get_running_loop().time()<deadline:
   self.check_relax(marker);b=await self.guard(require_torque=False)
   torque=_torque_on(b)
   pol=await self.call('robot.policies')
   if name=='duck_stand' and torque and b.get('homed') is True and pol.get('sitting') is False and ((b.get('home_start_guard') or {}).get('ready') is not False or b.get('policy_motion_enabled',b.get('policy_enabled')) is True):return result
   if name=='duck_start_walk' and torque and b.get('policy_motion_enabled',b.get('policy_enabled')) is True:return result
   await asyncio.sleep(.2)
  raise AiError('power_timeout','未读回站立/行走策略就绪状态，后续动作已取消')
 def check_speech(self,tts):
  if (getattr(tts,'cancel',None) and tts.cancel.is_set()) or tts.status.get('state') in ('error','stopping','idle') and tts.task is not None:
   raise AiError('speech','语音被停止或播放失败，后续动作已取消')
 async def sit_tool(self,marker):
  if self.pad is None:raise AiError('pad_unavailable','中控坐下服务未就绪')
  self.check_relax(marker);b=await self.guard(require_torque=False);pol=await self.call('robot.policies')
  if pol.get('sitting') is True:return {'already':True}
  if pol.get('sitting') is not False:raise AiError('sit_state','读不到当前坐站状态，未发送切换动作')
  if b.get('homed') is not True or not _torque_on(b):
   await self.power_tool('duck_stand',marker);b=await self.guard()
  await self.call('robot.stop');await self.call('robot.pose',{'active':False})
  if b.get('policy_motion_enabled',b.get('policy_enabled')) is not True:await self.power_tool('duck_start_walk',marker)
  # Wait for the canonical UI controller to observe the new policy/zero velocity.
  if hasattr(self.pad,'_motion_view'):
   deadline=asyncio.get_running_loop().time()+3;why=None
   while asyncio.get_running_loop().time()<deadline:
    self.check_relax(marker)
    with self.pad._lk:row=self.pad._motion_view(self.pad.clock())['buttons']['sit']
    if row['ok']:break
    why=row.get('why');await asyncio.sleep(.1)
   else:raise AiError('sit_refused',why or '中控尚未准备好坐下')
  self.check_relax(marker)
  await self.check_driver()
  try:r=await asyncio.to_thread(self.pad.handle,{'op':'sit'},by='AI 语音',conn=None)
  except Exception as e:raise AiError('sit_refused',str(e)[:240]) from None
  if not isinstance(r,dict) or r.get('accepted') is not True:raise AiError('sit_refused','中控未接受坐下')
  deadline=asyncio.get_running_loop().time()+8
  while asyncio.get_running_loop().time()<deadline:
   self.check_relax(marker);await self.guard()
   pol=await self.call('robot.policies')
   if pol.get('sitting') is True:return r
   await asyncio.sleep(.1)
  raise AiError('sit_unconfirmed','坐下请求已发送，但未读回坐下状态，不能确认已执行')
 async def wait_pad_ready(self,args,marker):
  if not hasattr(self.pad,'_motion_view'):return
  deadline=asyncio.get_running_loop().time()+3
  while True:
   self.check_relax(marker)
   with self.pad._lk:
    view=self.pad._motion_view(self.pad.clock())
    if args['op']=='drive_mode':row=view['drive_mode']
    elif args['op']=='skill':row=next((x for x in view['skills'] if x['name']==args['name']),{'ok':False,'why':'当前没有这个技能'})
    else:row=view['buttons'][args['op']]
   if row['ok']:return
   if asyncio.get_running_loop().time()>=deadline:raise AiError('prepare_refused',row.get('why') or '中控尚未准备好执行')
   await asyncio.sleep(.1)
 async def pad_command(self,args,marker):
  if self.pad is None:raise AiError('pad_unavailable','中控动作服务未就绪')
  await self.wait_pad_ready(args,marker)
  self.check_relax(marker)
  await self.check_driver()
  try:r=await asyncio.to_thread(self.pad.handle,args,by='AI 语音',conn=None)
  except Exception as e:raise AiError('prepare_refused',str(e)[:240]) from None
  self.check_relax(marker)
  if not isinstance(r,dict) or r.get('accepted') is not True:raise AiError('prepare_refused','中控未接受模式或动作请求')
  return r
 async def prepare_group(self,group,marker,tts):
  # Prepare once before launching independent head/mouth tasks, never concurrently.
  self.check_relax(marker);self.check_speech(tts)
  b=await self.guard(require_torque=False)
  group=[('duck_sit',a) if name=='duck_skill' and a.get('skill')=='sit_toggle' else (name,a) for name,a in group]
  names={name for name,a in group}
  await self.check_driver()
  if names<= {'duck_sound','duck_stop','duck_stand','duck_sit'}:return
  pol=await self.call('robot.policies')
  if names<={'duck_head','duck_shake','duck_nod','duck_mouth'} and (not _torque_on(b) or b.get('homed') is not True or pol.get('sitting') is True):
   await self.wait_speech(tts,marker,complete=True)
  # Native head/mouth intent slots are independent of the web controller mode.
  # Clear a stale body override so it cannot mask gait/head policy output.
  await self.call('robot.stop');await self.call('robot.pose',{'active':False})
  torque=_torque_on(b)
  if not torque or b.get('homed') is not True or pol.get('sitting') is True:
   await self.power_tool('duck_stand',marker)
  target=None
  if names & {'duck_move','duck_step','duck_spin','duck_start_walk'}:target='walk'
  for name,a in group:
   if name=='duck_skill':target='roller' if a['skill']=='roller_stand' else 'walk'
  if target:
   pol=await self.call('robot.policies')
   if pol.get('mode') not in ('walk','roller'):raise AiError('mode_unknown','读不到当前走路/轮滑模式，未执行动作')
   if pol['mode']!=target:
    await self.pad_command({'op':'drive_mode','expect':target},marker)
    deadline=asyncio.get_running_loop().time()+8
    while True:
     self.check_relax(marker);self.check_speech(tts);await self.guard(require_torque=False)
     pol=await self.call('robot.policies')
     if pol.get('mode')==target:break
     if asyncio.get_running_loop().time()>=deadline:raise AiError('mode_timeout','未读回目标运动模式，后续动作已取消')
     await asyncio.sleep(.1)
  self.check_relax(marker);self.check_speech(tts)
  await self.power_tool('duck_start_walk',marker)
  await self.guard()
 @staticmethod
 def channels(name):
  if name=='duck_mouth':return {'mouth'}
  if name in ('duck_head','duck_shake','duck_nod'):return {'head'}
  if name in ('duck_move','duck_step','duck_spin','duck_skill','duck_stand','duck_start_walk','duck_sit'):return {'body','head'}
  return set()
 def spin_sample(self):
  sample=self.rd.state;age=sample.age(time.monotonic());st=sample.value
  if age is None or age>.5 or not isinstance(st,dict):raise AiError('imu_stale','转圈需要实时IMU，读数中断，已停止')
  q=(st.get('imu') or {}).get('quat');key=st.get('t_ns')
  if not isinstance(q,(list,tuple)) or len(q)!=4 or key is None or any(type(x) not in (int,float) or not math.isfinite(x) for x in q):raise AiError('imu_invalid','没有有效IMU方向，无法确认360度')
  norm=math.sqrt(sum(x*x for x in q))
  if norm<.5 or norm>1.5:raise AiError('imu_invalid','IMU四元数无效')
  w,x,y,z=[v/norm for v in q]
  return key,math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
 async def spin(self,direction,tts,marker):
  velocity=self.step_velocity('turn_'+direction);sign=1 if direction=='left' else -1
  loop=asyncio.get_running_loop();started=loop.time();last_change=started
  key,last=self.spin_sample();total=0
  try:
   await self.call('robot.pose',{'active':False})
   while True:
    self.check_relax(marker);self.check_speech(tts);bus=await self.guard();await self.check_driver()
    if bus.get('policy_motion_enabled',bus.get('policy_enabled')) is not True:raise AiError('policy_off','行走策略关闭，转圈已停止')
    now=loop.time();new_key,yaw=self.spin_sample()
    if new_key!=key:
     delta=math.atan2(math.sin(yaw-last),math.cos(yaw-last))
     if abs(delta)>.8:raise AiError('imu_jump','IMU方向跳变，转圈已停止')
     total+=sign*delta;key,last=new_key,yaw;last_change=now
    if now-last_change>.5:raise AiError('imu_stale','IMU读数不再更新，转圈已停止')
    self.status['spin_degrees']=round(math.degrees(total),1)
    if total>=2*math.pi:break
    if now-started>30:raise AiError('spin_timeout','转圈超时，未确认转满360度，已停止')
    await self.call('robot.move',{k:velocity[k] for k in ('vx','vy','vyaw')})
    await asyncio.sleep(.05)
   self.status['results'].append({'tool':'duck_spin','state':'completed','degrees':math.degrees(total)})
  finally:await self.call('robot.stop')
 async def execute_tool(self,name,a,skills,tts,relax_marker):
  self.check_relax(relax_marker);self.check_speech(tts)
  if name=='duck_skill' and a.get('skill')=='sit_toggle':name='duck_sit'
  bus=await self.guard(require_torque=name not in ('duck_stand','duck_start_walk','duck_sit','duck_sound','duck_stop'))
  await self.check_driver()
  if name=='duck_sit':
   await self.sit_tool(relax_marker);self.status['results'].append({'tool':name,'state':'completed'});return
  if name in ('duck_stand','duck_start_walk'):
   await self.power_tool(name,relax_marker);self.status['results'].append({'tool':name,'state':'completed'});return
  if name=='duck_skill':
   live,_=await self.catalog()
   if a['skill'] not in live:raise AiError('skill_changed','当前动作列表已改变')
   await self.pad_command({'op':'skill','name':a['skill']},relax_marker);self.status['results'].append({'tool':name,'state':'accepted','skill':a['skill']})
   deadline=asyncio.get_running_loop().time()+skills[a['skill']]
   while asyncio.get_running_loop().time()<deadline:
    self.check_relax(relax_marker);self.check_speech(tts);await asyncio.sleep(.1)
   return
  if name=='duck_sound':await self.call('robot.sound',{'tag':a['tag']});self.status['results'].append({'tool':name,'state':'accepted'});return
  if name=='duck_stop':await self.call('robot.stop');self.status['results'].append({'tool':name,'state':'completed'});return
  if name=='duck_spin':
   await self.spin(a['direction'],tts,relax_marker);return
  step_direction=None
  if name=='duck_step':step_direction=a['direction'];a=self.step_velocity(step_direction);name='duck_move'
  if name=='duck_move' and bus.get('policy_motion_enabled',bus.get('policy_enabled')) is not True:raise AiError('policy_off','行走策略准备后已被关闭，动作已取消')
  await self.check_driver()
  if name=='duck_move':await self.call('robot.pose',{'active':False})
  started=asyncio.get_running_loop().time();deadline=started+a['seconds']
  try:
   while asyncio.get_running_loop().time()<deadline:
    self.check_relax(relax_marker);self.check_speech(tts);bus=await self.guard();await self.check_driver()
    if name=='duck_move' and bus.get('policy_motion_enabled',bus.get('policy_enabled')) is not True:
     raise AiError('policy_off','行走策略已关闭，动作已取消')
    if name=='duck_move':await self.call('robot.move',{k:a[k] for k in ['vx','vy','vyaw']})
    elif name=='duck_head':await self.call('robot.head',{'head_yaw':a['yaw'],'head_pitch':a['pitch'],'neck_pitch':0,'head_roll':0})
    elif name=='duck_shake':
     yaw=a['amplitude']*math.sin(2*math.pi*(asyncio.get_running_loop().time()-started))
     await self.call('robot.head',{'head_yaw':yaw,'head_pitch':0,'neck_pitch':0,'head_roll':0})
    elif name=='duck_nod':
     pitch=a['amplitude']*math.sin(2*math.pi*(asyncio.get_running_loop().time()-started))
     await self.call('robot.head',{'head_yaw':0,'head_pitch':pitch,'neck_pitch':0,'head_roll':0})
    else:await self.call('robot.mouth',{'open':a['opening']})
    await asyncio.sleep(.1)
  finally:
   try:
    if name=='duck_move':await self.call('robot.stop')
    elif name=='duck_mouth':await self.call('robot.mouth',{'open':0})
    else:await self.call('robot.head',{'head_yaw':0,'head_pitch':0,'neck_pitch':0,'head_roll':0})
   except Exception:pass
  self.status['results'].append({'tool':'duck_step' if step_direction else name,'state':'completed','params':a})
 async def wait_speech(self,tts,marker,complete=False):
  if tts.task:
   while not tts.task.done():
    self.check_relax(marker);self.check_speech(tts)
    if not complete and getattr(tts,'started',None) and tts.started.is_set():break
    await asyncio.sleep(.05)
   self.check_relax(marker);self.check_speech(tts)
 async def _run(self,plan,skills,tts,relax_marker=0):
  try:
   # Facial requests may overlap speech even when the model lists a body action first.
   facial={'duck_head','duck_shake','duck_nod','duck_mouth'}
   plan=[p for p in plan if p[0] in facial]+[p for p in plan if p[0] not in facial]
   index=0
   while index<len(plan):
    group=[plan[index]];channels=self.channels(plan[index][0]);index+=1
    while index<len(plan) and channels and channels<={'head','mouth'}:
     following=self.channels(plan[index][0])
     if not following or not following<={'head','mouth'} or channels&following:break
     group.append(plan[index]);channels|=following;index+=1
    face_only=all(name in facial for name,a in group)
    self.status['state']='waiting_speech_start' if face_only else 'waiting_speech'
    # Do not prepare/enable motors for body requests until playback really drains.
    await self.wait_speech(tts,relax_marker,complete=not face_only)
    if not face_only and getattr(self,'speech_motion',None):await self.speech_motion.finish(tts)
    self.active_channels=channels
    if 'head' in channels and getattr(self,'speech_motion',None):await self.speech_motion.release_head()
    self.status['state']='preparing'
    await self.prepare_group(group,relax_marker,tts)
    self.status['state']='executing'
    tasks=[asyncio.create_task(self.execute_tool(name,a,skills,tts,relax_marker)) for name,a in group]
    try:await asyncio.gather(*tasks)
    finally:
     for task in tasks:
      if not task.done():task.cancel()
     await asyncio.gather(*tasks,return_exceptions=True);self.active_channels=set()
   self.status['state']='done'
  except asyncio.CancelledError:self.status['state']='stopped';raise
  except Exception as error:
   self.status['state']='error';self.status['error']=str(error) if isinstance(error,AiError) else (str(error)[:240] if type(error).__name__=='RpcError' else '固件拒绝动作或连接中断，后续动作已取消')
  finally:
   self.active_channels=set()
