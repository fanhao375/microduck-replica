"""模拟模式：虚拟舵机按速度 / 加速度走梯形曲线，页面上接 / 拔虚拟 IMU 小板。不接硬件。
还有 0.15.0 审查修的：开扭矩先对齐目标、收尾不改目标、模拟校准不碰真的 calib/ 和 poses/。
Run: python -m pytest -q test_sim.py
"""
import asyncio
import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import imu_bus
import server

IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def profile(bus, sid, goal, speed, acc=0):
    """跟 server 的 goals_profile 一样写 41..47：加速度、目标、时间 0、速度。"""
    bus.sync_write(41, 7, [(sid, bytes([acc]) + struct.pack("<H", goal) + struct.pack("<H", 0)
                            + struct.pack("<H", speed))])


class FakeServoMotionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(server.time, "monotonic", self.clock))
        stack.enter_context(patch.object(server, "SPEED_UNIT", 1.0))
        self.bus = server.FakeBus([20, 21])

    def run_for(self, seconds, step=0.01):
        for _ in range(int(round(seconds / step))):
            self.clock.t += step
            self.bus.states([20, 21])

    def test_torque_on_by_default_and_moves_at_the_profile_speed(self):
        profile(self.bus, 20, 3048, speed=1000)            # 1000 步，1000 步/秒 → 约 1 秒
        self.run_for(0.5)
        mid = self.bus.states([20])[20]["pos"]
        self.assertTrue(400 < mid - 2048 < 600, mid)
        self.run_for(0.7)
        self.assertEqual(self.bus.states([20])[20]["pos"], 3048)
        self.assertEqual(self.bus.states([20])[20]["moving"], 0)

    def test_acceleration_limit_ramps_up(self):
        profile(self.bus, 20, 4000, speed=3000, acc=10)    # 10 × 8.7°/s² ≈ 990 步/s²
        self.run_for(0.2)
        moved = self.bus.states([20])[20]["pos"] - 2048
        self.assertTrue(10 < moved < 40, moved)             # ½·a·t² ≈ 20 步，远不到满速的 600

    def test_unlimited_speed_reaches_quickly_and_torque_off_freezes(self):
        self.bus.write_u16(21, 42, 2548)                    # 滑块：只写目标，速度不限
        self.run_for(0.3)
        self.assertEqual(self.bus.states([21])[21]["pos"], 2548)
        self.bus.write_u8(21, 40, 0)
        self.bus.write_u16(21, 42, 1548)
        self.run_for(0.5)
        self.assertEqual(self.bus.states([21])[21]["pos"], 2548)   # 扭矩关：停在原地

    def test_calibrate_and_readback(self):
        self.bus.calibrate_to(20, 2100)
        self.assertEqual(self.bus.read_u16(20, 56), 2100)

    def test_offset_register_is_sign_magnitude(self):
        self.bus.write_u8(20, 40, 0)                        # 扭矩关：读数只随偏移变，不会被拉回目标
        with patch.object(server, "BUS", self.bus):
            server.write_off(20, -5)
            self.assertEqual(self.bus.read_u16(20, 31), 0x8005)   # 跟真舵机一样 BIT15 = 负，不是补码
            self.assertEqual(server.read_off(20), -5)
            self.assertEqual(server.read_pos(20), 2043)
            server.write_off(20, 12)
            self.assertEqual((server.read_off(20), server.read_pos(20)), (12, 2060))

    def test_goal_written_while_torque_off_is_kept_for_later(self):
        self.bus.write_u8(20, 40, 0)
        self.bus.write_u16(20, 42, 3000)
        self.run_for(0.5)
        self.assertEqual(self.bus.states([20])[20]["pos"], 2048)   # 关着：记住目标，不动
        self.bus.write_u8(20, 40, 1)
        self.run_for(1.0)
        self.assertEqual(self.bus.states([20])[20]["pos"], 3000)   # 这就是审查说的「一开扭矩就冲过去」


class RecordingBus(server.FakeBus):
    """记下每次写了哪个地址；dead 里的 ID 读状态读不到。"""

    def __init__(self, ids):
        super().__init__(ids)
        self.writes, self.dead = [], set()

    def sync_write(self, addr, length, id_values):
        self.writes.append((addr, length, sorted(i for i, _ in id_values)))
        return super().sync_write(addr, length, id_values)

    def write_u16(self, sid, addr, v):
        self.writes.append((addr, 2, [sid]))
        return super().write_u16(sid, addr, v)

    def states(self, ids):
        out = super().states(ids)
        return {i: (None if i in self.dead else v) for i, v in out.items()}


class TorqueAndSpeedTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(server.time, "monotonic", self.clock))
        self.bus = RecordingBus([20, 21, 22])
        stack.enter_context(patch.multiple(server, BUS=self.bus, IDS=[20, 21, 22], PRESENT=[20, 21, 22], LOG=[],
                                           SPEED_UNIT=1.0, MAX_SPEED_REG=3000))

    def run_for(self, seconds, step=0.01):
        for _ in range(int(round(seconds / step))):
            self.clock.t += step
            self.bus.states([20, 21, 22])

    def test_torque_on_aligns_goal_to_where_the_joint_was_moved_by_hand(self):
        self.bus.write_u8(20, 40, 0)
        self.bus.write_u16(20, 42, 3000)                    # 扭矩关着时发过姿势（或者校准过），目标还在 3000
        self.bus.pos[20] = 1500.0                           # 然后用手把关节掰到 1500
        self.bus.write_u16(21, 42, 2548)                    # 21 一直开着，正在往 2548 走
        r = server.handle({"op": "torque", "ids": [20, 21], "on": 1})
        self.assertEqual(r["aligned"], {"20": 1500})        # 只对齐关着的那颗，开着的不动它的目标
        self.assertEqual(sorted(r["ids"]), [20, 21])
        self.assertEqual(self.bus.torque[20], 1)
        self.assertEqual(self.bus.goal[21], 2548)
        self.run_for(1.0)
        self.assertEqual(self.bus.states([20])[20]["pos"], 1500)    # 不跳
        order = [w[0] for w in self.bus.writes]
        self.assertLess(order.index(42), order.index(40))   # 先写目标，再开扭矩

    def test_torque_stays_off_when_position_cannot_be_read(self):
        self.bus.write_u8(22, 40, 0)
        self.bus.dead = {22}
        r = server.handle({"op": "torque", "ids": [22], "on": 1})
        self.assertIn("22", r["bad"])
        self.assertEqual(self.bus.torque[22], 0)            # 读不到位置就不开，免得冲向旧目标

    def test_release_speed_never_rewrites_the_goal(self):
        self.bus.write_u16(20, 42, 1234)
        self.bus.writes.clear()
        server.release_speed([20, 21])
        self.assertEqual([w[:2] for w in self.bus.writes], [(41, 1), (46, 2)])
        self.assertEqual(self.bus.goal[20], 1234)
        self.assertEqual((self.bus.acc_reg[20], self.bus.speed_reg[20]), (0, 3000))


class ConnectReadOnlyTests(unittest.TestCase):
    def test_connect_preserves_existing_motion_settings_and_only_sends_reads(self):
        from sim_bus import SimSerial

        for phase, unit, max_speed in [(52, 1.0, 3000), (0, 50.0, 60), (12, 1.0, 0)]:
            with self.subTest(phase=phase), contextlib.ExitStack() as stack:
                port = SimSerial([20, 21])
                for servo in port.servos.values():
                    servo.r[18], servo.r[40], servo.r[41] = phase, 0, 7
                    struct.pack_into("<H", servo.r, 42, 2100)
                    struct.pack_into("<H", servo.r, 46, 100)
                before = {sid: bytes(servo.r[:56]) for sid, servo in port.servos.items()}
                bus = server.feetech.FeetechBus(None, ser=port)
                sent = stack.enter_context(patch.object(port, "write", wraps=port.write))
                stack.enter_context(patch.object(server.feetech, "FeetechBus", return_value=bus))
                stack.enter_context(patch.object(server, "log"))
                stack.enter_context(patch.multiple(server, BUS=None, IDS=[20, 21], PRESENT=[],
                                                   IMU_SERVICE=None, SPEED_UNIT=1.0,
                                                   MAX_SPEED_REG=3000, PORT=None, BAUD=1_000_000))
                self.assertTrue(server.open_bus("test-port")["ok"])
                self.assertEqual(server.PRESENT, [20, 21])
                self.assertEqual((server.SPEED_UNIT, server.MAX_SPEED_REG), (unit, max_speed))
                self.assertTrue(all(call.args[0][4] in (1, 2) for call in sent.call_args_list))
                self.assertEqual({sid: bytes(servo.r[:56]) for sid, servo in port.servos.items()}, before)


