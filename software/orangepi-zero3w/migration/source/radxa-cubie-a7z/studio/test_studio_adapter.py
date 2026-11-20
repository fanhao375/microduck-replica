"""Focused tests against the clean Studio export plus the A7Z compatibility patch."""
import asyncio
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(os.environ.get('CUBIE_STUDIO_SOURCE', '/private/tmp/cubie-studio-build/software/studio'))
sys.path[:0] = [str(SOURCE / 'deps'), str(SOURCE)]
loader = importlib.machinery.SourceFileLoader('a7z_helper', str(SOURCE / 'deploy/microduck-helper'))
spec = importlib.util.spec_from_loader(loader.name, loader)
H = importlib.util.module_from_spec(spec)
loader.exec_module(H)

from studio.robotd import Sample
from studio import robotd as R
from studio.servo import view_from_robotd, IDS
from studio.state import posture
from studio.startup_status import sampled_joint_feedback
from studio.bus.feetech import FeetechBus
from studio.bus.sim_bus import SimSerial
from studio import pad as P


def reading():
    return {'mode': 'read_only', 'motor_writes_blocked': True, 'total_cycles': 123,
            'updated_at_us': 1000000, 'last_cycle_error': None, 'last_sample': {'cycle': 123, 'at_us': 1000000, 'servos': [
                {'id': sid, 'position_raw': 1071, 'voltage_v': 7.3, 'temperature_c': 27}
                for sid in IDS]}}


