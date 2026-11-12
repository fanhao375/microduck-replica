// 舵机页：servo-web 整个收进 Studio（用户 10-18：「舵机这个工具就是把外部的工具合进去，一模一样」）。
//
// 布局（用户 10-18：「布局写得不好」「每个都没对齐乱乱的」「单颗中位校准就够了，别的用不上」）：
// - 左边：3D 固定在屏幕上（往下滚也不跑），下面一张「常用」卡：一键校准、撤销、去官方姿势、全部保持 / 松开、停；
// - 右边：15 颗按左腿 / 右腿 / 头颈分三组，所有行同一套固定列宽、有表头，上下对齐；
//   点一行才展开电压、负载、电流、偏移、P/D、方向 ±；
// - 最下面折叠的「更多」：运动方式、存的姿态、高级工具（导出寄存器、改 ID……）、日志。
// 没接管总线时数据来自主程序，只能看；点「开始调舵机」才由 Studio 接管总线（主程序停、手柄不能用）。
// 后端是 studio/servo_ops.py（照搬 servo-web 的 server.py）。
import { html, Component, useState, useEffect, useRef, useCallback } from './vendor/preact-htm.module.js';
import { Duck3D, JOINT_NAMES } from './duck3d.js';
import { drawServoLabels } from './labels.js';
import { TwoStep } from './confirm.js';
import { Stepper } from './stepper.js';
import { servoReplyStatus } from './startup-status.js';

// 官方参考姿势：microduck_rl scene.xml 的关键帧（servo-web 同一份），弧度、模型约定，按 JOINT_NAMES 顺序。
const POSES = {
  zero: { label: '零位', rad: Array(15).fill(0) },
  stand: { label: '站', rad: [0, -0.0873, -0.4579, -0.0049, 0.453, 0.3491, 0.3491, 0, 0, 0, 0, 0.0873, 0.4579, 0.0049, -0.453] },
  sit: { label: '坐', rad: [0, 0, -0.5236, 1.0472, 0, 0.5, 1.6, 0, 0, 0, 0, 0, 0.5236, -1.0472, 0] },
  fold: { label: '折叠', rad: [0, 0, 1.57, 1.57, 0, 1, 1, 0, 0, 0, 0, 0, -1.57, -1.57, 0] },
};
const IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14];       // = JOINT_NAMES 的顺序
const ID_OF = Object.fromEntries(JOINT_NAMES.map((n, i) => [n, IDS[i]]));
const NAME_OF = Object.fromEntries(IDS.map((id, i) => [id, JOINT_NAMES[i]]));
const GROUP_OF = (id) => (id >= 30 ? 'H' : id >= 20 ? 'L' : 'R');
const SEL_COLOR = { R: '#E5484D', L: '#2F7CF6', H: '#F5B400' };
const GROUPS = [['L', '左腿', '20–24'], ['R', '右腿', '10–14'], ['H', '头颈', '30–34']];
const DANGER = { 5: 'ID：改了会撞号，总线上只能接这一颗再改', 6: '波特率：改了就失联', 8: '应答级别：改成 0 写指令就不回包',
  9: '最小角度限制', 11: '最大角度限制', 19: '卸载条件（保护开关）：关掉过热过流就不自动卸', 33: '运行模式：改成速度模式舵机会一直转' };
const REGS = [[41, '加速度', 1, 'SRAM'], [46, '运行速度', 2, 'SRAM'], [48, '转矩限制', 2, 'SRAM'],
  [50, '位置环P（运行中）', 1, 'SRAM'], [51, '位置环D（运行中）', 1, 'SRAM'], [21, '位置环P', 1, 'EEPROM'],
  [22, '位置环D', 1, 'EEPROM'], [23, '位置环I', 1, 'EEPROM'], [13, '最高温度上限', 1, 'EEPROM'], [19, '卸载条件', 1, 'EEPROM'],
  [28, '保护电流', 2, 'EEPROM'], [9, '最小角度限制', 2, 'EEPROM'], [11, '最大角度限制', 2, 'EEPROM'], [8, '应答级别', 1, 'EEPROM'],
  [33, '运行模式', 1, 'EEPROM'], [5, 'ID', 1, 'EEPROM'], [6, '波特率', 1, 'EEPROM']];

