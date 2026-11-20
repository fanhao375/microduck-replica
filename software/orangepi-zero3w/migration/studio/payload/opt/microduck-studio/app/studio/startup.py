"""robotd 启动四步的跟踪（调参方案 v2 §2.3、§5 T1；scratchpad console/tuning-plan.md）。

robotd 自己把启动分成四步，用 robot.startupSubscribe 推（fork robotd main.rs:4671-4686、1445-1456）：
    第一条回包 = 现在四步的样子：{"steps": {"1": {"status", "detail"}, ...}, "build": ...}
    之后有变化推通知 {"method": "robot.startup", "params": 同样的样子}；客户端发一行或断开就结束。
status 有 starting / waiting / success / failed。四步的原话在 main.rs:1205、1482、1773-1808、2184-2232、4137。

这里只做两件事：
- StartupTrack：跟踪结果（robotd.py 起的 startup_loop 写；tune.py、state.py 读）。每次 hello（robotd 重启了）作废重来；
  每步记下 Studio 第一次看到这个状态的时间（time.monotonic()：鸭子上是开机后的秒数）。
- startup_loop：robotd 在、这次 hello 的四步还没全成功时开一条连接订阅，四步都成功（或者有一步失败）就关。
  老版本 robotd 没有这个方法：记一句「不支持」，这次 hello 不再问。
- startup_todo：给总览待办补两句（state.py 调）：启动中写「第 N/4 步：原话」；「第 3 步成功但走路没加载」标红。

本模块不 import state.py（state.py 要 import 它）。
"""
import asyncio
import sys
import time

from .rpc import RpcError, Unreachable, encode, open_conn, read_message

STEPS = ("1", "2", "3", "4")
STEP_NAMES = {"1": "配置和控制线程", "2": "舵机总线和 IMU", "3": "加载模型", "4": "第一个控制周期"}
READ_TIMEOUT_S = 2.0          # 订阅里多久没消息就回头看一眼（robotd 重启了没有、Studio 要不要暂停）
RETRY_S = (1.0, 2.0, 5.0)
POLICY_OFF = "策略按配置关闭"   # fork main.rs:2229：第 3 步成功、但走路策略没加载（执行配置坏了时也是这句，审查 T-R1）


class StartupTrack:
    def __init__(self):
        self.epoch = None          # 属于哪一次 hello（rd.hello.at）
        self.steps = {}            # "1".."4" -> {"status", "detail", "at"}；at = Studio 第一次看到这个状态（monotonic）
        self.build = None
        self.done = False          # 四步都 success，或者有一步 failed：这次 hello 不用再订阅
        self.unsupported = None    # 老 robotd 没有 startupSubscribe：原因
        self.error = None          # 最近一次订阅出的错（还会重试）
        self.at = None             # 最近一次更新（monotonic）

    def reset(self, epoch):
        self.__init__()
        self.epoch = epoch

    def update(self, report, now=None):
        """收到一份四步的样子（回包或通知）。不是 dict 的不要。"""
        if not isinstance(report, dict):
            return
        now = time.monotonic() if now is None else now
        steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
        for k in STEPS:
            row = steps.get(k)
            if not isinstance(row, dict):
                continue
            status, detail = row.get("status"), row.get("detail")
            old = self.steps.get(k)
            if old and old["status"] == status and old["detail"] == detail:
                continue
            at = old["at"] if old and old["status"] == status else now
            self.steps[k] = {"status": status if isinstance(status, str) else None,
                             "detail": detail if isinstance(detail, str) else None, "at": at}
        if isinstance(report.get("build"), str):
            self.build = report["build"]
        self.at = now
        self.error = None
        self.done = (all((self.steps.get(k) or {}).get("status") == "success" for k in STEPS)
                     or any((self.steps.get(k) or {}).get("status") == "failed" for k in STEPS))

    def current(self):
        """(第几步, 那一步) —— 第一个不是 success 的；全成功回 (None, None)。"""
        for k in STEPS:
            row = self.steps.get(k)
            if not row or row.get("status") != "success":
                return k, row
        return None, None

    def policy_off(self):
        """第 3 步成功、原话是「策略按配置关闭」。"""
        row = self.steps.get("3") or {}
        return row.get("status") == "success" and POLICY_OFF in (row.get("detail") or "")

    def view(self, now=None):
        """推给页面的四行。at_s：鸭子上是开机后的秒数（monotonic 在 Linux 上从开机算）；电脑上没意义，boot_clock 说清楚。"""
        rows = []
        for k in STEPS:
            row = self.steps.get(k)
            rows.append({"n": int(k), "name": STEP_NAMES[k], "status": (row or {}).get("status"),
                         "detail": (row or {}).get("detail"),
                         "at_s": round(row["at"], 1) if row and row.get("at") is not None else None})
        n, row = self.current()
        return {"rows": rows, "known": bool(self.steps), "done": self.done, "now": int(n) if n else None,
                "unsupported": self.unsupported, "error": self.error, "build": self.build,
                "boot_clock": sys.platform.startswith("linux")}


