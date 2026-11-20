#!/usr/bin/env python3
"""Preinstall a verified runtime into a separately mounted offline ext4 image.

Run as Linux root with Python >= 3.11. Dependencies must already be installed in
this image by the build pipeline. No service is started and no apt command runs.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import tomllib

TOP_LEVEL = (
    'opt/robot/feetech-ft5-r5', 'etc/robot/feetech-ft5',
    'usr/local/lib/orangepi-control', 'opt/microduck-studio', 'opt/11quanzhi-source',
    'etc/systemd/system/robotd.service',
    'etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf',
    'etc/systemd/system/microduck-studio.service', 'etc/sudoers.d/microduck-studio',
    'usr/local/sbin/microduck-helper',
)
SERVICES = ('orangepi-provision.service', 'robotd.service', 'microduck-studio.service')
UNCONFIGURED = '/dev/orangepi-robot-uart-unconfigured'
STATE_DIRS = ('var/lib/microduck-studio', 'var/log/microduck-studio')
FORBIDDEN = {'password.json', 'sessions.json', 'authorized_keys', 'known_hosts',
             'id_rsa', 'id_ed25519', 'credentials.json', '.env', '__pycache__', '.DS_Store'}


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or path.as_posix() != value:
        raise ValueError('Unsafe manifest path')
    return path


def target_path(root, relative):
    path = root / relative
    # Absolute OS symlinks must never resolve against the container's own root.
    for parent in [path.parent, *path.parent.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Symlink parent in offline destination: ' + str(relative))
    return path


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


def verify_records(root, records, *, ownership=False):
    for item in records:
        path = target_path(root, item['path'])
        if 'symlink' in item:
            # Symlink mode is fixed at 0777 on Linux and may be 0755 in a
            # manifest produced on macOS. Only its target and owner matter.
            if not path.is_symlink() or str(path.readlink()) != item['symlink']:
                raise ValueError('Symlink readback mismatch: ' + item['path'])
        elif (not path.is_file() or path.is_symlink() or sha256(path) != item['sha256']
              or path.stat().st_size != item['bytes'] or mode(path) != item['mode']):
            raise ValueError('File content/mode readback mismatch: ' + item['path'])
        if ownership and (path.lstat().st_uid != 0 or path.lstat().st_gid != 0):
            raise ValueError('Runtime file is not root-owned: ' + item['path'])


def bundle_manifest(bundle):
    payload = bundle / 'payload'
    if payload.is_symlink() or not payload.is_dir():
        raise ValueError('Expected a prepared runtime payload directory')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    records = manifest['files']
    expected = [item['path'] for item in records]
    if len(expected) != len(set(expected)):
        raise ValueError('Duplicate manifest path')
    actual = {p.relative_to(payload).as_posix() for p in payload.rglob('*')
              if not p.is_dir() or p.is_symlink()}
    if actual != set(expected):
        raise ValueError('Payload file set differs from manifest')
    for item in records:
        relative = relative_path(item['path'])
        if not any(str(relative) == top or str(relative).startswith(top + '/') for top in TOP_LEVEL):
            raise ValueError('Unexpected runtime destination: ' + str(relative))
        if any(part in FORBIDDEN or part.endswith(('.pyc', '.nmconnection')) for part in relative.parts):
            raise ValueError('Credential/state/cache path in payload')
        path = payload / str(relative)
        if 'symlink' in item:
            link = Path(item['symlink'])
            if link.is_absolute() or not path.resolve().is_relative_to(payload.resolve()):
                raise ValueError('Payload symlink escapes its tree')
        elif not isinstance(item['mode'], int) or item['mode'] & ~0o777:
            raise ValueError('Invalid or privileged payload file mode')
    verify_records(payload, records)
    verify_guards(payload)
    return manifest


def verify_guards(root):
    params = tomllib.loads((root / 'etc/robot/feetech-ft5/robotd.toml').read_text())
    calibration = tomllib.loads((root / 'etc/robot/feetech-ft5/hd1910-calibration.toml').read_text())
    if params['bus']['port'] != UNCONFIGURED or params['policy']['enabled'] is not False:
        raise ValueError('Expected an unconfigured bus and disabled policy')
    if (any(calibration.get(key) is not False for key in
            ('motion_enabled', 'imu_mount_verified', 'units_verified'))
            or len(calibration.get('joints', [])) != 15
            or any(joint.get('calibrated') is not False for joint in calibration['joints'])):
        raise ValueError('Expected the uncommissioned calibration template')
    unit = (root / 'etc/systemd/system/robotd.service').read_text()
    commands = [shlex.split(line.partition('=')[2]) for line in unit.splitlines()
                if line.startswith('ExecStart=')]
    if len(commands) != 1 or '--read-only' not in commands[0]:
        raise ValueError('robotd service must start in read-only mode')
    studio = (root / 'etc/systemd/system/microduck-studio.service').read_text()
    if '--dev-autologin' in studio or '--servo-sim' in studio or UNCONFIGURED not in studio:
        raise ValueError('Studio must retain normal authentication and unconfigured serial')


def validate_root(root):
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise PermissionError('Run as root in a Linux image-build environment')
    if root == Path('/') or root.is_symlink() or not os.path.ismount(root):
        raise ValueError('Offline root must be a separate non-/ mountpoint')
    filesystems = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields, kind = line.split(' - ', 1)
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields.split()[4])
        filesystems[mount] = kind.split()[0]
    if filesystems.get(str(root)) != 'ext4':
        raise ValueError('Offline root must be an ext4 mount')
    release = target_path(root, 'etc/orangepi-release')
    if release.is_symlink() or 'BOARD=orangepizero3w\n' not in release.read_text():
        raise ValueError('Wrong offline board image')
    for name in ('etc/passwd', 'etc/group', 'etc/gshadow'):
        path = target_path(root, name)
        if not path.is_file() or path.is_symlink():
            raise ValueError('Expected regular offline account files')
        if path.stat().st_uid != 0 or mode(path) & 0o022:
            raise ValueError('Offline account files must be root-owned and not publicly writable')
    placeholder = target_path(root, UNCONFIGURED.lstrip('/'))
    if placeholder.exists() or placeholder.is_symlink():
        raise ValueError('The deliberately unconfigured UART must not exist')


def account_plan(root):
    passwd = [line.split(':') for line in (root / 'etc/passwd').read_text().splitlines()]
    users = [row for row in passwd if len(row) == 7 and row[0] == 'orangepi']
    if len(users) != 1 or int(users[0][2]) == 0:
        raise ValueError('Expected one non-root orangepi user')
    uid, gid = int(users[0][2]), int(users[0][3])
    groups = [line.split(':') for line in (root / 'etc/group').read_text().splitlines()]
    shadows = [line.split(':') for line in (root / 'etc/gshadow').read_text().splitlines()]
    for rows in (groups, shadows):
        if any(len(row) != 4 for row in rows) or len({r[0] for r in rows}) != len(rows):
            raise ValueError('Malformed or duplicate group records')
    by_name = {row[0]: row for row in groups}
    if 'dialout' not in by_name or not any(int(row[2]) == gid for row in groups):
        raise ValueError('Expected dialout and the orangepi primary group')
    used = {int(row[2]) for row in groups}
    if 'robot' not in by_name:
        available = next((number for number in range(100, 1000) if number not in used), None)
        if available is None:
            raise ValueError('No free system group ID')
        groups.append(['robot', 'x', str(available), ''])
        by_name['robot'] = groups[-1]
    robot_gid = int(by_name['robot'][2])
    if robot_gid == 0 or robot_gid >= 1000 or sum(int(r[2]) == robot_gid for r in groups) != 1:
        raise ValueError('robot must have a unique non-root system group ID')
    shadow_names = {row[0]: row for row in shadows}
    for name in ('robot', 'dialout'):
        row = by_name[name]
        row[3] = ','.join(dict.fromkeys([member for member in row[3].split(',') if member] + ['orangepi']))
        if name not in shadow_names:
            shadows.append([name, '!', '', ''])
            shadow_names[name] = shadows[-1]
        shadow_names[name][3] = row[3]
    return {'uid': uid, 'gid': gid, 'robot_gid': robot_gid}, {
        'etc/group': '\n'.join(':'.join(row) for row in groups) + '\n',
        'etc/gshadow': '\n'.join(':'.join(row) for row in shadows) + '\n',
    }


def replace_account_file(path, content):
    original = path.stat()
    descriptor, name = tempfile.mkstemp(prefix='.microduck-group-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def directory(root, relative, permission=0o755, uid=0, gid=0):
    target = target_path(root, relative)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError('Expected an offline directory: ' + relative)
    missing = []
    parent = target
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o755)
        os.chown(parent, 0, 0)
        parent.chmod(0o755)
    os.chown(target, uid, gid)
    target.chmod(permission)
    return target


def runtime_directories(account):
    return [(name, 0o700, account['uid'], account['gid']) for name in STATE_DIRS] + [
        ('var/lib/robot', 0o755, 0, 0), ('var/lib/microduck-helper', 0o755, 0, 0),
        ('var/lib/microduck-helper/imu-recalibrate', 0o755, 0, 0),
        ('var/lib/orangepi-migration', 0o755, 0, 0),
    ]


def empty_state(root):
    for name in STATE_DIRS:
        path = target_path(root, name)
        if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise ValueError('Expected fresh empty Studio state and logs')


def secure_runtime_parents(root, records):
    """The official base has some 0775 ancestors; runtime code must not inherit them."""
    parents = set()
    for item in records:
        for parent in PurePosixPath(item['path']).parents:
            if parent == PurePosixPath('.'):
                break
            parents.add(parent)
    for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
        directory(root, str(parent), 0o755, 0, 0)


def install_payload(root, bundle, manifest, account, changes):
    for name in (*TOP_LEVEL, 'usr/local/bin/robotctl'):
        path = target_path(root, name)
        if path.exists() or path.is_symlink():
            raise ValueError('Refusing to overwrite an existing application: /' + name)
    empty_state(root)
    provision = target_path(root, 'etc/systemd/system/orangepi-provision.service')
    if not provision.is_file() or provision.is_symlink():
        raise ValueError('Install the offline provisioning overlay first')
    secure_runtime_parents(root, manifest['files'])
    for item in manifest['files']:
        source = bundle / 'payload' / item['path']
        target = target_path(root, item['path'])
        directory(root, str(PurePosixPath(item['path']).parent))
        if 'symlink' in item:
            target.symlink_to(item['symlink'])
            os.lchown(target, 0, 0)
        else:
            shutil.copyfile(source, target)
            os.chown(target, 0, 0)
            target.chmod(item['mode'])
    for name, text in changes.items():
        replace_account_file(root / name, text)
    for entry in runtime_directories(account):
        directory(root, *entry)
    directory(root, 'usr/local/bin')
    link = root / 'usr/local/bin/robotctl'
    link.symlink_to('/opt/robot/feetech-ft5-r5/robotctl')
    os.lchown(link, 0, 0)
    wants = directory(root, 'etc/systemd/system/multi-user.target.wants')
    for service in SERVICES:
        link = wants / service
        expected = '/etc/systemd/system/' + service
        if link.is_symlink() and str(link.readlink()) == expected:
            continue
        if link.exists() or link.is_symlink():
            raise ValueError('Unexpected existing service enable link: ' + service)
        link.symlink_to(expected)
        os.lchown(link, 0, 0)
    (root / 'var/lib/orangepi-migration/installed-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    os.chown(root / 'var/lib/orangepi-migration/installed-manifest.json', 0, 0)
    (root / 'var/lib/orangepi-migration/installed-manifest.json').chmod(0o644)


def verify_image(root, manifest, account):
    verify_records(root, manifest['files'], ownership=True)
    verify_guards(root)
    directories = set()
    for item in manifest['files']:
        for parent in (root / item['path']).parents:
            if parent == root:
                break
            directories.add(parent)
    for path in directories:
        if path.stat().st_uid != 0 or path.stat().st_gid != 0 or mode(path) & 0o022:
            raise ValueError('Runtime directory must be root-owned and not publicly writable: /'
                             + path.relative_to(root).as_posix())
    actual = set()
    for name in TOP_LEVEL:
        path = root / name
        paths = path.rglob('*') if path.is_dir() else [path]
        actual.update(p.relative_to(root).as_posix() for p in paths if p.is_file() or p.is_symlink())
    if actual != {item['path'] for item in manifest['files']}:
        raise ValueError('Unexpected installed runtime files')
    empty_state(root)
    current, changes = account_plan(root)
    if current != account or any((root / name).read_text() != text for name, text in changes.items()):
        raise ValueError('Offline account/group readback mismatch')
    for name, permission, uid, gid in runtime_directories(account):
        path = target_path(root, name)
        if not path.is_dir() or path.is_symlink() or mode(path) != permission or (path.stat().st_uid, path.stat().st_gid) != (uid, gid):
            raise ValueError('Runtime directory owner/mode mismatch: /' + name)
    for service in SERVICES:
        link = root / 'etc/systemd/system/multi-user.target.wants' / service
        if not link.is_symlink() or str(link.readlink()) != '/etc/systemd/system/' + service:
            raise ValueError('Service is not enabled: ' + service)
    link = root / 'usr/local/bin/robotctl'
    if not link.is_symlink() or str(link.readlink()) != '/opt/robot/feetech-ft5-r5/robotctl':
        raise ValueError('robotctl link readback mismatch')
    installed = root / 'var/lib/orangepi-migration/installed-manifest.json'
    if (installed.is_symlink() or json.loads(installed.read_text()) != manifest
            or mode(installed) != 0o644 or (installed.stat().st_uid, installed.stat().st_gid) != (0, 0)):
        raise ValueError('Installed manifest readback mismatch')


def chroot_checks(root):
    checks = {}
    def run(label, command):
        result = subprocess.run(['chroot', str(root), *command], capture_output=True, text=True)
        if result.returncode or (label.startswith('ldd-') and 'not found' in result.stdout):
            raise RuntimeError('Offline chroot check failed: ' + label)
        checks[label] = 'passed'
        return result.stdout
    for executable in ('fuser', 'arecord', 'v4l2-ctl', 'ffmpeg', 'nmcli', 'ip', 'dnsmasq'):
        if not any((root / base / executable).is_file() for base in ('usr/bin', 'usr/sbin', 'bin', 'sbin')):
            raise RuntimeError('Image is missing a runtime dependency: ' + executable)
    robot = '/opt/robot/feetech-ft5-r5/'
    python = '/opt/microduck-studio/python/bin/python3.13'
    for executable in ('robotd', 'padd', 'mediad', 'robotctl'):
        run('ldd-' + executable, ['/usr/bin/ldd', robot + executable])
    run('ldd-python', ['/usr/bin/ldd', python])
    run('sudoers', ['/usr/sbin/visudo', '-cf', '/etc/sudoers.d/microduck-studio'])
    run('service-units', ['/usr/bin/systemd-analyze', 'verify',
        *['/etc/systemd/system/' + service for service in SERVICES]])
    run('python-standard-library', [python, '-B', '-I', '-c', 'import ssl,sqlite3,tomllib'])
    run('robotd-help', [robot + 'robotd', '--help'])
    temporary = root / 'tmp/microduck-preinstall-check'
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('Offline selfcheck temporary directory already exists')
    temporary.mkdir(mode=0o700)
    try:
        run('studio-selfcheck', [python, '-B', '-I', '/opt/microduck-studio/app/run.py',
            '--selfcheck', '--robotd', 'none', '--configd', 'none', '--tof', 'none',
            '--camera-socket', 'none', '--state-dir', '/tmp/microduck-preinstall-check/state',
            '--log-dir', '/tmp/microduck-preinstall-check/logs'])
        run('policy-validation', ['/usr/bin/env',
            'ORT_DYLIB_PATH=' + robot + 'onnxruntime/libonnxruntime.so.1.28.0',
            robot + 'robotd', 'validate-policy', robot + 'models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx'])
    finally:
        shutil.rmtree(temporary)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root, bundle, report = args.root.absolute(), args.bundle.resolve(), args.report.absolute()
    if report.exists() or report.is_symlink() or report.is_relative_to(root):
        raise ValueError('Report must be a new external file')
    validate_root(root)
    manifest = bundle_manifest(bundle)
    account, changes = account_plan(root)
    if not args.verify_only:
        install_payload(root, bundle, manifest, account, changes)
    verify_image(root, manifest, account)
    checks = chroot_checks(root)
    verify_image(root, manifest, account)
    result = {'status': 'preinstalled-and-offline-readback-verified', 'release': manifest['release'],
              'payload_files_verified': len(manifest['files']),
              'payload_manifest_sha256': sha256(bundle / 'manifest.json'),
              'file_hash_mode_and_root_ownership_verified': True, 'accounts': account,
              'enabled_services': list(SERVICES), 'studio_state_empty': True,
              'policy_enabled': False, 'robotd_mode': 'read_only', 'uart': UNCONFIGURED,
              'chroot_checks': checks, 'services_started': False, 'apt_executed': False,
              'board_boot_test': 'not-performed'}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
