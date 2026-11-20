"""手柄页后端（studio/pad.py、pad_drive.py、pad_combo.py）：全用假的 configd / 蓝牙 / padd 旁路 / robotd，电脑上没蓝牙也能跑。
真端口（rpc_call、PaddTap、RobotPort、BtSession 读管道）用本机 TCP 假服务 / 真子进程测。

跑法（studio 目录下）：
    py -3.13 -I -c "import sys;sys.path[:0]=['deps','.'];import unittest;unittest.main(module=None,argv=['x','-v','tests.test_pad'])"
"""
import copy
import itertools
import json
import math
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio import pad as P  # noqa: E402
from studio import pad_combo as C  # noqa: E402
from studio import pad_drive as D  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.rpc import RpcError, Timeout, Unreachable  # noqa: E402

PRO = "98:B6:E9:28:06:09"        # Switch 克隆的经典那张脸（configd/src/pad.rs:169 的例子）
PRO_LE = "98:B6:ED:28:06:09"     # 同一个手柄的 LE 那张脸
XBOX = "78:86:2E:BB:13:28"
FAST = {"poll": 0.01, "status_watch": 0.05, "status_job": 0.02, "padd": 0.6, "connect": 0.3, "readback": 0.3,
        "torque": 0.5, "press": 0.5, "lease": 5.0, "scan_tick": 0.02, "pair_window": 1, "scan_min": 0.1,
        "probe": 0.02, "shutdown_hold": 0.4, "select_long": 0.2, "relax_guard": 0.3,
        "walk_verify": 0.2, "walk_verify_poll": 0.02}
# 真机录的 robot.policies（tests/fixtures/now-20261018.jsonl:5），路径缩短
POLICIES = {"enabled": True, "homed": True, "mode": "walk", "sitting": False,
            "skills": ["roulade", "kick_left", "kick_right", "roller_stand"],
            "slots": [{"slot": "walk", "path": "/w.onnx"}, {"slot": "stand", "path": None},
                      {"slot": "sitstand", "path": "/s.onnx"}, {"slot": "ground_pick", "path": "/g.onnx"}]}


def wait_until(cond, timeout=3.0, what="条件"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError(f"{timeout} 秒没等到：{what}")


def bus(torque_on=True, **kw):
    b = {"mode": "motion", "phase": "control", "torque_off_pending": False,
         "native": {"torque_state_confirmed": bool(torque_on),
                    "hd1910": {"restore_pending": False, "temporary_gains_applied": False}}}
    b.update(kw)
    return b


# ---------------- 假的 ----------------

class FakeConfigd:
    def __init__(self):
        self.status = {"driver": "active", "pads": []}
        self.status_exc = None
        self.pair_result = None
        self.pair_exc = None
        self.pair_delay = 0.0
        self.on_pair = None
        self.forget_exc = None
        self.calls = []
        self.lk = threading.Lock()

    def call(self, method, params=None, timeout=3.0):
        with self.lk:
            self.calls.append((method, copy.deepcopy(params)))
        if method == "pad.status":
            if self.status_exc:
                raise self.status_exc
            with self.lk:
                return copy.deepcopy(self.status)
        if method == "pad.pair":
            time.sleep(self.pair_delay)
            if self.pair_exc:
                raise self.pair_exc
            if self.on_pair:
                self.on_pair(params)
            return copy.deepcopy(self.pair_result)
        if method == "pad.forget":
            if self.forget_exc:
                raise self.forget_exc
            return {"removed": True}
        raise RpcError(method, -32601, "method not found")

    def connect_pad(self, mac, name, connected=True, trusted=True):
        with self.lk:
            self.status["pads"] = [{"mac": mac, "name": name, "paired": True, "trusted": trusted, "connected": connected}]


class FakeBt:
    def __init__(self):
        self.state = {"present": True, "powered": True, "pairable": True, "blocked": False}
        self.found = []
        self.infos = {}
        self.calls = []
        self.scan_exc = None
        self.pair_exc = None
        self.pair_excs = []          # 依次抛出（用来演 agent 注册失败后换会话重试）
        self.on_pair = None
        self.removed = None
        self.pairable_ok = False
        self.on_reconnect = None
        self.reconnect_errors = []

    def adapter(self):
        return dict(self.state)

    def power_on(self):
        self.calls.append("power_on")
        self.state["powered"] = True

    def pairable_on(self):
        self.calls.append("pairable_on")
        if not self.pairable_ok:
            raise P.BtError("bt_permission", "org.freedesktop.DBus.Error.AccessDenied")
        self.state["pairable"] = True

    def pairable_off(self):
        self.calls.append("pairable_off")
        self.state["pairable"] = False

    def scan_start(self):
        self.calls.append("scan_start")
        if self.scan_exc:
            raise self.scan_exc

    def devices(self):
        return [dict(d, seen_at=time.monotonic()) for d in self.found]

    def scan_stop(self):
        self.calls.append("scan_stop")

    def info(self, mac):
        return dict(self.infos.get(mac, {}))

    def reconnect(self, mac, timeout=5.0):
        self.calls.append(("reconnect", mac, timeout))
        if self.reconnect_errors:
            raise self.reconnect_errors.pop(0)
        if self.on_reconnect:
            self.on_reconnect(mac)

    def pair_device(self, mac, classic, cancel, step):
        self.calls.append(("pair_device", mac, classic))
        for key in ("pair", "trust", "connect"):
            step(key, "doing")
            if key == "pair" and self.pair_excs:
                raise self.pair_excs.pop(0)
            if self.pair_exc and key == "pair":
                raise self.pair_exc
            step(key, "ok")
        if self.on_pair:
            self.on_pair(mac)

    def remove(self, mac):
        self.calls.append(("remove", mac))
        if isinstance(self.removed, Exception):
            raise self.removed
        return True

    def close(self):
        self.calls.append("close")


XBOX_DEV = {"name": "Xbox Wireless Controller", "node": "/dev/input/event5", "unique": "78:86:2e:bb:13:28", "bus": 5,
            "vendor": 1118, "product": 2835,
            "axes": [{"code": c, "name": n, "min": lo, "max": hi, "flat": 0, "fuzz": 0, "value": v} for c, n, lo, hi, v in (
                (0, "ABS_X", -32768, 32767, 0), (1, "ABS_Y", -32768, 32767, 0), (2, "ABS_Z", -32768, 32767, 0),
                (5, "ABS_RZ", -32768, 32767, 0), (10, "ABS_BRAKE", 0, 1023, 0), (9, "ABS_GAS", 0, 1023, 0),
                (16, "ABS_HAT0X", -1, 1, 0), (17, "ABS_HAT0Y", -1, 1, 0))],
            "buttons": [{"code": c, "name": n, "pressed": False} for c, n in (
                (0x130, "BTN_SOUTH"), (0x131, "BTN_EAST"), (0x133, "BTN_NORTH"), (0x134, "BTN_WEST"),
                (0x136, "BTN_TL"), (0x137, "BTN_TR"), (0x13A, "BTN_SELECT"), (0x13B, "BTN_START"))]}
PRO_DEV = {"name": "Pro Controller", "node": "/dev/input/event6", "unique": PRO.lower(), "bus": 5, "vendor": 0x057E,
           "product": 0x2009,
           "axes": [{"code": c, "name": n, "min": -32767, "max": 32767, "flat": 0, "fuzz": 0, "value": 0} for c, n in (
               (0, "ABS_X"), (1, "ABS_Y"), (3, "ABS_RX"), (4, "ABS_RY"))]
           + [{"code": 16, "name": "ABS_HAT0X", "min": -1, "max": 1, "flat": 0, "fuzz": 0, "value": 0},
              {"code": 17, "name": "ABS_HAT0Y", "min": -1, "max": 1, "flat": 0, "fuzz": 0, "value": 0}],
           "buttons": [{"code": c, "name": "x", "pressed": False} for c in (0x130, 0x131, 0x133, 0x134, 0x13A, 0x13B)]}


class FakeHelper:
    """假 root 小助手。available=False 时所有现有测试照旧走 configd / bluetoothctl。"""

    def __init__(self):
        self.is_available = False
        self.pair_result = (True, "paired", {})
        self.pair_exc = None
        self.pair_delay = 0.0
        self.on_pair = None
        self.forget_result = (True, "forgotten", {"removed": True})
        self.forget_exc = None
        self.pair_calls = []
        self.forget_calls = []

    def available(self):
        return self.is_available

    def pad_pair(self, by, mac=None, timeout=60):
        self.pair_calls.append((by, mac, timeout))
        time.sleep(self.pair_delay)
        if self.pair_exc:
            raise self.pair_exc
        if self.on_pair:
            self.on_pair(mac)
        return copy.deepcopy(self.pair_result)

    def pad_forget(self, by, mac, timeout=60):
        self.forget_calls.append((by, mac, timeout))
        if self.forget_exc:
            raise self.forget_exc
        return copy.deepcopy(self.forget_result)


class FakeTap:
    def __init__(self):
        self.on_report = self.on_state = None
        self.running = False
        self.starts = 0

    def start(self, on_report, on_state):
        self.on_report, self.on_state = on_report, on_state
        self.running = True
        self.starts += 1
        on_state("waiting", None)

    def stop(self):
        self.running = False

    def alive(self):
        return self.running

    def attach(self, dev):
        self.on_report({"report": "attached", "device": copy.deepcopy(dev)})

    def key(self, code, down):
        self.on_report({"report": "frame", "seq": 1, "at_us": 0,
                        "events": [{"kind": 1, "code": code, "value": 1 if down else 0, "name": "BTN"},
                                   {"kind": 0, "code": 0, "value": 0, "name": "SYN_REPORT"}]})

    def axis(self, code, value):
        self.on_report({"report": "frame", "seq": 2, "at_us": 0,
                        "events": [{"kind": 3, "code": code, "value": value, "name": "ABS"}]})


class FakeRobot:
    """假 robotd：notify 记进 sent；call 记进 calls。busStatus 回 self.bus（测急停自己问的就是它）。"""

    def __init__(self):
        self.sent = []
        self.calls = []
        self.fail = None
        self.bus = bus()
        self.replies = {}
        self.lk = threading.Lock()

    def notify(self, method, params):
        if self.fail:
            raise self.fail
        with self.lk:
            self.sent.append((method, dict(params)))

    def call(self, method, params=None, timeout=3.0):
        with self.lk:
            self.calls.append((method, copy.deepcopy(params)))
        if method == "pad.bindings" and method not in self.replies:
            return {"bindings": [{"button": "a", "skill": "ground_pick", "overridden": False}]}
        if method == "robot.busStatus":
            return copy.deepcopy(self.bus)
        r = self.replies.get(method)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(params)
        if r is not None:
            return copy.deepcopy(r)
        if method == "robot.enable":
            # 真的开/关策略：Studio 的「开始走」回读 busStatus 看 policy_enabled，假的也要跟着变
            on = bool((params or {}).get("on"))
            with self.lk:
                self.bus["policy_enabled"] = on
            return {"accepted": True}
        if method in ("robot.init", "robot.do", "robot.sound", "robot.stop"):
            return {"accepted": True}
        raise RpcError(method, -32601, "nope")

    def methods(self):
        with self.lk:
            return [m for m, _ in self.calls if m != "robot.busStatus"]


class PadTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Log(Path(self.tmp.name) / "logs", echo=False, background=False)
        self.cfg, self.bt, self.tap, self.robot = FakeConfigd(), FakeBt(), FakeTap(), FakeRobot()
        self.helper = FakeHelper()
        self.toml = "[netpad]\nenabled = false\n"
        self.weird = False
        self.presses = 0
        self.ops = P.PadOps(self.log, configd=self.cfg, bt=self.bt, tap=self.tap, robot=self.robot, helper=self.helper,
                            waits=FAST, read_text=lambda p: self.toml, exists=lambda p: self.weird,
                            state_dir=str(Path(self.tmp.name) / "state"))
        self.robot_ok()
        # Studio 第一次看到主程序在：后台线程发一次 robot.pose {"active": false}（三 V1）。等它发完再清空，别的测试从干净的开始
        wait_until(lambda: self.ops._pose_reset == "done", what="连上主程序时的 pose 复位")
        with self.robot.lk:
            self.robot.sent.clear()

    def tearDown(self):
        self.ops.close()
        self.tmp.cleanup()

    def robot_ok(self, torque_on=True, **kw):
        """喂一份 robotd 回读（app 那边 robot_view 的格式），同时让假 robotd 的 busStatus 回一样的。"""
        view = {"present": True, "bus": bus(torque_on), "bus_at": time.monotonic(), "estop_active": False, "paused": None,
                "estop_presses": self.presses}
        view.update(kw)
        if isinstance(view.get("bus"), dict):
            self.robot.bus = copy.deepcopy(view["bus"])
        self.ops.feed_robot(view)

    def relax_pressed(self):
        """顶上的「卸力」被按了一次（robotd.py 的 estop.press_count +1）。"""
        self.presses += 1
        self.robot_ok(torque_on=False)

    def wiz(self):
        return self.ops.snapshot()["wizard"]

    def steps(self):
        return {s["key"]: s["state"] for s in self.wiz()["steps"]}

    def wait_wiz(self, state, timeout=4.0):
        wait_until(lambda: self.wiz()["state"] == state, timeout, f"向导变成 {state}（现在 {self.wiz()['state']} · {self.wiz().get('error')}）")
        return self.wiz()

    def watch(self):
        self.ops.handle({"op": "watch"})
        wait_until(lambda: self.tap.running, what="旁路打开")


# ---------------- 搜索 ----------------

class ScanTest(PadTestBase):
    def test_scan_lists_pads_first_and_merges_two_faces(self):
        self.bt.found = [
            {"mac": "AA:BB:CC:00:11:22", "name": "Galaxy Buds", "icon": "audio-headset", "rssi": -70},
            {"mac": PRO_LE, "name": "BLE Controller_280609", "rssi": -55},
            {"mac": PRO, "name": "Pro Controller", "cls": 0x2508, "rssi": -50},
        ]
        self.ops.handle({"op": "scan", "seconds": 0.2})
        w = self.wait_wiz("waiting")
        self.assertEqual(w["phase"], "pick")
        macs = [d["mac"] for d in w["devices"]]
        self.assertEqual(macs[0], PRO)                    # 手柄在前
        self.assertNotIn(PRO_LE, macs)                     # 同一个手柄的 LE 脸合并掉
        buds = next(d for d in w["devices"] if d["mac"] == "AA:BB:CC:00:11:22")
        self.assertFalse(buds["pad"])
        self.assertEqual(buds["why_not"], "像是耳机")
        self.assertEqual(w["devices"][0]["kind"], "switch")
        self.assertEqual(w["picked"], PRO)                 # 只有一个在配对状态：推荐它
        self.assertEqual(self.steps(), {"ready": "ok", "scan": "ok", "pick": "doing"})
        self.assertIn("scan_stop", self.bt.calls)
        self.assertIn("close", self.bt.calls)              # 审查 5：搜完就关掉 bluetoothctl 会话
        self.assertIsNone(w["error"])

    def test_scan_with_nothing_says_put_pad_in_pairing_mode(self):
        self.ops.handle({"op": "scan", "seconds": 0.1})
        w = self.wait_wiz("waiting")
        self.assertEqual(w["error"]["code"], "not_found")
        self.assertIn("快闪", w["error"]["fix"])

    def test_adapter_problems(self):
        self.bt.state["blocked"] = True
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "rfkill")
        self.bt.state.update(blocked=False, present=False)
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "no_adapter")
        self.assertEqual(self.steps()["ready"], "fail")
        # bluetoothd 没在跑：说 bluez_down，不说「等一分钟」（审查 17）
        self.bt.state = {"present": None, "error": "Waiting to connect to bluetoothd...", "code": "bluez_down"}
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "bluez_down")
        # 蓝牙关着：自动打开再继续；不可绑定：搜索时只提醒、不去改（配对时才临时打开）
        self.bt.state = {"present": True, "powered": False, "pairable": False, "blocked": False}
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.wait_wiz("waiting")
        self.assertIn("power_on", self.bt.calls)
        self.assertNotIn("pairable_on", self.bt.calls)
        ready = next(s for s in self.wiz()["steps"] if s["key"] == "ready")
        self.assertEqual(ready["state"], "ok")
        self.assertIn("可绑定", ready["note"])

    def test_scan_start_failure_is_explained(self):
        self.bt.scan_exc = P.BtError(P.classify("Failed to start discovery: org.bluez.Error.InProgress"), "Failed to start discovery: org.bluez.Error.InProgress")
        self.ops.handle({"op": "scan", "seconds": 0.1})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "busy")
        self.assertEqual(self.steps()["scan"], "fail")
        self.assertIn("close", self.bt.calls)

    def test_cancel_and_busy(self):
        self.ops.handle({"op": "scan", "seconds": 5})
        wait_until(lambda: self.steps().get("scan") == "doing", what="开始搜索")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "scan", "seconds": 5})          # 一次只做一个
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "pair", "mac": PRO})            # 搜索中「配这个」：后端拒（页面按钮也变灰，审查 18a）
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "wizard_reset"})
        t0 = time.monotonic()
        self.ops.handle({"op": "cancel"})
        w = self.wait_wiz("failed", timeout=1.0)
        self.assertEqual(w["error"]["code"], "cancelled")
        self.assertLess(time.monotonic() - t0, 1.0)
        self.ops.handle({"op": "wizard_reset"})
        self.assertEqual(self.wiz()["state"], "idle")

    def test_refused_while_walking(self):
        """审查 8：在走 / 策略在跑时不让搜索配对（搜索占射频，卡住的手柄会让鸭子照旧走）。"""
        self.robot_ok(posture="policy", bus=bus(policy_enabled=True))
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "scan", "seconds": 1})
        self.assertIn("先停下", str(cm.exception))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "pair"})


# ---------------- 配对 ----------------

