# -*- coding: utf-8 -*-
"""从舵机 TTL 总线读 IMU 小板（ID 200）。默认使用仓库配套的飞特协议 56/15；可选兼容 DXL2 的 124/12。

舵机与 IMU 可运行不同总线协议：舵机状态仍由 Feetech SCS 解码，IMU 块交给这里。
飞特协议的 15 字节块判据照主控（fork 的 duck-control
imu.rs / bus.rs）写，跟 tools/imu200/imu200.py 同一套：

- 四元数三分量全 0 → 融合还没出数（主控沿用上一帧，不计入 ready）
- 不是有限数 / norm² > 1.02 → 主控拒绝这一帧
- 前 12 字节连续 25 帧完全相同 → 主控报 orientation is frozen
- 保留字节（byte 14）必须是 0；状态位 BIT1/BIT2 是板子自己报的故障

板子不应答时连续 5 次暂缓读取，每 2 秒单独 PING 一下 200；舵机始终使用独立的 Feetech
请求读取，因此 IMU 掉线不会连带丢舵机状态。
"""
import math
import struct
import threading
import time

from imu_bridge import BridgeError, HOST_STALE_MS, decode_quaternion, empty_frame

IMU_ID = 200
FEETECH_BLOCK_ADDR = 56
FEETECH_BLOCK_LEN = 15
DXL2_BLOCK_ADDR = 124
DXL2_BLOCK_LEN = 12
BLOCK_ADDR = FEETECH_BLOCK_ADDR
BLOCK_LEN = FEETECH_BLOCK_LEN
WINDOW = 12               # 主控只看前 12 字节，StaleImuTracker 也只比这 12 字节
FROZEN_FRAMES = 25        # bus.rs: 连续 25 帧相同就报 frozen
GYRO_DPS_PER_LSB = 0.0175
MISS_DEGRADE = 5          # 连续这么多帧 200 没应答：先把它移出 sync_read
RETRY_S = 2.0             # 移出后每隔这么久单独 PING 它一次

BIT_FUSION_NOT_READY = 0x01
BIT_IMU_COMM_FAIL = 0x02
BIT_SELF_TEST_FAIL = 0x04
BIT_READER_TOO_SLOW = 0x08
SLOW_READER_HZ = 30      # 固件 BIT3 = 两次读之间刷新 > 4 次（120 Hz 采样）≈ 读得比 30 Hz 慢


def decode_block(block):
    """解飞特 15 字节块或 DXL2 12 字节块，不做坐标变换。"""
    if len(block) not in (FEETECH_BLOCK_LEN, DXL2_BLOCK_LEN):
        raise BridgeError(f"块长度 {len(block)}，应为 12（DXL2）或 15（飞特）")
    gyro = struct.unpack_from("<3h", block, 0)
    packed = struct.unpack_from("<3H", block, 6)
    is_dxl2 = len(block) == DXL2_BLOCK_LEN
    return {"gyro_raw": list(gyro), "gyro_dps": [v * GYRO_DPS_PER_LSB for v in gyro],
            "quat_fp16": list(packed), "counter": None if is_dxl2 else block[12],
            "bits": 0 if is_dxl2 else block[13], "reserved": 0 if is_dxl2 else block[14],
            "window": bytes(block[:WINDOW])}


