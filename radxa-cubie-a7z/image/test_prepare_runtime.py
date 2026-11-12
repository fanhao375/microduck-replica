"""Offline packaging checks; no robot connection or AArch64 execution."""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest

import prepare_runtime as runtime


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, records, sums=None):
        archive = self.root / "input.tar.gz"
        if sums is None:
            sums = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                           for name, data, kind in records if kind == tarfile.REGTYPE)
        with tarfile.open(archive, "w:gz") as target:
            for name, data, kind in [*records, ("SHA256SUMS", sums.encode(), tarfile.REGTYPE)]:
                member = tarfile.TarInfo(runtime.ARCHIVE_PREFIX + "/" + name)
                member.type = kind
                member.size = len(data) if kind == tarfile.REGTYPE else 0
                member.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                target.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        return archive

    def test_complete_inventory_extracts(self):
        source = self.archive([("models/model.bin", b"model", tarfile.REGTYPE)])
        output = self.root / "unpacked"
        runtime.unpack_verified(source, output)
        self.assertEqual((output / "models/model.bin").read_bytes(), b"model")

    def test_traversal_is_rejected(self):
        source = self.archive([("../escaped", b"bad", tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, "unsafe archive path"):
            runtime.unpack_verified(source, self.root / "unpacked")
        self.assertFalse((self.root / "escaped").exists())

    def test_symlink_is_rejected(self):
        source = self.archive([("linked", b"", tarfile.SYMTYPE)])
        with self.assertRaisesRegex(ValueError, "link or special"):
            runtime.unpack_verified(source, self.root / "unpacked")

    def test_missing_manifest_entry_is_rejected(self):
        source = self.archive([("extra", b"data", tarfile.REGTYPE)], sums="")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            runtime.unpack_verified(source, self.root / "unpacked")

    def test_corrupted_member_is_rejected(self):
        source = self.archive([("wrong", b"data", tarfile.REGTYPE)], sums="0" * 64 + "  wrong\n")
        with self.assertRaisesRegex(ValueError, "member SHA256 mismatch"):
            runtime.unpack_verified(source, self.root / "unpacked")

    def test_duplicate_member_is_rejected(self):
        source = self.archive([("duplicate", b"a", tarfile.REGTYPE),
                               ("duplicate", b"b", tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, "duplicate archive file"):
            runtime.unpack_verified(source, self.root / "unpacked")

    def test_untrusted_source_hash_is_rejected(self):
        source = self.root / "binary"
        source.write_bytes(b"not the reviewed binary")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            runtime.regular_source(source, runtime.ROBOTD_SHA256)


@unittest.skipUnless(runtime.DEFAULT_ARCHIVE.is_file() and runtime.DEFAULT_ROBOTD.is_file(),
                     "reviewed local runtime inputs are unavailable")
class ReviewedPayloadChecks(unittest.TestCase):
    def test_new_hardware_has_all_models_but_no_previous_calibration_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "root"
            report = runtime.stage(runtime.DEFAULT_ARCHIVE, runtime.DEFAULT_ROBOTD, output)
            self.assertEqual(report["boot_mode"], "commissioning")
            self.assertFalse(report["autostart_motion"])
            for name in (runtime.CONFIG / "hd1910-calibration.toml",
                         runtime.CONFIG / "hd1910-calibration.saved.toml",
                         runtime.RUNTIME / "hd1910-calibration.robot.toml",
                         runtime.RUNTIME / "hd1910-calibration.training.toml"):
                settings = tomllib.loads((output / name).read_text())
                self.assertFalse(settings["motion_enabled"])
                self.assertFalse(settings["imu_mount_verified"])
                self.assertFalse(settings["units_verified"])
                self.assertEqual(len(settings["joints"]), 15)
                self.assertTrue(all(j["calibrated"] is False and j["zero_raw"] == 2048
                                    for j in settings["joints"]))
            config = tomllib.loads((output / runtime.CONFIG / "robotd.toml").read_text())
            self.assertTrue(config["policy"]["enabled"])
            self.assertEqual(config["audio"]["device"], "default")
            self.assertEqual(config["audio"]["bank"], "/var/lib/robot/sounds")
            self.assertEqual((output / "etc/robot/robotd.toml").resolve(),
                             (output / runtime.CONFIG / "robotd.toml").resolve())
            for ident in runtime.CAPABILITIES.values():
                self.assertTrue((output / runtime.RUNTIME / "models" / ident / "policy.onnx").is_file())
            dropin = output / "etc/systemd/system/robotd.service.d/zzzz-zz-microduck-commissioning.conf"
            self.assertIn(" --commissioning\n", dropin.read_text())
            unit = (output / "etc/systemd/system/robotd.service").read_text()
            self.assertIn("/var/lib/cubie-duck/firstboot.done", unit)
            self.assertIn("cubie-servo-uart.py", unit)
            self.assertNotIn("uart-service.py", unit)
            for name, digest in report["files"].items():
                self.assertEqual(runtime.sha256(output / name), digest, name)
            disk_report = json.loads((output / "usr/share/cubie-duck/runtime-manifest.json").read_text())
            self.assertEqual(report, disk_report)
            before = (output / runtime.CONFIG / "hd1910-calibration.toml").read_bytes()
            with self.assertRaisesRegex(ValueError, "output already exists"):
                runtime.stage(runtime.DEFAULT_ARCHIVE, runtime.DEFAULT_ROBOTD, output)
            self.assertEqual((output / runtime.CONFIG / "hd1910-calibration.toml").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
