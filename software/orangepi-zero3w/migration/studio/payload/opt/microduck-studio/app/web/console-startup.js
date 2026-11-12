import {html,useState,useEffect,useRef} from './vendor/preact-htm.module.js';
import {HandBackButton} from './servo.js?v=20261110-torque';
import {Icon} from './console-icons.js';
import {hardwareProblem,startupStep,startupToken} from './startup-state.js';

const PREF = 'studio.console.control';
const readChoice = ()=>{try{return localStorage.getItem(PREF)==='pad'?'pad':'web';}catch{return 'web';}};
function CommitDialog({st,link,close}) {
  const ref=useRef(null), running=useRef(false), watching=useRef(false);
  const [preview,setPreview]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[supported,setSupported]=useState(false),[notice,setNotice]=useState(null);
  const finish=(kind,text)=>{watching.current=false;running.current=false;setBusy(false);setNotice({kind,text});};
  useEffect(()=>{
    let alive=true;ref.current.showModal();
    link.cmd('calib.op',{op:'preview'},20000).then(r=>{if(alive){if(r.ok)setPreview(r.data);else setError(r.reason||r.error||'无法读取标定预览');}});
    const off=link.on(m=>{
      if(!watching.current||m.t!=='commit')return;
      if(m.phase==='started'||m.phase==='running')setNotice({kind:'progress',text:m.msg||'正在写入，主控会重启'});
      else if(m.phase==='done'||m.phase==='failed')finish(m.ok?'ok':'error',m.reason||(m.ok?'写进主程序了，主控已进入运动模式':'写入没有完成'));
    });
    return ()=>{alive=false;off();};
  },[link]);
  useEffect(()=>{
    const job=st?.commit?.job;
    if(!watching.current||!job)return;
    if(job.phase==='running')setNotice({kind:'progress',text:job.msg||'正在写入，主控会重启'});
    else if(job.phase==='done'||job.phase==='failed')finish(job.ok?'ok':'error',job.reason||job.msg||(job.ok?'写进主程序了，主控已进入运动模式':'写入没有完成'));
  },[st?.commit?.job]);
  const commit=async()=>{
    if(running.current||!supported||!st?.commit?.ready)return;
    running.current=true;watching.current=true;setBusy(true);setNotice({kind:'progress',text:'正在写入，主控会重启'});
    const r=await link.cmd('calib.op',{op:'commit'},20000);
    if(!watching.current)return;
    if(!r.ok||!r.data?.started)finish('error',r.reason||r.error||'提交未开始');
  };
  const done=notice?.kind==='ok';
  return html`<dialog ref=${ref} class="console-commit-modal" onCancel=${e=>{if(busy)e.preventDefault();}} onClose=${close}>
    <h2>预览标定提交</h2>
    ${(st?.commit?.items||[]).map(item=>html`<div class="console-commit-check"><b>${item.ok?'✓':'×'} ${item.label}</b><span>${item.text}</span></div>`)}
    ${preview?html`<details><summary>标定文件与关节方向</summary><pre>${preview.toml||JSON.stringify(preview.joints||[],null,2)}</pre></details>`:!error&&!notice?html`<p>正在读取标定预览…</p>`:null}
    <label><input type="checkbox" checked=${supported} onChange=${e=>setSupported(e.target.checked)} disabled=${busy||done}/> 已扶稳机身，允许卸力并重启主程序</label>
    ${error?html`<p class="console-commit-error" role="alert">${error}</p>`:null}
    ${notice?html`<p class=${notice.kind==='error'?'console-commit-error':'console-commit-status'+(notice.kind==='ok'?' ok':'')} role=${notice.kind==='error'?'alert':'status'}>${notice.text}</p>`:null}
    <div class="console-commit-actions"><button class="btn-light" disabled=${busy} onClick=${close}>${done?'关闭':'取消'}</button>${done?null:html`<button class="console-btn" disabled=${busy||!supported||!preview||!st?.commit?.ready} onClick=${commit}>${busy?'正在写入…':'写入并重启'}</button>`}</div>
  </dialog>`;
}

/** Only backend snapshots and command readbacks advance this guide; it never edits calibration on entry. */
export function useStartupFlow({st,sv,imu,tune,pad,live,open,link}) {
  const [choice,setChoice]=useState(readChoice),[checked,setChecked]=useState(false),[error,setError]=useState(''),[stopToken,setStopToken]=useState(null),[stand,setStand]=useState(null),[commitOpen,setCommitOpen]=useState(false);
  const token=startupToken(st);
  useEffect(()=>{
    if(!live||st?.servo_session){setStand(null);setStopToken(null);return;}
    // Posture is normalized by the backend from confirmed torque and the HOME guard.
    if(['holding','policy'].includes(st?.posture)&&st?.estop?.state==='ready')setStand(token);
    if(stand===token&&st?.posture==='limp'&&st?.estop?.state==='confirmed'){
      setStopToken(token);setStand(null);
    }
  },[live,st?.servo_session,st?.posture,st?.estop?.state,token,stand]);
  useEffect(()=>{const show=()=>setCommitOpen(true);window.addEventListener('console:commit',show);return()=>window.removeEventListener('console:commit',show);},[]);
  const step=startupStep({st,sv,imu,tune,live,open,checked,choice,stopVerified:stopToken===token,standObserved:stand===token});
  const act=()=>{
    setError('');
    if(step.action==='reconnect')link.reconnect();
    else if(step.action==='check'){const problem=hardwareProblem(st,live);setError(problem);setChecked(!problem);}
    else if(step.action==='commit')setCommitOpen(true);
    else if(step.route)location.hash='#'+step.route;
  };
  const control=(scope)=>step.index===5?html`<div class="console-start-choice" role="group" aria-label="首次启动控制方式">${[['web','网页遥控'],['pad','实体手柄（可选）']].map(([value,label])=>html`<label><input type="radio" name=${"console-start-control-"+scope} value=${value} checked=${choice===value} onChange=${()=>{setChoice(value);setStand(null);setStopToken(null);try{localStorage.setItem(PREF,value);}catch{}}}/>${label}</label>`)}</div>`:null;
  const button=()=>step.action==='handoff'?html`<${HandBackButton} link=${link}/>`:html`<button class="console-btn" disabled=${step.disabled||!open&&step.action!=='reconnect'} onClick=${act}>${step.label}<${Icon} name="arrow" size=${14}/></button>`;
  const card=html`<section class="board console-start-card" aria-label="首次启动"><div class="console-start-icon"><${Icon} name=${step.id==='complete'?'check':'compass'} size=${23}/></div><div class="console-start-copy"><div class="console-start-caption"><span>首次启动</span><span>${step.id==='complete'?'已完成':`${step.index+1} / 7`}</span></div><h2>${step.title}</h2><p>${error||step.detail}</p>${control("card")}</div>${button()}</section>`;
  const context=route=>{
    if(route==='/overview'||step.id==='complete')return null;
    const own=step.route===route||step.id==='commit'&&route==='/devices/imu'||step.action==='handoff';
    return html`<aside class="console-start-progress" aria-label="首次启动当前步骤"><div><small>首次启动 · ${step.index+1} / 7</small><strong>${step.title}</strong><p>${error||step.detail}</p>${control("context")}</div>${own?null:button()}</aside>`;
  };
  return {card,context,dialogs:commitOpen?html`<${CommitDialog} st=${st} link=${link} close=${()=>setCommitOpen(false)}/>`:null};
}
