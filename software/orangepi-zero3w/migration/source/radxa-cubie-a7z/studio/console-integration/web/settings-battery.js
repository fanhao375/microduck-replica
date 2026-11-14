// 设置页的「电池」一栏（battery-plan.md v2 §4）：型号（2S 锂电 / 台架电源）、提醒线、红线、负载电压线、读数校准
// （带「我量了电池电压」）、提示音、保养提示。设置存在鸭子上（state 目录的 battery-settings.json，后端 battery.op 校验），
// 存了马上生效。只警告：这里没有任何让鸭子动的东西。
import { html, useState, useEffect } from './vendor/preact-htm.module.js';
import { loadMute, saveMute } from './battery.js';
import { Stepper } from './stepper.js';

const f2 = (v) => (typeof v === 'number' && isFinite(v) ? v.toFixed(2) : '—');
const sign = (v) => (typeof v === 'number' && isFinite(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}` : '—');
const CLS = { rest: '卸力', light: '站着 / 坐着', heavy: '走路 / 做动作' };

const CSS = `
.bset { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.bset h2 { margin: 0; font-size: 15px; font-weight: 600; }
.bset .now { font-size: 13px; color: var(--text-2); }
.bset .row { display: grid; grid-template-columns: 120px 1fr; gap: 6px 14px; align-items: center; }
.bset .row > label, .bset .row > .k { font-size: 14px; }
.bset .note { grid-column: 2; font-size: 13px; line-height: 1.55; color: var(--text-2); }
.bset input[type=number] { width: 96px; height: 32px; padding: 0 8px; border: 1px solid #D2D2D7; border-radius: 8px; font: inherit; font-size: 15px; }
.bset .radio { display: flex; flex-wrap: wrap; gap: 4px 18px; }
.bset .radio label { display: flex; align-items: center; gap: 8px; height: 32px; font-size: 14px; }
.bset .btn { height: 34px; padding: 0 16px; border: 0; border-radius: 999px; background: #E5E5EA; font: inherit; font-size: 14px; cursor: pointer; }
.bset .btn.primary { background: var(--blue); color: #FFF; }
.bset .btn:disabled { opacity: .45; cursor: default; }
.bset .msg { font-size: 13px; line-height: 1.55; }
.bset .msg.bad { color: var(--danger); }
.bset .msg.ok { color: var(--ok); }
.bset .tips { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.65; color: var(--text-2); }
.bset .cal { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
@media (max-width: 560px) { .bset .row { grid-template-columns: 1fr; } .bset .note { grid-column: 1; } }
`;
if (!document.getElementById('bset-css')) {
  const s = document.createElement('style');
  s.id = 'bset-css';
  s.textContent = CSS;
  document.head.appendChild(s);
}

export function BatterySettings({ link, st }) {
  const [data, setData] = useState(null);         // 后端给的：settings、defaults、ranges、presets
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);           // {kind, text}
  const [busy, setBusy] = useState(false);
  const [meter, setMeter] = useState('');
  const [cal, setCal] = useState(null);           // calibrate 的回执
  const [muted, setMuted] = useState(loadMute());

  const load = async () => {
    await link.whenOpen();
    const r = await link.cmd('battery.op', { op: 'get' }, 8000);
    if (r.ok) { setData(r.data); setForm({ ...r.data.settings }); } else setMsg({ kind: 'bad', text: `读不到电池设置：${r.reason || r.error}` });
  };
  useEffect(() => { load(); }, []);

  if (!form) {
    return html`<div class="board bset" aria-label="电池"><h2>电池</h2><div class="now">${msg ? msg.text : '读取中…'}</div>${msg ? html`<button class="btn" onClick=${load}>重新读取设置</button>` : null}</div>`;
  }
  const s0 = data.settings, rg = data.ranges || {};
  const bench = form.preset === 'bench';
  const dirty = Object.keys(s0).some((k) => s0[k] !== form[k]);
  const b = st?.battery || {};
  const set = (k, v) => { setForm({ ...form, [k]: v }); setMsg(null); };
  const num = (k, step, unit) => html`<${Stepper} value=${form[k]} min=${rg[k]?.[0]} max=${rg[k]?.[1]} step=${step}
    aria-label=${k} unit=${unit} onChange=${(v) => set(k, v)} />`;

  const save = async (values) => {
    setBusy(true);
    const r = await link.cmd('battery.op', { op: 'set', values }, 8000);
    setBusy(false);
    if (r.ok) { setData(r.data); setForm({ ...r.data.settings }); setMsg({ kind: 'ok', text: '已保存，马上生效' }); setCal(null); return true; }
    setMsg({ kind: 'bad', text: `没存：${r.reason || r.error}` });
    return false;
  };
  const measure = async () => {
    const v = Number(meter);
    setCal(null);
    setMsg(null);
    const r = await link.cmd('battery.op', { op: 'calibrate', measured_v: meter === '' ? null : v }, 8000);
    if (!r.ok) { setCal({ ok: false, reason: r.reason || r.error }); return; }
    setCal(r.data);
  };

  const now = b.mode === 'no_data' ? '现在读不到舵机电压'
    : b.mode === 'estimating' ? '现在：估算中'
      : `现在：舵机读数 ${f2(b.volts)} V（${CLS[b.cls] || '状态不明'}）${bench || b.mode === 'bench' ? '' : `，电量约 ${b.pct ?? '—'}%`}`;
  return html`<div class="board bset" aria-label="电池">
    <h2>电池</h2><p class="console-caption">HD-1910 最高 8.4 V，只能使用 2S；台架电源不估算电量，提醒不会自动停止设备。</p>
    <div class="now num">${data.presets?.[s0.preset] || s0.preset} · ${s0.preset === 'bench' ? '只显示电压' : '通用查表'} · 校准 ${sign(s0.cal_offset_v)} V　${now}</div>

    <div class="row">
      <span class="k">型号</span>
      <div class="radio">
        ${Object.entries(data.presets || {}).map(([k, label]) => html`<label>
          <input type="radio" name="bat-preset" value=${k} checked=${form.preset === k} onChange=${() => set('preset', k)} /> ${label}</label>`)}
      </div>
      
    </div>

    ${bench ? null : html`
    <div class="row">
      <label>提醒线（黄）</label>
      <span>${num('warn_pct', 1, '%')}</span>
      
    </div>
    <div class="row">
      <label>红线</label>
      <span>${num('red_pct', 1, '%')}</span>
      
    </div>`}
    <div class="row">
      <label>负载电压线</label>
      <span>${num('load_floor_v', 0.1, 'V')}</span>
      
    </div>
    <div class="row">
      <label>读数校准</label>
      <span>${num('cal_offset_v', 0.01, 'V')}</span>
      
    </div>
    <div class="row">
      <span class="k">我量了电池电压</span>
      <div class="cal">
        <${Stepper} value=${meter} min=${6} max=${8.4} step=${0.01} placeholder="7.62" aria-label="万用表读数" unit="V"
          onChange=${(v) => { setMeter(v); setCal(null); }} />
        <button class="btn" type="button" disabled=${meter === ''} onClick=${measure}>算一下</button>
      </div>
      
      ${cal ? html`<div class=${'note msg ' + (cal.ok ? '' : 'bad')}>${cal.reason}
        ${cal.ok ? html`<div style="margin-top:6px"><button class="btn primary" type="button" disabled=${busy}
          onClick=${() => save({ cal_offset_v: cal.offset })}>${cal.confirm ? '确定，' : ''}用这个校准（${sign(cal.offset)} V）</button></div>` : null}</div>` : null}
    </div>

    <div class="row">
      <span class="k">提示音</span>
      <label style="font-size:14px"><input type="checkbox" checked=${!muted}
        onChange=${(e) => { const m = !e.target.checked; setMuted(m); saveMute(m); }} /> 过红线、电压很低时响一声</label>
      
    </div>

    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px">
      <button class="btn primary" type="button" disabled=${!dirty || busy} onClick=${() => save(form)}>保存</button>
      <button class="btn" type="button" disabled=${busy} onClick=${() => { setForm({ ...data.defaults }); setMsg(null); }}>恢复默认</button>
      ${msg ? html`<span class=${'msg ' + msg.kind}>${msg.text}</span>` : null}
    </div>

    <details class="console-help battery-guidance"><summary>设置说明与电池保养</summary><p>${bench
        ? '台架电源（可调电源供电）：只显示电压，不算百分比、不报电量。电压连续 30 秒低于下面的负载电压线照样提醒（电源限流了、线太细时会掉下去）'
        : 'HD-1910 最高 8.4 V，只能用 2S。电量是舵机电压查表估的（没有电量计），误差约 ±15%，没校准时偏保守（报得比实际低）'}</p><p>默认 ${data.defaults.warn_pct}%。查表的 30% 比以前主程序直线的 30% 电压高（7.42 V 对 7.08 V），黄条会比以前早亮</p><p>默认 ${data.defaults.red_pct}%，至少比提醒线低 5%。到红线也照样能站能走，只是提醒</p><p>默认 ${data.defaults.load_floor_v} V（每芯 3.45 V）。电压连续 30 秒低于这个就出最醒目的提醒</p><p>电池端电压 − 舵机读数，默认 0。15 颗舵机各有 ±0.1 V 的偏差，整体可能偏低一点</p><p>先让鸭子卸力静置 30 秒以上，用万用表量 HAT 的电池输入端，填进来，自动算出读数校准。
        接可调电源时，看电源屏上的电压跟这里对一下也行（鸭子不动时）</p><p>只在打开着的网页上响（页面关了就听不到）；跟 IMU 页的静音开关是同一个</p><ul class="tips">
      ${bench ? null : html`<li>只警告：没电时鸭子不会自己停，用完关电源、拔电池（挂着卸力也在耗电）。忘了关会把电池抽到过放，
        保护板（如果有）大约 5 V 断电，主板跟着掉电</li>
      <li>放空了尽快充；长期不用存一半电（约 7.4–7.6 V）</li>`}
      <li>设置存在鸭子上（${data.path}），换电脑、换手机都一样</li>
    </ul></details>
  </div>`;
}
