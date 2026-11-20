import { Duck3D } from '@duck-zero/renderer';
import { drawServoLabels, TAGS } from '@duck-zero/labels';

// Reference rendering is isolated from control: picks only select a visible item.
const P = window.P;
const colors = { R: '#E5484D', L: '#2F7CF6', H: '#F5B400' };
const namesById = Object.fromEntries(Object.entries(TAGS).map(([name, [id]]) => [id, name]));
const views = new Set(['front', 'side', 'back', 'front34', 'back34']);
let stage = null;
let viewer = null;
let clearLabels = null;
let labelsVisible = false;
let view = 'front';
let selection = null;
let generation = 0;
let loading = false;

function syncControls() {
  const ready = !!viewer?.ready && stage?.dataset.zeroReady === 'true';
  document.querySelectorAll('[data-zero-view]').forEach(button => {
    button.disabled = !ready;
    if (button.dataset.zeroView !== 'reset') button.setAttribute('aria-pressed', String(button.dataset.zeroView === view));
  });
  document.querySelectorAll('[data-zero-labels]').forEach(button => {
    button.disabled = !ready;
    button.setAttribute('aria-pressed', String(labelsVisible));
  });
}

function syncSelection() {
  if (!viewer?.ready) return;
  const id = P.state.devices?.expandedJoint ?? null;
  if (selection === id) return;
  selection = id;
  const name = namesById[id];
  viewer.select(name || null, name ? colors[TAGS[name][3]] : null);
}

function syncLabels() {
  if (!labelsVisible || !viewer?.ready || !stage) {
    clearLabels?.();
    clearLabels = null;
    return;
  }
  if (!clearLabels) clearLabels = drawServoLabels(viewer, stage);
}

function status(message, error = false) {
  if (!stage) return;
  stage.querySelector('[data-zero-status]')?.remove();
  const panel = document.createElement('div');
  panel.className = `zero-reference-status${error ? ' zero-reference-error' : ''}`;
  panel.dataset.zeroStatus = '';
  panel.setAttribute('role', error ? 'alert' : 'status');
  const text = document.createElement('span');
  text.textContent = message;
  panel.appendChild(text);
  if (error) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn small';
    retry.dataset.zeroRetry = '';
    retry.textContent = '重新加载模型';
    panel.appendChild(retry);
  }
  stage.appendChild(panel);
}

async function initialize() {
  if (!stage || viewer || loading) return;
  const token = ++generation;
  const initialStage = stage;
  loading = true;
  stage.dataset.zeroState = 'loading';
  stage.removeAttribute('data-zero-ready');
  stage.setAttribute('aria-busy', 'true');
  status('零位模型加载中…');
  const canvas = document.createElement('canvas');
  canvas.className = 'zero-reference-canvas';
  canvas.setAttribute('aria-label', '零位参考模型；仅显示参考姿势');
  stage.prepend(canvas);
  try {
    // Match the original servo zero-reference framing and original material pipeline.
    const instance = new Duck3D(canvas, { view, centerX: 0.5, centerY: 0.52, fill: 0.82, pageScroll: true });
    viewer = instance;
    instance.onPick = name => {
      const id = TAGS[name]?.[0];
      if (id !== undefined) P.pages.devices?.handle?.('joint-select', { dataset: { id: String(id) } });
    };
    await instance.load();
    if (token !== generation || initialStage !== stage || viewer !== instance) return;
    if (!instance.ready) throw new Error('模型尚未完成加载');
    instance.setJoints(Array(15).fill(0));
    instance.setGravity(null);
    instance.setView(view);
    selection = null;
    loading = false;
    stage.dataset.zeroState = 'ready';
    stage.dataset.zeroReady = 'true';
    stage.setAttribute('aria-busy', 'false');
    stage.querySelector('[data-zero-status]')?.remove();
    syncSelection();
    syncLabels();
    syncControls();
  } catch (error) {
    if (token !== generation || initialStage !== stage) return;
    clearLabels?.(); clearLabels = null;
    viewer?.dispose(); viewer = null;
    canvas.remove();
    loading = false;
    stage.dataset.zeroState = 'error';
    stage.removeAttribute('data-zero-ready');
    stage.setAttribute('aria-busy', 'false');
    console.error('Zero-reference model could not load:', error);
    status('零位模型未能加载，请重新加载。', true);
    syncControls();
  }
}

function retain() {
  // Return the actual canvas-containing node before root render replaces its parent.
  // Moving it preserves OrbitControls, model caches, current view and WebGL context.
  return stage?.isConnected ? stage : null;
}

function mount(retainedStage = null) {
  const placeholder = document.querySelector('[data-zero-stage]');
  if (!placeholder) { dispose(); return; }
  const existing = retainedStage === stage ? retainedStage : stage;
  if (existing && existing !== placeholder) placeholder.replaceWith(existing);
  stage = existing || placeholder;
  syncSelection();
  syncLabels();
  syncControls();
  if (!viewer && !loading && stage.dataset.zeroState !== 'error') initialize();
}

function dispose() {
  ++generation;
  clearLabels?.(); clearLabels = null;
  viewer?.dispose(); viewer = null;
  loading = false;
  labelsVisible = false;
  selection = null;
  view = 'front';
  if (stage) {
    stage.removeAttribute('data-zero-ready');
    stage.removeAttribute('data-zero-state');
    stage.removeAttribute('aria-busy');
    stage.replaceChildren();
  }
  stage = null;
}

document.addEventListener('click', event => {
  const target = event.target.closest?.('[data-zero-view],[data-zero-labels],[data-zero-retry]');
  if (!target) return;
  if (target.hasAttribute('data-zero-retry')) {
    if (!stage?.contains(target)) return;
    dispose(); mount();
    return;
  }
  if (!viewer?.ready) return;
  if (target.hasAttribute('data-zero-labels')) {
    labelsVisible = !labelsVisible;
    syncLabels(); syncControls();
    return;
  }
  const requested = target.dataset.zeroView;
  if (requested === 'reset') viewer.resetView();
  else if (views.has(requested)) { view = requested; viewer.setView(view); }
  syncControls();
});

P.zeroReference = { retain, mount, dispose };
// Module loading may finish after the first root render; fill its placeholder now.
mount();
