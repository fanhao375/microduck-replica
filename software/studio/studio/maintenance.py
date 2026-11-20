"""Authenticated operations on the fixed-scope local maintenance helper."""
import asyncio
import base64
import binascii
import json
import subprocess
import time

from starlette.responses import JSONResponse, Response

from .auth import COOKIE


HELPER = '/usr/local/sbin/microduck-maintenance'
ARCHIVE_LIMIT = 16 * 1024 * 1024
REQUEST_LIMIT = 24 * 1024 * 1024


def run(op, data=None):
    # A browser timeout must not interrupt a multi-file restore after replacement has started.
    timeout = None if op == 'restore' else 45
    result = subprocess.run(['/usr/bin/sudo', '-n', HELPER, op], input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        try:
            reason = json.loads(result.stdout)['error']
        except (ValueError, KeyError, TypeError):
            raise RuntimeError('维护服务不可用，请检查安装与权限') from None
        raise ValueError(str(reason)[:300])
    return result.stdout


def make_handler(auth, origin_ok, readiness):
    lock = asyncio.Lock()

    async def handler(request):
        headers = {'Cache-Control': 'no-store'}

        def error(message, status):
            return JSONResponse({'ok': False, 'error': message}, status_code=status, headers=headers)

        # Open-mode and loopback developer sessions cannot authorize a private device backup.
        session = auth.session(request.cookies.get(COOKIE)) if auth.has_password() else None
        if not session:
            return error('请先设置网页密码并登录', 401)
        if request.method == 'POST' and (not origin_ok(request) or request.headers.get('x-csrf') != session['csrf']):
            return error('请求校验失败，请刷新后重试', 403)
        if lock.locked():
            return error('另一项维护操作正在进行', 409)
        try:
            async with lock:
                if request.method == 'GET':
                    data = await asyncio.to_thread(run, 'export')
                    filename = 'microduck-backup-' + time.strftime('%Y%m%d-%H%M%S') + '.zip'
                    return Response(data, media_type='application/zip', headers={
                        **headers, 'Content-Disposition': f'attachment; filename="{filename}"'})

                raw = bytearray()
                async for part in request.stream():
                    if len(raw) + len(part) > REQUEST_LIMIT:
                        return error('上传备份请求超过 24 MB', 413)
                    raw.extend(part)
                try:
                    body = json.loads(raw)
                except (ValueError, RecursionError):
                    return error('请求须为 JSON 对象', 400)
                op = body.get('op') if isinstance(body, dict) else None
                if op not in ('preview', 'restore', 'shutdown'):
                    return error('操作不支持', 400)
                if op in ('restore', 'shutdown'):
                    if body.get('confirmed') is not True:
                        return error('请先确认操作', 400)
                    state = readiness()
                    if not state['ready']:
                        return error(state['error'], 409)
                data = None
                if op in ('preview', 'restore'):
                    archive = body.get('archive')
                    if not isinstance(archive, str):
                        return error('请选择备份文件', 400)
                    if len(archive) > 4 * ((ARCHIVE_LIMIT + 2) // 3):
                        return error('备份文件超过 16 MB', 413)
                    try:
                        data = base64.b64decode(archive, validate=True)
                    except (ValueError, binascii.Error):
                        return error('备份编码不正确', 400)
                    if len(data) > ARCHIVE_LIMIT:
                        return error('备份文件超过 16 MB', 413)
                result = json.loads(await asyncio.to_thread(run, op, data))
                return JSONResponse(result, headers=headers)
        except ValueError as exc:
            return error(str(exc)[:300], 400)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return error('维护服务不可用，请检查设备状态、安装和权限', 503)

    return handler
