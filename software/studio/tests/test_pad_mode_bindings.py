"""Tests for gamepad mode-toggle bindings (laser_track / autonomous) and updated LB/RB defaults."""
import unittest

from studio.log import Log
from studio.pad import PadOps
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
        self._tmp.cleanup()

    def test_default_bind_preserves_old_fork_with_lb_rb_yes_no(self):
        self.assertEqual(
            DEFAULT_BIND,
            {"a": "ground_pick", "x": "roulade", "lb": "yes", "rb": "no", "dpad_down": "sit_toggle"},
        )

    def test_bind_op_and_virtual_toggle_laser_and_autonomous(self):
        # Bind LB -> laser_track and RB -> autonomous via Studio pad.op bind
        r1 = self.ops.handle({"op": "bind", "button": "lb", "skill": "laser_track"}, by="test", conn="c1")
        r2 = self.ops.handle({"op": "bind", "button": "rb", "skill": "autonomous"}, by="test", conn="c1")
        self.assertTrue(r1["accepted"])
        self.assertTrue(r2["accepted"])
        self.assertIn(("pad.bind", {"button": "lb", "skill": "laser_track"}), self.robot.calls)
        self.assertIn(("pad.bind", {"button": "rb", "skill": "autonomous"}), self.robot.calls)

        # Pressing LB on virtual gamepad toggles laser tracking ON
        self.ops.handle({"op": "key", "key": "lb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.laserTrack", {"active": True}), self.robot.calls)

        # Once robot.state reports laser active, pressing LB toggles it OFF
        self.ops.feed_robot(_robot_view(self.now, laser={"phase": "tracking"}, autonomous=False))
        self.ops.handle({"op": "key", "key": "lb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.laserTrack", {"active": False}), self.robot.calls)

        # Pressing RB toggles autonomous ON, then OFF once state reflects autonomous=True
        self.ops.handle({"op": "key", "key": "rb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.autonomous", {"active": True}), self.robot.calls)

        self.ops.feed_robot(_robot_view(self.now, laser=None, autonomous=True))
        self.ops.handle({"op": "key", "key": "rb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.autonomous", {"active": False}), self.robot.calls)

        # Reset LB back to default
        r3 = self.ops.handle({"op": "bind", "button": "lb"}, by="test", conn="c1")
        self.assertTrue(r3["accepted"])
        self.assertIn(("pad.bind", {"button": "lb"}), self.robot.calls)

    def test_default_lb_yes_works_on_virtual_pad(self):
        self.ops._fetch_bindings()
        self.ops.handle({"op": "key", "key": "lb", "src": "svg"}, by="test", conn="c1")
        self.assertIn(("robot.do", {"skill": "yes"}), self.robot.calls)


if __name__ == "__main__":
    unittest.main()
