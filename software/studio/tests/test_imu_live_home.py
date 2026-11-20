import asyncio
import unittest
from unittest import mock
from tests import test_imu_home_coarse as fixtures


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

    def test_live_verify_calls_daemon_without_mode_switch(self):
        backend = self.home()
        backend.rd.calib.value['imu_home_verification_contract'] = 'home_mount_v1'
        backend.view()
        quat = [1., 0., 0., 0.]
        backend.last_result = {'ok': True, 'mode': 'coarse', 'mount': quat}
        backend._result_basis = backend._sampling_basis(backend.rd.bus.value)
        old = backend.rd.calib.value['calibration']['imu_mount_quat']
        status = backend.rd.calib.value
        with mock.patch.object(backend, '_rpc_once', new_callable=mock.AsyncMock,
                               side_effect=[status, {'home_held': True}]) as rpc, \
             mock.patch.object(backend, '_require_verification_proof'), \
             mock.patch.object(backend, '_verified_readback', new_callable=mock.AsyncMock, return_value=status), \
             mock.patch('studio.helper.imu_recalibrate') as switch:
            result = asyncio.run(backend.handle({'op':'verify','confirmed':True,'mount_wxyz':quat}))
        self.assertTrue(result['ok'])
        self.assertEqual(rpc.call_args_list[1].args[1], {
            'action':'verify-imu-home','confirmed':True,'mount_wxyz':quat,'expected_mount_wxyz':old})
        switch.assert_not_called()
        self.assertIsNone(backend.home_verification_task)
