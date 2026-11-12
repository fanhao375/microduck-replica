"""跟鸭子上的服务说话：robotd 4 条连接 + configd 1 条（review-impl-plan I4）。

| 连接    | 只做什么                                                      |
| stream  | robot.subscribe {"hz":10}。有人在看才开，最后一个人走了 10 秒后才关 |
| poll    | hello（一次）、health 2 s、busStatus 1 s（卸力确认时 10 Hz）、policies 2 s、calibration-status（连上时、phase 变成 control 时各一次，只认这次 hello 之后的） |
| cmd     | 运动命令（S1b 才用）                                           |
| 卸力    | 只发 robot.relax；每次新开一条连接（不留长连接）                   |
| 启动步骤 | robot.startupSubscribe：这次 hello 的四步没全成功时开，全成功 / 有一步失败就关（startup.py） |
| configd | pad.status 5 s、system.info 60 s                               |

为什么分开：robotd 一条连接一次只处理一个请求，卸力不能排在一个 2 秒超时的 busStatus 后面。
为什么订阅要「有人看才开」：只要有一个订阅者，robotd 的控制线程每拍都要组一帧（跟 hz 无关），
没人看就别让它白干活（review-tech-v3 T4）。

所有时间用 time.monotonic()：鸭子开机没联网时墙钟停在 1970（I12）。
本模块**不 import 任何串口相关的东西**：Studio 的这一版根本没有能打开 ttyS2 的路径（I1）。
"""
import asyncio
import time

from .estop import CONFIRM_TIMEOUT_S, JOIN_S, RESEND_AT_S, STARTING_PHASES, EStop, evidence
from .log import log_cmd
from .rpc import Conn, RpcError, Timeout, Unreachable, call_once, encode, open_conn, read_message
from .startup import StartupTrack, startup_loop

API_VERSION = 44                     # Current duck-ipc-proto hello contract.
SUPPORTED_API_VERSIONS = frozenset((42, 43, 44))  # Additive observation/verification/read-order contracts.
KNOWN_BUILDS = ("1fa8438-feetech-ft6-control.43", "4734cbb-feetech-imu-auto-control.44",
                "0791fcd-feetech-qinmo-control.45", "0791fcd-feetech-qinmo-control.46",
                "0791fcd-feetech-qinmo-control.47", "replica-imu-verify-control.48",
                "replica-imu-home-control.49", "replica-imu-home-control.50",
                "replica-bus-health-control.49", "replica-bus-health-control.51")
BACKOFF = (0.5, 1.0, 2.0, 5.0)
STREAM_HZ = 10
STREAM_LINGER_S = 10.0


class Sample:
    __slots__ = ("value", "at", "error")

    def __init__(self):
        self.value, self.at, self.error = None, None, None

    def set(self, value):
        self.value, self.at, self.error = value, time.monotonic(), None

    def fail(self, error):
        self.error = str(error)

    def age(self, now=None):
        if self.at is None:
            return None
        return (time.monotonic() if now is None else now) - self.at


