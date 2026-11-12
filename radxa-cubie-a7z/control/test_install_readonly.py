from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import install_readonly as install

spec = importlib.util.spec_from_file_location("uart_setup", Path(__file__).with_name("cubie-servo-uart.py"))
uart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uart)


class InstallGuardTests(unittest.TestCase):
    def test_policy_edit_does_not_change_other_enabled_flags(self):
        source = "[control]\nenabled = true\n[policy]\nenabled = true # comment\nmode = 'walk'\n[safety]\nenabled = true\n"
        result = install.disable_policy(source)
        self.assertIn("[control]\nenabled = true", result)
        self.assertIn("[safety]\nenabled = true", result)
        self.assertIn("enabled = false # comment", result)
        self.assertIn("mode = 'walk'", result)

    def test_bundled_calibration_is_not_marked_verified(self):
        result = install.set_false('"imu_mount_verified" = true\nunits_verified = false\n', "imu_mount_verified")
        self.assertEqual(result, '"imu_mount_verified" = false\nunits_verified = false\n')

    def test_missing_and_duplicate_keys_refuse_to_edit(self):
        for source in ("", "enabled = true\nenabled = false\n"):
            with self.assertRaises(ValueError):
                install.set_false(source, "enabled")

    def test_startup_profile_is_read_only_and_preserves_native_port(self):
        self.assertIn("--read-only", install.PROFILE)
        self.assertNotIn("--commissioning", install.PROFILE)
        self.assertIn("CUBIE_A7Z_SYNC_GROUP_SIZE=3", install.PROFILE)
        self.assertNotIn("--port", install.PROFILE)

    def test_uart_console_and_earlycon_are_rejected(self):
        for args in ("console=ttyAS0,115200", "console=ttyS2,1000000", "earlycon=uart8250,mmio32,0x2500000"):
            self.assertTrue(uart.console_conflicts(args))
        self.assertFalse(uart.console_conflicts("quiet console=tty1 clk_ignore_unused"))


class InstallServiceTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(TemporaryDirectory()))
        self.bundle = root / "bundle"
        self.bundle.mkdir()
        files = {"robotd": b"CUBIE_A7Z_SYNC_GROUP_SIZE a7z-sync3.1-readonly",
                 "cubie-servo-uart.py": b"# UART", "bus_monitor.py": b"# monitor"}
        for name, data in files.items():
            (self.bundle / name).write_bytes(data)
        (self.bundle / "manifest.json").write_text(json.dumps({
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        }))
        self.base = root / "runtime"
        self.base.mkdir()
        (self.base / "robotd").write_bytes(b"old robotd")
        config = self.base / "robotd.toml"
        config.write_text("[policy]\nenabled = true\n")
        calibration = self.base / "hd1910-calibration.toml"
        calibration.write_text("motion_enabled = false\nimu_mount_verified = false\n")
        self.dropin_dir = root / "robotd.service.d"
        self.dropin_dir.mkdir()
        (self.dropin_dir / "zzzz-zz-microduck-commissioning.conf").write_text(
            "[Service]\nExecStart=\nExecStart=/opt/robot/feetech-ft5-r5/robotd --commissioning\n")
        compatible = root / "compatible"
        compatible.write_bytes(b"radxa,cubie-a7z\0")
        cmdline = root / "cmdline"
        cmdline.write_bytes(str(self.base / "robotd").encode() + b"\0--commissioning\0")
        robot_state = root / "robot"
        robot_state.mkdir()
        redirected = {"/sys/firmware/devicetree/base/compatible": compatible,
                      "/proc/42/cmdline": cmdline, "/proc/42": root / "exited",
                      "/var/lib/robot": robot_state, "/usr/local/lib/cubie-control": root / "lib"}
        stack.enter_context(patch.multiple(install, BASE=self.base, CONFIG=config,
                                          CALIBRATION=calibration, UART=root / "uart",
                                          DROPIN=self.dropin_dir / install.DROPIN.name))
        stack.enter_context(patch.object(install, "Path", side_effect=lambda value: redirected.get(value, Path(value))))
        stack.enter_context(patch.object(install.os, "geteuid", return_value=0))
        self.direct_kill = stack.enter_context(patch.object(install.os, "kill"))
        stack.enter_context(patch.object(install, "run", side_effect=self.run_command))
        stack.enter_context(patch.object(install, "read_status", side_effect=self.bus_status))
        self.output = stack.enter_context(redirect_stdout(io.StringIO()))
        self.started = False
        self.pid = 42
        self.commands = []
        self.effective = {}
        self.idle = {"mode": "commissioning", "phase": "ready", "torque_state": "off", "motion": None,
                     "policy_motion_enabled": False, "torque_off_pending": None, "power_pending": False}
        self.recheck = None
        self.live = {"mode": "read_only", "motor_writes_blocked": True}

    def run_command(self, *args):
        self.commands.append(args)
        if args == ("systemctl", "daemon-reload"):
            self.effective = {"ExecStart": "", "ExecStop": "", "ExecStopPost": "",
                              "KillSignal": "15", "KillMode": "control-group", "Restart": "on-failure"}
            for path in sorted(self.dropin_dir.glob("*.conf")):
                for line in path.read_text().splitlines():
                    key, _, value = line.partition("=")
                    if key in self.effective:
                        self.effective[key] = "9" if value == "SIGKILL" else value
        if args[:3] == ("systemctl", "show", "robotd"):
            key = args[3].split("=", 1)[1]
            if key == "MainPID":
                return str(self.pid)
            value = self.effective[key]
            return "{ argv[]=" + value + " ; }" if key == "ExecStart" else value
        if args == ("systemctl", "stop", "robotd.service"):
            self.assertEqual(self.effective["KillSignal"], "9")
            self.assertEqual(self.effective["Restart"], "no")
            self.pid = 0
        if args == ("systemctl", "start", "robotd.service"):
            self.started = True
            self.pid = 43
        return ""

    def bus_status(self, socket_path):
        self.assertEqual(socket_path, "/run/robotd.sock")
        if self.started:
            if isinstance(self.live, Exception):
                raise self.live
            return self.live
        if (self.recheck is not None and install.DROPIN.exists()
                and "ConditionPathExists=!/" in install.DROPIN.read_text()):
            return self.recheck
        return self.idle

    def test_read_only_start_takes_precedence_over_image_commissioning(self):
        install.install(self.bundle)
        self.assertIn("--read-only", self.effective["ExecStart"])
        self.assertNotIn("--commissioning", self.effective["ExecStart"])

    def test_install_uses_a_systemd_stop_job_before_replacing_robotd(self):
        install.install(self.bundle)
        self.direct_kill.assert_not_called()
        stop = self.commands.index(("systemctl", "stop", "robotd.service"))
        start = self.commands.index(("systemctl", "start", "robotd.service"))
        self.assertLess(stop, start)
        self.assertEqual(self.effective["KillSignal"], "15")

    def test_refuses_conflicting_later_start_without_leaving_the_unit_blocked(self):
        (self.dropin_dir / "zzzzzzzz-local.conf").write_text(
            "[Service]\nExecStart=\nExecStart=/bin/true --commissioning\n")
        with self.assertRaisesRegex(ValueError, "effective robotd service"):
            install.install(self.bundle)
        self.assertEqual((self.base / "robotd").read_bytes(), b"old robotd")
        self.assertNotIn(("systemctl", "stop", "robotd.service"), self.commands)
        self.assertFalse(install.DROPIN.exists())

    def test_refuses_when_commissioning_motion_status_is_missing(self):
        del self.idle["motion"]
        with self.assertRaisesRegex(ValueError, "confirmed idle"):
            install.install(self.bundle)
        self.assertNotIn(("systemctl", "stop", "robotd.service"), self.commands)

    def test_conflicting_stop_signal_restores_original_configuration(self):
        (self.dropin_dir / "zzzzzzzz-local.conf").write_text("[Service]\nKillSignal=SIGTERM\n")
        with self.assertRaisesRegex(ValueError, "guarded stop profile"):
            install.install(self.bundle)
        self.assertNotIn(("systemctl", "stop", "robotd.service"), self.commands)
        self.assertFalse(install.DROPIN.exists())

    def test_hold_preload_does_not_count_as_idle_with_torque_off(self):
        self.idle.update(phase="moving", hold={"stage": "ramping"})
        with self.assertRaisesRegex(ValueError, "confirmed idle"):
            install.install(self.bundle)
        self.assertNotIn(("systemctl", "stop", "robotd.service"), self.commands)

    def test_pending_power_on_recheck_restores_previous_drop_in(self):
        # Wait for the installer to load its staged start block before changing the result.
        previous = "[Service]\n# existing local settings\n"
        install.DROPIN.write_text(previous)
        self.recheck = {**self.idle, "power_pending": True}
        with self.assertRaisesRegex(ValueError, "confirmed idle"):
            install.install(self.bundle)
        self.assertEqual(install.DROPIN.read_text(), previous)
        self.assertNotIn(("systemctl", "stop", "robotd.service"), self.commands)

    def test_live_guard_failure_quiesces_the_service_and_withholds_success(self):
        self.live = {"mode": "read_only", "motor_writes_blocked": False}
        with self.assertRaisesRegex(ValueError, "read-only guard"):
            install.install(self.bundle)
        self.assertEqual(self.commands.count(("systemctl", "stop", "robotd.service")), 2)
        self.assertNotIn("started read-only", self.output.getvalue())

    def test_non_object_rpc_reply_quiesces_the_service(self):
        self.live = AttributeError("'list' object has no attribute 'get'")
        with self.assertRaisesRegex(ValueError, "read-only guard"):
            install.install(self.bundle)
        self.assertEqual(self.commands.count(("systemctl", "stop", "robotd.service")), 2)


if __name__ == "__main__":
    unittest.main()