class PairTest(PadTestBase):
    def paired_result(self, mac=PRO, name="Pro Controller", trusted=True, connected=True):
        return {"outcome": "paired", "pad": {"mac": mac, "name": name, "paired": True, "trusted": trusted, "connected": connected}}

    def test_pair_from_list_via_configd_then_padd_then_keys(self):
        self.bt.found = [{"mac": PRO, "name": "Pro Controller", "cls": 0x2508, "rssi": -50}]
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.wait_wiz("waiting")
        self.watch()
        self.cfg.pair_result = self.paired_result()
        self.cfg.on_pair = lambda params: self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.steps().get("padd") == "doing", what="等 padd")
        self.assertIn(("pad.pair", {"timeout_seconds": 1, "mac": PRO}), self.cfg.calls)
        self.assertIn("close", self.bt.calls)               # 先关掉我们搜索用的会话，别跟 configd 抢默认代理
        self.tap.attach(PRO_DEV)
        wait_until(lambda: self.wiz()["phase"] == "keys", what="进入按键测试")
        for code in (0x130, 0x131, 0x133, 0x134):
            self.tap.key(code, True)
            self.tap.key(code, False)
        self.tap.axis(0, 30000)
        self.tap.axis(3, -30000)
        w = self.wait_wiz("done")
        self.assertEqual(set(self.steps().values()), {"ok"})
        self.assertIn("configd", w["via"])
        self.assertEqual(w["result_name"], "Pro Controller")
        snap = self.ops.snapshot()
        self.assertEqual(snap["status"]["active"]["mac"], PRO)
        self.assertEqual(snap["live"]["device"]["kind"], "switch")
        json.dumps(snap, ensure_ascii=False)                # 能推给页面

    def test_pairable_is_turned_on_only_for_pairing_and_restored(self):
        """审查 5：「可绑定」只在配对时临时打开，配完（成不成都）改回去。"""
        self.bt.state["pairable"] = False
        self.bt.pairable_ok = True
        self.cfg.pair_result = {"outcome": "failed", "reason": "timeout", "detail": "the pad did not finish pairing"}
        self.ops.handle({"op": "pair", "mac": PRO})
        self.wait_wiz("failed")
        self.assertEqual([c for c in self.bt.calls if str(c).startswith("pairable")], ["pairable_on", "pairable_off"])

    def test_configd_failures_in_plain_words(self):
        cases = [
            ({"outcome": "failed", "reason": "not_found", "detail": "nothing at all turned up"}, "not_found"),
            ({"outcome": "failed", "reason": "not_found", "detail": "nothing that looks like a gamepad turned up. These were in range"}, "not_found_pad"),
            ({"outcome": "failed", "reason": "ambiguous", "detail": "more than one pad is in pairing mode: a, b"}, "ambiguous"),
            ({"outcome": "failed", "reason": "timeout", "detail": "the pad did not finish pairing"}, "timeout"),
            ({"outcome": "failed", "reason": "no_adapter", "detail": "no Bluetooth adapter."}, "no_adapter"),
            ({"outcome": "failed", "reason": "rejected", "detail": "org.bluez.Error.AuthenticationFailed"}, "auth_failed"),
            ({"outcome": "failed", "reason": "rejected", "detail": "org.bluez.Error.Failed: le-connection-abort-by-local"}, "le_abort"),
            ({"outcome": "failed", "reason": "rejected", "detail": "something new"}, "rejected"),
        ]
        for result, code in cases:
            with self.subTest(code=code, detail=result["detail"]):
                self.cfg.pair_result = result
                self.ops.handle({"op": "pair", "mac": XBOX})
                w = self.wait_wiz("failed")
                self.assertEqual(w["error"]["code"], code)
                self.assertEqual(self.steps()["pair"], "fail")
                self.assertTrue(w["error"]["msg"] and w["error"]["fix"])
                self.assertIn(result["detail"], w["error"]["raw"])
                self.assertEqual(w["retry"], {"op": "pair", "mac": XBOX})

    def test_configd_timeout_down_and_no_permission(self):
        self.cfg.pair_exc = Timeout("pad.pair 2 秒没回话")
        self.ops.handle({"op": "pair", "mac": XBOX})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "timeout")
        # configd 不在、也没 MAC：说 configd 没在跑
        self.cfg.pair_exc = Unreachable("/run/configd.sock 不在")
        self.ops.handle({"op": "pair"})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "configd_down")
        # 审查 17：socket 在但没权限连，不能说成「configd 不在」
        self.cfg.pair_exc = P.NoPermission("没权限连 unix:/run/configd.sock")
        self.ops.handle({"op": "pair"})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "socket_perm")
        self.assertIn("robot 组", w["error"]["fix"])

    def test_cancel_then_new_pair_while_configd_still_busy(self):
        """审查 10 / R7：点了「停下」，configd 的 pad.pair 还在跑：不放行新的配对、搜索。"""
        self.cfg.pair_delay = 0.6
        self.cfg.pair_result = {"outcome": "failed", "reason": "timeout", "detail": "x"}
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: any(m == "pad.pair" for m, _ in self.cfg.calls), what="configd 在配")
        self.ops.handle({"op": "cancel"})
        self.wait_wiz("failed")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "pair", "mac": PRO})
        self.assertIn("configd", str(cm.exception))
        self.assertIsNotNone(self.wiz()["configd_busy_s"])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "scan", "seconds": 1})
        wait_until(lambda: self.wiz()["configd_busy_s"] is None, what="configd 做完")
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.wait_wiz("waiting")

    def test_permission_denied_falls_back_to_one_bluetoothctl_session(self):
        self.watch()
        self.cfg.pair_exc = RpcError("pad.pair", 14, "uid 1000 / gid 1000 may not change this robot's configuration")
        self.bt.infos[PRO] = {"cls": 0x2508, "name": "Pro Controller"}
        self.bt.on_pair = lambda mac: self.cfg.connect_pad(mac, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.steps().get("padd") == "doing", what="等 padd")
        self.assertIn(("pair_device", PRO, True), self.bt.calls)       # 经典蓝牙的顺序
        self.tap.attach(PRO_DEV)
        wait_until(lambda: self.wiz()["phase"] == "keys", what="进入按键测试")
        self.assertIn("bluetoothctl", self.wiz()["via"])
        self.ops.handle({"op": "keys_done"})
        self.assertEqual(self.wiz()["state"], "done")

    def test_fallback_failure_and_auto_pick_without_permission(self):
        self.cfg.pair_exc = RpcError("pad.pair", 14, "may not change this robot's configuration")
        raw = "Failed to pair: org.bluez.Error.AuthenticationFailed"
        self.bt.pair_exc = P.BtError(P.classify(raw), raw)
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "auth_failed")
        self.assertIn("别的设备", w["error"]["fix"])
        # 不带 MAC（自动挑）又没权限：没法自己配，照实说没权限、给 SSH 的办法
        self.ops.handle({"op": "pair"})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "permission")
        self.assertIn("sudo robotctl pad pair", w["error"]["fix"])

    def test_connected_but_padd_never_opens_it(self):
        self.watch()
        self.cfg.pair_result = self.paired_result()
        self.cfg.on_pair = lambda params: self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "no_input")
        self.assertEqual(self.steps()["padd"], "fail")
        self.assertEqual(self.steps()["connect"], "ok")

    def test_padd_not_running(self):
        self.watch()
        self.cfg.pair_result = self.paired_result()

        def on_pair(params):
            self.cfg.connect_pad(PRO, "Pro Controller")
            self.cfg.status["driver"] = "failed"
        self.cfg.on_pair = on_pair
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "padd_down")

    def test_paired_but_never_connects_and_untrusted(self):
        self.cfg.pair_result = self.paired_result(trusted=False, connected=False)
        self.cfg.on_pair = lambda params: self.cfg.connect_pad(PRO, "Pro Controller", connected=False, trusted=False)
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "not_connected")
        self.assertEqual(self.steps()["trust"], "fail")

    def test_bad_mac_and_forget(self):
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "pair", "mac": "nope"})
        self.assertEqual(self.ops.handle({"op": "forget", "mac": PRO.lower()}), {"removed": True})
        # configd 不让删（不是 root）：改用 bluetoothctl remove
        self.cfg.forget_exc = RpcError("pad.forget", 14, "may not change")
        self.assertEqual(self.ops.handle({"op": "forget", "mac": PRO}), {"removed": True})
        self.assertIn(("remove", PRO), self.bt.calls)
        # 两边都不行：拒绝，并给出 SSH 的办法
        self.bt.removed = P.BtError("bt_permission", "org.freedesktop.DBus.Error.AccessDenied")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "forget", "mac": PRO})
        self.assertIn("bluetooth", str(cm.exception))

    def helper_paired(self, mac=PRO, name="Pro Controller", trusted=True, connected=True):
        pair = {"outcome": "paired",
                "pad": {"mac": mac, "name": name, "paired": True, "trusted": trusted, "connected": connected}}
        return (True, "paired", {"pair": pair, "stderr": ["paused btd while the pad bonds"], "stdout": json.dumps(pair)})

    def test_pair_via_helper_then_padd_then_keys(self):
        """主路：新版小助手跑官方 robotctl pad pair；不开「可绑定」，也不走 configd。"""
        self.watch()
        self.helper.is_available = True
        self.helper.pair_result = self.helper_paired()
        self.helper.on_pair = lambda mac: self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO}, by="电脑 127.0.0.1")
        wait_until(lambda: self.steps().get("padd") == "doing", what="等 padd")
        self.assertEqual(self.helper.pair_calls[0][:2], ("电脑 127.0.0.1", PRO))
        w = self.wiz()
        self.assertIn("小助手", w["via"])
        self.assertIn("robotctl pad pair", w["via"])
        self.assertIs(w["helper_available"], True)
        self.assertEqual([c for c in self.cfg.calls if c[0] == "pad.pair"], [])
        self.assertNotIn("pairable_on", self.bt.calls)
        self.assertNotIn("pairable_off", self.bt.calls)
        self.tap.attach(PRO_DEV)
        wait_until(lambda: self.wiz()["phase"] == "keys", what="进入按键测试")
        self.ops.handle({"op": "keys_done"})
        self.assertEqual(self.wiz()["state"], "done")

    def recovery_pair(self, connected=True):
        self.watch()
        self.ops.w.update(reconnect_after=0.03, reconnect_interval=0.03, reconnect_timeout=0.05)
        self.helper.is_available = True
        self.helper.pair_result = self.helper_paired(connected=connected)
        self.helper.on_pair = lambda mac: self.cfg.connect_pad(PRO, "Pro Controller", connected=False)

    def reconnect_ready(self, mac):
        self.cfg.connect_pad(mac, "Pro Controller")
        self.tap.attach(PRO_DEV)

    def test_helper_stale_connected_recovers_until_matching_input_is_ready(self):
        """主路配完回过 Connected，但链路已掉；补一次 connect 后才有 event/attached。"""
        self.recovery_pair()
        self.bt.on_reconnect = self.reconnect_ready
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
        self.assertEqual(self.wiz()["phase"], "keys", self.wiz())
        self.assertEqual([x[1] for x in self.bt.calls if isinstance(x, tuple) and x[0] == "reconnect"], [PRO])
        self.assertEqual(self.helper.forget_calls, [])
        self.assertFalse(set(self.robot.methods()) - {"pad.bindings"})

    def test_helper_bonded_but_disconnected_also_recovers(self):
        self.recovery_pair(connected=False)
        self.bt.on_reconnect = self.reconnect_ready
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
        self.assertEqual(self.wiz()["phase"], "keys", self.wiz())

    def test_matching_input_wins_over_stale_disconnected_configd(self):
        self.recovery_pair(connected=False)
        self.bt.on_reconnect = lambda mac: self.tap.attach(PRO_DEV)
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
        self.assertEqual(self.wiz()["phase"], "keys", self.wiz())
        self.assertEqual(len([x for x in self.bt.calls if isinstance(x, tuple) and x[0] == "reconnect"]), 1)

    def test_matching_input_received_during_last_reconnect_is_not_lost_at_deadline(self):
        self.recovery_pair()
        clock, offset = self.ops.clock, [0]
        self.ops.clock = lambda: clock() + offset[0]
        def ready_then_timeout(mac):
            self.tap.attach(PRO_DEV)
            offset[0] += 1.0
        self.bt.on_reconnect = ready_then_timeout
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
        self.assertEqual(self.wiz()["phase"], "keys", self.wiz())

    def test_reconnect_attempts_are_bounded_and_do_not_count_as_input_success(self):
        self.recovery_pair()
        self.helper.on_pair = lambda mac: self.cfg.connect_pad(PRO, "Pro Controller")
        self.bt.reconnect_errors = [P.BtError("busy", "br-connection-busy")] * 5
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "no_input")
        calls = [x for x in self.bt.calls if isinstance(x, tuple) and x[0] == "reconnect"]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(0 < x[2] <= 0.05 for x in calls))

    def test_cancel_while_reconnecting_cannot_advance_or_retry(self):
        self.recovery_pair()
        def cancel(mac):
            self.ops.handle({"op": "cancel"})
            self.reconnect_ready(mac)
        self.bt.on_reconnect = cancel
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "cancelled")
        self.assertEqual(len([x for x in self.bt.calls if isinstance(x, tuple) and x[0] == "reconnect"]), 1)

    def test_cancel_on_final_connection_deadline_keeps_cancelled_reason(self):
        self.recovery_pair(connected=False)
        clock, offset = self.ops.clock, [0]
        self.ops.clock = lambda: clock() + offset[0]
        def cancel_then_timeout(mac):
            self.ops.handle({"op": "cancel"})
            offset[0] += 1.0
            raise P.BtError("timeout", "connect deadline")
        self.bt.on_reconnect = cancel_then_timeout
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        wait_until(lambda: not self.ops._job[1].is_alive())
        self.assertEqual(self.wiz()["error"]["code"], "cancelled", w)

    def test_another_attached_controller_is_not_pairing_success_or_reconnect_target(self):
        self.recovery_pair()
        self.tap.attach(XBOX_DEV)
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "wrong_input")
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "reconnect" for x in self.bt.calls))

    def test_driver_failure_while_disconnected_does_not_start_reconnect(self):
        self.recovery_pair(connected=False)
        def failed(mac):
            self.cfg.connect_pad(mac, "Pro Controller", connected=False)
            self.cfg.status["driver"] = "failed"
        self.helper.on_pair = failed
        self.ops.handle({"op": "pair", "mac": PRO})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "padd_down")
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "reconnect" for x in self.bt.calls))

    def test_policy_start_while_waiting_does_not_start_reconnect(self):
        self.recovery_pair()
        def driving(mac):
            self.cfg.connect_pad(mac, "Pro Controller", connected=False)
            self.robot_ok(posture="policy", bus=bus(policy_enabled=True))
        self.helper.on_pair = driving
        self.ops.handle({"op": "pair", "mac": PRO})
        self.assertEqual(self.wait_wiz("failed")["error"]["code"], "driving")
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "reconnect" for x in self.bt.calls))

    def test_unavailable_tap_keeps_explicit_unverified_compatibility_path(self):
        self.recovery_pair()
        def no_tap(mac):
            self.cfg.connect_pad(mac, "Pro Controller")
            self.tap.on_state("denied", "permission")
        self.helper.on_pair = no_tap
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
        self.assertEqual(self.wiz()["phase"], "keys", self.wiz())
        padd = next(s for s in self.wiz()["steps"] if s["key"] == "padd")
        self.assertIn("没法再核对", padd["note"])
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "reconnect" for x in self.bt.calls))

    def test_controller_arriving_during_unverified_fallback_is_checked_again(self):
        for device, expected in ((XBOX_DEV, "failed"), (PRO_DEV, "waiting")):
            with self.subTest(device=device["name"]):
                self.ops.handle({"op": "wizard_reset"})
                self.ops._tap_report({"report": "detached", "why": "test"})
                self.recovery_pair()
                def no_tap(mac):
                    self.cfg.connect_pad(mac, "Pro Controller")
                    self.tap.on_state("denied", "permission")
                self.helper.on_pair = no_tap
                step = self.ops._step
                def late_input(jid, key, state, note=None):
                    step(jid, key, state, note)
                    if key == "padd" and state == "ok" and "没法再核对" in (note or ""):
                        self.tap.attach(device)
                with mock.patch.object(self.ops, "_step", side_effect=late_input):
                    self.ops.handle({"op": "pair", "mac": PRO})
                    wait_until(lambda: self.wiz()["phase"] == "keys" or self.wiz()["state"] == "failed")
                w = self.wiz()
                self.assertEqual(w["state"], expected, w)
                if expected == "failed":
                    self.assertEqual(w["error"]["code"], "wrong_input")
                else:
                    padd = next(s for s in w["steps"] if s["key"] == "padd")
                    self.assertEqual(padd["note"], PRO_DEV["name"])

    def test_matching_late_input_clears_readiness_failure_but_not_cancel(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.ops.handle({"op": "wizard_reset"})
                self.ops._tap_report({"report": "detached", "why": "test"})
                self.recovery_pair()
                self.ops.handle({"op": "pair", "mac": PRO})
                self.wait_wiz("failed")
                if cancelled:
                    self.ops.handle({"op": "cancel"})
                self.tap.attach(PRO_DEV)
                w = self.wiz()
                if cancelled:
                    self.assertEqual(w["state"], "failed")
                else:
                    self.assertEqual(w["phase"], "keys", w)
                    self.assertIsNone(w["error"])
                    self.assertEqual(self.steps()["connect"], "ok")
                    self.assertEqual(self.steps()["padd"], "ok")
                self.ops._tap_report({"report": "detached", "why": "test"})

    def test_helper_failures_in_plain_words(self):
        cases = [
            ("not_found", "nothing that looks like a gamepad turned up", "not_found"),
            ("ambiguous", "more than one pad is in pairing state", "ambiguous"),
            ("timeout", "the pad did not finish pairing", "timeout"),
            ("no_adapter", "no Bluetooth adapter", "no_adapter"),
            ("other", "something new", "other"),
        ]
        self.helper.is_available = True
        for reason, detail, code in cases:
            with self.subTest(reason=reason):
                self.helper.pair_result = (False, "pair_failed", {
                    "reason": reason,
                    "pair": {"outcome": "failed", "reason": reason, "detail": detail},
                    "stderr": ["warning: could not power-cycle the Bluetooth adapter"],
                    "msg": f"robotctl 说配对失败：{reason}",
                })
                self.ops.handle({"op": "pair", "mac": XBOX})
                w = self.wait_wiz("failed")
                self.assertEqual(w["error"]["code"], code)
                self.assertIn(detail, w["error"]["raw"])
                self.assertIn("could not power-cycle", w["error"]["raw"])
                self.assertEqual(w["retry"], {"op": "pair", "mac": XBOX})

    def test_helper_process_timeout_is_reported(self):
        self.helper.is_available = True
        self.helper.pair_result = (False, "timeout", {"msg": "robotctl pad pair 超过 90 秒，已经杀掉", "stderr": []})
        self.ops.handle({"op": "pair", "mac": PRO})
        w = self.wait_wiz("failed")
        self.assertEqual(w["error"]["code"], "timeout")
        self.assertIn("超", w["error"]["raw"] + w["error"]["msg"])

    def test_old_helper_bad_verb_falls_back_to_configd(self):
        self.watch()
        self.helper.is_available = True
        self.helper.pair_result = (False, "bad_verb", {"msg": "只认这几个动词"})
        self.cfg.pair_result = self.paired_result()
        self.cfg.on_pair = lambda params: self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.steps().get("padd") == "doing", what="后备路等 padd")
        self.assertIn(("pad.pair", {"timeout_seconds": 1, "mac": PRO}), self.cfg.calls)
        self.assertIs(self.wiz()["helper_available"], False)
        self.assertIn("configd", self.wiz()["via"])
        self.tap.attach(PRO_DEV)
        wait_until(lambda: self.wiz()["phase"] == "keys", what="进入按键测试")

    def test_fallback_agent_failure_reopens_session_once(self):
        self.cfg.pair_exc = RpcError("pad.pair", 14, "may not change this robot's configuration")
        self.bt.infos[PRO] = {"cls": 0x2508, "name": "Pro Controller"}
        self.bt.pair_excs = [P.BtError("agent", "Failed to register agent object")]
        self.bt.on_pair = lambda mac: self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "pair", "mac": PRO})
        wait_until(lambda: self.steps().get("padd") == "doing", what="重试后等 padd")
        pairs = [c for c in self.bt.calls if isinstance(c, tuple) and c[:2] == ("pair_device", PRO)]
        self.assertEqual(len(pairs), 2)
        self.assertIn("close", self.bt.calls)
        self.tap.attach(PRO_DEV)
        wait_until(lambda: self.wiz()["phase"] == "keys", what="进入按键测试")

    def test_padd_wait_default_is_30_seconds(self):
        self.assertEqual(P.PADD_WAIT_S, 30)
        self.assertEqual(self.ops.w["padd"], FAST["padd"])       # 测试用 waits 改短，不真的等 30 秒

    def test_forget_via_helper_and_old_helper_falls_back(self):
        self.helper.is_available = True
        self.helper.forget_result = (True, "forgotten", {"removed": True})
        self.assertEqual(self.ops.handle({"op": "forget", "mac": PRO}, by="电脑"), {"removed": True})
        self.assertEqual(self.helper.forget_calls[0][:2], ("电脑", PRO))
        self.assertEqual([c for c in self.cfg.calls if c[0] == "pad.forget"], [])
        # 旧版小助手回 bad_verb：照旧 configd → bluetoothctl
        self.helper.forget_result = (False, "bad_verb", {"msg": "只认这几个动词"})
        self.assertEqual(self.ops.handle({"op": "forget", "mac": PRO}), {"removed": True})
        self.assertIn(("pad.forget", {"mac": PRO}), self.cfg.calls)
        self.assertIs(self.wiz()["helper_available"], False)

    def test_forget_connected_pad_while_walking_is_refused(self):
        self.cfg.connect_pad(PRO, "Pro Controller")
        self.ops.handle({"op": "refresh"})
        self.robot_ok(posture="walking")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "forget", "mac": PRO})
        self.assertIn("停下", str(cm.exception))


# ---------------- 急停 ----------------

