"""把 robotd / configd 的原始数据汇总成推给页面的一个小对象（一律用数组，字段缺了就当不知道）。

姿势判定照 review-impl-plan I2 的表，先看 mode，再看 phase：
    absent         robotd 的 socket 不在 / 拒绝连接
    starting       phase 是 configuring_bus / waiting_for_bus / configuration_error（标定模式第一次读成功之前的启动报告也是；
                   旧 fork 永远停在这里，连续 30 秒以上页面改口「一直报启动中」：starting_secs）
    commissioning  mode == "commissioning" 的实时快照（ready / moving / read_error / stop_unconfirmed / power_pending：
                   主程序起来了；这时 robotd 只读，init/enable/do 都拒绝）
    sitting        robot.policies.sitting
    limp           native.torque_state_confirmed == False
    walking        policy_enabled，且 move.applied 不为 0
    policy         policy_enabled（站着、策略在跑）
    holding        扭矩开、homed、home_start_guard.ready、策略没开（init 之后保持 HOME；关节实际位置也到位了）
    homing         扭矩开、还没到 HOME
    unknown        数据不够

只出文案用的状态，不出按钮（按钮在 S1b）。
"""
import math
import time

from .estop import STARTING_LONG_S, STARTING_PHASES
from .startup import startup_todo
from .startup_status import imu_context_current, imu_diagnostic, servo_observations, startup_wait, sampled_joint_feedback

JOINT_NAMES = ["left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
               "neck_pitch", "head_pitch", "head_yaw", "head_roll", "mouth",
               "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle"]
JOINT_CN = {"left_hip_yaw": "左髋转向", "left_hip_roll": "左髋横滚", "left_hip_pitch": "左髋俯仰", "left_knee": "左膝",
            "left_ankle": "左踝", "neck_pitch": "脖子", "head_pitch": "头俯仰", "head_yaw": "头转向", "head_roll": "头横滚",
            "mouth": "嘴", "right_hip_yaw": "右髋转向", "right_hip_roll": "右髋横滚", "right_hip_pitch": "右髋俯仰",
            "right_knee": "右膝", "right_ankle": "右踝"}
STALE_S = 3.0
# 各项数据最多用多旧的（代码审查 #18：查询失败后不能一直沿用旧值判断「坐着 / 在走」、电量和温度）
MAX_AGE = {"health": 8.0, "policies": 6.0, "state": 1.5, "pad": 15.0}
CPU_WARN_C = 80          # 这块板待机就 78 °C，75 °C 提醒会常亮，久了就没人看了


def _fresh(sample, key, now):
    age = sample.age(now)
    return sample.value if age is not None and age <= MAX_AGE[key] else None


def _ms(age):
    return None if age is None else int(age * 1000)


def starting_secs(rd, bus, now=None):
    """主程序从这次连上起、连续报启动阶段多少秒（robotd.Robotd.starting_since，轮询里记）。不在启动阶段 / 不知道回 None。
    超过 STARTING_LONG_S 页面就不再说「通常 10 秒内完成」（10-20 真机：旧 fork 的标定模式永远报 configuring_bus）。"""
    since = getattr(rd, "starting_since", None)
    if since is None or not isinstance(bus, dict) or bus.get("phase") not in STARTING_PHASES:
        return None
    return max(0, int((time.monotonic() if now is None else now) - since))


STILL_STARTING = "主程序可能其实起来了、只是状态没更新（旧版主程序的毛病），也可能卡在启动：去日志页看主程序最后说了什么"


