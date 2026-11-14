import {html, useEffect, useRef, useState} from './vendor/preact-htm.module.js';
import {Icon} from './console-icons.js';
import {PAGES, SEARCH, normalizeRoute, routeInfo} from './console-routes.js';

const GROUPS = [['日常使用',['overview','remote','sensing']],['设备与系统',['devices','models','logs','settings']]];
const num = (value, suffix='', digits=null) => Number.isFinite(value) ? `${digits===null?value:value.toFixed(digits)}${suffix}` : '—';

function Search({close}) {
  const [query,setQuery] = useState('');
  const ref = useRef(null);
  useEffect(()=>{ref.current.showModal();},[]);
  const results = SEARCH.filter(([,label])=>label.toLowerCase().includes(query.toLowerCase()));
  return html`<dialog ref=${ref} class="console-search" onClose=${close}>
    <div class="console-search-head"><h2>搜索页面与功能</h2><button class="console-icon-button" aria-label="关闭搜索" onClick=${close}>×</button></div>
    <input type="search" autoFocus value=${query} placeholder="输入页面或功能名称" aria-label="搜索页面与功能" onInput=${e=>setQuery(e.target.value)} />
    <div class="console-search-results">${results.length ? results.map(([path,label])=>html`<a href=${'#'+normalizeRoute(path)} onClick=${close}>${label}<${Icon} name="chevron" size=${14}/></a>`) : html`<p>没有匹配的功能</p>`}</div>
  </dialog>`;
}

export function ConsoleLayout({route, duck, st, live, open, emergency, children}) {
  const info = routeInfo(route), [menu,setMenu] = useState(false), [search,setSearch] = useState(false);
  useEffect(()=>{setMenu(false);},[route]);
  useEffect(()=>{
    const key = e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setSearch(v=>!v);}if(e.key==='Escape')setMenu(false);};
    window.addEventListener('keydown',key);return ()=>window.removeEventListener('keydown',key);
  },[]);
  const mode = st?.servo_session ? '设备维护' : ({motion:'运动模式',commissioning:'标定模式',read_only:'只读模式'}[st?.mode] || '等待读回');
  return html`<div class=${'console-frame'+(menu?' console-menu-open':'')}>
    ${menu ? html`<button class="console-scrim" aria-label="收起菜单" onClick=${()=>setMenu(false)} />` : null}
    <aside class="console-sidebar"><a href="#/overview" class="console-brand"><svg viewBox="0 0 34 44" fill="none" aria-hidden="true"><path d="M16 24c-3-8-3-15 2-16 6-2 9 6 5 11 0 2 2 3 5 4 7 6 1 14-9 14C5 38 1 30 8 26l8-2Z" stroke="currentColor" stroke-width="2.3"/><circle cx="21" cy="12" r="1" fill="currentColor"/></svg><span><b>智能鸭</b><small>SMART DUCK</small></span></a>
      <div class="console-device"><${Icon} name="device" size=${14}/><span>${duck?.label||'连接设备'}</span><i class=${open?'online':''}/></div>
      <nav aria-label="主导航">${GROUPS.map(([label,pages])=>html`<div class="console-nav-group"><p>${label}</p>${pages.map(page=>html`<a href=${'#'+normalizeRoute('/'+page)} aria-current=${info.page===page?'page':undefined}><${Icon} name=${PAGES[page][2]} size=${17}/>${PAGES[page][0]}</a>`)}</div>`)}</nav>
      <a class="console-account" href="#/settings/account"><span>我</span><div>设备账号<small>本地设备</small></div></a>
    </aside>
    <div class="console-workspace"><header class="console-toolbar"><div class="console-breadcrumb"><button class="console-icon-button" aria-label="展开或收起菜单" onClick=${()=>setMenu(v=>!v)}><${Icon} name="menu" size=${19}/></button><span>智能鸭空间</span><span>/</span><b>${info.title}</b></div><div class="console-tools"><button class="console-search-trigger" aria-label="打开功能搜索" onClick=${()=>setSearch(true)}><${Icon} name="search" size=${14}/><span>搜索页面与功能</span><kbd>⌘ K</kbd></button>${emergency}</div></header>
      <main class="console-main"><div class="console-page-heading"><h1>${info.title}</h1><p>${info.description}</p></div>${children}</main>
    </div>
    <nav class="console-mobile-nav" aria-label="手机常用导航">${[['overview','home','总览'],['remote','remote','遥控'],['devices','device','设备']].map(([page,icon,label])=>html`<a href=${'#'+normalizeRoute('/'+page)} aria-current=${info.page===page?'page':undefined}><${Icon} name=${icon} size=${20}/>${label}</a>`)}<button onClick=${()=>setMenu(v=>!v)}><${Icon} name="menu" size=${20}/>更多</button></nav>
    <footer class="console-statusbar" aria-label="设备状态"><span><${Icon} name="power" size=${12}/>动力 <b>${!open||!live?'未知':st?.estop?.state==='confirmed'?'已卸力':live?({holding:'保持',policy:'保持',walking:'保持',homing:'上力中',limp:'已卸力'}[st?.posture]||'待确认'):'未知'}</b></span><span><${Icon} name="device" size=${12}/>舵机 <b>${live?`${st?.servos?.online??'—'}/${st?.servos?.total??'—'}`:'—'}</b></span><span><${Icon} name="imu" size=${12}/>IMU <b>${live?({ok:'正常',unverified:'待验证',frozen:'冻结',missing:'未连接'}[st?.imu?.status]||'—'):'—'}</b></span><span class="console-optional"><${Icon} name="activity" size=${12}/>采集 <b>${live?num(st?.loop?.hz,' Hz',1):'—'}</b></span><span class="console-optional"><${Icon} name="thermometer" size=${12}/>温度 <b>${live?num(st?.cpu?.c,'°C'):'—'}</b></span><a href="#/settings/battery"><${Icon} name="battery" size=${12}/>${live?num(st?.battery?.pct,'%'):'—'}</a><span class="console-status-end">${mode}</span></footer>
    ${search?html`<${Search} close=${()=>setSearch(false)}/>`:null}
  </div>`;
}
