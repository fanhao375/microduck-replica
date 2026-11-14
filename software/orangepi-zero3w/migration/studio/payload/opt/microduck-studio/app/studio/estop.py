"""卸力按钮的状态，全部从数据推出来，不看「按过没有」（review-impl-plan I5）。

robot.relax 在运动模式下只是把请求放进槽里，马上回 accepted:true——**回了不等于已经卸力**。
所以确认要看 busStatus：

运动模式（同时满足 5 条，规则跟我们的安装脚本 imu-module-install.sh:165-170 一样）：
    mode == "motion"
    torque_off_pending 不为真
    native.torque_state_confirmed is False
    native.hd1910.restore_pending is False
    native.hd1910.temporary_gains_applied is False
调试模式：
    torque_state == "off" 且 torque_off_pending 为空

一次「按下」的经过（由 robotd.py 推进，这里只记事实）：
    press()        按下：开始一次尝试。连按以最后一次为准：回执、回读都要重新等
    sent()         robotd 回了卸力的回执（记下时间 sent_at）。只认这次按下之后（最多早 0.3 秒）
                   才开始的发送：robotd 卡住时早就在路上的旧发送不算（复核 #3）
    on_bus()       轮询拿到一份 busStatus。**只有在 sent_at 和最后一次按下之后才发出的那次查询**，
                   看到扭矩 OFF，才算确认——之前发出的查询可能反映的是卸力前的状态（代码审查 #3、复核 #2）
    finish()       确认了 / 8 秒没确认 / 没发出去：这次尝试结束，pressed_at 清空。
                   结束以后不会再自己补发（代码审查 #1：以前确认后 pressed_at 不清，
                   别人用手柄让它站起来时 Studio 会自动又发一次卸力）
补发（2 秒没确认补一次）和超时（8 秒）由 robotd.py 里的监视任务按时间做，不依赖有没有人看页面。

按钮的四种状态（status() 只读，不改任何东西）：
    ready        可以按
    pending      按了还没确认
    confirmed    确认 OFF（1.5 秒内的新鲜回读；别人再上力就回到 ready；本来就软着也是这个）
    unavailable  主程序不在、还在启动，或者刚才那次没发出去 / 8 秒没确认（提示 6 秒后回到 ready）
状态读不到（busStatus 旧了）时仍然是 ready：按了照样发，只是确认要等回读恢复。
"""
import time

STARTING_PHASES = ("configuring_bus", "waiting_for_bus", "configuration_error")
# 标定模式起来了：robot.busStatus 回的是 feetech_commissioning.rs 的实时快照（Status，fork 1083 行附近），phase 是这几个。
# 启动时那份报告（{"mode":"commissioning","phase":"configuring_bus",...}）的 phase 在 STARTING_PHASES 里；
# 旧 fork 从来不清它，Studio 就永远看到「启动中」（10-20 真机验收），新 fork 第一次读成功以后清掉（1497 行附近）
# holding：标定模式「站姿使能」（fork hold-home）站住了；扭矩在 torque_state=all_enabled，卸力照常走 robot.relax
COMMISSIONING_UP = ("ready", "moving", "holding", "read_error", "stop_unconfirmed", "power_pending")
STARTING_LONG_S = 30.0       # 主程序连续报启动阶段这么久（Studio 从这次连上算）：改口「一直报启动中」（旧 fork 的毛病，或者真卡住了）
CONFIRM_TIMEOUT_S = 8.0
RESEND_AT_S = 2.0
CONFIRMED_FRESH_S = 2.5      # 显示「已卸力」要求回读不超过这么旧（轮询 1 秒一次；1.5 秒在查询慢时会来回闪，复核 #7）
OUTCOME_SHOW_S = 6.0
JOIN_S = 0.3                 # 按下时已经在路上、开始不到 0.3 秒的发送，算这次按下的


def robotd_up(bus):
    """主程序起来了吗（不管站没站）：运动模式进了 control，或者标定模式回的是实时快照（phase 在 COMMISSIONING_UP 里；
    read_error 也算起来了，只是读舵机出错）。启动阶段、看不懂的都是 False。"""
    if not isinstance(bus, dict):
        return False
    mode, phase = bus.get("mode"), bus.get("phase")
    return (mode == "motion" and phase == "control") or (mode == "commissioning" and phase in COMMISSIONING_UP)


def relaxed(bus):
    """busStatus 说扭矩已经确认 OFF 了吗？True / False / None（数据不够，不知道）。"""
    if not isinstance(bus, dict):
        return None
    mode = bus.get("mode")
    if mode == "motion":
        native = bus.get("native")
        if not isinstance(native, dict) or "torque_state_confirmed" not in native:
            return None
        hd = native.get("hd1910") or {}
        return (bus.get("torque_off_pending") is not True
                and native.get("torque_state_confirmed") is False
                and hd.get("restore_pending") is False
                and hd.get("temporary_gains_applied") is False)
    if mode == "commissioning":
        if "torque_state" not in bus:
            return None
        return bus.get("torque_state") == "off" and not bus.get("torque_off_pending")
    return None


