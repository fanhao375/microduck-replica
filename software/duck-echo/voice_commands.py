"""Exact Chinese posture commands over the existing robotd JSON-RPC contract."""
import json
import math
import re
import socket
import time
import os
import stat
import threading
from pathlib import Path
from contextlib import contextmanager
from pad_mapping import PAD_MAPPING

UPDATE_MARKER=Path('/run/microduck-updater/installing')
NATIVE_JOURNAL='/var/lib/microduck-helper/native-update/journal.json'
NATIVE_EXCLUSIONS=('/var/lib/microduck-helper/native-update/active',
    '/run/microduck-helper/native-update-restore', *(
    '/etc/systemd/system/'+unit+'.service.d/zzzzzzz-microduck-native-update.conf'
    for unit in ('robotd','padd','gamesir-xboxd','duck-echo')))


class NativeUpdateGuard:
    """Standalone mirror of helper.native_update_state's public busy contract.

    Never imports managed Studio code or runs sudo in the audio path. Metadata
    changes invalidate a bounded JSON cache, including atomic journal replacement.
    Contract tests compare every journal flag and exclusion with the helper.
    """
    def __init__(self, root='/'):
        self.root=Path(root)
        parts=NATIVE_JOURNAL.strip('/').split('/')
        self.parents=[self.root.joinpath(*parts[:i]) for i in range(1,len(parts))]
        self.path=self.root/NATIVE_JOURNAL.lstrip('/')
        self.exclusions=[self.root/p.lstrip('/') for p in NATIVE_EXCLUSIONS]
        self.key=None;self.busy=False;self.lock=threading.Lock()

    def active(self):
        with self.lock:
            try:
                metadata=[]
                for path in (*self.parents,self.path,*self.exclusions):
                    try:
                        st=path.lstat()
                        metadata.append((st.st_mode,st.st_uid,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns))
                    except FileNotFoundError: metadata.append(None)
                key=tuple(metadata)
                if key==self.key:return self.busy
                self.busy=self._read(metadata)
                self.key=key
                return self.busy
            except (OSError,ValueError,TypeError,AttributeError,RecursionError):
                self.key=None;self.busy=True;return True

    def _read(self, metadata):
        journal=metadata[len(self.parents)]
        exclusions=any(item is not None for item in metadata[len(self.parents)+1:])
        for index,entry in enumerate(metadata[:len(self.parents)+1]):
            if entry is None:continue
            mode,uid,*_=entry
            kind=stat.S_ISREG if index==len(self.parents) else stat.S_ISDIR
            if not kind(mode) or (self.root==Path('/') and os.name=='posix' and (uid!=0 or mode&0o022)):
                return True
        if journal is None:return exclusions
        if journal[3]>65536:return True
        descriptor=os.open(self.path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(descriptor,'rb') as stream:
            raw=stream.read(65537)
        if len(raw)>65536:return True
        value=json.loads(raw)
        if (not isinstance(value,dict) or value.get('schema')!='microduck-native-update/1'
            or type(value.get('active')) is not bool
            or type(value.get('restore_pending',False)) is not bool
            or type(value.get('cleanup_pending',False)) is not bool
            or not re.fullmatch(r'[0-9a-f]{32}',value.get('transaction_id',''))
            or value.get('phase') not in ('prepared','guarding','guarded','replacing','replaced','verified_commissioning',
                'restoring_normal','verified_motion','factory_handoff_pending','completed','rolled_back',
                'recovering','old_restored','recovery_required')
            or (value.get('reason') is not None and (not isinstance(value['reason'],str) or len(value['reason'])>300))):
            return True
        return value['active'] or value.get('restore_pending',False) or value.get('cleanup_pending',False) or exclusions


NATIVE_GUARD=NativeUpdateGuard()

class UpdateLocked(RuntimeError): pass

def updates_allowed(): return not UPDATE_MARKER.exists() and not NATIVE_GUARD.active() and not PAD_MAPPING.blocked()

@contextmanager
def motion_lease(marker=UPDATE_MARKER,lock=None):
    """Finish existing actions before root's EX preflight; reject new actions."""
    marker=Path(marker);lock=Path(lock) if lock else marker.parent/'motion.lock'
    if PAD_MAPPING.blocked():raise UpdateLocked('controller mapping in progress')
    if marker.exists() or NATIVE_GUARD.active():raise UpdateLocked('software / native update in progress')
    descriptor=None
    try:
        try:descriptor=os.open(lock,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        except FileNotFoundError:
            # Legacy installation without the updater has no update lease yet.
            if marker.exists() or NATIVE_GUARD.active():raise UpdateLocked('software / native update in progress')
        if descriptor is not None:
            import fcntl
            try:fcntl.flock(descriptor,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:raise UpdateLocked('software update in progress') from None
        if marker.exists() or NATIVE_GUARD.active():raise UpdateLocked('software / native update in progress')
        if PAD_MAPPING.blocked():raise UpdateLocked('controller mapping in progress')
        yield
    finally:
        if descriptor is not None:os.close(descriptor)


# ---------------- 语音能做的动作 ----------------
# 名字跟手机中控 web/m/actions.js 保持一致：板载这条路的识别结果（audio.last.command）
# 直接就是这里的键，页面拿去查 CMD_CN 显示中文。
SKILL_COMMANDS = {'roulade': 'roulade', 'ground_pick': 'ground_pick'}   # robot.do {skill}
SOUND_COMMANDS = {'chirp': 'chirp', 'greet': 'greet'}                   # robot.sound {tag}
HEAD_COMMANDS = ('nod', 'shake', 'tilt')                                # robot.head 通知
ALL_COMMANDS = ('sit', 'stand', 'walk', 'stop', 'roulade', 'ground_pick',
                'chirp', 'greet', 'nod', 'shake', 'tilt')

# 用户在 Studio「模型」页上传的裸 ONNX：指令名 → robotd 技能名（custom_xxxxxxxx）。
# 模型库就在本机的 state 目录；读不到就当没有，别让语音整条挂掉。
CUSTOM_MODELS = Path('/var/lib/microduck-studio/models')
_CUSTOM_TTL_S = 1.0
_custom_cache = {'at': -1e9, 'value': {}}
_custom_lock = threading.Lock()


def custom_skills():
    now = time.monotonic()
    with _custom_lock:
        if now - _custom_cache['at'] < _CUSTOM_TTL_S:
            return dict(_custom_cache['value'])
    found = {}
    try:
        for child in CUSTOM_MODELS.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            meta = child / '.studio-model.json'
            try:
                if meta.is_symlink() or meta.stat().st_size > 64 << 10:
                    continue
                record = json.loads(meta.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or record.get('kind') != 'custom':
                continue
            command, skill = record.get('command'), record.get('skill')
            if (isinstance(command, str) and command.strip() and len(command.strip()) <= 20
                    and isinstance(skill, str) and re.fullmatch(r'custom_[a-f0-9]{8}', skill)
                    and (child / 'policy.onnx').is_file()):
                found[command.strip()] = skill
    except OSError:
        found = {}
    with _custom_lock:
        _custom_cache['at'] = now
        _custom_cache['value'] = found
    return dict(found)

# 点头 / 摇头 / 歪头的幅度和快慢：照搬手柄页 studio/pad_combo.py 的取值（那里标了「推测，要上机核」）。
# 头的指令进的是策略的观测，不直接写舵机，所以幅度是「意向」不是角度。
HEAD_TICK_S = 0.04                       # 25 Hz，跟 pad_combo.TICK_S 一样
NOD_AMP, NOD_PERIOD_S, NOD_TIMES = 0.5, 0.7, 2
SHAKE_AMP, SHAKE_PERIOD_S, SHAKE_TIMES = 0.6, 0.8, 2
TILT_AMP, TILT_S, TILT_RAMP_S = 0.4, 1.2, 0.25
HEAD_ZERO = {'neck_pitch': 0.0, 'head_pitch': 0.0, 'head_yaw': 0.0, 'head_roll': 0.0}


def head_seconds(command):
    """这个头部动作要做多久（秒）。"""
    if command == 'nod':
        return NOD_PERIOD_S * NOD_TIMES
    if command == 'shake':
        return SHAKE_PERIOD_S * SHAKE_TIMES
    if command == 'tilt':
        return TILT_S
    return 0.0


def head_frame(command, t):
    """第 t 秒头应该在哪（照 pad_combo.head_at）。不是头部动作返回 None。"""
    if command == 'nod':
        pitch = NOD_AMP * math.sin(2 * math.pi * t / NOD_PERIOD_S)
        return {'neck_pitch': 0.0, 'head_pitch': pitch, 'head_yaw': 0.0, 'head_roll': 0.0}
    if command == 'shake':
        yaw = SHAKE_AMP * math.sin(2 * math.pi * t / SHAKE_PERIOD_S)
        return {'neck_pitch': 0.0, 'head_pitch': 0.0, 'head_yaw': yaw, 'head_roll': 0.0}
    if command == 'tilt':
        total = TILT_S
        ramp = min(TILT_RAMP_S, total / 3)
        k = min(1.0, t / ramp) if t < total - ramp else max(0.0, (total - t) / ramp)
        return {'neck_pitch': 0.0, 'head_pitch': 0.0, 'head_yaw': 0.0, 'head_roll': TILT_AMP * k}
    return None


# 置信度门槛。0.75 实测太严：同一句「站起来」有时 1.00、有时 0.54，全被卡掉。
# 整句精确命中比「句子里有关键词」可靠得多，所以门槛分开定。
MIN_EXACT = 0.50
MIN_KEYWORD = 0.50

# 整句命中表（最精确的一档）
EXACT = {
    '坐下': 'sit', '坐下来': 'sit',
    '站起来': 'stand', '站起': 'stand', '起立': 'stand', '起来': 'stand',
    # 走路只认「走一小步」这类有限步数的话；「别走」「不要走」算否定，按原来的规矩不执行
    '走一小步': 'walk', '走一步': 'walk', '向前走': 'walk', '往前走': 'walk', '走': 'walk',
    '停': 'stop', '停下': 'stop', '停下来': 'stop', '停住': 'stop', '站住': 'stop', '停步': 'stop',
    # 技能：robot.do {skill}。要站好、策略在跑才做（手柄页的规矩），做不成会原样报理由
    '前滚翻': 'roulade', '翻跟头': 'roulade', '翻个跟头': 'roulade', '打个滚': 'roulade',
    '滚一个': 'roulade', '前翻': 'roulade',
    '低头捡东西': 'ground_pick', '捡东西': 'ground_pick', '低头捡': 'ground_pick',
    '捡个东西': 'ground_pick', '捡一下东西': 'ground_pick', '低头': 'ground_pick',
    # 叫一声：robot.sound {tag}。不动关节，卸力时也能叫（robotd main.rs:4300-4307）
    # 「叫一下」会被「一下」剥成「叫」，所以单字也要认
    '叫一声': 'chirp', '叫一下': 'chirp', '叫叫': 'chirp', '嘎一声': 'chirp',
    '嘎一下': 'chirp', '叫唤': 'chirp', '叫': 'chirp',
    '打招呼': 'greet', '打个招呼': 'greet', '问好': 'greet', '问个好': 'greet',
    # 点头 / 摇头 / 歪头：robot.head 通知，按顺序发完再回正（头是锁存的，不回正就一直歪着）
    '点头': 'nod', '点点头': 'nod', '点一下头': 'nod', '点个头': 'nod',
    '摇头': 'shake', '摇摇头': 'shake', '摇一下头': 'shake', '摇个头': 'shake',
    '歪头': 'tilt', '歪歪头': 'tilt', '歪一下头': 'tilt', '歪个头': 'tilt', '歪脑袋': 'tilt',
}

# 兜底关键词（按顺序试，先具体后笼统）。离线识别是语法约束的，输出只可能是语法表里的
# 组合，所以「句子里出现关键词」并不像自由听写那样容易误判。
# 实测的几种坏输出：「起立 起立 起立」「起立 站起来 招 [unk]」「前 捡 前 滚 翻」「翻」。
KEYWORDS = [
    ('站住', 'stop'),                       # 必须排在「站」前面
    ('滚翻', 'roulade'), ('翻身', 'roulade'), ('打滚', 'roulade'),
    ('翻跟头', 'roulade'), ('翻', 'roulade'),
    ('捡', 'ground_pick'),
    ('招呼', 'greet'), ('问好', 'greet'),
    ('点头', 'nod'), ('摇头', 'shake'), ('歪头', 'tilt'), ('歪脑袋', 'tilt'),
    ('停', 'stop'),
    ('起立', 'stand'), ('起来', 'stand'),
    ('走', 'walk'),
    ('坐', 'sit'), ('站', 'stand'),
    ('叫', 'chirp'),
]

# 单字关键词只有在整句很短时才认（「翻」= 只听到一个字，可以当意图；「走过去再说」不能当走路）
SINGLE_CHAR_MAX = 4
NEGATIVE_WORDS = ('不', '别', '甭', '勿')
# 「复读 坐下」「他说 站起来」是把指令当话转述，不是在命令
QUOTED_PREFIXES = ('复读', '他说', '她说', '我说', '你说', '我说啊')
# 「坐下再站起来」是两步指令：语音只能挑一个动作做，干脆不做
SEQUENCE_WORDS = ('再', '然后', '接着', '之后')


def _squash(text):
    """把「起立起立起立」压成「起立」。实测连着说两遍会被端点检测并成一句。"""
    while len(text) > 2:
        shrunk = False
        for cut in range(2, len(text) // 2 + 1):
            if len(text) % cut == 0:
                unit = text[:cut]
                if unit * (len(text) // cut) == text:
                    text, shrunk = unit, True
                    break
        if not shrunk:
            break
    return text


def _match(text, confidence):
    """一段文字 → (动作, 命中的关键词)。认不出返回 (None, None)。"""
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
        return None, None
    text = re.sub(r'\[unk\]|unk', '', text if isinstance(text, str) else '')
    text = re.sub(r'[\s，。！？、,.!?]', '', text)
    # 「鸭鸭」是手机中控约定的唤醒词（用户 10-31 定的）：说「鸭鸭站起来」跟说「站起来」一样。
    # 顺序按长度排：先剥「小鸭子」，再「鸭子」，最后「鸭鸭」——「小鸭子」不会被「鸭子」吃半个字。
    for address in ('小鸭子', '鸭子', '鸭鸭'):
        if text.startswith(address):
            text = text[len(address):]
            break
    if text.startswith('请'):
        text = text[1:]
    for suffix in ('一下吧', '一下', '吧'):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    text = _squash(text)
    if not text:
        return None, None
    # 否定式先判：宁可不动，也不能因为「不要点头」里有「点头」就真去点头
    if any(word in text for word in NEGATIVE_WORDS):
        return None, None
    if text.startswith(QUOTED_PREFIXES):
        return None, None
    if any(word in text for word in SEQUENCE_WORDS):
        return None, None
    if text in EXACT and confidence >= MIN_EXACT:
        return EXACT[text], text
    # 用户上传的 ONNX 指令：整句命中才做，别拿它去跑下面的关键词兜底
    if text in custom_skills() and confidence >= MIN_EXACT:
        return text, text
    if confidence < MIN_KEYWORD:
        return None, None
    hits = [(len(keyword), command, keyword) for keyword, command in KEYWORDS
            if keyword in text
            and not (len(keyword) == 1 and len(text) > SINGLE_CHAR_MAX)]
    if not hits:
        return None, None
    # 取最长（最具体）的那个：「翻 要 歪头」里「歪头」比「翻」可信，不能因为出现两个就都不做
    longest = max(hit[0] for hit in hits)
    top = {hit[1] for hit in hits if hit[0] == longest}
    if len(top) != 1:
        # 同样具体却指向不同动作（「点头再摇头」），说明没听清意图，不做
        return None, None
    command = top.pop()
    keyword = next(hit[2] for hit in hits if hit[0] == longest and hit[1] == command)
    return command, keyword


def parse_command(text, confidence):
    """一段文字 → 一个动作（认不出返回 None）。"""
    return _match(text, confidence)[0]


# 称呼单独拿出来：它既是唤醒词，也是「一句里并了好几条口令」时的天然分隔符
ADDRESSES = ('小鸭子', '鸭子', '鸭鸭')
MAX_COMMANDS_PER_UTTERANCE = 3


def parse_commands(text, confidence):
    """一段识别结果里可能并了好几条口令，逐条认出来，按说的顺序返回。

    上机实测（2026-11-01）：端点检测并不总在停顿处切断，连着说的几句会被并成一段，
    比如「点头 鸭 鸭 摇头 鸭 鸭 歪头」「鸭 鸭 前 滚 翻 鸭 鸭 低头 捡 东 西」。
    直接按整段匹配会被「两个动作都有」判成歧义、一条都不做；所以先按称呼切开再逐段认。
    """
    if not isinstance(text, str):
        return []
    # 先去掉空白再切：识别结果里「鸭 鸭」是带空格的（词表按单字），不去空白切不开
    text = re.sub(r'[\s，。！？、,.!?]', '', text)
    hits = []
    for part in re.split('|'.join(ADDRESSES), text):
        command, keyword = _match(part, confidence)
        if command is not None:
            hits.append((command, keyword, len(part)))
    # 「整段只剩一个孤字」多半是上一句的尾巴：鸭子一动，舵机噪声就把静音判没了，
    # 端点检测凑不满静音时长，会把前后两句连成一段（比如「翻 鸭 鸭 摇头」里的「翻」）。
    # 别的段落有更实在的命中时，就把这种孤字结果丢掉；只剩它一个时才认。
    if len(hits) > 1:
        hits = [hit for hit in hits if len(hit[1]) > 1 or hit[2] > 2] or hits
    commands = []
    for command, _, _ in hits:
        if not commands or commands[-1] != command:
            commands.append(command)
    return commands[:MAX_COMMANDS_PER_UTTERANCE]


class RobotClient:
    """robotd 的 JSON-RPC。请求（init / enable / do / sound / stop / policies / busStatus）走 call，
    每次新开一条连接、等回话；通知（head / move / mouth）走 notify，一条常驻连接、写完就走。

    通知不带 id、也不回话（duck-ipc-proto lib.rs:522-529），拿 call 去发会一直等到超时——
    点头每帧卡 1.5 秒，整个动作就没法做了。跟 Studio 的 RobotPort 一个做法。
    """

    def __init__(self, path='/run/robotd.sock', timeout=1.5, opener=None):
        self.path, self.timeout = path, timeout
        self._opener = opener or (lambda: socket.socket(socket.AF_UNIX))
        self._notify_socket = None
        self._notify_lock = threading.Lock()

    def notify(self, method, params=None):
        """发一条通知：断了就丢掉这条连接重连一次，第二次还不行才抛。"""
        line = (json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params or {}},
                           separators=(',', ':')) + '\n').encode()
        with self._notify_lock:
            for attempt in (1, 2):
                try:
                    if self._notify_socket is None:
                        client = self._opener()
                        self._notify_socket = client   # 先挂上：连不上时 _close_notify 也收得掉
                        client.settimeout(self.timeout)
                        client.connect(self.path)
                    self._notify_socket.sendall(line)
                    return
                except OSError:
                    self._close_notify()
                    if attempt == 2:
                        raise

    def _close_notify(self):
        if self._notify_socket is not None:
            try:
                self._notify_socket.close()
            except OSError:
                pass
            self._notify_socket = None

    def call(self, method, params=None):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(self.timeout)
            client.connect(self.path)
            client.sendall((json.dumps(request) + '\n').encode())
            with client.makefile('rb') as stream:
                response = stream.readline(1024 * 1024 + 1)
            if not response or len(response) > 1024 * 1024:
                raise RuntimeError('invalid or oversized robotd response')
            result = json.loads(response)
            if result.get('id') != 1 or 'error' in result:
                raise RuntimeError('robotd refused ' + method + ': ' + str(result.get('error')))
            data = result.get('result')
            if not isinstance(data, dict):
                raise RuntimeError('robotd returned an unexpected result for ' + method)
            return data


class PostureCommands:
    """语音能做的一切：坐 / 站 / 走一小步 / 停 / 前滚翻 / 低头捡 / 叫一声 / 打招呼 / 点头 / 摇头 / 歪头。

    类名沿用旧名字，避免动 import 和既有测试。
    """
    STEP_S = 1.0          # 「走一小步」就是走这么久，不放开无人看管的长走

    def __init__(self, robot, timeout=5, sleep=time.sleep,guard=updates_allowed,lease=motion_lease,
                 clock=time.monotonic):
        self.robot, self.timeout, self.sleep = robot, timeout, sleep
        self.guard,self.lease=guard,lease
        self.clock=clock

    def state(self):
        policies = self.robot.call('robot.policies')
        bus = self.robot.call('robot.busStatus')
        if not isinstance(policies.get('sitting'), bool):
            raise RuntimeError('cannot read current sitting state')
        if bus.get('phase') != 'control' or bus.get('mode') != 'motion':
            raise RuntimeError('robot is not in motion control mode')
        return policies, bus

    def _require_driving(self, policies, bus, request=None, allowed=None):
        """技能、头部动作的前置：站着、上着劲、回过中、策略在跑。

        跟手柄页的 need_drive 一致（studio/pad_drive.py:1592-1603）；头的指令进的是策略的观测，
        策略没在跑时发了也不动（pad_combo.py 文件头）。

        站好、上劲、回过中之后策略往往还是停的；以前要人工点「开始走策略」。
        这时跟那个按钮用同一套 stop → enable 自己把策略开起来，开不起来才报错，
        理由原样报给手机页。坐着 / 没上劲 / 没回过中仍然直接拒绝。
        """
        torque = bus.get('native', {}).get('torque_state_confirmed') is True
        homed = policies.get('homed') is True or bus.get('homed') is True
        if not torque or not homed:
            raise RuntimeError('robot must be upright and homed with torque confirmed')
        if policies['sitting']:
            raise RuntimeError('robot is sitting; stand up first')
        if bus.get('policy_enabled') is not True:
            if request is None:
                raise RuntimeError('walking policy is not running; stand up and start it first')
            self._ensure_policy(request, allowed or (lambda: True))

    def _ensure_policy(self, request, allowed):
        """站起来之后把「走策略」自己开起来（等同手机页的「开始走策略」按钮）。

        以前说「站起来」只是站起来，策略还停着，必须人工点一下才能继续走 / 点头。
        这里在站好、上劲、回过中之后用同一套 stop → enable 把策略开起来；
        已经开着就什么都不做。开不起来才抛，理由原样报给手机页。
        """
        _, bus = self.state()
        if bus.get('policy_enabled') is True:
            return
        request('robot.stop')
        request('robot.enable', {'on': True, 'toggle': False})
        deadline = self.clock() + 1.2
        while True:
            _, bus = self.state()
            if bus.get('policy_enabled') is True:
                return
            if self.clock() >= deadline or not allowed():
                raise RuntimeError('walking policy was not started')
            self.sleep(0.05)

    def _play_head(self, command, allowed):
        """发一串 robot.head 通知把点头 / 摇头 / 歪头做完，结束时一定回正。

        robot.head 是通知（不回话），走 RobotClient.notify；它的目标也不过期
        （fork robotd intents.rs:569-588，最后一次发的值一直留着），所以「做完」「被打断」
        「出错」三条路都要补一次全 0，否则头就一直歪着。
        """
        total = max(0.2, float(head_seconds(command)))
        start = self.clock()
        try:
            while True:
                if not allowed():
                    return {'accepted': False, 'reason': 'cancelled'}
                elapsed = self.clock() - start
                if elapsed >= total:
                    break
                self.robot.notify('robot.head', head_frame(command, elapsed) or dict(HEAD_ZERO))
                left = total - (self.clock() - start)
                if left <= 0:
                    break
                self.sleep(min(HEAD_TICK_S, left))
            return {'accepted': True, 'head': command}
        finally:
            # 回正不看 allowed：头已经动了，必须收回去
            try:
                self.robot.notify('robot.head', dict(HEAD_ZERO))
            except OSError:
                pass

    def execute(self, command, allowed=lambda: True):
        try:
            if not self.guard():raise UpdateLocked('software update in progress')
            with self.lease():
                return self._execute(command,lambda:allowed() and self.guard())
        except UpdateLocked as error:return {'accepted':False,'reason':str(error)}

    def _execute(self,command,allowed):
        custom = custom_skills()
        if command not in ALL_COMMANDS and command not in custom:
            return {'accepted': False, 'reason': 'unknown command'}
        try:
            policies, bus = self.state()
            if not allowed():
                return {'accepted': False, 'reason': 'cancelled'}
            desired = command == 'sit'
            torque = bus.get('native', {}).get('torque_state_confirmed') is True
            homed = policies.get('homed') is True or bus.get('homed') is True
            initialized = False

            def request(method, params=None):
                if not allowed():
                    raise RuntimeError('cancelled')
                result = self.robot.call(method, params)
                if result.get('accepted') is not True:
                    raise RuntimeError(str(result.get('reason', method + ' was refused')))

            # 叫一声 / 打招呼：robot.sound 不动关节，卸力着也能叫（robotd main.rs:4300-4307）。
            # 标 quiet：做完不再补一声「收到了」的提示音，不然叫一声会连着响两下。
            if command in SOUND_COMMANDS:
                request('robot.sound', {'tag': SOUND_COMMANDS[command]})
                return {'accepted': True, 'sound': SOUND_COMMANDS[command], 'quiet': True}

            # 技能（前滚翻 / 低头捡东西）：跟手柄页的技能按钮一个规矩，站好、策略在跑才做
            if command in SKILL_COMMANDS:
                self._require_driving(policies, bus, request, allowed)
                skill = SKILL_COMMANDS[command]
                request('robot.do', {'skill': skill})
                return {'accepted': True, 'skill': skill}

            # 自定义 ONNX 技能：跟内置技能一个规矩（站好、策略在跑才做）
            if command in custom:
                self._require_driving(policies, bus, request, allowed)
                skill = custom[command]
                request('robot.do', {'skill': skill})
                return {'accepted': True, 'skill': skill}

            # 点头 / 摇头 / 歪头：robot.head 通知一路发完，最后一定回正（见 _play_head）
            if command in HEAD_COMMANDS:
                self._require_driving(policies, bus, request, allowed)
                return self._play_head(command, allowed)

            # 走 / 停：只在「已经站着、上着劲、回过中」的时候放行。
            # 走只走 STEP_S 秒就停，不因为一句话把鸭子放出去跑——语音这条路看不见它去哪。
            if command in ('walk', 'stop'):
                if not torque or not homed:
                    raise RuntimeError('robot must be upright and homed with torque confirmed')
                if policies['sitting']:
                    raise RuntimeError('robot is sitting; stand up first')
                if command == 'stop':
                    request('robot.stop')
                    return {'accepted': True, 'changed': True, 'sitting': False, 'stopped': True}
                request('robot.stop')
                request('robot.enable', {'on': True, 'toggle': False})
                deadline = self.clock() + max(0.2, float(self.STEP_S))
                while self.clock() < deadline and allowed():
                    self.sleep(0.05)
                request('robot.stop')
                return {'accepted': True, 'changed': True, 'sitting': False, 'stepped': True}

            if policies['sitting'] == desired and (desired or (torque and homed)):
                if command == 'stand':
                    self._ensure_policy(request, allowed)
                return {'accepted': True, 'changed': False, 'sitting': desired}
            if command == 'stand' and not policies['sitting'] and not torque:
                request('robot.init')
                initialized = True
            else:
                if not torque or not homed:
                    raise RuntimeError('robot must be upright and homed with torque confirmed')
                if not any(isinstance(slot, dict) and slot.get('slot') == 'sitstand'
                           and slot.get('path') and not slot.get('error')
                           for slot in policies.get('slots', [])):
                    raise RuntimeError('sit/stand policy is not installed')
                if bus.get('policy_enabled') is not True:
                    if policies['sitting']:
                        raise RuntimeError('sitting with policy off; use the existing recovery procedure')
                    request('robot.stop')
                    request('robot.enable', {'on': True, 'toggle': False})
                    deadline = time.monotonic() + 1.2
                    while True:
                        _, bus = self.state()
                        if bus.get('policy_enabled') is True:
                            break
                        if time.monotonic() >= deadline or not allowed():
                            raise RuntimeError('policy enable was not confirmed')
                        self.sleep(0.05)
                # Refresh the toggle state immediately before using the toggle API.
                policies, _ = self.state()
                if policies['sitting'] != desired:
                    request('robot.do', {'skill': 'sit_toggle'})
            deadline = time.monotonic() + self.timeout
            while allowed():
                policies, bus = self.state()
                ready = policies['sitting'] == desired
                if initialized:
                    ready = ready and (policies.get('homed') is True or bus.get('homed') is True)
                    ready = ready and bus.get('native', {}).get('torque_state_confirmed') is True
                if ready:
                    if command == 'stand':
                        self._ensure_policy(request, allowed)
                    return {'accepted': True, 'changed': True, 'sitting': desired}
                if time.monotonic() >= deadline:
                    raise RuntimeError('posture request accepted but target state not confirmed')
                self.sleep(0.08)
            return {'accepted': False, 'reason': 'cancelled'}
        except (OSError, ValueError, RuntimeError) as error:
            return {'accepted': False, 'reason': str(error)}
