// IMU 页：看现在的姿态、选芯片、站着校安装方向。
// 用户 10-18：「开启采集读数，官方站姿的时候收集左移右移前移后移，让代码自己去校准，校准成功写进去保存；旁边有鸭子的 3D」；
// 接进 Studio 以后又定了流程：「点开始采集，采集开始，然后再点采集结束，采集完了，然后自动化校准」「大家装的都不一样，需要重新 offset 一下」。
//
// - 一个大按钮三种状态：开始采集 → 采集中…（点这里结束）→ 正在算… → 出结果。
// - 采集中：先在桌上静止 3 秒；然后前、后、左、右各倾一次（顺序随便），每一步先端平停半秒当零点，再倾、停 1 秒。
//   3D 上一条弯箭头示范下一步往哪倾；做够一个方向，那个方向的圆点亮、响一声短的（Web Audio 自己合成，能静音）。
//   五步齐了自动结束；中途点结束，缺哪步就说哪步，接着采或重来。
// - 算完：3D 变成「校准前 / 校准后」两只并排，竖直参考线 + 大字倾角，下面写差了几度；再下面是写入（后端现在拒，上鸭子才接）。
//   完整站着校完、跟主程序现在用的方向差 < 2°、站姿倾斜 ≤ 5°：后端给总览的上手清单记一笔「核对过」（res.check；只是记录，
//   不往鸭子写任何东西：总览重排审查 O4）。
//   没过：写原因，只让人补做没过的那几步（后端回的 redo）。
// - 「停住了没有」「端平的那一下」两条规则跟 studio/imu_calib.py 的 find_hold / find_ref 一句一句对应，参数同名（RULES 上半截）；
//   前端只是更严（倾 ≥12°、≤30°、停满 1 秒、方向离前后左右之一 ≤35°），前端放行的后端一定收。
//   方向由后端算：link.cmd('imu.op', {op:'solve', samples})，samples 是平铺列表 [{step, t, g}]。
//   端到端测试：tests/imu_wizard_e2e.mjs 喂读数给这里的向导，tests/test_imu_wizard_e2e.py 把采到的交给 Python 算。
// - 后端（studio/imu.py）接 solve / coarse-capture / verify：出厂标定模式下用 busStatus.last_sample.imu
//   采「静止 + 前倾」，算完交给主程序的 verify-imu（摆正 ≤5°，20 秒内往前低头 15–30°）写进标定文件。
//   总线板卡由主程序启动时自动识别；I²C 芯片检测仍用 microduck-firstboot。
import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';
import { Duck3D } from './duck3d.js';
import { TwoStep } from './confirm.js';
import { imuDiagnosticCopy } from './startup-status.js';

const RULES = {
  // ---- 跟 imu_calib.py RULES 同名同值（改一边就要改另一边，端到端测试会抓） ----
  still_min_n: 15,
  still_max_spread_deg: 1.5,
  level_deg: 5,              // 端平：离桌上静止这么多以内
  ref_min_n: 5,              // 端平至少连续这么多个读数
  ref_spread_deg: 1.5,       // 端平时自己最多晃这么多
  hold_band_deg: 1.8,        // 停住段：跟最后一个读数差这么多以内（单个乱跳读数跳过）
  // ---- 前端自己的（比后端严） ----
  still_s: 3.0,              // 静止采 3 秒
  hold_min_n: 8,             // 后端 5
  hold_s: 1.0,               // 停满 1 秒算这个方向做够了
  tilt_go_deg: 12,           // 后端最少 10°
  tilt_target_deg: 15,
  tilt_max_deg: 30,          // 后端最多 35°
  dir_max_deg: 35,           // 倾的方向离前后左右之一（按现在的方向）≤ 这么多才认
  stale_ms: 1000,
  dedupe_ms: 40,             // 没有 state_t_ns 时：两帧的「生成时间」差这么少，当成同一个读数
  max_step_n: 600,           // 每段最多留这么多个读数（10 Hz 下 1 分钟）
};

const STEPS = ['still', 'fwd', 'back', 'left', 'right'];
const TILTS = ['fwd', 'back', 'left', 'right'];
// dir：往这边倾时重力偏向哪（按「现在的方向」，从机身 +x 起逆时针，度）。只用来认方向、画箭头，不参与计算
const STEP = {
  still: { cn: '静止站好', short: '静止' },
  fwd: { cn: '前倾', short: '前', dir: 0, how: '头那边往下' },
  back: { cn: '后倾', short: '后', dir: 180, how: '屁股那边往下' },
  left: { cn: '左倾', short: '左', dir: 90, how: '鸭子自己的左边往下（站在它后面看，就是你的左边）' },
  right: { cn: '右倾', short: '右', dir: -90, how: '鸭子自己的右边往下（站在它后面看，就是你的右边）' },
};
const DIRV = { fwd: [1, 0, 0], back: [-1, 0, 0], left: [0, 1, 0], right: [0, -1, 0] };   // 机身系（= 3D 场景的世界系）

const CHIP_CN = { bus: '总线小板（ID 200）', bno08x: 'BNO085（I²C）', lsm6dsv16x: 'LSM6DSV16X（I²C）' };
const BUS_BOARD_CN = { ft6: 'FT6', imu_to_dxl: 'IMU-to-DXL', sflp_124_12: 'SFLP 124/12', qinmo: 'Qinmo（已配置）' };
const BUS_PROTOCOL_CN = { feetech: 'Feetech', dxl2: 'Dynamixel 2.0' };
const unsupportedProfile = info => info && info.profile !== undefined && !['legacy', 'qinmo'].includes(info.profile);
const unsupportedTransport = info => info && (!['feetech', 'dxl2'].includes(info.protocol) || unsupportedProfile(info));
function chipLabel(imu, live = true, diagnosticElapsed = 0) {
  if (imu?.source !== 'bus') return CHIP_CN[imu?.source] || imu?.source || '—';
  if (live === false) return CHIP_CN.bus;
  const diagnostic = imuDiagnosticCopy(imu?.diagnostic, live, diagnosticElapsed);
  if (diagnostic.board) return diagnostic.board;
  const info = imu.imu_bus;
  if (imu?.profile_error || unsupportedTransport(info)) return 'IMU 协议 / 类型待确认';
  return info ? `${BUS_BOARD_CN[info.board] || info.board || '总线小板'} · ${BUS_PROTOCOL_CN[info.protocol] || info.protocol}（ID 200）`
    : CHIP_CN.bus;
}
function imuContext(mode, imu) {
  return JSON.stringify([mode, imu?.source, imu?.source === 'bus' ? imu.imu_bus || null : null,
    imu?.configured_imu_profile ?? null, imu?.metadata_error ?? null, imu?.profile_error ?? null,
    imu?.redo?.session_id || null, !!imu?.redo?.active]);
}

// ---------------- 小算术（跟 imu_calib.py 同一套） ----------------

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.sqrt(dot(a, a));
const unit = (a) => { const n = len(a); return Number.isFinite(n) && n > 1e-9 ? [a[0] / n, a[1] / n, a[2] / n] : null; };
const deg = (r) => r * 180 / Math.PI;
const rad = (d) => d * Math.PI / 180;
const angle = (a, b) => { const ua = unit(a), ub = unit(b); return ua && ub ? deg(Math.acos(Math.max(-1, Math.min(1, dot(ua, ub))))) : null; };
const meanDir = (vs) => (vs.length ? unit(vs.reduce((s, v) => [s[0] + v[0], s[1] + v[1], s[2] + v[2]], [0, 0, 0])) : null);
const spread = (vs) => { const m = meanDir(vs); return m ? Math.max(0, ...vs.map((v) => angle(v, m) ?? 0)) : 0; };
// 90% 分位（imu_calib.spread_p90_deg）：静止那一步用，一两帧乱跳不算晃
const spreadP90 = (vs) => {
  const m = meanDir(vs);
  if (!m) return 0;
  const ds = vs.map((v) => angle(v, m) ?? 0).sort((a, b) => a - b);
  return ds[Math.floor(0.9 * (ds.length - 1))];
};
// imu_calib.trimmed_mean_dir：去掉离平均最远的 10% 再平均（静止那一步定「上」用）
const trimmedMeanDir = (vs) => {
  const m = meanDir(vs);
  if (!m) return null;
  const lim = spreadP90(vs);
  return meanDir(vs.filter((v) => (angle(v, m) ?? 0) <= lim + 1e-9)) || m;
};
const okVec = (g) => Array.isArray(g) && g.length === 3 && g.every((x) => typeof x === 'number' && Number.isFinite(x));
const DOWN = [0, 0, -1];

/** 相对 g0 倾了多少度、往哪边（在 g0 定的水平面里从 +x 起逆时针，度）。跟 imu_calib.tilt_of 一样。 */
function tiltOf(g0, g) {
  const t = angle(g0, g);
  const up = [-g0[0], -g0[1], -g0[2]];
  const d = sub(g, g0);
  const h = sub(d, up.map((u) => u * dot(d, up)));
  const x0 = sub([1, 0, 0], up.map((u) => u * up[0]));
  if (!unit(h) || !unit(x0)) return { t, d: null };
  const a = unit(x0), b = unit(h);
  return { t, d: deg(Math.atan2(dot(cross(a, b), up), dot(a, b))) };
}

