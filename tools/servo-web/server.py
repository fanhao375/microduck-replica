# -*- coding: utf-8 -*-
"""飞特舵机网页调试台：串口 + 网页滑块 + 3D 鸭子跟着动。

用法：
  python server.py --port COM5                      # Windows，URT-2
  python server.py --port /dev/ttyUSB0              # 板子上，URT-2 插 USB
  python server.py --port /dev/ttyS2                # 板子上，走半双工转接板（先 sudo systemctl stop robotd）
  python server.py --fake                           # 没舵机，看界面
然后浏览器开 http://<主机>:8080
"""
import argparse
import asyncio
import contextlib
import json
import logging
import math
from pathlib import Path
import os
import struct
import tempfile
import threading
import time
import traceback

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

import feetech

HERE = os.path.dirname(os.path.abspath(__file__))

# 调试台版本。改了前端或后端就加一：index.html 里的 PAGE_VERSION 要跟这里一样，
# 页面连上后会比对，不一样就提示"页面是旧的，Ctrl+F5"。改动记在 README 的「版本」一节。
VERSION = "0.15.5"
DEFAULT_IDS = "20-24,30-34,10-14"
JOINT_NAMES = {
    20: "left_hip_yaw", 21: "left_hip_roll", 22: "left_hip_pitch", 23: "left_knee", 24: "left_ankle",
    30: "neck_pitch", 31: "head_pitch", 32: "head_yaw", 33: "head_roll", 34: "mouth",
    10: "right_hip_yaw", 11: "right_hip_roll", 12: "right_hip_pitch", 13: "right_knee", 14: "right_ankle",
}


def parse_ids(text):
    ids = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            ids += list(range(int(a), int(b) + 1))
        else:
            ids.append(int(part))
    return ids


class FakeBus:
    """没舵机时的替身（模拟模式）：虚拟舵机按真舵机的速度 / 加速度寄存器走梯形曲线到目标，
    所以姿势、滑块、序列、灵动在 3D 里的快慢跟真机一样。扭矩默认开，没接串口也能直接看它动；
    扭矩关了就停在原地（没有重力，不会往下掉）。"""

    MAX_SPEED = 3000                  # 步/秒：HD-1910 满速约这么多，跟 MAX_SPEED_REG 一致
    ACC_UNIT = 8.7 * 4096 / 360       # 寄存器 41 一个单位 = 8.7°/s²，换成 步/s²
    ACC_MAX = 254 * ACC_UNIT          # 41 写 0 = 不限加速度，按最大算

    def __init__(self, ids):
        self.pos = {i: 2048.0 for i in ids}
        self.vel = {i: 0.0 for i in ids}
        self.goal = {i: 2048 for i in ids}
        self.speed_reg = {i: 0 for i in ids}   # 46：0 = 不限速
        self.acc_reg = {i: 0 for i in ids}     # 41：0 = 不限加速度
        self.torque = {i: 1 for i in ids}
        self.offset = {i: 0 for i in ids}
        self.locked = {i: 1 for i in ids}   # 出厂锁着
        self.regs = {}                      # 其它寄存器随便写随便读，(id, 地址) -> 值
        self.stats = {"tx": 0, "rx_ok": 0, "timeout": 0, "bad_checksum": 0, "bad_id": 0}
        self.imu200 = None                  # 模拟 IMU 时挂一个 imu_bus.FakeImu200，答 ID 200
        self._t = time.monotonic()

    def _advance(self):
        """按墙钟推进每颗虚拟舵机：朝目标加速，离目标近了按 v² = 2·a·d 减速，跟舵机自己的梯形曲线一样。
        写寄存器之前先推进一次：不然上次读到这次写之间的时间，会按新目标补走。"""
        now = time.monotonic()
        dt, self._t = min(0.2, now - self._t), now
        if dt <= 0:
            return
        for i in self.pos:
            if not self.torque[i]:
                self.vel[i] = 0.0
                continue
            err = self.goal[i] - self.pos[i]
            if abs(err) < 0.5 and abs(self.vel[i]) < 1:
                self.pos[i], self.vel[i] = float(self.goal[i]), 0.0
                continue
            limit = self.speed_reg[i] * SPEED_UNIT
            vmax = limit if 0 < limit < self.MAX_SPEED else self.MAX_SPEED
            amax = self.acc_reg[i] * self.ACC_UNIT if self.acc_reg[i] else self.ACC_MAX
            want = math.copysign(min(vmax, math.sqrt(2 * amax * abs(err))), err)
            dv = max(-amax * dt, min(amax * dt, want - self.vel[i]))
            self.vel[i] += dv
            step = self.vel[i] * dt
            if step * err > 0 and abs(step) >= abs(err):   # 朝着目标走、这一步就到：停在目标上。背着目标（刚换向）不算
                self.pos[i], self.vel[i] = float(self.goal[i]), 0.0
            else:
                self.pos[i] += step

    def ping(self, sid):
        return 0 if sid in self.pos or (sid == 200 and self.imu200 is not None) else None

    def states(self, ids):
        self._advance()
        out = {}
        for i in ids:
            if i not in self.pos:
                out[i] = None
                continue
            pos = int(round(self.pos[i]))
            out[i] = {"err": 0, "pos": pos, "speed": int(round(self.vel[i])), "load": 0, "volt": 7.9, "temp": 31,
                      "status": 0, "moving": int(abs(self.goal[i] - pos) > 2 or abs(self.vel[i]) > 1),
                      "goal": self.goal[i], "current_ma": 0.0, "status_text": ""}
        self.stats["tx"] += 1
        self.stats["rx_ok"] += len(ids)
        return out

    def write_u16(self, sid, addr, v):
        # 42 目标、46 速度、31 偏移跟真舵机一样是符号-幅值（BIT15 = 负），server 用 enc_sm 编好再写
        self._advance()
        v &= 0xFFFF
        self.regs[(sid, addr)] = v
        if addr == 42:
            self.goal[sid] = feetech.sign15(v)
        elif addr == 46:
            self.speed_reg[sid] = v & 0x7FFF
        elif addr == 31:
            off = feetech.sign15(v)
            self.pos[sid] += off - self.offset[sid]       # 读数 = 原始 + 偏移（OFFSET_SIGN = +1）
            self.offset[sid] = off
        return 0

    def write_u8(self, sid, addr, v):
        self._advance()
        if addr == 41:
            self.acc_reg[sid] = v & 0xFF
        if addr == 55:
            self.locked[sid] = 1 if v else 0
        elif addr != 40:
            self.regs[(sid, addr)] = v & 0xFF
        if addr == 40 and v != 128:          # 写 128 跟 HD-1910 一样：回成功，什么都不做
            self.torque[sid] = 1 if v == 1 else 0
        return 0

    def calibrate_to(self, sid, value=None):
        self._advance()
        value = 2048 if value is None else value
        self.offset[sid] += value - int(round(self.pos[sid]))
        self.pos[sid], self.vel[sid] = float(value), 0.0
        return 0

    def read_u8(self, sid, addr):
        return {40: self.torque[sid], 55: self.locked[sid]}.get(addr, self.regs.get((sid, addr), 0))

    def read_u16(self, sid, addr):
        self._advance()
        return {31: enc_sm(self.offset[sid]), 56: enc_sm(int(round(self.pos[sid]))),
                42: enc_sm(self.goal[sid])}.get(addr, self.regs.get((sid, addr), 0))

    def sync_write(self, addr, length, id_values):
        self._advance()
        for sid, data in id_values:
            if sid not in self.pos:
                continue
            if addr == 42:
                self.goal[sid] = feetech.sign15(struct.unpack("<H", bytes(data[:2]))[0])
            if addr == 41:                   # 加速度（+ 目标位置 + 时间 + 速度）
                self.acc_reg[sid] = data[0]
                if length >= 3:
                    self.goal[sid] = feetech.sign15(struct.unpack("<H", bytes(data[1:3]))[0])
                if length >= 7:
                    self.speed_reg[sid] = struct.unpack("<H", bytes(data[5:7]))[0] & 0x7FFF   # 符号-幅值，取幅值
            if addr == 46:
                self.speed_reg[sid] = struct.unpack("<H", bytes(data[:2]))[0] & 0x7FFF
            if addr == 40:
                self.torque[sid] = data[0]

    def sync_read(self, ids, addr, n):
        out = {i: (0, bytes([self.torque[i]]) + bytes(n - 1) if addr == 40 else bytes(n)) if i in self.pos else None
               for i in ids}
        imu = self.imu200                    # 页面随时可能拔掉虚拟小板：取一次，别判断完再读时变成 None
        if imu is not None and 200 in out and addr == 56 and n == 15:
            out[200] = (0, imu.block())
        return out

    def dump(self, sid):
        raw = [0] * feetech.DUMP_END
        raw[5], raw[8], raw[21], raw[22], raw[33], raw[55] = sid, 1, 32, 32, 4, self.locked.get(sid, 1)
        if sid in self.pos:
            off, pos = enc_sm(self.offset[sid]), enc_sm(int(round(self.pos[sid])))
            raw[31], raw[32], raw[40], raw[56], raw[57] = off & 0xFF, off >> 8, self.torque[sid], pos & 0xFF, pos >> 8
        rows = [{"addr": a, "name": feetech.REGISTERS[a][0], "area": feetech.REGISTERS[a][2],
                 "size": feetech.REGISTERS[a][1], "value": raw[a]} for a in sorted(feetech.REGISTERS)]
        return {"rows": rows, "raw": raw}

    def read(self, sid, addr, n):
        if addr == 0 and n == 5:
            return 0, bytes([3, 10, 0, 3, 11])
        return 0, bytes(n)

    def reboot(self, sid):
        pass

    def unlock(self, sid):
        self.locked[sid] = 0
        return 0

    def lock(self, sid):
        self.locked[sid] = 1
        return 0

    def set_id(self, old, new):
        self.pos[new] = self.pos.pop(old, 2048.0)
        self.vel[new] = self.vel.pop(old, 0.0)
        self.goal[new] = self.goal.pop(old, 2048)
        self.speed_reg[new] = self.speed_reg.pop(old, 0)
        self.acc_reg[new] = self.acc_reg.pop(old, 0)
        self.torque[new] = self.torque.pop(old, 1)
        self.offset[new] = self.offset.pop(old, 0)
        self.locked[new] = self.locked.pop(old, 1)
        return True

    def close(self):
        pass


