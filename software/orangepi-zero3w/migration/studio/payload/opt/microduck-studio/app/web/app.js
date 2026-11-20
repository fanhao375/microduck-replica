// Live console shell: preserve the installed transport, authentication and command safety.
import {html,render,useState,useEffect,useRef,useCallback} from './vendor/preact-htm.module.js';
import {HandBackButton} from './servo.js?v=20261110-torque';
import {padActivity} from './pad.js?v=20261110-walk';
import {ConsoleLayout} from './console-layout.js';
import {ConsoleOverview} from './console-overview.js';
import {ConsoleDevices} from './console-devices.js?v=20261110-torque';
import {ConsoleRemote,ConsoleSensing} from './console-experience.js?v=20261110-walk';
import {ConsoleModels,ConsoleSettings} from './console-system.js';
import {PasswordSettings} from './console-account.js';
import {ConsoleLogs} from './console-logs.js';
import {useStartupFlow} from './console-startup.js?v=20261110-torque';
import {applyStage,loadStage} from './console-preferences.js';
import {normalizeRoute,serverRoute,routeInfo} from './console-routes.js';

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
    this.send({ t: 'hb', seq: ++this.seq, visible: document.visibilityState === 'visible', route: serverRoute(location.hash),
      pad_visible: padActivity.active || this.padCapture });
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


