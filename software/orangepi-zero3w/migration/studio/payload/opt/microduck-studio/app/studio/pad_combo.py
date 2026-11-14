"""手柄页的「动作」和「组合动作」：数据、校验、存取、展开成时间表。这里全是纯函数，不碰 robotd。

一个组合动作 = 名字 + 几步，一步接一步做（下一步等上一步做完）。每一步是下面几种之一，参数都夹到安全范围：

| 步               | 发给 robotd 的                                    | 出处（官方仓库 microduck/） |
|------------------|---------------------------------------------------|-----------------------------|
| nod   点头       | robot.head 通知，head_pitch 来回，25 Hz            | duck-ipc-proto lib.rs:533、2058-2065；robotd main.rs:3637-3640 |
| shake 摇头       | robot.head 通知，head_yaw 来回                      | 同上 |
| tilt  歪头       | robot.head 通知，head_roll 停一会儿再回正           | 同上 |
| mouth 张嘴       | robot.mouth 通知 {open}，停一会儿再合上             | lib.rs:589、2269-2275；main.rs:3648-3651 |
| chatter 说话     | robot.mouth 一张一合                               | 同上 |
| sound 叫一声     | robot.sound 请求 {tag}（有回话：没声音会拒绝）       | lib.rs:590-600、2094-2140；main.rs:4300-4319 |
| skill 官方技能   | robot.do 请求 {skill}（有回话）                     | lib.rs:579-581、2212-2239；main.rs:4219-4255 |
| wait  停一下     | 什么都不发                                         | — |

不放「走路」：组合动作里不带 robot.move。「按住才走」只靠摇杆保证，组合动作再按住也只是原地动作。
不放 sit_toggle：它会改变姿势（坐下 / 站起），用专门的「坐下」按钮。

头和嘴的几个事实（决定了结束时一定要「回正、合嘴」）：
- robot.head 的目标没有过期：最后一次发的值一直留着（robotd intents.rs:569-588 只给 twist 算年龄）；
  默认是全 0（intents.rs:271-274），所以「回正」= 发一次全 0。
- robot.mouth 不带时间戳：客户端死了嘴就一直张着（intents.rs:144-146 原话「a mouth left open by a dead client
  is exactly what the prototype does」），所以中途打断也要发一次 open=0。
- 头的指令进的是策略的观测，不直接写舵机（lib.rs:2053-2057；padd main.rs:170-174「the network itself decides how
  far the head actually goes」），所以动头的步要求「策略在跑」。嘴在官方版只在策略在跑时才动（robotd main.rs:3093-3099），
  我们鸭子上的 fork（R17）只要站好了（含保持、坐着）就动（fork robotd main.rs:3897-3909、4779-4787），实体手柄也是每拍都发嘴、
  不看模式和策略（fork padd:1351-1363）。第三轮（pad-vpad-plan §8 V10）改成按 fork：只动嘴的步（张嘴、说话）不要求策略在跑，
  软着时 robotd 自己不动嘴；结束照样合嘴。
- 幅度、快慢是推测，没上机：点头 0.5 rad、摇头 0.6 rad、歪头 0.4 rad，要上鸭子看着调。
"""
import json
import math
import os
import re
import tempfile

TICK_S = 0.04                 # 头、嘴的通知 25 Hz（padd 是 50 Hz：padd main.rs:150-151）
NOD_AMP, SHAKE_AMP, TILT_AMP = 0.5, 0.6, 0.4     # rad，推测，要上机核
NOD_PERIOD_S, SHAKE_PERIOD_S = 0.7, 0.8
CHATTER_PERIOD_S = 0.32
MAX_STEPS = 12
MAX_TOTAL_S = 20.0
MAX_USER_COMBOS = 20
NAME_MAX = 16
COMBO_FILE = "pad-combos.json"

# robot.sound 的 tag（lib.rs:2094-2113）。wheee 要一直按着发（hold），不做成一次性的按钮
SOUNDS = {
    "chirp": "嘎（叫一声）",
    "greet": "打招呼",
    "inquire": "疑问",
    "coo": "咕咕（撒娇）",
    "alarm": "大叫",
    "peck": "告别",
}

# robot.do 的技能名 → 中文。robotd 实际有哪些看 robot.policies 的 skills（lib.rs:2338-2346）；
# ground_pick、sit_toggle 不在 skills 里，看 sitstand / ground_pick 两个槽装没装（robotd main.rs:4136-4156）
SKILL_CN = {
    "ground_pick": "低头捡",
    "roulade": "前滚翻",
    "kick_left": "左脚踢",
    "kick_right": "右脚踢",
    "roller_stand": "轮滑起身",
    "sit_toggle": "坐下 ↔ 站起",
}
# 技能做完要多久（组合动作里下一步等它）：robotd-params lib.rs:1311-1341 的默认 duration（踢 0.5 秒、滚 1 秒），
# 低头捡是相位动作、窗口 2.8 秒（原版训练配方说明）；不知道的按 2 秒等
SKILL_HOLD_S = {"kick_left": 0.8, "kick_right": 0.8, "roulade": 1.3, "ground_pick": 3.0}
SKILL_HOLD_DEFAULT_S = 2.0
NO_COMBO_SKILLS = {"sit_toggle"}
SKILL_RE = re.compile(r"^[a-z0-9_]{1,32}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")

