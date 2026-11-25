#!/usr/bin/env python3
"""Click R3 to toggle continuous speech; --trigger-mode hold retains hold/release."""
PADD_MAPPING_CONTRACT = 1

import argparse
import array
import glob
from itertools import islice
import json
import logging
import math
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

LOG = logging.getLogger('duck-echo')
RATE = 48000
FRAME_BYTES = 4
BUTTON = 318  # Linux BTN_THUMBR (right stick click)
INPUT_EVENT = struct.Struct('@qqHHi')  # 64-bit Linux timeval, type, code, value


def native_sound_durations(bank='/var/lib/robot/sounds'):
    """Read only a bounded set of installed native WAV headers, once at startup."""
    durations = {'chirp': .45, 'coo': 1.8, 'greet': 1.5, 'inquire': .85}
    for tag in durations:
        found = []
        try:
            with os.scandir(os.path.join(bank, tag)) as entries:
                for entry in islice(entries, 64):
                    if not entry.name.endswith('.wav') or not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        if entry.stat().st_size > 8 * 1024 * 1024:
                            continue
                        with wave.open(entry.path, 'rb') as sound:
                            duration = sound.getnframes() / sound.getframerate()
                        if 0 < duration <= 5:
                            found.append(duration)
                    except (OSError, EOFError, ValueError, wave.Error):
                        continue
        except OSError:
            continue
        if found:
            durations[tag] = max(found)
    return durations


