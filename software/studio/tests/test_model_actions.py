"""「更多动作」：动作包解包规则，以及登记 / 卸下 / 绑键都以主程序回读为准、失败就撤回。"""
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.model_package import PackageError, action_suggestion, extract_action_package
from studio.models import ModelError
from tests import test_models

SKILL_TOML = '''# 社区里在用的格式
[[policy.skill]]
name = "crouch"
path = "/替换为实际目录/policy.onnx"
duration = 3.0
command = [1.0, 0.0, 0.0]
unwind = [0.0, 0.0, 0.0]
unwind_s = 1.5
chain = false
[policy.skill.params]
action_scale = 1.0
gain_ratio = 1.0
'''


INLINE_SKILL = '"skill" = [{ "name" = "roulade", "duration" = 1.0 }]'


def make_zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


class ActionPackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_skill_toml_becomes_suggestion_and_everything_else_goes_to_extras(self):
        pkg = make_zip(self.dir / 'crouch.zip', {'policy.onnx': b'onnx', 'skill.toml': SKILL_TOML,
                                                 'deployment-contract.json': '{}', 'docs/README.md': 'x'})
        out = self.dir / 'out'
        data = extract_action_package(pkg, out)
        # 模型旁边只能有模型：合同文件放在旁边，主程序会去解析，坏了下次开机整套起不来。
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['extras', 'policy.onnx'])
        self.assertTrue((out / 'extras/deployment-contract.json').is_file())
        self.assertTrue((out / 'extras/docs/README.md').is_file())
        self.assertEqual(data['suggest'], {'name': 'crouch', 'duration': 3.0, 'unwind_s': 1.5, 'command': [1.0, 0.0, 0.0],
                                           'unwind': [0.0, 0.0, 0.0], 'chain': False, 'action_scale': 1.0, 'gain_ratio': 1.0})
        self.assertIn('extras/skill.toml', data['files'])

    def test_single_onnx_and_renamed_model(self):
        single = self.dir / 'bow.onnx'
        single.write_bytes(b'onnx')
        data = extract_action_package(single, self.dir / 'a')
        self.assertEqual(data['files'], ['policy.onnx'])
        self.assertEqual(data['suggest'], {})
        pkg = make_zip(self.dir / 'b.zip', {'nested/dance.onnx': b'onnx', 'nested/action.json': json.dumps(
            {'name': 'dance', 'title': '跳舞', 'duration': 4, 'action_scale': 1.0})})
        data = extract_action_package(pkg, self.dir / 'b')
        self.assertTrue((self.dir / 'b/policy.onnx').is_file())
        self.assertEqual(data['suggest']['title'], '跳舞')
        self.assertEqual(data['suggest']['duration'], 4.0)

    def test_several_models_need_a_policy_onnx(self):
        pkg = make_zip(self.dir / 'two.zip', {'a.onnx': b'1', 'b.onnx': b'2'})
        with self.assertRaisesRegex(PackageError, 'policy.onnx'):
            extract_action_package(pkg, self.dir / 'x')
        self.assertFalse((self.dir / 'x').exists())
        pkg = make_zip(self.dir / 'three.zip', {'policy.onnx': b'keep', 'straddle.onnx': b'other'})
        extract_action_package(pkg, self.dir / 'y')
        self.assertEqual((self.dir / 'y/policy.onnx').read_bytes(), b'keep')
        self.assertTrue((self.dir / 'y/extras/straddle.onnx').is_file())

    def test_unsafe_or_empty_packages_are_refused(self):
        for files, pattern in (({'../evil.onnx': b'x'}, '不规范|不安全'), ({'readme.txt': 'x'}, '没有 .onnx')):
            with self.assertRaisesRegex(PackageError, pattern):
                extract_action_package(make_zip(self.dir / 'bad.zip', files), self.dir / 'bad')
        txt = self.dir / 'model.txt'
        txt.write_text('x')
        with self.assertRaises(PackageError):
            extract_action_package(txt, self.dir / 'c')

    def test_tar_package(self):
        pkg = self.dir / 'p.tar.gz'
        with tarfile.open(pkg, 'w:gz') as tar:
            for name, data in (('policy.onnx', b'onnx'), ('skill.toml', SKILL_TOML.encode())):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.assertEqual(extract_action_package(pkg, self.dir / 't')['suggest']['name'], 'crouch')

    def test_bad_suggestions_are_dropped_not_fatal(self):
        self.assertEqual(action_suggestion({'name': 'Bad Name', 'duration': -1, 'command': [9, 0, 0],
                                            'params': {'action_scale': 0}, 'chain': 'yes'}), {})


