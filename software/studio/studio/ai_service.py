"""Async boundary: disk/network never block the web loop or touch robot ports."""
import asyncio
from types import SimpleNamespace

from .ai_client import CompatibleClient, RequestControl, validate_messages
from .ai_connections import Connections, AiError
from .tts import TtsService
from .ai_tools import RobotTools
from .speech_motion import SpeechMotion


class AiService:
    def __init__(self, state_dir, robotd=None, pad=None):
        self.store = Connections(state_dir)
        self.client = CompatibleClient(timeout=40)
        self.tts = TtsService(state_dir)
        self.tools = RobotTools(state_dir,robotd,pad)
        self.speech_motion = SpeechMotion(self.tools)
        self.tools.speech_motion = self.speech_motion
        self.tts.on_start = self.speech_motion.start
        self.turn_generation = 0

    async def view(self):
        return {**await asyncio.to_thread(self.store.view), "tts":await asyncio.to_thread(self.tts.view),"tools":self.tools.view()}

    async def handle(self, body):
        if not isinstance(body, dict):
            raise AiError('validation', '请求须为 JSON 对象')
        op = body.get('op')
        if op=='tools_get' and set(body)=={'op'}:return {'tools':self.tools.view()}
        if op=='tools_save' and set(body)=={'op','enabled'}:return {'tools':self.tools.save(body['enabled'])}
        if op=='tools_stop' and set(body)=={'op'}:
            self.turn_generation += 1
            self.tts.stop()
            return {'tools':await self.tools.stop()}
        if op == 'tts_get' and set(body) == {'op'}:
            return {'tts':await asyncio.to_thread(self.tts.view)}
        if op == 'tts_save' and set(body) == {'op','settings'}:
            return {'tts':await asyncio.to_thread(self.tts.save,body['settings'])}
        if op == 'tts_test' and set(body) == {'op'}:
            return {'speech':await self.tts.start('你好，我是小鸭子。语音回复已经准备好了。')}
        if op == 'tts_preview' and set(body) == {'op'}:
            return {'audio':await self.tts.preview()}
        if op == 'tts_stop' and set(body) == {'op'}:
            return {'tts':await asyncio.to_thread(self.tts.stop)}
        fields = {'save' :{'op','connection'}, 'select':{'op','id'}, 'delete':{'op','id'},
                  'test':{'op','id','revision'}, 'chat':{'op','id','revision','messages'}}
        if not isinstance(op, str) or op not in fields or set(body) != fields[op]:
            raise AiError('validation', 'AI 操作或参数不正确')
        if op == 'save':
            item = await asyncio.to_thread(self.store.save,body['connection'])
            return {**await self.view(), 'saved_id':item['id']}
        ident = body['id']
        if (ident is not None and (not isinstance(ident,str) or len(ident) != 24)
                or (ident is None and op != 'select')):
            raise AiError('validation', '连接 ID 不正确')
        if op in ('select','delete'):
            await asyncio.to_thread(getattr(self.store,op),ident)
            return await self.view()
        config, generation = await asyncio.to_thread(self.store.private,ident,body['revision'])
        if (not all(config[k] for k in ('name','base_url','model'))
                or config['auth_mode'] == 'bearer' and not config['api_key']):
            raise AiError('incomplete', '请先保存完整的名称、Base URL、Model ID 和认证设置')
        messages = ([{'role':'user','content':'请只回复：连接成功'}] if op == 'test'
                    else validate_messages(body['messages']))
        turn_generation=self.turn_generation
        schemas,skills=await self.tools.schemas() if op=='chat' else ([],{})
        extra={'tools':schemas} if schemas else {}
        control = RequestControl()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self.client.complete,config,messages,
                max_tokens=64 if op == 'test' else 512,cancel=control,**extra),22)
        except asyncio.CancelledError:
            control.cancel()
            raise
        except asyncio.TimeoutError:
            control.cancel()
            raise AiError('timeout','AI 请求超时，请稍后重试',504) from None
        result = await asyncio.to_thread(self.store.finish,generation,result)
        if turn_generation != self.turn_generation:
            raise AiError('cancelled','本轮 AI 回复已取消，未播放语音或执行动作')
        camera_image=None
        calls=result.get('tool_calls',[]) if isinstance(result,dict) else []
        if any(c.get('function',{}).get('name')=='duck_camera' for c in calls):
            import json
            for call in calls:
                if call.get('function',{}).get('name')=='duck_camera' and json.loads(call['function'].get('arguments','{}'))!={}:
                    raise AiError('validation','摄像头工具不接受参数')
            def capture():
                from .camera_capture import capture
                return capture()
            image=await asyncio.to_thread(capture)
            camera_image=image
            if turn_generation != self.turn_generation:
                raise AiError('cancelled','本轮已取消')
            control=RequestControl()
            try:
                result=await asyncio.wait_for(asyncio.to_thread(self.client.complete,config,messages,
                    max_tokens=4096,cancel=control,camera_image=image),45)
            except (asyncio.CancelledError,asyncio.TimeoutError):
                control.cancel()
                raise
            result=await asyncio.to_thread(self.store.finish,generation,result)
            if turn_generation != self.turn_generation:
                raise AiError('cancelled','本轮已取消')
            calls=[]
        # Explicit single-circle requests must not become a short timed move.
        import re,json
        text=next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'),'')
        if op=='chat' and self.tools.enabled and isinstance(text,str) and re.fullmatch(r'\s*(?:请|鸭子|小鸭子|你|给我|帮我|再|原地|向左|向右|往左|往右|左|右|来|一个|一下|转个|转|圈|一|360|度|转圈|转一圈|转个圈|转360度|[，。！! ])+',text) and re.search(r'转.*(?:圈|360)',text):
            calls=[{'function':{'name':'duck_spin','arguments':json.dumps({'direction':'right' if '右' in text else 'left'})}}]

        result=result['text'] if isinstance(result,dict) else result
        speech = None
        if op == 'chat':
            try:
                speech = await self.tts.start(result,automatic=True)
            except AiError as error:
                speech = {'started':False,'code':error.code,'error':str(error)}
        action=None
        if calls and not (speech or {}).get("error"):
            try:action=await self.tools.start(calls,skills,self.tts if (speech or {}).get("started") else SimpleNamespace(task=None,status={"state":"done"}))
            except (AiError,ValueError,TypeError,KeyError) as error:action={'accepted':False,'error':str(error)[:240] or '动作计划无效，未执行'}
        print('AI_TOOL_CALL',{'names':[c.get('function',{}).get('name') for c in calls], 'speech':speech,'action':action},flush=True)
        return {'camera_image':camera_image,'action':action,'id':ident, 'revision':config['revision'], 'text':result, 'speech':speech}
