"""同一个端口同时接 http 和 https（studio/dual_listen.py）。

真的起一个 uvicorn，用真的套接字连：明文、TLS、两种 WebSocket、连上不说话的、不认证书的。
证书现场用 openssl 生成（跟鸭子第一次开机一样带 IP 的 SAN）；找不到 openssl 就跳过。
"""
import asyncio
import http.client
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

import uvicorn  # noqa: E402
from wsproto import ConnectionType, WSConnection  # noqa: E402
from wsproto.events import AcceptConnection, Request, TextMessage  # noqa: E402

from studio import net as N  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.auth import Auth  # noqa: E402
from studio.dual_listen import DualServer, load_tls  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.robotd import Robotd  # noqa: E402
from studio.ws_protocol import MusicWebSocketProtocol  # noqa: E402

WEB = HERE.parent / "web"


def find_openssl():
    found = shutil.which("openssl")
    if found:
        return found
    for guess in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if Path(guess).is_file():
            return guess
    return None


OPENSSL = find_openssl()


def make_cert(directory, name):
    crt, key = Path(directory) / f"{name}.crt", Path(directory) / f"{name}.key"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=duck-test",
                    "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(key), "-out", str(crt)],
                   check=True, capture_output=True)
    return str(crt), str(key)


async def app(scope, receive, send):
    """回这条连接的协议和客户端地址：两种连接进的是不是同一个应用、各自认不认得清。"""
    if scope["type"] == "http":
        body = f"{scope['scheme']} {scope['client'][0]}".encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
    elif scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": scope["scheme"]})
        await send({"type": "websocket.close"})


