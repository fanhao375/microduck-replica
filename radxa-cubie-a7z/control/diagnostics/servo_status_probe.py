#!/usr/bin/env python3
"""Read-only servo reply diagnostics. robotd and Studio must already be stopped.

This is an independent acquisition experiment, not a locomotion controller.
Every transmitted instruction is checked at the packet boundary. Error-status
frames remain failures even if their payload and checksum are valid.
"""
from collections import Counter
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import select
import subprocess
import termios
import time

JOINT_IDS = (20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14)
SERVO_IDS = set(JOINT_IDS)
READ_RANGES = {(0, 5), (40, 1), (56, 15), (65, 1)}
ALLOWED_GROUPS = {tuple(JOINT_IDS[i:i+n]) for n in (1, 2, 3)
                  for i in range(0, len(JOINT_IDS), n)} | {(200,)}

def request(sid, instruction, params):
    """The only transmission boundary: no write/reset/calibration instructions."""
    if instruction == 0x82:
        if sid != 254 or params[:2] != bytes([56, 15]) or tuple(params[2:]) not in ALLOWED_GROUPS:
            raise ValueError("unsupported Sync Read")
    elif instruction == 2:
        if sid not in SERVO_IDS or tuple(params) not in READ_RANGES:
            raise ValueError("unsupported register read")
    elif instruction != 1 or params or sid not in (12, 13, 14):
        raise ValueError("only fixed read-only requests are permitted")
    body = bytes([sid, len(params) + 2, instruction]) + params
    return b"\xff\xff" + body + bytes([~sum(body) & 255])


def status():
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect('/run/robotd.sock')
        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
        return json.loads(client.makefile().readline())['result']


def uart_counts():
    line = next(x for x in Path('/proc/tty/driver/uart-ng').read_text().splitlines()
                if x.startswith('0:') and 'mmio:0x02500000' in x)
    found = dict(re.findall(r'\b(rx|tx|oe|fe|pe|brk|bo):(\d+)', line))
    return {key: int(found.get(key, 0)) for key in ('rx', 'tx', 'oe', 'fe', 'pe', 'brk', 'bo')}


def feedback(frame):
    raw = bytes.fromhex(frame)[5:-1]
    if len(raw) != 15:
        return None
    return {'position_raw': int.from_bytes(raw[:2], 'little'),
            'voltage_v': raw[6] / 10, 'temperature_c': raw[7],
            'status_register_65': raw[9], 'moving': raw[10],
            'current_raw': int.from_bytes(raw[13:15], 'little')}


def collect(fd, packet, ids, count, deadline):
    termios.tcflush(fd, termios.TCIFLUSH)
    if os.write(fd, packet) != len(packet):
        raise RuntimeError('short read request; no retry sent')
    buffer, frames = bytearray(), {}
    bad = discarded = echoes = 0
    while time.monotonic() < deadline and len(frames) != len(ids):
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        buffer.extend(os.read(fd, 256))
        while len(buffer) >= 4:
            if buffer[:2] != b'\xff\xff' or buffer[3] < 2 or buffer[3] > 32:
                del buffer[0]
                discarded += 1
                continue
            size = buffer[3] + 4
            if len(buffer) < size:
                break
            frame = bytes(buffer[:size])
            if sum(frame[2:]) & 255 != 255:
                bad += 1
                discarded += 1
                del buffer[0]
                continue
            del buffer[:size]
            if frame == packet:
                echoes += 1
                continue
            if frame[2] not in ids or frame[2] in frames or len(frame[5:-1]) != count:
                raise RuntimeError('unexpected reply: ' + frame.hex())
            # Retain nonzero error bytes and payloads instead of calling them missing.
            frames[frame[2]] = frame.hex()
    return {'frames': frames, 'missing': sorted(set(ids) - frames.keys()),
            'bad_checksums': bad, 'discarded': discarded + len(buffer), 'echoes': echoes}


def read_register(fd, sid, address, length):
    packet = request(sid, 2, bytes([address, length]))
    row = collect(fd, packet, (sid,), length, time.monotonic() + .020)
    time.sleep(.002)
    return row


