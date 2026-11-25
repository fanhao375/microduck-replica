"""root 小助手的 skill-table：把主程序配置里一整行的动作表改写成 [[policy.skill]] 一条一条的格式。

11-24 在 duck-8f3e 上查到：镜像带的 robotd.toml 把动作表写成 `"skill" = [{...}]` 一整行，主程序自己改配置
（robot.setSkill / removeSkill）只认 [[policy.skill]]，一改就拒「[[policy.skill]] is not a table array」。
要核对的：改前改后读出来逐项相等；已经是一条一条的不动；跟主程序抢同一把锁时不写。

    py -3.13 -I -m unittest tests.test_helper_skill_table
"""
import json
import shutil
import tempfile
import tomllib
import unittest

from tests.test_helper_bus import H, FakeCtx, World

# 跟 duck-8f3e 上 /etc/robot/feetech-ft5/robotd.toml 同样的写法（路径和数值照抄，其余段删短了）
CONFIG = '''["bus"]
"port" = "/dev/ttyS2"

["policy"]
"walk" = "/opt/robot/feetech-ft5-r5/models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx"
"action_scale" = 0.7
"skill" = [{ "name" = "roulade", "duration" = 1.0, "chain" = true, "command" = [0.0, 0.0, 0.0], "unwind" = [0.0, 0.0, 0.0], "unwind_s" = 0.0, "params" = { "action_scale" = 1.0, "gain_ratio" = 1.0 } }, { "name" = "kick_right", "path" = "/opt/robot/feetech-ft5-r5/models/67ad09d82f4865d222a7ae06f250cf9f/policy.onnx", "duration" = 0.5, "chain" = false, "command" = [0.0, 0.0, 0.0], "unwind" = [0.0, 0.0, 0.0], "unwind_s" = 0.0, "params" = { "action_scale" = 1.0 } }]
"head_lowpass" = 0.5

["safety"]
"max_torque" = 8.0
'''

MULTILINE = '''[policy]
walk = "/x/walk.onnx"
skill = [
  { name = "kick_left", path = "/x/k.onnx", duration = 0.5 },
  { name = "名字", duration = 2, params = { gain_ratio = 1.2 } },
]

[pad]
a = "kick_left"
'''


class SkillTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helper-skill-table-")
        self.w = World(self.tmp)
        self.cfg = self.w.path(H.IMU_CONFIG)
        self.saved = self.w.path(H.IMU_CONFIG_SAVED)
        self.cfg.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, args=None):
        ctx = FakeCtx(self.w, json.dumps(args if args is not None else {"by": "测试"}).encode())
        code = H.run(["microduck-helper", "skill-table"], ctx)
        lines = [json.loads(x) for x in ctx.stdout.buf.getvalue().decode("utf-8").splitlines() if x.strip()]
        return code, lines[-1]["detail"]

    def test_inline_table_becomes_array_of_tables_with_identical_content(self):
        self.cfg.write_text(CONFIG, encoding="utf-8")
        self.saved.write_text(CONFIG, encoding="utf-8")
        before = tomllib.loads(CONFIG)
        code, done = self.call()
        self.assertEqual(code, H.EXIT_OK, done)
        self.assertEqual(done["converted"], [H.IMU_CONFIG, H.IMU_CONFIG_SAVED])
        for path in (self.cfg, self.saved):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(tomllib.loads(text), before)
            self.assertIn("[[policy.skill]]", text)
            self.assertNotIn('"skill" = [', text)
        # 别的键原样留着（包括动作表后面那行）
        self.assertIn('"head_lowpass" = 0.5', self.cfg.read_text(encoding="utf-8"))

    def test_multiline_inline_array_and_nested_params(self):
        self.cfg.write_text(MULTILINE, encoding="utf-8")
        before = tomllib.loads(MULTILINE)
        code, done = self.call()
        self.assertEqual(code, H.EXIT_OK, done)
        self.assertEqual(tomllib.loads(self.cfg.read_text(encoding="utf-8")), before)

    def test_already_array_of_tables_is_left_alone(self):
        text = '[policy]\nwalk = "/x"\n\n[[policy.skill]]\nname = "kick_left"\nduration = 0.5\n'
        self.cfg.write_text(text, encoding="utf-8")
        code, done = self.call()
        self.assertEqual(code, H.EXIT_OK, done)
        self.assertEqual(done["converted"], [])
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    def test_waits_for_robotd_lock_then_gives_up_without_writing(self):
        self.cfg.write_text(CONFIG, encoding="utf-8")
        self.w.locks.add(H.IMU_CONFIG_DIR + "/robotd.lock")
        code, done = self.call()
        self.assertEqual(code, H.EXIT_BUSY, done)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), CONFIG)

    def test_bad_args_and_software_update(self):
        self.cfg.write_text(CONFIG, encoding="utf-8")
        self.assertEqual(self.call({"by": "x", "path": "/etc/passwd"})[0], H.EXIT_ARGS)
        self.w.path(H.SOFTWARE_MARKER).parent.mkdir(parents=True, exist_ok=True)
        self.w.path(H.SOFTWARE_MARKER).write_text("1")
        self.assertEqual(self.call()[0], H.EXIT_BUSY)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), CONFIG)

    def test_rewrite_refuses_values_it_cannot_render(self):
        with self.assertRaises(H.Stop):
            H.skill_table_rewrite('[policy]\nskill = [{ name = "x", deep = { a = { b = 1 } } }]\n')


if __name__ == "__main__":
    unittest.main()
