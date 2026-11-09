#!/usr/bin/env python3
"""microduck-firstboot：起名、热点 keyfile、IMU 识别、TOML 追加。"""
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import BASE, SH, FakeProject, require_sh, run_shell, posix_path  # noqa: E402

SCRIPT = BASE / "overlay/usr/local/sbin/microduck-firstboot"


def load_program():
    loader = importlib.machinery.SourceFileLoader("microduck_firstboot", str(SCRIPT))
    spec = importlib.util.spec_from_loader("microduck_firstboot", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


FB = load_program()


class Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append((list(args), timeout))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


class TlsRun(Run):
    """假 openssl：按 -out 参数造占位文件；调用 x509 时顺手记下 extfile 内容。"""

    def __init__(self, returncode=0, stderr=""):
        super().__init__(returncode=returncode, stderr=stderr)
        self.ext_text = ""

    def __call__(self, args, timeout=None):
        self.calls.append((list(args), timeout))
        a = list(args)
        if self.returncode == 0:
            if "-extfile" in a:
                try:
                    self.ext_text = Path(a[a.index("-extfile") + 1]).read_text(encoding="utf-8")
                except OSError:
                    pass
            if "-out" in a:
                out = Path(a[a.index("-out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("placeholder\n", encoding="utf-8")
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr=self.stderr)


def make_tree(tmp, *, serial=b"bb7b734a7717ac41\x00\n", hostname="microduck"):
    root = Path(tmp)
    for rel in ["etc/NetworkManager/system-connections", "etc/robot/feetech-ft5",
                "var/lib/microduck", "dev", "proc/device-tree",
                "sys/class/net/wlan0", "var/log"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    if serial is not None:
        (root / "proc/device-tree/serial-number").write_bytes(serial)
    (root / "etc/hostname").write_text(hostname + "\n", encoding="utf-8")
    (root / "etc/hosts").write_text(
        "127.0.0.1 localhost\n"
        "127.0.1.1 %s\n"
        "::1 localhost ip6-localhost ip6-loopback %s\n"
        "10.0.0.1 other %s\n" % (hostname, hostname, hostname),
        encoding="utf-8")
    (root / "etc/robot/feetech-ft5/robotd.toml").write_text(
        '["bus"]\n"port" = "/dev/ttyS2"\n', encoding="utf-8")
    (root / "etc/robot/feetech-ft5/robotd.saved.toml").write_text(
        '["bus"]\n"port" = "/dev/ttyS2"\n', encoding="utf-8")
    (root / "dev/i2c-3").write_bytes(b"")
    (root / "dev/i2c-4").write_bytes(b"")
    return root


def make_fb(root, *, fake_spec=None, run=None, now=1790000000.0, log=None):
    fake_bus = None
    if fake_spec is not None:
        fake_bus = Path(root) / "i2c-fake.json"
        fake_bus.write_text(json.dumps(fake_spec), encoding="utf-8")
    return FB.Firstboot(root=root, fake_bus=fake_bus, run_cmd=run,
                        now=lambda: now, log_stream=log or io.StringIO())


class HostnameTests(unittest.TestCase):
    def test_official_example(self):
        self.assertEqual(FB.hostname_from_seed(b"bb7b734a7717ac41"), "duck-c51b")
        self.assertEqual(FB.duck_name(b"bb7b734a7717ac41"), "duck-c51b")

    def test_serial_strips_nul_and_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp, serial=b"  bb7b734a7717ac41\x00 \n")
            fb = make_fb(root)
            self.assertEqual(fb.read_serial(), b"bb7b734a7717ac41")
            name, source = fb.pick_name()
            self.assertEqual(name, "duck-c51b")
            self.assertIn("序列号", source)

    def test_empty_or_nonprintable_serial_falls_back_to_mac(self):
        for serial in (b"", b"\x00\n", b"abc\x01def"):
            with self.subTest(serial=serial), tempfile.TemporaryDirectory() as tmp:
                root = make_tree(tmp, serial=serial)
                (root / "sys/class/net/wlan0/address").write_text("aa:bb:cc:dd:ee:ff\n")
                fb = make_fb(root)
                self.assertIsNone(fb.read_serial())
                name, source = fb.pick_name()
                self.assertEqual(name, FB.hostname_from_seed(b"aa:bb:cc:dd:ee:ff"))
                self.assertIn("MAC", source)

    def test_no_serial_or_mac_uses_random_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp, serial=None)
            fb = FB.Firstboot(root=root, random_bytes=lambda n: b"\x12\x34",
                              log_stream=io.StringIO())
            name, source = fb.pick_name()
            self.assertEqual(name, "duck-1234")
            self.assertIn("随机", source)

    def test_hosts_only_changes_two_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            fb = make_fb(root)
            fb.rewrite_hosts("microduck", "duck-c51b")
            lines = (root / "etc/hosts").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "127.0.0.1 localhost")
            self.assertEqual(lines[1], "127.0.1.1 duck-c51b")
            self.assertEqual(lines[2], "::1 localhost ip6-localhost ip6-loopback duck-c51b")
            self.assertEqual(lines[3], "10.0.0.1 other microduck")

    def test_setup_hostname_writes_and_calls_hostname(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            run = Run()
            fb = make_fb(root, run=run)
            # 临时 root 下默认不调用 hostname；显式注入 runner 后才调用。
            fb.setup_hostname()
            self.assertEqual((root / "etc/hostname").read_text(encoding="utf-8"), "duck-c51b\n")
            self.assertEqual(run.calls, [(["hostname", "duck-c51b"], 5)])


class HotspotTests(unittest.TestCase):
    def test_content_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            fb = make_fb(root)
            target = fb.setup_hotspot("duck-c51b")
            text = target.read_text(encoding="utf-8")
            self.assertIn("[connection]\nid=Microduck-Hotspot\nuuid=", text)
            self.assertIn("interface-name=wlan0\nautoconnect=false\n", text)
            self.assertIn("[wifi]\nmode=ap\nssid=Microduck-c51b\nband=bg\nchannel=6\n", text)
            self.assertIn("key-mgmt=wpa-psk\nproto=rsn\npairwise=ccmp\ngroup=ccmp\npmf=1\npsk=microduck\n", text)
            self.assertIn("[ipv4]\nmethod=shared\naddress1=10.42.0.1/24\n", text)
            self.assertIn("[ipv6]\nmethod=disabled\n", text)
            uuid_line = [line for line in text.splitlines() if line.startswith("uuid=")][0]
            self.assertRegex(uuid_line, r"^uuid=[0-9a-f-]{36}$")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_existing_file_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            target = root / "etc/NetworkManager/system-connections/Microduck-Hotspot.nmconnection"
            target.write_text("sentinel\n", encoding="utf-8")
            fb = make_fb(root)
            fb.setup_hotspot("duck-c51b")
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")


class ImuTests(unittest.TestCase):
    C1 = {"0x19": {"0x00": "0x1e"}, "0x68": {"0x00": "0x0f"}}
    L6A = {"0x6a": {"0x0f": "0x70"}}
    L6B = {"0x6b": {"0x0f": "0x70"}}
    BNO4A = {"raw": {"0x4a": "14010000"}}
    BNO4B = {"raw": {"0x4b": "14010000"}}

    def _detect(self, spec, *, force=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_tree(tmp.name)
        log = io.StringIO()
        fb = make_fb(root, fake_spec=spec, log=log)
        chosen = fb.detect_imu(force=force)
        toml = (root / "etc/robot/feetech-ft5/robotd.toml").read_text(encoding="utf-8")
        saved = (root / "etc/robot/feetech-ft5/robotd.saved.toml").read_text(encoding="utf-8")
        detect = json.loads((root / "var/lib/microduck/imu-detect.json").read_text(encoding="utf-8"))
        return root, fb, chosen, toml, saved, detect, log.getvalue()

    def assert_not_written(self, toml, saved):
        for text in (toml, saved):
            self.assertNotIn("trunk_imu", text)

    def test_c1_plus_6a_module_is_one_and_written(self):
        _, _, chosen, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.C1, **self.L6A)}, "/dev/i2c-4": {}})
        self.assertEqual(detect["verdict"], "one")
        self.assertTrue(detect["written"])
        self.assertEqual(detect["candidates"],
                         [{"source": "lsm6dsv16x", "device": "/dev/i2c-3", "address": 0x6A}])
        for text in (toml, saved):
            self.assertEqual(tomllib.loads(text)["trunk_imu"], {
                "source": "lsm6dsv16x", "i2c_device": "/dev/i2c-3",
                "i2c_address": 0x6A})
        self.assertEqual(chosen["address"], 0x6A)

    def test_c1_plus_6b_module_is_one_and_written(self):
        _, _, chosen, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}, "/dev/i2c-4": {}})
        self.assertEqual(detect["verdict"], "one")
        self.assertTrue(detect["written"])
        self.assertEqual(chosen["address"], 0x6B)
        for text in (toml, saved):
            self.assertEqual(tomllib.loads(text)["trunk_imu"]["i2c_address"], 0x6B)

    def test_d1_head_6a_plus_module_6b_writes_6b(self):
        _, _, chosen, toml, saved, detect, log = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.L6A, **self.L6B)}, "/dev/i2c-4": {}})
        self.assertEqual(detect["verdict"], "one")
        self.assertTrue(detect["written"])
        self.assertEqual(chosen["address"], 0x6B)
        for text in (toml, saved):
            self.assertEqual(tomllib.loads(text)["trunk_imu"]["i2c_address"], 0x6B)
        self.assertIn("0x6a 当作 D1 版 HAT 板载的头部 IMU 跳过", log)

    def test_d1_only_6a_is_none_with_hint(self):
        _, _, chosen, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.L6A)}, "/dev/i2c-4": {}})
        self.assertIsNone(chosen)
        self.assertEqual(detect["verdict"], "none")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("躯干模块必须设成 0x6B" in hint for hint in detect["hints"]))

    def test_6b_alone_without_c1_is_unsure(self):
        _, _, chosen, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.L6B)}, "/dev/i2c-4": {}})
        self.assertIsNone(chosen)
        self.assertEqual(detect["verdict"], "unsure")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("分不清它是躯干模块还是头部 IMU" in why
                            for why in detect["unsure"]))
        self.assertTrue(any("J5 上只有 0x6B" in hint for hint in detect["hints"]))

    def test_bno08x_on_i2c4_is_one_and_written(self):
        _, _, chosen, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-4": dict(self.BNO4B)})
        self.assertEqual(detect["verdict"], "one")
        self.assertTrue(detect["written"])
        self.assertEqual(chosen["address"], 0x4B)
        for text in (toml, saved):
            data = tomllib.loads(text)["trunk_imu"]
            self.assertEqual(data["source"], "bno08x")
            self.assertEqual(data["bno08x_fusion"], "gyro")
            self.assertEqual(data["i2c_device"], "/dev/i2c-4")
            self.assertEqual(data["i2c_address"], 0x4B)

    def test_bno_and_lsm_are_many_and_not_written(self):
        _, _, chosen, toml, saved, detect, _ = self._detect({
            "/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)},
            "/dev/i2c-4": dict(self.BNO4B),
        })
        self.assertIsNone(chosen)
        self.assertEqual(detect["verdict"], "many")
        self.assertFalse(detect["written"])
        self.assertEqual(len(detect["candidates"]), 2)
        self.assert_not_written(toml, saved)

    def test_i2c_error_is_unsure_not_absent(self):
        _, _, _, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-4": {"errors": {"0x4b": "EIO"}}})
        self.assertEqual(detect["verdict"], "unsure")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("读出错：出错（EIO）" in why for why in detect["unsure"]))

    def test_two_reads_differ_is_unsure(self):
        _, _, _, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-4": {"raw": {"0x4b": ["14010000", "14000900"]}}})
        self.assertEqual(detect["verdict"], "unsure")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("两次读得不一样" in why for why in detect["unsure"]))

    def test_j5_6a_ebusy_has_hint(self):
        _, _, _, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.L6B), "busy": ["0x6a"]}})
        self.assertEqual(detect["verdict"], "unsure")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("可能是给板载 IMU 绑的驱动" in hint for hint in detect["hints"]))

    def test_held_device_node_is_unsure(self):
        _, _, _, toml, saved, detect, _ = self._detect({
            "/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)},
            "/dev/i2c-4": {"held": {"by": "robotd"}},
        })
        self.assertEqual(detect["verdict"], "unsure")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("/dev/i2c-4 被 robotd 占用" in why for why in detect["unsure"]))

    def test_both_buses_missing_is_none_with_hint(self):
        _, _, _, toml, saved, detect, _ = self._detect({})
        self.assertEqual(detect["verdict"], "none")
        self.assertFalse(detect["written"])
        self.assert_not_written(toml, saved)
        self.assertTrue(any("都不存在" in hint for hint in detect["hints"]))

    def test_json_schema_and_per_address_log(self):
        _, _, _, _, _, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}, "/dev/i2c-4": {}})
        self.assertEqual(set(detect), {"at", "verdict", "candidates", "unsure",
                                       "hints", "log", "written"})
        self.assertEqual(detect["at"], 1790000000.0)
        self.assertTrue(any("0x6b WHO_AM_I：0x70 / 0x70" in line
                            for line in detect["log"]))
        for candidate in detect["candidates"]:
            self.assertEqual(set(candidate), {"source", "device", "address"})

    def test_both_toml_files_written(self):
        _, _, _, toml, saved, detect, _ = self._detect(
            {"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}})
        self.assertTrue(detect["written"])
        for text in (toml, saved):
            self.assertIn('["trunk_imu"]', text)
            self.assertEqual(tomllib.loads(text)["trunk_imu"]["i2c_address"], 107)

    def test_existing_trunk_imu_is_untouched(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_tree(tmp.name)
        target = root / "etc/robot/feetech-ft5/robotd.toml"
        target.write_text('["bus"]\n"port" = "/dev/ttyS2"\n\n["trunk_imu"]\n"source" = "old"\n',
                          encoding="utf-8")
        log = io.StringIO()
        fb = make_fb(root, fake_spec={"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}},
                     log=log)
        fb.detect_imu()
        text = target.read_text(encoding="utf-8")
        self.assertIn('"source" = "old"', text)
        self.assertNotIn("lsm6dsv16x", text)
        self.assertIn("已有 trunk_imu 段", log.getvalue())

    def test_force_replaces_existing_trunk_imu(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_tree(tmp.name)
        target = root / "etc/robot/feetech-ft5/robotd.toml"
        target.write_text('["bus"]\n"port" = "/dev/ttyS2"\n\n["trunk_imu"]\n"source" = "old"\n',
                          encoding="utf-8")
        fb = make_fb(root, fake_spec={"/dev/i2c-4": dict(self.BNO4A)})
        fb.detect_imu(force=True)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn('"source" = "old"', text)
        self.assertEqual(text.count('["trunk_imu"]'), 1)
        data = tomllib.loads(text)
        self.assertEqual(data["bus"]["port"], "/dev/ttyS2")
        self.assertEqual(data["trunk_imu"]["source"], "bno08x")

    def test_validation_failure_restores_original(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_tree(tmp.name)
        target = root / "etc/robot/feetech-ft5/robotd.toml"
        original = target.read_bytes()
        fb = make_fb(root, fake_spec={"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}})
        with mock.patch.object(FB.tomllib, "loads", side_effect=ValueError("broken")):
            with self.assertRaises(RuntimeError):
                fb.detect_imu()
        self.assertEqual(target.read_bytes(), original)

    def test_mode_and_owner_not_required_on_windows(self):
        if os.name == "nt":
            self.skipTest("Windows 不保留 Unix 权限")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = make_tree(tmp.name)
        target = root / "etc/robot/feetech-ft5/robotd.toml"
        os.chmod(target, 0o640)
        fb = make_fb(root, fake_spec={"/dev/i2c-3": {"regs": dict(self.C1, **self.L6B)}})
        fb.detect_imu()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)


class FlowTests(unittest.TestCase):
    def test_failure_in_hotspot_step_does_not_stop_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            # 把 NetworkManager 变成普通文件，热点 keyfile 必然写不进去。
            nm = root / "etc/NetworkManager"
            shutil.rmtree(nm)
            nm.write_text("not a directory\n", encoding="utf-8")
            log = io.StringIO()
            fb = make_fb(root, fake_spec={"/dev/i2c-4": dict(ImuTests.BNO4A)}, run=Run(), log=log)
            rc = fb.run()
            self.assertEqual(rc, 0)
            self.assertEqual((root / "etc/hostname").read_text(encoding="utf-8"), "duck-c51b\n")
            self.assertIn('"trunk_imu"', (root / "etc/robot/feetech-ft5/robotd.toml").read_text(encoding="utf-8"))
            self.assertFalse((root / "var/lib/microduck/firstboot.done").exists())
            self.assertIn("热点配置失败", log.getvalue())
            self.assertIn("不写完成标记", log.getvalue())

    def test_marker_written_after_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            fb = make_fb(root, fake_spec={}, run=Run())
            fb.run()
            text = (root / "var/lib/microduck/firstboot.done").read_text(encoding="utf-8")
            self.assertRegex(text, r"^1790000000\.000000 duck-c51b\n$")
            log = (root / "var/log/microduck-firstboot.log").read_text(encoding="utf-8")
            self.assertIn("起名", log)

    def test_detect_only_leaves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            target = root / "etc/robot/feetech-ft5/robotd.toml"
            original = '["trunk_imu"]\n"source" = "old"\n'
            target.write_text(original, encoding="utf-8")
            log = io.StringIO()
            fb = make_fb(root, fake_spec={"/dev/i2c-4": dict(ImuTests.BNO4A)}, log=log)
            self.assertEqual(fb.run(detect_only=True), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertIn("已有 trunk_imu 段", log.getvalue())
            self.assertFalse((root / "var/lib/microduck/firstboot.done").exists())

    def test_detect_only_force_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp)
            target = root / "etc/robot/feetech-ft5/robotd.toml"
            target.write_text('["trunk_imu"]\n"source" = "old"\n', encoding="utf-8")
            fb = make_fb(root, fake_spec={"/dev/i2c-4": dict(ImuTests.BNO4A)})
            self.assertEqual(fb.run(detect_only=True, force=True), 0)
            self.assertEqual(tomllib.loads(target.read_text(encoding="utf-8"))["trunk_imu"]["source"],
                             "bno08x")


class HotspotOnlyTests(unittest.TestCase):
    def test_only_rebuilds_hotspot_without_name_imu_or_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp, hostname="duck-c51b")
            hostname_before = (root / "etc/hostname").read_bytes()
            toml_before = (root / "etc/robot/feetech-ft5/robotd.toml").read_bytes()
            log = io.StringIO()
            fb = make_fb(root, log=log)
            self.assertEqual(fb.run(hotspot_only=True), 0)
            self.assertEqual((root / "etc/hostname").read_bytes(), hostname_before)
            self.assertEqual((root / "etc/robot/feetech-ft5/robotd.toml").read_bytes(), toml_before)
            self.assertFalse((root / "var/lib/microduck/firstboot.done").exists())
            hotspot = root / "etc/NetworkManager/system-connections/Microduck-Hotspot.nmconnection"
            self.assertIn("ssid=Microduck-c51b", hotspot.read_text(encoding="utf-8"))
            self.assertIn("只重建热点连接", log.getvalue())

    def test_force_rewrites_existing_hotspot_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_tree(tmp, hostname="duck-c51b")
            hotspot = root / "etc/NetworkManager/system-connections/Microduck-Hotspot.nmconnection"
            hotspot.write_text("broken\n", encoding="utf-8")
            fb = make_fb(root)
            self.assertEqual(fb.run(hotspot_only=True), 0)
            text = hotspot.read_text(encoding="utf-8")
            self.assertIn("ssid=Microduck-c51b", text)
            self.assertNotIn("broken", text)


class TlsTests(unittest.TestCase):
    """首次开机生成局部 CA + 服务器证书；旧的自签 leaf 迁移时先备份。"""

    def tls(self, tmp, *, run=None):
        root = make_tree(tmp, hostname="duck-c51b")
        log = io.StringIO()
        fb = make_fb(root, fake_spec={}, run=run or TlsRun(), log=log)
        return root, fb, log

    def test_generates_local_ca_and_server_cert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, log = self.tls(tmp)
            cert = fb.setup_tls("duck-c51b")
            directory = root / "etc/microduck/tls"
            self.assertEqual(cert, directory / "duck.crt")
            for name in ("ca.key", "ca.crt", "duck.key", "duck.crt"):
                self.assertTrue((directory / name).exists(), name)
            self.assertFalse((directory / "duck.csr").exists())      # 中间文件不留下
            self.assertFalse((directory / "duck.ext").exists())
            self.assertIn("已生成 HTTPS 证书", log.getvalue())
            run = fb.run_cmd
            verbs = [a[1] for a, _ in run.calls if a and a[0] == "openssl"]
            self.assertEqual(verbs.count("genrsa"), 2)
            self.assertIn("req", verbs)
            self.assertIn("x509", verbs)

    def test_san_covers_hotspot_hostname_and_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fb, _ = self.tls(tmp)
            fb.setup_tls("duck-c51b")
            ext = fb.run_cmd.ext_text
            for want in ("DNS:duck-c51b", "DNS:duck-c51b.local", "DNS:localhost",
                         "IP:10.42.0.1", "IP:127.0.0.1",
                         "extendedKeyUsage=serverAuth"):
                self.assertIn(want, ext)
            self.assertIn("basicConstraints=CA:FALSE", ext)

    def test_ca_subject_is_the_local_ca(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fb, _ = self.tls(tmp)
            fb.setup_tls("duck-c51b")
            argv = [a for a, _ in fb.run_cmd.calls if a and a[1] == "req"
                    and "-x509" in a][0]
            self.assertIn(fb.TLS_SUBJECT, argv)

    def test_existing_certs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fb, log = self.tls(tmp)
            fb.setup_tls("duck-c51b")
            run = fb.run_cmd
            before = len(run.calls)
            fb.setup_tls("duck-c51b")
            self.assertEqual(len(run.calls), before)               # 没有再次调用 openssl
            self.assertIn("已存在，不覆盖", log.getvalue())

    def test_old_leaf_without_ca_is_backed_up_and_regenerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, log = self.tls(tmp)
            directory = root / "etc/microduck/tls"
            directory.mkdir(parents=True)
            (directory / "duck.key").write_text("old-key\n", encoding="utf-8")
            (directory / "duck.crt").write_text("old-crt\n", encoding="utf-8")
            fb.setup_tls("duck-c51b")
            self.assertTrue((directory / "ca.crt").exists())
            backups = sorted(p.name for p in directory.glob("*.bak-*"))
            self.assertTrue(any(name.startswith("duck.key.bak-") for name in backups), backups)
            self.assertTrue(any(name.startswith("duck.crt.bak-") for name in backups), backups)
            self.assertIn("备份旧证书", log.getvalue())

    def test_force_backs_up_even_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, _ = self.tls(tmp)
            fb.setup_tls("duck-c51b")
            fb.setup_tls("duck-c51b", force=True)
            directory = root / "etc/microduck/tls"
            backups = [p.name for p in directory.glob("*.bak-*")]
            self.assertTrue(any(name.startswith("ca.crt.bak-") for name in backups), backups)
            self.assertTrue(any(name.startswith("duck.crt.bak-") for name in backups), backups)

    def test_openssl_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, _ = self.tls(tmp, run=TlsRun(returncode=2, stderr="bad key\n"))
            with self.assertRaises(RuntimeError) as ctx:
                fb.setup_tls("duck-c51b")
            self.assertIn("openssl", str(ctx.exception))
            self.assertIn("生成 CA 私钥", str(ctx.exception))

    def test_key_modes_on_unix(self):
        if os.name == "nt":
            self.skipTest("Windows 不保留 Unix 权限")
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, _ = self.tls(tmp)
            fb.setup_tls("duck-c51b")
            directory = root / "etc/microduck/tls"
            for name, mode in (("ca.key", 0o600), ("duck.key", 0o600),
                               ("ca.crt", 0o644), ("duck.crt", 0o644)):
                self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), mode, name)

    def test_tls_only_run_generates_without_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, log = self.tls(tmp)
            self.assertEqual(fb.run(tls_only=True), 0)
            directory = root / "etc/microduck/tls"
            self.assertTrue((directory / "duck.crt").exists())
            self.assertFalse((root / "var/lib/microduck/firstboot.done").exists())
            self.assertIn("只生成 HTTPS 证书", log.getvalue())

    def test_tls_only_failure_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, fb, log = self.tls(tmp, run=TlsRun(returncode=2, stderr="nope\n"))
            self.assertEqual(fb.run(tls_only=True), 1)
            self.assertIn("HTTPS 证书没有生成", log.getvalue())

    def test_full_run_generates_tls_before_writing_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, fb, _ = self.tls(tmp)
            self.assertEqual(fb.run(), 0)
            self.assertTrue((root / "etc/microduck/tls/duck.crt").exists())
            self.assertTrue((root / "var/lib/microduck/firstboot.done").exists())


class PythonFinderTests(unittest.TestCase):
    """三个 shell 脚本都要跳过 3.10，找到带 tomllib 的解释器。"""

    def _extract(self, script):
        text = (BASE / script).read_text(encoding="utf-8")
        match = re.search(r"find_python\(\) \{.*?\n\}", text, re.S)
        self.assertIsNotNone(match, script)
        return match.group(0)

    def _run_finder(self, function_text, bin_dir):
        require_sh()
        script = 'PATH="%s"\n%s\nfind_python\n' % (posix_path(bin_dir), function_text)
        return run_shell(["-c", script])

    def _write_fake(self, directory, name, ok):
        path = Path(directory) / name
        path.write_text("#!/bin/sh\nexit %d\n" % (0 if ok else 1), encoding="utf-8")
        os.chmod(path, 0o755)

    def test_all_three_scripts_skip_python310(self):
        for script in ["apply-rootfs.sh", "capture-payload.sh", "build-image.sh"]:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as tmp:
                bindir = Path(tmp) / "bin"
                bindir.mkdir()
                self._write_fake(bindir, "python3", ok=False)
                self._write_fake(bindir, "python3.11", ok=True)
                result = self._run_finder(self._extract(script), bindir)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), "python3.11", script)

    def test_python3_is_used_when_it_has_tomllib(self):
        for script in ["apply-rootfs.sh", "capture-payload.sh", "build-image.sh"]:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as tmp:
                bindir = Path(tmp) / "bin"
                bindir.mkdir()
                self._write_fake(bindir, "python3", ok=True)
                self._write_fake(bindir, "python3.11", ok=True)
                result = self._run_finder(self._extract(script), bindir)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), "python3", script)


@unittest.skipUnless(SH, "需要 sh 才能跑 apply-rootfs.sh")
class ApplyOverlayTests(unittest.TestCase):
    def test_new_overlay_files_are_enabled_and_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = FakeProject(tmp)
            payload = fx.make_payload()
            root = fx.make_rootfs()
            studio = fx.make_studio()
            overlay = fx.make_overlay()
            enable = (overlay / "enable.list").read_text(encoding="utf-8")
            enable += "microduck-firstboot.service\nmicroduck-netcheck.service\n"
            (overlay / "enable.list").write_text(enable, encoding="utf-8", newline="\n")
            modes = (overlay / "modes.list").read_text(encoding="utf-8")
            modes += ("usr/local/sbin/microduck-firstboot 0755\n"
                      "usr/local/sbin/microduck-netcheck 0755\n")
            (overlay / "modes.list").write_text(modes, encoding="utf-8", newline="\n")
            result = fx.run_apply(root, payload, studio, overlay)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            wants = root / "etc/systemd/system/multi-user.target.wants"
            self.assertTrue(os.path.lexists(wants / "microduck-firstboot.service"))
            self.assertTrue(os.path.lexists(wants / "microduck-netcheck.service"))
            if os.name != "nt":
                for rel in ["usr/local/sbin/microduck-firstboot",
                            "usr/local/sbin/microduck-netcheck"]:
                    self.assertEqual(stat.S_IMODE((root / rel).stat().st_mode), 0o755, rel)

            configd = (root / "etc/systemd/system/configd.service.d/zzzz-replica-studio.conf").read_text(encoding="utf-8")
            self.assertIn("ExecStart=\n", configd)
            self.assertIn("--allow-user btd,radxa", configd)
            self.assertIn("主组", configd)
            self.assertTrue((root / "etc/systemd/resolved.conf.d/10-microduck-mdns.conf").exists())
            self.assertTrue((root / "etc/NetworkManager/conf.d/90-microduck-mdns.conf").exists())

            manifest = (root / "usr/share/doc/microduck/镜像清单.txt").read_text(encoding="utf-8")
            for rel in ["usr/local/sbin/microduck-firstboot",
                        "usr/local/sbin/microduck-netcheck",
                        "etc/systemd/system/microduck-firstboot.service",
                        "etc/systemd/system/microduck-netcheck.service",
                        "etc/systemd/resolved.conf.d/10-microduck-mdns.conf",
                        "etc/NetworkManager/conf.d/90-microduck-mdns.conf",
                        "etc/systemd/system/configd.service.d/zzzz-replica-studio.conf"]:
                self.assertIn(rel, manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
