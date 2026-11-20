// 舵机页两件事的端到端（演示舵机 --servo-sim，连本机无头 Edge）：
//   1. 读数那一格能输数字：回车才动、按「用时」转过去、Esc 取消、超范围夹住；
//   2. 新舵机编号：把 21 号改成出厂的 1 号冒充新舵机，找舵机、选位置、写 ID。
// 结果用 JSON 打到 stdout，由 tests/test_servo_tools_e2e.py 判断。Node >= 22，不需要装包。
import { mkdirSync, writeFileSync } from 'node:fs';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg('url');
const CDP = arg('cdp');
const SHOTS = arg('shots', '');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => process.stderr.write(`[servo-tools-e2e] ${a.join(' ')}` + '\n');

const page = await (await fetch(`http://127.0.0.1:${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const waiting = new Map();
const errors = [];
const dialogs = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id).ok(m); waiting.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  if (m.method === 'Page.javascriptDialogOpening') { dialogs.push(m.params.message); send('Page.handleJavaScriptDialog', { accept: true }); }
});
const send = (method, params = {}) => new Promise((ok, fail) => {
  const i = ++id;
  const timer = setTimeout(() => { waiting.delete(i); fail(new Error(`CDP ${method} 15 秒没回`)); }, 15000);
  waiting.set(i, { ok: (m) => { clearTimeout(timer); ok(m); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const js = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(`页面里出错：${r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text}`);
  return r.result?.result?.value;
};
const waitFor = async (expr, what, timeout = 12000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await js(expr)) return true; await sleep(100); }
  const now = await js(`[location.hash, document.querySelector('.sv-sub')?.innerText, document.querySelector('.sv-modal')?.innerText?.slice(0, 400)].join(' ／ ')`);
  throw new Error(`${timeout / 1000} 秒没等到：${what}。页面上现在：${now}。页面错误：${errors.slice(-4).join(' ｜ ')}`);
};
const shot = async (name) => {
  if (!SHOTS) return;
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(`${SHOTS}/${name}`, Buffer.from(r.result.data, 'base64'));
};
const click = (sel, text, exact = true) => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})]
  .find((x) => (${exact} ? x.textContent.trim() === ${JSON.stringify(text)} : x.textContent.includes(${JSON.stringify(text)})) && !x.disabled);
  if (!b) return false; b.scrollIntoView({ block: 'center' }); b.click(); return true; })()`);
const must = async (ok, what) => { if (!await ok) throw new Error(`找不到：${what}`); };
// 第 n 号舵机那一行
const row = (n) => `[...document.querySelectorAll('.sv-list .sv-row')].find((x) => x.querySelector('.sv-id')?.textContent.trim() === '${n}')`;
const reading = (n) => js(`+(${row(n)}?.querySelector('.sv-num')?.value)`);
const holding = (n) => js(`${row(n)}?.querySelector('.sv-switch')?.classList.contains('on')`);
// 真的敲键盘：点进格子、全选、打字，再按一个键
const type = async (n, text, key) => {
  await js(`(() => { const e = ${row(n)}.querySelector('.sv-num'); e.scrollIntoView({ block: 'center' }); e.focus(); e.select(); })()`);
  await send('Input.insertText', { text });
  const code = { Enter: 13, Escape: 27 }[key];
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code: key, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code: key, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
};

await send('Page.enable');
await send('Runtime.enable');
await send('Page.bringToFront');
await send('Emulation.setFocusEmulationEnabled', { enabled: true });
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${BASE}#/servos` });
const out = {};

log('等舵机页接管（演示舵机）');
await waitFor(`/调舵机中/.test(document.querySelector('.sv-sub')?.innerText || '')`, '舵机页接管', 25000);
await waitFor(`document.querySelectorAll('.sv-list .sv-num').length === 15`, '15 行都有数字格', 15000);
out.inputs = await js(`document.querySelectorAll('.sv-list .sv-num').length`);

log('没上力时输数字：不动，给提示');
await waitFor(`!!${row(21)}?.querySelector('.sv-num')?.value && !${row(21)}.classList.contains('stale')`, '21 号有实际位置回读');
const limp = await reading(21);
await type(21, String(limp + 100), 'Enter');
await sleep(600);
out.limp = { before: limp, after: await reading(21), hint: await js(`/扭矩没开/.test(document.body.innerText)`) };

log('全部扭矩开 → 站姿');
await must(click('.sv-toolbar button', '全部扭矩开'), '全部扭矩开');
await waitFor(`${row(21)}?.querySelector('.sv-switch')?.classList.contains('on')`, '21 号保持住', 10000);
await must(click('.sv-toolbar button', '站姿'), '站姿');
await sleep(3200);                                        // 默认用时 2 秒
const stand = await reading(21);
out.stand = stand;

log('输数字 + 回车：转过去');
const tuned = stand + 40;
await type(21, String(tuned), 'Enter');
await waitFor(`+(${row(21)}?.querySelector('.sv-num')?.value) === ${tuned} && document.activeElement?.className.indexOf('sv-num') < 0`, '21 号转到输入的数', 8000);
await sleep(900);
out.typed = { want: tuned, got: await reading(21) };

log('点「+」两下、「−」一下：每下 1°（约 11 步），连点累加');
const nudgeAt = (n, sign) => js(`(() => { const b = [...${row(n)}.querySelectorAll('.sv-nudge')][${sign > 0 ? 1 : 0}]; b.click(); return true; })()`);
await nudgeAt(21, 1); await sleep(150); await nudgeAt(21, 1);
await waitFor(`+(${row(21)}?.querySelector('.sv-num')?.value) === ${tuned} + 22`, '点两下 + 以后读数 +22', 8000);
await nudgeAt(21, -1);
await waitFor(`+(${row(21)}?.querySelector('.sv-num')?.value) === ${tuned} + 11`, '再点一下 − 以后读数 +11', 8000);
await sleep(1500);
out.nudged = await reading(21);
await nudgeAt(21, -1);
await waitFor(`+(${row(21)}?.querySelector('.sv-num')?.value) === ${tuned}`, '回到原来的数', 8000);
await sleep(1500);

log('输到一半按 Esc：不动');
await type(21, '1234', 'Escape');
await sleep(700);
out.escaped = await reading(21);

log('超范围：夹到 4095（用头部偏航 32，不影响站姿）');
const head = await reading(32);
await type(32, '99999', 'Enter');
await waitFor(`+(${row(32)}?.querySelector('.sv-num')?.value) === 4095`, '32 号夹到 4095', 8000);
out.clamped = await reading(32);
await shot('servo-typed.png');
await type(32, String(head), 'Enter');                     // 转回去：后面只该挪 21 这一颗
await waitFor(`+(${row(32)}?.querySelector('.sv-num')?.value) === ${head}`, '32 号转回去', 8000);
await sleep(900);

log('新舵机编号：先用「改 ID」把 21 号改成出厂的 1 号，冒充一颗刚拆封的');
const choose = (sel, value) => js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false;
  e.value = ${JSON.stringify(String(value))}; e.dispatchEvent(new Event('change', { bubbles: true })); return e.value === ${JSON.stringify(String(value))}; })()`);
await js(`document.querySelector('.sv-more-area').open = true`);
await sleep(300);
// 「更多」里的舵机选择框是 .sv-more-grid 里最后一个 .sv-pick；它后面那一行是「改 ID」
await must(js(`(() => { const p = [...document.querySelectorAll('.sv-more-grid .sv-pick')].pop(); if (!p) return false;
  p.value = '21'; p.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`), '「更多」里的舵机选择框');
await sleep(300);
await must(js(`(() => { const s = [...document.querySelectorAll('.sv-more-grid select')].find((x) => [...x.options].some((o) => o.textContent === '选新 ID'));
  if (!s) return false; s.value = '1'; s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`), '改 ID 的新编号下拉框');
await sleep(300);
await must(click('.sv-more-grid button', '写 ID'), '改 ID 的「写 ID」');
await waitFor(`/改好了，新 ID ping 得通/.test(document.querySelector('.sv-more-grid')?.innerText || '')`, '21 → 1 改好', 15000);

log('找舵机：应该看到 1 号还没编号、21 号位置空着');
await must(click('.sv-numbering button', '找舵机'), '找舵机');
await waitFor(`!!document.querySelector('.sv-found .sv-chip')`, '找舵机的结果', 40000);
out.numbering = await js(`(() => { const n = document.querySelector('.sv-numbering');
  return { chips: [...n.querySelectorAll('.sv-found .sv-chip')].map((c) => c.textContent.trim().replace(/\s+/g, ' ')),
    fresh: [...n.querySelectorAll('.sv-found .sv-chip.new')].map((c) => c.dataset.id),
    from: n.querySelector('.sv-from').value, to: n.querySelector('.sv-to').value,
    open: [...n.querySelectorAll('.sv-to option')].filter((o) => !o.disabled && o.value).map((o) => o.value) }; })()`);
await shot('servo-numbering-found.png');

log('写 ID：1 → 21');
await must(click('.sv-numbering button', '写 ID'), '编号的「写 ID」');
await waitFor(`!!document.querySelector('.sv-number-note')`, '编号结果', 40000);
out.numbered = await js(`(() => { const n = document.querySelector('.sv-numbering');
  return { note: n.querySelector('.sv-number-note').textContent, fresh: n.querySelectorAll('.sv-found .sv-chip.new').length,
    chips: n.querySelectorAll('.sv-found .sv-chip').length }; })()`);
await waitFor(`!(${row(21)}?.classList.contains('stale'))`, '21 号重新在线', 10000);
await shot('servo-numbering-done.png');
out.dialogs = dialogs;

log('手机宽度截图');
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await sleep(800);
await js(`document.querySelector('.sv-list')?.scrollIntoView({ block: 'start' })`);
await sleep(400);
out.mobile = await js(`(() => { const e = ${row(21)}.querySelector('.sv-num').getBoundingClientRect();
  return { w: Math.round(e.width), h: Math.round(e.height), inView: e.right <= innerWidth + 1 && e.left >= 0,
    scrollX: document.documentElement.scrollWidth > innerWidth + 1 }; })()`);
await shot('servo-mobile.png');

out.errors = errors;
process.stdout.write(JSON.stringify(out));
ws.close();
