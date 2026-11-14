"""Offline filesystem contracts; no mounts, chroot or hardware are used."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('preinstall', Path(__file__).with_name('preinstall_runtime.py'))
PREINSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREINSTALL)


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o644)
    return path


def accounts(root):
    put(root, 'etc/passwd', 'root:x:0:0:root:/root:/bin/bash\norangepi:x:1000:1000:Orange Pi:/home/orangepi:/bin/bash\n')
    put(root, 'etc/group', 'root:x:0:\ndialout:x:20:existing\nusers:x:100:\norangepi:x:1000:\n')
    put(root, 'etc/gshadow', 'root:!::\ndialout:!::existing\nusers:!::\norangepi:!::\n')


def bundle(root):
    payload = root / 'payload'
    put(payload, 'etc/robot/feetech-ft5/robotd.toml', '[bus]\nport="' + PREINSTALL.UNCONFIGURED + '"\n[policy]\nenabled=false\n')
    put(payload, 'etc/robot/feetech-ft5/hd1910-calibration.toml',
        'motion_enabled=false\nimu_mount_verified=false\nunits_verified=false\n' +
        '\n'.join('[[joints]]\ncalibrated=false' for _ in range(15)))
    put(payload, 'etc/systemd/system/robotd.service', '[Service]\nExecStart=/opt/robot/feetech-ft5-r5/robotd --read-only\n')
    put(payload, 'etc/systemd/system/microduck-studio.service',
        '[Service]\nExecStart=python run.py --servo-port ' + PREINSTALL.UNCONFIGURED + '\n')
    library = put(payload, 'opt/microduck-studio/app/example.py', 'print("source")\n')
    (library.parent / 'reference.py').symlink_to('example.py')
    records = []
    for path in sorted(payload.rglob('*')):
        if not path.is_file() and not path.is_symlink():
            continue
        record = {'path': path.relative_to(payload).as_posix(), 'mode': PREINSTALL.mode(path)}
        if path.is_symlink():
            record['symlink'] = str(path.readlink())
        else:
            record.update(sha256=PREINSTALL.sha256(path), bytes=path.stat().st_size)
        records.append(record)
    (root / 'manifest.json').write_text(json.dumps({'release': 'test', 'files': records}))
    return payload


class AccountTests(unittest.TestCase):
    def test_selects_free_system_gid_and_preserves_existing_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts(root)
            before = (root / 'etc/group').read_bytes()
            account, changes = PREINSTALL.account_plan(root)
            self.assertEqual(account, {'uid': 1000, 'gid': 1000, 'robot_gid': 101})
            self.assertIn('dialout:x:20:existing,orangepi\n', changes['etc/group'])
            self.assertIn('robot:x:101:orangepi\n', changes['etc/group'])
            self.assertIn('robot:!::orangepi\n', changes['etc/gshadow'])
            self.assertEqual((root / 'etc/group').read_bytes(), before)
            for name, content in changes.items():
                (root / name).write_text(content)
            self.assertEqual(PREINSTALL.account_plan(root), (account, changes))

    def test_rejects_robot_gid_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts(root)
            with (root / 'etc/group').open('a') as stream:
                stream.write('robot:x:100:\n')
            with self.assertRaises(ValueError):
                PREINSTALL.account_plan(root)

    def test_rejects_duplicate_group_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts(root)
            with (root / 'etc/group').open('a') as stream:
                stream.write('dialout:x:21:\n')
            with self.assertRaises(ValueError):
                PREINSTALL.account_plan(root)


class PayloadTests(unittest.TestCase):
    def test_accepts_cross_platform_symlink_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = bundle(root)
            manifest = json.loads((root / 'manifest.json').read_text())
            link = next(item for item in manifest['files'] if 'symlink' in item)
            for recorded_mode in (0o755, 0o777):
                with self.subTest(recorded_mode=oct(recorded_mode)):
                    link['mode'] = recorded_mode
                    PREINSTALL.verify_records(payload, manifest['files'])

    def test_accepts_checked_files_and_relative_library_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle(root)
            manifest = PREINSTALL.bundle_manifest(root)
            self.assertEqual(len(manifest['files']), 6)

    def test_rejects_content_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = bundle(root)
            (payload / 'opt/microduck-studio/app/example.py').write_text('changed')
            with self.assertRaises(ValueError):
                PREINSTALL.bundle_manifest(root)

    def test_rejects_unlisted_credential_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = bundle(root)
            put(payload, 'opt/microduck-studio/app/password.json', 'test-state')
            with self.assertRaises(ValueError):
                PREINSTALL.bundle_manifest(root)

    def test_rejects_escaping_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = bundle(root)
            link = payload / 'opt/microduck-studio/app/reference.py'
            link.unlink()
            link.symlink_to('/etc/passwd')
            manifest = json.loads((root / 'manifest.json').read_text())
            next(item for item in manifest['files'] if 'symlink' in item)['symlink'] = '/etc/passwd'
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                PREINSTALL.bundle_manifest(root)

    def test_rejects_movement_policy_or_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = bundle(root)
            params = payload / 'etc/robot/feetech-ft5/robotd.toml'
            params.write_text(params.read_text().replace('enabled=false', 'enabled=true'))
            with self.assertRaises(ValueError):
                PREINSTALL.verify_guards(payload)
            params.write_text(params.read_text().replace('enabled=true', 'enabled=false'))
            service = payload / 'etc/systemd/system/robotd.service'
            service.write_text(service.read_text().replace('--read-only', '--enable-policy'))
            with self.assertRaises(ValueError):
                PREINSTALL.verify_guards(payload)


class DestinationTests(unittest.TestCase):
    def test_secures_existing_code_ancestors_without_changing_private_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('etc', 'opt', 'usr', 'usr/local'):
                path = root / name
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o775)
            state = root / 'var/lib/microduck-studio'
            state.mkdir(parents=True)
            state.chmod(0o700)
            records = [{'path': path} for path in (
                'etc/robot/robotd.toml', 'opt/robot/bin/robotd', 'usr/local/sbin/microduck-helper')]
            with patch.object(PREINSTALL.os, 'chown'):
                PREINSTALL.secure_runtime_parents(root, records)
            for name in ('etc', 'opt', 'usr', 'usr/local'):
                self.assertEqual(PREINSTALL.mode(root / name), 0o755)
            self.assertEqual(PREINSTALL.mode(state), 0o700)

    def test_rejects_symlink_parent_in_offline_root(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            (root / 'opt').symlink_to(outside)
            with self.assertRaises(ValueError):
                PREINSTALL.target_path(root, 'opt/microduck-studio/app/run.py')
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_refuses_existing_password_without_reading_or_modifying_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            password = put(root, 'var/lib/microduck-studio/password.json', 'existing-device-state')
            with self.assertRaises(ValueError):
                PREINSTALL.empty_state(root)
            self.assertEqual(password.read_text(), 'existing-device-state')

    def test_refuses_the_running_root(self):
        with patch.object(PREINSTALL.platform, 'system', return_value='Linux'), \
                patch.object(PREINSTALL.os, 'geteuid', return_value=0):
            with self.assertRaises(ValueError):
                PREINSTALL.validate_root(Path('/'))


if __name__ == '__main__':
    unittest.main()
