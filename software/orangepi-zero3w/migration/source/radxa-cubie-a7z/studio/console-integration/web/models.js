// 模型库只在服务端确认成功后更新；安装、选用和正在执行是三个不同状态。
import { html, useState, useEffect, useLayoutEffect, useRef } from './vendor/preact-htm.module.js';
import { ConfirmBox } from './confirm.js';

if (typeof document !== 'undefined' && !document.getElementById('models-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'models-css';
  sheet.rel = 'stylesheet';
  sheet.href = new URL('./models.css', import.meta.url).href;
  document.head.appendChild(sheet);
}

const BUTTONS = [
  { button: 'a', label: 'A' }, { button: 'x', label: 'X' },
  { button: 'lb', label: 'LB' }, { button: 'rb', label: 'RB' },
  { button: 'dpad_down', label: '十字下' },
];
const SKILL_SLOTS = ['sitstand', 'ground_pick', 'kick_left', 'kick_right', 'roulade'];
const CAPTURE_ROLES = { south: 'a', west: 'x', lb: 'lb', rb: 'rb', dd: 'dpad_down' };
const taskLabel = (item) => item?.task_label || ({ walk: '走路 / 保持', sitstand: '坐下 / 站起',
  ground_pick: '低头捡东西', kick_left: '左脚踢', kick_right: '右脚踢', roulade: '前滚翻',
  stand: '独立站立' }[item?.suggested_slot || item?.task]) || item?.task || '用途待确认';
const keyLabel = (button) => BUTTONS.find(b => b.button === button)?.label || button;
const itemName = (view, id) => view?.items?.find(item => item.id === id)?.name || '';
const slotName = (view, slot) => view?.slots?.find(s => s.slot === slot)?.label || slot;
const actionLabel = (view, slot) => {
  const s = view?.slots?.find(s => s.slot === slot);
  const name = itemName(view, s?.model_id);
  return name ? `${s.label} · ${name}` : s?.label || slot || '未绑定';
};

export function modelState(item) {
  const states = item.installed ? ['已安装'] : ['文件不可用'];
  if (item.slots?.length) states.push('当前选用');
  if (item.running === true) states.push('正在执行');
  return states;
}

export function modelSlots(view, item) {
  // 只提供服务端确认兼容的用途，不把所有用途暴露给任意模型。
  const allowed = item.allowed_slots || (item.suggested_slot ? [item.suggested_slot] : []);
  return (view?.slots || []).filter(slot => allowed.includes(slot.slot));
}

export function bindingChoice(view, button, slot) {
  if (!BUTTONS.some(b => b.button === button) || !SKILL_SLOTS.includes(slot)) {
    return { valid: false, reason: '这个用途不能分配给动作键；走路请使用原来的站、走控制。' };
  }
  const target = view?.slots?.find(s => s.slot === slot);
  if (!target?.model_id || target.error) return { valid: false, reason: '请先为这个动作选择可用模型。' };
  const current = view?.bindings?.find(b => b.button === button);
  const occupied = Boolean(current?.slot || current?.skill);
  const already = current?.slot === slot;
  const currentLabel = current?.slot ? actionLabel(view, current.slot) : current?.skill || '未绑定';
  return { valid: true, already, conflict: occupied && !already,
    currentLabel, targetLabel: actionLabel(view, slot) };
}

export function operationAvailability(view) {
  const c = view?.capabilities || {};
  return { import: c.import === true, select: c.select === true,
    rename: c.rename === true, delete: c.delete === true,
    reason: c.reason || view?.error || '还没有读到模型管理状态，请刷新重试。' };
}

export function captureKey(previous, live) {
  if (!previous || live?.state !== 'attached' || (live.last_ms != null && live.last_ms > 1500)) return null;
  const pressed = Object.keys(CAPTURE_ROLES).filter(role => live.buttons?.[role]);
  if (pressed.length !== 1 || previous[pressed[0]]) return null;
  return CAPTURE_ROLES[pressed[0]];
}

export function createModelReader(api) {
  let revision = 0;
  return {
    invalidate() { revision += 1; },
    async read() {
      const started = revision;
      try {
        const response = await api('/api/models');
        return started === revision ? response : null;
      } catch (error) {
        if (started === revision) throw error;
        return null;
      }
    },
  };
}

export function captureAllowed(view) {
  const rows = view?.bindings || [];
  const safeSkills = new Set(['', 'ground_pick', 'sit_toggle', 'kick_left', 'kick_right', 'roulade']);
  // 旁路监听不拦截 padd 的原动作；stand_test 可在卸力时直接上力起身。
  return rows.length === BUTTONS.length && BUTTONS.every(({ button }) => {
    const matches = rows.filter(row => row.button === button);
    return matches.length === 1 && !matches[0].error && safeSkills.has(matches[0].skill);
  });
}

function ModelItem({ item, view, cap, busy, onOp, onConfirm, onBind }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(item.name || '');
  const allowed = modelSlots(view, item);
  const [chosen, setChosen] = useState(allowed[0]?.slot || '');
  useEffect(() => { if (!renaming) setName(item.name || ''); }, [item.name, renaming]);
  const selectedSlot = allowed.find(s => s.slot === chosen) || allowed[0];
  const selected = (item.slots || []).map(slot => slotName(view, slot));
  const bound = (item.buttons || []).map(b => keyLabel(typeof b === 'string' ? b : b.button));
  const bindable = (item.slots || []).filter(slot => SKILL_SLOTS.includes(slot));
  const current = selectedSlot?.model_id === item.id;
  const removeReason = item.reason || (item.slots?.length ? '当前仍在使用，请先切换模型或恢复内置，再删除。' : '现在不能删除。');
  return html`<article class="models-item" data-model-id=${item.id} data-model-name=${item.name} aria-label=${`模型 ${item.name}`}>
    <div class="models-item-head">
      <div class="models-item-title"><h3 data-model-title>${item.name}</h3><span class="models-source">${item.source === 'builtin' ? '内置默认' : '自行导入'}</span></div>
      <div class="models-tags">${modelState(item).map(state => html`<span class=${`models-tag ${state === '正在执行' ? 'running' : state === '当前选用' ? 'selected' : ''}`}>${state}</span>`)}</div>
    </div>
    <p class="models-purpose">用途：${taskLabel(item)}</p>
    ${selected.length ? html`<p class="models-note">当前用于：${selected.join('、')}${bound.length ? ` · 手柄键：${bound.join('、')}` : ''}</p>` : null}
    ${renaming ? html`<form class="models-rename" onSubmit=${async e => {
      e.preventDefault();
      if (await onOp({ op: 'rename', id: item.id, name: name.trim() }, '名称已保存')) setRenaming(false);
    }}>
      <label>显示名称<input type="text" value=${name} maxLength="80" aria-label=${`模型名称 ${item.name}`} onInput=${e => setName(e.target.value)} disabled=${busy} /></label>
      <button class="models-btn primary" type="submit" disabled=${busy || !cap.rename || !name.trim()}>保存名称</button>
      <button class="models-btn" type="button" disabled=${busy} onClick=${() => setRenaming(false)}>取消改名</button>
    </form>` : null}
    <div class="models-item-actions">
      ${allowed.length > 1 ? html`<label class="models-slot-choice">用在<select value=${selectedSlot?.slot} aria-label=${`用途 ${item.name}`} onChange=${e => setChosen(e.target.value)} disabled=${busy || !cap.select}>
        ${allowed.map(s => html`<option value=${s.slot}>${s.label}</option>`)}
      </select></label>` : null}
      <button class=${`models-btn ${current ? '' : 'primary'}`} type="button" data-model-action="select" aria-label=${`使用 ${item.name}`}
        disabled=${busy || !cap.select || !item.installed || !selectedSlot || current}
        title=${!cap.select ? cap.reason : current ? '这个用途当前已经选用了此模型' : !selectedSlot ? '没有可用的兼容用途' : `用作${selectedSlot.label}`}
        onClick=${() => onOp({ op: 'select', id: item.id, slot: selectedSlot.slot }, `已选用「${item.name}」；不会自动开始动作`)}>${current ? '已选用' : '使用此模型'}</button>
      <button class="models-btn" type="button" aria-label=${`重命名 ${item.name}`} disabled=${busy || !cap.rename} onClick=${() => setRenaming(!renaming)}>改名</button>
      ${bindable.length ? html`<button class="models-btn" type="button" data-model-action="bind" aria-label=${`设置按键 ${item.name}`} disabled=${busy || !cap.select}
        onClick=${() => onBind(bindable[0])}>设置按键</button>` : null}
      ${item.source !== 'builtin' ? html`<button class="models-btn danger" type="button" data-model-action="delete" aria-label=${`删除 ${item.name}`}
        disabled=${busy || !cap.delete || !item.can_delete} title=${!item.can_delete ? removeReason : !cap.delete ? cap.reason : undefined}
        onClick=${() => onConfirm({ title: `删除「${item.name}」？`, lines: ['删除后如需再次使用，需要重新导入模型包。'],
          label: '确认删除', args: { op: 'delete', id: item.id }, success: '模型已删除' })}>删除</button>` : null}
    </div>
    ${item.source !== 'builtin' && !item.can_delete ? html`<p class="models-note">不能删除：${removeReason}</p>` : null}
    ${!item.installed && item.reason ? html`<p class="models-error">${item.reason}</p>` : null}
    <details class="models-details"><summary>模型详情</summary><dl>
      <dt>用途</dt><dd>${taskLabel(item)}</dd>
      <dt>文件指纹</dt><dd>${item.sha12 || item.sha256 || '未提供'}</dd>
      ${item.path ? html`<dt>文件位置</dt><dd>${item.path}</dd>` : null}
      ${item.rounds != null ? html`<dt>训练轮数</dt><dd>${item.rounds}</dd>` : null}
      ${item.commit ? html`<dt>训练版本</dt><dd>${item.commit}</dd>` : null}
      <dt>标识</dt><dd>${item.id}</dd>
    </dl></details>
  </article>`;
}

function BindingRow({ binding, view, busy, cap, preset, onBind, onOp }) {
  const candidates = (view.slots || []).filter(s => SKILL_SLOTS.includes(s.slot) && s.model_id && !s.error);
  const [slot, setSlot] = useState(binding?.slot || '');
  const button = binding.button;
  useEffect(() => { setSlot(binding.slot || ''); }, [binding.slot]);
  useEffect(() => { if (preset?.slot) setSlot(preset.slot); }, [preset?.tick]);
  const choice = bindingChoice(view, button, slot);
  const current = binding.slot ? actionLabel(view, binding.slot) : binding.skill || '未绑定';
  return html`<div class="models-binding" data-button=${button}>
    <div class="models-binding-current"><kbd>${keyLabel(button)}</kbd><span>${current}</span></div>
    <div class="models-binding-controls">
      <select value=${slot} aria-label=${`绑定动作 ${keyLabel(button)}`} disabled=${busy || !cap.select} onChange=${e => setSlot(e.target.value)}>
        <option value="">选择动作…</option>${candidates.map(s => html`<option value=${s.slot}>${actionLabel(view, s.slot)}</option>`)}
      </select>
      <button class="models-btn" type="button" data-model-action="bind" aria-label=${`设置按键 ${keyLabel(button)}`} disabled=${busy || !cap.select || !choice.valid || choice.already}
        onClick=${() => onBind(button, slot)}>${choice.already ? '已绑定' : '绑定'}</button>
      <button class="models-btn" type="button" aria-label=${`取消绑定 ${keyLabel(button)}`} disabled=${busy || !cap.select || !(binding.slot || binding.skill)}
        onClick=${() => onOp({ op: 'unbind', button }, `${keyLabel(button)} 已取消绑定`)}>取消绑定</button>
    </div>
  </div>`;
}

export function ModelsPage({ api, csrf, link, live, tune, section = 'all' }) {
  const [view, setView] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState('');
  const [message, setMessage] = useState(null);
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [upload, setUpload] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [preset, setPreset] = useState(null);
  const [capture, setCapture] = useState(null);
  const [captureNote, setCaptureNote] = useState('');
  const [seconds, setSeconds] = useState(20);
  const [stableBindings, setStableBindings] = useState('');
  const mounted = useRef(true);
  const working = useRef(false);
  const refreshing = useRef(false);
  const fileInput = useRef(null);
  const request = useRef(null);
  const reader = useRef(null);
  if (!reader.current) reader.current = createModelReader(api);
  const confirmArea = useRef(null);
  const bindingsArea = useRef(null);

  const load = async (quiet = false) => {
    if (refreshing.current || working.current) return;
    refreshing.current = true;
    if (!quiet) setLoading(true);
    try {
      const r = await reader.current.read();
      if (!r) return;
      if (!r.ok || !r.data) throw new Error(r.error || (r.status === 401 ? '登录已过期，请重新登录。' : '读不到模型管理状态。'));
      if (mounted.current) { setView(r.data); setLoadError(''); }
    } catch (e) {
      if (mounted.current) setLoadError(e.name === 'AbortError' ? '读取模型列表超时，请刷新重试。' : e.message || '连接失败，请刷新重试。');
    } finally {
      refreshing.current = false;
      if (mounted.current) setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    load();
    const timer = setInterval(() => { if (document.visibilityState !== 'hidden') load(true); }, 5000);
    return () => { mounted.current = false; clearInterval(timer); request.current?.abort(); };
  }, []);
  useEffect(() => {
    if (confirm) { confirmArea.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
  }, [confirm]);

  const captureFingerprint = JSON.stringify((view?.bindings || []).map(({ button, skill, error }) => [button, skill, error]));
  const safeCapture = captureAllowed(view);
  useEffect(() => {
    if (!safeCapture) { setStableBindings(''); return undefined; }
    // padd 每秒重读一次绑定，给已写入但尚未应用的旧特殊动作留出更新时间。
    const timer = setTimeout(() => setStableBindings(captureFingerprint), 2000);
    return () => clearTimeout(timer);
  }, [captureFingerprint, safeCapture]);
  const rawCap = { ...operationAvailability(view), capture: safeCapture && stableBindings === captureFingerprint };
  const cap = loadError ? { ...rawCap, import: false, select: false, rename: false, delete: false, reason: loadError } : rawCap;
  const busy = Boolean(pending) || Boolean(capture);
  const onOp = async (args, success) => {
    if (working.current) return false;
    setCapture(null);
    reader.current.invalidate();
    working.current = true;
    setPending('正在保存并确认…');
    setMessage(null);
    try {
      const r = await api('/api/models/op', args, 60000);
      if (!r.ok || !r.data) throw new Error(r.error || '操作没有成功，请刷新确认当前状态。');
      if (mounted.current) { setView(r.data); setLoadError(''); setMessage({ kind: 'ok', text: success }); }
      return true;
    } catch (e) {
      if (mounted.current) setMessage({ kind: 'bad', text: e.name === 'AbortError' ? '确认操作结果超时，请刷新查看当前状态。' : e.message || '连接失败，请刷新查看当前状态。' });
      return false;
    } finally {
      working.current = false;
      if (mounted.current) { setPending(''); setConfirm(null); }
    }
  };

  const doImport = async (e) => {
    e.preventDefault();
    if (working.current || !cap.import || !file) return;
    setMessage(null);
    if (!/\.(zip|tar\.gz|tgz)$/i.test(file.name)) {
      setMessage({ kind: 'bad', text: '请选择 ZIP 或 tar.gz 模型包。单独的 ONNX 文件还缺少部署说明，请先打包。' });
      return;
    }
    working.current = true;
    reader.current.invalidate();
    setPending('正在上传模型包…');
    setUpload(0);
    try {
      const response = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        request.current = xhr;
        const query = new URLSearchParams({ filename: file.name });
        if (name.trim()) query.set('name', name.trim());
        xhr.open('POST', `/api/models/import?${query}`);
        xhr.withCredentials = true;
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.setRequestHeader('X-CSRF', csrf || '');
        xhr.timeout = 180000;
        xhr.upload.onprogress = event => {
          if (!mounted.current) return;
          const percent = event.lengthComputable ? Math.round(event.loaded / event.total * 100) : null;
          setUpload(percent);
          setPending(percent === 100 ? '上传完成，正在校验并安装…' : '正在上传模型包…');
        };
        xhr.upload.onload = () => { if (mounted.current) { setUpload(100); setPending('上传完成，正在校验并安装…'); } };
        xhr.onload = () => {
          try { resolve({ status: xhr.status, ...JSON.parse(xhr.responseText) }); }
          catch { reject(new Error('服务器没有返回有效结果，请刷新确认是否安装。')); }
        };
        xhr.onerror = () => reject(new Error('上传连接中断，请刷新确认是否安装后再重试。'));
        xhr.ontimeout = () => reject(new Error('上传或校验超时，请刷新确认是否安装后再重试。'));
        xhr.onabort = () => reject(new Error('上传已取消。'));
        xhr.send(file);
      });
      if (!response.ok || !response.data) throw new Error(response.error || '模型未安装，请重试。');
      if (mounted.current) {
        setView(response.data); setLoadError(''); setFile(null); setName('');
        if (fileInput.current) fileInput.current.value = '';
        setMessage({ kind: 'ok', text: '模型已安装。选择「使用此模型」后才会改变当前选用，导入不会自动开始动作。' });
      }
    } catch (err) {
      if (mounted.current) setMessage({ kind: 'bad', text: err.message || '模型未安装。' });
    } finally {
      working.current = false; request.current = null;
      if (mounted.current) { setPending(''); setUpload(null); }
    }
  };

  const bind = (button, slot) => {
    const choice = bindingChoice(view, button, slot);
    if (!choice.valid) { setMessage({ kind: 'bad', text: choice.reason }); return; }
    if (choice.already) return;
    if (choice.conflict) {
      setConfirm({ title: `${keyLabel(button)} 已经绑定动作`,
        lines: [`现在：${choice.currentLabel}`, `改成：${choice.targetLabel}`, '替换后，原动作将不再由这个键触发。'],
        label: '替换绑定', args: { op: 'bind', button, slot, replace: true }, success: `${keyLabel(button)} 的绑定已更新` });
    } else onOp({ op: 'bind', button, slot }, `${keyLabel(button)} 已绑定「${choice.targetLabel}」`);
  };
  const jumpBind = slot => {
    if (!cap.select || !live || !link) {
      setMessage({ kind: 'bad', text: cap.reason || '请先连接鸭子并确认已卸力。' });
      return;
    }
    setPreset({ slot, tick: Date.now() });
    if (section === 'library') location.hash = '#/models/bindings';
    bindingsArea.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    if (!cap.capture) {
      setMessage({ kind: 'warn', text: safeCapture ? '正在确认手柄配置。可稍后重新点「设置按键」，或直接用下面的下拉框。' : '当前有特殊动作绑定或按键配置不完整，请使用下面的下拉框分配按键。' });
      bindingsArea.current?.querySelector('select')?.focus({ preventScroll: true });
      return;
    }
    setCapture({ slot, started: Date.now() });
    setCaptureNote('等待手柄输入…先松开所有按键，再按一个动作键。');
    setSeconds(20);
    bindingsArea.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };
  // 捕获提示可见时监听就必须就绪，避免首个按键落在绘制与普通 effect 之间。
  useLayoutEffect(() => {
    if (!capture || !link || section === 'library') return undefined;
    let previous = null;
    let released = false;
    let done = false;
    const cancel = text => {
      if (done) return;
      done = true;
      setCapture(null);
      if (text) setMessage({ kind: 'bad', text });
    };
    const visible = () => { if (document.visibilityState !== 'visible') cancel('已取消按键捕获：页面转到后台。'); };
    const watch = () => {
      if (!done && document.visibilityState === 'visible' && link.status === 'open') link.cmd('pad.op', { op: 'watch' }, 3000);
    };
    // 只借用快照频率与旁路订阅；不接管遥控，也不发送任何动作键或运动命令。
    link.setPadCapture(true);
    watch();
    const off = link.on(message => {
      if (done) return;
      if (message.t === '_status' && message.status !== 'open') { cancel('已取消按键捕获：和鸭子的连接断了。'); return; }
      if (message.t === 'state' && (message.estop?.state !== 'confirmed' || message.robotd_ok === false)) {
        cancel('已取消按键捕获：请先确认鸭子已卸力。'); return;
      }
      if (message.t !== 'pad') return;
      const input = message.live;
      if (input?.state !== 'attached') {
        previous = null; released = false;
        setCaptureNote('还没读到实体手柄。连接手柄后按键，或取消后用下面的下拉框。');
        return;
      }
      const buttons = input.buttons || {};
      if (!released) {
        previous = { ...buttons };
        released = !Object.values(buttons).some(Boolean);
        setCaptureNote(released ? '现在按 A、X、LB、RB 或十字下，稍停一下再松开。' : '请先松开手柄上所有按键。');
        return;
      }
      const key = captureKey(previous, input);
      previous = { ...buttons };
      if (key) {
        done = true;
        setCapture(null);
        link.setPadCapture(false);
        bind(key, capture.slot);
      }
    });
    const timer = setInterval(() => {
      const left = Math.max(0, 20 - Math.floor((Date.now() - capture.started) / 1000));
      setSeconds(left);
      if (!left) cancel('等待按键已超时。可以重新点「设置按键」，或使用下面的下拉框。');
    }, 250);
    const watchTimer = setInterval(watch, 2000);
    document.addEventListener('visibilitychange', visible);
    return () => { done = true; off(); clearInterval(timer); clearInterval(watchTimer); document.removeEventListener('visibilitychange', visible); link.setPadCapture(false); };
  }, [capture?.started, section]);
  useEffect(() => {
    if (capture && (!cap.select || !cap.capture || !live)) {
      setCapture(null);
      setMessage({ kind: 'bad', text: '已取消按键捕获：请保持连接，并确认鸭子已卸力。' });
    }
  }, [cap.select, cap.capture, live]);
  useEffect(() => { if (section === 'library') setCapture(null); else if (section === 'bindings') { setFile(null); setName(''); } }, [section]);
  const items = view?.items || [];
  const groups = [['imported', '自行导入'], ['builtin', '内置默认']];

  return html`<section class="models-page" aria-label="模型管理" aria-busy=${busy || loading} data-capture-ready=${cap.capture ? 'true' : 'false'}>
    <header class="models-heading"><div><h1>${section === 'bindings' ? '手柄按键' : '模型库'}</h1></div>
      <button class="models-btn" type="button" aria-label="刷新模型列表" disabled=${busy || loading} onClick=${() => load()}>${loading ? '正在读取…' : '刷新'}</button>
    </header>
    ${loadError || view?.error ? html`<div class="models-notice bad" role="alert">${loadError || view.error}</div>` : null}
    ${view && !cap.select && cap.reason !== (loadError || view.error) ? html`<div class="models-notice warn" role="status">${cap.reason}</div>` : null}
    ${message ? html`<div class=${`models-notice ${message.kind}`} role=${message.kind === 'bad' ? 'alert' : 'status'}>${message.text}</div>` : null}
    ${busy ? html`<div class="models-notice progress" role="status">${pending}${upload != null && upload < 100 ? ` ${upload}%` : ''}
      ${upload != null ? html`<progress max="100" value=${upload} aria-label="模型上传进度"></progress>` : null}</div>` : null}
    <div ref=${confirmArea}>${confirm ? html`<${ConfirmBox} lines=${[confirm.title, ...confirm.lines]} confirmLabel=${confirm.label} cancelLabel="取消"
      busy=${busy} onCancel=${() => setConfirm(null)} onConfirm=${() => onOp(confirm.args, confirm.success)} cls="models-confirm" />` : null}</div>

    ${section !== 'bindings' ? html`<details class="board models-panel models-import-panel"><summary>导入模型</summary>
      <p class="models-note">选择电脑上的 ZIP 或 tar.gz 模型包。安装后保留内置模型，随时可以恢复。</p>
      <form class="models-import" onSubmit=${doImport}>
        <label class="models-file-field">模型包<input ref=${fileInput} type="file" accept=".zip,.tar.gz,.tgz" aria-label="导入模型包" disabled=${busy || !cap.import}
          onChange=${e => { setFile(e.target.files[0] || null); setMessage(null); }} /></label>
        <label>显示名称（可选）<input type="text" value=${name} maxLength="80" placeholder="留空使用包内名称" aria-label="模型名称" disabled=${busy || !cap.import}
          onInput=${e => setName(e.target.value)} /></label>
        <button class="models-btn primary" type="submit" aria-label="导入模型" disabled=${busy || !cap.import || !file}>导入模型</button>
      </form>
      ${view && !cap.import ? html`<p class="models-note models-warning">暂时不能导入：${view.capabilities?.import_reason || cap.reason}</p>` : null}
      <details class="models-details"><summary>模型包需要哪些文件？</summary><p>把同一次部署导出的 policy.onnx、manifest.json、deployment-contract.json 一起打包；如果有 deployment-execution.json，也一起放入。导入时会检查用途和兼容性。</p></details>
    </details>` : null}

    ${!view && loading ? html`<div class="board models-panel models-note">正在读取模型库…</div>` : null}
    ${view && section !== 'bindings' ? groups.map(([source, label]) => {
      const group = items.filter(item => item.source === source);
      return html`<section class="board models-panel" aria-label=${label}>
        <div class="models-section-head"><h2>${label}</h2><span class="models-count">${group.length} 个</span></div>
        ${group.length ? html`<div class="models-list">${group.map(item => html`<${ModelItem} key=${item.id} item=${item} view=${view} cap=${cap} busy=${busy}
          onOp=${onOp} onConfirm=${setConfirm} onBind=${jumpBind} />`)}</div>` : html`<p class="models-note">${source === 'imported' ? '还没有自行导入的模型。可以从上方选择模型包。' : '还没有读到内置模型。'}</p>`}
      </section>`;
    }) : null}

    ${view && section !== 'bindings' ? html`<details class="board models-panel models-current-slots" aria-label="当前用途" data-model-slots><summary>当前用途与恢复默认</summary>
      <p class="models-note">「当前选用」是为这个用途准备好的模型；只有执行动作时才显示「正在执行」。切换模型不会自动上力。</p>
      <div class="models-slot-list">${(view.slots || []).map(slot => html`<div class="models-slot" data-slot=${slot.slot}>
        <div><strong>${slot.label}</strong><p class="models-note">${itemName(view, slot.model_id) || (slot.path ? '当前模型未收录在库中' : '未安装')}${slot.error ? html`<br /><span class="models-error">${slot.error}</span>` : null}</p>${(() => { const runtime = live && tune?.ready ? tune.models?.slots?.find(row => row.slot === slot.slot) : null; return html`<p class="models-note">主程序用途加载：${runtime ? runtime.loaded ? '已回读' : '未加载' : '等待有效回读'}${runtime?.active ? ' · 正在执行' : ''}</p>`; })()}</div>
        <button class="models-btn" type="button" data-model-action="restore" aria-label=${`恢复内置 ${slot.label}`} disabled=${busy || !cap.select || !slot.default_id || slot.is_default || slot.default_id === slot.model_id}
          title=${!cap.select ? cap.reason : !slot.default_id ? '未保存这个用途的默认配置' : slot.is_default ? '已经使用默认配置' : undefined}
          onClick=${() => setConfirm({ title: slot.default_disabled ? `恢复「${slot.label}」的默认停用状态？` : `恢复「${slot.label}」的内置模型？`,
            lines: [slot.default_disabled ? '该用途原来未加载模型。恢复后停用这个用途，自行导入的模型仍留在库里。' : '自行导入的模型会留在库里，手柄按键分配保持当前设置。'],
            label: slot.default_disabled ? '恢复默认停用' : '恢复内置模型', args: { op: 'restore', slot: slot.slot },
            success: `${slot.label} 已恢复${slot.default_disabled ? '默认停用状态' : '内置模型'}` })}>${slot.default_disabled ? '恢复默认停用' : '恢复内置'}</button>
      </div>`)}</div>
    </details>` : null}

    ${view && section !== 'library' ? html`<section ref=${bindingsArea} class="board models-panel models-bindings" aria-label="手柄按键" id="models-bindings">
      <div class="models-section-head"><h2>手柄按键</h2><button class="models-btn" type="button" aria-label="恢复默认按键" disabled=${busy || !cap.select}
        onClick=${() => setConfirm({ title: '恢复这五个按键的默认分配？', lines: ['A、X、LB、RB、十字下将恢复主程序的默认动作。当前选用的模型不变。'],
          label: '恢复默认按键', args: { op: 'reset_bindings' }, success: '按键已恢复默认分配' })}>恢复默认按键</button></div>
      <p class="models-note">动作键随选用模型生效；Select 卸力键固定，站立与行走仍使用遥控页。</p>
      ${capture ? html`<div class="models-capture" role="status" aria-label="等待手柄按键"><strong>为「${slotName(view, capture.slot)}」设置按键 · ${seconds} 秒</strong>
        <p>${captureNote}</p><button class="models-btn" type="button" aria-label="取消按键捕获" onClick=${() => setCapture(null)}>取消按键捕获</button></div>` : null}
      ${preset ? html`<p class="models-bind-hint" role="status">已选择「${slotName(view, preset.slot)}」，在下面选一个按键，点「绑定」。</p>` : null}
      ${BUTTONS.map(button => html`<${BindingRow} key=${button.button} binding=${{ ...button, ...(view.bindings || []).find(b => b.button === button.button) }}
        view=${view} busy=${busy} cap=${cap} preset=${preset} onBind=${bind} onOp=${onOp} />`)}
    </section>` : null}
  </section>`;
}
