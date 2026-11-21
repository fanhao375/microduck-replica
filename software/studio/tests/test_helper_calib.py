"""calib-read / calib-commit：写进主程序（deploy/microduck-helper）的 fork 真实形状测试。

电脑上跑，不碰真系统：沿用 test_helper_bus 的假 systemctl / 假 robotd / 临时根目录。
重点：先拿锁再查旗、标定模式 -> 删 drop-in -> motion+control+读回一致、两类 waiting_for_bus、
读回不一致、saved 写失败、回滚后 robotd 不回来、策略在跑拒绝、外来 --commissioning 拒绝、备份按序号留 10 组。

    py -3.13 -I -c "import sys;sys.path[:0]=['.','deps','dev'];import unittest;unittest.main(module=None,argv=['x','-v','tests.test_helper_calib'])"
"""
import copy
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from tests.test_helper_bus import FakeCtx, World, H, Robot


def current_calibration():
    joints = []
    for name in H.JOINT_ORDER:
        jid = H.JOINT_ID[name]
        joints.append({"name": name, "id": jid, "zero_raw": 2048, "direction": -1, "calibrated": False})
    return {"motion_enabled": False, "imu_mount_verified": False,
            "imu_mount_quat": list(H.DEFAULT_BUS_QUAT), "current_ma_per_count": 0.0,
            "units_verified": False, "imu_mounts": {}, "joints": joints}


def joints_payload(direction=None):
    direction = direction or {}
    return [{"id": H.JOINT_ID[name], "direction": direction.get(name, -1)} for name in H.JOINT_ORDER]


def build_valid():
    return H.build_calibration(current_calibration(), joints_payload({"left_hip_yaw": 1}),
                               {"source": "bus", "quat": [1.0, 0.0, 0.0, 0.0]})


STUCK_CALIB = "motion requires all 15 calibrated joints, measured limits and verified IMU mounting for the lsm6dsv16x IMU"
BUS_ERROR = "torque readback at register 40: Feetech Sync Read timeout; missing IDs [21]"


class CalibRobot(Robot):
    """标定写完之后按 World.restart_bus / calib_available 回话。"""

    def rpc(self, method, params=None):
        w = self.w
        if w.restarted and w.restart_bus is not None and method == "robot.busStatus":
            if self.w.robotd != "active":
                raise H.RpcError(f"{method}：连不上")
            # restart_bus 可以是函数：参数是重启以后过了几秒，用来模拟「刚启动那一下总线没打开」
            bus = w.restart_bus(w.clock.t - w.restart_t) if callable(w.restart_bus) else w.restart_bus
            return copy.deepcopy(bus)
        if method == "robot.busCommand" and (params or {}).get("action") == "calibration-status":
            if not w.calib_available:
                raise H.RpcError(f"{method}：calibration not available yet")
            status = w.calibration_status(method)
            if w.calib_mutate:
                status = copy.deepcopy(status)
                w.calib_mutate(status["calibration"])
            return status
        return super().rpc(method, params)


class CalibWorld(World):
    def __init__(self, root):
        self.restart_bus = None
        self.restart_calib_available = True
        self.calib_mutate = None
        self.restarted = False
        self.hello_ok = True
        self.restart_ok = True
        self.restart_ok_sequence = []
        super().__init__(root)
        self.robot = CalibRobot(self)
        self.ctx_factory = CalibCtx

    def run(self, argv):
        if argv and argv[0] == "journalctl":
            self.calls.append(list(argv))
            return 0, "robotd[1]: fake failure line\nrobotd[1]: another line", ""
        if argv[:3] == ["systemctl", "restart", "robotd"]:
            self.calls.append(list(argv))
            ok = self.restart_ok_sequence.pop(0) if self.restart_ok_sequence else self.restart_ok
            if ok:
                self.robotd, self.result, self.condition = "active", "success", "yes"
                self.robot.boot(self.clock.t)
                self.restarted = True
                self.restart_t = self.clock.t
                self.calib_available = self.restart_calib_available
                bus0 = self.restart_bus(0.0) if callable(self.restart_bus) else self.restart_bus
                self.robot.mode = (bus0 or {}).get("mode", "motion")
                return 0, "", ""
            self.robotd, self.result = "failed", "exit-code"
            return 1, "", "Job for robotd.service failed"
        return super().run(argv)


