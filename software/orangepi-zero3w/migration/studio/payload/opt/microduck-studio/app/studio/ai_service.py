"""Async boundary: disk/network never block the web loop or touch robot ports."""
import asyncio

from .ai_client import CompatibleClient, RequestControl, validate_messages
from .ai_connections import Connections, AiError


class AiService:
    def __init__(self, state_dir):
        self.store = Connections(state_dir)
        self.client = CompatibleClient()

    async def view(self):
        return await asyncio.to_thread(self.store.view)

    async def handle(self, body):
        if not isinstance(body, dict):
            raise AiError('validation', '请求须为 JSON 对象')
        op = body.get('op')
        fields = {'save':{'op','connection'}, 'select':{'op','id'}, 'delete':{'op','id'},
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
        control = RequestControl()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self.client.complete,config,messages,
                max_tokens=64 if op == 'test' else 512,cancel=control),22)
        except asyncio.CancelledError:
            control.cancel()
            raise
        except asyncio.TimeoutError:
            control.cancel()
            raise AiError('timeout','AI 请求超时，请稍后重试',504) from None
        result = await asyncio.to_thread(self.store.finish,generation,result)
        return {'id':ident, 'revision':config['revision'], 'text':result}
