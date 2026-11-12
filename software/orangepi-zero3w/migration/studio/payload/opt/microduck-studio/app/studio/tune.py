"""总览下面「运行和调参」那一块、设置 › 这只鸭子 › 模型 的后端（调参方案 v2 T1–T3：全部只读）。

方案：scratchpad console/tuning-plan.md（v2 定稿）。三张卡：
    卡 1 运行状态   robotd 阶段、启动四步（startup.py）、控制、能不能走、硬件
    卡 2 调手感     走路模型自己的幅度 / 头腿平滑（文件 vs 主程序在用 vs 这一拍）、P/D/限力、手柄嘴 / 头幅度
    卡 3 模型       每个槽一行（只看）；设置页的模型卡片、滑轮模型、导出鸭子档案（给训练用）

**这一版不往鸭子写任何东西**：改动要等 root 小助手（方案 T4）。唯一的写是 Studio 自己 state 目录里的
duck-body.json（导出档案用的重量，用户在设置页填的）。

关键事实（方案 §0、§2）：
- 联合 Walk 的幅度和头腿平滑写在**模型自己目录的 deployment-execution.json**（fork control.rs:622-632），
  robotd.toml [policy] 的 action_scale 谁都不用；全局平滑只有起坐用（control.rs:722-733）。
- 「主程序在用」一律看 busStatus.loaded_models.walk.runtime_execution_profile（审查 T-R14）；
  active_action_scale / active_target_filters 只在策略开着时有，是「这一拍实际用的（× 电压补偿）」。
- 舵机参数文件 servo-test-parameters.json 没有 = V167 原版（上力 P = 走路合同的 kp_fw、D 0、限力 1000）。
  主程序读到的是 native.hd1910.servo_parameters；这个键不存在 = 原版板端固件的 robotd（不支持改，D6c）。
  **不用 state.safety.gain**：飞特后端根本不写它（审查 T-R4，feetech.rs:1825-1834）。

读文件全在线程里（io()，2 秒一次，按 mtime 缓存；走路合同 431 KB 只在变了时才解析），view() 只拼内存里的东西。
文件路径都相对 root（鸭子上是 /；测试、电脑上截图用一个假文件树：dev/duckfs.py）。
"""
import asyncio
import json
import math
import os
import re
import sys
import tempfile
import time
import tomllib
from collections import deque
from pathlib import Path

from .imu import mount_of
from .robotd import fresh_calib
from .startup import _hello_at, walk_unloaded
from .startup_status import startup_wait

ETC = "etc/robot/feetech-ft5"
BASE = "opt/robot/feetech-ft5-r5"
CONFIG, SAVED = f"{ETC}/robotd.toml", f"{ETC}/robotd.saved.toml"
SERVO_FILE = f"{ETC}/servo-test-parameters.json"
PAD_FILE = f"{ETC}/pad-settings.json"
PAD_PREV = f"{ETC}/pad-settings.json.previous"
PAD_LIVE = "run/padd/input-settings.json"      # padd 实际在用的（padd:padd 0644，fork padd/src/input_settings.rs）
PROFILE = f"{BASE}/management-profile.json"     # 随包模型的名字（builtin_models）
ROLLER = f"{BASE}/roller"
ROLLERS = (("swizzle", "滑轮 · 对称滑"), ("crouch", "滑轮 · 蹲下"), ("spin", "滑轮 · 原地转"), ("standup", "滑轮 · 倒地起身"))
BODY_FILE = "duck-body.json"
EXEC = "deployment-execution.json"
SLOTS = ("walk", "sitstand", "ground_pick", "kick_left", "kick_right", "roulade", "stand")
SLOT_CN = {"walk": "走路 / 保持", "sitstand": "坐下 / 站起", "ground_pick": "低头捡东西", "kick_left": "左脚踢",
           "kick_right": "右脚踢", "roulade": "前滚翻", "stand": "站立（独立）"}
SLOT_SKILL = {"sitstand": "sit_toggle", "ground_pick": "ground_pick", "kick_left": "kick_left", "kick_right": "kick_right",
              "roulade": "roulade"}
BUTTON_CN = {"a": "A", "b": "B", "x": "X", "y": "Y", "lb": "LB", "rb": "RB", "dpad_down": "十字下", "dpad_up": "十字上",
             "dpad_left": "十字左", "dpad_right": "十字右", "ls": "L3", "rs": "R3"}
# 随包联合 Walk（只有它有硬写的默认值，审查 T-R17：别的模型要看 .orig）
BUNDLED_WALK = "1cbf8730eadd75a5704332c9f3c10f60"
BUNDLED_WALK_DEFAULT = {"action_scale": 1.0, "head_lowpass": 0.5, "legs_lowpass": 0.7}
V167_SERVO = {"kd": 0, "torque_limit": 1000}    # 没参数文件：D 0、限力 1000，P 取走路合同（hd1910.rs:121-155, 257-267）
PAD_DEFAULT = {"mouth_percent": 100.0, "head_rad": 2.5}
MOUTH_WARN = 10                                  # 嘴巴最大开度 ≤ 10%：几乎张不开，标黄（D6e：改回按钮先不做）
SCALE_USE_CN = {"joint_uniform": "走路（联合模型自己的参数）", "walking": "走路", "hold": "原地保持", "held": "没在推理",
                "homing": "回站姿", "sit": "坐着", "rise": "站起来", "ground_pick": "低头捡东西", "roulade": "前滚翻",
                "kick_left": "左脚踢", "kick_right": "右脚踢", "stand": "站立网络", "walk": "走路"}
