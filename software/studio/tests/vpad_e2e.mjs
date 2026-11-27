// 网页手柄（手柄页第三轮：图上的键能按）的端到端：连本机无头 Edge（CDP），打开 Studio 的手柄页（假 robotd 回放二号鸭的
// 数据），在图上点键、拖摇杆、按键盘、手机上两个手指一起拖，再看假 robotd 记下来的「收到了什么」（dev/fake_robotd.py --record）。
// 每一步收到的东西用 JSON 打到 stdout，由 tests/test_vpad_e2e.py 判断对不对。用的是真页面、真 Studio 后端，只有 robotd 是假的。
//
// 单独跑（先起好假 robotd、Studio、无头 Edge；端口别跟别人的撞）：
//   node tests/vpad_e2e.mjs --url http://127.0.0.1:18090/#/pad --cdp 9343 --record <rd.jsonl> --fake 17022 [--shots <截图目录>]
// Node ≥ 22（自带 WebSocket），不需要装任何包。
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import net from 'node:net';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const URL_ = arg('url');
const CDP = arg('cdp');
const REC = arg('record');
const FAKE = Number(arg('fake'));
const SHOTS = arg('shots', '');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => process.stderr.write(`[vpad-e2e] ${a.join(' ')}\n`);

// ---------------- 假 robotd 记下的东西 ----------------
// Studio 平时一直在问的（状态、绑定这些）不算：只看「让鸭子动 / 出声」的
const POLL = new Set(['hello', 'robot.health', 'robot.busStatus', 'robot.policies', 'robot.busCommand', 'pad.status', 'system.info',
  'robot.subscribe', 'pad.bindings', 'fake.set']);
const recs = () => readFileSync(REC, 'utf-8').split('\n').filter(Boolean).map((l) => JSON.parse(l)).filter((r) => !POLL.has(r.method));
let mark = 0;
const since = () => { const all = recs(); const out = all.slice(mark); mark = all.length; return out.map(({ method, params, notify }) => ({ method, params, notify })); };
const fakeSet = (params) => new Promise((resolve, reject) => {
  const s = net.connect(FAKE, '127.0.0.1', () => s.write(`${JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'fake.set', params })}\n`));
  let buf = '';
  s.on('data', (d) => { buf += d; if (buf.includes('\n')) { s.end(); resolve(JSON.parse(buf)); } });
  s.on('error', reject);
});

