"""真的 uvicorn + wsproto 集成测试（跟板子上同一套实现，不用 TestClient）。
覆盖 review-impl-plan S0 测试第 4、5、7 条：Host 421、Origin 矩阵、登录、没登录的 WebSocket、推送频率。
"""
import asyncio
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps"), str(HERE.parent / "dev")]

import uvicorn  # noqa: E402
from wsproto import ConnectionType, WSConnection  # noqa: E402
from wsproto.events import (AcceptConnection, CloseConnection, RejectConnection, Request,  # noqa: E402
                            TextMessage, Ping, Pong)

from fake_robotd import FakeRobotd  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.auth import COOKIE, Auth  # noqa: E402
from studio.imu import ImuBackend  # noqa: E402
from studio.pad import default_pad  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.onboard import Onboarding  # noqa: E402
from studio.robotd import Robotd  # noqa: E402
from studio import helper  # noqa: E402
from studio.ws_protocol import MusicWebSocketProtocol  # noqa: E402
from studio.servo import ServoSession  # noqa: E402

FIXTURE = HERE / "fixtures" / "now-20261018.jsonl"
WEB = HERE.parent / "web"
PW = "duck-secret-123"


def http_call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    c.close()
    return out


class WsClient:
    """最小的 WebSocket 客户端：握手结果 + 按条收 JSON。"""

    def __init__(self, port, origin=None, cookie=None, host=None):
        self.port, self.origin, self.cookie, self.host = port, origin, cookie, host
        self.ws = WSConnection(ConnectionType.CLIENT)
        self.msgs, self.buf = [], ""
        self.accepted = self.rejected = self.close_code = None
        self.pong_mode, self.pings = 'valid', []

    async def open(self, target="/ws"):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", self.port)
        hdrs = []
        if self.origin:
            hdrs.append(("Origin", self.origin))
        if self.cookie:
            hdrs.append(("Cookie", f"{COOKIE}={self.cookie}"))
        self.w.write(self.ws.send(Request(host=self.host or f"127.0.0.1:{self.port}", target=target, extra_headers=hdrs)))
        await self.w.drain()
        await self.pump(until=lambda: self.accepted or self.rejected or self.close_code is not None, timeout=3)

    async def pump(self, until, timeout=3.0):
        end = time.monotonic() + timeout
        while not until() and time.monotonic() < end:
            try:
                data = await asyncio.wait_for(self.r.read(65536), max(0.01, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if not data:
                break
            self.ws.receive_data(data)
            for ev in self.ws.events():
                if isinstance(ev, AcceptConnection):
                    self.accepted = True
                elif isinstance(ev, RejectConnection):
                    self.rejected = ev.status_code
                elif isinstance(ev, CloseConnection):
                    self.close_code = ev.code
                elif isinstance(ev, Ping):
                    self.pings.append(ev.payload)
                    if self.pong_mode is not None:
                        payload = ev.payload if self.pong_mode == 'valid' else b'unmatched'
                        self.w.write(self.ws.send(Pong(payload=payload)))
                        await self.w.drain()
                elif isinstance(ev, TextMessage):
                    self.buf += ev.data
                    if ev.message_finished:
                        self.msgs.append(json.loads(self.buf))
                        self.buf = ""

    async def send(self, obj):
        self.w.write(self.ws.send(TextMessage(json.dumps(obj))))
        await self.w.drain()

    def close(self):
        self.w.close()


class HttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        log = Log(Path(self.tmp.name) / "logs", echo=False, background=False)
        self.log = log
        self.auth = Auth(Path(self.tmp.name) / "state", log)
        self.auth.set_password(PW)
        self.fake = FakeRobotd(FIXTURE)
        self.s1, p1 = await self.fake.serve_tcp()
        self.s2, p2 = await self.fake.serve_tcp()
        self.rd = Robotd(f"tcp:127.0.0.1:{p1}", f"tcp:127.0.0.1:{p2}", log)
        self.servo = ServoSession(log, sim=True)
        self.pad = self.make_pad(log, p1, p2)
        self.onboard = Onboarding(str(Path(self.tmp.name) / "state"), log)
        self.ca_path = Path(self.tmp.name) / "ca.crt"
        self.ca_path.write_bytes(b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n")
        app = build_app(auth=self.auth, robotd=self.rd, log=log, web_dir=WEB, duck={"label": "测试鸭"},
                        names=["microduck-2"], servo=self.servo, imu=ImuBackend(self.rd, log, self.tmp.name),
                        pad=self.pad, onboard=self.onboard, ssh_home=str(Path(self.tmp.name) / "home"),
                        ca_cert=str(self.ca_path))
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0, ws=MusicWebSocketProtocol, http="h11", lifespan="on",
                             log_level="critical")
        self.server = uvicorn.Server(cfg)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def make_pad(self, log, robotd_port, configd_port):
        return None                     # 台架模式那样：没有手柄页后端（PadHttpTest 里换成真的）

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.task
        self.s1.close()
        self.s2.close()
        self.tmp.cleanup()

    async def call(self, *a, **kw):
        return await asyncio.to_thread(http_call, self.port, *a, **kw)

    async def login(self):
        st, h, _ = await self.call("POST", "/api/login", {"password": PW}, {"Origin": self.origin})
        self.assertEqual(st, 200)
        return h["set-cookie"].split(";")[0].split("=", 1)[1], h["set-cookie"]

    async def test_no_password_means_open(self):
        """用户 10-18：不用设密码，直接连。没设密码时不登录也能拿到会话、连上 WebSocket。"""
        import os
        os.remove(self.auth.pw_path)
        st, _, body = await self.call("GET", "/api/session")
        self.assertEqual(st, 200)
        c = WsClient(self.port, origin=self.origin)
        await c.open()
        self.assertTrue(c.accepted)
        c.close()
        c2 = WsClient(self.port, origin="http://evil.example.com")      # 别的网站照样进不来
        await c2.open()
        self.assertFalse(c2.accepted)
        c2.close()

    async def test_bad_host_gets_421(self):
        st, _, _ = await self.call("GET", "/", headers={"Host": "evil.example.com"})
        self.assertEqual(st, 421)
        st, _, _ = await self.call("GET", "/healthz", headers={"Host": f"microduck-2:{self.port}"})
        self.assertEqual(st, 200)

    async def test_probe_svg_for_finding_the_duck(self):
        """任务 17：连 WiFi 以后页面用 new Image() 加载 http://duck-xxxx:8000/probe.svg 找鸭子的新地址：
        设了密码也不用登录；duck-xxxx、duck-xxxx.local 这两种 Host 都给；是 SVG 图片；不缓存（每次都真的问到鸭子）。"""
        import socket
        hn = socket.gethostname().lower()
        for host in (f"{hn}:{self.port}", f"{hn}.local:{self.port}", f"127.0.0.1:{self.port}"):
            st, h, body = await self.call("GET", "/probe.svg?t=1790000000000", headers={"Host": host})
            self.assertEqual(st, 200, host)
            self.assertTrue(h["content-type"].startswith("image/svg+xml"), h)
            self.assertEqual(h["cache-control"], "no-cache")
            self.assertIn(b"<svg", body)
            self.assertLess(len(body), 1024)                     # 很小：每 2 秒探一次也不费事
        st, _, _ = await self.call("GET", "/probe.svg", headers={"Host": "evil.example.com"})
        self.assertEqual(st, 421)                                 # Host 校验照旧

    async def test_ca_cert_is_downloadable_before_login(self):
        """手机装证书要在登录之前完成：不登录也能下载，是 CA 证书 + 附件下载，且不缓存。"""
        body_want = self.ca_path.read_bytes()
        st, h, body = await self.call("GET", "/ca.crt")
        self.assertEqual(st, 200)
        self.assertEqual(h["content-type"], "application/x-x509-ca-cert")
        self.assertIn("attachment", h["content-disposition"])
        self.assertIn("Microduck-Local-CA.crt", h["content-disposition"])
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(body, body_want)
        st, _, _ = await self.call("GET", "/ca.crt", headers={"Host": "evil.example.com"})
        self.assertEqual(st, 421)                                # Host 校验照旧

    async def test_ca_cert_missing_is_404(self):
        import os
        os.remove(self.ca_path)
        st, _, _ = await self.call("GET", "/ca.crt")
        self.assertEqual(st, 404)

    async def test_login_origin_matrix_and_cookie(self):
        bad = [None, "http://evil.example.com", f"http://127.0.0.1:{self.port + 1}", "null"]
        for o in bad:
            st, _, _ = await self.call("POST", "/api/login", {"password": PW}, {"Origin": o} if o else {})
            self.assertEqual(st, 403, o)
        st, _, body = await self.call("POST", "/api/login", {"password": "wrong"}, {"Origin": self.origin})
        self.assertEqual(st, 401)
        token, raw = await self.login()
        self.assertIn("HttpOnly", raw)
        self.assertIn("samesite=lax", raw.lower())
        st, _, body = await self.call("GET", "/api/session", headers={"Cookie": f"{COOKIE}={token}"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["csrf"])

    async def test_login_backoff_after_five_failures(self):
        for _ in range(5):
            await self.call("POST", "/api/login", {"password": "wrong"}, {"Origin": self.origin})
        st, _, body = await self.call("POST", "/api/login", {"password": PW}, {"Origin": self.origin})
        self.assertEqual(st, 401)
        self.assertGreater(json.loads(body)["wait"], 0)          # 退避期间对的密码也不收

    async def test_logout_needs_csrf(self):
        token, _ = await self.login()
        ck = {"Cookie": f"{COOKIE}={token}", "Origin": self.origin}
        await self.call("POST", "/api/logout", {}, ck)                       # 没带 X-CSRF：不注销
        self.assertIsNotNone(self.auth.session(token))
        st, _, body = await self.call("GET", "/api/session", headers=ck)
        csrf = json.loads(body)["csrf"]
        await self.call("POST", "/api/logout", {}, dict(ck, **{"X-CSRF": csrf}))
        self.assertIsNone(self.auth.session(token))

    async def test_password_change_needs_login(self):
        st, _, _ = await self.call("POST", "/api/password",
                                   {"action": "set", "password": "new-secret", "confirm": "new-secret"},
                                   {"Origin": self.origin})
        self.assertEqual(st, 401)
        self.assertTrue(self.auth.check_password(PW))

    async def test_password_change_keeps_this_browser_logged_in(self):
        token, _ = await self.login()
        ck = {"Cookie": f"{COOKIE}={token}", "Origin": self.origin}
        _, _, body = await self.call("GET", "/api/session", headers=ck)
        csrf = json.loads(body)["csrf"]
        st, _, _ = await self.call("POST", "/api/password",
                                   {"action": "set", "password": "new-secret", "confirm": "new-secret"},
                                   dict(ck, **{"X-CSRF": csrf}))
        self.assertEqual(st, 200)
        st, _, _ = await self.call("GET", "/api/session", headers=ck)
        self.assertEqual(st, 200)
        self.assertTrue(self.auth.check_password("new-secret"))

    async def test_password_must_be_six_chars_and_typed_twice(self):
        token, _ = await self.login()
        ck = {"Cookie": f"{COOKIE}={token}", "Origin": self.origin}
        _, _, body = await self.call("GET", "/api/session", headers=ck)
        csrf = json.loads(body)["csrf"]
        for payload in ({"action": "set", "password": "12345", "confirm": "12345"},
                        {"action": "set", "password": "new-secret", "confirm": "new-secret2"}):
            st, _, _ = await self.call("POST", "/api/password", payload,
                                       dict(ck, **{"X-CSRF": csrf}))
            self.assertEqual(st, 400)
        self.assertTrue(self.auth.check_password(PW))

    async def test_first_password_from_the_web_logs_this_browser_in(self):
        os.remove(self.auth.pw_path)
        st, _, body = await self.call("GET", "/api/session")
        self.assertEqual(st, 200)
        csrf = json.loads(body)["csrf"]
        st, h, body = await self.call("POST", "/api/password",
                                      {"action": "set", "password": "new-secret", "confirm": "new-secret"},
                                      {"Origin": self.origin, "X-CSRF": csrf})
        self.assertEqual(st, 200, body)
        token = h["set-cookie"].split(";")[0].split("=", 1)[1]
        st, _, _ = await self.call("GET", "/api/session", headers={"Cookie": f"{COOKIE}={token}"})
        self.assertEqual(st, 200)
        self.assertTrue(self.auth.check_password("new-secret"))

    async def test_password_can_be_cleared_from_the_web(self):
        token, _ = await self.login()
        ck = {"Cookie": f"{COOKIE}={token}", "Origin": self.origin}
        _, _, body = await self.call("GET", "/api/session", headers=ck)
        csrf = json.loads(body)["csrf"]
        st, _, _ = await self.call("POST", "/api/password", {"action": "clear"},
                                   dict(ck, **{"X-CSRF": csrf}))
        self.assertEqual(st, 200)
        self.assertFalse(self.auth.has_password())
        self.assertFalse(self.auth.pw_path.exists())

    # ---- 「更多」页的 SSH 开关（用户 10-21：出了问题自己 SSH 修，不用重烧卡；密码用户自己定） ----

    async def ssh_post(self, payload):
        token, _ = await self.login()
        ck = {"Cookie": f"{COOKIE}={token}", "Origin": self.origin}
        _, _, body = await self.call("GET", "/api/session", headers=ck)
        csrf = json.loads(body)["csrf"]
        st, _, body = await self.call("POST", "/api/ssh", payload, dict(ck, **{"X-CSRF": csrf}))
        return st, json.loads(body or "{}")

    def ssh_patches(self, pw_result=("ssh_password_on", True)):
        result, on = pw_result
        view = {"password_login": on, "ssh_active": True, "keys": 0, "user": "radxa", "exit": 0, "result": "ssh_status"}
        return (unittest.mock.patch("studio.helper.available", return_value=True),
                unittest.mock.patch("studio.helper.ssh_password",
                                    return_value=(True, result, {"exit": 0, "result": result, "password_login": on})),
                unittest.mock.patch("studio.helper.ssh_status", return_value=(True, "ssh_status", view)))

    async def test_ssh_needs_login(self):
        st, _, _ = await self.call("POST", "/api/ssh", {"action": "status"}, {"Origin": self.origin})
        self.assertEqual(st, 401)

    async def test_ssh_on_needs_web_password_first(self):
        os.remove(self.auth.pw_path)
        _, _, body = await self.call("GET", "/api/session")
        csrf = json.loads(body)["csrf"]
        a, b, c = self.ssh_patches()
        with a, b as sp, c:
            st, _, body = await self.call("POST", "/api/ssh",
                                          {"action": "on", "password": "duck-secret-42", "confirm": "duck-secret-42"},
                                          {"Origin": self.origin, "X-CSRF": csrf})
        self.assertEqual(st, 403)
        self.assertIn("先在上面设网页登录密码", json.loads(body)["error"])
        sp.assert_not_called()

    async def test_ssh_on_goes_to_helper_and_password_is_never_logged(self):
        pw = "duck-secret-42"
        a, b, c = self.ssh_patches()
        with a, b as sp, c:
            st, data = await self.ssh_post({"action": "on", "password": pw, "confirm": pw})
        self.assertEqual(st, 200, data)
        self.assertTrue(data["ssh"]["password_login"])
        self.assertEqual(sp.call_args.args[1:], (True, pw))
        recent = json.dumps(self.log.recent(limit=500), ensure_ascii=False)
        self.assertIn("SSH 密码登录打开了", recent)
        self.assertNotIn(pw, recent)
        for f in (Path(self.tmp.name) / "logs").rglob("*"):
            if f.is_file():
                self.assertNotIn(pw, f.read_text(encoding="utf-8", errors="replace"), f)

    async def test_ssh_password_rules_checked_before_the_helper(self):
        a, b, c = self.ssh_patches()
        with a, b as sp, c:
            for payload in ({"action": "on", "password": "short77", "confirm": "short77"},
                            {"action": "on", "password": "duck-secret-42", "confirm": "duck-secret-43"},
                            {"action": "nope"}):
                st, _ = await self.ssh_post(payload)
                self.assertEqual(st, 400, payload)
        sp.assert_not_called()

    async def test_ssh_off_and_status(self):
        a, b, c = self.ssh_patches(("ssh_password_off", False))
        with a, b as sp, c:
            st, data = await self.ssh_post({"action": "off"})
            self.assertEqual(st, 200, data)
            self.assertEqual(sp.call_args.args[1], False)
            st, data = await self.ssh_post({"action": "status"})
        self.assertEqual(st, 200, data)
        self.assertEqual(data["ssh"], {"password_login": False, "ssh_active": True, "keys": 0, "key_list": [],
                                       "user": "radxa"})

    async def test_ssh_keys_add_list_remove(self):
        from tests.test_sshkeys import make_key
        line, fp = make_key(comment="laptop")
        a, b, c = self.ssh_patches(("ssh_password_off", False))
        with a, b, c:
            st, data = await self.ssh_post({"action": "add_key", "key": line})
            self.assertEqual(st, 200, data)
            self.assertTrue(data["added"])
            self.assertEqual([(k["fp"], k["comment"]) for k in data["ssh"]["key_list"]], [(fp, "laptop")])
            self.assertEqual(data["ssh"]["keys"], 1)
            st, data = await self.ssh_post({"action": "add_key", "key": line})
            self.assertEqual((st, data["added"]), (200, False))
            st, data = await self.ssh_post({"action": "add_key", "key": "-----BEGIN OPENSSH PRIVATE KEY-----"})
            self.assertEqual(st, 400)
            self.assertIn("私钥", data["error"])
            st, data = await self.ssh_post({"action": "remove_key", "fp": fp})
            self.assertEqual((st, data["removed"], data["ssh"]["key_list"]), (200, 1, []))
        text = (Path(self.tmp.name) / "home" / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
        self.assertEqual(text, "")
        self.assertIn("SSH 钥匙登记了：ssh-ed25519 laptop", json.dumps(self.log.recent(limit=200), ensure_ascii=False))

    async def test_ssh_add_key_needs_web_password_first(self):
        from tests.test_sshkeys import make_key
        line, _ = make_key()
        os.remove(self.auth.pw_path)
        _, _, body = await self.call("GET", "/api/session")
        csrf = json.loads(body)["csrf"]
        st, _, body = await self.call("POST", "/api/ssh", {"action": "add_key", "key": line},
                                      {"Origin": self.origin, "X-CSRF": csrf})
        self.assertEqual(st, 403)
        self.assertIn("再登记钥匙", json.loads(body)["error"])
        self.assertFalse((Path(self.tmp.name) / "home" / ".ssh" / "authorized_keys").exists())

    async def test_ssh_status_lists_keys_even_without_the_helper(self):
        from tests.test_sshkeys import make_key
        line, fp = make_key(comment="pc")
        home = Path(self.tmp.name) / "home" / ".ssh"
        home.mkdir(parents=True)
        (home / "authorized_keys").write_text(line + "\n", encoding="utf-8")
        with unittest.mock.patch("studio.helper.available", return_value=False):
            st, data = await self.ssh_post({"action": "status"})
        self.assertEqual(st, 200, data)
        self.assertIsNone(data["ssh"]["password_login"])            # 小助手不在：密码登录开没开读不到
        self.assertEqual([k["fp"] for k in data["ssh"]["key_list"]], [fp])

    async def test_ws_without_login_tells_page_to_login(self):
        c = WsClient(self.port, origin=self.origin)
        await c.open()
        # 页面要靠 4401 才知道该跳登录页；握手直接 403 的话浏览器只看到 1006，会一直重连
        self.assertEqual(c.close_code, 4401, f"accepted={c.accepted} rejected={c.rejected}")
        c.close()

    async def test_ws_wrong_origin_is_rejected(self):
        token, _ = await self.login()
        c = WsClient(self.port, origin="http://evil.example.com", cookie=token)
        await c.open()
        self.assertFalse(c.accepted)
        c.close()

    async def test_ws_push_rate_follows_visibility(self):
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        self.assertTrue(c.accepted)
        await c.pump(until=lambda: False, timeout=1.0)
        states = [m for m in c.msgs if m["t"] == "state"]
        self.assertEqual(c.msgs[0]["t"], "hello")
        self.assertGreaterEqual(len(states), 7)                  # 可见：约 10 Hz
        self.assertEqual(states[-1]["posture"], "holding")
        await c.send({"t": "hb", "seq": 1, "visible": False})
        await c.pump(until=lambda: False, timeout=0.3)
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=2.2)
        kinds = [m["t"] for m in c.msgs if m["t"] != "log"]
        self.assertNotIn("state", kinds)                         # 后台：只剩心跳
        self.assertTrue(1 <= kinds.count("hb") <= 3, kinds)
        c.close()

    async def test_ws_relax_round_trip(self):
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "cmd", "id": "c1", "name": "relax"})
        await c.pump(until=lambda: any(m.get("t") == "state" and m["estop"]["state"] == "confirmed" for m in c.msgs),
                     timeout=3.0)
        results = [m for m in c.msgs if m["t"] == "result"]
        self.assertEqual(results and results[0]["ok"], True)
        self.assertEqual([m for m in c.msgs if m["t"] == "state"][-1]["estop"]["state"], "confirmed")
        c.close()

    async def test_servo_session_via_ws(self):
        """舵机页：打开总线（模拟舵机）→ 收到 15 颗 → 卸力走总线、不找主程序 → 交还后恢复轮询。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "cmd", "id": "o1", "name": "servo.open"})
        await c.pump(until=lambda: (
            any(m.get("t") == "result" and m.get("id") == "o1" for m in c.msgs)
            and any(m.get("t") == "servo" and m.get("state") == "open" and m.get("online") == 15
                    for m in c.msgs)), timeout=3.0)
        res = [m for m in c.msgs if m.get("t") == "result" and m.get("id") == "o1"]
        self.assertTrue(res and res[0]["ok"], res)
        self.assertTrue(self.rd.paused)
        states = [m for m in c.msgs if m.get("t") == "state"]
        self.assertEqual(states[-1]["posture"], "servo_session")
        self.assertTrue(states[-1]["robotd_ok"])
        c.msgs.clear()
        await c.send({"t": "cmd", "id": "r1", "name": "relax"})
        await c.pump(until=lambda: any(m.get("t") == "state" and m["estop"]["state"] == "confirmed" for m in c.msgs),
                     timeout=3.0)
        self.assertTrue(any(m.get("t") == "result" and m.get("id") == "r1" and m["ok"] for m in c.msgs))
        self.assertEqual(len([m for _, m in self.fake.calls if m == "robot.relax"]), 0)   # 没去找主程序
        await c.send({"t": "cmd", "id": "x1", "name": "servo.close"})
        await c.pump(until=lambda: any(m.get("t") == "result" and m.get("id") == "x1" for m in c.msgs), timeout=3.0)
        self.assertEqual(self.servo.state, "closed")
        self.assertFalse(self.rd.paused)
        c.close()

    async def test_servo_session_accepts_open_snapshot_before_command_result(self):
        """The independent snapshot loop may observe open before open's worker returns."""
        release=threading.Event()
        original_open=self.servo.open
        original_client=WsClient
        loop=asyncio.get_running_loop()
        observed=[]
        clients=[]

        def opening(*args,**kwargs):
            result=original_open(*args,**kwargs)
            if result[0] and not release.wait(3):
                raise AssertionError('client did not observe the independent open snapshot')
            return result

        class Messages(list):
            def append(messages,message):
                super().append(message)
                if (not observed and message.get('t')=='servo' and message.get('state')=='open'
                        and message.get('online')==15):
                    observed.append(not any(m.get('t')=='result' and m.get('id')=='o1' for m in messages))
                    # Release on the next loop turn, after pump evaluates its
                    # completion predicate against this snapshot without an ack.
                    loop.call_soon(release.set)

        class Client(original_client):
            def __init__(client,*args,**kwargs):
                super().__init__(*args,**kwargs)
                client.msgs=Messages()
                clients.append(client)

        try:
            with unittest.mock.patch.object(self.servo,'open',side_effect=opening), \
                 unittest.mock.patch(__name__+'.WsClient',Client):
                await self.test_servo_session_via_ws()
            self.assertEqual(observed,[True],'test must exercise snapshot-before-result ordering')
        finally:
            release.set()
            for client in clients:
                if hasattr(client,'w'):client.close()

    async def test_imu_view_and_ops_via_ws(self):
        """IMU 页：收到 {t:'imu'}（芯片、安装方向取自主程序）；solve 回结果；写入这一步先拒、说清楚原因。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.pump(until=lambda: any(m.get("t") == "imu" and m.get("mount") for m in c.msgs), timeout=4.0)
        v = [m for m in c.msgs if m.get("t") == "imu" and m.get("mount")][-1]
        self.assertEqual(v["source"], "lsm6dsv16x")
        self.assertTrue(v["mount"]["verified"])
        await c.send({"t": "cmd", "id": "s1", "name": "imu.op", "args": {"op": "solve", "samples": [{"g": [0, 0, -1]}]}})
        await c.send({"t": "cmd", "id": "w1", "name": "imu.op", "args": {"op": "save"}})
        await c.pump(until=lambda: {m.get("id") for m in c.msgs if m.get("t") == "result"} >= {"s1", "w1"}, timeout=3.0)
        res = {m["id"]: m for m in c.msgs if m.get("t") == "result"}
        self.assertTrue(res["s1"]["ok"])                       # 指令本身成了；读数不够，结果里 ok=False
        self.assertFalse(res["s1"]["data"]["ok"])
        self.assertFalse(res["w1"]["ok"])
        self.assertIn("写进主程序", res["w1"]["reason"])
        c.close()

    async def test_servo_refused_while_walking(self):
        """鸭子在走 / 策略在跑：点进舵机页不接管（关节会冻在半步），页面只读。"""
        self.fake.set_posture("policy")
        await asyncio.sleep(1.2)
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "cmd", "id": "o1", "name": "servo.open"})
        await c.pump(until=lambda: any(m.get("t") == "result" for m in c.msgs), timeout=3.0)
        res = [m for m in c.msgs if m.get("t") == "result"][0]
        self.assertFalse(res["ok"])
        self.assertIn("先停下", res["reason"])
        self.assertEqual(self.servo.state, "closed")
        c.close()

    async def test_servo_auto_hand_back_when_nobody_on_page(self):
        """舵机页没人在看（页面关了、断网）超过 SERVO_IDLE_S：自动交还主程序。"""
        from studio import app as app_mod
        token, _ = await self.login()
        with unittest.mock.patch.object(app_mod, "SERVO_IDLE_S", 0.5):
            c = WsClient(self.port, origin=self.origin, cookie=token)
            await c.open()
            await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/servos"})
            await c.send({"t": "cmd", "id": "o1", "name": "servo.open"})
            await c.pump(until=lambda: any(m.get("t") == "result" for m in c.msgs), timeout=3.0)
            self.assertEqual(self.servo.state, "open")
            c.close()                                              # 页面关了
            end = time.monotonic() + 15
            while self.servo.state == "open" and time.monotonic() < end:
                await asyncio.sleep(0.2)
        self.assertEqual(self.servo.state, "closed")
        self.assertFalse(self.rd.paused)

    async def test_page_gone_while_relax_in_flight(self):
        """代码审查 #6：按下卸力后页面马上断了，卸力照样发完、确认。"""
        self.fake.delays["robot.relax"] = 0.5
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "cmd", "id": "c1", "name": "relax"})
        await asyncio.sleep(0.1)
        c.close()
        end = time.monotonic() + 3
        while self.rd.estop.outcome != "confirmed" and time.monotonic() < end:
            await asyncio.sleep(0.05)
        self.assertEqual(self.rd.estop.outcome, "confirmed")
        self.assertEqual(len([m for _, m in self.fake.calls if m == "robot.relax"]), 1)

    async def test_servo_table_only_on_servo_page(self):
        """总览重排审查 O12：舵机表一帧 5.6 KB，只有舵机页用——别的页面不推。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/"})
        await c.pump(until=lambda: False, timeout=1.0)
        self.assertNotIn("servo", [m["t"] for m in c.msgs])
        self.assertIn("state", [m["t"] for m in c.msgs])
        await c.send({"t": "hb", "seq": 2, "visible": True, "route": "/servos"})
        await c.pump(until=lambda: any(m["t"] == "servo" for m in c.msgs), timeout=2.0)
        self.assertIn("servo", [m["t"] for m in c.msgs])
        c.close()

    async def test_state_carries_onboarding_and_board_temperature(self):
        """总览上手清单（后端 0.5 秒判一次、state 里只带缓存）、主板温度格；「已经校过，核对一下」走 onboard.op。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.pump(until=lambda: any(m.get("t") == "state" and m.get("onboarding") for m in c.msgs), timeout=4.0)
        st = [m for m in c.msgs if m.get("t") == "state" and m.get("onboarding")][-1]
        self.assertEqual([r["key"] for r in st["onboarding"]["rows"]], ["servo", "imu", "commit", "pad", "estop", "walk"])
        self.assertEqual(st["cpu"]["c"], 76.9)
        await c.pump(until=lambda: self.rd.calib_fresh() is not None, timeout=3.0)
        await c.send({"t": "cmd", "id": "k1", "name": "onboard.op", "args": {"op": "confirm_servo"}})
        await c.send({"t": "cmd", "id": "k2", "name": "onboard.op", "args": {"op": "rm"}})
        await c.pump(until=lambda: {m.get("id") for m in c.msgs if m.get("t") == "result"} >= {"k1", "k2"}, timeout=4.0)
        res = {m["id"]: m for m in c.msgs if m.get("t") == "result"}
        self.assertTrue(res["k1"]["ok"], res["k1"])
        self.assertFalse(res["k2"]["ok"])
        self.assertTrue((Path(self.tmp.name) / "state" / "onboarding.json").exists())
        c.msgs.clear()
        await c.pump(until=lambda: any(m.get("t") == "state" and m.get("onboarding") for m in c.msgs), timeout=2.5)
        st = [m for m in c.msgs if m.get("t") == "state" and m.get("onboarding")][-1]   # 没变的帧不带清单（C5）
        self.assertEqual(next(r for r in st["onboarding"]["rows"] if r["key"] == "servo")["state"], "ok")
        c.close()

    async def test_onboard_skip_and_unskip_via_ws(self):
        """上手清单的「已完成」「跳过」「取消」走 onboard.op；IMU 方向主程序明说没确认时要点两次：
        没带 confirmed 后端拒（不能只靠页面的两步确认），带了就记（用户 10-20：每一步都能跳）。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.pump(until=lambda: self.rd.calib_fresh() is not None, timeout=3.0)

        async def op(cid, args):
            await c.send({"t": "cmd", "id": cid, "name": "onboard.op", "args": args})
            await c.pump(until=lambda: any(m.get("id") == cid for m in c.msgs), timeout=4.0)
            return next(m for m in c.msgs if m.get("id") == cid)

        async def imu_state():
            c.msgs.clear()
            await c.pump(until=lambda: any(m.get("t") == "state" and m.get("onboarding") for m in c.msgs), timeout=2.5)
            st = [m for m in c.msgs if m.get("t") == "state" and m.get("onboarding")][-1]
            return next(r for r in st["onboarding"]["rows"] if r["key"] == "imu")["state"]
        self.assertTrue((await op("s1", {"op": "skip", "step": "imu"}))["ok"])
        self.assertEqual(await imu_state(), "skip")
        self.assertTrue((await op("s2", {"op": "unskip", "step": "imu"}))["ok"])
        self.assertEqual(await imu_state(), "todo")
        self.assertTrue((await op("d1", {"op": "done", "step": "imu"}))["ok"])
        self.assertEqual(await imu_state(), "ok")
        self.assertTrue((await op("d2", {"op": "undone", "step": "imu"}))["ok"])
        self.assertEqual(await imu_state(), "todo")
        self.fake.bus["native"]["imu_mount_verified"] = False
        await asyncio.sleep(1.3)                                   # 等 busStatus 轮询到
        r = await op("s3", {"op": "skip", "step": "imu"})
        self.assertFalse(r["ok"])
        self.assertIn("要点两次确认：确定跳过？主程序没确认 IMU 方向", r["reason"])
        r = await op("s3b", {"op": "skip", "step": "imu", "confirmed": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(await imu_state(), "skip")
        self.assertFalse((await op("s4", {"op": "skip", "step": "bogus"}))["ok"])
        c.close()

    async def test_onboarding_only_when_changed(self):
        """代码审查 C5：上手清单没变时状态帧不带它（onboarding_same），每秒还是带一份整的。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/"})
        await c.pump(until=lambda: any(m.get("t") == "state" and m.get("onboarding") for m in c.msgs), timeout=4.0)
        await c.pump(until=lambda: False, timeout=1.0)       # 等清单稳定下来（刚连上时会记「站起来过」）
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=2.1)
        sts = [m for m in c.msgs if m.get("t") == "state"]
        full = [m for m in sts if "onboarding" in m]
        same = [m for m in sts if m.get("onboarding_same")]
        self.assertGreaterEqual(len(sts), 15)
        self.assertTrue(1 <= len(full) <= 5, (len(full), len(sts)))
        self.assertEqual(len(full) + len(same), len(sts))
        self.assertTrue(all("onboarding" not in m for m in same))
        c.close()

    async def test_ws_without_heartbeat_counts_as_not_looking(self):
        """代码审查 #11：手机走出 WiFi，连接半开、没心跳：不能一直当成有人在看。"""
        from studio import app as app_mod
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        with unittest.mock.patch.object(app_mod, "HB_QUIET_S", 0.4):
            await c.open()
            await c.pump(until=lambda: False, timeout=1.2)           # 一直不发心跳
            c.msgs.clear()
            await c.pump(until=lambda: False, timeout=1.5)
        self.assertNotIn("state", [m["t"] for m in c.msgs])
        self.assertEqual(self.rd.viewers, 0)
        c.close()

    async def test_ordinary_javascript_silent_connection_still_times_out(self):
        from studio import app as app_mod
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        with unittest.mock.patch.object(app_mod, "HB_DEAD_S", .4):
            await c.open()
            await c.pump(until=lambda: c.close_code is not None, timeout=2.)
        self.assertEqual(c.close_code, 4408)
        self.assertEqual(c.pings, [])
        c.close()


