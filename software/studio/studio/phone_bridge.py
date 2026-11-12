"""手机上 https 之后要补的两件事：摄像头信令转发、手机录音转文字。

为什么得有这个模块：

1. 摄像头：mediad 的信令口 8443 是明文 ws。http 页面直连没事，页面一换成 https，
   浏览器就把「https 页面连明文 ws」当混合内容挡掉，摄像头直接黑掉。
   所以挂一个同域同协议的 /media-ws，由 Studio 转发到本机 127.0.0.1:8443 —— 同源同协议，
   不存在混合内容，桌面版 http 那条路也照样能走。

2. 手机说话：浏览器的语音识别（Web Speech API）在安卓 Chrome 上走的是谷歌服务，国内基本不通，
   iOS 各家表现也不一致。与其指望它，不如把手机录的一段 PCM 传回来，
   用板上 duck-echo 正在用的那一套 Vosk 中文模型来认——跟对着鸭子说话是同一个模型、同一套词表。

依赖说明：vosk 和 websockets 都不在 Studio 自己的 deps 里，而在 duck-echo 用的 asr-deps 下，
所以这里要靠 sys.path 临时加一下。两个都是「用到才 import」，万一没有也不能拖垮整个网页。

11-03 的两处修正（手机说了话、板子回空字符串、还等了 12 秒）：

* **要带命令词表（grammar）**。原来用的是无 grammar 的通用解码，中文小模型通用解码又慢又飘：
  板子实测 1.2 秒的音频要跑 6.3 秒，2 秒就要 12 秒，还常常吐空。
  换成 duck-echo 那份 COMMAND_GRAMMAR，同一段音频 **0.3 秒**，结果一样对。
  词表是直接从 duck-echo 的 speech_control 里借的——两边共用一份，才不会出现
  「对着鸭子说得动、对着手机说不懂」这种玄学。
* **音量归一化**。手机麦克风隔着半米说话，峰值经常只有几百（满幅 32767），
  小模型直接当噪声丢掉。峰值低于 TARGET_PEAK 时线性放大（最多 8 倍），
  同时把峰值/均方/耗时回给前端 —— 认不出来时用户能立刻看到「是太小还是没说话」。
"""
import array
import asyncio
import json
import math
import os
import sys
import threading
import time

ASR_DEPS = '/opt/microduck/resources/asr-deps'
MODEL_DIR = '/opt/microduck/resources/models/vosk-model-small-cn-0.22'
ECHO_DIR = '/opt/microduck/software/current/duck-echo'   # duck-echo 就在隔壁，词表直接借它的
MEDIA_UPSTREAM = 'ws://127.0.0.1:8443'
SAMPLE_RATE = 16000

MIN_PEAK = 120          # 整段最大幅度低于这个值就当没说话（int16 满幅 32767）
MIN_CONF = 0.35         # 低于这个置信度当没听清：宁可不做，也别乱动
TARGET_PEAK = 20000     # 归一化目标
MAX_GAIN = 8.0
CHUNK = 8000            # 0.25 秒一喂

# 手机中控认的口令（和 web/m/actions.js 的 RULES 同一批）。duck-echo 的词表只有坐下/站起来，
# 手机这边还会点头、走一小步、前滚翻……所以这里自己列一份，再和 duck-echo 的合并。
# 模型是单字词表：词条要按单字用空格隔开，整词会被 vosk_model_find_word 过滤掉。
MOBILE_COMMANDS = [
    '点头', '摇头', '歪头', '偏头', '前滚翻', '翻跟头', '翻身', '低头捡东西', '低头', '捡东西',
    '叫一声', '叫一下', '叫两声', '打招呼', '你好', '张嘴', '开口',
    '站起来', '站起', '起立', '起来', '起身', '坐下', '坐一坐',
    '停下', '站住',
    '走一小步', '走一步', '走两步', '迈一步', '走一下', '前进', '往前', '开步',
]
MOBILE_PREFIXES = ('', '鸭鸭', '鸭子', '请', '不要', '别')

