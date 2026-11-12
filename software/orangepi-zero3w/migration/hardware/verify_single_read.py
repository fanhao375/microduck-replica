#!/usr/bin/env python3
"""Observe RPC and cached UART counters; never become a serial-port owner."""

import argparse
import json
import re
import socket
import subprocess
import time
from pathlib import Path


def status():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect('/run/robotd.sock')
        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
        with client.makefile('rb') as stream:
            response = json.loads(stream.readline(1024 * 1024))
    return response['result']


def uart():
    # ctrl_info only reads driver caches; the separate status attribute consumes RX.
    text = Path('/sys/class/tty/ttyS0/device/ctrl_info').read_text()
    values = {key: int(value) for key, value in re.findall(r'^\s*(tx|rx|parity|frame|overrun|throttled)\s*:\s*(\d+)', text, re.M)}
    values['actual_baud'] = int(re.search(r'last baud\s*:\s*(\d+)', text)[1])
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--grouped', action='store_true')
    args = parser.parse_args()
    pid = subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'robotd'], text=True).strip()
    environment = (Path('/proc') / pid / 'environ').read_bytes().split(b'\0')
    setting = [item for item in environment if item.startswith(b'CUBIE_A7Z_SYNC_GROUP_SIZE=')]
    assert setting == ([b'CUBIE_A7Z_SYNC_GROUP_SIZE=3'] if args.grouped else [])
    first, before = status(), uart()
    assert first['motor_writes_blocked'] is True and first['mode'] == 'read_only'
    started = time.monotonic()
    observations = []
    for index in range(60):
        time.sleep(1)
        current = status()
        trace = current['device_communication']['last_trace']
        sample = current['last_sample']
        observations.append({'cycle': current['total_cycles'], 'elapsed_us': trace['elapsed_us'],
                             'missing_ids': trace['missing_ids'], 'bad_checksums': trace['bad_checksums'],
                             'servo_count': len(sample['servos']),
                             'imu_ready': sample['imu']['ready'], 'imu_valid': sample['imu']['valid']})
        if index % 10 == 9:
            print(json.dumps({'seconds': index + 1, 'cycles': current['total_cycles'],
                              'failures': current['total_communication_failures'], 'hz': current['achieved_hz']}), flush=True)
    last, after = status(), uart()
    cycles = last['total_cycles'] - first['total_cycles']
    delta = {key: after[key] - before[key] for key in before if key != 'actual_baud'}
    report = {'duration_s': time.monotonic() - started, 'configuration_verified': True,
              'group_size': 3 if args.grouped else None, 'expected_tx_bytes_per_cycle': 64 if args.grouped else 24,
              'uartclk': int(Path('/sys/class/tty/ttyS0/uartclk').read_text()), 'actual_baud': after['actual_baud'],
              'cycle_delta': cycles, 'uart_delta': delta, 'tx_bytes_per_cycle': delta['tx'] / cycles,
              'communication_failure_delta': last['total_communication_failures'] - first['total_communication_failures'],
              'imu_fault_delta': last['total_imu_faults'] - first['total_imu_faults'],
              'first': first, 'last': last, 'observations': observations}
    name = 'hat-grouped-read-verification.json' if args.grouped else 'hat-single-read-verification.json'
    target = Path('/var/lib/orangepi-migration') / name
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('first', 'last', 'observations')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
