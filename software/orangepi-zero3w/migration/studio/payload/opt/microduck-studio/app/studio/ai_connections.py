"""Private, atomic AI configuration. No credentials or transcripts enter snapshots."""
import copy
import ipaddress
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ARK_URL = 'https://ark.cn-beijing.volces.com/api/v3'
PROVIDERS = ('doubao_ark', 'openai_compatible')


class AiError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def local_host(host):
    if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return (addr.is_private or addr.is_loopback) and not (addr.is_unspecified or addr.is_multicast)
    except ValueError:
        return False


def base_url(value):
    if not isinstance(value, str) or len(value) > 500 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise AiError('validation', 'Base URL 格式不正确')
    if not value:
        return ''
    try:
        u = urlsplit(value)
        port = u.port
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
                or u.password is not None or '?' in value or '#' in value or '\\' in value
                or (u.scheme == 'http' and not local_host(u.hostname))):
            raise ValueError()
    except ValueError:
        raise AiError('validation', 'Base URL 须为 HTTPS；仅本地服务可用 HTTP，且不能带账号、查询或片段') from None
    return value.rstrip('/')


def text(value, limit, field):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise AiError('validation', f'{field} 格式或长度不正确')
    return value.strip()


class Connections:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / 'ai-connections.json' if state_dir else None
        self._lock = threading.RLock()

    def _read(self):
        if self.path is None:
            raise AiError('state', 'AI 设置存储目录尚未配置', 503)
        try:
            if not self.path.exists():
                return {'version': 1, 'generation': 0, 'current_id': None, 'connections': []}
            if self.path.stat().st_size > 100000:
                raise ValueError()
            state = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(state, dict) or set(state) != {'version', 'generation', 'current_id', 'connections'}
                    or state['version'] != 1 or type(state['generation']) is not int
                    or state['generation'] < 0 or not isinstance(state['connections'], list)
                    or len(state['connections']) > 10):
                raise ValueError()
            ids = set()
            for item in state['connections']:
                if not isinstance(item, dict) or set(item) != {'id','revision','name','provider','base_url','model','auth_mode','api_key'}:
                    raise ValueError()
                if (not isinstance(item['id'], str) or len(item['id']) != 24 or item['id'] in ids
                        or type(item['revision']) is not int or item['revision'] < 1):
                    raise ValueError()
                ids.add(item['id'])
                self._fields({k:v for k,v in item.items() if k not in ('id','revision')})
            if state['current_id'] is not None and state['current_id'] not in ids:
                raise ValueError()
            return state
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise AiError('state', 'AI 配置无法读取，原文件已保留；请检查私有配置文件', 503) from None

    def _write(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix='.ai-connections-', suffix='.tmp', dir=self.path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                os.chmod(tmp, 0o600)
                json.dump(state, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
            tmp = None
        except OSError:
            raise AiError('state', 'AI 配置未能保存', 503) from None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _fields(self, value):
        if not isinstance(value, dict) or set(value) - {'id','name','provider','base_url','model','auth_mode','api_key','clear_key'}:
            raise AiError('validation', 'AI 连接字段不正确')
        provider = value.get('provider', 'doubao_ark')
        mode = value.get('auth_mode', 'bearer')
        if provider not in PROVIDERS or mode not in ('bearer', 'none'):
            raise AiError('validation', '提供商或认证方式不支持')
        url = base_url(value.get('base_url', ARK_URL if provider == 'doubao_ark' else ''))
        if mode == 'none' and (not url or not local_host(urlsplit(url).hostname)):
            raise AiError('validation', '无需 API Key 仅适用于明确配置的本地服务')
        if 'clear_key' in value and type(value['clear_key']) is not bool:
            raise AiError('validation', '清除 Key 参数不正确')
        key = text(value.get('api_key',''),4096,'API Key')
        if key and (not key.isascii() or any(c.isspace() for c in key)):
            raise AiError('validation','API Key 不能含空白或非 ASCII 字符')
        return {'name':text(value.get('name',''),80,'连接名称'), 'provider':provider,
                'base_url':url, 'model':text(value.get('model',''),200,'Model ID'),
                'auth_mode':mode, 'api_key':key}

    @staticmethod
    def redact(value, state):
        if isinstance(value, str):
            for item in state['connections']:
                if item['api_key']:
                    value = value.replace(item['api_key'], '[已隐藏]')
            return value
        if isinstance(value, list):
            return [Connections.redact(item,state) for item in value]
        if isinstance(value, dict):
            return {key:Connections.redact(item,state) for key,item in value.items()}
        return value

    def _public(self, item, state):
        result = {k:v for k,v in item.items() if k != 'api_key'}
        result['key_configured'] = bool(item['api_key'])
        result['missing'] = [k for k in ('name','base_url','model') if not item[k]]
        if item['auth_mode'] == 'bearer' and not item['api_key']:
            result['missing'].append('api_key')
        result['ready'] = not result['missing']
        for key in ('name','base_url','model'):
            result[key] = self.redact(result[key],state)
        return result

    def view(self):
        with self._lock:
            state = self._read()
            return {'connections':[self._public(i,state) for i in state['connections']], 'current_id':state['current_id']}

    def save(self, values):
        fields = self._fields(values)
        with self._lock:
            state = self._read()
            ident = values.get('id')
            old = next((i for i in state['connections'] if i['id'] == ident), None)
            if ident is not None and old is None:
                raise AiError('missing', '连接不存在', 404)
            if old is None and len(state['connections']) >= 10:
                raise AiError('limit', '最多保存 10 个连接')
            if (old and not fields['api_key'] and not values.get('clear_key')
                    and fields['provider'] == old['provider'] and fields['base_url'] == old['base_url']
                    and fields['auth_mode'] == 'bearer'):
                fields['api_key'] = old['api_key']
            if values.get('clear_key') or fields['auth_mode'] == 'none':
                fields['api_key'] = ''
            item = {'id':ident or secrets.token_hex(12), 'revision':old['revision']+1 if old else 1, **fields}
            if old:
                state['connections'][state['connections'].index(old)] = item
            else:
                state['connections'].append(item)
            state['generation'] += 1
            self._write(state)
            return self._public(item,state)

    def _mutate(self, ident, delete):
        with self._lock:
            state = self._read()
            if ident is not None and not any(i['id'] == ident for i in state['connections']):
                raise AiError('missing', '连接不存在', 404)
            if delete:
                state['connections'] = [i for i in state['connections'] if i['id'] != ident]
                if state['current_id'] == ident:
                    state['current_id'] = None
            else:
                state['current_id'] = ident
            state['generation'] += 1
            self._write(state)

    def select(self, ident):
        with self._lock:
            if ident is not None:
                state = self._read()
                item = next((i for i in state['connections'] if i['id'] == ident),None)
                if item and not self._public(item,state)['ready']:
                    raise AiError('incomplete','请先补齐并保存配置，再设为当前连接')
            self._mutate(ident, False)

    def delete(self, ident):
        self._mutate(ident, True)

    def private(self, ident, revision):
        """For server adapters only; never serialize this return value to a response/log."""
        with self._lock:
            state = self._read()
            item = next((i for i in state['connections'] if i['id'] == ident), None)
            if not item:
                raise AiError('missing', '连接不存在', 404)
            if type(revision) is not int or item['revision'] != revision:
                raise AiError('stale', '连接已更改，请重新读取设置', 409)
            return copy.deepcopy(item), state['generation']

    def finish(self, generation, value):
        with self._lock:
            state = self._read()
            if state['generation'] != generation:
                raise AiError('stale','连接已更改，本次旧配置的回复已丢弃；请刷新设置',409)
            return self.redact(value,state)
