"""robotd / configd 的 JSON-RPC 客户端。

线上格式（duck-ipc-proto/src/lib.rs 开头）：JSON-RPC 2.0，一行一个对象（NDJSON）。
    → {"jsonrpc":"2.0","id":1,"method":"robot.health","params":{}}
    ← {"jsonrpc":"2.0","id":1,"result":{...}}
没有 id 的是通知（订阅的 robot.state 就是）。

robotd 每条连接**一次只处理一个请求**：读一行、答一行。所以：
- 一个 Conn 同一时间只有一个请求在等回包（用锁排队）；
- 超时了就把这条连接关掉，下次用时重连。不关的话，超时的那个请求会一直挡在前面，
  晚到的回包也对不上号；
- 卸力单独占一条连接（见 robotd.py），不跟慢请求排队。

地址统一写成字符串，其余代码不用管走的是哪种：
    unix:/run/robotd.sock      鸭子上
    tcp:127.0.0.1:17001        电脑上（Windows 的 Python 没有 AF_UNIX），连假 robotd

「主程序不在」和「答得慢」要分开：socket 文件不在、拒绝连接 → Unreachable；
连上了但没按时回 → Timeout。界面上一个写「主程序不在」，一个写「主程序没回话」。
"""
import asyncio
import itertools
import json
import os

LINE_LIMIT = 1 << 20      # busStatus 实测 16.9 KB；默认 64 KiB 的上限迟早会撞，放到 1 MiB


class RpcError(Exception):
    """对方回了 error。code 见 lib.rs 的 error_code（-32602 参数不对、1 忙……）。"""

    def __init__(self, method, code, message, data=None):
        super().__init__(f"{method}：{message}（code {code}）")
        self.method, self.code, self.message, self.data = method, code, message, data


class Unreachable(Exception):
    """连不上：socket 不在、拒绝连接、连接被对方关掉。"""


class Timeout(Exception):
    """连上了，但没按时回话。"""


def parse_addr(addr):
    if addr.startswith("unix:"):
        return ("unix", addr[5:])
    if addr.startswith("tcp:"):
        host, _, port = addr[4:].rpartition(":")
        return ("tcp", host or "127.0.0.1", int(port))
    raise ValueError(f"地址要写成 unix:/路径 或 tcp:主机:端口，现在是 {addr!r}")


async def open_conn(addr):
    kind, *rest = parse_addr(addr)
    try:
        if kind == "unix":
            path = rest[0]
            if not hasattr(asyncio, "open_unix_connection"):
                raise Unreachable("这台电脑的 Python 不支持 unix socket，地址改用 tcp:")
            if not os.path.exists(path):
                raise Unreachable(f"{path} 不在（主程序没在跑）")
            return await asyncio.open_unix_connection(path, limit=LINE_LIMIT)
        host, port = rest
        return await asyncio.open_connection(host, port, limit=LINE_LIMIT)
    except Unreachable:
        raise
    except (ConnectionRefusedError, FileNotFoundError) as e:
        raise Unreachable(f"连不上 {addr}：拒绝连接") from e
    except OSError as e:
        raise Unreachable(f"连不上 {addr}：{type(e).__name__}: {e}") from e


def encode(rid, method, params):
    return (json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
                       ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


async def read_message(reader, timeout):
    """读一行、解成 JSON。超长行、对方关连接都算 Unreachable；乱码返回 None（调用方跳过）。"""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout)
    except asyncio.TimeoutError:
        raise
    except (ValueError, asyncio.LimitOverrunError) as e:      # 一行超过 LINE_LIMIT
        raise Unreachable(f"对方发来的一行太长（超过 {LINE_LIMIT} 字节）") from e
    except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
        raise Unreachable(f"连接断了：{type(e).__name__}: {e}") from e
    if not raw:
        raise Unreachable("对方关了连接")
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None      # [1,2,3]、"x" 这类也当乱码跳过


class Conn:
    """一条串行连接。call() 排队执行；超时、断线都会关掉连接，下次 call() 自动重连。"""

    def __init__(self, addr, name="rpc", log=None):
        self.addr, self.name, self.log = addr, name, log
        self._r = self._w = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self.connected = False
        self.reconnects = 0
        self.bad_lines = 0

    @property
    def busy(self):
        return self._lock.locked()

    async def call(self, method, params=None, timeout=2.0):
        """timeout 包括建连（robotd 卡住时连接可能一直挂着）。"""
        async with self._lock:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            if self._w is None:
                try:
                    await asyncio.wait_for(self._connect(), timeout)
                except asyncio.TimeoutError:
                    self._close()
                    raise Timeout(f"{method}：{timeout:g} 秒没连上") from None
            rid = next(self._ids)
            try:
                self._w.write(encode(rid, method, params))
                await self._w.drain()
                while True:
                    left = deadline - loop.time()
                    if left <= 0:
                        raise asyncio.TimeoutError
                    msg = await read_message(self._r, left)
                    if msg is None:
                        self.bad_lines += 1
                        continue
                    if msg.get("id") == rid:
                        break
                    # 通知、或者上一次超时请求的迟到回包：丢掉
            except asyncio.TimeoutError:
                self._close()
                raise Timeout(f"{method} {timeout:g} 秒没回话") from None
            except Unreachable:
                self._close()
                raise
            except (ConnectionError, OSError) as e:
                self._close()
                raise Unreachable(f"连接断了：{type(e).__name__}: {e}") from e
            except asyncio.CancelledError:
                # 调用方被取消时请求可能已经发出：robotd 还会按顺序处理它、回一个迟到的包。
                # 关掉连接，后面的请求不会排在它后面，也不会读到它的回包
                self._close()
                raise
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
            raise RpcError(method, err.get("code"), err.get("message", ""), err.get("data"))
        return msg.get("result")

    async def _connect(self):
        self._r, self._w = await open_conn(self.addr)
        if self.connected is False and self.reconnects:
            # 措辞固定、次数放在原文里：1 分钟内反复重连会被去重合并，不会刷屏
            self._note("info", f"跟主程序的 {self.name} 连接恢复", raw=f"第 {self.reconnects} 次重连")
        self.connected = True

    def _close(self):
        if self._w is not None:
            try:
                self._w.close()
            except Exception:  # noqa: BLE001 —— 关连接时什么错都不重要
                pass
        self._r = self._w = None
        if self.connected:
            self.reconnects += 1
        self.connected = False

    def close(self):
        self._close()

    def _note(self, level, msg, raw=None):
        if self.log:
            self.log.write(msg, cat="robotd", level=level, raw=raw)


async def call_once(addr, method, params=None, timeout=2.0, name="once", log=None):
    """新开一条连接、问一次、关掉。卸力用它：不留长连接，就不会撞上 robotd 重启后已经死掉的旧连接。
    本机 socket 建连不到 1 毫秒。"""
    c = Conn(addr, name, log)
    try:
        return await c.call(method, params, timeout)
    finally:
        c.close()
