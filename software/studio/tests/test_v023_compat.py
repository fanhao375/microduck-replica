"""Unit tests for v0.23 dual-mode IPC compatibility (while preserving legacy busStatus)."""
import asyncio
import importlib.machinery
import importlib.util
import math
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch

from studio.battery_svc import BatteryService
from studio.log import Log
from studio.pad import robot_view
from studio.robotd import Robotd, Sample, calib_from_file
from studio.rpc import RpcError
from studio.servo import readonly_view, view_from_state
from studio.state import autonomy_of, project_bus, summarize, viewed_bus


def _make_robotd(tmp_dir):
    return Robotd("none", "none", Log(tmp_dir))


class ProbeDaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_v023_sets_legacy_false_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)

            async def fake_call_once(addr, method, params, **kw):
                if method == "robot.modelApi":
                    return {"model_api": 2}
                if method == "robot.mode":
                    return {"mode": "walk"}
                raise AssertionError(f"unexpected method: {method}")

            with patch("studio.robotd.call_once", side_effect=fake_call_once):
                await rd._probe_daemon()

            self.assertIs(rd.legacy_bus, False)
            self.assertEqual(rd.model_api_version, 2)
            self.assertEqual(rd.daemon_mode, "walk")
            self.assertTrue(rd._motion_stream_ready())

    async def test_probe_legacy_32601_keeps_bus_status_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)

            async def fake_call_once(addr, method, params, **kw):
                raise RpcError(method, -32601, "method not found")

            with patch("studio.robotd.call_once", side_effect=fake_call_once):
                await rd._probe_daemon()

            self.assertIs(rd.legacy_bus, True)
            self.assertIsNone(rd.model_api_version)
            self.assertFalse(rd._motion_stream_ready())


