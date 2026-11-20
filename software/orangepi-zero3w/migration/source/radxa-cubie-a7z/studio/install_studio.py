#!/usr/bin/env python3
"""Install the reviewed Studio bundle without restarting or replacing robotd."""

import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tarfile

BASE = Path('/opt/microduck-studio')
BUNDLE_SHA256 = 'db8bcde8d9823c3e10abce1a015cd2422b81c25b279ac4f6a516d51c1fd10590'
UNIT = '''[Unit]
Description=Microduck Studio on Cubie A7Z
After=network.target

[Service]
Type=exec
User=radxa
Group=radxa
SupplementaryGroups=robot dialout
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


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def read_bus():
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect('/run/robotd.sock')
        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
        return json.loads(client.makefile().readline())['result']


def main():
    if os.geteuid() != 0:
        raise SystemExit('run as root')
    archive = Path(sys.argv[1])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != BUNDLE_SHA256:
        raise SystemExit('bundle SHA256 mismatch')
    before_pid = run('systemctl', 'show', 'robotd', '-p', 'MainPID', '--value')
    bus = read_bus()
    if bus.get('mode') != 'read_only' or bus.get('motor_writes_blocked') is not True:
        raise SystemExit('expected the existing guarded read-only robotd')
    binary = Path('/opt/robot/feetech-ft5-r5/robotd')
    calibration = Path('/etc/robot/feetech-ft5/hd1910-calibration.toml')
    original = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (binary, calibration)}
    helper = Path('/usr/local/sbin/microduck-helper')
    unit = Path('/etc/systemd/system/microduck-studio.service')
    sudoers = Path('/etc/sudoers.d/microduck-studio')
    guard = Path('/etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf')
    for path in (BASE, helper, unit, sudoers, guard):
        if path.exists() or path.is_symlink():
            raise SystemExit('already exists; inspect before replacing: ' + str(path))
    with socket.socket() as test:
        test.bind(('0.0.0.0', 8000))
    BASE.mkdir(mode=0o755)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or name.parts[0] not in ('app', 'python'):
                raise SystemExit('invalid bundle member')
        source.extractall(BASE)
    for path in BASE.rglob('*'):
        os.chown(path, 0, 0, follow_symlinks=False)
    python = str(BASE / 'python/bin/python3.13')
    print(run(python, '-I', '-c', 'import sys,ssl,sqlite3,tomllib; print(sys.version)'), flush=True)
    helper.write_bytes((BASE / 'app/deploy/microduck-helper').read_bytes())
    helper.chmod(0o755)
    guard.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    guard.write_bytes((BASE / 'app/deploy/zzzz-studio-bus.conf').read_bytes())
    guard.chmod(0o644)
    account = pwd.getpwnam('radxa')
    for name in ('/var/lib/microduck-studio', '/var/log/microduck-studio'):
        path = Path(name)
        path.mkdir(mode=0o750, exist_ok=True)
        os.chown(path, account.pw_uid, account.pw_gid)
    for name in ('/var/lib/microduck-helper', '/var/lib/microduck-helper/imu-recalibrate'):
        Path(name).mkdir(mode=0o755, exist_ok=True)
    temporary = Path('/tmp/cubie-studio-sudoers.check')
    temporary.write_text('radxa ALL=(root) NOPASSWD: /usr/local/sbin/microduck-helper\n')
    temporary.chmod(0o600)
    run('visudo', '-cf', str(temporary))
    shutil.copyfile(temporary, sudoers)
    sudoers.chmod(0o440)
    temporary.unlink()
    unit.write_text(UNIT)
    unit.chmod(0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'microduck-studio')
    after_pid = run('systemctl', 'show', 'robotd', '-p', 'MainPID', '--value')
    if before_pid != after_pid or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest
                                     for p, digest in original.items()):
        raise SystemExit('robotd or calibration changed unexpectedly')
    print(json.dumps({'installed': True, 'robotd_pid': after_pid, 'robotd_unchanged': True,
                      'calibration_unchanged': True, 'studio': 'http://192.0.2.114:8000/'}), flush=True)


if __name__ == '__main__':
    main()
