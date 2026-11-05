// 出厂场景「写进主程序」的端到端（任务 13）：连本机无头 Edge（CDP），走完标定模式 -> 运动模式。
// 假 robotd 用 --mode commissioning --calib factory 起（出厂未标定）；Studio 由 tests/test_commit_e2e.py 用
// 一个把 studio.helper.calib_commit 换成假实现的启动脚本拉起（电脑上没有 root 小助手）。
//
// --phase factory：出厂状态的准备情况（按钮灰着）、预览能展开、每颗的方向 / 限位 / TOML 都在。
// --phase mark   ：第 1 步点「已完成」（主程序说没标：两步确认）→ ✓，第 3 步准备情况里「舵机零位」一项变绿（任务 16）。
// --phase imu    ：IMU 页粗校：托起摆正采静止 -> 往前低头采前倾 -> 算方向 -> 交给主程序 verify-imu。
// --phase servo  ：舵机页一键中位（--servo-sim），然后交还主程序。
// --phase commit ：第 3 步写进主程序；假小助手让假主程序切 motion + control，清单第 3 步 ✓、顶栏显示运动模式。
// --phase fail   ：假小助手让标定被拒；页面要能看到失败原因，而且断线期间进度卡不能被「断线时不检查」盖掉。
// --phase stance0：标定模式下 IMU 页的「站姿使能 / 失能」灰着、写清楚原因、能去第 3 步（任务 17）。
// --phase stance ：运动模式下「站姿使能」发出的命令跟遥控卡「站起来」一样，「失能」跟顶上「卸力」一样（任务 17）。
// 结果用 JSON 打到 stdout，由 tests/test_commit_e2e.py 判断。Node >= 22，不需要装包。
import { mkdirSync, writeFileSync } from 'node:fs';
import { createConnection } from 'node:net';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg('url');
const CDP = arg('cdp');
const FAKE = Number(arg('fake-port'));
const PHASE = arg('phase', 'factory');
const SHOTS = arg('shots', '');
const CONTROL = arg('control-file', '');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => process.stderr.write(`[commit-e2e] ${a.join(' ')}` + '\n');

// 测试控制口：给假 robotd / 假 configd 发 fake.set（同一个假对象）
const fakeRpc = (method, params) => new Promise((resolve, reject) => {
  const s = createConnection({ host: '127.0.0.1', port: FAKE }, () => {
    s.write(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) + '\n');
  });
  let buf = '', done = false;
  const timer = setTimeout(() => { if (!done) { done = true; s.destroy(); reject(new Error('fake.set 超时')); } }, 5000);
  s.on('data', (d) => {
    buf += d;
    const i = buf.indexOf('\n');
    if (i < 0 || done) return;
    done = true; clearTimeout(timer);
    const line = buf.slice(0, i); s.end();
    try {
      const m = JSON.parse(line);
      if (m.error) reject(new Error(JSON.stringify(m.error))); else resolve(m.result);
    } catch (e) { reject(e); }
  });
  s.on('error', (e) => { if (!done) { done = true; clearTimeout(timer); reject(e); } });
});
const fakeSet = (params) => fakeRpc('fake.set', params);

