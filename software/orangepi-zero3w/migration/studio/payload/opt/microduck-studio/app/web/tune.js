// 总览下面的「运行和调参」（tuning-plan.md v2 §3）三张只读卡 + 设置页的「模型」。
// 数从后端的调参卡来（studio/tune.py，{t:'tune'} 每秒推一次），这里只画。这一版不能改
// （cap.can_edit 一直是 false，改动要等鸭子上装好 root 小助手，调参方案 T4/T5）。
// 只有设置页的重量表单会写东西，写的是 Studio 自己的 state 目录（duck-body.json），不碰鸭子。
import { html, useState, useEffect } from './vendor/preact-htm.module.js';
import { Stepper } from './stepper.js';

const CSS = `
.tune { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; align-items: start; }
/* 宽屏：运行状态占一整行，下面调手感、模型左右两列（用户 10-19：「这块做得不好」——以前三张都铺满整宽、字挤在左边） */
@media (min-width: 1100px) { .tune { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } .tune > .tn-run { grid-column: 1 / -1; } }
.tune .card { padding: 14px 16px; }
/* 三列对齐的表：名字 | 值 | 说明。每格自己画上边线，列间距用右内边距（gap 会把线断开） */
.tune .tbl { display: grid; grid-template-columns: max-content max-content minmax(0, 1fr); font-size: 13.5px; line-height: 1.6; }
.tune .tbl > * { padding: 7px 18px 7px 0; border-top: 1px solid var(--line); min-width: 0; }
.tune .tbl > :nth-child(-n+3) { border-top: 0; }
.tune .tbl > :nth-child(3n) { padding-right: 0; }
.tune .tbl .k { color: var(--text); }
.tune .tbl .v { font-weight: 600; font-variant-numeric: tabular-nums; }
.tune .tbl .d { color: var(--text-2); }
.tune .tbl .warn { color: var(--warn); }
.tune .tbl .bad { color: var(--danger); }
.tune .tn-tag { display: inline-block; white-space: nowrap; font-size: 12px; line-height: 18px; padding: 0 8px; border-radius: 999px; font-weight: 600; background: #EFEFF4; color: var(--text-2); }
.tune .tn-tag.on { background: #E7F5EC; color: var(--ok); }
.tune .tn-tag.err { background: #FDEBEC; color: var(--danger); }
.tune .cap { font-size: 13px; color: var(--text-2); margin: 0 0 10px; }
.tune .cap.bad { color: var(--warn); }
.tune h3 { margin: 0 0 10px; font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.tune .summary { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; font-size: 14px; }
.tune .summary .ph { font-weight: 600; }
.tune .summary .ph.warn { color: var(--warn); }
.tune .summary .ph.bad { color: var(--danger); }
.tune .group { margin-top: 12px; }
.tune .group > b { font-size: 13px; color: var(--text-2); font-weight: 600; }
.tune .kv { display: grid; grid-template-columns: 150px 1fr; gap: 3px 12px; font-size: 13.5px; line-height: 1.6; }
.tune .kv > span:first-child { color: var(--text-2); }
.tune .kv .warn { color: var(--warn); }
.tune .kv .bad { color: var(--danger); }
.tune .kv .ok { color: var(--ok); }
.tune .row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 14px; font-size: 13.5px; line-height: 1.7; padding: 5px 0; border-top: 1px solid var(--line); }
.tune .row:first-child { border-top: 0; }
.tune .row .k { color: var(--text); font-weight: 500; }
.tune .row .d { color: var(--text-2); }
.tune .row .v { font-weight: 500; }
.tune .row .warn { color: var(--warn); }
.tune .row .bad { color: var(--danger); }
.tune .fold { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }
.tune .fold > button { all: unset; cursor: pointer; font-size: 13px; color: var(--text-2); }
.tune .fold > button:hover { color: var(--text); }
.tune .foot { margin-top: 10px; font-size: 12.5px; color: var(--text-2); }
.tune .steps { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.tune .steps .step { display: flex; gap: 8px; line-height: 1.6; }
.tune .steps .mk { width: 16px; text-align: center; font-weight: 700; }
.tune .steps .mk.s { color: var(--ok); }
.tune .steps .mk.f { color: var(--danger); }
.tune .steps .mk.p { color: var(--blue); }
.tune .steps .mk.w { color: var(--text-2); }
.tune .steps .dt { color: var(--text-2); }
/* 设置页模型 */
.mmod { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.mmod h2 { margin: 0; font-size: 15px; font-weight: 600; }
.mmod .slot { border-top: 1px solid var(--line); padding-top: 10px; font-size: 13.5px; line-height: 1.7; }
.mmod .slot:first-of-type { border-top: 0; padding-top: 0; }
.mmod .slot .nm { font-weight: 600; }
.mmod .slot .meta { color: var(--text-2); font-size: 12.5px; }
.mmod .slot.err .nm { color: var(--danger); }
.mmod .rol { font-size: 13px; color: var(--text-2); display: flex; flex-wrap: wrap; gap: 4px 16px; }
.mmod .body { display: grid; grid-template-columns: 120px 1fr; gap: 6px 14px; align-items: center; }
.mmod .body > label, .mmod .body > .k { font-size: 14px; }
.mmod .body .note { grid-column: 2; font-size: 13px; line-height: 1.55; color: var(--text-2); }
.mmod input[type=number], .mmod input[type=text] { width: 120px; height: 32px; padding: 0 8px; border: 1px solid #D2D2D7; border-radius: 8px; font: inherit; font-size: 15px; }
.mmod input[type=text] { width: 100%; max-width: 340px; }
.mmod .btn { height: 34px; padding: 0 16px; border: 0; border-radius: 999px; background: #E5E5EA; font: inherit; font-size: 14px; cursor: pointer; }
.mmod .btn.primary { background: var(--blue); color: #FFF; }
.mmod .btn:disabled { opacity: .45; cursor: default; }
.mmod .msg { font-size: 13px; line-height: 1.55; }
.mmod .msg.bad { color: var(--danger); }
.mmod .msg.ok { color: var(--ok); }
@media (max-width: 560px) {
  .tune .kv { grid-template-columns: 1fr; }
  .tune .kv > span:first-child { color: var(--text); font-weight: 500; }
  .tune .tbl { grid-template-columns: max-content minmax(0, 1fr); }
  .tune .tbl > * { padding-right: 12px; }
  .tune .tbl > :nth-child(-n+3) { border-top: 1px solid var(--line); }
  .tune .tbl > :nth-child(-n+2) { border-top: 0; }
  .tune .tbl > .d { grid-column: 1 / -1; border-top: 0; padding-top: 0; }
  .mmod .body { grid-template-columns: 1fr; }
  .mmod .body .note { grid-column: 1; }
}
`;
if (!document.getElementById('tune-css')) {
  const s = document.createElement('style');
  s.id = 'tune-css';
  s.textContent = CSS;
  document.head.appendChild(s);
}