function useNow(ms = 250) {
  const [now, setNow] = useState(performance.now());
  useEffect(() => { const t = setInterval(() => setNow(performance.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}


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
      <button class="estop unavailable" onClick=${(e) => { e.currentTarget.blur(); location.hash = '#/devices/servos'; }}
        title="上次 Studio 退出时舵机会话开着、这次没接管上：先去舵机页接管总线，才能在总线上卸力">卸力 · 先去舵机页接管</button></span>`;
  }
  if (es.state === 'unavailable') {
    return html`<span class="estop-wrap">${note(err || es.why || fallback)}
      <button class="estop unavailable" onClick=${click}>卸力 · 不一定有效</button></span>`;
  }
  if (live && es.state === 'confirmed') {
    return html`<span class="estop-wrap">${note(err)}
      <button class="estop done" onClick=${click} title=${es.why}>全局卸力 · 已卸力</button></span>`;
  }
  // 数据旧了（!live）也给红色按钮：按了照样发，Studio 那边的卸力专线可能是好的
  return html`<span class="estop-wrap">${note(err || (!live ? '状态不明，仍可按' : ''))}
    <button class="estop" onClick=${click} title="让 15 颗舵机全部卸力。鸭子会软下来，扶住。">
      ${es.state === 'pending' ? '卸力中…' : '全局卸力'}<span class="kbd">空格</span></button></span>`;
}


function ServoBar({ st, onServos }) {
  const ss = st?.servo_session;
  if (ss) {
    if (onServos) return null;
    const unk = ss.unknown ? ` · ${ss.unknown} 颗读不到扭矩（可能也在保持）` : '';
    return html`<div class="banner warn servo-bar" role="status">
      <b>调舵机中 · ${ss.holding ?? '?'} 颗保持着${unk} · 主程序停着（手柄、遥控、站走都不能用）</b>
      <span class="sb-acts"><a href="#/devices/servos">回舵机页</a><${HandBackButton} link=${link} /></span>
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
      ${onServos ? null : html`<a href="#/devices/servos">去舵机页接管</a>`}
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


export const leaveGuards = {};
function useRoute() {
  const get = () => normalizeRoute(location.hash);
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

const PAD_HEAVY = ['skills','actions','combos','sounds'];
function Main({duck}) {
  const route=useRoute(), info=routeInfo(route);
  const [st,setSt]=useState(null),[sv,setSv]=useState(null),[imu,setImu]=useState(null),[tune,setTune]=useState(null),[pd,setPd]=useState(null);
  const [stAt,setStAt]=useState(0),[imuAt,setImuAt]=useState(0),[status,setStatus]=useState(link.status);
  const imuContext=useRef(null), now=useNow(250);
  useEffect(()=>{
    const off=link.on(m=>{
      if(m.t==='_status'){setStatus(m.status);if(m.status!=='open'){setImu(null);setImuAt(0);}}
      else if(m.t==='state'){
        const context=JSON.stringify([m.mode,m.build,m.imu?.source,m.imu?.configured_imu_profile,m.posture==='starting',m.imu?.context_current,m.imu?.generation]);
        if(imuContext.current!==null&&imuContext.current!==context){setImu(null);setImuAt(0);}
        imuContext.current=context;
        setSt(prev=>m.onboarding_same&&prev?{...m,onboarding:prev.onboarding}:m);setStAt(performance.now());
      } else if(m.t==='servo')setSv(m);
      else if(m.t==='imu'){setImu(m);setImuAt(performance.now());}
      else if(m.t==='tune')setTune(m);
      else if(m.t==='pad')setPd(prev=>{
        if(!m.lists_same||!prev?.motion)return m;
        const keep=Object.fromEntries(PAD_HEAVY.filter(key=>key in prev.motion).map(key=>[key,prev.motion[key]]));
        return {...m,motion:{...m.motion,...keep}};
      });
    });
    link.connect();return off;
  },[]);
  useEffect(()=>{window.scrollTo(0,0);},[route]);
  const age=st?(now-stAt)+(st.robotd_age_ms||0):Infinity;
  const live=status==='open'&&age<3000&&st?.robotd_ok!==false;
  const startup=useStartupFlow({st,sv,imu,tune,pad:pd,live,open:status==='open',link});
  if(status==='auth'){location.reload();return null;}
  let page=null;
  if(info.page==='devices')page=html`<${ConsoleDevices} section=${info.section} sv=${sv} st=${st} imu=${imu&&st?.imu?.diagnostic?{...imu,diagnostic:st.imu.diagnostic}:imu} link=${link} live=${live} age=${age} diagnosticElapsed=${now-(st?.imu?.diagnostic?stAt:imuAt)} pd=${pd} leaveGuards=${leaveGuards}/>`;
  else if(info.page==='remote')page=html`<${ConsoleRemote} section=${info.section} st=${st} pad=${pd} link=${link} api=${api} live=${live}/>`;
  else if(info.page==='sensing')page=html`<${ConsoleSensing} section=${info.section} st=${st} pad=${pd} link=${link} api=${api} live=${live} open=${status==='open'}/>`;
  else if(info.page==='models')page=html`<${ConsoleModels} section=${info.section} api=${api} csrf=${csrf} link=${link} live=${live} tune=${tune}/>`;
  else if(info.page==='settings')page=html`<${ConsoleSettings} section=${info.section} api=${api} csrf=${csrf} link=${link} st=${st} tune=${tune}/>`;
  else if(info.page==='logs')page=html`<${ConsoleLogs} api=${api} link=${link}/>`;
  document.title=`${info.title} · ${duck?.label||'智能鸭'}`;
  return html`<${ConsoleLayout} route=${route} duck=${duck} st=${st} live=${live} open=${status==='open'} emergency=${html`<${EStop} st=${st} live=${live} open=${status==='open'}/>`}>
    <${ServoBar} st=${st} onServos=${route==='/devices/servos'||route==='/overview'}/>
    <div hidden=${info.page!=='overview'}><${ConsoleOverview} st=${st} pad=${pd} tune=${tune} live=${live} guide=${startup.card}/></div>
    ${startup.context(route)}${page}${startup.dialogs}
  <//>`;
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
  if (noPw) return html`<section class="login" aria-label="首次设置登录密码" style="max-width:480px;margin:8vh auto 0;padding:24px">
    <h1>Microduck Studio</h1>
    <p style="margin:0;color:var(--text-2)">首次使用，请先设置网页登录密码，再进入中控。</p>
    <${PasswordSettings} hasPassword=${false} api=${api} onSaved=${load} />
  </section>`;
  return html`<${Main} duck=${sess.duck} />`;
}

render(html`<${App} />`, document.getElementById('app'));
