"""Compatibility must preserve native safety evidence and reject stale telemetry."""
import tempfile
import time
import unittest
from unittest.mock import patch

from studio.log import Log
from studio.robotd import Robotd
from studio.servo import readonly_view, view_from_state
from studio.state import autonomy_of, control_features, project_bus


class CompatibilitySafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rd = Robotd("none", "none", Log(self.tmp.name))

    async def test_legacy_model_api_keeps_native_bus_status(self):
        async def call(addr, method, params, **kwargs):
            if method == "robot.busStatus":
                return {"mode": "commissioning", "phase": "ready"}
            if method == "robot.modelApi":
                return {"model_api": 2}
            return {"mode": "walk"}
        with patch("studio.robotd.call_once", side_effect=call):
            await self.rd._probe_daemon()
        self.assertIs(self.rd.legacy_bus, True)

    def test_fall_limp_is_not_torque_confirmation(self):
        self.rd.legacy_bus = False
        for limp in (True, False):
            state = {"loop": {"hz": 50}, "policy": "held", "safety": {"limp": limp, "fallen": False}}
            self.assertIsNone(project_bus(None, state)["native"]["torque_state_confirmed"])
            self.assertIsNone(self.rd._estop_bus(state))

    def test_servo_telemetry_must_be_live_current_connection(self):
        self.rd.legacy_bus = False
        self.rd.present = True
        self.rd.state.set({"loop": {"hz": 50}, "joints": [0.0] * 15,
                           "safety": {"limp": False, "torqued": True}})
        self.assertEqual(readonly_view(self.rd, {})["online"], 15)
        self.rd.state.at = time.monotonic() - 60
        self.assertEqual(readonly_view(self.rd, {})["online"], 0)
        self.rd.state.at = time.monotonic()
        self.rd.present = False
        self.assertEqual(readonly_view(self.rd, {})["online"], 0)
        self.rd.present = True
        self.rd.hello.set({"api_version": 68})
        self.assertEqual(readonly_view(self.rd, {})["online"], 0)

    def test_zero_frequency_is_not_live_servo_feedback(self):
        state = {"loop": {"hz": 0}, "joints": [0.0] * 15,
                 "safety": {"limp": False, "torqued": True}}
        self.assertEqual(view_from_state(state, {})["online"], 0)

    def test_absent_autonomous_field_in_legacy_is_unknown(self):
        self.assertIsNone(autonomy_of(True, {"move": {"source": "padd"}})[0])

    def test_protocol_capabilities_distinguish_old_and_new(self):
        self.rd.present = True
        for version, autonomous, laser in ((43, False, False), (52, False, True), (68, True, True)):
            self.rd.hello.set({"api_version": version})
            self.assertEqual(control_features(self.rd), {"autonomous": autonomous, "laser_track": laser})
        self.assertIs(autonomy_of(True, {}, supported=True)[0], False)
        self.rd.present = False
        self.assertFalse(any(control_features(self.rd).values()))

    def test_buffered_pre_command_state_cannot_confirm_relax(self):
        self.rd.legacy_bus = False
        now = time.monotonic()
        self.rd.estop.press("test", now)
        self.rd.estop.sent(now)
        state = {"t_ns": 1, "safety": {"torqued": False, "limp": False}}
        self.assertFalse(self.rd._estop_on_state(state))
        self.assertTrue(self.rd.estop.active)
