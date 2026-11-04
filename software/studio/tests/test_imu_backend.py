"""IMU 页后端（studio/imu.py）：推给页面的状态取对了没有，粗校 / verify-imu 该拒的拒。

用 tests/fixtures/now-20261018.jsonl（10-18 二号鸭真机录的）里的 busStatus 和 calibration-status。
粗校 / verify 用假 cmd 连接，照 fork 的 verify-imu 回包文字演成功和三种失败。
"""
import asyncio
import copy
import json
import math
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio.imu import ImuBackend, Refused  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.robotd import Sample  # noqa: E402
from studio.rpc import RpcError  # noqa: E402

FIXTURE = HERE / "fixtures" / "now-20261018.jsonl"


def recorded(method):
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("kind") == "reply" and d.get("method") == method:
            return d["reply"]["result"]
    raise KeyError(method)


class FakeCmd:
    """站在假 rd 的 cmd 连接上：busStatus / calibration-status / verify-imu。"""

    def __init__(self, rd):
        self.rd = rd

    async def call(self, method, params, timeout):
        if method == "robot.busStatus":
            return self.rd.bus.value
        if method == "robot.busCommand":
            action = (params or {}).get("action")
            if action == "calibration-status":
                return self.rd.calib.value
            if action == "verify-imu":
                return self.rd.verify(params)
        raise RpcError(method, -32601, "method not found")


class FakeRd:
    def __init__(self, bus=None, calib=None):
        self.bus, self.calib = Sample(), Sample()
        if bus is not None:
            self.bus.set(bus)
        if calib is not None:
            self.calib.set(calib)
        self.addr, self.verify_plan = None, "ok"
        self.cmd = FakeCmd(self)

    def verify(self, params):
        plan = self.verify_plan
        if plan == "no_sample":
            raise RpcError("robot.busCommand", -32603,
                           "verify-imu needs a live lsm6dsv16x sample; check busStatus last_sample.imu and the reader")
        if plan == "not_upright":
            raise RpcError("robot.busCommand", -32603,
                           "under this mount the trunk reads 31.2° from upright (limit 5°); stand the robot upright, "
                           "or the mount is wrong: check the module axes")
        if plan == "wrong":
            raise RpcError("robot.busCommand", -32603,
                           "tipped 20.0°, but under this mount it reads 90.0° (90.0° from nose-down): the mount is about "
                           "that far off about the vertical axis. If the nose did not go down, tip it nose-down and run "
                           "verify-imu again. Nothing saved: mount unchanged")
        if plan == "timeout":
            raise RpcError("robot.busCommand", -32603,
                           "no nose-down tip of at least 12° held within 20 s of the upright check; nothing saved")
        q = [float(x) for x in params["mount_wxyz"]]
        n = math.sqrt(sum(x * x for x in q))
        q = [x / n for x in q]
        cal = copy.deepcopy(self.calib.value or {})
        cal.setdefault("calibration", {})
        src = cal.get("imu_source") or "bus"
        if src == "bus":
            cal["calibration"]["imu_mount_quat"] = q
            cal["calibration"]["imu_mount_verified"] = True
        else:
            cal["calibration"].setdefault("imu_mounts", {})[src] = {"quat": q, "verified": True}
        top = cal.get("imu_mount")
        if isinstance(top, dict):
            top["quat"], top["verified"] = q, True
        self.calib.set(cal)
        return {"accepted": True, "source": src, "mount_wxyz": q}


