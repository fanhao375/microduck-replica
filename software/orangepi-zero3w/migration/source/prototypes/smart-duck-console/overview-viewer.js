import { Duck3D } from '@duck-zero/renderer';

const P = window.P;
// Original Studio web/servo.js POSES.stand, in the renderer's JOINT_NAMES order.
// This fixed pose is presentation data and never reads or writes hardware state.
const STAND = [0, -0.0873, -0.4579, -0.0049, 0.453, 0.3491, 0.3491, 0, 0, 0, 0, 0.0873, 0.4579, 0.0049, -0.453];
let stage = null;
let viewer = null;
let loading = false;
let generation = 0;

function syncControls() {
  const ready = !!viewer?.ready && stage?.dataset.overviewDuckState === 'ready';
  document.querySelectorAll('[data-overview-duck-reset]').forEach(button => { button.disabled = !ready; });
}

function showStatus(message, error = false) {
  if (!stage) return;
  stage.querySelector('[data-overview-duck-status]')?.remove();
  const panel = document.createElement('div');
  panel.className = `overview-duck-status${error ? ' overview-duck-error' : ''}`;
  panel.dataset.overviewDuckStatus = '';
  panel.setAttribute('role', error ? 'alert' : 'status');
  const text = document.createElement('span');
  text.textContent = message;
  panel.appendChild(text);
  if (error) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn small';
    retry.dataset.overviewDuckRetry = '';
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
  stage.dataset.overviewDuckState = 'loading';
  stage.setAttribute('aria-busy', 'true');
  showStatus('3D 模型加载中…');
  const canvas = document.createElement('canvas');
  canvas.className = 'overview-duck-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  stage.prepend(canvas);
  syncControls();
  try {
    const instance = new Duck3D(canvas, { view: 'front34', centerX: 0.5, centerY: 0.52, fill: 0.82, pageScroll: true });
    viewer = instance;
    await instance.load();
    if (token !== generation || initialStage !== stage || viewer !== instance) return;
    if (!instance.ready) throw new Error('3D 模型尚未完成加载');
    instance.setJoints(STAND);
    instance.setGravity(null);
    instance.setView('front34');
    loading = false;
    stage.dataset.overviewDuckState = 'ready';
    stage.setAttribute('aria-busy', 'false');
    stage.querySelector('[data-overview-duck-status]')?.remove();
    syncControls();
  } catch (error) {
    if (token !== generation || initialStage !== stage) return;
    viewer?.dispose();
    viewer = null;
    canvas.remove();
    loading = false;
    stage.dataset.overviewDuckState = 'error';
    stage.setAttribute('aria-busy', 'false');
    console.error('Overview 3D model could not load:', error);
    showStatus('3D 模型未能加载，请重新加载。', true);
    syncControls();
  }
}

function retain() {
  // Moving the original node keeps the user's camera angle, controls and WebGL context.
  return stage?.isConnected ? stage : null;
}

function mount(retainedStage = null) {
  const placeholder = document.querySelector('[data-overview-duck-stage]');
  if (!placeholder) { dispose(); return; }
  const existing = retainedStage === stage ? retainedStage : stage;
  if (existing && existing !== placeholder) placeholder.replaceWith(existing);
  stage = existing || placeholder;
  syncControls();
  if (!viewer && !loading && stage.dataset.overviewDuckState !== 'error') initialize();
}

function dispose() {
  ++generation;
  viewer?.dispose();
  viewer = null;
  loading = false;
  if (stage) {
    stage.removeAttribute('data-overview-duck-state');
    stage.removeAttribute('aria-busy');
    stage.replaceChildren();
  }
  stage = null;
  syncControls();
}

document.addEventListener('click', event => {
  const target = event.target.closest?.('[data-overview-duck-reset],[data-overview-duck-retry]');
  if (!target) return;
  if (target.hasAttribute('data-overview-duck-retry')) {
    if (!stage?.contains(target)) return;
    dispose(); mount();
    return;
  }
  if (viewer?.ready) viewer.resetView();
});

P.overviewDuck = { retain, mount, dispose };
// ESM evaluation can finish after the first classic-script render.
mount();