function qRotate(q, v) {
  const [w, x, y, z] = q;
  const r = [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
    [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
    [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]];
  return r.map((row) => dot(row, v));
}

const fmt = (v, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—');
const sgn = (v, d = 1) => (typeof v === 'number' && Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(d)}` : '—');
const dirDiff = (a, b) => Math.abs(((a - b) % 360 + 540) % 360 - 180);
const names = (ks) => ks.map((k) => STEP[k].cn).join('、');

// ---------------- 停住段、端平基准（imu_calib.py 的 _run_back / find_hold / find_ref，一句一句对应） ----------------

function runBack(gs, end, band) {
  const last = gs[end];
  const idx = [end];
  let skipped = 0, j = end - 1;
  while (j >= 0) {
    if (angle(gs[j], last) <= band) { idx.push(j); j--; }
    else if (j - 1 >= 0 && angle(gs[j - 1], last) <= band) { skipped++; j--; }   // 单个乱跳：跳过
    else break;
  }
  return { idx: idx.sort((a, b) => a - b), skipped };
}

function findHold(gs) {
  if (!gs.length) return { idx: [], skipped: 0 };
  let best = runBack(gs, gs.length - 1, RULES.hold_band_deg);
  if (gs.length >= 2) {
    const alt = runBack(gs, gs.length - 2, RULES.hold_band_deg);
    if (alt.idx.length > best.idx.length + 1) best = alt;
  }
  return best;
}

function findRef(gs, g0, before) {
  const n = RULES.ref_min_n;
  for (let end = before; end >= n; end--) {
    const win = gs.slice(end - n, end);
    if (win.every((v) => (angle(g0, v) ?? 0) <= RULES.level_deg)) {
      const m = meanDir(win);
      if (m && spread(win) <= RULES.ref_spread_deg) return m;
    }
  }
  return null;
}

// ---------------- 采集（状态放在 ref 里，每来一帧 st 喂一次） ----------------
// phase：idle → collect →（齐了）solving → done / error；中途点结束、缺步 → short（接着采 / 重来）

function freshWizard() {
  return { phase: 'idle', t0: 0, g0: null,
    buf: { still: [], fwd: [], back: [], left: [], right: [] },          // 每个方向做够时，那一段读数（后端从里面找端平和停住）
    done: { still: false, fwd: false, back: false, left: false, right: false },
    seg: [],                 // 上一个方向做够以后到现在的读数
    ref: null, level: 0, cand: null, inZone: false, repeat: null, needLevel: false,
    live: null, progress: 0, hold: 0, paused: null, moved: false, over: false,
    lastKey: null, lastBirth: null, events: [], missing: [], result: null, error: null };
}

const missingSteps = (W) => STEPS.filter((k) => !W.done[k]);
const nextTodo = (W) => TILTS.find((k) => !W.done[k]) || null;

/** 喂一帧。live：页面给的「跟鸭子的连接是不是实时的」（跟总览同一个判断）；没给就看 joints_age_ms。返回 true = 要重画。
 *  做够一个方向往 W.events 里放一条（'still' / 'fwd' … / 'all'），页面拿去响一声。 */
function feed(W, st, live) {
  if (W.phase !== 'collect') return false;
  const g = st?.gravity;
  const fresh = live !== false && st && st.joints_age_ms != null && st.joints_age_ms < RULES.stale_ms;
  if (!okVec(g) || !fresh) { const p = W.paused; W.paused = '读数断了，等它回来（已采的不丢）'; return p !== W.paused; }
  const gu = unit(g);
  if (!gu) return false;
  // 去重：Studio 10 Hz 推状态，robot.state 也是 10 Hz，同一个读数可能推两次（robotd 卡住时会一直推同一个）。
  // 有 state_t_ns（robot.state 的 t_ns）就认它；没有就用「生成时间」= 收到的时间 − joints_age_ms
  const birth = performance.now() - st.joints_age_ms;
  if (st.state_t_ns != null) {
    if (st.state_t_ns === W.lastKey) return false;
    W.lastKey = st.state_t_ns;
  } else {
    if (W.lastBirth != null && Math.abs(birth - W.lastBirth) < RULES.dedupe_ms) return false;
    W.lastBirth = birth;
  }
  W.paused = null;
  const s = { t: +(birth / 1000 - W.t0).toFixed(3), g: gu.map((x) => +x.toFixed(6)) };
  if (!W.done.still) {
    const b = W.buf.still;
    b.push(s);
    if (spreadP90(b.map((x) => x.g)) > RULES.still_max_spread_deg) { W.buf.still = [s]; W.moved = true; }
    const b2 = W.buf.still;
    W.progress = Math.min(1, (b2[b2.length - 1].t - b2[0].t) / RULES.still_s);
    if (W.progress >= 1 && b2.length >= RULES.still_min_n) {
      W.g0 = trimmedMeanDir(b2.map((x) => x.g));
      W.done.still = true; W.moved = false; W.progress = 0; W.seg = []; W.ref = null;
      finishStep(W, 'still');
    }
    return true;
  }
  // 倾的几步：顺序随便。读数一直攒在 seg 里，停住了就看往哪倾、这个方向做过没有
  const b = W.seg;
  b.push(s);
  if (b.length > RULES.max_step_n) b.splice(0, b.length - RULES.max_step_n);
  const gs = b.map((x) => x.g);
  // 端平：最后 ref_min_n 个读数离桌上静止 ≤ level_deg、自己晃 ≤ ref_spread_deg → 下一次倾的零点（以最后一次为准）
  const n = RULES.ref_min_n;
  const tail = gs.slice(-n);
  const levelNow = tail.length === n && tail.every((v) => angle(W.g0, v) <= RULES.level_deg) && spread(tail) <= RULES.ref_spread_deg;
  if (levelNow) W.ref = meanDir(tail);
  let k = 0;
  for (let i = gs.length - 1; i >= 0 && k < n && angle(W.g0, gs[i]) <= RULES.level_deg; i--) k++;
  W.level = levelNow ? 1 : k / n;
  W.live = tiltOf(W.ref || W.g0, gu);
  W.over = W.live.t > RULES.tilt_max_deg;
  W.hold = 0; W.inZone = false; W.cand = null; W.repeat = null; W.needLevel = false;
  // 停住：跟后端同一套（停住段 + 它之前最后一次端平）；再加前端自己的：倾 12–30°、离前后左右之一 ≤35°、停满 1 秒
  const { idx } = findHold(gs);
  const hold = idx.map((i) => b[i]);
  const hm = meanDir(hold.map((x) => x.g));
  if (!hm) return true;
  const href = findRef(gs, W.g0, idx[0]);
  const ht = tiltOf(href || W.g0, hm);
  if (!(ht.t >= RULES.tilt_go_deg && ht.t <= RULES.tilt_max_deg) || ht.d == null) return true;
  const dirK = TILTS.find((x) => dirDiff(ht.d, STEP[x].dir) <= RULES.dir_max_deg) || null;
  W.cand = dirK;
  if (!dirK) return true;
  if (W.done[dirK]) { W.repeat = dirK; return true; }
  if (!href) { W.needLevel = true; return true; }
  W.inZone = true;
  const span = hold.length > 1 ? hold[hold.length - 1].t - hold[0].t : 0;
  W.hold = Math.min(1, (span + 1e-6) / RULES.hold_s);
  if (hold.length >= RULES.hold_min_n && W.hold >= 1) {
    W.buf[dirK] = b.slice();
    W.done[dirK] = true;
    W.seg = []; W.ref = null; W.hold = 0; W.inZone = false; W.cand = null;
    finishStep(W, dirK);
  }
  return true;
}

function finishStep(W, k) {
  if (!missingSteps(W).length) { W.phase = 'solving'; W.events.push('all'); }
  else W.events.push(k);
}

/** 开始采集（全新的一遍）。 */
function startCollect(W, now) {
  Object.assign(W, freshWizard(), { phase: 'collect', t0: now });
}

/** 点「结束」：齐了就去算，缺步就停在 short，说缺哪几步。 */
function endCollect(W) {
  if (W.phase !== 'collect') return;
  const miss = missingSteps(W);
  if (!miss.length) { W.phase = 'solving'; return; }
  W.phase = 'short'; W.missing = miss;
}

/** 接着采（做过的留着）。 */
function resumeCollect(W) {
  W.phase = 'collect'; W.seg = []; W.ref = null; W.paused = null; W.missing = []; W.result = null; W.error = null;
  if (!W.done.still) { W.buf.still = []; W.progress = 0; }
}

/** 只补做这几步（后端回的 redo）：清掉它们再接着采。 */
function redoSteps(W, steps) {
  for (const k of steps) { if (k in W.done) { W.done[k] = false; W.buf[k] = []; } }
  resumeCollect(W);
}

/** 交给后端的读数：平铺列表，每个带 step（studio/imu.py 要列表） */
function payload(W) {
  return STEPS.flatMap((k) => W.buf[k].map((x) => ({ step: k, t: x.t, g: x.g })));
}

// ---------------- 提示音（Web Audio 自己合成，很短很轻；静音记在 localStorage） ----------------

const MUTE_KEY = 'studio.imu.mute';
function loadMute() { try { return localStorage.getItem(MUTE_KEY) === '1'; } catch { return false; } }
function saveMute(v) { try { localStorage.setItem(MUTE_KEY, v ? '1' : '0'); } catch { /* 隐私模式：只在这一页有效 */ } }
let actx = null;
/** 浏览器要求「用户点过」才能出声：在「开始采集」的点击里调一次。 */
function audioUnlock() {
  try {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    actx = actx || new C();
    if (actx.state === 'suspended') actx.resume();
  } catch { actx = null; }
}
function beep(kind) {
  if (!actx) return;
  try {
    const t = actx.currentTime + 0.01;
    // 做够一个方向：一声 990 Hz；静止：低一点 660 Hz；全齐：两声往上走
    const tones = kind === 'all' ? [[880, 0], [1320, 0.13]] : [[kind === 'still' ? 660 : 990, 0]];
    for (const [f, dt] of tones) {
      const o = actx.createOscillator(), gn = actx.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      gn.gain.setValueAtTime(0.0001, t + dt);
      gn.gain.exponentialRampToValueAtTime(0.06, t + dt + 0.012);
      gn.gain.exponentialRampToValueAtTime(0.0001, t + dt + 0.11);
      o.connect(gn); gn.connect(actx.destination);
      o.start(t + dt); o.stop(t + dt + 0.12);
    }
  } catch { /* 出不了声就算了 */ }
}

// ---------------- 3D ----------------

const reduceMotion = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

/** 在 3D 上面画：竖直参考线、示范箭头（从头顶往要倾的方向弯过去，来回摆）。用鸭子自己的相机投影，转视角也跟着。 */
function drawOverlay(d, canvas, svg, arrow, plumb, now) {
  if (!svg) return;
  if (!d || !d.ready || !d.home || !d.blob) { svg.innerHTML = ''; return; }
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const V = d.camera.position.constructor;           // three.js 的 Vector3（不用另外 import three）
  d.camera.updateMatrixWorld();
  const P = d.blob.position;                          // 脚下（地面）中心
  const H = 2 * (d.home.target.z - P.z);              // 鸭子高度
  const pr = (x, y, z) => { const v = new V(x, y, z).project(d.camera); return [(v.x + 1) / 2 * w, (1 - v.y) / 2 * h]; };
  let out = '';
  if (plumb) {
    const [x1, y1] = pr(P.x, P.y, P.z), [x2, y2] = pr(P.x, P.y, P.z + H * 1.12);
    out += `<line class="plumb" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  if (arrow && DIRV[arrow]) {
    const u = DIRV[arrow], R = H * 1.08;
    const swing = reduceMotion() ? 1 : 0.55 + 0.45 * Math.sin(now / 260);
    const end = 6 + 20 * swing;                        // 弧从头顶（0°）摆到 6–26°
    const pts = [];
    for (let a = 0; a <= end + 1e-6; a += 2) {
      const s = Math.sin(rad(a)), c = Math.cos(rad(a));
      pts.push(pr(P.x + R * s * u[0], P.y + R * s * u[1], P.z + R * c));
    }
    if (pts.length >= 2) {
      const [ex, ey] = pts[pts.length - 1], [px, py] = pts[pts.length - 2];
      const dx = ex - px, dy = ey - py, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L, sz = Math.max(12, h * 0.03);
      const head = `${ex + ux * sz},${ey + uy * sz} ${ex - uy * sz * 0.6},${ey + ux * sz * 0.6} ${ex + uy * sz * 0.6},${ey - ux * sz * 0.6}`;
      // 示范的「倾下去的鸭子」：从脚下到箭头尖的一条虚线（机身中线倒向那边）
      const [gx, gy] = pr(P.x, P.y, P.z);
      out += `<line class="ghost" x1="${gx}" y1="${gy}" x2="${ex}" y2="${ey}"/>`;
      out += `<polyline class="arc" points="${pts.map((p) => p.join(',')).join(' ')}"/><polygon class="head" points="${head}"/>`;
      // 字放在箭头尖下面一点；别压到左上角的大字倾角、也别出框
      const lx = Math.min(w - 50, Math.max(50, ex)), ly = Math.min(h - 50, Math.max(120, ey + sz + 26));
      out += `<text class="lbl" x="${lx}" y="${ly}">往${STEP[arrow].short}倾</text>`;
    }
  }
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.innerHTML = out;
}

/** 换机位。字符串用 Duck3D 自带的（front34 / front / back34 / back）；{az, el} 是自己定的角度（度，跟 Duck3D 的 VIEWS 同一个算法：
 *  az 0 = 正前方，−90 = 鸭子右侧），先按 front 算好距离和看的点，再把机位挪过去（「回正」也回到这里）。 */
function applyView(d, view) {
  if (typeof view === 'string') { d.setView(view); return; }
  d.setView('front');
  const h = d.home;
  if (!h) return;
  const t = h.target, dist = h.pos.distanceTo(t), az = rad(view.az), el = rad(view.el);
  h.pos.set(t.x + dist * Math.cos(el) * Math.cos(az), t.y + dist * Math.cos(el) * Math.sin(az), t.z + dist * Math.sin(el));
  d.camera.position.copy(h.pos);
  d.controls.target.copy(t);
  d.controls.update();
  d.dirty = true;
}

function DuckView({ g, joints, stale, arrow, plumb, view = 'front34', children }) {
  const canvasRef = useRef(null), duckRef = useRef(null), svgRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [moved, setMoved] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const d = new Duck3D(canvasRef.current, { view: 'front34', centerX: 0.5, centerY: 0.56, fill: 0.7 });
    duckRef.current = d;
    d.onUserMove = () => setMoved(true);
    d.load('model/').then(() => setReady(true)).catch((e) => setErr(`3D 模型没加载：${e.message}`));
    return () => d.dispose();
  }, []);

  useEffect(() => {
    const d = duckRef.current;
    if (!d || !ready) return;
    if (Array.isArray(joints)) d.setJoints(joints);
    d.setGravity(g);
  }, [g, joints, ready]);

  const viewKey = JSON.stringify(view);
  useEffect(() => {
    if (!ready || !duckRef.current) return;
    applyView(duckRef.current, view);
    setMoved(false);
  }, [ready, viewKey]);

  useEffect(() => {
    if (!ready) return undefined;
    if (!arrow && !plumb) { if (svgRef.current) svgRef.current.innerHTML = ''; return undefined; }
    let raf = 0;
    const tick = (now) => { drawOverlay(duckRef.current, canvasRef.current, svgRef.current, arrow, plumb, now || 0); raf = requestAnimationFrame(tick); };
    tick(performance.now());
    return () => cancelAnimationFrame(raf);
  }, [ready, arrow, plumb]);

  return html`<div class="imu-view">
    <canvas ref=${canvasRef} class=${stale ? 'stale' : ''}></canvas>
    <svg class="imu-overlay" ref=${svgRef} aria-hidden="true"></svg>
    ${err ? html`<span class="imu-note" role="alert">${err}</span>` : children}
    ${moved ? html`<button class="reset-view" onClick=${(e) => { e.currentTarget.blur(); duckRef.current?.resetView(); setMoved(false); }}>回正</button>` : null}
  </div>`;
}

