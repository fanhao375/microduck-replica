# -*- coding: utf-8 -*-
"""（搬自同仓库 tools/servo-web/sim_bus.py。Studio 用它在电脑上不接舵机也能跑舵机页，测试也用它。）

协议级的 HD-1910 模拟器：假装是一个串口，收飞特指令包、按寄存器表回应答包。

跟 server.py 里的 FakeBus 不一样：FakeBus 模拟的是高层接口，测不到 feetech.py；
这个让真实的 FeetechBus 对着它收发字节，包格式、校验和、同步读写、锁、扭矩、中位校准的前提条件都按真机来。

  bus = feetech.FeetechBus(None, ser=SimSerial([20, 21, ...]))

模拟的真机行为（有出处的）：
- 扭矩开关写 128：HD-1910（固件 3.46）回「成功」但什么都不做（2026-09-20 实测 4 轮，解锁、扭矩关着也一样；
  协议手册里 HLS ≥3.43 也不支持）。accept_128=True 时按 STS 的规矩：锁标志 = 0 且扭矩开关 = 0 才校准
- 0x0B 位置校准：当前位置的读数改成参数值（没参数 = 2048）。support_0b=False 模拟不认这条指令的舵机
- 偏移寄存器 31：offset_adds 控制是「位置 = 原始 + 偏移」还是「原始 − 偏移」，手册没写死，两种都能模拟
- 重力下垂：扭矩一关，装在鸭子身上的关节就往下掉（真机实测颈部 130 ms 掉 80 步 ≈ 0.62 步/ms）。
  droop 设成每毫秒掉几步 —— 不模拟这个，"关扭矩等太久把垂下去的位置校成零位"这类 bug 测不出来
- 总线延迟：真机每个来回（发包 + 等应答 + USB latency timer）约 1~2 ms。latency_ms 模拟它，
  同时驱动一个虚拟时钟：下垂按这个时钟算，所以测试是确定性的，不看机器快慢；time.sleep 也照样计入
- 寄存器表到 86 为止，读过头只回剩下的字节（真机实测：读 80 起 10 个回 7 个）
- 位置、速度、电流第 15 位是符号位；偏移寄存器 31 同样按第 15 位记符号（真机读到过 34279 = -1511）
- REBOOT (0x08) 不回包
- P / D / I 两份：EEPROM 21–23 出厂 32 / 40 / 0；运行中用的是 RAM 50–52（上电、重启从 EEPROM 拷），
  转矩限制 48 上电 = 最大扭矩 16（实机数据：主程序诊断 registers_before_arm 的 eeprom_pdi / temporary_pdi）
"""
import struct
import time

from . import feetech

TABLE_END = 87


def enc15(v):
    return (0x8000 | (-v & 0x7FFF)) if v < 0 else (v & 0x7FFF)


class SimServo:
    def __init__(self, sid, phys=2048):
        self.r = bytearray(TABLE_END)
        r = self.r
        r[0], r[1], r[2], r[3], r[4] = 3, 46, 0, 3, 10      # 固件 3.46，END=0 小端
        r[5], r[6], r[8] = sid, 0, 1                        # ID、波特率 1M、应答级别 1
        struct.pack_into("<HH", r, 9, 0, 4095)
        r[13], r[15] = 70, 40
        struct.pack_into("<H", r, 16, 1000)
        r[18], r[21], r[22], r[33] = 52, 32, 40, 4          # 相位 52、出厂 P32 / D40 / I0（EEPROM 21–23）、模式 4
        r[50:53] = r[21:24]                                 # 运行中的 P / D / I（RAM 50–52）：上电从 EEPROM 拷
        struct.pack_into("<H", r, 48, 1000)
        r[55] = 1                                           # 出厂锁着
        r[62], r[63] = 74, 30
        self.phys = phys          # 编码器的物理读数（没加偏移）
        self.off = 0              # 偏移：报告的位置 = phys + off
        self.goal = None
        self.calibrations = 0     # 真正做了几次中位校准，测试用
        self.muted = set()        # 读这些地址不回包，模拟中途掉线
        self.accept_128 = False   # HD-1910 不认 128
        self.support_0b = True
        self.ignore_offset_writes = False   # 模拟连写偏移都不认的舵机
        self.offset_adds = True   # 报告位置 = phys + off；False 时 = phys − off（手册没写死，两种都得能跑）
        self.droop = 0.0          # 扭矩关着时每毫秒往下掉几步；真机颈部实测 0.62
        self._t = 0.0             # 上次 tick 时的总线虚拟时钟（毫秒）
        self.mute_after_calib = set()   # 校准一做完就把这些地址加进 muted
        self.readonly = set()     # 写这些地址回「成功」但不生效：模拟写不进去（测「回读不对就不上力」）

    @property
    def id(self):
        return self.r[5]

    def pos(self):
        return self.phys + (self.off if self.offset_adds else -self.off)

    def tick(self, clock_ms=0.0):
        """扭矩开着就直接走到目标（模拟器不管动力学）；关着就按 droop 往下掉。

        clock_ms 是总线的虚拟时钟，由 SimSerial 按每包延迟和真实 sleep 推进。"""
        dt_ms, self._t = clock_ms - self._t, clock_ms
        if self.r[40] == 1 and self.goal is not None:
            self.phys = self.goal - (self.off if self.offset_adds else -self.off)
        elif self.droop:
            self.phys -= self.droop * dt_ms
        p = round(self.pos())
        struct.pack_into("<H", self.r, 56, enc15(p))
        struct.pack_into("<H", self.r, 67, enc15(self.goal if self.goal is not None else p))
        struct.pack_into("<H", self.r, 31, enc15(self.off))

    def write(self, addr, data):
        for k, b in enumerate(data):
            a = addr + k
            if a >= TABLE_END:
                break
            if a == 40 and b == 128:
                if self.accept_128 and self.r[40] == 0 and self.r[55] == 0:
                    self.recalibrate(2048)
                continue                                     # 回成功，什么都不做
            if a in self.readonly:
                continue
            self.r[a] = b
        # 多字节寄存器落地
        if addr <= 42 < addr + len(data):
            self.goal = feetech.sign15(struct.unpack_from("<H", self.r, 42)[0])
        if addr <= 31 < addr + len(data) and not self.ignore_offset_writes:
            self.off = feetech.sign15(struct.unpack_from("<H", self.r, 31)[0])
        if addr <= 40 < addr + len(data) and self.r[40] == 1 and self.goal is None:
            self.goal = self.pos()

    def recalibrate(self, value):
        # phys 因为下垂是小数，偏移得取整：真舵机的寄存器只能存整数
        self.off = round((value - self.phys) if self.offset_adds else (self.phys - value))
        self.calibrations += 1
        self.muted |= self.mute_after_calib

    def read(self, addr, n, clock_ms=0.0):
        self.tick(clock_ms)
        return bytes(self.r[addr:min(addr + n, TABLE_END)])


