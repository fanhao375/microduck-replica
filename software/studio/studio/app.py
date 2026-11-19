"""Studio 的网页服务：静态页面、登录、日志、诊断包、WebSocket 枢纽。

WebSocket /ws（握手先查 Host、Origin：不对就拒绝握手；没登录则先 accept 再用 4401 关掉，
页面才看得到 4401、跳登录页——握手阶段就关，浏览器只会看到 1006，会一直重连）：
    Studio → 页面：{"t":"hello",...}  {"t":"state",...}（页面可见时 10 Hz）  {"t":"hb"}（看不见时 1 Hz）
                   {"t":"log","entry":{...}}  {"t":"result","id":...,"ok":...,"reason":...}
    页面 → Studio：{"t":"hb","seq":N,"visible":true,"route":"/","pad_visible":false}（每秒一次）  {"t":"cmd","id":...,"name":"relax"}
pad_visible：手柄那一块在不在屏幕上（总览重排审查 O1：手柄并进了总览，地址是 #/pad 不代表看得到）。在的页面 10 Hz 推手柄快照，
不在 1 Hz（仪表带的手柄格要用）；老页面的心跳没带这个字段，才按 route == "/pad" 兜底。

舵机会话（第三批代码审查 D1、D2、D3、D9）：
- **Studio 退出不卸力**：退出时只关串口，舵机保持着的照样保持（用户定过「离开舵机页保持使能」，退出也一样）。
  开总线时在 state 目录原子写 servo-session.json（串口、开始时间、谁开的），交还成功才删；Studio 起来时见到它、
  robotd 又不在 → 自动重新接管总线（只读打开，不改扭矩），恢复会话和提示条；接管不上 → 提示条写「上次退出时可能还有
  舵机保持着，去舵机页接管后才能卸力」，卸力按钮引导去舵机页。robotd 在 → 它起来时已经全部松开了，删掉标记。
- 离开 / 交还：舵机操作在事件循环里先记账（op_begin），交还先置 closing、等在路上的做完、当场读扭矩（servo.py）；
  离开时还有别的页面在舵机页、校准 / 运动在跑、读不到扭矩的，都不交还。
- 会话期间每 5 秒看一眼 robotd 的 socket：连得上 = 被手动起来了（它会把舵机全部松开、跟 Studio 抢串口），
  记错误、Studio 关总线（不写舵机），提示条变红。
"""
import asyncio
import contextlib
import gzip
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .auth import COOKIE, REMEMBER_S, Auth, OriginGuard
from . import helper
from .rpc import open_conn
from .servo import boot_id, view_from_robotd
from .sshkeys import BadKey, SshKeys
try:                      # https 之后手机要用到的两个功能，详见 phone_bridge 的注释
    from . import phone_bridge
except Exception:         # 模块不在就只是这两个功能没有，别把整个网页拖垮
    phone_bridge = None
from .state import summarize
from .pad import Refused as PadRefused, robot_view  # 手柄页
from .ai_connections import AiError
from .ai_service import AiService
from .updates import Updates, UpdateError, handle as handle_updates, safe_during_update

VERSION = "0.1.0-dev"
RUNTIME_ROOT = Path(__file__).resolve().parents[2]
try:
    SUITE_VERSION = json.loads((RUNTIME_ROOT / 'suite-version.json').read_bytes())['version']
except (OSError,ValueError,KeyError):
    SUITE_VERSION = None
MODEL_UPLOAD_LIMIT = 64 * 1024 * 1024
MODEL_OP_LIMIT = 32768
STATE_HZ = 10
COMMANDS = {"relax", "servo.open", "servo.close", "servo.op", "servo.ports", "imu.op", "calib.op", "pad.op", "onboard.op", "battery.op", "tune.op", "net.op"}
HB_QUIET_S = 5.0         # 页面这么久没发心跳：当作看不见（停推状态、可能退订）
HB_DEAD_S = 30.0         # 这么久没任何消息：当作半开的连接（手机走出 WiFi），直接关掉
MUSIC_PONG_TIMEOUT_S = 5.0
SERVO_IDLE_S = 60.0      # 舵机页没人在看（页面关了、断网、切走了没交还）这么久、又没有舵机保持着：自动交还主程序
ONBOARD_TICK_S = 0.5     # 上手清单多久判一次（站起来过 3 秒、走过 2 秒按这个粒度数）
SERVO_MARKER = "servo-session.json"   # 舵机会话开着的标记（state 目录；交还成功才删：审查 D1）
ROBOTD_PROBE_S = 5.0     # 舵机会话期间多久看一眼 robotd 起没起来（审查 D9）
# 手柄快照里带说明文字的长列表：没变就不每帧推（总览重排审查 O12：一帧 4.8 KB，WiFi 跟蓝牙是同一颗芯片）。
# 每 1 秒还是整份推一次，页面按缺了哪个就沿用上一份
PAD_HEAVY = ("skills", "actions", "combos", "sounds")


class HostGuard:
    """ASGI 中间件：Host 不认识的请求直接 421（挡 DNS 重绑定）。WebSocket 不认识就关掉。"""

    def __init__(self, app, guard, log):
        self.app, self.guard, self.log = app, guard, log

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and (scope.get('path') == '/api/ai' or scope.get('path','').startswith('/api/updates')):
            original_send = send
            async def private_send(message):
                if message['type'] == 'http.response.start':
                    message = {**message, 'headers':[(k,v) for k,v in message.get('headers',[])
                        if k.lower() != b'cache-control'] + [(b'cache-control',b'no-store')]}
                await original_send(message)
            send = private_send
        if scope["type"] in ("http", "websocket"):
            host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
            local_ip = (scope.get("server") or (None,))[0]
            if not self.guard.host_ok(host, local_ip):
                # 措辞固定、Host 值放原文：别人换着 Host 刷，也只会被去重合并成一行
                self.log.warn("拒绝了一个 Host 不对的请求", cat="连接", raw=repr(host[:100]),
                              hint="正常用不会遇到；如果你用的是新地址，在启动参数 --name 里加上它")
                if scope["type"] == "http":
                    await PlainTextResponse("Host 不对", status_code=421)(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 4403})
                return
        await self.app(scope, receive, send)


class AppStatic(StaticFiles):
    """页面自己的文件每次都问一下有没有更新（改了马上生效）；vendor、model 这些不会变的缓存一天。"""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        path = scope.get("path", "")
        if path.startswith(("/vendor/", "/model/")):
            resp.headers["Cache-Control"] = "public, max-age=86400"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def _client_desc(conn):
    ip = conn.client.host if conn.client else "?"
    ua = conn.headers.get("user-agent", "")
    kind = "手机" if any(k in ua for k in ("iPhone", "Android", "Mobile")) else "电脑"
    return f"{kind} {ip}"


def _bus_take(by, servo=None):
    """停主程序、把舵机总线拿过来（进舵机页时调，放线程里）。小助手在就调它（bus-take：不卸力、立旗）；
    不在（电脑上开发、台架、演示）当没做——servo.open 自己的 robotd_active 检查会接着报错。回 (ok, 原因)。"""
    if not helper.available():
        if servo is not None:
            servo.helper_available = False
        return True, None
    if servo is not None:
        servo.helper_available = True
    ok, result, detail = helper.bus_take(by)
    if ok:
        return True, None
    return False, helper.friendly(result, detail, "bus-take")


def _bus_give_back(by, servo=None):
    """交还以后起主程序（点「交还主程序」/ 离开舵机页时调，放线程里）。小助手在就调它
    （bus-give-back soft：撤旗、起 robotd、等启动四步成功）；不在当没做。回 (ok, 原因)。"""
    if not helper.available():
        if servo is not None:
            servo.helper_available = False
        return True, None
    if servo is not None:
        servo.helper_available = True
    ok, result, detail = helper.bus_give_back(by, soft=True, timeout=60)
    if servo is not None:
        # 主程序其实在回话、只是一直报启动阶段（旧 fork 的标定模式）：交还后的提示不能叫人手动起 robotd
        servo.giveback_stuck = bool(isinstance(detail, dict) and detail.get("still_starting"))
    if ok:
        return True, None
    return False, helper.friendly(result, detail, "bus-give-back")


