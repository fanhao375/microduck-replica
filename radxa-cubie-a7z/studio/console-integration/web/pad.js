// 手柄区块（并进了总览，往下一刷就是：总览重排方案 §1.2）：看手柄现在怎么样、配新手柄（可视化向导）、测急停、
// 网页手柄（图上的键就能按）、网页遥控（站起来 / 开始走 / 坐下、摇杆、技能、动作）。#/pad 是总览上的锚点，打开就滚到这一块。
//
// 数据全来自后端 studio/pad.py 推的 {t:'pad', …}（这一块在屏幕上时 10 Hz，不在 1 Hz）。
// 页面发的指令统一是 link.cmd('pad.op', {op, …})：
//   watch                 我在看手柄这一块（在屏幕上连续 0.3 秒以后每 2 秒一次，后端 6 秒没收到就关掉 padd 旁路、放慢轮询）
//   refresh / scan {seconds} / pair {mac?} / cancel / forget {mac} / keys_done / wizard_reset   配对向导
//   estop_test {action}   测急停：start / cancel
//   drive {open, seq}     进了手柄区（只解开「离开了」，不拿方向盘）；drive {on:false, seq} 离开；drive {on:true, force} 接管
//                         「在手柄区」= 路由是 / 或 /pad ∧ 页面可见 ∧ 这一块在视口里（审查 O1，见下面的 padActivity）
//   sticks {mode,lx,ly,rx,ry,seq}  网页手柄的摇杆（图上的那两个）：-1..1，x 向右、y 向下为正；后端按 padd 的公式
//                         换算成 robot.move / head / pose；按住才发（10 Hz），seq 是页面的递增序号
//   stop {seq}            松手：走路模式发一次 0，头模式头回正，身体模式身体回中。比 stop 旧的帧后端一律丢掉
//   mode {mode, seq}      图上的 Y / B：drive / head / body
//   mode {mode, hb:true}  头、身体模式时每秒报一次「还在」（后端 3 秒没听到就退出）；只续不进：后端已经退出了就回 dropped
//   keys {held, down, seq}   按住的键 LT / RT / X：按下、松开时发，按住时每 0.15 秒报一次（后端 0.45 秒、X 0.3 秒没听到当松开）
//   key {key, src:'svg'}  图上的 A / X / LB / RB / 十字下按下那一下（按 pad.bindings 做）
//   start {expect, src:'svg'}   图上的 Start：后端挑的跟 expect 不一样就拒（状态刚变了）
//   reboot {src:'svg'}    图上的十字右：重启全部舵机（照官方：按一下就发、不用确认；主程序先全身卸力）
//   drive_mode {expect, src:'svg'}   图上的十字上按住 3 秒（计时在页面）：切走路 / 轮滑，expect 是要切到的
//   stand / walk / walk_stop / sit         站起来 / 开始走 / 停下（站好）/ 坐下（后端按姿势再判一遍）
//   skill {name} / sound {tag}             官方技能（robot.do）/ 叫一声（robot.sound）
//   action_start {id, press} / action_hold {press} / action_stop   动作和组合动作：按住才做（每 0.15 秒说一次「还按着」），松手就停；
//                         press 是这一次按下的序号，后端只认同一次按下的续命（代码审查 C7）
// 图上的 Select 走顶上卸力同一条路：link.cmd('relax')，按下就发、什么时候都能按（审查三 V3）。
// 按键作用照鸭子上 fork 的 padd（fork 固件 padd/src/main.rs），键名按手柄上印的字。
// 虚拟手柄：网页发整帧、实体手柄连着就让位，代码是自己写的。
import { html, useState, useEffect, useRef, useCallback } from './vendor/preact-htm.module.js';
import { Duck3D } from './duck3d.js';
import { InlineCamera } from './sensors.js';
import { MotionStudio } from './motion-studio.js';
import { AudioInteraction } from './audio-interaction.js';

const SEND_MS = 100;        // 摇杆按住时每秒发 10 次；松手发一次 0，鸭子 0.5 秒收不到也会自己停（robotd 的 deadman）
const WATCH_MS = 2000;
const HOLD_MS = 150;        // 动作 / 按住的键每 0.15 秒说一次「还按着」；后端 0.45 秒没听到就停
const MODE_HB_MS = 1000;    // 头 / 身体模式每秒报一次「还在」
const MODE_GRACE_MS = 800;  // 刚按了 Y / B：这么久以内以页面自己的为准（后端的快照还没跟上）
const DRIVE_HOLD_MS = 3000; // 十字上按住这么久才切走路 / 轮滑（官方 padd:247-252：走着走着容易靠上去，所以要按住）
const KEY_STALE_MS = 1000;  // 键盘按住时浏览器会一直重复发按下；超过 1 秒没收到当作松开（卡键：审查 12）
const BUSY_MS = 3000;       // 站 / 走 / 坐按下后按钮先变灰，直到姿势变了或 3 秒
const DZ = 0.1;             // 摇杆死区（跟 padd 一样，padd:184-187）：每个轴小于这个当 0
const STICK_TRAVEL = 40;    // 图上的摇杆：手指拖开这么多（图的坐标，图宽 400）算满杆
const WATCH_SETTLE_MS = 300;   // 手柄这一块连续在屏幕上这么久才发 watch（一划而过别开 padd 旁路：审查 O1）
const TAP_MS = 120;         // 触屏：按下这么久没动、也没被浏览器取消（开始滚页面），才算按下（审查 O2）
const TAP_MOVE_PX = 8;      // 触屏：按下以后动了这么多就当是在滚页面
const SCROLL_GUARD_MS = 500;   // 触屏：页面滚动后这么久以内的按下不认（惯性滑动里手指一碰就是这种：审查 O2）
const STICK_GRAB = 30;      // 图上的摇杆：触屏起手区（半径，图的坐标；帽 17）。放大到 30 也不碰旁边的键 / 十字键
let SEQ = 0;
const nextSeq = () => ++SEQ;

