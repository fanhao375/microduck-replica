// Inline overview diagnostics. Visibility requests cached snapshots, never UART ownership.
import {html, useState, useEffect, useRef} from './vendor/preact-htm.module.js';
import {ServoMonitor, ServoInspector, ImuHealth, ReadOrderPanel} from './communication-health.js';

export function RuntimeDiagnostics({st, sv, imu, link, active = false, live = false,
  summaryLive = live && st?.robotd_ok !== false, svElapsed = 0, imuElapsed = 0}) {
  const [open, setOpen] = useState(false), [tab, setTab] = useState('servos');
  const [selected, setSelected] = useState(null), [pendingOrder, setPendingOrder] = useState(null);
  const [inView, setInView] = useState(false);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const box = useRef(null);
  useEffect(() => {
    const visibility = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', visibility);
    let observer;
    const measure = () => { const r=box.current?.getBoundingClientRect();setInView(!!r && r.bottom>0 && r.top<innerHeight); };
    if (typeof IntersectionObserver === 'function') {
      observer = new IntersectionObserver(entries => setInView(entries.some(e=>e.isIntersecting)));
      observer.observe(box.current);
    } else {
      measure();window.addEventListener('scroll', measure, {passive:true});window.addEventListener('resize', measure);
    }
    return () => {observer?.disconnect();document.removeEventListener('visibilitychange',visibility);
      window.removeEventListener('scroll',measure);window.removeEventListener('resize',measure);};
  }, []);
  useEffect(() => {
    link.setDiagnosticsVisible?.(!!(open && active && inView && visible));
    return () => link.setDiagnosticsVisible?.(false);
  }, [open, active, inView, visible, link]);
  const order=sv?.communication?.read_order;
  useEffect(() => {
    if (pendingOrder && (order?.session!==pendingOrder.session || order?.generation===pendingOrder.generation)) setPendingOrder(null);
  }, [order?.session, order?.generation, pendingOrder]);
  const monitor=pendingOrder ? {...sv,communication:{...sv?.communication,read_order:pendingOrder},
    rows:(sv?.rows || []).map(r=>({...r,health:null}))} : sv;
  const imuWord=({ok:'正常',unverified:'方向待确认',frozen:'读数冻住',missing:'未读到'})[st?.imu?.status] || '待确认';
  return html`<section class="board runtime-diagnostics" id="runtime-diagnostics" aria-label="诊断信息" ref=${box}>
    <button type="button" class="runtime-diag-toggle" data-diagnostics-toggle aria-expanded=${open} onClick=${()=>setOpen(!open)}>
      <span class="runtime-diag-title"><b>诊断信息</b><small>舵机与 IMU · 只读查看</small></span>
      <span class=${'runtime-diag-summary'+(summaryLive?'':' stale')}>
        <span>舵机 <b>${summaryLive?st?.servos?.online ?? '—':'—'} / ${st?.servos?.total ?? 15}</b></span>
        <span>IMU <b>${summaryLive?imuWord:live?'数据未更新':'等待连接'}</b></span>
      </span><span class="runtime-diag-expand">${open?'收起':'展开详情'} ${open?'▴':'▾'}</span>
    </button>
    ${open ? html`<div class="runtime-diag-body">
      <div class="runtime-diag-toolbar"><div class="comm-view-tabs" role="tablist" aria-label="诊断类型">
        <button role="tab" aria-selected=${tab==='servos'} onClick=${()=>setTab('servos')}>舵机诊断</button>
        <button role="tab" aria-selected=${tab==='imu'} onClick=${()=>setTab('imu')}>IMU 诊断</button>
      </div><a href=${tab==='servos'?'#/servos':'#/imu'}>${tab==='servos'?'前往舵机校准':'前往 IMU 标定'} →</a></div>
      ${tab==='servos' ? html`<div class="comm-monitor-grid">
        <${ServoMonitor} sv=${monitor} selected=${selected} onSelect=${setSelected} live=${live} elapsed=${svElapsed} />
        <aside class="comm-monitor-aside">
          <${ServoInspector} sv=${monitor} selected=${selected} live=${live} elapsed=${svElapsed} />
          <${ReadOrderPanel} communication=${monitor?.communication} rows=${monitor?.rows} link=${link}
            live=${live && svElapsed<1500} onApplied=${setPendingOrder} />
        </aside></div>` : html`<${ImuHealth} imu=${imu} live=${live} elapsed=${imuElapsed} />`}
    </div>`:null}
  </section>`;
}
