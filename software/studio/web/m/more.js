// 更多：语音方式切换、能看到但这一版手机页没重做的能力（跳到桌面版对应页）、以及连接信息。
// 桌面版的那几页本来就是响应式的（#/tof、#/servos、#/imu），手机上能用，所以这一版先给入口。
// 「模型」已经是手机壳里的整页（内嵌桌面版组件），不在这里重复列。
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { link } from './link.js';

const SRC_KEY = 'duck.voice.source';
const HOT_KEY = 'duck.hotspot';

// 切热点会把这台手机的连接断掉，可能收不到后端的状态和回执；先把热点信息留在本机。
function loadHotspot() {
  try { return JSON.parse(localStorage.getItem(HOT_KEY) || 'null') || null; } catch { return null; }
}

function saveHotspot(h) {
  try { localStorage.setItem(HOT_KEY, JSON.stringify(h)); } catch { /* 隐私模式 */ }
}

const PAGES = [
  { href: '#/more/camera', label: '摄像头', desc: '手机版实时画面' },
  { href: '/#/tof', label: 'ToF 测距', desc: '8×8 距离网格' },
  { href: '/#/servos', label: '舵机', desc: '15 颗的位置和温度' },
  { href: '/#/imu', label: 'IMU', desc: '姿态与倾斜' },
  { href: '/#/logs', label: '日志', desc: 'Studio 最近干了什么' },
  { href: '/#/settings', label: '设置', desc: '密码、WiFi、软件更新、AI' },
];

const SOURCES = [
  { id: 'auto', label: '自动', desc: '手机能收音就用手机，否则用板载' },
  { id: 'phone', label: '手机麦克风', desc: '需要 https 打开本页' },
  { id: 'board', label: '板载麦克风', desc: '鸭子自己听，离线识别' },
];