// ---------------- 「在手柄区」：旧总览 /pad 或独立 /remote/* ∧ 页面可见 ∧ 控制块在视口里 ----------------
// 手柄并进总览以后这一块一直挂着（总览只是藏起来、不卸载），以前挂在「挂上 / 卸载」上的清理（全部松开、drive off、
// HoldButton 停、模式心跳）永远不会跑。所以这些都挂在 active 上：由真变假的那一刻全部松开、发 drive {on:false}、
// 马上发一次心跳（pad_visible:false）；由假变真发 drive {open}。路由一变，app.js 在 hashchange 里同步先调 setRoute，
// 不等 IntersectionObserver（藏起来以后 IO 晚一帧才报「不相交」，安全不能押在它上面）。
const ACT = { route: location.hash.replace(/^#/, '') || '/', visible: document.visibilityState === 'visible',
  inView: false, active: false, standalone: false, subs: new Set(), link: null };
function actRecompute(why, routeChanged = false) {
  const legacyRoute = !ACT.standalone && (ACT.route === '/' || ACT.route === '/pad');
  const remoteRoute = legacyRoute || /^\/remote\/(control|recording|gamepad)$/.test(ACT.route);
  const a = remoteRoute && ACT.visible && ACT.inView;
  const changed = a !== ACT.active;
  ACT.active = a;
  for (const fn of ACT.subs) {
    try { fn({ active: a, inView: ACT.inView, changed, routeChanged }, why); } catch (e) { console.error('手柄区的订阅出错', e); }
  }
  if (changed && ACT.link) ACT.link.heartbeat();   // 后端马上知道：10 Hz ↔ 1 Hz（app.py sender 只看 pad_visible）
}
export const padActivity = {
  get active() { return ACT.active; },
  get inView() { return ACT.inView; },
  bind(link) { ACT.link = link; },
  useStandaloneRoutes() { if (!ACT.standalone) { ACT.standalone = true; actRecompute('启用独立遥控页'); } },
  setRoute(route) { const next = route || '/'; const changed = next !== ACT.route; ACT.route = next; actRecompute(`切换页面（#${ACT.route}）`, changed); },
  setInView(v) { if (v !== ACT.inView) { ACT.inView = v; actRecompute('滚出了手柄这一块'); } },
  subscribe(fn) { ACT.subs.add(fn); return () => ACT.subs.delete(fn); },
};
document.addEventListener('visibilitychange', () => { ACT.visible = document.visibilityState === 'visible'; actRecompute('页面转到后台'); });
window.__pad = padActivity;          // 排查用（e2e 也看它）：现在算不算「在手柄区」

// 上手清单的「去做」要直接到位（审查 O17）：配对向导、测急停、配过的手柄、遥控在这一块里面，清单在外面，靠这个小事件
const GO = new Set();
export function padGo(what) { for (const fn of GO) fn(what); }

// 触屏防误触（审查 O2）：总览是往下刷的首页，刷的路上全是一碰就生效的控件
let lastScrollAt = -1e9;
window.addEventListener('scroll', () => { lastScrollAt = performance.now(); }, { passive: true, capture: true });
const scrolledRecently = () => performance.now() - lastScrollAt < SCROLL_GUARD_MS;
const isTouch = (e) => e.pointerType === 'touch' || e.pointerType === 'pen';
// 触屏上的一次按下：TAP_MS 内没动（< TAP_MOVE_PX）、没被浏览器取消才算按下（press），之后松手 / 被取消 = release；
// TAP_MS 以内就松手 = 点一下（tap，没给就什么都不做）；动了、被取消 = 什么都不做（是在滚页面）。
// 不 preventDefault：浏览器照样能拿这一下去滚页面（它一滚就发 pointercancel）。回一个「作废」函数
function touchHold(e, { press, release, tap }) {
  const pid = e.pointerId, x0 = e.clientX, y0 = e.clientY;
  let phase = 'pending';
  const off = () => {
    clearTimeout(timer);
    window.removeEventListener('pointermove', mv, true);
    window.removeEventListener('pointerup', up, true);
    window.removeEventListener('pointercancel', cc, true);
  };
  const mv = (ev) => {
    if (ev.pointerId !== pid || phase !== 'pending') return;
    if (Math.hypot(ev.clientX - x0, ev.clientY - y0) >= TAP_MOVE_PX) { phase = 'gone'; off(); }
  };
  const up = (ev) => {
    if (ev.pointerId !== pid) return;
    const was = phase; phase = 'gone'; off();
    if (was === 'pending' && tap) tap(); else if (was === 'held' && release) release();
  };
  const cc = (ev) => {
    if (ev.pointerId !== pid) return;
    const was = phase; phase = 'gone'; off();
    if (was === 'held' && release) release();
  };
  const timer = setTimeout(() => { if (phase !== 'pending') return; phase = 'held'; window.removeEventListener('pointermove', mv, true); press(); }, TAP_MS);
  window.addEventListener('pointermove', mv, true);
  window.addEventListener('pointerup', up, true);
  window.addEventListener('pointercancel', cc, true);
  return () => { if (phase === 'held' && release) release(); phase = 'gone'; off(); };
}

// ---------------- 各型号：键名、外形、怎么进配对状态 ----------------

const KINDS = {
  xbox: {
    label: 'Xbox 手柄', layout: 'xbox',
    names: { south: 'A', east: 'B', west: 'X', north: 'Y', lb: 'LB', rb: 'RB', lt: 'LT', rt: 'RT', select: '视图', start: '菜单', home: 'Xbox', l3: '左摇杆按下', r3: '右摇杆按下' },
    how: ['短按 Xbox 键开机（别按住：按住是关机）。', '再按顶上 USB-C 口旁边的小配对键，直到 Xbox 灯「快闪」。慢闪是开着、但没在配对。', 'Xbox 手柄只记一个主机：配过电脑或手机的，配不上时先去那边删掉它。'],
  },
  ps: {
    label: 'PS5 / PS4 手柄', layout: 'ps',
    names: { south: '×', east: '○', west: '□', north: '△', lb: 'L1', rb: 'R1', lt: 'L2', rt: 'R2', select: 'Create', start: 'Options', home: 'PS', l3: 'L3', r3: 'R3' },
    how: ['同时按住 Create 和 PS 键，直到灯条快闪。', 'PS 手柄官方没在鸭子上实测过，配上以后先在下面把每个键按一遍看对不对。'],
  },
  switch: {
    label: 'Switch Pro 类（含 G50 Pro）', layout: 'switch',
    names: { south: 'B', east: 'A', west: 'Y', north: 'X', lb: 'L', rb: 'R', lt: 'ZL', rt: 'ZR', select: '−', start: '+', home: 'Home', l3: '左摇杆按下', r3: '右摇杆按下' },
    how: ['G50 Pro：先把背后的模式拨到 S 档（Switch 模式）。', '按住顶上 USB-C 口旁边的小 Sync 键，直到几个小灯来回跑。'],
  },
  generic: {
    label: '其他手柄', layout: 'xbox',
    names: { south: '下', east: '右', west: '左', north: '上', lb: 'L1', rb: 'R1', lt: 'L2', rt: 'R2', select: 'Select', start: 'Start', home: 'Home', l3: 'L3', r3: 'R3' },
    how: ['照说明书进配对状态（灯一般会快闪）。', '有模式开关的（安卓 / iOS / Switch / X-input），先选 Switch 或 X-input：安卓模式常常认不准键。'],
  },
};
const kindOf = (k) => KINDS[k] || KINDS.generic;

// 按键作用：照鸭子上 fork 的 padd（审查三 附 1）。role 是位置：south = 右边四个键最下面那个。
// does = 实体手柄；web = 网页手柄图上按的跟实体不一样的地方（没写就是一样）；off = 网页图上这颗键灰着的原因
const ROLLER_NOTE = '（轮滑模式另有作用）';
const FUNCS = [
  { role: 'ls', group: 'main', name: () => '左摇杆', does: '走路：前后、左右平移 · 头模式：头转向、抬低头 · 身体模式：起伏下蹲',
    web: '网页上：以按下的那一点为中心拖（不是按哪儿就到哪儿），松手回中' },
  { role: 'rs', group: 'main', name: () => '右摇杆', does: '走路：转弯 · 头模式：脖子俯仰、头歪 · 身体模式：前后左右倾' },
  { role: 'start', group: 'main', does: '软着 / 坐着：上力、直线回站姿；站着保持：开始走（策略开）；在走：停下回站姿（策略关）',
    web: '网页上：按现在的姿势挑一样做（图下面写着现在按会做什么）；坐着时走「站起来」（sitstand 起身，更柔和——实体手柄是直线回站姿）；在走时是先停再回站姿（不渐变）' },
  { role: 'select', group: 'main', does: '短按（松手时）：卸力 = 急停，鸭子会软下来，扶住；再按 Start 站回来', danger: '按住 2 秒：坐下、卸力、关机',
    web: '网页上：按下就卸力（跟顶上「卸力」同一条路，什么时候都能按）；网页上不做关机' },
  { role: 'dd', group: 'main', name: () => '十字下', does: '坐下 ↔ 站起', bind: 'dpad_down', web: '网页上：按坐没坐着走「坐下」或「站起来」' },
  { role: 'south', group: 'skill', does: '低头捡（ground pick）', bind: 'a' },
  { role: 'west', group: 'skill', does: '前滚翻；按住连着滚', bind: 'x' },
  { role: 'lb', group: 'skill', does: '左脚踢', bind: 'lb' },
  { role: 'rb', group: 'skill', does: '右脚踢', bind: 'rb' },
  { role: 'rt', group: 'skill', does: '张嘴，同时嘎一声（站好了就能动嘴，不用在走）' },
  { role: 'lt', group: 'skill', does: '张嘴；按住时一直「wheee」' },
  { role: 'north', group: 'mode', does: '头模式：摇杆改成管头，身体不动；再按回到走路', imu: true },
  { role: 'east', group: 'mode', does: '身体模式：摇杆让站着的身体倾斜、下蹲；再按回到走路' },
  { role: 'dr', group: 'other', name: () => '十字右', does: '重启全部舵机（过载保护跳了用）：先全身卸力再重启，之后按 Start 站回来',
    web: '网页上：一样，按一下就重启、不用确认（照官方）。鸭子会先软下来，扶住' },
  { role: 'du', group: 'other', name: () => '十字上', does: '按住 3 秒：切换走路 / 轮滑（装了轮子才用）。叫一声是走路、两声是轮滑；先回站姿再换',
    web: '网页上：也要按住 3 秒，没到 3 秒松手不切；没装轮滑模型主程序会拒' },
  { role: 'dl', group: 'other', name: () => '十字左', does: '走路模式没作用（轮滑模式：按住 2 秒切换 A 键是蹲还是转）', off: `走路模式没作用${ROLLER_NOTE}` },
  { role: 'home', group: 'other', does: '鸭子上没有作用（手柄自己的开关机键）', off: '没有作用' },
  { role: 'l3', group: 'other', does: '走路模式没作用（轮滑模式：倒地时轮滑起身测试）', off: `走路模式没作用${ROLLER_NOTE}` },
  { role: 'r3', group: 'other', does: '没有作用', off: '没有作用' },
];
const FUNC = Object.fromEntries(FUNCS.map((f) => [f.role, f]));
// 图上按位置的键 → 后端的名字
const ROLE_BIND = { south: 'a', west: 'x', lb: 'lb', rb: 'rb', dd: 'dpad_down' };
const ROLE_HELD = { lt: 'lt', rt: 'rt', west: 'x' };
const MODE_CN = { drive: '走路', head: '头模式', body: '身体模式' };
const DRIVE_CN = { walk: '走路', roller: '轮滑' };
const SKILL_CN = { ground_pick: '低头捡', roulade: '前滚翻', kick_left: '左脚踢', kick_right: '右脚踢', sit_toggle: '坐下 ↔ 站起', roller_stand: '轮滑起身' };
// 官方默认的按键绑定（fork robotd-params lib.rs:175-215，后端 pad_drive.DEFAULT_BIND 同一份）；robotd 的 pad.bindings 改过的以它为准
const DEFAULT_BIND = { a: 'ground_pick', x: 'roulade', lb: 'kick_left', rb: 'kick_right', dpad_down: 'sit_toggle' };
const BIND_ROLE = { a: 'south', x: 'west', lb: 'lb', rb: 'rb', dpad_down: 'dd' };

const STEP_ICON = { ok: '✓', fail: '×', bad: '×', doing: '', todo: '', skip: '–', unk: '?' };
const fmtMs = (ms) => (ms == null ? '—' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} 秒`);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const cx = (...xs) => xs.filter(Boolean).join(' ');

// ---------------- 手柄示意图（自己画的） ----------------

// Start / Select 在图上拉开一点（审查三 V3：挨着的话点 Start 容易点成卸力），点击区比画出来的大（pd-hit）
const LAYOUT = {
  xbox: { ls: [120, 100], dpad: [158, 152], face: [282, 100], rs: [243, 152], select: [172, 98], start: [228, 98], home: [200, 70] },
  switch: { ls: [120, 100], dpad: [158, 152], face: [282, 100], rs: [243, 152], select: [170, 82], start: [230, 82], home: [214, 112] },
  ps: { ls: [158, 156], dpad: [118, 100], face: [282, 100], rs: [242, 156], select: [150, 64], start: [250, 64], home: [200, 118], touch: true },
};
// 图上每个键外侧的小字：[x, y, text-anchor]。三种外形分开排，避免字压到键或别的字（十字上「按住 3 秒·轮滑」不写：只有装轮子才用、又挤，按键表里有）；
// 手机宽度只留 Start / Select（CSS 隐藏其余），这两个也在这里定位。
const NOTES = {
  xbox: { select: [170, 119], start: [236, 119], x: [253, 85], dr: [185, 137, 'start'], dd: [158, 190] },
  switch: { select: [170, 64], start: [230, 64], x: [256, 85], dr: [185, 137, 'start'], dd: [158, 190] },
  ps: { select: [135, 68, 'end'], start: [250, 52], x: [250, 85], dr: [147, 104, 'start'], dd: [104, 134] },
};
const HIT_W = 34, HIT_H = 28;      // Start / Select 的点击区（图的坐标）
const BODY = 'M128 44C96 44 70 52 56 74C40 100 26 150 22 190C19 222 38 240 60 240C80 240 92 226 104 206C114 190 124 180 144 180L256 180C276 180 286 190 296 206C308 226 320 240 340 240C362 240 381 222 378 190C374 150 360 100 344 74C330 52 304 44 272 44C240 44 226 52 200 52C174 52 160 44 128 44Z';
// 手柄顶上每边两颗：前面一排是肩键（LB / RB，贴着外壳上沿），后面一排是扳机（LT / RT，更高、更靠外），中间留空隙
const SHOULDER = { l: { x: 80, w: 72 }, r: { x: 248, w: 72 } };            // y 26–50，下半截被外壳盖住
const TRIGGER = { l: { x: 62, w: 62 }, r: { x: 276, w: 62 } };             // y -10–18
const SH_Y = 26, SH_H = 24, TR_Y = -10, TR_H = 28;

// ctl：网页手柄控制器（useVpad 给的）。有它，图上的键就能按（跟实体手柄干一样的活）；没有就只是示意图。
// 镜像（实体手柄连着）时 live 是实体手柄的按键，图只跟着它显示；不镜像时 live 是网页上按着的。
function GamepadSvg({ kind, live, seen, ctl, keys }) {
  const k = kindOf(kind);
  const L = LAYOUT[k.layout] || LAYOUT.xbox;
  const b = live?.buttons || {};
  const ax = live?.axes || {};
  const on = (r) => !!b[r];
  const was = (r) => !!(seen && seen.includes(r));
  const N = NOTES[k.layout] || NOTES.xbox;
  // 能改绑的键：以后端 motion.keys 推来的 does 为准（A / X / LB / RB / 十字下）；没有就按官方默认
  const does = (role) => {
    const row = keys?.[ROLE_BIND[role]];
    if (row && typeof row.does === 'string' && row.does) return row.does;
    const skill = DEFAULT_BIND[ROLE_BIND[role]];
    return SKILL_CN[skill] || skill || '';
  };
  const noteAt = (text, [x, y, anchor = 'middle'], extra) => html`<text class=${cx('pd-key-note', extra)} x=${x} y=${y} text-anchor=${anchor} aria-hidden="true">${text}</text>`;
  // 能按的键：role=button + aria-label（键名 + 作用），按下 / 松开交给 ctl；不能按的加 pd-key-off，<title> 写原因（鼠标放上去就看到）
  const act = (r) => {
    if (!ctl) return { info: null, props: {} };
    const info = ctl.info(r);
    const f = FUNC[r];
    const name = f?.name ? f.name() : k.names[r] || r;
    const say = `${name}：${info.ok ? info.does : info.why}`;
    return { info, say, props: {
      role: 'button', 'aria-label': say, 'aria-disabled': info.ok ? 'false' : 'true', 'data-role': r,
      onPointerDown: (e) => ctl.down(r, e), onPointerUp: (e) => ctl.up(r, e), onPointerCancel: (e) => ctl.up(r, e),
      onLostPointerCapture: (e) => ctl.up(r, e), onContextMenu: (e) => e.preventDefault(),
    } };
  };
  const tip = (a) => (a.info ? html`<title>${a.say}</title>` : null);
  // 镜像时图在显示实体手柄：不画成灰的（只是网页上按了不收，title 里写着）
  const offc = (a) => a.info && !a.info.ok && !ctl.mirror && 'pd-key-off';
  const cls = (r, a, extra) => cx('pd-key', on(r) && 'pd-on', was(r) && 'pd-seen', r === 'select' && 'pd-estop',
    a.info && 'pd-press', offc(a), extra);
  const scale = (e) => (e.currentTarget.ownerSVGElement || e.currentTarget).getBoundingClientRect().width / 400;
  const stick = (r, [x0, y0], x, y, press) => {
    const t = 12;
    const a = act(r);
    const side = r === 'ls' ? 'l' : 'r';
    // 图上的摇杆底座：鼠标从哪儿按都行；触屏只认中间圆帽上那块透明的 HTML（pd-ov，下面），底座上起手的是在滚页面（审查 O2）
    const sp = ctl ? { role: 'button', 'aria-label': a.say, 'data-role': r, onContextMenu: (e) => e.preventDefault(),
      onPointerDown: (e) => ctl.stickDown(side, e, scale(e) * STICK_TRAVEL, false), onPointerMove: (e) => ctl.stickMove(side, e),
      onPointerUp: (e) => ctl.stickUp(side, e), onPointerCancel: (e) => ctl.stickUp(side, e),
      onLostPointerCapture: (e) => ctl.stickUp(side, e) } : {};
    return html`<g class=${cx('pd-stick', on(press) && 'pd-on', was(r) && 'pd-seen', a.info && 'pd-press', offc(a))} ...${sp}>
      <circle class="pd-stick-base" cx=${x0} cy=${y0} r="27"></circle>
      <circle class="pd-stick-cap" cx=${x0 + clamp(x || 0, -1, 1) * t} cy=${y0 + clamp(y || 0, -1, 1) * t} r="17"></circle>
      <text x=${x0} y=${y0 + 44} class="pd-cap">${r === 'ls' ? 'L' : 'R'}</text>
      ${ctl ? html`<circle class="pd-hit" cx=${x0} cy=${y0} r="34"></circle>` : null}${tip(a)}
    </g>`;
  };
  const face = ([x0, y0]) => [['north', 0, -23], ['south', 0, 23], ['west', -23, 0], ['east', 23, 0]].map(([r, dx, dy]) => {
    const a = act(r);
    return html`<g class=${cls(r, a)} ...${a.props}>
      <circle cx=${x0 + dx} cy=${y0 + dy} r="11.5"></circle>
      <text x=${x0 + dx} y=${y0 + dy + 4}>${k.names[r]}</text>${tip(a)}</g>`;
  });
  const dkey = (r, x, y, w, h) => {
    const a = act(r);
    return html`<g class=${cls(r, a)} ...${a.props}><rect x=${x} y=${y} width=${w} height=${h} rx="3"></rect>${tip(a)}</g>`;
  };
  const dpad = ([x0, y0]) => html`<g>
    ${dkey('du', x0 - 7, y0 - 25, 14, 18)}${dkey('dd', x0 - 7, y0 + 7, 14, 18)}
    ${dkey('dl', x0 - 25, y0 - 7, 18, 14)}${dkey('dr', x0 + 7, y0 - 7, 18, 14)}
    <rect class="pd-dpad-mid" x=${x0 - 7} y=${y0 - 7} width="14" height="14"></rect></g>`;
  // 扳机：按多深就从下往上填多深；数字扳机（Switch 的 ZL / ZR）按下就是满的
  const trig = (r, side) => {
    const { x, w } = TRIGGER[side];
    const v = clamp(ax[r] ?? (on(r) ? 1 : 0), 0, 1);
    const pressed = on(r) || v > 0.5;
    const a = act(r);
    // 字：没按满时是黑字带白边（一半黑一半白的底上都看得清），按满了换白字
    return html`<g class=${cx('pd-key', 'pd-trig', pressed && 'pd-on', was(r) && 'pd-seen', v > 0.95 && 'pd-trig-hi', a.info && 'pd-press', offc(a))} ...${a.props}>
      <rect x=${x} y=${TR_Y} width=${w} height=${TR_H} rx="9"></rect>
      ${v > 0.02 ? html`<rect class="pd-fill" x=${x} y=${TR_Y + TR_H * (1 - v)} width=${w} height=${TR_H * v} rx=${v > 0.3 ? 9 : 3}></rect>` : null}
      <text x=${x + w / 2} y=${TR_Y + 18}>${k.names[r]}</text>${tip(a)}</g>`;
  };
  const shoulder = (r, side) => {
    const { x, w } = SHOULDER[side];
    const a = act(r);
    return html`<g class=${cls(r, a, 'pd-shoulder')} ...${a.props}>
      <rect x=${x} y=${SH_Y} width=${w} height=${SH_H} rx="10"></rect>
      <text x=${x + w / 2} y=${SH_Y + 12.5}>${k.names[r]}</text>${tip(a)}</g>`;
  };
  const small = (r, [x0, y0], text) => {
    const a = act(r);
    return html`<g class=${cls(r, a)} ...${a.props}><rect x=${x0 - 13} y=${y0 - 7} width="26" height="14" rx="7"></rect>
      <text x=${x0} y=${y0 + 3.5} class="pd-small">${text}</text>
      ${ctl ? html`<rect class="pd-hit" x=${x0 - HIT_W / 2} y=${y0 - HIT_H / 2} width=${HIT_W} height=${HIT_H} rx="8"></rect>` : null}${tip(a)}</g>`;
  };
  const home = () => {
    const a = act('home');
    return html`<g class=${cls('home', a)} ...${a.props}><circle cx=${L.home[0]} cy=${L.home[1]} r="10"></circle>${tip(a)}</g>`;
  };
  const mode = ctl?.mode && ctl.mode !== 'drive' ? MODE_CN[ctl.mode] : null;
  // 盖在图上的透明 HTML 块（审查 O2）：整张图在手机上能用来滚页面（touch-action: pan-y），只有两个摇杆的圆帽和 Select
  // 的点击区不让滚（touch-action: none）。SVG 子元素上的 touch-action 各浏览器认不认没核实，所以用 HTML 块接触摸。
  // 位置按图的坐标（viewBox 0 -16 400 266）换成百分比；鼠标点到这里也一样管用
  const pct = (x, y, w, h) => ({ left: `${(x / 400) * 100}%`, top: `${((y + 16) / 266) * 100}%`, width: `${(w / 400) * 100}%`, height: `${(h / 266) * 100}%` });
  const travel = (e) => (e.currentTarget.parentElement.getBoundingClientRect().width / 400) * STICK_TRAVEL;
  const ovStick = (r, [x0, y0]) => {
    const side = r === 'ls' ? 'l' : 'r';
    return html`<span class="pd-ov pd-ov-stick" data-ov=${r} aria-hidden="true" style=${pct(x0 - STICK_GRAB, y0 - STICK_GRAB, STICK_GRAB * 2, STICK_GRAB * 2)}
      onContextMenu=${(e) => e.preventDefault()}
      onPointerDown=${(e) => ctl.stickDown(side, e, travel(e), true)} onPointerMove=${(e) => ctl.stickMove(side, e)}
      onPointerUp=${(e) => ctl.stickUp(side, e)} onPointerCancel=${(e) => ctl.stickUp(side, e)}
      onLostPointerCapture=${(e) => ctl.stickUp(side, e)}></span>`;
  };
  const [sx, sy] = L.select;
  return html`<div class="pd-svgbox">
    <svg class=${cx('pd-svg', ctl && 'pd-svg-live')} viewBox="0 -16 400 266" role=${ctl ? 'group' : 'img'}
    aria-label=${ctl ? `${k.label}：图上的键能按，跟实体手柄一样` : `${k.label}示意图，按下的键会变黑`} onContextMenu=${ctl ? (e) => e.preventDefault() : undefined}>
    ${trig('lt', 'l')}${trig('rt', 'r')}
    ${shoulder('lb', 'l')}${shoulder('rb', 'r')}
    <path class="pd-body" d=${BODY}></path>
    ${L.touch ? html`<rect class="pd-touch" x="168" y="54" width="64" height="40" rx="8"></rect>` : null}
    ${mode ? html`<g class="pd-modetag"><rect x="164" y="22" width="72" height="20" rx="10"></rect><text x="200" y="36">${mode}</text></g>` : null}
    ${stick('ls', L.ls, ax.lx, ax.ly, 'l3')}
    ${stick('rs', L.rs, ax.rx, ax.ry, 'r3')}
    ${dpad(L.dpad)}
    ${face(L.face)}
    ${small('select', L.select, k.names.select.length > 2 ? '' : k.names.select)}
    ${small('start', L.start, k.names.start.length > 2 ? '' : k.names.start)}
    ${home()}
    ${noteAt('张嘴', [56, 7, 'end'])}
    ${noteAt('张嘴·嘎', [344, 7, 'start'])}
    ${noteAt(does('lb'), [74, 41, 'end'])}
    ${noteAt(does('rb'), [326, 41, 'start'])}
    ${noteAt('头模式', [282, 60])}
    ${noteAt('身体模式', [321, 104, 'start'])}
    ${noteAt(does('south'), [321, 129, 'start'])}
    ${noteAt(does('west'), N.x)}
    ${noteAt(`站立 / 模型接管`, N.start, 'pd-key-note-main')}
    ${noteAt('卸力（急停）', N.select, 'pd-key-note-main pd-note-danger')}
    ${noteAt('重启舵机', N.dr)}
    ${noteAt(does('dd'), N.dd)}
    </svg>
    ${ctl ? html`${ovStick('ls', L.ls)}${ovStick('rs', L.rs)}
      <span class="pd-ov pd-ov-select" data-ov="select" aria-hidden="true" style=${pct(sx - HIT_W / 2, sy - HIT_H / 2, HIT_W, HIT_H)}
        onContextMenu=${(e) => e.preventDefault()} onPointerDown=${(e) => ctl.down('select', e)}
        onPointerUp=${(e) => ctl.up('select', e)} onPointerCancel=${(e) => ctl.up('select', e)}></span>` : null}
  </div>`;
}

// ---------------- 按键表（按这款手柄印的字） ----------------

function KeyName({ f, k }) {
  const n = f.name ? f.name() : k.names[f.role];
  return html`<span class="pd-chip">${n}</span>`;
}

function Legend({ kind, live, bindings, imuHead }) {
  const k = kindOf(kind);
  const b = live?.buttons || {};
  const ax = live?.axes || {};
  const moved = (r) => (r === 'ls' ? Math.hypot(ax.lx || 0, ax.ly || 0) > 0.25 : r === 'rs' ? Math.hypot(ax.rx || 0, ax.ry || 0) > 0.25
    : r === 'lt' || r === 'rt' ? (ax[r] || 0) > 0.3 : false);
  const bound = (f) => {
    const x = f.bind && (bindings || []).find((y) => y.button === f.bind);
    if (!x || !x.overridden) return null;
    return x.skill ? `改成了：${SKILL_CN[x.skill] || x.skill}${x.error ? `（${x.error}）` : ''}` : '这个键被关掉了';
  };
  // Y：鸭子上 robotd.toml 开了 [pad_imu_head_control]、手柄又有陀螺仪时，实体手柄的 Y 是「头跟着手柄的姿态走」（fork padd:1044-1093）。
  // 浏览器没有手柄的姿态：网页上的 Y 一直是「摇杆管头」（审查三 V18）
  const does = (f) => {
    if (f.imu && imuHead) return '实体手柄：头跟着手柄的姿态走（手柄要有陀螺仪），摇杆照常走路；再按一下头停在那里';
    return bound(f) || f.does;
  };
  const web = (f) => (f.imu ? (imuHead ? '网页上：摇杆管头（浏览器拿不到手柄的姿态）' : imuHead == null ? '网页上：摇杆管头（实体手柄开没开「陀螺仪管头」没读到）' : null)
    : f.off ? `网页上：灰着（${f.off}）` : f.web);
  const row = (f) => html`<div class=${cx('pd-row', (b[f.role] || moved(f.role)) && 'pd-on')}>
    <${KeyName} f=${f} k=${k} />
    <span class="pd-does">${does(f)}${f.danger ? html`<br /><b class="pd-danger">${f.danger}</b>` : null}
      ${web(f) ? html`<small class="pd-web">${web(f)}</small>` : null}</span>
  </div>`;
  const group = (g) => FUNCS.filter((f) => f.group === g).map(row);
  return html`<div class="pd-legend">
    <div class="pd-legend-h">常用</div>${group('main')}
    <div class="pd-legend-h">技能</div>${group('skill')}
    <div class="pd-legend-h">切换摇杆管什么</div>${group('mode')}
    <details class="pd-other"><summary>其他按键</summary>${group('other')}</details>
    ${k.layout === 'switch' ? html`<p class="pd-note">Switch 类手柄 A 和 B 的位置跟 Xbox 是反的。表里按位置算：右边四个键「最下面那颗」干「A」的活，手柄上印的是 ${k.names.south}。上鸭子还要再核对一次。</p>` : null}
    ${live?.device?.keys_unverified ? html`<p class="pd-note">Xbox 手柄 X、Y 两颗键的位置是推测的，还没在鸭子上核对：按 X 时图上亮的如果是 Y，告诉我们。</p>` : null}
    ${live?.other?.length ? html`<p class="pd-note">还按着表里没有的键：${live.other.join('、')}（padd 不一定认；多半是这款手柄的额外键）</p>` : null}
  </div>`;
}

// ---------------- 页头：型号 · 已连 · 急停有效 ----------------

function Summary({ pad }) {
  const s = pad?.status || {};
  const live = pad?.live || {};
  const act = s.active;
  const es = pad?.estop || {};
  const name = act ? act.name || '手柄' : live.state === 'attached' ? live.device?.name || '手柄' : null;
  const mark = es.state === 'ok' ? html`<span class="pd-ok">有效 ✓</span>`
    : es.state === 'unknown' ? html`<span class="pd-unk">没核对完 ?</span>` : html`<span class="pd-bad">无效 ×</span>`;
  // 写「手柄急停」，跟顶栏的「卸力」分开（审查 O7）；跟仪表带的手柄格、上手清单第 3、4 步用同一份 status / estop
  if (!s.known && live.state !== 'attached') return html`<span class="pd-sum">${s.error ? '读不到手柄状态' : '正在读手柄状态…'}${' · 手柄急停'}${mark}</span>`;
  const tail = name ? ' · 已连' : (s.pads || []).length ? ` · 配过 ${(s.pads || []).length} 个，都没连着` : '';
  return html`<span class="pd-sum"><b>${name || '没连手柄'}</b>${tail}${' · 手柄急停'}${mark}</span>`;
}

function EStopChecks({ pad }) {
  const checks = pad?.estop?.checks || [];
  return html`<ul class="pd-checks">${checks.map((c) => {
    const s = c.ok === true ? 'ok' : c.ok === false ? 'bad' : 'unk';
    return html`<li class=${'pd-s-' + s}>
      <span class="pd-mark">${STEP_ICON[s]}</span>
      <span>${c.label}${c.why ? html`<small>${c.why}</small>` : null}</span></li>`;
  })}</ul>`;
}

// ---------------- 测急停：按着的时候扭矩是开的 + 松开之后回读扭矩关了，两样都有才算 ✓ ----------------

const TEST_STEPS = [
  ['torque', '托着鸭子（或者放在架子上），按手柄 Start 让它站起来'],
  ['press', '短按一下 Select（别按住：按住 2 秒会坐下并关机）'],
  ['readback', '等主程序回读：扭矩关了'],
];
const TEST_AT = { need_torque: 0, armed: 1, pressed: 1, released: 2 };              // 现在在哪一步
const FAIL_AT = { torque: 0, press: 1, early_off: 1, long: 1, pressed_off: 1, pressed_unknown: 1, readback: 2, relax: 2, studio: 2 };
function testStepState(t, i) {
  if (t.state === 'passed') return 'ok';
  if (t.state === 'failed') {
    const f = FAIL_AT[t.failed_at] ?? 2;
    return i < f ? 'ok' : i === f ? 'fail' : 'todo';
  }
  const at = TEST_AT[t.state];
  if (at === undefined) return 'todo';
  return i < at ? 'ok' : i === at ? 'doing' : 'todo';            // Select 还按着时第 2 步还在「进行中」（审查 18b）
}

function EStopTest({ pad, op }) {
  const t = pad?.estop_test || { state: 'idle' };
  const ok = pad?.estop?.ok;
  const running = !['idle', 'passed', 'failed'].includes(t.state);
  return html`<section class="board pd-card" id="pd-estop" tabIndex="-1">
    <h3>手柄急停有效吗</h3>
    <${EStopChecks} pad=${pad} />
    <div class="pd-test">
      ${t.state === 'idle' ? html`<p class="pd-small-p">上面几条都 ✓ 只说明「路是通的」。真按一次 Select、看到舵机真卸力了，才算测过。</p>` : null}
      ${t.state !== 'idle' ? html`<ol class="pd-steps">${TEST_STEPS.map(([key, text], i) => {
        const s = testStepState(t, i);
        return html`<li class=${'pd-s-' + s}><span class="pd-mark">${STEP_ICON[s] || ''}</span><span>${text}
          ${key === 'press' && t.state === 'pressed' ? html`<small>按着 ${t.held_s ?? 0} 秒…松开</small>` : null}</span></li>`;
      })}</ol>` : null}
      ${t.why && running ? html`<p class="pd-small-p">${t.why}</p>` : null}
      ${t.held_long && running ? html`<div class="banner warn" role="status"><b>Select 按住太久了，快松开</b><span class="more">按满 2 秒鸭子会坐下并关机，这次也不算。</span></div>` : null}
      ${t.state === 'passed' ? html`<div class="pd-result pd-good">✓ 急停测过了：松开 Select 到回读卸力 ${fmtMs(t.ms)}。<br />再按 Start 让它站回来。</div>` : null}
      ${t.state === 'failed' ? html`<div class="pd-result pd-fail"><b>× 没通过：${t.why || '原因不明'}</b>${t.fix ? html`<br />${t.fix}` : null}</div>` : null}
      <div class="pd-btns">
        ${running
          ? html`<button class="btn-light" onClick=${() => op({ op: 'estop_test', action: 'cancel' })}>不测了</button>`
          : html`<button class="btn-dark" id="pd-estop-btn" disabled=${!ok} title=${ok ? '' : '上面几条都 ✓ 才能测'} onClick=${() => op({ op: 'estop_test', action: 'start' })}>${t.state === 'idle' ? '测一次急停' : '再测一次'}</button>`}
      </div>
    </div>
  </section>`;
}

// ---------------- 配对向导 ----------------

function bars(rssi) {
  if (typeof rssi !== 'number') return html`<span class="pd-bars" title="这次搜索没看到它的信号（以前配过、记在鸭子上）">—</span>`;
  const n = rssi >= -60 ? 3 : rssi >= -75 ? 2 : 1;
  return html`<span class="pd-bars" title=${`信号 ${rssi} dBm`}>${[1, 2, 3].map((i) => html`<i class=${i <= n ? 'pd-on' : ''} style=${{ height: `${4 + i * 3}px` }}></i>`)}</span>`;
}

function DeviceList({ devices, picked, busy, op }) {
  const [showAll, setShowAll] = useState(false);
  const pads = devices.filter((d) => d.pad);
  const others = devices.filter((d) => !d.pad);
  const row = (d) => html`<div class=${cx('pd-dev', picked === d.mac && 'pd-sel')} key=${d.mac}>
    ${bars(d.rssi)}
    <span class="pd-dev-name"><b>${d.name || '（还没拿到名字）'}</b><small class="num">${d.mac}${d.kind_label ? ` · ${d.kind_label}` : ''}</small></span>
    <span class="pd-tags">
      ${d.pad ? html`<span class="pd-tag">手柄</span>` : html`<span class="pd-tag pd-gray">${d.why_not || '不像手柄'}</span>`}
      ${d.paired ? html`<span class="pd-tag pd-gray">配过</span>` : null}
      ${d.connected ? html`<span class="pd-tag pd-gray">连着</span>` : null}
    </span>
    <button class=${d.pad ? 'btn-dark' : 'btn-light'} disabled=${busy} title=${busy ? '还在搜索：等它搜完再点' : ''} onClick=${() => op({ op: 'pair', mac: d.mac }, 180000)}>配这个</button>
  </div>`;
  return html`<div class="pd-devs">
    ${pads.length ? pads.map(row) : html`<div class="pd-empty">还没搜到手柄。手柄的灯在快闪吗？</div>`}
    ${others.length ? html`<button class="pd-link" onClick=${() => setShowAll(!showAll)}>${showAll ? '收起' : `还有 ${others.length} 个不像手柄的蓝牙设备`}</button>` : null}
    ${showAll ? others.map(row) : null}
  </div>`;
}

function Wizard({ w, op, kind, setKind, onClose }) {
  const st = w?.state || 'idle';
  const busy = st === 'running';
  const k = kindOf(kind);
  const steps = w?.steps || [];
  const err = w?.error;
  const cfgBusy = w?.configd_busy_s;
  return html`<section class="board pd-card pd-wizard" id="pd-wizard" tabIndex="-1">
    <div class="pd-card-h"><h3>配对新手柄</h3>
      ${!busy ? html`<button class="pd-link" onClick=${onClose}>关掉</button>` : null}</div>
    ${cfgBusy ? html`<div class="banner warn" role="status"><b>configd 上一次配对还没做完</b><span class="more">它停不了，大约还要 ${cfgBusy} 秒，做完之前不能再配。</span></div>` : null}
    ${st === 'idle' ? html`
      <div class="pd-kinds" role="radiogroup" aria-label="手柄类型">
        ${Object.entries(KINDS).map(([key, x]) => html`<button role="radio" aria-checked=${kind === key} class=${kind === key ? 'pd-on' : ''} onClick=${() => setKind(key)}>${x.label}</button>`)}
      </div>
      <ol class="pd-how">${k.how.map((s) => html`<li>${s}</li>`)}</ol>
      <div class="pd-btns">
        <button class="btn-dark" disabled=${!!cfgBusy} onClick=${() => op({ op: 'scan', seconds: 12 }, 30000)}>灯在快闪了，开始搜索</button>
        <button class="btn-light" disabled=${!!cfgBusy} title="照官方 robotctl pad pair：不列清单，自动挑正在配对状态的那个" onClick=${() => op({ op: 'pair' }, 180000)}>直接配（自动挑）</button>
      </div>
      <p class="pd-small-p">搜索大约 12 秒。搜索时鸭子的 WiFi 可能会卡一下（蓝牙和 WiFi 是同一颗芯片）。鸭子在走的时候不能搜。</p>
      ${w.helper_available === false ? html`<p class="pd-small-p"><b>没有新版小助手</b>：这块板配新手柄要先停 btd，Studio 做不了，可能配不上。重装小助手，或者在鸭子上运行 <code>sudo robotctl pad pair</code>。</p>`
        : w.helper_available === true ? html`<p class="pd-small-p">配对走新版小助手：官方 <code>robotctl pad pair</code>，会先停 btd、重开蓝牙。</p>`
        : html`<p class="pd-small-p">新版小助手会用官方 <code>robotctl pad pair</code>（先停 btd、重开蓝牙）。这块板没有它时可能配不上：重装小助手，或者在鸭子上运行 <code>sudo robotctl pad pair</code>。</p>`}
      ` : html`
      <ol class="pd-steps">${steps.map((s) => html`<li class=${'pd-s-' + s.state}>
        <span class="pd-mark">${STEP_ICON[s.state] || ''}</span>
        <span>${s.label}${s.note ? html`<small>${s.note}</small>` : null}</span></li>`)}</ol>
      ${w.phase === 'scan' || w.phase === 'pick' ? html`<${DeviceList} devices=${w.devices || []} picked=${w.picked} busy=${busy} op=${op} />` : null}
      ${err ? html`<div class="pd-err" role="alert">
        <b>${err.msg}</b>
        ${err.fix ? html`<div>怎么办：${err.fix}</div>` : null}
        ${err.raw ? html`<details><summary>原文</summary><code>${err.raw}</code></details>` : null}
      </div>` : null}
      ${w.phase === 'keys' && st !== 'done' ? html`<p class="pd-small-p">把每个键按一遍、两个摇杆各推一圈。上面手柄图上按过的键会变成浅色描边。</p>` : null}
      ${st === 'done' ? html`<div class="pd-result pd-good">✓ 配好了：${w.result_name || '手柄'}。重启鸭子以后它会自己回连（开手柄就行）。</div>` : null}
      <div class="pd-btns">
        ${busy ? html`<button class="btn-light" onClick=${() => op({ op: 'cancel' })}>停下</button>` : null}
        ${w.phase === 'pick' && !busy ? html`<button class="btn-light" disabled=${!!cfgBusy} onClick=${() => op({ op: 'scan', seconds: 12 }, 30000)}>再搜一次</button>` : null}
        ${w.phase === 'keys' && st !== 'done' ? html`<button class="btn-dark" onClick=${() => op({ op: 'keys_done' })}>按键都对，完成</button>` : null}
        ${st === 'failed' ? html`<button class="btn-dark" disabled=${!!cfgBusy} onClick=${() => op(w.retry || { op: 'scan', seconds: 12 }, 180000)}>重试</button>` : null}
        ${!busy ? html`<button class="btn-light" onClick=${() => op({ op: 'wizard_reset' })}>${st === 'done' ? '好的' : '从头来'}</button>` : null}
      </div>
      ${w.via ? html`<p class="pd-small-p">配对走的是：${w.via}${w.elapsed_s != null ? ` · 用了 ${w.elapsed_s.toFixed(0)} 秒` : ''}</p>` : null}`}
  </section>`;
}

// ---------------- 配过的手柄 ----------------

function KnownPads({ pad, op }) {
  const pads = pad?.status?.pads || [];
  if (!pads.length) return null;
  const forget = (p) => {
    const lines = [`忘掉 ${p.name || p.mac}？`, ''];
    if (p.connected) lines.push('它现在正连着：忘掉以后它马上断开，鸭子会立刻停下（我们改过的 padd 还会让它坐下）。先扶好鸭子。', '');
    lines.push('只删鸭子这边。手柄自己还记着鸭子，以后要再配，得先让手柄重新进配对状态。');
    if (window.confirm(lines.join('\n'))) op({ op: 'forget', mac: p.mac });
  };
  return html`<section class="board pd-card" id="pd-known" tabIndex="-1">
    <h3>鸭子记着的手柄</h3>
    ${pads.map((p) => html`<div class="pd-dev" key=${p.mac}>
      <span class=${cx('pd-dot', p.connected && 'pd-on')}></span>
      <span class="pd-dev-name"><b>${p.name || '（没名字）'}</b><small class="num">${p.mac}</small></span>
      <span class="pd-tags">
        <span class="pd-tag pd-gray">${p.connected ? '连着' : '没连'}</span>
        ${p.trusted ? null : html`<span class="pd-tag pd-warn" title="现在能用，但重启鸭子以后不会自己回连">没信任</span>`}
      </span>
      ${p.trusted ? null : html`<button class="btn-light" onClick=${() => op({ op: 'pair', mac: p.mac }, 180000)}>修复</button>`}
      <button class="btn-light" onClick=${() => forget(p)}>忘掉</button>
    </div>`)}
    <p class="pd-small-p">「没连」的：打开手柄就会自己连上（不用再配）。</p>
  </section>`;
}

// ---------------- 网页手柄：图上的键、两处摇杆、键盘共用的一个控制器 ----------------

const TEXT_TYPES = ['text', 'password', 'search', 'email', 'number', 'tel', 'url'];
const typing = (el) => !!el && (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'
  || (el.tagName === 'INPUT' && TEXT_TYPES.includes((el.type || 'text').toLowerCase())));
// 键盘只管走路（头 / 身体模式不响应：头满杆默认 2.5 rad，按一下 W 头就打到底，审查三 V2）。方向键不要了：
// 没开关以后，人按方向键想滚页面，鸭子就会走
const KEYMAP = { KeyW: ['ly', -1], KeyS: ['ly', 1], KeyA: ['lx', -1], KeyD: ['lx', 1], KeyQ: ['rx', -1], KeyE: ['rx', 1] };
const ZERO = { lx: 0, ly: 0, rx: 0, ry: 0 };
const dz = (v) => (Math.abs(v) < DZ ? 0 : v);
const MIRROR_WHY = '实体手柄连着：图只跟着它显示，网页上按的不收（Select 照样能卸力）';

// 读数：跟后端 pad_drive.sticks_to_intent 同一套公式（后端为准，这里只是让人看见现在在发什么）
function readout(mode, f, lim, head) {
  const LX = dz(f.lx), LY = -dz(f.ly), RX = dz(f.rx), RY = -dz(f.ry);
  if (mode === 'head') return [['脖子', RY * head, 'rad'], ['低抬头', -LY * head, 'rad'], ['转头', -LX * head, 'rad'], ['歪头', RX * head, 'rad']];
  if (mode === 'body') {
    return [['高低', LY * (LY >= 0 ? 1.0 : 2.5), 'cm'], ['前后倾', clamp(RY * 0.2618, -0.26, 0.26), 'rad'],
      ['左右倾', clamp(RX * 0.2618, -0.26, 0.26), 'rad']];
  }
  return [['前后', LY * lim.v, 'm/s'], ['平移', -LX * lim.v, 'm/s'], ['转弯', -RX * lim.w, 'rad/s']];
}

// 网页手柄的状态都在这里：摇杆（按 pointerId 分开记，两个能同时拖）、键盘、按住的键（LT / RT / X）、模式（走路 / 头 / 身体）。
// 图上的键（GamepadSvg）、图上的两个摇杆、键盘都走它，谁动都一样。规矩（审查三）：
// - 摇杆相对：以按下的那一点为中心（V2），0.1 死区；按住才发（10 Hz），回到中间发一次 stop。
// - 键盘只在遥控区（手柄图）有焦点时管用，失焦松开（V2）。
// - 切页、窗口失焦、页面转后台、滚出手柄这一块、实体手柄连上、别人接管：全部松开（摇杆回 0、键松开、退出头 / 身体模式）。
//   离开手柄区（padActivity.active 由真变假）还发 drive {on:false}，回来发 drive {open}（总览重排审查 O1）。
// - 触屏防误触（审查 O2）：摇杆只认从图上圆帽附近的起手区按下；页面刚滚过 0.5 秒内的按下不认；图上的键按下 0.12 秒没动才算按下。
//   Select 例外：按下就卸力，什么时候都能按（三 V3）。
// - 模式以后端为准（V6）：后端替这个页面退出了模式（卸力、策略停了、超时……），页面跟着回到走路，按着的摇杆要松开重按。
function useVpad({ pad, st, link }) {
  const v = pad?.virtual || {};
  const m = pad?.motion || {};
  const mirror = !!v.mirror;
  const d = v.driver;
  const locked = !!(d && !d.mine);
  const usable = !!pad?.motion && !mirror && !locked;
  const [axes, setAxes] = useState(ZERO);
  const [pressedView, setPressedView] = useState(() => new Set());
  const [heldView, setHeldView] = useState(() => new Set());
  const [mode, setModeView] = useState('drive');
  const [msg, setMsg] = useState(null);
  const [kbOn, setKbOn] = useState(false);
  const R = useRef(null);
  if (!R.current) {
    R.current = { sticks: { l: { pid: null, x: 0, y: 0 }, r: { pid: null, x: 0, y: 0 } }, kb: new Map(), held: new Set(),
      pressed: new Set(), mode: 'drive', modeAt: 0, lastNonZero: false, timer: null, keysTimer: null, alive: true };
  }
  const S = R.current;
  Object.assign(S, { usable, pad, st, mirror, locked, d });

  const say = (text, bad = true) => setMsg(text ? { text, bad, at: performance.now() } : null);
  const cmd = (args, t = 2000) => link.cmd('pad.op', args, t);
  const whyNot = () => (S.mirror ? MIRROR_WHY : S.locked ? `${S.d?.by || '另一个页面'}正在遥控：等它松手，或者点下面的「接管」`
    : '还没收到鸭子的状态');
  const press = (role) => { S.pressed.add(role); setPressedView(new Set(S.pressed)); };
  const unpress = (role) => { if (S.pressed.delete(role)) setPressedView(new Set(S.pressed)); };

  // ---- 摇杆 ----
  const frame = () => {
    const f = { lx: S.sticks.l.x, ly: S.sticks.l.y, rx: S.sticks.r.x, ry: S.sticks.r.y };
    if (S.mode === 'drive') for (const code of S.kb.keys()) { const [axis, val] = KEYMAP[code]; f[axis] = val; }
    return f;
  };
  const isZero = (f) => Math.max(Math.abs(f.lx), Math.abs(f.ly), Math.abs(f.rx), Math.abs(f.ry)) < DZ;
  const r3 = (x) => Math.round(x * 1000) / 1000 || 0;
  const send = async () => {
    const f = frame();
    if (isZero(f) || !S.usable) { kick(); return; }
    S.lastNonZero = true;
    const sentMode = S.mode;
    const r = await cmd({ op: 'sticks', mode: sentMode, lx: r3(f.lx), ly: r3(f.ly), rx: r3(f.rx), ry: r3(f.ry), seq: nextSeq() });
    if (!S.alive) return;
    if (!r.ok) { say(r.reason || r.error || '没发出去'); return; }
    const dd = r.data || {};
    if (dd.dropped && dd.mode && dd.mode !== S.mode && sentMode === S.mode && performance.now() - S.modeAt > MODE_GRACE_MS) adopt(dd.mode, dd.dropped);
    else if (dd.dropped && S.mode !== 'drive') say(dd.dropped, false);
  };
  // 有输入：马上发一帧，之后每 100 ms 一帧（拖动时不按 pointermove 的频率发）；回到中间那一下发一次 stop
  function kick() {
    const f = frame();
    setAxes(f);
    if (isZero(f) || !S.usable) {
      if (S.timer) { clearInterval(S.timer); S.timer = null; }
      if (S.lastNonZero) { S.lastNonZero = false; cmd({ op: 'stop', seq: nextSeq() }); }
      return;
    }
    if (!S.timer) { S.timer = setInterval(send, SEND_MS); send(); }
  }
  const voidSticks = () => {
    for (const side of ['l', 'r']) { const s = S.sticks[side]; if (s.pid != null) s.void = true; s.x = 0; s.y = 0; }
    S.kb.clear();
    kick();
  };
  const focusZone = (e) => {
    const z = e.currentTarget?.closest && e.currentTarget.closest('.pd-zone');
    if (z && document.activeElement !== z) z.focus({ preventScroll: true });
  };
  const stickDown = (side, e, k, center = true) => {
    if (e.button > 0) return;
    // 触屏：只认从中间圆帽起手的（底座上起手的多半是在滚页面），页面刚滚过的不认（审查 O2）。不 preventDefault：让它滚
    if (isTouch(e) && (!center || scrolledRecently())) return;
    if (!ACT.active) return;                      // 手柄这一块不在屏幕上（切走了、滚走了）：不收
    e.preventDefault(); focusZone(e);
    if (!S.usable) { say(whyNot(), false); return; }
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 有的浏览器没有 */ }
    S.sticks[side] = { pid: e.pointerId, ox: e.clientX, oy: e.clientY, k: Math.max(8, k), x: 0, y: 0, void: false };
  };
  const stickMove = (side, e) => {
    const s = S.sticks[side];
    if (s.pid !== e.pointerId || s.void) return;
    let x = (e.clientX - s.ox) / s.k, y = (e.clientY - s.oy) / s.k;
    const n = Math.hypot(x, y);
    if (n > 1) { x /= n; y /= n; }
    s.x = x; s.y = y;
    kick();
  };
  const stickUp = (side, e) => {
    if (S.sticks[side].pid !== e.pointerId) return;
    S.sticks[side] = { pid: null, x: 0, y: 0 };
    kick();
  };

  // ---- 按住的键：LT / RT / X（keys 通道，审查三 V4） ----
  const sendKeys = async (down = []) => {
    const r = await cmd({ op: 'keys', held: [...S.held], down, seq: nextSeq() });
    if (!S.alive) return;
    const drop = (k) => { S.held.delete(k); unpress(k === 'x' ? 'west' : k); };
    if (!r.ok) { down.forEach(drop); say(r.reason || r.error || '没按上'); }
    else if (r.data?.ended?.length) { r.data.ended.forEach(drop); say('后端把按着的键停了（卸力、被接管或者断了一下）：松开再按', false); }
    setHeldView(new Set(S.held));
    if (!S.held.size && S.keysTimer) { clearInterval(S.keysTimer); S.keysTimer = null; }
  };
  const holdKey = (k) => {
    if (!ACT.active) return;
    S.held.add(k); setHeldView(new Set(S.held));
    sendKeys([k]);
    if (!S.keysTimer) S.keysTimer = setInterval(() => { if (S.held.size) sendKeys(); }, HOLD_MS);
  };
  const releaseKey = (k) => { if (!S.held.delete(k)) return; setHeldView(new Set(S.held)); sendKeys(); };

  // ---- 模式：Y / B ----
  function adopt(serverMode, why) {
    S.mode = serverMode; S.modeAt = performance.now(); setModeView(serverMode);
    voidSticks();
    if (why) say(why, false);
    // 跟着后端回到走路：回一句「走路」，后端就知道页面看到了，之后再按 Y / B 才进得去（审查 R3：没回之前来的「进模式」
    // 后端当成没看到退出的心跳，不收）
    if (serverMode === 'drive') cmd({ op: 'mode', mode: 'drive', seq: nextSeq() });
  }
  const setMode = async (next, why) => {
    if (next === S.mode) return;
    S.mode = next; S.modeAt = performance.now(); setModeView(next);
    voidSticks();                                   // 换模式：按着的摇杆作废，要松开重按（不然推着头的手一换模式就变成走路）
    const r = await cmd({ op: 'mode', mode: next, seq: nextSeq() });
    if (!S.alive) return;
    if (!r.ok) { say(r.reason || r.error || '没切过去'); if (S.mode === next && next !== 'drive') adopt('drive'); return; }
    if (r.data?.dropped && r.data.mode && r.data.mode !== S.mode) { adopt(r.data.mode, r.data.dropped); return; }
    if (r.data?.note) say(r.data.note, false);
    else if (why) say(`回到走路（${why}）`, false);
  };
  // 心跳带 hb:true：后端只续「还在」，不会把它已经替页面退出的模式（卸力、策略停了、超时……）再打开；不一样就跟着后端回到走路（审查 R3）
  const beatMode = async () => {
    const r = await cmd({ op: 'mode', mode: S.mode, hb: true });
    if (S.alive && r.ok && r.data?.mode && r.data.mode !== S.mode && performance.now() - S.modeAt > MODE_GRACE_MS) adopt(r.data.mode, r.data.dropped);
  };

  // ---- 全部松开 ----
  function releaseAll(why, keep) {
    if (S.touchOff) { const f = S.touchOff; S.touchOff = null; f(); }     // 触屏上还没算按下的那一下：作废
    if (S.driveTimer) { clearTimeout(S.driveTimer); S.driveTimer = null; }  // 十字上还没按够 3 秒：不切
    S.kb.clear();
    for (const side of ['l', 'r']) { const s = S.sticks[side]; if (s.pid != null) s.void = true; s.x = 0; s.y = 0; }
    if (S.held.size) { S.held.clear(); setHeldView(new Set()); sendKeys(); }
    S.pressed.clear(); if (keep) S.pressed.add(keep); setPressedView(new Set(S.pressed));
    if (S.mode !== 'drive') setMode('drive', why);   // 后端清掉头 / 身体（三 V1 第 2 条）
    kick();
  }

  // ---- 图上的键按下 / 松开 ----
  const estop = (fromCard) => {
    // 图上的 Select = 急停：按下就发（不等松手：触屏上手指一动浏览器发的是 pointercancel），跟顶上「卸力」同一条路（审查三 V3）。
    // 遥控卡顶上的「卸力」按钮（审查 O3）也走这里
    const p = link.cmd('relax', {}, 7000);
    if (fromCard === true) releaseAll('按了遥控卡上的「卸力」');
    else releaseAll('按了图上的 Select（卸力）', 'select');
    p.then((r) => { if (S.alive) say(r.ok ? '✓ 卸力发出去了：鸭子会软下来，扶住' : (r.reason || r.error || '卸力没发出去：用手柄 − 或关电源'), !r.ok); });
  };
  const startKey = async () => {
    const s = S.pad?.motion?.start;
    if (!s) return;
    const tilt = S.st?.tilt_deg;
    if (s.op === 'stand' && typeof tilt === 'number' && tilt > 30
      && !window.confirm(`它现在是躺着的（倾斜 ${Math.round(tilt)}°）。站起来时腿会突然用力：确定托好了吗？`)) { unpress('start'); return; }
    const r = await cmd({ op: 'start', expect: s.op, src: 'svg' }, 10000);
    if (S.alive) say(r.ok ? `✓ Start → ${s.label}${r.data?.reason ? `：${r.data.reason}` : ''}` : (r.reason || r.error || '没成功'), !r.ok);
  };
  // 十字右：照官方按一下就发（主程序先全身卸力），这边跟 Select 一样先全部松开
  const rebootKey = async () => {
    releaseAll('按了十字右（重启舵机：主程序会先卸力）', 'dr');
    const r = await cmd({ op: 'reboot', src: 'svg' }, 7000);
    if (S.alive) say(r.ok ? '✓ 重启舵机发出去了：鸭子会先软下来，扶住；好了按 Start 站回来' : (r.reason || r.error || '没成功'), !r.ok);
  };
  // 十字上：按住 3 秒才发；没到 3 秒松手（up）、或者全部松开（releaseAll）就不切
  const driveHold = () => {
    const target = S.pad?.motion?.drive_mode?.target;
    clearTimeout(S.driveTimer);
    say(`按住 3 秒切到${DRIVE_CN[target] || '另一个模式'}…`, false);
    S.driveTimer = setTimeout(async () => {
      S.driveTimer = null;
      if (!S.pressed.has('du')) return;
      const r = await cmd({ op: 'drive_mode', expect: target, src: 'svg' }, 10000);
      if (!S.alive) return;
      if (!r.ok) { say(r.reason || r.error || '没成功'); return; }
      say(r.data?.reason ? `✓ ${r.data.reason}` : `✓ 切到${DRIVE_CN[target]}：它会叫${target === 'roller' ? '两' : '一'}声、回站姿再换`, false);
    }, DRIVE_HOLD_MS);
  };
  const oneShot = async (role) => {
    const key = ROLE_BIND[role];
    const row = S.pad?.motion?.keys?.[key];
    const r = await cmd({ op: 'key', key, src: 'svg' }, 10000);
    if (!S.alive) return;
    if (!r.ok) { say(r.reason || r.error || '没成功'); if (key === 'x') { releaseKey('x'); unpress('west'); } } else say(`✓ ${row ? row.does : key}`, false);
  };
  const info = (role) => {
    if (role === 'select') return { ok: true, does: '卸力（急停）：按下就卸，鸭子会软下来，扶住' };
    const f = FUNC[role];
    if (f?.off) return { ok: false, why: f.off };
    const mm = S.pad?.motion || {};
    if (role === 'dr') {                          // 跟 Select 一样别人在遥控时也能按（救急：后端不看方向盘）
      const x = mm.reboot;
      if (!x || S.mirror) return { ok: false, why: whyNot() };
      return x.ok ? { ok: true, does: x.does } : { ok: false, why: x.why };
    }
    if (role === 'start') {
      const s = mm.start;
      if (!s || S.mirror) return { ok: false, why: whyNot() };
      if (S.locked && s.op !== 'walk_stop') return { ok: false, why: whyNot() };   // 「停下（站好）」谁都能按
      return s.ok ? { ok: true, does: `现在按：${s.label}` } : { ok: false, why: s.why };
    }
    if (!S.usable) return { ok: false, why: whyNot() };
    if (role === 'du') {
      const x = mm.drive_mode;
      if (!x) return { ok: false, why: '还没收到现在是走路还是轮滑' };
      return x.ok ? { ok: true, does: x.does } : { ok: false, why: x.why };
    }
    if (role === 'north') return { ok: true, does: S.mode === 'head' ? '回到走路' : '头模式：摇杆管头（网页上没有手柄的姿态）' };
    if (role === 'east') return { ok: true, does: S.mode === 'body' ? '回到走路' : '身体模式：摇杆让身体倾斜、下蹲' };
    if (role === 'ls' || role === 'rs') {
      const t = { drive: ['走路：前后、平移', '转弯'], head: ['转头、低抬头', '脖子俯仰、歪头'], body: ['起伏、下蹲', '前后左右倾'] }[S.mode];
      return { ok: true, does: `拖动：${t[role === 'ls' ? 0 : 1]}` };
    }
    if (role === 'lt' || role === 'rt') {
      const t = mm.triggers || {};
      if (!t.ok) return { ok: false, why: t.why || '现在不能按' };
      return { ok: true, does: (role === 'lt' ? '按住：张嘴、一直 wheee' : '按住：张嘴；按下嘎一声') + (t.note ? `（${t.note}）` : '') };
    }
    const bk = ROLE_BIND[role];
    if (bk) {
      const row = mm.keys?.[bk];
      if (!row) return { ok: false, why: '还没收到这个键绑了什么' };
      return row.ok ? { ok: true, does: row.does + (role === 'west' ? '（按住连着滚）' : '') } : { ok: false, why: row.why };
    }
    return { ok: false, why: '没有作用' };
  };
  const fire = (role) => {
    if (!ACT.active) return;                      // 手柄这一块不在屏幕上：不收（Select 不走这里）
    const x = info(role);
    if (!x.ok) { say(x.why, false); return; }
    press(role);
    if (role === 'start') { startKey(); return; }
    if (role === 'dr') { rebootKey(); return; }
    if (role === 'du') { driveHold(); return; }
    if (role === 'north' || role === 'east') { const want = role === 'north' ? 'head' : 'body'; setMode(S.mode === want ? 'drive' : want); return; }
    if (ROLE_HELD[role]) holdKey(ROLE_HELD[role]);
    if (ROLE_BIND[role]) oneShot(role);
  };
  const down = (role, e) => {
    if (e.button > 0) return;
    if (role === 'select') {
      // 图上的 Select = 急停：按下就卸力，什么时候都能按（三 V3）；它的点击区单独 touch-action:none（审查 O2）
      e.preventDefault();
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 有的浏览器没有 */ }
      estop(); return;
    }
    if (isTouch(e)) {
      // 触屏：按下 0.12 秒没动、没被浏览器取消才算按下；0.12 秒内松手 = 点一下（按下再松开）。页面刚滚过不认（审查 O2）
      if (scrolledRecently() || !ACT.active) return;
      if (S.touchOff) S.touchOff();
      S.touchOff = touchHold(e, { press: () => fire(role), release: () => up(role), tap: () => { fire(role); up(role); } });
      return;
    }
    e.preventDefault(); focusZone(e);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 有的浏览器没有 */ }
    fire(role);
  };
  const up = (role) => {
    if (!S.pressed.has(role)) return;
    unpress(role);
    if (ROLE_HELD[role]) releaseKey(ROLE_HELD[role]);
    if (role === 'du' && S.driveTimer) { clearTimeout(S.driveTimer); S.driveTimer = null; say('没按够 3 秒：没切（官方要按住 3 秒，防止误按）', false); }
  };

  // ---- 键盘：只在遥控区有焦点时（审查三 V2） ----
  const zoneProps = {
    tabIndex: 0,
    onKeyDown: (e) => {
      const km = KEYMAP[e.code];
      if (!km || typing(e.target) || e.ctrlKey || e.altKey || e.metaKey) return;
      // 用滚轮把遥控区滚出去以后焦点还留在里面：那时按着的键早就松开了（离开手柄区），按住重复来的按下也不收（审查 O1）
      if (!ACT.active) return;
      e.preventDefault();
      // 浏览器的自动重复只续「已经按着的」：任何「全部松开」（滚出手柄区、失焦、卸力……）之后都要真按一次，
      // 滚回来、切回窗口时手还按着 W，重复来的按下不能让鸭子接着走（代码审查 C3；摇杆也是松开重按）
      if (e.repeat && !S.kb.has(e.code)) return;
      if (S.mode !== 'drive') { say('头 / 身体模式下键盘不管用，用摇杆（按一下 W 头就会打到底）', false); return; }
      if (!S.usable) { say(whyNot(), false); return; }
      const had = S.kb.has(e.code);
      S.kb.set(e.code, performance.now());
      if (!had) kick();
    },
    onKeyUp: (e) => { if (S.kb.delete(e.code)) kick(); },
    onFocus: () => setKbOn(true),
    onBlur: (e) => {
      const to = e.relatedTarget;
      if (to && to.closest && to.closest('.pd-zone')) return;        // 在两个遥控区之间换：不算失焦
      setKbOn(false);
      if (S.kb.size) { S.kb.clear(); kick(); }
    },
  };

  useEffect(() => {
    S.alive = true;
    // 进手柄区（这一块到了屏幕上）：只解开「离开了」，不拿方向盘（审查三 V5）。等连接建好再发；发之前再看一眼还在不在
    const open = () => link.whenOpen().then(() => { if (S.alive && ACT.active) cmd({ op: 'drive', open: true, seq: nextSeq() }, 3000); });
    // 离开手柄区（切页、滚走、转后台）：全部松开，后端记「离开了」，晚到的帧都丢（审查 O1）
    const leave = (why) => {
      releaseAll(why);
      link.cmd('pad.op', { op: 'drive', on: false, seq: nextSeq(), why }, 3000);
    };
    const offAct = padActivity.subscribe(({ active, changed, routeChanged }, why) => {
      // A remote subpage keeps the recording lease, but never a held stick or key.
      if (routeChanged && !changed) releaseAll(why);
      if (!changed) return;
      if (active) open(); else leave(why);
    });
    if (ACT.active) open();
    // 断线重连以后是新的连接，后端默认它「不在手柄区」（审查 O1）：还在就再 open 一次
    const offLink = link.on((m) => { if (m.t === '_status' && m.status === 'open' && ACT.active) open(); });
    const blur = () => releaseAll('窗口失焦');
    const vis = () => { if (document.visibilityState !== 'visible') releaseAll('页面转到后台'); };
    // 空格是顶上的卸力（app.js 在捕获阶段截住）、Esc：顺手全部松开
    const keyCap = (e) => { if (e.code === 'Space' || e.code === 'Escape') releaseAll(e.code === 'Space' ? '按了空格（卸力）' : '按了 Esc'); };
    // 卡键：Mac 上按住 Cmd 时先松开的 W 收不到松开；弹框期间也可能丢。按住时浏览器会一直重复发按下，1 秒没收到就当松开
    const stale = setInterval(() => {
      const now = performance.now();
      let changed = false;
      for (const [c, at] of S.kb) if (now - at > KEY_STALE_MS) { S.kb.delete(c); changed = true; }
      if (changed) kick();
    }, 200);
    const beat = setInterval(() => { if (S.mode !== 'drive' && document.visibilityState === 'visible') beatMode(); }, MODE_HB_MS);
    window.addEventListener('blur', blur);
    document.addEventListener('visibilitychange', vis);
    window.addEventListener('keydown', keyCap, true);
    return () => {
      releaseAll('手柄这一块卸载了');
      S.alive = false;
      offAct(); offLink();
      clearInterval(stale); clearInterval(beat); clearInterval(S.timer); clearInterval(S.keysTimer);
      S.timer = S.keysTimer = null;
      window.removeEventListener('blur', blur);
      document.removeEventListener('visibilitychange', vis);
      window.removeEventListener('keydown', keyCap, true);
      link.cmd('pad.op', { op: 'drive', on: false, seq: nextSeq() }, 3000);   // 离开：后端记「离开了」，晚到的帧都丢
    };
  }, []);
  // 实体手柄连上、别人拿走了方向盘、读不到状态：全部松开
  useEffect(() => { if (!usable) releaseAll(mirror ? '实体手柄连上了' : locked ? '别的页面在遥控' : '读不到状态'); }, [usable]);
  // 模式以后端为准：后端替这个页面退出了（卸力、策略停了、心跳超时……），页面跟着回到走路
  useEffect(() => {
    const sm = v.mode;
    if (!sm || sm === S.mode || performance.now() - S.modeAt < MODE_GRACE_MS) return;
    adopt(sm, v.mode_exit ? `退出了${MODE_CN[S.mode]}：${v.mode_exit.why}` : null);
  });
  useEffect(() => {
    if (!msg) return undefined;
    const t = setTimeout(() => setMsg(null), msg.bad ? 8000 : 4000);
    return () => clearTimeout(t);
  }, [msg]);

  const liveWeb = { buttons: Object.fromEntries([...pressedView].map((r) => [r, true])),
    axes: { ...axes, lt: heldView.has('lt') ? 1 : 0, rt: heldView.has('rt') ? 1 : 0 } };
  return { mode, axes, msg, kbOn, mirror, locked, usable, liveWeb, zoneProps, info, down, up, stickDown, stickMove, stickUp, say,
    chooseMode: next => { if (ACT.active && S.usable && ['drive','head','body'].includes(next)) setMode(next); },
    release: () => releaseAll('点了摇杆归中'),
    estop: () => estop(true) };
}

// 按住才做的按钮：按下发 action_start，按着每 0.15 秒 action_hold，松手 / 移开 / 失焦 / 页面转后台 / 滚出手柄这一块
// 都发 action_stop。触屏上按下 0.12 秒没动、没被浏览器取消（开始滚页面）才算按下，页面刚滚过不认（审查 O2：
// 「左右各踢一脚」第一步就是 robot.do，手指从这里起手往下刷页面，以前一碰就踢）；0.12 秒内就松手什么都不做（按住才做）
function HoldButton({ item, link, onErr, lit, locked, quiet }) {
  const [held, setHeld] = useState(false);
  const timer = useRef(null);
  const alive = useRef(false);
  const pend = useRef(null);
  const seq = useRef(0);
  const stop = useCallback(() => {
    if (pend.current) { const f = pend.current; pend.current = null; f(); }
    if (!alive.current) return;
    alive.current = false; setHeld(false);
    clearInterval(timer.current); timer.current = null;
    link.cmd('pad.op', { op: 'action_stop' }, 3000);
  }, []);
  const down = (e) => {
    if (e.button > 0) return;
    if (isTouch(e)) {
      if (scrolledRecently() || !ACT.active || !item.ok || locked) return;
      if (pend.current) pend.current();
      pend.current = touchHold(e, { press: () => { pend.current = null; start(); }, release: stop });
      return;
    }
    start(e);
  };
  // 每次按下一个序号（press）：回执回来之前又松开、又按下了，第一次的回执就作废——不然会多设一个定时器、一直发
  // action_hold 到页面关掉，后端「0.45 秒没续就停」的保险对这个页面就失效了（代码审查 C7）。后端也按这个序号认续命
  const start = async (e) => {
    if (e) { e.preventDefault(); try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 有的浏览器没有 */ } }
    if (alive.current || !item.ok || locked || !ACT.active) return;
    const my = ++seq.current;
    alive.current = true; setHeld(true);
    const r = await link.cmd('pad.op', { op: 'action_start', id: item.id, press: my }, 4000);
    if (my !== seq.current) return;                       // 等回执的时候又按了一次：交给新的那次，这里什么都不做
    if (!r.ok) { onErr(r.reason || r.error || '没做成'); alive.current = false; setHeld(false); return; }
    if (!alive.current) { link.cmd('pad.op', { op: 'action_stop' }, 3000); return; }      // 回执到之前就松手了
    clearInterval(timer.current);
    const iv = setInterval(async () => {
      if (my !== seq.current || !alive.current) { clearInterval(iv); return; }
      const h = await link.cmd('pad.op', { op: 'action_hold', press: my }, 2000);
      if (my === seq.current && alive.current && h.ok && h.data && h.data.running === null) {
        alive.current = false; setHeld(false); clearInterval(iv);
      }
    }, HOLD_MS);
    timer.current = iv;
  };
  useEffect(() => {
    const vis = () => { if (document.visibilityState !== 'visible') stop(); };
    // 滚出手柄这一块、切到别的页：马上停（总览一直挂着，不会卸载；藏起来也不会释放指针捕获：审查 O1）
    const offAct = padActivity.subscribe(({ active, changed, routeChanged }) => { if (routeChanged || changed && !active) stop(); });
    window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', vis);
    return () => { offAct(); window.removeEventListener('blur', stop); document.removeEventListener('visibilitychange', vis); stop(); };
  }, []);
  return html`<button class=${cx('pd-act', 'pd-hold', held && 'pd-held', lit && 'pd-lit')} disabled=${!item.ok || locked}
    title=${item.ok ? `按住才做，松手就停：${item.text}` : item.why}
    onPointerDown=${down} onPointerUp=${stop} onPointerCancel=${stop} onLostPointerCapture=${stop}
    onKeyDown=${(e) => { if (e.key === 'Enter' && !e.repeat) start(); }} onKeyUp=${(e) => { if (e.key === 'Enter') stop(); }}
    onContextMenu=${(e) => e.preventDefault()}>
    <b>${item.name}</b><small>${quiet ? '' : item.ok ? item.text : item.why}</small></button>`;
}

// 图下面一行：Start 现在会做什么、摇杆管什么、刚按的结果；再加一行完整说明
function VpadBar({ vp, pad }) {
  const m = pad?.motion || {};
  const v = pad?.virtual || {};
  const lim = v.limits || {};
  const s = m.start;
  if (vp.mirror) return html`<p class="pd-small-p pd-vbar">${MIRROR_WHY}。</p>`;
  return html`<div class="pd-vbar">
    <span>Start 现在会：<b>${s ? (s.ok ? s.label : '不能按') : '—'}</b>${s && !s.ok ? html`<small>（${s.why}）</small>` : null}</span>
    <span>摇杆管：<b>${MODE_CN[vp.mode]}</b>${vp.mode !== 'drive' && pad?.motion && !m.driving ? html`<small>（策略没在跑，摇杆不会让它动）</small>` : null}</span>
    <span class="pd-kbhint">${vp.kbOn ? '键盘已接上' : '点一下图才接键盘'}</span>
    ${vp.msg ? html`<span class=${cx('pd-vmsg', vp.msg.bad && 'pd-bad-t')} role=${vp.msg.bad ? 'alert' : 'status'}>${vp.msg.text}</span>` : null}
  </div>
  <p class="pd-small-p pd-vbar-hint">键盘：点一下手柄图，W / S 前后、A / D 平移、Q / E 转弯（头 / 身体模式下键盘不管用）；空格 = 卸力（急停，整页都管用）· Esc = 全部松开（停下但不卸力）。满杆 ${(lim.v ?? 0.3).toFixed(2)} 米/秒、${(lim.w ?? 1.5).toFixed(1)} 弧度/秒（${lim.src || '官方默认'}），头满杆 ${(v.inputs?.head ?? 2.5).toFixed(1)} rad（${v.inputs?.src || '官方默认'}）。鸭子还没「准备好走」时摇杆不动：先点「站起来」、再点「开始走」（或者按图上的 Start）。</p>`;
}

function Remote({ st, pad, link, vp, section }) {
  const [err, setErr] = useState(null);
  const [busyUntil, setBusyUntil] = useState(0);
  const [busyPose, setBusyPose] = useState(null);
  const v = pad?.virtual || {};
  const m = pad?.motion || {};
  const live = pad?.live || {};
  const d = v.driver;
  const locked = !!(d && !d.mine);
  const mirror = !!v.mirror;
  const lb = live.buttons || {};
  const lax = live.axes || {};
  const binds = Object.fromEntries((pad?.bindings || []).map((b) => [b.button, b.skill]));
  const bindOf = (btn) => (btn in binds ? binds[btn] : DEFAULT_BIND[btn]);
  const physSkill = (name) => mirror && Object.entries(BIND_ROLE).some(([btn, role]) => bindOf(btn) === name && lb[role]);
  const now = performance.now();
  const busy = now < busyUntil && m.posture === busyPose;
  useEffect(() => { if (busyUntil) { const t = setTimeout(() => setBusyUntil(0), Math.max(0, busyUntil - performance.now())); return () => clearTimeout(t); } return undefined; }, [busyUntil]);

  const op = async (args, timeout = 8000) => {
    setErr(null);
    const r = await link.cmd('pad.op', args, timeout);
    if (!r.ok) setErr(r.reason || r.error || '没成功');
    return r;
  };
  const takeOver = async () => {
    if (!window.confirm(`现在是 ${d?.by || '另一个页面'} 在遥控。接管以后，那边的摇杆和动作马上就不管用了。接管吗？`)) return;
    await op({ op: 'drive', on: true, force: true, seq: nextSeq() });
  };

  const press = async (b) => {
    if (b.op === 'stand' && typeof st?.tilt_deg === 'number' && st.tilt_deg > 30
      && !window.confirm(`它现在是躺着的（倾斜 ${Math.round(st.tilt_deg)}°）。站起来时腿会突然用力：确定托好了吗？`)) return;
    setBusyUntil(performance.now() + BUSY_MS); setBusyPose(m.posture);
    const r = await op({ op: b.op }, 10000);
    if (!r.ok) setBusyUntil(0);
  };
  const B = m.buttons || {};
  const mainBtn = (b, extra) => {
    if (!b) return null;
    const lit = mirror && ((b.op === 'stand' || b.op === 'walk' || b.op === 'walk_stop') ? lb.start : b.op === 'sit' ? (bindOf('dpad_down') === 'sit_toggle' && lb.dd) : false);
    // 「停下（站好）」谁都能点、不排队（审查二 I1）；别的按钮在别人推摇杆时、上一个还没回话时先灰
    const off = !b.ok || (b.op !== 'walk_stop' && (locked || busy));
    return html`<button class=${cx('pd-big', extra, lit && 'pd-lit')} disabled=${off} onClick=${() => press(b)}
      title=${b.ok ? (b.call ? `发给主程序：${b.call}` : '') : b.why}>
      <b>${b.label}</b><small>${busy ? '等它回话…' : b.ok ? (b.note || '') : section ? '' : b.why}</small></button>`;
  };
  const last = m.last;
  const la = m.last_action;
  // 一组按钮都因为同一个原因不能按：原因只写一次，不在每个按钮上重复
  const common = (items) => {
    const xs = items || [];
    return xs.length && xs.every((x) => !x.ok && x.why === xs[0].why) ? xs[0].why : null;
  };
  const skWhy = common(m.skills), actWhy = common(m.actions), comboWhy = common(m.combos);
  const why = (text) => (text ? html`<p class="pd-small-p pd-why">${text}</p>` : null);
  return html`<section class="board pd-card pd-remote" id="pd-remote" tabIndex="-1" hidden=${section === 'gamepad'}>
    <div class="pd-card-h">
      <h3>遥控</h3>
      <span class="pd-pose">现在：<b>${m.posture_cn || '—'}</b></span>
      <span class="sv-spacer"></span>
      ${d ? html`<span class=${cx('pd-tag', !d.mine && 'pd-warn')}>${d.mine ? '这个页面在遥控' : `${d.by || '另一个页面'}正在遥控`}</span>` : null}
      ${locked ? html`<button class="btn-light" onClick=${takeOver}>接管</button>` : null}
      ${!section ? html`<button class="estop pd-relax" title="全部卸力，请扶住。" onClick=${(e) => { e.currentTarget.blur(); vp.estop(); }}>卸力</button>` : null}
    </div>
    ${!section ? html`<p class="pd-small-p pd-leave-note">滚出这一块、切到别的页，按着的摇杆和动作都会马上松开。</p>` : null}
    ${err ? html`<div class="banner bad" role="alert"><b>${err}</b><a href="#" onClick=${(e) => { e.preventDefault(); setErr(null); }}>知道了</a></div>` : null}
    ${locked ? html`<div class="banner warn" role="status"><b>${d.by || '另一个页面'}正在推摇杆 / 做动作</b><span class="more">同一时间只让一个页面开，免得两边抢。它松手 6 秒后这里就能用；急着用就点「接管」。「停下（站好）」、图上的 Select（卸力）随时能点。</span></div>` : null}
    ${v.pad_unknown ? html`<div class="banner warn" role="status"><b>认不出有没有实体手柄</b><span class="more">${v.pad_unknown}。如果手柄其实连着，图上的摇杆会被它盖掉。</span></div>` : null}
    ${mirror ? html`<div class="banner warn" role="status"><b>实体手柄已连接，网页摇杆只显示。</b><span class="more">站、走、坐、技能与叫声仍可使用。</span></div>` : null}

    <div class="pd-sec">
      <div class="pd-sec-h">站、走、坐</div>
      ${m.posture === 'servo_session' || st?.posture === 'servo_session' ? html`<p class="pd-small-p pd-why">调舵机中：主程序停着，站走都不能用（点顶上的「交还主程序」才回来）。</p>` : null}
      <div class="pd-bigs">${mainBtn(B.stand)}${mainBtn(B.walk, B.walk?.op === 'walk_stop' ? 'pd-stopbtn' : '')}${mainBtn(B.sit)}</div>
      ${last ? html`<p class=${cx('pd-small-p', !last.ok && 'pd-bad-t')}>${last.ok ? '✓' : '×'} ${last.what}${last.by ? `（${last.by}）` : ''}：${last.ok ? (last.reason || '主程序收到了') : last.reason}</p>` : null}
    </div>

    ${section ? html`<a class="pd-audio-link" href="#/sensing/audio">监听与音乐 ›</a>` : html`<${AudioInteraction} pad=${pad} link=${link} activity=${padActivity} />`}
    <${MotionStudio} pad=${pad} link=${link} activity=${padActivity} view=${section === 'control' ? 'compact' : 'full'} />

    <div class="pd-sec" hidden=${section === 'recording'}>
      <div class="pd-sec-h">技能<small>点一下做一次（官方的动作）</small></div>
      ${why(skWhy || actWhy || comboWhy)}
      <div class="pd-acts">
        ${(m.skills || []).map((s) => html`<button class=${cx('pd-act', physSkill(s.name) && 'pd-lit')} disabled=${!s.ok || locked}
          title=${s.ok ? `robot.do ${s.name}` : s.why} onClick=${() => op({ op: 'skill', name: s.name })}>
          <b>${s.label}</b><small>${section || s.ok || skWhy ? '' : s.why}</small></button>`)}
        ${!(m.skills || []).length ? html`<p class="pd-small-p">${m.skills_why || '这只鸭子没配技能'}</p>` : null}
      </div>
      <div class="pd-sec-h">叫一声</div>
      <div class="pd-acts pd-sounds">
        ${(section ? (m.sounds || []).slice(0,1) : m.sounds || []).map((s) => html`<button class=${cx('pd-act', 'pd-chipbtn', mirror && s.tag === 'chirp' && (lax.rt || 0) > 0.3 && 'pd-lit')} disabled=${!s.ok}
          title=${s.ok ? `robot.sound ${s.tag}` : s.why} onClick=${() => op({ op: 'sound', tag: s.tag })}>${s.label}</button>`)}
      </div>
      <div class="pd-sec-h">动作<small>按住才做，松手就停</small></div>
      ${section ? null : why(actWhy)}
      <div class="pd-acts">
        ${(m.actions || []).map((a) => html`<${HoldButton} key=${a.id} item=${a} link=${link} onErr=${setErr} locked=${locked} quiet=${!!section || !!actWhy}
          lit=${mirror && a.id === 'mouth' && Math.max(lax.lt || 0, lax.rt || 0) > 0.1} />`)}
      </div>
      <details class="pd-other-actions"><summary>组合动作与其他声音</summary>
      <div class="pd-sec-h">组合动作<small>按住才做，松手停止</small></div>
      ${!section && comboWhy && comboWhy !== actWhy ? why(comboWhy) : null}
      <div class="pd-acts">
        ${(m.combos || []).map((a) => html`<${HoldButton} key=${a.id} item=${a} link=${link} onErr=${setErr} locked=${locked} quiet=${!!section || !!comboWhy} />`)}
      </div>
      ${section ? html`<div class="pd-acts pd-sounds">${(m.sounds || []).slice(1).map(s => html`<button class="pd-act pd-chipbtn" disabled=${!s.ok} title=${s.why || s.label} onClick=${() => op({op:'sound',tag:s.tag})}>${s.label}</button>`)}</div>` : null}
      </details>
      ${m.running ? html`<p class="pd-small-p">正在做「${m.running.name}」：第 ${m.running.step + 1} / ${m.running.steps} 步${m.running.by ? `（${m.running.by}）` : ''}</p>`
        : la ? html`<p class=${cx('pd-small-p', !la.ok && 'pd-bad-t')}>「${la.name}」${la.result}</p>` : null}
    </div>
    <details class="pd-control-help"><summary>操作与停止方式</summary><p class="pd-small-p">松手会归中；“停下（站好）”关闭行走策略；“卸力”关闭舵机扭矩。离开遥控区、失焦或转后台会松开输入，头与身体回正。断线后由后端超时停止，不自动续动。</p><p class="pd-small-p">触屏从空白处滑动可滚页；按住动作后松手停止。空格卸力，Esc 全部松开。</p></details>
  </section>`;
}

// ---------------- 手柄区的 3D 鸭子（用户 10-18：「摇杆的左边应该是 3D 视图、右边是摇杆控制，这样控制着也能看见鸭子的状态」） ----------------
// 宽屏左侧是 3D 与摄像头上下两张卡，空间够时整体吸在仪表带下面；窄屏按顺序放在遥控上面。
// 跟总览舞台一样：实时关节、不缩放、单指滚页面两指转（duck3d 的 pageScroll，touch-action 也写在 CSS 里），不在视口就不画（duck3d 的 IO）；
// 模型几何体是 duck3d 模块里共用的缓存，不会加载两次。数据旧了去色（class，不用 style 字符串：代码审查 C6）。
// 下面一行小字：姿势 · 电量 · 现在的前后 / 平移 / 转弯（跟遥控卡的读数同一套公式、同一份摇杆状态）
function PadDuck({ st, pad, vp, live }) {
  const canvasRef = useRef(null);
  const duckRef = useRef(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    const d = new Duck3D(canvasRef.current, { view: 'front34', centerX: 0.5, centerY: 0.5, fill: 0.86, pageScroll: true });
    duckRef.current = d;
    d.load('model/').catch((e) => setErr(`3D 模型没加载：${e.message}`));
    return () => d.dispose();
  }, []);
  useEffect(() => {
    if (!duckRef.current || !st) return;
    duckRef.current.setJoints(st.joints);
    duckRef.current.setGravity(st.gravity);
  }, [st]);
  const v = pad?.virtual || {};
  const ax = pad?.live?.axes || {};
  const mirror = vp.mirror;
  const f = mirror ? { lx: ax.lx || 0, ly: ax.ly || 0, rx: ax.rx || 0, ry: ax.ry || 0 } : vp.axes;
  const rows = readout(mirror ? 'drive' : vp.mode, f, v.limits || { v: 0.3, w: 1.5 }, v.inputs?.head ?? 2.5);
  const pct = st?.battery?.pct;
  const old = !live || st?.joints_age_ms == null || st.joints_age_ms > 1000;
  return html`<section class="board pd-card pd-3d" aria-label="鸭子现在的姿态">
    <div class="pd-3d-stage"><canvas ref=${canvasRef} class=${old ? 'stage-stale' : ''}></canvas>
      ${err ? html`<span class="sv-loading">${err}</span>` : null}</div>
    <p class="pd-small-p pd-3d-line num">${pad?.motion?.posture_cn || '—'} · 电量 ${typeof pct === 'number' ? `${Math.round(pct)}%` : '—'}
      ${rows.map(([n, val, u]) => ` · ${n} ${(Math.round(val * 100) / 100 || 0).toFixed(2)} ${u}`).join('')}${old ? ' · 数据旧了' : ''}</p>
  </section>`;
}

// ---------------- 手柄区块（在总览里，往下一刷就是） ----------------

const TOPBAR_PX = 56;       // 吸顶的顶栏（styles.css .topbar）：看「在不在屏幕上」时扣掉它（审查 O1、O3）
const TABBAR_PX = 83;       // 手机底栏（styles.css .tabbar）

function ConsoleSticks({ vp, pad, shown, kind }) {
  const axes = shown?.axes || vp.axes;
  const labels = {drive:[['左右平移','前后移动'],['左右转向','—']],head:[['转头','低抬头'],['歪头','脖子俯仰']],body:[['—','起伏下蹲'],['左右倾','前后倾']]}[vp.mode];
  const lim = {v:.3,w:1.5,...pad?.virtual?.limits};
  const output = readout(vp.mode, axes, lim, pad?.virtual?.inputs?.head ?? 2.5);
  return html`<div class=${cx('pd-zone','console-drive-zone',vp.kbOn && 'pd-kb')} ...${vp.zoneProps} aria-label="网页遥控区，W S A D Q E 控制行走">
    <div class="console-mode-tabs">${[['drive','走路'],['head','头部'],['body','身体']].map(([mode,label]) => html`<button type="button" class=${vp.mode === mode ? 'active' : ''} aria-pressed=${vp.mode === mode} disabled=${!vp.usable} onClick=${() => vp.chooseMode(mode)}>${label}</button>`)}</div>
    <div class="console-sticks">${['l','r'].map((side,index) => html`<div class="console-stick-unit"><strong>${index ? '右摇杆' : '左摇杆'}</strong><span>${labels[index].filter(v=>v!=='—').join(' · ')}</span><div class="console-stick-well"><i class="console-stick-cross"></i><button type="button" class="console-stick-thumb" aria-label=${`${index ? '右':'左'}摇杆，按住并拖动`} disabled=${!vp.usable}
      style=${{transform:`translate(${(axes[side+'x'] || 0)*32}px,${(axes[side+'y'] || 0)*32}px)`}}
      onPointerDown=${e=>vp.stickDown(side,e,40,true)} onPointerMove=${e=>vp.stickMove(side,e)} onPointerUp=${e=>vp.stickUp(side,e)} onPointerCancel=${e=>vp.stickUp(side,e)} onLostPointerCapture=${e=>vp.stickUp(side,e)} onContextMenu=${e=>e.preventDefault()}>${index ? '↻' : '✥'}</button></div></div>`)}</div>
    <p class="console-stick-hint">按住圆心拖动，松手归中</p><div class="console-stick-output">${output.map(([name,value,unit])=>html`<span>${name} <b>${value.toFixed(2)}</b> ${unit}</span>`)}</div>
    <div class="console-stick-footer"><button type="button" class="btn-light" onClick=${()=>vp.release()}>摇杆归中</button>${vp.msg ? html`<span class=${vp.msg.bad ? 'pd-bad-t' : ''} role="status">${vp.msg.text}</span>` : null}</div>
    <details class="pd-control-help"><summary>键盘、全部按键与控制幅度</summary><${VpadBar} vp=${vp} pad=${pad} /><${GamepadSvg} kind=${kind} live=${shown} ctl=${vp} keys=${pad?.motion?.keys} /></details>
  </div>`;
}

// 总览重排（方案 §1.2、审查 O11）：顺序是 页头 → 手柄图（按键表默认折起）+ 遥控 → 配对向导 / 测急停 / 配过的手柄。
// 不再有单独的手柄页：#/pad 是总览上的锚点（app.js 打开就滚到这里，id="pad-block"）。
// 这一块一直挂着，「在不在」看 padActivity（路由 + 页面可见 + 这一块在视口里）：watch、drive open / off、全部松开都挂在它上面
export function PadPage({ st, pad, link, api, previewActive = false, live: duckLive = true, section = null }) {
  const [msg, setMsg] = useState(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [kind, setKind] = useState('xbox');
  const [visualFits, setVisualFits] = useState(true);
  const rootRef = useRef(null);
  const s = pad?.status || {};
  const live = pad?.live || {};
  const w = pad?.wizard || { state: 'idle' };
  const showWizard = wizardOpen || w.state !== 'idle';
  // 认出来的型号优先；还没连手柄时按向导里选的型号画（默认 Xbox）
  const activeKind = live.device?.kind || s.active?.kind || kind;
  const vp = useVpad({ pad, st, link });
  // 图上显示什么：实体手柄连着（镜像）或者在配对的按键测试 → 实体手柄的；否则 → 网页上按着的
  const shown = vp.mirror || live.state === 'attached' ? live : vp.liveWeb;

  // 顶部提示换行时也按实际高度判断；放不下就随页面滚，不能把摄像头固定在屏幕外。
  useEffect(() => {
    const left = rootRef.current?.querySelector('.pd-left');
    if (!left) return;
    let frame = null;
    const measure = () => {
      frame = null;
      const height = left.getBoundingClientRect().height;
      const top = parseFloat(getComputedStyle(left).top);
      if (height > 0 && Number.isFinite(top)) setVisualFits(height + top + 16 <= innerHeight);
    };
    const schedule = () => { if (frame == null) frame = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule);
    for (const el of [left, document.querySelector('.band'), document.querySelector('.sticky-bar')]) {
      if (el) observer.observe(el);
    }
    window.addEventListener('resize', schedule);
    schedule();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); window.removeEventListener('resize', schedule); };
  }, []);

  const op = useCallback(async (args, timeout = 15000) => {
    const r = await link.cmd('pad.op', args, timeout);
    if (!r.ok) setMsg(r.reason || r.error || '没成功'); else setMsg(null);
    return r;
  }, []);

  // 这一块在不在视口里（扣掉吸顶的顶栏、手机底栏）。藏起来（切到别的页）时 IO 也报「不相交」，但晚一帧：
  // 路由那边 app.js 在 hashchange 里同步先算过了（padActivity.setRoute）
  // 看的是原来那几块：页头、手柄图、遥控、下面的配对 / 测急停卡——3D 卡不算（只看着鸭子不算在手柄区：用户 10-18 第三条）
  useEffect(() => {
    let io = null;
    const mq = window.matchMedia('(max-width: 700px)');
    const seen = new Map();
    const build = () => {
      if (io) io.disconnect();
      seen.clear();
      io = new IntersectionObserver((es) => {
        for (const e of es) seen.set(e.target, e.isIntersecting);
        padActivity.setInView([...seen.values()].some(Boolean));
      }, { rootMargin: `-${TOPBAR_PX}px 0px -${mq.matches ? TABBAR_PX : 0}px 0px`, threshold: 0 });
      const root = rootRef.current;
      if (root) for (const el of root.querySelectorAll(':scope > .pd-head, .pd-live, #pd-remote, :scope > .pd-more')) io.observe(el);
    };
    build();
    if (mq.addEventListener) mq.addEventListener('change', build);
    return () => {
      if (io) io.disconnect();
      if (mq.removeEventListener) mq.removeEventListener('change', build);
      padActivity.setInView(false);
    };
  }, []);

  // 告诉后端「有人在看手柄这一块」：后端这时才订阅 padd 旁路（订阅了 padd 才打开手柄设备，平时不花钱）。
  // 要连续在屏幕上 0.3 秒才开（一划而过别开旁路：审查 O1）；断线重连后马上补一次
  useEffect(() => {
    let since = ACT.active ? performance.now() : null;
    let t0 = null;
    const tick = () => {
      if (ACT.active && since != null && performance.now() - since >= WATCH_SETTLE_MS) link.cmd('pad.op', { op: 'watch' }, 3000);
    };
    const arm = () => { clearTimeout(t0); t0 = setTimeout(tick, WATCH_SETTLE_MS + 20); };
    const offAct = padActivity.subscribe(({ active, changed }) => {
      if (!changed) return;
      if (active) { since = performance.now(); arm(); } else { since = null; clearTimeout(t0); }
    });
    if (since != null) arm();
    const iv = setInterval(tick, WATCH_MS);
    const offLink = link.on((m) => { if (m.t === '_status' && m.status === 'open') arm(); });
    return () => { offAct(); offLink(); clearInterval(iv); clearTimeout(t0); };
  }, []);

  // 上手清单的「去做」（审查 O17）：打开配对向导、滚到测急停并让「测一次急停」拿到焦点、滚到配过的手柄 / 遥控
  useEffect(() => {
    const fn = (what) => {
      if (what === 'wizard') setWizardOpen(true);
      // 等向导画出来（两帧）再滚
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const el = document.getElementById({ wizard: 'pd-wizard', estop: 'pd-estop', known: 'pd-known', remote: 'pd-remote' }[what]);
        if (!el) return;
        el.scrollIntoView({ block: 'center' });
        const btn = what === 'estop' ? document.getElementById('pd-estop-btn') : null;
        try { (btn && !btn.disabled ? btn : el).focus({ preventScroll: true }); } catch { /* 老浏览器 */ }
      }));
    };
    GO.add(fn);
    return () => GO.delete(fn);
  }, []);
  useEffect(() => { if (w.state === 'idle' && !wizardOpen) setMsg(null); }, [w.state]);

  const connected = !!s.active || live.state === 'attached';
  const tapTag = { attached: '实时', waiting: '等手柄', absent: '看不到按键', denied: '没权限读按键', error: '读不到按键', connecting: '连接中', off: '' }[live.state] || '';
  return html`<section class=${cx('pd-page', section && 'pd-console')} data-section=${section} id="pad-block" ref=${rootRef} aria-label="手柄">
    ${!section || section === 'gamepad' ? html`<header class="pd-head">
      <h2>${section ? {control:'网页遥控',recording:'录制与动作库',gamepad:'实体手柄'}[section] : '手柄'}</h2>
      ${!section || section === 'gamepad' ? html`<${Summary} pad=${pad} />` : null}
      <span class="sv-spacer"></span>
      ${!showWizard && (!section || section === 'gamepad') ? html`<button class="btn-dark" onClick=${() => setWizardOpen(true)}>${connected ? '再配一个手柄' : '配对手柄'}</button>` : null}
    </header>` : null}
    ${msg ? html`<div class="banner bad" role="alert"><b>${msg}</b><a href="#" onClick=${(e) => { e.preventDefault(); setMsg(null); }}>知道了</a></div>` : null}
    ${s.error && live.state !== 'attached' ? html`<div class="banner warn" role="status"><b>读不到 configd 的手柄状态</b><span class="more">${s.error}</span></div>` : null}
    ${s.netpad ? html`<div class="banner warn" role="status"><b>开着 UDP 手柄（netpad）</b><span class="more">这时 padd 只读电脑发来的 UDP 手柄，蓝牙手柄不管用、急停也不走蓝牙手柄。要用蓝牙手柄，先在配置里关掉 [netpad]。</span></div>` : null}
    ${s.known && s.driver && s.driver !== 'active' ? html`<div class="banner bad" role="alert"><b>padd 没在跑（${s.driver}）</b><span class="more">手柄连着也不管用。padd 只在主程序 robotd 跑着时才起。</span></div>` : null}
    ${st?.posture === 'servo_session' ? html`<div class="banner warn" role="status"><b>正在调舵机</b><span class="more">主程序停着，手柄、遥控、站走都不能用；离开舵机页舵机照样保持，点顶上的「交还主程序」才交还。</span></div>` : null}
    <div class="pd-main">
      <section class="board pd-card pd-live">
        <div class="pd-card-h"><h3>${section && section !== 'gamepad' ? '网页摇杆' : live.device?.name || s.active?.name || `${kindOf(activeKind).label}${vp.mirror ? '' : '（网页手柄）'}`}</h3>
          ${!section || section === 'gamepad' ? html`<span class="pd-livetag">${tapTag}${live.last_ms != null && live.state === 'attached' ? ` · ${fmtMs(live.last_ms)}前有输入` : ''}</span>` : null}</div>
        ${section && section !== 'gamepad' ? html`<${ConsoleSticks} vp=${vp} pad=${pad} shown=${shown} kind=${activeKind} />` : html`<div class=${cx('pd-zone', 'pd-svgzone', vp.kbOn && 'pd-kb')} ...${section === 'gamepad' ? {} : vp.zoneProps} aria-label=${section === 'gamepad' ? '实体手柄按键读回，Select 可卸力' : '网页手柄'}>
          <${GamepadSvg} kind=${activeKind} live=${section === 'gamepad' ? live : shown} seen=${w.phase === 'keys' ? w.seen : null} ctl=${section === 'gamepad' ? {...vp,info:role=>role === 'select' ? vp.info(role) : {ok:false,why:'实体输入读回'},down:(role,event)=>{if(role === 'select')vp.down(role,event);},up:role=>{if(role === 'select')vp.up(role);},stickDown:()=>{},stickMove:()=>{},stickUp:()=>{}} : vp} keys=${pad?.motion?.keys} />
        </div>
        ${!section ? html`<${VpadBar} vp=${vp} pad=${pad} />` : null}`}
        ${(!section || section === 'gamepad') && ['error', 'absent', 'denied'].includes(live.state) ? html`<p class="pd-small-p">${live.why || '读不到按键'}</p>` : null}
        ${section === 'gamepad' && !connected && live.state !== 'attached' ? html`<p class="pd-small-p">连接后，按键与摇杆输入将在图中亮起。</p>` : null}
        <details class="pd-legend-box" hidden=${!!section && section !== 'gamepad'}>
          <summary>按键表：每个键做什么</summary>
          <${Legend} kind=${activeKind} live=${shown} bindings=${pad?.bindings} imuHead=${pad?.virtual?.pad_imu_head} />
        </details>
      </section>
      <div class=${cx('pd-left', !visualFits && 'pd-left-overflow')} hidden=${!!section}>${!section ? html`<${PadDuck} st=${st} pad=${pad} vp=${vp} live=${duckLive} />
        <${InlineCamera} api=${api} active=${previewActive} />` : null}</div>
      <${Remote} st=${st} pad=${pad} link=${link} vp=${vp} section=${section} />
    </div>
    <div class="pd-more" hidden=${!!section && section !== 'gamepad'}>
      ${showWizard ? html`<${Wizard} w=${w} op=${op} kind=${kind} setKind=${setKind} onClose=${() => { setWizardOpen(false); op({ op: 'wizard_reset' }); }} />` : null}
      <${EStopTest} pad=${pad} op=${op} />
      <${KnownPads} pad=${pad} op=${op} />
    </div>
  </section>`;
}
