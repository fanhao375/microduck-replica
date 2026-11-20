"""studio/net.py：读 net.json、configd net.status/scan/connect/forget、小助手关/开热点的顺序和密码不外泄。

全部在本机跑：假 configd 就是替换 net.call_once，假小助手就是替换 helper.wifi_hotspot_*；
不连鸭子、不碰 192.168.0.x，也不碰任何真 socket。
"""
import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps"), str(HERE.parent / "dev")]

from studio import net as N  # noqa: E402
from studio.estop import EStop  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.robotd import Sample  # noqa: E402
from studio.state import summarize  # noqa: E402


def quiet_log(d):
    return Log(Path(d) / "logs", echo=False, background=False)


def state_json(mode="hotspot", **over):
    d = {"mode": mode, "hotspot_ssid": "Microduck-c51b", "hotspot_ip": "10.42.0.1", "hotspot_psk": "microduck",
         "hostname": "duck-c51b", "since": 1790000000.0,
         "scan": [{"ssid": "Cached", "signal": 61, "security": "wpa_psk"}], "scan_at": 1790000000.0}
    d.update(over)
    return d


class FakeRpc:
    """替身 call_once：记下 (method, params, timeout)，按 handler 回话（可以抛异常）。"""

    def __init__(self, handler, events=None):
        self.handler, self.calls, self.events = handler, [], events

    async def __call__(self, addr, method, params=None, timeout=2.0):
        self.calls.append((method, dict(params or {}), timeout))
        if method == "net.connect" and self.events is not None:
            self.events.append("connect")
        if method == "net.forget" and self.events is not None:
            self.events.append("forget")
        out = self.handler(method, params or {})
        if asyncio.iscoroutine(out):
            out = await out
        if isinstance(out, Exception):
            raise out
        return out


class FakeRd:
    def __init__(self):
        self.present = True
        self.bus, self.health, self.policies, self.state = Sample(), Sample(), Sample(), Sample()
        self.pad, self.sysinfo = Sample(), Sample()
        self.bus.set({"mode": "motion", "phase": "control", "native": {"torque_state_confirmed": True, "joints": []}})
        self.health.set({})
        self.policies.set({})
        self.state.set({"joints": []})
        self.pad.set({"pads": []})
        self.sysinfo.set({"name": "duck-c51b"})
        self.estop = EStop()


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = quiet_log(self.dir)
        self.state = self.dir / "net.json"
        self.net = N.Net("tcp:127.0.0.1:1", self.state, self.dir / "state", self.log, dev_mode=False,
                         hostname="duck-c51b")
        self.available = mock.patch.object(N.helper, "available", return_value=True)
        self.available.start()

    def tearDown(self):
        self.available.stop()
        self.tmp.cleanup()

    def write_state(self, **kw):
        self.state.write_text(json.dumps(state_json(**kw)), encoding="utf-8")

    def events(self):
        out = []

        def down(by, ssid=None):
            out.append("down")
            return True, "hotspot_down", {}

        def up(by):
            out.append("up")
            return True, "hotspot_up", {}
        return out, down, up


