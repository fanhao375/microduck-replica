"""Discovery contracts: the board is identified by its pinned host key, not a port."""

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "find_master.py"
spec = importlib.util.spec_from_file_location("find_master", MODULE_PATH)
find_master = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find_master)

PINNED = "AAAAC3NzaC1lZDI1NTE5AAAAIDqJ3uKp3ioEeXIwuAt/sk9DXS9Zud8aWQmCZLUoIDzX"
OTHER = "AAAAC3NzaC1lZDI1NTE5AAAAIBcJ0mVqKpBqQ0Kk1d6oPWhQmV0Z0Z0Z0Z0Z0Z0Z0Z0Z"


class PinnedKeyTests(unittest.TestCase):
    def test_reads_the_alias_key_only(self):
        text = "192.168.9.118 ssh-ed25519 %s\norangepizero3w-setup ssh-ed25519 %s\n" % (OTHER, PINNED)
        self.assertEqual(find_master.parse_pinned_host_key(text), PINNED)

    def test_missing_alias_is_an_error(self):
        with self.assertRaises(RuntimeError):
            find_master.parse_pinned_host_key("192.168.9.118 ssh-ed25519 %s\n" % OTHER)

    def test_unrelated_key_type_is_ignored(self):
        with self.assertRaises(RuntimeError):
            find_master.parse_pinned_host_key("orangepizero3w-setup ssh-rsa AAAA\n")


class NetworkParseTests(unittest.TestCase):
    def test_interface_from_default_route(self):
        text = "   route to: default\ndestination: default\n  interface: en0\n     flags: <UP>\n"
        self.assertEqual(find_master.parse_default_interface(text), "en0")

    def test_only_slash_24_is_scanned(self):
        self.assertEqual(
            find_master.parse_prefix("\tinet 192.168.9.111 netmask 0xffffff00 broadcast 192.168.9.255"),
            "192.168.9.",
        )
        with self.assertRaises(RuntimeError):
            find_master.parse_prefix("\tinet 10.0.0.5 netmask 0xffff0000 broadcast 10.0.255.255")

    def test_arp_lookup_keeps_missing_mac_none(self):
        text = "? (192.168.9.120) at 88:e6:fc:f0:6e:4f on en0 ifscope [ethernet]\n"
        self.assertEqual(find_master.parse_arp_mac(text, "192.168.9.120"), "88:e6:fc:f0:6e:4f")
        self.assertIsNone(find_master.parse_arp_mac(text, "192.168.9.118"))


class HostKeyScanTests(unittest.TestCase):
    def test_picks_the_requested_address(self):
        text = "# 192.168.9.120:22 SSH-2.0-OpenSSH_8.9p1\n192.168.9.120 ssh-ed25519 %s\n" % PINNED
        self.assertEqual(find_master.parse_host_key_scan(text, "192.168.9.120"), PINNED)
        self.assertIsNone(find_master.parse_host_key_scan(text, "192.168.9.118"))

    def test_empty_scan_has_no_key(self):
        self.assertIsNone(find_master.parse_host_key_scan("", "192.168.9.120"))


class VerdictTests(unittest.TestCase):
    def test_matching_host_key_is_verified(self):
        evidence = {"host_key": PINNED, "ssh_open": True, "console_ok": True}
        self.assertEqual(find_master.verdict(evidence, PINNED), "verified")

    def test_mismatched_key_never_becomes_verified(self):
        evidence = {"host_key": OTHER, "ssh_open": True, "console_ok": True}
        self.assertEqual(find_master.verdict(evidence, PINNED), "probable")

    def test_console_alone_is_only_a_candidate(self):
        evidence = {"host_key": None, "ssh_open": False, "console_ok": True}
        self.assertEqual(find_master.verdict(evidence, PINNED), "candidate")

    def test_open_port_alone_is_not_a_candidate(self):
        evidence = {"host_key": None, "ssh_open": True, "console_ok": False}
        self.assertIsNone(find_master.verdict(evidence, PINNED))


if __name__ == "__main__":
    unittest.main()