class ActionServiceTest(test_models.LibraryTest):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.action_pkg = make_zip(Path(self.tmp.name) / 'crouch.zip', {'policy.onnx': b'crouch-model', 'skill.toml': SKILL_TOML})

    async def import_action(self, pkg=None, name=''):
        view = await self.service.import_package(pkg or self.action_pkg, name, kind='action')
        return view, view['actions'][-1]

    async def install_action(self, **extra):
        _, action = await self.import_action()
        args = {'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0,
                'command': [1.0, 0.0, 0.0], 'unwind': [0.0, 0.0, 0.0], 'unwind_s': 1.5}
        args.update(extra)
        view = await self.service.handle(args)
        return view, next(a for a in view['actions'] if a['id'] == action['id'])

    def sent(self, method):
        return [p for m, p in self.runtime.calls if m == method]

    async def test_import_only_adds_to_library_and_keeps_model_alone(self):
        view, action = await self.import_action()
        self.assertEqual(action['state'], 'none')
        self.assertEqual(action['suggest']['name'], 'crouch')
        self.assertEqual(action['name'], 'crouch')
        self.assertFalse(self.sent('robot.setSkill'))
        self.assertNotIn(action['id'], [i['id'] for i in view['items']])
        folder = self.root / action['path'].lstrip('/')
        self.assertEqual(sorted(p.name for p in folder.parent.iterdir()), ['.studio-model.json', 'extras', 'policy.onnx'])
        # 放在 actions/ 子目录：旧版 Studio 只扫模型库第一层，回退时不会把它当成库损坏。
        self.assertEqual(folder.parent.parent, self.state / 'models/actions')
        self.assertFalse([p for p in (self.state / 'models').iterdir() if p.name != 'actions' and (p / '.studio-model.json').is_file()
                          and json.loads((p / '.studio-model.json').read_text(encoding='utf-8')).get('kind') == 'action'])

    async def test_rename_action_display_name(self):
        _, action = await self.import_action()
        view = await self.service.handle({'op': 'rename', 'id': action['id'], 'name': '深蹲'})
        self.assertEqual(next(a for a in view['actions'] if a['id'] == action['id'])['name'], '深蹲')

    async def test_preflight_failure_leaves_nothing(self):
        self.service.validator = lambda p: {'valid': False, 'error': 'bad graph'}
        with self.assertRaisesRegex(ModelError, '预检'):
            await self.import_action()
        self.assertEqual([p.name for p in (self.state / 'models/actions').iterdir()], [])

    async def test_install_sends_full_spec_with_scale_one_and_confirms_load(self):
        view, action = await self.install_action()
        sent = self.sent('robot.setSkill')[-1]
        self.assertEqual(sent['name'], 'crouch')
        self.assertEqual(sent['action_scale'], 1.0)
        self.assertEqual(sent['gain_ratio'], 1.0)
        self.assertEqual(sent['command'], [1.0, 0.0, 0.0])
        self.assertEqual(sent['unwind_s'], 1.5)
        self.assertIs(sent['chain'], False)
        self.assertTrue(sent['path'].endswith('/policy.onnx'))
        self.assertEqual(action['state'], 'loaded')
        self.assertEqual(action['skill'], 'crouch')
        self.assertFalse(action['can_delete'])
        self.assertIn('crouch', self.runtime.known_skills)

    async def test_bad_names_and_numbers_are_refused_before_writing(self):
        _, action = await self.import_action()
        for extra in ({'name': 'Crouch'}, {'name': 'kick_left'}, {'name': 'sit_toggle'}, {'name': 'a' * 33},
                      {'duration': 0.1}, {'duration': 61}, {'command': [3, 0, 0]}, {'command': [1, 0]},
                      {'action_scale': 0}, {'gain_ratio': 5}, {'chain': 'no'}, {'unwind_s': 11}):
            args = {'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0}
            args.update(extra)
            with self.assertRaises(ModelError, msg=str(extra)):
                await self.service.handle(args)
        self.assertFalse(self.sent('robot.setSkill'))

    async def test_needs_relaxed_robot(self):
        _, action = await self.import_action()
        self.runtime.torque = True
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'not_idle')
        self.assertFalse(self.sent('robot.setSkill'))

    async def test_name_taken_by_someone_else_is_refused(self):
        self.runtime.skills.append({'name': 'crouch', 'path': '/home/radxa/crouch.onnx', 'duration': 2.0, 'overridden': True})
        self.runtime.known_skills.append('crouch')
        _, action = await self.import_action()
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'name_taken')

    async def test_limit(self):
        for i in range(25):
            self.runtime.skills.append({'name': f'x{i}', 'path': f'/x/{i}.onnx', 'duration': 1.0, 'overridden': True})
        _, action = await self.import_action()
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'too_many')

    async def test_load_failure_is_rolled_back(self):
        self.runtime.fail_load.add('crouch')
        _, action = await self.import_action()
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'load_failed')
        self.assertIn('撤回', str(ctx.exception))
        self.assertEqual(self.sent('robot.removeSkill')[-1], {'name': 'crouch'})
        self.assertNotIn('crouch', [s['name'] for s in self.runtime.skills])

    async def test_failed_rollback_has_its_own_code(self):
        self.runtime.fail_load.add('crouch')
        _, action = await self.import_action()
        real = self.runtime.__call__

        async def refuse_remove(method, params=None, timeout=2.0):
            if method == 'robot.removeSkill':
                return {'accepted': False, 'reason': 'config locked'}
            return await real(method, params, timeout)
        self.service.rpc = refuse_remove
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'rollback_failed')

    async def test_change_params_and_rename(self):
        _, action = await self.install_action()
        view = await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 4.0,
                                          'gain_ratio': 1.2})
        changed = next(a for a in view['actions'] if a['id'] == action['id'])
        self.assertEqual(changed['params']['duration'], 4.0)
        self.assertEqual(changed['params']['gain_ratio'], 1.2)
        view = await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'deep_squat', 'duration': 4.0})
        renamed = next(a for a in view['actions'] if a['id'] == action['id'])
        self.assertEqual(renamed['skill'], 'deep_squat')
        self.assertNotIn('crouch', self.runtime.known_skills)
        self.assertIn({'name': 'crouch'}, self.sent('robot.removeSkill'))

    async def test_rename_while_bound_is_refused(self):
        _, action = await self.install_action()
        await self.service.handle({'op': 'bind', 'button': 'rb', 'skill': 'crouch', 'replace': True})
        with self.assertRaisesRegex(ModelError, '手柄键'):
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'other', 'duration': 3.0})

    async def test_bind_conflict_then_uninstall_restores_factory_key(self):
        _, action = await self.install_action()
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'bind', 'button': 'rb', 'skill': 'crouch'})
        self.assertEqual(ctx.exception.code, 'binding_conflict')
        view = await self.service.handle({'op': 'bind', 'button': 'rb', 'skill': 'crouch', 'replace': True})
        self.assertEqual(self.runtime.binds['rb'], 'crouch')
        row = next(b for b in view['bindings'] if b['button'] == 'rb')
        self.assertEqual(row['action_name'], 'crouch')
        self.assertEqual(next(a for a in view['actions'] if a['id'] == action['id'])['buttons'], ['rb'])
        view = await self.service.handle({'op': 'action_uninstall', 'id': action['id']})
        self.assertEqual(self.runtime.binds['rb'], 'kick_right')
        # 先还键再撤登记：撤了以后再还，键会短暂指向一个不存在的动作。
        order = [m for m, _ in self.runtime.calls if m in ('pad.bind', 'robot.removeSkill')]
        self.assertEqual(order[-2:], ['pad.bind', 'robot.removeSkill'])
        gone = next(a for a in view['actions'] if a['id'] == action['id'])
        self.assertEqual(gone['state'], 'none')
        self.assertTrue(gone['can_delete'])
        await self.service.handle({'op': 'delete', 'id': action['id']})
        self.assertEqual((await self.service.view())['actions'], [])

    async def test_cannot_bind_unloaded_or_reserved_names(self):
        for skill in ('crouch', 'stand_test', '../x'):
            with self.assertRaises(ModelError):
                await self.service.handle({'op': 'bind', 'button': 'a', 'skill': skill, 'replace': True})
        self.assertFalse(self.sent('pad.bind'))

    async def test_installed_action_cannot_be_deleted(self):
        _, action = await self.install_action()
        with self.assertRaises(ModelError):
            await self.service.handle({'op': 'delete', 'id': action['id']})
        self.assertTrue((self.root / action['path'].lstrip('/')).is_file())

    async def test_seven_slots_ignore_action_entries(self):
        await self.install_action()
        item = await self.install()
        await self.service.handle({'op': 'select', 'id': item['id'], 'slot': 'walk'})
        self.assertEqual(self.runtime.paths['walk'], item['path'])
        view = await self.service.view()
        self.assertTrue(all(i.get('kind') != 'action' for i in view['items']))
        _, action = await self.import_action()
        with self.assertRaises(ModelError):
            await self.service.handle({'op': 'select', 'id': action['id'], 'slot': 'walk'})

    async def test_action_install_refuses_slot_models(self):
        item = await self.install()
        with self.assertRaisesRegex(ModelError, '使用此模型'):
            await self.service.handle({'op': 'action_install', 'id': item['id'], 'name': 'crouch', 'duration': 3.0})

    async def test_hand_added_action_is_listed_read_only_and_bindable(self):
        self.runtime.skills.append({'name': 'idle_chest', 'path': '/home/radxa/idle/policy.onnx', 'duration': 3.1,
                                    'overridden': True})
        self.runtime.known_skills.append('idle_chest')
        view = await self.service.view()
        self.assertEqual([m['name'] for m in view['manual_actions']], ['idle_chest'])
        await self.service.handle({'op': 'bind', 'button': 'lb', 'skill': 'idle_chest', 'replace': True})
        self.assertEqual(self.runtime.binds['lb'], 'idle_chest')

    async def test_contract_next_to_model_blocks_install(self):
        _, action = await self.import_action()
        (self.root / action['path'].lstrip('/')).with_name('deployment-contract.json').write_text('{}')
        with self.assertRaisesRegex(ModelError, '部署合同'):
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertFalse(self.sent('robot.setSkill'))

    def inline_config(self):
        # 镜像带的写法：动作表一整行（主程序自己改动作表不认）
        lines = ['[policy]'] + [f'{s} = "{p}"' for s, p in self.runtime.paths.items()]
        lines.append(INLINE_SKILL)
        self.config.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')

    async def test_inline_skill_table_is_fixed_before_registering(self):
        self.inline_config()
        seen = []

        def fixer():
            seen.append([m for m, _ in self.runtime.calls].count('robot.setSkill'))
            fixed = chr(10).join(['[[policy.skill]]', 'name = "roulade"', 'duration = 1.0'])
            self.config.write_text(self.config.read_text(encoding='utf-8').replace(INLINE_SKILL, fixed), encoding='utf-8')
            return True, 'ok', {}
        self.service.skill_table = fixer
        _, action = await self.install_action()
        self.assertEqual(seen, [0])          # 先改格式，再登记
        self.assertEqual(action['state'], 'loaded')

    async def test_skill_table_failure_stops_before_writing(self):
        self.inline_config()
        self.service.skill_table = lambda: (False, 'busy', None)
        _, action = await self.import_action()
        with self.assertRaises(ModelError) as ctx:
            await self.service.handle({'op': 'action_install', 'id': action['id'], 'name': 'crouch', 'duration': 3.0})
        self.assertEqual(ctx.exception.code, 'skill_table')
        self.assertFalse(self.sent('robot.setSkill'))

    async def test_old_library_records_still_read(self):
        item = await self.install()
        await self.import_action()
        view = await self.service.view()
        self.assertFalse(view['error'])
        self.assertEqual([i['id'] for i in view['items'] if i['source'] == 'imported'], [item['id']])
        self.assertEqual(len(view['actions']), 1)

    # ── 随 Studio 发的动作包（深蹲、抖一抖）────────────────────────────────

    def bundled_service(self, bundled):
        service = self.new_service()
        service.bundled = bundled
        return service

    async def test_bundled_actions_show_up_once_and_stay_deleted(self):
        bundled = Path(self.tmp.name) / 'bundled'
        bundled.mkdir()
        make_zip(bundled / 'crouch.zip', {'policy.onnx': b'crouch-model', 'skill.toml': SKILL_TOML})
        make_zip(bundled / 'idle_chest.zip', {'policy.onnx': b'chest-model', 'skill.toml': SKILL_TOML.replace(
            'name = "crouch"', 'name = "idle_chest"')})
        self.service = self.bundled_service(bundled)
        actions = (await self.service.view())['actions']
        # 只进库、不登记：页面上是「还没装」，装的参数照包里的 skill.toml 预填
        self.assertEqual([(a['name'], a['state'], a['suggest'].get('name')) for a in actions],
                         [('深蹲', 'none', 'crouch'), ('抖一抖', 'none', 'idle_chest')] if actions[0]['name'] == '深蹲'
                         else [('抖一抖', 'none', 'idle_chest'), ('深蹲', 'none', 'crouch')])
        self.assertFalse(self.sent('robot.setSkill'))
        # 重启 Studio 不会再放一份
        self.service = self.bundled_service(bundled)
        self.assertEqual(len((await self.service.view())['actions']), 2)
        # 用户删掉的，下次启动也不会回来
        crouch = next(a for a in actions if a['name'] == '深蹲')
        await self.service.handle({'op': 'delete', 'id': crouch['id']})
        self.service = self.bundled_service(bundled)
        self.assertEqual([a['name'] for a in (await self.service.view())['actions']], ['抖一抖'])

    async def test_bundled_action_already_imported_is_not_doubled(self):
        await self.import_action()
        bundled = Path(self.tmp.name) / 'bundled'
        bundled.mkdir()
        make_zip(bundled / 'crouch.zip', {'policy.onnx': b'crouch-model', 'skill.toml': SKILL_TOML})
        self.service = self.bundled_service(bundled)
        self.assertEqual(len((await self.service.view())['actions']), 1)
        self.assertTrue((self.state / 'models/actions/.bundled.json').is_file())

    async def test_bundled_action_that_fails_preflight_is_retried_next_start(self):
        bundled = Path(self.tmp.name) / 'bundled'
        bundled.mkdir()
        make_zip(bundled / 'crouch.zip', {'policy.onnx': b'crouch-model'})
        self.service = self.bundled_service(bundled)
        self.service.validator = lambda p: {'valid': False, 'error': 'bad'}
        self.assertEqual((await self.service.view())['actions'], [])
        self.service = self.bundled_service(bundled)
        self.assertEqual([a['name'] for a in (await self.service.view())['actions']], ['深蹲'])

    async def test_unreadable_bundled_record_puts_nothing_back(self):
        bundled = Path(self.tmp.name) / 'bundled'
        bundled.mkdir()
        make_zip(bundled / 'crouch.zip', {'policy.onnx': b'crouch-model'})
        (self.state / 'models/actions').mkdir(parents=True)
        (self.state / 'models/actions/.bundled.json').write_text('{oops', encoding='utf-8')
        self.service = self.bundled_service(bundled)
        self.assertEqual((await self.service.view())['actions'], [])