_model = None
_error = None
_lock = threading.Lock()
_grammar = None
_grammar_ready = False


def _deps_on_path():
    if os.path.isdir(ASR_DEPS) and ASR_DEPS not in sys.path:
        sys.path.insert(0, ASR_DEPS)


def _load_model():
    """第一次用到才加载，加载完就一直留着（模型不大，但别每次请求都读一遍磁盘）。"""
    global _model, _error
    with _lock:
        if _model is not None or _error is not None:
            return _model, _error
        try:
            _deps_on_path()
            from vosk import Model, SetLogLevel
            # 不关掉的话，每认一句话都会把整张词表（两百多条）刷进 Studio 日志
            SetLogLevel(-1)
            _model = Model(MODEL_DIR)
        except Exception as exc:                       # 没装 / 模型不在：只是这个功能没有
            _error = '%s: %s' % (type(exc).__name__, exc)
    return _model, _error


def _mobile_grammar():
    """手机口令 → 单字分隔的词条，带上常见前后缀。"""
    out = []
    for phrase in MOBILE_COMMANDS:
        body = ' '.join(phrase)
        for prefix in MOBILE_PREFIXES:
            out.append((' '.join(prefix) + ' ' + body) if prefix else body)
    return out


def _grammar_json(model):
    """手机口令 + duck-echo 的命令词表。一个都过滤不出来就返回 None（退回通用解码，慢但能跑）。"""
    global _grammar, _grammar_ready
    if _grammar_ready:
        return _grammar
    entries = list(_mobile_grammar())
    try:
        if ECHO_DIR not in sys.path:
            sys.path.insert(0, ECHO_DIR)
        from speech_control import COMMAND_GRAMMAR
        entries.extend(COMMAND_GRAMMAR)
    except Exception:
        pass
    try:
        # 词表里真实存在的说法才留下：小模型是单字词表，写成整词的会整条丢掉
        kept, seen = [], set()
        for phrase in entries:
            if phrase in seen:
                continue
            seen.add(phrase)
            if all(model.vosk_model_find_word(w) >= 0 for w in phrase.split()):
                kept.append(phrase)
        if kept and '[unk]' not in kept:
            kept.append('[unk]')
        _grammar = json.dumps(kept, ensure_ascii=False) if kept else None
    except Exception:
        _grammar = None
    _grammar_ready = True
    return _grammar


def _normalize(pcm):
    """PCM → 归一化后的 PCM + 这段音频的体检结果。"""
    samples = array.array('h')
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if not samples:
        return b'', {'sec': 0.0, 'peak': 0, 'rms': 0, 'gain': 1.0}
    peak = max(max(samples), -min(samples))
    rms = int(math.sqrt(sum(x * x for x in samples) / len(samples)))
    gain = 1.0
    if 0 < peak < TARGET_PEAK:
        gain = min(MAX_GAIN, TARGET_PEAK / float(peak))
        if gain > 1.05:
            samples = array.array('h', [max(-32768, min(32767, int(x * gain))) for x in samples])
        else:
            gain = 1.0
    return samples.tobytes(), {'sec': round(len(samples) / float(SAMPLE_RATE), 2),
                               'peak': int(peak), 'rms': int(rms), 'gain': round(gain, 2)}


