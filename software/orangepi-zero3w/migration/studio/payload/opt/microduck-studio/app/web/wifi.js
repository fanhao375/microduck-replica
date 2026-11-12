// 总览的「连 WiFi」卡片、设置里的 WiFi 一栏（任务 10b）。
// 鸭子开着热点时，手机连热点、打开 Studio，选家里的 WiFi、填密码；Studio 先关热点，再让 configd net.connect。
// 热点开关、net.* 都在后端（studio/net.py）；这里只管选网、确认、把「断开以后还能看」的话留在屏幕上。
// 换网以后的地址按设备写（openLines），「更多」页还有一块能复制的「这只鸭子的地址」（DuckAddresses）。
// 连接发出去、页面跟鸭子断了以后自己去找鸭子的新地址（任务 17，用户 10-21：「连 WiFi 弄了三下」）：每 2 秒用 new Image()
// 加载 duck-xxxx / duck-xxxx.local 上的 probe.svg（跨域加载图片不要 CORS），哪个先加载成功就跳过去；最多找 3 分钟。
// 探测逻辑（findUrls / probeImage / startFind）不碰页面，tests/wifi_find.mjs 在 Node 里用假时钟跑它。
import { html, useState, useEffect } from './vendor/preact-htm.module.js';
import { ConfirmBox } from './confirm.js';

const CSS = `
.wifi-card { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.wifi-card h2 { margin: 0; font-size: 17px; font-weight: 600; }
.wifi-settings { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.wifi-settings h2 { margin: 0; font-size: 15px; font-weight: 600; }
.wifi-lead { margin: 0; font-size: 13px; line-height: 1.6; color: var(--text-2); }
.wifi-last-try { padding: 9px 12px; border-radius: 10px; background: #FFF1F1; box-shadow: inset 0 0 0 1px rgba(215, 0, 21, .18);
  color: var(--danger); font-size: 14px; line-height: 1.55; }
.wifi-list { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.wifi-net, .wifi-manual { border-radius: 12px; background: #F7F7FA; min-width: 0; overflow: hidden; }
.wifi-net.on { background: #EEF5FF; box-shadow: inset 0 0 0 1px rgba(0, 113, 227, .22); }
.wifi-net-head, .wifi-manual-head { width: 100%; min-height: 48px; padding: 8px 12px; border: 0; background: transparent;
  display: flex; align-items: center; gap: 10px; text-align: left; font-size: 16px; color: var(--text); }
.wifi-manual-head { color: var(--text-2); font-size: 14px; }
.wifi-name { flex: 1; min-width: 0; overflow-wrap: anywhere; line-height: 1.3; }
.wifi-name b { font-weight: 600; }
.wifi-sub { display: block; margin-top: 2px; font-size: 12px; color: var(--text-2); font-weight: 400; }
.wifi-badge { flex: none; font-size: 11px; padding: 1px 7px; border-radius: 999px; background: #E3F6EA; color: #1E7B3A; }
.wifi-lock { flex: none; font-size: 13px; opacity: .75; }
.wifi-bars { flex: none; width: 22px; height: 16px; display: inline-flex; align-items: flex-end; gap: 2px; }
.wifi-bars i { flex: 1; border-radius: 1px; background: #D5D5DB; height: 4px; }
.wifi-bars i:nth-child(2) { height: 7px; }
.wifi-bars i:nth-child(3) { height: 11px; }
.wifi-bars i:nth-child(4) { height: 15px; }
.wifi-bars i.on { background: var(--blue); }
.wifi-row-body { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 8px; }
.wifi-psk-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wifi-psk, .wifi-manual-name, .wifi-manual-psk { width: 100%; min-width: 0; height: 44px; padding: 0 12px;
  border: 1px solid #D2D2D7; border-radius: 10px; background: #FFF; color: var(--text); font: inherit; font-size: 16px; }
.wifi-show { display: inline-flex; align-items: center; gap: 6px; font-size: 14px; color: var(--text-2); }
.wifi-btn { min-height: 40px; padding: 0 16px; border: 0; border-radius: 999px; background: #E5E5EA;
  color: var(--text); font: inherit; font-size: 14px; }
.wifi-btn.primary { background: var(--blue); color: #FFF; font-weight: 600; }
.wifi-btn:disabled { opacity: .45; }
.wifi-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wifi-note { font-size: 13px; color: var(--text-2); line-height: 1.5; }
.wifi-msg { font-size: 13px; line-height: 1.5; color: var(--danger); }
.wifi-switching { padding: 12px 14px; border-radius: 12px; background: #FFF7EC; box-shadow: inset 0 0 0 1px #F5DDBF;
  font-size: 14px; line-height: 1.6; }
.wifi-switching b { display: block; margin-bottom: 4px; font-size: 15px; }
.wifi-switching p { margin: 2px 0 0; }
.wifi-now { display: flex; flex-direction: column; gap: 4px; font-size: 14px; line-height: 1.55; }
.wifi-now .big { font-size: 16px; font-weight: 600; }
.wifi-saved-row { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 4px 0; border-top: 1px solid var(--line); }
.wifi-saved-row .wifi-name { font-size: 15px; }
.wifi-flow { margin-top: 4px; padding-top: 12px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
.wifi-open { margin: 4px 0 0; padding-left: 18px; font-size: 13px; line-height: 1.6; color: var(--text-2); }
.wifi-open li { overflow-wrap: anywhere; }
.wifi-finding .wifi-find-urls { font-size: 14px; color: var(--text); }
.wifi-finding .wifi-find-urls a { font-weight: 600; }
.duck-addr-row { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 4px 0; border-top: 1px solid var(--line); }
.duck-addr-row .wifi-name { font-size: 15px; }
.duck-addr-row .wifi-name a { overflow-wrap: anywhere; }
@media (max-width: 700px) {
  .wifi-card, .wifi-settings { padding: 14px 14px; }
  .wifi-net-head { min-height: 52px; padding: 10px 12px; }
  .wifi-btn { min-height: 44px; }
  .wifi-psk, .wifi-manual-name, .wifi-manual-psk { height: 46px; }
}
`;
if (typeof document !== 'undefined' && !document.getElementById('wifi-css')) {   // Node 里跑探测逻辑的测试时没有 document
  const s = document.createElement('style');
  s.id = 'wifi-css';
  s.textContent = CSS;
  document.head.appendChild(s);
}

