import json
import unittest
import os
import tempfile
from pathlib import Path

import voice_commands as voice


class ParserTests(unittest.TestCase):
    def test_chinese_command_with_optional_address_and_politeness(self):
        self.assertEqual(voice.parse_command('鸭子，请坐下。', 0.95), 'sit')
        self.assertEqual(voice.parse_command('站 起 来', 0.95), 'stand')
        self.assertEqual(voice.parse_command('起立', 0.95), 'stand')

    def test_negation_quotes_and_long_sentences_are_not_commands(self):
        for text in ['不要坐下', '别站起来', '复读坐下', '他说坐下', '坐下再站起来', '坐下吧然后再说', '你现在不要站起来']:
            self.assertIsNone(voice.parse_command(text, 1.0), text)

    def test_low_confidence_and_nonfinite_results_are_rejected(self):
        # 门槛从 0.75 降到 0.50：上机实测同一句「站起来」有时 1.00、有时 0.54，
        # 0.75 会把真实口令整片卡掉（日志 2026-11-01 09:15:43）。
        for score in [0.1, 0.49, float('nan'), float('inf')]:
            self.assertIsNone(voice.parse_command('坐下', score))
        self.assertEqual(voice.parse_command('坐下', 0.54), 'sit')

    def test_repeated_and_clear_commands_still_resolve(self):
        cases = [
            ('起立 起立 起立', 0.56, 'stand'),      # 连说几遍被并成一句
            ('前 滚 翻', 0.73, 'roulade'),
            ('站起来', 0.54, 'stand'),               # 低置信度但确实是这句
        ]
        for text, score, want in cases:
            self.assertEqual(voice.parse_command(text, score), want, text)

    def test_several_commands_in_one_utterance_split_on_address(self):
        # 端点检测不总在停顿处切断，连说的几句会并成一段（2026-11-01 上机实录）
        cases = [
            ('点头 鸭 鸭 摇头 鸭 鸭 歪头', 0.69, ['nod', 'shake', 'tilt']),
            ('鸭 鸭 前 滚 翻 鸭 鸭 低头 捡 东 西', 1.00, ['roulade', 'ground_pick']),
            ('鸭 鸭 走 一小步 鸭 鸭 停 下', 1.00, ['stop']),
            ('鸭 叫 一 声 鸭 鸭 点头 鸭 鸭', 0.80, ['chirp', 'nod']),
            ('鸭 歪头 鸭 鸭 前 滚 翻', 0.73, ['tilt', 'roulade']),
            ('鸭 鸭 叫 一 声', 0.95, ['chirp']),
            ('站住 鸭 鸭 点头', 0.90, ['stop']),
        ]
        for text, score, want in cases:
            self.assertEqual(voice.parse_commands(text, score), want, text)

    def test_unsafe_or_incomplete_clauses_do_not_dispatch_other_commands(self):
        for text in ('别让鸭鸭站起来', '他说鸭鸭坐下', '不要鸭鸭前滚翻', '“鸭鸭前滚翻”',
                     '[unk]前滚翻', '起立 站起来 招 [unk]', '前 捡 前 滚 翻', '翻',
                     '翻 鸭 鸭 摇头', '翻 要 歪头', '鸭鸭点头鸭鸭什么鸭鸭摇头',
                     '鸭鸭点头鸭鸭别摇头', '点头鸭鸭鸭鸭歪头', '他会不会做前滚翻',
                     '它在做前滚翻', '鸭鸭请'):
            with self.subTest(text=text):
                self.assertIsNone(voice.parse_command(text, 0.95))
                self.assertEqual(voice.parse_commands(text, 0.95), [])

    def test_utterance_without_command_is_empty(self):
        for text, score in [('鸭 鸭', 0.96), ('', 1.0), ('你好', 1.0),
                            ('不要点头', 1.0), ('鸭 鸭 鸭 鸭', 1.0)]:
            self.assertEqual(voice.parse_commands(text, score), [], text)

    def test_at_most_three_commands_per_utterance(self):
        text = '鸭 鸭 点头 鸭 鸭 摇头 鸭 鸭 歪头 鸭 鸭 叫 一 声 鸭 鸭 打招呼'
        self.assertEqual(voice.parse_commands(text, 1.0), [])
        self.assertEqual(voice.parse_commands(text + ' 鸭 鸭 停下', 1.0), ['stop'])

    def test_wake_word_duckduck_prefix_is_the_same_as_bare_command(self):
        # 手机中控约定的唤醒词：说「鸭鸭站起来」跟说「站起来」是一个意思
        self.assertEqual(voice.parse_command('鸭鸭站起来', 0.95), 'stand')
        self.assertEqual(voice.parse_command('鸭鸭，坐下。', 0.95), 'sit')
        self.assertEqual(voice.parse_command('鸭鸭走一小步', 0.95), 'walk')
        self.assertEqual(voice.parse_command('鸭鸭停下', 0.95), 'stop')
        # 原来的两个称呼还在
        self.assertEqual(voice.parse_command('小鸭子站起来', 0.95), 'stand')
        self.assertEqual(voice.parse_command('鸭子停下', 0.95), 'stop')

    def test_walk_and_stop_are_exact_and_negation_still_wins(self):
        self.assertEqual(voice.parse_command('走一小步', 0.95), 'walk')
        self.assertEqual(voice.parse_command('往前走', 0.95), 'walk')
        self.assertEqual(voice.parse_command('停下来', 0.95), 'stop')
        for text in ['不要走', '别走', '走不走', '走过去再说']:
            self.assertIsNone(voice.parse_command(text, 1.0), text)

    def test_every_mobile_action_is_recognised(self):
        # 手机中控 web/m/actions.js 里的动作，板载这条路要认同一套说法
        cases = {
            '走一小步': 'walk', '停下': 'stop',
            '前滚翻': 'roulade', '翻跟头': 'roulade',
            '低头捡东西': 'ground_pick', '捡东西': 'ground_pick', '低头': 'ground_pick',
            '叫一声': 'chirp', '叫一下': 'chirp',
            '打招呼': 'greet', '问好': 'greet',
            '点头': 'nod', '点点头': 'nod',
            '摇头': 'shake', '摇摇头': 'shake',
            '歪头': 'tilt', '歪歪头': 'tilt',
        }
        for text, want in cases.items():
            self.assertEqual(voice.parse_command(text, 0.95), want, text)
            self.assertEqual(voice.parse_command('鸭鸭' + text, 0.95), want, '鸭鸭' + text)
            self.assertEqual(voice.parse_command('鸭鸭，' + text + '。', 0.95), want, text)
            self.assertEqual(voice.parse_command('请' + text, 0.95), want, '请' + text)

    def test_new_actions_still_lose_to_negation(self):
        for text in ['不要点头', '别摇头', '不要前滚翻', '别叫一声', '不要捡东西',
                     '别打招呼', '不要歪头', '别低头']:
            self.assertIsNone(voice.parse_command(text, 1.0), text)

    def test_unrelated_speech_about_the_new_actions_is_not_a_command(self):
        for text in ['点头再摇头', '叫一声然后坐下', '前滚翻好不好', '它的头歪了']:
            self.assertIsNone(voice.parse_command(text, 1.0), text)


