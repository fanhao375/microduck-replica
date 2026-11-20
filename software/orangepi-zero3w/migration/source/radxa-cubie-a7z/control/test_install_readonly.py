import importlib.util
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
