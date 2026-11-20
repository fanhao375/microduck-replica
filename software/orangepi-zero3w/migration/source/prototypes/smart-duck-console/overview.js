(() => {
  const P = window.P;
  const esc = value => P.escape(value);
  const valueOrDash = (value, suffix = '') => Number.isFinite(value) ? `${value}${suffix}` : '—';
  const modeName = () => P.state.owned ? '设备维护' : ({ ready:'运动模式', commissioning:'标定模式', starting:'启动中', stopped:'未运行', blocked:'待验证' }[P.state.boot] || '未知');
  const row = (title, value) => `<div class="field-row"><strong>${title}</strong><span class="overview-status-value">${value}</span></div>`;
  function snapshot() {
    const telemetry = P.state.telemetry || {};
    const disconnected = P.state.offline || P.state.stale || P.isSystemBusy?.();
    const servoUnknown = disconnected || P.state.maintenanceStatus === 'failed' || (['starting','stopped'].includes(P.state.boot) && !P.state.owned);
    return { telemetry, disconnected, servoUnknown, mainUnknown:disconnected || P.state.boot !== 'ready' || P.state.owned };
  }
  function posture(mainUnknown) {
    if (mainUnknown) return '等待姿态回读';
    if (P.torqueStatus() === '已卸力') return '已卸力';
    const remote = P.state.remote;
    if (remote?.posture === '行走中') return Object.values(remote.axes || {}).some(value => Math.abs(value) >= .1) ? '行走中' : '准备好走';
    if (remote?.posture === '坐姿') return '坐姿';
    if (P.state.standing) return '站立';
    return '关节保持';
  }
  function imuSummary(mainUnknown) {
    const i = P.state.devices?.imu;
    if (P.state.owned) return ['—','维护持有总线'];
    if (P.state.offline || P.state.stale) return ['—','等待有效姿态回读'];
    if (P.state.boot === 'blocked' || i?.coarse?.active || i?.mount?.verified === false) return ['待验证',i?.coarse?.active ? '安装方向标定中' : '安装方向尚未确认'];
    if (mainUnknown) return ['—','等待主程序姿态采集'];
    if (i?.fine?.phase === 'collect') return ['采集中',`${Object.values(i.fine.done || {}).filter(Boolean).length} / 5 项已记录`];
    if (i?.fine?.phase === 'short') return ['待补采','已记录方向保留'];
    if (P.state.calibrationPending?.imu) return ['待提交','精校结果尚未写入'];
    return i?.mount?.verified === true ? ['正常','安装方向已确认'] : ['—','安装方向状态未提供'];
  }
  function controllerSummary(disconnected) {
    const remote = P.state.remote;
    if (disconnected || !remote) return '<span class="muted">等待手柄状态</span>';
    const pads = Array.isArray(remote.pads) ? remote.pads : [];
    const active = pads.find(pad => pad.connected);
    const status = active ? `${esc(active.name || '手柄')} · 已连接` : pads.length ? '已配对 · 未连接' : '暂无配对记录';
    const result = String(remote.estopResult || '');
    const estop = remote.estopStep >= 0 ? '急停流程进行中' : /完成/.test(result) ? '急停流程已演示' : /失败|无效/.test(result) ? '急停未通过' : '急停尚未核对';
    return `<a class="link" href="#/remote/gamepad">${status}</a><small>${estop}</small>`;
  }
  function modelSummary(mainUnknown) {
    const system = P.state.system;
    if (!system) return '<span class="muted">未提供模型配置</span>';
    const selected = system.slots?.walk, loaded = system.loaded?.walk;
    const status = mainUnknown ? '加载待回读' : !loaded ? '未加载' : selected !== loaded ? '选用后待加载' : system.executing === 'walk' ? '执行中' : '已加载 · 未执行';
    return `<a class="link" href="#/models">${esc(system.modelName || '未命名模型')}</a><small>${status}</small>`;
  }
  function attention({telemetry, disconnected, servoUnknown}) {
    if (disconnected) return '';
    const items = [], cpu = telemetry.cpu;
    if (cpu?.throttled === true) items.push(['主板正在降频','查看温度与运行日志','logs']);
    else if (cpu?.hot === true) items.push(['主板温度偏高','查看散热与运行状态','devices/diagnostics']);
    else if (cpu?.limited === true) items.push(['主板频率受限','当前信息未指向高温','devices/diagnostics']);
    const servos = telemetry.servos;
    if (!servoUnknown && Number.isFinite(servos?.online) && Number.isFinite(servos?.total) && servos.online < servos.total) items.push([`舵机回读不完整：${servos.online} / ${servos.total}`,'查看各关节的回读与错误','devices/diagnostics']);
    const battery = P.state.system?.battery;
    if (battery && battery.preset !== 'bench' && Number.isFinite(P.state.batteryVolts)) {
      const percent = P.batteryPercent(P.state.batteryVolts + Number(battery.offset || 0));
      if (percent <= battery.red || P.state.batteryVolts <= battery.floor) items.push(['电量已到提醒阈值','检查供电后再继续动作','settings/battery']);
      else if (percent <= battery.warn && P.state.system?.lowBattery !== false) items.push(['电量偏低','查看电压与电池配置','settings/battery']);
    }
    if (!items.length) return '';
    return `<section class="card overview-attention section-space"><div class="card-head"><h2>运行关注</h2>${items.length>2?`<a class="link" href="#/logs">另有 ${items.length-2} 项</a>`:''}</div><div class="card-body">${items.slice(0,2).map(([title,detail,href])=>`<a class="overview-attention-row" href="#/${href}"><span>${P.icon('info',16)}</span><div><strong>${esc(title)}</strong><small>${esc(detail)}</small></div>${P.icon('chevron',14)}</a>`).join('')}</div></section>`;
  }
  function render() {
    const snap = snapshot(), {telemetry, disconnected, servoUnknown, mainUnknown:unavailable} = snap;
    const servo = telemetry.servos || {}, cpu = telemetry.cpu || {}, [imuValue, imuDetail] = imuSummary(unavailable);
    const hottest = servo.hottest;
    const hottestText = hottest && Number.isFinite(hottest.c) ? `最热 ${hottest.c}°C${hottest.name ? ' · '+hottest.name : Number.isInteger(hottest.id) ? ' · #'+hottest.id : ''}` : '最热温度未提供';
    const cpuText = cpu.throttled === true ? '正在降频' : cpu.hot === true ? '温度偏高' : cpu.limited === true ? '频率受限（非高温结论）' : cpu.throttled === false ? '当前未降频' : '降频状态未提供';
    const total = Number.isFinite(servo.total) ? servo.total : null;
    const metrics = [
      ['device','舵机在线',servoUnknown?'—':valueOrDash(servo.online)+(total!==null?`<span>/ ${total}</span>`:''),servoUnknown?'等待有效回读':hottestText],
      ['imu','IMU 姿态',imuValue,imuDetail],
      ['activity','控制频率',unavailable?'—':valueOrDash(telemetry.controlHz,'<span>Hz</span>'),unavailable?'等待主程序采集':'总线采集'],
      ['thermometer','主板温度',disconnected?'—':valueOrDash(cpu.c,'<span>°C</span>'),disconnected?'等待设备连接':cpuText],
    ];
    const bench = P.state.system?.battery?.preset === 'bench';
    const batteryText = servoUnknown || !Number.isFinite(P.state.batteryVolts) ? '—' : `${bench?'台架供电':`约 ${P.batteryPercent(P.state.batteryVolts+Number(P.state.system?.battery?.offset||0))}%`} <span class="muted">/ ${P.state.batteryVolts.toFixed(1)} V</span>`;
    return `${P.onboarding.card()}
    <div class="metric-grid section-space">${metrics.map(([i,t,v,d])=>`<div class="metric"><div class="metric-label">${P.icon(i,15)}${t}</div><div class="metric-value">${v}</div><small>${esc(d)}</small></div>`).join('')}</div>
    <div class="grid-two section-space overview-grid">
      <div class="card overview-duck-card"><div class="card-head"><h2>${P.icon('imu')}小鸭姿态</h2><div class="overview-duck-tools">${P.badge('姿态演示')}<button class="btn small" data-overview-duck-reset disabled>回正视角</button></div></div><div class="card-body"><div class="duck-stage overview-duck-stage" data-overview-duck-stage role="img" aria-label="原版 3D 小鸭，固定站姿演示；鼠标拖动旋转，手机双指旋转" aria-busy="true"><div class="overview-duck-status" data-overview-duck-status role="status"><span>3D 模型加载中…</span></div></div><div class="pose-data">${['横滚','俯仰','偏航'].map((x,i)=>`<div><span>${x}</span><b>${unavailable?'—':valueOrDash([telemetry.pose?.rollDeg,telemetry.pose?.pitchDeg,telemetry.pose?.yawDeg][i],'°')}</b></div>`).join('')}</div></div></div>
      <section class="card overview-device-state"><div class="card-head"><h2>设备状态</h2></div><div class="card-body"><div class="divider"></div>${row('运行模式',modeName())}${row('动力状态',P.badge(P.torqueStatus(),P.torqueStatus()==='未知'?'warning':P.torqueStatus()==='已卸力'?'':'success'))}${row('当前姿态',posture(unavailable))}${row('控制权',P.state.owned?'设备维护':'主程序')}${row('行走模型',modelSummary(unavailable))}${row(bench?'供电':'电池',batteryText)}${row('实体手柄',controllerSummary(disconnected))}</div></section>
    </div>${attention(snap)}`;
  }
  P.register('overview', {render});
})();
