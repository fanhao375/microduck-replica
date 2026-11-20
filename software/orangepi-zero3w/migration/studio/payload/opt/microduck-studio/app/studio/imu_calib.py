"""IMU 安装方向校准（站着做）：纯计算。不碰硬件、不碰网络，只用标准库 math。

页面采一串重力读数：静止站好 → 前倾 → 后倾 → 左倾 → 右倾，交给 solve()，回来安装方向、站姿补偿、
每一步合不合格、质量指标。写不写进鸭子、怎么写，不归这里管（见 imu-page-plan.md）。

## 约定（都核对过真机数据，见方案「事实」一节）
- 机身系 B：x 朝前（头那边）、y 朝左、z 朝上。robot.state.safety.gravity 就在这个系里，
  站直时约 (0, 0, -1)（tests/fixtures/now-20261018.jsonl 第 10 行：[0.044, 0.016, -0.999]）。
- 四元数一律 [w, x, y, z]（robot.state.imu.quat、verify-imu 的 mount_wxyz 都是这个顺序）。
- 姿态四元数 q（机身 → 世界）换重力：g = R(q)ᵀ·(0, 0, -1)。robotd 的 safety.gravity 就是这么来的（再取 3 点中值）。
- 安装方向 q_mount：芯片系 → 机身系，v_机身 = q_mount ⊗ v_芯片 ⊗ q_mount*；robotd 算机身姿态是
  q_机身 = q_芯片 ⊗ q_mount⁻¹（fork 固件 duck-control/src/imu.rs:137-151）。

## 采样格式
每个采样是一个 dict（也可以直接给 [gx, gy, gz]），三种给法任选一种：
    {"g": [gx, gy, gz]}       重力方向（只取方向，长度无所谓）——robot.state.safety.gravity
    {"acc": [ax, ay, az]}     加速度计读数：静止时读到的是向上的 1 g，取反才是重力
    {"quat": [w, x, y, z]}    姿态（机身 → 世界）
可以带 "t"（秒）、"step"（平铺列表时按它分步）。有 inf / nan 的读数直接丢掉。

## frame
- "body"（默认）：读数已经按鸭子上**存的**安装方向换算到机身系。运动模式的 robot.state、调试模式的
  busStatus.last_sample.imu（gravity、quaternion_wxyz，fork 固件 robotd/src/feetech_commissioning.rs:1236-1255）
  都是这样。算出来的是「修正」，新方向 = 修正 ∘ 旧方向，所以要给 old_mount（存的那个方向）。
- "sensor"：读数是芯片原始轴（现在 robotd 不给这种数据，留给以后直接读芯片用）。算出来的就是安装方向本身。

## 每一步怎么取读数（页面 web/imu.js 照同一套规则判「停住了，下一步」，两边参数见 RULES）
- 停住段：从这一步最后一个读数往回数，跟最后一个读数差 ≤ hold_band_deg 的连续读数；中间夹着的单个乱跳读数跳过
  （BNO085 偶尔一帧乱跳）。冲过头再回来停住，只算回来以后停住的那段。
- 端平基准：停住段之前，最后一段「离桌上静止 ≤ level_deg、自己晃 ≤ ref_spread_deg、连续 ≥ ref_min_n 个」的读数。
  人把鸭子提起来时手不可能正好水平，每一步都以它自己端平的那一下为零点，手偏几度不会被算成「斜着倾」。
  找不到就用桌上静止那一刻。
- 倾了多少、往哪倾：停住段的平均方向相对端平基准。「最少 / 最多」都按停住段判。

## 原理
1. 静止站好（桌上、手不碰）：重力反过来就是「上」。
2. 四步各自的倾斜方向 d = 水平分量(停住 − 端平)，它在水平面里的方位角减去这一步该有的方位（前 0°、左 90°、
   后 180°、右 −90°），就是这一步认为的「前」的朝向 c。
3. 前后两步的 c 应该相同（不同 → 两次往同一边倾了）；左右同理；前后那对和左右那对也应该相同（差 ~180° →
   有一对做反了；差十几度 → 斜着倾了，叫 cross）。四个 c 取圆平均当「前」，每步离它多远就是这一步「斜了多少」。
4. 「上」+「前」正交化成三根轴。
5. 站姿补偿：「上」不硬取竖直，而是官方 HOME 姿势下机身该有的重力 home_gravity（默认 (0,0,-1)，
   官方训练 HOME 机身水平，见方案）。站着读到的偏差——不管是芯片装歪还是腿让机身歪了——一起折进
   安装方向。校完以后，站好时的读数 = 官方 HOME 的读数。光靠重力分不开这两种原因，所以超过 5° 要人确认。
6. 前后、左右**同时**做反（鸭子转了个身）自检查不出来，只能跟旧方向比：朝向改了 90° 以上就要人确认。
"""
import math

