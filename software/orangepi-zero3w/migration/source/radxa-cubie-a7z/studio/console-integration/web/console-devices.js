import { html, useState } from './vendor/preact-htm.module.js';
import { ServoPage } from './servo.js';
import { ImuPage, DiagnosticCard } from './imu.js';
import { startupStageCopy } from './startup-status.js';

if (typeof document !== 'undefined' && !document.getElementById('console-devices-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'console-devices-css'; sheet.rel = 'stylesheet';
  sheet.href = new URL('./console-devices.css', import.meta.url).href;
  document.head.appendChild(sheet);
}

const number = (value, suffix = '') => Number.isFinite(value) ? `${value}${suffix}` : '—';
const field = (label, value) => html`<div class="console-diagnostic-field"><span>${label}</span><strong>${value}</strong></div>`;

function TrackingAvailability({ link, open }) {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('尚未检查板端能力。');
  const [component, setComponent] = useState(null);
  const check = async () => {
    if (link.status !== 'open' || busy) return;
    setBusy(true); setMessage('正在读取跟踪能力…');
    try {
      const reply = await link.cmd('tracking.op', { op: 'status' }, 5000);
      if (!reply.ok) { setMessage(reply.reason || reply.error || '板端未提供跟踪状态'); return; }
      // Only load the original real tool when both the installed backend and asset respond.
      const module = await import('./leg-tracking.js');
      if (typeof module.LegTracking !== 'function') throw Error('板端已响应，但跟踪界面模块不完整');
      setComponent(() => module.LegTracking); setMessage('');
    } catch (error) { setMessage(error.message || '跟踪工具未安装或暂时不可用'); }
    finally { setBusy(false); }
  };
  if (component) return html`<${component} link=${link} open=${open} />`;
  return html`<section class="board console-tracking-capability"><div class="console-card-head"><h2>关节跟踪</h2><span>以板端能力为准</span></div>
    <div class="console-card-content"><p role="status">${message}</p><button class="btn-light" disabled=${busy || link.status !== 'open'} onClick=${check}>${busy ? '检查中…' : '检查跟踪能力'}</button>
      <details><summary>使用条件</summary><p>需要板端跟踪服务与对应界面模块。空载结果用于筛查，不能代替带载站立与行走验证。</p></details></div></section>`;
}

function ConsoleDiagnostics({ st, sv, imu, link, live, age, diagnosticElapsed }) {
  const maintenance = sv?.state === 'open', fresh = link.status === 'open' && (maintenance || live === true);
  const mainFresh = live === true, rows = sv?.rows || [], stage = startupStageCopy(st);
  const servos = st?.servos || {}, bus = sv?.bus || {};
  const read = (value, suffix = '') => fresh ? number(value, suffix) : '—';
  return html`<div class="console-diagnostics-grid"><div class="console-diagnostic-stack">
    <section class="board"><div class="console-card-head"><h2>通信与回读</h2><span>${fresh ? maintenance ? '维护总线' : '主程序' : '等待有效回读'}</span></div><div class="console-card-content">
      ${field('控制权', maintenance ? 'Studio 维护会话' : st?.posture === 'servo_session' ? '其他维护会话' : mainFresh ? '主程序' : '未知')}
      ${field('舵机在线', fresh ? `${(maintenance ? sv?.online : servos.online) ?? '—'} / ${(maintenance ? sv?.total : servos.total) ?? '—'}` : '—')}
      ${field('维护采集频率', maintenance ? read(sv?.hz, ' Hz') : '—')}${field('主程序读数年龄', mainFresh ? number(age, ' ms') : '—')}
      ${field('维护保持数', maintenance ? read(sv?.holding) : '—')}${field('维护丢帧率', maintenance && Number.isFinite(sv?.drop_rate) ? read(sv.drop_rate * 100, ' %') : '—')}
      <details><summary>总线与逐关节通信统计</summary><div class="console-stat-grid">${['tx','rx_ok','timeout','bad_checksum','bad_id'].map(key => field(key, maintenance ? read(bus[key]) : '—'))}</div>
        <div class="console-table-scroll"><table><thead><tr><th>关节</th><th>问 / 回 / 丢</th><th>连丢 / 最长</th><th>状态</th></tr></thead><tbody>${rows.map(row => html`<tr><td>#${row.id} ${row.cn}</td>
          <td>${['asked','ok','missed'].map(key => read(row.comm?.[key])).join(' / ')}</td><td>${['streak','max_streak'].map(key => read(row.comm?.[key])).join(' / ')}</td>
          <td>${fresh && row.fresh ? row.status ?? '—' : '—'}</td></tr>`)}</tbody></table></div></details>
    </div></section>
    <section class="board"><div class="console-card-head"><h2>主程序</h2></div><div class="console-card-content">
      ${field('运行模式', st?.mode || '—')}${field('当前阶段', st?.posture || '—')}${stage ? field('启动进度', stage.h) : null}
      ${stage?.sub ? html`<p>${stage.sub}</p>` : null}${st?.start_error ? html`<p role="alert">${st.start_error}</p>` : null}
      <a class="btn-light" href="#/logs">运行日志</a><a class="btn-light" href="/api/diag">下载诊断包</a>
    </div></section>
  </div><div class="console-diagnostic-stack"><${DiagnosticCard} st=${st} imu=${imu} live=${live} diagnosticElapsed=${diagnosticElapsed} />
    <section class="board"><div class="console-card-head"><h2>姿态与安装方向</h2></div><div class="console-card-content">
      ${field('安装方向', !mainFresh ? '等待主程序回读' : imu?.mount?.verified === true ? '已验证' : imu?.mount?.verified === false ? '未验证' : '未知')}
      ${field('倾角', mainFresh ? number(st?.tilt_deg, '°') : '—')}${field('关节读数年龄', mainFresh ? number(st?.joints_age_ms, ' ms') : '—')}
      ${field('重力向量', mainFresh && Array.isArray(st?.gravity) ? st.gravity.map(n => number(n)).join(' / ') : '—')}
      <a class="btn-light" href="#/devices/imu">IMU 与姿态</a>
    </div></section>
  </div></div>`;
}

export function ConsoleDevices({ section = 'servos', sv, st, imu, link, live, age, diagnosticElapsed = 0, pd, leaveGuards }) {
  const selected = ['servos','imu','diagnostics'].includes(section) ? section : 'servos';
  return html`<div class="console-devices"><nav class="console-device-nav" aria-label="设备分类">${[['servos','舵机与关节'],['imu','IMU 与姿态'],['diagnostics','运行诊断']].map(([key,label]) =>
    html`<a href=${'#/devices/' + key} class=${selected === key ? 'active' : ''} aria-current=${selected === key ? 'page' : undefined}>${label}</a>`)}</nav>
    ${selected === 'servos' ? html`<${ServoPage} sv=${sv} st=${st} live=${live} link=${link} leaveGuards=${leaveGuards}
      tracking=${html`<${TrackingAvailability} link=${link} open=${sv?.state === 'open' && link.status === 'open' && !sv?.closing} />`} />`
      : selected === 'imu' ? html`<${ImuPage} st=${st} imu=${imu} link=${link} live=${live} age=${age} diagnosticElapsed=${diagnosticElapsed} pd=${pd} />`
      : html`<${ConsoleDiagnostics} ...${{st,sv,imu,link,live,age,diagnosticElapsed}} />`}
  </div>`;
}
