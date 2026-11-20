"""Bounded camera warmup; only a stable, newly acquired frame reaches the model."""
import base64
import struct
import time
import urllib.request
import zlib
from .ai_connections import AiError


def brightness(data):
    # mediad emits 8-bit RGB PNG. Sample a small grid after undoing PNG filters.
    pos=8; packed=[]
    while pos<len(data):
        n=struct.unpack('>I',data[pos:pos+4])[0]; kind=data[pos+4:pos+8]; chunk=data[pos+8:pos+8+n];pos+=n+12
        if kind==b'IHDR':
            w,h,depth,color,_,_,interlace=struct.unpack('>IIBBBBB',chunk)
            if depth!=8 or color not in (2,6) or interlace:raise ValueError('unsupported PNG')
        elif kind==b'IDAT':packed.append(chunk)
        elif kind==b'IEND':break
    channels=3 if color==2 else 4; stride=w*channels
    raw=zlib.decompress(b''.join(packed),bufsize=(stride+1)*h)
    if len(raw)!=(stride+1)*h:raise ValueError('invalid PNG')
    prev=bytearray(stride);values=[]
    for y in range(h):
        offset=y*(stride+1);f=raw[offset];row=bytearray(raw[offset+1:offset+1+stride])
        for x in range(stride):
            a=row[x-channels] if x>=channels else 0;b=prev[x];c=prev[x-channels] if x>=channels else 0
            if f==1:v=a
            elif f==2:v=b
            elif f==3:v=(a+b)//2
            elif f==4:
                p=a+b-c;pa,pb,pc=abs(p-a),abs(p-b),abs(p-c);v=a if pa<=pb and pa<=pc else b if pb<=pc else c
            elif f==0:v=0
            else:raise ValueError('invalid filter')
            row[x]=(row[x]+v)&255
        if y%max(1,h//24)==0:
            for x in range(0,w,max(1,w//32)):
                i=x*channels;values.append((row[i]*299+row[i+1]*587+row[i+2]*114)/1000)
        prev=row
    return sum(values)/len(values)


class CameraSession:
    """Short local signalling session keeps camera demand alive across snapshots."""
    def __enter__(self):
        import socket,os,hashlib,json
        self.sock=socket.create_connection(('127.0.0.1',8443),timeout=5)
        self.sock.settimeout(5)
        key=base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET / HTTP/1.1\r\nHost: 127.0.0.1:8443\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+key+'\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        header=b''
        try:
            while not header.endswith(b'\r\n\r\n'):
                header+=self.read(1)
                if len(header)>8192:raise ValueError('handshake too large')
            expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            if b' 101 ' not in header or expected.lower() not in header.lower():raise ValueError('handshake failed')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                opcode,payload=self.receive()
                if opcode==9:self.send(payload,10);continue
                if opcode!=1:continue
                message=json.loads(payload)
                if message.get('type')=='welcome':self.send_json({'type':'list'})
                elif message.get('type')=='list':
                    self.send_json({'type':'startSession','peerId':message['producers'][0]['id']})
                elif message.get('type')=='sessionStarted':return self
                elif message.get('type')=='error':raise ValueError('camera session refused')
            raise TimeoutError()
        except BaseException:
            self.sock.close();raise
    def read(self,n):
        data=b''
        while len(data)<n:
            part=self.sock.recv(n-len(data))
            if not part:raise OSError('camera session closed')
            data+=part
        return data
    def receive(self):
        first,second=self.read(2);n=second&127
        if n==126:n=struct.unpack('>H',self.read(2))[0]
        elif n==127:n=struct.unpack('>Q',self.read(8))[0]
        if n>1024*1024:raise ValueError('oversized signalling')
        return first&15,self.read(n)
    def send(self,payload,opcode=1):
        import os
        mask=os.urandom(4);n=len(payload)
        header=bytes([128|opcode,128|n]) if n<126 else bytes([128|opcode,254])+struct.pack('>H',n)
        self.sock.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(payload)))
    def send_json(self,value):
        import json
        self.send(json.dumps(value).encode())
    def __exit__(self,*args):
        self.sock.close()


def capture():
    try:
        import json
        from pathlib import Path
        try:
            already_active=json.loads(Path("/run/mediad/capture.json").read_text()).get("active") is True
        except Exception:
            already_active=False
        with CameraSession():
            # Keep demand alive while AE converges; do not repeatedly cold-start.
            time.sleep(3 if already_active else 12)
            with urllib.request.urlopen('http://127.0.0.1:8080/frame',timeout=5) as response:
                data=response.read(4*1024*1024+1)
            if len(data)>4*1024*1024 or not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('invalid image')
            return 'data:image/png;base64,'+base64.b64encode(data).decode('ascii')
    except Exception:
        raise AiError('camera_unavailable','摄像头启动或取图失败，请稍后重试') from None