DOWN = (0.0, 0.0, -1.0)
STEPS = ("still", "fwd", "back", "left", "right")
STEP_CN = {"still": "静止站好", "fwd": "前倾", "back": "后倾", "left": "左倾", "right": "右倾"}
TILTS = ("fwd", "back", "left", "right")
EXPECT_DIR = {"fwd": 0.0, "left": 90.0, "back": 180.0, "right": -90.0}   # 往这边倾时重力偏向哪（从 +x 逆时针，度）

# 判据。页面 web/imu.js 的 RULES 用同一套名字：前端略严（倾 ≥12°、≤30°、停 1 秒），前端放行的这里一定收。
RULES = {
    "still_min_n": 15,              # 静止至少这么多个读数（10 Hz 下 1.5 秒；页面采 3 秒）
    "still_max_spread_deg": 1.5,    # 静止时读数最多晃这么多（按 90% 分位：一两帧乱跳不算，一直在晃才算）
    "level_deg": 5.0,               # 端平：离桌上静止这么多以内
    "ref_min_n": 5,                 # 端平那一下至少这么多个连续读数（0.5 秒）
    "ref_spread_deg": 1.5,          # 端平时自己最多晃这么多
    "hold_band_deg": 1.8,           # 停住段：跟这一步最后一个读数差这么多以内
    "hold_min_n": 5,                # 停住段至少这么多个读数（页面要求停满 1 秒、≥8 个）
    "tilt_min_deg": 10.0,           # 停住段相对端平至少倾这么多：倾得少，手抖占比大，「前」定不准
    "tilt_max_deg": 35.0,           # 最多倾这么多：再多脚会滑、读数也不稳
    "pair_max_deg": 60.0,           # 前倾、后倾认为的「前」差这么多以上：两次往同一边倾了（左右同理）
    "skew_max_deg": 20.0,           # 每一步离大家公认的方向最多斜这么多
    "cross_max_deg": 12.0,          # 前后那对和左右那对最多差这么多（差 Δ，朝向大约就错 Δ/2）
    "good_skew_deg": 10.0,          # 每步斜 ≤10°、两对差 ≤6° 评「好」
    "good_cross_deg": 6.0,
    "big_change_deg": 8.0,          # 跟旧方向比改了这么多：提醒「模块可能装歪了 / 换过装法」
    "stand_confirm_deg": 5.0,       # 站姿补偿超过这么多要人确认（robotd 自己验方向时直立只放 5°）
    "flip_deg": 90.0,               # 朝向改了这么多：多半是前后、左右都做反了（鸭子转了个身），要人确认
}


# ---------------- 向量 ----------------

def _v(x):
    return (float(x[0]), float(x[1]), float(x[2]))


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    """单位向量；零向量、有 inf / nan 的回 None。"""
    n = norm(a)
    if not (math.isfinite(n) and n > 1e-9):
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def angle_deg(a, b):
    """两个向量的夹角（度），任意一个算不出方向就回 None。"""
    ua, ub = unit(a), unit(b)
    if ua is None or ub is None:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(ua, ub)))))


def horiz(v, up):
    """v 去掉沿 up 的分量（up 是单位向量）。"""
    return sub(v, scale(up, dot(v, up)))


def signed_angle_deg(a, b, axis):
    """绕 axis 从 a 转到 b 的有向角（度，右手定则为正）。a、b 先投到垂直 axis 的平面上。"""
    ha, hb = unit(horiz(a, axis)), unit(horiz(b, axis))
    if ha is None or hb is None:
        return None
    return math.degrees(math.atan2(dot(cross(ha, hb), axis), dot(ha, hb)))


