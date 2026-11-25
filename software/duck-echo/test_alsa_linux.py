"""Exercise real Linux subprocesses with simulated ALSA tools, without hardware."""
import json
import os
import socket
from pathlib import Path
import tempfile
import time
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import duck_echo as echo
from continuous_audio import ContinuousAudio


FAKE_ALSA = r'''#!/usr/bin/env python3
import array, json, math, os, signal, sys, time
from pathlib import Path
kind = Path(sys.argv[0]).name
if kind == 'arecord':
    time.sleep(float(os.environ.get('FAKE_CAPTURE_START_DELAY', '0')))
if os.environ.get('FAKE_BUSY') == kind:
    print('audio open error: Device or resource busy', file=sys.stderr)
    sys.exit(1)
if kind == 'arecord':
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    seconds = int(sys.argv[sys.argv.index('-d') + 1]) if '-d' in sys.argv else 3600
    block = array.array('h', [x for i in range(480) for x in (0, int(2000*math.sin(i/7)))])
    for index in range(seconds * 100):
        if index == int(os.environ.get('FAKE_CAPTURE_EOF', '-1')):
            print('injected capture exit', file=sys.stderr)
            sys.exit(1)
        sys.stdout.buffer.write(block.tobytes())
        sys.stdout.buffer.flush()
        time.sleep(.01)
elif kind == 'aplay':
    data = sys.stdin.buffer.read()
    time.sleep(float(os.environ.get('FAKE_PLAYBACK_DELAY', '0')))
    Path(os.environ['FAKE_PLAYBACK']).write_text(str(len(data)))
    if os.environ.get('FAKE_PLAY_LOG'):
        with open(os.environ['FAKE_PLAY_LOG'], 'a') as target:
            target.write(str(len(data)) + '\n')
elif kind == 'amixer':
    state_path = Path(os.environ['FAKE_MIXER'])
    state = json.loads(state_path.read_text())
    if sys.argv[3] == 'controls':
        for index, name in enumerate(state):
            print("numid=%d,iface=MIXER,name='%s'" % (index+1, name))
        sys.exit(0)
    name = sys.argv[4].removeprefix('name=')
    if sys.argv[3] == 'cset':
        state[name] = sys.argv[5]
        state_path.write_text(json.dumps(state))
    print('  : values=' + state[name])
'''


