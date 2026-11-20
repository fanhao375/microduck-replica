"""同一个端口同时接 http 和 https。

第一次开机生成证书以后，Studio 曾经只用 https 起：说明书、群里和大家收藏的
http://10.42.0.1:8000/ 全打不开（浏览器只看到「空回复」），家里 WiFi 上用 IP 打开的安卓手机
也进不来（证书不含 DHCP 地址）。https 只是手机麦克风需要，不该把原来的地址关掉。

做法：自己接连接，偷看第一个字节（MSG_PEEK，不取走）。TLS 握手的第一个字节固定是 0x16，
是它就套上证书，其余当明文 http。两种连接进的是同一个 uvicorn：一份应用、一份 lifespan，
协议层按连接自己认 http / https（uvicorn 看的是这条连接有没有 TLS），客户端地址也是真的。
"""
import asyncio
import socket
import ssl
import sys

import uvicorn
from uvicorn.config import SSL_PROTOCOL_VERSION, create_ssl_context

TLS_HANDSHAKE = 0x16      # TLS 记录类型 handshake：ClientHello 的第一个字节
PEEK_TIMEOUT_S = 10.0     # 连上不说话（浏览器预连接）最多等这么久，到点关掉，浏览器会自己重连
PEEK_FIRST_S = 0.002      # 没数据时隔多久再看一眼，逐次加倍
PEEK_MAX_S = 0.05


def load_tls(certfile, keyfile):
    """跟 uvicorn 自己起 https 时同一套默认参数；证书读不了 / 对不上私钥会抛 OSError 或 ssl.SSLError。"""
    return create_ssl_context(certfile=certfile, keyfile=keyfile, password=None, ssl_version=SSL_PROTOCOL_VERSION,
                              cert_reqs=ssl.CERT_NONE, ca_certs=None, ciphers="TLSv1")


class DualServer(uvicorn.Server):
    def __init__(self, config, tls, peek_timeout=PEEK_TIMEOUT_S):
        super().__init__(config)
        self.tls = tls
        self.peek_timeout = peek_timeout
        self.port = None            # 实际监听的端口（--listen 写 0 时测试要用）
        self._listener = None
        self._accepting = None
        self._waiting = set()       # 还没看出是哪种的连接

    async def startup(self, sockets=None):
        await self.lifespan.startup()
        if self.lifespan.should_exit:
            self.should_exit = True
            return
        config = self.config
        host = config.host or "0.0.0.0"
        try:
            self._listener = socket.create_server((host, config.port), backlog=config.backlog,
                                                  family=socket.AF_INET6 if ":" in host else socket.AF_INET)
        except OSError as exc:
            uvicorn.server.logger.error(exc)
            await self.lifespan.shutdown()
            sys.exit(1)
        self._listener.setblocking(False)
        self.port = self._listener.getsockname()[1]
        self.servers = []           # 父类关机时要遍历；监听口我们自己关
        self._accepting = asyncio.get_running_loop().create_task(self._accept())
        self.started = True

    def _protocol(self):
        return self.config.http_protocol_class(config=self.config, server_state=self.server_state,
                                               app_state=self.lifespan.state)

    async def _accept(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn, _ = await loop.sock_accept(self._listener)
            except OSError:
                await asyncio.sleep(0.2)    # 句柄用光之类：歇一下再接，别空转
                continue
            task = loop.create_task(self._hand_over(conn))
            self._waiting.add(task)
            task.add_done_callback(self._waiting.discard)

    async def _first_byte(self, conn):
        loop = asyncio.get_running_loop()
        end, delay = loop.time() + self.peek_timeout, PEEK_FIRST_S
        while True:
            try:
                data = conn.recv(1, socket.MSG_PEEK)
            except (BlockingIOError, InterruptedError):
                data = None
            if data is not None:
                return data[0] if data else None    # b"" = 对方没说话就挂了
            if loop.time() >= end:
                return None
            await asyncio.sleep(delay)
            delay = min(delay * 2, PEEK_MAX_S)

    async def _hand_over(self, conn):
        try:
            conn.setblocking(False)
            first = await self._first_byte(conn)
            if first is None:
                conn.close()
                return
            await asyncio.get_running_loop().connect_accepted_socket(
                self._protocol, conn, ssl=self.tls if first == TLS_HANDSHAKE else None)
        except asyncio.CancelledError:
            conn.close()
            raise
        except Exception:
            # 握手没成（手机还没装鸭子的证书、中途断开）很常见，不记日志
            conn.close()

    async def shutdown(self, sockets=None):
        if self._accepting is not None:
            self._accepting.cancel()
        for task in list(self._waiting):
            task.cancel()
        await asyncio.gather(*filter(None, [self._accepting, *self._waiting]), return_exceptions=True)
        if self._listener is not None:
            self._listener.close()
        await super().shutdown(sockets=sockets)
