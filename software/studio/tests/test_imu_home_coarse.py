"""Installation-direction collection from the real motion/HOME entry."""
import asyncio
import copy
import math
import threading
import unittest
from unittest import mock

from tests import test_imu_backend as fixtures
from studio import helper
from studio.imu import Refused
from studio.robotd import Sample


commissioning_bus, factory_calib = fixtures.commissioning_bus, fixtures.factory_calib


class HomeCoarseTest(unittest.TestCase):
    setUp = fixtures.ImuBackendTest.setUp
    tearDown = fixtures.ImuBackendTest.tearDown
    backend = fixtures.ImuBackendTest.backend
    def home(self):
        bus = commissioning_bus(mode='motion')
        bus.pop('last_sample')
        bus.update(policy_enabled=False, homed=True, home_recovery=False,
                   fresh_sample=True, all_joints_fresh=True,
                   home_start_guard={'max_joint_error_deg': .4})
        bus['native'].update(torque_enabled=True, torque_state_confirmed=True,
                             imu_ready=True, imu_age_ms=4,
                             hd1910={'restore_pending': False, 'temporary_gains_applied': False})
        b = self.backend(bus=bus, calib=factory_calib())
        b.rd.state = Sample()
        self.gravity = [0., 0., -1.]
        async def fresh():
            old = b.rd.state.value or {}
            b.rd.state.set({'t_ns': old.get('t_ns', 0) + 100000000,
                            'safety': {'gravity': list(self.gravity)}})
            return b.rd.bus.value
        b._bus_once = fresh
        return b

    def test_home_entry_offers_readonly_collection_despite_torque_on(self):
        b = self.home()
        with mock.patch.object(helper, 'imu_recalibrate') as switch:
            state = b.view()['coarse_sampling']
        self.assertTrue(state['available'])
        self.assertIsNone(state['reason'])
        self.assertTrue(state['needs_unload'])
        switch.assert_not_called()

    def test_home_still_forward_solve_without_mode_switch_or_actuator_rpc(self):
        b = self.home()
        b.rd.bus.value['home_start_guard']['max_joint_error_deg'] = 5.1
        b.rd.bus.value['imu_calibration_hold_ready'] = True
        original = copy.deepcopy(b.rd.calib.value)
        with mock.patch.object(helper, 'imu_recalibrate') as switch, \
             mock.patch.object(b, '_rpc_once') as command:
            still = asyncio.run(b.handle({'op':'coarse-capture','phase':'still','seconds':3}))
            self.gravity = [math.sin(math.radians(20)), 0., -math.cos(math.radians(20))]
            fwd = asyncio.run(b.handle({'op':'coarse-capture','phase':'fwd','seconds':3}))
            solved = asyncio.run(b.handle({'op':'solve','mode':'coarse','samples':still['samples']+fwd['samples']}))
        self.assertGreaterEqual(still['n'], 15)
        self.assertTrue(solved['ok'], solved)
        self.assertEqual(b.rd.bus.value['mode'], 'motion')
        self.assertTrue(b.rd.bus.value['native']['torque_enabled'])
        self.assertEqual(original, b.rd.calib.value)
        switch.assert_not_called()
        command.assert_not_called()

    def test_home_collection_rejects_running_unsettled_or_unknown_torque(self):
        for mutation in (
            {'policy_enabled': True}, {'homed': False}, {'home_recovery': True},
            {'home_start_guard': {'max_joint_error_deg': 5.1}},
            {'imu_calibration_hold_ready': False},
            {'home_start_guard': {'max_joint_error_deg': float('nan')}},
            {'native': {'torque_state_confirmed': None}},
            {'native': {'imu_ready': False}}, {'native': {'imu_age_ms': 2000}},
        ):
            b = self.home()
            if 'native' in mutation:
                b.rd.bus.value['native'].update(mutation['native'])
            else:
                b.rd.bus.value.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(Refused):
                asyncio.run(b.handle({'op':'coarse-capture','phase':'still','seconds':.5}))

    def test_motion_stream_does_not_recount_a_frozen_sample(self):
        b = self.home()
        asyncio.run(b._bus_once())
        async def frozen():
            return b.rd.bus.value
        b._bus_once = frozen
        with self.assertRaises(Refused):
            asyncio.run(b.handle({'op':'coarse-capture','phase':'still','seconds':2}))

    def test_home_verification_requires_unload_and_preserves_candidate(self):
        b = self.home()
        b.view()
        result = {'ok':True,'mode':'coarse','mount':[1.,0.,0.,0.]}
        b.last_result = result
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        with mock.patch.object(helper, 'imu_recalibrate') as switch, self.assertRaisesRegex(Refused, '卸力'):
            asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':result['mount']}))
        self.assertIs(b.last_result, result)
        switch.assert_not_called()

    def test_supported_home_verification_saves_without_unloading_or_restarting(self):
        b = self.home()
        b.rd.bus.value['home_start_guard']['max_joint_error_deg'] = 5.1
        b.rd.bus.value['imu_calibration_hold_ready'] = True
        b.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        b.rd.calib.value['calibration']['motion_enabled'] = True
        b.view()
        mount = [0.5] * 4
        b.last_result = {'ok': True, 'mode': 'coarse', 'mount': mount}
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        before = copy.deepcopy(b.rd.calib.value['calibration'])
        old_call = b.rd.cmd.call
        calls = []

        async def robotd(method, params, timeout):
            calls.append((method, params))
            if params.get('action') == 'verify-imu-home':
                self.assertEqual(params['expected_mount_wxyz'], before['imu_mount_quat'])
                self.assertTrue(b.rd.bus.value['native']['torque_enabled'])
                return b.rd.verify(params)
            return await old_call(method, params, timeout)

        b.rd.cmd.call = robotd
        self.assertFalse(b.view()['coarse_sampling']['needs_unload'])
        with mock.patch.object(helper, 'imu_recalibrate') as switch:
            result = asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':mount}))
        self.assertTrue(result['ok'])
        self.assertEqual(b.rd.bus.value['mode'], 'motion')
        self.assertTrue(b.rd.bus.value['native']['torque_enabled'])
        self.assertFalse(b.rd.bus.value['policy_enabled'])
        expected = {**before, 'imu_mount_quat': mount, 'imu_mount_verified': True}
        self.assertEqual(b.rd.calib.value['calibration'], expected)
        self.assertEqual([params['action'] for _, params in calls],
                         ['calibration-status', 'verify-imu-home', 'calibration-status'])
        switch.assert_not_called()

    def test_solve_refuses_samples_after_saved_mount_changes(self):
        b = self.home()
        b._capture_basis = b._sampling_basis(b.rd.bus.value)
        b.rd.calib.value['calibration']['imu_mount_quat'] = [1.,0.,0.,0.]
        with mock.patch('studio.imu.imu_calib.solve') as solve, self.assertRaisesRegex(Refused, '重新采静止'):
            asyncio.run(b.handle({'op':'solve','mode':'coarse','samples':[{'g':[0,0,-1]}]}))
        solve.assert_not_called()

    def test_full_calibration_can_verify_its_mount_without_combined_joint_commit(self):
        b = self.home()
        b.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        old_mount = b.view()['mount']['quat']
        mount = [0.5] * 4
        old_call = b.rd.cmd.call

        async def robotd(method, params, timeout):
            if params.get('action') == 'verify-imu-home':
                return b.rd.verify(params)
            return await old_call(method, params, timeout)

        b.rd.cmd.call = robotd
        with mock.patch('studio.imu.imu_calib.solve', return_value={'ok': True, 'mode': 'full', 'mount': mount}), \
             mock.patch('studio.commit.save_pending') as pending, \
             mock.patch.object(helper, 'imu_recalibrate') as switch:
            solved = asyncio.run(b.handle({'op':'solve', 'mode':'full', 'basis_mount_wxyz':old_mount,
                                           'samples':[{'g':[0,0,-1]}]}))
            self.assertTrue(solved['live_verify'])
            result = asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':mount}))
        self.assertTrue(result['ok'])
        self.assertTrue(b.rd.bus.value['native']['torque_enabled'])
        pending.assert_not_called()
        switch.assert_not_called()

    def test_capable_controller_requires_held_home_and_never_falls_back_to_restart(self):
        b = self.home()
        b.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        b.view()
        mount = [0.5] * 4
        b.last_result = {'ok': True, 'mode': 'coarse', 'mount': mount}
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        b.rd.bus.value['native'].update(torque_enabled=False, torque_state_confirmed=False)
        self.assertTrue(b.view()['coarse_sampling']['live_verify'])
        with mock.patch.object(helper, 'imu_recalibrate') as switch, self.assertRaisesRegex(Refused, '等 HOME 站稳'):
            asyncio.run(b.handle({'op':'verify', 'confirmed':True, 'mount_wxyz':mount}))
        switch.assert_not_called()

    def test_held_home_uses_available_imu_or_host_sample_age(self):
        for board, age_field in (('ft6', 'imu_age_upper_bound_ms'), ('imu_to_dxl', 'sample_age_ms')):
            b = self.home()
            native = b.rd.bus.value['native']
            native['imu_bus'] = {'protocol':'feetech', 'board':board, 'identity':[1, 2, 3]}
            del native['imu_age_ms']
            native[age_field] = 10
            self.assertIsNone(b.view()['coarse_sampling']['reason'])
            native[age_field] = 251
            self.assertIn('不新鲜', b.view()['coarse_sampling']['reason'])

    def test_solve_drops_result_if_mount_changes_during_calculation(self):
        b = self.home()
        b._capture_basis = b._sampling_basis(b.rd.bus.value)
        def changed(*args, **kwargs):
            b.rd.calib.value['calibration']['imu_mount_quat'] = [1.,0.,0.,0.]
            return {'ok':True,'mode':'coarse','mount':[1.,0.,0.,0.]}
        with mock.patch('studio.imu.imu_calib.solve', side_effect=changed), self.assertRaisesRegex(Refused, '已改变'):
            asyncio.run(b.handle({'op':'solve','mode':'coarse','samples':[{'g':[0,0,-1]}]}))
        self.assertIsNone(b.last_result)

    def test_home_candidate_survives_unload_and_enters_existing_verified_transaction(self):
        b = self.home()
        b.view()
        q = [0.5]*4
        b.last_result = {'ok':True,'mode':'coarse','mount':q}
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        b.rd.bus.value['native'].update(torque_enabled=False, torque_state_confirmed=False)
        session = {'active':False}
        actions = []
        def transaction(by, action, **kwargs):
            actions.append(action)
            if action == 'enter':
                session.update(active=True, phase='commissioning', session_id='d'*32, source='bus')
                b.rd.bus.set(commissioning_bus())
            else:
                self.assertEqual(action, 'finish')
                self.assertEqual(kwargs['quat'], q)
                session['active'] = False
                b.rd.bus.set(commissioning_bus(mode='motion'))
            return True, 'imu_'+action, copy.deepcopy(session)
        with mock.patch.object(helper, 'available', return_value=True), \
             mock.patch.object(helper, 'imu_redo_state', side_effect=lambda **_: copy.deepcopy(session)), \
             mock.patch.object(helper, 'imu_recalibrate', side_effect=transaction):
            result = asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':q}, 'test'))
        self.assertTrue(result['ok'])
        self.assertEqual(actions, ['enter', 'finish'])

    def test_home_old_contract_refuses_before_mode_switch_and_preserves_candidate(self):
        b = self.home()
        b.view()
        q = [0.5]*4
        candidate = {'ok': True, 'mode': 'coarse', 'mount': q}
        b.last_result = candidate
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        b.rd.bus.value['native'].update(torque_enabled=False, torque_state_confirmed=False)
        latest = copy.deepcopy(b.rd.calib.value)
        latest.pop('imu_verification_contract')
        original = b.rd.cmd.call

        async def old_daemon(method, params, timeout):
            if (params or {}).get('action') == 'calibration-status':
                return latest
            return await original(method, params, timeout)

        b.rd.cmd.call = old_daemon
        with mock.patch.object(helper, 'available', return_value=True), \
             mock.patch.object(helper, 'imu_recalibrate', return_value=(False, 'old_daemon', None)) as switch, \
             mock.patch.object(b.rd, 'verify', wraps=b.rd.verify) as save:
            with self.assertRaisesRegex(Refused, 'API43'):
                asyncio.run(b.handle({'op': 'verify', 'confirmed': True, 'mount_wxyz': q}))
        switch.assert_not_called()
        save.assert_not_called()
        self.assertIs(b.last_result, candidate)

    def test_verification_waits_for_poll_reconnect_after_enter(self):
        b = self.home()
        b.rd.present = True
        b.rd.hello = Sample()
        b.rd.hello.set({'build':'test'})
        b.rd.bus.set(b.rd.bus.value)
        b.rd.calib_valid_after = b.rd.hello.at
        b.rd.calib.set(b.rd.calib.value)
        b.view()
        q = [0.5]*4
        b.last_result = {'ok':True,'mode':'coarse','mount':q}
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        b.rd.bus.value['native'].update(torque_enabled=False,torque_state_confirmed=False)
        session = {'active':False}
        actions = []
        def reconnect():
            b.rd.hello.set({'build':'test'})
            b.rd.calib_valid_after = b.rd.hello.at
            b.rd.bus.set(b.rd.bus.value)
            b.rd.calib.set(b.rd.calib.value)
            b.rd.present = True
        def transaction(by, action, **kwargs):
            actions.append(action)
            if action == 'enter':
                session.update(active=True,phase='commissioning',session_id='e'*32,source='bus')
                b.rd.bus.set(commissioning_bus())
                b.rd.present = False
                timer = threading.Timer(.05,reconnect)
                timer.start()
                self.addCleanup(timer.join)
            else:
                session['active'] = False
                b.rd.bus.set(commissioning_bus(mode='motion'))
            return True,'imu_'+action,copy.deepcopy(session)
        with mock.patch.object(helper,'available',return_value=True), \
             mock.patch.object(helper,'imu_redo_state',side_effect=lambda **_:copy.deepcopy(session)), \
             mock.patch.object(helper,'imu_recalibrate',side_effect=transaction):
            result=asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':q},'test'))
        self.assertTrue(result['ok'])
        self.assertEqual(actions,['enter','finish'])

    def test_verify_refuses_fresh_board_change_even_when_poll_cache_is_old(self):
        b = self.home()
        b.rd.bus.value['imu_bus'] = {'protocol':'feetech','board':'ft6','identity':[1,2,3]}
        b.view()
        b.last_result = {'ok':True,'mode':'coarse','mount':[1.,0.,0.,0.]}
        b._result_basis = b._sampling_basis(b.rd.bus.value)
        fresh_bus = copy.deepcopy(b.rd.bus.value)
        fresh_bus['imu_bus'] = {'protocol':'feetech','board':'imu_to_dxl','identity':[9,9,9]}
        fresh_bus['native'].update(torque_enabled=False,torque_state_confirmed=False)
        async def replacement():
            return fresh_bus
        b._bus_once = replacement
        with mock.patch.object(helper, 'imu_recalibrate') as switch, self.assertRaisesRegex(Refused, '已改变'):
            asyncio.run(b.handle({'op':'verify','confirmed':True,'mount_wxyz':[1.,0.,0.,0.]}))
        switch.assert_not_called()
        self.assertIsNone(b.last_result)

