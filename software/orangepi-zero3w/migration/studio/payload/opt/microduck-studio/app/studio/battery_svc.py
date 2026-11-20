"""电池估算挂进 Studio（battery-plan.md v2 §6 B2/B3）：一个 1 Hz 的循环喂估算器，summarize 只读结果，页面的 battery.op。

为什么单独一个循环（审查 B-R7）：summarize 每个页面每秒跑 10 次，在那里喂样本，开三个页面就是每秒 30 个重复值。
这里一秒一拍，按 busStatus 的采样时间（Sample.at）去重；调舵机时（Studio 自己占着总线，robotd 按计划停着）
从 servo.snapshot() 的每颗电压取，同一个寄存器，切换时不重新起步。

设置存 state_dir/battery-settings.json（studio/battery.py 读写、校验）；改了马上换掉估算器的设置，下一拍生效。
只警告：这里不发任何命令给鸭子。
"""
import asyncio
import os
import time

from . import battery as B
from .state import JOINT_CN, MAX_AGE

TICK_S = 1.0
BUS_STALE_S = 3.0       # busStatus 这么久没更新：这一拍当没有新数据（10 秒以后估算器自己重新起步）
# 开发用（端到端测试、截图）：估算器的时间按这个倍数快进（30 秒估算中、60 秒才亮在 20 倍下是 1.5 秒、3 秒）。
# 快进时每一拍都把最新的那份读数当新样本喂（不按 busStatus 的采样时间去重）。鸭子上不要设
SPEED_ENV = "STUDIO_BATTERY_SPEED"


class BatteryService:
    def __init__(self, robotd, log, state_dir=None, servo=None, onboard=None, speed=None):
        self.rd, self.log, self.state_dir, self.servo, self.onboard = robotd, log, state_dir, servo, onboard
        self.settings = B.load_settings(state_dir, log)
        self.est = B.Estimator(self.settings)
        self.names = None
        self.errors = 0
        if speed is None:
            try:
                speed = float(os.environ.get(SPEED_ENV) or 1.0)
            except ValueError:
                speed = 1.0
        self.speed = max(1.0, speed)
        self._t0 = time.monotonic()
        if self.speed != 1.0:
            log.warn(f"开发用：电量估算按 {self.speed:g} 倍速快进", cat=B.LOG_CAT, raw=f"{SPEED_ENV}={self.speed:g}",
                     hint="只给端到端测试、截图用；鸭子上不要设这个环境变量")

    def clock(self, now=None):
        """估算器用的时间（快进时按倍数放大）。"""
        now = time.monotonic() if now is None else now
        return self._t0 + (now - self._t0) * self.speed

    # ---- 一拍 ----

    def sample(self, now):
        """这一拍的输入：(电压, fresh, cls, key, source, 名字) 或 None（没有新数据）。"""
        sv = self.servo
        if sv is not None and getattr(sv, "state", None) == "open":
            snap = sv.snapshot()
            rows = snap.get("rows") if isinstance(snap, dict) else None
            vs, fr = B.servo_session_volts(rows)
            if vs is None:
                return None
            names = [r.get("cn") if isinstance(r, dict) else None for r in rows]
            # 舵机表没有采样时间：按秒去重（估算器在没给 key 时也会按时间去重）
            return vs, fr, B.classify_servo_session(rows), ("servo", int(now)), "servo", names
        bus = self.rd.bus.value
        age = self.rd.bus.age(now)
        if bus is None or age is None or age > BUS_STALE_S:
            return None
        vs, fr = B.robotd_volts(bus)
        if vs is None:
            return None
        st_age = self.rd.state.age(now)
        st = self.rd.state.value if st_age is not None and st_age <= MAX_AGE["state"] else None
        joints = ((bus.get("native") or {}).get("joints") or []) if isinstance(bus.get("native"), dict) else []
        names = [JOINT_CN.get(j.get("name"), j.get("name")) if isinstance(j, dict) else None for j in joints]
        cls = B.classify_robotd(bus, st) if st is not None else None
        if cls is None and st is None and isinstance(bus.get("native"), dict):
            # 状态流没在推（没人看页面时 robotd 不推 robot.state）：只看 busStatus。卸力就是卸力；
            # 扭矩开着、策略没开 = 保持 HOME（不会走）；策略开着可能在走，算不知道
            torque = bus["native"].get("torque_state_confirmed")
            if torque is False:
                cls = "rest"
            elif torque is True and bus.get("policy_enabled") is False:
                cls = "light"
        return vs, fr, cls, ("bus", self.rd.bus.at), "robotd", names

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        s = self.sample(now)
        t = self.clock(now)
        if s is None:
            events = self.est.feed(t, None)
        else:
            vs, fr, cls, key, source, names = s
            self.names = names
            events = self.est.feed(t, vs, fr, cls, key=key if self.speed == 1.0 else None, source=source)
        B.log_events(self.log, events)
        return events

    def view(self, now=None):
        """summarize 用（每秒 10 次 × 每个页面）：只读内存，不算新样本。"""
        now = time.monotonic() if now is None else now
        h = self.rd.health
        health = h.value if h.age(now) is not None and h.age(now) <= MAX_AGE["health"] else None
        rd = (health or {}).get("battery") if isinstance(health, dict) else None
        shutdown = getattr(self.onboard, "battery_shutdown", None) if self.onboard is not None else None
        return self.est.view(self.clock(now), rd=rd, names=self.names, shutdown=shutdown)

    async def loop(self):
        while True:
            await asyncio.sleep(TICK_S / self.speed)
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                self.log.error("电量估算出错", cat=B.LOG_CAT, raw=f"{type(e).__name__}: {e}", exc=True,
                               hint="这是 Studio 的问题，导出诊断包发出来；不影响站和走")
                await asyncio.sleep(5.0)

    # ---- 页面的 battery.op ----

    async def handle(self, args, by="?"):
        """{op:"get"} → 设置 + 说明；{op:"set", values:{…}} → 校验、存盘、马上生效；
        {op:"calibrate", measured_v} → 算出读数校准（不存，页面确认后再 set）。不合法的抛 ValueError（原因给人看）。"""
        op = (args or {}).get("op")
        if op == "get":
            return self._payload()
        if op == "set":
            values = (args or {}).get("values")
            good, errors = B.validate_settings(values, self.settings)
            if errors:
                raise ValueError("；".join(errors))
            old = dict(self.settings)
            if good == old:
                return self._payload()
            try:
                saved = await asyncio.to_thread(B.save_settings, self.state_dir, good)
            except B.SettingsError as e:
                self.log.warn("电池设置没存进去", cat=B.LOG_CAT, raw=str(e), hint="state 目录满了或者没权限")
                raise ValueError(str(e)) from None
            self.settings = saved
            self.est.set_settings(saved)
            self.log.info(f"电池设置改了：{B.settings_changes(old, saved)}", cat=B.LOG_CAT, raw=f"by={by}")
            return self._payload()
        if op == "calibrate":
            mv = (args or {}).get("measured_v")
            r = self.est.calibrate(self.clock(), mv)
            if r["ok"]:
                self.log.info(f"「我量了电池电压」：{r['reason']}（还没存，等确认）", cat=B.LOG_CAT, raw=f"by={by}")
            return dict(r, current=self.settings["cal_offset_v"])
        raise ValueError(f"不认识的电池操作：{op}")

    def _payload(self):
        return {"settings": dict(self.settings), "defaults": dict(B.DEFAULTS), "ranges": {k: list(v) for k, v in B.RANGES.items()},
                "presets": dict(B.PRESETS), "path": B.settings_path(self.state_dir)}