const SEC = { open: '开放', wep: 'WEP', wpa_psk: 'WPA2', wpa3_sae: 'WPA3', enterprise: '企业认证' };
const secLabel = (sec) => SEC[sec] || (sec ? '加密' : '');
const pskFormatOk = (v) => v === '' || (v.length >= 8 && v.length <= 63) || /^[0-9a-fA-F]{64}$/.test(v);
const pskOk = (v) => v !== '' && pskFormatOk(v);

function Bars({ signal }) {
  const n = typeof signal === 'number' ? (signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1) : 0;
  return html`<span class="wifi-bars" title=${typeof signal === 'number' ? '信号 ' + signal : '信号不知道'}
    aria-label=${typeof signal === 'number' ? '信号 ' + signal : '信号不知道'}>
    ${[1, 2, 3, 4].map((i) => html`<i class=${i <= n ? 'on' : ''}></i>`)}</span>`;
}

function minutesSince(at) {
  if (typeof at !== 'number') return null;
  return Math.max(1, Math.round((Date.now() / 1000 - at) / 60));
}

// 换了网以后怎么打开 Studio，按设备写（10-20 真机：开着 Clash 系统代理的 Windows 打不开 duck-xxxx.local——走了代理、502；
// 不带点的 duck-xxxx Windows 用 LLMNR 问得到，代理按 <local> 绕过；IP 哪儿都能开）
export function openLines(net) {
  const host = net.hostname || 'duck-xxxx';
  return [
    `Windows 电脑：${net.url_host || `http://${host}:8000/`}（开着代理也能打开）`,
    `苹果手机、Mac：${net.url_local || `http://${host}.local:8000/`}`,
    `安卓手机，或者上面都打不开：去路由器的设备列表找 ${host}，用它的 IP 打开`,
    '电脑开着 Clash 的 TUN 模式还打不开：在 Clash 里把局域网（192.168.0.0/16）设成直连',
  ];
}