class EStopTest(PadTestBase):
    def ready(self):
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.watch()
        self.tap.attach(XBOX_DEV)
        wait_until(lambda: self.ops.snapshot()["estop"]["ok"], what="急停四条都 ✓")

    def state(self):
        return self.ops.snapshot()["estop_test"]["state"]

    def start_armed(self):
        self.ready()
        self.ops.handle({"op": "estop_test", "action": "start"})
        wait_until(lambda: self.state() == "armed", what="确认上着力、等按 Select")

    def press_select(self):
        self.tap.key(0x13A, True)
        self.assertEqual(self.state(), "pressed")
        wait_until(lambda: self.ops.test.get("_on_while_pressed") is not None or self.ops.test.get("_press_err"),
                   what="按着的时候问过一次扭矩")

    def test_checks(self):
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        es = self.ops.snapshot()["estop"]
        self.assertTrue(es["ok"])
        self.assertEqual(es["state"], "ok")
        self.assertEqual([c["key"] for c in es["checks"]], ["pad", "padd", "robotd", "netpad"])
        self.toml = "[netpad]\nenabled = true\nport = 4210\n"
        self.ops._refresh_files()
        es = self.ops.snapshot()["estop"]
        self.assertFalse(es["ok"])
        self.assertEqual(es["state"], "bad")
        self.assertFalse(next(c for c in es["checks"] if c["key"] == "netpad")["ok"])
        # 审查 7：读不到 robotd.toml 不当作「没开」：总评不给 ✓，写「没核对」
        self.toml = None
        self.ops._refresh_files()
        es = self.ops.snapshot()["estop"]
        self.assertFalse(es["ok"])
        self.assertEqual(es["state"], "unknown")
        self.assertIn("没核对", next(c for c in es["checks"] if c["key"] == "netpad")["why"])
        self.ops.read_text = lambda p: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
        self.ops._refresh_files()
        es = self.ops.snapshot()["estop"]
        self.assertEqual(es["state"], "unknown")
        self.assertIn("没权限", next(c for c in es["checks"] if c["key"] == "netpad")["why"])
        # 文件不在：robotd 全用默认，netpad 默认关
        self.ops.read_text = lambda p: (_ for _ in ()).throw(FileNotFoundError(2, "No such file"))
        self.ops._refresh_files()
        self.assertTrue(self.ops.snapshot()["estop"]["ok"])
        # 主程序不在 / 调试模式：不算有效
        self.robot_ok(present=False)
        self.assertFalse(self.ops.snapshot()["estop"]["ok"])
        self.robot_ok(bus=dict(bus(), mode="commissioning"))
        self.assertFalse(self.ops.snapshot()["estop"]["ok"])
        self.assertEqual(self.ops.estop_ok(), False)        # 审查 23 的轻量版跟 snapshot 一致

    def test_tap_is_evidence_when_configd_unreadable(self):
        """审查 16：第一台平时关着 configd；旁路看到 attached 就当「手柄连着、padd 在跑」的证据。"""
        self.cfg.status_exc = Unreachable("/run/configd.sock 不在")
        self.watch()
        self.tap.attach(XBOX_DEV)
        es = self.ops.snapshot()["estop"]
        by = {c["key"]: c for c in es["checks"]}
        self.assertTrue(by["pad"]["ok"])
        self.assertIn("旁路", by["pad"]["why"])
        self.assertTrue(by["padd"]["ok"])
        self.assertTrue(es["ok"])

    def test_connected_but_no_input_device_is_not_effective(self):
        self.cfg.connect_pad(PRO, "Pro Controller")
        self.watch()
        self.ops._attach_wait_since = time.monotonic() - 10       # 订阅了 10 秒，padd 一直没打开它
        es = self.ops.snapshot()["estop"]
        self.assertFalse(es["ok"])
        self.assertFalse(next(c for c in es["checks"] if c["key"] == "input")["ok"])

    def test_passes_only_with_select_seen_and_torque_read_back_off(self):
        self.start_armed()
        self.press_select()
        self.assertIs(self.ops.test["_on_while_pressed"], True)
        self.tap.key(0x13A, False)
        self.assertEqual(self.state(), "released")
        time.sleep(0.05)
        self.robot_ok(torque_on=False)                          # padd 发了 relax：回读扭矩确认 OFF
        wait_until(lambda: self.state() == "passed", what="通过")
        t = self.ops.snapshot()["estop_test"]
        self.assertIsInstance(t["ms"], int)
        self.assertNotIn("_released_at", t)                     # 内部字段不推给页面

    def test_r1_torque_already_off_before_select_is_not_a_pass(self):
        """审查 1 / R1：顶上「卸力」先确认完了（estop_active 已回 False）、或者摔倒自动卸力，再按 Select：不能打勾。"""
        self.start_armed()
        self.robot_ok(torque_on=False, estop_active=False)      # 扭矩在按 Select 之前就关了
        wait_until(lambda: self.state() == "failed", what="还没按就判没通过")
        self.assertEqual(self.ops.snapshot()["estop_test"]["failed_at"], "early_off")
        self.tap.key(0x13A, True)                               # 之后再按 Select、回读 OFF：也翻不成通过
        self.tap.key(0x13A, False)
        self.robot_ok(torque_on=False)
        time.sleep(0.1)
        self.assertEqual(self.state(), "failed")

    def test_r1b_top_relax_pressed_during_test_is_not_a_pass(self):
        """测的时候顶上的「卸力」也按了（press_count 变了）：哪怕之后回读 OFF 也不算。"""
        self.start_armed()
        self.press_select()
        self.tap.key(0x13A, False)
        self.relax_pressed()
        wait_until(lambda: self.state() == "failed", what="没通过")
        self.assertEqual(self.ops.snapshot()["estop_test"]["failed_at"], "relax")

    def test_r2_long_hold_is_shutdown_not_estop(self):
        """审查 1 / R2：按住满 2 秒 padd 发的是 robot.shutdown（坐下关机），直接判失败。"""
        self.start_armed()
        self.press_select()
        wait_until(lambda: self.ops.snapshot()["estop_test"]["held_long"], what="按太久的警告")
        wait_until(lambda: self.state() == "failed", what="按满了直接判失败")
        t = self.ops.snapshot()["estop_test"]
        self.assertEqual(t["failed_at"], "long")
        self.assertIn("shutdown", t["why"])
        self.tap.key(0x13A, False)
        self.robot_ok(torque_on=False)
        time.sleep(0.1)
        self.assertEqual(self.state(), "failed")                # 松开后回读 OFF 也不翻成通过

    def test_off_readback_asked_before_release_does_not_count(self):
        """只认「松开之后才问的」回读：松开前就问出 OFF（按着的时候扭矩就关了）判失败。"""
        self.start_armed()
        self.robot.bus = bus(False)                             # 按下的那一刻 robotd 已经回 OFF
        self.tap.key(0x13A, True)
        wait_until(lambda: self.ops.test.get("_on_while_pressed") is not None or self.state() == "failed", what="按着时问过")
        self.tap.key(0x13A, False)
        wait_until(lambda: self.state() == "failed", what="没通过")
        self.assertIn(self.ops.snapshot()["estop_test"]["failed_at"], ("pressed_off", "early_off"))

    def test_fails_when_readback_never_shows_torque_off(self):
        self.start_armed()
        self.press_select()
        self.tap.key(0x13A, False)
        wait_until(lambda: self.state() == "failed", what="没通过")
        t = self.ops.snapshot()["estop_test"]
        self.assertEqual(t["failed_at"], "readback")
        self.assertIn("顶上的「卸力」", t["fix"])

    def test_waits_for_torque_then_times_out_without_select(self):
        self.ready()
        self.robot_ok(torque_on=False)                          # 软着：先等它站起来
        self.ops.handle({"op": "estop_test", "action": "start"})
        self.assertEqual(self.state(), "need_torque")
        time.sleep(0.05)
        self.robot_ok(torque_on=True)
        wait_until(lambda: self.state() == "armed", what="上力了")
        wait_until(lambda: self.state() == "failed", what="没按 Select")
        self.assertEqual(self.ops.snapshot()["estop_test"]["failed_at"], "press")

    def test_refused_when_path_is_broken(self):
        self.cfg.status["driver"] = "failed"
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "estop_test", "action": "start"})
        self.assertIn("padd", str(cm.exception))


# ---------------- 网页遥控：摇杆 ----------------

class MoveTest(PadTestBase):
    def test_sends_official_fields_clamped(self):
        r = self.ops.handle({"op": "move", "vx": 1.0, "vy": -0.1, "vyaw": 9, "seq": 1})
        self.assertEqual(r["sent"], [0.3, -0.1, 1.5])
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.3, "vy": -0.1, "vyaw": 1.5}))
        self.assertEqual(self.ops.snapshot()["virtual"]["owner"], "web")
        # 审查 21：比 25 Hz 还密的不丢，等一下发最新的那个
        r = self.ops.handle({"op": "move", "vx": 0.1, "seq": 2})
        self.assertTrue(r["merged"])
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.1, "vy": 0.0, "vyaw": 0.0}))
        self.ops.handle({"op": "stop", "seq": 3})
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))
        self.assertIsNone(self.ops.snapshot()["virtual"]["owner"])
        lim = self.ops.snapshot()["virtual"]["limits"]
        self.assertEqual((lim["v"], lim["w"]), (0.3, 1.5))
        self.assertIn("官方默认", lim["src"])

    def test_merging_keeps_only_the_newest(self):
        self.ops.handle({"op": "move", "vx": 0.1, "seq": 1})
        out = {}
        ts = [threading.Thread(target=lambda s=s: out.__setitem__(s, self.ops.handle({"op": "move", "vx": 0.1 * s, "seq": s})))
              for s in (2, 3)]
        for t in ts:
            t.start()
            time.sleep(0.005)
        for t in ts:
            t.join()
        self.assertIn("dropped", out[2])                     # 等的时候来了更新的：旧的不发
        self.assertEqual(self.robot.sent[-1][1]["vx"], 0.3)

    def test_r8_stale_move_after_stop_is_dropped(self):
        """审查 3 / R8：页面先发 move 再发 stop，to_thread 里 stop 先到：之后才到的旧 move 一律丢掉。"""
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 10})
        self.ops.handle({"op": "stop", "seq": 12})
        r = self.ops.handle({"op": "move", "vx": 0.3, "seq": 11})     # 比 stop 旧
        self.assertIn("dropped", r)
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))
        self.assertIsNone(self.ops.snapshot()["virtual"]["owner"])
        r = self.ops.handle({"op": "move", "vx": 0.1, "seq": 13})     # 新按下的照常
        self.assertEqual(r["sent"][0], 0.1)

    def test_owner_only_stop_fences_its_own_frames_without_stopping_another_driver(self):
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 1}, conn="a")
        before = list(self.robot.sent)
        result = self.ops.handle({"op": "stop", "seq": 2, "owned_only": True}, conn="b")
        self.assertIn("dropped", result)
        self.assertEqual(self.robot.sent, before)
        self.assertEqual(self.ops._seq_of("b")["stop"], 2)
        result = self.ops.handle({"op": "stop", "seq": 3, "owned_only": True}, conn="a")
        self.assertTrue(result["sent"])
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))

    def test_refused_cases(self):
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": float("nan")})
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": "fast"})
        self.robot_ok(present=False)
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.1})
        # 审查 14：主程序状态不明（不是运动模式、回读旧了）也不放行
        self.robot_ok(bus=bus(mode="weird"))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.1})
        self.robot_ok(bus_at=time.monotonic() - 10)
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.1})
        self.robot_ok()
        # 实体手柄连着：padd 每 0.1 秒发零速度，网页的会被盖掉 → 拒绝，页面做镜像
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "move", "vx": 0.1})
        self.assertIn("实体手柄", str(cm.exception))
        snap = self.ops.snapshot()["virtual"]
        self.assertEqual(snap["owner"], "pad")
        self.assertTrue(snap["mirror"])
        self.assertEqual(self.robot.sent, [])
        # 镜像时网页没在开：松手也不发 0（不去打断实体手柄：审查二 一般 2）
        self.assertEqual(self.ops.handle({"op": "stop"}), {"sent": False})
        self.assertEqual(self.robot.sent, [])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "drive", "on": True})        # 镜像时不接受打开网页摇杆

    def test_stale_pad_status_does_not_block_forever(self):
        """审查 15：configd 停了、状态一直停在「连着」：过了 15 秒不再当作实体手柄连着。"""
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        self.ops._status_at = time.monotonic() - 30
        self.assertEqual(self.ops.handle({"op": "move", "vx": 0.1})["sent"][0], 0.1)

    def test_relax_guard(self):
        """卸力后 2 秒内不接受摇杆（上力 / 卸力共用一个槽，后写的赢）。"""
        self.relax_pressed()
        self.robot_ok()
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "move", "vx": 0.1})
        self.assertIn("卸力", str(cm.exception))

    def test_send_failure_is_refused_not_retried(self):
        self.robot.fail = Unreachable("/run/robotd.sock 不在")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.1})

    def test_one_page_drives_at_a_time(self):
        """审查 19、25：同一时间只有一个页面能开；别的页面要点「接管」；拿着方向盘的页面断了马上停。"""
        self.ops.handle({"op": "move", "vx": 0.1, "seq": 1}, by="电脑 A", conn="a")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "move", "vx": 0.2, "seq": 1}, by="手机 B", conn="b")
        self.assertIn("电脑 A", str(cm.exception))
        self.assertIn("接管", str(cm.exception))
        snap_b = self.ops.snapshot(conn="b")["virtual"]["driver"]
        self.assertEqual((snap_b["by"], snap_b["mine"]), ("电脑 A", False))
        self.assertTrue(self.ops.snapshot(conn="a")["virtual"]["driver"]["mine"])
        self.ops.handle({"op": "drive", "on": True, "force": True}, by="手机 B", conn="b")
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))   # 接管先停
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.1, "seq": 2}, by="电脑 A", conn="a")
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 2}, by="手机 B", conn="b")
        self.ops.drop_client("b", "手机 B")
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))
        self.assertIsNone(self.ops.snapshot()["virtual"]["driver"])
        self.ops.handle({"op": "move", "vx": 0.1, "seq": 3}, by="电脑 A", conn="a")        # 空出来了就能开

    def test_speed_limits_from_padd_unit(self):
        """审查 24：padd.service 里改过满杆速度就用改过的；超过上限的按上限算。"""
        files = {D.PADD_UNIT: "[Service]\nExecStart=/opt/robot/daemon/current/bin/padd --max-linear 0.2 --max-angular=1.0\n"}
        self.ops.read_text = lambda p: files[p] if p in files else self.toml
        self.ops._refresh_files()
        self.assertEqual(self.ops.handle({"op": "move", "vx": 1, "vyaw": 5})["sent"], [0.2, 0.0, 1.0])
        files[D.PADD_UNIT] = "ExecStart=/x/padd --max-linear 5\n"
        self.ops._refresh_files()
        lim = self.ops.snapshot()["virtual"]["limits"]
        self.assertEqual(lim["v"], D.V_CEIL)
        self.assertIn("上限", lim["src"])
        # 写坏的数（审查二 I3）：不抛，跳过那个参数，页面写明
        files[D.PADD_UNIT] = "ExecStart=/x/padd --max-linear 1.2.3 --max-angular 1.0\n"
        self.ops._refresh_files()
        lim = self.ops.snapshot()["virtual"]["limits"]
        self.assertEqual((lim["v"], lim["w"]), (0.3, 1.0))
        self.assertIn("1.2.3", lim["src"])


# ---------------- 站起来 / 开始走 / 坐下 / 技能 / 声音 ----------------

class MotionTest(PadTestBase):
    def pose(self, posture, keep_guard=False, **kw):
        pol = dict(POLICIES, **kw.pop("pol", {}))
        torque = kw.pop("torque_on", posture not in ("limp",))
        b = kw.pop("bus", bus(torque, policy_enabled=posture in ("policy", "walking") or pol.get("sitting") is True,
                              homed=posture in ("holding", "policy", "walking", "sitting")))
        self.robot_ok(posture=posture, policies=pol, bus=b, **kw)
        if not keep_guard:
            self.ops._relax_at = -1e9          # 这些测试不测卸力保护：变软那一下不算

    def buttons(self):
        return self.ops.snapshot()["motion"]["buttons"]

    def test_stand_walk_sit_follow_the_official_table(self):
        self.pose("limp")
        b = self.buttons()
        self.assertTrue(b["stand"]["ok"])
        self.assertEqual(b["stand"]["call"], "robot.init")
        self.assertIn("站起来", b["walk"]["why"])
        self.assertIn("站起来", b["sit"]["why"])
        self.ops.handle({"op": "stand"}, by="电脑")
        self.assertEqual(self.robot.calls[-1], ("robot.init", {}))
        # 站好保持：能「开始走」；坐下要策略在跑（main.rs:4231-4235），先说清楚
        self.pose("holding")
        b = self.buttons()
        self.assertFalse(b["stand"]["ok"])
        self.assertTrue(b["walk"]["ok"])
        self.assertIn("开始走", b["sit"]["why"])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "sit"})
        self.ops.handle({"op": "walk"})
        # 「开始走」回执之后还会回读 busStatus 确认策略真的起来了，所以不能用 calls[-1]
        self.assertIn(("robot.enable", {"on": True, "toggle": False}),
                      [c for c in self.robot.calls if c[0] != "robot.busStatus"])   # 不用 toggle
        # 策略在跑：按钮变成「停下」；坐下 = sit_toggle
        self.pose("policy")
        b = self.buttons()
        self.assertEqual(b["walk"]["op"], "walk_stop")
        self.assertTrue(b["sit"]["ok"])
        self.ops.handle({"op": "sit"})
        self.assertEqual(self.robot.calls[-1], ("robot.do", {"skill": "sit_toggle"}))
        # 坐着：站起来也是 sit_toggle，不能发 init（lib.rs:2362-2370）
        self.pose("sitting", pol={"sitting": True})
        b = self.buttons()
        self.assertEqual(b["stand"]["call"], "robot.do sit_toggle")
        self.ops.handle({"op": "stand"})
        self.assertEqual(self.robot.calls[-1], ("robot.do", {"skill": "sit_toggle"}))
        log = self.log.recent(50, cats=["命令"])
        self.assertTrue(any("站起来" in e["msg"] and "成功" in e["msg"] for e in log))

    def test_walk_reports_the_real_reason_when_policy_is_refused(self):
        """robot.enable 只是收下请求：home_start_guard 拒了策略启动时，不能写「开始走 成功」。"""
        self.pose("holding")
        self.robot.replies["robot.enable"] = {"accepted": True}      # 回执说收下，策略并没启动
        self.robot.bus["last_enable_refusal"] = (
            "不能进入保持：正在确认实际 HOME 和直立姿态（连续 3 帧）；请回 HOME、扶正后重新按键")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "walk"})
        self.assertIn("不能进入保持", str(cm.exception))
        log = [e["msg"] for e in self.log.recent(50, cats=["命令"])]
        self.assertTrue(any("开始走" in m and "没成功" in m for m in log), log)
        self.assertFalse(any("开始走" in m and "成功" in m and "没成功" not in m for m in log), log)

    def test_walk_falls_back_to_home_start_guard_reason(self):
        self.pose("holding")
        self.robot.replies["robot.enable"] = {"accepted": True}
        self.robot.bus.pop("last_enable_refusal", None)
        self.robot.bus["home_start_guard"] = {"ready": False, "reason": "不能进入保持：舵机反馈不完整或不新鲜"}
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "walk"})
        self.assertIn("舵机反馈不完整", str(cm.exception))

    def test_walk_stop_zeros_speed_then_disables(self):
        """审查二 I5：真要等到速度归零才发 enable off（把等的那段删掉这条就过不了）。"""
        self.pose("walking", applied=[0.2, 0, 0])
        seen = {}

        def enable(params):
            seen["applied"] = list(self.ops._robot.get("applied") or [])
            seen["params"] = params
            seen["t"] = time.monotonic()
            return {"accepted": True}
        self.robot.replies["robot.enable"] = enable
        t0 = time.monotonic()
        threading.Timer(0.3, lambda: self.pose("walking", applied=[0.0, 0.0, 0.0])).start()
        self.ops.handle({"op": "walk_stop"})
        self.assertEqual(seen["applied"], [0.0, 0.0, 0.0])
        self.assertEqual(seen["params"], {"on": False, "toggle": False})
        self.assertGreaterEqual(seen["t"] - t0, 0.25)
        self.assertEqual(self.robot.methods()[-2:], ["robot.stop", "robot.enable"])

    def test_refusals_are_explained_and_broadcast(self):
        self.pose("policy")
        self.robot.replies["robot.do"] = {"accepted": False, "reason": "the policy is not driving — press Start on the pad"}
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "sit"}, by="手机")
        self.assertIn("开始走", str(cm.exception))
        last = self.ops.snapshot()["motion"]["last"]
        self.assertEqual((last["ok"], last["by"]), (False, "手机"))
        err = [e for e in self.log.recent(20, cats=["命令"]) if e["level"] == "error"]
        self.assertTrue(err and "the policy is not driving" in err[-1]["msg"])

    def test_blocked_states(self):
        for kw, word in (({"paused": "调舵机"}, "调舵机"), ({"present": False}, "没在跑"),
                         ({"bus": bus(mode="commissioning")}, "调试"), ({"bus": bus(phase="waiting_for_bus")}, "启动")):
            with self.subTest(word=word):
                self.pose("limp", **kw)
                with self.assertRaises(P.Refused) as cm:
                    self.ops.handle({"op": "stand"})
                self.assertIn(word, str(cm.exception))
        # 卸力后 2 秒内不让站起来（防止刚卸力又被顶起来）
        self.pose("limp")
        self.relax_pressed()
        self.pose("limp", keep_guard=True)
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "stand"})
        self.assertIn("卸力", str(cm.exception))

    def test_skills_from_policies(self):
        self.pose("policy")
        sk = {s["name"]: s for s in self.ops.snapshot()["motion"]["skills"]}
        self.assertEqual(list(sk), ["ground_pick", "roulade", "kick_left", "kick_right", "roller_stand"])
        self.assertTrue(sk["kick_left"]["ok"])
        self.assertIn("滑轮", sk["roller_stand"]["why"])       # 轮滑起身只在轮滑模式
        self.ops.handle({"op": "skill", "name": "kick_left"})
        self.assertEqual(self.robot.calls[-1], ("robot.do", {"skill": "kick_left"}))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "skill", "name": "fly"})
        self.pose("holding")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "skill", "name": "kick_left"})
        self.assertIn("开始走", str(cm.exception))

    def test_sound_works_even_when_limp_and_explains_no_voice(self):
        self.pose("limp")
        self.ops.handle({"op": "sound", "tag": "chirp"})
        self.assertEqual(self.robot.calls[-1], ("robot.sound", {"tag": "chirp"}))
        self.robot.replies["robot.sound"] = {"accepted": False, "reason": "this robot has no voice: audio is disabled"}
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "sound", "tag": "greet"})
        self.assertIn("没有声音", str(cm.exception))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "sound", "tag": "wheee"})       # 要一直按着发的，不做成一次性按钮

    def test_one_command_at_a_time(self):
        self.pose("limp")
        gate = threading.Event()
        self.robot.replies["robot.init"] = lambda p: (gate.wait(1), {"accepted": True})[1]
        t = threading.Thread(target=self.ops.handle, args=({"op": "stand"},))
        t.start()
        wait_until(lambda: self.ops.snapshot()["motion"]["busy"], what="第一个在路上")
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "stand"})
        self.assertIn("还没回话", str(cm.exception))
        gate.set()
        t.join()

    def test_pad_attached_still_allows_discrete_commands(self):
        """实体手柄连着：站 / 走 / 坐 / 技能是一次性的请求，跟 padd 不抢，照常能用；摇杆和动作不行。"""
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        self.pose("policy")
        self.ops.handle({"op": "skill", "name": "roulade"})
        acts = {a["id"]: a for a in self.ops.snapshot()["motion"]["actions"]}
        self.assertIn("实体手柄", acts["nod"]["why"])


