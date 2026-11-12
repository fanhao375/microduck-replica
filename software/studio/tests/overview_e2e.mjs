// 总览重排（overview-merge-plan.md §5，审查 overview-merge-review.md O1–O18）的端到端：连本机无头 Edge（CDP），
// 打开 Studio 的总览（假 robotd 回放二号鸭的数据），看主板温度格、上手清单、手柄区块，再做几件会出事的操作——
// 按着摇杆时切页 / 滚走、在舞台上滚滚轮、手机上从摇杆外圈 / 动作按钮 / 图上的 A 起手往下刷——看假 robotd 收到了什么。
// 每一步的结果用 JSON 打到 stdout，由 tests/test_overview_e2e.py 判断对不对。
//
// --phase main：上面这些；--phase done：Studio 带着「6 步都做过」的记录重启以后，看清单收成一行。
// 单独跑（先起好假 robotd、Studio、无头 Edge；端口别跟别人的撞）：
//   node tests/overview_e2e.mjs --url http://127.0.0.1:18090/ --cdp 9343 --record <rd.jsonl> --fake 17022 --phase main [--shots <截图目录>]
// Node ≥ 22（自带 WebSocket），不需要装任何包。
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import net from 'node:net';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg('url');                 // http://127.0.0.1:端口/
const CDP = arg('cdp');
const REC = arg('record');
const FAKE = Number(arg('fake'));
const PHASE = arg('phase', 'main');
const SHOTS = arg('shots', '');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => process.stderr.write(`[overview-e2e] ${a.join(' ')}\n`);

// ---------------- 假 robotd 记下的东西（只看「让鸭子动 / 出声」的） ----------------
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
const studioLogs = async () => ((await (await fetch(`${BASE}api/logs?limit=500`)).json()).items || []).map((e) => e.msg);

// ---------------- CDP ----------------
const page = await (await fetch(`http://127.0.0.1:${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const waiting = new Map();
const errors = [];
const dialogs = [];                  // 页面弹过的浏览器对话框（舵机页离开时不该再弹「确定离开？」）
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Page.javascriptDialogOpening') { dialogs.push(m.params.message); send('Page.handleJavaScriptDialog', { accept: true }); }
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(`页面里出错：${r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text}`);
  return r.result?.result?.value;
};
const waitFor = async (expr, what, timeout = 8000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await js(expr)) return true; await sleep(100); }
  const now = await js(`[location.hash, scrollY, window.__pad && __pad.active, document.querySelector('.onb')?.innerText?.slice(0, 300),
    document.querySelector('.band')?.innerText?.replace(/\\s+/g, ' ')].join(' ／ ')`);
  throw new Error(`${timeout / 1000} 秒没等到：${what}。页面上现在：${now}`);
};
const box = (sel, scroll = true) => js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null;
  ${scroll ? "e.scrollIntoView({ block: 'center', inline: 'nearest' });" : ''} const r = e.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, l: r.x, t: r.y }; })()`);
const key = (r) => `.pd-svg-live [data-role="${r}"]`;
const mouse = (type, x, y, buttons = 1, extra = {}) => send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons, clickCount: 1, ...extra });
const touch = (type, pts) => send('Input.dispatchTouchEvent', { type, touchPoints: pts });
const sw = () => js(`({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })`);
let VIEW = { w: 1440, h: 900, mobile: false };

async function setView(w, h, mobile) {
  VIEW = { w, h, mobile };
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send('Emulation.setTouchEmulationEnabled', { enabled: !!mobile, maxTouchPoints: mobile ? 5 : 1 });
}
async function open(hash, w, h, mobile) {
  // 无头 Edge 里新开的标签页可能被当成「在后台」：拉到前面、模拟有焦点
  await send('Page.bringToFront');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await setView(w, h, mobile);
  await send('Page.navigate', { url: 'about:blank' });
  await sleep(300);
  await send('Page.navigate', { url: `${BASE}${hash}` });
  await waitFor(`!!document.querySelector('.onb') && !!document.querySelector('#pad-block') && !!document.querySelector('.band-cpu')`, '总览出来');
  await waitFor(`/Start 现在会：[^—]|实体手柄连着/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '收到手柄的状态');
  await sleep(800);
}
// 截图。full：整页（把视口拉到整页那么高再截：手机底栏、吸顶的顶栏都在该在的地方）；sel：一个元素
async function shot(name, sel) {
  if (!SHOTS) return;
  let clip;
  if (sel === 'full') {
    const orig = { ...VIEW };
    await js('window.scrollTo(0, 0)');
    await sleep(200);
    const h = await js('Math.ceil(document.documentElement.scrollHeight)');
    await setView(orig.w, h, orig.mobile);
    await sleep(900);                                 // 等 3D 鸭子画出来
    const r = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(SHOTS, name), Buffer.from(r.result.data, 'base64'));
    await setView(orig.w, orig.h, orig.mobile);
    await sleep(300);
    log('截图', name);
    return;
  }
  if (sel) {
    const r = await js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); const r = e.getBoundingClientRect();
      return { x: r.x + scrollX - 8, y: r.y + scrollY - 8, width: r.width + 16, height: r.height + 16 }; })()`);
    clip = { ...r, scale: 1 };
  }
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: !!clip, ...(clip ? { clip } : {}) });
  writeFileSync(join(SHOTS, name), Buffer.from(r.result.data, 'base64'));
  log('截图', name);
}
const moves = (rs) => rs.filter((r) => r.method === 'robot.move').map((r) => r.params);
const nonzero = (p) => Object.values(p).some((v) => typeof v === 'number' && Math.abs(v) > 1e-6);
const onbMarks = () => js(`[...document.querySelectorAll('.onb-row')].map((r) => r.querySelector('.onb-mark').textContent.trim())`);