const fmt = (v, d = 0) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '—');
const sign = (v, d = 1) => (typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(d)}` : '—');
let calibHandler = null;      // ServoPage 设置：各处的「中位校准」都走同一个预览对话框

// ---------------- 3D ----------------

function ServoStage({ sv, pose, setPose, selected, onSelect, fixed = false }) {
  const boxRef = useRef(null), canvasRef = useRef(null), duckRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [moved, setMoved] = useState(false);

  useEffect(() => {
    const d = new Duck3D(canvasRef.current, { view: 'front', centerX: 0.5, centerY: 0.52, fill: 0.82 });
    duckRef.current = d;
    d.onUserMove = () => setMoved(true);
    d.onPick = (name) => ID_OF[name] && onSelect(ID_OF[name]);
    d.load('model/').then(() => setReady(true)).catch(() => {});
    return () => d.dispose();
  }, []);

  // 实时：读数换成弧度（方向 ×（读数 − 2048）× 2π / 4096）；参考姿势：官方关键帧
  useEffect(() => {
    const d = duckRef.current;
    if (!d || !ready) return;
    if (pose === 'live') {
      if (sv?.diagnostic) return;   // Raw startup feedback does not establish a calibrated pose.
      if (!sv?.rows?.length) return;
      d.setJoints(sv.rows.map((r) => (r.raw == null ? 0 : r.dir * (r.raw - 2048) * 2 * Math.PI / 4096)));
    } else {
      d.setJoints(POSES[pose].rad);
    }
    d.setGravity(null);
  }, [sv, pose, ready]);

  useEffect(() => {
    if (!ready) return;
    duckRef.current.select(selected ? NAME_OF[selected] : null, selected ? SEL_COLOR[GROUP_OF(selected)] : null);
  }, [selected, ready]);

  useEffect(() => {
    if (!ready || pose !== 'zero') return undefined;
    return drawServoLabels(duckRef.current, boxRef.current);
  }, [ready, pose]);

  return html`<div class="sv-stage" ref=${boxRef}>
    <canvas ref=${canvasRef} style=${{ visibility: pose === 'live' && sv?.diagnostic ? 'hidden' : 'visible' }}></canvas>
    ${!fixed ? html`<div class="sv-poses" role="tablist" aria-label="3D 显示">
      ${[['live', '实时'], ...Object.entries(POSES).map(([k, p]) => [k, p.label])].map(([k, label]) =>
        html`<button role="tab" aria-selected=${pose === k} class=${pose === k ? 'on' : ''} onClick=${(e) => { e.currentTarget.blur(); setPose(k); }}>${label}</button>`)}
    </div>` : null}
    ${!ready ? html`<span class="sv-loading">3D 模型加载中…</span>` : null}
    <span class="sv-note">${pose !== 'live' ? '参考姿势 · 不是鸭子现在的样子' : sv?.diagnostic ? '启动诊断只有原始反馈，暂不显示标定姿态' : '拖动转视角 · 点舵机选中'}</span>
    ${moved ? html`<button class="reset-view" onClick=${(e) => { e.currentTarget.blur(); duckRef.current?.resetView(); setMoved(false); }}>回正</button>` : null}
  </div>`;
}

// 页头一行「软硬：P5 · D0 · 限力 1000（跟主程序一样 · 来源）」（用户 10-19：上力的 P/D/限力跟主程序一样）。
// 手动在寄存器面板改过、或者有上着力但不一样的，后面标黄说一句。后端 servo_ops.gains_view
function GainsLine({ g }) {
  const man = g.manual || [], off = g.differ_on || [];
  return html`<div class="sv-sub sv-gains" title=${g.detail + (g.warn ? '\n' + g.warn : '')}>
    软硬：P${g.kp} · D${g.kd} · 限力 ${g.limit}（跟主程序一样 · ${g.source}）${
    g.warn ? html` · <span class="sv-warn">参数文件不合法，先按主程序默认（看日志）</span>` : null}${
    man.length ? html` · <span class="sv-warn">手动改过，跟主程序不一样：${man.map((m) => `#${m.id} ${m.what || '寄存器'}=${m.value}（寄存器 ${m.addr}）`).join('、')}</span>`
      : off.length ? html` · <span class="sv-warn">${off.map((i) => '#' + i).join(' ')} 上着力但软硬不一样（先松开再保持就改成这个）</span>` : null}
  </div>`;
}

function Why() {
  return html`<section class="board sv-card sv-why">
    <h3>为什么要人去摆、去标零位</h3>
    <ol>
      <li>每颗舵机里有一个编码器，转一圈是 0–4095 共 4096 格。舵盘是一齿一齿装上去的，每颗装进去的角度都不一样，差一两齿就是好几度。</li>
      <li><b>标定</b>就是照上面这张图，把鸭子摆成官方零位（腿伸直、头摆正、嘴闭上），告诉每颗舵机「你现在这个位置就是 <b>2048</b>」，写进舵机里。</li>
      <li>标好以后，机器读到 2048 就知道这个关节正好在零位。官方程序、训练出来的走路策略、Studio，<b>全部以 2048 为零</b>来算角度。</li>
      <li>没标好的话，策略以为腿是直的，实际弯了几度：走起来会歪、会蹭地、容易摔。</li>
      <li>机器自己不知道零件装成了什么样，只能由人照官方零位摆好，所以这一步要人来做。</li>
    </ol>
  </section>`;
}

// ---------------- 15 颗：同一套列宽，上下对齐 ----------------

function commDot(r) {
  const reply = servoReplyStatus(r);
  if (reply) return html`<span class=${'sv-dot ' + reply.kind} title=${reply.word} aria-label=${reply.word}></span>`;
  const c = r.comm || {};
  const tip = c.asked == null
    ? `累计丢 ${c.missed} 次 · 现在连丢 ${c.streak} 次（主程序统计）`
    : `问 ${c.asked} · 回 ${c.ok} · 丢 ${c.missed} · 最多连丢 ${c.max_streak}${c.alarm ? ` · 报警 ${c.alarm}` : ''}`;
  const kind = !r.fresh || c.streak >= 3 ? 'bad' : c.asked != null && c.missed > 0 && c.missed / Math.max(c.asked, 1) > 0.01 ? 'warn' : 'ok';
  return html`<span class=${'sv-dot ' + kind} title=${tip}></span>`;
}

function tempClass(r) {
  if (typeof r.temp !== 'number' || typeof r.temp_limit !== 'number' || !r.temp_limit) return '';
  if (r.temp >= r.temp_limit) return 'bad';
  if (r.temp >= r.temp_limit - 10) return 'warn';
  return '';
}

function Row({ r, open, selected, onSelect, op, follow, setHint }) {
  const [drag, setDrag] = useState(null);
  const last = useRef(0), release = useRef(null);
  useEffect(() => () => clearTimeout(release.current), []);
  const fresh = r.fresh === true && Number.isFinite(r.raw);
  const send = (v, force) => {
    if (!open || !fresh) return;
    if (!Number.isInteger(v) || v < 0 || v > 4095) { setHint('目标位置须为 0–4095 整数'); return; }
    if (r.torque !== 1) { setHint(`${r.id} ${r.cn} 尚未保持，先点这颗的「保持」`); return; }
    const now = performance.now();
    if (!force && now - last.current < 50) return;
    last.current = now;
    op({ op: 'goal', id: r.id, pos: v }, 3000, true);
  };
  const onInput = (e) => { const v = +e.target.value; setDrag(v); clearTimeout(release.current); if (follow) send(v, false); };
  const onDone = (e) => { send(+e.target.value, true); clearTimeout(release.current); release.current = setTimeout(() => setDrag(null), 600); };
  const val = drag ?? r.raw ?? 2048, c = r.comm || {}, reply = servoReplyStatus(r);
  const read = (value, digits = 0) => fresh ? fmt(value, digits) : '—';
  return html`<article class=${'console-servo-item' + (selected ? ' expanded' : '') + (fresh ? '' : ' stale')}>
    <div class="console-servo-summary">
      <button class="console-servo-main" aria-expanded=${selected} aria-controls=${'servo-detail-' + r.id}
        onClick=${() => onSelect(selected ? null : r.id)}>
        <span class="console-servo-id">#${r.id}</span><span class="console-servo-name">${r.cn}
          <small>${fresh ? `${read(r.raw)} 步` : reply?.word || '等待回读'}</small></span>
        <span class="console-servo-position num">${read(r.raw)}<small>步</small></span><span aria-hidden="true">${selected ? '⌄' : '›'}</span>
      </button>
      <div class="console-servo-actions" aria-label=${`${r.id} ${r.cn}操作`}>
        <button class=${'btn-light' + (fresh && r.torque === 0 ? ' current' : '')} disabled=${!open}
          onClick=${() => op({ op: 'torque', on: 0, ids: [r.id] })} aria-label=${`#${r.id} 松开`}>松开</button>
        <button class=${'btn-light' + (fresh && r.torque === 1 ? ' current' : '')} disabled=${!open || !fresh}
          onClick=${() => op({ op: 'torque', on: 1, ids: [r.id] })} aria-label=${`#${r.id} 保持`}>保持</button>
        <button class="btn-light" disabled=${!open || !fresh} onClick=${() => calibHandler && calibHandler(String(r.id))}
          aria-label=${`#${r.id} 中位标定`}>中位</button>
      </div>
    </div>
    ${selected ? html`<div id=${'servo-detail-' + r.id} class="console-servo-detail">
      <div class="console-servo-metrics"><span>温度<b class=${tempClass(r)}>${read(r.temp)} °C</b></span>
        <span>电压<b>${read(r.volt, 1)} V</b></span><span>电流<b>${read(r.current_ma)} mA</b></span><span>负载<b>${read(r.load)}</b></span></div>
      <div class="console-servo-target"><label for=${'servo-target-' + r.id}>目标位置</label><b class="num">${fmt(val)} 步</b>
        <div class="console-servo-target-line"><input id=${'servo-target-' + r.id} type="range" min="0" max="4095" step="1" value=${val} disabled=${!open || !fresh}
          aria-label=${`${r.cn} 目标位置`} onInput=${onInput} onChange=${onDone} />
          <input type="number" min="0" max="4095" step="1" value=${val} disabled=${!open || !fresh} aria-label=${`${r.cn} 目标原始位置`}
            onInput=${e => setDrag(e.target.value === '' ? '' : Number(e.target.value))} />
          <button class="btn-light" disabled=${!open || !fresh || !Number.isInteger(val)} onClick=${() => send(val, true)}>移动</button></div></div>
      <details><summary>参数与详细回读</summary><div class="console-servo-parameters">
        <span>运行 P / D / 限力<b>${read(r.p_run)} / ${read(r.d_run)} / ${read(r.limit)}</b></span>
        <span>EEPROM P / D<b>${read(r.p)} / ${read(r.d)}</b></span><span>零位偏移<b>${fresh && r.offset != null ? r.offset : '—'}</b></span>
        <span>原始速度 / 电流<b>${read(r.speed_raw)} / ${read(r.current_raw)}</b></span>
        <span>通信<b>${reply?.word || (fresh ? `丢 ${c.missed ?? '—'} / 问 ${c.asked ?? '—'}` : '等待有效回读')}</b></span>
        <span>状态<b>${fresh && r.status != null ? r.status || '无报警' : '—'}</b></span>
        <span>方向<button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'set_dir', id: r.id, dir: -r.dir })}>${r.dir > 0 ? '+1' : '−1'}</button></span>
      </div></details>
    </div>` : null}
  </article>`;
}

// 舵机数据每秒推 10 次：这一行显示的东西没变就不重画（15 行带滑块，整页重画会让 3D 转起来卡）
const rowSig = (p) => { const r = p.r, c = r.comm || {};
  return [r.raw, r.deg, r.volt, r.temp, r.load, r.current_ma, r.current_raw, r.speed_raw, r.responding, r.torque, r.fresh, r.dir, r.offset, r.p, r.d, r.status,
    r.p_run, r.d_run, r.limit, r.gains_ok, c.missed, c.streak, p.open, p.selected, p.follow].join('|'); };
class RowPure extends Component {
  shouldComponentUpdate(next) { return rowSig(next) !== rowSig(this.props) || next.op !== this.props.op; }
  render(props) { return html`<${Row} ...${props} />`; }
}

function ServoList({ sv, open, selected, onSelect, op, follow, setHint }) {
  const rows = sv.rows || [];
  return html`<section class="board sv-list console-servo-list"><div class="console-card-head"><h2>舵机列表</h2><span>${sv.online ?? '—'} / ${sv.total ?? '—'} 在线</span></div>
    ${rows.length ? GROUPS.map(([g, label, range]) => html`<section class="console-servo-group"><h3>${label}<small>${range}</small></h3>
      ${rows.filter(r => r.group === g).map(r => html`<${RowPure} key=${r.id} r=${r} open=${open} selected=${r.id === selected}
        onSelect=${onSelect} op=${op} follow=${follow} setHint=${setHint} />`)}</section>`)
      : html`<p class="console-empty">等待舵机回读。</p>`}
  </section>`;
}

// ---------------- 常用（3D 下面） ----------------

// 选一颗舵机的下拉框：跟 3D、表格联动（哪边选了，另外两边都跟着变）
function ServoPick({ selected, onSelect, rows }) {
  return html`<select class="sv-pick" value=${selected ?? ''} onChange=${(e) => onSelect(e.target.value === '' ? null : +e.target.value)}>
    <option value="">选一颗舵机</option>
    ${GROUPS.map(([g, label]) => html`<optgroup label=${label}>${(rows || []).filter((r) => r.group === g).map((r) =>
      html`<option value=${r.id}>${r.id} · ${r.cn}</option>`)}</optgroup>`)}
  </select>`;
}

// 照 servo-web 顶上那一排（用户 10-18：「跟 servo-web 一样，左边 3D、右边各种功能，对齐」）
function Toolbar({ open, op, motion, setMotion, idle, setIdle, follow, setFollow, poses, setHint, syncSliders }) {
  const ms = { seconds: motion.seconds, mode: motion.mode, acc: motion.acc };
  const goNamed = (word, fallback) => {
    const pose = poses.find(p => p.name.includes(word));
    if (pose) op({ op: 'go_pose', file: pose.file, ...ms });
    else if (fallback) op({ op: 'official', key: fallback, ...ms });
    else setHint(`尚未保存「${word}」姿态`);
  };
  return html`<div class="console-motion-controls"><div class="sv-ctrl">
    <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'official', key: 'zero', ...ms })}>全部回零位</button>
    <button class="btn-light" disabled=${!open} onClick=${() => goNamed('站姿', 'stand')}>站姿</button>
    <button class="btn-light" disabled=${!open} onClick=${() => goNamed('缩起来')}>缩起来</button>
    <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'official', key: 'sit', ...ms })}>坐姿</button>
    <button class="btn-light" disabled=${!open} onClick=${() => { if (window.confirm('折叠会转动关节；确认零位与机械空间后继续？')) op({ op: 'official', key: 'fold', ...ms }); }}>折叠</button>
    <button class="btn-light" disabled=${!open} onClick=${async () => { const r = await op({ op: 'idle', on: !idle }); if (r.ok) setIdle(!idle); }}>${idle ? '停止灵动' : '头部灵动'}</button>
    <button class="btn-light" onClick=${syncSliders}>同步当前位置</button>
  </div><div class="sv-ctrl"><span>用时 <${Stepper} value=${motion.seconds} min=${0} max=${30} step=${0.5} onChange=${v => setMotion({ ...motion, seconds: v === '' ? 0 : v })} unit="秒" /></span>
    <label><input type="checkbox" checked=${follow} onChange=${e => setFollow(e.target.checked)} /> 跟随拖动</label></div></div>`;
}

// ---------------- 更多（折叠） ----------------

function MoreArea({ open, op, motion, setMotion, follow, setFollow, poses, setPoses, idle, setIdle, setHint, syncSliders }) {
  const [name, setName] = useState('');
  const ms = { seconds: motion.seconds, mode: motion.mode, acc: motion.acc };
  return html`<details class="board console-motion-area"><summary>姿态与运动</summary><div class="console-motion-body">
    <${Toolbar} ...${{ open, op, motion, setMotion, idle, setIdle, follow, setFollow, poses, setHint, syncSliders }} />
    <div class="sv-form"><span class="sv-label">运动方式</span><div class="sv-ctrl"><select value=${motion.mode} onChange=${e => setMotion({ ...motion, mode: e.target.value })}>
      <option value="profile">平滑 · 舵机梯形曲线</option><option value="stream">流式 · 分步发送</option></select>
      <span>加速度 <${Stepper} value=${motion.acc} min=${1} max=${254} onChange=${v => setMotion({ ...motion, acc: v === '' ? 0 : v })} /></span></div>
      <span class="sv-label">保存的姿态</span><div class="sv-ctrl sv-chips">${poses.map(pose => html`<span class="sv-chip"><button disabled=${!open} onClick=${() => op({ op: 'go_pose', file: pose.file, ...ms })}>${pose.name}</button>
        <button class="x" disabled=${!open} title="删除姿态" onClick=${async () => { if (!window.confirm(`删除「${pose.name}」？`)) return; const r = await op({ op: 'delete_pose', file: pose.file }); if (r.ok) setPoses(r.data.poses); }}>×</button></span>`)}</div>
      <span></span><div class="sv-ctrl"><input value=${name} placeholder="姿态名称" onInput=${e => setName(e.target.value)} />
        <button class="btn-light" disabled=${!open || !name.trim()} onClick=${async () => { const r = await op({ op: 'save_pose', name }); if (r.ok) { setPoses(r.data.poses); setName(''); } }}>保存当前位置</button>
        <button class="btn-light" disabled=${!open} onClick=${async () => { const r = await op({ op: 'poses' }); if (r.ok) setPoses(r.data.poses); }}>刷新姿态</button></div>
    </div></div></details>`;
}

function Tools({ open, op, selected, onSelect, sv, dirs }) {
  const sel = selected ?? 20;
  const [newId, setNewId] = useState('');
  const [reg, setReg] = useState(41);
  const [size, setSize] = useState(1);
  const [val, setVal] = useState('');
  const [unlock, setUnlock] = useState(false);
  const [scanIds, setScanIds] = useState('20-24,30-34,10-14');
  const [out, setOut] = useState(null);
  const online = new Set((sv.rows || []).filter((r) => r.fresh).map((r) => r.id));
  const run = async (args, t) => { const r = await op(args, t); if (r.ok && r.data) setOut({ op: args.op, ...r.data }); };
  const copyDirs = () => {
    const line = 'directions = [' + IDS.map((id) => ((dirs[id] ?? -1) < 0 ? -1 : 1)).join(', ') + ']';
    navigator.clipboard?.writeText(line);
    setOut({ op: 'note', text: `已复制：${line}（robotd.toml 的 [bus.feetech] directions，顺序 20–24、30–34、10–14）` });
  };
  return html`<section class="sv-form">
    <span class="sv-label">舵机</span>
    <div class="sv-ctrl"><${ServoPick} selected=${sel} onSelect=${(id) => onSelect(id ?? 20)} rows=${sv.rows} /></div>
    <span class="sv-label">高级</span>
    <div class="sv-ctrl">
      <button class="btn-light" disabled=${!open} onClick=${() => run({ op: 'dump', id: sel })}>导出寄存器</button>
      <button class="btn-light" disabled=${!open} onClick=${() => { if (window.confirm(`重启 ${sel}（先关扭矩，约 0.8 秒后回来）？`)) run({ op: 'reboot', id: sel }); }}>重启</button>
      <button class="btn-light" onClick=${copyDirs}>复制 robotd 方向表</button>
    </div>
    <span class="sv-label">改 ID</span>
    <div class="sv-ctrl"><span>${sel} →</span>
      <select value=${newId} onChange=${(e) => setNewId(e.target.value)}>
        <option value="">选新 ID</option>
        ${Array.from({ length: 254 }, (_, i) => i).filter((i) => i !== sel).map((i) => html`<option value=${i}>${i}${online.has(i) ? '（已占用）' : ''}</option>`)}
      </select>
      <button class="btn-danger" disabled=${!open || newId === ''} onClick=${() => {
        if (online.has(+newId)) { window.alert(`ID ${newId} 已经有舵机在用，会撞号`); return; }
        if (window.confirm(`把 ${sel} 改成 ${newId}？改的时候总线上最好只接这一颗。`)) run({ op: 'set_id', old: sel, new: +newId });
      }}>写 ID</button></div>
    <span class="sv-label">写寄存器</span>
    <div class="sv-ctrl"><select value=${reg} onChange=${(e) => { const r = REGS.find((x) => x[0] === +e.target.value); setReg(+e.target.value); setSize(r ? r[2] : 1); setUnlock(r ? r[3] === 'EEPROM' : false); }}>
        ${REGS.map(([a, n, s, area]) => html`<option value=${a}>${a} · ${n}（${s} 字节 ${area}）</option>`)}</select>
      <input type="number" value=${val} onInput=${(e) => setVal(e.target.value)} placeholder="值" style="width:80px" />
      <label class="sv-inline"><input type="checkbox" checked=${unlock} onChange=${(e) => setUnlock(e.target.checked)} /> 解锁写</label>
      <button class=${DANGER[reg] ? 'btn-danger' : 'btn-light'} disabled=${!open || val === ''} onClick=${() => {
        const numeric = Number(val);
        if (!Number.isInteger(numeric) || numeric < 0 || numeric > (size === 1 ? 255 : 65535)) { window.alert('写入值超过字段字节范围'); return; }
        if (DANGER[reg] && !window.confirm(`危险：${DANGER[reg]}\n确定把 ${sel} 的寄存器 ${reg} 写成 ${val}？`)) return;
        run({ op: 'write_reg', id: sel, addr: +reg, size, value: numeric, unlock });
      }}>写</button></div>
    ${DANGER[reg] ? html`<span></span><p class="sv-warn">寄存器 ${reg}：${DANGER[reg]}</p>` : null}
    <span class="sv-label">总线</span>
    <div class="sv-ctrl">
      <button class="btn-light" disabled=${!open} onClick=${() => run({ op: 'timing', n: 200 }, 30000)}>时序测试 ×200</button>
      <button class="btn-light" disabled=${!open} onClick=${() => run({ op: 'scan_all' }, 30000)}>全网扫描 0–253</button>
      <input type="text" value=${scanIds} onInput=${(e) => setScanIds(e.target.value)} style="width:140px" />
      <button class="btn-light" disabled=${!open} onClick=${() => run({ op: 'scan', ids: scanIds })}>扫描</button>
    </div>
    ${out ? html`<span></span><${ToolOut} out=${out} />` : null}
  </section>`;
}

function ToolOut({ out }) {
  if (out.op === 'note') return html`<p class="sv-small">${out.text}</p>`;
  if (out.op === 'timing') return html`<p class="sv-out num">时序：${out.n} 次 × ${out.ids} 颗 · 最快 ${out.min_ms} ms · 平均 ${out.avg_ms} ms · p99 ${out.p99_ms} ms · 最慢 ${out.max_ms} ms · 漏读 ${out.fails}</p>`;
  if (out.op === 'scan' || out.op === 'scan_all') return html`<p class="sv-out">在线 ${out.found.length} 颗：${out.found.join('、') || '无'}${out.lost?.length ? `；不在线：${out.lost.join('、')}` : ''}</p>`;
  if (out.op === 'set_id') return html`<p class="sv-out">${out.ok ? '改好了，新 ID ping 得通' : '新 ID ping 不通'}</p>`;
  if (out.op === 'write_reg') return html`<p class="sv-out">原来 ${out.old} → 读回 ${out.new}${out.same ? ' ✓' : '（读回跟写的不一样）'}</p>`;
  if (out.op === 'dump') {
    const md = ['| 地址 | 名称 | 区 | 值 |', '|---|---|---|---|', ...out.rows.map((r) => `| ${r.addr} | ${r.name} | ${r.area} | ${r.value} |`)].join('\n');
    const download = () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' }));
      a.download = `HD-1910-id${out.id}-regs-${new Date().toISOString().slice(0, 10)}.json`; a.click();
    };
    return html`<div class="sv-out">
      <div class="sv-ctrl"><b>#${out.id} 寄存器</b><button class="btn-light" onClick=${download}>下载 JSON</button>
        <button class="btn-light" onClick=${() => navigator.clipboard?.writeText(md)}>复制 Markdown 表</button></div>
      <div class="sv-dump">${out.rows.map((r) => html`<span class=${DANGER[r.addr] ? 'danger' : ''}><i>${r.addr}</i> ${r.name} <b class="num">${r.value}</b></span>`)}</div>
    </div>`;
  }
  return null;
}

function LogBox({ link }) {
  const [items, setItems] = useState([]);
  useEffect(() => link.on((m) => {
    if (m.t === 'log' && ['总线', '校准', '安全', '命令'].includes(m.entry.cat)) setItems((xs) => [m.entry, ...xs].slice(0, 60));
  }), []);
  return html`<div class="sv-logwrap">
    <div class="sv-label">日志 <span class="sv-small">（总线、校准、安全；全部在左边「日志」页）</span></div>
    <div class="sv-log">${items.length ? items.map((e) => html`<div key=${e.n} class=${e.level}><span class="num">${e.t}</span> [${e.cat}] ${e.msg}${e.raw ? html` <small>${e.raw}</small>` : null}</div>`)
      : html`<span class="sv-small">还没有</span>`}</div>
  </div>`;
}

function CalibDialog({ plan, onOk, onCancel }) {
  const big = plan.rows.filter((r) => r.diff_deg != null && Math.abs(r.diff_deg) > plan.big_deg);
  return html`<div class="sv-modal" role="dialog" aria-modal="true">
    <div class="board sv-modal-box">
      <h2>中位校准（官方零位 2048）</h2>
      <p class="sv-small">鸭子要摆成官方零位：腿伸直、头摆正、嘴闭上。每颗只关几毫秒扭矩；校完自动保持住。${plan.sim ? '（演示：没接舵机，校的是假的）' : ''}</p>
      <table class="sv-plan"><thead><tr><th>舵机</th><th class="r">现在</th><th class="r">写成</th><th class="r">差</th></tr></thead>
        <tbody>${plan.rows.map((r) => html`<tr class=${big.includes(r) ? 'big' : ''}><td>${r.id}</td><td class="r num">${fmt(r.pos)}</td>
          <td class="r num">2048</td><td class="r num">${r.diff_deg == null ? '读不到' : sign(r.diff_deg) + '°'}</td></tr>`)}</tbody></table>
      ${big.length ? html`<p class="sv-warn">有 ${big.length} 颗差 ${plan.big_deg}° 以上。装舵盘时转了半圈没关系；要是方向 ± 设反了，校进去的零点是错的。</p>` : null}
      <div class="sv-modal-btns"><button class="btn-light" onClick=${onCancel}>取消</button>
        <button class="btn-dark" onClick=${() => { if (!big.length || window.confirm(`有 ${big.length} 颗差 ${plan.big_deg}° 以上，确定照现在的位置校？`)) onOk(); }}>确认，校准 ${plan.rows.length} 颗</button></div>
    </div>
  </div>`;
}

// ---------------- 页面 ----------------

// 「交还主程序」：页面内两步确认（confirm.js 的 TwoStep，跟测急停的跳过共用：审查 D4）——第一下旁边冒出确认按钮，
// 700 ms 后、3 秒内点它才交还（舵机页头上、每一页顶上的提示条都用它）。
// 交还走后端 hand_back：先等在路上的舵机操作做完，再在总线上全部卸力并回读确认、关总线、起主程序（小助手在的话）。
// 交还是 Studio 当场卸力（审查 D7：以前写「主程序起来时会松开」不对；不卸力的交接要等 fork 的 robotd 部署）
const HANDBACK_ASK = '交还：马上把 15 颗全部松开，鸭子会软下来——先扶住或放平';
export function HandBackButton({ link, disabled = false }) {
  const [err, setErr] = useState(null);
  const confirm = async () => {
    setErr(null);
    if (disabled || link.status !== 'open') { setErr('连接或交还状态尚未就绪'); return; }
    const r = await link.cmd('servo.close', {}, 90000);     // 小助手起主程序要等启动四步（最多 60 秒）
    if (!r.ok) setErr(r.reason || r.error || '没交还成');
  };
  return html`<span class="sv-handback"><${TwoStep} label="交还主程序" ask=${HANDBACK_ASK} busyLabel="交还中…" disabled=${disabled || link.status !== 'open'} onConfirm=${confirm} />${
    err ? html`<span class="sv-warn">${err}</span>` : null}</span>`;
}

export function ServoPage({ sv, link, leaveGuards, tracking, live }) {
  const [tab, setTab] = useState('calibration');
  const [selected, setSelected] = useState(null), [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null), [hint, setHint] = useState(null);
  const [motion, setMotion] = useState({ seconds: 2, mode: 'profile', acc: 30 });
  const [follow, setFollow] = useState(true), [poses, setPoses] = useState([]), [idle, setIdle] = useState(false);
  const [plan, setPlan] = useState(null), [syncKey, setSyncKey] = useState(0);
  const [ports, setPorts] = useState(null), [pick, setPick] = useState('');
  const latest = useRef({ sv, link }); latest.current = { sv, link };
  const open = sv?.state === 'open' && link.status === 'open' && !sv.closing;
  const syncSliders = useCallback(() => { setSyncKey(k => k + 1); setHint('滑块已对齐当前位置'); }, []);
  const onSelect = useCallback(id => setSelected(id), []);
  const dirs = Object.fromEntries((sv?.rows || []).map(r => [r.id, r.dir]));
  const readSv = link.status === 'open' && (sv?.state === 'open' || live !== false) ? sv : { ...sv, online: null, rows: (sv?.rows || []).map(row => ({ ...row, fresh: false })) };
  const op = useCallback(async (args, timeout = 15000, quiet = false) => {
    const current = latest.current;
    if (current.link.status !== 'open' || current.sv?.state !== 'open' || current.sv.closing) {
      const error = '维护控制权或连接尚未就绪'; setMsg(error); return { ok: false, error };
    }
    const r = await current.link.cmd('servo.op', args, timeout);
    if (!r.ok) setMsg(r.reason || r.error || '操作未完成'); else if (!quiet) setMsg(null);
    if (r.ok && args.op === 'stop') setIdle(false);
    return r;
  }, []);
  const run = async (name, args = {}) => {
    setBusy(true); setMsg(null);
    try { const r = await link.cmd(name, args, 15000); if (!r.ok) setMsg(r.reason || r.error || '操作未完成'); }
    finally { setBusy(false); }
  };
  const loadPorts = async () => {
    const r = await link.cmd('servo.ports', {}, 5000);
    if (r.ok) { setPorts(r.data); setPick(p => p || (r.data.sim ? 'sim' : r.data.port || '')); }
  };
  useEffect(() => {
    let alive = true;
    // Port enumeration is read-only; entering this page never takes the bus.
    link.whenOpen().then(() => { if (alive) loadPorts(); });
    // Keep the original backend-owned handoff rule: held or unknown joints stay owned.
    return () => { alive = false; link.cmd('servo.close', { if_idle: true }, 15000); };
  }, []);
  calibHandler = useCallback(async ids => {
    const text = Array.isArray(ids) ? ids.join(',') : String(ids);
    const r = await op({ op: 'calib_plan', ids: text });
    if (r.ok) setPlan({ ...r.data, ids: text });
  }, []);
  useEffect(() => { if (open) op({ op: 'poses' }).then(r => { if (r.ok) setPoses(r.data.poses); }); else setIdle(false); }, [open]);
  useEffect(() => { if (sv?.latched || sv?.relaxing || sv?.closing) setIdle(false); }, [sv?.latched, sv?.relaxing, sv?.closing]);
  useEffect(() => { if (!hint) return; const timer = setTimeout(() => setHint(null), 4000); return () => clearTimeout(timer); }, [hint]);
  return html`<div class="sv-page console-servo-page">
    <div class="console-servo-toolbar"><div class="sv-ctrl">
      ${sv?.state === 'open' ? html`<${HandBackButton} link=${link} disabled=${!!sv.closing} />`
        : html`<button class="btn-dark" disabled=${busy || link.status !== 'open' || sv?.state === 'opening'} onClick=${() => run('servo.open', pick ? { port: pick } : {})}>${busy || sv?.state === 'opening' ? '接管中…' : '接管总线'}</button>`}
      <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'scan', ids: '20-24,30-34,10-14' })}>扫描</button>
      <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'torque', on: 1 })}>全部保持</button>
      <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'stop' })}>停止过渡</button>
      <button class="btn-light" disabled=${!open} onClick=${() => { if (window.confirm('将上次备份的舵机偏移写回，撤销上次标定？')) op({ op: 'undo' }, 30000); }}>撤销上次</button>
    </div><span>${sv?.closing ? '交还中' : sv?.state === 'open' ? 'Studio 控制中' : '主程序回读 · 只读'}</span></div>
    ${msg || sv?.error ? html`<div class="banner bad" role="alert">${msg || sv.error}<button class="btn-light" onClick=${() => setMsg(null)}>收起</button></div>` : null}
    ${sv?.marker_error ? html`<div class="banner warn" role="status">会话标记未保存：${sv.marker_error}</div>` : null}
    ${sv?.gains?.warn || sv?.gains?.manual?.length || sv?.gains?.differ_on?.length ? html`<p class="console-device-hint" role="status">部分关节参数与主程序配置需要核对。<button class="btn-light" onClick=${() => setTab('advanced')}>查看运行参数</button></p>` : null}
    ${hint ? html`<p class="console-device-hint" role="status">${hint}</p>` : null}
    <div class="console-servo-tabs" role="tablist" aria-label="舵机功能">${[['calibration','舵机标定'],['tracking','关节跟踪'],['advanced','高级工具']].map(([key,label]) => html`<button role="tab" aria-selected=${tab === key} class=${tab === key ? 'active' : ''} onClick=${() => setTab(key)}>${label}</button>`)}</div>
    <div hidden=${tab !== 'calibration'} class="console-servo-calibration">
      <aside class="console-zero-reference"><section class="board"><div class="console-card-head"><h2>零位参考</h2><span>15 关节 · 0 rad</span></div>
        <${ServoStage} sv=${sv} pose="zero" selected=${selected} onSelect=${onSelect} fixed />
        <details class="console-reference-help"><summary>零位说明</summary><${Why} /></details></section></aside>
      <div class="console-servo-controls"><${ServoList} key=${'list' + syncKey} sv=${readSv || { rows: [] }} open=${open} selected=${selected} onSelect=${onSelect} op=${op} follow=${follow} setHint=${setHint} />
        <${MoreArea} ...${{open,op,motion,setMotion,follow,setFollow,poses,setPoses,idle,setIdle,setHint,syncSliders}} /></div>
    </div>
    ${tab === 'tracking' ? tracking || html`<section class="board console-empty">跟踪能力尚未读取。</section>` : null}
    ${tab === 'advanced' ? html`<div class="board console-servo-advanced"><details><summary>串口与运行参数</summary><div class="sv-ctrl console-port-controls">
      <select value=${pick} onFocus=${loadPorts} onChange=${e => setPick(e.target.value)} disabled=${sv?.state === 'open'} aria-label="维护串口"><option value="">使用已配置串口</option>
        ${(ports?.ports || []).map(p => html`<option value=${p.device}>${p.device}${p.desc ? ' · ' + p.desc : ''}</option>`)}
        ${ports?.port && !(ports.ports || []).some(p => p.device === ports.port) ? html`<option value=${ports.port}>${ports.port}</option>` : null}<option value="sim">模拟串口（显式选择）</option></select>
      <button class="btn-light" disabled=${sv?.state === 'open' || busy || !pick} onClick=${() => run('servo.open', { port: pick })}>接管所选串口</button></div>
      <div class="console-maintenance-stats"><span>平均电压 <b>${fmt(sv?.avg_volts, 1)} V</b></span><span>采集 <b>${open ? fmt(sv?.hz) : '—'} Hz</b></span><span>保持 <b>${sv?.holding ?? '—'}</b></span><span>丢帧 <b>${open && sv?.drop_rate != null ? fmt(sv.drop_rate * 100, 1) : '—'} %</b></span></div>
      ${sv?.gains ? html`<${GainsLine} g=${sv.gains} />` : null}</details>
      <${Tools} open=${open} op=${op} selected=${selected} onSelect=${onSelect} sv=${sv || { rows: [] }} dirs=${dirs} />
      <details><summary>维护日志</summary><${LogBox} link=${link} /></details></div>` : null}
    ${plan ? html`<${CalibDialog} plan=${plan} onCancel=${() => setPlan(null)} onOk=${async () => {
      const ids = plan.ids; setPlan(null); const r = await op({ op: 'calibrate', ids }, 60000);
      if (r.ok) { const d = r.data, bad = Object.keys(d.bad || {}); setHint(`校准完成 ${d.ok.length} 颗${bad.length ? '，未完成：' + bad.join('、') : ''}；成功的关节已保持`); }
    }} />` : null}
  </div>`;
}
