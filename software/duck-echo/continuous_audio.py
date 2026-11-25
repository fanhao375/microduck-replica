"""One continuous PCM source, bounded utterances, and cancellable echo dispatch."""
import logging
from collections import deque
import subprocess
import tempfile
import threading
import time
from voice_commands import updates_allowed,motion_lease,UpdateLocked

LOG = logging.getLogger('duck-echo')
RATE, FRAME_BYTES = 48000, 4


class ToggleButton:
    def __init__(self, audio, initially_down=False):
        self.audio, self.down = audio, initially_down
        self._recording = False

    @property
    def recording(self):
        return getattr(self.audio, 'active', self._recording)

    def event(self, value, now=None):
        if value == 0:
            self.down = False
        elif value == 1 and not self.down:
            self.down = True
            if self.recording:
                self.cancel(announce=True)
            else:
                self.audio.start()
                self._recording = True

    def tick(self, now=None):
        if self.recording and self.audio.expired():
            self.cancel()

    def cancel(self, announce=False):
        self._recording = False
        if announce:
            self.audio.stop(play=False, announce=True)
        else:
            self.audio.stop(play=False)


class PcmSegmenter:
    """DC-free 20 ms windows, ambient contrast, and bounded speech pre-roll."""
    def __init__(self, channel=1, maximum=10, silence=.6, minimum=.25, threshold=-42):
        from duck_echo import prepare_audio
        if channel not in (0, 1) or not .25 <= maximum <= 10:
            raise ValueError('invalid microphone channel or phrase maximum')
        self.prepare = prepare_audio
        self.channel = channel
        self.window_bytes = round(RATE * .02) * FRAME_BYTES
        self.maximum_bytes = round(maximum * RATE) * FRAME_BYTES
        self.minimum_bytes = round(minimum * RATE) * FRAME_BYTES
        self.silence_bytes = round(silence * RATE) * FRAME_BYTES
        self.threshold = threshold
        self.minimum_noise = 32768 * 10 ** (threshold / 20)
        self.noise_floor = self.minimum_noise
        self.pre_roll_windows = 6  # 120 ms preserves the start of an utterance.
        self.reset()

    def reset(self):
        self.level_dbfs = None
        self.partial = bytearray()
        self.phrase = bytearray()
        self.quiet = 0
        self.voiced = 0
        self.pre_roll = deque(maxlen=self.pre_roll_windows)
        self.startup_bytes = round(.06 * RATE) * FRAME_BYTES

    @property
    def buffered_bytes(self):
        return len(self.partial) + len(self.phrase) + sum(map(len, self.pre_roll))

    def feed(self, data, detect_speech=True):
        phrases = []
        # Never retain an unbounded source chunk, even with a non-ALSA source.
        for offset in range(0, len(data), self.window_bytes):
            self.partial.extend(data[offset:offset + self.window_bytes])
            while len(self.partial) >= self.window_bytes:
                raw = bytes(self.partial[:self.window_bytes])
                del self.partial[:self.window_bytes]
                selected, level = self.prepare(raw, self.channel, center=True)
                # Discard the short analog settling transient after capture or
                # output suppression. Do not put that transient in pre-roll.
                if self.startup_bytes > 0:
                    self.startup_bytes -= len(selected)
                    continue
                self.level_dbfs = max(-96., min(0., level['dbfs']))
                if not detect_speech:
                    continue
                voiced = level['rms'] >= self.noise_floor * 2.5
                if not self.phrase and not voiced:
                    # Only non-speech trains the environment estimate. Keep a
                    # conservative initial floor so quiet/DC windows cannot
                    # make the measured microphone hiss look like speech.
                    self.noise_floor = max(self.minimum_noise,
                                           .95 * self.noise_floor + .05 * level['rms'])
                    self.pre_roll.append(selected)
                    continue
                if not self.phrase:
                    self.phrase.extend(b''.join(self.pre_roll))
                    self.pre_roll.clear()
                self.phrase.extend(selected)
                self.voiced += len(selected) if voiced else 0
                self.quiet = 0 if voiced else self.quiet + len(selected)
                if len(self.phrase) >= self.maximum_bytes or self.quiet >= self.silence_bytes:
                    if self.voiced >= self.minimum_bytes:
                        phrases.append(bytes(self.phrase[:self.maximum_bytes]))
                        LOG.info('speech phrase %.2fs endpoint=%s ambient_ac_rms=%.0f',
                                 min(len(self.phrase), self.maximum_bytes) / RATE / FRAME_BYTES,
                                 'silence' if self.quiet >= self.silence_bytes else 'maximum',
                                 self.noise_floor)
                    if self.quiet >= self.silence_bytes:
                        tail = self.phrase[-self.pre_roll_windows * self.window_bytes:]
                        self.pre_roll.extend(bytes(tail[offset:offset + self.window_bytes])
                                             for offset in range(0, len(tail), self.window_bytes))
                    self.phrase.clear()
                    self.quiet = self.voiced = 0
        return phrases