PHASE_CN = {"control": ("控制中", "ok"), "configuring_bus": ("在配置舵机总线", "warn"), "waiting_for_bus": ("等待总线设备响应", "warn"),
            "configuration_error": ("配置错了", "bad"), "configuring_uart": ("在配置串口", "warn"), "ready": ("调试模式", "warn"),
            "read_error": ("调试模式 · 读舵机出错", "bad")}
MAX_JSON = 2 << 20            # 普通 json / toml 最大 2 MB
MAX_CONTRACT = 8 << 20        # 合同 431 KB 左右，留够
IO_EVERY_S = 2.0
MISSED_WINDOW_S = 60.0
AGE = {"health": 8.0, "policies": 6.0, "state": 1.5}
BODY_KEYS = ("total_g", "trunk_g", "head_g", "leg_left_g", "leg_right_g")
BODY_MAX_G = 5000


class Refused(Exception):
    """这个操作现在不能做，原因直接给页面看。"""


def num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return float(x) if math.isfinite(x) else None
    except (OverflowError, ValueError):
        return None


def _fresh(sample, key, now):
    age = sample.age(now) if sample is not None else None
    return sample.value if age is not None and age <= AGE[key] and isinstance(sample.value, dict) else None


def rounds_of(checkpoint):
    """训练轮数：检查点 9599 → 9600 轮、9999 → 10000 轮、11000 → 11000 轮（凑整的规律跟随包模型的名字一样）。
    字符串（老模型的 manifest 写的是检查点文件路径，比如 …/model_9999.pt）取最后一个数字。"""
    n = None
    if isinstance(checkpoint, bool):
        return None
    if isinstance(checkpoint, int):
        n = checkpoint
    elif isinstance(checkpoint, float) and checkpoint.is_integer():
        n = int(checkpoint)
    elif isinstance(checkpoint, str):
        m = re.findall(r"(\d+)", os.path.basename(checkpoint))
        n = int(m[-1]) if m else None
    if n is None or n < 0:
        return None
    return n + 1 if (n + 1) % 100 == 0 else n


