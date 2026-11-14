"""Private audio-service status and small, policy-mediated rhythm intents."""
import copy
import json
import math
import socket
import threading
import time

HEAD_ZERO = {'neck_pitch': 0., 'head_pitch': 0., 'head_yaw': 0., 'head_roll': 0.}


def _number(value, lo, hi):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError('invalid audio status number')
    return value


def validate_status(value):
    if not isinstance(value, dict) or set(value)-{'listening','music','rhythm','error','runtime_root','voice_ready'}:
        raise ValueError('invalid audio status fields')
    if 'voice_ready' in value and type(value['voice_ready']) is not bool:
        raise ValueError('invalid voice readiness')
    if 'runtime_root' in value and (not isinstance(value['runtime_root'],str) or len(value['runtime_root'])>512):
        raise ValueError('invalid voice runtime')
    if type(value.get('listening')) is not bool or type(value.get('music')) is not bool:
        raise ValueError('invalid audio switches')
    rhythm = value.get('rhythm')
    if not isinstance(rhythm, dict) or set(rhythm)-{'bpm','confidence','feel','last_beat','age','level'}:
        raise ValueError('invalid rhythm status')
    if rhythm.get('bpm') is not None:
        _number(rhythm['bpm'], 40, 220)
    _number(rhythm.get('confidence'), 0, 1)
    if rhythm.get('feel') not in ('unknown','lively','mellow','sway'):
        raise ValueError('invalid music feel')
    for name in ('last_beat','age','level'):
        if rhythm.get(name) is not None:
            _number(rhythm[name], 0, 1e12)
    error = value.get('error')
    if error is not None and (not isinstance(error,str) or len(error)>240):
        raise ValueError('invalid audio error')
    return copy.deepcopy(value)


def audio_request(message, path='/run/duck-echo/control.sock'):
    encoded = json.dumps(message,allow_nan=False,separators=(',',':')).encode()+b'\n'
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(.4)
        connection.connect(path)
        connection.sendall(encoded)
        data = bytearray()
        while b'\n' not in data:
            block = connection.recv(min(2048,8193-len(data)))
            if not block:
                raise ValueError('audio service closed response')
            data.extend(block)
            if len(data)>8192:
                raise ValueError('audio response too large')
        return json.loads(bytes(data).split(b'\n',1)[0])


class AudioClient:
    """Network operations are background/command work; snapshots only copy cache."""
    def __init__(self, request=audio_request, clock=time.monotonic, autostart=True):
        self.request,self.clock,self.autostart = request,clock,autostart
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.until = 0.
        self.cached = None
        self.at = None
        self.why = '语音服务尚未连接'

    def _call(self, message):
        try:
            result = self.request(message)
            if not isinstance(result,dict) or result.get('ok') is not True:
                raise ValueError('语音服务未完成操作')
            status = validate_status(result.get('status'))
            with self.lock:
                self.cached,self.at,self.why = status,self.clock(),None
            return copy.deepcopy(status)
        except (OSError,ValueError,TypeError):
            with self.lock:
                self.cached = self.at = None
                self.why = '连接不到语音服务，请检查 duck-echo'
            raise

    def set(self, **switches):
        if not switches or set(switches)-{'listening','music'} or any(type(v) is not bool for v in switches.values()):
            raise ValueError('监听和音乐开关必须为布尔值')
        return self._call({'method':'set',**switches})

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self.until = now+5
            if self.autostart and not self.stop.is_set() and (self.thread is None or not self.thread.is_alive()):
                self.thread = threading.Thread(target=self._loop,name='audio-status',daemon=True)
                self.thread.start()
            if self.cached is None or self.at is None or now-self.at > .6:
                return {'available':False,'why':self.why or '语音状态超过600毫秒未更新'}
            result = copy.deepcopy(self.cached)
            result['available'] = True
            if result['rhythm'].get('age') is not None:
                result['rhythm']['age'] += max(0.,now-self.at)
            return result

    def _loop(self):
        try:
            while not self.stop.is_set():
                with self.lock:
                    if self.clock()>self.until:
                        return
                try:
                    self._call({'method':'status'})
                except (OSError,ValueError,TypeError):
                    pass
                self.stop.wait(.2)
        finally:
            with self.lock:
                self.thread = None

    def close(self):
        self.stop.set()
        with self.lock:
            thread = self.thread
        if thread is not None:
            thread.join(timeout=.8)


class MusicRhythm:
    """Same generator interface as Lively; the caller owns motion arbitration."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.moving = False
        self.source_active = False
        self.started = None

    def sample(self, now, head_limit, mouth_percent):
        state = self.snapshot()
        rhythm = state.get('rhythm') or {}
        bpm, beat = rhythm.get('bpm'), rhythm.get('last_beat')
        self.source_active = (state.get('available') is True and state.get('listening') is True
                              and state.get('music') is True and not state.get('error'))
        valid = (self.source_active and type(bpm) in (int,float) and math.isfinite(bpm) and 40 <= bpm <= 220
                 and type(beat) in (int,float) and math.isfinite(beat)
                 and 0 <= now-beat <= max(1.5,120/bpm)
                 and type(rhythm.get('age')) in (int,float) and 0 <= rhythm['age'] <= .5
                 and type(rhythm.get('confidence')) in (int,float) and .55 <= rhythm['confidence'] <= 1)
        if not valid:
            self.started = None
            if self.moving:
                self.moving = False
                return [('robot.head',dict(HEAD_ZERO)),('robot.pose',{'active':False})]
            return []
        if self.started is None:
            self.started = now
        self.moving = True
        fade = min(1.,max(0.,now-self.started))
        amount = {'lively':1.,'mellow':.55,'sway':.75}.get(rhythm.get('feel'),.5)*fade
        phase = 2*math.pi*(now-beat)*bpm/60
        sway, nod = math.sin(phase), math.cos(phase)
        limit = min(.18,max(0.,head_limit))
        head = {'neck_pitch':.06*amount*nod,'head_pitch':-.08*amount*nod,
                'head_yaw':limit*amount*sway,'head_roll':.06*amount*sway}
        head = {key:max(-limit,min(limit,value)) for key,value in head.items()}
        return [('robot.head',head),
                ('robot.pose',{'active':True,'roll':.025*amount*sway,
                               'pitch':.018*amount*nod,'z':.0015*amount*nod})]
