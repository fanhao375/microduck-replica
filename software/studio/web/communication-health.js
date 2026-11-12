// Read-only views of owned snapshots. No serial reads, calibration writes or motion.
import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';

const number = v => typeof v === 'number' && Number.isFinite(v);
const fmt = (v, d = 0) => number(v) ? v.toFixed(d) : '—';
const count = v => Number.isSafeInteger(v) && v >= 0 ? v.toLocaleString('zh-CN', {useGrouping:false}) : '—';
const ageText = age => !number(age) ? '更新时间未知' : age < 1000 ? `${Math.round(age)} ms 前` : `${(age / 1000).toFixed(1)} 秒前`;
const groups = [['L','左腿','20–24'],['R','右腿','10–14'],['H','头颈','30–34']];
const sourceText = source => ({robotd:'主程序回读',bus:'调试会话回读',sim:'模拟数据'}[source] || '来源待确认');

function rowStatus(row, live, elapsed) {
  const age = number(row.age_ms) ? row.age_ms + elapsed : null;
  if (!live || elapsed >= 1500 || number(age) && age >= 1500) return {kind:'stale',text:'数据过期',age};
  if (!row.fresh) return row.responding === false ? {kind:'bad',text:'未应答',age} : {kind:'neutral',text:'等待新数据',age};
  if (row.comm?.streak > 0) return {kind:row.comm.streak >= 3?'bad':'warn',text:`连续缺 ${row.comm.streak} 次`,age};
  if (number(row.health?.rate) && row.health.rate > .01) return {kind:'warn',text:'近期有缺回包',age};
  if (!number(row.comm?.asked)) return {kind:'neutral',text:'有应答 · 统计未知',age};
  return {kind:'ok',text:row.responding === true && !number(row.comm?.asked) ? '有应答' : '数据正常',age};
}

function Rate({ health }) {
  const known = number(health?.rate) && health.rate >= 0 && health.rate <= 1 && health.asked > 0;
  return html`<span class="comm-rate"><b>${known ? `${(health.rate * 100).toFixed(1)}%` : '—'}</b>
    <small>${known ? `${count(health.missed)} / ${count(health.asked)}` : '暂无有效统计'}</small></span>`;
}

function Parameter({label, value, suffix = ''}) {
  return html`<div><dt>${label}</dt><dd>${value == null ? '未提供' : `${value}${suffix}`}</dd></div>`;
}

