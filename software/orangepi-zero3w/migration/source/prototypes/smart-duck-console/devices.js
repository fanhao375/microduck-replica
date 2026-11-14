(() => {
  const P = window.P;
  const JOINTS = [[10,'右髋偏航'],[11,'右髋横滚'],[12,'右髋俯仰'],[13,'右膝'],[14,'右踝'],[20,'左髋偏航'],[21,'左髋横滚'],[22,'左髋俯仰'],[23,'左膝'],[24,'左踝'],[30,'颈部俯仰'],[31,'头部俯仰'],[32,'头部偏航'],[33,'头部横滚'],[34,'嘴部']];
  const LEGS = JOINTS.filter(([id]) => id < 30);
  const REGS = [[41,'加速度',1,'SRAM'],[46,'运行速度',2,'SRAM'],[48,'转矩限制',2,'SRAM'],[50,'运行 P',1,'SRAM'],[51,'运行 D',1,'SRAM'],[21,'位置环 P',1,'EEPROM'],[22,'位置环 D',1,'EEPROM'],[23,'位置环 I',1,'EEPROM'],[13,'温度上限',1,'EEPROM'],[19,'卸载保护',1,'EEPROM'],[28,'保护电流',2,'EEPROM'],[9,'最小角度限制',2,'EEPROM'],[11,'最大角度限制',2,'EEPROM'],[8,'应答级别',1,'EEPROM'],[33,'运行模式',1,'EEPROM'],[5,'设备 ID',1,'EEPROM'],[6,'波特率',1,'EEPROM']];
  // Match the original HD-1910 dump table; raw bytes without a supplied reading stay null.
  const DUMP_REGS = [[0,'固件主版本',1,'只读'],[1,'固件次版本',1,'只读'],[2,'END 字节序',1,'只读'],[3,'舵机主版本',1,'只读'],[4,'舵机次版本',1,'只读'],[5,'ID',1,'EEPROM'],[6,'波特率',1,'EEPROM'],[7,'预留地址',1,'EEPROM'],[8,'应答状态级别',1,'EEPROM'],[9,'最小角度限制',2,'EEPROM'],[11,'最大角度限制',2,'EEPROM'],[13,'最高温度上限',1,'EEPROM'],[14,'最高输入电压',1,'EEPROM'],[15,'最低输入电压',1,'EEPROM'],[16,'最大扭矩',2,'EEPROM'],[18,'相位',1,'EEPROM'],[19,'卸载条件',1,'EEPROM'],[20,'LED 报警条件',1,'EEPROM'],[21,'位置环 P',1,'EEPROM'],[22,'位置环 D',1,'EEPROM'],[23,'位置环 I',1,'EEPROM'],[24,'最小启动扭矩',1,'EEPROM'],[25,'积分限制值',1,'EEPROM'],[26,'正向不灵敏区',1,'EEPROM'],[27,'负向不灵敏区',1,'EEPROM'],[28,'保护电流',2,'EEPROM'],[30,'角度分辨率',1,'EEPROM'],[31,'位置偏移',2,'EEPROM'],[33,'运行模式',1,'EEPROM'],[34,'保持扭矩',1,'EEPROM'],[35,'保护时间',1,'EEPROM'],[36,'过载扭矩',1,'EEPROM'],[37,'速度环 P',1,'EEPROM'],[38,'过流保护时间',1,'EEPROM'],[39,'速度环 I',1,'EEPROM'],[40,'扭矩开关',1,'SRAM'],[41,'加速度',1,'SRAM'],[42,'目标位置',2,'SRAM'],[44,'PWM 开环速度',2,'SRAM'],[46,'运行速度',2,'SRAM'],[48,'转矩限制',2,'SRAM'],[50,'位置环 P（运行中）',1,'SRAM'],[51,'位置环 D（运行中）',1,'SRAM'],[52,'位置环 I（运行中）',1,'SRAM'],[55,'锁标志',1,'SRAM'],[56,'当前位置',2,'反馈'],[58,'当前速度',2,'反馈'],[60,'当前负载',2,'反馈'],[62,'当前电压（0.1 V）',1,'反馈'],[63,'当前温度（°C）',1,'反馈'],[64,'异步写标志',1,'反馈'],[65,'舵机状态位',1,'反馈'],[66,'移动标志',1,'反馈'],[67,'目标位置回读',2,'反馈'],[69,'当前电流（6.5 mA）',2,'反馈'],[80,'移动速度阈值',1,'出厂'],[81,'DTs（ms）',1,'出厂'],[82,'速度单位系数',1,'出厂'],[83,'最小速度限制',1,'出厂'],[84,'最大速度限制',1,'出厂'],[85,'加速度限制',1,'出厂'],[86,'加速度倍数',1,'出厂']];
  const DANGER = {5:'更改 ID 可能与其他设备撞号。',6:'更改波特率后可能失联。',8:'应答级别设为 0 后，写指令不再返回。',9:'角度限制影响活动范围。',11:'角度限制影响活动范围。',19:'更改卸载保护可能关闭过热、过流保护。',33:'速度模式下舵机会持续转动。'};
  const S = P.state.devices = {
    tab:'maintenance', expandedJoint:null, disclosures:{}, positions:Object.fromEntries(JOINTS.map(([id]) => [id,2048])), directions:{},
    calibrated:{}, undo:null, pose:'站姿', poses:['站姿','缩起来'], motion:{mode:'profile',seconds:1,acc:30}, moving:false, reference:'实时', follow:false, idle:false,
    register:41, registerRead:null, registerValue:30, registers:{}, registerWrite:null, unlock:false, scanIds:'10-14,20-24,30-34', scan:null, timing:null,
    tracking:{ids:[21],amplitude:3,pairs:false,tune:false,kp:'',kd:'',supported:false,active:false,status:'尚未开始',run:null,history:[],baseline:''},
  };
  P.state.calibrationPending ||= {joints:[],imu:false};
  P.deviceTab = name => { if(name==='calibration')S.tab='maintenance';else if(['maintenance','tracking','advanced'].includes(name))S.tab=name;P.navigate('devices/servos');P.render(); };
  P.stopDevice = () => { if(S.tracking.active) { S.tracking.active=false; S.tracking.status='已停止 · 恢复与卸力结果为演示'; } S.moving=false; S.idle=false; P.imuPage?.stop(); };
  const E=P.escape;
  const disclosure=(key,initial=false)=>`data-device-disclosure="${key}" ${(S.disclosures[key]??initial)?'open':''}`;
  const btn=(label,action,attrs='',cls='')=>`<button class="btn ${cls}" data-action="${action}" ${attrs}>${label}</button>`;
  const card=(title,body,extra='')=>`<section class="card"><div class="card-head"><h2>${title}</h2>${extra}</div><div class="card-body">${body}</div></section>`;
  const row=(label,value)=>`<div class="field-row"><strong>${label}</strong><span>${value}</span></div>`;
  const unavailable=()=>P.state.offline||P.state.stale||P.isSystemBusy?.()||P.state.boot==='stopped'&&!P.state.owned||P.state.maintenanceStatus==='failed';
  const read=value=>unavailable()?'—':value;
  const held=id=>P.state.jointTorque?.[id]??P.state.torque;
  const off=condition=>condition?'disabled':'';
  const committing=()=>['handoff','verifying'].includes(P.state.calibrationStatus);
  const disabled=()=>off(!P.state.owned||unavailable()||P.state.maintenanceStatus==='returning'||committing());
  const nameOf=id=>JOINTS.find(j=>j[0]===id)?.[1]||String(id);
  const current=()=>Number(P.state.selectedJoint||21);
  const pending=()=>P.state.calibrationPending;
  const options=(ids,value)=>ids.map(([id,name])=>`<option value="${id}" ${id===value?'selected':''}>#${id} ${name}</option>`).join('');
  const setJoint=id=>{P.state.selectedJoint=Number(id);P.render();};
  const confirm=(title,body,done,label='确认演示')=>P.openModal(title,body,label,dialog=>{if(disabled()){P.toast('等待控制权与有效回读后再操作');return false;}if(done(dialog)===false)return false;P.render();});
  const field=(label,body)=>`<label class="field">${label}${body}</label>`;
  const onboardingActive=()=>!!P.onboarding?.active?.();
  const jointCalibrated=id=>S.calibrated[id]?.zero===2048&&S.calibrated[id]?.direction===(S.directions[id]??1);
  function calibrationProgress() {
    const ids=JOINTS.filter(([id])=>jointCalibrated(id)).map(([id])=>id);
    return {done:ids.length,total:JOINTS.length,ids,missing:JOINTS.map(([id])=>id).filter(id=>!ids.includes(id)),complete:ids.length===JOINTS.length};
  }
  function saveCalibrationUndo() {
    S.undo={calibrated:Object.fromEntries(Object.entries(S.calibrated).map(([id,value])=>[id,{...value}])),directions:{...S.directions},pending:[...pending().joints]};
  }
  function resetForOnboarding() {
    S.calibrated={};S.undo=null;S.tab='maintenance';S.expandedJoint=null;
    S.moving=false;S.idle=false;S.follow=false;S.tracking.active=false;S.tracking.supported=false;
    pending().joints=[];
    return calibrationProgress();
  }
  function registerByte(id,addr) {
    if(unavailable())return null;
    const supplied=S.registers[id]?.[addr]??P.state.telemetry?.servos?.registers?.[id]?.raw?.[addr];
    if(Number.isInteger(supplied)&&supplied>=0&&supplied<=255)return supplied;
    const temp=P.state.telemetry?.servos?.temperaturesC?.[id],pos=S.positions[id];
    if(addr===40)return held(id)?1:0;
    if(addr===56||addr===57)return Number.isInteger(pos)?(pos>>(addr===56?0:8))&255:null;
    if(addr===62)return Number.isFinite(P.state.batteryVolts)?Math.round(P.state.batteryVolts*10):null;
    if(addr===63)return Number.isFinite(temp)?temp:null;
    return null;
  }
  function registerValue(id,addr,size=1) {
    const lo=registerByte(id,addr),hi=size===2?registerByte(id,addr+1):0;
    return lo===null||hi===null?null:lo|(hi<<8);
  }
  const registerText=(id,addr,size=1)=>registerValue(id,addr,size)??'—';
  function registerDump(id) {
    return {prototype:true,source:'demo',id,raw:Array.from({length:87},(_,addr)=>registerByte(id,addr)),rows:DUMP_REGS.map(([addr,name,size,area])=>({addr,name,size,area,value:registerValue(id,addr,size)}))};
  }
  function writeRegister(id,addr,size,value) {
    const old=registerValue(id,addr,size),raw=S.registers[id]||={};
    raw[addr]=value&255;if(size===2)raw[addr+1]=(value>>8)&255;
    S.registerWrite={prototype:true,source:'demo',id,addr,size,old,requested:value,new:registerValue(id,addr,size),same:registerValue(id,addr,size)===value};
    S.registerRead=id;S.registerValue=value;
  }
  function moveJoint(id,value) {
    if(disabled())return false;
    if(!Number.isInteger(value)||value<0||value>4095){P.toast('原始位置须为 0–4095 整数');return false;}
    if(!held(id)){P.toast('先保持这颗关节');return false;}
    S.positions[id]=value;S.moving=true;S.idle=false;
    const raw=S.registers[id]||={};raw[42]=value&255;raw[43]=(value>>8)&255;
    return true;
  }
  function copyRegisters(id) {
    const dump=registerDump(id),table=['| 地址 | 名称 | 区 | 值 |','|---|---|---|---|',...dump.rows.map(r=>`| ${r.addr} | ${r.name} | ${r.area} | ${r.value??'未提供'} |`)].join('\n');
    const fallback=()=>P.openModal('复制寄存器表',`<textarea class="register-copy-text" rows="10" style="width:100%" readonly aria-label="寄存器 Markdown 表">${E(table)}</textarea>`,'关闭');
    if(!navigator.clipboard?.writeText)return fallback();
    return navigator.clipboard.writeText(table).then(()=>P.toast('寄存器表已复制')).catch(fallback);
  }
  function navigation(sub) {
    return `<nav class="subnav exp-section-tabs device-section-tabs" aria-label="设备分类">${[['servos','舵机与关节'],['imu','IMU 与姿态'],['diagnostics','运行诊断']].map(([id,label])=>`<a class="${sub===id?'active':''}" href="#/devices/${id}" ${sub===id?'aria-current="page"':''}>${label}</a>`).join('')}</nav>`;
  }
  function toolbar() {
    const returning=P.state.maintenanceStatus==='returning',failed=P.state.maintenanceStatus==='failed';
    const save=pending().joints.length?`<div class="calibration-save-actions">${S.undo?btn('撤销上次','calib-undo',disabled()):''}${onboardingActive()?'':btn('写入主程序（'+pending().joints.length+'）','calibration-commit',off(unavailable()||returning||committing()),'primary')}</div>`:'';
    return `<div class="content-toolbar device-maintenance-toolbar"><div class="toolbar-group">${P.state.owned?btn(returning?'交还中…':failed?'重试交还':'交还主程序','release-bus',off(returning||committing()),'primary'):btn('接管总线','takeover',off(unavailable()||committing()),'primary')}${btn(P.icon('refresh',14)+' 扫描','servo-scan',disabled())}${btn('全部保持','joint-hold-all',disabled())}${btn('全部卸力','emergency','','danger')}</div>${save}<span class="status-text ${failed?'device-return-failed':''}">${returning?'正在卸力并交还':failed?(P.state.calibrationFailedStage==='handoff'?'交还未确认 · 标定尚未写入':'交还未确认'):P.state.owned?'Studio 控制中':'主程序控制中'}${failed?'<a class="link" href="#/logs">查看日志</a>':''}</span></div>`;
  }
  function tabs() {
    return `<div class="segmented device-tabs">${[['maintenance','舵机标定'],['tracking','关节跟踪'],['advanced','高级工具']].map(([id,label])=>btn(label,'device-tab',`data-tab="${id}"`,S.tab===id?'active':'')).join('')}</div>`;
  }
  function jointDetails(id) {
    const pos=S.positions[id],temperature=P.state.telemetry?.servos?.temperaturesC?.[id];
    const comm=P.state.telemetry?.servos?.comm?.[id],commText=comm?`丢 ${comm.missed??'—'} / 问 ${comm.asked??'—'}`:'—';
    const gains=[50,51,48].map(addr=>registerText(id,addr,addr===48?2:1)).join(' / ');
    const eeprom=[21,22,23].map(addr=>registerText(id,addr)).join(' / ');
    const currentRaw=registerValue(id,69,2),loadRaw=registerValue(id,60,2),status=registerValue(id,65);
    return `<div class="servo-inline-details" id="joint-details-${id}" role="region" aria-label="#${id} ${nameOf(id)}详情"><div class="servo-quick-metrics">${[['温度',read(Number.isFinite(temperature)?temperature+'°C':'—')],['电压',read(Number.isFinite(P.state.batteryVolts)?P.state.batteryVolts.toFixed(1)+' V':'—')],['电流',currentRaw===null?'—':((currentRaw&32768?-(currentRaw&32767):currentRaw)*6.5).toFixed(1)+' mA'],['负载',loadRaw===null?'—':((loadRaw&1024?-(loadRaw&1023):loadRaw)/10).toFixed(1)+' %']].map(([label,value])=>`<div><small>${label}</small><strong>${value}</strong></div>`).join('')}</div><div class="servo-position-control"><label for="joint-position">目标位置 <span data-joint-target-label>${read(pos)} 步 / ${read(((pos-2048)*360/4096).toFixed(1)+'°')}</span></label><div class="servo-target-line"><input id="joint-position" data-id="${id}" aria-label="#${id} 目标位置滑杆" type="range" min="0" max="4095" value="${pos}" ${disabled()}><input id="joint-position-number" aria-label="#${id} 目标原始位置" type="number" min="0" max="4095" value="${pos}" ${disabled()}>${btn('移动','joint-move',`${disabled()} data-id="${id}"`,'primary')}</div></div><details class="servo-parameters" ${disclosure('joint-parameters-'+id)}><summary>参数与详细回读</summary><div class="servo-detail-grid">${row('运行 P / D / 限力',gains)}${row('EEPROM P / D / I',eeprom)}${row('零位偏移',registerText(id,31,2))}${row('原始速度',registerText(id,58,2))}${row('通信',read(commText))}${row('状态位',status===null?'—':'0x'+status.toString(16).padStart(2,'0'))}${row('方向',btn((S.directions[id]??1)>0?'+1':'−1','joint-direction',`${disabled()} data-id="${id}"`,'small'))}</div>${S.registerWrite?.id===id?'<p class="inline-note">已手动修改演示寄存器；实际主程序参数未变更。</p>':''}</details></div>`;
  }
  function jointItem(id,name) {
    const expanded=S.expandedJoint===id,unknown=unavailable(),on=held(id);
    return `<article class="servo-item ${expanded?'expanded':''}" data-joint-id="${id}"><div class="servo-item-summary"><button class="servo-summary-main" data-action="joint-select" data-id="${id}" aria-expanded="${expanded}" aria-controls="joint-details-${id}"><span class="servo-item-id">#${id}</span><span class="servo-item-name"><strong>${name}</strong><small class="servo-mobile-position">${read(S.positions[id])} 步${unknown?' · 未知':''}</small></span><span class="servo-summary-position">${read(S.positions[id])}<small>步</small></span><span class="servo-chevron ${expanded?'open':''}">${P.icon('chevron',14)}</span></button><div class="servo-inline-actions" aria-label="#${id} 操作">${btn('松开','joint-off',`${disabled()} data-id="${id}" aria-label="#${id} ${name}松开"`,!unknown&&!on?'is-current':'')}${btn('保持','joint-hold',`${disabled()} data-id="${id}" aria-label="#${id} ${name}保持"`,!unknown&&on?'is-current':'')}${btn('中位','calib-preview',`${disabled()} data-id="${id}" aria-label="#${id} ${name}中位标定" title="${jointCalibrated(id)?'本轮中位已记录':'记录本颗中位'}"`,jointCalibrated(id)?'is-current':'')}</div></div>${expanded?jointDetails(id):''}</article>`;
  }
  function jointList() {
    const groups=[['左腿',JOINTS.filter(([id])=>id>=20&&id<30)],['右腿',JOINTS.filter(([id])=>id<20)],['头颈',JOINTS.filter(([id])=>id>=30)]];
    const online=P.state.telemetry?.servos?.online,total=P.state.telemetry?.servos?.total;
    return card('舵机列表',`<div class="servo-accordion">${groups.map(([label,joints])=>`<section class="servo-joint-group" aria-label="${label}"><h3>${label}</h3>${joints.map(([id,name])=>jointItem(id,name)).join('')}</section>`).join('')}</div>`,P.badge(unavailable()?'等待回读':`${online??'—'} / ${total??'—'} 在线`,unavailable()?'warning':''));
  }
  function poses() {
    return card('姿态与运动',`<div class="button-row">${btn('全部回零位','servo-zero',disabled())}${btn('停止过渡','servo-stop')}${btn('同步当前位置','servo-sync',disabled())}</div><details ${disclosure('motion-settings')}><summary>参考姿势与运动设置</summary><div class="button-row section-space">${['站姿','缩起来','坐姿','折叠'].map(name=>btn(name,'servo-reference',`${disabled()} data-pose="${name}"`)).join('')}</div><p>参考姿势：${E(S.reference)}；${S.moving?'过渡演示中':'未执行过渡'}${S.idle?'；头部灵动演示中':''}。</p><div class="device-form-grid">${field('运动方式',`<select data-device-field="motion-mode" ${disabled()}><option value="profile" ${S.motion.mode==='profile'?'selected':''}>平滑 · 舵机梯形曲线</option><option value="stream" ${S.motion.mode==='stream'?'selected':''}>流式 · 分步发送</option></select>`)}${field('用时（秒）',`<input data-device-field="motion-seconds" type="number" min="0" max="30" step="0.5" value="${S.motion.seconds}" ${disabled()}>`)}${field('加速度',`<input data-device-field="motion-acc" type="number" min="1" max="254" value="${S.motion.acc}" ${disabled()}>`)}</div><label class="device-check"><input data-device-field="motion-follow" type="checkbox" ${S.follow?'checked':''} ${disabled()}>跟随拖动 · 已保持关节随滑块演示移动</label><div class="button-row section-space">${btn(S.idle?'停止灵动':'头部灵动',S.idle?'servo-idle-stop':'servo-idle',S.idle?'':disabled())}<small class="muted">仅 30–34；停止或卸力时结束演示。</small></div></details><details ${disclosure('saved-poses')}><summary>已保存姿态</summary><div class="device-pose-row">${field('姿态',`<select id="saved-pose">${S.poses.map(name=>`<option ${name===S.pose?'selected':''}>${E(name)}</option>`).join('')}</select>`)}${btn('调用','pose-load',disabled())}</div><div class="button-row">${btn('保存当前姿态','pose-save',disabled())}${btn('删除','pose-delete',`${disabled()} ${off(!S.poses.length)}`)}</div></details>`);
  }
  function maintenance() {
    const reference=P.zeroReferenceMarkup?.()||'<section class="card zero-reference-card"><div class="card-head"><h2>零位参考</h2></div><div class="card-body"><p class="muted">正在准备原版零位参考模型</p></div></section>';
    return `<div class="servo-calibration-layout"><aside class="zero-reference-column">${reference}</aside><div class="servo-calibration-main">${jointList()}<div class="section-space">${poses()}</div></div></div>`;
  }
  function trackingPlan() {
    const t=S.tracking;
    return {ids:[...t.ids],amplitude_deg:t.amplitude,stages:'ramp+sine-0.25+sine-0.5',pairs:t.pairs,profile:'prototype',calibration:'prototype-v1',centers:t.ids.map(id=>[id,S.positions[id]]),speed:50,acceleration:30,torque_limit:1000};
  }
  function comparable(a,b) {return !!a&&!!b&&!a.issues.length&&!b.issues.length&&JSON.stringify(a.plan)===JSON.stringify(b.plan);}
  function createRun(plan=trackingPlan()) {
    const t=S.tracking,n=t.history.length+1;
    const stages=plan.ids.flatMap(id=>['慢移停稳','0.25 Hz','0.5 Hz'].map((stage,k)=>({id,stage,rms_error_deg:Number((0.2+k*0.08).toFixed(2)),lag_ms:20+k*10,hold_tail_range_deg:0.12+k*0.03})));
    const samples=Array.from({length:60},(_,k)=>({prototype:true,t_ms:k*50,id:plan.ids[Math.floor(k/60*plan.ids.length)],target_deg:Number((Math.sin(k/6)*plan.amplitude_deg).toFixed(3)),actual_deg:Number((Math.sin((k-0.3)/6)*plan.amplitude_deg).toFixed(3)),target_raw:2048+Math.round(Math.sin(k/6)*plan.amplitude_deg*4096/360),actual_raw:2048+Math.round(Math.sin((k-0.3)/6)*plan.amplitude_deg*4096/360),speed_raw:10,load_raw:0,temperature_c:31,voltage_v:P.state.batteryVolts||7.8,status:0,tx_bytes:(k+1)*21,rx_bytes:(k+1)*63}));
    return {prototype:true,source:'demo',id:'demo-run-'+n,label:'演示记录 '+n,status:'completed',failure:null,cleanup_errors:[],cleanup:{unloaded:true,parametersRestored:true},plan,pd:{p:t.tune&&t.kp!==''?Number(t.kp):registerValue(plan.ids[0],50),d:t.tune&&t.kd!==''?Number(t.kd):registerValue(plan.ids[0],51)},issues:[],stages,samples};
  }
  function trackingResult() {
    const t=S.tracking,r=t.run,b=t.history.find(run=>run.id===t.baseline),match=comparable(r,b);
    return card('测试记录',`<div class="device-track-status" role="status">${E(t.status)}</div><div class="tracking-chart"><div class="tracking-legend"><span>— 目标角</span><span>— 实际角</span></div><svg viewBox="0 0 560 150" role="img" aria-label="原型目标与反馈示意曲线"><path class="chart-grid" d="M20 25H540M20 75H540M20 125H540M20 15V135M150 15V135M280 15V135M410 15V135M540 15V135"/><path d="M20 75C65 75 65 30 110 30S155 120 200 120 245 30 290 30 335 120 380 120 425 30 470 30 515 75 540 75" fill="none" stroke="#00877e" stroke-width="2"/><path d="M20 80C70 80 70 34 115 34S160 117 205 117 250 34 295 34 340 117 385 117 430 34 475 34 520 80 540 80" fill="none" stroke="#93b8e1" stroke-width="2"/></svg><small>示意曲线 · 未采集硬件信号</small></div>${r?.cleanup?`<details ${r.issues.length?'open':''}><summary>${r.issues.length?'停止原因与清理结果':'本次结束状态'} · 演示记录</summary>${r.failure?row('停止原因',E(r.failure)):''}${row('记录中的卸力',r.cleanup.unloaded===true?'已确认（演示）':r.cleanup.unloaded===false?'未完成（演示）':'未确认')}${row('参数恢复',r.cleanup.parametersRestored===true?'已确认（演示）':'尚未确认（演示）')}${r.cleanup_errors.length?`<p class="device-warning">${r.cleanup_errors.map(E).join('；')}</p>`:''}<p class="inline-note">仅说明此条演示记录，不代表当前设备状态。</p></details>`:''}${r?`<div class="table-scroll section-space"><table class="data-table"><thead><tr><th>关节 / 阶段</th><th>RMS °</th><th>滞后 ms</th><th>停稳摆幅 °</th>${match?'<th>误差变化 °</th>':''}</tr></thead><tbody>${r.stages.map(s=>`<tr><td>#${s.id} ${s.stage}</td><td>${s.rms_error_deg.toFixed(2)}</td><td>${s.lag_ms}</td><td>${s.hold_tail_range_deg.toFixed(2)}</td>${match?`<td>${(s.rms_error_deg-b.stages.find(x=>x.id===s.id&&x.stage===s.stage).rms_error_deg).toFixed(2)}</td>`:''}</tr>`).join('')}</tbody></table></div>`:''}<details ${disclosure('tracking-history',!!t.baseline)}><summary>历史对照</summary>${field('查看历史记录',`<select data-device-field="tracking-record" ${off(t.active)}><option value="">选择演示记录</option>${t.history.map(run=>`<option value="${run.id}" ${run.id===r?.id?'selected':''}>${E(run.label)}${run.issues.length?' · 异常':''}</option>`).join('')}</select>`)}${btn('查看异常报告示例','tracking-example',off(t.active),'small')}${field('选择基线',`<select data-device-field="tracking-baseline"><option value="">选择演示记录</option>${t.history.filter(run=>run.id!==r?.id).map(run=>`<option value="${run.id}" ${run.id===t.baseline?'selected':''}>${E(run.label)}${run.issues.length?' · 异常':''} · ${run.plan.ids.map(id=>'#'+id).join(' ')}</option>`).join('')}</select>`)}<p>${!b?'先完成两次演示记录，再选择基线。':match?'姿态、幅度、阶段、限速、标定及完整性匹配，可展示差值。':'条件不匹配或记录有异常，不计算差值。'}</p></details><div class="button-row section-space">${btn('原始记录 .jsonl','tracking-raw',off(!r||t.active))}${btn('阶段报告 .json','tracking-report',off(!r||t.active))}</div>`,P.badge('演示数据'));
  }
  function tracking() {
    const t=S.tracking,locked=t.active||committing()||P.isSystemBusy?.(),lock=off(locked);
    return `<div class="grid-two"><div class="stack">${card('关节跟踪',`<div class="button-row">${btn('全选双腿','tracking-all',lock)}${btn('清空','tracking-clear',lock)}</div><fieldset class="device-fieldset" ${lock}><legend>关节 · 已选 ${t.ids.length} 颗</legend><div class="device-joint-checks">${LEGS.map(([id,name])=>`<label><input data-device-field="tracking-joint" data-id="${id}" type="checkbox" ${t.ids.includes(id)?'checked':''}>#${id} ${name}</label>`).join('')}</div></fieldset><div class="device-form-grid">${field('摆幅 ±°',`<input type="number" data-device-field="tracking-amplitude" min="1" max="5" value="${t.amplitude}" ${lock}>`)}${field('轨迹','<input value="慢移 + 0.25 / 0.5 Hz" readonly>')}</div><label class="device-check"><input type="checkbox" data-device-field="tracking-pairs" ${t.pairs?'checked':''} ${lock}>追加左右同类关节反相测试</label><details ${disclosure('tracking-pd')}><summary>临时 P / D</summary><label class="device-check"><input type="checkbox" data-device-field="tracking-tune" ${t.tune?'checked':''} ${off(locked||t.ids.length!==1)}>启用临时调整 · 仅单颗可用</label><div class="device-form-grid">${field('P · 1–32',`<input data-device-field="tracking-kp" type="number" min="1" max="32" placeholder="保留当前" value="${t.kp}" ${off(!t.tune||locked)}>`)}${field('D · 0–64',`<input data-device-field="tracking-kd" type="number" min="0" max="64" placeholder="保留当前" value="${t.kd}" ${off(!t.tune||locked)}>`)} </div><p>先做原参数基线。仅 RAM 临时参数，停止后恢复。</p></details><p class="inline-note">多颗按顺序逐颗测试；仅用于空载筛查。</p><label class="device-check"><input data-device-field="tracking-supported" type="checkbox" ${t.supported?'checked':''} ${lock}>躯干已架稳，双脚离地，线材无遮挡</label>${P.torqueStatus()!=='已卸力'?'<p class="device-warning">开始前先全部卸力。</p>':''}<div class="button-row">${btn(t.active?'演示本轮采集完成':'开始流程演示',t.active?'tracking-finish':'tracking-start',`${disabled()} ${off(!t.active&&(!t.ids.length||!t.supported||P.torqueStatus()!=='已卸力'))}`,'primary')}${btn('停止并卸力','tracking-stop',off(!t.active),'danger')}</div>`,P.badge('待板端安装','warning'))}</div>${trackingResult()}</div>`;
  }
  function dumpMarkup(id) {
    const dump=registerDump(id);
    return `<details ${disclosure('register-dump-'+id)}><summary>逐地址回读 · #${id} · ${dump.rows.length} 项</summary><div class="button-row section-space">${btn('下载 JSON','register-export',`data-id="${id}"`)}${btn('复制 Markdown 表','register-copy',`data-id="${id}"`)}</div><p class="inline-note">演示快照；未提供的值显示 —，导出为 null。</p><div class="table-scroll"><table class="data-table"><thead><tr><th>地址</th><th>字段</th><th>区 / 字节</th><th>值</th></tr></thead><tbody>${dump.rows.map(r=>`<tr><td>${r.addr}</td><td>${E(r.name)}</td><td>${r.area} / ${r.size}</td><td>${r.value??'—'}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function busResults() {
    const scan=S.scan,timing=S.timing;
    return `${scan?`<details open><summary>扫描结果 · 演示</summary>${row('请求范围',scan.ids.length+' 个 ID')}${row('found · 在线',scan.found===null?'未提供':scan.found.map(id=>'#'+id).join('、')||'无')}${row('lost · 未应答',scan.lost===null?'未提供':scan.lostOmitted?'大范围扫描不列逐颗缺失':scan.lost.map(id=>'#'+id).join('、')||'无')}<p class="inline-note">按演示设备列表生成，未请求串口。</p></details>`:''}${timing?`<details open><summary>时序统计 · 演示格式</summary>${row('样本 / 在线数',timing.n+' 次 / '+(timing.ids??'—')+' 颗')}${[['min_ms','最快'],['avg_ms','平均'],['p99_ms','p99'],['max_ms','最慢']].map(([key,label])=>row(label,timing[key]===null?'—':timing[key]+' ms')).join('')}${row('fails · 漏读',timing.fails??'—')}<p class="inline-note">未提供串口耗时的字段留空。</p></details>`:''}`;
  }
  function advanced() {
    const reg=REGS.find(r=>r[0]===S.register),id=current(),w=S.registerWrite;
    const writeResult=w?`<div class="section-space" role="status">${row('#'+w.id+' · '+w.addr+' 写入演示',(w.old??'未提供')+' → '+w.requested+' → 回读 '+w.new)}${row('核对',w.same?'演示回读一致':'读回与写入不一致')}<small class="muted">未请求设备；${w.unlock?'已演示解锁与重新加锁。':'只更新当前演示快照。'}</small></div>`:'';
    return `<div class="grid-two">${card('参数与寄存器',`${field('目标关节',`<select data-device-field="joint">${options(JOINTS,id)}</select>`)}<div class="button-row">${btn('回读参数','register-read',disabled(),'primary')}${btn('重启这颗','joint-reboot',disabled())}</div>${S.registerRead===id?dumpMarkup(id):''}<details ${disclosure('register-write',w?.id===id)}><summary>写寄存器</summary>${field('字段',`<select data-device-field="register" ${disabled()}>${REGS.map(([addr,name,size,area])=>`<option value="${addr}" ${addr===S.register?'selected':''}>${addr} · ${name} · ${size} 字节 · ${area}</option>`).join('')}</select>`)}${row('存储区域',`${reg[3]} · ${reg[2]} 字节`)}${row('当前回读',registerText(id,reg[0],reg[2]))}${field('写入值',`<input id="register-value" type="number" min="0" max="${reg[2]===1?255:65535}" value="${S.registerValue}" ${disabled()}>`)}<label class="device-check"><input data-device-field="register-unlock" type="checkbox" ${S.unlock?'checked':''} ${disabled()}>解锁写 ${reg[3]==='EEPROM'?'· 完成后重新加锁':''}</label>${DANGER[S.register]?`<p class="device-warning">${DANGER[S.register]}</p>`:''}${btn('预览写入','register-write',disabled(),DANGER[S.register]?'danger':'')}${writeResult}</details>`)}<div class="stack">${card('总线工具',`${field('扫描 ID / 范围',`<input id="scan-ids" value="${E(S.scanIds)}" placeholder="10-14,20-24,30-34" ${disabled()}>`)}<div class="button-row">${btn('扫描范围','servo-scan-range',disabled(),'primary')}${btn('全网 0–253','servo-scan-all',disabled())}${btn('时序 ×200','servo-timing',disabled())}</div>${busResults()}<details ${disclosure('direction-table')}><summary>方向表</summary><p>顺序 20–24、30–34、10–14；导出完整 15 颗方向。</p>${btn('导出方向表','direction-export','','small')}</details>`)}${card('修改 ID',`<div class="device-form-grid">${field('当前 ID',`<input id="old-joint-id" type="number" value="${id}" readonly>`)}${field('新 ID',`<input id="new-joint-id" type="number" min="0" max="253" value="36" ${disabled()}>`)}</div>${btn('预览修改 ID','joint-id',disabled())}<p class="inline-note">操作时总线上仅保留目标舵机。</p>`)}</div></div>`;
  }
  function diagnostics() {
    const telemetry=P.state.telemetry||{},servos=telemetry.servos||{},mainUnknown=unavailable()||P.state.owned||P.state.boot!=='ready';
    const stage={ready:'运行中',starting:'启动中',blocked:'IMU 方向待验证',stopped:'尚未运行'}[P.state.boot]||'未提供';
    const numeric=(v,suffix='')=>!unavailable()&&Number.isFinite(v)?v+suffix:'—';
    const policy=P.state.system?.executing==='walk'?'行走模型执行中':P.state.system?.loaded?.walk?'模型已加载，未执行':'模型尚未加载';
    return `<div class="grid-two"><div class="stack">${card('通信与回读',`${row('主程序采集频率',mainUnknown?'—':numeric(telemetry.controlHz,' Hz'))}${row('最近舵机回读',unavailable()?P.badge('等待有效回读','warning'):numeric(servos.ageMs,' ms'))}${row('在线舵机',unavailable()?'—':(servos.online??'—')+' / '+(servos.total??'—'))}${row('控制权',P.state.owned?'Studio':'主程序')}${row('扭矩状态',P.torqueStatus())}<details ${disclosure('diagnostic-stats')}><summary>逐关节错误与通信统计</summary><div class="table-scroll"><table class="data-table"><thead><tr><th>ID</th><th>问 / 回 / 丢</th><th>连丢 / 最长</th><th>报警 / 状态</th></tr></thead><tbody>${JOINTS.map(([id])=>{const c=servos.comm?.[id]||{},status=registerValue(id,65);return `<tr><td>#${id}</td><td>${[c.asked,c.ok,c.missed].map(n=>numeric(n)).join(' / ')}</td><td>${[c.streak,c.max_streak].map(n=>numeric(n)).join(' / ')}</td><td>${numeric(c.alarm)} / ${status===null?'—':'0x'+status.toString(16).padStart(2,'0')}</td></tr>`;}).join('')}</tbody></table></div><p class="inline-note">演示数据未提供的通信统计保持为空。</p></details>`)}${card('主程序',`${row('启动阶段',E(stage))}${row('启动耗时',numeric(telemetry.startup?.elapsedMs,' ms'))}${row('姿态采集',mainUnknown?'等待主程序有效回读':P.state.devices.imu?.mount?.verified===true?'安装方向已确认':'方向尚未确认')}${row('策略状态',mainUnknown?'等待主程序状态':policy)}<div class="section-space"><a class="btn" href="#/logs">${P.icon('log',14)}运行日志与诊断导出</a></div>`)}</div>${card('状态预览',`${row('设备连接',btn(P.state.offline?'恢复连接':'模拟断线','diagnostic-offline',off(committing()||P.isSystemBusy?.()),'small'))}${row('反馈新鲜度',btn(P.state.stale?'恢复回读':'模拟回读过期','diagnostic-stale',off(committing()||P.isSystemBusy?.()),'small'))}<div class="section-space">${btn('重新检查','diagnostic-check',off(committing()||P.isSystemBusy?.()),'primary')}</div>`)}</div>`;
  }
  function parseIds(value,knownOnly=true) {
    const ids=[];
    for(const token of value.split(/[,，\s]+/).filter(Boolean)) {
      if(!/^\d+(?:-\d+)?$/.test(token))throw Error('请使用 ID 或范围，例如 10-14,21');
      const [a,b=a]=token.split('-').map(Number);
      if(a<0||b>253||a>b)throw Error('ID 范围须为 0–253，且起点不大于终点');
      for(let id=a;id<=b;id++)if(!ids.includes(id))ids.push(id);
    }
    if(!ids.length||knownOnly&&ids.some(id=>!JOINTS.some(j=>j[0]===id)))throw Error('请选择当前列表中的关节');
    return ids;
  }
  function calibrationPreview(target) {
    const ids=[Number(target.dataset.id)];
    if(!JOINTS.some(([id])=>id===ids[0])){P.toast('请选择当前列表中的关节');return;}
    const large=ids.some(id=>Math.abs(S.positions[id]-2048)*360/4096>30);
    confirm('预览中位标定',`<p>本轮记录 ${ids.length} 颗；完成后保持当前位置。此操作为原型演示。</p><table class="data-table"><thead><tr><th>关节</th><th>现在</th><th>记录为</th><th>差值 °</th></tr></thead><tbody>${ids.map(id=>`<tr><td>#${id} ${nameOf(id)}</td><td>${S.positions[id]}</td><td>2048</td><td>${((S.positions[id]-2048)*360/4096).toFixed(1)}</td></tr>`).join('')}</tbody></table>${large?'<label class="device-check"><input id="large-calib-confirm" type="checkbox">偏差较大，已复核姿势和方向</label>':''}`,dialog=>{
      if(large&&!dialog.querySelector('#large-calib-confirm').checked){P.toast('请先复核大偏差');return false;}
      saveCalibrationUndo();
      ids.forEach(id=>{S.calibrated[id]={zero:2048,direction:S.directions[id]??1};P.state.jointTorque||={};P.state.jointTorque[id]=true;});
      pending().joints=[...new Set([...pending().joints,...ids])];P.onboarding?.record?.('calibration-changed','servo');P.toast('已记录本轮中位演示，尚未提交主程序');
    },'记录这 '+ids.length+' 颗');
  }
  function onField(target) {
    const key=target.dataset.deviceField,value=target.value,t=S.tracking;
    if(key==='joint')return setJoint(value);
    if((committing()||P.isSystemBusy?.())&&!['tracking-baseline','tracking-record'].includes(key)){P.render();return;}
    if(key==='motion-mode')S.motion.mode=value;
    else if(key==='motion-seconds')S.motion.seconds=Math.min(30,Math.max(0,Number(value)));
    else if(key==='motion-acc')S.motion.acc=Math.min(254,Math.max(1,Number(value)));
    else if(key==='motion-follow')S.follow=target.checked;
    else if(key==='register'){S.register=Number(value);S.unlock=REGS.find(r=>r[0]===S.register)[3]==='EEPROM';P.render();}
    else if(key==='register-unlock')S.unlock=target.checked;
    else if(key==='tracking-joint'){const id=Number(target.dataset.id);t.ids=target.checked?[...t.ids,id]:t.ids.filter(n=>n!==id);if(t.ids.length!==1){t.tune=false;t.kp='';t.kd='';}P.render();}
    else if(key==='tracking-amplitude')t.amplitude=Number(value);
    else if(key==='tracking-pairs')t.pairs=target.checked;
    else if(key==='tracking-tune'){t.tune=target.checked;P.render();}
    else if(key==='tracking-kp')t.kp=value;
    else if(key==='tracking-kd')t.kd=value;
    else if(key==='tracking-supported'){t.supported=target.checked;P.render();}
    else if(key==='tracking-baseline'){t.baseline=value;P.render();}
    else if(key==='tracking-record'&&!t.active){const r=t.history.find(run=>run.id===value);if(r){t.run=r;t.status=r.issues.length?'历史异常报告示例 · 仅供流程查看':'历史演示记录';}P.render();}
  }
  function handle(action,target) {
    const id=target.dataset.id!=null?Number(target.dataset.id):current(),t=S.tracking;
    if(action==='device-tab'){S.tab=target.dataset.tab==='calibration'?'maintenance':target.dataset.tab;P.render();return;}
    if(action==='joint-select'){P.state.selectedJoint=id;S.expandedJoint=S.expandedJoint===id?null:id;P.render();return;}
    if(action==='calibration-commit')return P.openCalibrationCommit?.();
    if(action.startsWith('imu-'))return P.imuPage?.handle(action,target);
    const maintenanceActions=['joint-move','joint-hold','joint-off','joint-hold-all','joint-direction','servo-zero','servo-reference','servo-sync','pose-load','pose-save','pose-delete','calib-preview','calib-undo','tracking-start','tracking-finish','servo-scan','servo-scan-range','servo-scan-all','servo-timing','register-read','register-write','joint-reboot','joint-id'];
    if((maintenanceActions.includes(action)||action==='servo-idle')&&disabled()){P.toast('先取得维护控制权并等待有效回读');return;}
    if((committing()||P.isSystemBusy?.())&&['tracking-all','tracking-clear','diagnostic-offline','diagnostic-stale','diagnostic-check'].includes(action))return;
    if(action==='joint-move'){if(!moveJoint(id,Number(document.querySelector('#joint-position-number').value)))return;}
    else if(action==='joint-hold'||action==='joint-off'){P.state.jointTorque||={};P.state.jointTorque[id]=action==='joint-hold';if(action==='joint-off'&&id>=30)S.idle=false;}
    else if(action==='joint-hold-all'){P.state.torque=true;P.state.jointTorque={};}
    else if(action==='joint-direction'){
      saveCalibrationUndo();S.directions[id]=-(S.directions[id]??1);
      // The earlier zero check was made with another direction; explicitly recheck this joint.
      delete S.calibrated[id];pending().joints=[...new Set([...pending().joints,id])];P.onboarding?.record?.('calibration-changed','servo');
    }
    else if(action==='servo-zero'||action==='servo-reference'){const pose=target.dataset.pose||'零位';if(pose==='折叠')return confirm('折叠姿势演示','<p>实机折叠前需确认零位与机械活动空间，原型不驱动设备。</p>',()=>{S.reference=pose;S.moving=true;S.idle=false;});S.reference=pose;S.moving=true;S.idle=false;if(pose==='零位')JOINTS.forEach(([n])=>S.positions[n]=2048);}
    else if(action==='servo-stop'){S.moving=false;S.idle=false;P.toast('已停止过渡与灵动演示，保持状态不变');}
    else if(action==='servo-idle'||action==='servo-idle-stop'){S.idle=action==='servo-idle';S.moving=false;if(S.idle){P.state.jointTorque||={};JOINTS.filter(([n])=>n>=30).forEach(([n])=>P.state.jointTorque[n]=true);}P.toast(S.idle?'头部灵动流程演示中，未驱动设备':'灵动演示已停止');}
    else if(action==='servo-sync')P.toast('目标滑块已按示意当前位置对齐');
    else if(action==='pose-load'){S.pose=document.querySelector('#saved-pose').value;S.reference=S.pose;S.moving=true;P.toast('已展示姿态调用');}
    else if(action==='pose-save')return confirm('保存姿态',field('名称','<input id="pose-name" value="新姿态" maxlength="30">'),dialog=>{const name=dialog.querySelector('#pose-name').value.trim();if(!name)return false;S.pose=name;if(!S.poses.includes(name))S.poses.push(name);},'保存');
    else if(action==='pose-delete'){const name=document.querySelector('#saved-pose').value;return confirm('删除姿态',`<p>删除「${E(name)}」？</p>`,()=>{S.poses=S.poses.filter(p=>p!==name);S.pose=S.poses[0]||'';},'删除');}
    else if(action==='calib-preview')return calibrationPreview(target);
    else if(action==='calib-undo'&&S.undo){S.calibrated={...S.undo.calibrated};S.directions={...S.undo.directions};pending().joints=[...S.undo.pending];S.undo=null;P.onboarding?.record?.('calibration-changed','servo');}
    else if(action==='tracking-all'||action==='tracking-clear'){t.ids=action==='tracking-all'?LEGS.map(([id])=>id):[];t.tune=false;t.kp='';t.kd='';}
    else if(action==='tracking-start'){if(!P.state.owned||unavailable()||P.torqueStatus()!=='已卸力'||!t.supported||!t.ids.length){P.toast('先接管、全部卸力并确认支撑');return;}if(!t.ids.every(n=>LEGS.some(([id])=>id===n))||!Number.isFinite(t.amplitude)||t.amplitude<1||t.amplitude>5||t.tune&&(t.ids.length!==1||t.kp!==''&&(!Number.isFinite(Number(t.kp))||Number(t.kp)<1||Number(t.kp)>32)||t.kd!==''&&(!Number.isFinite(Number(t.kd))||Number(t.kd)<0||Number(t.kd)>64))){P.toast('检查双腿选择、幅度及 P/D 范围');return;}t.active=true;t.status='流程演示中 · 双腿按所选顺序逐颗执行';t.supported=false;}
    else if(action==='tracking-finish'&&t.active){t.run=createRun();t.history.unshift(t.run);t.active=false;t.status='演示记录完成 · 已展示卸力与参数恢复结果';P.state.torque=false;P.state.jointTorque={};}
    else if(action==='tracking-example'&&!t.active){let example=t.history.find(r=>r.example);if(!example){example=createRun({...trackingPlan(),ids:[21],amplitude_deg:3,pairs:false,centers:[[21,2048]]});Object.assign(example,{id:'demo-anomaly',label:'异常报告示例',example:true,pd:{p:null,d:null},status:'aborted',failure:'演示：关节反馈连续超时，采集中止',issues:['采集中止','参数恢复回读未确认'],cleanup_errors:['演示：卸力后参数恢复回读未确认'],cleanup:{unloaded:true,parametersRestored:false}});example.stages=example.stages.slice(0,1);example.samples=example.samples.slice(0,20);t.history.unshift(example);}t.run=example;t.status='历史异常报告示例 · 未运行测试';}
    else if(action==='tracking-stop'){P.stopDevice();P.state.torque=false;P.state.jointTorque={};t.supported=false;}
    else if(action==='tracking-raw'&&t.run&&!t.active)return P.download(t.run.id+'.jsonl',t.run.samples.map(x=>JSON.stringify(x)).join('\n')+'\n','application/x-ndjson');
    else if(action==='tracking-report'&&t.run&&!t.active){const {samples,...report}=t.run;return P.download(t.run.id+'-report.json',JSON.stringify(report,null,2));}
    else if(action==='servo-timing'){const fixture=P.state.telemetry?.servos?.timing||{};S.timing={prototype:true,source:'demo',n:200,ids:P.state.telemetry?.servos?.online??null,...Object.fromEntries(['min_ms','avg_ms','p99_ms','max_ms','fails'].map(key=>[key,Number.isFinite(fixture[key])?fixture[key]:null]))};P.toast('已展示时序结果格式，未请求串口');}
    else if(['servo-scan','servo-scan-range','servo-scan-all'].includes(action)){try{S.scanIds=document.querySelector('#scan-ids')?.value||S.scanIds;const ids=action==='servo-scan-all'?Array.from({length:254},(_,n)=>n):parseIds(S.scanIds,false),telemetry=P.state.telemetry?.servos;const onlineIds=Array.isArray(telemetry?.onlineIds)?telemetry.onlineIds:telemetry?.online===15&&telemetry?.total===15?JOINTS.map(([id])=>id):null;const found=onlineIds?ids.filter(id=>onlineIds.includes(id)):null;S.scan={prototype:true,source:'demo',ids,found,lost:onlineIds?(ids.length>32?[]:ids.filter(id=>!onlineIds.includes(id))):null,lostOmitted:ids.length>32};P.toast(found?'扫描演示：在线 '+found.length+' 颗，未请求串口':'当前未提供逐 ID 在线数据');}catch(error){P.toast(error.message);return;}}
    else if(action==='register-read'){S.registerRead=id;P.toast('演示寄存器快照已展开');}
    else if(action==='register-export')return P.download('servo-'+id+'-demo.json',JSON.stringify(registerDump(id),null,2));
    else if(action==='register-copy')return copyRegisters(id);
    else if(action==='direction-export'){const ids=[20,21,22,23,24,30,31,32,33,34,10,11,12,13,14];return P.download('directions-demo.toml','# Prototype data\ndirections = ['+ids.map(n=>S.directions[n]??1).join(', ')+']\n','text/plain');}
    else if(action==='register-write'){const reg=REGS.find(r=>r[0]===S.register),input=document.querySelector('#register-value'),value=Number(input.value),unlock=S.unlock;if(!input.value.trim()||!Number.isInteger(value)||value<0||value>(reg[2]===1?255:65535)){P.toast('数值超过字段字节范围');return;}if(reg[3]==='EEPROM'&&!unlock){P.toast('EEPROM 字段需要解锁写确认');return;}return confirm('预览寄存器写入',`<p>#${id} · ${reg[1]}（地址 ${reg[0]}）<br>${reg[2]} 字节 ${reg[3]}：${registerText(id,reg[0],reg[2])} → ${value}<br>${DANGER[reg[0]]||'只更新演示快照，未请求设备。'}</p>`,()=>{writeRegister(id,reg[0],reg[2],value);S.registerWrite.unlock=unlock;P.toast('演示写入与回读已核对，未请求设备');});}
    else if(action==='joint-reboot')return confirm('重启关节演示',`<p>#${id} 会先卸力，再重新连接读取状态。</p>`,()=>{P.state.jointTorque||={};P.state.jointTorque[id]=false;if(id>=30)S.idle=false;});
    else if(action==='joint-id'){const next=Number(document.querySelector('#new-joint-id').value);if(!Number.isInteger(next)||next<0||next>253||JOINTS.some(([id])=>id===next)){P.toast('新 ID 须为 0–253 未占用整数');return;}return confirm('修改 ID 演示',`<p>#${id} → #${next}。实际操作时总线上只保留目标舵机。</p>`,()=>P.toast('已展示修改 ID，未请求设备'));}
    else if(action==='diagnostic-offline'){P.stopDevice();P.state.offline=!P.state.offline;}
    else if(action==='diagnostic-stale'){P.stopDevice();P.state.stale=!P.state.stale;}
    else if(action==='diagnostic-check'){P.state.offline=false;P.state.stale=false;}
    P.render();
  }
  document.addEventListener('keydown',event=>{const item=event.target.closest?.('.servo-summary-main');if(item&&event.key==='Enter'){const id=item.dataset.id;event.preventDefault();item.click();document.querySelector('.servo-summary-main[data-id="'+id+'"]')?.focus({preventScroll:true});}});
  P.devices={jointIds:JOINTS.map(([id])=>id),calibrationProgress,resetForOnboarding};
  P.register('devices',{
    render(subpage){const sub=['servos','imu','diagnostics'].includes(subpage)?subpage:'servos';return `<div class="devices-page">${navigation(sub)}<div class="device-page-content">${sub==='servos'?`${toolbar()}${tabs()}<div class="section-space">${S.tab==='maintenance'?maintenance():S.tab==='tracking'?tracking():advanced()}</div>`:sub==='imu'?(P.imuPage?.render()||''):diagnostics()}</div></div>`;},
    afterRender(sub){
      document.querySelectorAll('[data-device-disclosure]').forEach(panel=>panel.addEventListener('toggle',()=>{S.disclosures[panel.dataset.deviceDisclosure]=panel.open;}));
      if(sub==='imu'){P.imuPage?.afterRender?.();return;}
      document.querySelectorAll('[data-device-field]').forEach(el=>el.addEventListener('change',()=>onField(el)));
      const slider=document.querySelector('#joint-position'),number=document.querySelector('#joint-position-number');
      if(slider&&number){
        let last=0;
        slider.addEventListener('input',()=>{
          number.value=slider.value;
          const value=Number(slider.value),label=document.querySelector('[data-joint-target-label]');
          if(label)label.textContent=value+' 步 / '+((value-2048)*360/4096).toFixed(1)+'°';
          // Keep the original 50 ms follow cadence without rebuilding a dragged slider.
          const now=performance.now();
          if(S.follow&&!disabled()&&held(Number(slider.dataset.id))&&now-last>=50){last=now;moveJoint(Number(slider.dataset.id),value);}
        });
        slider.addEventListener('change',()=>{if(moveJoint(Number(slider.dataset.id),Number(slider.value)))P.render();});
        number.addEventListener('input',()=>slider.value=number.value);
      }
    },handle,
  });
})();