def posture(present, bus, policies, state):
    if present is False:
        return "absent"
    if not isinstance(bus, dict):
        return "unknown"
    mode, phase = bus.get("mode"), bus.get("phase")
    if phase in STARTING_PHASES:
        return "starting"
    if mode == "read_only":
        return "read_only"
    if mode == "commissioning":
        return "commissioning"
    if mode != "motion":
        return "unknown"
    if isinstance(policies, dict) and policies.get("sitting") is True:
        return "sitting"
    native = bus.get("native") if isinstance(bus.get("native"), dict) else {}
    confirmed = native.get("torque_state_confirmed")
    if confirmed is False:
        return "limp"
    if bus.get("policy_enabled") is True:
        applied = ((state or {}).get("move") or {}).get("applied") if isinstance(state, dict) else None
        if isinstance(applied, list) and any(abs(v) > 0.01 for v in applied if isinstance(v, (int, float))):
            return "walking"
        return "policy"
    if confirmed is True:
        if bus.get("homed") is not True:
            return "homing"
        # homed 只是「控制环认为到 HOME 了」，关节实际位置还会落后一点：主程序自己的 home_start_guard
        # 才同时看关节实际位置（≤ joint_tolerance_deg）和机身倾角，并且要连续几拍稳定。
        # robotd 没给这个字段（老版本）时，才退回只看 homed。
        guard = bus.get("home_start_guard")
        if isinstance(guard, dict) and isinstance(guard.get("ready"), bool):
            return "holding" if guard["ready"] else "homing"
        return "holding"
    return "unknown"


def tilt_deg(gravity):
    """跟 robotd home_start.rs 同一个算法：重力跟竖直向下（0,0,-1）的夹角。"""
    if not isinstance(gravity, (list, tuple)) or len(gravity) != 3:
        return None
    n = math.sqrt(sum(g * g for g in gravity))
    if n < 1e-6:
        return None
    c = max(-1.0, min(1.0, -gravity[2] / n))
    return math.degrees(math.acos(c))


def cpu_view(health, health_age, bus, present, servo=None, ob=None):
    """主板温度格（总览重排审查 O8、O5）。字段：
        c          °C：所有 thermal zone 里最热的那个（robotd health 的 cpu_temp_c，fork soc.rs:25-64）；读不到 None
        throttled  内核的温控在压频（cooling device 的 level > 0）→ 页面红字「降频了 x / y MHz」；None = 读不到，**不是「正常」**
        limited    level 是 0、但允许的频率比最高的低：被限频了，多半不是热的（fork lib.rs:3615-3651 的原意）
        khz / max_khz / level / max_level   原样给（khz 是「允许跑到多少」，不是瞬时频率）
        hot        ≥ 80 °C（带回差：降到 77 °C 以下才收，回差记在 onboard.Onboarding.cpu_hot；没有它就不带回差）
        src        robotd / studio（robotd 不报时 Studio 自己读 /sys/class/thermal：鸭子上才有，电脑上开发时没有就算了）
        why        读不到的原因（主程序没报 / 在调舵机 / 调试模式不报 / 等总线时不报 / 健康检查过期）
    robotd 只在运动模式的控制循环里每秒采一次（fork main.rs:117-120、4148-4154），调试模式、等总线时都不采。"""
    out = {"c": None, "throttled": None, "limited": None, "khz": None, "max_khz": None, "level": None, "max_level": None,
           "hot": False, "src": None, "why": None}
    h = health if isinstance(health, dict) else None
    c = (h or {}).get("cpu_temp_c")
    thr = (h or {}).get("cpu_throttle") if isinstance((h or {}).get("cpu_throttle"), dict) else None
    src = "robotd" if isinstance(c, (int, float)) else None
    board = getattr(ob, "board", None) if ob is not None else None
    if not isinstance(c, (int, float)) and isinstance(board, dict) and isinstance(board.get("c"), (int, float)):
        c, thr, src = board["c"], board.get("throttle"), "studio"
    if isinstance(c, (int, float)):
        out.update(c=round(float(c), 1), src=src)
        out["hot"] = bool(ob.cpu_hot) if ob is not None else c >= CPU_WARN_C
    else:
        if servo is not None and getattr(servo, "state", None) in ("open", "opening"):
            why = "在调舵机：主程序按计划停着，不报温度"
        elif present is False:
            why = "主程序没在跑"
        elif (bus or {}).get("mode") == "commissioning":
            why = "调试模式下主程序不报温度"
        elif (bus or {}).get("phase") in STARTING_PHASES:
            wait = startup_wait(bus)
            why = f"主程序{wait['text'] if wait else '还在启动'}，这时不报温度"
        elif h is None:
            why = f"健康检查 {health_age:.0f} 秒没更新" if health_age is not None else "还没读到健康检查"
        else:
            why = "主程序没报温度"
        out["why"] = why
    if isinstance(thr, dict):
        lv, mx, khz, mk = thr.get("level"), thr.get("max_level"), thr.get("khz"), thr.get("max_khz")
        out.update(level=lv, max_level=mx, khz=khz, max_khz=mk)
        nums = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lv, khz, mk))
        if nums:
            out["throttled"] = lv > 0
            out["limited"] = lv == 0 and mk > 0 and khz < mk
    elif out["c"] is not None:
        out["throttled"] = None          # 有温度、没降频数据：不知道，不写「正常」
    return out