def transcribe_detail(pcm, use_grammar=True):
    """16 kHz / 16 bit / 单声道 / 小端的裸 PCM → {'text', 'error', 'info'}。

    info 里带上这次的体检数据（秒数、峰值、放大倍数、识别耗时、置信度、没认出来的原因），
    前端能照着说人话：「声音太小」「听清了但不确定」。

    use_grammar=False 走通用解码：慢得多（2 秒音频要 6~12 秒），但不受命令词表限制，
    自检页用它来看「板子到底听到什么」，也用来判断 [unk] 是「没听清」还是「词不在表里」。
    """
    out = {'text': '', 'error': None, 'info': {}}
    model, err = _load_model()
    if err:
        out['error'] = err
        return out

    pcm, info = _normalize(pcm)
    out['info'] = info
    if info['peak'] < MIN_PEAK:
        info['reason'] = 'silent'
        return out

    started = time.time()
    try:
        _deps_on_path()
        from vosk import KaldiRecognizer
        grammar = _grammar_json(model) if use_grammar else None
        rec = (KaldiRecognizer(model, SAMPLE_RATE, grammar) if grammar
               else KaldiRecognizer(model, SAMPLE_RATE))
        rec.SetWords(True)
        parts = []
        for i in range(0, len(pcm), CHUNK):
            if rec.AcceptWaveform(pcm[i:i + CHUNK]):
                parts.append(json.loads(rec.Result()))
        parts.append(json.loads(rec.FinalResult()))
    except Exception as exc:
        out['error'] = '%s: %s' % (type(exc).__name__, exc)
        return out

    text = ''.join(p.get('text', '') for p in parts).replace(' ', '')
    # 带词表解码时，语音对不上词表里的任何一条就会吐 [unk]（unknown word）。
    # 它不是一个「词」，不能当成识别结果往下传 —— 否则前端会去找一个叫 [unk] 的动作。
    unknown = '[unk]' in text
    text = text.replace('[unk]', '').strip()
    confs = [w.get('conf', 0.0) for p in parts for w in p.get('result', [])]
    conf = min(confs, default=0.0)
    info.update({'ms': int((time.time() - started) * 1000), 'conf': round(conf, 2),
                 'grammar': bool(grammar)})
    if not text:
        info['reason'] = 'unknown' if unknown else 'unclear'
        return out
    if unknown:
        # 「点头 [unk]」这种：认出一半，还剩一截听不懂。按没认出处理更安全。
        info['reason'] = 'unknown'
        info['heard'] = text
        return out
    if conf < MIN_CONF:
        # 认出来了但不敢用：把原话留在 info 里，diag 页能看见，前端只提示「没听清」
        info['reason'] = 'unclear'
        info['heard'] = text
        return out
    out['text'] = text
    return out


def transcribe(pcm):
    """老接口：只要文字和错误。"""
    detail = transcribe_detail(pcm)
    return detail['text'], detail['error']


def warmup():
    """启动时先在后台把模型和词表读好，别让用户说的第一句话干等它。

    Zero 3W 上冷加载实测 2~5 秒，开机那阵子 CPU 忙的时候更久（用户 11-03 就遇到过一次
    上传后等了 25 秒才回来）。这段时间页面上什么都不会变，看着就像「长按没反应」，
    所以让 Studio 一起来就把它读好。
    """
    model, err = _load_model()
    if model is not None:
        _grammar_json(model)          # 词表也要编译一次，第一句话才不会又卡在这里
    return (model is not None), err


async def pump_media(ws, upstream=MEDIA_UPSTREAM):
    """在 starlette 的 WebSocket 和本机 mediad 之间双向转发。

    两边 API 不一样（starlette 收 dict，websockets 库直接给 str/bytes），所以各写一个方向的泵，
    谁先结束就把另一个也收掉。
    """
    _deps_on_path()
    import websockets

    async with websockets.connect(upstream, max_size=None, ping_interval=None) as up:

        async def down_to_up():
            """浏览器 → mediad"""
            try:
                while True:
                    message = await ws.receive()
                    kind = message.get('type')
                    if kind == 'websocket.disconnect':
                        return
                    if kind == 'websocket.receive':
                        data = message.get('bytes')
                        await up.send(data if data is not None else (message.get('text') or ''))
            except Exception:
                return

        async def up_to_down():
            """mediad → 浏览器"""
            try:
                async for data in up:
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)
            except Exception:
                return

        tasks = [asyncio.create_task(down_to_up()), asyncio.create_task(up_to_down())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