const page = await (await fetch(`http://127.0.0.1:${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const waiting = new Map();
const errors = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id).ok(m); waiting.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  // 浏览器自带的确认框（比如「它现在是躺着的……确定托好了吗？」）：照 overview_e2e 的做法点「确定」，记下问了什么
  if (m.method === 'Page.javascriptDialogOpening') { dialogs.push(m.params.message); send('Page.handleJavaScriptDialog', { accept: true }); }
});
const dialogs = [];
const send = (method, params = {}) => new Promise((ok, fail) => {
  const i = ++id;
  const timer = setTimeout(() => {
    waiting.delete(i);
    fail(new Error(`CDP ${method} 15 秒没回`));
  }, 15000);
  waiting.set(i, { ok: (m) => { clearTimeout(timer); ok(m); }, fail: (e) => { clearTimeout(timer); fail(e); } });
  ws.send(JSON.stringify({ id: i, method, params }));
});
ws.addEventListener('close', () => {
  for (const w of waiting.values()) w.fail(new Error('Edge 调试连接断了'));
  waiting.clear();
});
ws.addEventListener('error', () => log('CDP WebSocket 出错'));
const js = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(`页面里出错：${r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text}`);
  return r.result?.result?.value;
};
const waitFor = async (expr, what, timeout = 12000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await js(expr)) return true; await sleep(100); }
  const now = await js(`[location.hash, document.querySelector('[data-step="commit"]')?.innerText?.slice(0, 600), document.querySelector('.onb-h')?.innerText, document.querySelector('.imu-coarse')?.innerText?.slice(0, 400), document.querySelector('.sv-sub')?.innerText].join(' ／ ')`);
  throw new Error(`${timeout / 1000} 秒没等到：${what}。页面上现在：${now}。页面错误：${errors.slice(-4).join(' ｜ ')}`);
};
const shot = async (name) => {
  if (!SHOTS) return;
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(`${SHOTS}/${name}`, Buffer.from(r.result.data, 'base64'));
};
const clickExact = (sel, text) => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})]
  .find((x) => x.textContent.trim() === ${JSON.stringify(text)} && !x.disabled);
  if (!b) return false; b.scrollIntoView({ block: 'center' }); b.click(); return true; })()`);
const clickHas = (sel, text) => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})]
  .find((x) => x.textContent.includes(${JSON.stringify(text)}) && !x.disabled);
  if (!b) return false; b.scrollIntoView({ block: 'center' }); b.click(); return true; })()`);

log('连上 Edge，开始加载页面');
await send('Page.enable');
await send('Runtime.enable');
await send('Page.bringToFront');                                  // 页面要算「在前台」：后台时 Studio 1 Hz 推，清单判成断线
await send('Emulation.setFocusEmulationEnabled', { enabled: true });
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${BASE}#/` });
await sleep(800);

log('页面已打开');
const out = { phase: PHASE };
const card = `document.querySelector('[data-step="commit"] .onb-commit')`;
const snapshot = () => js(`(() => {
  const c = ${card};
  if (!c) return null;
  const checks = [...c.querySelectorAll('.onb-checks li')].map((li) => ({
    ok: li.classList.contains('ok'), text: li.innerText.replace(/\\s+/g, ' ').trim() }));
  const act = [...c.querySelectorAll('.onb-commit-acts button')];
  const write = act.find((b) => b.textContent.includes('写进主程序'));
  return {
    rowState: document.querySelector('[data-step="commit"] .onb-mark')?.getAttribute('aria-label'),
    desc: c.querySelector('.onb-commit-desc')?.innerText,
    checks, bad: checks.filter((x) => !x.ok).length,
    writeDisabled: !!(write && write.disabled),
    job: c.querySelector('.onb-commit-job')?.innerText || null,
    msg: c.querySelector('.onb-commit-msg')?.innerText || null,
    msgs: [...c.querySelectorAll('.onb-commit-msg')].map((e) => e.innerText.replace(/\\s+/g, ' ').trim()),
  };
})()`);

// IMU 页的「站姿使能 / 失能」（任务 17）：按钮状态、旁边那行字；页面发出去的命令（拦 WebSocket.send，只记 cmd）
const stance = () => js(`(() => { const c = document.querySelector('.imu-stance'); if (!c) return null;
  const b = (sel) => { const e = c.querySelector(sel); return e ? { text: e.textContent.trim(), disabled: e.disabled } : null; };
  return { stand: b('.imu-stand'), relax: b('.imu-relax'),
    now: c.querySelector('.imu-stance-now')?.innerText.replace(/\\s+/g, ' ').trim() || '',
    why: c.querySelector('.imu-stance-why')?.innerText.replace(/\\s+/g, ' ').trim() || null,
    goCommit: !!c.querySelector('.imu-go-commit'), text: c.innerText.replace(/\\s+/g, ' ').trim() }; })()`);
const recordSends = () => js(`(() => { if (window.__sent) { window.__sent.length = 0; return true; } window.__sent = [];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m.t === 'cmd') window.__sent.push({ name: m.name, args: m.args }); } catch (e) { /* 不是 JSON */ }
    return orig.call(this, d);
  };
  return true; })()`);
const sentStand = () => js(`(window.__sent || []).filter((m) => m.name === 'pad.op' && m.args && m.args.op === 'stand')`);
const remoteStand = `[...document.querySelectorAll('.pd-remote .pd-big')].find((x) => x.querySelector('b')?.textContent === '站起来')`;