@unittest.skipUnless(OPENSSL, "没找到 openssl，生成不了测试证书")
class DualListenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.crt, cls.key = make_cert(cls.tmp.name, "duck")
        cls.trust = ssl.create_default_context(cafile=cls.crt)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, ws=MusicWebSocketProtocol, http="h11",
                                lifespan="off", log_level="warning")
        cls.server = DualServer(config, load_tls(cls.crt, cls.key), peek_timeout=0.6)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        end = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < end:
            time.sleep(0.02)
        assert cls.server.started, "服务没起来"
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.tmp.cleanup()

    def get(self, tls, context=None):
        if tls:
            c = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=5, context=context or self.trust)
        else:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request("GET", "/")
            r = c.getresponse()
            return r.status, r.read().decode()
        finally:
            c.close()

    def test_plain_http_is_served_as_http(self):
        self.assertEqual(self.get(False), (200, "http 127.0.0.1"))

    def test_tls_on_the_same_port_is_served_as_https(self):
        self.assertEqual(self.get(True), (200, "https 127.0.0.1"))

    def test_the_two_can_alternate(self):
        for _ in range(3):
            self.assertEqual(self.get(False)[1], "http 127.0.0.1")
            self.assertEqual(self.get(True)[1], "https 127.0.0.1")

    def test_websocket_scheme_follows_the_connection(self):
        async def one(context):
            r, w = await asyncio.open_connection("127.0.0.1", self.port, ssl=context)
            ws = WSConnection(ConnectionType.CLIENT)
            w.write(ws.send(Request(host=f"127.0.0.1:{self.port}", target="/ws")))
            await w.drain()
            accepted, text = False, None
            end = time.monotonic() + 5
            while text is None and time.monotonic() < end:
                data = await asyncio.wait_for(r.read(65536), 5)
                if not data:
                    break
                ws.receive_data(data)
                for event in ws.events():
                    accepted = accepted or isinstance(event, AcceptConnection)
                    if isinstance(event, TextMessage):
                        text = event.data
            w.close()
            return accepted, text

        self.assertEqual(asyncio.run(one(None)), (True, "ws"))
        self.assertEqual(asyncio.run(one(self.trust)), (True, "wss"))

    def test_first_byte_may_arrive_late(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            time.sleep(0.25)
            s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            data = b""
            while chunk := s.recv(4096):
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.1 200"), data[:80])
        self.assertTrue(data.endswith(b"http 127.0.0.1"), data[-40:])

    def test_silent_connection_is_closed_without_blocking_others(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as silent:
            self.assertEqual(self.get(False)[0], 200)        # 有人占着不说话，别人照样进得来
            self.assertEqual(self.get(True)[0], 200)
            silent.settimeout(5)
            self.assertEqual(silent.recv(1), b"")            # 等够了，被服务端关掉

    def test_peer_that_hangs_up_first_is_harmless(self):
        socket.create_connection(("127.0.0.1", self.port), timeout=5).close()
        self.assertEqual(self.get(False)[0], 200)

    def test_client_that_distrusts_the_certificate_does_not_hurt_the_server(self):
        with self.assertRaises(ssl.SSLError):
            self.get(True, context=ssl.create_default_context())   # 手机还没装鸭子的证书
        self.assertEqual(self.get(False)[1], "http 127.0.0.1")
        self.assertEqual(self.get(True)[1], "https 127.0.0.1")

    def test_mismatched_key_is_reported_as_oserror(self):
        _, other_key = make_cert(self.tmp.name, "other")
        with self.assertRaises(OSError):
            load_tls(self.crt, other_key)


@unittest.skipUnless(OPENSSL, "没找到 openssl，生成不了测试证书")
class DualListenShutdownTest(unittest.TestCase):
    def test_stops_while_a_connection_is_still_undecided(self):
        with tempfile.TemporaryDirectory() as tmp:
            crt, key = make_cert(tmp, "duck")
            config = uvicorn.Config(app, host="127.0.0.1", port=0, http="h11", lifespan="off", log_level="warning")
            server = DualServer(config, load_tls(crt, key), peek_timeout=60)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            end = time.monotonic() + 10
            while not server.started and time.monotonic() < end:
                time.sleep(0.02)
            self.assertTrue(server.started)
            with socket.create_connection(("127.0.0.1", server.port), timeout=5) as silent:
                time.sleep(0.1)
                server.should_exit = True
                thread.join(10)
                self.assertFalse(thread.is_alive(), "有连接还没分清是哪种时关不掉")
                silent.settimeout(5)
                self.assertEqual(silent.recv(1), b"")
            with self.assertRaises(OSError):                 # 监听口也关了
                socket.create_connection(("127.0.0.1", server.port), timeout=2).close()


@unittest.skipUnless(OPENSSL, "没找到 openssl，生成不了测试证书")
class StudioBothSchemesTest(unittest.IsolatedAsyncioTestCase):
    """真的 Studio 应用：老地址（http）和手机麦克风用的 https 在同一个端口上都能用，各自拿到自己协议的地址。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.crt, self.key = make_cert(root, "duck")
        log = Log(root / "logs", echo=False, background=False)
        self.rd = Robotd("tcp:127.0.0.1:1", "tcp:127.0.0.1:1", log)     # 主程序不在：页面照样要能打开
        state = root / "net.json"
        state.write_text(json.dumps({"mode": "hotspot", "hotspot_ssid": "Microduck-c51b", "hotspot_ip": "10.42.0.1",
                                     "hotspot_psk": "microduck", "hostname": "duck-c51b", "since": time.time(),
                                     "scan": [], "scan_at": time.time()}), encoding="utf-8")
        net = N.Net("tcp:127.0.0.1:1", state, root / "state", log, dev_mode=False, hostname="duck-c51b")
        net._read_once()
        app = build_app(auth=Auth(root / "state", log), robotd=self.rd, log=log, web_dir=WEB, duck={"label": "测试鸭"},
                        net=net, ca_cert=self.crt)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, ws=MusicWebSocketProtocol, http="h11", lifespan="on",
                                log_level="critical")
        self.server = DualServer(config, load_tls(self.crt, self.key))
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        self.port = self.server.port
        self.trust = ssl.create_default_context(cafile=self.crt)

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.task
        self.tmp.cleanup()

    def page(self, tls):
        cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
        c = cls("127.0.0.1", self.port, timeout=5, **({"context": self.trust} if tls else {}))
        try:
            c.request("GET", "/")
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    async def first_state(self, tls):
        scheme = "https" if tls else "http"
        r, w = await asyncio.open_connection("127.0.0.1", self.port, ssl=self.trust if tls else None)
        ws = WSConnection(ConnectionType.CLIENT)
        w.write(ws.send(Request(host=f"127.0.0.1:{self.port}", target="/ws",
                                extra_headers=[("Origin", f"{scheme}://127.0.0.1:{self.port}")])))
        await w.drain()
        buf, end = "", time.monotonic() + 8
        try:
            while time.monotonic() < end:
                data = await asyncio.wait_for(r.read(65536), 8)
                if not data:
                    break
                ws.receive_data(data)
                for event in ws.events():
                    if isinstance(event, TextMessage):
                        buf += event.data
                        if event.message_finished:
                            msg, buf = json.loads(buf), ""
                            if msg.get("t") == "state":
                                return msg
        finally:
            w.close()
        self.fail("没等到 state 消息")

    async def test_page_opens_over_both(self):
        plain = await asyncio.to_thread(self.page, False)
        secure = await asyncio.to_thread(self.page, True)
        self.assertEqual(plain[0], 200)
        self.assertEqual(secure, plain)

    async def test_plain_updater_health_and_signed_asset_with_secure_mobile_page(self):
        from studio.app import RUNTIME_ROOT, SUITE_VERSION
        from urllib.request import urlopen

        def check():
            plain = f"http://127.0.0.1:{self.port}"
            with urlopen(plain + "/healthz", timeout=3) as response:
                status = json.load(response)
            self.assertEqual(status["suite_version"], SUITE_VERSION)
            self.assertEqual(status["runtime_root"], str(RUNTIME_ROOT))
            with urlopen(plain + "/app.js", timeout=3) as response:
                served = response.read(2 * 1024 * 1024)
            self.assertEqual(hashlib.sha256(served).digest(), hashlib.sha256((WEB / "app.js").read_bytes()).digest())
            with urlopen(f"https://127.0.0.1:{self.port}/m", timeout=3, context=self.trust) as response:
                self.assertIn(b'm/app.js', response.read())
        await asyncio.to_thread(check)

    async def test_each_page_is_told_addresses_in_its_own_scheme(self):
        plain = (await self.first_state(False))["net"]
        self.assertEqual(plain["url_host"], "http://duck-c51b:8000/")
        self.assertEqual([u["url"] for u in plain["urls"]][0], "http://10.42.0.1:8000/")
        secure = (await self.first_state(True))["net"]
        self.assertEqual(secure["url_host"], "https://duck-c51b:8000/")
        self.assertEqual(secure["url_hotspot"], "https://10.42.0.1:8000/m")


if __name__ == "__main__":
    unittest.main()
