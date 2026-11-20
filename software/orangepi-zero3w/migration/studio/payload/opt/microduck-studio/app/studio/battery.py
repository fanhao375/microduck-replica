"""电池电量估算（battery-plan.md v2 定稿）：只警告，不做任何自动动作。

鸭子上没有电量计、没有 ADC，唯一的测量是 15 颗舵机各自上报的供电电压（0.1 V 一格，舵机直接吃电池原压）。
所以电量只能「电压查表」：静置时准，带载时电压被拉低就偏低。这里的做法（审查 B-R1…B-R15 + 用户 10-18 拍板）：

    每秒一个读数   新鲜、3.0–9.5 V 的那几颗里去掉最高 3 颗、最低 3 颗再平均（不足 9 颗取中位，不足 5 颗这一秒不算）
    三类状态       rest 卸力 · light 扭矩开但没在动（保持、站着、坐着）· heavy 走路 / 做动作 / 摔了
    轻松段         rest、light 连续 ≥ 30 秒；每秒取它最近 30 秒的中位，记一个「轻松读数」
    估算           最近 5 分钟里最高的那个轻松读数（= 最轻松的那会儿）+ 读数校准 → 除以 2 查表；
                   5 分钟里一直没停下 → 最近 30 秒的中位直接查表，写明「走路中，按负载电压粗估，偏低」（粗估最多出黄条）
    起步           开机 / Studio 重启 / 读不到电压超过 10 秒以后，头 30 秒只显示「估算中」，不出黄红
    显示           整数，跟上一次差不到 2% 不动（死区）
    警告           条件连续 60 秒才亮，回到线上方 3% 并连续 60 秒才收；
                   负载线：最近 10 秒中位（校准后）低于 6.9 V 连续 30 秒 → 最醒目的一条，回到 7.0 V 以上 60 秒才收

纯计算：不碰网络、不碰总线、不起线程，时间从外面传进来（测试里能快进）。日志由调用方拿 feed() 返回的事件去记
（log_events），设置文件的读写也在这里（原子写；坏了改名 .bad、用默认值、记 warn）。
"""
import json
import math
import os
import tempfile
from collections import deque

LOG_CAT = "电池"

# 2S 锂离子 18650（NP-F550 等）静置开路电压 → 电量。几种常见 18650 曲线揉出来的折中，形状偏钴酸锂：
# 同样电压下三元电芯的电量更高，所以这张表偏保守（报得比实际低），误差约 ±15%（battery-plan.md §1.1、审查 B-R5）
OCV_TABLE = ((4.20, 100), (4.15, 95), (4.10, 89), (4.05, 83), (4.00, 77), (3.95, 70), (3.90, 63), (3.85, 55),
             (3.80, 46), (3.75, 37), (3.70, 28), (3.65, 19), (3.60, 12), (3.55, 8), (3.50, 5), (3.40, 2),
             (3.30, 1), (3.00, 0))
CELLS = 2                       # HD-1910 最高 8.4 V：只能用 2S
V_MIN, V_MAX = 3.0, 9.5         # 一颗舵机的读数在这之外就当没读到
RD_EMPTY_V, RD_FULL_V = 6.6, 8.2    # robotd 自己的直线（fork duck-control/src/model.rs:118-137），只拿来对照

ESTIMATING_S = 30       # 起步后头 30 秒只显示「估算中」（B-R2）
CALM_S = 30             # 轻松段至少这么长才算数（B-R4：60 → 30）
CALM_WINDOW_S = 300     # 「最近 5 分钟最轻松的那会儿」
LOAD_ROUGH_S = 30       # 没有轻松段时，粗估用最近 30 秒的中位
GAP_S = 10.0            # 15 颗都读不到电压超过 10 秒 → 重新起步（B-R4）
RUN_GAP_S = 10.0        # 轻松段里两个样本隔得超过这个就断开
KEEP_S = CALM_WINDOW_S + 60
WARN_HOLD_S = 60        # 黄 / 红：持续 60 秒才亮、才收
HYST_PCT = 3
LOW_MED_S = 10          # 负载线看最近 10 秒的中位
LOW_HOLD_S = 30         # 负载线：持续 30 秒才亮
LOW_CLEAR_HOLD_S = 60   # 回到线上方 0.1 V、持续 60 秒才收
LOW_CLEAR_V = 0.1
DEADBAND_PCT = 2
GLITCH_V = 1.0          # 这一秒比最近 60 秒中位低 1.0 V 以上：记一条（分清是真掉电还是读坏了，B-R4）。
                        # 不用审查说的 0.5 V：一号鸭走路时一秒的读数比中位低 0.5–0.8 V 很常见（10-12 15:32 那场 12 次），那是真负载
GLITCH_EVERY_S = 60     # 最多一分钟记一条
JUMP_PCT = 10           # 估算比上次高 10% 以上：记一条「换了电池或充过电」

