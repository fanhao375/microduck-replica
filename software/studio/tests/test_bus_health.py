"""Communication evidence is counted once, bounded, and independent for every ID."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio import servo
from studio.robotd import Sample
from studio.robotd import Robotd


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


def native_motion_bus_status(board="ft6", read_index=100, sequence=42):
    """Actual raw_stream wire fixture plus BusImuRawSample for each native board.

    feetech.rs decode/decode_qinmo/decode_dxl2 omit sensor age; accepted
    read_index is an attempt identifier, never an accepted-sample count.
    """
    wire = json.loads((HERE/"fixtures"/"robotd-stopped-wire.json").read_text(encoding="utf-8"))
    bus = wire["bus"]
    native = bus["native"]
    source_sequence = None if board == "sflp_124_12" else sequence
    bound = 30 if board == "ft6" else None
    protocol, profile = ("dxl2", "legacy") if board == "sflp_124_12" else ("feetech", "qinmo" if board == "qinmo" else "legacy")
    native.update(imu_source="bus", imu_ready=True, imu_sequence=source_sequence,
        sample_age_ms=2, imu_age_ms=None, imu_age_upper_bound_ms=bound,
        imu_status=0 if source_sequence is not None else None,
        configured_imu_profile=profile, imu_bus={"protocol": protocol, "board": board,
            "profile": profile, "identity": [1, 0, 0, 0], "gyro_dps_per_lsb": .061,
            "identification": "probe", "quaternion": "legacy"},
        imu_raw={"block": [1, 0, 255, 255, 0, 0, 0, 48, 0, 0, 0, 0],
            "sequence": source_sequence, "status": 0 if source_sequence is not None else None,
            "age_ms": None, "age_upper_bound_ms": bound, "host_read_start_us": read_index*20_000,
            "transaction_us": 4700, "read_index": read_index})
    return bus, wire["states"][0]


class NativeMotionImuContractTest(unittest.TestCase):
    def backend(self, rd):
        from studio.imu import ImuBackend
        return ImuBackend(rd, SimpleNamespace(info=lambda *a, **kw: None, warn=lambda *a, **kw: None))

    def test_ft6_qinmo_and_counterless_dxl2_use_accepted_host_read_age_and_source_basis(self):
        for board in ("ft6", "qinmo", "sflp_124_12"):
            with self.subTest(board=board), mock.patch("time.monotonic", return_value=10.) as clock:
                frame, state = native_motion_bus_status(board)
                rd = fake_rd(frame)
                rd.state.set(state)
                backend = self.backend(rd)
                first = backend.view()
                self.assertTrue(first["health"]["fresh"])
                self.assertEqual(first["health"]["age_ms"], 2.)
                self.assertEqual(first["health"]["age_basis"], "host_accepted_sample")
                self.assertEqual(first["gyro"], state["imu"]["gyro"])
                self.assertEqual(first["health"]["sensor_age_upper_bound_ms"], 32. if board == "ft6" else None)
                clock.return_value = 11.
                frame, state = native_motion_bus_status(board, 150, 92)
                rd.bus.set(frame)
                rd.state.set(state)
                second = backend.view()
                if board == "sflp_124_12":
                    self.assertEqual(second["health"]["rate_basis"], "accepted_host_read")
                    self.assertEqual(second["health"]["update_hz"], 1., "attempt-number delta is not accepted data count")
                else:
                    self.assertEqual(second["health"]["rate_basis"], "accepted_sensor_counter")
                    self.assertEqual(second["health"]["update_hz"], 50.)
                clock.return_value = 12.
                frame["cycle"] += 50
                if board != "sflp_124_12":
                    frame["native"]["imu_sequence"] = 93  # rejected packet advances native tracker only
                rd.bus.set(copy.deepcopy(frame))
                stale = backend.view()
                self.assertGreaterEqual(stale["health"]["age_ms"], 1002.)
                self.assertFalse(stale["health"]["fresh"])
                self.assertIsNone(stale["health"]["update_hz"])

    def test_accepted_host_age_is_required_even_when_state_stream_and_raw_counter_are_fresh(self):
        frame, state = native_motion_bus_status()
        frame["native"]["sample_age_ms"] = None
        rd = fake_rd(frame)
        rd.state.set(state)
        view = self.backend(rd).view()
        self.assertIsNone(view["health"]["age_ms"])
        self.assertFalse(view["health"]["fresh"])
        self.assertIsNone(view["health"]["update_hz"])

    def test_current_accepted_motion_raw_is_not_invalidated_by_old_startup_diagnostic(self):
        frame, state = native_motion_bus_status()
        frame["native"]["imu_observation"] = {"source": "bus", "sensor_ready": False,
            "sample_valid": False, "sample_fresh": False, "error": "startup still converging"}
        rd = fake_rd(frame)
        rd.state.set(state)
        self.assertTrue(self.backend(rd).view()["health"]["fresh"])

    def test_legacy_report_without_accepted_raw_provenance_keeps_accepted_rate_unavailable(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            frame, state = native_motion_bus_status()
            frame["native"].pop("imu_raw")
            rd = fake_rd(frame)
            rd.state.set(state)
            backend = self.backend(rd)
            self.assertTrue(backend.view()["health"]["fresh"])
            clock.return_value = 11.
            frame["native"]["imu_sequence"] += 50
            rd.bus.set(copy.deepcopy(frame))
            self.assertIsNone(backend.view()["health"]["update_hz"])

    @mock.patch("time.monotonic", return_value=10.)
    def test_explicit_reported_sensor_age_includes_time_since_native_control_accepted_it(self, _clock):
        frame, state = native_motion_bus_status()
        frame["native"].update(imu_age_ms=7, imu_age_upper_bound_ms=9, sample_age_ms=600)
        rd = fake_rd(frame)
        rd.state.set(state)
        view = self.backend(rd).view()
        self.assertEqual(view["health"]["age_ms"], 607.)
        self.assertFalse(view["health"]["fresh"])


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
    def test_open_debug_owner_uses_existing_per_id_counters(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            session = servo.ServoSession(mock.Mock(), sim=True)
            session.state = "open"
            session.bus = SimpleNamespace(per_id={sid: counters() for sid in servo.IDS}, stats={})
            self.assertNotIn("communication", session.snapshot())
            first = session.snapshot(diagnostics=True)
            self.assertIsNone(first["rows"][0].get("health", {}).get("rate"))
            clock.return_value = 11.
            session.bus.per_id = {sid: counters(120, 4 if sid == 13 else 0) for sid in servo.IDS}
            by_id = {row["id"]: row for row in session.snapshot(diagnostics=True)["rows"]}
            self.assertIn("health", by_id[13])
            self.assertEqual(by_id[13]["health"]["rate"], .2)
            self.assertEqual(by_id[20]["health"]["rate"], 0.)

    def test_previous_connection_cache_is_not_fresh_after_hello(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            rd = fake_rd(bus_frame())
            servo.view_from_robotd(rd, None)
            clock.return_value = 10.1
            rd.hello.set({"api_version": 43})
            self.assertFalse(any(row["fresh"] for row in servo.view_from_robotd(rd, None)["rows"]))

    def test_control_cycles_alone_do_not_count_as_effective_feedback_reads(self):
        with mock.patch("time.monotonic", return_value=10.) as clock:
            frame = bus_frame()
            frame["cycle"] = 10
            rd = fake_rd(frame)
            servo.view_from_robotd(rd, None)
            clock.return_value = 11.
            frame["cycle"] = 60
            rd.bus.set(frame)
            self.assertIsNone(servo.view_from_robotd(rd, None)["communication"]["sample_hz"])
            clock.return_value = 12.
            frame["native"]["telemetry"].update(total_reads=200, failed_reads=50)
            rd.bus.set(frame)
            servo.view_from_robotd(rd, None)
            clock.return_value = 13.
            frame["cycle"] = 160
            frame["native"]["telemetry"].update(total_reads=250, failed_reads=55)
            rd.bus.set(frame)
            self.assertEqual(servo.view_from_robotd(rd, None)["communication"]["sample_hz"], 45.)

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
        rd._due["bus"] = 1.
        rd.set_telemetry_viewers(1)
        self.assertEqual(rd.bus_poll_interval(), .1)
        self.assertEqual(rd._due["bus"], 0.)
        self.assertFalse(rd._wake_poll.is_set(), "viewer demand must not interrupt reconnect backoff")
        rd.set_telemetry_viewers(0)
        self.assertEqual(rd.bus_poll_interval(), 1.)
        rd.bus.set({"mode": "commissioning"})
        self.assertEqual(rd.bus_poll_interval(), .1)


if __name__ == "__main__":
    unittest.main()
