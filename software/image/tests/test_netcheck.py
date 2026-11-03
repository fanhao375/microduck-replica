#!/usr/bin/env python3
"""microduck-netcheck：45 秒规则、开热点前扫描、重试、热点空闲关断、net.json。"""
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import BASE  # noqa: E402

SCRIPT = BASE / "overlay/usr/local/sbin/microduck-netcheck"


def load_program():
    loader = importlib.machinery.SourceFileLoader("microduck_netcheck", str(SCRIPT))
    spec = importlib.util.spec_from_loader("microduck_netcheck", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


NC = load_program()

SCAN = ("Home:72:WPA2\n"
        "Home:40:WPA2\n"
        "Cafe:30:--\n"
        "Corp:55:WPA2 802.1X\n"
        "WPA3:60:WPA3\n"
        ":10:WPA2\n")
SAVED_CONNECTIONS = [("Microduck-Hotspot", "802-11-wireless"),
                     ("Home", "802-11-wireless"),
                     ("Wired", "802-3-ethernet")]


def nmcli_escape(value):
    return value.replace("\\", "\\\\").replace(":", "\\:")


class FakeNmcli:
    def __init__(self):
        self.calls = []
        self.device = ("none", "")
        self.device_rc = 0
        self.scan_out = SCAN
        self.scan_rc = 0
        self.connections = list(SAVED_CONNECTIONS)
        self.ssids = {"Home": "Home"}
        self.ssid_rc = {}
        self.up_rc = 0
        self.up_stdout = "up out\n"
        self.up_stderr = "up err\n"
        self.down_rc = 0
        self.stations = ""
        self.iw_rc = 0
        self.firstboot_rc = 0
        self.conn_rc = 0
        self.hotspot_keyfile = None
        self.load_rc = 0
        self.load_registers = True
        self.up_sets_hotspot = True

    def __call__(self, args, timeout=None):
        args = list(args)
        self.calls.append((args, timeout))
        if args[:1] and args[0].endswith("microduck-firstboot"):
            if self.firstboot_rc == 0 and self.hotspot_keyfile is not None:
                self.hotspot_keyfile.write_text("[wifi]\nssid=Microduck-c51b\n[wifi-security]\npsk=microduck\n", encoding="utf-8")
            return NC.Result(self.firstboot_rc, "",
                             "" if self.firstboot_rc == 0 else "firstboot failed\n")
        if args[:3] == ["nmcli", "connection", "load"]:
            if self.load_rc:
                return NC.Result(self.load_rc, "", "profile load failed\n")
            if not Path(args[3]).is_file():
                return NC.Result(10, "", "profile file missing\n")
            if self.load_registers and not any(name == NC.HOTSPOT_NAME for name, _ in self.connections):
                self.connections.append((NC.HOTSPOT_NAME, "802-11-wireless"))
            return NC.Result(0, "profile loaded\n", "")
        if args[:1] == ["nmcli"] and "device" in args and "show" in args:
            if self.device_rc:
                return NC.Result(self.device_rc, "", "device show failed\n")
            state, conn = self.device
            number = {"none": 30, "wifi": 100, "hotspot": 100, "connecting": 50,
                      "unavailable": 20}[state]
            return NC.Result(0, "GENERAL.STATE:%d (x)\nGENERAL.CONNECTION:%s\n"
                             % (number, conn or "--"), "")
        if args[:1] == ["nmcli"] and "wifi" in args and "list" in args:
            return NC.Result(self.scan_rc, self.scan_out if self.scan_rc == 0 else "",
                             "" if self.scan_rc == 0 else "scan failed\n")
        if (args[:1] == ["nmcli"] and "-g" in args and "connection" in args
                and "show" in args and "id" in args):
            name = args[args.index("id") + 1]
            rc = self.ssid_rc.get(name, 0)
            if rc:
                return NC.Result(rc, "", "no ssid\n")
            if name not in self.ssids:
                return NC.Result(0, "", "")
            return NC.Result(0, nmcli_escape(self.ssids[name]) + "\n", "")
        if (args[:1] == ["nmcli"] and "-t" in args and "NAME,TYPE" in args
                and "connection" in args and "show" in args):
            if self.conn_rc:
                return NC.Result(self.conn_rc, "", "connection show failed\n")
            out = "".join("%s:%s\n" % (nmcli_escape(name), nmcli_escape(kind))
                          for name, kind in self.connections)
            return NC.Result(0, out, "")
        if args[:1] == ["nmcli"] and "connection" in args and "up" in args:
            if self.up_rc == 0:
                if not any(name == NC.HOTSPOT_NAME for name, _ in self.connections):
                    return NC.Result(10, "", "unknown connection\n")
                if self.up_sets_hotspot:
                    self.device = ("hotspot", "Microduck-Hotspot")
                return NC.Result(0, self.up_stdout, self.up_stderr)
            return NC.Result(self.up_rc, self.up_stdout, self.up_stderr)
        if args[:1] == ["nmcli"] and "connection" in args and "down" in args:
            if self.down_rc == 0:
                self.device = ("none", "")
            return NC.Result(self.down_rc, "" if self.down_rc == 0 else "down failed\n", "")
        if args[:1] == ["iw"]:
            return NC.Result(self.iw_rc, self.stations,
                             "" if self.iw_rc == 0 else "iw failed\n")
        return NC.Result(1, "", "unknown command\n")

    def calls_of(self, *words):
        return [call for call in self.calls if all(word in call[0] for word in words)]


def make_root(tmp):
    root = Path(tmp)
    for rel in ["etc/NetworkManager/system-connections", "run/microduck", "var/log",
                "sys/class/net/wlan0"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "etc/hostname").write_text("duck-c51b\n", encoding="utf-8")
    (root / "etc/NetworkManager/system-connections/Microduck-Hotspot.nmconnection").write_text(
        "[wifi]\nssid=Microduck-c51b\n[wifi-security]\npsk=microduck\n", encoding="utf-8")
    return root


class NetcheckTests(unittest.TestCase):
    def make(self, fake=None, now=0.0):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_root(tmp.name)
        if fake is None:
            fake = FakeNmcli()
        fake.hotspot_keyfile = root / "etc/NetworkManager/system-connections/Microduck-Hotspot.nmconnection"
        log = io.StringIO()
        nc = NC.Netcheck(root=root, run_cmd=fake, now=lambda: now, log_stream=log)
        return root, fake, nc, log

    def test_44_seconds_does_not_open_45_does(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(44)
        self.assertEqual(nc.mode, "none")
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(45)
        self.assertEqual(nc.mode, "hotspot")
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_no_saved_wifi_opens_the_hotspot_at_once(self):
        # 10-21：新卡一个 WiFi 都没存，热点也要等 45 秒才开，白等
        root, fake, nc, log = self.make()
        fake.connections = [("Microduck-Hotspot", "802-11-wireless"), ("Wired", "802-3-ethernet")]
        nc.tick(0)
        self.assertEqual(nc.mode, "hotspot")
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        self.assertIn("一个 WiFi 都没存过", log.getvalue())

    def test_unknown_saved_list_keeps_the_45_second_rule(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Microduck-Hotspot", "802-11-wireless")]
        fake.conn_rc = 4
        nc.tick(0)
        nc.tick(44)
        self.assertEqual(nc.mode, "none")
        fake.conn_rc = 0
        nc.tick(45)
        self.assertEqual(nc.mode, "hotspot")

    def test_saved_wifi_still_waits_45_seconds_and_respects_retry(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(30)
        self.assertEqual(nc.mode, "none")            # Home 存着：照旧等 45 秒
        fake.connections = [("Microduck-Hotspot", "802-11-wireless")]
        fake.up_rc = 1
        nc.tick(33)                                   # 刚删光：马上开，但开失败
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        fake.up_rc = 0
        nc.tick(36)                                   # 失败后 60 秒内不重试
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        nc.tick(93)
        self.assertEqual(nc.mode, "hotspot")

    def test_scan_happens_before_hotspot_up(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(45)
        kinds = [("scan" if ("wifi" in call[0] and "list" in call[0]) else
                  "up" if ("connection" in call[0] and "up" in call[0]) else "other")
                 for call in fake.calls]
        self.assertIn("scan", kinds)
        self.assertIn("up", kinds)
        self.assertLess(kinds.index("scan"), kinds.index("up"))

    def test_scan_content_is_normalized(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(45)
        state = json.loads((root / "run/microduck/net.json").read_text(encoding="utf-8"))
        self.assertEqual([item["ssid"] for item in state["scan"]],
                         ["Home", "WPA3", "Corp", "Cafe"])
        self.assertEqual(state["scan"][0], {"ssid": "Home", "signal": 72,
                                            "security": "wpa_psk", "saved": True})
        self.assertEqual(state["scan"][1]["security"], "wpa3_sae")
        self.assertEqual(state["scan"][2]["security"], "enterprise")
        self.assertEqual(state["scan"][3]["security"], "open")
        self.assertEqual(state["scan_at"], 45.0)

    def test_failed_hotspot_retries_after_60_seconds(self):
        root, fake, nc, log = self.make()
        fake.up_rc = 1
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        self.assertIn("up out", log.getvalue())
        nc.tick(104)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        nc.tick(105)
        self.assertEqual(len(fake.calls_of("connection", "up")), 2)

    def test_connecting_pauses_the_45_second_timer(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(30)
        fake.device = ("connecting", "Home")
        nc.tick(31)
        nc.tick(100)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        fake.device = ("none", "")
        nc.tick(101)
        nc.tick(115)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(116)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_connecting_over_120_seconds_becomes_none(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        fake.device = ("connecting", "Home")
        nc.tick(1)
        nc.tick(121)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(122)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_hotspot_5_minutes_idle_closes_for_saved_wifi(self):
        root, fake, nc, log = self.make()
        fake.device = ("hotspot", "Microduck-Hotspot")
        nc.tick(0)
        self.assertEqual(nc.mode, "hotspot")
        nc.tick(299)
        self.assertEqual(fake.calls_of("connection", "down"), [])
        nc.tick(300)
        self.assertEqual(len(fake.calls_of("connection", "down")), 1)
        self.assertEqual(nc.mode, "none")
        self.assertIn("热点 5 分钟没人连", log.getvalue())

    def test_hotspot_with_a_connected_phone_is_never_closed(self):
        root, fake, nc, log = self.make()
        fake.device = ("hotspot", "Microduck-Hotspot")
        fake.stations = "Station aa:bb:cc:dd:ee:ff (on wlan0)\n"
        nc.tick(0)
        nc.tick(300)
        self.assertEqual(fake.calls_of("connection", "down"), [])
        self.assertEqual(nc.mode, "hotspot")

    def test_hotspot_without_saved_wifi_stays(self):
        root, fake, nc, log = self.make()
        fake.device = ("hotspot", "Microduck-Hotspot")
        fake.connections = [("Microduck-Hotspot", "802-11-wireless")]
        nc.tick(0)
        nc.tick(300)
        self.assertEqual(fake.calls_of("connection", "down"), [])
        self.assertEqual(nc.mode, "hotspot")

    def test_saved_wifi_uses_name_type_list_and_get_values(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless"),
                            ("Cafe", "802-11-wireless"),
                            ("Wired", "802-3-ethernet")]
        fake.ssids = {"Home": "Home", "Cafe": "Cafe:Guest"}
        self.assertEqual(nc.saved_wifi_names(), ["Home", "Cafe"])
        self.assertEqual(nc.saved_ssids(), {"Home", "Cafe:Guest"})
        self.assertTrue(any(call[0] == ["nmcli", "-t", "-f", "NAME,TYPE",
                                        "connection", "show"] for call in fake.calls))
        self.assertTrue(any(call[0] == ["nmcli", "-g", "802-11-wireless.ssid",
                                        "connection", "show", "id", "Cafe"]
                            for call in fake.calls))
        self.assertFalse(any("802-11-wireless.ssid" in call[0]
                             for call in fake.calls if "NAME,TYPE" in call[0]))

    def test_saved_ssid_falls_back_to_connection_name(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless"),
                            ("Fallback", "802-11-wireless")]
        fake.ssids = {"Home": ""}
        fake.ssid_rc = {"Fallback": 1}
        self.assertEqual(nc.saved_ssids(), {"Home", "Fallback"})

    def test_net_json_fields_and_atomic_write(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(45)
        path = root / "run/microduck/net.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "hotspot")
        self.assertEqual(data["hotspot_ssid"], "Microduck-c51b")
        self.assertEqual(data["hotspot_ip"], "10.42.0.1")
        self.assertEqual(data["hotspot_psk"], "microduck")
        self.assertEqual(data["hostname"], "duck-c51b")
        self.assertEqual(data["since"], 45.0)
        self.assertEqual(data["scan_at"], 45.0)
        self.assertFalse(list((root / "run/microduck").glob("*.tmp*")))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE((root / "run/microduck").stat().st_mode), 0o755)

    def test_state_written_only_when_content_changes(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        first = nc.write_count
        nc.tick(1)
        self.assertEqual(nc.write_count, first)
        fake.device = ("wifi", "Home")
        nc.tick(2)
        self.assertEqual(nc.write_count, first + 1)

    def test_mode_change_logged_once(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(1)
        self.assertNotIn("配网状态", log.getvalue())
        fake.device = ("wifi", "Home")
        nc.tick(2)
        nc.tick(3)
        self.assertEqual(log.getvalue().count("none -> wifi"), 1)

    def test_scan_failure_still_tries_hotspot(self):
        root, fake, nc, log = self.make()
        fake.scan_rc = 1
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        self.assertIn("扫描 WiFi 失败", log.getvalue())
        state = json.loads((root / "run/microduck/net.json").read_text(encoding="utf-8"))
        self.assertEqual(state["scan"], [])
        self.assertIsNone(state["scan_at"])

    def test_unavailable_device_does_not_count_until_disconnected(self):
        root, fake, nc, log = self.make()
        fake.device = ("unavailable", "")
        nc.tick(0)
        nc.tick(100)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        fake.device = ("none", "")
        nc.tick(101)
        nc.tick(145)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(146)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_nmcli_error_does_not_count(self):
        root, fake, nc, log = self.make()
        fake.device_rc = 1
        nc.tick(0)
        nc.tick(100)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        fake.device_rc = 0
        nc.tick(101)
        nc.tick(145)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(146)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_device_disappearing_resets_the_counter(self):
        root, fake, nc, log = self.make()
        nc.tick(0)
        nc.tick(30)
        fake.device = ("unavailable", "")
        nc.tick(31)
        nc.tick(100)
        fake.device = ("none", "")
        nc.tick(101)
        nc.tick(145)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(146)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_idle_five_minutes_is_counted_from_last_client(self):
        root, fake, nc, log = self.make()
        fake.device = ("hotspot", "Microduck-Hotspot")
        fake.stations = "Station aa:bb:cc:dd:ee:ff (on wlan0)\n"
        nc.tick(0)
        nc.tick(100)
        fake.stations = ""
        nc.tick(101)
        nc.tick(399)
        self.assertEqual(fake.calls_of("connection", "down"), [])
        nc.tick(400)
        self.assertEqual(len(fake.calls_of("connection", "down")), 1)
        self.assertIn("最后看到客户端", log.getvalue())

    def test_studio_connecting_window_pauses_everything(self):
        root, fake, nc, log = self.make()
        path = root / "run/microduck/studio-connecting.json"
        path.write_text(json.dumps({"until": 100, "ssid": "Home"}), encoding="utf-8")
        os.utime(path, (0, 0))
        nc.tick(0)
        nc.tick(99)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(100)
        self.assertFalse(path.exists())
        nc.tick(144)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(145)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        self.assertIn("Studio 正在连 WiFi", log.getvalue())

    def test_studio_connecting_does_not_close_hotspot(self):
        root, fake, nc, log = self.make()
        fake.device = ("hotspot", "Microduck-Hotspot")
        nc.tick(0)
        path = root / "run/microduck/studio-connecting.json"
        path.write_text(json.dumps({"until": 1000, "ssid": "Home"}), encoding="utf-8")
        os.utime(path, (0, 0))
        nc.tick(1)
        os.utime(path, (300, 300))
        nc.tick(300)
        self.assertEqual(fake.calls_of("connection", "down"), [])
        self.assertEqual(nc.mode, "hotspot")

    def test_stale_studio_file_is_deleted_and_ignored(self):
        root, fake, nc, log = self.make(now=200.0)
        path = root / "run/microduck/studio-connecting.json"
        path.write_text(json.dumps({"until": 1000, "ssid": "Home"}), encoding="utf-8")
        os.utime(path, (0, 0))
        nc.tick(200)
        self.assertFalse(path.exists())
        nc.tick(245)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_missing_hotspot_connection_calls_firstboot_before_up(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless")]
        fake.hotspot_keyfile.unlink()
        nc.tick(0)
        nc.tick(45)
        calls = [call[0] for call in fake.calls]
        firstboot = next(i for i, args in enumerate(calls)
                         if args and args[0].endswith("microduck-firstboot"))
        up = next(i for i, args in enumerate(calls)
                  if args[:1] == ["nmcli"] and "connection" in args and "up" in args)
        self.assertLess(firstboot, up)
        self.assertIn("--hotspot-only", calls[firstboot])
        self.assertIn("--hotspot-only", log.getvalue())

    def test_unreachable_saved_wifi_retries_cannot_postpone_hotspot_forever(self):
        root, fake, nc, log = self.make()
        for t in range(0, 124, 3):
            fake.device = ("none", "") if t % 30 == 0 else ("connecting", "Home")
            nc.tick(t)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)
        self.assertEqual(nc.mode, "hotspot")

    def test_successful_wifi_connection_resets_the_offline_window(self):
        root, fake, nc, log = self.make()
        fake.device = ("connecting", "Home")
        nc.tick(0)
        nc.tick(60)
        fake.device = ("wifi", "Home")
        nc.tick(90)
        for t in range(120, 242, 3):
            fake.device = ("none", "") if t % 30 == 0 else ("connecting", "Home")
            nc.tick(t)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        fake.device = ("connecting", "Home")
        nc.tick(243)
        self.assertEqual(nc.mode, "hotspot")

    def test_web_connection_window_resets_previous_connecting_timeout(self):
        root, fake, nc, log = self.make()
        fake.device = ("connecting", "Home")
        nc.tick(0)
        path = root / "run/microduck/studio-connecting.json"
        path.write_text(json.dumps({"until": 160, "ssid": "NewHome"}), encoding="utf-8")
        os.utime(path, (70, 70))
        nc.tick(70)
        nc.tick(159)
        nc.tick(160)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(279)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        nc.tick(283)
        self.assertEqual(nc.mode, "hotspot")

    def test_disk_profile_is_loaded_without_overwriting_custom_hotspot_password(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless")]
        original = "[wifi]\nssid=Microduck-c51b\n[wifi-security]\npsk=owner-chosen-pass\n"
        fake.hotspot_keyfile.write_text(original, encoding="utf-8")
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(nc.mode, "hotspot")
        self.assertEqual(fake.hotspot_keyfile.read_text(encoding="utf-8"), original)
        self.assertFalse(any(args[0].endswith("microduck-firstboot") for args, _ in fake.calls))
        calls = [args for args, _ in fake.calls]
        load = next(i for i, args in enumerate(calls) if args[:3] == ["nmcli", "connection", "load"])
        up = next(i for i, args in enumerate(calls) if args[:3] == ["nmcli", "connection", "up"])
        self.assertLess(load, up)

    def test_failed_hotspot_regeneration_does_not_attempt_activation(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless")]
        fake.hotspot_keyfile.unlink()
        fake.firstboot_rc = 1
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        self.assertEqual(nc.mode, "none")
        calls = len(fake.calls)
        nc.tick(48)
        self.assertFalse(any(args[0].endswith("microduck-firstboot") for args, _ in fake.calls[calls:]))

    def test_failed_hotspot_load_keeps_credentials_and_obeys_retry_backoff(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless")]
        original = fake.hotspot_keyfile.read_bytes()
        fake.load_rc = 1
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        self.assertEqual(len(fake.calls_of("connection", "load")), 1)
        self.assertEqual(fake.hotspot_keyfile.read_bytes(), original)
        nc.tick(104)
        self.assertEqual(len(fake.calls_of("connection", "load")), 1)
        fake.load_rc = 0
        nc.tick(105)
        self.assertEqual(nc.mode, "hotspot")

    def test_load_success_without_registered_hotspot_is_not_reported_ready(self):
        root, fake, nc, log = self.make()
        fake.connections = [("Home", "802-11-wireless")]
        fake.load_registers = False
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(fake.calls_of("connection", "up"), [])
        self.assertEqual(nc.mode, "none")

    def test_activation_exit_zero_requires_hotspot_state_readback(self):
        root, fake, nc, log = self.make()
        fake.up_sets_hotspot = False
        nc.tick(0)
        nc.tick(45)
        self.assertEqual(nc.mode, "none")
        state = json.loads((root / "run/microduck/net.json").read_text(encoding="utf-8"))
        self.assertEqual(state["mode"], "none")
        nc.tick(104)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1)

    def test_unavailable_hotspot_discards_previous_connecting_timeout(self):
        root, fake, nc, log = self.make()
        for t in range(0, 124, 3):
            fake.device = ("none", "") if t % 30 == 0 else ("connecting", "Home")
            nc.tick(t)
        self.assertEqual(nc.mode, "hotspot")
        fake.device = ("unavailable", "")
        nc.tick(126)
        fake.device = ("connecting", "Home")
        nc.tick(300)
        self.assertEqual(len(fake.calls_of("connection", "up")), 1,
                         "A recovered adapter must receive a new connection window")


class HelperTests(unittest.TestCase):
    def test_classify_device(self):
        self.assertEqual(NC.classify_device(
            "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Microduck-Hotspot\n"), "hotspot")
        self.assertEqual(NC.classify_device(
            "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Home\n"), "wifi")
        for number in (40, 50, 60, 70, 80, 90):
            self.assertEqual(NC.classify_device(
                "GENERAL.STATE:%d (x)\nGENERAL.CONNECTION:Home\n" % number), "connecting")
        self.assertEqual(NC.classify_device(
            "GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n"), "none")
        self.assertEqual(NC.classify_device(
            "GENERAL.STATE:20 (unavailable)\nGENERAL.CONNECTION:--\n"), "unavailable")
        self.assertEqual(NC.classify_device("GENERAL.CONNECTION:--\n"), "unavailable")

    def test_terse_split_keeps_escaped_colon(self):
        self.assertEqual(NC.terse_split(r"a\:b:72:WPA2", 2), ["a:b", "72", "WPA2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
