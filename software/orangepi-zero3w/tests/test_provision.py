"""Observable provisioning contracts; NetworkManager stays behind mocked I/O."""

import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "provision" / "orangepi_provision.py"
spec = importlib.util.spec_from_file_location("orangepi_provision", MODULE_PATH)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class PolicyTests(unittest.TestCase):
    def test_first_boot_opens_hotspot_without_waiting(self):
        self.assertEqual(provision.decide(100, 100, False, ""), "hotspot")

    def test_saved_network_gets_boot_grace_then_hotspot(self):
        self.assertEqual(provision.decide(144, 100, True, ""), "wait")
        self.assertEqual(provision.decide(145, 100, True, ""), "hotspot")

    def test_connected_client_does_not_start_hotspot(self):
        self.assertEqual(provision.decide(200, 100, True, "192.168.9.20"), "client")

    def test_connection_in_progress_is_not_interrupted_after_grace(self):
        self.assertEqual(provision.decide(300, 100, True, "", connecting=True), "hold")


class InputTests(unittest.TestCase):
    def test_ssid_byte_limit_and_legal_whitespace(self):
        self.assertEqual(provision.validate(" 网:络 ", "12345678"), "")
        self.assertEqual(provision.validate("中" * 10 + "ab", ""), "")
        self.assertTrue(provision.validate("中" * 11, "12345678"))
        self.assertTrue(provision.validate("x" * 33, "12345678"))

    def test_empty_ssid_and_control_characters_are_rejected(self):
        for ssid in ("", "bad\nname", "bad\rname", "bad\x00name"):
            with self.subTest(ssid=repr(ssid)):
                self.assertTrue(provision.validate(ssid, "12345678"))

    def test_password_boundaries_and_raw_hex_key(self):
        for password in ("", "x" * 8, "x" * 63, "a1" * 32):
            with self.subTest(length=len(password)):
                self.assertEqual(provision.validate("home", password), "")
        for password in ("x" * 7, "x" * 64, "a1" * 33, "1234567\n", "密码密码密码密码"):
            with self.subTest(password=repr(password)):
                self.assertTrue(provision.validate("home", password))

    def test_nmcli_escaped_colon_and_backslash_are_unescaped(self):
        self.assertEqual(provision.split_nmcli(r"living\:room\\wifi:802-11-wireless"),
                         ["living:room\\wifi", "802-11-wireless"])


