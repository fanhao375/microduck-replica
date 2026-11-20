import { html, useEffect, useRef, useState } from './vendor/preact-htm.module.js';
import { createMotionHeartbeat, isBackgroundMusic, motionTime } from './motion-heartbeat.js';
import { musicMotionLabel } from './audio-ui-state.js';

if (typeof document !== 'undefined' && !document.getElementById('motion-studio-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'motion-studio-css'; sheet.rel = 'stylesheet';
  sheet.href = new URL('./motion-studio.css', import.meta.url).href;
  document.head.appendChild(sheet);
}

const LABELS = { idle: '待开始', recording: '正在录制', record_paused: '录制已暂停', draft: '待保存',
  playing: '正在回放', play_paused: '回放已暂停', idle_motion: '灵动中' };
const START = { record_start: 'recording', play: 'playing', idle_start: 'idle_motion' };
const RUNNING = new Set(['recording', 'record_paused', 'playing', 'play_paused', 'idle_motion']);

export function MotionStudio({ pad, link, activity, view = 'full' }) {
  const motion = pad?.motion_studio;
  const [active, setActive] = useState(activity.active);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);
  const [name, setName] = useState('');
  const [selected, setSelected] = useState('');
  const [loops, setLoops] = useState(1);
  const [editing, setEditing] = useState(null);
  const [rename, setRename] = useState('');
  const [deleting, setDeleting] = useState(null);
  const focused = useRef(true), alive = useRef(false), epoch = useRef(0), busy = useRef(false);
  const expectedStop = useRef(false), lease = useRef(null);
  const currentMotion = useRef(motion), backgroundStart = useRef(false);
  currentMotion.current = motion;
  const state = motion?.state || 'idle';
  const owned = !!motion?.owner?.mine;
  const other = !!motion?.owner && !owned;
  const running = RUNNING.has(state);
  const recording = state === 'recording' || state === 'record_paused';
  const playing = state === 'playing' || state === 'play_paused';
  const clips = motion?.recordings || [];
  const chosen = clips.find(clip => clip.id === selected);
  const ready = active && link.status === 'open' && !pending && !other;

  useEffect(() => {
    alive.current = true;
    lease.current = createMotionHeartbeat({
      send: args => link.cmd('pad.op', args, 800),
      isActive: () => activity.active && focused.current,
      isConnected: () => link.status === 'open',
      onError: reason => { if (alive.current && !expectedStop.current) setError(reason); },
    });
    const interrupt = reason => {
      if (reason === 'inactive' && (backgroundStart.current ||
          (currentMotion.current?.owner?.mine && isBackgroundMusic(currentMotion.current)))) {
        lease.current?.interrupt(reason);
        return;
      }
      epoch.current += 1; busy.current = false; setPending(null);
      backgroundStart.current = false;
      lease.current?.interrupt(reason);
    };
    const offActivity = activity.subscribe(({ active: value }) => {
      setActive(value); if (!value) interrupt('inactive');
    });
    const offLink = link.on(message => { if (message.t === '_status' && message.status !== 'open') interrupt('disconnect'); });
    const blur = () => { focused.current = false; interrupt('inactive'); };
    const focus = () => { focused.current = true; };
    window.addEventListener('blur', blur); window.addEventListener('focus', focus);
    return () => {
      alive.current = false; epoch.current += 1;
      offActivity(); offLink(); window.removeEventListener('blur', blur); window.removeEventListener('focus', focus);
      lease.current?.dispose(); lease.current = null;
    };
  }, [link, activity]);

  useEffect(() => { lease.current?.sync(motion); }, [motion?.session_id, state, owned, motion?.idle_kind]);
  useEffect(() => {
    if (!clips.some(clip => clip.id === selected)) setSelected(clips[0]?.id || '');
  }, [clips, selected]);

  const command = async (action, params = {}) => {
    if (busy.current && action !== 'stop') return null;
    if (!activity.active || !focused.current || link.status !== 'open') { setError('请回到遥控区并恢复连接后操作'); return null; }
    const mine = ++epoch.current;
    const startingBackground = action === 'idle_start' && params.kind === 'music' && !recording;
    backgroundStart.current = startingBackground;
    busy.current = true; setPending(action); setError(null);
    const ending = action === 'stop' || action === 'record_finish' || (action === 'idle_stop' && !recording);
    if (ending) expectedStop.current = true;
    else if (START[action]) expectedStop.current = false;
    try {
      const request = { op: 'motion', action,
        ...(motion?.session_id && RUNNING.has(state) ? { session_id: motion.session_id } : {}), ...params };
      const result = await link.cmd('pad.op', request, 8000);
      if (!alive.current || epoch.current !== mine ||
          (!startingBackground && (!activity.active || !focused.current))) {
        if (START[action] && result?.ok && result.data?.session_id) {
          void link.cmd('pad.op', { op: 'motion', action: 'stop', session_id: result.data.session_id }, 1000);
        }
        return null;
      }
      if (!result?.ok) { if (ending) expectedStop.current = false; setError(result?.reason || result?.error || '操作没有完成'); return result; }
      if (ending) lease.current?.sync(null);
      if (START[action] && result.data?.session_id) {
        lease.current?.sync({ state: action === 'idle_start' && recording ? state : START[action],
          idle_kind: action === 'idle_start' ? params.kind || 'lively' : null,
          session_id: result.data.session_id, owner: { mine: true } });
      }
      if (action === 'save') setName('');
      if (action === 'rename') setEditing(null);
      if (action === 'delete') setDeleting(null);
      return result;
    } catch (err) {
      if (alive.current && mine === epoch.current) setError(err?.message || '操作连接中断');
      return null;
    } finally {
      if (alive.current && mine === epoch.current) {
        busy.current = false; backgroundStart.current = false; setPending(null);
      }
    }
  };
  const button = (action, label, enabled = true, params = {}, className = 'btn-light') => html`
    <button type="button" class=${className} data-motion=${action} data-motion-kind=${params.kind || null} disabled=${!ready || !enabled}
      onClick=${() => command(action, params)}>${pending === action ? '处理中…' : label}</button>`;
  const idleCanStart = !running || state === 'recording';
  const hasDraft = state === 'draft' && !!motion?.draft;
  const max = recording ? 60 : Math.max(0.1, motion?.duration_s || 0.1);
  const progress = Math.max(0, Math.min(max, motion?.elapsed_s || 0));

  return html`<section id="motion-studio" class="pd-motion" data-state=${state} data-loop=${motion?.loop || 0} aria-label="动作录制与回放">
    <div class="motion-heading"><h4>${view === 'compact' ? '互动与录制' : '动作录制与回放'}</h4>
      <span class=${`motion-state ${state === 'recording' ? 'is-recording' : ''}`} role="status">${musicMotionLabel(motion) || LABELS[state] || '状态待确认'}</span>
    </div>
    ${!motion ? html`<p class="pd-small-p">动作功能尚未就绪，连接后重新查看。</p>` : html`
      ${other ? html`<p class="pd-small-p pd-why">${motion.owner.by || '另一个页面'}正在使用动作功能，请先停止或接管。</p>` : null}
      ${error || motion.error ? html`<p class="motion-error" role="alert">${error || motion.error}</p>` : null}
      ${motion.library_warning ? html`<p class="motion-library-warning" role="status">${motion.library_warning}</p>` : null}
      <div class="motion-toolbar">
        ${!recording && !playing && state !== 'draft' ? view === 'compact'
          ? html`<a class="btn-light" href="#/remote/recording">录制与动作库</a>`
          : button('record_start', '开始录制', motion.record_available?.ok) : null}
        ${state === 'recording' ? button('record_pause', '暂停录制', owned) : null}
        ${state === 'record_paused' ? button('record_resume', '继续录制', owned) : null}
        ${recording ? button('record_finish', '结束录制', owned) : null}
        ${state === 'playing' ? button('pause', '暂停回放', owned) : null}
        ${state === 'play_paused' ? button('resume', '继续回放', owned) : null}
        ${motion.idle_running ? button('idle_stop', motion.idle_kind === 'music' ? '停止律动' : '停止灵动', owned) :
          button('idle_start', '灵动', idleCanStart && state !== 'draft' && motion.idle_available?.ok)}
        ${!motion.idle_running ? button('idle_start', '随乐律动', idleCanStart && state !== 'draft' && motion.idle_available?.ok
          && pad?.audio?.available && pad?.audio?.listening && pad?.audio?.music, {kind:'music'}) : null}
        ${running ? html`<button type="button" class="motion-stop" data-motion="stop" disabled=${!active || !owned || link.status !== 'open'}
          onClick=${() => command('stop', { session_id: motion.session_id })}>停止当前动作</button>` : null}
      </div>
      ${motion.posture_step ? html`<p class="motion-posture" data-motion-posture=${motion.posture_step.target} role="status">
        ${motion.posture_step.label}</p>` : null}
      ${!running && !hasDraft && motion.record_available?.why ? html`<p class="pd-small-p pd-why">${motion.record_available.why}</p>` : null}
      ${!motion.idle_running && !motion.idle_available?.ok && motion.idle_available?.why && motion.idle_available.why !== motion.record_available?.why
        ? html`<p class="pd-small-p">灵动：${motion.idle_available.why}</p>` : null}
      ${running ? html`<div class="motion-progress"><progress max=${max} value=${progress} aria-label="动作进度"></progress>
        <span class="num">${motionTime(motion.elapsed_s)}${playing ? ` / ${motionTime(motion.duration_s)} · 第 ${motion.loop || 1}/${motion.loops || 1} 次` : recording ? ' / 1:00.0' : ''}</span></div>` : null}
      ${hasDraft ? html`<form class="motion-save" onSubmit=${e => { e.preventDefault(); void command('save', { name: name.trim() }); }}>
        <p>录好了：${motionTime(motion.draft.duration_s)}${motion.draft.reason ? ` · ${motion.draft.reason}` : ''}</p>
        <label>动作名称<input name="motion-name" value=${name} maxLength="32" placeholder="例如：打招呼" autoComplete="off"
          onInput=${e => setName(e.currentTarget.value)} disabled=${!!pending || other} /></label>
        <div class="motion-toolbar">${button('save', '保存动作', !!name.trim(), { name: name.trim() }, 'btn-primary')}
          ${button('discard', '放弃这段', !other)}</div>
      </form>` : null}
      <div class="motion-library" hidden=${view === 'compact'}>
        <div class="motion-heading"><b>已保存的动作</b><small>${clips.length} / 20 段</small></div>
        ${clips.length ? html`<label>选择动作<select aria-label="选择已保存动作" value=${selected} disabled=${running || !!pending || other}
          onChange=${e => { setSelected(e.currentTarget.value); setEditing(null); setDeleting(null); }}>
          ${clips.map(clip => html`<option key=${clip.id} value=${clip.id}>${clip.name} · ${motionTime(clip.duration_s)}</option>`)}
        </select></label>
        <div class="motion-toolbar"><label class="motion-repeat">播放次数<select aria-label="回放次数" value=${loops}
          disabled=${running || !!pending || other} onChange=${e => setLoops(Number(e.currentTarget.value))}>
          ${[1, 3, 5].map(count => html`<option value=${count}>${count} 次</option>`)}</select></label>
          ${button('play', '开始回放', !!chosen && state === 'idle', { id: selected, loops }, 'btn-primary')}
        </div>
        ${chosen ? html`<p class="motion-clip-name">${chosen.name} <small>${motionTime(chosen.duration_s)}</small></p>
          <div class="motion-toolbar"><button type="button" class="btn-light" data-motion="rename_open" disabled=${!ready || running || hasDraft}
            onClick=${() => { setEditing(chosen.id); setRename(chosen.name); setDeleting(null); }}>改名</button>
          <button type="button" class="btn-light" data-motion="delete_open" disabled=${!ready || running || hasDraft}
            onClick=${() => { setDeleting(chosen.id); setEditing(null); }}>删除</button></div>` : null}
        ${editing && chosen?.id === editing ? html`<form class="motion-edit" onSubmit=${e => { e.preventDefault(); void command('rename', { id: editing, name: rename.trim() }); }}>
          <label>新名称<input name="motion-rename" value=${rename} maxLength="32" autoComplete="off" disabled=${!!pending}
            onInput=${e => setRename(e.currentTarget.value)} /></label><div class="motion-toolbar">
          ${button('rename', '保存名称', !!rename.trim() && !running, { id: editing, name: rename.trim() })}
          <button type="button" class="btn-light" disabled=${!!pending} onClick=${() => setEditing(null)}>取消改名</button></div>
        </form>` : null}
        ${deleting && chosen?.id === deleting ? html`<div class="motion-edit" role="group" aria-label="确认删除动作">
          <p>删除“${chosen.name}”？</p><div class="motion-toolbar">${button('delete', '确认删除', !running, { id: deleting })}
          <button type="button" class="btn-light" disabled=${!!pending} onClick=${() => setDeleting(null)}>保留动作</button></div></div>` : null}`
          : html`<p class="pd-small-p">还没有保存的动作。录一段操作，再起个名字。</p>`}
      </div>
      ${motion.last_result ? html`<p class="pd-small-p motion-result" role="status">${motion.last_result}</p>` : null}
      <details class="motion-help"><summary>录制范围与持续运行</summary><p class="pd-small-p motion-note">每段最多 60 秒。录下控制指令与节奏，回放时实际路线可能变化。
        支持摇杆、头部、身体、嘴部、叫声，以及坐下和站起；踢腿、翻滚、捡取暂不录制。
        坐站按目标姿态回放，等待状态确认后继续。普通停止会取消后续步骤，已经开始的坐站仍由主程序完成。
        普通灵动只做头部和嘴部的小动作。离开遥控区、切后台、失焦或断线会停止后续输出，回来后需重新开始。</p>
      <p class="pd-small-p motion-note">随乐律动让头部小幅跟拍；身体跟随效果取决于运动模型。没有清晰节拍时等待，音乐或收音停止后回正。
        独立随乐律动开启后，失焦、切到后台、滚出遥控区或切换站内页面仍会继续；关闭网页或连接断开会停止。
        录制中的随乐律动仍需保持前台和遥控区可见，离开后停止。返回页面可查看状态并停止律动。
        推网页摇杆、执行动作或实体手柄接管时，按现有控制规则停止律动。</p></details>
    `}
  </section>`;
}