# ---------------- 动作、组合动作 ----------------

class ActionTest(PadTestBase):
    def setUp(self):
        super().setUp()
        self.robot_ok(posture="policy", policies=POLICIES)

    def heads(self):
        return [p for m, p in self.robot.sent if m == "robot.head"]

    def keep_holding(self, stop, conn=None):
        while not stop.is_set():
            self.ops.handle({"op": "action_hold"}, conn=conn)
            self.robot_ok(posture="policy", policies=POLICIES)
            time.sleep(0.1)

    def test_hold_to_run_then_release_recenters_head(self):
        r = self.ops.handle({"op": "action_start", "id": "nod"}, by="电脑")
        self.assertGreater(r["seconds"], 0.5)
        stop = threading.Event()
        threading.Thread(target=self.keep_holding, args=(stop,), daemon=True).start()
        wait_until(lambda: len(self.heads()) > 3, what="在点头")
        self.assertTrue(any(abs(h["head_pitch"]) > 0.1 for h in self.heads()))
        stop.set()
        self.ops.handle({"op": "action_stop"})
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="停了")
        self.assertEqual(self.heads()[-1], {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0})
        self.assertIn("停了", self.ops.snapshot()["motion"]["last_action"]["result"])

    def test_page_stops_saying_hold_means_stop(self):
        """松手、切页、断网、转后台：页面不再说「还按着」，0.45 秒内停，嘴合上。"""
        self.ops.handle({"op": "action_start", "id": "mouth"})
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, timeout=2, what="自己停")
        mouths = [p for m, p in self.robot.sent if m == "robot.mouth"]
        self.assertTrue(mouths and mouths[-1] == {"open": 0.0})
        self.assertIn("没说", self.ops.snapshot()["motion"]["last_action"]["result"])

    def test_relax_and_pad_attach_interrupt(self):
        stop = threading.Event()
        self.ops.handle({"op": "action_start", "id": "hello"})
        threading.Thread(target=self.keep_holding, args=(stop,), daemon=True).start()
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is not None, what="在做")
        stop.set()
        self.presses += 1
        self.robot_ok(posture="policy", policies=POLICIES)          # 顶上按了卸力
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="卸力打断")
        self.assertIn("卸力", self.ops.snapshot()["motion"]["last_action"]["result"])
        self.assertEqual(self.robot.calls[0], ("robot.sound", {"tag": "greet"}))
        # 实体手柄连上：也打断
        time.sleep(FAST["relax_guard"] + 0.05)
        self.robot_ok(posture="policy", policies=POLICIES)
        stop = threading.Event()
        self.ops.handle({"op": "action_start", "id": "shake"})
        threading.Thread(target=self.keep_holding, args=(stop,), daemon=True).start()
        self.watch()
        self.tap.attach(XBOX_DEV)
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="手柄连上打断")
        stop.set()
        self.assertIn("实体手柄", self.ops.snapshot()["motion"]["last_action"]["result"])

    def test_actions_need_policy_and_one_at_a_time(self):
        self.ops.handle({"op": "action_start", "id": "nod"}, conn="a")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "action_start", "id": "shake"}, conn="a")
        self.ops.handle({"op": "action_stop"}, conn="a")
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="停了")
        self.robot_ok(posture="holding", policies=POLICIES)
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "action_start", "id": "nod"}, conn="a")
        self.assertIn("开始走", str(cm.exception))

    def test_user_combos_saved_loaded_and_validated(self):
        combo = {"id": "wave", "name": "挥挥手", "steps": [{"do": "sound", "tag": "coo"}, {"do": "nod", "times": 9},
                                                          {"do": "mouth", "open": 3, "ms": 50}]}
        self.ops.handle({"op": "combo_save", "combo": combo})
        saved = next(c for c in self.ops.snapshot()["motion"]["combos"] if c["id"] == "wave")
        self.assertFalse(saved["builtin"])
        self.assertIn("点头 4 次", saved["text"])             # 夹到范围里
        again = P.PadOps(self.log, robot=self.robot, state_dir=self.ops.state_dir)
        self.assertEqual([c["id"] for c in again._user_combos], ["wave"])
        for bad, word in (({"id": "hello", "name": "x", "steps": [{"do": "wait"}]}, "内置"),
                          ({"id": "w2", "name": "x", "steps": [{"do": "move", "vx": 1}]}, "不认识"),
                          ({"id": "w3", "name": "x", "steps": [{"do": "skill", "name": "sit_toggle"}]}, "坐下"),
                          ({"id": "W!", "name": "x", "steps": [{"do": "wait"}]}, "编号")):
            with self.subTest(word=word):
                with self.assertRaises(P.Refused) as cm:
                    self.ops.handle({"op": "combo_save", "combo": bad})
                self.assertIn(word, str(cm.exception))
        self.ops.handle({"op": "combo_delete", "id": "wave"})
        self.assertFalse(any(c["id"] == "wave" for c in self.ops.snapshot()["motion"]["combos"]))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "combo_delete", "id": "hello"})

    def test_broken_combo_file_is_skipped_with_a_log_line(self):
        path = Path(self.ops.state_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / C.COMBO_FILE).write_text('{"combos": [{"id": "ok1", "name": "好", "steps": [{"do": "nod"}]}, {"id": "bad"}]}',
                                         encoding="utf-8")
        ops = P.PadOps(self.log, robot=self.robot, state_dir=str(path))
        self.assertEqual([c["id"] for c in ops._user_combos], ["ok1"])
        self.assertTrue(any("跳过" in e["msg"] for e in self.log.recent(20, cats=["手柄"])))


class Review2Test(PadTestBase):
    """第二轮审查（padreview2 repro2.py Q1–Q8、repro3.py Q9）的反例。"""

    def feed(self, posture="policy", **kw):
        pol = kw.pop("pol", {})
        policies = None if pol is None else dict(POLICIES, **pol)
        b = kw.pop("bus", bus(kw.pop("torque_on", True), policy_enabled=posture in ("policy", "walking"), homed=True))
        self.robot_ok(posture=posture, policies=policies, bus=b, **kw)

    def test_q1_drive_off_then_late_move_is_dropped(self):
        """关掉网页摇杆 → stop(6) → 线程池里晚到的 move(5)：不发、拿不回方向盘；只有再 drive on 才解开。"""
        self.feed()
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 4}, by="A", conn="a")
        self.ops.handle({"op": "drive", "on": False}, by="A", conn="a")
        self.ops.handle({"op": "stop", "seq": 6}, by="A", conn="a")
        n = len(self.robot.sent)
        self.assertIn("dropped", self.ops.handle({"op": "move", "vx": 0.2, "seq": 5}, by="A", conn="a"))
        self.assertIn("dropped", self.ops.handle({"op": "move", "vx": 0.2, "seq": 7}, by="A", conn="a"))
        self.assertEqual(len(self.robot.sent), n)
        snap = self.ops.snapshot(conn="a")["virtual"]
        self.assertIsNone(snap["driver"])
        self.assertIsNone(snap["owner"])
        self.ops.handle({"op": "watch"}, conn="a")              # 开着页面不续方向盘
        self.assertIsNone(self.ops.snapshot(conn="a")["virtual"]["driver"])
        self.ops.handle({"op": "drive", "on": True}, by="A", conn="a")
        self.assertEqual(self.ops.handle({"op": "move", "vx": 0.1, "seq": 8}, by="A", conn="a")["sent"][0], 0.1)

    def test_q2_disconnect_then_late_move_is_dropped(self):
        """页面断了：先同步 mark_gone、再线程池 drop_client；之后晚到的 move 不发，别的页面马上能开。"""
        self.feed()
        self.assertTrue(self.ops.new_conn() != self.ops.new_conn())
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 7}, by="A", conn="a")
        self.assertTrue(self.ops.mark_gone("a"))
        self.assertIsNone(self.ops.snapshot()["virtual"]["driver"])      # 同步那一下就松开了
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "move", "vx": 0.25, "seq": 8}, by="A", conn="a")
        self.ops.drop_client("a", "A")
        self.assertEqual(self.robot.sent[-1], ("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}))
        self.assertIn("dropped", self.ops._move({"vx": 0.25, "seq": 9}, "a", "A"))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "drive", "on": True}, by="A", conn="a")   # 断开的永远拿不回
        self.assertEqual(self.ops.handle({"op": "move", "vx": 0.1, "seq": 1}, by="B", conn="b")["sent"][0], 0.1)

    def test_q3_walk_stop_from_any_page_and_while_busy(self):
        """「停下（站好）」不看方向盘、不排队；一次性的命令不拿方向盘。"""
        self.feed("holding")
        self.ops.handle({"op": "walk"}, by="手机 A", conn="a")
        self.assertIsNone(self.ops.snapshot()["virtual"]["driver"])
        self.feed("walking", applied=[0.0, 0.0, 0.0])
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 1}, by="手机 A", conn="a")     # A 正在推摇杆
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "skill", "name": "kick_left"}, by="电脑 B", conn="b")
        gate = threading.Event()
        self.robot.replies["robot.do"] = lambda p: (gate.wait(1), {"accepted": True})[1]
        t = threading.Thread(target=self.ops.handle, args=({"op": "skill", "name": "kick_left"}, "手机 A", "a"))
        t.start()
        wait_until(lambda: self.ops.snapshot()["motion"]["busy"], what="技能在路上")
        self.ops.handle({"op": "walk_stop"}, by="电脑 B", conn="b")                    # 别的页面、上一个还没回话：照样停
        self.assertEqual(self.robot.methods()[-2:], ["robot.stop", "robot.enable"])
        gate.set()
        t.join()

    def test_q4_halt_does_not_eat_the_next_move(self):
        self.feed()
        self.ops.handle({"op": "move", "vx": 0.2, "seq": 1}, conn="a")
        self.ops.handle({"op": "stop", "seq": 2}, conn="a")
        self.ops._halt("点了停下", "a")
        self.assertEqual(self.ops.handle({"op": "move", "vx": 0.2, "seq": 3}, conn="a")["sent"][0], 0.2)

    def test_q5_relax_guard_covers_everything_and_all_sources(self):
        """卸力保护：顶上按钮按下那一刻（note_relax）、手柄 Select / 摔倒（回读扭矩开→关）都算；挡 walk / sit / skill / 动作 / 摇杆。"""
        self.feed("policy")
        self.ops.note_relax("电脑")
        for args in ({"op": "sit"}, {"op": "skill", "name": "kick_left"}, {"op": "action_start", "id": "nod"},
                     {"op": "move", "vx": 0.1}):
            with self.subTest(op=args["op"]):
                with self.assertRaises(P.Refused) as cm:
                    self.ops.handle(args)
                self.assertIn("卸", str(cm.exception))
        self.ops.handle({"op": "sound", "tag": "chirp"})                 # 叫一声不动关节：照样能叫
        time.sleep(FAST["relax_guard"] + 0.05)
        self.feed("holding")
        self.ops.handle({"op": "walk"})
        self.ops._relax_at = -1e9
        self.feed("holding")
        self.feed("limp", torque_on=False)                                # 不经过 Studio 的卸力（手柄 Select、摔倒）
        b = self.ops.snapshot()["motion"]["buttons"]
        self.assertFalse(b["stand"]["ok"])
        self.assertIn("卸", b["stand"]["why"])
        self.feed("holding")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "walk"})

    def test_q6_seated_and_limp_is_blocked_with_a_clear_reason(self):
        self.feed("sitting", torque_on=False, pol={"sitting": True, "enabled": False})
        self.ops._relax_at = -1e9
        b = self.ops.snapshot()["motion"]["buttons"]
        self.assertFalse(b["stand"]["ok"])
        self.assertIn("锁存", b["stand"]["why"])
        self.assertIn("站起来", b["walk"]["why"])
        self.assertEqual(b["sit"]["why"], "已经坐着了")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "stand"})
        self.assertNotIn("robot.do", self.robot.methods())
        self.assertNotIn("robot.init", self.robot.methods())

    def test_q7_relax_interrupts_combo_recenters_and_stops_calling(self):
        stop = threading.Event()

        def hold():
            while not stop.is_set():
                self.ops.handle({"op": "action_hold"}, conn="a")
                time.sleep(0.05)
        self.feed()
        self.ops.handle({"op": "action_start", "id": "happy"}, conn="a")     # 叫一声 → 嘴一张一合 → 点头
        threading.Thread(target=hold, daemon=True).start()
        wait_until(lambda: any(m == "robot.mouth" for m, _ in self.robot.sent), what="在张嘴")
        self.ops.note_relax("电脑")
        n_calls = len(self.robot.calls)
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="打断")
        stop.set()
        self.assertIn("卸力", self.ops.snapshot()["motion"]["last_action"]["result"])
        self.assertEqual([m for m, _ in self.robot.calls[n_calls:] if m in ("robot.do", "robot.sound")], [])
        tail = {m: p for m, p in self.robot.sent[-2:]}
        self.assertEqual(tail.get("robot.mouth"), {"open": 0.0})
        self.assertEqual(tail.get("robot.head"), {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0})
        # 技能组合：第一脚踢完、等的时候卸力：第二脚不再发
        self.ops._relax_at = -1e9
        time.sleep(0.05)
        self.feed()
        stop = threading.Event()
        threading.Thread(target=hold, daemon=True).start()
        self.ops.handle({"op": "action_start", "id": "kicks"}, conn="a")
        wait_until(lambda: ("robot.do", {"skill": "kick_left"}) in self.robot.calls, what="第一脚")
        self.ops.note_relax("电脑")
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="打断")
        stop.set()
        self.assertNotIn(("robot.do", {"skill": "kick_right"}), self.robot.calls)

    def test_q8_mirror_sends_nothing(self):
        self.watch()
        self.tap.attach(XBOX_DEV)
        self.feed()
        for args in ({"op": "move", "vx": 0.1, "seq": 1}, {"op": "action_start", "id": "nod"}, {"op": "drive", "on": True}):
            with self.assertRaises(P.Refused):
                self.ops.handle(args, by="A", conn="a")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "drive", "on": True, "force": True}, by="B", conn="b")
        self.ops.mark_gone("b")
        self.ops.drop_client("b", "B")
        self.ops.handle({"op": "stop", "seq": 2}, conn="a")
        self.assertEqual(self.robot.sent, [])

    def test_q9_stale_policies_disable_sit_and_stand(self):
        from studio.state import posture
        b = bus(True, policy_enabled=True, homed=True)
        self.robot_ok(posture=posture(True, b, None, {"move": {"applied": [0, 0, 0]}}), policies=None, bus=b)
        btn = self.ops.snapshot()["motion"]["buttons"]
        for k in ("sit", "stand"):
            self.assertFalse(btn[k]["ok"])
            self.assertIn("是不是坐着", btn[k]["why"])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "sit"})
        b2 = bus(False)
        self.robot_ok(posture=posture(True, b2, None, None), policies=None, bus=b2)
        self.ops._relax_at = -1e9
        self.assertFalse(self.ops.snapshot()["motion"]["buttons"]["stand"]["ok"])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "stand"})
        self.assertEqual([m for m in self.robot.methods() if m in ("robot.do", "robot.init")], [])

    def test_walk_waits_for_pairing_and_unknown_pad_is_said(self):
        self.feed("holding")
        self.ops.handle({"op": "scan", "seconds": 2})
        wait_until(lambda: self.steps().get("scan") == "doing", what="在搜索")
        self.assertIn("配对", self.ops.snapshot()["motion"]["buttons"]["walk"]["why"])
        self.ops.handle({"op": "cancel"})
        self.wait_wiz("failed")
        # configd 读不到、按键也没权限读：页面写「认不出有没有实体手柄」
        self.cfg.status_exc = Unreachable("/run/configd.sock 不在")
        self.watch()
        self.tap.on_state("denied", "没权限连 unix:/run/padd/pad.sock")
        self.ops._status_at = time.monotonic() - 30                      # 前面搜索时问到的 pad.status 也过期了
        v = self.ops.snapshot()["virtual"]
        self.assertFalse(v["mirror"])
        self.assertIn("按键也读不到", v["pad_unknown"])

    def test_user_combo_with_roller_skill_follows_skill_rules(self):
        self.feed("policy")
        self.ops.handle({"op": "combo_save", "combo": {"id": "roll", "name": "滚起来", "steps": [{"do": "skill", "name": "roller_stand"}]}})
        row = next(c for c in self.ops.snapshot()["motion"]["combos"] if c["id"] == "roll")
        self.assertFalse(row["ok"])
        self.assertIn("轮滑", row["why"])
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "action_start", "id": "roll"})

    def test_safe_snapshot_and_masked_diag(self):
        self.bt.found = [{"mac": PRO, "name": "Pro Controller", "cls": 0x2508, "rssi": -50}]
        self.ops.handle({"op": "scan", "seconds": 0.1})
        self.wait_wiz("waiting")
        d = json.dumps(self.ops.diag(), ensure_ascii=False)
        self.assertNotIn(PRO, d)
        self.assertNotIn("Pro Controller", d)
        self.assertIn('"count": 1', d)
        with mock.patch.object(self.ops, "_motion_view", side_effect=ValueError("坏了")):
            self.assertIsNone(self.ops.safe_snapshot())
            self.assertIsNone(self.ops.safe_snapshot())
        errs = [e for e in self.log.recent(50, cats=["手柄"]) if "跳过这一帧" in e["msg"]]
        self.assertEqual(len(errs), 1)
        self.assertIs(self.ops.executor_for({"op": "move"}), self.ops._fast_pool)
        self.assertIsNone(self.ops.executor_for({"op": "pair"}))


HEAD0 = dict(D.HEAD_ZERO)
POSE_OFF = {"active": False}


