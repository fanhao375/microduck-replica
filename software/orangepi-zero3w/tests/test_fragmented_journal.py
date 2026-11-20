"""Journal erasure must preserve extent-tree nodes on fragmented filesystems."""
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

IMAGE = Path(__file__).resolve().parents[1] / 'image'
sys.path.insert(0, str(IMAGE))
SPEC = importlib.util.spec_from_file_location('fragmented_journal_under_test', IMAGE / 'build_public_image.py')
journal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journal)
import image_fs


class JournalMappingTests(unittest.TestCase):
    def mapping(self, extents, *, size=16384, allocated=b'10 20 21 30 31', first=b'20'):
        responses = {
            'stats': b'Block size: 4096\nBlock count: 100\n',
            'stat <8>': f'User: 0 Group: 0 Size: {size}\nEXTENTS:\n{extents}\n'.encode(),
            'blocks <8>': allocated,
            'bmap <8> 0': first,
        }
        fs = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=409600))
        with patch.object(journal, 'debug', side_effect=lambda _fs, command: responses[command]):
            return journal.journal_data_layout(fs)

    def test_metadata_first_does_not_become_journal_logical_zero(self):
        data, metadata, size = self.mapping('(ETB0):10, (0-1):20-21, (2-3):30-31')
        self.assertEqual((data, metadata, size), ([20, 21, 30, 31], {10}, 4096))

    def test_holes_overlaps_duplicate_physical_blocks_and_wrong_size_are_rejected(self):
        for extents, size in [
                ('(ETB0):10, (0-1):20-21, (3-4):30-31', 16384),
                ('(ETB0):10, (0-1):20-21, (1-2):30-31', 16384),
                ('(ETB0):10, (0-1):20-21, (2-3):21-22', 16384),
                ('(ETB0):10, (0-1):20-21, (2-3):30-31', 12288)]:
            with self.subTest(extents=extents, size=size), self.assertRaises(RuntimeError):
                self.mapping(extents, size=size)
        with self.assertRaises(RuntimeError):
            self.mapping('(ETB0):10, (0-1):20-21, (2-3):30-31', first=b'10')
        with self.assertRaises(RuntimeError):
            self.mapping('(ETB0):10, (0-1):20-21, (2-3):30-31', allocated=b'20 21 30 31')

    def test_real_fragmented_ext4_journal_keeps_extent_nodes_and_passes_fsck(self):
        try:
            mke2fs = image_fs.tool('mke2fs')
            image_fs.tool('tune2fs')
        except RuntimeError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            fs = work / 'fragmented.ext4'
            with fs.open('wb') as stream:
                stream.truncate(64 * 1024 * 1024)
            image_fs.run([mke2fs, '-q', '-t', 'ext4', '-F', '-b', '1024', '-O', '^has_journal', fs])
            free = int(re.search(rb'^Free blocks:\s*(\d+)', image_fs.debug(fs, 'stats'), re.M)[1])
            count = (free - 1024) // 512
            filler = work / 'public-filler'
            filler.write_bytes(b'P' * (512 * 1024))
            commands = ['mkdir /fill']
            commands += [f'write {image_fs.quote(filler)} /fill/f{i:03}' for i in range(count)]
            commands += [f'rm /fill/f{i:03}' for i in range(0, count, 2)]
            batch = work / 'fragmentation-commands'
            batch.write_text('\n'.join(commands) + '\n')
            changed = subprocess.run([image_fs.tool('debugfs'), '-w', '-f', str(batch), str(fs)],
                                     capture_output=True)
            self.assertEqual(changed.returncode, 0)
            self.assertEqual(len(image_fs.children(fs, '/fill')), count // 2)
            report = journal.rebuild_journal(fs)
            data, metadata, block_size = journal.journal_data_layout(fs)
            self.assertGreater(report['journal_extent_metadata_blocks_preserved'], 0)
            allocated = list(map(int, image_fs.debug(fs, 'blocks <8>').split()))
            self.assertIn(allocated[0], metadata)
            self.assertNotEqual(allocated[0], data[0])
            with fs.open('rb') as stream:
                for block in metadata:
                    stream.seek(block * block_size)
                    self.assertEqual(stream.read(2), b'\x0a\xf3')  # ext4 extent-node magic
                stream.seek(data[0] * block_size)
                self.assertEqual(struct.unpack('>I', stream.read(4))[0], 0xc03b3998)
                for block in data[1:]:
                    stream.seek(block * block_size)
                    self.assertEqual(stream.read(block_size), b'\0' * block_size)
            image_fs.check(fs, work / 'filesystem-after.txt')


if __name__ == '__main__':
    unittest.main()
