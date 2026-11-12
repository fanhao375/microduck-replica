"""配网（WiFi）：读 netcheck 的状态文件、调 configd 的 net.status / scan / connect / forget、跟小助手开关热点。

镜像里的 microduck-netcheck 开机连不上 WiFi 就开热点（NM 连接名 Microduck-Hotspot，地址 10.42.0.1），
并把现在的样子写进 /run/microduck/net.json。Studio 只读它：

    {"mode": "wifi" | "hotspot" | "none",
     "hotspot_ssid": "Microduck-c51b", "hotspot_ip": "10.42.0.1", "hotspot_psk": "microduck",
     "hostname": "duck-c51b", "since": 1790000000.0,
     "scan": [{"ssid": "Home", "signal": 72, "security": "wpa_psk"}], "scan_at": 1790000000.0}

文件不在（电脑上开发、老镜像）就当「不知道」：页面整块 WiFi 隐藏，别的照常。
热点模式下 wlan0 扫不了（AP 模式），configd 的 net.scan 空 / 出错就用文件里开热点前的 scan，并标 cached。

密码：家里的 WiFi 密码只在内存里从网页 → configd 传一次，从不写日志、state、last-try 或诊断包。
state 里的 hotspot.psk 是鸭子自己热点的公共密码（microduck），页面要显示给用户，不是家里的密码。
"""
import asyncio
import json
import os
import re
import socket
import time
from pathlib import Path

from . import helper
from .log import log_cmd
from .rpc import Conn, RpcError, Timeout, Unreachable, call_once

NET_STATE = "/run/microduck/net.json"
LAST_TRY_NAME = "net-last-try.json"
READ_EVERY_S = 2.0
STATUS_EVERY_S = 5.0
STATUS_TIMEOUT_S = 4.0
CONNECT_DELAY_S = 2.0          # 先让页面收到「开始切换」，再动热点
CONNECT_TIMEOUT_S = 60.0
SCAN_TIMEOUT_S = 15.0
FORGET_TIMEOUT_S = 10.0
STATUS_MAX_AGE_S = 15.0        # 5 秒一次，三次都失败才当作不知道
PORT = 8000
HOTSPOT_IP = "10.42.0.1"       # NetworkManager 共享热点的默认地址（net.json 没写 hotspot_ip 时用）
OWN_PREFIX = "Microduck-"      # 鸭子热点的名字都是 Microduck-xxxx（netcheck 起的）
PSK_HEX = re.compile(r"^[0-9A-Fa-f]{64}$")
PERMISSION = "Studio 没权限改 WiFi（configd 没放行 radxa）"

REASON_TEXT = {
    "bad_key": "密码不对",
    "not_found": "鸭子找不到这个 WiFi（名字对吗？离路由器近一点；隐藏的 WiFi 现在不支持）",
    "timeout": "连上了但拿不到地址，可以再试一次",
    "studio_timeout": "鸭子的配网程序 60 秒没回话",
    "unsupported": "这个 WiFi 鸭子连不了（企业认证或没填密码）",
    "other": "没连上",
}


class Refused(Exception):
    """配网操作没法做 / 参数不对：message 是给页面看的人话。"""


def validate_ssid(value):
    """SSID 1–32 字节（UTF-8；中文一个字 3 个字节）。不 strip：SSID 前后的空格可能是名字的一部分。"""
    if not isinstance(value, str) or value == "":
        raise Refused("WiFi 名字要是 1–32 个字节")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as e:
        raise Refused(f"WiFi 名字里有不能用的字符：{e}") from None
    if size > 32:
        raise Refused("WiFi 名字最长 32 个字节（中文一个字算 3 个）")
    if "\x00" in value:
        raise Refused("WiFi 名字里不能有 NUL")
    return value