BUS = None
PORT = None        # 当前串口名；None = 假总线
BAUD = 1_000_000
IDS = []
PRESENT = []
CLIENTS = set()
IMU_SERVICE = None  # 可选 IMU 读取器（J-Link DLL 或 pyOCD）；与舵机状态共用 /ws，不接受网页控制探针
LOG = []           # [{"n": 序号, "t": "时:分:秒", "cat": 分类, "level": info/warn/error, "msg": 正文}]
LOG_N = 0
LOG_DIR = os.path.join(HERE, "logs")
LOG_CATS = ["系统", "总线", "运动", "校准", "寄存器", "姿态", "方向", "IMU"]   # 页面自己还有一类「页面」
LEVEL_TAG = {"info": "", "warn": "[警告]", "error": "[错误]", "debug": "[调试]"}
_log_io = threading.Lock()
STREAM_HZ = 50
FAKE_STREAM_HZ = 30    # 模拟模式不占总线，推快一点，3D 动起来顺
SPEED_UNIT = 1.0   # 速度寄存器 46 一个单位 = 多少步/秒：相位 18 BIT2=1 → 1（0.0146 rpm），BIT2=0 → 50（0.732 rpm）
MAX_SPEED_REG = 3000   # "不限速"写多少：相位 BIT3=1 时 0 = 最快；BIT3=0 时 0 = 停，得写个大数（HD-1910 满速约 3000 步/秒）
STREAM_LAST = {}       # goals_stream 上一帧给每颗的目标，用来算这一帧该多快


def release_speed(ids):
    """把速度上限放开、加速度设最大（41 = 0，46 = MAX_SPEED_REG）。只写这两个，**不碰目标位置 42**：
    以前一包写 41..47 连目标一起，目标取自回读的 67。校准收尾刚把目标对准当前读数，
    67 要是还没跟上，就把旧目标写回去了，下次开扭矩关节被拉走。"""
    ids = list(ids)
    if ids:
        BUS.sync_write(41, 1, [(i, bytes([0])) for i in ids])
        BUS.sync_write(46, 2, [(i, struct.pack("<H", enc_sm(MAX_SPEED_REG))) for i in ids])


def read_phase():
    """连接时只读相位 18，确定速度单位；保留舵机现有的速度、加速度和目标。"""
    global SPEED_UNIT, MAX_SPEED_REG
    if is_fake() or not PRESENT:
        return
    try:
        ph = BUS.read_u8(PRESENT[0], 18)
        SPEED_UNIT = 1.0 if ph & 0x04 else 50.0
        MAX_SPEED_REG = 0 if ph & 0x08 else int(3000 / SPEED_UNIT)
        log(f"相位 18 = {ph}（BIT2={'1' if ph & 4 else '0'} → 速度单位 {SPEED_UNIT:g} 步/秒；BIT3={'1 速度0=最快' if ph & 8 else '0 速度0=停'} → 不限速写 {MAX_SPEED_REG}）")
    except Exception as e:
        log(f"读相位失败，速度单位按 1 步/秒：{type(e).__name__}: {e}", "系统", "warn", exc=True)


def list_ports():
    """系统里现在有哪些串口。拔插 USB 后口号会变，页面上直接选，不用重启服务。"""
    try:
        from serial.tools import list_ports as lp
        return [{"device": p.device, "desc": (p.description or "").strip()} for p in lp.comports()]
    except Exception as e:
        log(f"列串口失败：{type(e).__name__}: {e}", "总线", "warn")
        return []


def bus_trace(line):
    """串口收发的原始字节，只进日志文件（页面不显示，不然刷屏）。
    出问题时 grep 日志文件里的 → / ← 就能还原当时总线上到底发生了什么。"""
    log(line, "总线", "debug")


def open_bus(port, baud=None):
    """打开串口并接管总线。失败不抛异常，返回 {ok, msg}，页面照样能用（假总线）。"""
    global BUS, PORT, BAUD, PRESENT
    baud = baud or BAUD
    try:
        bus = feetech.FeetechBus(port, baud)
    except Exception as e:
        ports = "、".join(p["device"] for p in list_ports()) or "一个都没有"
        msg = f"打不开 {port}：{type(e).__name__}: {e}；现在能看到的串口：{ports}"
        log(msg, "总线", "error")
        return {"ok": False, "msg": msg}
    bus.trace = bus_trace
    old = BUS
    BUS, PORT, BAUD = bus, port, baud
    if old is not None and not isinstance(old, FakeBus):
        try:
            old.close()
        except Exception:
            pass
    log(f"串口 {port} @ {baud} 已打开", "总线")
    drop_sim_imu("接上真串口")
    do_scan(IDS)
    read_phase()
    return {"ok": True, "msg": f"{port} 已连上，在线 {len(PRESENT)} 颗"}


def fallback_fake(why):
    """没有串口时退到假总线：页面照开，能看界面、看 3D，就是不动真舵机。"""
    global BUS, PORT, PRESENT
    BUS, PORT = FakeBus(IDS), None
    PRESENT = list(IDS)
    if imu_backend(IMU_SERVICE) == "bus":   # 启动参数 --imu-bus 但没串口：假总线上挂一块虚拟小板答 ID 200
        import imu_bus
        BUS.imu200 = imu_bus.FakeImu200()
    log(f"{why}，先用假总线把页面起起来；插好 USB 后在页面顶栏选串口点「连接」", "总线", "warn")


def log_path():
    return os.path.join(LOG_DIR, f"servo-web-{time.strftime('%Y-%m-%d')}.log")


def log(msg, cat="系统", level="info", exc=False):
    """cat：系统 / 总线 / 运动 / 校准 / 寄存器 / 姿态 / 方向 / IMU。level：info / warn / error / debug。
    每行都追加到 logs/servo-web-日期.log（debug 只进文件，不上页面）；exc=True 把当前异常的 traceback 一起写进文件。
    出问题时这个文件就是现场，发过来或者让 Claude 直接读。"""
    global LOG_N
    t = time.strftime("%H:%M:%S")
    line = f"{t} [{cat}]{LEVEL_TAG.get(level, '')} {msg}"
    tb = traceback.format_exc() if exc else ""
    with _log_io:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n" + tb)
        except OSError:
            pass
        if level != "debug":
            LOG_N += 1
            LOG.append({"n": LOG_N, "t": t, "cat": cat, "level": level, "msg": msg})
            del LOG[:-400]
    print(line + ("\n" + tb.rstrip() if tb else ""), flush=True)
    return line


def _ack(err):
    """写指令的应答：0 = 正常；None = 没回包（应答级别 0，或者丢了）；别的 = 舵机状态位有报警。"""
    return "✓" if err == 0 else ("（没应答）" if err is None else f"（状态 0x{err:02X}）")


def fmt_off(v):
    """偏移寄存器 31：HD-1910 第 15 位是符号位，34279 = -1511。
    传进来的可能是原始寄存器值，也可能是已经解过符号的负数，两种都得印对。"""
    if v is None:
        return "?"
    if v < 0:
        return str(v)                                  # 已经是带符号的，直接印
    return f"{feetech.sign15(v)}（原始{v}）" if v & 0x8000 else str(v)


def is_fake():
    return isinstance(BUS, FakeBus)


async def index(request):
    # 不让浏览器缓存页面：改了前端以后，缓存里的旧页面会用旧逻辑跑，现象像"改了没生效"
    return FileResponse(os.path.join(HERE, "index.html"),
                        headers={"Cache-Control": "no-store, max-age=0"})


