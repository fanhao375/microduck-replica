// 手机中控的外壳：会话 → 连接 → 顶栏（鸭名 + 卸力）→ 页面 → 底栏。
//
// 跟桌面版的关系：同一个 Studio 进程、同一个 /ws、同一套 pad.op 命令，只是换了一副为竖屏手机做的壳。
// 这一层只负责三件事：把状态分发给各页、管住「卸力」这条永远能按的路、以及底栏路由。
import { html, render, useState, useEffect, useRef, useCallback } from '../vendor/preact-htm.module.js';
import { link, session, login, api, csrfToken } from './link.js';
import { Overview } from './overview.js';
import { VoicePage } from './voice.js';
import { GamepadPage } from './gamepad.js';
import { CameraPage } from './camera.js';
import { MorePage } from './more.js';
// 模型页不另写一套：直接内嵌桌面版的 ModelsPage，只把外壳换成手机底栏。
import { ModelsPage } from '../models.js';

const STALE_MS = 1500;      // 超过 1.5 秒没有状态帧：算「读不到」了
const GREY_MS = 1000;       // 超过 1 秒：数值变灰

const ICONS = {
  '/': html`<svg viewBox="0 0 24 24"><path d="M3 10.5 12 4l9 6.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>`,
  '/voice': html`<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="10" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg>`,
  '/pad': html`<svg viewBox="0 0 24 24"><path d="M7 8h10a5 5 0 0 1 4.9 6l-.6 2.4A2.6 2.6 0 0 1 16 17.6L14.5 16h-5L8 17.6a2.6 2.6 0 0 1-5.3-1.2L2.1 14A5 5 0 0 1 7 8Z"/><path d="M7.5 12.5v2M6.5 13.5h2M16 12.5h.01M17.5 14h.01"/></svg>`,
  '/models': html`<svg viewBox="0 0 24 24"><path d="M12 3 3 7.5v9L12 21l9-4.5v-9z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/></svg>`,
  '/more': html`<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>`,
};

// camera 现在挂在「更多」下面（#/more/camera）；「模型」也是手机壳内部的整页，内嵌桌面版组件。
// 五个 tab 全部是内部路由，底栏始终留在原位。
const TABS = [
  { href: '/', label: '总览' },
  { href: '/voice', label: '语音' },
  { href: '/pad', label: '手柄' },
  { href: '/models', label: '模型' },
  { href: '/more', label: '更多' },
];

const KNOWN = new Set([...TABS.map((t) => t.href), '/pad', '/more/camera']);
const routeOf = () => {
  const r = '/' + location.hash.replace(/^#\/?/, '');
  return KNOWN.has(r) ? r : '/';
};

// ---------------- 昵称 ----------------
// 默认拿鸭子报上来的 label（镜像里是主机名，比如 duck-a1b2，电脑台架上是 DESKTOP-XXXX）。
// 主机名当昵称不好看，所以长得像主机名的一律显示「鸭鸭」；用户自己起的名字存在这台手机上。
const NICK_KEY = 'duck.nickname';
const hostish = (s) => /^duck-[0-9a-z]{4,}$/i.test(s) || /^[A-Z0-9][A-Z0-9-]{2,}$/.test(s);

function nickStored() {
  try { return localStorage.getItem(NICK_KEY) || ''; } catch { return ''; }
}

// ---------------- 顶栏 ----------------

function Head({ name, dot, onEstop, busy }) {
  return html`<header class="m-head">
    <div class="m-head-in">
      <div class="m-duck">
        <span class=${'m-dot ' + dot} aria-hidden="true"></span>
        <span class="m-duck-name">${name}</span>
      </div>
      <span class="m-head-gap"></span>
      <button class="m-estop" onClick=${onEstop} disabled=${busy}
        aria-label="卸力：让所有舵机松掉力气">卸力</button>
    </div>
  </header>`;
}

// ---------------- 登录（只有设了网页密码的鸭子才出现） ----------------

function Login({ onDone }) {
  const [pw, setPw] = useState('');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr(null);
    const r = await login(pw, true);
    setBusy(false);
    if (r.ok) onDone(); else setErr(r.error);
  };
  return html`<form class="m-login" onSubmit=${submit}>
    <h1>登录这只鸭子</h1>
    <p>它设了网页密码。输一次，这台手机会记 30 天。</p>
    <input type="password" inputmode="text" autocomplete="current-password" placeholder="网页密码"
      value=${pw} onInput=${(e) => setPw(e.currentTarget.value)} autoFocus />
    ${err ? html`<div class="m-banner bad" style="margin-top:12px">${err}</div>` : null}
    <button class="m-btn primary" type="submit" disabled=${busy || !pw}>${busy ? '正在登录…' : '登录'}</button>
  </form>`;
}

// ---------------- 外壳 ----------------