class StateProjectionTests(unittest.TestCase):
    def test_project_bus_prefers_safety_torqued(self):
        st_torqued = {"loop": {"hz": 50.0}, "policy": "walk", "safety": {"limp": False, "torqued": True}}
        bus = project_bus(None, st_torqued, {"homed": True})
        self.assertEqual(bus["mode"], "motion")
        self.assertEqual(bus["phase"], "control")
        self.assertTrue(bus["policy_enabled"])
        self.assertTrue(bus["native"]["torque_state_confirmed"])
        self.assertTrue(bus["homed"])

        st_relaxed = {"loop": {"hz": 50.0}, "policy": "held", "safety": {"limp": False, "torqued": False}}
        bus_off = project_bus(None, st_relaxed)
        self.assertFalse(bus_off["policy_enabled"])
        self.assertFalse(bus_off["native"]["torque_state_confirmed"])

    def test_project_bus_zero_hz_marks_starting(self):
        st_zero = {"loop": {"hz": 0}, "safety": {"limp": False}}
        bus = project_bus({"mode": "commissioning"}, st_zero)
        self.assertEqual(bus["phase"], "configuring_bus")
        self.assertNotIn("mode", bus)

    def test_autonomy_of_distinguishes_false_vs_unknown(self):
        self.assertEqual(autonomy_of(False, {"autonomous": True}), (None, None))
        self.assertEqual(autonomy_of(True, None), (None, None))
        auto, drv = autonomy_of(True, {"move": {"source": "padd", "limited_by": ["deadman"]}})
        self.assertIs(auto, False)
        self.assertEqual(drv, {"source": "padd", "limited_by": ["deadman"]})
        auto2, drv2 = autonomy_of(True, {"autonomous": True, "move": {"source": "autod"}})
        self.assertIs(auto2, True)
        self.assertEqual(drv2, {"source": "autod", "limited_by": []})

    def test_summarize_v023_uses_state_projection_and_autonomy(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.present = True
            rd.legacy_bus = False
            rd.state.set({
                "loop": {"hz": 50.0},
                "policy": "walk",
                "autonomous": True,
                "move": {"source": "autod", "applied": [0.1, 0.0, 0.0]},
                "safety": {"limp": False, "torqued": True, "gravity": [0.0, 0.0, -9.8]},
                "joints": [0.0] * 15,
            })
            rd.policies.set({"homed": True, "sitting": False})
            snap = summarize(rd, {"label": "duck"})
            self.assertTrue(snap["robotd_ok"])
            self.assertEqual(snap["posture"], "walking")
            self.assertIs(snap["autonomous"], True)
            self.assertEqual(snap["driver"], {"source": "autod", "limited_by": []})

    def test_viewed_bus_and_pad_robot_view_on_v023(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.present = True
            rd.legacy_bus = False
            rd.state.set({"loop": {"hz": 50.0}, "policy": "held", "safety": {"limp": False, "torqued": False}})
            vb = viewed_bus(rd)
            self.assertEqual(vb["phase"], "control")
            self.assertFalse(vb["native"]["torque_state_confirmed"])
            rv = robot_view(rd)
            self.assertEqual(rv["bus"]["mode"], "motion")
            self.assertIsNotNone(rv["bus_at"])


class EstopAndAutonomyRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_estop_on_state_confirms_when_torqued_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.legacy_bus = False
            now = time.monotonic()
            rd.estop.press("test", now)
            rd.estop.sent(now)
            confirmed = rd._estop_on_state({
                "loop": {"hz": 50.0},
                "safety": {"limp": False, "torqued": False},
            })
            self.assertTrue(confirmed)
            self.assertFalse(rd.estop.active)

    async def test_set_autonomous_rpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            calls = []

            async def fake_call_once(addr, method, params, **kw):
                calls.append((method, params))
                return {"accepted": True}

            with patch("studio.robotd.call_once", side_effect=fake_call_once):
                ok, reason = await rd.set_autonomous(True, "test")

            self.assertTrue(ok)
            self.assertIsNone(reason)
            self.assertEqual(calls, [("robot.autonomous", {"active": True})])


class ConsumerCompatTests(unittest.TestCase):
    def test_readonly_view_v023_uses_state_joints(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.legacy_bus = False
            rd.state.set({
                "loop": {"hz": 50.0},
                "joints": [math.radians(15.0)] + [0.0] * 14,
                "safety": {"limp": False, "torqued": True},
            })
            snap = readonly_view(rd, directions={})
            self.assertEqual(snap["online"], 15)
            self.assertEqual(snap["holding"], 15)
            self.assertEqual(snap["state"], "closed")
            self.assertAlmostEqual(snap["rows"][0]["deg"], 15.0, places=1)
            self.assertEqual(snap["rows"][0]["torque"], 1)

    def test_readonly_view_legacy_uses_bus_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.legacy_bus = True
            rd.bus.set({"native": {"joints": []}})
            snap = readonly_view(rd, directions={})
            self.assertEqual(snap["t"], "servo")
            self.assertEqual(snap["online"], 0)

    def test_battery_service_health_pack_fallback_on_v023(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = _make_robotd(tmp)
            rd.present = True
            rd.legacy_bus = False
            rd.health.set({"battery": {"volts": 7.82, "percent": 74.0}})
            rd.state.set({"loop": {"hz": 50.0}, "policy": "held", "safety": {"limp": False, "torqued": False}})
            svc = BatteryService(rd, Log(tmp), state_dir=tmp)
            now = time.monotonic()
            sample = svc.sample(now)
            self.assertIsNotNone(sample)
            self.assertEqual(sample[0], "pack")
            self.assertAlmostEqual(sample[1], 7.82, places=2)
            svc.tick(now)
            v = svc.view(now)
            self.assertEqual(v["source"], "health")
            self.assertAlmostEqual(v["rd_volts"], 7.82, places=2)

    def test_calib_from_file_parses_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "hd1910-calibration.toml"
            p.write_text(
                'imu_mount_quat = [1.0, 0.0, 0.0, 0.0]\n'
                'imu_mount_verified = true\n'
                '[[joints]]\nname = "left_hip_yaw"\nid = 11\n',
                encoding="utf-8",
            )
            got = calib_from_file(str(p))
            self.assertIsNotNone(got)
            self.assertTrue(got["from_file"])
            self.assertTrue(got["imu_mount"]["verified"])
            self.assertEqual(len(got["calibration"]["joints"]), 1)

    def test_helper_startup_step4_returns_unsupported_on_32601(self):
        helper_path = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "microduck-helper"
        loader = importlib.machinery.SourceFileLoader("microduck_helper_mod", str(helper_path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)

        class _FakeCtx:
            def startup_report(self, timeout=1.0):
                raise mod.RpcError("robot.startupSubscribe: {'code': -32601, 'message': 'Method not found'}")

        class _FakeOp:
            ctx = _FakeCtx()

        self.assertEqual(mod.startup_step4(_FakeOp()), "unsupported")


if __name__ == "__main__":
    unittest.main()
