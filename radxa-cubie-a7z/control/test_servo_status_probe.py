"""Packet boundaries and preservation of device errors in the read-only probe."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).parent / 'diagnostics/servo_status_probe.py'
spec = importlib.util.spec_from_file_location('servo_status_probe', PATH)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def frame(sid, error, payload):
    body = bytes([sid, len(payload) + 2, error]) + payload
    return b'\xff\xff' + body + bytes([~sum(body) & 255])


class ProbeTests(unittest.TestCase):
    def test_every_motor_write_instruction_is_rejected(self):
        for instruction in set(range(256)) - {1, 2, 0x82}:
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                P.request(11, instruction, b'')

    def test_sync_reads_are_fixed_telemetry_groups_only(self):
        for group in P.ALLOWED_GROUPS:
            packet = P.request(254, 0x82, bytes([56, 15, *group]))
            self.assertEqual(sum(packet[2:]) & 255, 255)
        for params in (bytes([40, 1, 11]), bytes([56, 15, 253]), bytes([56, 15, 11, 11])):
            with self.assertRaises(ValueError):
                P.request(254, 0x82, params)

    def test_unapproved_register_reads_are_rejected(self):
        for sid, address, length in ((200, 40, 1), (11, 42, 2), (11, 65, 2), (253, 56, 15)):
            with self.assertRaises(ValueError):
                P.request(sid, 2, bytes([address, length]))

    def test_valid_error_frame_is_retained_with_register_65(self):
        payload = bytearray(15)
        payload[9] = 2
        raw = frame(11, 2, payload)
        with patch.object(P.termios, 'tcflush'), patch.object(P.os, 'write', return_value=8), \
             patch.object(P.select, 'select', return_value=([7], [], [])), \
             patch.object(P.os, 'read', return_value=raw):
            row = P.collect(7, b'12345678', (11,), 15, P.time.monotonic() + 1)
        self.assertEqual(row['missing'], [])
        self.assertEqual(row['frames'][11], raw.hex())
        self.assertEqual(P.feedback(raw.hex())['status_register_65'], 2)
        self.assertEqual(bytes.fromhex(row['frames'][11])[4], 2)

    def test_corrupt_reply_does_not_become_a_success(self):
        raw = frame(11, 0, bytes(15))[:-1] + b'\0'
        with patch.object(P.termios, 'tcflush'), patch.object(P.os, 'write', return_value=8), \
             patch.object(P.select, 'select', side_effect=[([7], [], []), ([], [], [])]), \
             patch.object(P.os, 'read', return_value=raw):
            row = P.collect(7, b'12345678', (11,), 15, P.time.monotonic() + 1)
        self.assertEqual(row['missing'], [11])
        self.assertGreater(row['bad_checksums'], 0)
        self.assertEqual(row['frames'], {})


if __name__ == '__main__':
    unittest.main()
