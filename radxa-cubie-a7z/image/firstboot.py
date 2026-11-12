#!/usr/bin/env python3
"""Finish a new A7Z installation without issuing robot or servo commands."""

import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

STATE = Path('/var/lib/cubie-duck')
DONE = STATE / 'firstboot.done'
RUNTIME = Path('/opt/robot/feetech-ft5-r5')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=180).stdout.strip()


def state_directory(path, uid=0, gid=0, mode=0o755):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    os.chown(path, uid, gid)


def main():
    if os.geteuid() != 0:
        raise SystemExit('first boot requires root on the A7Z')
    compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    if b'radxa,cubie-a7z' not in compatible:
        raise SystemExit('expected Cubie A7Z; no setup performed')
    if DONE.exists():
        print('first boot already completed')
        return
    state_directory(STATE, mode=0o700)
    # The stock rsetup first-boot task creates radxa and grows the root partition.
    # Its unit is oneshot in this image so this lookup cannot race user creation.
    account = pwd.getpwnam('radxa')
    try:
        grp.getgrnam('robot')
    except KeyError:
        run('groupadd', '--system', 'robot')
    groups = ['robot', 'dialout', 'audio']
    for name in ('input', 'video'):
        try:
            grp.getgrnam(name)
            groups.append(name)
        except KeyError:
            pass
    run('usermod', '-aG', ','.join(groups), 'radxa')
    for path in ('/var/lib/microduck-studio', '/var/log/microduck-studio'):
        state_directory(path, account.pw_uid, account.pw_gid, 0o750)
    for path in ('/var/lib/robot', '/var/lib/cubie-provision',
                 '/var/lib/microduck-helper', '/var/lib/microduck-helper/imu-recalibrate'):
        state_directory(path)
    run('visudo', '-cf', '/etc/sudoers.d/microduck-studio')
    # These commands only load programs or generate WAV files; none opens the bus.
    run(str(RUNTIME / 'robotd'), '--help')
    run('/opt/microduck-studio/python/bin/python3.13', '-I', '-c',
        'import ssl,sqlite3,tomllib; print("Studio Python ready")')
    run(str(RUNTIME / 'sounds'), 'ensure-bank', '--dir', '/var/lib/robot/sounds')
    if not list(Path('/var/lib/robot/sounds').rglob('*.wav')):
        raise RuntimeError('sound bank generation produced no WAV files')
    run('ssh-keygen', '-A')
    if shutil.which('rfkill'):
        run('rfkill', 'unblock', 'wifi')
    run('nmcli', 'radio', 'wifi', 'on')
    # Enumeration is evidence of available devices, not proof of an audible quack.
    audio = subprocess.run(['aplay', '-l'], capture_output=True, text=True, timeout=10)
    (STATE / 'audio-devices.txt').write_text(audio.stdout + audio.stderr)
    release = json.loads(Path('/usr/share/cubie-duck/image-release.json').read_text())
    result = {'release': release['release'], 'setup_complete': True,
              'boot_mode': 'commissioning', 'motion_autostart': False,
              'speaker_verified': False}
    temporary = DONE.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.replace(DONE)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