async def startup_loop(rd):
    """robotd.py 起的后台任务（跟轮询一样由 _supervise 兜底）。只读：只发 robot.startupSubscribe。"""
    track = rd.startup
    fails = 0
    while True:
        if rd.addr == "none":
            return
        epoch = rd.hello.at
        if rd.paused or rd.present is not True or epoch is None:
            await asyncio.sleep(1.0)
            continue
        if track.epoch != epoch:
            track.reset(epoch)            # robotd 重新 hello 过（多半重启了）：旧的四步作废
        if track.done or track.unsupported:
            await asyncio.sleep(1.0)
            continue
        try:
            await _follow(rd, track, epoch)
            fails = 0
        except (Unreachable, OSError, asyncio.TimeoutError) as e:
            fails += 1
            track.error = f"订阅启动步骤没成：{e}"
            await asyncio.sleep(RETRY_S[min(fails - 1, len(RETRY_S) - 1)])


async def _follow(rd, track, epoch):
    r, w = await asyncio.wait_for(open_conn(rd.addr), 3.0)
    try:
        try:
            w.write(encode(1, "robot.startupSubscribe", {}))
            await w.drain()
        except (ConnectionError, OSError) as e:
            raise Unreachable(f"请求没发出去：{type(e).__name__}: {e}") from e
        while True:
            if rd.hello.at != epoch or rd.paused:
                return                    # robotd 重启了 / Studio 在调舵机：外面会重来
            try:
                msg = await read_message(r, READ_TIMEOUT_S)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                continue
            if msg.get("id") == 1:
                if "error" in msg:
                    err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
                    e = RpcError("robot.startupSubscribe", err.get("code"), err.get("message", ""))
                    track.unsupported = f"这一版主程序不报启动步骤（{e}）"
                    rd.log.info("主程序不报启动步骤（老版本没有 robot.startupSubscribe）", cat="robotd", raw=str(e))
                    return
                track.update(msg.get("result"))
            elif msg.get("method") == "robot.startup":
                track.update(msg.get("params"))
            else:
                continue
            _log_change(rd, track)
            if track.done:
                return
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001 —— 关连接时什么错都不重要
            pass


def _log_change(rd, track):
    """卡住、失败才记一行（成功的每一步 robotd 自己的 journal 里有）。同一句去重由日志模块做。"""
    n, row = track.current()
    if row and row.get("status") == "failed":
        rd.log.error(f"主程序启动第 {n}/4 步失败", cat="robotd", step=STEP_NAMES[n], raw=row.get("detail"),
                     hint="看日志页里 robotd 的那几行；导出诊断包发出来")
    elif not n and track.policy_off():
        rd.log.warn("主程序起来了，但走路策略没加载（第 3 步：策略按配置关闭）", cat="robotd", step="加载模型",
                    hint="多半是走路模型目录里的执行配置或合同不对；能站不能走")


def _hello_at(rd):
    return getattr(getattr(rd, "hello", None), "at", None)


def walk_unloaded(rd, pol=None, bus=None):
    """走路模型没加载的原因（人话），没问题回 None（审查 T-R1：第 3 步报成功、策略却关了，健康检查还可能是好的）。
    只在运动模式、控制中判；pol 是新鲜的 robot.policies（state.py 已经按 MAX_AGE 挑过）。"""
    if getattr(rd, "paused", None) or getattr(rd, "present", None) is False:
        return None                       # 调舵机 / 主程序不在：手里的回读是旧的
    bus = bus if isinstance(bus, dict) else (rd.bus.value if isinstance(rd.bus.value, dict) else None)
    if not bus or bus.get("mode") != "motion" or bus.get("phase") != "control":
        return None
    if isinstance(pol, dict) and pol.get("enabled") is False:
        return None                       # 配置里本来就关了策略（台架这么配）：不算坏
    walk_err = None
    if isinstance(pol, dict):
        for s in pol.get("slots") or []:
            if isinstance(s, dict) and s.get("slot") == "walk" and s.get("error"):
                walk_err = str(s["error"])
    lm = bus.get("loaded_models")
    track = getattr(rd, "startup", None)
    off = bool(track is not None and track.epoch == _hello_at(rd) and track.policy_off())
    missing = "loaded_models" in bus and (not isinstance(lm, dict) or not isinstance(lm.get("walk"), dict))
    if walk_err:
        return f"走路槽加载失败：{walk_err}"
    if missing or off:
        return "主程序说「策略按配置关闭」，走路模型没加载" if off else "主程序没有加载走路模型（loaded_models 里没有 walk）"
    return None


def startup_todo(rd, items, pol=None, bus=None):
    """state.py 的 todo() 算完以后再过一遍（调参方案 §3.2）：
    - 「鸭子主程序还在启动」那条，提示换成第几步的原话（原来写的 busStatus.error 原文接在后面）；
    - 补一条「走路模型没加载」（红）。"""
    try:
        track = getattr(rd, "startup", None)
        if track is not None and track.epoch is not None and track.epoch == _hello_at(rd):
            n, row = track.current()
            for it in items:
                if it.get("key") == "starting" and n and row and row.get("detail"):
                    raw = (bus or {}).get("error") if isinstance(bus, dict) else None
                    hint = f"第 {n}/4 步（{STEP_NAMES[n]}）：{row['detail']}"
                    if raw and raw not in row["detail"]:
                        hint += f"　原文：{raw}"
                    it["hint"] = hint
        why = walk_unloaded(rd, pol, bus)
        if why and not any(it.get("key") == "walk_unloaded" for it in items):
            items.insert(0, {"key": "walk_unloaded", "level": "error", "msg": "走路模型没加载",
                             "hint": f"{why}；能站不能走，看总览下面「运行和调参」"})
    except Exception:  # noqa: BLE001 —— 补一句提示，出错也不能把整个状态推送弄坏
        pass
    return items