class NetworkBoundaryTests(unittest.TestCase):
    def test_missing_wifi_device_does_not_fall_back_to_wlan0(self):
        with patch.object(provision, "run", return_value=(0, "eth0:ethernet\n", "")):
            self.assertEqual(provision.wifi_device(), "")

    def test_wifi_device_command_failure_has_no_invented_interface(self):
        with patch.object(provision, "run", return_value=(1, "", "not ready")):
            with self.assertRaises(provision.ProvisionError):
                provision.wifi_device()

    def test_escaped_wifi_interface_is_decoded(self):
        with patch.object(provision, "run", return_value=(0, "eth0:ethernet\nwlan0:wifi\n", "")):
            self.assertEqual(provision.wifi_device(), "wlan0")


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(provision, "hotspot_ssid", return_value="OrangePi_test"))

    def request(self, length, payload=b""):
        handler = provision.Handler.__new__(provision.Handler)
        handler.path = "/connect"
        handler.headers = {"Content-Length": length}
        handler.rfile = io.BytesIO(payload)
        with patch.object(handler, "_send") as send:
            handler.do_POST()
        return send.call_args.args

    def test_invalid_content_length_is_rejected_without_reading(self):
        for value in ("-1", "not-a-number"):
            with self.subTest(length=value):
                self.assertIn(self.request(value)[0], (400, 413))

    def test_large_content_length_is_rejected(self):
        self.assertEqual(self.request("4097")[0], 413)

    def test_empty_form_is_validation_error(self):
        self.assertEqual(self.request("0")[0], 400)

    def test_html_messages_escape_network_controlled_text(self):
        unsafe = '<script>alert("ssid")</script>'
        self.assertNotIn(unsafe, provision.page(unsafe))
        self.assertIn("&lt;script&gt;", provision.page(unsafe))
        self.assertNotIn(unsafe, provision.result_page(unsafe))


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.client_path = directory / "client-uuid"
        self.error_path = directory / "last-error.txt"
        self.enterContext(patch.object(provision, "CLIENT_PATH", self.client_path))
        self.enterContext(patch.object(provision, "ERROR_PATH", self.error_path))
        self.enterContext(patch.object(provision.time, "sleep"))
        self.enterContext(patch.object(provision.uuid, "uuid4", return_value="new-candidate"))
        self.enterContext(patch.object(provision, "profile_uuid", return_value="nm-generated-candidate"))
        self.enterContext(patch.object(provision, "hotspot_uuid", return_value="nm-generated-hotspot"))
        self.enterContext(patch.object(provision, "hotspot_active", return_value=True))
        self.enterContext(patch.object(provision, "active_uuid", return_value="nm-generated-candidate"))
        self.enterContext(patch.object(provision, "lan_ip", return_value="192.168.9.20"))
        self.restore = self.enterContext(patch.object(provision, "ensure_hotspot"))
        self.bind = self.enterContext(patch.object(provision, "bind_servers"))
        self.commands = []
        self.cleanup = self.enterContext(patch.object(provision, "run", return_value=(0, "", "")))
        self.enterContext(patch.object(provision, "nmcli", side_effect=self.nmcli))

    def nmcli(self, args, timeout=20):
        self.commands.append(args)
        return ""

    def test_success_saves_network_for_boot_and_only_removes_owned_previous_profile(self):
        self.client_path.write_text("old-owned-profile")
        self.error_path.write_text("Previous attempt failed")
        with patch.object(provision, "query", return_value=[["connection.id", provision.CLIENT_PREFIX + "old"]]):
            provision.connect_worker("wlan0", " 房间:WiFi ", "newpassword")
        self.assertEqual(self.client_path.read_text(), "nm-generated-candidate")
        self.assertFalse(self.error_path.exists())
        enabled = [cmd for cmd in self.commands if "connection.autoconnect" in cmd
                   and cmd[cmd.index("connection.autoconnect") + 1] == "yes"]
        self.assertEqual(len(enabled), 1)
        self.assertIn("nm-generated-candidate", enabled[0])
        self.assertLess(self.commands.index(["connection", "up", "uuid", "nm-generated-candidate"]),
                        self.commands.index(enabled[0]))
        self.assertIn(["connection", "delete", "uuid", "old-owned-profile"], self.commands)
        self.restore.assert_not_called()
        self.bind.assert_not_called()

    def test_success_preserves_user_profile_even_if_state_file_points_to_it(self):
        self.client_path.write_text("user-profile")
        with patch.object(provision, "query", return_value=[["connection.id", "Home Wi-Fi"]]):
            provision.connect_worker("wlan0", "home", "newpassword")
        deleted = [cmd for cmd in self.commands if cmd[:2] == ["connection", "delete"]]
        self.assertEqual(deleted, [])
        self.assertEqual(self.client_path.read_text(), "nm-generated-candidate")

    def test_wrong_password_removes_candidate_restores_hotspot_and_preserves_saved_network(self):
        self.client_path.write_text("previous-working-profile")
        original = self.nmcli

        def wrong_password(args, timeout=20):
            result = original(args, timeout)
            if args == ["connection", "up", "uuid", "nm-generated-candidate"]:
                raise provision.ProvisionError("Authentication failed")
            return result

        with patch.object(provision, "nmcli", side_effect=wrong_password):
            provision.connect_worker("wlan0", "home", "wrongpassword")
        self.assertEqual(self.client_path.read_text(), "previous-working-profile")
        self.assertTrue(self.error_path.exists())
        self.assertNotIn("wrongpassword", self.error_path.read_text())
        cleanup_commands = [call.args[0] for call in self.cleanup.call_args_list]
        self.assertTrue(any("delete" in cmd and "nm-generated-candidate" in cmd for cmd in cleanup_commands))
        self.assertFalse(any("previous-working-profile" in cmd for cmd in cleanup_commands))
        self.assertFalse(any("connection.autoconnect" in cmd
                             and cmd[cmd.index("connection.autoconnect") + 1] == "yes"
                             for cmd in self.commands))
        self.restore.assert_called_once_with("wlan0")
        self.bind.assert_called_once_with()

    def test_stale_ip_from_another_profile_does_not_count_as_candidate_success(self):
        with patch.object(provision, "active_uuid", return_value="another-profile"):
            provision.connect_worker("wlan0", "home", "newpassword")
        self.assertFalse(self.client_path.exists())
        self.restore.assert_called_once_with("wlan0")

    def test_add_timeout_cleans_possibly_created_candidate_and_preserves_previous_profile(self):
        self.client_path.write_text("previous-working-profile")
        with patch.object(provision, "nmcli", side_effect=provision.ProvisionError("add timed out")):
            provision.connect_worker("wlan0", "home", "newpassword")
        cleanup_commands = [call.args[0] for call in self.cleanup.call_args_list]
        self.assertEqual([cmd[4] for cmd in cleanup_commands], ["down", "delete"])
        self.assertTrue(all(cmd[-1] == "nm-generated-candidate" for cmd in cleanup_commands))
        self.assertEqual(self.client_path.read_text(), "previous-working-profile")
        self.restore.assert_called_once_with("wlan0")
        self.bind.assert_called_once_with()


class HotspotTests(unittest.TestCase):
    def test_hotspot_uses_fixed_portal_subnet_and_requires_verified_activation(self):
        with patch.object(provision, "hotspot_active", side_effect=[False, True]), \
             patch.object(provision, "hotspot_uuid", return_value="nm-generated-hotspot"), \
             patch.object(provision, "hotspot_ssid", return_value="OrangePi_test"), \
             patch.object(provision, "nmcli") as nmcli:
            self.assertTrue(provision.ensure_hotspot("wlan0"))
        modified = next(call.args[0] for call in nmcli.call_args_list if call.args[0][:2] == ["connection", "modify"])
        self.assertEqual(modified[modified.index("ipv4.addresses") + 1], "10.42.0.1/24")
        self.assertEqual(modified[modified.index("ipv4.method") + 1], "shared")
        self.assertEqual(modified[modified.index("connection.autoconnect") + 1], "no")

    def test_failed_hotspot_activation_is_not_reported_as_ready(self):
        with patch.object(provision, "hotspot_active", return_value=False), \
             patch.object(provision, "nmcli", side_effect=provision.ProvisionError("driver error")):
            with self.assertRaises(provision.ProvisionError):
                provision.ensure_hotspot("wlan0")


if __name__ == "__main__":
    unittest.main()