class StateFileTest(Base):
    def test_missing_is_unknown(self):
        self.net._read_once()
        self.assertEqual(self.net.view(), {"known": False})

    def test_corrupt_is_warned_once_and_unknown(self):
        self.state.write_text("{not json", encoding="utf-8")
        self.net._read_once()
        self.net._read_once()
        self.assertEqual(self.net.view(), {"known": False})
        msgs = [e["msg"] for e in self.log.recent()]
        self.assertEqual(sum("读不懂" in m for m in msgs), 1)

    def test_each_mode_is_known(self):
        for mode in ("wifi", "hotspot", "none"):
            with self.subTest(mode=mode):
                self.write_state(mode=mode)
                self.net._read_once()
                v = self.net.view()
                self.assertTrue(v["known"])
                self.assertEqual(v["mode"], mode)
                self.assertEqual(v["hostname"], "duck-c51b")
                self.assertEqual(v["url_local"], "http://duck-c51b.local:8000/")
                self.assertEqual(v["url_hotspot"], "http://10.42.0.1:8000/m")
                self.assertEqual(v["hotspot"], {"ssid": "Microduck-c51b", "ip": "10.42.0.1", "psk": "microduck"})

    def test_local_url_uses_the_listen_port(self):
        self.net.port = 8123
        self.write_state(mode="wifi")
        self.net._read_once()
        self.assertEqual(self.net.view()["url_local"], "http://duck-c51b.local:8123/")

    def test_addresses_takes_the_scheme(self):
        """开了 HTTPS 以后，给用户的地址也得是 https，不然手机麦克风那条路还是用不了。"""
        self.assertEqual([u["url"] for u in N.addresses("duck-x", "1.2.3.4", 9000, "https")],
                         ["https://duck-x:9000/", "https://duck-x.local:9000/"])
        self.assertEqual([u["url"] for u in N.addresses("duck-x", None, scheme="https")],
                         ["https://duck-x:8000/", "https://duck-x.local:8000/"])

    def test_https_scheme_reaches_every_url(self):
        net = N.Net("tcp:127.0.0.1:1", self.state, self.dir / "state", self.log, dev_mode=False,
                    hostname="duck-c51b", scheme="https")
        self.write_state(mode="hotspot")
        net._read_once()
        v = net.view()
        self.assertEqual(v["url_local"], "https://duck-c51b.local:8000/")
        self.assertEqual(v["url_host"], "https://duck-c51b:8000/")
        self.assertEqual(v["url_hotspot"], "https://10.42.0.1:8000/m")
        self.assertEqual([u["url"] for u in v["urls"]],
                         ["https://10.42.0.1:8000/", "https://duck-c51b:8000/", "https://duck-c51b.local:8000/"])

    def test_unknown_scheme_falls_back_to_http(self):
        net = N.Net("tcp:127.0.0.1:1", self.state, self.dir / "state", self.log, dev_mode=False,
                    hostname="duck-c51b", scheme="ftp")
        self.write_state(mode="wifi")
        net._read_once()
        self.assertEqual(net.view()["url_local"], "http://duck-c51b.local:8000/")

    def test_https_lan_view_preserves_ip_status_but_publishes_only_dns_urls(self):
        net = N.Net("tcp:127.0.0.1:1", self.state, self.dir / "state", self.log, dev_mode=False,
                    hostname="duck-c51b", scheme="https")
        self.write_state(mode="wifi")
        net._read_once()
        net.status = {"state": "connected", "ssid": "Home", "ip4": "192.168.1.50"}
        net.status_at = time.monotonic()
        view = net.view()
        self.assertEqual(view["ip_now"], "192.168.1.50")
        self.assertEqual([u["url"] for u in view["urls"]],
                         ["https://duck-c51b:8000/", "https://duck-c51b.local:8000/"])

    def test_status_fields_and_last_try_text(self):
        self.write_state(mode="wifi")
        self.net._read_once()
        self.net.status = {"state": "connected", "ssid": "Home", "signal": 72, "ip4": "192.168.1.50"}
        self.net.status_at = time.monotonic()
        self.net.last_try = {"ssid": "Home", "at": 1.0, "ok": False, "reason": "bad_key", "detail": None, "ip4": None}
        v = self.net.view()
        self.assertEqual((v["ssid"], v["signal"], v["ip4"]), ("Home", 72, "192.168.1.50"))
        self.assertEqual(v["last_try"]["reason_text"], "密码不对")
        self.assertIsNone(v["busy"])

    def test_addresses_hotspot_and_wifi(self):
        """任务 16：「这只鸭子的地址」——IP（热点模式是 10.42.0.1，否则 net.status 的 ip4）、duck-xxxx、duck-xxxx.local，
        按设备写该用哪个（开着 Clash 系统代理的 Windows 打不开 .local，不带点的能开）。"""
        self.write_state(mode="hotspot")
        self.net._read_once()
        v = self.net.view()
        self.assertEqual(v["ip_now"], "10.42.0.1")
        self.assertEqual(v["url_host"], "http://duck-c51b:8000/")
        self.assertEqual([(u["kind"], u["url"]) for u in v["urls"]],
                         [("ip", "http://10.42.0.1:8000/"), ("host", "http://duck-c51b:8000/"), ("local", "http://duck-c51b.local:8000/")])
        self.assertIn("Windows", v["urls"][1]["note"])
        self.assertIn("苹果", v["urls"][2]["note"])
        self.write_state(mode="wifi")
        self.net._read_once()
        self.assertEqual([u["kind"] for u in self.net.view()["urls"]], ["host", "local"])     # 没连上：不知道 IP 就不列
        self.net.status = {"state": "connected", "ssid": "Home", "signal": 72, "ip4": "192.168.0.102"}
        self.net.status_at = time.monotonic()
        v = self.net.view()
        self.assertEqual(v["ip_now"], "192.168.0.102")
        self.assertEqual(v["urls"][0], {"kind": "ip", "url": "http://192.168.0.102:8000/",
                                        "note": "IP：哪台设备都能打开（换了路由器、重新分了地址会变）"})

    def test_state_summarize_has_net_shapes(self):
        rd = FakeRd()
        self.assertEqual(summarize(rd, {"label": "鸭"})["net"], {"known": False})
        self.write_state(mode="hotspot")
        self.net._read_once()
        s = summarize(rd, {"label": "鸭"}, net=self.net)
        self.assertTrue(s["net"]["known"])
        self.assertEqual(s["net"]["mode"], "hotspot")
        self.write_state(mode="wifi")
        self.net._read_once()
        s = summarize(rd, {"label": "鸭"}, net=self.net)
        self.assertEqual(s["net"]["mode"], "wifi")


