"""舵机页的全部操作：照搬 servo-web 的 server.py（同仓库 tools/servo-web v0.15.4，我们自己的代码，真机验证过）。

搬过来的：
- 扭矩开 / 关：开之前先把目标对准当前读数；
- 拖滑块写目标；平滑 / 流式两种运动方式；目标回读、没收到的重发；「停」；
- 官方姿势（零位 / 站 / 坐 / 折叠，折叠时膝只折 88°）；存的姿态（存、发、删进回收站）；
- 中位校准：单颗 / 一键；先预览；探偏移符号；关扭矩回读确认；0x0B 不认就兜底写寄存器 31；补重力下垂；
  收尾永远把目标写成当前读数；备份、撤销；校准后存的姿态跟着换算；
- 扫描（已知 / 指定 / 全网 0–253）、导出寄存器 0–86、写寄存器（EEPROM 解锁写）、改 ID、重启 0x08、
  sync_read 时序测试、方向 ±；
- 灵动（头部随机小动作）：servo-web 是在网页里跑的，这里搬到后端线程里跑，页面关了也能停。

Studio 改的（用户要求和方案审查）：
- **卸力锁住**（session.latched）以后，上力、写目标、运动、校准、撤销、写寄存器、改 ID 一律拒绝，要人点「解除锁定」；
- **校完自动保持**（用户：中位校准做完要立刻保持）：成功的那几颗走「对准再上力」；
- **校准基准只用官方零位 2048**（用户：不用折叠）；
- **上力的软硬跟主程序一样**（用户 10-19）：每条上力的路上，开扭矩之前先把 RAM 里的 P / D / 限力写成主程序上力时的
  （参数文件，没有就 P5 · D0 · 限力 1000），写不进去不上力；见 MainGains、apply_gains；
- 文件放在 Studio 的 state 目录（servo-calib/、servo-poses/）；模拟模式的校准备份放临时目录，不跟真舵机混。
"""
import json
import math
import os
import random
import struct
import tempfile
import threading
import time

from .bus import feetech
from .servo import IDS, Refused  # noqa: F401 —— Refused 放在 servo.py（会话的 op_begin 也要抛它），这里照旧导出

CALIB_TOLERANCE = 2      # 校准后读数离目标多少步算到位
DROOP_TOLERANCE = 3      # 关扭矩那几毫秒关节垂了多少步以内就不补偿
OFFSET_LIMIT = 2047      # 偏移寄存器 31 的安全量程（|偏移| ≤ 2047 几种编码理解都一致，超出不写）
CALIB_BIG_DEG = 30       # 摆好的位置离目标差这么多度以上：页面再确认一次，日志单独列出来（不拦）
FOLD_KNEE_SEND = 88 * math.pi / 180     # 发「折叠」时膝只折 88°：官方 90° 会让踝顶着大腿上的舵机
JOINT = {20: "left_hip_yaw", 21: "left_hip_roll", 22: "left_hip_pitch", 23: "left_knee", 24: "left_ankle",
         30: "neck_pitch", 31: "head_pitch", 32: "head_yaw", 33: "head_roll", 34: "mouth",
         10: "right_hip_yaw", 11: "right_hip_roll", 12: "right_hip_pitch", 13: "right_knee", 14: "right_ankle"}
# 官方姿势：microduck_rl scene.xml 的 keyframe（跟 servo-web 同一份），弧度、模型约定，按舵机号。嘴只有零位带
OFFICIAL = {
    "zero": ("零位", {i: 0.0 for i in IDS}),
    "stand": ("站", {20: 0, 21: -0.0873, 22: -0.4579, 23: -0.0049, 24: 0.453, 30: 0.3491, 31: 0.3491, 32: 0, 33: 0,
                     10: 0, 11: 0.0873, 12: 0.4579, 13: 0.0049, 14: -0.453}),
    "sit": ("坐", {20: 0, 21: 0, 22: -0.5236, 23: 1.0472, 24: 0, 30: 0.5, 31: 1.6, 32: 0, 33: 0,
                   10: 0, 11: 0, 12: 0.5236, 13: -1.0472, 14: 0}),
    "fold": ("折叠", {20: 0, 21: 0, 22: 1.57, 23: 1.57, 24: 0, 30: 1, 31: 1, 32: 0, 33: 0,
                      10: 0, 11: 0, 12: -1.57, 13: -1.57, 14: 0}),
}
# 写这些寄存器后果严重：页面标红并写明后果（审查 G3）
DANGER = {5: "ID：改了会撞号，总线上只能接这一颗再改", 6: "波特率：改了就失联", 8: "应答级别：改成 0 写指令就不回包",
          9: "最小角度限制", 11: "最大角度限制", 19: "卸载条件（保护开关）：关掉过热过流就不自动卸",
          33: "运行模式：改成速度模式舵机会一直转", 40: "扭矩开关：用页面上的开关，别在这里写"}
DEG = 4096 / 360
# 灵动：每个动作 [相对中心的度数, 秒]。32 yaw、33 roll、31 头 pitch、30 颈 pitch、34 嘴（正 = 张）。{} = 停住
GESTURES = [
    (3, lambda s: [({33: 14 * s, 32: 8 * s}, 0.5), ({}, random.uniform(0.6, 1.4)), ({33: 0, 32: 0}, 0.6)]),           # 歪头
    (3, lambda s: [({32: 30 * s, 33: -5 * s, 31: 4}, 0.6), ({}, random.uniform(0.8, 1.8)), ({32: 0, 33: 0, 31: 0}, 0.7)]),  # 看一边
    (2, lambda s: [({32: -20}, 0.3), ({32: 20}, 0.38), ({32: -12}, 0.32), ({32: 0}, 0.35)]),                            # 摇头
    (2, lambda s: [({31: 12}, 0.3), ({31: -7}, 0.34), ({31: 6}, 0.3), ({31: 0}, 0.35)]),                                # 点头
    (2, lambda s: [({34: 16}, 0.22), ({34: 0}, 0.26), ({34: 13}, 0.22), ({34: 0}, 0.28)]),                              # 嘎嘎两下
    (2, lambda s: [({33: 12 * s, 31: -8, 34: 9}, 0.55), ({}, random.uniform(0.5, 1.0)), ({34: 0}, 0.25), ({33: 0, 31: 0}, 0.6)]),  # 好奇
    (2, lambda s: [({32: 18 * s, 31: 6}, 0.45), ({32: -18 * s, 31: 6}, 0.7), ({32: 0, 31: 0}, 0.5)]),                   # 扫视
    (1, lambda s: [({33: 7, 32: 6}, 0.18), ({33: -7, 32: -6}, 0.2), ({33: 0, 32: 0}, 0.25)]),                           # 抖一下
]
CAT = {"运动": "总线", "寄存器": "总线", "方向": "校准", "姿态": "校准"}


def enc_sm(v):
    """符号-幅值编码：寄存器 31（偏移）、42（目标位置）、46（速度）都是 BIT15 当方向位，不是补码。
    写 -5 要写 0x8005；写成 0xFFFB 舵机会理解成 -32763，然后满速撞限位。"""
    v = int(v)
    return (0x8000 | (-v & 0x7FFF)) if v < 0 else (v & 0x7FFF)


def fmt_off(v):
    if v is None:
        return "?"
    if v < 0:
        return str(v)
    return f"{feetech.sign15(v)}（原始{v}）" if v & 0x8000 else str(v)


def _ack(err):
    """写指令的应答：0 = 正常；None = 没回包；别的 = 舵机状态位有报警。"""
    return "✓" if err == 0 else ("（没应答）" if err is None else f"（状态 0x{err:02X}）")