// ---- 格式化 ----
const num = (v, d = 1) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '—');
const rnd = (v) => (typeof v === 'number' && isFinite(v) ? String(Math.round(v)) : '—');
const txt = (v) => (v === null || v === undefined || v === '' ? '—' : String(v));

// ---- 卡 1 运行状态 ----
function StepMark({ status }) {
  const c = { success: 's', failed: 'f', starting: 'p', waiting: 'w' }[status] || 'w';
  const g = { success: '✓', failed: '✗', starting: '●', waiting: '○' }[status] || '·';
  return html`<span class=${'mk ' + c}>${g}</span>`;
}

function RunCard({ run }) {
  const [open, setOpen] = useState(false);
  const ph = run?.phase || {};
  const auto = ph.kind === 'warn' || ph.kind === 'bad' || ph.error;
  const shown = open || auto;
  const toggle = () => setOpen(!open);
  const hz = run?.loop?.hz;
  const can = run?.can_walk;
  const walk = run?.net?.walk_model;
  const actual = run?.scale?.actual;
  const st = run?.startup;
  return html`<section class="board card tn-run" aria-label="运行状态">
    <button style=${'all:unset;display:block;width:100%;cursor:pointer'} onClick=${toggle} aria-expanded=${shown}>
      <div class="summary">
        <span class=${'ph ' + (ph.kind || '')}>${txt(ph.word)}</span>
        <span>·</span>
        <span class="num">${num(hz)} Hz</span>
        ${can !== null ? html`<span>·</span><span>${can ? '能开始走' : '还不能走'}</span>` : null}
        ${walk ? html`<span>·</span><span>走路模型 ${walk}</span>` : null}
        ${actual !== null && actual !== undefined ? html`<span>·</span><span>这一拍倍率 ${num(actual, 2)}</span>` : null}
        <span style="color:var(--text-2);margin-left:auto">${shown ? '收起 ▲' : '详情 ▼'}</span>
      </div>
    </button>
    ${shown ? html`
      ${st && st.known ? html`<div class="group"><b>启动四步</b>
        <div class="steps">${(st.rows || []).map((r) => html`<div class="step">
          <${StepMark} status=${r.status} /><span>${r.name}</span>
          ${r.detail ? html`<span class="dt">${r.detail}</span>` : null}
          ${r.at_s !== null && r.at_s !== undefined ? html`<span class="dt">（开机 ${num(r.at_s)} s）</span>` : null}
        </div>`)}</div>
        ${st.unsupported ? html`<div class="foot">${st.unsupported}</div>` : null}
      </div>` : null}
      <div class="group"><b>控制</b>
        <div class="kv">
          <span>目标 / 实际频率</span><span class="num">${num(run?.loop?.target)} / ${num(hz)} Hz</span>
          <span>超时拍数</span><span class="num">${rnd(run?.loop?.missed)}${run?.loop?.missed_per_min != null ? `（最近一分钟 +${rnd(run?.loop?.missed_per_min)}）` : ''}</span>
          <span>最近一拍多久前</span><span class="num">${num(run?.loop?.last_tick_age_ms, 0)} ms</span>
          <span>在用哪个网络</span><span>${txt(run?.net?.active_model)}${run?.net?.scale_use_cn ? ` · ${run?.net?.scale_use_cn}` : ''}</span>
          <span>这一拍倍率</span><span class="num">${num(actual, 2)} = 基础 ${num(run?.scale?.base, 2)} × 电压 ${num(run?.scale?.volt_mult, 2)}</span>
        </div>
      </div>
      ${run?.guard ? html`<div class="group"><b>能不能走</b>
        <div class="kv">
          <span>关节最大偏差</span><span class="num">${num(run.guard.max_joint_error_deg, 2)}° / 容差 ${num(run.guard.joint_tolerance_deg, 2)}°</span>
          <span>机身倾角</span><span class="num">${num(run.guard.tilt_deg, 2)}° / 容差 ${num(run.guard.tilt_tolerance_deg, 2)}°</span>
          ${run.guard.reason ? html`<span>原因</span><span>${run.guard.reason}</span>` : null}
          ${run?.refusal ? html`<span>上次被拒</span><span class="warn">${run.refusal}</span>` : null}
        </div>
      </div>` : null}
      <div class="group"><b>硬件</b>
        <div class="kv">
          <span>IMU</span><span>${txt(run?.imu?.source)} · ${run?.imu?.ready === true ? '就绪' : run?.imu?.ready === false ? '没就绪' : '—'}${run?.imu?.age_ms !== undefined ? ` · ${num(run?.imu?.age_ms, 0)} ms 前` : ''}${run?.imu?.fusion?.verdict ? ` · 融合 ${run.imu.fusion.verdict}` : ''}</span>
          <span>总线</span><span>${rnd(run?.bus?.consecutive_errors)} 次连续失败${run?.bus?.startup_failures ? ` · ${rnd(run?.bus?.startup_failures)} 次启动失败` : ''}</span>
          ${(run?.bus?.worst || []).length ? html`<span>超时最多的舵机</span><span>${run.bus.worst.map((w) => `${w.name}（超时 ${w.timeout} · 缺包 ${w.missing}）`).join(' · ')}</span>` : null}
          ${run?.cpu_c !== undefined && run?.cpu_c !== null ? html`<span>主板温度</span><span class="num">${num(run.cpu_c, 0)} °C</span>` : null}
        </div>
      </div>
      <div class="foot">${txt(run?.infer_note)}</div>
    ` : null}
  </section>`;
}