// ---------------- CDP ----------------
// 自己开一个标签页（别借别的会话的页面：视口会互相改）
const page = await (await fetch(`http://127.0.0.1:${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const waiting = new Map();
const errors = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Page.javascriptDialogOpening') { errors.push(`弹框：${m.params.message}`); send('Page.handleJavaScriptDialog', { accept: true }); }
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(`页面里出错：${r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text}`);
  return r.result?.result?.value;
};
const waitFor = async (expr, what, timeout = 6000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await js(expr)) return true; await sleep(100); }
  const now = await js(`[document.visibilityState, document.hasFocus(), document.querySelector('.pd-vbar')?.textContent, document.querySelector('.pd-pose')?.textContent,
    [...document.querySelectorAll('.pd-big')].map((b) => b.textContent).join(' | ')].join(' ／ ').replace(/\\s+/g, ' ')`);
  let fk = null;
  try { fk = (await fakeSet({})).result?.posture; } catch { /* 假 robotd 不在 */ }
  throw new Error(`${timeout / 1000} 秒没等到：${what}。页面上现在：${now}；假 robotd 的姿势：${fk}`);
};
const vbar = () => js(`document.querySelector('.pd-vbar')?.textContent?.replace(/\\s+/g, ' ') || ''`);
// 元素中心（先滚到屏幕中间）
const box = (sel) => js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null;
  e.scrollIntoView({ block: 'center', inline: 'nearest' }); const r = e.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height }; })()`);
const key = (r) => `.pd-svg-live [data-role="${r}"]`;
const mouse = (type, x, y, buttons = 1) => send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons, clickCount: 1 });
async function hold(sel, ms, during) {
  const b = await box(sel);
  if (!b) throw new Error(`找不到 ${sel}`);
  await mouse('mouseMoved', b.x, b.y, 0);
  await mouse('mousePressed', b.x, b.y);
  if (during) await during();
  await sleep(ms);
  await mouse('mouseReleased', b.x, b.y, 0);
}
// 拖摇杆：在 (中心 + off) 按下，分几步拖到 +d，停 ms，松开
async function drag(sel, d, ms, { off = [0, 0], during } = {}) {
  const b = await box(sel);
  const x0 = b.x + off[0], y0 = b.y + off[1];
  await mouse('mouseMoved', x0, y0, 0);
  await mouse('mousePressed', x0, y0);
  for (let i = 1; i <= 5; i++) { await mouse('mouseMoved', x0 + (d[0] * i) / 5, y0 + (d[1] * i) / 5); await sleep(20); }
  if (during) await during();
  await sleep(ms);
  await mouse('mouseReleased', x0 + d[0], y0 + d[1], 0);
}
async function shot(name, sel, toSel) {
  if (!SHOTS) return;
  let clip;
  if (toSel) {                                     // 从 sel 的上沿截到 toSel 的下沿
    const r = await js(`(() => { const a = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
      const b = document.querySelector(${JSON.stringify(toSel)}).getBoundingClientRect();
      return { x: a.x + scrollX - 8, y: a.y + scrollY - 8, width: a.width + 16, height: b.bottom - a.y + 16 }; })()`);
    clip = { ...r, scale: 1 };
  } else if (sel === 'full') {
    const h = await js('Math.ceil(document.documentElement.scrollHeight)');
    const w = await js('document.documentElement.clientWidth');
    clip = { x: 0, y: 0, width: w, height: h, scale: 1 };
  } else if (sel) {
    const r = await js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); const r = e.getBoundingClientRect();
      return { x: r.x + scrollX - 8, y: r.y + scrollY - 8, width: r.width + 16, height: r.height + 16 }; })()`);
    clip = { ...r, scale: 1 };
  }
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: !!clip, ...(clip ? { clip } : {}) });
  writeFileSync(join(SHOTS, name), Buffer.from(r.result.data, 'base64'));
  log('截图', name);
}
async function open(w, h, mobile) {
  // 无头 Edge 里新开的标签页可能被当成「在后台」：页面就报 visible:false，Studio 停推状态。拉到前面、模拟有焦点
  await send('Page.bringToFront');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send('Emulation.setTouchEmulationEnabled', { enabled: !!mobile, maxTouchPoints: mobile ? 5 : 1 });
  await send('Page.navigate', { url: 'about:blank' });
  await sleep(300);
  await send('Page.navigate', { url: URL_ });
  await waitFor(`!!document.querySelector('.pd-svg-live [data-role="start"]')`, '手柄图出来');
  await waitFor(`/Start 现在会：[^—]/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '收到手柄页的状态');
  await sleep(600);
}

// 只为镜像那两张截图：电脑上没有 padd 旁路，图上看不到实体手柄按了什么。window.__fakeLive 设了的时候，
// 把推来的 {t:'pad'} 里的 live 换成一份假的实体手柄按键（只改页面显示；Studio 后端判「实体手柄连着」用的是假 configd 的 pad.status，是真的）
const INJECT = `(() => {
  const NWS = window.WebSocket;
  class W extends NWS {
    set onmessage(fn) {
      super.onmessage = (ev) => {
        let d = ev.data;
        if (window.__fakeLive && typeof d === 'string' && d.startsWith('{"t":"pad"')) {
          try { const m = JSON.parse(d); m.live = window.__fakeLive; d = JSON.stringify(m); } catch (e) { /* 原样给 */ }
        }
        fn({ data: d });
      };
    }
    get onmessage() { return super.onmessage; }
  }
  window.WebSocket = W;
})();`;
const FAKE_LIVE = JSON.stringify({ state: 'attached', last_ms: 20, other: [], buttons: { south: true, lt: true },
  axes: { lx: 0.45, ly: -0.7, rx: -0.6, ry: 0, lt: 0.8, rt: 0 },
  device: { name: 'Xbox Wireless Controller', kind: 'xbox', bus: '蓝牙', keys_unverified: false } });
await send('Runtime.enable');
await send('Page.enable');
await send('Page.addScriptToEvaluateOnNewDocument', { source: INJECT });
const out = { steps: {}, errors };
async function step(name, fn, settle = 500) {
  since();
  const extra = (await fn()) || {};
  await sleep(settle);
  out.steps[name] = { recs: since(), ...extra };
  log(name, out.steps[name].recs.length, '条');
}

// ================= 电脑宽度 =================
await open(1280, 900, false);
await shot('desktop-1-idle.png');
out.start0 = await vbar();

