"""Play the questioning call through ALSA; mouth motion is an optional callback."""
import random
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from .pad_combo import Refused
from .mouth_sync import FRAME_S, openings

CONFIG = Path('/etc/robot/robotd.toml')
DEFAULT_BANK = '/var/lib/robot/sounds'
DEFAULT_DEVICE = 'plughw:aic3104'
_playing = threading.Lock()


def _audio(config):
    try:
        with Path(config).open('rb') as source:
            audio = tomllib.load(source).get('audio', {})
    except (OSError, tomllib.TOMLDecodeError):
        raise Refused('鸭哥的声音设置暂时读不到') from None
    if audio.get('enabled', True) is False:
        raise Refused('鸭哥的声音已关闭')
    return audio


def play_inquire(config=CONFIG, runner=subprocess.run):
    audio = _audio(config)
    wav = Path(audio.get('bank', DEFAULT_BANK)) / 'inquire' / 'inquire_a.wav'
    if not wav.is_file():
        raise Refused('鸭哥还没有准备好叫声')
    if not _playing.acquire(blocking=False):
        raise Refused('鸭哥正在叫，请稍等一下')
    try:
        try:
            result = runner(['aplay', '-q', '-D', audio.get('device', DEFAULT_DEVICE), str(wav)],
                            capture_output=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            raise Refused('鸭哥暂时发不出声音，请检查扬声器') from None
        if result.returncode != 0:
            raise Refused('鸭哥暂时发不出声音，请检查扬声器')
    finally:
        _playing.release()
    return {'accepted': True, 'speaker_only': True}


def play_inquire_with_mouth(notify, config=CONFIG, popen=subprocess.Popen,
                           clock=time.monotonic, sleep=time.sleep, choose=random.choice):
    """Play one WAV and send its 40 Hz mouth curve; always close after opening.

    ``notify`` returns false when the real motor is no longer safe to command.
    The sound continues in that case, with no more mouth openings.
    """
    audio = _audio(config)
    files = sorted((Path(audio.get('bank', DEFAULT_BANK)) / 'inquire').glob('*.wav'))
    if not files:
        raise Refused('鸭哥还没有准备好叫声')
    wav = choose(files)
    try:
        curve = openings(wav)
    except (OSError, ValueError, EOFError):
        return play_inquire(config)
    if not _playing.acquire(blocking=False):
        raise Refused('鸭哥正在叫，请稍等一下')
    child = None
    opened = False
    try:
        try:
            child = popen(['aplay', '-q', '-D', audio.get('device', DEFAULT_DEVICE), str(wav)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
            started = clock() + 0.025  # ALSA needs a short startup buffer.
            for index, level in enumerate(curve):
                delay = started + index * FRAME_S - clock()
                if delay > 0:
                    sleep(delay)
                if child.poll() is not None:
                    break
                if level > 0:
                    if not notify(level):
                        break
                    opened = True
            try:
                result = child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
                raise Refused('鸭哥暂时发不出声音，请检查扬声器') from None
            if result != 0:
                raise Refused('鸭哥暂时发不出声音，请检查扬声器')
        except OSError:
            raise Refused('鸭哥暂时发不出声音，请检查扬声器') from None
    finally:
        try:
            if opened:
                notify(0.0)
        finally:
            _playing.release()
    return {'accepted': True, 'speaker_only': not opened, 'mouth_synced': opened,
            'sound_file': wav.name}
