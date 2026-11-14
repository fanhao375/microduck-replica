"""Exercise actuator boundaries, failure cleanup and measured tracking analysis."""

import copy
import importlib.util
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).parent / 'diagnostics'
sys.path.insert(0, str(DIRECTORY))
import leg_tracking_probe as probe
import analyze_leg_tracking as analysis


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        self.now += .0001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeBus:
    def __init__(self, fail_sample=False):
        self.regs = {sid: bytearray(80) for sid in probe.ALL_IDS}
        for regs in self.regs.values():
            regs[33] = 4
            regs[42:44] = struct.pack('<H', 2048)
            regs[50:53] = bytes([5, 0, 0])
        self.writes = []
        self.fail_sample = fail_sample

    def read(self, sid, address, length):
        return 0, bytes(self.regs[sid][address:address + length])

    def read_u8(self, sid, address):
        return self.read(sid, address, 1)[1][0]

    def write(self, sid, address, data):
        self.writes.append((sid, address, bytes(data)))
        self.regs[sid][address:address + len(data)] = data
        return 0

    def write_u16(self, sid, address, value):
        return self.write(sid, address, struct.pack('<H', value))

    def state(self, sid):
        powered = bool(self.regs[sid][40])
        if powered and self.fail_sample:
            raise OSError('injected read timeout')
        goal = int.from_bytes(self.regs[sid][42:44], 'little')
        return {'pos': goal, 'goal': goal, 'speed': 0, 'load': 0, 'current_ma': 0,
                'err': 0, 'status': 0, 'volt': 7.8, 'temp': 30}


def calibration():
    return {'joints': [{'id': sid, 'direction': -1, 'zero_raw': 2048,
                        'min_rad': -.5, 'max_rad': .5, 'calibrated': True}
                       for sid in probe.ALL_IDS]}


class ProbeTests(unittest.TestCase):
    def test_all_generated_commands_are_bounded_and_sine_returns_to_center(self):
        stages = probe.build_stages(probe.LEG_IDS, 3, pairs=True)
        self.assertEqual(len(stages), 60)
        for stage in stages:
            self.assertLessEqual(max(map(abs, stage['values_deg'])), 3)
            if stage['kind'] == 'sine':
                self.assertAlmostEqual(stage['values_deg'][-1], 0)

    def test_packet_gate_rejects_other_joints_eeprom_broadcast_and_excess_travel(self):
        def allowed(sid, instruction, data):
            return probe.permitted(sid, instruction, data, {21: 2048}, 35, {(21, 50): {5, 6}})
        self.assertTrue(allowed(21, 3, b'\x28\x01'))
        self.assertTrue(allowed(21, 3, b'\x32\x06'))
        self.assertTrue(allowed(21, 3, bytes([42]) + struct.pack('<H', 2083)))
        for sid, instruction, data in [(20, 3, b'\x28\x01'), (254, 3, b'\x28\x00'),
                                        (21, 3, b'\x15\x06'), (21, 3, b'\x32\x07'),
                                        (21, 3, bytes([42]) + struct.pack('<H', 2084)),
                                        (21, 0x83, b''), (21, 6, b''), (21, 3, b'\x29\x01')]:
            with self.subTest(sid=sid, instruction=instruction, data=data):
                self.assertFalse(allowed(sid, instruction, data))

    def test_direction_and_calibrated_limit_are_applied(self):
        j = calibration()['joints'][0]
        self.assertLess(probe.raw_target(j, 2048, 3), 2048)
        j['min_rad'] = -.01
        with self.assertRaises(RuntimeError):
            probe.raw_target(j, 2048, -3)

    def exercise(self, bus, cal=None):
        rows = []
        args = SimpleNamespace(ids=[21], amplitude_deg=3, kp=6, kd=1)
        stage = {'ids': (21,), 'kind': 'ramp_hold', 'hz': 20,
                 'values_deg': [0, 1, 2, 3], 'ramp_samples': 2}
        clock = FakeClock()
        with patch.object(probe.time, 'monotonic', clock.monotonic), \
                patch.object(probe.time, 'sleep', clock.sleep):
            result = probe.run_test(bus, args, [stage], cal or calibration(), {}, {}, rows.append)
        return result, rows

    def test_complete_trial_preloads_before_torque_and_restores_gains(self):
        bus = FakeBus()
        result, rows = self.exercise(bus)
        self.assertTrue(result['completed'])
        self.assertEqual(bus.writes[0], (21, 42, struct.pack('<H', 2048)))
        power_index = bus.writes.index((21, 40, b'\1'))
        self.assertGreater(power_index, 0)
        self.assertEqual(bus.regs[21][50:53], bytes([5, 0, 0]))
        self.assertTrue(all(regs[40] == 0 for regs in bus.regs.values()))
        self.assertTrue(all(sid == 21 and address in (40, 42, 50, 51)
                            for sid, address, _ in bus.writes))
        self.assertEqual(sum(r['type'] == 'sample' for r in rows), 4)

    def test_feedback_timeout_aborts_and_still_relaxes_and_restores(self):
        bus = FakeBus(fail_sample=True)
        result, rows = self.exercise(bus)
        self.assertFalse(result['completed'])
        self.assertIn('injected read timeout', result['failure'])
        self.assertEqual(bus.regs[21][40], 0)
        self.assertEqual(bus.regs[21][50:53], bytes([5, 0, 0]))
        self.assertTrue(next(r for r in rows if r['type'] == 'cleanup')['confirmed'])

    def test_invalid_trajectory_or_powered_servo_refuses_before_any_write(self):
        for mode in ('limit', 'powered', 'uncalibrated'):
            bus, cal = FakeBus(), calibration()
            if mode == 'limit':
                next(j for j in cal['joints'] if j['id'] == 21)['max_rad'] = .01
            elif mode == 'powered':
                bus.regs[11][40] = 1
            else:
                next(j for j in cal['joints'] if j['id'] == 21)['calibrated'] = False
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                self.exercise(bus, cal)
            self.assertEqual(bus.writes, [])

    def test_cleanup_continues_after_one_joint_cannot_be_relaxed(self):
        bus = FakeBus()
        original_write = bus.write
        def write(sid, address, data):
            if sid == 21 and address == 40:
                raise OSError('unreachable')
            return original_write(sid, address, data)
        bus.write = write
        snapshots = {sid: {'pdi': [5, 0, 0]} for sid in (21, 11)}
        errors = probe.cleanup(bus, {21, 11}, snapshots, lambda _: None)
        self.assertTrue(errors)
        self.assertIn((11, 40, b'\0'), bus.writes)


