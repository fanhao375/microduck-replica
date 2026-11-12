// IMU workflow adapted from the original Studio page; samples and results here are explicit demo fixtures.
(() => {
  const P = window.P;
  const STEPS = ['still', 'fwd', 'back', 'left', 'right'];
  const STEP = {
    still: { label: '静止站好', short: '静止', hint: '平桌上静止 3 秒，不要碰机身。' },
    fwd: { label: '前倾', short: '前', hint: '提起躯干，头那边向下倾约 15°，停 1 秒。' },
    back: { label: '后倾', short: '后', hint: '提起躯干，尾部向下倾约 15°，停 1 秒。' },
    left: { label: '左倾', short: '左', hint: '鸭子自己的左侧向下倾约 15°，停 1 秒。' },
    right: { label: '右倾', short: '右', hint: '鸭子自己的右侧向下倾约 15°，停 1 秒。' },
  };
  const PROFILE = { legacy: '自动识别（现有板卡）', qinmo: 'Qinmo · Feetech · ÷16.4 °/s' };
  const radians = degrees => degrees * Math.PI / 180;
  const DEMO_GRAVITY = [Math.sin(radians(8)), 0, -Math.cos(radians(8))];
  const DEMO_CORRECTION = [Math.cos(radians(3.75)), 0, Math.sin(radians(3.75)), 0];
  const esc = value => P.escape(value);
  const badge = (text, kind = '') => P.badge(text, kind);
  const off = value => value ? 'disabled' : '';
  const button = (text, action, kind = '', attrs = '') => `<button type="button" class="btn ${kind}" data-action="${action}" ${attrs}>${text}</button>`;
  const row = (label, value) => `<div class="field-row"><span>${label}</span><strong>${value}</strong></div>`;
  const card = (title, content, tail = '', className = '') => `<section class="card imu-card ${className}"><div class="card-head"><h2>${title}</h2>${tail}</div><div class="card-body">${content}</div></section>`;
  const freshFine = () => ({ phase: 'idle', done: Object.fromEntries(STEPS.map(key => [key, false])), selected: 'still', message: '' });
  const freshCoarse = () => ({ phase: 'idle', active: false, confirmed: false, backup: null });
  function state() {
    P.state.devices ||= {};
    if (P.state.devices.imu?.version !== 1) {
      P.state.devices.imu = { version: 1, mount: { verified: true, source: 'existing' }, profile: 'legacy', savedProfile: 'legacy', standing: false, coarse: freshCoarse(), fine: freshFine(), result: null, muted: false, open: { chip: false, diagnostics: false, result: false } };
    }
    return P.state.devices.imu;
  }
  const pending = () => P.state.calibrationPending ||= { joints: [], imu: false };
  const onboardingActive = () => !!P.onboarding?.active?.();
  const committing = () => ['handoff', 'verifying'].includes(P.state.calibrationStatus);
  const missing = () => STEPS.filter(key => !state().fine.done[key]);
  const doneCount = () => STEPS.length - missing().length;
  const labels = keys => keys.map(key => STEP[key].label).join('、');
  function commonBlock({ coarse = false } = {}) {
    if (P.isSystemBusy?.()) return '软件更新进行中，等待重新连接后再继续。';
    if (P.state.offline) return '设备未连接，恢复连接后再继续。';
    if (P.state.stale) return '姿态回读已过期，等待有效读数。';
    if (P.state.maintenanceStatus === 'failed') return '维护交还尚未确认，先查看恢复结果。';
    if (P.state.maintenanceStatus === 'returning') return '正在交还控制权，请稍等。';
    if (committing()) return '标定提交进行中，请等待当前提交完成。';
    if (P.state.owned) return '设备维护持有总线，先交还主程序。';
    if (P.state.boot === 'stopped') return '主程序未运行，先检查启动状态。';
    if (P.state.boot === 'starting') return '主程序启动中，等待姿态采集就绪。';
    if (P.state.boot === 'blocked' && !coarse) return '安装方向待验证，先完成粗校与主程序验证。';
    // Commissioning keeps normal motion blocked while allowing the IMU hold-home and fine steps.
    return '';
  }
  function fineBlock() {
    const i = state();
    return commonBlock() || (i.coarse.active ? '重新标定安装方向中，先完成验证或取消恢复。' : !i.mount.verified ? '安装方向尚未验证，先做粗校。' : !i.standing || !P.state.torque ? '先使能站姿，再开始站着校准。' : '');
  }
  function readable() { return !(P.isSystemBusy?.() || P.state.offline || P.state.stale || P.state.owned || ['stopped', 'starting'].includes(P.state.boot)); }
  function details(key, title, content) {
    return `<details class="imu-details" data-imu-details="${key}" ${state().open[key] ? 'open' : ''}><summary>${title}</summary>${content}</details>`;
  }
  function directionPad() {
    const f = state().fine;
    // Original Studio DirPad geometry and state classes; keep the demo's direction selection.
    const C = 100, R = 80, MAX = 32;
    const radius = tilt => Math.min(tilt, MAX) / MAX * R;
    const point = (tilt, direction) => [C - radius(tilt) * Math.sin(radians(direction)), C - radius(tilt) * Math.cos(radians(direction))];
    const directions = { fwd:0, back:180, left:90, right:-90 };
    const collecting = f.phase === 'collect';
    const next = collecting && f.done.still ? STEPS.find(key => !f.done[key]) : null;
    const live = collecting && f.done.still ? f.live : null;
    const p = live && live.t != null ? point(live.t, live.d ?? 0) : [C, C];
    const attrs = key => `role="button" tabindex="0" data-action="imu-select-direction" data-direction="${key}" aria-pressed="${f.selected === key}" aria-label="${STEP[key].label}${f.done[key] ? '，已记录' : ''}"`;
    return `<svg class="imu-pad" viewBox="0 0 200 200" role="group" aria-label="静止与四个倾斜方向，先静止，四方向顺序不限">
      <circle cx="${C}" cy="${C}" r="${R + 6}" class="pad-bg" />
      ${[12,30].map(tilt => `<circle cx="${C}" cy="${C}" r="${radius(tilt)}" class="pad-ring" />`).join('')}
      ${Object.entries(directions).map(([key, direction]) => {
        const [x,y] = point(20,direction), [lx,ly] = point(33,direction);
        const cls = f.done[key] ? 'done' : key === next ? 'next' : key === f.cand && f.inZone ? 'hot' : '';
        return `<g class="pad-dot ${cls}" ${attrs(key)}><circle cx="${x}" cy="${y}" r="13" />${f.done[key] ? `<path d="M${x - 5.5},${y} l4,4 l7,-8" class="tick" />` : ''}<text x="${lx}" y="${ly + 4}" class="pad-lbl">${STEP[key].short}</text></g>`;
      }).join('')}
      <g class="pad-dot center ${f.done.still ? 'done' : collecting ? 'next' : ''}" ${attrs('still')}><circle cx="${C}" cy="${C}" r="11" />${f.done.still ? `<path d="M${C - 5},${C} l3.5,3.5 l6.5,-7.5" class="tick" />` : ''}</g>
      ${live && f.hold > 0 ? `<circle cx="${p[0]}" cy="${p[1]}" r="12" class="pad-hold" style="stroke-dasharray:${(f.hold * 75.4).toFixed(1)} 76" transform="rotate(-90 ${p[0]} ${p[1]})" />` : ''}
      ${live ? `<circle cx="${p[0]}" cy="${p[1]}" r="6" class="pad-live${f.over ? ' over' : f.inZone ? ' on' : ''}" />` : ''}
    </svg>`;
  }
  function stagePanel(key) {
    return `<div class="imu-view" data-imu-stage="${key}" role="group" aria-label="${key === 'before' ? '校准前' : '校准后'}的演示 3D 姿态"><div class="imu-stage-read ${key === 'after' ? 'after' : ''}"><span data-imu-stage-label="${key}">${key === 'before' ? '机身倾角' : '校准后'}</span><b class="num" data-imu-angle="${key}">—</b></div>${key === 'before' ? '<span class="imu-cue" data-imu-cue></span>' : ''}<span class="imu-note" data-imu-note="${key}">演示读数</span></div>`;
  }
  function stageCard() {
    const compare = !!state().result;
    return `<div class="imu-stage-wrap">${card('IMU 姿态', `<div class="imu-stage ${compare ? 'duo' : ''}">${stagePanel('before')}${compare ? stagePanel('after') : ''}</div><p class="imu-diff" data-imu-summary>演示姿态，不跟随真实设备。</p>`, `<div class="imu-stage-tools">${badge(compare ? '示例对比' : '姿态演示')}<button type="button" class="btn small" data-imu-reset="all">回正视角</button></div>`, 'imu-stage-card')}</div>`;
  }
  function statusCard() {
    const i = state(), verified = i.mount.verified && P.state.boot !== 'blocked' && !i.coarse.active;
    return card('控制与安装方向', `${row('安装方向', badge(verified ? '方向已确认' : '待验证', verified ? 'success' : 'warning'))}${row('当前芯片', PROFILE[i.savedProfile])}${row('姿态来源', readable() ? '演示数据' : '等待有效回读')}${row('角速度单位', readable() ? '°/s' : '—')}${details('mount', '当前方向说明', onboardingActive()?'<p>先验证安装方向，再使能站姿完成静止与四方向精校，最后统一提交。</p>':'<p>初始使用现有有效安装方向，便于直接预览站着校准。重新标定或更换板卡后，须重新验证。</p>')}`);
  }
  function coarseCard() {
    const i = state(), c = i.coarse, blocked = commonBlock({ coarse: true });
    let body;
    if (!c.active) {
      body = `<p class="imu-small">静止与前倾用于初次安装方向粗校，之后由主程序验证。</p><div class="imu-actions">${button(!i.mount.verified&&onboardingActive()?'开始标定方向':'重新标定方向', 'imu-coarse-begin', '', off(!!blocked))}</div>`;
    } else {
      const content = {
        still: ['扶住躯干，摆正并静止', '正式采集需静止 3 秒；当前只演示本步记录。', '模拟采样：完成静止', 'imu-coarse-sample'],
        fwd: ['整个躯干向前倾 15–30°', '头那边朝下，不要只动头；正式采集保持 3 秒。', '模拟采样：完成前倾', 'imu-coarse-sample'],
        confirm: ['核对安装方向', '示例采样已完成，核对后进入主程序验证演示。', '交给主程序验证 · 演示', 'imu-coarse-verify'],
        verifyLevel: ['主程序验证：先回正', '正式验证先检查摆正，再在 20 秒内前倾 15–30°。', '演示验证：已摆正', 'imu-coarse-verify-level'],
        verifyFwd: ['主程序验证：前倾', '托住整个躯干向前倾，保持稳定。', '演示验证：已前倾', 'imu-coarse-verify-fwd'],
      }[c.phase];
      body = `<div class="imu-coarse-step"><h3>${content[0]}</h3><p class="imu-small">${content[1]}</p>${c.phase === 'confirm' ? `<label class="imu-check"><input type="checkbox" data-imu-field="coarse-confirm" ${c.confirmed ? 'checked' : ''} ${off(committing())}> 已核对示例方向，了解正式流程需要主程序验证</label>` : ''}<div class="imu-actions">${button(content[2], content[3], 'primary', off(!!blocked || c.phase === 'confirm' && !c.confirmed))}${c.phase === 'fwd' ? button('重采静止', 'imu-coarse-restill', '', off(!!blocked)) : ''}${button('取消并恢复', 'imu-coarse-cancel', '', off(committing()))}</div></div>`;
    }
    if (c.phase === 'done') body = '<p class="imu-small">粗校与主程序验证流程已演示完成。可继续站姿使能与站着校准。</p><div class="imu-actions">' + button(!i.mount.verified&&onboardingActive()?'开始标定方向':'重新标定方向', 'imu-coarse-begin', '', off(!!blocked)) + '</div>';
    return card('IMU 安装方向', body, badge(c.active ? '重新标定中' : i.mount.verified && P.state.boot !== 'blocked' ? '现有方向' : '待验证', c.active || P.state.boot === 'blocked' ? 'warning' : ''), 'imu-coarse');
  }
  function stanceCard() {
    const i = state();
    const block = commonBlock() || (i.coarse.active ? '先结束重新标定方向。' : !i.mount.verified ? '先完成安装方向验证。' : '');
    return card('站姿', `<div class="imu-stance-row">${button('站姿使能', 'imu-stance-enable', 'primary', off(!!block || i.standing))}${button('失能（卸力）', 'imu-relax', 'danger')}<span>现在：<b>${i.standing && P.state.torque ? '站着 · 始终扶稳' : '未使能站姿'}</b></span></div><p class="imu-small">标定站姿只保持关节，不提供平衡。</p>`);
  }
  function collectCard() {
    const i = state(), f = i.fine, block = fineBlock(), selected = STEP[f.selected];
    const primary = f.phase === 'collect' ? button('<span class="imu-rec-dot"></span>采集中…（点这里结束）', 'imu-collect-end', 'imu-big rec') : f.phase === 'short' ? button('接着采', 'imu-collect-resume', 'imu-big primary', off(!!block)) : button(f.phase === 'idle' ? '开始采集' : '重新采集', f.phase === 'idle' ? 'imu-collect-start' : 'imu-collect-restart', 'imu-big primary', off(!!block));
    const canSample = f.phase === 'collect' && !block && !f.done[f.selected] && (f.selected === 'still' || f.done.still);
    let words = f.phase === 'short' ? `还差：${labels(missing())}。已记录步骤保留。` : f.phase === 'done' ? '五项演示记录齐全，可查看校准前后示例。' : f.phase === 'collect' ? !f.done.still ? '先完成静止，再采四个方向。' : '前、后、左、右顺序不限。' : '先站姿使能；静止后采四方向，顺序不限。';
    if (f.message && f.phase === 'short') words = `${f.message} 还差：${labels(missing())}。`;
    return card('站着校准', `<div class="imu-collect-intro"><p>${words}</p><span>${doneCount()} / 5</span></div><div class="imu-progress" role="progressbar" aria-label="演示步骤完成数" aria-valuemin="0" aria-valuemax="5" aria-valuenow="${doneCount()}"><span style="width:${doneCount() * 20}%"></span></div><div class="imu-live">${directionPad()}<div class="imu-live-txt"><h3>${f.done[f.selected] ? '✓ ' : ''}${selected.label}</h3><p>${selected.hint}</p>${f.selected !== 'still' ? '<p class="imu-small">每次先端平停半秒。站在鸭子后面，它的左就是你的左。</p>' : ''}${f.phase === 'collect' ? button(f.done[f.selected] ? '本步已记录' : `模拟采样：完成${selected.short === '静止' ? '静止' : selected.label}`, 'imu-demo-sample', '', off(!canSample)) : ''}<span class="imu-demo-caption">点击“模拟采样”体验采集流程。</span></div></div>${block && f.phase !== 'collect' ? `<p class="imu-block" role="status">${block}</p>` : ''}<div class="imu-actions imu-main-action">${primary}${['collect', 'short'].includes(f.phase) ? button('重来', 'imu-collect-restart', '', off(!!commonBlock())) : ''}</div>${!i.result && f.phase !== 'idle' && doneCount() ? `<div class="imu-secondary-actions">${button('重做指定方向', 'imu-redo-open', 'ghost', off(!!commonBlock()))}</div>` : ''}`, `<label class="imu-mute"><input type="checkbox" data-imu-field="muted" ${i.muted ? 'checked' : ''}> 静音</label>`, 'imu-collect');
  }
  function resultCard() {
    const i = state();
    if (!i.result) return '';
    const awaiting = !!pending().imu;
    const written = !awaiting && P.state.calibrationStatus === 'complete';
    return card('结果', `<div class="imu-result-label">五项记录齐全</div><div class="imu-parts">${[['前后补偿', '+7.5°'], ['左右', '0.0°'], ['朝向', '0.0°'], ['总修正', '7.5°']].map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('')}</div><p class="imu-small">示例结果，未计算实际标定参数。</p>${row('完整性', '静止 + 四方向 · 5 / 5')}${row('质量', '示例展示 · 未评估实际采样质量')}${row('保存状态', awaiting ? '已记录在 Studio（演示），待统一提交' : written ? '写入与验证流程已演示' : '无待提交结果')}${details('result', '步骤记录与质量说明', `<table class="imu-table"><thead><tr><th>步骤</th><th>当前记录</th><th>正式采集要求</th></tr></thead><tbody>${STEPS.map(key => `<tr><td>${STEP[key].label}</td><td>模拟采样完成</td><td>${key === 'still' ? '静止 3 秒' : '先端平，再倾斜停住'}</td></tr>`).join('')}</tbody></table><p>正式标定会评估方向偏斜、稳定性与前后 / 左右一致性；当前展示示例步骤。</p>`)}<div class="imu-actions">${onboardingActive()?'':button(awaiting ? '预览并提交主程序' : written ? '已写入 · 演示' : '无待提交结果', 'imu-commit', 'primary', off(!awaiting || !!commonBlock()))}${button('重做指定方向', 'imu-redo-open', '', off(!!commonBlock()))}</div>` , badge('示例对比'), 'imu-result');
  }
  function configurationCard() {
    const i = state(), locked = committing() || i.coarse.active || !!commonBlock({ coarse: true });
    return card('芯片与诊断', `${details('chip', '芯片配置', `<p>自动识别支持 FT6、IMU-to-DXL 与兼容 DXL2 板卡。Qinmo 需手动选择类型。</p><label class="field">总线 IMU 类型<select data-imu-field="profile" ${off(locked)}>${Object.entries(PROFILE).map(([key, name]) => `<option value="${key}" ${i.profile === key ? 'selected' : ''}>${name}</option>`).join('')}</select></label><div class="imu-actions">${button('保存类型并重新连接 · 演示', 'imu-profile-save', '', off(locked || !!commonBlock({ coarse: true })))}</div><p>更换类型会重新进入标定流程，旧方向待验证；取消可恢复原参数。I²C 模块检测仍由首次启动配置完成。</p>`)}${details('diagnostics', '总线 IMU 只读诊断', `${row('板卡', readable() ? '总线 IMU · ID 200' : '等待读数')}${row('协议 / 类型', readable() ? i.savedProfile === 'qinmo' ? 'Feetech / Qinmo（已配置）' : '自动识别 · 演示配置' : '—')}${row('原始重力向量', readable() ? '0.139 / 0.000 / −0.990 · 示例' : '—')}${row('姿态年龄', readable() ? '38 ms · 示例' : '已过期 / 不可用')}<p>板卡应答不等于安装方向验证或运动许可。</p><a class="link" href="#/devices/diagnostics">查看运行诊断 ${P.icon('arrow', 13)}</a>`)}`);
  }
  function beep() {
    if (state().muted) return;
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) return;
      const context = new Context(), tone = context.createOscillator(), gain = context.createGain();
      tone.frequency.value = 660; gain.gain.value = .045; tone.connect(gain); gain.connect(context.destination);
      tone.onended = () => context.close().catch(() => {}); tone.start(); tone.stop(context.currentTime + .07);
    } catch { /* A disabled audio device does not invalidate a visual prototype step. */ }
  }
  function completeFine() {
    const i = state();
    i.fine.phase = 'done'; i.fine.message = '';
    // Explicit clicks only create a display fixture, never a solved calibration or hardware payload.
    i.result = { demo: true, complete: true };
    pending().imu = true;
  }
  function makeBackup() {
    const i = state();
    return { mount: { ...i.mount }, profile: i.savedProfile, fine: JSON.parse(JSON.stringify(i.fine)), result: i.result ? { ...i.result } : null, pendingImu: pending().imu, boot: P.state.boot };
  }
  function beginCoarse() {
    const i = state();
    i.coarse = { phase: 'still', active: true, confirmed: false, backup: makeBackup() };
    P.state.boot = 'blocked';
    i.mount = { verified: false, source: 'coarse-demo' }; i.fine = freshFine(); i.result = null; i.standing = false; pending().imu = false;
    P.onboarding?.record?.('calibration-changed');
  }
  function confirmCoarse(profileChange = false) {
    const why = commonBlock({ coarse: true });
    if (why) { P.toast(why); return; }
    if (P.torqueStatus() !== '已卸力') { P.toast('先失能卸力并扶稳机身，再重新标定方向'); return; }
    P.openModal(profileChange ? '保存类型并重新连接 · 演示' : '重新标定安装方向', '<p>正式流程会备份原方向并进入标定模式；保持卸力，双手托住躯干。原型只演示静止、前倾与主程序验证。</p>', '进入粗校演示', () => {
      const reason = commonBlock({ coarse: true });
      if (reason || P.torqueStatus() !== '已卸力') { P.toast(reason || '请先卸力'); return false; }
      beginCoarse(); P.render();
    });
  }
  function stop() {
    const i = state(); i.standing = false;
    if (i.fine.phase === 'collect') { i.fine.phase = 'short'; i.fine.message = '采集已停止，已记录步骤保留。'; }
  }
  function resetForOnboarding() {
    const i=state();
    Object.assign(i,{mount:{verified:false,source:'onboarding-demo'},standing:false,coarse:freshCoarse(),fine:freshFine(),result:null,open:{chip:false,diagnostics:false,result:false}});
    pending().imu=false;P.state.standing=false;
    return i;
  }
  function redoDirections() {
    const why = fineBlock();
    if (why) { P.toast(why); return; }
    P.openModal('重做指定方向', `<p>只清除勾选步骤，其余记录保留。</p>${STEPS.map(key => `<label class="imu-check"><input type="checkbox" name="imu-redo" value="${key}"> ${STEP[key].label}</label>`).join('')}`, '重做所选方向', dialog => {
      const reason = fineBlock(); if (reason) { P.toast(reason); return false; }
      const selected = [...dialog.querySelectorAll('input[name="imu-redo"]:checked')].map(input => input.value);
      if (!selected.length) { P.toast('至少选择一个方向'); return false; }
      const i = state(); selected.forEach(key => { i.fine.done[key] = false; }); i.fine.phase = 'collect'; i.fine.selected = missing()[0]; i.fine.message = ''; i.result = null; pending().imu = false; P.onboarding?.record?.('calibration-changed'); P.render();
    });
  }
  function handle(action, target = {}) {
    const i = state(), f = i.fine, c = i.coarse;
    if (action === 'imu-field') {
      const field = target.dataset.imuField;
      if (field === 'muted') { i.muted = target.checked; return true; }
      const block = commonBlock({ coarse: true });
      if (block) { P.toast(block); P.render(); return true; }
      if (field === 'profile' && PROFILE[target.value] && !c.active) i.profile = target.value;
      if (field === 'coarse-confirm') c.confirmed = !!target.checked;
      P.render(); return true;
    }
    if (action === 'imu-relax') {
      const global = document.querySelector('[data-action="emergency"]');
      if (global) { global.click(); return true; }
      P.state.torque = false; P.state.standing = false; P.state.jointTorque = {}; P.stopRemote?.(); stop(); P.render(); return true;
    }
    if (action === 'imu-select-direction') { if (STEPS.includes(target.dataset.direction)) f.selected = target.dataset.direction; P.render(); return true; }
    if (action === 'imu-collect-end') { if (f.phase === 'collect') { f.phase = 'short'; f.message = '采集已结束。'; } P.render(); return true; }
    if (action === 'imu-coarse-cancel') {
      if (committing()) { P.toast('先等待当前提交完成'); return true; }
      const backup = c.backup;
      if (backup) { i.mount = { ...backup.mount }; i.profile = backup.profile; i.savedProfile = backup.profile; i.fine = backup.fine; i.result = backup.result; pending().imu = backup.pendingImu; P.state.boot = backup.boot; }
      i.coarse = freshCoarse(); i.standing = false; P.toast('已恢复原型中的原参数状态'); P.render(); return true;
    }
    const coarseAction = action.startsWith('imu-coarse-') || action === 'imu-profile-save';
    const reason = commonBlock({ coarse: coarseAction });
    if (reason) { P.toast(reason); return true; }
    if (action === 'imu-coarse-begin' || action === 'imu-profile-save') { confirmCoarse(action === 'imu-profile-save'); return true; }
    if (action === 'imu-coarse-restill' && c.active) { c.phase = 'still'; c.confirmed = false; }
    else if (action === 'imu-coarse-sample' && c.active) { if (c.phase === 'still') c.phase = 'fwd'; else if (c.phase === 'fwd') c.phase = 'confirm'; beep(); }
    else if (action === 'imu-coarse-verify' && c.phase === 'confirm' && c.confirmed) c.phase = 'verifyLevel';
    else if (action === 'imu-coarse-verify-level' && c.phase === 'verifyLevel') c.phase = 'verifyFwd';
    else if (action === 'imu-coarse-verify-fwd' && c.phase === 'verifyFwd') { i.mount = { verified: true, source: 'coarse-demo' }; i.savedProfile = i.profile; c.phase = 'done'; c.active = false; c.backup = null; P.state.boot = onboardingActive()?'commissioning':'ready'; P.toast('主程序验证流程已演示，未连接真实主程序'); }
    else if (action === 'imu-stance-enable') {
      if (c.active || !i.mount.verified) { P.toast('先完成安装方向验证'); return true; }
      P.openModal('站姿使能 · 演示', '<p>正式标定模式会让 15 颗舵机转到站姿并保持，不提供平衡。请始终扶稳躯干。</p>', '演示站姿使能', () => { const block = commonBlock(); if (block || c.active || !i.mount.verified) { P.toast(block || '先完成方向验证'); return false; } i.standing = true; P.state.torque = true; P.state.standing = true; P.state.jointTorque = {}; P.render(); }); return true;
    } else if (['imu-collect-start', 'imu-collect-restart', 'imu-collect-resume', 'imu-demo-sample'].includes(action)) {
      const block = fineBlock(); if (block) { P.toast(block); return true; }
      if (action === 'imu-collect-start' || action === 'imu-collect-restart') { i.fine = freshFine(); i.fine.phase = 'collect'; i.result = null; pending().imu = false; P.onboarding?.record?.('calibration-changed'); }
      else if (action === 'imu-collect-resume') { f.phase = 'collect'; f.message = ''; f.selected = missing()[0] || 'still'; }
      else if (f.phase === 'collect' && !f.done[f.selected]) {
        if (f.selected !== 'still' && !f.done.still) { P.toast('先完成静止采样'); return true; }
        f.done[f.selected] = true; beep(); if (!missing().length) completeFine(); else f.selected = missing()[0];
      }
    } else if (action === 'imu-redo-open') { redoDirections(); return true; }
    else if (action === 'imu-commit') { if (pending().imu) P.openCalibrationCommit?.(); return true; }
    else return false;
    P.render(); return true;
  }
  function getViewerState() {
    const i = state(), f = i.fine, c = i.coarse;
    const guide = f.phase === 'collect' ? f.selected : c.active && ['still', 'verifyLevel'].includes(c.phase) ? 'still' : c.active && ['fwd', 'verifyFwd'].includes(c.phase) ? 'fwd' : null;
    const a = radians(18), s = Math.sin(a), z = -Math.cos(a);
    const examples = { still: [0, 0, -1], fwd: [s, 0, z], back: [-s, 0, z], left: [0, s, z], right: [0, -s, z] };
    return { gravity: i.result ? [...DEMO_GRAVITY] : guide ? examples[guide] : [...DEMO_GRAVITY], correction: i.result ? [...DEMO_CORRECTION] : null, parts: { pitch_deg: i.result ? 7.5 : 0, roll_deg: 0 }, collecting: f.phase === 'collect' || c.active && !!guide, arrow: guide && guide !== 'still' ? guide : null, cue: guide ? guide === 'still' ? '静止站好 · 演示' : `${STEP[guide].label}示范 · 18°` : '', stale: !readable() };
  }
  function render() {
    state();
    return `<div class="imu-page"><div class="imu-grid">${stageCard()}<div class="imu-side">${statusCard()}${coarseCard()}${stanceCard()}${collectCard()}${resultCard()}${configurationCard()}</div></div></div>`;
  }
  function afterRender() {
    document.querySelectorAll('.imu-pad [data-direction]').forEach(point => { point.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); handle('imu-select-direction', point); } }; });
    document.querySelectorAll('[data-imu-field]').forEach(field => { field.onchange = () => handle('imu-field', field); });
    document.querySelectorAll('[data-imu-details]').forEach(panel => { panel.ontoggle = () => { state().open[panel.dataset.imuDetails] = panel.open; }; });
  }
  function invalidateFineForOnboarding() {
    const i = state();
    i.fine = freshFine(); i.result = null; pending().imu = false;
  }
  P.imuPage = { render, handle, afterRender, stop, getViewerState, resetForOnboarding, invalidateFineForOnboarding };
})();