class VpadTest(PadTestBase):
    """第三轮：网页手柄图本身能按（pad-vpad-plan.md §8，审查 pad-vpad-review.md V1–V20）。"""

    def feed(self, posture="policy", **kw):
        pol = kw.pop("pol", {})
        policies = None if pol is None else dict(POLICIES, **pol)
        b = kw.pop("bus", bus(kw.pop("torque_on", posture != "limp"), policy_enabled=posture in ("policy", "walking"),
                              homed=True))
        self.robot_ok(posture=posture, policies=policies, bus=b, **kw)

    def sent(self, method=None):
        with self.robot.lk:
            return [(m, p) for m, p in self.robot.sent if method is None or m == method]

    def poses(self):
        return [p for _, p in self.sent("robot.pose")]

    def heads(self):
        return [p for _, p in self.sent("robot.head")]

    def s(self):
        """页面的递增序号（一个页面只有一个计数，跟 pad.js 的 nextSeq 一样）。"""
        return next(self._n)

    def into(self, mode, conn="a", seq=None):
        return self.ops.handle({"op": "mode", "mode": mode, "seq": self.s() if seq is None else seq}, by="电脑 A", conn=conn)

    def stick(self, mode, conn="a", seq=None, **v):
        return self.ops.handle(dict({"op": "sticks", "mode": mode, "seq": self.s() if seq is None else seq,
                                     "lx": 0, "ly": 0, "rx": 0, "ry": 0}, **v), by="电脑 A", conn=conn)

    def stop(self, conn="a"):
        return self.ops.handle({"op": "stop", "seq": self.s()}, conn=conn)

    def body_latched(self, conn="a"):
        self.feed()
        self.into("body", conn)
        self.stick("body", conn, ly=1)
        self.assertEqual(self.poses()[-1]["active"], True)
        self.assertLess(self.poses()[-1]["z"], 0)

    def head_latched(self, conn="a"):
        self.feed()
        self.into("head", conn)
        self.stick("head", conn, lx=0.5)
        self.assertNotEqual(self.heads()[-1], HEAD0)

    def assert_no_empty_pose(self):
        # 坑：PoseParams 默认 active:true（fork lib.rs:2320-2346），发 {} 反而打开身体模式 —— 退出一定写 active:false
        for p in self.poses():
            self.assertIn("active", p)

    # ---- 换算（附 2） ----

    def test_formulas_follow_padd(self):
        lim = {"v": 0.3, "w": 1.5}
        m, p = D.sticks_to_intent("drive", 0, -1, 1, 0, lim, 2.5)        # 左摇杆推上、右摇杆推右
        self.assertEqual((m, p), ("robot.move", {"vx": 0.3, "vy": 0.0, "vyaw": -1.5}))
        self.assertEqual(D.sticks_to_intent("drive", 1, 0, 0, 0, lim, 2.5)[1]["vy"], -0.3)   # 推右 = 向右平移（vy 为负）
        self.assertEqual(D.sticks_to_intent("drive", 0.05, 0.09, 0, 0, lim, 2.5)[1], {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
        m, p = D.sticks_to_intent("head", 0.5, -1, 0.5, -1, lim, 2.5)
        self.assertEqual(m, "robot.head")
        self.assertEqual(p, {"neck_pitch": 2.5, "head_pitch": -2.5, "head_yaw": -1.25, "head_roll": 1.25})
        m, p = D.sticks_to_intent("body", 0, -1, 1, -1, lim, 2.5)
        self.assertEqual(m, "robot.pose")
        self.assertEqual(p, {"z": 0.01, "roll": 0.26, "pitch": 0.26, "active": True})   # 夹到训练范围 ±0.26
        self.assertEqual(D.sticks_to_intent("body", 0, 1, 0, 0, lim, 2.5)[1]["z"], -0.025)  # 往下推最多蹲 2.5 cm

    # ---- V1：头 / 身体锁存，每条退出路径都清 ----

    def test_v1_mode_exit_and_release(self):
        self.head_latched()
        self.stop()                                                           # 松手：头回正（还在头模式）
        self.assertEqual(self.heads()[-1], HEAD0)
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode"], "head")
        self.stick("head", ly=-1)
        self.into("drive")                                                    # 再按一次 Y：回到走路，头回正
        self.assertEqual(self.heads()[-1], HEAD0)
        self.into("body")
        self.stick("body", rx=1)
        self.stop()                                                           # 身体模式松手：回中，还在身体模式
        self.assertEqual(self.poses()[-1], dict(D.POSE_CENTER))
        self.into("drive")                                                    # 退出：一定是 active:false
        self.assertEqual(self.poses()[-1], POSE_OFF)
        self.assert_no_empty_pose()
        self.assertEqual(self.ops._latch, {"head": None, "body": None})

    def test_v1_leaving_page_and_disconnect(self):
        self.body_latched()
        self.ops.handle({"op": "drive", "on": False, "seq": self.s()}, conn="a")   # 离开手柄页
        self.assertEqual(self.poses()[-1], POSE_OFF)
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode"], "drive")
        # 断开：mark_gone 在事件循环里同步调，只改内存、不发东西；drop_client（线程池）里发
        self.ops.handle({"op": "drive", "open": True, "seq": self.s()}, conn="b")
        self.body_latched("b")
        n = len(self.sent())
        self.ops.mark_gone("b")
        self.assertEqual(len(self.sent()), n)
        self.ops.drop_client("b", "B")
        wait_until(lambda: self.poses()[-1] == POSE_OFF, what="断开后退出身体模式")
        self.assert_no_empty_pose()

    def test_v1_takeover_and_lease_timeout(self):
        self.body_latched("a")
        self.ops.handle({"op": "drive", "on": True, "force": True}, by="手机 B", conn="b")   # 接管
        self.assertEqual(self.poses()[-1], POSE_OFF)
        # 方向盘 6 秒没动静、别的页面推摇杆：原来那个页面置过的头也清掉（三 V1 第 4 条）
        self.ops.handle({"op": "drive", "on": False}, conn="b")
        # 看门狗（0.6 秒回中）、模式心跳超时（3 秒）都设大：只让「方向盘 6 秒没动静、换人」这一条路来清（审查 R7）
        with mock.patch.object(D, "LEASE_S", 0.2), mock.patch.object(D, "STICK_STALE_S", 60), \
                mock.patch.object(D, "MODE_HB_S", 60):
            self.head_latched("c")
            time.sleep(0.25)
            self.stick("drive", conn="d", ly=-1)
            wait_until(lambda: self.heads()[-1] == HEAD0, timeout=1.0, what="换人后头回正")
            v = self.ops.snapshot(conn="c")["virtual"]
        self.assertEqual(v["mode"], "drive")
        self.assertIn("方向盘 6 秒没动静", v["mode_exit"]["why"])

    def test_v1_relax_sets_a_flag_then_clears_after_the_relax(self):
        """卸力的入口在事件循环里：只置标志、不发；后台线程 0.1 秒后再发（排在卸力之后）。镜像时也清。"""
        self.body_latched()
        n = len(self.sent())
        t0 = time.monotonic()
        self.ops.note_relax("电脑")                                         # 顶上卸力、空格、图上 Select
        self.assertEqual(len(self.sent()), n)                               # 当场什么都没发
        wait_until(lambda: self.poses()[-1] == POSE_OFF, what="卸力后退出身体模式")
        self.assertGreaterEqual(time.monotonic() - t0, D.RELAX_CLEAR_DELAY_S - 0.02)
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode_exit"]["why"], "卸力了")
        # 回读看到扭矩开→关（手柄 Select、摔倒）：同样清
        self.ops._relax_at = -1e9
        self.feed()
        self.into("drive")                    # 页面跟着后端回到走路（回一句，后端才让再进模式：审查 R3）
        # 姿势一直是「准备好走」，只把扭矩从开变成关；看门狗设大：只让「回读看到扭矩关了」这一条路来清（审查 R7）
        with mock.patch.object(D, "STICK_STALE_S", 60):
            self.head_latched()
            self.feed("policy", torque_on=False)
            wait_until(lambda: self.heads()[-1] == HEAD0, timeout=1.0, what="回读卸力后头回正")
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode_exit"]["why"], "卸力了（回读看到的）")

    def test_v1_pad_attached_clears_even_in_mirror(self):
        self.body_latched()
        self.watch()
        self.tap.attach(XBOX_DEV)                                           # 实体手柄连上的那一刻
        wait_until(lambda: self.poses()[-1] == POSE_OFF, what="手柄连上后退出身体模式")
        self.assertTrue(self.ops.snapshot()["virtual"]["mirror"])
        with self.assertRaises(P.Refused):
            self.into("head")                                               # 镜像时网页上按的不收
        # configd 那条路（没有旁路）也算
        self.tap.on_report({"report": "detached", "why": "x"})
        self.into("drive")                    # 页面跟着后端回到走路
        self.head_latched()
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        wait_until(lambda: self.heads()[-1] == HEAD0, what="configd 说手柄连着后头回正")

    def test_v1_walk_stop_and_start_stop_clear(self):
        self.body_latched()
        self.ops.handle({"op": "walk_stop"}, by="手机 B", conn="b")         # 别的页面点「停下（站好）」
        self.assertEqual(self.poses()[-1], POSE_OFF)
        self.into("drive")                    # 页面跟着后端回到走路
        self.head_latched()
        self.ops.handle({"op": "start", "expect": "walk_stop", "src": "svg"}, conn="a")   # 图上 Start（在走 → 停下）
        self.assertEqual(self.heads()[-1], HEAD0)

    def test_v1_leaving_driving_exits_mode(self):
        self.body_latched()
        self.feed("holding")                                                # 策略停了
        wait_until(lambda: self.poses()[-1] == POSE_OFF, what="策略停了退出身体模式")
        v = self.ops.snapshot(conn="a")["virtual"]
        self.assertEqual(v["mode"], "drive")
        self.assertIn("策略停了", v["mode_exit"]["why"])

    def test_v1_close_and_startup_reset(self):
        self.head_latched()
        self.ops.close()
        self.assertEqual(self.heads()[-1], HEAD0)
        # Studio 第一次看到主程序在：发一次 robot.pose {"active": false}，只发一次（setUp 里等过、清掉了）
        robot = FakeRobot()
        ops = P.PadOps(self.log, robot=robot, waits=FAST, read_text=lambda p: self.toml, state_dir=self.tmp.name)
        try:
            self.assertEqual(robot.sent, [])
            ops.feed_robot({"present": True, "bus": bus(), "bus_at": time.monotonic()})
            wait_until(lambda: robot.sent == [("robot.pose", {"active": False})], what="连上时的复位")
            ops.feed_robot({"present": False})
            ops.feed_robot({"present": True, "bus": bus(), "bus_at": time.monotonic()})
            time.sleep(0.1)
            self.assertEqual(robot.sent, [("robot.pose", {"active": False})])
            self.assertTrue(any("连上主程序" in e["msg"] for e in self.log.recent(50, cats=["手柄"])))
        finally:
            ops.close()

    # ---- V5：页面进出手柄页带序号；gone 永远拿不回；动作不再绕过 closed ----

    def test_v5_open_off_out_of_order_and_gone(self):
        self.feed()
        self.ops.handle({"op": "drive", "open": True, "seq": 11}, conn="a")   # 离开又回来：open(11) 先到
        self.assertIn("dropped", self.ops.handle({"op": "drive", "on": False, "seq": 10}, conn="a"))   # off(10) 晚到：丢
        self.assertIn("sent", self.stick("drive", seq=12, ly=-1))
        self.ops.handle({"op": "stop", "seq": 13}, conn="a")
        self.ops.handle({"op": "drive", "on": False, "seq": 21}, conn="a")    # 进来又离开：off(21) 先到
        self.assertIn("dropped", self.ops.handle({"op": "drive", "open": True, "seq": 20}, conn="a"))
        self.assertIn("dropped", self.stick("drive", seq=22, ly=-1))
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "action_start", "id": "mouth"}, conn="a")   # 以前 action=True 能绕过 closed
        self.assertIsNone(self.ops.snapshot()["virtual"]["driver"])           # open 不拿方向盘
        self.ops.handle({"op": "drive", "open": True, "seq": 30}, conn="a")
        self.assertIsNone(self.ops.snapshot()["virtual"]["driver"])
        # 断开的永远拿不回：open 直接拒，之后的帧照样丢
        self.ops.mark_gone("a")
        self.ops.drop_client("a")
        with self.assertRaises(P.Refused):
            self.ops.handle({"op": "drive", "open": True, "seq": 40}, conn="a")
        self.assertIn("dropped", self.ops._sticks({"mode": "drive", "seq": 41, "ly": -1}, "a", "A"))
        self.assertIn("dropped", self.ops._move({"vx": 0.2, "seq": 42}, "a", "A"))

    # ---- V6：通道分开、屏障共用、模式以后端为准 ----

    def test_v6_channels_are_separate_barrier_is_shared(self):
        self.feed()
        self.into("head", seq=1)
        self.assertIn("sent", self.stick("head", seq=5, ry=-1))
        self.assertEqual(self.heads()[-1]["neck_pitch"], 2.5)               # 右摇杆的 y 传上来了
        # 头模式里旧的 move 通道也不走（审查 R6、三 V6：不在走路模式时不发速度）
        self.assertIn("dropped", self.ops.handle({"op": "move", "vx": 0.1, "seq": 6}, conn="a"))
        self.ops.handle({"op": "stop", "seq": 9}, conn="a")
        self.assertIn("dropped", self.stick("head", seq=8, ry=-1))           # 屏障共用：stop 以前的都作废
        # 模式对不上：丢掉，告诉页面后端现在是什么
        r = self.stick("drive", seq=10, ly=-1)
        self.assertEqual((r["mode"], "dropped" in r), ("head", True))
        self.assertFalse(any(p["vx"] for _, p in self.sent("robot.move")))
        # 通道分开（在走路模式里看）：刚发过一帧头，紧接着的走路帧不用等 40 ms、也不会被判成「被更新的替掉」
        self.stick("head", seq=11, ry=-1)
        self.into("drive", seq=12)
        r = self.stick("drive", seq=13, ly=-1)
        self.assertEqual((r["sent"][0], r["merged"]), (0.3, False))
        self.assertEqual(self.ops._seq_of("a")["sent"], {"head": 11, "move": 13})

    def test_v6_entering_mode_while_walking_stops_first(self):
        self.feed()
        self.stick("drive", seq=1, ly=-1)
        self.assertEqual(self.sent("robot.move")[-1][1]["vx"], 0.3)
        self.into("head", seq=2)
        self.assertEqual(self.sent("robot.move")[-1][1], {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})

    # ---- V7：Start ----

    def test_v7_start_picks_by_posture_with_expect(self):
        cases = (("limp", {}, "stand", ("robot.init", {})),
                 ("holding", {}, "walk", ("robot.enable", {"on": True, "toggle": False})),
                 ("sitting", {"sitting": True}, "stand", ("robot.do", {"skill": "sit_toggle"})))
        for posture, pol, expect, call in cases:
            with self.subTest(posture=posture):
                b = bus(posture != "limp", policy_enabled=posture == "sitting", homed=True)
                self.robot_ok(posture=posture, policies=dict(POLICIES, **pol), bus=b)
                self.ops._relax_at = -1e9
                self.assertEqual(self.ops.snapshot()["motion"]["start"]["op"], expect)
                r = self.ops.handle({"op": "start", "expect": expect, "src": "svg"}, by="电脑", conn="a")
                self.assertEqual(r["did"], expect)
                # walk 会多回读几次 busStatus 确认策略启动，别用 calls[-1]
                self.assertIn(call, [c for c in self.robot.calls if c[0] != "robot.busStatus"])
        self.feed("policy")
        self.ops.handle({"op": "start", "expect": "walk_stop"}, conn="a")
        self.assertEqual(self.robot.methods()[-2:], ["robot.stop", "robot.enable"])
        # 状态在路上变了：页面以为「开始走」，后端判「停下」→ 拒，什么都不发
        n = len(self.robot.calls)
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "start", "expect": "walk"}, conn="a")
        self.assertIn("状态刚变了", str(cm.exception))
        self.assertEqual(len(self.robot.calls), n)
        # 坐没坐读不到：拒（审查二 S2）
        self.feed("holding", pol=None)
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "start", "expect": "walk"}, conn="a")
        self.assertIn("坐着", str(cm.exception))
        self.assertTrue(any("网页手柄 Start → " in e["msg"] for e in self.log.recent(80, cats=["手柄"])))

    def test_v7_start_waits_until_joints_settle(self):
        """homed 了、但关节实际位置还没到 HOME：Start / 开始走不能先显示能按。"""
        guard = {"ready": False, "reason": "关节还没到 HOME", "max_joint_error_deg": 12.3,
                 "joint_tolerance_deg": 5.0, "stable_samples": 1, "tilt_deg": 2.0, "tilt_tolerance_deg": 10.0}
        self.feed("homing", bus=bus(homed=True, home_start_guard=guard))
        mv = self.ops.snapshot()["motion"]
        self.assertEqual(mv["posture"], "homing")
        self.assertFalse(mv["start"]["ok"])
        self.assertEqual(mv["start"]["label"], "站起来")
        self.assertEqual(mv["start"]["why"], "还在站起来，站稳了再点（关节还没到 HOME）")      # 带上主程序说的原因
        self.assertEqual(mv["buttons"]["walk"]["why"], "还在站起来，站稳了再点（关节还没到 HOME）")

    # ---- V4：按住的键 ----

    def keep(self, held, stop, conn="a", every=0.1):
        while not stop.is_set():
            self.ops.handle({"op": "keys", "held": held, "seq": next(self._kseq)}, conn=conn)
            self.feed()
            time.sleep(every)

    def setUp(self):
        super().setUp()
        self._kseq = itertools.count(100)
        self._n = itertools.count(1000)

    def test_v4_x_hold_keeps_rolling_then_times_out(self):
        self.feed()
        self.ops.handle({"op": "keys", "held": ["x"], "down": ["x"], "seq": 1}, conn="a")
        self.ops.handle({"op": "key", "key": "x", "src": "svg"}, conn="a")          # 按下那一下：请求，拿拒绝原因
        self.assertEqual(self.robot.calls[-1], ("robot.do", {"skill": "roulade"}))
        stop = threading.Event()
        threading.Thread(target=self.keep, args=(["x"], stop), daemon=True).start()
        time.sleep(0.5)
        stop.set()
        n_do = len(self.sent("robot.do"))
        self.assertGreaterEqual(n_do, 6)                                             # 每 40 ms 一次通知
        self.assertTrue(all(p == {"skill": "roulade"} for _, p in self.sent("robot.do")))
        time.sleep(D.X_HOLD_S + 0.2)                                                 # 页面不报了：0.3 秒内停
        n = len(self.sent("robot.do"))
        time.sleep(0.2)
        self.assertEqual(len(self.sent("robot.do")), n)
        self.assertLessEqual(n - n_do, 10)

    def test_v4_lt_wheee_and_mouth_end_cleanly(self):
        self.feed()
        self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": 1}, conn="a")
        stop = threading.Event()
        threading.Thread(target=self.keep, args=(["lt"], stop), daemon=True).start()
        time.sleep(0.4)
        stop.set()
        time.sleep(0.15)
        self.ops.handle({"op": "keys", "held": [], "seq": 9999}, conn="a")          # 松开
        wait_until(lambda: self.sent("robot.mouth")[-1][1] == {"open": 0.0}, what="合嘴")
        sounds = [p for _, p in self.sent("robot.sound")]
        self.assertGreater(sounds.count({"tag": "wheee", "hold": True}), 5)
        self.assertEqual(sounds[-1], {"tag": "wheee", "hold": False})                # 结束发 hold:false
        self.assertIn({"open": 1.0}, [p for _, p in self.sent("robot.mouth")])
        # 日志只记按下、松开，不按拍记（三 V19）
        lines = [e for e in self.log.recent(200, cats=["手柄"]) if "网页手柄 LT" in e["msg"]]
        self.assertEqual(len(lines), 2)

    def test_v4_rt_chirps_once_and_holding_still_works(self):
        """RT：按下那一下嘎一声（通知，不占「上一个还没回话」）；嘴按 fork 的规矩，站着保持也能按（三 V10）。"""
        self.feed("holding")
        self.ops.handle({"op": "keys", "held": ["rt"], "down": ["rt"], "seq": 1}, conn="a")
        stop = threading.Event()
        threading.Thread(target=self.keep, args=(["rt"], stop), daemon=True).start()
        time.sleep(0.3)
        stop.set()
        self.assertEqual([p for _, p in self.sent("robot.sound")], [{"tag": "chirp"}])
        self.assertNotIn("robot.sound", self.robot.methods())                        # 是通知，不是请求
        wait_until(lambda: self.sent("robot.mouth")[-1][1] == {"open": 0.0}, what="0.45 秒没报：合嘴")

    def test_v4_relax_ends_keys_and_heartbeat_does_not_revive(self):
        self.feed()
        self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": 1}, conn="a")
        wait_until(lambda: self.sent("robot.mouth"), what="张嘴")
        self.ops.note_relax("电脑")
        wait_until(lambda: self.sent("robot.mouth")[-1][1] == {"open": 0.0}, what="卸力后合嘴")
        wait_until(lambda: self.sent("robot.sound")[-1][1] == {"tag": "wheee", "hold": False}, what="wheee 停")
        r = self.ops.handle({"op": "keys", "held": ["lt"], "seq": 2}, conn="a")    # 页面的心跳还报着：不续上
        self.assertEqual((r["held"], r["ended"]), ([], ["lt"]))
        n = len(self.sent())
        time.sleep(0.15)
        self.assertEqual(len(self.sent()), n)

    def test_v4_mouth_actions_and_triggers_are_exclusive(self):
        self.feed()
        stop = threading.Event()

        def hold():
            while not stop.is_set():
                self.ops.handle({"op": "action_hold"}, conn="a")
                time.sleep(0.05)
        self.ops.handle({"op": "action_start", "id": "mouth"}, conn="a")
        threading.Thread(target=hold, daemon=True).start()
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "keys", "held": ["rt"], "down": ["rt"], "seq": 1}, conn="a")
        self.assertIn("张嘴", str(cm.exception))
        stop.set()
        self.ops.handle({"op": "action_stop"}, conn="a")
        wait_until(lambda: self.ops.snapshot()["motion"]["running"] is None, what="动作停了")
        self.ops.handle({"op": "keys", "held": ["rt"], "down": ["rt"], "seq": 2}, conn="a")
        acts = {a["id"]: a for a in self.ops.snapshot()["motion"]["actions"]}
        self.assertIn("LT / RT", acts["mouth"]["why"])

    # ---- V9：头 / 嘴幅度从 padd 的配置读；V18：padd 的 --config ----

    def test_v9_v18_inputs_from_padd_unit(self):
        unit = ("[Service]\nExecStart=\nExecStart=/usr/bin/python3 /opt/robot/feetech-ft5-r5/startup_gate.py --timeout 60 --exec "
                "/opt/robot/feetech-ft5-r5/padd --socket /run/robotd.sock --config /etc/robot/feetech-ft5/robotd.toml "
                "--max-head 2.5 --input-settings /etc/robot/feetech-ft5/pad-settings.json\n")
        files = {D.PADD_UNIT: unit, "/etc/robot/feetech-ft5/pad-settings.json": '{"mouth_percent":60,"head_rad":0.5}',
                 "/etc/robot/feetech-ft5/robotd.toml": "[netpad]\nenabled = false\n[pad_imu_head_control]\nenabled = true\n"}

        def read(p):
            if p in files:
                return files[p]
            raise FileNotFoundError(p)
        self.ops.read_text = read
        self.ops._refresh_files()
        v = self.ops.snapshot()["virtual"]
        self.assertEqual((v["inputs"]["head"], v["inputs"]["mouth"]), (0.5, 60.0))
        self.assertTrue(v["pad_imu_head"])
        self.assertFalse(self.ops.snapshot()["status"]["netpad"])          # [netpad] 也从 padd 的 --config 读
        self.feed()
        self.into("head")
        self.stick("head", lx=-1)
        self.assertEqual(self.heads()[-1]["head_yaw"], 0.5)
        with self.assertRaises(P.Refused) as cm:                            # 方向盘在 a 手里（头那一帧拿的）：b 按不了
            self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": 3}, by="手机 B", conn="b")
        self.assertIn("接管", str(cm.exception))
        self.into("drive")
        # padd 不认的设置文件：保持 --max-head、嘴 100%（input_settings.rs:75-78）
        files["/etc/robot/feetech-ft5/pad-settings.json"] = '{"mouth_percent":120,"head_rad":0.5}'
        self.ops._refresh_files()
        inp = self.ops.snapshot()["virtual"]["inputs"]
        self.assertEqual((inp["head"], inp["mouth"]), (2.5, 100.0))
        self.assertIn("不认", inp["src"])

    # ---- V11：看门狗 ----

    def test_v11_stale_frame_recenters_and_mode_heartbeat_exits(self):
        with mock.patch.object(D, "STICK_STALE_S", 0.2), mock.patch.object(D, "MODE_HB_S", 0.5):
            self.head_latched()
            wait_until(lambda: self.heads()[-1] == HEAD0, timeout=1.0, what="0.2 秒没新帧：头回正")
            self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode"], "head")   # 模式还在
            self.into("drive")
            self.into("body")
            self.stick("body", ly=-1)
            wait_until(lambda: self.poses()[-1] == dict(D.POSE_CENTER), timeout=1.0, what="身体回中")
            wait_until(lambda: self.poses()[-1] == POSE_OFF, timeout=1.5, what="模式心跳超时：退出身体模式")
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode"], "drive")
        self.assertTrue(any("没报还在" in e["msg"] for e in self.log.recent(50, cats=["安全"])))

    # ---- V12：策略没在跑时头 / 身体帧丢掉，模式照样能切 ----

    def test_v12_frames_dropped_when_not_driving(self):
        self.feed("holding")
        r = self.into("head")
        self.assertIn("策略没在跑", r["note"])
        r = self.stick("head", lx=1)
        self.assertIn("策略没在跑", r["dropped"])
        self.assertEqual(self.heads(), [])

    # ---- V13：快线程池 4 条；watch 不在快线程池里等 pad.bindings ----

    def test_v13_watch_does_not_wait_for_bindings(self):
        self.assertEqual(self.ops._fast_pool._max_workers, 4)
        self.robot.replies["pad.bindings"] = lambda p: (time.sleep(1.0), {"bindings": []})[1]
        t0 = time.monotonic()
        self.ops.handle({"op": "watch"}, conn="a")
        self.assertLess(time.monotonic() - t0, 0.3)
        wait_until(lambda: "pad.bindings" in self.robot.methods(), what="看守线程去拉了")

    # ---- V14：bindings 刷新、怎么用 ----

    def test_v14_bindings_refresh_and_rules(self):
        self.ops.w["bindings"] = 0.1
        rows = [{"button": "a", "skill": "sit_toggle", "overridden": True}, {"button": "x", "skill": "", "overridden": True},
                {"button": "lb", "skill": "moonwalk", "overridden": True, "error": "no skill named moonwalk"},
                {"button": "rb", "skill": "kick_right", "overridden": False},
                {"button": "dpad_down", "skill": "sit_toggle", "overridden": False}]
        self.robot.replies["pad.bindings"] = {"bindings": rows}
        self.watch()
        wait_until(lambda: [m for m in self.robot.methods() if m == "pad.bindings"].__len__() >= 2, what="10 秒（测试里 0.1 秒）刷新")
        self.feed()
        keys = self.ops.snapshot()["motion"]["keys"]
        self.assertEqual((keys["a"]["op"], keys["a"]["does"]), ("sit", "坐下"))      # 绑 sit_toggle：按坐没坐走坐下 / 站起来
        self.assertIn("关掉", keys["x"]["why"])
        self.assertIn("moonwalk", keys["lb"]["why"])
        self.ops.handle({"op": "key", "key": "a", "src": "svg"}, conn="a")
        self.assertIn(("robot.do", {"skill": "sit_toggle"}), self.robot.calls)
        for k, word in (("x", "关掉"), ("lb", "moonwalk")):
            with self.assertRaises(P.Refused) as cm:
                self.ops.handle({"op": "key", "key": k, "src": "svg"}, conn="a")
            self.assertIn(word, str(cm.exception))
        self.feed("policy", pol={"sitting": True})
        self.assertEqual(self.ops.snapshot()["motion"]["keys"]["dpad_down"]["op"], "stand")
        self.feed("policy", pol=None)
        self.assertIn("是不是坐着", self.ops.snapshot()["motion"]["keys"]["dpad_down"]["why"])

    # ---- V15：声音不占「上一个还没回话」 ----

    def test_v15_sound_does_not_take_busy(self):
        self.feed("limp")
        self.ops._relax_at = -1e9
        gate = threading.Event()
        self.robot.replies["robot.init"] = lambda p: (gate.wait(1), {"accepted": True})[1]
        t = threading.Thread(target=self.ops.handle, args=({"op": "stand"},))
        t.start()
        wait_until(lambda: self.ops.snapshot()["motion"]["busy"], what="站起来在路上")
        self.ops.handle({"op": "sound", "tag": "chirp"})
        self.assertTrue(self.ops.snapshot()["motion"]["busy"])
        gate.set()
        t.join()
        self.assertFalse(self.ops.snapshot()["motion"]["busy"])

    # ---- V16：实体手柄连着，图上按的后端也拒 ----

    def test_v16_svg_keys_refused_in_mirror(self):
        self.feed()
        self.watch()
        self.tap.attach(XBOX_DEV)
        for args in ({"op": "key", "key": "a", "src": "svg"}, {"op": "start", "expect": "walk_stop", "src": "svg"},
                     {"op": "keys", "held": ["lt"], "down": ["lt"]}, {"op": "mode", "mode": "head"},
                     {"op": "sticks", "mode": "drive", "ly": -1}):
            with self.subTest(op=args["op"]):
                with self.assertRaises(P.Refused) as cm:
                    self.ops.handle(args, conn="a")
                self.assertIn("实体手柄", str(cm.exception))
        self.ops.handle({"op": "skill", "name": "kick_left"}, conn="a")   # 下面的大按钮照常（不带 src）
        self.assertEqual(self.sent(), [])

    # ---- 十字右 / 十字上：照官方（用户 10-19 定） ----

    def test_dpad_right_reboots_every_servo_like_official(self):
        """按一下就发 robot.rebootMotors {"ids":[]}（空 = 全部）；收下以后记一次卸力：2 秒内不让上力、头回正。"""
        self.robot.replies["robot.rebootMotors"] = {"accepted": True}
        self.head_latched()
        self.assertTrue(self.ops.snapshot()["motion"]["reboot"]["ok"])
        r = self.ops.handle({"op": "reboot", "src": "svg"}, by="电脑 A", conn="a")
        self.assertEqual(r["did"], "reboot")
        self.assertIn(("robot.rebootMotors", {"ids": []}), self.robot.calls)
        self.assertIn("刚卸了力", self.ops.snapshot()["motion"]["buttons"]["stand"]["why"])
        wait_until(lambda: self.heads() and self.heads()[-1] == HEAD0, what="重启舵机以后头回正")

    def test_dpad_right_works_after_relax_and_while_another_page_drives(self):
        """救急的路：卸力之后（官方就是「卸力了再按」的场景）、别的页面拿着方向盘时都能按。"""
        self.robot.replies["robot.rebootMotors"] = {"accepted": True}
        self.feed()
        self.stick("drive", conn="b", ly=-1)                                  # B 正在推摇杆
        self.relax_pressed()
        self.ops.handle({"op": "reboot", "src": "svg"}, by="电脑 A", conn="a")
        self.assertEqual([m for m in self.robot.methods() if m == "robot.rebootMotors"], ["robot.rebootMotors"])

    def test_dpad_right_refused_in_servo_session_and_mirror(self):
        self.robot.replies["robot.rebootMotors"] = {"accepted": True}
        self.feed(paused={"by": "舵机页"})
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "reboot", "src": "svg"}, conn="a")
        self.assertIn("调舵机", str(cm.exception))
        self.feed()
        self.watch()
        self.tap.attach(XBOX_DEV)
        for op in ({"op": "reboot", "src": "svg"}, {"op": "drive_mode", "expect": "roller", "src": "svg"}):
            with self.subTest(op=op["op"]):
                with self.assertRaises(P.Refused) as cm:
                    self.ops.handle(op, conn="a")
                self.assertIn("实体手柄", str(cm.exception))
        self.assertNotIn("robot.rebootMotors", self.robot.methods())
        self.assertNotIn("robot.setMode", self.robot.methods())

    def test_dpad_up_switches_walk_and_roller(self):
        """页面按住 3 秒才发；后端按 robot.policies 的 mode 挑要切到的，跟 expect 不一样就拒。"""
        self.robot.replies["robot.setMode"] = {"accepted": True}
        self.feed()
        dm = self.ops.snapshot()["motion"]["drive_mode"]
        self.assertEqual((dm["ok"], dm["current"], dm["target"]), (True, "walk", "roller"))
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "drive_mode", "expect": "walk", "src": "svg"}, conn="a")
        self.assertIn("状态刚变了", str(cm.exception))
        self.assertNotIn("robot.setMode", self.robot.methods())
        r = self.ops.handle({"op": "drive_mode", "expect": "roller", "src": "svg"}, conn="a")
        self.assertEqual((r["did"], r["target"]), ("drive_mode", "roller"))
        self.assertEqual(self.robot.calls[-1], ("robot.setMode", {"mode": "roller"}))
        self.feed(pol={"mode": "roller"})
        self.assertEqual(self.ops.snapshot()["motion"]["drive_mode"]["target"], "walk")
        self.robot.replies["robot.setMode"] = {"accepted": True, "reason": "mode already selected"}
        r = self.ops.handle({"op": "drive_mode", "expect": "walk", "src": "svg"}, conn="a")
        self.assertEqual(r["reason"], "已经是走路了")
        self.assertFalse(self.ops.snapshot()["motion"]["busy"])

    def test_dpad_up_refusals(self):
        self.feed()
        self.robot.replies["robot.setMode"] = {"accepted": False,
                                               "reason": "roller ONNX models are not installed; original walking policy unchanged"}
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "drive_mode", "expect": "roller", "src": "svg"}, conn="a")
        self.assertIn("没装轮滑模型", str(cm.exception))
        self.assertFalse(self.ops.snapshot()["motion"]["busy"])
        self.stick("drive", conn="b", ly=-1)                                  # B 正在推摇杆：要切模式（会回站姿）得等它
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "drive_mode", "expect": "roller", "src": "svg"}, conn="a")
        self.assertIn("正在推摇杆", str(cm.exception))
        self.feed(pol=None)
        dm = self.ops.snapshot()["motion"]["drive_mode"]
        self.assertFalse(dm["ok"])
        self.assertIn("走路还是轮滑", dm["why"])
        self.relax_pressed()
        self.assertIn("刚卸了力", self.ops.snapshot()["motion"]["drive_mode"]["why"])


