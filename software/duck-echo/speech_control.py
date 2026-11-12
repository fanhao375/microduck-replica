"""Local Mandarin recognition and one cancellable, bounded command queue."""
import array
from collections import deque
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

from voice_commands import parse_commands,updates_allowed

LOG = logging.getLogger('duck-echo')

# 只在排查时打开：把本次识别到的原话打进日志。默认关，避免日志里留对话内容。
DEBUG_TEXT = os.environ.get('ECHO_DEBUG_TEXT') == '1'

# Keep an unknown-speech alternative and negative/quoted forms. A grammar
# containing only positive commands would force unrelated speech into commands.
COMMAND_GRAMMAR = [
    '坐下', '坐下 来', '站起来', '起立', '起来',
    '鸭子 坐下', '鸭子 坐下 来', '鸭子 站起来', '鸭子 起立',
    '小 鸭子 坐下', '小 鸭子 站起来', '请 坐下', '请 站起来',
    '坐下 吧', '站起来 吧',
    # 「鸭鸭」：手机中控约定的唤醒词。写成「鸭 鸭」是因为词表里是单字「鸭」，
    # 成词的话下面按 model.vosk_model_find_word 过滤时会自己掉出去，不会让整个语法失败
    '鸭 鸭 坐下', '鸭 鸭 站起来', '鸭 鸭 走 一小步', '鸭 鸭 停', '鸭 鸭 停 下',
    # 仍识别「走一小步」以提示改用手机或手柄。明确的停下口令由板载执行。
    '走 一小步', '走 一步', '往 前 走', '往 前 走 一步', '停', '停 下', '停 下来',
    '不要 坐下', '不要 站起来', '不要 起来', '不要 起立',
    '别 坐下', '别 站起来', '别 起来', '别 起立',
    '不 坐下', '不 站起来', '不想 坐下', '不想 站起来',
    '不用 坐下', '不用 站起来', '不能 坐下', '不能 站起来',
    '复读 坐下', '复读 站起来', '他说 坐下', '我说 坐下',
    '[unk]',
]

# 走一小步 / 停之外还认这些动作：前滚翻、低头捡东西、叫一声、打招呼、点头、摇头、歪头。
# 每种说法都给成词和拆成单字两种写法：小模型多半是单字词表，成词的话按
# model.vosk_model_find_word 过滤时会自己掉出去，不会让整个语法失败。
ACTION_PHRASES = {
    # 「前 滚 翻」实测常被听成「前 捡 前 滚 翻」「翻」。加「滚 翻」一条就够——
    # 语法表越长，短词越容易被错认出来（09:24 那次「翻 要 歪头」就是多出来的「翻」）。
    'roulade': ['前滚翻', '前 滚 翻', '翻跟头', '翻 跟 头', '滚 翻'],
    'ground_pick': ['低头捡东西', '低头', '捡东西', '捡 东 西'],
    'chirp': ['叫一声', '叫 一 声', '叫一下', '叫 一 下'],
    'greet': ['打招呼', '打 招 呼', '问好', '问 好'],
    'nod': ['点头', '点 头', '点点头', '点 点 头'],
    'shake': ['摇头', '摇 头', '摇摇头', '摇 摇 头'],
    'tilt': ['歪头', '歪 头', '歪歪头', '歪 歪 头'],
}


def _grammar_entries(phrases):
    """一个动作的说法 → 语法里要有的几条。

    除了原话还要给：唤醒词「鸭 鸭」和称呼「鸭子」前缀、礼貌的「请」、
    以及「不要 / 别」两种否定式——语法里没有否定式的话，说「不要点头」会被硬凑成「点头」而真的执行。
    """
    out = []
    for phrase in phrases:
        out.extend([phrase, '鸭 鸭 ' + phrase, '鸭子 ' + phrase, '请 ' + phrase,
                    '不 要 ' + phrase, '别 ' + phrase])
    return out


COMMAND_GRAMMAR += [entry for phrases in ACTION_PHRASES.values()
                    for entry in _grammar_entries(phrases)]