class FakeRobot:
    def __init__(self, sitting=False, enabled=True, torque=True, homed=True):
        self.sitting, self.enabled, self.torque, self.homed = sitting, enabled, torque, homed
        self.calls = []
        self.notified = []
        self.refuse = None

    def notify(self, method, params=None):
        # robot.head 是通知：不回话（lib.rs:522-529），只记下来
        self.calls.append((method, params or {}))
        self.notified.append(method)

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == 'robot.policies':
            return {'sitting': self.sitting, 'homed': self.homed,
                    'slots': [{'slot': 'sitstand', 'path': '/models/sit-network.onnx'}]}
        if method == 'robot.busStatus':
            return {'phase': 'control', 'mode': 'motion', 'homed': self.homed,
                    'policy_enabled': self.enabled, 'native': {'torque_state_confirmed': self.torque}}
        if method == self.refuse:
            return {'accepted': False, 'reason': 'test refusal'}
        if method == 'robot.do':
            self.sitting = not self.sitting
        elif method == 'robot.enable':
            self.enabled = True
        elif method == 'robot.init':
            self.torque, self.homed = True, True
        return {'accepted': True}

    def mutations(self):
        return [(method, params) for method, params in self.calls
                if method not in ('robot.policies', 'robot.busStatus')]


class MotionTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Linux updater motion lease')
    def test_motion_lease_refuses_root_exclusive_even_before_marker_observed(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            lock=Path(tmp)/'motion.lock';lock.touch();marker=Path(tmp)/'installing'
            with lock.open('rb') as root:
                fcntl.flock(root,fcntl.LOCK_EX)
                with self.assertRaises(voice.UpdateLocked):
                    with voice.motion_lease(marker):self.fail('exclusive lease was bypassed')
            with voice.motion_lease(marker):pass

    def test_update_appearing_between_state_and_dispatch_prevents_enable_and_toggle(self):
        blocked=[False]
        class Late(FakeRobot):
            def call(self,method,params=None):
                result=super().call(method,params)
                if method=='robot.stop':blocked[0]=True
                return result
        robot=Late(enabled=False)
        result=voice.PostureCommands(robot,guard=lambda:not blocked[0]).execute('sit')
        self.assertFalse(result['accepted'])
        self.assertNotIn('robot.enable',[method for method,_ in robot.calls])
        self.assertNotIn('robot.do',[method for method,_ in robot.calls])

    def test_sit_from_standing_and_repeated_sit_is_idempotent(self):
        robot = FakeRobot()
        motion = voice.PostureCommands(robot)
        self.assertTrue(motion.execute('sit')['accepted'])
        self.assertTrue(motion.execute('sit')['accepted'])
        self.assertEqual(robot.mutations(), [('robot.do', {'skill': 'sit_toggle'})])

    def test_stand_from_sitting_uses_sit_model_once(self):
        robot = FakeRobot(sitting=True)
        motion = voice.PostureCommands(robot)
        self.assertTrue(motion.execute('stand')['accepted'])
        self.assertTrue(motion.execute('stand')['accepted'])
        self.assertEqual(robot.mutations(), [('robot.do', {'skill': 'sit_toggle'})])

    def test_limp_standing_request_uses_init_and_starts_policy(self):
        robot = FakeRobot(torque=False, homed=False, enabled=False)
        self.assertTrue(voice.PostureCommands(robot).execute('stand')['accepted'])
        # 站起来之后顺手把走策略开起来，下一条「点头 / 走一小步」不用再点按钮
        self.assertEqual(robot.mutations(), [
            ('robot.init', {}), ('robot.stop', {}),
            ('robot.enable', {'on': True, 'toggle': False}),
        ])

    def test_stand_starts_walking_policy_for_the_next_command(self):
        robot = FakeRobot(sitting=False, enabled=False)
        self.assertTrue(voice.PostureCommands(robot).execute('stand')['accepted'])
        self.assertEqual(robot.mutations(), [
            ('robot.stop', {}), ('robot.enable', {'on': True, 'toggle': False}),
        ])

    def test_sit_from_homed_hold_enables_policy_before_sit(self):
        robot = FakeRobot(enabled=False)
        self.assertTrue(voice.PostureCommands(robot).execute('sit')['accepted'])
        self.assertEqual(robot.mutations(), [
            ('robot.stop', {}), ('robot.enable', {'on': True, 'toggle': False}),
            ('robot.do', {'skill': 'sit_toggle'}),
        ])

    def test_unknown_sitting_and_limp_sitting_do_not_move(self):
        for robot in [FakeRobot(sitting=None), FakeRobot(sitting=True, torque=False, enabled=False)]:
            self.assertFalse(voice.PostureCommands(robot).execute('stand')['accepted'])
            self.assertEqual(robot.mutations(), [])

    def test_enable_refusal_prevents_toggle(self):
        robot = FakeRobot(enabled=False)
        robot.refuse = 'robot.enable'
        self.assertFalse(voice.PostureCommands(robot).execute('sit')['accepted'])
        self.assertNotIn(('robot.do', {'skill': 'sit_toggle'}), robot.mutations())

    def test_cancelled_command_does_not_issue_motion(self):
        robot = FakeRobot()
        self.assertFalse(voice.PostureCommands(robot).execute('sit', allowed=lambda: False)['accepted'])
        self.assertEqual(robot.mutations(), [])


def fake_clock():
    """假时钟：sleep 把时间往前推，头 / 嘴那类按时序发的通知不用真的等。"""
    now = [0.0]
    return now, lambda seconds: now.__setitem__(0, now[0] + seconds), lambda: now[0]


class ShortStepTests(unittest.TestCase):
    def test_board_walk_reports_unsupported_without_touching_robotd(self):
        for enabled in (True, False):
            robot = FakeRobot(enabled=enabled)
            _, sleep, clock = fake_clock()
            result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute('walk')
            self.assertFalse(result['accepted'], result)
            self.assertIn('手机', result['reason'])
            self.assertEqual(robot.calls, [])

    def test_board_stop_still_clears_motion(self):
        robot = FakeRobot()
        self.assertTrue(voice.PostureCommands(robot).execute('stop')['accepted'])
        self.assertEqual(robot.mutations(), [('robot.stop', {})])


class SoundSkillTests(unittest.TestCase):
    def test_sound_commands_only_play_a_sound(self):
        for command, tag in (('chirp', 'chirp'), ('greet', 'greet')):
            robot = FakeRobot()
            result = voice.PostureCommands(robot).execute(command)
            self.assertTrue(result['accepted'], command)
            self.assertEqual(robot.mutations(), [('robot.sound', {'tag': tag})], command)
            # 叫一声本身就是声音，别再补一声「收到了」
            self.assertTrue(result['quiet'], command)

    def test_sound_works_without_policy_running(self):
        robot = FakeRobot(enabled=False, torque=False, homed=False)
        self.assertTrue(voice.PostureCommands(robot).execute('chirp')['accepted'])
        self.assertEqual(robot.mutations(), [('robot.sound', {'tag': 'chirp'})])

    def test_skill_commands_go_through_robot_do(self):
        for command, skill in (('roulade', 'roulade'), ('ground_pick', 'ground_pick')):
            robot = FakeRobot()
            self.assertTrue(voice.PostureCommands(robot).execute(command)['accepted'], command)
            self.assertEqual(robot.mutations(), [('robot.do', {'skill': skill})], command)

    def test_skill_and_head_refuse_when_sitting_or_limp(self):
        for command in ('roulade', 'ground_pick', 'nod', 'shake', 'tilt'):
            for robot in (FakeRobot(sitting=True), FakeRobot(torque=False)):
                result = voice.PostureCommands(robot, sleep=lambda _: None).execute(command)
                self.assertFalse(result['accepted'], (command, robot.sitting, robot.enabled, robot.torque))
                self.assertEqual(robot.mutations(), [], command)

    def test_skill_and_head_start_policy_when_upright_without_manual_step(self):
        # 站起来之后策略往往还停着。技能 / 头部命令应像手机页「开始走策略」那样自己开起来。
        _, sleep, clock = fake_clock()
        for command in ('roulade', 'ground_pick'):
            robot = FakeRobot(enabled=False)
            result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute(command)
            self.assertTrue(result['accepted'], command)
            self.assertEqual(robot.mutations(), [
                ('robot.stop', {}), ('robot.enable', {'on': True, 'toggle': False}),
                ('robot.do', {'skill': command}),
            ], command)
        for command, axis in (('nod', 'head_pitch'), ('shake', 'head_yaw'), ('tilt', 'head_roll')):
            robot = FakeRobot(enabled=False)
            result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute(command)
            self.assertTrue(result['accepted'], command)
            mutations = robot.mutations()
            self.assertEqual(mutations[:2], [
                ('robot.stop', {}), ('robot.enable', {'on': True, 'toggle': False})], command)
            heads = [params for method, params in mutations if method == 'robot.head']
            self.assertTrue(any(abs(params[axis]) > 0.01 for params in heads), command)
            self.assertEqual(heads[-1], voice.HEAD_ZERO, command)

    def test_skill_refusal_is_reported(self):
        robot = FakeRobot()
        robot.refuse = 'robot.do'
        result = voice.PostureCommands(robot).execute('roulade')
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'test refusal')