class CodeReviewTest(PadTestBase):
    """第三轮代码审查（vpad-codereview.md）R1–R10 的反例。辅助函数借 VpadTest 的（不继承：免得它的测试再跑一遍）。"""
    feed, sent, poses, heads, s = VpadTest.feed, VpadTest.sent, VpadTest.poses, VpadTest.heads, VpadTest.s
    into, stick, body_latched, head_latched = VpadTest.into, VpadTest.stick, VpadTest.body_latched, VpadTest.head_latched
    assert_no_empty_pose = VpadTest.assert_no_empty_pose

    def setUp(self):
        super().setUp()
        self._n = itertools.count(1000)

    def first_body_frame_in_flight(self, exit_fn):
        """R1：身体模式第一帧在 robotd 的 socket 上卡住（这时 _latch 还是 None），另一条线程走一条退出路径。"""
        self.feed()
        self.into("body")
        gate, entered = threading.Event(), threading.Event()
        orig = self.robot.notify

        def slow(method, params):
            if method == "robot.pose" and params.get("active") is True and params.get("z") and not gate.is_set():
                entered.set()
                gate.wait(2)
            return orig(method, params)
        self.robot.notify = slow
        t1 = threading.Thread(target=lambda: self.stick("body", ly=1))
        t1.start()
        self.assertTrue(entered.wait(2))
        t2 = threading.Thread(target=exit_fn)
        t2.start()
        time.sleep(0.15)
        gate.set()
        t1.join(3)
        t2.join(3)
        wait_until(lambda: self.poses()[-1] == POSE_OFF, timeout=1.5, what="退出以后身体模式要关上（不能留着 active:true）")
        time.sleep(0.7)                       # 看门狗 0.6 秒：不能再把它打开
        self.assertEqual(self.poses()[-1], POSE_OFF)
        self.assertIsNone(self.ops._latch["body"])
        self.assert_no_empty_pose()

    def test_r1_first_body_frame_in_flight_then_leave_page(self):
        self.first_body_frame_in_flight(lambda: self.ops.handle({"op": "drive", "on": False, "seq": self.s()}, conn="a"))

    def test_r1_first_body_frame_in_flight_then_disconnect(self):
        self.first_body_frame_in_flight(lambda: (self.ops.mark_gone("a"), self.ops.drop_client("a", "A")))

    def test_r1_first_body_frame_in_flight_then_b_again(self):
        self.first_body_frame_in_flight(lambda: self.into("drive"))

    def test_r2_watchdog_center_cannot_reopen_after_exit(self):
        """R2：看门狗排好「回中（active:true）」、还没拿到发送锁时，退出路径先发了 active:false。"""
        with mock.patch.object(D, "STICK_STALE_S", 0.3):
            self.body_latched()
            real = self.ops._send_lk
            armed = threading.Event()

            class Late:
                """只让后台线程拿发送锁时慢 80 ms（线程被切走、锁被别人占着），别的线程照常。"""
                def __enter__(s):
                    if threading.current_thread().name == "pad-tick" and armed.is_set():
                        time.sleep(0.08)
                    real.acquire()
                    return s

                def __exit__(s, *a):
                    real.release()
            self.ops._send_lk = Late()
            armed.set()
            time.sleep(0.45)                  # 看门狗该动手了（帧 0.3 秒没来）
            self.ops.handle({"op": "drive", "on": False, "seq": self.s()}, conn="a")
            time.sleep(0.4)
            armed.clear()
        self.assertEqual(self.poses()[-1], POSE_OFF)
        self.assertEqual(self.ops._latch, {"head": None, "body": None})

    def test_r3_heartbeat_and_stale_enter_do_not_reopen(self):
        self.head_latched()
        self.ops.note_relax("电脑")                                      # 后端替页面退出头模式
        wait_until(lambda: self.ops.snapshot(conn="a")["virtual"]["mode"] == "drive", what="退出")
        self.ops._relax_at = -1e9
        self.feed()
        r = self.ops.handle({"op": "mode", "mode": "head", "hb": True}, conn="a")   # 页面还没跟上：心跳报「还在头模式」
        self.assertEqual(r["mode"], "drive")
        self.assertIn("卸力了", r["dropped"])
        r = self.into("head")                                             # 没跟上快照的「进头模式」也不收
        self.assertEqual((r["mode"], "dropped" in r), ("drive", True))
        self.assertEqual(self.ops.snapshot(conn="a")["virtual"]["mode"], "drive")
        self.into("drive")                                                # 页面跟着回到走路（回一句）
        self.assertEqual(self.into("head")["mode"], "head")              # 之后再按 Y 就进得去
        r = self.ops.handle({"op": "mode", "mode": "head", "hb": True}, conn="a")
        self.assertEqual(r, {"mode": "head"})                             # 一样的：只续「还在」

    def test_r4_late_press_after_release_is_dropped(self):
        self.feed()
        self.ops.handle({"op": "keys", "held": [], "seq": 6}, conn="a")                   # 松开（6）先到
        r = self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": 5}, conn="a")   # 按下（5）晚到
        self.assertIn("dropped", r)
        time.sleep(0.15)
        self.assertEqual(self.sent("robot.mouth"), [])

    def test_r5_stale_x_arm_does_not_roll_after_refused_x(self):
        self.feed()
        gate = threading.Event()
        self.robot.replies["robot.do"] = lambda p: (gate.wait(2), {"accepted": True})[1]
        self.ops.handle({"op": "keys", "held": ["x"], "down": ["x"], "seq": 1}, conn="a")
        t = threading.Thread(target=lambda: self.ops.handle({"op": "key", "key": "x", "src": "svg"}, conn="a"))
        t.start()
        time.sleep(0.05)
        self.ops.handle({"op": "keys", "held": [], "seq": 2}, conn="a")                   # 快速点一下：先松手
        gate.set()
        t.join(3)                                                                            # 请求这时才回话
        gate2 = threading.Event()
        self.robot.replies["robot.do"] = lambda p: (gate2.wait(2), {"accepted": True})[1]
        ta = threading.Thread(target=lambda: self.ops.handle({"op": "key", "key": "a", "src": "svg"}, conn="a"))
        ta.start()
        wait_until(lambda: self.ops._cmd_busy, what="A 在路上")
        with self.robot.lk:
            self.robot.sent.clear()
        self.ops.handle({"op": "keys", "held": ["x"], "down": ["x"], "seq": 3}, conn="a")
        with self.assertRaises(P.Refused):                                                  # 这一下 X 被拒（上一个还没回话）
            self.ops.handle({"op": "key", "key": "x", "src": "svg"}, conn="a")
        time.sleep(0.15)
        dos = self.sent("robot.do")
        gate2.set()
        ta.join(3)
        self.assertEqual(dos, [], "X 被拒了，不能拿上一下 X 的 arm 连滚（roulade 能抢断正在做的低头捡）")

    def test_r6_drive_frame_never_goes_out_in_head_mode(self):
        self.feed()
        real = D.sticks_to_intent
        fired = []

        def hook(mode, *a):
            if mode == "drive" and not fired:     # _sticks 刚查完模式（走路）、放了锁：另一条线程切到头模式
                fired.append(1)
                self.ops.handle({"op": "mode", "mode": "head", "seq": 20}, conn="a")
            return real(mode, *a)
        with mock.patch.object(D, "sticks_to_intent", hook):
            r = self.stick("drive", seq=19, ly=-1)
        self.assertIn("dropped", r)
        self.assertFalse(any(p["vx"] for _, p in self.sent("robot.move")))
        self.into("head", seq=21)
        self.assertIn("dropped", self.ops.handle({"op": "move", "vx": 0.2, "seq": 22}, conn="a"))   # 旧的 move 通道也按走路核

    def test_r7_x_release_stops_rolling_at_once(self):
        """X 按住时松开：通知当拍就停（不是靠 0.3 秒的心跳超时）。"""
        self.feed()
        self.ops.handle({"op": "keys", "held": ["x"], "down": ["x"], "seq": 1}, conn="a")
        self.ops.handle({"op": "key", "key": "x", "src": "svg"}, conn="a")
        wait_until(lambda: len(self.sent("robot.do")) >= 3, timeout=1.0, what="在连滚")
        self.ops.handle({"op": "keys", "held": [], "seq": 2}, conn="a")
        time.sleep(0.08)                      # 至多还有在路上的一拍
        n = len(self.sent("robot.do"))
        time.sleep(0.2)                       # 还在 0.3 秒心跳超时以内：停是因为松开了
        self.assertEqual(len(self.sent("robot.do")), n)

    def test_r8_close_waits_for_the_tick_thread(self):
        self.feed()
        self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": 1}, conn="a")
        wait_until(lambda: self.sent("robot.mouth"), what="张嘴")
        self.ops.close()
        self.assertFalse(self.ops._tick_thread.is_alive())
        time.sleep(0.1)
        self.assertEqual(self.sent("robot.mouth")[-1][1], {"open": 0.0})                 # 合嘴是最后一条
        self.assertEqual(self.sent("robot.sound")[-1][1], {"tag": "wheee", "hold": False})

    def test_r9_mode_exit_does_not_grow(self):
        self.head_latched()
        self.ops.note_relax("电脑")
        self.assertIn("a", self.ops._mode_exit)
        self.ops.mark_gone("a")
        self.assertNotIn("a", self.ops._mode_exit)                      # 页面断了：删掉
        self.ops._mode_exit["old"] = {"why": "x", "at": time.monotonic() - 20}
        self.ops._request_clear(None, "y")
        self.assertNotIn("old", self.ops._mode_exit)                    # 超过 10 秒：删掉

    def test_r10_input_settings_like_padd(self):
        files = {D.PADD_UNIT: "ExecStart=/x/padd --max-head 1.0 --input-settings /s.json\n",
                 "/s.json": '{"mouth_percent": 60, "head_rad": 0.5, "extra": 1}'}

        def read(p):
            if p in files:
                return files[p]
            raise FileNotFoundError(p)
        self.ops.read_text = read
        self.ops._refresh_files()
        inp = self.ops.snapshot()["virtual"]["inputs"]
        self.assertEqual((inp["head"], inp["mouth"]), (1.0, 100.0))    # 多一个字段：padd 整份拒，按 --max-head、嘴 100%
        self.assertIn("不认", inp["src"])
        # padd 自己写的回执：它正在用的数（写回执的那个 padd 还活着、文件对得上）
        files[D.PADD_INPUT_ACK] = json.dumps({"pid": 4242, "path": "/s.json", "settings": {"mouth_percent": 30, "head_rad": 2.5}})
        self.ops.exists = lambda p: p == "/proc/4242"
        self.ops._refresh_files()
        inp = self.ops.snapshot()["virtual"]["inputs"]
        self.assertEqual((inp["head"], inp["mouth"]), (2.5, 30.0))
        self.assertIn("回执", inp["src"])
        self.ops.exists = lambda p: False                                # 写回执的 padd 已经不在了：不算
        self.ops._refresh_files()
        self.assertEqual(self.ops.snapshot()["virtual"]["inputs"]["head"], 1.0)