class CalibCtx(FakeCtx):
    def rpc(self, method, params=None, timeout=1.0):
        if method == "hello":
            if self.w.robotd != "active" or not self.w.hello_ok:
                raise H.RpcError("连不上 /run/robotd.sock")
            return {"api_version": 40, "daemon_version": "fake"}
        return super().rpc(method, params, timeout)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helper-calib-")
        self.w = CalibWorld(self.tmp)
        self.path = self.w.path(H.CALIB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.old = H.render_calibration(current_calibration()).encode("utf-8")
        self.path.write_bytes(self.old)
        os.chmod(self.path, 0o640)
        self.saved = self.w.path(H.CALIB_SAVED)
        self.saved.write_bytes(self.old + b"# old saved\n")
        os.chmod(self.saved, 0o600)
        self.dropin = self.w.path(H.COMMISSIONING_DROPIN)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commissioning(self, on=True, exec_too=False):
        self.w.commissioning_dropin = on
        self.w.exec_commissioning = bool(exec_too or on)
        if on:
            self.dropin.write_text("[Service]\nExecStart=\nExecStart=/opt/robot/robotd --commissioning\n", encoding="utf-8")
            self.w.robot.mode, self.w.robot.phase = "commissioning", "ready"
        else:
            self.w.robot.mode, self.w.robot.phase = "motion", "control"

    def limp_motion(self):
        self.commissioning(False)
        self.w.robot.torque = False
        self.w.robot.policy_enabled = False
        self.w.robot.home_recovery = False
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        self.w.restart_calib_available = True

    def call(self, args=None, raw=None, verb="calib-commit"):
        ctx = CalibCtx(self.w, raw if raw is not None else json.dumps(args or self.payload()).encode())
        code = H.run(["microduck-helper", verb], ctx)
        return code, ctx

    def last(self):
        return json.loads(self.w.path(H.LAST).read_text(encoding="utf-8"))

    def payload(self, dry=False, **over):
        p = {"by": "测试", "joints": joints_payload(), "imu": {"source": "bus", "quat": [1.0, 0.0, 0.0, 0.0]},
             "dry_run": dry}
        p.update(over)
        return p


class ArgsTest(Base):
    def bad(self, mut):
        p = self.payload()
        mut(p)
        code, _ = self.call(p)
        self.assertEqual(code, H.EXIT_ARGS)
        self.assertEqual(self.last()["result"], "bad_args")

    def test_joints_must_be_15(self):
        self.bad(lambda p: p["joints"].pop())

    def test_direction_must_be_pm1(self):
        self.bad(lambda p: p["joints"][0].update(direction=2))

    def test_joint_rows_only_id_direction(self):
        self.bad(lambda p: p["joints"][0].update(extra=1))

    def test_imu_source(self):
        self.bad(lambda p: p["imu"].update(source="mpu6050"))

    def test_dry_run_is_bool(self):
        self.bad(lambda p: p.update(dry_run="yes"))


class ValidationTest(unittest.TestCase):
    def bad(self, mutate, why):
        cal = copy.deepcopy(build_valid())
        mutate(cal)
        with self.assertRaises(H.CalibError) as cm:
            H.validate_calibration(cal, "bus")
        self.assertIn(why, str(cm.exception))

    def test_extra_top_key(self):
        self.bad(lambda c: c.update(extra=1), "顶层有不认识的键")

    def test_joints_count(self):
        self.bad(lambda c: c["joints"].pop(), "正好 15 颗")

    def test_order(self):
        self.bad(lambda c: c["joints"].reverse(), "官方顺序")

    def test_id_200(self):
        self.bad(lambda c: c["joints"][0].update(id=200), "不能是 200")

    def test_direction(self):
        self.bad(lambda c: c["joints"][0].update(direction=2), "direction 只能是")

    def test_zero_raw_range(self):
        self.bad(lambda c: c["joints"][0].update(zero_raw=4096), "zero_raw 要是 0–4095")

    def test_limits_need_both(self):
        self.bad(lambda c: c["joints"][0].pop("max_rad"), "限位要两个都有")

    def test_min_less_than_max(self):
        self.bad(lambda c: c["joints"][0].update(min_rad=1.0, max_rad=0.5), "min_rad 要小于 max_rad")

    def test_motion_requires_calibrated(self):
        self.bad(lambda c: c["joints"][0].update(calibrated=False), "motion_enabled = true 时 15 颗都要 calibrated")

    def test_motion_requires_verified_imu(self):
        self.bad(lambda c: c.update(imu_mount_verified=False), "IMU 方向要 verified")


class CalibReadTest(Base):
    def test_reads_file_and_mode_without_lock_or_write(self):
        self.commissioning(True)
        self.w.locks.add(H.LOCK)
        locks_before = set(self.w.locks)
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        code, ctx = self.call({"by": "测试"}, verb="calib-read")
        self.assertEqual(code, H.EXIT_OK)
        done = next(x for x in ctx.stdout.lines() if x.get("step") == "done")["detail"]
        self.assertEqual(done["result"], "read")
        self.assertEqual(done["mode"], "commissioning")
        self.assertTrue(done["commissioning_present"])
        self.assertEqual(done["calibration"]["joints"][0]["name"], H.JOINT_ORDER[0])
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(self.w.locks, locks_before, "calib-read 不拿锁")
        self.assertFalse(self.w.path(H.LAST).exists(), "calib-read 不写 last-result")
        self.assertEqual(ctx.fs_log, [], "calib-read 不写任何文件")

    def test_foreign_commissioning_is_reported_not_refused(self):
        self.commissioning(False, exec_too=True)
        code, ctx = self.call({"by": "测试"}, verb="calib-read")
        self.assertEqual(code, H.EXIT_OK)
        done = next(x for x in ctx.stdout.lines() if x.get("step") == "done")["detail"]
        self.assertEqual(done["result"], "read")
        self.assertEqual(done["mode"], "foreign")
        self.assertFalse(done["commissioning_present"])
        self.assertTrue(done["exec_has_commissioning"])
        self.assertIn("不是镜像设的", done["mode_text"])


class CommitTest(Base):
    def test_old_request_preserves_centers_and_new_request_changes_one(self):
        self.commissioning()
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        old = build_valid()
        centers = [1980 + i for i in range(15)]
        for joint, center in zip(old["joints"], centers):
            joint["zero_raw"] = center
        self.path.write_text(H.render_calibration(old), encoding="utf-8")
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_OK, self.last())
        def saved_centers():
            return [j["zero_raw"] for j in H.tomllib.loads(self.path.read_text("utf-8"))["joints"]]
        self.assertEqual(saved_centers(), centers)
        self.commissioning()
        joints = joints_payload()
        joints[4]["centered"] = True
        code, _ = self.call(self.payload(joint_mode="recorded", joints=joints))
        self.assertEqual(code, H.EXIT_OK, self.last())
        centers[4] = 2048
        self.assertEqual(saved_centers(), centers)

    def test_keep_existing_joint_calibration_through_write_and_readback(self):
        self.commissioning()
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        old = build_valid()
        for i, joint in enumerate(old["joints"]):
            joint.update(zero_raw=1980+i, direction=1 if i % 2 else -1,
                         min_rad=-1.31, max_rad=1.42)
        self.path.write_text(H.render_calibration(old), encoding="utf-8")
        code, _ = self.call(self.payload(joint_mode="keep"))
        self.assertEqual(code, H.EXIT_OK, self.last())
        self.assertEqual(H.tomllib.loads(self.path.read_text("utf-8"))["joints"], old["joints"])
        self.assertTrue(self.last()["detail"]["readback_ok"])

    def test_keep_factory_centers_when_finishing_first_setup(self):
        self.commissioning()
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        old = current_calibration()
        for i, row in enumerate(old["joints"]):
            row["zero_raw"] = 2000+i
        self.path.write_text(H.render_calibration(old), encoding="utf-8")
        code, _ = self.call(self.payload(joint_mode="keep"))
        self.assertEqual(code, H.EXIT_OK, self.last())
        new = H.tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual([j["zero_raw"] for j in new["joints"]], list(range(2000,2015)))
        self.assertTrue(new["motion_enabled"])

    def test_import_in_commissioning_keeps_mode_and_uncalibrated_joints(self):
        self.commissioning()
        self.w.restart_bus = {"mode": "commissioning", "phase": "ready"}
        old = current_calibration()
        for i, joint in enumerate(old["joints"]):
            joint["zero_raw"] = 2000+i
        self.path.write_text(H.render_calibration(old), encoding="utf-8")
        code, _ = self.call(self.payload(joint_mode="imu_only"))
        self.assertEqual(code, H.EXIT_OK, self.last())
        result = H.tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual(result["joints"], old["joints"])
        self.assertFalse(result["motion_enabled"])
        self.assertTrue(result["imu_mount_verified"])
        self.assertTrue(self.dropin.exists())

    def test_commissioning_success_deletes_dropin_and_reads_back(self):
        self.commissioning(True)
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        code, _ = self.call(self.payload(joints=joints_payload({"left_hip_yaw": 1})))
        self.assertEqual(code, H.EXIT_OK, self.last())
        last = self.last()
        self.assertEqual(last["result"], "committed")
        self.assertEqual(last["detail"]["mode_before"], "commissioning")
        self.assertTrue(last["detail"]["readback_ok"])
        self.assertEqual(last["detail"]["mismatches"], [])
        self.assertEqual(last["detail"]["bus_status"], {"mode": "motion", "phase": "control", "error": None})
        self.assertFalse(self.dropin.exists(), "写完要删掉镜像的 commissioning drop-in")
        self.assertTrue([c for c in self.w.calls if c[:2] == ["systemctl", "daemon-reload"]])
        main = self.path.read_bytes()
        self.assertEqual(self.saved.read_bytes(), main)
        self.assertEqual(self.w.path(H.CALIB_PREVIOUS).read_bytes(), self.old)
        back = H.tomllib.loads(main.decode("utf-8"))
        self.assertTrue(back["motion_enabled"])
        self.assertEqual(back["joints"][0]["direction"], 1)
        groups = sorted(self.w.path(H.CALIB_BACKUP_DIR).iterdir())
        self.assertEqual(len(groups), 1)
        manifest = json.loads((groups[0] / "manifest.json").read_text(encoding="utf-8"))
        names = {row["name"]: row for row in manifest["files"]}
        self.assertTrue(names["hd1910-calibration.toml"]["existed"])
        self.assertTrue(names["hd1910-calibration.saved.toml"]["existed"])
        self.assertTrue(names["zzzz-zz-microduck-commissioning.conf"]["existed"])
        self.assertEqual((groups[0] / "hd1910-calibration.toml").read_bytes(), self.old)

    def test_motion_waiting_for_bus_can_be_rewritten(self):
        """运动模式卡在等总线（没有策略）：允许写标定把它救回来。"""
        self.commissioning(False)
        self.w.robot.mode, self.w.robot.phase = "motion", "waiting_for_bus"
        self.w.robot.error = STUCK_CALIB
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        self.w.restart_calib_available = True
        code, _ = self.call(self.payload(joints=joints_payload({"left_hip_yaw": 1})))
        self.assertEqual(code, H.EXIT_OK, self.last())
        self.assertEqual(self.last()["result"], "committed")

    def test_motion_waiting_for_bus_calibration_error_rolls_back(self):
        self.limp_motion()
        self.w.restart_bus = {"mode": "motion", "phase": "waiting_for_bus", "error": STUCK_CALIB}
        self.w.restart_calib_available = False
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertEqual(last["result"], "calib_rejected")
        self.assertIn("标定没通过主程序的检查", last["reason"])
        self.assertIn(STUCK_CALIB, last["reason"])
        self.assertTrue(last["detail"]["rolled_back"])
        self.assertTrue(last["detail"]["files_restored"])
        self.assertTrue(last["detail"]["mode_restored"])
        self.assertTrue(last["detail"]["robotd_up_after_rollback"])
        self.assertEqual(self.path.read_bytes(), self.old)
        self.assertEqual(self.saved.read_bytes(), self.old + b"# old saved\n")

    def test_motion_waiting_for_bus_bus_error_mentions_servo_power(self):
        self.limp_motion()
        self.w.restart_bus = {"mode": "motion", "phase": "waiting_for_bus", "error": BUS_ERROR}
        self.w.restart_calib_available = False
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertEqual(last["result"], "bus_unavailable")
        self.assertIn("主程序打不开舵机总线", last["reason"])
        self.assertIn("舵机电源", last["reason"])
        self.assertTrue(last["detail"]["files_restored"])

    def test_bus_error_right_after_restart_then_control_commits(self):
        """刚重启那一下总线没打开（主程序每秒重试），之后进了控制阶段：照样算写成功，不回滚。"""
        self.limp_motion()
        self.w.restart_bus = lambda t: ({"mode": "motion", "phase": "waiting_for_bus", "error": BUS_ERROR}
                                        if t < 3.0 else {"mode": "motion", "phase": "control"})
        self.w.restart_calib_available = True
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_OK, self.last())
        self.assertEqual(self.last()["result"], "committed")
        self.assertNotEqual(self.path.read_bytes(), self.old)

    def test_readback_mismatch_rolls_back(self):
        self.limp_motion()
        def mutate(cal):
            cal["joints"][0]["zero_raw"] = 2047
        self.w.calib_mutate = mutate
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertEqual(last["result"], "readback_mismatch")
        self.assertFalse(last["detail"]["readback_ok"])
        self.assertIn("joints[0].zero_raw", last["detail"]["mismatches"][0])
        self.assertTrue(last["detail"]["rolled_back"])
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_bus_protocol_readback_mismatch_restores_bound_files(self):
        self.limp_motion()
        self.old = b'imu_bus_protocol = "dxl2"\n' + self.old
        self.path.write_bytes(self.old)
        saved = self.old + b"# separate saved\n"
        self.saved.write_bytes(saved)
        self.w.calib_mutate = lambda cal: cal.update(imu_bus_protocol="feetech")
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        self.assertEqual(self.last()["result"], "readback_mismatch")
        self.assertTrue(any("imu_bus_protocol" in item for item in self.last()["detail"]["mismatches"]))
        self.assertEqual(self.path.read_bytes(), self.old)
        self.assertEqual(self.saved.read_bytes(), saved)

    def test_saved_write_error_restores_both_files(self):
        self.commissioning(True)
        self.w.restart_bus = {"mode": "motion", "phase": "control"}
        real, calls = H.write_atomic, {"n": 0}
        def flaky(ctx, path, data, mode, uid=None, gid=None):
            if path == H.CALIB_SAVED:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("disk full")
            return real(ctx, path, data, mode, uid, gid)
        with mock.patch.object(H, "write_atomic", side_effect=flaky):
            code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertTrue(last["detail"]["rolled_back"], last)
        self.assertTrue(last["detail"]["files_restored"])
        self.assertEqual(self.path.read_bytes(), self.old)
        self.assertEqual(self.saved.read_bytes(), self.old + b"# old saved\n")
        self.assertTrue(self.dropin.exists(), "没删成的 commissioning 文件还在")

    def test_rollback_robotd_does_not_come_up(self):
        self.limp_motion()
        self.w.restart_bus = {"mode": "motion", "phase": "waiting_for_bus", "error": STUCK_CALIB}
        self.w.restart_calib_available = False
        self.w.hello_ok = False
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertFalse(last["detail"]["rolled_back"])
        self.assertFalse(last["detail"]["robotd_up_after_rollback"])
        self.assertIn("去日志页导出诊断包", last["reason"])

    def test_lock_comes_before_flag(self):
        self.limp_motion()
        self.w.locks.add(H.LOCK)
        flag = self.w.path(H.FLAG)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("{}", encoding="utf-8")
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_BUSY, self.last())
        self.assertEqual(self.last()["result"], "busy")

    def test_flag_refused_when_lock_is_free(self):
        self.limp_motion()
        flag = self.w.path(H.FLAG)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("{}", encoding="utf-8")
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_STATE)
        self.assertEqual(self.last()["result"], "bus_held")
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_policy_running_is_refused(self):
        self.limp_motion()
        self.w.robot.policy_enabled = True
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_STATE, self.last())
        self.assertIn("鸭子在走 / 站着", self.last()["reason"])
        self.assertEqual(self.path.read_bytes(), self.old)
        self.assertFalse(self.w.restarted)

    def test_foreign_commissioning_is_refused(self):
        self.commissioning(False, exec_too=True)
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_STATE, self.last())
        self.assertEqual(self.last()["result"], "foreign_commissioning")
        self.assertIn("不是镜像设的", self.last()["reason"])
        self.assertIn("诊断包", self.last()["reason"])

    def test_restart_failure_rolls_back(self):
        self.limp_motion()
        self.w.restart_ok_sequence = [False, True]
        code, _ = self.call(self.payload())
        self.assertEqual(code, H.EXIT_FAIL, self.last())
        last = self.last()
        self.assertEqual(last["result"], "restart_failed")
        self.assertTrue(last["detail"]["rolled_back"])
        self.assertEqual(self.path.read_bytes(), self.old)
        restarts = [c for c in self.w.calls if c[:3] == ["systemctl", "restart", "robotd"]]
        self.assertEqual(len(restarts), 2)

    def test_backup_keeps_ten_by_sequence(self):
        class Op:
            by = "测试"
            def __init__(self, ctx):
                self.ctx, self.lines = ctx, []
            def emit(self, *a, **k):
                self.lines.append(a)
        ctx = CalibCtx(self.w, b"{}")
        for _ in range(12):
            H.calib_backup(Op(ctx))
        names = sorted(n for n in os.listdir(self.w.path(H.CALIB_BACKUP_DIR)))
        self.assertEqual(len(names), 10)
        self.assertEqual(names[0].split("-", 1)[0], "0003")
        self.assertEqual(names[-1].split("-", 1)[0], "0012")


if __name__ == "__main__":
    unittest.main()