def build_app(*, auth, robotd, log, web_dir, duck, names=(), state_dir=None, dev_autologin=False, servo=None, imu=None, pad=None,
              onboard=None, battery=None, tune=None, net=None, commit=None, ssh_home=None, models=None, sensors=None, updates=None,
              ca_cert=None):
    guard = OriginGuard(names=names)
    ai = AiService(state_dir)
    updates = updates or Updates()
    if pad is not None:
        pad.update_guard=updates.installing
    update_commands = set()
    mapping_requests = set()

    def update_readiness(*,native_transaction=None):
        busy = any(not task.done() for task in update_commands) or model_upload_gate.locked()
        motion = getattr(pad,'motion_studio',None)
        native=updates.native_state() if native_transaction is not None else {}
        own_prepared=(native.get('bundle_schema')=='microduck-controller-payload/1' and native.get('active') is True
            and native.get('phase')=='prepared' and isinstance(native_transaction,str)
            and native.get('transaction_id')==native_transaction)
        guarded=updates.marker.exists() or (updates.installing() and not own_prepared)
        if (guarded or busy or (servo is not None and servo.state != 'closed')
            or mapping_requests or (pad is not None and getattr(getattr(pad,'mapping',None),'blocked',False))
            or (motion is not None and (motion.state != 'idle' or motion.session is not None))
            or (imu is not None and (getattr(imu,'_redo_busy',False)
                or getattr(imu,'blocks_updates',lambda:False)()))):
            return {'ready':False,'error':'请结束舵机、标定、录制、回放和动作会话后重试'}
        return {'ready':True}
    if imu is not None and commit is not None:
        imu.commit = commit                          # 标定模式「站姿使能」：跟写进主程序同一套方向
    if onboard is not None:
        # 上手清单的两条记录从这两处来：测急停过了（pad.py 测急停线程）、IMU 页站着核过（imu.py）
        if pad is not None:
            pad.on_estop_passed = onboard.note_estop
        if imu is not None:
            imu.on_check = onboard.note_imu_check
        if commit is not None:
            onboard.commit_service = commit          # 第 3 步「写进主程序」的准备情况 / 完成判定
            commit.onboard = onboard                 # 第 1 / 2 步用户标了「已完成」「跳过」：准备情况那两项要知道
    web_dir = Path(web_dir)
    clients = {}           # websocket -> {"visible": bool, "by": str, "hb_at": monotonic}
    login_gate = asyncio.Semaphore(1)
    servo_gate = asyncio.Lock()         # 开 / 关舵机总线一次一个
    model_upload_gate = asyncio.Lock()
    def mapping_readiness():
        # Called in a pad worker: cached flags only. Robot poll latency must
        # not hide a servo takeover or an active IMU calibration session.
        from .pad_mapping import MappingError
        if (servo_gate.locked() or model_upload_gate.locked()
                or (models is not None and getattr(models,'_lock',None) is not None and models._lock.locked())
                or (commit is not None and getattr(commit,'_writing',None) is not None and commit._writing.locked())
                or (servo is not None and servo.state!='closed')
                or (imu is not None and (getattr(imu,'_redo_busy',False)
                    or getattr(imu,'blocks_updates',lambda:False)()))):
            raise MappingError('请先结束舵机、标定和模型更新会话，再重新映射手柄')
    if pad is not None:pad.mapping_guard=mapping_readiness
    # 舵机会话的标记（审查 D1）：没给 state 目录（测试）就不写
    marker = Path(state_dir) / SERVO_MARKER if state_dir and servo is not None else None   # 登录一个一个来：退避不会被并发绕过，scrypt 也不会同时吃掉几份 16 MB

    dev_sess = {"csrf": "dev", "auth_at": time.time(), "remember": False}
    # 用户 10-18：「不用设管理员密码，直接启动连就行」。没设密码 = 开放：同一个网络里打开就能用（跟 servo-web 一样）。
    # 设了密码才要登录（设置里以后能加）。Host / Origin 校验照旧：别的网站没法借你的浏览器控制鸭子
    open_sess = {"csrf": "open", "auth_at": time.time(), "remember": False}
    if dev_autologin:
        log.warn("开发模式：本机来的请求不用登录", cat="系统", hint="只在电脑上截图测试用，鸭子上不要开")

    def session_of(conn, touch=True):
        token = conn.cookies.get(COOKIE)
        if dev_autologin and conn.client and conn.client.host == "127.0.0.1":
            return token or "dev", dev_sess
        if not auth.has_password():
            return token or "open", open_sess
        return token, auth.session(token, touch=touch)

    def origin_ok(conn):
        return guard.origin_ok(conn.headers.get("origin"), conn.headers.get("host", ""))

    # ---- HTTP ----

    async def api_updates(request):
        try:
            _,sess=session_of(request)
            result=await handle_updates(request,updates,sess,auth.has_password(),
                dev_autologin and request.client and request.client.host=='127.0.0.1',origin_ok(request))
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except UpdateError as error:
            return JSONResponse({'ok':False,'code':error.code,'error':str(error)},status_code=error.status,headers={'Cache-Control':'no-store'})
        except Exception:
            return JSONResponse({'ok':False,'code':'service','error':'更新服务未完成请求，请稍后重试'},status_code=503,headers={'Cache-Control':'no-store'})

    async def api_ai(request):
        def response(data, status=200):
            return JSONResponse(data, status_code=status, headers={'Cache-Control':'no-store'})
        _, sess = session_of(request)
        local_dev = dev_autologin and request.client and request.client.host == '127.0.0.1'
        # The general Studio open session is unsuitable for billable external requests.
        if not auth.has_password() and not local_dev:
            return response({'ok':False,'code':'password_required','error':'请先在设置中配置网页密码，再使用 AI 连接'},403)
        if not sess:
            return response({'ok':False,'code':'unauthorized','error':'请先登录'},401)
        if request.method == 'POST' and (not origin_ok(request) or request.headers.get('x-csrf') != sess['csrf']):
            return response({'ok':False,'code':'csrf','error':'请求来源或登录令牌不正确'},403)
        try:
            if request.method == 'GET':
                result = await ai.view()
            else:
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise AiError('oversize','AI 请求过大',413)
                try:
                    body = json.loads(raw)
                except (ValueError,RecursionError):
                    raise AiError('validation','请求不是有效 JSON') from None
                result = await ai.handle(body)
            return response({'ok':True,**result})
        except AiError as error:
            return response({'ok':False,'code':error.code,'error':str(error)},error.status)
        except Exception:
            # Do not log exception details: network libraries may include request credentials.
            return response({'ok':False,'code':'internal','error':'AI 请求未完成，请稍后重试'},500)

    async def healthz(request):
        return JSONResponse({"ok": True, "version": VERSION,"suite_version":SUITE_VERSION,"runtime_root":str(RUNTIME_ROOT)})

    async def api_session(request):
        token, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False, "has_password": auth.has_password()}, status_code=401)
        return JSONResponse({"ok": True, "csrf": sess["csrf"], "duck": duck, "version": VERSION})

    async def api_login(request):
        if not origin_ok(request):
            return JSONResponse({"ok": False, "error": "请求来源不对"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        ip = request.client.host if request.client else "?"
        if not isinstance(body, dict):
            body = {}
        pw = str(body.get("password", ""))
        # 请求体从来不写日志，不用 log.hide(pw)（代码审查 #12：每个试过的密码都进打码表，
        # 试过 "true" 以后日志里所有 true 都会变成 [已隐藏]）
        async with login_gate:
            token, why, wait = await asyncio.to_thread(auth.login, pw, ip, bool(body.get("remember")))
        if not token:
            return JSONResponse({"ok": False, "error": why, "wait": wait}, status_code=401)
        resp = JSONResponse({"ok": True})
        # SameSite=Lax：从微信、聊天软件点链接进来也能带上登录（Strict 会每次都要求重新登录）；
        # 跨站请求由 Origin 校验和 CSRF 令牌挡
        resp.set_cookie(COOKIE, token, max_age=REMEMBER_S if body.get("remember") else None,
                        httponly=True, samesite="lax", path="/")
        return resp

    async def api_logout(request):
        token, sess = session_of(request)
        if sess and origin_ok(request) and request.headers.get("x-csrf") == sess["csrf"]:
            auth.logout(token)
            log.info(f"退出登录（{_client_desc(request)}）", cat="连接")
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    async def api_password(request):
        """在网页上设 / 改 / 清管理员密码。写的就是 run.py set-password 用的 password.json。"""
        token, sess = session_of(request)
        if not origin_ok(request):
            return JSONResponse({"ok": False, "error": "请求来源不对"}, status_code=403)
        if not sess:
            return JSONResponse({"ok": False, "error": "先登录才能改密码"}, status_code=401)
        if request.headers.get("x-csrf") != sess["csrf"]:
            return JSONResponse({"ok": False, "error": "请求校验失败，刷新页面再试"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        action = body.get("action", "set")
        ip = request.client.host if request.client else "?"
        if action == "clear":
            if auth.has_password():
                await asyncio.to_thread(auth.clear_password)
                log.info(f"管理员密码已清掉（{_client_desc(request)}）", cat="连接")
            resp = JSONResponse({"ok": True, "has_password": False})
            resp.delete_cookie(COOKIE, path="/")
            return resp
        if action != "set":
            return JSONResponse({"ok": False, "error": "不认识的操作"}, status_code=400)
        pw = body.get("password")
        confirm = body.get("confirm")
        if not isinstance(pw, str) or len(pw) < 6:
            return JSONResponse({"ok": False, "error": "密码至少 6 位"}, status_code=400)
        if pw != confirm:
            return JSONResponse({"ok": False, "error": "两次输入不一样"}, status_code=400)
        had_password = auth.has_password()
        keep = token if had_password and token and token != "open" else None
        try:
            await asyncio.to_thread(auth.set_password, pw, keep)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        resp = JSONResponse({"ok": True, "has_password": True})
        if not had_password:
            new_token, _, _ = await asyncio.to_thread(auth.login, pw, ip, False)
            if new_token:
                resp.set_cookie(COOKIE, new_token, httponly=True, samesite="lax", path="/")
        log.info(f"管理员密码已{'设置' if not had_password else '修改'}（{_client_desc(request)}）", cat="连接")
        return resp

    ssh_gate = asyncio.Lock()
    ssh_keys = SshKeys(ssh_home)        # Studio 以 radxa 身份跑：~/.ssh/authorized_keys 是它自己的文件，不用小助手

    async def ssh_view(detail=None):
        """密码登录开没开、ssh 服务（小助手 ssh-status；读不到就是 None）+ 登记的钥匙（Studio 自己读）。"""
        if detail is None and await asyncio.to_thread(helper.available):
            ok, _, d = await asyncio.to_thread(helper.ssh_status)
            detail = d if ok else None
        d = detail if isinstance(detail, dict) else {}
        try:
            keys = await asyncio.to_thread(ssh_keys.list)
        except OSError:
            keys = []
        return {"password_login": d.get("password_login") if isinstance(d.get("password_login"), bool) else None,
                "ssh_active": d.get("ssh_active"), "keys": len(keys), "key_list": keys, "user": d.get("user") or "radxa"}

    async def api_ssh(request):
        """「更多」页的 SSH：status / on（用户自己定的密码）/ off / add_key（贴自己的公钥）/ remove_key。
        镜像出厂只认钥匙、不带任何人的钥匙，也没有人人都知道的默认密码。打开密码登录、登记钥匙都要先设网页登录密码：
        不然连上热点（密码公开）的人都能打开网页、顺手打开 SSH 拿到 root。密码只交给小助手的 stdin，不进日志。"""
        _, sess = session_of(request)
        if not origin_ok(request):
            return JSONResponse({"ok": False, "error": "请求来源不对"}, status_code=403)
        if not sess:
            return JSONResponse({"ok": False, "error": "先登录"}, status_code=401)
        if request.headers.get("x-csrf") != sess["csrf"]:
            return JSONResponse({"ok": False, "error": "请求校验失败，刷新页面再试"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        action = body.get("action", "status")
        if action not in ("status", "on", "off", "add_key", "remove_key"):
            return JSONResponse({"ok": False, "error": "不认识的操作"}, status_code=400)
        if action in ("on", "add_key") and not auth.has_password():
            what = "打开 SSH" if action == "on" else "登记钥匙"
            return JSONResponse({"ok": False, "error": f"先在上面设网页登录密码，再{what}：不然连上热点的人都能打开网页、"
                                                     "顺手把 SSH 打开"}, status_code=403)
        if action == "on":
            pw, confirm = body.get("password"), body.get("confirm")
            if not isinstance(pw, str) or not 8 <= len(pw) <= 64:
                return JSONResponse({"ok": False, "error": "SSH 密码要 8–64 位"}, status_code=400)
            if pw != confirm:
                return JSONResponse({"ok": False, "error": "两次输入不一样"}, status_code=400)
        by = _client_desc(request)
        async with ssh_gate:
            if action == "status":
                return JSONResponse({"ok": True, "ssh": await ssh_view()})
            if action in ("add_key", "remove_key"):
                try:
                    if action == "add_key":
                        added, k = await asyncio.to_thread(ssh_keys.add, body.get("key"))
                    else:
                        gone = await asyncio.to_thread(ssh_keys.remove, body.get("fp"))
                except BadKey as e:
                    return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
                except OSError as e:
                    log.error("SSH 钥匙没写进去", cat="系统", raw=f"{type(e).__name__}: {e}", step=action,
                              hint="看看 /home/radxa/.ssh 的权限；还不行就导出诊断包")
                    return JSONResponse({"ok": False, "error": f"写不进 authorized_keys：{e}"}, status_code=500)
                if action == "add_key":
                    what = f"{k['type']} {k['comment'] or '（没注释）'} {k['fp']}"
                    log.info(f"SSH 钥匙{'登记了' if added else '本来就在'}：{what}（{by}）", cat="系统")
                    return JSONResponse({"ok": True, "added": added, "fp": k["fp"], "ssh": await ssh_view()})
                if gone:
                    log.info(f"SSH 钥匙删掉了：{body.get('fp')}（{by}）", cat="系统")
                return JSONResponse({"ok": True, "removed": gone, "ssh": await ssh_view()})
            if not await asyncio.to_thread(helper.available):
                return JSONResponse({"ok": False, "error": "这只鸭子没装 root 小助手，网页里开不了 SSH 密码登录"},
                                    status_code=503)
            if action == "on":
                ok, result, detail = await asyncio.to_thread(helper.ssh_password, by, True, body["password"])
            else:
                ok, result, detail = await asyncio.to_thread(helper.ssh_password, by, False)
            word = "打开" if action == "on" else "关掉"
            if not ok:
                why = helper.friendly(result, detail, "ssh-password")
                log.warn(f"SSH 密码登录没{word}：{why}", cat="系统", step=f"ssh-password {action}",
                         hint="照提示改；还不行就导出诊断包")
                return JSONResponse({"ok": False, "error": why},
                                    status_code=400 if result in ("bad_password", "bad_args") else 502)
            log.info(f"SSH 密码登录{word}了（{by}）", cat="系统")
            return JSONResponse({"ok": True, "ssh": await ssh_view()})

    async def api_logs(request):
        _, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False}, status_code=401)
        try:
            limit = max(1, min(int(request.query_params.get("limit", 200)), 500))
        except ValueError:
            return JSONResponse({"ok": False, "error": "limit 要是数字"}, status_code=400)
        return JSONResponse({"ok": True, "items": log.recent(limit=limit)})

    async def api_diag(request):
        """诊断包：最近两天的日志 + 当时的状态 + 版本 + robotd 的系统日志。发群里求助用。"""
        _, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False}, status_code=401)
        log.info(f"导出诊断包（{_client_desc(request)}）", cat="系统")
        # 状态在事件循环里取好再交给线程（不跨线程读正在变的东西）
        snap = summarize(robotd, duck, servo, ob=onboard, bat=battery, net=net, commit=commit, auth=auth)
        raw = {"hello": robotd.hello.value, "health": robotd.health.value, "busStatus": robotd.bus.value,
               "policies": robotd.policies.value, "calibration": robotd.calib.value,
               "pad": robotd.pad.value, "system": robotd.sysinfo.value}
        if onboard is not None:
            raw["onboarding_records"] = dict(onboard.rec)        # 上手清单记下的（时间、哪只手柄、指纹）
        if pad is not None:
            try:
                raw["pad_page"] = pad.diag()
            except Exception as e:  # noqa: BLE001
                raw["pad_page"] = f"出错：{type(e).__name__}: {e}"
        if tune is not None:
            try:
                raw["tune"] = tune.diag()                          # 调参快照（调参方案 §5：文件、回读）
            except Exception as e:  # noqa: BLE001
                raw["tune"] = f"出错：{type(e).__name__}: {e}"
        data = await asyncio.to_thread(_diag_zip, log, snap, raw)
        name = f"studio-diag-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def api_profile(request):
        """导出鸭子档案（给训练用，调参方案 T3）：只读汇总，JSON 下载。"""
        _, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False}, status_code=401)
        if tune is None:
            return JSONResponse({"ok": False, "error": "这只鸭子没开调参卡的后端"}, status_code=404)
        log.info(f"导出鸭子档案（{_client_desc(request)}）", cat="调参")
        data = json.dumps(tune.profile(), ensure_ascii=False, indent=1, default=str).encode("utf-8")
        name = f"duck-profile-{time.strftime('%Y%m%d-%H%M%S')}.json"
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def sensor_read(request, kind):
        headers = {"Cache-Control": "no-store"}
        _, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False, "error": "先登录才能查看传感器"}, status_code=401, headers=headers)
        if sensors is None:
            return JSONResponse({"ok": False, "error": "当前未开启传感器页面的后端"}, status_code=503, headers=headers)
        try:
            data = await getattr(sensors, kind)()
            return JSONResponse({"ok": True, "data": data}, headers=headers)
        except Exception as e:
            label = "摄像头" if kind == "camera" else "ToF"
            log.error(f"读取{label}状态失败", cat="传感器", raw=f"{type(e).__name__}: {e}", exc=True)
            return JSONResponse({"ok": False, "error": f"暂时无法读取{label}状态，请稍后重试或查看日志"},
                                status_code=503, headers=headers)

    async def api_camera(request):
        return await sensor_read(request, "camera")

    async def api_tof(request):
        return await sensor_read(request, "tof")

    def model_response(payload, status=200):
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    def model_access(request, *, write=False):
        _, sess = session_of(request)
        if not sess:
            return model_response({"ok": False, "error": "先登录才能管理模型"}, 401)
        if write and (not origin_ok(request) or request.headers.get("x-csrf") != sess["csrf"]):
            return model_response({"ok": False, "error": "请求校验失败，刷新页面再试"}, 403)
        if write and updates.installing():
            return model_response({'ok':False,'error':'软件更新正在进行，请等待完成'},409)
        if write and (mapping_requests or (pad is not None and pad.mapping.blocked)):
            return model_response({'ok':False,'error':'请先完成或取消手柄映射并回中，再修改模型'},409)
        if models is None:
            return model_response({"ok": False, "error": "当前模式没有开启模型管理"}, 503)
        return None

    async def model_result(operation):
        try:
            return model_response({"ok": True, "data": await operation()})
        except ValueError as e:
            return model_response({"ok": False, "error": str(e), "code": getattr(e, "code", "invalid")},
                                  getattr(e, "status", 400))
        except Exception as e:  # 不把上传内容写进日志
            log.error("模型管理操作失败", cat="模型", raw=f"{type(e).__name__}: {e}", exc=True)
            return model_response({"ok": False, "error": "模型管理暂时出错，请查看日志后重试"}, 500)

    async def limited_body(request, limit):
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) < 0:
                    raise ValueError()
                if int(length) > limit:
                    raise OverflowError()
            except ValueError:
                raise ValueError("请求大小不正确") from None
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > limit:
                raise OverflowError()
            data.extend(chunk)
        return data

    async def api_models(request):
        refused = model_access(request)
        if refused is not None:
            return refused
        return await model_result(models.view)

    async def api_model_op(request):
        refused = model_access(request, write=True)
        if refused is not None:
            return refused
        try:
            body = json.loads(await limited_body(request, MODEL_OP_LIMIT))
            if not isinstance(body, dict):
                raise ValueError()
        except OverflowError:
            return model_response({"ok": False, "error": "操作请求过大"}, 413)
        except (ValueError, UnicodeError, RecursionError):
            return model_response({"ok": False, "error": "操作请求格式不正确"}, 400)
        task=asyncio.current_task();update_commands.add(task)
        try:
            refused=model_access(request,write=True)
            if refused is not None:return refused
            return await model_result(lambda: models.handle(body, by=_client_desc(request)))
        finally:update_commands.discard(task)

    async def api_model_import(request):
        refused = model_access(request, write=True)
        if refused is not None:
            return refused
        # 原文件名仅用于判定格式。暂存文件采用系统随机名，永不拼接用户路径。
        filename = request.query_params.get("filename", "")
        if not filename or len(filename) > 200 or any(c in filename for c in ("/", "\\", ":", "\x00")):
            return model_response({"ok": False, "error": "请选择模型部署包文件"}, 400)
        suffix = next((s for s in (".tar.gz", ".tgz", ".zip") if filename.lower().endswith(s)), None)
        if suffix is None:
            return model_response({"ok": False, "error": "请选择 ZIP、tar.gz 或 tgz 模型部署包"}, 400)
        name = request.query_params.get("name", "")
        if len(name) > 80:
            return model_response({"ok": False, "error": "模型名称最多 80 个字符"}, 400)
        async with model_upload_gate:
            temporary = None
            try:
                refused=model_access(request,write=True)
                if refused is not None:return refused
                length = request.headers.get("content-length")
                if length is not None and int(length) > MODEL_UPLOAD_LIMIT:
                    raise OverflowError()
                upload_dir = Path(state_dir or auth.dir) / "model-uploads"
                await asyncio.to_thread(upload_dir.mkdir, parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(suffix=suffix, dir=upload_dir)
                total = 0
                with os.fdopen(fd, "wb") as output:
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > MODEL_UPLOAD_LIMIT:
                            raise OverflowError()
                        await asyncio.to_thread(output.write, chunk)
                if not total:
                    raise ValueError("模型部署包是空的")
                return await model_result(lambda: models.import_package(Path(temporary), name=name))
            except OverflowError:
                return model_response({"ok": False, "error": "模型部署包不能超过 64 MiB"}, 413)
            except ValueError as e:
                return model_response({"ok": False, "error": str(e) or "请求格式不正确"}, 400)
            finally:
                if temporary is not None:
                    await asyncio.to_thread(Path(temporary).unlink, missing_ok=True)

    async def meshes(request):
        """3D 模型 2.8 MB：预先压好的 .gz 直接给（不在板子上现压），浏览器缓存一天。"""
        raw = web_dir / "model" / "meshes.bin"
        gz = raw.with_suffix(".bin.gz")
        headers = {"Cache-Control": "public, max-age=86400", "Vary": "Accept-Encoding"}
        if gz.is_file() and "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
            return FileResponse(gz, media_type="application/octet-stream", headers=headers)
        return FileResponse(raw, media_type="application/octet-stream", headers=headers)

    # ---- WebSocket ----

    def update_viewers():
        robotd.set_viewers(sum(1 for c in clients.values() if c["visible"]))

    tune_err = {"logged": False}

    def tune_view():
        """调参卡的快照；出错只记一次日志、回 None（不能因为它把整条页面连接弄断）。"""
        try:
            v = tune.view()
            tune_err["logged"] = False
            return v
        except Exception as e:  # noqa: BLE001
            if not tune_err["logged"]:
                tune_err["logged"] = True
                log.error("拼调参卡的数据时出错", cat="调参", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 的问题，导出诊断包发出来；不影响站和走")
            return None

    async def ws_endpoint(ws: WebSocket):
        token, sess = session_of(ws)
        if not origin_ok(ws):
            log.warn(f"拒绝了一个来源不对的页面连接（{_client_desc(ws)}）", cat="连接",
                     raw=f"Origin={ws.headers.get('origin')!r} Host={ws.headers.get('host')!r}",
                     hint="正常打开鸭子的网页不会遇到；如果是别的网站在偷连，这样拒掉是对的")
            await ws.close(code=4403)
            return
        if not sess:
            log.info(f"页面连接没带有效登录，让它重新登录（{_client_desc(ws)}）", cat="连接")
            await ws.accept()
            await ws.close(code=4401)
            return
        await ws.accept()
        by = _client_desc(ws)
        pad_conn = pad.new_conn() if pad is not None else None
        me = {"visible": True, "by": by, "hb_at": time.monotonic(), "route": None, "pad_visible": None}
        clients[ws] = me
        update_viewers()
        log.info(f"页面连上（{by}，现在 {len(clients)} 个）", cat="连接", dedup=False)
        logq = log.subscribe()
        send_lock = asyncio.Lock()

        async def send(obj):
            async with send_lock:
                await ws.send_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        loop = asyncio.get_running_loop()      # 写主程序在后台线程跑，进度从这里推回页面

        async def sender():
            await send({"t": "hello", "version": VERSION, "duck": duck})
            last_check = time.monotonic()
            last_imu = 0.0
            last_tune = 0.0
            pad_tick = 0
            pad_lists = None                    # 上一次推出去的手柄长列表（没变就不再推：审查 O12）
            state_tick = 0
            onb_sent = None                     # 上一次推出去的上手清单（没变就不再带：代码审查 C5）
            while True:
                quiet = time.monotonic() - me["hb_at"]
                if quiet > HB_DEAD_S:
                    # Background timer throttling must not end server-owned music.
                    # A matched native Pong renews transport liveness only: it never
                    # changes hb_at, visible viewers, drive-area state or ownership.
                    music = pad is not None and pad.motion_studio.background_music(pad_conn)
                    ping = ws.scope.get("extensions", {}).get("studio.websocket.ping")
                    transport_alive = music and time.monotonic() - me.get("pong_at", 0.) <= HB_DEAD_S
                    if music and not transport_alive and callable(ping):
                        transport_alive = await ping(MUSIC_PONG_TIMEOUT_S)
                        if transport_alive:
                            me["pong_at"] = time.monotonic()
                    if not transport_alive:
                        log.info(f"页面 {HB_DEAD_S:.0f} 秒没消息且连接未确认，当作断了（{by}）", cat="连接")
                        await ws.close(code=4408)
                        return
                if me["visible"] and quiet > HB_QUIET_S:
                    me["visible"] = False          # 没心跳了：先当看不见，别让 robotd 白组帧
                    update_viewers()
                if me["visible"]:
                    st = summarize(robotd, duck, servo, ob=onboard, bat=battery, net=net, commit=commit, auth=auth)
                    # 上手清单一份 1 KB 左右：没变就不每帧带（页面沿用上一份），每秒还是整份带一次（代码审查 C5，
                    # 跟手柄快照的长列表一个办法）
                    state_tick += 1
                    onb = st.get("onboarding")
                    if onb is not None and onb == onb_sent and state_tick % STATE_HZ != 0:
                        del st["onboarding"]
                        st["onboarding_same"] = True
                    else:
                        onb_sent = onb
                    await send(st)
                    # 舵机表一帧 5.6 KB，只有舵机页用：只在舵机页、或者总线接管着的时候推（总览重排审查 O12）
                    if servo is not None and (me.get("route") == "/servos" or servo.state in ("open", "opening")):
                        # 接管了总线：推总线上读到的整张表；没接管：用主程序的数据拼一张只读的表
                        await send(servo.snapshot() if servo.state == "open"
                                   else view_from_robotd(robotd, servo.directions, servo.state,
                                                         servo.error, servo.sim))
                    if imu is not None and time.monotonic() - last_imu >= 0.5:
                        last_imu = time.monotonic()        # IMU 页要的芯片、安装方向、读数状态：半秒一次够了
                        await send(imu.view())
                    if (tune is not None and time.monotonic() - last_tune >= 1.0
                            and me.get("route") in ("/", "/pad", "/settings")):     # 还没收到心跳（不知道在哪页）先不推
                        last_tune = time.monotonic()       # 总览下面的「运行和调参」、设置页的模型：1 秒一次（调参方案 T1–T3）
                        tv = tune_view()
                        if tv is not None:
                            await send(tv)
                    if pad is not None:
                        # 手柄那一块在屏幕上的页面 10 Hz，别的 1 Hz（够仪表带的手柄格用）。只看心跳里的 pad_visible；
                        # 老页面没带才按路由兜底（审查 O1：#/pad 现在是总览上的锚点，地址是它不代表看得到）
                        pad_tick += 1
                        hot = me["pad_visible"] if me.get("pad_visible") is not None else me.get("route") == "/pad"
                        full = pad_tick % STATE_HZ == 0
                        if hot or full:
                            try:
                                snap = pad.safe_snapshot(conn=pad_conn)    # 自己不抛；同一种错只记一次日志
                            except Exception:  # noqa: BLE001
                                snap = None
                            if snap is not None:
                                m = snap.get("motion") if isinstance(snap.get("motion"), dict) else None
                                if m is not None:
                                    lists = {k: m.get(k) for k in PAD_HEAVY}
                                    if not full and lists == pad_lists:
                                        snap = dict(snap, motion={k: v for k, v in m.items() if k not in PAD_HEAVY},
                                                    lists_same=True)
                                    else:
                                        pad_lists = lists
                                await send(snap)
                    await asyncio.sleep(1 / STATE_HZ)
                else:
                    await send({"t": "hb"})
                    await asyncio.sleep(1.0)
                if time.monotonic() - last_check > 5:
                    last_check = time.monotonic()
                    # 只查不续期：页面开着不等于有人在用，12 小时没操作照样过期
                    if session_of(ws, touch=False)[1] is None:
                        await ws.close(code=4401)
                        return

        async def log_forwarder():
            while True:
                entry = await logq.get()
                await send({"t": "log", "entry": entry})

        cmd_tasks = set()

        async def run_relax(cid):
            if pad is not None:
                pad.note_relax(by)
            try:
                if servo is not None and servo.state == "open":
                    # 调舵机时 robotd 停着：卸力直接走总线，回读确认（servo.relax_all）
                    accepted, reason = await asyncio.to_thread(servo.relax_all, by)
                elif servo is not None and servo.orphan and robotd.present is not True:
                    # 上次退出时会话开着、这次没接管上（审查 D1）：robotd 不在，发给它也没用；总线上卸要先接管
                    log.warn("卸力：上次退出时舵机会话开着、这次没接管上，没法在总线上卸", cat="安全", raw=f"by={by}",
                             hint="去舵机页接管总线后再按卸力；急的话关电源")
                    accepted, reason = False, "上次 Studio 退出时可能还有舵机保持着：先去舵机页接管总线才能卸力（急的话关电源）"
                else:
                    accepted, reason = await robotd.relax(by)
            except Exception as e:  # noqa: BLE001 —— 不该发生；发生了也要给页面一个回执
                log.error("处理卸力命令时 Studio 出错", cat="命令", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 自己的 bug；先用手柄 − 或关电源，再把诊断包发出来")
                accepted, reason = False, f"Studio 出错：{type(e).__name__}"
            try:
                await send({"t": "result", "id": cid, "ok": accepted, "reason": reason})
            except Exception:  # noqa: BLE001 —— 页面已经断了：卸力照样发出去了，回执送不到无所谓
                pass

        async def run_servo(name, cid, args=None):
            if servo is None:
                await send({"t": "result", "id": cid, "ok": False, "reason": "这只鸭子没配舵机总线"})
                return
            async with servo_gate:
                if name == "servo.open":
                    # 用户 10-18：点进舵机页就自动接管。鸭子在走 / 策略在跑 / 正在站起来时不接管：关节会冻在半步
                    pose = summarize(robotd, duck, net=net, auth=auth)["posture"] if servo.state != "open" else None
                    if pose in ("walking", "policy", "homing"):
                        ok, why = False, "鸭子在走或者策略在跑：先停下或坐下，再进舵机页（现在只读）"
                    else:
                        was_open = servo.state == "open"
                        if was_open:
                            # 会话一直开着、本来就是 paused：成功失败都不碰轮询（审查 #3）
                            ok, why = await asyncio.to_thread(servo.open, by, (args or {}).get("port"))
                        else:
                            # 一键接管：先停主程序（小助手 bus-take，不卸力、立旗）。小助手没装就交给
                            # servo.open 自己的 robotd_active 检查去报错
                            robotd.set_paused(True, "调舵机")
                            taken, take_why = await asyncio.to_thread(_bus_take, by, servo)
                            if not taken:
                                ok, why = False, take_why
                                if helper.flag_present():
                                    why = f"{why}。主程序现在被旗挡着起不来：处理好以后再点「连接」接管、用完点交还；或者重启鸭子"
                                robotd.set_paused(False)
                            else:
                                ok, why = await asyncio.to_thread(servo.open, by, (args or {}).get("port"))
                                if not ok:
                                    robotd.set_paused(False)
                                    # 主程序停了、总线却没开成：起回来，别留个旗让主程序一直起不来
                                    asyncio.create_task(asyncio.to_thread(_bus_give_back, by, servo))
                                else:
                                    servo.orphan = servo.conflict_at = None
                                    servo.handed_back_at = None
                                    servo.handback_note = servo.giveback_error = servo.giveback_hint = None
                                    await remember_marker(by)
                elif (args or {}).get("if_idle"):
                    # 离开舵机页：有舵机保持着（当场读）、读不到扭矩、校准 / 运动在跑、别的页面还在舵机页，都不交还
                    # （用户 10-18：「舵机这个页面出去的时候应该保持住使能，不应该泄力」；审查 D2、D3）。
                    # fork 的 robotd 一起来就先全部卸力（duck-control feetech.rs:1495 confirm_startup_limp），交还必然软，
                    # 所以只能留着总线会话；要交还点「交还主程序」（不带 if_idle）
                    done, kept = await leave_servos(f"离开舵机页（{by}）", ws, by)
                    if done:
                        ok, why = True, None
                    else:
                        log.info(f"离开舵机页：{kept}，不交还（总线会话留着；点「交还主程序」才交还）", cat="总线",
                                 raw=f"by={by}", dedup=False)
                        ok, why = True, f"{kept}：没交还，会话留着"
                else:
                    ok, why = await hand_back(f"点了「交还主程序」（{by}）", by)
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": why})
            except Exception:  # noqa: BLE001
                pass

        async def run_servo_op(cid, args):  # noqa: C901
            """舵机页的操作（扭矩、滑块、运动、校准、扫描、寄存器……）：servo_ops.handle 在线程里跑。"""
            from .servo_ops import Refused
            data, ok, reason = None, True, None
            ops = servo.ops if servo is not None else None
            if ops is None:
                ok, reason = False, "舵机总线还没接管：刷新舵机页重试"
            else:
                # 在事件循环里、进线程之前就记账（审查 D2）：「保持」跟「离开」同一个包里到，交还也会等它做完再读扭矩
                try:
                    tok = servo.op_begin(args.get("op"))
                except Refused as e:
                    ok, reason, tok = False, str(e), None
                if tok is not None:
                    try:
                        data = await asyncio.to_thread(ops.handle, args, tok)
                    except Refused as e:
                        ok, reason = False, str(e)
                    except Exception as e:  # noqa: BLE001
                        ok, reason = False, f"Studio 出错：{type(e).__name__}: {e}"
                        log.error(f"舵机操作出错：{args.get('op')}", cat="总线", raw=f"{type(e).__name__}: {e}", exc=True,
                                  hint="这是 Studio 的问题，导出诊断包发出来")
                    finally:
                        servo.op_end(tok)
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": reason, "data": data})
            except Exception:  # noqa: BLE001
                pass

        async def run_imu_op(cid, args):
            """IMU 页的操作：现在只有 solve（算安装方向）；写入、粗校、换芯片要上鸭子再接。"""
            from .imu import Refused as ImuRefused
            data, ok, reason = None, True, None
            if imu is None:
                ok, reason = False, "这只鸭子没开 IMU 页的后端"
            else:
                try:
                    data = await imu.handle(args, by)
                except ImuRefused as e:
                    ok, reason = False, str(e)
                except Exception as e:  # noqa: BLE001
                    ok, reason = False, f"Studio 出错：{type(e).__name__}: {e}"
                    log.error(f"IMU 操作出错：{args.get('op')}", cat="IMU", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": reason, "data": data})
            except Exception:  # noqa: BLE001
                pass

        async def run_calib_op(cid, args):
            """上手清单第 3 步：「先看看要写什么」= 小助手 dry_run；「写进主程序」= 校验准备情况后交给小助手写、重启主程序。"""
            from .commit import Refused as CommitRefused
            op = args.get("op") if isinstance(args, dict) else None
            if commit is None:
                await send({"t": "result", "id": cid, "ok": False, "reason": "这只鸭子没开「写进主程序」"})
                return
            if op == "preview":
                try:
                    data = await asyncio.to_thread(commit.preview, by)
                except CommitRefused as e:
                    await send({"t": "result", "id": cid, "ok": False, "reason": str(e)})
                    return
                except Exception as e:  # noqa: BLE001
                    log.error("算「要写什么」时出错", cat="校准", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
                    await send({"t": "result", "id": cid, "ok": False,
                                "reason": f"Studio 出错：{type(e).__name__}: {e}"})
                    return
                await send({"t": "result", "id": cid, "ok": True, "data": data})
                return
            if op != "commit":
                await send({"t": "result", "id": cid, "ok": False, "reason": f"不认识的写入操作：{op}"})
                return
            try:
                view = commit.view(robotd, servo)
            except Exception as e:  # noqa: BLE001
                log.error("查「写进主程序」的准备情况时出错", cat="校准", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 的问题，导出诊断包发出来")
                await send({"t": "result", "id": cid, "ok": False, "reason": f"Studio 出错：{type(e).__name__}: {e}"})
                return
            if not view.get("ready"):
                missing = [i for i in view.get("items") or [] if not i.get("ok")]
                reason = "还没准备好：" + "；".join(f"{i.get('label')}：{i.get('text')}" for i in missing[:3])
                if len(missing) > 3:
                    reason += "…"
                await send({"t": "result", "id": cid, "ok": False, "reason": reason, "data": {"readiness": view}})
                return
            # 先回执「开始写」，后面全在后台线程里：页面不能被 90 秒的重启卡住，卸力也照常能点
            await send({"t": "commit", "phase": "started", "msg": "开始写进主程序：会重启主程序（约 20 秒），鸭子会先卸力"})
            await send({"t": "result", "id": cid, "ok": True, "data": {"started": True, "readiness": view}})

            def progress(msg, detail=None):
                item = {"t": "commit", "phase": "running", "msg": msg}
                if isinstance(detail, dict):
                    item["detail"] = detail
                try:
                    asyncio.run_coroutine_threadsafe(send(item), loop)
                except Exception:  # noqa: BLE001 —— 页面断了不影响写
                    pass

            try:
                res = await asyncio.to_thread(commit.commit, by, progress)
            except Exception as e:  # noqa: BLE001
                log.error("写进主程序的后台任务出错", cat="校准", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="看看主程序有没有起来；原来的文件有没有换回去，日志里有")
                res = {"ok": False, "reason": f"Studio 出错：{type(e).__name__}: {e}", "detail": {}}
            await send({"t": "commit", "phase": "done" if res.get("ok") else "failed", "ok": bool(res.get("ok")),
                        "reason": res.get("reason"), "detail": res.get("detail") or {}})

        async def run_tune_op(cid, args):
            """调参卡 / 设置页模型的操作：这一版只有 save_body（导出档案用的重量，只存在 Studio 里）；改鸭子的一律拒。"""
            from .tune import Refused as TuneRefused
            data, ok, reason = None, True, None
            if tune is None:
                ok, reason = False, "这只鸭子没开调参卡的后端"
            else:
                try:
                    data = await tune.handle(args, by)
                except TuneRefused as e:
                    ok, reason = False, str(e)
                except Exception as e:  # noqa: BLE001
                    ok, reason = False, f"Studio 出错：{type(e).__name__}: {e}"
                    log.error(f"调参操作出错：{args.get('op')}", cat="调参", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": reason, "data": data})
            except Exception:  # noqa: BLE001
                pass

        async def run_pad_op(cid, args):
            """手柄页的操作：pad.handle 在线程里跑（搜索、配对、动作自己开后台线程，这里马上回）。"""
            data, ok, reason = None, True, None
            if pad is None:
                ok, reason = False, "这只鸭子没开手柄页的后端（台架模式）"
            else:
                try:
                    data = await asyncio.get_running_loop().run_in_executor(
                        pad.executor_for(args), lambda: pad.handle(args, by, pad_conn))
                except PadRefused as e:
                    ok, reason = False, str(e)
                except Exception as e:  # noqa: BLE001
                    ok, reason = False, f"Studio 出错：{type(e).__name__}: {e}"
                    log.error(f"手柄操作出错：{args.get('op')}", cat="手柄", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": reason, "data": data})
            except Exception:  # noqa: BLE001
                pass

        async def onboard_op(cid, args):
            """上手清单上的按钮（只记一笔，不碰鸭子）：
                confirm_servo              舵机零位「已经校过，核对一下」（审查 O4；页面现在用「已完成」，老接口留着）
                done {step, confirmed}     「已完成」：我以前做过（用户 10-20：「去做、已完成、跳过，有三个也行」）
                skip {step, confirmed}     「跳过」：先不做（用户 10-18：「应该允许跳过、允许重新做」）
                undone / unskip {step, confirmed}   取消
            每一行任何时候都能点；有后果的（主程序明说没做完、读不到、测出了问题、测急停、调舵机时）要点两次，
            页面第二下才带 confirmed: true，后端按现在的情况再判一遍，没带就拒（不能只靠页面）"""
            op = args.get("op")
            if onboard is None:
                return {"t": "result", "id": cid, "ok": False, "reason": "这只鸭子没开上手清单"}
            what = {"confirm_servo": "「已经校过，核对一下」", "done": "「已完成」", "skip": "跳过", "unskip": "取消跳过",
                    "undone": "取消「已完成」"}.get(op)
            if what is None:
                return {"t": "result", "id": cid, "ok": False, "reason": f"不认识的指令：{op}"}
            step = str(args.get("step") or "")
            confirmed = args.get("confirmed") is True
            if op == "confirm_servo":
                ok, why = onboard.confirm_servo(robotd, by)
            elif op in ("done", "skip"):
                ok, why = onboard.mark(step, op, robotd, pad, servo, by, confirmed)
            else:
                ok, why = onboard.unmark(step, "skip" if op == "unskip" else "done", by, servo, confirmed)
            if ok:
                onboard.tick(robotd, pad, servo)               # 马上重判一次，页面不用等下一拍
                await asyncio.to_thread(onboard.save)
            else:
                log.info(f"上手清单{what}没记：{why}", cat="校准", step=step or "上手清单")
            return {"t": "result", "id": cid, "ok": ok, "reason": why}

        async def run_battery_op(cid, args):
            """设置页「电池」一栏：get / set / calibrate（studio/battery_svc.py）。只改 Studio 自己的设置，不碰鸭子。"""
            data, ok, reason = None, True, None
            if battery is None:
                ok, reason = False, "这只鸭子没开电量估算"
            else:
                try:
                    data = await battery.handle(args, by)
                except ValueError as e:
                    ok, reason = False, str(e)
                except Exception as e:  # noqa: BLE001
                    ok, reason = False, f"Studio 出错：{type(e).__name__}: {e}"
                    log.error(f"电池设置出错：{args.get('op')}", cat="电池", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
            try:
                await send({"t": "result", "id": cid, "ok": ok, "reason": reason, "data": data})
            except Exception:  # noqa: BLE001
                pass

        async def run_net_op(cid, args):
            """配网页：scan / connect / forget。connect 先回「开始切换」，真正关热点、连 WiFi 在后台做。"""
            from .net import Refused as NetRefused, validate_connect
            if net is None:
                await send({"t": "result", "id": cid, "ok": False, "reason": "这只鸭子没开配网"})
                return
            op = args.get("op")
            if op == "scan":
                try:
                    data = await net.scan()
                except NetRefused as e:
                    await send({"t": "result", "id": cid, "ok": False, "reason": str(e)})
                    return
                except Exception as e:  # noqa: BLE001
                    log.error("扫 WiFi 出错", cat="网络", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
                    await send({"t": "result", "id": cid, "ok": False, "reason": f"Studio 出错：{type(e).__name__}: {e}"})
                    return
                await send({"t": "result", "id": cid, "ok": True, "data": data})
                return
            if op == "connect":
                try:
                    ssid, psk = validate_connect(args.get("ssid"), args.get("psk"))
                    started = net.begin_connect()
                except NetRefused as e:
                    await send({"t": "result", "id": cid, "ok": False, "reason": str(e)})
                    return
                if not started:
                    await send({"t": "result", "id": cid, "ok": False, "reason": "正在连，等一下"})
                    return
                # 先让页面收到回执（显示「正在切换……」），2 秒后 Net.connect 才动热点
                await send({"t": "result", "id": cid, "ok": True, "reason": "开始切换",
                            "data": {"ssid": ssid}})

                async def do_connect():
                    try:
                        await net.connect(ssid, psk, by)
                    except Exception as e:  # noqa: BLE001
                        net.busy = None
                        log.error("连 WiFi 的后台任务出错", cat="网络", raw=f"{type(e).__name__}: {e}", exc=True,
                                  hint="这是 Studio 的问题，导出诊断包发出来")
                # 引用挂在 net 上，不挂在这个页面的 cmd_tasks：手机断开（热点一关就会断）以后页面的处理函数结束，
                # 后台这一路还要接着连完、写 last-try
                net.connect_task = asyncio.create_task(do_connect())
                return
            if op == "forget":
                try:
                    data = await net.forget(args.get("ssid"))
                except NetRefused as e:
                    await send({"t": "result", "id": cid, "ok": False, "reason": str(e)})
                    return
                except Exception as e:  # noqa: BLE001
                    log.error("忘掉 WiFi 出错", cat="网络", raw=f"{type(e).__name__}: {e}", exc=True,
                              hint="这是 Studio 的问题，导出诊断包发出来")
                    await send({"t": "result", "id": cid, "ok": False, "reason": f"Studio 出错：{type(e).__name__}: {e}"})
                    return
                await send({"t": "result", "id": cid, "ok": True, "data": data})
                return
            if op in ("hotspot-up", "hotspot-down"):
                try:
                    started = net.begin_hotspot()
                except NetRefused as e:
                    await send({"t": "result", "id": cid, "ok": False, "reason": str(e)})
                    return
                if not started:
                    await send({"t": "result", "id": cid, "ok": False, "reason": "正在处理 WiFi，等一下"})
                    return
                what = "开热点" if op == "hotspot-up" else "关热点"
                # 先回执：切 AP 模式会把这台手机的连接断掉，回执要在断之前发出去
                await send({"t": "result", "id": cid, "ok": True, "reason": f"正在{what}"})
                ssid = args.get("ssid") if isinstance(args.get("ssid"), str) else None

                async def do_hotspot():
                    try:
                        if op == "hotspot-up":
                            ok, reason = await net.hotspot_up(by)
                        else:
                            ok, reason = await net.hotspot_down(by, ssid)
                        if not ok:
                            log.info(f"{what}没成：{reason}", cat="网络", step=op)
                    except Exception as e:  # noqa: BLE001
                        net.busy = None
                        log.error(f"{what}的后台任务出错", cat="网络", raw=f"{type(e).__name__}: {e}", exc=True,
                                  hint="这是 Studio 的问题，导出诊断包发出来")
                # 跟 connect 一样：手机断线以后页面的处理函数结束，后台这一路还要跑完
                net.connect_task = asyncio.create_task(do_hotspot())
                return
            await send({"t": "result", "id": cid, "ok": False, "reason": f"不认识的配网操作：{op}"})

        async def run_onboard_op(cid, args):
            try:
                out = await onboard_op(cid, args)
            except Exception as e:  # noqa: BLE001
                log.error("上手清单的按钮出错", cat="系统", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 的问题，导出诊断包发出来")
                out = {"t": "result", "id": cid, "ok": False, "reason": f"Studio 出错：{type(e).__name__}"}
            try:
                await send(out)
            except Exception:  # noqa: BLE001
                pass

        async def receiver():
            while True:
                msg = await ws.receive_json()
                me["hb_at"] = time.monotonic()
                if not isinstance(msg, dict):
                    continue
                t = msg.get("t")
                if t == "hb":
                    me["route"] = msg.get("route")
                    if isinstance(msg.get("pad_visible"), bool):
                        me["pad_visible"] = msg["pad_visible"]
                    vis = bool(msg.get("visible", True))
                    if vis != me["visible"]:
                        me["visible"] = vis
                        update_viewers()
                elif t == "cmd":
                    name, cid = msg.get("name"), msg.get("id")
                    args = msg.get('args') or {}
                    if updates.installing() and not safe_during_update(name,args):
                        await send({'t':'result','id':cid,'ok':False,'reason':'软件更新正在进行，请等待完成；急停仍可使用'})
                        continue
                    if (imu is not None and getattr(imu, 'blocks_updates', lambda: False)()
                            and name in {'pad.op', 'tune.op', 'servo.open', 'servo.op', 'calib.op'}
                            and not safe_during_update(name, args)):
                        await send({'t':'result','id':cid,'ok':False,
                                    'reason':'IMU 标定 / 类型切换会话未结束：先验证完成或取消；卸力仍可使用'})
                        continue
                    if ((mapping_requests or (pad is not None and getattr(getattr(pad, 'mapping', None), 'blocked', False)))
                            and name in {'tune.op', 'servo.open', 'servo.op', 'calib.op', 'imu.op'}
                            and not safe_during_update(name, args)):
                        await send({'t':'result','id':cid,'ok':False,
                                    'reason':'请先完成或取消手柄映射并回中；网页卸力仍可使用'})
                        continue
                    if name not in COMMANDS:
                        await send({"t": "result", "id": cid, "ok": False, "reason": f"这一版还不支持「{name}」"})
                        continue
                    if name == "imu.op":
                        task = asyncio.create_task(run_imu_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "calib.op":
                        task = asyncio.create_task(run_calib_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "tune.op":
                        task = asyncio.create_task(run_tune_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "pad.op":
                        task = asyncio.create_task(run_pad_op(cid, msg.get("args") or {}))
                        if args.get('op')=='mapping' and args.get('action')=='begin':
                            # Reserve on the event loop before another command
                            # from the same WebSocket packet can start takeover.
                            mapping_requests.add(task)
                            task.add_done_callback(mapping_requests.discard)
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "onboard.op":
                        # 不在这里 await：写盘慢的时候也不能挡住后面来的卸力（代码审查 #6 同一个道理）
                        task = asyncio.create_task(run_onboard_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "net.op":
                        # 扫 WiFi 最坏约 10 秒、连 WiFi 在后台跑：都不挡住同一页面的卸力
                        task = asyncio.create_task(run_net_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "servo.op":
                        task = asyncio.create_task(run_servo_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "servo.ports":
                        from .servo import list_ports
                        ports = await asyncio.to_thread(list_ports)
                        await send({"t": "result", "id": cid, "ok": True, "data": {
                            "ports": ports, "port": servo.port if servo else None, "sim": bool(servo and servo.sim),
                            "state": servo.state if servo else None}})
                        continue
                    if name in ("servo.open", "servo.close"):
                        task = asyncio.create_task(run_servo(name, cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "battery.op":
                        task = asyncio.create_task(run_battery_op(cid, msg.get("args") or {}))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    if name == "relax":
                        # 卸力不在这里 await（代码审查 #6）：同一页面连按不排队，页面断了也不会把它取消
                        task = asyncio.create_task(run_relax(cid))
                        cmd_tasks.add(task)
                        update_commands.add(task)
                        task.add_done_callback(update_commands.discard)
                        task.add_done_callback(cmd_tasks.discard)
                        continue
                    # 进了 COMMANDS 却忘了写分支：明说，别默认当卸力（手柄页审查：每秒 10 次的 move 会全变成卸力）
                    log.error(f"指令「{name}」在 COMMANDS 里但没有处理分支", cat="系统", hint="这是 Studio 的 bug，导出诊断包发出来")
                    await send({"t": "result", "id": cid, "ok": False, "reason": f"Studio 没写「{name}」怎么处理（bug）"})

        tasks = [asyncio.create_task(f()) for f in (sender, log_forwarder, receiver)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                    log.warn("页面连接出错", cat="连接", raw=f"{type(exc).__name__}: {exc}")
        finally:
            for t in tasks:
                t.cancel()
            verification = getattr(imu, "home_verification_task", None)
            if verification in cmd_tasks:
                verification.cancel()
            log.unsubscribe(logq)
            clients.pop(ws, None)
            if pad is not None:
                pad.mark_gone(pad_conn)
                asyncio.get_running_loop().run_in_executor(None, pad.drop_client, pad_conn, by)
            update_viewers()
            log.info(f"页面断开（{by}，还剩 {len(clients)} 个）", cat="连接", dedup=False)

    # ---- 舵机会话：标记文件（审查 D1）、交还、离开、看 robotd（审查 D9） ----

    def marker_write(by):
        """开总线时写（线程里调）：先写临时文件再改名。回 True / False；写不进去只记 warn
        （重启以后就不知道上次还开着，但会话不能因此关掉：舵机可能正保持着）。"""
        if marker is None:
            return True
        data = {"v": 1, "port": servo.port, "sim": bool(servo.sim), "at": time.time(), "by": by,
                "boot_id": boot_id()}
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            tmp = marker.with_name(marker.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, marker)
            return True
        except OSError as e:
            log.warn("舵机会话的标记没写进去", cat="总线", step=str(marker), raw=f"{type(e).__name__}: {e}",
                     hint="Studio 重启以后就不知道上次还有舵机保持着；看看 state 目录满没满、有没有权限")
            return False

    async def remember_marker(by):
        """写会话标记并把它写没写进去记到 servo.marker_error（页面提示条用）。"""
        ok = await asyncio.to_thread(marker_write, by)
        servo.marker_error = None if ok else (
            "会话标记没写进去：Studio 重启后不知道这里还开着。别重启 Studio，用完点「交还主程序」")
        return ok

    def marker_drop():
        if marker is None:
            return
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warn("舵机会话的标记删不掉", cat="总线", step=str(marker), raw=f"{type(e).__name__}: {e}",
                     hint="下次 Studio 起来会以为上次还开着、去接管一次总线；看看 state 目录的权限")

    def marker_read():
        """(在不在, 内容)：内容 None = 文件坏了（读不出、不是 JSON 对象）。"""
        if marker is None:
            return False, None
        try:
            raw = marker.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False, None
        except OSError:
            return True, None
        try:
            d = json.loads(raw)
        except ValueError:
            return True, None
        return True, d if isinstance(d, dict) else None

    async def robotd_up():
        """robotd 的 socket 连得上吗（连上就关，不说话）。台架模式没有 robotd。"""
        addr = getattr(robotd, "addr", "none")
        if not addr or addr == "none":
            return False
        try:
            _, w = await asyncio.wait_for(open_conn(addr), 1.0)
        except Exception:  # noqa: BLE001 —— 连不上、超时都算不在
            return False
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
        return True

    async def handed_back(why, by=None):
        """总线已经卸力、关了：恢复跟主程序的轮询、起主程序、按结果删 / 留标记、记一行（每次都记：审查 D13）。"""
        servo.handed_back_at = time.monotonic()
        servo.orphan = None
        servo.marker_error = None
        servo.giveback_error = servo.giveback_hint = None
        robotd.set_paused(False)
        log.info(f"交还主程序：{why}", cat="总线", dedup=False)
        # 一键交还：起主程序（小助手 bus-give-back soft：撤旗、起 robotd、等启动四步成功）。
        # 标记等起完再删：没起来、旗还在的话留着，Studio 重启才能接着管（审查 #4）
        ok, gb_why = await asyncio.to_thread(_bus_give_back, by or "Studio", servo)
        if ok:
            await asyncio.to_thread(marker_drop)
            if servo.helper_available is False:
                log.warn("交还了；没装小助手，主程序要手动起：sudo systemctl start robotd", cat="总线", dedup=False)
            else:
                log.info("主程序起来了", cat="总线", dedup=False)
        else:
            servo.giveback_error = gb_why
            flag = await asyncio.to_thread(helper.flag_present)
            if flag:
                servo.giveback_hint = ("旗还在：手动 start robotd 会被挡住。再进一次舵机页、再点交还；"
                                       "或者重启鸭子（旗在 /run，重启就没了）")
            elif getattr(servo, "giveback_stuck", False):
                servo.giveback_hint = "主程序在回话，只是一直报启动阶段：多半是旧版主程序的毛病，去日志页看它最后说了什么"
                await asyncio.to_thread(marker_drop)
            else:
                servo.giveback_hint = "手动 sudo systemctl start robotd，或者再进一次舵机页再交还"
                await asyncio.to_thread(marker_drop)
            log.error(f"主程序没起来：{gb_why}", cat="总线", dedup=False, hint=servo.giveback_hint)

    async def hand_back(why, by=None):
        """点「交还主程序」：先置 closing、等在路上的舵机操作做完（等不到不交还），再在总线上全部卸力并回读确认
        （不指望主程序启动时卸力，审查 S1），再关总线、起主程序（handed_back，审查 D2）。"""
        if servo is None or servo.state != "open":
            return True, None
        ok, reason = await asyncio.to_thread(servo.hand_back_bus, why, before_close=lambda: robotd.set_paused(False))
        if not ok:
            log.error("没交还主程序", cat="总线", raw=reason, hint="等校准 / 动作做完再点「交还主程序」", dedup=False)
            return False, reason
        servo.handback_note = reason          # 成功时第二个值是「哪几颗没确认卸力」的提示；全确认是 None
        await handed_back(why, by)
        return True, None

    async def leave_servos(why, ws=None, by=None):
        """离开舵机页 / 没人在看：别的页面还在舵机页、校准 / 运动在跑、当场读到有保持的或读不到的 → 不交还，回 (False, 原因)；
        否则卸力、关总线、起主程序，回 (True, None)（审查 D2、D3）。"""
        others = sum(1 for w, c in clients.items() if w is not ws and c.get("route") == "/servos")
        if others:
            return False, f"还有 {others} 个页面在舵机页"
        verdict, info = await asyncio.to_thread(servo.leave, why, before_close=lambda: robotd.set_paused(False))
        if verdict == "handed_back":
            await handed_back(why, by)
            return True, None
        if verdict == "closed":
            return True, None
        return False, info["why"]

    async def robotd_conflict():
        """会话期间 robotd 被手动起来了（审查 D9）：它起来就把舵机全部松开、还跟 Studio 抢同一个串口。
        Studio 关总线（不写舵机）、删标记、恢复轮询，提示条变红。"""
        async with servo_gate:
            if servo.state != "open" or servo.closing:
                return
            await asyncio.to_thread(servo.close)
            servo.conflict_at = time.monotonic()
            await asyncio.to_thread(marker_drop)
            servo.marker_error = None
            robotd.set_paused(False)
        log.error("主程序被手动起来了：它会把舵机全部松开、跟 Studio 抢串口", cat="总线", step="会话期间查 robotd 的 socket",
                  hint="Studio 已经关掉舵机总线（没写舵机）。下次别手动起 robotd：先在页面上点「交还主程序」", dedup=False)

    async def servo_restore():
        """Studio 起来时：上次退出时舵机会话开着（有标记）→ robotd 不在就重新接管总线（只读打开，不改扭矩），恢复会话
        和提示条；接管不上 → 记成 orphan（提示条、卸力按钮引导去舵机页）；robotd 在 → 它起来时已经全部松开，删标记（审查 D1）。"""
        if servo is None:
            return
        if updates.installing():
            log.warn('主程序更新保护仍在：暂停自动恢复舵机会话，请先在软件更新页恢复主程序更新',cat='总线')
            return
        there, data = await asyncio.to_thread(marker_read)
        if not there:
            return
        if data is None:
            log.warn("舵机会话的标记坏了：照上次开着处理（用默认的串口）", cat="总线", step=str(marker),
                     hint="多半是写到一半断电了；不影响用")
            data = {}
        here = boot_id()
        if isinstance(data, dict) and data.get("boot_id") and here and data["boot_id"] != here:
            log.info("上次开机留下的舵机会话标记：断过电，舵机早就松了，作废", cat="总线", dedup=False)
            await asyncio.to_thread(marker_drop)
            return
        if await robotd_up():
            log.info("上次 Studio 退出时舵机会话开着；主程序现在在跑（它起来时会把舵机全部松开）：不接管", cat="总线",
                     dedup=False)
            await asyncio.to_thread(marker_drop)
            return
        port = "sim" if data.get("sim") is True else (data.get("port") if isinstance(data.get("port"), str) else None)
        async with servo_gate:
            if servo.state == "open":
                return
            robotd.set_paused(True, "调舵机（接着上次的会话）")
            # 恢复会话跟网页进舵机页一个样：先 bus_take 立旗（robotd 起不来、管理锁在、确认串口空），再开串口。
            # 不立旗就开串口，robotd 一起来会跟 Studio 抢同一个总线
            taken, take_why = await asyncio.to_thread(_bus_take, "Studio 重启后接着管", servo)
            if not taken:
                ok, why = False, take_why
                servo.orphan = {"why": take_why, "at": time.monotonic()}
                robotd.set_paused(False)
                # bus-take 半路失败可能已经把旗立了：起主程序回滚（撤旗 + 起 robotd），别留个旗让主程序一直起不来
                asyncio.create_task(asyncio.to_thread(_bus_give_back, "Studio 重启后接着管", servo))
            else:
                ok, why = await asyncio.to_thread(servo.open, "Studio 重启后接着管", port, True)
                if ok:
                    servo.orphan = None
                    servo.handed_back_at = None
                    servo.handback_note = servo.giveback_error = servo.giveback_hint = None
                    await remember_marker(data.get("by") if isinstance(data.get("by"), str) else "Studio 重启后接着管")
                else:
                    servo.orphan = {"why": why, "at": time.monotonic()}
                    robotd.set_paused(False)
                    # 主程序停了、总线却没开成：起回来，别留个旗让主程序一直起不来
                    asyncio.create_task(asyncio.to_thread(_bus_give_back, "Studio 重启后接着管", servo))
        if ok:
            log.info(f"会话恢复：上次 Studio 退出时舵机会话开着，重新接管了总线（只读打开，没改扭矩；{servo.holding()} 颗保持着）",
                     cat="总线", dedup=False)
        else:
            log.error("上次 Studio 退出时舵机会话开着，这次没接管上：可能还有舵机保持着", cat="总线", raw=why, dedup=False,
                      hint="去舵机页接管后才能卸力；急的话关电源")

    async def servo_watch():
        """每秒一拍：
        - 舵机页没人在看超过 SERVO_IDLE_S：没有舵机保持着（当场读，读不到的当在保持）就自动交还（页面关了、断网时主程序
          别一直停着、手柄一直不能用）。有保持着的：不交还、不卸力（用户定过「没人操作不自动卸力」；交还会让 robotd 起来时
          全部松开），只记一行 warn（同一句 1 分钟只记一次）；
        - 会话期间 5 秒看一眼 robotd 起没起来（审查 D9）；
        - 交还以后 / 没接管上的标记：主程序连上（交还之后有新回包）才清掉提示（审查 D12）。"""
        since = None
        last_probe = last_try = 0.0
        while True:
            await asyncio.sleep(1.0)
            if servo is None:
                continue
            now = time.monotonic()
            if servo.state != "open":
                since = None
                fresh = robotd.present is True and robotd.bus.at is not None
                if servo.handed_back_at is not None and fresh and robotd.bus.at > servo.handed_back_at and not robotd.paused:
                    servo.handed_back_at = None                    # 主程序连上了：「已交还……手动起 robotd」那句收起来
                    servo.handback_note = None
                    servo.giveback_error = servo.giveback_hint = None
                if servo.orphan and fresh and robotd.bus.at > servo.orphan["at"] and not robotd.paused:
                    servo.orphan = None                            # 主程序起来了（它会全部松开）：不用再去接管
                    await asyncio.to_thread(marker_drop)
                    log.info("上次退出时开着的舵机会话：主程序起来了（它会把舵机全部松开），不用再接管", cat="总线", dedup=False)
                continue
            probe = servo.probe_robotd if servo.probe_robotd is not None else not servo.sim
            if probe and now - last_probe >= ROBOTD_PROBE_S and not servo.closing:
                last_probe = now
                if await robotd_up():
                    await robotd_conflict()
                    continue
            here = sum(1 for c in clients.values() if c.get("route") == "/servos")
            if here:
                since = None
            elif since is None:
                since = now
            elif now - since > SERVO_IDLE_S and now - last_try >= 5.0:
                last_try = now                                     # 有保持着的时候 5 秒再看一次（当场读扭矩）
                async with servo_gate:
                    if servo.state != "open":
                        continue
                    done, kept = await leave_servos(f"舵机页 {SERVO_IDLE_S:.0f} 秒没人在看，也没有舵机保持着", by="Studio 自动交还")
                if done:
                    since = None
                else:
                    log.warn(f"舵机页没人在看，但{kept}：不自动交还、不卸力", cat="总线", raw=kept,
                             hint="要交还点页面顶上的「交还主程序」；交还以后舵机会全部松开，先扶住")

    async def pad_feed():
        """robotd 的回读 10 Hz 交给手柄页的后端，不管有没有页面在看（测急停、动作、卸力后 2 秒的保护都靠它）。"""
        while True:
            await asyncio.sleep(1 / STATE_HZ)
            try:
                # A server-owned music lease consumes real robot.state frames
                # even after the last browser viewer leaves the drive area.
                robotd.set_background_music(pad.motion_studio.background_music())
                pad.feed_robot(robot_view(robotd))
            except Exception as e:  # noqa: BLE001
                log.error("把主程序的状态交给手柄页时出错", cat="手柄", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 的问题，导出诊断包发出来")
                await asyncio.sleep(1.0)

    async def onboard_loop():
        """上手清单、主板温度的回差：0.5 秒判一次（只改内存），记录变了在线程里写盘（审查 O6：只在这一处判、只在这里写）。"""
        while True:
            await asyncio.sleep(ONBOARD_TICK_S)
            try:
                # 读文件（servo-calib 目录、robotd 不报温度时的 /sys/class/thermal）放线程里：SD 卡一卡不能挡住
                # 同一个事件循环里的卸力（代码审查 C9-1）。tick 本身只用内存
                await asyncio.to_thread(onboard.io, onboard.need_board(robotd))
                if pad is not None:
                    pad.battery_shutdown = onboard.battery_shutdown   # 手柄页的低电文案照 robotd 配置说（任务 1 附）
                if onboard.tick(robotd, pad, servo):
                    await asyncio.to_thread(onboard.save)
            except Exception as e:  # noqa: BLE001
                log.error("上手清单判的时候出错", cat="系统", raw=f"{type(e).__name__}: {e}", exc=True,
                          hint="这是 Studio 的问题，导出诊断包发出来；不影响站和走")
                await asyncio.sleep(5.0)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await updates.start_readiness(update_readiness)
        watch = asyncio.create_task(servo_watch())
        feed = asyncio.create_task(pad_feed()) if pad is not None else None
        ob_task = asyncio.create_task(onboard_loop()) if onboard is not None else None
        bat_task = asyncio.create_task(battery.loop()) if battery is not None else None   # 电量估算 1 Hz（审查 B-R7）
        tune_task = asyncio.create_task(tune.loop()) if tune is not None else None     # 调参卡读文件 2 秒一次（线程里）
        robotd.start()
        if net is not None:
            net.start()
        restore = asyncio.create_task(servo_restore())        # 上次退出时舵机会话开着：接着管（审查 D1）
        if phone_bridge is not None:
            # 后台先加载语音模型：第一句话不该自带 5~25 秒的冷启动
            asr_warm = asyncio.create_task(asyncio.to_thread(phone_bridge.warmup))
        log.info(f"Studio {VERSION} 启动 · Python {platform.python_version()} · {platform.node()}", cat="系统",
                 dedup=False)
        yield
        await updates.stop_readiness()
        restore.cancel()
        watch.cancel()
        if tune_task is not None:
            tune_task.cancel()
        if bat_task is not None:
            bat_task.cancel()
        if ob_task is not None:
            ob_task.cancel()
            await asyncio.to_thread(onboard.save)
        if feed is not None:
            feed.cancel()
            await asyncio.to_thread(pad.close)
        if servo is not None and servo.state != "closed":
            # Studio 退出不卸力（审查 D1）：只关串口，保持着的舵机照样保持；标记留着，下次起来接着管
            n = servo.holding()
            await asyncio.to_thread(servo.close)
            log.info(f"Studio 退出：舵机会话开着（{n} 颗保持着），不卸力；下次起来会接着管", cat="总线", dedup=False)
        if net is not None:
            await net.stop()
        await robotd.stop()
        auth.flush()
        log.info("Studio 停止", cat="系统", dedup=False)
        log.flush()

    # ---- 手机走 https 后必须补的两件事 ----
    # 摄像头：mediad 的信令是明文 ws，https 页面直连会被当混合内容挡掉。
    # 这里挂一个同域同协议的 /media-ws，由 Studio 转发到本机 mediad 的信令端口。
    async def media_ws_proxy(ws: WebSocket):
        if phone_bridge is None:
            await ws.close(code=1011)
            return
        if not origin_ok(ws):
            await ws.close(code=4403)
            return
        _, sess = session_of(ws)
        if not sess:
            await ws.accept()
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            port = getattr(sensors, "camera_signaling_port", None)
            upstream = phone_bridge.MEDIA_UPSTREAM if port is None else f"ws://127.0.0.1:{port}"
            await phone_bridge.pump_media(ws, upstream=upstream)
        except Exception as exc:
            log.info(f"摄像头信令转发结束（{_client_desc(ws)}）：{exc}", cat="连接")

    # 手机说话：浏览器语音识别在安卓上走谷歌服务，国内基本不通。把手机录的 PCM 传回来，
    # 用板上 duck-echo 同一套 Vosk 中文模型来认，效果跟对着鸭子说话一致。
    async def api_asr(request):
        no_store = {"Cache-Control": "no-store"}
        _, sess = session_of(request)
        if not sess:
            return JSONResponse({"ok": False, "error": "登录过期了，重新登录再试"}, 401, headers=no_store)
        if not origin_ok(request):
            return JSONResponse({"ok": False, "error": "请求校验失败，刷新页面再试"}, 403, headers=no_store)
        if phone_bridge is None:
            return JSONResponse({"ok": False, "error": "这块板子没有装语音识别模块"}, 503, headers=no_store)
        body = await request.body()
        if len(body) < 3200:          # 16 kHz / 16 bit：0.1 秒都不到，什么也认不出来
            return JSONResponse({"ok": False, "error": "这段太短了，再说一次"}, 400, headers=no_store)
        if len(body) > 12 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "这段录音太长了"}, 413, headers=no_store)
        # free=1：不用命令词表，走通用解码。自检页用它看「板子到底听到什么」，
        # 也用来区分「没听清」和「词不在词表里」。慢得多，只给排查用。
        free = (request.query_params.get("free") or "").lower() in ("1", "true", "yes")
        detail = await asyncio.get_running_loop().run_in_executor(
            None, phone_bridge.transcribe_detail, body, not free)
        if detail.get("error"):
            return JSONResponse({"ok": False, "error": f"识别没跑起来：{detail['error']}"},
                                503, headers=no_store)
        return JSONResponse({"ok": True, "data": {"text": detail.get("text") or "",
                                                    "info": detail.get("info") or {}}},
                            headers=no_store)

    # 手机装一次本地 CA 才会信任 https://10.42.0.1:8000 并放出麦克风。这个文件要在登录前就能下载。
    async def api_ca(request):
        no_store = {"Cache-Control": "no-store"}
        if not ca_cert:
            return Response("这块板子没有本地 CA", status_code=404, headers=no_store)
        try:
            body = await asyncio.to_thread(Path(ca_cert).read_bytes)
        except OSError:
            return Response("这块板子没有本地 CA", status_code=404, headers=no_store)
        return Response(body, media_type="application/x-x509-ca-cert",
                        headers={"Content-Disposition": 'attachment; filename="Microduck-Local-CA.crt"',
                                 **no_store})

    routes = [
        Route("/healthz", healthz),
        Route("/api/session", api_session),
        Route("/api/ai", api_ai, methods=["GET", "POST"]),
        Route('/api/updates',api_updates,methods=['GET','POST']),
        Route('/api/updates/upload',api_updates,methods=['POST']),
        Route("/api/login", api_login, methods=["POST"]),
        Route("/api/logout", api_logout, methods=["POST"]),
        Route("/api/password", api_password, methods=["POST"]),
        Route("/api/ssh", api_ssh, methods=["POST"]),
        Route("/api/logs", api_logs),
        Route("/api/diag", api_diag),
        Route("/api/profile", api_profile),
        Route("/api/camera", api_camera),
        Route("/api/tof", api_tof),
        Route("/api/models", api_models),
        Route("/api/models/op", api_model_op, methods=["POST"]),
        Route("/api/models/import", api_model_import, methods=["POST"]),
        Route("/model/meshes.bin", meshes),
        Route("/m", lambda request: RedirectResponse("/m.html", status_code=307)),
        # 手机走 https 之后才用得上：本地 CA 下载、摄像头信令同域转发、手机录音转文字
        Route("/ca.crt", api_ca),
        Route("/api/asr", api_asr, methods=["POST"]),
        WebSocketRoute("/media-ws", media_ws_proxy),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/", AppStatic(directory=str(web_dir), html=True)),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    return HostGuard(app, guard, log)


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"（没拿到：{type(e).__name__}: {e}）"


def _diag_zip(log, snap, raw):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("先看我.txt", "Microduck Studio 诊断包\n\n"
                   "里面有：最近两天的 Studio 日志、导出时的状态、robotd 原始回包、版本、robotd/padd 的系统日志。\n"
                   "注意：含鸭子的序列号和局域网 IP。发到公开的群之前，介意的话先删掉 system.txt 和 robotd-raw.json。\n")
        for p in log.files(days=2):
            z.write(p, f"logs/{p.name}")
        z.writestr("state.json", json.dumps(snap, ensure_ascii=False, indent=1, default=str))
        z.writestr("robotd-raw.json", json.dumps(raw, ensure_ascii=False, indent=1, default=str))
        info = {"studio": VERSION, "python": sys.version, "platform": platform.platform(),
                "log": log.snapshot(), "time": time.strftime("%F %T %z")}
        z.writestr("versions.json", json.dumps(info, ensure_ascii=False, indent=1))
        deps = Path(__file__).resolve().parents[1] / "deps" / "VERSIONS.txt"
        if deps.is_file():
            z.write(deps, "deps-VERSIONS.txt")
        if sys.platform.startswith("linux"):
            z.writestr("journal-robotd.txt", _run(["journalctl", "-u", "robotd", "-n", "300", "--no-pager", "-o", "short-iso"]))
            z.writestr("journal-padd.txt", _run(["journalctl", "-u", "padd", "-n", "100", "--no-pager", "-o", "short-iso"]))
            z.writestr("system.txt", "\n".join(_run(c) for c in (["uptime"], ["free", "-m"], ["df", "-h", "/"], ["ip", "-brief", "addr"])))
    return buf.getvalue()


def precompress(web_dir):
    """meshes.bin 先压一份 .gz（只在 .gz 比原文件旧时重压）。"""
    raw = Path(web_dir) / "model" / "meshes.bin"
    gz = raw.with_suffix(".bin.gz")
    try:
        if raw.is_file() and (not gz.is_file() or gz.stat().st_mtime < raw.stat().st_mtime):
            with open(raw, "rb") as f, gzip.open(gz, "wb", compresslevel=9) as g:
                g.write(f.read())
    except OSError:
        # 网页目录只读（装到 /opt、换专用用户以后）：不压了，直接给原文件
        return None
    return gz
