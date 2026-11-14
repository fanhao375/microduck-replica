"""总览上的「上手清单」，顺带主板温度格的回差、Studio 自己读的主板温度（总览重排 §5、审查 O4–O8、O14、O15）。

清单照 overview-merge-review.md 末尾「清单每一步的最终判法」表，按 §5 O14 改成 6 行（任务 11 加了第 3 步）：
    卡片头（不编号）连上鸭子
    1 舵机零位   2 IMU 方向   3 写进主程序   4 配手柄   5 测急停   6 站起来、走几步（记录还是两条：stood、walked）
每一步五种状态：ok ✓ 完成 · skip ↷ 跳过了 · todo ● 该做 / 要重做 · no ○ 没做 · unk ? 读不到。
「现在该做」= 第一个 ● / ○ 的步（跳过的、标了已完成的都算完成）。后面的不锁，照样能做。

**每一行任何时候都能「去做」「已完成」「跳过」**（用户 10-20 真机验收：「这个逻辑绝对有问题，允许人家直接跳过」
「可能有人之前做过重新走一遍」；10-18 就定过每步都能跳。二号鸭零位早校过，以前却被锁着逼重校）：
- 「已完成」= 我以前做过：记 done.<步> = {at, by, fp}，这一行 ✓、写「你标了已完成（时间）」；
- 「跳过」= 先不做：记 skipped.<步> = {at, by, fp}，这一行 ↷；
- 两种互斥（标一种就删掉另一种）；标过的行给「重新做」+「取消」，Studio 自己有记录的 ✓ 只给「重新做」。
fp 跟这一步的正式记录绑同样的东西（舵机：关节指纹；IMU：芯片 + 方向；写进主程序、站走：标定指纹；测急停：
手柄 MAC + 主程序 build；配手柄：不绑）。指纹变了标的作废、那行回 ●（记录留着，指纹变回来又算）；
标的时候指纹读不到就先记 null，tick 第一次读到时绑上（照 C2：读不到不作废）。
**不再锁，但有后果的要点两次**：主程序明说没做完（hard：标定没做完、IMU 方向没确认、不让上力）、读不到（?）、
测出了问题（found：IMU 站着核过差 8.5°、急停测过但现在不管用、两套零位打架，第三批代码审查 D5）、测急停（审查 D11）、
指纹读不到、调舵机时（清单冻着，标了要交还以后才显示）——点「已完成」「跳过」都是两步，第二下按钮上写后果
（「确定跳过？主程序说标定没做完：跳过了鸭子也站不起来」这种），后端没收到 confirmed 也拒。
记录里的 ack 记下第二下确认过哪几种：没确认过 hard 的标记在主程序明说不行时不算（「标的不管用」）；
没确认过 found 的标记在测出问题以后作废、从记录里删掉（以前「跳过」以后会一直显示「你说以前做过」，把问题藏起来）。
✓ 和跳过了的行都能「重新做」：就是那一步的「去做」，旧记录留着，新结果出来才替换。
第 1 步标了已完成 / 跳过：第 3 步准备情况里「舵机零位」算满足（commit.py 读 user_mark）；第 2 步标了、主程序里方向没确认、
IMU 页也没新校准：第 3 步按默认装法写正在用的那颗 IMU 并标成已确认（任务 17，commit.py 的 _default_imu）。

**主程序的字段只当「没完成」的证据**（审查 O4）：fork 的运动模式要 15 颗都标了、IMU 也 verified 才肯开总线
（fork robotd main.rs:1510-1519），所以 control 阶段这两个字段恒为 true；而这个 true 可能是安装脚本拿一号鸭的种子值
补上的（fork packaging/rebuild-config.py:75-99），也可能是手写的（二号鸭 10-17 手写了 verified = true）。
「完成」要靠 Studio 自己的记录：舵机页标定留下的 servo-calib/eeprom-*.json、用户点过的「已经校过，核对一下」（老接口，
页面现在不给这个按钮了）、IMU 页站着完整校过一次且跟主程序用的方向差 < 2°；或者用户自己标的「已完成」。

记录存 state_dir/onboarding.json（审查 O6）：先写临时文件再改名；文件坏了改名成 .bad 留着、当空的，记一行 warn。
每条记录带「它成立的条件」，条件变了显示「要重做」，记录本身不删：
    急停     {at, ms, mac, name, build}      换了手柄（MAC 不同）、主程序升级（build 不同）→ 再测一次
    站 / 走  {at, fp}                        fp = 标定指纹（15 颗 zero_raw / direction + 现在用的 IMU 方向）
    舵机核对 {at, fp}                        fp = 关节指纹（只看 15 颗 zero_raw / direction）
    IMU 核对 {at, source, mount_fp, ...}     芯片或方向改过 → 再核一次
    已完成 / 跳过 done.<步> / skipped.<步> = {at, by, fp, ack?}   fp 变了 → 不算；ack 见上
时间存 time.time()（墙钟，给人看的）；鸭子开机没联网时墙钟停在 1970，页面上年份 < 2024 写「时间不准」。
换卡：state 目录跟着卡走，新卡上自然是空的（舵机 EEPROM 里的零位还在，第 1 步点「已完成」）；
重装 Studio 不动 /var/lib 就留着；换主板不清（零位在舵机和卡上，不跟主板走）。

只在后端一处判：app 的小任务每 0.5 秒调一次 tick()，summarize 只读 view()（每个页面每秒 10 次，不能在那里写盘）。
读文件（servo-calib 目录、/sys/class/thermal）在线程里做（io()，代码审查 C9），tick 只用缓存：SD 卡一卡不能挡住同一个
事件循环里的卸力。记录文件读进来逐条校验，坏的那一条丢掉、记一行 warn，别的照用；判清单整体出错时清单写「?」和原因，
主板温度的回差单独算、不受它影响（代码审查 C1）。
"""
import hashlib
import json
import os
import re
import tempfile
import threading
import time

from .estop import STARTING_LONG_S, STARTING_PHASES
from .imu import mount_of
from .robotd import fresh_calib
from .state import JOINT_CN, MAX_AGE, STILL_STARTING, posture, starting_secs
from .startup_status import startup_wait

ONBOARD_FILE = "onboarding.json"
SERVO_IDS = (20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14)   # 跟 servo.IDS 一样（这里不 import servo：它带串口）
STOOD_S = 3.0             # 站着（holding / policy / walking 且扭矩确认开着）连续这么久算「站起来过」（审查 O6）
WALKED_S = 2.0            # walking 累计这么久算「走过」
CPU_WARN_C = 80.0         # 跟 state.CPU_WARN_C 一样：这块板待机就 77–78 °C
CPU_CLEAR_C = 77.0        # 回差：降到这以下才收（审查 O8：没有回差，仪表带和待办会来回跳、把下面的控件推上推下）
STUDIO_SCAN_S = 10.0      # servo-calib 目录多久重新扫一次
THERMAL_ROOT = "/sys/class/thermal"
# robotd 读的配置：fork 是 --params /etc/robot/feetech-ft5/robotd.toml（fork packaging/runtime.py:28、123），官方默认 /etc/robot/robotd.toml
ROBOTD_CONFIGS = ("/etc/robot/feetech-ft5/robotd.toml", "/etc/robot/robotd.toml")
ROBOTD_CFG_S = 30.0       # 多久重读一次
CPUFREQ_ROOT = "/sys/devices/system/cpu/cpufreq"
STAND = ("holding", "policy", "walking")
# 运动模式卡在「等总线」时报的是这一句（fork main.rs:1546-1552；feetech.rs:1011-1049）：标定没过、IMU 没过都报它
CALIB_ERR = re.compile(r"calibrat|motion_enabled|calibration file|IMU mounting", re.I)

TIPS = {
    "servo": "两套零位会打架：用 Studio 的舵机页标（标完，主程序文件里 15 颗的 zero_raw 都要是 2048）",
    "imu": "装歪 8.5° 一走就往前冲（一号鸭 10-12）",
    "commit": "写的时候主程序会先卸力：扶好鸭子，别让它摔了",
    "pad": "G50 Pro 先拨到 S 档",
    "estop": "每次换手柄都测一次",
    "walk": None,
}
TITLES = {"servo": "舵机零位", "imu": "IMU 方向", "commit": "写进主程序", "pad": "配手柄", "estop": "测急停", "walk": "站起来、走几步"}
ORDER = ("servo", "imu", "commit", "pad", "estop", "walk")
GO = {"servo": "servos", "imu": "imu", "commit": "commit", "pad": "pad_wizard", "estop": "estop", "walk": "remote"}   # 每一步的「去做」/「重新做」
SKIP_CHANGED = {"servo": "舵机零位", "imu": "IMU 方向（或芯片）", "commit": "标定", "estop": "手柄（或主程序版本）", "walk": "标定"}
COMMIT_TEXT = "把舵机零位、方向、IMU 方向写进主程序。出厂镜像是标定模式；写完会切到运动模式并重启（约 20 秒），以后开机直接就能站、能走，不开网页也行。"