function ServoDetail({row, status}) {
  const c = row.comm || {}, h = row.health || {}, p = row.parameters || {};
  const pdi = value => Array.isArray(value) && value.length===3 && value.every(number) ? value.join(' / ') : null;
  const source = ({robotd_before_arm_cache:'主程序上力前快照',studio_cached_inspection:'调试会话寄存器回读'})[p.source] || '已提供的回读';
  return html`<div class="sv-monitor-detail" id=${`servo-detail-${row.id}`} data-id=${row.id}>
    <div class="comm-detail-head"><b>${row.id} · ${row.cn}</b><span>最新反馈 ${ageText(status.age)}</span></div>
    <dl class="comm-values">
      <${Parameter} label="累计请求" value=${number(c.asked)?count(c.asked):null} />
      <${Parameter} label="累计应答" value=${number(c.ok)?count(c.ok):null} />
      <${Parameter} label="累计缺回包" value=${number(c.missed)?count(c.missed):null} />
      <${Parameter} label="连续缺回包" value=${number(c.streak)?count(c.streak):null} />
      <${Parameter} label="最长连续缺回包" value=${number(c.max_streak)?count(c.max_streak):null} />
      <${Parameter} label="窗口覆盖" value=${number(h.observed_s)?fmt(h.observed_s,1):null} suffix=" / 60 秒" />
      <${Parameter} label="负载原始值" value=${row.load} />
      <${Parameter} label="电流" value=${number(row.current_ma)?fmt(row.current_ma):null} suffix=" mA" />
      <${Parameter} label="速度原始值" value=${row.speed_raw} />
      <${Parameter} label="关节角速度" value=${number(row.velocity_rad_s)?fmt(row.velocity_rad_s,3):null} suffix=" rad/s" />
    </dl>
    <details class="comm-parameters"><summary>参数与标定<span>配置、回读与缓存</span></summary>
    <dl class="comm-values">
      <${Parameter} label="运行 P / D" value=${row.p_run == null || row.d_run == null ? null : `${row.p_run} / ${row.d_run}`} />
      <${Parameter} label="运行限力" value=${row.limit} />
      <${Parameter} label="存储 P / D" value=${row.p == null || row.d == null ? null : `${row.p} / ${row.d}`} />
      ${pdi(p.runtime?.pdi) ? html`<${Parameter} label=${p.runtime?.observed===true?'运行 P / D / I':'上力前 RAM P / D / I（缓存）'} value=${pdi(p.runtime.pdi)} />`:null}
      ${pdi(p.eeprom?.pdi) ? html`<${Parameter} label="存储 P / D / I（缓存）" value=${pdi(p.eeprom.pdi)} />`:null}
      ${number(p.before_arm?.acceleration_raw) ? html`<${Parameter} label="上力前加速度参数" value=${p.before_arm.acceleration_raw} />`:null}
      ${p.configured ? html`<${Parameter} label="配置 P / D" value=${number(p.configured.kp)&&number(p.configured.kd)?`${p.configured.kp} / ${p.configured.kd}`:null} />
        <${Parameter} label="配置限力" value=${p.configured.torque_limit} />`:null}
      <${Parameter} label="零位偏移" value=${row.offset} />
      <${Parameter} label="模型方向" value=${row.dir} />
      <${Parameter} label="角度下限" value=${row.min_angle ?? p.min_angle} />
      <${Parameter} label="角度上限" value=${row.max_angle ?? p.max_angle} />
    </dl>
    <p class="comm-caption">参数来源：${source} · ${number(p.age_ms) ? ageText(p.age_ms) : '参数读取时间未提供'}。
      固定参数是已读取的快照，不随网页刷新重读；未提供的值不会补成默认值。</p>
    </details>
  </div>`;
}