class ScanTest(Base):
    async def test_hotspot_empty_uses_net_json_cache(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        fake = FakeRpc(lambda m, p: {"networks": []})
        with mock.patch.object(N, "call_once", fake):
            data = await self.net.scan()
        self.assertTrue(data["cached"])
        self.assertEqual([n["ssid"] for n in data["networks"]], ["Cached"])
        self.assertEqual(data["scan_at"], 1790000000.0)

    async def test_hotspot_error_uses_cache(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        fake = FakeRpc(lambda m, p: N.Timeout("net.scan 15 秒没回话"))
        with mock.patch.object(N, "call_once", fake):
            data = await self.net.scan()
        self.assertTrue(data["cached"])
        self.assertEqual(data["networks"][0]["ssid"], "Cached")

    async def test_own_hotspot_is_filtered(self):
        """任务 16：真机扫描结果里有鸭子自己的热点（Microduck-0de6，显示「已保存」），点了会让人往里填家里密码：滤掉。
        按 net.json 的 hotspot_ssid 认；没有 hotspot_ssid 时认「Microduck- 开头、而且是已保存」的。"""
        nets = [{"ssid": "Microduck-c51b", "signal": 90, "security": "wpa_psk", "saved": True},
                {"ssid": "Home", "signal": 70, "security": "wpa_psk", "saved": False},
                {"ssid": "Microduck-0de6", "signal": 40, "security": "wpa_psk", "saved": False}]
        self.write_state(mode="wifi")
        self.net._read_once()
        with mock.patch.object(N, "call_once", FakeRpc(lambda m, p: {"networks": [dict(n) for n in nets]})):
            data = await self.net.scan()
        self.assertEqual([n["ssid"] for n in data["networks"]], ["Home", "Microduck-0de6"])   # 别的鸭子的热点不滤
        self.write_state(mode="wifi", hotspot_ssid=None)
        self.net._read_once()
        nets.append({"ssid": "Microduck-0de6", "signal": 40, "security": "wpa_psk", "saved": True})
        with mock.patch.object(N, "call_once", FakeRpc(lambda m, p: {"networks": [dict(n) for n in nets]})):
            data = await self.net.scan()
        self.assertEqual([n["ssid"] for n in data["networks"]], ["Home", "Microduck-0de6"])   # 已保存的 Microduck-* 滤掉
        self.assertTrue(all(not (n["ssid"].startswith("Microduck-") and n["saved"]) for n in data["networks"]))
        self.write_state(mode="hotspot", scan=[{"ssid": "Microduck-c51b", "signal": 99, "security": "wpa_psk"},
                                               {"ssid": "Cached", "signal": 61, "security": "wpa_psk"}])
        self.net._read_once()
        with mock.patch.object(N, "call_once", FakeRpc(lambda m, p: {"networks": []})):
            data = await self.net.scan()
        self.assertTrue(data["cached"])
        self.assertEqual([n["ssid"] for n in data["networks"]], ["Cached"])              # 开热点前的缓存也滤

    async def test_wifi_mode_error_is_not_hidden_by_cache(self):
        self.write_state(mode="wifi")
        self.net._read_once()
        fake = FakeRpc(lambda m, p: {"networks": []})
        with mock.patch.object(N, "call_once", fake):
            data = await self.net.scan()
        self.assertFalse(data["cached"])
        self.assertEqual(data["networks"], [])


class ConnectTest(Base):
    async def test_hotspot_connect_order_and_last_try(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        fake = FakeRpc(lambda m, p: {"outcome": "connected", "ssid": p["ssid"], "ip4": "192.168.1.66"}, ev)
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", fake):
            self.assertTrue(self.net.begin_connect())
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "connect"])
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["ip4"], "192.168.1.66")
        self.assertIsNone(self.net.busy)
        saved = json.loads((self.dir / "state" / "net-last-try.json").read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"ssid", "at", "ok", "reason", "detail", "ip4"})
        self.assertTrue(saved["ok"])
        self.assertNotIn("SecretPSK123", json.dumps(saved))
        self.assertEqual(self.net.view()["ssid"], "Home")

    async def test_not_found_restores_hotspot_immediately(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        fake = FakeRpc(lambda m, p: {"outcome": "failed", "reason": "not_found", "detail": "no such ssid"}, ev)
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Nope", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "connect", "up"])
        self.assertEqual(rec["reason"], "not_found")
        self.assertIn("找不到", N.explain(rec["reason"], rec["detail"]))

    async def test_unsupported_restores_hotspot_immediately(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        fake = FakeRpc(lambda m, p: {"outcome": "failed", "reason": "unsupported", "detail": "enterprise"}, ev)
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Office", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "connect", "up"])
        self.assertEqual(rec["reason"], "unsupported")
        self.assertIn("连不了", N.explain(rec["reason"], rec["detail"]))

    async def test_bad_key_forgets_then_restores_hotspot(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        fake = FakeRpc(lambda m, p: {"outcome": "failed", "reason": "bad_key", "detail": "auth failed"}, ev)
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "connect", "forget", "up"])
        self.assertEqual(rec["reason"], "bad_key")
        self.assertIn("密码不对", N.explain(rec["reason"]))

    async def test_timeout_and_other_restore_hotspot_immediately(self):
        for reason in ("timeout", "other"):
            with self.subTest(reason=reason):
                self.write_state(mode="hotspot")
                self.net._read_once()
                ev, down, up = self.events()
                fake = FakeRpc(lambda m, p, reason=reason: {
                    "outcome": "failed", "reason": reason, "detail": "configd said so"}, ev)
                with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
                     mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
                     mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
                     mock.patch.object(N, "call_once", fake):
                    self.net.begin_connect()
                    rec = await self.net.connect("Home", "SecretPSK123", "电脑")
                self.assertEqual(ev, ["down", "connect", "up"])
                self.assertEqual(rec["reason"], reason)

    async def test_studio_waiting_for_configd_is_its_own_reason(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        fake = FakeRpc(lambda m, p: N.Timeout("60 秒没回话"), ev)
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "connect", "up"])
        self.assertEqual(rec["reason"], "studio_timeout")
        self.assertIn("配网程序 60 秒没回话", N.explain(rec["reason"], rec["detail"]))

    async def test_hotspot_down_failure_still_tries_to_bring_it_back(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, _, up = self.events()

        def down_fail(by, ssid=None):
            ev.append("down")
            return False, "down_failed", {"stderr": "nmcli said no"}

        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down_fail), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", FakeRpc(lambda m, p: {})):
            self.net.begin_connect()
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(ev, ["down", "up"])
        self.assertFalse(rec["ok"])
        self.assertIn("关热点没成", rec["detail"])

    async def test_hotspot_connect_refuses_without_helper(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        ev, down, up = self.events()
        with mock.patch.object(N.helper, "available", return_value=False), \
             mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", side_effect=down), \
             mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=up), \
             mock.patch.object(N, "call_once", FakeRpc(lambda m, p: {})):
            self.net.begin_connect()
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(ev, [])
        self.assertFalse(rec["ok"])
        self.assertIn("deploy/install.sh", rec["detail"])

    async def test_only_one_connect_at_a_time(self):
        self.write_state(mode="wifi")
        self.net._read_once()
        self.assertTrue(self.net.begin_connect())
        self.assertFalse(self.net.begin_connect())
        self.assertEqual(self.net.view()["busy"], "connecting")

    async def test_permission_error_is_translated(self):
        self.write_state(mode="wifi")
        self.net._read_once()
        fake = FakeRpc(lambda m, p: N.RpcError("net.connect", 14, "not allowed"))
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Home", "SecretPSK123", "电脑")
        self.assertEqual(rec["detail"], N.PERMISSION)
        self.assertIn("configd 没放行 radxa", json.loads(
            (self.dir / "state" / "net-last-try.json").read_text(encoding="utf-8"))["detail"])


class HotspotOpTest(Base):
    """户外模式：手机点「开/关热点」→ net.op → hotspot_up/down。这里只验证顺序、busy 和报错文案。"""

    def setUp(self):
        super().setUp()
        self.write_state(mode="wifi")
        self.net._read_once()

    def test_begin_hotspot_locks_and_resets(self):
        self.assertTrue(self.net.begin_hotspot())
        self.assertEqual(self.net.view()["busy"], "hotspot")
        self.assertFalse(self.net.begin_hotspot())          # 第二次不给进
        self.assertFalse(self.net.begin_connect())          # 连 WiFi 也不插队
        self.net.busy = None
        self.assertTrue(self.net.begin_connect())           # 清掉后别人能用

    def test_begin_hotspot_unknown_state_is_refused(self):
        net = N.Net("tcp:127.0.0.1:1", self.dir / "missing.json", self.dir / "state", self.log,
                    dev_mode=False, hostname="duck-c51b")
        net._read_once()
        with self.assertRaises(N.Refused):
            net.begin_hotspot()

    async def test_hotspot_up_success_and_busy_reset(self):
        self.net.begin_hotspot()
        with mock.patch.object(N.helper, "wifi_hotspot_up", return_value=(True, "hotspot_up", {})) as up:
            ok, why = await self.net.hotspot_up("手机")
        self.assertTrue(ok)
        self.assertIsNone(why)
        self.assertIsNone(self.net.busy)
        up.assert_called_once_with("手机")

    async def test_hotspot_up_already_up_still_ok(self):
        with mock.patch.object(N.helper, "wifi_hotspot_up", return_value=(True, "already_up", {})):
            ok, why = await self.net.hotspot_up("手机")
        self.assertTrue(ok)
        self.assertIsNone(why)

    async def test_hotspot_up_bad_verb_points_to_reinstall(self):
        with mock.patch.object(N.helper, "wifi_hotspot_up", return_value=(False, "bad_verb", "unknown verb")):
            ok, why = await self.net.hotspot_up("手机")
        self.assertFalse(ok)
        self.assertIn("wifi-hotspot-up", why)
        self.assertIn("install.sh", why)
        self.assertIsNone(self.net.busy)

    async def test_hotspot_up_helper_raises_is_reported(self):
        with mock.patch.object(N.helper, "wifi_hotspot_up", side_effect=OSError("boom")):
            ok, why = await self.net.hotspot_up("手机")
        self.assertFalse(ok)
        self.assertIn("小助手没跑成", why)
        self.assertIsNone(self.net.busy)

    async def test_hotspot_up_without_helper_refuses(self):
        with mock.patch.object(N.helper, "available", return_value=False):
            with self.assertRaises(N.Refused):
                await self.net.hotspot_up("手机")
        self.assertIsNone(self.net.busy)

    async def test_hotspot_down_success(self):
        self.write_state(mode="hotspot")
        self.net._read_once()
        self.net.begin_hotspot()
        with mock.patch.object(N.helper, "wifi_hotspot_down", return_value=(True, "hotspot_down", {})) as down:
            ok, why = await self.net.hotspot_down("手机", "Home")
        self.assertTrue(ok)
        self.assertIsNone(why)
        self.assertIsNone(self.net.busy)
        down.assert_called_once_with("手机", "Home")

    async def test_hotspot_down_bad_verb_points_to_reinstall(self):
        with mock.patch.object(N.helper, "wifi_hotspot_down", return_value=(False, "bad_verb", "unknown verb")):
            ok, why = await self.net.hotspot_down("手机")
        self.assertFalse(ok)
        self.assertIn("wifi-hotspot-down", why)
        self.assertIn("install.sh", why)
        self.assertIsNone(self.net.busy)

    async def test_hotspot_down_without_helper_refuses(self):
        with mock.patch.object(N.helper, "available", return_value=False):
            with self.assertRaises(N.Refused):
                await self.net.hotspot_down("手机")
        self.assertIsNone(self.net.busy)


class ValidateTest(unittest.TestCase):
    def test_ssid_bytes(self):
        self.assertEqual(N.validate_ssid("x" * 32), "x" * 32)
        self.assertEqual(N.validate_ssid("中" * 10), "中" * 10)
        for bad in ("", "x" * 33, "中" * 11, 3, None):
            with self.subTest(bad=bad):
                with self.assertRaises(N.Refused):
                    N.validate_ssid(bad)

    def test_psk(self):
        self.assertIsNone(N.validate_psk(None))
        self.assertIsNone(N.validate_psk(""))
        self.assertEqual(N.validate_psk("12345678"), "12345678")
        self.assertEqual(N.validate_psk("x" * 63), "x" * 63)
        self.assertEqual(N.validate_psk("a" * 64), "a" * 64)
        for bad in ("1234567", "x" * 64 + "0", "z" * 65, 12345678):
            with self.subTest(bad=bad):
                with self.assertRaises(N.Refused):
                    N.validate_psk(bad)


class FakeConfigdTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_encrypted_network_still_needs_password(self):
        from fake_robotd import FakeRobotd
        fake = FakeRobotd(str(HERE / "fixtures" / "now-20261018.jsonl"), net_mode="hotspot")
        r = await fake._net_connect({"ssid": "Home-5G"})
        self.assertEqual((r["outcome"], r["reason"]), ("failed", "unsupported"))


class SecretTest(Base):
    async def test_home_password_never_reaches_logs_state_or_diag(self):
        psk = "VerySecretHomePSK-2026"
        self.write_state(mode="hotspot")
        self.net._read_once()
        fake = FakeRpc(lambda m, p: {"outcome": "failed", "reason": "bad_key",
                                     "detail": f"key {p.get('psk')} rejected"})
        with mock.patch.object(N, "CONNECT_DELAY_S", 0), \
             mock.patch.object(N.helper, "wifi_hotspot_down", return_value=(True, "hotspot_down", {})), \
             mock.patch.object(N, "call_once", fake):
            self.net.begin_connect()
            rec = await self.net.connect("Home", psk, "电脑")
        self.assertEqual(rec["reason"], "bad_key")
        texts = [json.dumps(self.net.view(), ensure_ascii=False),
                 (self.dir / "state" / "net-last-try.json").read_text(encoding="utf-8")]
        for p in self.log.files():
            texts.append(p.read_text(encoding="utf-8"))
        from studio.app import _diag_zip
        data = _diag_zip(self.log, summarize(FakeRd(), {"label": "鸭"}, net=self.net), {})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                texts.append(z.read(name).decode("utf-8", "replace"))
        for text in texts:
            self.assertNotIn(psk, text)
        self.assertIn("带密码", "\n".join(texts))
        self.assertIn("[已隐藏]", "\n".join(texts))


if __name__ == "__main__":
    unittest.main()