# 「已完成」「跳过」（用户 10-20）：页面上的动作 → 记录里的键；两种互斥
KINDS = {"done": "done", "skip": "skipped"}
OTHER = {"done": "skip", "skip": "done"}
MARKED_TEXT = {"done": "你标了已完成", "skip": "跳过了（先不做）"}
MARKED_WORD = {"done": "标了已完成", "skip": "点过跳过"}
CHANGED_WORD = {"done": "标了已完成", "skip": "跳过"}
# 要点两次的第二下那句：{q} 换成「跳过」/「标成已完成」（问句），{v} 换成「跳过了」/「标成已完成」（后果）
ASK_Q = {"done": "标成已完成", "skip": "跳过"}
ASK_V = {"done": "标成已完成", "skip": "跳过了"}
# 原来不让跳的几种情况（主程序明说不行 hard、测出了问题 found）：现在都能点，第二下按钮上写后果
HARD_SERVO = "主程序说标定没做完：{v}鸭子也站不起来"
HARD_IMU = "主程序没确认 IMU 方向：{v}鸭子也站不起来"
HARD_STUCK = "主程序不让上力（标定或 IMU 方向没过）：{v}鸭子也站不起来"
UNK_WHY = "这一步现在读不到（{text}）：Studio 核对不了，就按你说的算"
FP_WHY = "{what}：Studio 先记下，读到以后再核对"
FROZEN_ASK = "确定{q}？在调舵机：清单停在调之前的样子，{v}要等交还主程序以后才显示"
FROZEN_UNDO = "确定取消？在调舵机：清单停在调之前的样子，取消了要等交还主程序以后才显示"


def fill(text, kind):
    """把问句 / 后果里的 {q}、{v} 换成这个动作的说法（用 replace：原因里可能带花括号）。"""
    return text.replace("{q}", ASK_Q[kind]).replace("{v}", ASK_V[kind]) if text else text


# ---------------- 指纹 ----------------

def _joints(calib):
    cal = (calib or {}).get("calibration") if isinstance((calib or {}).get("calibration"), dict) else None
    js = (cal or {}).get("joints")
    return [j for j in js if isinstance(j, dict)] if isinstance(js, list) else None


def _hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def joints_fp(calib):
    """15 颗的 zero_raw / direction 的指纹；读不到标定文件回 None。"""
    js = _joints(calib)
    if not js:
        return None
    return _hash(sorted([j.get("id"), j.get("zero_raw"), j.get("direction")] for j in js))


def mount_fp(source, quat):
    """芯片 + 安装方向的指纹（四元数取 4 位小数：同一份文件读两次一定一样）。"""
    if not isinstance(quat, (list, tuple)) or len(quat) != 4:
        return None
    try:
        return _hash([source, [round(float(x), 4) for x in quat]])
    except (TypeError, ValueError):
        return None


def full_fp(bus, calib):
    """标定指纹：关节 + 现在用的 IMU 方向（站起来 / 走过的记录绑它：标定改过以后要重新站、重新走）。"""
    j = joints_fp(calib)
    source, mount = mount_of(bus, calib)
    m = mount_fp(source, (mount or {}).get("quat"))
    if j is None or m is None:
        return None
    return _hash([j, m])


# ---------------- Studio 自己的舵机标定记录（servo_ops.py:426-441、612-656、735） ----------------

def studio_servo_record(state_dir):
    """非模拟、没撤销的 eeprom-*.json 里，targets 减 bad 的并集；回 {"ids": set, "time": 最近一份的时间字符串}。
    撤销会把文件改名成 undone-…（不以 eeprom- 开头，自然跳过）；模拟模式的备份在临时目录，这里看不到。"""
    d = os.path.join(state_dir or ".", "servo-calib")
    ids, latest = set(), None
    try:
        names = sorted(f for f in os.listdir(d) if f.startswith("eeprom-") and f.endswith(".json"))
    except OSError:
        return {"ids": ids, "time": None}
    for fn in names:
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("sim"):
            continue
        targets = rec.get("targets") if isinstance(rec.get("targets"), dict) else {}
        bad = rec.get("bad") if isinstance(rec.get("bad"), dict) else {}
        got = {int(k) for k in targets if str(k).isdigit()} - {int(k) for k in bad if str(k).isdigit()}
        if got:
            ids |= got
            latest = rec.get("time") or latest
    return {"ids": ids, "time": latest}


# ---------------- 主板温度：Studio 自己读（照 fork robotd/src/soc.rs 的算法） ----------------