def servo_session_view(servo):
    """舵机会话的几个字（提示条用）；没开回 None。unknown = 扭矩读不到的颗数（可能也在保持：审查 D3c）；
    restored = Studio 重启以后接着管的（审查 D1）。"""
    if servo is None or getattr(servo, "state", None) not in ("open", "opening"):
        return None
    holding = servo.holding() if hasattr(servo, "holding") else None
    return {"state": servo.state, "holding": holding, "latched": bool(getattr(servo, "latched", False)),
            "unknown": servo.unknown() if hasattr(servo, "unknown") else 0,
            "restored": bool(getattr(servo, "restored", False)), "closing": bool(getattr(servo, "closing", False)),
            "port": "演示（没接舵机）" if getattr(servo, "sim", False) else getattr(servo, "port", None)}


def servo_orphan_view(servo):
    """上次 Studio 退出时舵机会话开着、这次没接管上（审查 D1）：可能还有舵机保持着，卸力要先去舵机页接管。"""
    o = getattr(servo, "orphan", None) if servo is not None else None
    if not o or getattr(servo, "state", None) in ("open", "opening"):
        return None
    return {"why": o.get("why")}


def controller_connection(rd, now=None):
    """Distinguish the device's robotd socket from desktop TCP development data."""
    now = time.monotonic() if now is None else now
    address = getattr(rd, 'addr', '')
    source = ('device' if address == 'unix:/run/robotd.sock' else
              'development' if address.startswith('tcp:') else 'unknown')
    bus_age = rd.bus.age(now)
    connected = (source == 'device' and rd.present is True
                 and isinstance(rd.bus.value, dict)
                 and bus_age is not None and 0 <= bus_age < STALE_S
                 and not getattr(rd.bus, 'error', None)
                 and not getattr(rd, 'paused', None))
    return {'source': source, 'connected': connected}