class PadHttpTest(HttpTest):
    """开着手柄页后端，把上面的网页测试整套再跑一遍（卸力、舵机页接管交还、IMU、心跳……不能被手柄页弄坏），
    再加手柄页自己的来回。假 robotd 同时当 configd；电脑上没有 bluetoothctl、也没有 padd 旁路。"""

    def make_pad(self, log, robotd_port, configd_port):
        return default_pad(log, configd_addr=f"tcp:127.0.0.1:{configd_port}", robotd_addr=f"tcp:127.0.0.1:{robotd_port}",
                           tap_addr="tcp:127.0.0.1:9", state_dir=self.tmp.name)

    async def test_mapping_readiness_uses_current_servo_state_before_robot_poll(self):
        from studio.pad_mapping import MappingError
        self.servo.state='opening'
        try:
            self.assertTrue(callable(getattr(self.pad,'mapping_guard',None)))
            with self.assertRaises(MappingError):self.pad.mapping_guard()
        finally:self.servo.state='closed'
        self.pad.mapping_guard()

    async def test_mapping_begin_and_servo_open_same_packet_are_mutually_exclusive(self):
        from tests.test_pad_mapping import Native
        class SlowStatus(Native):
            def call(inner,request):
                if request['op']=='status':time.sleep(.15)
                return super().call(request)
        self.pad.mapping.client=SlowStatus()
        self.fake.policies['enabled']=True
        self.fake.set_posture('holding')
        token,_=await self.login()
        c=WsClient(self.port,origin=self.origin,cookie=token);await c.open()
        await c.send({'t':'hb','visible':True,'route':'/pad','pad_visible':True})
        await c.pump(until=lambda:((self.pad._robot or {}).get('bus') or {}).get('policy_enabled') is False
                     and ((self.pad._robot or {}).get('policies') or {}).get('enabled') is True,timeout=4)
        c.w.write(b''.join(c.ws.send(TextMessage(json.dumps(message))) for message in (
            {'t':'cmd','id':'map','name':'pad.op','args':{'op':'mapping','action':'begin'}},
            {'t':'cmd','id':'servo','name':'servo.open','args':{}})))
        await c.w.drain()
        await c.pump(until=lambda:sum(m.get('id') in ('map','servo') for m in c.msgs)>=2,timeout=5)
        result={m['id']:m for m in c.msgs if m.get('id') in ('map','servo')}
        self.assertEqual(set(result),{'map','servo'})
        self.assertEqual(sum(result[k]['ok'] for k in ('map','servo')),1,result)
        if result['map']['ok']:self.assertEqual(self.servo.state,'closed')
        c.close()

    async def music_client(self):
        self.fake.set_posture("policy")
        class Audio:
            def snapshot(self):
                return {"available": True, "listening": True, "music": True,
                        "rhythm": {"bpm": 120., "confidence": .9, "feel": "lively",
                                   "last_beat": time.monotonic(), "age": .01}}
            def close(self):
                pass
        self.pad.audio.close()
        self.pad.audio = Audio()
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "hb", "visible": True, "route": "/pad", "pad_visible": True})
        await c.send({"t": "cmd", "id": "open", "name": "pad.op", "args": {"op": "drive", "open": True}})
        await c.pump(until=lambda: any(m.get("motion_studio", {}).get("idle_available", {}).get("ok")
                                     for m in c.msgs) and self.rd.state.at is not None, timeout=4.)
        await c.send({"t": "cmd", "id": "music", "name": "pad.op", "args": {"op": "motion", "action": "idle_start", "kind": "music"}})
        await c.pump(until=lambda: any(m.get("id") == "music" for m in c.msgs), timeout=3.)
        result = next(m for m in c.msgs if m.get("id") == "music")
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(self.pad.motion_studio.background_music())
        await c.send({"t": "hb", "visible": False, "route": "/logs", "pad_visible": False})
        await c.send({"t": "cmd", "id": "leave", "name": "pad.op", "args": {"op": "drive", "on": False}})
        await c.pump(until=lambda: any(m.get("id") == "leave" for m in c.msgs))
        return c, result["data"]["session_id"]

    async def test_javascript_silent_music_uses_native_pongs_without_fake_viewers(self):
        from studio import app as app_mod
        c, token = await self.music_client()
        before = self.rd.state.at
        self.rd._last_viewer_at = time.monotonic() - 11.
        with unittest.mock.patch.object(app_mod, "HB_DEAD_S", .4):
            await c.pump(until=lambda: False, timeout=2.4)
        self.assertIsNone(c.close_code)
        self.assertGreaterEqual(len(c.pings), 2)
        self.assertEqual(self.rd.viewers, 0)
        self.assertGreater(self.rd.state.at, before)
        self.assertEqual(self.pad.motion_studio.session["id"], token)
        c.close()
        end = time.monotonic() + 1.
        while self.pad.motion_studio.session is not None and time.monotonic() < end:
            await asyncio.sleep(.02)
        self.assertIsNone(self.pad.motion_studio.session)

    async def test_missing_or_unmatched_native_pong_closes_and_stops_music(self):
        from studio import app as app_mod
        for mode in (None, "invalid"):
            with self.subTest(mode=mode):
                c, _ = await self.music_client()
                c.pong_mode = mode
                with unittest.mock.patch.object(app_mod, "HB_DEAD_S", .4), \
                        unittest.mock.patch.object(app_mod, "MUSIC_PONG_TIMEOUT_S", .15):
                    await c.pump(until=lambda: c.close_code is not None, timeout=2.5)
                self.assertTrue(c.pings)
                self.assertEqual(c.close_code, 4408)
                await asyncio.sleep(.1)
                self.assertIsNone(self.pad.motion_studio.session)
                c.close()

    async def test_pad_page_via_ws(self):
        """手柄页开着时：收到 {t:'pad'}；顶上卸力照常落到主程序（只发一次）；页面断开后方向盘是空的。
        遥控、站走坐、组合动作的逻辑在 tests/test_pad.py 里测。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/pad"})
        await c.send({"t": "cmd", "id": "w1", "name": "pad.op", "args": {"op": "watch"}})
        await c.pump(until=lambda: sum(1 for m in c.msgs if m.get("t") == "pad") >= 3, timeout=4.0)
        self.assertTrue(any(m.get("t") == "result" and m.get("id") == "w1" and m["ok"] for m in c.msgs))
        snap = [m for m in c.msgs if m.get("t") == "pad"][-1]
        self.assertIn("virtual", snap)
        c.msgs.clear()
        await c.send({"t": "cmd", "id": "r1", "name": "relax"})
        await c.pump(until=lambda: any(m.get("t") == "result" and m.get("id") == "r1" for m in c.msgs), timeout=4.0)
        self.assertTrue([m for m in c.msgs if m.get("id") == "r1"][0]["ok"])
        self.assertEqual(len([m for _, m in self.fake.calls if m == "robot.relax"]), 1)
        c.close()
        t0 = time.monotonic()
        while self.pad.snapshot()["virtual"].get("driver") is not None and time.monotonic() - t0 < 3:
            await asyncio.sleep(0.05)
        self.assertIsNone(self.pad.snapshot()["virtual"].get("driver"))

    def pads(self, c):
        return [m for m in c.msgs if m.get("t") == "pad"]

    # ---- 用户 10-18：「舵机这个页面出去的时候应该保持住使能，不应该泄力，不应该失能」 ----

    async def servo_open_holding(self, c):
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/servos"})
        await c.send({"t": "cmd", "id": "o1", "name": "servo.open"})
        await c.pump(until=lambda: self.servo.state == "open", timeout=5.0)
        await c.send({"t": "cmd", "id": "t1", "name": "servo.op", "args": {"op": "torque", "ids": [20, 21], "on": 1}})
        end = time.monotonic() + 5
        while self.servo.holding() < 2 and time.monotonic() < end:
            await c.pump(until=lambda: False, timeout=0.2)
        self.assertGreaterEqual(self.servo.holding(), 2)

    async def test_leaving_servo_page_keeps_session_while_holding(self):
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await self.servo_open_holding(c)
        relax0 = len([m for _, m in self.fake.calls if m == "robot.relax"])
        # 离开舵机页（页面卸载时发 servo.close {if_idle}）：有舵机保持着，不交还、不卸力
        await c.send({"t": "hb", "seq": 2, "visible": True, "route": "/"})
        await c.send({"t": "cmd", "id": "x1", "name": "servo.close", "args": {"if_idle": True}})
        await c.pump(until=lambda: any(m.get("id") == "x1" for m in c.msgs), timeout=5.0)
        self.assertTrue(next(m for m in c.msgs if m.get("id") == "x1")["ok"])
        self.assertEqual(self.servo.state, "open")
        self.assertGreaterEqual(self.servo.holding(), 2)
        self.assertTrue(self.rd.paused)
        c.msgs.clear()
        await c.pump(until=lambda: any(m.get("t") == "state" for m in c.msgs), timeout=2.0)
        st = [m for m in c.msgs if m.get("t") == "state"][-1]
        self.assertGreaterEqual(st["servo_session"]["holding"], 2)            # 别的页上的提示条要用
        self.assertEqual(len([m for _, m in self.fake.calls if m == "robot.relax"]), relax0)
        # 全部松开以后再离开：照旧交还
        await c.send({"t": "cmd", "id": "t2", "name": "servo.op", "args": {"op": "torque", "on": 0}})
        end = time.monotonic() + 5
        while self.servo.holding() > 0 and time.monotonic() < end:
            await c.pump(until=lambda: False, timeout=0.2)
        await c.send({"t": "cmd", "id": "x2", "name": "servo.close", "args": {"if_idle": True}})
        await c.pump(until=lambda: any(m.get("id") == "x2" for m in c.msgs), timeout=8.0)   # 交还做完（关总线、恢复轮询）才回
        self.assertEqual(self.servo.state, "closed")
        self.assertFalse(self.rd.paused)
        c.msgs.clear()
        await c.pump(until=lambda: any(m.get("t") == "state" for m in c.msgs), timeout=2.0)
        st = [m for m in c.msgs if m.get("t") == "state"][-1]
        self.assertIsNone(st["servo_session"])
        self.assertIsNotNone(st["servo_handback_s"])                          # 「已交还……要手动起 robotd」那一句用
        c.close()

    async def test_nobody_watching_but_holding_does_not_hand_back(self):
        """60 秒没人看舵机页：有舵机保持着就不交还、不卸力，只记 warn；全部松开以后照旧自动交还。"""
        from studio import app as app_mod
        token, _ = await self.login()
        with unittest.mock.patch.object(app_mod, "SERVO_IDLE_S", 0.5):
            c = WsClient(self.port, origin=self.origin, cookie=token)
            await c.open()
            await self.servo_open_holding(c)
            c.close()                                                          # 页面关了
            await asyncio.sleep(12.0)                                         # servo_watch 5 秒一轮
            self.assertEqual(self.servo.state, "open")
            self.assertGreaterEqual(self.servo.holding(), 2)
            warns = [e for e in self.rd.log.recent(200) if "不自动交还、不卸力" in e["msg"]]
            self.assertEqual(len(warns), 1)                                    # 1 分钟只记一次
            await asyncio.to_thread(self.servo.ops.handle, {"op": "torque", "on": 0})
            end = time.monotonic() + 15
            while self.servo.state == "open" and time.monotonic() < end:
                await asyncio.sleep(0.2)
        self.assertEqual(self.servo.state, "closed")
        self.assertFalse(self.rd.paused)

    async def test_pad_push_rate_follows_pad_visible_not_route(self):
        """总览重排审查 O1：手柄那一块在屏幕上（心跳 pad_visible）才 10 Hz；地址是 #/pad 但没在屏幕上 → 1 Hz。
        老页面的心跳没带 pad_visible：按 route == /pad 兜底。"""
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": "/pad", "pad_visible": False})
        await c.pump(until=lambda: False, timeout=0.3)
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=2.05)
        self.assertLessEqual(len(self.pads(c)), 3)
        await c.send({"t": "hb", "seq": 2, "visible": True, "route": "/", "pad_visible": True})
        await c.pump(until=lambda: False, timeout=0.3)
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=1.0)
        snaps = self.pads(c)
        self.assertGreaterEqual(len(snaps), 7)
        # 长列表没变时不每帧推（审查 O12）：大多数帧没带 actions，带了 lists_same；每秒至少一份整的
        slim = [m for m in snaps if m.get("lists_same")]
        self.assertGreaterEqual(len(slim), len(snaps) - 2)
        self.assertTrue(all("actions" not in m["motion"] for m in slim))
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=1.2)
        self.assertTrue(any("actions" in m["motion"] for m in self.pads(c)))
        c.close()
        c2 = WsClient(self.port, origin=self.origin, cookie=token)               # 老页面：心跳里没有 pad_visible
        await c2.open()
        await c2.send({"t": "hb", "seq": 1, "visible": True, "route": "/pad"})
        await c2.pump(until=lambda: False, timeout=0.3)
        c2.msgs.clear()
        await c2.pump(until=lambda: False, timeout=1.0)
        self.assertGreaterEqual(len(self.pads(c2)), 7)
        c2.close()

    async def test_new_page_sticks_dropped_until_open(self):
        """总览重排审查 O1：新连接默认不在手柄区，没发 drive {open} 的摇杆帧被丢掉，不会到 robotd。"""
        self.fake.set_posture("policy")
        await asyncio.sleep(1.2)
        token, _ = await self.login()
        c = WsClient(self.port, origin=self.origin, cookie=token)
        await c.open()
        stick = {"op": "sticks", "mode": "drive", "lx": 0, "ly": -1, "rx": 0, "ry": 0}
        await c.send({"t": "cmd", "id": "s1", "name": "pad.op", "args": dict(stick, seq=1)})
        await c.pump(until=lambda: any(m.get("id") == "s1" for m in c.msgs), timeout=3.0)
        r = next(m for m in c.msgs if m.get("id") == "s1")
        self.assertIn("dropped", r["data"])
        self.assertNotIn("robot.move", [m for _, m in self.fake.calls])
        await c.send({"t": "cmd", "id": "o1", "name": "pad.op", "args": {"op": "drive", "open": True, "seq": 2}})
        await c.pump(until=lambda: any(m.get("id") == "o1" for m in c.msgs), timeout=3.0)
        await c.send({"t": "cmd", "id": "s2", "name": "pad.op", "args": dict(stick, seq=3)})
        await c.pump(until=lambda: any(m.get("id") == "s2" for m in c.msgs), timeout=3.0)
        r = next(m for m in c.msgs if m.get("id") == "s2")
        self.assertIn("sent", r["data"], r)
        c.close()


def free_port():
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


class ServoSessionBatch3Test(unittest.IsolatedAsyncioTestCase):
    """第三批代码审查 D1、D2、D3、D9、D12：舵机会话的标记 / 重启接着管、离开时的竞态、robotd 被手动起来、交还提示。
    跟 HttpTest 不一样的地方：给了 state 目录（标记写在这里）；robotd 可以是「不在」（连一个没人听的端口）。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.state_dir.mkdir()
        self.log = Log(Path(self.tmp.name) / "logs", echo=False, background=False)
        self.fake = FakeRobotd(FIXTURE)
        self.s2, self.p2 = await self.fake.serve_tcp()
        self.s1 = None
        self.server = None

    async def start(self, robotd_up=True, servo=None):
        if robotd_up:
            self.s1, p1 = await self.fake.serve_tcp()
        else:
            p1 = free_port()                                # 没人听：robotd 不在
        self.p1 = p1
        self.rd = Robotd(f"tcp:127.0.0.1:{p1}", f"tcp:127.0.0.1:{self.p2}", self.log)
        self.servo = servo or ServoSession(self.log, sim=True)
        auth = Auth(self.state_dir, self.log)
        app = build_app(auth=auth, robotd=self.rd, log=self.log, web_dir=WEB, duck={"label": "测试鸭"}, state_dir=str(self.state_dir),
                        servo=self.servo, dev_autologin=True)
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0, ws="wsproto", http="h11", lifespan="on", log_level="critical")
        self.server = uvicorn.Server(cfg)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    async def stop(self):
        if self.server is not None:
            self.server.should_exit = True
            await self.task
            self.server = None

    async def asyncTearDown(self):
        await self.stop()
        if self.servo.state == "open":
            self.servo.close()
        for sv in (self.s1, self.s2):
            if sv is not None:
                sv.close()
        self.tmp.cleanup()

    @property
    def marker(self):
        return self.state_dir / "servo-session.json"

    def logs(self, word):
        return [e for e in self.log.recent(300) if word in e["msg"]]

    async def until(self, cond, timeout=8.0):
        end = time.monotonic() + timeout
        while not cond() and time.monotonic() < end:
            await asyncio.sleep(0.05)
        return cond()

    async def client(self, route="/servos"):
        c = WsClient(self.port, origin=self.origin)
        await c.open()
        self.assertTrue(c.accepted)
        await c.send({"t": "hb", "seq": 1, "visible": True, "route": route})
        return c

    async def cmd(self, c, cid, name, args=None, timeout=10.0):
        await c.send({"t": "cmd", "id": cid, "name": name, "args": args or {}})
        await c.pump(until=lambda: any(m.get("id") == cid for m in c.msgs), timeout=timeout)
        return next((m for m in c.msgs if m.get("id") == cid), None)

    async def state(self, c):
        """最新的一份（socket 里可能还压着之前的几份：先收 0.4 秒再取最后一份）。"""
        c.msgs.clear()
        await c.pump(until=lambda: False, timeout=0.4)
        await c.pump(until=lambda: any(m.get("t") == "state" for m in c.msgs), timeout=3.0)
        return [m for m in c.msgs if m.get("t") == "state"][-1]

    async def open_holding(self, c):
        r = await self.cmd(c, "o1", "servo.open")
        self.assertTrue(r["ok"], r)
        r = await self.cmd(c, "t1", "servo.op", {"op": "torque", "ids": [20, 21], "on": 1})
        self.assertTrue(r["ok"], r)

    # ---- D1：标记、Studio 退出不卸力、重启接着管 ----

    async def test_native_persistent_guard_prevents_boot_servo_autorestore(self):
        self.marker.write_text(json.dumps({'v':1,'sim':True,'at':1.7e9,'by':'x'}),encoding='utf-8')
        with unittest.mock.patch('studio.helper.native_update_state',return_value={'active':True,'phase':'recovery_required'}), \
             unittest.mock.patch('studio.app._bus_take',return_value=(True,None)) as take:
            await self.start(robotd_up=False)
            self.assertTrue(await self.until(lambda:self.logs('主程序更新保护'),timeout=2))
            self.assertEqual(self.servo.state,'closed')
            take.assert_not_called()
            self.assertTrue(self.marker.exists())

    async def test_marker_kept_on_exit_and_session_restored_after_restart(self):
        await self.start(robotd_up=True)
        c = await self.client()
        await self.open_holding(c)
        self.assertTrue(self.marker.exists())                                   # 开总线就写
        m = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertTrue(m["sim"])
        self.assertIn("by", m)
        c.close()
        sim = self.servo.sim_serial
        await self.stop()                                                       # Studio 正常退出
        self.assertEqual([sim._by_id(i).r[40] for i in (20, 21)], [1, 1])      # 退出不卸力
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.logs("Studio 退出：舵机会话开着"))
        # 重启：robotd 不在 → 接着管（演示舵机每次打开是新的一套，这里只看会话恢复了）
        self.s1.close()
        self.s1 = None
        self.servo = ServoSession(self.log, sim=True)
        await self.start(robotd_up=False, servo=self.servo)
        self.assertTrue(await self.until(lambda: self.servo.state == "open"))
        self.assertTrue(self.servo.restored)
        self.assertTrue(self.rd.paused)
        self.assertTrue(await self.until(lambda: self.logs("会话恢复")))
        c = await self.client("/")
        st = await self.state(c)
        self.assertTrue(st["servo_session"]["restored"])
        self.assertEqual(st["posture"], "servo_session")
        r = await self.cmd(c, "r1", "relax")                                     # 顶栏卸力走总线
        self.assertTrue(r["ok"], r)
        c.close()

    async def test_bad_marker_does_not_crash(self):
        self.marker.write_text("{坏的", encoding="utf-8")
        await self.start(robotd_up=False)
        self.assertTrue(await self.until(lambda: self.servo.state == "open"))
        self.assertTrue(self.logs("标记坏了"))
        self.assertTrue(await self.until(lambda: self.logs("会话恢复")))
        self.assertTrue(json.loads(self.marker.read_text(encoding="utf-8"))["sim"])   # 接管以后重新写好

    async def test_marker_with_robotd_running_is_dropped(self):
        self.marker.write_text(json.dumps({"v": 1, "sim": True, "port": None, "at": 1.7e9, "by": "x"}), encoding="utf-8")
        await self.start(robotd_up=True)
        self.assertTrue(await self.until(lambda: not self.marker.exists()))
        self.assertEqual(self.servo.state, "closed")
        self.assertTrue(self.logs("主程序现在在跑"))

    async def test_restore_failure_shows_orphan_and_relax_points_to_servo_page(self):
        self.marker.write_text(json.dumps({"v": 1, "sim": False, "port": None, "at": 1.7e9, "by": "x"}), encoding="utf-8")
        await self.start(robotd_up=False, servo=ServoSession(self.log, sim=False, port=None))
        self.assertTrue(await self.until(lambda: self.servo.orphan is not None))
        self.assertTrue(self.marker.exists())                                   # 没接管上：标记留着
        c = await self.client("/")
        st = await self.state(c)
        self.assertIsNotNone(st["servo_orphan"])
        self.assertEqual(st["estop"]["state"], "unavailable")
        self.assertIn("去舵机页接管", st["estop"]["why"])
        r = await self.cmd(c, "r1", "relax")
        self.assertFalse(r["ok"])
        self.assertIn("先去舵机页接管", r["reason"])
        c.close()

    async def test_handback_removes_marker_and_note_stays_until_robotd_connects(self):
        """D12：「已交还……sudo systemctl start robotd」一直到主程序连上为止（不再 10 分钟自己消失）。"""
        await self.start(robotd_up=False)
        c = await self.client()
        await self.open_holding(c)
        r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertFalse(self.marker.exists())
        self.servo.handed_back_at -= 700                                        # 过了 10 分钟：以前这里就消失了
        await asyncio.sleep(2.5)
        st = await self.state(c)
        self.assertIsNotNone(st["servo_handback_s"])
        self.s1, _ = await self.fake.serve_tcp(port=self.p1)                     # 主程序起来了
        ok = False
        end = time.monotonic() + 15
        while time.monotonic() < end and not ok:
            st = await self.state(c)
            ok = st["servo_handback_s"] is None
        self.assertTrue(ok)
        c.close()

    async def test_helper_available_is_exposed_for_handback_note(self):
        """D12：小助手可用时页面把「已交还」那句换成「正在把主程序起回来…」；后端把可用性推给页面。"""
        await self.start(robotd_up=False)
        self.servo.helper_available = True
        self.servo.handed_back_at = time.monotonic()
        c = await self.client("/")
        st = await self.state(c)
        self.assertTrue(st["servo_helper"])
        self.assertIsNotNone(st["servo_handback_s"])
        c.close()

    async def test_open_port_switch_while_open_is_refused_and_keeps_paused(self):
        """F1：调舵机中换口失败：state 仍 open、串口 / sim 没变、扭矩没写、robotd 仍 paused。"""
        await self.start()
        c = await self.client()
        await self.open_holding(c)
        before = (self.servo.port, self.servo.sim)
        r = await self.cmd(c, "p1", "servo.open", {"port": "/dev/ttyS99"})
        self.assertFalse(r["ok"])
        self.assertIn("不能换串口", r["reason"])
        self.assertEqual(self.servo.state, "open")
        self.assertEqual((self.servo.port, self.servo.sim), before)
        self.assertTrue(self.rd.paused)
        self.assertEqual([self.servo.sim_serial._by_id(i).r[40] for i in (20, 21)], [1, 1])
        c.close()

    async def test_marker_from_other_boot_is_dropped_without_take(self):
        """F2：上次开机留下的标记（boot_id 不同）= 断过电，舵机早松了：不接管、不 pause、删标记。"""
        self.marker.write_text(json.dumps({"v": 1, "sim": True, "port": None, "at": 1.7e9, "by": "x",
                                           "boot_id": "old-boot"}), encoding="utf-8")
        with unittest.mock.patch("studio.app.boot_id", return_value="new-boot"), \
             unittest.mock.patch("studio.app._bus_take") as take:
            await self.start(robotd_up=False)
            self.assertTrue(await self.until(lambda: not self.marker.exists()))
            await asyncio.sleep(0.1)
            self.assertEqual(self.servo.state, "closed")
            self.assertFalse(self.rd.paused)
            self.assertTrue(self.logs("断过电"))
            self.assertFalse(self.logs("会话恢复"))
            self.assertFalse(take.called)

    async def test_marker_with_same_boot_id_is_restored(self):
        """F2：boot_id 相同就照旧恢复（不是所有带 boot_id 的标记都作废）。"""
        self.marker.write_text(json.dumps({"v": 1, "sim": True, "port": None, "at": 1.7e9, "by": "x",
                                           "boot_id": "same-boot"}), encoding="utf-8")
        with unittest.mock.patch("studio.app.boot_id", return_value="same-boot"):
            await self.start(robotd_up=False)
            self.assertTrue(await self.until(lambda: self.servo.state == "open"))
            self.assertTrue(self.servo.restored)
            self.assertTrue(self.rd.paused)
            self.assertTrue(await self.until(lambda: self.logs("会话恢复")))
            m = json.loads(self.marker.read_text(encoding="utf-8"))
            self.assertEqual(m["boot_id"], "same-boot")

    async def test_handback_note_reports_unconfirmed_relax(self):
        """F6：卸力没确认的颗要跟着交还成功一起给页面看。"""
        await self.start(robotd_up=False)
        c = await self.client()
        await self.open_holding(c)
        self.servo.sim_serial._by_id(14).muted = {40}
        r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertIn("#14", self.servo.handback_note)
        self.assertIn("没确认卸力", self.servo.handback_note)
        st = await self.state(c)
        self.assertEqual(st["servo_handback_note"], self.servo.handback_note)
        c.close()

    async def test_handback_keeps_marker_when_helper_fails_and_flag_remains(self):
        """F7：交还后主程序没起来、旗还在：标记留着，Studio 重启才能接着管。"""
        await self.start(robotd_up=False)
        c = await self.client()
        await self.open_holding(c)
        with unittest.mock.patch.object(helper, "available", return_value=True), \
             unittest.mock.patch.object(helper, "bus_give_back",
                                        return_value=(False, "soft_failed", {"result": "soft_failed"})), \
             unittest.mock.patch.object(helper, "flag_present", return_value=True):
            r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.marker.exists())
        self.assertIn("主程序起不来", self.servo.giveback_error)
        self.assertIn("旗还在", self.servo.giveback_hint)
        st = await self.state(c)
        self.assertEqual(st["servo_giveback_error"], self.servo.giveback_error)
        self.assertEqual(st["servo_giveback_hint"], self.servo.giveback_hint)
        c.close()

    async def test_handback_drops_marker_when_helper_fails_without_flag(self):
        """F7：没起来但旗不在：标记删掉，提示手动起主程序。"""
        await self.start(robotd_up=False)
        c = await self.client()
        await self.open_holding(c)
        with unittest.mock.patch.object(helper, "available", return_value=True), \
             unittest.mock.patch.object(helper, "bus_give_back",
                                        return_value=(False, "soft_failed", {"result": "soft_failed"})), \
             unittest.mock.patch.object(helper, "flag_present", return_value=False):
            r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertFalse(self.marker.exists())
        self.assertIn("手动 sudo systemctl start robotd", self.servo.giveback_hint)
        c.close()

    async def test_take_failure_with_flag_adds_recovery_hint(self):
        """F8：bus-take 失败、旗挡住了主程序：把恢复办法接在失败原因后面。"""
        await self.start(robotd_up=False)
        c = await self.client()
        with unittest.mock.patch.object(helper, "available", return_value=True), \
             unittest.mock.patch.object(helper, "bus_take",
                                        return_value=(False, "port_busy", {"result": "port_busy"})), \
             unittest.mock.patch.object(helper, "flag_present", return_value=True):
            r = await self.cmd(c, "o1", "servo.open")
        self.assertFalse(r["ok"])
        self.assertIn("主程序现在被旗挡着起不来", r["reason"])
        self.assertFalse(self.rd.paused)
        self.assertEqual(self.servo.state, "closed")
        c.close()

    async def test_marker_write_failure_is_visible_and_keeps_session(self):
        """F10：标记写不进去不能关会话（舵机可能保持着），但要让页面看得到。"""
        await self.start()
        c = await self.client()
        real_replace = os.replace

        def fail_marker(src, dst):
            if str(dst).endswith("servo-session.json"):
                raise OSError("disk full")
            return real_replace(src, dst)

        with unittest.mock.patch("studio.app.os.replace", side_effect=fail_marker):
            r = await self.cmd(c, "o1", "servo.open")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.servo.state, "open")
        self.assertIn("会话标记没写进去", self.servo.marker_error)
        st = await self.state(c)
        self.assertEqual(st["servo_marker_error"], self.servo.marker_error)
        r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertIsNone(self.servo.marker_error)
        c.close()

    async def test_handback_without_helper_does_not_claim_robotd_started(self):
        """F12：没装小助手时只说手动起，不记「主程序起来了」。"""
        await self.start(robotd_up=False)
        c = await self.client()
        await self.open_holding(c)
        with unittest.mock.patch.object(helper, "available", return_value=False):
            r = await self.cmd(c, "x1", "servo.close", {}, timeout=15)
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.logs("没装小助手，主程序要手动起"))
        self.assertFalse(self.logs("主程序起来了"))
        c.close()

    # ---- D2、D3：离开时的竞态和判断 ----

    async def test_torque_then_leave_in_one_packet_keeps_holding(self):
        """D2：「保持」和「离开」同一个 TCP 包里到：等保持做完、当场读扭矩，不交还。"""
        await self.start()
        c = await self.client()
        r = await self.cmd(c, "o1", "servo.open")
        self.assertTrue(r["ok"])
        data = b"".join(c.ws.send(TextMessage(json.dumps(m))) for m in (
            {"t": "cmd", "id": "tq", "name": "servo.op", "args": {"op": "torque", "ids": [20, 21], "on": 1}},
            {"t": "hb", "seq": 9, "visible": True, "route": "/"},
            {"t": "cmd", "id": "x", "name": "servo.close", "args": {"if_idle": True}}))
        c.w.write(data)
        await c.w.drain()
        await c.pump(until=lambda: sum(1 for m in c.msgs if m.get("id") in ("tq", "x")) >= 2, timeout=10)
        res = {m["id"]: m for m in c.msgs if m.get("id") in ("tq", "x")}
        self.assertTrue(res["tq"]["ok"], res)
        self.assertIn("2 颗保持着", res["x"]["reason"])
        self.assertEqual(self.servo.state, "open")
        self.assertEqual([self.servo.sim_serial._by_id(i).r[40] for i in (20, 21)], [1, 1])
        c.close()

    async def test_torque_then_leave_same_packet_40_times(self):
        """D2：同一包里「保持 + 离开」重复 40 次，每一次都不能在交还后还上着力（repro_torque_race.py 的确定性版）。"""
        await self.start()
        c = await self.client()
        self.assertTrue((await self.cmd(c, "o1", "servo.open"))["ok"])
        try:
            for n in range(40):
                c.msgs.clear()
                tq_id, x_id = f"tq{n}", f"x{n}"
                payload = b"".join(c.ws.send(TextMessage(json.dumps(m))) for m in (
                    {"t": "cmd", "id": tq_id, "name": "servo.op", "args": {"op": "torque", "ids": [20, 21], "on": 1}},
                    {"t": "hb", "seq": 20 + n, "visible": True, "route": "/"},
                    {"t": "cmd", "id": x_id, "name": "servo.close", "args": {"if_idle": True}}))
                c.w.write(payload)
                await c.w.drain()
                await c.pump(until=lambda: sum(1 for m in c.msgs if m.get("id") in (tq_id, x_id)) >= 2, timeout=10)
                res = {m["id"]: m for m in c.msgs if m.get("id") in (tq_id, x_id)}
                self.assertTrue(res[tq_id]["ok"], f"第 {n + 1} 次上力：{res[tq_id]}")
                self.assertIn("2 颗保持着", res[x_id]["reason"], f"第 {n + 1} 次离开：{res[x_id]}")
                self.assertEqual(self.servo.state, "open", f"第 {n + 1} 次离开后会话不该被交还")
                self.assertEqual([self.servo.sim_serial._by_id(i).r[40] for i in (20, 21)], [1, 1],
                                 f"第 {n + 1} 次离开后舵机还上着力")
                off = await self.cmd(c, f"off{n}", "servo.op", {"op": "torque", "ids": [20, 21], "on": 0})
                self.assertTrue(off["ok"], f"第 {n + 1} 次复位：{off}")
        finally:
            c.close()

    async def test_leave_while_calibrating_keeps_session_and_calibration_finishes(self):
        """D3a：一键校准跑着时离开：不交还，校准 15 颗都做完。"""
        await self.start()
        c = await self.client()
        self.assertTrue((await self.cmd(c, "o1", "servo.open"))["ok"])
        # 点了「一键校准」马上切页（同一个包里到：演示舵机校得很快，分开发的话离开时可能已经校完了）
        c.w.write(b"".join(c.ws.send(TextMessage(json.dumps(m))) for m in (
            {"t": "cmd", "id": "cal", "name": "servo.op", "args": {"op": "calibrate", "ids": "20-24,30-34,10-14"}},
            {"t": "hb", "seq": 2, "visible": True, "route": "/"},
            {"t": "cmd", "id": "x", "name": "servo.close", "args": {"if_idle": True}})))
        await c.w.drain()
        await c.pump(until=lambda: any(m.get("id") == "x" for m in c.msgs), timeout=10)
        r = next(m for m in c.msgs if m.get("id") == "x")
        self.assertIn("正在校准", r["reason"])
        await c.pump(until=lambda: any(m.get("id") == "cal" for m in c.msgs), timeout=30)
        cal = next(m for m in c.msgs if m.get("id") == "cal")
        self.assertTrue(cal["ok"], cal)
        self.assertEqual(cal["data"]["bad"], {})
        self.assertEqual(len(cal["data"]["ok"]), 15)
        self.assertEqual(self.servo.state, "open")
        c.close()

    async def test_other_page_on_servos_keeps_session(self):
        """D3b：A 页还在舵机页（0 颗保持），B 页离开：不把 A 的会话交还。"""
        await self.start()
        a = await self.client()
        self.assertTrue((await self.cmd(a, "o1", "servo.open"))["ok"])
        b = await self.client()
        await b.send({"t": "hb", "seq": 2, "visible": True, "route": "/"})
        r = await self.cmd(b, "x", "servo.close", {"if_idle": True})
        self.assertIn("还有 1 个页面在舵机页", r["reason"])
        self.assertEqual(self.servo.state, "open")
        a.close()
        b.close()

    async def test_unreadable_torque_counts_as_maybe_holding(self):
        """D3c：读不到扭矩的舵机不当 0：离开不交还，提示条能写出来。"""
        await self.start()
        c = await self.client()
        self.assertTrue((await self.cmd(c, "o1", "servo.open"))["ok"])
        self.servo.sim_serial._by_id(14).muted = {40}
        await c.send({"t": "hb", "seq": 2, "visible": True, "route": "/"})
        r = await self.cmd(c, "x", "servo.close", {"if_idle": True})
        self.assertIn("1 颗读不到扭矩", r["reason"])
        self.assertEqual(self.servo.state, "open")
        st = await self.state(c)
        self.assertEqual(st["servo_session"]["unknown"], 1)
        c.close()

    # ---- D9：会话期间 robotd 被手动起来 ----

    async def test_robotd_started_by_hand_closes_bus_without_writing(self):
        await self.start(robotd_up=True)
        self.servo.probe_robotd = True                     # 演示舵机默认不查（配的假 robotd 一直在）；这里当真串口查
        c = await self.client()
        await self.open_holding(c)
        sim = self.servo.sim_serial
        # close() publishes the port state in a worker before the async watcher
        # finishes dropping its marker, unpausing robotd and reporting conflict.
        self.assertTrue(await self.until(lambda: self.servo.state == "closed" and not self.rd.paused
                                        and not self.marker.exists() and bool(self.logs("主程序被手动起来了")), timeout=12))
        self.assertEqual([sim._by_id(i).r[40] for i in (20, 21)], [1, 1])      # 没写舵机（robotd 自己会全部松开）
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.rd.paused)
        self.assertTrue(self.logs("主程序被手动起来了"))
        st = await self.state(c)
        self.assertIsNotNone(st["servo_conflict_s"])
        c.close()


if __name__ == "__main__":
    unittest.main()
