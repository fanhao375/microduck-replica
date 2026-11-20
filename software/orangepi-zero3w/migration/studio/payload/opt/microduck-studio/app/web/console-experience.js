import { html, useEffect, useRef } from './vendor/preact-htm.module.js';
import { PadPage, padActivity } from './pad.js?v=20261110-walk';
import { VoicePage } from './voice.js';
import { CameraPage, TofPage } from './sensors.js';
import { AudioInteraction } from './audio-interaction.js';

if (typeof document !== 'undefined' && !document.getElementById('console-experience-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'console-experience-css'; sheet.rel = 'stylesheet';
  sheet.href = new URL('./console-experience.css?v=20261110-palette', import.meta.url).href;
  document.head.appendChild(sheet);
}

const REMOTE = [['control', '网页遥控'], ['recording', '录制与动作库'], ['gamepad', '实体手柄']];
const SENSING = [['camera', '摄像头'], ['tof', 'ToF 距离'], ['voice', '网页语音'], ['audio', '监听与音乐']];
function SectionTabs({ family, section, items }) {
  return html`<nav class="console-section-tabs" aria-label=${family === 'remote' ? '遥控分类' : '语音与视觉分类'}>
    ${items.map(([id, label]) => html`<a key=${id} href=${`#/${family}/${id}`} aria-current=${section === id ? 'page' : undefined}>${label}</a>`)}
  </nav>`;
}

// This component keeps one PadPage below one fixed parent across all remote tabs.
export function ConsoleRemote({ section = 'control', st, pad, link, api, live }) {
  const current = REMOTE.some(([id]) => id === section) ? section : 'control';
  useEffect(() => { padActivity.useStandaloneRoutes(); }, []);
  return html`<div class="console-experience console-remote">
    <${SectionTabs} family="remote" section=${current} items=${REMOTE} />
    <${PadPage} section=${current} st=${st} pad=${pad} link=${link} api=${api} live=${live} previewActive=${false} />
  </div>`;
}

// Audio controls need page visibility, never drive-area activity or a drive lease.
function useAudioActivity(enabled) {
  const state = useRef(null);
  if (!state.current) {
    const value = { enabled, subscribers: new Set() };
    value.activity = {
      get active() { return value.enabled && document.visibilityState === 'visible'; },
      subscribe(fn) { value.subscribers.add(fn); return () => value.subscribers.delete(fn); },
    };
    state.current = value;
  }
  const value = state.current;
  value.enabled = enabled;
  useEffect(() => {
    const notify = () => value.subscribers.forEach(fn => fn({ active: value.activity.active }));
    notify();
    document.addEventListener('visibilitychange', notify);
    return () => { document.removeEventListener('visibilitychange', notify); value.subscribers.forEach(fn => fn({ active: false })); };
  }, [value, enabled]);
  return value.activity;
}

export function ConsoleSensing({ section = 'camera', st, pad, link, api, live, open }) {
  const current = SENSING.some(([id]) => id === section) ? section : 'camera';
  const activity = useAudioActivity(current === 'audio');
  let content;
  if (current === 'camera') content = html`<${CameraPage} api=${api} />`;
  else if (current === 'tof') content = html`<${TofPage} api=${api} />`;
  else if (current === 'voice') content = html`<${VoicePage} st=${st} live=${live} open=${open} link=${link} />`;
  else content = html`<section class="console-audio-page">
    <header class="sensor-heading"><div><h1>监听与音乐</h1><p>使用小鸭上的麦克风。</p></div></header>
    <${AudioInteraction} pad=${pad} link=${link} activity=${activity} />
    <section class="board console-audio-motion"><h2>随乐律动</h2><p>听音乐后，在遥控页站稳并开始走，再开启随乐律动。</p><a class="btn-light" href="#/remote/control">前往遥控</a></section>
  </section>`;
  return html`<div class="console-experience console-sensing">
    <${SectionTabs} family="sensing" section=${current} items=${SENSING} />${content}
  </div>`;
}