class SimCalibrationTests(unittest.TestCase):
    """模拟模式按官方折叠校准：只改虚拟舵机，真的 calib/ 和 poses/ 一个字节都不动。"""

    def setUp(self):
        self.clock = Clock()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(server.time, "monotonic", self.clock))
        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        real_calib, poses = os.path.join(tmp, "calib"), os.path.join(tmp, "poses")
        os.makedirs(real_calib)
        os.makedirs(poses)
        # 真 calib/ 里有一份上次真机校准的备份：模拟里点撤销绝不能拿它去写
        self.real_backup = os.path.join(real_calib, "eeprom-20260920-015310000.json")
        with open(self.real_backup, "w", encoding="utf-8") as f:
            json.dump({"time": "2026-09-20 01:53:10", "servos": {"20": {"offset": 33}}, "shifted": []}, f)
        self.pose = os.path.join(poses, "站-2026-09-20.json")
        with open(self.pose, "w", encoding="utf-8") as f:
            json.dump({"name": "站", "time": "2026-09-20 02:00", "rows": [{"id": i, "pos": 2100} for i in IDS]}, f)
        dirs = os.path.join(tmp, "directions.json")
        with open(dirs, "w", encoding="utf-8") as f:
            json.dump({str(i): -1 for i in IDS}, f)         # 跟实机一致：15 颗全 −1
        self.snap = {p: open(p, "rb").read() for p in (self.real_backup, self.pose)}
        self.bus = server.FakeBus(IDS)
        stack.enter_context(patch.multiple(server, BUS=self.bus, IDS=IDS, PRESENT=IDS, LOG=[], OFFSET_SIGN=None,
                                           CALIB_DIR=real_calib, POSES_DIR=poses, DIRS_FILE=dirs,
                                           LOG_DIR=os.path.join(tmp, "logs"), SIM_CALIB_DIR=None))
        self.real_calib = real_calib

    def fold_by_hand(self, errors=None):
        """扭矩关着，用手摆成校准用折叠；读数离目标差 errors 步（零点没校准时就是这样）。"""
        _, want = server.official_targets("@official:FOLD")
        for i in IDS:
            self.bus.torque[i] = 0
            self.bus.pos[i] = float(want[i] + (errors or {}).get(i, 3))
        return want

    def test_official_fold_targets_with_all_minus_one(self):
        _, want = server.official_targets("@official:FOLD")
        self.assertEqual((want[22], want[23], want[12], want[13]), (1025, 1025, 3071, 3071))
        self.assertTrue(all(want[i] == 2048 for i in IDS if i not in (22, 23, 12, 13)))   # 头颈摆正、嘴闭上

    def test_plan_lists_current_target_and_degrees(self):
        self.fold_by_hand({12: -1707})                      # 右髋 pitch 差 150°：装的时候转了（或 ± 反了）
        m = server.handle({"op": "calib_plan", "ids": IDS, "ref": "@official:FOLD", "for": "all"})
        rows = {r["id"]: r for r in m["rows"]}
        self.assertEqual((rows[22]["pos"], rows[22]["target"], rows[22]["diff_deg"]), (1028, 1025, -0.3))
        self.assertEqual(rows[12]["diff_deg"], 150.0)
        self.assertEqual((m["big_deg"], m["fake"], m["for"]), (30, True, "all"))

    def test_calibrate_with_torque_off_then_torque_on_does_not_jump(self):
        want = self.fold_by_hand({12: -1707, 22: 10})
        r = server.handle({"op": "calib_mid_all", "ids": IDS, "ref": "@official:FOLD"})
        self.assertEqual(sorted(r["ok"]), sorted(IDS))
        self.assertEqual(list(r["big"]), ["12"])            # 差得多的不拦，只标出来
        self.assertTrue(any("30° 以上" in e["msg"] and e["level"] == "warn" for e in server.LOG))
        st = self.bus.states(IDS)
        self.assertTrue(all(st[i]["pos"] == want[i] for i in IDS))
        self.assertTrue(all(self.bus.torque[i] == 0 for i in IDS))            # 扭矩原来关着，校完还关着
        self.assertTrue(all(self.bus.goal[i] == st[i]["pos"] for i in IDS))   # 目标 = 当前读数
        for i in IDS:                                       # 用手把鸭子展开，再开扭矩
            self.bus.pos[i] = 2048.0
        server.handle({"op": "torque", "on": 1})
        for _ in range(100):
            self.clock.t += 0.01
            self.bus.states(IDS)
        self.assertTrue(all(self.bus.states([i])[i]["pos"] == 2048 for i in IDS))   # 不会甩回折叠

    def test_sim_calibration_and_undo_never_touch_real_files(self):
        before = self.fold_by_hand()
        server.handle({"op": "calib_mid_all", "ids": IDS, "ref": "@official:FOLD"})
        self.assertEqual(sorted(os.listdir(self.real_calib)), ["eeprom-20260920-015310000.json"])
        self.assertEqual({p: open(p, "rb").read() for p in self.snap}, self.snap)
        sim = [f for f in os.listdir(server.SIM_CALIB_DIR) if f.startswith("eeprom-")]
        self.assertEqual(len(sim), 1)
        r = server.handle({"op": "calib_undo"})
        self.assertEqual(sorted(r["ok"]), sorted(IDS))
        st = self.bus.states(IDS)
        self.assertTrue(all(st[i]["pos"] == before[i] + 3 for i in IDS))    # 虚拟舵机回到校准前
        self.assertEqual(sorted(os.listdir(self.real_calib)), ["eeprom-20260920-015310000.json"])   # 没被改名 undone-
        self.assertEqual({p: open(p, "rb").read() for p in self.snap}, self.snap)


class SimImuTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.bus = server.FakeBus([20, 21])
        stack.enter_context(patch.multiple(server, BUS=self.bus, IMU_SERVICE=None, SIM_IMU_OWNED=False,
                                           LOG=[], PORT=None))

    def test_plug_lose_and_unplug_the_virtual_board(self):
        self.assertEqual(server.sim_imu_mode(), "off")
        r = server.set_sim_imu("on")
        self.assertEqual((r["enabled"], r["sim_imu"]), (True, "on"))
        self.assertEqual(server.imu_backend(server.IMU_SERVICE), "bus")
        self.assertIsNotNone(self.bus.imu200)
        self.assertEqual(self.bus.ping(200), 0)
        r = server.set_sim_imu("lost")
        self.assertEqual(r["sim_imu"], "lost")
        self.assertIsNone(self.bus.ping(200))
        r = server.set_sim_imu("off")
        self.assertEqual((r["enabled"], r["sim_imu"]), (False, "off"))
        self.assertIsNone(server.IMU_SERVICE)

    def test_lost_board_degrades_then_recovers_through_ping(self):
        server.set_sim_imu("on")
        self.bus.imu200 = imu_bus.FakeImu200(ready_after_s=0)
        server.read_states_with_imu([20, 21], server.IMU_SERVICE)
        self.assertTrue(server.IMU_SERVICE.get_status()["live"])
        server.set_sim_imu("lost")
        for _ in range(imu_bus.MISS_DEGRADE):
            server.read_states_with_imu([20, 21], server.IMU_SERVICE)
        self.assertFalse(server.IMU_SERVICE.include_in_sync())       # 出列，舵机单独读
        server.set_sim_imu("on")
        server.IMU_SERVICE.last_probe = None
        server.read_states_with_imu([20, 21], server.IMU_SERVICE)    # 这一帧单独 PING，放回
        self.assertTrue(server.IMU_SERVICE.include_in_sync())

    def test_refused_on_real_bus_or_when_started_with_an_imu(self):
        with patch.object(server, "BUS", object()), self.assertRaises(ValueError):
            server.set_sim_imu("on")
        with patch.object(server, "IMU_SERVICE", object()), self.assertRaises(ValueError):
            server.set_sim_imu("on")
        with self.assertRaises(ValueError):
            server.set_sim_imu("maybe")

    def test_connecting_a_real_port_unplugs_the_virtual_board(self):
        server.set_sim_imu("on")

        class Port:
            trace = None

            def __init__(self, port, baud):
                pass

        with patch.object(server.feetech, "FeetechBus", Port), patch.object(server, "do_scan", lambda ids: []), \
                patch.object(server, "read_phase", lambda: None), patch.object(server, "IDS", [20]):
            self.assertTrue(server.open_bus("COM_TEST", 1_000_000)["ok"])
        self.assertIsNone(server.IMU_SERVICE)
        self.assertFalse(server.SIM_IMU_OWNED)
        self.assertEqual(server.imu_config_frame(),
                         {"type": "imu_config", "enabled": False, "protocol": None, "sim_imu": None})

    def test_imu_stream_dynamic_follows_plug_and_unplug(self):
        seen = []

        async def fake_stream(svc, startup_error=None):
            seen.append(("start", svc))
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                seen.append(("stop", svc))
                raise

        async def run():
            task = asyncio.create_task(server.imu_stream_dynamic())
            await asyncio.sleep(0.05)
            svc = server.set_sim_imu("on") and server.IMU_SERVICE
            await asyncio.sleep(0.3)
            server.set_sim_imu("off")
            await asyncio.sleep(0.3)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return svc

        with patch.object(server, "imu_stream", fake_stream):
            svc = asyncio.run(run())
        self.assertEqual(seen, [("start", svc), ("stop", svc)])


if __name__ == "__main__":
    unittest.main()
