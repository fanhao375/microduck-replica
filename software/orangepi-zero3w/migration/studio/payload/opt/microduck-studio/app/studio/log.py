"""Studio 的日志：分类、分级、落盘，出错写明卡在哪一步，帮人定位问题。

每条日志去三个地方：
- 文件 `logs/studio-日期.log`：一行一条，时间到毫秒带时区，出错带 traceback 和现场。
  出问题时这个文件就是现场：导出诊断包、发群里、或者让 Claude 直接读，都从它开始；
- 内存里最近 500 条：页面打开时先拿这些，之后实时推；
- 标准输出：systemd 收进 journal（`journalctl -u microduck-studio`）。

一条错误怎么写才算清楚：
    log.error("站起来没成功", cat="robotd", step="robot.init",
              raw="bus not ready: waiting_for_bus", hint="鸭子主程序还在启动，等 10 秒再试",
              ctx={"mode": "motion", "phase": "waiting_for_bus"})
文件里是一行：…[robotd][错误] 站起来没成功 · 卡在：robot.init · 原文：… · 建议：… · 现场：{…}
页面上显示「站起来没成功 · 卡在 robot.init」，展开能看到原文、建议和现场。

日志按「每条命令记请求、回执和耗时」来做，代码是自己写的；
出错时把当时的状态和 robotd 的系统日志一起留下；密码之类的自动打码；时间到毫秒。
一个 TOF 连接反复断连能刷 8700 多行，把有用的几条淹没，所以这里同样的事
1 分钟内只记一次，后面合并计数。

SD 卡寿命：只留最近 14 天、总共不超过 50 MB；调试级别默认不落盘；写文件在后台线程，
不卡事件循环。
"""
import asyncio
import json
import os
import queue
import re
import threading
import time
import traceback
from collections import deque
from pathlib import Path

CATS = ["系统", "连接", "网络", "robotd", "命令", "安全", "总线", "校准", "IMU", "手柄", "页面", "电池", "调参"]
LEVELS = ["debug", "info", "warn", "error"]
_TAG = {"debug": "[调试]", "info": "", "warn": "[警告]", "error": "[错误]"}

KEEP_DAYS = 14
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024    # 单个文件（一天）超过这么大，只再记警告和错误
MAX_SECRETS = 64
RING = 500
DEDUP_WINDOW_S = 60
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# 按键名打码：这些键的值永远不会进日志（家里 WiFi 的 psk 就是走这条）
SECRET_KEYS = frozenset({"psk", "password", "passwd", "secret", "token", "authorization"})


