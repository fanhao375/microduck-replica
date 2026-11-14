// Layout composition only: each mounted section retains its original API and safety gates.
import { html } from './vendor/preact-htm.module.js';
import { Icon } from './console-icons.js';
import { ModelsPage } from './models.js';
import { ModelSettings } from './tune.js';
import { AiSettings } from './settings-ai.js';
import { BatterySettings } from './settings-battery.js';
import { UpdateSettings } from './settings-updates.js';
import { WifiSettings, DuckAddresses } from './wifi.js';
import { AccountSettings } from './console-account.js';
import { DisplaySettings } from './console-preferences.js';

if (!document.getElementById('console-system-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'console-system-css'; sheet.rel = 'stylesheet';
  sheet.href = new URL('./console-system.css', import.meta.url).href;
  document.head.appendChild(sheet);
}
const MODEL_SECTIONS = [['library', '模型库'], ['bindings', '手柄按键'], ['profile', '机器档案与参数']];
const SETTING_SECTIONS = [['network', '网络与连接'], ['account', '账号与 SSH'], ['ai', 'AI 连接'], ['battery', '电池'], ['updates', '软件更新'], ['display', '显示与关于']];
const CATEGORY_ICONS = {library:'layers',bindings:'remote',profile:'device',network:'wifi',account:'shield',ai:'message',battery:'battery',updates:'download',display:'settings'};
function Categories({ base, items, current }) {
  return html`<nav class="console-system-nav" aria-label=${base === 'models' ? '模型中心分类' : '系统设置分类'}>
    ${items.map(([key, label]) => html`<a href=${`#/${base}/${key}`} class=${key === current ? 'active' : ''} aria-current=${key === current ? 'page' : undefined}><${Icon} name=${CATEGORY_ICONS[key]} size=${15} />${label}</a>`)}
  </nav>`;
}
export function ConsoleModels({ section = 'library', api, csrf, link, live, tune }) {
  const current = MODEL_SECTIONS.some(([key]) => key === section) ? section : 'library';
  return html`<div class="console-system console-system-models">
    <${Categories} base="models" items=${MODEL_SECTIONS} current=${current} />
    <div class="console-system-content">${current === 'profile'
      ? html`<${ModelSettings} tune=${tune} link=${link} />`
      : html`<${ModelsPage} section=${current} api=${api} csrf=${csrf} link=${link} live=${live} tune=${tune} />`}</div>
  </div>`;
}
function NetworkSettings({ st, link }) {
  if (!st?.net?.known) return html`<section class="board console-unavailable"><h2>网络与连接</h2>
    <p>当前设备未提供配网服务。已有的中控连接仍可使用。</p></section>`;
  return html`<div class="console-settings-stack"><${DuckAddresses} net=${st.net} /><${WifiSettings} net=${st.net} link=${link} /></div>`;
}
export function ConsoleSettings({ section = 'network', api, csrf, link, st, tune }) {
  const current = SETTING_SECTIONS.some(([key]) => key === section) ? section : 'network';
  // Conditional VNodes ensure visiting one category does not request every service.
  const body = current === 'network' ? html`<${NetworkSettings} st=${st} link=${link} />`
    : current === 'account' ? html`<${AccountSettings} api=${api} st=${st} />`
    : current === 'ai' ? st?.auth?.has_password === false ? html`<section class="board console-unavailable"><h2>AI 文字连接</h2><p>设置网页登录密码后，可以管理连接和文字试聊。</p><a class="btn primary" href="#/settings/account">设置登录密码</a></section>` : html`<${AiSettings} api=${api} />`
    : current === 'battery' ? html`<${BatterySettings} link=${link} st=${st} />`
    : current === 'updates' ? html`<${UpdateSettings} api=${api} csrf=${csrf} />`
    : html`<div class="console-settings-stack"><${DisplaySettings} /><section class="board console-about"><h2>关于中控</h2><p>Microduck Studio · 设备网页控制台</p></section></div>`;
  return html`<div class="console-system console-system-settings"><${Categories} base="settings" items=${SETTING_SECTIONS} current=${current} />
    <div class="console-system-content">${body}</div></div>`;
}