class ShippedBundleTest(unittest.TestCase):
    """仓库里真随包发的两个动作：解得开、参数对（幅度 1.0，深蹲带蹲的指令），模型旁边没有合同文件。"""

    def test_shipped_packages(self):
        from studio.models import BUNDLED_ACTIONS, BUNDLED_DIR
        expect = {'crouch': (3.0, [1.0, 0.0, 0.0], 1.5), 'idle_chest': (3.1, [0.0, 0.0, 0.0], 0.0)}
        with tempfile.TemporaryDirectory() as tmp:
            for key, _ in BUNDLED_ACTIONS:
                data = extract_action_package(BUNDLED_DIR / (key + '.zip'), Path(tmp) / key)
                s = data['suggest']
                self.assertEqual((s['name'], s['duration'], s['command'], s['unwind_s'], s['action_scale']),
                                 (key, *expect[key], 1.0))
                self.assertEqual(sorted(p.name for p in (Path(tmp) / key).iterdir()), ['extras', 'policy.onnx'])

    def test_shipped_packages_survive_the_update_package_filter(self):
        # 升级包只收 Studio 下面几个目录；动作包放错地方会被悄悄丢掉（11-27 第一次出包就这样）
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'updater'))
        from package import runtime_path
        from studio.models import BUNDLED_ACTIONS, BUNDLED_DIR
        studio = Path(__file__).resolve().parents[1]
        for key, _ in BUNDLED_ACTIONS:
            rel = (BUNDLED_DIR / (key + '.zip')).relative_to(studio).as_posix()
            self.assertEqual(runtime_path('software/studio/' + rel), 'studio/' + rel)


# 只借用模型库测试的环境搭建；那边的用例已经在 test_models 里跑，这里不重复。
for _name in dir(test_models.LibraryTest):
    if _name.startswith('test_') and _name not in ActionServiceTest.__dict__:
        setattr(ActionServiceTest, _name, None)


if __name__ == '__main__':
    unittest.main()
