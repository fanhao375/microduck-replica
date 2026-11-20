import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';

const JOINTS = [[20,'左髋偏航'],[21,'左髋横滚'],[22,'左髋俯仰'],[23,'左膝'],[24,'左踝'],
  [10,'右髋偏航'],[11,'右髋横滚'],[12,'右髋俯仰'],[13,'右膝'],[14,'右踝']];
const nameOf = id => JOINTS.find(j => j[0] === id)?.[1] || String(id);
const num = (value, digits=2) => Number.isFinite(value) ? value.toFixed(digits) : '—';
const css = document.createElement('style');
css.textContent = `
.lt{padding:20px;margin:12px 0;background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:14px}
.lt h2{font-size:19px;margin:0 0 6px}.lt p{font-size:13px;line-height:1.65;color:var(--text-2);margin:6px 0 12px}
.lt-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:24px}.lt fieldset{border:0;padding:0;margin:0 0 12px;min-width:0}
.lt legend{font-size:13px;font-weight:600;margin-bottom:8px}.lt-joints{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.lt label{font-size:13px;display:flex;gap:6px;align-items:center}.lt select,.lt input[type=number]{font:inherit;min-height:34px;border:1px solid var(--line,#ccc);border-radius:6px;max-width:100%;padding:3px 7px}
.lt-params{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.lt-params label{flex-direction:column;align-items:start}.lt-params input{width:70px}
.lt-pick-actions{display:flex;gap:8px;margin:0 0 9px}.lt-pick-actions button{font-size:12px;padding:5px 9px}
.lt-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.lt button{font:inherit;border:0;border-radius:8px;padding:9px 12px;cursor:pointer;background:#e9edf2;color:var(--text)}
.lt button.primary{background:#2f7cf6;color:white}.lt button.stop{background:#c33b40;color:white}.lt button:disabled{opacity:.45;cursor:default}
.lt :focus-visible{outline:3px solid #2f7cf6;outline-offset:2px}.lt-status{font-size:14px;font-weight:600;margin-bottom:10px}.lt-alert{color:#b43137;font-size:13px;overflow-wrap:anywhere}
.lt-chart{width:100%;height:auto;display:block;background:#f6f8fb;border-radius:8px}.lt-legend{font-size:12px;display:flex;gap:16px;margin:7px 0 12px}.lt-legend span:first-child{color:#2f7cf6}.lt-legend span:last-child{color:#c16a22}
.lt-results{overflow:auto;max-height:310px}.lt table{width:100%;font-size:12px;border-collapse:collapse;white-space:nowrap}.lt th,.lt td{padding:7px 9px;text-align:right;border-bottom:1px solid var(--line,#ddd)}
.lt th:first-child,.lt td:first-child{text-align:left}.lt-downloads{display:flex;gap:16px;margin-top:12px;font-size:13px}.lt small{font-size:12px;color:var(--text-2)}
@media(max-width:750px){.lt{padding:14px}.lt-layout{grid-template-columns:minmax(0,1fr);gap:16px}.lt-joints{grid-template-columns:repeat(2,minmax(0,1fr))}.lt-status{line-height:1.6}}
`;
document.head.appendChild(css);

export function comparable(a,b) {
  if (!a || !b || JSON.stringify(a.capture.plan.ids) !== JSON.stringify(b.capture.plan.ids)
      || a.capture.plan.amplitude_deg !== b.capture.plan.amplitude_deg
      || a.capture.plan.stages !== b.capture.plan.stages
      || a.capture.profile !== b.capture.profile || !!a.capture.sim !== !!b.capture.sim
      || a.capture.calibration_sha256 !== b.capture.calibration_sha256) return false;
  return a.snapshots.every(x => {
    const y=b.snapshots.find(j=>j.id===x.id);
    return y && Math.abs(x.center-y.center)<=2 && x.acceleration_raw===y.acceleration_raw
      && x.pdi[2]===y.pdi[2] && JSON.stringify(x.goal_block.slice(2))===JSON.stringify(y.goal_block.slice(2));
  });
}

