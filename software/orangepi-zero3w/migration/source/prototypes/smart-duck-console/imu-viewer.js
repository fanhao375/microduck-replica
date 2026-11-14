import { Duck3D } from '@duck-zero/renderer';

// Adapted from Studio web/imu.js:334–461 (Apache-2.0); geometry comes from the
// existing offline renderer. This module renders supplied demo vectors only.
const P = window.P;
const DOWN = [0, 0, -1];
const STAND = [0, -0.0873, -0.4579, -0.0049, 0.453, 0.3491, 0.3491, 0, 0, 0, 0, 0.0873, 0.4579, 0.0049, -0.453];
const DIRECTIONS = { fwd: [1, 0, 0], back: [-1, 0, 0], left: [0, 1, 0], right: [0, -1, 0] };
const SHORT = { fwd: '前', back: '后', left: '左', right: '右' };
const records = new Map();
const radians = value => value * Math.PI / 180;
const degrees = value => value * 180 / Math.PI;
const reducedMotion = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };
const validVector = (value, size) => Array.isArray(value) && value.length === size && value.every(Number.isFinite) && Math.hypot(...value) > 1e-9;
const unit = vector => { const length = Math.hypot(...vector); return vector.map(value => value / length); };
function angle(a, b) {
  if (!a || !b) return null;
  const x = unit(a), y = unit(b);
  return degrees(Math.acos(Math.max(-1, Math.min(1, x.reduce((sum, value, i) => sum + value * y[i], 0)))));
}

// Original Studio qRotate convention is [w,x,y,z], not Three's [x,y,z,w].
function rotate(q, vector) {
  const [w, x, y, z] = q;
  const matrix = [[1 - 2 * (y*y + z*z), 2 * (x*y - w*z), 2 * (x*z + w*y)],
    [2 * (x*y + w*z), 1 - 2 * (x*x + z*z), 2 * (y*z - w*x)],
    [2 * (x*z - w*y), 2 * (y*z + w*x), 1 - 2 * (x*x + y*y)]];
  return matrix.map(row => row.reduce((sum, value, i) => sum + value * vector[i], 0));
}

function computeFrame(raw = {}) {
  const gravity = validVector(raw.gravity, 3) ? raw.gravity.slice() : null;
  const correction = validVector(raw.correction, 4) ? unit(raw.correction) : null;
  const compare = raw.correction != null;
  const after = gravity && correction ? rotate(correction, gravity) : null;
  const pitch = Number.isFinite(raw.parts?.pitch_deg) ? raw.parts.pitch_deg : 0;
  const roll = Number.isFinite(raw.parts?.roll_deg) ? raw.parts.roll_deg : 0;
  const sideways = compare && Math.abs(pitch) >= Math.abs(roll);
  return {
    gravity, after, compare, sideways,
    beforeAngle: angle(gravity, DOWN), afterAngle: angle(after, DOWN), difference: angle(gravity, after),
    stale: !!raw.stale || !gravity || (compare && !correction),
    arrow: !compare && !raw.stale && DIRECTIONS[raw.arrow] ? raw.arrow : null,
    cue: typeof raw.cue === 'string' ? raw.cue : '',
    view: compare ? (sideways ? { az: -80, el: 6 } : 'front') : raw.collecting ? 'back34' : 'front34',
  };
}

function readFrame() {
  try { return computeFrame(P.getImuViewerState?.() || P.imuPage?.getViewerState?.() || {}); }
  catch (error) { console.error('IMU demo state is unavailable:', error); return computeFrame({ stale: true }); }
}

function applyView(duck, view) {
  if (typeof view === 'string') { duck.setView(view); return; }
  duck.setView('front');
  const home = duck.home;
  if (!home) return;
  const target = home.target, distance = home.pos.distanceTo(target), az = radians(view.az), el = radians(view.el);
  home.pos.set(target.x + distance * Math.cos(el) * Math.cos(az), target.y + distance * Math.cos(el) * Math.sin(az), target.z + distance * Math.sin(el));
  duck.camera.position.copy(home.pos);
  duck.controls.target.copy(target);
  duck.controls.update();
  duck.dirty = true;
}