class MandarinRecognizer:
    def __init__(self, model_path, dependencies=None):
        if dependencies:
            sys.path.insert(0, str(dependencies))
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        if not (Path(model_path) / 'am/final.mdl').is_file():
            raise RuntimeError('Chinese speech model is not installed')
        self.model = Model(str(model_path))
        self.recognizer_type = KaldiRecognizer
        self._grammar_key = ()
        self.grammar = self._make_grammar(self._custom_key() or ())

    @staticmethod
    def _custom_key():
        """Studio 里上传的自定义指令。读不到返回 None，沿用当前语法。"""
        try:
            from voice_commands import custom_skills
            return tuple(sorted(custom_skills().items()))
        except Exception:
            return None

    def _make_grammar(self, key):
        phrases = list(COMMAND_GRAMMAR)
        for command, _skill in key:
            # 小模型是单字词表：整词过滤会掉出去，拆成单字再给一条
            phrases.extend(_grammar_entries([command, ' '.join(command)]))
        grammar = [phrase for phrase in dict.fromkeys(phrases)
                   if all(self.model.vosk_model_find_word(word) >= 0 for word in phrase.split())]
        self._grammar_key = key
        return json.dumps(grammar, ensure_ascii=False)

    def _grammar_for(self):
        key = self._custom_key()
        if key is not None and key != self._grammar_key:
            self.grammar = self._make_grammar(key)
        return self.grammar

    def recognize(self, stereo, rate=48000):
        samples = array.array('h')
        samples.frombytes(stereo[:len(stereo) // 4 * 4])
        # Input is already the selected microphone duplicated into stereo by
        # prepare_audio. Keep one channel; Kaldi's model config resamples 48k.
        mono = samples[::2].tobytes()
        recognizer = self.recognizer_type(self.model, rate, self._grammar_for())
        recognizer.SetWords(True)
        parts = []
        for offset in range(0, len(mono), 8000):
            if recognizer.AcceptWaveform(mono[offset:offset + 8000]):
                parts.append(json.loads(recognizer.Result()))
        parts.append(json.loads(recognizer.FinalResult()))
        scores = [word.get('conf', 0) for part in parts for word in part.get('result', [])]
        return {'text': ' '.join(part.get('text', '') for part in parts).strip(),
                'confidence': min(scores, default=0)}


class VoiceControl:
    def __init__(self, recognizer_factory, executor, fallback, execute_commands=True,
                 cue=None, ttl=6, clock=time.monotonic, recognize_speech=True,guard=updates_allowed):
        self.factory, self.executor, self.fallback = recognizer_factory, executor, fallback
        self.execute_commands, self.cue = execute_commands, cue
        self.recognize_speech = recognize_speech
        self.guard=guard
        self._recognizer = None
        self.ttl, self.clock = ttl, clock
        self.condition = threading.Condition()
        self.queue = deque(maxlen=1)
        self.generation, self.closed = 0, False
        self.ready, self.idle = threading.Event(), threading.Event()
        self.idle.set()
        self.last_result = None
        self.last_at = 0.
        self.last_seq = 0
        self.thread = threading.Thread(target=self._run, name='local-speech', daemon=True)
        self.thread.start()

    def _record(self, result):
        """记下「最近一次认出来的指令」。只留指令名和成不成，不留整句话：
        原话里可能有无关内容，手机页只需要知道它听懂了什么。"""
        with self.condition:
            self.last_result = result
            self.last_at = self.clock()
            self.last_seq += 1

    def last_view(self):
        """给控制口 / 手机页看的只读视图：指令名、成没成、多久之前、第几次、失败原因。"""
        with self.condition:
            item, at, seq = self.last_result, self.last_at, self.last_seq
        if not isinstance(item, dict):
            return None
        command = item.get('command')
        if command is None:
            return None
        accepted = item.get('accepted')
        view = {'command': str(command)[:32],
                'accepted': accepted if type(accepted) is bool else None,
                'age': round(max(0., self.clock() - at), 2),
                'seq': seq}
        # 只带 robotd / 执行器给出的公开理由（例如「机器人在坐姿」「舵机反馈不完整」），
        # 不带识别原话；前端据此说清「为什么没做成」。
        reason = item.get('reason')
        if isinstance(reason, str) and reason:
            view['reason'] = reason[:160]
        return view

    @property
    def recognizer(self):
        """Ready shared Model owner; consumers create their own Kaldi decoder."""
        return self._recognizer if self.ready.is_set() else None

    def submit(self, pcm):
        with self.condition:
            if not self.guard():
                self.cancel();return
            if self.closed:
                return
            self.queue.append((self.generation, self.clock(), pcm))
            self.idle.clear()
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self.generation += 1
            self.queue.clear()

    def valid(self, job):
        with self.condition:
            if not self.guard():
                self.cancel();return False
            return not self.closed and job[0] == self.generation and self.clock() - job[1] <= self.ttl

    def _echo(self, job):
        # Hold the short generation check through starting playback so a new
        # R3 press can always cancel first, then stop any already-started echo.
        with self.condition:
            if self.valid(job):
                self.fallback(job[2])

    def _run(self):
        try:
            recognizer = self.factory()
            self._recognizer = recognizer
            LOG.info('local Chinese speech recognition ready; mode=%s',
                     'actions' if self.execute_commands else 'shadow')
        except Exception:
            recognizer = None
            LOG.exception('speech model unavailable; ordinary echo remains available')
        finally:
            self.ready.set()
        while True:
            with self.condition:
                while not self.queue and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                job = self.queue.popleft()
            try:
                if not self.valid(job):
                    continue
                if recognizer is None or not self.recognize_speech:
                    self._echo(job)
                    continue
                result = recognizer.recognize(job[2])
                # 一段里可能并了好几条口令，按说的顺序逐条做（见 voice_commands.parse_commands）
                commands = parse_commands(result.get('text', ''), result.get('confidence', 0))
                if not self.valid(job):
                    continue
                # Do not retain arbitrary conversation transcriptions in logs.
                # 排查「听到了但认不出」时才开：ECHO_DEBUG_TEXT=1，只打本次识别到的文字。
                if DEBUG_TEXT:
                    LOG.info('speech text=%r', str(result.get('text', ''))[:120])
                LOG.info('speech command=%s confidence=%.2f',
                         ' '.join(commands) or 'none', result.get('confidence', 0))
                if not commands or not self.execute_commands:
                    self._echo(job)
                    self._record({'command': commands[-1] if commands else None,
                                  'shadow': not self.execute_commands})
                    continue
                outcome, command = None, None
                for command in commands:
                    outcome = self.executor.execute(command, allowed=lambda: self.valid(job))
                    LOG.info('voice action: %s',
                             json.dumps({'command': command, **outcome}, ensure_ascii=False))
                    if outcome.get('accepted') is not True:
                        break
                self._record({'command': command, **outcome})
                if self.cue is not None and self.valid(job) and not outcome.get('quiet'):
                    try:
                        self.cue(outcome.get('accepted') is True)
                    except Exception:
                        LOG.exception('voice acknowledgement sound unavailable')
            except Exception:
                LOG.exception('speech processing failed; preserving ordinary echo')
                self._echo(job)
            finally:
                with self.condition:
                    if not self.queue:
                        self.idle.set()

    def close(self):
        with self.condition:
            self.closed = True
            self.generation += 1
            self.queue.clear()
            self.condition.notify_all()
        self.thread.join(timeout=2)