function Plot({samples}) {
  if (!samples.length) return html`<svg class="lt-chart" viewBox="0 0 700 230" role="img" aria-label="等待关节跟踪数据">
    <text x="350" y="115" text-anchor="middle" fill="#687382" font-size="14">开始测试后，显示目标与反馈曲线</text></svg>`;
  const latest=samples.at(-1), rows=samples.filter(r=>r.stage===latest.stage && r.id===latest.id);
  const start=rows[0].t, span=Math.max(rows.at(-1).t-start,1);
  const bound=Math.max(2,...rows.flatMap(r=>[Math.abs(r.target),Math.abs(r.actual)]))*1.15;
  const path=key=>rows.map((r,i)=>`${i?'L':'M'}${40+(r.t-start)/span*640},${110-r[key]/bound*85}`).join(' ');
  return html`<svg class="lt-chart" viewBox="0 0 700 230" role="img" aria-label=${`${nameOf(latest.id)}目标与反馈角度，单位度`}>
    ${[-1,0,1].map(v=>html`<g><line x1="40" x2="680" y1=${110-v*85} y2=${110-v*85} stroke="#dce2e9"/>
      <text x="35" y=${114-v*85} text-anchor="end" fill="#687382" font-size="11">${num(v*bound,1)}°</text></g>`)}
    <path d=${path('target')} fill="none" stroke="#2f7cf6" stroke-width="2" stroke-dasharray="6 4"/>
    <path d=${path('actual')} fill="none" stroke="#c16a22" stroke-width="2"/>
    <text x="40" y="219" fill="#687382" font-size="12">${nameOf(latest.id)} · 当前阶段</text>
    <text x="680" y="219" text-anchor="end" fill="#687382" font-size="12">${num(span,1)} 秒</text>
  </svg>`;
}