def _read_int(path):
    try:
        with open(path, encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def read_board(thermal_root=THERMAL_ROOT, cpufreq_root=CPUFREQ_ROOT):
    """主板温度和降频，跟 robotd health 的 cpu_temp_c / cpu_throttle 同一个算法（fork soc.rs:25-170）：
    温度取所有 thermal_zone 里最热的（≤ 0 °C 的当坏传感器跳过）；频率取被压得最狠的 policy 的
    scaling_max_freq / cpuinfo_max_freq；level 取类型名含 cpufreq 的 cooling device 里压得最深的。
    读不到（不是 Linux、电脑上开发）回 None。"""
    try:
        zones = [e for e in os.listdir(thermal_root)]
    except OSError:
        return None
    hot = None
    for name in zones:
        if not name.startswith("thermal_zone"):
            continue
        milli = _read_int(os.path.join(thermal_root, name, "temp"))
        if milli is None or milli <= 0:
            continue
        c = milli / 1000.0
        hot = c if hot is None or c > hot else hot
    thr = None
    try:
        pols = os.listdir(cpufreq_root)
    except OSError:
        pols = []
    for name in pols:
        if not name.startswith("policy"):
            continue
        khz = _read_int(os.path.join(cpufreq_root, name, "scaling_max_freq"))
        mx = _read_int(os.path.join(cpufreq_root, name, "cpuinfo_max_freq"))
        if khz is None or not mx:
            continue
        if thr is None or khz * thr["max_khz"] < thr["khz"] * mx:
            thr = {"khz": khz, "max_khz": mx, "level": 0, "max_level": 0}
    if thr is not None:
        for name in zones:
            if not name.startswith("cooling_device"):
                continue
            try:
                with open(os.path.join(thermal_root, name, "type"), encoding="ascii") as f:
                    kind = f.read().strip()
            except OSError:
                continue
            if "cpufreq" not in kind:
                continue
            lv, mx = _read_int(os.path.join(thermal_root, name, "cur_state")), _read_int(os.path.join(thermal_root, name, "max_state"))
            if lv is None or not mx:
                continue
            if thr["max_level"] == 0 or lv * thr["max_level"] > thr["level"] * mx:
                thr.update(level=lv, max_level=mx)
    if hot is None and thr is None:
        return None
    return {"c": hot, "throttle": thr}


def read_battery_shutdown(paths=ROBOTD_CONFIGS):
    """robotd 配置里 [safety] battery_empty_shutdown 写的是什么：True / False；文件读不到、没写这一项回 None
    （没写时 robotd 默认是开着的：fork robotd-params lib.rs:1804；但我们按「读不到」处理，页面上说「不会自己坐下」，宁可保守）。
    按顺序找第一个在的文件（fork 的、官方默认的）。"""
    import tomllib
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        v = (data.get("safety") or {}).get("battery_empty_shutdown") if isinstance(data.get("safety"), dict) else None
        return v if isinstance(v, bool) else None
    return None


# ---------------- 判一步 ----------------

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _f1(x):
    """一位小数；不是数字写「?」（记录文件里可能是 null：代码审查 C1）。"""
    return f"{x:.1f}" if _num(x) else "?"


def _row(key, state, text, when=None, actions=(), note=None, hard=None, found=None, ask=None):
    """hard：主程序明说这一步不行（标定没做完、IMU 方向没确认、不让上力）——标了鸭子也站不起来，「已完成」「跳过」要点两次。
    found：做过 / 查过、结果不行（测出了问题，审查 D5）——第一个按钮写「重新做」，标的要点两次。
    hard / found 是带 {v} 的后果（fill 换成「跳过了」/「标成已完成」）。ask：测急停第二下按钮上的那句（带 {q}、{v}，按情况写：审查 D11）。
    mark：「已完成」「跳过」两个按钮要不要点两次、第二下写什么（with_mark 填）；marked：这一行现在是用户标的（done / skip）。"""
    return {"key": key, "n": ORDER.index(key) + 1, "title": TITLES[key], "tip": TIPS[key], "state": state,
            "text": text, "when": when, "note": note, "actions": [{"do": d, "label": lab} for d, lab in actions],
            "hard": hard, "found": found, "ask": ask, "go": GO[key], "mark": None, "marked": None}


def header_of(inp):
    """卡片头上的「连上鸭子」（不编号）。kind：ok / absent / connecting / bench / servo / commissioning / stuck_calib / starting / other。"""
    bus = inp.get("bus") if isinstance(inp.get("bus"), dict) else None
    if inp.get("bench"):
        return {"ok": False, "kind": "bench", "text": "台架模式：不连主程序，清单不检查"}
    if inp.get("servo_session"):
        return {"ok": False, "kind": "servo", "text": "在舵机页调舵机（主程序按计划停着）：清单先停在调之前的样子"}
    if inp.get("present") is False:
        return {"ok": False, "kind": "absent", "text": "主程序没在跑：连不上 robotd"}
    if bus is None:
        return {"ok": False, "kind": "connecting", "text": "正在连主程序…"}
    mode, phase, err = bus.get("mode"), bus.get("phase"), bus.get("error")
    if phase in STARTING_PHASES:
        if err and CALIB_ERR.search(str(err)):
            return {"ok": False, "kind": "stuck_calib", "text": "主程序不让上力：标定或 IMU 方向没过", "raw": str(err)}
        wait = startup_wait(bus)
        if wait:
            text = wait["text"]
        elif (inp.get("starting_s") or 0) >= STARTING_LONG_S:
            # 超过 30 秒还报启动阶段：不再说「还在启动」（10-20 真机：旧 fork 的标定模式永远报 configuring_bus）
            text = f"主程序一直报「启动中」，超过 {STARTING_LONG_S:.0f} 秒了：{STILL_STARTING}"
        else:
            text = "主程序还在启动"
        return {"ok": False, "kind": "starting", "text": text, "raw": str(err) if err else None}
    if mode == "commissioning":
        # 标定模式的实时快照（phase 是 ready / moving / read_error / stop_unconfirmed / power_pending）：主程序起来了。
        # read_error 也算起来了，只是这一拍读舵机出错：照原样把主程序的原话带出来
        if phase == "read_error":
            return {"ok": False, "kind": "commissioning", "text": "标定模式：主程序起来了，但读舵机出错（看舵机电源和线）",
                    "raw": str(bus.get("last_cycle_error") or err or "") or None}
        return {"ok": False, "kind": "commissioning", "text": "标定模式：做完 IMU 粗校，第 3 步写完会自动切运动模式"}
    if mode == "motion" and phase == "control":
        return {"ok": True, "kind": "ok", "text": "连上了"}
    return {"ok": False, "kind": "other", "text": f"主程序在 {mode or '?'} 模式、{phase or '?'} 阶段"}


def _imu_mount_unverified(bus, calib):
    """主程序明说 IMU 方向没确认吗：busStatus 的 native.imu_mount_verified 或标定文件 mount.verified 是 False。
    不知道也当 False（只有主程序明说没确认才拦）。"""
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    _source, mount = mount_of(bus, calib)
    return native.get("imu_mount_verified") is False or (isinstance(mount, dict) and mount.get("verified") is False)


def servo_row(inp, rec, head):
    bus = inp.get("bus") or {}
    calib = inp.get("calib")
    if head["kind"] in ("absent", "connecting", "bench"):
        return _row("servo", "unk", "连上主程序才判得出", actions=[("servos", "去舵机页")])
    uncal = (calib or {}).get("uncalibrated_joints")
    if head["kind"] == "stuck_calib":
        # 只有 IMU 明说没确认、而且关节都标了（uncalibrated_joints 是空列表）时，原因在 IMU：
        # 舵机行不 hard，照正常逻辑往下判；其余情况照旧 hard。
        if not (_imu_mount_unverified(bus, calib) and isinstance(uncal, list) and not uncal):
            return _row("servo", "no", "主程序不让上力：标定或 IMU 方向没过（它报的是同一句话，分不出是哪个）",
                        actions=[("servos", "去舵机页")], hard=HARD_STUCK)
    js = _joints(calib)
    if isinstance(uncal, list) and uncal:
        names = "、".join(JOINT_CN.get(str(x), str(x)) for x in uncal[:4]) + ("…" if len(uncal) > 4 else "")
        return _row("servo", "no", f"主程序说 {len(uncal)} 颗还没标：{names}", actions=[("servos", "去舵机页")], hard=HARD_SERVO)
    if js and any(j.get("calibrated") is False for j in js):
        bad = [JOINT_CN.get(j.get("name"), j.get("name")) for j in js if j.get("calibrated") is False]
        return _row("servo", "no", f"主程序文件里 {len(bad)} 颗没标：{'、'.join(bad[:4])}", actions=[("servos", "去舵机页")],
                    hard=HARD_SERVO)
    if head["kind"] == "commissioning" and bus.get("calibrated") is False:
        return _row("servo", "no", "标定模式下主程序说标定没做完", actions=[("servos", "去舵机页")], hard=HARD_SERVO)
    if not js:
        return _row("servo", "unk", "这次连上以后还没读到主程序的标定文件（calibration-status）", actions=[("servos", "去舵机页")])
    off = [j for j in js if j.get("zero_raw") != 2048]
    if off:
        def one(j):
            z = j.get("zero_raw")
            d = f"（差 {(z - 2048) * 360 / 4096:+.1f}°）" if isinstance(z, (int, float)) else ""
            return f"{JOINT_CN.get(j.get('name'), j.get('name'))} {z}{d}"
        text = f"两套零位会打架：主程序文件里 {len(off)} 颗的 zero_raw 不是 2048——" + "、".join(one(j) for j in off[:3]) \
            + ("…" if len(off) > 3 else "") + "。多半是随包的一号鸭数值，在舵机页标一遍"
        return _row("servo", "todo", text, actions=[("servos", "去舵机页")],
                    found="两套零位会打架：{v}也会站歪、走偏（第 3 步按 2048 写零位）")
    st = inp.get("studio_servo") or {}
    fp = joints_fp(calib)
    if set(SERVO_IDS) <= set(st.get("ids") or ()):
        # 舵机页标过 15 颗：第一次看到时记下当时的关节指纹（servo_studio，_track 里记），之后指纹变了就要重新核对
        # （判法表：「并且指纹没变」，代码审查 C9-3）
        ss = rec.get("servo_studio") if isinstance(rec.get("servo_studio"), dict) else None
        if ss and ss.get("src") == st.get("time") and ss.get("fp") == fp:
            return _row("servo", "ok", "舵机页标过；主程序文件里 15 颗都是 2048", when=st.get("time"))
        if ss and ss.get("src") == st.get("time"):
            return _row("servo", "todo", "舵机页标过以后，主程序的标定文件改过了：确实校过就点「已完成」，或者在舵机页再标一遍",
                        when=st.get("time"), actions=[("servos", "去舵机页")])
    c = rec.get("confirm_servo") if isinstance(rec.get("confirm_servo"), dict) else None
    if c and c.get("fp") == fp:
        return _row("servo", "ok", "核对过：主程序文件里 15 颗都是 2048", when=c.get("at"))
    if c:
        return _row("servo", "todo", "核对以后主程序的标定文件改过了：确实校过就点「已完成」，或者在舵机页标一遍",
                    actions=[("servos", "去舵机页")])
    # 以前这里还有个「已经校过，核对一下」：现在三个按钮里的「已完成」就是它（用户 10-20：去做、已完成、跳过）
    return _row("servo", "todo", "主程序文件里写着标过（可能是随包的一号鸭数值或手改的），Studio 这边没有记录："
                "以前用 Studio 的舵机页校过就点「已完成」", actions=[("servos", "去舵机页")])


def imu_row(inp, rec, head):
    bus = inp.get("bus") or {}
    calib = inp.get("calib")
    if head["kind"] in ("absent", "connecting", "bench"):
        return _row("imu", "unk", "连上主程序才判得出", actions=[("imu", "去 IMU 页")])
    source, mount = mount_of(bus, calib)
    # stuck_calib 不在这里拦：主程序明说 IMU 没确认由下面两个 HARD_IMU 分支拦，否则照正常逻辑判（能跳过）
    if head["kind"] == "commissioning" and bus.get("imu_mount_verified") is False:
        return _row("imu", "no", "标定模式下主程序说 IMU 方向没确认", actions=[("imu", "去 IMU 页")], hard=HARD_IMU)
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    if native.get("imu_mount_verified") is False:
        # busStatus 明说没确认：标定状态读不到也是「没做」，不是「?」（代码审查 C4；待办那条也就不该被藏）
        return _row("imu", "no", "主程序说 IMU 的安装方向还没确认：装歪了走路会往前冲", actions=[("imu", "去 IMU 页")],
                    hard=HARD_IMU)
    if mount and mount.get("verified") is False:
        return _row("imu", "no", "主程序存的 IMU 方向还没确认（verified 不是 true）：fork 上没确认连站都不让",
                    actions=[("imu", "去 IMU 页")], hard=HARD_IMU)
    mfp = mount_fp(source, (mount or {}).get("quat"))
    if mfp is None:
        return _row("imu", "unk", "这次连上以后还没读到主程序存的 IMU 方向（calibration-status）", actions=[("imu", "去 IMU 页")])
    chk = rec.get("imu_check") if isinstance(rec.get("imu_check"), dict) else None
    if chk and chk.get("source") == source and chk.get("mount_fp") == mfp:
        d, t = chk.get("diff_deg"), chk.get("tilt_deg")
        if chk.get("ok") is True and _num(d):
            return _row("imu", "ok", f"IMU 页站着核过：跟主程序用的方向差 {_f1(d)}°", when=chk.get("at"))
        return _row("imu", "todo", f"上次站着核对：主程序用的方向跟算出来的差 {_f1(d)}°、站姿倾斜 {_f1(t)}°"
                    "（要差 < 2°、倾斜 ≤ 5°）", when=chk.get("at"), actions=[("imu", "去 IMU 页")],
                    found=f"站着核过，方向差 {_f1(d)}°：{{v}}走路也会往前冲（一号鸭 10-12 就是装歪 8.5°）")
    if chk:
        return _row("imu", "todo", "核对以后 IMU 方向（或芯片）改过了：去 IMU 页站着再核一次", actions=[("imu", "去 IMU 页")])
    return _row("imu", "todo", "文件里写着已确认；装歪 8.5° 也会显示已确认：去 IMU 页站着核一次", actions=[("imu", "去 IMU 页")])


def _commit_complete(bus, calib):
    """主程序那边的完成判据（任务 11）：motion_enabled = true、15 颗都 calibrated、正在用的 IMU 那份 verified。
    从 calibration-status / busStatus 读，跟 imu_row 用的是同一条 mount_of。"""
    cal = (calib or {}).get("calibration") if isinstance(calib, dict) else None
    cal = cal if isinstance(cal, dict) else (calib if isinstance(calib, dict) else {})
    js = _joints(calib)
    _source, mount = mount_of(bus, calib)
    return (cal.get("motion_enabled") is True and isinstance(js, list) and len(js) == len(SERVO_IDS)
            and all(j.get("calibrated") is True for j in js)
            and isinstance(mount, dict) and mount.get("verified") is True)


def commit_row(inp, rec, head):
    """第 3 步「写进主程序」：完成看主程序文件；没完成时把 CommitService 的准备情况逐项带出去。"""
    bus = inp.get("bus") or {}
    calib = inp.get("calib")
    cv = inp.get("commit") if isinstance(inp.get("commit"), dict) else None

    def out(state, text, actions=()):
        row = _row("commit", state, text, actions=actions)
        row["desc"] = COMMIT_TEXT
        if cv is not None and cv.get("note"):
            row["desc"] += "\n" + cv["note"]
        if cv is not None:
            row["commit"] = cv
        return row

    if head["kind"] in ("absent", "connecting", "bench"):
        return out("unk", "连上主程序才判得出", [("commit", "去看看")])
    if cv is not None and cv.get("dirty") is True:
        why = "、".join(cv.get("dirty_why") or []) or "有新改动"
        return out("todo", f"有新改动还没写进主程序：{why}", [("commit", "去写进主程序")])
    if _commit_complete(bus, calib):
        return out("ok", "写进主程序了：15 颗都标好、IMU 方向也确认了，开机直接就能站、能走",
                   [("commit", "重新写")])
    js = _joints(calib)
    if (not isinstance(js, list) or not js) and not (cv is not None and cv.get("preview_ok") is True):
        return out("unk", "这次连上以后还没读到主程序的标定文件：等几秒再试", [("commit", "去看看")])
    if cv is None:
        return out("todo", "还没写进主程序：等 Studio 把准备情况读出来", [("commit", "去看看")])
    items = cv.get("items") if isinstance(cv.get("items"), list) else []
    bad = [i for i in items if isinstance(i, dict) and not i.get("ok")]
    if bad:
        labels = "、".join(str(i.get("label") or "?") for i in bad[:3]) + ("…" if len(bad) > 3 else "")
        return out("todo", f"还差{labels}：{bad[0].get('text') or '照下面的清单补齐'}", [("commit", "去写进主程序")])
    if cv.get("ready") is True:
        return out("todo", "准备齐了：点「写进主程序」一次写好，写完主程序重启", [("commit", "去写进主程序")])
    return out("todo", "还没写进主程序：照下面的清单补齐再来", [("commit", "去写进主程序")])

def _pad_names(pads):
    return "、".join(p.get("name") or p.get("mac") or "手柄" for p in pads[:2])


def pad_row(inp, rec, head):
    pad = inp.get("pad")
    if pad is None:
        return _row("pad", "unk", "这只鸭子没开手柄的后端（台架模式）")
    pads = [p for p in pad.get("pads") or [] if isinstance(p, dict)]
    good = [p for p in pads if p.get("paired") and p.get("trusted")]
    if good:
        on = next((p for p in good if p.get("connected")), None)
        return _row("pad", "ok", f"{on.get('name') or '手柄'} · 连着" if on else
                    f"配过 {_pad_names(good)} · 没连（打开手柄就会自己连上）")
    if any(p.get("paired") for p in pads):
        return _row("pad", "todo", "配上了但没「信任」：重启鸭子以后不会自己回连，点「修复」", actions=[("pad_known", "去修复")])
    dev = pad.get("device") or {}
    if pad.get("attached"):
        return _row("pad", "ok", f"padd 看到 {dev.get('name') or '手柄'} 连着" + ("" if pad.get("known") else "（configd 读不到）"))
    if pad.get("known"):
        return _row("pad", "no", "还没配手柄（也可以先用下面的网页手柄）", actions=[("pad_wizard", "配对手柄")])
    seen = rec.get("pads_seen") if isinstance(rec.get("pads_seen"), dict) else {}
    tail = f"；以前配过 {'、'.join(list(seen.values())[:2])}" if seen else ""
    return _row("pad", "unk", f"读不到 configd 的手柄状态（有的鸭子关着它）{tail}", actions=[("pad_wizard", "配对手柄")])


def _current_pad(pad):
    """现在的手柄 (mac, name)：连着的那只；旁路看到的；都没有时，唯一配过的那只。读不到回 (None, None)。"""
    if pad is None:
        return None, None
    a = pad.get("active") or {}
    if a.get("mac"):
        return str(a["mac"]).upper(), a.get("name")
    dev = pad.get("device") or {}
    if pad.get("attached") and dev.get("unique"):
        return str(dev["unique"]).upper(), dev.get("name")
    paired = [p for p in pad.get("pads") or [] if isinstance(p, dict) and p.get("paired") and p.get("mac")]
    if pad.get("known") and len(paired) == 1:
        return str(paired[0]["mac"]).upper(), paired[0].get("name")
    return None, None


def _no_physical_pad(pad):
    """确定没有实体手柄（configd 读得到、一个都没配、旁路也没看到）：只用网页手柄。读不到 configd 不算。"""
    return bool(pad and pad.get("known") and not pad.get("attached")
                and not [p for p in pad.get("pads") or [] if isinstance(p, dict) and p.get("paired")])


def _many_unconnected(pad):
    """配过不止一只手柄、一只都没连：认不出「现在的手柄」是哪只（审查 D11：这时写「先连上一只手柄再测」）。"""
    if not pad or pad.get("attached") or (pad.get("active") or {}).get("mac"):
        return 0
    paired = [p for p in pad.get("pads") or [] if isinstance(p, dict) and p.get("paired")]
    return len(paired) if len(paired) >= 2 and not any(p.get("connected") for p in paired) else 0


# 测急停的「已完成」「跳过」都要点两次（审查 D11）；{q}、{v} 由 fill 换成这个动作的说法
ESTOP_ASK = {"never": "确定{q}？没测过：手柄 Select 能不能停住它没验过",
             "web": "确定{q}？只用网页的话急停是空格 / 顶上红按钮，以后配了手柄要再测",
             "pad": "确定{q}？换了手柄：这只手柄的 Select 没测过",
             "build": "确定{q}？主程序升级过：升级以后 Select 没测过",
             "bad": "确定{q}？急停现在不管用（{why}）：{v}也不会变好用"}


def estop_row(inp, rec, head):
    pad = inp.get("pad")
    r = rec.get("estop") if isinstance(rec.get("estop"), dict) else None
    es = (pad or {}).get("estop") or {}
    first_bad = next((c for c in es.get("checks") or [] if c.get("ok") is False), None)
    bad_why = f"{first_bad.get('label')}：{first_bad.get('why') or '×'}" if first_bad else None
    many = _many_unconnected(pad)
    if r is None:
        # 没配实体手柄、只用网页：网页上的急停是空格 / 顶上红按钮（跟遥控卡的卸力同一条路），不经过手柄，用不着测（用户 10-18）
        web = _no_physical_pad(pad)
        web_only = "。只用网页的话：空格就是急停，跟顶上红按钮同一条路，不用测；可以跳过这步" if web else ""
        now = f"（配过 {many} 只手柄、现在都没连：先连上一只手柄再测）" if many else (f"（现在测不了：{bad_why}）" if bad_why else "")
        ask = ESTOP_ASK["web"] if web else (ESTOP_ASK["bad"].replace("{why}", bad_why) if es.get("state") == "bad" and bad_why
                                            else ESTOP_ASK["never"])
        return _row("estop", "no", "还没真按一次 Select 测过" + now + web_only, actions=[("estop", "去测急停")], ask=ask)
    mac, _ = _current_pad(pad)
    build = (inp.get("bus") or {}).get("build")
    if mac is None or not build:
        miss = (f"配过 {many} 只手柄、现在都没连：先连上一只手柄再测" if many else "读不到现在连的是哪只手柄") if mac is None \
            else "读不到主程序版本"
        return _row("estop", "unk", f"{r.get('name') or '手柄'} 测过，现在核对不了：{miss}", when=r.get("at"),
                    actions=[("estop", "去测急停")])
    if str(r.get("mac") or "").upper() != mac:
        return _row("estop", "todo", "换了手柄：再测一次", when=r.get("at"), actions=[("estop", "去测急停")], ask=ESTOP_ASK["pad"])
    if r.get("build") != build:
        return _row("estop", "todo", "主程序升级过：再测一次", when=r.get("at"), actions=[("estop", "去测急停")],
                    ask=ESTOP_ASK["build"])
    if es.get("state") == "bad":
        return _row("estop", "todo", f"测过，但现在急停不管用：{bad_why}", when=r.get("at"), actions=[("estop", "去看看")],
                    found=f"测过，但现在急停不管用（{bad_why}）：{{v}}也不会变好用，先把它弄好")
    ms = r.get("ms")
    return _row("estop", "ok", f"{r.get('name') or '手柄'} 测过：松开 Select 到卸力 {ms} ms" if _num(ms)
                else f"{r.get('name') or '手柄'} 测过", when=r.get("at"))


def walk_row(inp, rec, head):
    s = rec.get("stood") if isinstance(rec.get("stood"), dict) else None
    w = rec.get("walked") if isinstance(rec.get("walked"), dict) else None
    go = [("remote", "去遥控")]
    if s is None and w is None:
        return _row("walk", "no", "还没站起来过：托着它，点「站起来」，再点「开始走」、推左摇杆", actions=go)
    fp = inp.get("fp")
    last = (w if w is not None else s).get("at")
    if fp is None:
        return _row("walk", "unk", ("站起来过、走过" if w else "站起来过") + "；标定读不到，没法核对改没改过", when=last, actions=go)
    s_ok = s is not None and s.get("fp") == fp
    w_ok = w is not None and w.get("fp") == fp
    if w_ok:                                   # 用现在的标定走过，当然也站起来过
        return _row("walk", "ok", "站起来过、走过", when=w.get("at"))
    if (s is not None and not s_ok) or w is not None:
        return _row("walk", "todo", "标定改过以后还没" + ("站过、走过" if not s_ok else "走过"), when=last, actions=go)
    return _row("walk", "todo", "站起来过，还没走过：点「开始走」，推左摇杆", when=s.get("at"), actions=go)


def step_fp(key, inp):
    """(这一步的跳过绑不绑指纹, 现在的指纹)。跟正式记录绑同样的东西；读不到是 None。"""
    bus = inp.get("bus") or {}
    calib = inp.get("calib")
    if key == "servo":
        return True, joints_fp(calib)
    if key == "imu":
        source, mount = mount_of(bus, calib)
        return True, mount_fp(source, (mount or {}).get("quat"))
    if key == "commit":
        return True, inp.get("fp")             # 标定（关节 + IMU 方向）改过：跳过作废、要重写
    if key == "estop":
        mac, _ = _current_pad(inp.get("pad"))
        if mac is None and _no_physical_pad(inp.get("pad")):
            mac = "无实体手柄"            # 只用网页：之后配上手柄，指纹就变了，跳过作废、要测一次
        build = bus.get("build")
        return True, (_hash([mac, build]) if mac and build else None)
    if key == "walk":
        return True, inp.get("fp")
    return False, None                          # 配手柄：不绑


FP_WHAT = {"servo": "读不到主程序的标定文件", "imu": "读不到主程序存的 IMU 方向", "commit": "读不到标定",
           "estop": "读不到现在连的是哪只手柄（或主程序版本）", "walk": "读不到标定"}
BOUND = ("servo", "imu", "commit", "estop", "walk")      # 标记要绑指纹的步（配手柄不绑）


def _marks(rec, key):
    """这一步的「已完成」「跳过」记录：{kind: 记录}（正常最多一种，两种都有时「已完成」优先）。"""
    out = {}
    for kind, rk in KINDS.items():
        d = rec.get(rk) if isinstance(rec.get(rk), dict) else {}
        if isinstance(d.get(key), dict):
            out[kind] = d[key]
    return out


def mark_offer(row, bound, fp):
    """「已完成」「跳过」两个按钮（用户 10-20：每一行任何时候都能点）：要不要点两次、第二下按钮上写什么。
    原来不让跳的几种都要点两次：测出了问题（found）> 主程序明说不行（hard）> 测急停（按情况那句，审查 D11）>
    读不到（?）> 指纹读不到。ack 是这一行有的那几种问题：标的时候存进记录，确认过的问题以后不再作废它。"""
    key = row["key"]
    acks = [k for k in ("found", "hard") if row.get(k)]
    if row["state"] == "unk":
        acks.append("unk")
    if bound and fp is None:
        acks.append("fp")
    if row.get("found"):
        ask = "确定{q}？" + row["found"]
    elif row.get("hard"):
        ask = "确定{q}？" + row["hard"]
    elif key == "estop" and row.get("ask"):
        ask = row["ask"]
    elif row["state"] == "unk":
        ask = "确定{q}？" + UNK_WHY.replace("{text}", row["text"])
    elif key == "estop":
        ask = ESTOP_ASK["never"]
    elif bound and fp is None:
        ask = "确定{q}？" + FP_WHY.replace("{what}", FP_WHAT[key])
    else:
        ask = None
    out = {"ack": acks}
    for kind in KINDS:
        out[kind] = {"confirm": ask is not None, "ask": fill(ask, kind)}
    return out


def with_mark(row, inp, rec):
    """把用户标的「已完成」「跳过」套到一行上。Studio 自己有记录、判成 ✓ 的不变（只给「重新做」）。
    有标记、指纹没变 → ✓「你标了已完成」/ ↷「跳过了」（底下真实的情况放进 note）；指纹变了 → ●「标了已完成以后 xx 改过了」。
    标的时候没确认过的问题后来出现了：主程序明说不行（hard）→ 标的不管用；测出了问题（found）→ 作废（void：tick 删掉它）。
    没标的行带上 mark：两个按钮要不要点两次、第二下那句（mark_offer）。"""
    key = row["key"]
    if row["state"] == "ok":
        return row
    bound, fp = step_fp(key, inp)
    for kind, m in _marks(rec, key).items():
        ack = m.get("ack") if isinstance(m.get("ack"), list) else []
        if row.get("found") and "found" not in ack:
            row["note"] = f"之前{MARKED_WORD[kind]}；测出了问题以后{'跳过' if kind == 'skip' else '标的'}不算了"
            row["void"] = row.get("void", []) + [kind]
            continue
        if row.get("hard") and "hard" not in ack:
            row["note"] = f"之前{MARKED_WORD[kind]}，但现在主程序明说这一步不行：{'跳过' if kind == 'skip' else '标的'}不管用"
            continue
        old = m.get("fp")
        if bound and fp is not None and old is not None and old != fp:
            row = dict(row, state="todo", text=f"{CHANGED_WORD[kind]}以后{SKIP_CHANGED[key]}改过了：{row['text']}")
            continue
        out = _row(key, "ok" if kind == "done" else "skip", MARKED_TEXT[kind], when=m.get("at"))
        out["note"] = row["text"] if row["state"] != "unk" else None   # 底下真实的情况，页面小字写
        out["marked"] = kind
        for k in ("desc", "commit"):                                  # 第 3 步那张卡片点开还看得到准备情况
            if k in row:
                out[k] = row[k]
        return out
    row["mark"] = mark_offer(row, bound, fp)
    return row


def evaluate(inp, rec):
    """纯函数：inp 是 Onboarding._inputs() 收好的现场，rec 是记录。回给页面的整张卡。"""
    head = header_of(inp)
    rows = [servo_row(inp, rec, head), imu_row(inp, rec, head), commit_row(inp, rec, head),
            pad_row(inp, rec, head), estop_row(inp, rec, head), walk_row(inp, rec, head)]
    rows = [with_mark(r, inp, rec) for r in rows]
    nxt = next((r["key"] for r in rows if r["state"] in ("todo", "no")), None)
    return {"header": head, "rows": rows, "next": nxt, "all_done": all(r["state"] in ("ok", "skip") for r in rows),
            "skipped_n": sum(1 for r in rows if r["state"] == "skip"),
            "marked_n": sum(1 for r in rows if r.get("marked") == "done"), "frozen": False}


# ---------------- 记录文件逐条校验（代码审查 C1：合法 JSON、但某一条类型不对，不能让整张清单每 0.5 秒出错一次） ----------------

def _opt(x, *types):
    return x is None or (isinstance(x, types) and not isinstance(x, bool))


def _at(d):
    return _opt(d.get("at"), int, float)


CHECKS = {
    "confirm_servo": lambda d: _at(d) and isinstance(d.get("fp"), str),
    "servo_studio": lambda d: _at(d) and isinstance(d.get("fp"), str) and _opt(d.get("src"), str),
    "imu_check": lambda d: (_at(d) and _opt(d.get("source"), str) and isinstance(d.get("mount_fp"), str)
                            and _num(d.get("diff_deg")) and _opt(d.get("tilt_deg"), int, float) and isinstance(d.get("ok"), bool)),
    "estop": lambda d: (_at(d) and _opt(d.get("ms"), int, float) and _opt(d.get("mac"), str) and _opt(d.get("name"), str)
                        and _opt(d.get("build"), str)),
    "stood": lambda d: _num(d.get("at")) and isinstance(d.get("fp"), str),
    "walked": lambda d: _num(d.get("at")) and isinstance(d.get("fp"), str),
}


def clean_records(data):
    """(能用的记录, 丢掉的 [(键, 原因)])。认识的键逐条按 CHECKS 查；pads_seen 只留 字符串 → 字符串；不认识的键原样留着（以后的版本加的）。"""
    out, bad = {}, []
    for k, v in data.items():
        if k == "v":
            continue
        if k == "pads_seen":
            if isinstance(v, dict):
                out[k] = {str(m): n for m, n in v.items() if isinstance(n, str)}
            else:
                bad.append((k, f"不是对象（{type(v).__name__}）"))
            continue
        if k in KINDS.values():
            # skipped.<步> / done.<步>：at 必须是数字（审查 D10）；fp 是字符串，或者 null（标的时候指纹读不到：
            # tick 第一次读到时绑上，绑上以后指纹变了照样作废）；ack 是字符串列表（可以没有）
            if not isinstance(v, dict):
                bad.append((k, f"不是对象（{type(v).__name__}）"))
                continue
            good = {}
            for step, e in v.items():
                if (step in ORDER and isinstance(e, dict) and _num(e.get("at")) and _opt(e.get("by"), str)
                        and _opt(e.get("fp"), str)
                        and (e.get("ack") is None or (isinstance(e.get("ack"), list)
                                                      and all(isinstance(a, str) for a in e["ack"])))):
                    good[step] = e
                else:
                    bad.append((f"{k}.{step}", json.dumps(e, ensure_ascii=False, default=str)[:120]))
            out[k] = good
            continue
        chk = CHECKS.get(k)
        if chk is None:
            out[k] = v
            continue
        try:
            ok = isinstance(v, dict) and bool(chk(v))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            out[k] = v
        else:
            bad.append((k, json.dumps(v, ensure_ascii=False, default=str)[:120]))
    return out, bad


def error_view(why):
    """判清单整体出错：六行都写「?」和原因（不要永远「还在判…」，代码审查 C1）。"""
    rows = [_row(k, "unk", f"Studio 判这一步时出错：{why}") for k in ORDER]
    return {"header": {"ok": False, "kind": "error", "text": "清单判的时候出错（看日志里「系统」那几行）", "raw": why},
            "rows": rows, "next": None, "all_done": False, "skipped_n": 0, "marked_n": 0, "frozen": False}


# ---------------- 主体 ----------------

class Onboarding:
    def __init__(self, state_dir, log, clock=time.monotonic, wall=time.time):
        self.state_dir, self.log = state_dir, log
        self.clock, self.wall = clock, wall
        self._lk = threading.Lock()
        self._save_lk = threading.Lock()      # 整个写盘过程拿着它：两处同时存时旧的不会后落盘把新的盖掉（代码审查 C9-2）
        self.rec = {}
        self._dirty = False
        self._view = None
        self._stand_since = None
        self._walk_acc = 0.0
        self._walk_last = None
        self._studio = {"ids": set(), "time": None}
        self._studio_at = -1e9
        self._studio_mtime = None             # servo-calib 目录的 mtime：没变就不重新读文件
        self._eval_err = None                 # 上一次判清单出错的原因（同一种错只记一次）
        self._servo = None                    # 最近见到的舵机会话（取消跳过没传时用它判「清单冻着」）
        self.commit_service = None            # studio/commit.py 的 CommitService（app 接上；第 3 步的准备情况从这里来）
        self.battery_shutdown = None          # robotd 配置的 [safety] battery_empty_shutdown：True / False / None（读不到）
        self._cfg_at = -1e9
        self.robotd_configs = ROBOTD_CONFIGS
        self.cpu_hot = False                  # 主板「有点热」带回差（审查 O8）
        self.board = None                     # Studio 自己读的主板温度（robotd 不报时兜底）
        self._board_ok = os.path.isdir(THERMAL_ROOT)
        self.load()

    # ---- 文件 ----

    def path(self):
        return os.path.join(self.state_dir or ".", ONBOARD_FILE)

    def load(self):
        """读记录。文件坏了：改名成 .bad 留着（以后查得到），当空的，记一行 warn——不能让总览因为它崩（审查 O6）。"""
        p = self.path()
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"顶层不是对象（{type(data).__name__}）")
        except FileNotFoundError:
            self.rec = {}
            return
        except (OSError, ValueError) as e:
            bad = p + ".bad"
            try:
                os.replace(p, bad)
            except OSError:
                bad = None
            self.log.warn("上手清单的记录文件读不出来，当成空的重新记", cat="系统", step=p, raw=f"{type(e).__name__}: {e}",
                          hint=(f"坏文件留在 {bad}" if bad else "坏文件改名也没成功，看看 state 目录的权限"))
            self.rec = {}
            return
        self.rec, bad = clean_records(data)
        for k, raw in bad:
            self.log.warn(f"上手清单的记录「{k}」格式不对，这一条不用了", cat="系统", step=p, raw=raw,
                          hint="多半是手改过或者旧版本写的；那一步会按「没做」重新判，做一次就会重新记")

    def save(self):
        """整份写回（先写临时文件再改名，写一半断电不会把旧的弄坏）。在线程里调；出错只记日志，不抛。"""
        with self._save_lk:
            return self._save_locked()

    def _save_locked(self):
        with self._lk:
            if not self._dirty:
                return False
            data = dict(self.rec, v=1)
            self._dirty = False
        d = self.state_dir or "."
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".onboarding-", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path())
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            with self._lk:
                self._dirty = True              # 下一拍再试
            self.log.warn("上手清单的记录没存进去", cat="系统", step=self.path(), raw=f"{type(e).__name__}: {e}",
                          hint="state 目录满了或者没权限；不影响站和走，下次再试")
            return False
        return True

    def _put(self, key, value, say=None, cat="系统"):
        with self._lk:
            if self.rec.get(key) == value:
                return False
            self.rec[key] = value
            self._dirty = True
        if say:
            self.log.info(say, cat=cat, dedup=False)
        return True

    # ---- 外面来的记录 ----

    def note_estop(self, r):
        """测急停过了（pad.py 测急停线程里调：只改内存）。r = {ms, mac, name, build}。"""
        try:
            rec = {"at": self.wall(), "ms": r.get("ms"), "mac": (str(r["mac"]).upper() if r.get("mac") else None),
                   "name": r.get("name"), "build": r.get("build")}
            self._put("estop", rec, say=f"上手清单：记下「测急停过了」（{rec['name'] or rec['mac'] or '手柄'}，主程序 {rec['build']}）",
                      cat="安全")
            return True
        except Exception:  # noqa: BLE001 —— 测急停线程里，记不上也不能出事
            return False

    def note_imu_check(self, c):
        """IMU 页完整站着校完（imu.py _check 调）：记最近一次核对，不管够不够近（不够近清单写「差 x°」）。"""
        rec = {"at": self.wall(), "source": c.get("source"), "mount_fp": mount_fp(c.get("source"), c.get("quat")),
               "diff_deg": c.get("diff_deg"), "tilt_deg": c.get("tilt_deg"), "ok": bool(c.get("ok")), "by": c.get("by")}
        if rec["mount_fp"] is None:
            return False
        return self._put("imu_check", rec, say=f"上手清单：记下 IMU 核对（差 {rec['diff_deg']}°，{'够' if rec['ok'] else '不够'}近）",
                         cat="IMU") or True

    def confirm_servo(self, rd, by=None):
        """「已经校过，核对一下」：主程序文件里 15 颗都标了、zero_raw 全是 2048，才记一笔（指纹绑关节）。不能记就回原因。"""
        calib = fresh_calib(rd)
        js = _joints(calib)
        if not js:
            return False, "这次连上以后还没读到主程序的标定文件：等几秒再点"
        if (calib or {}).get("uncalibrated_joints"):
            return False, "主程序说还有没标的关节：去舵机页标"
        off = [j for j in js if j.get("zero_raw") != 2048 or j.get("calibrated") is False]
        if off:
            return False, f"有 {len(off)} 颗的 zero_raw 不是 2048（或者没标）：两套零位会打架，去舵机页标"
        if len(js) < len(SERVO_IDS):
            return False, f"主程序文件里只有 {len(js)} 颗"
        fp = joints_fp(calib)
        self._put("confirm_servo", {"at": self.wall(), "fp": fp, "by": by},
                  say=f"上手清单：舵机零位「已经校过，核对一下」（{by or '页面'}；主程序文件里 15 颗都是 2048）", cat="校准")
        return True, None

    def mark(self, step, kind, rd, pad=None, servo=None, by=None, confirmed=False):
        """「已完成」（kind=done，记 done.<步>）/「跳过」（kind=skip，记 skipped.<步>）。按现在的情况重判一遍：
        要点两次的（主程序明说不行、读不到、测出了问题、测急停、指纹读不到、调舵机时）没带 confirmed 回 (False, 第二下那句)——
        页面的两步确认不能只靠前端。记下 {at, by, fp, ack}：ack 是第二下确认过的那几种问题；两种标记互斥。"""
        if step not in ORDER:
            return False, f"不认识的步骤：{step}"
        if kind not in KINDS:
            return False, f"不认识的标法：{kind}"
        if servo is not None:
            self._servo = servo
        inp = self._inputs(rd, pad, servo, self.clock())
        if inp["bench"]:
            return False, "台架模式：清单现在不判，也不用标"
        with self._lk:
            rec = dict(self.rec)
        row = next(r for r in evaluate(inp, rec)["rows"] if r["key"] == step)
        if row.get("marked") == kind:
            return True, "已经跳过了" if kind == "skip" else "已经标了已完成"
        if row.get("marked"):
            return False, "这一步已经跳过了：先点「取消跳过」" if row["marked"] == "skip" else "这一步标了已完成：先点「取消」"
        if row["state"] == "ok":
            return False, "这一步已经完成了（Studio 有记录），不用再标"
        offer = row.get("mark") or {}
        ask = (offer.get(kind) or {}).get("ask")
        if inp["servo_session"] and not ask:
            ask = fill(FROZEN_ASK, kind)             # 调舵机时清单冻着：标了要交还以后才显示（审查 D11 的冻结，现在能点了）
        if ask and confirmed is not True:
            return False, f"要点两次确认：{ask}"
        bound, fp = step_fp(step, inp)
        entry = {"at": self.wall(), "by": by, "fp": fp if bound else None}
        if offer.get("ack"):
            entry["ack"] = list(offer["ack"])
        with self._lk:
            mine = dict(self.rec.get(KINDS[kind]) or {})
            theirs = dict(self.rec.get(KINDS[OTHER[kind]]) or {})
        mine[step] = entry
        word = "跳过" if kind == "skip" else "标了已完成"
        self._put(KINDS[kind], mine, cat="校准",
                  say=f"上手清单：{TITLES[step]} {word}（{by or '页面'}）" + (f"；点了两次确认：{ask}" if ask else ""))
        if theirs.pop(step, None) is not None:
            self._put(KINDS[OTHER[kind]], theirs)
        return True, None

    def skip(self, step, rd, pad=None, servo=None, by=None, confirmed=False):
        return self.mark(step, "skip", rd, pad, servo, by, confirmed)

    def mark_done(self, step, rd, pad=None, servo=None, by=None, confirmed=False):
        return self.mark(step, "done", rd, pad, servo, by, confirmed)

    def unmark(self, step, kind, by=None, servo=None, confirmed=False):
        """「取消跳过」/「取消」（已完成的）：删掉那条记录，那一行回到按真实情况判。
        调舵机时清单冻着：要点两次（取消了要交还以后才显示），没带 confirmed 回 (False, 那句)。"""
        if step not in ORDER:
            return False, f"不认识的步骤：{step}"
        if kind not in KINDS:
            return False, f"不认识的标法：{kind}"
        sv = servo if servo is not None else self._servo      # 没传就看 tick / mark 最近见到的那个舵机会话
        if sv is not None and getattr(sv, "state", None) in ("open", "opening") and confirmed is not True:
            return False, f"要点两次确认：{FROZEN_UNDO}"
        with self._lk:
            mine = dict(self.rec.get(KINDS[kind]) or {})
        if step not in mine:
            return True, "本来就没跳过" if kind == "skip" else "本来就没标已完成"
        mine.pop(step)
        word = "取消跳过" if kind == "skip" else "取消已完成"
        self._put(KINDS[kind], mine, say=f"上手清单：{TITLES[step]} {word}（{by or '页面'}）", cat="校准")
        return True, None

    def unskip(self, step, by=None, servo=None, confirmed=False):
        return self.unmark(step, "skip", by, servo, confirmed)

    def user_mark(self, step):
        """第 step 步现在算数的是不是用户自己标的：done（「已完成」）/ skip（「跳过」）/ None。
        commit.py 的准备情况用（第 1 步标了 → 「舵机零位」算满足）；读的是上一拍判好的缓存（tick 每 0.5 秒）。"""
        rows = (self._view or {}).get("rows") or []
        r = next((x for x in rows if isinstance(x, dict) and x.get("key") == step), None)
        return r.get("marked") if r else None

    # ---- 读文件：在线程里（app 用 asyncio.to_thread 调），tick 只用缓存（代码审查 C9-1） ----

    def need_board(self, rd):
        """robotd 没报温度（调试模式、等总线、过期）时才要 Studio 自己读 /sys/class/thermal。在事件循环里调（只看内存）。"""
        ha = rd.health.age()
        h = rd.health.value if ha is not None and ha <= MAX_AGE["health"] else None
        return self._board_ok and not (isinstance(h, dict) and _num(h.get("cpu_temp_c")))

    def io(self, board=False, now=None):
        """在线程里调：servo-calib 目录（mtime 变了、或者 10 秒到了才重读）、主板温度（board=True 才读）。不抛。"""
        now = self.clock() if now is None else now
        try:
            d = os.path.join(self.state_dir or ".", "servo-calib")
            try:
                mt = os.stat(d).st_mtime
            except OSError:
                mt = None
            if mt != self._studio_mtime or now - self._studio_at >= STUDIO_SCAN_S:
                self._studio = studio_servo_record(self.state_dir)
                self._studio_mtime, self._studio_at = mt, now
        except Exception:  # noqa: BLE001 —— 读不到就当没有记录
            pass
        if board:
            try:
                self.board = read_board()
            except Exception:  # noqa: BLE001
                self.board = None
        else:
            self.board = None
        if now - self._cfg_at >= ROBOTD_CFG_S:
            self._cfg_at = now
            self.battery_shutdown = read_battery_shutdown(self.robotd_configs)

    # ---- 每 0.5 秒一次 ----

    def _inputs(self, rd, pad, servo, now):
        bus = rd.bus.value if isinstance(rd.bus.value, dict) else None
        calib = fresh_calib(rd)
        pv = None
        if pad is not None:
            try:
                pv = pad.onboard_view()
            except Exception as e:  # noqa: BLE001 —— 手柄后端出错：这一行写「读不到」，别把整张卡弄没
                pv = {"known": False, "error": f"{type(e).__name__}: {e}", "pads": []}
        return {"bench": getattr(rd, "addr", None) == "none", "present": rd.present, "bus": bus, "calib": calib,
                "servo_session": servo is not None and getattr(servo, "state", None) in ("open", "opening"),
                "pad": pv, "studio_servo": self._studio, "fp": full_fp(bus, calib),
                # starting_since 是轮询按真的钟记的：这里也用真的钟（测试里 tick 的 now 是拨出来的）
                "commit": self._commit_view(rd, servo), "starting_s": starting_secs(rd, bus)}

    def _commit_view(self, rd, servo):
        """第 3 步的准备情况（CommitService.view）。读不出来只让这一步显示「?」，别把整张卡弄没。"""
        svc = self.commit_service
        if svc is None:
            return None
        try:
            return svc.view(rd, servo)
        except Exception as e:  # noqa: BLE001
            return {"ready": False, "items": [], "job": None, "error": f"{type(e).__name__}: {e}"}

    def tick(self, rd, pad=None, servo=None, now=None):
        """判一遍、记该记的（只改内存，不读文件）。回 True = 记录变了，调用方在线程里 save()。
        主板温度的回差先算、单独 try：判清单出错不能让它停在旧值（代码审查 C1）。"""
        now = self.clock() if now is None else now
        if servo is not None:
            self._servo = servo
        try:
            self._board_tick(rd)
        except Exception as e:  # noqa: BLE001
            self.log.error("算主板温度的回差时出错", cat="系统", raw=f"{type(e).__name__}: {e}", exc=True)
        try:
            inp = self._inputs(rd, pad, servo, now)
            if not inp["servo_session"]:
                self._track(rd, inp, now)
                self._bind_marks(inp)
            with self._lk:
                rec = dict(self.rec)
            v = evaluate(inp, rec)
            if not inp["servo_session"]:
                self._void_marks(v)
            if inp["servo_session"] and self._view is not None and self._view["header"].get("kind") != "error":
                # 在舵机页调舵机：主程序按计划停着，各步保留上一次的显示、变灰（审查判法表「在调舵机」一行）。
                # 「已完成」「跳过」「取消」照样能点，但要点两次：第二下写「要等交还以后才显示」（frozen_ask）
                v = dict(self._view, header=v["header"], frozen=True,
                         frozen_ask={"done": fill(FROZEN_ASK, "done"), "skip": fill(FROZEN_ASK, "skip"), "undo": FROZEN_UNDO})
            self._eval_err = None
        except Exception as e:  # noqa: BLE001 —— 清单出错：五行写「?」和原因，记一行（同一种错只记一次）
            why = f"{type(e).__name__}: {e}"[:200]
            if why != self._eval_err:
                self._eval_err = why
                self.log.error("上手清单判的时候出错，清单先写「?」", cat="系统", raw=why, exc=True,
                               hint="这是 Studio 的问题，导出诊断包发出来；不影响站和走")
            v = error_view(why)
        self._view = v
        with self._lk:
            return self._dirty

    def _void_marks(self, v):
        """测出了问题的行（found），之前没确认过这个问题的「跳过」「已完成」作废：从记录里删掉、记一行（审查 D5）。
        页面这一拍已经按问题显示了。标的时候第二下确认过这个问题的（ack 里有 found）不动。"""
        for kind, rk in KINDS.items():
            gone = [r for r in v["rows"] if kind in (r.get("void") or ())]
            if not gone:
                continue
            with self._lk:
                d = dict(self.rec.get(rk) or {})
            for r in gone:
                d.pop(r["key"], None)
            word = "跳过" if kind == "skip" else "「已完成」"
            self._put(rk, d, cat="校准",
                      say="上手清单：" + "、".join(f"{r['title']}的{word}作废（{fill(r['found'], kind)}）" for r in gone))

    def _bind_marks(self, inp):
        """标的时候指纹读不到（记的是 null）：第一次读到时绑上现在的指纹，之后变了照样作废（照 C2：读不到不作废）。"""
        for kind, rk in KINDS.items():
            with self._lk:
                d = dict(self.rec.get(rk) or {}) if isinstance(self.rec.get(rk), dict) else {}
            named = []
            for step, e in list(d.items()):
                if step not in BOUND or not isinstance(e, dict) or e.get("fp") is not None:
                    continue
                _bound, fp = step_fp(step, inp)
                if fp is not None:
                    d[step] = dict(e, fp=fp)
                    named.append(TITLES[step])
            if named:
                word = "跳过" if kind == "skip" else "「已完成」"
                self._put(rk, d, cat="校准", say=f"上手清单：{'、'.join(named)}的{word}标的时候读不到指纹，现在读到了、绑上")

    def _track(self, rd, inp, now):
        """站起来过、走过、配过哪些手柄（只看 Studio 自己收到的状态：robot.state 要有页面看着才推）。"""
        bus = inp["bus"] or {}
        native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
        # 数据新不新看真的钟（Sample 记的是 monotonic）；站了多久、走了多久看 now（测试里能拨）。新鲜度跟 state.MAX_AGE 一样
        pa, sa = rd.policies.age(), rd.state.age()
        pol = rd.policies.value if pa is not None and pa <= MAX_AGE["policies"] else None
        st = rd.state.value if sa is not None and sa <= MAX_AGE["state"] else None
        pose = posture(rd.present, inp["bus"], pol, st)
        # 指纹读不到（robotd 刚重启、刚起来、标定读失败在重试）：不记、不覆盖带指纹的旧记录——不然标定读回来以后
        # 第 5 步会变成「标定改过以后还没站过」（代码审查 C2）。站的计时、走的累计照样算，读到了马上记
        fp = inp["fp"]
        if pose in STAND and native.get("torque_state_confirmed") is True:
            if self._stand_since is None:
                self._stand_since = now
            elif now - self._stand_since >= STOOD_S and fp is not None:
                old = self.rec.get("stood") if isinstance(self.rec.get("stood"), dict) else None
                if old is None or old.get("fp") != fp:
                    self._put("stood", {"at": self.wall(), "fp": fp}, say="上手清单：记下「站起来过」", cat="手柄")
        else:
            self._stand_since = None
        if pose == "walking":
            if self._walk_last is not None:
                self._walk_acc += min(now - self._walk_last, 1.0)
            self._walk_last = now
            old = self.rec.get("walked") if isinstance(self.rec.get("walked"), dict) else None
            if self._walk_acc >= WALKED_S and fp is not None and (old is None or old.get("fp") != fp):
                self._walk_acc = 0.0
                self._put("walked", {"at": self.wall(), "fp": fp}, say="上手清单：记下「走过」", cat="手柄")
        else:
            self._walk_last = None
        # 舵机页标过 15 颗、主程序文件里也都是 2048：记下这时的关节指纹，之后变了要重新核对（代码审查 C9-3）
        stu = inp["studio_servo"] or {}
        js = _joints(inp["calib"])
        jfp = joints_fp(inp["calib"])
        if set(SERVO_IDS) <= set(stu.get("ids") or ()) and js and jfp and all(j.get("zero_raw") == 2048 for j in js):
            ss = self.rec.get("servo_studio") if isinstance(self.rec.get("servo_studio"), dict) else None
            if ss is None or ss.get("src") != stu.get("time"):
                self._put("servo_studio", {"at": self.wall(), "fp": jfp, "src": stu.get("time")},
                          say="上手清单：舵机页的标定记录跟主程序文件对上了（15 颗都是 2048）", cat="校准")
        pv = inp["pad"] or {}
        seen = dict(self.rec.get("pads_seen") or {}) if isinstance(self.rec.get("pads_seen"), dict) else {}
        changed = False
        for p in pv.get("pads") or []:
            if isinstance(p, dict) and p.get("paired") and p.get("mac") and seen.get(str(p["mac"]).upper()) != p.get("name"):
                seen[str(p["mac"]).upper()] = p.get("name") or p.get("mac")
                changed = True
        if changed:
            self._put("pads_seen", seen)

    def _board_tick(self, rd):
        """主板温度：回差（≥ 80 °C 亮、降到 77 °C 以下才收）；robotd 不报时用 Studio 自己读的（io() 在线程里读好的
        /sys/class/thermal，鸭子上才有）。"""
        ha = rd.health.age()
        h = rd.health.value if ha is not None and ha <= MAX_AGE["health"] else None
        c = (h or {}).get("cpu_temp_c") if isinstance(h, dict) else None
        if not _num(c):
            c = (self.board or {}).get("c") if isinstance(self.board, dict) else None
        if not _num(c):
            self.cpu_hot = False
        elif c >= CPU_WARN_C:
            self.cpu_hot = True
        elif c < CPU_CLEAR_C:
            self.cpu_hot = False

    def view(self):
        return self._view
