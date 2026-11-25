"""Model library persistence and real RPC confirmation semantics (no board/network)."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from studio.models import Models, ModelError
except ModuleNotFoundError:
    Models = None
    ModelError = ValueError

NAMES = ('left_hip_yaw,left_hip_roll,left_hip_pitch,left_knee,left_ankle,neck_pitch,'
         'head_pitch,head_yaw,head_roll,right_hip_yaw,right_hip_roll,right_hip_pitch,right_knee,right_ankle')
DEFAULT = {'a': 'ground_pick', 'x': 'roulade', 'lb': 'kick_left', 'rb': 'kick_right', 'dpad_down': 'sit_toggle'}
SLOTS = ['walk', 'sitstand', 'ground_pick', 'kick_left', 'kick_right', 'roulade', 'stand']


def contract(home=0.0, kp=5):
    return {'task': 'Mjlab-Velocity-Test', 'input_dim': 61, 'output_dim': 14, 'obs_normalizer': 'baked_in_onnx',
            'onnx_metadata': {'joint_names': NAMES},
            'model': {'joints': [{'name': n, 'home_rad': home} for n in NAMES.split(',')],
                      'actuators': [{'kp_fw': kp}]}}


class Runtime:
    def __init__(self):
        self.paths = {s: f'/opt/robot/feetech-ft5-r5/models/{s}/policy.onnx' for s in SLOTS}
        self.loaded = dict(self.paths)
        self.binds = dict(DEFAULT)
        self.overrides = set()
        self.calls = []
        self.online = True
        self.torque = False
        self.policy = False
        self.accept_only = None
        self.fail_binding_read = False
        self.responses = {}
        self.known_skills = ['kick_left', 'kick_right', 'roulade']
        self.report_overridden = True
        self.skills = [{'name': s, 'path': self.paths[s], 'duration': 2.0} for s in ('kick_left', 'kick_right', 'roulade')]
        # 「更多动作」用：名字在这里的动作，登记会成功但重新加载失败（主程序把原因写进 change_error）。
        self.fail_load = set()
        self.change_error = None

    async def __call__(self, method, params=None, timeout=2.0):
        params = params or {}
        self.calls.append((method, copy.deepcopy(params)))
        if not self.online:
            raise OSError('offline')
        if method in self.responses:
            return copy.deepcopy(self.responses[method])
        if method == 'robot.busStatus':
            return {'mode': 'motion', 'policy_enabled': self.policy,
                    'native': {'torque_enabled': self.torque, 'torque_state_confirmed': self.torque},
                    'loaded_models': {s: {'path': p} for s, p in self.loaded.items() if p},
                    'active_model': None}
        if method == 'robot.policies':
            return {'enabled': True, 'change_error': self.change_error, 'skills': list(self.known_skills),
                    'slots': [{'slot': s, 'path': p, 'overridden': self.report_overridden, 'error': None} for s, p in self.paths.items()]}
        if method == 'robot.skills':
            return {'skills': copy.deepcopy(self.skills), 'built_in': ['ground_pick', 'sit_toggle']}
        if method == 'pad.bindings':
            if self.fail_binding_read:
                raise OSError('readback failed')
            return {'bindings': [{'button': b, 'skill': s, 'overridden': s != DEFAULT[b], 'error': None}
                                 for b, s in self.binds.items()]}
        if method == 'robot.loadPolicy':
            self.paths[params['slot']] = None if params['path'] == 'none' else params['path']
            if params['path'] != self.accept_only:
                self.loaded[params['slot']] = self.paths[params['slot']]
            return {'accepted': True}
        if method == 'robot.setSkill':
            name = params['name']
            sk = next((s for s in self.skills if s['name'] == name), None)
            if sk is None:
                if 'duration' not in params:
                    return {'accepted': False, 'reason': 'new skill needs a duration'}
                sk = {'name': name, 'overridden': True}
                self.skills.append(sk)
            sk.update({k: v for k, v in params.items() if k != 'name'})
            if name in self.fail_load:
                self.change_error = f'cannot load {name}'
                if name in self.known_skills:
                    self.known_skills.remove(name)
            elif params['path'] != self.accept_only:
                self.change_error = None
                self.loaded[name] = params['path']
                if name not in self.known_skills:
                    self.known_skills.append(name)
            return {'accepted': True}
        if method == 'robot.removeSkill':
            name = params['name']
            before = len(self.skills)
            self.skills = [s for s in self.skills if s['name'] != name]
            if name in self.known_skills:
                self.known_skills.remove(name)
            self.loaded.pop(name, None)
            self.change_error = None
            return {'accepted': True, 'reason': None if len(self.skills) < before else 'no such skill'}
        if method == 'pad.bind':
            b = params['button']
            if params.get('skill') and params['skill'] not in self.known_skills + ['ground_pick', 'sit_toggle']:
                return {'accepted': False, 'reason': 'no such skill'}
            if 'skill' in params:
                self.binds[b] = params['skill']
                self.overrides.add(b)
            else:
                self.binds[b] = DEFAULT[b]
                self.overrides.discard(b)
            return {'accepted': True}
        raise AssertionError(method)


class LibraryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if Models is None:
            self.fail('模型管理服务尚未实现')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'root'
        self.state = self.root / 'var/lib/microduck-studio'
        self.state.mkdir(parents=True)
        self.runtime = Runtime()
        self.config = self.root / 'etc/robot/feetech-ft5/robotd.toml'
        self.config.parent.mkdir(parents=True)
        self.config.write_text('[policy]\n' + '\n'.join(f'{s} = "{p}"' for s, p in self.runtime.paths.items()), encoding='utf-8')
        for path in self.runtime.paths.values():
            d = self.root / path.lstrip('/')
            d.parent.mkdir(parents=True)
            d.write_bytes(b'default-model')
            d.with_name('deployment-contract.json').write_text(json.dumps(contract()), encoding='utf-8')
        self.rd = SimpleNamespace(addr='tcp:127.0.0.1:1')
        self.service = self.new_service()
        self.pkg = Path(self.tmp.name) / 'model.zip'
        self.pkg.write_bytes(b'package fixture handled by archive boundary tests')
        self.new_contract = contract()
        self.next_slot = 'walk'
        self.extract_patch = patch('studio.models.extract_package', self.extract)
        self.extract_patch.start()
        self.addCleanup(self.extract_patch.stop)
        timing = patch.multiple('studio.models', READBACK_TIMEOUT=0.025, READBACK_POLL=0.003)
        timing.start()
        self.addCleanup(timing.stop)

    def new_service(self):
        return Models(self.rd, None, self.state, root=self.root,
                      validator=lambda p: {'valid': True}, rpc=self.runtime)

    def extract(self, archive, dest):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'policy.onnx').write_bytes(b'new-policy')
        (dest / 'deployment-contract.json').write_text(json.dumps(self.new_contract), encoding='utf-8')
        return {'onnx': 'policy.onnx', 'sha256': hashlib.sha256(b'new-policy').hexdigest(), 'task': 'Mjlab-Velocity-Test',
                'slot': self.next_slot, 'name': '导入模型', 'files': ['policy.onnx', 'deployment-contract.json']}

    async def install(self):
        v = await self.service.import_package(self.pkg, '新模型')
        return next(i for i in v['items'] if i['source'] == 'imported')

    async def test_service_exists(self):
        self.assertIsNotNone(Models, '模型管理服务尚未实现')

    async def test_install_does_not_select_and_rename_survives_restart(self):
        item = await self.install()
        self.assertEqual(item['slots'], [])
        self.assertEqual(item['allowed_slots'], ['walk'])
        self.assertFalse(any(m in ('robot.loadPolicy', 'pad.bind') for m, _ in self.runtime.calls))
        await self.service.handle({'op': 'rename', 'id': item['id'], 'name': '自己训练的走路'})
        v = await self.new_service().view()
        self.assertEqual(next(i for i in v['items'] if i['id'] == item['id'])['name'], '自己训练的走路')

    async def test_validation_failure_leaves_no_half_install(self):
        self.service.validator = lambda p: {'valid': False, 'error': 'bad graph'}
        with self.assertRaisesRegex(ModelError, '预检|bad graph'):
            await self.install()
        self.assertFalse([p for p in (self.state / 'models').iterdir() if p.is_dir()])

    async def test_builtin_cannot_be_deleted(self):
        v = await self.service.view()
        item = next(i for i in v['items'] if i['source'] == 'builtin')
        with self.assertRaisesRegex(ModelError, '内置'):
            await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_select_confirms_loaded_path_and_restore_uses_explicit_default(self):
        item = await self.install()
        before = self.runtime.paths['walk']
        v = await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        self.assertEqual(next(s for s in v['slots'] if s['slot'] == 'walk')['model_id'], item['id'])
        await self.service.handle({'op': 'restore', 'slot': 'walk'})
        self.assertEqual(self.runtime.paths['walk'], before)
        self.assertIn(('robot.loadPolicy', {'slot': 'walk', 'path': before}), self.runtime.calls)

    async def test_accepted_without_loaded_readback_rolls_back(self):
        item = await self.install()
        before = self.runtime.paths['walk']
        self.runtime.accept_only = next(i for i in (await self.service.view())['items'] if i['id'] == item['id'])['path']
        with self.assertRaisesRegex(ModelError, '回退|恢复'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        self.assertEqual(self.runtime.paths['walk'], before)

    async def test_select_requires_fresh_confirmed_torque_off(self):
        item = await self.install()
        self.runtime.torque = True
        with self.assertRaisesRegex(ModelError, '卸力'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        self.assertFalse(any(m == 'robot.loadPolicy' for m, _ in self.runtime.calls))

    async def test_different_controller_home_or_p_is_refused(self):
        for c in (contract(home=0.1), contract(kp=6)):
            self.new_contract = c
            item = await self.install()
            with self.assertRaisesRegex(ModelError, 'HOME|舵机|P|站姿'):
                await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})

    async def test_wrong_slot_is_refused(self):
        item = await self.install()
        with self.assertRaisesRegex(ModelError, '用途|槽'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'roulade'})

    async def test_referenced_import_cannot_be_deleted_and_unused_can(self):
        item = await self.install()
        await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        with self.assertRaisesRegex(ModelError, '选用|引用|使用'):
            await self.service.handle({'op': 'delete', 'id': item['id']})
        await self.service.handle({'op': 'restore', 'slot': 'walk'})
        v = await self.service.handle({'op': 'delete', 'id': item['id']})
        self.assertNotIn(item['id'], [i['id'] for i in v['items']])

    async def test_skill_reference_prevents_delete(self):
        item = await self.install()
        self.runtime.skills.append({'name': 'custom', 'path': item['path'], 'duration': 1})
        with self.assertRaisesRegex(ModelError, '引用|使用'):
            await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_offline_rename_allowed_but_delete_refused(self):
        item = await self.install()
        self.runtime.online = False
        await self.service.handle({'op': 'rename', 'id': item['id'], 'name': '离线名称'})
        with self.assertRaisesRegex(ModelError, '连接|主程序|确认'):
            await self.service.handle({'op': 'delete', 'id': item['id']})
        self.assertTrue(any(i['id'] == item['id'] for i in (await self.service.view())['items']))

    async def test_binding_conflict_needs_explicit_replace(self):
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'bind', 'button': 'a', 'slot': 'roulade'})
        self.assertEqual(ctx.exception.code, 'binding_conflict')
        self.assertEqual(self.runtime.binds['a'], 'ground_pick')
        await self.service.handle({'op': 'bind', 'button': 'a', 'slot': 'roulade', 'replace': True})
        self.assertEqual(self.runtime.binds['a'], 'roulade')

    async def test_unbind_uses_empty_string_reset_omits_skill(self):
        await self.service.handle({'op': 'unbind', 'button': 'x'})
        self.assertEqual(self.runtime.binds['x'], '')
        await self.service.handle({'op': 'reset_bindings'})
        self.assertEqual(self.runtime.binds, DEFAULT)
        self.assertIn(('pad.bind', {'button': 'x'}), self.runtime.calls)

    async def test_canonical_skill_path_changes_with_selected_model(self):
        self.next_slot = 'roulade'
        item = await self.install()
        await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'roulade'})
        self.assertEqual(next(s for s in self.runtime.skills if s['name'] == 'roulade')['path'], item['path'])
        self.assertEqual(self.runtime.loaded['roulade'], item['path'])

    async def test_broken_catalog_does_not_recapture_import_as_builtin(self):
        item = await self.install()
        await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        (self.state / 'models/library.json').write_text('{broken', encoding='utf-8')
        service = self.new_service()
        v = await service.view()
        self.assertTrue(v['error'])
        self.assertFalse(v['capabilities']['select'])
        self.assertFalse(any(i['source'] == 'builtin' and i.get('path') == item['path'] for i in v['items']))

    async def test_bad_button_and_name_are_rejected(self):
        item = await self.install()
        for args in ({'op': 'bind', 'button': 'start', 'slot': 'walk'},
                     {'op': 'rename', 'id': item['id'], 'name': ''},
                     {'op': 'delete', 'id': '../../escape'}):
            with self.assertRaises(ModelError):
                await self.service.handle(args)

    async def test_real_contract_shape_allows_passive_and_base_joints(self):
        con = contract()
        con['model']['joints'].insert(0, {'name': 'trunk_base_freejoint'})
        con['model']['joints'].append({'name': 'passive_left_knee', 'home_rad': 0.0})
        for path in self.runtime.paths.values():
            (self.root / path.lstrip('/')).with_name('deployment-contract.json').write_text(json.dumps(con), encoding='utf-8')
        self.new_contract = con
        item = await self.install()
        await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})

    async def test_incomplete_runtime_reports_do_not_allow_delete(self):
        item = await self.install()
        for method, response in (('robot.policies', {}), ('robot.skills', {}),
                                 ('robot.busStatus', {}), ('pad.bindings', {})):
            self.runtime.responses[method] = response
            with self.assertRaises(ModelError):
                await self.service.handle({'op': 'delete', 'id': item['id']})
            self.runtime.responses.clear()

    async def test_bad_json_argument_types_are_expected_errors(self):
        for args in ({'op': 'bind', 'button': [], 'slot': 'roulade'},
                     {'op': 'select', 'id': [], 'slot': {}},
                     {'op': 'unbind', 'button': {}}):
            with self.assertRaises(ModelError):
                await self.service.handle(args)

    async def test_bad_metadata_does_not_break_view(self):
        item = await self.install()
        meta = self.state / 'models' / item['id'] / '.studio-model.json'
        data = json.loads(meta.read_text(encoding='utf-8'))
        data['sha256'] = 12
        meta.write_text(json.dumps(data), encoding='utf-8')
        v = await self.service.view()
        self.assertTrue(v['error'])
        self.assertFalse(v['capabilities']['select'])

    async def test_missing_canonical_skill_is_refused_before_writing(self):
        self.next_slot = 'roulade'
        item = await self.install()
        self.runtime.known_skills.remove('roulade')
        self.runtime.skills = [s for s in self.runtime.skills if s['name'] != 'roulade']
        with self.assertRaisesRegex(ModelError, '技能|动作'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'roulade'})
        self.assertFalse(any(m == 'robot.loadPolicy' for m, _ in self.runtime.calls))

    async def test_binding_readback_failure_cannot_be_reported_as_success(self):
        original = self.runtime.__call__
        async def lose_readback(method, params=None, timeout=2.0):
            result = await original(method, params, timeout)
            if method == 'pad.bind':
                self.runtime.fail_binding_read = True
            return result
        self.service.rpc = lose_readback
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'unbind', 'button': 'x'})
        self.assertEqual(ctx.exception.code, 'rollback_failed')

    async def test_binding_to_the_default_skill_is_confirmed_without_override(self):
        await self.service.handle({'op': 'bind', 'button': 'a', 'slot': 'ground_pick'})
        self.assertEqual(self.runtime.binds['a'], 'ground_pick')

    async def test_initially_disabled_slot_can_be_restored_and_import_deleted(self):
        self.runtime.paths['stand'] = None
        self.runtime.loaded['stand'] = None
        self.next_slot = 'stand'
        item = await self.install()
        before = next(s for s in (await self.service.view())['slots'] if s['slot'] == 'stand')
        self.assertTrue(before['default_disabled'])
        self.assertTrue(before['is_default'])
        selected = await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'stand'})
        self.assertFalse(next(s for s in selected['slots'] if s['slot'] == 'stand')['is_default'])
        restored = await self.service.handle({'op': 'restore', 'slot': 'stand'})
        self.assertTrue(next(s for s in restored['slots'] if s['slot'] == 'stand')['is_default'])
        self.assertIsNone(self.runtime.paths['stand'])
        await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_persisted_config_reference_prevents_delete_even_when_not_loaded(self):
        item = await self.install()
        self.config.write_text('[policy]\nwalk="' + item['path'] + '"\n', encoding='utf-8')
        v = await self.service.view()
        self.assertFalse(next(i for i in v['items'] if i['id'] == item['id'])['can_delete'])
        with self.assertRaisesRegex(ModelError, '配置|引用'):
            await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_unreadable_persisted_config_prevents_delete(self):
        item = await self.install()
        self.config.write_text('invalid [', encoding='utf-8')
        with self.assertRaisesRegex(ModelError, '配置|确认'):
            await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_missing_policy_is_reported_not_installed(self):
        item = await self.install()
        (self.state / 'models' / item['id'] / 'policy.onnx').unlink()
        v = await self.service.view()
        row = next(i for i in v['items'] if i['id'] == item['id'])
        self.assertFalse(row['installed'])
        self.assertTrue(row['reason'])
        with self.assertRaisesRegex(ModelError, '文件|模型'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})

    async def test_same_path_already_result_does_not_require_overridden_to_change(self):
        self.runtime.report_overridden = False
        await self.service.view()
        await self.service.handle({'op': 'restore', 'slot': 'walk'})

    async def test_wrong_observation_interface_is_refused(self):
        self.new_contract['input_dim'] = 62
        item = await self.install()
        with self.assertRaisesRegex(ModelError, '观测|接口'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})

    async def test_corrupt_default_signature_is_reported_as_catalog_error(self):
        await self.service.view()
        path = self.state / 'models/library.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['defaults']['walk']['signature']['home'][0] = 'broken'
        path.write_text(json.dumps(data), encoding='utf-8')
        v = await self.service.view()
        self.assertTrue(v['error'])
        self.assertFalse(v['capabilities']['select'])

    async def test_alias_path_in_config_still_protects_import_from_delete(self):
        item = await self.install()
        alias = item['path'].replace('/policy.onnx', '/../' + item['id'] + '/policy.onnx')
        self.config.write_text('[policy]\nwalk="' + alias + '"\n', encoding='utf-8')
        with self.assertRaisesRegex(ModelError, '引用'):
            await self.service.handle({'op': 'delete', 'id': item['id']})

    async def test_missing_current_contract_gives_actionable_error(self):
        item = await self.install()
        (self.root / self.runtime.paths['walk'].lstrip('/')).with_name('deployment-contract.json').unlink()
        with self.assertRaisesRegex(ModelError, '合同|文件'):
            await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})

    async def test_import_name_requires_text(self):
        with self.assertRaises(ModelError):
            await self.service.import_package(self.pkg, name=0)

    async def test_disk_write_error_is_an_expected_chinese_service_error(self):
        item = await self.install()
        with patch('studio.models._atomic_json', side_effect=PermissionError('read only')):
            with self.assertRaisesRegex(ModelError, '保存|写入|文件'):
                await self.service.handle({'op': 'rename', 'id': item['id'], 'name': '更新名'})


if __name__ == '__main__':
    unittest.main()
