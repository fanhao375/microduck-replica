"""Temporary-file transactions only. No real sockets, services, processes, or installation paths."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / 'install_console.py'
SPEC = importlib.util.spec_from_file_location('install_console', MODULE)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


class Guard:
    def __init__(self, fail_at=None, change_at=None, static_startup=False, phase_change_at=None):
        self.calls = 0
        self.fail_at, self.change_at = fail_at, change_at
        self.static_startup, self.phase_change_at = static_startup, phase_change_at

    def capture(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise INSTALL.InstallError('torque-off is not confirmed')
        return {'robotd_pid': 101 if self.calls == self.change_at else 100,
                'binary_sha256': '1' * 64, 'calibration_sha256': '2' * 64, 'mode': 'motion',
                'phase': 'waiting_for_bus' if self.static_startup and self.calls != self.phase_change_at else 'control',
                'static_startup': self.static_startup,
                'backend_sha256': dict(INSTALL.BACKEND_BASELINE)}


class ConsoleInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.base = self.root / 'app'
        self.web = self.base / 'web'
        self.bundle = self.root / 'bundle'
        for directory in [self.web / 'model', self.bundle / 'payload' / 'web' / 'console']:
            directory.mkdir(parents=True)
        self.put(self.web, 'app.js', b'old app')
        self.put(self.web, 'app.js.gz', b'old gzip')
        self.put(self.web, 'app.js.br', b'old brotli')
        self.put(self.web, 'obsolete.css', b'old stylesheet')
        self.put(self.web, 'index.html', b'unchanged index')
        self.put(self.web, 'index.html.gz', b'unchanged index gzip')
        self.put(self.web, 'model/meshes.bin', b'immutable model')
        self.put(self.web, 'model/meshes.bin.gz', b'immutable model gzip')
        self.put(self.base, 'studio/state.py', b'backend untouched')
        self.put(self.root, 'calibration.toml', b'private calibration fixture')
        self.put(self.root, 'accounts.json', b'private account fixture')
        self.put(self.root, 'robotd', b'unchanged binary fixture')
        self.put(self.bundle / 'payload' / 'web', 'app.js', b'new app')
        self.put(self.bundle / 'payload' / 'web', 'console/new.css', b'new stylesheet')
        self.manifest = {'schema': 1, 'kind': 'console-web', 'release': '20261109-console1',
                         'compression': 'remove-changed-variants', 'files': {
                             'app.js': {'before_sha256': INSTALL.digest(b'old app'), 'after_sha256': INSTALL.digest(b'new app')},
                             'console/new.css': {'before_sha256': None, 'after_sha256': INSTALL.digest(b'new stylesheet')},
                             'obsolete.css': {'before_sha256': INSTALL.digest(b'old stylesheet'), 'after_sha256': None}}}
        self.write_manifest()
        self.before = INSTALL.inventory(self.web)
        self.outside = {str(path): INSTALL.digest(path.read_bytes()) for path in [self.base / 'studio/state.py', self.root / 'calibration.toml', self.root / 'accounts.json', self.root / 'robotd']}

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, root, name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_manifest(self):
        self.put(self.bundle, 'manifest.json', json.dumps(self.manifest).encode())

    def install(self, guard=None):
        return INSTALL.apply(self.bundle, self.base, guard=guard or Guard())

    def assert_outside_unchanged(self):
        for path, expected in self.outside.items():
            self.assertEqual(INSTALL.digest(Path(path).read_bytes()), expected)

    def test_preview_is_read_only_and_is_cli_default(self):
        with mock.patch.object(INSTALL.SystemGuard, 'capture', side_effect=AssertionError('no live queries')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(INSTALL.main([str(self.bundle), '--base', str(self.base)]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['preview'])
        self.assertFalse(result['readiness_checked'])
        self.assertEqual(result['remove_variants'], ['app.js.br', 'app.js.gz'])
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assertFalse((self.root / 'console-backups').exists())
        self.assertFalse((self.root / '.console-install.lock').exists())

    def test_wrong_before_hash_is_rejected(self):
        self.manifest['files']['app.js']['before_sha256'] = 'f' * 64
        self.write_manifest()
        with self.assertRaisesRegex(INSTALL.InstallError, 'installed web differs'):
            self.install()
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_new_file_must_not_already_exist(self):
        self.put(self.web, 'console/new.css', b'preexisting')
        with self.assertRaisesRegex(INSTALL.InstallError, 'installed web differs'):
            INSTALL.preview(self.bundle, self.base)

    def test_new_file_cannot_replace_a_directory(self):
        (self.web / 'console/new.css').mkdir(parents=True)
        with self.assertRaisesRegex(INSTALL.InstallError, 'already exists'):
            INSTALL.preview(self.bundle, self.base)

    def test_payload_hash_and_extra_files_are_rejected(self):
        path = self.bundle / 'payload/web/app.js'
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(INSTALL.InstallError, 'payload files or hashes'):
            INSTALL.preview(self.bundle, self.base)
        path.write_bytes(b'new app')
        self.put(self.bundle / 'payload/web', 'unlisted.js', b'unlisted')
        with self.assertRaisesRegex(INSTALL.InstallError, 'payload files or hashes'):
            INSTALL.preview(self.bundle, self.base)

    def test_path_escape_hidden_paths_and_models_are_rejected(self):
        for name in ['../outside.js', '/absolute.js', 'console/../../outside.js', 'console\\outside.js',
                     'console//file.js', './app.js', '.env', 'model/asset.js', 'models/asset.js', 'server.py', 'app.js.gz']:
            with self.subTest(name=name):
                self.manifest['files'] = {name: {'before_sha256': None, 'after_sha256': 'a' * 64}}
                self.write_manifest()
                with self.assertRaises(INSTALL.InstallError):
                    INSTALL.preview(self.bundle, self.base)
        self.assert_outside_unchanged()

    def test_payload_symlink_is_rejected(self):
        path = self.bundle / 'payload/web/app.js'
        path.unlink()
        path.symlink_to(self.root / 'accounts.json')
        with self.assertRaises(INSTALL.InstallError):
            INSTALL.preview(self.bundle, self.base)
        self.assert_outside_unchanged()

    def test_payload_parent_link_is_rejected(self):
        path = self.bundle / 'payload/web/console'
        (path / 'new.css').unlink()
        path.rmdir()
        path.symlink_to(self.web / 'model', target_is_directory=True)
        with self.assertRaises(INSTALL.InstallError):
            INSTALL.preview(self.bundle, self.base)

    def test_target_links_and_hardlinks_are_rejected(self):
        path = self.web / 'app.js.gz'
        path.unlink()
        path.symlink_to(self.root / 'accounts.json')
        with self.assertRaises(INSTALL.InstallError):
            INSTALL.preview(self.bundle, self.base)
        path.unlink()
        os.link(self.web / 'app.js', path)
        with self.assertRaises(INSTALL.InstallError):
            INSTALL.preview(self.bundle, self.base)
        self.assert_outside_unchanged()

    def test_payload_hardlink_is_rejected(self):
        os.link(self.bundle / 'payload/web/app.js', self.bundle / 'second-copy')
        with self.assertRaises(INSTALL.InstallError):
            INSTALL.preview(self.bundle, self.base)

    def test_not_ready_refuses_before_staging(self):
        with self.assertRaisesRegex(INSTALL.InstallError, 'torque-off'):
            self.install(Guard(fail_at=1))
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assertFalse((self.root / 'console-backups').exists())

    def test_publish_preserves_backup_and_only_changes_web(self):
        guard = Guard()
        result = self.install(guard)
        self.assertTrue(result['installed'])
        self.assertFalse(result['services_restarted'])
        self.assertEqual(guard.calls, 3)
        self.assertEqual((self.web / 'app.js').read_bytes(), b'new app')
        self.assertEqual((self.web / 'console/new.css').read_bytes(), b'new stylesheet')
        self.assertFalse((self.web / 'app.js.gz').exists())
        self.assertFalse((self.web / 'app.js.br').exists())
        self.assertFalse((self.web / 'obsolete.css').exists())
        for name in ['model/meshes.bin', 'model/meshes.bin.gz', 'index.html.gz']:
            self.assertEqual(INSTALL.inventory(self.web)[name], self.before[name])
        backup = Path(result['backup'])
        self.assertEqual(INSTALL.inventory(backup / 'before-web'), self.before)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        journal = json.loads((backup / 'journal.json').read_text())
        self.assertEqual(journal['state'], 'installed')
        self.assertEqual(journal['before_guard'], journal['after_guard'])
        self.assert_outside_unchanged()

    def test_private_umask_does_not_make_new_assets_unreadable(self):
        old = os.umask(0o077)
        try:
            self.install()
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE((self.web / 'console/new.css').stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.web / 'console').stat().st_mode), 0o755)

    def test_identity_change_before_publish_keeps_web_unchanged(self):
        with self.assertRaisesRegex(INSTALL.InstallError, 'before publication.*web unchanged'):
            self.install(Guard(change_at=2))
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_identity_change_after_publish_restores_entire_old_web(self):
        with self.assertRaisesRegex(INSTALL.InstallError, 'after publication.*web restored'):
            self.install(Guard(change_at=3))
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        journal = next((self.root / 'console-backups').glob('*/journal.json'))
        self.assertEqual(json.loads(journal.read_text())['state'], 'failed-restored')
        self.assert_outside_unchanged()

    def test_post_publish_guard_failure_also_restores(self):
        with self.assertRaisesRegex(INSTALL.InstallError, 'torque-off.*web restored'):
            self.install(Guard(fail_at=3))
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_exchange_failure_does_not_fall_back_to_non_atomic_copy(self):
        with mock.patch.object(INSTALL, 'exchange_directories', side_effect=INSTALL.InstallError('atomic exchange unavailable')):
            with self.assertRaisesRegex(INSTALL.InstallError, 'atomic exchange unavailable.*web unchanged'):
                self.install()
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_failure_syncing_after_exchange_is_treated_as_published(self):
        actual = INSTALL.sync_directory
        failed = False
        def failing_sync(path):
            nonlocal failed
            if Path(path) == self.base and (self.web / 'app.js').read_bytes() == b'new app' and not failed:
                failed = True
                raise OSError('injected directory fsync failure')
            return actual(path)
        with mock.patch.object(INSTALL, 'sync_directory', side_effect=failing_sync):
            with self.assertRaisesRegex(INSTALL.InstallError, 'web restored'):
                self.install()
        self.assertTrue(failed)
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_rollback_preview_and_apply(self):
        result = self.install()
        after = INSTALL.inventory(self.web)
        guard = Guard()
        preview = INSTALL.rollback(result['backup'], self.base, guard=guard)
        self.assertTrue(preview['preview'])
        self.assertEqual(guard.calls, 0)
        self.assertEqual(INSTALL.inventory(self.web), after)
        rollback = INSTALL.rollback(result['backup'], self.base, guard=guard, apply_changes=True)
        self.assertTrue(rollback['rolled_back'])
        self.assertEqual(guard.calls, 3)
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assert_outside_unchanged()

    def test_rollback_refuses_changed_live_web_or_backup(self):
        result = self.install()
        self.put(self.web, 'app.js', b'subsequent change')
        with self.assertRaisesRegex(INSTALL.InstallError, 'current web differs'):
            INSTALL.rollback(result['backup'], self.base)
        self.put(self.web, 'app.js', b'new app')
        self.put(Path(result['backup']) / 'before-web', 'app.js', b'tampered backup')
        with self.assertRaisesRegex(INSTALL.InstallError, 'backup hash mismatch'):
            INSTALL.rollback(result['backup'], self.base)

    def test_failed_rollback_postcheck_restores_installed_web(self):
        result = self.install()
        after = INSTALL.inventory(self.web)
        with self.assertRaisesRegex(INSTALL.InstallError, 'during rollback'):
            INSTALL.rollback(result['backup'], self.base, guard=Guard(change_at=3), apply_changes=True)
        self.assertEqual(INSTALL.inventory(self.web), after)
        self.assertEqual(json.loads((Path(result['backup']) / 'journal.json').read_text())['state'], 'installed')

    def test_failed_rollback_journal_fsync_restores_retryable_installed_journal(self):
        result = self.install()
        after, backup = INSTALL.inventory(self.web), Path(result['backup'])
        actual, failed = INSTALL.sync_directory, False
        def failing_sync(path):
            nonlocal failed
            if Path(path) == backup and not failed and json.loads((backup / 'journal.json').read_text())['state'] == 'rolled-back':
                failed = True
                raise OSError('injected rollback journal fsync failure')
            return actual(path)
        with mock.patch.object(INSTALL, 'sync_directory', side_effect=failing_sync):
            with self.assertRaisesRegex(OSError, 'journal fsync'):
                INSTALL.rollback(backup, self.base, guard=Guard(), apply_changes=True)
        self.assertTrue(failed)
        self.assertEqual(INSTALL.inventory(self.web), after)
        self.assertEqual(json.loads((backup / 'journal.json').read_text())['state'], 'installed')
        self.assertTrue(INSTALL.rollback(backup, self.base)['preview'])
        self.assertTrue(INSTALL.rollback(backup, self.base, guard=Guard(), apply_changes=True)['rolled_back'])
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_cli_apply_requires_root(self):
        with mock.patch.object(INSTALL.os, 'geteuid', return_value=1001):
            with self.assertRaisesRegex(INSTALL.InstallError, 'requires root'):
                INSTALL.main([str(self.bundle), '--base', str(self.base), '--apply'])
        self.assertEqual(INSTALL.inventory(self.web), self.before)

    def test_duplicate_manifest_json_keys_are_rejected(self):
        raw = json.dumps(self.manifest).replace('"schema": 1', '"schema": 1, "schema": 1', 1)
        (self.bundle / 'manifest.json').write_text(raw)
        with self.assertRaisesRegex(INSTALL.InstallError, 'duplicate JSON key'):
            INSTALL.preview(self.bundle, self.base)

    def test_model_bytes_and_timestamps_remain_unchanged(self):
        paths = [self.web / 'model/meshes.bin', self.web / 'model/meshes.bin.gz']
        for offset, path in enumerate(paths):
            os.utime(path, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000 + offset * 1_000_000_000))
        times = {path.name: path.stat().st_mtime_ns for path in paths}
        self.install()
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in paths}, times)

    def test_metadata_uses_descriptors_when_path_nofollow_operations_are_unavailable(self):
        source_file, target_file = self.root / 'metadata-source.js', self.root / 'metadata-target.js'
        source_file.write_bytes(b'source'); target_file.write_bytes(b'target')
        source_directory, target_directory = self.root / 'metadata-source-dir', self.root / 'metadata-target-dir'
        source_directory.mkdir(); target_directory.mkdir()
        os.chmod(source_file, 0o640); os.chmod(source_directory, 0o750)
        for source in (source_file, source_directory):
            os.utime(source, ns=(1_500_000_000_000_000_000, 1_500_000_001_000_000_000))
        actual_utime = os.utime
        def descriptor_utime(descriptor, *args, **kwargs):
            self.assertIsInstance(descriptor, int, 'metadata timestamps must use the anchored descriptor')
            return actual_utime(descriptor, *args, **kwargs)
        with mock.patch.object(INSTALL.os, 'geteuid', return_value=0), mock.patch.object(INSTALL.os, 'chown', side_effect=NotImplementedError('path chown is unavailable')), mock.patch.object(INSTALL.os, 'chmod', side_effect=NotImplementedError('chmod: follow_symlinks unavailable on this platform')), mock.patch.object(INSTALL.os, 'fchown') as ownership, mock.patch.object(INSTALL.os, 'utime', side_effect=descriptor_utime):
            for source, target in ((source_file, target_file), (source_directory, target_directory)):
                info = source.stat()
                INSTALL.set_metadata(target, info)
                result = target.stat()
                self.assertEqual(stat.S_IMODE(result.st_mode), stat.S_IMODE(info.st_mode))
                self.assertEqual((result.st_atime_ns, result.st_mtime_ns), (info.st_atime_ns, info.st_mtime_ns))
            self.assertEqual(ownership.call_count, 2)
            for call in ownership.call_args_list:
                self.assertIsInstance(call.args[0], int)

    def test_metadata_rejects_symlinks_without_mutating_their_target(self):
        target = self.root / 'accounts.json'
        before = target.stat()
        link = self.root / 'metadata-link.js'
        link.symlink_to(target)
        with self.assertRaises((OSError, INSTALL.InstallError)):
            INSTALL.set_metadata(link, (self.web / 'app.js').stat())
        after = target.stat()
        self.assertTrue(link.is_symlink())
        self.assertEqual((after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns),
                         (before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns))
        self.assert_outside_unchanged()

    def test_apply_and_rollback_work_without_path_chmod_nofollow_support(self):
        actual = os.chmod
        def linux_chmod(path, *args, **kwargs):
            if kwargs.get('follow_symlinks') is False:
                raise NotImplementedError('chmod: follow_symlinks unavailable on this platform')
            return actual(path, *args, **kwargs)
        with mock.patch.object(INSTALL.os, 'chmod', side_effect=linux_chmod):
            result = self.install()
            self.assertTrue(result['installed'])
            self.assertTrue(INSTALL.rollback(result['backup'], self.base, guard=Guard(), apply_changes=True)['rolled_back'])
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assert_outside_unchanged()

    def test_tampered_journal_cannot_restore_unlisted_model_changes(self):
        result = self.install()
        backup = Path(result['backup'])
        journal = json.loads((backup / 'journal.json').read_text())
        self.put(backup / 'before-web', 'model/meshes.bin', b'forged model')
        journal['before']['model/meshes.bin'] = INSTALL.digest(b'forged model')
        (backup / 'journal.json').write_text(json.dumps(journal))
        with self.assertRaisesRegex(INSTALL.InstallError, 'outside its manifest'):
            INSTALL.rollback(backup, self.base)
        self.assertEqual((self.web / 'model/meshes.bin').read_bytes(), b'immutable model')

    def test_world_readable_backup_is_rejected(self):
        result = self.install()
        backup = Path(result['backup'])
        backup.chmod(0o755)
        with self.assertRaisesRegex(INSTALL.InstallError, 'private and installer-owned'):
            INSTALL.rollback(backup, self.base)

    def make_system_guard(self, static_startup=False):
        return INSTALL.SystemGuard(base=self.base, marker=self.root / 'servo-session.json',
                                   binary=self.root / 'robotd', calibration=self.root / 'calibration.toml',
                                   static_startup=static_startup)

    def test_readiness_rejects_mode_policy_unknown_torque_and_active_work(self):
        safe = {'mode': 'motion', 'phase': 'control', 'policy_enabled': False,
                'native': {'torque_state_confirmed': False,
                           'hd1910': {'restore_pending': False, 'temporary_gains_applied': False}}}
        cases = [dict(safe, mode='commissioning'), dict(safe, policy_enabled=True),
                 dict(safe, policy_motion_enabled=None), dict(safe, policy_motion_enabled=True), dict(safe, native={}),
                 dict(safe, native={'torque_state_confirmed': True}),
                 dict(safe, home_recovery={'stage': 'ramping'}), dict(safe, imu_verification=True),
                 dict(safe, servo_session=True)]
        for state in cases:
            with self.subTest(state=state):
                guard = self.make_system_guard()
                with mock.patch.object(guard, 'bus_status', return_value=state), mock.patch.object(INSTALL.subprocess, 'check_output') as command:
                    with self.assertRaises(INSTALL.InstallError):
                        guard.capture()
                command.assert_not_called()

    def test_readiness_rejects_even_a_broken_maintenance_marker_link(self):
        guard = self.make_system_guard()
        guard.marker.symlink_to(self.root / 'missing')
        with mock.patch.object(guard, 'bus_status') as query:
            with self.assertRaisesRegex(INSTALL.InstallError, 'maintenance session'):
                guard.capture()
        query.assert_not_called()

    def test_readiness_requires_the_motion_control_phase(self):
        safe = {'mode': 'motion', 'policy_enabled': False,
                'native': {'torque_state_confirmed': False,
                           'hd1910': {'restore_pending': False, 'temporary_gains_applied': False}}}
        for extra in ({}, {'phase': 'waiting_for_bus'}, {'phase': 'configuration_error'}, {'phase': None}):
            with self.subTest(phase=extra):
                guard = self.make_system_guard()
                with mock.patch.object(guard, 'bus_status', return_value=dict(safe, **extra)), mock.patch.object(INSTALL.subprocess, 'check_output') as command:
                    with self.assertRaisesRegex(INSTALL.InstallError, 'control phase'):
                        guard.capture()
                command.assert_not_called()

    def test_readiness_waits_for_torque_off_and_hd1910_restoration(self):
        safe = {'mode': 'motion', 'phase': 'control', 'policy_enabled': False,
                'native': {'torque_state_confirmed': False,
                           'hd1910': {'restore_pending': False, 'temporary_gains_applied': False}}}
        cases = [dict(safe, torque_off_pending=True),
                 dict(safe, native={'torque_state_confirmed': False})]
        for hd in ({}, [], {'restore_pending': True, 'temporary_gains_applied': False},
                   {'restore_pending': False, 'temporary_gains_applied': True},
                   {'restore_pending': None, 'temporary_gains_applied': False},
                   {'restore_pending': False, 'temporary_gains_applied': None}):
            cases.append(dict(safe, native={'torque_state_confirmed': False, 'hd1910': hd}))
        for state in cases:
            with self.subTest(state=state):
                guard = self.make_system_guard()
                with mock.patch.object(guard, 'bus_status', return_value=state), mock.patch.object(INSTALL.subprocess, 'check_output') as command:
                    with self.assertRaisesRegex(INSTALL.InstallError, 'torque-off'):
                        guard.capture()
                command.assert_not_called()

    def test_motion_without_commissioning_policy_flag_reaches_pid_verification(self):
        # raw_stream::Bus has policy_enabled; policy_motion_enabled belongs to commissioning.
        safe = {'mode': 'motion', 'phase': 'control', 'policy_enabled': False,
                'native': {'torque_state_confirmed': False,
                           'hd1910': {'restore_pending': False, 'temporary_gains_applied': False}}}
        guard = self.make_system_guard()
        with mock.patch.object(guard, 'bus_status', return_value=safe), mock.patch.object(INSTALL.subprocess, 'check_output', return_value='0\n') as command:
            with self.assertRaisesRegex(INSTALL.InstallError, 'PID is unavailable'):
                guard.capture()
        command.assert_called_once()

    def test_system_guard_only_reads_process_and_hashes(self):
        guard = self.make_system_guard()
        safe = {'mode': 'motion', 'phase': 'control', 'policy_enabled': False,
                'native': {'torque_state_confirmed': False,
                           'hd1910': {'restore_pending': False, 'temporary_gains_applied': False}}}
        read_bytes = Path.read_bytes
        def read(path):
            return b'unchanged binary fixture' if str(path) == '/proc/100/exe' else read_bytes(path)
        backend = {'studio/state.py': INSTALL.digest(b'backend untouched')}
        for extra in ({}, {'policy_motion_enabled': False}):
            with self.subTest(policy_motion_enabled=extra), mock.patch.object(guard, 'bus_status', return_value=dict(safe, **extra)), mock.patch.object(INSTALL.subprocess, 'check_output', return_value='100\n') as command, mock.patch.object(INSTALL.os, 'readlink', return_value=str(guard.binary)), mock.patch.object(Path, 'read_bytes', read), mock.patch.object(INSTALL, 'BACKEND_BASELINE', backend):
                result = guard.capture()
            self.assertEqual(result['robotd_pid'], 100)
            self.assertEqual(result['backend_sha256'], backend)
            self.assertEqual(result['calibration_sha256'], INSTALL.digest(b'private calibration fixture'))
            self.assertEqual(command.call_args.args[0], ['systemctl', 'show', 'robotd', '-p', 'MainPID', '--value'])
            self.assertNotIn('private calibration fixture', json.dumps(result))
        self.assert_outside_unchanged()

    def test_static_startup_only_accepts_the_exact_motion_waiting_phase(self):
        for mode, phase in [('motion', None), ('motion', 'control'), ('motion', 'configuring_bus'),
                            ('motion', 'configuration_error'), ('motion', 'unknown'), ('commissioning', 'waiting_for_bus')]:
            with self.subTest(mode=mode, phase=phase):
                guard = self.make_system_guard(static_startup=True)
                with mock.patch.object(guard, 'bus_status', return_value={'mode': mode, 'phase': phase}), mock.patch.object(INSTALL.subprocess, 'check_output') as command:
                    with self.assertRaises(INSTALL.InstallError):
                        guard.capture()
                command.assert_not_called()

    def test_static_startup_still_rejects_active_work_and_maintenance(self):
        safe = {'mode': 'motion', 'phase': 'waiting_for_bus', 'native': None}
        for extra in ({'policy_enabled': True}, {'policy_motion_enabled': True}, {'policy_enabled': 'unknown'},
                      {'home_recovery': True}, {'imu_verification': 'running'}, {'servo_session': True}):
            with self.subTest(extra=extra):
                guard = self.make_system_guard(static_startup=True)
                with mock.patch.object(guard, 'bus_status', return_value=dict(safe, **extra)), mock.patch.object(INSTALL.subprocess, 'check_output') as command:
                    with self.assertRaises(INSTALL.InstallError):
                        guard.capture()
                command.assert_not_called()
        guard = self.make_system_guard(static_startup=True)
        guard.marker.write_text('maintenance is held')
        with mock.patch.object(guard, 'bus_status') as query:
            with self.assertRaisesRegex(INSTALL.InstallError, 'maintenance'):
                guard.capture()
        query.assert_not_called()

    def test_static_startup_capture_retains_all_identity_and_backend_checks(self):
        guard = self.make_system_guard(static_startup=True)
        state = {'mode': 'motion', 'phase': 'waiting_for_bus', 'policy_enabled': None,
                 'policy_motion_enabled': None, 'native': None}
        backend = {}
        for name in INSTALL.BACKEND_BASELINE:
            if not (self.base / name).exists():
                self.put(self.base, name, ('protected fixture: ' + name).encode())
            backend[name] = INSTALL.digest((self.base / name).read_bytes())
        read_bytes = Path.read_bytes
        def read(path):
            return b'unchanged binary fixture' if str(path) == '/proc/100/exe' else read_bytes(path)
        with mock.patch.object(guard, 'bus_status', return_value=state), mock.patch.object(INSTALL.subprocess, 'check_output', return_value='100\n'), mock.patch.object(INSTALL.os, 'readlink', return_value=str(guard.binary)), mock.patch.object(Path, 'read_bytes', read), mock.patch.object(INSTALL, 'BACKEND_BASELINE', backend):
            result = guard.capture()
            self.assertEqual(result['backend_sha256'], backend)
            self.assertEqual(result['phase'], 'waiting_for_bus')
            self.assertIs(result['static_startup'], True)
            self.assertEqual(result['calibration_sha256'], INSTALL.digest(b'private calibration fixture'))
            self.put(self.base, 'studio/pad.py', b'changed backend must still fail')
            with self.assertRaisesRegex(INSTALL.InstallError, 'backend baseline differs'):
                guard.capture()

    def test_static_startup_install_and_rollback_do_not_claim_hardware_ready(self):
        result = INSTALL.apply(self.bundle, self.base, guard=Guard(static_startup=True))
        self.assertIs(result['static_startup'], True)
        self.assertIs(result['readiness_checked'], False)
        self.assertIs(result['torque_off_verified'], False)
        self.assertIn('--static-startup', result['rollback_command'])
        rolled_back = INSTALL.rollback(result['backup'], self.base, guard=Guard(static_startup=True), apply_changes=True)
        self.assertIs(rolled_back['readiness_checked'], False)
        self.assertIs(rolled_back['torque_off_verified'], False)
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assert_outside_unchanged()

    def test_startup_phase_change_after_publication_restores_original_web(self):
        guard = Guard(static_startup=True, phase_change_at=3)
        with self.assertRaisesRegex(INSTALL.InstallError, 'changed after publication.*web restored'):
            INSTALL.apply(self.bundle, self.base, guard=guard)
        self.assertEqual(INSTALL.inventory(self.web), self.before)
        self.assert_outside_unchanged()

    def test_cli_passes_static_startup_to_apply_and_rollback_guards(self):
        with mock.patch.object(INSTALL.os, 'geteuid', return_value=0), contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(INSTALL, 'apply', return_value={}) as action:
                INSTALL.main([str(self.bundle), '--base', str(self.base), '--apply', '--static-startup'])
                self.assertIs(action.call_args.kwargs['guard'].static_startup, True)
            with mock.patch.object(INSTALL, 'rollback', return_value={}) as action:
                INSTALL.main(['--rollback', str(self.root / 'backup'), '--base', str(self.base), '--apply', '--static-startup'])
                self.assertIs(action.call_args.kwargs['guard'].static_startup, True)

    def test_interrupted_publish_can_be_rolled_back(self):
        result = self.install()
        backup = Path(result['backup'])
        journal = json.loads((backup / 'journal.json').read_text())
        journal['state'] = 'publishing'
        journal.pop('after_guard')
        (backup / 'journal.json').write_text(json.dumps(journal))
        os.rename(backup / 'before-web', backup / 'candidate-web')
        self.assertTrue(INSTALL.rollback(backup, self.base)['preview'])
        self.assertTrue(INSTALL.rollback(backup, self.base, guard=Guard(), apply_changes=True)['rolled_back'])
        self.assertEqual(INSTALL.inventory(self.web), self.before)


if __name__ == '__main__':
    unittest.main()
