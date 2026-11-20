"""Doubao Seed TTS 2.0 and Agent Plan streaming PCM speech."""
import asyncio
import base64
import http.client
import io
import json
import os
import re
import select
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path
from .ai_connections import AiError
from .ai_client import RequestControl

DEFAULT_SPEAKER = 'zh_female_vv_uranus_bigtts'
ENDPOINT = '/api/v3/plan/tts/unidirectional'
PROVIDERS = {
    'volcano_agent_plan': ENDPOINT,
    'volcano': '/api/v3/tts/unidirectional',
}
MAX_AUDIO = 8 * 1024 * 1024
PREVIEW_TEXT = '你好，我是小鸭子。语音回复已经准备好了。'

class TtsService:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / 'tts-settings.json' if state_dir else None
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.task = None
        self.on_start = None
        self.playback = threading.Event()
        self.started = threading.Event()
        self.control = RequestControl()
        self.player = None
        self.preview_busy = False
        self.status = {'state': 'idle', 'error': None, 'bytes': 0}

    def _read(self):
        if self.path is None:
            raise AiError('state','语音设置存储目录尚未配置',503)
        try:
            if not self.path.exists():
                return {'enabled': False, 'speaker': DEFAULT_SPEAKER, 'api_key': '', 'provider': 'volcano_agent_plan'}
            v = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(v,dict):raise ValueError()
            v.setdefault('provider', 'volcano_agent_plan')
            if (set(v) != {'enabled','speaker','api_key','provider'} or not isinstance(v['provider'], str)
                or v['provider'] not in PROVIDERS or type(v['enabled']) is not bool
                or not isinstance(v['api_key'], str) or len(v['api_key']) > 4096
                or not isinstance(v['speaker'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',v['speaker'])):
                raise ValueError()
            return v
        except (OSError, ValueError, TypeError, KeyError):
            raise AiError('tts_config', '语音配置无法读取，请检查 tts-settings.json', 503) from None

    def view(self):
        with self.lock:
            v = self._read()
            return {'enabled':v['enabled'], 'speaker':v['speaker'], 'provider':v['provider'], 'key_configured':bool(v['api_key']),
                    'status':dict(self.status)}

    def save(self, values):
        if not isinstance(values,dict) or set(values) not in ({'enabled','speaker','api_key','clear_key'}, {'enabled','speaker','api_key','clear_key','provider'}):
            raise AiError('validation','语音设置参数不正确')
        if 'provider' in values and (not isinstance(values['provider'], str) or values['provider'] not in PROVIDERS):
            raise AiError('validation','请选择支持的语音服务')
        if (type(values['enabled']) is not bool or type(values['clear_key']) is not bool
            or not isinstance(values['speaker'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',values['speaker'])
            or not isinstance(values['api_key'],str) or len(values['api_key']) > 4096
            or any(ord(c)<33 or ord(c)>126 for c in values['api_key'])):
            raise AiError('validation','请检查音色 ID 和 API Key，Key 不能包含空白')
        with self.lock:
            old = self._read()
            v = {k:values[k] for k in ('enabled','speaker','api_key')}
            v['provider'] = values.get('provider', old['provider'])
            previous_key = old['api_key'] if v['provider'] == old['provider'] else ''
            v['api_key'] = '' if values['clear_key'] else values['api_key'] or previous_key
            if v['enabled'] and not v['api_key']:
                raise AiError('tts_key','请先配置语音 API Key')
            self.path.parent.mkdir(parents=True,exist_ok=True)
            fd,name = tempfile.mkstemp(prefix='.tts-',dir=self.path.parent)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as f:
                    os.chmod(name,0o600);json.dump(v,f);f.flush();os.fsync(f.fileno())
                os.replace(name,self.path)
            finally:
                if os.path.exists(name):os.unlink(name)
        return self.view()

    async def start(self,text,automatic=False):
        if not isinstance(text,str) or not text.strip() or len(text)>12000:
            raise AiError('validation','朗读文字须为 1–12000 字')
        with self.lock:
            config = self._read()
            if automatic and not config['enabled']:
                return {'started':False,'reason':'disabled'}
            if not config['api_key']:
                raise AiError('tts_key','请先保存所选语音服务的 API Key')
            if self.task and not self.task.done():
                raise AiError('tts_busy','正在播放，请先停止或等本段结束',409)
            self.cancel = threading.Event()
            self.control = RequestControl()
            self.playback = threading.Event()
            self.started = threading.Event()
            self.status = {'state':'connecting','error':None,'bytes':0}
            self.task = asyncio.create_task(asyncio.to_thread(self._stream,config,text,self.cancel))
        if automatic and self.on_start is not None:
            await self.on_start(self)
        return {'started':True}

    def stop_playback(self):
        with self.lock:
            self.cancel.set()
            if self.player and self.player.poll() is None:
                self.player.terminate()
            self.control.cancel()
            self.status['state']='stopping' if self.task and not self.task.done() else 'idle'

    def stop(self):
        self.stop_playback()
        return self.view()

    async def preview(self):
        with self.lock:
            config = self._read()
            if not config['api_key']:
                raise AiError('tts_key', '请先保存所选语音服务的 API Key')
            if self.preview_busy:
                raise AiError('tts_busy', '正在生成浏览器试听，请稍后再试', 409)
            self.preview_busy = True
        return await asyncio.to_thread(self._preview, config)

    def _preview(self, config):
        """Generate a short WAV in memory; never open the device sound card."""
        conn = None
        try:
            conn = http.client.HTTPSConnection('openspeech.bytedance.com', timeout=10,
                                               context=ssl.create_default_context())
            payload = {'req_params': {'text': PREVIEW_TEXT, 'speaker': config['speaker'],
                                     'audio_params': {'format': 'pcm', 'sample_rate': 24000}}}
            conn.request('POST', PROVIDERS[config['provider']], body=json.dumps(payload).encode(), headers={
                'X-Api-Key': config['api_key'], 'X-Api-Resource-Id': 'seed-tts-2.0',
                'X-Api-Request-Id': str(uuid.uuid4()), 'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status != 200:
                raise AiError('tts_http', f'语音服务返回 HTTP {response.status}，请检查 Key、套餐和额度')
            pcm = bytearray()
            done = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                raw = response.readline(2 * 1024 * 1024 + 1)
                if not raw:
                    break
                if len(raw) > 2 * 1024 * 1024:
                    raise AiError('tts_size', '试听音频过大')
                if not raw.strip():
                    continue
                data = json.loads(raw)
                code = data.get('code', 0)
                if type(code) is not int:
                    raise AiError('tts_protocol', '语音返回格式不正确')
                if code == 20000000:
                    done = True
                    break
                if code != 0:
                    raise AiError('tts_provider', f'语音服务错误码 {code}，请检查音色权限和套餐额度')
                if data.get('data'):
                    pcm.extend(base64.b64decode(data['data'], validate=True))
                if len(pcm) > 1024 * 1024:
                    raise AiError('tts_size', '试听音频过大')
            if not done or not pcm or len(pcm) % 2:
                raise AiError('tts_protocol', '语音流中断或没有有效音频，请重试')
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)
            return {'mime': 'audio/wav', 'data': base64.b64encode(output.getvalue()).decode('ascii')}
        except AiError:
            raise
        except Exception:
            raise AiError('tts_preview', '试听生成失败，请检查网络、Key 和音色权限') from None
        finally:
            try:
                if conn:
                    conn.close()
            finally:
                with self.lock:
                    self.preview_busy = False

    def _stream(self,config,text,cancel):
        conn = player = None
        total = 0
        deadline = time.monotonic()+120
        try:
            if cancel.is_set():
                with self.lock:self.status['state']='idle'
                return
            conn=http.client.HTTPSConnection('openspeech.bytedance.com',timeout=15,context=ssl.create_default_context())
            conn.connect()
            self.control.bind(conn.sock)
            payload={'req_params':{'text':text,'speaker':config['speaker'],
                                  'audio_params':{'format':'pcm','sample_rate':24000}}}
            conn.request('POST',PROVIDERS[config.get('provider', 'volcano_agent_plan')],body=json.dumps(payload).encode(),headers={
                'X-Api-Key':config['api_key'],'X-Api-Resource-Id':'seed-tts-2.0',
                'X-Api-Request-Id':str(uuid.uuid4()),
                'Content-Type':'application/json','X-Control-Require-Usage-Tokens-Return':'*'})
            response=conn.getresponse()
            if response.status!=200:
                raise AiError('tts_http',f'语音服务返回 HTTP {response.status}，请检查 Key、套餐和额度')
            done=False
            while not cancel.is_set():
                if time.monotonic()>deadline:raise AiError('tts_timeout','语音播放超时')
                raw=response.readline(4*1024*1024+1)
                if not raw:break
                if len(raw)>4*1024*1024:raise AiError('tts_protocol','语音数据帧过大')
                if not raw.strip():continue
                data=json.loads(raw)
                code=data.get('code',0)
                if type(code) is not int:raise AiError('tts_protocol','语音返回格式不正确')
                if code==20000000:done=True;break
                if code!=0:raise AiError('tts_provider',f'语音服务错误码 {code}，请检查音色权限和套餐额度')
                if not data.get('data'):continue
                audio=base64.b64decode(data['data'],validate=True)
                total+=len(audio)
                if total>MAX_AUDIO:raise AiError('tts_size','本段语音过长，请缩短回复')
                if not audio:continue
                if player is None:
                    if cancel.is_set():break
                    player=subprocess.Popen(['/usr/bin/aplay','-q','-D','plughw:CARD=aic3104,DEV=0',
                        '-t','raw','-f','S16_LE','-r','24000','-c','1'],stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    os.set_blocking(player.stdin.fileno(),False)
                    with self.lock:self.player=player;self.status['state']='playing'
                pos=0
                while pos<len(audio) and not cancel.is_set():
                    if time.monotonic()>deadline:raise AiError('tts_timeout','语音播放超时')
                    if player.poll() is not None:raise AiError('tts_audio','鸭子扬声器播放失败，请检查声卡是否被占用')
                    if select.select([], [player.stdin], [], .2)[1]:
                        try:
                            pos+=os.write(player.stdin.fileno(),audio[pos:pos+8192])
                            self.started.set();self.playback.set()
                        except BlockingIOError:pass
                with self.lock:self.status['bytes']=total
            if not cancel.is_set():
                if not done or not total:raise AiError('tts_protocol','语音流中断或没有音频，请重试')
                player.stdin.close()
                while player.poll() is None and not cancel.wait(.1):
                    if time.monotonic()>deadline:raise AiError('tts_timeout','语音播放超时')
                if not cancel.is_set() and player.returncode!=0:raise AiError('tts_audio','扬声器播放失败')
            with self.lock:self.status.update(state='idle' if cancel.is_set() else 'done',error=None,bytes=total)
        except Exception as error:
            # Never return provider bodies or exception strings containing credentials/text.
            message=str(error) if isinstance(error,AiError) else '语音连接或播放失败，请检查网络、Key 和声卡'
            with self.lock:self.status.update(state='idle' if cancel.is_set() else 'error',error=None if cancel.is_set() else message,bytes=total)
        finally:
            self.playback.clear()
            if player:
                if player.poll() is None:player.terminate()
                try:player.wait(timeout=2)
                except subprocess.TimeoutExpired:player.kill();player.wait()
                if player.stdin and not player.stdin.closed:player.stdin.close()
            self.control.unbind()
            if conn:conn.close()
            with self.lock:self.player=None
