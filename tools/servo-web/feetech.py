# -*- coding: utf-8 -*-
"""飞特 STS/SMS 总线的最小实现：包格式、读写、同步读写、重启。

对照 docs/飞特资料/SCS通信协议-v1.0-2026-06-09.md 和 磁编码STS内存表手册-v1.1-2026-08-27.md 写的，
不依赖飞特 SDK。字节序按磁编码系列：小端。
"""
import struct
import threading
import time

import serial

PING, READ, WRITE, REG_WRITE, ACTION = 0x01, 0x02, 0x03, 0x04, 0x05
RECOVERY, REBOOT, BACKUP, RESET_TURNS, CALIBRATE = 0x06, 0x08, 0x09, 0x0A, 0x0B
SYNC_READ, SYNC_WRITE = 0x82, 0x83
INSTR_NAMES = {0x01: "PING", 0x02: "READ", 0x03: "WRITE", 0x04: "REG_WRITE", 0x05: "ACTION",
               0x06: "参数恢复", 0x08: "REBOOT", 0x09: "参数备份", 0x0A: "清圈数", 0x0B: "位置校准",
               0x82: "SYNC_READ", 0x83: "SYNC_WRITE"}
BROADCAST = 0xFE

# 内存表：地址 -> (名字, 字节数, 区)。给寄存器导出用。
REGISTERS = {
    0: ("固件主版本", 1, "只读"), 1: ("固件次版本", 1, "只读"), 2: ("END 字节序(0=小端)", 1, "只读"),
    3: ("舵机主版本", 1, "只读"), 4: ("舵机次版本", 1, "只读"),
    5: ("ID", 1, "EEPROM"), 6: ("波特率(0=1M)", 1, "EEPROM"), 7: ("预留地址（STS 无应答延时寄存器）", 1, "EEPROM"),
    8: ("应答状态级别", 1, "EEPROM"), 9: ("最小角度限制", 2, "EEPROM"), 11: ("最大角度限制", 2, "EEPROM"),
    13: ("最高温度上限", 1, "EEPROM"), 14: ("最高输入电压", 1, "EEPROM"), 15: ("最低输入电压", 1, "EEPROM"),
    16: ("最大扭矩", 2, "EEPROM"), 18: ("相位", 1, "EEPROM"), 19: ("卸载条件", 1, "EEPROM"),
    20: ("LED报警条件", 1, "EEPROM"), 21: ("位置环P", 1, "EEPROM"), 22: ("位置环D", 1, "EEPROM"),
    23: ("位置环I", 1, "EEPROM"), 24: ("最小启动扭矩", 1, "EEPROM"), 25: ("积分限制值", 1, "EEPROM"),
    26: ("正向不灵敏区", 1, "EEPROM"), 27: ("负向不灵敏区", 1, "EEPROM"), 28: ("保护电流", 2, "EEPROM"),
    30: ("角度分辨率", 1, "EEPROM"), 31: ("位置偏移", 2, "EEPROM"), 33: ("运行模式", 1, "EEPROM"),
    34: ("保持扭矩", 1, "EEPROM"), 35: ("保护时间", 1, "EEPROM"), 36: ("过载扭矩", 1, "EEPROM"),
    37: ("速度环P", 1, "EEPROM"), 38: ("过流保护时间", 1, "EEPROM"), 39: ("速度环I", 1, "EEPROM"),
    40: ("扭矩开关", 1, "SRAM"), 41: ("加速度", 1, "SRAM"), 42: ("目标位置", 2, "SRAM"),
    44: ("PWM开环速度", 2, "SRAM"), 46: ("运行速度", 2, "SRAM"), 48: ("转矩限制", 2, "SRAM"),
    55: ("锁标志", 1, "SRAM"),
    56: ("当前位置", 2, "反馈"), 58: ("当前速度", 2, "反馈"), 60: ("当前负载", 2, "反馈"),
    62: ("当前电压(0.1V)", 1, "反馈"), 63: ("当前温度(°C)", 1, "反馈"), 64: ("异步写标志", 1, "反馈"),
    65: ("舵机状态位", 1, "反馈"), 66: ("移动标志", 1, "反馈"), 67: ("目标位置(回读)", 2, "反馈"),
    69: ("当前电流(6.5mA)", 2, "反馈"),
    80: ("移动速度阀值", 1, "出厂"), 81: ("DTs(ms)", 1, "出厂"), 82: ("速度单位系数", 1, "出厂"),
    83: ("最小速度限制", 1, "出厂"), 84: ("最大速度限制", 1, "出厂"), 85: ("加速度限制", 1, "出厂"),
    86: ("加速度倍数", 1, "出厂"),
}
STATUS_BITS = {0: "电压", 1: "磁编码", 2: "温度", 3: "电流", 5: "负载"}
DUMP_END = max(REGISTERS) + 1   # 87：寄存器表最后一个地址 86


def sign15(v):
    """BIT15 是方向位的 16 位量（位置、速度、电流）。"""
    return -(v & 0x7FFF) if v & 0x8000 else v