class PolicyStartTests(unittest.TestCase):
    """robot.enable 的回执不等于策略真的启动：home_start_guard 会先拒几次，必须回读 policy_enabled。"""

    def test_transient_home_guard_refusal_is_retried_until_policy_starts(self):
        class Guarded(FakeRobot):
            def __init__(self):
                super().__init__(enabled=False)
                self.refusals = 2

            def call(self, method, params=None):
                if method == 'robot.enable' and self.refusals > 0:
                    self.refusals -= 1
                    self.calls.append((method, params or {}))
                    return {'accepted': False,
                            'reason': '不能进入保持：正在确认实际 HOME 和直立姿态（连续 3 帧）'}
                return super().call(method, params)

        robot = Guarded()
        _, sleep, clock = fake_clock()
        result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute('nod')
        self.assertTrue(result['accepted'], result)
        self.assertEqual(robot.refusals, 0)                 # 拒了两次之后真的开起来了
        heads = [params for method, params in robot.mutations() if method == 'robot.head']
        self.assertTrue(heads and heads[-1] == voice.HEAD_ZERO)

    def test_persistent_home_guard_refusal_is_reported_verbatim(self):
        class StuckGuard(FakeRobot):
            def call(self, method, params=None):
                if method == 'robot.enable':
                    self.calls.append((method, params or {}))
                    return {'accepted': False,
                            'reason': '不能进入保持：正在确认实际 HOME 和直立姿态（连续 3 帧）；请回 HOME、扶正后重新按键'}
                return super().call(method, params)

        robot = StuckGuard(enabled=False)
        _, sleep, clock = fake_clock()
        result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute('nod')
        self.assertFalse(result['accepted'])
        self.assertIn('不能进入保持', result['reason'])
        # 策略没跑起来就绝不能往策略里喂头部观测
        self.assertEqual([m for m, _ in robot.calls if m == 'robot.head'], [])

    def test_accepted_ack_without_actual_start_reports_bus_refusal(self):
        class AckOnly(FakeRobot):
            def call(self, method, params=None):
                if method == 'robot.enable':
                    self.calls.append((method, params or {}))
                    return {'accepted': True}               # 只是收下请求，策略没开
                if method == 'robot.busStatus':
                    self.calls.append((method, params or {}))
                    return {'phase': 'control', 'mode': 'motion',
                            'policy_enabled': False,
                            'native': {'torque_state_confirmed': True},
                            'last_enable_refusal': '不能进入保持：舵机反馈不完整或不新鲜'}
                return super().call(method, params)

        robot = AckOnly(enabled=False)
        _, sleep, clock = fake_clock()
        result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute('nod')
        self.assertFalse(result['accepted'])
        self.assertIn('舵机反馈不完整', result['reason'])
        self.assertEqual([m for m, _ in robot.calls if m == 'robot.head'], [])