class ContinuousAudio:
    """Capture never waits for ASR/playback, and input cancellation never joins workers.

    Only the capture thread owns arecord, so rapid off/on cannot open two capture
    streams. The dispatch thread keeps one pending phrase or playback request.
    Generation changes invalidate work across stop, EOF, and reconnect.
    """
    def __init__(self, backend, capture_device=None, channel=None, maximum=None,
                 silence=.6, minimum=.25, tail=.35, clock=time.monotonic, observer=None,
                 threshold=-42,guard=updates_allowed,lease=motion_lease):
        self.backend = backend
        self.guard,self.lease=guard,lease
        self.capture_device = capture_device or backend.capture_device
        self.channel = backend.channel if channel is None else channel
        self.maximum = backend.maximum if maximum is None else maximum
        self.segmenter = PcmSegmenter(self.channel, self.maximum, silence, minimum, threshold)
        self.tail, self.clock = tail, clock
        self.observer = observer
        self.voice = None
        self.music = None
        self.music_mode = False
        self.switching_source = False
        self.level_at = None
        self.activation_cue = None
        self.deactivation_cue = None
        self.deactivation_pending = None
        self.activation_duration = .45
        self.activation_pending = self.activation_inflight = None
        self.condition = threading.Condition()
        # Never acquire this while holding condition: VoiceControl._echo holds
        # its own condition while calling our nonblocking play() enqueue.
        self.dispatch_lock = threading.Lock()
        self.generation = 0
        self.mode_generation = 0
        self.active = self.closed = self.failed = False
        self.pending = None
        self.stop_output = False
        self.output_pending = False
        self.cue_active = False
        self.suppress_until = 0
        self.capture = None
        self.capture_stop = None
        self.capture_thread = threading.Thread(target=self._capture_loop, name='continuous-capture', daemon=True)
        self.worker = threading.Thread(target=self._dispatch, name='continuous-dispatch', daemon=True)
        self.capture_thread.start()
        self.worker.start()

    def start(self):
        if not self.guard():self.stop(play=False);return
        try:
            with self.lease():
                if not self.guard():self.stop(play=False);return
                self._start()
        except UpdateLocked:self.stop(play=False)

    def _start(self):
        # Keep capture/play inactive until both consumers have invalidated old
        # work. Otherwise an old VoiceControl._echo can enqueue into this session.
        with self.dispatch_lock:
            with self.condition:
                if self.closed or self.active:
                    return
                generation = self._invalidate()
                capture_stop = self.capture_stop
            if self.voice is not None:
                self.voice.cancel()
            if self.music is not None:
                self.music.set_enabled(False)
            with self.condition:
                if self.closed or generation != self.generation:
                    return
                self.active, self.failed = True, False
                self.stop_output = True
                if self.activation_cue is not None:
                    self.activation_pending = generation
                self.condition.notify_all()
        if capture_stop is not None:
            capture_stop.set()
        LOG.info('continuous listening ON; click R3 again to stop')

    def expired(self):
        with self.condition:
            return self.failed

    @property
    def microphone_level_dbfs(self):
        with self.condition:
            if self.active and self.level_at is not None and self.clock() - self.level_at <= .6:
                return self.segmenter.level_dbfs
            return None

    def change_microphone(self, routes, source):
        # Invalidate everything made from the previous route before it changes.
        # A new arecord instance discards old samples buffered in ALSA/the pipe.
        with self.dispatch_lock:
            with self.condition:
                self.switching_source = True
                self._invalidate()
                self.segmenter.noise_floor = self.segmenter.minimum_noise
                if self.capture_stop is not None:
                    self.capture_stop.set()
                self.stop_output = True
                self.condition.notify_all()
            try:
                if self.voice is not None:
                    self.voice.cancel()
                if self.music is not None:
                    self.music.cancel()
                routes.select(source)
            except Exception:
                if routes.source is None:
                    with self.condition:
                        self.active = self.music_mode = False
                    if self.music is not None:
                        self.music.set_enabled(False)
                raise
            finally:
                with self.condition:
                    self.switching_source = False
                    self.condition.notify_all()

    def cancel_processing(self):
        with self.condition:
            generation = self._invalidate()
        self._cancel_voice(generation)
        if self.music is not None:
            self.music.cancel()

    def set_music(self, enabled):
        if enabled and not self.guard():self.stop(play=False);return
        # Changing consumers must not invalidate the generation owned by the
        # still-running capture process. Each recognizer cancels its own work.
        with self.dispatch_lock:
            with self.condition:
                self.music_mode = bool(enabled) and self.active and not self.closed
                self.mode_generation += 1
                self.pending = None
                self.output_pending = False
                self.segmenter.reset()
                self.stop_output = True
                self.condition.notify_all()
            if self.voice is not None:
                self.voice.cancel()
            if self.music is not None:
                self.music.set_enabled(self.music_mode)

    def _invalidate(self):
        self.generation += 1
        self.level_at = None
        self.pending = None
        self.activation_pending = self.activation_inflight = None
        self.deactivation_pending = None
        self.output_pending = False
        self.segmenter.reset()
        return self.generation

    def _valid(self, generation, mode_generation=None):
        with self.condition:
            return (self.guard() and self.active and generation == self.generation and not self.closed and
                    not self.music_mode and (mode_generation is None or mode_generation == self.mode_generation))

    def _cancel_voice(self, generation, stop_music=False):
        with self.dispatch_lock:
            with self.condition:
                current = generation == self.generation
            if current and self.voice is not None:
                self.voice.cancel()
            if current and stop_music and not self.music_mode and self.music is not None:
                self.music.set_enabled(False)

    def stop(self, play=False, expected_generation=None, announce=False):
        with self.condition:
            if expected_generation is not None and expected_generation != self.generation:
                return
            if announce and not self.active:
                return
            was_active = self.active
            self.active = False
            self.music_mode = False
            generation = self._invalidate()
            capture_stop = self.capture_stop
            self.stop_output = True
            self.condition.notify_all()
        if was_active:
            LOG.info('continuous listening OFF; pending speech and playback canceled')
        self._cancel_voice(generation, stop_music=True)
        if capture_stop is not None:
            capture_stop.set()
        with self.condition:
            if (announce and was_active and self.deactivation_cue is not None and
                    not self.active and not self.closed and generation == self.generation):
                self.deactivation_pending = generation
                self.condition.notify_all()

    def stop_playback(self):
        with self.condition:
            self.stop_output = True
            self.condition.notify_all()

    def _suppressed(self):
        if self.activation_pending is not None or self.activation_inflight is not None:
            return True
        playing = self.backend.playback_active()
        if playing or self.output_pending or self.cue_active:
            self.suppress_until = self.clock() + self.tail
            return True
        return self.clock() < self.suppress_until

    def suppressed(self):
        with self.condition:
            return self._suppressed()

    def feed(self, data, generation=None):
        """Feed captured PCM; this is also the future single-source analysis hook."""
        if not self.guard():self.stop(play=False);return
        with self.condition:
            if not self.active or self.switching_source or (generation is not None and generation != self.generation):
                return
            if self._suppressed():
                self.segmenter.reset()
                return
            for phrase in self.segmenter.feed(data, detect_speech=not self.music_mode):
                self.pending = ('phrase', self.generation, phrase)
                self.condition.notify_all()
            observer, timestamp = self.observer, self.clock()
            self.level_at = timestamp
            session, mode = self.generation, self.mode_generation
        # Observers must be quick in-memory consumers. They never run under the
        # capture lock and never receive output-contaminated or inactive input.
        if observer is not None:
            with self.dispatch_lock:
                with self.condition:
                    if not self.active or session != self.generation or mode != self.mode_generation:
                        return
                try:
                    observer(data, timestamp)
                except Exception:
                    LOG.exception('PCM observer failed')

    def play(self, data):
        if not self.guard():return
        with self.condition:
            if self.active and not self.closed and not self.switching_source:
                self.output_pending = True
                self.segmenter.reset()
                self.pending = ('play', self.generation, data)
                self.condition.notify_all()

    def command_cue(self, callback, accepted, duration=1, allowed=None):
        if not self.guard():return
        with self.condition:
            if not self.active or self.closed or (allowed is not None and not allowed()):
                return
            self.cue_active = True
            self.pending = None
            self.output_pending = False
            self.segmenter.reset()
        try:
            if allowed is None or allowed():
                callback(accepted)
        finally:
            with self.condition:
                self.cue_active = False
                # robot.sound acknowledges asynchronously, so reserve enough
                # time for the short chirp itself as well as the acoustic tail.
                self.suppress_until = max(self.suppress_until, self.clock() + duration + self.tail)

    def _activation_valid(self, generation):
        with self.condition:
            return self.guard() and self.active and not self.closed and generation == self.generation

    def _deactivation_valid(self, generation):
        with self.condition:
            return not self.active and not self.closed and generation == self.generation

    def _dispatch(self):
        while True:
            with self.condition:
                while (self.pending is None and self.activation_pending is None and self.deactivation_pending is None and
                       not self.stop_output and not self.closed):
                    self.condition.wait()
                stop = self.stop_output
                self.stop_output = False
                job, self.pending = self.pending, None
                activation, self.activation_pending = self.activation_pending, None
                deactivation, self.deactivation_pending = self.deactivation_pending, None
                if activation is not None:
                    self.activation_inflight = activation
                mode_generation = self.mode_generation
                closed = self.closed
            if stop or closed:
                self.backend.stop_playback()
            if closed:
                self.backend.close()
                return
            if deactivation is not None:
                allowed = lambda generation=deactivation: self._deactivation_valid(generation)
                try:
                    if allowed():
                        self.deactivation_cue(allowed)
                except Exception:
                    LOG.exception('listening deactivation sound unavailable')
                continue
            if activation is not None:
                allowed = lambda generation=activation: self._activation_valid(generation)
                try:
                    self.command_cue(lambda _: self.activation_cue(allowed), True,
                                     duration=self.activation_duration, allowed=allowed)
                except Exception:
                    LOG.exception('listening activation sound unavailable')
                finally:
                    with self.condition:
                        if self.activation_inflight == activation:
                            self.activation_inflight = None
                continue
            if job is None:
                continue
            kind, generation, data = job
            if kind == 'phrase' and self.voice is not None:
                with self.dispatch_lock:
                    with self.condition:
                        valid = self.active and generation == self.generation
                    if valid and self.guard() and not self.music_mode and mode_generation == self.mode_generation:
                        self.voice.submit(data)
                continue
            with self.condition:
                valid = (self.active and generation == self.generation and not self.music_mode
                         and mode_generation == self.mode_generation)
                if kind == 'phrase' and valid:
                    self.output_pending = True
                    self.segmenter.reset()
            if valid:
                try:
                    self.backend.play(data, allowed=lambda generation=generation, mode=mode_generation:
                                      self._valid(generation, mode))
                except Exception:
                    LOG.exception('continuous playback failed')
                finally:
                    with self.condition:
                        if generation == self.generation:
                            self.output_pending = False
                            self.suppress_until = self.clock() + self.tail

    @staticmethod
    def _reap_capture(process, requested):
        requested.wait()
        from duck_echo import terminate
        terminate(process)

    def _capture_loop(self):
        while True:
            with self.condition:
                while (not self.active or self.switching_source) and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                generation = self.generation
            errors = tempfile.TemporaryFile()
            process = None
            reaper = capture_stop = None
            try:
                process = subprocess.Popen([
                    'arecord', '-q', '-D', self.capture_device, '-t', 'raw',
                    '-f', 'S16_LE', '-c', '2', '-r', str(RATE),
                    '--period-time=20000', '--buffer-time=80000',
                ], stdout=subprocess.PIPE, stderr=errors)
                # Exactly one reaper owns this process. Repeated off/cancel
                # events only set its Event; cleanup joins the same owner.
                capture_stop = threading.Event()
                reaper = threading.Thread(target=self._reap_capture, args=(process, capture_stop),
                                          name='continuous-reaper', daemon=True)
                reaper.start()
                with self.condition:
                    self.capture = process
                    self.capture_stop = capture_stop
                    valid = self.active and generation == self.generation
                if valid:
                    while True:
                        data = process.stdout.read(self.segmenter.window_bytes)
                        if not data:
                            break
                        self.feed(data, generation)
                        with self.condition:
                            if not self.active or generation != self.generation:
                                break
            except (OSError, ValueError):
                LOG.exception('continuous capture unavailable')
            finally:
                if process is not None:
                    # Also handles stop arriving during Popen, before the
                    # process and its stop Event were visible to evdev.
                    capture_stop.set()
                    reaper.join()
                    process.stdout.close()
                errors.seek(0)
                message = errors.read().decode(errors='replace').strip()
                errors.close()
                with self.condition:
                    if self.capture is process:
                        self.capture = None
                        self.capture_stop = None
                    failed = self.active and generation == self.generation
                    if failed:
                        self.failed = True
                if failed:
                    LOG.warning('continuous capture ended; press R3 after release to retry: %s', message)
                    self.stop(play=False, expected_generation=generation)

    def close(self):
        self.stop(play=False)
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.capture_thread.join(timeout=2)
        self.worker.join(timeout=2)
