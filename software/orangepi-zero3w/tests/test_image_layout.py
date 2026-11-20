"""Exercise disk boundary checks through actual assembly and extraction."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'image/image_fs.py'
SPEC = importlib.util.spec_from_file_location('image_fs_under_test', SOURCE)
image_fs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image_fs)


class ImageLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / 'base.img'
        self.original_rootfs_bytes = 4096
        header = bytearray(512)
        header[450] = 0x83
        struct.pack_into('<II', header, 454, image_fs.OFFSET // 512, self.original_rootfs_bytes // 512)
        header[510:] = b'\x55\xaa'
        self.header = bytes(header)
        with self.base.open('wb') as stream:
            stream.write(header)
            stream.truncate(image_fs.OFFSET + self.original_rootfs_bytes)

    def tearDown(self):
        self.temporary.cleanup()

    def test_expansion_updates_partition_end_and_extracts_entire_rootfs(self):
        rootfs = self.root / 'expanded.ext4'
        content = b'expanded filesystem data' + b'\0' * (8192 - 24)
        rootfs.write_bytes(content)
        output = self.root / 'full.img'
        image_fs.assemble(self.base, rootfs, output)
        with output.open('rb') as stream:
            header = stream.read(512)
        start, sectors = struct.unpack_from('<II', header, 454)
        self.assertEqual(start * 512, image_fs.OFFSET)
        self.assertEqual((start + sectors) * 512, output.stat().st_size)
        self.assertEqual(sectors * 512, rootfs.stat().st_size)
        restored = self.root / 'restored.ext4'
        image_fs.extract(output, restored)
        self.assertEqual(restored.read_bytes(), content)

    def test_original_size_preserves_the_original_mbr(self):
        rootfs = self.root / 'same.ext4'
        rootfs.write_bytes(b'\0' * self.original_rootfs_bytes)
        output = self.root / 'same.img'
        image_fs.assemble(self.base, rootfs, output)
        with output.open('rb') as stream:
            self.assertEqual(stream.read(512), self.header)

    def test_unaligned_filesystem_is_rejected_before_output_creation(self):
        rootfs = self.root / 'bad.ext4'
        rootfs.write_bytes(b'bad')
        output = self.root / 'bad.img'
        with self.assertRaises(ValueError):
            image_fs.assemble(self.base, rootfs, output)
        self.assertFalse(output.exists())

    def test_extra_partition_and_trailing_image_bytes_are_rejected(self):
        with self.base.open('r+b') as stream:
            stream.seek(466)
            stream.write(b'\x83')
        with self.assertRaises(ValueError):
            image_fs.extract(self.base, self.root / 'bad.ext4')
        with self.base.open('r+b') as stream:
            stream.seek(466)
            stream.write(b'\0')
            stream.truncate(self.base.stat().st_size + 512)
        with self.assertRaises(ValueError):
            image_fs.extract(self.base, self.root / 'trailing.ext4')


if __name__ == '__main__':
    unittest.main()