// 代码审查 C7 那一步要：把 Studio 回给页面的 result 拖 300 ms 再交给页面（模拟 WiFi 慢），并记下页面发出的 pad.op。
// 只包一层 WebSocket，Studio 那边收到、发出的一样
const INJECT = `(() => {
  const NWS = window.WebSocket;
  window.__sentOps = [];
  window.__delayResults = 0;
  class W extends NWS {
    send(d) {
      try { const m = JSON.parse(d); if (m.t === 'cmd' && m.name === 'pad.op') window.__sentOps.push({ at: performance.now(), op: m.args && m.args.op }); } catch (e) { /* 不是 JSON */ }
      return super.send(d);
    }
    set onmessage(fn) {
      super.onmessage = (ev) => {
        const d = ev.data;
        if (window.__delayResults && typeof d === 'string' && d.startsWith('{"t":"result"')) { setTimeout(() => fn({ data: d }), window.__delayResults); return; }
        fn(ev);
      };
    }
    get onmessage() { return super.onmessage; }
  }
  window.WebSocket = W;
})();`;
await send('Runtime.enable');
await send('Page.enable');
await send('Page.addScriptToEvaluateOnNewDocument', { source: INJECT });
const out = { phase: PHASE, errors, steps: {} };

if (PHASE === 'done') {
  // Studio 带着「5 步都做过」的记录重启（IMU 那步是跳过的）；实体手柄连着（假 configd），测急停记录里就是这只手柄、这个主程序版本
  await open('#/', 1440, 900, false);
  await js(`localStorage.removeItem('studio.onb.open')`);      // Edge 的 profile 可能是上一次留下的：从收起开始
  await open('#/', 1440, 900, false);
  await waitFor(`!!document.querySelector('.onb-done')`, '清单收成一行', 10000);
  out.done = await js(`document.querySelector('.onb-done').innerText.replace(/\\s+/g, ' ')`);
  await shot('onb-all-done.png', '.onb');
  await shot('skip-all-done-collapsed.png', '.onb');
  await js('window.scrollTo(0, 0)');
  await sleep(300);
  await shot('desktop-1440-all-done-viewport.png');
  // 收成一行的那一行能点开：展开回 5 行（✓ 的有「重新做」），再点收起；开没开记在 localStorage
  await js(`document.querySelector('.onb-done .onb-fold').click()`);
  await waitFor(`document.querySelectorAll('.onb-row').length === 6`, '展开回 6 行');
  out.expanded = await js(`({ rows: document.querySelectorAll('.onb-row').length,
    redo: document.querySelectorAll('.onb-row.onb-ok .onb-redo').length, skip: document.querySelectorAll('.onb-row.onb-skip').length,
    head: document.querySelector('.onb-fold-h')?.innerText.replace(/\\s+/g, ' ') })`);
  await shot('skip-all-done-expanded.png', '.onb');
  await open('#/', 1440, 900, false);                           // 刷新以后还是展开的
  out.expandedAfterReload = await js(`document.querySelectorAll('.onb-row').length`);
  await js(`document.querySelector('.onb-fold-h').click()`);
  await waitFor(`!!document.querySelector('.onb-done')`, '收起');
  await open('#/', 1440, 900, false);
  out.collapsedAfterReload = await js(`!!document.querySelector('.onb-done') && document.querySelectorAll('.onb-row').length === 0`);
} else {
  // ================= 电脑 1440 宽 =================
  await open('#/', 1440, 900, false);
  out.desk = await js(`({ cells: document.querySelectorAll('.band > *').length, padActive: __pad.active,
    rows: document.querySelectorAll('.onb-row').length, now: document.querySelector('.onb-now .onb-title b')?.textContent,
    cpu: document.querySelector('.band-cpu').innerText.replace(/\\s+/g, ' '), nav: document.querySelector('.nav [aria-current]')?.textContent })`);
  out.desk.marks = await onbMarks();
  await shot('desktop-1440-full.png', 'full');
  await shot('onb-incomplete.png', '.onb');

  // 舞台上滚滚轮：滚的是页面，不是缩放 3D（审查 O2）
  await js('window.scrollTo(0, 0)');
  await sleep(200);
  const st = await box('.stage', false);
  await mouse('mouseMoved', st.x, st.y, 0);
  await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: st.x, y: st.y, deltaX: 0, deltaY: 400 });
  await sleep(500);
  out.wheel = await js('scrollY');

  // #/pad：滚到手柄那一块（扣掉吸顶的顶栏），算「在手柄区」，导航亮「手柄」（审查 O13）
  await js(`location.hash = '#/pad'`);
  await waitFor(`__pad.active`, '#/pad 进了手柄区');
  await sleep(400);
  out.padNav = await js(`({ top: Math.round(document.getElementById('pad-block').getBoundingClientRect().top), active: __pad.active,
    nav: document.querySelector('.nav [aria-current]')?.textContent, topbar: Math.round(document.querySelector('.topbar').getBoundingClientRect().top),
    band: [Math.round(document.querySelector('.overview > .band').getBoundingClientRect().top), Math.round(document.querySelector('.overview > .band').getBoundingClientRect().bottom)],
    navTop: Math.round(document.querySelector('.nav').getBoundingClientRect().top) })`);
  await shot('pad-block-desktop.png');

  // 按着摇杆时切到别的页：马上停、发 drive off（审查 O1）；之后手还在动也不再走
  const offs = async (why) => (await studioLogs()).filter((m) => m.includes(`离开了手柄区：${why}`)).length;
  const logs0 = await offs('切到了别的页');
  since();
  let b = await box(key('ls'));
  await mouse('mouseMoved', b.x, b.y, 0);
  await mouse('mousePressed', b.x, b.y);
  for (let i = 1; i <= 5; i++) { await mouse('mouseMoved', b.x, b.y - 8 * i); await sleep(20); }
  await sleep(400);
  const before = since();
  await js(`location.hash = '#/logs'`);
  await sleep(300);
  const atLeave = since();
  for (let i = 1; i <= 4; i++) { await mouse('mouseMoved', b.x + 3 * i, b.y - 45); await sleep(60); }
  await sleep(400);
  const after = since();
  await mouse('mouseReleased', b.x, b.y - 45, 0);
  const logs1 = await offs('切到了别的页');
  out.steps.route_leave = { before, atLeave, after, active: await js('__pad.active'), offLogged: logs1 > logs0 };

  // 回到 #/pad，按着摇杆时把手柄那一块滚出视口：一样（审查 O1）
  await js(`location.hash = '#/pad'`);
  await waitFor(`__pad.active`, '回到手柄区');
  await sleep(600);
  b = await box(key('ls'));
  await sleep(200);
  await mouse('mouseMoved', b.x, b.y, 0);
  await mouse('mousePressed', b.x, b.y);
  for (let i = 1; i <= 5; i++) { await mouse('mouseMoved', b.x, b.y - 8 * i); await sleep(20); }
  await sleep(400);
  const before2 = since();
  await js('window.scrollTo(0, 0)');
  await sleep(400);
  const atScroll = since();
  for (let i = 1; i <= 4; i++) { await mouse('mouseMoved', b.x, b.y - 45 - 3 * i); await sleep(60); }
  await sleep(400);
  const after2 = since();
  await mouse('mouseReleased', b.x, b.y - 45, 0);
  const logs2 = await offs('滚出了手柄这一块');
  out.steps.scroll_out = { before: before2, atScroll, after: after2, active: await js('__pad.active'), offLogged: logs2 > 0 };

  // 代码审查 C3：键盘按住 W（浏览器自动重复），滚出手柄区会停；滚回来以后重复来的按下不能让它接着走（要真按一次）
  await js(`document.getElementById('pad-block').scrollIntoView({ block: 'start' })`);   // 地址已经是 #/pad：自己滚回去
  await waitFor(`__pad.active`, '回到手柄区（键盘）');
  await sleep(700);
  const kz = await box('.pd-svgzone', false);
  await mouse('mouseMoved', kz.l + 6, kz.t + 6, 0);
  await mouse('mousePressed', kz.l + 6, kz.t + 6);
  await mouse('mouseReleased', kz.l + 6, kz.t + 6, 0);
  await sleep(200);
  const W = (repeat) => send('Input.dispatchKeyEvent', { type: 'keyDown', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87, autoRepeat: repeat });
  since();
  await W(false);
  for (let i = 0; i < 12; i++) { await sleep(50); await W(true); }
  await sleep(150);
  const kHeld = since();
  await js('window.scrollTo(0, 0)');
  await sleep(400);
  for (let i = 0; i < 8; i++) { await sleep(50); await W(true); }
  const kOut = since();
  await js(`document.getElementById('pad-block').scrollIntoView({ block: 'start' })`);
  await sleep(700);
  const kActive = await js('__pad.active');
  for (let i = 0; i < 14; i++) { await sleep(50); await W(true); }
  await sleep(300);
  const kBack = since();
  await send('Input.dispatchKeyEvent', { type: 'keyUp', code: 'KeyW', key: 'w', windowsVirtualKeyCode: 87 });
  await sleep(300);
  since();
  out.steps.kbd_repeat = { held: kHeld, out: kOut, back: kBack, activeBack: kActive,
    focus: await js(`!!document.activeElement?.closest?.('.pd-zone')`) };

  // 代码审查 C7：「动作」按下 → 松开 → 再按下，都在第一次回执回来之前（回执拖 300 ms）；最后松手以后不能再有 action_hold
  const hb = `[...document.querySelectorAll('.pd-hold')].find((x) => x.textContent.includes('点头') && !x.disabled)`;
  const hp = await js(`(() => { const e = ${hb}; if (!e) return null; e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
  if (!hp) throw new Error('找不到能按的「点头」（C7）');
  await sleep(400);
  await js('window.__delayResults = 300; window.__sentOps = []');
  await mouse('mouseMoved', hp.x, hp.y, 0);
  await mouse('mousePressed', hp.x, hp.y);
  await sleep(80);
  await mouse('mouseReleased', hp.x, hp.y, 0);
  await sleep(80);
  await mouse('mousePressed', hp.x, hp.y);
  await sleep(900);
  const tRel = await js('performance.now()');
  await mouse('mouseReleased', hp.x, hp.y, 0);
  await sleep(2000);
  const ops = await js('window.__sentOps');
  await js('window.__delayResults = 0');
  out.steps.hold_race = { holdsDuring: ops.filter((o) => o.op === 'action_hold' && o.at < tRel).length,
    holdsAfter: ops.filter((o) => o.op === 'action_hold' && o.at > tRel + 50).length, ops: ops.map((o) => o.op).join(',') };
  await sleep(500);

  // 手柄区的 3D 鸭子（用户 10-18：「手柄图的左边应该是 3D 视图、右边是手柄图」）：滚到手柄区时 3D 在视口里、在遥控卡的左边
  await js(`(() => { const s = document.querySelector('#pd-remote').getBoundingClientRect(); window.scrollTo(0, scrollY + s.top - 150); })()`);
  await sleep(900);
  out.pad3d = await js(`(() => { const c = document.querySelector('.pd-3d canvas').getBoundingClientRect(); const s = document.querySelector('#pd-remote').getBoundingClientRect();
    const vis = Math.max(0, Math.min(c.bottom, innerHeight) - Math.max(c.top, 56));
    return { visFrac: +(vis / c.height).toFixed(2), left: c.right <= s.left, cRight: Math.round(c.right), sLeft: Math.round(s.left),
      sticky: getComputedStyle(document.querySelector('.pd-3d')).position,
      stackSticky: getComputedStyle(document.querySelector('.pd-left')).position,
      cameraBelow: document.querySelector('.inline-camera').getBoundingClientRect().top >= document.querySelector('.pd-3d').getBoundingClientRect().bottom,
      touch: getComputedStyle(document.querySelector('.pd-3d canvas')).touchAction }; })()`);
  await shot('pad-3d-desktop.png');
  // 不在视口就不画：在视口时标脏会画，滚出去以后标脏不画（duck3d 的 frames 计数）
  const f3d = () => js(`document.querySelector('.pd-3d canvas').__duck3d.frames`);
  const dirty3d = () => js(`document.querySelector('.pd-3d canvas').__duck3d.dirty = true`);
  let fa = await f3d();
  out.pad3d.drawsInView = false;
  for (let i = 0; i < 20 && !out.pad3d.drawsInView; i++) {          // 机器忙（全套一起跑）时模型、IO 可能慢一点：最多等 4 秒
    await dirty3d(); await sleep(200);
    out.pad3d.drawsInView = (await f3d()) > fa;
  }
  await js('window.scrollTo(0, 0)');
  await sleep(1000);
  fa = await f3d();
  for (let i = 0; i < 5; i++) { await dirty3d(); await sleep(100); }
  out.pad3d.drawsOutOfView = (await f3d()) > fa;
  out.pad3d.activeAtTop = await js('__pad.active');

  // 上手清单的「去做」直接到位（审查 O17）
  await js('window.scrollTo(0, 0)');
  await sleep(300);
  // 直接在页面里点那个按钮（按坐标点的话，那一刻版面一动就点到旁边的「跳过」上了）
  const clickBtn = async (rowKeyText, label) => {
    const ok = await js(`(() => { const row = [...document.querySelectorAll('.onb-row')].find((r) => r.innerText.includes(${JSON.stringify(rowKeyText)}));
      const btn = row && [...row.querySelectorAll('button')].find((x) => x.textContent.includes(${JSON.stringify(label)}));
      if (!btn) return false; btn.scrollIntoView({ block: 'center' }); btn.click(); return true; })()`);
    if (!ok) throw new Error(`清单里找不到「${rowKeyText}」的「${label}」`);
    await sleep(700);
  };
  const inView = (sel) => `(() => { const e = document.querySelector('${sel}'); if (!e) return false; const r = e.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 56; })()`;
  await clickBtn('5 测急停', '去测急停');
  try { await waitFor(inView('#pd-estop'), '滚到测急停卡', 3000); } catch { /* 没到：下面照实记 */ }
  out.goto = { estop: await js(`({ inView: ${inView('#pd-estop')}, focus: document.activeElement?.id })`) };
  await js('window.scrollTo(0, 0)');
  await sleep(300);
  await clickBtn('4 配手柄', '配对手柄');
  try { await waitFor(inView('#pd-wizard'), '滚到配对向导', 3000); } catch { /* 没到：下面照实记 */ }
  out.goto.wizard = await js(`({ open: !!document.querySelector('#pd-wizard'), inView: ${inView('#pd-wizard')} })`);
  await js(`[...document.querySelectorAll('#pd-wizard button')].find((x) => x.textContent.includes('关掉'))?.click()`);
  await js('window.scrollTo(0, 0)');
  await sleep(300);
  await clickBtn('2 IMU 方向', '去 IMU 页');
  out.goto.imu = await js('location.hash');
  await js(`location.hash = '#/'`);
  await sleep(500);

  // ================= 已完成 / 跳过 / 取消 / 重新做（用户 10-18：「应该允许跳过、允许重新做」；10-20：「去做、已完成、跳过」） =================
  await js('window.scrollTo(0, 0)');
  await sleep(300);
  const rowState = (step) => js(`(() => { const r = document.querySelector('.onb-row[data-step="${step}"]'); return r ? [...r.classList].find((c) => /^onb-(ok|skip|todo|no|unk)$/.test(c)) : null; })()`);
  const dblAt = async (expr) => {
    const b = await js(`(() => { const e = ${expr}; if (!e) return null; e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
    if (!b) throw new Error(`双击：找不到 ${expr}`);
    await sleep(200);
    // 页面 0.5 秒刷新一次，量完坐标以后按钮可能被挤开（点到这一行的空白处）：点之前确认这个位置还是它，不是就重量
    for (let i = 0; i < 20; i++) {
      const hit = await js(`(() => { const e = ${expr}; const at = document.elementFromPoint(${b.x}, ${b.y}); return !!e && !!at && (at === e || e.contains(at)); })()`);
      if (hit) break;
      Object.assign(b, await js(`(() => { const e = ${expr}; const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`));
      await sleep(50);
    }
    out.dblAtRects = await js(`(() => { const row = document.querySelector('.onb-row[data-step="estop"]'); const rc = (sel) => { const e = row && row.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; }; return { done: rc('.onb-donebtn'), skip: rc('.onb-skip'), at: [${b.x}, ${b.y}] }; })()`);
    const mouse = (type, clickCount, buttons) => send('Input.dispatchMouseEvent', { type, x: b.x, y: b.y, button: 'left', buttons, clickCount });
    await mouse('mouseMoved', 0, 0);
    await mouse('mousePressed', 1, 1); await mouse('mouseReleased', 1, 0);
    await sleep(60);
    await mouse('mousePressed', 2, 1); await mouse('mouseReleased', 2, 0);
  };
  const rowBtn = async (step, label) => {
    const ok = await js(`(() => { const r = document.querySelector('.onb-row[data-step="${step}"]');
      const b = r && [...r.querySelectorAll('button')].find((x) => x.textContent.includes(${JSON.stringify(label)}) && !x.disabled);
      if (!b) return false; b.click(); return true; })()`);
    if (!ok) throw new Error(`清单「${step}」那行找不到能点的「${label}」`);
  };
  // 每一行三个按钮：去做、已完成、跳过（✓ 的没有）
  out.threeButtons = await js(`[...document.querySelectorAll('.onb-row')].filter((r) => !r.classList.contains('onb-ok'))
    .map((r) => ({ step: r.dataset.step, labels: [...r.querySelectorAll('.onb-acts > button, .onb-acts .two-step > button:first-child')].map((b) => b.textContent.trim()) }))`);
  // 舵机点「已完成」（没有后果，点一下：以前的「已经校过，核对一下」）→ ✓「你标了已完成」；IMU 跳过 → 「跳过了」，现在该做到第 4 行
  await rowBtn('servo', '已完成');
  await waitFor(`document.querySelector('.onb-row[data-step="servo"]')?.classList.contains('onb-ok')`, '舵机零位 ✓');
  out.marked = { text: await js(`document.querySelector('.onb-row[data-step="servo"] .onb-text').textContent`),
    buttons: await js(`[...document.querySelectorAll('.onb-row[data-step="servo"] .onb-acts button')].map((b) => b.textContent.trim())`) };
  await rowBtn('imu', '跳过');
  await waitFor(`document.querySelector('.onb-row[data-step="imu"]')?.classList.contains('onb-skip')`, 'IMU 变成「跳过了」');
  out.skip = { imu: await rowState('imu'), imuText: await js(`document.querySelector('.onb-row[data-step="imu"] .onb-text').textContent`),
    now: await js(`document.querySelector('.onb-now')?.dataset.step`), mark: await js(`document.querySelector('.onb-row[data-step="imu"] .onb-mark').textContent`) };
  await shot('skip-rows.png', '.onb');
  // 取消跳过：复原
  await rowBtn('imu', '取消跳过');
  await waitFor(`document.querySelector('.onb-row[data-step="imu"]')?.classList.contains('onb-todo')`, 'IMU 取消跳过');
  out.unskip = { imu: await rowState('imu'), now: await js(`document.querySelector('.onb-now')?.dataset.step`) };
  // ✓ 行的「重新做」：跟去做一样（舵机 → 舵机页）
  await rowBtn('servo', '重新做');
  await sleep(300);
  out.redoHash = await js('location.hash');
  await js(`location.hash = '#/'`);
  await waitFor(`/连上鸭子/.test(document.querySelector('.onb-h')?.innerText || '') && !document.querySelector('.onb-frozen')`, '交还主程序、清单恢复', 20000);
  await sleep(500);
  // 「已完成」的「取消」：复原（后面「读不到」那一步要看到 ?，标着的 ✓ 会把它盖住）
  await rowBtn('servo', '取消');
  await waitFor(`document.querySelector('.onb-row[data-step="servo"]')?.classList.contains('onb-todo')`, '舵机零位取消已完成');
  out.undone = await rowState('servo');
  // 测急停的跳过要点两次（3 秒内）：先连上一只实体手柄（测急停的跳过绑手柄 MAC）
  await fakeSet({ pad_connected: true });
  await waitFor(`(() => { const r = document.querySelector('.onb-row[data-step="estop"]'); const b = r && [...r.querySelectorAll('button')].find((x) => x.textContent === '跳过'); return b && !b.disabled; })()`,
    '测急停能跳过', 10000);
  // 两步确认（审查 D4）：第一下原按钮不动、旁边冒出确认按钮；700 ms 内的点击不算；3 秒不点就收起
  const estopSkipText = () => js(`(() => { const r = document.querySelector('.onb-row[data-step="estop"]');
    const a = r && r.querySelector('.two-step-ask'); return a ? a.textContent : r && r.querySelector('button.onb-skip')?.textContent; })()`);
  // 先双击（真鼠标）：不能直接跳过。机器忙的时候两下之间可能被拖过 700 ms 保护（第二下就成了「收起」），
  // 在页面里记下两下点击的真实间隔，超了就等它 3 秒自己复原、重新双击，最多 3 次
  await js(`(() => { window.__clicks = []; document.addEventListener('click', (e) => window.__clicks.push(performance.now()), true); window.__targets = []; document.addEventListener('click', (e) => { const t = e.target; window.__targets.push((t.className || t.tagName) + ':' + (t.textContent || '').slice(0, 12)); }, true); window.__arms = []; new MutationObserver(() => { const a = !!document.querySelector('.onb-row[data-step="estop"] .two-step-ask'); if (window.__arms[window.__arms.length - 1]?.[1] !== a) { const row = document.querySelector('.onb-row[data-step="estop"]'); const rc = (sel) => { const e = row && row.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; }; window.__arms.push([Math.round(performance.now()), a, rc('.onb-donebtn'), rc('.onb-skip'), rc('.two-step-ask'), getComputedStyle(row.querySelector('.two-step') || row).display]); } }).observe(document.body, { subtree: true, childList: true }); return true; })()`);
  for (let i = 0; i < 3; i++) {
    await js(`(() => { window.__clicks.length = 0; return true; })()`);
    await dblAt(`[...document.querySelectorAll('.onb-row[data-step="estop"] button')].find((x) => x.textContent === '跳过')`);
    await sleep(300);
    const gap = await js(`window.__clicks.length >= 2 ? window.__clicks[1] - window.__clicks[0] : null`);
    out.estopDblGap = gap;
    out.estopArmed300 = await js(`!!document.querySelector('.onb-row[data-step="estop"] .two-step-ask')`);
    out.estopClicks = await js(`window.__clicks.slice()`);
    out.estopTargets = await js(`window.__targets.slice(-4)`);
    out.estopArms = await js(`window.__arms.slice(-6)`);
    const onSkip = await js(`window.__targets.slice(-2).every((t) => t.includes('onb-skip'))`);
    if (gap != null && gap < 600 && onSkip) break;
    await sleep(3500);
  }
  await sleep(1200);
  out.estopDbl = { state: await rowState('estop'), armed: await js(`!!document.querySelector('.onb-row[data-step="estop"] .two-step-ask')`) };
  await sleep(2500);
  await rowBtn('estop', '跳过');
  await sleep(300);
  out.estopOnce = { text: await estopSkipText(), state: await rowState('estop'), skipStill: await js(`[...document.querySelectorAll('.onb-row[data-step="estop"] button.onb-skip')].map((b) => b.textContent)`) };
  await shot('skip-estop-confirm.png', '.onb-row[data-step="estop"]');
  await sleep(3300);
  out.estopTimeout = { text: await estopSkipText(), state: await rowState('estop') };
  await rowBtn('estop', '跳过');
  await sleep(900);
  await rowBtn('estop', '确定跳过');
  await waitFor(`document.querySelector('.onb-row[data-step="estop"]')?.classList.contains('onb-skip')`, '测急停跳过生效');
  out.estopTwice = await rowState('estop');
  await rowBtn('estop', '取消跳过');
  await waitFor(`!document.querySelector('.onb-row[data-step="estop"]')?.classList.contains('onb-skip')`, '测急停取消跳过');
  await fakeSet({ pad_connected: false });
  // IMU 方向主程序明说没确认：以前跳过灰着；现在能点，但要点两次，第二下按钮上写后果（用户 10-20：不再锁）
  await fakeSet({ imu_verified: false });
  await waitFor(`document.querySelector('.onb-row[data-step="imu"]')?.classList.contains('onb-no')`, 'IMU 变成「主程序没确认」', 10000);
  await rowBtn('imu', '跳过');
  await sleep(300);
  out.imuBlocked = await js(`(() => { const r = document.querySelector('.onb-row[data-step="imu"]');
    return { disabled: r.querySelector('button.onb-skip').disabled, ask: r.querySelector('.two-step-ask')?.textContent || null,
      state: [...r.classList].find((c) => /^onb-(ok|skip|todo|no|unk)$/.test(c)) }; })()`);
  await shot('skip-imu-two-step.png', '.onb');
  await sleep(3300);                                                   // 不点确认：3 秒后复原，还是 ○
  out.imuBlocked.after = await rowState('imu');
  await fakeSet({ imu_verified: true });
  await waitFor(`!/实体手柄连着/.test(document.querySelector('.pd-vbar')?.textContent || '')`, '实体手柄断开', 10000);
  await sleep(500);

  // ================= 电脑 1100 宽：仪表带 4 + 3 =================
  await open('#/', 1100, 900, false);
  out.d1100 = await js(`(() => { const c = [...document.querySelectorAll('.band > *')].map((e) => Math.round(e.getBoundingClientRect().top));
    return { tops: c, sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }; })()`);
  await shot('desktop-1100-full.png', 'full');

  // ================= 主板温度格：降频（红）、有点热（橙）、读不到（—） =================
  await open('#/', 1440, 900, false);
  await fakeSet({ cpu: { c: 92.4, level: 4, max_level: 6, khz: 1008000, max_khz: 1800000 } });
  await waitFor(`/降频了/.test(document.querySelector('.band-cpu').innerText)`, '主板格变红', 8000);
  out.cpu = { throttled: await js(`({ text: document.querySelector('.band-cpu').innerText.replace(/\\s+/g, ' '), bad: document.querySelector('.band-cpu').classList.contains('bad'),
    todo: document.querySelector('.todo')?.innerText.replace(/\\s+/g, ' ') })`) };
  await shot('band-throttled.png', '.band');
  await shot('band-throttled-with-todo.png', '.overview');
  await fakeSet({ cpu: { c: 82.3, level: 0, khz: 1800000 } });
  await waitFor(`/有点热/.test(document.querySelector('.band-cpu').innerText)`, '主板格变橙', 8000);
  out.cpu.hot = await js(`document.querySelector('.band-cpu').innerText.replace(/\\s+/g, ' ')`);
  await shot('band-hot.png', '.band');
  await fakeSet({ errors: { 'robot.health': { code: -32603, message: 'health 没回（测试）' } } });
  await waitFor(`/秒没更新/.test(document.querySelector('.band-cpu').innerText)`, '主板格写「—」', 15000);
  out.cpu.stale = await js(`document.querySelector('.band-cpu').innerText.replace(/\\s+/g, ' ')`);
  await shot('band-health-stale.png', '.band');
  await fakeSet({ errors: { 'robot.health': null }, cpu: { c: 76.875, level: 0, khz: 1800000 } });

  // ================= 手机 390 宽 =================
  await open('#/', 390, 844, true);
  out.phone = await sw();
  // 代码审查 C6：第一份状态到了以后，舞台画布的 touch-action 还是 pan-y（单指滚页面、两指转鸭子，不是整页缩放）
  out.phoneCanvas = await js(`(() => { const c = document.querySelector('.stage-ov canvas'); return { computed: getComputedStyle(c).touchAction, inline: c.style.touchAction }; })()`);
  await shot('phone-390-full.png', 'full');
  await open('#/pad', 390, 844, true);
  out.phone_pad = await sw();
  await shot('phone-pad-block.png');
  // 手机：3D 卡在遥控卡上面、矮一点、不 sticky
  out.phone3d = await js(`(() => { const c = document.querySelector('.pd-3d'); const r = document.querySelector('#pd-remote');
    return { above: c.getBoundingClientRect().bottom <= r.getBoundingClientRect().top, h: Math.round(c.querySelector('.pd-3d-stage').getBoundingClientRect().height),
      sticky: getComputedStyle(c).position }; })()`);
  await js(`document.querySelector('.pd-3d').scrollIntoView({ block: 'center' })`);
  await sleep(900);
  await shot('pad-3d-phone.png');
  // 从手柄图的空白处起手往上刷：是在滚页面，不推图上的摇杆（审查 O2）
  const swipe = async (x, y, dy, stepMs = 30) => {
    await touch('touchStart', [{ x, y, id: 1 }]);
    for (let i = 1; i <= 4; i++) { await touch('touchMove', [{ x, y: y - (dy * i) / 4, id: 1 }]); await sleep(stepMs); }
    await touch('touchEnd', []);
  };
  await step('swipe_pad_blank', async () => {
    const r = await box('.pd-svgbox');
    await sleep(700);
    await swipe(r.x - r.w * 0.47, r.y, 90);          // 图外面、SVG 里面的空白处（不是摇杆起手区）
  });
  // 从「动作」按钮上起手往上刷：不做动作；同一个按钮按住不动 0.4 秒：做（证明前面不是因为点不到）
  const holdBtn = `[...document.querySelectorAll('.pd-hold')].find((x) => x.textContent.includes('点头') && !x.disabled)`;
  const btnBox = async () => js(`(() => { const e = ${holdBtn}; if (!e) return null; e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
  await step('swipe_action', async () => {
    const p = await btnBox();
    if (!p) throw new Error('找不到能按的「点头」');
    await sleep(700);
    await swipe(p.x, p.y, 70);
  }, 800);
  await step('hold_action', async () => {
    const p = await btnBox();
    await sleep(700);
    await touch('touchStart', [{ x: p.x, y: p.y, id: 2 }]);
    await sleep(500);
    await touch('touchEnd', []);
  }, 800);
  // 页面刚滚过（惯性滑动）0.5 秒内按住按钮：不认
  await step('press_right_after_scroll', async () => {
    const p = await btnBox();
    await sleep(700);
    await js('window.scrollBy(0, 2)');
    await sleep(60);
    await touch('touchStart', [{ x: p.x, y: p.y - 2, id: 3 }]);
    await sleep(400);
    await touch('touchEnd', []);
  }, 800);
  // 从图上的 A 起手往上刷：不发技能；点一下：发（对照）
  await step('swipe_svg_a', async () => {
    const p = await box(key('south'));
    await sleep(700);
    await swipe(p.x, p.y, 70);
  }, 800);
  await step('tap_svg_a', async () => {
    const p = await box(key('south'));
    await sleep(700);
    await touch('touchStart', [{ x: p.x, y: p.y, id: 4 }]);
    await sleep(70);
    await touch('touchEnd', []);
  }, 1000);

  // ================= 读不到：configd 关着、主程序照常在跑但读标定的请求失败 → ? =================
  // （fork 的主程序在运动模式下只要进了控制阶段就一定读得到标定；真读不到标定时它会卡在等总线并报错，
  //   那是「主程序不让上力」，不是「不知道」。所以这里让 calibration-status 那一下请求出错。）
  await open('#/', 1440, 900, false);
  await fakeSet({ errors: { 'pad.status': { code: -32603, message: 'configd 关着（测试）' },
                            'robot.busCommand': { code: -32603, message: '读标定失败（测试）' } }, restart: 0.3 });
  await waitFor(`(() => { const m = [...document.querySelectorAll('.onb-row .onb-mark')].map((e) => e.textContent.trim()); return m[0] === '?' && m[1] === '?' && m[2] === '?' && m[3] === '?'; })()`,
    '清单出现 ?', 30000);
  out.unknown = await onbMarks();
  out.unknownText = await js(`document.querySelector('.onb').innerText.replace(/\\s+/g, ' ')`);
  await shot('onb-unknown.png', '.onb');
  await fakeSet({ errors: { 'pad.status': null, 'robot.busCommand': null }, restart: 0.3 });

  // ================= 舵机页：有舵机保持着时离开不交还（用户 10-18：「出去的时候应该保持住使能，不应该泄力」） =================
  await open('#/', 1440, 900, false);
  await waitFor(`/连上鸭子/.test(document.querySelector('.onb-h')?.innerText || '')`, '主程序重新连上', 15000);
  await fakeSet({ posture: 'holding' });                          // 策略在跑时舵机页不接管（关节会冻在半步）：先站好
  await sleep(1500);
  await js(`location.hash = '#/servos'`);
  await waitFor(`!![...document.querySelectorAll('.sv-page button')].find(b=>b.textContent.trim()==='开始调舵机'&&!b.disabled)`, '明确接管入口', 10000);
  await js(`[...document.querySelectorAll('.sv-page button')].find(b=>b.textContent.trim()==='开始调舵机').click()`);
  await waitFor(`/调舵机中/.test(document.querySelector('.sv-sub')?.innerText || '')`, '舵机页接管（演示舵机）', 15000);
  // 另开一条连接让两颗上力（会话是整个 Studio 共用的）
  await js(`(async () => { const w = new WebSocket(location.origin.replace('http', 'ws') + '/ws'); await new Promise((ok) => { w.onopen = ok; });
    w.send(JSON.stringify({ t: 'cmd', id: 'h1', name: 'servo.op', args: { op: 'torque', ids: [20, 21], on: 1 } }));
    await new Promise((z) => setTimeout(z, 800)); w.close(); })()`);
  await sleep(1500);
  const dialogs0 = dialogs.length;
  await js(`location.hash = '#/'`);
  await waitFor(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '')`, '别的页上出现舵机提示条', 10000);
  await js('window.scrollTo(0, 0)');
  await sleep(500);
  out.servoBar = { text: await js(`document.querySelector('.servo-bar').innerText.replace(/\\s+/g, ' ')`), dialogs: dialogs.length - dialogs0 };
  await shot('servo-hold-bar.png');
  // 往下滚到手柄区：提示条吸顶，还在屏幕上（审查 D6）
  await js(`document.getElementById('pd-remote').scrollIntoView({ block: 'center' })`);
  await sleep(600);
  out.servoBar.sticky = await js(`(() => { const r = document.querySelector('.servo-bar').getBoundingClientRect(); return { top: Math.round(r.top), visible: r.top >= 50 && r.bottom <= innerHeight }; })()`);
  out.servoBar.visualFits = await js(`(() => { const el=document.querySelector('.pd-left'), s=getComputedStyle(el); return s.position==='static' || el.getBoundingClientRect().height+parseFloat(s.top)+16<=innerHeight; })()`);
  // 手机 390：滚到遥控卡，提示条还在顶栏下面；顶栏有「调舵机中」小徽章；遥控卡写「调舵机中：主程序停着……」
  await open('#/', 390, 844, true);
  await waitFor(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '')`, '手机上的提示条', 10000);
  await js(`document.getElementById('pd-remote').scrollIntoView({ block: 'center' })`);
  await sleep(800);
  out.servoBar.phone = await js(`(() => { const r = document.querySelector('.servo-bar').getBoundingClientRect(); const b = document.querySelector('.topbar .sv-badge');
    return { top: Math.round(r.top), h: Math.round(r.height), visible: r.top >= 50 && r.bottom <= innerHeight, badge: b ? getComputedStyle(b).display !== 'none' && b.textContent : null,
      remote: /调舵机中：主程序停着，站走都不能用/.test(document.getElementById('pd-remote')?.innerText || ''),
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }; })()`);
  await shot('b3-phone-bar.png');
  // 页面关了重新打开（别的页）：会话还在，提示条照样有
  await open('#/imu', 1440, 900, false);
  out.servoBar.afterReload = await js(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '')`);
  // 「交还主程序」两步（审查 D4）：先真鼠标双击——不能直接交还；再正常点：第一下旁边冒出确认，700 ms 后点它才交还
  const handBtn = `[...document.querySelectorAll('.servo-bar button')].find((b) => b.textContent.includes('交还主程序'))`;
  await dblAt(handBtn);
  await sleep(1500);
  out.servoBar.dbl = { stillOpen: await js(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '')`),
    armed: await js(`!!document.querySelector('.servo-bar .two-step-ask')`) };
  await sleep(2500);
  out.servoBar.dbl.disarmed = await js(`!document.querySelector('.servo-bar .two-step-ask')`);
  await js(`${handBtn}.click()`);
  await sleep(300);
  out.servoBar.armed = await js(`document.querySelector('.servo-bar .two-step-ask')?.textContent`);
  out.servoBar.stillOpen = await js(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '')`);
  await js(`document.querySelector('.servo-bar .two-step-ask').click()`);          // 300 ms：还在 700 ms 内，不算
  await sleep(200);
  out.servoBar.earlyIgnored = await js(`/颗保持着/.test(document.querySelector('.servo-bar')?.innerText || '') && !!document.querySelector('.servo-bar .two-step-ask')`);
  await sleep(400);
  // 主程序假装是手动停的（假 robotd 8 秒不接连接）
  await fakeSet({ restart: 8 });
  await sleep(300);                                                // 假 robotd 0.05 秒后才「停」：等它停稳再交还（不然轮询抢先连上）
  await js(`document.querySelector('.servo-bar .two-step-ask').click()`);
  await waitFor(`/已交还/.test(document.querySelector('.servo-bar')?.innerText || '')`, '提示条换成「已交还」', 12000);
  out.servoBar.after = await js(`document.querySelector('.servo-bar').innerText.replace(/\\s+/g, ' ')`);
  await shot('servo-handback-bar.png', '.servo-bar');
  await sleep(2500);
  out.servoBar.stillThere = await js(`/已交还/.test(document.querySelector('.servo-bar')?.innerText || '')`);   // 主程序没连上：一直在（审查 D12）
  await waitFor(`!document.querySelector('.servo-bar')`, '主程序连上后提示条消失', 20000);
  out.servoBar.gone = true;
}

async function step(name, fn, settle = 500) {
  since();
  const extra = (await fn()) || {};
  await sleep(settle);
  out.steps[name] = { recs: since(), ...extra };
  log(name, out.steps[name].recs.length, '条');
}

process.stdout.write(JSON.stringify(out));
ws.close();
await fetch(`http://127.0.0.1:${CDP}/json/close/${page.id}`);