def summarize(rd, duck, servo=None, now=None, ob=None, bat=None, net=None, commit=None, auth=None):
    """bat：电量估算（studio/battery_svc.BatteryService，1 Hz 循环里喂）；这里只读它的结果。没给就按 robotd 的直线（老样子）。"""
    now = time.monotonic() if now is None else now
    bus = rd.bus.value
    health, pol, st = _fresh(rd.health, "health", now), _fresh(rd.policies, "policies", now), _fresh(rd.state, "state", now)
    bus_age, st_age = rd.bus.age(now), rd.state.age(now)
    native = (bus or {}).get("native") if isinstance((bus or {}).get("native"), dict) else {}

    # 新鲜度：Studio 自己多久没拿到新数据（浏览器那边再加上传输的年龄）
    freshest = min([a for a in (bus_age, st_age) if a is not None], default=None)
    robotd_ok = rd.present is True and bus_age is not None and bus_age < STALE_S

    # 仪表带
    b = (health or {}).get("battery") or {}
    battery = {"pct": b.get("percent"), "volts": b.get("volts")}
    joints = native.get("joints") if isinstance(native.get("joints"), list) else []
    sampled = sampled_joint_feedback(bus, receipt_age_s=bus_age, bus_error=getattr(rd.bus, "error", None))
    if sampled is not None:
        joints = sampled
    fresh = [j for j in joints if isinstance(j, dict) and j.get("feedback_fresh") is True]
    motors = (health or {}).get("motors") or {}
    hottest = None
    if isinstance(motors.get("max_c"), (int, float)):
        hottest = {"name": JOINT_CN.get(motors.get("hottest"), motors.get("hottest")), "c": motors.get("max_c")}
    servos = {"online": len(fresh) if joints else None, "total": len(joints) or 15, "hottest": hottest,
              "stale": [JOINT_CN.get(j.get("name"), j.get("name")) for j in joints
                        if isinstance(j, dict) and j.get("feedback_fresh") is False]}
    if sampled is not None:
        # A whole-sample cache cannot prove that every servo went offline.
        if not fresh:
            servos["online"] = None
        sync = native.get("sync") or {}
        servos["read_error"] = bool((bus or {}).get("last_cycle_error") or sync.get("deadline_exceeded")
                                    or sync.get("missing_ids") or sync.get("bad_checksums"))
    observations = servo_observations(bus, present=rd.present, bus_age_s=bus_age,
                                      bus_error=getattr(rd.bus, "error", None))
    if observations is not None:
        rows = observations["rows"]
        temperatures = [r for r in rows if r["temperature_c"] is not None]
        hot = max(temperatures, key=lambda r: r["temperature_c"]) if temperatures else None
        servos = {"online": observations["online"], "total": len(rows) or 15,
                  "hottest": {"name": JOINT_CN.get(hot["name"], hot["name"]), "c": hot["temperature_c"]} if hot else None,
                  "stale": [JOINT_CN.get(r["name"], r["name"]) for r in rows if r["responding"] is False]}
    imu_h = (health or {}).get("imu") or {}
    if imu_h.get("ready") is False:
        imu_status = "missing"
    elif (imu_h.get("consecutive_stale_blocks") or 0) >= 25:
        imu_status = "frozen"
    elif (bus or {}).get("mode") == "read_only":
        imu_status = "unverified"  # Sensor readiness does not verify mounting/calibration.
    elif native.get("imu_mount_verified", (bus or {}).get("imu_mount_verified")) is False:
        imu_status = "unverified"      # 方向没确认：装歪了走路会往前冲（10-12 猛冲的根因）
    elif imu_h.get("ready") is True:
        imu_status = "ok"
    else:
        imu_status = None
    pad_v = _fresh(rd.pad, "pad", now)
    pads = (pad_v.get("pads") or []) if isinstance(pad_v, dict) else None
    pad = {"known": pads is not None, "connected": bool(pads and any(p.get("connected") for p in pads if isinstance(p, dict))),
           "name": next((p.get("name") for p in (pads or []) if isinstance(p, dict) and p.get("connected")), None),
           "driver": pad_v.get("driver") if isinstance(pad_v, dict) else None}
    pad["estop_ok"] = bool(pad["connected"] and pad["driver"] == "active" and (bus or {}).get("mode") == "motion")

    cpu = cpu_view(health, rd.health.age(now), bus, rd.present, servo, ob)
    loop = (health or {}).get("control_loop") or {}

    # 3D：关节弧度按 JOINT_NAMES 原样给（不乘方向），重力用 safety.gravity。
    # 旧的也照给（页面按 joints_age_ms 去色），姿势判定只用新鲜的 st
    st_any = rd.state.value if isinstance(rd.state.value, dict) else {}
    js = st_any.get("joints")
    grav = (st_any.get("safety") or {}).get("gravity")

    st_age_live = st_age
    state_t_ns = st_any.get("t_ns")
    hold = (bus or {}).get("hold") if isinstance((bus or {}).get("hold"), dict) else None
    if (bus or {}).get("mode") == "commissioning":
        # 标定模式没有 robot.state：IMU 读数从 busStatus.last_sample 拿（标定模式 10 Hz 轮询），
        # IMU 页的「现在」和站着校准就都有实时数据了（用户 10-21：标定模式也能站姿、站着校）
        ls = (bus or {}).get("last_sample") if isinstance((bus or {}).get("last_sample"), dict) else {}
        s_imu = ls.get("imu") if isinstance(ls.get("imu"), dict) else {}
        g = s_imu.get("gravity")
        if s_imu.get("valid") is True and isinstance(g, list) and len(g) == 3:
            grav = g
            st_age_live = bus_age
            state_t_ns = ls.get("at_us") * 1000 if isinstance(ls.get("at_us"), int) else None
    pose = posture(rd.present, bus, pol, st)
    est = rd.estop.status(present=rd.present is not False, bus=bus, bus_age_s=bus_age, now=now)
    if servo_orphan_view(servo) is not None and rd.present is not True:
        # 上次退出时会话开着、这次没接管上（审查 D1）：robotd 不在，总线上又没接管——卸力要先去舵机页接管
        est = {"state": "unavailable", "why": "上次 Studio 退出时可能还有舵机保持着：去舵机页接管后才能卸力"}
    if servo is not None and servo.state in ("open", "opening"):
        # 调舵机：robotd 按计划停着。卸力以总线回读为准，电量用 15 颗舵机电压的平均（官方同一个算法）
        pose = "servo_session"
        robotd_ok = True
        freshest = 0.0                  # 主程序轮询按计划暂停了：新鲜度看舵机总线（它 50 Hz 在读）
        est = servo.estop_status()
        snap = servo.snapshot()
        if snap.get("avg_volts"):
            battery = {"pct": snap.get("battery_pct"), "volts": snap.get("avg_volts")}
        servos = {"online": snap.get("online"), "total": snap.get("total"), "hottest": None, "stale": []}
    if bat is not None:
        battery = bat.view(now)         # 电压查表的估算（调舵机时它自己从 Studio 的总线读数取）：battery-plan.md v2
    starting_s = starting_secs(rd, bus, now) if pose == "starting" else None
    return {
        "t": "state",
        "robotd_ok": robotd_ok,
        "controller_connection": controller_connection(rd, now),
        "robotd_age_ms": _ms(freshest),
        "health_age_ms": _ms(rd.health.age(now)),
        "present": rd.present,
        "mode": (bus or {}).get("mode"),
        "phase": (bus or {}).get("phase"),
        "build": (bus or {}).get("build"),
        "posture": pose,
        "start_error": (bus or {}).get("error") if pose == "starting" else None,
        "start_wait": startup_wait(bus) if pose == "starting" else None,
        # 连续报启动阶段多少秒（这次连上算）、超没超过 30 秒：总览头图、顶栏、待办改口「一直报启动中」
        "starting_s": starting_s,
        "starting_long": starting_s is not None and starting_s >= STARTING_LONG_S,
        "battery": battery,
        "servos": servos,
        "imu": {"status": imu_status, "source": native.get("imu_source"),
                "configured_imu_profile": native.get("configured_imu_profile", (bus or {}).get("configured_imu_profile")),
                "context_current": imu_context_current(rd, now),
                "generation": getattr(getattr(rd, "hello", None), "at", None),
                "diagnostic": imu_diagnostic(rd, now)},
        "pad": pad,
        "cpu": cpu,
        "loop": {"hz": loop.get("achieved_hz"), "target": loop.get("target_hz"), "missed": loop.get("missed")},
        "joints": js if isinstance(js, list) and len(js) == 15 else None,
        "joints_age_ms": _ms(st_age_live),
        "state_t_ns": state_t_ns,   # robotd 这一帧的时间戳：IMU 页靠它去重（同一帧别当两个读数）
        "gravity": grav if isinstance(grav, list) and len(grav) == 3 else None,
        "tilt_deg": tilt_deg(grav),
        "guard": (bus or {}).get("home_start_guard"),
        "hold": hold,                  # 标定模式站姿使能：{stage: ramping|holding, max_error_deg, directions}
        "estop": est,
        # startup_todo（调参方案 T1）：「还在启动」换成第几步的原话、补「走路模型没加载」
        "todo": startup_todo(rd, todo(health, bus, servos, imu_status, cpu, loop, battery, pose, rd.health.age(now),
                     battery_shutdown=getattr(ob, "battery_shutdown", None) if ob is not None else None,
                     starting_s=starting_s), pol, bus),
        # 上手清单：后端 0.5 秒判一次（onboard.Onboarding.tick），这里只拿缓存（审查 O6：这里每秒跑 10 次 × 每个页面）
        "onboarding": ob.view() if ob is not None else None,
        # 写进主程序：准备情况 + 后台任务的进度（CommitService；没有这个后端时是 None）
        "commit": (commit.view(rd, servo) if commit is not None else None),
        # 舵机会话（每一页顶上的提示条用）：开着就带；离开舵机页、有舵机保持着时不交还，会话会一直开着（用户 10-18）
        "servo_session": servo_session_view(servo),
        # 交还了多久：主程序如果是手动停的，要在鸭子上起来，页面一直提示到主程序连上为止（app 的 servo_watch 连上后清掉：审查 D12）
        "servo_handback_s": (int(now - servo.handed_back_at) if servo is not None and servo.state == "closed"
                             and getattr(servo, "handed_back_at", None) is not None else None),
        "servo_helper": bool(getattr(servo, "helper_available", False)),   # root 小助手可用：交还提示「正在把主程序起回来…」（审查 D12）
        # 交还成功但没确认卸力 / 基线没应答的颗（F6）；交还后主程序没起来的原因和恢复办法（F7）
        "servo_handback_note": getattr(servo, "handback_note", None) if servo is not None else None,
        "servo_giveback_error": getattr(servo, "giveback_error", None) if servo is not None else None,
        "servo_giveback_hint": getattr(servo, "giveback_hint", None) if servo is not None else None,
        # 会话标记没写进去（F10）：Studio 重启后会不知道这里还开着
        "servo_marker_error": getattr(servo, "marker_error", None) if servo is not None else None,
        # 上次退出时会话开着、这次没接管上（审查 D1）；会话期间主程序被手动起来了（审查 D9，10 分钟内）
        "servo_orphan": servo_orphan_view(servo),
        "servo_conflict_s": (int(now - servo.conflict_at) if servo is not None and getattr(servo, "conflict_at", None) is not None
                             and servo.state == "closed" and now - servo.conflict_at < 600 else None),
        # 配网（studio/net.py）：net.json 不在 = known False，页面整块 WiFi 隐藏
        "net": net.view(now) if net is not None else {"known": False},
        "auth": {"has_password": auth.has_password() if auth is not None else None},
        "duck": duck,
        "name_on_duck": (rd.sysinfo.value or {}).get("name"),
    }


