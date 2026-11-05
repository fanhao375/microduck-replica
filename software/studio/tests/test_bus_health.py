"""Communication evidence is counted once, bounded, and independent for every ID."""
import copy
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio import servo
from studio.robotd import Sample
from studio.robotd import Robotd
from studio.log import Log


def counters(asked=100, missed=0):
    return {"asked": asked, "ok": asked-missed, "missed": missed, "streak": 0, "max_streak": 0}


def bus_frame(asked=100, missed=0, session="one", generation=0):
    return {"mode": "motion", "phase": "control", "policy_enabled": False,
            "native": {"torque_enabled": False, "torque_state_confirmed": False,
                       "hd1910": {"temporary_gains_applied": False, "restore_pending": False},
                       "telemetry": {"session": session, "bad_checksums": 2, "failed_reads": 3},
                       "read_order": {"mode": "default", "last_id": None, "ids": list(servo.IDS),
                                      "generation": generation, "session": session},
                       "imu_source": "bus", "imu_sequence": asked, "imu_ready": True, "imu_age_ms": 5,
                       "joints": [{"id": sid, "position_raw": 2048, "feedback_fresh": True,
                                   "sample_age_ms": 10, "communication": counters(asked, missed if sid == 13 else 0)}
                                  for sid in servo.IDS]}}


def fake_rd(frame):
    rd = SimpleNamespace(bus=Sample(), state=Sample(), hello=Sample(), health=Sample(),
                         calib=Sample(), present=True, calib_valid_after=None)
    rd.bus.set(frame)
    return rd


def native_commissioning_status(cycle=100, sequence=300, source="bus", sample_at_us=2_000_000,
                                updated_at_us=2_002_000):
    """Wire keys of native6af2fad feetech_commissioning::{Status,Sample,ImuStatus}.

    Bus ImuStatus omits age_ms/epoch; no motion-only native.joints or IMU
    reader/age/source fields exist in this commissioning native block.
    """
    imu = {"id": 200, "source": source, "ready": True, "valid": True,
           "gravity": [0., 0., -1.], "gyro_rad_s": [.1, .2, .3],
           "quaternion_wxyz": [1., 0., 0., 0.], "mount_verified": True}
    if sequence is not None:
        imu["sequence"] = sequence
    if source != "bus":
        imu.pop("id")
        imu.update(age_ms=7., epoch=1, tilt_deg=0.)
    return {"native": {"read_order": {"mode": "default", "last_id": None, "ids": list(servo.IDS),
                "servo_ids": list(servo.IDS), "session": "commissioning-one", "generation": 0,
                "configuration_path": "/etc/robot/feetech-ft5/servo-read-order.json"},
                "servo_observations": {"attempted": True, "age_ms": 1, "rows": [
                    {"id": sid, "name": servo.NAMES[sid][1], "communication": counters(cycle),
                     "responding": True, "check_age_ms": 2, "reply_age_ms": 1, "feedback_age_ms": 1,
                     "position_raw": 2048, "speed_raw": 3, "load_raw": 2, "current_raw": 1,
                     "volts": 7.8, "temperature_c": 31} for sid in servo.IDS]},
                "telemetry": {"session": "commissioning-one", "bad_checksums": 0, "timeouts": 0, "failed_reads": 0},
                "torque_state_confirmed": False,
                "hd1910": {"temporary_gains_applied": False, "restore_pending": False}},
            "configured_imu_profile": "legacy", "build": "replica-bus-health-control.49",
            "port": "/dev/ttyS1", "mode": "commissioning", "phase": "ready", "session": 123,
            "target_hz": 50., "achieved_hz": 50., "total_cycles": cycle, "total_success": cycle,
            "total_communication_failures": 0, "errors_by_id": {}, "device_communication": {},
            "last_cycle_error": None, "last_failure": None,
            "last_sample": {"cycle": cycle, "at_us": sample_at_us, "servos": [
                {"id": sid, "joint": servo.NAMES[sid][1], "position_raw": 2048,
                 "position_wire_raw": 2048, "position_signed_raw": 2048, "speed_raw": 3,
                 "load_raw": 2, "current_raw": 1, "voltage_v": 7.8, "temperature_c": 31,
                 "moving": False, "calibrated": True} for sid in servo.IDS], "imu": imu},
            "calibrated": True, "imu_mount_verified": True,
            "imu_bus": {"protocol": "feetech", "board": "ft6", "identity": [1, 0, 0, 0],
                "profile": "legacy", "gyro_dps_per_lsb": .061, "identification": "probe", "quaternion": "legacy"},
            "torque_state": "off", "torque_off_pending": None, "power_pending": False,
            "motion": None, "policy_motion_enabled": False, "updated_at_us": updated_at_us}