def prepare_audio(raw, channel, center=False):
    if channel not in (0, 1):
        raise ValueError('microphone channel must be 0 or 1')
    samples = array.array('h')
    samples.frombytes(raw[:len(raw) // FRAME_BYTES * FRAME_BYTES])
    if sys.byteorder != 'little':
        samples.byteswap()
    mono = samples[channel::2]
    dc = sum(mono) / max(1, len(mono))
    if center:
        mono = array.array('h', (max(-32768, min(32767, round(value - dc))) for value in mono))
    peak = max((abs(x) for x in mono), default=0)
    rms = math.sqrt(sum(x * x for x in mono) / max(1, len(mono)))
    stereo = array.array('h', (x for sample in mono for x in (sample, sample)))
    if sys.byteorder != 'little':
        stereo.byteswap()
    return stereo.tobytes(), {
        'frames': len(mono), 'seconds': round(len(mono) / RATE, 3),
        'peak': peak, 'rms': round(rms, 3), 'dc_offset': round(dc, 3),
        'dbfs': round(20 * math.log10(max(rms, 1e-6) / 32768), 1),
    }


def decode_events(raw):
    if len(raw) % INPUT_EVENT.size:
        raise ValueError('incomplete evdev event')
    return [(kind, code, value) for _, _, kind, code, value in INPUT_EVENT.iter_unpack(raw)]


class HoldButton:
    def __init__(self, audio, initially_down=False, repeat_on_limit=True):
        self.audio = audio
        self.down = initially_down
        self.recording = False
        self.since = 0.0
        self.repeat_on_limit = repeat_on_limit

    def event(self, value, now=None):
        now = time.monotonic() if now is None else now
        if value == 1 and not self.down:
            self.down = True
            self.since = now
            self.audio.start()
            self.recording = True
        elif value == 0:
            self.down = False
            if self.recording:
                self.recording = False
                self.audio.stop(play=now - self.since >= 0.25)

    def tick(self, now=None):
        if self.recording and self.audio.expired():
            self.recording = False
            self.audio.stop(play=self.repeat_on_limit)

    def cancel(self):
        cancel_processing = getattr(self.audio, 'cancel_processing', None)
        if cancel_processing is not None:
            cancel_processing()
        if self.recording:
            self.recording = False
            self.audio.stop(play=False)


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


class AlsaAudio:
    def __init__(self, capture_device, playback_device, channel=1, pitch=1.25, maximum=10):
        self.capture_device = capture_device
        self.playback_device = playback_device
        self.channel, self.pitch, self.maximum = channel, pitch, maximum
        self.capture = None
        self.player = None
        self.player_thread = None
        self.playback_ok = None
        self.last_result = None
        self.voice = None
        self.playback_lock = threading.RLock()

    def start(self):
        self.cancel_processing()
        self.stop(play=False)
        self.stop_playback()
        errors = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen([
                'arecord', '-q', '-D', self.capture_device, '-t', 'raw',
                '-f', 'S16_LE', '-c', '2', '-r', str(RATE), '-d', str(self.maximum),
            ], stdout=subprocess.PIPE, stderr=errors)
        except BaseException:
            errors.close()
            raise
        chunks = bytearray()

        def read():
            try:
                while True:
                    block = process.stdout.read(4096)
                    if not block:
                        break
                    remaining = self.maximum * RATE * FRAME_BYTES - len(chunks)
                    if remaining > 0:
                        chunks.extend(block[:remaining])
            except OSError:
                LOG.exception('capture stream read failed')

        reader = threading.Thread(target=read, daemon=True)
        self.capture = (process, errors, reader, chunks, time.monotonic())
        reader.start()
        LOG.info('recording: hold R3; release to repeat (maximum %ss)', self.maximum)

    def expired(self):
        if self.capture is None:
            return False
        process, _, _, _, started = self.capture
        return process.poll() is not None or time.monotonic() - started >= self.maximum + 1

    def stop(self, play):
        if not play:
            self.cancel_processing()
        if self.capture is None:
            return None
        process, errors, reader, chunks, _ = self.capture
        self.capture = None
        interrupted = process.poll() is None
        if interrupted:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            terminate(process)
        reader.join(timeout=2)
        if reader.is_alive():
            errors.close()
            raise RuntimeError('capture reader did not stop; discarding clip')
        process.stdout.close()
        errors.seek(0)
        error_text = errors.read().decode(errors='replace').strip()
        errors.close()
        # arecord exits 1 when its normal SIGINT handler finalizes a recording.
        normal_interrupt = interrupted and process.returncode in (-signal.SIGINT, 1) and (
            not error_text or 'signal' in error_text.lower())
        if process.returncode != 0 and not normal_interrupt:
            LOG.error('capture failed (%s): %s', process.returncode, error_text)
            self.last_result = {'ok': False, 'error': error_text or 'arecord failed'}
            return self.last_result
        output, level = prepare_audio(bytes(chunks), self.channel)
        level['signal_detected'] = level['dbfs'] > -65 and level['peak'] > 20
        level['ok'] = level['signal_detected'] and level['seconds'] >= 0.25
        self.last_result = level
        LOG.info('microphone: %s', json.dumps(level))
        if play and level['ok']:
            if self.voice is not None:
                self.voice.submit(output)
            else:
                self.play(output)
        elif play:
            LOG.warning('clip too short or silent; check microphone and mixer routing')
        return level

    def play(self, data, allowed=None):
        with self.playback_lock:
            self._play(data, allowed)

    def _play(self, data, allowed=None):
        self.stop_playback()
        if allowed is not None and not allowed():
            return
        self.playback_ok = None
        errors = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen([
                'aplay', '-q', '-D', self.playback_device, '-t', 'raw', '-f', 'S16_LE',
                '-c', '2', '-r', str(round(RATE * self.pitch)),
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        except BaseException:
            errors.close()
            raise
        self.player = process

        def write():
            try:
                if allowed is not None and not allowed():
                    terminate(process)
                    process.communicate(timeout=1)
                    return
                process.communicate(input=data, timeout=self.maximum / self.pitch + 4)
                self.playback_ok = process.returncode == 0
                if process.returncode not in (0, -signal.SIGTERM):
                    errors.seek(0)
                    LOG.error('playback failed: %s', errors.read().decode(errors='replace').strip())
                elif process.returncode == 0:
                    LOG.info('repeat playback completed (pitch/speed %.2fx)', self.pitch)
            except subprocess.TimeoutExpired:
                self.playback_ok = False
                terminate(process)
                process.communicate()
                LOG.error('playback timeout')
            except (OSError, ValueError):
                self.playback_ok = False
                LOG.exception('playback stream failed')
                terminate(process)
            finally:
                errors.close()

        self.player_thread = threading.Thread(target=write, daemon=True)
        self.player_thread.start()

    def stop_playback(self):
        with self.playback_lock:
            self._stop_playback()

    def _stop_playback(self):
        if self.player is not None:
            terminate(self.player)
            self.player = None
        if self.player_thread is not None:
            self.player_thread.join(timeout=2)
            self.player_thread = None

    def finish_playback(self):
        if self.player_thread:
            self.player_thread.join(timeout=self.maximum / self.pitch + 6)
        self.stop_playback()
        return self.playback_ok is True

    def playback_active(self):
        player, writer = self.player, self.player_thread
        return ((player is not None and player.poll() is None) or
                (writer is not None and writer.is_alive()))

    def close(self):
        self.cancel_processing()
        self.stop(play=False)
        self.stop_playback()

    def cancel_processing(self):
        if self.voice is not None:
            self.voice.cancel()


def key_bitmap(fd, current=False):
    import fcntl
    bits = bytearray(96)
    # EVIOCGKEY or EVIOCGBIT(EV_KEY), with sizeof(bitmap) in the ioctl.
    request = (2 << 30) | (len(bits) << 16) | (ord('E') << 8) | (0x18 if current else 0x21)
    fcntl.ioctl(fd, request, bits, True)
    return bits


def right_pressed(fd):
    bits = key_bitmap(fd, current=True)
    return bool(bits[BUTTON // 8] & (1 << (BUTTON % 8)))


def devices(node=None):
    result = []
    for path in sorted(glob.glob('/dev/input/event*')):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                bits = key_bitmap(fd)
                if (node is not None and path==node) or (node is None and bits[BUTTON // 8] & (1 << (BUTTON % 8))):
                    name_path = '/sys/class/input/' + os.path.basename(path) + '/device/name'
                    with open(name_path, encoding='utf-8') as source:
                        name = source.read().strip()
                    result.append({'path': path, 'name': name})
            finally:
                os.close(fd)
        except (OSError, ValueError):
            continue
    return result


def make_button(audio, initially_down=False, trigger_mode='toggle'):
    if trigger_mode == 'hold':
        return HoldButton(audio, initially_down=initially_down, repeat_on_limit=audio.voice is None)
    from continuous_audio import ToggleButton
    return ToggleButton(audio, initially_down=initially_down)


def binding_pressed(fd,binding):
    if binding['kind']=='key':
        bits=key_bitmap(fd,current=True);code=binding['code']
        return code<len(bits)*8 and bool(bits[code//8]&(1<<(code%8)))
    import fcntl
    # struct input_absinfo: value, minimum, maximum, fuzz, flat, resolution.
    values=bytearray(24)
    request=(2<<30)|(len(values)<<16)|(ord('E')<<8)|(0x40+binding['code'])
    fcntl.ioctl(fd,request,values,True)
    return struct.unpack('iiiiii',values)[0]==binding['value']


class MappedVoiceInput:
    """Rebase raw bindings and held edges whenever native ownership changes."""
    def __init__(self,audio,current,trigger_mode='toggle'):
        self.audio,self.current,self.trigger_mode=audio,current,trigger_mode
        self.state=None;self.button=None;self.select_down=False

    def cancel(self):
        if self.button:self.button.cancel()
        self.audio.cancel_processing();self.audio.stop(play=False);self.audio.stop_playback()

    def update(self,state,force=False):
        if self.state==state and not force:return
        self.cancel();self.state=state
        bindings=state['bindings'];r3=bindings.get('r3');select_binding=bindings.get('select')
        self.button=make_button(self.audio,initially_down=self.current(r3) if r3 else True,
                                trigger_mode=self.trigger_mode)
        self.select_down=self.current(select_binding) if select_binding else False

    def event(self,kind,code,value):
        if not self.state or self.state['blocked']:return
        from pad_mapping import binding_event
        bindings=self.state['bindings']
        pressed=binding_event(bindings.get('select'),kind,code,value)
        if pressed is not None:
            if pressed and not self.select_down:self.cancel()
            self.select_down=pressed
        pressed=binding_event(bindings.get('r3'),kind,code,value)
        if pressed is not None:self.button.event(int(pressed))

    def tick(self):
        if self.state and not self.state['blocked']:self.button.tick()


def daemon(audio, stopped, selected=None, trigger_mode='toggle'):
    from pad_mapping import PAD_MAPPING
    announced = False
    while not stopped.is_set():
        mapping=PAD_MAPPING.read()
        available = devices(mapping['node']) if mapping.get('node') or mapping.get('legacy') else []
        if selected:
            available = [item for item in available if item['path'] == selected]
        if not available:
            if not announced:
                LOG.info('waiting for a gamepad with R3; existing padd keeps control')
                announced = True
            stopped.wait(1)
            continue
        announced = False
        item = available[0]
        fd = None
        button = None
        try:
            fd = os.open(item['path'], os.O_RDONLY | os.O_NONBLOCK)
            button = MappedVoiceInput(audio,lambda binding:binding_pressed(fd,binding),trigger_mode)
            button.update(mapping)
            LOG.info('listening without grab: %s (%s)', item['name'], item['path'])
            resync = False
            while not stopped.is_set():
                current_mapping=PAD_MAPPING.read()
                if not current_mapping.get('legacy') and current_mapping.get('node')!=item['path']:break
                button.update(current_mapping)
                readable, _, _ = select.select([fd], [], [], 0.1)
                if readable:
                    raw = os.read(fd, INPUT_EVENT.size * 64)
                    if not raw:
                        raise OSError('gamepad disconnected')
                    for kind, code, value in decode_events(raw):
                        if kind == 0 and code == 3:  # SYN_DROPPED: discard until SYN_REPORT
                            button.cancel()
                            resync = True
                        elif resync:
                            if kind == 0 and code == 0:
                                button.update(PAD_MAPPING.read(),force=True)
                                resync = False
                        else:
                            button.event(kind,code,value)
                if not resync:button.tick()
        except (OSError, ValueError, RuntimeError):
            LOG.exception('input/audio unavailable; retrying')
        finally:
            if button:
                button.cancel()
            audio.stop(play=False)
            audio.stop_playback()
            if fd is not None:
                os.close(fd)
        stopped.wait(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['daemon', 'devices', 'probe', 'record'])
    parser.add_argument('--capture-device', default='plughw:CARD=aic3104,DEV=0')
    parser.add_argument('--playback-device', default='plughw:CARD=aic3104,DEV=0')
    parser.add_argument('--channel', type=int, choices=[0, 1], default=1)
    parser.add_argument('--pitch', type=float, default=1.25, help='pitch AND speed ratio; 1.0 = original')
    parser.add_argument('--maximum', type=int, default=10)
    parser.add_argument('--seconds', type=float, default=4)
    parser.add_argument('--input', help='optional fixed /dev/input/eventN')
    parser.add_argument('--trigger-mode', choices=['toggle', 'hold'], default='toggle')
    parser.add_argument('--voice-mode', choices=['off', 'shadow', 'actions'], default='off')
    parser.add_argument('--reply-mode', choices=['duck', 'echo'], default='duck',
                        help='daemon ordinary speech: native duck interaction or explicit PCM replay')
    parser.add_argument('--voice-model', default='/opt/duck-echo/models/vosk-model-small-cn-0.22')
    parser.add_argument('--speech-deps', default='/opt/duck-echo/asr-deps')
    parser.add_argument('--control-socket', default='/run/duck-echo/control.sock')
    args = parser.parse_args(argv)
    if not 0.75 <= args.pitch <= 1.6:
        parser.error('--pitch must be between 0.75 and 1.6')
    if not 1 <= args.maximum <= 10 or not 0.25 <= args.seconds <= args.maximum:
        parser.error('require 1 <= maximum <= 10 and 0.25 <= seconds <= maximum')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if not sys.platform.startswith('linux'):
        parser.error('runtime requires 64-bit Linux on the duck; unit tests also run on Windows')
    if args.action == 'devices':
        print(json.dumps(devices(), ensure_ascii=False, indent=2))
        return 0
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    audio = AlsaAudio(args.capture_device, args.playback_device, args.channel, args.pitch, args.maximum)
    routes, route_error = None, None
    if args.action == 'daemon':
        from install import MicrophoneRoutes
        routes = MicrophoneRoutes.for_capture(args.capture_device, args.channel)
        if routes is not None:
            try:
                routes.restore()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                route_error = 'microphone: ' + str(error)
                LOG.warning('could not restore microphone route: %s', error)
    toggle_daemon = args.action == 'daemon' and args.trigger_mode == 'toggle'
    if toggle_daemon:
        from continuous_audio import ContinuousAudio
        audio = ContinuousAudio(audio)
    if args.action == 'daemon' and (args.voice_mode != 'off' or toggle_daemon or args.reply_mode == 'duck'):
        from speech_control import MandarinRecognizer, VoiceControl
        from voice_commands import PostureCommands, RobotClient
        robot = RobotClient()
        sound_lengths = native_sound_durations()
        def cue(accepted):
            robot.call('robot.sound', {'tag': 'chirp' if accepted else 'inquire'})
        if args.trigger_mode == 'toggle':
            raw_cue = cue
            cue = lambda accepted: audio.command_cue(
                raw_cue, accepted, duration=sound_lengths['chirp' if accepted else 'inquire']+.1)
        audio.voice = VoiceControl(
            lambda: MandarinRecognizer(args.voice_model, args.speech_deps),
            PostureCommands(robot), audio.play if args.reply_mode == 'echo' else lambda pcm: None,
            execute_commands=args.voice_mode == 'actions', cue=cue,
            recognize_speech=args.voice_mode != 'off')
    control_server = None
    try:
        if toggle_daemon:
            from audio_control import AudioControl, AudioControlServer
            from music_feel import MusicFeel
            from music_interaction import MusicInteraction
            feel = MusicFeel(channel=args.channel)
            audio.activation_duration = sound_lengths['chirp']+.1
            audio.activation_cue = lambda allowed: robot.call('robot.sound', {'tag': 'chirp'}) if allowed() else None
            audio.deactivation_cue = lambda allowed: robot.call('robot.sound', {'tag': 'coo'}) if allowed() else None
            def reply(tag, allowed):
                if allowed():
                    audio.command_cue(lambda _: robot.call('robot.sound', {'tag': tag}) if allowed() else None,
                                      True, duration=sound_lengths.get(tag, 1)+.1, allowed=allowed)
            audio.music = MusicInteraction(lambda: audio.voice.recognizer, feel, reply, channel=args.channel)
            def observe(raw, timestamp):
                if audio.music_mode:
                    feel.feed(raw, timestamp)
                    audio.music.feed(raw, timestamp)
            audio.observer = observe
            control_server = AudioControlServer(AudioControl(audio, feel, routes, route_error), args.control_socket)
            control_server.start()
        if args.action == 'daemon':
            daemon(audio, stopped, args.input, trigger_mode=args.trigger_mode)
            return 0
        audio.start()
        until = time.monotonic() + args.seconds
        while time.monotonic() < until and not stopped.is_set() and not audio.expired():
            stopped.wait(0.05)
        result = audio.stop(play=args.action == 'record' and not stopped.is_set())
        played = audio.finish_playback()
        if args.action == 'record' and result is not None:
            result['playback_ok'] = played
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result and result['ok'] and (args.action != 'record' or played) else 2
    finally:
        if control_server is not None:
            control_server.close()
        audio.close()
        if toggle_daemon and audio.music is not None:
            audio.music.close()
        if audio.voice is not None:
            audio.voice.close()


if __name__ == '__main__':
    sys.exit(main())
