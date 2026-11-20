"""Acquire one current PNG from the local mediad session and snapshot service."""
import base64
import contextlib
import http.client
import json
import socket
import time
from pathlib import Path

from wsproto import ConnectionType, WSConnection
from wsproto.events import Ping, Request, TextMessage

from .ai_connections import AiError

FRAME_LIMIT = 4 * 1024 * 1024


@contextlib.contextmanager
def CameraSession(port):
    """Keep demand alive for one identified Microduck source through exposure warmup."""
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        protocol = WSConnection(ConnectionType.CLIENT)
        sock.sendall(protocol.send(Request(host=f'127.0.0.1:{port}', target='/')))
        deadline = time.monotonic() + 5
        size = 0
        fragments = []
        requested = False
        while deadline > time.monotonic():
            sock.settimeout(max(.001, deadline - time.monotonic()))
            data = sock.recv(8192)
            size += len(data)
            if not data or size > 1024 * 1024:
                raise ValueError('invalid camera signalling')
            protocol.receive_data(data)
            for event in protocol.events():
                if isinstance(event, Ping):
                    sock.sendall(protocol.send(event.response()))
                if not isinstance(event, TextMessage):
                    continue
                fragments.append(event.data)
                if not event.message_finished:
                    continue
                message = json.loads(''.join(fragments))
                fragments.clear()
                kind = message.get('type')
                if kind == 'welcome':
                    reply = {'type':'list'}
                elif kind == 'list' and not requested:
                    producers = [item for item in message['producers']
                                 if isinstance(item.get('id'), str) and 0 < len(item['id']) <= 256
                                 and isinstance(item.get('meta'), dict)
                                 and isinstance(item['meta'].get('release'), str)
                                 and str(item['meta'].get('api_version', '')).isdigit()
                                 and item['meta'].get('kind') in (None, 'microduck')]
                    if len(producers) != 1:
                        raise ValueError('no unique local camera')
                    requested = True
                    reply = {'type':'startSession', 'peerId':producers[0]['id']}
                elif kind == 'sessionStarted' and requested and message.get('sessionId'):
                    yield
                    return
                elif kind in ('error', 'sessionRejected', 'endSession'):
                    raise ValueError('camera session refused')
                else:
                    continue
                sock.sendall(protocol.send(TextMessage(data=json.dumps(reply))))
        raise TimeoutError('camera signalling timed out')


def capture(port=8443, frame_port=8080):
    try:
        try:
            state = json.loads(Path('/run/mediad/capture.json').read_text())
            already_active = state.get('active') is True
        except (OSError, ValueError, AttributeError):
            already_active = False
        with CameraSession(port):
            time.sleep(3 if already_active else 12)
            with contextlib.closing(http.client.HTTPConnection('127.0.0.1', frame_port, timeout=5)) as conn:
                conn.request('GET', '/frame')
                with conn.getresponse() as response:
                    if response.status != 200:
                        raise ValueError('snapshot unavailable')
                    data = response.read(FRAME_LIMIT + 1)
            if (len(data) > FRAME_LIMIT or not data.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
                    or not data.endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82')):
                raise ValueError('invalid snapshot')
            return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
    except Exception:
        raise AiError('camera_unavailable','摄像头启动或取图失败，请稍后重试') from None