class _FakeSock:
    """假的 unix socket：只记下写了什么，fail=True 时连不上 / 写不进。"""

    def __init__(self, fail=False):
        self.fail, self.written, self.closed = fail, b'', False

    def settimeout(self, _):
        pass

    def connect(self, path):
        if self.fail:
            raise OSError('boom')

    def sendall(self, data):
        if self.fail:
            raise OSError('boom')
        self.written += data

    def close(self):
        self.closed = True


class NotifyTests(unittest.TestCase):
    """robot.head 是通知：不带 id、不回话（lib.rs:522-529）。拿 call 去发会等到超时。"""

    def test_notify_writes_idless_lines_over_one_kept_connection(self):
        made = []
        client = voice.RobotClient('/run/robotd.sock',
                                   opener=lambda: made.append(_FakeSock()) or made[-1])
        client.notify('robot.head', {'head_pitch': 0.25})
        client.notify('robot.head', voice.HEAD_ZERO)
        self.assertEqual(len(made), 1)                       # 常驻一条，复用
        sent = [json.loads(line) for line in made[0].written.decode().splitlines()]
        self.assertEqual(len(sent), 2)
        self.assertNotIn('id', sent[0])                      # 通知不带 id：robotd 不回话
        self.assertEqual(sent[0]['method'], 'robot.head')
        self.assertEqual(sent[0]['params'], {'head_pitch': 0.25})

    def test_notify_reconnects_once_when_the_socket_is_dead(self):
        made = [_FakeSock(True), _FakeSock(False)]
        pending = iter(made)
        client = voice.RobotClient('/run/robotd.sock', opener=lambda: next(pending))
        client.notify('robot.head', voice.HEAD_ZERO)
        self.assertTrue(made[0].closed)                      # 断掉的那条丢掉
        self.assertTrue(made[1].written.endswith(b'\n'))

    def test_head_command_reports_failure_instead_of_crashing_when_robotd_is_gone(self):
        class Dead(FakeRobot):
            def notify(self, method, params=None):
                raise OSError('no such socket')

        now = [0.0]
        result = voice.PostureCommands(Dead(), sleep=lambda s: now.__setitem__(0, now[0] + s),
                                       clock=lambda: now[0]).execute('nod')
        self.assertFalse(result['accepted'])
        self.assertIn('no such socket', result['reason'])