async def imu_attitude_file(request):
    return FileResponse(os.path.join(HERE, "imu_attitude.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})


async def model_file(request):
    name = request.path_params["name"]
    if name not in ("model.json", "meshes.bin"):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(HERE, "model", name)
    if not os.path.exists(path):
        return JSONResponse({"error": "model 目录没生成，跑 build_model.py"}, status_code=404)
    return FileResponse(path)


POSES_DIR = os.path.join(HERE, "poses")
DIRS_FILE = os.path.join(HERE, "directions.json")


def load_dirs():
    """每颗舵机的方向 +1 / -1。文件里没写的按 +1。"""
    try:
        with open(DIRS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): (-1 if v == -1 else 1) for k, v in raw.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log(f"directions.json 读不了，全部按 +1：{e}", "方向", "warn")
        return {}


def save_dirs(dirs):
    note = ""
    try:
        with open(DIRS_FILE, encoding="utf-8") as f:
            note = json.load(f).get("_note", "")
    except Exception:
        pass
    out = {"_note": note} if note else {}
    for i in IDS:
        out[str(i)] = dirs.get(i, 1)
    with open(DIRS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


CALIB_DIR = os.path.join(HERE, "calib")
SIM_CALIB_DIR = None   # 模拟模式的校准备份：放临时目录，服务一关就没了


def calib_dir():
    """校准备份放哪。模拟模式不能用真的 calib/：虚拟舵机的偏移要是混进去，
    接上真舵机点「撤销」，会把虚拟的偏移写进真舵机。"""
    global SIM_CALIB_DIR
    if not is_fake():
        return CALIB_DIR
    if SIM_CALIB_DIR is None or not os.path.isdir(SIM_CALIB_DIR):
        SIM_CALIB_DIR = tempfile.mkdtemp(prefix="servo-web-sim-calib-")
    return SIM_CALIB_DIR


def backup_calibration(ids):
    """中位校准前把每颗的偏移（寄存器 31）、读数、扭矩、锁标志、固件版本记下来，撤销用。"""
    rows = {}
    for i in ids:
        try:
            ver = BUS.read(i, 0, 5)[1]
            rows[str(i)] = {"offset": BUS.read_u16(i, 31), "pos": feetech.sign15(BUS.read_u16(i, 56)),
                            "torque": BUS.read_u8(i, 40), "lock": BUS.read_u8(i, 55), "firmware": f"{ver[0]}.{ver[1]}"}
        except Exception as e:
            rows[str(i)] = {"error": f"{type(e).__name__}: {e}"}
            log(f"#{i} 备份时读不到：{type(e).__name__}: {e}", "校准", "warn", exc=True)
    d = calib_dir()
    os.makedirs(d, exist_ok=True)
    now = time.time()                                  # 带毫秒：同一秒点两次也不撞名，字典序 = 时间顺序
    fn = f"eeprom-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{int(now * 1000) % 1000:03d}.json"
    with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
        json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "servos": rows}, f, ensure_ascii=False, indent=1)
    return fn, rows


def shift_saved_poses(deltas, sign=1, only=None, exclude=()):
    """重新校准后，同一个物理姿势的读数变了 delta。把 poses/ 里存的原始读数跟着挪，免得发旧数字摆错。
    only：只挪这些文件（撤销时用：只把当次挪过的挪回来，之后新存的姿态不碰）；exclude：不挪的（当基准的那个姿态）。
    序列文件只存"相对某个姿态几度"，不用挪。返回挪过的文件名列表。"""
    changed = []
    if not os.path.isdir(POSES_DIR) or not any(deltas.values()):
        return changed
    for fn in sorted(os.listdir(POSES_DIR)):
        if not fn.endswith(".json") or fn in exclude or (only is not None and fn not in only):
            continue
        p = os.path.join(POSES_DIR, fn)
        with open(p, encoding="utf-8") as f:
            pose = json.load(f)
        rows = pose.get("rows")
        if not rows:
            continue
        hit = False
        for r in rows:
            d = deltas.get(str(r.get("id")))
            if d:
                r["pos"] = int(r["pos"]) + sign * int(d)
                r["deg"] = round((r["pos"] - 2048) * 360 / 4096, 1)
                hit = True
        if hit:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(pose, f, ensure_ascii=False, indent=1)
            changed.append(fn)
    return changed


CALIB_TOLERANCE = 2      # 校准后读数离目标多少步算到位
DROOP_TOLERANCE = 3      # 关扭矩那几毫秒关节垂了多少步以内就不补偿
OFFSET_LIMIT = 2047      # 偏移寄存器 31 的安全量程：手册说 0~2047 表示正、2048~4095 表示负，
                         # 只有 |偏移| ≤ 2047 这一段几种编码理解都一致，超出就不写
CALIB_LOCK = threading.Lock()   # 校准期间不让状态轮询抢总线：一次 sync_read 卡住能占几百毫秒，
                                # 正好落在关扭矩的窗口里，关节就垂下去了
OFFSET_SIGN = None       # +1：读数 = 原始 + 偏移；-1：读数 = 原始 − 偏移。手册没写死，开机探一次
CALIB_BIG_DEG = 30       # 摆好的位置离目标差这么多度以上：页面再确认一次，日志单独列出来（不拦）


def enc_sm(v):
    """符号-幅值编码：寄存器 31（偏移）、42（目标位置）、46（速度）都是 BIT15 当方向位，
    不是二进制补码。写 -5 要写 0x8005；写成 0xFFFB 舵机会理解成 -32763，然后满速撞限位。"""
    v = int(v)
    return (0x8000 | (-v & 0x7FFF)) if v < 0 else (v & 0x7FFF)


enc_off = enc_sm          # 老名字留着，别处还在用


def read_off(i):
    return feetech.sign15(BUS.read_u16(i, 31))


def read_pos(i):
    return feetech.sign15(BUS.read_u16(i, 56))


def write_off(i, v):
    return BUS.write_u16(i, 31, enc_sm(v))


def wait_off_change(i, was, ms=20):
    """写完偏移就轮询它落地没有，最多等 ms 毫秒。比固定 sleep 强在：
    落地通常 1~2 ms，固定睡 20 ms 等于白送 18 ms 的重力下垂。返回 (读到的偏移, 变了没有)。"""
    end = time.monotonic() + ms / 1000.0
    off = was
    while True:
        off = read_off(i)
        if off != was or time.monotonic() >= end:
            return off, off != was


def probe_offset_sign(i):
    """探一次偏移的符号方向：扭矩开着时把偏移 +1，看读数往哪边动。

    只动 1 步（0.09°），而且在校准窗口之外做，不影响精度。同型号固件一致，探一次给 15 颗共用。
    兜底方案靠它一次写到位；探不出来就两个方向都试（慢，但仍然安全）。"""
    global OFFSET_SIGN
    if OFFSET_SIGN is not None:
        return OFFSET_SIGN
    try:
        o0, r0 = read_off(i), read_pos(i)
        if abs(o0 + 1) > OFFSET_LIMIT:
            return None
        BUS.unlock(i)
        write_off(i, o0 + 1)
        wait_off_change(i, o0)
        r1 = read_pos(i)
        write_off(i, o0)                      # 立刻还原，只是探一下
        wait_off_change(i, o0 + 1)
        BUS.lock(i)
        if r1 == r0:
            log(f"偏移符号探针：#{i} 偏移 +1 读数没变（{r0}），方向未知，兜底时两个方向都试", "校准", "warn")
            return None
        OFFSET_SIGN = 1 if r1 > r0 else -1
        log(f"偏移符号探针：#{i} 偏移 +1 → 读数 {r0}→{r1}，即「读数 = 原始 {'+' if OFFSET_SIGN > 0 else '−'} 偏移」", "校准")
        return OFFSET_SIGN
    except Exception as e:
        log(f"偏移符号探针失败：{type(e).__name__}: {e}", "校准", "warn", exc=True)
        return None


def calibrate_by_offset(i, target, trail):
    """兜底：舵机连 0x0B 也不认时，自己算偏移写寄存器 31。

    符号方向优先用探针的结果（一次写就够）；探不出来就两个方向都试、回读验证。
    只要没成功就把原偏移写回去，中途抛异常也一样 —— 绝不给舵机留一个乱写的偏移。
    返回 (成功与否, 校准后读数, 现在的偏移)。"""
    o0, r0 = read_off(i), read_pos(i)
    signs = [OFFSET_SIGN] if OFFSET_SIGN else [1, -1]
    ok, pos, off = False, r0, o0
    try:
        for sign in signs:
            cand = o0 + sign * (target - r0)
            if abs(cand) > OFFSET_LIMIT:
                trail.append(f"偏移{cand:+d}超量程跳过")
                continue
            write_off(i, cand)
            off, _ = wait_off_change(i, off)
            pos = read_pos(i)
            trail.append(f"写偏移{cand}→读数{pos}")
            if abs(pos - target) <= CALIB_TOLERANCE:
                ok = True
                return True, pos, off
        return False, pos, off
    finally:
        if not ok and off != o0:
            try:
                write_off(i, o0)
                off, _ = wait_off_change(i, off)
                trail.append(f"没成，偏移已还原成{o0}")
            except Exception as e:
                log(f"#{i} 兜底失败后还原偏移也失败了：{type(e).__name__}: {e}", "校准", "error", exc=True)


def calibrate_one(i, before, target=2048):
    """一颗舵机的位置校准：让它「现在这个位置」的读数变成 target（默认 2048 = 官方零位）。

    两条路：先用 0x0B 位置校准指令（舵机自己算偏移、自己存 EEPROM），不生效就自己算偏移写寄存器 31。
    HD-1910（固件 3.46）不认扭矩开关写 128：2026-09-20 实测 4 轮，解锁、扭矩关着写 128，
    15 颗偏移一个都没变，还照样回"成功"应答 —— 所以**判据不看应答，看偏移寄存器变没变**。

    关扭矩期间关节被重力压下去（实测颈部 0.62 步/毫秒），而 USB 串口一个来回 1~16 ms，窗口没法做到零。
    对策是把下垂**算出来补偿**：

        校准前读数 pre（扭矩还开着，舵机顶着你摆好的位置）
        校准后这个位置读 target（0x0B 的定义），偏移变了 Δ
        下垂 d = pre − target + 符号 × Δ          ← 三个数都读得到

    把偏移再挪 d，你摆好的那个位置就正好读 target。关节本身不动（停在垂下去的地方），
    扭矩恢复后也不会自己回去 —— 要回去用「回零位」按正常限速走。

    顺序：压低加速度/速度 → 解锁 → 读 pre → 关扭矩并**回读确认真关了** → 0x0B → 轮询偏移确认落地
    → 没落地就兜底 → 算下垂补偿 → 目标写成**当前读数**（永远不写 target，判断错了也不会拉关节）
    → 扭矩复原 → 加锁 → 恢复速度。中途出错，finally 里照样写目标、恢复扭矩、加锁。
    返回 {"ok", "pre", "pos", "target", "droop", "method", "why"}。"""
    was_on = before.get("torque") == 1
    old_off = before.get("offset")
    trail, step, ok, pre, pos, off, why = [], "限速", False, None, None, None, ""
    method, droop = "0x0B", 0
    try:
        # 安全网：校准期间把这颗压成慢速小加速度。万一哪一步判断错了，关节也是慢慢走，人能伸手挡住
        BUS.write_u8(i, 41, 10)
        BUS.write_u16(i, 46, enc_sm(200))
        step = "解锁"
        trail.append("解锁" + _ack(BUS.unlock(i)))
        step = "读当前位置"
        pre = read_pos(i)                     # 扭矩还开着，读到的就是舵机顶着的位置 = 你摆好的位置
        o_before = read_off(i)
        if was_on:
            step = "关扭矩"
            BUS.write_u8(i, 40, 0)
            if BUS.read_u8(i, 40) != 0:       # 这包丢了的话 0x0B 会在扭矩开着时执行，偏移一改就满速拉
                BUS.write_u8(i, 40, 0)
                if BUS.read_u8(i, 40) != 0:
                    raise feetech.BusError("关扭矩没生效（回读不是 0），不敢往下做")
            trail.append("关扭矩✓")
        step = "0x0B 位置校准"
        trail.append(f"0x0B→{target}" + _ack(BUS.calibrate_to(i, target)))
        step = "确认偏移变了"
        off, changed = wait_off_change(i, o_before)
        pos = read_pos(i)
        ok = changed or abs(pre - target) <= CALIB_TOLERANCE   # 本来就在目标上，偏移不变也算对
        if not ok:
            step = "兜底：写偏移寄存器 31"
            trail.append("0x0B 没让偏移变，改用写偏移")
            ok, pos, off = calibrate_by_offset(i, target, trail)
            method = "写偏移31"
        if ok:
            step = "算下垂并补偿"
            sign = OFFSET_SIGN or 1
            droop = pre - target + sign * (off - o_before)
            if abs(droop) > DROOP_TOLERANCE and abs(off - sign * droop) <= OFFSET_LIMIT:
                write_off(i, off - sign * droop)
                off, _ = wait_off_change(i, off)
                pos = read_pos(i)
                trail.append(f"关扭矩期间垂了{droop:+d}步，偏移再补{-sign * droop:+d}")
            elif abs(droop) > DROOP_TOLERANCE:
                trail.append(f"垂了{droop:+d}步但补偿超量程，没补")
        step = "写目标"
        trail.append(f"目标{pos}" + _ack(BUS.write_u16(i, 42, enc_sm(pos))))   # 永远写当前读数
        if was_on:
            step = "开扭矩"
            trail.append("开扭矩" + _ack(BUS.write_u8(i, 40, 1)))
        if not ok:
            why = f"0x0B 和写偏移都没让偏移变（还是 {fmt_off(o_before)}），读数 {pos} 不是 {target}"
    except Exception as e:
        ok = False
        why = f"卡在「{step}」：{type(e).__name__}: {e}"
        trail.append("✗" + why)
        log(f"#{i} {why}", "校准", "error", exc=True)
    finally:
        # 不管上面怎么错，都要：目标对准当前读数（下次开扭矩不会按旧坐标系把关节拉走）→ 扭矩复原 → 加锁 → 恢复速度
        try:
            p = read_pos(i)
            BUS.write_u16(i, 42, enc_sm(p))
            if was_on and BUS.read_u8(i, 40) != 1:
                BUS.write_u8(i, 40, 1)
                trail.append("扭矩已恢复")
        except Exception as e:
            trail.append(f"✗收尾（写目标/恢复扭矩）：{type(e).__name__}: {e}")
            log(f"#{i} 收尾失败，这颗可能卸着力、目标寄存器指向旧位置，开扭矩前先手动写目标："
                f"{type(e).__name__}: {e}", "校准", "error", exc=True)
        try:
            BUS.lock(i)
            locked = BUS.read_u8(i, 55)
            trail.append("加锁✓" if locked == 1 else f"加锁后锁标志读回 {locked}")
        except Exception as e:
            trail.append(f"✗加锁：{type(e).__name__}: {e}")
            log(f"#{i} 加锁失败，舵机可能还是解锁状态：{type(e).__name__}: {e}", "校准", "error", exc=True)
        try:
            release_speed([i])
        except Exception:
            pass
    gap = "" if pre is None else f"，差 {(target - pre) * 360 / 4096:+.1f}°"
    head = (f"#{i} 读数 {pre if pre is not None else '?'}→{pos if pos is not None else '?'}（目标 {target}{gap}，{method}）"
            f" 偏移 {fmt_off(old_off)}→{fmt_off(off)}"
            + (f" 下垂{droop:+d}步" if droop else "")
            + ("" if before.get("lock") == 1 else f" 校准前锁标志 {before.get('lock')}")
            + f" 扭矩原来{'开' if was_on else '关'}")
    log(head + " · " + " ".join(trail) + ("" if ok else f" · 失败：{why}"), "校准", "info" if ok else "warn")
    return {"ok": ok, "pre": pre, "pos": pos, "target": target, "droop": droop, "method": method, "why": why}


def save_json_atomic(path, obj):
    """先写 .tmp 再改名：json.dump 写一半崩了会留个半截文件，
    而读不了的姿态/备份文件只会被静默跳过，等于悄悄丢数据。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# 官方姿势：microduck_rl src/mjlab_microduck/robot/microduck/scene.xml 的 keyframe（d424a0c），弧度、模型约定。
# 跟 index.html 的 OFFICIAL_POSES 是同一份。只有「折叠」能当校准基准：鸭子折好能自己趴住，膝靠零件顶住
OFFICIAL_FOLD = {20: 0, 21: 0, 22: 1.57, 23: 1.57, 24: 0, 30: 1, 31: 1, 32: 0, 33: 0,
                 10: 0, 11: 0, 12: -1.57, 13: -1.57, 14: 0}
# 校准用的折叠：腿按官方 FOLD，头颈摆正（颈、头 pitch 0°）、嘴闭上。官方 FOLD 的颈 / 头 57° 不是接触点
# （腿折好时颈要转到 66.6° 才碰上零件，fold_contact 算的），靠眼睛摆不准；脖子竖直、头平视好摆得多。零点照样是官方 2048
FOLD_CALIB = {**OFFICIAL_FOLD, 30: 0, 31: 0, 34: 0}
OFFICIAL_REFS = {"@official:FOLD": ("官方折叠（头颈摆正）", FOLD_CALIB)}


def official_targets(ref):
    """官方基准的目标读数：2048 + 角度 × 4096/2π × ±。± 用 directions.json，跟 3D、姿态发送一致。"""
    label, rad = OFFICIAL_REFS[ref]
    dirs = load_dirs()
    return label, {i: int(round(2048 + a * 4096 / (2 * math.pi) * dirs.get(i, 1))) for i, a in rad.items()}


def read_pose_file(fn):
    if not fn or os.path.basename(fn) != fn or not fn.endswith(".json"):
        raise ValueError(f"不认识的姿态文件 {fn!r}")
    with open(os.path.join(POSES_DIR, fn), encoding="utf-8") as f:
        return json.load(f)


def calib_targets(ref):
    """基准 → (名字, {id: 目标读数}, 是不是官方基准)。页面确认框和真校准用同一份，不各算各的。"""
    if not ref:
        return "零位 2048", {i: 2048 for i in IDS}, False
    if ref in OFFICIAL_REFS:
        label, targets = official_targets(ref)
        return label, targets, True
    pose = read_pose_file(ref)
    return (f"姿态「{pose.get('name', ref)}」（{ref}）",
            {int(r["id"]): int(r["pos"]) for r in pose.get("rows", [])}, False)


def calib_plan(ids, ref=None):
    """校准前给页面看：每颗现在读多少、要写成多少、差几度。只读，不写舵机。
    差得多（> CALIB_BIG_DEG）不拦：装舵盘时转了半圈就是这样，校准正好把它纠回来；
    页面会再确认一次，提醒排除「方向 ± 设反了」。"""
    name, targets, _ = calib_targets(ref)
    st = BUS.states(ids)
    rows = []
    for i in ids:
        s, t = st.get(i), targets.get(i)
        pos = s["pos"] if s else None
        rows.append({"id": i, "joint": JOINT_NAMES.get(i), "pos": pos, "target": t,
                     "diff_deg": None if pos is None or t is None else round((t - pos) * 360 / 4096, 1)})
    return {"type": "calib_plan", "ref": ref, "name": name, "rows": rows, "big_deg": CALIB_BIG_DEG, "fake": is_fake()}


def calibrate_mid_all(ids, ref=None):
    """（见下）。整段持 CALIB_LOCK：状态轮询和另一个标签页都得让路。"""
    with CALIB_LOCK:
        return _calibrate_mid_all(ids, ref)


def _calibrate_mid_all(ids, ref=None):
    """把选中舵机「现在的位置」校准成目标读数，一颗一颗来：每颗只关几毫秒扭矩，别的舵机照样撑着。

    ref=None：目标都是 2048，鸭子要摆成官方零位（腿伸直、脚板垂直、头平视、嘴闭上）。
    ref=姿态文件名：目标是那个姿态里存的读数。比如「缩」：鸭子自己能缩着放稳，靠机械接触定位，比手扶着摆直准。
    零位始终是官方的 2048，姿态只当「校准夹具」用，所以官方代码、策略、别的姿态都不用加 offset。

    存的姿态挪不挪：校到 2048 是换了零位，之前存的姿态是旧读数，要跟着挪；
    按姿态校准是把舵机拉回基准姿态那套读数，别的姿态跟基准是同一套读数存的，不挪（舵机换过、舵盘重装过，校回来它们就又对了）。"""
    # 官方基准（折叠）跟零位 2048 一样是在定零点：目标来自官方角度，不是存的读数，所以存的姿态要跟着换算
    name, targets, official = calib_targets(ref)
    if official:
        name += "（" + " ".join(f"{i}→{targets[i]}" for i in ids if i in targets) + "）"
    elif not ref:
        targets = {i: 2048 for i in ids}
    pose_ref = bool(ref) and not official
    sim = is_fake()
    # 模拟：虚拟舵机的读数跟真舵机没关系，存的姿态（真舵机的读数）一个都不挪
    shift = not pose_ref and not sim
    log(f"开始校准 {len(ids)} 颗到{name}：{ids}" + ("（模拟：只改虚拟舵机，备份放临时目录，存的姿态不挪）" if sim else ""), "校准")
    fn, before = backup_calibration(ids)
    log(f"原来的偏移、读数、扭矩、锁标志备份在 {'临时目录 ' + calib_dir() + os.sep if sim else 'calib/'}{fn}", "校准")
    probe_offset_sign(next((i for i in ids if "offset" in before.get(str(i), {})), ids[0]))
    ok, bad, deltas, big = [], {}, {}, {}
    for i in ids:
        b = before.get(str(i), {})
        if i not in targets:
            bad[i] = "参考姿态里没有这颗"
            continue
        if not 0 <= targets[i] <= 4095:               # 姿态文件是能手改的 JSON，改错一个数就校到离谱的读数上
            bad[i] = f"目标 {targets[i]} 不在 0~4095，没动它"
            continue
        if "offset" not in b:
            bad[i] = "备份时读不到，没动它"
            continue
        r = calibrate_one(i, b, targets[i])
        if r["ok"]:
            ok.append(i)
            # 同一个物理位置（你摆好的那个），读数从 pre 变成了 target —— 下垂已经补偿掉了
            deltas[str(i)] = r["target"] - r["pre"]
            if abs(deltas[str(i)]) * 360 / 4096 > CALIB_BIG_DEG:
                big[i] = deltas[str(i)] * 360 / 4096
        else:
            bad[i] = r["why"]
    # 先把「打算挪哪些」落盘再真挪：反过来的话，中间崩了就再也挪不回去了
    bp = os.path.join(calib_dir(), fn)
    with open(bp, encoding="utf-8") as f:
        rec = json.load(f)
    plan = [p["file"] for p in list_poses() if p.get("goals")] if shift else []
    rec.update({"ref": ref, "targets": {str(k): v for k, v in targets.items() if k in ids},
                "deltas": deltas, "shifted": plan, "bad": {str(k): v for k, v in bad.items()}, "sim": sim})
    save_json_atomic(bp, rec)
    moved = shift_saved_poses(deltas, only=set(plan)) if shift else []
    if moved != plan:
        rec["shifted"] = moved
        save_json_atomic(bp, rec)
    if moved:
        log(f"存的姿态换算到新零点：{moved}（" + " ".join(f"#{k}{v:+d}" for k, v in deltas.items() if v) + "）", "姿态")
    if big:
        # 不拦，只标出来：装舵盘时转了半圈会差 180°，校准正好纠回来；方向 ± 设反了也会差这么多，那种校进去是错的
        log(f"这几颗摆好的位置离目标差 {CALIB_BIG_DEG}° 以上：" + " ".join(f"#{i} {d:+.1f}°" for i, d in big.items())
            + "。装舵盘时转了半圈没关系；要是方向 ± 设反了，这次校进去的零点是错的：开扭矩用滑块推一点，"
            "看 3D 里这个关节跟实物转的方向是不是一致，不一致就撤销、改 ±、重校", "校准", "warn")
    log(f"校准完（{name}）：成功 {len(ok)} 颗 {ok}" + (f"；失败 {len(bad)} 颗 {sorted(bad)}，原因见上面每颗那行" if bad else "")
        + f"；备份 {'临时目录（模拟）' if sim else 'calib/'}{fn}", "校准", "warn" if bad or big else "info")
    return {"type": "calib_result", "ok": ok, "bad": {str(k): v for k, v in bad.items()}, "backup": fn,
            "big": {str(k): round(v, 1) for k, v in big.items()}}


def undo_calibration():
    """（见下）。整段持 CALIB_LOCK。"""
    with CALIB_LOCK:
        return _undo_calibration()


def _undo_calibration():
    """把最近一次备份里的偏移写回去（关扭矩 → 解锁 → 写 31 → 加锁），目标位置改成当前读数，扭矩状态照旧。"""
    d = calib_dir()
    files = sorted(f for f in os.listdir(d) if f.startswith("eeprom-")) if os.path.isdir(d) else []
    if not files:
        log("没有可撤销的中位校准" + ("（模拟模式只撤销模拟里做的校准）" if is_fake() else ""), "校准", "warn")
        return {"type": "calib_result", "ok": [], "bad": {}, "backup": None}
    fn = files[-1]
    with open(os.path.join(d, fn), encoding="utf-8") as f:
        rec = json.load(f)
    log(f"开始撤销：把 calib/{fn}（{rec.get('time', '?')}）里的偏移写回去", "校准")
    ok, bad = [], {}
    for k, row in rec["servos"].items():
        i = int(k)
        if "offset" not in row:
            log(f"#{i} 备份里没有偏移（{row.get('error', '?')}），跳过", "校准", "warn")
            continue
        trail, step, on = [], "读当前状态", False
        try:
            on = BUS.read_u8(i, 40) == 1
            off0, p0 = BUS.read_u16(i, 31), read_pos(i)
            if on:
                step = "关扭矩"
                BUS.write_u8(i, 40, 0)
                if BUS.read_u8(i, 40) != 0:     # 跟校准一样：这包丢了，改偏移就等于让关节满速跑
                    BUS.write_u8(i, 40, 0)
                    if BUS.read_u8(i, 40) != 0:
                        raise feetech.BusError("关扭矩没生效（回读不是 0），不敢写偏移")
                trail.append("关扭矩✓")
            step = "解锁"
            trail.append("解锁" + _ack(BUS.unlock(i)))
            step = "写偏移"
            trail.append(f"写偏移{fmt_off(row['offset'])}" + _ack(BUS.write_u16(i, 31, row["offset"])))
            step = "读回"
            wait_off_change(i, feetech.sign15(off0))   # 轮询落地，别固定睡 20 ms 让关节多垂
            off1, p1 = BUS.read_u16(i, 31), read_pos(i)
            step = "写目标"
            trail.append(f"目标{p1}" + _ack(BUS.write_u16(i, 42, enc_sm(p1))))
            if on:
                step = "开扭矩"
                trail.append("开扭矩" + _ack(BUS.write_u8(i, 40, 1)))
            good = off1 == row["offset"]
            (ok.append(i) if good else bad.__setitem__(i, f"偏移写了 {row['offset']} 读回 {off1}"))
            log(f"#{i} 偏移 {fmt_off(off0)}→{fmt_off(off1)}（备份值 {fmt_off(row['offset'])}） 读数 {p0}→{p1} · " + " ".join(trail),
                "校准", "info" if good else "warn")
        except Exception as e:
            bad[i] = f"卡在「{step}」：{type(e).__name__}: {e}"
            log(f"#{i} 撤销{bad[i]}（扭矩{'没恢复' if on else '本来就关着'}）", "校准", "error", exc=True)
        finally:
            try:                                       # 跟校准同样的收尾：目标对准当前读数再恢复扭矩
                p = read_pos(i)
                BUS.write_u16(i, 42, enc_sm(p))
                if on and BUS.read_u8(i, 40) != 1:
                    BUS.write_u8(i, 40, 1)
            except Exception as e:
                log(f"#{i} 撤销收尾失败，这颗可能卸着力：{type(e).__name__}: {e}", "校准", "error", exc=True)
            try:
                BUS.lock(i)
            except Exception as e:
                log(f"#{i} 加锁失败：{type(e).__name__}: {e}", "校准", "error", exc=True)
    if rec.get("sim"):
        pass                                           # 模拟里校的：当时就没挪姿态，撤销也不挪
    elif "shifted" in rec:
        # 当次挪过的要挪回来；校准之后新存的姿态是按新坐标系存的，也得挪回去，
        # 不然撤销完它们指向的物理姿势整体偏了 delta 步，一发就把关节顶到限位
        later = []
        t0 = rec.get("time", "")
        for p in list_poses():
            if p["file"] in rec["shifted"] or not p.get("goals"):
                continue
            if (p.get("time") or "") >= t0[:16]:
                later.append(p["file"])
        moved = shift_saved_poses(rec.get("deltas", {}), sign=-1, only=set(rec["shifted"]) | set(later))
        if moved:
            log(f"存的姿态换算回原来的零点：{moved}" + (f"（其中 {later} 是校准之后存的，按新零点存的也要挪）" if later else ""), "姿态")
    elif any(rec.get("deltas", {}).values()):
        log("这份备份是 0.8.0 以前的，没记当时挪了哪些姿态，姿态不自动挪回，需要的话手动核对", "姿态", "warn")
    os.rename(os.path.join(d, fn), os.path.join(d, "undone-" + fn))
    log(f"撤销完（{fn} 已改名 undone-{fn}）：{len(ok)} 颗偏移写回" + (f"；失败 {sorted(bad)}" if bad else ""),
        "校准", "warn" if bad else "info")
    return {"type": "calib_result", "ok": ok, "bad": {str(k): v for k, v in bad.items()}, "backup": fn}


def list_poses():
    out = []
    if os.path.isdir(POSES_DIR):
        for fn in sorted(os.listdir(POSES_DIR)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(POSES_DIR, fn), encoding="utf-8") as f:
                        p = json.load(f)
                    out.append({"file": fn, "name": p.get("name", fn[:-5]), "time": p.get("time", ""), "note": p.get("note", ""),
                                "goals": {r["id"]: r["pos"] for r in p.get("rows", [])},
                                "steps": p.get("steps")})
                except Exception as e:
                    log(f"姿态文件 {fn} 读不了：{type(e).__name__}: {e}", "姿态", "warn")
    return out


def save_pose(name):
    st = BUS.states(PRESENT or IDS)
    rows = [{"id": i, "joint": JOINT_NAMES.get(i), "pos": s["pos"], "deg": round((s["pos"] - 2048) * 360 / 4096, 1)}
            for i, s in st.items() if s]
    os.makedirs(POSES_DIR, exist_ok=True)
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "pose"
    fn = f"{safe}-{time.strftime('%Y-%m-%d')}.json"
    with open(os.path.join(POSES_DIR, fn), "w", encoding="utf-8") as f:
        json.dump({"name": safe, "time": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}, f, ensure_ascii=False, indent=1)
    missing = [i for i, v in st.items() if not v]
    log(f"姿态已存 poses/{fn}（{len(rows)} 颗）" + (f"；读不到 {missing}，没存进去" if missing else ""), "姿态", "warn" if missing else "info")
    return fn


def delete_pose(fn):
    """不真删：挪进 poses/.trash/，文件名前加时间，删错了从那里拿回来。只认 poses/ 里的 .json。"""
    if not fn or os.path.basename(fn) != fn or not fn.endswith(".json"):
        raise ValueError(f"不认识的姿态文件 {fn!r}")
    src = os.path.join(POSES_DIR, fn)
    if not os.path.isfile(src):
        raise ValueError(f"poses/ 里没有 {fn}")
    trash = os.path.join(POSES_DIR, ".trash")
    os.makedirs(trash, exist_ok=True)
    dst = os.path.join(trash, f"{time.strftime('%Y%m%d-%H%M%S')}-{fn}")
    os.replace(src, dst)
    log(f"姿态已删除：{fn}（挪到 poses/.trash/，要找回就挪回来）", "姿态")


async def logfile(request):
    path = log_path()
    if not os.path.exists(path):
        return JSONResponse({"error": "今天还没有日志"}, status_code=404)
    return FileResponse(path, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


async def poses(request):
    return JSONResponse(list_poses())


async def info(request):
    return JSONResponse({"version": VERSION, "ids": IDS, "present": PRESENT, "names": JOINT_NAMES, "fake": is_fake(),
                         "stats": BUS.stats, "log": LOG[-50:]})


def torque_on_aligned(ids):
    """开扭矩前先把目标（42）对准当前读数。扭矩关着时用手掰过关节、或者关着时发过姿势 / 校准过，
    目标寄存器里还是旧值，一开扭矩舵机就按放开的速度冲过去。
    顺序：读扭矩 → 关着的读位置 → 写目标 = 位置（带应答）→ 直接读 42 核对 → 开扭矩。
    核对读 42 本身，不看状态里的 67：67 在真机上跟不跟得上没验证过。本来就开着的不动它的目标（可能正在走）。
    读不到位置、目标写不进去的那颗不开。返回 (开了的, {id: 目标}, {id: 没开的原因})。"""
    tq = BUS.sync_read(ids, 40, 1)
    already = [i for i in ids if tq.get(i) and tq[i][1][0] == 1]
    need = [i for i in ids if i not in already]
    aligned, bad = {}, {}
    st = BUS.states(need) if need else {}
    for i in need:
        if not st.get(i):
            bad[i] = "读不到位置，没开扭矩（开了可能冲向旧目标）"
            continue
        p = st[i]["pos"]
        try:
            for _ in range(2):
                BUS.write_u16(i, 42, enc_sm(p))
                got = feetech.sign15(BUS.read_u16(i, 42))
                if got == p:
                    aligned[i] = p
                    break
            else:
                bad[i] = f"目标写 {p} 读回 {got}，没开扭矩"
        except Exception as e:
            bad[i] = f"写目标出错（{type(e).__name__}: {e}），没开扭矩"
    on = already + [i for i in need if i in aligned]
    if on:
        BUS.sync_write(40, 1, [(i, bytes([1])) for i in on])
    return on, aligned, bad


def do_scan(ids):
    global PRESENT
    found = [i for i in ids if BUS.ping(i) is not None]
    PRESENT = found
    lost = [i for i in ids if i not in found] if len(ids) <= 32 else []
    log(f"扫描 {ids[0]}..{ids[-1]}：在线 {len(found)} 颗 {found}" + (f"；不在线 {lost}" if lost else ""),
        "总线", "warn" if lost else "info")
    return found


def do_timing(n=200):
    """sync_read 全部在线舵机 n 次，看耗时和失败。"""
    ids = PRESENT or IDS
    times, fails = [], 0
    for _ in range(n):
        t = time.perf_counter()
        r = BUS.sync_read(ids, 56, 15)
        times.append((time.perf_counter() - t) * 1000)
        fails += sum(1 for v in r.values() if v is None)
    times.sort()
    res = {"n": n, "ids": len(ids), "min_ms": round(times[0], 2), "avg_ms": round(sum(times) / n, 2),
           "p99_ms": round(times[max(0, int(n * 0.99) - 1)], 2), "max_ms": round(times[-1], 2), "fails": fails,
           "stats": dict(BUS.stats)}
    log(f"时序测试：{res}", "总线", "warn" if fails else "info")
    return res


SIM_IMU_OWNED = False   # 当前 IMU_SERVICE 是不是页面「模拟」里接上的（启动参数给的不归它管）
SIM_IMU_LOCK = threading.Lock()   # 两个标签页同时点、或者跟「连接串口」撞上，别各改一半


def imu_config_frame():
    return {"type": "imu_config", "enabled": IMU_SERVICE is not None,
            "protocol": getattr(IMU_SERVICE, "protocol", None), "sim_imu": sim_imu_mode()}


def drop_sim_imu(why):
    """接上真串口时把模拟里接的虚拟小板拔掉：真总线上没有它，留着页面会一直显示假姿态。启动参数给的 IMU 不动。"""
    global IMU_SERVICE, SIM_IMU_OWNED
    with SIM_IMU_LOCK:
        if not SIM_IMU_OWNED:
            return False
        IMU_SERVICE, SIM_IMU_OWNED = None, False
    log(f"{why}：模拟里接的虚拟 IMU 小板已拔掉；真 IMU 用启动参数 --imu-bus / --imu-swd / --imu-jlink", "IMU")
    return True


def sim_imu_mode():
    """模拟 IMU 现在是：off 不接 / on 虚拟小板在线 / lost 虚拟小板掉线；不是模拟接上的返回 None。"""
    if not SIM_IMU_OWNED or IMU_SERVICE is None:
        return "off" if is_fake() and IMU_SERVICE is None else None
    return "on" if getattr(BUS, "imu200", None) is not None else "lost"


def set_sim_imu(mode):
    """模拟模式下接 / 拔虚拟 IMU 小板。走的是 --imu-bus 同一条路：虚拟小板挂在假总线上答 ID 200。"""
    with SIM_IMU_LOCK:
        return _set_sim_imu(mode)


def _set_sim_imu(mode):
    global IMU_SERVICE, SIM_IMU_OWNED
    import imu_bus
    if not is_fake():
        raise ValueError("接了真串口，不能用虚拟 IMU；真 IMU 用启动参数 --imu-bus / --imu-swd / --imu-jlink")
    if IMU_SERVICE is not None and not SIM_IMU_OWNED:
        raise ValueError("服务启动时已经指定了 IMU（启动参数），页面里不改")
    if mode == "off":
        IMU_SERVICE, SIM_IMU_OWNED = None, False
        BUS.imu200 = None
        log("模拟：拔掉虚拟 IMU 小板，页面隐藏姿态面板", "IMU")
    elif mode in ("on", "lost"):
        if IMU_SERVICE is None:
            IMU_SERVICE, SIM_IMU_OWNED = imu_bus.BusImuService(poll_hz=FAKE_STREAM_HZ), True
        if mode == "on":
            if BUS.imu200 is None:
                BUS.imu200 = imu_bus.FakeImu200()
            log("模拟：接上虚拟 IMU 小板（假总线上的 ID 200，跟 --imu-bus 同一条路）", "IMU")
        else:
            BUS.imu200 = None
            log("模拟：虚拟小板掉线 —— 看调试台怎么把 200 出列、改成单独 PING", "IMU")
    else:
        raise ValueError(f"不认识的模拟 IMU 模式：{mode}")
    return imu_config_frame()


def handle(cmd):
    """WebSocket 指令，同步执行（在线程里跑）。返回要回给前端的 dict 或 None。"""
    op = cmd.get("op")
    if op == "sim_imu":
        set_sim_imu(cmd.get("mode"))
        return None                  # ws_endpoint 把新配置广播给所有页面
    if op == "imu_protocol":
        service = IMU_SERVICE
        if service is None or imu_backend(service) != "bus":
            raise ValueError("只有启用 --imu-bus 时才能切换总线协议")
        protocol = cmd.get("protocol")
        if protocol not in ("dxl2", "feetech"):
            raise ValueError("协议必须是 dxl2 或 feetech")
        # Wait for an in-flight serial transaction to finish before changing
        # protocol, so the old request cannot be mistaken for the new one.
        if isinstance(BUS, FakeBus):
            changed = service.set_protocol(protocol)
        else:
            with BUS._io:
                changed = service.set_protocol(protocol)
        log(f"IMU 总线协议{'切换为 ' + protocol if changed else '保持 ' + protocol}", "IMU")
        return imu_config_frame()
    if op == "goal":
        BUS.write_u16(int(cmd["id"]), 42, int(cmd["pos"]) & 0xFFFF)
    elif op == "goals":  # {id: pos}
        items = [(int(i), struct.pack("<H", int(p) & 0xFFFF)) for i, p in cmd["goals"].items()]
        BUS.sync_write(42, 2, items)
    elif op == "goals_stream":
        # 连续流：每帧给目标位置，同时把速度上限设成"正好在 dt 内走到"，加速度最大。
        # 舵机就以匹配的速度连续走，不会每帧冲一下停一下。结束时发 stream_end 放开速度。
        dt = max(0.01, float(cmd.get("dt", 0.05)))
        want = {int(i): int(p) & 0xFFFF for i, p in cmd["goals"].items()}
        fresh = [i for i in want if i not in STREAM_LAST]
        if fresh:
            st = BUS.states(fresh)
            for i in fresh:
                STREAM_LAST[i] = (st[i]["pos"] if st.get(i) else want[i])
        items = []
        for i, p in want.items():
            sps = abs(p - STREAM_LAST[i]) / dt * 1.2          # 留 20% 余量，宁可早到一点
            reg = max(3, min(32767, int(round(sps / SPEED_UNIT))))
            items.append((i, bytes([0]) + struct.pack("<H", p) + struct.pack("<H", 0) + struct.pack("<H", reg)))
            STREAM_LAST[i] = p
        BUS.sync_write(41, 7, items)
    elif op == "stream_end":
        ids = [int(i) for i in cmd.get("ids", [])] or list(STREAM_LAST)
        if ids:
            release_speed(ids)
        for i in ids:
            STREAM_LAST.pop(i, None)
    elif op == "goals_profile":
        # 平滑运动：一次写 41..47 = 加速度(1) 目标位置(2) 时间(2)=0 速度(2)，舵机自己走梯形曲线到终点。
        # 速度按 |终点-当前| / 秒数 算，单位由相位 BIT2 定（SPEED_UNIT 步/秒 每单位）。
        # 发完立刻返回，不在这里等——等的话会把后面的指令（比如灵动的头部流）全堵住。页面等完自己发 goals_verify 和 release。
        seconds = max(0.2, float(cmd.get("seconds", 2)))
        acc = max(1, min(254, int(cmd.get("acc", 30))))
        want = {int(i): int(p) & 0xFFFF for i, p in cmd["goals"].items()}
        st = BUS.states(list(want))
        items, speeds = [], {}
        for i, p in want.items():
            cur = st[i]["pos"] if st.get(i) else p
            sps = abs(p - cur) / seconds                       # 步/秒
            reg = max(1, min(32767, int(round(sps / SPEED_UNIT)))) if sps > 0 else 1
            speeds[i] = reg
            items.append((i, bytes([acc]) + struct.pack("<H", p) + struct.pack("<H", 0) + struct.pack("<H", reg)))
        BUS.sync_write(41, 7, items)
        for i in want:
            STREAM_LAST.pop(i, None)
        log(f"平滑 {seconds}s acc={acc} 速度单位={SPEED_UNIT:g}步/s → " + " ".join(f"{i}:{want[i]}@{speeds[i]}" for i in want), "运动")
    elif op == "release":
        ids = [int(i) for i in cmd.get("ids", [])] or (PRESENT or IDS)
        release_speed(ids)
        for i in ids:
            STREAM_LAST.pop(i, None)
    elif op == "goals_verify":  # 同上，但回读舵机里的目标位置，没收到的重发，最多 4 轮
        want = {int(i): int(p) & 0xFFFF for i, p in cmd["goals"].items()}
        for attempt in range(4):
            BUS.sync_write(42, 2, [(i, struct.pack("<H", p)) for i, p in want.items()])
            time.sleep(0.02)
            st = BUS.states(list(want))
            want = {i: p for i, p in want.items() if not st.get(i) or (st[i]["goal"] & 0xFFFF) != p}
            if not want:
                break
        if want:
            log(f"目标回读：{len(want)} 颗 4 轮后目标仍没写进去 {sorted(want)}", "运动", "warn")
        return {"type": "goals_verify", "missing": sorted(want), "attempts": attempt + 1}
    elif op == "torque":
        if cmd.get("ids"):
            ids = [int(i) for i in cmd["ids"]]
        else:
            ids = [int(cmd["id"])] if cmd.get("id") is not None else (PRESENT or IDS)
        if int(cmd["on"]):
            on, aligned, bad = torque_on_aligned(ids)
            log(f"扭矩开 → {on}" + (f"；开之前目标对准当前位置：" + " ".join(f"#{i}→{p}" for i, p in aligned.items()) if aligned else "")
                + (f"；没开 " + " ".join(f"#{i}（{w}）" for i, w in bad.items()) if bad else ""), "运动", "warn" if bad else "info")
            return {"type": "torque", "on": 1, "ids": on, "aligned": {str(i): p for i, p in aligned.items()},
                    "bad": {str(i): w for i, w in bad.items()}}
        BUS.sync_write(40, 1, [(i, bytes([0])) for i in ids])
        log(f"扭矩关 → {ids}", "运动")
    elif op == "scan":
        return {"type": "scan", "present": do_scan(parse_ids(cmd.get("ids") or DEFAULT_IDS))}
    elif op == "scan_all":
        return {"type": "scan", "present": do_scan(list(range(0, 254)))}
    elif op == "dump":
        sid = int(cmd["id"])
        d = BUS.dump(sid)
        r = d["raw"]
        log(f"导出 #{sid}：固件 {r[0]}.{r[1]} 舵机 {r[3]}.{r[4]} END={r[2]} 模式={r[33]} 相位={r[18]} "
            f"P/D/I={r[21]}/{r[22]}/{r[23]} 偏移={fmt_off(r[31] | r[32] << 8)} 扭矩={r[40]} 锁={r[55]} 应答级别={r[8]}", "寄存器")
        return {"type": "dump", "id": sid, **d}
    elif op == "write_reg":
        sid, addr, size, val = int(cmd["id"]), int(cmd["addr"]), int(cmd.get("size", 1)), int(cmd["value"])
        unlock = bool(cmd.get("unlock"))
        name = feetech.REGISTERS.get(addr, ("?",))[0]
        rd = BUS.read_u8 if size == 1 else BUS.read_u16
        old = rd(sid, addr)
        if unlock:
            BUS.unlock(sid)
        try:
            err = (BUS.write_u8 if size == 1 else BUS.write_u16)(sid, addr, val)
        finally:
            if unlock:
                BUS.lock(sid)
        new = rd(sid, addr)
        same = new == val & (0xFF if size == 1 else 0xFFFF)
        log(f"#{sid} 写 {addr}「{name}」{old} → {val}{_ack(err)}，读回 {new}" + ("（解锁写入，已重新加锁）" if unlock else "")
            + ("" if same else "（读回跟写的不一样：只读？超范围？扭矩开关写 128 这类命令值本来就读不回）"),
            "寄存器", "info" if same else "warn")
    elif op == "reboot":
        sid = int(cmd["id"])
        BUS.write_u8(sid, 40, 0)
        BUS.reboot(sid)
        log(f"#{sid} 重启 0x08（先关了扭矩，约 0.8 s 后回来）", "寄存器")
    elif op == "calibrate":
        return calibrate_mid_all([int(cmd["id"])], cmd.get("ref") or None)
    elif op == "set_id":
        ok = BUS.set_id(int(cmd["old"]), int(cmd["new"]))
        log(f"改 ID {cmd['old']} → {cmd['new']}：" + ("新 ID ping 得通" if ok else "新 ID ping 不通"), "寄存器", "info" if ok else "error")
        return {"type": "scan", "present": do_scan(IDS)}
    elif op == "timing":
        return {"type": "timing", **do_timing(int(cmd.get("n", 200)))}
    elif op == "log":
        return {"type": "logs", "items": LOG[-100:]}
    elif op == "calib_mid_all":
        ids = [int(i) for i in cmd.get("ids") or []] or list(PRESENT or IDS)
        return calibrate_mid_all([i for i in ids if i in (PRESENT or IDS)], cmd.get("ref") or None)
    elif op == "calib_plan":
        ids = [int(i) for i in cmd.get("ids") or []] or list(PRESENT or IDS)
        return {**calib_plan([i for i in ids if i in (PRESENT or IDS)], cmd.get("ref") or None),
                "for": cmd.get("for"), "id": cmd.get("id")}
    elif op == "ports":
        return {"type": "ports", "ports": list_ports(), "port": PORT, "fake": is_fake()}
    elif op == "reconnect":
        port = str(cmd.get("port") or PORT or "")
        if not port:
            return {"type": "reconnect", "ok": False, "msg": "先选一个串口"}
        r = open_bus(port, int(cmd.get("baud") or BAUD))
        return {"type": "reconnect", **r, "port": PORT, "fake": is_fake(), "present": PRESENT,
                "ports": list_ports()}
    elif op == "calib_undo":
        return undo_calibration()
    elif op == "set_dir":
        dirs = load_dirs()
        sid, d = int(cmd["id"]), (-1 if int(cmd["dir"]) < 0 else 1)
        dirs[sid] = d
        save_dirs(dirs)
        log(f"方向 #{sid} → {d:+d}（已存 directions.json）", "方向")
        return {"type": "dirs", "dirs": dirs}
    elif op == "save_pose":
        save_pose(str(cmd.get("name") or "pose"))
        return {"type": "poses", "poses": list_poses()}
    elif op == "poses":
        return {"type": "poses", "poses": list_poses()}
    elif op == "page_log":
        log(str(cmd.get("msg", ""))[:500], str(cmd.get("cat") or "页面"), cmd.get("level") if cmd.get("level") in ("info", "warn", "error") else "info")
    elif op == "delete_pose":
        delete_pose(str(cmd.get("file") or ""))
        return {"type": "poses", "poses": list_poses()}
    else:
        log(f"未知指令 {cmd}", "系统", "warn")
    return None


async def broadcast(text):
    for ws in list(CLIENTS):
        try:
            await ws.send_text(text)
        except Exception:
            CLIENTS.discard(ws)


def read_states_with_imu(ids, service):
    """Read the IMU with its configured protocol and HD-1910 servos with Feetech."""
    import imu_bus
    servo_ids = [i for i in ids if i != imu_bus.IMU_ID]
    with_imu = service.include_in_sync()
    if not with_imu and service.due_probe():
        if isinstance(BUS, FakeBus):
            answered = BUS.ping(imu_bus.IMU_ID) is not None
        elif service.protocol == "dxl2":
            import dxl2
            answered = dxl2.Dxl2Bus(BUS).ping(imu_bus.IMU_ID) is not None
        else:
            answered = BUS.ping(imu_bus.IMU_ID) is not None
        if service.probe_result(answered):
            log(f"ID 200 的 {service.protocol} PING 有应答了，恢复 IMU 轮询", "IMU")
        with_imu = service.include_in_sync()
    if isinstance(BUS, FakeBus):
        st = BUS.states(servo_ids)
        got = BUS.sync_read([imu_bus.IMU_ID], imu_bus.BLOCK_ADDR, imu_bus.BLOCK_LEN) if with_imu else {}
        v = got.get(imu_bus.IMU_ID)
        if with_imu or v is not None:
            block = v[1] if v else None
            if block is not None and service.protocol == "dxl2":
                block = block[:imu_bus.DXL2_BLOCK_LEN]
            service.feed(block)
        return st

    if service.protocol == "dxl2":
        import dxl2
        imu_client = dxl2.Dxl2Bus(BUS)
        if with_imu:
            result = imu_client.sync_read([imu_bus.IMU_ID], imu_bus.DXL2_BLOCK_ADDR,
                                          imu_bus.DXL2_BLOCK_LEN).get(imu_bus.IMU_ID)
            service.feed(result[1] if result and result[0] == 0 and
                         len(result[1]) == imu_bus.DXL2_BLOCK_LEN else None)
        return BUS.states(servo_ids) if servo_ids else {}

    ids_sent = ([imu_bus.IMU_ID] if with_imu else []) + servo_ids
    got = BUS.sync_read(ids_sent, imu_bus.BLOCK_ADDR, imu_bus.BLOCK_LEN) if ids_sent else {}
    st = {sid: (feetech.FeetechBus._decode_state(*got[sid]) if got.get(sid) else None) for sid in servo_ids}
    v = got.get(imu_bus.IMU_ID)
    if with_imu or v is not None:
        service.feed(v[1] if v else None)
    return st


def stream_should_poll():
    """这一拍要不要读总线。--imu-bus 时没扫到舵机也要读：台架上可能只接了小板（ID 200 不在扫描范围里）。"""
    return bool(CLIENTS) and (bool(PRESENT) or imu_backend(IMU_SERVICE) == "bus") \
        and not CALIB_LOCK.locked()


async def stream():
    """按 STREAM_HZ 广播全部舵机状态和新日志。读不到的舵机攒 3 秒报一次，同一个错误 5 秒内只记一次。"""
    sent_n = LOG_N
    miss, miss_t0 = {}, time.monotonic()
    err_last, err_t = "", 0.0
    while True:
        t0 = time.monotonic()
        if stream_should_poll():
            # 校准期间不抢总线：一次 sync_read 遇上掉包能占几百毫秒，
            # 正好卡在关扭矩的窗口里，关节就垂下去了
            try:
                if imu_backend(IMU_SERVICE) == "bus":
                    st = await asyncio.to_thread(read_states_with_imu, PRESENT, IMU_SERVICE)
                else:
                    st = await asyncio.to_thread(BUS.states, PRESENT)
                for i, v in st.items():
                    if v is None:
                        miss[i] = miss.get(i, 0) + 1
                await broadcast(json.dumps({"type": "state", "t": time.time(), "states": st, "stats": BUS.stats}))
            except Exception as e:
                text = f"{type(e).__name__}: {e}"
                if text != err_last or t0 - err_t > 5:
                    log(f"刷新状态出错：{text}", "总线", "error", exc=True)
                    err_last, err_t = text, t0
        if t0 - miss_t0 >= 3:
            if miss:
                log("3 秒内读不到：" + " ".join(f"#{i}×{n}" for i, n in sorted(miss.items()))
                    + f"（每秒读 {STREAM_HZ} 次；累计超时 {BUS.stats.get('timeout')}、校验错 {BUS.stats.get('bad_checksum')}）", "总线", "warn")
            miss, miss_t0 = {}, t0
        if CLIENTS and LOG_N > sent_n:
            items = [e for e in LOG if e["n"] > sent_n]
            if items:
                sent_n = items[-1]["n"]
                await broadcast(json.dumps({"type": "logs", "items": items}))
        hz = FAKE_STREAM_HZ if is_fake() else STREAM_HZ
        await asyncio.sleep(max(0.0, 1.0 / hz - (time.monotonic() - t0)))


# 指令 → 日志分类；QUIET 是拖滑块、流式这种高频指令，不逐条记
OP_CAT = {"goal": "运动", "goals": "运动", "goals_stream": "运动", "stream_end": "运动", "goals_profile": "运动",
          "release": "运动", "goals_verify": "运动", "torque": "运动", "scan": "总线", "scan_all": "总线", "timing": "总线",
          "dump": "寄存器", "write_reg": "寄存器", "reboot": "寄存器", "set_id": "寄存器", "calibrate": "校准",
          "calib_mid_all": "校准", "calib_undo": "校准", "calib_plan": "校准", "sim_imu": "IMU", "set_dir": "方向", "save_pose": "姿态", "poses": "姿态",
          "delete_pose": "姿态", "log": "系统", "page_log": "页面"}
QUIET = {"goal", "goals", "goals_stream", "stream_end", "poses", "log", "release", "page_log"}


async def ws_endpoint(ws):
    await ws.accept()
    await ws.send_text(json.dumps({"type": "hello", "ids": IDS, "present": PRESENT, "names": JOINT_NAMES,
                                   "fake": is_fake(), "logs": LOG[-150:], "cats": LOG_CATS, "dirs": load_dirs(),
                                   "port": PORT, "ports": list_ports(),
                                   "regs": [[a, *feetech.REGISTERS[a]] for a in sorted(feetech.REGISTERS)],
                                   "version": VERSION, "imu_enabled": IMU_SERVICE is not None,
                                   "imu_protocol": getattr(IMU_SERVICE, "protocol", None),
                                   "sim_imu": sim_imu_mode()}))
    CLIENTS.add(ws)  # hello 必须先于后台广播，前端先收到功能配置
    try:
        while True:
            cmd = json.loads(await ws.receive_text())
            op = cmd.get("op")
            if op not in QUIET:
                log(f"收到 {json.dumps(cmd, ensure_ascii=False)[:300]}", OP_CAT.get(op, "系统"), "debug")
            try:
                reply = await asyncio.to_thread(handle, cmd)
            except Exception as e:
                msg = f"{op} 出错：{type(e).__name__}: {e}"
                log(msg, OP_CAT.get(op, "系统"), "error", exc=True)
                reply = {"type": "error", "msg": msg}
            if reply:
                await ws.send_text(json.dumps(reply))
            if op in ("sim_imu", "reconnect", "imu_protocol"):
                # 成功失败都广播：失败时页面下拉框要退回实际状态；别的标签页也跟着变
                await broadcast(json.dumps(imu_config_frame()))
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.discard(ws)


class ImuLogHandler(logging.Handler):
    """J-Link SDK/桥接器也写现有的分类日志，保留一条排查时间线。"""

    def emit(self, record):
        level = ("error" if record.levelno >= logging.ERROR else
                 "warn" if record.levelno >= logging.WARNING else
                 "info" if record.levelno >= logging.INFO else "debug")
        log(self.format(record), "IMU", level)


def imu_backend(service):
    """服务用的探针后端名（jlink / swd），拿不到就是 None；只认字符串，免得把怪对象塞进 JSON。"""
    backend = getattr(service, "backend", None)
    return backend if isinstance(backend, str) else None


def imu_error_frame(message, backend=None):
    return {"type": "imu", "status": "error", "mode": "live", "live": False,
            "connected": False, "quaternion_xyzw": None, "message": message,
            "backend": backend, "timestamp_ms": int(time.time() * 1000)}


async def imu_stream(service, startup_error=None):
    """独立读状态/广播任务：串口超时不会拖慢 IMU，异常也不影响舵机流。"""
    previous = None
    error_last, error_time = "", 0.0
    while True:
        try:
            frame = (imu_error_frame(startup_error, imu_backend(service))
                     if startup_error else service.get_status())
            state = (frame.get("mode"), frame.get("status"), frame.get("connected"))
            if state != previous:
                level = "error" if frame.get("status") == "error" else (
                    "info" if frame.get("live") else "warn")
                log(f"状态 {frame.get('mode')}/{frame.get('status')}：{frame.get('message', '')}", "IMU", level)
                previous = state
            if CLIENTS:
                await broadcast(json.dumps(frame))
        except Exception as exc:
            message = f"读取 IMU 状态失败：{type(exc).__name__}: {exc}"
            now = time.monotonic()
            if message != error_last or now - error_time >= 5:
                log(message, "IMU", "error", exc=True)
                error_last, error_time = message, now
            if CLIENTS:
                await broadcast(json.dumps(imu_error_frame(message, imu_backend(service))))
        await asyncio.sleep(0.05)


async def imu_stream_dynamic():
    """模拟模式：虚拟 IMU 可以在页面上随时接上 / 拔掉，推送任务跟着 IMU_SERVICE 换。"""
    current, task = None, None
    try:
        while True:
            svc = IMU_SERVICE
            if svc is not current:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                task = asyncio.create_task(imu_stream(svc)) if svc is not None else None
                current = svc
            await asyncio.sleep(0.2)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@contextlib.asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(stream())]
    service = IMU_SERVICE
    handler = None
    logger = logging.getLogger("imu_bridge")
    old_level, old_propagate = logger.level, logger.propagate
    # pyOCD 自己的日志很啰嗦，只收警告以上，跟桥接器同一条 IMU 时间线
    pyocd_logger = logging.getLogger("pyocd")
    pyocd_old = (pyocd_logger.level, pyocd_logger.propagate)
    pyocd_handler = None
    try:
        if service is not None:
            handler = ImuLogHandler()
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if imu_backend(service) == "swd":
                pyocd_handler = ImuLogHandler(level=logging.WARNING)
                pyocd_logger.addHandler(pyocd_handler)
                pyocd_logger.setLevel(logging.WARNING)
                pyocd_logger.propagate = False
            startup_error = None
            try:
                service.start()
                log("姿态读取已启动（演示数据）" if service.demo else
                    "SWD 姿态读取已启动（pyOCD）" if imu_backend(service) == "swd" else
                    "总线 IMU 已启用：ID 200 跟舵机在同一条 sync_read 里读（有网页连着才轮询）"
                    if imu_backend(service) == "bus" else "J-Link 姿态读取已启动", "IMU")
            except Exception as exc:
                startup_error = f"启动 IMU 失败：{type(exc).__name__}: {exc}"
                log(startup_error, "IMU", "error", exc=True)
            tasks.append(asyncio.create_task(imu_stream(service, startup_error)))
        else:
            tasks.append(asyncio.create_task(imu_stream_dynamic()))
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if service is not None:
            try:
                await asyncio.to_thread(service.stop)
                log("姿态读取已停止，探针连接已释放", "IMU")
            except Exception as exc:
                log(f"停止 IMU 失败：{type(exc).__name__}: {exc}", "IMU", "error", exc=True)
            finally:
                logger.removeHandler(handler)
                logger.setLevel(old_level)
                logger.propagate = old_propagate
                if pyocd_handler is not None:
                    pyocd_logger.removeHandler(pyocd_handler)
                    pyocd_logger.setLevel(pyocd_old[0])
                    pyocd_logger.propagate = pyocd_old[1]


app = Starlette(routes=[
    Route("/", index),
    Route("/imu_attitude.js", imu_attitude_file),
    Route("/api/info", info),
    Route("/api/poses", poses),
    Route("/api/logfile", logfile),
    Route("/model/{name}", model_file),
    WebSocketRoute("/ws", ws_endpoint),
], lifespan=lifespan)


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="串口，如 COM5 / /dev/ttyUSB0 / /dev/ttyS2")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--ids", default=DEFAULT_IDS, help="要管的 ID，如 20-24,30-34,10-14")
    ap.add_argument("--fake", action="store_true", help="不接舵机，界面演示")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8080)
    imu = ap.add_mutually_exclusive_group()
    imu.add_argument("--imu-jlink", type=Path, metavar="CONFIG.json",
                     help="用本机配置文件启用 J-Link IMU 读数，数据仍走当前 /ws")
    imu.add_argument("--imu-swd", nargs="?", const="auto", metavar="UID",
                     help="用 pyOCD 经 ST-Link / DAPLink 读 IMU（只插一个调试器时可省略 UID；J-Link 用 --imu-jlink）")
    imu.add_argument("--imu-bus", action="store_true",
                     help="从舵机总线读 IMU 小板 ID 200（默认飞特协议；HD-1910 舵机仍走飞特协议）")
    imu.add_argument("--imu-demo", action="store_true", help="合成 IMU 数据，不连接 J-Link")
    ap.add_argument("--imu-bus-protocol", choices=("dxl2", "feetech"), default="feetech",
                    help="IMU 总线协议；默认 feetech；dxl2 用于兼容卖家提供的 DXL2 固件")
    return ap.parse_args(argv)


def create_imu_service(args):
    # 不启用时不导入桥接器，也不需要 J-Link SDK / pyOCD。
    imu_swd = getattr(args, "imu_swd", None)
    imu_bus = getattr(args, "imu_bus", False)
    if not args.imu_jlink and not args.imu_demo and imu_swd is None and not imu_bus:
        return None
    if imu_bus:
        from imu_bus import BusImuService
        # 跟 stream() 实际读总线的频率一致：模拟 30 Hz、真串口 50 Hz（启动时没给串口也按模拟算）
        return BusImuService(poll_hz=FAKE_STREAM_HZ if args.fake or not args.port else STREAM_HZ,
                             protocol=getattr(args, "imu_bus_protocol", "feetech"))
    from imu_bridge import BridgeService, DEFAULT_FIRMWARE, validate_probe_settings
    if args.imu_demo:
        return BridgeService(demo=True)
    if imu_swd is not None:
        if not imu_swd.strip():
            raise ValueError("--imu-swd 后面的 UID 不能为空；只插一个调试器时直接写 --imu-swd")
        import importlib.util
        if importlib.util.find_spec("pyocd") is None:
            raise ValueError("--imu-swd 需要 pyOCD：pip install pyocd")
        if not DEFAULT_FIRMWARE.is_file():
            raise ValueError(f"IMU 固件文件不存在：{DEFAULT_FIRMWARE}")
        return BridgeService(backend="swd", probe_id=imu_swd.strip())
    path = args.imu_jlink.resolve()
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"IMU 配置读取失败 {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("IMU 配置必须是 JSON 对象")
    unknown = set(config) - {"dll", "probe_serial", "firmware", "hz", "resume"}
    if unknown:
        raise ValueError("未知 IMU 配置项：" + ", ".join(sorted(unknown)))
    if not isinstance(config.get("dll"), str) or not config["dll"].strip():
        raise ValueError("IMU 配置必须填写本机 JLink_x64.dll 路径 dll")
    serial = config.get("probe_serial")
    if isinstance(serial, bool) or not isinstance(serial, int) or serial <= 0:
        raise ValueError("IMU 配置必须填写自己的正整数 probe_serial，不自动选择探针")
    resume = config.get("resume", False)
    if not isinstance(resume, bool):
        raise ValueError("IMU 配置 resume 必须是 true 或 false")
    hz = config.get("hz", 20)
    if isinstance(hz, bool) or not isinstance(hz, (int, float)) or not math.isfinite(hz) or not 1 <= hz <= 20:
        raise ValueError("IMU 配置 hz 必须是 1 到 20 之间的有限数值")

    def config_path(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("IMU 文件路径必须是非空字符串")
        candidate = Path(value).expanduser()
        return (candidate if candidate.is_absolute() else path.parent / candidate).resolve()

    dll = config_path(config["dll"])
    dll, serial = validate_probe_settings(dll, serial)
    firmware = config_path(config["firmware"]) if "firmware" in config else DEFAULT_FIRMWARE
    if not firmware.is_file():
        raise ValueError(f"IMU 固件文件不存在：{firmware}")
    return BridgeService(dll_path=dll, serial=serial, firmware=firmware, hz=float(hz), resume=resume)


def main(argv=None):
    global BUS, IDS, PRESENT, BAUD, IMU_SERVICE
    a = parse_args(argv)
    try:
        IMU_SERVICE = create_imu_service(a)
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f"IMU 配置错误：{exc}") from exc
    IDS = parse_ids(a.ids)
    BAUD = a.baud
    log(f"舵机调试台 v{VERSION} 启动，日志写在 {log_path()}", "系统")
    if a.fake or not a.port:
        fallback_fake("没给 --port（或用了 --fake）" if not a.port else "--fake")
    elif not open_bus(a.port, a.baud)["ok"]:
        # 串口打不开（USB 没插、口号变了、被别的程序占着）也要把页面起起来，
        # 插好以后在页面顶栏选串口点「连接」就行，不用重启服务
        fallback_fake(f"串口 {a.port} 打不开")
    print(f"舵机调试台 v{VERSION} · 浏览器开 http://127.0.0.1:{a.http_port}  （局域网用本机 IP）", flush=True)
    uvicorn.run(app, host=a.host, port=a.http_port, log_level="warning")


if __name__ == "__main__":
    main()
