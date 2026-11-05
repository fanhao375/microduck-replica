// Microduck Studio 前端。不构建：Preact + htm（vendor/preact-htm.module.js）直接在浏览器里跑。
//
// 跟 Studio 后端只有两条路：
// - HTTP：/api/session、/api/login、/api/logout、/api/password、/api/logs、/api/diag；
// - WebSocket /ws：后端推 hello / state / log / result，前端发 hb（心跳，带页面是否可见）和 cmd（命令）。
// 消息格式见 software/studio/README.md「前后端协议」。
import { html, render, Component, useState, useEffect, useRef, useCallback } from './vendor/preact-htm.module.js';
import { Duck3D } from './duck3d.js';
import { ServoPage, HandBackButton } from './servo.js';
import { RuntimeDiagnostics } from './runtime-diagnostics.js';
import { ImuPage } from './imu.js';
import { PadPage, padActivity, padGo } from './pad.js';
import { BatteryCell } from './battery.js';
import { BatterySettings } from './settings-battery.js';
import { AiSettings } from './settings-ai.js';
import { UpdateSettings } from './settings-updates.js';
import { AccessGuide, AiSshEntry } from './settings-access.js';
import { TuneSection, ModelSettings } from './tune.js';
import { WifiCard, WifiSettings, DuckAddresses } from './wifi.js';
import { TwoStep } from './confirm.js';
import { ModelsPage } from './models.js';
import { CameraPage, TofPage, SensorBandCells } from './sensors.js';
import { imuDiagnosticCopy, startupStageCopy } from './startup-status.js';

const STALE_GRAY_MS = 1000;   // 超过 1 秒没新数据：数值变灰
const STALE_ALARM_MS = 3000;  // 超过 3 秒：横幅；卸力按钮仍可按（只要跟 Studio 的连接在）

// ---------------- 连接 ----------------

class Link {
  constructor() {
    this.ws = null;
    this.seq = 0;
    this.pending = new Map();
    this.listeners = new Set();
    this.status = 'connecting';
    this.lastAt = 0;
    this.backoff = 1000;
    this._hb = null;
    this.padCapture = false;
    document.addEventListener('visibilitychange', () => this.heartbeat());
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(m) { for (const fn of this.listeners) fn(m); }
  setStatus(s) { this.status = s; this.emit({ t: '_status', status: s }); }

  connect() {
    clearTimeout(this._retry);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;
    let opened = false;
    this.setStatus('connecting');
    ws.onopen = () => {
      opened = true;
      this.backoff = 1000;
      this.setStatus('open');
      this.heartbeat();
      clearInterval(this._hb);
      this._hb = setInterval(() => this.heartbeat(), 1000);
    };
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;            // 已经换掉的旧连接：它推的东西不要
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      this.lastAt = performance.now();
      // 后台标签页里 setInterval 一分钟才跑一次，收消息不受限：服务端的心跳来了就回一个，免得被当成断线
      if (m.t === 'hb') this.heartbeat();
      if (m.t === 'result' && this.pending.has(m.id)) {
        this.pending.get(m.id)(m);
        this.pending.delete(m.id);
      }
      this.emit(m);
    };
    ws.onclose = async (ev) => {
      if (this.ws !== ws) return;            // 旧连接（reconnect 换掉的）：别动新连接的心跳
      clearInterval(this._hb);
      for (const done of this.pending.values()) done({ ok: false, error: '和 Studio 的连接断了' });
      this.pending.clear();
      if (ev.code === 4401) { this.setStatus('auth'); return; }   // 登录过期
      if (!opened) {
        // 握手就没成：可能是登录过期（老版本 Studio 在握手阶段就拒绝，浏览器只看到 1006）
        try { if ((await api('/api/session', undefined, 2000)).status === 401) { this.setStatus('auth'); return; } } catch { /* Studio 不在 */ }
        if (this.ws !== ws) return;          // 等的这会儿用户点了「重新连接」：交给新连接
      }
      this.setStatus('closed');
      this._retry = setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 8000);
    };
  }

  /** 等跟 Studio 的连接建好（刷新时页面先出来、连接后到：别在连上之前就发命令） */
  whenOpen() {
    if (this.status === 'open') return Promise.resolve();
    return new Promise((resolve) => { const off = this.on((m) => { if (m.t === '_status' && m.status === 'open') { off(); resolve(); } }); });
  }

  reconnect() { this.backoff = 1000; try { this.ws && this.ws.close(); } catch { /* 已经关了 */ } this.connect(); }

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) { this.ws.send(JSON.stringify(obj)); return true; }
    return false;
  }

  // pad_visible：手柄那一块现在在不在屏幕上（总览重排审查 O1）。后端只看它决定手柄快照 10 Hz 还是 1 Hz；
  // #/pad 现在是总览上的锚点，地址是它不代表看得到（往上滑回舞台，地址栏还是 #/pad）
  heartbeat() {
    const route=location.hash.replace(/^#/, '') || '/';
    this.send({ t: 'hb', seq: ++this.seq, visible: document.visibilityState === 'visible', route,
      pad_visible: padActivity.active || this.padCapture,
      diagnostics_visible: !!this.diagnosticsVisible && (route==='/' || route==='/pad') });
  }

  setDiagnosticsVisible(active) {
    if (!!this.diagnosticsVisible===!!active) return;
    this.diagnosticsVisible=!!active;this.heartbeat();
  }

  // 模型改键只读旁路：请求快照，不改变 padActivity，也不接管网页遥控。
  setPadCapture(active) { this.padCapture = Boolean(active); this.heartbeat(); }

  cmd(name, args = {}, timeoutMs = 15000) {
    const id = `c${++this.seq}`;
    return new Promise((resolve) => {
      if (!this.send({ t: 'cmd', id, name, args })) { resolve({ ok: false, error: '和 Studio 的连接断了' }); return; }
      this.pending.set(id, resolve);
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); resolve({ ok: false, error: '鸭子没回话（超时）' }); }
      }, timeoutMs);
    });
  }
}

const link = new Link();
padActivity.bind(link);                  // 手柄区进出的那一刻马上发一次心跳

let csrf = '';   // /api/session 给的；改动类 POST 要带上（X-CSRF），配合后端的 Origin 校验
async function api(path, body, timeoutMs = 10000, signal) {
  const opt = body === undefined ? { credentials: 'same-origin' }
    : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify(body) };
  const ctl = new AbortController();
  const abort = () => ctl.abort();
  if (signal?.aborted) ctl.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => ctl.abort(), timeoutMs);   // 手机 WiFi 抖的时候请求可能挂几十秒
  let r;
  let data = {};
  try {
    r = await fetch(path, { ...opt, signal: ctl.signal });
    try { data = await r.json(); } catch (error) { if (ctl.signal.aborted) throw error; }
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  return { status: r.status, ...data };
}

// ---------------- 小工具 ----------------

const fmt1 = (v) => (typeof v === 'number' && isFinite(v) ? v.toFixed(1) : '—');

