// 手机中控跟 Studio 的连接层。
//
// 为什么另写一份而不是 import ../app.js：app.js 是桌面版的入口，模块加载时就会挂载整个桌面界面
// （还带着 3D、手柄、传感器一堆副作用）。手机页只要同一套协议，不要那些副作用，所以这里把
// WebSocket 客户端和 fetch 封装按同一份契约重写一遍，行为跟桌面版逐条对齐：
// - 连接：ws(s)://<当前 host>/ws，握手被拒是 4401（登录过期）；
// - 心跳：每秒一次，带 route 和 pad_visible（后端靠它决定推 10 Hz 还是 1 Hz、要不要发手柄快照）；
// - 命令：{t:'cmd', id, name, args} → 回执 {t:'result', id, ok, reason, data}；
// - 改动类 POST 要带 X-CSRF（值来自 /api/session），配合后端的 Origin 同源校验。

export const link = {
  ws: null,
  seq: 0,
  padSeq: 0,
  pending: new Map(),
  listeners: new Set(),
  status: 'idle',        // idle / connecting / open / closed / auth
  backoff: 1000,
  route: '/',
  padVisible: false,
  padCapture: false,      // 模型改键的只读旁路：只要手柄快照，不接管遥控
  _hb: null,

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
  emit(m) { for (const fn of this.listeners) fn(m); },
  setStatus(s) { this.status = s; this.emit({ t: '_status', status: s }); },

  connect() {
    clearTimeout(this._retry);
    clearInterval(this._hb);
    try { this.ws && this.ws.close(); } catch { /* 已经关了 */ }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = this.ws = new WebSocket(`${proto}://${location.host}/ws`);
    let opened = false;
    this.setStatus('connecting');
    ws.onopen = () => {
      opened = true;
      this.backoff = 1000;
      this.setStatus('open');
      this.heartbeat();
      this._hb = setInterval(() => this.heartbeat(), 1000);
    };
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.t === 'hb') this.heartbeat();          // 后台标签页里定时器被节流，收到服务端心跳就回一个
      if (m.t === 'result' && this.pending.has(m.id)) {
        this.pending.get(m.id)(m);
        this.pending.delete(m.id);
      }
      this.emit(m);
    };
    ws.onclose = async (ev) => {
      if (this.ws !== ws) return;
      clearInterval(this._hb);
      for (const done of this.pending.values()) done({ ok: false, error: '和鸭子的连接断了' });
      this.pending.clear();
      if (ev.code === 4401) { this.setStatus('auth'); return; }
      if (!opened) {
        try {
          const r = await api('/api/session', undefined, 2000);
          if (r.status === 401) { this.setStatus('auth'); return; }
        } catch { /* Studio 不在 */ }
        if (this.ws !== ws) return;
      }
      this.setStatus('closed');
      this._retry = setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 8000);
    };
  },

  reconnect() { this.backoff = 1000; this.connect(); },

  whenOpen() {
    if (this.status === 'open') return Promise.resolve();
    return new Promise((resolve) => {
      const off = this.on((m) => { if (m.t === '_status' && m.status === 'open') { off(); resolve(); } });
      setTimeout(() => { off(); resolve(); }, 6000);      // 6 秒还没连上：别让调用方一直等
    });
  },

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) { this.ws.send(JSON.stringify(obj)); return true; }
    return false;
  },

  setRoute(route) { this.route = route; this.heartbeat(); },
  setPadVisible(v) { this.padVisible = Boolean(v); this.heartbeat(); },
  // 模型改键页面借用 10 Hz 手柄快照来捕获按键，但不改变 padVisible、也不接管遥控。
  setPadCapture(v) { this.padCapture = Boolean(v); this.heartbeat(); },
  nextPadSeq() { return ++this.padSeq; },

  heartbeat() {
    this.send({
      t: 'hb', seq: ++this.seq,
      visible: document.visibilityState === 'visible',
      route: this.route,
      pad_visible: this.padVisible || this.padCapture,
    });
  },

  cmd(name, args = {}, timeoutMs = 15000) {
    const id = `m${++this.seq}`;
    return new Promise((resolve) => {
      if (!this.send({ t: 'cmd', id, name, args })) { resolve({ ok: false, error: '和鸭子的连接断了' }); return; }
      this.pending.set(id, resolve);
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); resolve({ ok: false, error: '鸭子没回话（超时）' }); }
      }, timeoutMs);
    });
  },
};

let csrf = '';

export async function api(path, body, timeoutMs = 10000) {
  const opt = body === undefined
    ? { credentials: 'same-origin' }
    : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify(body) };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  let r, data = {};
  try {
    r = await fetch(path, { ...opt, signal: ctl.signal });
    try { data = await r.json(); } catch { /* 不是 JSON（比如 204） */ }
  } finally { clearTimeout(timer); }
  return { status: r.status, ...data };
}

export function setCsrf(value) { csrf = value || ''; }

/** 模型导入用 XHR 直传，拿不到封装好的 api：把当前 token 交给调用方自己拼请求头 */
export function csrfToken() { return csrf; }

/** 会话：没设密码的鸭子直接给 open 会话，设了就要登录 */
export async function session() {
  const r = await api('/api/session', undefined, 4000);
  if (r.status === 200) setCsrf(r.csrf);
  return r;
}

export async function login(password, remember = true) {
  const r = await api('/api/login', { password, remember }, 8000);
  if (r.status === 200 || r.ok) { const s = await session(); return s.status === 200 ? { ok: true } : { ok: false, error: '登录没生效' }; }
  return { ok: false, error: r.error || '密码不对' };
}