if (PHASE === 'ssh') {
  // 「更多」页的 SSH 开关（10-21）：卡片在、写清出厂只认钥匙；电脑上没有 root 小助手，后端回「没装小助手」，卡片照实写，不报错
  await js(`location.hash = '#/settings'`);
  await waitFor(`!!document.querySelector('.ssh-settings')`, '更多页的 SSH 卡片', 20000);
  await waitFor(`!/正在读/.test(document.querySelector('.ssh-settings .ssh-now')?.innerText || '正在读')`, 'SSH 卡片读到状态', 20000);
  out.ssh = await js(`(() => { const c = document.querySelector('.ssh-settings');
    return { text: c.innerText.replace(/\\s+/g, ' ').trim(), now: c.querySelector('.ssh-now').innerText.trim(),
      onDisabled: c.querySelector('.ssh-on').disabled, needWebPw: !!c.querySelector('.ssh-need-web-pw'),
      inputs: c.querySelectorAll('input[type=password]').length }; })()`);
  await shot('settings-ssh.png');
}

if (PHASE === 'stance0') {
  // 标定模式也能站（用户 10-21）：「站姿使能」能点，确认框点确定以后发 imu.op hold（fork hold-home），站姿卡写「标定模式（站着）」；
  // 「失能（卸力）」也能点，发顶上同一个 relax，卸完回「标定模式（软着）」
  await js(`location.hash = '#/imu'`);
  await waitFor(`!!document.querySelector('.imu-stance')`, 'IMU 页的站姿卡', 20000);
  await waitFor(`/标定模式（软着）/.test(document.querySelector('.imu-stance .imu-stance-now')?.innerText || '')`, '站姿卡写着标定模式（软着）', 10000);
  await waitFor(`(() => { const b = document.querySelector('.imu-stance .imu-stand'); return !!b && !b.disabled; })()`,
    '标定模式：「站姿使能」能点', 10000);
  out.stance = await stance();
  await shot('imu-stance-commissioning.png');
  await recordSends();
  await js(`document.querySelector('.imu-stance .imu-stand').click()`);
  await waitFor(`/标定模式（站着）/.test(document.querySelector('.imu-stance .imu-stance-now')?.innerText || '')`, '站姿使能以后写着站着', 15000);
  out.holdSent = await js(`(window.__sent || []).filter((m) => m.name === 'imu.op')`);
  out.dialogs = dialogs.slice();
  out.holding = await stance();
  await shot('imu-stance-commissioning-holding.png');
  await recordSends();
  await js(`document.querySelector('.imu-stance .imu-relax').click()`);
  await waitFor(`/标定模式（软着）/.test(document.querySelector('.imu-stance .imu-stance-now')?.innerText || '')`, '失能以后回到软着', 15000);
  out.relaxSent = await js(`(window.__sent || []).filter((m) => m.name === 'relax')`);
  out.after = await stance();
}

