import array
import math
from pathlib import Path
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

import duck_echo as echo


def pcm(values):
    return array.array('h', values).tobytes()


class AudioTests(unittest.TestCase):
    def test_selects_onboard_right_mic_and_duplicates_to_speakers(self):
        data, level = echo.prepare_audio(pcm([100, 2000, -100, -2000]), 1)
        self.assertEqual(array.array('h', data).tolist(), [2000, 2000, -2000, -2000])
        self.assertAlmostEqual(level['rms'], 2000)

    def test_silence_has_finite_level(self):
        data, level = echo.prepare_audio(b'\x00' * 40, 1)
        self.assertEqual(len(data), 40)
        self.assertEqual(level['peak'], 0)
        self.assertTrue(math.isfinite(level['dbfs']))

    def test_partial_pcm_frame_is_discarded(self):
        data, level = echo.prepare_audio(pcm([1, 200]) + b'\x00', 1)
        self.assertEqual(data, pcm([200, 200]))
        self.assertEqual(level['frames'], 1)

    def test_rejects_invalid_channel(self):
        with self.assertRaises(ValueError):
            echo.prepare_audio(b'', 2)

    def test_selected_channel_can_remove_dc_without_changing_default_api(self):
        import inspect
        self.assertIn('center', inspect.signature(echo.prepare_audio).parameters,
                      'selected microphone DC centering is missing')
        raw = pcm([0, 1500, 0, 500])
        centered, level = echo.prepare_audio(raw, 1, center=True)
        self.assertEqual(array.array('h', centered).tolist(), [500, 500, -500, -500])
        self.assertEqual(level['rms'], 500)
        self.assertEqual(array.array('h', echo.prepare_audio(raw, 1)[0]).tolist(),
                         [1500, 1500, 500, 500])


class FakeAudio:
    def __init__(self):
        self.calls = []
        self.finished = False

    def start(self):
        self.calls.append('start')
        self.finished = False

    def stop(self, play):
        self.calls.append(('stop', play))

    def expired(self):
        return self.finished


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.audio = FakeAudio()
        self.button = echo.HoldButton(self.audio)

    def test_holds_record_and_release_repeats(self):
        self.button.event(1, now=1)
        self.button.event(2, now=1.2)
        self.button.event(0, now=2)
        self.assertEqual(self.audio.calls, ['start', ('stop', True)])

    def test_short_accidental_click_is_discarded(self):
        self.button.event(1, now=1)
        self.button.event(0, now=1.1)
        self.assertEqual(self.audio.calls, ['start', ('stop', False)])

    def test_disconnect_cancels_without_playback(self):
        self.button.event(1, now=1)
        self.button.cancel()
        self.assertEqual(self.audio.calls, ['start', ('stop', False)])

    def test_started_held_requires_release_before_recording(self):
        self.button = echo.HoldButton(self.audio, initially_down=True)
        self.button.event(1, now=1)
        self.button.event(0, now=2)
        self.assertEqual(self.audio.calls, [])
        self.button.event(1, now=3)
        self.assertEqual(self.audio.calls, ['start'])

    def test_time_limit_plays_only_once_and_requires_new_press(self):
        self.button.event(1, now=1)
        self.audio.finished = True
        self.button.tick(now=11)
        self.button.event(1, now=11.1)
        self.button.event(0, now=12)
        self.assertEqual(self.audio.calls, ['start', ('stop', True)])
        self.button.event(1, now=13)
        self.assertEqual(self.audio.calls[-1], 'start')

    def test_repeated_press_does_not_restart_capture(self):
        self.button.event(1, now=1)
        self.button.event(1, now=2)
        self.assertEqual(self.audio.calls, ['start'])

    def test_voice_time_limit_cancels_instead_of_executing_while_held(self):
        self.button = echo.HoldButton(self.audio, repeat_on_limit=False)
        self.button.event(1, now=1)
        self.audio.finished = True
        self.button.tick(now=11)
        self.button.event(0, now=12)
        self.assertEqual(self.audio.calls, ['start', ('stop', False)])

    def test_cancel_after_release_invalidates_pending_recognition_for_input_overflow(self):
        self.audio.cancel_processing = lambda: self.audio.calls.append('cancel_pending')
        self.assertFalse(self.button.recording)
        self.button.cancel()  # SYN_DROPPED also takes this path after release.
        self.assertEqual(self.audio.calls, ['cancel_pending'])


