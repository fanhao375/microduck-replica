"""Migration contracts; no serial devices, daemons or real credentials are used."""
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
PAYLOAD = BASE / "payload"
APP = PAYLOAD / "opt/microduck-studio/app"
sys.path[:0] = [str(APP / "deps"), str(APP)]
from studio.auth import Auth  # noqa: E402


class PayloadTests(unittest.TestCase):
    def test_runtime_imports_and_disabled_socket_selfcheck(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, "-I", str(APP / "run.py"), "--selfcheck", "--robotd", "none",
                 "--configd", "none", "--tof", "none", "--camera-socket", "none",
                 "--state-dir", str(Path(temp) / "state"), "--log-dir", str(Path(temp) / "logs")],
                text=True, capture_output=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Python", result.stdout)

    def test_new_state_is_independent_of_linux_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            auth = Auth(temp)
            self.assertFalse(auth.has_password())
            token, why, _ = auth.login("not-a-real-system-password", "127.0.0.1")
            self.assertIsNone(token)
            self.assertIn("还没设", why)
            auth.set_password("temporary-test-credential")
            token, why, _ = auth.login("temporary-test-credential", "127.0.0.1")
            self.assertIsNotNone(token)
            self.assertIsNone(why)
            self.assertEqual((Path(temp) / "password.json").stat().st_mode & 0o777, 0o600)

    def test_service_has_real_auth_and_unconfigured_serial(self):
        unit = (PAYLOAD / "etc/systemd/system/microduck-studio.service").read_text()
        self.assertIn("User=orangepi\n", unit)
        self.assertIn("SupplementaryGroups=robot dialout\n", unit)
        self.assertIn("--servo-port /dev/orangepi-robot-uart-unconfigured", unit)
        self.assertIn("--robotd unix:/run/robotd.sock", unit)
        self.assertIn("--configd none", unit)
        self.assertNotIn("--dev-autologin", unit)
        self.assertNotIn("--servo-sim", unit)
        self.assertFalse((PAYLOAD / "var/lib/microduck-studio").exists())


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = runpy.run_path(str(PAYLOAD / "usr/local/sbin/microduck-helper"))

    def exercise(self, verb):
        context_type = self.helper["Ctx"]
        with tempfile.TemporaryDirectory() as temp:
            context = context_type(root=temp, stdin=io.BytesIO(b"{}"), stdout=io.BytesIO(), env={})
            commands = []
            context.is_root = lambda: True
            def run(argv, **kwargs):
                commands.append(argv)
                if argv[:3] == ["systemctl", "show", "robotd"]:
                    return 0, "ExecStart=/opt/robot/feetech-ft5-r5/robotd --read-only\n", ""
                return 0, "", ""
            context.run = run
            code = self.helper["run"](["microduck-helper", verb], context)
            rows = [json.loads(line) for line in context.stdout.getvalue().splitlines()]
        return code, rows[-1]["detail"]["result"], commands

    def test_legacy_wifi_verbs_cannot_race_working_provisioner(self):
        for verb in ("wifi-hotspot-down", "wifi-hotspot-up"):
            with self.subTest(verb=verb):
                code, result, commands = self.exercise(verb)
                self.assertEqual(code, self.helper["EXIT_STATE"])
                self.assertEqual(result, "external_network_manager")
                self.assertFalse(any(argv[0] == "nmcli" for argv in commands))

    def test_readonly_robotd_still_blocks_calibration_and_update(self):
        for verb in ("calib-commit", "imu-recalibrate", "robotd-update"):
            with self.subTest(verb=verb):
                code, result, commands = self.exercise(verb)
                self.assertEqual(code, self.helper["EXIT_STATE"])
                self.assertEqual(result, "read_only")
                self.assertFalse(any("restart" in argv or "start" in argv for argv in commands))


if __name__ == "__main__":
    unittest.main()
