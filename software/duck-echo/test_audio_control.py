"""Private bounded control protocol, with optional real local UNIX socket."""
import importlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace

try:
    control = importlib.import_module('audio_control')
except ModuleNotFoundError:
    control = SimpleNamespace()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(control, 'AudioControl'), 'audio control protocol missing')
        class Audio:
            active = music_mode = failed = False
            def start(self): self.active = True
            def stop(self, play=False, announce=False): self.active = self.music_mode = False
            def set_music(self, enabled): self.music_mode = enabled
        self.audio = Audio()
        self.feel = SimpleNamespace(snapshot=lambda: {'bpm': None, 'confidence': 0, 'feel': 'unknown'})
        self.hub = control.AudioControl(self.audio, self.feel)

    def test_starts_off_and_only_reports_bounded_public_state(self):
        status = self.hub.request({'method': 'status'})
        self.assertEqual(set(status['status']),
                         {'listening', 'music', 'rhythm', 'error', 'runtime_root', 'voice_ready', 'last'})
        self.assertFalse(status['status']['listening'])
        self.assertFalse(status['status']['voice_ready'])
        # 还没听过任何话：last 是空的，而不是一个空壳对象
        self.assertIsNone(status['status']['last'])

    def test_last_command_is_reported_from_voice_without_transcript(self):
        class Voice:
            ready = SimpleNamespace(is_set=lambda: True)
            recognizer = object()
            def last_view(self): return {'command': 'stand', 'accepted': True, 'age': 0.4, 'seq': 3}
        self.audio.voice = Voice()
        status = self.hub.status()
        self.assertEqual(status['last'], {'command': 'stand', 'accepted': True, 'age': 0.4, 'seq': 3})
        self.assertNotIn('text', status['last'])          # 原话不外泄

    def test_stale_last_command_drops_out(self):
        class Voice:
            ready = SimpleNamespace(is_set=lambda: True)
            recognizer = object()
            def last_view(self): return {'command': 'sit', 'accepted': True, 'age': 31, 'seq': 1}
        self.audio.voice = Voice()
        self.assertIsNone(self.hub.status()['last'])

    def test_voice_health_requires_loaded_recognizer(self):
        self.audio.voice=SimpleNamespace(ready=SimpleNamespace(is_set=lambda:True),recognizer=None)
        self.assertFalse(self.hub.status()['voice_ready'])
        self.audio.voice.recognizer=object()
        self.assertTrue(self.hub.status()['voice_ready'])

    def test_music_enables_listening_off_cancels_both(self):
        status = self.hub.request({'method': 'set', 'music': True})['status']
        self.assertTrue(status['music'] and status['listening'])
        self.hub.request({'method': 'set', 'music': False})
        self.assertTrue(self.audio.active)
        self.hub.request({'method': 'set', 'listening': False})
        self.assertFalse(self.audio.active or self.audio.music_mode)

    def test_reads_actual_active_state_after_physical_toggle(self):
        self.hub.request({'method': 'set', 'listening': True})
        self.audio.stop()
        self.assertFalse(self.hub.request({'method': 'status'})['status']['listening'])

    def test_rejects_commands_wrong_types_unknown_keys_and_contradiction(self):
        for request in [[], {'method': 'robot.sound'}, {'method': 'status', 'pcm': 'abc'},
                        {'method': 'set'}, {'method': 'set', 'listening': 1},
                        {'method': 'set', 'music': 'yes'},
                        {'method': 'set', 'listening': False, 'music': True},
                        {'method': 'set', 'path': '/tmp/recording'}]:
            self.assertIs(self.hub.request(request)['ok'], False, request)
        self.assertFalse(self.audio.active)

    def test_wire_rejects_invalid_json_oversize_and_multiple_requests(self):
        self.assertTrue(hasattr(control, 'decode_request'), 'bounded JSON reader missing')
        for data in [b'{bad}\n', b'x' * 8193, b'{}\n{}\n', b'{}', b'\xff\n']:
            with self.assertRaises(ValueError):
                control.decode_request(data)

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'requires local UNIX sockets')
    def test_socket_one_request_mode_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'control.sock')
            server = control.AudioControlServer(self.hub, path)
            server.start()
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(path)
                    client.sendall(b'{"method":"set","music":true}\n')
                    answer = json.loads(client.recv(8192))
                self.assertTrue(answer['ok'])
                self.assertTrue(answer['status']['music'])
                if os.name != 'nt':
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            finally:
                server.close()
            self.assertFalse(Path(path).exists())


if __name__ == '__main__':
    unittest.main()
