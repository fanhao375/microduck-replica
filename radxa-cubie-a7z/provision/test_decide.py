#!/usr/bin/env python3
import unittest
from unittest.mock import patch

import cubie_provision as p


class DecideTest(unittest.TestCase):
    def test_no_saved_wifi_opens_hotspot_immediately(self):
        self.assertEqual(p.decide(100, 100, False, "", None), "hotspot")

    def test_saved_wifi_waits_before_grace(self):
        self.assertEqual(p.decide(110, 100, True, "", None), "wait")

    def test_saved_wifi_opens_hotspot_after_grace(self):
        self.assertEqual(p.decide(145, 100, True, "", None), "hotspot")

    def test_lan_address_stays_client(self):
        self.assertEqual(p.decide(200, 100, True, "192.168.9.103", None), "client")

    def test_hold_blocks_hotspot_until_it_expires(self):
        self.assertEqual(p.decide(100, 0, False, "", 130), "hold")
        self.assertEqual(p.decide(130, 0, False, "", 130), "hotspot")

    def test_lan_address_wins_over_a_fresh_hold(self):
        self.assertEqual(p.decide(100, 0, False, "192.168.9.50", 190), "client")


class NameTest(unittest.TestCase):
    def test_hotspot_name_is_qiqilong_plus_four(self):
        original = p.suffix
        p.suffix = lambda: "42f4"
        try:
            self.assertEqual(p.hotspot_ssid(), "qiqilong_42f4")
        finally:
            p.suffix = original

    def test_page_does_not_ask_for_a_hotspot_password(self):
        text = p.page("")
        self.assertIn("不用密码", text)
        self.assertNotIn("cubiea7z", text)

    def test_captive_probe_is_the_portal_page(self):
        status, content_type, body = p.reply_for("/hotspot-detect.html")
        self.assertEqual((status, content_type, body), (200, "text/html; charset=utf-8", None))
        status, content_type, body = p.reply_for("/captive-api")
        self.assertEqual(status, 200)
        self.assertIn(b"user-portal-url", body)
        self.assertIn("address=/#/10.42.0.1", p.CAPTIVE_CONF)


class ClientAddressTest(unittest.TestCase):
    def query(self, profile, address):
        def run(args, timeout):
            if args[0] == "nmcli":
                return 0, profile + "\n", ""
            return 0, f"3: wlan0 inet {address}/24 scope global wlan0\n", ""

        with patch.object(p, "run", side_effect=run):
            return p.lan_ip("wlan0")

    def test_accepts_client_address_in_same_subnet_as_hotspot(self):
        self.assertEqual(self.query("Home Wi-Fi", "10.42.0.24"), "10.42.0.24")

    def test_ignores_hotspot_address_even_if_its_subnet_is_different(self):
        self.assertEqual(self.query("Cubie-Hotspot", "10.42.1.1"), "")

    def test_keeps_ignoring_link_local_addresses(self):
        self.assertEqual(self.query("Home Wi-Fi", "169.254.1.12"), "")

    def test_hotspot_profile_explicitly_sets_the_portal_address(self):
        with patch.object(p, "run", return_value=(0, "Cubie-Hotspot\n", "")) as run:
            p.ensure_hotspot("wlan0", "qiqilong_42f4")
        settings = next(args[0] for args, _ in run.call_args_list if "ipv4.method" in args[0])
        self.assertIn("ipv4.addresses", settings)
        self.assertIn("10.42.0.1/24", settings)

    def test_reactivates_an_active_hotspot_only_if_its_address_is_wrong(self):
        for address in ("10.42.0.1", "10.42.1.1"):
            with self.subTest(address=address):
                active_address = address

                def run(args, timeout):
                    nonlocal active_address
                    if args[0] == "ip":
                        return 0, f"3: wlan0 inet {active_address}/24 scope global wlan0\n", ""
                    if args[1:3] == ["connection", "up"]:
                        active_address = "10.42.0.1"
                    return 0, "Cubie-Hotspot\n", ""

                with patch.object(p, "run", side_effect=run) as command:
                    self.assertTrue(p.ensure_hotspot("wlan0", "qiqilong_42f4"))
                transitions = [args[0][2] for args, _ in command.call_args_list
                               if args[0][1:3] in (["connection", "down"], ["connection", "up"])]
                self.assertEqual(transitions, [] if address == "10.42.0.1" else ["down", "up"])

    def test_failed_hotspot_profile_update_stops_before_activation(self):
        def run(args, timeout):
            if "ipv4.addresses" in args:
                return 1, "", "could not update profile"
            return 0, "Cubie-Hotspot\n", ""

        with patch.object(p, "run", side_effect=run) as command:
            self.assertIs(p.ensure_hotspot("wlan0", "qiqilong_42f4"), False)
        self.assertFalse(any(args[0][1:3] in (["connection", "down"], ["connection", "up"])
                             for args, _ in command.call_args_list))


class ValidateTest(unittest.TestCase):
    def test_open_network_allows_an_empty_password(self):
        self.assertEqual(p.validate("home", ""), "")

    def test_short_password_is_rejected(self):
        self.assertTrue(p.validate("home", "1234567"))

    def test_blank_name_is_rejected(self):
        self.assertTrue(p.validate("  ", "12345678"))


if __name__ == "__main__":
    unittest.main()