if (PHASE === 'stance') {
  // 运动模式：软着时「站姿使能」能点，发出去的跟遥控卡「站起来」一模一样；「失能」跟顶上「卸力」一样
  await fakeSet({ mode: 'motion', calib: 'committed', posture: 'limp', restart: 0.4 });
  await sleep(3000);       // 刚卸力 2 秒内不接受让它动的指令（按钮会灰），手柄快照也 1 秒才刷新：等它过去
  await js(`location.hash = '#/imu'`);
  await waitFor(`!!document.querySelector('.imu-stance')`, 'IMU 页的站姿卡', 20000);
  await waitFor(`(() => { const b = document.querySelector('.imu-stance .imu-stand'); return !!b && !b.disabled; })()`,
    '运动模式软着：「站姿使能」能点', 20000);
  out.before = await stance();
  await shot('imu-stance-motion.png');
  await recordSends();
  // 按钮亮着那一刻才点（在页面里等，免得查和点之间它又灰了）
  await js(`new Promise((ok) => { const t0 = Date.now(); const t = setInterval(() => {
    const b = document.querySelector('.imu-stance .imu-stand');
    if (b && !b.disabled) { clearInterval(t); b.click(); ok(true); } else if (Date.now() - t0 > 10000) { clearInterval(t); ok(false); } }, 50); })`);
  // 假主程序不一定模拟「站起来」的过程：要验的是发出去的命令跟遥控卡「站起来」一样（那条路真机上是通的）
  await waitFor(`(window.__sent || []).some((m) => m.name === 'pad.op' && m.args && m.args.op === 'stand')`, '点了站姿使能以后发出站起来', 5000);
  out.standSent = await sentStand();
  await waitFor(`/现在：站着/.test(document.querySelector('.imu-stance .imu-stance-now')?.innerText || '')`,
    '第一条站立命令实际完成，再重置遥控场景', 15000);
  out.standing = await stance();
  await fakeSet({ posture: 'limp' });
  await sleep(3000);       // 同上：刚变软 2 秒内后端不接受站起来（按钮靠 1 秒一次的手柄快照，可能还亮着），点了会被拒
  await js(`location.hash = '#/pad'`);
  try {
    await waitFor(`(() => { const b = ${remoteStand}; return !!b && !b.disabled; })()`, '遥控卡「站起来」能点', 20000);
  } catch (e) {
    // 偶发（全套一起跑、机器忙时）：把遥控卡每个按钮的字、灰没灰、为什么灰都带上
    const why = await js(`[...document.querySelectorAll('.pd-remote .pd-big')].map((x) => (x.querySelector('b')?.textContent || '?') + '|' + (x.disabled ? '灰' : '亮') + '|' + (x.title || '')).join(' ; ')`);
    throw new Error(`${e.message} ／ 遥控卡按钮：${why}`);
  }
  await recordSends();
  await js(`new Promise((ok) => { const t0 = Date.now(); const t = setInterval(() => {
    const b = ${remoteStand};
    if (b && !b.disabled) { clearInterval(t); b.click(); ok(true); } else if (Date.now() - t0 > 10000) { clearInterval(t); ok(false); } }, 50); })`);
  await waitFor(`(window.__sent || []).some((m) => m.name === 'pad.op' && m.args && m.args.op === 'stand')`, '遥控卡发出站起来', 5000);
  out.remoteSent = await sentStand();
  await js(`location.hash = '#/imu'`);
  await waitFor(`(() => { const b = document.querySelector('.imu-stance .imu-relax'); return !!b && !b.disabled; })()`, '「失能（卸力）」能点', 10000);
  await recordSends();
  await js(`document.querySelector('.imu-stance .imu-relax').click()`);
  await waitFor(`(window.__sent || []).some((m) => m.name === 'relax')`, '失能发出卸力', 5000);
  out.relaxSent = await js(`(window.__sent || []).filter((m) => m.name === 'relax')`);
}

if (PHASE === 'mark') {
  // 出厂：舵机零位那一行主程序说 15 颗没标（hard）。以前「跳过」灰着、也没有「已完成」；现在点两次能标（用户 10-20）
  const servoRow = `document.querySelector('.onb-row[data-step="servo"]')`;
  const servoCheck = `[...(${card}?.querySelectorAll('.onb-checks li') || [])].find((li) => li.innerText.includes('舵机零位'))`;
  await waitFor(`!!${servoRow} && !!${card} && !!(${servoCheck})`, '第 1 步和第 3 步的准备情况', 20000);
  out.before = await js(`({ row: ${servoRow}.querySelector('.onb-mark').getAttribute('aria-label'), check: (${servoCheck}).className })`);
  const doneBtn = `[...${servoRow}.querySelectorAll('button')].find((b) => b.textContent.trim() === '已完成')`;
  if (!await js(`(() => { const b = ${doneBtn}; if (!b) return false; b.scrollIntoView({ block: 'center' }); b.click(); return true; })()`)) {
    throw new Error('舵机零位那一行找不到「已完成」');
  }
  await waitFor(`!!${servoRow}.querySelector('.two-step-ask')`, '「已完成」的第二下（写着后果）', 5000);
  out.ask = await js(`${servoRow}.querySelector('.two-step-ask').textContent`);
  out.afterOne = await js(`${servoRow}.querySelector('.onb-mark').getAttribute('aria-label')`);   // 点一下不算
  await shot('commit-mark-servo-ask.png');
  await sleep(900);                                                  // TwoStep 的 700 ms 保护
  await js(`${servoRow}.querySelector('.two-step-ask').click()`);
  await waitFor(`${servoRow}.querySelector('.onb-mark')?.getAttribute('aria-label') === '完成'`, '第 1 步变成 ✓', 10000);
  await waitFor(`(${servoCheck})?.classList.contains('ok')`, '第 3 步「舵机零位」变绿', 10000);
  out.after = await js(`({ row: ${servoRow}.querySelector('.onb-mark').getAttribute('aria-label'),
    text: ${servoRow}.querySelector('.onb-text').textContent, check: (${servoCheck}).innerText.replace(/\\s+/g, ' ').trim() })`);
  await shot('commit-mark-servo-done.png');
}