# robot.state 里的 policy 标签：这些是「没在做动作」（fork robotd main.rs:3706-3720、control.rs:592）
CALM_LABELS = {"held", "walk", "stand", "homing", "sit"}
CLS_CN = {"rest": "卸力", "light": "站着 / 坐着", "heavy": "走路 / 做动作"}

MAINTENANCE = "放空了尽快充；长期不用存一半电（约 7.4–7.6 V）"
WARN_ONLY = "只警告：没电时鸭子不会自己停，用完关电源、拔电池（挂着卸力也在耗电）"


# ---------------- 查表 ----------------

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def soc_from_cell(vc):
    """每芯电压 → 电量 %（0–100，浮点；格子之间线性插值，两头夹住）。"""
    if not _num(vc):
        return None
    if vc >= OCV_TABLE[0][0]:
        return 100.0
    for (hi_v, hi_p), (lo_v, lo_p) in zip(OCV_TABLE, OCV_TABLE[1:]):
        if lo_v <= vc <= hi_v:
            return lo_p + (vc - lo_v) / (hi_v - lo_v) * (hi_p - lo_p)
    return 0.0


def soc_from_pack(v, cells=CELLS):
    return soc_from_cell(v / cells) if _num(v) else None


def linear_pct(v):
    """robotd 的算法：6.6 V = 0%、8.2 V = 100%、中间直线（带载口径，低电量那头偏高）。"""
    if not _num(v) or v <= 0:
        return None
    return max(0.0, min(1.0, (v - RD_EMPTY_V) / (RD_FULL_V - RD_EMPTY_V))) * 100


# ---------------- 一秒一个读数 ----------------

def trimmed(volts, fresh=None):
    """15 颗电压 → 这一秒的读数：(值或 None, 用了几颗, [(下标, 电压)] 有效的)。
    有效 = 是有限的数、在 3.0–9.5 V 之间、fresh 给了的话那一颗是新鲜的。
    ≥ 9 颗去掉最高最低各 3 颗再平均；5–8 颗取中位；不足 5 颗回 None。"""
    valid = []
    for i, v in enumerate(volts or ()):
        if fresh is not None and (i >= len(fresh) or fresh[i] is not True):
            continue
        if _num(v) and V_MIN <= v <= V_MAX:
            valid.append((i, float(v)))
    n = len(valid)
    if n < 5:
        return None, n, valid
    xs = sorted(v for _, v in valid)
    if n >= 9:
        mid = xs[3:-3]
        return sum(mid) / len(mid), n, valid
    return _median(xs), n, valid


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def classify(torque_on, moving=False, fallen=False, label=None):
    """rest / light / heavy；扭矩读不到回 None（这一秒不算轻松，也不算负载线以外的任何东西）。"""
    if torque_on is False:
        return "rest"
    if torque_on is not True:
        return None
    if moving or fallen or (isinstance(label, str) and label not in CALM_LABELS):
        return "heavy"
    return "light"


def classify_robotd(bus, state):
    """robotd 的 busStatus + robot.state → 状态类别。扭矩看 native.torque_state_confirmed（回读确认的，不是命令的）；
    动没动看 move.applied 非 0、policy 标签是不是做动作（踢球、翻滚、limp_fall…）、safety.fallen。"""
    native = (bus or {}).get("native") if isinstance((bus or {}).get("native"), dict) else {}
    torque = native.get("torque_state_confirmed")
    st = state if isinstance(state, dict) else {}
    applied = (st.get("move") or {}).get("applied") if isinstance(st.get("move"), dict) else None
    moving = isinstance(applied, list) and any(_num(x) and abs(x) > 1e-3 for x in applied)
    fallen = (st.get("safety") or {}).get("fallen") is True if isinstance(st.get("safety"), dict) else False
    return classify(torque if isinstance(torque, bool) else None, moving, fallen, st.get("policy"))


def robotd_volts(bus):
    """busStatus → (15 颗电压, 15 颗 fresh)。读不到回 (None, None)。"""
    native = (bus or {}).get("native") if isinstance((bus or {}).get("native"), dict) else {}
    joints = native.get("joints")
    if not isinstance(joints, list) or not joints:
        return None, None
    js = [j if isinstance(j, dict) else {} for j in joints]
    return [j.get("volts") for j in js], [j.get("feedback_fresh") is True for j in js]


def classify_servo_session(rows):
    """调舵机时 Studio 自己占着总线：有一颗扭矩开着就算 light，全关算 rest，读不到回 None。"""
    flags = [r.get("torque") for r in rows or () if isinstance(r, dict)]
    known = [bool(f) for f in flags if f is not None and f in (0, 1)]   # servo.py 存的是 0 / 1（也认 True / False）
    if not known:
        return None
    return "light" if any(known) else "rest"


