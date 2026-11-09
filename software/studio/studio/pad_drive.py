"""手柄页里「让鸭子动」的部分：谁在开（方向盘）、网页手柄（图上的键、摇杆）、站起来 / 开始走 / 坐下、技能、声音、
动作和组合动作。

PadOps（pad.py）继承这个类，共用锁、robotd 回读、日志。这里的规矩（安全优先）：
- **方向盘**（审查 19、25）：推摇杆、按住扳机 / X、做动作的页面拿方向盘；别的页面要点「接管」才能抢。拿着的页面 6 秒
  没推摇杆、没说「还按着」就自动松开（只开着手柄页不算：watch、drive open 都不续方向盘，审查二 I1、三 V5）。一次性的
  命令（站、走、坐、技能、叫）不拿方向盘；别人正在推摇杆时不让点（「停下（站好）」除外，谁都能点、不排队）。
- **页面在不在手柄区**（三 V5；总览重排审查 O1：手柄并进了总览，「在」= 路由是 / 或 /pad、页面可见、手柄那一块在
  视口里）：进来发 drive {open, seq}，离开发 drive {on:false, seq}，seq 是页面的递增序号，按连接记最后一个，比它旧的丢掉
  （线程池里会乱序）。离开的连接记进 closed：之后晚到的摇杆、按键、动作一律丢；再 open 才解开。**新连接一开始就在 closed
  里**（new_conn），没发过 open 不算在。**断开的连接（gone）永远拿不回**：open 遇到 gone 直接拒。
- **摇杆按住才动**：页面按住时 10 Hz 发 sticks（或旧的 move），松手发一次 stop；服务端不替页面重复发（断了靠 robotd 的
  deadman 500 ms：duck-control safety.rs:226-237）。序号按连接存、换方向盘也不清；比这个连接最后一次 stop 旧的帧一律丢
  （屏障三个通道共用）；「最新的那一帧」按通道分（move / head / body，三 V6）。检查和发送在同一把锁里。
- **头、身体是锁存的**（三 V1）：robot.head、robot.pose 在 robotd 里都不过期（fork robotd intents.rs:327-341），
  pose 的 active:true 会让控制器把速度清零、强制用站立网络（fork control.rs:595-607）——网页留下一个，网页和实体手柄
  都推不走鸭子。所以记下「网页置过的头 / 身体」（_latch），下面每条路都清一次（头全 0、身体 {"active": false}，
  **镜像时也清**：padd 在身体模式时最多 0.1 秒就重发，padd:1570、1625-1631）：切回走路、松手离页、页面断开、被接管、
  方向盘 6 秒换人、卸力（四个来源）、实体手柄连上、点停下 / Start 停下、姿势离开「准备好走」、Studio 关闭。
  卸力那几个入口在事件循环里，只置标志（_request_clear），交给后台线程发，而且排在卸力之后 0.1 秒。
  **坑**：PoseParams 默认 active:true（fork lib.rs:2320-2346），发 robot.pose {} 反而会打开身体模式，退出一定写 active:false。
  Studio 第一次看到主程序在时无条件发一次 robot.pose {"active": false}（Studio 崩了重启，不知道上次有没有留下）。
- **看门狗**（三 V11）：头 / 身体的最后一帧非零、0.6 秒没新帧 → 头全 0、身体回中（还在身体模式）；模式 3 秒没报「还在」
  → 退出模式（发 active:false）。按住的键 0.45 秒（X 0.3 秒）没报就当松开。都在 25 Hz 的后台线程（_tick_loop）里。
- **按住的键**（三 V4）：LT / RT / X 走 keys 通道（页面按下、松开、按住每 0.15 秒报一次），后台 25 Hz 按「按着什么」发：
  嘴 = 按着 LT 或 RT × mouth_percent、LT 一直 wheee（hold:true，松开发 hold:false）、RT 按下那一下嘎一声、X 按住每 40 ms
  再发一次 robot.do（连滚，padd:1182-1211）。不复用 _run_action；跟正在做的头 / 嘴类动作互斥。
- **实体手柄连着**（镜像）：padd 哪怕没人碰也每 0.1 秒发一次零速度（padd main.rs:962-977），每拍发一次嘴
  （padd main.rs:1351-1363）→ 网页摇杆、按键、模式、图上的一次性键（src:"svg"）都不收；不接受 drive on；网页没在开就不发
  「停」（不去打断实体手柄；头 / 身体的清理是例外，见上）。下面的大按钮（站起来 / 开始走 / 坐下 / 技能 / 叫一声）是一次性的
  请求，跟 padd 不抢，照常能用。
- **卸力之后 2 秒内**不接受站、走、坐、技能、摇杆、动作（上力、回站姿、卸力共用一个请求槽，后写的赢：
  robotd intents.rs:24-36、149-157）。卸力时间三处来：app 的 run_relax 同步调 note_relax()（顶上按钮、空格、图上 Select）、
  robotd.py 的卸力计数变了、回读看到扭矩从开变关（手柄 Select、robotctl、摔倒、低电量：审查二 I2）。
- **坐着没坐着要读新鲜的 robot.policies.sitting**（审查二 S2）：读不到就不让点「坐下 / 站起来」，不靠猜
  （lib.rs:2362-2368：站起来是两个不同的调用，猜错会让坐着的鸭子每按两次就坐下一次）。Start、十字下、绑了 sit_toggle 的键一样。

接口出处（官方仓库 pollen-robotics/microduck；fork 指 fork 固件）：
| 页面上的         | 发给 robotd                                   | 出处 |
|------------------|-----------------------------------------------|------|
| 站起来（软着时） | robot.init（请求）                            | lib.rs:553-558；main.rs:4550-4563；fork main.rs:5898-5908（总线没就绪会拒） |
| 站起来（坐着时） | robot.do {"skill":"sit_toggle"}               | lib.rs:2362-2370（坐着要用 sit_toggle，init 会跟锁存打架）；main.rs:659-669 |
| 开始走           | robot.enable {"on":true,"toggle":false}       | lib.rs:2685-2704；main.rs:4518-4548；fork main.rs:5780-5850（HOME/IMU/策略几道闸） |
| 停下（站好）     | robot.stop，再 robot.enable {"on":false}       | lib.rs:537-540；main.rs:4513-4516（stop 只清速度） |
| 坐下             | robot.do {"skill":"sit_toggle"}               | main.rs:4223-4255（策略没在跑、没到 HOME 都会拒）；main.rs:4158-4168 |
| 技能             | robot.do {"skill":名字}                       | lib.rs:2338-2346（名字看 robot.policies 的 skills）；main.rs:4136-4176 |
| 叫一声           | robot.sound {"tag":…}（请求）                 | lib.rs:590-600、2094-2140；main.rs:4300-4319 |
| 摇杆（走路）     | robot.move 通知 {vx,vy,vyaw}                  | lib.rs:2041-2051；main.rs:3589-3597、3631-3636 |
| 摇杆（头模式）   | robot.head 通知（换算见 sticks_to_intent）     | fork padd:1427-1445 |
| 摇杆（身体模式） | robot.pose 通知 {z,roll,pitch,active}         | fork padd:316-320、1447-1460；fork lib.rs:2320-2346 |
| LT / RT / X 按住 | robot.mouth / robot.sound / robot.do 通知     | fork padd:1182-1211、1351-1396 |
| 点头、歪头、张嘴 | robot.head / robot.mouth 通知                 | 见 pad_combo.py 文件头 |
| 十字右（图上）   | robot.rebootMotors {"ids":[]}（请求，空 = 全部） | 官方 padd main.rs:530、750-765；fork（handover 版）main.rs:2731-2758（先全身卸力再重启，之后软着）、6050-6056（从不拒） |
| 十字上按住 3 秒  | robot.setMode {"mode":"roller" / "walk"}（请求） | 官方 padd main.rs:247-252、766-806；fork（handover 版）main.rs:5700-5736（没装轮滑模型会拒；autodetect 版一样）、2870-2930 |

十字右、十字上照官方（用户 10-19 定：沿用官方方案）：十字右按一下就发、不用确认，不看方向盘、不占「上一个还没回话」
（跟卸力一样是救急的路）；主程序收下以后记一次卸力（note_relax：2 秒内不让上力、叫停动作、清头 / 身体）。十字上在页面上
按住 3 秒才发（计时在页面），带 expect（要切到哪个）；后端按 robot.policies 的 mode 再判一遍，不一样就拒。

网页手柄的 Start（三 V7、V8）：页面按 motion.start 算出 Start 会做什么，发 start {expect}；后端挑出来的跟 expect 不一样就拒
（「状态刚变了，再按一次」）。实体手柄 Start 跟网页的差别（fork padd:1113-1148、fork robotd:5795-5873）：坐着时实体是
robot.init 直线回 HOME，网页走 sit_toggle（sitstand 网络起身，更柔和）；在走时实体是 toggle 关（渐变回 HOME），网页是
stop + enable off（直接给 HOME 目标）。
"""
import itertools
from contextlib import nullcontext
import json
import math
import os
import re
import threading

from . import pad_combo as C
from .estop import STARTING_PHASES, relaxed, torque_off
from .log import log_cmd
from .pad_combo import Refused
from .rpc import RpcError, Timeout, Unreachable
from .updates import neutral_robot_command

MAX_V = 0.3               # 满杆 m/s：padd 默认 --max-linear 0.3（padd main.rs:158-164），官方网页遥控同一个数
MAX_W = 1.5               # 满杆 rad/s：padd 默认 --max-angular 1.5（padd main.rs:166-168）
V_CEIL, W_CEIL = 0.6, 3.0   # 从配置里读到的再大也不超过这个（读错了也不至于飞出去）
HEAD_RAD = 2.5            # 头满杆 rad：padd 默认 --max-head 2.5（fork padd:201-205，「the network itself decides how far」）
HEAD_CEIL = 2.5           # 配置里写得再大也按这个算
HEAD_CHOICES = (0.5, 1.0, 2.5)   # --input-settings 文件里 head_rad 只认这三个（fork padd/src/input_settings.rs:16-21）
BODY_Z_UP, BODY_Z_DOWN, BODY_ANGLE = 0.010, 0.025, 0.2618   # 身体模式满杆（fork padd:316-320）
BODY_TRAIN_ANGLE = 0.26   # 训练范围 roll / pitch ±0.26 rad、z −0.025..+0.010 m（fork lib.rs:2320-2325）：发之前夹到这里
DEADZONE = 0.1            # 摇杆死区，每个轴单独算、不重新缩放（fork padd:184-187、1342-1349）
LEASE_S = 6.0             # 拿着方向盘的页面这么久没推摇杆、没说「还按着」：松开
RELAX_GUARD_S = 2.0
HOLD_S = 0.45             # 动作按住时页面每 0.15 秒说一次「还按着」；这么久没说就停
KEYS_HOLD_S = 0.45        # 按住的键（LT / RT）：页面每 0.15 秒报一次，这么久没报当松开（三 V4）
X_HOLD_S = 0.3            # X 单独短一点：连滚窗口是 0.15 秒（fork control.rs:42-47），页面掉线最多多滚一次
STICK_STALE_S = 0.6       # 头 / 身体最后一帧非零、这么久没新帧：回中（三 V11）
MODE_HB_S = 3.0           # 头 / 身体模式这么久没报「还在」：退出模式（三 V11）
TICK_S = 0.04             # 后台 25 Hz：按住的键、看门狗、清理
RELAX_CLEAR_DELAY_S = 0.1   # 卸力引起的头 / 身体清理排在卸力请求之后（三 V1 第 5 条）
MOVE_MIN_GAP_S = 0.04     # 同一通道两帧至少隔这么久；来得太密的不丢，等一下发最新的那个（审查 21）
WALK_STOP_WAIT_S = 1.0
WALK_VERIFY_S = 3.0       # 「开始走」回执之后再回读这么久，看到 policy_enabled 才算真的开起来
WALK_VERIFY_POLL_S = 0.1
CMD_TIMEOUT_S = 3.0
BINDINGS_STALE_S = 10.0   # pad.bindings 这么旧了，按图上的键之前先拉一次（三 V14；padd 每秒重读配置 padd:806-817）
LOW_BATTERY = 10          # 不再用：电量低不拦「站起来」（用户 10-18 电池只警告，见 studio/battery.py）
PADD_UNIT = "/etc/systemd/system/padd.service"        # 单元装在这里：官方 scripts/board-test.sh:800-806
PADD_DROPIN_DIR = "/etc/systemd/system/padd.service.d"
PADD_INPUT_ACK = "/run/padd/input-settings.json"      # padd 用上幅度设置以后写的回执（fork padd/src/input_settings.rs:63-74）
# 走单独的小线程池（4 条：三 V13），不跟配对、蓝牙、一次性请求这些慢调用挤
FAST_OPS = ("move", "stop", "sticks", "keys", "mode", "action_hold", "action_stop", "drive", "watch")
FAST_WORKERS = 4
GONE_KEEP = 500           # 记住多少个断开 / 关掉的连接
INF = float("inf")

POSTURE_CN = {"absent": "主程序没在跑", "starting": "主程序在启动", "commissioning": "调试模式", "sitting": "坐着",
              "limp": "软着", "walking": "在走", "policy": "站着（准备好走了）", "holding": "站着（保持）",
              "homing": "在站起来", "unknown": "姿势读不出来", "servo_session": "在调舵机"}
DRIVING = ("policy", "walking")
MODES = ("drive", "head", "body")
MODE_CN = {"drive": "走路", "head": "头模式", "body": "身体模式"}
DRIVE_MODE_CN = {"walk": "走路", "roller": "轮滑"}     # robot.setMode / robot.policies 的 mode（十字上按住 3 秒切）
HELD_KEYS = ("lt", "rt", "x")
KEY_CN = {"a": "A", "x": "X", "lb": "LB", "rb": "RB", "dpad_down": "十字下", "lt": "LT", "rt": "RT"}
BIND_KEYS = ("a", "x", "lb", "rb", "dpad_down")
# 官方默认绑定（fork robotd-params lib.rs:175-215）；读不到 pad.bindings 时按它
DEFAULT_BIND = {"a": "ground_pick", "x": "roulade", "lb": "kick_left", "rb": "kick_right", "dpad_down": "sit_toggle"}
HEAD_ZERO = {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0}
POSE_OFF = {"active": False}                                   # 一定写 active:false（默认是 true）
POSE_CENTER = {"z": 0.0, "roll": 0.0, "pitch": 0.0, "active": True}
_FLAG = re.compile(r"--(max-linear-backward|max-linear|max-angular)(?:=|\s+)(\S+)")
_FLAG_HEAD = re.compile(r"--max-head(?:=|\s+)(\S+)")
_FLAG_PATH = re.compile(r"--(input-settings|config)(?:=|\s+)(\S+)")
NO_SIT_INFO = "读不到它是不是坐着（robot.policies 读不到或者太旧）：等几秒再点。猜错了会让坐着的鸭子站起来"
MIRROR_SVG = "实体手柄连着：图上只跟着它显示，网页上按的不收（跟实体手柄抢会被它每 0.1 秒发的指令盖掉）"
MODE_IDLE = "策略没在跑：摇杆不会让头 / 身体动（Y / B 照样能切；点「开始走」以后才管用）"