class NativeCommissioningContractTest(unittest.TestCase):
    def backend(self, rd):
        from studio.imu import ImuBackend
        return ImuBackend(rd, SimpleNamespace(info=lambda *a, **kw: None, warn=lambda *a, **kw: None))

    def test_bus_imu_gyro_and_freshness_use_actual_accepted_snapshot_timestamps(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(native_commissioning_status())
            backend = self.backend(rd)
            first = backend.view()
            self.assertEqual(first["gyro"], [.1, .2, .3])
            self.assertEqual(first["health"]["source"], "bus")
            self.assertEqual(first["health"]["age_ms"], 2.)
            self.assertTrue(first["health"]["fresh"])
            clock.return_value = 11.
            rd.bus.set(native_commissioning_status(150, 350, sample_at_us=3_000_000, updated_at_us=3_002_000))
            self.assertEqual(backend.view()["health"]["update_hz"], 50.)
            clock.return_value = 12.
            rd.bus.set(copy.deepcopy(rd.bus.value))
            stale = backend.view()
            self.assertGreaterEqual(stale["health"]["age_ms"], 1002.)
            self.assertFalse(stale["health"]["fresh"])
            self.assertIsNone(stale["health"]["update_hz"])

    def test_commissioning_servo_rows_and_rate_use_real_status_names(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(native_commissioning_status())
            view = servo.view_from_robotd(rd, None)
            self.assertEqual(view["online"], 15)
            self.assertEqual(view["rows"][0]["volt"], 7.8)
            self.assertEqual(view["rows"][0]["raw"], 2048)
            self.assertEqual(view["rows"][0]["comm"]["asked"], 100)
            clock.return_value = 11.
            rd.bus.set(native_commissioning_status(150, 350, sample_at_us=3_000_000, updated_at_us=3_002_000))
            view = servo.view_from_robotd(rd, None)
            self.assertEqual(view["communication"]["sample_hz"], 50.)
            self.assertEqual(view["rows"][0]["health"]["asked"], 50)

    def test_module_age_is_added_to_accepted_snapshot_age_and_epoch_resets_rate(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(native_commissioning_status(source="lsm6dsv16x"))
            backend = self.backend(rd)
            first = backend.view()
            self.assertEqual(first["health"]["age_ms"], 9.)
            self.assertEqual(first["metadata"]["chip"], "lsm6dsv16x")
            self.assertIsNone(first["metadata"]["board"])
            self.assertIsNone(first["metadata"]["protocol"])
            clock.return_value = 11.
            frame = native_commissioning_status(150, 350, source="lsm6dsv16x", sample_at_us=3_000_000, updated_at_us=3_002_000)
            rd.bus.set(frame)
            self.assertEqual(backend.view()["health"]["update_hz"], 50.)
            clock.return_value = 12.
            frame["last_sample"]["imu"].update(epoch=2, sequence=1)
            rd.bus.set(frame)
            self.assertIsNone(backend.view()["health"]["update_hz"])

    def test_missing_native_timing_keeps_unknown_age_and_missing_sequence_uses_observed_timestamp_basis(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(native_commissioning_status(sequence=None))
            backend = self.backend(rd)
            self.assertTrue(backend.view()["health"]["fresh"])
            clock.return_value = 10.1
            rd.bus.set(native_commissioning_status(105, None, sample_at_us=2_100_000, updated_at_us=2_102_000))
            view = backend.view()
            self.assertEqual(view["health"]["rate_basis"], "accepted_snapshot_timestamp")
            self.assertEqual(view["health"]["update_hz"], 10.)
            frame = native_commissioning_status()
            frame.pop("updated_at_us")
            frame["last_sample"].pop("at_us")
            self.assertIsNone(self.backend(fake_rd(frame)).view()["health"]["age_ms"])

    def test_disconnected_commissioning_keeps_last_known_source_and_does_not_refresh_frozen_sequence(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(native_commissioning_status())
            backend = self.backend(rd)
            backend.view()
            clock.return_value = 11.
            rd.bus.set(native_commissioning_status(150, 300, sample_at_us=3_000_000, updated_at_us=3_002_000))
            self.assertFalse(backend.view()["health"]["fresh"])
            clock.return_value = 15.
            rd.present = False
            stale = backend.view()
            self.assertEqual(stale["health"]["source"], "bus")
            self.assertFalse(stale["health"]["fresh"])
            self.assertGreaterEqual(stale["health"]["age_ms"], 4002.)


class RobotdHealthTest(unittest.TestCase):
    def test_native_speed_raw_and_physical_velocity_are_preserved(self):
        frame = bus_frame()
        frame["native"]["joints"][0].update(speed_raw=7, velocity_rad_s=.75)
        row = servo.view_from_robotd(fake_rd(frame), None)["rows"][0]
        self.assertEqual(row["speed_raw"], 7)
        self.assertEqual(row["velocity_rad_s"], .75)

    def test_counter_deltas_15_independent_ids_and_cached_repaint(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            first = servo.view_from_robotd(rd, None)
            self.assertIsNone(first["rows"][0]["health"]["rate"])
            clock.return_value = 11.
            rd.bus.set(bus_frame(110, 2))
            snap = servo.view_from_robotd(rd, None)
            by_id = {r["id"]: r for r in snap["rows"]}
            self.assertEqual(by_id[13]["health"]["asked"], 10)
            self.assertEqual(by_id[13]["health"]["missed"], 2)
            self.assertEqual(by_id[13]["health"]["rate"], .2)
            self.assertTrue(all(by_id[i]["health"]["rate"] == 0 for i in servo.IDS if i != 13))
            self.assertEqual(by_id[13]["comm"]["asked"], 110)
            clock.return_value = 11.4
            same = servo.view_from_robotd(rd, None)
            self.assertEqual(same["rows"][0]["health"]["asked"], 10)
            self.assertGreaterEqual(same["rows"][0]["age_ms"], 409)
            self.assertEqual(same["communication"]["counters"]["bad_checksum"], 2)
            self.assertNotIn("bad_checksum", same["rows"][0]["comm"])
            clock.return_value = 72.
            expired = servo.view_from_robotd(rd, None)
            self.assertIsNone(expired["rows"][0]["health"]["rate"])
            self.assertFalse(expired["rows"][0]["fresh"])
            self.assertIsNone(expired["communication"]["sample_hz"])

    def test_old_build_has_no_denominator_and_disconnect_cannot_be_fresh(self):
        frame = bus_frame()
        for joint in frame["native"]["joints"]:
            joint.pop("communication")
            joint["missing_total"] = 500
        rd = fake_rd(frame)
        view = servo.view_from_robotd(rd, None)
        self.assertIsNone(view["rows"][0]["health"]["rate"])
        self.assertIsNone(view["rows"][0]["health"]["asked"])
        rd.present = False
        self.assertFalse(servo.view_from_robotd(rd, None)["rows"][0]["fresh"])

    def test_source_session_order_and_counter_rollback_reset_window(self):
        for reset in ("session", "generation", "rollback"):
            with self.subTest(reset=reset), mock.patch("time.monotonic", return_value=10.) as clock:
                rd = fake_rd(bus_frame())
                servo.view_from_robotd(rd, None)
                clock.return_value = 11.
                rd.bus.set(bus_frame(110))
                self.assertEqual(servo.view_from_robotd(rd, None)["rows"][0]["health"]["asked"], 10)
                clock.return_value = 12.
                rd.bus.set(bus_frame(1 if reset == "rollback" else 120,
                                    session="two" if reset == "session" else "one",
                                    generation=1 if reset == "generation" else 0))
                self.assertIsNone(servo.view_from_robotd(rd, None)["rows"][0]["health"]["rate"])

    def test_same_native_snapshot_new_ipc_receipt_cannot_refresh_data(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            servo.view_from_robotd(rd, None)
            clock.return_value = 11.
            rd.bus.set(copy.deepcopy(rd.bus.value))
            snap = servo.view_from_robotd(rd, None)
            self.assertGreaterEqual(snap["rows"][0]["age_ms"], 1000)
            self.assertFalse(snap["rows"][0]["fresh"])

    def test_runtime_and_eeprom_cache_preserves_before_arm_scope_and_unknown_age(self):
        frame = bus_frame()
        frame["native"]["hd1910"]["registers_before_arm"] = [{"id": 13, "mode": 4,
            "eeprom_pdi": [32, 40, 0], "temporary_pdi": [5, 0, 0], "acceleration_raw": 0}]
        row = next(r for r in servo.view_from_robotd(fake_rd(frame), None)["rows"] if r["id"] == 13)
        self.assertIn("parameters", row, "native immutable inspection cache is projected read-only")
        self.assertEqual(row["parameters"]["source"], "robotd_before_arm_cache")
        self.assertIsNone(row["parameters"]["age_ms"])
        self.assertEqual(row["parameters"]["runtime"]["pdi"], [5, 0, 0])
        self.assertEqual(row["parameters"]["eeprom"]["pdi"], [32, 40, 0])

    def test_partial_startup_observations_have_independent_communication_windows(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            frame = bus_frame()
            frame["phase"] = "waiting_for_bus"
            frame["native"]["servo_observations"] = {"attempted": True, "age_ms": 5,
                "rows": [{"id": i, "responding": i != 13, "check_age_ms": 2,
                          "reply_age_ms": 2 if i != 13 else None, "feedback_age_ms": None,
                          "communication": counters(100, 10 if i == 13 else 0)} for i in servo.IDS]}
            rd = fake_rd(frame)
            servo.view_from_robotd(rd, None)
            clock.return_value = 11.
            for row in frame["native"]["servo_observations"]["rows"]:
                row["communication"] = counters(110, 20 if row["id"] == 13 else 0)
            rd.bus.set(frame)
            snap = servo.view_from_robotd(rd, None)
            row = next(r for r in snap["rows"] if r["id"] == 13)
            self.assertEqual(row["health"]["rate"], 1.)
            self.assertEqual(snap["online"], 14)

    def test_rolling_window_storage_is_bounded(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            servo.view_from_robotd(rd, None)
            for n in range(1, 150):
                clock.return_value = 10. + n
                rd.bus.set(bus_frame(100+n))
                servo.view_from_robotd(rd, None)
            self.assertLessEqual(rd._servo_health.stored_events, 4096*15)

    def test_long_unobserved_gap_starts_a_baseline_without_assigning_old_counts_to_sixty_seconds(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            servo.view_from_robotd(rd, None)
            clock.return_value = 80.
            rd.bus.set(bus_frame(800))
            health = servo.view_from_robotd(rd, None)["rows"][0]["health"]
            self.assertIsNone(health["rate"])
            self.assertEqual(health["asked"], 0)
            self.assertEqual(health["observed_s"], 0.)


class ReadOrderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Log(Path(self.tmp.name)/"logs", echo=False, background=False)
        self.session = servo.ServoSession(self.log, sim=True, state_dir=self.tmp.name)

    async def test_validation_has_exactly_the_same_ids_and_preserves_joint_order(self):
        self.assertTrue(hasattr(servo, "validate_read_order"), "read-order validator is implemented")
        original = list(servo.IDS)
        mode, last, ids = servo.validate_read_order({"mode": "id_last", "last_id": 13})
        self.assertEqual(ids, [i for i in original if i != 13] + [13])
        self.assertEqual(sorted(ids), sorted(original))
        self.assertEqual(servo.IDS, original)
        for bad in ({"mode": "wrong"}, {"mode": "id_last", "last_id": True},
                    {"mode": "id_last", "last_id": 99}, {"mode": "default", "last_id": 13},
                    {"mode": "id_last", "last_id": 13, "ids": list(reversed(original))}):
            with self.subTest(bad=bad), self.assertRaises(servo.Refused):
                servo.validate_read_order(bad)

    async def test_debug_owner_guard_no_automatic_torque_or_gain_write(self):
        self.assertTrue(hasattr(self.session, "set_read_order"), "debug owner implements guarded order")
        self.assertTrue(self.session.open()[0])
        self.addCleanup(self.session.close)
        initial = {sid: bytes(sv.r) for sid, sv in self.session.sim_serial.servos.items()}
        result = self.session.set_read_order({"mode": "id_last", "last_id": 13})
        self.assertTrue(result["applied"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["read_order"]["ids"][-1], 13)
        self.assertEqual({sid: bytes(sv.r) for sid, sv in self.session.sim_serial.servos.items()}, initial)
        self.assertEqual(self.session.snapshot()["communication"]["read_order"]["ids"][-1], 13)
        self.session.bus.write_u8(20, 40, 1)
        with self.assertRaisesRegex(servo.Refused, "卸力"):
            self.session.set_read_order({"mode": "default"})
        self.session.bus.write_u8(20, 40, 0)
        self.session._read_torque()
        self.session.bus.write_u8(20, 50, 1)
        with self.assertRaisesRegex(servo.Refused, "增益"):
            self.session.set_read_order({"mode": "default"})

    async def test_debug_owner_save_failure_preserves_actual_order(self):
        self.assertTrue(hasattr(self.session, "set_read_order"), "debug owner implements guarded order")
        self.assertTrue(self.session.open()[0])
        self.addCleanup(self.session.close)
        before = self.session.snapshot()["communication"]["read_order"]
        with mock.patch("studio.servo.save_read_order", side_effect=OSError("readonly")):
            with self.assertRaisesRegex(servo.Refused, "保存"):
                self.session.set_read_order({"mode": "id_ascending"})
        self.assertEqual(self.session.snapshot()["communication"]["read_order"], before)

    async def test_debug_owner_rejects_pending_original_owner_gain_restoration_and_busy_operation(self):
        self.assertTrue(self.session.open()[0])
        self.addCleanup(self.session.close)
        self.session.gains_restored_before_takeover = False
        with self.assertRaisesRegex(servo.Refused, "增益"):
            self.session.set_read_order({"mode": "id_ascending"})
        self.session.gains_restored_before_takeover = True
        token = self.session.op_begin("move")
        try:
            with self.assertRaises(servo.Refused):
                self.session.set_read_order({"mode": "id_ascending"})
        finally:
            self.session.op_end(token)

    async def test_debug_owner_cannot_use_cached_gains_when_fresh_gain_readback_is_missing(self):
        self.assertTrue(self.session.open()[0])
        self.addCleanup(self.session.close)
        self.session.sim_serial.servos[13].muted = {48}
        with self.assertRaisesRegex(servo.Refused, "增益"):
            self.session.set_read_order({"mode": "id_ascending"})

    async def test_native_apply_requires_guard_and_exact_readback_and_updates_next_debug_owner(self):
        from studio import servo_read_order
        path = str(Path(self.tmp.name)/"servo-read-order.json")
        rd = fake_rd(bus_frame())
        rd.bus.value["native"]["read_order"]["configuration_path"] = path
        calls = []

        async def call(method, params=None, timeout=2.):
            calls.append(method)
            if method == "robot.feetechReadOrder":
                order = {"mode": "id_last", "last_id": 13, "ids": [i for i in servo.IDS if i != 13]+[13],
                         "generation": 1, "session": "one", "configuration_path": path}
                Path(path).write_text('{"mode":"id_last","last_id":13}', encoding="utf-8")
                rd.bus.value["native"]["read_order"] = order
                return {"applied": True, "saved": True, "read_order": order}
            return rd.bus.value

        rd.cmd = SimpleNamespace(call=call)
        control = servo_read_order.ReadOrderControl(rd, self.session)
        out = await control.change({"mode": "id_last", "last_id": 13})
        self.assertTrue(out["applied"])
        self.assertEqual(calls, ["robot.feetechReadOrder", "robot.busStatus"])
        self.assertEqual(self.session.read_order["mode"], "id_last")
        rd.bus.value["native"]["torque_state_confirmed"] = True
        with self.assertRaises(servo.Refused):
            await control.change({"mode": "default"})
        self.assertEqual(len(calls), 2)

    async def test_native_failed_or_different_readback_never_reports_saved_success(self):
        from studio import servo_read_order
        rd = fake_rd(bus_frame())
        async def call(method, params=None, timeout=2.):
            return {"applied": True, "saved": True} if method == "robot.feetechReadOrder" else rd.bus.value
        rd.cmd = SimpleNamespace(call=call)
        with self.assertRaisesRegex(servo.Refused, "读回"):
            await servo_read_order.ReadOrderControl(rd, self.session).change({"mode": "id_ascending"})

    async def test_commissioning_requires_explicit_policy_motion_disabled(self):
        frame = bus_frame()
        frame.update(mode="commissioning", phase="ready", policy_motion_enabled=False)
        frame.pop("policy_enabled")
        rd = fake_rd(frame)
        self.assertTrue(servo.robotd_read_order_guard(rd, frame)["allowed"])
        frame["policy_motion_enabled"] = True
        self.assertFalse(servo.robotd_read_order_guard(rd, frame)["allowed"])


class ImuHealthTest(unittest.TestCase):
    def make_backend(self, rd):
        from studio.imu import ImuBackend
        log = SimpleNamespace(info=lambda *a, **kw: None, warn=lambda *a, **kw: None)
        return ImuBackend(rd, log)

    def test_accepted_sequence_rate_repeated_ipc_and_disconnect_age(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            frame = bus_frame()
            frame["native"]["imu_reader"] = {"samples": 100, "errors": 2, "dropped": 3, "epoch": 1}
            rd = fake_rd(frame)
            rd.state.set({"t_ns": 1, "imu": {"gyro": [1, 2, 3], "quat": [1, 0, 0, 0]}})
            backend = self.make_backend(rd)
            first = backend.view()
            self.assertIn("health", first, "IMU health includes accepted sample evidence")
            self.assertIsNone(first["health"]["update_hz"])
            clock.return_value = 11.
            frame = copy.deepcopy(frame)
            frame["native"]["imu_reader"]["samples"] = 150
            frame["native"]["imu_sequence"] = 150
            rd.bus.set(frame)
            rd.state.set({"t_ns": 2, "imu": {"gyro": [3, 2, 1], "quat": [1, 0, 0, 0]}})
            now = backend.view()
            self.assertEqual(now["health"]["update_hz"], 50.)
            self.assertEqual(now["gyro"], [3, 2, 1])
            self.assertIsNone(now["accel_m_s2"])
            self.assertIsNone(now["temperature_c"])
            self.assertEqual(now["health"]["counters"]["errors"], 2)
            clock.return_value = 12.
            rd.bus.set(copy.deepcopy(frame))
            same = backend.view()
            self.assertGreaterEqual(same["health"]["age_ms"], 1005.)
            self.assertFalse(same["health"]["fresh"])
            self.assertIsNone(same["health"]["update_hz"])
            rd.present = False
            self.assertFalse(backend.view()["health"]["ready"])

    def test_source_change_and_reader_counter_rollback_reset_frequency(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            backend = self.make_backend(rd)
            self.assertIn("health", backend.view(), "IMU health includes accepted sample evidence")
            clock.return_value = 11.
            rd.bus.set(bus_frame(150))
            self.assertEqual(backend.view()["health"]["update_hz"], 50.)
            clock.return_value = 12.
            frame = bus_frame(200)
            frame["native"]["imu_source"] = "lsm6dsv16x"
            rd.bus.set(frame)
            self.assertIsNone(backend.view()["health"]["update_hz"])
            clock.return_value = 13.
            frame["native"]["imu_sequence"] = 1
            rd.bus.set(frame)
            self.assertIsNone(backend.view()["health"]["update_hz"])

    def test_no_sensor_sequence_does_not_guess_odr_or_stream_repaint_rate(self):
        rd = fake_rd(bus_frame())
        rd.bus.value["native"].pop("imu_sequence")
        rd.state.set({"t_ns": 1, "imu": {"gyro": [1, 2, 3], "quat": [1, 0, 0, 0]}})
        backend = self.make_backend(rd)
        self.assertIn("health", backend.view(), "IMU health includes accepted sample evidence")
        rd.state.set({"t_ns": 2, "imu": {"gyro": [1, 2, 3], "quat": [1, 0, 0, 0]}})
        self.assertIsNone(backend.view()["health"]["update_hz"])

    def test_bus_diagnostic_cannot_establish_host_sensor_readiness(self):
        frame = bus_frame()
        frame["native"].update(imu_source="lsm6dsv16x", imu_observation={
            "source": "bus", "sensor_ready": True, "sample_valid": True, "sample_fresh": True,
            "sequence": 900, "age_ms": 2})
        frame["native"].pop("imu_ready")
        frame["native"].pop("imu_age_ms")
        frame["native"].pop("imu_sequence")
        view = self.make_backend(fake_rd(frame)).view()
        self.assertFalse(view["health"]["ready"])
        self.assertFalse(view["health"]["fresh"])
        self.assertIsNone(view["health"]["update_hz"])

    def test_read_order_generation_resets_imu_rate_evidence(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            backend = self.make_backend(rd)
            backend.view()
            clock.return_value = 11.
            rd.bus.set(bus_frame(150))
            self.assertEqual(backend.view()["health"]["update_hz"], 50.)
            clock.return_value = 12.
            rd.bus.set(bus_frame(200, generation=1))
            self.assertIsNone(backend.view()["health"]["update_hz"])

    def test_motion_values_come_from_current_state_even_if_last_sample_only_has_gravity(self):
        frame = bus_frame()
        frame["last_sample"] = {"imu": {"source": "bus", "valid": True, "gravity": [0, 0, -1]}}
        rd = fake_rd(frame)
        rd.state.set({"t_ns": 1, "imu": {"gyro": [1, 2, 3], "quat": [1, 0, 0, 0]}})
        view = self.make_backend(rd).view()
        self.assertEqual(view["gyro"], [1, 2, 3])
        self.assertEqual(view["quaternion_wxyz"], [1, 0, 0, 0])


class StatusPollingTest(unittest.TestCase):
    def test_only_status_viewers_request_ten_hz_cached_owner_snapshots(self):
        rd = Robotd("none", "none", SimpleNamespace(info=lambda *a, **kw: None))
        self.assertTrue(hasattr(rd, "set_telemetry_viewers"), "explicit status viewers control cached polling")
        rd.set_viewers(1)
        self.assertEqual(rd.bus_poll_interval(), 1.)
        rd.set_telemetry_viewers(1)
        self.assertEqual(rd.bus_poll_interval(), .1)
        rd.set_telemetry_viewers(0)
        self.assertEqual(rd.bus_poll_interval(), 1.)
        rd.bus.set({"mode": "commissioning"})
        self.assertEqual(rd.bus_poll_interval(), .1)


if __name__ == "__main__":
    unittest.main()