def servo_session_volts(rows):
    """servo.snapshot() 的 rows → (电压, fresh)。"""
    rs = [r if isinstance(r, dict) else {} for r in rows or ()]
    if not rs:
        return None, None
    return [r.get("volt") for r in rs], [r.get("fresh") is True for r in rs]


# ---------------- 设置 ----------------

SETTINGS_FILE = "battery-settings.json"
SETTINGS_VERSION = 1
PRESETS = {"2s_liion": "2S 锂电（NP-F550 等）", "bench": "台架电源（不算电量）"}
DEFAULTS = {"preset": "2s_liion", "warn_pct": 30, "red_pct": 15, "load_floor_v": 6.9, "cal_offset_v": 0.0}
RANGES = {"warn_pct": (15, 60), "red_pct": (5, 40), "load_floor_v": (6.6, 7.4), "cal_offset_v": (-0.5, 0.5)}
LABELS = {"preset": "电池型号", "warn_pct": "提醒线", "red_pct": "红线", "load_floor_v": "负载电压线", "cal_offset_v": "读数校准"}
CAL_CONFIRM_V = 0.3     # 校准差超过 ±0.3 V 先问一句（多半是量错了，B-R11）


class SettingsError(ValueError):
    pass


def _check_item(k, v):
    """一项合不合法：回 (规整后的值, None) 或 (None, 原因)。"""
    if k == "preset":
        return (v, None) if v in PRESETS else (None, f"只能是 {'、'.join(PRESETS)}")
    lo, hi = RANGES[k]
    if not _num(v):
        return None, "要是数字"
    if k in ("warn_pct", "red_pct"):
        if float(v) != int(v):
            return None, "要是整数"
        v = int(v)
    else:
        v = round(float(v), 2)
    if not lo <= v <= hi:
        return None, f"要在 {lo}–{hi} 之间"
    return v, None


def validate_settings(values, base=None):
    """把 values（可以只给几项）合到 base（默认 DEFAULTS）上：回 (新设置, [错误……])。有错误时新设置不能用。
    不认识的项算错（页面发错了要知道）；红线至少比提醒线低 5%。"""
    out = dict(DEFAULTS if base is None else base)
    errors = []
    if not isinstance(values, dict):
        return out, ["设置要是一个对象"]
    for k, v in values.items():
        if k not in DEFAULTS:
            errors.append(f"不认识的设置项「{k}」")
            continue
        good, why = _check_item(k, v)
        if why:
            errors.append(f"{LABELS[k]}{why}（收到 {v!r}）")
        else:
            out[k] = good
    if not errors and out["red_pct"] > out["warn_pct"] - 5:
        errors.append(f"红线（{out['red_pct']}%）要比提醒线（{out['warn_pct']}%）至少低 5%")
    return out, errors


def settings_path(state_dir):
    return os.path.join(state_dir or ".", SETTINGS_FILE)


def load_settings(state_dir, log=None):
    """读设置。没有文件 = 默认值。文件坏了（不是 JSON、顶层不是对象）：改名成 .bad 留着、用默认值、记一行 warn；
    个别项不对：那几项用默认值、记一行 warn；不认识的项不管（以后的版本加的）。永远不抛异常。"""
    p = settings_path(state_dir)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"顶层不是对象（{type(data).__name__}）")
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError) as e:
        bad = p + ".bad"
        try:
            os.replace(p, bad)
        except OSError:
            bad = None
        if log is not None:
            log.warn("电池设置读不出来，用默认值", cat=LOG_CAT, step=p, raw=f"{type(e).__name__}: {e}",
                     hint=(f"坏文件留在 {bad}；去设置页重新存一次就好" if bad else "坏文件改名也没成功，看看 state 目录的权限"))
        return dict(DEFAULTS)
    out, wrong = dict(DEFAULTS), []
    for k in DEFAULTS:
        if k in data:
            good, why = _check_item(k, data[k])
            if why:
                wrong.append(f"{LABELS[k]}={data[k]!r}（{why}）")
            else:
                out[k] = good
    if out["red_pct"] > out["warn_pct"] - 5:
        wrong.append(f"红线 {out['red_pct']}% 没比提醒线 {out['warn_pct']}% 低 5%")
        out["warn_pct"], out["red_pct"] = DEFAULTS["warn_pct"], DEFAULTS["red_pct"]
    if wrong and log is not None:
        log.warn("电池设置里有几项不对，那几项用默认值", cat=LOG_CAT, step=p, raw="；".join(wrong),
                 hint="多半是手改过；去设置页重新存一次就好")
    return out


