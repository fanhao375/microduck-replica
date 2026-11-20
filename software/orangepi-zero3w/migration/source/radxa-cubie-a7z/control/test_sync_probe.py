import unittest

import sync_probe


class ReadRequestTests(unittest.TestCase):
    def test_only_fixed_telemetry_sync_read_can_be_encoded(self):
        for offset in range(0, len(sync_probe.IDS), 3):
            ids = sync_probe.IDS[offset:offset + 3]
            packet = sync_probe.sync_request(ids)
            self.assertEqual(packet[:3], b"\xff\xff\xfe")
            self.assertEqual(packet[4:7], bytes([0x82, 56, 15]))
            self.assertEqual(sum(packet[2:]) & 255, 255)
            self.assertLessEqual(len(ids) * 21, 64)

    def test_unknown_duplicate_large_and_imu_first_groups_are_refused(self):
        for ids in ([], [29], [10, 10], [10, 11, 12, 13], [200, 10]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                sync_probe.sync_request(ids)


if __name__ == "__main__":
    unittest.main()
