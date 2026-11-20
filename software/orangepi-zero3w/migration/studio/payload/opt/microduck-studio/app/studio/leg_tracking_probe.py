#!/usr/bin/env python3
"""Supported, unloaded HD1910 leg tracking experiment; dry plan by default.

Uses the installed Studio transport, with a packet-level allowlist. Does not run
the learned walking policy or change EEPROM, calibration or robotd settings.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import tomllib


ALL_IDS = (20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14)
LEG_IDS = (20, 21, 22, 23, 24, 10, 11, 12, 13, 14)
PAIRS = tuple(zip(LEG_IDS[:5], LEG_IDS[5:]))
DEG_PER_RAW = 360 / 4096
READS = {(33, 1), (40, 1), (41, 1), (42, 2), (42, 8),
         (50, 1), (51, 1), (50, 3), (56, 15)}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def build_stages(ids, amplitude_deg, hz=20, pairs=False):
    """Finite, smooth ramps and two known sine frequencies, never a policy replay."""
    stages = []
    groups = [(sid,) for sid in ids]
    if pairs:
        groups += [pair for pair in PAIRS if all(sid in ids for sid in pair)]
    for group in groups:
        # Holds make residual oscillation observable after the target stops moving.
        for target in (amplitude_deg, -amplitude_deg):
            ramp = [target * (1 - math.cos(math.pi * i / hz)) / 2
                    for i in range(1, hz + 1)]
            stages.append({'ids': group, 'kind': 'ramp_hold', 'hz': hz,
                           'values_deg': ramp + [target] * (2 * hz), 'ramp_samples': hz})
        for frequency in (0.25, 0.5):
            count = round(3 * hz / frequency)
            stages.append({'ids': group, 'kind': 'sine', 'hz': hz,
                           'frequency_hz': frequency,
                           'values_deg': [amplitude_deg * math.sin(2 * math.pi * frequency * i / hz)
                                          for i in range(count + 1)]})
    return stages


def raw_target(joint, center, offset_deg):
    target = center + round(offset_deg / DEG_PER_RAW * joint['direction'])
    angle = (target - joint['zero_raw']) * joint['direction'] * 2 * math.pi / 4096
    require(0 <= target <= 4095 and joint['min_rad'] <= angle <= joint['max_rad'],
            f"ID{joint['id']} target outside calibrated limits: raw={target}")
    return target


def permitted(sid, instruction, params, centers, amplitude_raw, gain_values):
    if instruction == 2:
        return sid in ALL_IDS and tuple(params) in READS
    if instruction != 3 or sid not in centers:
        return False
    if params in (b'\x28\x00', b'\x28\x01'):
        return True
    if len(params) == 3 and params[0] == 42:
        target = int.from_bytes(params[1:], 'little')
        return 0 <= target <= 4095 and abs(target - centers[sid]) <= amplitude_raw
    return (len(params) == 2 and params[0] in (50, 51)
            and params[1] in gain_values.get((sid, params[0]), set()))


def open_bus(log, centers, amplitude_raw, gain_values):
    # Import only for --run: plans and offline tests work without board packages.
    import fcntl
    import termios
    sys.path[:0] = ['/opt/microduck-studio/app', '/opt/microduck-studio/app/deps']
    import serial
    from studio.bus.feetech import FeetechBus

    class LimitedBus(FeetechBus):
        cleanup_mode = False

        def emit(self, row):
            try:
                log(row)
            except OSError:
                if not self.cleanup_mode:
                    raise

        def _send(self, sid, instruction, params=b''):
            require(permitted(sid, instruction, bytes(params), centers, amplitude_raw, gain_values),
                    'packet rejected by leg-test allowlist')
            self.emit({'type': 'tx', 'id': sid, 'instruction': instruction,
                       'params_hex': bytes(params).hex(), 'at': time.monotonic()})
            super()._send(sid, instruction, params)

        def _read_status(self, expect_id=None):
            sid, error, data = super()._read_status(expect_id)
            self.emit({'type': 'rx', 'id': sid, 'error': error,
                       'data_hex': data.hex(), 'at': time.monotonic()})
            require(error == 0, f'ID{sid} reply error 0x{error:02x}')
            return sid, error, data

        def close(self):
            try:
                fcntl.ioctl(self.ser.fileno(), termios.TIOCNXCL)
            finally:
                super().close()

    ser = serial.Serial('/dev/ttyS2', 1_000_000, timeout=.02, write_timeout=.1, exclusive=True)
    try:
        fcntl.ioctl(ser.fileno(), termios.TIOCEXCL)
        return LimitedBus('/dev/ttyS2', ser=ser)
    except BaseException:
        ser.close()
        raise


def stopped():
    for service in ('robotd', 'microduck-studio'):
        result = subprocess.run(['systemctl', 'show', service, '-p', 'ActiveState', '--value'],
                                capture_output=True, text=True, check=True)
        require(result.stdout.strip() in ('inactive', 'failed'), f'{service} must be stopped')


def checked_write(bus, sid, address, data):
    require(bus.write(sid, address, data) in (0, None), f'ID{sid} write rejected')
    error, actual = bus.read(sid, address, len(data))
    require(error == 0 and actual == bytes(data), f'ID{sid} register {address} readback mismatch')


def safe_state(state, sid):
    require(state['err'] == 0 and state['status'] == 0, f'ID{sid} servo alarm: {state}')
    require(6.5 <= state['volt'] <= 8.4 and state['temp'] < 50,
            f'ID{sid} voltage/temperature stop: {state}')


def cleanup(bus, touched, snapshots, log):
    """Try every joint even if one is unreachable; report unconfirmed restoration."""
    errors = []
    bus.cleanup_mode = True
    for sid in sorted(touched):
        try:
            checked_write(bus, sid, 40, b'\0')
        except Exception as error:
            errors.append(f'ID{sid} torque off unconfirmed: {error}')
    for sid in sorted(touched):
        try:
            # Never restore gains while torque-off is unconfirmed for this joint.
            require(bus.read_u8(sid, 40) == 0, f'ID{sid} still powered')
            for address, value in zip((50, 51), snapshots[sid]['pdi'][:2]):
                checked_write(bus, sid, address, bytes([value]))
        except Exception as error:
            errors.append(f'ID{sid} P/D restore unconfirmed: {error}')
    for sid in ALL_IDS:
        try:
            require(bus.read_u8(sid, 40) == 0, f'ID{sid} not off')
        except Exception as error:
            errors.append(f'ID{sid} final torque check failed: {error}')
    try:
        log({'type': 'cleanup', 'confirmed': not errors, 'errors': errors})
    except OSError as error:
        errors.append(f'cleanup log failed: {error}')
    return errors


def run_test(bus, args, stages, calibration, centers, gains, log):
    joints = {j['id']: j for j in calibration['joints']}
    require(len(joints) == 15 and set(joints) == set(ALL_IDS), 'expected 15 unique calibrated IDs')
    for sid in ALL_IDS:
        require(bus.read_u8(sid, 40) == 0, f'ID{sid} torque must initially be off')
    snapshots = {}
    # Validate the entire trajectory before the first actuator write.
    for sid in args.ids:
        j = joints[sid]
        require(j['calibrated'] and j['direction'] in (-1, 1), f'ID{sid} calibration incomplete')
        state = bus.state(sid)
        safe_state(state, sid)
        center = state['pos']
        require(bus.read_u8(sid, 33) == 4, f'ID{sid} expected native position mode 4')
        centers[sid] = center
        for offset in (-args.amplitude_deg, 0, args.amplitude_deg):
            raw_target(j, center, offset)
        _, pdi = bus.read(sid, 50, 3)
        _, goal = bus.read(sid, 42, 8)
        snapshots[sid] = {'center': center, 'pdi': list(pdi), 'goal_block': list(goal),
                          'acceleration_raw': bus.read_u8(sid, 41), 'joint': j}
        for address, old, new in ((50, pdi[0], args.kp), (51, pdi[1], args.kd)):
            gains[sid, address] = {old} if new is None else {old, new}
        log({'type': 'before', 'id': sid, **snapshots[sid]})
    touched = set()
    powered = set()
    failure = None
    try:
        for number, stage in enumerate(stages):
            group = stage['ids']
            log({'type': 'stage', 'stage': number,
                 **{k: v for k, v in stage.items() if k != 'values_deg'}})
            if set(group) != powered:
                for sid in powered:
                    checked_write(bus, sid, 40, b'\0')
                powered = set()
                for sid in group:
                    state = bus.state(sid)
                    safe_state(state, sid)
                    require(abs(state['pos'] - centers[sid]) * DEG_PER_RAW <= 1,
                            f'ID{sid} moved >1deg since preflight; reposition and restart')
                    touched.add(sid)  # Include partially completed writes in cleanup.
                    # Preload the measured pose while unpowered; never power an old goal.
                    checked_write(bus, sid, 42, struct.pack('<H', centers[sid]))
                    for address, value in ((50, args.kp), (51, args.kd)):
                        if value is not None:
                            checked_write(bus, sid, address, bytes([value]))
                    checked_write(bus, sid, 40, b'\1')
                    powered.add(sid)
            started = time.monotonic()
            for index, offset in enumerate(stage['values_deg']):
                deadline = started + index / stage['hz']
                time.sleep(max(0, deadline - time.monotonic()))
                require(time.monotonic() - deadline < .1, 'scheduler overrun >100ms')
                commands = {}
                for side, sid in enumerate(group):
                    # Pair trajectories use calibrated coordinates and opposite phase.
                    target = raw_target(joints[sid], centers[sid], offset * (-1 if side else 1))
                    sent = time.monotonic()
                    require(bus.write_u16(sid, 42, target) in (0, None), f'ID{sid} target write error')
                    commands[sid] = (target, sent)
                for sid in group:
                    state = bus.state(sid)
                    target, sent = commands[sid]
                    row = {'type': 'sample', 'stage': number, 'id': sid,
                           'kind': stage['kind'], 'index': index, 'hz': stage['hz'],
                           'frequency_hz': stage.get('frequency_hz'),
                           'hold': stage['kind'] == 'ramp_hold' and index >= stage['ramp_samples'],
                           'target': target, 'command_at': sent, 'at': time.monotonic(),
                           'state': state}
                    log(row)
                    safe_state(state, sid)
                    require(abs(state['pos'] - target) * DEG_PER_RAW < 9,
                            f'ID{sid} tracking error >=9deg')
            # A sine ends at the center; ramps end at their held target. Return
            # slowly to center before releasing so the next stage starts smoothly.
            for step in range(1, stage['hz'] + 1):
                for sid in group:
                    origin = commands[sid][0]
                    target = round(origin + (centers[sid] - origin) * step / stage['hz'])
                    require(bus.write_u16(sid, 42, target) in (0, None), 'return write error')
                    state = bus.state(sid)
                    log({'type': 'return_sample', 'id': sid, 'target': target,
                         'at': time.monotonic(), 'state': state})
                    safe_state(state, sid)
                    require(abs(state['pos'] - target) * DEG_PER_RAW < 9, 'return tracking error >=9deg')
                time.sleep(1 / stage['hz'])
    except BaseException as error:
        failure = f'{type(error).__name__}: {error}'
        try:
            log({'type': 'failure', 'detail': failure})
        except OSError:
            pass  # A full disk must not prevent the torque-off path below.
    finally:
        errors = cleanup(bus, touched, snapshots, log)
    result = {'type': 'result', 'completed': failure is None and not errors,
              'failure': failure, 'cleanup_errors': errors, 'unloaded_only': True,
              'walking_verified': False}
    log(result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--supported', action='store_true', help='operator confirms supported torso and clear legs')
    ap.add_argument('--ids', type=int, nargs='+', choices=LEG_IDS, default=list(LEG_IDS))
    ap.add_argument('--pairs', action='store_true', help='also test matching left/right joints in opposite phase')
    ap.add_argument('--amplitude-deg', type=float, default=3)
    ap.add_argument('--kp', type=int, choices=range(1, 33))
    ap.add_argument('--kd', type=int, choices=range(65))
    ap.add_argument('--calibration', type=Path,
                    default=Path('/etc/robot/feetech-ft5/hd1910-calibration.toml'))
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    require(len(args.ids) == len(set(args.ids)), 'duplicate joint IDs')
    require(math.isfinite(args.amplitude_deg) and 1 <= args.amplitude_deg <= 5, 'amplitude must be 1..5deg')
    require((args.kp is None and args.kd is None) or len(args.ids) == 1,
            'temporary P/D tests require a single joint')
    stages = build_stages(args.ids, args.amplitude_deg, pairs=args.pairs)
    plan = {'ids': args.ids, 'stages': len(stages), 'amplitude_deg': args.amplitude_deg,
            'nominal_hz': 20, 'motion_seconds': sum(len(s['values_deg']) / s['hz'] + 1 for s in stages),
            'kp_override': args.kp, 'kd_override': args.kd, 'motor_commands_sent': 0}
    if not args.run:
        print(json.dumps({'dry_plan': plan}, indent=2))
        return
    require(args.supported and args.output is not None, '--run requires --supported and --output')
    stopped()
    raw_calibration = args.calibration.read_bytes()
    calibration = tomllib.loads(raw_calibration.decode())
    with args.output.open('x') as out:
        def log(row):
            out.write(json.dumps(row, allow_nan=False) + '\n')
            out.flush()

        log({'type': 'meta', 'schema': 'leg-tracking-v1', 'plan': plan,
             'wall_time': time.time(), 'calibration_sha256': hashlib.sha256(raw_calibration).hexdigest(),
             'profile': 'existing_duration_speed_acceleration_torque_limit_preserved',
             'current_units_verified': False})
        centers, gains = {}, {}
        bus = open_bus(log, centers, math.ceil(args.amplitude_deg / DEG_PER_RAW), gains)
        def interrupted(signum, _frame):
            raise RuntimeError(f'interrupted by signal {signum}')
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        try:
            result = run_test(bus, args, stages, calibration, centers, gains, log)
        finally:
            bus.close()
    print(json.dumps(result))
    if not result['completed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
