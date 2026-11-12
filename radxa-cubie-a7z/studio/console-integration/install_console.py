#!/usr/bin/env python3
"""Preview a checksummed web-only update; --apply atomically publishes it without restarts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import uuid

BASE = Path('/opt/microduck-studio/app')
CALIBRATION = Path('/etc/robot/feetech-ft5/hd1910-calibration.toml')
BINARY = Path('/opt/robot/feetech-ft5-r5/robotd')
MARKER = Path('/var/lib/microduck-studio/servo-session.json')
SHA = re.compile(r'^[0-9a-f]{64}$')
EXTENSIONS = {'.js', '.css', '.html', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.txt'}
MAX_BYTES = 64 * 1024 * 1024
BACKEND_BASELINE = {
    'studio/pad.py': '27bbe0ae137b6e827f5b1d1b8a40a62ae57e670b19d9c36cc498ca884eaf2b5f',
    'studio/robotd.py': '8f3f4412901756fdd29fb6305b89421cbf1a9fa45e33df73e413faaef69c2750',
    'studio/state.py': 'fba77fae1068faed81267bd86687e81bf6851a62e93d1ce860d4ede61fc38c77',
}


class InstallError(RuntimeError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def require(condition, message):
    if not condition:
        raise InstallError(message)


def relative_name(value):
    require(isinstance(value, str) and value and '\\' not in value and '\0' not in value,
            'invalid web-relative filename')
    parts = value.split('/')
    require(all(part and part not in ('.', '..') and not part.startswith('.') and ':' not in part for part in parts),
            'invalid web-relative filename')
    require(parts[0].casefold() not in {'model', 'models'}, 'model assets cannot be changed')
    require(Path(value).suffix.lower() in EXTENSIONS, 'unsupported frontend file type')
    return value


def real_directory(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_dir(), 'expected a real directory')
    return path.resolve()


def read_regular(root, relative):
    """Open beneath an anchored directory, without following payload or target links."""
    parts = relative.split('/')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'links and special files are forbidden')
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            actual = os.fstat(descriptor)
            require((actual.st_dev, actual.st_ino) == (info.st_dev, info.st_ino)
                    and stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1, 'file changed during inspection')
            require(actual.st_size <= MAX_BYTES, 'frontend file is too large')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            after = os.fstat(descriptor)
            require(len(data) <= MAX_BYTES and (after.st_size, after.st_mtime_ns) == (actual.st_size, actual.st_mtime_ns),
                    'file changed during inspection')
            return data, actual
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def scan_tree(root):
    root = real_directory(root)
    files, directories = {}, {'': root.stat()}
    for parent, names, leaves in os.walk(root, followlinks=False):
        for name in names:
            path = Path(parent) / name
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and not path.is_symlink(), 'directory links are forbidden')
            directories[path.relative_to(root).as_posix()] = info
        for name in leaves:
            relative = (Path(parent) / name).relative_to(root).as_posix()
            data, info = read_regular(root, relative)
            files[relative] = (digest(data), info)
    return files, directories


def inventory(root):
    return {name: item[0] for name, item in scan_tree(root)[0].items()}


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def set_metadata(path, info, preserve_times=True):
    # Linux Python builds need not implement path chmod(..., follow_symlinks=False).
    # Keep the no-link guarantee by changing metadata through the opened inode.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        actual = os.fstat(descriptor)
        require(stat.S_ISDIR(actual.st_mode)
                or stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1,
                'metadata target must be a real directory or unlinked regular file')
        if os.geteuid() == 0:
            os.fchown(descriptor, info.st_uid, info.st_gid)
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode))
        if preserve_times:
            os.utime(descriptor, ns=(info.st_atime_ns, info.st_mtime_ns))
    finally:
        os.close(descriptor)


def write_file(root, relative, data, mode=0o644):
    target = root / relative
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o755)
            current.chmod(0o755)
            if os.geteuid() == 0:
                owner = root.stat()
                os.chown(current, owner.st_uid, owner.st_gid)
        require(current.is_dir() and not current.is_symlink(), 'invalid candidate parent')
    # The candidate is private to the transaction. No live file is truncated.
    if target.exists():
        require(target.is_file() and not target.is_symlink() and target.stat().st_nlink == 1, 'invalid candidate file')
        target.unlink()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def copy_tree(source, destination):
    files, directories = scan_tree(source)
    destination.mkdir(mode=0o700)
    for name in sorted(directories, key=lambda item: item.count('/')):
        if name:
            (destination / name).mkdir(exist_ok=True)
    for name, (expected, info) in files.items():
        data, _ = read_regular(source, name)
        require(digest(data) == expected, 'web tree changed while copying')
        write_file(destination, name, data)
        set_metadata(destination / name, info)
    for name, info in sorted(directories.items(), key=lambda item: item[0].count('/'), reverse=True):
        set_metadata(destination / name, info)
        sync_directory(destination / name)
    sync_directory(destination)
    return {name: value[0] for name, value in files.items()}


def write_json(directory, name, data):
    content = (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()
    temporary = name + '.tmp-' + uuid.uuid4().hex
    write_file(directory, temporary, content, 0o600)
    os.replace(directory / temporary, directory / name)
    sync_directory(directory)


def exchange_directories(left, right):
    """An unsupported filesystem must fail closed; there is no non-atomic fallback."""
    require(left.stat().st_dev == right.stat().st_dev, 'web and backup must share a filesystem')
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        operation = library.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = operation(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # RENAME_EXCHANGE
    elif sys.platform == 'darwin' and hasattr(library, 'renamex_np'):
        operation = library.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = operation(os.fsencode(left), os.fsencode(right), 2)  # RENAME_SWAP
    else:
        raise InstallError('atomic directory exchange is unavailable')
    require(result == 0, 'atomic directory exchange failed: ' + errno.errorcode.get(ctypes.get_errno(), 'unknown'))


def validate_manifest(manifest):
    require(isinstance(manifest, dict) and type(manifest.get('schema')) is int and manifest.get('schema') == 1 and manifest.get('kind') == 'console-web'
            and manifest.get('compression') == 'remove-changed-variants', 'unsupported console manifest')
    require(isinstance(manifest.get('release'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', manifest['release']),
            'invalid release identifier')
    changes = manifest.get('files')
    require(isinstance(changes, dict) and 0 < len(changes) <= 512, 'invalid manifest file set')
    for name, checks in changes.items():
        relative_name(name)
        require(isinstance(checks, dict) and set(checks) == {'before_sha256', 'after_sha256'}, 'invalid file checksums')
        for value in checks.values():
            require(value is None or isinstance(value, str) and SHA.fullmatch(value), 'invalid SHA256')
        require(checks['before_sha256'] != checks['after_sha256'], 'manifest includes an unchanged file')
    return manifest


def parse_json(raw, message):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise InstallError(message) from error


def load_manifest(bundle):
    bundle = real_directory(bundle)
    raw, _ = read_regular(bundle, 'manifest.json')
    manifest = validate_manifest(parse_json(raw, 'invalid manifest JSON'))
    changes = manifest['files']
    payload = bundle / 'payload' / 'web'
    require(not (bundle / 'payload').is_symlink(), 'payload links are forbidden')
    contents = inventory(payload) if payload.exists() else {}
    expected = {name: checks['after_sha256'] for name, checks in changes.items() if checks['after_sha256'] is not None}
    require(contents == expected, 'payload files or hashes do not match manifest')
    return bundle, manifest


def inspect_target(base, manifest):
    base = real_directory(base)
    before = inventory(base / 'web')
    changes = manifest['files']
    for name, checks in changes.items():
        require(before.get(name) == checks['before_sha256'], 'installed web differs from manifest: ' + name)
        if checks['before_sha256'] is None:
            require(not os.path.lexists(base / 'web' / name), 'new frontend path already exists: ' + name)
    after = dict(before)
    removed = []
    for name, checks in changes.items():
        if checks['after_sha256'] is None:
            after.pop(name, None)
        else:
            after[name] = checks['after_sha256']
        for extension in ('.gz', '.br'):
            variant = name + extension
            if variant in after:
                removed.append(variant)
                del after[variant]
    return base, before, after, sorted(removed)


class SystemGuard:
    def __init__(self, socket_path='/run/robotd.sock', marker=MARKER, binary=BINARY,
                 calibration=CALIBRATION, expected_mode='motion', base=BASE, static_startup=False):
        self.socket_path, self.marker = str(socket_path), Path(marker)
        self.binary, self.calibration, self.expected_mode = Path(binary), Path(calibration), expected_mode
        self.base = Path(base)
        self.static_startup = static_startup

    def bus_status(self):
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(3)
                connection.connect(self.socket_path)
                connection.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
                with connection.makefile('rb') as stream:
                    row = stream.readline(1 << 20)
                reply = json.loads(row)
            require(isinstance(reply, dict) and reply.get('id') == 1 and isinstance(reply.get('result'), dict) and not reply.get('error'),
                    'robotd status response is unavailable')
            return reply['result']
        except (OSError, ValueError) as error:
            raise InstallError('cannot read current robotd status') from error

    def capture(self):
        require(not os.path.lexists(self.marker), 'maintenance session exists; hand the bus back first')
        state = self.bus_status()
        require(state.get('mode') == self.expected_mode, 'robotd mode is not the expected mode')
        if self.static_startup:
            require(self.expected_mode == 'motion' and state.get('phase') == 'waiting_for_bus',
                    'static-startup requires motion waiting_for_bus')
            # This is a non-interfering web publication, not evidence of torque OFF or healthy hardware.
            require(all(state.get(key) is None or state[key] is False
                        for key in ('policy_enabled', 'policy_motion_enabled')), 'policy must not be active')
        else:
            require(state.get('phase') == 'control', 'robotd is not in the control phase')
            require(state.get('policy_enabled') is False
                    and ('policy_motion_enabled' not in state or state['policy_motion_enabled'] is False),
                    'policy-off is not confirmed')
            native = state.get('native')
            hd = native.get('hd1910') if isinstance(native, dict) else None
            # Match Studio estop.relaxed: torque OFF precedes completion of gain restoration.
            require(isinstance(native, dict) and native.get('torque_state_confirmed') is False
                    and state.get('torque_off_pending') is not True
                    and isinstance(hd, dict) and hd.get('restore_pending') is False
                    and hd.get('temporary_gains_applied') is False,
                    'torque-off is not confirmed')
        require(not state.get('home_recovery') and not state.get('imu_verification') and not state.get('servo_session'),
                'a robot operation or maintenance session is active')
        try:
            output = subprocess.check_output(['systemctl', 'show', 'robotd', '-p', 'MainPID', '--value'],
                                             text=True, timeout=5, stderr=subprocess.DEVNULL).strip()
            require(output.isdecimal() and int(output) > 0, 'robotd PID is unavailable')
            pid = int(output)
            running = Path('/proc') / str(pid) / 'exe'
            require(not os.readlink(running).endswith(' (deleted)'), 'running robotd binary was replaced')
            binary, _ = read_regular(self.binary.parent, self.binary.name)
            calibration, _ = read_regular(self.calibration.parent, self.calibration.name)
            running_sha = digest(running.read_bytes())  # /proc/PID/exe is the intentional kernel link.
            require(running_sha == digest(binary), 'running binary differs from the expected robotd binary')
            require(not os.path.lexists(self.marker), 'maintenance session appeared during inspection')
            backend = {name: digest(read_regular(self.base, name)[0]) for name in BACKEND_BASELINE}
            require(backend == BACKEND_BASELINE, 'A7Z backend baseline differs; no backend files will be changed')
            return {'robotd_pid': pid, 'binary_sha256': running_sha, 'calibration_sha256': digest(calibration),
                    'mode': state['mode'], 'phase': state['phase'], 'static_startup': self.static_startup,
                    'backend_sha256': backend}
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallError('cannot verify robotd identity and calibration fingerprints') from error


@contextmanager
def install_lock(base):
    path = base.parent / '.console-install.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid(), 'invalid installer lock')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError('another console installation is running') from error
        yield
    finally:
        os.close(descriptor)


def preview(bundle, base=BASE):
    _, manifest = load_manifest(bundle)
    base, _, _, variants = inspect_target(base, manifest)
    return {'preview': True, 'release': manifest['release'], 'base': str(base),
            'files': sorted(manifest['files']), 'remove_variants': variants,
            'readiness_checked': False, 'services_restarted': False}


def new_transaction(base):
    parent = base.parent / 'console-backups'
    require(not parent.is_symlink(), 'backup directory links are forbidden')
    parent.mkdir(mode=0o700, exist_ok=True)
    require(stat.S_IMODE(parent.stat().st_mode) & 0o077 == 0 and parent.stat().st_uid == os.geteuid(), 'backup directory must be private and owned by the installer')
    name = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
    directory = parent / name
    directory.mkdir(mode=0o700)
    return directory


def restore_before(base, transaction, expected, source=None):
    restore = transaction / ('restore-' + uuid.uuid4().hex)
    require(copy_tree(source or transaction / 'before-web', restore) == expected, 'backup changed before restore')
    exchange_directories(base / 'web', restore)
    sync_directory(base)
    sync_directory(transaction)
    require(inventory(base / 'web') == expected, 'restored web readback mismatch')


def apply(bundle, base=BASE, guard=None):
    bundle, manifest = load_manifest(bundle)
    base = real_directory(base)
    guard = guard or SystemGuard(base=base)
    with install_lock(base):
        base, before, after, variants = inspect_target(base, manifest)
        fingerprint = guard.capture()
        transaction = new_transaction(base)
        journal = {'schema': 1, 'kind': 'console-web-backup', 'base': str(base), 'manifest': manifest,
                   'before': before, 'after': after, 'before_guard': fingerprint, 'state': 'staging'}
        write_json(transaction, 'journal.json', journal)
        candidate = transaction / 'candidate-web'
        published = False
        try:
            require(copy_tree(base / 'web', candidate) == before, 'web changed during staging')
            for name, checks in manifest['files'].items():
                if checks['after_sha256'] is None:
                    (candidate / name).unlink()
                else:
                    content, _ = read_regular(bundle / 'payload' / 'web', name)
                    require(digest(content) == checks['after_sha256'], 'payload changed during staging')
                    original = (candidate / name).stat() if (candidate / name).exists() else None
                    write_file(candidate, name, content)
                    if original:
                        set_metadata(candidate / name, original, preserve_times=False)
                for extension in ('.gz', '.br'):
                    (candidate / (name + extension)).unlink(missing_ok=True)
            require(inventory(candidate) == after, 'candidate readback mismatch')
            for directory, _, _ in os.walk(candidate, topdown=False):
                sync_directory(directory)
            require(inventory(base / 'web') == before, 'live web changed before publication')
            require(guard.capture() == fingerprint, 'robotd or calibration changed before publication')
            journal['state'] = 'publishing'
            write_json(transaction, 'journal.json', journal)
            exchange_directories(base / 'web', candidate)
            published = True
            sync_directory(base)
            sync_directory(transaction)
            os.rename(candidate, transaction / 'before-web')
            sync_directory(transaction)
            require(inventory(base / 'web') == after, 'installed web readback mismatch')
            journal['after_guard'] = guard.capture()
            require(journal['after_guard'] == fingerprint, 'robotd or calibration changed after publication')
            journal['state'] = 'installed'
            write_json(transaction, 'journal.json', journal)
        except BaseException as error:
            if published:
                # Static rollback is also safe when the post-check detects unrelated robot activity.
                original = transaction / 'before-web'
                if not original.exists():
                    original = candidate
                try:
                    restore_before(base, transaction, before, source=original)
                except BaseException as recovery_error:
                    journal.update(state='failed-rollback', original_web=original.name)
                    write_json(transaction, 'journal.json', journal)
                    raise InstallError('automatic web rollback failed; inspect backup: ' + str(transaction), backup=str(transaction), web_restored=False) from recovery_error
            journal['state'] = 'failed-restored' if published else 'failed-unpublished'
            write_json(transaction, 'journal.json', journal)
            raise InstallError(str(error) + '; ' + ('web restored' if published else 'web unchanged') + '; backup: ' + str(transaction), backup=str(transaction), web_restored=published) from error
        static_startup = fingerprint.get('static_startup', False)
        return {'installed': True, 'backup': str(transaction), 'release': manifest['release'],
                'removed_variants': variants, 'fingerprints': fingerprint, 'services_restarted': False,
                'static_startup': static_startup, 'readiness_checked': not static_startup,
                'torque_off_verified': not static_startup,
                'rollback_command': ['python3', str(Path(__file__).resolve()), '--rollback', str(transaction), '--base', str(base), '--apply']
                                    + (['--static-startup'] if static_startup else [])}


def inspect_rollback(backup, base):
    backup, base = real_directory(backup), real_directory(base)
    raw, _ = read_regular(backup, 'journal.json')
    require(backup.parent == base.parent / 'console-backups' and backup.stat().st_uid == os.geteuid()
            and stat.S_IMODE(backup.stat().st_mode) & 0o077 == 0, 'rollback backup must be private and installer-owned')
    journal = parse_json(raw, 'invalid rollback journal')
    require(isinstance(journal, dict) and journal.get('schema') == 1 and journal.get('kind') == 'console-web-backup'
            and journal.get('base') == str(base) and journal.get('state') in ('installed', 'publishing'), 'backup is not a published console transaction')
    require(isinstance(journal.get('before'), dict) and isinstance(journal.get('after'), dict), 'invalid backup inventory')
    manifest = validate_manifest(journal.get('manifest'))
    projected = dict(journal['before'])
    for name, checks in manifest['files'].items():
        require(projected.get(name) == checks['before_sha256'], 'backup manifest mismatch')
        if checks['after_sha256'] is None:
            projected.pop(name, None)
        else:
            projected[name] = checks['after_sha256']
        projected.pop(name + '.gz', None)
        projected.pop(name + '.br', None)
    require(projected == journal['after'], 'backup contains changes outside its manifest')
    # A crash immediately after the atomic exchange can leave the original at candidate-web.
    original = backup / ('before-web' if (backup / 'before-web').exists() else 'candidate-web')
    require(original.exists() and inventory(original) == journal['before'], 'backup hash mismatch')
    journal['original_web'] = original.name
    require(inventory(base / 'web') == journal['after'], 'current web differs from installed release; rollback refused')
    return backup, base, journal


def rollback(backup, base=BASE, guard=None, apply_changes=False):
    backup, base, journal = inspect_rollback(backup, base)
    if not apply_changes:
        return {'preview': True, 'rollback': str(backup), 'files': sorted(journal['manifest']['files']),
                'readiness_checked': False, 'services_restarted': False}
    guard = guard or SystemGuard(base=base)
    with install_lock(base):
        backup, base, journal = inspect_rollback(backup, base)
        original_journal = dict(journal)
        fingerprint = guard.capture()
        candidate = backup / ('rollback-' + uuid.uuid4().hex)
        require(copy_tree(backup / journal['original_web'], candidate) == journal['before'], 'rollback copy mismatch')
        require(guard.capture() == fingerprint, 'robotd or calibration changed before rollback')
        exchanged = False
        try:
            exchange_directories(base / 'web', candidate)
            exchanged = True
            sync_directory(base)
            sync_directory(backup)
            require(inventory(base / 'web') == journal['before'], 'rollback readback mismatch')
            after_guard = guard.capture()
            require(after_guard == fingerprint, 'robotd or calibration changed during rollback')
            journal.update(state='rolled-back', rollback_before_guard=fingerprint, rollback_after_guard=after_guard)
            write_json(backup, 'journal.json', journal)
        except BaseException:
            if exchanged:
                exchange_directories(base / 'web', candidate)
                sync_directory(base)
                sync_directory(backup)
                # write_json may have replaced the journal before its directory fsync failed.
                # Restoring the installed tree must also restore a retryable transaction state.
                write_json(backup, 'journal.json', original_journal)
            raise
        static_startup = fingerprint.get('static_startup', False)
        return {'rolled_back': True, 'backup': str(backup), 'fingerprints': fingerprint, 'services_restarted': False,
                'static_startup': static_startup, 'readiness_checked': not static_startup,
                'torque_off_verified': not static_startup}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, nargs='?')
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--static-startup', action='store_true',
                        help='allow web-only publication in motion waiting_for_bus; does not verify torque or hardware readiness')
    parser.add_argument('--rollback', type=Path, metavar='BACKUP')
    args = parser.parse_args(argv)
    require(bool(args.bundle) != bool(args.rollback), 'provide a bundle or --rollback BACKUP')
    if args.apply:
        require(os.geteuid() == 0, '--apply requires root')
    guard = SystemGuard(base=args.base, static_startup=args.static_startup) if args.apply else None
    result = rollback(args.rollback, args.base, guard=guard, apply_changes=args.apply) if args.rollback else (
        apply(args.bundle, args.base, guard=guard) if args.apply else preview(args.bundle, args.base))
    if not args.apply and args.static_startup:
        result['static_startup_requested'] = True
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (InstallError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error), **getattr(error, 'details', {})}), file=sys.stderr)
        raise SystemExit(1)
