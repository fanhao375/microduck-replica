"""开机自动用上摄像头和 ToF（studio/sensors_auto.py）：按「认没认到摄像头 × 配置现在是什么」决定让小助手做什么。

用假的文件树（sysfs 里的 imx219 设备、robotd.toml、systemd 的单元文件）和假的小助手跑。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio.log import Log  # noqa: E402
from studio.sensors_auto import OFF_NAME, SensorAuto, camera_present, media_source, tof_pending  # noqa: E402


class SensorAutoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.log = Log(Path(self.tmp.name) / "logs", echo=False, background=False)
        self.calls = []
        self.reply = (True, "sensors_applied", {"exit": 0, "result": "sensors_applied"})
        self.helper_there = True
        self.updating = False

    def tearDown(self):
        self.tmp.cleanup()

    def duck(self, source="test", camera=False, tofd=True, tofd_enabled=False):
        (self.root / "etc/robot").mkdir(parents=True, exist_ok=True)
        text = "[bus]\nport = \"/dev/ttyS2\"\n" + (f"\n[media]\nsource = \"{source}\"\n" if source else "")
        (self.root / "etc/robot/robotd.toml").write_text(text, encoding="utf-8")
        driver = self.root / "sys/bus/i2c/drivers/imx219"
        driver.mkdir(parents=True, exist_ok=True)
        (driver / "bind").write_text("", encoding="utf-8")            # 驱动在、没接摄像头时目录里只有这些
        if camera:
            (driver / "4-0010").mkdir(exist_ok=True)
        elif (driver / "4-0010").exists():
            (driver / "4-0010").rmdir()
        units = self.root / "etc/systemd/system"
        (units / "multi-user.target.wants").mkdir(parents=True, exist_ok=True)
        if tofd:
            (units / "tofd.service").write_text("[Unit]\n", encoding="utf-8")
        if tofd_enabled:
            (units / "multi-user.target.wants/tofd.service").write_text("", encoding="utf-8")

    def auto(self):
        def apply(by, camera, tof):
            self.calls.append((camera, tof))
            return self.reply
        return SensorAuto(self.log, self.state, root=self.root, apply=apply, available=lambda: self.helper_there,
                          busy=lambda: self.updating, settle=0.2)

    async def test_camera_found_switches_test_picture_to_real(self):
        self.duck(source="test", camera=True, tofd_enabled=True)
        last = await self.auto().run("开机")
        self.assertEqual(self.calls, [("camera", False)])
        self.assertEqual((last["state"], last["source"], last["camera_present"]), ("ok", "camera", True))
        self.assertIn("已切到真实画面", last["note"])

    async def test_no_camera_puts_real_back_to_test_picture(self):
        self.duck(source="camera", camera=False, tofd_enabled=True)
        last = await self.auto().run("开机")
        self.assertEqual(self.calls, [("test", False)])
        self.assertEqual((last["state"], last["source"]), ("ok", "test"))
        self.assertIn("没认到摄像头", last["note"])

    async def test_matching_setup_touches_nothing(self):
        for source, camera in (("camera", True), ("test", False)):
            with self.subTest(source=source):
                self.calls.clear()
                self.duck(source=source, camera=camera, tofd_enabled=True)
                last = await self.auto().run("开机")
                self.assertEqual(self.calls, [])
                self.assertEqual((last["state"], last["note"]), ("ok", None))

    async def test_tof_is_enabled_once_even_without_a_camera(self):
        self.duck(source="test", camera=False, tofd_enabled=False)
        await self.auto().run("开机")
        self.assertEqual(self.calls, [(None, True)])

    async def test_camera_and_tof_go_in_one_call(self):
        self.duck(source="test", camera=True, tofd_enabled=False)
        await self.auto().run("开机")
        self.assertEqual(self.calls, [("camera", True)])

    async def test_duck_without_tofd_installed_is_not_asked_to_enable_it(self):
        self.duck(source="test", camera=False, tofd=False)
        await self.auto().run("开机")
        self.assertEqual(self.calls, [])

    async def test_camera_that_is_slow_to_appear_is_still_found(self):
        self.duck(source="test", camera=False, tofd_enabled=True)
        auto = self.auto()
        auto.settle = 3.0
        task = asyncio.create_task(auto.run("开机"))
        await asyncio.sleep(0.7)
        (self.root / "sys/bus/i2c/drivers/imx219/4-0010").mkdir()
        await asyncio.wait_for(task, 5)
        self.assertEqual(self.calls, [("camera", False)])

    async def test_video_that_cannot_start_says_so_and_keeps_the_test_picture(self):
        self.duck(source="test", camera=True, tofd_enabled=True)
        self.reply = (False, "camera_unstable", {"exit": 5, "result": "camera_unstable", "camera": "reverted"})
        last = await self.auto().run("开机")
        self.assertEqual((last["state"], last["source"]), ("failed", "test"))
        self.assertIn("已退回测试画面", last["note"])

    async def test_unreadable_media_config_leaves_the_camera_alone(self):
        self.duck(source=None, camera=True, tofd_enabled=True)
        last = await self.auto().run("开机")
        self.assertEqual(self.calls, [])
        self.assertEqual(last["state"], "ok")

    async def test_off_file_disables_everything(self):
        self.duck(source="test", camera=True)
        (self.state / OFF_NAME).write_text("", encoding="utf-8")
        last = await self.auto().run("开机")
        self.assertEqual((self.calls, last["state"]), ([], "off"))

    async def test_without_the_helper_or_during_an_update_nothing_is_changed(self):
        self.duck(source="test", camera=True)
        self.helper_there = False
        self.assertEqual((await self.auto().run("开机"))["state"], "unavailable")
        self.helper_there, self.updating = True, True
        self.assertEqual((await self.auto().run("开机"))["state"], "busy")
        self.assertEqual(self.calls, [])

    def test_readers(self):
        self.duck(source="camera", camera=True, tofd_enabled=False)
        self.assertTrue(camera_present(self.root))
        self.assertEqual(media_source(self.root), "camera")
        self.assertTrue(tof_pending(self.root))
        self.assertFalse(camera_present(Path(self.tmp.name) / "nowhere"))
        self.assertIsNone(media_source(Path(self.tmp.name) / "nowhere"))
        self.assertFalse(tof_pending(Path(self.tmp.name) / "nowhere"))


if __name__ == "__main__":
    unittest.main()