export function LegTracking({link,open}) {
  const [state,setState]=useState(null), [ids,setIds]=useState([21]);
  const [amplitude,setAmplitude]=useState(3), [pairs,setPairs]=useState(false);
  const [supported,setSupported]=useState(false), [tune,setTune]=useState(false);
  const [kp,setKp]=useState(''), [kd,setKd]=useState('');
  const [error,setError]=useState(''), [pending,setPending]=useState(false);
  const [baseline,setBaseline]=useState(null), [baselineId,setBaselineId]=useState('');
  const [selectionNote,setSelectionNote]=useState('');
  const polling=useRef(false);
  const active=!!state?.active, report=state?.report;
  useEffect(()=>{
    let alive=true;
    const poll=async()=>{
      if(polling.current || link.status!=='open') return;
      polling.current=true;
      try{
        const r=await link.cmd('tracking.op',{op:'status'},2500);
        if(alive && r.ok) setState(r.data);
        else if(alive) setError(r.reason||r.error||'读取测试状态失败');
      }finally{polling.current=false;}
    };
    poll(); const timer=setInterval(poll,500);
    return()=>{alive=false;clearInterval(timer);link.cmd('tracking.op',{op:'stop'});};
  },[]);
  const start=async()=>{
    setError('');setPending(true);
    const r=await link.cmd('tracking.op',{op:'start',ids,amplitude_deg:Number(amplitude),pairs,
      supported,kp:tune && kp!==''?Number(kp):null,kd:tune && kd!==''?Number(kd):null});
    setPending(false);setSupported(false);
    if(!r.ok) setError(r.reason||r.error||'未能开始测试');
  };
  const stop=async()=>{
    const r=await link.cmd('tracking.op',{op:'stop'});
    if(!r.ok) setError(r.reason||r.error||'未能停止，请按卸力');
  };
  const loadBaseline=async id=>{
    setBaselineId(id);setBaseline(null);
    if(!id)return;
    const r=await link.cmd('tracking.op',{op:'load',run_id:id});
    if(r.ok)setBaseline(r.data);else setError(r.reason||r.error);
  };
  const canCompare=comparable(report,baseline) && !report?.issues.length && !baseline?.issues.length;
  const matching=s=>baseline?.stages.find(b=>b.id===s.id && b.stage===s.stage && b.kind===s.kind);
  const pairedCount=pairs?[0,1,2,3,4].filter(index=>ids.includes(20+index)&&ids.includes(10+index)).length:0;
  const approxMinutes=Math.ceil(28*(ids.length+pairedCount)/60);
  const selectIds=next=>{
    setIds(next);
    if(next.length!==1 && tune){
      setTune(false);setKp('');setKd('');
      setSelectionNote('已切回原参数测试：选多颗关节时会逐颗测试；临时改 P/D 只用于单关节对照。');
    }else setSelectionNote('');
  };
  return html`<section class="lt" aria-label="关节跟踪调试">
    <h2>关节跟踪调试</h2>
    <p>躯干架稳、双脚离地，用慢动作找出跟不上的关节。空载结果用于筛查，调整后还需扶稳站立与行走验证。</p>
    <div class="lt-layout"><div>
      <fieldset disabled=${active||pending}><legend>选择关节 · 已选 ${ids.length} 颗</legend>
        <div class="lt-pick-actions"><button type="button" onClick=${()=>selectIds(JOINTS.map(j=>j[0]))}>全选双腿</button>
          <button type="button" onClick=${()=>selectIds([])}>清空</button></div>
        <div class="lt-joints">
        ${JOINTS.map(([id,name])=>html`<label><input type="checkbox" checked=${ids.includes(id)}
          onChange=${e=>selectIds(e.target.checked?[...ids,id]:ids.filter(x=>x!==id))}/>${name} #${id}</label>`)}
      </div><div class="lt-params"><label>摆幅 ±°<input type="number" min="1" max="5" step="1" value=${amplitude} onInput=${e=>setAmplitude(e.target.value)}/></label></div>
      <p>多颗关节按顺序逐颗测试；勾选“左右同类关节反相测试”会追加双腿配合动作。</p>
      <p>预计约 ${approxMinutes} 分钟；首次建议只选一颗，确认动作和记录正常后再全选。</p>
      <label><input type="checkbox" checked=${pairs} onChange=${e=>setPairs(e.target.checked)}/>追加左右同类关节反相测试</label></fieldset>
      <fieldset disabled=${active||pending}><legend>测试参数</legend>
        <label><input type="checkbox" checked=${tune} disabled=${ids.length!==1} onChange=${e=>setTune(e.target.checked)}/>临时调整 P/D（选 1 颗关节时可用）</label>
        ${tune?html`<div class="lt-params"><label>P<input type="number" min="1" max="32" value=${kp} placeholder="保留" onInput=${e=>setKp(e.target.value)}/></label>
          <label>D<input type="number" min="0" max="64" value=${kd} placeholder="保留" onInput=${e=>setKd(e.target.value)}/></label></div>`:null}
        <p>首轮保留当前参数。每次只改一项，对照相同动作；结束恢复原 P/D。</p>
        ${selectionNote?html`<p role="status">${selectionNote}</p>`:null}
        <label><input type="checkbox" checked=${supported} onChange=${e=>setSupported(e.target.checked)}/>已架稳躯干，双腿及线材无遮挡</label>
      </fieldset>
      ${!open?html`<p>先在上方接管舵机总线，再将所有关节卸力。</p>`:null}
      <div class="lt-actions"><button class="primary" disabled=${!open||active||pending||!supported||!ids.length} onClick=${start}>${pending?'准备中…':'开始测试'}</button>
        <button class="stop" disabled=${!active&&!pending} onClick=${stop}>停止并卸力</button></div>
      <small>页面失联超过 2.5 秒自动停止。急停请用页面“卸力”按钮。</small>
    </div><div>
      <div class="lt-status" role="status">${state?.sim?'演示舵机 · ':''}${state?.message||'正在连接调试服务…'}${active && state?.ids?` · ${state.ids.map(nameOf).join('、')}`:''}</div>
      ${error?html`<div class="lt-alert" role="alert">${error}</div>`:null}
      ${state?.result?.failure?html`<details class="lt-alert"><summary>停止原因</summary>${state.result.failure}</details>`:null}
      ${state?.result?.cleanup_errors?.length?html`<div class="lt-alert" role="alert">卸力或参数恢复尚未确认，请保持支撑并检查连接。</div>`:null}
      <${Plot} samples=${state?.tail||[]}/><div class="lt-legend"><span>虚线：目标角</span><span>实线：反馈角（相对测试起点）</span></div>
      <label>对照记录<select value=${baselineId} onChange=${e=>loadBaseline(e.target.value)}><option value="">选择一次基线测试…</option>
        ${(state?.history||[]).map(h=>html`<option value=${h.run_id}>${new Date(h.at*1000).toLocaleString()} · ${h.plan.ids.map(nameOf).join('、')}${h.sim?' · 演示':''}</option>`)}</select></label>
      ${baseline&&!canCompare?html`<p>对照未就绪：请使用相同姿态、幅度、限速和标定的完整记录；异常或丢帧记录不计算差值。</p>`:null}
      ${report?html`<div class="lt-results"><table><thead><tr><th>关节 / 阶段</th><th>均方根误差 °</th><th>滞后 ms</th><th>停稳摆幅 °</th>${canCompare?html`<th>误差变化 °</th>`:null}</tr></thead>
        <tbody>${report.stages.map(s=>html`<tr><td>${nameOf(s.id)} · ${s.kind==='sine'?`${s.frequency_hz||'—'} Hz`:'慢移停稳'}</td><td>${num(s.rms_error_deg)}</td><td>${num(s.lag_ms,0)}</td><td>${num(s.hold_tail_range_deg)}</td>
          ${canCompare?html`<td>${matching(s)?num(s.rms_error_deg-matching(s).rms_error_deg):'—'}</td>`:null}</tr>`)}</tbody></table></div>`:null}
      ${report?.issues.length?html`<p class="lt-alert">这次记录存在中断、丢帧或恢复异常，统计仅供排查。请检查记录后复测。</p>`:null}
      ${state?.run_id&&!active?html`<div class="lt-downloads"><a href=${`/api/tracking/${state.run_id}/capture`}>下载原始信号</a>${report?html`<a href=${`/api/tracking/${state.run_id}/report`}>下载分析报告</a>`:null}</div>`:null}
    </div></div>
  </section>`;
}