class ComboTest(unittest.TestCase):
    def test_shapes_and_text(self):
        self.assertAlmostEqual(C.head_at({"do": "nod", "times": 1}, C.NOD_PERIOD_S / 4)[1], C.NOD_AMP, places=3)
        self.assertEqual(C.head_at({"do": "mouth", "open": 0.5, "ms": 300}, 0.1), None)
        self.assertEqual(C.mouth_at({"do": "mouth", "open": 0.5, "ms": 300}, 0.1), 0.5)
        self.assertEqual(C.mouth_at({"do": "mouth", "open": 0.5, "ms": 300}, 0.29), 0.0)     # 最后一拍合上
        tilt = C.head_at({"do": "tilt", "side": "left", "ms": 1000}, 0.5)
        self.assertLess(tilt[3], 0)
        for c in C.ACTIONS + C.BUILTIN_COMBOS:
            with self.subTest(c=c["id"]):
                self.assertEqual(C.clean_combo(dict(c, id="x" + c["id"]))["steps"], c["steps"])
                self.assertLess(C.duration(c["steps"]), C.MAX_TOTAL_S)
        self.assertEqual(C.describe(C.BUILTIN_COMBOS[0]["steps"]), "叫一声（打招呼） → 点头 2 次 → 张嘴")
        with self.assertRaises(C.Refused):
            C.clean_combo({"id": "long", "name": "x", "steps": [{"do": "wait", "ms": 3000}] * 8})


# ---------------- 解析：实时按键、bluetoothctl ----------------

class LiveInputTest(unittest.TestCase):
    def test_xbox_ble_layout(self):
        """注意：Xbox 的 X / Y 键码对调是推测（E9 待上机核），这里只测「按推测的规则翻」，页面上写明待核。"""
        li = P.LiveInput()
        li.attach(XBOX_DEV, 0.0)
        self.assertEqual(li.kind, "xbox")
        self.assertTrue(li.view(0.0)["device"]["keys_unverified"])
        li.frame({"events": [{"kind": 1, "code": 0x133, "value": 1}]}, 1.0)       # 推测：印 X 的键报 BTN_X(0x133)
        self.assertTrue(li.buttons["west"])
        li.frame({"events": [{"kind": 3, "code": 2, "value": 32767}, {"kind": 3, "code": 9, "value": 1023},
                             {"kind": 3, "code": 17, "value": -1}]}, 1.1)
        self.assertAlmostEqual(li.axes["rx"], 1.0, places=2)                       # BLE：右摇杆是 Z
        self.assertAlmostEqual(li.axes["rt"], 1.0, places=2)                       # 扳机是 GAS
        self.assertTrue(li.buttons["du"])                                          # 十字上
        self.assertIn("rs", li.seen)
        self.assertIs(li.frame({"events": [{"kind": 1, "code": 0x13A, "value": 1}]}, 1.2), True)
        self.assertIsNone(li.frame({"events": [{"kind": 1, "code": 0x13A, "value": 2}]}, 1.3))  # 自动重复不是松开
        self.assertIs(li.frame({"events": [{"kind": 1, "code": 0x13A, "value": 0}]}, 1.4), False)
        li.frame({"events": [{"kind": 1, "code": 0x2C0, "value": 1, "name": "BTN_TRIGGER_HAPPY1"}]}, 1.5)
        self.assertEqual(li.view(2.0)["other"], ["BTN_TRIGGER_HAPPY1"])

    def test_switch_is_positional(self):
        li = P.LiveInput()
        li.attach(PRO_DEV, 0.0)
        self.assertEqual(li.kind, "switch")
        self.assertFalse(li.view(0.0)["device"]["keys_unverified"])
        li.frame({"events": [{"kind": 1, "code": 0x133, "value": 1}, {"kind": 3, "code": 4, "value": -32767}]}, 1.0)
        self.assertTrue(li.buttons["north"])
        self.assertAlmostEqual(li.axes["ry"], -1.0, places=2)


class FakeProc:
    """假的 bluetoothctl 进程：stdin 收到命令，按剧本往 stdout 吐几块（一块不一定是一整行：提示符后面没有换行）。"""

    def __init__(self, script):
        self.script = script
        self.q = queue.Queue()
        self.stdin = self
        self.stdout = iter(self.q.get, None)
        self.got = []
        self.terminated = self.killed = 0
        self.wait_ok = True
        for line in script.get("", []):
            self.q.put(line)

    def write(self, text):
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        cmd = text.strip()
        self.got.append(cmd)
        out = self.script.get(cmd, [])
        for line in out() if callable(out) else out:          # 可以是函数：同一条命令每次回得不一样
            self.q.put(line)
        if cmd == "quit":
            self.q.put(None)

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self, t=None):
        if not self.wait_ok:
            raise subprocess.TimeoutExpired("bluetoothctl", t)
        return 0

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1
        self.q.put(None)


