#!/usr/bin/env python3
"""Observe robotd without issuing any motor-bus or motion commands (Python 3.9+)."""

import argparse
import json
import math
from pathlib import Path
import re
import socket
import sys
import time

SERVO_IDS = set(range(10, 15)) | set(range(20, 25)) | set(range(30, 35))
DEVICE_IDS = SERVO_IDS | {200}
MIN_SECONDS = 300
RPC_METHOD = "robot.busStatus"
MAX_REPLY = 1024 * 1024


def read_status(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(path))
        request = {"jsonrpc": "2.0", "id": 1, "method": RPC_METHOD, "params": {}}
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("rb") as stream:
            line = stream.readline(MAX_REPLY + 1)
        if len(line) > MAX_REPLY or not line.endswith(b"\n"):
            raise ValueError("robotd response truncated or too large")
        reply = json.loads(line)
        if reply.get("id") != 1 or "error" in reply or not isinstance(reply.get("result"), dict):
            raise ValueError("robotd did not return a busStatus result")
        return reply["result"]


def uart_counts(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith("0:") and "mmio:0x02500000" in line:
            values = dict(re.findall(r"\b(rx|tx|oe|fe|pe|brk|bo):(\d+)", line))
            return {key: int(values.get(key, 0)) for key in ("rx", "tx", "oe", "fe", "pe", "brk", "bo")}
    raise ValueError("A7Z UART0 counters not found")


def inspect_status(status):
    """Fail closed on stale, partial, non-commissioning or unrecognised telemetry."""
    problems = []
    if status.get("mode") == "read_only":
        torque = status.get("torque_at_start", [])
        if (status.get("motor_writes_blocked") is not True or len(torque) != 15
                or {row.get("id") for row in torque} != SERVO_IDS
                or any(row.get("torque_register_at_start") != 0 for row in torque)):
            problems.append("read-only writes guard or torque-off startup read not confirmed")
    else:
        if status.get("mode") != "commissioning" or status.get("torque_state") != "off":
            problems.append("expected commissioning with torque off")
        if status.get("policy_motion_enabled") is not False or status.get("motion") is not None:
            problems.append("motion/strategy is not idle")
        if status.get("torque_off_pending") is not None or status.get("power_pending") is not False:
            problems.append("power transition pending or unknown")
    hz = status.get("achieved_hz")
    if status.get("target_hz") != 50 or not isinstance(hz, (int, float)) or not 49 <= hz <= 51:
        problems.append("50 Hz target/achieved rate not confirmed")
    comm = status.get("device_communication", {})
    if comm.get("classification") != "exclusive-v2" or not comm.get("session"):
        problems.append("unsupported communication counters/session")
    if set(comm.get("by_id", {})) != {str(i) for i in DEVICE_IDS}:
        problems.append("communication counters do not cover all 16 IDs")
    trace = comm.get("last_trace", {})
    expected = {"address": 56, "length": 15, "rx_bytes": 336, "missing_ids": [],
                "bad_checksums": 0, "discarded": 0, "request_sent": True,
                "partial_received": False, "deadline_exceeded": False, "complete_but_late": False}
    if any(trace.get(key) != value for key, value in expected.items()):
        problems.append("last sync read is incomplete, corrupt, late or unsent")
    elapsed = trace.get("elapsed_us")
    if not isinstance(elapsed, (int, float)) or not 0 < elapsed < 20000:
        problems.append("sync read did not finish within 20 ms")
    sample = status.get("last_sample") or {}
    servos = sample.get("servos", [])
    if len(servos) != 15 or {s.get("id") for s in servos} != SERVO_IDS:
        problems.append("latest sample does not contain exactly 15 servos")
    imu = sample.get("imu") or {}
    if (imu.get("id") != 200 or imu.get("source") != "bus"
            or imu.get("ready") is not True or imu.get("valid") is not True):
        problems.append("ID 200 IMU sample is not valid/ready")
    if status.get("imu_bus", {}).get("identity") != [6, 4, 77, 68, 0]:
        problems.append("MicroDuck IMU v1.2 identity not confirmed")
    if sample.get("cycle") != status.get("total_cycles"):
        problems.append("last complete sample is behind the current cycle")
    if status.get("last_cycle_error") is not None:
        problems.append("current cycle has an error")
    return problems


def counters(status):
    comm = status["device_communication"]
    result = {"reads": comm["total_reads"], "success": status["total_success"],
              "failures": status["total_communication_failures"]}
    if status.get("mode") == "read_only":
        result["imu_faults"] = status["total_imu_faults"]
    for key in ("incomplete_timeout_reads", "missing_reads", "complete_but_late", "unsent_reads"):
        result[key] = comm[key]
    for device, values in comm["by_id"].items():
        for key in ("missing", "timeout"):
            result["id_" + device + "_" + key] = values[key]
    if any(type(v) is not int or v < 0 for v in result.values()):
        raise ValueError("non-integer or negative communication counter")
    return result


class Window:
    def __init__(self):
        self.first = None
        self.last = None
        self.problems = set()
        self.observations = 0

    def add(self, status, uart, at):
        self.problems.update(inspect_status(status))
        current = {"at": at, "session": status["device_communication"]["session"],
                   "counters": counters(status), "uart": uart}
        self.observations += 1
        if self.last:
            if current["session"] != self.last["session"]:
                self.problems.add("robotd session changed during observation")
            if not 0 < at - self.last["at"] <= 3:
                self.problems.add("observation gap exceeds 3 seconds or clock regressed")
            for key, value in current["counters"].items():
                previous = self.last["counters"].get(key)
                if previous is None or value < previous:
                    self.problems.add("communication counters reset or changed schema")
                elif key not in ("reads", "success") and value != previous:
                    self.problems.add("communication failure counters increased")
            for key, value in uart.items():
                previous = self.last["uart"][key]
                if value < previous or (key not in ("rx", "tx") and value != previous):
                    self.problems.add("UART error counter increased or reset")
            # A cached successful report must not certify a silent/restarted control loop.
            span = at - self.last["at"]
            if span >= 0.5:
                advanced = current["counters"]["reads"] - self.last["counters"]["reads"]
                if not 40 * span <= advanced <= 60 * span:
                    self.problems.add("sync reads stopped or rate deviated between observations")
        self.first = self.first or current
        self.last = current

    def report(self):
        if self.first is None or self.last is None:
            return {"passed": False, "problems": sorted(self.problems | {"no complete observations"})}
        elapsed = self.last["at"] - self.first["at"]
        delta = {k: v - self.first["counters"][k] for k, v in self.last["counters"].items()}
        uart_delta = {k: v - self.first["uart"][k] for k, v in self.last["uart"].items()}
        problems = set(self.problems)
        if elapsed < MIN_SECONDS:
            problems.add("requires at least 300 seconds of continuous observation")
        hz = delta["reads"] / elapsed if elapsed > 0 else 0
        if not 49 <= hz <= 51 or delta["reads"] != delta["success"]:
            problems.add("not all 50 Hz reads completed successfully")
        return {"passed": not problems, "elapsed_s": elapsed, "observations": self.observations,
                "observed_hz": hz, "delta": delta, "uart_delta": uart_delta,
                "session": self.last["session"], "problems": sorted(problems)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/run/robotd.sock")
    parser.add_argument("--uart-stats", default="/proc/tty/driver/uart-ng")
    parser.add_argument("--seconds", type=float, default=MIN_SECONDS)
    parser.add_argument("--output", type=Path, required=True, help="new JSONL evidence file")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be a finite positive number")
    window = Window()
    with args.output.open("x", encoding="utf-8") as output:
        started = time.monotonic()
        next_tick = started
        try:
            while True:
                status = read_status(args.socket)
                uart = uart_counts(args.uart_stats)
                at = time.monotonic()
                window.add(status, uart, at)
                output.write(json.dumps({"type": "sample", "at": at, "wall_time": time.time(),
                                         "bus_status": status, "uart": uart}) + "\n")
                output.flush()
                if window.observations % 10 == 1:
                    report = window.report()
                    print(json.dumps({"elapsed_s": report["elapsed_s"], "observed_hz": report["observed_hz"],
                                      "reads": report["delta"]["reads"], "failures": report["delta"]["failures"],
                                      "uart_errors": {k: v for k, v in report["uart_delta"].items()
                                                      if k not in ("rx", "tx")}, "problems": report["problems"]}), flush=True)
                if at - window.first["at"] >= args.seconds:
                    break
                next_tick += 1
                time.sleep(max(0, next_tick - time.monotonic()))
        except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
            window.problems.add("observation interrupted: " + str(exc))
        report = window.report()
        output.write(json.dumps({"type": "result", **report}) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