if (PHASE === 'imu-redo') {
  await js(`location.hash = '#/imu'`);
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.trim() === '重新标定方向' && !b.disabled)`,
    '已保存的 IMU 仍有重新标定方向入口', 10000);
  out.entryVisible = true;
  out.original = await fakeRpc('robot.busCommand', { action: 'calibration-status' });
  await recordSends();
  await clickExact('.imu-coarse button', '重新标定方向');
  out.beforeConfirm = await js(`(window.__sent || []).filter((m) => m.name === 'imu.op')`);
  await waitFor(`!!document.querySelector('.imu-coarse .two-step-ask')`, '重新标定需第二次确认', 5000);
  await sleep(900);
  await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
  await waitFor(`/重标定未结束/.test(document.querySelector('.imu-coarse')?.innerText || '') &&
    !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采静止') && !b.disabled)`, '回到标定模式原粗校流程', 15000);
  out.active = await stance();
  await shot('imu-redo-active.png');
  // Refreshing must recover the persistent session instead of hiding the verified mount.
  await send('Page.reload');
  await waitFor(`/重标定未结束/.test(document.querySelector('.imu-coarse')?.innerText || '') &&
    !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采静止') && !b.disabled)`, '刷新仍可继续原粗校', 15000);
  await recordSends();
  await fakeSet({ imu_pose: 'upright' });
  await clickHas('.imu-coarse button', '采静止');
  await waitFor(`/静止采到/.test(document.querySelector('.imu-coarse')?.innerText || '')`, '重新采到静止', 15000);
  await fakeSet({ imu_pose: 'nose_down' });
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采前倾') && !b.disabled)`, '前倾采集按钮就绪');
  if (!await clickHas('.imu-coarse button', '采前倾')) throw new Error('前倾采集未发出');
  await waitFor(`!!document.querySelector('.imu-coarse input[type=checkbox]')`, '重新算出安装方向', 20000);
  out.verifyInstruction = await js(`document.querySelector('.imu-coarse')?.innerText`);
  await js(`[...document.querySelectorAll('.imu-coarse input[type=checkbox]')].forEach((x) => { if (!x.checked) x.click(); })`);
  await fakeSet({ imu_pose: 'upright', imu_verify_plan: 'ok' });
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent === '交给主程序验证' && !b.disabled)`, '验证按钮就绪');
  if (!await clickHas('.imu-coarse button', '交给主程序验证')) throw new Error('验证未发出');
  await waitFor(`/运动模式/.test(document.querySelector('.topbar')?.innerText || document.querySelector('header')?.innerText || '') &&
    !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.trim() === '重新标定方向' && !b.disabled)`, '新方向验证通过且恢复运动模式', 45000);
  out.after = await fakeRpc('robot.busCommand', { action: 'calibration-status' });
  out.afterStance = await stance();
  out.motionCommands = await js(`(window.__sent || []).filter((m) => m.name === 'pad.op' || m.name === 'init' || (m.name === 'imu.op' && m.args.op === 'hold'))`);
  await shot('imu-redo-complete.png');
  // A second revalidation can be cancelled and must restore exactly that saved calibration.
  await clickExact('.imu-coarse button', '重新标定方向');
  await sleep(900);
  await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
  await waitFor(`/重标定未结束/.test(document.querySelector('.imu-coarse')?.innerText || '')`, '可以再次开始重标定', 15000);
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent === '取消并恢复原参数' && !b.disabled)`, '取消按钮就绪');
  if (!await clickExact('.imu-coarse button', '取消并恢复原参数')) throw new Error('取消未发出');
  await sleep(900);
  await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
  await waitFor(`/运动模式/.test(document.querySelector('.topbar')?.innerText || '') &&
    !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.trim() === '重新标定方向' && !b.disabled)`, '取消后恢复原模式', 15000);
  out.cancelled = await fakeRpc('robot.busCommand', { action: 'calibration-status' });
  out.recoveredPhases = [];
  for (const phase of ['entering', 'finishing', 'cancelling']) {
    await clickExact('.imu-coarse button', '重新标定方向');
    await sleep(900);
    await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
    await waitFor(`/重标定未结束/.test(document.querySelector('.imu-coarse')?.innerText || '') &&
      !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采静止') && !b.disabled)`, '重标定已进入');
    writeFileSync(CONTROL, JSON.stringify({ outcome: 'ok', redo_phase: phase }));
    await send('Page.reload');
    await waitFor(`/重标定未结束/.test(document.querySelector('.imu-coarse')?.innerText || '') &&
      !document.querySelector('.imu-coarse-steps') &&
      !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent === '取消并恢复原参数' && !b.disabled)`, `中断的 ${phase} 会话可取消`, 6000);
    await clickExact('.imu-coarse button', '取消并恢复原参数');
    await sleep(900);
    await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
    await waitFor(`/运动模式/.test(document.querySelector('.topbar')?.innerText || '') &&
      !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent === '重新标定方向' && !b.disabled)`, '恢复原运动模式');
    out.recoveredPhases.push(phase);
    writeFileSync(CONTROL, JSON.stringify({ outcome: 'ok' }));
  }
  // Already verified commissioning deployments also retain a direct revalidation entry.
  await fakeSet({ mode: 'commissioning', imu_pose: 'upright' });
  await waitFor(`/标定模式/.test(document.querySelector('.topbar')?.innerText || '') &&
    !![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.trim() === '重新标定方向' && !b.disabled)`, '已验证标定模式仍可重做', 15000);
  await recordSends();
  await clickExact('.imu-coarse button', '重新标定方向');
  await sleep(900);
  await js(`document.querySelector('.imu-coarse .two-step-ask').click()`);
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采静止'))`, '直接重开原粗校', 5000);
  out.commissioningCommands = await js(`(window.__sent || []).filter((m) => m.name === 'imu.op')`);
}