class Robotd:
    def __init__(self, robotd_addr, configd_addr, log):
        self.log = log
        self.addr = robotd_addr
        self.poll = Conn(robotd_addr, "poll", log)
        self.cmd = Conn(robotd_addr, "cmd", log)
        self.cfg = Conn(configd_addr, "configd", log)
        self.hello, self.health, self.bus = Sample(), Sample(), Sample()
        self.policies, self.calib, self.state = Sample(), Sample(), Sample()
        self.pad, self.sysinfo = Sample(), Sample()
        self.present = None          # robotd 的 socket 在不在；None = 还没试过
        self.absent_since = None
        self.estop = EStop()
        self.viewers = 0             # 可见的页面数
        self.telemetry_viewers = 0   # 舵机/IMU 状态页需要 10 Hz IPC 缓存快照；不增加 UART 读取
        self.force_subscribe = False
        self.background_music = False  # 内部动作消费者；不伪装成可见页面
        self.stream_open = False
        self._last_viewer_at = 0.0
        self._tasks = []
        self._wake_stream = asyncio.Event()
        self._due = {"health": 0.0, "bus": 0.0, "policies": 0.0}   # 轮询各项下次的时间（monotonic）
        self._fails = {}             # 轮询各项连续失败的次数
        self._relax_send = None      # 正在路上的卸力发送（asyncio.Task）
        self._relax_send_started = 0.0
        self._relax_watch = None     # 卸力的补发 / 超时监视
        self._calib_task = None
        # 标定状态（calibration-status）只认「这次 hello 之后」读到的（总览重排审查 O5）：robotd 重启以后、
        # 刚起来还没开总线的那 20 秒，旧的「15 颗都标了」不能当真。None = 还没 hello 过（测试里的假对象也是 None）
        self.calib_valid_after = None
        self._last_phase = None
        # 这次连上以后 busStatus 从什么时候起一直报启动阶段（monotonic）；不在启动阶段是 None。超过 30 秒页面改口
        # 「一直报启动中」（10-20 真机：旧 fork 的标定模式永远报 configuring_bus）
        self.starting_since = None
        self._wake_poll = asyncio.Event()   # 卸力时把轮询从退避的睡眠里叫醒
        self._loop = None                   # 事件循环：别的线程调 set_paused 时用它叫醒轮询（线程安全）
        self.paused = None                  # 调舵机时 robotd 按计划停着：轮询暂停，不报「主程序不在」
        self.startup = StartupTrack()       # 启动四步（startup.py 的 startup_loop 写；调参卡、待办读）

    # ---- 生命周期 ----

    def start(self):
        loop = asyncio.get_running_loop()
        self._loop = loop
        if self.addr == "none":
            # 台架模式：主控没烧、只用调试板在电脑上调舵机，不连主程序
            self.log.info("台架模式：不连主程序 robotd", cat="robotd")
            self._tasks = []
            return
        self._tasks = [loop.create_task(self._supervise("轮询", self._poll_loop), name="robotd-poll"),
                       loop.create_task(self._supervise("实时状态订阅", self._stream_loop), name="robotd-stream"),
                       loop.create_task(self._supervise("configd 轮询", self._configd_loop), name="configd-poll"),
                       loop.create_task(self._supervise("启动步骤订阅", lambda: startup_loop(self)), name="robotd-startup")]

    async def stop(self):
        # 正在路上的卸力先让它发完（最多等 1 秒），别在关 Studio 时把它掐掉
        if self._relax_send is not None and not self._relax_send.done():
            await asyncio.wait({self._relax_send}, timeout=1.0)
        tasks = [t for t in self._tasks + [self._relax_send, self._relax_watch, self._calib_task]
                 if t is not None]
        for t in tasks:
            t.cancel()
        for c in (self.poll, self.cmd, self.cfg):
            c.close()
        # 取消以后要等任务真的退出：只 cancel 不等，任务可能正好停在「future 已回、下一步还没跑」，
        # 留下一个永远醒不来的 cancelling 任务，测试 / 退出时就会卡在 _cancel_all_tasks。
        # 兜底最多等 2 秒：真遇到也不至于让 Studio 退不出去。
        pending = [t for t in tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=2.0)

    async def _supervise(self, name, fn):
        """后台循环出了任何意外都不能无声停掉（代码审查 #4）：记下完整报错，1 秒后重启。"""
        while True:
            try:
                await fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.log.error(f"后台任务「{name}」出错，1 秒后重启", cat="系统", raw=f"{type(e).__name__}: {e}",
                               hint="这是 Studio 自己的 bug，请把诊断包发出来", exc=True)
                await asyncio.sleep(1.0)

    def set_paused(self, on, why=None):
        if on and not self.paused:
            self.paused = why or "暂停"
            self.log.info(f"暂停跟主程序的轮询（{self.paused}）", cat="robotd", hint="主程序按计划停着，不是出错")
        elif not on and self.paused:
            self.log.info(f"恢复跟主程序的轮询（{self.paused}结束）", cat="robotd")
            self.paused = None
            # 从舵机线程调（关总线前恢复轮询）时不能直接 Event.set：debug 模式下 Future.set_result
            # 会先把事件置成已完成，再在跨线程调度那一步抛错，等待方就永远醒不过来了。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                if self._loop is not None and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._wake_poll.set)
            else:
                self._wake_poll.set()

    def set_viewers(self, n):
        n = max(0, n)
        if n and not self.viewers:
            self._wake_stream.set()
        if self.viewers and not n:
            self._last_viewer_at = time.monotonic()
        self.viewers = n

    def set_telemetry_viewers(self, n):
        n = max(0, n)
        if n and not self.telemetry_viewers:
            self._due["bus"] = 0.
            self._wake_poll.set()
        self.telemetry_viewers = n

    def bus_poll_interval(self):
        return .1 if self.telemetry_viewers or (self.bus.value or {}).get("mode") == "commissioning" else 1.

    def set_background_music(self, on):
        on = bool(on)
        if on != self.background_music:
            self.background_music = on
            self._wake_stream.set()

    # ---- 卸力 ----

    async def relax(self, by):
        """按一下卸力，回 (发出去了没有, 原因)。确认由 busStatus 回读完成（estop.on_bus）。

        - 发送在独立的任务里跑，调用方（页面连接）断了也不会把它取消（代码审查 #6）；
        - 同一时间最多一个发送在路上，连按就等同一个；
        - 监视任务负责 2 秒补发一次、8 秒超时，确认或超时就结束（代码审查 #1）。"""
        now = time.monotonic()
        self.estop.press(by, now)
        self._due["bus"] = 0.0          # 排在后面的 busStatus 提前：马上开始每 0.1 秒回读
        self._wake_poll.set()           # 轮询要是正在退避里睡（robotd 刚回来），叫醒它
        loop = asyncio.get_running_loop()
        # 在路上的发送刚开始（< 0.3 秒）就跟它；早就开始的（robotd 卡住了）另发一个，不吞掉这次按下（复核 #3）
        if (self._relax_send is None or self._relax_send.done()
                or now - self._relax_send_started > JOIN_S):
            self._start_send(by, "卸力")
        if self._relax_watch is None or self._relax_watch.done():
            self._relax_watch = loop.create_task(self._watch_relax())
        return await asyncio.shield(self._relax_send)

    def _start_send(self, by, what):
        started = time.monotonic()
        self._relax_send_started = started
        self._relax_send = asyncio.get_running_loop().create_task(self._send_relax(by, what, started))

    async def _send_relax(self, by, what, started):
        """每次新开一条连接发（代码审查 #2：长期空闲的连接在 robotd 重启后已经死了）。
        连不上隔 0.1 秒再试一次（robotd 重启时 socket 会短暂不在）。
        发出之前的每一步都不许失败：日志不抛异常，现场字段只读不算（复核 #1）。"""
        with log_cmd(self.log, what, "robot.relax", who=by,
                     ctx={"mode": self._mode(), "phase": self._phase()}) as t:
            r = None
            for attempt in (1, 2):
                try:
                    r = await call_once(self.addr, "robot.relax", {}, timeout=3.0, name="卸力", log=self.log)
                    break
                except Unreachable as e:
                    if attempt == 1:
                        await asyncio.sleep(0.1)
                        continue
                    return self._relax_failed(t, started, f"没发出去：{e}")
                except (Timeout, RpcError) as e:
                    return self._relax_failed(t, started, f"没发出去：{e}")
            if isinstance(r, dict) and r.get("accepted") is False:
                return self._relax_failed(t, started, f"主程序没接受卸力：{r.get('reason') or '没说原因'}", reply=r)
            self.estop.sent(started)
            self._mark_present(True)        # robotd 回话了：它在（轮询可能还在退避里，没发现它回来）
            note = "已发出，等回读确认"
            if not (isinstance(r, dict) and "accepted" in r):
                note += "（回执里没有 accepted 字段，以回读为准）"
            t.ok(r, note=note)
            return True, None

    def _relax_failed(self, t, started, why, reply=None):
        t.fail(why, hint="要停住它：用手柄 − 或者关电源", raw=reply)
        self.estop.send_failed(started, why)
        return False, why

    async def _watch_relax(self):
        while self.estop.active:
            el = self.estop.elapsed()
            if el >= CONFIRM_TIMEOUT_S:
                bus = self.bus.value
                why = "按了 8 秒还没确认卸力"
                if self.estop.send_error:
                    why += f"（后来那次{self.estop.send_error}）"
                self.estop.finish("timeout", why)
                self.log.error("卸力 8 秒还没确认", cat="安全", step="busStatus 回读",
                               ctx=dict(evidence(bus), bus_age_s=_round(self.bus.age()),
                                        saw_torque_off=self.estop.saw_off, send_error=self.estop.send_error),
                               hint="舵机可能还顶着：用手柄 − 或者关电源；把诊断包发出来")
                return
            # 2 秒还没确认，补发一次。回读里已经看到扭矩关了（只差 hd1910 标志）就不补：
            # 那时别人可能已经用手柄让它站起来了，补发会把它又卸软（复核 #5）
            if el >= RESEND_AT_S and not self.estop.resent and not self.estop.saw_off:
                self.estop.resent = True
                if self._relax_send is None or self._relax_send.done():
                    self._start_send(self.estop.last_by, "卸力（2 秒没确认，补发一次）")
            await asyncio.sleep(0.1)

    # ---- 轮询 ----

    async def _ask(self, key, method, params=None, timeout=2.0, every=1.0):
        """问一项。成功了按 every 排下一次；回了错误或超时只影响这一项（代码审查 #5），
        按退避推迟它，不挡别的项。Unreachable 往上抛（整条连接没了）。"""
        asked = time.monotonic()
        try:
            r = await self.poll.call(method, params or {}, timeout=timeout)
            if not isinstance(r, dict):
                raise RpcError(method, None, f"回的不是对象（{type(r).__name__}）")
        except (RpcError, Timeout) as e:
            if key == "bus" or isinstance(e, Timeout):
                self.bus.fail(e)
            n = self._fails[key] = self._fails.get(key, 0) + 1
            self._due[key] = time.monotonic() + BACKOFF[min(n - 1, len(BACKOFF) - 1)] * 2
            if n in (1, 10) or n % 60 == 0:      # 第 1、10 次和之后每 60 次记一行
                self.log.warn("主程序没回好这项查询", cat="robotd", step=method, raw=f"{e}（连续第 {n} 次）",
                              hint="偶尔一次不要紧；一直这样，看主板温度和 robotd 日志")
            return None, asked
        if self._fails.pop(key, 0) >= 10:
            self.log.info("这项查询恢复了", cat="robotd", step=method)
        self._due[key] = time.monotonic() + every
        return r, asked

    async def _poll_loop(self):
        due = self._due
        fails = 0
        hello_done = False
        hello_link = None             # hello 那一刻轮询连接的重连计数
        while True:
            if self.paused:
                hello_done = False
                await self._nap(1.0)
                continue
            if hello_done and self.poll.reconnects != hello_link:
                # 轮询连接断过、重连过（某一项超时 rpc 会关掉连接，下次自动重连）：robotd 可能已经是个新进程了，
                # 当成新连上——重新 hello、作废已经读到的标定（代码审查 C8）
                hello_done = False
                self.log.info("跟主程序的轮询连接重连过：当成新连上，重新 hello、重读标定状态", cat="robotd")
            try:
                if not hello_done:
                    self.bus.fail("等待这次连接的总线状态")
                    r = await self.poll.call("hello", {"api_version": API_VERSION}, timeout=2.0)
                    self.hello.set(r)
                    self.calib_valid_after = self.hello.at    # 这之前读到的标定状态作废（审查 O5）
                    self._last_phase = None
                    self.starting_since = None                # 「一直报启动中」从这次连上重新算
                    self._mark_present(True)
                    hello_done = True
                    hello_link = self.poll.reconnects
                    self._ask_calib(again=True)
                    v = r.get("api_version") if isinstance(r, dict) else None
                    self._check_api(v)
                    ver = r.get("daemon_version") if isinstance(r, dict) else None
                    self.log.info(f"连上主程序（接口 v{v}，版本 {ver}）", cat="robotd")
                now = time.monotonic()
                if now >= due["bus"]:
                    # 标定模式 10 Hz：IMU 页的实时读数、站着校准都从 last_sample 拿（标定模式没有 robot.state）
                    every = self.bus_poll_interval()
                    bus, asked = await self._ask("bus", "robot.busStatus", every=every)
                    if bus is not None:
                        self.bus.set(bus)
                        self._check_build(bus)
                        # 离开 control（robotd 卡回等总线、换了模式）：已经读到的标定不作数（代码审查 C8）；
                        # 从「等总线 / 启动中」进了 control（或调试模式的 ready）：标定文件这时才存进 robotd
                        # （fork main.rs:1535），再问一次（审查 O5）
                        phase = bus.get("phase") if isinstance(bus, dict) else None
                        if self._last_phase == "control" and phase != "control":
                            self.calib_valid_after = time.monotonic()
                        if (phase != self._last_phase and self._last_phase is not None and phase is not None
                                and phase not in STARTING_PHASES):
                            self._ask_calib(again=True)
                        self._last_phase = phase
                        if phase in STARTING_PHASES:
                            if self.starting_since is None:
                                self.starting_since = time.monotonic()
                        else:
                            self.starting_since = None
                        if self.estop.on_bus(bus, asked):
                            self.log.info(f"卸力已确认（按下到确认 {self.estop.confirm_ms} ms）", cat="安全",
                                          step="busStatus 回读")
                    if self.estop.active:
                        due["bus"] = min(due["bus"], time.monotonic() + 0.1)
                if now >= due["health"]:
                    r, _ = await self._ask("health", "robot.health", every=2.0)
                    if r is not None:
                        self.health.set(r)
                if now >= due["policies"]:
                    if self._mode() == "motion" and self._phase() == "control":
                        r, _ = await self._ask("policies", "robot.policies", every=2.0)
                        if r is not None:
                            self.policies.set(r)
                    else:
                        due["policies"] = now + 2.0
                fails = 0
            except Unreachable as e:
                self.bus.fail(e)
                hello_done = False
                self._mark_present(False, e)
                fails += 1
                await self._nap(BACKOFF[min(fails - 1, len(BACKOFF) - 1)])
                continue
            except (Timeout, RpcError) as e:
                # 只有 hello 会走到这里
                fails += 1
                self.log.warn("主程序没回好 hello", cat="robotd", raw=str(e),
                              hint="主程序可能正在启动；一直这样，看 robotd 日志")
                await self._nap(BACKOFF[min(fails - 1, len(BACKOFF) - 1)])
                continue
            await asyncio.sleep(0.05)

    async def _nap(self, seconds):
        """退避的睡眠，按卸力时会被叫醒。"""
        try:
            await asyncio.wait_for(self._wake_poll.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        self._wake_poll.clear()

    def _ask_calib(self, again=False):
        """起一次 _calib_once（已经在问就不重复起）。again：phase 刚变成 control，上一轮可能正在「没读到、10 秒后再试」
        的睡眠里，掐掉它马上重问（标定文件这时才有）。"""
        if again and self._calib_task is not None and not self._calib_task.done():
            self._calib_task.cancel()
            self._calib_task = None
        if self._calib_task is None or self._calib_task.done():
            self._calib_task = asyncio.get_running_loop().create_task(self._calib_once())

    def calib_fresh(self):
        """calibration-status 的回包，只要这次 hello 之后读到的；读不到 / 旧的回 None（审查 O5）。"""
        return fresh_calib(self)

    async def _calib_once(self):
        """标定状态只在连上时、和 phase 变成 control 时各查一次，用独立连接：它在 robotd 里可能很慢，不能挡住
        busStatus（复核 #8）。失败最多再试两次，隔 10 秒；还不行就算了（页面上显示不知道）。"""
        for attempt in range(3):
            try:
                r = await call_once(self.addr, "robot.busCommand", {"action": "calibration-status"},
                                    timeout=5.0, name="标定状态", log=self.log)
                if isinstance(r, dict):
                    self.calib.set(r)
                    return
                self.calib.fail(f"回的不是对象（{type(r).__name__}）")
            except (Unreachable, Timeout, RpcError) as e:
                self.calib.fail(e)
                if attempt == 2:
                    self.log.warn("读不到标定状态", cat="校准", raw=str(e), hint="不影响站和走；页面上标定那一项显示「不知道」")
            await asyncio.sleep(10.0)

    def _mark_present(self, ok, why=None):
        if ok and self.present is not True:
            if self.present is False:
                gone = time.monotonic() - (self.absent_since or time.monotonic())
                self.log.info(f"主程序回来了（断了 {gone:.0f} 秒）", cat="robotd")
            self.present, self.absent_since = True, None
        elif not ok and self.present is not False:
            self.present, self.absent_since = False, time.monotonic()
            self.log.warn("连不上主程序 robotd", cat="robotd", raw=str(why),
                          hint="鸭子主程序没在跑，或者正在重启（重启约 2 秒）。注意：停掉 robotd 不会卸力，舵机可能还顶着")

    def _check_api(self, version):
        if version not in SUPPORTED_API_VERSIONS:
            self.log.warn(f"主程序的接口版本是 {version}，Studio 已核对接口 42–44", cat="robotd",
                          hint="一般能用；具体功能仍按回读字段和能力确认")

    def _check_build(self, bus):
        build = (bus or {}).get("build")
        if build and build not in KNOWN_BUILDS and not getattr(self, "_warned_build", False):
            self._warned_build = True
            self.log.warn(f"主程序版本 {build} Studio 还没核对过", cat="robotd",
                          hint="只读显示和卸力照常；以后的运动按钮会先置灰")

    def _mode(self):
        b = self.bus.value
        return b.get("mode") if isinstance(b, dict) else None

    def _phase(self):
        b = self.bus.value
        return b.get("phase") if isinstance(b, dict) else None

    # ---- 订阅 ----

    def _want_stream(self):
        if self.paused:
            return False
        if self.force_subscribe or self.background_music or self.viewers > 0:
            return True
        return time.monotonic() - self._last_viewer_at < STREAM_LINGER_S

    async def _stream_loop(self):
        fails = 0
        while True:
            if not self._want_stream() or self._mode() != "motion" or self._phase() != "control":
                self._wake_stream.clear()
                try:
                    await asyncio.wait_for(self._wake_stream.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self._stream_once()
                fails = 0
            except (Unreachable, RpcError) as e:
                fails += 1
                self.log.warn("实时状态订阅断了", cat="robotd", raw=str(e))
                await asyncio.sleep(BACKOFF[min(fails - 1, len(BACKOFF) - 1)])
            except asyncio.TimeoutError:
                fails += 1
                self.log.warn("实时状态 3 秒没来新帧，重新订阅", cat="robotd")

    async def _stream_once(self):
        r, w = await asyncio.wait_for(open_conn(self.addr), 3.0)
        self.stream_open = True
        self.log.info(f"开始订阅实时状态（{STREAM_HZ} Hz，{self.viewers} 个页面在看）", cat="robotd")
        try:
            try:
                w.write(encode(1, "robot.subscribe", {"hz": int(STREAM_HZ)}))   # hz 必须是整数（u32）
                await w.drain()
            except (ConnectionError, OSError) as e:
                raise Unreachable(f"订阅请求没发出去：{type(e).__name__}: {e}") from e
            while True:
                msg = await read_message(r, 3.0)
                if msg is None:
                    continue
                if msg.get("id") == 1:
                    if "error" in msg:
                        err = msg["error"] or {}
                        raise RpcError("robot.subscribe", err.get("code"), err.get("message", ""))
                    continue
                if msg.get("method") == "robot.state":
                    self.state.set(msg.get("params"))
                if not self._want_stream():
                    self.log.info("没人在看了，停止订阅", cat="robotd")
                    return
        finally:
            self.stream_open = False
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- configd ----

    async def _configd_loop(self):
        """system.info 60 s、pad.status 5 s，两项各自出错各自退避（代码审查 #19：以前 system.info
        一直失败，pad.status 就永远轮不到）。"""
        due = {"info": 0.0, "pad": 0.0}
        down = {"info": False, "pad": False}
        while True:
            now = time.monotonic()
            for key, method, sample, every in (("info", "system.info", self.sysinfo, 60.0),
                                               ("pad", "pad.status", self.pad, 5.0)):
                if now < due[key]:
                    continue
                try:
                    sample.set(await self.cfg.call(method, {}, timeout=2.0))
                    due[key] = now + every
                    if down[key]:
                        down[key] = False
                        self.log.info(f"configd 的 {method} 恢复了", cat="手柄")
                except (Unreachable, Timeout, RpcError) as e:
                    sample.fail(e)
                    due[key] = now + 5.0
                    if not down[key]:
                        down[key] = True
                        self.log.warn(f"configd 的 {method} 读不到", cat="手柄", raw=str(e),
                                      hint="有的鸭子关掉了 configd；不影响站和走，只是手柄那一栏显示「不知道」")
            await asyncio.sleep(0.5)

    # ---- 只读自检（--selfcheck） ----

    async def selfcheck(self):
        """只读。除了各问一次，还查三个 socket 的权限（上机最容易卡在 robot 组上），
        以及「不先 hello、新连接直接查询」行不行（卸力每次都是新连接直接发）。"""
        import grp
        import os
        import pwd
        out = {}
        try:
            me = pwd.getpwuid(os.getuid()).pw_name
            groups = sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
            out["用户"] = f"{me} · 组 {','.join(groups)}"
        except (ImportError, KeyError, OSError) as e:
            out["用户"] = f"（查不到：{e}）"
        for path in ("/run/robotd.sock", "/run/configd.sock", "/run/padd/pad.sock"):
            if not os.path.exists(path):
                out[path] = "不在"
            else:
                out[path] = "可读写" if os.access(path, os.R_OK | os.W_OK) else "没权限（把用户加进 robot 组）"
        for name, method, params, addr in (
            ("新连接直接查", "robot.health", {}, self.addr),
            ("hello", "hello", {"api_version": API_VERSION}, self.addr),
            ("busStatus", "robot.busStatus", {}, self.addr),
            ("pad.status", "pad.status", {}, self.cfg.addr),
            ("system.info", "system.info", {}, self.cfg.addr),
        ):
            try:
                r = await call_once(addr, method, params, timeout=3.0)
                if name == "busStatus":
                    out[name] = f"ok · mode={r.get('mode')} phase={r.get('phase')} build={r.get('build')}"
                elif name == "hello":
                    out[name] = f"ok · 接口 v{r.get('api_version')} 版本 {r.get('daemon_version')}"
                else:
                    out[name] = "ok"
            except Exception as e:  # noqa: BLE001
                out[name] = f"失败：{e}"
        return out


def _round(x, nd=2):
    return None if x is None else round(x, nd)


def fresh_calib(rd):
    """rd.calib 的值，但只认「这次 hello 之后」读到的（总览重排审查 O5）。rd 没有 calib_valid_after（测试里的假对象）
    就不挡。不是 dict 的一律 None。"""
    s = getattr(rd, "calib", None)
    if s is None or not isinstance(s.value, dict) or s.at is None:
        return None
    after = getattr(rd, "calib_valid_after", None)
    if after is not None and s.at < after:
        return None
    return s.value
