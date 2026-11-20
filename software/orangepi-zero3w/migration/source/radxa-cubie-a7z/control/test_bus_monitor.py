import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

import bus_monitor as monitor


def status(cycle=1000):
    return {
        "mode": "commissioning", "torque_state": "off", "policy_motion_enabled": False,
        "motion": None, "power_pending": False, "torque_off_pending": None,
        "target_hz": 50, "achieved_hz": 50.0, "total_cycles": cycle,
        "total_success": cycle - 10, "total_communication_failures": 10,
        "imu_bus": {"identity": [6, 4, 77, 68, 0]}, "last_cycle_error": None,
        "last_sample": {"cycle": cycle, "servos": [{"id": i} for i in sorted(monitor.SERVO_IDS)],
                        "imu": {"id": 200, "ready": True, "valid": True, "source": "bus"}},
        "device_communication": {
            "classification": "exclusive-v2", "session": "test-session", "total_reads": cycle,
            "incomplete_timeout_reads": 10, "missing_reads": 0, "unsent_reads": 0,
            "complete_but_late": 0,
            "by_id": {str(i): {"timeout": 10 if i == 200 else 0, "missing": 0}
                      for i in monitor.DEVICE_IDS},
            "last_trace": {"address": 56, "length": 15, "rx_bytes": 336, "missing_ids": [],
                           "bad_checksums": 0, "discarded": 0, "request_sent": True,
                           "partial_received": False, "deadline_exceeded": False,
                           "complete_but_late": False, "elapsed_us": 4800},
        },
    }


UART = dict(rx=1000, tx=100, oe=3, fe=0, pe=0, brk=0, bo=0)


def fill_window(change=None, seconds=300):
    window = monitor.Window()
    for second in range(seconds + 1):
        sample = status(1000 + second * 50)
        uart = dict(UART, rx=1000 + second * 16800, tx=100 + second * 1200)
        if change:
            change(sample, uart, second)
        window.add(sample, uart, 100.0 + second)
    return window.report()


class GateTests(unittest.TestCase):
    def test_read_only_requires_all_torque_reads_and_a_real_packet_guard(self):
        sample = status()
        sample["mode"] = "read_only"
        sample["motor_writes_blocked"] = True
        sample["total_imu_faults"] = 0
        sample["torque_at_start"] = [{"id": i, "torque_register_at_start": 0}
                                    for i in sorted(monitor.SERVO_IDS)]
        self.assertEqual(monitor.inspect_status(sample), [])
        sample["torque_at_start"][0]["torque_register_at_start"] = 1
        self.assertTrue(monitor.inspect_status(sample))
        sample["torque_at_start"][0]["torque_register_at_start"] = 0
        sample["motor_writes_blocked"] = False
        self.assertTrue(monitor.inspect_status(sample))

    def test_full_window_passes_despite_historical_failures(self):
        report = fill_window()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["delta"]["reads"], 15000)
        self.assertEqual(report["delta"]["success"], 15000)

    def test_short_clean_window_is_not_a_pass(self):
        self.assertFalse(fill_window(seconds=299)["passed"])

    def test_one_dropped_read_invalidates_whole_window(self):
        def drop(sample, uart, second):
            if second >= 50:
                sample["total_communication_failures"] += 1
                sample["total_success"] -= 1
                sample["device_communication"]["incomplete_timeout_reads"] += 1
                sample["device_communication"]["by_id"]["34"]["timeout"] += 1
        self.assertFalse(fill_window(drop)["passed"])

    def test_uart_overrun_fails_even_when_health_would_be_ok(self):
        def overrun(sample, uart, second):
            if second >= 50:
                uart["oe"] += 1
        report = fill_window(overrun)
        self.assertFalse(report["passed"])
        self.assertEqual(report["uart_delta"]["oe"], 1)

    def test_intermediate_counter_reset_is_not_hidden_by_recovery(self):
        def reset(sample, uart, second):
            if second == 50:
                sample["total_communication_failures"] = 0
        self.assertFalse(fill_window(reset)["passed"])

    def test_session_change_is_not_hidden_by_matching_counters(self):
        def restart(sample, uart, second):
            if second >= 50:
                sample["device_communication"]["session"] = "another-session"
        self.assertFalse(fill_window(restart)["passed"])

    def test_cached_reports_do_not_pass(self):
        window = monitor.Window()
        for second in range(301):
            window.add(status(), UART, 100.0 + second)
        self.assertFalse(window.report()["passed"])

    def test_sparse_snapshots_do_not_certify_continuity(self):
        window = monitor.Window()
        window.add(status(), UART, 100)
        window.add(status(16000), UART, 400)
        self.assertFalse(window.report()["passed"])

    def test_partial_late_corrupt_and_wrong_modes_are_rejected(self):
        for field, value in [("rx_bytes", 315), ("bad_checksums", 1), ("discarded", 1),
                             ("partial_received", True), ("deadline_exceeded", True),
                             ("request_sent", False), ("elapsed_us", 20000)]:
            with self.subTest(field=field):
                sample = status()
                sample["device_communication"]["last_trace"][field] = value
                self.assertTrue(monitor.inspect_status(sample))
        for field, value in [("mode", "motion"), ("torque_state", "on"),
                             ("policy_motion_enabled", True), ("achieved_hz", float("nan")),
                             ("target_hz", 25)]:
            sample = status()
            sample[field] = value
            self.assertTrue(monitor.inspect_status(sample))

    def test_wrong_or_duplicate_servo_and_invalid_imu_are_rejected(self):
        for key in ("servos", "imu"):
            sample = status()
            if key == "servos":
                sample["last_sample"][key][-1]["id"] = 29
            else:
                sample["last_sample"][key]["valid"] = False
            self.assertTrue(monitor.inspect_status(sample))


class TransportTests(unittest.TestCase):
    def test_only_bus_status_is_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "robot.sock")
            received = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(1)
                def reply():
                    with server.accept()[0] as client, client.makefile("rb") as stream:
                        request = json.loads(stream.readline())
                        received.append(request)
                        client.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "result": status()}) + "\n").encode())
                worker = threading.Thread(target=reply)
                worker.start()
                self.assertEqual(monitor.read_status(path), status())
                worker.join(timeout=2)
            self.assertEqual(received, [{"jsonrpc": "2.0", "id": 1, "method": "robot.busStatus", "params": {}}])

    def test_uart0_selected_without_opening_tty_or_mmio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uart-ng"
            path.write_text("serinfo:1.0\n0: uart:SUNXI mmio:0x02500000 irq:421 tx:100 rx:1000 oe:3 RTS\n"
                            "1: uart:SUNXI mmio:0x02501000 irq:422 tx:900 rx:8000 oe:90\n")
            self.assertEqual(monitor.uart_counts(path), UART)


if __name__ == "__main__":
    unittest.main()
