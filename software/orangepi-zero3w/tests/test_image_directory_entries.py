"""Deleted ext4 directory records must never become recursive child paths."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'image/image_fs.py'
SPEC = importlib.util.spec_from_file_location('image_directory_under_test', SOURCE)
image_fs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image_fs)


class DirectoryListingTests(unittest.TestCase):
    def listing(self, content):
        with patch.object(image_fs, 'exists', return_value=True), \
                patch.object(image_fs, 'debug', return_value=content):
            return image_fs.children('unused.ext4', '/var/lib/apt/lists/partial')

    def test_real_debugfs_deleted_record_is_ignored(self):
        listing = (b'/2/040755/0/0/.//\n/3/040755/0/0/..//\n'
                   b'/0/000000/0/0//0/\n/4/100600/0/0/retained/7/\n')
        self.assertEqual(self.listing(listing),
                         [('/var/lib/apt/lists/partial/retained', 0o100600)])

    def test_unsafe_names_and_modes_fail_closed(self):
        for listing in [b'/4/040755/0/0//0/\n',
                        b'/4/040755/0/0/name/with/slash/0/\n',
                        b'/4/040755/0/0/name\nwith-newline/0/\n',
                        b'/4/000000/0/0/retained/0/\n',
                        b'/4/040755/0/0/name\x00/0/\n']:
            with self.subTest(listing=repr(listing)), self.assertRaises(ValueError):
                self.listing(listing)

    def test_actual_ext4_delete_hole_can_be_removed_recursively(self):
        try:
            executable = image_fs.tool('mke2fs')
        except RuntimeError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            fs = work / 'regression.ext4'
            with fs.open('wb') as stream:
                stream.truncate(16 * 1024 * 1024)
            image_fs.run([executable, '-q', '-t', 'ext4', '-F', '-b', '1024', fs])
            image_fs.mkdir(fs, '/fixture/partial')
            image_fs.write(fs, '/fixture/partial/deleted-item', b'public fixture', work)
            # An extra directory block retains inode-zero free records after
            # deletion, just as the observed APT lists/partial directory did.
            image_fs.debug(fs, 'expand_dir /fixture/partial', write=True)
            image_fs.remove(fs, '/fixture/partial/deleted-item')
            listing = image_fs.debug(fs, 'ls -lp /fixture/partial')
            self.assertIn(b'/0/000000/0/0//0/', listing)
            self.assertEqual(image_fs.children(fs, '/fixture/partial'), [])
            image_fs.remove(fs, '/fixture')
            self.assertFalse(image_fs.exists(fs, '/fixture'))
            image_fs.check(fs, work / 'fsck.txt')


if __name__ == '__main__':
    unittest.main()
