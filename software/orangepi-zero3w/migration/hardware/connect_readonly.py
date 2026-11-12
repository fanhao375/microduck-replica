#!/usr/bin/env python3
"""Connect only the read-only daemon after verifying the boot UART adapter."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

OLD = '/dev/orangepi-robot-uart-unconfigured'
PORT = '/dev/ttyS0'
ROOT = Path('/var/lib/orangepi-migration')
FILES = (
    Path('/etc/robot/feetech-ft5/robotd.toml'),
    Path('/etc/systemd/system/microduck-studio.service'),
    Path('/usr/local/sbin/microduck-helper'),
    Path('/etc/systemd/system/robotd.service'),
)


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def replace_file(path, data, mode):
    temporary = path.with_name(path.name + '.hat-new')
    temporary.write_bytes(data)
    temporary.chmod(mode)
    os.replace(temporary, path)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run as root')
    assert 'BOARD=orangepizero3w\n' in Path('/etc/orangepi-release').read_text()
    assert 'ttyS0' not in Path('/proc/cmdline').read_text()
    assert 'earlyprintk' not in Path('/proc/cmdline').read_text()
    assert Path('/sys/class/tty/ttyS0/console').read_text().strip() == 'N'
    assert run('systemctl', 'is-enabled', 'serial-getty@ttyS0', check=False).stdout.strip() == 'masked'
    assert run('systemctl', 'is-active', 'serial-getty@ttyS0', check=False).stdout.strip() == 'inactive'
    for tty in ('ttyS0', 'ttyS1'):
        assert Path(f'/sys/class/tty/{tty}/uartclk').read_text().strip() == '48000000'
    pinmux = Path('/sys/kernel/debug/pinctrl/2000000.pinctrl/pinmux-pins').read_text()
    for pin in ('PB9', 'PB10'):
        assert f'({pin}): device 2500000.uart function uart0' in pinmux
    command = run('systemctl', 'show', '-p', 'ExecStart', '--value', 'robotd').stdout
    assert '--read-only' in command
    assert hashlib.sha256(Path('/opt/robot/feetech-ft5-r5/robotd').read_bytes()).hexdigest() == '1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc'
    original = {path: (path.read_bytes(), path.stat().st_mode & 0o777) for path in FILES}
    assert all(original[path][0].count(OLD.encode()) == 1 for path in FILES[:3])
    assert b'enabled = false' in original[FILES[0]][0]
    assert b'\nEnvironment=CUBIE_A7Z_SYNC_GROUP_SIZE=3' in original[FILES[3]][0]
    assert b'UnsetEnvironment=CUBIE_A7Z_SYNC_GROUP_SIZE' not in original[FILES[3]][0]
    backup = ROOT / 'hat-uart0-runtime-before'
    backup.mkdir(mode=0o700, exist_ok=False)
    for path in FILES:
        shutil.copy2(path, backup / path.name)
    run('systemctl', 'stop', 'microduck-studio', 'robotd')
    try:
        assert run('fuser', PORT, check=False).returncode == 1, 'Another process owns UART0'
        for path, (data, mode) in original.items():
            data = data.replace(OLD.encode(), PORT.encode())
            if path == FILES[3]:
                data = data.replace(b'Orange Pi, unconfigured read-only bus', b'Orange Pi, grouped read-only bus')
            if path == FILES[0]:
                data = data.replace(b'# Orange Pi installation: no UART/IMU wiring has been commissioned.\n# The nonexistent device is deliberate: report waiting_for_bus, never guess a real UART.',
                                    b'# Original HAT on physical pins 8/10, UART0 at verified 48MHz.\n# robotd remains --read-only; calibration and motion are not commissioned.')
            replace_file(path, data, mode)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', 'robotd')
        run('systemctl', 'start', 'microduck-studio')
    except Exception:
        run('systemctl', 'stop', 'microduck-studio', 'robotd', check=False)
        for path, (data, mode) in original.items():
            replace_file(path, data, mode)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', 'robotd', 'microduck-studio')
        raise
    report = {'port': PORT, 'read_only': True, 'telemetry_group_size': 3, 'telemetry_requests_per_cycle': 6,
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'files': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}}
    (ROOT / 'hat-uart0-runtime-installation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
