"""Tests for gamepad mode-toggle bindings (laser_track / autonomous) and updated LB/RB defaults."""
import unittest

from studio.log import Log
from studio.pad import PadOps, Refused
from studio.pad_drive import DEFAULT_BIND


class FakeRobot:
    def __init__(self):
        self.calls = []
        self.bindings = [
            {"button": "a", "skill": "sit_toggle", "overridden": False},
            {"button": "b", "skill": "ground_pick", "overridden": False},
            {"button": "x", "skill": "", "overridden": False},
            {"button": "y", "skill": "excited", "overridden": False},
            {"button": "lb", "skill": "yes", "overridden": False},
            {"button": "rb", "skill": "no", "overridden": False},
        ]

    def call(self, method, params=None, timeout=3.0):
        params = params or {}
        self.calls.append((method, params))
        if method == "pad.bindings":
            return {"bindings": list(self.bindings)}
        if method == "pad.bind":
            btn = params["button"]
            skill = params.get("skill")
            for row in self.bindings:
                if row["button"] == btn:
                    if skill is None:
                        row["skill"] = {"lb": "yes", "rb": "no"}.get(btn, "")
                        row["overridden"] = False
                    else:
                        row["skill"] = skill
                        row["overridden"] = True
            return {"accepted": True}
        if method in ("robot.laserTrack", "robot.autonomous", "robot.do"):
            return {"accepted": True}
        return {"accepted": True}

    def notify(self, method, params):
        self.calls.append((method, params))


def _robot_view(now, *, laser=None, autonomous=False):
    return {
        "present": True,
        "state_at": now,
        "control_features": {"laser_track": True, "autonomous": True},
        "paused": False,
        "posture": "policy",
        "bus_at": now,
        "bus": {
            "mode": "motion",
            "phase": "control",
            "policy_enabled": True,
            "homed": True,
            "native": {"torque_state_confirmed": True},
        },
        "policies": {
            "mode": "walk",
            "sitting": False,
            "homed": True,
            "slots": [
                {"slot": "ground_pick", "path": "/opt/p/gp.onnx"},
                {"slot": "sitstand", "path": "/opt/p/ss.onnx"},
            ],
            "skills": ["roulade", "kick_left", "kick_right"],
        },
        "state": {
            "laser": laser,
            "autonomous": autonomous,
        },
        "estop_active": False,
        "estop_presses": 0,
    }


class TestPadModeBindings(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.now = 100.0
        self.robot = FakeRobot()
        self.ops = PadOps(Log(self._tmp.name), robot=self.robot, clock=lambda: self.now)
        self.ops.feed_robot(_robot_view(self.now))

    def tearDown(self):
        self.ops.close()
        self.ops.log.flush()
        self._tmp.cleanup()

    def test_default_bind_preserves_existing_controls(self):
        self.assertEqual(
            DEFAULT_BIND,
            {"a": "ground_pick", "x": "roulade", "lb": "kick_left", "rb": "kick_right", "dpad_down": "sit_toggle"},
        )

    def test_skill_binding_and_direct_web_switches(self):
        self.ops._fetch_bindings()
        self.ops.handle({"op": "bind", "button": "lb", "skill": "yes"}, by="test", conn="c1")
        self.assertIn(("pad.bind", {"button": "lb", "skill": "yes"}), self.robot.calls)
        self.ops.handle({"op": "laser_track"}, by="test", conn="c1")
        self.assertIn(("robot.laserTrack", {"active": True}), self.robot.calls)
        self.ops.feed_robot(_robot_view(self.now, laser={"phase": "tracking"}))
        self.ops.handle({"op": "laser_track"}, by="test", conn="c1")
        self.assertIn(("robot.laserTrack", {"active": False}), self.robot.calls)
        self.ops.handle({"op": "autonomous"}, by="test", conn="c1")
        self.assertIn(("robot.autonomous", {"active": True}), self.robot.calls)
        self.ops.feed_robot(_robot_view(self.now, autonomous=True))
        self.ops.handle({"op": "autonomous"}, by="test", conn="c1")
        self.assertIn(("robot.autonomous", {"active": False}), self.robot.calls)
        self.ops.handle({"op": "bind", "button": "lb"}, by="test", conn="c1")
        self.assertIn(("pad.bind", {"button": "lb"}), self.robot.calls)

    def test_modes_cannot_be_bound_as_native_skills(self):
        self.ops._fetch_bindings()
        for skill in ("laser_track", "autonomous"):
            with self.assertRaisesRegex(Refused, "实体手柄"):
                self.ops.handle({"op": "bind", "button": "lb", "skill": skill}, conn="c1")
        self.assertFalse(any(method == "pad.bind" for method, _ in self.robot.calls))

    def test_unsupported_and_stale_controls_never_send(self):
        for override in ({"control_features": {}}, {"state_at": self.now - 10}):
            self.ops.feed_robot({**_robot_view(self.now), **override})
            for mode in ("laser_track", "autonomous"):
                with self.assertRaises(Refused):
                    self.ops.handle({"op": mode}, conn="c1")
        self.assertFalse(any(method in ("robot.laserTrack", "robot.autonomous") for method, _ in self.robot.calls))

    def test_recording_session_blocks_mode_switches(self):
        self.ops._tick_stop.set()
        self.ops._tick_evt.set()
        self.ops._tick_thread.join(timeout=2)
        self.ops.motion_studio.session = {"id": "recording"}
        try:
            for mode in ("laser_track", "autonomous"):
                with self.assertRaises(Refused):
                    self.ops.handle({"op": mode}, conn="c1")
        finally:
            self.ops.motion_studio.session = None
        self.assertFalse(any(method in ("robot.laserTrack", "robot.autonomous") for method, _ in self.robot.calls))

    def test_default_lb_yes_works_on_virtual_pad(self):
        self.ops._fetch_bindings()
        self.ops.handle({"op": "key", "key": "lb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.do", {"skill": "yes"}), self.robot.calls)


if __name__ == "__main__":
    unittest.main()