def sign10(v):
    """BIT10 是方向位（负载）。"""
    return -(v & 0x3FF) if v & 0x400 else v


class BusError(Exception):
    pass


class BusTimeout(BusError):
    """没应答或应答不完整。应答级别 0 时写指令本来就不回包，调用方自己决定算不算错。"""


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


class FeetechBus:
    def __init__(self, port, baud=1_000_000, timeout=0.02, ser=None):
        # ser 给模拟器用（sim_bus.SimSerial），真机走串口
        self.ser = ser if ser is not None else serial.Serial(port, baud, timeout=timeout, write_timeout=0.1)
        # 串口互斥锁。别叫 self.lock：会盖住下面的 lock(sid)「加 EEPROM 锁」方法（2026-09-20 真机踩到）
        self._io = threading.Lock()
        # 原始收发记录：设一个 trace(文本) 回调就开始记。飞特不支持的操作照样回"成功"，
        # 只有对着十六进制才分得清「真做了」和「装作做了」。10 Hz 的状态轮询不记，不然日志全是它
        self.trace = None
        self.trace_skip = {SYNC_READ, SYNC_WRITE}
        self._last = (None, None)
        self.stats = {"tx": 0, "rx_ok": 0, "timeout": 0, "bad_checksum": 0, "bad_id": 0}

    def close(self):
        self.ser.close()

    # ---- 包 ----
    @staticmethod
    def _packet(sid, instr, params=b""):
        length = len(params) + 2
        chk = (~(sid + length + instr + sum(params))) & 0xFF
        return bytes([0xFF, 0xFF, sid, length, instr]) + bytes(params) + bytes([chk])

    def _send(self, sid, instr, params=b""):
        self.ser.reset_input_buffer()
        pkt = self._packet(sid, instr, params)
        self.ser.write(pkt)
        self.stats["tx"] += 1
        self._last = (sid, instr)
        if self.trace and instr not in self.trace_skip:
            note = ""
            if instr in (READ, WRITE) and params:
                reg = REGISTERS.get(params[0])
                note = f"  地址{params[0]}" + (f"「{reg[0]}」" if reg else "") + (
                    f" 写 {list(params[1:])}" if instr == WRITE else f" 读 {params[1]} 字节")
            self.trace(f"→ #{sid} {INSTR_NAMES.get(instr, hex(instr))} {hexs(pkt)}{note}")

    def _read_status(self, expect_id=None):
        """读一个应答帧，返回 (id, error, params)。"""
        # 找 FF FF
        hdr = b""
        deadline = time.monotonic() + self.ser.timeout * 3
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                break
            hdr = (hdr + b)[-2:]
            if hdr == b"\xff\xff":
                break
        who = f"id {expect_id}" if expect_id is not None else "舵机"
        if hdr != b"\xff\xff":
            self.stats["timeout"] += 1
            raise BusTimeout(f"{who} 没应答（{self.ser.timeout * 3 * 1000:.0f} ms 内没收到帧头）")
        rest = self.ser.read(2)
        if len(rest) < 2:
            self.stats["timeout"] += 1
            raise BusTimeout(f"{who} 应答断在 ID/长度（收到 ff ff {hexs(rest)}）")
        sid, length = rest[0], rest[1]
        body = self.ser.read(length)
        if len(body) < length:
            self.stats["timeout"] += 1
            raise BusTimeout(f"{who} 应答不完整：长度 {length} 只收到 {len(body)}（ff ff {hexs(rest + body)}）")
        err, params, chk = body[0], body[1:-1], body[-1]
        if ((~(sid + length + err + sum(params))) & 0xFF) != chk:
            self.stats["bad_checksum"] += 1
            raise BusError(f"{who} 校验和不对（ff ff {hexs(rest + body)}）")
        if expect_id is not None and sid != expect_id:
            self.stats["bad_id"] += 1
            raise BusError(f"等 id {expect_id} 的应答，来的是 id {sid}（ff ff {hexs(rest + body)}）")
        self.stats["rx_ok"] += 1
        if self.trace and self._last[1] not in self.trace_skip:
            frame_hex = hexs(b'\xff\xff' + rest + body)
            data_suffix = f"  数据 {list(params)}" if params else ""
            self.trace(f"← #{sid} 状态0x{err:02X} {frame_hex}" + data_suffix)
        return sid, err, bytes(params)

    # ---- 指令 ----
    def ping(self, sid):
        with self._io:
            self._send(sid, PING)
            try:
                _, err, _ = self._read_status(sid)
                return err
            except BusError:
                return None

    def read(self, sid, addr, n):
        with self._io:
            self._send(sid, READ, bytes([addr, n]))
            _, err, params = self._read_status(sid)
            if len(params) != n:
                raise BusError(f"id {sid} 读地址 {addr} 起 {n} 字节，只回了 {len(params)} 字节")
            return err, params

    def write(self, sid, addr, data, wait=True):
        with self._io:
            self._send(sid, WRITE, bytes([addr]) + bytes(data))
            if not wait or sid == BROADCAST:
                return None
            try:
                _, err, _ = self._read_status(sid)
                return err
            except BusTimeout:
                # 应答级别 0 时写指令不回包，不算错；调用方拿到 None 自己判断
                return None

    def write_u8(self, sid, addr, v):
        return self.write(sid, addr, [v & 0xFF])

    def write_u16(self, sid, addr, v):
        return self.write(sid, addr, struct.pack("<H", v & 0xFFFF))

    def read_u8(self, sid, addr):
        return self.read(sid, addr, 1)[1][0]

    def read_u16(self, sid, addr):
        return struct.unpack("<H", self.read(sid, addr, 2)[1])[0]

    def sync_write(self, addr, length, id_values):
        """id_values: [(id, bytes), ...]，每个 bytes 长度 = length。"""
        params = bytes([addr, length])
        for sid, data in id_values:
            assert len(data) == length
            params += bytes([sid]) + bytes(data)
        with self._io:
            self._send(BROADCAST, SYNC_WRITE, params)

    def sync_read(self, ids, addr, n):
        """返回 {id: (err, params) 或 None}；按应答里的 ID 归档，缺包不连带丢后续包。"""
        out = dict.fromkeys(ids)
        with self._io:
            self._send(BROADCAST, SYNC_READ, bytes([addr, n]) + bytes(ids))
            for _ in ids:
                try:
                    sid, err, params = self._read_status()
                except BusTimeout:
                    break
                except BusError:
                    continue
                if sid not in out:
                    self.stats["bad_id"] += 1
                elif len(params) == n and out[sid] is None:
                    out[sid] = (err, params)
        return out

    def reboot(self, sid):
        """0x08，无应答，约 800 ms 后回来。"""
        with self._io:
            self._send(sid, REBOOT)

    def calibrate_to(self, sid, value=None):
        """0x0B 位置校准：把舵机「现在这个位置」的读数改成 value（不给就是中位 2048），偏移舵机自己算、自己存。
        协议手册 4.9：STS ≥3.10、HLS ≥3.43 支持带参数。HD-1910（固件 3.46）不认扭矩开关写 128，只能走这个。
        返回应答里的状态字节；没应答返回 None。"""
        params = b"" if value is None else struct.pack("<H", value & 0xFFFF)
        with self._io:
            self._send(sid, CALIBRATE, params)
            try:
                return self._read_status(sid)[1]
            except BusTimeout:
                return None

    def unlock(self, sid):
        return self.write_u8(sid, 55, 0)

    def lock(self, sid):
        return self.write_u8(sid, 55, 1)

    # ---- 组合动作 ----
    def state(self, sid):
        """56..70：位置 速度 负载 电压 温度 异步写 状态 移动 目标 电流 = 15 字节。"""
        err, p = self.read(sid, 56, 15)
        return self._decode_state(err, p)

    @staticmethod
    def _decode_state(err, p):
        pos, spd, load = struct.unpack_from("<HHH", p, 0)
        goal, cur = struct.unpack_from("<HH", p, 11)
        return {
            "err": err, "pos": sign15(pos), "speed": sign15(spd), "load": sign10(load),
            "volt": p[6] / 10.0, "temp": p[7], "status": p[9], "moving": p[10],
            "goal": sign15(goal), "current_ma": sign15(cur) * 6.5,
            "status_text": "".join(n for b, n in STATUS_BITS.items() if p[9] >> b & 1) or "",
        }

    def states(self, ids):
        """一次 sync_read 拿全部，失败的 ID 值为 None。"""
        raw = self.sync_read(ids, 56, 15)
        return {sid: (self._decode_state(*v) if v else None) for sid, v in raw.items()}

    def dump(self, sid):
        """0–86 全读，按 REGISTERS 解码。"""
        raw = b""
        for start in range(0, DUMP_END, 10):          # HD-1910 的表到 86 为止，读过头舵机只回剩下的字节
            n = min(10, DUMP_END - start)
            for attempt in range(4):
                try:
                    raw += self.read(sid, start, n)[1]
                    break
                except BusError as e:
                    if attempt == 3:
                        raise BusError(f"导出到地址 {start}：{e}")
                    time.sleep(0.01)
        rows = []
        for addr in sorted(REGISTERS):
            name, size, area = REGISTERS[addr]
            v = raw[addr] if size == 1 else struct.unpack_from("<H", raw, addr)[0]
            rows.append({"addr": addr, "name": name, "area": area, "size": size, "value": v})
        return {"rows": rows, "raw": list(raw)}

    def set_id(self, old, new):
        self.unlock(old)
        # 改 ID 这一包的应答可能用新 ID 回，也可能用旧 ID 回，不等它；下一包发之前会清接收缓冲
        self.write(old, 5, [new & 0xFF], wait=False)
        time.sleep(0.02)
        self.lock(new)
        return self.ping(new) is not None