@unittest.skipUnless(os.name == 'posix', 'Linux subprocess integration')
class LinuxAlsaTests(unittest.TestCase):
    def test_private_socket_switches_both_physical_inputs_with_listening_off(self):
        from audio_control import AudioControl, AudioControlServer
        from install import CAPTURE_SETTINGS, MicrophoneRoutes
        hardware = self.root / 'mixer.json'
        hardware.write_text(json.dumps(CAPTURE_SETTINGS))
        routes = MicrophoneRoutes('aic3104', self.root / 'microphone-source.json')
        routes.restore()
        audio = self.continuous()
        server = AudioControlServer(AudioControl(audio, SimpleNamespace(snapshot=lambda: {}), routes),
                                    self.root / 'control.sock')
        server.start()
        self.addCleanup(server.close)
        for source, mic, line in [('external', 'off', 'on'), ('both', 'on', 'on'), ('onboard', 'on', 'off')]:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(5)
                client.connect(str(server.path))
                client.sendall((json.dumps({'method': 'microphone', 'source': source}) + '\n').encode())
                reply = json.loads(client.recv(8192))
            self.assertTrue(reply['ok'], reply)
            self.assertEqual(reply['status']['microphone_source'], source)
            self.assertIsNone(reply['status']['microphone_level_dbfs'])
            self.assertFalse(reply['status']['listening'])
            self.assertIsNone(audio.capture)
            state = json.loads(hardware.read_text())
            self.assertEqual((state['Right PGA Mixer Mic3R Switch'], state['Right PGA Mixer Line1L Switch']), (mic, line))
        self.assertFalse(self.playback.exists())

    def test_music_query_answers_during_unbroken_fake_capture_and_ipc_off_cancels(self):
        from audio_control import AudioControl, AudioControlServer
        from music_interaction import MusicInteraction
        from test_music_interaction import Decoder, eventually
        class QuestionDecoder(Decoder):
            def AcceptWaveform(self, pcm):
                self.current = ('partial', '感觉 怎么样', .99)
                return False
        shared = SimpleNamespace(model=SimpleNamespace(vosk_model_find_word=lambda word: 0),
                                 recognizer_type=QuestionDecoder)
        feel = SimpleNamespace(snapshot=lambda: {'feel': 'lively', 'confidence': 1., 'age': 0})
        audio = ContinuousAudio(self.audio, maximum=1)
        quacks = []
        def reply(tag, allowed):
            if allowed():
                audio.command_cue(lambda accepted: quacks.append(tag) if allowed() else None, True)
        music = MusicInteraction(lambda: shared, feel, reply)
        self.addCleanup(music.close)
        self.addCleanup(audio.close)
        audio.music = music
        audio.observer = lambda raw, timestamp: music.feed(raw, timestamp) if audio.music_mode else None
        server = AudioControlServer(AudioControl(audio, feel), str(self.root / 'control.sock'))
        server.start()
        self.addCleanup(server.close)
        def request(fields):
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(1)
                client.connect(str(self.root / 'control.sock'))
                client.sendall((json.dumps(fields)+'\n').encode())
                return json.loads(client.recv(8192))
        self.assertTrue(request({'method': 'set', 'music': True})['status']['listening'])
        self.assertTrue(eventually(lambda: audio.capture is not None))
        process = audio.capture
        self.assertTrue(eventually(lambda: len(quacks) == 1, timeout=2))
        self.assertEqual(quacks, ['chirp'])
        self.assertIs(audio.capture, process)
        self.assertFalse(self.playback.exists(), 'music was submitted to ordinary echo')
        self.assertEqual(audio.segmenter.buffered_bytes, 0)
        request({'method': 'set', 'music': False})
        self.assertTrue(audio.active)
        self.assertIs(audio.capture, process)
        status = request({'method': 'set', 'listening': False})['status']
        self.assertFalse(status['listening'] or status['music'])
        self.assertTrue(eventually(lambda: process.poll() is not None))
        time.sleep(.1)
        self.assertEqual(quacks, ['chirp'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('arecord', 'aplay', 'amixer'):
            path = self.root / name
            path.write_text(FAKE_ALSA)
            path.chmod(0o755)
        self.playback = self.root / 'played'
        self.environment = patch.dict(os.environ, {
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'FAKE_PLAYBACK': str(self.playback),
            'FAKE_MIXER': str(self.root / 'mixer.json'),
            'FAKE_BUSY': '',
            'FAKE_CAPTURE_EOF': '-1',
            'FAKE_PLAYBACK_DELAY': '0',
            'FAKE_PLAY_LOG': '',
            'FAKE_CAPTURE_START_DELAY': '0',
        })
        self.environment.start()
        self.audio = echo.AlsaAudio('fake-mic', 'fake-speaker', maximum=1)

    def tearDown(self):
        self.audio.close()
        self.environment.stop()
        self.temp.cleanup()

    def test_real_capture_release_and_playback_subprocesses(self):
        # Startup deliberately exceeds the old 400 ms recording sleep, which
        # reproduced short/empty clips on the ARM integration runner.
        os.environ['FAKE_CAPTURE_START_DELAY'] = '.45'
        self.audio.start()
        self.wait_for_captured_audio()
        result = self.audio.stop(play=True)
        self.assertTrue(self.audio.finish_playback())
        self.assertTrue(result['ok'])
        self.assertGreater(int(self.playback.read_text()), 48000)

    def test_natural_time_limit_caps_recording(self):
        self.audio.start()
        deadline = time.monotonic() + 4
        while not self.audio.expired() and time.monotonic() < deadline:
            time.sleep(.05)
        result = self.audio.stop(play=False)
        self.assertLessEqual(result['frames'], 48000)
        self.assertGreater(result['frames'], 40000)
        self.assertFalse(self.playback.exists())

    def test_disconnect_discards_clip_and_reaps_recorder(self):
        button = echo.HoldButton(self.audio)
        button.event(1)
        process = self.audio.capture[0]
        self.wait_for_captured_audio()
        button.cancel()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.playback.exists())

    def test_busy_capture_does_not_play(self):
        os.environ['FAKE_BUSY'] = 'arecord'
        os.environ['FAKE_CAPTURE_START_DELAY'] = '.2'
        self.audio.start()
        process = self.audio.capture[0]
        self.assertTrue(self.wait_for(lambda: process.poll() is not None),
                        'fake capture did not reach its injected busy-device exit')
        self.assertEqual(process.returncode, 1)
        with self.assertLogs('duck-echo', level='ERROR'):
            result = self.audio.stop(play=True)
        self.assertFalse(result['ok'])
        self.assertFalse(self.playback.exists())

    def test_busy_playback_reports_failure(self):
        os.environ['FAKE_BUSY'] = 'aplay'
        os.environ['FAKE_CAPTURE_START_DELAY'] = '.45'
        self.audio.start()
        self.wait_for_captured_audio()
        with self.assertLogs('duck-echo', level='ERROR'):
            self.audio.stop(play=True)
            self.assertFalse(self.audio.finish_playback())

    def continuous(self):
        audio = ContinuousAudio(self.audio, maximum=1)
        self.addCleanup(audio.close)
        return audio

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.02)
        return predicate()

    def wait_for_captured_audio(self, seconds=.3):
        process, _, _, chunks, _ = self.audio.capture
        required = round(seconds * echo.RATE) * echo.FRAME_BYTES
        self.assertTrue(self.wait_for(lambda: len(chunks) >= required or process.poll() is not None),
                        'fake capture produced no complete clip before deadline')
        self.assertGreaterEqual(len(chunks), required,
                                f'capture exited {process.poll()} before producing a complete clip')

    def test_continuous_limit_echoes_without_ending_single_capture_stream(self):
        audio = self.continuous()
        audio.start()
        self.assertTrue(self.wait_for(lambda: audio.capture is not None))
        process = audio.capture
        self.assertIn('--period-time=20000', process.args)
        self.assertIn('--buffer-time=80000', process.args)
        self.assertTrue(self.wait_for(self.playback.exists))
        self.assertFalse(audio.expired())
        self.assertIs(audio.capture, process)
        self.assertIsNone(process.poll())
        audio.stop(play=False)
        self.assertTrue(self.wait_for(lambda: process.poll() is not None))

    def test_continuous_busy_capture_ends_session_and_does_not_retry(self):
        os.environ['FAKE_BUSY'] = 'arecord'
        audio = self.continuous()
        with self.assertLogs('duck-echo', level='WARNING'):
            audio.start()
            self.assertTrue(self.wait_for(audio.expired))
        generation = audio.generation
        time.sleep(.1)
        self.assertEqual(audio.generation, generation)
        self.assertIsNone(audio.capture)
        self.assertFalse(self.playback.exists())

    def test_continuous_eof_discards_unfinished_phrase_and_reaps_process(self):
        os.environ['FAKE_CAPTURE_EOF'] = '30'
        audio = self.continuous()
        with self.assertLogs('duck-echo', level='WARNING'):
            audio.start()
            self.assertTrue(self.wait_for(audio.expired))
        self.assertFalse(self.playback.exists())
        self.assertIsNone(audio.capture)

    def test_continuous_playback_and_tail_do_not_form_next_phrase(self):
        os.environ['FAKE_PLAYBACK_DELAY'] = '.4'
        os.environ['FAKE_PLAY_LOG'] = str(self.root / 'play-log')
        audio = self.continuous()
        audio.start()
        self.assertTrue(self.wait_for(self.playback.exists))
        time.sleep(.5)
        self.assertEqual((self.root / 'play-log').read_text().count('\n'), 1)
        self.assertFalse(audio.expired())
        audio.stop(play=False)
        self.assertTrue(self.wait_for(lambda: not self.audio.playback_active()))

    def test_mic_setup_and_restore_preserve_existing_settings(self):
        import install
        original = {name: 'off' for name in install.CAPTURE_SETTINGS}
        original['PGA Capture Volume'] = '12,13'
        (self.root / 'mixer.json').write_text(json.dumps(original))
        backup = self.root / 'backup.json'
        install.prepare_microphone('aic3104', backup)
        self.assertEqual(json.loads(backup.read_text())['values'], original)
        changed = json.loads((self.root / 'mixer.json').read_text())
        self.assertEqual(changed['Right PGA Mixer Mic3R Switch'], 'on')
        install.restore_microphone(backup)
        self.assertEqual(json.loads((self.root / 'mixer.json').read_text()), original)

    def test_native_3104_mic2_alias_without_line2_controls(self):
        import install
        original = {name.replace('Mic3', 'Mic2'): 'off'
                    for name in install.CAPTURE_SETTINGS if 'Line2' not in name}
        original['PGA Capture Volume'] = '10,10'
        (self.root / 'mixer.json').write_text(json.dumps(original))
        backup = self.root / 'backup.json'
        install.prepare_microphone('aic3104', backup)
        changed = json.loads((self.root / 'mixer.json').read_text())
        self.assertEqual(changed['Right PGA Mixer Mic2R Switch'], 'on')
        self.assertNotIn('Right PGA Mixer Line2R Switch', changed)
        install.restore_microphone(backup)
        self.assertEqual(json.loads((self.root / 'mixer.json').read_text()), original)

    def test_unknown_codec_is_rejected_before_mixer_changes(self):
        import install
        original = {'PGA Capture Volume': '10,10'}
        (self.root / 'mixer.json').write_text(json.dumps(original))
        backup = self.root / 'backup.json'
        with self.assertRaises(RuntimeError):
            install.prepare_microphone('aic3104', backup)
        self.assertEqual(json.loads((self.root / 'mixer.json').read_text()), original)
        self.assertFalse(backup.exists())

    def test_reinstall_after_restore_saves_a_fresh_baseline(self):
        import install
        original = {name: 'off' for name in install.CAPTURE_SETTINGS}
        original['PGA Capture Volume'] = '10,10'
        mixer = self.root / 'mixer.json'
        mixer.write_text(json.dumps(original))
        backup = self.root / 'backup.json'
        install.prepare_microphone('aic3104', backup)
        install.restore_microphone(backup)
        updated = dict(original, **{'PGA Capture Volume': '20,20'})
        mixer.write_text(json.dumps(updated))
        install.prepare_microphone('aic3104', backup)
        install.restore_microphone(backup)
        self.assertEqual(json.loads(mixer.read_text()), updated)

    def test_upgrade_preserves_active_installation_backup(self):
        import install
        original = {name: 'off' for name in install.CAPTURE_SETTINGS}
        original['PGA Capture Volume'] = '10,10'
        mixer = self.root / 'mixer.json'
        mixer.write_text(json.dumps(original))
        backup = self.root / 'backup.json'
        install.prepare_microphone('aic3104', backup)
        install.prepare_microphone('aic3104', backup)
        self.assertEqual(json.loads(backup.read_text())['values'], original)
        install.restore_microphone(backup)
        self.assertEqual(json.loads(mixer.read_text()), original)

    def test_failed_upgrade_restores_attempt_state_files_and_prior_service(self):
        import install
        original = {name: 'off' for name in install.CAPTURE_SETTINGS}
        original['PGA Capture Volume'] = '20,20'
        mixer = self.root / 'mixer.json'
        mixer.write_text(json.dumps(original))
        state, destination = self.root / 'state', self.root / 'installed'
        state.mkdir()
        destination.mkdir()
        baseline = {'card': 'aic3104', 'values': dict(original, **{'PGA Capture Volume': '12,13'})}
        backup = state / 'mixer-before.json'
        backup.write_text(json.dumps(baseline))
        program = destination / 'duck_echo.py'
        program.write_text('# prior program\n')
        unit = self.root / 'unit.service'
        unit.write_text('# prior unit\n')
        calls = []
        real_run = install.run

        def run(arguments, check=True):
            if arguments[0] == 'amixer':
                return real_run(arguments, check)
            calls.append(arguments)
            if arguments[:2] == ['systemctl', 'enable'] and '--now' in arguments:
                raise subprocess.CalledProcessError(1, arguments, stderr='injected enable failure')
            return SimpleNamespace(returncode=0, stdout='stable', stderr='')

        with patch.multiple(install, STATE=state, DESTINATION=destination, UNIT=unit,
                            DEFAULTS=self.root / 'defaults'), patch.object(install, 'run', run), \
                patch.object(install.os, 'geteuid', return_value=0), \
                patch.object(install.shutil, 'which', return_value='/fake/tool'):
            with self.assertRaises(subprocess.CalledProcessError):
                install.main(['install'])
        self.assertEqual(json.loads(mixer.read_text()), original)
        self.assertEqual(json.loads(backup.read_text()), baseline)
        self.assertEqual(program.read_text(), '# prior program\n')
        self.assertEqual(unit.read_text(), '# prior unit\n')
        self.assertIn(['systemctl', 'start', 'duck-echo.service'], calls)


if __name__ == '__main__':
    unittest.main()