def torque_off(bus):
    """扭矩本身已经确认 OFF 了吗（不管 hd1910 标志）。用来决定还要不要补发。"""
    if not isinstance(bus, dict):
        return False
    if bus.get("mode") == "commissioning":
        return bus.get("torque_state") == "off"
    native = bus.get("native")
    return isinstance(native, dict) and native.get("torque_state_confirmed") is False


def evidence(bus):
    """确认不了时写进日志的现场：判断卸力用到的那几个字段。"""
    if not isinstance(bus, dict):
        return {"busStatus": None}
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    hd = native.get("hd1910") if isinstance(native.get("hd1910"), dict) else {}
    return {"mode": bus.get("mode"), "phase": bus.get("phase"), "torque_off_pending": bus.get("torque_off_pending"),
            "torque_state": bus.get("torque_state"), "torque_state_confirmed": native.get("torque_state_confirmed"),
            "restore_pending": hd.get("restore_pending"), "temporary_gains_applied": hd.get("temporary_gains_applied")}


class EStop:
    def __init__(self):
        self.pressed_at = None       # 进行中的那次尝试从什么时候开始（monotonic）；None = 没有
        self.sent_at = None          # 这次尝试里 robotd 第一次回执的时间
        self.resent = False
        self.saw_off = False         # 回执之后的回读里见过扭矩确认 OFF（之后只差 hd1910 标志，不用补发）
        self.send_error = None       # 第一次已经发出去、后来的补发 / 再按没发出去的原因（超时时写进日志）
        self.press_count = 0
        self.last_by = None
        self.confirm_ms = None       # 最近一次从按下到确认用了多少毫秒
        self.outcome = None          # 最近一次尝试的结果：confirmed / timeout / send_failed / refused
        self.outcome_at = None
        self.outcome_why = ""

    @property
    def active(self):
        return self.pressed_at is not None

    def press(self, by=None, now=None):
        """每按一次都重新计时，回执、回读都重新等（按住空格连按、手机上连点：以最后一次为准）。"""
        now = time.monotonic() if now is None else now
        self.sent_at = None
        self.resent = False
        self.saw_off = False
        self.send_error = None
        self.pressed_at = now
        self.press_count += 1
        self.last_by = by
        self.outcome = None

    def belongs(self, started):
        """一次发送（started = 开始时间）算不算这次尝试的。"""
        return self.active and started >= self.pressed_at - JOIN_S

    def sent(self, started, now=None):
        now = time.monotonic() if now is None else now
        if self.belongs(started) and self.sent_at is None:
            self.sent_at = now

    def send_failed(self, started, why, now=None):
        """这次尝试的一次发送失败了。还没有任何回执：这次尝试就算失败；
        已经有回执（第一次发出去了，是补发或再按失败）：继续等回读，原因留着写进超时日志。"""
        if not self.belongs(started):
            return
        if self.sent_at is None:
            self.finish("send_failed", why, now=now)
        else:
            self.send_error = why

    def on_bus(self, bus, asked_at, now=None):
        """轮询拿到一份 busStatus（asked_at = 这次查询发出的时间）。确认了返回 True。"""
        now = time.monotonic() if now is None else now
        if not self.active or self.sent_at is None or asked_at < max(self.sent_at, self.pressed_at):
            return False
        if torque_off(bus):
            self.saw_off = True
        if relaxed(bus) is True:
            self.confirm_ms = round((now - self.pressed_at) * 1000)
            self.finish("confirmed", now=now)
            return True
        return False

    def finish(self, outcome, why="", now=None):
        now = time.monotonic() if now is None else now
        self.outcome, self.outcome_at, self.outcome_why = outcome, now, why
        self.pressed_at = None

    def elapsed(self, now=None):
        if not self.active:
            return None
        return (time.monotonic() if now is None else now) - self.pressed_at

    def status(self, *, present, bus, bus_age_s, now=None):
        """返回 {"state": ..., "why": 给人看的一句话}。只读。"""
        now = time.monotonic() if now is None else now
        mode = bus.get("mode") if isinstance(bus, dict) else None
        phase = bus.get("phase") if isinstance(bus, dict) else None
        no_pad = "只能关电源" if (not present or phase in STARTING_PHASES or mode == "commissioning") \
            else "用手柄 − 或关电源"
        if self.active:
            # 先于「主程序不在」：robotd 刚回来、轮询还在退避里睡时按下，回执已经到了，别显示「不在」（复核 #6）
            return {"state": "pending", "why": "卸力中…"}
        if not present:
            return {"state": "unavailable", "why": f"主程序不在，{no_pad}"}
        fresh = bus is not None and bus_age_s is not None and bus_age_s <= CONFIRMED_FRESH_S
        if fresh and phase in STARTING_PHASES:
            return {"state": "unavailable", "why": f"主程序还在启动，{no_pad}"}
        if fresh and relaxed(bus) is True:
            return {"state": "confirmed", "why": "15 颗都已卸力"}
        recent = self.outcome_at is not None and now - self.outcome_at < OUTCOME_SHOW_S
        if recent and self.outcome in ("timeout", "send_failed", "refused"):
            return {"state": "unavailable", "why": f"{self.outcome_why}，{no_pad}"}
        if not fresh:
            return {"state": "ready", "why": "读不到鸭子的状态，按了照样会发"}
        return {"state": "ready", "why": ""}