def redact(value):
    """把字典里敏感键的值换成「[已隐藏]」；嵌套的字典、列表也照做（原文是 JSON 的也走这里）。"""
    if isinstance(value, dict):
        return {k: ("[已隐藏]" if isinstance(k, str) and k.lower() in SECRET_KEYS else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _stamp(ts):
    """2026-10-18T09:41:07.123+08:00"""
    lt = time.localtime(ts)
    off = time.strftime("%z", lt)
    return time.strftime("%Y-%m-%dT%H:%M:%S", lt) + f".{int(ts * 1000) % 1000:03d}" + (off[:3] + ":" + off[3:] if off else "")


class Log:
    def __init__(self, directory, prefix="studio", echo=True, background=True):
        self.dir = Path(directory)
        self.prefix = prefix
        self.echo = echo
        self.debug_to_file = False
        self._lock = threading.Lock()
        self._ring = deque(maxlen=RING)
        self._n = 0
        self._day = None
        self._day_bytes = 0
        self._capped = False
        self._listeners = set()      # (loop, queue)：页面的 WebSocket 订阅
        self._secrets = set()
        self._seen = {}              # (cat, level, msg) -> [第一次的时间, 之后又来了几次]
        self.counts = {lv: 0 for lv in LEVELS}
        self.write_errors = 0        # 写日志本身出错的次数（诊断包里能看到）
        self._q = queue.SimpleQueue() if background else None
        if background:
            threading.Thread(target=self._writer, name="studio-log", daemon=True).start()

    # ---- 写 ----

    def hide(self, value):
        """以后日志里出现这个字符串，一律换成「[已隐藏]」（密码、令牌）。"""
        if value and len(value) >= 4:
            with self._lock:
                if len(self._secrets) < MAX_SECRETS:
                    self._secrets.add(str(value))

    def write(self, msg, cat="系统", level="info", step=None, raw=None, hint=None, ctx=None,
              exc=False, dedup=True):
        """cat 见 CATS；level 见 LEVELS。
        step：卡在哪一步（接口名、流程的第几步）；raw：原始报错或返回原文；hint：建议怎么办；
        ctx：现场（当时的模式、姿势、电量……，字典）；exc=True 把当前异常的 traceback 写进文件；
        dedup=False：每次都要记（比如命令的请求和回执）。

        **永远不抛异常**：卸力在发出之前要先记一行，记日志出错不能把卸力挡住（复核 #1）。"""
        try:
            return self._write(msg, cat, level, step, raw, hint, ctx, exc, dedup)
        except Exception:  # noqa: BLE001
            self.write_errors += 1
            return None

    def _write(self, msg, cat, level, step, raw, hint, ctx, exc, dedup):
        if level not in LEVELS:
            level = "info"
        now = time.time()
        msg, raw, hint, step = (self._clean(x) for x in (msg, raw, hint, step))
        tb = self._clean(traceback.format_exc()) if exc else ""
        ctx_text = self._clean(json.dumps(redact(ctx), ensure_ascii=False, default=str)) if ctx else None
        with self._lock:
            if dedup:
                key = (cat, level, msg)
                seen = self._seen.get(key)
                if seen and now - seen[0] < DEDUP_WINDOW_S:
                    seen[1] += 1
                    return None
                if seen and seen[1]:
                    self._emit_locked(now, cat, level, f"（上一条「{msg}」在 1 分钟内又出现了 {seen[1]} 次，已合并）")
                self._seen[key] = [now, 0]
                if len(self._seen) > 2000:
                    self._seen = {k: v for k, v in self._seen.items() if now - v[0] < DEDUP_WINDOW_S}
            return self._emit_locked(now, cat, level, msg, step, raw, hint, ctx_text, tb)

    def _emit_locked(self, now, cat, level, msg, step=None, raw=None, hint=None, ctx=None, tb=""):
        parts = [f"{_stamp(now)} [{cat}]{_TAG[level]} {msg}"]
        if step:
            parts.append(f"卡在：{step}" if level in ("warn", "error") else f"步骤：{step}")
        if raw:
            parts.append(f"原文：{raw}")
        if hint:
            parts.append(f"建议：{hint}")
        if ctx:
            parts.append(f"现场：{ctx}")
        line = " · ".join(parts)
        self.counts[level] += 1
        if level != "debug" or self.debug_to_file:
            self._to_file(now, line + "\n" + tb, level in ("warn", "error"))
        entry = None
        if level != "debug":
            self._n += 1
            entry = {"n": self._n, "ts": now, "t": time.strftime("%H:%M:%S", time.localtime(now)),
                     "cat": cat, "level": level, "msg": msg, "step": step, "raw": raw, "hint": hint, "ctx": ctx}
            self._ring.append(entry)
            for loop, q in list(self._listeners):
                try:
                    loop.call_soon_threadsafe(_offer, q, entry)
                except RuntimeError:      # 事件循环已经关了
                    pass
        if self.echo:
            try:
                # 标准输出可能已经写不进（SSH 窗口关了、journald 卡住）：丢掉这份，文件和页面那份还在
                print(line + ("\n" + tb.rstrip() if tb else ""), flush=True)
            except Exception:  # noqa: BLE001
                self.write_errors += 1
        return entry

    def debug(self, msg, **kw):
        return self.write(msg, level="debug", **kw)

    def info(self, msg, **kw):
        return self.write(msg, level="info", **kw)

    def warn(self, msg, **kw):
        return self.write(msg, level="warn", **kw)

    def error(self, msg, **kw):
        return self.write(msg, level="error", **kw)

    # ---- 读 ----

    def recent(self, limit=200, cats=None, min_level="info"):
        floor = LEVELS.index(min_level) if min_level in LEVELS else 1
        with self._lock:
            items = [e for e in self._ring
                     if LEVELS.index(e["level"]) >= floor and (not cats or e["cat"] in cats)]
        return items[-limit:]

    def subscribe(self, loop=None, maxsize=200):
        """页面实时日志：返回一个 asyncio.Queue，满了就丢最旧的（页面慢不拖累写日志的人）。"""
        loop = loop or asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._listeners.add((loop, q))
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._listeners = {(lp, qq) for lp, qq in self._listeners if qq is not q}

    def files(self, days=2):
        """诊断包用：最近 days 天的日志文件，新的在后。"""
        self.flush()
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob(f"{self.prefix}-*.log"))[-days:]

    def snapshot(self):
        with self._lock:
            return {"counts": dict(self.counts), "last_n": self._n, "dir": str(self.dir),
                    "write_errors": self.write_errors}

    def flush(self, timeout=2.0):
        """等后台线程把排队的行写完（导出诊断包、退出前用）。"""
        if self._q is None:
            return
        done = threading.Event()
        self._q.put(done)
        done.wait(timeout)

    # ---- 文件 ----

    def _clean(self, text):
        if text is None:
            return None
        text = _ANSI.sub("", str(text))
        for s in self._secrets:
            text = text.replace(s, "[已隐藏]")
        return text

    def _to_file(self, now, text, important=False):
        if self._q is not None:
            self._q.put((now, text, important))
        else:
            self._append(now, text, important)

    def _writer(self):
        while True:
            item = self._q.get()
            if isinstance(item, threading.Event):
                item.set()
                continue
            self._append(*item)

    def _append(self, now, text, important=False):
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{self.prefix}-{day}.log"
            if day != self._day:
                self._day = day
                self._prune()
                self._day_bytes = _size(path)
                self._capped = False
            if self._day_bytes > MAX_FILE_BYTES:
                # 刷屏保护：今天已经写了 20 MB，普通的行不再写进文件（页面上照常），警告和错误照写
                if not important:
                    if not self._capped:
                        self._capped = True
                        text = f"（今天的日志超过 {MAX_FILE_BYTES >> 20} MB，后面只记警告和错误）\n"
                    else:
                        return
                elif self._day_bytes > MAX_FILE_BYTES * 2:
                    return
            data = text.encode("utf-8")
            with open(path, "ab") as f:
                f.write(data)
            self._day_bytes += len(data)
        except OSError:
            # 写不进文件（卡满、只读）也不能让调用方出错；标准输出那份还在
            pass

    def _prune(self):
        """只留最近 KEEP_DAYS 天，总量超过 MAX_TOTAL_BYTES 时从最旧的删起（今天的不删）。"""
        logs = sorted(self.dir.glob(f"{self.prefix}-*.log"))
        today = self.dir / f"{self.prefix}-{self._day}.log"
        old = [p for p in logs if p != today]
        excess = len(old) - (KEEP_DAYS - 1)
        for p in old[:max(excess, 0)]:
            _unlink(p)
        logs = sorted(self.dir.glob(f"{self.prefix}-*.log"))
        total = sum(_size(p) for p in logs)
        for p in logs:
            if total <= MAX_TOTAL_BYTES:
                break
            if p == today:      # 钟停在 1970 时今天的文件排在最前：跳过它接着删，不能就此停下
                continue
            total -= _size(p)
            _unlink(p)


def _offer(q, entry):
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(entry)


def _size(p):
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _unlink(p):
    try:
        p.unlink()
    except OSError:
        pass


class Timer:
    """记一条命令的来回：
        with log_cmd(log, "站起来", "robot.init", ctx=...) as t:
            reply = await ...
            t.ok(reply)        # 或 t.fail("原因", raw=..., hint=...)
    请求、回执、耗时各一行（不合并），失败时带现场。"""

    def __init__(self, log, what, step, who=None, args=None, ctx=None):
        self.log, self.what, self.step, self.ctx = log, what, step, ctx
        self.t0 = time.monotonic()
        self.done = False
        detail = f"（{who}）" if who else ""
        self.log.write(f"→ {what}{detail}", cat="命令", step=step,
                       raw=json.dumps(redact(args), ensure_ascii=False) if args else None, dedup=False)

    def ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def ok(self, reply=None, note=""):
        self.done = True
        self.log.write(f"← {self.what} 成功 · {self.ms()} ms{(' · ' + note) if note else ''}", cat="命令",
                       step=self.step, raw=_brief(reply), dedup=False)

    def fail(self, why, raw=None, hint=None, ctx=None):
        self.done = True
        self.log.write(f"← {self.what} 没成功：{why} · {self.ms()} ms", cat="命令", level="error",
                       step=self.step, raw=_brief(raw), hint=hint, ctx=ctx or self.ctx, dedup=False)

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is not None and not self.done:
            self.fail(f"{et.__name__}: {ev}")
        elif not self.done:
            self.ok()
        return False


def _brief(x, limit=600):
    if x is None:
        return None
    s = x if isinstance(x, str) else json.dumps(redact(x), ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"…（共 {len(s)} 字）"


def log_cmd(log, what, step, **kw):
    return Timer(log, what, step, **kw)


def to_json(entry):
    return json.dumps(entry, ensure_ascii=False)


_default = None


def setup(directory, **kw):
    """进程里只建一个；各模块 `from studio.log import get; log = get()`。"""
    global _default
    _default = Log(directory, **kw)
    return _default


def get():
    if _default is None:
        return setup(os.path.join(os.getcwd(), "logs"))
    return _default
