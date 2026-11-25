"""Continuous listening tests use PCM and fake processes, never robot hardware."""
import array
import importlib
import math
import signal
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import duck_echo as echo

try:
    continuous = importlib.import_module('continuous_audio')
except ModuleNotFoundError:
    continuous = SimpleNamespace()


def pcm(seconds, right=2000, left=0):
    # A constant sample is DC, not speech. Keep the old amplitude/channel API
    # while making voice fixtures oscillate around zero.
    return array.array('h', (value for i in range(round(48000 * seconds))
                            for value in (left, round(right * math.cos(2 * math.pi * i / 192))))).tobytes()


def measured_pcm(seconds, amplitude=500, dc=900):
    return array.array('h', (value for i in range(round(48000 * seconds))
                            for value in (0, round(dc + amplitude * math.sin(2 * math.pi * i / 192))))).tobytes()


def eventually(predicate, timeout=1):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


class FakeAudio:
    voice = None
    channel, maximum = 1, 1

    def __init__(self):
        self.calls = []
        self.finished = False
        self.playing = False
        self.played = threading.Event()

    def start(self):
        self.calls.append('start')

    def stop(self, play, announce=False):
        self.calls.append(('stop', play))

    def expired(self):
        return self.finished

    def cancel_processing(self):
        self.calls.append('cancel')

    def stop_playback(self):
        self.playing = False
        self.calls.append('stop_playback')

    def playback_active(self):
        return self.playing

    def close(self):
        self.stop_playback()

    def play(self, data, allowed=None):
        if allowed is not None and not allowed():
            return
        self.calls.append(('play', data))
        self.playing = True
        self.played.set()


class ToggleTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(continuous, 'ToggleButton'), 'ToggleButton is missing')
        self.audio = FakeAudio()
        self.button = continuous.ToggleButton(self.audio)

    def test_click_enables_and_next_press_disables(self):
        self.button.event(1)
        self.button.event(0)
        self.assertTrue(self.button.recording)
        self.assertEqual(self.audio.calls, ['start'])
        self.button.event(1)
        self.button.event(0)
        self.assertFalse(self.button.recording)
        self.assertIn(('stop', False), self.audio.calls)

    def test_repeat_and_duplicate_press_do_not_toggle(self):
        for value in (1, 1, 2, 2):
            self.button.event(value)
        self.assertEqual(self.audio.calls, ['start'])

    def test_initially_held_requires_release(self):
        self.button = continuous.ToggleButton(self.audio, initially_down=True)
        self.button.event(1)
        self.button.event(2)
        self.button.event(0)
        self.assertEqual(self.audio.calls, [])
        self.button.event(1)
        self.assertEqual(self.audio.calls, ['start'])

    def test_disconnect_overflow_or_select_cancels_pending_even_after_session_failed(self):
        self.button.cancel()
        self.assertIn(('stop', False), self.audio.calls)
        self.button.event(1)
        self.audio.finished = True
        self.button.tick()
        self.assertFalse(self.button.recording)
        self.assertEqual(self.audio.calls.count('start'), 1)
        self.button.tick()
        self.assertEqual(self.audio.calls.count('start'), 1)

    def test_external_listening_start_and_stop_use_same_actual_toggle_state(self):
        self.audio.active = True
        self.button.event(1)
        self.assertIn(('stop', False), self.audio.calls)
        self.assertNotIn('start', self.audio.calls)
        self.button.event(0)
        self.audio.active = False
        self.button.event(1)
        self.assertEqual(self.audio.calls.count('start'), 1)


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(continuous, 'PcmSegmenter'), 'PcmSegmenter is missing')
        self.segmenter = continuous.PcmSegmenter(maximum=1, silence=.1, minimum=.1)

    def test_silence_ends_phrase_and_selected_right_mic_is_stereo(self):
        self.assertEqual(self.segmenter.feed(pcm(.2, right=0, left=10000)), [])
        self.assertEqual(self.segmenter.feed(pcm(.2)), [])
        phrases = self.segmenter.feed(pcm(.1, right=0))
        self.assertEqual(len(phrases), 1)
        samples = array.array('h', phrases[0])
        self.assertEqual(max(samples), 2000)
        self.assertEqual(samples[::2].tolist(), samples[1::2].tolist())
        self.assertLessEqual(len(phrases[0]), 48000 * 4)

    def test_maximum_splits_phrase_and_next_utterance_still_works(self):
        phrases = self.segmenter.feed(pcm(2.2))
        self.assertEqual(len(phrases), 2)
        self.assertTrue(all(len(data) == 48000 * 4 for data in phrases))
        self.assertLessEqual(self.segmenter.buffered_bytes, 48000 * 4 + 3840)
        self.assertEqual(len(self.segmenter.feed(pcm(.1, right=0))), 1)

    def test_partial_frames_and_long_silence_keep_memory_bounded(self):
        raw = pcm(.2) + pcm(.1, right=0)
        phrases = []
        for offset in range(0, len(raw), 997):
            phrases.extend(self.segmenter.feed(raw[offset:offset + 997]))
        self.assertEqual(len(phrases), 1)
        self.segmenter.feed(pcm(20, right=0))
        self.assertLessEqual(self.segmenter.buffered_bytes, round(.14 * 48000) * 4)


