#!/usr/bin/env python3
"""Prepare a clean rootfs overlay; no board, disk or running service is changed."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
BOARD = HERE.parent
PROJECT = BOARD.parent
STUDIO_SHA256 = 'db8bcde8d9823c3e10abce1a015cd2422b81c25b279ac4f6a516d51c1fd10590'
STUDIO = BOARD / 'studio/artifacts/cubie-studio.tar.gz'
SOURCE = PROJECT.parent / '10fanhao_duck/studio-replica'
BACKEND = {
    'studio/pad.py': '27bbe0ae137b6e827f5b1d1b8a40a62ae57e670b19d9c36cc498ca884eaf2b5f',
    'studio/robotd.py': '8f3f4412901756fdd29fb6305b89421cbf1a9fa45e33df73e413faaef69c2750',
    'studio/state.py': 'fba77fae1068faed81267bd86687e81bf6851a62e93d1ce860d4ede61fc38c77',
}
FIRSTBOOT = '''[Unit]
Description=Prepare a new Cubie A7Z duck
After=local-fs.target rsetup.service NetworkManager.service
Wants=rsetup.service NetworkManager.service
Before=robotd.service microduck-studio.service cubie-provision.service
ConditionPathExists=!/var/lib/cubie-duck/firstboot.done
RequiresMountsFor=/config

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/cubie-duck/firstboot.py
RemainAfterExit=yes
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
'''
STUDIO_UNIT = '''[Unit]
Description=Microduck Studio on Cubie A7Z
After=network.target cubie-duck-firstboot.service
Requires=cubie-duck-firstboot.service

[Service]
Type=exec
User=radxa
Group=radxa
SupplementaryGroups=robot dialout audio
WorkingDirectory=/opt/microduck-studio/app
ExecStart=/opt/microduck-studio/python/bin/python3.13 -I run.py --listen 0.0.0.0:8000 --state-dir /var/lib/microduck-studio --log-dir /var/log/microduck-studio --configd none --tof none --camera-socket none
Restart=on-failure
RestartSec=5
Nice=10
CPUWeight=20
CPUQuota=60%
MemoryMax=300M

[Install]
WantedBy=multi-user.target
'''


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def write(root, name, text, mode=0o644):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def link(root, name, target):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def without_uart_console(text):
    """Keep all other boot arguments, including root UUID and rescue selection."""
    pattern = r'(?<!\S)(?:console=tty(?:AS0|S2)(?:,[^\s]+)?|earlycon(?:=[^\s]+)?|earlyprintk=[^\s]+)(?=\s|$)'
    return re.sub(pattern, '', text)


def read_rootfs(tool, rootfs, name):
    result = subprocess.run([str(tool), '-R', 'cat ' + name, str(rootfs)],
                            check=True, capture_output=True, text=True)
    if 'File not found' in result.stderr or not result.stdout:
        raise ValueError('required base file missing: ' + name)
    return result.stdout


def unpack_studio(archive, root):
    if archive.is_symlink() or digest(archive) != STUDIO_SHA256:
        raise ValueError('Studio archive SHA256 mismatch')
    base = root / 'opt/microduck-studio'
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] not in ('app', 'python')):
                raise ValueError('invalid Studio archive member: ' + member.name)
            if path.parts[0] == 'app':
                # Runtime only: exclude old logs, tests, simulators and local metadata.
                if len(path.parts) == 1:
                    continue
                if path.parts[1] not in ('deps', 'deploy', 'studio', 'web', 'run.py', 'LICENSE'):
                    continue
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            target = base.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym():
                resolved = (target.parent / member.linkname).resolve()
                if not resolved.is_relative_to(base.resolve()):
                    raise ValueError('Studio symlink escapes installation')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            else:
                raise ValueError('unsupported Studio archive entry: ' + member.name)


def prepare(args):
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('output exists; choose a new directory')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.image-overlay-', dir=args.output.parent))
    root = work / 'root'
    try:
        # The runtime builder checks upstream and A7Z binary hashes independently.
        subprocess.run(['python3', str(HERE / 'prepare_runtime.py'), '--output', str(root)], check=True)
        unpack_studio(args.studio_archive, root)
        rebuilt = work / 'studio'
        subprocess.run(['python3', str(BOARD / 'studio/console-integration/prepare_console.py'),
                        str(args.studio_source), '--output', str(rebuilt)], check=True,
                       capture_output=True, text=True)
        app = root / 'opt/microduck-studio/app'
        # A clean pinned export prevents local development state entering the image.
        for directory in ('studio', 'web', 'deploy'):
            shutil.rmtree(app / directory)
            shutil.copytree(rebuilt / directory, app / directory,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for relative, expected in BACKEND.items():
            if digest(app / relative) != expected:
                raise ValueError('A7Z Studio backend mismatch: ' + relative)
        for path in ('/boot/extlinux/extlinux.conf', '/etc/kernel/cmdline'):
            text = read_rootfs(args.debugfs, args.rootfs, path)
            if 'console=ttyAS0' not in text or 'earlycon' not in text:
                raise ValueError('unexpected base boot configuration; review manually')
            write(root, path.lstrip('/'), without_uart_console(text))
        write(root, 'etc/machine-id', '', 0o444)
        link(root, 'var/lib/dbus/machine-id', '/etc/machine-id')
        write(root, 'usr/local/lib/cubie-duck/firstboot.py', (HERE / 'firstboot.py').read_text(), 0o755)
        write(root, 'etc/systemd/system/cubie-duck-firstboot.service', FIRSTBOOT)
        write(root, 'etc/systemd/system/microduck-studio.service', STUDIO_UNIT)
        # Wait for stock user creation and partition growth to finish before setup.
        write(root, 'etc/systemd/system/rsetup.service.d/10-cubie-duck.conf',
              '[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=300\n')
        write(root, 'etc/systemd/system/ssh.service.d/10-cubie-duck.conf',
              # Stock rsetup starts ssh synchronously: ordering it after rsetup
              # would deadlock. Generate keys before the stock sshd validation.
              '[Service]\nExecStartPre=\nExecStartPre=/usr/bin/ssh-keygen -A\n'
              'ExecStartPre=/usr/sbin/sshd -t\n')
        for name in ('ttyAS0', 'ttyS2'):
            link(root, 'etc/systemd/system/serial-getty@' + name + '.service', '/dev/null')
        link(root, 'etc/systemd/system/default.target', '/lib/systemd/system/multi-user.target')
        for unit in ('robotd', 'microduck-studio', 'cubie-duck-firstboot',
                     'cubie-provision', 'cubie-thermal-policy'):
            link(root, 'etc/systemd/system/multi-user.target.wants/' + unit + '.service',
                 '../' + unit + '.service')
        for directory, script in (('provision', 'cubie_provision.py'), ('thermal', 'thermal_policy.py')):
            destination = 'cubie-provision' if directory == 'provision' else 'cubie-thermal-policy'
            write(root, 'usr/local/lib/' + destination + '/' + script,
                  (BOARD / directory / script).read_text(), 0o755)
        for directory, unit in (('provision', 'cubie-provision'), ('thermal', 'cubie-thermal-policy')):
            write(root, 'etc/systemd/system/' + unit + '.service',
                  (BOARD / directory / (unit + '.service')).read_text())
        write(root, 'etc/systemd/system/cubie-provision.service.d/10-cubie-duck.conf',
              '[Unit]\nAfter=cubie-duck-firstboot.service\nRequires=cubie-duck-firstboot.service\n')
        write(root, 'usr/local/sbin/microduck-helper', (app / 'deploy/microduck-helper').read_text(), 0o755)
        write(root, 'etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf',
              (app / 'deploy/zzzz-studio-bus.conf').read_text())
        write(root, 'etc/sudoers.d/microduck-studio',
              'radxa ALL=(root) NOPASSWD: /usr/local/sbin/microduck-helper\n', 0o440)
        release = {'release': args.release, 'board': 'radxa,cubie-a7z',
                   'studio_release': '20261109-console1', 'boot_mode': 'commissioning',
                   'network_setup': 'qiqilong_<unique suffix> / http://10.42.0.1/',
                   'studio_url': 'http://cubie-<unique suffix>.local:8000/',
                   'physical_validation': 'pending: board unreachable during image build',
                   'features': ['walk', 'sit_stand', 'ground_pick', 'roulade', 'kick_left', 'kick_right', 'quack'],
                   'audio': 'ALSA default; physical output requires board verification'}
        write(root, 'usr/share/cubie-duck/image-release.json', json.dumps(release, ensure_ascii=False, indent=2) + '\n')
        manifest = {}
        for file in sorted(root.rglob('*')):
            if file.is_symlink():
                continue
            if file.is_dir():
                # Image directories have fixed permissions independent of the builder's umask.
                file.chmod(0o755)
            elif file.is_file():
                manifest[str(file.relative_to(root))] = digest(file)
        write(root, 'usr/share/cubie-duck/image-files.json', json.dumps(manifest, indent=2) + '\n')
        os.rename(root, args.output)
    finally:
        shutil.rmtree(work)
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True, help='ext4 partition extracted from the official base')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--studio-archive', type=Path, default=STUDIO)
    parser.add_argument('--studio-source', type=Path, default=SOURCE)
    parser.add_argument('--debugfs', type=Path, default=Path('/opt/homebrew/opt/e2fsprogs/sbin/debugfs'))
    parser.add_argument('--release', default='20261109-a7z-duck-preview1')
    args = parser.parse_args()
    args.output = args.output.absolute()
    print(json.dumps(prepare(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