/** 平时一只鸭子（采集中带示范箭头）；算完一左一右「校准前 / 校准后」。第一只始终是同一个实例（key 不变），切换不重新加载。
 *  机位：采集中从后面看（鸭子的左 = 屏幕左 = 方向盘的左 = 站在它后面的人的左）；
 *  对比时，修正以前后为主就从侧面看（前倾、后倾正面看不出来），以左右为主就从正面看。 */
function ImuStage({ st, stale, corr, parts, collecting, arrow, cue }) {
  const g = okVec(st?.gravity) ? st.gravity : null;
  const joints = st?.joints;
  const after = g && corr ? qRotate(corr, g) : null;
  const tb = g ? angle(g, DOWN) : null, ta = after ? angle(after, DOWN) : null;
  const old = stale || !g;
  const sideways = corr && Math.abs(parts?.pitch_deg ?? 0) >= Math.abs(parts?.roll_deg ?? 0);
  const view = corr ? (sideways ? { az: -80, el: 6 } : 'front') : collecting ? 'back34' : 'front34';
  const noteLive = old ? html`<span class="imu-note">${g ? '读数旧了，不是现在的样子' : '没有姿态数据'} · 主程序在运动模式时才有</span>`
    : html`<span class="imu-note">实时 · 机身跟着 IMU 倾</span>`;
  return html`<div class="imu-stage-wrap">
    <div class=${'imu-stage' + (corr ? ' duo' : '')}>
      <${DuckView} key="before" g=${g} joints=${joints} stale=${old} arrow=${corr ? null : arrow} plumb=${!!corr} view=${view}>
        <div class="imu-stage-read num"><span>${corr ? '校准前' : '机身倾角（跟竖直比）'}</span><b>${fmt(tb)}°</b></div>
        ${!corr && cue ? html`<span class="imu-cue">${cue}</span>` : null}
        ${corr ? html`<span class="imu-note">现在存的方向</span>` : noteLive}
      <//>
      ${corr ? html`<${DuckView} key="after" g=${after} joints=${joints} stale=${old} plumb=${true} view=${view}>
        <div class="imu-stage-read num after"><span>校准后</span><b>${fmt(ta)}°</b></div>
        <span class="imu-note">按新方向 · 还没写</span>
      <//>` : null}
    </div>
    ${corr ? html`<p class="imu-diff num">站着的时候：校准前倾 <b>${fmt(tb)}°</b> → 校准后 <b>${fmt(ta)}°</b>，差 <b>${fmt(g && after ? angle(g, after) : null)}°</b>。
      两只鸭子都是现在的实时读数，虚线是竖直方向；${sideways ? '从侧面看（这次主要是前后歪）' : '从正面看（这次主要是左右歪）'}。</p>` : null}
  </div>`;
}

// ---------------- 方向盘：前后左右四个点 + 中间静止；做够的亮，下一个闪，蓝点是现在往哪倾 ----------------

function DirPad({ W }) {
  const C = 100, R = 80, MAX = 32;
  const r = (t) => Math.min(t, MAX) / MAX * R;
  const pt = (t, d) => [C - r(t) * Math.sin(rad(d)), C - r(t) * Math.cos(rad(d))];   // 头朝上：前 → 上，鸭子的左 → 左
  const collecting = W.phase === 'collect';
  const next = collecting && W.done.still ? nextTodo(W) : null;
  const L = collecting && W.done.still ? W.live : null;
  const p = L && L.t != null ? pt(L.t, L.d ?? 0) : [C, C];
  const ring = (t) => html`<circle cx=${C} cy=${C} r=${r(t)} class="pad-ring" />`;
  return html`<svg class="imu-pad" viewBox="0 0 200 200" role="img"
    aria-label=${`已做：${names(STEPS.filter((k) => W.done[k])) || '还没有'}`}>
    <circle cx=${C} cy=${C} r=${R + 6} class="pad-bg" />
    ${ring(RULES.tilt_go_deg)}${ring(RULES.tilt_max_deg)}
    ${TILTS.map((k) => {
      const [x, y] = pt(20, STEP[k].dir), [lx, ly] = pt(33, STEP[k].dir);
      const cls = W.done[k] ? 'done' : k === next ? 'next' : (k === W.cand && W.inZone ? 'hot' : '');
      return html`<g class=${'pad-dot ' + cls}><circle cx=${x} cy=${y} r="13" />
        ${W.done[k] ? html`<path d=${`M${x - 5.5},${y} l4,4 l7,-8`} class="tick" />` : null}
        <text x=${lx} y=${ly + 4} class="pad-lbl">${STEP[k].short}</text></g>`;
    })}
    <g class=${'pad-dot center ' + (W.done.still ? 'done' : collecting ? 'next' : '')}>
      <circle cx=${C} cy=${C} r="11" />
      ${W.done.still ? html`<path d=${`M${C - 5},${C} l3.5,3.5 l6.5,-7.5`} class="tick" />` : null}
    </g>
    ${L && W.hold > 0 ? html`<circle cx=${p[0]} cy=${p[1]} r="12" class="pad-hold"
      style=${`stroke-dasharray:${(W.hold * 75.4).toFixed(1)} 76`} transform=${`rotate(-90 ${p[0]} ${p[1]})`} />` : null}
    ${L ? html`<circle cx=${p[0]} cy=${p[1]} r="6" class=${'pad-live' + (W.over ? ' over' : W.inZone ? ' on' : '')} />` : null}
  </svg>`;
}

// ---------------- 右边几块 ----------------

// 方向确认没有，只认 imu 消息里的 mount.verified（calibration-status 回读，studio/imu.py 的 view）。
// 不看 robotd 起不来时的原话：关节没标定时报的也是「…and verified IMU mounting」（feetech.rs:1018-1029）。
// 主程序只有「确认 / 没确认」两种；「临时方向」是 Studio 以后可能加的（方案拍板 3 的 ②），后端给 imu.temporary 才显示。
function statusOf(st, imu, live) {
  const s = st?.imu?.status;
  const v = imu?.mount?.verified;
  if (imu?.profile_error) return { word: 'IMU 类型无法确认', kind: 'bad', hint: imu.profile_error };
  if (imu?.mount_protocol_mismatch) return { word: '方向需重新标定', kind: 'warn',
    hint: '当前总线协议 / 类型与已存的安装方向不匹配：在下面重新标定，并交给主程序验证' };
  if (v === false) {
    return { word: '方向没确认', kind: 'warn',
      hint: st?.posture === 'starting' ? '主程序不让进运动模式（站不起来）：先粗校一次' : '装歪了走路会往前冲：先校准' };
  }
  if (imu?.temporary) return { word: '临时方向', kind: 'warn', hint: '只认了哪边朝上，朝前没验过：能站，别走' };
  if (live !== false && s === 'missing') return { word: '读不到 IMU', kind: 'bad', hint: '检查模块接线，或者在下面重新识别芯片' };
  if (live !== false && s === 'frozen') return { word: '读数冻住了', kind: 'bad', hint: '姿态不更新，别走路' };
  // imu 消息还没到时：运动模式下 IMU 正常就说明方向确认过（没确认 robotd 进不了运动模式）
  if (v === true || (v == null && s === 'ok' && st?.mode === 'motion')) {
    const when = imu?.stand_calib_at;
    return { word: '方向已确认', kind: 'ok', hint: when ? `上次站着校准：${when}` : '主程序验过「哪边朝上、哪边朝前」；还没站着校过站姿' };
  }
  return { word: '不知道', kind: null, hint: '还没读到主程序存的安装方向' };
}

function freshText(st, live, age) {
  if (live === false) return { live: false, text: Number.isFinite(age) ? `${Math.round(age / 1000)} 秒没更新` : '连不上鸭子' };
  const a = st?.joints_age_ms;
  if (a == null) return { live: false, text: '没有' };
  if (a >= RULES.stale_ms) return { live: false, text: `${Math.round(a / 1000)} 秒没更新` };
  return { live: true, text: `实时 · ${(a / 1000).toFixed(1)} 秒前` };
}

function StatusCard({ st, imu, live, age, diagnosticElapsed }) {
  const S = statusOf(st, imu, live);
  const F = freshText(st, live, age);
  const src = imu?.source;
  const guard = st?.guard;
  return html`<section class=${'board imu-card' + (F.live ? '' : ' imu-stale')}>
    <h3>控制与安装方向${F.live ? '' : html` <span class="sv-small">（姿态读数：${F.text}）</span>`}</h3>
    <div class="imu-kv">
      <span>方向</span><b class=${'imu-word ' + (S.kind || '')}>${S.kind === 'warn' || S.kind === 'bad' ? html`<span class=${'badge ' + S.kind}>${S.kind === 'bad' ? '×' : '!'}</span>` : S.kind === 'ok' ? '✓ ' : ''}${S.word}</b>
      <span></span><span class="sv-small">${S.hint}</span>
      <span>芯片</span><b>${chipLabel(imu, live, diagnosticElapsed)}</b>
      ${src === 'bus' ? html`<span>配置类型</span><b>${imu?.configured_imu_profile === 'qinmo' ? 'Qinmo（已配置）'
        : imu?.configured_imu_profile === 'legacy' ? '现有板卡自动识别'
        : imu?.metadata_error || unsupportedTransport(imu?.imu_bus) ? '待确认（诊断数据不受支持）'
        : imu?.configured_imu_profile != null ? '不支持的类型（需确认）' : '现有板卡（旧版主程序）'}</b>
        <span>实际连接</span><b>${imuDiagnosticCopy(imu?.diagnostic, live, diagnosticElapsed).board || (imu?.imu_bus ? chipLabel(imu, live, diagnosticElapsed) : '尚未连接')}</b>
        <span>角速度单位</span><b class="num">${imu?.profile_error || unsupportedTransport(imu?.imu_bus) ? '待确认（诊断数据不受支持）'
          : imu?.imu_bus?.profile === 'qinmo' ? '原始值 ÷ 16.4 °/s'
          : imu?.imu_bus ? '原始值 × 0.0175 °/s' : '连接后显示'}</b>` : null}
      <span>倾角</span><b class="num">${fmt(st?.tilt_deg)}°<small> 跟竖直比；主程序 ${guard?.tilt_tolerance_deg ?? 10}° 以内才让走</small></b>
      <span>站姿</span><b class="num">${st?.hold ? html`离 HOME 最多差 ${fmt(st.hold.max_error_deg)}°<small> 标定模式站姿使能${st.hold.stage === 'holding' ? '：站住了' : '：正在站起来'}</small>`
        : guard?.max_joint_error_deg != null ? html`离 HOME 最多差 ${fmt(guard.max_joint_error_deg)}°<small> ${guard.joint_tolerance_deg ?? 5}° 以内算站好</small>` : '—'}</b>
      <span>读数</span><b class=${'num' + (F.live ? '' : ' imu-dim')}>${F.text}${imu?.reader?.errors ? html`<small> 读错 ${imu.reader.errors} 次</small>` : null}</b>
    </div>
    ${src === 'bus' && imu?.imu_bus?.protocol === 'dxl2' ? html`<p class="sv-small">这类板卡不提供采样序号、状态位和传感器读数年龄；主程序按主机接收新鲜度和原始字节是否冻结检查读数。</p>` : null}
    ${imu?.mount?.quat ? html`<details class="imu-details"><summary>安装方向（四元数 w, x, y, z）</summary>
      <code class="num">${imu.mount.quat.map((x) => fmt(x, 4)).join(', ')}</code>${imu.mount.verified ? ' · 已验证' : ' · 没验证'}
      <p class="sv-small">装法改了或上次没调对，可以在下面「IMU 安装方向」重新标定、重新验证。</p></details>` : null}
  </section>`;
}

export function DiagnosticCard({ st, imu, live, diagnosticElapsed }) {
  const diagnostic = imu?.diagnostic || st?.imu?.diagnostic;
  if ((imu?.source ?? st?.imu?.source) !== 'bus' || (st?.posture !== 'starting' && !diagnostic?.age_ms
      && diagnostic?.responding == null)) return null;
  const d = imuDiagnosticCopy(diagnostic, live, diagnosticElapsed);
  return html`<section class="board imu-card imu-diagnostic" aria-label="总线 IMU 只读诊断">
    <h3>总线 IMU 只读诊断</h3>
    <p class=${'imu-word ' + d.kind}>${d.word}</p>
    ${d.board ? html`<p>${d.board}</p>` : null}
    <p class="sv-small">${d.hint}</p>
    ${live !== false && diagnostic?.error ? html`<p class="sv-small">原文：${diagnostic.error}</p>` : null}
    <p class="sv-small">板卡应答独立于舵机；不代表已确认安装方向、可采集标定或可运动。姿态和标定仍使用主程序的控制读数。</p>
  </section>`;
}

function ChipCard({ imu, link, live, diagnosticElapsed }) {
  const [profile, setProfile] = useState(imu?.configured_imu_profile || 'legacy');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { setProfile(imu?.configured_imu_profile || 'legacy'); }, [imu?.configured_imu_profile]);
  const selection = imu?.profile_selection;
  const reason = live === false || link.status !== 'open' ? '连不上鸭子：等它回来'
    : imu?.metadata_error || unsupportedTransport(imu?.imu_bus) ? imu?.metadata_error || imu?.profile_error || '主程序报告了不支持的 IMU 类型'
    : !selection?.can_start ? selection?.reason || '主程序尚不支持网页选择类型：请先由维护者更新主程序' : null;
  const select = async () => {
    setBusy(true); setMessage(null);
    try {
      const reply = await link.cmd('imu.op', { op: 'profile-select', profile, confirmed: true }, 180000);
      setMessage(reply.ok ? '类型已保存，正在重连标定模式。双手扶稳并保持卸力，完成上面的安装方向采集与主程序验证。'
        : reply.reason || reply.error || '类型选择未完成：查看会话状态，可取消并恢复原参数');
    } finally { setBusy(false); }
  };
  return html`<section class="board imu-card">
    <h3>芯片 <span class="sv-small">正在用：${chipLabel(imu, live, diagnosticElapsed)}</span></h3>
    <p class="sv-small">现有自动识别支持 FT6、IMU-to-DXL（Feetech）；SFLP 124/12（Dynamixel 2.0）。FT6 和自家小板按已知身份识别；通用 DXL2 只确认兼容布局。</p>
    <p class="sv-small">Qinmo 没有文档可验证的唯一身份，需在这里选择一次。保存后以后开机会沿用此类型与单位；页面显示「已配置」，不会把兼容布局当成厂商认证。换板或装法改变后，必须重新验证安装方向。</p>
    ${imu?.source === 'bus' || imu?.source == null ? html`<label class="sv-small">总线 IMU 类型
      <select aria-label="总线 IMU 类型" value=${profile} disabled=${busy || !!imu?.redo?.active}
        onChange=${e => { setProfile(e.target.value); setMessage(null); }}>
        <option value="legacy">自动识别（现有板卡）</option><option value="qinmo">Qinmo · Feetech · 原始值 ÷ 16.4 °/s</option>
      </select></label>
      <div class="imu-actions"><${TwoStep} key=${profile} label="保存类型并重新连接"
        ask="已扶稳，确认重连并重新标定" onConfirm=${select} disabled=${busy || !!reason}
        title=${reason} busyLabel="正在保存类型并重连…" /></div>
      <p class="sv-small">先扶稳并卸力。保存后主程序会重启进入标定模式，旧安装方向失效；验证通过后才完成。取消可恢复原参数；启动失败恢复时会停留标定模式。</p>
      ${reason ? html`<p class="sv-warn">${reason}</p>` : null}
      ${message ? html`<p role="status" class="sv-small">${message}</p>` : null}` : null}
    <p class="sv-small">I²C 模块要在第一次开机前接好。之后才接的，用 SSH 在鸭子上运行</p>
    <p><code class="num">sudo microduck-firstboot --detect-imu-only --force</code></p>
    <p class="sv-small">它会重新检测 IMU 并补上启动配置；跑完重启一次主程序。</p>
  </section>`;
}

function blockReason(st, imu, live) {
  if (!st) return '还没连上鸭子';
  if (live === false) return '连不上鸭子：等它回来';
  if (imu?.profile_error) return imu.profile_error;
  if (imu?.redo?.active) return '正在重新标定安装方向：先完成主程序验证，或取消并恢复原参数';
  if (st.posture === 'servo_session') return '舵机页还占着总线（有舵机保持着时不会自动交还）：点顶上的「交还主程序」';
  if (st.posture === 'absent') return '鸭子主程序没在跑';
  if (st.posture === 'starting') return `主程序还在启动${st.start_error ? `：${st.start_error}` : ''}`;
  if (st.mode === 'commissioning') {
    // 标定模式也能站（fork hold-home，用户 10-21）：站住了就能站着校，方向没确认过也能校（算出整份新方向，第 3 步写进去）
    if (!st.hold) return '标定模式软着：先点上面的「站姿使能」让它站起来（双手扶着）';
    if (st.hold.stage !== 'holding') return '正在站起来：等它站稳';
    if (!okVec(st.gravity) || st.joints_age_ms == null || st.joints_age_ms > RULES.stale_ms) return '没有实时姿态数据';
    return null;
  }
  if (imu?.mount?.verified === false) return '方向还没确认过：先做上面的「粗校」';
  // 站姿没使能：提示先点上面的「站姿使能」（任务 17）
  if (st.posture === 'limp') return '软着：先点上面的「站姿使能」让它站起来（双手托着）';
  if (st.posture === 'homing') return '正在站起来：等它站稳';
  if (st.posture === 'walking') return '正在走：先停下';
  if (st.posture === 'policy') return '策略在跑，一提起来它会迈腿：按一下手柄 +，回到「站好」再做';
  if (st.posture === 'sitting') return '坐着：先点上面的「站姿使能」让它站起来';
  if (st.posture !== 'holding') return '读不出它现在的姿势';
  if (!okVec(st.gravity) || st.joints_age_ms == null || st.joints_age_ms > RULES.stale_ms) return '没有实时姿态数据';
  if (st.imu?.status === 'missing' || st.imu?.status === 'frozen') return 'IMU 读数不正常，先查线';
  return null;
}

// ---------------- 站姿使能 / 失能（任务 17，用户 10-21：「得有个按钮站姿使能、失能」） ----------------
// 「站姿使能」走遥控卡「站起来」同一条路：link.cmd('pad.op', {op: 'stand'})，后端按姿势再判一遍（软着 robot.init、坐着 sit_toggle）；
// 能不能按用手柄快照里遥控卡那颗「站起来」的判断（pd.motion.buttons.stand）。「失能（卸力）」走顶上「卸力」同一条路：link.cmd('relax')。
// 标定模式也能站（用户 10-21：「咱标定也能站姿不就好了吗？非要切到运动模式」）：「站姿使能」发 imu.op hold →
// fork 标定模式的 hold-home，15 颗一起 3 秒转到 HOME 锁住（没有平衡，扶着点），IMU 照常读，站着校准就在标定模式做；
// 「失能」一样走顶上「卸力」（标定模式下 robot.relax 就是总线 relax）。
const POSE_CN = { holding: '站着', policy: '站着（策略在跑）', walking: '在走', limp: '软着', homing: '正在站起来',
  sitting: '坐着', commissioning: '标定模式（软着）', starting: '主程序在启动', absent: '主程序没在跑', servo_session: '调舵机中' };
const HOLD_CN = { holding: '标定模式（站着）', ramping: '标定模式（正在站起来）' };
const HOLD_CONFIRM = '标定模式站姿使能：15 颗舵机会在 3 秒内一起转到站姿并锁住。\n'
  + '标定模式不管平衡，站起来以后要扶着点；方向用舵机页设的（没设过的沿用主程序文件里的）。\n双手扶好鸭子，确定吗？';

function StanceCard({ st, imu, pd, link, live }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const commissioning = (st?.mode || imu?.mode) === 'commissioning';
  const hold = st?.hold || imu?.hold || null;
  const now = !st ? '还没连上' : live === false ? '连不上鸭子' : st.posture === 'servo_session' ? POSE_CN.servo_session
    : commissioning && hold ? HOLD_CN[hold.stage] || '标定模式（站着）' : (POSE_CN[st.posture] || '读不出');
  const sb = pd?.motion?.buttons?.stand;              // 遥控卡「站起来」的判断（后端推的手柄快照，1 Hz 也够用）
  const standWhy = !st || live === false ? '连不上鸭子：等它回来'
    : imu?.redo?.active ? '重新标定方向期间保持卸力：双手托住，完成或取消后再站起来'
    : st.posture === 'servo_session' ? '舵机页还占着总线：点顶上的「交还主程序」'
      : st.posture === 'starting' ? '主程序还在启动：等它起来'
        : commissioning ? (hold ? (hold.stage === 'holding' ? '已经站着了' : '正在站起来') : null)
          : sb && !sb.ok ? sb.why : null;
  const stand = async () => {
    if (commissioning) {
      if (!window.confirm(HOLD_CONFIRM)) return;
      setBusy(true); setMsg(null);
      const r = await link.cmd('imu.op', { op: 'hold' }, 25000);
      setBusy(false);
      setMsg(r.ok ? { ok: true, text: '站姿已保持。校准期间持续扶稳，结束后点右上角「全局卸力」。' }
        : { ok: false, text: r.reason || r.error || '没站起来' });
      return;
    }
    if (typeof st?.tilt_deg === 'number' && st.tilt_deg > 30
      && !window.confirm(`它现在是躺着的（倾斜 ${Math.round(st.tilt_deg)}°）。站起来时腿会突然用力：确定托好了吗？`)) return;
    setBusy(true); setMsg(null);
    const r = await link.cmd('pad.op', { op: 'stand' }, 10000);
    setBusy(false);
    setMsg(r.ok ? { ok: true, text: '站起来发出去了：双手托着它，站稳了再开始站着校准' } : { ok: false, text: r.reason || r.error || '没站起来' });
  };
  return html`<section class="board imu-card imu-stance">
    <h3>站姿 <span class="sv-small">站着校准要它先站住</span></h3>
    <div class="imu-actions">
      <button class="btn-dark imu-stand" disabled=${!!standWhy || busy} onClick=${stand}
        title=${standWhy || '跟遥控卡上的「站起来」同一条路'}>${busy ? '等它回话…' : '站姿使能'}</button>
      <span class="sv-small imu-stance-now">现在：<b>${now}</b></span>
    </div>
    ${standWhy ? html`<p class="sv-small imu-stance-why">${standWhy}</p>` : null}
    ${imu?.redo?.active ? null : commissioning
      ? html`<p class="sv-small">15 颗舵机转到站姿并锁住，不提供平衡。持续扶稳，结束后点右上角「全局卸力」。</p>`
      : standWhy ? null
        : html`<p class="sv-small">站姿使能与遥控「站起来」相同。持续扶稳，结束后点右上角「全局卸力」。</p>`}
    ${msg ? html`<div class=${'banner ' + (msg.ok ? 'warn' : 'bad')} role="status"><span>${msg.text}</span></div>` : null}
  </section>`;
}

/** 采集中这一刻该跟人说的一句话（大字）+ 一句补充（小字）。 */
function collectWords(W) {
  if (W.paused) return [W.paused, ''];
  if (!W.done.still) {
    return [W.moved ? '动了一下，重新数' : '放在平桌上，松手，别碰它',
      `鸭子自己站着的这 3 秒就是「官方站姿」的参照 · 还要 ${Math.max(0, RULES.still_s * (1 - W.progress)).toFixed(1)} 秒`];
  }
  const next = nextTodo(W);
  const n = next ? STEP[next] : null;
  const tip = n ? `双手捏住躯干两侧提起来一点（脚离桌 2–3 cm），端平停半秒，再往${n.short}倾（${n.how}）15° 左右，停 1 秒。顺序随便` : '';
  if (W.over) return ['太多了，回一点', tip];
  if (W.repeat) return [`${STEP[W.repeat].cn}已经记下了`, `放回端平，做${next ? STEP[next].cn : '下一个'}`];
  if (W.needLevel) return ['先端平停一下', '零点没记上：每次倾之前，端平停半秒'];
  if (W.inZone && W.cand) return [`${STEP[W.cand].cn}：停住…`, '再停一下就记下了'];
  return [n ? `下一个：${n.cn}` : '都齐了', tip];
}

function CollectCard({ st, imu, live, W, act, muted, setMuted }) {
  const block = blockReason(st, imu, live);
  const ph = W.phase;
  const guardErr = st?.guard?.max_joint_error_deg;
  const res = W.result;
  const mute = html`<label class="imu-mute" title="做够一个方向时响一声"><input type="checkbox" checked=${muted}
    onChange=${(e) => setMuted(e.target.checked)} /> 静音</label>`;
  let button;
  if (ph === 'collect') button = html`<button class="imu-big rec" onClick=${act.end}><span class="rec-dot"></span>采集中…（点这里结束）</button>`;
  else if (ph === 'solving') button = html`<button class="imu-big" disabled>正在算…</button>`;
  else if (ph === 'short') button = html`<button class="imu-big" onClick=${act.resume}>接着采</button>`;
  else button = html`<button class="imu-big" disabled=${!!block} onClick=${act.start}>${ph === 'idle' ? '开始采集' : '重新采集'}</button>`;
  const [say, sub] = ph === 'collect' ? collectWords(W) : ['', ''];
  return html`<section class="board imu-card imu-collect">
    <h3>站着校准 <span class="sv-small">约 1 分钟 · 两只手</span>${mute}</h3>
    ${ph === 'idle' ? html`<details class="imu-instructions"><summary>采集步骤</summary><ol class="imu-howto">
      <li>鸭子站好（点上面的「站姿使能」或者按手柄 +，别开走路），点「开始采集」。</li>
      <li>放在平桌上，<b>别碰它 3 秒</b>。</li>
      <li>站到鸭子后面（跟它朝一个方向，它的左就是你的左），双手捏住躯干两侧提起来，<b>前、后、左、右各倾一次</b>：
        每次先端平停半秒，再倾 15° 左右停 1 秒。顺序随便，3D 上的箭头会示范。</li>
      <li>做够一个方向那个点就亮、响一声；四个都亮了自动结束、自动算。</li>
    </ol></details>` : null}
    ${ph === 'collect' || ph === 'solving' || ph === 'short' ? html`<div class="imu-live">
      <${DirPad} W=${W} />
      <div class="imu-live-txt">
        ${ph === 'collect' ? html`<p class="imu-say"><b>${say}</b><br /><span class="sv-small">${sub}</span></p>
          ${!W.done.still ? html`<div class="imu-bar" aria-label="静止进度"><span style=${{ width: `${Math.round(W.progress * 100)}%` }}></span></div>`
            : html`<p class="imu-tilt num"><b>${fmt(W.live?.t)}°</b><span>离端平那一下</span></p>`}`
        : ph === 'solving' ? html`<p class="imu-say"><b>采完了，正在算…</b></p>`
          : html`<p class="imu-say"><b>还差：${names(W.missing)}</b><br />
            <span class="sv-small">做过的都留着。点「接着采」把${names(W.missing)}补上；不想要这一遍就「重来」。</span></p>`}
      </div>
    </div>` : null}
    ${ph === 'error' ? html`<div class="banner bad" role="alert"><b>没算成</b><span class="more">${W.error}</span></div>` : null}
    ${ph === 'done' && res && !res.ok ? html`<p class="sv-warn">没校成，原因和要补做哪步在下面「结果」里。</p>` : null}
    ${block && ph !== 'solving' ? html`<p class="sv-warn">${block}</p>`
      : ph === 'idle' && guardErr != null && guardErr > 5 ? html`<p class="sv-warn">腿离 HOME 还差 ${fmt(guardErr)}°：站姿不准，补偿也会跟着不准。先把舵机零位校好</p>` : null}
    <div class="imu-actions">${button}
      ${ph === 'short' || ph === 'collect' ? html`<button class="btn-light" onClick=${act.start} disabled=${!!block && ph !== 'collect'}>重来</button>` : null}</div>
  </section>`;
}

const GRADE = { good: ['好', 'ok'], ok: ['能用', 'warn'], coarse: ['粗校', 'warn'] };

function ResultCard({ res, imu, link, onRedo }) {
  const [checks, setChecks] = useState({});
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);
  useEffect(() => { setChecks({}); setOut(null); }, [res]);
  if (!res) return null;
  const P = res.parts || {}, Q = res.quality || {}, R = res.rules || {};
  const confirms = res.confirm || [];
  const allChecked = confirms.every((_, i) => checks[i]);
  const [gw, gk] = GRADE[Q.grade] || ['—', ''];
  const redo = (res.redo || []).filter((k) => STEP[k]);
  const save = () => {
    // The backend keeps this result pending; only the shared commit dialog writes it.
    setOut({ ok: true, text: '校准结果已记录，请预览后统一写进主程序。' });
    window.dispatchEvent(new Event('console:commit'));
  };
  const detail = html`<table class="imu-table num">
      <thead><tr><th>步骤</th><th class="r">倾了</th><th class="r">斜了</th><th class="r">晃</th><th></th></tr></thead>
      <tbody>${STEPS.map((k) => { const s = (res.steps || {})[k] || {}; return html`<tr class=${s.ok === false ? 'bad' : ''}
        title=${s.ref ? `零点：${s.ref}${s.ref_offset_deg != null ? `（离桌上静止 ${fmt(s.ref_offset_deg)}°）` : ''}` : ''}>
        <td>${STEP[k].cn}</td><td class="r">${k === 'still' ? '—' : `${fmt(s.tilt_deg, 0)}°`}</td>
        <td class="r">${s.skew_deg != null ? `${fmt(s.skew_deg, 0)}°` : '—'}</td><td class="r">${s.spread_deg != null ? `${fmt(s.spread_deg)}°` : '—'}</td>
        <td>${s.ok ? '✓' : s.msg || (s.ok === false ? '×' : '')}</td></tr>`; })}</tbody>
    </table>
    ${res.ok && Q.grade !== 'coarse' ? html`<p class="sv-small">自检：前后两次差 ${fmt(Q.fb_pair_deg)}°、左右两次差 ${fmt(Q.lr_pair_deg)}°、前后和左右差 ${fmt(Q.cross_deg)}°、
      最斜的一步 ${fmt(Q.max_skew_deg)}°（越小越好；每步斜 ≤${R.good_skew_deg ?? 10}°、两对差 ≤${R.good_cross_deg ?? 6}° 评「好」，
      斜 >${R.skew_max_deg ?? 20}° 或两对差 >${R.cross_max_deg ?? 12}° 判失败）</p>` : null}
    ${res.standard ? html`<p class="sv-small">新方向：${res.standard.text}${res.standard.off_deg > 0.5 ? `（再偏 ${fmt(res.standard.off_deg)}°）` : ''}</p>` : null}
    ${res.mount ? html`<details class="imu-details"><summary>新安装方向（四元数 w, x, y, z）</summary><code class="num">${res.mount.map((x) => fmt(x, 6)).join(', ')}</code></details>` : null}`;
  return html`<section class="board imu-card">
    <h3>结果 ${res.ok ? html`<span class=${'imu-grade ' + gk}>${gw}</span>` : html`<span class="imu-grade bad">没过</span>`}</h3>
    ${!res.ok ? html`<p class="sv-warn">${res.reason}</p>
      ${redo.length ? html`<div class="imu-actions"><button class="btn-dark" onClick=${() => onRedo(redo)}>补做${names(redo)}</button>
        <span class="sv-small">别的几步留着，只重做这${redo.length > 1 ? '几' : '一'}步</span></div>` : null}` : html`
      <div class="imu-parts num">
        <div><span>前后</span><b>${sgn(P.pitch_deg)}°</b><small>+ = 站着时读数前倾</small></div>
        <div><span>左右</span><b>${sgn(P.roll_deg)}°</b><small>+ = 左倾</small></div>
        <div><span>朝向</span><b>${sgn(P.yaw_deg)}°</b><small>绕竖直轴，站着看不出</small></div>
        <div><span>一共</span><b>${fmt(P.total_deg)}°</b><small>相对现在的方向</small></div>
      </div>`}
    ${res.check ? html`<p class=${res.check.ok ? 'sv-small imu-check' : 'sv-warn imu-check'}>${res.check.ok
      ? `✓ 跟主程序现在用的方向只差 ${fmt(res.check.diff_deg)}°、站姿倾斜 ${fmt(res.check.tilt_deg)}°：${res.check.recorded ? '记下了「核对过」（总览上手清单第 2 步）' : '够近，但没记上（看日志）'}`
      : `这次不算「核对过」：${res.check.why}`}</p>` : null}
    ${(res.notes || []).map((n) => html`<p class="sv-small">· ${n}</p>`)}
    ${res.ok ? html`<details class="imu-details"><summary>详细数据</summary>${detail}</details>` : detail}
    ${confirms.map((c, i) => html`<label class="imu-confirm"><input type="checkbox" checked=${!!checks[i]} onChange=${(e) => setChecks({ ...checks, [i]: e.target.checked })} /> ${c}</label>`)}
    ${res.ok ? html`<div class="imu-save">
      <button class="btn-dark" disabled=${!res.mount} onClick=${save}>去上手清单第 3 步</button>
      <span class="sv-small">这里只记下方向；第 3 步「写进主程序」才会重启主程序、切到运动模式</span>
    </div>` : null}
    ${!res.mount && res.ok ? html`<p class="sv-warn">算不出新方向（不知道现在的方向），不能写</p>` : null}
    ${out ? html`<div class=${'banner ' + (out.ok ? 'warn' : 'bad')} role="status"><b>${out.ok ? '写好了' : '没写成'}</b><span class="more">${out.text}</span></div>` : null}
  </section>`;
}

// 站着精校的结果先记在 Studio（imu-pending.json）；写进主程序统一在上手清单第 3 步做。

// ---------------- 方向没确认、站不起来：先粗校 ----------------

function CoarseCard({ st, imu, link, live }) {
  const [opened, setOpened] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const mode = st?.mode || imu?.mode;
  const redo = imu?.redo || {};
  const context = imuContext(mode, imu);
  useEffect(() => { setOpened(false); setMsg(null); }, [context]);
  const open = link.status === 'open' && live !== false;
  const commissioning = mode === 'commissioning';
  const verified = imu?.mount?.verified === true;
  const working = !!(imu?.job && !imu.job.done && !imu.job.failed);
  const canCollect = commissioning && !imu?.profile_error && (!redo.active || ['commissioning', 'finish_failed'].includes(redo.phase));
  const showSteps = canCollect && (!verified || opened || redo.active);
  const startWhy = !open ? '连不上鸭子：等它回来' : imu?.profile_error ? imu.profile_error : working ? '上一项操作还没结束'
    : commissioning ? null : !redo.can_start ? redo.reason || '暂时不能返回标定模式：先卸力并等待读数更新' : null;
  const run = async (op) => {
    setBusy(true); setMsg(null);
    const r = await link.cmd('imu.op', { op, confirmed: true }, 90000);
    setBusy(false);
    setMsg({ ok: r.ok, text: r.ok ? r.data?.note || (op === 'redo-start'
      ? '已回到标定模式，保持卸力，双手托住鸭子开始采集。' : '已取消，原来的安装方向和模式已恢复。')
      : r.reason || r.error || '操作没完成：查看当前状态后重试' });
  };
  const start = async () => {
    if (startWhy || busy) return;
    if (commissioning) { setMsg(null); setOpened(true); }
    else await run('redo-start');
  };
  return html`<section class="board imu-card imu-coarse">
    <h3>IMU 安装方向 <span class="sv-small">${redo.active ? '重标定未结束' : verified ? '已保存，可重新校验' : '第一次标定'}</span></h3>
    <p class="sv-small">先采静止和前倾，计算安装方向（四元数），再由主程序验证。装法改了或上次没调对，都可以重新做。</p>
    ${imu?.mount_protocol_mismatch ? html`<p class="sv-warn">总线协议与已存安装方向不匹配：需要重新标定；主程序验证通过后才确认这份方向。</p>` : null}
    ${redo.active ? html`<p class="sv-warn">${canCollect
      ? '本次重标定还没完成：双手托住鸭子，保持卸力。验证通过后会恢复原模式；也可以取消并恢复原参数。'
      : '正在切换或恢复标定会话，请等当前操作结束；如未能恢复，可以取消并恢复原参数。'}</p>
      <div class="imu-actions"><${TwoStep} label="取消并恢复原参数" ask="扶好鸭子，确认恢复原方向和模式？"
        disabled=${!open || busy || working}
        onConfirm=${() => run('redo-cancel')} busyLabel="正在恢复…" /></div>` : null}
    ${!showSteps && !redo.active ? html`<div class="imu-actions"><${TwoStep}
      label="重新标定方向" ask=${commissioning ? '扶好鸭子，确认重新采集？' : '扶好并已卸力，确认重启并进入标定模式？'}
      cls="btn-dark" disabled=${!!startWhy || busy} title=${startWhy} onConfirm=${start} busyLabel="正在进入标定模式…" /></div>
      <p class="sv-small">${commissioning
        ? '双手扶稳，可卸力托住，也可站姿使能后等站稳，再重新采集。'
        : '重新标定前先扶稳鸭子，点右上角「全局卸力」。进入和完成时会重启主程序，并备份原 IMU 方向。'}保留舵机零位、方向和限位；验证通过才采用新方向。</p>
      ${startWhy ? html`<p class="sv-warn">${startWhy}</p>` : null}` : null}
    ${showSteps ? html`<${CoarseSteps} key=${context} link=${link} disabled=${!open || busy || working} unloadedOnly=${!!redo.active}
      onBegin=${() => setOpened(true)} onDone=${(text) => setMsg({ ok: true, text })} />` : null}
    ${msg ? html`<div class=${'banner ' + (msg.ok ? 'warn' : 'bad')} role="status"><span>${msg.text}</span></div>` : null}
  </section>`;
}

function CoarseSteps({ link, disabled, unloadedOnly, onBegin, onDone }) {
  const [step, setStep] = useState('idle');       // idle / still / fwd / confirm / verifying / done / failed
  const [still, setStill] = useState(null);
  const [res, setRes] = useState(null);
  const [checks, setChecks] = useState({});
  const [out, setOut] = useState(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const grab = async (phase, seconds) => {
    if (disabled || busy) return null;
    onBegin();
    setBusy(true); setOut(null);
    const r = await link.cmd('imu.op', { op: 'coarse-capture', phase, seconds }, 15000);
    if (!alive.current) return null;
    setBusy(false);
    if (!r.ok) { setOut({ ok: false, text: r.reason || r.error || '没采到读数' }); return null; }
    return r.data || {};
  };
  const takeStill = async () => {
    const d = await grab('still', 3);
    if (!d) return;
    setStill(d); setStep('fwd');
    setOut({ ok: true, text: `静止采到 ${d.n} 个读数。现在往前低头 15–30°，停住再点下一步。` });
  };
  const takeFwd = async () => {
    const d = await grab('fwd', 3);
    if (!d) return;
    const samples = [...((still && still.samples) || []), ...(d.samples || [])];
    setBusy(true);
    const r = await link.cmd('imu.op', { op: 'solve', mode: 'coarse', frame: 'body', samples }, 20000);
    if (!alive.current) return;
    setBusy(false);
    if (!r.ok || !r.data || r.data.ok !== true) {
      setStep('failed');
      setOut({ ok: false, text: r.reason || (r.data && r.data.reason) || r.error || '算不出方向' });
      return;
    }
    setRes(r.data); setStep('confirm');
    setOut({ ok: true, text: '方向算出来了：对照模块实际装法勾一下，再交给主程序验证。' });
  };
  const reset = () => { setStep('idle'); setStill(null); setRes(null); setChecks({}); setOut(null); };
  const verify = async () => {
    if (disabled || busy || !res?.mount) return;
    setBusy(true); setStep('verifying'); setOut(null);
    const r = await link.cmd('imu.op', { op: 'verify', confirmed: true, mount_wxyz: res.mount }, 90000);
    if (!alive.current) return;
    if (r.ok) onDone(r.data?.note || '主程序验证通过，安装方向已经保存。');
    setBusy(false);
    if (r.ok) { setStep('done'); setOut({ ok: true, text: (r.data && r.data.note) || '主程序验证通过，安装方向已经写进标定文件' }); }
    else { setStep('failed'); setOut({ ok: false, text: r.reason || r.error || '主程序没通过验证' }); }
  };
  const confirms = (res && res.confirm) || [];
  const allChecked = confirms.every((_, i) => checks[i]);
  const nowStep = step === 'idle' ? 'still' : step;
  return html`<div class="imu-coarse-steps">
    <p class="sv-small">${unloadedOnly
      ? '本次重标定会话要恢复原来的运行模式：保持失能（卸力），双手托住鸭子。'
      : '粗校在标定模式做：可先点「站姿使能」，等站稳后双手扶住；也可失能（卸力）后双手托住。站姿使能只锁住关节，需要一直扶稳。'}
      先把整个鸭子摆正、别动，采 3 秒；再让整个躯干前倾 15–30° 停住，采 3 秒；电脑算出方向，再交给主程序自己验一遍。</p>
    ${nowStep === 'still' ? html`<div class="imu-coarse-step">
      <p class="imu-say"><b>① 扶住鸭子，摆正，别动</b></p>
      <p class="sv-small">双手扶住躯干，让实物站直、脚朝下，保持不动；已站姿使能的先等站稳。屏幕仍按旧方向显示，先以实物摆正为准。</p>
      <button class="btn-dark" disabled=${busy || disabled} onClick=${takeStill}>${busy ? '采集中…' : '采静止（3 秒）'}</button>
    </div>` : null}
    ${nowStep === 'fwd' ? html`<div class="imu-coarse-step">
      <p class="imu-say"><b>② 往前低头 15–30°，停住</b></p>
      <p class="sv-small">托住整个躯干往前倾，头那边往下，慢慢低到 15–30°，保持不动 3 秒。不要只转头，也不要往左右歪。</p>
      <div class="imu-actions">
        <button class="btn-dark" disabled=${busy || disabled} onClick=${takeFwd}>${busy ? '采集 / 计算中…' : '采前倾（3 秒）'}</button>
        <button class="btn-light" disabled=${busy || disabled} onClick=${takeStill}>重采静止</button>
      </div>
    </div>` : null}
    ${nowStep === 'confirm' && res ? html`<div class="imu-coarse-step">
      <p class="sv-small">粗校算出来：${res.standard ? `${res.standard.text}${res.standard.off_deg > 0.5 ? `（再偏 ${fmt(res.standard.off_deg)}°）` : ''}` : '方向已算出'}。
        这次只采静止和前倾，左右还没验；之后再做下面的「站着校准」精校一次。</p>
      ${(res.notes || []).map((n) => html`<p class="sv-small">· ${n}</p>`)}
      ${confirms.map((c, i) => html`<label class="imu-confirm"><input type="checkbox" checked=${!!checks[i]}
        onChange=${(e) => setChecks({ ...checks, [i]: e.target.checked })} /> ${c}</label>`)}
      <div class="imu-actions">
        <button class="btn-dark" disabled=${busy || disabled || !res.mount || !allChecked} onClick=${verify}>交给主程序验证</button>
        <button class="btn-light" disabled=${busy} onClick=${reset}>重做</button>
      </div>
      <p class="sv-small"><b>点击验证前，先把实物回正并停住。</b>点了以后，再按提示在 20 秒内往前倾 15–30° 并停住。</p>
    </div>` : null}
    ${nowStep === 'verifying' ? html`<div class="imu-coarse-step">
      <p class="imu-say"><b>按主程序提示，在 20 秒内往前倾 15–30°</b></p>
      <p class="sv-small">保持双手托住。主程序会先检查摆正，再检查前倾；验证成功后会保存，重标定会话还需等原模式恢复完毕。</p>
    </div>` : null}
    ${nowStep === 'done' ? html`<p class="sv-small">主程序验证通过，方向已经写进标定文件。下面「站着校准」还可以再精校一次。</p>` : null}
    ${nowStep === 'failed' ? html`<div class="imu-actions">
      <button class="btn-dark" onClick=${reset}>重做</button>
      <span class="sv-small">按下面的具体原因处理；重标定会话可以取消并恢复原参数。</span>
    </div>` : null}
    ${out ? html`<div class=${'banner ' + (out.ok ? 'warn' : 'bad')} role="status"><span>${out.text}</span></div>` : null}
  </div>`;
}

// ---------------- 后端在做的事（粗校、写入、换芯片）：一步一步报给页面 ----------------
// imu.job = { kind: 'coarse'|'save'|'set_chip'|'detect', title, msg, tilt_deg?, target_deg?, hold?, failed?, done? }
// 托着鸭子的那几步（摆正 ≤5°、低头 15–30°）要看倾角，所以 tilt_deg 放大写

function JobBanner({ job }) {
  if (!job) return null;
  const kind = job.failed ? 'bad' : 'warn';
  return html`<div class=${'banner imu-job ' + kind} role="status" aria-live="polite">
    <b>${job.title || '在做'}</b><span class="more">${job.msg || ''}</span>
    ${job.tilt_deg != null ? html`<span class="imu-job-tilt num">${fmt(job.tilt_deg)}°${job.target_deg ? html`<small> / ${job.target_deg}</small>` : null}</span>` : null}
    ${job.hold != null && !job.done ? html`<span class="imu-bar small"><span style=${{ width: `${Math.round(Math.min(1, job.hold) * 100)}%` }}></span></span>` : null}
  </div>`;
}

// 给测试用（不接浏览器也能喂一串读数，看采集怎么走）
export const _test = { RULES, STEPS, TILTS, feed, freshWizard, startCollect, endCollect, resumeCollect, redoSteps,
  missingSteps, payload, findHold, findRef, spreadP90, tiltOf, qRotate };

// ---------------- 页面 ----------------

/** live / age：跟总览同一个判断（app.js 的 Main 里算好传进来）；没传就只看 st.joints_age_ms。 */
export function ImuPage({ st, imu, link, live, age, diagnosticElapsed = 0, pd }) {
  const W = useRef(freshWizard());
  const [, bump] = useState(0);
  const redraw = () => bump((n) => n + 1);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [muted, setMutedState] = useState(loadMute);
  const mutedRef = useRef(muted);
  const setMuted = (v) => { mutedRef.current = v; setMutedState(v); saveMute(v); };
  const context = imuContext(st?.mode || imu?.mode, imu);

  useEffect(() => {
    W.current = freshWizard(); setRes(null); setMsg(null); redraw();
  }, [context]);

  useEffect(() => {
    if (!feed(W.current, st, live)) return;
    for (const e of W.current.events.splice(0)) if (!mutedRef.current) beep(e);
    redraw();
  }, [st, live]);

  // 采完了：交给后端算（旧方向后端自己从 calibration-status 拿，不用页面传）
  const phase = W.current.phase;
  useEffect(() => {
    if (phase !== 'solving') return undefined;
    let dead = false;
    const w = W.current;
    link.cmd('imu.op', { op: 'solve', frame: 'body', mode: 'full', samples: payload(w) }, 15000).then((r) => {
      if (dead || W.current !== w) return;
      if (r.ok && r.data) { w.phase = 'done'; w.result = r.data; setRes(r.data); }
      else { w.phase = 'error'; w.error = r.reason || r.error || '没算出来'; }
      redraw();
    });
    return () => { dead = true; };
  }, [phase, context]);

  const act = {
    start: () => { audioUnlock(); startCollect(W.current, performance.now() / 1000); setRes(null); redraw(); },
    end: () => { endCollect(W.current); redraw(); },
    resume: () => { audioUnlock(); resumeCollect(W.current); redraw(); },
  };
  const onRedo = (steps) => { audioUnlock(); redoSteps(W.current, steps); setRes(null); redraw(); };

  const run = async (args, what, timeout = 15000) => {
    setBusy(true); setMsg(null);
    const r = await link.cmd('imu.op', args, timeout);
    setBusy(false);
    setMsg(r.ok ? { ok: true, text: `${what}：${r.data?.note || '好了'}` } : { ok: false, text: `${what}没成：${r.reason || r.error || '没说原因'}` });
    return r;
  };
  const w = W.current;
  const F = freshText(st, live, age);
  const arrow = w.phase === 'collect' && w.done.still ? nextTodo(w) : null;
  const cue = w.phase === 'collect' ? (!w.done.still ? '静止 3 秒：别碰它' : arrow ? `下一个：${STEP[arrow].cn}（顺序随便）` : null) : null;
  return html`<div class="imu-page">
    <details class="console-imu-explanation"><summary>标定与采集说明</summary><p>IMU 告诉策略机身往哪边倾斜。安装方向验证、站姿精校与写入主程序分别完成；以采集质量和主程序回读结果为准。</p></details>
    <${JobBanner} job=${imu?.job} />
    ${msg ? html`<div class=${'banner ' + (msg.ok ? 'warn' : 'bad')} role="status"><span>${msg.text}</span>
      <a href="#" onClick=${(e) => { e.preventDefault(); setMsg(null); }}>知道了</a></div>` : null}
    <div class="imu-grid">
      <${ImuStage} st=${st} stale=${!F.live} corr=${res?.ok ? res.correction : null} parts=${res?.parts}
        collecting=${w.phase === 'collect'} arrow=${arrow} cue=${cue} />
      <div class="imu-side">
        <${StatusCard} st=${st} imu=${imu} live=${live} age=${age} diagnosticElapsed=${diagnosticElapsed} />
        <${DiagnosticCard} st=${st} imu=${imu} live=${live} diagnosticElapsed=${diagnosticElapsed} />
        <${CoarseCard} st=${st} imu=${imu} link=${link} live=${live} />
        <${StanceCard} st=${st} imu=${imu} pd=${pd} link=${link} live=${live} />
        <${CollectCard} st=${st} imu=${imu} live=${live} W=${w} act=${act} muted=${muted} setMuted=${setMuted} />
        <${ResultCard} res=${w.phase === 'done' ? res || w.result : null} imu=${imu} link=${link} onRedo=${onRedo} />
        <details class="board console-imu-chip" open=${!!imu?.profile_error || !!imu?.redo?.active}><summary>芯片配置与连接诊断</summary><${ChipCard} imu=${imu} link=${link} live=${live} diagnosticElapsed=${diagnosticElapsed} /></details>
      </div>
    </div>
  </div>`;
}