def todo(health, bus, servos, imu_status, cpu, loop, battery, pose, health_age=None, battery_shutdown=None, starting_s=None):
    """把 health 结论翻成人话，最要紧的在前。只放「要处理的事」，正常的不放。
    每条带 key：总览上手清单已经说了的（卡片头的「连上鸭子」、IMU 方向）页面按 key 去重，同一件事只说一次（审查 O15）。
    starting_s：连续报启动阶段多少秒；超过 30 秒「还在启动」那条改口「一直报启动中」，不再说「通常 10 秒内完成」。"""
    items = []
    # 电量：battery 是估算器的结果（有 mode）就用 battery.todo_items；电压很低 / 红线放最前面，调舵机时也出（审查 B-R6）。
    # 没接估算器（battery 只有 pct / volts）还按下面 robotd 直线的老写法
    bat_items = None
    if isinstance(battery, dict) and "mode" in battery:
        from .battery import todo_items as battery_todo
        bat_items = battery_todo(battery)
        items += [i for i in bat_items if i["level"] == "error"]
    if health is None and pose not in ("absent", "unknown", "servo_session"):   # 调舵机时主程序按计划停着，不算读不到
        # 健康检查过期或读不到：不能显示「没有要处理的事」，那是假的放心
        when = f"{health_age:.0f} 秒没更新" if health_age is not None else "一直没读到"
        items.append({"key": "health", "level": "warn", "msg": "读不到健康检查",
                      "hint": f"电量、温度、IMU 这些{when}；看日志里的 robotd 那几行"})
    if pose == "starting":
        raw = (bus or {}).get("error")
        wait = startup_wait(bus)
        if wait:
            items.append({"key": "starting", "level": "warn", "msg": wait["text"],
                          "hint": f"原文：{raw}" if raw else "去日志页看主程序最后说了什么"})
        elif starting_s is not None and starting_s >= STARTING_LONG_S:
            items.append({"key": "starting", "level": "warn", "msg": "主程序一直报「启动中」",
                          "hint": f"已经 {starting_s} 秒了。" + (f"原文：{raw}。去日志页看主程序最后说了什么" if raw
                                                                  else STILL_STARTING)})
        else:
            items.append({"key": "starting", "level": "warn", "msg": "鸭子主程序还在启动",
                          "hint": f"原文：{raw}" if raw else "通常 10 秒内完成"})
    if pose == "servo_session":
        items.append({"key": "servo_session", "level": "warn", "msg": "正在调舵机",
                      "hint": "主程序停着，手柄、遥控、站走都不能用；离开舵机页舵机照样保持，点「交还主程序」才交还"})
        if bat_items is not None:
            items += [i for i in bat_items if i["level"] != "error"]
        else:
            # 没接估算器：调舵机时也出电量待办（审查 D8），电压是 Studio 自己总线上 15 颗读数的平均（summarize 里换好了）。
            # 这时 robotd 停着，配置里开没开自动关机都不管用
            pct = battery.get("pct") if isinstance(battery, dict) else None
            if isinstance(pct, (int, float)) and pct < 15:
                items.append({"key": "battery", "level": "error", "msg": f"电量只剩 {pct:.0f}%",
                              "hint": "该充电了；robotd 停着，没电它不会自己坐下"})
            elif isinstance(pct, (int, float)) and pct < 30:
                items.append({"key": "battery", "level": "warn", "msg": f"电量 {pct:.0f}%",
                              "hint": "快没电了；robotd 停着，没电它不会自己坐下"})
        return items
    if pose == "absent":
        items.append({"key": "absent", "level": "error", "msg": "鸭子主程序没在跑", "hint": "停掉 robotd 不会卸力，舵机可能还顶着"})
    items += [i for i in bat_items or () if i["level"] != "error"]
    pct = battery.get("pct") if bat_items is None else None
    if isinstance(pct, (int, float)):
        if pct < 15:
            # 没电了会不会自己坐下关机，看 robotd 配置的 [safety] battery_empty_shutdown（onboard.py 在线程里读）：
            # 二号鸭真机配置是 false——没电时没有任何东西让它停。读不到就按「不会」说（宁可让人先停下）
            empty = ("到 6.6 V 它会自己坐下关机" if battery_shutdown is True
                     else "没电时它不会自己坐下，先停下、坐下再充")
            items.append({"key": "battery", "level": "error", "msg": f"电量只剩 {pct:.0f}%", "hint": f"该充电了；{empty}"})
        elif pct < 30:
            items.append({"key": "battery", "level": "warn", "msg": f"电量 {pct:.0f}%", "hint": "快没电了"})
    if imu_status == "frozen":
        items.append({"key": "imu_frozen", "level": "error", "msg": "IMU 读数冻住了", "hint": "姿态数据不更新，别走路；检查 IMU 的线"})
    elif imu_status == "missing":
        items.append({"key": "imu_missing", "level": "error", "msg": "读不到 IMU", "hint": "检查 IMU 模块的接线"})
    elif imu_status == "unverified":
        items.append({"key": "imu_unverified", "level": "warn", "msg": "IMU 的安装方向还没确认",
                      "hint": "装歪了走路会往前冲；先去「IMU」页校准（fork 上没确认连站都不让）"})
    if servos.get("stale"):
        items.append({"key": "servo_stale", "level": "warn", "msg": f"{'、'.join(servos['stale'][:3])} 读数没更新",
                      "hint": "多半是线松了或者没电"})
    khz, mk = cpu.get("khz"), cpu.get("max_khz")
    if cpu.get("throttled"):
        mhz = f"：{khz // 1000} / {mk // 1000} MHz" if isinstance(khz, int) and isinstance(mk, int) and mk else ""
        items.append({"key": "cpu", "level": "warn", "msg": f"主板过热降频了{mhz}", "hint": "控制会变慢；加散热片或小风扇"})
    elif cpu.get("hot") and isinstance(cpu.get("c"), (int, float)):
        # 用跟主板格同一个带回差的 hot（≥ 80 °C 亮、77 °C 以下才收：审查 O8），不然待机 78–80 °C 时会来回跳
        items.append({"key": "cpu", "level": "warn", "msg": f"主板 {cpu['c']:.0f} °C，有点热", "hint": "再热就会降频，控制会变慢；加散热片或小风扇"})
    elif cpu.get("limited") and isinstance(khz, int):
        items.append({"key": "cpu", "level": "warn", "msg": f"主板被限频到 {khz // 1000} MHz（不是热的）",
                      "hint": "有程序（或系统设置）把 CPU 最高频率压低了；控制可能变慢"})
    hz, target = loop.get("achieved_hz") or loop.get("hz"), loop.get("target_hz") or loop.get("target")
    if isinstance(hz, (int, float)) and isinstance(target, (int, float)) and target and hz < 0.95 * target:
        items.append({"key": "loop", "level": "warn", "msg": f"控制频率只有 {hz:.1f} Hz", "hint": f"目标 {target:.0f} Hz；看主板温度和负载"})
    return items