class SimSerial:
    """假串口：write() 收指令，按总线上的舵机生成应答；read() 把应答吐出来。"""

    def __init__(self, ids, phys=None):
        self.timeout = 0.02
        self.servos = {i: SimServo(i, (phys or {}).get(i, 2048)) for i in ids}
        self.rx = bytearray()
        self.latency_ms = 0.0     # 每个来回多少毫秒；真机 URT-2 约 1~2
        self.clock = 0.0          # 总线虚拟时钟（毫秒）：每包 +latency，再加上调用方 sleep 掉的真实时间
        self._wall = time.monotonic()

    # ---- 串口接口 ----
    def reset_input_buffer(self):
        self.rx.clear()

    def read(self, n=1):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def close(self):
        pass

    def write(self, data):
        data = bytes(data)
        while len(data) >= 6:
            if data[0] != 0xFF or data[1] != 0xFF:
                data = data[1:]
                continue
            sid, length = data[2], data[3]
            pkt = data[:4 + length]
            data = data[4 + length:]
            instr, params = pkt[4], pkt[5:-1]
            if ((~sum(pkt[2:-1])) & 0xFF) != pkt[-1]:
                continue                                         # 校验错：真舵机不理
            self._handle(sid, instr, params)
        return len(data)

    # ---- 舵机 ----
    def _by_id(self, sid):
        for s in self.servos.values():
            if s.id == sid:
                return s
        return None

    def _advance(self):
        """推进虚拟时钟：一次总线来回的延迟，加上上次之后真实睡掉的时间（代码里的 time.sleep 要算进下垂）。"""
        now = time.monotonic()
        self.clock += self.latency_ms + (now - self._wall) * 1000
        self._wall = now
        return self.clock

    def _reply(self, s, params=b""):
        s.tick(self.clock)
        err = s.r[65]
        length = len(params) + 2
        body = bytes([s.id, length, err]) + bytes(params)
        self.rx += bytes([0xFF, 0xFF]) + body + bytes([(~sum(body)) & 0xFF])

    def _handle(self, sid, instr, p):
        self._advance()
        if instr == feetech.SYNC_WRITE:
            addr, ln = p[0], p[1]
            rest = p[2:]
            for k in range(0, len(rest), ln + 1):
                s = self._by_id(rest[k])
                if s:
                    s.write(addr, rest[k + 1:k + 1 + ln])
            return
        if instr == feetech.SYNC_READ:
            addr, ln = p[0], p[1]
            for i in p[2:]:
                s = self._by_id(i)
                # alive() 给假 IMU 小板用：模拟"总线处理器还没起来"和"板子彻底不答"
                if s and addr not in getattr(s, "muted", ()) and getattr(s, "alive", lambda _: True)(self.clock):
                    self._reply(s, s.read(addr, ln, self.clock))
            return
        s = self._by_id(sid)
        if s is None:
            return
        if instr == feetech.PING:
            if sid == feetech.BROADCAST or not getattr(s, "alive", lambda _: True)(self.clock):
                return                                       # 手册：总线上多个设备时不能用广播 PING
            self._reply(s)
        elif instr == feetech.READ:
            if p[0] in s.muted or not getattr(s, "alive", lambda _: True)(self.clock):
                return
            self._reply(s, s.read(p[0], p[1], self.clock))
        elif instr == feetech.WRITE:
            s.write(p[0], p[1:])
            self._reply(s)                                       # 改 ID 时用新 ID 回
        elif instr == feetech.CALIBRATE:
            if s.support_0b:
                s.recalibrate(2048 if len(p) < 2 else feetech.sign15(p[0] | p[1] << 8))
            self._reply(s)
        elif instr == feetech.REBOOT:
            s.r[40] = 0                                          # 不回包，回来时扭矩关
            s.r[50:53] = s.r[21:24]                              # RAM 的 P/D/I、限力恢复成上电值（真机：重启舵机 RAM 回出厂）
            s.r[48:50] = s.r[16:18]