class HeadTests(unittest.TestCase):
    def test_head_command_plays_frames_and_ends_at_zero(self):
        for command, axis in (('nod', 'head_pitch'), ('shake', 'head_yaw'), ('tilt', 'head_roll')):
            robot = FakeRobot()
            _, sleep, clock = fake_clock()
            result = voice.PostureCommands(robot, sleep=sleep, clock=clock).execute(command)
            self.assertTrue(result['accepted'], command)
            # 走 notify（通知），不是 call（等回话的请求）
            self.assertEqual(set(robot.notified), {'robot.head'}, command)
            heads = [params for method, params in robot.mutations() if method == 'robot.head']
            self.assertTrue(len(heads) > 4, command)
            self.assertTrue(any(abs(params[axis]) > 0.01 for params in heads), command)
            # robot.head 是锁存的：最后一定回到全 0，不然头就一直歪着
            self.assertEqual(heads[-1], voice.HEAD_ZERO, command)

    def test_head_command_recentres_when_cancelled_midway(self):
        robot = FakeRobot()
        _, sleep, clock = fake_clock()
        left = [8]          # allowed() 每问一次就少一次：发几帧之后「人松手了」
        motion = voice.PostureCommands(robot, sleep=sleep, clock=clock)
        motion.execute('nod', allowed=lambda: left.__setitem__(0, left[0] - 1) or left[0] > 0)
        heads = [params for method, params in robot.mutations() if method == 'robot.head']
        self.assertTrue(len(heads) >= 2, heads)
        # 头已经歪出去了，就算中途「松手」也必须收回来
        self.assertEqual(heads[-1], voice.HEAD_ZERO)

    def test_head_command_keeps_the_other_axes_at_zero(self):
        robot = FakeRobot()
        _, sleep, clock = fake_clock()
        voice.PostureCommands(robot, sleep=sleep, clock=clock).execute('nod')
        for _, params in robot.mutations():
            self.assertEqual(params['neck_pitch'], 0.0)
            self.assertEqual(params['head_yaw'], 0.0)
            self.assertEqual(params['head_roll'], 0.0)


if __name__ == '__main__':
    unittest.main()