def plain_refusal(reason):
    """robotd 拒绝时的原文 → 人话（fork 的中文原因原样给）。"""
    r = str(reason or "")
    low = r.lower()
    if "policy is not driving" in low:
        return "策略没在跑：先点「开始走」（或者按手柄 Start 两次）"
    if "home pose" in low and "still" in low:
        return "它还在回站姿，等一秒再点"
    if "no skill named" in low:
        return f"这只鸭子没有这个技能（原文：{r}）"
    if "no skills configured" in low:
        return "这只鸭子没配技能"
    if "no voice" in low:
        return "这只鸭子没有声音：音频关了，或者声音包是空的（在鸭子上运行 sounds ensure-bank）"
    if "read-only" in low:
        return "主程序现在是只读的（调试模式），动作都不接受"
    if "roller onnx models are not installed" in low:
        return "这只鸭子没装轮滑模型：切不了，还是走路"
    if "nothing to switch between" in low:
        return "这只鸭子没装走路的策略：没有可切的"
    if "requires stationary home" in low:
        return "要站好不动（回到站姿、没在做技能）才能切"
    return f"主程序没接受：{r}" if r else "主程序没接受（没说原因）"


def _exec_line(texts):
    """padd 单元文件和 drop-in [(路径, 文本)] 里最后生效的 ExecStart（空的 ExecStart= 是「清掉前面的」）。"""
    exec_line, exec_src = None, None
    for path, text in texts:
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if line.startswith("ExecStart="):
                val = line[len("ExecStart="):].strip()
                exec_line, exec_src = (val or None), (path if val else None)
    return exec_line, exec_src


def parse_speed_limits(texts):
    """padd 单元文件和 drop-in 的文本 [(路径, 文本)] → {"v", "w", "src"}。写坏的数（比如 1.2.3）跳过，不抛（审查二 I3）。"""
    exec_line, exec_src = _exec_line(texts)
    found, bad = {}, []
    for k, v in _FLAG.findall(exec_line or ""):
        try:
            x = float(v)
        except ValueError:
            bad.append(f"--{k} {v}")
            continue
        if math.isfinite(x) and x > 0:
            found[k] = x
        else:
            bad.append(f"--{k} {v}")
    v = min([found[k] for k in ("max-linear", "max-linear-backward") if k in found] or [MAX_V])
    w = found.get("max-angular", MAX_W)
    if found:
        src = f"padd 的配置（{exec_src}）"
    elif texts:
        src = "官方默认（padd.service 里没改）"
    else:
        src = "官方默认（读不到 padd 的配置）"
    if bad:
        src += f"；看不懂的参数没用：{'、'.join(bad)}"
    capped = v > V_CEIL or w > W_CEIL
    return {"v": min(v, V_CEIL), "w": min(w, W_CEIL), "src": src + ("，超过上限的按上限算" if capped else "")}


def parse_padd_inputs(texts):
    """ExecStart 里的 --max-head、--input-settings、--config（三 V9、V18）。fork 的 drop-in 长这样
    （fork packaging/gamepad.py:94）：startup_gate.py … --exec …/padd --socket … --config /etc/robot/feetech-ft5/robotd.toml
    --max-head 2.5 --input-settings /etc/robot/feetech-ft5/pad-settings.json。写坏的数跳过、不抛。"""
    exec_line, exec_src = _exec_line(texts)
    out = {"max_head": None, "input_settings": None, "config": None, "bad": [], "src": exec_src}
    for v in _FLAG_HEAD.findall(exec_line or ""):
        try:
            x = float(v)
        except ValueError:
            out["bad"].append(f"--max-head {v}")
            continue
        if math.isfinite(x) and x > 0:
            out["max_head"] = x
        else:
            out["bad"].append(f"--max-head {v}")
    for k, v in _FLAG_PATH.findall(exec_line or ""):
        out["input_settings" if k == "input-settings" else "config"] = v
    return out


def _padd_settings(d):
    """padd 认不认这份幅度设置（fork padd/src/input_settings.rs:7-21）：只能有 mouth_percent、head_rad 两个字段
    （deny_unknown_fields：多一个字段整份拒，审查 R10），嘴 0–100，头只认 0.5 / 1.0 / 2.5。认 → (头, 嘴)，不认 → None。"""
    if not isinstance(d, dict) or set(d) != {"mouth_percent", "head_rad"}:
        return None
    m, h = d["mouth_percent"], d["head_rad"]
    if not (isinstance(m, (int, float)) and not isinstance(m, bool) and math.isfinite(m) and 0 <= m <= 100):
        return None
    if not (isinstance(h, (int, float)) and not isinstance(h, bool) and float(h) in HEAD_CHOICES):
        return None
    return float(h), float(m)


def input_limits(inputs, settings_text=None, settings_error=None, ack=None):
    """跟 padd 一样算头、嘴的幅度（fork padd/src/input_settings.rs:9-27、36-46、63-80）：先是嘴 100%、头 --max-head（没写就 2.5）；
    --input-settings 文件 padd 认（_padd_settings）就两个都用文件里的，不认就保持前面的。
    ack：padd 自己写的回执里的 settings（_read_inputs 核过写回执的 padd 还活着、文件对得上）——那是 padd 正在用的数，以它为准。
    → {"head", "mouth"（百分比）, "src"}。"""
    head = inputs.get("max_head") or HEAD_RAD
    src = f"--max-head {head:g}" if inputs.get("max_head") else "官方默认（头 2.5 rad、嘴 100%）"
    mouth = 100.0
    path = inputs.get("input_settings")
    got = _padd_settings(ack) if path and ack is not None else None
    if got is not None:
        head, mouth = got
        src = f"padd 正在用的（它的回执 {PADD_INPUT_ACK}）"
    elif path:
        if settings_error is not None:
            src += f"；{path} 读不出来（{settings_error}），按前面的"
        else:
            try:
                got = _padd_settings(json.loads(settings_text or ""))
            except ValueError:
                got = None
            if got is not None:
                head, mouth = got
                src = f"padd 的幅度设置（{path}）"
            else:
                src += f"；{path} 里的 padd 不认（只能有 mouth_percent、head_rad 两项，头只认 0.5 / 1.0 / 2.5，嘴 0–100），按前面的"
    if inputs.get("bad"):
        src += f"；看不懂的参数没用：{'、'.join(inputs['bad'])}"
    if head > HEAD_CEIL:
        head, src = HEAD_CEIL, src + f"，头超过 {HEAD_CEIL:g} rad 的按 {HEAD_CEIL:g} 算"
    return {"head": head, "mouth": mouth, "src": src}


def _dz(v):
    return 0.0 if abs(v) < DEADZONE else v


def sticks_to_intent(mode, lx, ly, rx, ry, lim, head_rad):
    """网页摇杆（-1..1，x 向右、y 向下为正，跟 TouchStick 一样）→ 发给 robotd 的那一帧（审查三 附 2，照 fork padd 原样，
    正负号也照抄：padd:1437-1439「the prototype's alpha mapping, signs included … verified on hardware there」）。
    padd 的坐标 y 向上为正：LX = dz(lx)、LY = −dz(ly)、RX = dz(rx)、RY = −dz(ry)。返回 (method, params)。"""
    LX, LY, RX, RY = _dz(lx), -_dz(ly), _dz(rx), -_dz(ry)
    r = lambda x: round(x, 4) + 0.0    # noqa: E731 —— +0.0 去掉 -0.0
    if mode == "head":                  # padd:1427-1445
        return "robot.head", {"neck_pitch": r(RY * head_rad), "head_pitch": r(-LY * head_rad),
                              "head_yaw": r(-LX * head_rad), "head_roll": r(RX * head_rad)}
    if mode == "body":                  # padd:1447-1460，再夹到训练范围（fork lib.rs:2320-2325）
        z = LY * (BODY_Z_UP if LY >= 0 else BODY_Z_DOWN)
        clampa = lambda a: max(-BODY_TRAIN_ANGLE, min(BODY_TRAIN_ANGLE, a))   # noqa: E731
        return "robot.pose", {"z": r(max(-BODY_Z_DOWN, min(BODY_Z_UP, z))), "roll": r(clampa(RX * BODY_ANGLE)),
                              "pitch": r(clampa(RY * BODY_ANGLE)), "active": True}
    v, w = lim["v"], lim["w"]           # padd:1415-1426：vx = LY×0.3、vy = −LX×0.3、vyaw = −RX×1.5，右摇杆的 Y 不用
    return "robot.move", {"vx": r(LY * v), "vy": r(-LX * v), "vyaw": r(-RX * w)}


class _Abort(Exception):
    pass