// Keep the original camera projection, plumb line and curved direction cue.
function drawOverlay(record, now = 0) {
  const { duck, canvas, svg, frame } = record;
  if (!duck?.ready || !duck.home || !duck.blob || !frame || frame.stale) { svg.innerHTML = ''; return; }
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const Vector = duck.camera.position.constructor;
  duck.camera.updateMatrixWorld();
  const foot = duck.blob.position, duckHeight = 2 * (duck.home.target.z - foot.z);
  const project = (x,y,z) => { const point = new Vector(x,y,z).project(duck.camera); return [(point.x + 1) / 2 * width, (1 - point.y) / 2 * height]; };
  let markup = '';
  if (frame.compare) {
    const [x1,y1] = project(foot.x,foot.y,foot.z), [x2,y2] = project(foot.x,foot.y,foot.z + duckHeight * 1.12);
    markup += `<line class="plumb" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  const arrow = record.key === 'before' ? frame.arrow : null;
  if (arrow) {
    const direction = DIRECTIONS[arrow], radius = duckHeight * 1.08;
    const swing = reducedMotion() ? 1 : 0.55 + 0.45 * Math.sin(now / 260);
    const end = 6 + 20 * swing, points = [];
    for (let value = 0; value <= end + 1e-6; value += 2) {
      const sine = Math.sin(radians(value)), cosine = Math.cos(radians(value));
      points.push(project(foot.x + radius * sine * direction[0], foot.y + radius * sine * direction[1], foot.z + radius * cosine));
    }
    if (points.length >= 2) {
      const [ex,ey] = points.at(-1), [px,py] = points.at(-2);
      const dx=ex-px, dy=ey-py, length=Math.hypot(dx,dy)||1, ux=dx/length, uy=dy/length, size=Math.max(12,height*.03);
      const head = `${ex+ux*size},${ey+uy*size} ${ex-uy*size*.6},${ey+ux*size*.6} ${ex+uy*size*.6},${ey-ux*size*.6}`;
      const [gx,gy] = project(foot.x,foot.y,foot.z);
      markup += `<line class="ghost" x1="${gx}" y1="${gy}" x2="${ex}" y2="${ey}"/><polyline class="arc" points="${points.map(point=>point.join(',')).join(' ')}"/><polygon class="head" points="${head}"/>`;
      const x=Math.min(width-50,Math.max(50,ex)), y=Math.min(height-50,Math.max(120,ey+size+26));
      markup += `<text class="lbl" x="${x}" y="${y}">往${SHORT[arrow]}倾</text>`;
    }
  }
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = markup;
}

function scheduleOverlay(record) {
  if (record.disposed || record.raf) return;
  record.raf = requestAnimationFrame(function tick(now) {
    record.raf = 0;
    if (record.disposed) return;
    if (!record.node.isConnected || document.hidden || !record.onScreen) return;
    drawOverlay(record, now);
    if (record.frame?.arrow && !record.frame.stale && !reducedMotion()) record.raf = requestAnimationFrame(tick);
  });
}

function updateText(record, frame) {
  const node = record.node, after = record.key === 'after';
  const label = node.querySelector('[data-imu-stage-label]'), value = node.querySelector('[data-imu-angle]');
  const note = node.querySelector('[data-imu-note]'), cue = node.querySelector('[data-imu-cue]');
  if (label) label.textContent = frame.compare ? (after ? '校准后' : '校准前') : '机身倾角（对比竖直）';
  const tilt = after ? frame.afterAngle : frame.beforeAngle;
  if (value) value.textContent = frame.stale || tilt === null ? '—' : `${tilt.toFixed(1)}°`;
  if (note) note.textContent = frame.stale ? '演示数据不可用，等待有效姿态' : frame.compare ? (after ? '修正后 · 演示数据' : '当前方向 · 演示') : '演示读数';
  if (cue) { cue.textContent = frame.compare || frame.stale ? '' : frame.cue; cue.hidden = !cue.textContent; }
  record.canvas?.classList.toggle('stale', frame.stale);
}

function updateRecord(record, frame) {
  record.frame = frame;
  updateText(record, frame);
  if (!record.duck?.ready) return;
  record.duck.setJoints(STAND);
  record.duck.setGravity(record.key === 'after' ? frame.after : frame.gravity);
  const key = JSON.stringify(frame.view);
  if (record.viewKey !== key) { applyView(record.duck, frame.view); record.viewKey = key; }
  scheduleOverlay(record);
}

function updateSummary(frame) {
  const summary = document.querySelector('[data-imu-summary]');
  if (!summary) return;
  summary.hidden = !frame.compare;
  summary.textContent = !frame.compare ? '' : frame.stale ? '姿态演示数据不可用，恢复后再比较。' : `演示：校准前 ${frame.beforeAngle.toFixed(1)}° → 校准后 ${frame.afterAngle.toFixed(1)}°，方向修正 ${frame.difference.toFixed(1)}°。虚线为竖直；从${frame.sideways ? '侧面' : '正面'}观察。`;
}

function showStatus(record, text, failed = false) {
  record.node.querySelector('[data-imu-model-status]')?.remove();
  const status = document.createElement('div');
  status.className = `imu-model-status${failed ? ' imu-model-error' : ''}`;
  status.dataset.imuModelStatus = '';
  status.setAttribute('role', failed ? 'alert' : 'status');
  const message = document.createElement('span'); message.textContent = text; status.appendChild(message);
  if (failed) {
    const retry = document.createElement('button'); retry.type='button'; retry.className='btn small';
    retry.dataset.imuRetry = record.key; retry.textContent='重新加载模型'; status.appendChild(retry);
  }
  record.node.appendChild(status);
}

async function initialize(record) {
  if (record.duck || record.loading || record.disposed) return;
  const token = ++record.generation;
  record.loading=true;
  record.node.dataset.imuState='loading'; record.node.removeAttribute('data-imu-ready'); record.node.setAttribute('aria-busy','true');
  const canvas=document.createElement('canvas'); canvas.className='imu-canvas'; canvas.setAttribute('aria-label',record.key==='after'?'校准后姿态演示模型':'机身姿态演示模型');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('class','imu-overlay'); svg.setAttribute('aria-hidden','true');
  record.canvas=canvas; record.svg=svg; record.node.prepend(canvas); record.node.appendChild(svg);
  showStatus(record,'姿态模型加载中…');
  try {
    const duck=new Duck3D(canvas,{view:'front34',centerX:.5,centerY:.56,fill:.7,pageScroll:true});
    record.duck=duck;
    record.change=()=>scheduleOverlay(record); duck.controls.addEventListener('change',record.change);
    record.resize=new ResizeObserver(record.change); record.resize.observe(record.node);
    if (typeof IntersectionObserver!=='undefined') { record.intersection=new IntersectionObserver(entries=>{for(const entry of entries)record.onScreen=entry.isIntersecting;if(record.onScreen)scheduleOverlay(record);}); record.intersection.observe(record.node); }
    await duck.load();
    if(record.disposed || token!==record.generation || records.get(record.key)!==record)return;
    if(!duck.ready)throw new Error('模型尚未完成加载');
    record.loading=false; record.node.dataset.imuState='ready'; record.node.dataset.imuReady='true'; record.node.setAttribute('aria-busy','false');
    record.node.querySelector('[data-imu-model-status]')?.remove();
    const frame=readFrame(); updateRecord(record,frame); updateSummary(frame);
  } catch(error) {
    if(record.disposed || token!==record.generation)return;
    releaseResources(record); record.loading=false;
    record.node.dataset.imuState='error'; record.node.removeAttribute('data-imu-ready'); record.node.setAttribute('aria-busy','false');
    console.error('IMU reference model could not load:',error); showStatus(record,'姿态模型未能加载，请重试。',true);
  }
}

function releaseResources(record) {
  cancelAnimationFrame(record.raf); record.raf=0;
  record.resize?.disconnect(); record.intersection?.disconnect();
  if(record.change)record.duck?.controls.removeEventListener('change',record.change);
  record.duck?.dispose(); record.duck=null;
  record.canvas?.remove(); record.svg?.remove(); record.canvas=null; record.svg=null;
  record.viewKey=null;
}

function disposeRecord(record) {
  record.disposed=true; ++record.generation; releaseResources(record);
  record.node.querySelector('[data-imu-model-status]')?.remove();
  record.node.removeAttribute('data-imu-ready'); record.node.removeAttribute('data-imu-state'); record.node.removeAttribute('aria-busy');
  records.delete(record.key);
}

function retain() { return new Map([...records].filter(([,record])=>record.node.isConnected).map(([key,record])=>[key,record.node])); }

function mount(retained = null) {
  const placeholders=[...document.querySelectorAll('[data-imu-stage]')].filter(node=>['before','after'].includes(node.dataset.imuStage));
  const present=new Set(placeholders.map(node=>node.dataset.imuStage));
  for(const [key,record] of [...records])if(!present.has(key))disposeRecord(record);
  if(!placeholders.length)return;
  const frame=readFrame();
  for(const placeholder of placeholders) {
    const key=placeholder.dataset.imuStage;
    let record=records.get(key);
    if(record) { const old=retained?.get?.(key)||record.node;if(old!==placeholder)placeholder.replaceWith(old);record.node=old; }
    else { record={key,node:placeholder,duck:null,canvas:null,svg:null,frame,viewKey:null,loading:false,disposed:false,generation:0,raf:0,onScreen:true};records.set(key,record); }
    updateRecord(record,frame);
    if(!record.duck&&!record.loading&&record.node.dataset.imuState!=='error')initialize(record);
  }
  updateSummary(frame);
}

function dispose() { for(const record of [...records.values()])disposeRecord(record); }

document.addEventListener('click',event=>{
  const target=event.target.closest?.('[data-imu-reset],[data-imu-retry]'); if(!target)return;
  if(target.hasAttribute('data-imu-retry')) {
    const record=records.get(target.dataset.imuRetry);if(!record||!record.node.contains(target))return;
    record.node.querySelector('[data-imu-model-status]')?.remove(); record.node.removeAttribute('data-imu-state'); initialize(record);return;
  }
  for(const [key,record] of records)if(target.dataset.imuReset==='all'||target.dataset.imuReset===key)record.duck?.resetView();
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)for(const record of records.values())scheduleOverlay(record);});

P.imuViewer={retain,mount,dispose};
mount();
