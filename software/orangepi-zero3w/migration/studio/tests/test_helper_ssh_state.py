"""Ubuntu SSH state checks, using only a temporary tree and fake commands."""
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest


HELPER_PATH = Path(__file__).resolve().parents[1] / "payload/usr/local/sbin/microduck-helper"


class SshStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = runpy.run_path(str(HELPER_PATH))

    def exercise(self, verb, *, effective, own_file=False, reload_rc=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "etc/ssh/sshd_config"
            config.parent.mkdir(parents=True)
            config.write_text("PasswordAuthentication yes\n")
            own_config = root / self.helper["SSH_PASSWORD_CONF"].lstrip("/")
            own_config.parent.mkdir(parents=True)
            if own_file:
                own_config.write_text("PasswordAuthentication yes\n")
            args = {} if verb == "ssh-status" else {"by": "Studio", "on": False}
            context = self.helper["Ctx"](
                root=str(root), stdin=io.BytesIO(json.dumps(args).encode()), stdout=io.BytesIO(), env={})
            context.is_root = lambda: True
            commands = []
            def run(argv, **kwargs):
                commands.append(argv)
                if argv == ["sshd", "-T"]:
                    if effective is None:
                        return 1, "", "unable to read effective SSH configuration"
                    return 0, "passwordauthentication " + ("yes" if effective else "no") + "\n", ""
                if argv == ["systemctl", "is-active", "ssh"]:
                    return 0, "active\n", ""
                if argv == ["systemctl", "reload-or-restart", "ssh"]:
                    return reload_rc, "", "reload rejected" if reload_rc else ""
                return 0, "", ""
            context.run = run
            code = self.helper["run"](["microduck-helper", verb], context)
            rows = [json.loads(line) for line in context.stdout.getvalue().splitlines()]
            final = rows[-1]["detail"]
            self.assertEqual(config.read_text(), "PasswordAuthentication yes\n")
            self.assertFalse(any(argv[0] == "chpasswd" for argv in commands))
            remaining_own_config = own_config.exists()
        return code, final, commands, remaining_own_config

    def test_status_reads_enabled_system_default_without_own_file(self):
        code, detail, commands, _ = self.exercise("ssh-status", effective=True)
        self.assertEqual(code, self.helper["EXIT_OK"])
        self.assertIs(detail["password_login"], True)
        self.assertIn(["sshd", "-T"], commands)

    def test_status_reports_disabled_effective_config_even_with_own_file(self):
        code, detail, _, remaining = self.exercise("ssh-status", effective=False, own_file=True)
        self.assertEqual(code, self.helper["EXIT_OK"])
        self.assertIs(detail["password_login"], False)
        self.assertTrue(remaining)

    def test_status_unknown_is_not_reported_as_disabled(self):
        code, detail, _, _ = self.exercise("ssh-status", effective=None)
        self.assertEqual(code, self.helper["EXIT_OK"])
        self.assertIsNone(detail["password_login"])

    def test_off_refuses_success_when_system_still_allows_passwords(self):
        for own_file in (False, True):
            with self.subTest(own_file=own_file):
                code, detail, _, remaining = self.exercise("ssh-password", effective=True, own_file=own_file)
                self.assertEqual(code, self.helper["EXIT_FAIL"])
                self.assertEqual(detail["result"], "still_enabled")
                self.assertIs(detail["password_login"], True)
                self.assertFalse(remaining)

    def test_off_succeeds_only_after_effective_disabled_readback(self):
        code, detail, _, remaining = self.exercise("ssh-password", effective=False, own_file=True)
        self.assertEqual(code, self.helper["EXIT_OK"])
        self.assertEqual(detail["result"], "ssh_password_off")
        self.assertIs(detail["password_login"], False)
        self.assertFalse(remaining)

    def test_off_unknown_is_failure_with_unknown_state(self):
        code, detail, _, _ = self.exercise("ssh-password", effective=None, own_file=True)
        self.assertEqual(code, self.helper["EXIT_FAIL"])
        self.assertEqual(detail["result"], "effective_unknown")
        self.assertIsNone(detail["password_login"])

    def test_reload_failure_does_not_invent_disabled_state(self):
        code, detail, _, _ = self.exercise("ssh-password", effective=True, own_file=True, reload_rc=1)
        self.assertEqual(code, self.helper["EXIT_FAIL"])
        self.assertEqual(detail["result"], "reload_failed")
        self.assertIs(detail["password_login"], True)


if __name__ == "__main__":
    unittest.main()
