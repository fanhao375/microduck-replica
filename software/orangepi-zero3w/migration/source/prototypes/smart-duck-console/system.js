(function () {
  'use strict';
  const P = window.P;
  const S = P.state.system = {
    modelFilter: 'all', logFilter: 'all', logCategory: '', logSearch: '', logPaused: false,
    wifi: 'Studio WiFi', wifiOn: true, hotspot: false, ssh: false,
    lowBattery: true, showStatus: true, compact: false, updates: 'idle',
    modelSlot: 'walk', modelName: '行走模型', imported: [], bindings: {},
    slots: { walk: 'walk', sitstand: 'sit', ground_pick: 'pick', kick_left: 'kick-left', kick_right: 'kick-right', roulade: 'roll', stand: null },
    loaded: { walk: 'walk', sitstand: 'sit', ground_pick: 'pick', kick_left: 'kick-left', kick_right: 'kick-right', roulade: 'roll', stand: null }, executing: null,
    aiSaved: false, aiSelected: '', aiCurrent: '', aiConnections: [], aiDirty: false, passwordSet: true, keys: [],
    battery: { preset: '2s_liion', warn: 30, red: 15, floor: 6.9, offset: 0, beep: true }, meterOffset: null,
    profile: { total_g: '', trunk_g: '', head_g: '', leg_left_g: '', leg_right_g: '', foot: '', note: '' },
    stage: 'light', savedNetworks: ['Studio WiFi', 'SmartDuck Lab'], updateFile: '',
    modelError: '', bindingPreset: '', aiDraft: null, aiResult: '', aiMessages: [], formDirty: {}, formDrafts: {},
    networkPhase: 'idle', pendingSsid: '', networkError: '',
    updateError: '', updateMode: 'install', updateInstalledVersion: 'A7Z 适配版', updatePreviousVersion: null,
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const button = (text, action, extra = '', cls = '') => `<button type="button" class="btn ${cls}" data-action="${action}" ${extra}>${text}</button>`;
  const badge = (text, type = '') => P.badge(text, type);
  const card = (title, content, action = '') => `<section class="card sys-card"><div class="card-head"><h2>${title}</h2>${action}</div><div class="card-body">${content}</div></section>`;
  const field = (label, name, value, type = 'text', extra = '') => `<label class="field"><span>${label}</span><input type="${type}" name="${name}" value="${esc(value)}" ${extra}></label>`;
  const select = (label, name, options, current) => `<label class="field"><span>${label}</span><select name="${name}">${options.map(([value, text]) => `<option value="${value}" ${value === current ? 'selected' : ''}>${text}</option>`).join('')}</select></label>`;
  const switchRow = (title, key, note = '') => `<div class="sys-setting-row"><div><strong>${title}</strong>${note ? `<span class="muted">${note}</span>` : ''}</div><button type="button" class="switch ${S[key] ? 'on' : ''}" role="switch" aria-checked="${S[key]}" aria-label="${title}" data-action="system-toggle" data-key="${key}"><span></span></button></div>`;
  const row = (title, value, action = '') => `<div class="sys-setting-row"><span>${title}</span><div class="sys-row-value">${value}${action}</div></div>`;
  const categoryNav = (base, items, current) => `<nav class="subnav sys-subnav" aria-label="${base === 'settings' ? '系统设置' : '模型中心'}分类">${items.map(([key, label, icon]) => `<a href="#/${base}/${key}" class="${key === current ? 'active' : ''}" ${key === current ? 'aria-current="page"' : ''}>${P.icon(icon, 14)}<span>${label}</span></a>`).join('')}</nav>`;
  const save = (label, action = 'system-save', extra = '') => `<div class="sys-form-actions">${button(label, action, extra, 'primary')}</div>`;
  const slotLabels = { walk: '走路 / 保持', sitstand: '坐下 / 站起', ground_pick: '低头捡东西', kick_left: '左脚踢', kick_right: '右脚踢', roulade: '前滚翻', stand: '站立（独立）' };
  const defaultSlots = { ...S.slots };
  const defaultBindings = { a: 'ground_pick', x: 'roulade', lb: 'kick_left', rb: 'kick_right', dpad_down: 'sitstand' };
  const buttonLabels = { a: 'A', x: 'X', lb: 'LB', rb: 'RB', dpad_down: '十字下' };
  const modelSpecs = [['walk','行走模型','walk','平稳迈步与方向控制'],['sit','起坐模型','sitstand','坐下与站起的姿态过渡'],['pick','低头捡取','ground_pick','低头并执行捡取动作'],['kick-left','左脚踢','kick_left','左脚单次踢腿动作'],['kick-right','右脚踢','kick_right','右脚单次踢腿动作'],['roll','前滚翻','roulade','完成一次前滚翻动作'],['stand','独立站立','stand','独立站立用途，默认停用']];
  const models = modelSpecs.map(([id,name,slot,description]) => ({ id, name, allowed: [slot], slot, label: slotLabels[slot], description, kind: slot === 'walk' ? 'walk' : ['sitstand','stand'].includes(slot) ? 'posture' : 'skill', version: '内置模型', installed: true }));
  const modelName = id => getModel(id)?.name || (id ? '未收录模型' : '未配置');
  const selectedSlots = id => Object.keys(S.slots).filter(slot => S.slots[slot] === id);
  const bodyFields = [['total_g','整鸭重量'],['trunk_g','躯干'],['head_g','头部'],['leg_left_g','左腿'],['leg_right_g','右腿']];
  const voltage = () => Number(P.state.batteryVolts ?? 7.8);
  const batteryPercent = () => P.batteryPercent(voltage() + Number(S.battery.offset));
  function modelEditReason() {
    if (P.isSystemBusy?.()) return '软件正在安装或重新连接，请等版本结果读回';
    if (P.state.offline || P.state.stale || P.state.boot !== 'ready') return '主程序回读不可用，暂不能修改模型或按键';
    if (['handoff','verifying'].includes(P.state.calibrationStatus) || ['failed','returning'].includes(P.state.maintenanceStatus)) return '请先完成标定交接或恢复总线回读';
    if (P.state.owned) return '请先交还舵机总线';
    if (P.torqueStatus() !== '已卸力' || P.state.moving) return '先停止策略并卸力，再修改模型或按键';
    return '';
  }
  function requireIdle() { const reason=modelEditReason(); if(reason)P.toast(reason); return !reason; }
  function updateBlockReason() {
    const reason=modelEditReason();if(reason)return reason;
    const remote=P.state.remote||{},imu=P.state.devices?.imu;
    if(['recording','paused'].includes(remote.recording)||remote.playing||remote.idle||Object.values(remote.axes||{}).some(value=>value!==0))return '先结束遥控、录制和回放，再验证更新';
    if(P.state.sensing?.listening||P.state.sensing?.music)return '先关闭语音监听和音乐，再验证更新';
    if(P.state.devices?.tracking?.active||imu?.coarse?.active||imu?.fine?.phase==='collect')return '先结束关节跟踪和标定采集，再验证更新';
    return '';
  }
  function updateFailure(message) { S.updateError=message;P.render(); }
  const aiReady = () => { const item=S.aiConnections.find(c=>c.id===S.aiSelected); return S.passwordSet&&!!item&&!aiMissing(item).length&&!S.aiDirty; };
  const savedProfile = () => Object.fromEntries(bodyFields.map(([key])=>[key,S.profile[key]===''?null:Number(S.profile[key])]));
  const logs = [
    ['11:24:08.204', 'info', '主程序', '收到有效回读，15 颗舵机在线'],
    ['11:24:07.186', 'command', '网页遥控', '停止行走 → 已接受，速度已回零'],
    ['11:23:45.612', 'command', '网页遥控', '开始行走 → 已接受'],
    ['11:23:39.088', 'command', '网页遥控', '站起来 → 已接受，姿态已确认'],
    ['11:22:16.503', 'warn', '舵机总线', 'ID 33 读取超时 · 后续回读已恢复'],
    ['11:20:14.117', 'info', 'IMU', '安装方向已确认，姿态数据有效'],
    ['11:18:52.830', 'info', 'Studio', '已连接主程序，控制模式'],
    ['11:18:50.406', 'info', '系统', '网页中控启动完成'],
  ];
  const logWords = { info: '信息', command: '命令', warn: '警告', error: '错误' };
  const logEvidence = {
    '11:22:16.503': { step: 'robot.busStatus / sync_read', raw: 'id=33, result=timeout', hint: '对照当前会话的新增错误与回读时间，不清除历史计数。' },
    '11:23:39.088': { step: 'pad.op stand → 主程序回执 → 姿态回读', raw: 'accepted=true', hint: '命令接受与姿态回读分别展示。' },
  };
  const shownLogs = () => logs.filter(item => (S.logFilter === 'all' || item[1] === S.logFilter) && (!S.logCategory || item[2] === S.logCategory) && (!S.logSearch || item.join(' ').toLowerCase().includes(S.logSearch.toLowerCase())));
  function modelLibrary() {
    const filtered = [...models, ...S.imported].filter(item => S.modelFilter === 'all' || item.kind === S.modelFilter);
    const blocked=modelEditReason();
    return `<div class="sys-toolbar"><div class="segmented">${[['all', '全部模型'], ['walk', '行走'], ['posture', '姿态'], ['skill', '技能']].map(([key, label]) => button(label, 'system-model-filter', `data-value="${key}" aria-pressed="${S.modelFilter === key}"`, key === S.modelFilter ? 'active' : '')).join('')}</div><div class="sys-actions">${button('刷新状态','system-model-refresh')}${button(`${P.icon('upload')} 导入模型`, 'system-model-import', '', 'primary')}</div></div>${S.modelError?`<div class="sys-inline-result error" role="alert">${esc(S.modelError)}</div>`:''}
      <div class="sys-model-grid">${filtered.map(item => {
        const selected = selectedSlots(item.id), pending = !item.installed, waiting = selected.some(slot => S.loaded[slot] !== item.id), executing = S.executing && S.loaded[S.executing] === item.id;
        return `<article class="card sys-model-card"><div class="sys-model-symbol">${P.icon(item.kind === 'walk' ? 'activity' : item.kind === 'posture' ? 'layers' : 'sparkles', 24)}</div><div class="sys-model-meta">${badge(pending ? '兼容性待校验' : executing ? '正在执行' : waiting ? '已选用 · 待加载' : selected.length ? '当前选用' : '已安装', pending || waiting ? 'warning' : selected.length || executing ? 'success' : '')}</div><h2>${esc(item.name)}</h2><p class="muted">${esc(item.description)}</p><div class="sys-model-purpose">${pending ? '尚不能选用' : `用途：${esc(item.label)}`}</div><div class="sys-model-foot"><span class="muted">${item.version}</span>${button(selected.length ? '已选用' : '选用', 'system-model-use', `data-model="${item.id}" title="${esc(blocked||'')}" ${pending || selected.length || blocked ? 'disabled' : ''}`, selected.length || pending ? '' : 'primary')}${button('详情', 'system-model-detail', `data-model="${item.id}"`)}</div></article>`;
      }).join('')}</div>
      <details class="card sys-slot-card"><summary>当前用途与主程序加载状态</summary><div class="sys-slot-list">${Object.entries(slotLabels).map(([slot,label]) => `<div class="sys-slot-row"><div><strong>${label}</strong><span>选用：${esc(modelName(S.slots[slot]))}</span><small>主程序加载：${S.loaded[slot] ? esc(modelName(S.loaded[slot])) : '未加载'}${S.executing === slot ? ' · 正在执行' : ' · 未执行'}</small></div>${button(defaultSlots[slot] ? '恢复内置' : '恢复默认停用', 'system-model-restore', `data-slot="${slot}" ${S.slots[slot] === defaultSlots[slot] ? 'disabled' : ''}`)}</div>`).join('')}</div></details>
      <p class="sys-caption muted">选用与加载分别显示；切换模型不会自动开始动作。</p>`;
  }
  function modelBindings() {
    const choices = Object.entries(slotLabels).filter(([slot]) => !['walk','stand'].includes(slot) && S.slots[slot]);
    const blocked=modelEditReason(),dirty=!!S.bindingDraft&&Object.keys(defaultBindings).some(key=>S.bindingDraft[key] !== (S.bindings[key]??defaultBindings[key]));
    return card('动作按键', `${S.bindingPreset?`<p class="sys-caption muted">已选择“${slotLabels[S.bindingPreset]}”，将要使用的按键改为此动作后保存。</p>`:''}<div class="sys-bindings">${Object.entries(buttonLabels).map(([key,label]) => row(`<kbd>${label}</kbd>`, `<select data-binding="${key}" aria-label="${label} 对应动作" ${blocked?'disabled':''}><option value="">不绑定</option>${choices.map(([slot,text]) => `<option value="${slot}" ${slot === (S.bindingDraft?.[key] ?? S.bindings[key] ?? defaultBindings[key]) ? 'selected' : ''}>${text} · ${esc(modelName(S.slots[slot]))}</option>`).join('')}</select>`)).join('')}${row('<kbd>Select</kbd>', '卸力 · 固定急停键')}${row('站立与行走', '使用遥控页的站、走控制')}</div><div class="sys-form-actions">${button('保存按键', 'system-binding-save', 'data-dirty-save="bindings" '+(blocked||!dirty?'disabled':''), 'primary')}${button('按键捕获演示', 'system-binding-capture',blocked?'disabled':'')}</div>`, button('恢复默认', 'system-binding-reset',blocked?'disabled':''));
  }
  function modelProfile() {
    const body=S.formDrafts.profile||S.profile;
    return `<div class="sys-settings-stack">${card('模型与机器档案', `${row('主控与关节', 'Cubie A7Z / 15 颗 HD-1910')}${row('行走选用', esc(modelName(S.slots.walk)))}${row('主程序加载', esc(modelName(S.loaded.walk)))}${row('当前执行', S.executing ? slotLabels[S.executing] : '无')}<form id="sys-profile-form"><details class="sys-details" ${S.formDirty.profile?'open':''}><summary>重量、脚底材料与备注</summary><div class="sys-form-grid">${bodyFields.map(([key,label]) => field(`${label}（g）`, key, body[key], 'number', 'min="0" max="5000" step="0.1" placeholder="未填写"')).join('')}${field('脚底材料', 'foot', body.foot, 'text', 'maxlength="40" placeholder="例如：橡胶"')}${field('备注', 'note', body.note, 'text', 'maxlength="200"')}</div>${save('保存档案', 'system-profile-save', 'data-dirty-save="profile" '+(S.formDirty.profile?'':'disabled'))}</details></form><div class="sys-form-actions">${button(`${P.icon('download')} 导出鸭子档案`, 'system-profile-export', S.formDirty.profile?'disabled':'', 'primary')}</div><p class="sys-caption muted">导出包含标定、模型、运行参数与已填写的重量；未填写的重量记为 null。</p>`)}${card('调手感', `<div class="sys-value-grid"><div><span class="muted">行走幅度</span><strong>0.25</strong></div><div><span class="muted">头部平滑</span><strong>0.80</strong></div><div><span class="muted">腿部平滑</span><strong>0.00</strong></div></div><details class="sys-details"><summary>参数、生效来源与模型信息</summary>${row('运行 P / D', '5 / 0')}${row('运行限力（原始值）', '1000')}${row('嘴巴开度 / 头部幅度', '65% / 0.5 rad')}${row('来源', '主程序运行配置')}${row('训练轮数 / 文件指纹', '由实际模型元数据提供')}<p class="muted">当前调手感参数只读。</p></details>`, badge('只读'))}</div>`;
  }
  P.register('models', {
    render(sub = 'library') {
      const current = ['library', 'bindings', 'profile'].includes(sub) ? sub : 'library';
      return `<div class="sys-page page-grid sys-layout">${categoryNav('models', [['library', '模型库', 'layers'], ['bindings', '手柄按键', 'remote'], ['profile', '机器档案与参数', 'log']], current)}<div class="sys-content">${current === 'bindings' ? modelBindings() : current === 'profile' ? modelProfile() : modelLibrary()}</div></div>`;
    }, handle, afterRender,
  });
  P.register('logs', {
    render() {
      const shown = shownLogs();
      return `<div class="sys-page"><div class="sys-toolbar"><div class="segmented">${[['all', '全部'], ['warn', '警告'], ['error', '错误'], ['command', '命令']].map(([key, label]) => button(label, 'system-log-filter', `data-value="${key}" aria-pressed="${S.logFilter === key}"`, S.logFilter === key ? 'active' : '')).join('')}</div><div class="sys-actions">${button(`${P.icon('download')} 导出诊断包`, 'system-diag-download', '', 'primary')}</div></div>
        <section class="card sys-log-card"><div class="card-head"><div class="sys-log-search"><select id="sys-log-category" aria-label="按来源筛选"><option value="">全部来源</option>${[...new Set(logs.map(item => item[2]))].map(cat => `<option ${cat === S.logCategory ? 'selected' : ''}>${cat}</option>`).join('')}</select><input id="sys-log-search" type="search" placeholder="搜索日志内容" value="${esc(S.logSearch)}" aria-label="搜索日志内容">${button('筛选', 'system-log-search')}</div><div class="sys-actions">${badge(S.logPaused ? '已暂停' : '示例记录')}${button(S.logPaused ? '继续更新' : '暂停更新', 'system-log-pause')}</div></div>
        <div class="sys-log-list"><div class="sys-log-heading"><span>时间</span><span>级别</span><span>来源</span><span>内容</span></div>${shown.length ? shown.map(item => { const detail = logEvidence[item[0]]; return `<div class="sys-log-row"><time>${item[0]}</time><span>${badge(logWords[item[1]], item[1] === 'warn' ? 'warning' : item[1] === 'error' ? 'danger' : '')}</span><span>${item[2]}</span><div class="sys-log-message"><p>${item[3]}</p>${detail ? `<details><summary>步骤与原始信息</summary><p>步骤：${esc(detail.step)}</p><p>原文：<code>${esc(detail.raw)}</code></p><p>建议：${esc(detail.hint)}</p></details>` : ''}</div></div>`; }).join('') : `<div class="empty-state">${P.icon('search', 28)}<h3>没有匹配的日志</h3><p class="muted">换个关键词或筛选条件。</p>${button('清除筛选', 'system-log-clear')}</div>`}</div><div class="sys-log-footer"><span class="muted">最近 ${shown.length} 条记录</span>${button('复制当前日志', 'system-log-copy')}</div></section>
        ${card('诊断信息', `<div class="sys-value-grid"><div><span class="muted">Studio</span><strong>A7Z 适配版</strong></div><div><span class="muted">主程序</span><strong>sync3.1</strong></div><div><span class="muted">数据来源</span><strong>原型样例</strong></div></div>`)}
      </div>`;
    }, handle,
  });
  function networkSettings() {
    const connected = S.wifiOn && S.wifi !== '未连接' && S.networkPhase!=='switching';
    return `${card('WiFi', `${switchRow('无线网络', 'wifiOn')}${row('当前网络', connected ? `<span class="sys-network-name">${P.icon('wifi')} ${esc(S.wifi)}</span>${badge('已连接', 'success')}` : '未连接')}${row('信号强度', connected ? '良好' : '—')}${S.networkPhase==='switching'?`<div class="sys-inline-result">正在切换到 ${esc(S.pendingSsid)}，等待设备重新出现。</div>`:''}${S.networkError?`<div class="sys-inline-result error" role="alert">${esc(S.networkError)}</div>`:''}<div class="sys-form-actions">${S.networkPhase==='switching'?button('重新寻找设备','system-network-find','','primary'):button('选择网络', 'system-wifi-scan', S.wifiOn ? '' : 'disabled', 'primary')}${S.networkError?button('回到设备热点','system-network-recovery'):''}</div><details class="sys-details"><summary>已保存的网络 · ${S.savedNetworks.length}</summary>${S.savedNetworks.map(name => row(esc(name), name === S.wifi ? badge('正在使用', 'success') : '', button('忘记', 'system-wifi-forget', `data-network="${esc(name)}" ${S.networkPhase==='switching'?'disabled':''}`))).join('') || '<p class="muted">没有已保存的网络。</p>'}</details>`)}
      ${card('设备地址', `${row('局域网 IP', connected ? '<code>http://192.0.2.114:8000/</code>' : '未获取', connected ? button('复制', 'system-copy-address', 'data-address="http://192.0.2.114:8000/"') : '')}${row('主机名', '<code>http://cubie-42f4.local:8000/</code>', button('复制', 'system-copy-address', 'data-address="http://cubie-42f4.local:8000/"'))}${S.hotspot ? row('设备热点', '<code>http://10.42.0.1:8000/</code>', button('复制', 'system-copy-address', 'data-address="http://10.42.0.1:8000/"')) : ''}<p class="sys-caption muted">与设备处于同一网络，手机打不开主机名时使用 IP。</p>`)}
      ${card('热点与连接', `${switchRow('设备热点', 'hotspot')}${row('热点名称', 'SmartDuck-Setup')}${row('热点地址', '10.42.0.1')}<details class="sys-details"><summary>换网后找回设备</summary><p>连接新 WiFi 后，将手机或电脑切回相同网络，再通过设备地址打开中控。连接失败时可回到设备热点重试。</p></details>`)}`;
  }
  function accountSettings() {
    return `${card('网页登录', `${row('登录密码', S.passwordSet ? badge('已设置', 'success') : badge('未设置'))}<div class="sys-form-actions">${button(S.passwordSet ? '修改登录密码' : '设置登录密码', 'system-password', '', 'primary')}${S.passwordSet ? button('清除密码', 'system-password-clear') : ''}</div>`)}
      ${card('SSH 远程登录', `${row('密码登录',badge(S.ssh?'已开启':'已关闭',S.ssh?'success':''))}${row('登录命令', '<code>ssh radxa@192.0.2.114</code>', button('复制', 'system-copy-ssh'))}<div class="sys-form-actions">${button(S.ssh?'修改 SSH 密码':'开启密码登录', 'system-ssh-password',S.passwordSet?'':'disabled','primary')}${S.ssh?button('关闭密码登录','system-ssh-off',S.passwordSet?'':'disabled'):''}</div><details class="sys-details"><summary>SSH 公钥 ${S.keys.length ? `· ${S.keys.length} 把` : ''}</summary>${S.keys.map((key, i) => row(esc(key.comment||'无注释'), `<code class="sys-key-fingerprint" title="${esc(key.fp)}">${esc(key.type)} ${esc(key.fp)}</code>`, button('移除', 'system-key-remove', `data-index="${i}"`))).join('')}<label class="field"><span>登记公钥</span><textarea id="sys-ssh-key" rows="2" placeholder="ssh-ed25519 …" aria-label="SSH 公钥" ${S.passwordSet?'':'disabled'}></textarea></label>${save('登记公钥', 'system-key-add',S.passwordSet?'':'disabled')}<p class="sys-caption muted">仅保留公钥类型、注释与指纹作为演示记录。</p></details>${!S.passwordSet?'<p class="sys-caption muted">先设置网页登录密码，再开启 SSH 或登记公钥。</p>':''}`)}
      ${card('会话', `${row('当前会话', '此浏览器')}${row('权限', '设备管理员')}<div class="sys-form-actions">${button('退出登录', 'system-logout')}</div>`)}`;
  }
  function aiSettings() {
    if(!S.passwordSet)return card('AI 文字连接',`<p class="sys-caption muted">设置网页登录密码后，可管理 AI 连接与文字试聊。</p><a class="btn primary" href="#/settings/account">设置登录密码</a>`);
    const previous = S.aiConnections.find(item => item.id === S.aiSelected);
    const connection = S.aiDraft||previous||{ provider: 'doubao', url: 'https://ark.cn-beijing.volces.com/api/v3' };
    const saved = !!previous, missing = aiMissing(connection), ready = aiReady();
    return `${card('AI 文字连接', `<div class="sys-ai-connections">${S.aiConnections.map(item => button(`${esc(item.name || '未命名草稿')}${item.id === S.aiCurrent ? ' · 当前' : ''}`, 'system-ai-select', `data-id="${item.id}"`, item.id === S.aiSelected ? 'primary' : '')).join('')}${button(`${P.icon('plus')} 新建连接`, 'system-ai-new', S.aiConnections.length >= 10 ? 'disabled' : '')}</div><form id="sys-ai-form"><div class="sys-form-grid">${field('连接名称', 'name', connection.name || '', 'text', 'placeholder="例如：我的文字模型" maxlength="80"')}${select('提供商', 'provider', [['doubao', '豆包·方舟'], ['compatible', 'OpenAI 兼容接口']], connection.provider)}${field('Base URL', 'url', connection.url || '', 'url', 'placeholder="https://服务地址/v1"')}${field('模型 ID', 'model', connection.model || '', 'text', 'placeholder="可以先保存草稿"')}${field('API Key', 'key', '', 'password', `placeholder="${connection.keyConfigured ? '已配置；留空保留同一地址的 Key' : '未配置，可先保存草稿'}" autocomplete="new-password"`)}<div><label class="sys-check"><input type="checkbox" name="noKey" ${connection.noKey ? 'checked' : ''}> 本地服务无需 Key</label><label class="sys-check"><input type="checkbox" name="clearKey" ${connection.clearKey?'checked':''}> 清除已保存的 Key</label></div></div></form><p class="sys-caption muted sys-ai-dirty">${S.aiDirty ? '有未保存的修改，请先保存。' : saved && missing.length ? `草稿已保存 · 待补齐：${missing.join('、')}` : saved ? '连接资料完整，可测试或试聊。' : '配置未齐也可以保存草稿。'}</p><div class="sys-form-actions">${button('保存连接', 'system-ai-save',S.aiDirty?'':'disabled', 'primary')}${button('设为当前', 'system-ai-current', ready && S.aiCurrent !== connection.id ? '' : 'disabled')}${button('测试连接', 'system-ai-test', ready ? '' : 'disabled')}${button('删除连接', 'system-ai-delete', saved ? '' : 'disabled')}</div>${S.aiResult?`<div class="sys-inline-result" role="status">${esc(S.aiResult)}</div>`:''}`, saved ? badge(missing.length ? '草稿' : '已保存', missing.length ? 'warning' : 'success') : badge('新连接'))}
      ${card('文字试聊', `<div class="sys-chat" id="sys-ai-chat">${S.aiMessages.length?S.aiMessages.map(item=>`<div class="sys-chat-${item.role}">${esc(item.text)}</div>`).join(''):`<div class="sys-chat-empty">${P.icon('message', 24)}<span class="muted">${ready?'发送消息，预览文字试聊。':'补齐并保存连接后，可演示文字试聊。'}</span></div>`}</div><div class="sys-chat-compose"><input id="sys-ai-message" aria-label="试聊内容" placeholder="输入一条消息…" maxlength="2000">${button('发送', 'system-ai-chat', ready ? '' : 'disabled', 'primary')}${button('清空', 'system-ai-chat-clear')}</div><p class="sys-caption muted">文字接口与动作口令、持续监听分别配置；本页不控制设备动作。</p>`)}`;
  }
  function aiMissing(connection) {
    const missing = [['name','名称'],['url','Base URL'],['model','模型 ID']].filter(([key]) => !connection[key]?.trim()).map(([,label]) => label);
    if (!connection.noKey && !connection.keyConfigured) missing.push('API Key');
    return missing;
  }
  function batterySettings() {
    const b = S.formDrafts.battery||S.battery, bench = b.preset === 'bench', applied=S.battery, appliedBench=applied.preset==='bench', unknown = P.state.offline || P.state.stale;
    return `${card('电池状态', `<div class="sys-battery"><div class="sys-battery-icon">${P.icon('battery', 48)}</div><div><strong>${unknown ? '—' : appliedBench ? voltage().toFixed(2) : `约 ${batteryPercent()}`}<span>${appliedBench ? 'V' : '%'}</span></strong><p class="muted">${unknown ? '等待有效电压回读' : `${voltage().toFixed(2)} V · ${appliedBench ? '台架电源，不估算电量' : '2S 电压查表估算'}`}</p></div></div><div class="sys-value-grid"><div><span class="muted">供电类型</span><strong>${appliedBench ? '台架电源' : '2S 锂电池'}</strong></div><div><span class="muted">校准偏移</span><strong>${Number(applied.offset).toFixed(2)} V</strong></div><div><span class="muted">HD-1910 上限</span><strong>8.4 V</strong></div></div>`)}
      ${card('电量设置', `<form id="sys-battery-form"><div class="sys-form-grid">${select('供电类型', 'preset', [['2s_liion','2S 锂电池'],['bench','台架电源（不算电量）']], b.preset)}${field('负载电压线（V）', 'floor', b.floor, 'number', 'min="6.6" max="7.4" step="0.1" required')}${field('提醒线（%）', 'warn', b.warn, 'number', `min="15" max="60" step="1" required ${bench ? 'disabled' : ''}`)}${field('红线（%）', 'red', b.red, 'number', `min="5" max="40" step="1" required ${bench ? 'disabled' : ''}`)}</div><details class="sys-details"><summary>电压校准与提示音</summary><div class="sys-form-grid">${field('读数校准偏移（V）', 'offset', b.offset, 'number', 'min="-0.5" max="0.5" step="0.01" required')}${field('万用表实测电压（V）', 'meter', '', 'number', 'min="6" max="8.4" step="0.01" placeholder="设备卸力静置 30 秒后测量"')}</div><div class="sys-form-actions">${button('计算校准偏移', 'system-battery-measure')}</div><label class="sys-check sys-battery-beep"><input type="checkbox" name="beep" ${b.beep ? 'checked' : ''}> 低电量提示音</label><p class="sys-caption muted">只在打开的网页上提示；电量提醒不会自动停止设备。</p></details></form><div class="sys-form-actions">${button('保存设置', 'system-battery-save', 'data-dirty-save="battery" '+(S.formDirty.battery?'':'disabled'), 'primary')}${button('恢复默认', 'system-battery-default')}</div><p class="sys-caption muted">红线需比提醒线低至少 5%；负载电压持续偏低时单独提醒。</p>`)}`;
  }
  function updatesSettings() {
    const stages = { idle: '尚未检查', candidate: '发现示例版本，尚未下载', downloaded: '样例包已暂存，等待验证', validated: '样例验证完成，等待安装', reconnecting: '演示服务重启，等待读回', complete: '演示读回：网页版本已恢复连接' };
    const primary={candidate:['下载更新','system-update-download'],downloaded:['验证安装条件','system-update-validate'],validated:['安装更新','system-update-install'],reconnecting:['重新连接并读取版本','system-update-readback']}[S.updates]||['检查更新','system-update-check'];
    return `${card('网页与语音软件', `${row('当前版本', esc(S.updateInstalledVersion))}${row('更新状态', stages[S.updates])}${S.updateFile ? row('当前包', esc(S.updateFile)) : ''}${S.updateError?`<div class="sys-inline-result error" role="alert">${esc(S.updateError)}</div>`:''}<div class="sys-form-actions">${button(primary[0],primary[1],'','primary')}${S.updates!=='reconnecting'?button('上传离线包','system-update-upload'):''}</div><details class="sys-details"><summary>更新详情与恢复</summary><p class="muted">仅演示版本切换与读回，保留用户数据。</p>${S.updates==='validated'?button('重新验证','system-update-validate'):''}${S.updatePreviousVersion?row('可恢复版本',esc(S.updatePreviousVersion),button('恢复上一版','system-update-rollback',S.updates==='reconnecting'?'disabled':'')):'<p class="muted">当前没有可恢复的上一版。</p>'}</details>`, badge('流程演示'))}
      ${card('主程序', `${row('当前构建', 'sync3.1')}${row('运行模式', '以主程序回读为准')}${row('A7Z 包兼容性', badge('未核验', 'warning'))}<p class="sys-caption muted">现有在线主程序更新通道未确认适配此 A7Z 构建，不能直接安装。</p><div class="sys-form-actions">${button('安装主程序包', 'system-native-update', 'disabled')}${button('恢复未完成更新', 'system-native-recover', 'disabled')}</div>`)}`;
  }
  function displaySettings() {
    return `${card('界面显示', `<form id="sys-display-form"><div class="sys-form-grid">${select('舞台背景', 'stage', [['mid','中灰'],['graphite','石墨'],['light','浅灰']], S.formDrafts.display?.stage||S.stage)}</div></form>${switchRow('底部设备状态栏', 'showStatus')}${switchRow('紧凑间距', 'compact')}${save('保存舞台背景', 'system-display-save','data-dirty-save="display" '+(S.formDirty.display?'':'disabled'))}`)}${card('关于', `${row('产品', '智能鸭 Smart Duck')}${row('中控', 'Studio 网页控制台')}${row('主控平台', 'Cubie A7Z')}`)}`;
  }
  const settingsPages = { network: networkSettings, account: accountSettings, ai: aiSettings, battery: batterySettings, updates: updatesSettings, display: displaySettings };
  P.register('settings', {
    render(sub = 'network') {
      const current = settingsPages[sub] ? sub : 'network';
      return `<div class="sys-page page-grid sys-layout">${categoryNav('settings', [['network', '网络与连接', 'wifi'], ['account', '账号与 SSH', 'shield'], ['ai', 'AI 连接', 'sparkles'], ['battery', '电池', 'battery'], ['updates', '软件更新', 'refresh'], ['display', '显示与关于', 'settings']], current)}<div class="sys-content sys-settings-stack ${S.compact ? 'sys-compact' : ''}">${settingsPages[current]()}</div></div>`;
    }, handle, afterRender,
  });
  window.addEventListener?.('hashchange',()=>{if(!/^#\/?settings\/ai(?:$|\/)/.test(location.hash))S.aiMessages=[];});
  function getModel(id) { return [...models, ...S.imported].find(model => model.id === id); }
  function download(name, content, mime = 'application/json') {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const link = document.createElement('a'); link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function confirm(title, body, label, done) { P.openModal(title, body, label, done); }
  function formValues(id) {
    const form = document.getElementById(id);
    if (!form || !form.reportValidity()) return null;
    return Object.fromEntries(new FormData(form));
  }
  function afterRender() {
    const nav = document.querySelector('.sys-subnav');
    const activeCategory = nav?.querySelector('[aria-current="page"]');
    if (activeCategory && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft += activeCategory.getBoundingClientRect().left - nav.getBoundingClientRect().left - (nav.clientWidth - activeCategory.clientWidth) / 2;
    }
    const ai = document.getElementById('sys-ai-form');
    if (ai) {
      const dirty = () => {
        const values=Object.fromEntries(new FormData(ai)),previous=S.aiConnections.find(item=>item.id===S.aiSelected);
        const initial=previous||{name:'',provider:'doubao',url:'https://ark.cn-beijing.volces.com/api/v3',model:'',noKey:false};
        S.aiDirty=['name','provider','url','model'].some(key=>(values[key]||'')!==(initial[key]||''))||!!values.noKey!==!!initial.noKey||!!values.clearKey||!!values.key;
        S.aiDraft={id:S.aiSelected,name:values.name,provider:values.provider,url:values.url,model:values.model,noKey:!!values.noKey,clearKey:!!values.clearKey,keyConfigured:!values.clearKey&&previous?.provider===values.provider&&previous?.url===values.url&&!!previous?.keyConfigured};
        S.aiResult='';if(S.aiMessages.length){S.aiMessages=[];const chat=document.getElementById('sys-ai-chat');if(chat)chat.innerHTML='<div class="sys-chat-empty"><span class="muted">连接已修改，保存后重新试聊。</span></div>';}
        const saveButton=document.querySelector('[data-action="system-ai-save"]');if(saveButton)saveButton.disabled=!S.aiDirty||!previous&&S.aiConnections.length>=10;
        document.querySelectorAll('[data-action="system-ai-current"],[data-action="system-ai-test"],[data-action="system-ai-chat"]').forEach(el => { el.disabled = !aiReady()||el.dataset.action==='system-ai-current'&&S.aiCurrent===S.aiSelected; });
        document.querySelector('.sys-ai-dirty').textContent = S.aiDirty?'有未保存的修改，请先保存。':'连接配置没有变化。';
      };
      ai.addEventListener('input', dirty); ai.addEventListener('change', dirty);
      ai.elements.provider.addEventListener('change', () => { ai.elements.url.value = ai.elements.provider.value === 'doubao' ? 'https://ark.cn-beijing.volces.com/api/v3' : ''; ai.elements.key.value = ''; });
    }
    const chat=document.getElementById('sys-ai-chat');if(chat)chat.scrollTop=chat.scrollHeight;
    const battery = document.getElementById('sys-battery-form');
    battery?.elements.preset.addEventListener('change', () => { ['warn','red'].forEach(key => { battery.elements[key].disabled = battery.elements.preset.value === 'bench'; }); });
    document.querySelectorAll('[data-binding]').forEach(el=>el.addEventListener('change',()=>{S.bindingDraft=Object.fromEntries([...document.querySelectorAll('[data-binding]')].map(input=>[input.dataset.binding,input.value]));const changed=Object.keys(S.bindingDraft).some(key=>S.bindingDraft[key] !== (S.bindings[key]??defaultBindings[key]));const saveButton=document.querySelector('[data-dirty-save="bindings"]');if(saveButton)saveButton.disabled=!changed||!!modelEditReason();}));
    for(const key of ['profile','battery','display']) {
      const form=document.getElementById(`sys-${key}-form`);if(!form)continue;
      const note=()=>{if(!form.querySelector('.sys-unsaved')){const el=document.createElement('p');el.className='sys-unsaved';el.textContent='有未保存的修改';form.appendChild(el);}};
      if(S.formDirty[key])note();
      const mark=(event)=>{
        if(key==='battery'&&event.target.name==='meter')return;
        const values=Object.fromEntries(new FormData(form));delete values.meter;
        const next=key==='battery'?{...S.battery,...values,beep:!!values.beep}:values,saved=key==='display'?{stage:S.stage}:S[key];
        const dirty=Object.keys(saved).some(name=>typeof saved[name]==='number'?Number(next[name])!==saved[name]:next[name]!==saved[name]);
        S.formDrafts[key]=dirty?next:null;S.formDirty[key]=dirty;
        const saveButton=document.querySelector(`[data-dirty-save="${key}"]`);if(saveButton)saveButton.disabled=!dirty;
        if(key==='profile'){const exportButton=document.querySelector('[data-action="system-profile-export"]');if(exportButton)exportButton.disabled=dirty;}
        if(dirty)note();else form.querySelector('.sys-unsaved')?.remove();
      };
      form.addEventListener('input',mark);form.addEventListener('change',mark);
    }
  }
  function openWifiDialog() {
    confirm('选择无线网络', `<label class="field"><span>网络</span><select id="sys-wifi-select"><option>Studio WiFi</option><option>SmartDuck Lab</option><option value="Guest WiFi">Guest WiFi · 开放网络</option><option value="__manual">其他网络…</option></select></label><label class="field" id="sys-wifi-manual" hidden><span>WiFi 名称</span><input id="sys-wifi-ssid" maxlength="32"></label><label class="field"><span>WiFi 密码</span><input id="sys-wifi-password" type="password" placeholder="已保存的加密网络也需填写密码" autocomplete="new-password"></label><label class="sys-check"><input id="sys-wifi-show" type="checkbox"> 显示密码</label><div class="sys-actions">${button('重新扫描','system-wifi-rescan')}<span class="muted" id="sys-wifi-scan-status"></span></div><p class="sys-caption muted">连接会切换网络，再通过设备地址找回中控。</p>`, '连接', () => {
      const selected=document.getElementById('sys-wifi-select').value,manual=selected==='__manual',ssid=manual?document.getElementById('sys-wifi-ssid').value.trim():selected,psk=selected==='Guest WiFi'?'':document.getElementById('sys-wifi-password').value;
      if(!ssid){P.toast('请填写 WiFi 名称');return false;}
      if(!manual&&selected!=='Guest WiFi'&&!psk){P.toast('加密网络需要填写密码');return false;}
      if(psk&&!((psk.length>=8&&psk.length<=63)||/^[0-9a-fA-F]{64}$/.test(psk))){P.toast('WiFi 密码需 8–63 字符或 64 位十六进制');return false;}
      S.pendingSsid=ssid;S.networkPhase='switching';S.networkError='';P.render();
    });
    document.getElementById('sys-wifi-select').addEventListener('change',event=>{const manual=event.target.value==='__manual';document.getElementById('sys-wifi-manual').hidden=!manual;document.getElementById('sys-wifi-password').disabled=event.target.value==='Guest WiFi';});
    document.getElementById('sys-wifi-show').addEventListener('change',event=>{document.getElementById('sys-wifi-password').type=event.target.checked?'text':'password';});
  }
  async function publicKeyMetadata(line) {
    if(line.length>16384||/[\r\n]/.test(line)||line.includes('PRIVATE KEY'))throw new Error('只粘贴 .pub 文件中的一整行公钥');
    const [,type,encoded,comment='']=line.trim().match(/^(\S+)\s+(\S+)(?:\s+([\s\S]*))?$/)||[];
    const types=['ssh-ed25519','ssh-rsa','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521','sk-ssh-ed25519@openssh.com','sk-ecdsa-sha2-nistp256@openssh.com'];
    if(!types.includes(type)||!encoded||!/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)||encoded.length%4)throw new Error('公钥类型或 base64 无效，前面不能带选项');
    const bytes=Uint8Array.from(atob(encoded),ch=>ch.charCodeAt(0));
    if(bytes.length<4)throw new Error('公钥内容不完整');
    const size=new DataView(bytes.buffer).getUint32(0,false);
    if(size>bytes.length-4||new TextDecoder().decode(bytes.slice(4,4+size))!==type)throw new Error('公钥内容与类型不匹配');
    if(!globalThis.crypto?.subtle)throw new Error('当前浏览器无法计算公钥指纹');
    const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    return {type,comment:comment.replace(/[\x00-\x1f\x7f]/g,'').slice(0,200),fp:'SHA256:'+btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/=+$/,'')};
  }
  async function copy(text, message) {
    try { await navigator.clipboard.writeText(text); P.toast(message); }
    catch { confirm('复制内容', `<textarea readonly rows="5" class="sys-copy-area">${esc(text)}</textarea>`, '完成', () => {}); }
  }
  function passwordDialog(ssh = false) {
    const min = ssh ? 8 : 6;
    if (ssh && !S.passwordSet) { P.toast('请先设置网页登录密码'); return; }
    confirm(ssh ? '设置 SSH 密码' : '设置登录密码', `<label class="field"><span>新密码</span><input id="sys-new-password" type="password" minlength="${min}" maxlength="64" placeholder="至少 ${min} 个字符" autocomplete="new-password"></label><label class="field"><span>再输入一次</span><input id="sys-confirm-password" type="password" autocomplete="new-password"></label>`, '保存密码', () => {
      const value = document.getElementById('sys-new-password')?.value || '';
      if (value.length < min || (ssh && value.toLowerCase() === 'microduck')) { P.toast(`请填写至少 ${min} 个字符的独立密码`); return false; }
      if (value !== document.getElementById('sys-confirm-password')?.value) { P.toast('两次密码不一致'); return false; }
      if (ssh) S.ssh = true; else S.passwordSet = true;
      P.toast(ssh ? 'SSH 密码设置已演示' : '登录密码设置已演示'); P.render();
    });
  }
  async function handle(action, target) {
    const value = target?.dataset?.value;
    if (action === 'system-toggle') {
      const key = target.dataset.key;
      if (key === 'ssh' && !S.passwordSet) { P.toast('请先设置网页登录密码'); return; }
      if (key === 'ssh' && !S.ssh) { passwordDialog(true); return; }
      if(!['wifiOn','hotspot','ssh','showStatus','compact'].includes(key))return;
      S[key] = !S[key];if(key==='hotspot')P.state.hotspotMode=S.hotspot;if(key==='wifiOn'&&!S.wifiOn){S.networkPhase='idle';S.pendingSsid='';S.networkError='';}P.render();return;
    }
    if (action === 'system-model-filter') { S.modelFilter = value; P.render(); return; }
    if (action === 'system-model-refresh') {
      if(P.state.offline||P.state.stale||P.state.owned||P.state.boot!=='ready'){S.modelError='模型状态未能读回，保留上次状态；连接恢复后可刷新。';P.render();return;}
      S.modelError='';if(!modelEditReason())S.loaded={...S.slots};P.render();P.toast('模型选用与加载状态已刷新（演示）');return;
    }
    if (action === 'system-model-restore') {
      const slot = target.dataset.slot; if (!Object.hasOwn(slotLabels, slot)) return;
      if(!requireIdle())return;
      confirm(`恢复${slotLabels[slot]}`, defaultSlots[slot] ? '恢复此用途的内置模型，保留导入模型和按键配置。' : '恢复此用途的默认停用状态，保留模型库与按键配置。', '恢复默认', () => { if(!requireIdle())return false;S.slots[slot] = defaultSlots[slot]; P.render(); P.toast('用途配置已恢复；刷新状态可核对加载结果'); }); return;
    }
    if (action === 'system-model-use') {
      if(!requireIdle())return;
      const model = getModel(target.dataset.model), slot = model?.allowed?.[0];
      if (!model?.installed || !slot || !model.allowed.includes(slot)) { P.toast('模型兼容性待校验，尚不能选用'); return; }
      S.slots[slot] = model.id; if (slot === 'walk') { S.modelSlot = model.id; S.modelName = model.name; }
      P.toast(`已选用于${slotLabels[slot]}；等待主程序加载回读`); P.render(); return;
    }
    if (action === 'system-model-detail') {
      const model = getModel(target.dataset.model);if(!model)return;
      confirm(model.name, `${row('用途', model.installed ? esc(model.label) : '待部署契约确认')}${row('来源', model.version)}${row('安装状态', model.installed ? badge('示例内置模型') : badge('兼容性待校验', 'warning'))}${row('选用用途', selectedSlots(model.id).map(slot => slotLabels[slot]).join('、') || '未选用')}<details class="sys-details"><summary>包与版本信息</summary>${row('标识', esc(model.id))}${row('包文件', esc(model.file || '内置示例'))}<p class="muted">文件指纹、训练轮数和版本由部署包元数据提供；原型不检测真实兼容性。</p></details><label class="field"><span>模型名称</span><input id="sys-model-name" value="${esc(model.name)}" maxlength="80"></label><div class="sys-actions">${selectedSlots(model.id).some(slot=>!['walk','stand'].includes(slot))?button('设置动作按键','system-model-bind',`data-model="${model.id}"`):''}${model.custom ? button('删除模型', 'system-model-delete', `data-model="${model.id}"`, 'danger') : ''}</div>`, '保存名称', () => {
        const name = document.getElementById('sys-model-name')?.value.trim();
        if (!name) { P.toast('请填写模型名称'); return false; }
        model.name = name; if (S.modelSlot === model.id) S.modelName = name; P.render(); P.toast('模型名称已保存');
      }); return;
    }
    if (action === 'system-model-delete') {
      if (selectedSlots(target.dataset.model).length || Object.values(S.loaded).includes(target.dataset.model)) { P.toast('模型仍在选用或加载，请先切换此用途'); return; }
      const model=getModel(target.dataset.model);if(!model?.custom)return;
      confirm(`删除“${model.name}”`,'删除后再次使用需要重新导入模型包。','确认删除',()=>{if(selectedSlots(model.id).length||Object.values(S.loaded).includes(model.id)){P.toast('模型仍在选用或加载，请先切换此用途');return false;}S.imported=S.imported.filter(item=>item.id!==model.id);P.render();P.toast('导入记录已删除');});return;
    }
    if(action==='system-model-bind'){S.bindingPreset=selectedSlots(target.dataset.model).find(slot=>!['walk','stand'].includes(slot))||'';document.querySelector('#modal')?.close();P.navigate('models/bindings');return;}
    if (action === 'system-model-import') {
      confirm('导入模型', '<div class="sys-upload-zone"><p>选择模型部署包</p><input id="sys-model-file" type="file" accept=".zip,.tar.gz,.tgz" aria-label="模型部署包"><span class="muted">ZIP / tar.gz；包含 policy.onnx、manifest.json 与 deployment-contract.json。</span></div><label class="field"><span>模型名称</span><input id="sys-import-name" placeholder="留空使用文件名" maxlength="80"></label><p class="muted">本原型只展示导入记录，兼容性保持待校验。</p>', '添加导入记录', () => {
        const file = document.getElementById('sys-model-file')?.files?.[0];
        if (!file) { P.toast('请选择模型包'); return false; }
        if (!/\.(zip|tar\.gz|tgz)$/i.test(file.name)) { P.toast('请选择 ZIP 或 tar.gz 部署包'); return false; }
        const name = document.getElementById('sys-import-name')?.value.trim() || file.name.replace(/\.(tar\.gz|zip|tgz)$/i, '');
        S.imported.push({ id: `custom-${Date.now()}`, name, kind: 'pending', label: '用途待确认', allowed: [], installed: false, description: '部署契约与硬件兼容性尚未校验', version: '自行导入', file: file.name, custom: true });
        S.modelFilter = 'all'; P.render(); P.toast('导入记录已添加，兼容性待校验');
      }); return;
    }
    if (action === 'system-binding-save') {
      if(!requireIdle())return;
      const next = Object.fromEntries([...document.querySelectorAll('[data-binding]')].map(el => [el.dataset.binding, el.value]));
      const changes = Object.keys(next).filter(key => next[key] !== (S.bindings[key] ?? defaultBindings[key]));
      if (!changes.length) { P.toast('按键配置没有变化'); return; }
      if(Object.entries(next).some(([key,slot])=>!Object.hasOwn(defaultBindings,key)||slot&&(!Object.values(defaultBindings).includes(slot)||!S.slots[slot]))){P.toast('动作按键配置无效');return;}
      confirm('保存动作按键', changes.map(key => `<p>${buttonLabels[key]}：${slotLabels[S.bindings[key] ?? defaultBindings[key]] || '不绑定'} → ${slotLabels[next[key]] || '不绑定'}</p>`).join(''), '确认替换', () => { if(!requireIdle())return false;S.bindings = next;S.bindingDraft=null; P.render(); P.toast('五个动作键已保存，固定急停键不变'); }); return;
    }
    if (action === 'system-binding-reset') { if(!requireIdle())return;confirm('恢复默认动作键','恢复 A、X、LB、RB、十字下的默认分配，当前选用模型保持不变。','恢复默认',()=>{if(!requireIdle())return false;S.bindings={};S.bindingDraft=null;P.render();P.toast('已恢复默认按键');});return; }
    if (action === 'system-binding-capture') {
      if(!requireIdle())return;
      confirm('按键捕获演示', `<p>正式中控会等待 20 秒并监听实体手柄。此处选择一个键，预览绑定流程。</p><label class="field"><span>动作</span><select id="sys-capture-slot">${Object.keys(defaultBindings).map(key => `<option value="${defaultBindings[key]}">${slotLabels[defaultBindings[key]]}</option>`).join('')}</select></label><label class="field"><span>按下的实体键（示意）</span><select id="sys-capture-key">${Object.entries(buttonLabels).map(([key,label]) => `<option value="${key}">${label}</option>`).join('')}</select></label>`, '应用演示绑定', () => { if(!requireIdle())return false;const key=document.getElementById('sys-capture-key').value,slot=document.getElementById('sys-capture-slot').value;if(!Object.hasOwn(defaultBindings,key)||!Object.values(defaultBindings).includes(slot)||!S.slots[slot]){P.toast('请选择有选用模型的动作与可配置按键');return false;}S.bindingDraft={...defaultBindings,...S.bindings,[key]:slot};S.bindingPreset=slot;P.render();P.toast('捕获结果已填入，保存按键后生效'); }); return;
    }
    if (action === 'system-profile-save') { const values = formValues('sys-profile-form'); if (!values) return; S.profile = values;S.formDirty.profile=false;S.formDrafts.profile=null;P.render(); P.toast('设备档案已保存到本次演示'); return; }
    if (action === 'system-profile-export') {
      if(S.formDirty.profile){P.toast('请先保存档案修改，再导出');return;}const body = savedProfile();
      download('smart-duck-prototype-profile.json', JSON.stringify({ prototype: true, body: { ...body, foot: S.profile.foot || null, note: S.profile.note || null }, models: S.slots, calibration: { imu_mount: null, servo_zero_raw: null, note: '原型未读取真实标定' }, runtime: { kp: 5, kd: 0, torque_limit: 1000 } }, null, 2)); P.toast('设备档案样例已导出'); return;
    }
    if (action === 'system-log-filter') { S.logFilter = value; P.render(); return; }
    if (action === 'system-log-search') { S.logSearch = document.getElementById('sys-log-search').value.trim(); S.logCategory = document.getElementById('sys-log-category').value; P.render(); return; }
    if (action === 'system-log-clear') { S.logFilter = 'all'; S.logSearch = ''; S.logCategory = ''; P.render(); return; }
    if (action === 'system-log-pause') { S.logPaused = !S.logPaused; P.render(); return; }
    if (action === 'system-log-copy') { copy(shownLogs().map(item => item.join('  ') + (logEvidence[item[0]] ? `\n${JSON.stringify(logEvidence[item[0]])}` : '')).join('\n'), '当前日志已复制'); return; }
    if (action === 'system-diag-download') { download('smart-duck-prototype-diagnostics.json', JSON.stringify({ prototype: true, device: 'Cubie A7Z', mode: 'demo', logs }, null, 2)); P.toast('诊断样例已下载'); return; }
    if (action === 'system-wifi-scan') {
      openWifiDialog(); return;
    }
    if(action==='system-wifi-rescan'){document.getElementById('sys-wifi-scan-status').textContent='网络列表示例已刷新';return;}
    if(action==='system-network-find'){if(S.networkPhase!=='switching'||!S.pendingSsid)return;if(P.state.offline||P.state.stale){S.networkError='尚未找到设备。切回相同网络后重试，或回到设备热点。';}else{S.wifi=S.pendingSsid;S.pendingSsid='';S.networkPhase='idle';S.networkError='';S.hotspot=false;P.state.hotspotMode=false;if(!S.savedNetworks.includes(S.wifi))S.savedNetworks.push(S.wifi);}P.render();return;}
    if (action === 'system-wifi-forget') { const name = target.dataset.network || S.wifi; confirm('忘记网络', `移除 ${esc(name)} 的连接记录？${name === S.wifi ? '正在使用此网络；正式操作会断线，没有其他网络时回到热点。' : ''}`, '忘记网络', () => { S.savedNetworks = S.savedNetworks.filter(item => item !== name); if (S.wifi === name) S.wifi = '未连接'; P.render(); P.toast('网络记录已移除'); }); return; }
    if (action === 'system-copy-address') { copy(target.dataset.address, '设备地址已复制'); return; }
    if (action === 'system-network-recovery') { confirm('换网恢复路径', '<p>① 将手机或电脑切到设备的新 WiFi。</p><p>② 尝试局域网 IP 或主机名。</p><p>③ 找不到设备时，重新连接设备热点并检查上次连接结果。</p>', '演示回到热点', () => { S.hotspot = true;P.state.hotspotMode=true; S.wifi = '未连接';S.networkPhase='idle';S.networkError='';S.pendingSsid=''; P.render(); P.toast('已切换至热点恢复示意'); }); return; }
    if (action === 'system-password') { passwordDialog(); return; }
    if (action === 'system-ssh-password') { passwordDialog(true); return; }
    if (action === 'system-password-clear') { confirm('清除登录密码', '清除后，AI 与 SSH 配置需先重新设置网页登录密码。已有 SSH 登录方式不会因此被关闭。', '清除密码', () => { S.passwordSet = false;S.aiDraft=null;S.aiDirty=false; P.render(); P.toast('登录密码已清除'); }); return; }
    if(action==='system-ssh-off'){if(!S.passwordSet){P.toast('先设置网页登录密码');return;}confirm('关闭 SSH 密码登录','关闭后仍可使用已登记公钥登录。','关闭密码登录',()=>{if(!S.passwordSet)return false;S.ssh=false;P.render();P.toast('密码登录已关闭（演示）');});return;}
    if (action === 'system-copy-ssh') { copy('ssh radxa@192.0.2.114', '登录命令已复制'); return; }
    if (action === 'system-key-add') {
      if (!S.passwordSet) { P.toast('请先设置网页登录密码'); return; }
      const input=document.getElementById('sys-ssh-key');
      try{const metadata=await publicKeyMetadata(input.value.trim());if(input.isConnected===false||!S.passwordSet)return;if(S.keys.some(key=>key.fp===metadata.fp)){P.toast('这把公钥已登记');return;}S.keys.push(metadata);input.value='';P.render();P.toast('公钥指纹已登记到本次演示');}catch(error){P.toast(error.message);}return;
    }
    if (action === 'system-key-remove') { if(!S.passwordSet){P.toast('先设置网页登录密码');return;}const key=S.keys[Number(target.dataset.index)];if(!key)return;confirm('移除 SSH 公钥',`${row('公钥',esc(key.comment||'无注释'))}${row('指纹',esc(key.fp))}`,'确认移除',()=>{S.keys=S.keys.filter(item=>item.fp!==key.fp);P.render();P.toast('公钥已移除');});return; }
    if (action === 'system-logout') { confirm('退出登录', '结束当前浏览器的设备管理会话。', '退出', () => P.toast('退出登录流程已演示')); return; }
    if (action === 'system-ai-new') { S.aiSelected = '';S.aiDraft=null;S.aiResult='';S.aiMessages=[]; S.aiDirty = false; P.render(); return; }
    if (action === 'system-ai-select') { S.aiSelected = target.dataset.id;S.aiDraft=null;S.aiResult='';S.aiMessages=[]; S.aiDirty = false; P.render(); return; }
    if (action === 'system-ai-current') { if (!aiReady()) { P.toast('请补齐并保存连接'); return; } S.aiCurrent = S.aiSelected;S.aiMessages=[]; P.render(); P.toast('已设为当前连接'); return; }
    if (action === 'system-ai-save') {
      if(!S.passwordSet){P.toast('先设置网页登录密码');return;}
      const values = formValues('sys-ai-form'); if (!values) return;
      if(!S.aiSelected&&S.aiConnections.length>=10){P.toast('最多保存 10 个连接');return;}
      // Keep only non-secret display fields in the in-memory prototype state.
      const previous = S.aiConnections.find(item => item.id === S.aiSelected);
      const keepKey = previous?.provider === values.provider && previous?.url === values.url && previous?.keyConfigured;
      const connection = { id: S.aiSelected || `ai-${Date.now()}`, name: values.name.trim(), provider: values.provider, url: values.url.trim(), model: values.model.trim(), noKey: !!values.noKey, keyConfigured: !values.noKey && !values.clearKey && (!!values.key || !!keepKey) };
      S.aiConnections = S.aiConnections.filter(item => item.id !== connection.id).concat(connection);
      S.aiSelected = connection.id; S.aiSaved = true; S.aiDirty = false;S.aiDraft=null;S.aiResult='';S.aiMessages=[]; P.render(); P.toast(aiMissing(connection).length ? '连接草稿已保存' : '连接资料已保存到本次演示'); return;
    }
    if (action === 'system-ai-test') { if(!aiReady()){P.toast('请补齐并保存连接');return;}S.aiResult=P.state.offline?'连接状态不可用，请恢复连接后重试。':'文字请求与返回流程已演示；未请求真实提供商。';P.render();return; }
    if (action === 'system-ai-delete') { const id=S.aiSelected;if(!S.passwordSet||!S.aiConnections.some(item=>item.id===id))return;confirm('删除 AI 连接','删除此连接配置，并清空当前页的试聊。','删除连接',()=>{S.aiConnections=S.aiConnections.filter(item=>item.id!==id);if(S.aiCurrent===id)S.aiCurrent='';S.aiSelected='';S.aiSaved=!!S.aiConnections.length;S.aiDirty=false;S.aiDraft=null;S.aiResult='';S.aiMessages=[];P.render();});return; }
    if (action === 'system-ai-chat-clear') { S.aiMessages=[];P.render();return; }
    if (action === 'system-ai-chat') {
      if(!aiReady()){P.toast('请补齐并保存连接');return;}
      const input = document.getElementById('sys-ai-message'), message = input.value.trim();
      if (!message) return;
      if(message.length>2000){P.toast('每条试聊最多 2000 字');return;}
      if(P.state.offline){P.toast('连接状态不可用，恢复后再试聊');return;}
      const history=[...S.aiMessages.slice(-8),{role:'user',text:message}];while(history.length>1&&history.reduce((sum,item)=>sum+item.text.length,0)>12000)history.splice(0,2);
      S.aiMessages=[...history,{role:'reply',text:'这是文字试聊的示例回复。正式连接后，这里显示模型回答。'}].slice(-10);P.render();return;
    }
    if (action === 'system-battery-save') {
      const values = formValues('sys-battery-form'); if (!values) return;
      const next = { preset: values.preset, warn: Number(values.warn ?? S.formDrafts.battery?.warn ?? S.battery.warn), red: Number(values.red ?? S.formDrafts.battery?.red ?? S.battery.red), floor: Number(values.floor), offset: Number(values.offset), beep: !!values.beep };
      if (next.red > next.warn - 5) { P.toast('红线需比提醒线低至少 5%'); return; }
      S.battery = next;S.formDirty.battery=false;S.formDrafts.battery=null; P.render(); P.toast('电池设置已保存到本次演示'); return;
    }
    if (action === 'system-battery-default') { const defaults={preset:'2s_liion',warn:30,red:15,floor:6.9,offset:0,beep:true};S.formDirty.battery=Object.keys(defaults).some(key=>defaults[key]!==S.battery[key]);S.formDrafts.battery=S.formDirty.battery?defaults:null;S.meterOffset=null;P.render();P.toast(S.formDirty.battery?'默认值已填入，保存后生效':'当前已是默认设置');return; }
    if (action === 'system-battery-measure') {
      if (P.torqueStatus() !== '已卸力' || P.state.offline || P.state.stale) { P.toast('请确认设备卸力并有新鲜电压回读后再校准'); return; }
      const form = document.getElementById('sys-battery-form'), measured = Number(form.elements.meter.value);
      if (!form.elements.meter.value || !form.elements.meter.reportValidity()) { P.toast('请填写 6–8.4 V 的万用表实测值'); return; }
      const offset = Math.round((measured - voltage()) * 100) / 100;
      if (Math.abs(offset) > 0.5) { P.toast('偏移超出 ±0.5 V，请核对测量与设备读数'); return; }
      S.meterOffset = offset;
      confirm('电压读数校准', `${row('示例舵机读数', `${voltage().toFixed(2)} V`)}${row('万用表实测', `${measured.toFixed(2)} V`)}${row('计算偏移', `${offset >= 0 ? '+' : ''}${offset.toFixed(2)} V`)}<p class="muted">采用后仍需点击“保存设置”。</p>`, '采用此偏移', () => { form.elements.offset.value = offset.toFixed(2);S.formDirty.battery=true;const values=Object.fromEntries(new FormData(form));S.formDrafts.battery={...S.battery,...values,beep:!!values.beep,offset};const saveButton=document.querySelector('[data-dirty-save="battery"]');if(saveButton)saveButton.disabled=false; P.toast('偏移已填入，等待保存'); }); return;
    }
    if (action === 'system-update-check') {
      if(S.updates==='reconnecting')return;
      if(P.state.offline){updateFailure('网络检查失败，尚未确认是否有更新。恢复连接后可重试。');return;}
      S.updates='candidate';S.updateFile='';S.updateError='';P.render();return;
    }
    if (action === 'system-update-download') {
      if(S.updates!=='candidate')return;
      if(P.state.offline){updateFailure('下载连接中断，发布版本保留；恢复连接后重试。');return;}
      S.updates='downloaded';S.updateFile='studio-example-update.tar';S.updateError='';P.render();return;
    }
    if (action === 'system-update-validate') {
      if(!['downloaded','validated'].includes(S.updates))return;
      const reason=updateBlockReason();if(reason){S.updates='downloaded';updateFailure(reason+'；包记录已保留。');return;}
      S.updates='validated';S.updateError='';P.render();P.toast('安装条件检查已演示，不代表真实签名验证');return;
    }
    if (action === 'system-update-readback') {
      if(S.updates!=='reconnecting')return;
      if(P.state.offline||P.state.stale){updateFailure('服务尚未重新连接，版本结果未读回；请恢复连接后重试。');return;}
      if(S.updateMode==='rollback'){S.updateInstalledVersion=S.updatePreviousVersion;S.updatePreviousVersion=null;}
      else{S.updatePreviousVersion=S.updateInstalledVersion;S.updateInstalledVersion='示例网页 / 语音更新版';}
      S.updates='complete';S.updateError='';P.render();P.toast('演示版本结果已读回');return;
    }
    if (action === 'system-update-upload') {
      if(S.updates==='reconnecting')return;
      confirm('上传离线更新包', '<label class="field"><span>软件更新包 · 1 字节至 160 MiB</span><input id="sys-update-file" type="file" accept=".tar"></label>', '暂存包记录', () => {
        const file=document.getElementById('sys-update-file').files?.[0];
        if(!file){P.toast('请选择更新包');return false;}
        if(!/\.tar$/i.test(file.name)){P.toast('请选择 .tar 软件更新包');return false;}
        if(!file.size||file.size>160*1024*1024){P.toast('请选择非空且不超过 160 MiB 的软件更新包');return false;}
        if(S.updates==='reconnecting')return false;
        S.updates='downloaded';S.updateFile=file.name;S.updateError='';P.render();P.toast('更新包记录已保留，等待验证演示');
      });return;
    }
    if (['system-native-update','system-native-recover'].includes(action)) { P.toast('当前 A7Z 主程序更新适配未核验，不能安装'); return; }
    if (['system-update-install','system-update-rollback'].includes(action)) {
      const rollback=action==='system-update-rollback';
      if(S.updates==='reconnecting'||rollback&&!S.updatePreviousVersion)return;
      if(!rollback&&S.updates!=='validated'){P.toast('请先完成安装条件验证');return;}
      const reason=updateBlockReason();if(reason){if(!rollback)S.updates='downloaded';updateFailure(reason);return;}
      confirm(rollback?'恢复上一版':'安装软件更新', '<p>承托设备并保持卸力，结束遥控、监听、录制和标定。</p><label class="sys-check"><input id="sys-update-confirm" type="checkbox"> 我已完成准备</label>', '确认继续', () => {
        if(!document.getElementById('sys-update-confirm').checked){P.toast('请先确认准备状态');return false;}
        const blocked=updateBlockReason();if(blocked){S.updateError=blocked;P.toast(blocked);return false;}
        P.stopAllOutputs?.();S.updateMode=rollback?'rollback':'install';S.updates='reconnecting';S.updateError='';P.render();P.toast('演示服务重启中，等待读取版本结果');
      });return;
    }
    if (action === 'system-display-save') {
      const values=formValues('sys-display-form');if(!values||!['mid','graphite','light'].includes(values.stage))return;
      S.stage=values.stage;S.formDirty.display=false;S.formDrafts.display=null;document.documentElement.dataset.duckStage=S.stage;P.render();P.toast('舞台背景已保存到本次演示');return;
    }
    if (action === 'system-save') P.toast('设置已保存');
  }
})();