function App() {
  const [phase, setPhase] = useState('boot');       // boot / login / ready
  const [hello, setHello] = useState(null);
  const [st, setSt] = useState(null);
  const [stAt, setStAt] = useState(0);
  const [pad, setPad] = useState(null);
  const [conn, setConn] = useState('connecting');
  const [route, setRoute] = useState(routeOf());
  const [now, setNow] = useState(performance.now());
  const [toast, setToast] = useState(null);
  const [estopBusy, setEstopBusy] = useState(false);
  const toastTimer = useRef(null);

  const say = useCallback((text, bad = false) => {
    setToast({ text, bad });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), bad ? 3600 : 2200);
  }, []);

  // 会话：200 直接进；401 先登录
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await session();
        if (dead) return;
        setPhase(r.status === 200 ? 'ready' : r.status === 401 ? 'login' : 'ready');
      } catch { if (!dead) setPhase('ready'); }        // Studio 抽风：先按开放模式进，页面自己会显示连不上
    })();
    return () => { dead = true; };
  }, []);

  // 连接 + 消息分发
  useEffect(() => {
    if (phase !== 'ready') return;
    const off = link.on((m) => {
      if (m.t === '_status') { setConn(m.status); if (m.status === 'auth') setPhase('login'); return; }
      if (m.t === 'hello') { setHello(m); return; }
      if (m.t === 'state') { setSt(m); setStAt(performance.now()); return; }
      if (m.t === 'pad') { setPad(m); return; }
    });
    link.connect();
    const tick = setInterval(() => setNow(performance.now()), 500);
    return () => { off(); clearInterval(tick); };
  }, [phase]);

  // 路由
  useEffect(() => {
    const onHash = () => setRoute(routeOf());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // 告诉后端「我在哪一页」：手柄 / 语音页要看手柄快照（语音开关走 pad.op 的 audio 分支，
  // 后端要求这条连接没被标记成「离开了」），所以这两页要把 pad_visible 打开
  useEffect(() => {
    link.setRoute(route);
    link.setPadVisible(route === '/pad' || route === '/voice');
  }, [route]);

  const live = conn === 'open' && st && now - stAt < STALE_MS;
  const age = stAt ? now - stAt : Infinity;
  const [nick, setNick] = useState(nickStored());
  const label = hello?.duck?.label;
  const name = nick || (label && !hostish(label) ? label : '鸭鸭');
  const rename = useCallback((v) => {
    const clean = String(v || '').trim().slice(0, 12);
    setNick(clean);
    try { clean ? localStorage.setItem(NICK_KEY, clean) : localStorage.removeItem(NICK_KEY); } catch { /* 隐私模式 */ }
  }, []);
  const posture = st?.posture;

  const doEstop = useCallback(async () => {
    if (estopBusy) return;
    setEstopBusy(true);
    const r = await link.cmd('relax', {}, 7000);
    setEstopBusy(false);
    if (r.ok) say('已卸力，舵机松开了');
    else say(r.reason || r.error || '卸力没发出去', true);
  }, [estopBusy, say]);

  if (phase === 'boot') {
    return html`<div class="m-app"><div class="m-empty">正在连${'鸭子'}…</div></div>`;
  }
  if (phase === 'login') {
    return html`<div class="m-app"><${Login} onDone=${() => { setPhase('ready'); }} /></div>`;
  }

  let dot = 'warn';
  if (conn === 'open') dot = live ? 'on' : 'warn';
  else if (conn === 'closed' || conn === 'auth') dot = 'bad';

  let page = null;
  if (route === '/') page = html`<${Overview} st=${st} pad=${pad} live=${live} age=${age} name=${name} say=${say} />`;
  else if (route === '/voice') page = html`<${VoicePage} st=${st} pad=${pad} live=${live} name=${name} say=${say} />`;
  else if (route === '/pad') page = html`<${GamepadPage} st=${st} live=${live} say=${say} />`;
  else if (route === '/models') page = html`<${ModelsPage} api=${api} csrf=${csrfToken()} link=${link} live=${live} />`;
  else if (route === '/more/camera') page = html`<${CameraPage} live=${live} say=${say} />`;
  else page = html`<${MorePage} hello=${hello} st=${st} conn=${conn} say=${say} nick=${name} rename=${rename} />`;

  const go = (href) => { location.hash = '#' + href; };

  return html`<div class="m-app">
    <${Head} name=${name} dot=${dot} onEstop=${doEstop} busy=${estopBusy} />
    <main class="m-main">
      ${conn !== 'open' ? html`<div class="m-banner bad">
        <span>${conn === 'closed' ? '和鸭子的连接断了' : '正在连鸭子…'}</span>
        <button class="act" onClick=${() => link.reconnect()}>重新连接</button>
      </div>` : null}
      ${conn === 'open' && !live ? html`<div class="m-banner">
        <span>${isFinite(age) ? Math.round(age / 1000) + ' 秒没收到状态' : '还没收到状态'}，站着走的鸭子会自己停下。</span>
      </div>` : null}
      ${page}
    </main>
    <nav class="m-tabs" aria-label="页面">
      <div class="m-tabs-in">
        ${TABS.map((t) => {
          const on = t.href === route || (t.href === '/more' && route.startsWith('/more/'));
          return html`<a class="m-tab" href=${'#' + t.href} aria-current=${on ? 'page' : null}>
            ${ICONS[t.href]}<span>${t.label}</span>
          </a>`;
        })}
      </div>
    </nav>
    ${toast ? html`<div class=${'m-toast' + (toast.bad ? ' bad' : '')} role="status">${toast.text}</div>` : null}
  </div>`;
}

render(html`<${App} />`, document.getElementById('app'));

// 手机上按返回键是改 hash，不刷新页面，这里不用再管
window.addEventListener('unhandledrejection', (e) => { console.warn('未处理的异步错误', e.reason); });