class BusImuService:
    """跟 BridgeService 同样的接口（start/stop/get_status），数据由 server 的状态轮询喂进来。"""

    demo = False
    backend = "bus"

    def __init__(self, poll_hz=None, protocol="feetech"):
        # 调试台自己按 poll_hz 读（默认 10 Hz）。低于 30 Hz 时 BIT3 每帧都会亮，那是我们读得慢，不是主控，不提示
        self.poll_hz = poll_hz
        self.protocol = protocol
        self.lock = threading.Lock()
        self.frame = self._frame("disconnected", "等待第一条带 ID 200 的 sync_read。")
        self.last_publish = time.monotonic()
        self.misses = 0
        self.total_misses = 0
        self.excluded_since = None       # 被移出 sync_read 的时刻；None = 正常带着 200
        self.last_probe = None
        self.last_window = None
        self.same_run = 0
        self.previous_ok = None
        self.smooth_hz = 0.0
        self.frames = 0

    # ---- 生命周期：没有自己的线程，接口跟 BridgeService 对齐 ----
    def start(self):
        pass

    def stop(self):
        pass

    def set_protocol(self, protocol):
        if protocol not in ("dxl2", "feetech"):
            raise ValueError(f"不支持的 IMU 总线协议：{protocol}")
        with self.lock:
            if protocol == self.protocol:
                return False
            self.protocol = protocol
            self.misses = 0
            self.excluded_since = None
            self.last_probe = None
            self.last_window = None
            self.same_run = 0
            self.previous_ok = None
            self.smooth_hz = 0.0
            self.frame = self._frame("disconnected", f"已切换到 {protocol}，等待 ID 200 应答。")
            self.last_publish = time.monotonic()
        return True

    def _frame(self, status, message, **extra):
        frame = empty_frame("live", status, message)
        frame.update(backend="bus", probe_name="舵机总线 ID 200", **extra)
        return frame

    def _publish(self, frame):
        frame["timestamp_ms"] = int(time.time() * 1000)
        with self.lock:
            self.frame = frame
            self.last_publish = time.monotonic()

    def get_status(self):
        with self.lock:
            result = dict(self.frame)
            elapsed_ms = (time.monotonic() - self.last_publish) * 1000
        if result["live"] and elapsed_ms >= HOST_STALE_MS:
            result.update(status="stale", live=False, quaternion_xyzw=None, gyro_dps=None, hz=0.0,
                          message="总线轮询停了（没有网页连着，或者正在校准），姿态暂停。")
        return result

    # ---- 轮询侧 ----
    def include_in_sync(self):
        """这一帧 sync_read 要不要带上 200。"""
        return self.excluded_since is None

    def due_probe(self, now=None):
        """出列期间，到点了就该单独 PING 一次 200。"""
        if self.excluded_since is None:
            return False
        now = time.monotonic() if now is None else now
        if self.last_probe is None or now - self.last_probe >= RETRY_S:
            self.last_probe = now
            return True
        return False

    def probe_result(self, answered):
        """单独 PING 的结果：应答了就放回 sync_read。返回是否放回。"""
        if answered and self.excluded_since is not None:
            self.excluded_since = None
            self.misses = 0
            self._publish(self._frame("disconnected", "ID 200 的 PING 有应答了，下一帧重新带上它。"))
            return True
        return False

    def feed(self, block, now=None):
        """block = 200 的 15 字节；None = 这一帧它没应答（或没带它）。返回生成的帧。"""
        now = time.monotonic() if now is None else now
        if block is None:
            self.misses += 1
            self.total_misses += 1
            if self.misses >= MISS_DEGRADE and self.excluded_since is None:
                self.excluded_since = now
            frame = self._frame("disconnected",
                                f"ID 200 连续 {self.misses} 帧没应答：检查小板供电、总线接线（J1/J2 的 DATA）"
                                f"和 IMU 协议（当前 {self.protocol}）；舵机已改为单独读，每 {RETRY_S:.0f} 秒单独 PING 一次 200。",
                                misses=self.total_misses)
            self._publish(frame)
            return frame
        self.misses = 0
        self.excluded_since = None
        d = decode_block(block)
        extra = {"connected": True, "sample_count": d["counter"], "raw_gyro": d["gyro_raw"],
                 "quat_fp16": d["quat_fp16"], "status_bits": d["bits"] if self.protocol != "dxl2" else None,
                 "misses": self.total_misses,
                 "errors": {"imu": d["bits"] & (BIT_IMU_COMM_FAIL | BIT_SELF_TEST_FAIL), "spi": None,
                            "fifo": None, "quaternion": None, "recovery": None}}
        self.same_run = self.same_run + 1 if d["window"] == self.last_window else 0
        self.last_window = d["window"]
        if d["reserved"] != 0:
            frame = self._frame("error", f"块的保留字节（byte 14）是 {d['reserved']}，规格要求 0：固件不符合总线协议 §3。", **extra)
        elif d["bits"] & BIT_SELF_TEST_FAIL:
            frame = self._frame("error", "小板报告 IMU 自检失败（BIT2，WHO_AM_I 不对）：检查 IMU 芯片焊接。", **extra)
        elif d["bits"] & BIT_IMU_COMM_FAIL:
            frame = self._frame("error", "小板报告跟 IMU 芯片通信失败（BIT1：SPI/复位/配置/FIFO/数据过期）。", **extra)
        elif not any(d["quat_fp16"]) or d["bits"] & BIT_FUSION_NOT_READY:
            frame = self._frame("stale", "IMU 融合还没出数（四元数全 0 / BIT0），刚上电约 0.5 秒内正常。", **extra)
        elif self.same_run + 1 >= FROZEN_FRAMES:
            frame = self._frame("stale", f"前 12 字节连续 {self.same_run + 1} 帧完全相同：主控会报 orientation is frozen。"
                                "真芯片静置时低位也在跳，固件不能平滑或量化陀螺。", **extra)
        else:
            try:
                quaternion = decode_quaternion(tuple(d["quat_fp16"]))
            except BridgeError as exc:
                frame = self._frame("error", f"四元数主控会拒绝：{exc}", **extra)
            else:
                if self.previous_ok is not None:
                    instant = 1.0 / max(1e-4, now - self.previous_ok)
                    self.smooth_hz = instant if self.smooth_hz == 0 else self.smooth_hz * 0.8 + instant * 0.2
                self.previous_ok = now
                slow = ("；BIT3：两次读之间块刷新了 4 次以上，读得太慢"
                        if d["bits"] & BIT_READER_TOO_SLOW
                        and (self.poll_hz is None or self.poll_hz >= SLOW_READER_HZ) else "")
                frame = self._frame("live", "正在从舵机总线读 ID 200（跟主控同一条 sync_read）" + slow + "。",
                                    live=True, quaternion_xyzw=quaternion, gyro_dps=d["gyro_dps"],
                                    hz=round(self.smooth_hz, 1), age_ms=None, **extra)
        self.frames += 1
        self._publish(frame)
        return frame


class FakeImu200:
    """假总线（--fake）用的假小板：120 Hz 换块，陀螺低位带抖动，四元数绕 Z 慢慢转。"""

    def __init__(self, sample_hz=120.0, ready_after_s=0.5):
        self.t0 = time.monotonic()
        self.sample_hz, self.ready_after_s = sample_hz, ready_after_s

    def block(self, now=None):
        t = (time.monotonic() if now is None else now) - self.t0
        n = int(t * self.sample_hz)
        if t < self.ready_after_s:
            return bytes(12) + bytes([n & 0xFF, BIT_FUSION_NOT_READY, 0])
        jitter = (n % 7) - 3
        yaw = 0.6 * math.sin(t * 0.5)
        gyro = (int(17 * math.cos(t * 0.5)) + jitter, jitter, -jitter)
        quat = (0.05, 0.0, math.sin(yaw / 2))   # 带一点倾斜，z 过零时也不会是全 0 的"未就绪"标记
        return (struct.pack("<3h", *gyro) + struct.pack("<3e", *quat)
                + bytes([n & 0xFF, 0, 0]))
