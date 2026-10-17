# -*- coding: utf-8 -*-
"""Minimal Dynamixel Protocol 2.0 client for the IMU board on a shared TTL bus.

The HD-1910 servos still use Feetech SCS. This module only addresses the IMU
device and shares FeetechBus' serial port and I/O lock, so the two protocols
can safely take turns on the same half-duplex wire.
"""
import struct
import time


HEADER = b"\xff\xff\xfd\x00"
PING, READ, SYNC_READ = 0x01, 0x02, 0x82
BROADCAST = 0xFE


def crc16(data):
    crc = 0
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x8005 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def packet(device_id, instruction, params=b""):
    body = bytes([instruction]) + bytes(params)
    stuffed = body.replace(b"\xff\xff\xfd", b"\xff\xff\xfd\xfd")
    frame = HEADER + bytes([device_id]) + struct.pack("<H", len(stuffed) + 2) + stuffed
    return frame + struct.pack("<H", crc16(frame))


class StatusParser:
    """Incremental parser for Dynamixel 2.0 status packets."""

    def __init__(self):
        self.buffer = bytearray()
        self.bad = 0

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(HEADER)
            if start < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            del self.buffer[:start]
            if len(self.buffer) < 7:
                break
            length = int.from_bytes(self.buffer[5:7], "little")
            if length < 4 or length > 1024:
                self.bad += 1
                del self.buffer[0]
                continue
            total = 7 + length
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            if crc16(raw[:-2]) != int.from_bytes(raw[-2:], "little"):
                self.bad += 1
                del self.buffer[0]
                continue
            del self.buffer[:total]
            stuffed_body = raw[7:-2]
            body = bytearray()
            i = 0
            valid = True
            while i < len(stuffed_body):
                body.append(stuffed_body[i])
                i += 1
                if body[-3:] == b"\xff\xff\xfd":
                    if i >= len(stuffed_body) or stuffed_body[i] != 0xFD:
                        valid = False
                        break
                    i += 1
            if not valid:
                self.bad += 1
                continue
            if len(body) >= 2 and body[0] == 0x55:
                frames.append((raw[4], body[1], bytes(body[2:]), raw))
        return frames


class Dxl2Bus:
    """Operate DXL2 IMU requests on the existing serial bus, under its lock."""

    def __init__(self, feetech_bus, timeout=0.06):
        self.bus = feetech_bus
        self.timeout = timeout

    def _transact(self, tx, expected_ids, expected_lengths):
        ser = self.bus.ser
        parser = StatusParser()
        replies = {}
        deadline = time.monotonic() + self.timeout
        with self.bus._io:
            ser.reset_input_buffer()
            if ser.write(tx) != len(tx):
                raise OSError("DXL2 串口数据未完整发送")
            while time.monotonic() < deadline:
                chunk = ser.read(min(max(getattr(ser, "in_waiting", 0), 1), 2048))
                if not chunk:
                    continue
                for sid, error, payload, raw in parser.feed(chunk):
                    if sid not in expected_ids or sid in replies:
                        continue
                    if error == 0 and len(payload) != expected_lengths[sid]:
                        continue
                    replies[sid] = (error, payload)
                if len(replies) == len(expected_ids):
                    break
        if getattr(self.bus, "trace", None):
            from feetech import hexs
            self.bus.trace(f"→ DXL2 {hexs(tx)}")
            for sid, (error, payload) in replies.items():
                if error:
                    self.bus.trace(f"← DXL2 #{sid} status_error=0x{error:02x} {hexs(payload)}")
                else:
                    self.bus.trace(f"← DXL2 #{sid} {hexs(payload)}")
            if not replies:
                self.bus.trace("← DXL2 无有效状态包")
        return replies

    def ping(self, device_id):
        replies = self._transact(packet(device_id, PING), {device_id}, {device_id: 3})
        return replies.get(device_id)

    def read(self, device_id, address, length):
        params = struct.pack("<HH", address, length)
        replies = self._transact(packet(device_id, READ, params), {device_id}, {device_id: length})
        return replies.get(device_id)

    def sync_read(self, ids, address, length):
        ids = list(ids)
        if not ids:
            return {}
        params = struct.pack("<HH", address, length) + bytes(ids)
        return self._transact(packet(BROADCAST, SYNC_READ, params), set(ids),
                              {sid: length for sid in ids})