export function MorePage({ hello, st, conn, say, nick, rename }) {
  const [src, setSrc] = useState('auto');
  const [draft, setDraft] = useState('');
  useEffect(() => { try { setSrc(localStorage.getItem(SRC_KEY) || 'auto'); } catch { /* 隐私模式 */ } }, []);
  const pick = (id) => {
    setSrc(id);
    try { localStorage.setItem(SRC_KEY, id); } catch { /* 隐私模式 */ }
    say(`语音方式已切到「${SOURCES.find((s) => s.id === id).label}」`);
  };
  const saveName = (e) => {
    e.preventDefault();
    rename(draft);
    setDraft('');
    say(draft.trim() ? `以后就叫它「${draft.trim()}」` : '名字恢复成默认的「鸭鸭」');
  };

  // 户外：热点信息跟状态同步，同时留在本机（切热点会把连接断掉）
  const net = st?.net;
  const hs = net?.hotspot;
  const [hot, setHot] = useState(loadHotspot);
  const [outdoorAsk, setOutdoorAsk] = useState(false);
  const [netBusy, setNetBusy] = useState(false);
  const [netNote, setNetNote] = useState(null);
  useEffect(() => {
    if (hs && hs.ssid) {
      const clean = { ssid: hs.ssid, psk: hs.psk || 'microduck', ip: hs.ip || '10.42.0.1',
                      url: net?.url_hotspot || '' };
      setHot(clean);
      saveHotspot(clean);
    }
  }, [hs?.ssid, hs?.psk, hs?.ip, net?.url_hotspot]);

  const netBusyNow = netBusy || !!net?.busy;
  const netOp = async (op) => {
    if (netBusyNow) return;
    setNetBusy(true);
    setNetNote(null);
    const r = await link.cmd('net.op', { op }, 45000);
    setNetBusy(false);
    if (r.ok) {
      if (op === 'hotspot-up') setOutdoorAsk(false);
      setNetNote(r.reason || '已经在处理');
      return;
    }
    const dropped = r.error === '和鸭子的连接断了' || r.error === '鸭子没回话（超时）';
    if (dropped) {
      if (op === 'hotspot-up') setOutdoorAsk(false);
      setNetNote(op === 'hotspot-up'
        ? '连接已经断开，热点应该正在开。按下面三步连上去。'
        : '连接断了，热点应该正在关。稍后手机重新连回家里的 WiFi。');
      return;
    }
    setNetNote(r.reason || r.error || '没发出去');
  };

  const secure = window.isSecureContext !== false;
  const version = hello?.version || '—';
  const duck = hello?.duck || {};
  const hotspot = hs || hot;
  const hotspotSsid = hotspot?.ssid || 'Microduck-xxxx';
  const hotspotPsk = hotspot?.psk || 'microduck';
  const hotspotUrl = net?.url_hotspot || hotspot?.url
    || `${location.protocol}//${hotspot?.ip || '10.42.0.1'}:8000/m`;
  const atHotspot = net?.mode === 'hotspot';

  return html`<div>
    <h2 class="m-h2">给它起个名字</h2>
    <div class="m-card m-pad">
      <div class="m-sub">现在叫「${nick}」。名字存在这台手机上，换个手机要重新起。</div>
      <form class="m-type" onSubmit=${saveName}>
        <input placeholder=${`新名字，留空恢复「鸭鸭」`} value=${draft} maxlength="12"
          onInput=${(e) => setDraft(e.currentTarget.value)} />
        <button class="m-btn primary" type="submit">保存</button>
      </form>
    </div>

    <h2 class="m-h2">语音方式</h2>
    <div class="m-card">
      <ul class="m-list">
        ${SOURCES.map((s) => html`<li>
          <button class="row" style="width:100%;text-align:left" onClick=${() => pick(s.id)}>
            <span style="flex:1">
              <span style="display:block">${s.label}</span>
              <span style="display:block;font-size:12px;color:var(--text-3)">${s.desc}</span>
            </span>
            <span class="chev">${src === s.id ? '✓' : ''}</span>
          </button>
        </li>`)}
      </ul>
    </div>
    ${!secure ? html`<div class="m-banner">
      现在是 http 打开，浏览器不给麦克风。先按下面「手机麦克风证书」装好证书，再用 https 地址打开本页；
      在那之前用板载麦克风或打字。
    </div>` : null}

    <h2 class="m-h2">手机麦克风证书</h2>
    <div class="m-card m-pad">
      <div class="m-sub">第一次用「手机说话」前，让手机信任鸭子自己的 https 证书。只影响手机麦克风；板载麦克风和实体手柄不需要。</div>
      <a class="m-btn" href="/ca.crt" download="Microduck-Local-CA.crt">下载证书</a>
      <ol class="m-tip" style="padding-left:18px;margin:10px 0 0">
        <li>iPhone：点下载 → 设置 → 已下载描述文件 → 安装；再到 设置 → 通用 → 关于本机 → 证书信任设置，打开 Microduck Local CA。</li>
        <li>Android：设置 → 安全 → 加密与凭据 → 安装证书 → CA 证书，选刚下载的文件。</li>
        <li>装完刷新本页，再试「手机说话」。</li>
      </ol>
    </div>

    ${net?.known ? html`
      <h2 class="m-h2">户外（没有路由器）</h2>
      <div class="m-card m-pad">
        <div class="m-sub">
          ${atHotspot ? '手机现在连的是鸭子自己的热点。'
            : net.mode === 'wifi' ? `鸭子现在连着家里的 WiFi${net.ssid ? `（${net.ssid}）` : ''}。`
            : '鸭子现在没有连 WiFi。'}
          没有路由器时，鸭子自己开热点：手机连它，「手机说话」走板载 Vosk，不需要互联网。实体手柄走蓝牙，跟 WiFi 无关。
        </div>
        <div class="m-kv"><span class="k">热点名</span><span class="v">${hotspotSsid}</span></div>
        <div class="m-kv"><span class="k">密码</span><span class="v">${hotspotPsk}</span></div>
        <div class="m-kv"><span class="k">手机打开</span><span class="v">${hotspotUrl}</span></div>
        ${atHotspot ? html`
          <div class="m-btns" style="margin-top:12px">
            <button class="m-btn" onClick=${() => netOp('hotspot-down')} disabled=${netBusyNow}>关热点，回家里 WiFi</button>
          </div>
          <p class="m-tip">关掉热点后，鸭子会自动连回保存过的家庭 WiFi；手机也要重新连回家里的 WiFi。</p>
        ` : outdoorAsk ? html`
          <div class="m-banner">
            <b>接下来手机会断线，按这三步重连：</b>
            <ol style="padding-left:18px;margin:8px 0 0">
              <li>打开手机 WiFi 列表，连「${hotspotSsid}」（密码 ${hotspotPsk}）</li>
              <li>提示「无互联网」时选「保持连接」，并临时关掉移动数据</li>
              <li>浏览器打开 ${hotspotUrl}</li>
            </ol>
          </div>
          <div class="m-btns" style="margin-top:12px">
            <button class="m-btn primary" onClick=${() => netOp('hotspot-up')} disabled=${netBusyNow}>确认，开热点</button>
            <button class="m-btn" onClick=${() => setOutdoorAsk(false)} disabled=${netBusyNow}>取消</button>
          </div>
        ` : html`
          <div class="m-btns" style="margin-top:12px">
            <button class="m-btn primary" onClick=${() => setOutdoorAsk(true)} disabled=${netBusyNow}>开热点（当前连接会断开）</button>
          </div>
        `}
        ${netNote ? html`<div class="m-banner" style="margin-top:12px">${netNote}</div>` : null}
        <p class="m-tip">户外演示前先在「设置 → 登录密码」设网页密码：热点密码是公开的默认值，不设密码同一热点下的人都能操作。</p>
      </div>
    ` : null}

    <h2 class="m-h2">这一版没重做的页</h2>
    <div class="m-card">
      <ul class="m-list">
        ${PAGES.map((p) => html`<li><a href=${p.href}>
          <span>${p.label}</span><span class="desc">${p.desc}</span><span class="chev">›</span>
        </a></li>`)}
      </ul>
    </div>
    <p class="m-tip">「摄像头」是手机版页面；其余是桌面版 Studio 的页面，手机上也能用，只是没按手机重新排过。</p>

    <h2 class="m-h2">这只鸭子</h2>
    <div class="m-card">
      <div class="m-kv"><span class="k">昵称</span><span class="v">${nick}</span></div>
      <div class="m-kv"><span class="k">设备名</span><span class="v">${duck.label || '—'}</span></div>
      <div class="m-kv"><span class="k">Studio 版本</span><span class="v">${version}</span></div>
      <div class="m-kv"><span class="k">连接</span><span class="v">${conn === 'open' ? '已连上' : conn === 'closed' ? '断了' : '连接中'}</span></div>
      <div class="m-kv"><span class="k">姿势</span><span class="v">${st?.posture || '—'}</span></div>
      <div class="m-kv"><span class="k">本页地址</span><span class="v">${location.host}</span></div>
    </div>
    <div class="m-btns" style="margin-top:12px">
      <button class="m-btn" onClick=${() => link.reconnect()}>重新连接</button>
      <a class="m-btn" href="/">打开桌面版</a>
    </div>

    <p class="m-tip" style="margin-top:22px">
      卸力在每一页的右上角；卸力之后舵机是松的，鸭子会趴下，点「站起来」才会重新上劲。
    </p>
  </div>`;
}