def save_settings(state_dir, settings):
    """整份写回（先写临时文件再改名，写一半断电不会把旧的弄坏）。先校验，不合法抛 SettingsError；写不进去也抛。"""
    good, errors = validate_settings(settings, DEFAULTS)
    if errors:
        raise SettingsError("；".join(errors))
    if not state_dir:
        raise SettingsError("这只鸭子没配 state 目录，存不了")
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".battery-settings-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dict(good, v=SETTINGS_VERSION), f, ensure_ascii=False, indent=1)
        os.replace(tmp, settings_path(state_dir))
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SettingsError(f"存不进去：{type(e).__name__}: {e}") from None
    return good


def settings_changes(old, new):
    """给日志看的一句：「红线 15% → 20%；读数校准 +0.00 → +0.12 V」。没变回空串。"""
    unit = {"warn_pct": "%", "red_pct": "%", "load_floor_v": " V", "cal_offset_v": " V"}
    parts = []
    for k in DEFAULTS:
        a, b = old.get(k), new.get(k)
        if a != b:
            fa = PRESETS.get(a, a) if k == "preset" else (f"{a:+.2f}" if k == "cal_offset_v" else a)
            fb = PRESETS.get(b, b) if k == "preset" else (f"{b:+.2f}" if k == "cal_offset_v" else b)
            parts.append(f"{LABELS[k]} {fa}{unit.get(k, '')} → {fb}{unit.get(k, '')}")
    return "；".join(parts)


# ---------------- 估算器 ----------------

class _Latch:
    """持续 on_s 秒才亮、持续 off_s 秒才收的开关。"""

    def __init__(self, on_s, off_s):
        self.on_s, self.off_s = on_s, off_s
        self.on = False
        self._since = None

    def step(self, now, cond_on, cond_off):
        """回 "on" / "off"（这一拍刚变的）或 None。"""
        if not self.on:
            if cond_on:
                self._since = now if self._since is None else self._since
                if now - self._since >= self.on_s:
                    self.on, self._since = True, None
                    return "on"
            else:
                self._since = None
        else:
            if cond_off:
                self._since = now if self._since is None else self._since
                if now - self._since >= self.off_s:
                    self.on, self._since = False, None
                    return "off"
            else:
                self._since = None
        return None

    def reset(self):
        self.on, self._since = False, None


def _ev(level, msg, hint=None, raw=None, ctx=None, step=None):
    return {"level": level, "msg": msg, "hint": hint, "raw": raw, "ctx": ctx, "step": step}


