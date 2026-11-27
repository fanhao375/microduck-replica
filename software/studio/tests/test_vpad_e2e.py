"""网页手柄（手柄页第三轮：图上的键能按）的端到端：假 robotd + 真 Studio + 无头 Edge。

tests/vpad_e2e.mjs 在页面上点图上的键、拖摇杆、按键盘、手机上两个手指一起拖，把假 robotd 每一步收到的东西打出来；
这里核对「点了什么 → robotd 收到什么」（pad-vpad-plan.md §8 / 审查 pad-vpad-review.md 的 V1–V7、V16、V17）。

要电脑上有 Node（≥ 22，自带 WebSocket）和 Edge；没有（比如鸭子上）就跳过。设 STUDIO_E2E=0 也跳过。
端口默认 Studio 18090、假 robotd 17021 / configd 17022、Edge 调试口 9343，被占了就换空闲的（不碰别人在跑的）。
VPAD_E2E_DIR=目录：日志、Edge 的 profile、假 robotd 的记录放这里（默认临时目录，跑完删）；VPAD_SHOTS=目录：顺手截图。
单独跑：
    py -3.13 -I -c "import sys;sys.path[:0]=['deps','.'];import unittest;unittest.main(module=None,argv=['x','-v','tests.test_vpad_e2e'])"
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NODE = shutil.which("node")
EDGE = next((p for p in (os.environ.get("STUDIO_EDGE"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", shutil.which("msedge"),
                         shutil.which("microsoft-edge")) if p and os.path.exists(p)), None)
HEAD0 = {"neck_pitch": 0, "head_pitch": 0, "head_yaw": 0, "head_roll": 0}


def _node_ok():
    if not NODE:
        return False
    try:
        v = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().lstrip("v")
        return int(v.split(".")[0]) >= 22
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _port(want):
    """想要的端口空着就用它，被占了（别人的 Studio / Edge）换一个空闲的。"""
    for p in (want, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("找不到空闲端口")


def _wait_http(url, what, timeout=20):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机地址：不走系统代理
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with opener.open(url, timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"{timeout} 秒没等到 {what}（{url}）")


def _kill(p):
    if p is None or p.poll() is not None:
        return
    if sys.platform == "win32":     # Edge 会起一串子进程：整棵树一起关
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


def moves(recs):
    return [r["params"] for r in recs if r["method"] == "robot.move"]


def nonzero(p):
    return any(abs(v) > 1e-6 for v in p.values() if isinstance(v, (int, float)))


@unittest.skipUnless(_node_ok() and EDGE and os.environ.get("STUDIO_E2E", "1") != "0",
                     "没有 Node ≥ 22 或者 Edge（或者设了 STUDIO_E2E=0）：跳过网页手柄的端到端测试")
class VpadE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keep = os.environ.get("VPAD_E2E_DIR")
        cls.dir = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="vpad-e2e-"))
        cls.dir.mkdir(parents=True, exist_ok=True)
        cls.procs = []
        rec = cls.dir / "robotd-recv.jsonl"
        rec.unlink(missing_ok=True)
        p_rd, p_cfg, p_web, p_cdp = _port(17021), _port(17022), _port(18090), _port(9343)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = lambda name: open(cls.dir / name, "w", encoding="utf-8")   # noqa: E731
        try:
            cls.procs.append(subprocess.Popen(
                [sys.executable, "-I", str(ROOT / "dev" / "fake_robotd.py"), "--fixture", str(HERE / "fixtures" / "now-20261018.jsonl"),
                 "--robotd-port", str(p_rd), "--configd-port", str(p_cfg), "--posture", "policy", "--record", str(rec)],
                cwd=ROOT, env=env, stdout=out("fake_robotd.out"), stderr=subprocess.STDOUT))
            cls.procs.append(subprocess.Popen(
                [sys.executable, "-I", str(ROOT / "run.py"), "--listen", f"127.0.0.1:{p_web}", "--robotd", f"tcp:127.0.0.1:{p_rd}",
                 "--configd", f"tcp:127.0.0.1:{p_cfg}", "--duck-label", "② 二号鸭", "--dev-autologin", "--servo-sim",
                 "--state-dir", str(cls.dir / "state"), "--log-dir", str(cls.dir / "logs")],
                cwd=ROOT, env=env, stdout=out("studio.out"), stderr=subprocess.STDOUT))
            _wait_http(f"http://127.0.0.1:{p_web}/healthz", "Studio")
            cls.procs.append(subprocess.Popen(
                [EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={p_cdp}", f"--user-data-dir={cls.dir / 'edge'}",
                 "--no-first-run", "--no-default-browser-check", "about:blank"],
                stdout=out("edge.out"), stderr=subprocess.STDOUT))
            _wait_http(f"http://127.0.0.1:{p_cdp}/json/version", "无头 Edge 的调试口")
            args = [NODE, str(HERE / "vpad_e2e.mjs"), "--url", f"http://127.0.0.1:{p_web}/#/pad", "--cdp", str(p_cdp),
                    "--record", str(rec), "--fake", str(p_cfg)]
            if os.environ.get("VPAD_SHOTS"):
                args += ["--shots", os.environ["VPAD_SHOTS"]]
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=ROOT)
            (cls.dir / "vpad_e2e.err").write_text(r.stderr or "", encoding="utf-8")
            if r.returncode != 0:
                raise AssertionError(f"Node 脚本出错（日志在 {cls.dir}）：{(r.stderr or '')[-3000:]}")
            cls.out = json.loads(r.stdout)
            (cls.dir / "vpad_e2e.json").write_text(r.stdout, encoding="utf-8")
        finally:
            for p in reversed(cls.procs):
                _kill(p)
        if not keep:
            shutil.rmtree(cls.dir, ignore_errors=True)

    def recs(self, step):
        return self.out["steps"][step]["recs"]

    def test_page_has_no_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_sticks_are_relative_and_release_stops(self):
        self.assertFalse(any(nonzero(p) for p in moves(self.recs("stick_press_only"))), "按下不动就不该走（相对摇杆，V2）")
        mv = moves(self.recs("stick_drag"))
        self.assertGreaterEqual(sum(1 for p in mv if p["vx"] > 0.15), 3)
        self.assertTrue(all(r["notify"] for r in self.recs("stick_drag")))
        self.assertEqual(mv[-1], {"vx": 0, "vy": 0, "vyaw": 0})
        self.assertTrue(all(p["vy"] == 0 and p["vyaw"] == 0 for p in mv))

    def test_a_is_one_skill_request(self):
        self.assertEqual(self.recs("key_a"), [{"method": "robot.do", "params": {"skill": "ground_pick"}, "notify": False}])

    def test_x_hold_rolls_again_and_again(self):
        """方案 C（V4）：按下发一次请求，按住每 40 ms 一次通知。"""
        rs = self.recs("x_hold")
        self.assertEqual(rs[0], {"method": "robot.do", "params": {"skill": "roulade"}, "notify": False})
        self.assertGreaterEqual(sum(1 for r in rs[1:] if r["method"] == "robot.do" and r["notify"]), 8)
        self.assertTrue(all(r["params"] == {"skill": "roulade"} for r in rs))

    def test_lt_wheee_and_mouth_close(self):
        rs = self.recs("lt_hold")
        mouths = [r["params"] for r in rs if r["method"] == "robot.mouth"]
        sounds = [r["params"] for r in rs if r["method"] == "robot.sound"]
        self.assertGreaterEqual(mouths.count({"open": 1}), 5)
        self.assertGreaterEqual(sounds.count({"tag": "wheee", "hold": True}), 5)
        self.assertEqual(mouths[-1], {"open": 0})                       # 结束一定合嘴
        self.assertEqual(sounds[-1], {"tag": "wheee", "hold": False})   # 松开发 hold:false
        self.assertTrue(all(r["notify"] for r in rs))

    def test_rt_chirps_once(self):
        rs = self.recs("rt_tap")
        self.assertEqual([r["params"] for r in rs if r["method"] == "robot.sound"], [{"tag": "chirp"}])
        self.assertEqual([r["params"] for r in rs if r["method"] == "robot.mouth"][-1], {"open": 0})

    def test_head_mode_moves_head_not_body(self):
        rs = self.recs("head")
        heads = [r["params"] for r in rs if r["method"] == "robot.head"]
        self.assertGreaterEqual(sum(1 for h in heads if h["neck_pitch"] > 1.0), 3)   # 右摇杆往上 = 脖子（附 2）
        self.assertEqual(heads[-1], HEAD0)                                            # 松手 / 退出：头回正
        self.assertFalse(any(nonzero(p) for p in moves(rs)), "头模式里不该走")
        self.assertNotIn("robot.pose", [r["method"] for r in rs])

    def test_body_mode_crouch_then_exit_with_active_false(self):
        poses = [r["params"] for r in self.recs("body") if r["method"] == "robot.pose"]
        self.assertTrue(any(p["z"] < -0.01 and p["active"] is True for p in poses if "z" in p))   # 往下推 = 蹲
        self.assertIn({"z": 0, "roll": 0, "pitch": 0, "active": True}, poses)                     # 松手回中
        self.assertEqual(poses[-1], {"active": False})                                            # 退出：一定写 active:false
        self.assertTrue(all("active" in p for p in poses))

    def test_keyboard_only_when_focused_and_no_arrows(self):
        self.assertEqual(moves(self.recs("keyboard_unfocused")), [])
        st = self.out["steps"]["keyboard"]
        self.assertTrue(st["focused"])
        w = moves(st["w_recs"])
        self.assertGreaterEqual(sum(1 for p in w if p["vx"] == 0.3), 2)
        self.assertEqual(w[-1], {"vx": 0, "vy": 0, "vyaw": 0})
        self.assertFalse(any(nonzero(p) for p in moves(self.recs("keyboard"))), "方向键不该让鸭子走")

    def test_blur_stops_even_while_still_pressed(self):
        st = self.out["steps"]["blur"]
        self.assertTrue(any(nonzero(p) for p in moves(st["before"])))
        self.assertIn({"vx": 0, "vy": 0, "vyaw": 0}, moves(st["atBlur"]))
        self.assertFalse(any(nonzero(p) for p in moves(st["after"])), "失焦以后手还按着也不该再走")

    def test_start_follows_posture(self):
        self.assertIn("停下", self.out["start0"])
        self.assertEqual([(r["method"], r["params"]) for r in self.recs("start_stop") if not r["notify"]],
                         [("robot.stop", {}), ("robot.enable", {"on": False, "toggle": False})])
        self.assertEqual([(r["method"], r["params"]) for r in self.recs("start_walk") if not r["notify"]],
                         [("robot.enable", {"on": True, "toggle": False})])

    def test_select_relaxes_on_press(self):
        """V3：图上的 Select 按下就卸力（松手之前 robotd 已经收到了）；镜像时也能按。"""
        self.assertIn("robot.relax", [r["method"] for r in self.out["steps"]["select"]["whilePressed"]])
        self.assertIn("robot.relax", [r["method"] for r in self.recs("mirror_select")])

    def test_dpad_up_needs_a_3s_hold_like_official(self):
        """十字上照官方：按不到 3 秒不切；按满 3 秒发 robot.setMode（要切到的那个），主程序拒了页面写原因。"""
        def modes(step):
            return [r["params"] for r in self.recs(step) if r["method"] == "robot.setMode"]
        self.assertEqual(modes("dpad_up_short"), [])
        self.assertIn("没按够 3 秒", self.out["du_short"])
        self.assertEqual(modes("dpad_up_refused"), [{"mode": "roller"}])
        self.assertEqual(modes("dpad_up_hold"), [{"mode": "roller"}])
        self.assertEqual(modes("dpad_up_back"), [{"mode": "walk"}])

    def test_dpad_right_reboots_with_one_press(self):
        """十字右照官方：按一下就发 robot.rebootMotors（空 = 全部），不用确认。"""
        self.assertEqual([(r["method"], r["params"]) for r in self.recs("dpad_right") if not r["notify"]],
                         [("robot.rebootMotors", {"ids": []})])

    def test_leaving_page_exits_body_mode_and_coming_back_works(self):
        poses = [r["params"] for r in self.recs("leave_page") if r["method"] == "robot.pose"]
        self.assertEqual(poses[-1], {"active": False})
        self.assertTrue(any(p["vx"] > 0.15 for p in moves(self.recs("back_again"))), "回到手柄页以后摇杆不管用了（V5 open）")

    def test_binding_controls_and_emergency_button_fit_mobile_and_desktop(self):
        for layout in self.out['binding_layouts']:
            with self.subTest(width=layout['width']):
                self.assertLessEqual(layout['scroll'], layout['width'])
                self.assertTrue(layout['stopVisible'])
                self.assertTrue(layout['autoDisabled'], 'legacy daemon lacks autonomous RPC')
                self.assertIn('yes', layout['choices'])
                self.assertNotIn('autonomous', layout['choices'])
                self.assertNotIn('laser_track', layout['choices'])

    def test_mirror_sends_nothing(self):
        """V16：实体手柄连着，图上点的、拖的、按的都不发。"""
        self.assertEqual(self.recs("mirror"), [])
        self.assertIn("实体手柄连着", self.out["steps"]["mirror"]["bar"])

    def test_phone_two_fingers_and_no_side_scroll(self):
        both = [p for p in moves(self.recs("two_fingers")) if p["vx"] > 0.1 and p["vyaw"] < -0.3]
        self.assertGreaterEqual(len(both), 2, "两个手指一起拖：前进和右转要在同一帧里")
        self.assertEqual(moves(self.recs("two_fingers"))[-1], {"vx": 0, "vy": 0, "vyaw": 0})
        for k in ("phone", "phone_mirror"):
            self.assertLessEqual(self.out[k]["sw"], self.out[k]["cw"], f"{k}：手机宽度能左右滑")


if __name__ == "__main__":
    unittest.main()
