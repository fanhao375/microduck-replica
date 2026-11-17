"""舵机页的操作（servo_ops，照搬 servo-web）：用协议级模拟舵机跑一遍。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio.log import Log  # noqa: E402
from studio.servo import ServoSession  # noqa: E402
from studio.servo_ops import Refused  # noqa: E402


class ServoOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = ServoSession(Log(Path(self.tmp.name) / "logs", echo=False, background=False), sim=True,
                              state_dir=self.tmp.name)
        ok, why = self.s.open("测试")
        self.assertTrue(ok, why)
        end = time.monotonic() + 2
        while len(self.s.rows) < 15 and time.monotonic() < end:
            time.sleep(0.02)
        self.o = self.s.ops

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def sim(self, i):
        return self.s.sim_serial.servos[i]

    def test_torque_on_aligns_goal_first(self):
        r = self.o.handle({"op": "torque", "on": 1, "ids": [22]})
        self.assertEqual(r["on"], [22])
        self.assertEqual(self.sim(22).r[40], 1)
        self.assertEqual(round(self.sim(22).pos()), 2347)          # 没冲：目标先对准了当前位置

    def test_slider_goal_moves_when_holding(self):
        self.o.handle({"op": "torque", "on": 1, "ids": [20]})
        self.o.handle({"op": "goal", "id": 20, "pos": 2300})
        self.assertEqual(round(self.sim(20).pos()), 2300)

    def test_manual_goal_overrides_a_transition_still_running(self):
        """用户 11-18 点「+」：上一次 2 秒的平滑转动还没走完，它收尾时不能把手点的目标写回去。"""
        self.o.handle({"op": "torque", "on": 1, "ids": [20]})
        self.o.handle({"op": "move", "goals": {20: 2200}, "seconds": 1.0})
        time.sleep(0.2)
        self.o.handle({"op": "goal", "id": 20, "pos": 2111})
        time.sleep(1.6)
        self.assertEqual(round(self.sim(20).pos()), 2111)

    def test_calibrate_sets_2048_and_holds(self):
        plan = self.o.handle({"op": "calib_plan", "ids": "22"})
        self.assertAlmostEqual(plan["rows"][0]["diff_deg"], -26.3, delta=0.1)
        r = self.o.handle({"op": "calibrate", "ids": "22"})
        self.assertEqual(r["ok"], [22])
        self.assertEqual(round(self.sim(22).pos()), 2048)
        self.assertEqual(self.sim(22).r[40], 1)                     # 用户要求：校完马上保持
        self.assertEqual(r["hold"]["on"], [22])
        u = self.o.handle({"op": "undo"})
        self.assertEqual(u["ok"], [22])
        self.assertEqual(round(self.sim(22).pos()), 2347)          # 撤销回原来的偏移

    def test_calibrate_with_torque_on_and_minus_offset_does_not_double_the_error(self):
        """10-19 二号鸭 #24：扭矩开着校，偏移符号探针探不出（舵机顶回目标、读数不动），以前默认按「+」补下垂，
        HD-1910 实际是「读数 = 原始 − 偏移」→ 误差翻倍（16→32、43→86、87→174 步）。现在从这次 0x0B 反推方向。"""
        for droop in (0.0, 0.5):
            with self.subTest(droop=droop):
                self.o.offset_sign = None
                sv = self.sim(22)
                sv.offset_adds = False
                sv.droop = droop
                self.o.handle({"op": "torque", "on": 1, "ids": [22]})
                self.o.handle({"op": "goal", "id": 22, "pos": 2120})      # 摆到离零位 72 步的地方
                phys = sv.phys                                   # 用户摆好的位置（扭矩开着顶在这里）
                r = self.o.handle({"op": "calibrate", "ids": "22"})
                self.assertEqual(r["ok"], [22])
                self.assertEqual(self.o.offset_sign, -1)         # 反推出来了
                at_phys = phys - sv.off                          # 摆好的那个位置现在读多少
                self.assertLessEqual(abs(at_phys - 2048), 3, f"摆好的位置读 {at_phys}，不是 2048")
                sv.droop = 0.0

    def test_infer_offset_sign(self):
        from studio.servo_ops import infer_offset_sign
        self.assertEqual(infer_offset_sign(2064, 2048, 16), -1)      # #24 第一次：读数降 16、偏移加 16
        self.assertEqual(infer_offset_sign(2064, 2048, -16), 1)
        self.assertEqual(infer_offset_sign(2064, 2046, 16), -1)      # 中间又垂了 2 步也认得
        self.assertIsNone(infer_offset_sign(2050, 2048, 2))          # 变得太少，分不清
        self.assertIsNone(infer_offset_sign(2100, 2048, 16))         # 两个方向都对不上

    def test_latched_refuses_motion_until_manual_torque_on(self):
        """卸力以后：拖滑块、走姿势、校准都被拒绝；人自己点「扭矩开 / 保持」就算解除（用户不想再点「解除锁定」）。"""
        self.assertTrue(self.s.relax_all("测试")[0])
        for args in ({"op": "goal", "id": 20, "pos": 2100}, {"op": "calibrate", "ids": "20"},
                     {"op": "official", "key": "stand"}, {"op": "idle", "on": True}):
            with self.assertRaises(Refused):
                self.o.handle(args)
        self.assertEqual(self.o.handle({"op": "torque", "on": 1, "ids": [20]})["on"], [20])
        self.assertFalse(self.s.latched)
        self.o.handle({"op": "goal", "id": 20, "pos": 2100})

    def test_official_pose_and_saved_poses(self):
        self.o.handle({"op": "torque", "on": 1})
        self.o.handle({"op": "official", "key": "zero", "seconds": 0.2, "mode": "stream"})
        time.sleep(0.8)
        self.assertEqual(round(self.sim(22).pos()), 2048)
        poses = self.o.handle({"op": "save_pose", "name": "零位存档"})["poses"]
        self.assertEqual(poses[0]["name"], "零位存档")
        self.assertEqual(self.o.handle({"op": "delete_pose", "file": poses[0]["file"]})["poses"], [])

    def test_tools(self):
        self.assertEqual(self.o.handle({"op": "scan", "ids": "20-24"})["found"], [20, 21, 22, 23, 24])
        self.assertEqual(len(self.o.handle({"op": "dump", "id": 20})["rows"]), 62)    # 含 HD-1910 运行中的 P/D/I（50–52）
        self.assertEqual(self.o.handle({"op": "timing", "n": 10})["fails"], 0)
        with self.assertRaises(Refused):
            self.o.handle({"op": "write_reg", "id": 20, "addr": 40, "value": 1})   # 扭矩开关不让从这里写
        with self.assertRaises(Refused):
            self.o.handle({"op": "nope"})


if __name__ == "__main__":
    unittest.main()
