"""Exercise the actual GPT/ext4 builder against disposable disk-image files."""

import importlib.util
import lzma
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib

SPEC = importlib.util.spec_from_file_location("build_image", Path(__file__).with_name("build_image.py"))
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def make_disk(directory):
    filesystem = directory / "rootfs.ext4"
    with filesystem.open("wb") as stream:
        stream.truncate(24 * 1024 * 1024)
    subprocess.run([builder.tool("mke2fs"), "-q", "-t", "ext4", "-F", "-L", "rootfs", str(filesystem)],
                   check=True, capture_output=True)
    offset = 2048 * 512
    sectors = (offset + filesystem.stat().st_size) // 512 + 2048
    table = bytearray(128 * 128)
    table[:16] = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4").bytes_le
    table[16:32] = uuid.uuid4().bytes_le
    struct.pack_into("<QQ", table, 32, offset // 512, (offset + filesystem.stat().st_size) // 512 - 1)
    table[56:68] = "rootfs".encode("utf-16-le")

    disk_guid = uuid.uuid4().bytes_le

    def header(current, backup, entries):
        value = bytearray(512)
        value[:8] = b"EFI PART"
        struct.pack_into("<IIIIQQQQ", value, 8, 0x10000, 92, 0, 0, current, backup, 34, sectors - 34)
        value[56:72] = disk_guid
        struct.pack_into("<QIII", value, 72, entries, 128, 128, zlib.crc32(table))
        struct.pack_into("<I", value, 16, zlib.crc32(value[:92]))
        return value

    disk = directory / "base.img"
    with disk.open("wb") as stream:
        stream.truncate(sectors * 512)
        mbr = bytearray(512)
        mbr[:14] = b"BOOT-SENTINEL!"
        mbr[450] = 0xee
        struct.pack_into("<II", mbr, 454, 1, sectors - 1)
        mbr[510:] = b"\x55\xaa"
        stream.seek(0)
        stream.write(mbr)
        stream.write(header(1, sectors - 1, 2))
        stream.write(table)
        stream.seek(offset)
        with filesystem.open("rb") as source:
            builder.copy_bytes(source, stream, filesystem.stat().st_size)
        stream.seek((sectors - 33) * 512)
        stream.write(table)
        stream.write(header(sectors - 1, 1, sectors - 33))
    return disk, filesystem, offset


class ImageBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            for name in ("debugfs", "mke2fs", "e2fsck", "xz"):
                builder.tool(name)
        except ValueError as error:
            raise unittest.SkipTest(str(error))

    def test_complete_build_preserves_boot_and_sanitizes_credentials(self):
        with tempfile.TemporaryDirectory(prefix="image-tests-") as temporary:
            directory = Path(temporary)
            base, fs, offset = make_disk(directory)
            secret = directory / "secret"
            secret.write_text("base-only secret")
            builder.debugfs(fs, ['mkdir /etc', 'write ' + builder.quote(secret) + ' /etc/host-key'], directory, write=True)
            with base.open("r+b") as target, fs.open("rb") as source:
                target.seek(offset)
                builder.copy_bytes(source, target, fs.stat().st_size)
            original = base.read_bytes()
            overlay = directory / "overlay"
            (overlay / "opt/duck").mkdir(parents=True)
            content = overlay / "opt/duck/start"
            content.write_bytes(b"#!/bin/sh\nprintf 'duck\\n'\n")
            content.chmod(0o750)
            (overlay / "etc").mkdir()
            (overlay / "etc/duck").symlink_to("/opt/duck/start")
            output = directory / "duck.img.xz"
            manifest = builder.build(base, overlay, output, removals=["/etc/host-key", "/etc/absent-key"])
            packed = lzma.decompress(output.read_bytes())
            self.assertEqual(packed[:offset], original[:offset])
            end = offset + fs.stat().st_size
            self.assertEqual(packed[end:], original[end:])
            self.assertEqual(manifest["output"]["sha256"], builder.digest(output))
            self.assertTrue(Path(str(output) + ".sha256").is_file())
            readback = directory / "result.ext4"
            readback.write_bytes(packed[offset:end])
            records = builder.image_stats(readback, ["/etc/host-key", "/opt/duck/start", "/etc/duck"], directory)
            self.assertNotIn("/etc/host-key", records)
            self.assertEqual(records["/opt/duck/start"]["mode"], 0o750)
            self.assertEqual(records["/etc/duck"]["kind"], "symlink")

    def test_corrupt_gpt_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            disk, _fs, _offset = make_disk(directory)
            with disk.open("r+b") as image:
                image.seek(512 + 56)
                image.write(b"corrupted")
            with self.assertRaisesRegex(ValueError, "GPT header checksum"):
                builder.partitions(disk)

    def test_overlay_preserves_existing_directory_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _disk, fs, _offset = make_disk(directory)
            builder.debugfs(fs, ["mkdir /etc", "set_inode_field /etc mode 040755",
                                 "set_inode_field /etc uid 123", "set_inode_field /etc gid 456"],
                            directory, write=True)
            overlay = directory / "overlay"
            (overlay / "etc").mkdir(parents=True)
            (overlay / "etc").chmod(0o700)
            (overlay / "etc/duck.conf").write_text("configuration")
            entries = builder.apply_overlay(fs, builder.overlay_entries(overlay), [], directory)
            original = builder.image_stats(fs, ["/etc"], directory)["/etc"]
            self.assertEqual((original["mode"], original["uid"], original["gid"]),
                             (0o755, 123, 456))
            manifest_entry = next(entry for entry in entries if entry["path"] == "/etc")
            self.assertEqual((manifest_entry["mode"], manifest_entry["uid"], manifest_entry["gid"]),
                             (0o755, 123, 456))

    def test_corrupt_backup_gpt_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            disk, _fs, _offset = make_disk(directory)
            with disk.open("r+b") as image:
                image.seek(-512, 2)
                image.write(b"NOT GPT!")
            with self.assertRaisesRegex(ValueError, "backup GPT header"):
                builder.partitions(disk)

    def test_existing_symlink_ancestor_and_directory_removal_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _disk, fs, _offset = make_disk(directory)
            builder.debugfs(fs, ['mkdir /real', 'symlink /opt /real'], directory, write=True)
            overlay = directory / "overlay"
            (overlay / "opt").mkdir(parents=True)
            (overlay / "opt/duck").write_text("duck")
            with self.assertRaisesRegex(ValueError, "ancestor is not a directory"):
                builder.apply_overlay(fs, builder.overlay_entries(overlay), [], directory)
            with self.assertRaisesRegex(ValueError, "directory removal"):
                builder.apply_overlay(fs, [], ["/real"], directory)

    def test_special_files_and_unsafe_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            os.mkfifo(directory / "pipe")
            with self.assertRaisesRegex(ValueError, "special file"):
                builder.overlay_entries(directory)
        for value in ("/", "relative/path", "/etc/../root/key", '/etc/a"b', "/etc/a\nb"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                builder.absolute_path(value)

    def test_ambiguous_rootfs_requires_partition_selection(self):
        items = [{"number": number, "ext4": True, "label": "data", "name": "primary"}
                 for number in (1, 2)]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            builder.root_partition(items)
        self.assertEqual(builder.root_partition(items, 2)["number"], 2)


if __name__ == "__main__":
    unittest.main()
