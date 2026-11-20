import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


CAMERA = Path(__file__).resolve().parent
BOARD = CAMERA.parents[1]
STUDIO = BOARD.parent / "software/studio"


class CameraPreviewTests(unittest.TestCase):
    def test_studio_override_preserves_installed_arguments(self):
        override = CAMERA / "service/microduck-studio.service.d/a7z-camera.conf"
        self.assertTrue(override.is_file(), "missing opt-in Studio service override")
        starts = [line.removeprefix("ExecStart=") for line in override.read_text().splitlines()
                  if line.startswith("ExecStart=")]
        self.assertEqual(starts[0], "")
        self.assertEqual(len(starts), 2)
        for base in (BOARD / "studio/install_studio.py", BOARD / "image/prepare_image.py"):
            command = next(line for line in base.read_text(encoding="utf-8").splitlines()
                           if line.startswith("ExecStart=") and "run.py" in line)
            args = shlex.split(command.removeprefix("ExecStart="))
            socket = args.index("--camera-socket") + 1
            self.assertEqual(args[socket], "none")
            args[socket] = "unix:/run/mediad/media.sock"
            self.assertEqual(shlex.split(starts[1]), args)

    def test_enabled_studio_reads_mediad_camera_source(self):
        patch = CAMERA / "studio-camera-config.patch"
        self.assertTrue(patch.is_file(), "missing A7Z Studio media-config patch")
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / "studio").mkdir()
            for name in ("sensors.py", "rpc.py"):
                shutil.copyfile(STUDIO / "studio" / name, app / "studio" / name)
            subprocess.run(["git", "apply", "--ignore-space-change", str(patch)],
                           cwd=app, check=True)
            config = app / "etc/robot"
            config.mkdir(parents=True)
            (config / "robotd.toml").write_text('[media]\nsource = "test"\n')
            shutil.copyfile(CAMERA / "service/mediad.toml", config / "mediad.toml")
            probe = ("from studio.sensors import Sensors\n"
                     "assert Sensors(root='.')._camera_config()['source'] == 'camera'\n")
            subprocess.run([sys.executable, "-c", probe], cwd=app, check=True,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_sparse_web_package_contains_camera_screen(self):
        page = BOARD / "studio/console-integration/web/sensors.js"
        self.assertTrue(page.is_file(), "default console bundle omits the camera screen")
        js = page.read_text(encoding="utf-8")
        self.assertIn('aspectRatio: sideways ? `${height} / ${width}` : `${width} / ${height}`', js)
        self.assertNotIn('72 * (sideways ? height / width : width / height)', js)


if __name__ == "__main__":
    unittest.main()