def sine_rows(lag_s=.1, ratio=.8):
    rows = []
    for index in range(241):
        sent = index / 20
        received = sent + .008
        frequency = .25
        rows.append({'type': 'sample', 'stage': 0, 'id': 21, 'kind': 'sine', 'index': index,
                     'hz': 20, 'frequency_hz': frequency, 'hold': False,
                     'command_at': sent, 'at': received,
                     'target': 2048 + 35 * math.sin(2 * math.pi * frequency * sent),
                     'state': {'pos': 2048 + 35 * ratio * math.sin(2 * math.pi * frequency * (received - lag_s)),
                               'goal': 2048, 'err': 0, 'status': 0, 'volt': 7.8, 'temp': 30}})
    return rows


class AnalysisTests(unittest.TestCase):
    def test_recovers_known_phase_lag_and_amplitude_with_rx_timing(self):
        result = analysis.stage_metrics(sine_rows())
        self.assertAlmostEqual(result['lag_ms'], 100, places=6)
        self.assertAlmostEqual(result['amplitude_ratio'], .8, places=6)
        self.assertLess(result['sine_residual_deg'], 1e-8)

    def test_stationary_or_short_signal_does_not_invent_phase(self):
        rows = sine_rows()
        for row in rows:
            row['state']['pos'] = 2048
        self.assertNotIn('lag_ms', analysis.stage_metrics(rows))
        self.assertNotIn('lag_ms', analysis.stage_metrics(sine_rows()[:20]))

    def test_incomplete_capture_and_timing_gap_are_visible(self):
        rows = sine_rows()
        del rows[50:55]
        report = analysis.analyze([{'type': 'meta', 'schema': 'leg-tracking-v1', 'plan': {'ids': [21]}}] + rows)
        self.assertTrue(any('incomplete' in i for i in report['issues']))
        self.assertTrue(any('not confirmed' in i for i in report['issues']))
        self.assertTrue(any('timing gaps' in i for i in report['issues']))
        self.assertFalse(report['walking_verified'])

    def test_nonfinite_and_reversed_times_are_rejected(self):
        for bad in ('nonfinite', 'reversed'):
            rows = sine_rows()
            if bad == 'nonfinite':
                rows[3]['state']['pos'] = float('nan')
            else:
                rows[3]['at'] = rows[2]['at']
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                analysis.analyze([{'type': 'meta', 'schema': 'leg-tracking-v1', 'plan': {'ids': [21]}}] + rows)


if __name__ == '__main__':
    unittest.main()
