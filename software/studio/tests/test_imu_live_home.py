import asyncio
import copy
import unittest
from unittest import mock
from tests import test_imu_home_coarse as fixtures
from studio.commit import load_pending, save_pending
from studio.imu import Refused


class LiveHomeTest(unittest.TestCase):
    setUp = fixtures.HomeCoarseTest.setUp
    tearDown = fixtures.HomeCoarseTest.tearDown
    backend = fixtures.HomeCoarseTest.backend
    home = fixtures.HomeCoarseTest.home

    def test_contract_allows_live_verification_with_new_hold_and_age_fields(self):
        backend = self.home()
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        bus = backend.rd.bus.value
        bus['imu_calibration_hold_ready'] = True
        bus['home_start_guard'] = {'max_joint_error_deg': 8}
        bus['native']['imu_age_upper_bound_ms'] = bus['native'].pop('imu_age_ms')
        state = backend.view()['coarse_sampling']
        self.assertTrue(state['live_verify'])
        self.assertTrue(state['hold_ready'])
        self.assertIsNone(state['reason'])
        self.assertFalse(state['needs_unload'])

    def test_explicit_unsettled_hold_blocks_even_with_small_joint_error(self):
        backend = self.home()
        backend.rd.bus.value['imu_calibration_hold_ready'] = False
        state = backend.view()['coarse_sampling']
        self.assertFalse(state['hold_ready'])
        self.assertIsNotNone(state['reason'])

    def test_legacy_host_joint_sample_age_cannot_replace_missing_imu_age(self):
        backend = self.home()
        backend.rd.bus.value['imu_bus'] = {'protocol':'dxl2', 'board':'sflp_124_12', 'identity':[1, 2, 3]}
        backend.rd.bus.value['native'].update(imu_age_ms=None, sample_age_ms=1)
        self.assertIn('不新鲜', backend.view()['coarse_sampling']['reason'] or '')

    def test_live_verify_calls_daemon_without_mode_switch(self):
        backend = self.home()
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        backend.view()
        quat = [1., 0., 0., 0.]
        backend.last_result = {'ok': True, 'mode': 'coarse', 'mount': quat}
        backend._result_basis = backend._sampling_basis(backend.rd.bus.value)
        old = backend.rd.calib.value['calibration']['imu_mount_quat']
        save_pending(backend.state_dir, 'bus', quat, imu_bus={'protocol': 'dxl2',
                     'board': 'sflp_124_12', 'identity': [1, 2, 3]})
        async def daemon(method, params, timeout):
            if params['action'] == 'verify-imu-home':
                return {**backend.rd.verify(params), 'home_held': True}
            return backend.rd.calib.value
        with mock.patch.object(backend.rd.cmd, 'call', side_effect=daemon) as rpc, \
             mock.patch('studio.helper.imu_recalibrate') as switch:
            result = asyncio.run(backend.handle({'op':'verify','confirmed':True,'mount_wxyz':quat}))
        self.assertTrue(result['ok'])
        self.assertEqual(rpc.call_args_list[1].args[1], {
            'action':'verify-imu-home','confirmed':True,'mount_wxyz':quat,'expected_mount_wxyz':old})
        switch.assert_not_called()
        self.assertIsNone(backend.home_verification_task)
        self.assertTrue(result['calibration']['calibration']['imu_mount_verified'])
        self.assertEqual(load_pending(backend.state_dir)['imu_bus']['protocol'], 'dxl2')

    def test_matching_readback_without_verification_proof_does_not_report_success(self):
        backend = self.home()
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        backend.view()
        quat = [1., 0., 0., 0.]
        backend.last_result = {'ok': True, 'mode': 'coarse', 'mount': quat}
        backend._result_basis = backend._sampling_basis(backend.rd.bus.value)
        async def daemon(method, params, timeout):
            if params['action'] == 'verify-imu-home':
                backend.rd.verify(params)
                return {'home_held': True}
            return backend.rd.calib.value
        with mock.patch.object(backend.rd.cmd, 'call', side_effect=daemon):
            with self.assertRaisesRegex(Refused, '核验结果'):
                asyncio.run(backend.handle({'op':'verify','confirmed':True,'mount_wxyz':quat}))
        self.assertTrue(backend.job['failed'])

    def test_live_status_that_drops_contract_still_requires_manual_unload(self):
        backend = self.home()
        status = copy.deepcopy(backend.rd.calib.value)
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        backend.view()
        quat = [1., 0., 0., 0.]
        backend.last_result = {'ok': True, 'mode': 'coarse', 'mount': quat}
        backend._result_basis = backend._sampling_basis(backend.rd.bus.value)
        with mock.patch.object(backend, '_rpc_once', new_callable=mock.AsyncMock, return_value=status) as rpc, \
             mock.patch('studio.helper.imu_recalibrate') as switch:
            with self.assertRaisesRegex(Refused, '先扶稳并卸力'):
                asyncio.run(backend.handle({'op':'verify','confirmed':True,'mount_wxyz':quat}))
        self.assertEqual([call.args[1]['action'] for call in rpc.call_args_list], ['calibration-status'])
        switch.assert_not_called()

    def test_live_status_must_confirm_source_before_verify(self):
        backend = self.home()
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        backend.view()
        quat = [1., 0., 0., 0.]
        backend.last_result = {'ok': True, 'mode': 'coarse', 'mount': quat}
        backend._result_basis = backend._sampling_basis(backend.rd.bus.value)
        status = {**backend.rd.calib.value, 'imu_source': 'bno08x'}
        with mock.patch.object(backend, '_rpc_once', new_callable=mock.AsyncMock, return_value=status) as rpc:
            with self.assertRaisesRegex(Refused, '重新采'):
                asyncio.run(backend.handle({'op':'verify','confirmed':True,'mount_wxyz':quat}))
        self.assertEqual([call.args[1]['action'] for call in rpc.call_args_list], ['calibration-status'])