// ---- 卡 2 调手感（只读） ----
function FeelCard({ feel, cap }) {
  const [adv, setAdv] = useState(true);
  const walk = feel?.walk || {};
  const vals = walk.values || {};
  const active = feel?.active;
  const pad = feel?.pad || {};
  const cur = pad.current || {};
  const servo = feel?.servo || {};
  const eff = servo.effective || {};
  // 一行三格：名字 | 值 | 说明（说明格永远在，空着也占位，表才对得齐）
  const row = (k, v, d, kind) => html`<span class="k">${k}</span><span class="v">${v}</span><span class=${'d ' + (kind || '')}>${d || ''}</span>`;
  // 走路三项：值 = 主程序在用的（读不到才用文件里的）；文件里写了、又跟在用的不一样，才提醒（文件里没写 = 用默认，不是不一致）
  const wv = (key, label, def) => {
    const x = vals[key] || {};
    const v = x.loaded != null ? x.loaded : x.file;
    const dflt = def != null ? `默认 ${num(def, 2)}` : '';
    if (x.file != null && x.loaded != null && x.file !== x.loaded) {
      return row(label, num(v, 2), `${dflt ? `${dflt} · ` : ''}文件里改成了 ${num(x.file, 2)}，主程序还在用 ${num(x.loaded, 2)}（重启主程序才生效）`, 'warn');
    }
    return row(label, num(v, 2), dflt);
  };
  const pdi = (gs) => (gs || []).map((g) => `P${g.pdi[0]} D${g.pdi[1]} ×${g.n}`).join(' / ') || '—';
  return html`<section class="board card" aria-label="调手感">
    <h3>调手感</h3>
    <p class=${'cap ' + (cap?.can_edit === false ? '' : 'bad')}>${cap?.why || '还在读鸭子的配置…'}</p>
    <div class="tbl">
      ${wv('action_scale', '走路幅度', walk.default?.action_scale)}
      ${wv('head_lowpass', '头平滑', walk.default?.head_lowpass)}
      ${wv('legs_lowpass', '腿平滑', walk.default?.legs_lowpass)}
      ${active ? row('这一拍实际用的', `${num(active.action_scale, 2)} · ${num(active.head_lowpass, 2)} · ${num(active.legs_lowpass, 2)}`,
        `幅度 · 头 · 腿${active.use ? `（${active.use}）` : ''}`) : null}
      ${row('嘴巴开度', `${num(cur.mouth_percent, 0)}%`,
        pad.mouth_warn ? '几乎张不开：网页手柄 LT / RT 张嘴会乘这个比例；「张嘴」动作不受影响' : '', pad.mouth_warn ? 'warn' : '')}
      ${row('头部幅度', `${num(cur.head_rad, 1)} rad`, '')}
    </div>
    <div class="fold">
      <button onClick=${() => setAdv(!adv)} aria-expanded=${adv}>${adv ? '▾' : '▸'} 舵机软硬 · 高级</button>
      ${adv ? html`<div class="tbl" style="margin-top:8px">
        ${row('P', rnd(eff.kp), servo.src ? `生效来源：${servo.src}` : '')}
        ${row('D', rnd(eff.kd), '')}
        ${row('限力', rnd(eff.torque_limit), '')}
        ${servo.written_p ? row('上力时写入的 P', servo.written_p.p != null ? rnd(servo.written_p.p) : '—',
          `${servo.written_p.src || ''}${servo.written_p.applied ? ' · 已写入' : ''}`) : null}
        ${servo.regs ? row('上力前舵机里', '', `RAM ${pdi(servo.regs.ram_before_arm)} · EEPROM ${pdi(servo.regs.eeprom)}`) : null}
        ${(servo.warn || []).map((w) => row('', '', w, 'warn'))}
      </div>` : null}
      ${adv && servo.regs_why ? html`<div class="foot">${servo.regs_why}</div>` : null}
    </div>
  </section>`;
}

