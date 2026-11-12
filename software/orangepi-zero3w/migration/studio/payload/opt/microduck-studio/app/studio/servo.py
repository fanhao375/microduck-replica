"""舵机页的总线会话：Studio 自己打开舵机总线（跟 servo-web 一样），读 15 颗、在总线上卸力。

这一步（舵机页 4a）**只读**：除了卸力，不往舵机写任何东西。保持、设中位在后面几步加。

为什么要自己开总线：robotd 两个版本都做不到「单颗上力并一直保持」（官方一次写 15 颗；
原版板端固件的 move 走到位就卸力），见 scratchpad console/servo-page-plan.md。所以舵机页跟 servo-web
一样，robotd 停着的时候由 Studio 直接跟舵机说话。**总线只有这一个入口：ServoSession.open()。**

线程：飞特总线是阻塞读写（pyserial），轮询放在一个线程里跑（50 Hz），结果放在 snapshot 里，
网页那边 10 Hz 取最新的。卸力不排队：直接在调用它的线程里发（总线每一包有锁，跟轮询穿插），
最多等轮询正在收的那一包（几毫秒）。

卸力 = 锁存的急停（方案 1.3、审查 S4）：发 sync_write 40=0 → sync_read 40 回读 → 没变 0 的逐颗重写，
最多三轮。锁存以后，以后加的「上力、写目标、写零位」一律先查 latched，要人点「解除」才行。

交还 / 离开（第三批代码审查 D2、D3）：舵机操作（servo_ops.handle）在线程里跑，交还跟「刚点的保持」会并发——
同一个包里先到「保持」后到「离开」，40 次里 35 次交还以后舵机还上着力。所以：
- 每个舵机操作进出都记账（op_begin / op_end，app 在事件循环里就先记上，线程还没起来也算在路上）；
- 交还 / 离开先置 closing（之后来的舵机操作一律拒，手动上力也不许清卸力锁存），等在路上的做完（带超时，超时不交还），
  再当场读一次扭矩（不用 1 秒一次的缓存），再卸力、关总线；
- 离开（leave）：校准 / 运动 / 灵动在跑不交还；15 颗扭矩都当场读到、都是 0 才交还，读不到的当它可能还在保持。
"""
import contextlib
import json
import os
import subprocess
import sys
import threading
import time

from .bus import feetech
from .startup_status import servo_observations, sampled_joint_feedback

IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]     # 跟 robotd 的 JOINT_NAMES 同一个顺序
NAMES = {
    20: ("左髋偏航", "Left Hip Yaw"), 21: ("左髋横滚", "Left Hip Roll"), 22: ("左髋俯仰", "Left Hip Pitch"),
    23: ("左膝", "Left Knee"), 24: ("左踝", "Left Ankle"),
    30: ("颈部俯仰", "Neck Pitch"), 31: ("头部俯仰", "Head Pitch"), 32: ("头部偏航", "Head Yaw"),
    33: ("头部横滚", "Head Roll"), 34: ("嘴巴", "Mouth"),
    10: ("右髋偏航", "Right Hip Yaw"), 11: ("右髋横滚", "Right Hip Roll"), 12: ("右髋俯仰", "Right Hip Pitch"),
    13: ("右膝", "Right Knee"), 14: ("右踝", "Right Ankle"),
}
GROUP = {**{i: "R" for i in (10, 11, 12, 13, 14)}, **{i: "L" for i in (20, 21, 22, 23, 24)},
         **{i: "H" for i in (30, 31, 32, 33, 34)}}
# HD-1910 读数变大的方向跟模型（XL330）相反，servo-web 2026-09-20 实机逐颗核对过：15 颗全是 −1
DEFAULT_DIRECTIONS = {i: -1 for i in IDS}
POLL_HZ = 50
TORQUE_EVERY_S = 1.0          # 扭矩开关单独读，一秒一次
STATIC_REGS = {13: "temp_limit", 21: "p", 22: "d", 33: "mode", 8: "resp_level", 19: "unload",
               50: "p_run", 51: "d_run"}    # 21/22 是 EEPROM 里存的；HD-1910 运行中的 P/D 在 RAM 50/51（servo_ops 上力前写这里）
BATTERY_FULL_V, BATTERY_EMPTY_V = 8.2, 6.6      # 官方 duck-control/src/model.rs，同一个算法
LEAVE_WAIT_S = 3.0            # 离开舵机页：等在路上的舵机操作做完最多这么久（等不到就不交还）
HANDBACK_WAIT_S = 10.0        # 点「交还主程序」：同上（校准看到 closing 会在下一颗前停）
# 这几样在跑 = 「活还没干完」：离开舵机页不交还（第三批代码审查 D3a：一键校准跑着时离开，后面几颗被卸力锁打断）
BUSY_OPS = {"calibrate": "正在校准", "undo": "正在撤销校准", "timing": "正在测总线时序", "scan_all": "正在扫全网",
            "scan": "正在扫描", "set_id": "正在改 ID", "reboot": "正在重启舵机", "write_reg": "正在写寄存器",
            "tracking": "正在做关节跟踪测试"}