class Estimator:
    """一只鸭子一个。由一个单独的 1 Hz 循环喂（不在 summarize 里喂：那里每个页面每秒跑 10 次，审查 B-R7），
    summarize 只调 view()。feed() 按样本的 key（busStatus 的采样时间）去重，同一份数据喂几遍都只算一次。"""

    def __init__(self, settings=None):
        self.settings = dict(DEFAULTS)
        if settings:
            self.set_settings(settings)
        self._ref_pct = None        # 重新起步前最后显示的电量（只在内存里）：判「换了电池或充过电」
        self._reset(None)

    # ---- 状态 ----

    def _reset(self, now):
        self.samples = deque()      # (t, 原始读数 V, cls)；原始 = 没加校准，校准在用的时候加，改设置马上生效
        self.calm = deque()         # (t, 最近 30 秒中位, 主要的 cls)
        self.started_at = None
        self.last_valid_t = None
        self.last_feed_t = None
        self.last_key = None
        self.run_start = None
        self.run_last_t = None
        self.shown = None
        self.mode = "no_data"
        self.est = None             # (pct 浮点, 依据的 V（已校准）, 依据：calm / load, cls, 依据的时间)
        self.spread = None
        self.started_logged = False
        self.warn = _Latch(WARN_HOLD_S, WARN_HOLD_S)
        self.red = _Latch(WARN_HOLD_S, WARN_HOLD_S)
        self.low = _Latch(LOW_HOLD_S, LOW_CLEAR_HOLD_S)
        self.low_min = None
        self.glitch_at = None
        self.n_used = 0
        self.source = None

    def set_settings(self, settings):
        good, errors = validate_settings(settings, DEFAULTS)
        if errors:
            raise SettingsError("；".join(errors))
        self.settings = good

    @property
    def off(self):
        return self.settings["cal_offset_v"]

    # ---- 喂数据 ----

    def feed(self, now, volts=None, fresh=None, cls=None, key=None, source="robotd"):
        """每秒调一次。volts=None 或 key 跟上次一样 = 这一秒没有新数据（照样走计时：读不到 10 秒要重新起步）。
        回这一拍要记的日志事件（log_events 去记）。"""
        events = []
        new = volts is not None and (key is None or key != self.last_key)
        if new and key is None and self.last_feed_t is not None and now - self.last_feed_t < 0.5:
            new = False             # 没给 key 时按时间去重：同一秒喂两次只算一次
        if new:
            self.last_key, self.last_feed_t = key, now
            v, n, valid = trimmed(volts, fresh)
            self.n_used = n
            if v is not None:
                events += self._add(now, v, cls, valid, volts, source)
        if self.last_valid_t is not None and now - self.last_valid_t > GAP_S:
            if self.started_at is not None:
                events.append(_ev("warn", f"15 颗舵机都读不到电压超过 {GAP_S:.0f} 秒：电量重新估算",
                                  hint="舵机断电了（拔电池、换电池）或者总线断了；读数回来后先显示「估算中」30 秒",
                                  ctx={"last_pct": self.shown}))
                if self.shown is not None:
                    self._ref_pct = self.shown
            self._reset(now)
        events += self._evaluate(now)
        return events

    def _add(self, now, v, cls, valid, volts, source):
        events = []
        if self.started_at is None:
            self.started_at = now
        # 毛刺：这一秒比最近 60 秒中位低 0.5 V 以上（中位本身挡得住，这里只是记下来，带 15 颗原值）
        recent = [x for t, x, _ in self.samples if now - t <= 60]
        if len(recent) >= 10:
            med = _median(recent)
            if v < med - GLITCH_V and (self.glitch_at is None or now - self.glitch_at >= GLITCH_EVERY_S):
                self.glitch_at = now
                raw = [round(x, 2) if _num(x) else x for x in (volts or [])]
                # 文字固定、数放在原文里：Studio 日志 1 分钟内同一句只记一次
                events.append(_ev("warn", "电压读数一下子掉了 1 V 以上",
                                  hint="一两秒的话不影响电量估算；经常出现要查电池接头和总线（看 15 颗原值分得清是真掉电还是读坏了）",
                                  raw=f"这一秒 {v:.2f} V，最近一分钟中位 {med:.2f} V；15 颗：{raw}",
                                  ctx={"cls": cls, "source": source}))
        self.samples.append((now, v, cls))
        self.last_valid_t = now
        self.source = source
        lo = min(valid, key=lambda x: x[1])
        hi = max(valid, key=lambda x: x[1])
        self.spread = {"min": lo[1], "min_i": lo[0], "max": hi[1], "max_i": hi[0], "n": len(valid)}
        # 轻松段：rest / light 连续；中间隔太久或者来了别的状态就断
        if cls in ("rest", "light"):
            if self.run_start is None or self.run_last_t is None or now - self.run_last_t > RUN_GAP_S:
                self.run_start = now
            self.run_last_t = now
            if now - self.run_start >= CALM_S:
                win = [(x, c) for t, x, c in self.samples if t >= now - CALM_S and t >= self.run_start]
                if len(win) >= CALM_S // 2:
                    nrest = sum(1 for _, c in win if c == "rest")
                    self.calm.append((now, _median([x for x, _ in win]), "rest" if nrest * 2 >= len(win) else "light"))
        else:
            self.run_start = self.run_last_t = None
        while self.samples and now - self.samples[0][0] > KEEP_S:
            self.samples.popleft()
        while self.calm and now - self.calm[0][0] > CALM_WINDOW_S:
            self.calm.popleft()
        return events

    # ---- 算 ----

    def _evaluate(self, now):
        events = []
        s = self.settings
        if self.started_at is None:
            self.mode, self.shown, self.est = "no_data", None, None
            return events
        bench = s["preset"] == "bench"
        warming = now - self.started_at < ESTIMATING_S
        while self.calm and now - self.calm[0][0] > CALM_WINDOW_S:
            self.calm.popleft()
        prev_mode = self.mode
        if bench:
            self.mode, self.shown, self.est = "bench", None, None
        elif warming:
            self.mode, self.shown, self.est = "estimating", None, None
        else:
            if self.calm:
                t, v, c = max(self.calm, key=lambda x: x[1])
                vv = v + self.off
                self.mode, self.est = "calm", (soc_from_pack(vv), vv, "calm", c, t)
            else:
                win = [x for t, x, _ in self.samples if now - t <= LOAD_ROUGH_S]
                if win:
                    vv = _median(win) + self.off
                    self.mode, self.est = "load_rough", (soc_from_pack(vv), vv, "load", None, now)
            if self.est is not None:
                pct = self.est[0]
                if self.shown is None or prev_mode != self.mode or abs(pct - self.shown) >= DEADBAND_PCT:
                    self.shown = int(round(pct))
                events += self._log_estimate(now)

        # 警告：起步的 30 秒里不判（B-R2）；台架电源不判百分比
        if warming:
            return events
        pct = self.shown
        w, r = s["warn_pct"], s["red_pct"]
        have = pct is not None and self.mode in ("calm", "load_rough")
        ch = self.warn.step(now, have and pct <= w, (not have) or pct >= w + HYST_PCT)
        if ch == "on":
            events.append(_ev("warn", f"电量到提醒线 {w}%：现在约 {pct}%",
                              hint="准备充电；" + self._basis_short(), ctx=self._ctx()))
        elif ch == "off":
            events.append(_ev("info", f"电量回到约 {pct}%，提醒收起" if pct is not None else "电量提醒收起", ctx=self._ctx()))
        # 粗估（一直在走、没停下过）最多出黄条：负载电压查表偏低，红条只认轻松段的估算或负载线（避免狼来了）
        ch = self.red.step(now, have and self.mode == "calm" and pct <= r, (not have) or pct >= r + HYST_PCT)
        if ch == "on":
            events.append(_ev("error", f"电量到红线 {r}%：现在约 {pct}%",
                              hint="该充电了；" + WARN_ONLY, ctx=self._ctx()))
        elif ch == "off":
            events.append(_ev("info", f"电量回到约 {pct}%，红线收起" if pct is not None else "电量红线收起", ctx=self._ctx()))
        # 负载线：最近 10 秒中位（校准后）低于设定值，持续 30 秒
        m10 = self.volts_now(now)
        floor = s["load_floor_v"]
        if m10 is not None and m10 < floor:
            self.low_min = m10 if self.low_min is None else min(self.low_min, m10)
        ch = self.low.step(now, m10 is not None and m10 < floor, m10 is not None and m10 >= floor + LOW_CLEAR_V)
        if ch == "on":
            events.append(_ev("error", f"电压很低：最近 {LOW_HOLD_S} 秒都在 {floor:.1f} V 以下（最低 {self.low_min:.2f} V）",
                              hint="先停下、坐下，准备充电；" + WARN_ONLY, ctx=self._ctx()))
        elif ch == "off":
            events.append(_ev("info", f"电压回到 {m10:.2f} V，负载线提醒收起", ctx=self._ctx()))
            self.low_min = None
        if not self.low.on and not (m10 is not None and m10 < floor):
            self.low_min = None
        return events

    def _log_estimate(self, now):
        """起步第一次出数记一条；重新起步（读不到电压 10 秒以上）以后第一次轻松段的估算比断之前高 10% 以上，
        记一条「换了电池或充过电」。连续读着的时候不判：从保持换到卸力，同一块电池也能高 20%（10-12 15:32 那场）。"""
        events = []
        pct, vv, kind, c, _ = self.est
        if not self.started_logged:
            self.started_logged = True
            events.append(_ev("info", f"电量估算起步：{self._basis_short()} → 约 {int(round(pct))}%",
                              hint=f"查表估的，误差约 ±15%；读数校准 {self.off:+.2f} V", ctx=self._ctx()))
        ref = self._ref_pct
        if ref is not None and kind == "calm":
            if pct - ref >= JUMP_PCT:
                events.append(_ev("info", f"电量估算比断电前高了 {pct - ref:.0f}%：换了电池或充过电",
                                  hint=f"约 {ref:.0f}% → {pct:.0f}%（{self._basis_short()}）", ctx=self._ctx()))
            self._ref_pct = None
        return events

    # ---- 读结果 ----

    def volts_now(self, now):
        """最近 10 秒中位（加了校准），给小字和负载线用。不足 5 个样本回 None。"""
        win = [x for t, x, _ in self.samples if now - t <= LOW_MED_S]
        return _median(win) + self.off if len(win) >= 5 else None

    def level(self):
        if self.red.on or self.low.on:
            return "error"
        return "warn" if self.warn.on else None

    def current_cls(self):
        return self.samples[-1][2] if self.samples else None

    def _basis_short(self):
        if self.est is None:
            return "还没有读数"
        _, vv, kind, c, _ = self.est
        if kind == "calm":
            return f"{'卸力' if c == 'rest' else '站着 / 坐着'}时 {vv:.2f} V"
        return f"走路中 {vv:.2f} V（负载电压，偏低）"

    def _ctx(self):
        return {"mode": self.mode, "pct": self.shown, "cls": self.current_cls(), "source": self.source,
                "basis_v": round(self.est[1], 3) if self.est else None, "cal": self.off,
                "calm_points": len(self.calm), "samples": len(self.samples)}

    def view(self, now, rd=None, names=None, shutdown=None):
        """给页面的：电池格、详情弹窗、待办都从这里拿。rd：robotd health 的 battery（{volts, percent}）；
        names：15 颗的中文名（按 volts 的顺序）；shutdown：robotd 配置的 battery_empty_shutdown（onboard.read_battery_shutdown）。"""
        s = self.settings
        v10 = self.volts_now(now)
        cls = self.current_cls()
        rd = rd if isinstance(rd, dict) else {}
        rd_v = rd.get("volts") if _num(rd.get("volts")) else None
        rd_pct = rd.get("percent") if _num(rd.get("percent")) else (linear_pct(v10 - self.off) if v10 is not None else None)
        basis = None
        if self.est is not None:
            pct, vv, kind, c, t = self.est
            basis = {"kind": kind, "cls": c, "volts": round(vv, 2), "cell": round(vv / CELLS, 3),
                     "age_s": round(max(0.0, now - t)), "pct": round(pct, 1)}
        why = {
            "no_data": "读不到电压",
            "estimating": "估算中，刚开始读",
            "bench": "台架电源，不算电量",
            "load_rough": "走路中，按负载电压粗估，偏低",
        }.get(self.mode)
        if self.mode == "calm":
            why = ("静置" if cls == "rest" else "站着 / 坐着" if cls == "light"
                   else "走路中，按停下时的电压估" if cls == "heavy" else "按停下时的电压估")
        detail = self._detail(now, v10, cls, rd_v, rd_pct, names, shutdown)
        return {
            "pct": self.shown,
            "mode": self.mode,
            "level": self.level(),
            "warn": self.warn.on,
            "red": self.red.on,
            "low_volt": self.low.on,
            "volts": round(v10, 2) if v10 is not None else None,
            "cls": cls,
            "why": why,
            "basis": basis,
            "rd_volts": round(rd_v, 2) if rd_v is not None else None,
            "rd_pct": round(rd_pct) if rd_pct is not None else None,
            "spread": self._spread(names),
            "warn_pct": s["warn_pct"], "red_pct": s["red_pct"], "load_floor_v": s["load_floor_v"],
            "cal_offset_v": self.off, "preset": s["preset"], "source": self.source, "shutdown": shutdown,
            "estimating_left_s": (max(0, int(math.ceil(ESTIMATING_S - (now - self.started_at))))
                                  if self.mode == "estimating" else None),
            "detail": detail,
        }

    def _spread(self, names):
        sp = self.spread
        if not sp:
            return None
        nm = lambda i: names[i] if names and i < len(names) else f"第 {i + 1} 颗"
        return {"min": round(sp["min"], 2), "min_name": nm(sp["min_i"]), "max": round(sp["max"], 2),
                "max_name": nm(sp["max_i"]), "n": sp["n"]}

    def _detail(self, now, v10, cls, rd_v, rd_pct, names, shutdown):
        """详情弹窗的几行字：这个数怎么来的（用户要能查）。"""
        s = self.settings
        lines = []
        cal = f"校准 {self.off:+.2f} V"
        if self.mode == "no_data":
            lines.append("读不到舵机电压：舵机没电（没接电池？）或者主程序没在跑")
        elif self.mode == "estimating":
            left = max(0, int(math.ceil(ESTIMATING_S - (now - self.started_at))))
            lines.append(f"刚开始读电压，{left} 秒后出数（头 {ESTIMATING_S} 秒的读数可能正好落在迈步的低谷上，先不算）")
        elif self.mode == "bench":
            lines.append("设置里选的是台架电源：只显示电压，不算电量、不出电量提醒")
        elif self.est is not None:
            pct, vv, kind, c, t = self.est
            if kind == "calm":
                ago = int(now - t)
                when = "现在" if ago < 5 else f"{ago // 60} 分钟前" if ago >= 60 else f"{ago} 秒前"
                lines.append(f"最近 5 分钟最轻松的那会儿（{CLS_CN.get(c, c)}，{when}）：{vv:.2f} V"
                             f"（15 颗去掉最高最低各 3 颗的平均，{cal}）→ 每芯 {vv / CELLS:.2f} V → 约 {pct:.0f}%")
                if c == "light":
                    lines.append("站着、坐着时舵机在用电，电压比卸力低一点，所以这个数偏低一点；卸力静置 30 秒以上最准")
            else:
                lines.append(f"最近 5 分钟一直没停下来：按走路时的电压 {vv:.2f} V（{cal}）粗估约 {pct:.0f}%。"
                             "走路会把电压拉低，所以偏低；停下站一会儿（30 秒）就会按站着的电压重估")
            lines.append("2S 锂电通用查表，误差约 ±15%，没校准时偏保守（报得比实际低）")
        if v10 is not None and self.mode != "no_data":
            lines.append(f"现在：{v10:.2f} V（{CLS_CN.get(cls, '状态不明')}）")
        if self.mode in ("calm", "load_rough"):
            lines.append(f"提醒线 {s['warn_pct']}% · 红线 {s['red_pct']}% · 电压连续 {LOW_HOLD_S} 秒低于 {s['load_floor_v']:.1f} V 也会提醒"
                         "（都只是提醒；查表的 30% 比以前主程序直线的 30% 电压高，黄条会比以前早亮）")
        elif self.mode == "bench":
            lines.append(f"电压连续 {LOW_HOLD_S} 秒低于 {s['load_floor_v']:.1f} V 会提醒（可调电源限流了、线太细时会掉下去）")
        if rd_pct is not None and self.mode != "bench":
            src = f"{rd_v:.2f} V，" if rd_v is not None else ""
            lines.append(f"主程序自己的算法：{rd_pct:.0f}%（{src}6.6–8.2 V 直线，低电量偏高）")
        sp = self._spread(names)
        if sp and self.mode != "no_data":
            lines.append(f"{sp['n']} 颗电压：最低 {sp['min']:.1f} V（{sp['min_name']}）· 最高 {sp['max']:.1f} V（{sp['max_name']}）")
        if self.mode == "bench":
            return lines                # 台架电源：没有电池，不说充电、保养
        lines.append(stop_words({"source": self.source, "shutdown": shutdown}))
        lines.append(MAINTENANCE)
        return lines

    # ---- 校准 ----

    def calibrate(self, now, measured_v):
        """「我量了电池电压」：只在卸力静置满 30 秒时收（B-R11）。回 {ok, offset, confirm, reason}；
        offset 是新的读数校准（电池端 − 舵机读数），调用方确认后再存进设置。差超过 ±0.3 V 要用户再确认一次。"""
        if not _num(measured_v) or not 6.0 <= measured_v <= 9.0:
            return {"ok": False, "offset": None, "confirm": False, "reason": "量到的电压要在 6.0–9.0 V 之间：看看是不是量错了、表笔接反了"}
        win = [(x, c) for t, x, c in self.samples if now - t <= CALM_S]
        rest_long = (self.run_start is not None and now - self.run_start >= CALM_S
                     and len(win) >= CALM_S // 2 and all(c == "rest" for _, c in win))
        if not rest_long:
            return {"ok": False, "offset": None, "confirm": False,
                    "reason": f"要先卸力静置 {CALM_S} 秒以上再量（静置时电压最稳）"}
        raw = _median([x for x, _ in win])
        off = round(measured_v - raw, 2)
        lo, hi = RANGES["cal_offset_v"]
        if not lo <= off <= hi:
            return {"ok": False, "offset": off, "confirm": False,
                    "reason": f"你量的 {measured_v:.2f} V 跟舵机读数 {raw:.2f} V 差了 {off:+.2f} V，超过 ±{hi} V：多半是量错了（量 HAT 的电池输入端）"}
        confirm = abs(off) > CAL_CONFIRM_V
        why = (f"舵机读数 {raw:.2f} V，你量的 {measured_v:.2f} V，差 {off:+.2f} V"
               + ("；差得有点多，确认是在 HAT 的电池输入端量的吗？" if confirm else ""))
        return {"ok": True, "offset": off, "confirm": confirm, "reason": why, "servo_v": round(raw, 3)}


# ---------------- 给待办和日志 ----------------

def stop_words(view):
    """没电时会不会有人让它停：只警告（用户 10-18）。调舵机时主程序停着，就算配置里开了自动关机也不会管。"""
    if view.get("source") == "servo":
        return "调舵机时主程序停着，没电它不会自己坐下；用完关电源、拔电池"
    if view.get("shutdown") is True:
        return "主程序配置里开着「没电自动坐下关机」：10 秒平均降到 6.6 V 它会坐下关机"
    return WARN_ONLY


def todo_items(view):
    """估算结果 → 待办（最要紧的在前）。只警告：文案只给建议。"""
    if not isinstance(view, dict):
        return []
    items = []
    pct, floor = view.get("pct"), view.get("load_floor_v")
    if view.get("low_volt"):
        v = view.get("volts")
        vtxt = f"：{v:.2f} V" if _num(v) else ""
        if view.get("preset") == "bench":
            items.append({"key": "battery_low_volt", "level": "error", "msg": f"电源电压很低{vtxt}",
                          "hint": f"连续 {LOW_HOLD_S} 秒低于 {floor:.1f} V；看看可调电源是不是限流了、线是不是太细太长"})
        else:
            items.append({"key": "battery_low_volt", "level": "error", "msg": f"电压很低{vtxt}",
                          "hint": f"连续 {LOW_HOLD_S} 秒低于 {floor:.1f} V；先停下、坐下，准备充电。{stop_words(view)}"})
    rough = view.get("mode") == "load_rough"
    tail = "（走路中按负载电压粗估，偏低）" if rough else ""
    if view.get("red") and _num(pct):
        items.append({"key": "battery", "level": "error", "msg": f"电量只剩约 {pct}%",
                      "hint": f"该充电了。{stop_words(view)}"})
    elif view.get("warn") and _num(pct):
        items.append({"key": "battery", "level": "warn", "msg": f"电量约 {pct}%{tail}",
                      "hint": "准备充电；电压查表估的，误差约 ±15%，没校准时偏低"})
    return items


def log_events(log, events):
    """feed() 回的事件写进 Studio 日志（分类「电池」）。log 为 None 时什么都不做。"""
    if log is None:
        return
    for e in events or ():
        log.write(e["msg"], cat=LOG_CAT, level=e["level"], step=e.get("step"), raw=e.get("raw"),
                  hint=e.get("hint"), ctx=e.get("ctx"))
