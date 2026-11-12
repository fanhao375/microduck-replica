(() => {
  const paths = {
    home: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    remote: '<path d="M7 7h10c3 0 5 10 3 11s-4-3-5-3H9c-1 0-3 4-5 3S4 7 7 7Z"/><path d="M8 9v5m-2.5-2.5h5M16 10h.01M18 13h.01"/>',
    camera: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="m8 6 2-3h4l2 3"/><circle cx="12" cy="13" r="4"/>',
    device: '<rect x="5" y="5" width="14" height="14" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M8 2v3m8-3v3M8 19v3m8-3v3M2 8h3m-3 8h3m14-8h3m-3 8h3"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
    log: '<path d="M8 3h11v18H5V6l3-3Z"/><path d="M8 3v4H5m4 4h6m-6 4h6m-6 3h4"/>',
    settings: '<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    power: '<path d="M12 2v10m-6-7a9 9 0 1 0 12 0"/>',
    wifi: '<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0M12 20h.01"/>',
    activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    battery: '<rect x="2" y="7" width="18" height="10" rx="2"/><path d="M22 10v4M5 10v4m3-4v4m3-4v4"/>',
    thermometer: '<path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0ZM12 9v9"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
    imu: '<path d="m12 3 8 5v8l-8 5-8-5V8l8-5Zm0 9 8-4m-8 4L4 8m8 4v9"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 3M4 16l2 3a8 8 0 0 0 13-2"/>',
    play: '<path d="m8 4 12 8-12 8V4Z"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    eye: '<path d="M2 12S6 5 12 5s10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    moon: '<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z"/>',
    shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z"/><path d="m8 12 3 3 5-6"/>',
    upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 17v4h16v-4"/>',
    sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
    message: '<path d="M4 3h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H8l-5 4V4a1 1 0 0 1 1-1Z"/>',
    move: '<path d="M12 2v20M2 12h20M8 6l4-4 4 4M8 18l4 4 4-4M6 8l-4 4 4 4m12-8 4 4-4 4"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m15 9-2 4-4 2 2-4 4-2Z"/>',
    chevronDown: '<path d="m5 9 7 7 7-7"/>',
    volume: '<path d="m3 9 4 0 5-5v16l-5-5H3V9Zm13-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  };
  const servoIds = [10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
  const pages = {};
  const P = window.P = {
    pages,
    state: {
      torque: false, owned: false, offline: false, stale: false, standing: false, selectedJoint: 21,
      batteryVolts: 7.8, boot: 'ready', maintenanceStatus: 'idle', returnShouldFail: false,
      calibrationPending: { joints: [], imu: false }, calibrationStatus: 'idle', calibrationShouldFail: false,
      telemetry: {
        source: 'demo',
        servos: { online: 15, total: 15, ids:[...servoIds], hottest: { c: 33, id: 13, name: '右膝' }, temperaturesC: Object.fromEntries(servoIds.map((id,index)=>[id,30+index%4])) },
        imu: { fresh:true },
        controlHz: 50, cpu: { c: 43.6, throttled: false, limited: false, hot: false },
        pose: { rollDeg: 0, pitchDeg: 0, yawDeg: 0 },
      },
    },
    isSystemBusy() { return ['installing','reconnecting'].includes(P.state.system?.updates); },
    torqueStatus() {
      if (P.state.offline || P.state.stale || P.isSystemBusy() || P.state.boot === 'stopped' && !P.state.owned || P.state.maintenanceStatus === 'failed') return '未知';
      const ids = servoIds;
      const held = ids.filter(id => P.state.jointTorque?.[id] ?? P.state.torque).length;
      return held === 0 ? '已卸力' : held === ids.length ? '全部保持' : '部分保持';
    },
    register(name, page) { pages[name] = page; },
    batteryPercent(volts = P.state.batteryVolts) {
      // Match the existing Studio 2S OCV table; this remains a sample, not telemetry.
      const points = [[4.2,100],[4.15,95],[4.1,89],[4.05,83],[4,77],[3.95,70],[3.9,63],[3.85,55],[3.8,46],[3.75,37],[3.7,28],[3.65,19],[3.6,12],[3.55,8],[3.5,5],[3.4,2],[3.3,1],[3,0]];
      const v = volts / 2;
      if (v >= points[0][0]) return 100;
      for (let i = 1; i < points.length; i++) {
        const [hiV, hiP] = points[i-1], [loV, loP] = points[i];
        if (v >= loV) return Math.round(loP + (v-loV)/(hiV-loV)*(hiP-loP));
      }
      return 0;
    },
    readiness() {
      if (P.isSystemBusy()) return {title:'软件更新进行中',detail:'等待更新完成并重新连接，再继续设备操作。',href:'settings/updates',action:'查看更新进度',kind:'warning'};
      if (P.state.offline || P.state.stale) return {title:'等待设备回读',detail:'连接恢复前无法确认姿态与动力状态。',href:'devices/diagnostics',action:'检查连接',kind:'warning'};
      if (P.state.maintenanceStatus === 'failed') return {title:'交还后尚未确认恢复',detail:'保留当前维护状态，先查看原因。',href:'logs',action:'查看日志',kind:'warning'};
      if (P.state.owned) return {title:'设备维护持有控制权',detail:'先交还主程序，再使用遥控与语音动作。',action:'交还主程序',kind:'warning',actionId:'release-bus'};
      if (P.state.boot === 'stopped') return {title:'主程序未运行',detail:'网页仍可连接，动力状态尚未确认。',href:'logs',action:'查看启动日志',kind:'warning'};
      if (P.state.boot === 'starting') return {title:'主程序启动中',detail:'正在准备总线与姿态采集。',href:'devices/diagnostics',action:'查看启动进度',kind:'warning'};
      if (P.state.boot === 'blocked') return {title:'暂不能站立：IMU 方向待验证',detail:'完成方向验证后，再进入遥控。',href:'devices/imu',action:'验证 IMU',kind:'warning'};
      if (P.state.boot === 'commissioning') return {title:'标定模式',detail:'完成首次启动，再进入遥控。',href:'overview',action:'继续首次启动',kind:'warning'};
      const motionBlock = P.onboarding?.motionBlock();
      if (motionBlock) return {title:'运动准备未完成',detail:motionBlock,href:'overview',action:'继续首次启动',kind:'warning'};
      return {title:P.state.standing?'已站立，可以进入遥控':P.torqueStatus()==='已卸力'?'已卸力，下一步进入遥控':'关节保持中，可查看遥控状态',detail:'站立、行走和动作集中在遥控页。',href:'remote/control',action:'打开遥控',kind:'success'};
    },
    icon(name, size = 18) { return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.device}</svg>`; },
    badge(text, type = '') { return `<span class="badge ${type}">${text}</span>`; },
    toggle(key, on, label = '') { return `<button class="switch ${on ? 'on' : ''}" role="switch" aria-checked="${!!on}" aria-label="${label || key}" data-action="toggle" data-key="${key}"><span></span></button>`; },
    navigate(path) { location.hash = path.startsWith('#') ? path : `#/${path.replace(/^\//, '')}`; },
    toast(message) {
      const el = document.querySelector('#toast');
      el.textContent = message; el.classList.add('visible');
      clearTimeout(P.toastTimer); P.toastTimer = setTimeout(() => el.classList.remove('visible'), 2600);
    },
    openModal(title, body, confirmLabel = '确认', onConfirm = () => {}) {
      const dialog = document.querySelector('#modal');
      dialog.querySelector('.modal-title').textContent = title;
      dialog.querySelector('.modal-content').innerHTML = body;
      dialog.querySelector('#modal-confirm').textContent = confirmLabel;
      dialog.querySelector('#modal-confirm').onclick = () => { if (onConfirm(dialog) !== false) dialog.close(); };
      dialog.showModal();
    },
    download(filename, content, type = 'application/json') {
      const url = URL.createObjectURL(new Blob([content], { type }));
      const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    escape(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },
  };
})();