class InputTests(unittest.TestCase):
    def test_event_layout_matches_64_bit_linux(self):
        self.assertEqual(echo.INPUT_EVENT.size, 24)
        raw = echo.INPUT_EVENT.pack(10, 500, 1, 318, 1)
        self.assertEqual(echo.decode_events(raw), [(1, 318, 1)])

    def test_incomplete_event_is_rejected(self):
        with self.assertRaises(ValueError):
            echo.decode_events(b'123')


class NativeCueDurationTests(unittest.TestCase):
    def test_fixed_native_tags_use_maximum_wav_header_duration_and_safe_fallback(self):
        self.assertTrue(hasattr(echo, 'native_sound_durations'), 'native cue duration lookup missing')
        with tempfile.TemporaryDirectory() as folder:
            bank = Path(folder)
            for tag, durations in [('chirp', [.1, .28]), ('coo', [1.11])]:
                (bank / tag).mkdir()
                for index, duration in enumerate(durations):
                    with wave.open(str(bank / tag / f'{index}.wav'), 'wb') as sound:
                        sound.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                        sound.writeframes(b'\x00\x00' * round(duration * 1000))
            (bank / 'chirp' / 'bad.wav').write_bytes(b'not a wave')
            durations = echo.native_sound_durations(bank)
            self.assertAlmostEqual(durations['chirp'], .28)
            self.assertAlmostEqual(durations['coo'], 1.11)
            self.assertGreaterEqual(durations['greet'], 1.4)
            self.assertEqual(set(durations), {'chirp', 'coo', 'greet', 'inquire'})


class PlaybackTests(unittest.TestCase):
    def player(self, code):
        return SimpleNamespace(
            returncode=code, communicate=lambda **kw: None,
            poll=lambda: code,
        )

    def test_device_busy_is_reported_as_failed_playback(self):
        audio = echo.AlsaAudio('capture', 'playback')
        with patch.object(echo.subprocess, 'Popen', return_value=self.player(1)):
            with self.assertLogs('duck-echo', level='ERROR'):
                audio.play(pcm([1000, 1000]))
                self.assertFalse(audio.finish_playback())

    def test_success_requires_player_exit_zero(self):
        audio = echo.AlsaAudio('capture', 'playback')
        with patch.object(echo.subprocess, 'Popen', return_value=self.player(0)):
            audio.play(pcm([1000, 1000]))
            self.assertTrue(audio.finish_playback())

    def test_active_playback_includes_writer_and_player_lifetime(self):
        audio = echo.AlsaAudio('capture', 'playback')
        self.assertTrue(hasattr(audio, 'playback_active'), 'playback state is missing')
        self.assertFalse(audio.playback_active())
        audio.player = SimpleNamespace(poll=lambda: None)
        self.assertTrue(audio.playback_active())
        audio.player = None
        audio.player_thread = SimpleNamespace(is_alive=lambda: True)
        self.assertTrue(audio.playback_active())

    def test_cancelled_playback_guard_is_rechecked_after_old_player_teardown(self):
        import inspect
        audio = echo.AlsaAudio('capture', 'playback')
        self.assertIn('allowed', inspect.signature(audio.play).parameters,
                      'cancellable playback launch guard is missing')
        valid = [True]
        with patch.object(audio, 'stop_playback', side_effect=lambda: valid.__setitem__(0, False)), \
                patch.object(echo.subprocess, 'Popen') as spawn:
            audio.play(pcm([1000, 1000]), allowed=lambda: valid[0])
        spawn.assert_not_called()

    def test_cancellation_after_spawn_closes_player_pipes_without_sending_pcm(self):
        audio = echo.AlsaAudio('capture', 'playback')
        valid = [True]
        calls = []
        player = self.player(0)
        player.communicate = lambda **kwargs: calls.append(kwargs)
        def spawn(*args, **kwargs):
            valid[0] = False
            return player
        with patch.object(echo.subprocess, 'Popen', side_effect=spawn):
            audio.play(pcm([1000, 1000]), allowed=lambda: valid[0])
            audio.finish_playback()
        self.assertEqual(calls, [{'timeout': 1}])