STEP_CN = {"nod": "点头", "shake": "摇头", "tilt": "歪头", "mouth": "张嘴", "chatter": "说话（嘴一张一合）",
           "sound": "叫一声", "skill": "技能", "wait": "停一下"}
# 要「策略在跑」才有效的步（见文件头）。张嘴、说话（只动嘴）按 fork 的规矩不要（三 V10）
NEEDS_POLICY = {"nod", "shake", "tilt", "skill"}


class Refused(Exception):
    """这个操作现在不能做。原因直接给页面看（pad.py、pad_drive.py 共用这一个）。"""


# ---------------- 内置的 ----------------

# 单个动作（页面上按住就做一遍，松开就停）
ACTIONS = [
    {"id": "nod", "name": "点头", "steps": [{"do": "nod", "times": 2}]},
    {"id": "shake", "name": "摇头", "steps": [{"do": "shake", "times": 2}]},
    {"id": "tilt", "name": "歪头", "steps": [{"do": "tilt", "side": "right", "ms": 1200}]},
    {"id": "mouth", "name": "张嘴", "steps": [{"do": "mouth", "open": 0.9, "ms": 900}]},
]
# 组合动作（先内置这几个；自己编的存 state 目录的 pad-combos.json，编辑界面下一轮做）
BUILTIN_COMBOS = [
    {"id": "hello", "name": "打招呼", "steps": [{"do": "sound", "tag": "greet"}, {"do": "nod", "times": 2},
                                              {"do": "mouth", "open": 0.8, "ms": 500}]},
    {"id": "happy", "name": "开心", "steps": [{"do": "sound", "tag": "chirp"}, {"do": "chatter", "times": 3},
                                            {"do": "nod", "times": 1}]},
    {"id": "ask", "name": "疑问", "steps": [{"do": "sound", "tag": "inquire"}, {"do": "tilt", "side": "right", "ms": 1000}]},
    {"id": "no", "name": "不要", "steps": [{"do": "shake", "times": 3}, {"do": "sound", "tag": "coo"}]},
    {"id": "kicks", "name": "左右各踢一脚", "steps": [{"do": "skill", "name": "kick_left"}, {"do": "wait", "ms": 600},
                                                 {"do": "skill", "name": "kick_right"}]},
]


# ---------------- 校验 ----------------

def _num(v, lo, hi, default, what):
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Refused(f"{what}要是数字")
    return max(lo, min(hi, v))


def clean_step(raw):
    """一步 → 干净的一步（不认识的字段丢掉、数夹到范围里）。不对就抛 Refused。"""
    if not isinstance(raw, dict):
        raise Refused("每一步要是 {\"do\": …} 这样的对象")
    do = raw.get("do")
    if do == "nod" or do == "shake":
        return {"do": do, "times": int(_num(raw.get("times"), 1, 4, 2, "次数"))}
    if do == "tilt":
        side = raw.get("side", "right")
        if side not in ("left", "right"):
            raise Refused("歪头只能 left / right")
        return {"do": "tilt", "side": side, "ms": int(_num(raw.get("ms"), 300, 2000, 1000, "歪头的时间"))}
    if do == "mouth":
        return {"do": "mouth", "open": round(_num(raw.get("open"), 0.1, 1.0, 0.8, "张嘴的大小"), 2),
                "ms": int(_num(raw.get("ms"), 100, 2000, 600, "张嘴的时间"))}
    if do == "chatter":
        return {"do": "chatter", "times": int(_num(raw.get("times"), 1, 6, 3, "次数"))}
    if do == "sound":
        tag = raw.get("tag")
        if tag not in SOUNDS:
            raise Refused(f"不认识的声音：{tag}（只有 {'、'.join(SOUNDS)}）")
        return {"do": "sound", "tag": tag}
    if do == "skill":
        name = raw.get("name")
        if not isinstance(name, str) or not SKILL_RE.match(name):
            raise Refused("技能名只能是小写字母、数字、下划线")
        if name in NO_COMBO_SKILLS:
            raise Refused("坐下 / 站起会改变姿势，不能放进组合动作：用「坐下」按钮")
        return {"do": "skill", "name": name}
    if do == "wait":
        return {"do": "wait", "ms": int(_num(raw.get("ms"), 0, 3000, 300, "停的时间"))}
    raise Refused(f"不认识的一步：{do}（只能是 {'、'.join(STEP_CN)}）")


