#!/usr/bin/env python3
"""Record commanded/measured joints and health. Never sends actuator intents.

The operator controls any physical trial separately. A capture while limp is
useful to verify the recorder, but is not evidence of walking correctness.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import threading
import time


MAX_LINE = 1 << 20


def request(method):
    if method == 'robot.subscribe':
        params = {'hz': 25}
    elif method in ('robot.busStatus', 'robot.health'):
        params = {}
    else:
        raise ValueError('recorder only permits read-only RPCs')
    return (json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                        'params': params}) + '\n').encode()


def connect(path):
    client = socket.socket(socket.AF_UNIX)
    try:
        client.settimeout(3)
        client.connect(str(path))
        return client
    except BaseException:
        client.close()
        raise


def read_message(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line or len(line) > MAX_LINE or not line.endswith(b'\n'):
        raise ValueError('missing, oversized or truncated RPC record')
    msg = json.loads(line)
    if not isinstance(msg, dict):
        raise ValueError('RPC record is not an object')
    if 'error' in msg:
        raise ValueError(str(msg['error']))
    return msg


def query(path, method):
    with connect(path) as client:
        client.sendall(request(method))
        with client.makefile('rb') as stream:
            reply = read_message(stream)
        if reply.get('id') != 1 or not isinstance(reply.get('result'), dict):
            raise ValueError('RPC result missing')
        return reply['result']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--socket', type=Path, default=Path('/run/robotd.sock'))
    ap.add_argument('--seconds', type=int, choices=range(5, 61), required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    stop = threading.Event()
    lock = threading.Lock()
    counts = Counter()
    policies = Counter()
    first_ns = last_ns = None

    with args.output.open('x') as output:
        def write(source, value):
            row = {'source': source, 'received_ns': time.monotonic_ns(),
                   'wall_time': time.time(), 'value': value}
            with lock:
                output.write(json.dumps(row, separators=(',', ':')) + '\n')
                counts[source] += 1
                if source != 'robot.state':
                    output.flush()

        def slow_samples():
            while not stop.is_set():
                for method in ('robot.busStatus', 'robot.health'):
                    if stop.is_set():
                        return
                    try:
                        write(method, query(args.socket, method))
                    except (OSError, ValueError) as error:
                        write('error', {'method': method, 'detail': str(error)})
                stop.wait(1)

        started = time.monotonic()
        worker = threading.Thread(target=slow_samples, daemon=True)
        worker.start()
        try:
            with connect(args.socket) as client:
                client.sendall(request('robot.subscribe'))
                with client.makefile('rb') as stream:
                    while time.monotonic() - started < args.seconds:
                        msg = read_message(stream)
                        if msg.get('method') != 'robot.state':
                            continue
                        value = msg.get('params')
                        if not isinstance(value, dict):
                            raise ValueError('state payload is not an object')
                        if len(value.get('joints', [])) != 15 or len(value.get('targets', [])) != 15:
                            raise ValueError('state must contain all 15 measured and commanded joints')
                        now = time.monotonic_ns()
                        first_ns = first_ns if first_ns is not None else now
                        last_ns = now
                        policies[value.get('policy', 'unknown')] += 1
                        write('robot.state', value)
        finally:
            stop.set()
            worker.join(timeout=4)
        span = 0 if first_ns is None or last_ns is None else (last_ns - first_ns) / 1e9
        result = {'path': str(args.output), 'elapsed_s': time.monotonic() - started,
                  'counts': dict(counts), 'policies': dict(policies),
                  'observed_state_hz': (counts['robot.state'] - 1) / span if span else None,
                  'motor_commands_sent': 0, 'walking_verified': False}
        write('capture_result', result)
    print(json.dumps(result))
    if counts['robot.state'] < args.seconds * 15 or counts['error']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
