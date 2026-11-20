(() => {
  const P = window.P;
  const remote = P.state.remote = {
    posture: '已卸力', mode: '走路', paired: false, kind: 'Switch / GameSir', pads: [],
    axes: { lx: 0, ly: 0, rx: 0, ry: 0 }, held: '', recording: 'idle', elapsed: 0,
    startedAt: 0, events: [], clips: [], selected: '', playing: false, playElapsed: 0,
    playStarted: 0, loops: 1, idle: '', seen: [], estopStep: -1, estopResult: '', owner: 'mine', ownerName: '另一页面',
    lastResult: '', fingerprint: '', wheelMode: false, rollerAvailable: false, netpad: false, stopPending: false,
    wizard: { phase: 'idle', kind: 'Switch / GameSir', candidates: [], error: '' },
  };
  const sensing = P.state.sensing = { preview: false, zone: 27, listening: false, music: false, talk: false, command: '站起来', transcript: '', response: '', tofUpdated: '0.2 秒前' };
  const pointers = new Map(), keys = new Set();
  const TEST_KEYS = ['A', 'B', 'X', 'Y', 'LB', 'RB', 'LT', 'RT', '十字上', '十字下', '十字左', '十字右', 'Start', 'Select', 'L3', 'R3', 'Home', '左摇杆', '右摇杆'];
  const VOICE_COMMANDS = ['鸭哥', '站起来', '开始走', '坐下', '前进', '后退', '左转', '右转', '停下', '翻个跟头'];
  let activityObserver = null, activityGeneration = 0, remoteInView = true, padMoveTimer = null;
  const icon = (name, size = 18) => P.icon(name, size);
  const esc = value => P.escape(value);
  const button = (text, action, type = '', attrs = '') => `<button class="btn ${type}" data-action="${action}" ${attrs}>${text}</button>`;
  const chip = (text, active) => P.badge(text, active ? 'success' : '');
  const field = (label, value) => `<div class="field-row"><span>${label}</span><strong>${value}</strong></div>`;
  const tabs = (base, selected, items) => `<nav class="subnav exp-section-tabs" aria-label="页面分组">${items.map(([id, label]) => `<a href="#/${base}/${id}" class="${selected === id ? 'active' : ''}" ${selected === id ? 'aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
  const submissionBusy = () => ['handoff', 'verifying'].includes(P.state.calibrationStatus);
  const systemBusy = () => P.isSystemBusy ? P.isSystemBusy() : ['installing', 'reconnecting'].includes(P.state.system?.updates);
  const availabilityBlocked = () => P.state.offline || P.state.stale || P.state.owned || submissionBusy() || ['failed', 'returning'].includes(P.state.maintenanceStatus) || systemBusy();
  const blocked = (action = '') => availabilityBlocked() || P.state.boot !== 'ready' || !!P.onboarding?.motionBlock?.(action);
  const managementBlocked = () => availabilityBlocked() || ['stopped', 'starting'].includes(P.state.boot);
  const whyBlocked = (action = '') => submissionBusy() ? '标定提交中，等待当前操作完成' : systemBusy() ? '软件重启中，先完成重新连接与版本读回' : P.state.maintenanceStatus === 'failed' ? '维护交还尚未确认，先查看恢复结果' : P.state.maintenanceStatus === 'returning' ? '控制权正在交还' : P.state.owned ? '设备维护已接管，遥控暂不可用' : P.state.offline || P.state.stale ? '连接状态不可用，操作已暂停' : P.onboarding?.motionBlock?.(action) || P.readiness().title;
  const webChoiceConflict = () => P.onboarding?.active?.() && P.state.onboarding?.controlChoice === 'web' && remote.paired;
  const motionReason = (action = '') => blocked(action) ? whyBlocked(action) : webChoiceConflict() ? '先断开实体手柄，再使用网页遥控' : '';
  const motionBlocked = (action = '') => !!motionReason(action);
  const foreignDriver = () => remote.owner !== 'mine';
  const modelFingerprint = () => JSON.stringify(P.state.system?.loaded || {});
  const isRecording = () => remote.recording === 'recording' || remote.recording === 'paused';
  const duration = () => remote.elapsed + (remote.recording === 'recording' ? (performance.now() - remote.startedAt) / 1000 : 0);
  const clock = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}.${Math.floor(seconds * 10) % 10}`;
  const chosenClip = () => remote.clips.find(clip => clip.id === remote.selected);
  const axesActive = () => Object.values(remote.axes).some(value => Math.abs(value) >= .1);
  const postureLabel = () => remote.posture === '行走中' ? axesActive() ? '行走中' : '准备好走' : remote.posture;
  const playTime = () => remote.playElapsed + (remote.playing === 'playing' ? (performance.now() - remote.playStarted) / 1000 : 0);
  const soundNames = ['嘎一声', '打招呼', '疑问', '咕咕', '大叫', '告别'];
  const holdNames = ['点头', '摇头', '歪头', '张嘴'];
  const comboNames = ['打招呼', '开心', '疑问', '不要', '左右各踢一脚'];
  function note(text) { if (remote.recording === 'recording') remote.events.push({ at: duration(), text }); }
  function finishRecording(reason = '录制结束') {
    remote.elapsed = Math.min(60, duration()); remote.idle = '';
    remote.recording = remote.events.length ? 'draft' : 'idle';
    remote.lastResult = remote.events.length ? `${reason}，尚未保存` : '没有有效动作，未生成草稿';
    if (!remote.events.length) remote.elapsed = 0;
  }
  function neutral() {
    clearTimeout(padMoveTimer); padMoveTimer = null;
    pointers.forEach(pointer => clearTimeout(pointer.timer));
    pointers.clear(); keys.clear(); remote.axes = { lx: 0, ly: 0, rx: 0, ry: 0 }; remote.held = '';
    document.querySelectorAll('.exp-stick-thumb').forEach(el => { el.style.transform = ''; el.setAttribute('aria-valuenow', '0'); el.setAttribute('aria-valuetext', '归中'); });
    document.querySelectorAll('[data-exp-hold]').forEach(el => el.classList.remove('held'));
    updateReadouts();
  }
  // The owner calls this before release, takeover, or disconnect. It only resets this local prototype.
  P.stopRemote = () => {
    if (isRecording()) finishRecording();
    neutral(); cancelTalk(); remote.playing = false; remote.idle = ''; remote.mode = '走路';
    remote.posture = P.state.torque ? (remote.posture === '坐姿' ? '坐姿' : '站立') : '已卸力';
  };
  function moveAllowed() {
    if (motionBlocked('move')) { P.toast(motionReason('move')); return false; }
    if (!remoteInView) { P.toast('回到遥控区域后再操作'); return false; }
    if (foreignDriver()) { P.toast(`${remote.ownerName}正在遥控，可在控制状态中接管`); return false; }
    if (remote.paired) { P.toast('实体手柄已连接，网页摇杆只显示'); return false; }
    if (!P.state.torque || !P.state.standing || remote.stopPending || remote.posture !== '行走中') { P.toast('先站起来，再开始走'); return false; }
    if (remote.playing) { P.toast('先停止回放，再操作摇杆'); return false; }
    return true;
  }
  function recordWalkInput() {
    const moving = remote.mode === '走路' && ['lx', 'ly', 'rx'].some(axis => Math.abs(remote.axes[axis]) >= .1);
    if (moving) P.onboarding?.record?.('move');
    return moving;
  }
  function padWalkDemo() {
    return P.onboarding?.active?.() && P.state.onboarding?.controlChoice === 'pad' && remote.paired && remote.estopResult && remote.posture === '行走中' ? `<div class="exp-playback-buttons">${button('演示轻推手柄', 'exp-pad-move', '', motionBlocked('move') ? 'disabled' : '')}</div>` : '';
  }
  function modeAxes() {
    if (remote.mode === '头部') return [['转头', '低抬头'], ['歪头', '脖子俯仰']];
    if (remote.mode === '身体') return [['—', '起伏下蹲'], ['左右倾', '前后倾']];
    return [['左右平移', '前后移动'], ['左右转向', '—']];
  }
  function readoutText() {
    if (remote.held) return `正在做：${remote.held}，松手停止`;
    const a = remote.axes, n = v => Math.abs(v) < .1 ? 0 : v;
    if (remote.mode === '头部') return `转头 ${(-n(a.lx) * 2.5).toFixed(2)} rad　低抬头 ${(n(a.ly) * 2.5).toFixed(2)} rad　脖子 ${(-n(a.ry) * 2.5).toFixed(2)} rad　歪头 ${(n(a.rx) * 2.5).toFixed(2)} rad`;
    if (remote.mode === '身体') { const height = -n(a.ly); return `高低 ${(height * (height >= 0 ? 1 : 2.5)).toFixed(1)} cm　前后倾 ${(-n(a.ry) * .26).toFixed(2)} rad　左右倾 ${(n(a.rx) * .26).toFixed(2)} rad`; }
    return `前后 ${(-n(a.ly) * .3).toFixed(2)} m/s　平移 ${(-n(a.lx) * .3).toFixed(2)} m/s　转向 ${(-n(a.rx) * 1.5).toFixed(2)} rad/s`;
  }
  function updateReadouts() {
    document.querySelectorAll('.exp-live-readout').forEach(el => { el.textContent = readoutText(); });
    document.querySelectorAll('[data-exp-posture] .badge').forEach(el => { el.textContent = foreignDriver() ? `${remote.ownerName}占用中` : remote.paired ? '实体手柄 · 网页摇杆让位' : postureLabel(); });
  }
  function changeAudio(action) {
    const closing = action === 'exp-listening' ? sensing.listening : sensing.music;
    if (!closing && blocked()) { P.toast(whyBlocked()); return; }
    if (action === 'exp-listening') { sensing.listening = !sensing.listening; if (!sensing.listening) sensing.music = false; }
    else { if (!sensing.listening) { P.toast('先开启监听，再听音乐'); return; } sensing.music = !sensing.music; }
    if ((!sensing.music || !sensing.listening) && remote.idle === '随乐律动中') remote.idle = '';
  }
  function controlPad(side) {
    const axis = modeAxes()[side === 'left' ? 0 : 1];
    return `<div class="exp-control-pad"><div class="exp-pad-label">${side === 'left' ? '左摇杆' : '右摇杆'}<span>${axis.filter(x => x !== '—').join(' · ')}</span></div>
      <div class="exp-pad exp-stick ${remote.paired ? 'mirror' : ''}" data-stick="${side}"><span class="exp-axis north">${axis[1] === '—' ? '' : '↑'}</span><span class="exp-axis south">${axis[1] === '—' ? '' : '↓'}</span><span class="exp-axis west">${axis[0] === '—' ? '' : '←'}</span><span class="exp-axis east">${axis[0] === '—' ? '' : '→'}</span>
        <button class="exp-stick-thumb" data-exp-stick="${side}" role="slider" aria-label="${side === 'left' ? '左' : '右'}摇杆，按住并拖动" aria-valuemin="-100" aria-valuemax="100" aria-valuenow="0" aria-valuetext="归中" ${remote.paired ? 'disabled' : ''}>${icon(side === 'left' ? 'remote' : 'activity', 24)}</button></div></div>`;
  }
  function heldButtons(names, group) {
    return names.map(name => `<button class="btn exp-hold-btn" data-exp-hold="${name}" data-hold-group="${group}" ${motionBlocked() ? 'disabled' : ''}>${name}<small>按住</small></button>`).join('');
  }
  function recordingBar() {
    if (remote.recording === 'idle' && !remote.playing && !remote.idle) return '';
    const state = isRecording() ? remote.recording === 'recording' ? '录制中' : '录制已暂停' : remote.playing ? remote.playing === 'playing' ? '正在回放' : '回放已暂停' : remote.recording === 'draft' ? '录制待保存' : remote.idle;
    return `<div class="exp-session-bar" role="status"><span class="exp-session-indicator ${remote.recording === 'recording' ? 'recording' : ''}"></span><strong>${state || '动作已停止'}</strong><span data-exp-clock>${clock(remote.playing ? playTime() : duration())}</span><div class="exp-session-buttons">
      ${isRecording() ? button(remote.recording === 'recording' ? '暂停' : '继续', 'exp-record-pause') + button('结束录制', 'exp-record-finish') : remote.recording === 'draft' && !remote.playing && !remote.idle ? button('保存动作', 'exp-record-save', 'primary') + button('放弃', 'exp-record-discard') : ''}
      ${remote.playing ? button(remote.playing === 'playing' ? '暂停' : '继续', 'exp-play-pause') : ''}${remote.playing || remote.idle ? button(remote.playing ? '停止回放' : remote.idle === '随乐律动中' ? '停止律动' : '停止灵动', 'exp-stop-action', 'danger') : ''}</div></div>`;
  }
  function interactionCard(recordingPage) {
    return `<section class="card"><div class="card-head"><h2>${recordingPage ? '互动' : '录制与互动'}</h2>${recordingPage ? '' : '<a class="exp-text-link" href="#/remote/recording">动作库</a>'}</div><div class="card-body">
      ${!recordingPage && remote.recording === 'idle' && !remote.playing ? `<div class="exp-playback-buttons">${button('开始录制', 'exp-record-start')}</div>` : ''}
      <div class="exp-playback-buttons">${remote.idle ? chip(remote.idle, true) : button('灵动', 'exp-idle') + button('随乐律动', 'exp-music')}</div>
      <div class="exp-playback-buttons exp-audio-shortcuts">${button(sensing.listening ? '关闭监听' : '开启监听', 'exp-listening', sensing.listening ? 'selected' : '')}${button(sensing.music ? '退出音乐' : '听音乐', 'exp-music-listen', '', sensing.listening ? '' : 'disabled')}</div>
      <div class="exp-audio-inline-state"><span>${sensing.listening ? sensing.music ? '轻快 · 104 BPM' : '正在听你说话' : '监听已关闭'}</span><a class="link" href="#/sensing/audio">音频详情 ${icon('arrow', 13)}</a></div></div></section>`;
  }
  function controls({ recordingPage = false } = {}) {
    const standing = P.state.torque && P.state.standing && !remote.stopPending && ['站立', '行走中'].includes(remote.posture);
    return `<div class="exp-content-grid"><section class="card exp-main-control exp-drive-focus" tabindex="0" aria-label="网页遥控区，聚焦后可使用 W S A D Q E 键">
      <div class="card-head"><h2>网页遥控</h2><div class="exp-control-heading-actions"><span data-exp-posture>${chip(foreignDriver() ? `${esc(remote.ownerName)}占用中` : remote.paired ? '实体手柄 · 网页摇杆让位' : postureLabel(), standing)}</span>${foreignDriver() ? button('接管遥控', 'exp-takeover', 'small') : ''}${webChoiceConflict() ? button('断开手柄', 'exp-pad-disconnect', 'small') : ''}<button class="btn danger small" data-action="emergency">卸力</button></div></div>
      <div class="exp-mode-bar"><div class="segmented" aria-label="摇杆模式">${['走路', '头部', '身体'].map(mode => button(mode, 'exp-mode', remote.mode === mode ? 'active' : '', `data-mode="${mode}" aria-pressed="${remote.mode === mode}" ${remote.paired || motionBlocked() ? 'disabled' : ''}`)).join('')}</div><span class="muted">${remote.mode === '走路' ? '步行模式' : `摇杆控制${remote.mode}`}</span></div>
      <div class="exp-pads">${controlPad('left')}${controlPad('right')}</div><p class="exp-control-hint">按住圆心拖动，松手归中</p><div class="exp-motion-output"><span class="exp-live-readout">${readoutText()}</span></div>
      <div class="exp-primary-actions">${button('站起来', 'exp-stand', 'primary', motionBlocked('exp-stand') ? 'disabled' : '')}${button(remote.posture === '行走中' ? '停下站好' : '开始走', remote.posture === '行走中' ? 'exp-walk-stop' : 'exp-walk', remote.posture === '行走中' ? 'exp-stop-btn' : '', remote.posture !== '行走中' && (!standing || motionBlocked('exp-walk')) ? 'disabled' : '')}${button('坐下', 'exp-sit', '', motionBlocked('exp-sit') || remote.posture !== '行走中' || axesActive() ? 'disabled' : '')}${button('摇杆归中', 'exp-neutral')}</div>${padWalkDemo()}
      ${remote.posture === '站立' ? '<p class="exp-control-reason">开始走后，可使用摇杆、坐下与技能。</p>' : ''}
      <details class="exp-control-details"><summary>键盘、控制幅度与停止方式</summary><div class="card-body"><p>点一下遥控区后使用 W / S 前后、A / D 平移、Q / E 转向。头部与身体模式使用摇杆。</p>${field('满杆上限', '移动 0.30 m/s · 转向 1.5 rad/s')}${field('头部满杆', '2.5 rad · 来源：现有配置')}<p>摇杆归中保留行走策略；停下站好会关闭策略并保持站姿；卸力会关闭舵机扭矩。Esc 归中，空格卸力。</p></div></details>
    </section><div class="exp-side-stack"><section class="card"><div class="card-head"><h2>动作与声音</h2>${button('嘎一声', 'exp-sound', 'small', 'data-name="嘎一声"')}</div><div class="exp-skills">${['低头捡', '左脚踢', '右脚踢', '前滚翻'].map(name => button(name, 'exp-skill', '', `data-name="${name}"`)).join('')}</div><div class="exp-actions-subsection"><span class="kicker">按住动作，松手停止</span><div class="exp-held-grid">${heldButtons(holdNames, 'single')}</div></div><details class="exp-control-details"><summary>组合动作与其他按键</summary><div class="card-body"><div class="exp-held-grid">${heldButtons(comboNames, 'combo')}</div><div class="exp-held-grid exp-trigger-buttons">${heldButtons(['张嘴嘎一声', '张嘴长叫', '连续前滚翻'], 'trigger')}</div><div class="exp-sound-grid">${soundNames.slice(1).map(name => button(name, 'exp-sound', '', `data-name="${name}"`)).join('')}</div></div></details></section>
      ${interactionCard(recordingPage)}
      <details class="card exp-details"><summary>控制状态与更多操作${icon('chevron', 16)}</summary><div class="card-body">${field('当前控制者', foreignDriver() ? esc(remote.ownerName) : remote.paired ? '实体手柄（网页摇杆让位）' : '本页')}${field('通信', blocked() ? '不可用' : '10 Hz · 失联自动归中')}${button('重启全部舵机', 'exp-reboot', '', blocked() ? 'disabled' : '')}<button class="btn exp-hold-btn" data-exp-wheel ${blocked() ? 'disabled' : ''}>按住 3 秒切到${remote.wheelMode ? '步行' : '轮滑'}</button><span class="exp-small-note">${remote.rollerAvailable ? '已加载轮滑模型' : '未加载轮滑模型，切换会被拒绝'}</span><a class="btn" href="#/models/bindings">按键映射</a></div></details>
    </div></div>`;
  }
  function recorder() {
    const clip = chosenClip();
    const libraryLocked = isRecording() || remote.recording === 'draft' || !!remote.playing;
    const recorderState = isRecording() ? remote.recording === 'paused' ? '录制已暂停。' : '正在记录下方操作。' : remote.recording === 'draft' ? '这段录制已结束，保存后可回放。' : remote.playing ? '动作正在回放。' : remote.lastResult || '在下方直接操控小鸭，录好后保存。';
    return `<div class="exp-recorder-grid"><section class="card"><div class="card-head"><h2>录制控制指令与节奏</h2>${chip(`${remote.clips.length} / 20 段`, false)}</div><div class="card-body"><p class="exp-recorder-intro">${recorderState}</p>${remote.recording === 'idle' && !remote.playing ? `<div class="exp-recording-actions">${button('开始录制', 'exp-record-start', 'primary')}</div>` : ''}<details><summary>录制范围与限制</summary><p>每段最多 60 秒。支持摇杆、坐站、头部、身体、嘴部与叫声；技能、音视频、上卸力和开始走不录制。回放按目标姿态等待坐站完成，路线可能变化。</p></details></div></section>
      <section class="card"><div class="card-head"><h2>已保存动作</h2>${clip && remote.playing ? chip(remote.playing === 'paused' ? '回放已暂停' : '正在回放', true) : ''}</div><div class="card-body">${clip ? `<label class="exp-loop-label">动作<select data-exp-clip aria-label="选择已保存动作" ${libraryLocked ? 'disabled' : ''}>${remote.clips.map(item => `<option value="${item.id}" ${item.id === remote.selected ? 'selected' : ''}>${esc(item.name)} · ${item.duration.toFixed(1)} 秒</option>`).join('')}</select></label><div class="exp-library-toolbar"><label class="exp-loop-label">次数<select data-exp-loops aria-label="回放次数" ${libraryLocked ? 'disabled' : ''}>${[1, 3, 5].map(n => `<option ${n === remote.loops ? 'selected' : ''}>${n}</option>`).join('')}</select></label>${button('开始回放', 'exp-play', 'primary', isRecording() || remote.recording === 'draft' || remote.playing ? 'disabled' : '')}${button('改名', 'exp-rename', '', libraryLocked ? 'disabled' : '')}${button('删除', 'exp-delete', '', libraryLocked ? 'disabled' : '')}</div><p class="exp-play-progress" data-exp-play>${remote.playing ? `${clock(playTime())} / ${clock(clip.duration)} · ${remote.loops} 次` : `${clip.events.length} 条演示控制事件`}</p>` : '<div class="exp-library-empty"><p>还没有保存的动作</p><span class="muted">录一段操作，再起个名字</span></div>'}</div></section></div>`;
  }
  function pairWizard() {
    const w = remote.wizard;
    const how = w.kind.includes('Switch') ? '切到 S 档，按 Sync 至指示灯快闪。' : w.kind === 'Xbox' ? '先开机，再按顶部配对键至 Xbox 灯快闪。' : w.kind === 'PlayStation' ? '同时按住 Create 与 PS 键，直到灯条快闪。' : '按说明书进入配对状态，优先选择 Switch 或 X-input 模式。';
    if (w.phase === 'idle' || w.phase === 'done') return `<div class="exp-pair-stage exp-pair-stage-small">${icon('remote', 64)}<h3>${remote.paired ? esc(remote.kind) : '连接你的手柄'}</h3>${w.phase === 'done' ? '<p class="muted">配对流程已完成</p>' : '<p class="muted">Xbox、PlayStation、Switch 与兼容手柄</p>'}${button('配对新手柄', 'exp-pair', 'primary')}</div>`;
    if (w.phase === 'keys') return `<div class="exp-pair-wizard"><h3>已连接 ${esc(remote.kind)}</h3><p class="muted">在按键测试中检查按钮与两个摇杆，然后完成配对。</p>${button('结束配对向导', 'exp-pad-cancel')}</div>`;
    return `<div class="exp-pair-wizard"><label class="field">手柄类型<select data-exp-pad-kind>${['Switch / GameSir', 'Xbox', 'PlayStation', '其他兼容手柄'].map(kind => `<option ${w.kind === kind ? 'selected' : ''}>${kind}</option>`).join('')}</select></label><p class="exp-pair-how">${how}</p>
      ${w.phase === 'choose' ? `<div class="exp-pair-buttons">${button('搜索手柄', 'exp-pad-scan', 'primary')}${button('直接配对', 'exp-pad-auto')}</div>` : `<div class="exp-found-pads"><span class="kicker">可连接设备 · 演示</span>${w.candidates.map(item => `<div class="exp-found-pad-row"><div><strong>${esc(item.name)}</strong><small>蓝牙手柄 · ${item.signal}</small></div>${button('连接', 'exp-pad-connect', 'primary', `data-id="${item.id}"`)}</div>`).join('') || '<p class="muted">未找到手柄，确认指示灯快闪后重试。</p>'}</div><div class="exp-pair-buttons">${button(w.error ? '重试' : '重新搜索', 'exp-pad-scan')}</div>`}
      ${w.error ? `<p class="exp-pair-error" role="alert">${esc(w.error)}</p>` : ''}${button('取消配对', 'exp-pad-cancel', 'ghost')}</div>`;
  }
  function connectPad(name) {
    if (managementBlocked() || axesActive()) { P.toast(managementBlocked() ? whyBlocked() : '先松开摇杆停稳，再配对'); return false; }
    neutral(); remote.mode = '走路'; remote.playing = false; remote.idle = ''; if (isRecording()) finishRecording('实体手柄接入，录制已结束');
    remote.paired = true; remote.kind = name; remote.seen = []; remote.estopStep = -1; remote.estopResult = '';
    remote.pads.forEach(pad => { pad.connected = false; });
    const known = remote.pads.find(pad => pad.name === name);
    if (known) { known.connected = true; known.trusted = true; }
    else remote.pads.push({ id: String(Date.now()), name, trusted: true, connected: true });
    remote.wizard.phase = 'keys'; return true;
  }
  function gamepad() {
    const testLabels = ['Start 上力', '短按 Select', '回读卸力'];
    return `${remote.netpad ? '<div class="exp-input-notice" role="status">UDP 手柄已开启：蓝牙按键与急停不生效，先在配置中关闭 netpad。</div>' : ''}<div class="exp-content-grid"><div class="exp-side-stack"><section class="card"><div class="card-head"><h2>实体手柄</h2>${chip(remote.paired ? '已连接' : '未连接', remote.paired)}</div>${pairWizard()}</section>
      <section class="card"><div class="card-head"><h2>已配对手柄</h2></div><div class="card-body">${remote.pads.length ? remote.pads.map(pad => `<div class="exp-known-pad"><div><strong>${esc(pad.name)}</strong><small>${pad.connected ? '已连接' : '未连接'}${pad.trusted ? '' : ' · 未信任'}</small></div>${!pad.trusted ? button('修复', 'exp-pad-repair', '', `data-id="${pad.id}"`) : ''}${pad.connected ? button('断开', 'exp-pad-disconnect', '', `data-id="${pad.id}"`) : ''}${button('忘记', 'exp-pad-forget', '', `data-id="${pad.id}"`)}</div>`).join('') : '<p class="muted exp-empty-line">还没有配对记录</p>'}</div></section></div>
      <div class="exp-side-stack"><section class="card"><div class="card-head"><h2>按键与摇杆测试</h2>${chip(`${remote.seen.length} / ${TEST_KEYS.length}`, false)}</div><div class="card-body"><div class="exp-key-test">${TEST_KEYS.map(name => button(name, 'exp-test-key', remote.seen.includes(name) ? 'selected' : '', `data-name="${name}" ${remote.paired && !remote.netpad && !P.state.offline && !P.state.stale ? '' : 'disabled'}`)).join('')}</div><div class="exp-test-actions">${remote.wizard.phase === 'keys' ? button('按键都对，完成', 'exp-pad-keys-done', 'primary') : remote.seen.length ? button('重新测试', 'exp-test-reset') : ''}<a class="link" href="#/models/bindings">按键映射 ${icon('arrow', 13)}</a></div><details class="exp-control-details exp-key-legend"><summary>按键作用</summary><div>${field('Start / Select', '站起与策略切换 / 急停卸力')}${field('LT / RT', '张嘴长叫 / 张嘴并嘎一声')}${field('Y / B', '头模式 / 身体模式')}${field('十字下', '坐下 ↔ 站起')}</div></details></div></section>
      <section class="card"><div class="card-head"><h2>手柄急停测试</h2>${chip(remote.estopResult || '尚未测试', !!remote.estopResult)}</div><div class="card-body"><ol class="exp-estop-steps">${testLabels.map((text, index) => `<li class="${remote.estopStep > index || remote.estopResult ? 'complete' : remote.estopStep === index ? 'current' : ''}">${text}</li>`).join('')}</ol>${button(remote.estopStep < 0 ? '开始测试' : remote.estopStep === 0 ? '体验 Start 上力' : remote.estopStep === 1 ? '体验短按 Select' : '查看回读结果', 'exp-estop-next', 'primary', remote.paired && !remote.netpad ? '' : 'disabled')}${remote.estopStep >= 0 ? button('取消测试', 'exp-estop-cancel') : ''}${padWalkDemo()}<details><summary>通过条件</summary><p>先确认上力，短按 Select 后确认回读卸力。长按 2 秒会触发关机。此处只演示流程。</p></details></div></section></div></div>`;
  }
  function requestName(title, current, save) {
    P.openModal(title, `<label class="field">动作名称<input maxlength="32" value="${esc(current)}"></label>`, '保存', dialog => {
      if (blocked() || remote.playing || isRecording()) { P.toast(blocked() ? whyBlocked() : '先停止当前动作'); return false; }
      const name = dialog.querySelector('input').value.trim();
      if (!name) { P.toast('请输入动作名称'); return false; }
      if (save(name) === false) return false; P.render(); P.toast('动作已保存');
    });
  }
  P.register('remote', {
    render(subpage) {
      if (foreignDriver() && (axesActive() || remote.held || isRecording() || remote.playing || remote.idle)) P.stopRemote();
      if (remote.stopPending) { remote.posture = '停止待确认'; P.state.standing = false; }
      else {
        if (!P.state.torque && remote.posture !== '已卸力') remote.posture = '已卸力';
        if (P.state.torque && P.state.standing && remote.posture === '已卸力') remote.posture = '站立';
      }
      const selected = ['recording', 'gamepad'].includes(subpage) ? subpage : 'control';
      return tabs('remote', selected, [['control', '网页遥控'], ['recording', '录制与动作库'], ['gamepad', '实体手柄']]) + recordingBar() + (selected === 'gamepad' ? gamepad() : (selected === 'recording' ? recorder() : '') + controls({ recordingPage: selected === 'recording' }));
    },
    afterRender: observeRemoteActivity,
    handle(action, target) {
      if (!action.startsWith('exp-')) return false;
      const safe = ['exp-neutral', 'exp-walk-stop', 'exp-stop-action', 'exp-record-finish', 'exp-record-pause', 'exp-play-pause', 'exp-estop-cancel'];
      const control = ['exp-mode', 'exp-stand', 'exp-walk', 'exp-sit', 'exp-skill', 'exp-sound', 'exp-record-start', 'exp-play', 'exp-idle', 'exp-music', 'exp-reboot', 'exp-roller'];
      const motion = ['exp-mode', 'exp-stand', 'exp-walk', 'exp-sit', 'exp-skill', 'exp-play', 'exp-idle', 'exp-music', 'exp-reboot', 'exp-roller'];
      const resuming = action === 'exp-record-pause' && remote.recording === 'paused' || action === 'exp-play-pause' && remote.playing === 'paused';
      const padManaging = ['exp-pair', 'exp-pad-scan', 'exp-pad-auto', 'exp-pad-connect', 'exp-pad-repair', 'exp-pad-forget', 'exp-pad-disconnect', 'exp-pad-keys-done', 'exp-test-key', 'exp-test-reset'].includes(action);
      const managing = ['exp-record-save', 'exp-rename', 'exp-delete', 'exp-takeover'].includes(action);
      if (padManaging && managementBlocked() || (control.includes(action) && !safe.includes(action) || resuming || managing) && blocked(action)) { P.toast(whyBlocked(action)); return true; }
      if ((motion.includes(action) || resuming && action === 'exp-play-pause') && motionBlocked(action)) { P.toast(motionReason(action)); return true; }
      if ((control.includes(action) && action !== 'exp-sound' || resuming) && foreignDriver()) { P.toast(`${remote.ownerName}正在遥控，请先接管`); return true; }
      if (resuming && remote.fingerprint !== modelFingerprint()) { if (isRecording()) finishRecording('模型或配置已改变'); remote.playing = false; remote.lastResult = '模型或配置已改变，请重新开始'; P.render(); P.toast(remote.lastResult); return true; }
      if (['exp-rename', 'exp-delete'].includes(action) && (isRecording() || remote.recording === 'draft' || remote.playing)) { P.toast('先结束当前录制或回放'); return true; }
      if (action === 'exp-mode') { if (remote.paired || !['走路', '头部', '身体'].includes(target.dataset.mode)) return true; neutral(); remote.mode = target.dataset.mode; }
      else if (action === 'exp-stand') { remote.stopPending = false; remote.posture = '站立'; P.state.torque = true; P.state.standing = true; note('站起来'); P.onboarding?.record?.('stand'); }
      else if (action === 'exp-walk') { if (remote.stopPending || !P.state.torque || !P.state.standing || remote.posture !== '站立') { P.toast('先明确确认站姿，再开始走'); return true; } remote.posture = '行走中'; remote.idle = ''; }
      else if (action === 'exp-walk-stop') {
        const unavailable = blocked();
        P.stopRemote();
        if (unavailable) { remote.stopPending = true; remote.posture = '停止待确认'; P.state.standing = false; P.toast('网页输出已清理，设备停止待确认'); }
        else { remote.stopPending = false; remote.posture = P.state.torque ? '站立' : '已卸力'; P.state.standing = !!P.state.torque; P.toast('已停下站好，行走策略关闭'); }
      }
      else if (action === 'exp-sit') { if (remote.posture !== '行走中' || axesActive()) { P.toast('先开始走，并松开摇杆停稳，再坐下'); return true; } neutral(); remote.posture = '坐姿'; P.state.standing = false; note('坐下'); }
      else if (action === 'exp-neutral') { neutral(); P.toast(blocked() ? '网页输出已清理，设备状态待确认' : '摇杆已归中，保留当前行走策略'); }
      else if (action === 'exp-stop-action') { neutral(); remote.playing = false; remote.idle = ''; P.toast(blocked() ? '网页后续动作已清理，设备状态待确认' : '已停止后续动作，保持当前动力状态'); }
      else if (action === 'exp-skill') { if (remote.posture !== '行走中') { P.toast('先开始走，再执行技能'); return true; } remote.idle = ''; P.toast(`已演示：${target.dataset.name}`); }
      else if (action === 'exp-sound') { note(`叫声：${target.dataset.name}`); P.toast(`叫声已演示：${target.dataset.name}`); }
      else if (action === 'exp-listening' || action === 'exp-music-listen') changeAudio(action);
      else if (action === 'exp-record-start') { if (remote.clips.length >= 20) { P.toast('动作库已满，请先删除一段'); return true; } if (isRecording() || remote.recording === 'draft') return true; remote.playing = false; remote.events = []; remote.elapsed = 0; remote.lastResult = ''; remote.startedAt = performance.now(); remote.recording = 'recording'; remote.fingerprint = modelFingerprint(); }
      else if (action === 'exp-record-pause') { if (remote.recording === 'recording') { remote.elapsed = duration(); remote.recording = 'paused'; neutral(); remote.idle = ''; } else if (remote.recording === 'paused') { remote.startedAt = performance.now(); remote.recording = 'recording'; } }
      else if (action === 'exp-record-finish') { if (isRecording()) finishRecording(); }
      else if (action === 'exp-record-discard') { remote.recording = 'idle'; remote.events = []; remote.elapsed = 0; }
      else if (action === 'exp-record-save') { if (remote.recording !== 'draft' || !remote.events.length) return true; requestName('保存动作', '我的动作', name => { if (remote.clips.length >= 20) { P.toast('动作库已满'); return false; } const id = String(Date.now()); remote.clips.push({ id, name, duration: Math.max(.2, remote.elapsed), events: [...remote.events] }); remote.selected = id; remote.recording = 'idle'; remote.elapsed = 0; remote.lastResult = '动作已保存'; }); return true; }
      else if (action === 'exp-rename') { const clip = chosenClip(); if (clip) requestName('修改动作名称', clip.name, name => { clip.name = name; }); return true; }
      else if (action === 'exp-delete') { const clip = chosenClip(); if (clip) P.openModal('删除动作', `<p>删除“${esc(clip.name)}”？</p>`, '删除动作', () => { if (blocked() || isRecording() || remote.playing) { P.toast(blocked() ? whyBlocked() : '先停止当前动作'); return false; } remote.clips = remote.clips.filter(x => x.id !== clip.id); remote.selected = remote.clips[0]?.id || ''; P.render(); }); return true; }
      else if (action === 'exp-play') { if (!P.state.torque) { P.toast('先站起来，再回放动作'); return true; } if (!chosenClip() || isRecording() || remote.recording === 'draft') return true; neutral(); remote.idle = ''; remote.playElapsed = 0; remote.playStarted = performance.now(); remote.playing = 'playing'; remote.fingerprint = modelFingerprint(); }
      else if (action === 'exp-play-pause') { if (remote.playing === 'playing') { remote.playElapsed = playTime(); remote.playing = 'paused'; neutral(); remote.idle = ''; } else if (remote.playing === 'paused') { remote.playStarted = performance.now(); remote.playing = 'playing'; } }
      else if (action === 'exp-idle' || action === 'exp-music') {
        const name = action === 'exp-idle' ? '灵动中' : '随乐律动中';
        if (remote.idle === name) remote.idle = '';
        else { if (remote.posture !== '行走中') { P.toast('先站稳并开始走，再开启灵动'); return true; } if (name === '随乐律动中' && (!sensing.listening || !sensing.music)) { P.toast('先在“监听与音乐”开启监听并听音乐'); P.navigate('sensing/audio'); return true; } if (remote.paired) { P.toast('开启律动前，先断开实体手柄'); return true; } neutral(); remote.playing = false; remote.idle = name; note(name); }
      } else if (action === 'exp-pair') { if (axesActive()) { P.toast('先松开摇杆停稳，再配对'); return true; } remote.wizard = { phase: 'choose', kind: remote.kind, candidates: [], error: '' };
      } else if (action === 'exp-pad-scan') { if (axesActive()) { P.toast('先停稳再搜索'); return true; } remote.wizard.phase = 'pick'; remote.wizard.error = ''; remote.wizard.candidates = [{ id: 'demo-controller', name: remote.wizard.kind, signal: '−53 dBm' }]; }
      else if (action === 'exp-pad-auto') connectPad(remote.wizard.kind);
      else if (action === 'exp-pad-connect') { const candidate = remote.wizard.candidates.find(item => item.id === target.dataset.id); if (candidate) connectPad(candidate.name); }
      else if (action === 'exp-pad-cancel') { remote.wizard.phase = 'idle'; remote.wizard.candidates = []; remote.wizard.error = ''; }
      else if (action === 'exp-pad-keys-done') remote.wizard.phase = 'done';
      else if (action === 'exp-test-reset') remote.seen = [];
      else if (action === 'exp-takeover') { P.openModal('接管遥控', `<p>${esc(remote.ownerName)}的网页摇杆与按住动作会停止；本页接管后不会自动续动。</p>`, '接管遥控', () => { if (blocked()) { P.toast(whyBlocked()); return false; } const posture = remote.posture; P.stopRemote(); remote.posture = posture; remote.owner = 'mine'; P.render(); }); return true;
      } else if (action === 'exp-pad-repair') { const pad = remote.pads.find(p => p.id === target.dataset.id); if (pad) pad.trusted = true; }
      else if (action === 'exp-pad-disconnect') { if (!remote.paired) return true; P.openModal('断开手柄', '<p>演示断开当前手柄并停止、坐下。保留配对记录，之后可重新连接。</p>', '断开手柄', () => { if (managementBlocked()) { P.toast(whyBlocked()); return false; } remote.pads.forEach(pad => { pad.connected = false; }); remote.paired = false; P.stopRemote(); remote.posture = P.state.torque ? '坐姿' : '已卸力'; P.state.standing = false; remote.estopStep = -1; remote.wizard.phase = 'idle'; P.render(); }); return true; }
      else if (action === 'exp-pad-forget') { const pad = remote.pads.find(p => p.id === target.dataset.id); if (pad) P.openModal('忘记手柄', `<p>从设备删除“${esc(pad.name)}”的配对记录？${pad.connected ? '当前连接会断开，小鸭会停止并坐下，请扶稳。' : ''}</p>`, '忘记手柄', () => { if (managementBlocked()) { P.toast(whyBlocked()); return false; } remote.pads = remote.pads.filter(p => p.id !== pad.id); if (pad.connected) { remote.paired = false; P.stopRemote(); remote.posture = P.state.torque ? '坐姿' : '已卸力'; P.state.standing = false; remote.wizard.phase = 'idle'; } P.render(); }); return true; }
      else if (action === 'exp-test-key') { if (remote.paired && !remote.netpad && TEST_KEYS.includes(target.dataset.name) && !remote.seen.includes(target.dataset.name)) remote.seen.push(target.dataset.name); }
      else if (action === 'exp-estop-next') { if (!remote.paired || remote.netpad || blocked(action)) { P.toast(remote.netpad ? 'UDP 手柄开启，蓝牙急停测试不可用' : blocked(action) ? whyBlocked(action) : '先连接手柄'); return true; } remote.estopResult = ''; remote.estopStep += 1; if (remote.estopStep === 1) { remote.stopPending = false; P.state.torque = true; P.state.standing = true; remote.posture = '站立'; P.onboarding?.record?.('stand'); } if (remote.estopStep === 2) { P.state.torque = false; P.state.standing = false; P.stopRemote(); } if (remote.estopStep === 3) { remote.estopResult = '流程演示完成'; remote.estopStep = -1; P.onboarding?.record?.('pad-estop'); } }
      else if (action === 'exp-pad-move') { if (motionBlocked('move')) { P.toast(motionReason('move')); return true; } if (!P.onboarding?.active?.() || P.state.onboarding?.controlChoice !== 'pad' || !remote.paired || remote.netpad || foreignDriver() || !remote.estopResult || !P.state.torque || !P.state.standing || remote.stopPending || remote.posture !== '行走中' || remote.mode !== '走路' || remote.playing) { P.toast('先通过手柄急停测试，站起并开始走'); return true; } neutral(); remote.idle = ''; remote.axes.ly = -.3; updateReadouts(); recordWalkInput(); padMoveTimer = setTimeout(neutral, 250); }
      else if (action === 'exp-estop-cancel') remote.estopStep = -1;
      else if (action === 'exp-reboot') { P.openModal('重启全部舵机', '<p>先卸力再重启，完成后需重新站立。</p>', '体验重启流程', () => { if (blocked()) { P.toast(whyBlocked()); return false; } P.state.torque = false; P.state.standing = false; P.stopRemote(); P.stopDevice?.(); P.render(); P.toast('重启流程已演示'); }); return true; }
      else if (action === 'exp-roller') { P.openModal('轮滑模式', '<p>只有装好轮子并加载轮滑模型后才能切换。当前原型保留入口。</p><a class="btn" href="#/models">查看模型</a>', '知道了'); return true; }
      else return false;
      P.render(); return true;
    }
  });
  function cameraScene() { return '<svg class="exp-scene" viewBox="0 0 720 380" aria-label="摄像头预览布局示意"><rect width="720" height="380" fill="#e9f0ed"/><path d="M0 265h720v115H0Z" fill="#d9e1db"/><path d="m0 380 310-115m410 115L420 265M190 380l170-115m170 115L380 265" stroke="#c9d4cc"/><rect x="65" y="48" width="175" height="180" rx="3" fill="#cadbd3"/><path d="M152 49v177M66 138h172" stroke="#f6f9f5" stroke-width="8"/><rect x="470" y="180" width="160" height="22" rx="4" fill="#b8c6bb"/><path d="M486 201v85m128-85v85" stroke="#acbbb0" stroke-width="9"/><rect x="503" y="134" width="33" height="46" rx="2" fill="#a0beb3"/></svg>'; }
  function camera() {
    const usable = !P.state.offline && !P.state.stale, active = usable && sensing.preview;
    return `<div class="exp-content-grid exp-camera-grid"><section class="card"><div class="card-head"><h2>${icon('camera')} 摄像头画面</h2>${chip(active ? '正在预览' : usable ? '预览未开启' : '等待连接', active)}</div><div class="exp-camera-stage">${active ? cameraScene() + '<span class="exp-scene-caption">预览示意画面</span>' : `<div class="exp-camera-empty">${icon('camera', 42)}<h3>${usable ? '画面待机' : '画面暂不可用'}</h3>${button(usable ? '开始预览' : '重新连接', usable ? 'exp-preview' : 'exp-reconnect', 'primary')}</div>`}</div><div class="exp-camera-toolbar"><span class="muted">${active ? '1920 × 1080　10 fps' : 'USB Camera'}</span>${active ? button('停止预览', 'exp-preview') + button('重新连接', 'exp-reconnect') : ''}</div></section><section class="card exp-fit-card"><div class="card-head"><h2>摄像头连接</h2>${chip(usable ? '连接正常' : '不可用', usable)}</div><div class="card-body">${field('配置来源', '摄像头 · 只读')}${field('传输方式', 'WebRTC')}${field('接收状态', active ? '画面更新中 · 示例' : '等待有效画面')}<details><summary>来源与连接详情</summary><p>配置来源不等于真实摄像头已验证；测试图案与模拟来源会单独标识。</p></details></div></section></div>`;
  }
  function tof() {
    const usable = !P.state.offline && !P.state.stale;
    const zones = Array.from({ length: 64 }, (_, i) => ({ value: 420 + Math.abs(27 - i) * 31 + i % 5 * 47, kind: i % 23 === 0 ? 'none' : i % 19 === 0 ? 'invalid' : 'range' }));
    const selected = zones[sensing.zone], values = zones.filter(z => z.kind === 'range').map(z => z.value);
    return `<div class="exp-content-grid"><section class="card"><div class="card-head"><h2>8 × 8 距离图</h2>${chip(usable ? '读数正常' : P.state.stale ? '读数已过期' : '等待连接', usable)}</div><div class="exp-tof-content"><div class="exp-tof-grid" aria-label="ToF 距离分区">${zones.map((z, i) => `<button data-action="exp-zone" data-zone="${i}" aria-label="第${Math.floor(i / 8) + 1}行第${i % 8 + 1}列 ${z.kind === 'range' ? z.value + '毫米' : z.kind === 'none' ? '无目标' : '无效'}" aria-pressed="${i === sensing.zone}" class="${i === sensing.zone ? 'selected' : ''} ${z.kind}" style="--zone-alpha:${(0.2 + (1 - z.value / 2200) * .64).toFixed(2)}" ${usable ? '' : 'disabled'}>${usable ? z.kind === 'range' ? z.value : z.kind === 'none' ? '—' : '×' : '·'}</button>`).join('')}</div><div class="exp-tof-legend"><span>近</span><i></i><span>远</span><span class="muted">— 无目标　× 无效</span></div></div><div class="exp-zone-detail"><span>第 ${Math.floor(sensing.zone / 8) + 1} 行，第 ${sensing.zone % 8 + 1} 列</span><strong>${usable ? selected.kind === 'range' ? selected.value : selected.kind === 'none' ? '—' : '×' : '—'} <small>mm</small></strong><span class="muted">${usable ? `原始 ${selected.value} mm · 状态 ${selected.kind === 'range' ? 5 : selected.kind === 'none' ? 255 : 1}` : '等待有效新读数'}</span></div></section><section class="card exp-fit-card"><div class="card-head"><h2>距离传感器</h2></div><div class="card-body">${field('型号', 'VL53L5CX')}${field('有效分区', usable ? `${values.length} / 64` : '—')}${field('最近 / 最远', usable ? `${Math.min(...values)} / ${Math.max(...values)} mm` : '—')}${field('更新时间', usable ? sensing.tofUpdated : '等待更新')}${field('传感器频率', usable ? '15 Hz · 示例' : '—')}${button('重新读取', 'exp-tof-refresh', 'primary')}<details><summary>分区显示说明</summary><p>按传感器原始行列显示，未与摄像头画面对齐。无目标与无效读数不参与最近、最远距离统计。</p></details></div></section></div>`;
  }
  function voice() {
    const usable = !blocked();
    return `<div class="exp-content-grid"><section class="card exp-voice-card"><div class="card-head"><h2>网页语音口令</h2>${chip(usable ? '可以说话' : '暂不可用', usable)}</div><div class="exp-voice-stage"><div class="exp-voice-orbit ${sensing.talk ? 'listening' : ''}"><button class="exp-mic" data-exp-talk aria-label="按住说话，松开发送" aria-pressed="${sensing.talk}" ${usable ? '' : 'disabled'}>${icon('mic', 38)}</button></div><h3 data-exp-talk-title>${sensing.talk ? '松开发送' : '鸭哥，听我说'}</h3><p class="muted" data-exp-talk-hint>按住说话，松开发送</p><div class="exp-transcript" aria-live="polite">${sensing.transcript ? `<span class="muted">听到你说</span><strong>“${esc(sensing.transcript)}”</strong><span>${esc(sensing.response)}</span>` : `<span class="muted">${esc(sensing.response || '使用当前手机或电脑的麦克风')}</span>`}</div><div class="exp-voice-commands">${['站起来', '开始走', '坐下', '前进'].map(text => button(text, 'exp-command', sensing.command === text ? 'selected' : '', `data-command="${text}" ${sensing.talk ? 'disabled' : ''}`)).join('')}</div></div><div class="exp-voice-footer">${button('试叫一声', 'exp-quack')}${button('停下行走', 'exp-voice-stop', 'exp-stop-btn')}</div></section><div class="exp-side-stack"><section class="card"><div class="card-head"><h2>语音链路</h2></div><div class="card-body">${field('收音', '本机浏览器麦克风')}${field('识别', '机器人本地语音服务')}${field('反馈', '机器人叫声与动作')}<details><summary>连接条件与录音范围</summary><p>正式使用需安全连接与麦克风权限。按住时收音，松开后发送；最长 12 秒、16 kHz 单声道，不保存录音。原型不采集真实声音。</p></details></div></section><section class="card"><div class="card-head"><h2>其他语音功能</h2></div><div class="card-body"><details class="exp-more-commands"><summary>更多动作口令</summary><div class="exp-voice-commands">${VOICE_COMMANDS.filter(text => !['站起来', '开始走', '坐下', '前进'].includes(text)).map(text => button(text, 'exp-command', sensing.command === text ? 'selected' : '', `data-command="${text}" ${sensing.talk ? 'disabled' : ''}`)).join('')}</div></details><a class="btn" href="#/sensing/audio">机器人监听与音乐</a><a class="link exp-audio-link" href="#/settings/ai">AI 文字试聊与连接设置 ${icon('arrow', 13)}</a></div></section></div></div>`;
  }
  function audio() {
    const usable = !blocked();
    return `<div class="exp-content-grid"><section class="card"><div class="card-head"><h2>机器人监听</h2>${chip(!usable ? '语音服务不可用' : sensing.listening ? sensing.music ? '正在听音乐和提问' : '正在听你说话' : '监听已关闭', usable && sensing.listening)}</div><div class="card-body"><div class="exp-audio-state">${icon(sensing.music ? 'activity' : 'mic', 38)}<div><h3>${sensing.listening ? sensing.music ? '音乐模式' : '语音互动' : '开启后，直接对小鸭说话'}</h3><p>使用机器人上的收音设备</p></div></div><div class="exp-audio-primary">${button(sensing.listening ? '关闭监听' : '开启监听', 'exp-listening', sensing.listening ? '' : 'primary', usable || sensing.listening ? '' : 'disabled')}${button(sensing.music ? '退出音乐模式' : '听音乐', 'exp-music-listen', '', usable && sensing.listening || sensing.music ? '' : 'disabled')}</div>${usable && sensing.listening && sensing.music ? `<div class="exp-rhythm"><strong>轻快</strong><span>104 BPM</span><span class="muted">节拍示意</span></div>` : ''}<details><summary>口令与音乐模式</summary><p>监听持续到关闭，R3 可切换。普通模式支持“坐下”“站起来”，用叫声确认。音乐模式可问“感觉怎么样”，暂不执行坐站口令，也不复读整首歌。</p></details></div></section><div class="exp-side-stack"><section class="card"><div class="card-head"><h2>随乐律动</h2>${chip(remote.idle === '随乐律动中' ? '律动中' : '尚未开启', remote.idle === '随乐律动中')}</div><div class="card-body"><p class="exp-recorder-intro">先听音乐，站稳并开始走，再开启随乐律动。</p>${button(remote.idle === '随乐律动中' ? '停止律动' : '前往遥控开启', remote.idle === '随乐律动中' ? 'exp-stop-music' : 'exp-goto-music', remote.idle === '随乐律动中' ? 'danger' : 'primary')}<details><summary>持续运行与停止</summary><p>独立律动可在站内切页或后台继续；关闭网页或连接断开后停止。录制中的律动需保持遥控区在前台。没有稳定节拍时等待，音乐或收音停止后回正。</p></details></div></section><section class="card"><div class="card-head"><h2>AI 对话</h2></div><div class="card-body"><a class="btn" href="#/settings/ai">配置连接与文字试聊</a><p class="exp-small-note">文字试聊与网页语音口令分别使用。</p></div></section></div></div>`;
  }
  P.register('sensing', {
    render(subpage) { const current = ['camera', 'tof', 'voice', 'audio'].includes(subpage) ? subpage : 'camera'; return tabs('sensing', current, [['camera', '摄像头'], ['tof', 'ToF 与距离'], ['voice', '网页语音'], ['audio', '监听与音乐']]) + ({ camera, tof, voice, audio })[current](); },
    handle(action, target) {
      if (action === 'exp-preview') { if (!sensing.preview && (P.state.offline || P.state.stale)) { P.toast('等待有效连接后再开启预览'); return true; } sensing.preview = !sensing.preview; }
      else if (action === 'exp-reconnect') { if (P.state.offline) { P.toast('连接仍不可用，等待设备恢复'); return true; } sensing.preview = true; }
      else if (action === 'exp-zone') { const zone = Number(target.dataset.zone); if (Number.isInteger(zone) && zone >= 0 && zone < 64) sensing.zone = zone; }
      else if (action === 'exp-tof-refresh') { if (P.state.offline || P.state.stale) { P.toast('等待有效新读数'); return true; } sensing.tofUpdated = new Date().toLocaleTimeString('zh-CN', { hour12: false }); P.toast('距离读数已更新'); }
      else if (action === 'exp-command') { if (!sensing.talk && VOICE_COMMANDS.includes(target.dataset.command)) sensing.command = target.dataset.command; }
      else if (action === 'exp-voice-stop') { neutral(); cancelTalk(); if (!blocked()) { remote.posture = P.state.torque ? '站立' : '已卸力'; P.state.standing = !!P.state.torque; } sensing.transcript = '停下'; sensing.response = blocked() ? '连接不可用，无法确认停止' : '已请求停止行走'; }
      else if (action === 'exp-quack') { if (blocked()) { P.toast(whyBlocked()); return true; } cancelTalk(); P.toast('机器人叫声已演示'); }
      else if (action === 'exp-listening' || action === 'exp-music-listen') changeAudio(action);
      else if (action === 'exp-stop-music') remote.idle = '';
      else if (action === 'exp-goto-music') { P.navigate('remote/control'); return true; }
      else return false;
      P.render(); return true;
    }
  });
  function startHold(el) {
    const name = el.dataset.expHold;
    if (motionBlocked()) { P.toast(motionReason()); return false; }
    if (foreignDriver() || el.dataset.holdGroup === 'trigger' && remote.paired) { P.toast(foreignDriver() ? `${remote.ownerName}正在遥控` : '实体手柄已连接，虚拟扳机让位'); return false; }
    if (remote.playing) { P.toast('先停止回放，再操作动作'); return false; }
    if (!['张嘴', '张嘴嘎一声', '张嘴长叫'].includes(name) && remote.posture !== '行走中') { P.toast('先开始走，再做头部与组合动作'); return false; }
    remote.idle = ''; remote.held = name; el.classList.add('held'); if (name !== '连续前滚翻') note(name); updateReadouts(); return true;
  }
  function startTalk(el) {
    if (blocked()) return;
    sensing.talk = true; sensing.transcript = ''; sensing.response = ''; el.setAttribute('aria-pressed', 'true'); el.closest('.exp-voice-orbit').classList.add('listening');
    document.querySelector('[data-exp-talk-title]').textContent = '松开发送';
    document.querySelector('[data-exp-talk-hint]').textContent = `口令演示：${sensing.command}`;
  }
  function cancelTalk() {
    if (sensing.talk) sensing.response = '本次口令已取消';
    sensing.talk = false;
    document.querySelectorAll('[data-exp-talk]').forEach(el => { el.setAttribute('aria-pressed', 'false'); el.closest('.exp-voice-orbit')?.classList.remove('listening'); });
    const title = document.querySelector('[data-exp-talk-title]'), hint = document.querySelector('[data-exp-talk-hint]');
    if (title) title.textContent = '鸭哥，听我说';
    if (hint) hint.textContent = '按住说话，松开发送';
  }
  function finishTalk(cancelled) {
    cancelTalk();
    if (!cancelled && !blocked()) { sensing.transcript = sensing.command; sensing.response = '口令发送流程已演示'; }
    P.render();
  }
  // Pointer movement updates only the live nodes; replacing the page would lose pointer capture mid-gesture.
  document.addEventListener('pointerdown', event => {
    const stick = event.target.closest('[data-exp-stick]');
    const hold = event.target.closest('[data-exp-hold]');
    const talk = event.target.closest('[data-exp-talk]');
    const wheel = event.target.closest('[data-exp-wheel]');
    if (!stick && !hold && !talk && !wheel || event.button !== 0) return;
    if (stick && !moveAllowed()) return;
    if (hold && motionBlocked()) { P.toast(motionReason()); return; }
    if (talk && blocked()) return;
    if (wheel && (motionBlocked() || remote.paired || foreignDriver() || axesActive())) { P.toast(motionBlocked() ? motionReason() : '先释放摇杆与控制占用，再切换模式'); return; }
    const el = stick || hold || talk || wheel;
    el.setPointerCapture(event.pointerId);
    if (stick) { event.preventDefault(); pointers.set(event.pointerId, { type: 'stick', el, side: stick.dataset.expStick, x: event.clientX, y: event.clientY }); remote.idle = ''; }
    if (hold) {
      const pointer = { type: 'pending-hold', el, x: event.clientX, y: event.clientY };
      pointers.set(event.pointerId, pointer);
      if (event.pointerType === 'touch' || event.pointerType === 'pen') {
        // A moving touch is a page scroll, not a motion request.
        pointer.timer = setTimeout(() => { if (!pointers.has(event.pointerId)) return; if (startHold(el)) pointer.type = 'hold'; else pointers.delete(event.pointerId); }, 120);
      } else if (startHold(el)) pointer.type = 'hold';
      else pointers.delete(event.pointerId);
    }
    if (talk) { event.preventDefault(); pointers.set(event.pointerId, { type: 'talk', el }); startTalk(el); }
    if (wheel) {
      const pointer = { type: 'wheel', el }; pointers.set(event.pointerId, pointer); el.textContent = '继续按住，满 3 秒切换…';
      pointer.timer = setTimeout(() => {
        if (!pointers.has(event.pointerId)) return; pointers.delete(event.pointerId);
        if (motionBlocked() || remote.paired || foreignDriver()) { el.textContent = '切换已取消'; return; }
        if (!remote.rollerAvailable) { el.textContent = '按住 3 秒切到轮滑'; P.toast('未加载轮滑模型，切换被拒绝'); return; }
        P.stopRemote(); remote.wheelMode = !remote.wheelMode; remote.posture = P.state.torque ? '站立' : '已卸力'; P.render(); P.toast(`已演示切到${remote.wheelMode ? '轮滑' : '步行'}模式`);
      }, 3000);
    }
  });
  document.addEventListener('pointermove', event => {
    const pointer = pointers.get(event.pointerId);
    if (pointer?.type === 'pending-hold' && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) >= 8) { clearTimeout(pointer.timer); pointers.delete(event.pointerId); return; }
    if (pointer?.type !== 'stick') return;
    if (!moveAllowed()) { neutral(); return; }
    const x = Math.max(-1, Math.min(1, (event.clientX - pointer.x) / 40));
    const y = Math.max(-1, Math.min(1, (event.clientY - pointer.y) / 40));
    const prefix = pointer.side === 'left' ? 'l' : 'r';
    remote.axes[prefix + 'x'] = x; remote.axes[prefix + 'y'] = y;
    pointer.el.style.transform = `translate(${x * 32}px,${y * 32}px)`;
    pointer.el.setAttribute('aria-valuenow', String(Math.round(y * 100))); pointer.el.setAttribute('aria-valuetext', `${Math.round(x * 100)}, ${Math.round(y * 100)}`);
    updateReadouts();
    if (!pointer.moveRecorded) pointer.moveRecorded = recordWalkInput();
  });
  function endPointer(event, cancelled) {
    const pointer = pointers.get(event.pointerId);
    if (!pointer) return;
    pointers.delete(event.pointerId);
    clearTimeout(pointer.timer);
    if (pointer.type === 'stick') { const prefix = pointer.side === 'left' ? 'l' : 'r'; note(`${remote.mode}摇杆操作`); remote.axes[prefix + 'x'] = 0; remote.axes[prefix + 'y'] = 0; pointer.el.style.transform = ''; pointer.el.setAttribute('aria-valuenow', '0'); pointer.el.setAttribute('aria-valuetext', '归中'); updateReadouts(); }
    if (pointer.type === 'hold') { remote.held = ''; pointer.el.classList.remove('held'); updateReadouts(); }
    if (pointer.type === 'wheel') pointer.el.textContent = `按住 3 秒切到${remote.wheelMode ? '步行' : '轮滑'}`;
    if (pointer.type === 'talk') finishTalk(cancelled);
  }
  document.addEventListener('pointerup', e => endPointer(e, false));
  document.addEventListener('pointercancel', e => endPointer(e, true));
  document.addEventListener('lostpointercapture', e => endPointer(e, true));
  document.addEventListener('focusout', event => { if (keys.size && event.target.closest('.exp-drive-focus') && !event.relatedTarget?.closest('.exp-drive-focus')) neutral(); });
  document.addEventListener('keydown', event => {
    const inField = /INPUT|TEXTAREA|SELECT/.test(event.target.tagName);
    if (inField) return;
    if (event.code === 'Enter' && !event.repeat) {
      const talk = event.target.closest('[data-exp-talk]'), hold = event.target.closest('[data-exp-hold]');
      if (talk || hold) { event.preventDefault(); if (talk) startTalk(talk); else startHold(hold); return; }
    }
    if (event.code === 'Escape') { neutral(); return; }
    if (event.code === 'Space' && location.hash.startsWith('#/remote')) { event.preventDefault(); document.querySelector('[data-action="emergency"]')?.click(); return; }
    if (!document.activeElement?.closest('.exp-drive-focus') || remote.mode !== '走路') return;
    if (!['KeyW', 'KeyS', 'KeyA', 'KeyD', 'KeyQ', 'KeyE'].includes(event.code)) return;
    event.preventDefault(); if (event.repeat) return; if (!moveAllowed()) return; keys.add(event.code);
    remote.axes.ly = (keys.has('KeyS') ? 1 : 0) - (keys.has('KeyW') ? 1 : 0); remote.axes.lx = (keys.has('KeyD') ? 1 : 0) - (keys.has('KeyA') ? 1 : 0); remote.axes.rx = (keys.has('KeyE') ? 1 : 0) - (keys.has('KeyQ') ? 1 : 0); updateReadouts(); recordWalkInput();
  });
  document.addEventListener('keyup', event => {
    if (event.code === 'Enter') { if (sensing.talk) finishTalk(false); if (remote.held) { remote.held = ''; document.querySelectorAll('[data-exp-hold]').forEach(el => el.classList.remove('held')); updateReadouts(); } }
    if (!keys.delete(event.code)) return;
    remote.axes.ly = (keys.has('KeyS') ? 1 : 0) - (keys.has('KeyW') ? 1 : 0); remote.axes.lx = (keys.has('KeyD') ? 1 : 0) - (keys.has('KeyA') ? 1 : 0); remote.axes.rx = (keys.has('KeyE') ? 1 : 0) - (keys.has('KeyQ') ? 1 : 0); note('键盘遥控'); updateReadouts();
  });
  document.addEventListener('change', event => {
    if (event.target.matches('[data-exp-pad-kind]')) { if (managementBlocked()) { P.toast(whyBlocked()); return; } remote.wizard.kind = event.target.value; remote.wizard.phase = 'choose'; remote.wizard.candidates = []; P.render(); return; }
    if (blocked() || isRecording() || remote.recording === 'draft' || remote.playing) return;
    if (event.target.matches('[data-exp-loops]') && [1, 3, 5].includes(Number(event.target.value))) remote.loops = Number(event.target.value);
    if (event.target.matches('[data-exp-clip]') && remote.clips.some(item => item.id === event.target.value)) { remote.selected = event.target.value; P.render(); }
  });
  function observeRemoteActivity() {
    activityObserver?.disconnect(); const generation = ++activityGeneration;
    const region = document.querySelector('#page-content');
    if (!region || typeof IntersectionObserver === 'undefined') { remoteInView = true; return; }
    const mobile = window.matchMedia?.('(max-width:680px)').matches;
    activityObserver = new IntersectionObserver(entries => {
      if (generation !== activityGeneration) return;
      remoteInView = entries.some(entry => entry.isIntersecting);
      const active = axesActive() || remote.held || isRecording() || remote.playing || remote.idle && remote.idle !== '随乐律动中';
      if (!remoteInView && active) { leaveControl(); P.render(); }
    }, { rootMargin: `-${mobile ? 57 : 63}px 0px -${mobile ? 88 : 32}px 0px`, threshold: 0 });
    activityObserver.observe(region);
  }
  function leaveControl() {
    const backgroundMusic = remote.idle === '随乐律动中' && !isRecording();
    neutral(); remote.mode = '走路'; cancelTalk();
    if (isRecording()) finishRecording(); remote.playing = false; if (!backgroundMusic) remote.idle = '';
  }
  function interruptForVisibility() {
    const changed = axesActive() || remote.held || isRecording() || remote.playing || sensing.talk || remote.idle && remote.idle !== '随乐律动中';
    leaveControl(); if (changed) P.render();
  }
  window.addEventListener('blur', interruptForVisibility);
  window.addEventListener('pagehide', leaveControl);
  document.addEventListener('visibilitychange', () => { if (document.hidden) interruptForVisibility(); });
  window.addEventListener('hashchange', () => { activityObserver?.disconnect(); ++activityGeneration; remoteInView = true; neutral(); cancelTalk(); if (!location.hash.startsWith('#/remote')) leaveControl(); });
  setInterval(() => {
    if (remote.recording === 'recording' && duration() >= 60) { finishRecording(); if (location.hash.startsWith('#/remote')) P.render(); }
    if (remote.playing === 'playing') { const clip = chosenClip(); if (!clip || playTime() >= clip.duration * remote.loops) { remote.playing = false; if (location.hash.startsWith('#/remote')) P.render(); } }
    document.querySelectorAll('[data-exp-clock]').forEach(el => { el.textContent = clock(remote.playing ? playTime() : duration()); });
    const output = document.querySelector('[data-exp-play]'), clip = chosenClip();
    if (output && clip && remote.playing) output.textContent = `${clock(playTime() % clip.duration)} / ${clock(clip.duration)} · 第 ${Math.min(remote.loops, Math.floor(playTime() / clip.duration) + 1)}/${remote.loops} 次`;
  }, 200);
})();
