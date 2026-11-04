// 更多：语音方式切换、能看到但这一版手机页没重做的能力（跳到桌面版对应页）、以及连接信息。
// 桌面版的那几页本来就是响应式的（#/tof、#/servos、#/imu），手机上能用，所以这一版先给入口。
// 「模型」已经是手机壳里的整页（内嵌桌面版组件），不在这里重复列。
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { link } from './link.js';

const SRC_KEY = 'duck.voice.source';

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

  const secure = window.isSecureContext !== false;
  const version = hello?.version || '—';
  const duck = hello?.duck || {};

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
      现在是 http 打开，浏览器不给麦克风。要手机收音，得让 Studio 用 https 起（见 README「手机中控」一节）。
      在那之前用板载麦克风或打字。
    </div>` : null}

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