// ---- 卡 3 模型（只读） ----
function ModelsCard({ models }) {
  const slots = models?.slots || [];
  const rollers = models?.rollers || [];
  // 名字读不到（鸭子上模型目录里的说明文件没读到）就照实说，后面跟文件指纹；别只写「没读到」（用户 10-19 看不懂）
  const what = (s) => (s.name ? `${s.name}${s.rounds ? ` · ${s.rounds} 轮` : ''}` : s.path ? '读不到模型说明' : '没装');
  const rolOn = rollers.filter((r) => r.installed);
  return html`<section class="board card" aria-label="模型">
    <h3>模型 <a href="#/models" aria-label="模型管理" style="font-size:12px;font-weight:400;color:var(--blue);text-decoration:none">管理…</a></h3>
    <div class="tbl">
      ${slots.map((s) => html`<span class="k">${s.label}</span>
        <span>${s.error ? html`<span class="tn-tag err">没加载</span>` : s.active ? html`<span class="tn-tag on">正在执行</span>`
          : s.loaded ? html`<span class="tn-tag on">已加载</span>` : html`<span class="tn-tag">${s.path ? '没加载' : '没装'}</span>`}</span>
        <span class="d">${what(s)}${s.sha12 ? html` · <span title="文件指纹（sha256 前 12 位）">${s.sha12}</span>` : null}
          ${s.error ? html`<br /><span class="bad">${s.error}</span>` : null}
          ${s.exec ? html`<br />幅度 ${num(s.exec.action_scale, 2)} · 头 ${num(s.exec.head_lowpass, 2)} · 腿 ${num(s.exec.legs_lowpass, 2)}` : null}
          ${s.buttons?.length ? html`<br />手柄键：${s.buttons.join(' ')}` : null}</span>`)}
      ${rollers.length ? html`<span class="k">轮滑（${rollers.length} 个）</span>
        <span><span class=${'tn-tag' + (rolOn.length ? ' on' : '')}>${rolOn.length ? `装了 ${rolOn.length} 个` : '没装'}</span></span>
        <span class="d">${rolOn.length
          ? rollers.map((r) => `${r.label.replace(/^滑轮 · /, '')} ${r.installed ? (r.rounds ? `${r.rounds} 轮` : '已装') : '没装'}`).join(' · ')
          : '装了轮子才用'}</span>` : null}
    </div>
  </section>`;
}

