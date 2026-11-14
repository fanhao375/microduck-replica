"""Journal recovery gates must complete before any provisioning payload write."""

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "install_rootfs.py"
spec = importlib.util.spec_from_file_location("install_rootfs", MODULE_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class FilesystemPreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = self.directory / "rootfs.ext4"
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_clean_journal_requires_only_readonly_filesystem_check(self):
        stats = "Filesystem features: has_journal ext_attr extent\n"
        with patch.object(installer, "inspect", return_value=stats), \
             patch.object(installer, "run_logged") as run:
            self.assertFalse(installer.prepare_filesystem(self.target, self.directory))
        run.assert_called_once_with(
            [str(installer.E2TOOLS / "e2fsck"), "-fn", str(self.target)],
            self.directory / "filesystem-before.txt",
        )
        self.assertEqual((self.directory / "superblock-before.txt").read_text(), stats)

    def test_pending_journal_is_replayed_before_readonly_check(self):
        with patch.object(installer, "inspect", return_value="Filesystem features: has_journal needs_recovery extent\n"), \
             patch.object(installer, "run_logged") as run:
            self.assertTrue(installer.prepare_filesystem(self.target, self.directory))
        self.assertEqual(len(run.call_args_list), 2)
        recovery, readonly = run.call_args_list
        self.assertEqual(recovery.args[0], [
            str(installer.E2TOOLS / "e2fsck"), "-p", "-E", "journal_only", "-z",
            str(self.directory / "journal.e2undo"), str(self.target),
        ])
        self.assertEqual(recovery.kwargs, {"allowed_codes": (0, 1)})
        self.assertEqual(readonly.args[0], [str(installer.E2TOOLS / "e2fsck"), "-fn", str(self.target)])
        self.assertEqual(readonly.kwargs, {})

    def test_failed_journal_replay_stops_before_readonly_check(self):
        with patch.object(installer, "inspect", return_value="Filesystem features: has_journal needs_recovery\n"), \
             patch.object(installer, "run_logged", side_effect=RuntimeError("recovery failed")) as run:
            with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                installer.prepare_filesystem(self.target, self.directory)
        self.assertEqual(run.call_count, 1)
        self.assertIn("journal_only", run.call_args.args[0])

    def test_filesystem_errors_stop_installation_before_payload_mutation(self):
        report = self.directory / "report"
        responses = ["BOARD=orangepizero3w\n", "Filesystem features: has_journal extent\n"]
        with patch.object(installer, "inspect", side_effect=responses), \
             patch.object(installer, "run_logged", side_effect=RuntimeError("filesystem errors remain")), \
             patch.object(installer, "exists") as exists, \
             patch.object(installer.subprocess, "run") as mutate:
            with self.assertRaisesRegex(RuntimeError, "filesystem errors remain"):
                installer.install(self.target, self.directory / "staging", report)
        exists.assert_not_called()
        mutate.assert_not_called()
        self.assertFalse((report / "result.json").exists())

    def test_unknown_filesystem_features_stop_before_any_filesystem_command(self):
        with patch.object(installer, "inspect", return_value="not a readable superblock\n"), \
             patch.object(installer, "run_logged") as run:
            with self.assertRaisesRegex(RuntimeError, "Cannot determine filesystem features"):
                installer.prepare_filesystem(self.target, self.directory)
        run.assert_not_called()


class LoggedCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def command(self, code):
        return [sys.executable, "-c", f"print('filesystem evidence'); raise SystemExit({code})"]

    def test_explicitly_allowed_recovery_exit_one_preserves_evidence(self):
        logfile = self.directory / "recovery.txt"
        installer.run_logged(self.command(1), logfile, allowed_codes=(0, 1))
        self.assertEqual(logfile.read_text(), "filesystem evidence\n")

    def test_recovery_exit_four_is_rejected_and_preserves_evidence(self):
        logfile = self.directory / "recovery.txt"
        with self.assertRaisesRegex(RuntimeError, "exit 4"):
            installer.run_logged(self.command(4), logfile, allowed_codes=(0, 1))
        self.assertEqual(logfile.read_text(), "filesystem evidence\n")

    def test_readonly_check_rejects_nonzero_exit_even_if_recovery_would_allow_it(self):
        with self.assertRaisesRegex(RuntimeError, "exit 1"):
            installer.run_logged(self.command(1), self.directory / "check.txt")


if __name__ == "__main__":
    unittest.main()
