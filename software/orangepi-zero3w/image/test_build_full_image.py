"""Archive, checkpoint and cleanup contracts; no Docker daemon is contacted."""

import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import build_full_image as BUILDER


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as output:
        for name, permission, content, kind in entries:
            member = tarfile.TarInfo(name)
            member.mode = permission
            if kind == 'file':
                member.size = len(content)
                output.addfile(member, io.BytesIO(content))
            else:
                member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                member.linkname = content
                output.addfile(member)


class ArchiveTests(unittest.TestCase):
    def test_preserves_executable_and_private_configuration_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'runtime.tar.gz'
            archive(source, [
                ('orangepi-quanzhi/payload/robotd', 0o755, b'prebuilt executable', 'file'),
                ('orangepi-quanzhi/payload/calibration.toml', 0o440, b'motion_enabled=false', 'file'),
            ])
            BUILDER.safe_extract_runtime(source, root / 'native')
            self.assertEqual((root / 'native/orangepi-quanzhi/payload/robotd').stat().st_mode & 0o777, 0o755)
            self.assertEqual((root / 'native/orangepi-quanzhi/payload/calibration.toml').stat().st_mode & 0o777, 0o440)

    def test_rejects_paths_links_and_hardlinks_outside_package_contract(self):
        cases = [
            ('../outside', 0o644, b'state', 'file'),
            ('/etc/secret', 0o644, b'state', 'file'),
            ('orangepi-quanzhi/escape', 0o777, '../../outside', 'symlink'),
            ('orangepi-quanzhi/link', 0o777, 'orangepi-quanzhi/existing', 'hardlink'),
        ]
        for entry in cases:
            with self.subTest(name=entry[0], kind=entry[3]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / 'runtime.tar.gz'
                archive(source, [entry])
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    BUILDER.safe_extract_runtime(source, root / 'native')
                self.assertFalse((root / 'outside').exists())


class ReportTests(unittest.TestCase):
    def test_report_requires_matching_manifest_and_safe_runtime_state(self):
        valid = {'status': 'preinstalled-and-offline-readback-verified',
                 'payload_manifest_sha256': 'a' * 64,
                 'file_hash_mode_and_root_ownership_verified': True,
                 'studio_state_empty': True, 'policy_enabled': False,
                 'robotd_mode': 'read_only', 'services_started': False}
        BUILDER.validate_report(valid, 'a' * 64)
        for field, value in [('payload_manifest_sha256', 'b' * 64),
                             ('studio_state_empty', False), ('policy_enabled', True),
                             ('robotd_mode', 'motion'), ('services_started', True)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                BUILDER.validate_report({**valid, field: value}, 'a' * 64)


class CleanupTests(unittest.TestCase):
    def test_failed_guest_still_unmounts_and_removes_builder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, work = root / 'source', root / 'work'
            source.mkdir()
            work.mkdir()
            success = subprocess.CompletedProcess([], 0, '', '')
            with patch.object(BUILDER, 'logged', side_effect=[None, None, RuntimeError('guest failed')]), \
                    patch.object(BUILDER.subprocess, 'run', return_value=success) as calls:
                with self.assertRaisesRegex(RuntimeError, 'guest failed'):
                    BUILDER.build_linux_rootfs('docker', source, work)
            self.assertIn('/work/cleanup-guest.sh', calls.call_args_list[0].args[0])
            self.assertEqual(calls.call_args_list[1].args[0][1:3], ['rm', '--force'])

    def test_cleanup_timeout_still_removes_builder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, work = root / 'source', root / 'work'
            source.mkdir()
            work.mkdir()
            success = subprocess.CompletedProcess([], 0, '', '')
            with patch.object(BUILDER, 'logged', side_effect=[None, None, RuntimeError('guest failed')]), \
                    patch.object(BUILDER.subprocess, 'run', side_effect=[subprocess.TimeoutExpired('cleanup', 45), success]) as calls:
                with self.assertRaises(subprocess.TimeoutExpired):
                    BUILDER.build_linux_rootfs('docker', source, work)
            self.assertEqual(calls.call_args_list[1].args[0][1:3], ['rm', '--force'])


if __name__ == '__main__':
    unittest.main()