class FileCache:
    """按 (mtime_ns, size) 缓存解析结果。读不到 = {"missing": True}；坏了 = {"error": "原因"}。"""

    def __init__(self):
        self._c = {}

    def get(self, path, parse, limit=MAX_JSON):
        try:
            stt = os.stat(path)
        except FileNotFoundError:
            self._c.pop(path, None)
            return {"missing": True}
        except OSError as e:
            return {"error": f"读不了：{type(e).__name__}: {e}"}
        key = (stt.st_mtime_ns, stt.st_size)
        hit = self._c.get(path)
        if hit and hit[0] == key:
            return hit[1]
        if stt.st_size > limit:
            out = {"error": f"文件太大（{stt.st_size} 字节）"}
        else:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                out = {"value": parse(data), "mtime": stt.st_mtime}
            except (OSError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
                out = {"error": f"{type(e).__name__}: {e}"[:300]}
        self._c[path] = (key, out)
        return out


def _json(data):
    return json.loads(data.decode("utf-8"))


def _toml(data):
    return tomllib.loads(data.decode("utf-8"))


def _contract_summary(data):
    """走路 / 动作合同只留几个字段（整份 431 KB 不留在内存里）。"""
    c = json.loads(data.decode("utf-8"))
    acts = ((c.get("model") or {}).get("actuators") or []) if isinstance(c.get("model"), dict) else []
    kp = acts[0].get("kp_fw") if acts and isinstance(acts[0], dict) else None
    rp = c.get("required_policy_settings") if isinstance(c.get("required_policy_settings"), dict) else {}
    return {"task": c.get("task"), "kp_fw": kp, "task_adapter": c.get("task_adapter"),
            "action_filter": c.get("action_filter") if isinstance(c.get("action_filter"), dict) else None,
            "required": {k: rp.get(k) for k in ("action_scale", "head_lowpass", "legs_lowpass", "ground_pick_action_scale")},
            "runtime_action_scale": c.get("runtime_action_scale"), "robot_mode": c.get("robot_mode")}


def _val(entry):
    return entry.get("value") if isinstance(entry, dict) else None


def _err(entry):
    if not isinstance(entry, dict):
        return None
    return entry.get("error")


class Tune:
    def __init__(self, rd, log, state_dir=".", root="/", duck=None):
        self.rd, self.log, self.state_dir, self.duck = rd, log, state_dir, duck or {}
        self.root = Path(root)
        self.cache = FileCache()
        self.disk = {}                 # io() 读好的东西：整份换掉（view() 在事件循环里读，不加锁）
        self._missed = deque(maxlen=64)   # (monotonic, missed, ticks)：算「最近一分钟加了几拍」
        self._io_err_logged = set()

    # ---- 路径 ----

    def p(self, rel):
        return self.root / rel

    def abs_path(self, path):
        """robotd 报的绝对路径（/opt/robot/…）换到 root 下面。"""
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        return self.root / path.lstrip("/")

    # ---- 读文件（线程里） ----

    def io(self):
        c = self.cache
        disk = {}
        cfg = c.get(self.p(CONFIG), _toml)
        saved = c.get(self.p(SAVED), _toml)
        disk["config"], disk["saved"] = cfg, saved
        disk["servo_file"] = c.get(self.p(SERVO_FILE), _json)
        disk["pad_file"] = c.get(self.p(PAD_FILE), _json)
        disk["pad_prev"] = c.get(self.p(PAD_PREV), _json)
        disk["pad_live"] = c.get(self.p(PAD_LIVE), _json)
        prof = _val(c.get(self.p(PROFILE), _json)) or {}
        disk["builtin_names"] = {m.get("id"): m.get("name") for m in (prof.get("builtin_models") or [])
                                 if isinstance(m, dict) and isinstance(m.get("id"), str)}
        disk["body"] = c.get(Path(self.state_dir) / BODY_FILE, _json)
        # 要读的模型目录：toml 里各槽 + 技能的路径，加上主程序报的 loaded_models（两边不一定一样）
        paths = set()
        pol = (_val(cfg) or {}).get("policy") if isinstance(_val(cfg), dict) else None
        if isinstance(pol, dict):
            for s in SLOTS:
                if isinstance(pol.get(s), str):
                    paths.add(pol[s])
            for sk in pol.get("skill") or []:
                if isinstance(sk, dict) and isinstance(sk.get("path"), str):
                    paths.add(sk["path"])
        bus = self.rd.bus.value if isinstance(self.rd.bus.value, dict) else {}
        lm = bus.get("loaded_models") if isinstance(bus.get("loaded_models"), dict) else {}
        for v in lm.values():
            if isinstance(v, dict) and isinstance(v.get("path"), str):
                paths.add(v["path"])
        models = {}
        for path in sorted(paths):
            full = self.abs_path(path)
            if full is None or path == "none":
                continue
            d = full.parent
            models[path] = self._model_dir(d)
        disk["models"] = models
        rollers = []
        for name, label in ROLLERS:
            d = self.p(ROLLER) / name
            m = self._model_dir(d)
            rollers.append({"id": name, "label": label, **m})
        disk["rollers"] = rollers
        disk["at"] = time.monotonic()
        # 两份 toml 一不一致（板端脚本两份一起写；robot.loadPolicy 只写第一份）
        a, b = _val(cfg), _val(saved)
        if a is None or b is None:
            disk["tomls_same"] = None
        else:
            disk["tomls_same"] = a == b
        self.disk = disk
        # 健康检查里的超时拍数：记历史，算最近一分钟加了几拍
        h = self.rd.health.value if isinstance(self.rd.health.value, dict) else None
        loop = (h or {}).get("control_loop") if isinstance((h or {}).get("control_loop"), dict) else None
        if loop and isinstance(loop.get("missed"), int) and isinstance(loop.get("ticks"), int):
            if self._missed and (loop["ticks"] < self._missed[-1][2] or loop["missed"] < self._missed[-1][1]):
                self._missed.clear()          # robotd 重启过：计数从头来
            self._missed.append((time.monotonic(), loop["missed"], loop["ticks"]))
        for key, entry in (("config", cfg), ("servo_file", disk["servo_file"]), ("pad_file", disk["pad_file"])):
            e = _err(entry)
            if e and key not in self._io_err_logged:
                self._io_err_logged.add(key)
                self.log.warn(f"读不懂鸭子上的配置文件（{key}）", cat="调参", raw=e,
                              hint="调参卡上那一项会显示「读不懂」；文件被手改坏了的话，主程序也可能起不来")
            elif not e:
                self._io_err_logged.discard(key)

    def _model_dir(self, d):
        c = self.cache
        man = c.get(d / "manifest.json", _json)
        info = c.get(d / "model-info.json", _json)
        exe = c.get(d / EXEC, _json)
        con = c.get(d / "deployment-contract.json", _contract_summary, limit=MAX_CONTRACT)
        out = {"dir": d.name, "onnx": (d / "policy.onnx").is_file(), "orig": (d / (EXEC + ".orig")).is_file(),
               "manifest": _val(man), "manifest_error": _err(man), "info": _val(info), "exec": _val(exe),
               "exec_error": _err(exe), "exec_missing": bool(exe.get("missing")) if isinstance(exe, dict) else None,
               "contract": _val(con), "contract_error": _err(con)}
        if out["orig"]:
            out["orig_exec"] = _val(c.get(d / (EXEC + ".orig"), _json))
        return out

    async def loop(self):
        """app.py 的 lifespan 起：2 秒读一次文件（线程里，SD 卡一卡不挡卸力）。"""
        while True:
            try:
                await asyncio.to_thread(self.io)
            except Exception as e:  # noqa: BLE001
                self.log.error("调参卡读文件时出错", cat="调参", raw=f"{type(e).__name__}: {e}", exc=True,
                               hint="这是 Studio 的问题，导出诊断包发出来；不影响站和走")
                await asyncio.sleep(5.0)
            await asyncio.sleep(IO_EVERY_S)

    # ---- 推给页面 ----

    def view(self, now=None):
        now = time.monotonic() if now is None else now
        rd = self.rd
        bus = rd.bus.value if isinstance(rd.bus.value, dict) else {}
        native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
        hd = native.get("hd1910") if isinstance(native.get("hd1910"), dict) else {}
        health = _fresh(rd.health, "health", now)
        pol = _fresh(rd.policies, "policies", now)
        st = _fresh(rd.state, "state", now)
        disk = self.disk
        cfg = _val(disk.get("config")) if disk else None
        policy_cfg = cfg.get("policy") if isinstance(cfg, dict) and isinstance(cfg.get("policy"), dict) else {}
        fork = ("servo_parameters" in hd) if hd else None
        walk = self._walk(bus, policy_cfg, disk)
        out = {
            "t": "tune",
            "ready": bool(disk),
            "cap": self._cap(fork, bus),
            "run": self._run(now, bus, native, health, pol, st, walk, cfg),
            "feel": self._feel(bus, native, hd, fork, walk, policy_cfg, disk),
            "models": self._models(bus, pol, policy_cfg, cfg, disk),
        }
        return out

    def _cap(self, fork, bus):
        if fork is False:
            why = "这只鸭子的主程序不支持改（不是我们的 fork：没有舵机参数、执行配置这些接口）"
        else:
            why = "现在只看不改：等鸭子上装好 root 小助手，才能在这里改"
        return {"can_edit": False, "fork": fork, "why": why, "build": bus.get("build")}

    def _phase(self, bus):
        rd = self.rd
        if getattr(rd, "paused", None):
            return {"word": "在调舵机", "kind": "warn", "why": "主程序按计划停着（舵机页接管着总线）", "error": None}
        if rd.present is False:
            return {"word": "主程序没在跑", "kind": "bad", "why": "停掉 robotd 不会卸力，舵机可能还顶着", "error": None}
        if not bus:
            return {"word": "还没读到", "kind": None, "why": None, "error": None}
        word, kind = PHASE_CN.get(bus.get("phase"), (bus.get("phase") or "不知道", None))
        wait = startup_wait(bus)
        if wait:
            word, kind = wait["text"], "warn"
        if bus.get("mode") == "commissioning" and bus.get("phase") == "ready":
            word, kind = "调试模式", "warn"
        return {"word": word, "kind": kind, "mode": bus.get("mode"), "phase": bus.get("phase"),
                "why": None, "error": bus.get("error")}

    def _missed_per_min(self, now):
        if len(self._missed) < 2:
            return None
        last = self._missed[-1]
        first = next((x for x in self._missed if last[0] - x[0] <= MISSED_WINDOW_S), None)
        if first is None or first is last or last[0] - first[0] < 5:
            return None
        return round((last[1] - first[1]) * 60.0 / (last[0] - first[0]), 1)

    def _run(self, now, bus, native, health, pol, st, walk, cfg):
        rd = self.rd
        loop = (health or {}).get("control_loop") if isinstance((health or {}).get("control_loop"), dict) else {}
        guard = bus.get("home_start_guard") if isinstance(bus.get("home_start_guard"), dict) else None
        track = getattr(rd, "startup", None)
        startup = track.view(now) if track is not None and track.epoch is not None and track.epoch == _hello_at(rd) else None
        unloaded = walk_unloaded(rd, pol, bus)
        lm = bus.get("loaded_models") if isinstance(bus.get("loaded_models"), dict) else {}
        am = bus.get("active_model") if isinstance(bus.get("active_model"), dict) else None
        models = self.disk.get("models") or {}
        active_name = self._name_of(am.get("path"), models) if am else None
        reader = native.get("imu_reader") if isinstance(native.get("imu_reader"), dict) else {}
        fusion = ((st or {}).get("imu") or {}).get("fusion") if isinstance((st or {}).get("imu"), dict) else None
        trunk = (cfg or {}).get("trunk_imu") if isinstance((cfg or {}).get("trunk_imu"), dict) else {}
        hbus = (health or {}).get("bus") if isinstance((health or {}).get("bus"), dict) else {}
        mv = (st or {}).get("move") if isinstance((st or {}).get("move"), dict) else None
        worst = self._worst_servos(bus)
        hz = num(loop.get("achieved_hz"))
        can_walk = None
        if guard is not None:
            can_walk = bool(guard.get("ready")) and not unloaded
        scale_base = (walk.get("loaded") or {}).get("action_scale") if walk.get("joint") else num(bus.get("action_scale"))
        return {
            "phase": self._phase(bus),
            "startup": startup,
            "walk_unloaded": unloaded,
            "loop": {"target": num(loop.get("target_hz")), "hz": hz, "missed": loop.get("missed"),
                     "missed_per_min": self._missed_per_min(now), "last_tick_age_ms": loop.get("last_tick_age_ms")},
            "health": {"healthy": (health or {}).get("healthy"), "degraded": (health or {}).get("degraded"),
                       "reason": (health or {}).get("reason")} if health else None,
            "guard": {k: guard.get(k) for k in ("ready", "reason", "max_joint_error_deg", "joint_tolerance_deg",
                                                 "tilt_deg", "tilt_tolerance_deg")} if guard else None,
            "can_walk": can_walk,
            "refusal": bus.get("last_enable_refusal"),
            "net": {"policy": (st or {}).get("policy"), "scale_use": bus.get("scale_use"),
                    "scale_use_cn": SCALE_USE_CN.get(bus.get("scale_use"), bus.get("scale_use")),
                    "policy_enabled": bus.get("policy_enabled"), "homed": bus.get("homed"),
                    "active_model": active_name, "walk_model": walk.get("name"), "walk_loaded": isinstance(lm.get("walk"), dict)},
            "scale": {"base": scale_base, "volt_mult": num(bus.get("voltage_scale_mult")),
                      "actual": num(bus.get("active_action_scale")), "voltage_adapt": bus.get("voltage_adapt"),
                      "nominal_v": num(bus.get("nominal_voltage"))},
            "move": {"requested": mv.get("requested"), "applied": mv.get("applied"),
                     "limited_by": mv.get("limited_by") or []} if mv else None,
            "imu": {"source": native.get("imu_source"), "ready": native.get("imu_ready"),
                    "reader": {k: reader.get(k) for k in ("state", "samples", "errors", "dropped", "last_error")} if reader else None,
                    "age_ms": native.get("imu_age_ms"),
                    "fusion_mode": trunk.get("bno08x_fusion") if native.get("imu_source") == "bno08x" else None,
                    "fusion": {"verdict": fusion.get("verdict"), "theta_deg": num(fusion.get("theta_deg"))}
                    if isinstance(fusion, dict) else None},
            "bus": {"consecutive_errors": hbus.get("consecutive_errors"), "startup_failures": hbus.get("startup_failures"),
                    "worst": worst, "last_cycle_error": bus.get("last_cycle_error"), "read_recovery": bus.get("read_recovery"),
                    "clamps": native.get("command_clamps_total"), "stale_ids": native.get("stale_joint_ids") or []},
            "cpu_c": num((health or {}).get("cpu_temp_c")),
            "infer_note": "推理耗时：主程序不报，看超时拍数",
        }

    def _worst_servos(self, bus):
        from .servo import NAMES
        dc = bus.get("device_communication") if isinstance(bus.get("device_communication"), dict) else {}
        by = dc.get("by_id") if isinstance(dc.get("by_id"), dict) else {}
        rows = []
        for k, v in by.items():
            if not isinstance(v, dict):
                continue
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            t, m = v.get("timeout") or 0, v.get("missing") or 0
            if not isinstance(t, int) or not isinstance(m, int) or t + m == 0:
                continue
            rows.append({"id": i, "name": (NAMES.get(i) or (str(i),))[0], "timeout": t, "missing": m})
        rows.sort(key=lambda r: (-(r["timeout"] + r["missing"]), r["id"]))
        return rows[:3]

    # ---- 卡 2 ----

    def _walk(self, bus, policy_cfg, disk):
        """走路模型：toml 里配的那个目录、它的执行配置（文件）、主程序在用的（loaded_models.walk）。"""
        models = disk.get("models") or {}
        lm = bus.get("loaded_models") if isinstance(bus.get("loaded_models"), dict) else {}
        loaded = lm.get("walk") if isinstance(lm.get("walk"), dict) else None
        path = policy_cfg.get("walk") if isinstance(policy_cfg.get("walk"), str) else (loaded or {}).get("path")
        m = models.get(path) or {}
        file = m.get("exec") if isinstance(m.get("exec"), dict) else None
        running = loaded.get("runtime_execution_profile") if loaded and isinstance(loaded.get("runtime_execution_profile"), dict) else None
        joint = bool(file or running)
        keys = ("action_scale", "head_lowpass", "legs_lowpass")
        if joint:
            loaded_vals = {k: num(running.get(k)) for k in keys} if running else None
        else:
            # 不是联合模型：走路用全局 [policy]（主程序报的 resolved 值）
            loaded_vals = {k: num(bus.get(k)) for k in keys} if any(num(bus.get(k)) is not None for k in keys) else None
        vals = {k: {"file": num((file or {}).get(k)), "loaded": (loaded_vals or {}).get(k)} for k in keys}
        mid = m.get("dir") or (os.path.basename(os.path.dirname(path)) if isinstance(path, str) else None)
        default = None
        if mid == BUNDLED_WALK:
            default, dsrc = dict(BUNDLED_WALK_DEFAULT), "随包默认"
        elif isinstance(m.get("orig_exec"), dict):
            default, dsrc = {k: num(m["orig_exec"].get(k)) for k in keys}, "第一次改之前的（.orig）"
        else:
            dsrc = None
        loaded_path = (loaded or {}).get("path")
        return {
            "path": path, "id": mid, "name": self._name_of(path, models), "joint": joint,
            "values": vals, "file": file is not None, "file_error": m.get("exec_error"),
            "loaded": loaded_vals,
            "same": (all(vals[k]["file"] == vals[k]["loaded"] for k in keys) if file and running else None),
            "orig": bool(m.get("orig")), "default": default, "default_src": dsrc,
            "path_differs": bool(loaded_path and path and loaded_path != path),
            "loaded_path": loaded_path,
        }

    def _feel(self, bus, native, hd, fork, walk, policy_cfg, disk):
        active = None
        if bus.get("policy_enabled") is True and num(bus.get("active_action_scale")) is not None:
            tf = bus.get("active_target_filters") if isinstance(bus.get("active_target_filters"), dict) else {}
            active = {"action_scale": num(bus.get("active_action_scale")), "head_lowpass": num(tf.get("head_lowpass")),
                      "legs_lowpass": num(tf.get("legs_lowpass")), "use": SCALE_USE_CN.get(bus.get("scale_use"), bus.get("scale_use"))}
        glob = {"action_scale": num(bus.get("action_scale")), "head_lowpass": num(bus.get("head_lowpass")),
                "legs_lowpass": num(bus.get("legs_lowpass")),
                "in_file": {k: policy_cfg.get(k) for k in ("action_scale", "head_lowpass", "legs_lowpass") if k in policy_cfg},
                "note": "幅度谁都不用；平滑只有起坐用（起坐不露，方案 D6d）"}
        return {
            "walk": walk, "active": active, "global": glob,
            "voltage": {"adapt": bus.get("voltage_adapt"), "nominal_v": num(bus.get("nominal_voltage")),
                        "mult": num(bus.get("voltage_scale_mult"))},
            "servo": self._servo(hd, fork, walk, disk, native.get("gain_mode")),
            "pad": self._pad(disk),
            "tomls_same": disk.get("tomls_same") if disk else None,
        }

    def _servo(self, hd, fork, walk, disk, gain_mode=None):
        models = disk.get("models") or {}
        con = (models.get(walk.get("path")) or {}).get("contract") or {}
        kp_fw = num(con.get("kp_fw"))
        default = {"kp": int(kp_fw) if kp_fw is not None else 5, "kd": V167_SERVO["kd"], "torque_limit": V167_SERVO["torque_limit"]}
        fe = disk.get("servo_file") or {}
        file, ferr = None, None
        if fe.get("missing"):
            file = None
        elif fe.get("error"):
            ferr = fe["error"]
        else:
            v = fe.get("value")
            if (isinstance(v, dict) and set(v) == {"kp", "kd", "torque_limit"}
                    and all(type(x) is int for x in v.values())):
                file = dict(v)
            else:
                ferr = "格式不对：要正好 kp / kd / torque_limit 三个整数（主程序读到这样的文件会停在等总线）"
        loaded = hd.get("servo_parameters") if fork else None
        loaded = dict(loaded) if isinstance(loaded, dict) else None
        if fork:
            eff, src = (loaded, "参数文件") if loaded else (default, "没有参数文件：V167 原版（P 用走路合同的）")
        else:
            eff, src = default, "V167 原版（P 用走路合同的）"
        same = None
        if fork is not None:
            same = (file == loaded) if fork else None
        regs = hd.get("registers_before_arm") if isinstance(hd.get("registers_before_arm"), list) else []
        reg = None
        if regs:
            ram = _group([r.get("temporary_pdi") for r in regs if isinstance(r, dict)])
            eep = _group([r.get("eeprom_pdi") for r in regs if isinstance(r, dict)])
            reg = {"ram_before_arm": ram, "eeprom": eep, "n": len(regs)}
        warn = []
        if eff:
            if (eff.get("kp") or 0) > 10:
                warn.append("P 大于 10：比常用的硬，可能会抖")
            if (eff.get("torque_limit") or 1000) < 500:
                warn.append("限力小于 500：可能站不住")
            if (eff.get("kd") or 0) > 0:
                warn.append("D 大于 0：在鸭子上还没试过")
        return {"fork": fork, "file": file, "file_exists": not fe.get("missing", False), "file_error": ferr,
                "loaded": loaded, "effective": eff, "src": src, "default": default,
                "file_vs_loaded": same, "written_p": {"p": (eff or {}).get("kp"),
                                                      "src": "参数文件" if loaded else "走路合同（kp_fw）",
                                                      "applied": hd.get("temporary_gains_applied") if hd else None},
                "regs": reg, "regs_why": None if reg else "站起来（上过力）以后才有", "gain_mode": gain_mode,
                "warn": warn}

    def _pad(self, disk):
        def settings(entry):
            v = _val(entry)
            if isinstance(v, dict) and "settings" in v and isinstance(v["settings"], dict):
                v = v["settings"]                # padd 的回执：{"pid", "path", "settings": {...}}
            if not isinstance(v, dict):
                return None
            return {"mouth_percent": num(v.get("mouth_percent")), "head_rad": num(v.get("head_rad"))}
        fe = disk.get("pad_file") or {}
        file = settings(fe)
        live = settings(disk.get("pad_live") or {})
        prev = settings(disk.get("pad_prev") or {})
        cur = file if file else (dict(PAD_DEFAULT) if fe.get("missing") else None)
        mouth = (cur or {}).get("mouth_percent")
        return {"file": file, "file_missing": bool(fe.get("missing")), "file_error": _err(fe), "live": live,
                "previous": prev, "current": cur, "default": dict(PAD_DEFAULT),
                "mouth_warn": mouth is not None and mouth <= MOUTH_WARN,
                "same": (file == live) if file and live else None}

    # ---- 卡 3 / 模型页 ----

    def _name_of(self, path, models):
        if not isinstance(path, str):
            return None
        m = models.get(path) or {}
        info = m.get("info") if isinstance(m.get("info"), dict) else {}
        names = self.disk.get("builtin_names") or {}
        return info.get("name") or names.get(m.get("dir")) or None

    def _models(self, bus, pol, policy_cfg, cfg, disk):
        models = disk.get("models") or {}
        lm = bus.get("loaded_models") if isinstance(bus.get("loaded_models"), dict) else {}
        am = bus.get("active_model") if isinstance(bus.get("active_model"), dict) else {}
        pad = (cfg or {}).get("pad") if isinstance((cfg or {}).get("pad"), dict) else {}
        slots_pol = {s.get("slot"): s for s in (pol or {}).get("slots") or [] if isinstance(s, dict)}
        rows = []
        for slot in SLOTS:
            ps = slots_pol.get(slot) or {}
            path = ps.get("path") if ps else None
            if not path:
                path = policy_cfg.get(slot) if isinstance(policy_cfg.get(slot), str) else None
            if path == "none":
                path = None
            m = models.get(path) or {}
            man = m.get("manifest") if isinstance(m.get("manifest"), dict) else {}
            tr = man.get("training") if isinstance(man.get("training"), dict) else {}
            ld = lm.get(slot) if isinstance(lm.get(slot), dict) else None
            sha = (ld or {}).get("sha256") or man.get("onnx_sha256")
            ckpt = tr.get("checkpoint", man.get("checkpoint"))
            skill = SLOT_SKILL.get(slot)
            buttons = [BUTTON_CN.get(b, b) for b, v in pad.items() if skill and v == skill]
            exe = m.get("exec") if isinstance(m.get("exec"), dict) else None
            con = m.get("contract") if isinstance(m.get("contract"), dict) else {}
            filt = con.get("action_filter") or {}
            plain = filt.get("enabled") is False and (filt.get("head_alpha") == 1.0 and filt.get("legs_alpha") == 1.0)
            rows.append({
                "slot": slot, "label": SLOT_CN[slot], "path": path, "id": m.get("dir"),
                "name": self._name_of(path, models), "task": (ld or {}).get("task") or man.get("task") or con.get("task"),
                "rounds": rounds_of(ckpt), "checkpoint": ckpt if isinstance(ckpt, (int, float)) else None,
                "commit": tr.get("commit"), "repo": tr.get("repo"), "sha": sha, "sha12": sha[:12] if isinstance(sha, str) else None,
                "loaded": ld is not None, "active": bool(am and path and am.get("path") == path),
                "error": ps.get("error"), "buttons": buttons,
                "exec": {k: num(exe.get(k)) for k in ("action_scale", "head_lowpass", "legs_lowpass")} if exe else None,
                "orig": bool(m.get("orig")), "unfiltered": bool(plain) and not exe,
                "files_ok": bool(m.get("onnx")) if path else None, "manifest_error": m.get("manifest_error"),
                "kp_fw": num(con.get("kp_fw")),
            })
        rollers = []
        for r in disk.get("rollers") or []:
            man = r.get("manifest") if isinstance(r.get("manifest"), dict) else {}
            tr = man.get("training") if isinstance(man.get("training"), dict) else {}
            con = r.get("contract") if isinstance(r.get("contract"), dict) else {}
            req = con.get("required") or {}
            scale = con.get("runtime_action_scale") if r["id"] == "standup" else (
                req.get("ground_pick_action_scale") if r["id"] in ("crouch", "spin") else req.get("action_scale"))
            rollers.append({"id": r["id"], "label": r["label"], "installed": bool(r.get("onnx") and man),
                            "rounds": rounds_of(tr.get("checkpoint", man.get("checkpoint"))), "task": man.get("task"),
                            "sha12": (man.get("onnx_sha256") or "")[:12] or None,
                            "action_scale": num(scale), "head_lowpass": num(req.get("head_lowpass")),
                            "legs_lowpass": num(req.get("legs_lowpass"))})
        return {"slots": rows, "rollers": rollers, "skills": (pol or {}).get("skills") or [],
                "change_error": (pol or {}).get("change_error"), "policies_known": pol is not None}

    # ---- 导出鸭子档案 ----

    def body(self):
        v = _val((self.disk or {}).get("body") or {})
        if v is None:
            v = _val(self.cache.get(Path(self.state_dir) / BODY_FILE, _json))
        return v if isinstance(v, dict) else None

    def profile(self):
        """给训练用的鸭子档案（只读汇总，方案 §2.4）。读不到的写 null，不编。"""
        rd = self.rd
        bus = rd.bus.value if isinstance(rd.bus.value, dict) else {}
        native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
        hd = native.get("hd1910") if isinstance(native.get("hd1910"), dict) else {}
        calib = fresh_calib(rd)
        source, mount = mount_of(bus, calib)
        cal = (calib or {}).get("calibration") if isinstance((calib or {}).get("calibration"), dict) else (calib or {})
        joints = []
        for j in cal.get("joints") or []:
            if isinstance(j, dict):
                joints.append({k: j.get(k) for k in ("name", "id", "zero_raw", "direction", "min_rad", "max_rad", "calibrated")})
        v = self.view()
        servo = v["feel"]["servo"]
        walk = v["feel"]["walk"]
        wrow = next((r for r in v["models"]["slots"] if r["slot"] == "walk"), {})
        regs = hd.get("registers_before_arm") if isinstance(hd.get("registers_before_arm"), list) else []
        return {
            "schema": "microduck-studio-duck-profile/v1",
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "note": "Microduck Studio 导出的鸭子档案（给训练用）。读不到的写 null；重量是人填的。",
            "duck": {"label": self.duck.get("label"), "name_on_duck": (rd.sysinfo.value or {}).get("name") if isinstance(rd.sysinfo.value, dict) else None,
                     "build": bus.get("build")},
            "body": self.body(),
            "imu": {"chip": source, "mount_quat_wxyz": (mount or {}).get("quat"), "verified": (mount or {}).get("verified")},
            "joints": joints or None,
            "servo": {"effective": servo["effective"], "source": servo["src"], "file": servo["file"],
                      "registers_before_arm": [{k: r.get(k) for k in ("id", "name", "temporary_pdi", "eeprom_pdi", "mode")}
                                               for r in regs if isinstance(r, dict)] or None},
            "walk_model": {"id": walk.get("id"), "name": walk.get("name"), "task": wrow.get("task"), "checkpoint": wrow.get("checkpoint"),
                           "rounds": wrow.get("rounds"), "commit": wrow.get("commit"), "sha256": wrow.get("sha"),
                           "execution_loaded": walk.get("loaded"), "execution_file": {k: walk["values"][k]["file"] for k in walk["values"]}
                           if walk.get("file") else None},
            "policy_global": {k: v["feel"]["global"][k] for k in ("action_scale", "head_lowpass", "legs_lowpass")},
            "voltage": v["feel"]["voltage"],
        }

    def save_body(self, body, by=None):
        """设置页填的重量（g）和脚底材料。只写 Studio 自己的 state 目录，不碰鸭子的配置。"""
        if not isinstance(body, dict):
            raise Refused("没收到数据")
        out = {}
        for k in BODY_KEYS:
            x = body.get(k)
            if x in (None, ""):
                out[k] = None
                continue
            if isinstance(x, str):
                try:
                    x = float(x)
                except ValueError:
                    raise Refused(f"{k} 要填数字（克）") from None
            x = num(x)
            if x is None or not 0 < x <= BODY_MAX_G:
                raise Refused(f"{k} 要在 0–{BODY_MAX_G} 克之间")
            out[k] = round(x, 1)
        for k, n in (("foot", 40), ("note", 200)):
            s = body.get(k)
            if s in (None, ""):
                out[k] = None
                continue
            if not isinstance(s, str) or len(s) > n or any(ord(ch) < 32 for ch in s):
                raise Refused(f"{'脚底材料' if k == 'foot' else '备注'}最多 {n} 个字、不能有换行")
            out[k] = s.strip() or None
        out["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out["by"] = by
        path = Path(self.state_dir) / BODY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".duck-body-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.log.info("鸭子档案的重量记下了（只存在 Studio 里）", cat="调参",
                      raw=json.dumps({k: out[k] for k in BODY_KEYS + ("foot",)}, ensure_ascii=False), ctx={"by": by})
        self.disk = dict(self.disk, body={"value": out})
        return out

    async def handle(self, args, by=None):
        op = args.get("op")
        if op == "save_body":
            return await asyncio.to_thread(self.save_body, args.get("body"), by)
        if op == "body":
            return self.body()
        if op in ("tune_walk", "tune_servo", "tune_pad", "undo", "default"):
            raise Refused("现在只看不改：等鸭子上装好 root 小助手，才能在这里改")
        raise Refused(f"不认识的指令：{op}")

    def diag(self):
        """诊断包里的「调参快照」：三类文件读到的、主程序回读的。"""
        v = self.view()
        d = self.disk or {}
        return {"view": v, "files": {k: (d.get(k) or {}).get("error") or ("缺" if (d.get(k) or {}).get("missing") else "有")
                                     for k in ("config", "saved", "servo_file", "pad_file", "pad_live")},
                "tomls_same": d.get("tomls_same"), "root": str(self.root), "platform": sys.platform}


def _group(rows):
    """[[P, D, I], ...] → [{"pdi": [P, D, I], "n": 颗数}]（15 颗一样就一行）。"""
    out = {}
    for r in rows:
        if isinstance(r, list) and len(r) == 3:
            key = tuple(r)
            out[key] = out.get(key, 0) + 1
    return [{"pdi": list(k), "n": n} for k, n in sorted(out.items(), key=lambda kv: -kv[1])]
