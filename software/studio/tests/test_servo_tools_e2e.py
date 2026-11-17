"""舵机页「输数字」和「新舵机编号」的端到端：假 robotd + 真 Studio（演示舵机）+ 无头 Edge。

tests/servo_tools_e2e.mjs 在页面上真的点、真的敲键盘；这里核对它看到的东西。
要电脑上有 Node（≥ 22）和 Edge；没有就跳过。设 STUDIO_E2E=0 也跳过。SERVO_TOOLS_SHOTS=目录：顺手截图。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(ROOT / "deps"), str(ROOT / "dev")]

from tests.test_vpad_e2e import EDGE, NODE, _kill, _node_ok, _port, _wait_http  # noqa: E402

FIXTURE = HERE / "fixtures" / "now-20261018.jsonl"


@unittest.skipUnless(_node_ok() and EDGE and os.environ.get("STUDIO_E2E", "1") != "0",
                     "没有 Node ≥ 22 或者 Edge（或者设了 STUDIO_E2E=0）：跳过舵机页的端到端测试")
class ServoToolsE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="servo-tools-e2e-")).resolve()
        (cls.dir / "state").mkdir()
        p_rd, p_cfg, p_web, p_cdp = _port(17061), _port(17062), _port(18096), _port(9349)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = lambda name: open(cls.dir / name, "w", encoding="utf-8")   # noqa: E731
        cls.procs = {"fake": subprocess.Popen(
            [sys.executable, "-I", str(ROOT / "dev" / "fake_robotd.py"), "--fixture", str(FIXTURE),
             "--robotd-port", str(p_rd), "--configd-port", str(p_cfg)],
            cwd=ROOT, env=env, stdout=out("fake.out"), stderr=subprocess.STDOUT)}
        try:
            cls.procs["studio"] = subprocess.Popen(
                [sys.executable, "-I", str(ROOT / "run.py"), "--listen", f"127.0.0.1:{p_web}",
                 "--robotd", f"tcp:127.0.0.1:{p_rd}", "--configd", f"tcp:127.0.0.1:{p_cfg}", "--dev-autologin",
                 "--servo-sim", "--state-dir", str(cls.dir / "state"), "--log-dir", str(cls.dir / "logs")],
                cwd=ROOT, env=env, stdout=out("studio.out"), stderr=subprocess.STDOUT)
            _wait_http(f"http://127.0.0.1:{p_web}/healthz", "Studio")
            edge_dir = cls.dir / "edge"
            edge_dir.mkdir()
            cls.procs["edge"] = subprocess.Popen(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-setuid-sandbox",
                 "--enable-unsafe-swiftshader", f"--remote-debugging-port={p_cdp}",
                 f"--user-data-dir={edge_dir}", "--no-first-run", "--no-default-browser-check", "about:blank"],
                stdout=out("edge.out"), stderr=subprocess.STDOUT)
            _wait_http(f"http://127.0.0.1:{p_cdp}/json/version", "Edge 调试口", timeout=60)
        except Exception:
            for p in cls.procs.values():
                _kill(p)
            raise
        args = [NODE, str(HERE / "servo_tools_e2e.mjs"), "--url", f"http://127.0.0.1:{p_web}/", "--cdp", str(p_cdp)]
        if os.environ.get("SERVO_TOOLS_SHOTS"):
            args += ["--shots", os.environ["SERVO_TOOLS_SHOTS"]]
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=240, cwd=ROOT)
        if r.returncode != 0:
            for p in cls.procs.values():
                _kill(p)
            raise AssertionError(f"Node 脚本出错（日志在 {cls.dir}）：{(r.stderr or '')[-4000:]}")
        cls.out = json.loads(r.stdout)

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", {}).values():
            _kill(p)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_every_row_has_a_number_box_and_the_page_has_no_errors(self):
        self.assertEqual(self.out["inputs"], 15)
        self.assertEqual(self.out["errors"], [])

    def test_typing_does_nothing_while_the_servo_is_limp(self):
        limp = self.out["limp"]
        self.assertEqual(limp["after"], limp["before"])
        self.assertTrue(limp["hint"], "没上力时输数字要提示先开「保持」")

    def test_enter_moves_and_escape_does_not(self):
        typed = self.out["typed"]
        self.assertEqual(typed["got"], typed["want"])
        self.assertEqual(self.out["escaped"], typed["want"], "按 Esc 取消以后舵机不该动")

    def test_out_of_range_number_is_clamped(self):
        self.assertEqual(self.out["clamped"], 4095)

    def test_new_servo_is_found_and_offered_its_empty_position(self):
        found = self.out["numbering"]
        self.assertEqual(found["fresh"], ["1"])                             # 只有它标成「还没编号」
        self.assertIn("1 · 还没编号", found["chips"])
        self.assertEqual(len(found["chips"]), 15)
        self.assertEqual((found["from"], found["to"]), ("1", "21"))         # 默认就选好了：这颗 → 空着的位置
        self.assertEqual(found["open"], ["21"])                             # 已经有的位置选不了

    def test_numbering_puts_it_in_place_and_says_what_is_left(self):
        done = self.out["numbered"]
        self.assertIn("这颗现在是 21 号", done["note"])
        self.assertIn("15 个位置都齐了", done["note"])
        self.assertEqual((done["fresh"], done["chips"]), (0, 15))
        self.assertTrue(any("把 1 号改成 21 号" in text for text in self.out["dialogs"]))

    def test_number_box_fits_on_a_phone(self):
        mobile = self.out["mobile"]
        self.assertTrue(mobile["inView"], mobile)
        self.assertFalse(mobile["scrollX"], "手机上舵机页不该能左右滑")
        self.assertGreaterEqual(mobile["w"], 40)


if __name__ == "__main__":
    unittest.main()
