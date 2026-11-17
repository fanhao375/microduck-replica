"""root 小助手的 sensors-apply：摄像头视频来源切真实画面 / 测试画面、ToF 设成开机启动。

用户 11-17：接了摄像头和 ToF 就自动用，不用 SSH 改配置。要核对的：只换 [media] 里 source 那一行；
切成真实画面以后视频服务起不来就整份退回去（网页上的视频和遥控不能跟着没了）；参数只认固定的几个值。

    py -3.13 -I -m unittest tests.test_helper_sensors
"""
import json
import shutil
import tempfile
import unittest

from tests.test_helper_bus import H, FakeCtx, World

CONFIG = """# 官方配置
[bus]
port = "/dev/ttyS2"

[media]
source = "test"   # 复刻镜像默认：没接摄像头也能起
width = 1280

[audio]
source = "board"
"""


class SensorsWorld(World):
    """在 World 上加 mediad / tofd：camera_works=False 时真实画面起不来，每 5 秒换一个进程号。"""

    def __init__(self, root):
        super().__init__(root)
        self.camera_works = True
        self.restart_rc, self.enable_rc = 0, 0
        self.restarts = 0
        self.tofd_enabled = False
        self.path(H.MEDIA_CONFIG).parent.mkdir(parents=True, exist_ok=True)
        self.path(H.MEDIA_CONFIG).write_bytes(CONFIG.encode("utf-8"))

    def source(self):
        import tomllib
        return tomllib.loads(self.path(H.MEDIA_CONFIG).read_text(encoding="utf-8"))["media"]["source"]

    def show(self, unit, props):
        if unit != "mediad":
            return super().show(unit, props)
        broken = self.source() == "camera" and not self.camera_works
        vals = {"ActiveState": "activating" if broken else "active",
                "MainPID": str(int(self.clock.t)) if broken else str(1000 + self.restarts)}
        return "".join(f"{p}={vals.get(p, '')}\n" for p in props)

    def run(self, argv):
        name, args = argv[0], argv[1:]
        if name == "systemctl" and args == ["restart", "mediad"]:
            self.calls.append(list(argv))
            self.restarts += 1
            return self.restart_rc, "", "Job for mediad.service failed" if self.restart_rc else ""
        if name == "systemctl" and args == ["enable", "--now", "tofd.service"]:
            self.calls.append(list(argv))
            self.tofd_enabled = not self.enable_rc
            return self.enable_rc, "", "Failed to enable unit: Unit file tofd.service does not exist." if self.enable_rc else ""
        return super().run(argv)


class SensorsApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helper-sensors-")
        self.w = SensorsWorld(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, args):
        ctx = FakeCtx(self.w, json.dumps(args).encode())
        code = H.run(["microduck-helper", "sensors-apply"], ctx)
        lines = [json.loads(x) for x in ctx.stdout.buf.getvalue().decode("utf-8").splitlines() if x.strip()]
        done = dict(lines[-1]["detail"])
        done.pop("exit")
        return code, done

    def text(self):
        return self.w.path(H.MEDIA_CONFIG).read_text(encoding="utf-8")

    def test_camera_switches_only_the_media_source_line(self):
        code, last = self.call({"by": "测试", "camera": "camera"})
        self.assertEqual((code, last["result"]), (H.EXIT_OK, "sensors_applied"))
        self.assertEqual(last["camera"], "camera")
        self.assertEqual(self.text(), CONFIG.replace('source = "test"   # 复刻镜像默认：没接摄像头也能起', 'source = "camera"'))
        self.assertEqual(self.w.restarts, 1)

    def test_already_on_the_wanted_source_does_not_restart_video(self):
        code, last = self.call({"by": "测试", "camera": "test"})
        self.assertEqual((code, last["camera"]), (H.EXIT_OK, "unchanged"))
        self.assertEqual((self.text(), self.w.restarts), (CONFIG, 0))

    def test_video_that_cannot_start_on_the_camera_goes_back_to_the_test_picture(self):
        self.w.camera_works = False
        code, last = self.call({"by": "测试", "camera": "camera"})
        self.assertEqual((code, last["result"]), (H.EXIT_FAIL, "camera_unstable"))
        self.assertEqual(last["camera"], "reverted")
        self.assertEqual(self.text(), CONFIG)                     # 整份原样写回
        self.assertEqual(self.w.restarts, 2)                      # 切过去一次、退回来一次

    def test_restart_failure_also_goes_back(self):
        self.w.restart_rc = 1
        code, last = self.call({"by": "测试", "camera": "camera"})
        self.assertEqual((code, last["result"]), (H.EXIT_FAIL, "restart_failed"))
        self.assertEqual(self.text(), CONFIG)

    def test_back_to_test_picture_needs_no_stability_wait(self):
        self.w.path(H.MEDIA_CONFIG).write_text(CONFIG.replace('"test"', '"camera"'), encoding="utf-8")
        started = self.w.clock.t
        code, last = self.call({"by": "测试", "camera": "test"})
        self.assertEqual((code, last["camera"], self.w.source()), (H.EXIT_OK, "test", "test"))
        self.assertLess(self.w.clock.t - started, H.MEDIA_SETTLE_S)

    def test_commented_out_official_default_is_replaced(self):
        self.w.path(H.MEDIA_CONFIG).write_text(CONFIG.replace('source = "test"   # 复刻镜像默认：没接摄像头也能起',
                                                              '# source = "camera"'), encoding="utf-8")
        code, _ = self.call({"by": "测试", "camera": "test"})
        self.assertEqual((code, self.w.source()), (H.EXIT_OK, "test"))
        self.assertEqual(self.text().count("source ="), 2)       # [audio] 那一行没动

    def test_config_without_a_media_source_line_is_left_alone(self):
        text = CONFIG.replace('source = "test"   # 复刻镜像默认：没接摄像头也能起\n', "")
        self.w.path(H.MEDIA_CONFIG).write_text(text, encoding="utf-8")
        code, last = self.call({"by": "测试", "camera": "camera"})
        self.assertEqual((code, last["result"]), (H.EXIT_STATE, "bad_config"))
        self.assertEqual((self.text(), self.w.restarts), (text, 0))

    def test_tof_is_enabled_and_started(self):
        code, last = self.call({"by": "测试", "tof": True})
        self.assertEqual((code, last), (H.EXIT_OK, {"result": "sensors_applied", "tof": "enabled"}))
        self.assertTrue(self.w.tofd_enabled)
        self.assertEqual(self.w.restarts, 0)

    def test_tof_failure_is_reported(self):
        self.w.enable_rc = 1
        code, last = self.call({"by": "测试", "tof": True, "camera": "camera"})
        self.assertEqual((code, last["result"]), (H.EXIT_FAIL, "tof_failed"))
        self.assertEqual(self.text(), CONFIG)                     # 后面的摄像头没碰

    def test_both_at_once(self):
        code, last = self.call({"by": "测试", "camera": "camera", "tof": True})
        self.assertEqual((code, last), (H.EXIT_OK, {"result": "sensors_applied", "tof": "enabled", "camera": "camera"}))

    def test_only_fixed_values_are_accepted(self):
        for args in ({"by": "测试"}, {"by": "测试", "camera": "/dev/video9"}, {"by": "测试", "camera": True},
                     {"by": "测试", "tof": False}, {"by": "测试", "tof": "tofd; reboot"},
                     {"by": "测试", "camera": "camera", "unit": "ssh"}, {"camera": "camera"}):
            with self.subTest(args=args):
                code, last = self.call(args)
                self.assertEqual((code, last["result"]), (H.EXIT_ARGS, "bad_args"))
        self.assertEqual((self.text(), self.w.restarts, self.w.tofd_enabled), (CONFIG, 0, False))

    def test_waits_for_a_software_install_to_finish(self):
        marker = self.w.path(H.SOFTWARE_MARKER)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("job", encoding="utf-8")
        code, last = self.call({"by": "测试", "camera": "camera", "tof": True})
        self.assertEqual((code, last["result"]), (H.EXIT_BUSY, "busy"))
        self.assertEqual((self.text(), self.w.tofd_enabled), (CONFIG, False))


if __name__ == "__main__":
    unittest.main()
