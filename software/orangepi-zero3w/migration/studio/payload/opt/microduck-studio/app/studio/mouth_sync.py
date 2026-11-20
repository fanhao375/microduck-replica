"""A short, bounded mouth curve from the exact WAV being played."""
from array import array
import math
import sys
import wave

FRAME_S = 0.025
MAX_OPEN = 0.65


def _pitch(samples, rate):
    # Downsample to about 6 kHz; these duck calls have a clear 140–300 Hz note.
    stride = max(1, round(rate / 6000))
    values = samples[::stride]
    mean = sum(values) / len(values)
    values = [sample - mean for sample in values]
    energy = sum(value * value for value in values)
    if energy == 0:
        return None
    sampled_rate = rate / stride
    best = (0.0, 0)
    for lag in range(max(2, int(sampled_rate / 350)),
                     min(len(values) // 2, int(sampled_rate / 100)) + 1):
        score = sum(values[i] * values[i + lag]
                    for i in range(len(values) - lag)) / energy
        if score > best[0]:
            best = (score, lag)
    return sampled_rate / best[1] if best[0] >= 0.45 else None


def openings(wav_path):
    """Return 40 Hz targets in [0, 0.65], ending with a closed mouth."""
    with wave.open(str(wav_path), 'rb') as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError('疑问叫声格式不支持嘴部同步')
        rate = source.getframerate()
        if not 8000 <= rate <= 96000 or source.getnframes() > rate * 2:
            raise ValueError('疑问叫声长度或采样率异常')
        samples = array('h', source.readframes(source.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    frame_size = max(1, round(rate * FRAME_S))
    frames = [samples[start:start + frame_size]
              for start in range(0, len(samples), frame_size)]
    if not frames:
        raise ValueError('疑问叫声是空的')
    rms = [math.sqrt(sum(value * value for value in frame) / len(frame))
           for frame in frames]
    peak = max(rms)
    if peak < 1:
        return [0.0] * (len(frames) + 1)
    targets = []
    tone = 0.3
    previous = 0.0
    for frame, level in zip(frames, rms):
        pitch = _pitch(frame, rate)
        if pitch is not None:
            tone = min(1.0, max(0.0, (pitch - 140) / 160))
        loudness = level / peak
        target = 0.0 if loudness < 0.03 else (0.28 + 0.40 * tone) * loudness ** 0.7
        # The motor should follow the note, not jump between neighboring frames.
        previous += max(-0.12, min(0.12, target - previous))
        targets.append(round(min(MAX_OPEN, max(0.0, previous)), 3))
    return targets + [0.0]
