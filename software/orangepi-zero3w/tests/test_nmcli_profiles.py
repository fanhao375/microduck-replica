"""NetworkManager profile contracts, including UUID generation by NM itself."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "provision" / "orangepi_provision.py"
spec = importlib.util.spec_from_file_location("orangepi_nm_profile_tests", MODULE_PATH)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class NetworkManagerBoundary:
    """Small stateful NM boundary that rejects read-only UUID assignments."""

    def __init__(self):
        self.profiles = {"user-network-uuid": {"name": "Existing home Wi-Fi", "mode": "infrastructure"}}
        self.active = ""
        self.commands = []
        self.timeout_after_client_add = False
        self.fail_client_activation = False
        self.next_id = 1

    @staticmethod
    def escaped(value):
        return value.replace("\\", "\\\\").replace(":", "\\:")

    def run(self, argv, timeout=15):
        if argv[0] == "ip":
            return 0, "3: wlan0 inet 192.168.9.20/24 scope global wlan0\n", ""
        if argv[0] != "nmcli":
            raise AssertionError("Unexpected executable: " + argv[0])
        args = list(argv[1:])
        fields = None
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option in ("--wait", "-e"):
                args.pop(0)
            elif option == "-f":
                fields = args.pop(0)
            elif option not in ("--", "-t"):
                raise AssertionError("Unexpected nmcli option: " + option)
        self.commands.append(args)
        if fields is not None:
            rows = []
            if args[:2] != ["connection", "show"]:
                raise AssertionError("Unexpected query: " + repr(args))
            if fields == "connection.id":
                selected = self.profiles.get(args[-1])
                rows = [["connection.id", selected["name"]]] if selected else []
            elif fields == "UUID,NAME":
                rows = [[key, value["name"]] for key, value in self.profiles.items()]
            elif fields == "UUID,TYPE":
                rows = [[key, "802-11-wireless"] for key in self.profiles]
            elif fields == "UUID,NAME,TYPE":
                rows = [[key, value["name"], "802-11-wireless"] for key, value in self.profiles.items()]
            elif fields == "UUID,DEVICE" and "--active" in args:
                rows = [[self.active, "wlan0"]] if self.active else []
            else:
                raise AssertionError("Unexpected query fields: " + fields)
            return 0, "\n".join(":".join(self.escaped(v) for v in row) for row in rows), ""
        if args == ["radio", "wifi", "on"]:
            return 0, "", ""
        operation = args[1]
        if operation == "add":
            if "connection.uuid" in args:
                return 2, "", "connection.uuid: property is read-only"
            properties = dict(zip(args[2::2], args[3::2]))
            name = properties["con-name"]
            generated_uuid = f"00000000-0000-4000-8000-{self.next_id:012d}"
            self.next_id += 1
            self.profiles[generated_uuid] = {
                "name": name,
                "mode": properties.get("802-11-wireless.mode", "infrastructure"),
                **properties,
            }
            if self.timeout_after_client_add and name.startswith(provision.CLIENT_PREFIX):
                return 124, "", "timed out after NetworkManager created the profile"
            return 0, f"Connection '{name}' ({generated_uuid}) successfully added.", ""
        if args[2] != "uuid":
            raise AssertionError("Profile operations must use NM's returned UUID: " + repr(args))
        connection_uuid = args[3]
        profile = self.profiles.get(connection_uuid)
        if profile is None:
            return 10, "", "Unknown connection"
        if operation == "modify":
            properties = dict(zip(args[4::2], args[5::2]))
            profile.update(properties)
            if "802-11-wireless.mode" in properties:
                profile["mode"] = properties["802-11-wireless.mode"]
        elif operation == "up":
            if self.fail_client_activation and profile["name"].startswith(provision.CLIENT_PREFIX):
                return 10, "", "Authentication failed"
            self.active = connection_uuid
        elif operation == "down":
            if self.active == connection_uuid:
                self.active = ""
        elif operation == "delete":
            del self.profiles[connection_uuid]
        else:
            raise AssertionError("Unexpected profile operation: " + operation)
        return 0, "", ""


class NetworkManagerProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for constant, filename in (("CLIENT_PATH", "client-uuid"), ("HOTSPOT_PATH", "hotspot-uuid"),
                                   ("ERROR_PATH", "last-error.txt")):
            self.enterContext(patch.object(provision, constant, self.directory / filename))
        self.enterContext(patch.object(provision, "hotspot_ssid", return_value="OrangePi_test"))
        self.enterContext(patch.object(provision.time, "sleep"))
        self.enterContext(patch.object(provision.uuid, "uuid4", return_value="agent-generated-suffix"))
        self.bind = self.enterContext(patch.object(provision, "bind_servers"))
        self.nm = NetworkManagerBoundary()
        self.enterContext(patch.object(provision, "run", side_effect=self.nm.run))

    def start_hotspot(self):
        self.assertTrue(provision.ensure_hotspot("wlan0"))
        return next(key for key, item in self.nm.profiles.items() if provision.is_hotspot_name(item["name"]))

    def test_hotspot_accepts_only_nm_generated_uuid_then_activates_it(self):
        actual_uuid = self.start_hotspot()
        self.assertEqual(provision.HOTSPOT_PATH.read_text(), actual_uuid)
        self.assertEqual(self.nm.active, actual_uuid)
        self.assertIn(["connection", "up", "uuid", actual_uuid], self.nm.commands)
        adds = [command for command in self.nm.commands if command[:2] == ["connection", "add"]]
        self.assertTrue(adds)
        self.assertTrue(all("connection.uuid" not in command for command in adds))

    def test_client_persists_real_nm_uuid_and_enables_autoconnect(self):
        self.start_hotspot()
        provision.connect_worker("wlan0", "Home Wi-Fi", "newpassword")
        actual_uuid = provision.CLIENT_PATH.read_text()
        self.assertNotEqual(actual_uuid, "agent-generated-suffix")
        self.assertEqual(self.nm.active, actual_uuid)
        self.assertEqual(self.nm.profiles[actual_uuid]["connection.autoconnect"], "yes")
        self.assertIn("user-network-uuid", self.nm.profiles)

    def test_add_timeout_cleans_real_created_uuid_and_restores_hotspot(self):
        hotspot = self.start_hotspot()
        provision.CLIENT_PATH.write_text("user-network-uuid")
        self.nm.timeout_after_client_add = True
        provision.connect_worker("wlan0", "Home Wi-Fi", "newpassword")
        self.assertEqual(self.nm.active, hotspot)
        self.assertIn("user-network-uuid", self.nm.profiles)
        self.assertEqual(provision.CLIENT_PATH.read_text(), "user-network-uuid")
        self.assertFalse(any(item["name"].startswith(provision.CLIENT_PREFIX)
                             for item in self.nm.profiles.values()))
        deletes = [command for command in self.nm.commands if command[:2] == ["connection", "delete"]]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][-1], "00000000-0000-4000-8000-000000000002")

    def test_activation_failure_cleans_actual_uuid_without_deleting_user_profile(self):
        hotspot = self.start_hotspot()
        self.nm.fail_client_activation = True
        provision.connect_worker("wlan0", "Home Wi-Fi", "wrongpassword")
        self.assertEqual(self.nm.active, hotspot)
        self.assertIn("user-network-uuid", self.nm.profiles)
        self.assertFalse(provision.CLIENT_PATH.exists())
        self.assertFalse(any(item["name"].startswith(provision.CLIENT_PREFIX)
                             for item in self.nm.profiles.values()))

    def test_hotspot_profile_alone_does_not_count_as_saved_client_network(self):
        self.nm.profiles.clear()
        self.start_hotspot()
        self.assertFalse(provision.saved_wifi())

    def test_legacy_imported_hotspot_uuid_is_reused(self):
        self.nm.profiles[provision.HOTSPOT_UUID] = {"name": provision.HOTSPOT_CON, "mode": "ap"}
        self.assertTrue(provision.ensure_hotspot("wlan0"))
        self.assertEqual(self.nm.active, provision.HOTSPOT_UUID)
        self.assertEqual(provision.HOTSPOT_PATH.read_text(), provision.HOTSPOT_UUID)
        self.assertFalse(any(command[:2] == ["connection", "add"] for command in self.nm.commands))

    def test_stale_hotspot_state_cannot_repurpose_user_profile(self):
        provision.HOTSPOT_PATH.write_text("user-network-uuid")
        actual_uuid = self.start_hotspot()
        self.assertNotEqual(actual_uuid, "user-network-uuid")
        self.assertEqual(self.nm.profiles["user-network-uuid"]["mode"], "infrastructure")

    def test_ambiguous_candidate_name_stops_cleanup_instead_of_deleting_any_profile(self):
        name = provision.CLIENT_PREFIX + "same-random-name"
        self.nm.profiles.update({"duplicate-one": {"name": name}, "duplicate-two": {"name": name}})
        provision.cleanup_candidate(name)
        self.assertIn("duplicate-one", self.nm.profiles)
        self.assertIn("duplicate-two", self.nm.profiles)
        self.assertFalse(any(command[:2] == ["connection", "delete"] for command in self.nm.commands))

    def test_cleanup_rejects_an_unowned_user_profile_name(self):
        provision.cleanup_candidate("Existing home Wi-Fi")
        self.assertIn("user-network-uuid", self.nm.profiles)
        self.assertEqual(self.nm.commands, [])


class ErrorRedactionTests(unittest.TestCase):
    def test_nmcli_retains_diagnostics_but_redacts_secret_values(self):
        secret = "sensitive pass phrase"
        for key in ("password", "802-11-wireless-security.psk", "802-1x.private-key-password"):
            with self.subTest(key=key), \
                 patch.object(provision, "run", return_value=(2, "", f"driver rejected '{secret}' at the adapter")):
                with self.assertRaises(provision.ProvisionError) as failed:
                    provision.nmcli(["connection", "modify", "uuid", "example", key, secret])
                message = str(failed.exception)
                self.assertNotIn(secret, message)
                self.assertIn("[REDACTED]", message)
                self.assertIn("driver rejected", message)

    def test_long_secret_is_redacted_before_diagnostic_truncation(self):
        secret = "sensitive-" * 50
        with patch.object(provision, "run", return_value=(2, "", secret + " driver rejected the profile")):
            with self.assertRaises(provision.ProvisionError) as failed:
                provision.nmcli(["connection", "modify", "uuid", "example", "psk", secret])
        message = str(failed.exception)
        self.assertNotIn("sensitive-", message)
        self.assertIn("[REDACTED]", message)
        self.assertIn("driver rejected", message)


if __name__ == "__main__":
    unittest.main()
