"""Decode a bounded browser microphone clip with the duck's installed Vosk model."""
import base64
import binascii
import json
import sys
import threading
from pathlib import Path

from .pad_combo import Refused
from .voice_words import RECOGNITION_PHRASES

RATE = 16000
MAX_PCM_BYTES = RATE * 2 * 12
MIN_PCM_BYTES = RATE * 2 // 10
MODEL_PATH = Path('/opt/microduck/resources/models/vosk-model-small-cn-0.22')
DEPS_PATH = Path('/opt/microduck/resources/asr-deps')
_model = None
_decoder_type = None
_model_lock = threading.Lock()
_recognizers = {}
_recognition_lock = threading.Lock()
WAKE_GRAMMAR = json.dumps(['鸭 哥', '雅 阁', '雅 各', '亚 哥', '压 哥', '[unk]'], ensure_ascii=False)


def _tokens(phrase, model):
    """Use the installed dictionary's longest words; never drop unknown text."""
    words = []
    while phrase:
        for end in range(len(phrase), 0, -1):
            word = phrase[:end]
            if model.vosk_model_find_word(word) >= 0:
                words.append(word)
                phrase = phrase[end:]
                break
        else:
            raise Refused('鸭哥的语音词表暂时未准备好')
    return ' '.join(words)


def _grammar(model):
    # Include denials, filler and unknown speech, not just positive commands.
    # The command parser refuses [unk], negation and conflicting actions.
    return json.dumps([_tokens(phrase, model) for phrase in RECOGNITION_PHRASES] + ['[unk]'], ensure_ascii=False)


def decode_pcm(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_PCM_BYTES + 2) // 3) * 4:
        raise Refused('录音太长，请按住说一句简短口令')
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise Refused('录音没有收到，请重新按住说话') from None
    if len(pcm) < MIN_PCM_BYTES or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
        raise Refused('录音太短或太长，请重新按住说话')
    return pcm


def _decoder(wake_only=False):
    global _model, _decoder_type
    with _model_lock:
        if _model is None:
            if not (MODEL_PATH / 'am/final.mdl').is_file():
                raise Refused('鸭哥暂时听不清，请稍后再试')
            if str(DEPS_PATH) not in sys.path:
                sys.path.insert(0, str(DEPS_PATH))
            try:
                from vosk import KaldiRecognizer, Model, SetLogLevel
                SetLogLevel(-1)
                _model = Model(str(MODEL_PATH))
                _decoder_type = KaldiRecognizer
            except (ImportError, OSError, RuntimeError):
                raise Refused('鸭哥暂时听不清，请稍后再试') from None
        # Keep the command graph warm and Reset between clips. The unrestricted
        # Chinese graph exceeds the browser's latency budget on this board.
        if wake_only not in _recognizers:
            _recognizers[wake_only] = (_decoder_type(_model, RATE, WAKE_GRAMMAR) if wake_only
                                       else _decoder_type(_model, RATE, _grammar(_model)))
        return _recognizers[wake_only]


def warmup():
    """Load the model before the first push-to-talk release pays that cost."""
    _decoder()


def recognize_pcm(encoded, decoder=None, wake_only=False):
    pcm = decode_pcm(encoded)
    if decoder is not None:
        return _recognize(pcm, decoder)
    # One cached recognizer owns one clip at a time. Refuse another request
    # immediately instead of queuing stale speech from a different browser.
    if not _recognition_lock.acquire(blocking=False):
        raise Refused('鸭哥正在识别上一段录音，请稍等一下再说')
    try:
        recognizer = _decoder(wake_only)
        recognizer.Reset()
        try:
            return _recognize(pcm, recognizer)
        finally:
            recognizer.Reset()
    finally:
        _recognition_lock.release()


def _recognize(pcm, recognizer):
    parts = []
    try:
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                parts.append(json.loads(recognizer.Result()).get('text', ''))
        parts.append(json.loads(recognizer.FinalResult()).get('text', ''))
    except (ValueError, RuntimeError, KeyError):
        raise Refused('这次没有听清，请重新按住说话') from None
    text = ' '.join(part for part in parts if isinstance(part, str) and part).strip()[:80]
    if not text:
        raise Refused('这次没有听清，请重新按住说话')
    return {'text': text}