function useNow(ms = 250) {
  const [now, setNow] = useState(performance.now());
  useEffect(() => { const t = setInterval(() => setNow(performance.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

function Badge({ kind }) {
  return html`<span class=${'badge ' + kind} aria-hidden="true">${kind === 'bad' ? '×' : '!'}</span>`;
}

// ---------------- 顶部安全条 ----------------

// 卸力按钮：状态全看 Studio 推来的 st.estop（由 busStatus 回读推出来，不看「按过没有」）。
// 只要跟 Studio 的连接在，按钮就能按——状态旧了、读不到也能按（代码审查 #7：手机没有空格键，
// 按钮一禁用就完全没法卸力）。只有连接断了才禁用。
// 空格键：按下和松开都在捕获阶段拦住。只拦按下的话，松开时浏览器会顺手「点击」有焦点的按钮
// （比如以后的「站起来」），刚卸力又站起来（review-impl-plan I6）。
// 只有焦点在打字的地方时才不拦（代码审查 #8：复选框、单选框、下拉框上按空格照样卸力）。
const TEXT_TYPES = ['text', 'password', 'search', 'email', 'number', 'tel', 'url'];
const typing = (el) => !!el && (el.isContentEditable || el.tagName === 'TEXTAREA'
  || (el.tagName === 'INPUT' && TEXT_TYPES.includes((el.type || 'text').toLowerCase())));
// Ctrl / Alt / Win + 空格是切输入法这类系统快捷键，不当成卸力
const plainSpace = (e) => e.code === 'Space' && !e.ctrlKey && !e.altKey && !e.metaKey;

function EStop({ st, live, open }) {
  const [sentAt, setSentAt] = useState(0);
  const [err, setErr] = useState(null);
  const fire = useCallback(async () => {
    if (performance.now() - sentAt < 200) return;           // 按住空格自动重复：最多每秒 5 次
    setSentAt(performance.now()); setErr(null);
    const r = await link.cmd('relax', {}, 7000);       // 后端最坏 3 + 0.1 + 3 秒
    if (!r.ok) setErr(r.reason || r.error || '没发出去');
  }, [sentAt]);

  useEffect(() => {
    const down = (e) => {
      if (!plainSpace(e) || typing(e.target)) return;
      e.preventDefault(); e.stopPropagation();
      if (!e.repeat) fire();
    };
    const up = (e) => { if (plainSpace(e) && !typing(e.target)) { e.preventDefault(); e.stopPropagation(); } };
    window.addEventListener('keydown', down, true);
    window.addEventListener('keyup', up, true);
    return () => { window.removeEventListener('keydown', down, true); window.removeEventListener('keyup', up, true); };
  }, [fire]);

  const es = st?.estop || {};
  // 确认卸力了，之前那句「没发出去 / 超时」就过时了
  useEffect(() => { if (es.state === 'confirmed') setErr(null); }, [es.state]);
  const click = (e) => { e.currentTarget.blur(); fire(); };
  const note = (text) => (text ? html`<span class="why" role=${err ? 'alert' : undefined}>${text}</span>` : null);
  const fallback = st?.mode === 'commissioning' ? '只能关电源' : '用手柄 − 或关电源';
  if (!open) {
    // 跟 Studio 的连接断了：按了也发不出去
    return html`<span class="estop-wrap">${note(fallback)}
      <button class="estop unavailable" disabled>卸力 · 不可用</button></span>`;
  }
  if (st?.servo_orphan) {
    // 上次 Studio 退出时舵机会话开着、这次没接管上（审查 D1）：robotd 不在、总线没接管，这里按了也发不出去——带去舵机页接管
    // （舵机页一进去就接管总线，接管以后卸力走总线）。空格照样发，后端回「先去舵机页接管」
    return html`<span class="estop-wrap">${note(err || '可能还有舵机保持着')}
      <button class="estop unavailable" onClick=${(e) => { e.currentTarget.blur(); location.hash = '#/servos'; }}
        title="上次 Studio 退出时舵机会话开着、这次没接管上：先去舵机页接管总线，才能在总线上卸力">卸力 · 先去舵机页接管</button></span>`;
  }
  if (es.state === 'unavailable') {
    return html`<span class="estop-wrap">${note(err || es.why || fallback)}
      <button class="estop unavailable" onClick=${click}>卸力 · 不一定有效</button></span>`;
  }
  if (live && es.state === 'confirmed') {
    return html`<span class="estop-wrap">${note(err)}
      <button class="estop done" onClick=${click} title=${es.why}>已卸力 ✓</button></span>`;
  }
  // 数据旧了（!live）也给红色按钮：按了照样发，Studio 那边的卸力专线可能是好的
  return html`<span class="estop-wrap">${note(err || (!live ? '状态不明，仍可按' : ''))}
    <button class="estop" onClick=${click} title="让 15 颗舵机全部卸力。鸭子会软下来，扶住。">
      ${es.state === 'pending' ? '卸力中…' : '卸力'}<span class="kbd">空格</span></button></span>`;
}

function TopBar({ duck, st, live, age, open, hideEStop }) {
  // 顶栏先说现在是什么模式（出厂标定模式 / 写完切运动模式），再带姿势；标定模式没有策略，别写成「调试中」。
  // 连续报启动阶段超过 30 秒（Studio 从这次连上算）：写「一直报启动中」（10-20 真机：旧 fork 的标定模式永远报 configuring_bus）
  const postureText = st ? ({ starting: st.start_wait?.text || (st.starting_long ? '一直报启动中' : '启动中'), absent: '主程序没在跑', homing: '在站起来',
    servo_session: '调舵机中' }[st.posture] || WAS[st.posture] || '状态不明') : null;
  const modeText = !st ? '连接中'
    : st.mode === 'commissioning' ? (st.posture === 'commissioning' ? '标定模式' : `标定模式 · ${postureText}`)
    : st.mode === 'motion' && st.posture !== 'absent' && st.posture !== 'servo_session' ? `运动模式 · ${postureText}`
    : postureText;
  const pct = st?.battery?.pct;
  // 鼠标停在名字上看得到现在的 IP 和几个地址（「更多」页有能复制的那一块）
  const net = st?.net;
  const addrTitle = net?.known ? [`IP ${net.ip_now || '不知道'}`, ...(net.urls || []).map((u) => u.url)].join('\n') : undefined;
  return html`<header class="topbar">
    <div class="duckid">
      <span class="dot" style=${{ background: duck?.color || '#0F7C80' }}></span>
      <span class="name" title=${addrTitle}>${duck?.label || '鸭子'}</span>
      ${st?.servo_session ? html`<span class="sv-badge">调舵机中</span>` : null}
      <span class="meta num">${live ? '' : `${Math.round(age / 1000)} 秒前 · `}${modeText}${typeof pct === 'number' ? ` · ${Math.round(pct)}%` : ''}</span>
    </div>
    <span class=${hideEStop ? 'estop-hidden' : ''}><${EStop} st=${st} live=${live} open=${open} /></span>
  </header>`;
}

// ---------------- 舵机会话的提示条（每一页顶栏下面，舵机页自己的页头已经有了就不重复） ----------------
// 用户 10-18：「舵机这个页面出去的时候应该保持住使能，不应该泄力」：有舵机保持着，离开舵机页不交还，会话一直开着——
// 这时主程序停着，手柄、遥控、站走都不能用，所以每一页都要看得到、随时能回舵机页或交还。页面关了、断网重连：会话还在，照样显示
// 吸顶（顶栏下面，所有宽度：审查 D6）——手机上滚到手柄区也看得到。舵机页自己的页头有会话那几个字，那一页不重复，
// 但「主程序被手动起来了」「上次退出时没接管上」这两条红的哪一页都出
function ServoBar({ st, onServos }) {
  const ss = st?.servo_session;
  if (ss) {
    if (onServos) return null;
    const unk = ss.unknown ? ` · ${ss.unknown} 颗读不到扭矩（可能也在保持）` : '';
    return html`<div class="banner warn servo-bar" role="status">
      <b>调舵机中 · ${ss.holding ?? '?'} 颗保持着${unk} · 主程序停着（手柄、遥控、站走都不能用）</b>
      <span class="sb-acts"><a href="#/servos">回舵机页</a><${HandBackButton} link=${link} /></span>
      <span class="more sb-more">${ss.restored ? 'Studio 重启过，接着上次的会话管着。' : ''}急停用顶上红按钮或空格（手柄 Select 这时不管用）；别手动起 robotd，先点「交还主程序」</span>
      ${st.servo_marker_error ? html`<span class="more sb-more">${st.servo_marker_error}</span>` : null}</div>`;
  }
  // 会话期间主程序被手动起来了（审查 D9）：它起来就把舵机全部松开、还跟 Studio 抢串口；Studio 已经关了总线（没写舵机）
  if (st?.servo_conflict_s != null) {
    return html`<div class="banner bad servo-bar" role="alert"><b>主程序被手动起来了：它会把舵机全部松开、跟 Studio 抢串口</b>
      <span class="more sb-more">Studio 已经关掉舵机总线（没写舵机）。下次别手动起 robotd：先在页面上点「交还主程序」</span></div>`;
  }
  // 上次 Studio 退出时舵机会话开着、这次没接管上（审查 D1）
  if (st?.servo_orphan) {
    return html`<div class="banner bad servo-bar" role="alert"><b>上次退出时可能还有舵机保持着，去舵机页接管后才能卸力</b>
      ${onServos ? null : html`<a href="#/servos">去舵机页接管</a>`}
      <span class="more sb-more">Studio 重启时没接管上${st.servo_orphan.why ? `（${st.servo_orphan.why}）` : ''}；急的话关电源</span></div>`;
  }
  // 交还了、主程序还没连上：一直提示到主程序连上为止（后端连上后清掉，不再 10 分钟自己消失：审查 D12）
  if (st?.servo_handback_s != null) {
    if (st.servo_giveback_error) {
      return html`<div class="banner bad servo-bar" role="alert"><b>已交还，但主程序没起来</b>
        <span class="more sb-more">${st.servo_giveback_error}${st.servo_giveback_hint ? `：${st.servo_giveback_hint}` : ''}</span></div>`;
    }
    return html`<div class="banner warn servo-bar" role="status"><b>已交还。</b>
      ${st.servo_helper ? html`<span class="more">正在把主程序起回来…</span>`
        : html`<span class="more">主程序连上以后这条就收起。一直没连上（主程序是手动停的）就在鸭子上起来：<code>sudo systemctl start robotd</code></span>`}
      ${st.servo_handback_note ? html`<span class="more sb-more">${st.servo_handback_note}</span>` : null}</div>`;
  }
  return null;
}

// ---------------- 总览 ----------------

// 舞台文案，按 review-impl-plan I2 的状态表。站、走、坐的按钮在下面的手柄区块（往下一刷就是遥控：总览重排 §1.2）；
// 卸力在右上角（顶栏吸顶，任何时候都在屏幕上），遥控卡顶上也有一个。
const WAS = { holding: '站着', policy: '站着', walking: '在走', limp: '软着', sitting: '坐着', homing: '在站起来' };
function stageCopy(st, live, name, age) {
  if (!st) return { h: `正在连接${name}…`, sub: '鸭子开着、跟这台设备在同一个 WiFi 里就行。' };
  if (!live && st.posture !== 'absent') {
    return { h: `${name} ${Math.round(age / 1000)} 秒没响应`, sub: `断线前它${WAS[st.posture] || '的状态不清楚'}。软件现在管不到它。`, stale: true };
  }
  switch (st.posture) {
    case 'absent': return { h: '鸭子主程序没在跑', sub: '可能正在重启（约 2 秒）。注意：主程序停了不会卸力，舵机可能还顶着。', stale: true };
    case 'starting':
      if (startupStageCopy(st)) return startupStageCopy(st);
      // 连续报启动阶段超过 30 秒：不再说「通常 10 秒内完成」，给「去日志页」（10-20 真机：旧 fork 的标定模式永远报 configuring_bus）
      if (st.starting_long) {
        return { h: '主程序一直报「启动中」', act: { href: '#/logs', label: '去日志页' },
          sub: st.start_error ? `已经 ${st.starting_s} 秒了，卡在：${st.start_error}。去日志页看主程序最后说了什么。`
            : `已经 ${st.starting_s} 秒了。主程序可能其实起来了、只是状态没更新（旧版主程序的毛病），也可能卡在启动：去日志页看主程序最后说了什么。` };
      }
      return { h: '主程序正在启动', sub: st.start_error ? `卡在：${st.start_error}` : '通常 10 秒内完成。' };
    case 'commissioning': return { h: '标定模式', sub: '出厂第一次：先粗校 IMU、再一键中位，第 3 步写进主程序会自动切到运动模式。' };
    case 'limp': return { h: `${name}软着`, sub: '双手托起它，往下滑到「遥控」点「站起来」（或者按手柄 +）。' };
    case 'homing': return { h: '正在站起来…', sub: '扶着它，别松手。' };
    case 'holding': return { h: `${name}站好了`, sub: '往下滑到「遥控」点「开始走」（或者按手柄 +），再推摇杆。' };
    case 'policy': return { h: `${name}站着，准备好走了`, sub: '推摇杆就走（往下有网页手柄）；点「停下（站好）」或按手柄 + 回到站好。' };
    case 'walking': return { h: '正在走', sub: '松开摇杆它会停下、继续站着。' };
    case 'sitting': return { h: `${name}坐着`, sub: '点遥控里的「站起来」，或者按手柄十字键下。' };
    case 'servo_session': return { h: '正在调舵机', sub: '主程序停着（手柄、遥控、站走都不能用）。离开舵机页舵机照样保持，点顶上的「交还主程序」才交还。' };
    default: return { h: name, sub: '读不出它现在的姿势。' };
  }
}

// 舞台：滚轮、手机单指都留给页面（总览是要往下刷的首页：审查 O2）——Duck3D 的 pageScroll 关掉缩放、单指不转；
// 桌面上鼠标拖着转，手机上两个手指转。手机上矮一点（300 px），第一屏露得出仪表带和清单（审查 O11）。
// 断线横幅盖在舞台上，不插在上面把下面的控件推下去（审查 O10）
function Stage({ st, live, age, name }) {
  const canvasRef = useRef(null);
  const duckRef = useRef(null);
  const [msg, setMsg] = useState(null);
  const [moved, setMoved] = useState(false);     // 用户转过视角：显示「回正」
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const phone = window.innerWidth < 700;   // 手机：鸭子居中偏上、小一点，下面留给字
    const d = new Duck3D(canvasRef.current, phone ? { view: 'front34', centerX: 0.5, centerY: 0.34, fill: 0.6, pageScroll: true }
      : { view: 'front34', centerX: 0.61, pageScroll: true });
    duckRef.current = d;
    d.onUserMove = () => setMoved(true);
    d.load('model/').then(() => setReady(true)).catch((e) => setMsg(`3D 模型没加载：${e.message}`));
    return () => d.dispose();
  }, []);

  useEffect(() => {
    if (!duckRef.current || !st) return;
    duckRef.current.setJoints(st.joints);
    duckRef.current.setGravity(st.gravity);
  }, [st]);

  const c = stageCopy(st, live, name, age);
  // 没有关节数据（null）也当旧的：不然全彩显示默认姿势，看着像实时
  const jointsOld = st?.joints_age_ms == null || st.joints_age_ms > 1000;
  return html`<section class="stage stage-ov" aria-label="鸭子现在">
    ${/* 变灰用 class：给画布传 style 字符串会把 duck3d 设的 touch-action:pan-y 整个盖掉，手机两指就成了整页缩放（代码审查 C6） */ ''}
    <canvas ref=${canvasRef} class=${c.stale || jointsOld ? 'stage-stale' : ''}></canvas>
    ${!live && st ? html`<div class="banner bad stage-banner" role="alert"><${Badge} kind="bad" />
      <b>连不上${name} · ${isFinite(age) ? Math.round(age / 1000) + ' 秒' : ''}</b><span class="more">站着的鸭子会停下、继续站着。</span>
      <a href="#" onClick=${(e) => { e.preventDefault(); link.reconnect(); }}>重新连接</a></div>` : null}
    <div class="copy">
      <h1>${c.h}</h1>
      <p class="sub">${c.sub}</p>
      ${c.act ? html`<div class="btns"><button class="btn2 stage-act" onClick=${(e) => { e.currentTarget.blur(); location.hash = c.act.href; }}>${c.act.label}</button></div>` : null}
      ${msg ? html`<span class="tip" role="alert">${msg}</span>` : c.tip ? html`<span class="tip">${c.tip}</span>` : null}
    </div>
    ${c.stale && isFinite(age) ? html`<span class="age num">${Math.round(age / 1000)} 秒前</span>` : null}
    ${!ready ? html`<span class="sv-loading">3D 模型加载中…</span>` : null}
    ${moved ? html`<button class="reset-view" onClick=${(e) => { e.currentTarget.blur(); duckRef.current?.resetView(); setMoved(false); }}>回正</button>` : null}
  </section>`;
}

// 主板温度格（总览重排 §1.1、审查 O8）：字段见 studio/state.py cpu_view。降频红（写 x / y MHz），被限频（不是热的）照实写，
// ≥ 80 °C 橙（带回差，77 °C 以下才收），读不到写「—」加原因。我们的 80 °C 是自己定的，所以只用橙色、不当成错误
function boardCell(cpu) {
  const mhz = (k) => (typeof k === 'number' ? Math.round(k / 1000) : '?');
  if (typeof cpu?.c !== 'number') return { big: '—', small: cpu?.why || '读不到', kind: null };
  const studio = cpu.src === 'studio' ? ' · Studio 自己读的' : '';
  if (cpu.throttled) return { big: Math.round(cpu.c), small: `降频了：${mhz(cpu.khz)} / ${mhz(cpu.max_khz)} MHz${studio}`, kind: 'bad' };
  if (cpu.hot) return { big: Math.round(cpu.c), small: `有点热（80 °C 起提醒）${studio}`, kind: 'warn' };
  if (cpu.limited) return { big: Math.round(cpu.c), small: `被限频到 ${mhz(cpu.khz)} MHz（不是热的）${studio}`, kind: null };
  if (cpu.throttled == null) return { big: Math.round(cpu.c), small: `降频情况读不到${studio}`, kind: null };
  return { big: Math.round(cpu.c), small: `正常${studio}`, kind: null };
}

// 手柄格：用手柄快照的 status / estop（跟下面手柄区块的页头、上手清单第 3、4 步同一份：审查 O7）；
// 没有手柄后端（台架模式）才用 state.pad。写「手柄急停」，跟顶栏的「卸力」分开
function padCell(st, pd) {
  if (!pd) {
    const p = st?.pad || {};
    return { word: !p.known ? '不知道' : p.connected ? '已连' : '没连',
      small: !p.known ? '读不到手柄状态' : p.connected ? `${p.name || '手柄'}${p.estop_ok ? ' · 手柄急停有效' : ''}` : '去配对' };
  }
  const s = pd.status || {}, lv = pd.live || {}, es = pd.estop || {};
  const attached = lv.state === 'attached';
  const known = !!s.known || attached;
  const connected = !!s.active || attached;
  const name = s.active?.name || (attached ? lv.device?.name : null) || '手柄';
  const esw = { ok: '手柄急停有效 ✓', unknown: '手柄急停没核对完', bad: '手柄急停无效' }[es.state] || '';
  if (!known) return { word: '不知道', small: '读不到手柄状态（configd）' };
  if (connected) return { word: '已连', small: `${name}${esw ? ` · ${esw}` : ''}` };
  return { word: '没连', small: (s.pads || []).length ? '配过：打开手柄就会自己连上' : '去配对' };
}

// 吸顶的那几层（用户 10-19：往下滑，侧栏和仪表带一直在）量出自己多高，写到根上的 CSS 变量（--bar-h 舵机提示条、--band-h 仪表带）：
// 下面吸顶的手柄 3D 卡、舵机页左栏、滚到手柄区的停靠位置都往下让。不吸顶（手机上的仪表带）或藏起来（别的页）记 0
function useStickyHeight(name) {
  const ro = useRef(null);
  return useCallback((el) => {
    const root = document.documentElement.style;
    ro.current?.disconnect();
    ro.current = null;
    const put = () => root.setProperty(name, el && getComputedStyle(el).position === 'sticky' ? `${el.offsetHeight}px` : '0px');
    put();
    if (el && typeof ResizeObserver !== 'undefined') {
      ro.current = new ResizeObserver(put);
      ro.current.observe(el);
    }
  }, [name]);
}

function Band({ st, pd, gray, live, diagnosticElapsed = 0, onSame, sensorsActive }) {
  const bandRef = useStickyHeight('--band-h');
  const b = st?.battery || {}, s = st?.servos || {}, imu = st?.imu || {};
  const diagnostic = st?.posture === 'starting' && (imu.source == null || imu.source === 'bus')
    ? imuDiagnosticCopy(imu.diagnostic, live, diagnosticElapsed) : null;
  const imuWord = diagnostic?.word || { ok: '正常', unverified: '方向没确认', frozen: '读数冻住了', missing: '没读到' }[imu.status] || '—';
  const imuKind = diagnostic ? diagnostic.kind : { unverified: 'warn', frozen: 'bad', missing: 'bad' }[imu.status];
  const pct = typeof b.pct === 'number' ? Math.round(b.pct) : null;
  const cpu = boardCell(st?.cpu);
  const pad = padCell(st, pd);
  return html`<section class=${'band board' + (gray ? ' stale' : '')} aria-label="状态" ref=${bandRef}>
    <${BatteryCell} b=${b} />
    <div>
      <span class="label">舵机</span>
      <span class="num" style="display:flex;align-items:baseline;gap:4px"><span class="big">${s.online ?? '—'}</span><span class="unit">/ ${s.total ?? 15}</span></span>
      <span class="small">${s.hottest ? `最热 ${Math.round(s.hottest.c)} °C · ${s.hottest.name}` : '—'}</span>
    </div>
    <a href="#/imu">
      <span class="label">IMU</span>
      <span class="word">${imuKind ? html`<${Badge} kind=${imuKind} />` : null}${imuWord}</span>
      <span class="small">${diagnostic ? diagnostic.board || 'ID 200 · 只读诊断' : { unverified: '先只站着', ok: '方向已确认' }[imu.status] || '去校准'}</span>
    </a>
    <${SensorBandCells} api=${api} active=${sensorsActive} />
    <a href="#/pad" onClick=${(e) => onSame(e, '#/pad')}>
      <span class="label">手柄</span>
      <span class="word">${pad.word}</span>
      <span class="small">${pad.small}</span>
    </a>
    <div class=${'band-cpu' + (cpu.kind ? ` ${cpu.kind}` : '')}>
      <span class="label">主板</span>
      <span class="num" style="display:flex;align-items:baseline;gap:4px"><span class="big">${cpu.big}</span>${cpu.big !== '—' ? html`<span class="unit">°C</span>` : null}</span>
      <span class="small">${cpu.kind ? html`<${Badge} kind=${cpu.kind} /> ` : null}${cpu.small}</span>
    </div>
  </section>`;
}

// ---------------- 上手清单（总览重排 §1.3、§5 O14：6 行 + 卡片头的「连上鸭子」；任务 11 加了「写进主程序」） ----------------
// 判法都在后端（studio/onboard.py，0.5 秒判一次），这里只画。五种状态：✓ 完成 · ↷ 跳过了 · ● 该做 / 要重做 · ○ 没做 · ? 读不到。
// 「现在该做」= 第一个 ● / ○ 的步（ob.next），它的第一个按钮是深色的「去做」；后面的不锁，照样能点。
// 每一行任何时候都有三个按钮：「去做」「已完成」「跳过」（用户 10-20：「去做、已完成、跳过，有三个也行」；以前锁着的情况也能点）。
// 有后果的（主程序明说没做完、读不到、测出了问题、测急停、调舵机时）点两次：第二下按钮上写后果（后端给的 mark.*.ask），
// 页面第二下才带 confirmed，后端没收到也拒。✓ 的行有「重新做」（就是那一步的去做）；用户标的 ✓ / ↷ 还有「取消」/「取消跳过」。
// 全部完成收成一行，能点开看 6 行（每行能重新做），开没开记在 localStorage

const ONB_MARK = { ok: '✓', skip: '↷', todo: '●', no: '○', unk: '?' };
const ONB_WORD = { ok: '完成', skip: '跳过了', todo: '该做', no: '没做', unk: '读不到' };
const ONB_OPEN_KEY = 'studio.onb.open';
const ONB_FROZEN = '在调舵机：清单停在调之前的样子；这时标的「已完成」「跳过」要等交还主程序以后才显示';
function fmtWhen(w) {
  if (w == null || w === '') return null;
  let d = null;
  if (typeof w === 'number') d = new Date(w * 1000);
  else if (typeof w === 'string') { const m = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)/.exec(w); if (m) d = new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5]); }
  if (!d || isNaN(d.getTime())) return String(w);
  if (d.getFullYear() < 2024) return '时间不准（鸭子没联网）';    // 鸭子开机没联网时墙钟停在 1970（审查 O6）
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function loadOnbOpen() { try { return localStorage.getItem(ONB_OPEN_KEY) === '1'; } catch { return false; } }
function saveOnbOpen(v) { try { localStorage.setItem(ONB_OPEN_KEY, v ? '1' : '0'); } catch { /* 隐私模式 */ } }

function Onboard({ st, live }) {
  const ob = st?.onboarding;
  const [msg, setMsg] = useState(null);
  const [open, setOpen] = useState(loadOnbOpen);
  const [cprev, setCprev] = useState(null);       // 「先看看要写什么」的结果（每颗的方向、限位、IMU 方向、TOML）
  const [cmsg, setCmsg] = useState(null);         // 预览 / 写入按钮这一下的话
  const [cbusy, setCbusy] = useState(false);
  const [copen, setCopen] = useState(false);      // 「写进主程序」那张卡片：做过 / 判不出时收着，点「去看看」「重新做」才展开
  const job = st?.commit?.job;                   // 写入进度 / 结果（Studio 推过来；st.commit 只在第 3 步用）
  // 进度卡一出现，本地「开始写了」的临时回执就让位：失败原因只由 job 显示，成功 2 分钟后卡片能自己收起。
  useEffect(() => {
    if (job) setCmsg(null);
  }, [job?.phase, job?.ok, job?.finished]);
  // 写入要重启主程序，这段时间页面算「没连着」。第 3 步的进度 / 失败原因不能被「断线时不检查」盖掉：
  // 有 job 就单独显示一张卡（成功的那份 2 分钟后后端不再推，卡片自然收起）。
  if (!live && job) {
    const title = job.phase === 'running' ? '写进主程序' : (job.ok ? '写进主程序：好了' : '写进主程序：没成');
    return html`<section class="onb board" data-step="commit" aria-label="写进主程序进度">
      <div class="onb-h"><b>${title}</b><span class="quiet">主程序在重启，页面暂时没连着</span></div>
      <div class="onb-commit">
        ${job.phase === 'running'
          ? html`<div class="onb-commit-job" role="status"><span class="onb-spin">⋯</span>${job.msg || '在写…'}</div>`
          : html`<div class=${'onb-commit-msg ' + (job.ok ? 'ok' : 'bad')}>${job.reason || job.msg || ''}</div>`}
        ${job.phase !== 'running' && !job.ok ? html`<p class="onb-tip">小助手会把原来的标定文件和模式换回去；等主程序回来再看第 3 步。</p>` : null}
      </div>
    </section>`;
  }
  if (!live) return html`<section class="onb board"><div class="onb-h"><b>上手清单</b><span class="quiet">断线时不检查</span></div></section>`;
  if (!ob) return html`<section class="onb board"><div class="onb-h"><b>上手清单</b><span class="quiet">还在判…</span></div></section>`;
  const head = ob.header || {};
  if (head.kind === 'bench') return html`<section class="onb board"><div class="onb-h"><b>上手清单</b><span class="quiet">${head.text}</span></div></section>`;
  const done = ob.all_done && head.ok;
  const n = ob.skipped_n || 0;
  const m = ob.marked_n || 0;
  const counts = [n ? `跳过 ${n} 步` : '', m ? `标了已完成 ${m} 步` : ''].filter(Boolean).join('、');
  const doneText = `上手 6 步都完成了${counts ? `（${counts}）` : ''}`;
  const toggle = () => { const v = !open; setOpen(v); saveOnbOpen(v); };
  if (done && !open) {
    return html`<section class="onb board onb-done" aria-label="上手清单">
      <button class="onb-fold" aria-expanded="false" onClick=${toggle}><span class="onb-mark onb-ok">✓</span><b>${doneText}</b>
        <span class="onb-more">展开</span></button></section>`;
  }
  const op = async (args, okText) => {
    const r = await link.cmd('onboard.op', args, 8000);
    setMsg(r.ok ? (okText ? { ok: true, text: okText } : null) : { ok: false, text: r.reason || r.error || '没记成' });
  };
  const go = async (a, row) => {
    setMsg(null);
    if (a.do === 'servos') location.hash = '#/servos';
    else if (a.do === 'imu') location.hash = '#/imu';
    else if (a.do === 'commit') {
      setCopen(true);
      setTimeout(() => document.querySelector('[data-step="commit"] .onb-commit')?.scrollIntoView({ block: 'center' }), 50);
    }
    else if (a.do === 'pad_wizard') padGo('wizard');
    else if (a.do === 'pad_known') padGo('known');
    else if (a.do === 'estop') padGo('estop');
    else if (a.do === 'remote') padGo('remote');
    else if (a.do === 'robotd' || a.do === 'logs') location.hash = '#/logs';   // 第 3 步「主程序模式」那一项的「去看日志页」
  };
  // 第 3 步：预览（小助手 dry_run / 电脑上 Studio 直接算）与真正写入。写入回到 ack 就开始，进度看 st.commit.job。
  const commitPreview = async () => {
    if (cprev) { setCprev(null); return; }
    setCmsg(null);
    const r = await link.cmd('calib.op', { op: 'preview' }, 20000);
    if (r.ok) setCprev(r.data || {});
    else setCmsg({ ok: false, text: r.reason || r.error || '算不出来' });
  };
  const commitRun = async () => {
    setCmsg(null);
    setCbusy(true);
    try {
      const r = await link.cmd('calib.op', { op: 'commit' }, 20000);
      if (!r.ok) setCmsg({ ok: false, text: r.reason || r.error || '没开始写' });
      else setCmsg({ ok: true, text: '开始写了：主程序会重启，等它回来' });
    } finally {
      setCbusy(false);
    }
  };
  const btn = (a, row, dark) => html`<button class=${dark ? 'btn-dark' : 'btn-light'} onClick=${() => go(a, row)}>${a.label}</button>`;
  const frozen = !!ob.frozen;               // 调舵机时清单冻着：标、取消都要点两次（标了要交还以后才显示）
  const fa = ob.frozen_ask || {};
  // 「已完成」（done）/「跳过」（skip）：后端说要点两次（mark.*.ask）或者冻着，用 TwoStep，第二下才带 confirmed
  const markBtn = (row, kind, label, cls) => {
    const ask = (row.mark || {})[kind]?.ask || (frozen ? fa[kind] : null);
    const args = { op: kind, step: row.key };
    return ask ? html`<${TwoStep} label=${label} ask=${ask} cls=${'btn-light ' + cls} askCls="btn-light onb-skip-arm"
        onConfirm=${() => op({ ...args, confirmed: true })} />`
      : html`<button class=${'btn-light ' + cls} onClick=${() => op(args)}>${label}</button>`;
  };
  const undoBtn = (row) => {
    const args = { op: row.marked === 'skip' ? 'unskip' : 'undone', step: row.key };
    const label = row.marked === 'skip' ? '取消跳过' : '取消';
    return frozen ? html`<${TwoStep} label=${label} ask=${fa.undo || '确定取消？'} cls="btn-light onb-unskip" askCls="btn-light onb-skip-arm"
        onConfirm=${() => op({ ...args, confirmed: true })} />`
      : html`<button class="btn-light onb-unskip" onClick=${() => op(args)}>${label}</button>`;
  };
  return html`<section class=${'onb board' + (ob.frozen ? ' onb-frozen' : '')} aria-label="上手清单">
    <div class="onb-h">
      <b>上手清单</b>
      ${done ? html`<button class="onb-fold onb-fold-h" aria-expanded="true" onClick=${toggle}><span class="onb-mark onb-ok">✓</span>${doneText}
        <span class="onb-more">收起</span></button>`
        : head.ok ? html`<span class="onb-conn"><span class="onb-mark onb-ok">✓</span>连上鸭子</span>`
        : html`<span class="onb-conn onb-conn-bad"><${Badge} kind=${head.kind === 'servo' || head.kind === 'connecting' || head.kind === 'bench' ? 'warn' : 'bad'} />
          ${head.text}${head.raw ? html`<small>原文：${head.raw}</small>` : null}</span>`}
      ${ob.frozen ? html`<span class="onb-tip onb-noskip">${ONB_FROZEN}</span>` : null}
    </div>
    ${msg ? html`<div class=${'banner ' + (msg.ok ? 'warn' : 'bad')} role="status"><span>${msg.text}</span>
      <a href="#" onClick=${(e) => { e.preventDefault(); setMsg(null); }}>知道了</a></div>` : null}
    <ol class="onb-rows">
      ${(ob.rows || []).map((r) => {
        const now = ob.next === r.key;
        const when = fmtWhen(r.when);
        const goA = { do: r.go, label: '去做' };
        // 第 3 步那张卡片只在「写进主程序」这一行里展开：还没做（● / ○）、正在写 / 刚写完、或者点了「去看看」「重新做」；
        // 做完了（✓）、跳过了、判不出（?）时收着
        const isCommit = r.key === 'commit' && (r.state === 'todo' || r.state === 'no' || copen || !!job || !!cmsg || !!cprev);
        let acts;
        if (r.marked) {
          // 用户自己标的 ✓ / ↷：「重新做」（就是那一步的去做）+「取消」/「取消跳过」（审查 D12）
          acts = html`<button class="btn-light onb-redo" onClick=${() => go(goA, r)}>重新做</button>${undoBtn(r)}`;
        } else if (r.state === 'ok') acts = html`<button class="btn-light onb-redo" onClick=${() => go(goA, r)}>重新做</button>`;
        else {
          // 三个按钮：去做（这一行自己的动作，没有就去 GO 那一页）、已完成、跳过。测出了问题的行（found：审查 D5）第一个写「重新做」
          const first = (r.actions || []).length ? r.actions : [goA];
          acts = html`${first.map((a, i) => btn(r.found && i === 0 ? { ...a, label: '重新做' }
            : now && i === 0 ? { ...a, label: '去做' } : a, r, now && i === 0))}${markBtn(r, 'done', '已完成', 'onb-donebtn')}${markBtn(r, 'skip', '跳过', 'onb-skip')}`;
        }
        return html`<li class=${'onb-row onb-' + r.state + (now ? ' onb-now' : '')} key=${r.key} data-step=${r.key}>
          <span class=${'onb-mark onb-' + r.state} aria-label=${ONB_WORD[r.state] || '读不到'}>${ONB_MARK[r.state] || '?'}</span>
          <span class="onb-body">
            <span class="onb-title"><b>${r.n} ${r.title}</b>${now ? html`<span class="onb-tag">现在该做</span>` : null}${when && !r.marked ? html`<span class="onb-when num">${when}</span>` : null}</span>
            <span class="onb-text">${r.text}${r.marked ? `（${when || '时间不知道'}）` : ''}</span>
            ${r.note ? html`<span class="onb-tip">${r.marked ? `现在：${r.note}` : r.note}</span>` : null}
            ${r.tip && r.state !== 'ok' && r.state !== 'skip' ? html`<span class="onb-tip">坑：${r.tip}</span>` : null}
            ${isCommit ? html`<div class="onb-commit">
              <div class="onb-commit-desc">${r.desc || ''}</div>
              ${(r.commit?.items || []).length ? html`<ul class="onb-checks">
                ${r.commit.items.map((it) => html`<li class=${it.ok ? 'ok' : 'bad'}>
                  <span class="onb-check-mark" aria-label=${it.ok ? '好' : '还不行'}>${it.ok ? '✓' : '×'}</span>
                  <span class="onb-check-body"><b>${it.label}</b><span>${it.text}</span></span>
                  ${!it.ok && it.action ? html`<button class="btn-light onb-check-go" onClick=${() => go(it.action, r)}>${it.action.label || '去做'}</button>` : null}
                </li>`)}
              </ul>` : null}
              <div class="onb-commit-acts">
                <button class="btn-light" disabled=${cbusy} onClick=${commitPreview}>${cprev ? '收起要写的内容' : '先看看要写什么'}</button>
                ${/* 第 2 步标了、IMU 按默认装法写（任务 17）：第二下写清楚 */ ''}
                <${TwoStep} label="写进主程序" ask=${r.commit?.imu_default ? '确定？IMU 按默认装法写' : '会重启主程序，鸭子会先卸力，扶好鸭子'}
                  cls="btn-dark" disabled=${!r.commit?.ready || cbusy}
                  title=${!r.commit?.ready ? '准备情况还没全绿：照上面的清单补齐' : ''} onConfirm=${commitRun} />
              </div>
              ${cmsg ? html`<div class=${'onb-commit-msg ' + (cmsg.ok ? 'ok' : 'bad')}>${cmsg.text}</div>` : null}
              ${job && job.phase === 'running' ? html`<div class="onb-commit-job" role="status"><span class="onb-spin">⋯</span>${job.msg || '在写…'}</div>` : null}
              ${job && job.phase !== 'running' && job.reason ? html`<div class=${'onb-commit-msg ' + (job.ok ? 'ok' : 'bad')}>${job.reason}</div>` : null}
              ${cprev ? html`<div class="onb-preview">
                <div class="onb-preview-imu"><b>IMU</b> ${cprev.imu?.label || ''} · ${cprev.imu?.text || ''}${cprev.imu?.default ? `（按默认装法写：${cprev.imu.default}）` : ''}</div>
                <table class="onb-preview-table"><thead><tr><th>关节</th><th>方向</th><th>限位</th></tr></thead><tbody>
                  ${(cprev.joints || []).map((j) => html`<tr><td>${j.cn || j.name}</td><td>${j.direction > 0 ? '+1' : '−1'}</td><td class="num">${j.min_deg}° ~ ${j.max_deg}°</td></tr>`)}
                </tbody></table>
                <details class="onb-preview-toml"><summary>要写的文件</summary><pre>${cprev.toml || ''}</pre></details>
              </div>` : null}
            </div>` : null}
          </span>
          <span class="onb-acts">${acts}</span>
        </li>`;
      })}
    </ol>
  </section>`;
}

// 待办：上手清单已经把同一件事说清楚了，待办里才不重复（审查 O15）。清单那一行 / 卡片头真的在说这件事才去重：
// 清单写「?」（读不到）的时候待办照样留着（代码审查 C4）
const TODO_IN_ONB = {
  starting: (ob) => ['starting', 'stuck_calib'].includes(ob.header?.kind),
  absent: (ob) => ob.header?.kind === 'absent',
  servo_session: (ob) => ob.header?.kind === 'servo',
  imu_unverified: (ob) => ['todo', 'no'].includes((ob.rows || []).find((r) => r.key === 'imu')?.state),
};
function Todo({ st, live }) {
  const ob = st?.onboarding;
  const all = (st?.todo || []).filter((it) => !(ob && TODO_IN_ONB[it.key] && TODO_IN_ONB[it.key](ob)));
  const items = all.slice(0, 2);
  const more = all.length - items.length;
  if (!live) return html`<section class="todo board"><span class="quiet">断线时不检查</span></section>`;
  if (!items.length) {
    return html`<section class="todo board"><span class="quiet">没有要处理的事</span>
      <span class="when num">${st?.health_age_ms != null ? `${Math.round(st.health_age_ms / 1000)} 秒前查过` : ''}</span></section>`;
  }
  return html`<section class="todo board list">
    ${items.map((it) => html`<div class="item">
      <${Badge} kind=${it.level === 'error' ? 'bad' : 'warn'} />
      <span class="msg">${it.msg}</span>
      <span class="hint">${it.hint || ''}</span>
    </div>`)}
    ${more > 0 ? html`<a href="#/logs" style="font-size:13px">还有 ${more} 条 ›</a>` : null}
  </section>`;
}

// 总览：（开着热点时最上面是「连 WiFi」）→ 舞台 → 仪表带（5 格）→ 上手清单 → 待办 → 往下一刷就是手柄（总览重排 §1）
function Overview({ st, live, age, diagnosticElapsed, duck, pd, onSame, tune, sensorsActive, sv, imu, connectionLive, svElapsed, imuElapsed }) {
  // 主机名这类英文名后面空一格再接中文：「duck-c51b 站好了」
  const short = duck?.short || '鸭子';
  const name = /[A-Za-z0-9]$/.test(short) ? short + ' ' : short;
  return html`<div class="overview">
    ${st?.net?.known && st.net.mode === 'hotspot'
      ? html`<${WifiCard} net=${st.net} link=${link} secure=${st?.auth?.has_password} />` : null}
    <${Stage} st=${st} live=${live} age=${age} name=${name} />
    <${Band} st=${st} pd=${pd} live=${live} diagnosticElapsed=${diagnosticElapsed} gray=${age > STALE_GRAY_MS} onSame=${onSame} sensorsActive=${sensorsActive} />
    <${AiSshEntry} />
    <${Onboard} st=${st} live=${live} />
    <${Todo} st=${st} live=${live} />
    <${PadPage} st=${st} pad=${pd} link=${link} api=${api} previewActive=${sensorsActive} live=${live} />
    <${RuntimeDiagnostics} st=${st} sv=${sv} imu=${imu} link=${link} active=${sensorsActive}
      live=${connectionLive} summaryLive=${live} svElapsed=${svElapsed} imuElapsed=${imuElapsed} />
    <${TuneSection} tune=${tune} />
  </div>`;
}

// ---------------- 日志 ----------------

function Logs() {
  const [items, setItems] = useState([]);
  const [onlyBad, setOnlyBad] = useState(false);
  const [cat, setCat] = useState('');
  useEffect(() => {
    api('/api/logs?limit=300').then((r) => setItems(r.items || []));
    return link.on((m) => { if (m.t === 'log') setItems((xs) => [...xs.slice(-499), m.entry]); });
  }, []);
  const shown = items.filter((e) => (!onlyBad || e.level !== 'info') && (!cat || e.cat === cat));
  const cats = [...new Set(items.map((e) => e.cat))];
  const copy = () => navigator.clipboard && navigator.clipboard.writeText(shown.map((e) =>
    `${e.t} [${e.cat}]${e.level === 'info' ? '' : `[${e.level}]`} ${e.msg}${e.step ? ` · ${e.level === 'info' ? '步骤' : '卡在'}：${e.step}` : ''}${e.raw ? ` · 原文：${e.raw}` : ''}${e.hint ? ` · 建议：${e.hint}` : ''}`).join('\n'));
  return html`<div style="display:flex;flex-direction:column;gap:12px">
    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:12px">
      <h1 style="margin:0;font-size:28px;font-weight:600;flex:1">日志</h1>
      <select value=${cat} onChange=${(e) => setCat(e.target.value)} aria-label="按分类看">
        <option value="">全部分类</option>${cats.map((c) => html`<option value=${c}>${c}</option>`)}
      </select>
      <label style="font-size:14px"><input type="checkbox" checked=${onlyBad} onChange=${(e) => setOnlyBad(e.target.checked)} /> 只看警告和错误</label>
      <button onClick=${copy} style="height:34px;padding:0 14px;border:0;border-radius:999px;background:#E5E5EA">复制</button>
      <a class="logs-export" href="/api/diag" style="font-size:14px" title="含鸭子的序列号和局域网 IP">导出诊断包（发群里求助用）</a>
    </div>
    <div class="board" style="padding:8px 16px;font-size:14px;line-height:1.7">
      ${shown.length ? shown.slice().reverse().map((e) => html`<div key=${e.n} style="padding:4px 0;border-top:1px solid var(--line)">
        <span class="num" style="color:var(--text-2)">${e.t}</span> <span style="color:var(--text-2)">[${e.cat}]</span>
        ${e.level === 'warn' ? html` <${Badge} kind="warn" />` : e.level === 'error' ? html` <${Badge} kind="bad" />` : ''} ${e.msg}
        ${e.step || e.raw || e.hint ? html`<div style="font-size:13px;color:var(--text-2)">
          ${e.step ? html`${e.level === 'info' ? '步骤' : '卡在'}：${e.step}　` : ''}${e.raw ? html`原文：${e.raw}　` : ''}${e.hint ? html`建议：${e.hint}` : ''}</div>` : null}
      </div>`) : html`<div style="color:var(--text-2)">还没有日志。</div>`}
    </div>
  </div>`;
}

// 页面每秒收 10 次状态，Main 会跟着重绘；日志页跟状态无关，别跟着把 500 行重画一遍（代码审查 #20）
class LogsPage extends Component {
  shouldComponentUpdate() { return false; }
  render() { return html`<${Logs} />`; }
}

// ---------------- 设置（第一片只有舞台背景） ----------------

const STAGES = [['mid', '中灰（推荐）'], ['graphite', '石墨'], ['light', '浅灰']];
function applyStage(v) {
  if (v === 'mid') document.documentElement.removeAttribute('data-stage');
  else document.documentElement.setAttribute('data-stage', v);
}
function loadStage() { try { return localStorage.getItem('studio.stage') || 'mid'; } catch { return 'mid'; } }

// Setting the first web password replaces the anonymous cookie/CSRF session.
// Refresh it before the next SSH POST and reconnect the status stream with that cookie.
async function refreshAccessSession() {
  const session = await api('/api/session');
  if (session.status !== 200 || !session.csrf) throw new Error('session unavailable');
  csrf = session.csrf;
  link.reconnect();
}

function Settings({ st, tune }) {
  const [v, setV] = useState(loadStage());
  const pick = (x) => { setV(x); applyStage(x); try { localStorage.setItem('studio.stage', x); } catch { /* 隐私模式 */ } };
  return html`<div style="display:flex;flex-direction:column;gap:16px;max-width:640px">
    <h1 style="margin:0;font-size:28px;font-weight:600">设置</h1>
    <${AiSshEntry} />
    <${UpdateSettings} api=${api} csrf=${csrf} />
    <${AiSettings} api=${api} />
    <div class="board" style="padding:16px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center">
      <span style="font-size:15px;font-weight:600;flex:1">日志和诊断</span>
      <a class="settings-logs" href="#/logs" style="font-size:15px">日志</a>
      <a class="settings-diag" href="/api/diag" style="font-size:15px" title="含鸭子的序列号和局域网 IP">导出诊断包</a>
    </div>
    <${DuckAddresses} net=${st?.net} />
    <${WifiSettings} net=${st?.net} link=${link} />
    <${AccessGuide} api=${api} hasPassword=${st?.auth?.has_password} net=${st?.net} refreshSession=${refreshAccessSession} />
    <${BatterySettings} link=${link} st=${st} />
    <${ModelSettings} tune=${tune} link=${link} />
    <div class="board" style="padding:16px 20px">
      <div style="font-size:15px;font-weight:600;margin-bottom:10px">舞台背景</div>
      ${STAGES.map(([k, label]) => html`<label style="display:flex;align-items:center;gap:10px;height:40px">
        <input type="radio" name="stage" checked=${v === k} onChange=${() => pick(k)} /> ${label}</label>`)}
      <div style="font-size:13px;color:var(--text-2)">只影响这台电脑或手机上的显示。</div>
    </div>
    <button onClick=${async () => { await api('/api/logout', {}); location.reload(); }}
      style="align-self:flex-start;height:40px;padding:0 18px;border:0;border-radius:999px;background:#E5E5EA">退出登录</button>
  </div>`;
}

function Later({ what }) {
  return html`<div class="board" style="padding:24px"><h1 style="margin:0 0 8px;font-size:28px;font-weight:600">${what}</h1>
    <p style="margin:0;color:var(--text-2)">这一页在后面的切片里做。</p></div>`;
}

// ---------------- 外壳 ----------------

// #/ 和 #/pad 是同一页（总览）：「手柄」是总览上的锚点（审查 O13）。高亮：手柄那一块在屏幕上亮「手柄」，否则亮「总览」。
// 地址没变时（再点一次同一个）不会有 hashchange，onSame 自己滚
const OVERVIEW = (r) => r === '/' || r === '/pad';
function navCur(route, padInView) {
  return (r) => {
    if (r === '/settings') return route === '/settings' || route === '/settings/ssh' ? 'page' : undefined;
    if (r === '/pad') return OVERVIEW(route) && padInView ? 'page' : undefined;
    if (r === '/') return OVERVIEW(route) && !padInView ? 'page' : undefined;
    return route === r ? 'page' : undefined;
  };
}

function Nav({ route, padInView, onSame }) {
  const cur = navCur(route, padInView);
  const a = (href, r, text) => html`<a href=${href} aria-current=${cur(r)} onClick=${(e) => onSame(e, href)}>${text}</a>`;
  return html`<nav class="nav" aria-label="页面">
    <div class="nav-logo"><svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#1D1D1F"></rect><path d="M6 14.5a7 6 0 0 1 14 0z" fill="#FFFFFF"></path><rect x="6" y="14.5" width="14" height="2.6" rx="1.3" fill="#F5B400"></rect><circle cx="16.2" cy="11.4" r="1.3" fill="#1D1D1F"></circle></svg>Studio</div>
    ${a('#/', '/', '总览')}
    ${a('#/pad', '/pad', '手柄')}
    ${a('#/camera', '/camera', '摄像头')}
    ${a('#/tof', '/tof', 'ToF')}
    ${a('#/servos', '/servos', '舵机')}
    ${a('#/imu', '/imu', 'IMU')}
    ${a('#/models', '/models', '模型')}
    ${a('#/logs', '/logs', '日志')}
    <span class="spacer"></span>
    ${a('#/settings', '/settings', '设置')}
  </nav>`;
}

// 模型管理在手机底栏也能直接打开；顺序跟侧栏一致。
function TabBar({ route, padInView, onSame }) {
  const cur = navCur(route, padInView);
  const a = (href, r, text) => html`<a href=${href} aria-current=${cur(r)} onClick=${(e) => onSame(e, href)}>${text}</a>`;
  return html`<nav class="tabbar" aria-label="主导航">
    ${a('#/', '/', '总览')}${a('#/pad', '/pad', '手柄')}${a('#/camera', '/camera', '摄像头')}${a('#/tof', '/tof', 'ToF')}${a('#/servos', '/servos', '舵机')}${a('#/imu', '/imu', 'IMU')}${a('#/models', '/models', '模型')}${a('#/settings', '/settings', '更多')}
  </nav>`;
}

// 离开某一页之前问一句（舵机页：有舵机在保持时，离开会交还主程序、舵机会全部松开）
export const leaveGuards = {};
function useRoute() {
  const get = () => (location.hash.replace(/^#/, '') || '/');
  const [r, setR] = useState(get());
  const cur = useRef(r);
  useEffect(() => {
    const f = () => {
      const next = get();
      const guard = leaveGuards[cur.current];
      if (next !== cur.current && guard && !guard()) { history.replaceState(null, '', '#' + cur.current); return; }
      cur.current = next;
      // 先同步算「在不在手柄区」（审查 O1）：切到别的页的这一刻就全部松开、发 drive off，不等重绘、不等 IntersectionObserver
      padActivity.setRoute(next);
      setR(next);
      link.heartbeat();                   // 马上告诉 Studio 换页了（交还 / 接管不用等下一次心跳）
    };
    window.addEventListener('hashchange', f);
    return () => window.removeEventListener('hashchange', f);
  }, []);
  return r;
}

// 换页、点导航时滚到哪：#/pad 滚到手柄那一块（scroll-margin-top 扣掉吸顶的顶栏），别的都回到顶上
function scrollFor(route) {
  if (route === '/settings/ssh') {
    requestAnimationFrame(() => {
      const guide = document.getElementById('ssh-guide');
      guide?.scrollIntoView({ block: 'start' });
      guide?.focus({ preventScroll: true });
    });
    return;
  }
  if (route === '/pad') {
    const el = document.getElementById('pad-block');
    if (el) { el.scrollIntoView({ block: 'start' }); return; }
  }
  window.scrollTo(0, 0);
}

const PAD_HEAVY = ['skills', 'actions', 'combos', 'sounds'];

function Main({ duck }) {
  const route = useRoute();
  const [st, setSt] = useState(null);
  const [sv, setSv] = useState(null);
  const [imu, setImu] = useState(null);
  const [tune, setTune] = useState(null);
  const [pd, setPd] = useState(null);
  const [stAt, setStAt] = useState(0);
  const [svAt, setSvAt] = useState(0);
  const [imuAt, setImuAt] = useState(0);
  const imuContext = useRef(null);
  const [status, setStatus] = useState(link.status);
  const [padInView, setPadInView] = useState(padActivity.inView);
  const [navTick, setNavTick] = useState(0);
  const firstScroll = useRef(route === '/pad');       // 打开 #/pad：第一份 state 和 pad 都到了再滚一次（上面的高度还会变）
  const now = useNow(250);
  const barRef = useStickyHeight('--bar-h');

  useEffect(() => {
    const off = link.on((m) => {
      if (m.t === '_status') {
        setStatus(m.status);
        if (m.status !== 'open') { setImu(null); setImuAt(0); }
      }
      else if (m.t === 'state') {
        const context = JSON.stringify([m.mode, m.build, m.imu?.source, m.imu?.configured_imu_profile,
          m.posture === 'starting', m.imu?.context_current, m.imu?.generation]);
        if (imuContext.current !== null && imuContext.current !== context) { setImu(null); setImuAt(0); }
        imuContext.current = context;
        // 上手清单没变时后端不每帧带（onboarding_same，代码审查 C5）：沿用上一份
        setSt((prev) => (m.onboarding_same && prev ? { ...m, onboarding: prev.onboarding } : m));
        setStAt(performance.now());
      }
      else if (m.t === 'servo') { setSv(m); setSvAt(performance.now()); }
      else if (m.t === 'imu') { setImu(m); setImuAt(performance.now()); }
      else if (m.t === 'tune') setTune(m);
      else if (m.t === 'pad') {
        // 长列表（动作、组合动作、技能、声音）没变时后端不再每帧推（审查 O12）：缺了就沿用上一份
        setPd((prev) => {
          if (!m.lists_same || !prev?.motion) return m;
          const keep = Object.fromEntries(PAD_HEAVY.filter((k) => k in prev.motion).map((k) => [k, prev.motion[k]]));
          return { ...m, motion: { ...m.motion, ...keep } };
        });
      }
    });
    const offAct = padActivity.subscribe(({ inView }) => setPadInView(inView));
    // 用户自己动了（滚轮、手指、键盘）：第一份数据到了也别再替他滚
    const stop = () => { firstScroll.current = false; };
    window.addEventListener('wheel', stop, { passive: true, once: true });
    window.addEventListener('touchstart', stop, { passive: true, once: true });
    window.addEventListener('keydown', stop, { once: true });
    link.connect();
    return () => { off(); offAct(); };
  }, []);

  // 换页、或者点了正在看的那一项（navTick）：滚到该去的地方
  useEffect(() => { scrollFor(route); }, [route, navTick]);
  useEffect(() => {
    if (firstScroll.current && st && pd && route === '/pad') {
      firstScroll.current = false;
      requestAnimationFrame(() => scrollFor('/pad'));
    }
  }, [!!st, !!pd]);
  // 再点一次同一个地址：浏览器不发 hashchange，自己滚（审查 O13）
  const onSame = (e, href) => {
    if ((location.hash || '#/') === href) { e.preventDefault(); setNavTick((n) => n + 1); }
  };

  if (status === 'auth') { location.reload(); return null; }
  // 新鲜度 = 浏览器多久没收到状态，加上 Studio 自己多久没从 robotd 拿到数据
  const age = st ? (now - stAt) + (st.robotd_age_ms || 0) : Infinity;
  const live = status === 'open' && age < STALE_ALARM_MS && st?.robotd_ok !== false;
  // 总览一直挂着、只是藏起来：3D 鸭子不随切页面销毁重建（three.js 每建一次留一份内部对象，
  // 浏览器能同时开的 WebGL 上下文也有限）。手柄那一块也在总览里（#/pad 是它的锚点），一直挂着：
  // 「在不在手柄区」看 padActivity，不看挂没挂上（审查 O1）
  const overview = html`<div hidden=${!OVERVIEW(route)}><${Overview} st=${st} live=${live} age=${age} diagnosticElapsed=${now - stAt} duck=${duck} pd=${pd} onSame=${onSame} tune=${tune} sensorsActive=${OVERVIEW(route)}
    sv=${sv} imu=${imu} connectionLive=${status==='open'} svElapsed=${svAt?now-svAt:Infinity} imuElapsed=${imuAt?now-imuAt:Infinity} /></div>`;
  let page = null;
  if (OVERVIEW(route)) page = null;
  else if (route === '/logs') page = html`<${LogsPage} />`;
  else if (route === '/camera') page = html`<${CameraPage} api=${api} />`;
  else if (route === '/tof') page = html`<${TofPage} api=${api} />`;
  else if (route === '/servos') page = html`<${ServoPage} sv=${sv} link=${link} leaveGuards=${leaveGuards} />`;
  else if (route === '/settings' || route === '/settings/ssh') page = html`<${Settings} st=${st} tune=${tune} />`;
  else if (route === '/models') page = html`<${ModelsPage} api=${api} csrf=${csrf} link=${link} live=${live} />`;
  else if (route === '/imu') page = html`<${ImuPage} st=${st}
    imu=${imu && st?.imu?.diagnostic ? { ...imu, diagnostic: st.imu.diagnostic } : imu}
    link=${link} live=${live} age=${age} diagnosticElapsed=${now - (st?.imu?.diagnostic ? stAt : imuAt)} pd=${pd} />`;
  else page = html`<${Later} what="这一页" />`;

  document.title = `${duck?.label || '鸭子'} · Microduck Studio`;
  return html`<div class="shell">
    <${Nav} route=${route} padInView=${padInView} onSame=${onSame} />
    <main class="main">
      <${TopBar} duck=${duck} st=${st} live=${live} age=${age} open=${status === 'open'}
        hideEStop=${route === '/servos' && sv?.state === 'open'} />
      <div class="sticky-bar" ref=${barRef}><${ServoBar} st=${st} onServos=${route === '/servos'} /></div>
      ${overview}${page}
    </main>
    <${TabBar} route=${route} padInView=${padInView} onSame=${onSame} />
  </div>`;
}

function Login({ onDone, duck, noPassword }) {
  const [pw, setPw] = useState('');
  const [remember, setRemember] = useState(true);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    let r;
    try { r = await api('/api/login', { password: pw, remember }); } catch { r = { error: '连不上鸭子，检查网络后再试' }; }
    setBusy(false);
    if (r.ok) onDone(); else setErr(r.error || '登录没成功');
  };
  return html`<form class="login board" onSubmit=${submit} autocomplete="on">
    <h1>${duck?.label || 'Microduck Studio'}</h1>
    <input type="text" name="username" autocomplete="username" value="admin" hidden readOnly />
    <label>管理员密码
      <input type="password" name="password" autocomplete="current-password" value=${pw} onInput=${(e) => setPw(e.target.value)} required autoFocus />
    </label>
    <label class="row"><input type="checkbox" checked=${remember} onChange=${(e) => setRemember(e.target.checked)} /> 记住我（30 天）</label>
    ${noPassword ? html`<div class="err">还没设管理员密码。在鸭子上运行：<code>python3 -I run.py set-password</code></div>` : null}
    ${err ? html`<div class="err" role="alert">${err}</div>` : null}
    <button type="submit" disabled=${busy || !pw}>登录</button>
  </form>`;
}

function App() {
  const [sess, setSess] = useState(undefined);
  const [noPw, setNoPw] = useState(false);
  const [down, setDown] = useState(false);
  const load = () => api('/api/session')
    .then((r) => { setDown(false); csrf = r.csrf || ''; setNoPw(r.has_password === false); setSess(r.status === 200 ? r : null); })
    .catch(() => { setDown(true); setTimeout(load, 2000); });   // Studio 正在重启：2 秒后再问
  useEffect(() => { applyStage(loadStage()); load(); }, []);
  if (sess === undefined) {
    return down ? html`<div class="login board"><h1>Microduck Studio</h1><p>连不上鸭子上的 Studio，正在重试…</p></div>` : null;
  }
  if (!sess) return html`<${Login} onDone=${load} duck=${null} noPassword=${noPw} />`;
  return html`<${Main} duck=${sess.duck} />`;
}

render(html`<${App} />`, document.getElementById('app'));
