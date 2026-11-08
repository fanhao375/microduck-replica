"""Music input never starts the motion core and expires with its evidence."""
import math
import unittest
from tests.test_pad import PadTestBase
from studio.pad_combo import Refused

from studio import audio_interaction as audio


class SnapshotTests(unittest.TestCase):
    def test_only_validated_small_music_status_is_accepted(self):
        state = audio.validate_status({'listening': True, 'music': True,
            'rhythm': {'bpm': 120, 'confidence': .9, 'feel': 'lively', 'last_beat': 10., 'age': .1}})
        self.assertTrue(state['listening'])
        for change in ({'listening': 'yes'}, {'rhythm': {'bpm': float('nan')}},
                       {'rhythm': {'bpm': 10000}}, {'transcript': 'private'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                audio.validate_status({'listening': True, 'music': True,
                    'rhythm': {'bpm': 120, 'confidence': .9, 'feel': 'lively', 'last_beat': 10., 'age': .1}, **change})

    def test_voice_last_reason_is_validated(self):
        base = {'listening': True, 'music': True,
                'rhythm': {'bpm': 120, 'confidence': .9, 'feel': 'lively', 'last_beat': 10., 'age': .1}}
        good = {**base, 'last': {'command': 'nod', 'accepted': False, 'age': 1.,
                                 'seq': 3, 'reason': 'robot is sitting; stand up first'}}
        self.assertEqual(audio.validate_status(good)['last']['reason'],
                         'robot is sitting; stand up first')
        for change in ({'reason': 5}, {'reason': 'x' * 201},
                       {'command': 7}, {'seq': -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                audio.validate_status({**base, 'last': {**good['last'], **change}})


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.requests = []
        self.state = {'listening': False, 'music': False,
            'rhythm': {'bpm': None, 'confidence': 0., 'feel': 'unknown', 'last_beat': None, 'age': None}}
        def request(message):
            self.requests.append(message)
            self.state.update({key:value for key,value in message.items() if key in ('listening','music')})
            return {'ok':True,'status':self.state}
        self.client = audio.AudioClient(request=request, clock=lambda:self.now, autostart=False)

    def test_set_and_snapshot_use_cache_and_expire_without_io(self):
        self.client.set(listening=True, music=True)
        self.assertEqual(self.requests,[{'method':'set','listening':True,'music':True}])
        self.assertTrue(self.client.snapshot()['available'])
        self.assertEqual(len(self.requests),1)
        self.now += .7
        self.assertFalse(self.client.snapshot()['available'])
        self.assertEqual(len(self.requests),1)

    def test_mutating_snapshot_cannot_modify_shared_cache(self):
        self.client.set(listening=True)
        state = self.client.snapshot()
        state['rhythm']['confidence'] = 1
        self.assertEqual(self.client.snapshot()['rhythm']['confidence'],0)

    def test_bad_switches_and_private_transcript_are_rejected(self):
        with self.assertRaises(ValueError):
            self.client.set(listening='true')
        self.client.request = lambda _: {'ok':True,'status':{**self.state,'transcript':'private'}}
        with self.assertRaises(ValueError):
            self.client.set(music=True)
        self.assertFalse(self.client.snapshot()['available'])


class RhythmTests(unittest.TestCase):
    def setUp(self):
        self.status = {'available': True, 'listening': True, 'music': True,
            'rhythm': {'bpm': 120., 'confidence': .9, 'feel': 'lively', 'last_beat': 10., 'age': .02}}
        self.rhythm = audio.MusicRhythm(lambda: self.status)

    def test_music_produces_small_head_and_body_intents_only(self):
        sends = []
        for n in range(30):
            now = 10+n*.04
            self.status['rhythm']['last_beat'] = 10 + math.floor((now-10)/.5)*.5
            sends += self.rhythm.sample(now, 2.5, 100)
        self.assertEqual({method for method, _ in sends}, {'robot.head', 'robot.pose'})
        self.assertTrue(all(abs(value) <= .2 for _, params in sends for key, value in params.items() if key != 'active'))
        self.assertTrue(any(abs(params.get('head_yaw',0)) > .01 for _, params in sends))

    def test_music_does_not_invent_a_beat_and_cleans_once_on_silence(self):
        self.status['rhythm']['confidence'] = 0
        self.assertEqual(self.rhythm.sample(10, 2.5, 100), [])
        self.status['rhythm']['confidence'] = .9
        self.rhythm.sample(10, 2.5, 100)
        self.status['rhythm']['age'] = .8
        stopped = self.rhythm.sample(10.8, 2.5, 100)
        self.assertIn(('robot.pose', {'active': False}), stopped)
        self.assertEqual(self.rhythm.sample(10.9, 2.5, 100), [])

    def test_user_head_limit_caps_every_head_axis(self):
        self.rhythm.sample(10, .01, 100)
        self.status['rhythm']['last_beat'] = 11.
        head = dict(self.rhythm.sample(11, .01, 100))['robot.head']
        self.assertTrue(all(abs(value)<=.01 for value in head.values()),head)

    def test_off_unavailable_and_expired_beat_never_move(self):
        for change in ({'listening': False}, {'music': False}, {'available': False},
                       {'rhythm': {**self.status['rhythm'], 'last_beat': 1.}}):
            with self.subTest(change=change):
                self.status.update(change)
                self.rhythm = audio.MusicRhythm(lambda: self.status)
                self.assertEqual(self.rhythm.sample(10, 2.5, 100), [])
                self.setUp()


class AudioApiTests(PadTestBase):
    def test_audio_switch_uses_component_only_and_publishes_status(self):
        state = {'available':True,'listening':False,'music':False,'rhythm':{}}
        class Audio:
            def snapshot(self):
                return dict(state)
            def set(self, **values):
                state.update(values)
                return dict(state)
            def close(self):
                pass
        self.ops.audio = Audio()
        conn = self.ops.new_conn()
        self.ops.handle({'op':'drive','open':True},conn=conn)
        self.ops.handle({'op':'audio','listening':True},conn=conn)
        self.assertTrue(self.ops.snapshot(conn=conn)['audio']['listening'])
        self.assertFalse(any(method in ('robot.enable','robot.init') for method,_ in self.robot.calls))
        for values in ({'listening':'yes'},{'path':'/tmp/arbitrary'},{'method':'robot.init'}):
            with self.subTest(values=values), self.assertRaises(Refused):
                self.ops.handle({'op':'audio',**values},conn=conn)

    def test_missing_audio_service_is_actionable(self):
        conn = self.ops.new_conn()
        self.ops.handle({'op':'drive','open':True},conn=conn)
        with self.assertRaisesRegex(Refused,'语音服务'):
            self.ops.handle({'op':'audio','listening':True},conn=conn)


if __name__ == '__main__':
    unittest.main()