export function ServoMonitor({sv, selected, onSelect, live = true, elapsed = 0}) {
  const c = sv?.communication || {};
  const rows = sv?.rows || [];
  const strip=useRef(null), [ends,setEnds]=useState({before:false,after:false});
  const ordered=groups.flatMap(([group,label])=>rows.filter(r=>r.group===group).map(row=>({row,group,label})));
  const picked=rows.find(r=>r.id===selected);
  useEffect(()=>{
    const node=strip.current;
    const measure=()=>{const before=node.scrollLeft>2,after=node.scrollLeft+node.clientWidth<node.scrollWidth-2;
      setEnds(prev=>prev.before===before&&prev.after===after?prev:{before,after});};
    measure();node.addEventListener('scroll',measure,{passive:true});
    const observer=typeof ResizeObserver==='function'?new ResizeObserver(measure):null;
    observer?.observe(node);window.addEventListener('resize',measure);
    return()=>{node.removeEventListener('scroll',measure);observer?.disconnect();window.removeEventListener('resize',measure);};
  },[rows.length]);
  useEffect(()=>{
    const node=strip.current,card=node?.querySelector(`[data-id="${selected}"]`);
    if(!card)return;
    const box=node.getBoundingClientRect(),r=card.getBoundingClientRect();
    if(r.left<box.left || r.right>box.right)node.scrollLeft+=r.left-box.left-(node.clientWidth-r.width)/2;
  },[selected,rows.length]);
  const slide=direction=>strip.current?.scrollBy({left:direction*Math.max(160,strip.current.clientWidth*.8),
    behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches?'auto':'smooth'});
  const statuses = rows.map(row => ({row,status:rowStatus(row,live,elapsed)}));
  const valid = statuses.filter(({row,status}) => status.kind !== 'stale' && row.fresh).map(({row}) => row);
  const feedbackKnown = valid.length > 0 || statuses.some(({row,status}) => status.kind === 'bad' && row.responding === false);
  const hottest = valid.filter(r => number(r.temp)).sort((a,b)=>b.temp-a.temp)[0];
  const lowest = valid.filter(r => number(r.volt)).sort((a,b)=>a.volt-b.volt)[0];
  const attention = statuses.filter(({status}) => ['bad','warn'].includes(status.kind));
  return html`<section class="board sv-monitor" aria-label="舵机实时状态">
    <div class="comm-monitor-head"><div><h2>实时状态</h2><p>${sv?.sim ? '模拟数据' : sourceText(c.source || sv?.source)} · 只读查看</p></div>
      <span class=${'comm-badge '+(!live || elapsed>=1500 ? 'stale':'neutral')}>${!live?'连接已断开':elapsed>=1500?'等待新数据':ageText(number(c.age_ms)?c.age_ms+elapsed:null)}</span></div>
    <div class="comm-summary">
      <div><span>在线反馈</span><b>${feedbackKnown ? valid.length : '—'}<small> / ${sv?.total ?? 15}</small></b></div>
      <div><span>最高温度</span><b>${fmt(hottest?.temp)}<small> °C${hottest ? ` · #${hottest.id}`:''}</small></b></div>
      <div><span>最低电压</span><b>${fmt(lowest?.volt,1)}<small> V${lowest ? ` · #${lowest.id}`:''}</small></b></div>
      <div><span>有效读取更新</span><b>${live && elapsed<1500 ? fmt(c.sample_hz,1):'—'}<small> Hz</small></b></div>
    </div>
    ${attention.length ? html`<div class="comm-attention" role="status"><b>${attention.length} 颗需关注</b>
      ${attention.map(x=>html`<button type="button" onClick=${()=>onSelect(x.row.id)}>#${x.row.id} ${x.row.cn} · ${x.status.text}</button>`)}</div>`:null}
    <div class="comm-strip-heading"><span>${rows.length} 颗舵机 · ${ends.before||ends.after?'左右滑动查看':'全部显示'}</span>${ends.before||ends.after ? html`<span class="comm-strip-arrows">
      <button type="button" aria-label="向左查看舵机" aria-controls="servo-card-strip" disabled=${!ends.before} onClick=${()=>slide(-1)}>‹</button>
      <button type="button" aria-label="向右查看舵机" aria-controls="servo-card-strip" data-servo-next disabled=${!ends.after} onClick=${()=>slide(1)}>›</button>
    </span>`:null}</div>
    <div class="comm-strip-wrap"><div class="comm-servo-strip" id="servo-card-strip" ref=${strip} role="region" aria-label="舵机卡片，左右滑动查看" tabindex="0">
      ${ordered.map(({row,group,label})=>{
        const status=rowStatus(row,live,elapsed);
        return html`<button type="button" key=${row.id} class=${'sv-monitor-row comm-servo-card '+status.kind+(selected===row.id?' selected':'')} data-id=${row.id}
          aria-expanded=${selected===row.id} aria-controls=${`servo-detail-${row.id}`} onClick=${()=>onSelect(selected===row.id?null:row.id)}>
          <span class="comm-card-heading"><span class=${'sv-id '+group}>${row.id}</span><b>${row.cn}</b><small>${label}</small></span>
          <span class="comm-card-metrics">
            <span><small>温度</small><b>${fmt(row.temp)}<em> °C</em></b></span>
            <span><small>电压</small><b>${fmt(row.volt,1)}<em> V</em></b></span>
            <span><small>角度</small><b>${fmt(row.deg,1)}<em>°</em></b></span>
            <span title="近60秒缺回包率"><small>缺回包</small><${Rate} health=${row.health} /></span>
          </span>
          <span class="comm-card-tail"><span class=${'comm-badge '+status.kind}>${status.text}</span><span>${selected===row.id?'收起 ▴':'详情 ▾'}</span></span>
        </button>`;
      })}</div></div>
    ${picked ? html`<div class="comm-strip-detail"><${ServoDetail} row=${picked} status=${rowStatus(picked,live,elapsed)} /></div>`:null}
    ${!rows.length ? html`<p class="comm-empty">等待舵机回读。主程序、调试会话和每颗舵机的状态分别显示。</p>` : null}
    <div class="comm-footer"><span>全总线坏校验 <b>${count(c.counters?.bad_checksum ?? c.counters?.bad_checksums)}</b></span>
      <span>读取失败 <b>${count(c.counters?.failed_reads)}</b></span><span>超时 <b>${count(c.counters?.timeout)}</b></span></div>
    <p class="comm-caption">缺回包率 = 窗口内未获可用应答 / 实际发出的读取请求；不足60秒时按已覆盖时段统计。全总线校验错误不归到某一颗舵机。</p>
  </section>`;
}

export function ReadOrderPanel({communication, link, rows, live = true, onApplied}) {
  const actual = communication?.read_order, guard = communication?.read_order_guard;
  const [mode,setMode] = useState(actual?.mode || 'default');
  const [lastId,setLastId] = useState(actual?.last_id ?? 13);
  const [busy,setBusy] = useState(false), [message,setMessage] = useState(null);
  const context = JSON.stringify([communication?.session, actual?.generation, actual?.mode, actual?.last_id]);
  useEffect(()=>{setMode(actual?.mode || 'default');setLastId(actual?.last_id ?? 13);setMessage(null);},[context]);
  const allowed = live && !!actual && guard?.allowed === true;
  const modes = {default:'原顺序',id_ascending:'ID 升序',id_last:`#${actual?.last_id ?? '—'} 最后读取`};
  const apply = async target => {
    if (!allowed || busy) return;
    setBusy(true);setMessage(null);
    try {
      const args = {mode:target}; if(target==='id_last')args.last_id=Number(lastId);
      const response=await link.cmd('servo.read_order',args,15000);
      if(!response.ok || response.data?.applied !== true || !response.data?.read_order) {
        setMessage({bad:true,text:response.reason || response.error || '未确认顺序已生效，保留当前配置'});
      } else {
        onApplied?.(response.data.read_order);
        setMessage({bad:false,text:`已回读确认应用${response.data.saved===true?'并保存':''}；从新窗口开始统计`});
      }
    } catch(error) {setMessage({bad:true,text:`未收到应用确认：${error.message || error}`});}
    finally {setBusy(false);}
  };
  return html`<details class="board comm-order comm-advanced" aria-label="通信读取顺序对照">
    <summary><span><b>高级诊断</b><small>读取顺序对照</small></span><span class="comm-disclosure">展开</span></summary>
    <div class="comm-monitor-head"><div><h2>读取顺序对照</h2><p>调整遥测请求顺序，回包仍按舵机 ID 对应。</p></div>
      <span class="comm-badge neutral">当前：${actual ? modes[actual.mode] || '待确认':'未提供'}</span></div>
    <div class="comm-order-controls">
      <label>对照方案<select aria-label="通信读取方案" value=${mode} disabled=${busy} onChange=${e=>setMode(e.target.value)}>
        <option value="default">原顺序</option><option value="id_ascending">ID 升序</option><option value="id_last">指定舵机最后读取</option></select></label>
      ${mode==='id_last' ? html`<label>最后读取<select aria-label="最后读取的舵机" value=${lastId} disabled=${busy} onChange=${e=>setLastId(Number(e.target.value))}>
        ${(rows || []).map(r=>html`<option value=${r.id}>#${r.id} ${r.cn}</option>`)}</select></label>`:null}
      <button type="button" class="btn-dark" data-read-order-apply disabled=${!allowed||busy} onClick=${()=>apply(mode)}>${busy?'正在应用…':'应用读取顺序'}</button>
      <button type="button" class="btn-light" disabled=${!allowed||busy||actual?.mode==='default'} onClick=${()=>apply('default')}>恢复原顺序</button>
    </div>
    <p class="comm-order-ids">实际舵机读取顺序：${actual?.ids?.join(' → ') || actual?.servo_ids?.join(' → ') || '等待主程序提供'}</p>
    ${!allowed ? html`<p class="comm-caption">${!live?'连接已断开，等待新回读':guard?.reason || '等待确认模型停止、全部卸力及参数恢复'}</p>`:null}
    ${message ? html`<p class=${'comm-result '+(message.bad?'bad':'ok')} role="status">${message.text}</p>`:null}
  </details>`;
}

export function ImuHealth({imu, live = true, elapsed = 0}) {
  const h=imu?.health || {}, meta=imu?.metadata || {};
  const age=number(h.age_ms)?h.age_ms+elapsed:null;
  const fresh=live && elapsed<1500 && h.fresh===true && (!number(age)||age<1500);
  const valuesAge=number(imu?.values_age_ms)?imu.values_age_ms+elapsed:null;
  const valuesFresh=fresh && number(valuesAge) && valuesAge<1500;
  const vector=(value,d=4)=>Array.isArray(value)&&value.every(number)?value.map(v=>v.toFixed(d)).join(' / '):null;
  const board={imu_to_dxl:'IMU-to-DXL',ft6:'FT6',qinmo:'Qinmo'}[meta.board] || meta.board;
  const source={bus:'总线 ID200',lsm6dsv16x:'I²C · LSM6DSV16X',bno08x:'I²C · BNO08x'}[h.source || imu?.source] || h.source || '来源待确认';
  return html`<section class=${'board imu-card imu-health'+(fresh?'':' stale')} aria-label="IMU 实时状态">
    <div class="comm-monitor-head"><div><h3>IMU 实时状态</h3><p>${source}</p></div>
      <span class=${'comm-badge '+(fresh?'ok':'stale')}>${!live?'连接已断开':fresh?'数据正常':h.reason || '等待新数据 / 数据过期'}</span></div>
    <dl class="comm-values">
      <${Parameter} label="芯片" value=${meta.chip} /><${Parameter} label="板卡" value=${board} />
      <${Parameter} label="协议" value=${meta.protocol} /><${Parameter} label="固件身份" value=${meta.firmware != null && typeof meta.firmware==='object'?JSON.stringify(meta.firmware):meta.firmware} />
      <${Parameter} label=${h.rate_basis==='accepted_snapshot_timestamp'?'有效快照更新':h.rate_basis==='accepted_host_read'?'有效接收更新':'有效数据更新'} value=${fresh && number(h.update_hz)?fmt(h.update_hz,1):null} suffix=" Hz" />
      <${Parameter} label="数据年龄" value=${number(age)?ageText(age):null} />
      ${number(h.sensor_age_upper_bound_ms)?html`<${Parameter} label="传感器数据年龄上界" value=${fmt(h.sensor_age_upper_bound_ms+elapsed)} suffix=" ms" />`:null}
    </dl>
    ${h.last_error ? html`<p class="comm-last-error"><b>最近错误</b><span>${h.last_error}</span><small>历史记录；当前状态以上方实时状态为准。</small></p>`:null}
    <div class=${'comm-sensor-values'+(valuesFresh?'':' stale')}><p class="comm-values-note">${valuesFresh?`姿态数值 · ${ageText(valuesAge)}`:`姿态数值${number(valuesAge)?'已过期':'更新时间未知'} · ${ageText(valuesAge)}`}</p>
    <dl class="comm-values">
      <${Parameter} label="角速度 X / Y / Z" value=${vector(imu?.gyro,3)} suffix=" rad/s" />
      <${Parameter} label="四元数 W / X / Y / Z" value=${vector(imu?.quaternion_wxyz)} />
      <${Parameter} label="加速度 X / Y / Z" value=${Array.isArray(imu?.accel_m_s2)?vector(imu.accel_m_s2,3):null} suffix=" m/s²" />
      <${Parameter} label="传感器温度" value=${number(imu?.temperature_c)?fmt(imu.temperature_c,1):null} suffix=" °C" />
    </dl>
    </div>
    <div class="comm-footer">${Object.entries(h.counters || {}).filter(([,v])=>number(v)).map(([key,value])=>html`<span>${({samples:'累计样本',dropped:'丢弃样本',invalid:'无效样本',errors:'读取错误',stale:'过期样本',rejected:'拒收样本',timeouts:'超时',bad_checksums:'坏校验'})[key] || key} <b>${count(value)}</b></span>`)}</div>
    <p class="comm-caption">${['accepted_snapshot_timestamp','accepted_host_read'].includes(h.rate_basis)?'更新率是主程序接收的有效数据频率，传感器内部采样率未提供。':'更新率按实际新数据统计，和网页刷新次数不同。'}${['accepted_snapshot','host_accepted_sample'].includes(h.age_basis)?' 数据年龄按主程序接收时间计算。':h.age_basis==='sensor_reported_plus_host_age'?' 数据年龄包含传感器报告值和主机接收后的等待时间。':''}未提供的加速度、温度等信息不作推算。这里不直接把大幅姿态变化判断为漂移。</p>
  </section>`;
}

export const _test={rowStatus,ageText};
