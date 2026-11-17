"""舵机页「新舵机编号」：主板当调试板用，给出厂 1 号的新舵机编成它要装的位置（用户 11-17）。

用协议级模拟舵机跑：把 21 号改回出厂的 1 号，当成一颗刚拆封的新舵机。
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio.bus.sim_bus import SimServo  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.servo import ServoSession  # noqa: E402
from studio.servo_ops import IMU_BUS_ID, Refused  # noqa: E402


class NumberingTest(unittest.TestCase):
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
        self.s.sim_serial.servos[21].r[5] = 1          # 21 号位置上换了一颗刚拆封的：出厂 1 号

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def find(self):
        r = self.o.handle({"op": "find"})
        return r, {f["id"]: f for f in r["found"]}

    def test_find_shows_the_new_servo_and_the_empty_position(self):
        r, found = self.find()
        self.assertEqual(r["missing"], [21])
        self.assertEqual((found[1]["known"], found[1]["kind"], found[1]["firmware"]), (False, "servo", "3.46"))
        self.assertEqual((found[22]["known"], found[22]["joint"]), (True, "left_hip_pitch"))
        self.assertEqual(len(found), 15)

    def test_new_servo_is_numbered_into_its_position(self):
        r = self.o.handle({"op": "set_id", "old": 1, "new": 21})
        self.assertEqual(r, {"ok": True, "old": 1, "new": 21})
        r, found = self.find()
        self.assertEqual(r["missing"], [])
        self.assertNotIn(1, found)

    def test_position_that_is_already_taken_is_refused(self):
        with self.assertRaises(Refused) as e:
            self.o.handle({"op": "set_id", "old": 1, "new": 22})
        self.assertIn("会撞号", str(e.exception))
        self.assertEqual(self.find()[0]["missing"], [21])

    def test_source_that_is_not_on_the_bus_is_refused(self):
        with self.assertRaises(Refused) as e:
            self.o.handle({"op": "set_id", "old": 7, "new": 21})
        self.assertIn("找不到 7 号", str(e.exception))

    def test_imu_board_is_recognised_and_never_renumbered(self):
        self.s.sim_serial.servos[IMU_BUS_ID] = SimServo(IMU_BUS_ID)
        _, found = self.find()
        self.assertEqual((found[IMU_BUS_ID]["kind"], found[IMU_BUS_ID]["known"]), ("imu", False))
        for old, new in ((IMU_BUS_ID, 21), (1, IMU_BUS_ID)):
            with self.assertRaises(Refused):
                self.o.handle({"op": "set_id", "old": old, "new": new})
        self.assertEqual(self.s.sim_serial.servos[IMU_BUS_ID].id, IMU_BUS_ID)

    def test_config_tools_work_on_a_servo_outside_the_fifteen(self):
        dump = self.o.handle({"op": "dump", "id": 1})
        self.assertEqual(dump["id"], 1)
        r = self.o.handle({"op": "write_reg", "id": 1, "addr": 41, "size": 1, "value": 20, "unlock": False})
        self.assertEqual((r["new"], r["same"]), (20, True))


if __name__ == "__main__":
    unittest.main()