class MeasuredEndpointTests(unittest.TestCase):
    def test_measured_dc_and_ambient_noise_never_generate_a_phrase(self):
        segmenter = continuous.PcmSegmenter()
        self.assertEqual(segmenter.feed(measured_pcm(3.2)), [],
                         'measured AC354/DC900 ambient was classified as speech')
        self.assertLessEqual(segmenter.buffered_bytes, round(.14 * 48000) * 4,
                             'ambient must remain pre-roll, not accumulate a ten-second phrase')

    def test_short_speech_ends_on_ambient_with_centered_bounded_preroll(self):
        segmenter = continuous.PcmSegmenter()
        self.assertEqual(segmenter.feed(measured_pcm(1)), [])
        self.assertEqual(segmenter.feed(measured_pcm(.6, amplitude=6000)), [])
        phrases = segmenter.feed(measured_pcm(.7))
        self.assertEqual(len(phrases), 1, 'ambient must end speech after roughly .6 seconds')
        samples = array.array('h', phrases[0])[::2]
        self.assertLessEqual(len(samples) / 48000, 2)
        self.assertGreaterEqual(len(samples) / 48000, 1.2)
        self.assertLess(abs(sum(samples) / len(samples)), 2)
        self.assertLessEqual(max(abs(value) for value in samples[:4800]), 501,
                             'bounded pre-roll should preserve ambient before speech onset')

    def test_dc_only_and_startup_analog_transient_never_echo(self):
        segmenter = continuous.PcmSegmenter()
        transient = array.array('h', [0, -18332] * round(48000 * .04)).tobytes()
        self.assertEqual(segmenter.feed(transient), [])
        self.assertEqual(segmenter.feed(measured_pcm(3.2)), [])
        constant_dc = array.array('h', [0, 900] * (48000 * 3)).tobytes()
        self.assertEqual(segmenter.feed(constant_dc), [])
        self.assertLessEqual(segmenter.buffered_bytes, round(.14 * 48000) * 4)

    def test_long_speech_cannot_train_itself_into_the_noise_floor(self):
        segmenter = continuous.PcmSegmenter(maximum=1, minimum=.1)
        self.assertEqual(segmenter.feed(measured_pcm(.4)), [])
        self.assertTrue(hasattr(segmenter, 'noise_floor'), 'adaptive ambient noise estimate is missing')
        floor = segmenter.noise_floor
        phrases = segmenter.feed(measured_pcm(2.2, amplitude=6000))
        self.assertEqual(len(phrases), 2)
        self.assertTrue(all(len(data) == 48000 * 4 for data in phrases))
        self.assertAlmostEqual(segmenter.noise_floor, floor)

    def test_environment_floor_adapts_to_non_speech_before_next_utterance(self):
        segmenter = continuous.PcmSegmenter()
        segmenter.feed(measured_pcm(.5))
        first_floor = segmenter.noise_floor
        self.assertEqual(segmenter.feed(measured_pcm(1, amplitude=750)), [])
        self.assertGreater(segmenter.noise_floor, first_floor)
        segmenter.feed(measured_pcm(.6, amplitude=6000))
        self.assertEqual(len(segmenter.feed(measured_pcm(.7, amplitude=750))), 1)