class TriggerModeTests(unittest.TestCase):
    def setUp(self):
        # CLI wiring tests simulate Linux on the host and never access its mixer.
        restore = patch('install.MicrophoneRoutes.restore')
        restore.start()
        self.addCleanup(restore.stop)

    def test_daemon_restores_preference_on_configured_alsa_card_before_serving(self):
        import install
        restored = []
        def restore(routes):
            restored.append(routes.card)
            routes.source = 'both'
        def serve(server, *args):
            self.assertEqual(restored, ['TestCard'])
            self.assertEqual(server.control.status()['microphone_source'], 'both')
            self.assertFalse(server.control.status()['listening'])
        with patch.object(install.MicrophoneRoutes, 'restore', restore), \
                patch.object(echo.sys, 'platform', 'linux'), patch.object(echo, 'daemon'), \
                patch.object(echo.signal, 'signal'), patch.object(echo.logging, 'basicConfig'), \
                patch('audio_control.AudioControlServer.start', serve), \
                patch('speech_control.MandarinRecognizer'):
            self.assertEqual(echo.main(['daemon', '--capture-device', 'plughw:CARD=TestCard,DEV=0']), 0)

    def test_daemon_default_discards_unknown_pcm_and_explicit_echo_restores_replay(self):
        import continuous_audio
        for options, should_play in (([], False), (['--reply-mode', 'echo'], True)):
            def inspect_audio(audio, *args, **kwargs):
                self.assertTrue(audio.voice.ready.wait(1))
                audio.voice.fallback(b'unknown words pcm')
            with patch.object(echo.sys, 'platform', 'linux'), \
                    patch.object(echo, 'daemon', side_effect=inspect_audio), \
                    patch.object(echo.signal, 'signal'), patch.object(echo.logging, 'basicConfig'), \
                    patch('audio_control.AudioControlServer'), patch('speech_control.MandarinRecognizer'), \
                    patch.object(continuous_audio.ContinuousAudio, 'play') as play:
                self.assertEqual(echo.main(['daemon', *options]), 0)
                self.assertEqual(play.called, should_play)

    def test_toggle_daemon_has_private_control_and_shared_music_model_even_in_voice_off(self):
        import audio_control
        import speech_control
        recognizer = SimpleNamespace(model=object())
        def inspect_audio(audio, stopped, selected, trigger_mode):
            self.assertIsNotNone(getattr(audio, 'music', None), 'music streaming is not integrated')
            self.assertTrue(audio.voice.ready.wait(1))
            self.assertIs(audio.voice.recognizer, recognizer)
            self.assertFalse(audio.voice.recognize_speech)
            self.assertIsNotNone(audio.observer)
            self.assertIsNotNone(audio.activation_cue, 'native listening chirp is not wired')
            self.assertIsNotNone(audio.deactivation_cue, 'native listening coo is not wired')
            audio.activation_cue(lambda: False)
            robot.return_value.call.assert_not_called()
            audio.activation_cue(lambda: True)
            robot.return_value.call.assert_called_once_with('robot.sound', {'tag': 'chirp'})
            audio.deactivation_cue(lambda: True)
            self.assertEqual(robot.return_value.call.call_args.args, ('robot.sound', {'tag': 'coo'}))
        with patch.object(echo.sys, 'platform', 'linux'), patch.object(echo, 'daemon', side_effect=inspect_audio), \
                patch.object(echo.signal, 'signal'), patch.object(echo.logging, 'basicConfig'), \
                patch.object(audio_control, 'AudioControlServer') as server, \
                patch.object(speech_control, 'MandarinRecognizer', return_value=recognizer), \
                patch('voice_commands.RobotClient') as robot:
            self.assertEqual(echo.main(['daemon']), 0)
            self.assertEqual(server.call_args.args[1], '/run/duck-echo/control.sock')
            server.return_value.start.assert_called_once()
            server.return_value.close.assert_called_once()

    def test_no_pad_wait_keeps_web_started_capture_running(self):
        import threading
        stopped = threading.Event()
        audio = FakeAudio()
        audio.active = True
        audio.voice = None
        audio.stop_playback = lambda: self.fail('waiting for absent pad stopped web audio')
        def wait(delay):
            self.assertEqual(audio.calls, [])
            stopped.set()
        with patch.object(echo, 'devices', return_value=[]), patch.object(stopped, 'wait', side_effect=wait):
            echo.daemon(audio, stopped)
        self.assertEqual(audio.calls, [])

    def test_factory_defaults_to_toggle_and_retains_explicit_hold(self):
        import continuous_audio
        self.assertTrue(hasattr(echo, 'make_button'), 'trigger-mode factory is missing')
        audio = FakeAudio()
        audio.voice = None
        self.assertIsInstance(echo.make_button(audio, initially_down=True), continuous_audio.ToggleButton)
        self.assertIsInstance(echo.make_button(audio, trigger_mode='hold'), echo.HoldButton)

    def test_cli_defaults_daemon_to_continuous_and_hold_uses_original_capture(self):
        import continuous_audio
        for options, expected in (([], continuous_audio.ContinuousAudio),
                                  (['--trigger-mode', 'hold'], echo.AlsaAudio)):
            with patch.object(echo.sys, 'platform', 'linux'), patch.object(echo, 'daemon') as daemon, \
                    patch.object(echo.signal, 'signal'), patch.object(echo.logging, 'basicConfig'), \
                    patch('audio_control.AudioControlServer'), patch('speech_control.MandarinRecognizer'):
                echo.main(['daemon', *options])
                self.assertIsInstance(daemon.call_args.args[0], expected)
                self.assertEqual(daemon.call_args.kwargs['trigger_mode'], 'hold' if options else 'toggle')

    def test_probe_remains_bounded_capture_with_toggle_default(self):
        with patch.object(echo.sys, 'platform', 'linux'), patch.object(echo, 'AlsaAudio') as audio, \
                patch.object(echo.signal, 'signal'), patch.object(echo.logging, 'basicConfig'), \
                patch('builtins.print'):
            audio.return_value.expired.return_value = True
            audio.return_value.stop.return_value = {'ok': True}
            audio.return_value.voice = None
            self.assertEqual(echo.main(['probe']), 0)
            audio.return_value.start.assert_called_once()

    def test_daemon_uses_factory_for_initial_state_and_syn_dropped_resync(self):
        stopped = SimpleNamespace(is_set=lambda: False, wait=lambda delay: None)
        audio = FakeAudio()
        audio.voice = None
        audio.close = lambda: None
        audio.stop_playback = lambda: None
        audio.cancel_processing = lambda: None
        bits = iter((True, False))
        records = [echo.INPUT_EVENT.pack(1, 0, 0, 3, 0) + echo.INPUT_EVENT.pack(1, 0, 0, 0, 0)]
        records.append(b'')
        polls = iter((False, False, True, True))
        stopped.is_set = lambda: next(polls)
        self.assertTrue(hasattr(echo, 'make_button'), 'trigger-mode factory is missing')
        with patch.object(echo, 'devices', return_value=[{'name': 'pad', 'path': 'event0'}]), \
                patch.object(echo.os, 'O_NONBLOCK', 0, create=True), \
                patch.object(echo.os, 'open', return_value=4), patch.object(echo.os, 'close'), \
                patch.object(echo, 'binding_pressed', side_effect=lambda fd,binding: next(bits) if binding['code']==318 else False), \
                patch.object(echo.select, 'select', return_value=([4], [], [])), \
                patch.object(echo.os, 'read', side_effect=records), \
                patch.object(echo, 'make_button', wraps=echo.make_button) as factory:
            echo.daemon(audio, stopped)
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(factory.call_args_list[0].kwargs['initially_down'])
        self.assertFalse(factory.call_args_list[1].kwargs['initially_down'])
        self.assertTrue(all(call.kwargs['trigger_mode'] == 'toggle' for call in factory.call_args_list))


if __name__ == '__main__':
    unittest.main()