def clean_combo(raw, builtin_ids=()):
    """页面要存的组合动作 → 干净的。名字、步数、总时长都有上限；id 不能跟内置的撞。"""
    if not isinstance(raw, dict):
        raise Refused("组合动作要是对象")
    cid = raw.get("id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise Refused("编号只能是小写字母、数字、- 和 _（最多 24 个）")
    if cid in builtin_ids:
        raise Refused(f"「{cid}」是内置的，换个编号")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise Refused("要起个名字")
    name = name.strip()[:NAME_MAX]
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise Refused("至少要有一步")
    if len(steps) > MAX_STEPS:
        raise Refused(f"最多 {MAX_STEPS} 步")
    clean = [clean_step(s) for s in steps]
    total = duration(clean)
    if total > MAX_TOTAL_S:
        raise Refused(f"太长了：大约 {total:.0f} 秒，最多 {MAX_TOTAL_S:.0f} 秒")
    return {"id": cid, "name": name, "steps": clean}


# ---------------- 展开成时间表 ----------------

def step_seconds(step):
    do = step["do"]
    if do == "nod":
        return step["times"] * NOD_PERIOD_S
    if do == "shake":
        return step["times"] * SHAKE_PERIOD_S
    if do in ("tilt", "mouth", "wait"):
        return step["ms"] / 1000
    if do == "chatter":
        return step["times"] * CHATTER_PERIOD_S
    if do == "sound":
        return 0.25            # 声音自己播；稍停一下再做下一步，听起来像是先叫后动
    if do == "skill":
        return SKILL_HOLD_S.get(step["name"], SKILL_HOLD_DEFAULT_S)
    return 0.0


def duration(steps):
    return sum(step_seconds(s) for s in steps)


def head_at(step, t):
    """这一步在第 t 秒头应该在哪：(neck_pitch, head_pitch, head_yaw, head_roll)；不管头的步返回 None。"""
    do = step["do"]
    if do == "nod":
        return (0.0, NOD_AMP * math.sin(2 * math.pi * t / NOD_PERIOD_S), 0.0, 0.0)
    if do == "shake":
        return (0.0, 0.0, SHAKE_AMP * math.sin(2 * math.pi * t / SHAKE_PERIOD_S), 0.0)
    if do == "tilt":
        total = step["ms"] / 1000
        ramp = min(0.25, total / 3)
        k = min(1.0, t / ramp) if t < total - ramp else max(0.0, (total - t) / ramp)
        return (0.0, 0.0, 0.0, (TILT_AMP if step["side"] == "right" else -TILT_AMP) * k)
    return None


def mouth_at(step, t):
    """这一步在第 t 秒嘴张多大（0..1）；不管嘴的步返回 None。"""
    do = step["do"]
    if do == "mouth":
        return step["open"] if t < step["ms"] / 1000 - TICK_S else 0.0
    if do == "chatter":
        return 0.85 if (t % CHATTER_PERIOD_S) < CHATTER_PERIOD_S / 2 else 0.05
    return None


def needs_policy(steps):
    return any(s["do"] in NEEDS_POLICY for s in steps)


def describe(steps):
    """给页面看的一句话：点头 2 次 → 张嘴 → 叫一声（打招呼）"""
    out = []
    for s in steps:
        do = s["do"]
        if do in ("nod", "shake", "chatter"):
            out.append(f"{STEP_CN[do]} {s['times']} 次")
        elif do == "tilt":
            out.append("歪头" + ("（右）" if s["side"] == "right" else "（左）"))
        elif do == "mouth":
            out.append("张嘴")
        elif do == "sound":
            out.append(f"叫一声（{SOUNDS.get(s['tag'], s['tag'])}）")
        elif do == "skill":
            out.append(SKILL_CN.get(s["name"], s["name"]))
        elif do == "wait":
            out.append(f"停 {s['ms'] / 1000:g} 秒")
    return " → ".join(out)


# ---------------- 存取（state 目录的 pad-combos.json） ----------------

def load_user_combos(state_dir, log=None):
    """读用户自己编的组合动作。文件坏了、某一条不对：跳过那条、记日志，不挡别的。"""
    if not state_dir:
        return []
    path = os.path.join(state_dir, COMBO_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        if log:
            log.warn("自己编的组合动作读不出来，先不用", cat="手柄", step=path, raw=f"{type(e).__name__}: {e}",
                     hint="文件坏了的话删掉它，重新编")
        return []
    builtin = {c["id"] for c in BUILTIN_COMBOS + ACTIONS}
    out = []
    for raw in (data.get("combos") if isinstance(data, dict) else None) or []:
        try:
            out.append(clean_combo(raw, builtin))
        except Refused as e:
            if log:
                log.warn("跳过一条不对的组合动作", cat="手柄", step=path, raw=f"{raw!r:.200}：{e}")
    return out[:MAX_USER_COMBOS]


def save_user_combos(state_dir, combos):
    """整份写回（先写临时文件再改名，写一半断电不会把旧的弄坏）。"""
    if not state_dir:
        raise Refused("这只鸭子没配 state 目录，存不了")
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, COMBO_FILE)
    fd, tmp = tempfile.mkstemp(prefix=".pad-combos-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "combos": combos}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise Refused(f"存不进去：{type(e).__name__}: {e}") from None
    return path