// ---- 总览三张卡 ----
export function TuneSection({ tune }) {
  if (!tune || !tune.ready) return html`<div class="board card tune"><h3>运行和调参</h3><div class="cap">还在读鸭子的配置…</div></div>`;
  return html`<div class="tune" id="tune-block">
    <${RunCard} run=${tune.run} />
    <${FeelCard} feel=${tune.feel} cap=${tune.cap} />
    <${ModelsCard} models=${tune.models} />
  </div>`;
}

// ---- 设置页 › 模型 ----
const BODY_FIELDS = [
  ['total_g', '整鸭重量', 'g'],
  ['trunk_g', '躯干', 'g'],
  ['head_g', '头', 'g'],
  ['leg_left_g', '左腿', 'g'],
  ['leg_right_g', '右腿', 'g'],
];

export function ModelSettings({ tune, link }) {
  const [body, setBody] = useState(null);
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    await link.whenOpen();
    const r = await link.cmd('tune.op', { op: 'body' }, 8000);
    if (r.ok) {
      const b = r.data || {};
      setBody(b);
      setForm(Object.fromEntries(BODY_FIELDS.map(([k]) => [k, b[k] ?? '']).concat([['foot', b.foot ?? ''], ['note', b.note ?? '']])));
    } else {
      setMsg({ kind: 'bad', text: `读不到重量：${r.reason || r.error}` });
    }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    const b = {};
    for (const [k] of BODY_FIELDS) {
      const v = form[k];
      b[k] = (v === '' || v === null || v === undefined) ? null : Number(v);
    }
    b.foot = (form.foot || '').trim() || null;
    b.note = (form.note || '').trim() || null;
    const r = await link.cmd('tune.op', { op: 'save_body', body: b }, 8000);
    setBusy(false);
    if (r.ok) { setBody(r.data); setMsg({ kind: 'ok', text: '已保存（只存在 Studio 里，导出档案时带上）' }); }
    else setMsg({ kind: 'bad', text: `没存：${r.reason || r.error}` });
  };

  const slots = tune?.models?.slots || [];
  const rollers = tune?.models?.rollers || [];
  return html`<div class="board mmod" aria-label="模型">
    <h2>模型与机器档案</h2>
    <a href="#/models/library" style="font-size:12px;color:var(--blue)">返回模型库 →</a>
    <details class="console-help"><summary>模型、执行参数与版本信息</summary>
    ${slots.map((s) => html`<div class=${'slot' + (s.error ? ' err' : '')}>
      <div><span class="nm">${s.label}</span>${s.active ? ' · 在用' : s.loaded ? ' · 已加载' : ' · 没装'}${s.rounds ? ` · ${s.rounds} 轮` : ''}</div>
      <div class="meta">${s.name ? s.name : '—'}${s.task ? ` · ${s.task}` : ''}${s.sha12 ? ` · ${s.sha12}` : ''}${s.commit ? ` · ${s.commit}` : ''}</div>
      ${s.exec ? html`<div class="meta">执行配置：幅度 ${num(s.exec.action_scale, 2)} · 头 ${num(s.exec.head_lowpass, 2)} · 腿 ${num(s.exec.legs_lowpass, 2)}</div>` : null}
      ${s.error ? html`<div class="meta" style="color:var(--danger)">${s.error}</div>` : null}
    </div>`)}
    ${rollers.length ? html`<div class="rol">${rollers.map((r) => html`<span>${r.label}：${r.installed ? (r.rounds ? `${r.rounds} 轮` : '已装') : '没装'}</span>`)}</div>` : null}
    </details>

    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px">
      <a class="btn primary" href="/api/profile" style="display:inline-flex;align-items:center;text-decoration:none">导出鸭子档案（JSON）</a>
      <span class="msg" style="color:var(--text-2)">给训练用：鸭子的 IMU 方向、舵机零位、P/D/限力、走路模型和下面的重量</span>
    </div>

    <div class="body">
      ${BODY_FIELDS.map(([k, label, unit]) => html`
        <label>${label}</label>
        <span style="display:flex;align-items:center;gap:8px">
          <${Stepper} value=${form ? form[k] : ''} min=${0} max=${5000} step=${0.1}
            aria-label=${label} unit=${unit} onChange=${(v) => setForm({ ...form, [k]: v })} />
        </span>`)}
      <span class="k">脚底材料</span>
      <span><input type="text" maxlength="40" value=${form ? form.foot : ''} aria-label="脚底材料"
        onInput=${(e) => setForm({ ...form, foot: e.target.value })} /></span>
      <span class="k">备注</span>
      <span><input type="text" maxlength="200" value=${form ? form.note : ''} aria-label="备注"
        onInput=${(e) => setForm({ ...form, note: e.target.value })} /></span>
      <span></span>
      <div class="note">重量在导出的档案里，读不到就写 null（不写 0）。填个大概就行，训练用。</div>
    </div>

    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px">
      <button class="btn primary" type="button" disabled=${!form || busy} onClick=${save}>保存重量</button>
      ${msg ? html`<span class=${'msg ' + msg.kind}>${msg.text}</span>` : null}
      ${!form ? html`<button class="btn" onClick=${load}>重新读取档案</button>` : null}
    </div>
  </div>`;
}
