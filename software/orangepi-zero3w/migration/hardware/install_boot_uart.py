#!/usr/bin/env python3
"""Stage the verified original HAT's UART0; never open the servo bus."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DT = Path('/sys/firmware/devicetree/base')
CCU = '/soc@3000000/ccu@2002000'
PIO = '/soc@3000000/pinctrl@2000000'
ENV = Path('/boot/orangepiEnv.txt')
BASE = Path('/boot/dtb/allwinner/sun60i-a733-orangepi-zero3w.dtb')
BACKUP = Path('/var/lib/orangepi-migration/hat-uart0-before')
OVERLAY = Path('/boot/overlay-user/orangepi-hat-uart0.dtbo')


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def read_dt(node, prop):
    return (DT / node.lstrip('/') / prop).read_bytes()


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.hat-new')
    temporary.write_bytes(data)
    temporary.chmod(mode)
    os.replace(temporary, path)


def validate_board():
    if os.geteuid() != 0:
        raise RuntimeError('Run as root on the target board')
    if 'BOARD=orangepizero3w\n' not in Path('/etc/orangepi-release').read_text():
        raise RuntimeError('This adapter is only for Orange Pi Zero 3W')
    if read_dt(CCU, 'compatible') != b'allwinner,sun60iw2-ccu\0':
        raise RuntimeError('Unexpected CCU')
    if read_dt('/__symbols__', 'ccu') != CCU.encode() + b'\0':
        raise RuntimeError('Unexpected CCU symbol')
    ccu = DT / CCU.lstrip('/')
    if any((ccu / key).exists() for key in ('assigned-clocks', 'assigned-clock-parents', 'assigned-clock-rates')):
        raise RuntimeError('Existing CCU clock assignments require review')
    for unit in ('uart@2500000', 'uart@2501000'):
        if (DT / 'soc@3000000' / unit / 'clock-frequency').exists():
            raise RuntimeError('UART clock-frequency would override shared timing')
    for number, function in ((0, b'uart0\0'), (1, b'gpio_in\0')):
        node = f'{PIO}/uart0_pins@{number}'
        if read_dt(node, 'function') != function or read_dt(node, 'pins').strip(b'\0'):
            raise RuntimeError('UART0 base pinctrl has changed')
    unit = run('systemctl', 'cat', 'robotd.service')
    if '--read-only' not in unit:
        raise RuntimeError('robotd must stay read-only')
    params = Path('/etc/robot/feetech-ft5/robotd.toml').read_text()
    if 'port = "/dev/orangepi-robot-uart-unconfigured"' not in params:
        raise RuntimeError('Keep UART unopened until post-boot validation')


def configure_env(text):
    lines = text.splitlines()
    keys = [line.split('=', 1)[0] for line in lines if '=' in line and not line.startswith('#')]
    if len(keys) != len(set(keys)):
        raise RuntimeError('Duplicate environment keys require review')
    values = dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))
    if values.get('user_overlays') or values.get('overlays'):
        raise RuntimeError('Existing overlays require conflict review')
    updates = {'console': 'display', 'earlycon': 'off', 'user_overlays': 'orangepi-hat-uart0'}
    result = []
    for line in lines:
        key = line.split('=', 1)[0]
        if key in updates:
            result.append(key + '=' + updates.pop(key))
        else:
            result.append(line)
    result.extend(key + '=' + value for key, value in updates.items())
    return '\n'.join(result) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    validate_board()
    new_env = configure_env(ENV.read_text())
    with tempfile.TemporaryDirectory(prefix='hat-uart-') as temporary:
        overlay = Path(temporary) / 'hat.dtbo'
        merged = Path(temporary) / 'merged.dtb'
        run('dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', str(overlay), str(args.source))
        run('fdtoverlay', '-i', str(BASE), '-o', str(merged), str(overlay))
        phandle = run('fdtget', '-tu', str(merged), CCU, 'phandle')
        assert run('fdtget', '-ts', str(merged), PIO + '/uart0_pins@0', 'pins') == 'PB9 PB10'
        assert run('fdtget', '-ts', str(merged), PIO + '/uart0_pins@1', 'pins') == 'PB9 PB10'
        assert run('fdtget', '-ts', str(merged), PIO + '/uart0_pins@0', 'function') == 'uart0'
        assert run('fdtget', '-ts', str(merged), PIO + '/uart0_pins@1', 'function') == 'gpio_in'
        assert run('fdtget', '-ts', str(merged), '/soc@3000000/uart@2500000', 'status') == 'okay'
        for unit, gate in (('uart@2500000', 154), ('uart@2501000', 155)):
            assert run('fdtget', '-tu', str(merged), '/soc@3000000/' + unit, 'assigned-clock-rates') == '48000000'
            assert run('fdtget', '-tu', str(merged), '/soc@3000000/' + unit, 'assigned-clocks') == phandle + ' 46'
            clocks = run('fdtget', '-tu', str(merged), '/soc@3000000/' + unit, 'clocks')
            assert clocks == f'{phandle} {gate}'
        report = {'base_dtb_sha256': hashlib.sha256(BASE.read_bytes()).hexdigest(),
                  'overlay_sha256': hashlib.sha256(overlay.read_bytes()).hexdigest(),
                  'boot_id_before': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'servo_bus_opened': False, 'applied': args.apply}
        if args.apply:
            if OVERLAY.exists() or OVERLAY.with_suffix('.dts').exists():
                raise RuntimeError('Existing HAT overlay requires review')
            BACKUP.mkdir(mode=0o700, parents=True, exist_ok=False)
            shutil.copy2(ENV, BACKUP / 'orangepiEnv.txt')
            state = subprocess.run(['systemctl', 'is-enabled', 'serial-getty@ttyS0.service'], text=True, capture_output=True)
            (BACKUP / 'getty-enabled-before.txt').write_text(state.stdout)
            active = subprocess.run(['systemctl', 'is-active', '--quiet', 'serial-getty@ttyS0.service']).returncode == 0
            try:
                run('systemctl', 'mask', '--now', 'serial-getty@ttyS0.service')
                atomic_write(OVERLAY, overlay.read_bytes())
                shutil.copy2(args.source, OVERLAY.with_suffix('.dts'))
                atomic_write(ENV, new_env.encode())
                os.sync()
            except Exception:
                atomic_write(ENV, (BACKUP / 'orangepiEnv.txt').read_bytes())
                OVERLAY.unlink(missing_ok=True)
                OVERLAY.with_suffix('.dts').unlink(missing_ok=True)
                if not state.stdout.startswith('masked'):
                    run('systemctl', 'unmask', 'serial-getty@ttyS0.service')
                    if active:
                        run('systemctl', 'start', 'serial-getty@ttyS0.service')
                raise
            (BACKUP.parent / 'hat-uart0-boot-installation.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
