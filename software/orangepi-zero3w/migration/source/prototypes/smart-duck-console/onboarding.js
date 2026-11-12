// A guided demo workflow; completion is derived from prototype results, never manual checkmarks.
(() => {
  const P = window.P;
  const KEY = 'smart-duck-first-start-v1';
  const fresh = () => ({ started:false, checked:false, controlChoice:'web', standSeen:false, stopVerified:false, moved:false, complete:false, committedSnapshot:null, checkError:'' });
  P.state.onboarding = fresh();
  const state = () => P.state.onboarding;
  const imu = () => P.state.devices?.imu;
  const zeroes = () => P.devices.calibrationProgress();
  const pending = () => P.state.calibrationPending;
  const busy = () => P.isSystemBusy() || ['handoff','verifying'].includes(P.state.calibrationStatus);
  const online = () => !P.state.offline && !P.state.stale && !P.isSystemBusy();
  const fineDone = () => !!imu()?.result?.complete && ['still','fwd','back','left','right'].every(key => imu().fine.done[key]);
  const modelReady = () => !!P.state.system?.slots.walk && P.state.system.loaded.walk === P.state.system.slots.walk && !P.state.system.modelError;
  const snapshot = () => JSON.stringify({ calibrated:P.state.devices.calibrated, directions:P.state.devices.directions, mount:imu()?.mount, profile:imu()?.savedProfile, fine:imu()?.fine.done, result:imu()?.result });
  const committed = () => !!state().committedSnapshot && state().committedSnapshot === snapshot() && !pending().joints.length && !pending().imu && P.state.calibrationStatus === 'complete';

  function checkProblem() {
    if (!online()) return '等待设备连接与有效回读。';
    if (['starting','stopped'].includes(P.state.boot)) return '主程序尚未准备好，请查看运行诊断。';
    const servos = P.state.telemetry?.servos;
    const ids = servos?.ids;
    if (servos?.online !== 15 || servos?.total !== 15 || !Array.isArray(ids) || ids.length !== 15 || new Set(ids).size !== 15 || !P.devices.jointIds.every(id => ids.includes(id))) return '舵机回读不完整，请检查连接与关节编号。';
    if (P.state.telemetry?.imu?.fresh !== true) return 'IMU 暂无有效数据，请检查连接。';
    const battery = P.state.system?.battery;
    if (!Number.isFinite(P.state.batteryVolts) || !battery || P.state.batteryVolts <= battery.floor) return '供电电压不足或未知，请先检查供电。';
    return '';
  }
  function commitBlock() {
    if (imu()?.coarse.active || imu()?.mount.verified === false) return '先完成 IMU 安装方向验证。';
    if (!state().started) return '';
    if (!zeroes().complete) return '先完成 15 颗舵机的零位标定。';
    if (!fineDone()) return '先完成站着校准，再统一提交。';
    return '';
  }
  function motionBlock(action = '') {
    if (!online() || busy()) return '等待设备连接、提交与有效回读。';
    if (P.state.owned || P.state.maintenanceStatus !== 'idle') return '先交还主程序并确认恢复。';
    if (imu()?.coarse.active || imu()?.mount.verified === false) return '先完成 IMU 安装方向验证。';
    if (P.state.boot !== 'ready') return '先完成标定并写进主程序。';
    if (!state().started) return '';
    if (!state().checked || checkProblem()) return '先通过首次启动的设备检查。';
    if (!zeroes().complete || !committed()) return '先完成标定并确认提交结果。';
    if (!modelReady()) return '行走模型尚未加载，请先检查模型状态。';
    if (['exp-walk','move'].includes(action) && !state().stopVerified) return '先站起来并验证卸力，再开始走。';
    return '';
  }
  function invalidate() {
    Object.assign(state(), { committedSnapshot:null, standSeen:false, stopVerified:false, moved:false, complete:false });
  }
  function sync() {
    const w = state();
    if (!w.started) return;
    if (w.committedSnapshot && w.committedSnapshot !== snapshot()) invalidate();
    if (!online() || P.state.owned || !modelReady()) {
      // A stale stop result must not unlock a new attempt after a connection/model change.
      w.standSeen = false; w.stopVerified = false; w.moved = false; w.complete = false;
    }
    if (w.moved && w.stopVerified && !motionBlock()) w.complete = true;
  }
  function save() {
    if (!state().started) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ workflow:state(), devices:{ calibrated:P.state.devices.calibrated, directions:P.state.devices.directions, imu:imu() }, pending:pending(), calibrationStatus:P.state.calibrationStatus, boot:P.state.boot }));
    } catch { /* Navigation still works when browser session storage is unavailable. */ }
  }
  function restore() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (!saved?.workflow?.started) return;
      if (!saved.devices?.imu || !['web','pad'].includes(saved.workflow.controlChoice)) { reset(); return; }
      const restored = saved.devices.imu;
      if (restored.version !== 1 || typeof restored.mount?.verified !== 'boolean' || typeof restored.coarse?.active !== 'boolean' || !['idle','still','fwd','confirm','verifyLevel','verifyFwd','done'].includes(restored.coarse.phase) || !['idle','collect','short','done'].includes(restored.fine?.phase) || !['still','fwd','back','left','right'].every(key => typeof restored.fine.done?.[key] === 'boolean')) { reset(); return; }
      Object.assign(P.state.onboarding, fresh(), saved.workflow, { standSeen:false });
      Object.assign(P.state.devices, saved.devices);
      P.state.devices.imu.standing = false;
      P.state.calibrationPending = saved.pending || { joints:[], imu:false };
      P.state.calibrationStatus = ['handoff','verifying'].includes(saved.calibrationStatus) ? 'failed' : saved.calibrationStatus;
      P.state.boot = saved.boot;
      // Restore progress, never torque, a held pose, or a movement command.
      Object.assign(P.state, { torque:false, standing:false, jointTorque:{} });
    } catch { /* A partial/older demo session starts from a clean first-start card. */ }
  }
  function reset() {
    P.stopAllOutputs?.();
    P.devices.resetForOnboarding();
    P.imuPage.resetForOnboarding();
    Object.assign(P.state, { calibrationStatus:'idle', calibrationCommitted:false, boot:'commissioning', torque:false, standing:false, jointTorque:{} });
    P.state.onboarding = { ...fresh(), started:true };
    save();
  }
  const step = (id,index,title,detail,label,action,route = '') => ({id,index,title,detail,label,action,route});
  function current() {
    const w = state();
    if (!w.started) return step('welcome',0,'让鸭子第一次走起来','按顺序完成检查、标定和首次行走。','开始首次启动','onboarding-start');
    if (busy()) return {...step('busy',4,'正在处理，请稍等','完成重启与回读后继续。','等待结果',''),disabled:true};
    if (!online()) return step('connection',0,'连接设备','恢复连接后，保留已完成的标定继续。','检查连接','onboarding-continue','settings/network');
    if (!w.checked || checkProblem()) return step('check',1,'检查设备',w.checkError || checkProblem() || '确认 15 颗舵机、IMU 和供电状态。',P.state.owned?'交还并检查':'检查设备',P.state.owned?'onboarding-release':'onboarding-check');
    const progress = zeroes();
    if (!progress.complete) return step('servos',2,'校准舵机零位',`已完成 ${progress.done} / 15。照零位参考摆正，逐颗点“中位”。`,'前往舵机标定','onboarding-continue','devices/servos');
    if (P.state.owned || P.state.maintenanceStatus !== 'idle') return step('handoff',2,'交还主程序','零位已完成，扶稳机身后卸力并交还。','交还主程序','onboarding-release','devices/servos');
    if (!imu()?.mount.verified || imu()?.coarse.active) return step('imu-direction',3,'确认 IMU 安装方向','先做静止与前倾采样，再交给主程序验证。','前往 IMU 校准','onboarding-continue','devices/imu');
    if (!fineDone()) return step('imu-fine',3,'完成站姿校准','使能站姿后，采集静止与前后左右五项。','继续站姿校准','onboarding-continue','devices/imu');
    if (!committed()) return step('commit',4,'统一写进主程序',P.state.calibrationStatus === 'failed'?'上次提交未确认，原标定保留，可以重试。':'校准已齐全，卸力后统一保存并读回。','预览并提交','onboarding-commit');
    if (!modelReady()) return step('models',4,'确认行走模型','需要“主程序已加载”，仅“已选用”还不能走。','查看行走模型','onboarding-continue','models/library');
    if (!w.stopVerified) {
      if (w.controlChoice === 'pad') return step('stop-pad',5,'确认手柄能急停','配对、核对按键，再完成手柄急停测试。','前往手柄测试','onboarding-continue','remote/gamepad');
      if (P.state.remote.paired) return step('disconnect',5,'使用网页遥控','先断开实体手柄，配对记录会保留。','前往网页遥控','onboarding-continue','remote/control');
      return w.standSeen ? step('unload',5,'确认能卸力','已经站起。扶稳躯干，点右上角“全局卸力”。','全局卸力','onboarding-unload','remote/control') : step('stop-web',5,'先确认能停','扶稳躯干，先站起来，再验证全局卸力。','前往验证停止','onboarding-continue','remote/control');
    }
    if (!w.complete) return step('walk',6,'第一次迈步','站起来 → 等站稳 → 开始走 → 小幅推摇杆。','前往首次行走','onboarding-continue','remote/control');
    return step('complete',6,'首次启动已完成','之后开机检查正常，就可以进入遥控。','打开遥控','onboarding-continue','remote/control');
  }
  function choiceMarkup(s) {
    if (s.index !== 5 || state().complete) return '';
    return `<div class="onboarding-choice" role="group" aria-label="首次启动控制方式">${[['web','网页遥控'],['pad','实体手柄（可选）']].map(([value,label]) => `<label><input type="radio" name="onboarding-control" value="${value}" data-action="onboarding-control" ${state().controlChoice===value?'checked':''}>${label}</label>`).join('')}</div>`;
  }
  function primary(s) {
    return `<button class="btn primary" data-action="${s.action}" ${s.disabled?'disabled':''}>${P.escape(s.label)}${!s.disabled?P.icon('arrow',14):''}</button>`;
  }
  function card() {
    const s = current();
    return `<section class="onboarding-card" data-onboarding-card aria-label="首次启动"><div class="onboarding-symbol">${P.icon(state().complete?'check':'compass',23)}</div><div class="onboarding-copy"><div class="onboarding-caption"><span>首次启动</span><span>${state().complete?'已完成':`${s.index+1} / 7`}</span></div><h2>${P.escape(s.title)}</h2><p>${P.escape(s.detail)}</p>${choiceMarkup(s)}</div>${primary(s)}</section>`;
  }
  function contextMarkup(page, sub = '') {
    if (!active() || page === 'overview') return '';
    const s = current(), route = `${page}/${sub || ({devices:'servos',remote:'control',models:'library',settings:'network'}[page] || '')}`;
    const samePage = s.route === route;
    const handledHere = samePage || s.id === 'unload';
    return `<aside class="onboarding-context" data-onboarding-context aria-label="首次启动当前步骤"><div><span>首次启动 · ${s.index+1} / 7</span><strong>${P.escape(s.title)}</strong><p>${P.escape(s.detail)}</p>${choiceMarkup(s)}</div>${handledHere?'':primary(s)}</aside>`;
  }
  function refreshContext() {
    const [page,sub] = location.hash.replace(/^#\/?/,'').split('/');
    const context = document.querySelector('[data-onboarding-context]');
    if (context) context.outerHTML = contextMarkup(page,sub);
    const home = document.querySelector('[data-onboarding-card]');
    if (home) home.outerHTML = card();
  }
  function record(event, source = '') {
    const w = state();
    if (!w.started) return;
    if (event === 'calibration-changed') {
      // A changed mechanical zero changes HOME; its old standing correction is no longer usable.
      if (source === 'servo') P.imuPage.invalidateFineForOnboarding();
      invalidate();
    }
    if (event === 'committed') {
      if (commitBlock()) return;
      w.committedSnapshot = snapshot(); w.standSeen = false; w.stopVerified = false;
    }
    if (event === 'stand' && online() && !motionBlock() && P.state.standing && P.torqueStatus() === '全部保持') w.standSeen = true;
    const matchingStop = event === 'unload' && w.controlChoice === 'web' || event === 'pad-estop' && w.controlChoice === 'pad' && P.state.remote.paired && P.state.remote.estopResult === '流程演示完成';
    if (matchingStop && w.standSeen && online() && !motionBlock() && P.torqueStatus() === '已卸力') {
      w.stopVerified = true; w.standSeen = false;
    }
    if (event === 'move' && w.stopVerified && online() && !motionBlock('move') && P.state.standing && P.torqueStatus() === '全部保持' && P.state.remote.mode === '走路' && P.state.remote.posture === '行走中' && ['lx','ly','rx'].some(axis => Math.abs(P.state.remote.axes[axis]) >= .1)) {
      const first = !w.complete;
      w.moved = true; w.complete = true;
      if (first) P.toast('首次迈步流程已演示完成');
    }
    sync(); save();
    // Pointer input may call record: update only the guide, retaining joystick capture.
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(refreshContext);
  }
  function handle(action, target = {}) {
    if (!action.startsWith('onboarding-')) return false;
    if (action === 'onboarding-start') reset();
    else if (action === 'onboarding-check') {
      state().checkError = P.state.owned ? '先交还主程序，再检查设备。' : checkProblem(); state().checked = !state().checkError;
    } else if (action === 'onboarding-control') {
      if (!['web','pad'].includes(target.value)) return true;
      Object.assign(state(), { controlChoice:target.value, standSeen:false, stopVerified:false, moved:false, complete:false });
    } else if (action === 'onboarding-continue') {
      const s = current();
      if (s.route === 'devices/servos') P.deviceTab('maintenance'); else if (s.route) P.navigate(s.route);
    } else if (action === 'onboarding-release') { P.releaseBus?.(); return true; }
    else if (action === 'onboarding-commit') { P.openCalibrationCommit(); return true; }
    else if (action === 'onboarding-unload') { P.emergency?.(); return true; }
    sync(); save(); P.render(); return true;
  }
  const active = () => state().started && !state().complete;
  P.onboarding = { active, sync, current, card, contextMarkup, handle, record, motionBlock, commitBlock, reset, save };
  restore();
})();
