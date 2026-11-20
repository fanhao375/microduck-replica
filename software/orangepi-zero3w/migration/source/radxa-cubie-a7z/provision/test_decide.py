#!/usr/bin/env python3
import unittest

import cubie_provision as p


class DecideTest(unittest.TestCase):
    def test_no_saved_wifi_opens_hotspot_immediately(self):
        self.assertEqual(p.decide(100, 100, False, "", None), "hotspot")

    def test_saved_wifi_waits_before_grace(self):
        self.assertEqual(p.decide(110, 100, True, "", None), "wait")

    def test_saved_wifi_opens_hotspot_after_grace(self):
        self.assertEqual(p.decide(145, 100, True, "", None), "hotspot")

    def test_lan_address_stays_client(self):
        self.assertEqual(p.decide(200, 100, True, "192.0.2.103", None), "client")

    def test_hold_blocks_hotspot_until_it_expires(self):
        self.assertEqual(p.decide(100, 0, False, "", 130), "hold")
        self.assertEqual(p.decide(130, 0, False, "", 130), "hotspot")

    def test_lan_address_wins_over_a_fresh_hold(self):
        self.assertEqual(p.decide(100, 0, False, "192.0.2.50", 190), "client")


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


class ValidateTest(unittest.TestCase):
    def test_open_network_allows_an_empty_password(self):
        self.assertEqual(p.validate("home", ""), "")

    def test_short_password_is_rejected(self):
        self.assertTrue(p.validate("home", "1234567"))

    def test_blank_name_is_rejected(self):
        self.assertTrue(p.validate("  ", "12345678"))


if __name__ == "__main__":
    unittest.main()
