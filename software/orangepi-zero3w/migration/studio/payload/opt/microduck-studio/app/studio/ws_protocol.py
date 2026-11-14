"""Studio's wsproto adapter: optional native liveness for background music.

The dispatch table matches the pinned Uvicorn WSProtocol, adding matched Pong
handling. Ordinary ASGI message delivery and client Ping handling stay inherited.
"""
import asyncio
import secrets

from uvicorn.protocols.websockets.wsproto_impl import WSProtocol
from wsproto import events
from wsproto.utilities import LocalProtocolError

PING_EXTENSION = 'studio.websocket.ping'


class MusicWebSocketProtocol(WSProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pong = None

    def handle_connect(self, event):
        super().handle_connect(event)
        self.scope['extensions'][PING_EXTENSION] = self.ping

    def handle_events(self):
        # Keep parity with the pinned parent's handlers; Pong is the only addition.
        for event in self.conn.events():
            if isinstance(event, events.Request):
                self.handle_connect(event)
            elif isinstance(event, events.TextMessage):
                self.handle_text(event)
            elif isinstance(event, events.BytesMessage):
                self.handle_bytes(event)
            elif isinstance(event, events.CloseConnection):
                self.handle_close(event)
            elif isinstance(event, events.Ping):
                self.handle_ping(event)
            elif isinstance(event, events.Pong) and self._pong is not None:
                nonce, future = self._pong
                if event.payload == nonce and not future.done():
                    future.set_result(True)

    async def ping(self, timeout):
        if self._pong is not None or self.transport.is_closing():
            return False
        nonce, future = secrets.token_bytes(16), self.loop.create_future()
        pending = self._pong = (nonce, future)
        try:
            async with asyncio.timeout(timeout):
                await self.writable.wait()
                if self.transport.is_closing():
                    return False
                self.transport.write(self.conn.send(events.Ping(payload=nonce)))
                return await future
        except (TimeoutError, LocalProtocolError, OSError):
            return False
        finally:
            if self._pong is pending:
                self._pong = None
            if not future.done():
                future.cancel()

    def _finish_ping(self):
        if self._pong is not None and not self._pong[1].done():
            self._pong[1].set_result(False)

    def connection_lost(self, exc):
        self._finish_ping()
        super().connection_lost(exc)

    def shutdown(self):
        self._finish_ping()
        super().shutdown()
