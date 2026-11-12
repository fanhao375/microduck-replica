"""Verify executable layout survives diagnostic path sanitization."""
import importlib.util
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'image/sanitize_runtime.py'
SPEC = importlib.util.spec_from_file_location('runtime_paths_under_test', SOURCE)
runtime_paths = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_paths)


def elf_fixture(rodata, text=b'code'):
    names = b'\0.shstrtab\0.text\0.rodata\0'
    data = bytearray(1280)
    ident = b'\x7fELF\x02\x01\x01' + b'\0' * 9
    struct.pack_into('<16sHHIQQQIHHHHHH', data, 0, ident, 3, 183, 1,
                     0, 64, 1024, 0, 64, 56, 1, 64, 4, 1)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 4, 0, 0, 0, len(data), len(data), 4096)
    data[128:128 + len(names)] = names
    data[192:192 + len(text)] = text
    data[256:256 + len(rodata)] = rodata
    for index, name, kind, offset, size in [(1, 1, 3, 128, len(names)),
                                          (2, 11, 1, 192, len(text)),
                                          (3, 17, 1, 256, len(rodata))]:
        struct.pack_into('<IIQQQQIIQQ', data, 1024 + index * 64,
                         name, kind, 0, 0, offset, size, 0, 0, 1, 0)
    return bytes(data)


class RuntimePathTests(unittest.TestCase):
    def test_private_rodata_path_changes_without_layout_or_pointer_length_changes(self):
        original = elf_fixture(b'/Users/operator/work/project/.toolchain/cargo/source.rs\0')
        changed, count, roots = runtime_paths.scrub_elf(original, 'operator')
        self.assertEqual((count, roots), (1, 1))
        self.assertNotIn(b'/Users/operator', changed)
        self.assertIn(b'/build/source', changed)
        self.assertIn(b'/.toolchain/cargo/source.rs\0', changed)
        self.assertEqual(len(changed), len(original))
        self.assertEqual(changed[:128], original[:128])
        self.assertEqual(changed[1024:], original[1024:])

    def test_other_authors_public_paths_stay_unchanged(self):
        original = elf_fixture(b'/home/upstream-author/project/source.rs\0')
        changed, count, roots = runtime_paths.scrub_elf(original, 'operator')
        self.assertEqual((changed, count, roots), (original, 0, 0))

    def test_private_data_outside_rodata_is_rejected(self):
        original = elf_fixture(b'public', b'/Users/operator/project/source.rs\0')
        with self.assertRaises(ValueError):
            runtime_paths.scrub_elf(original, 'operator')


if __name__ == '__main__':
    unittest.main()