// 摇杆是相对的：按下的那一点是中心，按下不动不走（审查三 V2）
await step('stick_press_only', () => drag(key('ls'), [0, 0], 300, { off: [12, 0] }));
// 图上的左摇杆往上拖：走（vx > 0），松手发一次 0
await step('stick_drag', () => drag(key('ls'), [0, -40], 600));
// A：低头捡（robot.do 请求）
await step('key_a', () => hold(key('south'), 80), 700);
// X 按住：先发一次请求，之后连着发通知（连滚）
await step('x_hold', () => hold(key('west'), 800), 600);
// LT 按住：张嘴、一直 wheee；松开合嘴、wheee 停（截一张「按着键」的图）
await step('lt_hold', () => hold(key('lt'), 600, async () => {
  await sleep(250);
  const b = await box(key('ls'));
  await shot('desktop-2-pressed.png', '.pd-live', '.pd-vbar');
  return b;
}), 700);
// RT 点一下：嘎一声
await step('rt_tap', () => hold(key('rt'), 120), 600);
// Y：头模式；右摇杆往上 = 脖子（neck_pitch 为正）；松手头回正；再按 Y 回走路
await step('head', async () => {
  await hold(key('north'), 80);
  await waitFor(`/摇杆管：头模式/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '进了头模式');
  await drag(key('rs'), [0, -40], 500, { during: () => shot('desktop-3-head.png', '.pd-live', '.pd-vbar') });
  await sleep(300);
  await hold(key('north'), 80);
  await waitFor(`/摇杆管：走路/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '回到走路');
  return { bar: await vbar() };
});
// B：身体模式；左摇杆往下 = 蹲（z < 0）；松手回中（还在身体模式）；再按 B 退出（active:false）
await step('body', async () => {
  await hold(key('east'), 80);
  await waitFor(`/摇杆管：身体模式/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '进了身体模式');
  await drag(key('ls'), [0, 40], 400);
  await sleep(300);
  await hold(key('east'), 80);
  await waitFor(`/摇杆管：走路/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '回到走路');
});
// 键盘：没点过图时按 W 不走；点一下图（遥控区拿到焦点）再按 W 走；方向键不走（审查三 V2）
await step('keyboard_unfocused', async () => {
  await js('document.activeElement && document.activeElement.blur()');
  await send('Input.dispatchKeyEvent', { type: 'keyDown', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87 });
  await sleep(300);
  await send('Input.dispatchKeyEvent', { type: 'keyUp', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87 });
});
await step('keyboard', async () => {
  const b = await box('.pd-svgzone');
  await mouse('mousePressed', b.x, b.y - b.h * 0.42);               // 点图的上沿空白处（不是键）
  await mouse('mouseReleased', b.x, b.y - b.h * 0.42, 0);
  const focused = await js(`document.activeElement?.classList.contains('pd-zone')`);
  await send('Input.dispatchKeyEvent', { type: 'keyDown', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87 });
  await sleep(200);
  await send('Input.dispatchKeyEvent', { type: 'keyDown', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87, autoRepeat: true });
  await sleep(250);
  await send('Input.dispatchKeyEvent', { type: 'keyUp', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87 });
  await sleep(300);
  const n = since();
  await send('Input.dispatchKeyEvent', { type: 'keyDown', code: 'ArrowUp', key: 'ArrowUp', windowsVirtualKeyCode: 38 });
  await sleep(300);
  await send('Input.dispatchKeyEvent', { type: 'keyUp', code: 'ArrowUp', key: 'ArrowUp', windowsVirtualKeyCode: 38 });
  return { focused, w_recs: n };
});
// 按着摇杆时窗口失焦：马上停，手还按着也不再发
await step('blur', async () => {
  const b = await box(key('ls'));
  await mouse('mouseMoved', b.x, b.y, 0);
  await mouse('mousePressed', b.x, b.y);
  for (let i = 1; i <= 5; i++) { await mouse('mouseMoved', b.x, b.y - 8 * i); await sleep(20); }
  await sleep(400);
  const before = since();
  await js(`window.dispatchEvent(new Event('blur'))`);
  await sleep(150);
  const atBlur = since();
  await mouse('mouseMoved', b.x, b.y - 45);
  await sleep(500);
  const after = since();
  await mouse('mouseReleased', b.x, b.y - 45, 0);
  return { before, atBlur, after };
});
// Start：在走（准备好走）→ 停下（站好）；假 robotd 变成「站着保持」以后再按 → 开始走
await step('start_stop', () => hold(key('start'), 80), 900);
await waitFor(`/Start 现在会：开始走/.test(document.querySelector('.pd-vbar')?.textContent || '')`, 'Start 变成「开始走」');
await step('start_walk', () => hold(key('start'), 80), 900);
await waitFor(`/Start 现在会：停下/.test(document.querySelector('.pd-vbar')?.textContent || '')`, 'Start 变回「停下」');
// 图上的 Select = 急停：按下就卸力（不等松手）
await step('select', async () => {
  const b = await box(key('select'));
  await mouse('mouseMoved', b.x, b.y, 0);
  await mouse('mousePressed', b.x, b.y);
  await sleep(250);
  const whilePressed = since();
  await mouse('mouseReleased', b.x, b.y, 0);
  await waitFor(`!!document.querySelector('.estop.done')`, '卸力确认');
  return { whilePressed };
}, 400);
await fakeSet({ posture: 'policy' });
await sleep(2600);                                                     // 卸力后 2 秒的保护过去
// 十字上（照官方）：按不到 3 秒不切；按满 3 秒发 robot.setMode。假 robotd 先没装轮滑模型（拒，页面写原因），装上再按就切，再按切回走路
const vmsg = () => js(`document.querySelector('.pd-vmsg')?.textContent || ''`);
const duSays = (word) => `(document.querySelector('${key('du')} title')?.textContent || '').includes(${JSON.stringify(word)})`;
await step('dpad_up_short', () => hold(key('du'), 1000), 300);
out.du_short = await vmsg();
await step('dpad_up_refused', async () => {
  await hold(key('du'), 3400);
  await waitFor(`/没装轮滑模型/.test(document.querySelector('.pd-vmsg')?.textContent || '')`, '页面说没装轮滑模型');
}, 300);
await fakeSet({ roller_models: true });
await step('dpad_up_hold', async () => {
  await hold(key('du'), 3400);
  await waitFor(`/切到轮滑/.test(document.querySelector('.pd-vmsg')?.textContent || '')`, '页面说切到轮滑');
}, 300);
await waitFor(duSays('切到走路'), '十字上变成「切到走路」（robot.policies 2 秒问一次）');
await step('dpad_up_back', async () => {
  await hold(key('du'), 3400);
  await waitFor(`/切到走路/.test(document.querySelector('.pd-vmsg')?.textContent || '')`, '页面说切到走路');
}, 300);
await fakeSet({ roller_models: false });
// 十字右（照官方）：按一下就发 robot.rebootMotors，不用确认；之后跟卸力一样软下来
await step('dpad_right', async () => {
  await hold(key('dr'), 80);
  await waitFor(`/重启舵机发出去了/.test(document.querySelector('.pd-vmsg')?.textContent || '')`, '页面说重启舵机发出去了');
}, 300);
await fakeSet({ posture: 'policy' });
await sleep(2600);
// 离开手柄区（身体模式开着）：身体退出（active:false）。手柄并进了总览以后，#/ 是同一页（可能还看得到手柄那一块），
// 所以切到日志页才是「离开」（总览重排审查 O1；滚出视口的那种在 tests/overview_e2e.mjs 里测）
await step('leave_page', async () => {
  await hold(key('east'), 80);
  await waitFor(`/摇杆管：身体模式/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '进了身体模式');
  await drag(key('ls'), [0, -30], 300);
  await js(`location.hash = '#/logs'`);
  await sleep(600);
  await js(`location.hash = '#/pad'`);
  await waitFor(`/Start 现在会：[^—]/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '回到手柄页');
  await sleep(500);
});
// 回到手柄页以后摇杆照样能用（drive open 解开了「离开了」）
await step('back_again', () => drag(key('ls'), [0, -40], 400));
// 实体手柄连着（镜像）：图上按的、拖的都不发；Select 照样卸力
await fakeSet({ pad_connected: true });
await waitFor(`!!document.querySelector('.pd-remote .banner') && /实体手柄连着/.test(document.querySelector('.pd-remote')?.textContent || '')`, '镜像', 8000);
await step('mirror', async () => {
  await hold(key('south'), 80);
  await drag(key('ls'), [0, -40], 400);
  await hold(key('north'), 80);
  await hold(key('lt'), 300);
  const bar = await vbar();
  await js(`window.__fakeLive = ${FAKE_LIVE}`);
  await sleep(400);
  await shot('desktop-4-mirror.png', '.pd-live', '.pd-vbar');
  await js('window.__fakeLive = null');
  return { bar };
});
await step('mirror_select', async () => {
  await hold(key('select'), 100);
  await waitFor(`!!document.querySelector('.estop.done')`, '镜像时卸力确认');
}, 400);
await fakeSet({ pad_connected: false, posture: 'policy' });
await waitFor(`!/实体手柄连着/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '镜像结束', 8000);
await sleep(2600);

// ================= 手机宽度 390 =================
await open(390, 844, true);
out.phone = await js(`({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })`);
await shot('phone-1-idle.png', 'full');
// 两个手指一起拖手柄图上的两个摇杆（触屏必须从透明起手区 .pd-ov-stick 按下）：左的往上（前进）、右的往右（右转）
await step('two_fingers', async () => {
  const [L, Rr] = await js(`(() => {
    const ls = document.querySelector('.pd-svgbox .pd-ov-stick[data-ov="ls"]');
    const rs = document.querySelector('.pd-svgbox .pd-ov-stick[data-ov="rs"]');
    ls.scrollIntoView({ block: 'center', inline: 'nearest' });
    const a = ls.getBoundingClientRect(), b = rs.getBoundingClientRect();
    return [{ x: a.x + a.width / 2, y: a.y + a.height / 2 }, { x: b.x + b.width / 2, y: b.y + b.height / 2 }];
  })()`);
  await sleep(650);                    // 页面刚滚过 0.5 秒内的触摸不认（防惯性滑动里误触：总览重排审查 O2）
  const pts = (dy, dx) => [{ x: L.x, y: L.y - dy, id: 1 }, { x: Rr.x + dx, y: Rr.y, id: 2 }];
  await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pts(0, 0) });
  for (let i = 1; i <= 5; i++) { await send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pts(9 * i, 9 * i) }); await sleep(30); }
  await sleep(500);
  await shot('phone-2-two-sticks.png', '.pd-svgzone');
  await send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
});
// 手机上点图上的 Y：头模式
await step('phone_head', async () => {
  const b = await box(key('north'));
  await sleep(650);                    // 同上：滚完 0.5 秒以后再点
  await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: b.x, y: b.y, id: 3 }] });
  await sleep(80);
  await send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await waitFor(`/摇杆管：头模式/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '手机上进了头模式');
  await shot('phone-3-head.png', '.pd-live', '.pd-vbar');
  const back = await box(key('north'));
  await sleep(650);
  await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: back.x, y: back.y, id: 4 }] });
  await sleep(80);
  await send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await waitFor(`/摇杆管：走路/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '手机上回到走路');
});
await fakeSet({ pad_connected: true });
await waitFor(`/实体手柄连着/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '手机上镜像', 8000);
await js(`window.__fakeLive = ${FAKE_LIVE}`);
await sleep(400);
await shot('phone-4-mirror.png', '.pd-live', '.pd-vbar');
await js('window.__fakeLive = null');
out.phone_mirror = await js(`({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })`);
await fakeSet({ pad_connected: false });

out.binding_layouts = [];
for (const width of [320, 375, 1366]) {
  await open(width, 900, false);
  await js(`(() => {
    const panel = [...document.querySelectorAll('details')].find(e => e.querySelector('summary')?.textContent.includes('按键绑定与功能开关'));
    if (!panel) throw new Error('Missing binding settings');
    panel.open = true;
  })()`);
  await waitFor(`!!document.querySelector('select[aria-label="LB 功能"]')`, '按键绑定设置出现');
  out.binding_layouts.push(await js(`(() => {
    const select = document.querySelector('select[aria-label="LB 功能"]');
    const stop = document.querySelector('.estop').getBoundingClientRect();
    return {width: innerWidth, scroll: document.documentElement.scrollWidth,
      stopVisible: stop.left >= 0 && stop.right <= innerWidth,
      autoDisabled: document.querySelector('.auto-btn').disabled,
      choices: [...select.options].map(o => o.value)};
  })()`));
  await shot(`bindings-${width}.png`, 'full');
}

process.stdout.write(JSON.stringify(out));
ws.close();
await fetch(`http://127.0.0.1:${CDP}/json/close/${page.id}`);