class Refused(Exception):
    """这个操作现在不能做（卸力锁着、总线没开、正在交还、参数不对）。原因直接给页面看。servo_ops 也用这一个。"""


def angle_deg(raw, direction):
    return None if raw is None else direction * (raw - 2048) * 360.0 / 4096


def battery_percent(volts):
    if not volts or volts <= 0:
        return None
    return max(0.0, min(1.0, (volts - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V))) * 100


def robotd_active():
    """robotd 在跑吗？在电脑上（没有 systemctl）当成没在跑；Linux 上 systemctl 出错 / 超时回 None（查不了）。"""
    if not sys.platform.startswith("linux"):
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", "robotd"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() in ("active", "activating", "reloading")
    except (OSError, subprocess.TimeoutExpired):
        return None


def boot_id():
    """这次开机的编号：读 /proc/sys/kernel/random/boot_id；读不到（Windows、没这个文件）回 None。"""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


BASELINE_PREFIX = "servo-baseline-"
_baseline_mem = {}       # boot_id 读不到时（电脑上、演示）基线只记在这个进程里：(state_dir, 是否演示) -> {id: {"41","46"}}


class ServoSession:
    def __init__(self, log, port=None, sim=False, directions=None, baud=1_000_000, state_dir="."):
        self.log, self.port, self.sim, self.baud, self.state_dir = log, port, sim, baud, state_dir
        self.tracking = None             # 关节跟踪：build_app 挂上 TrackingController
        self.ops = None                  # servo_ops.ServoOps：总线开着时才有
        self.directions = {**DEFAULT_DIRECTIONS, **(directions or {})}    # 键是 int：dict(..., **d) 会因为键不是字符串而崩（10-19 二号鸭存过方向后 Studio 起不来）
        self.bus = None
        self.state = "closed"            # closed / opening / open / error
        self.error = None
        self.latched = False             # 卸力锁存
        self.relaxing = False
        self.last_relax = None           # {"ok", "ms", "bad", "at"}
        self.static = {}                 # id -> {temp_limit, p, d, mode, ...}
        self.rows = {}                   # id -> 最新一次读到的状态
        self.torque = {}                 # id -> 0/1/None
        self.torque_at = None
        self.hz = 0.0
        self.opened_at = None
        self.by = None
        self._stop = threading.Event()
        self._thread = None
        self._window = []                # 最近几秒的 (时间, asked, missed) 用来算丢帧率
        self.sim_serial = None
        self.handed_back_at = None       # 上一次交还主程序的时间（monotonic）：主程序连上以前页面上一直提示「要手动起来」
        self.closing = False             # 正在交还 / 离开：新的舵机操作一律拒，手动上力也不许清卸力锁存（审查 D2）
        self._cv = threading.Condition()
        self._inflight = {}              # 在路上的舵机操作：token -> 操作名
        self._tok = 0
        self.restored = False            # 这次会话是 Studio 重启后接着管的（审查 D1）
        self.orphan = None               # 上次退出时会话开着、这次没接管成：{"why", "at"}（卸力按钮引导去舵机页接管）
        self.helper_available = None     # 起 / 停 robotd 的 root 小助手可用吗：交还提示「正在把主程序起回来…」（审查 D12）
        self.conflict_at = None          # 会话期间发现 robotd 被手动起来了（monotonic，审查 D9）
        self.probe_robotd = None         # 会话期间查不查 robotd 起没起来：None = 真串口才查（演示舵机配的是假 robotd，一直在）
        self.boot_id = None              # 这次开机第一次接管时记 41/46 基线用的（boot_id() 的结果）
        self.baseline = {}               # id -> {"41": 原始整数, "46": 原始整数}：交还前写回去
        self.handback_note = None        # 交还成功但没确认卸力的颗（页面提示条用；下次接管清掉）
        self.giveback_error = None       # 交还后主程序没起来的原因（提示条变红）
        self.giveback_hint = None        # 主程序没起来时怎么恢复（旗在 / 不在两种）
        self.marker_error = None         # 会话标记没写进去：Studio 重启后会不知道这里还开着

    # ---- 打开 / 关闭 ----

    def open(self, by=None, port=None, restored=False):
        """打开总线（阻塞，调用方放到线程里）。返回 (ok, 原因)。
        port：页面上选的串口（电脑上插 USB 转 TTL 是 COM5 之类，跟 servo-web 一样）；"sim" = 模拟舵机。
        已经打开时不能换串口（换口会先关真总线，舵机保持着急停却卸不到）：必须交还以后再换。
        restored：Studio 重启后接着上次的会话（审查 D1）——跟 state="open" 一起定，
        别人看到「开了」的那一刻就看得到它是接着管的（以前在线程外面后设，中间有个窗口）。"""
        if port:
            want_sim = port == "sim"
            if self.state == "open" and (want_sim != self.sim or (not want_sim and port != self.port)):
                # 调舵机中换口会先把真总线关掉，15 颗还在保持、急停却卸不到：一律不换
                return False, "调舵机中不能换串口：先点「交还主程序」，再换"
            self.sim = want_sim
            if not want_sim:
                self.port = port
        if self.state in ("open", "opening"):
            return True, None
        if not self.sim and not self.port:
            self.state, self.error = "closed", None
            return False, "先选一个串口（电脑上插 USB 转 TTL）或者「演示（没接舵机）」"
        self.state, self.error, self.by = "opening", None, by
        try:
            if self.sim:
                from .bus.sim_bus import SimSerial
                self.sim_serial = SimSerial(IDS, phys=_sim_home())
                self.bus = feetech.FeetechBus(None, ser=self.sim_serial)
                where = "演示（没接舵机，软件里假装有 15 颗）"
            else:
                active = robotd_active()
                if active is None:
                    raise RuntimeError("查不了主程序在不在（systemctl 出错）：为了不跟它抢串口，先不开")
                if active:
                    raise RuntimeError("主程序 robotd 还在用舵机总线。先停主程序（下一步做成网页上一键接管）")
                self.bus = feetech.FeetechBus(self.port, self.baud)
                where = f"{self.port} @ {self.baud}"
            self.log.info(f"打开舵机总线：{where}", cat="总线", raw=f"by={by}")
            found = [i for i in IDS if self.bus.ping(i) is not None]
            missing = [i for i in IDS if i not in found]
            self._read_static(found)
            self._record_baseline(found)
            self._read_torque()
            self.restored = bool(restored)
            self.state, self.opened_at, self.latched = "open", time.monotonic(), False
            from .servo_ops import ServoOps
            self.ops = ServoOps(self, self.state_dir)
            self.ops.read_phase()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="servo-poll", daemon=True)
            self._thread.start()
            msg = f"舵机总线打开了：{len(found)}/15 颗有应答"
            if missing:
                self.log.warn(msg, cat="总线", raw=f"没应答：{missing}", hint="看那颗的线有没有插好、有没有电")
            else:
                self.log.info(msg, cat="总线")
            return True, None
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", f"{type(e).__name__}: {e}"
            self.log.error("打不开舵机总线", cat="总线", raw=self.error,
                           hint="鸭子上要先停主程序；电脑上要接好 USB 转 TTL；端口写对了吗")
            self._close_bus()
            return False, self.error

    def holding(self):
        """现在有几颗上着力（扭矩回读是 1，1 秒一读的缓存：只给页面显示用；交还 / 离开的判断用 read_torque_now）。总线没开是 0。"""
        if self.state != "open":
            return 0
        return sum(1 for i in IDS if self.torque.get(i) == 1)

    def unknown(self):
        """扭矩读不到的有几颗（没应答）：它们可能也在保持。总线没开是 0。"""
        if self.state != "open":
            return 0
        return sum(1 for i in IDS if self.torque.get(i) not in (0, 1))

    @property
    def halted(self):
        """卸力锁着、或者正在交还：后台的过渡、灵动、校准收尾都不许再上力 / 再动。"""
        return self.latched or self.closing

    # ---- 在路上的舵机操作（审查 D2） ----

    def op_begin(self, name):
        """记上一个舵机操作（app 在事件循环里、线程起来之前就调）。正在交还 / 总线没开：抛 Refused。回 token 给 op_end。"""
        with self._cv:
            if self.closing:
                raise Refused("正在交还主程序：舵机操作不收了")
            if self.tracking is not None and self.tracking.active:
                raise Refused("关节跟踪测试进行中：请先停止测试；卸力仍可用")
            if self.state != "open":
                raise Refused("舵机总线没打开：先点「开始调舵机」")
            self._tok += 1
            tok = self._tok
            self._inflight[tok] = str(name or "?")
            return tok

    def op_end(self, tok):
        """做完了（重复调没事）。"""
        with self._cv:
            if self._inflight.pop(tok, None) is not None:
                self._cv.notify_all()

    @contextlib.contextmanager
    def op_slot(self, name):
        tok = self.op_begin(name)
        try:
            yield
        finally:
            self.op_end(tok)

    def wait_ops(self, timeout):
        """等在路上的舵机操作都做完；超时回 False。"""
        with self._cv:
            return self._cv.wait_for(lambda: not self._inflight, timeout)

    def inflight_names(self):
        with self._cv:
            return sorted(set(self._inflight.values()))

    def _busy_locked(self):
        """校准 / 运动 / 灵动在跑：回一句原因；没在跑回 None（拿着 _cv 调）。"""
        for name in self._inflight.values():
            if name in BUSY_OPS:
                return BUSY_OPS[name]
        ops = self.ops
        if ops is not None:
            if ops.calib_lock.locked():
                return "正在校准"
            if ops._motion is not None and ops._motion.is_alive() and not ops._motion_stop.is_set():
                return "动作在跑"
            if ops.idle_running:
                return "灵动在跑"
        return None

    def read_torque_now(self):
        """当场读一次 15 颗的扭矩开关（不用缓存）：{id: 0 / 1 / None（没应答）}。总线出错全是 None。"""
        try:
            got = self._read_torque()
        except Exception as e:  # noqa: BLE001
            self.log.warn("当场读扭矩没读到", cat="总线", raw=f"{type(e).__name__}: {e}")
            return {i: None for i in IDS}
        return {i: (v[1][0] if v else None) for i, v in ((i, got.get(i)) for i in IDS)}

    def leave(self, why, wait_s=LEAVE_WAIT_S, before_close=None):
        """离开舵机页 / 没人在看（阻塞，放线程里）：能交还就卸力、关总线。回 (结果, 详情)：
            ("closed", None)       本来就没开
            ("kept", {"why", "holding", "unknown"})   没交还（活没干完、还有在保持的、读不到的、等不到在路上的操作）
            ("handed_back", None)  卸了力、关了总线（主程序那边的事 app 做）"""
        with self._cv:
            if self.state != "open":
                return "closed", None
            busy = self._busy_locked()
            if busy:
                return "kept", {"why": busy, "holding": self.holding(), "unknown": self.unknown()}
            self.closing = True
        try:
            if not self.wait_ops(wait_s):
                return "kept", {"why": f"还有舵机操作没做完（{'、'.join(self.inflight_names())}）", "holding": self.holding(),
                                "unknown": self.unknown()}
            tq = self.read_torque_now()
            on = [i for i in IDS if tq.get(i) == 1]
            unk = [i for i in IDS if tq.get(i) not in (0, 1)]
            if on or unk:
                parts = ([f"{len(on)} 颗保持着"] if on else []) + ([f"{len(unk)} 颗读不到扭矩（可能也在保持）"] if unk else [])
                return "kept", {"why": "、".join(parts), "holding": len(on), "unknown": len(unk)}
            ok, reason = self.relax_all(why)
            if not ok:
                self.log.warn("交还前卸力没全部确认", cat="安全", raw=reason, hint="主程序启动时还会再卸一次；不放心就关电源")
            bad, _unknown = self._restore_baseline()
            if bad:
                ids = "、".join(f"#{i}" for i in bad)
                msg = f"{len(bad)} 颗加速度/速度没写回（{ids}）：没交还。舵机已经松了；再点一次「交还主程序」重试"
                self.log.error("41/46 基线没写回：没交还", cat="安全", raw=ids, dedup=False, hint=msg)
                return "kept", {"why": msg, "holding": self.holding(), "unknown": self.unknown()}
            if before_close is not None:
                before_close()          # 关总线前把「调舵机」的暂停一起清掉：外面看到 closed 时状态已经完整（审查 D1/D2 同一个时序要求）
            self.close()
            return "handed_back", None
        finally:
            with self._cv:
                self.closing = False

    def hand_back_bus(self, why, wait_s=HANDBACK_WAIT_S, before_close=None):
        """点「交还主程序」（阻塞，放线程里）：置 closing → 停运动 / 灵动 → 等在路上的操作做完 → 卸力回读 → 关总线。
        等不到就不交还，回 (False, 原因)。"""
        with self._cv:
            if self.state != "open":
                return True, None
            self.closing = True
        try:
            if self.ops is not None:
                self.ops.idle_running = False
                self.ops._motion_stop.set()
            if not self.wait_ops(wait_s):
                names = "、".join(self.inflight_names()) or "?"
                return False, f"还有舵机操作没做完（{names}），等了 {wait_s:.0f} 秒：没交还。等它做完再点「交还主程序」"
            ok, reason = self.relax_all(why)
            if not ok:
                self.log.warn("交还前卸力没全部确认", cat="安全", raw=reason, hint="主程序启动时还会再卸一次；不放心就关电源")
            bad, unknown = self._restore_baseline()
            if bad:
                ids = "、".join(f"#{i}" for i in bad)
                msg = f"{len(bad)} 颗加速度/速度没写回（{ids}）：没交还。舵机已经松了；再点一次「交还主程序」重试"
                self.log.error("41/46 基线没写回：没交还", cat="安全", raw=ids, dedup=False, hint=msg)
                return False, msg
            relax_bad = list((self.last_relax or {}).get("bad") or [])
            note_ids = sorted(set(relax_bad) | set(unknown), key=IDS.index)
            note = (f"{'、'.join(f'#{i}' for i in note_ids)} 没确认卸力：主程序起来时会再卸一次；不放心就关电源"
                    if note_ids else None)
            if before_close is not None:
                before_close()          # 关总线前把「调舵机」的暂停一起清掉：外面看到 closed 时状态已经完整
            self.close()
            return True, note
        finally:
            with self._cv:
                self.closing = False

    def close(self):
        """关总线（阻塞）。只关串口，不改舵机状态（Studio 退出、发现 robotd 被手动起来时也走这里：不卸力、不写）。
        交还主程序（先卸力回读再关）走 hand_back_bus / leave。"""
        if self.ops is not None:
            self.ops.idle_running = False
            self.ops._motion_stop.set()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._close_bus()
        self.ops = None
        if self.state != "closed":
            self.log.info("关掉舵机总线", cat="总线", dedup=False)
        self.state = "closed"
        self.rows, self.torque = {}, {}
        self.restored = False

    def _close_bus(self):
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception:  # noqa: BLE001
                pass
        self.bus = None

    # ---- 读 ----

    def _read_static(self, ids):
        for sid in ids:
            d = {}
            for addr, key in STATIC_REGS.items():
                try:
                    d[key] = self.bus.read_u8(sid, addr)
                except feetech.BusError:
                    d[key] = None
            try:
                d["offset"] = feetech.sign15(self.bus.read_u16(sid, 31))
                d["torque_limit"] = self.bus.read_u16(sid, 48)
            except feetech.BusError:
                d.setdefault("offset", None)
            self.static[sid] = d

    def _baseline_path(self, bid):
        return os.path.join(self.state_dir, f"{BASELINE_PREFIX}{bid}.json")

    def _baseline_load(self, bid):
        """读这次开机的基线。boot_id 读不到、或者演示会话，就用进程内存（按 state 目录 + 是否演示分开）。"""
        if bid is None:
            return _baseline_mem.setdefault((os.path.abspath(self.state_dir), bool(self.sim)), {})
        try:
            with open(self._baseline_path(bid), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        regs = data.get("regs") if isinstance(data, dict) else None
        out = {}
        if isinstance(regs, dict):
            for k, v in regs.items():
                try:
                    sid = int(k)
                except (TypeError, ValueError):
                    continue
                if sid in IDS and isinstance(v, dict) and isinstance(v.get("41"), int) and isinstance(v.get("46"), int):
                    out[sid] = {"41": v["41"] & 0xFF, "46": v["46"] & 0xFFFF}
        return out

    def _baseline_save(self, bid, regs):
        """原子写 servo-baseline-<boot_id>.json（先写临时文件再 os.replace）。"""
        path = self._baseline_path(bid)
        data = {"v": 1, "boot_id": bid, "at": time.time(),
                "regs": {str(i): dict(regs[i]) for i in IDS if i in regs}}
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            self.log.warn("41/46 基线没写进文件（这次进程里还记着）", cat="总线", step=path,
                          raw=f"{type(e).__name__}: {e}", hint="看看 state 目录满没满、有没有权限")

    def _record_baseline(self, found):
        """这次开机第一次接管时记下 41（1 字节）/ 46（2 字节）原始值。
        同一个 boot_id 的文件已经在就不覆盖，只补这次有应答、文件里还没有的舵机；
        boot_id 读不到就只记在进程内存里。演示会话只记进程内存，不碰 state 目录里的基线文件。"""
        bid = boot_id()
        self.boot_id = bid
        if self.sim:
            # 演示舵机的 41/46 是假的：不读、不写、不删 state 里的基线，只记进程内存
            regs = self._baseline_load(None)
        else:
            if bid is not None:
                current = os.path.basename(self._baseline_path(bid))
                try:
                    names = os.listdir(self.state_dir)
                except OSError:
                    names = []
                for name in names:                  # 别的开机的基线已经没用了
                    if name.startswith(BASELINE_PREFIX) and name.endswith(".json") and name != current:
                        try:
                            os.remove(os.path.join(self.state_dir, name))
                        except OSError:
                            pass
            regs = self._baseline_load(bid)
        added = False
        for sid in found:
            if sid in regs:
                continue
            try:
                regs[sid] = {"41": self.bus.read_u8(sid, 41), "46": self.bus.read_u16(sid, 46)}
            except feetech.BusError as e:
                self.log.warn(f"41/46 基线：#{sid} 读不到，跳过", cat="总线", step="读 41/46 基线",
                              raw=f"{type(e).__name__}: {e}", hint="这颗这次可能不在总线上")
                continue
            added = True
        self.baseline = regs
        if not self.sim and bid is not None and added:
            self._baseline_save(bid, regs)

    def _restore_baseline(self):
        """交还前把 41/46 写回基线并回读，最多重试 2 次。
        回 (回读不对的颗, 没应答的颗)：前者不交还；后者主程序起来时也管不了，只记警告。"""
        if self.state != "open" or self.bus is None:
            return [], []
        base = getattr(self, "baseline", None) or {}
        missing = [i for i in IDS if i not in base]
        if missing:
            self.log.info(f"交接：{len(missing)} 颗没有 41/46 基线（这次开机接管时没应答），跳过", cat="总线",
                          raw=f"没有基线：{missing}", dedup=False)
        bad, unknown = [], []
        for sid in IDS:
            want = base.get(sid)
            if not want:
                continue
            outcome, last = "unknown", None
            for _ in range(3):                      # 第一次 + 重试 2 次
                try:
                    self.bus.write_u8(sid, 41, int(want["41"]) & 0xFF)
                    self.bus.write_u16(sid, 46, int(want["46"]) & 0xFFFF)
                    got41 = self.bus.read_u8(sid, 41)
                    got46 = self.bus.read_u16(sid, 46)
                except feetech.BusError as e:
                    outcome, last = "unknown", e
                    continue
                if got41 == (int(want["41"]) & 0xFF) and got46 == (int(want["46"]) & 0xFFFF):
                    outcome = "ok"
                    break
                outcome, last = "bad", f"回读 41={got41}、46={got46}，要的是 {want['41']}、{want['46']}"
            if outcome == "ok":
                continue
            if outcome == "bad":
                bad.append(sid)
                self.log.error(f"41/46 基线写回没对上：#{sid}", cat="安全", step="回读 41/46",
                               raw=str(last), hint="回读不对就不交还，再点一次「交还主程序」")
            else:
                unknown.append(sid)
                self.log.warn(f"41/46 基线写回：#{sid} 没应答，没确认", cat="安全", step="写/回读 41/46",
                              raw=f"{type(last).__name__}: {last}", hint="主程序起来时也管不了它")
        return bad, unknown

    def refresh_static(self, ids):
        """校准、写寄存器以后重读这几颗的偏移、P/D 这些（表里展开行要显示新值）。"""
        if self.bus is not None:
            self._read_static([i for i in ids if i in IDS])

    def _read_torque(self):
        got = self.bus.sync_read(IDS, 40, 1)
        self.torque = {sid: (v[1][0] if v else None) for sid, v in got.items()}
        self.torque_at = time.monotonic()
        return got

    def _loop(self):
        period = 1.0 / POLL_HZ
        n, t_rate = 0, time.monotonic()
        next_torque = time.monotonic() + TORQUE_EVERY_S
        while not self._stop.is_set():
            t0 = time.monotonic()
            if self.ops is not None and self.ops.calib_lock.locked():
                # 校准、时序测试时不抢总线：一次 sync_read 卡住能占几百毫秒，正好落在关扭矩的窗口里
                self._stop.wait(0.02)
                continue
            try:
                states = self.bus.states(IDS)
                now = time.monotonic()
                for sid, s in states.items():
                    if s is not None:
                        s["at"] = now
                        self.rows[sid] = s
                if now >= next_torque:
                    self._read_torque()
                    next_torque = now + TORQUE_EVERY_S
            except Exception as e:  # noqa: BLE001 —— 串口拔了之类：记一次、等一会儿再试
                self.log.error("读舵机出错", cat="总线", raw=f"{type(e).__name__}: {e}", dedup=True)
                time.sleep(0.5)
            n += 1
            if time.monotonic() - t_rate >= 1.0:
                self.hz = n / (time.monotonic() - t_rate)
                n, t_rate = 0, time.monotonic()
                self._note_window()
            left = period - (time.monotonic() - t0)
            if left > 0:
                self._stop.wait(left)

    def _note_window(self):
        tot = self._totals()
        self._window.append((time.monotonic(), tot[0], tot[1]))
        self._window = self._window[-6:]        # 最近 5 秒

    def _totals(self):
        per = self.bus.per_id if self.bus else {}
        return (sum(c["asked"] for c in per.values()), sum(c["missed"] for c in per.values()))

    def drop_rate(self):
        if len(self._window) < 2:
            return None
        (_, a0, m0), (_, a1, m1) = self._window[0], self._window[-1]
        return None if a1 == a0 else (m1 - m0) / (a1 - a0)

    # ---- 卸力 ----

    def relax_all(self, by=None):
        """总线上 15 颗全部卸力并回读确认（阻塞，几到十几毫秒）。返回 (全部确认, 原因)。"""
        if self.state != "open" or self.bus is None:
            return False, "舵机总线没打开"
        with self._cv:
            self.latched, self.relaxing = True, True
            if self.tracking is not None:
                self.tracking.stop_event.set()
        if self.ops is not None:                 # 正在走的过渡、灵动先停（锁住以后它们也写不进去）
            self.ops.idle_running = False
            self.ops._motion_stop.set()
        t0 = time.monotonic()
        bad = list(IDS)
        try:
            self.bus.sync_write(40, 1, [(sid, b"\x00") for sid in IDS])
            for _ in range(3):
                got = self._read_torque()
                bad = [sid for sid, v in got.items() if v is None or v[1][0] != 0]
                if not bad:
                    break
                for sid in bad:                      # 没确认的逐颗再写一次
                    try:
                        self.bus.write_u8(sid, 40, 0)
                    except feetech.BusError:
                        pass
        except Exception as e:  # noqa: BLE001
            self.log.error("总线卸力出错", cat="安全", raw=f"{type(e).__name__}: {e}", exc=True,
                           hint="用手捏住鸭子，关电源")
        finally:
            self.relaxing = False
        ms = round((time.monotonic() - t0) * 1000)
        self.last_relax = {"ok": not bad, "ms": ms, "bad": bad, "at": time.monotonic()}
        if bad:
            self.log.error(f"总线卸力：{len(bad)} 颗没确认", cat="安全", step="sync_read 40 回读",
                           raw=f"没确认：{bad}", ctx={"by": by, "ms": ms}, hint="可能是线松了没应答；关电源最保险", dedup=False)
            return False, f"{len(bad)} 颗没确认：{bad}"
        # 状态变化的日志每次都记（审查 D13：1 分钟去重会把第二次交还、第二次卸力吞掉，事后对不上）
        self.log.info(f"总线卸力：15 颗全部确认（{ms} ms）", cat="安全", step="sync_read 40 回读", raw=f"by={by}", dedup=False)
        return True, None

    # ---- 给网页的 ----

    def snapshot(self):
        now = time.monotonic()
        rows, volts = [], []
        per = self.bus.per_id if self.bus else {}
        for sid in IDS:
            s = self.rows.get(sid)
            st = self.static.get(sid, {})
            c = per.get(sid, {})
            age = None if not s else now - s["at"]
            fresh = age is not None and age < 0.5
            if fresh and s.get("volt"):
                volts.append(s["volt"])
            cn, en = NAMES[sid]
            rows.append({
                "id": sid, "cn": cn, "en": en, "group": GROUP[sid], "dir": self.directions[sid],
                "raw": s["pos"] if s else None,
                "deg": round(angle_deg(s["pos"], self.directions[sid]), 1) if s else None,
                "volt": s["volt"] if s else None, "temp": s["temp"] if s else None,
                "temp_limit": st.get("temp_limit"), "load": s["load"] if s else None,
                "current_ma": round(s["current_ma"]) if s else None,
                "status": s["status_text"] if s else None, "moving": s["moving"] if s else None,
                "torque": self.torque.get(sid), "fresh": fresh, "age_ms": None if age is None else round(age * 1000),
                "p": st.get("p"), "d": st.get("d"), "mode": st.get("mode"), "offset": st.get("offset"),
                "comm": {k: c.get(k, 0) for k in ("asked", "ok", "missed", "alarm", "streak", "max_streak")},
            })
        avg_v = round(sum(volts) / len(volts), 2) if volts else None
        torque_fresh = self.torque_at is not None and now - self.torque_at < 2.5
        all_off = torque_fresh and all(self.torque.get(i) == 0 for i in IDS)
        dr = self.drop_rate()
        return {
            "t": "servo", "state": self.state, "error": self.error, "sim": self.sim, "source": "bus",
            "port": "演示（没接舵机）" if self.sim else self.port,
            "marker_error": self.marker_error,
            "online": sum(1 for r in rows if r["fresh"]), "total": len(IDS),
            "hz": round(self.hz, 1), "avg_volts": avg_v, "battery_pct": battery_percent(avg_v),
            "drop_rate": None if dr is None else round(dr, 4),
            "bus": dict(self.bus.stats) if self.bus else None,
            "holding": sum(1 for i in IDS if self.torque.get(i) == 1),
            "all_off": all_off, "latched": self.latched, "relaxing": self.relaxing, "closing": self.closing,
            "last_relax": None if not self.last_relax else {k: v for k, v in self.last_relax.items() if k != "at"},
            "rows": rows,
            # 页头「软硬：P5 · D0 · 限力 1000（跟主程序一样）」；顺手给每行补运行中的 P/D/限力（servo_ops.gains_view）
            "gains": ops.gains_view(rows) if (ops := self.ops) is not None else None,
        }

    def estop_status(self):
        """给顶栏卸力按钮：舵机会话期间以总线回读为准。"""
        if self.state != "open":
            return {"state": "unavailable", "why": "舵机总线没打开，只能关电源"}
        if self.relaxing:
            return {"state": "pending", "why": "卸力中…"}
        s = self.snapshot()
        if s["all_off"]:
            return {"state": "confirmed", "why": "15 颗都已卸力（总线回读）"}
        return {"state": "ready", "why": "调舵机中：卸力直接走总线"}


def view_from_robotd(bus, directions, state="closed", error=None, sim=False):
    """没接管总线时，舵机页照样有表：数据来自主程序的 busStatus（它本来就在读舵机），只能看。
    角度跟接管以后同一个算法（方向 ×（读数 − 2048）），接管前后数字对得上。
    主程序的 position_rad 是按它自己的零位（zero_raw）算的，这里不用它。"""
    # Robotd carries IPC receipt/error evidence. The legacy dict form stays valid
    # for ordinary feedback, but cannot establish fresh startup observations.
    rd = bus if hasattr(bus, "bus") else None
    if rd is not None:
        bus = rd.bus.value
    observations = servo_observations(bus, present=getattr(rd, "present", None),
                                      bus_age_s=rd.bus.age() if rd is not None else None,
                                      bus_error=getattr(rd.bus, "error", None) if rd is not None else None)
    if observations is not None:
        return _view_startup_observations(observations, directions, state, error, sim)
    native = (bus or {}).get("native") if isinstance(bus, dict) and isinstance(bus.get("native"), dict) else {}
    joints = {j.get("id"): j for j in (native.get("joints") or []) if isinstance(j, dict)}
    sampled = sampled_joint_feedback(bus, receipt_age_s=rd.bus.age() if rd is not None else None,
                                     bus_error=getattr(rd.bus, "error", None) if rd is not None else None)
    if sampled is not None:
        joints = {j["id"]: j for j in sampled}
    torque_on = native.get("torque_enabled")
    if isinstance(bus, dict) and bus.get("mode") == "commissioning":
        torque_on = {"off": False, "all_enabled": True}.get(bus.get("torque_state"))
    rows, volts = [], []
    for sid in IDS:
        j = joints.get(sid) or {}
        raw = j.get("position_raw")
        d = DEFAULT_DIRECTIONS[sid] if directions is None else directions.get(sid, -1)
        fresh = j.get("feedback_fresh") is True
        if fresh and isinstance(j.get("volts"), (int, float)):
            volts.append(j["volts"])
        cn, en = NAMES[sid]
        rows.append({
            "id": sid, "cn": cn, "en": en, "group": GROUP[sid], "dir": d,
            "raw": raw, "deg": round(angle_deg(raw, d), 1) if isinstance(raw, int) else None,
            "volt": j.get("volts"), "temp": j.get("temperature_c"), "temp_limit": None,
            "load": j.get("load_raw"), "current_ma": j.get("current_ma"), "status": None, "moving": None,
            "torque": None if torque_on is None else int(bool(torque_on)), "fresh": fresh,
            "age_ms": j.get("sample_age_ms"), "p": None, "d": None, "mode": None, "offset": None,
            "comm": {"asked": None, "ok": None, "missed": j.get("missing_total", 0) or 0, "alarm": 0,
                     "streak": j.get("consecutive_missing", 0) or 0, "max_streak": None},
        })
    avg_v = round(sum(volts) / len(volts), 2) if volts else None
    return {
        "t": "servo", "state": state, "error": error, "sim": sim, "source": "robotd",
        "port": "主程序", "online": (None if sampled is not None and not any(r["fresh"] for r in rows) else sum(1 for r in rows if r["fresh"])), "total": len(IDS),
        "hz": None, "avg_volts": avg_v, "battery_pct": battery_percent(avg_v), "drop_rate": None, "bus": None,
        "holding": sum(1 for r in rows if r["torque"] == 1), "all_off": False, "latched": False,
        "relaxing": False, "last_relax": None, "rows": rows,
    }


def _view_startup_observations(observations, directions, state, error, sim):
    by_id = {row["id"]: row for row in observations["rows"]}
    rows, volts = [], []
    for sid in IDS:
        row = by_id.get(sid, {})
        cn, en = NAMES[sid]
        volt = row.get("volts")
        if volt is not None:
            volts.append(volt)
        rows.append({
            "id": sid, "cn": cn, "en": en, "group": GROUP[sid],
            "dir": DEFAULT_DIRECTIONS[sid] if directions is None else directions.get(sid, -1),
            "raw": row.get("position_raw"), "deg": None, "volt": volt,
            "temp": row.get("temperature_c"), "temp_limit": None, "load": row.get("load_raw"),
            "speed_raw": row.get("speed_raw"), "current_raw": row.get("current_raw"), "current_ma": None,
            "responding": row.get("responding"), "fresh": row.get("responding") is True,
            "feedback_fresh": row.get("feedback_fresh", False), "age_ms": row.get("check_age_ms"),
            "torque": None, "status": None, "moving": None, "p": None, "d": None, "mode": None, "offset": None,
            "comm": {key: None for key in ("asked", "ok", "missed", "alarm", "streak", "max_streak")},
        })
    avg_v = round(sum(volts) / len(volts), 2) if volts else None
    return {"t": "servo", "state": state, "error": error, "sim": sim, "source": "robotd", "diagnostic": True,
            "port": "主程序", "online": observations["online"], "total": len(IDS),
            "hz": None, "avg_volts": avg_v, "battery_pct": battery_percent(avg_v), "drop_rate": None, "bus": None,
            "holding": None, "all_off": False, "latched": False, "relaxing": False, "last_relax": None, "rows": rows}


def _sim_home():
    """模拟舵机摆在官方站姿（读数 = 2048 + 方向 × 角度），3D 上看得出是站着的。"""
    home = {20: 0.0, 21: -0.0873, 22: -0.4579, 23: -0.0049, 24: 0.4530, 30: 0.3491, 31: 0.3491,
            32: 0.0, 33: 0.0, 34: 0.0, 10: 0.0, 11: 0.0873, 12: 0.4579, 13: 0.0049, 14: -0.4530}
    return {sid: round(2048 + DEFAULT_DIRECTIONS[sid] * rad * 4096 / (2 * 3.141592653589793)) for sid, rad in home.items()}


def list_ports():
    """系统里现在能看到的串口。拔插 USB 后口号会变，页面上直接选（跟 servo-web 一样）。"""
    try:
        from serial.tools import list_ports as lp
        return [{"device": p.device, "desc": (p.description or "").strip()} for p in lp.comports()]
    except Exception:  # noqa: BLE001
        return []


def load_directions(state_dir):
    """方向表：state 目录里有 servo-directions.json 就用它，没有就全 −1。"""
    p = os.path.join(state_dir, "servo-directions.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return {int(k): int(v) for k, v in d.items() if k.isdigit() and int(v) in (1, -1)}
    except (OSError, ValueError):
        return {}