class DriveMixin:
    # ================= 初始化 =================

    def _drive_init(self, state_dir):
        self.state_dir = state_dir
        self._send_lk = threading.Lock()      # 发给 robotd 的「检查 + 发送」在这把锁里：顺序就是落地的顺序（先拿它、再拿 _lk）
        self._seqs = {}                       # 连接 → {"hi", "stop", "life", "mode", "latest":{通道}, "sent":{通道}}：换方向盘也不清
        self._gone = {}                       # 断开的连接（mark_gone）→ True；永远拿不回方向盘
        self._closed = {}                     # 离开手柄页的连接（drive off）→ True；drive open / on 才解开
        self._halt_after_gone = {}
        self._conn_ids = itertools.count(1)
        self._last_sent_at = {}               # 通道 → 上一次发出的时间（同一通道至少隔 40 ms）
        self._web_driving = False
        self.virtual = {"owner": None, "last": [0.0, 0.0, 0.0], "at": None}
        self._driver = None                   # {"conn", "by", "since", "seen"}
        self._cmd_busy = False
        self._last_cmd = None                 # 最近一次站 / 走 / 坐 / 技能 / 声音的结果（推给所有页面）
        self._relax_presses = None
        self._relax_noted = 0                 # note_relax 被调了几次（测急停用：测的时候顶上卸力按了就不算）
        self._relax_at = -1e9
        self._torque_was = None
        self._action = None                   # 正在做的动作 / 组合动作
        self._last_action = None
        self._pad_said = False                # 「实体手柄连着，网页让位」这句只记一次
        self._limits = parse_speed_limits([])
        self._inputs = input_limits({})       # 头 / 嘴的幅度（三 V9）
        self._imu_head, self._imu_head_why = None, None   # [pad_imu_head_control] enabled（三 V18）
        self.list_dir = os.listdir
        self._user_combos = C.load_user_combos(state_dir, self.log)
        # ---- 第三轮：网页手柄 ----
        self._latch = {"head": None, "body": None}   # 网页置过的头 / 身体：{"conn", "nonzero", "at"}；None = 没置过或者清掉了
        self._modes = {}                      # 连接 → {"mode": "head" / "body", "hb"}；不在里面 = 走路
        self._mode_exit = {}                  # 连接 → {"why", "at"}：后端替它退出了模式（推给那个页面看）
        self._clears = []                     # 待发的清理 [{"conn", "why", "due", "tries"}]（_request_clear 只改这里）
        self._keys = {}                       # 连接 → 按住的键（见 _keys_op）
        # 连接 → {"skill", "x_down"}：这一下 X（按下的时间 x_down）的请求被接受了、X 还按着，按住才连滚（审查 R5）
        self._x_armed = {}
        self._was_driving = False
        self._pose_reset = "pending"          # Studio 第一次看到主程序在：发一次 robot.pose {"active": false}
        self._pose_reset_tries = 0
        self._bindings_due = 0.0
        self._tick_evt = threading.Event()
        self._tick_stop = threading.Event()
        self._tick_said = set()               # 后台线程同一种发不出去只记一次
        self._tick_thread = None

    def _drive_start(self):
        """PadOps.__init__ 最后调：起 25 Hz 的后台线程（没事做时睡着，有事才醒）。留着句柄：关的时候要等它停（审查 R8）。"""
        self._tick_thread = threading.Thread(target=self._tick_loop, name="pad-tick", daemon=True)
        self._tick_thread.start()

    def new_conn(self):
        """给一个页面连接起名字（全局递增，不用 id(ws)：Python 会重用回收了的对象的 id，审查二 I4）。
        **新连接默认「不在手柄区」**（总览重排审查 O1）：手柄并进总览以后，页面只有手柄那一块真的在屏幕上才发
        drive {open}；没发过 open 的连接，摇杆、按住的键、头 / 身体、模式一律丢（一次性命令照旧不看这个：三 V5）。"""
        conn = f"ws{next(self._conn_ids)}"
        with self._lk:
            self._remember(self._closed, conn)
        return conn

    def executor_for(self, args):
        """app 用：move / sticks / keys 这些快的走单独的小线程池（审查二 一般 4、三 V13）；None = 默认线程池。"""
        fast_motion = (args or {}).get("op") == "motion" and (args or {}).get("action") in ("heartbeat", "stop", "pause", "record_pause", "idle_stop")
        return self._fast_pool if (args or {}).get("op") in FAST_OPS or fast_motion else None

    # ================= 谁在开 =================

    def _pad_attached(self, now):
        """实体手柄连着吗：(True / False / None, 说明)。None = 认不出来（审查二 一般 3）。
        旁路看到 attached 最直接；没有时看 configd 15 秒内的 pad.status（连着 + padd 在跑）。"""
        if self.tap is not None and self._tap_on and self.tap_state == "attached":
            alive = getattr(self.tap, "alive", None)
            if alive is None or alive():
                return True, "padd 旁路看到实体手柄"
        st = self._status if isinstance(self._status, dict) else None
        fresh = st is not None and self._status_at is not None and now - self._status_at < 15
        if fresh:
            if any(isinstance(p, dict) and p.get("connected") for p in st.get("pads") or []) and st.get("driver") == "active":
                return True, "configd 说手柄连着、padd 在跑"
            return False, None
        if self._worker_error:
            return None, f"看手柄的后台线程出错了（{self._worker_error}），刷新手柄页会重来"
        if self.tap is not None and self._tap_on and self.tap_state in ("denied", "error"):
            return None, f"configd 读不到，按键也读不到（{self.tap_why or self.tap_state}）"
        return False, None

    def _owner(self, now):
        if self._pad_attached(now)[0]:
            return "pad"
        if self.virtual["at"] is not None and now - self.virtual["at"] < 0.6 and self._web_driving:
            return "web"
        return None

    def _lease_holder(self, now):
        d = self._driver
        return d if d is not None and now - d["seen"] < LEASE_S else None

    def _seq_of(self, conn):
        q = self._seqs.get(conn)
        if q is None:
            # keys：按住的键最后一个序号。放这里、不放在 _keys[conn] 里：那一项松开以后会被删掉，
            # 删了以后比「松开」更旧的「按下」晚到就又按上了（审查 R4）
            q = self._seqs[conn] = {"hi": -1, "stop": -1, "life": -1, "mode": -1, "keys": -1, "latest": {}, "sent": {}}
            if len(self._seqs) > GONE_KEEP * 2:
                for k in list(self._seqs)[:GONE_KEEP]:
                    if k != conn and (self._driver or {}).get("conn") != k:
                        self._seqs.pop(k, None)
        return q

    def _remember(self, table, conn):
        table[conn] = True
        while len(table) > GONE_KEEP:
            table.pop(next(iter(table)))

    def _take_lease(self, conn, by, now, force=False, reopen=False):
        """拿方向盘（在 _lk 里调）。断开的连接永远不给；离开了手柄页的（closed）只有 drive on（reopen）才给——
        以前「按下动作也解开」的口子去掉了（三 V5：按下动作马上切页，off 先到、action_start 后到，动作照样开始）；
        别人拿着、还新鲜：不给，除非 force（页面点了「接管」）。方向盘 6 秒没动静自动换人时，原来那个页面置过的头 / 身体、
        按住的键、模式都清掉（三 V1 第 4 条），交给后台线程发。"""
        if conn in self._gone:
            raise Refused("这个页面已经断开了")
        if conn in self._closed and not reopen:
            raise Refused("这个页面不在手柄这一块（滚走了、切到别的页，或者还没滚到）：滚到手柄那里再按")
        d = self._driver
        if d is not None and d["conn"] == conn:
            d["seen"] = now
            if by:
                d["by"] = by
            if reopen:
                self._closed.pop(conn, None)
            return None
        held = self._lease_holder(now)
        if held is not None and not force:
            raise Refused(f"{held['by'] or '另一个页面'}正在遥控（{now - held['since']:.0f} 秒了）。要接管就点「接管」")
        if reopen:
            self._closed.pop(conn, None)
        self._driver = {"conn": conn, "by": by, "since": now, "seen": now}
        if d is not None and d["conn"] != conn and held is None:
            self.log.info(f"网页遥控换人：{by or '页面'}（原来的 {d['by'] or '页面'} {now - d['seen']:.0f} 秒没动静，已自动松开）",
                          cat="安全", dedup=False)
            self._request_clear(d["conn"], "方向盘 6 秒没动静，换了人")
        return held or d

    def _touch_lease(self, conn, now):
        d = self._driver
        if d is not None and d["conn"] == conn:
            d["seen"] = now

    def _drive(self, args, conn, by):
        """页面进手柄页（open）、离开（on:false）、点「接管」（on:true, force）。open / off 带页面的递增序号，
        按连接记最后一个，比它旧的丢掉（三 V5：快线程池里 off 紧接着 open 可能先后颠倒）。"""
        now = self.clock()
        seq = self._seq(args)
        if args.get("open"):
            with self._lk:
                if conn in self._gone:
                    raise Refused("这个页面已经断开了")
                q = self._seq_of(conn)
                if seq is not None and seq <= q["life"]:
                    return {"dropped": "旧的（这个页面后来又离开 / 回来过）"}
                if seq is not None:
                    q["life"] = seq
                self._closed.pop(conn, None)            # 只解开 closed：不拿方向盘、不停（只开着页面不挡别人）
            return {"open": True}
        if args.get("on"):
            with self._lk:
                attached, _ = self._pad_attached(now)
                if attached:
                    raise Refused("实体手柄连着：网页摇杆只跟着它动、只显示，打不开（打开了也会被它每 0.1 秒发的「停」盖掉）")
                q = self._seq_of(conn)
                if seq is not None and seq <= q["life"]:
                    return {"dropped": "旧的（这个页面后来又离开过手柄页）"}
                if seq is not None:
                    q["life"] = seq
                prev = self._take_lease(conn, by, now, force=bool(args.get("force")), reopen=True)
            if prev is not None and prev.get("conn") != conn:
                self.log.warn(f"{by or '一个页面'}接管了遥控（原来是 {prev.get('by') or '另一个页面'}）", cat="安全",
                              hint="原来那个页面的摇杆、按键、动作马上就不管用了", dedup=False)
                self._halt(f"被 {by or '另一个页面'} 接管了", prev.get("conn"))
            return {"driver": by}
        with self._lk:
            q = self._seq_of(conn)
            if seq is not None and seq <= q["life"]:
                return {"dropped": "旧的（这个页面后来又回到了手柄页）"}
            if seq is not None:
                q["life"] = seq
            if conn not in self._gone:
                self._remember(self._closed, conn)
            top = max(q["hi"], seq if seq is not None else -1)
            q["stop"] = max(q["stop"], top)                  # 离开之前发出的摇杆帧都作废
            q["hi"] = max(q["hi"], top)
            mine = self._driver is not None and self._driver["conn"] == conn
            background_music = self.motion_studio.background_music(conn)
            if mine and not background_music:
                self._driver = None
        if background_music:
            # The drive area closes and late manual frames stay fenced, while
            # standalone music remains owned by this still-connected socket.
            pass
        elif mine:
            self._halt("离开了手柄区", conn)            # 里面排清理、发
        else:
            self._request_clear(conn, "离开了手柄区")
            self._flush_clears(force=True)
        # 页面说为什么离开（切到了别的页 / 滚出了手柄这一块 / 转到后台：总览重排审查 O1）。同一句 1 分钟内只记一次（日志去重），
        # 来回滚不会刷屏；原因是页面给的，截短了只用来记日志
        why = str(args.get("why") or "")[:40]
        self.log.info(f"{by or '页面'}离开了手柄区：{why or '没说原因'}", cat="手柄")
        return {}

    def mark_gone(self, conn):
        """页面的 WebSocket 断了：app 在事件循环里**同步**调（只占锁、不碰网络）。之后这个连接晚到的指令一律丢，
        也拿不回方向盘；它在做的动作马上叫停、它置过的头 / 身体交给后台线程清。真正发「停」在 drop_client（线程池里）。"""
        if conn is None:
            return False
        with self._lk:
            if conn in self._gone:
                return False
            self._remember(self._gone, conn)
            self.motion_studio.invalidate("页面断开了", conn)
            self._closed.pop(conn, None)
            self._seq_of(conn)["stop"] = INF
            mine = self._driver is not None and self._driver["conn"] == conn
            if mine:
                self._driver = None
            a = self._action
            act = a is not None and a["conn"] == conn
            if act:
                a["why"] = a["why"] or "页面断了"
                a["stop"].set()
            self._request_clear(conn, "页面断了")
            self._mode_exit.pop(conn, None)         # 页面已经没了：不用再告诉它（审查 R9）
            need = (mine and self._web_driving) or act
            self._halt_after_gone[conn] = need
        return need

    def drop_client(self, conn, by=None):
        """页面断了（app 在线程池里调，mark_gone 之后）：它在开就发一次「停」；它置过的头 / 身体清掉。没先调 mark_gone 也行。"""
        with self._lk:
            need = self._halt_after_gone.pop(conn, None)
        if need is None:
            self.mark_gone(conn)
            with self._lk:
                need = self._halt_after_gone.pop(conn, False)
        if need:
            self.log.info(f"页面断了（{by or conn}）：网页遥控松开、发一次「停」", cat="安全", dedup=False)
            self._halt("页面断了", conn)
        else:
            self._flush_clears(force=True)

    def _halt(self, why, conn=None, clear_all=False):
        """停下网页这边：conn 的动作打断、按住的键松开、头 / 身体清掉、模式退出，它的序号以内的帧作废、发一次 0。
        clear_all：头 / 身体、按住的键、模式不管是哪个页面的都清（点「停下（站好）」：三 V1 第 7 条）。
        镜像时（实体手柄连着）网页没在开就不发 move 0，免得打断实体手柄（审查二 一般 2）；头 / 身体的清理镜像时也发（三 V1）。
        发不出去也没关系：robotd 0.5 秒收不到自己停。"""
        self.motion_studio.invalidate(why, None if clear_all else conn)
        self.motion_studio.tick()
        self._action_stop(conn, why)
        self._request_clear(None if clear_all else conn, why)
        if self.robot is None:
            return
        with self._send_lk:
            with self._lk:
                now = self.clock()
                if conn is not None:
                    q = self._seq_of(conn)
                    q["stop"] = max(q["stop"], q["hi"])     # 不加 1：页面紧接着的下一帧照常（审查二 一般 1）
                was = self._web_driving
                self._web_driving = False
                attached, _ = self._pad_attached(now)
                send = was or not attached
                if send:
                    self.virtual.update(last=[0.0, 0.0, 0.0], at=now)
            if send:
                try:
                    self._notify_output("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
                except (Unreachable, Timeout) as e:
                    self.log.warn("「停」没发出去", cat="安全", step="robot.move", raw=str(e),
                                  hint="鸭子 0.5 秒收不到指令也会自己停（robotd 的 deadman）")
        self._flush_clears(force=True)
        if was:
            self.log.info(f"网页遥控松开了（{why}）", cat="手柄")

    # ================= 头 / 身体的清理（三 V1）、模式 =================

    def _mode_of(self, conn):
        m = self._modes.get(conn)
        return m["mode"] if m else "drive"

    def _request_clear(self, conn, why, delay=0.0, say=True):
        """要把 conn 置过的头 / 身体清掉（conn=None：谁置的都清），同时退出它的头 / 身体模式、松开它按住的键。
        **只改内存、不碰网络、不抛**：事件循环里（note_relax、feed_robot、mark_gone）也能调。真正发在 _flush_clears。
        清理**一律排一条**，不看 _latch 现在有没有记录（审查 R1）：第一帧头 / 身体可能正在路上（过了检查、还没写 _latch）。
        那一帧从第二次检查到写 _latch 都拿着 _send_lk，_flush_clears 也在 _send_lk 里才看 _latch，所以一定排在它后面、
        看得到它写的；真没有记录的条目 flush 时直接扔掉。say=False：退出模式那一行由调用方自己记（同一件事只记一行）。"""
        try:
            with self._lk:
                now = self.clock()
                self._clears.append({"conn": conn, "why": why, "due": now + delay, "tries": 0})
                for c in [c for c, x in self._mode_exit.items() if now - x["at"] > 10]:
                    self._mode_exit.pop(c, None)          # 只给页面看 10 秒：过期的删掉，不让它一直涨（审查 R9）
                for c in list(self._modes):
                    if conn is None or c == conn:
                        m = self._modes.pop(c)
                        self._mode_exit[c] = {"why": why, "at": now}
                        if say:
                            self.log.info(f"退出{MODE_CN[m['mode']]}（{why}）", cat="手柄", dedup=False)
                for c, st in self._keys.items():
                    if conn is None or c == conn:
                        st["end"] = st.get("end") or why
                if conn is None:
                    self._x_armed.clear()
                else:
                    self._x_armed.pop(conn, None)
            self._tick_evt.set()
        except Exception:  # noqa: BLE001 —— 卸力、断开的路上不能因为这里出错
            pass

    def _flush_clears(self, force=False):
        """发到期的清理（在线程里调：后台线程、_halt、drive off、drop_client）。头发全 0、身体发 {"active": false}；
        只发网页真置过的（_latch 里有记录的）。决定和发送都在 _send_lk 里：跟摇杆帧的「检查 + 发送」不会交错。"""
        if self.robot is None:
            with self._lk:
                self._clears.clear()
            return
        with self._send_lk:
            with self._lk:
                now = self.clock()
                due = [c for c in self._clears if force or c["due"] <= now]
                if not due:
                    return
                ids = {id(c) for c in due}
                self._clears = [c for c in self._clears if id(c) not in ids]
                sends = []
                for kind, method, params in (("head", "robot.head", HEAD_ZERO), ("body", "robot.pose", POSE_OFF)):
                    lat = self._latch[kind]
                    if lat is None:
                        continue
                    hit = next((c for c in due if c["conn"] is None or c["conn"] == lat["conn"]), None)
                    if hit is not None:
                        self._latch[kind] = None
                        sends.append((kind, method, params, hit, lat))
            for kind, method, params, req, lat in sends:
                try:
                    self._notify_output(method, dict(params))
                    self.log.info(("头回正" if kind == "head" else "身体回到标称、退出身体模式") + f"（{req['why']}）",
                                  cat="手柄", raw=f"{method} {json.dumps(params)}", dedup=False)
                except (Unreachable, Timeout) as e:
                    with self._lk:
                        if self._latch[kind] is None:
                            self._latch[kind] = lat                 # 还算网页置着：过 1 秒再试，最多 3 次
                        if req["tries"] < 2:
                            self._clears.append(dict(req, due=self.clock() + 1.0, tries=req["tries"] + 1))
                    self.log.warn("头 / 身体没能回正", cat="安全", step=method, raw=str(e),
                                  hint=("1 秒后再试" if req["tries"] < 2 else "试了 3 次都没发出去：") +
                                  "身体模式留着的话鸭子推不走，按一下手柄 B 两次、或者卸力再站起来")

    def _mode_op(self, args, conn, by):
        """页面按了图上的 Y / B（切头模式 / 身体模式 / 回到走路），或者在头 / 身体模式时每秒报一次「还在」（三 V6、V11）。
        进模式不发任何东西（跟 padd 一样）；网页正在走就先发一次停。离开模式清掉这个页面置过的头 / 身体。
        策略没在跑也能切（padd 也能），只是摇杆帧会被丢掉（三 V12）。"""
        mode = args.get("mode")
        if mode not in MODES:
            raise Refused(f"不认识的模式：{mode}")
        seq = self._seq(args)
        now = self.clock()
        stop_first = False
        with self._lk:
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧模式请求已丢弃", "mode": self._mode_of(conn)}
            if conn in self._gone:
                raise Refused("这个页面已经断开了")
            cur = self._mode_of(conn)
            if mode != "drive":
                if conn in self._closed:
                    return {"dropped": "这个页面不在手柄区", "mode": cur}
                if self._pad_attached(now)[0]:
                    raise Refused(MIRROR_SVG)
            if args.get("hb"):
                # 心跳（页面在头 / 身体模式时每秒一次）只续「还在」，**不进模式**：后端已经替页面退出了（卸力、策略停了、
                # 3 秒没心跳、被接管、实体手柄连上），心跳不能把它要回来，模式以后端为准（审查 R3、三 V6）。
                # 心跳不碰模式的序号：它不改模式，晚到的旧心跳也只会被拒
                if mode == cur:
                    if mode != "drive":
                        self._modes[conn]["hb"] = now
                    return {"mode": cur}
                why = (self._mode_exit.get(conn) or {}).get("why")
                return {"mode": cur, "dropped": f"后端已经退出了{MODE_CN.get(mode, mode)}" + (f"（{why}）" if why else "")}
            q = self._seq_of(conn)
            if seq is not None:
                if seq <= q["mode"]:
                    return {"dropped": "旧的", "mode": cur}
                q["mode"] = seq
            ex = self._mode_exit.get(conn)
            if mode == "drive":
                self._mode_exit.pop(conn, None)         # 页面回到走路 = 它知道后端退出过了（前端跟着后端回走路时会发这一句）
            elif cur == "drive" and ex is not None:
                # 后端刚替这个页面退出了模式、页面还没回过「走路」：这时来的「进头 / 身体模式」多半是页面还没看到退出
                # （心跳、没跟上快照），不能把它要回来（审查 R3、三 V6）。页面跟着回到走路以后再按就进得去
                return {"dropped": f"后端已经退出了{MODE_CN[mode]}（{ex['why']}）：先回到走路，再按一次", "mode": cur}
            note = MODE_IDLE if mode != "drive" and self._posture() not in DRIVING else None
            if mode == cur:
                if mode != "drive":
                    self._modes[conn]["hb"] = now
                return {"mode": cur, "note": note}
            if cur != "drive":
                self._modes.pop(conn, None)
                # 一律排（审查 R1）：这个页面第一帧头 / 身体可能还在路上，_flush_clears 在 _send_lk 里会等它写完 _latch
                self._clears.append({"conn": conn, "why": f"退出{MODE_CN[cur]}", "due": now, "tries": 0})
            if mode != "drive":
                self._modes[conn] = {"mode": mode, "hb": now}
                d = self._driver
                stop_first = self._web_driving and d is not None and d["conn"] == conn
            q["stop"] = max(q["stop"], q["hi"])            # 换模式之前的摇杆帧作废
        self.log.info(f"网页手柄 {'Y' if 'head' in (mode, cur) else 'B'} → {MODE_CN[mode]}（{by or '页面'}）"
                      + (f"：{note}" if note else ""), cat="手柄", dedup=False)
        if stop_first:
            self._stop_move({}, conn, by)                  # padd 在头 / 身体模式每拍发 move 0（padd:1427-1448）
        self._flush_clears(force=True)
        return {"mode": mode, "note": note}

    # ================= 卸力（审查二 I2） =================

    def note_relax(self, by=None, why="卸力了"):
        """顶上「卸力」、空格、图上 Select 按下的那一刻：app 的 run_relax 在事件循环里同步调（只占锁、不碰网络，不抛）。
        记下卸力时间（2 秒内不接受上力）、叫停正在做的动作、头 / 身体的清理排到卸力之后（三 V1 第 5 条）。
        图上十字右（重启舵机，主程序会先全身卸力）收下以后也调，why 写它。"""
        try:
            with self._lk:
                self._relax_at = self.clock()
                self._relax_noted += 1
                self.motion_studio.invalidate(why)
                a = self._action
                if a is not None:
                    a["why"] = a["why"] or why
                    a["stop"].set()
            self._request_clear(None, why, RELAX_CLEAR_DELAY_S)
            self._test_evt.set()
        except Exception:  # noqa: BLE001 —— 卸力路上不能因为手柄页出错
            pass

    def _relax_seen(self, view, now):
        """feed_robot 里调（拿着锁）：卸力计数变了、或者回读扭矩从开变关（手柄 Select、robotctl、摔倒、低电量），都记成卸力。
        返回 True = 这一下是新的卸力。"""
        new = False
        presses = view.get("estop_presses")
        if isinstance(presses, int) and not isinstance(presses, bool):
            if self._relax_presses is not None and presses > self._relax_presses:
                new = True
            self._relax_presses = presses
        bus = view.get("bus")
        if isinstance(bus, dict) and bus.get("mode") in ("motion", "commissioning"):
            on = bool((bus.get("native") or {}).get("torque_state_confirmed") is True) if bus.get("mode") == "motion" \
                else bus.get("torque_state") == "on"
            off = torque_off(bus) or relaxed(bus) is True
            if self._torque_was is True and off:
                new = True
            self._torque_was = True if on else (False if off else self._torque_was)
        if new:
            self._relax_at = now
        return new

    def _drive_feed(self, view):
        """feed_robot 里调（拿着锁，事件循环里：只改内存）：Studio 第一次看到主程序在 → 排一次 pose 复位；
        姿势离开「准备好走」→ 退出模式、清掉头 / 身体（三 V1 第 8 条、V12）。"""
        self.motion_studio.policy_feed(view)
        if self._pose_reset == "pending" and view.get("present") is True:
            self._pose_reset = "due"
            self._tick_evt.set()
        drv = self._posture() in DRIVING
        if not drv and not self.motion_studio.posture_allowed():
            self.motion_studio.invalidate("策略离开准备好走的状态")
        left = self._was_driving and not drv
        self._was_driving = drv
        if left and (self._modes or self._latch["head"] is not None or self._latch["body"] is not None):
            self._request_clear(None, f"策略停了（现在{POSTURE_CN.get(self._posture(), self._posture())}）")

    def _relax_block(self, now):
        r = self._robot or {}
        if r.get("estop_active"):
            return "正在卸力，等它确认完再说"
        guard = self.w.get("relax_guard", RELAX_GUARD_S)
        if now - self._relax_at < guard:
            return f"刚卸了力：{guard:g} 秒内不接受让它动的指令（防止刚卸力又被顶起来）"
        return None

    # ================= 速度上限（审查 24）、头 / 嘴幅度（三 V9） =================

    def _speed_limits(self, now=None):
        """padd 的满杆速度是命令行参数（padd main.rs:158-168），padd.service 的 ExecStart 没带参数（padd/systemd/padd.service:36）
        就是官方默认。文件在后台线程里读（_refresh_files），这里只拿缓存（审查二 一般 9）。"""
        return self._limits

    def _read_padd_texts(self):
        texts = []
        try:
            texts.append((PADD_UNIT, self.read_text(PADD_UNIT)))
        except Exception:  # noqa: BLE001 —— 没这个文件 / 没权限：用官方默认
            pass
        try:
            for name in sorted(self.list_dir(PADD_DROPIN_DIR)):
                if name.endswith(".conf"):
                    p = os.path.join(PADD_DROPIN_DIR, name)
                    texts.append((p, self.read_text(p)))
        except Exception:  # noqa: BLE001
            pass
        return texts

    def _read_inputs(self, texts):
        """头 / 嘴幅度（三 V9）+ padd 读配置的地方（--config，没写就是 robotd.toml 默认路径：fork padd/src/main.rs 的 Args）。
        先看 padd 自己写的回执 /run/padd/input-settings.json（fork input_settings.rs:63-74：它真用上了才写，里面是它正在用的数），
        写回执的那个 padd 还活着、文件路径对得上就以它为准（审查 R10）；没有再自己按 padd 的规矩读文件。"""
        inputs = parse_padd_inputs(texts)
        text, err, ack = None, None, None
        if inputs["input_settings"]:
            try:
                text = self.read_text(inputs["input_settings"])
            except FileNotFoundError:
                err = "文件不在"
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            try:
                a = json.loads(self.read_text(PADD_INPUT_ACK))
                pid = a.get("pid") if isinstance(a, dict) else None
                alive = isinstance(pid, int) and not isinstance(pid, bool) and self.exists(f"/proc/{pid}")
                if alive and a.get("path") == inputs["input_settings"]:
                    ack = a.get("settings")
            except Exception:  # noqa: BLE001 —— 没有回执（padd 没读过这个文件、电脑上）：自己读
                pass
        return input_limits(inputs, text, err, ack), inputs["config"]

    # ================= 网页摇杆 =================

    def _seq(self, args):
        seq = args.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            return None
        return seq

    def _gate(self, conn, now):
        """连续指令（摇杆的 move / head / pose、按住的键）共同的挡（在 _lk 里调）。
        返回 None = 放行；返回 dict = 悄悄丢掉（页面已经走了）；不能做就抛 Refused（原因给页面看）。"""
        if conn in self._gone:
            return {"dropped": "页面已经断开"}
        if conn in self._closed:
            return {"dropped": "这个页面不在手柄区"}
        attached, _ = self._pad_attached(now)
        if attached:
            if not self._pad_said:
                self._pad_said = True
                self.log.info("实体手柄连着，网页摇杆只跟着它动、不发指令", cat="手柄")
            raise Refused("实体手柄连着：网页摇杆现在只跟着它动、不发指令（padd 哪怕没人碰也每 0.1 秒发一次「停」，"
                          "网页的会被盖掉）。要用网页遥控，先把手柄关掉")
        self._pad_said = False
        ok, why = self._robot_ok(now)
        if ok is not True:           # 审查 14：不知道主程序在不在运动模式，也不放行
            raise Refused(f"现在不能遥控：{why or '读不到主程序的状态'}")
        block = self._relax_block(now)
        if block:
            raise Refused(block)
        return None

    def _send_intent(self, chan, method, params, conn, by, seq, mode=None):
        """发一帧连续指令（move / head / pose 通知）：过 _gate、按序号挡旧的、拿方向盘、同一通道 40 ms 内只发最新的。
        检查和发送在 _send_lk 里，robotd 收到的顺序就是这里定的顺序。mode：头 / 身体帧发之前再看一眼模式没变（三 V6）。
        返回 (结果 dict, 发出去了吗)。"""
        now = self.clock()
        motion_epoch = self.motion_studio.epoch
        with self._lk:
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧指令已丢弃"}, False
            g = self._gate(conn, now)
            if g:
                return g, False
            if mode is not None and self._mode_of(conn) != mode:
                return {"dropped": f"模式对不上：后端现在是「{MODE_CN[self._mode_of(conn)]}」", "mode": self._mode_of(conn)}, False
            q = self._seq_of(conn)
            if seq is None:
                seq = q["hi"] + 1        # 页面没给序号：按到达的先后排
            if seq <= q["stop"] or seq <= q["sent"].get(chan, -1):
                return {"dropped": "旧的指令（已经松手了）"}, False
            self._take_lease(conn, by, now)
            q["hi"] = max(q["hi"], seq)
            q["latest"][chan] = max(q["latest"].get(chan, -1), seq)
            gap = MOVE_MIN_GAP_S - (now - self._last_sent_at.get(chan, -1e9))
        if self.robot is None:
            raise Refused("这只鸭子没配主程序的地址")
        if self._clears:
            self._flush_clears()         # 方向盘刚换了人：先把原来那个页面的头 / 身体清掉
        if gap > 0:
            self._cancel_wait(gap)
        with self._send_lk:
            with self._lk:
                d, q = self._driver, self._seq_of(conn)
                if (not self.motion_studio.request_current() or motion_epoch != self.motion_studio.epoch or conn in self._gone or conn in self._closed or seq != q["latest"].get(chan) or seq <= q["stop"]
                        or d is None or d["conn"] != conn or (mode is not None and self._mode_of(conn) != mode)):
                    return {"dropped": "被更新的指令替掉了，或者已经松手"}, False
                q["sent"][chan] = seq
                self._last_sent_at[chan] = self.clock()
            try:
                self._notify_output(method, params)
            except (Unreachable, Timeout) as e:
                raise Refused(f"没发出去：{e}") from None
            with self._lk:
                if chan in ("head", "body"):
                    nonzero = any(abs(v) > 1e-9 for k, v in params.items() if k != "active")
                    self._latch[chan] = {"conn": conn, "nonzero": nonzero, "at": self.clock()}
                    self._tick_evt.set()
        return {"merged": gap > 0}, True

    def _move(self, args, conn=None, by=None, mode="drive"):
        """走路的一帧（旧的 move 通道和 sticks 的走路模式都走这里）。mode：这一帧是哪个模式下的，后端模式不是它就丢——
        查完模式到发出去之间切到了头 / 身体模式，也不会在头 / 身体模式里发出速度（审查 R6、三 V6）。"""
        try:
            vals = [float(args.get(k, 0) or 0) for k in ("vx", "vy", "vyaw")]
        except (TypeError, ValueError):
            raise Refused("速度要是数字") from None
        if not all(math.isfinite(v) for v in vals):
            raise Refused("速度不是有限的数")
        lim = self._speed_limits()
        vx = max(-lim["v"], min(lim["v"], vals[0]))
        vy = max(-lim["v"], min(lim["v"], vals[1]))
        vyaw = max(-lim["w"], min(lim["w"], vals[2]))
        with self._lk:
            start = not self._web_driving and any((vx, vy, vyaw))
        r, sent = self._send_intent("move", "robot.move", {"vx": vx, "vy": vy, "vyaw": vyaw}, conn, by, self._seq(args),
                                    mode=mode)
        if not sent:
            return r
        with self._lk:
            self._web_driving = any((vx, vy, vyaw))
            self.virtual.update(last=[vx, vy, vyaw], at=self.clock())
        if start:
            self.log.info(f"网页遥控开始开（{by or '页面'}，robot.move）", cat="手柄")
        return {"sent": [vx, vy, vyaw], "merged": r["merged"]}

    def _sticks(self, args, conn=None, by=None):
        """网页手柄的摇杆（图上的两个、下面两个大的共用这一个通道，三 V6、V20）：{mode, lx, ly, rx, ry, seq}，
        -1..1、x 向右、y 向下为正。后端按 padd 的公式换算（sticks_to_intent），模式以后端记的为准：对不上就丢、告诉页面。"""
        mode = args.get("mode", "drive")
        if mode not in MODES:
            raise Refused(f"不认识的模式：{mode}")
        try:
            vals = [float(args.get(k, 0) or 0) for k in ("lx", "ly", "rx", "ry")]
        except (TypeError, ValueError):
            raise Refused("摇杆要是数字") from None
        if not all(math.isfinite(v) for v in vals):
            raise Refused("摇杆不是有限的数")
        lx, ly, rx, ry = (max(-1.0, min(1.0, v)) for v in vals)
        now = self.clock()
        with self._lk:
            cur = self._mode_of(conn)
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧摇杆请求已丢弃", "mode": cur}
            if conn in self._gone or conn in self._closed:
                return {"dropped": "页面已经离开", "mode": cur}
            if mode != cur:
                why = (self._mode_exit.get(conn) or {}).get("why")
                return {"dropped": f"模式对不上：后端现在是「{MODE_CN[cur]}」" + (f"（{why}）" if why else ""), "mode": cur}
            if cur != "drive":
                self._modes[conn]["hb"] = now
                if self._posture() not in DRIVING:
                    self._gate(conn, now)           # 镜像、卸力这些照样先说
                    return {"dropped": MODE_IDLE, "mode": cur}
                a = self._action
                if cur == "head" and a is not None and any(C.head_at(s, 0.0) is not None for s in a["steps"]):
                    raise Refused(f"正在做「{a['name']}」（也在动头）：等它做完或者先松开")
        method, params = sticks_to_intent(cur, lx, ly, rx, ry, self._speed_limits(), self._inputs["head"])
        if cur == "drive":
            r = self._move(dict(params, seq=args.get("seq")), conn, by, mode="drive")
            return dict(r, mode=self._mode_of(conn))
        r, sent = self._send_intent(cur, method, params, conn, by, self._seq(args), mode=cur)
        if sent:
            return {"sent": params, "mode": cur, "merged": r["merged"]}
        return dict(r, mode=cur)

    def _cancel_wait(self, s):
        threading.Event().wait(s)

    def _stop_move(self, args=None, conn=None, by=None):
        """松手：走路模式发一次 move 0；头模式头回正、身体模式身体回中（还在身体模式：{0,0,0,active:true}，三 附 2）。
        不在服务端重复发：浏览器断了，robotd 的 deadman 500 ms 后自己清零（safety.rs:226-237），头 / 身体靠看门狗。
        镜像时网页没在开就不发 move 0（不去打断实体手柄）；别的情况多发一次 0 不危险，照发。"""
        if self.robot is None:
            return {}
        args = args or {}
        with self._send_lk:
            with self._lk:
                if not self.motion_studio.request_current():
                    return {"dropped": "动作会话已经改变，旧停止请求已丢弃"}
                now = self.clock()
                q = self._seq_of(conn)
                seq = self._seq(args)
                if seq is None:
                    seq = q["hi"] + 1
                q["stop"] = max(q["stop"], seq)
                q["hi"] = max(q["hi"], seq)
                d = self._driver
                mine = d is None or d["conn"] == conn
                if d is not None and d["conn"] == conn:
                    d["seen"] = now
                attached, _ = self._pad_attached(now)
                send = self._web_driving or not attached
                center = None
                mode = self._mode_of(conn)
                lat = self._latch.get(mode) if mode != "drive" else None
                if lat is not None and lat["conn"] == conn and lat["nonzero"]:
                    center = ("robot.head", HEAD_ZERO) if mode == "head" else ("robot.pose", POSE_CENTER)
                    lat.update(nonzero=False, at=now)
            if send:
                try:
                    self._notify_output("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
                except (Unreachable, Timeout) as e:
                    raise Refused(f"「停」没发出去（鸭子 0.5 秒收不到也会自己停）：{e}") from None
            if center is not None:
                try:
                    self._notify_output(center[0], dict(center[1]))
                except (Unreachable, Timeout) as e:
                    with self._lk:
                        lat.update(nonzero=True)            # 看门狗 0.6 秒后再试
                    raise Refused(f"回正没发出去（0.6 秒后再试一次）：{e}") from None
            with self._lk:
                was = self._web_driving and mine
                if mine and send:
                    self._web_driving = False
                    self.virtual.update(last=[0.0, 0.0, 0.0], at=self.clock())
        if was:
            self.log.info("网页遥控松开了", cat="手柄")
        return {"sent": send, **({"centered": True} if center is not None else {})}

    # ================= 按住的键：LT / RT / X（三 V4） =================

    def _keys_op(self, args, conn, by):
        """{held:[按着的], down:[这一下刚按下的], seq}。页面按下、松开时发，按住期间每 0.15 秒也发一次。
        只认「后端已经记着的」和「这一下刚按下的」：后端中途叫停了（卸力、被接管……），页面的心跳再报着也不会自己续上，
        要松开重按（回给页面 ended）。真正发给 robotd 的在后台线程（_keys_plan）。"""
        held, down = args.get("held"), args.get("down") or []
        if not isinstance(held, list) or not isinstance(down, list) or any(k not in HELD_KEYS for k in held + down):
            raise Refused("held / down 只能是 lt、rt、x")
        seq = self._seq(args)
        now = self.clock()
        with self._lk:
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧按键请求已丢弃", "held": []}
            if conn in self._gone or conn in self._closed:
                return {"dropped": "页面已经离开", "held": []}
            st = self._keys.get(conn)
            q = self._seq_of(conn)
            if seq is not None:
                if seq <= q["keys"]:
                    return {"dropped": "旧的", "held": sorted(st["held"]) if st is not None and not st.get("end") else []}
                q["keys"] = seq
            had = set(st["held"]) if st is not None and not st.get("end") else set()
            want = set(held)
            pressed = (set(down) & want) - had
            keep = (want & had) | pressed
            ended = sorted(want - keep)
            if pressed:
                attached, _ = self._pad_attached(now)
                if attached:
                    raise Refused(MIRROR_SVG)
                base = self._base_block(now)
                if base:
                    raise Refused(base)
                a = self._action
                if pressed & {"lt", "rt"} and a is not None and any(C.mouth_at(s, 0.0) is not None for s in a["steps"]):
                    raise Refused(f"正在做「{a['name']}」（也在动嘴）：等它做完或者先松开")
                self._take_lease(conn, by, now)
            if st is None or st.get("end"):
                if not keep:
                    return {"held": [], "ended": ended}
                st = self._keys[conn] = {"held": {}, "started": now, "by": by, "mouth": False, "rt": False,
                                         "wheee": False, "x_down": None, "end": None,
                                         **({k: st[k] for k in ("mouth", "wheee")} if st is not None else {})}
            released = set(st["held"]) - keep
            for k in released:
                st["held"].pop(k, None)
                if k == "x":
                    self._x_armed.pop(conn, None)
            for k in keep:
                st["held"][k] = now
            if "x" in pressed:
                st["x_down"] = now
                self._x_armed.pop(conn, None)     # 新的一下：上一下 X 的 arm 作废（审查 R5）
            if keep:
                self._touch_lease(conn, now)
        for k in sorted(pressed):
            self.log.info(f"网页手柄 {KEY_CN[k]} 按下（{by or '页面'}）", cat="手柄", dedup=False)
        for k in sorted(released):
            self.log.info(f"网页手柄 {KEY_CN[k]} 松开了", cat="手柄", dedup=False)
        self._tick_evt.set()
        return {"held": sorted(keep), "ended": ended}

    def _keys_abort(self, conn, st, now):
        """按住的键要不要中途叫停（在 _lk 里调）：原因照搬 _abort_reason。"""
        if conn in self._gone or conn in self._closed:
            return "页面离开了"
        if self._pad_attached(now)[0]:
            return "实体手柄连上了"
        d = self._driver
        if d is None or d["conn"] != conn:
            return "被别的页面接管了"
        if self._relax_at >= st["started"] or (self._robot or {}).get("estop_active"):
            return "卸力了"
        return self._base_block(now)

    def _keys_plan(self, now, sends, logs):
        """后台线程每拍调（在 _lk 里）：按「按着哪些键」排这一拍要发的（fork padd:1351-1396、1182-1211）。"""
        for conn, st in list(self._keys.items()):
            why = st.get("end") or self._keys_abort(conn, st, now)
            if why:
                fresh = set()
            else:
                fresh = {k for k, at in st["held"].items() if now - at < (X_HOLD_S if k == "x" else KEYS_HOLD_S)}
            lt, rt, x = "lt" in fresh, "rt" in fresh, "x" in fresh
            mouth = (lt or rt) and self._relax_block(now) is None       # 刚卸了力：只叫、不动嘴
            if mouth or st["mouth"]:
                sends.append(("robot.mouth", {"open": round(self._inputs["mouth"] / 100.0, 3) if mouth else 0.0}, None))
                st["mouth"] = mouth
            if rt and not st["rt"]:
                sends.append(("robot.sound", {"tag": "chirp"}, None))        # RT 按下那一下（padd 是过 0.3 的上升沿）
            st["rt"] = rt
            if lt:
                sends.append(("robot.sound", {"tag": "wheee", "hold": True}, None))
                st["wheee"] = True
            elif st["wheee"]:
                sends.append(("robot.sound", {"tag": "wheee", "hold": False}, None))   # 不带 hold 等于 true（intents.rs:443-452）
                st["wheee"] = False
            arm = self._x_armed.get(conn)
            if x and arm is not None and arm["x_down"] == st.get("x_down") \
                    and self._posture() in DRIVING and self._relax_block(now) is None:
                sends.append(("robot.do", {"skill": arm["skill"]}, None))   # 连滚：robotd 只刷新 0.15 秒的窗口（control.rs:429-434）
            gone = set(st["held"]) - fresh
            if gone:
                reason = why or "页面 0.45 秒没说还按着（松手、切页或断网）"
                logs.append(("安全" if why in (None, "卸力了") else "手柄",
                             f"网页手柄 {'、'.join(KEY_CN[k] for k in sorted(gone))} 停了：{reason}"))
                for k in gone:
                    st["held"].pop(k, None)
                if "x" in gone:
                    self._x_armed.pop(conn, None)
            if why:
                st["end"] = why
            if not st["held"] and not st["mouth"] and not st["wheee"]:
                del self._keys[conn]

    def _key_press(self, args, conn, by):
        """网页手柄图上的一次性键：A / X / LB / RB / 十字下（三 V14、V16）。按 pad.bindings 做（读不到用官方默认）：
        绑的是 sit_toggle → 按坐没坐走「站起来 / 坐下」；空 = 关掉了；有 error = 这只鸭子没有。X 按下这一下的请求被接受了，
        按住才连滚（_keys_plan 每拍发通知，方案 C）。"""
        key = args.get("key")
        if key not in BIND_KEYS:
            raise Refused(f"不认识的键：{key}")
        now = self.clock()
        if args.get("src") == "svg" and self._pad_attached(now)[0]:
            raise Refused(MIRROR_SVG)
        if self.robot is not None and now - self._bindings_at > BINDINGS_STALE_S:
            self._fetch_bindings()
        with self._lk:
            mv = self._motion_view(self.clock())
        row = mv["keys"][key]
        name = KEY_CN[key]
        if not row["ok"]:
            self.log.info(f"网页手柄 {name}：不能按（{row['why']}）", cat="手柄")
            raise Refused(row["why"])
        self.log.info(f"网页手柄 {name} → {row['does']}（{by or '页面'}）", cat="手柄", dedup=False)
        label = f"网页手柄 {name} → {row['does']}"
        if row["op"] == "skill":
            with self._lk:
                st = self._keys.get(conn)
                x_down0 = st.get("x_down") if st is not None else None    # 这一下 X 是哪一下（keys 通道可能还没到：None）
            r = self._motion_cmd("skill", {"name": row["skill"]}, conn, by, label=label)
            if key == "x":
                # arm 绑在发请求的那一下 X 上（审查 R5）：_keys_plan 只在「现在按着的这一下」的 x_down 跟它一样时才连滚。
                # 快速点一下、松手以后请求才回话：记下的是上一下的 x_down，下一下 X 新按下时 _keys_op 先扔掉它，也对不上，
                # 下一下的请求被拒了也不会照着它连滚。keys 通道比请求晚到（x_down0 是 None）：X 还按着就用现在这一下
                with self._lk:
                    st = self._keys.get(conn)
                    if x_down0 is None and st is not None and not st.get("end") and "x" in st["held"]:
                        x_down0 = st.get("x_down")
                    if x_down0 is not None:
                        self._x_armed[conn] = {"skill": row["skill"], "x_down": x_down0}
                self._tick_evt.set()
            return r
        return self._motion_cmd(row["op"], {}, conn, by, label=label)

    def _start(self, args, conn, by):
        """网页手柄图上的 Start（三 V7、V8）：按现在的姿势挑「停下（站好）/ 开始走 / 站起来」，跟页面看到的（expect）不一样就拒。"""
        now = self.clock()
        if args.get("src") == "svg" and self._pad_attached(now)[0]:
            raise Refused(MIRROR_SVG)
        with self._lk:
            st = self._motion_view(now)["start"]
        expect = args.get("expect")
        if expect is not None and expect != st["op"]:
            self.log.info(f"网页手柄 Start：页面以为是「{expect}」，后端判的是「{st['label']}」，没发", cat="手柄")
            raise Refused(f"状态刚变了，再按一次（现在按 Start 会「{st['label']}」）")
        if not st["ok"]:
            self.log.info(f"网页手柄 Start：不能按（{st['why']}）", cat="手柄")
            raise Refused(st["why"])
        self.log.info(f"网页手柄 Start → {st['label']}（{by or '页面'}）", cat="手柄", dedup=False)
        return self._motion_cmd(st["op"], {}, conn, by, label=f"网页手柄 Start → {st['label']}")

    def _reboot(self, args, conn, by):
        """网页手柄图上的十字右：重启全部舵机（照官方：按一下就发、不用确认）。不看方向盘、不占「上一个还没回话」：
        跟卸力一样是救急的路。主程序先全身卸力再重启（fork handover 版 main.rs:2731-2758），收下以后这边记一次卸力。"""
        now = self.clock()
        if args.get("src") == "svg" and self._pad_attached(now)[0]:
            raise Refused(MIRROR_SVG)
        with self._lk:
            mv = self._motion_view(now)
        row = mv["reboot"]
        what = "网页手柄 十字右 → 重启全部舵机"
        if not row["ok"]:
            self.log.info(f"网页手柄 十字右：不能按（{row['why']}）", cat="手柄")
            with self._lk:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": row["why"], "at": now}
            raise Refused(row["why"])
        self.log.info(f"{what}（{by or '页面'}）", cat="手柄", dedup=False)
        try:
            r = self._call(what, "robot.rebootMotors", {"ids": []}, by, {"posture": mv["posture"]})
        except Refused as e:
            with self._lk:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": str(e), "at": self.clock()}
            raise
        self.note_relax(by, "重启舵机（主程序先全身卸力）")
        note = r.get("reason") if isinstance(r, dict) else None
        with self._lk:
            self._last_cmd = {"what": what, "by": by, "ok": True, "reason": note, "at": self.clock()}
        return {"accepted": True, "reason": note, "did": "reboot"}

    def _drive_mode(self, args, conn, by):
        """网页手柄图上十字上按住 3 秒：走路 ⇄ 轮滑（照官方 padd:766-806）。3 秒在页面上算，到了发 {expect: 要切到的}；
        跟后端判的不一样就拒（状态刚变了）。主程序收下会叫一声（走路）/ 两声（轮滑）、回站姿、换上那个模式的策略
        （fork handover 版 main.rs:2870-2930）；没装轮滑模型它会拒。"""
        now = self.clock()
        if args.get("src") == "svg" and self._pad_attached(now)[0]:
            raise Refused(MIRROR_SVG)
        with self._lk:
            mv = self._motion_view(now)
            row = mv["drive_mode"]
            target = row["target"]
            what = f"网页手柄 十字上（按住 3 秒）→ 切到{DRIVE_MODE_CN.get(target, '另一个模式')}"
            why = row["why"]
            expect = args.get("expect")
            if why is None and expect is not None and expect != target:
                why = f"状态刚变了，再按住一次（现在会{row['does'].replace('按住 3 秒：', '')}）"
            if why is None and self._cmd_busy:
                why = "上一个指令还没回话，等一下"
            if why is None:
                held = self._lease_holder(now)
                if held is not None and held["conn"] != conn:
                    why = f"{held['by'] or '另一个页面'}正在推摇杆：等它松手，或者点「接管」"
            if why is not None:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": why, "at": now}
            else:
                self._cmd_busy = True
        if why is not None:
            self.log.info(f"网页手柄 十字上：不能切（{why}）", cat="手柄")
            raise Refused(why)
        self.log.info(f"{what}（{by or '页面'}）", cat="手柄", dedup=False)
        try:
            r = self._call(what, "robot.setMode", {"mode": target}, by, {"posture": mv["posture"], "mode": row["current"]})
        except Refused as e:
            with self._lk:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": str(e), "at": self.clock()}
            raise
        finally:
            with self._lk:
                self._cmd_busy = False
        note = r.get("reason") if isinstance(r, dict) else None
        if note and "already" in note.lower():
            note = f"已经是{DRIVE_MODE_CN[target]}了"
        with self._lk:
            self._last_cmd = {"what": what, "by": by, "ok": True, "reason": note, "at": self.clock()}
        return {"accepted": True, "reason": note, "did": "drive_mode", "target": target}

    # ================= 后台线程：按住的键、看门狗、清理（25 Hz，没事做就睡） =================

    def _tick_work(self):
        return bool(self.motion_studio.session or self.motion_studio._pending_cleanup or self._keys or self._clears or self._modes or self._pose_reset == "due"
                    or (self._latch["head"] or {}).get("nonzero") or (self._latch["body"] or {}).get("nonzero"))

    def _tick_loop(self):
        while not self._tick_stop.is_set():
            try:
                self._tick_once()
            except Exception as e:  # noqa: BLE001 —— 这个线程不能悄悄死掉：按住的键、看门狗都靠它
                key = f"{type(e).__name__}: {e}"[:200]
                if key not in self._tick_said:
                    self._tick_said.add(key)
                    self.log.error("手柄页的后台线程出错（按住的键、看门狗）", cat="手柄", raw=key, exc=True,
                                   hint="这是 Studio 的问题，导出诊断包发出来；线程会接着跑")
            with self._lk:
                busy = self._tick_work()
            if busy:
                self._tick_stop.wait(TICK_S)
            else:
                self._tick_evt.wait(1.0)
                self._tick_evt.clear()

    def _tick_once(self):
        if self._update_blocked():
            with self._lk:
                held=bool(self._modes or self._keys or self._latch['head'] or self._latch['body'])
            if held:self._request_clear(None,'软件 / 主程序更新保护仍在',say=False)
        self.motion_studio.tick()
        self._flush_clears()                       # 它自己拿 _send_lk：放在下面那把锁之前（Lock 不能重入）
        sends, logs = [], []                       # sends：(方法, 参数, 标记 None / "pose_reset" / "watchdog")
        # 决定和发送在同一把 _send_lk 里（审查 R2，跟 _flush_clears、_stop_move 一个写法）：看门狗决定「回中（active:true）」
        # 以后、发出去之前，别的线程不能插进来先发退出（active:false），再被这个回中重新打开——那样 _latch 已经清了，再也没人清
        with self._send_lk:
            with self._lk:
                now = self.clock()
                if self._pose_reset == "due":
                    self._pose_reset = "sending"
                    sends.append(("robot.pose", dict(POSE_OFF), "pose_reset"))
                if self._pad_attached(now)[0] and (self._modes or self._keys or self._latch["head"] or self._latch["body"]):
                    self._request_clear(None, "实体手柄连上了")               # 三 V1 第 6 条（configd 那条路也算）
                for kind, center in (("head", HEAD_ZERO), ("body", POSE_CENTER)):
                    lat = self._latch[kind]
                    if lat is not None and lat["nonzero"] and now - lat["at"] > STICK_STALE_S:
                        lat.update(nonzero=False, at=now)
                        sends.append(("robot.head" if kind == "head" else "robot.pose", dict(center), "watchdog"))
                        logs.append(("安全", f"{'头' if kind == 'head' else '身体'}的摇杆 {STICK_STALE_S:g} 秒没有新的一帧（页面卡住了？）："
                                           f"{'头回正' if kind == 'head' else '身体回中'}"))
                for c, m in list(self._modes.items()):
                    if now - m["hb"] > MODE_HB_S:
                        why = f"页面 {MODE_HB_S:g} 秒没报还在{MODE_CN[m['mode']]}"
                        logs.append(("安全", f"{why}：退出"))
                        self._request_clear(c, why, say=False)          # 同一件事只记「安全」这一行（V19）
                self._keys_plan(now, sends, logs)
            for method, params, tag in (sends if self.robot is not None else []):
                try:
                    self._notify_output(method, params)
                except (Unreachable, Timeout) as e:
                    if tag == "pose_reset":
                        with self._lk:
                            self._pose_reset_tries += 1
                            self._pose_reset = "pending" if self._pose_reset_tries < 3 else "failed"
                    if method not in self._tick_said:
                        self._tick_said.add(method)
                        self.log.warn(f"网页手柄的 {method} 没发出去", cat="手柄", step=method, raw=str(e),
                                      hint="主程序 robotd 不在？嘴、头没收到也不危险：robotd 自己不动")
                    continue
                self._tick_said.discard(method)
                if tag == "pose_reset":
                    with self._lk:
                        self._pose_reset = "done"
                    self.log.info("Studio 连上主程序：发了一次 robot.pose {\"active\": false}（清掉上一次可能留下的身体模式）",
                                  cat="手柄")
        for cat, msg in logs:
            self.log.write(msg, cat=cat, level="info", dedup=False)

    def _drive_close(self):
        """Studio 关闭（pad.close 里调，在线程里）：先停后台线程、等它停下，再尽力把头 / 身体清掉、按住的键松开（三 V1 第 9 条）。"""
        self._tick_stop.set()
        self._tick_evt.set()
        t = self._tick_thread
        if t is not None and t is not threading.current_thread():
            t.join(0.5)       # 等它这一拍发完：不然它排好的「张嘴」可能落在下面的「合嘴」后面，嘴一直张着（审查 R8）
        self.motion_studio.close()
        try:
            self._request_clear(None, "Studio 关了")
            self._flush_clears(force=True)
            sends = []
            with self._lk:
                for st in self._keys.values():
                    if st["mouth"]:
                        sends.append(("robot.mouth", {"open": 0.0}))
                    if st["wheee"]:
                        sends.append(("robot.sound", {"tag": "wheee", "hold": False}))
                self._keys.clear()
            if self.robot is not None:
                with self._send_lk:
                    for method, params in sends:
                        try:
                            self._notify_output(method, params)
                        except (Unreachable, Timeout):
                            pass
        except Exception:  # noqa: BLE001 —— 关的路上尽力而为
            pass

    # ================= 站起来 / 开始走 / 坐下 / 技能 / 声音：能不能按 =================

    def _update_blocked(self):
        try:return getattr(self,'update_guard',lambda:False)() is True
        except Exception:return True

    def _base_block(self, now, *, ignore_mapping=False):
        """跟姿势无关、什么动作都不能做的情况（声音也挡：调试模式下主程序只读，fork main.rs:5379-5396）。"""
        if self._update_blocked():return '软件 / 主程序更新保护仍在'
        mapping = getattr(self, 'mapping', None)
        if not ignore_mapping and mapping is not None and mapping.blocked:
            return '手柄映射／回中确认中，动作输出已暂停，网页卸力仍可使用'
        r = self._robot or {}
        if self._robot_at is None or now - self._robot_at > 3:
            return "Studio 还没收到主程序的状态"
        if r.get("paused"):
            return "正在调舵机：主程序停着（舵机页的会话还开着）。点页面顶上的「交还主程序」才交还"
        if r.get("present") is False:
            return "主程序 robotd 没在跑"
        bus = r.get("bus") if isinstance(r.get("bus"), dict) else None
        if bus is None or r.get("bus_at") is None or now - r["bus_at"] > 3:
            return "读不到主程序的状态"
        if bus.get("phase") in STARTING_PHASES:
            return "主程序还在启动"
        if bus.get("mode") == "commissioning":
            return "调试模式：主程序这时只读，动作都不接受"
        if bus.get("mode") != "motion":
            return f"主程序在 {bus.get('mode')} 模式"
        return None

    def _policies(self):
        p = (self._robot or {}).get("policies")
        return p if isinstance(p, dict) else None

    def _known_skills(self):
        """robot.do 认的名字：ground_pick（装了这个槽）+ sit_toggle（装了 sitstand）+ skills（robotd main.rs:4136-4156）。
        读不到 robot.policies 返回 None。"""
        p = self._policies()
        if p is None:
            return None
        slots = {s.get("slot"): s for s in p.get("slots") or [] if isinstance(s, dict)}
        names = []
        if (slots.get("ground_pick") or {}).get("path"):
            names.append("ground_pick")
        if (slots.get("sitstand") or {}).get("path"):
            names.append("sit_toggle")
        names += [s for s in p.get("skills") or [] if isinstance(s, str)]
        return names

    def _posture(self):
        r = self._robot or {}
        return "servo_session" if r.get("paused") else (r.get("posture") or "unknown")

    def _skill_why(self, name, need_drive, known, pol):
        """一个技能现在能不能做（技能按钮、组合动作里的技能、组合动作跑到那一步时都用这一个：审查二 一般 6）。"""
        if known is None:
            return "读不到这只鸭子有哪些技能（robot.policies 只在运动模式下问）"
        if name not in known:
            return f"这只鸭子没有「{C.SKILL_CN.get(name, name)}」"
        if need_drive:
            return need_drive
        if name == "roller_stand" and pol.get("mode") != "roller":
            return "装了滑轮、切到轮滑模式才用（手柄十字上按住 3 秒切换）"
        return None

    def _bind_map(self):
        """五个能绑的键现在各绑了什么（三 V14）：{键: {"skill", "error", "overridden", "src"}}。pad.bindings 读不到、
        或者列表里没有这个键，用官方默认（robotd-params lib.rs:175-215）。"""
        rows = {b.get("button"): b for b in self.bindings or [] if isinstance(b, dict)}
        out = {}
        for k in BIND_KEYS:
            b = rows.get(k)
            if b is not None and isinstance(b.get("skill"), str):
                out[k] = {"skill": b["skill"], "error": b.get("error") or None, "overridden": bool(b.get("overridden")),
                          "src": "pad.bindings"}
            else:
                out[k] = {"skill": DEFAULT_BIND[k], "error": None, "overridden": False,
                          "src": "官方默认" + ("（读不到 pad.bindings）" if self.bindings is None else "")}
        return out

    def _key_rows(self, buttons, sitting, skills, skills_why, known, need_drive, pol):
        """网页手柄图上 A / X / LB / RB / 十字下 各做什么、现在能不能按（推给页面画图，按的时候 _key_press 用同一份）。"""
        out = {}
        for k, b in self._bind_map().items():
            skill = b["skill"]
            row = {"key": k, "skill": skill, "src": b["src"], "overridden": b["overridden"], "op": None,
                   "does": C.SKILL_CN.get(skill, skill) if skill else "（关掉了）"}
            if b["error"]:
                row.update(ok=False, why=f"绑的「{skill}」这只鸭子用不了：{b['error']}")
            elif not skill:
                row.update(ok=False, why="这个键被关掉了（robotd.toml 的 [pad] 里设成了空）")
            elif skill == "sit_toggle":
                # 不直接发 robot.do sit_toggle：按坐没坐走「站起来 / 坐下」（审查二 S2，三 V14）
                if sitting is None:
                    row.update(ok=False, why=NO_SIT_INFO, op="sit")
                else:
                    btn = buttons["stand" if sitting else "sit"]
                    row.update(ok=btn["ok"], why=btn["why"], op=btn["op"], does=btn["label"])
            else:
                s = next((x for x in skills if x["name"] == skill), None)
                why = s["why"] if s is not None else (skills_why or self._skill_why(skill, need_drive, known, pol))
                row.update(ok=why is None, why=why, op="skill")
            out[k] = row
        return out

    def _motion_view(self, now):  # noqa: C901
        """推给页面：每个按钮能不能按、为什么不能。服务端按的时候也用这一份再判一遍。"""
        base = self._base_block(now)
        relax = self._relax_block(now)
        block = base or relax
        pose = self._posture()
        pol = self._policies() or {}
        sit_raw = pol.get("sitting")
        sitting = sit_raw if isinstance(sit_raw, bool) else None      # None = 读不到（审查二 S2：不靠猜）
        homed = pol.get("homed") if isinstance(pol.get("homed"), bool) else None
        r = self._robot or {}
        bus = r.get("bus") if isinstance(r.get("bus"), dict) else {}
        policy_on = bus.get("policy_enabled") is True
        torque_on = (bus.get("native") or {}).get("torque_state_confirmed") is True
        batt = r.get("battery_pct")
        job = self._job_alive()
        # 还没站稳（state.posture 判 homing）：主程序的 home_start_guard 说了原因就带上（比如「机身未直立」），不然人不知道在等什么
        guard = bus.get("home_start_guard") if isinstance(bus.get("home_start_guard"), dict) else {}
        unsteady = "还在站起来，站稳了再点" + (f"（{guard['reason']}）" if guard.get("ready") is False
                                             and isinstance(guard.get("reason"), str) and guard["reason"] else "")

        def b(op, label, call, why):
            return {"op": op, "label": label, "call": call, "ok": why is None, "why": why}

        # 站起来：坐着要 sit_toggle（还要策略在跑、到了 HOME），没坐着才 init；坐没坐读不到就不让点
        stuck = None
        if block:
            stand = b("stand", "站起来", None, block)
        elif sitting is None:
            stand = b("stand", "站起来", None, NO_SIT_INFO)
        elif sitting:
            if policy_on and torque_on and (homed is True or bus.get("homed") is True):
                stand = b("stand", "站起来", "robot.do sit_toggle",
                          None if "sit_toggle" in (self._known_skills() or []) else "这只鸭子没装坐下 / 站起的模型")
            else:
                stuck = ("坐着、但策略没在跑（多半是坐着时卸了力）：网页现在站不起来——发 init 会跟坐下的锁存打架，"
                         "发 sit_toggle 主程序又要策略在跑才收。先把它扶起来放平再点「站起来」，或者用手柄 Start")
                stand = b("stand", "站起来", None, stuck)
        elif pose == "limp":
            # 电量低不拦（用户 10-18：电池只警告）：只在按钮下面提示一句，照样能点。
            # 以前那句「站起来很快又会自己坐下关机」在我们的鸭子上不对：按 robotd 配置的 [safety] battery_empty_shutdown 说（任务 1 附）
            stand = b("stand", "站起来", "robot.init", None)
            if isinstance(batt, (int, float)) and not isinstance(batt, bool) and batt < 15:
                if self.battery_shutdown is True:
                    stand["note"] = f"电量只剩 {batt:.0f}%，先充电再玩；没电它到 6.6 V 会自己坐下关机"
                else:
                    stand["note"] = f"电量只剩 {batt:.0f}%，先充电再玩；没电它不会自己坐下"
        elif pose == "homing":
            stand = b("stand", "站起来", None, unsteady)
        elif pose in ("holding", "policy", "walking"):
            stand = b("stand", "站起来", None, "已经站着了")
        else:
            stand = b("stand", "站起来", None, f"读不出它现在的姿势（{POSTURE_CN.get(pose, pose)}）")
        # 开始走 / 停下（停下谁都能点、卸力后也能点）
        if pose in DRIVING and not base:
            walk = b("walk_stop", "停下（站好）", "robot.stop → robot.enable {on:false}", None)
        elif block:
            walk = b("walk", "开始走", None, block)
        elif job:
            walk = b("walk", "开始走", None, "正在搜索 / 配对手柄（会占蓝牙）：等它做完再开始走")
        elif stuck:
            walk = b("walk", "开始走", None, "坐着又没在跑策略：看「站起来」那里的说明")
        elif sitting is None:
            walk = b("walk", "开始走", None, NO_SIT_INFO)
        elif sitting:
            walk = b("walk", "开始走", None, "坐着：先点「站起来」")
        elif pose == "holding":
            walk = b("walk", "开始走", "robot.enable {on:true}", None)
        elif pose == "limp":
            walk = b("walk", "开始走", None, "软着：先点「站起来」")
        elif pose == "homing":
            walk = b("walk", "开始走", None, unsteady)
        else:
            walk = b("walk", "开始走", None, f"读不出它现在的姿势（{POSTURE_CN.get(pose, pose)}）")
        # 坐下：没坐着（读得到）、策略在跑、到了 HOME、没在走
        known = self._known_skills()
        if block:
            sit = b("sit", "坐下", None, block)
        elif sitting is None:
            sit = b("sit", "坐下", None, NO_SIT_INFO)
        elif sitting:
            sit = b("sit", "坐下", None, "已经坐着了")
        elif known is not None and "sit_toggle" not in known:
            sit = b("sit", "坐下", None, "这只鸭子没装坐下的模型（sitstand）")
        elif pose == "policy" and policy_on:
            sit = b("sit", "坐下", "robot.do sit_toggle", None if homed is not False else "它还在回站姿，等一秒")
        elif pose == "walking":
            sit = b("sit", "坐下", None, "在走：先松开摇杆，等它停下")
        elif pose == "holding":
            sit = b("sit", "坐下", None, "坐下要策略在跑（官方规定）：先点「开始走」，再点坐下")
        elif pose == "limp":
            sit = b("sit", "坐下", None, "软着：先点「站起来」")
        else:
            sit = b("sit", "坐下", None, f"现在{POSTURE_CN.get(pose, pose)}，坐不了")
        # 网页手柄的 Start：在走 / 准备好走 → 停下；站着保持 → 开始走；别的 → 站起来（三 V7，附 1 第一行）
        if walk["op"] == "walk_stop":
            pick = walk
        elif pose == "holding":
            pick = walk
        else:
            pick = stand
        start = {"op": pick["op"], "label": pick["label"], "ok": pick["ok"], "why": pick["why"], "call": pick["call"]}
        # 十字右：重启全部舵机。官方按一下就发、robotd 从不拒（过载保护跳了的鸭子正要它），所以卸力之后也能按
        reboot = {"ok": base is None, "why": base, "does": "重启全部舵机：鸭子先软下来（扶住），之后按 Start 站回来"}
        # 十字上按住 3 秒：走路 ⇄ 轮滑。现在是哪个看 robot.policies 的 mode（官方 padd 是开机问一次 robot.mode）
        cur = pol.get("mode") if pol.get("mode") in DRIVE_MODE_CN else None
        target = {"walk": "roller", "roller": "walk"}.get(cur)
        mode_why = block or (None if target else "读不到现在是走路还是轮滑（robot.policies 读不到）")
        drive_mode = {"ok": mode_why is None, "why": mode_why, "current": cur, "target": target,
                      "does": f"按住 3 秒：切到{DRIVE_MODE_CN[target]}" if target else "按住 3 秒：走路 ⇄ 轮滑"}
        # 技能
        need_drive = None
        if block:
            need_drive = block
        elif pose not in DRIVING:
            need_drive = "要「准备好走」（策略在跑）才能做：先点「开始走」" if pose == "holding" else \
                f"现在{POSTURE_CN.get(pose, pose)}：先站起来、点「开始走」"
        elif sitting is None:
            need_drive = NO_SIT_INFO
        elif sitting:
            need_drive = "坐着：先站起来"
        elif homed is False:
            need_drive = "它还在回站姿，等一秒"
        skills = []
        for name in [n for n in (known or []) if n != "sit_toggle"]:
            why = self._skill_why(name, need_drive, known, pol)
            skills.append({"name": name, "label": C.SKILL_CN.get(name, name), "ok": why is None, "why": why})
        skills_why = None if known is not None else "读不到这只鸭子有哪些技能（robot.policies 只在运动模式下问）"
        # 声音（叫一声不动关节，卸力后也能叫：main.rs:4300-4307）
        sounds = [{"tag": t, "label": lab, "ok": base is None, "why": base} for t, lab in C.SOUNDS.items()]
        # 动作、组合动作
        attached, _ = self._pad_attached(now)
        held_mouth = any({"lt", "rt"} & set(st["held"]) for st in self._keys.values() if not st.get("end"))
        head_mode = any(m["mode"] == "head" for m in self._modes.values()) or bool((self._latch["head"] or {}).get("nonzero"))
        limp = pose == "limp" or relaxed(bus) is True

        def combo_row(c, builtin):
            why = None
            if C.needs_policy(c["steps"]):
                why = need_drive
            if why is None and attached:
                why = "实体手柄连着：它每一拍都发一次嘴和速度，会盖掉动作。先把手柄关掉"
            if why is None:
                why = base or relax
            if why is None:
                for s in c["steps"]:
                    if s["do"] == "skill":
                        why = self._skill_why(s["name"], need_drive, known, pol)
                        if why:
                            break
            if why is None and held_mouth and any(C.mouth_at(s, 0.0) is not None for s in c["steps"]):
                why = "正在按着 LT / RT 张嘴：先松开"
            if why is None and head_mode and any(C.head_at(s, 0.0) is not None for s in c["steps"]):
                why = "头模式开着、摇杆在管头：先按 Y 回到走路"
            text = C.describe(c["steps"])
            if why is None and limp and not C.needs_policy(c["steps"]):
                text += "（软着：嘴不会动，站好了才动）"
            return {"id": c["id"], "name": c["name"], "builtin": builtin, "text": text,
                    "seconds": round(C.duration(c["steps"]), 1), "ok": why is None, "why": why}

        actions = [combo_row(c, True) for c in C.ACTIONS]
        combos = [combo_row(c, True) for c in C.BUILTIN_COMBOS] + [combo_row(c, False) for c in self._user_combos]
        a = self._action
        running = None if a is None else {"id": a["id"], "name": a["name"], "by": a["by"], "step": a["step"],
                                          "steps": len(a["steps"]), "elapsed_s": round(now - a["started"], 1)}
        # LT / RT：嘴按 fork 的规矩（三 V10）——只要主程序在运动模式就能按；软着、刚卸力时只有声音
        trig_why = base
        if trig_why is None and a is not None and any(C.mouth_at(s, 0.0) is not None for s in a["steps"]):
            trig_why = f"正在做「{a['name']}」（也在动嘴）：等它做完"
        triggers = {"ok": trig_why is None, "why": trig_why, "mouth_pct": self._inputs["mouth"],
                    "note": "软着：只有声音，嘴不会动（站好了才动嘴）" if trig_why is None and (limp or relax) else None}
        last = None
        if self._last_cmd is not None and now - self._last_cmd["at"] < 15:
            last = {k: v for k, v in self._last_cmd.items() if k != "at"}
            last["age_s"] = round(now - self._last_cmd["at"], 1)
        last_action = None
        if self._last_action is not None and now - self._last_action["at"] < 15:
            last_action = {k: v for k, v in self._last_action.items() if k != "at"}
        buttons = {"stand": stand, "walk": walk, "sit": sit}
        return {"posture": pose, "posture_cn": POSTURE_CN.get(pose, pose), "block": block, "sitting": sitting,
                "buttons": buttons, "start": start, "reboot": reboot, "drive_mode": drive_mode,
                "keys": self._key_rows(buttons, sitting, skills, skills_why, known, need_drive, pol),
                "triggers": triggers, "driving": pose in DRIVING,
                "skills": skills, "skills_why": skills_why,
                "sounds": sounds, "actions": actions, "combos": combos, "running": running, "last": last,
                "last_action": last_action, "busy": self._cmd_busy}

    # ================= 站起来 / 开始走 / 坐下 / 技能 / 声音：按 =================

    def _call(self, what, method, params, by, ctx=None, posture_target=None, ordered=False, verify=None):
        if posture_target is None:
            return self._call_rpc(what, method, params, by, ctx, verify=verify)
        motion = self.motion_studio
        with (nullcontext() if ordered else self._send_lk):
            with self._lk:
                if not motion.request_current():
                    raise Refused("动作会话已经改变，旧命令请求已丢弃")
                recording = motion.session and motion.state == "recording"
                # Old robotd/manual fixtures without state evidence retain their
                # original control path. Automation always requires complete data.
                legacy = (not recording and not ordered and not motion.posture.pending
                          and "state" not in (self._robot or {}))
                if recording:
                    motion._boundary(record=True)
                    motion._refresh_request()
            if legacy:
                return self._call_rpc(what, method, params, by, ctx)
            if recording:
                motion._flush_outputs()
            with self._lk:
                if recording and (not motion.session or motion.session.get("invalid")):
                    raise Refused("动作会话已经结束，坐站请求已取消")
                token = motion.begin_posture(posture_target, manual=not recording and not ordered)
                origin = motion.recording_origin()
            if not token["command"]:
                with self._lk:
                    motion.posture.accepted(token, self.clock())
                return {"accepted": True, "reason": "已经达到目标姿态"}
            return self._call_rpc(what, method, params, by, ctx, posture_token=token, record_origin=origin)

    def _call_rpc(self, what, method, params, by, ctx=None, posture_token=None, record_origin=None,
                  verify=None):
        """一个要回话的请求：记「发出 → 回执 · 耗时」（log_cmd，分类「命令」），拒绝的原因翻成人话抛 Refused。

        verify：回执之后再查一次真实状态；返回原因就按失败记日志并抛 Refused。用给「开始走」这种
        回执 accepted 但主程序可能没真的启动命令（见 _verify_policy_started），别再写「成功」骗人。
        """
        if self.robot is None:
            raise Refused("这只鸭子没配主程序的地址")
        if self._update_blocked() and not neutral_robot_command(method,params):
            raise Refused('软件 / 主程序更新保护仍在，旧动作请求已取消')
        with self._lk:
            if not self.motion_studio.request_current():
                raise Refused("动作会话已经改变，旧命令请求已丢弃")
        if method == "robot.sound":
            record_origin = self.motion_studio.recording_origin()
        skill_fence = self.motion_studio.note_skill() if method == "robot.do" and posture_token is None else None
        with log_cmd(self.log, what, method, who=by, args=params or None, ctx=ctx) as t:
            try:
                r = self.robot.call(method, params, CMD_TIMEOUT_S)
            except Unreachable as e:
                if posture_token:
                    self.motion_studio.posture_reply(posture_token, None, reason="连接中断")
                t.fail(f"没发出去：{e}", hint="主程序 robotd 不在？看总览页")
                raise Refused(f"没发出去：{e}") from None
            except Timeout:
                if posture_token:
                    self.motion_studio.posture_reply(posture_token, None, reason="回执超时")
                t.fail(f"主程序 {CMD_TIMEOUT_S:g} 秒没回话", hint="它可能卡住了：看日志里 robotd 那几行，必要时卸力")
                raise Refused(f"主程序 {CMD_TIMEOUT_S:g} 秒没回话：它可能卡住了") from None
            except RpcError as e:
                if posture_token:
                    self.motion_studio.posture_reply(posture_token, False)
                why = plain_refusal(e.message)
                t.fail(f"主程序报错：{e.message}", raw={"code": e.code, "message": e.message}, hint=why)
                raise Refused(why) from None
            if isinstance(r, dict) and r.get("accepted") is False:
                if posture_token:
                    self.motion_studio.posture_reply(posture_token, False)
                self.motion_studio.rejected_skill(skill_fence)
                why = plain_refusal(r.get("reason"))
                t.fail(f"主程序没接受：{r.get('reason')}", raw=r, hint=why)
                raise Refused(why)
            if verify is not None:
                why = verify()
                if why:
                    t.fail(f"主程序收下了，但没真的执行：{why}", raw=r, hint=why)
                    raise Refused(why)
            if posture_token:
                if not isinstance(r, dict) or r.get("accepted") is not True:
                    self.motion_studio.posture_reply(posture_token, None, reason="回执格式不明确")
                    raise Refused(self.motion_studio.posture.error)
                self.motion_studio.posture_reply(posture_token, True, origin=record_origin)
            t.ok(r, note=(r.get("reason") or "") if isinstance(r, dict) else "")
        if method == "robot.sound":
            self.motion_studio.accepted_from(record_origin, method, params)
        return r

    @staticmethod
    def _policy_refusal(bus):
        """robotd 没把策略开起来时给的原因：busStatus 的 last_enable_refusal / home_start_guard.reason。"""
        if not isinstance(bus, dict):
            return None
        why = bus.get("last_enable_refusal")
        if isinstance(why, str) and why:
            return why
        guard = bus.get("home_start_guard")
        if isinstance(guard, dict):
            why = guard.get("reason")
            if isinstance(why, str) and why:
                return why
        return None

    def _verify_policy_started(self):
        """robot.enable 只是收下请求（fork robotd："the policy starts when it is ready"），真正的启动在
        控制循环里做，站姿 / IMU 没稳定时会被 home_start_guard 拒（不能进入保持：…）。所以回读
        busStatus，看到 policy_enabled 才当成功；等不到就把主程序给的原话回给页面。

        按次数等，不看注入的时钟：时钟停了（测试、或者别的毛病）也不会卡死在这里。
        """
        step = max(0.01, float(self.w.get("walk_verify_poll", WALK_VERIFY_POLL_S)))
        span = max(step, float(self.w.get("walk_verify", WALK_VERIFY_S)))
        bus = None
        for _ in range(int(span / step) + 1):
            try:
                r = self.robot.call("robot.busStatus", {}, CMD_TIMEOUT_S)
            except (Unreachable, Timeout):
                r = None
            if isinstance(r, dict):
                bus = r
                if r.get("policy_enabled") is True:
                    return None
            self._cancel_wait(step)
        return self._policy_refusal(bus) or "主程序没把走路策略开起来（可能还在回中或者没扶正）"

    def _motion_cmd(self, op, args, conn, by, label=None):  # noqa: C901
        """一次性的命令：不拿方向盘（审查二 I1）。别人正在推摇杆时不让点（停下、叫一声除外）；一次只发一个
        （停下、叫一声除外：声音不动关节，不占「上一个还没回话」，三 V15）。label：网页手柄图上按的，日志里写「网页手柄 A → …」。"""
        now = self.clock()
        what = {"stand": "站起来", "walk": "开始走", "walk_stop": "停下（站好）", "sit": "坐下"}.get(op)
        urgent = op == "walk_stop"
        exclusive = op not in ("walk_stop", "sound")
        with self._lk:
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧命令请求已丢弃"}
            if self._cmd_busy and exclusive:
                raise Refused("上一个指令还没回话，等一下")
            mv = self._motion_view(now)
            if op == "skill":
                name = args.get("name")
                row = next((s for s in mv["skills"] if s["name"] == name), None)
                if row is None:
                    raise Refused(mv["skills_why"] or f"这只鸭子没有「{C.SKILL_CN.get(name, name)}」")
                what = f"技能：{row['label']}"
                ok, why = row["ok"], row["why"]
            elif op == "sound":
                tag = args.get("tag")
                row = next((s for s in mv["sounds"] if s["tag"] == tag), None)
                if row is None:
                    raise Refused(f"不认识的声音：{tag}")
                what = f"叫一声：{row['label']}"
                ok, why = row["ok"], row["why"]
            else:
                btn = next((x for x in mv["buttons"].values() if x["op"] == op), None)
                if btn is None:
                    pose = mv["posture_cn"]
                    msg = {"walk": "已经「准备好走」了：推摇杆就走" if mv["posture"] in DRIVING else f"现在{pose}，「开始走」用不了",
                           "walk_stop": f"现在{pose}，没在走"}.get(op, "现在不能按这个")
                    self._last_cmd = {"what": label or what, "by": by, "ok": False, "reason": msg, "at": now}
                    raise Refused(msg)
                ok, why = btn["ok"], btn["why"]
            what = label or what
            if ok and op not in ("sound", "walk_stop"):
                held = self._lease_holder(now)
                if held is not None and held["conn"] != conn:
                    ok, why = False, f"{held['by'] or '另一个页面'}正在推摇杆：等它松手，或者点「接管」"
            if not ok:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": why, "at": now}
                raise Refused(why)
            if exclusive:
                self._cmd_busy = True
            pose = mv["posture"]
            stand_call = mv["buttons"]["stand"]["call"]
        try:
            ctx = {"posture": pose}
            if op == "stand":
                if stand_call == "robot.init":
                    r = self._call(what, "robot.init", {}, by, ctx)
                else:
                    r = self._call(what + "（坐着：sit_toggle）", "robot.do", {"skill": "sit_toggle"}, by, ctx, posture_target="stand")
            elif op == "walk":
                # 回执 accepted 不等于策略真的开起来：在同一个日志条目里回读 busStatus 确认，
                # 没起来就把主程序的真原因报出来（不再写「成功」）
                r = self._call(what, "robot.enable", {"on": True, "toggle": False}, by, ctx,
                               verify=self._verify_policy_started)
            elif op == "walk_stop":
                with self._lk:
                    d = self._driver
                self._halt("点了停下", d["conn"] if d else None, clear_all=True)   # 三 V1 第 7 条：谁置的头 / 身体都清
                self._call("停下：先把速度清零", "robot.stop", {}, by, ctx)
                # 走着的时候先等速度归零（最多 1 秒）再回站姿（review-impl-plan I2「走着」一行）。
                # 按次数等，不看注入的时钟：时钟停了（测试、或者别的毛病）也不会卡死在这里
                for _ in range(int(WALK_STOP_WAIT_S / 0.05)):
                    applied = (self._robot or {}).get("applied")
                    if not (isinstance(applied, list) and any(abs(v) > 0.01 for v in applied if isinstance(v, (int, float)))):
                        break
                    self._cancel_wait(0.05)
                r = self._call(what, "robot.enable", {"on": False, "toggle": False}, by, ctx)
            elif op == "sit":
                r = self._call(what, "robot.do", {"skill": "sit_toggle"}, by, ctx, posture_target="sit")
            elif op == "skill":
                r = self._call(what, "robot.do", {"skill": args.get("name")}, by, ctx)
            else:
                r = self._call(what, "robot.sound", {"tag": args.get("tag")}, by, ctx)
        except Refused as e:
            with self._lk:
                self._last_cmd = {"what": what, "by": by, "ok": False, "reason": str(e), "at": self.clock()}
            raise
        finally:
            if exclusive:
                with self._lk:
                    self._cmd_busy = False
        note = r.get("reason") if isinstance(r, dict) else None
        with self._lk:
            self._last_cmd = {"what": what, "by": by, "ok": True, "reason": note, "at": self.clock()}
        return {"accepted": True, "reason": note, "did": op}

    # ================= 动作、组合动作（按住才做，松手就停） =================

    def _find_combo(self, cid):
        for c in C.ACTIONS + C.BUILTIN_COMBOS + self._user_combos:
            if c["id"] == cid:
                return c
        return None

    def _action_start(self, args, conn, by):
        """press：页面这一次按下的序号（代码审查 C7）。之后的 action_hold 带同一个序号才算续命：页面上一次按下留下的
        定时器（回执回来之前又松开、又按下）续不了这一次的命。"""
        cid = args.get("id")
        combo = self._find_combo(cid)
        if combo is None:
            raise Refused(f"不认识的动作：{cid}")
        press = self._seq({"seq": args.get("press")})
        now = self.clock()
        with self._lk:
            if not self.motion_studio.request_current():
                return {"dropped": "动作会话已经改变，旧动作请求已丢弃"}
            a = self._action
            if a is not None:
                if a["conn"] == conn and a["id"] == cid:
                    a["hold"] = now
                    a["press"] = press
                    return {"running": cid}
                raise Refused(f"正在做「{a['name']}」（{a['by'] or '页面'}）：等它做完或者先松开")
            mv = self._motion_view(now)
            row = next((x for x in mv["actions"] + mv["combos"] if x["id"] == cid), None)
            if row is None or not row["ok"]:
                raise Refused((row or {}).get("why") or "现在做不了")
            self._take_lease(conn, by, now)          # 离开了手柄页的连接拿不到（三 V5：不再有 action 的口子）
            a = {"id": cid, "name": combo["name"], "conn": conn, "by": by, "started": now, "hold": now, "press": press,
                 "stop": threading.Event(), "why": None, "step": 0, "steps": list(combo["steps"]),
                 "motion_epoch": self.motion_studio.epoch}
            self._action = a
        self.log.info(f"开始做动作「{combo['name']}」（{by or '页面'}）", cat="手柄", raw=C.describe(combo["steps"]), dedup=False)
        threading.Thread(target=self._run_action, args=(a,), name="pad-action", daemon=True).start()
        return {"started": cid, "seconds": round(C.duration(combo["steps"]), 1)}

    def _action_hold(self, conn, press=None):
        """页面说「还按着」。带了序号、跟这个动作开始时的不一样：是上一次按下留下的，不续（代码审查 C7）。"""
        press = self._seq({"seq": press})
        with self._lk:
            a = self._action
            if (a is not None and a["conn"] == conn and conn not in self._gone
                    and (press is None or a.get("press") is None or press == a["press"])):
                a["hold"] = self.clock()
                self._touch_lease(conn, a["hold"])
                return {"running": a["id"]}
        return {"running": None}

    def _action_stop(self, conn, why="松开了"):
        with self._lk:
            a = self._action
            if a is None or (conn is not None and a["conn"] != conn):
                return {}
            a["why"] = a["why"] or why
            a["stop"].set()
        return {}

    def _abort_reason(self, a, needs_policy):
        now = self.clock()
        with self._lk:
            if a["stop"].is_set():
                return a["why"] or "停了"
            if now - a["hold"] > HOLD_S:
                return f"页面 {HOLD_S:g} 秒没说「还按着」（松手、切页或断网）"
            if self._relax_at >= a["started"]:
                return "卸力了"
            r = self._robot or {}
            if r.get("estop_active"):
                return "卸力了"
            if needs_policy and relaxed(r.get("bus")) is True:     # 只动嘴的（三 V10）软着也照做：robotd 自己不动
                return "扭矩关了（软了）"
            if self._pad_attached(now)[0]:
                return "实体手柄连上了"
            d = self._driver
            if d is None or d["conn"] != a["conn"]:
                return "被别的页面接管了"
            base = self._base_block(now)
            if base:
                return base
            if needs_policy and self._posture() not in DRIVING:
                return "策略停了"
        return None

    def _notify_output(self, method, params):
        if self._update_blocked() and not neutral_robot_command(method,params):return False
        if method == "robot.do":
            self.motion_studio.note_skill()
        self.robot.notify(method, params)
        self.motion_studio.accepted(method, params)
        return True

    def _notify_quiet(self, method, params, action=None, cleanup=False):
        with self._send_lk:
            with self._lk:
                if action is not None and (self._action is not action
                        or action.get("motion_epoch") != self.motion_studio.epoch
                        or (action["stop"].is_set() and not cleanup)):
                    return False
            return self._notify_output(method, params)

    def _run_action(self, a):  # noqa: C901
        steps, needs = a["steps"], C.needs_policy(a["steps"])
        moves_head = any(C.head_at(s, 0.0) is not None for s in steps)
        moves_mouth = any(C.mouth_at(s, 0.0) is not None for s in steps)
        result, level = "做完了", "info"
        try:
            if self.robot is None:
                raise _Abort("这只鸭子没配主程序的地址")
            for i, step in enumerate(steps):
                with self._lk:
                    a["step"] = i
                why = self._abort_reason(a, needs)
                if why:
                    raise _Abort(why)
                if step["do"] == "sound":
                    self._call(f"动作「{a['name']}」：叫一声", "robot.sound", {"tag": step["tag"]}, a["by"])
                elif step["do"] == "skill":
                    with self._lk:
                        mv = self._motion_view(self.clock())
                    row = next((s for s in mv["skills"] if s["name"] == step["name"]), None)
                    why = (row or {}).get("why") if row else f"这只鸭子没有「{C.SKILL_CN.get(step['name'], step['name'])}」"
                    if why:
                        raise _Abort(why)
                    if self._abort_reason(a, needs):     # 查完到发出之间也可能卸力：发之前再看一眼
                        raise _Abort(self._abort_reason(a, needs))
                    self._call(f"动作「{a['name']}」：{C.SKILL_CN.get(step['name'], step['name'])}", "robot.do",
                               {"skill": step["name"]}, a["by"])
                t0 = self.clock()
                dur = C.step_seconds(step)
                while True:
                    why = self._abort_reason(a, needs)
                    if why:
                        raise _Abort(why)
                    t = self.clock() - t0
                    if t >= dur:
                        break
                    h, m = C.head_at(step, t), C.mouth_at(step, t)
                    if h is not None:
                        self._notify_quiet("robot.head", dict(zip(("neck_pitch", "head_pitch", "head_yaw", "head_roll"), h)), action=a)
                    if m is not None:
                        self._notify_quiet("robot.mouth", {"open": m}, action=a)
                    a["stop"].wait(C.TICK_S)
        except _Abort as e:
            result, level = f"停了：{e}", "info"
        except Refused as e:
            result, level = f"没做完：{e}", "warn"
        except (Unreachable, Timeout) as e:
            result, level = f"没做完：发不出去（{e}）", "warn"
        except Exception as e:  # noqa: BLE001 —— 不该发生；发生了也要回正、合嘴、说清楚
            result, level = f"Studio 出错：{type(e).__name__}: {e}", "error"
            self.log.error(f"动作「{a['name']}」出错", cat="手柄", raw=f"{type(e).__name__}: {e}", exc=True,
                           hint="这是 Studio 的问题，导出诊断包发出来")
        finally:
            # 头的目标没有过期、嘴不带时间戳：不回正、不合上就会一直留着（pad_combo.py 文件头）。
            # 这个动作里有头 / 嘴的步就都发一次（不管做到哪一步了）；打断之后不再发 robot.do / robot.sound
            for used, method, params in ((moves_head, "robot.head", {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0}),
                                         (moves_mouth, "robot.mouth", {"open": 0.0})):
                if used and self.robot is not None:
                    try:
                        self._notify_quiet(method, params, action=a, cleanup=True)
                    except (Unreachable, Timeout) as e:
                        self.log.warn("动作结束时没能回正 / 合嘴", cat="手柄", step=method, raw=str(e),
                                      hint="头可能还歪着、嘴可能还张着：再按一次那个动作，或者让它坐下再站起来")
            with self._lk:
                if self._action is a:
                    self._action = None
                self._last_action = {"id": a["id"], "name": a["name"], "by": a["by"], "result": result,
                                     "ok": result == "做完了", "at": self.clock()}
            cat = "安全" if ("卸力" in result or "没说" in result or "软了" in result) else "手柄"
            self.log.write(f"动作「{a['name']}」{result}", cat=cat, level=level, step=f"第 {a['step'] + 1}/{len(steps)} 步",
                           dedup=False)

    # ================= 自己编的组合动作：存、删（编辑界面下一轮做） =================

    def _combo_save(self, args):
        builtin = {c["id"] for c in C.ACTIONS + C.BUILTIN_COMBOS}
        combo = C.clean_combo(args.get("combo"), builtin)
        with self._lk:
            others = [c for c in self._user_combos if c["id"] != combo["id"]]
            if len(others) >= C.MAX_USER_COMBOS:
                raise Refused(f"最多存 {C.MAX_USER_COMBOS} 个，先删掉几个")
            new = others + [combo]
        path = C.save_user_combos(self.state_dir, new)
        with self._lk:
            self._user_combos = new
        self.log.info(f"存了组合动作「{combo['name']}」", cat="手柄", step=path, raw=C.describe(combo["steps"]))
        return {"saved": combo["id"]}

    def _combo_delete(self, args):
        cid = args.get("id")
        with self._lk:
            if not any(c["id"] == cid for c in self._user_combos):
                raise Refused("没有这个自己编的组合动作（内置的删不了）")
            new = [c for c in self._user_combos if c["id"] != cid]
        C.save_user_combos(self.state_dir, new)
        with self._lk:
            self._user_combos = new
        self.log.info(f"删了组合动作 {cid}", cat="手柄")
        return {"deleted": cid}
