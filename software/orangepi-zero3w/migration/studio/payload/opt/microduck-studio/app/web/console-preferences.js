import { html, useState } from './vendor/preact-htm.module.js';

const STAGES = [['mid', '中灰'], ['graphite', '石墨'], ['light', '浅灰']];
export function applyStage(value) {
  const stage = STAGES.some(([key]) => key === value) ? value : 'light';
  if (stage === 'mid') document.documentElement.removeAttribute('data-stage');
  else document.documentElement.setAttribute('data-stage', stage);
}
export function loadStage() {
  try {
    const stage = localStorage.getItem('studio.stage');
    return STAGES.some(([key]) => key === stage) ? stage : 'light';
  } catch { return 'light'; }
}
export function DisplaySettings() {
  const [value, setValue] = useState(loadStage());
  const [message, setMessage] = useState('');
  const pick = stage => {
    setValue(stage); applyStage(stage);
    try { localStorage.setItem('studio.stage', stage); setMessage('已保存到此浏览器。'); }
    catch { setMessage('背景已切换；当前浏览器无法保存偏好。'); }
  };
  return html`<section class="board console-display"><h2>舞台背景</h2>
    <div class="console-stage-options">${STAGES.map(([key, label]) => html`<label>
      <input type="radio" name="console-stage" value=${key} checked=${value === key} onChange=${() => pick(key)} />${label}</label>`)}</div>
    <p class="console-caption">仅影响此浏览器的 3D 舞台显示。</p>
    ${message ? html`<p role="status">${message}</p>` : null}
  </section>`;
}
