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
import { ServoMonitor, ServoInspector, ReadOrderPanel } from './communication-health.js';

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

function ServoStage({ sv, pose, setPose, selected, onSelect, compact = false }) {
  const boxRef = useRef(null), canvasRef = useRef(null), duckRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [moved, setMoved] = useState(false);

  useEffect(() => {
    const d = new Duck3D(canvasRef.current, { view: 'front', centerX: 0.5, centerY: compact ? 0.56 : 0.52, fill: compact ? 0.65 : 0.82 });
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
    <div class="sv-poses" role="tablist" aria-label="3D 显示">
      ${[['live', '实时'], ...Object.entries(POSES).map(([k, p]) => [k, p.label])].map(([k, label]) =>
        html`<button role="tab" aria-selected=${pose === k} class=${pose === k ? 'on' : ''} onClick=${(e) => { e.currentTarget.blur(); setPose(k); }}>${label}</button>`)}
    </div>
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
  const [drag, setDrag] = useState(null);           // 拖动中的滑块值；松手 0.6 秒后回到实际读数
  const last = useRef(0), release = useRef(null);
  const send = (v, force) => {
    if (!open) return;
    if (r.torque !== 1) { setHint(`${r.id} ${r.cn} 扭矩没开：拖滑块舵机不会动（先打开它的「保持」）`); return; }
    const now = performance.now();
    if (!force && now - last.current < 50) return;   // 每 50 ms 最多发一次
    last.current = now;
    op({ op: 'goal', id: r.id, pos: v }, 3000, true);
  };
  const onInput = (e) => { const v = +e.target.value; setDrag(v); clearTimeout(release.current); if (follow) send(v, false); };
  const onDone = (e) => { const v = +e.target.value; send(v, true); clearTimeout(release.current); release.current = setTimeout(() => setDrag(null), 600); };
  const val = drag ?? r.raw ?? 2048;
  const stop = (e) => e.stopPropagation();
  const c = r.comm || {};
  const reply = servoReplyStatus(r);
  return html`<div class=${'sv-row' + (r.fresh ? '' : ' stale') + (selected ? ' sel ' + r.group : '')} onClick=${() => onSelect(selected ? null : r.id)}>
    <span class=${'sv-id ' + r.group}>${r.id}</span>
    <span class="sv-name" title=${r.en}>${r.cn}</span>
    <input class="sv-slider" type="range" min="0" max="4095" step="1" value=${val} disabled=${!open}
      aria-label=${`${r.cn} 目标位置`} onClick=${stop} onInput=${onInput} onChange=${onDone} />
    <span class="num r">${fmt(drag ?? r.raw)}</span>
    <span class="num r sv-deg">${r.deg == null ? '—' : sign(r.deg) + '°'}</span>
    <span class=${'num r ' + tempClass(r)} title=${r.temp_limit ? `这颗的温度上限 ${r.temp_limit} °C` : ''}>${fmt(r.temp)}°C</span>
    <label class=${'sv-switch' + (r.torque === 1 ? ' on' : '')} onClick=${stop} title=${reply ? '启动诊断：只显示应答情况，扭矩状态未知' : r.torque === 1 ? '在保持：点一下松开' : '松着：点一下保持（先对准当前位置再上力，不会冲）'}>
      <input type="checkbox" checked=${r.torque === 1} disabled=${!open}
        onChange=${(e) => op({ op: 'torque', on: e.target.checked ? 1 : 0, ids: [r.id] })} />
      <span>${reply ? reply.word : r.torque === 1 ? '保持' : '松'}</span>
    </label>
    <button class="sv-mini" disabled=${!open} onClick=${(e) => { stop(e); calibHandler && calibHandler(String(r.id)); }}
      title="中位校准：把这颗现在的位置设成零位 2048，校完自动保持">中位</button>
    ${commDot(r)}
    ${selected ? html`<div class="sv-more num" onClick=${stop}>
      <span>电压 <b>${fmt(r.volt, 1)} V</b></span><span>负载 <b>${fmt(r.load)}</b></span>
      ${r.current_ma != null ? html`<span>电流 <b>${fmt(r.current_ma)} mA</b></span>` : null}
      ${r.current_raw != null ? html`<span>电流原始值 <b>${fmt(r.current_raw)}</b></span>` : null}
      ${r.speed_raw != null ? html`<span>速度原始值 <b>${fmt(r.speed_raw)}</b></span>` : null}
      ${r.offset != null ? html`<span title="寄存器 31：舵机里存的零位偏移">偏移 <b>${r.offset}</b></span>` : null}
      ${r.p_run != null ? html`<span title=${`运行中的 P / D / 限力（回读 RAM 50 / 51 / 48；上力前 Studio 写成跟主程序一样，标黄 = 不一样）。EEPROM 里存的 P / D（21 / 22）：${r.p ?? '?'} / ${r.d ?? '?'}`}>P/D/限力 <b class=${r.gains_ok === false ? 'warn' : ''}>${r.p_run}/${r.d_run}/${r.limit ?? '?'}</b></span>`
        : r.p != null ? html`<span title="位置环 P / D（寄存器 21 / 22）">P/D <b>${r.p}/${r.d}</b></span>` : null}
      <span>通信 <b>${reply ? reply.word : `丢 ${c.missed ?? 0}${c.asked != null ? ` / 问 ${c.asked}` : ''}`}</b></span>
      ${r.status ? html`<span class="sv-alarm">状态位 ${r.status}</span>` : null}
      <span class="sv-spacer"></span>
      <span>方向</span><button class="sv-mini" disabled=${!open} onClick=${() => op({ op: 'set_dir', id: r.id, dir: -r.dir })}
        title="方向 ±：读数变大对应模型里哪个方向（只影响显示和换算，不动舵机）">${r.dir > 0 ? '+1' : '−1'}</button>
    </div>` : null}
  </div>`;
}

// 舵机数据每秒推 10 次：这一行显示的东西没变就不重画（15 行带滑块，整页重画会让 3D 转起来卡）
const rowSig = (p) => { const r = p.r, c = r.comm || {};
  return [r.raw, r.deg, r.volt, r.temp, r.load, r.current_ma, r.current_raw, r.speed_raw, r.responding, r.torque, r.fresh, r.dir, r.offset, r.p, r.d, r.status,
    r.p_run, r.d_run, r.limit, r.gains_ok, c.asked, c.ok, c.missed, c.streak, c.max_streak, c.alarm,
    r.age_ms, p.open, p.selected, p.follow].join('|'); };
class RowPure extends Component {
  shouldComponentUpdate(next) { return rowSig(next) !== rowSig(this.props) || next.op !== this.props.op; }
  render(props) { return html`<${Row} ...${props} />`; }
}

function ServoList({ sv, open, selected, onSelect, op, follow, setHint }) {
  const rows = sv.rows || [];
  return html`<section class="board sv-list">
    <div class="sv-row sv-headrow"><span></span><span>舵机</span><span>位置（拖动）</span><span class="r">读数</span><span class="r">角度</span>
      <span class="r">温度</span><span>扭矩</span><span></span><span></span></div>
    ${GROUPS.map(([g, label, range]) => {
      const gr = rows.filter((r) => r.group === g);
      const hold = gr.filter((r) => r.torque === 1).length;
      return html`<div class="sv-group">
        <div class="sv-grouphead"><span class=${'sv-gdot ' + g}></span>${label}<span class="sv-small">${range}</span>
          ${hold ? html`<span class="sv-hold-n">保持 ${hold}</span>` : null}</div>
        ${gr.map((r) => html`<${RowPure} key=${r.id} r=${r} open=${open} selected=${r.id === selected} onSelect=${onSelect}
          op=${op} follow=${follow} setHint=${setHint} />`)}
      </div>`;
    })}
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
function Toolbar({ open, op, link, motion, setMotion, idle, setIdle, follow, setFollow, poses, setHint, syncSliders, main }) {
  const ms = { seconds: motion.seconds, mode: motion.mode, acc: motion.acc };
  // 存的姿态里名字带这个词的（实机调好的）优先；站姿没有就用官方站姿
  const named = (word) => poses.find((p) => p.name.includes(word));
  const goNamed = (word, fallbackKey) => {
    const p = named(word);
    if (p) op({ op: 'go_pose', file: p.file, ...ms });
    else if (fallbackKey) op({ op: 'official', key: fallbackKey, ...ms });
    else setHint(`还没有叫「${word}」的姿态：先摆好，在「更多」里存一个叫「${word}」的`);
  };
  return html`<div class="sv-toolbar">
    <button class="btn-ok" disabled=${!open} onClick=${() => op({ op: 'torque', on: 1 })} title="先对准当前位置再上力，不会冲">全部扭矩开</button>
    <button class="btn-danger" disabled=${!open} onClick=${(e) => { e.currentTarget.blur(); link.cmd('relax', {}, 7000); }}
      title="这一页的急停（空格键也是）：15 颗全部卸力并回读确认；卸完后台不会再自动上力，自己点「全部扭矩开」或「保持」就恢复">全部扭矩关</button>
    <button class="btn-light" onClick=${syncSliders}>滑块对齐当前位置</button>
    <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'official', key: 'zero', ...ms })}
      title="零位 = 腿伸直、脚板垂直、头平视、嘴闭上，官方定义这时每颗都是 2048">全部回零位（2048）</button>
    <button class="btn-light" disabled=${!open} onClick=${() => goNamed('站姿', 'stand')}
      title="存的姿态里名字带「站姿」的（实机调好的）；没有才用官方站姿">站姿</button>
    <button class="btn-light" disabled=${!open} onClick=${() => goNamed('缩起来', null)} title="存的姿态里名字带「缩起来」的">缩起来</button>
    <button class="btn-light" disabled=${!open} onClick=${async () => { const r = await op({ op: 'idle', on: !idle }); if (r.ok) setIdle(!idle); }}
      title="头部随机小动作：歪头、摇头、点头、张嘴、左右看，只动 30–34">${idle ? '⏹ 停灵动' : '🦆 灵动'}</button>
    <button class="btn-light" disabled=${!open} onClick=${() => op({ op: 'scan', ids: '20-24,30-34,10-14' })}>重新扫描</button>
    <button class="btn-stop" disabled=${!open} onClick=${() => op({ op: 'stop' })} title="打断正在走的过渡和灵动，停在原地">停</button>
    <span class="sv-inline">用时 <${Stepper} value=${motion.seconds} min=${0} max=${30} step=${0.5}
      onChange=${(v) => setMotion({ ...motion, seconds: v === '' ? 0 : v })} unit="秒" aria-label="用时" /></span>
    <label class="sv-inline"><input type="checkbox" checked=${follow} onChange=${(e) => setFollow(e.target.checked)} /> 跟随拖动</label>
    ${main}
  </div>`;
}

function CalibCard({ open, op, selected, onSelect, rows }) {
  const [ids, setIds] = useState('20-24,30-34,10-14');
  return html`<section class="board sv-card sv-calib">
    <div class="sv-form">
      <span class="sv-label">单颗中位</span>
      <div class="sv-ctrl">
        <${ServoPick} selected=${selected} onSelect=${onSelect} rows=${rows} />
        <button class="btn-dark" disabled=${!open || !selected} onClick=${() => calibHandler && calibHandler(String(selected))}>中位校准这一颗</button>
        <span class="sv-small">也可以点每行的「中位」</span>
      </div>
      <span class="sv-label">一键校准</span>
      <div class="sv-ctrl">
        <input type="text" value=${ids} onInput=${(e) => setIds(e.target.value)} title="校准哪些舵机" class="sv-ids" />
        <button class="btn-dark" disabled=${!open} onClick=${() => calibHandler && calibHandler(ids)}>一键校准</button>
        <button class="btn-light" disabled=${!open} onClick=${() => { if (window.confirm('撤销上一次校准：把那次备份的偏移写回去？')) op({ op: 'undo' }, 30000); }}>撤销上一次</button>
      </div>
      <span></span><p class="sv-small">把鸭子摆成官方零位（腿伸直、头摆正、嘴闭上）再点：现在的位置设成 2048。先出预览，确认了才写；校完自动保持住。</p>
    </div>
  </section>`;
}

// ---------------- 更多（折叠） ----------------

function MoreArea({ open, op, motion, setMotion, follow, setFollow, poses, setPoses, selected, onSelect, sv, dirs, link }) {
  const [name, setName] = useState('');
  const ms = { seconds: motion.seconds, mode: motion.mode, acc: motion.acc };
  return html`<details class="board sv-more-area">
    <summary>更多：运动方式、存的姿态、高级工具、日志</summary>
    <div class="sv-more-grid">
      <section class="sv-form">
        <span class="sv-label">官方姿势</span>
        <div class="sv-ctrl">
          ${Object.entries(POSES).map(([k, p]) => html`<button class="btn-light" disabled=${!open} onClick=${() => {
            if (k === 'fold' && !window.confirm('去折叠：膝只折 88°（官方 90° 会顶到零件）。零点没校准的话别用：膝差几度就顶上零件了。继续？')) return;
            op({ op: 'official', key: k, ...ms });
          }}>去${p.label}</button>`)}
        </div>
        <span class="sv-label">运动方式</span>
        <div class="sv-ctrl"><select value=${motion.mode} onChange=${(e) => setMotion({ ...motion, mode: e.target.value })}>
          <option value="profile">平滑（舵机自己走梯形曲线）</option><option value="stream">流式（这边一步步发）</option></select>
          <span class="sv-inline">加速度 <${Stepper} value=${motion.acc} min=${1} max=${254}
            onChange=${(v) => setMotion({ ...motion, acc: v === '' ? 0 : v })} aria-label="加速度" /></span></div>
        <span class="sv-label">存的姿态</span>
        <div class="sv-ctrl sv-chips">
          ${poses.length ? poses.map((p) => html`<span class="sv-chip"><button disabled=${!open} onClick=${() => op({ op: 'go_pose', file: p.file, ...ms })} title=${p.time}>${p.name}</button>
            <button class="x" disabled=${!open} title="删除（挪进回收站，能找回）" onClick=${async () => { if (!window.confirm(`删除姿态「${p.name}」？`)) return; const r = await op({ op: 'delete_pose', file: p.file }); if (r.ok) setPoses(r.data.poses); }}>×</button></span>`)
            : html`<span class="sv-small">还没有</span>`}
        </div>
        <span></span>
        <div class="sv-ctrl"><input type="text" placeholder="名字，比如 缩起来" value=${name} onInput=${(e) => setName(e.target.value)} />
          <button class="btn-light" disabled=${!open || !name.trim()} onClick=${async () => { const r = await op({ op: 'save_pose', name }); if (r.ok) { setPoses(r.data.poses); setName(''); } }}>存当前位置</button></div>
      </section>
      <${Tools} open=${open} op=${op} selected=${selected} onSelect=${onSelect} sv=${sv} dirs=${dirs} />
    </div>
    <${LogBox} link=${link} />
  </details>`;
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
        if (DANGER[reg] && !window.confirm(`危险：${DANGER[reg]}\n确定把 ${sel} 的寄存器 ${reg} 写成 ${val}？`)) return;
        run({ op: 'write_reg', id: sel, addr: +reg, size, value: +val, unlock });
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
export function HandBackButton({ link }) {
  const [err, setErr] = useState(null);
  const confirm = async () => {
    setErr(null);
    const r = await link.cmd('servo.close', {}, 90000);     // 小助手起主程序要等启动四步（最多 60 秒）
    if (!r.ok) setErr(r.reason || r.error || '没交还成');
  };
  return html`<span class="sv-handback"><${TwoStep} label="交还主程序" ask=${HANDBACK_ASK} busyLabel="交还中…" onConfirm=${confirm} />${
    err ? html`<span class="sv-warn">${err}</span>` : null}</span>`;
}

export function ServoPage({ sv, link, leaveGuards, live = true, elapsed = 0 }) {
  const [view, setView] = useState('monitor');
  const [pendingOrder, setPendingOrder] = useState(null);
  const acquired = useRef(false);
  const [pose, setPose] = useState('live');
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [hint, setHint] = useState(null);
  const [motion, setMotion] = useState({ seconds: 2, mode: 'profile', acc: 30 });
  const [follow, setFollow] = useState(true);
  const [poses, setPoses] = useState([]);
  const [idle, setIdle] = useState(false);
  const [plan, setPlan] = useState(null);
  const [syncKey, setSyncKey] = useState(0);
  const open = sv?.state === 'open';
  // 滑块对齐当前位置：重建表格，所有滑块丢掉拖动中的值、回到实际读数（平时松手 0.6 秒也会自己回去）
  const syncSliders = useCallback(() => { setSyncKey((k) => k + 1); setHint('滑块已对齐当前位置'); }, []);
  const dirs = Object.fromEntries((sv?.rows || []).map((r) => [r.id, r.dir]));

  // 这几个函数要固定不变（useCallback）：每行按「数据变没变」决定重不重画，函数一变就白比了
  const onSelect = useCallback((id) => setSelected(id), []);
  // 舵机操作：不成就在页头显示原因；quiet = 滑块这种高频的，成功了不打扰
  const op = useCallback(async (args, timeout = 15000, quiet = false) => {
    const r = await link.cmd('servo.op', args, timeout);
    if (!r.ok) setMsg(r.reason || r.error || '没成功');
    else if (!quiet) setMsg(null);
    return r;
  }, []);
  const run = async (name, args = {}) => {
    setBusy(true); setMsg(null);
    const r = await link.cmd(name, args, 15000);
    if (name === 'servo.open' && r.ok) acquired.current = true;
    setBusy(false);
    if (!r.ok) setMsg(r.reason || r.error || '没成功');
  };
  // Monitoring is passive. UART ownership is acquired only by the explicit debug action.
  // Preserve an existing session; only a session opened here may be closed on exit if idle.
  const [ports, setPorts] = useState(null);       // {ports, port, sim}：电脑上插 USB 转 TTL 时选串口（跟 servo-web 一样）
  const [pick, setPick] = useState('');
  const loadPorts = async () => {
    const r = await link.cmd('servo.ports', {}, 5000);
    if (r.ok) { setPorts(r.data); setPick((p) => p || (r.data.sim ? 'sim' : r.data.port || '')); }
    return r.ok ? r.data : null;
  };
  useEffect(() => {
    let alive = true;
    link.whenOpen().then(() => { if (alive) return loadPorts(); });
    return () => { alive = false; if (acquired.current) link.cmd('servo.close', { if_idle: true }, 15000); };
  }, []);
  calibHandler = useCallback(async (ids) => {
    const text = Array.isArray(ids) ? ids.join(',') : String(ids);
    const r = await op({ op: 'calib_plan', ids: text });
    if (r.ok) setPlan({ ...r.data, ids: text });
  }, []);
  useEffect(() => { if (open && view === 'debug') op({ op: 'poses' }).then((r) => r.ok && setPoses(r.data.poses)); else setIdle(false); }, [open, view]);
  useEffect(() => { if (!hint) return undefined; const t = setTimeout(() => setHint(null), 4000); return () => clearTimeout(t); }, [hint]);

  const dr = sv?.drop_rate;
  const order = sv?.communication?.read_order;
  useEffect(() => {
    if (pendingOrder && (order?.session !== pendingOrder.session || order?.generation === pendingOrder.generation)) setPendingOrder(null);
  }, [order?.session, order?.generation, pendingOrder]);
  const monitor = pendingOrder ? {...sv,
    communication:{...sv?.communication, read_order:pendingOrder},
    rows:(sv?.rows || []).map(r=>({...r,health:null}))} : sv;
  return html`<div class="sv-page">
    <header class="sv-head">
      <div class="sv-titles">
        <div class="sv-titleline">
          <h1>舵机</h1>
          ${sv?.rows && view === 'debug' ? html`<div class="sv-stats num">
            <div><b>${sv.online ?? '—'}<small>/${sv.total}</small></b><span>在线</span></div>
            <div><b>${fmt(sv.avg_volts, 1)}<small> V</small></b><span>平均电压</span></div>
            <div><b>${open ? fmt(sv.hz, 0) : '—'}<small> Hz</small></b><span>刷新</span></div>
            <div><b>${open && dr != null ? (dr * 100).toFixed(1) : '—'}<small> %</small></b><span>丢帧</span></div>
            <div><b>${sv.holding ?? '—'}</b><span>保持</span></div>
          </div>` : null}
        </div>
        <div class="sv-sub">${open
          ? html`<b class="sv-live">调舵机中</b> · ${sv.port} · 主程序停着，手柄不能用${sv.latched ? ' · 已全部卸力' : ''} · 离开这页舵机照样保持；点「交还主程序」才交还
            <${HandBackButton} link=${link} />`
          : busy ? '正在接管舵机总线…'
            : html`没接管 · 只读${sv?.diagnostic ? ' · 主程序启动诊断 · 原始反馈' : ''}`}
        ${view === 'debug' ? html`<span class="sv-port">串口
          <select value=${pick} onFocus=${loadPorts} onChange=${(e) => setPick(e.target.value)} disabled=${open}
            title=${open ? '调舵机中不能换串口：先交还主程序' : ''}>
            <option value="">选串口…</option>
            ${(ports?.ports || []).map((p) => html`<option value=${p.device}>${p.device}${p.desc ? ` · ${p.desc}` : ''}</option>`)}
            ${ports?.port && !(ports.ports || []).some((p) => p.device === ports.port) ? html`<option value=${ports.port}>${ports.port}</option>` : null}
            <option value="sim">演示（没接舵机，先看看页面）</option>
          </select>
          <button class="btn-light" disabled=${open || busy || !pick} onClick=${() => run('servo.open', { port: pick })}
            title=${open ? '调舵机中不能换串口：先交还主程序' : '由 Studio 接管总线，主程序会停止'}>${open ? '换这个' : '开始调舵机'}</button>
        </span>` : null}</div>
        ${open && sv.gains ? html`<${GainsLine} g=${sv.gains} />` : null}
      </div>
      ${view === 'debug' ? html`<div class="sv-actions">
        <${Toolbar} open=${open} op=${op} link=${link} motion=${motion} setMotion=${setMotion} idle=${idle} setIdle=${setIdle}
          follow=${follow} setFollow=${setFollow} poses=${poses} setHint=${setHint} syncSliders=${syncSliders}
          main=${null} />
      </div>` : null}
    </header>
    <div class="comm-view-tabs" role="tablist" aria-label="舵机页面模式">
      <button role="tab" aria-selected=${view==='monitor'} onClick=${()=>setView('monitor')}>实时状态</button>
      <button role="tab" aria-selected=${view==='debug'} onClick=${()=>setView('debug')}>调舵机 / 标定</button>
    </div>
    ${msg || sv?.error ? html`<div class="banner bad" role="alert"><b>${msg || '打不开舵机总线'}</b><span class="more">${msg ? '' : sv?.error || ''}</span>
      <a href="#" onClick=${(e) => { e.preventDefault(); setMsg(null); }}>知道了</a></div>` : null}
    ${sv?.marker_error ? html`<div class="banner warn" role="status"><b>会话标记没写进去</b>
      <span class="more">${sv.marker_error}</span></div>` : null}
    ${hint ? html`<div class="banner warn" role="status"><span>${hint}</span></div>` : null}
    ${view==='monitor' ? html`<div class="comm-monitor-grid">
      <${ServoMonitor} sv=${monitor} selected=${selected} onSelect=${onSelect} live=${live} elapsed=${elapsed} />
      <aside class="comm-monitor-aside">
        <${ServoStage} sv=${sv} pose=${pose} setPose=${setPose} selected=${selected} onSelect=${onSelect} compact=${true} />
        <${ServoInspector} sv=${monitor} selected=${selected} live=${live} elapsed=${elapsed} />
        <${ReadOrderPanel} communication=${monitor?.communication} rows=${monitor?.rows} link=${link} live=${live && elapsed<1500}
          onApplied=${setPendingOrder} />
      </aside></div>` : html`<div class="sv-main">
      <div class="sv-left">
        <${ServoStage} sv=${sv} pose=${pose} setPose=${setPose} selected=${selected} onSelect=${onSelect} />
        ${pose === 'zero' ? html`<${Why} />` : null}
      </div>
      <div class="sv-right">
        <${CalibCard} open=${open} op=${op} selected=${selected} onSelect=${onSelect} rows=${sv?.rows} />
        <${ServoList} key=${'list' + syncKey} sv=${sv || { rows: [] }} open=${open} selected=${selected} onSelect=${onSelect} op=${op} follow=${follow} setHint=${setHint} />
      </div>
    </div>`}
    ${view==='debug' ? html`<${MoreArea} open=${open} op=${op} motion=${motion} setMotion=${setMotion} follow=${follow} setFollow=${setFollow}
      poses=${poses} setPoses=${setPoses} selected=${selected} onSelect=${onSelect} sv=${sv || { rows: [] }} dirs=${dirs} link=${link} />`:null}
    ${plan ? html`<${CalibDialog} plan=${plan} onCancel=${() => setPlan(null)} onOk=${async () => {
      const ids = plan.ids; setPlan(null);
      const r = await op({ op: 'calibrate', ids }, 60000);
      if (r.ok) {
        const d = r.data, bad = Object.keys(d.bad || {});
        setHint(`校准完：成功 ${d.ok.length} 颗${bad.length ? `，失败 ${bad.join('、')}（看日志）` : ''}；成功的已经保持住`);
      }
    }} />` : null}
  </div>`;
}