def commissioning_bus(source="bus", gravity=None, mode="commissioning"):
    return {"mode": mode, "phase": "ready" if mode == "commissioning" else "control",
            "torque_state": "off", "torque_off_pending": None,
            "native": {"imu_source": source},
            "last_sample": {"servos": [{"id": i} for i in range(15)],
                            "imu": {"source": source, "ready": True, "valid": True,
                                    "gravity": list(gravity or [0.0, 0.0, -1.0]),
                                    "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                                    "mount_verified": False, "tilt_deg": 0.0}}}


def factory_calib(source="bus"):
    cal = {"motion_enabled": False, "imu_mount_verified": False,
           "imu_mount_quat": [math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0],
           "current_ma_per_count": 0.0, "units_verified": False, "imu_mounts": {},
           "joints": [{"name": n, "id": 10 + i, "zero_raw": 2048, "direction": -1, "calibrated": False}
                      for i, n in enumerate(("left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee",
                                             "left_ankle", "neck_pitch", "head_pitch", "head_yaw", "head_roll",
                                             "mouth", "right_hip_yaw", "right_hip_roll", "right_hip_pitch",
                                             "right_knee", "right_ankle"))]}
    if source != "bus":
        cal["imu_mounts"][source] = {"quat": [0.5, 0.5, 0.5, 0.5], "verified": False}
    return {"calibration": cal, "imu_source": source}


def bus_transport(protocol="dxl2", board="sflp_124_12", identity=None):
    return {"protocol": protocol, "board": board, "identity": list(identity or [1, 2, 3])}


class ImuBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Log(Path(self.tmp.name) / "logs", echo=False, background=False)

    def tearDown(self):
        self.tmp.cleanup()

    def backend(self, **kw):
        return ImuBackend(FakeRd(**kw), self.log, self.tmp.name)

    def test_view_picks_mount_of_chip_in_use(self):
        # 二号鸭用 LSM6DSV16X：要取 imu_mounts 里它那份，不是顶上那个（那是另一种芯片的）
        v = self.backend(bus=recorded("robot.busStatus"), calib=recorded("robot.busCommand")).view()
        self.assertEqual(v["t"], "imu")
        self.assertEqual(v["source"], "lsm6dsv16x")
        self.assertEqual(v["mount"]["quat"][0], 0.698208849)
        self.assertIs(v["mount"]["verified"], True)
        self.assertTrue(v["ready"])
        self.assertEqual(v["reader"]["state"], "streaming")
        json.dumps(v)                                           # 能直接发给页面

    def test_view_without_robotd(self):
        v = self.backend().view()
        self.assertIsNone(v["source"])
        self.assertIsNone(v["mount"])
        self.assertIsNone(v["stand_calib_at"])

    def test_view_ignores_malformed_commissioning_imu_sample(self):
        for sample in ([], {"imu": []}, {"imu": "invalid"}):
            bus = commissioning_bus()
            bus.pop("native")
            bus["last_sample"] = sample
            with self.subTest(sample=sample):
                self.assertEqual(self.backend(bus=bus, calib=factory_calib()).view()["source"], "bus")

    def test_view_bus_board_falls_back_to_top_level_mount(self):
        # 总线小板：busStatus 没有 imu_source → 当成 bus，方向用顶上的 imu_mount_quat
        bus = {"native": {"imu_age_ms": 5}}
        calib = {"calibration": {"imu_mount_quat": [1, 0, 0, 0], "imu_mount_verified": False}}
        v = self.backend(bus=bus, calib=calib).view()
        self.assertEqual(v["source"], "bus")
        self.assertEqual(v["mount"], {"quat": [1, 0, 0, 0], "verified": False})

    def test_view_commissioning_reads_last_sample(self):
        v = self.backend(bus=commissioning_bus(), calib=factory_calib()).view()
        self.assertEqual(v["mode"], "commissioning")
        self.assertTrue(v["ready"])
        self.assertEqual(v["reader"]["state"], "streaming")
        self.assertEqual(v["gravity"], [0.0, 0.0, -1.0])

    def test_view_reports_actual_bus_transport_in_both_modes(self):
        for mode in ("commissioning", "motion"):
            bus = commissioning_bus(mode=mode)
            info = bus_transport()
            if mode == "commissioning":
                bus.pop("native")
                bus["imu_bus"] = info
            else:
                bus["native"]["imu_bus"] = info
            v = self.backend(bus=bus, calib=factory_calib()).view()
            with self.subTest(mode=mode):
                self.assertEqual(v["source"], "bus")
                self.assertEqual(v.get("imu_bus"), info)
                self.assertIsNone(v["age_ms"])

    def test_bus_verified_mount_requires_matching_protocol_binding(self):
        for actual, bound, expected in (("feetech", None, True), ("feetech", "feetech", True),
                                        ("dxl2", None, False), ("dxl2", "feetech", False),
                                        ("dxl2", "dxl2", True), ("feetech", "dxl2", False)):
            bus, calib = commissioning_bus(), factory_calib()
            bus["imu_bus"] = bus_transport(actual, "ft6" if actual == "feetech" else "sflp_124_12")
            calib["calibration"]["imu_mount_verified"] = True
            calib["imu_mount"] = {"quat": [1, 0, 0, 0], "verified": True}
            if bound is not None:
                calib["calibration"]["imu_bus_protocol"] = bound
            with self.subTest(actual=actual, bound=bound):
                v = self.backend(bus=bus, calib=calib).view()
                self.assertIs(v["mount"]["verified"], expected)
                self.assertEqual(v.get("mount_protocol_mismatch"), not expected)

    def test_bus_transport_does_not_change_i2c_mount_binding(self):
        bus, calib = commissioning_bus("lsm6dsv16x"), factory_calib("lsm6dsv16x")
        bus["imu_bus"] = bus_transport()
        calib["calibration"]["imu_mounts"]["lsm6dsv16x"]["verified"] = True
        v = self.backend(bus=bus, calib=calib).view()
        self.assertIs(v["mount"]["verified"], True)
        self.assertIsNone(v.get("imu_bus"))

    def test_bus_transport_identity_change_clears_cached_solve(self):
        for original, changed in ((bus_transport(), bus_transport("feetech", "ft6")),
                                  (bus_transport("feetech", "ft6"), bus_transport("feetech", "imu_to_dxl")),
                                  (bus_transport(), bus_transport(identity=[1, 2, 4]))):
            bus = commissioning_bus()
            bus["imu_bus"] = original
            b = self.backend(bus=bus, calib=factory_calib())
            b.view()
            b.last_result = {"ok": True, "mode": "coarse", "mount": [1., 0., 0., 0.]}
            b.job = {"kind": "coarse", "done": True, "title": "主程序验证通过"}
            bus["imu_bus"] = changed
            with self.subTest(changed=changed):
                b.view()
                self.assertIsNone(b.last_result)
                self.assertIsNone(b.job)

    def test_transport_change_during_solve_rejects_inflight_result(self):
        bus = commissioning_bus()
        bus["imu_bus"] = bus_transport()
        b = self.backend(bus=bus, calib=factory_calib())
        def changed(*args, **kw):
            bus["imu_bus"]["identity"] = [9, 9, 9]
            return {"ok": True, "mode": "coarse", "mount": [1., 0., 0., 0.]}
        with mock.patch("studio.imu.imu_calib.solve", side_effect=changed), self.assertRaisesRegex(Refused, "改变"):
            asyncio.run(b.handle({"op": "solve", "mode": "coarse", "samples": [{"g": [0, 0, -1]}]}))
        self.assertIsNone(b.last_result)

    def test_coarse_capture_rejects_transport_change_mid_collection(self):
        b = self.backend(bus=commissioning_bus(), calib=factory_calib())
        calls = {"n": 0}
        async def bus_once():
            calls["n"] += 1
            bus = commissioning_bus()
            bus["imu_bus"] = bus_transport(identity=[calls["n"]])
            return bus
        b._bus_once = bus_once
        with self.assertRaisesRegex(Refused, "改变"):
            asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 0.5}))

    def test_verify_rejects_transport_changed_during_runtime_verification(self):
        bus = commissioning_bus()
        bus["imu_bus"] = bus_transport()
        b = self.backend(bus=bus, calib=factory_calib())
        b.rd.calib.value["calibration"]["imu_bus_protocol"] = "dxl2"
        b.view()
        q = [0.5] * 4
        b.last_result = {"ok": True, "mode": "coarse", "mount": q}
        original = b.rd.verify
        def changed(params):
            result = original(params)
            bus["imu_bus"]["identity"] = [8, 8, 8]
            return result
        b.rd.verify = changed
        with self.assertRaisesRegex(Refused, "改变"):
            asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": q}))
        self.assertFalse((b.job or {}).get("done"))

    def test_dxl2_verify_accepts_new_runtime_protocol_binding(self):
        from studio import commit
        bus = commissioning_bus()
        bus["imu_bus"] = bus_transport()
        b = self.backend(bus=bus, calib=factory_calib())
        b.view()
        q = [0.5] * 4
        b.last_result = {"ok": True, "mode": "coarse", "mount": q}
        commit.save_pending(self.tmp.name, "bus", [1, 0, 0, 0])
        original = b.rd.verify
        def bound(params):
            reply = original(params)
            b.rd.calib.value["calibration"]["imu_bus_protocol"] = "dxl2"
            return reply
        b.rd.verify = bound
        result = asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": q}))
        self.assertTrue(result["ok"])
        self.assertIs(b.view()["mount"]["verified"], True)
        self.assertIsNone(commit.load_pending(self.tmp.name))

    def test_full_bus_solve_persists_transport_identity(self):
        from tests.test_imu_calib import Duck
        bus, calib = commissioning_bus(), factory_calib()
        bus["imu_bus"] = bus_transport()
        calib["calibration"].update(imu_bus_protocol="dxl2", imu_mount_verified=True)
        b = self.backend(bus=bus, calib=calib)
        old = b.view()["mount"]["quat"]
        data = Duck(old, old_mount=old, noise_deg=0.1).run()
        samples = [{"step": step, "g": s["g"]} for step, rows in data.items() for s in rows]
        result = asyncio.run(b.handle({"op": "solve", "samples": samples}))
        self.assertTrue(result["ok"], result)
        pending = json.loads((Path(self.tmp.name) / "imu-pending.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["imu_bus"], bus["imu_bus"])

    def test_stand_calib_time_read_from_state_dir(self):
        (Path(self.tmp.name) / "imu-stand-calib.json").write_text('{"at": "2026-10-18 02:10"}', encoding="utf-8")
        self.assertEqual(self.backend().view()["stand_calib_at"], "2026-10-18 02:10")

    def test_refuses_bad_or_unfinished_ops(self):
        b = self.backend()
        for args, word in (({"op": "solve"}, "没收到读数"),
                           ({"op": "solve", "samples": [{}] * 6001}, "读数太多"),
                           ({"op": "save"}, "写进主程序"),
                           ({"op": "coarse"}, "粗校"),
                           ({"op": "detect"}, "第一次开机前接好"),
                           ({"op": "set_chip", "chip": "bno08x"}, "microduck-firstboot"),
                           ({"op": "rm -rf"}, "不认识")):
            with self.subTest(args=args.get("op")):
                with self.assertRaises(Refused) as cm:
                    asyncio.run(b.handle(args, "测试"))
                self.assertIn(word, str(cm.exception))

    def test_full_solve_saves_pending_for_commit(self):
        """站着完整校完、结果 ok：新的方向、来源、质量写进 state/imu-pending.json（Studio 重启也不丢）。"""
        from tests.test_imu_calib import Duck
        b = self.backend(bus=recorded("robot.busStatus"), calib=recorded("robot.busCommand"))
        old = b.view()["mount"]["quat"]
        data = Duck(old, old_mount=old, noise_deg=0.1).run()
        samples = [{"step": step, "g": s["g"]} for step, rows in data.items() for s in rows]
        res = asyncio.run(b.handle({"op": "solve", "samples": samples}, "测试"))
        self.assertTrue(res["ok"], res)
        row = json.loads((Path(self.tmp.name) / "imu-pending.json").read_text(encoding="utf-8"))
        self.assertEqual(row["source"], "lsm6dsv16x")
        self.assertEqual(row["by"], "测试")
        self.assertIn("quality", row)
        self.assertAlmostEqual(sum(x * x for x in row["quat"]), 1.0, places=9)

    def test_solve_bad_samples_fails_politely(self):
        # 乱七八糟的读数：回 ok=False 和一句原因，不抛异常、不把后端弄挂
        b = self.backend()
        res = asyncio.run(b.handle({"op": "solve", "samples": [{"step": "still", "g": [0, 0, -1]}]}, "测试"))
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"])
        self.assertIs(b.last_result, res)

    def test_coarse_capture_then_solve_uses_commissioning_last_sample(self):
        rd = FakeRd(bus=commissioning_bus(), calib=factory_calib())
        b = ImuBackend(rd, self.log, self.tmp.name)
        still = asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 3}, "测试"))
        self.assertEqual(still["phase"], "still")
        self.assertGreaterEqual(still["n"], 15)
        self.assertEqual(still["samples"][0]["g"], [0.0, 0.0, -1.0])

        a = math.radians(20.0)
        rd.bus.value["last_sample"]["imu"]["gravity"] = [math.sin(a), 0.0, -math.cos(a)]
        fwd = asyncio.run(b.handle({"op": "coarse-capture", "phase": "fwd", "seconds": 3}, "测试"))
        self.assertGreaterEqual(fwd["n"], 15)

        samples = still["samples"] + fwd["samples"]
        res = asyncio.run(b.handle({"op": "solve", "mode": "coarse", "frame": "body", "samples": samples}, "测试"))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["mode"], "coarse")
        self.assertEqual(res["quality"]["grade"], "coarse")
        self.assertEqual(len(res["mount"]), 4)
        self.assertTrue(res["confirm"])

    def test_verify_success_writes_and_rereads_calibration(self):
        rd = FakeRd(bus=commissioning_bus("lsm6dsv16x"), calib=factory_calib("lsm6dsv16x"))
        b = ImuBackend(rd, self.log, self.tmp.name)
        mount = [0.5, 0.5, 0.5, 0.5]
        b.last_result = {"ok": True, "mode": "coarse", "mount": mount}
        rd.verify_plan = "ok"
        res = asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": mount}, "测试"))
        self.assertTrue(res["ok"], res)
        v = b.view()
        self.assertEqual(v["source"], "lsm6dsv16x")
        self.assertIs(v["mount"]["verified"], True)
        self.assertAlmostEqual(v["mount"]["quat"][0], 0.5)

    def _verify_failure(self, plan):
        rd = FakeRd(bus=commissioning_bus("lsm6dsv16x"), calib=factory_calib("lsm6dsv16x"))
        b = ImuBackend(rd, self.log, self.tmp.name)
        mount = [0.5, 0.5, 0.5, 0.5]
        b.last_result = {"ok": True, "mode": "coarse", "mount": mount}
        rd.verify_plan = plan
        with self.assertRaises(Refused) as cm:
            asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": mount}, "测试"))
        return str(cm.exception)

    def test_verify_failure_no_sample_is_translated(self):
        msg = self._verify_failure("no_sample")
        self.assertIn("没有 IMU 读数", msg)
        self.assertIn("Nothing saved", msg)

    def test_verify_failure_not_upright_is_translated(self):
        msg = self._verify_failure("not_upright")
        self.assertIn("没有摆正", msg)
        self.assertIn("Nothing saved", msg)

    def test_verify_failure_wrong_heading_is_translated(self):
        msg = self._verify_failure("wrong")
        self.assertIn("低头方向不对", msg)
        self.assertIn("90°", msg)
        self.assertIn("Nothing saved", msg)

    def test_verify_failure_timeout_is_translated(self):
        msg = self._verify_failure("timeout")
        self.assertIn("超时没看到往前低头", msg)
        self.assertIn("Nothing saved", msg)

    def test_motion_mode_refuses_coarse(self):
        b = self.backend(bus=commissioning_bus(mode="motion"), calib=factory_calib())
        for args in ({"op": "solve", "mode": "coarse", "samples": [{"step": "still", "g": [0, 0, -1]}]},
                     {"op": "coarse-capture", "phase": "still", "seconds": 0.5},
                     {"op": "verify", "confirmed": True, "mount_wxyz": [1, 0, 0, 0]}):
            with self.subTest(op=args["op"]):
                with self.assertRaises(Refused) as cm:
                    asyncio.run(b.handle(args, "测试"))
                self.assertIn("主程序已经在运动模式", str(cm.exception))

    def test_verify_readback_failure_or_mismatch_is_not_success(self):
        for corrupt in ("unreachable", "wrong-source", "wrong-quat", "unverified", "not-normalized", "contradictory-top"):
            b = self.backend(bus=commissioning_bus(), calib=factory_calib())
            q = [0.5]*4
            b.last_result = {"ok": True, "mode": "coarse", "mount": q}
            original = b.rd.cmd.call
            async def broken(method, params, timeout):
                if (params or {}).get("action") == "calibration-status":
                    if corrupt == "unreachable":
                        raise RpcError(method, -1, "readback missing")
                    status = copy.deepcopy(b.rd.calib.value)
                    if corrupt == "wrong-source":
                        status["imu_source"] = "lsm6dsv16x"
                    elif corrupt == "wrong-quat":
                        status["calibration"]["imu_mount_quat"] = [1.,0.,0.,0.]
                    elif corrupt == "not-normalized":
                        status["calibration"]["imu_mount_quat"] = [1.]*4
                    elif corrupt == "contradictory-top":
                        status["imu_mount"] = {"quat": q, "verified": True}
                        status["calibration"]["imu_mount_verified"] = False
                    else:
                        status["calibration"]["imu_mount_verified"] = False
                    return status
                return await original(method, params, timeout)
            b.rd.cmd.call = broken
            with self.subTest(corrupt=corrupt), self.assertRaises(Refused):
                asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": q}))
            self.assertFalse((b.job or {}).get("done"))

    def test_redo_view_resumes_persistent_session_and_allows_hold(self):
        from studio import helper
        root = Path(self.tmp.name) / "duck"
        directory = root / "var/lib/microduck-helper/imu-recalibrate"
        directory.mkdir(parents=True)
        manifest = {"schema": "microduck-imu-recalibrate/1", "active": True, "phase": "commissioning",
                    "session_id": "a"*32, "source": "bus"}
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        b = ImuBackend(FakeRd(bus=commissioning_bus(), calib=factory_calib()), self.log, self.tmp.name, root=str(root))
        with mock.patch.object(helper, "_call", side_effect=AssertionError("view must not sudo")):
            redo = b.view()["redo"]
        self.assertTrue(redo["active"])
        self.assertEqual(redo["session_id"], "a"*32)
        self.assertEqual(redo["phase"], "commissioning")
        b.commit = SimpleNamespace(hold_directions=lambda: [-1] * 15)
        b._rpc_once = mock.AsyncMock(return_value={"accepted": True})
        self.assertTrue(asyncio.run(b.handle({"op": "hold"}))["accepted"])
        b._rpc_once.assert_awaited_once_with("robot.busCommand",
            {"action": "hold-home", "directions": [-1] * 15, "duration_ms": 3000}, 20.0, "站姿使能")
        self.assertFalse(b._redo_busy)

    def test_factory_coarse_refuses_enabled_hold_and_pending_torque(self):
        for key, value in (("torque_state", "all_enabled"), ("torque_state", None),
                           ("torque_off_pending", True), ("hold", {"active": True})):
            bus = commissioning_bus()
            bus[key] = value
            b = self.backend(bus=bus, calib=factory_calib())
            with self.subTest(key=key), self.assertRaisesRegex(Refused, "卸力"):
                asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 0.5}))

    def test_factory_coarse_capture_solve_and_verify_while_holding_home(self):
        bus = commissioning_bus()
        bus.update(phase="holding", torque_state="all_enabled",
                   hold={"stage": "holding", "max_error_deg": 0.6, "directions": [-1] * 15})
        b = self.backend(bus=bus, calib=factory_calib())
        original_joints = copy.deepcopy(b.rd.calib.value["calibration"]["joints"])
        still = asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 3}))
        a = math.radians(20)
        bus["last_sample"]["imu"]["gravity"] = [math.sin(a), 0, -math.cos(a)]
        fwd = asyncio.run(b.handle({"op": "coarse-capture", "phase": "fwd", "seconds": 3}))
        solved = asyncio.run(b.handle({"op": "solve", "mode": "coarse",
                                      "samples": still["samples"] + fwd["samples"]}))
        self.assertTrue(solved["ok"], solved)
        bus["last_sample"]["imu"]["gravity"] = [0, 0, -1]
        result = asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": solved["mount"]}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(bus["torque_state"], "all_enabled")
        self.assertFalse(b.rd.calib.value["calibration"]["motion_enabled"])
        self.assertEqual(b.rd.calib.value["calibration"]["joints"], original_joints)

    def test_factory_coarse_rejects_unsettled_or_conflicting_home_hold(self):
        mutations = [
            lambda b: b["hold"].update(stage="ramping"),
            lambda b: b.update(phase="moving"),
            lambda b: b.update(torque_state="unknown"),
            lambda b: b.update(torque_state="single_joint_enabled"),
            lambda b: b.update(torque_off_pending={"ids": [11]}),
            lambda b: b.update(power_pending=True),
            lambda b: b.update(motion={"id": 11}),
            lambda b: b.update(policy_enabled=True),
            lambda b: b["native"].update(torque_state_confirmed=False),
            *[lambda b, e=e: b["hold"].update(max_error_deg=e)
              for e in (None, True, -1, 5.1, float("nan"), float("inf"))],
        ]
        for index, mutate in enumerate(mutations):
            bus = commissioning_bus()
            bus.update(phase="holding", torque_state="all_enabled",
                       hold={"stage": "holding", "max_error_deg": 0.6})
            mutate(bus)
            backend = self.backend(bus=bus, calib=factory_calib())
            with self.subTest(index=index), self.assertRaises(Refused):
                asyncio.run(backend.handle({"op": "coarse-capture", "phase": "still", "seconds": 0.5}))

    def test_redo_coarse_capture_and_solve_while_holding_home(self):
        from studio import helper
        bus = commissioning_bus()
        bus.update(phase="holding", torque_state="all_enabled",
                   hold={"stage": "holding", "max_error_deg": 0.6})
        backend = self.backend(bus=bus, calib=factory_calib())
        session = {"active": True, "phase": "commissioning", "source": "bus", "session_id": "c" * 32}
        original_joints = copy.deepcopy(backend.rd.calib.value["calibration"]["joints"])
        with mock.patch.object(helper, "imu_redo_state", return_value=session):
            still = asyncio.run(backend.handle({"op": "coarse-capture", "phase": "still", "seconds": 3}))
            a = math.radians(20)
            bus["last_sample"]["imu"]["gravity"] = [math.sin(a), 0, -math.cos(a)]
            fwd = asyncio.run(backend.handle({"op": "coarse-capture", "phase": "fwd", "seconds": 3}))
            solved = asyncio.run(backend.handle({"op": "solve", "mode": "coarse",
                                                "samples": still["samples"] + fwd["samples"]}))
        self.assertTrue(solved["ok"], solved)
        self.assertEqual(bus["torque_state"], "all_enabled")
        self.assertEqual(backend.rd.calib.value["calibration"]["joints"], original_joints)

    def test_redo_held_capture_keeps_motion_and_session_guards(self):
        from studio import helper
        for change, session_change in (
            (lambda b: b["hold"].update(stage="ramping"), {}),
            (lambda b: b["hold"].update(max_error_deg=5.1), {}),
            (lambda b: b.update(policy_enabled=True), {}),
            (lambda b: b.update(power_pending=True), {}),
            (lambda b: b.update(mode="motion"), {}),
            (lambda b: None, {"phase": "cancelling"}),
            (lambda b: None, {"source": "bno08x"}),
        ):
            bus = commissioning_bus()
            bus.update(phase="holding", torque_state="all_enabled",
                       hold={"stage": "holding", "max_error_deg": 0.6})
            change(bus)
            backend = self.backend(bus=bus, calib=factory_calib())
            session = {"active": True, "phase": "commissioning", "source": "bus",
                       "session_id": "c" * 32, **session_change}
            with self.subTest(session=session_change), mock.patch.object(helper, "imu_redo_state", return_value=session), self.assertRaises(Refused):
                backend._coarse_guard(bus)

    def test_redo_verify_while_holding_requires_unload_before_rpc(self):
        from studio import helper
        bus = commissioning_bus()
        bus.update(phase="holding", torque_state="all_enabled", hold={"stage": "holding", "max_error_deg": 0.6})
        backend = self.backend(bus=bus, calib=factory_calib())
        backend.last_result = {"ok": True, "mode": "coarse", "mount": [1., 0., 0., 0.]}
        backend._rpc_once = mock.AsyncMock(return_value={"accepted": True})
        session = {"active": True, "phase": "commissioning", "source": "bus", "session_id": "c" * 32}
        with mock.patch.object(helper, "imu_redo_state", return_value=session), self.assertRaisesRegex(Refused, "卸力"):
            asyncio.run(backend.handle({"op": "verify", "confirmed": True, "mount_wxyz": [1., 0., 0., 0.]}))
        backend._rpc_once.assert_not_awaited()
        self.assertIsNotNone(backend.last_result)

    def test_hold_blocks_collection_and_mode_switch_until_completion(self):
        from studio import helper
        backend = self.backend(bus=commissioning_bus(), calib=factory_calib())
        backend.commit = SimpleNamespace(hold_directions=lambda: [-1] * 15)
        session = {"active": True, "phase": "commissioning", "source": "bus", "session_id": "c" * 32}
        async def hold_rpc(*args):
            self.assertTrue(backend._redo_busy)
            with self.assertRaises(Refused):
                backend._coarse_guard(backend.rd.bus.value)
            with self.assertRaises(Refused):
                await backend.handle({"op": "redo-cancel", "confirmed": True})
            return {"accepted": True}
        backend._rpc_once = hold_rpc
        with mock.patch.object(helper, "imu_redo_state", return_value=session), mock.patch.object(helper, "imu_recalibrate") as switch:
            self.assertTrue(asyncio.run(backend.handle({"op": "hold"}))["accepted"])
            switch.assert_not_called()
        self.assertFalse(backend._redo_busy)

    def test_hold_rejects_wrong_session_and_releases_busy_on_rpc_error(self):
        from studio import helper
        backend = self.backend(bus=commissioning_bus(), calib=factory_calib())
        backend.commit = SimpleNamespace(hold_directions=lambda: [-1] * 15)
        backend._rpc_once = mock.AsyncMock(side_effect=RpcError("robot.busCommand", -32603, "relax cancelled"))
        session = {"active": True, "phase": "commissioning", "source": "bus", "session_id": "c" * 32}
        for key, value in (("phase", "finishing"), ("source", "bno08x")):
            with mock.patch.object(helper, "imu_redo_state", return_value={**session, key: value}), self.assertRaises(Refused):
                asyncio.run(backend.handle({"op": "hold"}))
        backend._rpc_once.assert_not_awaited()
        with mock.patch.object(helper, "imu_redo_state", return_value=session), self.assertRaises(Refused):
            asyncio.run(backend.handle({"op": "hold"}))
        backend._rpc_once.assert_awaited_once()
        self.assertFalse(backend._redo_busy)

    def test_redo_mode_switch_rechecks_busy_after_async_preflight(self):
        from studio import helper
        bus = commissioning_bus(mode="motion")
        backend = self.backend(bus=bus, calib=factory_calib())
        async def concurrent_hold_started():
            backend._redo_busy = True
            return bus
        backend._bus_once = concurrent_hold_started
        with mock.patch.object(helper, "imu_redo_state", return_value={"active": False}), \
             mock.patch.object(helper, "available", return_value=True), \
             mock.patch.object(helper, "imu_recalibrate", return_value=(True, "imu_entered", {})) as switch, self.assertRaises(Refused):
            asyncio.run(backend.handle({"op": "redo-start", "confirmed": True}))
        switch.assert_not_called()

    def test_hold_refuses_active_capture_or_verification_job(self):
        for title in ("粗校采数", "交给主程序验证"):
            backend = self.backend(bus=commissioning_bus(), calib=factory_calib())
            backend.commit = SimpleNamespace(hold_directions=lambda: [-1] * 15)
            backend._rpc_once = mock.AsyncMock()
            backend.job = {"kind": "coarse", "title": title}
            with self.subTest(title=title), self.assertRaises(Refused):
                asyncio.run(backend.handle({"op": "hold"}))
            backend._rpc_once.assert_not_awaited()

    def test_coarse_capture_rejects_source_change_mid_collection(self):
        b = self.backend(bus=commissioning_bus(), calib=factory_calib())
        calls = {"n": 0}
        async def bus_once():
            calls["n"] += 1
            return commissioning_bus("bus" if calls["n"] == 1 else "lsm6dsv16x")
        b._bus_once = bus_once
        with self.assertRaisesRegex(Refused, "来源"):
            asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 0.5}))
        self.assertIs((b.job or {}).get("failed"), True)

    def test_coarse_capture_rpc_failure_ends_job_and_keeps_retry_available(self):
        b = self.backend(bus=commissioning_bus(), calib=factory_calib())
        async def fail():
            raise RpcError("robot.busStatus", -1, "fresh status unavailable")
        b._bus_once = fail
        with self.assertRaises((RpcError, Refused)):
            asyncio.run(b.handle({"op": "coarse-capture", "phase": "still", "seconds": 0.5}))
        self.assertIs((b.job or {}).get("failed"), True)

    def test_redo_start_requires_confirmation_and_runs_helper_off_event_loop(self):
        from studio import helper
        import threading
        bus = commissioning_bus(mode="motion")
        b = self.backend(bus=bus, calib=factory_calib())
        with mock.patch.object(helper, "available", return_value=True), mock.patch.object(helper, "imu_recalibrate") as redo:
            with self.assertRaises(Refused):
                asyncio.run(b.handle({"op": "redo-start", "confirmed": False}))
            redo.assert_not_called()
            seen = []
            def enter(*a, **kw):
                seen.append(threading.current_thread())
                b.rd.bus.set(commissioning_bus())
                return True, "imu_entered", {"active": True, "phase": "commissioning", "session_id": "a"*32, "source": "bus"}
            redo.side_effect = enter
            res = asyncio.run(b.handle({"op": "redo-start", "confirmed": True}, "test"))
        self.assertTrue(res["ok"])
        self.assertIsNot(seen[0], threading.current_thread())
        self.assertEqual(redo.call_args.args[:2], ("test", "enter"))

    def test_redo_old_helper_and_denied_sudo_explain_required_install_update(self):
        from studio import helper
        for result in ("bad_verb", "denied", "sudo: a password is required"):
            b = self.backend(bus=commissioning_bus(mode="motion"), calib=factory_calib())
            with mock.patch.object(helper, "available", return_value=True), \
                 mock.patch.object(helper, "imu_recalibrate", return_value=(False, result, None)):
                with self.assertRaisesRegex(Refused, "新版.*小助手.*sudoers"):
                    asyncio.run(b.handle({"op": "redo-start", "confirmed": True}))

    def test_source_mode_or_session_change_clears_stale_solve(self):
        b = self.backend(bus=commissioning_bus(), calib=factory_calib())
        b.view()
        b.last_result = {"ok": True, "mode": "coarse", "mount": [1.,0.,0.,0.]}
        b.rd.bus.value["native"]["imu_source"] = "lsm6dsv16x"
        b.view()
        self.assertIsNone(b.last_result)

    def test_redo_verification_finishes_with_expected_session_source_and_quaternion(self):
        from studio import helper
        b = self.backend(bus=commissioning_bus(), calib=factory_calib())
        session = {"active": True, "phase": "commissioning", "session_id": "b"*32, "source": "bus"}
        q = [0.5]*4
        b.last_result = {"ok": True, "mode": "coarse", "mount": q}
        def finish(by, action, **kw):
            self.assertEqual(action, "finish")
            self.assertEqual(kw["source"], "bus")
            self.assertEqual(kw["quat"], q)
            self.assertEqual(kw["session_id"], "b"*32)
            session["active"] = False
            b.rd.bus.set(commissioning_bus(mode="motion"))
            return True, "imu_finished", {}
        with mock.patch.object(helper, "imu_redo_state", side_effect=lambda **_: dict(session)), \
             mock.patch.object(helper, "imu_recalibrate", side_effect=finish) as call:
            res = asyncio.run(b.handle({"op": "verify", "confirmed": True, "mount_wxyz": q}, "test"))
        self.assertTrue(res["ok"])
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