def infer_offset_sign(before, after, d_off, slack=8):
    """从一次校准反推偏移方向：读数从 before 变到 after，偏移变了 d_off。读数 = 原始 + s × 偏移，所以 after − before ≈ s × d_off
    （中间关节可能又垂了几步，留 slack）。只有一个方向对得上、另一个明显对不上才算数；偏移变得太少分不清，回 None。"""
    if before is None or after is None or d_off is None or abs(d_off) < 4:
        return None
    moved = after - before
    miss = {s: abs(moved - s * d_off) for s in (1, -1)}
    s = min(miss, key=miss.get)
    if miss[s] > max(slack, abs(d_off) // 2) or miss[-s] - miss[s] < abs(d_off):
        return None
    return s


def parse_ids(text):
    """「20-24,30-34,10-14」→ [20..24, 30..34, 10..14]。"""
    out = []
    for part in str(text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    if any(not 0 <= i <= 253 for i in out):
        raise Refused("ID 要在 0–253 之间")
    return out


# ---------------- 上力时的 P / D / 限力：跟主程序一样（用户 10-19） ----------------
# 「P、D 那些应该跟主程序保持一致，也是默认 P5、D0、限力 1000」：Studio 给舵机上力时写的软硬，跟主程序（我们的 fork robotd）
# 上力时写的一样，不用舵机 EEPROM 里的出厂值（P32 / D40，硬得多）。
# 这样调舵机时的软硬跟走路时一样；以后「不卸力交接」主程序核对得过，也不用换 P（带载关节换 P 会塌一下）。
ROBOT_ETC = "/etc/robot/feetech-ft5"
SERVO_PARAMS = ROBOT_ETC + "/servo-test-parameters.json"       # 标定文件同目录（fork hd1910_test.rs:21-22）
ROBOTD_TOMLS = (ROBOT_ETC + "/robotd.toml", "/etc/robot/robotd.toml")   # 跟 onboard.ROBOTD_CONFIGS 同一份
POLICY_DIR = "/opt/robot/policies/current"                     # [policy] walk 没写时的目录（fork robotd-params lib.rs:41、1611-1666）
DEFAULT_GAINS = (5, 0, 1000)      # V167 没参数文件：P = firmware_p() 没合约时 5（deployment.rs:78-80），D0、限力 1000（hd1910.rs:257-267）
GAIN_REGS = {21: "EEPROM 的 P", 22: "EEPROM 的 D", 48: "限力", 49: "限力高字节", 50: "P", 51: "D"}
GAINS_RECHECK_S = 3.0             # 页面每秒 10 帧要看「软硬」：文件最多每 3 秒 stat 一次，mtime 变了才重读


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load_servo_params(path):
    """照 fork hd1910_test.rs:21-42 读 servo-test-parameters.json：正好 kp、kd、torque_limit 三个键（deny_unknown_fields），
    都是整数（serde 的 u8/u16 不收 5.0、true），P 1–32、D 0–64、限力 200–1000。不合法抛 ValueError（说清哪一条）。"""
    def pairs(items):
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError("有重复的键")
        return dict(items)
    with open(path, "rb") as f:
        obj = json.loads(f.read().decode("utf-8"), object_pairs_hook=pairs)
    if not isinstance(obj, dict) or set(obj) != {"kp", "kd", "torque_limit"}:
        raise ValueError("要正好三个键 kp、kd、torque_limit")
    for k in ("kp", "kd", "torque_limit"):
        if type(obj[k]) is not int:
            raise ValueError(f"{k} 要是整数，不是 {obj[k]!r}")
    if not 1 <= obj["kp"] <= 32:
        raise ValueError("P（kp）要在 1–32")
    if not 0 <= obj["kd"] <= 64:
        raise ValueError("D（kd）要在 0–64")
    if not 200 <= obj["torque_limit"] <= 1000:
        raise ValueError("限力（torque_limit）要在 200–1000")
    return obj["kp"], obj["kd"], obj["torque_limit"]


def _walk_contract(toml_path):
    """robotd.toml 的 [policy] walk → 那个模型目录里的 deployment-contract.json（fork deployment.rs:147-150）。
    walk 没写或写 "none"：按 mode 用默认（walk 模式 velstand.onnx，roller 模式 roller.onnx，在 POLICY_DIR 里）。"""
    import tomllib     # noqa: PLC0415 —— Python 3.11+；鸭子上是 3.11
    with open(toml_path, "rb") as f:
        policy = tomllib.load(f).get("policy") or {}
    walk = policy.get("walk")
    if not isinstance(walk, str) or not walk.strip() or walk.strip().lower() == "none":
        walk = POLICY_DIR + "/" + ("roller.onnx" if policy.get("mode") == "roller" else "velstand.onnx")
    elif not (walk.startswith("/") or os.path.isabs(walk)):
        walk = "/" + walk                 # 相对路径：robotd 在 systemd 下工作目录是 /（isabs 那半是给电脑上的测试）
    return walk.rsplit("/", 1)[0] + "/deployment-contract.json"


def contract_kp(path):
    """走路模型合约里训练用的舵机 P：model.actuators[0].kp_fw，整数 1–32（fork deployment.rs:556-568）。不合法抛 ValueError。"""
    with open(path, encoding="utf-8") as f:
        v = json.load(f)
    acts = ((v.get("model") or {}).get("actuators")) if isinstance(v, dict) else None
    if not isinstance(acts, list) or len(acts) != 1 or not isinstance(acts[0], dict):
        raise ValueError("model.actuators 要正好一个")
    p = acts[0].get("kp_fw")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p != int(p) or not 1 <= p <= 32:
        raise ValueError(f"kp_fw 要是 1–32 的整数，不是 {p!r}")
    return int(p)


class MainGains:
    """主程序上力时给舵机的 P / D / 限力（fork robotd 的两条路，duck-control/src/hd1910.rs:106-191）：
    1. 有 servo-test-parameters.json 且合法：用它（arm_with_file）；
    2. 没有：V167 原版——P = 走路模型合约的 kp_fw（robotd selftrained_profile.rs:11-27 装进 firmware_p），D = 0，限力 = 1000；
       合约读不到 P 按 5（firmware_p() 的默认）；
    3. 文件坏 / 超范围：主程序会停在「等总线」、不上力（fork docs/project/replica-v167.zh-CN.md:16、39）。Studio 用 2 并记一条 warn。
    电脑上（--bench 台架、演示舵机）没有这些文件，就是 5 / 0 / 1000。按 mtime 缓存：文件没变不重读。
    get() 回 {"kp", "kd", "limit", "source"（「参数文件」/「主程序默认」）, "detail", "warn"}。"""

    def __init__(self, params_path=SERVO_PARAMS, robotd_tomls=ROBOTD_TOMLS, log=None):
        self.params_path, self.robotd_tomls, self.log = params_path, tuple(robotd_tomls), log
        self._lock = threading.Lock()
        self._key, self._value, self._checked = None, None, 0.0
        self._walk = (None, None)          # (robotd.toml 的 key, 合约路径)：toml 没变不重新解析

    def get(self, max_age=0.0):
        with self._lock:
            now = time.monotonic()
            if self._value is not None and now - self._checked < max_age:
                return self._value
            self._checked = now
            toml = next((p for p in self.robotd_tomls if _stat(p)), None)
            tkey = (toml, _stat(toml) if toml else None)
            if self._walk[0] != tkey:
                try:
                    contract = _walk_contract(toml) if toml else None
                except Exception as e:  # noqa: BLE001
                    contract = ("bad", f"{type(e).__name__}: {e}")
                self._walk = (tkey, contract)
            contract = self._walk[1]
            key = (_stat(self.params_path), tkey, contract,
                   _stat(contract) if isinstance(contract, str) else None)
            if key != self._key or self._value is None:
                self._key, self._value = key, self._compute(toml, contract)
                if self._value["warn"] and self.log:
                    self.log(self._value["warn"], "总线", "warn")
            return self._value

    def _compute(self, toml, contract):
        kp, kd, limit = DEFAULT_GAINS
        warn = None
        if _stat(self.params_path):
            try:
                kp, kd, limit = load_servo_params(self.params_path)
                return {"kp": kp, "kd": kd, "limit": limit, "source": "参数文件",
                        "detail": f"{self.params_path}（主程序有这个文件时上力就写它）", "warn": None}
            except Exception as e:  # noqa: BLE001
                warn = (f"舵机参数文件 {self.params_path} 不合法（{e}）：主程序遇到它会停在「等总线」、不上力；"
                        f"Studio 先按主程序默认上力。改好或删掉这个文件")
        etc = os.path.dirname(self.params_path)
        if toml is None and not os.path.isdir(etc):
            detail = f"这台机器上没有主程序的配置（{etc}），按主程序 V167 默认：P5、D0、限力 1000"
        elif isinstance(contract, str):
            try:
                kp = contract_kp(contract)
                detail = f"主程序 V167：P 取走路模型合约的 kp_fw（{contract}），D0、限力 1000"
            except Exception as e:  # noqa: BLE001
                detail = f"走路模型合约 {contract} 读不出 kp_fw（{type(e).__name__}: {e}）：P 按主程序的默认 5，D0、限力 1000"
        else:
            why = contract[1] if isinstance(contract, tuple) else "没找到 robotd.toml"
            detail = f"读不到走路模型（{why}）：P 按主程序的默认 5，D0、限力 1000"
        return {"kp": kp, "kd": 0, "limit": 1000, "source": "主程序默认", "detail": detail, "warn": warn}


def gains_bytes(g):
    """RAM 48..51 一包：[限力低, 限力高, P, D]（fork hd1910_test.rs:44-47 payload）。"""
    return struct.pack("<H", g["limit"]) + bytes([g["kp"], g["kd"]])


def gains_text(b):
    return "读不到" if b is None else f"P{b[2]} · D{b[3]} · 限力 {b[0] | b[1] << 8}"


class ServoOps:
    def __init__(self, session, state_dir):
        self.s = session
        self.state_dir = state_dir
        self.offset_sign = None          # +1：读数 = 原始 + 偏移；-1：读数 = 原始 − 偏移。手册没写死，校准前探一次
        self.speed_unit = 1.0            # 速度寄存器 46 一个单位 = 多少步/秒（相位 18 BIT2 决定）
        self.max_speed_reg = 3000        # 「不限速」写多少（相位 BIT3=1 时 0 = 最快）
        self.stream_last = {}
        self.calib_lock = threading.Lock()   # 校准期间不让状态轮询抢总线（关扭矩的窗口里关节会垂）
        self._motion = None
        self._motion_stop = threading.Event()
        self.idle_running = False
        self._idle_thread = None
        self._sim_calib_dir = None
        # 上力用的 P / D / 限力（跟主程序一样，MainGains）；manual：寄存器面板手动写过的 P/D/限力 {id: {地址: 读回值}}
        self.gains = MainGains(log=self.log)
        self.manual = {}
        self._gains_logged = None

    # ---- 小工具 ----

    @property
    def bus(self):
        if self.s.bus is None or self.s.state != "open":
            raise Refused("舵机总线没打开：先点「开始调舵机」")
        return self.s.bus

    def log(self, msg, cat="总线", level="info", exc=False):
        self.s.log.write(msg, cat=CAT.get(cat, cat), level=level, exc=exc)

    def guard(self):
        if self.s.latched:
            raise Refused("刚才全部卸了力：先点「全部扭矩开」或某颗的「保持」，再让它动")

    def dirs(self):
        return self.s.directions

    def present(self):
        return [i for i in IDS if (self.s.rows.get(i) or {}).get("at")] or list(IDS)

    def calib_dir(self):
        if self.s.sim:
            if not self._sim_calib_dir or not os.path.isdir(self._sim_calib_dir):
                self._sim_calib_dir = tempfile.mkdtemp(prefix="studio-sim-calib-")
            return self._sim_calib_dir
        return os.path.join(self.state_dir, "servo-calib")

    def poses_dir(self):
        return os.path.join(self.state_dir, "servo-poses")

    def read_off(self, i):
        return feetech.sign15(self.bus.read_u16(i, 31))

    def read_pos(self, i):
        return feetech.sign15(self.bus.read_u16(i, 56))

    def write_off(self, i, v):
        return self.bus.write_u16(i, 31, enc_sm(v))

    def wait_off_change(self, i, was, ms=20):
        """写完偏移就轮询它落地没有，最多等 ms 毫秒（落地通常 1~2 ms；固定睡 20 ms 等于白送重力下垂）。"""
        end = time.monotonic() + ms / 1000.0
        off = was
        while True:
            off = self.read_off(i)
            if off != was or time.monotonic() >= end:
                return off, off != was

    def read_phase(self):
        """开总线时读一次相位 18，确定速度单位；保留舵机现有的速度、加速度和目标。"""
        if self.s.sim:
            return
        try:
            ph = self.bus.read_u8(self.present()[0], 18)
            self.speed_unit = 1.0 if ph & 0x04 else 50.0
            self.max_speed_reg = 0 if ph & 0x08 else int(3000 / self.speed_unit)
            self.log(f"相位 18 = {ph} → 速度单位 {self.speed_unit:g} 步/秒，不限速写 {self.max_speed_reg}")
        except Exception as e:  # noqa: BLE001
            self.log(f"读相位失败，速度单位按 1 步/秒：{type(e).__name__}: {e}", "系统", "warn")

    def release_speed(self, ids):
        """放开速度上限、加速度设最大（41 = 0，46 = 不限速）。只写这两个，**不碰目标 42**。"""
        ids = list(ids)
        if ids:
            self.bus.sync_write(41, 1, [(i, bytes([0])) for i in ids])
            self.bus.sync_write(46, 2, [(i, struct.pack("<H", enc_sm(self.max_speed_reg))) for i in ids])

    # ---- 上力时的 P / D / 限力（跟主程序一样） ----

    def _read_gains(self, ids):
        """RAM 48..51 回读（跟 fork hd1910_test.rs:70-81 verify 同一个 sync_read 48×4）；没回包的逐颗再读一次（总线偶尔丢一包）。
        回 {id: 4 字节 或 None}；读到的顺手记进 session.static（表里显示运行中的 P / D / 限力）。"""
        got = self.bus.sync_read(ids, 48, 4)
        out = {}
        for i in ids:
            v = got.get(i)
            data = bytes(v[1]) if v else None
            if data is None:
                try:
                    data = bytes(self.bus.read(i, 48, 4)[1])
                except feetech.BusError:
                    data = None
            out[i] = data
            if data is not None:
                st = self.s.static.setdefault(i, {})
                st["torque_limit"], st["p_run"], st["d_run"] = data[0] | data[1] << 8, data[2], data[3]
        return out

    def _log_gains(self, g):
        if self._gains_logged != g:
            self._gains_logged = g
            self.log(f"上力用的软硬：P{g['kp']} · D{g['kd']} · 限力 {g['limit']}（跟主程序一样 · {g['source']}：{g['detail']}）")

    def _clear_manual(self, i):
        """这颗运行中的 P / D / 限力又跟主程序一样了：手动改过的 RAM 那几项（48–51）不再算；EEPROM 的 21 / 22 还记着。"""
        m = self.manual.get(i)
        if m:
            for a in (47, 48, 49, 50, 51):            # 47：从 47 起写 2 字节也盖到了 48
                m.pop(a, None)
            if not m:
                self.manual.pop(i, None)

    def apply_gains(self, ids):
        """上力前：把这几颗（扭矩关着的）P / D / 限力写成主程序上力时的值（MainGains）。照 fork 有参数文件那条路的写法
        （duck-control/src/hd1910.rs:159-191 arm_with_file、hd1910_test.rs:44-81）：RAM 48..51 一包 4 字节
        [限力低, 限力高, P, D]，sync_write 写，sync_read 48×4 回读核对。不碰 EEPROM 21 / 22、不碰锁标志 55
        （RAM 不受它管，fork 也不写）、不碰 52（I）/ 41（加速度）/ 46（速度）。
        跟 fork 不一样的一处：只写回读跟要的不一样的那几颗（fork 每次 15 颗全写），没对上的再写一次。
        回 {id: 原因}：读不到、写了读回不对的那几颗——调用方不给它们上力。"""
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        g = self.gains.get()
        self._log_gains(g)
        want = gains_bytes(g)
        before, wrote, cur = {}, [], {}
        try:
            cur = self._read_gains(ids)
            before = dict(cur)
            todo = [i for i in ids if cur[i] != want]
            for _ in range(2):
                if not todo:
                    break
                self.bus.sync_write(48, 4, [(i, want) for i in todo])
                wrote += [i for i in todo if i not in wrote]
                cur.update(self._read_gains(todo))
                todo = [i for i in todo if cur[i] != want]
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            bad = {i: f"写 P/D/限力时总线出错（{err}）" for i in ids if cur.get(i) != want}
            self.log(f"上力前写 P/D/限力出错：{err}；这几颗不上力 {sorted(bad)}", "总线", "error", exc=True)
            return bad
        bad = {}
        for i in ids:
            if cur[i] == want:
                self._clear_manual(i)
                continue
            if cur[i] is None:
                bad[i] = "读不到 P/D/限力（寄存器 48–51 没回包）"
            else:
                c = cur[i]
                diff = [f"{a}（{name}）写 {w} 读回 {r}" for a, name, w, r in (
                    (48, "限力", g["limit"], c[0] | c[1] << 8), (50, "P", g["kp"], c[2]), (51, "D", g["kd"], c[3])) if w != r]
                bad[i] = "P/D/限力写不进去：寄存器 " + "、".join(diff)
            self.log(f"#{i} 上力前写 P/D/限力（P{g['kp']} · D{g['kd']} · 限力 {g['limit']}）：{bad[i]}；这颗不上力", "总线", "error")
        ok = [i for i in wrote if i not in bad]
        if ok:
            self.log(f"上力前 P/D/限力写成 P{g['kp']} · D{g['kd']} · 限力 {g['limit']}（{g['source']}）："
                     + " ".join(f"#{i}（原来 {gains_text(before.get(i))}）" for i in ok))
        return bad

    def check_gains(self, ids):
        """已经上着力的：只核对、不写（fork 扭矩开着时 arm 也只核对：hd1910.rs:116-140、169-179；
        上着力换 P 带载关节会塌一下）。回 {id: 说明}：跟主程序不一样的那几颗（照样算上着力，页头标黄）。"""
        ids = [int(i) for i in ids]
        if not ids:
            return {}
        want = gains_bytes(self.gains.get())
        try:
            cur = self._read_gains(ids)
        except Exception as e:  # noqa: BLE001
            self.log(f"核对上着力的舵机的 P/D/限力出错：{type(e).__name__}: {e}", "总线", "warn")
            return {}
        differ = {}
        for i in ids:
            if cur[i] == want:
                self._clear_manual(i)
            elif cur[i] is not None:
                differ[i] = (f"已经上着力，软硬是 {gains_text(cur[i])}，跟主程序（{gains_text(want)}）不一样：没改它"
                             "（上着力换 P 带载关节会塌一下）；要改先松开再保持")
        if differ:
            self.log("上着力的舵机软硬跟主程序不一样，没改：" + " ".join(f"#{i} {gains_text(cur[i])}" for i in differ)
                     + f"（主程序 {gains_text(want)}）；要改先松开再保持", "总线", "warn")
        return differ

    def _gains_then_torque(self, i, trail, label="开扭矩"):
        """校准 / 撤销里给这一颗恢复扭矩：先写主程序的 P/D/限力（apply_gains），写不进去就不恢复。回恢复了没有。"""
        bad = self.apply_gains([i])
        if bad:
            trail.append(f"✗{bad[i]}，扭矩没恢复")
            return False
        trail.append(label + _ack(self.bus.write_u8(i, 40, 1)))
        return True

    def gains_view(self, rows=None):
        """页头「软硬：P5 · D0 · 限力 1000（跟主程序一样 · 来源）」要的。rows（snapshot 的每一行）顺手补上运行中的
        P / D / 限力（RAM 50 / 51 / 48，开总线时、上力前、写寄存器后读的）和 gains_ok（跟主程序一样没有；读不到 None）。
        在事件循环里调：不碰总线，文件最多每 GAINS_RECHECK_S 秒 stat 一次。"""
        g = self.gains.get(max_age=GAINS_RECHECK_S)
        want = (g["kp"], g["kd"], g["limit"])
        differ_on = []
        for r in rows or []:
            st = self.s.static.get(r["id"]) or {}
            run = (st.get("p_run"), st.get("d_run"), st.get("torque_limit"))
            r["p_run"], r["d_run"], r["limit"] = run
            r["gains_ok"] = None if None in run else run == want
            if r["gains_ok"] is False and r.get("torque") == 1:
                differ_on.append(r["id"])
        manual = [{"id": i, "addr": a, "value": v, "what": GAIN_REGS.get(a, "")}
                  for i, m in sorted(list(self.manual.items())) for a, v in sorted(list(m.items()))]
        return {"kp": g["kp"], "kd": g["kd"], "limit": g["limit"], "source": g["source"], "detail": g["detail"],
                "warn": g["warn"], "manual": manual, "differ_on": differ_on}

    # ---- 扭矩 ----

    def torque_on_aligned(self, ids):
        """开扭矩前先把目标（42）对准当前读数：手掰过、扭矩关着时发过姿势 / 校准过，目标里还是旧值，
        一开扭矩舵机就冲过去。读扭矩 → 关着的写 P/D/限力（跟主程序一样，apply_gains）→ 读位置 → 写目标 = 位置
        → 读 42 核对 → 开扭矩（跟 fork 的顺序一样：arm 写增益 → 预载测到的位置 → 扭矩开，feetech.rs:1836-1873）。
        本来就开着的不动它的目标、也不改它的 P/D/限力（只核对）。P/D/限力写不进去、读不到位置、目标写不进去的那颗不开。"""
        self.guard()
        bus = self.bus
        tq = bus.sync_read(ids, 40, 1)
        already = [i for i in ids if tq.get(i) and tq[i][1][0] == 1]
        need = [i for i in ids if i not in already]
        aligned, bad = {}, {}
        # 用户 10-19：上力的软硬跟主程序一样（不用 EEPROM 里的出厂 P32 / D40）。写不进去的那颗不开
        bad.update({i: w + "，没开扭矩" for i, w in self.apply_gains(need).items()})
        differ = self.check_gains(already)
        need = [i for i in need if i not in bad]
        st = bus.states(need) if need else {}
        for i in need:
            if not st.get(i):
                bad[i] = "读不到位置，没开扭矩（开了可能冲向旧目标）"
                continue
            p = st[i]["pos"]
            got = None
            try:
                for _ in range(2):
                    bus.write_u16(i, 42, enc_sm(p))
                    got = feetech.sign15(bus.read_u16(i, 42))
                    if got == p:
                        aligned[i] = p
                        break
                else:
                    bad[i] = f"目标写 {p} 读回 {got}，没开扭矩"
            except Exception as e:  # noqa: BLE001
                bad[i] = f"写目标出错（{type(e).__name__}: {e}），没开扭矩"
        on = already + [i for i in need if i in aligned]
        if on:
            self.guard()                                   # 对准的这几毫秒里有人按了卸力：不开
            bus.sync_write(40, 1, [(i, bytes([1])) for i in on])
        self.s._read_torque()
        self.log(f"扭矩开 → {on}" + (f"；开之前目标对准当前位置：" + " ".join(f"#{i}→{p}" for i, p in aligned.items()) if aligned else "")
                 + (f"；没开 " + " ".join(f"#{i}（{w}）" for i, w in bad.items()) if bad else ""), "运动",
                 "warn" if bad else "info")
        g = self.gains.get(max_age=GAINS_RECHECK_S)
        return {"on": on, "bad": {str(i): w for i, w in bad.items()}, "differ": {str(i): w for i, w in differ.items()},
                "gains": {k: g[k] for k in ("kp", "kd", "limit", "source")}}

    def torque_off(self, ids):
        self.bus.sync_write(40, 1, [(i, bytes([0])) for i in ids])
        self.s._read_torque()
        self.log(f"扭矩关 → {ids}", "运动")
        return {"off": ids}

    # ---- 运动 ----

    def goal(self, sid, pos):
        self.guard()
        pos = max(0, min(4095, int(pos)))
        self.bus.write_u16(int(sid), 42, enc_sm(pos))
        return {"id": sid, "pos": pos}

    def goals_verify(self, want):
        """写目标并回读核对，没收到的重发，最多 4 轮。"""
        want = {int(i): max(0, min(4095, int(p))) for i, p in want.items()}
        attempt = 0
        for attempt in range(4):
            self.bus.sync_write(42, 2, [(i, struct.pack("<H", p)) for i, p in want.items()])
            time.sleep(0.02)
            st = self.bus.states(list(want))
            want = {i: p for i, p in want.items() if not st.get(i) or (st[i]["goal"] & 0xFFFF) != p}
            if not want:
                break
        if want:
            self.log(f"目标回读：{len(want)} 颗 4 轮后目标仍没写进去 {sorted(want)}", "运动", "warn")
        return {"missing": sorted(want), "attempts": attempt + 1}

    def move(self, goals, seconds=2.0, mode="profile", acc=30):
        """从当前位置走到 goals（{id: 读数}）。profile = 平滑（舵机自己走梯形曲线）；stream = 流式（这边 20 Hz 插值）。
        在后台线程里走，马上返回；「停」或卸力会打断它。"""
        self.guard()
        goals = {int(i): max(0, min(4095, int(p))) for i, p in goals.items() if int(i) in IDS}
        if not goals:
            raise Refused("没有要动的舵机")
        self.stop(quiet=True)
        self._motion_stop.clear()
        t = threading.Thread(target=self._run_move, args=(goals, float(seconds), mode, int(acc)), name="servo-move", daemon=True)
        self._motion = t
        t.start()
        return {"started": len(goals), "seconds": seconds, "mode": mode}

    def _run_move(self, goals, seconds, mode, acc):
        try:
            if mode == "profile" and seconds > 0:
                self._profile(goals, seconds, acc)
                if self._motion_stop.wait(seconds + 0.4):
                    return
            else:
                st = self.bus.states(list(goals))
                start = {i: (st[i]["pos"] if st.get(i) else p) for i, p in goals.items()}
                t0 = time.monotonic()
                while True:
                    if self.s.halted:
                        return
                    tt = 1.0 if seconds <= 0 else min(1.0, (time.monotonic() - t0) / seconds)
                    g = {i: round(start[i] + (p - start[i]) * tt) for i, p in goals.items()}
                    if tt >= 1:
                        break
                    self._stream(g, 0.05)
                    if self._motion_stop.wait(0.05):
                        return
            if not self.s.halted:
                self.goals_verify(goals)
                self.release_speed(list(goals))
                for i in goals:
                    self.stream_last.pop(i, None)
        except Refused:
            pass
        except Exception as e:  # noqa: BLE001
            self.log(f"运动出错：{type(e).__name__}: {e}", "运动", "error", exc=True)

    def _profile(self, want, seconds, acc):
        """一次写 41..47 = 加速度(1) 目标(2) 时间(2)=0 速度(2)，舵机自己走梯形曲线。"""
        self.guard()
        acc = max(1, min(254, acc))
        st = self.bus.states(list(want))
        items = []
        for i, p in want.items():
            cur = st[i]["pos"] if st.get(i) else p
            sps = abs(p - cur) / max(0.2, seconds)
            reg = max(1, min(32767, int(round(sps / self.speed_unit)))) if sps > 0 else 1
            items.append((i, bytes([acc]) + struct.pack("<H", enc_sm(p)) + struct.pack("<H", 0) + struct.pack("<H", reg)))
        self.bus.sync_write(41, 7, items)
        for i in want:
            self.stream_last.pop(i, None)

    def _stream(self, want, dt):
        """连续流：每帧给目标，同时把速度上限设成「正好在 dt 内走到」，加速度最大。"""
        self.guard()
        fresh = [i for i in want if i not in self.stream_last]
        if fresh:
            st = self.bus.states(fresh)
            for i in fresh:
                self.stream_last[i] = st[i]["pos"] if st.get(i) else want[i]
        items = []
        for i, p in want.items():
            sps = abs(p - self.stream_last[i]) / dt * 1.2
            reg = max(3, min(32767, int(round(sps / self.speed_unit))))
            items.append((i, bytes([0]) + struct.pack("<H", enc_sm(p)) + struct.pack("<H", 0) + struct.pack("<H", reg)))
            self.stream_last[i] = p
        self.bus.sync_write(41, 7, items)

    def stop(self, quiet=False):
        """停：打断正在走的过渡和灵动，把目标改成当前位置（平滑模式下舵机已经拿到终点在自己走，这样它停在原地）。"""
        self._motion_stop.set()
        self.idle_running = False
        if self._motion and self._motion.is_alive():
            self._motion.join(timeout=0.5)
        if quiet:
            return {}
        if self.s.state == "open":
            st = self.bus.states(self.present())
            g = {i: s["pos"] for i, s in st.items() if s}
            if g and not self.s.halted:
                self.goals_verify(g)
            self.log("已停止：目标改成当前位置", "运动")
        return {}

    def official_goals(self, key, for_send=True):
        if key not in OFFICIAL:
            raise Refused(f"不认识的官方姿势：{key}")
        label, rad = OFFICIAL[key]
        rad = dict(rad)
        if key == "fold" and for_send:
            rad[23], rad[13] = FOLD_KNEE_SEND, -FOLD_KNEE_SEND
        d = self.dirs()
        return label, {i: int(round(2048 + a * 4096 / (2 * math.pi) * d.get(i, -1))) for i, a in rad.items()}

    def go_official(self, key, seconds, mode, acc):
        label, goals = self.official_goals(key)
        self.log(f"去官方姿势「{label}」（{seconds:g} 秒，{'平滑' if mode == 'profile' else '流式'}）", "运动")
        return self.move(goals, seconds, mode, acc)

    # ---- 灵动 ----

    def idle(self, on):
        if not on:
            self.idle_running = False
            return {"idle": False}
        self.guard()
        head = [i for i in (30, 31, 32, 33, 34) if i in self.present()]
        if len(head) < 3:
            raise Refused("头部舵机不在线")
        self.torque_on_aligned(head)
        st = self.bus.states(head)
        center = {i: st[i]["pos"] for i in head if st.get(i)}
        self.stop(quiet=True)
        self.idle_running = True
        self._idle_thread = threading.Thread(target=self._run_idle, args=(center,), name="servo-idle", daemon=True)
        self._idle_thread.start()
        self.log(f"灵动开始，中心 {center}", "运动")
        return {"idle": True}

    def _run_idle(self, center):
        d = self.dirs()

        def head(delta, sec):
            goals = {i: max(0, min(4095, round(center[i] + deg * DEG * d.get(i, -1)))) for i, deg in delta.items() if i in center}
            if goals:
                self._profile(goals, sec, 35)
            time.sleep(sec + 0.06)

        bag = [g for w, g in GESTURES for _ in range(w)]
        last = None
        try:
            while self.idle_running and not self.s.halted:
                g = random.choice(bag)
                if g is last:
                    g = random.choice(bag)
                last = g
                for delta, sec in g(random.choice([-1, 1])):
                    if not self.idle_running or self.s.halted:
                        break
                    if delta:
                        head(delta, sec)
                    else:
                        time.sleep(sec)
                if self.idle_running:
                    time.sleep(random.uniform(0.3, 1.5))
            if not self.s.halted and self.s.state == "open":
                head({i: 0 for i in center}, 0.7)
                self.release_speed(list(center))
            self.log("灵动结束，头回中心", "运动")
        except Refused:
            pass
        except Exception as e:  # noqa: BLE001
            self.log(f"灵动出错：{type(e).__name__}: {e}", "运动", "error", exc=True)
        finally:
            self.idle_running = False

    # ---- 中位校准 ----

    def backup_calibration(self, ids):
        rows = {}
        for i in ids:
            try:
                ver = self.bus.read(i, 0, 5)[1]
                rows[str(i)] = {"offset": self.bus.read_u16(i, 31), "pos": self.read_pos(i),
                                "torque": self.bus.read_u8(i, 40), "lock": self.bus.read_u8(i, 55),
                                "firmware": f"{ver[0]}.{ver[1]}"}
            except Exception as e:  # noqa: BLE001
                rows[str(i)] = {"error": f"{type(e).__name__}: {e}"}
                self.log(f"#{i} 备份时读不到：{type(e).__name__}: {e}", "校准", "warn")
        d = self.calib_dir()
        os.makedirs(d, exist_ok=True)
        now = time.time()
        fn = f"eeprom-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{int(now * 1000) % 1000:03d}.json"
        _save_json(os.path.join(d, fn), {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "servos": rows})
        return fn, rows

    def probe_offset_sign(self, i):
        """探一次偏移的符号方向：偏移 +1，看读数往哪边动（只动 1 步 0.09°，在校准窗口之外做）。"""
        if self.offset_sign is not None:
            return self.offset_sign
        try:
            o0, r0 = self.read_off(i), self.read_pos(i)
            if abs(o0 + 1) > OFFSET_LIMIT:
                return None
            self.bus.unlock(i)
            self.write_off(i, o0 + 1)
            self.wait_off_change(i, o0)
            r1 = self.read_pos(i)
            self.write_off(i, o0)
            self.wait_off_change(i, o0 + 1)
            self.bus.lock(i)
            if r1 == r0:
                self.log(f"偏移符号探针：#{i} 偏移 +1 读数没变（{r0}），方向未知，兜底时两个方向都试", "校准", "warn")
                return None
            self.offset_sign = 1 if r1 > r0 else -1
            self.log(f"偏移符号探针：#{i} 偏移 +1 → 读数 {r0}→{r1}，即「读数 = 原始 {'+' if self.offset_sign > 0 else '−'} 偏移」", "校准")
            return self.offset_sign
        except Exception as e:  # noqa: BLE001
            self.log(f"偏移符号探针失败：{type(e).__name__}: {e}", "校准", "warn")
            return None

    def calibrate_by_offset(self, i, target, trail):
        """兜底：舵机连 0x0B 也不认时，自己算偏移写寄存器 31。没成功就把原偏移写回去。"""
        o0, r0 = self.read_off(i), self.read_pos(i)
        signs = [self.offset_sign] if self.offset_sign else [1, -1]
        ok, pos, off = False, r0, o0
        try:
            for sign in signs:
                cand = o0 + sign * (target - r0)
                if abs(cand) > OFFSET_LIMIT:
                    trail.append(f"偏移{cand:+d}超量程跳过")
                    continue
                self.write_off(i, cand)
                off, _ = self.wait_off_change(i, off)
                pos = self.read_pos(i)
                trail.append(f"写偏移{cand}→读数{pos}")
                if abs(pos - target) <= CALIB_TOLERANCE:
                    ok = True
                    return True, pos, off
            return False, pos, off
        finally:
            if not ok and off != o0:
                try:
                    self.write_off(i, o0)
                    off, _ = self.wait_off_change(i, off)
                    trail.append(f"没成，偏移已还原成{o0}")
                except Exception as e:  # noqa: BLE001
                    self.log(f"#{i} 兜底失败后还原偏移也失败了：{type(e).__name__}: {e}", "校准", "error")

    def calibrate_one(self, i, before, target=2048):
        """一颗舵机的位置校准：让它「现在这个位置」的读数变成 target（2048 = 官方零位）。
        判据不看应答，看偏移寄存器变没变（HD-1910 不认的指令也回「成功」）。
        关扭矩期间关节被重力压下去：下垂 d = pre − target + 符号 × Δ偏移，算出来补偿。
        收尾永远把目标写成当前读数（不写 target，判断错了也不会拉关节），扭矩复原，加锁，恢复速度。"""
        bus = self.bus
        was_on = before.get("torque") == 1
        old_off = before.get("offset")
        trail, step, ok, pre, pos, off, why = [], "限速", False, None, None, None, ""
        method, droop = "0x0B", 0
        gains_bad = False             # 恢复扭矩前写 P/D/限力没写进去：收尾也不再恢复
        try:
            bus.write_u8(i, 41, 10)                                    # 万一判断错了，关节也是慢慢走
            bus.write_u16(i, 46, enc_sm(200))
            step = "解锁"
            trail.append("解锁" + _ack(bus.unlock(i)))
            step = "读当前位置"
            pre = self.read_pos(i)
            o_before = self.read_off(i)
            if was_on:
                step = "关扭矩"
                bus.write_u8(i, 40, 0)
                if bus.read_u8(i, 40) != 0:                           # 这包丢了的话 0x0B 会在扭矩开着时执行
                    bus.write_u8(i, 40, 0)
                    if bus.read_u8(i, 40) != 0:
                        raise feetech.BusError("关扭矩没生效（回读不是 0），不敢往下做")
                trail.append("关扭矩✓")
            # 关扭矩以后、0x0B 之前再读一次：偏移方向没探出来时（扭矩开着探不出：舵机会顶回目标，读数不动），
            # 拿「这一刻的读数 → 0x0B 以后的读数」跟偏移变化对一下，就知道方向（10-19 二号鸭 #24 补反了三次）
            post = self.read_pos(i) if was_on else pre
            step = "0x0B 位置校准"
            trail.append(f"0x0B→{target}" + _ack(bus.calibrate_to(i, target)))
            step = "确认偏移变了"
            off, changed = self.wait_off_change(i, o_before)
            pos = self.read_pos(i)
            ok = changed or abs(pre - target) <= CALIB_TOLERANCE
            if not ok:
                step = "兜底：写偏移寄存器 31"
                trail.append("0x0B 没让偏移变，改用写偏移")
                ok, pos, off = self.calibrate_by_offset(i, target, trail)
                method = "写偏移31"
            if ok:
                step = "算下垂并补偿"
                sign = self.offset_sign
                if sign is None:
                    sign = infer_offset_sign(post, pos, off - o_before)
                    if sign is not None:
                        self.offset_sign = sign
                        trail.append(f"偏移方向由这次校准推出：读数 = 原始 {'+' if sign > 0 else '−'} 偏移")
                if sign is None:
                    # 方向不知道就不补：猜错了会把误差翻一倍（以前默认 +1，HD-1910 实际是 −）
                    droop = 0
                    if abs(pre - pos) > DROOP_TOLERANCE:
                        trail.append("偏移方向推不出来，没补下垂（校完的零位可能差几步：关扭矩、手扶到位再校一次）")
                else:
                    droop = pre - target + sign * (off - o_before)
                if abs(droop) > DROOP_TOLERANCE and abs(off - sign * droop) <= OFFSET_LIMIT:
                    self.write_off(i, off - sign * droop)
                    off, _ = self.wait_off_change(i, off)
                    pos = self.read_pos(i)
                    trail.append(f"关扭矩期间垂了{droop:+d}步，偏移再补{-sign * droop:+d}")
                elif abs(droop) > DROOP_TOLERANCE:
                    trail.append(f"垂了{droop:+d}步但补偿超量程，没补")
            step = "写目标"
            trail.append(f"目标{pos}" + _ack(bus.write_u16(i, 42, enc_sm(pos))))
            if was_on and not self.s.halted:
                step = "写 P/D/限力并开扭矩"                          # 上力前先写主程序的 P/D/限力，写不进去就不开（用户 10-19）
                gains_bad = not self._gains_then_torque(i, trail)
            if not ok:
                why = f"0x0B 和写偏移都没让偏移变（还是 {fmt_off(o_before)}），读数 {pos} 不是 {target}"
        except Exception as e:  # noqa: BLE001
            ok = False
            why = f"卡在「{step}」：{type(e).__name__}: {e}"
            trail.append("✗" + why)
            self.log(f"#{i} {why}", "校准", "error")
        finally:
            try:
                p = self.read_pos(i)
                bus.write_u16(i, 42, enc_sm(p))
                # 卸力锁住了就不恢复扭矩（servo-web 这里会恢复，审查 S4：中途按卸力会被重新上力）
                if was_on and not self.s.halted and not gains_bad and bus.read_u8(i, 40) != 1:
                    self._gains_then_torque(i, trail, "扭矩已恢复")      # P/D/限力写不进去就不恢复
            except Exception as e:  # noqa: BLE001
                trail.append(f"✗收尾（写目标/恢复扭矩）：{type(e).__name__}: {e}")
                self.log(f"#{i} 收尾失败，这颗可能卸着力、目标寄存器指向旧位置：{type(e).__name__}: {e}", "校准", "error")
            try:
                bus.lock(i)
                locked = bus.read_u8(i, 55)
                trail.append("加锁✓" if locked == 1 else f"加锁后锁标志读回 {locked}")
            except Exception as e:  # noqa: BLE001
                trail.append(f"✗加锁：{type(e).__name__}: {e}")
                self.log(f"#{i} 加锁失败，舵机可能还是解锁状态：{type(e).__name__}: {e}", "校准", "error")
            try:
                self.release_speed([i])
            except Exception:  # noqa: BLE001
                pass
        gap = "" if pre is None else f"，差 {(target - pre) * 360 / 4096:+.1f}°"
        head = (f"#{i} 读数 {pre if pre is not None else '?'}→{pos if pos is not None else '?'}（目标 {target}{gap}，{method}）"
                f" 偏移 {fmt_off(old_off)}→{fmt_off(off)}" + (f" 下垂{droop:+d}步" if droop else "")
                + f" 扭矩原来{'开' if was_on else '关'}")
        self.log(head + " · " + " ".join(trail) + ("" if ok else f" · 失败：{why}"), "校准", "info" if ok else "warn")
        return {"ok": ok, "pre": pre, "pos": pos, "target": target, "droop": droop, "method": method, "why": why}

    def calib_plan(self, ids):
        """校准前给页面看：每颗现在读多少、要写成多少（官方零位 2048）、差几度。只读。"""
        st = self.bus.states(ids)
        rows = []
        for i in ids:
            s = st.get(i)
            pos = s["pos"] if s else None
            rows.append({"id": i, "joint": JOINT.get(i), "pos": pos, "target": 2048,
                         "diff_deg": None if pos is None else round((2048 - pos) * 360 / 4096, 1)})
        return {"name": "官方零位 2048", "rows": rows, "big_deg": CALIB_BIG_DEG, "sim": self.s.sim}

    def calibrate(self, ids):
        """把选中舵机「现在的位置」校准成 2048（官方零位）。一颗一颗来，每颗只关几毫秒扭矩。
        校完成功的那几颗走「对准再上力」保持住（用户要求）。整段不让状态轮询抢总线。"""
        self.guard()
        ids = [i for i in ids if i in IDS]
        if not ids:
            raise Refused("没选要校准的舵机")
        self.stop(quiet=True)
        with self.calib_lock:
            res = self._calibrate(ids)
        if res["ok"] and not self.s.halted:
            res["hold"] = self.torque_on_aligned(res["ok"])
        self.s.refresh_static(ids)
        return res

    def _calibrate(self, ids):
        sim = self.s.sim
        self.log(f"开始校准 {len(ids)} 颗到官方零位 2048：{ids}" + ("（模拟：只改虚拟舵机，备份放临时目录，存的姿态不挪）" if sim else ""), "校准")
        fn, before = self.backup_calibration(ids)
        self.log(f"原来的偏移、读数、扭矩、锁标志备份在 {self.calib_dir()}{os.sep}{fn}", "校准")
        self.probe_offset_sign(next((i for i in ids if "offset" in before.get(str(i), {})), ids[0]))
        ok, bad, deltas, big = [], {}, {}, {}
        for i in ids:
            if self.s.latched:
                bad[i] = "卸力锁住了，后面的没校"
                continue
            if self.s.closing:
                bad[i] = "在交还主程序，后面的没校"
                continue
            b = before.get(str(i), {})
            if "offset" not in b:
                bad[i] = "备份时读不到，没动它"
                continue
            r = self.calibrate_one(i, b, 2048)
            if r["ok"]:
                ok.append(i)
                if not sim:
                    try:
                        from .commit import record_centered
                        record_centered(self.state_dir, [i], self.s.by)
                    except OSError as e:
                        self.log(f"#{i} 零位校好了，但没记进 Studio 的清单：{e}", "校准", "warn")
                deltas[str(i)] = r["target"] - r["pre"]
                if abs(deltas[str(i)]) * 360 / 4096 > CALIB_BIG_DEG:
                    big[i] = deltas[str(i)] * 360 / 4096
            else:
                bad[i] = r["why"]
        # 先把「打算挪哪些姿态」落盘再真挪：反过来的话，中间崩了就再也挪不回去了
        bp = os.path.join(self.calib_dir(), fn)
        rec = _load_json(bp)
        plan = [p["file"] for p in self.list_poses() if p.get("goals")] if not sim else []
        rec.update({"ref": None, "targets": {str(i): 2048 for i in ids}, "deltas": deltas, "shifted": plan,
                    "bad": {str(k): v for k, v in bad.items()}, "sim": sim})
        _save_json(bp, rec)
        moved = self.shift_saved_poses(deltas, only=set(plan)) if not sim else []
        if moved != plan:
            rec["shifted"] = moved
            _save_json(bp, rec)
        if moved:
            self.log(f"存的姿态换算到新零点：{moved}", "姿态")
        if big:
            self.log(f"这几颗摆好的位置离零位差 {CALIB_BIG_DEG}° 以上：" + " ".join(f"#{i} {d:+.1f}°" for i, d in big.items())
                     + "。装舵盘时转了半圈没关系；要是方向 ± 设反了，这次校进去的零点是错的：上力用滑块推一点，"
                     "看 3D 里这个关节跟实物转的方向是不是一致，不一致就撤销、改 ±、重校", "校准", "warn")
        self.log(f"校准完：成功 {len(ok)} 颗 {ok}" + (f"；失败 {len(bad)} 颗 {sorted(bad)}" if bad else "")
                 + f"；备份 {fn}", "校准", "warn" if bad or big else "info")
        return {"ok": ok, "bad": {str(k): v for k, v in bad.items()}, "backup": fn,
                "big": {str(k): round(v, 1) for k, v in big.items()}}

    def undo(self):
        """把最近一次备份里的偏移写回去（关扭矩 → 解锁 → 写 31 → 加锁），目标改成当前读数，扭矩状态照旧。"""
        self.guard()
        self.stop(quiet=True)
        with self.calib_lock:
            res = self._undo()
        self.s.refresh_static([int(k) for k in res.get("ok", [])])
        return res

    def _undo(self):
        bus = self.bus
        d = self.calib_dir()
        files = sorted(f for f in os.listdir(d) if f.startswith("eeprom-")) if os.path.isdir(d) else []
        if not files:
            raise Refused("没有可撤销的中位校准" + ("（模拟模式只撤销模拟里做的校准）" if self.s.sim else ""))
        fn = files[-1]
        rec = _load_json(os.path.join(d, fn))
        self.log(f"开始撤销：把 {fn}（{rec.get('time', '?')}）里的偏移写回去", "校准")
        ok, bad = [], {}
        for k, row in rec["servos"].items():
            i = int(k)
            if "offset" not in row:
                self.log(f"#{i} 备份里没有偏移（{row.get('error', '?')}），跳过", "校准", "warn")
                continue
            trail, step, on = [], "读当前状态", False
            gains_bad = False         # 恢复扭矩前写 P/D/限力没写进去：收尾也不再恢复
            try:
                on = bus.read_u8(i, 40) == 1
                off0, p0 = bus.read_u16(i, 31), self.read_pos(i)
                if on:
                    step = "关扭矩"
                    bus.write_u8(i, 40, 0)
                    if bus.read_u8(i, 40) != 0:
                        bus.write_u8(i, 40, 0)
                        if bus.read_u8(i, 40) != 0:
                            raise feetech.BusError("关扭矩没生效（回读不是 0），不敢写偏移")
                    trail.append("关扭矩✓")
                step = "解锁"
                trail.append("解锁" + _ack(bus.unlock(i)))
                step = "写偏移"
                trail.append(f"写偏移{fmt_off(row['offset'])}" + _ack(bus.write_u16(i, 31, row["offset"])))
                step = "读回"
                self.wait_off_change(i, feetech.sign15(off0))
                off1, p1 = bus.read_u16(i, 31), self.read_pos(i)
                step = "写目标"
                trail.append(f"目标{p1}" + _ack(bus.write_u16(i, 42, enc_sm(p1))))
                if on and not self.s.halted:
                    step = "写 P/D/限力并开扭矩"                      # 上力前先写主程序的 P/D/限力，写不进去就不开（用户 10-19）
                    gains_bad = not self._gains_then_torque(i, trail)
                good = off1 == row["offset"]
                if good:
                    ok.append(i)
                else:
                    bad[i] = f"偏移写了 {row['offset']} 读回 {off1}"
                self.log(f"#{i} 偏移 {fmt_off(off0)}→{fmt_off(off1)}（备份值 {fmt_off(row['offset'])}） 读数 {p0}→{p1} · "
                         + " ".join(trail), "校准", "info" if good else "warn")
            except Exception as e:  # noqa: BLE001
                bad[i] = f"卡在「{step}」：{type(e).__name__}: {e}"
                self.log(f"#{i} 撤销{bad[i]}", "校准", "error")
            finally:
                try:
                    p = self.read_pos(i)
                    bus.write_u16(i, 42, enc_sm(p))
                    if on and not self.s.halted and not gains_bad and bus.read_u8(i, 40) != 1:
                        self._gains_then_torque(i, trail, "扭矩已恢复")  # P/D/限力写不进去就不恢复
                except Exception as e:  # noqa: BLE001
                    self.log(f"#{i} 撤销收尾失败，这颗可能卸着力：{type(e).__name__}: {e}", "校准", "error")
                try:
                    bus.lock(i)
                except Exception as e:  # noqa: BLE001
                    self.log(f"#{i} 加锁失败：{type(e).__name__}: {e}", "校准", "error")
        if not rec.get("sim") and "shifted" in rec:
            later, t0 = [], rec.get("time", "")
            for p in self.list_poses():
                if p["file"] not in rec["shifted"] and p.get("goals") and (p.get("time") or "") >= t0[:16]:
                    later.append(p["file"])
            moved = self.shift_saved_poses(rec.get("deltas", {}), sign=-1, only=set(rec["shifted"]) | set(later))
            if moved:
                self.log(f"存的姿态换算回原来的零点：{moved}", "姿态")
        if ok and not rec.get("sim"):
            try:
                from .commit import forget_centered
                gone = forget_centered(self.state_dir, ok)
                if gone:
                    self.log(f"撤销成功的 {len(gone)} 颗也从「都已校过中位」的记录里删掉了：{gone}", "校准")
            except OSError as e:
                self.log(f"撤销成功的颗没能从 Studio 的中位记录里删掉：{type(e).__name__}: {e}", "校准", "warn")
        os.rename(os.path.join(d, fn), os.path.join(d, "undone-" + fn))
        self.log(f"撤销完（{fn} 已改名 undone-{fn}）：{len(ok)} 颗偏移写回" + (f"；失败 {sorted(bad)}" if bad else ""),
                 "校准", "warn" if bad else "info")
        return {"ok": ok, "bad": {str(k): v for k, v in bad.items()}, "backup": fn}

    # ---- 姿态 ----

    def list_poses(self):
        out = []
        d = self.poses_dir()
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".json"):
                    try:
                        p = _load_json(os.path.join(d, fn))
                        out.append({"file": fn, "name": p.get("name", fn[:-5]), "time": p.get("time", ""),
                                    "goals": {r["id"]: r["pos"] for r in p.get("rows", [])}})
                    except Exception as e:  # noqa: BLE001
                        self.log(f"姿态文件 {fn} 读不了：{type(e).__name__}: {e}", "姿态", "warn")
        return out

    def save_pose(self, name):
        st = self.bus.states(self.present())
        rows = [{"id": i, "joint": JOINT.get(i), "pos": s["pos"], "deg": round((s["pos"] - 2048) * 360 / 4096, 1)}
                for i, s in st.items() if s]
        os.makedirs(self.poses_dir(), exist_ok=True)
        safe = "".join(c for c in str(name) if c not in '\\/:*?"<>|').strip()[:40] or "pose"
        fn = f"{safe}-{time.strftime('%Y-%m-%d')}.json"
        _save_json(os.path.join(self.poses_dir(), fn), {"name": safe, "time": time.strftime("%Y-%m-%d %H:%M"), "rows": rows})
        missing = [i for i, v in st.items() if not v]
        self.log(f"姿态已存 {fn}（{len(rows)} 颗）" + (f"；读不到 {missing}，没存进去" if missing else ""), "姿态",
                 "warn" if missing else "info")
        return {"file": fn}

    def delete_pose(self, fn):
        """不真删：挪进回收站，文件名前加时间，删错了能拿回来。"""
        fn = str(fn or "")
        if not fn or os.path.basename(fn) != fn or not fn.endswith(".json"):
            raise Refused(f"不认识的姿态文件 {fn!r}")
        src = os.path.join(self.poses_dir(), fn)
        if not os.path.isfile(src):
            raise Refused(f"没有这个姿态：{fn}")
        trash = os.path.join(self.poses_dir(), ".trash")
        os.makedirs(trash, exist_ok=True)
        os.replace(src, os.path.join(trash, f"{time.strftime('%Y%m%d-%H%M%S')}-{fn}"))
        self.log(f"姿态已删除：{fn}（挪到回收站，要找回就挪回来）", "姿态")
        return {}

    def go_pose(self, fn, seconds, mode, acc):
        p = next((x for x in self.list_poses() if x["file"] == fn), None)
        if not p:
            raise Refused(f"没有这个姿态：{fn}")
        self.log(f"去姿态「{p['name']}」（{seconds:g} 秒）", "运动")
        return self.move(p["goals"], seconds, mode, acc)

    def shift_saved_poses(self, deltas, sign=1, only=None):
        """重新校准后，同一个物理姿势的读数变了 delta：存的姿态跟着挪。"""
        changed = []
        d = self.poses_dir()
        if not os.path.isdir(d) or not any(deltas.values()):
            return changed
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or (only is not None and fn not in only):
                continue
            path = os.path.join(d, fn)
            pose = _load_json(path)
            hit = False
            for r in pose.get("rows") or []:
                dl = deltas.get(str(r.get("id")))
                if dl:
                    r["pos"] = int(r["pos"]) + sign * int(dl)
                    r["deg"] = round((r["pos"] - 2048) * 360 / 4096, 1)
                    hit = True
            if hit:
                _save_json(path, pose)
                changed.append(fn)
        return changed

    # ---- 总线工具 ----

    def scan(self, ids):
        found = [i for i in ids if self.bus.ping(i) is not None]
        lost = [i for i in ids if i not in found] if len(ids) <= 32 else []
        self.log(f"扫描 {ids[0]}..{ids[-1]}：在线 {len(found)} 颗 {found}" + (f"；不在线 {lost}" if lost else ""),
                 "总线", "warn" if lost else "info")
        return {"found": found, "lost": lost}

    def dump(self, sid):
        d = self.bus.dump(int(sid))
        r = d["raw"]
        self.log(f"导出 #{sid}：固件 {r[0]}.{r[1]} 舵机 {r[3]}.{r[4]} 模式={r[33]} 相位={r[18]} P/D/I={r[21]}/{r[22]}/{r[23]}"
                 f"（运行中 {r[50]}/{r[51]}/{r[52]}，限力 {r[48] | r[49] << 8}） "
                 f"偏移={fmt_off(r[31] | r[32] << 8)} 扭矩={r[40]} 锁={r[55]} 应答级别={r[8]}", "寄存器")
        return {"id": int(sid), **d}

    def write_reg(self, sid, addr, size, value, unlock):
        self.guard()
        sid, addr, size, value = int(sid), int(addr), int(size), int(value)
        if addr == 40:
            raise Refused("扭矩开关用页面上的开关，别在这里写")
        if size not in (1, 2) or not 0 <= addr <= 86:
            raise Refused("地址 0–86，字节 1 或 2")
        bus = self.bus
        name = feetech.REGISTERS.get(addr, ("?",))[0]
        rd = bus.read_u8 if size == 1 else bus.read_u16
        old = rd(sid, addr)
        if unlock:
            bus.unlock(sid)
        try:
            err = (bus.write_u8 if size == 1 else bus.write_u16)(sid, addr, value)
        finally:
            if unlock:
                bus.lock(sid)
        new = rd(sid, addr)
        same = new == value & (0xFF if size == 1 else 0xFFFF)
        self.log(f"#{sid} 写 {addr}「{name}」{old} → {value}{_ack(err)}，读回 {new}" + ("（解锁写入，已重新加锁）" if unlock else "")
                 + ("" if same else "（读回跟写的不一样：只读？超范围？）"), "寄存器", "info" if same else "warn")
        touched = sorted(set(range(addr, addr + size)) & set(GAIN_REGS))
        if touched:
            # 手动改 P / D / 限力：高级用法照旧允许，页头写「手动改过，跟主程序不一样」（用户 10-19）
            self.manual.setdefault(sid, {})[addr] = new
            g = self.gains.get(max_age=GAINS_RECHECK_S)
            eeprom = all(a in (21, 22) for a in touched)
            self.log(f"#{sid} 手动改了{'、'.join(GAIN_REGS[a] for a in touched)}（寄存器 {addr} = {new}），跟主程序"
                     f"（P{g['kp']} · D{g['kd']} · 限力 {g['limit']}）不一样"
                     + ("：这是 EEPROM 里存的，上电后才用；Studio 上力时照样写运行中的（50 / 51 / 48）" if eeprom else
                        "：上着力就一直这样；这颗下次从松开到上力时，Studio 会写回主程序的值"), "寄存器", "warn")
        self.s.refresh_static([sid])
        return {"old": old, "new": new, "same": same}

    def reboot(self, sid):
        sid = int(sid)
        self.bus.write_u8(sid, 40, 0)
        self.bus.reboot(sid)
        self.log(f"#{sid} 重启 0x08（先关了扭矩，约 0.8 s 后回来）", "寄存器")
        return {}

    def set_id(self, old, new):
        self.guard()
        old, new = int(old), int(new)
        if not 0 <= new <= 253:
            raise Refused("新 ID 要在 0–253")
        if new != old and self.bus.ping(new) is not None:
            raise Refused(f"ID {new} 已经有舵机在用，会撞号")
        ok = self.bus.set_id(old, new)
        self.log(f"改 ID {old} → {new}：" + ("新 ID ping 得通" if ok else "新 ID ping 不通"), "寄存器", "info" if ok else "error")
        return {"ok": ok}

    def timing(self, n=200):
        """sync_read 全部在线舵机 n 次，看耗时和失败。"""
        ids = self.present()
        times, fails = [], 0
        with self.calib_lock:                      # 测的时候轮询让开，测出来的才是总线本身
            for _ in range(int(n)):
                t = time.perf_counter()
                r = self.bus.sync_read(ids, 56, 15)
                times.append((time.perf_counter() - t) * 1000)
                fails += sum(1 for v in r.values() if v is None)
        times.sort()
        res = {"n": n, "ids": len(ids), "min_ms": round(times[0], 2), "avg_ms": round(sum(times) / n, 2),
               "p99_ms": round(times[max(0, int(n * 0.99) - 1)], 2), "max_ms": round(times[-1], 2), "fails": fails}
        self.log(f"时序测试：{res}", "总线", "warn" if fails else "info")
        return res

    def set_dir(self, sid, d):
        sid, d = int(sid), (-1 if int(d) < 0 else 1)
        self.s.directions[sid] = d
        path = os.path.join(self.state_dir, "servo-directions.json")
        os.makedirs(self.state_dir, exist_ok=True)
        _save_json(path, {str(k): v for k, v in self.s.directions.items()})
        self.log(f"方向 #{sid} → {d:+d}（已存 servo-directions.json）", "方向")
        return {"dirs": {str(k): v for k, v in self.s.directions.items()}}

    # ---- 页面来的指令 ----

    def handle(self, args, tok=None):
        """页面的指令（在线程里跑）。返回给页面的 dict；不能做就抛 Refused（原因给页面看）。
        进出都记账：交还 / 离开要等在路上的做完，正在交还时新来的直接拒（审查 D2）。app 在事件循环里已经记上了（tok），
        这里就不再记；直接调的（测试）这里记。记上了的照常做完——交还 / 离开等它做完以后再当场读扭矩。"""
        if tok is not None:
            return self._handle(args)
        with self.s.op_slot(args.get("op")):
            return self._handle(args)

    def _handle(self, args):
        op = args.get("op")
        sec = float(args.get("seconds", 2))
        mode = "stream" if args.get("mode") == "stream" else "profile"
        acc = int(args.get("acc", 30))
        if op == "torque":
            ids = [int(i) for i in (args.get("ids") or [])] or self.present()
            if int(args.get("on", 0)) and self.s.latched and self.s.closing:
                raise Refused("正在交还主程序：卸力锁着，不许再上力")       # 交还时卸的力不许被手动上力清掉（审查 D2）
            if int(args.get("on", 0)) and self.s.latched:
                # 卸力以后，人自己点「扭矩开 / 保持」就算解除（后台的过渡、灵动、校准收尾仍然不会自己上力）
                self.s.latched = False
                self.log("手动上力：解除卸力锁定", "安全")
            return self.torque_on_aligned(ids) if int(args.get("on", 0)) else self.torque_off(ids)
        if op == "goal":
            return self.goal(args["id"], args["pos"])
        if op == "move":
            return self.move(args.get("goals") or {}, sec, mode, acc)
        if op == "stop":
            return self.stop()
        if op == "official":
            return self.go_official(args.get("key"), sec, mode, acc)
        if op == "idle":
            return self.idle(bool(args.get("on")))
        if op == "calib_plan":
            return self.calib_plan(parse_ids(args.get("ids") or "20-24,30-34,10-14"))
        if op == "calibrate":
            return self.calibrate(parse_ids(args.get("ids") or "20-24,30-34,10-14"))
        if op == "undo":
            return self.undo()
        if op == "poses":
            return {"poses": self.list_poses()}
        if op == "save_pose":
            self.save_pose(args.get("name") or "pose")
            return {"poses": self.list_poses()}
        if op == "delete_pose":
            self.delete_pose(args.get("file"))
            return {"poses": self.list_poses()}
        if op == "go_pose":
            return self.go_pose(args.get("file"), sec, mode, acc)
        if op == "scan":
            return self.scan(parse_ids(args.get("ids") or "20-24,30-34,10-14"))
        if op == "scan_all":
            return self.scan(list(range(0, 254)))
        if op == "dump":
            return self.dump(args["id"])
        if op == "write_reg":
            return self.write_reg(args["id"], args["addr"], args.get("size", 1), args["value"], bool(args.get("unlock")))
        if op == "reboot":
            return self.reboot(args["id"])
        if op == "set_id":
            return self.set_id(args["old"], args["new"])
        if op == "timing":
            return self.timing(int(args.get("n", 200)))
        if op == "set_dir":
            return self.set_dir(args["id"], args["dir"])
        if op == "unlatch":
            self.s.latched = False
            self.log("解除卸力锁定：可以再上力了", "安全")
            return {}
        if op == "info":
            return {"danger": {str(k): v for k, v in DANGER.items()},
                    "registers": {str(k): v for k, v in feetech.REGISTERS.items()},
                    "official": {k: v[0] for k, v in OFFICIAL.items()}}
        raise Refused(f"不认识的指令：{op}")


def _save_json(path, obj):
    """先写 .tmp 再改名：写一半崩了不会留半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