if (PHASE === 'imu' || PHASE === 'imu-hold') {
  log('IMU 页：粗校');
  if (PHASE === 'imu-hold') {
    await fakeSet({ mode: 'commissioning', calib: 'factory', posture: 'commissioning', imu_pose: 'upright', restart: 0.4 });
  }
  await js(`location.hash = '#/imu'`);
  await waitFor(`!!document.querySelector('.imu-coarse')`, 'IMU 粗校卡片', 20000);
  if (PHASE === 'imu-hold') {
    await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采静止') && !b.disabled)`,
      '重新读取出厂方向标定状态', 20000);
  }
  await js(`document.querySelector('.imu-stance .imu-relax').click()`);
  await sleep(500);
  if (PHASE === 'imu-hold') {
    await waitFor(`!!document.querySelector('.imu-stance .imu-stand') && !document.querySelector('.imu-stance .imu-stand').disabled`,
      '初次标定允许站姿使能', 10000);
    await js(`document.querySelector('.imu-stance .imu-stand').click()`);
    await waitFor(`/标定模式（站着）/.test(document.querySelector('.imu-stance .imu-stance-now')?.innerText || '')`,
      '站稳再做初次标定', 15000);
    out.holdBefore = await fakeRpc('robot.busStatus', {});
  }
  out.start = await js(`document.querySelector('.imu-coarse')?.innerText.replace(/\\s+/g, ' ').slice(0, 500)`);
  if (!await clickHas('.imu-coarse button', '采静止')) throw new Error('找不到「采静止」');
  await waitFor(`/静止采到/.test(document.querySelector('.imu-coarse')?.innerText || '')`, '静止采好', 20000);
  await fakeSet({ imu_pose: 'nose_down' });
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent.includes('采前倾') && !b.disabled)`,
    '静止采数结束，前倾采数按钮可用', 15000);
  if (!await clickHas('.imu-coarse button', '采前倾')) throw new Error('找不到「采前倾」');
  await waitFor(`!!document.querySelector('.imu-coarse input[type=checkbox]') || /交给主程序验证/.test(document.querySelector('.imu-coarse')?.innerText || '')`,
    '粗校算出方向、等确认', 25000);
  await js(`[...document.querySelectorAll('.imu-coarse input[type=checkbox]')].forEach((x) => { if (!x.checked) x.click(); })`);
  await fakeSet({ imu_verify_plan: 'ok' });        // 开发用剧本：主程序先看到摆正，再看到往前低头
  await waitFor(`!![...document.querySelectorAll('.imu-coarse button')].find((b) => b.textContent === '交给主程序验证' && !b.disabled)`, '原粗校验证按钮就绪');
  if (!await clickHas('.imu-coarse button', '交给主程序验证')) throw new Error('找不到「交给主程序验证」');
  // 验证成功后主程序里 verified 变成 true，粗校卡会收起：看 IMU 页顶部状态变成「方向已确认」。
  await waitFor(`/方向已确认/.test(document.querySelector('.sv-metrics')?.innerText || '')`,
    'verify-imu 成功', 40000);
  out.imu = await js(`[document.querySelector('.sv-metrics')?.innerText || '',
    document.querySelector('.imu-coarse')?.innerText || ''].filter(Boolean).join(' ／ ').replace(/\\s+/g, ' ').slice(0, 700)`);
  if (PHASE === 'imu-hold') {
    out.holdAfter = await fakeRpc('robot.busStatus', {});
    await shot('commit-imu-hold-verified.png');
    // Reset the fake fixture independently of which cards remain mounted after verification.
    await fakeRpc('robot.relax', {});
    for (let attempt = 0; attempt < 50; attempt++) {
      out.cleaned = await fakeRpc('robot.busStatus', {});
      if (out.cleaned.torque_state === 'off') break;
      await sleep(100);
    }
  } else await shot('commit-imu-verified.png');
}

if (PHASE === 'servo') {
  log('舵机页：一键中位，然后交还主程序');
  await js(`location.hash = '#/servos'`);
  await waitFor(`!![...document.querySelectorAll('.sv-page button')].find(b=>b.textContent.trim()==='开始调舵机'&&!b.disabled)`, '明确接管入口', 10000);
  if (!await clickExact('.sv-page button', '开始调舵机')) throw new Error('找不到明确接管按钮');
  await waitFor(`/调舵机中/.test(document.querySelector('.sv-sub')?.innerText || '')`, '舵机页接管（演示舵机）', 20000);
  if (!await clickExact('.sv-page button', '一键校准')) throw new Error('找不到「一键校准」');
  await waitFor(`!!document.querySelector('.sv-modal')`, '中位校准预览', 15000);
  out.planRows = await js(`document.querySelectorAll('.sv-plan tbody tr').length`);
  if (!await clickHas('.sv-modal button', '确认，校准')) throw new Error('找不到「确认，校准」');
  await waitFor(`/校准完：成功 15 颗/.test(document.body.innerText)`, '15 颗中位校好', 40000);
  if (!await clickHas('.sv-handback button', '交还主程序')) throw new Error('找不到「交还主程序」');
  await waitFor(`!!document.querySelector('.sv-handback .two-step-ask')`, '交还二次确认', 5000);
  await sleep(900);
  await js(`document.querySelector('.sv-handback .two-step-ask')?.click()`);
  await waitFor(`!/调舵机中/.test(document.querySelector('.sv-sub')?.innerText || '')`, '交还完成', 30000);
  out.servo = await js(`document.querySelector('.sv-sub')?.innerText.replace(/\\s+/g, ' ')`);
  await shot('commit-servo-done.png');
  await js(`location.hash = '#/'`);
}

if (PHASE === 'commit' || PHASE === 'fail') {
  await js(`location.hash = '#/'`);
  await waitFor(`!!${card}`, '第 3 步「写进主程序」的卡片', 20000);
  await waitFor(`(() => { const c = ${card}; return c && c.querySelectorAll('.onb-checks li').length > 0; })()`, '准备情况清单', 20000);
  await waitFor(`(() => { const c = ${card}; const w = [...c.querySelectorAll('.onb-commit-acts button')]
    .find((b) => b.textContent.includes('写进主程序')); return w && !w.disabled; })()`, '「写进主程序」按钮可点', 25000);
  out.ready = await snapshot();
  const writeBtn = `[...document.querySelectorAll('[data-step="commit"] .onb-commit-acts button')]
    .find((b) => b.textContent.includes('写进主程序'))`;
  await js(`${writeBtn}.click()`);
  await waitFor(`!!document.querySelector('[data-step="commit"] .two-step-ask')`, '二次确认', 5000);
  out.confirmText = await js(`document.querySelector('[data-step="commit"] .two-step-ask')?.textContent`);
  await sleep(900);       // TwoStep 的 700 ms 保护
  await js(`document.querySelector('[data-step="commit"] .two-step-ask').click()`);
  // 写入很快时，回执会被最终结果直接替换（甚至第 3 步已经 ✓、卡片收起）：等到回执、进度卡或完成都行。
  await waitFor(`/已经在写|开始写/.test(document.querySelector('[data-step="commit"] .onb-commit-msg')?.innerText || '') ||
    !!document.querySelector('[data-step="commit"] .onb-commit-job') ||
    document.querySelector('[data-step="commit"] .onb-mark')?.getAttribute('aria-label') === '完成'`,
    '开始写的回执或最终结果', 20000);
  out.started = await snapshot();

  if (PHASE === 'fail') {
    // 假小助手会让假主程序重启一下：断线期间第 3 步的进度卡要单独显示
    let offline = null;
    const end = Date.now() + 25000;
    while (Date.now() < end) {
      const s = await js(`(() => { const c = document.querySelector('[data-step="commit"]'); if (!c) return null;
        return { text: c.innerText.replace(/\\s+/g, ' ').slice(0, 600), job: !!c.querySelector('.onb-commit-job'), msg: c.querySelector('.onb-commit-msg')?.innerText || null }; })()`);
      if (s && s.job && /主程序在重启|断线/.test(s.text)) { offline = s; break; }
      await sleep(200);
    }
    out.offline = offline;
    if (!offline) throw new Error('断线期间没看到第 3 步的进度卡');
    await shot('commit-fail-offline.png');
    await waitFor(`(() => { const m = document.querySelector('[data-step="commit"] .onb-commit-msg.bad');
      return /标定没通过主程序的检查/.test(m?.innerText || ''); })()`, '失败原因', 30000);
    // 回滚要把主程序重新拉成标定模式：等它回来、标定读得到，第 3 步才会从「读不到」变回「该做」。
    await waitFor(`document.querySelector('[data-step="commit"] .onb-mark')?.getAttribute('aria-label') === '该做'`,
      '主程序回来后第 3 步回到该做', 30000);
    out.wsCommit = await js(`(async () => {
      const w = new WebSocket(location.origin.replace('http', 'ws') + '/ws');
      const m = await new Promise((ok) => {
        const t = setTimeout(() => { try { w.close(); } catch (e) {} ok(null); }, 5000);
        w.onmessage = (e) => { const x = JSON.parse(e.data); if (x.t === 'state') { clearTimeout(t); w.close(); ok(x); } };
      });
      return m ? { robotd_ok: m.robotd_ok, posture: m.posture, mode: m.mode, phase: m.phase,
        job: m.commit && m.commit.job, commit_mode: m.commit && m.commit.mode } : null;
    })()`);
    out.failed = await snapshot();
    await shot('commit-fail-reason.png');
  } else {
    await waitFor(`document.querySelector('[data-step="commit"] .onb-mark')?.getAttribute('aria-label') === '完成'`,
      '第 3 步变成完成（假主程序切到运动模式）', 40000);
    await waitFor(`/运动模式/.test(document.querySelector('.topbar .meta')?.innerText || '')`, '顶栏显示运动模式', 15000);
    out.done = await snapshot();
    out.topbar = await js(`document.querySelector('.topbar .meta')?.innerText`);
    await shot('commit-done.png');
  }
}

if (PHASE === 'factory') {
  await waitFor(`!!${card}`, '第 3 步「写进主程序」的卡片', 20000);
  await waitFor(`(() => { const c = ${card}; return c && c.querySelectorAll('.onb-checks li').length > 0; })()`, '准备情况清单', 15000);
  out.factory = await snapshot();
  await shot('commit-factory.png');
  await js(`[...document.querySelectorAll('[data-step="commit"] .onb-commit-acts button')]
    .find((b) => b.textContent.includes('先看看要写什么'))?.click()`);
  await waitFor(`!!document.querySelector('[data-step="commit"] .onb-preview')`, '预览展开', 20000);
  out.preview = await js(`(() => {
    const p = document.querySelector('[data-step="commit"] .onb-preview');
    return { jointRows: p.querySelectorAll('.onb-preview-table tbody tr').length,
      imu: p.querySelector('.onb-preview-imu')?.innerText || '',
      toml: (p.querySelector('pre')?.textContent || '').slice(0, 4000) };
  })()`);
  await shot('commit-preview.png');
}

log('收尾');
out.errors = errors;
process.stdout.write(JSON.stringify(out));
await new Promise((r) => process.stdout.write('', r));
ws.close();
await fetch(`http://127.0.0.1:${CDP}/json/close/${page.id}`);