class BlockingStream:
    def __init__(self):
        self.condition = threading.Condition()
        self.blocks = []
        self.ended = False

    def feed(self, data):
        with self.condition:
            self.blocks.append(data)
            self.condition.notify_all()

    def read(self, size):
        with self.condition:
            while not self.blocks and not self.ended:
                self.condition.wait()
            return self.blocks.pop(0) if self.blocks else b''

    def close(self):
        self.end()

    def end(self):
        with self.condition:
            self.ended = True
            self.condition.notify_all()


class CaptureProcess:
    def __init__(self):
        self.stdout = BlockingStream()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -signal.SIGTERM
        self.stdout.end()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class ContinuousTests(unittest.TestCase):
    def test_switch_while_off_does_not_start_capture_or_play_a_cue(self):
        self.assertTrue(hasattr(self.audio, 'change_microphone'), 'source transition missing')
        applied = []
        self.audio.activation_cue = lambda *_: self.fail('unexpected activation')
        self.audio.change_microphone(SimpleNamespace(select=applied.append), 'external')
        self.assertEqual(applied, ['external'])
        self.assertEqual(self.processes, [])
        self.assertFalse(self.audio.active)

    def test_switch_invalidates_inflight_recognition_and_restarts_active_capture_without_cue(self):
        from speech_control import VoiceControl
        self.assertTrue(hasattr(self.audio, 'change_microphone'), 'source transition missing')
        entered, release = threading.Event(), threading.Event()
        actions = []
        class Recognizer:
            def recognize(self, data):
                entered.set()
                release.wait(2)
                return {'text': '坐下', 'confidence': 1}
        voice = VoiceControl(Recognizer, SimpleNamespace(execute=lambda *a, **kw: actions.append('sit')), self.audio.play)
        self.audio.voice = voice
        self.addCleanup(voice.close)
        self.addCleanup(release.set)
        process = self.start()
        self.feed_phrase(process)
        self.assertTrue(entered.wait(1))
        old = self.audio.generation
        cues = []
        self.audio.activation_cue = self.audio.deactivation_cue = lambda *_: cues.append(True)
        def apply(source):
            release.set()
            self.assertTrue(voice.idle.wait(1))
            self.audio.feed(pcm(.4), old)
            self.audio.play(b'stale playback')
        self.audio.change_microphone(SimpleNamespace(select=apply), 'both')
        self.assertTrue(eventually(lambda: len(self.processes) == 2))
        self.assertTrue(self.audio.active)
        self.assertIsNotNone(process.poll())
        self.assertEqual((actions, cues), ([], []))
        self.assertFalse(self.backend.played.is_set())

    def test_level_uses_live_right_adc_ac_samples_in_voice_and_music_and_expires(self):
        self.assertTrue(hasattr(self.audio, 'microphone_level_dbfs'), 'live microphone level missing')
        self.assertIsNone(self.audio.microphone_level_dbfs)
        self.start()
        for music in (False, True):
            self.audio.set_music(music)
            self.audio.feed(measured_pcm(.12, amplitude=8192, dc=5000))
            self.assertAlmostEqual(self.audio.microphone_level_dbfs, -15.05, delta=.3)
            self.audio.feed(pcm(.12, right=0, left=25000))
            self.assertEqual(self.audio.microphone_level_dbfs, -96)
        self.now[0] += 1
        self.assertIsNone(self.audio.microphone_level_dbfs)
        self.audio.stop()
        self.assertIsNone(self.audio.microphone_level_dbfs)

    def test_switch_preserves_music_and_concurrent_stop_wins(self):
        self.assertTrue(hasattr(self.audio, 'change_microphone'), 'source transition missing')
        self.start()
        self.audio.set_music(True)
        self.audio.change_microphone(SimpleNamespace(select=lambda source: None), 'both')
        self.assertTrue(self.audio.active and self.audio.music_mode)
        switching, release = threading.Event(), threading.Event()
        def apply(source):
            switching.set()
            release.wait(1)
        worker = threading.Thread(target=self.audio.change_microphone, args=(SimpleNamespace(select=apply), 'external'))
        worker.start()
        self.assertTrue(switching.wait(1))
        off = threading.Thread(target=self.audio.stop)
        off.start()
        self.assertTrue(eventually(lambda: not self.audio.active))
        release.set()
        worker.join(1)
        off.join(1)
        self.assertFalse(self.audio.active or self.audio.music_mode)

    def test_update_blocks_r3_activation_and_cancels_active_feed(self):
        blocked=[True];self.audio.guard=lambda:not blocked[0]
        button=continuous.ToggleButton(self.audio);button.event(1)
        self.assertFalse(self.audio.active);self.assertEqual(self.processes,[])
        blocked[0]=False;button.event(0);button.event(1)
        self.assertTrue(eventually(lambda:len(self.processes)==1))
        blocked[0]=True;self.audio.feed(pcm(.1))
        self.assertFalse(self.audio.active);self.assertFalse(self.audio.music_mode)
        self.assertIsNone(self.audio.pending)
        button.event(0);button.event(1);self.assertFalse(self.audio.active)

    def setUp(self):
        self.assertTrue(hasattr(continuous, 'ContinuousAudio'), 'ContinuousAudio is missing')
        self.backend = FakeAudio()
        self.processes = []
        self.commands = []

        def spawn(command, **kwargs):
            self.commands.append(command)
            process = CaptureProcess()
            self.processes.append(process)
            return process

        self.spawn = patch.object(continuous.subprocess, 'Popen', side_effect=spawn)
        self.spawn.start()
        self.now = [0.0]
        self.audio = continuous.ContinuousAudio(
            self.backend, capture_device='mic', maximum=1, silence=.1, minimum=.1,
            tail=.2, clock=lambda: self.now[0])
        self.addCleanup(self.spawn.stop)
        self.addCleanup(self.audio.close)

    def start(self):
        self.audio.start()
        self.assertTrue(eventually(lambda: len(self.processes) == 1))
        return self.processes[0]

    def feed_phrase(self, process):
        process.stdout.feed(pcm(.2) + pcm(.1, right=0))

    def test_single_unlimited_capture_echoes_completed_phrase(self):
        process = self.start()
        self.feed_phrase(process)
        self.assertTrue(self.backend.played.wait(1))
        self.assertEqual(len(self.processes), 1)
        self.assertNotIn('-d', self.commands[0])
        self.assertIn('48000', self.commands[0])
        self.assertFalse(self.audio.expired())

    def test_continuous_capture_requests_20ms_period_and_80ms_buffer(self):
        self.start()
        self.assertIn('--period-time=20000', self.commands[0])
        self.assertIn('--buffer-time=80000', self.commands[0])

    def test_listening_on_and_off_are_logged_immediately(self):
        with self.assertLogs('duck-echo', level='INFO') as logged:
            self.start()
            self.audio.stop(play=False)
        self.assertTrue(any('listening ON' in line for line in logged.output))
        self.assertTrue(any('listening OFF' in line for line in logged.output))

    def test_playback_and_tail_are_not_recognized(self):
        process = self.start()
        self.audio.play(pcm(.2))
        self.assertTrue(self.backend.played.wait(1))
        self.feed_phrase(process)
        self.assertTrue(eventually(lambda: not process.stdout.blocks))
        self.backend.playing = False
        self.now[0] = .1
        self.feed_phrase(process)
        self.assertTrue(eventually(lambda: not process.stdout.blocks))
        self.assertEqual(sum(call[0] == 'play' for call in self.backend.calls if isinstance(call, tuple)), 1)
        self.now[0] = 1
        self.feed_phrase(process)
        self.assertTrue(eventually(lambda: sum(call[0] == 'play' for call in self.backend.calls
                                              if isinstance(call, tuple)) == 2))

    def test_command_cue_suppresses_audio_through_tail(self):
        process = self.start()
        observed = []
        def cue(accepted):
            self.feed_phrase(process)
            observed.append(accepted)
            self.assertTrue(eventually(lambda: not process.stdout.blocks))
        self.audio.command_cue(cue, True)
        self.feed_phrase(process)
        self.assertTrue(eventually(lambda: not process.stdout.blocks))
        self.assertEqual(observed, [True])
        self.assertFalse(self.backend.played.is_set())

    def test_off_cancels_inflight_recognition_and_playback_without_blocking(self):
        from speech_control import VoiceControl
        entered, release = threading.Event(), threading.Event()
        actions = []
        class Recognizer:
            def recognize(self, data):
                entered.set()
                release.wait(1)
                return {'text': '坐下', 'confidence': 1}
        executor = SimpleNamespace(execute=lambda *a, **kw: actions.append('sit'))
        self.audio.voice = VoiceControl(Recognizer, executor, self.audio.play)
        self.addCleanup(self.audio.voice.close)
        process = self.start()
        self.feed_phrase(process)
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.audio.stop(play=False)
        self.assertLess(time.monotonic() - started, .1)
        release.set()
        self.assertTrue(self.audio.voice.idle.wait(1))
        self.assertEqual(actions, [])
        self.assertFalse(self.backend.played.is_set())
        self.assertTrue(eventually(lambda: 'stop_playback' in self.backend.calls))

    def test_eof_ends_session_without_respawning(self):
        process = self.start()
        with self.assertLogs('duck-echo', level='WARNING'):
            process.returncode = 1
            process.stdout.end()
            self.assertTrue(eventually(self.audio.expired))
        time.sleep(.03)
        self.assertEqual(len(self.processes), 1)
        self.assertFalse(self.backend.played.is_set())

    def test_capture_read_failure_ends_session(self):
        process = self.start()
        with self.assertLogs('duck-echo', level='ERROR'):
            process.stdout.read = lambda size: (_ for _ in ()).throw(OSError('lost capture'))
            process.stdout.feed(pcm(.02))
            self.assertTrue(eventually(self.audio.expired))
        self.assertEqual(len(self.processes), 1)

    def test_observer_sees_raw_pcm_outside_lock_only_when_unsuppressed(self):
        observed = []
        acquired = threading.Event()
        self.assertTrue(hasattr(self.audio, 'observer'), 'raw PCM observer hook is missing')
        self.assertTrue(hasattr(self.audio, 'suppressed'), 'suppression gate is missing')
        def observe(raw, timestamp):
            def acquire():
                with self.audio.condition:
                    acquired.set()
            thread = threading.Thread(target=acquire)
            thread.start()
            self.assertTrue(acquired.wait(.2), 'observer called while capture lock held')
            thread.join()
            observed.append((raw, timestamp))
        self.audio.observer = observe
        self.start()
        raw = pcm(.02, left=777)
        self.audio.feed(raw)
        self.assertEqual(observed, [(raw, 0.0)])
        self.audio.play(pcm(.2))
        self.assertTrue(self.audio.suppressed())
        self.audio.feed(raw)
        self.assertEqual(len(observed), 1)
        self.audio.stop(play=False)
        self.audio.feed(raw)
        self.assertEqual(len(observed), 1)

    def test_stale_eof_cleanup_cannot_stop_new_session(self):
        process = self.start()
        entered, release = threading.Event(), threading.Event()
        def pause(*args):
            entered.set()
            release.wait(1)
        with patch.object(continuous.LOG, 'warning', side_effect=pause):
            process.returncode = 1
            process.stdout.end()
            self.assertTrue(entered.wait(1))
            self.audio.stop(play=False)
            self.audio.start()
            release.set()
            self.assertTrue(eventually(lambda: len(self.processes) == 2),
                            'old EOF teardown canceled the new capture session')
            self.assertFalse(self.audio.expired())
            self.assertTrue(self.audio.active)

    def test_off_during_backend_teardown_cannot_launch_stale_pcm(self):
        entered, release = threading.Event(), threading.Event()
        sent = []
        def delayed_play(data, allowed=None):
            entered.set()
            release.wait(1)
            if allowed is None or allowed():
                sent.append(data)
        self.backend.play = delayed_play
        self.start()
        self.audio.play(pcm(.2))
        self.assertTrue(entered.wait(1))
        self.audio.stop(play=False)
        release.set()
        self.assertTrue(eventually(lambda: not self.audio.output_pending))
        time.sleep(.01)
        self.assertEqual(len(sent), 0)

    def test_close_reaps_capture_stops_workers_and_releases_backend(self):
        closed = threading.Event()
        self.backend.close = closed.set
        process = self.start()
        self.audio.close()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.audio.capture_thread.is_alive())
        self.assertFalse(self.audio.worker.is_alive())
        self.assertTrue(closed.is_set(), 'playback backend was not closed')

    def test_controller_accepts_calibrated_signal_threshold(self):
        import inspect
        self.assertIn('threshold', inspect.signature(continuous.ContinuousAudio).parameters,
                      'controller signal threshold cannot be calibrated')

    def test_command_cue_clearing_queued_echo_does_not_latch_suppression(self):
        self.start()
        with self.audio.condition:  # Keep dispatch from consuming the queued play.
            self.audio.play(pcm(.2))
            self.audio.command_cue(lambda accepted: None, True)
            self.now[0] = 100
            self.assertFalse(self.audio.suppressed(),
                             'cue discarded playback but left output suppression latched')

    def test_repeated_stop_uses_one_reaper_for_the_current_capture(self):
        process = self.start()
        entered, release = threading.Event(), threading.Event()
        calls = []
        original_terminate = echo.terminate
        def blocked_terminate(target):
            calls.append(target)
            entered.set()
            release.wait(1)
            original_terminate(target)
        try:
            with patch.object(echo, 'terminate', side_effect=blocked_terminate):
                for _ in range(50):
                    self.audio.stop(play=False)
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(calls), 1, 'stop spawned duplicate recorder reapers')
                release.set()
                self.assertTrue(eventually(lambda: self.audio.capture is None))
                self.assertEqual(len(calls), 1, 'capture cleanup reaped the same process twice')
        finally:
            release.set()

    def test_music_mode_only_streams_observer_and_never_submits_song_to_voice_or_echo(self):
        self.assertTrue(hasattr(self.audio, 'set_music'), 'music phrase exclusion missing')
        streams, cancellations = [], []
        self.audio.music = SimpleNamespace(set_enabled=lambda enabled: cancellations.append(enabled),
                                          cancel=lambda: cancellations.append('cancel'))
        self.audio.voice = SimpleNamespace(cancel=lambda: None,
                                          submit=lambda phrase: self.fail('song submitted to posture ASR'))
        self.audio.observer = lambda raw, at: streams.append(raw)
        process = self.start()
        self.audio.set_music(True)
        self.feed_phrase(process)
        self.assertTrue(eventually(lambda: len(streams) > 0))
        self.assertFalse(self.backend.played.is_set())
        self.assertEqual(self.audio.segmenter.buffered_bytes, 0)
        self.audio.stop(play=False)
        self.assertFalse(self.audio.music_mode)
        self.assertIn(False, cancellations)

    def test_mode_round_trip_invalidates_echo_waiting_in_backend_teardown(self):
        self.assertTrue(hasattr(self.audio, 'set_music'))
        entered, release = threading.Event(), threading.Event()
        sent = []
        def delayed_play(data, allowed=None):
            entered.set()
            release.wait(1)
            if allowed is None or allowed():
                sent.append(data)
        self.backend.play = delayed_play
        self.start()
        self.audio.play(pcm(.2))
        self.assertTrue(entered.wait(1))
        self.audio.set_music(True)
        self.audio.set_music(False)
        release.set()
        self.assertTrue(eventually(lambda: not self.audio.output_pending))
        time.sleep(.02)
        self.assertEqual(len(sent), 0)

    def test_stale_stop_cannot_disable_music_after_new_web_start(self):
        music = SimpleNamespace(enabled=False, cancel=lambda: None)
        music.set_enabled = lambda enabled: setattr(music, 'enabled', enabled)
        self.audio.music = music
        self.start()
        self.audio.set_music(True)
        entered, release = threading.Event(), threading.Event()
        cancel = self.audio._cancel_voice
        def pause(*args, **kwargs):
            cancel(*args, **kwargs)
            if threading.current_thread().name == 'stale-stop':
                entered.set()
                release.wait(1)
        with patch.object(self.audio, '_cancel_voice', side_effect=pause):
            stale = threading.Thread(target=self.audio.stop, name='stale-stop')
            stale.start()
            try:
                self.assertTrue(entered.wait(1))
                self.audio.start()
                self.audio.set_music(True)
            finally:
                release.set()
                stale.join(1)
        self.assertTrue(self.audio.active and self.audio.music_mode)
        self.assertTrue(music.enabled, 'stale stop disabled the newly enabled question worker')

    def test_activation_chirp_once_for_r3_and_web_actual_off_on(self):
        from audio_control import AudioControl
        self.assertTrue(hasattr(self.audio, 'activation_cue'), 'activation cue is missing')
        calls = []
        self.audio.activation_cue = lambda allowed: calls.append('chirp') if allowed() else None
        button = continuous.ToggleButton(self.audio)
        button.event(1)
        button.event(2)
        button.event(0)
        self.assertTrue(eventually(lambda: len(calls) == 1))
        self.audio.start()  # Already active does not restart/chirp.
        hub = AudioControl(self.audio, SimpleNamespace(snapshot=lambda: {}))
        hub.request({'method': 'set', 'listening': True})
        hub.request({'method': 'status'})
        time.sleep(.02)
        self.assertEqual(calls, ['chirp'])
        self.audio.stop()
        hub.request({'method': 'set', 'music': True})
        self.assertTrue(eventually(lambda: len(calls) == 2))
        self.assertTrue(self.audio.music_mode)

    def test_normal_restart_cancels_old_music_worker_even_if_previous_stop_is_waiting(self):
        music = SimpleNamespace(enabled=False, cancel=lambda: None)
        music.set_enabled = lambda enabled: setattr(music, 'enabled', enabled)
        self.audio.music = music
        self.start()
        self.audio.set_music(True)
        entered, release = threading.Event(), threading.Event()
        cancel = self.audio._cancel_voice
        def pause(*args, **kwargs):
            if threading.current_thread().name == 'stale-stop':
                entered.set()
                release.wait(1)
            cancel(*args, **kwargs)
        with patch.object(self.audio, '_cancel_voice', side_effect=pause):
            stale = threading.Thread(target=self.audio.stop, name='stale-stop')
            stale.start()
            try:
                self.assertTrue(entered.wait(1))
                self.audio.start()
                self.assertFalse(music.enabled, 'new normal session retained old music generation')
            finally:
                release.set()
                stale.join(1)

    def test_restart_does_not_expose_active_until_old_voice_is_canceled(self):
        self.start()
        self.audio.stop()
        entered, release = threading.Event(), threading.Event()
        def cancel():
            entered.set()
            release.wait(1)
        self.audio.voice = SimpleNamespace(cancel=cancel)
        restart = threading.Thread(target=self.audio.start)
        restart.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.audio.active, 'old voice could enqueue PCM into the new session')
            self.audio.play(b'old pcm')
            self.assertIsNone(self.audio.pending)
        finally:
            release.set()
            restart.join(1)

    def test_off_cancels_queued_activation_and_does_not_latch_suppression(self):
        self.assertTrue(hasattr(self.audio, 'activation_cue'), 'activation cue is missing')
        calls = []
        self.audio.activation_cue = lambda allowed: calls.append('chirp') if allowed() else None
        with self.audio.condition:  # Dispatcher cannot consume the pending cue.
            self.audio.start()
            self.assertTrue(self.audio.suppressed())
            self.audio.stop()
        time.sleep(.02)
        self.now[0] = 100
        self.assertFalse(self.audio.suppressed())
        self.assertEqual(calls, [])

    def test_activation_chirp_runs_off_caller_thread_and_suppresses_feedback_and_tail(self):
        self.assertTrue(hasattr(self.audio, 'activation_cue'), 'activation cue is missing')
        entered, release = threading.Event(), threading.Event()
        observed = []
        self.audio.observer = lambda raw, at: observed.append(raw)
        def cue(allowed):
            if allowed():
                entered.set()
                release.wait(1)
        self.audio.activation_cue = cue
        began = time.monotonic()
        self.audio.start()
        self.assertLess(time.monotonic()-began, .1)
        try:
            self.assertTrue(entered.wait(1))
            self.audio.feed(pcm(.02))
            self.assertEqual(len(observed), 0)
        finally:
            release.set()
        self.assertTrue(eventually(lambda: not self.audio.cue_active))
        self.audio.feed(pcm(.02))
        self.assertEqual(len(observed), 0)
        self.now[0] = 2
        self.audio.feed(pcm(.02))
        self.assertEqual(len(observed), 1)

    def test_manual_r3_and_web_off_announce_different_native_sound_once(self):
        from audio_control import AudioControl
        self.assertTrue(hasattr(self.audio, 'deactivation_cue'), 'manual off cue is missing')
        calls = []
        self.audio.activation_cue = lambda allowed: calls.append('chirp') if allowed() else None
        def off(allowed):
            self.assertFalse(self.audio.active, 'off cue must not re-enable capture')
            if allowed():
                calls.append('coo')
        self.audio.deactivation_cue = off
        button = continuous.ToggleButton(self.audio)
        button.event(1)
        button.event(0)
        self.assertTrue(eventually(lambda: calls == ['chirp']))
        button.event(1)
        button.event(2)
        button.event(0)
        self.assertTrue(eventually(lambda: calls == ['chirp', 'coo']))
        hub = AudioControl(self.audio, SimpleNamespace(snapshot=lambda: {}))
        hub.request({'method': 'set', 'listening': True})
        self.assertTrue(eventually(lambda: len(calls) == 3))
        with self.audio.condition:
            hub.request({'method': 'set', 'listening': False})
            hub.request({'method': 'set', 'listening': False})
        self.assertTrue(eventually(lambda: len(calls) == 4))
        self.assertEqual(calls, ['chirp', 'coo', 'chirp', 'coo'])

    def test_automatic_or_initially_off_stops_do_not_announce(self):
        self.assertTrue(hasattr(self.audio, 'deactivation_cue'), 'manual off cue is missing')
        calls = []
        self.audio.deactivation_cue = lambda allowed: calls.append('coo') if allowed() else None
        self.audio.stop(announce=True)
        self.audio.start()
        self.audio.stop()  # Select/disconnect/EOF and close use this quiet path.
        self.audio.close()
        time.sleep(.02)
        self.assertEqual(calls, [])

    def test_new_start_and_close_cancel_queued_off_cue(self):
        self.assertTrue(hasattr(self.audio, 'deactivation_cue'), 'manual off cue is missing')
        calls = []
        on = []
        self.audio.activation_cue = lambda allowed: on.append('chirp') if allowed() else None
        self.audio.deactivation_cue = lambda allowed: calls.append('coo') if allowed() else None
        with self.audio.condition:
            self.audio.start()
            self.audio.stop(announce=True)
            self.audio.start()
        time.sleep(.02)
        self.assertEqual(calls, [])
        self.assertTrue(eventually(lambda: on == ['chirp']), 'stale off cue discarded the new on cue')
        entered, release = threading.Event(), threading.Event()
        def stop_output():
            entered.set()
            release.wait(1)
        self.backend.stop_playback = stop_output
        self.audio.stop(announce=True)
        closing = threading.Thread(target=self.audio.close)
        try:
            self.assertTrue(entered.wait(1))
            closing.start()
            self.assertTrue(eventually(lambda: self.audio.closed))
        finally:
            release.set()
            if closing.ident is not None:
                closing.join(1)
        self.assertEqual(calls, [])

    def test_off_sound_waits_for_consumer_cancellation_and_does_not_block_caller(self):
        self.assertTrue(hasattr(self.audio, 'deactivation_cue'), 'manual off cue is missing')
        self.start()
        canceled, entered, release = threading.Event(), threading.Event(), threading.Event()
        self.audio.voice = SimpleNamespace(cancel=canceled.set)
        def cue(allowed):
            self.assertTrue(canceled.is_set())
            if allowed():
                entered.set()
                release.wait(1)
        self.audio.deactivation_cue = cue
        began = time.monotonic()
        self.audio.stop(announce=True)
        self.assertLess(time.monotonic()-began, .1)
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.audio.active)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
