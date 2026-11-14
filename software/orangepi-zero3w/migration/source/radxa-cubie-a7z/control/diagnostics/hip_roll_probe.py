"""Operator-supported, unloaded +/-5 degree test of hip roll IDs21 and11."""
import argparse,fcntl,json,math,signal,struct,subprocess,sys,time,tomllib
from pathlib import Path
sys.path[:0]=['/opt/microduck-studio/app','/opt/microduck-studio/app/deps']
from studio.bus.feetech import FeetechBus,READ,WRITE
IDS=(20,21,22,23,24,30,31,32,33,34,10,11,12,13,14)
SELECTED=(21,11)
AMPLITUDE=57

def permitted(sid,inst,params,centers):
 if inst==READ:return sid in IDS and tuple(params) in ((40,1),(33,1),(50,3),(56,15),(42,8),(42,2))
 if inst!=WRITE or sid not in SELECTED:return False
 if params in (b'\x28\x00',b'\x28\x01'):return True
 return (len(params)==3 and params[0]==42 and sid in centers
         and abs(int.from_bytes(params[1:],'little')-centers[sid])<=AMPLITUDE)

class LimitedBus(FeetechBus):
 def __init__(self):
  import serial
  self.centers={}
  ser=serial.Serial('/dev/ttyS2',1_000_000,timeout=.02,write_timeout=.1,exclusive=True)
  super().__init__('/dev/ttyS2',ser=ser)
  fcntl.ioctl(ser.fileno(),__import__('termios').TIOCEXCL)
 def _send(self,sid,instr,params=b''):
  if not permitted(sid,instr,bytes(params),self.centers):raise RuntimeError('packet outside approved hip-roll test')
  super()._send(sid,instr,params)
 def close(self):
  try:fcntl.ioctl(self.ser.fileno(),__import__('termios').TIOCNXCL)
  finally:super().close()

def all_off(bus):
 for sid in SELECTED:
  bus.write_u8(sid,40,0)
 for sid in IDS:
  if bus.read_u8(sid,40)!=0:raise RuntimeError(f'ID{sid} torque off unconfirmed')

def stopped():
 for name in ('robotd','microduck-studio'):
  s=subprocess.run(['systemctl','is-active',name],capture_output=True,text=True).stdout.strip()
  if s not in ('inactive','failed'):raise RuntimeError(name+' must be stopped')

def require(condition,message):
 if not condition:raise RuntimeError(message)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path);ap.add_argument('--relax-only',action='store_true');ap.add_argument('--joint-id',type=int,choices=SELECTED);a=ap.parse_args();selected=(a.joint_id,) if a.joint_id else SELECTED
 stopped();bus=LimitedBus()
 try:
  if a.relax_only:all_off(bus);print(json.dumps({'all_torque_off_confirmed':True}));return
  require(a.output is not None,'output path required')
  for sid in IDS:require(bus.read_u8(sid,40)==0,f'ID{sid} torque is not off')
  cal=tomllib.loads(Path('/etc/robot/feetech-ft5/hd1910-calibration.toml').read_text())
  for sid in selected:
   j=next(j for j in cal['joints'] if j['id']==sid);reading=bus.state(sid)
   require(reading['err']==0 and reading['status']==0,f'ID{sid} has a reported status error')
   center=reading['pos']
   require(bus.read_u8(sid,33)==4,f'ID{sid} expected native position mode4')
   for target in (center-AMPLITUDE,center+AMPLITUDE):
    rad=(target-j['zero_raw'])*j['direction']*2*math.pi/4096
    require(0<=target<4096 and j['min_rad']<=rad<=j['max_rad'],f'ID{sid} center={center}, candidate={target}, angle_deg={math.degrees(rad):.2f} exceeds configured limits; no movement started')
   bus.centers[sid]=center
  def interrupted(*_):raise RuntimeError('test interrupted')
  signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
  with a.output.open('x') as out:
   def log(row):out.write(json.dumps(row)+'\n');out.flush()
   for sid in selected:
    center=bus.centers[sid];static={'mode':bus.read_u8(sid,33),'pdi':list(bus.read(sid,50,3)[1]),'goal_block':list(bus.read(sid,42,8)[1])}
    log({'type':'before','id':sid,'center':center,'static':static});print(json.dumps({'starting_joint':sid}),flush=True);time.sleep(3)
    bus.write_u16(sid,42,center);require(bus.read_u16(sid,42)==center,f'ID{sid} preload goal not confirmed')
    bus.write_u8(sid,40,1);require(bus.read_u8(sid,40)==1,f'ID{sid} torque on not confirmed')
    previous=center
    for phase,target in enumerate((center+AMPLITUDE,center-AMPLITUDE,center)):
     for step in range(1,25):
      at=time.monotonic();goal=round(previous+(target-previous)*step/24)
      bus.write_u16(sid,42,goal);state=bus.state(sid)
      log({'type':'sample','id':sid,'phase':phase,'settled':False,'at':at,'target':goal,'state':state})
      require(state['err']==0 and state['status']==0 and state['temp']<50 and 6.5<=state['volt']<=8.5,f'ID{sid} status/temperature/voltage stop: {state}')
      require(abs(state['pos']-goal)<100,f'ID{sid} position tracking deviation stop')
      time.sleep(max(0,.05-(time.monotonic()-at)))
     for _ in range(40):
      at=time.monotonic();state=bus.state(sid)
      log({'type':'sample','id':sid,'phase':phase,'settled':True,'at':at,'target':target,'state':state})
      require(state['err']==0 and state['status']==0 and state['temp']<50 and 6.5<=state['volt']<=8.5,f'ID{sid} status/temperature/voltage stop: {state}')
      time.sleep(max(0,.05-(time.monotonic()-at)))
     previous=target
    bus.write_u8(sid,40,0);require(bus.read_u8(sid,40)==0,f'ID{sid} torque off not confirmed')
    log({'type':'finished_joint','id':sid,'torque_off':True})
   all_off(bus);log({'type':'result','unloaded_only':True,'all_torque_off':True,'ids':selected,'max_delta_raw':AMPLITUDE})
  print(json.dumps({'completed':True,'ids':selected,'amplitude_degrees':AMPLITUDE*360/4096,'all_torque_off':True,'output':str(a.output)}))
 finally:
  try:all_off(bus)
  finally:bus.close()
if __name__=='__main__':main()
