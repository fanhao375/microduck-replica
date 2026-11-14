"""Push-to-talk commands use Studio's existing policy and input arbitration."""
import unicodedata

from .pad_combo import Refused
from .rpc import RpcError, Timeout, Unreachable
from .voice_words import WORDS, WAKE_FORMS, NEGATIONS

# Speech only selects an existing Pad operation. Its posture/ownership gates
# remain authoritative; enabling a command never enables a movement policy.
ENABLED_COMMANDS = frozenset({'wake', 'stand', 'walk', 'sit', 'toggle', 'stop',
                              'forward', 'backward', 'left', 'right', 'roll'})

MOVES = {'forward': (.12, 0., 0.), 'backward': (-.12, 0., 0.),
         'left': (0., 0., .6), 'right': (0., 0., -.6)}
LABELS = {'wake': '疑问地嘎一声', 'forward': '向前一小步', 'backward': '向后一小步',
          'left': '向左转一下', 'right': '向右转一下', 'sit': '坐下', 'stand': '站起来', 'walk': '开始走',
          'roll': '前滚翻一次', 'stop': '停止行走'}


def _mouth_ready(pad, conn, epoch):
    """Only the independent beak motor may follow a wake call."""
    with pad._lk:
        now = pad.clock()
        robot = pad._robot or {}
        bus = robot.get('bus') or {}
        return (conn not in pad._gone and pad.motion_studio.epoch == epoch
                and pad._base_block(now) is None and pad._relax_block(now) is None
                and bus.get('phase') == 'control' and bus.get('mode') == 'motion'
                and pad._posture() in ('policy', 'walking', 'holding', 'sitting')
                and pad._pad_attached(now)[0] is False and pad._action is None
                and not any({'lt', 'rt'} & set(state.get('held', {}))
                            for state in pad._keys.values()))


def parse_command(raw):
    if not isinstance(raw, str) or not 0 < len(raw) <= 80:
        raise Refused('请说一条简短的动作口令')
    if '[unk]' in raw.lower():
        raise Refused('这句话有没听清的部分，没有执行动作，请重新说一次')
    text = ''.join(c for c in unicodedata.normalize('NFKC', raw)
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))
    # A phrase may be incomplete or contain filler. Keep the parser on the server
    # so provisional browser transcripts cannot bypass the same command rules.
    if '别走了' in text or '不要走了' in text:
        return 'stop'
    if any(word in text for word in NEGATIONS):
        raise Refused('听到了否定表达，没有执行动作；请重新说一条口令')
    found = {command for word, command in WORDS.items()
             if word not in ('走', '停', '鸭哥') and word in text}
    if not found:
        short = text
        for wake in WAKE_FORMS:
            short = short.removeprefix(wake)
        short = short.removeprefix('请').removesuffix('吧')
        if short in ('走', '停'):
            found.add(WORDS[short])
    if len(found) > 1:
        raise Refused('听到了多个不同动作，请一次说一个')
    if found:
        return found.pop()
    if any(wake in text for wake in WAKE_FORMS):
        return 'wake'
    else:
        raise Refused('没有匹配到口令，没有执行动作。试试说“站起来”或“鸭哥”')


def execute_voice(pad, args, conn, by):
    if set(args) - {'op', 'text', 'seq'}:
        raise Refused('语音请求只接收一条口令')
    command = parse_command(args.get('text'))
    seq = args.get('seq')
    if type(seq) is not int or not 0 < seq < 2**53 or conn is None:
        raise Refused('语音请求序号无效')
    if command not in ENABLED_COMMANDS:
        raise Refused('这条语音动作尚未开放，请换一条口令')
    with pad._lk:
        q = pad._seq_of(conn)
        if seq <= q.get('voice', -1):
            raise Refused('这条语音已经处理过，请重新按住说话')
        q['voice'] = seq
        if conn in pad._gone:
            raise Refused('页面已经断开，语音已取消')
        if command != 'wake' and pad._pad_attached(pad.clock())[0]:
            raise Refused('实体手柄已接管，请先关闭实体手柄再用语音控制')
        if command == 'toggle':
            sitting = (pad._policies() or {}).get('sitting')
            if type(sitting) is not bool:
                raise Refused('还没有确认坐姿，请等机器人状态恢复')
            command = 'stand' if sitting else 'sit'
    if command in MOVES:
        mapped = {'op': 'move'}
    elif command == 'wake':
        mapped = {'op': 'sound', 'tag': 'inquire'}
    elif command == 'roll':
        mapped = {'op': 'skill', 'name': 'roulade'}
    else:
        mapped = {'op': command}
    with pad._lk:
        robot = pad._robot or {}
        bus = robot.get('bus') or {}
        sound_only = (robot.get('present') is True and not robot.get('paused')
                      and pad._robot_at is not None and pad.clock() - pad._robot_at < 3
                      and bus.get('mode') == 'motion' and bus.get('phase') == 'waiting_for_bus')
    if sound_only and command != 'wake':
        raise Refused('鸭哥现在只能试叫声，等它准备好再试动作')
    if pad.motion_studio.before(mapped, conn, by) is False:
        raise Refused('动作会话已改变，请重新说话')
    if command == 'wake':
        if sound_only:
            from .speaker_test import play_inquire
            result = play_inquire()
        elif _mouth_ready(pad, conn, pad.motion_studio.epoch):
            from .speaker_test import play_inquire_with_mouth
            epoch = pad.motion_studio.epoch
            def notify(opening):
                if opening > 0 and not _mouth_ready(pad, conn, epoch):
                    return False
                try:
                    return pad._notify_quiet('robot.mouth', {'open': opening}) is not False
                except (RpcError, Timeout, Unreachable):
                    return False
            result = play_inquire_with_mouth(notify)
        else:
            result = pad._motion_cmd(mapped['op'], mapped, conn, by, label='语音：' + LABELS[command])
    elif command in MOVES or command == 'stop':
        with pad._lk:
            holder = pad._lease_holder(pad.clock())
            if holder is not None and holder['conn'] != conn:
                raise Refused('另一页面正在控制鸭哥，请等它释放控制')
        if command == 'stop':
            result = pad._stop_move(conn=conn, by=by)
        else:
            with pad._lk:
                bus = (pad._robot or {}).get('bus') or {}
                if (bus.get('homed') is not True or bus.get('policy_enabled') is not True
                        or pad._posture() not in ('policy', 'walking')
                        or (pad._policies() or {}).get('sitting') is not False):
                    raise Refused('先在手柄页站稳并点“开始走”，再说移动口令')
            pad._drive({'open': True}, conn, by)
            vx, vy, vyaw = MOVES[command]
            # Send one frame only. robotd's existing 500 ms deadman zeros velocity;
            # no browser heartbeat can extend a spoken step or replay it after reconnect.
            result = pad._move({'vx': vx, 'vy': vy, 'vyaw': vyaw}, conn, by)
    else:
        result = pad._motion_cmd(mapped['op'], mapped, conn, by, label='语音：' + LABELS[command])
    if isinstance(result, dict) and result.get('dropped'):
        raise Refused(result['dropped'])
    return {'accepted': True, 'command': command, 'label': LABELS[command], 'receipt': result}
