import asyncio
import base64
import json
import subprocess
import time
from starlette.responses import JSONResponse, Response

HELPER = "/usr/local/sbin/microduck-helper"
LIMIT = 24 * 1024 * 1024


def run(op, data=None):
    r = subprocess.run(
        ["/usr/bin/sudo", "-n", HELPER, "maintenance-" + op],
        input=data,
        capture_output=True,
        timeout=45,
    )
    if r.returncode:
        try:
            failure = json.loads(r.stdout.splitlines()[-1])
            msg = failure.get("error") or failure.get("msg") or "维护操作失败"
        except Exception:
            msg = "维护服务不可用，请检查安装与权限"
        raise ValueError(msg)
    return r.stdout


def make_handler(auth, session_of, origin_ok, blocker=None):
    lock = asyncio.Lock()

    async def handler(request):
        headers = {"Cache-Control": "no-store"}

        def reply(d, status=200):
            return JSONResponse(d, status_code=status, headers=headers)

        _, sess = session_of(request)
        if not auth.has_password() or not sess:
            return reply({"ok": False, "error": "请先设置网页密码并登录"}, 401)
        if request.method == "POST" and (
            not origin_ok(request) or request.headers.get("x-csrf") != sess["csrf"]
        ):
            return reply({"ok": False, "error": "请求校验失败，请刷新后重试"}, 403)
        try:
            if lock.locked():
                return reply({"ok": False, "error": "另一项维护操作正在进行"}, 409)
            async with lock:
                if request.method == "GET":
                    data = await asyncio.to_thread(run, "export")
                    return Response(
                        data,
                        media_type="application/zip",
                        headers={
                            **headers,
                            "Content-Disposition": 'attachment; filename="microduck-backup-'
                            + time.strftime("%Y%m%d-%H%M%S")
                            + '.zip"',
                        },
                    )
                raw = bytearray()
                async for part in request.stream():
                    if len(raw) + len(part) > LIMIT:
                        return reply({"ok": False, "error": "上传备份请求超过24MB"}, 413)
                    raw.extend(part)
                body = json.loads(raw)
                op = body.get("op")
                if op not in ["preview", "restore", "shutdown"]:
                    raise ValueError("操作不支持")
                if op in ["restore", "shutdown"] and body.get("confirmed") is not True:
                    raise ValueError("请先确认操作")
                if op in ["restore", "shutdown"] and blocker:
                    reason = blocker()
                    if reason:
                        raise ValueError(reason)
                if op == "restore" and not auth.check_password(
                    body.get("password") if isinstance(body.get("password"), str) else ""
                ):
                    raise ValueError("恢复前请再输入一次网页登录密码，密码不正确")
                data = None
                if op in ["preview", "restore"]:
                    text = body.get("archive")
                    if not isinstance(text, str):
                        raise ValueError("请选择备份文件")
                    try:
                        data = base64.b64decode(text, validate=True)
                    except Exception:
                        raise ValueError("备份编码不正确")
                return reply(json.loads(await asyncio.to_thread(run, op, data)))
        except (ValueError, KeyError, TypeError) as exc:
            return reply({"ok": False, "error": str(exc)[:300]}, 400)
        except Exception:
            return reply({"ok": False, "error": "维护操作未完成，请检查设备状态"}, 503)

    return handler