def torque_snapshot(fd, write, phase):
    for sid in JOINT_IDS:
        row = read_register(fd, sid, 40, 1)
        write({"type": "torque", "phase": phase, "id": sid, **row})
        raw = bytes.fromhex(row["frames"].get(sid, ""))
        if len(raw) != 7 or raw[5] != 0:
            raise RuntimeError(f"torque off not confirmed: ID {sid}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group-size", type=int, choices=(1, 2, 3), required=True)
    ap.add_argument("--seconds", type=int, choices=range(1, 301), required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    for name in ("robotd", "microduck-studio"):
        state = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True).stdout.strip()
        if state not in ("inactive", "failed"):
            raise RuntimeError(f"{name} must already be stopped")
    groups = [JOINT_IDS[i:i+args.group_size] for i in range(0, len(JOINT_IDS), args.group_size)] + [(200,)]
    packets = [request(254, 0x82, bytes([56, 15, *g])) for g in groups]
    fd = os.open('/dev/ttyS2', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if termios.tcgetattr(fd)[4:6] != [termios.B1000000, termios.B1000000]:
            raise RuntimeError("UART must already be 1 Mbps")
        with args.output.open('x') as out:
            def write(row):
                out.write(json.dumps(row, separators=(',', ':')) + '\n')
            torque_snapshot(fd, write, 'before')
            for sid in JOINT_IDS:
                write({"type": "identity", "id": sid, **read_register(fd, sid, 0, 5)})
            counts = Counter()
            missing = Counter()
            errors = Counter()
            status65 = Counter()
            durations = []
            uart_before = uart_counts()
            started = scheduled = time.monotonic()
            while time.monotonic() - started < args.seconds:
                began = time.monotonic()
                rows = []
                for group, packet in zip(groups, packets):
                    row = collect(fd, packet, group, 15, began + .020)
                    rows.append(row)
                    if row['missing'] or row['bad_checksums'] or row['discarded']:
                        break
                frames = {sid: raw for row in rows for sid, raw in row['frames'].items()}
                bad_status = {sid: raw for sid, raw in frames.items() if bytes.fromhex(raw)[4]}
                elapsed_us = (time.monotonic() - began) * 1e6
                durations.append(elapsed_us)
                absent = [sid for row in rows for sid in row['missing']]
                corrupt = sum(row['bad_checksums'] + row['discarded'] for row in rows)
                failed = len(frames) != 16 or bool(bad_status) or corrupt > 0 or elapsed_us >= 20000
                counts['cycles'] += 1
                counts['failed'] += bool(failed)
                counts['status_error_cycles'] += bool(bad_status)
                counts['incomplete_cycles'] += len(frames) != 16
                counts['corrupt_cycles'] += corrupt > 0
                counts['late_cycles'] += elapsed_us >= 20000
                missing.update(absent)
                errors.update(f'{sid}:0x{bytes.fromhex(raw)[4]:02x}' for sid, raw in bad_status.items())
                for sid, raw in frames.items():
                    if sid in SERVO_IDS:
                        status65.update([f"{sid}:0x{feedback(raw)['status_register_65']:02x}"])
                if failed or counts['cycles'] % 50 == 0:
                    write({'type': 'cycle', 'cycle': counts['cycles'], 'at': time.monotonic(),
                           'elapsed_us': elapsed_us, 'failed': bool(failed), 'rows': rows})
                    out.flush()
                # Corroborate a device-reported status on a separate unicast READ.
                # It does not turn the failed cycle into a successful one.
                for sid in bad_status:
                    if sid in SERVO_IDS:
                        write({'type': 'status_followup', 'cycle': counts['cycles'], 'id': sid,
                               **read_register(fd, sid, 65, 1)})
                scheduled += .020
                now = time.monotonic()
                if scheduled < now:
                    counts['skipped_slots'] += int((now - scheduled) / .020) + 1
                    scheduled += (int((now - scheduled) / .020) + 1) * .020
                time.sleep(max(0, scheduled - time.monotonic()))
            span = time.monotonic() - started
            uart_after = uart_counts()
            torque_snapshot(fd, write, 'after')
            ordered = sorted(durations)
            result = {'type': 'result', 'group_size': args.group_size, 'elapsed_s': span,
                      'observed_hz': counts['cycles'] / span, 'counts': dict(counts),
                      'missing': dict(missing), 'status_errors': dict(errors), 'status65': dict(status65),
                      'duration_us': {'p50': ordered[len(ordered)//2],
                                      'p99': ordered[min(len(ordered)-1, int(len(ordered)*.99))], 'max': ordered[-1]},
                      'uart_delta': {k: uart_after[k]-uart_before[k] for k in uart_before},
                      'motor_write_instructions': 0, 'output': str(args.output)}
            write(result)
            print(json.dumps(result), flush=True)
    finally:
        try:
            fcntl.ioctl(fd, termios.TIOCNXCL)
        finally:
            os.close(fd)


if __name__ == '__main__':
    main()