// ---------------- 连 WiFi 以后找鸭子的新地址（任务 17） ----------------
export const FIND_EVERY_MS = 2000;           // 每 2 秒探一轮
export const FIND_GIVEUP_MS = 3 * 60 * 1000; // 最多找 3 分钟
export const FIND_PROBE_MS = 6000;           // 一次探测最多等这么久（连不上的地址可能挂几十秒才报错）
export const FIND_SILENT_MS = 5000;          // 这么久没收到 Studio 的任何消息也算断了（WebSocket 有时不马上报断）

/** 要找的地址：http://<hostname>:<端口>/ 和 http://<hostname>.local:<端口>/（net.json 的 hostname，端口跟 --listen 一致）。 */
export function findUrls(net) {
  const host = (net && net.hostname) || 'duck-xxxx';
  const urls = [(net && net.url_host) || `http://${host}:8000/`, (net && net.url_local) || `http://${host}.local:8000/`];
  return urls.filter((u, i) => urls.indexOf(u) === i);
}

/** 用一张小图探一个地址：加载成功回 true，出错 / 超时回 false。跨域加载图片不要 CORS；?t= 防缓存。 */
export function probeImage(url, timeoutMs = FIND_PROBE_MS) {
  return new Promise((resolve) => {
    const img = new Image();
    let done = false;
    const end = (ok) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      img.onload = null; img.onerror = null;
      resolve(ok);
    };
    const timer = setTimeout(() => end(false), timeoutMs);
    img.onload = () => end(true);
    img.onerror = () => end(false);
    img.src = `${url}probe.svg?t=${Date.now()}`;
  });
}

/** 每 every 毫秒把 urls 各探一次（上一次还没回话的那个先不探），哪个先成功就 onFound(那个地址)、停下；
 *  giveUp 毫秒还没找到就 onGiveUp()、停下。回 {stop}：页面重新连上原来的地址（热点回来了）时调它，停下以后什么都不再回调。 */
export function startFind({ urls, onFound, onGiveUp, probe = probeImage, every = FIND_EVERY_MS, giveUp = FIND_GIVEUP_MS,
  now = () => Date.now(), timers = { setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (t) => clearTimeout(t) } }) {
  const t0 = now();
  const inFlight = new Set();
  let stopped = false;
  let timer = null;
  const stop = () => {
    stopped = true;
    if (timer != null) timers.clearTimeout(timer);
    timer = null;
  };
  const tick = () => {
    timer = null;
    if (stopped) return;
    if (now() - t0 >= giveUp) { stop(); if (onGiveUp) onGiveUp(); return; }
    for (const url of urls) {
      if (inFlight.has(url)) continue;
      inFlight.add(url);
      Promise.resolve().then(() => probe(url)).then((ok) => {
        inFlight.delete(url);
        if (ok === true && !stopped) { stop(); if (onFound) onFound(url); }
      }, () => { inFlight.delete(url); });
    }
    timer = timers.setTimeout(tick, every);
  };
  tick();
  return { stop, get stopped() { return stopped; } };
}

