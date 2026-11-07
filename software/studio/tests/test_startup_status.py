"""Startup device observations are diagnostic evidence, independent of IMU readiness."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps"), str(HERE.parent / "dev")]

from studio import onboard, servo  # noqa: E402
from studio.commit import CommitService  # noqa: E402
from studio.robotd import API_VERSION, KNOWN_BUILDS, Robotd  # noqa: E402
from studio.state import JOINT_NAMES, summarize  # noqa: E402
from studio.tune import Tune  # noqa: E402


class QuietLog:
    def info(self, *args, **kwargs):
        pass

    warn = error = info


def observation(sid, name, **changes):
    row = {"id": sid, "name": name, "responding": True, "check_age_ms": 10,
           "reply_age_ms": 10, "feedback_age_ms": 10, "position_raw": 2050,
           "speed_raw": -4, "load_raw": -5, "current_raw": 6, "volts": 7.8,
           "temperature_c": 33}
    return dict(row, **changes)


def startup_bus(**changes):
    bus = {"mode": "motion", "phase": "waiting_for_bus",
           "error": "Feetech host IMU has no sample", "native": {
               "imu_source": "lsm6dsv16x", "imu_ready": False,
               "servo_observations": {"attempted": True, "age_ms": 0, "rows": [
                   observation(sid, name) for sid, name in zip(servo.IDS, JOINT_NAMES)]}}}
    bus.update(changes)
    return bus


class StartupObservationsTest(unittest.TestCase):
    def setUp(self):
        self.rd = Robotd("none", "none", QuietLog())
        self.rd.present = True
        self.rd.bus.set(startup_bus())
        self.rd.bus.at = 100.

    def summary(self, now=100.2):
        return summarize(self.rd, {}, now=now)

    def table(self, now=100.2):
        from unittest import mock
        with mock.patch("studio.servo.time.monotonic", return_value=now):
            return servo.view_from_robotd(self.rd, None)

    def rows(self):
        return self.rd.bus.value["native"]["servo_observations"]["rows"]

    def test_all_servo_replies_remain_visible_while_host_imu_blocks_startup(self):
        result = self.summary()
        self.assertEqual(result["servos"]["online"], 15)
        self.assertEqual(result["posture"], "starting")
        self.assertFalse(result["guard"])
        self.assertEqual(result["estop"]["state"], "unavailable")
        self.assertEqual(result["start_wait"]["text"], "等待 IMU 数据（来源：lsm6dsv16x）")
        self.assertIn("host IMU", result["start_error"])
        json.dumps(result, allow_nan=False)

    def test_torque_only_reply_proves_presence_without_fabricating_feedback(self):
        for row in self.rows():
            row.update(feedback_age_ms=None, position_raw=None, speed_raw=None,
                       load_raw=None, current_raw=None, volts=None, temperature_c=None)
        view = self.table()
        self.assertEqual(view["online"], 15)
        self.assertTrue(view["diagnostic"])
        self.assertEqual(view["source"], "robotd")
        self.assertIsNone(view["avg_volts"])
        self.assertIsNone(view["holding"])
        self.assertFalse(view["all_off"])
        for row in view["rows"]:
            self.assertIs(row["responding"], True)
            self.assertTrue(row["fresh"])
            self.assertFalse(row["feedback_fresh"])
            for key in ("raw", "deg", "volt", "temp", "load", "current_ma", "current_raw", "torque"):
                self.assertIsNone(row[key], key)

    def test_partial_replies_distinguish_latest_missing_from_unchecked(self):
        self.rows()[0].update(responding=False, reply_age_ms=None, feedback_age_ms=None)
        self.rows()[1].update(responding=None, check_age_ms=None, reply_age_ms=None, feedback_age_ms=None)
        self.rows()[2].update(responding=False, reply_age_ms=9000, feedback_age_ms=9000)
        self.assertEqual(self.summary()["servos"]["online"], 12)
        view = self.table()
        self.assertEqual(view["online"], 12)
        self.assertEqual([r["responding"] for r in view["rows"][:4]], [False, None, False, True])
        self.assertTrue(all(r["raw"] is None for r in view["rows"][:3]))

    def test_diagnostic_raw_feedback_is_never_presented_as_calibrated_degrees(self):
        view = self.table()
        self.assertEqual(view["online"], 15)
        row = view["rows"][0]
        self.assertEqual(row["raw"], 2050)
        self.assertEqual(row["load"], -5)
        self.assertEqual(row["speed_raw"], -4)
        self.assertEqual(row["current_raw"], 6)
        self.assertEqual(row["volt"], 7.8)
        self.assertEqual(row["temp"], 33)
        self.assertIsNone(row["deg"])
        self.assertIsNone(row["current_ma"])
        self.assertIsNone(row["torque"])
        self.assertTrue(row["feedback_fresh"])

    def test_snapshot_row_and_studio_receipt_ages_are_added(self):
        obs = self.rd.bus.value["native"]["servo_observations"]
        obs["age_ms"] = 1200
        for row in self.rows():
            row.update(check_age_ms=1600, reply_age_ms=1600, feedback_age_ms=1600)
        self.assertEqual(self.summary(now=100.199)["servos"]["online"], 15)
        self.assertIsNone(self.summary(now=100.201)["servos"]["online"])
        self.assertIsNone(self.table(now=100.201)["online"])
        self.assertTrue(all(r["responding"] is None and r["raw"] is None
                            for r in self.table(now=100.201)["rows"]))

    def test_cached_native_snapshot_expires_despite_a_new_ipc_receipt(self):
        self.rd.bus.value["native"]["servo_observations"]["age_ms"] = 3000
        self.rd.bus.at = 100.2
        self.assertIsNone(self.summary()["servos"]["online"])
        self.assertIsNone(self.table()["online"])

    def test_feedback_expires_separately_from_new_presence_reply(self):
        for row in self.rows():
            row["feedback_age_ms"] = 2900
        self.assertEqual(self.summary()["servos"]["online"], 15)
        view = self.table()
        self.assertEqual(view["online"], 15)
        self.assertTrue(all(r["responding"] is True and r["raw"] is None
                            and r["volt"] is None and not r["feedback_fresh"] for r in view["rows"]))

    def test_reply_age_cannot_be_refreshed_by_a_new_request(self):
        for row in self.rows():
            row["reply_age_ms"] = 2900
        self.assertIsNone(self.summary()["servos"]["online"])
        self.assertIsNone(self.table()["online"])
        self.assertTrue(all(r.get("responding") is None for r in self.table()["rows"]))

    def test_absent_failed_or_stale_ipc_never_refreshes_cached_observations(self):
        for present, age, error in ((False, .1, None), (None, .1, None),
                                    (True, 3., None), (True, .1, "request failed")):
            with self.subTest(present=present, age=age, error=error):
                self.rd.present = present
                self.rd.bus.at, self.rd.bus.error = 100.2 - age, error
                self.assertIsNone(self.summary()["servos"]["online"])
                view = self.table()
                self.assertIsNone(view["online"])
                self.assertTrue(all(r["responding"] is None and r["raw"] is None for r in view["rows"]))

    def test_no_attempt_or_incomplete_contract_is_unknown_instead_of_zero(self):
        original = copy.deepcopy(self.rd.bus.value)
        for change in ("absent", "unattempted", "no_check", "empty", "no_age"):
            with self.subTest(change=change):
                self.rd.bus.value = copy.deepcopy(original)
                obs = self.rd.bus.value["native"]["servo_observations"]
                if change == "absent":
                    del self.rd.bus.value["native"]["servo_observations"]
                elif change == "unattempted":
                    obs["attempted"] = False
                elif change == "no_check":
                    for row in obs["rows"]:
                        row.pop("check_age_ms")
                elif change == "empty":
                    obs["rows"] = []
                else:
                    obs.pop("age_ms")
                self.assertIsNone(self.summary()["servos"]["online"])
                self.assertIsNone(self.table()["online"])

    def test_fresh_misses_can_report_zero_online(self):
        for row in self.rows():
            row.update(responding=False, reply_age_ms=None, feedback_age_ms=None)
        self.assertEqual(self.summary()["servos"]["online"], 0)
        self.assertEqual(self.table()["online"], 0)
        self.assertTrue(all(r["responding"] is False for r in self.table()["rows"]))

    def test_ordinary_motion_feedback_retains_priority(self):
        bus = self.rd.bus.value
        bus["phase"] = "control"
        bus["native"]["joints"] = [{"id": sid, "name": name, "position_raw": 2080,
            "volts": 7.6, "feedback_fresh": True} for sid, name in zip(servo.IDS, JOINT_NAMES)]
        bus["native"]["torque_state_confirmed"] = False
        bus["native"]["servo_observations"]["rows"] = []
        self.assertEqual(self.summary()["servos"]["online"], 15)
        self.assertEqual(self.summary()["posture"], "limp")
        view = servo.view_from_robotd(bus, None)
        self.assertEqual(view["online"], 15)
        self.assertEqual(view["rows"][0]["raw"], 2080)
        self.assertIsNotNone(view["rows"][0]["deg"])

    def test_api_version_matches_native_observation_contract(self):
        self.assertEqual(API_VERSION, 44)
        for build in ("0791fcd-feetech-qinmo-control.45", "0791fcd-feetech-qinmo-control.46",
                      "0791fcd-feetech-qinmo-control.47", "replica-imu-home-control.49"):
            self.assertIn(build, KNOWN_BUILDS)


class StartupCopyTest(unittest.TestCase):
    def setUp(self):
        self.rd = Robotd("none", "none", QuietLog())
        self.rd.present = True
        self.rd.bus.set(startup_bus())

    def texts(self):
        bus = self.rd.bus.value
        state = summarize(self.rd, {})
        header = onboard.header_of({"bus": bus, "present": True})
        with tempfile.TemporaryDirectory() as folder:
            mode = next(item for item in CommitService(self.rd, QuietLog(), folder).readiness()["items"]
                        if item["key"] == "mode")
            phase = Tune(self.rd, QuietLog(), folder).view()["run"]["phase"]
        todo = next(item for item in state["todo"] if item["key"] == "starting")
        return state, header, mode, phase, todo

    def test_host_and_bus_imu_errors_consistently_name_imu_and_configured_source(self):
        for mode in ("motion", "commissioning"):
            for source, error in (("lsm6dsv16x", "Feetech host IMU has no sample"),
                                  ("bus", "Feetech bus IMU id=200 missing"),
                                  ("bus", "IMU200: Feetech ID 200 response timeout")):
                with self.subTest(mode=mode, source=source):
                    bus = startup_bus(mode=mode, error=error)
                    bus["native"]["imu_source"] = source
                    self.rd.bus.set(bus)
                    state, header, item, phase, todo = self.texts()
                    for text in (header["text"], item["text"], phase["word"], todo["msg"],
                                 (state.get("start_wait") or {}).get("text", "")):
                        self.assertIn("等待 IMU 数据", text)
                        self.assertIn(source, text)
                        self.assertNotIn("舵机没上电", text)
                    self.assertEqual(header["raw"], error)
                    self.assertEqual(phase["error"], error)
                    self.assertIn(error, item["text"])
                    self.assertFalse(header["ok"])
                    self.assertFalse(item["ok"])

    def test_generic_bus_wait_does_not_guess_servo_power(self):
        self.rd.bus.set(startup_bus(error="Feetech Sync Read timeout; missing IDs [21]"))
        state, header, item, phase, todo = self.texts()
        for text in (header["text"], item["text"], phase["word"], todo["msg"],
                     (state.get("start_wait") or {}).get("text", "")):
            self.assertIn("等待总线设备响应", text)
            self.assertNotIn("多半", text)
        self.assertIn("missing IDs [21]", header["raw"])

    def test_calibration_refusal_keeps_its_existing_explanation(self):
        self.rd.bus.set(startup_bus(error="motion requires all 15 calibrated joints and verified IMU mounting"))
        header = onboard.header_of({"bus": self.rd.bus.value, "present": True})
        self.assertEqual(header["kind"], "stuck_calib")
        self.assertIn("标定或 IMU 方向没过", header["text"])
