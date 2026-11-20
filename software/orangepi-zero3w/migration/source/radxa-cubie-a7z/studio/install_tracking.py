#!/usr/bin/env python3
"""Review by default; --apply updates Studio only while the duck is confirmed limp."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

BASE = Path('/opt/microduck-studio/app')
ALLOWED = {'studio/app.py', 'studio/servo.py', 'studio/updates.py', 'web/servo.js',
           'studio/leg_tracking.py', 'studio/leg_tracking_probe.py',
           'studio/analyze_leg_tracking.py', 'web/leg-tracking.js'}
MARKER = Path('/var/lib/microduck-studio/servo-session.json')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True, timeout=20).strip()


def bus_status():
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect('/run/robotd.sock')
        conn.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
        row = conn.makefile('rb').readline(1 << 20)
        return json.loads(row)['result']


def ready():
    require(not MARKER.exists(), 'Studio servo session active; hand the bus back first')
    status = bus_status()
    require(status.get('mode') == 'motion', 'expected the current motion-mode daemon')
    require(status.get('policy_enabled') is False and status.get('policy_motion_enabled') is not True,
            'policy must be inactive')
    require((status.get('native') or {}).get('torque_state_confirmed') is False,
            'torque-off not confirmed')
    require(not status.get('home_recovery') and not status.get('imu_verification'), 'robot operation active')


def verify_files(bundle):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    require(manifest.get('schema') == 1 and set(manifest['files']) == ALLOWED, 'invalid manifest file set')
    for relative, checks in manifest['files'].items():
        source, target = bundle / 'payload' / relative, BASE / relative
        require(source.is_file() and not source.is_symlink() and not target.is_symlink(), 'invalid payload path')
        require(digest(source) == checks['after_sha256'], f'payload hash mismatch: {relative}')
        expected = checks['before_sha256']
        require(digest(target) == expected if expected else not target.exists(),
                f'installed Studio differs; review before replacing: {relative}')
        if source.suffix == '.py':
            compile(source.read_text(), relative, 'exec')
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('bundle', type=Path)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    manifest = verify_files(args.bundle)
    ready()
    if not args.apply:
        print(json.dumps({'reviewed': True, 'will_update': sorted(ALLOWED), 'robotd_will_restart': False}))
        return
    require(os.geteuid() == 0, '--apply requires root')
    pid = run('systemctl', 'show', 'robotd', '-p', 'MainPID', '--value')
    immutable = {p: digest(p) for p in (Path('/etc/robot/feetech-ft5/hd1910-calibration.toml'),
                                       Path('/opt/robot/feetech-ft5-r5/robotd'))}
    backup = BASE.parent / ('before-leg-tracking-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    for relative in ALLOWED:
        path = BASE / relative
        if path.exists():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    run('systemctl', 'stop', 'microduck-studio')
    try:
        ready()  # Catch a user opening a bus session between review and stop.
        verify_files(args.bundle)
        for relative in sorted(ALLOWED):
            target = BASE / relative
            temporary = target.with_name(target.name + '.tracking-tmp')
            temporary.write_bytes((args.bundle / 'payload' / relative).read_bytes())
            temporary.chmod(0o644)
            temporary.replace(target)
            if target.suffix == '.js':
                target.with_suffix('.js.gz').unlink(missing_ok=True)
        run('systemctl', 'start', 'microduck-studio')
        healthy = False
        for _ in range(20):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=1) as response:
                    healthy = response.status == 200
                if healthy:
                    break
            except OSError:
                pass
            time.sleep(.25)
        require(healthy, 'Studio health check failed')
        require(run('systemctl', 'show', 'robotd', '-p', 'MainPID', '--value') == pid, 'robotd PID changed')
        require(all(digest(p) == expected for p, expected in immutable.items()), 'robotd/calibration changed')
        require(all(digest(BASE / rel) == item['after_sha256'] for rel, item in manifest['files'].items()),
                'installed file readback mismatch')
        ready()
    except BaseException:
        run('systemctl', 'stop', 'microduck-studio')
        for relative in ALLOWED:
            target, original = BASE / relative, backup / relative
            if original.exists():
                shutil.copy2(original, target)
            else:
                target.unlink(missing_ok=True)
            if target.suffix == '.js':
                target.with_suffix('.js.gz').unlink(missing_ok=True)
        run('systemctl', 'start', 'microduck-studio')
        raise
    print(json.dumps({'installed': True, 'backup': str(backup), 'robotd_pid_unchanged': pid,
                      'motion_commands_sent': 0, 'calibration_unchanged': True}))


if __name__ == '__main__':
    main()
