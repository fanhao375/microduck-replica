"""出厂场景「写进主程序」（任务 13）的端到端：假 robotd（--mode commissioning --calib factory）+ 真 Studio + 无头 Edge。

流程：tests/commit_e2e.mjs 先在出厂状态看第 3 步的灰按钮和预览；然后 IMU 页粗校（假主程序模拟托起、低头、
verify-imu 成功）、舵机页一键中位（--servo-sim）、第 3 步写入。假小助手让假主程序切到 motion + control，
清单第 3 步变成完成、顶栏显示运动模式。最后再跑一次「标定被拒」：页面要看到原因，断线期间进度卡也不能丢。
IMU 页的「站姿使能 / 失能」（任务 17）：标定模式下灰着、能去第 3 步；运动模式下发的命令跟遥控卡「站起来」、顶上「卸力」一样。

电脑上没有 root 小助手：用临时启动脚本把 studio.helper.calib_commit 换成假实现（只在这一段里）。
要电脑上有 Node（≥ 22）和 Edge；没有（比如鸭子上）就跳过。设 STUDIO_E2E=0 也跳过。
端口默认 Studio 18091、假 robotd 17031 / configd 17032、Edge 调试口 9344，被占了就换空闲的。
COMMIT_E2E_DIR=目录：日志、Edge 的 profile 放这里（默认临时目录，跑完删）；COMMIT_SHOTS=目录：顺手截图。
单独跑：
    py -3.13 -I -c "import sys;sys.path[:0]=['deps','.','dev'];import unittest;unittest.main(module=None,argv=['x','-v','tests.test_commit_e2e'])"
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(ROOT / "deps"), str(ROOT / "dev")]

from tests.test_vpad_e2e import EDGE, NODE, _kill, _node_ok, _port, _wait_http  # noqa: E402

FIXTURE = HERE / "fixtures" / "now-20261018.jsonl"
STUCK_CALIB = "motion requires all 15 calibrated joints, measured limits and verified IMU mounting for the lsm6dsv16x IMU"

BOOT = r'''
import copy, json, os, socket, sys, time
sys.path[:0] = [ROOT, os.path.join(ROOT, "deps"), os.path.join(ROOT, "dev")]
from studio import helper
from studio import commit as C

helper.available = lambda: True
# 电脑上没有 root 小助手：进舵机页要调 bus-take，交还要调 bus-give-back；这里都当“已经做了”。
helper.flag_present = lambda: False
helper.bus_take = lambda by, allow_seated=False, timeout=30: (True, "taken", {})
helper.bus_give_back = lambda by, *, soft=True, marker=None, timeout=60: (True, "soft", {})
# 「更多」页的 SSH 卡片：出厂只认钥匙、一把钥匙都没有（跟公开镜像一样）
helper.ssh_status = lambda timeout=10: (True, "ssh_status", {"exit": 0, "result": "ssh_status", "password_login": False,
                                                            "ssh_active": True, "keys": 0, "user": "radxa"})


def fake_rpc(method, obj):
    port = int(os.environ["E2E_FAKE_PORT"])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": obj}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            part = s.recv(4096)
            if not part:
                break
            buf += part
    return json.loads(buf.split(b"\n", 1)[0])


def fake_set(obj):
    return fake_rpc("fake.set", obj)


# Only replaces the systemd/filesystem boundary. Real ImuBackend capture, solver,
# runtime verify and exact calibration readback still run against fake robotd.
redo_state = {"active": False, "phase": "idle", "session_id": None, "source": None}
redo_backup = None


def fake_imu_state(*, root="/"):
    state = copy.deepcopy(redo_state)
    try:
        with open(os.environ["E2E_OUTCOME_FILE"], encoding="utf-8") as f:
            phase = json.load(f).get("redo_phase")
        if state["active"] and phase in ("entering", "finishing", "cancelling"):
            state["phase"] = phase
    except (OSError, ValueError):
        pass
    return state


def fake_imu_recalibrate(by, action, *, confirmed=None, session_id=None, source=None, quat=None, timeout=180):
    global redo_state, redo_backup
    if action == "enter":
        redo_backup = fake_rpc("robot.busCommand", {"action": "calibration-status"})["result"]
        redo_state = {"active": True, "phase": "commissioning", "session_id": "e2e-imu-session",
                      "source": redo_backup.get("imu_source") or "bus"}
        fake_set({"mode": "commissioning", "imu_pose": "upright"})
        fake_rpc("robot.relax", {})
        time.sleep(0.25)
        return True, "entered", dict(redo_state, note="已回到标定模式，双手托住开始采集")
    if action == "status":
        return True, "status", dict(redo_state)
    if action not in ("finish", "cancel") or not redo_state["active"]:
        return False, "no_session", {"msg": "没有重标定会话"}
    current = fake_rpc("robot.busCommand", {"action": "calibration-status"})["result"]
    restored = copy.deepcopy(current if action == "finish" else redo_backup)
    restored["calibration"]["motion_enabled"] = redo_backup["calibration"]["motion_enabled"]
    fake_set({"calib_commit": {"calibration": restored, "outcome": "ok"}, "posture": "limp", "imu_pose": "upright"})
    redo_state = {"active": False, "phase": "idle", "session_id": None, "source": None}
    return True, "finished" if action == "finish" else "cancelled", dict(redo_state,
        mode="motion", readback_ok=True, note="新方向已保存，已恢复运动模式并保持卸力" if action == "finish" else "已恢复原方向和运动模式")


helper.imu_redo_state = fake_imu_state
helper.imu_recalibrate = fake_imu_recalibrate


def outcome():
    path = os.environ.get("E2E_OUTCOME_FILE")
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("outcome") or "ok"
    except (OSError, ValueError):
        return "ok"


def fake_calib_commit(by, joints, imu, dry_run=False, timeout=180, on_line=None, *, joint_mode="recorded"):
    cur = {"motion_enabled": False, "imu_mount_verified": False,
           "imu_mount_quat": list(C.DEFAULT_BUS_QUAT), "current_ma_per_count": 0.0, "units_verified": False,
           "imu_mounts": {}, "joints": [{"name": n, "id": C.JOINT_IDS[n], "zero_raw": 2048,
                                         "direction": -1, "calibrated": False} for n in C.JOINT_ORDER]}
    cal = C.build_calibration(cur, joints, imu, joint_mode)
    C.validate_calibration(cal, imu["source"])
    text = C.render_calibration(cal)
    if on_line:
        on_line({"step": "write", "msg": "假小助手：写好了"})
    if dry_run:
        return True, "preview", {"exit": 0, "toml": text}

    want = outcome()
    if want == "reject":
        # 先让假主程序切到 motion，但标定校验不过 -> waiting_for_bus；再模拟 robotd 重启，页面会短暂断线
        fake_set({"calib_commit": {"calibration": cal, "outcome": "reject"}})
        fake_set({"restart": 1.5})
        if on_line:
            on_line({"step": "wait", "msg": "假小助手：等主程序回来"})
        time.sleep(1.7)
        # 模拟回滚：文件、模式都换回出厂那份，再重启一次（真小助手也是这样把 robotd 拉回标定模式的）。
        fake_set({"mode": "commissioning", "calib": "factory"})
        fake_set({"restart": 1.0})
        time.sleep(1.2)
        msg = "标定没通过主程序的检查：" + %r + "；已经换回原来的文件和模式，又起了一次主程序"
        return False, "calib_rejected", {"exit": 5, "msg": msg, "rolled_back": True,
                                         "readback_ok": False, "mismatches": [],
                                         "bus_status": {"mode": "motion", "phase": "waiting_for_bus", "error": %r}}
    fake_set({"calib_commit": {"calibration": cal, "outcome": "ok"}})
    if on_line:
        on_line({"step": "done", "msg": "假小助手：写进去了，主程序切到运动模式"})
    return True, "committed", {"exit": 0, "toml": text, "mode": "motion", "phase": "control",
                               "readback_ok": True, "mismatches": [], "fake": True}


helper.calib_commit = fake_calib_commit
from studio.__main__ import main
main(sys.argv[1:])
''' % (STUCK_CALIB, STUCK_CALIB)


@unittest.skipUnless(_node_ok() and EDGE and os.environ.get("STUDIO_E2E", "1") != "0",
                     "没有 Node ≥ 22 或者 Edge（或者设了 STUDIO_E2E=0）：跳过「写进主程序」的端到端测试")
class CommitE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keep = os.environ.get("COMMIT_E2E_DIR")
        cls.dir = (Path(keep) if keep else Path(tempfile.mkdtemp(prefix="commit-e2e-"))).resolve()
        cls.dir.mkdir(parents=True, exist_ok=True)
        cls.state = cls.dir / "state"
        cls.state.mkdir(exist_ok=True)
        cls.outcome_file = cls.dir / "commit-outcome.json"
        p_rd, p_cfg, p_web, p_cdp = _port(17031), _port(17032), _port(18091), _port(9344)
        cls.p_web, cls.p_cfg = p_web, p_cfg
        env = dict(os.environ, PYTHONIOENCODING="utf-8", E2E_FAKE_PORT=str(p_cfg),
                   E2E_OUTCOME_FILE=str(cls.outcome_file))
        out = lambda name: open(cls.dir / name, "w", encoding="utf-8")   # noqa: E731
        boot = cls.dir / "e2e_boot.py"
        boot.write_text(f"ROOT = {str(ROOT)!r}\n" + BOOT, encoding="utf-8")
        cls.procs = {
            "fake": subprocess.Popen(
                [sys.executable, "-I", str(ROOT / "dev" / "fake_robotd.py"), "--fixture", str(FIXTURE),
                 "--robotd-port", str(p_rd), "--configd-port", str(p_cfg), "--mode", "commissioning",
                 "--posture", "commissioning", "--calib", "factory", "--record", str(cls.dir / "robotd-recv.jsonl")],
                cwd=ROOT, env=env, stdout=out("fake.out"), stderr=subprocess.STDOUT),
        }
        try:
            cls.procs["studio"] = subprocess.Popen(
                [sys.executable, "-I", str(boot), "--listen", f"127.0.0.1:{p_web}", "--robotd", f"tcp:127.0.0.1:{p_rd}",
                 "--configd", f"tcp:127.0.0.1:{p_cfg}", "--duck-label", "② 二号鸭", "--dev-autologin", "--servo-sim",
                 "--state-dir", str(cls.state), "--log-dir", str(cls.dir / "logs")],
                cwd=ROOT, env=env, stdout=out("studio.out"), stderr=subprocess.STDOUT)
            _wait_http(f"http://127.0.0.1:{p_web}/healthz", "Studio")
            edge_dir = cls.dir / "edge"
            shutil.rmtree(edge_dir, ignore_errors=True)      # 上一次没清理的 profile 会让 Edge 起得极慢
            edge_dir.mkdir(parents=True, exist_ok=True)         # Edge 自己建不出来（沙箱里会报 Lock file 不能创建）
            cls.procs["edge"] = subprocess.Popen(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-setuid-sandbox",
                 "--enable-unsafe-swiftshader", f"--remote-debugging-port={p_cdp}",
                 f"--user-data-dir={edge_dir}", "--no-first-run", "--no-default-browser-check", "about:blank"],
                stdout=out("edge.out"), stderr=subprocess.STDOUT)
            _wait_http(f"http://127.0.0.1:{p_cdp}/json/version", "Edge 调试口", timeout=60)
        except Exception:
            for p in cls.procs.values():
                _kill(p)
            raise
        cls.p_cdp = p_cdp

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", {}).values():
            _kill(p)
        if os.environ.get("COMMIT_E2E_DIR"):
            return
        shutil.rmtree(cls.dir, ignore_errors=True)

    def node(self, phase):
        args = [NODE, str(HERE / "commit_e2e.mjs"), "--url", f"http://127.0.0.1:{self.p_web}/",
                "--cdp", str(self.p_cdp), "--fake-port", str(self.p_cfg), "--phase", phase,
                "--control-file", str(self.outcome_file)]
        if os.environ.get("COMMIT_SHOTS"):
            args += ["--shots", os.environ["COMMIT_SHOTS"]]
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=220, cwd=ROOT)
        (self.dir / f"commit_e2e-{phase}.err").write_text(r.stderr or "", encoding="utf-8")
        if r.returncode != 0:
            raise AssertionError(f"Node 脚本（{phase}）出错（日志在 {self.dir}）：{(r.stderr or '')[-4000:]}")
        (self.dir / f"commit_e2e-{phase}.json").write_text(r.stdout, encoding="utf-8")
        return json.loads(r.stdout)

    def fake_set(self, obj):
        with socket.create_connection(("127.0.0.1", self.p_cfg), timeout=5) as s:
            s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "fake.set", "params": obj}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                part = s.recv(4096)
                if not part:
                    break
                buf += part
        return json.loads(buf.split(b"\n", 1)[0])

    def test_01_factory_preview(self):
        first = self.node("factory")
        self.assertGreaterEqual(first["factory"]["bad"], 1, first)
        self.assertTrue(first["factory"]["writeDisabled"], first["factory"])
        blob = first["factory"]["desc"] + json.dumps(first["factory"]["checks"], ensure_ascii=False)
        self.assertIn("舵机零位", blob)
        self.assertEqual(first["preview"]["jointRows"], 15, first["preview"])
        self.assertTrue(first["preview"]["imu"], first["preview"])
        self.assertIn("motion_enabled = true", first["preview"]["toml"])

    def test_01b_mark_servo_step_done(self):
        """任务 16：出厂场景第 1 步点「已完成」（主程序说没标：两步，第二下写后果）→ ✓，第 3 步准备情况「舵机零位」变绿。"""
        m = self.node("mark")
        self.assertEqual(m["before"]["check"], "bad", m)
        self.assertEqual(m["ask"], "确定标成已完成？主程序说标定没做完：标成已完成鸭子也站不起来", m)
        self.assertNotEqual(m["afterOne"], "完成", m)                          # 点一下不算
        self.assertEqual(m["after"]["row"], "完成", m)
        self.assertIn("你标了已完成", m["after"]["text"])
        self.assertIn("你标了第 1 步已完成：沿用主程序里每颗舵机原有的中位（方向照舵机页的写）", m["after"]["check"])
        self.assertEqual(m["errors"], [])

    def test_01d_ssh_card_on_the_more_page(self):
        """10-21「出了问题自己 SSH 修，不用重烧卡」：「更多」页有 SSH 卡片，写清出厂只认钥匙、没有默认密码；
        假小助手回「只认钥匙、0 把钥匙」（跟公开镜像出厂一样）。这只测试鸭子没设网页登录密码：提示先设，按钮灰着，没有密码框。"""
        r = self.node("ssh")
        s = r["ssh"]
        self.assertIn("SSH 远程登录", s["text"])
        self.assertIn("出厂只认钥匙、不认密码，也没有默认密码", s["text"])
        self.assertEqual(s["now"], "现在：只认钥匙（密码登录关着），一把钥匙都没登记")
        self.assertTrue(s["needWebPw"], s)
        self.assertEqual(s["inputs"], 0)
        self.assertTrue(s["onDisabled"])
        self.assertEqual(r["errors"], [])

    def test_01c_stance_buttons_in_commissioning(self):
        """用户 10-21「咱标定也能站姿不就好了吗」：标定模式下「站姿使能」能点，确认以后发 imu.op hold，
        主程序（fork hold-home）回站住，站姿卡写「标定模式（站着）」；「失能（卸力）」走 relax，卸完回软着。"""
        s = self.node("stance0")
        st = s["stance"]
        self.assertEqual(st["stand"], {"text": "站姿使能", "disabled": False}, st)
        self.assertEqual(st["relax"], {"text": "失能（卸力）", "disabled": False}, st)
        self.assertIn("现在：标定模式（软着）", st["now"])
        self.assertIsNone(st["why"], st)
        self.assertFalse(st["goCommit"], st)
        self.assertIn("标定模式也能站", st["text"])
        self.assertEqual(s["holdSent"], [{"name": "imu.op", "args": {"op": "hold"}}])
        self.assertEqual(len(s["dialogs"]), 1, s["dialogs"])
        self.assertIn("15 颗舵机会在 3 秒内一起转到站姿并锁住", s["dialogs"][0])
        self.assertIn("现在：标定模式（站着）", s["holding"]["now"])
        self.assertEqual(s["holding"]["stand"], {"text": "站姿使能", "disabled": True}, s["holding"])
        self.assertEqual(s["holding"]["why"], "已经站着了")
        self.assertEqual(s["relaxSent"], [{"name": "relax", "args": {}}])
        self.assertIn("现在：标定模式（软着）", s["after"]["now"])
        recv = [json.loads(x) for x in (self.dir / "robotd-recv.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        holds = [r for r in recv if r.get("method") == "robot.busCommand" and (r.get("params") or {}).get("action") == "hold-home"]
        self.assertEqual(len(holds), 1, holds)
        dirs = holds[0]["params"]["directions"]
        self.assertEqual(len(dirs), 15)
        self.assertEqual(holds[0]["params"]["duration_ms"], 3000)
        self.assertEqual(s["errors"], [])

    def test_02_imu_coarse_verify(self):
        imu = self.node("imu")
        self.assertIn("方向已确认", imu["imu"], imu)

    def test_02b_imu_coarse_verify_while_holding(self):
        result = self.node("imu-hold")
        self.assertIn("方向已确认", result["imu"], result)
        self.assertIn("可先点「站姿使能」", result["start"])
        for label in ("holdBefore", "holdAfter"):
            self.assertEqual(result[label]["torque_state"], "all_enabled", result[label])
            self.assertEqual(result[label]["hold"]["stage"], "holding", result[label])
            self.assertIs(result[label]["policy_motion_enabled"], False)
        self.assertEqual(result["cleaned"]["torque_state"], "off")
        self.assertEqual(result["errors"], [])

    def test_03_servo_center_then_commit(self):
        from studio import commit as C
        servo = self.node("servo")
        self.assertEqual(servo["planRows"], 15, servo)
        # --servo-sim 是演示舵机，后端不会把它记进 servo-centered.json（真机上真校才记）。
        # 这里补记 15 颗，模拟「在这只鸭子上用舵机页一键中位过」；真正的记录路径在 test_servo_* 里测。
        C.record_centered(str(self.state), list(C.JOINT_IDS.values()), "e2e 演示中位")
        time.sleep(1.6)                       # CommitService 最多 1 秒重读一次 state 文件

        second = self.node("commit")
        self.assertEqual(second["ready"]["bad"], 0, second["ready"])
        self.assertFalse(second["ready"]["writeDisabled"], second["ready"])
        self.assertIn("会重启主程序，鸭子会先卸力，扶好鸭子", second["confirmText"])
        self.assertEqual(second["done"]["rowState"], "完成", second["done"])
        self.assertIn("运动模式", second["topbar"], second)
        self.assertEqual(second["errors"], [])

    def test_03b_stance_buttons_in_motion(self):
        """任务 17：运动模式软着时「站姿使能」能点，发出的命令跟遥控卡「站起来」一样（pad.op stand → robot.init）；
        「失能（卸力）」跟顶上「卸力」一样（relax）。"""
        s = self.node("stance")
        self.assertEqual(s["before"]["stand"], {"text": "站姿使能", "disabled": False}, s["before"])
        self.assertFalse(s["before"]["relax"]["disabled"], s["before"])
        self.assertIn("现在：软着", s["before"]["now"])
        self.assertFalse(s["before"]["goCommit"])
        self.assertEqual(s["standSent"], [{"name": "pad.op", "args": {"op": "stand"}}])
        self.assertEqual(s["remoteSent"], s["standSent"])
        self.assertEqual(s["relaxSent"], [{"name": "relax", "args": {}}])
        recv = [json.loads(x) for x in (self.dir / "robotd-recv.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        methods = [r.get("method") for r in recv]
        self.assertGreaterEqual(methods.count("robot.init"), 2, methods[-40:])      # IMU 页一次、遥控卡一次
        self.assertIn("robot.relax", methods)
        self.assertEqual(s["errors"], [])

    def test_04_failure_rollback_is_visible(self):
        self.outcome_file.write_text(json.dumps({"outcome": "reject"}), encoding="utf-8")
        self.fake_set({"mode": "commissioning"})          # 回到标定模式再写一次
        time.sleep(1.6)
        failed = self.node("fail")
        self.assertTrue(failed["offline"], failed.get("offline"))
        self.assertIn("主程序在重启", failed["offline"]["text"])
        self.assertEqual(failed["failed"]["rowState"], "该做", failed["failed"])
        self.assertTrue(any("标定没通过主程序的检查" in m for m in failed["failed"]["msgs"]), failed["failed"])
        self.assertEqual((failed.get("wsCommit") or {}).get("job", {}).get("phase"), "failed", failed.get("wsCommit"))
        self.outcome_file.write_text(json.dumps({"outcome": "ok"}), encoding="utf-8")

    def test_03c_verified_imu_can_be_recalibrated(self):
        """HOME 入口保留运动模式；旧客户端打开的重标定会话可以继续、取消和恢复。"""
        self.fake_set({"mode": "motion", "calib": "seed", "posture": "limp", "imu_pose": "upright"})
        result = self.node("imu-redo")
        self.assertTrue(result["entryVisible"], result)
        self.assertEqual(result["beforeConfirm"], [], result)
        self.assertEqual(result["entryMode"], "motion", result)
        self.assertEqual(result["entryCommands"], [], result)
        self.assertFalse(result["active"]["stand"]["disabled"], result)
        self.assertFalse(result["active"]["relax"]["disabled"], result)
        self.assertIn("点击验证前，先把实物回正", result["verifyInstruction"])
        self.assertEqual(result["original"]["calibration"]["joints"], result["after"]["calibration"]["joints"])
        self.assertTrue(any(j["zero_raw"] != 2048 for j in result["after"]["calibration"]["joints"]))
        self.assertEqual(result["after"], result["cancelled"])
        self.assertIn("软着", result["afterStance"]["now"])
        self.assertEqual(result["motionCommands"], [])
        self.assertEqual(result["commissioningCommands"], [])
        self.assertEqual(result["recoveredPhases"], ["entering", "finishing", "cancelling"])
        self.assertEqual(result["errors"], [], result)


if __name__ == "__main__":
    unittest.main()