def wrap180(d):
    return (d + 180.0) % 360.0 - 180.0


def circ_mean_deg(ds):
    s = sum(math.sin(math.radians(d)) for d in ds)
    c = sum(math.cos(math.radians(d)) for d in ds)
    if abs(s) < 1e-9 and abs(c) < 1e-9:
        return None
    return math.degrees(math.atan2(s, c))


# ---------------- 矩阵（行优先，3×3 嵌套元组） ----------------

def mat_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def mat_vec(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def mat_t(m):
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))


# ---------------- 四元数 [w, x, y, z] ----------------

def q_normalize(q):
    if len(q) != 4 or not all(math.isfinite(float(c)) for c in q):
        raise ValueError("四元数要 4 个有限的数")
    n = math.sqrt(sum(float(c) * float(c) for c in q))
    if not n > 1e-12:
        raise ValueError("四元数长度是 0")
    q = tuple(float(c) / n for c in q)
    return q if q[0] >= 0 else tuple(-c for c in q)     # 统一成 w ≥ 0（q 和 −q 是同一个转动）


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def q_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def q_rotate(q, v):
    return mat_vec(mat_from_q(q), v)


def mat_from_q(q):
    w, x, y, z = q_normalize(q)
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def q_from_mat(m):
    """旋转矩阵 → 四元数（Shepperd，数值稳）。"""
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = ((m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s)
    return q_normalize(q)


def q_angle_deg(q):
    """这个转动转了多少度（0–180）。"""
    w = abs(q_normalize(q)[0])
    return math.degrees(2 * math.acos(min(1.0, w)))


def q_between(a, b):
    """把单位向量 a 转到 b 的最短转动。"""
    a, b = unit(a), unit(b)
    c = dot(a, b)
    if c < -0.999999:
        # 正好反向：随便找一根跟 a 垂直的轴转 180°
        axis = unit(cross(a, (1.0, 0.0, 0.0))) or unit(cross(a, (0.0, 1.0, 0.0)))
        return (0.0, axis[0], axis[1], axis[2])
    x = cross(a, b)
    return q_normalize((1.0 + c, x[0], x[1], x[2]))


def gravity_from_quat(q):
    """姿态（机身 → 世界）→ 机身系里的重力方向，跟 robotd 的 safety.gravity 同一个算法。"""
    r = mat_from_q(q)
    return (-r[2][0], -r[2][1], -r[2][2])


def compose_mount(correction, old_mount):
    """新安装方向 = 修正 ∘ 旧方向（都是 芯片 → 机身 的约定）：
    v_新机身 = R修正 · v_旧机身 = R修正 · R旧 · v_芯片。"""
    return q_normalize(q_mul(correction, old_mount))


# ---------------- 标准装法（芯片轴跟机身轴对齐的 24 种） ----------------

_AXIS_CN = {(1, 0): "前", (1, 1): "左", (1, 2): "上", (-1, 0): "后", (-1, 1): "右", (-1, 2): "下"}


def _standard_mounts():
    out = []
    perms = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
    for p in perms:
        for sx in (1, -1):
            for sy in (1, -1):
                for sz in (1, -1):
                    m = [[0.0] * 3 for _ in range(3)]
                    for col, (row, s) in enumerate(zip(p, (sx, sy, sz))):
                        m[row][col] = float(s)      # 芯片第 col 根轴 → 机身第 row 根轴
                    m = tuple(tuple(r) for r in m)
                    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
                    if det > 0:
                        out.append(m)
    return out


STANDARD_MOUNTS = _standard_mounts()


def describe_mount(q):
    """安装方向说成人话：芯片的 x / y / z 各朝机身哪边（取最接近的标准装法），再给偏了多少度。"""
    best, best_deg = None, None
    for m in STANDARD_MOUNTS:
        d = q_angle_deg(q_mul(q_conj(q_from_mat(m)), q))
        if best_deg is None or d < best_deg:
            best, best_deg = m, d
    words = []
    for col, name in enumerate("xyz"):
        row = next(r for r in range(3) if best[r][col] != 0)
        words.append(f"芯片 {name} 朝{_AXIS_CN[(int(best[row][col]), row)]}")
    return {"text": "，".join(words), "off_deg": round(best_deg, 2), "quat": list(q_from_mat(best))}


# ---------------- 采样 ----------------

def _finite3(x):
    if not isinstance(x, (list, tuple)) or len(x) != 3:
        return None
    try:
        v = _v(x)
    except (TypeError, ValueError):
        return None
    return v if all(math.isfinite(c) for c in v) else None


def sample_gravity(s):
    """一个采样 → 机身（或芯片）系里的重力单位向量；认不出、有 inf / nan 就回 None（这个读数丢掉）。"""
    try:
        if isinstance(s, (list, tuple)):
            v = _finite3(s)
            return unit(v) if v else None
        if not isinstance(s, dict):
            return None
        if s.get("g") is not None:
            v = _finite3(s["g"])
            return unit(v) if v else None
        if s.get("acc") is not None:
            v = _finite3(s["acc"])
            a = unit(v) if v else None
            return None if a is None else scale(a, -1.0)
        if s.get("quat") is not None:
            return gravity_from_quat(s["quat"])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return None


def sum_vec(vs):
    s = (0.0, 0.0, 0.0)
    for v in vs:
        s = add(s, v)
    return s


def mean_dir(vs):
    return unit(sum_vec(vs)) if vs else None


def spread_deg(vs, m):
    return max((angle_deg(v, m) or 0.0 for v in vs), default=0.0)


def spread_p90_deg(vs, m):
    """离 m 的角度的 90% 分位（排序后第 ⌊0.9·(n−1)⌋ 个）：静止那一步用它，个别乱跳的读数不算晃。页面 spreadP90 同一个算法。"""
    ds = sorted(angle_deg(v, m) or 0.0 for v in vs)
    return ds[int(0.9 * (len(ds) - 1))] if ds else 0.0


def trimmed_mean_dir(vs):
    """先平均，去掉离平均最远的那 10%（超过 90% 分位的），再平均：个别乱跳的读数拉不偏「上」。页面 trimmedMeanDir 同一个算法。"""
    m = mean_dir(vs)
    if m is None:
        return None
    lim = spread_p90_deg(vs, m)
    keep = [v for v in vs if (angle_deg(v, m) or 0.0) <= lim + 1e-9]
    return mean_dir(keep) or m


def group_steps(samples):
    """{"still": [...], ...} 原样用；一个平铺的列表就按每个采样的 "step" 分组。"""
    if isinstance(samples, dict):
        return {k: list(samples.get(k) or []) for k in STEPS}
    out = {k: [] for k in STEPS}
    for s in samples or []:
        if isinstance(s, dict) and s.get("step") in out:
            out[s["step"]].append(s)
    return out


def tilt_of(g0, g):
    """相对 g0 倾了多少度，以及往哪边倾（在 g0 定的水平面里，从 +x 量起的方位角，度）。
    页面上的倾角圈用同一个算法（web/imu.js 的 tiltOf）。"""
    t = angle_deg(g0, g)
    up = scale(g0, -1.0)
    d = signed_angle_deg((1.0, 0.0, 0.0), sub(g, g0), up) if unit(horiz(sub(g, g0), up)) else None
    return t, d


# ---------------- 停住段、端平基准（页面 web/imu.js 的 findHold / findRef 照这里一句一句写） ----------------

def _run_back(gs, end, band):
    """从 gs[end] 往回数：跟 gs[end] 差 ≤ band 的连续读数；中间夹着的单个乱跳读数跳过。回 (下标列表, 跳过几个)。"""
    last = gs[end]
    idx, skipped, j = [end], 0, end - 1
    while j >= 0:
        if angle_deg(gs[j], last) <= band:
            idx.append(j)
            j -= 1
        elif j - 1 >= 0 and angle_deg(gs[j - 1], last) <= band:
            skipped += 1                  # 单个乱跳：跳过它，接着往回数
            j -= 1
        else:
            break
    return sorted(idx), skipped


def find_hold(gs, rules=RULES):
    """停住段：从最后一个读数往回数（最后一个本身乱跳的话，从倒数第二个数），取长的那段。"""
    if not gs:
        return [], 0
    best = _run_back(gs, len(gs) - 1, rules["hold_band_deg"])
    if len(gs) >= 2:
        alt = _run_back(gs, len(gs) - 2, rules["hold_band_deg"])
        if len(alt[0]) > len(best[0]) + 1:
            best = alt
    return best


def find_ref(gs, g0, before, rules=RULES):
    """端平基准：gs[:before] 里最后一段「离 g0 ≤ level_deg、自己晃 ≤ ref_spread_deg、连续 ≥ ref_min_n 个」的读数，
    回平均方向；找不到回 None。"""
    n = rules["ref_min_n"]
    for end in range(before, n - 1, -1):
        win = gs[end - n:end]
        if all((angle_deg(g0, v) or 0.0) <= rules["level_deg"] for v in win):
            m = mean_dir(win)
            if m is not None and spread_deg(win, m) <= rules["ref_spread_deg"]:
                return m
    return None


# ---------------- 每一步 ----------------

def _r(x, nd=2):
    return None if x is None else round(x, nd)


def _gs(samples):
    return [g for g in (sample_gravity(s) for s in samples) if g is not None]


def check_still(samples, rules=RULES):
    gs = _gs(samples)
    out = {"step": "still", "name": STEP_CN["still"], "n": len(gs), "dropped": len(samples) - len(gs), "ok": False}
    if len(gs) < rules["still_min_n"]:
        out["msg"] = f"静止的读数只有 {len(gs)} 个（至少 {rules['still_min_n']} 个）：数据断了，或者采得太短"
        return out, None
    g0 = trimmed_mean_dir(gs)
    if g0 is None:
        out["msg"] = "静止的读数加起来是 0，算不出方向"
        return out, None
    spread = spread_p90_deg(gs, g0)
    out["spread_deg"] = _r(spread)
    out["max_dev_deg"] = _r(spread_deg(gs, g0))
    if spread > rules["still_max_spread_deg"]:
        out["msg"] = f"静止时读数晃了 {spread:.1f}°（最多 {rules['still_max_spread_deg']}°）：扶稳、别碰桌子，再来"
        return out, None
    out.update(ok=True, msg="稳")
    return out, g0


def check_tilt(step, samples, g0, rules=RULES):
    """回 (这一步的说明, 停住段的平均重力, 端平基准)。"""
    gs = _gs(samples)
    name = STEP_CN[step]
    out = {"step": step, "name": name, "n": len(gs), "dropped": len(samples) - len(gs), "ok": False}
    if not gs:
        out["msg"] = f"「{name}」没有读数"
        return out, None, None
    idx, skipped = find_hold(gs, rules)
    hold = [gs[i] for i in idx]
    ref = find_ref(gs, g0, idx[0], rules)
    out["ref"] = "端平那一下" if ref is not None else "桌上静止"
    ref = ref or g0
    out["ref_offset_deg"] = _r(angle_deg(g0, ref))
    g = mean_dir(hold)
    tilt = angle_deg(ref, g) if g is not None else None
    tmax = max(angle_deg(ref, v) for v in hold)       # 「最多」按停住段里倾得最多的那个读数判，不按平均
    out.update(n_used=len(hold), skipped=skipped, tilt_deg=_r(tilt), max_tilt_deg=_r(tmax),
               spread_deg=_r(spread_deg(hold, g) if g else None))
    if len(hold) < rules["hold_min_n"] or g is None:
        out["msg"] = f"「{name}」倾到位以后没停住（停住的读数只有 {len(hold)} 个）：倾到位停 1 秒"
        return out, None, None
    if tilt < rules["tilt_min_deg"]:
        out["msg"] = f"「{name}」只倾了 {tilt:.1f}°，至少要 {rules['tilt_min_deg']:.0f}°"
        return out, None, None
    if tmax > rules["tilt_max_deg"]:
        out["msg"] = f"「{name}」倾了 {tmax:.0f}°，太多了（最多 {rules['tilt_max_deg']:.0f}°）：脚会滑，读数也不稳"
        return out, None, None
    out.update(ok=True, msg=f"倾了 {tilt:.0f}°")
    return out, g, ref


# ---------------- 主函数 ----------------

def solve(samples, frame="body", old_mount=None, home_gravity=DOWN, rules=None, mode="full", old_verified=True,
          old_source=None):
    """算安装方向。samples 见模块说明；回一个 dict（全是 JSON 能直接发的类型）。

    mode="full"：五步（站着精校）。mode="coarse"：只要「静止」和「前倾」两步（「后倾」有就一起用），
    给方向一次都没确认过、站不起来的鸭子用：软着、双手托起来做，算出来交给 robotd 的 verify-imu 再验一遍。
    粗校没有左右两步，自检少，站姿补偿也不准（托着不是站着），所以之后还要站着精校；结果里要人对照模块
    实际装法勾一下（方案 S1）。
    old_verified=False：旧方向只是默认值（没验过），不拿它判断「改得太多 / 转了个身」。
    old_source="coarse"：旧方向是上次粗校写的，「转了个身」的提示改成「上次粗校可能做反了」。

    回的字段：
        ok         True / False
        reason     不 ok 时一句人话（第一个出问题的地方）
        redo       不 ok 时要补做哪几步（页面照这个让人只补那几步，不用全部重来）
        steps      每一步：{step, name, ok, msg, n, n_used, tilt_deg, spread_deg, skew_deg, ref, ...}
        correction [w,x,y,z]：「现在的读数系」→ 机身系（frame="sensor" 时就是安装方向）
        mount      [w,x,y,z]：新的安装方向（frame="body" 时要给 old_mount 才有）
        parts      修正拆开说（frame="body" 才有前四项）：pitch_deg（+ = 站着时读数比 HOME 前倾）、
                   roll_deg（+ = 左倾）、yaw_deg（+ = 真正的前方在现在读数的「前」的左边，从上往下看逆时针），
                   stand_tilt_deg（站着时读数离 HOME 多少度），total_deg（修正一共转了多少度）
        quality    fb_pair_deg、lr_pair_deg、cross_deg、max_skew_deg（越小越好）、grade（good / ok / coarse）、yaw_uncert_deg
        standard   新方向最接近哪种标准装法、偏了多少度
        notes      要知道的事；confirm：要人确认才能写的事（页面每条要打勾）
    """
    rules = dict(RULES, **(rules or {}))
    if frame not in ("body", "sensor"):
        raise ValueError("frame 只能是 body 或 sensor")
    if mode not in ("full", "coarse"):
        raise ValueError("mode 只能是 full 或 coarse")
    hg = unit(_v(home_gravity))
    if hg is None:
        raise ValueError("home_gravity 是零向量")
    steps = group_steps(samples)
    res = {"ok": False, "reason": None, "redo": [], "steps": {}, "frame": frame, "mode": mode, "rules": rules,
           "notes": [], "confirm": []}
    need = TILTS if mode == "full" else ("fwd",)
    optional = () if mode == "full" else ("back",)

    st, g0 = check_still(steps["still"], rules)
    res["steps"]["still"] = st
    gs, refs = {}, {}
    for k in need + optional:
        if g0 is None:
            res["steps"][k] = {"step": k, "name": STEP_CN[k], "ok": False, "msg": "先要静止那一步合格"}
            continue
        if k in optional and not steps[k]:
            continue
        res["steps"][k], g, ref = check_tilt(k, steps[k], g0, rules)
        if g is not None:
            gs[k], refs[k] = g, ref
    bad = [res["steps"][k] for k in ("still",) + need if not res["steps"][k]["ok"]]
    bad += [res["steps"][k] for k in optional if k in res["steps"] and not res["steps"][k]["ok"]]
    if bad:
        res["reason"] = bad[0]["msg"]
        # 静止那步不合格时，倾的几步只是「先要静止合格」：补静止就行，倾的读数留着重算
        res["redo"] = ["still"] if g0 is None else [b["step"] for b in bad]
        return res

    up = scale(g0, -1.0)
    # 方位角的零点：现在读数系的 +x 投到水平面（芯片原始轴时 x 可能正好竖着，就换 +y）
    axis0 = unit(horiz((1.0, 0.0, 0.0), up)) or unit(horiz((0.0, 1.0, 0.0), up))
    c = {}
    for k in gs:
        d = horiz(sub(gs[k], refs[k]), up)
        az = signed_angle_deg(axis0, d, up)
        if az is None:
            res["reason"] = f"「{STEP_CN[k]}」倾的方向算不出来：重做这一步"
            res["redo"] = [k]
            return res
        c[k] = wrap180(az - EXPECT_DIR[k])      # 这一步认为「前」朝哪
    q = {}
    res["quality"] = q

    def pair(a, b):
        if a in c and b in c:
            d = abs(wrap180(c[a] - c[b]))
            q[f"{'fb' if a == 'fwd' else 'lr'}_pair_deg"] = _r(d)
            if d > rules["pair_max_deg"]:
                res["reason"] = (f"{STEP_CN[a]}和{STEP_CN[b]}的方向对不上（差了 {d:.0f}°，应该正好相反）："
                                 "是不是两次往同一边倾了？")
                res["redo"] = [a, b]
                return False
        return True

    if not pair("fwd", "back") or not pair("left", "right"):
        return res
    if mode == "full":
        psi_fb = circ_mean_deg([c["fwd"], c["back"]])
        psi_lr = circ_mean_deg([c["left"], c["right"]])
        crs = abs(wrap180(psi_fb - psi_lr))
        q["cross_deg"] = _r(crs)
        if crs > 90.0:
            res["reason"], res["redo"] = _swapped_reason(psi_fb, psi_lr, frame == "body" and old_verified)
            return res
    psi = circ_mean_deg(list(c.values()))
    skews = {k: wrap180(c[k] - psi) for k in c}
    for k, s in skews.items():
        res["steps"][k]["skew_deg"] = _r(abs(s))
    worst = max(skews, key=lambda k: abs(skews[k]))
    q["max_skew_deg"] = _r(abs(skews[worst]))
    if abs(skews[worst]) > rules["skew_max_deg"]:
        res["reason"] = (f"「{STEP_CN[worst]}」斜了 {abs(skews[worst]):.0f}°（应该正{STEP_CN[worst][0]}方）："
                         "照 3D 上箭头指的方向倾，手别拧")
        res["redo"] = [worst]
        return res
    if mode == "full":
        if crs > rules["cross_max_deg"]:
            res["reason"] = (f"前后和左右不垂直（差了 {crs:.0f}°，朝向会错一半那么多）："
                             "倾的时候手拧着了？照 3D 上的箭头，正前、正后、正左、正右")
            # 旧方向可信时，离现在的「前」远的那一对多半是拧着的；不可信就四步都补
            if frame == "body" and old_verified:
                res["redo"] = ["left", "right"] if abs(wrap180(psi_lr)) > abs(wrap180(psi_fb)) else ["fwd", "back"]
            else:
                res["redo"] = list(TILTS)
            return res
        q["grade"] = "good" if crs <= rules["good_cross_deg"] and abs(skews[worst]) <= rules["good_skew_deg"] else "ok"
        q["yaw_uncert_deg"] = _r(max(crs / 2, 0.5))
    else:
        q["grade"] = "coarse"
        res["notes"].append("粗校：只用了静止和前倾，左右没验；托着做的，站姿补偿不准。能站以后再站着精校一次")
    p = math.radians(psi)
    x_dir = add(scale(axis0, math.cos(p)), scale(cross(up, axis0), math.sin(p)))
    return _finish(res, g0, up, x_dir, hg, frame, old_mount, old_verified, old_source, rules)


def _swapped_reason(psi_fb, psi_lr, trust_old):
    """前后那对和左右那对差了 ~180°：有一对做反了。旧方向可信时，离现在的「前」远的那对就是做反的。回 (原因, 要补做的步)。"""
    if trust_old:
        if abs(wrap180(psi_lr)) > abs(wrap180(psi_fb)):
            return "左倾、右倾做反了：左右以鸭子自己的为准（鸭子面对你时，它的左边在你右手边）", ["left", "right"]
        return "前倾、后倾做反了：头那边往下是前倾", ["fwd", "back"]
    return ("前后、左右里有一对做反了：前倾是头那边往下；左右以鸭子自己的为准（鸭子面对你时，它的左边在你右手边）",
            list(TILTS))


def _finish(res, g0, up, x_dir, hg, frame, old_mount, old_verified, old_source, rules):
    """「上」+「前」→ 三根轴 → 修正 / 新方向；再拆开说、查要人确认的事。"""
    y = unit(cross(up, x_dir))
    x = cross(y, up)
    r_lm = (x, y, up)                        # 行 = 水平系 L 的三根轴：v_L = R_LM · v_M
    r_bl = mat_from_q(q_between(DOWN, hg))   # HOME 时机身相对水平系的姿态（偏航取 0）
    r_bm = mat_mul(r_bl, r_lm)
    corr = q_from_mat(r_bm)
    res["correction"] = [round(c, 9) for c in corr]

    # 拆开说：站姿那部分（俯仰、横滚）、朝向那部分（偏航）
    hp = math.degrees(math.atan2(hg[0], -hg[2]))
    hroll = math.degrees(math.atan2(hg[1], -hg[2]))
    parts = {"total_deg": _r(q_angle_deg(corr))}
    if frame == "body":
        parts["pitch_deg"] = _r(math.degrees(math.atan2(g0[0], -g0[2])) - hp)
        parts["roll_deg"] = _r(math.degrees(math.atan2(g0[1], -g0[2])) - hroll)
        parts["yaw_deg"] = _r(signed_angle_deg((1.0, 0.0, 0.0), x, up))
        parts["stand_tilt_deg"] = _r(angle_deg(g0, hg))
    res["parts"] = parts

    # 新方向
    if frame == "sensor":
        mount = corr
    elif old_mount is not None:
        mount = compose_mount(corr, q_normalize(old_mount))
    else:
        mount = None
        res["notes"].append("没给旧的安装方向，只能给出修正量，算不出新方向")
    if mount is not None:
        res["mount"] = [round(c, 9) for c in mount]
        res["standard"] = describe_mount(mount)

    # 要知道 / 要确认的事
    full = res["mode"] == "full"
    if frame == "body" and old_verified and parts.get("yaw_deg") is not None:
        tot = q_angle_deg(corr)
        if abs(parts["yaw_deg"]) > rules["flip_deg"]:
            if old_source == "coarse":
                res["confirm"].append(
                    f"朝向要改 {parts['yaw_deg']:+.0f}°：上次粗校可能做反了（那次只验了「前」，没验左右）。"
                    "对照模块实际装法，确认现在这次是对的再写")
            else:
                res["confirm"].append(
                    f"朝向要改 {parts['yaw_deg']:+.0f}°。多半是前后、左右都做反了（相当于鸭子转了个身）。"
                    "只有确实把 IMU 模块转着装过，才照这个写")
        elif tot > rules["big_change_deg"]:
            res["confirm"].append(f"跟现在的方向差了 {tot:.1f}°，比较多：模块是不是装歪了、换过装法？确认了再写")
    if frame == "body" and full:
        st = parts["stand_tilt_deg"]
        if st is not None and st > rules["stand_confirm_deg"]:
            res["confirm"].append(
                f"站着时读数离官方 HOME 差 {st:.1f}°（主程序自己验方向时直立只放 {rules['stand_confirm_deg']:.0f}°）："
                "多半是腿的零位没校好、桌子不平，或者静止那 3 秒手碰着鸭子了。确认是模块装歪了再写")
        res["notes"].append(
            f"站着时读数比官方 HOME 前倾 {parts['pitch_deg']:+.1f}°、左倾 {parts['roll_deg']:+.1f}°，会一起折进安装方向"
            "（光靠 IMU 分不清是模块装歪还是腿让机身歪了；腿的零位先校好）")
    if not full and res.get("standard"):
        s = res["standard"]
        res["confirm"].append(
            f"粗校算出来：{s['text']}" + (f"（再偏 {s['off_deg']:.0f}°）" if s["off_deg"] >= 1 else "")
            + "。对照模块实际怎么装的，对得上再写（做反了主程序验不出左右）")
    if res.get("standard") and res["standard"]["off_deg"] > 20:
        res["notes"].append(f"新方向离最近的标准装法（{res['standard']['text']}）也差 {res['standard']['off_deg']:.0f}°：模块是斜着装的？")
    res["ok"] = True
    return res
