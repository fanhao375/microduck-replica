"""关节跟踪：用协议级模拟舵机，不碰真串口。"""
from pathlib import Path
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "payload/opt/microduck-studio/app"
sys.path[:0] = [str(APP / "deps"), str(APP)]
from studio.leg_tracking import TrackingController, SessionBus  # noqa: E402
from studio.log import Log  # noqa: E402
from studio.servo import ServoSession, Refused  # noqa: E402
from studio import leg_tracking_probe as probe  # noqa: E402


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        directory = Path(self.tmp.name)
        self.s = ServoSession(Log(directory / "logs", echo=False, background=False),
                              sim=True, state_dir=self.tmp.name)
        self.assertTrue(self.s.open("test")[0])
        calibration = directory / "calibration.toml"
        joints = ",\n".join(
            "{ id = %d, direction = -1, zero_raw = 2048, calibrated = true, min_rad = -3.0, max_rad = 3.0 }"
            % sid for sid in probe.ALL_IDS)
        calibration.write_text("joints = [" + joints + "]")
        self.c = TrackingController(self.s, self.tmp.name, calibration)
        self.s.tracking = self.c

    def tearDown(self):
        self.c.shutdown()
        self.s.close()
        self.tmp.cleanup()

    def options(self):
        return {"ids": [21], "amplitude_deg": 3, "supported": True, "kp": 5, "kd": 0}

    def short_stages(self, *_args, **_kwargs):
        return [{"ids": (21,), "kind": "ramp_hold", "hz": 20, "ramp_samples": 2,
                 "values_deg": [0, 1, 2, 3, 3, 3]}]

    def test_status_answers_before_a_trial(self):
        status = self.c.handle({"op": "status"}, "owner")
        self.assertFalse(status["active"])
        self.assertEqual(status["phase"], "idle")

    def test_protocol_trial_finishes_and_exports_real_generated_records(self):
        servo = self.s.sim_serial.servos[21]
        before = bytes(servo.r[50:53])
        with patch.object(probe, "build_stages", self.short_stages):
            started = self.c.start(self.options(), "owner")
            while self.c.active:
                self.c.snapshot("owner")
                time.sleep(0.05)
        snapshot = self.c.snapshot("owner")
        self.assertTrue(snapshot["result"]["completed"], snapshot)
        self.assertTrue(snapshot["report"]["stages"])
        self.assertTrue(self.c.export_path(started["run_id"], "capture").is_file())
        self.assertTrue(self.c.export_path(started["run_id"], "report").is_file())
        self.assertEqual(bytes(servo.r[50:53]), before)
        self.assertTrue(all(s.r[40] == 0 for s in self.s.sim_serial.servos.values()))
        self.assertFalse(snapshot["report"]["walking_verified"])

    def test_other_servo_operations_are_blocked_and_disconnect_stops(self):
        self.c.start(self.options(), "owner")
        with self.assertRaises(Refused):
            self.s.op_begin("goal")
        self.c.disconnect("owner")
        self.c.shutdown()
        self.assertFalse(self.c.active)
        self.assertTrue(all(s.r[40] == 0 for s in self.s.sim_serial.servos.values()))

    def test_estop_latch_blocks_following_writes(self):
        self.c.active = True
        self.c.lease_at = time.monotonic()
        adapter = SessionBus(self.c, lambda _: None, {21: 2048}, {}, 35)
        ok, _ = self.s.relax_all("test")
        self.assertTrue(ok)
        for address, data in ((40, b"\1"), (42, b"\x00\x08")):
            with self.assertRaises(RuntimeError):
                adapter.write(21, address, data)
        self.c.active = False

    def test_validation_and_download_path_boundaries(self):
        for changes in ({"supported": False}, {"ids": [30]}, {"ids": [21, 21]}, {"kp": 33},
                        {"kd": True}, {"amplitude_deg": float("nan")}, {"ids": [21, 11]}):
            with self.subTest(changes=changes), self.assertRaises(Refused):
                self.c.validate({**self.options(), **changes})
        for run_id, kind in (("../secret", "capture"), ("a" * 32, "../../secret")):
            with self.assertRaises(Refused):
                self.c.export_path(run_id, kind)

    def test_readable_calibration_does_not_ask_the_helper(self):
        with patch("studio.helper.calib_read", side_effect=AssertionError("helper")), \
             patch.object(probe, "build_stages", self.short_stages):
            self.c.start(self.options(), "owner")
            while self.c.active:
                self.c.snapshot("owner")
                time.sleep(0.05)
        self.assertEqual(self.c.snapshot("owner")["result"]["completed"], True)

    def test_unreadable_calibration_uses_helper_and_still_runs(self):
        parsed = tomllib.loads(self.c.calibration.read_text())
        self.c.calibration = Path(self.tmp.name) / "missing.toml"
        detail = {"exit": 0, "result": "read", "calibration": parsed}
        with patch("studio.helper.calib_read", return_value=(True, "read", detail)) as helper, \
             patch.object(probe, "build_stages", self.short_stages):
            started = self.c.start(self.options(), "owner")
            while self.c.active:
                self.c.snapshot("owner")
                time.sleep(0.05)
        helper.assert_called_once()
        snapshot = self.c.snapshot("owner")
        self.assertTrue(snapshot["result"]["completed"], snapshot)
        capture = self.c.export_path(started["run_id"], "capture").read_text()
        self.assertIn('"calibration_source": "helper"', capture)

    def test_unreadable_calibration_without_helper_refuses_before_start(self):
        self.c.calibration = Path(self.tmp.name) / "missing.toml"
        with patch("studio.helper.calib_read", return_value=(False, "no_calibration", None)):
            with self.assertRaises(Refused) as caught:
                self.c.start(self.options(), "owner")
        self.assertEqual(str(caught.exception), "无法读取主程序的舵机标定，请先完成标定并检查配置")
        self.assertFalse(self.c.active)

    def test_update_guard_allows_read_and_stop_but_blocks_test_start(self):
        from studio.updates import safe_during_update
        for op in ("status", "load", "stop"):
            self.assertTrue(safe_during_update("tracking.op", {"op": op}))
        self.assertFalse(safe_during_update("tracking.op", {"op": "start"}))


if __name__ == "__main__":
    unittest.main()