// 确认框里的几行：热点卡片和设置里的「换一个 WiFi」只在第一句不同
function confirmLines(net, ssid, variant) {
  const back = variant === 'switch'
    ? '没连上：这个页面会断开；热点马上就回来，手机重新连鸭子热点就能看到原因。'
    : `没连上：热点马上就回来，手机重新连 ${net.hotspot && net.hotspot.ssid ? net.hotspot.ssid : '鸭子自己的热点'} 就能看到原因。`;
  const first = variant === 'switch'
    ? `鸭子会断开现在的 WiFi 去连「${ssid}」，这个页面会断开。`
    : `鸭子会关掉热点，去连「${ssid}」。你的手机会断开。`;
  return [first, '连上以后，手机（或电脑）连回家里的 WiFi，按设备打开：', ...openLines(net).map((l) => `· ${l}`), back];
}

// 复制一个地址。鸭子的局域网地址是 http，不算安全上下文，navigator.clipboard 用不了：退回老办法（选中一个看不见的输入框再 copy）
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* 退回下面 */ }
  try {
    const t = document.createElement('textarea');
    t.value = text;
    t.setAttribute('readonly', '');
    t.style.position = 'fixed';
    t.style.opacity = '0';
    document.body.appendChild(t);
    t.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(t);
    return ok;
  } catch { return false; }
}

function LastTry({ net }) {
  const lt = net.last_try;
  if (!lt || lt.ok !== false) return null;
  const why = lt.reason_text || '没连上';
  const ssid = net.hotspot && net.hotspot.ssid ? net.hotspot.ssid : '鸭子热点';
  return html`<div class="wifi-last-try" role="alert">上次连「${lt.ssid || '这个 WiFi'}」没成功：${why}
    ${net.mode === 'hotspot' ? html`<div>热点马上就回来，手机重新连 ${ssid} 就能看到原因。</div>` : null}</div>`;
}

function Switching({ net, ssid, variant }) {
  return html`<div class="wifi-switching" role="status">
    <b>正在切换……手机马上会断开</b>
    ${confirmLines(net, ssid || '这个 WiFi', variant).map((line) => html`<p>${line}</p>`)}
  </div>`;
}

// 「断了」= 跟 Studio 的连接不在，或者 5 秒没收到任何消息（Main 每 250 ms 重画一次，WifiFlow 跟着重算）
const linkGone = (link) => link.status !== 'open' || (link.lastAt > 0 && performance.now() - link.lastAt > FIND_SILENT_MS);

// 页面跟鸭子断了以后：在找鸭子的新地址（每 2 秒探一次，找到就跳过去）；3 分钟没找到就换回原来那段说明
function Finding({ net, find, variant }) {
  const ssid = find.ssid || '这个 WiFi';
  if (find.phase === 'timeout') {
    return html`<div class="wifi-switching wifi-finding" role="status" data-phase="timeout">
      <b>找了 3 分钟还没找到鸭子：自己打开吧</b>
      ${confirmLines(net, ssid, variant).slice(1).map((line) => html`<p>${line}</p>`)}
    </div>`;
  }
  return html`<div class="wifi-switching wifi-finding" role="status" data-phase="finding">
    <b>鸭子正在连「${ssid}」……</b>
    <p>请把这台设备连回「${ssid}」（电脑插着网线的不用管），找到鸭子会自动打开。</p>
    <ul class="wifi-open wifi-find-urls">${find.urls.map((u) => html`<li><a href=${u}>${u}</a>${/\.local[:/]/.test(u) ? '（苹果手机、Mac）' : '（Windows 电脑）'}</li>`)}</ul>
    <p>找不到时：去路由器设备列表找 ${net.hostname || 'duck-xxxx'} 的 IP。</p>
  </div>`;
}