def validate_psk(value):
    """空 = 开放网络；否则 8–63 个字符，或者 64 位十六进制（WPA 的两种写法）。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise Refused("WiFi 密码要是文字")
    if PSK_HEX.fullmatch(value):
        return value
    if 8 <= len(value) <= 63:
        return value
    raise Refused("WiFi 密码要 8–63 个字符，或者 64 位十六进制")


def validate_connect(ssid, psk):
    return validate_ssid(ssid), validate_psk(psk)


def addresses(host, ip, port=PORT, scheme="http"):
    """「这只鸭子的地址」：现在能用的几个，按设备说该用哪个（10-20 真机：开着 Clash 系统代理的 Windows 打不开 .local——
    走了代理、502；不带点的主机名 Windows 用 LLMNR 问得到，代理按 <local> 绕过；IP 哪儿都能开）。
    HTTPS 的出厂证书只覆盖主机名、固定热点地址和回环地址。"""
    out = []
    if ip and (scheme != "https" or ip in (HOTSPOT_IP, "127.0.0.1")):
        out.append({"kind": "ip", "url": f"{scheme}://{ip}:{port}/", "note": "IP：哪台设备都能打开（换了路由器、重新分了地址会变）"})
    out.append({"kind": "host", "url": f"{scheme}://{host}:{port}/", "note": "Windows 电脑用这个：开着代理也能打开"})
    out.append({"kind": "local", "url": f"{scheme}://{host}.local:{port}/", "note": "苹果手机、Mac 用这个"})
    return out


def own_hotspot(n, hotspot_ssid):
    """扫描结果里这一条是不是鸭子自己的热点（真机扫出来 Microduck-0de6、显示「已保存」，点了会让人往里填家里密码）。
    net.json 有 hotspot_ssid 就按它认；没有就认「Microduck- 开头、而且是已保存」的。"""
    ssid = n.get("ssid") if isinstance(n, dict) else None
    if not isinstance(ssid, str):
        return False
    if hotspot_ssid:
        return ssid == hotspot_ssid
    return ssid.startswith(OWN_PREFIX) and bool(n.get("saved"))


def explain(reason, detail=None):
    """configd 的失败原因 → 页面上的一句话。other 附上 detail；没权限的错误原样说出来。"""
    if detail and str(detail).strip() == PERMISSION:
        return PERMISSION
    base = REASON_TEXT.get(reason, REASON_TEXT["other"])
    if reason == "other" and detail:
        return f"{base}（{detail}）"
    return base


def _scrub(text, psk=None):
    """写日志 / last-try 之前再兜一层：家里的密码和它在 URL 里出现的样子都换成 [已隐藏]。"""
    if text is None:
        return None
    out = str(text)
    if psk:
        out = out.replace(psk, "[已隐藏]")
    return out[:600]


def _rpc_error(e, what):
    if isinstance(e, RpcError):
        if e.code == 14 or re.search(r"permission|not authori[sz]ed|not allowed|access denied", e.message or "", re.I):
            return PERMISSION
        return f"{what}：{e.message}"
    if isinstance(e, Timeout):
        return f"{what}超时了（{e}）"
    return f"连不上 configd：{e}"


class Net:
    """配网状态 + 操作。start() 起两个循环：2 秒读 net.json、5 秒问 configd net.status（只在文件在时）。"""

    def __init__(self, configd_addr, state_file=NET_STATE, state_dir=None, log=None, *, dev_mode=False,
                 hostname=None, port=PORT, scheme="http"):
        self.configd_addr = configd_addr
        self.state_file = Path(state_file)
        self.hostname = hostname
        self.port = int(port)
        self.scheme = "https" if scheme == "https" else "http"
        self.log = log
        self.dev_mode = bool(dev_mode)
        self.last_try_path = Path(state_dir) / LAST_TRY_NAME if state_dir else None
        self.cfg = Conn(configd_addr, "configd-net", log) if configd_addr and configd_addr != "none" else None
        self.info = None
        self.info_at = None
        self.status = None
        self.status_at = None
        self.busy = None
        self.last_try = self._load_last_try()
        self._bad_state_logged = False
        self._status_error_logged = False
        self._tasks = []
        self.connect_task = None       # app.py 放后台连 WiFi 的那一路（页面断开也不能被回收）

    # ---- 生命周期 ----

    def start(self):
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._read_loop(), name="net-state"),
                       loop.create_task(self._status_loop(), name="net-status")]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=1.0)
        self._tasks = []
        if self.cfg is not None:
            self.cfg.close()

    async def _read_loop(self):
        while True:
            try:
                await asyncio.to_thread(self._read_once)
            except Exception as e:  # noqa: BLE001 —— 读状态文件出错不能让整个 Studio 停
                if self.log:
                    self.log.warn("读配网状态文件时出错", cat="网络", step=str(self.state_file),
                                  raw=f"{type(e).__name__}: {e}", dedup=True)
            await asyncio.sleep(READ_EVERY_S)

    def _read_once(self):
        try:
            raw = self.state_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            had = self.info is not None
            self.info, self.info_at = None, None
            self.status, self.status_at = None, None
            self._bad_state_logged = False
            if had and self.log:
                self.log.info("配网状态文件不在了：WiFi 那一块先隐藏，别的照常", cat="网络")
            return
        except OSError as e:
            self._state_bad(f"读不了 {self.state_file}：{type(e).__name__}: {e}")
            return
        try:
            data = json.loads(raw)
        except ValueError as e:
            self._state_bad(f"不是 JSON：{e}")
            return
        if not isinstance(data, dict) or data.get("mode") not in ("wifi", "hotspot", "none"):
            self._state_bad("不是对象，或者 mode 不是 wifi / hotspot / none")
            return
        was_bad = self._bad_state_logged
        self.info, self.info_at = data, time.monotonic()
        self._bad_state_logged = False
        if was_bad and self.log:
            self.log.info("配网状态文件又读到了", cat="网络")

    def _state_bad(self, why):
        self.info, self.info_at = None, None
        self.status, self.status_at = None, None
        if self._bad_state_logged or self.log is None:
            return
        self._bad_state_logged = True
        self.log.warn("配网状态文件读不懂：WiFi 那一块先隐藏", cat="网络", step=str(self.state_file),
                      raw=why, hint="这是 netcheck 写的；它下次会重写。别的功能照常，不用管")

    async def _status_loop(self):
        while True:
            if self.cfg is not None and self.info is not None:
                try:
                    r = await self.cfg.call("net.status", {}, timeout=STATUS_TIMEOUT_S)
                    if not isinstance(r, dict):
                        raise RpcError("net.status", None, f"回的不是对象（{type(r).__name__}）")
                    self.status, self.status_at = r, time.monotonic()
                    if self._status_error_logged and self.log:
                        self.log.info("WiFi 状态恢复了", cat="网络")
                    self._status_error_logged = False
                except (Unreachable, Timeout, RpcError) as e:
                    self.status, self.status_at = None, None
                    if not self._status_error_logged and self.log:
                        self._status_error_logged = True
                        self.log.warn("读不到 WiFi 状态", cat="网络", step="configd net.status", raw=str(e),
                                      hint="configd 没在跑？不影响站和走，只是 WiFi 那一块显示不知道")
            else:
                self.status, self.status_at = None, None
                self._status_error_logged = False
            await asyncio.sleep(STATUS_EVERY_S)

    # ---- 给页面 ----

    def mode(self):
        return self.info.get("mode") if isinstance(self.info, dict) else None

    def view(self, now=None):
        """推给页面的 net 字段。文件不在 / 坏了 = {"known": false}。"""
        if not isinstance(self.info, dict):
            return {"known": False}
        now = time.monotonic() if now is None else now
        st = self.status if (isinstance(self.status, dict) and self.status_at is not None
                             and now - self.status_at <= STATUS_MAX_AGE_S) else None
        connected = bool(st and st.get("state") == "connected")
        host = self.info.get("hostname") or self.hostname or socket.gethostname()
        last = None
        if isinstance(self.last_try, dict):
            last = {k: self.last_try.get(k) for k in ("ssid", "at", "ok", "reason", "detail", "ip4")}
            last["reason_text"] = explain(last.get("reason"), last.get("detail"))
        mode = self.info.get("mode")
        ip4 = st.get("ip4") if connected else None
        # 现在能用的 IP：热点模式就是鸭子热点的地址（10.42.0.1），否则是 configd net.status 的 ip4（没连上就没有）
        ip_now = (self.info.get("hotspot_ip") or HOTSPOT_IP) if mode == "hotspot" else ip4
        hotspot_ip = self.info.get("hotspot_ip") or HOTSPOT_IP
        return {
            "known": True,
            "mode": mode,
            "ssid": st.get("ssid") if connected else None,
            "signal": st.get("signal") if connected else None,
            "ip4": ip4,
            "ip_now": ip_now,
            "hostname": host,
            "url_local": f"{self.scheme}://{host}.local:{self.port}/",
            "url_host": f"{self.scheme}://{host}:{self.port}/",
            "url_hotspot": f"{self.scheme}://{hotspot_ip}:{self.port}/m",
            "urls": addresses(host, ip_now, self.port, self.scheme),
            "hotspot": {"ssid": self.info.get("hotspot_ssid"), "ip": hotspot_ip,
                        "psk": self.info.get("hotspot_psk")},
            "last_try": last,
            "busy": self.busy,
        }

    def begin_connect(self):
        """占住「正在连」：第二个 connect 回 False。不知道配网状态时抛 Refused。"""
        if not isinstance(self.info, dict):
            raise Refused("不知道鸭子的配网状态：WiFi 功能现在用不了")
        if self.busy is not None:
            return False
        self.busy = "connecting"
        return True

    def begin_hotspot(self):
        """占住热点开关：已有操作回 False，不知道配网状态时抛 Refused。
        跟 begin_connect 分开，页面上「正在连 WiFi」和「正在开关热点」不会互相插队。"""
        if not isinstance(self.info, dict):
            raise Refused("不知道鸭子的配网状态：WiFi 功能现在用不了")
        if self.busy is not None:
            return False
        self.busy = "hotspot"
        return True

    # ---- 扫描 ----

    async def scan(self):
        """configd net.scan。热点模式下网卡可能扫不了：空 / 出错就用 net.json 里开热点前的 scan。
        鸭子自己的热点滤掉（own_hotspot）：不然它在列表里显示「已保存」，点了会让人往里填家里的密码。"""
        if not isinstance(self.info, dict):
            raise Refused("不知道鸭子的配网状态：WiFi 功能现在用不了")
        own = self.info.get("hotspot_ssid")
        result, error = None, None
        with log_cmd(self.log, "扫 WiFi", "configd net.scan") as t:
            try:
                r = await call_once(self.configd_addr, "net.scan", {}, timeout=SCAN_TIMEOUT_S)
                nets = r.get("networks") if isinstance(r, dict) else None
                if not isinstance(nets, list):
                    raise Refused("configd 回的 WiFi 列表看不懂")
                result = [n for n in nets if isinstance(n, dict) and n.get("ssid") and not own_hotspot(n, own)]
                t.ok({"networks": len(result)})
            except (Unreachable, Timeout, RpcError, Refused) as e:
                error = _rpc_error(e, "扫 WiFi") if not isinstance(e, Refused) else str(e)
                result = None
                t.fail(error, hint="热点模式下用开热点前的那次扫描")
        if self.mode() == "hotspot" and not result:
            cached = self.info.get("scan")
            if isinstance(cached, list):
                if self.log:
                    self.log.info("热点模式下用开热点前的扫描结果", cat="网络", raw=error or "configd 回的列表是空的",
                                  dedup=True)
                return {"networks": [n for n in cached if isinstance(n, dict) and not own_hotspot(n, own)], "cached": True,
                        "scan_at": self.info.get("scan_at")}
        if result is None:
            raise Refused(error or "读不到 WiFi 列表")
        return {"networks": result, "cached": False, "scan_at": None}

    # ---- 连接 ----

    async def connect(self, ssid, psk, by):
        """关热点 → configd net.connect → 写 last-try。调用前先 begin_connect()。
        回 last-try 那份记录（不含密码）。失败时马上把热点开回来；bad_key 还要先忘掉这次写坏的配置。"""
        ssid, psk = validate_connect(ssid, psk)
        if self.busy is None:
            self.busy = "connecting"
        if psk and self.log:
            self.log.hide(psk)                    # 兜底：万一别的代码把密码带进日志，也打码
        outcome = {"ok": False, "reason": "other", "detail": None, "ip4": None}
        hotspot_stopped = False
        hotspot_down_tried = False
        try:
            await asyncio.sleep(CONNECT_DELAY_S)
            if self.mode() == "hotspot":
                if self.dev_mode:
                    if self.log:
                        self.log.info("电脑上开发（--dev-autologin）：不关热点，直接连 WiFi", cat="网络",
                                      raw=f"by={by}", dedup=False)
                else:
                    if not helper.available(refresh=True):
                        if self.log:
                            self.log.warn("连 WiFi 前发现小助手不在", cat="网络", step="wifi-hotspot-down",
                                          hint="先在鸭子上运行 deploy/install.sh")
                        raise Refused("还没装小助手：先在鸭子上运行 deploy/install.sh，Studio 才能关热点去连 WiFi")
                    hotspot_down_tried = True
                    with log_cmd(self.log, "关热点", "小助手 wifi-hotspot-down", who=by) as th:
                        try:
                            ok_h, result_h, detail_h = await asyncio.to_thread(helper.wifi_hotspot_down, by, ssid)
                        except Exception as e:  # noqa: BLE001
                            th.fail(f"{type(e).__name__}: {e}", hint="看小助手装没装、版本对不对")
                            raise Refused(f"小助手没跑成：{type(e).__name__}: {e}") from None
                        if result_h == "bad_verb":
                            th.fail("小助手太旧，不认 wifi-hotspot-down", hint="重装 deploy/install.sh")
                            raise Refused("小助手太旧，重装 deploy/install.sh")
                        if not ok_h:
                            why = helper.friendly(result_h, detail_h, "wifi-hotspot-down")
                            th.fail(why, raw=str(detail_h), hint="看 nmcli 是不是能看到 Microduck-Hotspot")
                            raise Refused(f"关热点没成：{why}")
                        th.ok({"result": result_h})
                    hotspot_stopped = True
            params = {"ssid": ssid}
            if psk:
                params["psk"] = psk
            with log_cmd(self.log, f"连 WiFi「{ssid}」", "configd net.connect", who=by,
                         args={"ssid": ssid, "密码": "带密码" if psk else "没密码"}) as t:
                try:
                    r = await call_once(self.configd_addr, "net.connect", params, timeout=CONNECT_TIMEOUT_S)
                except (Unreachable, Timeout, RpcError) as e:
                    if isinstance(e, Timeout):
                        outcome = {"ok": False, "reason": "studio_timeout",
                                   "detail": f"鸭子的配网程序 {CONNECT_TIMEOUT_S:.0f} 秒没回话", "ip4": None}
                    else:
                        outcome = {"ok": False, "reason": "other", "detail": _scrub(_rpc_error(e, "连 WiFi"), psk),
                                   "ip4": None}
                    t.fail(explain(outcome["reason"], outcome["detail"]), raw=_scrub(str(e), psk))
                else:
                    outcome = self._read_connect(r, psk)
                    if outcome["ok"]:
                        note = f"IP {outcome['ip4']}" if outcome.get("ip4") else ""
                        t.ok({"outcome": "connected", "ssid": r.get("ssid"), "ip4": r.get("ip4")}, note=note)
                    else:
                        t.fail(explain(outcome["reason"], outcome["detail"]),
                               raw={"outcome": r.get("outcome"), "reason": r.get("reason")} if isinstance(r, dict) else None,
                               hint="热点马上就回来，手机重新连鸭子热点就能看到原因")
        except Refused as e:
            outcome = {"ok": False, "reason": "other", "detail": _scrub(str(e), psk), "ip4": None}
            if self.log:
                self.log.warn("连 WiFi 没开始", cat="网络", step="关热点", raw=_scrub(str(e), psk),
                              hint="看小助手装没装、版本对不对")
        except Exception as e:  # noqa: BLE001 —— Studio 自己的 bug 也要写 last-try
            outcome = {"ok": False, "reason": "other", "detail": _scrub(f"{type(e).__name__}: {e}", psk), "ip4": None}
            if self.log:
                self.log.error("连 WiFi 时 Studio 出错", cat="网络", raw=_scrub(f"{type(e).__name__}: {e}", psk),
                               exc=True, hint="这是 Studio 的问题，导出诊断包发出来")
        finally:
            rec = {"ssid": ssid, "at": time.time(), "ok": bool(outcome["ok"]),
                   "reason": outcome["reason"], "detail": _scrub(outcome["detail"], psk),
                   "ip4": outcome.get("ip4")}
            self.last_try = rec
            self._save_last_try(rec)
            if outcome["ok"]:
                self._set_connected(ssid, outcome.get("ip4"))
            elif hotspot_down_tried and not outcome["ok"]:
                if hotspot_stopped and outcome["reason"] == "bad_key":
                    await self._forget_bad_key(ssid, by)
                await self._hotspot_up(by)
            self.busy = None
        return rec

    def _read_connect(self, r, psk):
        if not isinstance(r, dict):
            return {"ok": False, "reason": "other", "detail": "configd 回的不是对象", "ip4": None}
        if r.get("outcome") == "connected":
            return {"ok": True, "reason": None, "detail": None, "ip4": r.get("ip4")}
        if r.get("outcome") == "failed":
            reason = r.get("reason") if r.get("reason") in REASON_TEXT else "other"
            detail = _scrub(r.get("detail"), psk) if r.get("detail") else None
            return {"ok": False, "reason": reason, "detail": detail, "ip4": None}
        return {"ok": False, "reason": "other", "detail": _scrub(f"看不懂的回包：{r}", psk), "ip4": None}

    async def _hotspot_up(self, by):
        """开热点；返回 (ok, 给页面的一句话)。连接失败回补热点和「户外模式」按钮共用。"""
        with log_cmd(self.log, "开热点", "小助手 wifi-hotspot-up", who=by) as t:
            try:
                ok, result, detail = await asyncio.to_thread(helper.wifi_hotspot_up, by)
            except Exception as e:  # noqa: BLE001
                why = f"小助手没跑成：{type(e).__name__}: {e}"
                t.fail(why, hint="连接窗口结束后 netcheck 也会自己开")
                return False, why
            if result == "bad_verb":
                why = "小助手太旧，不认 wifi-hotspot-up，重装 deploy/install.sh"
                t.fail(why, hint="连接窗口结束后 netcheck 也会自己开")
                return False, why
            if ok:
                t.ok({"result": result})
                return True, None
            why = helper.friendly(result, detail, "wifi-hotspot-up")
            t.fail(why, raw=str(detail), hint="连接窗口结束后 netcheck 也会自己开")
            return False, why

    async def hotspot_up(self, by):
        """户外模式：马上开热点，不等 netcheck。调用前先 begin_hotspot()。返回 (ok, reason)。"""
        try:
            if not helper.available(refresh=True):
                raise Refused("还没装小助手：先在鸭子上运行 deploy/install.sh，Studio 才能开热点")
            return await self._hotspot_up(by)
        finally:
            self.busy = None

    async def hotspot_down(self, by, ssid=None):
        """户外模式：关热点，NetworkManager 会按已保存的配置连回家里的 WiFi。
        调用前先 begin_hotspot()。ssid 只写给 netcheck 的连接窗口，可为空。返回 (ok, reason)。"""
        try:
            if not helper.available(refresh=True):
                raise Refused("还没装小助手：先在鸭子上运行 deploy/install.sh，Studio 才能关热点")
            with log_cmd(self.log, "关热点", "小助手 wifi-hotspot-down", who=by) as t:
                try:
                    ok, result, detail = await asyncio.to_thread(helper.wifi_hotspot_down, by, ssid)
                except Exception as e:  # noqa: BLE001
                    why = f"小助手没跑成：{type(e).__name__}: {e}"
                    t.fail(why, hint="看小助手装没装、版本对不对")
                    return False, why
                if result == "bad_verb":
                    why = "小助手太旧，不认 wifi-hotspot-down，重装 deploy/install.sh"
                    t.fail(why, hint="重装 deploy/install.sh")
                    return False, why
                if ok:
                    t.ok({"result": result})
                    return True, None
                why = helper.friendly(result, detail, "wifi-hotspot-down")
                t.fail(why, raw=str(detail), hint="看 nmcli 是不是能看到 Microduck-Hotspot")
                return False, why
        finally:
            self.busy = None

    async def _forget_bad_key(self, ssid, by):
        """密码错了：官方 connect 已经用这次的错密码替换了同名配置，先忘掉，免得 netcheck 以后还去试。"""
        with log_cmd(self.log, f"忘掉密码不对的 WiFi「{ssid}」", "configd net.forget", who=by) as t:
            try:
                r = await call_once(self.configd_addr, "net.forget", {"ssid": ssid}, timeout=FORGET_TIMEOUT_S)
            except (Unreachable, Timeout, RpcError) as e:
                t.fail(_rpc_error(e, "忘掉 WiFi"), raw=str(e), hint="热点照样马上开回来；这个配置可能还留着")
                return
            if not isinstance(r, dict):
                t.fail("configd 回的不是对象", raw=repr(r)[:200])
                return
            t.ok({"removed": bool(r.get("removed"))})

    def _set_connected(self, ssid, ip4):
        self.status = {"state": "connected", "ssid": ssid, "ip4": ip4}
        self.status_at = time.monotonic()

    # ---- 忘掉 ----

    async def forget(self, ssid):
        ssid = validate_ssid(ssid)
        if not isinstance(self.info, dict):
            raise Refused("不知道鸭子的配网状态：WiFi 功能现在用不了")
        with log_cmd(self.log, f"忘掉 WiFi「{ssid}」", "configd net.forget") as t:
            try:
                r = await call_once(self.configd_addr, "net.forget", {"ssid": ssid}, timeout=FORGET_TIMEOUT_S)
            except (Unreachable, Timeout, RpcError) as e:
                text = _rpc_error(e, "忘掉 WiFi")
                t.fail(text, raw=str(e))
                raise Refused(text) from None
            if not isinstance(r, dict):
                t.fail("configd 回的不是对象", raw=repr(r)[:200])
                raise Refused("configd 回的不是对象")
            removed = bool(r.get("removed"))
            t.ok({"removed": removed})
        if isinstance(self.status, dict) and self.status.get("ssid") == ssid:
            self.status, self.status_at = None, None
        return {"removed": removed}

    # ---- last-try 文件 ----

    def _load_last_try(self):
        if self.last_try_path is None:
            return None
        try:
            data = json.loads(self.last_try_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return {k: data.get(k) for k in ("ssid", "at", "ok", "reason", "detail", "ip4")}

    def _save_last_try(self, rec):
        if self.last_try_path is None:
            return
        try:
            self.last_try_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.last_try_path.with_name(self.last_try_path.name + ".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o644)
            except OSError:
                pass
            os.replace(tmp, self.last_try_path)
        except OSError as e:
            if self.log:
                self.log.warn("上次连 WiFi 的结果没写进去", cat="网络", step=str(self.last_try_path),
                              raw=f"{type(e).__name__}: {e}",
                              hint="页面重新连上时看不到上次失败的原因；看看 state 目录满没满、有没有权限")