class AdapterTests(unittest.TestCase):
    def test_polling_keeps_display_fresh_and_stopped_feedback_expires(self):
        # Exercise the real scheduler across several display-expiry windows.
        for mode in ('motion', 'commissioning', 'read_only'):
            with self.subTest(mode=mode):
                clock = SimpleNamespace(now=100.0)
                rd = R.Robotd('unix:/unused', 'none', Mock())
                rd._ask_calib = Mock()
                rd._check_build = Mock()
                payload = reading()
                payload['mode'] = mode
                if mode == 'motion':
                    payload['native'] = {'joints': [
                        {'id': sid, 'feedback_fresh': True, 'sample_age_ms': 20}
                        for sid in IDS]}

                async def reply(method, *args, **kwargs):
                    return payload if method == 'robot.busStatus' else {}

                rd.poll = SimpleNamespace(call=reply, reconnects=0)
                observed = []

                async def tick(seconds):
                    clock.now += seconds
                    rows = sampled_joint_feedback(rd.bus.value, receipt_age_s=rd.bus.age())
                    observed.append(sum(row['feedback_fresh'] for row in rows))
                    if clock.now >= 103.0:
                        raise asyncio.CancelledError

                async def exercise():
                    try:
                        await rd._poll_loop()
                    except asyncio.CancelledError:
                        pass

                with patch.object(R, 'time', SimpleNamespace(monotonic=lambda: clock.now)), \
                     patch.object(R.asyncio, 'sleep', tick):
                    asyncio.run(exercise())
                    self.assertGreater(len(observed), 50)
                    self.assertEqual(set(observed), {15})
                    clock.now += .6
                    rows = sampled_joint_feedback(rd.bus.value, receipt_age_s=rd.bus.age())
                    self.assertFalse(any(row['feedback_fresh'] for row in rows))

    def test_disabled_configd_keeps_virtual_pad_watcher_running(self):
        with patch.object(P, 'ConfigdPort', side_effect=AssertionError('disabled RPC created')), \
             patch.object(P, 'PaddTap', return_value=None), \
             patch.object(P, 'RobotPort', return_value=None), \
             patch.object(P.shutil, 'which', return_value=None):
            ops = P.default_pad(Mock(), configd_addr='none')
        try:
            self.assertIsNone(ops.configd)
            ops.w.update(poll=.01, status_watch=.02)
            ops._watch()
            deadline = time.monotonic() + 1
            while ops._status_error is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(ops._status_error, '这只鸭子没配 configd')
            self.assertIsNone(ops._worker_error)
            self.assertTrue(ops._worker.is_alive())
            self.assertEqual(ops._pad_attached(time.monotonic()), (False, None))
        finally:
            ops.close()

    def test_enabled_configd_keeps_its_real_status_port(self):
        port = Mock()
        with patch.object(P, 'ConfigdPort', return_value=port) as factory, \
             patch.object(P, 'PadOps') as ops:
            P.default_pad(Mock(), configd_addr='unix:/run/configd.sock')
        factory.assert_called_once_with('unix:/run/configd.sock')
        self.assertIs(ops.call_args.kwargs['configd'], port)

    def test_ready_readonly_keeps_its_distinct_posture(self):
        bus = reading()
        self.assertTrue(H.readonly_ready(bus))
        self.assertEqual(H.judge(bus, None, None, False)[0], 'ok')
        self.assertEqual(posture(True, bus, None, None), 'read_only')

    def test_failed_stale_missing_or_unguarded_feedback_cannot_authorize_take(self):
        for change in ('failed', 'stale', 'missing', 'unguarded'):
            bus = reading()
            if change == 'failed':
                bus['last_cycle_error'] = '0x02'
            elif change == 'stale':
                bus['last_sample']['cycle'] -= 1
            elif change == 'missing':
                bus['last_sample']['servos'].pop()
            else:
                bus['motor_writes_blocked'] = False
            self.assertFalse(H.readonly_ready(bus), change)
            self.assertEqual(H.judge(bus, None, None, False)[0], 'refuse', change)

    def test_calibration_commit_cannot_treat_readonly_as_motion(self):
        op = Mock()
        with patch.object(H, 'exec_start', return_value='robotd --read-only'), patch.object(H, 'exists', return_value=False):
            self.assertEqual(H.calib_mode(op, strict=False)[0], 'read_only')
            with self.assertRaises(H.Stop):
                H.calib_mode(op, strict=True)

    def test_readonly_giveback_never_starts_controllers(self):
        op = Mock()
        op.ctx.now.return_value = 1
        op.finish.return_value = 0
        with patch.object(H, 'port_free', return_value=(True, None)), \
             patch.object(H, 'remove', return_value=False), \
             patch.object(H, 'start_robotd') as start, \
             patch.object(H, 'abort_requested', return_value=False), \
             patch.object(H, 'robotd_state', return_value={}), \
             patch.object(H, 'bus_status', return_value=reading()), \
             patch.object(H, 'hold_stop') as release, \
             patch.object(H, 'pads_start') as pads, \
             patch.object(H, 'startup_step4') as startup:
            self.assertEqual(H.give_soft(op, 0), 0)
            start.assert_called_once()
            release.assert_called_once()
            pads.assert_not_called()
            startup.assert_not_called()

    def test_display_keeps_unknown_torque_and_rejects_stale_feedback(self):
        sample = Sample()
        sample.set(reading())
        rd = SimpleNamespace(bus=sample, present=True)
        view = view_from_robotd(rd, None)
        self.assertEqual(view['online'], 15)
        self.assertTrue(all(row['torque'] is None for row in view['rows']))
        sample.fail('connection failed')
        self.assertIsNone(view_from_robotd(rd, None)['online'])

    def test_one_failed_cycle_does_not_erase_recent_fifteen_replies(self):
        bus = reading()
        bus['total_cycles'] += 1
        bus['updated_at_us'] += 20000
        bus['last_cycle_error'] = 'missing ID34'
        rows = sampled_joint_feedback(bus, receipt_age_s=.05)
        self.assertEqual(sum(row['feedback_fresh'] for row in rows), 15)
        self.assertEqual(bus['last_cycle_error'], 'missing ID34')

    def test_old_cached_native_sample_expires_despite_fresh_rpc(self):
        bus = reading()
        bus['updated_at_us'] += 600000
        self.assertFalse(any(row['feedback_fresh'] for row in sampled_joint_feedback(bus, receipt_age_s=.01)))

    def test_commissioning_feedback_and_confirmed_off_are_displayed(self):
        bus = reading()
        bus.update(mode='commissioning', torque_state='off')
        sample = Sample(); sample.set(bus)
        view = view_from_robotd(SimpleNamespace(bus=sample, present=True), None)
        self.assertEqual(view['online'], 15)
        self.assertTrue(all(row['torque'] == 0 for row in view['rows']))

    def test_network_stale_and_failed_rpc_do_not_show_online(self):
        for age,error in ((.6,None),(.01,'disconnected')):
            self.assertFalse(any(row['feedback_fresh'] for row in sampled_joint_feedback(reading(), receipt_age_s=age,bus_error=error)))

    def test_motion_display_uses_bounded_recent_reply_without_changing_control_freshness(self):
        native_row = {'id': 13, 'feedback_fresh': False, 'sample_age_ms': 30, 'volts': 7.3}
        bus = {'mode': 'motion', 'native': {'joints': [native_row]}}
        self.assertTrue(sampled_joint_feedback(bus, receipt_age_s=.01)[0]['feedback_fresh'])
        self.assertFalse(native_row['feedback_fresh'])
        native_row['sample_age_ms'] = 600
        self.assertFalse(sampled_joint_feedback(bus, receipt_age_s=.01)[0]['feedback_fresh'])

    def test_fifo_bounded_groups_keep_all_fifteen_replies(self):
        bus = FeetechBus(None, ser=SimSerial(IDS))
        reply = bus.sync_read(IDS, 56, 15)
        self.assertEqual(set(reply), set(IDS))
        self.assertTrue(all(row is not None and len(row[1]) == 15 for row in reply.values()))
        self.assertEqual(bus.stats['timeout'], 0)


if __name__ == '__main__':
    unittest.main()