export function WifiFlow({ net, link, variant = 'card' }) {
  const [scan, setScan] = useState({ networks: [], loading: true, error: null, cached: false, scan_at: null });
  const [sel, setSel] = useState(null);
  const [psk, setPsk] = useState('');
  const [show, setShow] = useState(false);
  const [manual, setManual] = useState(false);
  const [manualName, setManualName] = useState('');
  const [manualPsk, setManualPsk] = useState('');
  const [manualShow, setManualShow] = useState(false);
  const [confirm, setConfirm] = useState(null);        // {ssid, psk}
  const [trying, setTrying] = useState(null);
  const [switching, setSwitching] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [msg, setMsg] = useState(null);
  // 连接发出去以后要找的鸭子新地址：{ssid, urls, phase}。phase：wait（还连着）→ finding（断了，在探）→ timeout（3 分钟没找到）
  const [find, setFind] = useState(null);
  const [linkUp, setLinkUp] = useState(link.status === 'open');
  useEffect(() => link.on((m) => { if (m.t === '_status') setLinkUp(m.status === 'open'); }), []);
  const gone = !linkUp || linkGone(link);

  const rescan = async () => {
    setScan((s) => ({ ...s, loading: true, error: null }));
    const r = await link.cmd('net.op', { op: 'scan' }, 25000);
    if (!r.ok) {
      setScan({ networks: [], loading: false, error: r.reason || r.error || '读不到 WiFi 列表', cached: false, scan_at: null });
      return;
    }
    const d = r.data || {};
    setScan({ networks: Array.isArray(d.networks) ? d.networks : [], loading: false, error: null,
      cached: !!d.cached, scan_at: d.scan_at || null });
  };
  useEffect(() => {
    let alive = true;
    (async () => {
      await link.whenOpen();
      if (alive) await rescan();
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!switching && !find) return;
    const lt = net.last_try;
    // 连着的时候这一次有了结果（或者早就不忙了）：不用再找
    if ((lt && typeof lt.at === 'number' && startedAt && lt.at * 1000 >= startedAt - 2000)
      || (!net.busy && startedAt && Date.now() - startedAt > 12000)) { setSwitching(false); setFind(null); }
  }, [net.busy, net.last_try ? net.last_try.at : null]);

  // 断了：开始找鸭子。断过又连上了原来的地址（热点回来了，说明没连上）：停止探测、退回「等结果」，
  // 上面那个 effect 一收到这一次的 last_try 就收起，失败原因照旧由 LastTry 显示（只是手机睡了几秒、其实没断，也不会把找鸭子弄丢）
  useEffect(() => {
    if (!find) return;
    if (gone && find.phase === 'wait') setFind({ ...find, phase: 'finding' });
    else if (!gone && find.phase !== 'wait') setFind({ ...find, phase: 'wait' });
  }, [gone, find]);
  useEffect(() => {
    if (!find || find.phase !== 'finding') return undefined;
    const f = startFind({
      urls: find.urls,
      onFound: (url) => { if (linkGone(link)) location.href = url; },     // 原来的地址刚好又连上了：不跳，交给上面停下
      onGiveUp: () => setFind((x) => (x ? { ...x, phase: 'timeout' } : x)),
    });
    return () => f.stop();
  }, [find ? find.phase : null]);

  // 确认框在卡片最下面：手机上点了「连接」看不到它，弹出时滚过去
  useEffect(() => {
    if (!confirm) return;
    const e = document.querySelector('.wifi-confirm-wrap');
    if (e && e.scrollIntoView) e.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [confirm]);

  const pick = (n) => {
    if (sel === n.ssid) { setSel(null); setPsk(''); return; }
    setSel(n.ssid); setPsk(''); setShow(false); setMsg(null);
  };
  const ask = (ssid, pass, locked = false) => {
    if (!ssid) { setMsg('先填 WiFi 名字'); return; }
    if (locked && !pass) { setMsg('这个 WiFi 加密了，要填密码'); return; }
    if (!pskFormatOk(pass)) { setMsg('WiFi 密码要 8–63 个字符，或者 64 位十六进制'); return; }
    setMsg(null);
    setConfirm({ ssid, psk: pass });
  };
  const start = async () => {
    if (!confirm) return;
    const { ssid, psk: pass } = confirm;
    setConfirm(null); setTrying(ssid); setStartedAt(Date.now()); setSwitching(true); setMsg(null);
    const wasOpen = link.status === 'open';
    setFind({ ssid, urls: findUrls(net), phase: 'wait' });
    const r = await link.cmd('net.op', { op: 'connect', ssid, psk: pass }, 20000);
    // Studio 明说不行、本来就没连着、或者连着却没回话：没发出去。回执路上连接断了：多半已经开始切换，接着找鸭子
    if (!r.ok && (r.reason || !wasOpen || link.status === 'open')) {
      setSwitching(false); setFind(null); setMsg(r.reason || r.error || '没发出去');
    }
  };

  const nets = scan.networks || [];
  const ago = scan.cached ? minutesSince(scan.scan_at) : null;
  const busy = switching || net.busy === 'connecting' || (!!find && find.phase !== 'wait');
  return html`<div class="wifi-flow">
    ${find && find.phase !== 'wait' ? html`<${Finding} net=${net} find=${find} variant=${variant} />`
      : busy ? html`<${Switching} net=${net} ssid=${trying || (net.last_try && net.last_try.ssid)} variant=${variant} />` : null}
    ${scan.error ? html`<div class="wifi-msg">${scan.error}</div>` : null}
    <div class="wifi-list">
      ${nets.map((n) => {
        const on = sel === n.ssid;
        const open = n.security === 'open';
        const needs = !open;
        const can = open || pskOk(psk);
        return html`<div class=${'wifi-net' + (on ? ' on' : '')} data-ssid=${n.ssid}>
          <button class="wifi-net-head" type="button" aria-expanded=${on ? 'true' : 'false'} onClick=${() => pick(n)}>
            <${Bars} signal=${n.signal} />
            <span class="wifi-name"><b>${n.ssid}</b><span class="wifi-sub">${secLabel(n.security)}${n.saved ? ' · 已保存' : ''}</span></span>
            ${n.saved ? html`<span class="wifi-badge">已保存</span>` : null}
            ${open ? null : html`<span class="wifi-lock" title="${secLabel(n.security)}">🔒</span>`}
          </button>
          ${on ? html`<div class="wifi-row-body">
            ${needs ? html`<div class="wifi-psk-row">
              <input class="wifi-psk" type=${show ? 'text' : 'password'} value=${psk} autocomplete="new-password"
                placeholder="家里 WiFi 的密码" onInput=${(e) => setPsk(e.target.value)} />
              <label class="wifi-show"><input type="checkbox" checked=${show} onChange=${(e) => setShow(e.target.checked)} /> 显示密码</label>
            </div>${n.saved ? html`<div class="wifi-note">已保存；鸭子每次连都要密码（官方配网程序的规矩）。</div>` : null}`
              : html`<div class="wifi-note">这是开放网络，不用密码。</div>`}
            <div class="wifi-actions"><button class="wifi-btn primary wifi-connect" type="button" disabled=${!can || busy}
              onClick=${() => ask(n.ssid, needs ? psk : '', needs)}>连接</button></div>
          </div>` : null}
        </div>`;
      })}
    </div>
    <div class="wifi-manual">
      <button class="wifi-manual-head" type="button" aria-expanded=${manual ? 'true' : 'false'} onClick=${() => setManual(!manual)}>
        ${manual ? '收起' : '其他网络（手动输入名字）'}
      </button>
      ${manual ? html`<div class="wifi-row-body">
        <input class="wifi-manual-name" value=${manualName} placeholder="WiFi 名字" onInput=${(e) => setManualName(e.target.value)} />
        <input class="wifi-manual-psk" type=${manualShow ? 'text' : 'password'} value=${manualPsk}
          placeholder="密码（开放网络留空）" autocomplete="new-password" onInput=${(e) => setManualPsk(e.target.value)} />
        <label class="wifi-show"><input type="checkbox" checked=${manualShow} onChange=${(e) => setManualShow(e.target.checked)} /> 显示密码</label>
        <div class="wifi-actions"><button class="wifi-btn primary wifi-manual-connect" type="button" disabled=${busy || !manualName}
          onClick=${() => ask(manualName, manualPsk)}>连接</button></div>
      </div>` : null}
    </div>
    <div class="wifi-actions">
      <button class="wifi-btn wifi-rescan" type="button" disabled=${scan.loading || busy} onClick=${rescan}>
        ${scan.loading ? '正在扫描…' : '重新扫描'}</button>
      ${ago ? html`<span class="wifi-note">这是 ${ago} 分钟前扫的</span>` : null}
    </div>
    ${msg ? html`<div class="wifi-msg">${msg}</div>` : null}
    ${confirm ? html`<div class="wifi-confirm-wrap"><${ConfirmBox} cls="wifi-confirm" lines=${confirmLines(net, confirm.ssid, variant)}
      confirmLabel=${'断开去连「' + confirm.ssid + '」'} onCancel=${() => setConfirm(null)} onConfirm=${start} /></div>` : null}
  </div>`;
}

export function WifiCard({ net, link, secure }) {
  if (!net || !net.known || net.mode !== 'hotspot') return null;
  return html`<section class="board wifi-card" aria-label="连 WiFi">
    <h2>连 WiFi</h2>
    <p class="wifi-lead">现在是鸭子自己的热点（${net.hotspot && net.hotspot.ssid ? net.hotspot.ssid : 'Microduck'}）。
      选家里的 WiFi、填密码，鸭子就连过去；连上以后手机（或电脑）连回家里的 WiFi，按设备打开：</p>
    <ul class="wifi-open">${openLines(net).map((l) => html`<li>${l}</li>`)}</ul>
    <${LastTry} net=${net} />
    <${WifiFlow} net=${net} link=${link} />
    ${secure === false ? html`<div class="wifi-note" role="note">热点密码大家都知道，附近的人也能连上控制鸭子；在外面演示前，去「更多」设个登录密码</div>` : null}
  </section>`;
}

export function WifiSettings({ net, link }) {
  const [flow, setFlow] = useState(false);
  const [scan, setScan] = useState(null);
  const [forget, setForget] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = async () => {
    const r = await link.cmd('net.op', { op: 'scan' }, 25000);
    setScan(r.ok ? (r.data || { networks: [] }) : { networks: [], error: r.reason || r.error });
  };
  useEffect(() => {
    if (!net || !net.known) return undefined;
    let alive = true;
    (async () => { await link.whenOpen(); if (alive) await load(); })();
    return () => { alive = false; };
  }, [net && net.known]);
  if (!net || !net.known) return null;
  const saved = (scan && scan.networks ? scan.networks : []).filter((n) => n.saved);
  const doForget = async (ssid) => {
    setForget(null); setMsg(null);
    const r = await link.cmd('net.op', { op: 'forget', ssid }, 15000);
    if (!r.ok) { setMsg(r.reason || r.error || '没忘掉'); return; }
    setMsg(r.data && r.data.removed === false ? `鸭子本来就没记着「${ssid}」` : `忘掉了「${ssid}」`);
    load();
  };
  const current = net.mode === 'hotspot' ? '鸭子自己的热点' : (net.ssid || '没连着 WiFi');
  return html`<div class="board wifi-settings" aria-label="WiFi 设置">
    <h2>WiFi</h2>
    <${LastTry} net=${net} />
    <div class="wifi-now">
      <div class="big">现在：${current}</div>
      ${net.mode === 'hotspot' ? html`<div>热点 ${net.hotspot && net.hotspot.ssid ? net.hotspot.ssid : '—'} · 密码 ${net.hotspot && net.hotspot.psk ? net.hotspot.psk : '—'}</div>`
        : net.mode === 'wifi' ? html`<div class="wifi-actions"><${Bars} signal=${net.signal} /><span>信号 ${net.signal ?? '—'}</span><span>IP ${net.ip4 || '—'}</span></div>`
          : html`<div>现在没连着 WiFi。</div>`}
      <div class="wifi-note">打开 Studio 的地址见上面「这只鸭子的地址」（按设备挑一个，能复制）。</div>
    </div>
    <div class="wifi-actions"><button class="wifi-btn primary" type="button" onClick=${() => setFlow(!flow)}>
      ${flow ? '收起' : '换一个 WiFi'}</button></div>
    ${flow ? html`<div class="wifi-flow"><${WifiFlow} net=${net} link=${link} variant="switch" /></div>` : null}
    <div>
      <div class="wifi-note">已保存的网络</div>
      ${scan && scan.error ? html`<div class="wifi-msg">${scan.error}</div>` : null}
      ${saved.length ? saved.map((n) => html`<div class="wifi-saved-row">
        <span class="wifi-name">${n.ssid}</span>
        ${n.ssid === net.ssid ? html`<span class="wifi-badge">正在用</span>` : null}
        <button class="wifi-btn wifi-forget" type="button" data-ssid=${n.ssid}
          onClick=${() => n.ssid === net.ssid ? setForget(n.ssid) : doForget(n.ssid)}>忘掉</button>
      </div>`) : html`<div class="wifi-note">${scan ? '还没有已保存的 WiFi。' : '读取中…'}</div>`}
    </div>
    ${msg ? html`<div class="wifi-note">${msg}</div>` : null}
    ${forget ? html`<div class="wifi-confirm-wrap"><${ConfirmBox} cls="wifi-confirm"
      lines=${[`忘掉「${forget}」以后鸭子会断开；没有别的 WiFi 可连的话，1 分钟后开热点。`]}
      confirmLabel="忘掉" onCancel=${() => setForget(null)} onConfirm=${() => doForget(forget)} /></div>` : null}
  </div>`;
}

// 「更多」页的「这只鸭子的地址」（10-20 真机验收：开着 Clash 的电脑打不开 .local）：后端 net.urls 列出现在能用的
// IP（configd net.status 的 ip4；热点模式是 10.42.0.1）、duck-xxxx、duck-xxxx.local，每个一个复制按钮
export function DuckAddresses({ net }) {
  const [copied, setCopied] = useState(null);       // {url, ok}
  useEffect(() => {
    if (!copied) return undefined;
    const t = setTimeout(() => setCopied(null), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  if (!net || !net.known) return null;
  const urls = net.urls || [];
  const copy = async (url) => setCopied({ url, ok: await copyText(url) });
  return html`<div class="board wifi-settings duck-addr" aria-label="这只鸭子的地址">
    <h2>这只鸭子的地址</h2>
    <p class="wifi-lead">${net.mode === 'hotspot' ? '现在是鸭子自己的热点：连着热点的手机用 IP 那一个。'
      : '跟鸭子在同一个 WiFi 里，按设备挑一个。'}</p>
    ${urls.map((u) => html`<div class="duck-addr-row" data-kind=${u.kind}>
      <span class="wifi-name"><a href=${u.url}>${u.url}</a><span class="wifi-sub">${u.note}</span></span>
      <button class="wifi-btn duck-addr-copy" type="button" onClick=${() => copy(u.url)}>
        ${copied && copied.url === u.url ? (copied.ok ? '已复制' : '复制不了，长按地址复制') : '复制'}</button>
    </div>`)}
    ${urls.some((u) => u.kind === 'ip') ? null
      : html`<div class="wifi-note">现在不知道 IP（没连上 WiFi，或者读不到配网程序）：去路由器的设备列表找 ${net.hostname || 'duck-xxxx'}。</div>`}
    <div class="wifi-note">安卓手机打不开带名字的地址就用 IP；电脑开着 Clash 的 TUN 模式还打不开：在 Clash 里把局域网（192.168.0.0/16）设成直连。</div>
  </div>`;
}
