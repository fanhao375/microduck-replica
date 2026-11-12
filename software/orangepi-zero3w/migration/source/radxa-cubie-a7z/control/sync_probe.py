#!/usr/bin/env python3
"""Read-only 50 Hz grouped Sync Read bench; never sends a motor write instruction.

The caller must suspend robotd first and guarantee SIGCONT in its own cleanup.
This tool does not change termios, pinmux, clocks or torque. Run as root on A7Z.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import select
import sys
import termios
import time

from bus_monitor import DEVICE_IDS, uart_counts

IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14, 200]


def sync_request(ids):
    if not ids or len(ids) > 3 or len(set(ids)) != len(ids) or not set(ids) <= DEVICE_IDS:
        raise ValueError("only groups of 1..3 known devices may be read")
    if 200 in ids and ids[-1] != 200:
        raise ValueError("IMU 200 must be last")
    body = bytes([254, len(ids) + 4, 0x82, 56, 15] + list(ids))
    return b"\xff\xff" + body + bytes([~sum(body) & 255])


def collect(fd, ids, deadline):
    packet = sync_request(ids)
    if os.write(fd, packet) != len(packet):
        raise OSError("short Sync Read request; no retry sent")
    replies, buffer = {}, bytearray()
    checksums = discarded = 0
    while time.monotonic() < deadline and len(replies) < len(ids):
        ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        buffer.extend(os.read(fd, 256))
        while len(buffer) >= 4:
            if buffer[:2] != b"\xff\xff" or buffer[2] >= 254 or buffer[3] != 17:
                del buffer[0]
                discarded += 1
                continue
            if len(buffer) < 21:
                break
            if sum(buffer[2:21]) & 255 != 255:
                checksums += 1
                del buffer[0]
                continue
            sid, error = buffer[2], buffer[4]
            if sid not in ids or sid in replies or error:
                raise ValueError("unexpected, duplicate or error reply: ID %d status %d" % (sid, error))
            replies[sid] = bytes(buffer[5:20])
            del buffer[:21]
    return replies, checksums, discarded + len(buffer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--paused-pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be 1..600")
    if b"radxa,cubie-a7z\0" not in Path("/sys/firmware/devicetree/base/compatible").read_bytes():
        raise ValueError("expected Cubie A7Z")
    pid = args.paused_pid
    cmdline = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
    if b"--commissioning" not in cmdline or Path(os.fsdecode(cmdline[0])).name != "robotd":
        raise ValueError("expected commissioning robotd")
    # /proc status exposes tgid state; confirm every thread is stopped, not just main.
    for task in Path("/proc/%d/task" % pid).iterdir():
        state = next(line for line in (task / "status").read_text().splitlines() if line.startswith("State:"))
        if "T (stopped)" not in state:
            raise ValueError("robotd thread not stopped: " + str(task))
    fd = os.open("/dev/ttyS2", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        if attrs[4:6] != [termios.B1000000, termios.B1000000]:
            raise ValueError("existing port is not at B1000000")
        time.sleep(0.025)  # let a pre-suspension request finish; not a warm-up sample
        termios.tcflush(fd, termios.TCIFLUSH)
        before = uart_counts("/proc/tty/driver/uart-ng")
        failures = Counter()
        total = success = 0
        worst_ms = 0
        started = time.monotonic()
        scheduled = started
        with args.output.open("x") as output:
            while time.monotonic() - started < args.seconds:
                began = time.monotonic()
                received, bad, discarded = {}, 0, 0
                for offset in range(0, len(IDS), 3):
                    block, errors, skipped = collect(fd, IDS[offset:offset + 3], began + 0.020)
                    received.update(block)
                    bad += errors
                    discarded += skipped
                    if time.monotonic() >= began + 0.020:
                        break
                elapsed = time.monotonic() - began
                worst_ms = max(worst_ms, elapsed * 1000)
                missing = sorted(DEVICE_IDS - received.keys())
                ok = not missing and not bad and not discarded and elapsed < 0.020
                total += 1
                success += int(ok)
                failures.update(missing)
                if not ok:
                    termios.tcflush(fd, termios.TCIFLUSH)
                output.write(json.dumps({"cycle": total, "at": began, "ms": elapsed * 1000,
                                         "missing": missing, "bad_checksums": bad, "discarded": discarded,
                                         "ok": ok}) + "\n")
                scheduled += 0.020
                if scheduled < time.monotonic():
                    scheduled += (int((time.monotonic() - scheduled) / 0.020) + 1) * 0.020
                time.sleep(max(0, scheduled - time.monotonic()))
            span = time.monotonic() - started
            after = uart_counts("/proc/tty/driver/uart-ng")
            result = {"type": "result", "seconds": span, "cycles": total, "success": success,
                      "failures": total - success, "missing_by_id": dict(failures), "hz": total / span,
                      "worst_ms": worst_ms, "uart_delta": {k: after[k] - before[k] for k in before},
                      "group_size": 3, "motor_write_instructions": 0}
            output.write(json.dumps(result) + "\n")
            print(json.dumps(result), flush=True)
        return 0 if total == success and 49 <= total / span <= 51 else 2
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
