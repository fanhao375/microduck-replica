// 「动作」页：鸭子会的所有动作放在一起——选一个看看，点播放让鸭子做，或者绑到手柄键上。
// 照「动作库」设计稿的 Studio 画板做：左边舞台，右边「我的动作」，下面五个手柄键。
// 数据和写操作都走模型库的接口（/api/models、/api/models/op），成功与否以主程序回读为准；
// 让鸭子做一次走遥控页同一条路（pad.op skill），能不能做由那边按鸭子现在的姿态判断。
import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';
import { ConfirmBox } from './confirm.js';
import { actionArgs, actionForm, bindingChoice, createModelReader, operationAvailability } from './models.js';

if (typeof document !== 'undefined' && !document.getElementById('actions-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'actions-css';
  sheet.rel = 'stylesheet';
  sheet.href = new URL('./actions.css', import.meta.url).href;
  document.head.appendChild(sheet);
}

export const KEYS = [
  { button: 'a', label: 'A' }, { button: 'x', label: 'X' }, { button: 'lb', label: 'LB' },
  { button: 'rb', label: 'RB' }, { button: 'dpad_down', label: '十字下' },
];
// 镜像自带的单次动作：skill 是 robot.do 认的名字，slot 是模型页里的运行槽（绑键时用）。
export const BUILTIN = [
  { skill: 'roulade', slot: 'roulade', id: 'roulade', name: '前滚翻' },
  { skill: 'kick_left', slot: 'kick_left', id: 'kick_left', name: '踢左脚' },
  { skill: 'kick_right', slot: 'kick_right', id: 'kick_right', name: '踢右脚' },
  { skill: 'ground_pick', slot: 'ground_pick', id: 'ground_pick', name: '捡东西' },
  { skill: 'sit_toggle', slot: 'sitstand', id: 'sitstand', name: '坐下 / 站起', toggle: true },
];
// 有渲染缩略图的动作（web/model/stills/）。导入的动作如果用的是这些名字，也用同一张图。
const STILLS = { roulade: 'roulade', kick_left: 'kick_left', kick_right: 'kick_right', ground_pick: 'ground_pick',
  sit_toggle: 'sitstand', sitstand: 'sitstand', walk: 'walk', crouch: 'crouch', idle_chest: 'idle_chest' };
const still = (key) => (STILLS[key] ? new URL(`./model/stills/${STILLS[key]}.webp`, import.meta.url).href : null);
const keyLabel = (b) => KEYS.find((k) => k.button === b)?.label || b;
const seconds = (v) => (typeof v === 'number' && Number.isFinite(v) ? `${Math.round(v * 10) / 10} 秒` : null);

/** 把模型库的回读整理成「我的动作」列表：出厂的 5 个 + 走路 + 导入的 + 别处手动加的。 */
export function actionList(view) {
  const binds = view?.bindings || [];
  const keysOf = (skill) => binds.filter((b) => b.skill === skill).map((b) => b.button);
  const loaded = new Set(view?.loaded_skills || []);
  const durations = view?.skill_durations || {};
  const out = [];
  for (const b of BUILTIN) {
    const slot = (view?.slots || []).find((s) => s.slot === b.slot);
    const ready = Boolean(slot?.path) && !slot?.error;
    out.push({ key: 'b:' + b.slot, kind: 'builtin', name: b.name, id: b.id, skill: b.skill, slot: b.slot,
      image: still(b.skill), keys: keysOf(b.skill), ready, state: ready ? 'loaded' : 'missing',
      duration: b.toggle ? '按一下切换' : seconds(durations[b.skill]) ? '约 ' + seconds(durations[b.skill]) : null,
      error: slot?.error || (ready ? null : '镜像里没装这个动作的模型') });
  }
  out.push({ key: 'walk', kind: 'walk', name: '走路', id: 'walk', skill: null, image: still('walk'), keys: [],
    ready: true, state: 'loaded', duration: '一直走' });
  for (const a of view?.actions || []) {
    out.push({ key: 'a:' + a.id, kind: 'imported', name: a.name, id: a.skill || a.suggest?.name || '还没装', skill: a.skill,
      action: a, image: still(a.skill || a.suggest?.name), keys: a.buttons || [], ready: a.state === 'loaded',
      state: a.state, duration: seconds(a.params?.duration ?? a.suggest?.duration), error: a.error });
  }
  for (const m of view?.manual_actions || []) {
    out.push({ key: 'm:' + m.name, kind: 'manual', name: m.name, id: m.name, skill: m.skill, action: m,
      image: still(m.skill), keys: m.buttons || [], ready: m.state === 'loaded' && loaded.has(m.skill),
      state: m.state, duration: seconds(m.params?.duration) });
  }
  return out;
}

/** 选中某个动作时，点一个键要发什么、要不要先确认。 */
export function keyChoice(view, item, button) {
  if (!item || item.kind === 'walk') return { valid: false, reason: '走路用摇杆，不绑动作键。' };
  if (!item.ready) return { valid: false, reason: item.kind === 'imported' ? '先把这个动作装到鸭子上，再绑键。' : '这个动作现在用不了。' };
  const choice = bindingChoice(view, button, item.kind === 'builtin' ? item.slot : 'skill:' + item.skill);
  if (!choice.valid) return choice;
  const lines = [];
  if (choice.conflict) lines.push(`${keyLabel(button)} 现在是：${keyName(view, button)}`, `换成：${item.name}`);
  if (button === 'x' && item.skill !== 'roulade') lines.push('X 键按住会连着发，动作会一遍接一遍地做。');
  return { ...choice, confirm: lines.length ? lines : null, args: { ...choice.args, ...(lines.length ? { replace: true } : {}) } };
}

/** 键帽上写什么：出厂的写中文名，导入的写显示名，关掉的写「没绑」。 */
export function keyName(view, button) {
  const row = (view?.bindings || []).find((b) => b.button === button);
  if (!row) return '读不到';
  if (!row.skill) return '没绑动作';
  const item = actionList(view).find((x) => x.skill === row.skill);
  return item?.name || row.action_name || row.skill;
}

function uploadAction(file, csrf, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const query = new URLSearchParams({ filename: file.name, kind: 'action' });
    xhr.open('POST', `/api/models/import?${query}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-CSRF', csrf || '');
    xhr.timeout = 180000;
    xhr.upload.onprogress = (e) => onProgress(e.lengthComputable ? Math.round(e.loaded / e.total * 100) : null);
    xhr.onload = () => { try { resolve({ status: xhr.status, ...JSON.parse(xhr.responseText) }); } catch { reject(new Error('服务器没有返回有效结果，请刷新确认是否导入。')); } };
    xhr.onerror = () => reject(new Error('上传连接中断，请刷新确认后再试。'));
    xhr.ontimeout = () => reject(new Error('上传或校验超时，请刷新确认后再试。'));
    xhr.send(file);
  });
}

function Thumb({ item, big }) {
  if (item.image) return html`<img class=${big ? 'ac-stage-img' : 'ac-thumb'} src=${item.image} alt=${big ? `${item.name}的样子` : ''} />`;
  return html`<span class=${big ? 'ac-stage-img ac-none' : 'ac-thumb ac-none'} aria-hidden="true">
    <svg viewBox="0 0 40 40"><path d="M11 24a9 8 0 0 1 18 0z" fill="currentColor" opacity=".55"/><rect x="11" y="24" width="18" height="3" rx="1.5" fill="currentColor" opacity=".8"/></svg></span>`;
}

function InstallPanel({ item, busy, cap, onOp, onConfirm, onCancel }) {
  const a = item.action;
  const [form, setForm] = useState(() => actionForm(a));
  const [touched, setTouched] = useState(false);
  const set = (k) => (e) => { setTouched(true); setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }); };
  const parsed = actionArgs(form);
  const installed = a.state !== 'none';
  return html`<form class="ac-install" onSubmit=${async (e) => {
    e.preventDefault(); setTouched(true);
    if (!parsed.ok) return;
    const ok = await onOp({ op: 'action_install', id: a.id, ...parsed.args },
      installed ? `「${item.name}」的参数改好了` : `「${item.name}」装上了。第一次做请把鸭子架起来。`);
    if (ok && onCancel) onCancel();
  }}>
    <strong>${installed ? '改参数' : '还没装到鸭子上'}</strong>
    <div class="ac-fields">
      <label>动作名（英文）<input value=${form.name} maxLength="32" placeholder="比如 crouch" aria-label="动作名" onInput=${set('name')} disabled=${busy} /></label>
      <label>做多久（秒）<input type="number" step="0.1" min="0.2" max="60" value=${form.duration} aria-label="时长" onInput=${set('duration')} disabled=${busy} /></label>
    </div>
    <details><summary>高级（一般照动作包里写的就行）</summary>
      <div class="ac-fields">
        <label>指令（3 个数）<input value=${form.command} aria-label="指令" onInput=${set('command')} disabled=${busy} /></label>
        <label>收尾指令<input value=${form.unwind} aria-label="收尾指令" onInput=${set('unwind')} disabled=${busy} /></label>
        <label>收尾多久（秒）<input type="number" step="0.1" min="0" max="10" value=${form.unwind_s} aria-label="收尾秒数" onInput=${set('unwind_s')} disabled=${busy} /></label>
        <label>幅度<input type="number" step="0.05" min="0.05" max="2" value=${form.action_scale} aria-label="幅度" onInput=${set('action_scale')} disabled=${busy} /></label>
        <label>力度比例<input type="number" step="0.05" min="0.2" max="2" value=${form.gain_ratio} aria-label="力度比例" onInput=${set('gain_ratio')} disabled=${busy} /></label>
        <label class="ac-check"><input type="checkbox" checked=${form.chain} onChange=${set('chain')} disabled=${busy} />做的时候再按一次，接着再做一遍</label>
      </div>
      <p>指令是动作进行时喂给模型的 3 个数，「蹲 / 站」这类开关式的写 1, 0, 0。收尾是做完以后用收尾指令再跑几秒，让它自己站回来。幅度要跟训练时一样，一般是 1。</p>
    </details>
    ${touched && !parsed.ok ? html`<p class="ac-bad">${parsed.reason}</p>` : null}
    <div class="ac-row">
      <button class="ac-btn blue" type="submit" disabled=${busy || !cap.select || !a.installed} title=${!cap.select ? cap.reason : undefined}>${installed ? '保存参数' : '装上'}</button>
      ${onCancel ? html`<button class="ac-btn" type="button" disabled=${busy} onClick=${onCancel}>取消</button>` : null}
      ${!installed ? html`<button class="ac-btn danger" type="button" disabled=${busy || !cap.delete || !a.can_delete} title=${a.can_delete ? undefined : a.reason}
        onClick=${() => onConfirm({ lines: [`删除「${item.name}」？`, '删除后要再用，得重新导入动作包。'], label: '删除',
          args: { op: 'delete', id: a.id }, success: '动作已删除', after: 'reset' })}>删除</button>` : null}
    </div>
  </form>`;
}

export function ActionsPage({ api, csrf, link, plazaUrl }) {
  const [view, setView] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [sel, setSel] = useState('b:roulade');
  const [listen, setListen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState('');
  const [message, setMessage] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [doing, setDoing] = useState(false);
  const [swap, setSwap] = useState(0);
  const mounted = useRef(true);
  const working = useRef(false);
  const fileInput = useRef(null);
  const confirmArea = useRef(null);
  const reader = useRef(null);
  if (!reader.current) reader.current = createModelReader(api);

  const load = async () => {
    if (working.current) return;
    try {
      const r = await reader.current.read();
      if (!r) return;
      if (!r.ok || !r.data) throw new Error(r.error || '读不到动作列表。');
      if (mounted.current) { setView(r.data); setLoadError(''); }
    } catch (e) {
      if (mounted.current) setLoadError(e.message || '连接失败，请刷新重试。');
    }
  };
  useEffect(() => {
    mounted.current = true; load();
    const timer = setInterval(() => { if (document.visibilityState !== 'hidden') load(); }, 5000);
    return () => { mounted.current = false; clearInterval(timer); };
  }, []);

  // 键帽在页面下面，确认框在上面：弹出时滚到眼前，不然点完键看不到要确认什么。
  useEffect(() => { if (confirm) confirmArea.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, [confirm]);

  const cap0 = operationAvailability(view);
  const cap = loadError ? { ...cap0, select: false, delete: false, import: false, reason: loadError } : cap0;
  const busy = Boolean(pending);
  const items = actionList(view);
  const item = items.find((x) => x.key === sel) || items[0];
  const pick = (key) => { if (key !== sel) { setSel(key); setEditing(false); setSwap((n) => n + 1); } };

  const onOp = async (args, success, after) => {
    if (working.current) return false;
    working.current = true; reader.current.invalidate();
    setPending('正在保存，等鸭子确认…'); setMessage(null);
    try {
      const r = await api('/api/models/op', args, 60000);
      if (!r.ok || !r.data) throw new Error(r.error || '没有成功，请刷新确认现在的状态。');
      if (mounted.current) { setView(r.data); setMessage({ kind: 'ok', text: success }); if (after === 'reset') setSel('b:roulade'); }
      return true;
    } catch (e) {
      if (mounted.current) setMessage({ kind: 'bad', text: e.name === 'AbortError' ? '等确认超时了，请刷新看现在的状态。' : e.message });
      return false;
    } finally {
      working.current = false;
      if (mounted.current) { setPending(''); setConfirm(null); }
    }
  };
  const ask = (c) => setConfirm(c);

  const doImport = async (file) => {
    if (!file || working.current) return;
    if (!/\.(onnx|zip|tar\.gz|tgz)$/i.test(file.name)) { setMessage({ kind: 'bad', text: '请选择 .onnx 模型，或 ZIP、tar.gz 动作包。' }); return; }
    working.current = true; reader.current.invalidate(); setMessage(null); setPending('正在上传动作包…');
    try {
      const before = new Set((view?.actions || []).map((a) => a.id));
      const r = await uploadAction(file, csrf, (p) => mounted.current && setPending(p === 100 ? '上传完了，正在检查模型…' : `正在上传动作包… ${p ?? ''}%`));
      if (!r.ok || !r.data) throw new Error(r.error || '没导入成功。');
      if (mounted.current) {
        setView(r.data);
        const added = (r.data.actions || []).find((a) => !before.has(a.id));
        if (added) { setSel('a:' + added.id); setSwap((n) => n + 1); }
        setMessage({ kind: 'ok', text: '动作放进来了，还没装到鸭子上。确认动作名和时长，点「装上」。' });
      }
    } catch (e) {
      if (mounted.current) setMessage({ kind: 'bad', text: e.message });
    } finally {
      working.current = false;
      if (fileInput.current) fileInput.current.value = '';
      if (mounted.current) setPending('');
    }
  };

  const play = async () => {
    if (!item?.ready || !item.skill || doing) return;
    setDoing(true); setMessage(null);
    const r = await link.cmd('pad.op', { op: 'skill', name: item.skill }, 4000);
    if (mounted.current) {
      setDoing(false);
      setMessage(r.ok ? { kind: 'ok', text: `鸭子在做「${item.name}」。` } : { kind: 'bad', text: r.reason || r.error || '鸭子没接这个动作。' });
    }
  };

  const tapKey = (button) => {
    if (!listen) {
      const row = (view?.bindings || []).find((b) => b.button === button);
      const hit = items.find((x) => x.skill && x.skill === row?.skill);
      if (hit) pick(hit.key);
      return;
    }
    const c = keyChoice(view, item, button);
    if (!c.valid) { setMessage({ kind: 'bad', text: c.reason }); return; }
    setListen(false);
    if (c.already) { setMessage({ kind: 'ok', text: `${keyLabel(button)} 本来就是「${item.name}」。` }); return; }
    const success = `${keyLabel(button)} 键现在做「${item.name}」`;
    if (c.confirm) ask({ lines: c.confirm, label: '换', args: c.args, success });
    else onOp(c.args, success);
  };

  const a = item?.action;
  const tags = [];
  if (item) {
    tags.push({ cls: '', text: item.kind === 'builtin' ? '出厂自带' : item.kind === 'walk' ? '出厂自带' : item.kind === 'manual' ? '别处加的' : '导入' });
    if (item.duration) tags.push({ cls: '', text: item.duration });
    if (item.kind !== 'walk') tags.push(item.keys.length ? { cls: 'key', text: item.keys.map(keyLabel).join('、') + ' 键' } : { cls: 'warn', text: '还没绑键' });
    if (item.kind === 'imported' && item.state === 'loaded') tags.push({ cls: 'warn', text: '新动作，先架起来试' });
    if (item.kind === 'imported' && item.state === 'none') tags.push({ cls: 'warn', text: '还没装到鸭子上' });
    if (item.state === 'failed') tags.push({ cls: 'bad', text: '没加载成功' });
  }
  const showInstall = item?.kind === 'imported' && (item.state !== 'loaded' || editing);
  const builtinCount = items.filter((x) => x.kind === 'builtin' || x.kind === 'walk').length;
  const importCount = items.length - builtinCount;
  const dockSub = item?.kind === 'walk' ? '走路用「手柄」页的摇杆，按 Start 站起来、再按 Start 开始走。'
    : !item?.ready ? (item?.error || '这个动作现在用不了。')
    : listen ? `现在点下面一个键，就把「${item.name}」放上去。` : '点播放，鸭子就做这个动作（要先按 Start 站好）。';

  return html`<section class="ac-page" aria-label="动作" aria-busy=${busy}>
    <header class="ac-head">
      <div><h1>动作</h1><p>选一个看看，点播放让鸭子做，或者绑到手柄键上。</p></div>
      <div class="ac-row">
        ${plazaUrl ? html`<a class="ac-btn" href=${plazaUrl} target="_blank" rel="noopener">动作广场 ↗</a>` : null}
        <button class="ac-btn blue" type="button" disabled=${busy || !cap.import} title=${!cap.import ? (view?.capabilities?.import_reason || cap.reason) : undefined}
          onClick=${() => fileInput.current?.click()}>导入动作包</button>
        <input ref=${fileInput} type="file" accept=".onnx,.zip,.tar.gz,.tgz" hidden onChange=${(e) => doImport(e.target.files[0])} aria-label="选择动作包" />
      </div>
    </header>
    ${loadError ? html`<div class="ac-note bad" role="alert">${loadError}</div>` : null}
    ${view && !cap.select && !loadError ? html`<div class="ac-note warn" role="status">${cap.reason}（看动作、让鸭子做不受影响；换键、装动作要先卸力）</div>` : null}
    ${message ? html`<div class=${`ac-note ${message.kind}`} role=${message.kind === 'bad' ? 'alert' : 'status'}>${message.text}</div>` : null}
    ${pending ? html`<div class="ac-note progress" role="status">${pending}</div>` : null}
    <div ref=${confirmArea}>${confirm ? html`<${ConfirmBox} lines=${confirm.lines} confirmLabel=${confirm.label} cancelLabel="取消" busy=${busy}
      onCancel=${() => setConfirm(null)} onConfirm=${() => onOp(confirm.args, confirm.success, confirm.after)} cls="ac-confirm" />` : null}</div>

    <div class="ac-grid">
      <section class="ac-stage" aria-label="舞台">
        ${item ? html`<div class=${`ac-stage-top swap${swap % 2}`}>
          <h2>${item.name}<span>${item.id}</span></h2>
          <div class="ac-tags">${tags.map((t) => html`<span class=${'ac-tag ' + t.cls}>${t.text}</span>`)}</div>
          ${item.kind === 'imported' && item.state !== 'none' && !editing ? html`<div class="ac-row ac-stage-acts">
            <button class="ac-btn ghost" type="button" disabled=${busy || !cap.select} onClick=${() => setEditing(true)}>改参数</button>
            <button class="ac-btn ghost" type="button" disabled=${busy || !cap.select} onClick=${() => ask({
              lines: [`把「${item.name}」从鸭子上卸下？`, item.keys.length ? `${item.keys.map(keyLabel).join('、')} 键会恢复出厂的动作。` : '卸下后文件还在，随时可以再装。'],
              label: '卸下', args: { op: 'action_uninstall', id: a.id }, success: `「${item.name}」卸下了` })}>卸下</button>
          </div>` : null}
        </div>
        <div class=${`ac-stage-pic swap${swap % 2}`}><${Thumb} item=${item} big=${true} /></div>
        ${showInstall ? html`<${InstallPanel} key=${item.key + (editing ? ':e' : '')} item=${item} busy=${busy} cap=${cap} onOp=${onOp} onConfirm=${ask}
          onCancel=${item.state === 'loaded' ? () => setEditing(false) : null} />` : html`<div class="ac-dock">
          <button class="ac-play" type="button" aria-label=${`让鸭子做 ${item.name}`} disabled=${!item.ready || !item.skill || doing} onClick=${play}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.4v13.2L17 10z" fill="#FFF"/></svg></button>
          <div class="ac-dock-text"><strong>${doing ? '正在发给鸭子…' : '让鸭子做这个动作'}</strong><span>${dockSub}</span></div>
          ${item.kind !== 'walk' ? html`<button class=${'ac-btn ghost' + (listen ? ' on' : '')} type="button" disabled=${busy || !cap.select || !item.ready}
            title=${!cap.select ? cap.reason : undefined} onClick=${() => setListen(!listen)}>${listen ? '取消' : '设置按键'}</button>` : null}
        </div>`}` : html`<p class="ac-dim">正在读动作列表…</p>`}
      </section>

      <section class="ac-list" aria-label="我的动作">
        <div class="ac-list-head"><h2>我的动作</h2><span>内置 ${builtinCount} · 导入 ${importCount}</span></div>
        ${items.map((x) => html`<button type="button" class=${'ac-item' + (x.key === item?.key ? ' sel' : '')} aria-pressed=${x.key === item?.key ? 'true' : 'false'} onClick=${() => pick(x.key)}>
          <${Thumb} item=${x} />
          <span class="ac-item-text"><span class="ac-item-name">${x.name}${x.kind === 'imported' && x.state === 'loaded' && !x.keys.length ? html`<em>新</em>` : null}</span>
            <span class="ac-item-sub">${x.id}${x.duration ? ' · ' + x.duration : ''}</span></span>
          ${x.kind === 'walk' ? html`<span class="ac-pill">摇杆</span>`
            : x.state === 'none' ? html`<span class="ac-pill warn">没装</span>`
            : x.state === 'failed' || x.state === 'missing' ? html`<span class="ac-pill bad">用不了</span>`
            : x.keys.length ? x.keys.map((k) => html`<span class="ac-cap">${keyLabel(k)}</span>`)
            : html`<span class="ac-pill warn">没绑键</span>`}
        </button>`)}
        <button class="ac-add" type="button" disabled=${busy || !cap.import} onClick=${() => fileInput.current?.click()}>＋ 导入动作包</button>
      </section>
    </div>

    <section class="ac-keys" aria-label="手柄按键">
      <div class="ac-keys-head"><div><h2>手柄按键</h2>
        <p>${listen ? `点一个键，把「${item?.name}」放上去。` : '五个动作键。点「设置按键」再点一个键，就把上面选中的动作绑上去。走路还是用摇杆。'}</p></div>
        <button class="ac-btn" type="button" disabled=${busy || !cap.select} onClick=${() => ask({ lines: ['五个键恢复出厂的动作？', '装上的动作不会被卸下。'],
          label: '恢复默认按键', args: { op: 'reset_bindings' }, success: '五个键恢复出厂了' })}>恢复默认按键</button></div>
      <div class="ac-keycaps">${KEYS.map((k) => html`<button type="button" class=${'ac-keycap' + (listen ? ' listen' : '')} aria-label=${`${k.label} 键：${keyName(view, k.button)}`}
        disabled=${busy} onClick=${() => tapKey(k.button)}><span class="cap">${k.label}</span><span class="what">${keyName(view, k.button)}</span></button>`)}</div>
    </section>

    <section class="ac-more">
      <div><h2>走路和站立模型</h2><p>换走路模型、恢复内置模型在这里，做法和原来的「模型」页一样。</p></div>
      <a class="ac-btn" href="#/models">打开</a>
    </section>
  </section>`;
}
