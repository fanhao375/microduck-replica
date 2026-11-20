#!/usr/bin/env python3
"""Capture ID13 status without motor writes or changes to UART configuration.

Temporarily suspends an already verified read-only robotd. A systemd caller must
also arrange ExecStopPost=kill -CONT <pid> and a runtime limit as crash cleanup.
This independent probe diagnoses status bits; it does not certify robotd.
"""

from collections import Counter
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import termios
import time

GROUPS = ((20, 21, 22), (23, 24, 30), (31, 32, 33),
          (34, 10, 11), (12, 13, 14), (200,))
SERVO_IDS = set(range(10, 15)) | set(range(20, 25)) | set(range(30, 35))
READ_RANGES = {(0, 5), (9, 12), (40, 1), (56, 15), (65, 1)}


def request(sid, instruction, params):
    """The only transmission boundary: no write/reset/calibration instructions."""
    if instruction == 0x82:
        if sid != 254 or params[:2] != bytes([56, 15]) or tuple(params[2:]) not in GROUPS:
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


def snapshots(fd, write, phase):
    for sid in (12, 13, 14):
        for address, length in ((0, 5), (9, 12), (40, 1), (56, 15), (65, 1)):
            row = read_register(fd, sid, address, length)
            write({'type': 'register', 'phase': phase, 'id': sid,
                   'address': address, 'length': length, **row})
            if address == 56 and sid in row['frames']:
                print(json.dumps({'phase': phase, 'id': sid,
                                  'header_error': bytes.fromhex(row['frames'][sid])[4],
                                  **feedback(row['frames'][sid])}), flush=True)
    for sid in sorted(SERVO_IDS):
        row = read_register(fd, sid, 40, 1)
        frame = bytes.fromhex(row['frames'].get(sid, ''))
        write({'type': 'torque_read', 'phase': phase, 'id': sid, **row})
        if len(frame) != 7 or frame[4:6] != b'\0\0':
            raise RuntimeError('torque-off read not confirmed for ID %d' % sid)


def main():
    pid, seconds, output_path = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    if pid <= 1 or not 1 <= seconds <= 300:
        raise ValueError('invalid PID/duration')
    current = int(subprocess.check_output(['systemctl', 'show', 'robotd', '-p', 'MainPID', '--value']))
    before_status = status()
    if (current != pid or before_status['mode'] != 'read_only'
            or before_status['motor_writes_blocked'] is not True):
        raise RuntimeError('expected the verified read-only service')
    command = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
    if b'--read-only' not in command or command[0] != b'/opt/robot/feetech-ft5-r5/robotd':
        raise RuntimeError('unexpected service command')
    if b'radxa,cubie-a7z\0' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes():
        raise RuntimeError('expected Cubie A7Z')
    fd = None
    paused = False
    with output_path.open('x') as output:
        def write(value):
            output.write(json.dumps(value) + '\n')

        try:
            os.kill(pid, signal.SIGSTOP)
            paused = True
            time.sleep(.050)
            for task in Path('/proc/%d/task' % pid).iterdir():
                if '\nState:\tT (stopped)\n' not in (task / 'status').read_text():
                    raise RuntimeError('robotd thread was not suspended')
            fd = os.open('/dev/ttyS2', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            if termios.tcgetattr(fd)[4:6] != [termios.B1000000, termios.B1000000]:
                raise RuntimeError('existing UART baud is not 1 Mbps')
            snapshots(fd, write, 'before')
            uart_before = uart_counts()
            errors, status_registers = Counter(), Counter()
            cycles = failed = 0
            positions, voltages, temperatures = [], [], []
            started = scheduled = time.monotonic()
            while time.monotonic() - started < seconds:
                began = time.monotonic()
                rows = []
                for group in GROUPS:
                    row = collect(fd, request(254, 0x82, bytes([56, 15, *group])),
                                  group, 15, began + .020)
                    rows.append(row)
                    if row['missing'] or row['bad_checksums'] or row['discarded']:
                        break
                frames = {sid: frame for row in rows for sid, frame in row['frames'].items()}
                error_frames = {sid: frame for sid, frame in frames.items() if bytes.fromhex(frame)[4]}
                errors.update('%d:0x%02x' % (sid, bytes.fromhex(frame)[4]) for sid, frame in error_frames.items())
                elapsed = time.monotonic() - began
                cycle_failed = (len(frames) != 16 or bool(error_frames) or elapsed >= .020
                                or any(row['bad_checksums'] or row['discarded'] for row in rows))
                cycles += 1
                failed += int(cycle_failed)
                id13 = frames.get(13)
                if id13:
                    values = feedback(id13)
                    status_registers.update([values['status_register_65']])
                    positions.append(values['position_raw'])
                    voltages.append(values['voltage_v'])
                    temperatures.append(values['temperature_c'])
                write({'type': 'cycle', 'cycle': cycles, 'ms': elapsed * 1000,
                       'id13_frame': id13, 'error_frames': error_frames,
                       'bad_checksums': sum(row['bad_checksums'] for row in rows),
                       'discarded': sum(row['discarded'] for row in rows),
                       'received_ids': sorted(frames), 'failed': cycle_failed})
                if error_frames or cycles % 1500 == 0:
                    output.flush()
                    print(json.dumps({'cycles': cycles, 'failures': failed,
                                      'status_errors': dict(errors),
                                      'id13_status65': dict(status_registers)}), flush=True)
                scheduled += .020
                if scheduled < time.monotonic():
                    scheduled += (int((time.monotonic() - scheduled) / .020) + 1) * .020
                time.sleep(max(0, scheduled - time.monotonic()))
            span = time.monotonic() - started
            uart_after = uart_counts()
            snapshots(fd, write, 'after')
            result = {'type': 'result', 'elapsed_s': span, 'cycles': cycles, 'failures': failed,
                      'observed_hz': cycles / span, 'status_errors': dict(errors),
                      'id13_status65': dict(status_registers), 'motor_write_instructions': 0,
                      'uart_delta': {key: uart_after[key] - uart_before[key] for key in uart_before},
                      'id13_range': {'position_raw': [min(positions), max(positions)],
                                     'voltage_v': [min(voltages), max(voltages)],
                                     'temperature_c': [min(temperatures), max(temperatures)]} if positions else None}
            write(result)
            print(json.dumps(result), flush=True)
        finally:
            if fd is not None:
                os.close(fd)
            if paused:
                os.kill(pid, signal.SIGCONT)
                print('same read-only robotd resumed', flush=True)
    print(json.dumps({'mode_after': status()['mode'], 'evidence': str(output_path)}), flush=True)


if __name__ == '__main__':
    main()