class BluetoothctlTest(unittest.TestCase):
    def test_reconnect_uses_only_requested_mac_and_remaining_time(self):
        calls = []
        def run(argv, timeout):
            calls.append((argv, timeout))
            return 0, "Connection successful\n"
        bt = P.BluetoothCtl(run=run, prefix=("sudo", "-n"), stdbuf=False)
        bt.reconnect(PRO, timeout=0.25)
        self.assertEqual(calls, [(["sudo", "-n", "bluetoothctl", "connect", PRO], 0.25)])
        with self.assertRaises(P.BtError):
            bt.reconnect("--help")
        with self.assertRaises(P.BtError):
            bt.reconnect(PRO, timeout=0)
        self.assertEqual(len(calls), 1)

    def test_reconnect_does_not_accept_another_devices_connection_event(self):
        bt = P.BluetoothCtl(run=lambda argv, timeout: (0, f"[CHG] Device {XBOX} Connected: yes\n"))
        with self.assertRaises(P.BtError):
            bt.reconnect(PRO)

    PROMPT = "\x01\x1b[0;94m\x02[bluetooth]\x01\x1b[0m\x02# "
    T = {"pair": 1.0, "connect": 1.0, "trust": 1.0, "find": 0.3}

    def script(self, pair_ok=True):
        return {
            "": [f"{self.PROMPT}[NEW] Device 11:22:33:44:55:66 Old Phone\n"],                 # 缓存的旧设备：不算看到
            "agent off": ["Agent unregistered\n"],
            "agent NoInputNoOutput": ["Agent registered\n"],
            "default-agent": ["Default agent request successful\n"],
            "scan on": ["Discovery started\n", "[CHG] Controller 00:11:22:33:44:55 Discovering: yes\n",
                        f"\r{self.PROMPT}\x1b[K[\x1b[0;92mNEW\x1b[0m] Device {PRO} Pro Controller\n",
                        f"[CHG] Device {PRO} RSSI: 0xffffffc7 (-57)\n"],
            "scan off": ["Discovery stopped\n"],
            # 真 bluetoothctl 的代理提问是 readline 提示符，后面没有换行（审查 4）
            f"pair {PRO}": (["Attempting to pair with " + PRO + "\n", "\x1b[0;91m[agent]\x1b[0m Accept pairing (yes/no): "] if pair_ok else
                            ["Attempting to pair with " + PRO + "\n", "Failed to pair: org.bluez.Error.AuthenticationFailed\n"]),
            "yes": [f"\n[CHG] Device {PRO} Paired: yes\n", "Pairing successful\n"],
            f"trust {PRO}": [f"[CHG] Device {PRO} Trusted: yes\n", f"Changing {PRO} trust succeeded\n"],
            f"connect {PRO}": ["Attempting to connect to " + PRO + "\n", f"[CHG] Device {PRO} Connected: yes\n", "Connection successful\n"],
        }

    def make(self, pair_ok=True, script=None, prefix=()):
        self.proc = FakeProc(script or self.script(pair_ok))
        self.argv = None
        info = ("Device 98:B6:E9:28:06:09 (public)\n\tName: Pro Controller\n\tClass: 0x00002508\n\tIcon: input-gaming\n"
                "\tPaired: no\n\tTrusted: no\n\tConnected: no\n\tUUID: Human Interface Device... (00001124-0000-1000-8000-00805f9b34fb)\n")
        runs = {"info": (0, info), "show": (0, "Controller 00:11:22:33:44:55 (public)\n\tPowered: yes\n\tPairable: no\n\tDiscovering: no\n")}

        def spawn(argv):
            self.argv = argv
            return self.proc
        bt = P.BluetoothCtl(run=lambda argv, timeout=8.0: runs.get(argv[len(prefix) + 1], (1, "")), spawn=spawn,
                            rfkill_dir="/nonexistent", prefix=prefix, stdbuf=True)
        bt.t = dict(self.T)
        return bt

    def test_scan_lists_only_devices_seen_while_discovering(self):
        bt = self.make()
        a = bt.adapter()
        self.assertEqual((a["present"], a["powered"], a["pairable"]), (True, True, False))
        bt.scan_start()
        wait_until(lambda: any(d.get("rssi") == -57 for d in bt.devices()), what="RSSI")
        devs = {d["mac"]: d for d in bt.devices()}
        self.assertEqual(devs[PRO]["name"], "Pro Controller")
        self.assertEqual(devs[PRO]["cls"], 0x2508)
        self.assertIsNone(devs["11:22:33:44:55:66"].get("seen_at"))         # 搜索前的缓存不算
        self.assertEqual(self.proc.got[0], "agent off")                     # 审查 5：启动自带的代理先撤掉
        self.assertNotIn("agent NoInputNoOutput", self.proc.got)            # 搜索不注册代理
        bt.close()
        self.assertIn("quit", self.proc.got)

    def test_pair_sequence_in_one_session_and_auto_yes_without_newline(self):
        bt = self.make()
        bt.scan_start()
        steps = []
        bt.pair_device(PRO, True, threading.Event(), lambda k, s, n=None: steps.append((k, s)))
        self.assertEqual([c for c in self.proc.got if c.split()[0] in ("pair", "trust", "connect")],
                         [f"pair {PRO}", f"trust {PRO}", f"connect {PRO}"])
        self.assertIn("yes", self.proc.got)                                   # 提示符没换行也答了 yes
        self.assertEqual(steps[-1], ("connect", "ok"))
        got = self.proc.got
        i_agent, i_pair = got.index("agent NoInputNoOutput"), got.index(f"pair {PRO}")
        self.assertLess(i_agent, i_pair)
        self.assertEqual(got[-1], "agent off")                                # 配完交还代理
        bt.close()

    def test_prompt_outside_pairing_is_answered_no(self):
        s = dict(self.script(), **{"scan on": ["Discovery started\n", "[agent] Authorize service 0000110e (yes/no): "]})
        bt = self.make(script=s)
        bt.scan_start()
        wait_until(lambda: "no" in self.proc.got, what="答 no")
        self.assertNotIn("yes", self.proc.got)
        bt.close()

    def test_pair_failure_classified(self):
        bt = self.make(pair_ok=False)
        bt.scan_start()
        with self.assertRaises(P.BtError) as cm:
            bt.pair_device(PRO, True, threading.Event(), lambda *a: None)
        self.assertEqual(cm.exception.code, "auth_failed")
        self.assertEqual(self.proc.got[-1], "agent off")
        bt.close()

    def test_success_marks_are_locked_to_the_mac(self):
        """审查 11：别的设备的 Paired: yes 不算；搜不到这个设备报 not_found，不报 timeout。"""
        s = self.script()
        s[f"pair {PRO}"] = ["Attempting to pair with " + PRO + "\n", "[CHG] Device 11:22:33:44:55:66 Paired: yes\n"]
        bt = self.make(script=s)
        bt.scan_start()
        with self.assertRaises(P.BtError) as cm:
            bt.pair_device(PRO, True, threading.Event(), lambda *a: None)
        self.assertEqual(cm.exception.code, "timeout")
        bt.close()
        s = self.script()
        s["scan on"] = ["Discovery started\n"]
        bt = self.make(script=s)
        with self.assertRaises(P.BtError) as cm:
            bt.pair_device(PRO, True, threading.Event(), lambda *a: None)
        self.assertEqual(cm.exception.code, "not_found")
        bt.close()

    def test_startup_agent_registered_late_is_replaced(self):
        """10-19 二号鸭（bluetoothctl 5.82）：开会话那句 agent off 回「No agent is registered」，随后它自带的代理才注册上；
        配对时 agent NoInputNoOutput 回「Agent is already registered」→ 先撤掉、再注册一次，照常配上。"""
        s = self.script()
        offs = iter([["No agent is registered\n", "Agent registered\n"]])
        ons = iter([["Agent is already registered\n"]])
        s["agent off"] = lambda: next(offs, ["Agent unregistered\n"])
        s["agent NoInputNoOutput"] = lambda: next(ons, ["Agent registered\n"])
        bt = self.make(script=s)
        bt.scan_start()
        steps = []
        bt.pair_device(PRO, True, threading.Event(), lambda k, st, n=None: steps.append((k, st)))
        self.assertEqual(steps[-1], ("connect", "ok"))
        got = [c for c in self.proc.got if c.startswith("agent")]
        self.assertEqual(got, ["agent off", "agent NoInputNoOutput", "agent off", "agent NoInputNoOutput", "agent off"])
        bt.close()

    def test_connect_busy_right_after_pairing_waits_then_retries(self):
        """10-19 二号鸭 G50 Pro：配完马上 connect 回 InProgress br-connection-busy（手柄自己也在连）。
        手柄自己连上了就算成；没连上等一会儿再连一次。"""
        busy = ["Attempting to connect to " + PRO + "\n", "Failed to connect: org.bluez.Error.InProgress br-connection-busy\n"]
        for self_connects in (True, False):
            with self.subTest(self_connects=self_connects):
                s = self.script()
                first = busy + ([f"[CHG] Device {PRO} Connected: yes\n"] if self_connects else [])
                tries = iter([first])
                s[f"connect {PRO}"] = lambda: next(tries, [f"[CHG] Device {PRO} Connected: yes\n", "Connection successful\n"])
                bt = self.make(script=s)
                bt.t["settle"] = 0.3
                bt.scan_start()
                steps = []
                bt.pair_device(PRO, True, threading.Event(), lambda k, st, n=None: steps.append((k, st)))
                self.assertEqual(steps[-1], ("connect", "ok"))
                self.assertEqual(self.proc.got.count(f"connect {PRO}"), 1 if self_connects else 2)
                bt.close()

    def test_agent_failure_and_scan_failure_close_the_session(self):
        """审查 6：注册代理失败、搜索起不来：会话要关掉，不留孤儿进程。"""
        s = self.script()
        s["agent NoInputNoOutput"] = ["Failed to register agent object\n"]
        bt = self.make(script=s)
        with self.assertRaises(P.BtError) as cm:
            bt.pair_device(PRO, True, threading.Event(), lambda *a: None)
        self.assertEqual(cm.exception.code, "agent")
        bt.close()
        self.assertIn("quit", self.proc.got)
        s = self.script()
        s["scan on"] = ["Failed to start discovery: org.bluez.Error.NotReady\n"]
        bt = self.make(script=s)
        with self.assertRaises(P.BtError):
            bt.scan_start()
        self.assertIn("quit", self.proc.got)
        self.assertIsNone(bt._session)

    def test_close_escalates_and_sudo_prefix_puts_stdbuf_after_sudo(self):
        bt = self.make(prefix=("sudo", "-n"))
        bt.scan_start()
        self.assertEqual(self.argv, ["sudo", "-n", "stdbuf", "-oL", "bluetoothctl"])
        self.proc.wait_ok = False                                             # quit 之后不退：先 terminate 再 kill
        bt.close()
        self.assertEqual((self.proc.terminated, self.proc.killed), (1, 1))

    def test_real_pipe_prompt_without_newline(self):
        """用真的子进程（真管道）：提示符没有换行，按块读也能马上答 yes（审查 4 的根因）。"""
        child = (
            "import sys\n"
            "o=sys.stdout.buffer\n"
            "def w(s):\n    o.write(s.encode()); o.flush()\n"
            "for line in sys.stdin.buffer:\n"
            "    c=line.decode().strip()\n"
            "    if c=='quit': break\n"
            "    if c=='agent off': w('Agent unregistered\\n')\n"
            "    elif c.startswith('pair '): w('Attempting to pair\\n'); w('[agent] Accept pairing (yes/no): ')\n"
            "    elif c=='yes': w('\\nPairing successful\\n')\n")
        spawn = lambda argv: subprocess.Popen([sys.executable, "-I", "-u", "-c", child], stdin=subprocess.PIPE,  # noqa: E731
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        s = P.BtSession(["bluetoothctl"], spawn)
        try:
            s.answer_yes = True
            s.cmd("pair AA", r"Pairing successful", timeout=5.0)
            self.assertTrue(any(a.endswith("→ yes") for a in s.asked))
        finally:
            s.close()
            s.p.wait(5)


class ParseTest(unittest.TestCase):
    def test_classify(self):
        for raw, code in [("Failed to pair: org.bluez.Error.AuthenticationFailed", "auth_failed"),
                          ("Failed to connect: org.bluez.Error.Failed br-connection-page-timeout", "timeout"),
                          ("Encryption Change: PIN or Key Missing (0x06)", "key_missing"),
                          ("SMP: Pairing Failed: DHKey check failed (0x0b)", "dhkey"),
                          ("Failed to start discovery: org.bluez.Error.NotReady", "powered_off"),
                          ("No default controller available", "no_adapter"),
                          ("Waiting to connect to bluetoothd...", "bluez_down"),
                          ("Device 98:B6:E9:28:06:09 not available", "not_found"),
                          ("org.freedesktop.DBus.Error.AccessDenied: Rejected send message", "bt_permission"),
                          ("connect failed: Permission denied (13)", "not_bondable"),
                          ("Failed to register agent object", "agent"),
                          ("this robot cannot enter a PIN", "pin"),
                          ("something odd", "other")]:
            with self.subTest(raw=raw):
                self.assertEqual(P.classify(raw), code)
        for code in P.FAILS:
            e = P.explain(code)
            self.assertTrue(e["msg"] and e["fix"], code)

    def test_bluetoothd_down_is_not_no_adapter(self):
        bt = P.BluetoothCtl(run=lambda argv, timeout=8.0: (124, "Waiting to connect to bluetoothd..."), rfkill_dir="/nonexistent")
        a = bt.adapter()
        self.assertEqual(a["code"], "bluez_down")

    def test_gamepad_rules_match_configd(self):
        self.assertEqual(P.gamepad_evidence("x", icon="input-gaming"), "classified")
        self.assertEqual(P.gamepad_evidence("x", cls=0x002508), "classified")
        self.assertEqual(P.gamepad_evidence("x", cls=0x002540), None)          # 键盘
        self.assertEqual(P.gamepad_evidence("x", appearance=0x03C4), "classified")
        self.assertEqual(P.gamepad_evidence("BLE Controller_280609"), "name")
        self.assertIsNone(P.gamepad_evidence("Galaxy Buds", icon="audio-headset"))
        self.assertTrue(P.same_pad(PRO, PRO_LE))
        self.assertFalse(P.same_pad(PRO, XBOX))
        self.assertEqual([P.pad_kind(n) for n in ("Xbox Wireless Controller", "DualSense Wireless Controller",
                                                  "Wireless Controller", "Pro Controller", "GameSir-Nova 2 Lite")],
                         ["xbox", "ps", "ps", "switch", "generic"])

    def test_bluetoothctl_lines(self):
        line = "\x01\x1b[0;94m\x02[Pro Controller]\x01\x1b[0m\x02# [CHG] Device 98:B6:E9:28:06:09 RSSI: -61\r"
        self.assertEqual(P.clean_line(line), "[CHG] Device 98:B6:E9:28:06:09 RSSI: -61")
        p = P.parse_props("\tName: Pro Controller\n\tRSSI: 0xffffffc3 (-61)\n\tAppearance: 0x03c4\n\tUUID: A\n\tUUID: B\n")
        d = P.device_from_props(PRO, p)
        self.assertEqual((d["rssi"], d["appearance"], p["uuids"]), (-61, 0x03C4, ["A", "B"]))
        self.assertTrue(math.isfinite(d["rssi"]))

    def test_robot_view_from_a_robotd_like_object(self):
        from studio.robotd import Sample

        class Rd:
            present, paused = True, None
            bus, policies, state, health, pad = Sample(), Sample(), Sample(), Sample(), Sample()
            estop = type("E", (), {"active": False, "press_count": 3})()
        rd = Rd()
        rd.bus.set(bus(policy_enabled=True, homed=True))
        rd.policies.set(dict(POLICIES))
        rd.state.set({"move": {"applied": [0.2, 0, 0]}})
        rd.health.set({"battery": {"percent": 55}})
        v = P.robot_view(rd)
        self.assertEqual((v["posture"], v["estop_presses"], v["battery_pct"]), ("walking", 3, 55))
        self.assertEqual(v["policies"]["skills"], POLICIES["skills"])


# ---------------- 真端口：本机 TCP 假服务（审查 20） ----------------

class FakeServer:
    """一个本机 TCP 服务：每来一个连接，在线程里跑 handler(sock)。"""

    def __init__(self, handler):
        self.handler = handler
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.addr = f"tcp:127.0.0.1:{self.srv.getsockname()[1]}"
        self.conns = 0
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns += 1
            threading.Thread(target=self._run, args=(c,), daemon=True).start()

    def _run(self, c):
        try:
            self.handler(c)
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.alive = False
        self.srv.close()


def read_line(c):
    buf = b""
    while not buf.endswith(b"\n"):
        x = c.recv(4096)
        if not x:
            break
        buf += x
    return buf


def free_port_addr():
    s = socket.create_server(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"tcp:127.0.0.1:{port}"


class PortTest(unittest.TestCase):
    def test_rpc_call_skips_junk_and_maps_errors(self):
        def ok(c):
            read_line(c)
            c.sendall(b'[1,2]\n"x"\ngarbage\n{"jsonrpc":"2.0","method":"note"}\n{"jsonrpc":"2.0","id":1,"result":{"ok":1}}\n')
        srv = FakeServer(ok)
        self.assertEqual(P.ConfigdPort(srv.addr).call("pad.status", {}, 2.0), {"ok": 1})
        srv.close()

        def err(c):
            read_line(c)
            c.sendall(b'{"jsonrpc":"2.0","id":1,"error":{"code":14,"message":"may not change"}}\n')
        srv = FakeServer(err)
        with self.assertRaises(RpcError) as cm:
            P.rpc_call(srv.addr, "pad.pair", {}, 2.0)
        self.assertEqual(cm.exception.code, 14)
        srv.close()

        def err_str(c):
            read_line(c)
            c.sendall(b'{"jsonrpc":"2.0","id":1,"error":"bad"}\n')
        srv = FakeServer(err_str)
        with self.assertRaises(RpcError) as cm:
            P.rpc_call(srv.addr, "x", {}, 2.0)
        self.assertEqual(cm.exception.message, "bad")
        srv.close()

        srv = FakeServer(lambda c: read_line(c))           # 收了就关：没回话
        with self.assertRaises(Unreachable):
            P.rpc_call(srv.addr, "x", {}, 2.0)
        srv.close()

        srv = FakeServer(lambda c: (read_line(c), time.sleep(1.0)))
        with self.assertRaises(Timeout):
            P.rpc_call(srv.addr, "x", {}, 0.3)
        srv.close()

        with self.assertRaises(Unreachable):
            P.rpc_call("unix:/nonexistent/configd.sock", "x", {}, 1.0)     # 鸭子上：socket 不在
        with self.assertRaises((Unreachable, Timeout)):                    # 没人听的端口：Linux 立刻拒绝，Windows 要等超时
            P.rpc_call(free_port_addr(), "x", {}, 1.0)

    def test_permission_error_is_its_own_kind(self):
        with mock.patch("socket.create_connection", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(P.NoPermission) as cm:
                P.rpc_call("tcp:127.0.0.1:1", "x", {}, 1.0)
        self.assertIn("robot 组", str(cm.exception))

    def test_padd_tap_reports_and_survives_callback_errors(self):
        def tap(c):
            read_line(c)
            dev = {"name": "Pro Controller", "axes": [], "buttons": []}
            c.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"accepted": True}}) + "\n"
                       + "[1]\nnot json\n"
                       + json.dumps({"method": "pad.report", "params": {"report": "attached", "device": dev}}) + "\n"
                       + json.dumps({"method": "pad.report", "params": "nope"}) + "\n"
                       + json.dumps({"method": "pad.report", "params": {"report": "frame", "events": []}}) + "\n").encode())
            time.sleep(0.2)
        srv = FakeServer(tap)
        reports, states, boom = [], [], {"n": 0}

        def on_report(r):
            if r.get("report") == "attached" and boom["n"] == 0:
                boom["n"] += 1
                raise ValueError("回调里出错")
            reports.append(r)
        tmp = tempfile.TemporaryDirectory()
        log = Log(Path(tmp.name), echo=False, background=False)
        t = P.PaddTap(srv.addr, reconnect_s=0.05, log=log)
        t.start(on_report, lambda s, why=None: states.append(s))
        try:
            wait_until(lambda: sum(1 for r in reports if r.get("report") == "attached") >= 1, what="重连后又收到 attached")
            self.assertTrue(t.alive())
            self.assertIn("error", states)
            self.assertIn("waiting", states)
            self.assertTrue(any(r.get("report") == "frame" for r in reports))
            self.assertTrue(any("读手柄按键的线程出错" in e["msg"] for e in log.recent(20)))
        finally:
            t.stop()
            srv.close()
            tmp.cleanup()

    def test_padd_tap_absent_and_refused_subscription(self):
        states = []
        t = P.PaddTap("unix:/nonexistent/pad.sock", reconnect_s=0.05)
        t.start(lambda r: None, lambda s, why=None: states.append((s, why)))
        wait_until(lambda: states, what="报 absent")
        t.stop()
        self.assertEqual(states[0][0], "absent")

        def refuse(c):
            read_line(c)
            c.sendall(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"no such method"}}\n')
            time.sleep(0.2)
        srv = FakeServer(refuse)
        states = []
        t = P.PaddTap(srv.addr, reconnect_s=0.5)
        t.start(lambda r: None, lambda s, why=None: states.append((s, why)))
        wait_until(lambda: any(s == "error" for s, _ in states), what="拒绝订阅")
        t.stop()
        srv.close()
        self.assertIn("no such method", next(w for s, w in states if s == "error"))

    def test_robot_port_notify(self):
        got = []

        def sink(c):
            buf = b""
            while True:
                x = c.recv(4096)
                if not x:
                    break
                buf += x
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    got.append(json.loads(line))
        srv = FakeServer(sink)
        rp = P.RobotPort(srv.addr)
        rp.notify("robot.move", {"vx": 0.1, "vy": 0, "vyaw": 0})
        rp.notify("robot.mouth", {"open": 0.5})
        wait_until(lambda: len(got) == 2, what="收到两条通知")
        self.assertEqual(got[0], {"jsonrpc": "2.0", "method": "robot.move", "params": {"vx": 0.1, "vy": 0, "vyaw": 0}})
        self.assertNotIn("id", got[1])                         # 通知不带 id
        self.assertEqual(srv.conns, 1)                          # 常驻连接
        rp._close()
        srv.close()
        with self.assertRaises((Unreachable, Timeout)):
            P.RobotPort(free_port_addr()).notify("robot.move", {"vx": 0})
        with self.assertRaises(Unreachable):
            P.RobotPort("unix:/nonexistent/robotd.sock").notify("robot.move", {"vx": 0})


# ---------------- 总览重排（overview-merge-plan.md §5、审查 overview-merge-review.md O1、O6、O7、O12、O16） ----------------

class OverviewMergeTest(PadTestBase):
    """手柄并进总览以后的后端规矩。借 VpadTest 的几个小工具（不继承它：继承的话它的测试会再跑一遍）。"""
    feed, sent, s, into, stick = VpadTest.feed, VpadTest.sent, VpadTest.s, VpadTest.into, VpadTest.stick

    def setUp(self):
        super().setUp()
        self._n = itertools.count(1000)

    def test_new_connection_is_not_in_pad_area_until_open(self):
        """O1：新连接默认「不在手柄区」——没发过 drive {open} 的页面，摇杆、按住的键、模式一律丢，动作拿不到方向盘。"""
        self.feed()
        c = self.ops.new_conn()
        r = self.stick("drive", conn=c, ly=-1)
        self.assertIn("dropped", r)
        self.assertEqual(self.sent("robot.move"), [])
        self.assertIn("dropped", self.ops.handle({"op": "keys", "held": ["lt"], "down": ["lt"], "seq": self.s()}, conn=c))
        self.assertIn("dropped", self.into("head", conn=c))
        with self.assertRaises(P.Refused) as cm:
            self.ops.handle({"op": "action_start", "id": "nod"}, by="电脑 A", conn=c)
        self.assertIn("不在手柄这一块", str(cm.exception))
        time.sleep(0.15)
        self.assertEqual([m for m, _ in self.sent() if m in ("robot.move", "robot.mouth", "robot.sound", "robot.head")], [])
        # 一次性命令照旧不看 closed（三 V5）：「停下（站好）」谁都能点
        self.ops.handle({"op": "walk_stop"}, by="电脑 A", conn=c)
        self.assertIn(("robot.enable", {"on": False, "toggle": False}), self.robot.calls)
        # 发了 open（手柄那一块到了屏幕上）才收
        self.feed()
        self.ops.handle({"op": "drive", "open": True, "seq": self.s()}, conn=c)
        self.assertEqual(self.stick("drive", conn=c, ly=-1)["sent"][0], 0.3)

    def test_scroll_out_then_back(self):
        """O1：滚出手柄区（drive off）：按着的摇杆发一次 0、之后晚到的帧都丢；滚回来（open）接着能用。"""
        self.feed()
        c = self.ops.new_conn()
        self.ops.handle({"op": "drive", "open": True, "seq": self.s()}, conn=c)
        late = self.s()                                                          # 滚出去之前就发出的一帧，晚到
        self.stick("drive", conn=c, ly=-1)
        self.ops.handle({"op": "drive", "on": False, "seq": self.s()}, conn=c)
        self.assertEqual(self.sent("robot.move")[-1][1], {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
        self.assertIn("dropped", self.stick("drive", conn=c, seq=late, ly=-1))
        self.assertIn("dropped", self.stick("drive", conn=c, ly=-1))
        self.assertTrue(any("离开了手柄区" in e["msg"] for e in self.log.recent(30)))
        self.ops.handle({"op": "drive", "open": True, "seq": self.s()}, conn=c)
        self.assertEqual(self.stick("drive", conn=c, ly=-1)["sent"][0], 0.3)

    def test_estop_pass_is_handed_to_checklist_with_pad_and_build(self):
        """O6：测急停过了，记下是哪只手柄（MAC）、哪个版本的主程序测的。"""
        got = []
        self.ops.on_estop_passed = got.append
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.watch()
        self.tap.attach(XBOX_DEV)
        wait_until(lambda: self.ops.snapshot()["estop"]["ok"], what="急停四条都 ✓")
        self.robot_ok(bus=bus(build="1fa8438-feetech-ft6-control.43"))
        self.ops.handle({"op": "estop_test", "action": "start"})
        wait_until(lambda: self.ops.snapshot()["estop_test"]["state"] == "armed", what="等按 Select")
        self.tap.key(0x13A, True)
        wait_until(lambda: self.ops.test.get("_on_while_pressed") is not None, what="按着时问过扭矩")
        self.tap.key(0x13A, False)
        time.sleep(0.05)
        self.robot_ok(torque_on=False, bus=bus(False, build="1fa8438-feetech-ft6-control.43"))
        wait_until(lambda: got, what="交给上手清单")
        self.assertEqual(got[0]["mac"], XBOX)
        self.assertEqual(got[0]["name"], "Xbox Wireless Controller")
        self.assertEqual(got[0]["build"], "1fa8438-feetech-ft6-control.43")
        self.assertIsInstance(got[0]["ms"], int)
        # 钩子出错只记日志，测急停照样算过
        self.ops.on_estop_passed = lambda r: 1 / 0
        self.ops._estop_passed_record()
        self.assertTrue(any("记「测急停过了」时出错" in e["msg"] for e in self.log.recent(20)))

    def test_onboard_view_uses_same_status_and_estop_as_snapshot(self):
        """O7：清单第 3、4 步跟手柄快照用同一份 status / estop。"""
        self.cfg.connect_pad(XBOX, "Xbox Wireless Controller")
        self.ops.handle({"op": "refresh"})
        v, snap = self.ops.onboard_view(), self.ops.snapshot()
        self.assertEqual(v["pads"], snap["status"]["pads"])
        self.assertEqual(v["active"]["mac"], XBOX)
        self.assertEqual(v["estop"]["state"], snap["estop"]["state"])
        self.assertFalse(v["attached"])
        self.watch()
        self.tap.attach(XBOX_DEV)
        self.assertEqual(self.ops.onboard_view()["device"]["unique"], XBOX_DEV["unique"])

    def test_attached_logged_only_when_device_changes(self):
        """O16：滚出去 6 秒（旁路关了）再滚回来又是一次 attached：同一个设备不再记一行。"""
        self.watch()
        for _ in range(3):
            self.tap.attach(XBOX_DEV)
        self.tap.attach(PRO_DEV)
        msgs = [e["msg"] for e in self.log.recent(50) if e["msg"].startswith("padd 打开了手柄")]
        self.assertEqual(len(msgs), 2)

    def test_c7_action_hold_only_counts_for_the_same_press(self):
        """代码审查 C7：页面上一次按下留下的定时器（带旧的 press）续不了这一次按下的命；老页面不带 press 照旧。"""
        self.robot_ok(posture="policy", policies=POLICIES)
        self.ops.handle({"op": "action_start", "id": "nod", "press": 1}, by="电脑 A", conn="a")
        self.assertEqual(self.ops.handle({"op": "action_hold", "press": 1}, conn="a")["running"], "nod")
        self.ops.handle({"op": "action_start", "id": "nod", "press": 2}, by="电脑 A", conn="a")   # 松开又按下
        held = self.ops._action["hold"]
        time.sleep(0.02)
        self.assertIsNone(self.ops.handle({"op": "action_hold", "press": 1}, conn="a")["running"])
        self.assertEqual(self.ops._action["hold"], held)                       # 旧的没续上
        self.assertEqual(self.ops.handle({"op": "action_hold", "press": 2}, conn="a")["running"], "nod")
        self.assertEqual(self.ops.handle({"op": "action_hold"}, conn="a")["running"], "nod")   # 不带序号（老页面）
        self.ops.handle({"op": "action_stop"}, conn="a")

    def test_tap_skips_pad_imu_lines_before_parsing(self):
        """O12：手柄自带 IMU 的那些行，按字符串先跳过，不 json.loads。"""
        imu = json.dumps({"jsonrpc": "2.0", "method": "pad.report", "params": {"report": "imu", "samples": [[0, 0, 0]] * 3}},
                         separators=(",", ":"))
        att = json.dumps({"jsonrpc": "2.0", "method": "pad.report", "params": {"report": "imu_attached", "device": {"name": "imu"}}},
                         separators=(",", ":"))

        def tap(c):
            read_line(c)
            c.sendall(('{"jsonrpc":"2.0","id":1,"result":{"accepted":true}}\n' + (imu + "\n") * 20 + att + "\n").encode())
            time.sleep(0.3)
        srv = FakeServer(tap)
        reports, parsed = [], []
        real = json.loads

        def counting(s, *a, **k):
            parsed.append(s)
            return real(s, *a, **k)
        t = P.PaddTap(srv.addr, reconnect_s=5)
        with mock.patch.object(P.json, "loads", counting):
            t.start(reports.append, lambda s, why=None: None)
            try:
                wait_until(lambda: any(r.get("report") == "imu_attached" for r in reports), what="收到 imu_attached")
            finally:
                t.stop()
                srv.close()
        self.assertFalse(any(r.get("report") == "imu" for r in reports))
        self.assertFalse(any(b'"report":"imu"' in (s if isinstance(s, bytes) else s.encode()) for s in parsed))


if __name__ == "__main__":
    unittest.main()
