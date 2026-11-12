"""Public installations must create their own credentials and sessions."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "orangepi_install", Path(__file__).resolve().parents[2] / "install.py"
)
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class FreshStateTests(unittest.TestCase):
    def test_accepts_absent_or_empty_state_without_creating_files(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            INSTALLER.require_clean_state(state)
            self.assertFalse(state.exists())
            state.mkdir()
            INSTALLER.require_clean_state(state)
            self.assertEqual(list(state.iterdir()), [])

    def test_preserves_and_rejects_existing_credentials_or_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            for name in ("password.json", "sessions.json"):
                with self.subTest(name=name):
                    record = state / name
                    record.write_text("existing device state")
                    with self.assertRaises(RuntimeError):
                        INSTALLER.require_clean_state(state)
                    self.assertEqual(record.read_text(), "existing device state")
                    record.unlink()

    def test_rejects_symlinks_and_regular_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for target in (root / "missing", root):
                link = root / "state-link"
                link.symlink_to(target)
                with self.assertRaises(RuntimeError):
                    INSTALLER.require_clean_state(link)
                link.unlink()
            record = root / "state-file"
            record.touch()
            with self.assertRaises(RuntimeError):
                INSTALLER.require_clean_state(record)


if __name__ == "__main__":
    unittest.main()
