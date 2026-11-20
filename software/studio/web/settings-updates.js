import {html,useEffect,useRef,useState} from './vendor/preact-htm.module.js';
import {updateMessage,updateOffer,updateActions,PHASES,nativeMessage,nativeActions} from './updates-ui-state.js';

const CSS=`.update-settings{padding:18px 20px;min-width:0;overflow-wrap:anywhere}.update-settings h2{font-size:20px;margin:0 0 8px}.update-settings p{font-size:13px;line-height:1.65;color:var(--text-2);margin:8px 0}.update-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.update-settings button{border:0;border-radius:999px;padding:9px 14px;min-height:38px;font:inherit;font-size:14px;background:#e9e9ee;color:#242428;cursor:pointer}.update-settings button.primary{background:#007aff;color:white}.update-settings button:disabled{opacity:.4;cursor:default}.update-settings button:focus-visible{outline:2px solid #007aff;outline-offset:2px}.update-settings [role=alert]{color:var(--red,#b42318)}.update-settings pre{white-space:pre-wrap;font:inherit;font-size:13px;line-height:1.6;max-height:200px;overflow:auto}.update-settings input{max-width:100%;font-size:13px}.update-settings progress{width:100%}@media(max-width:480px){.update-settings{padding:16px 12px}.update-settings button{max-width:100%;white-space:normal}}`;
if(!document.getElementById('updates-css')){
  const style=document.createElement('style');style.id='updates-css';style.textContent=CSS+`
    .update-offer{margin:12px 0;padding:12px 16px;background:var(--page,#f2f2f7);border-radius:12px}
    .update-offer h4{margin:0;font-size:16px}.update-offer pre{margin:6px 0}
    .update-settings summary{cursor:pointer;font-size:14px}.update-settings [data-offline]{margin:12px 0}
    .update-settings{scroll-margin-top:calc(72px + var(--bar-h,0px))}
    .update-notice{display:inline-block;align-self:flex-start;padding:6px 0;color:var(--link);font-size:14px}
  `;document.head.appendChild(style);
}

export function UpdateNotice({api}){
  const [version,setVersion]=useState(null);
  useEffect(()=>{
    const controller=new AbortController();
    const read=body=>api('/api/updates',body,10000,controller.signal);
    const delays=[1000,3000,7000,15000,30000];
    let timer,index=0;
    function show(result){
      if(controller.signal.aborted||!result.ok||result.online_update!==true)return;
      if(!result.busy){
        setVersion(updateOffer(result)?.version||null);return;
      }
      if(index<delays.length){
        timer=setTimeout(()=>read().then(show).catch(()=>{}),delays[index++]);
      }
    }
    async function check(){
      const status=await read();
      if(controller.signal.aborted||!status.ok||!status.available||status.online_update!==true
        ||status.busy||status.native?.active||status.native?.submitting)return;
      show(await read({op:'check_auto'}));
    }
    check().catch(()=>{});
    return ()=>{controller.abort();clearTimeout(timer);};
  },[api]);
  return version?html`<a class="update-notice" data-update-notice href="#/settings/updates">发现新版本 ${version} · 查看更新</a>`:null;
}

export function UpdateSettings({api,csrf}){
  const [data,setData]=useState(null),[pending,setPending]=useState(false),[error,setError]=useState(''),[reconnecting,setReconnecting]=useState(false);
  const [confirmed,setConfirmed]=useState(false),[readError,setReadError]=useState('');
  const mounted=useRef(true),loading=useRef(false),input=useRef(null);
  const submitting=useRef(false),revision=useRef(0),checkedOnEntry=useRef(false);
  const merge=result=>setData(previous=>({...previous,...result,native:result.native??previous?.native}));
  async function refresh(){
    if(loading.current||submitting.current)return;
    const started=revision.current;loading.current=true;
    try{const result=await api('/api/updates');if(!mounted.current||started!==revision.current)return;
      if(!result.ok){setReadError(result.error||'暂时无法读取更新状态');setReconnecting(!result.error);return;}
      setData(result);setReadError('');setReconnecting(false);
      if(result.available&&!checkedOnEntry.current){
        checkedOnEntry.current=true;
        if(result.online_update===true&&!result.busy&&!result.native?.active&&!result.native?.submitting)operate('check_auto');
      }
    }catch{if(mounted.current&&started===revision.current)setReconnecting(true);}finally{loading.current=false;}
  }
  useEffect(()=>{mounted.current=true;refresh();const timer=setInterval(refresh,2000);return()=>{mounted.current=false;clearInterval(timer);};},[]);
  async function operate(op,version){
    const native=op==='native_install'||op==='native_recover'||op==='native_restore';
    if(submitting.current||(native&&!confirmed))return;
    submitting.current=true;revision.current++;setPending(true);setError('');
    const body=native?{op,confirmed:true}:op==='online'?{op,version}:{op};
    try{const result=await api('/api/updates',body,20000);if(!mounted.current)return;
      if(result.ok)merge(result);
      else if(result.error)setError(result.error);
      else setReconnecting(true);
    }catch{if(mounted.current)setReconnecting(true);}
    finally{submitting.current=false;if(mounted.current){if(native)setConfirmed(false);setPending(false);refresh();}}
  }
  async function upload(event){
    const file=event.target.files?.[0];if(!file)return;
    if(!file.size||file.size>160*1024*1024){setError('请选择 160 MiB 以内的软件更新包');event.target.value='';return;}
    if(submitting.current)return;
    submitting.current=true;revision.current++;setPending(true);setError('');
    try{const response=await fetch('/api/updates/upload',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF':csrf},body:file});
      const result=await response.json();if(!result.ok)throw new Error(result.error||'上传未完成');if(mounted.current)merge(result);
    }catch(reason){if(mounted.current)setError(reason.message||'上传中断，请重新选择文件');}
    finally{event.target.value='';submitting.current=false;if(mounted.current){setPending(false);refresh();}}
  }
  const unavailable=pending||reconnecting||!!readError;
  const actions=updateActions(data,unavailable),job=data?.job,native=data?.native,nativeOps=nativeActions(data,unavailable,confirmed);
  const online=data?.online_update===true;
  const offer=updateOffer(data);
  const manual=html`${data?.staged?html`<p>缓存版本 ${data.staged.version} · ${data.ready?'已验证':'待验证'}</p>`:null}
  <div class="update-actions">
    <button type="button" data-update="upload" disabled=${!actions.upload} onClick=${()=>input.current?.click()}>上传离线包</button>
    <input ref=${input} type="file" aria-label="软件更新包" accept=".tar" style="display:none" disabled=${!actions.upload} onChange=${upload} />
    <button type="button" data-update="validate" disabled=${!actions.validate} onClick=${()=>operate('validate')}>重新验证</button>
    <button type="button" class=${online?'':'primary'} data-update="install" disabled=${!actions.install} onClick=${()=>operate('install')}>${online?'安装已验证的软件包':'立即更新'}</button>
  </div>`;
  return html`<section class="board update-settings" id="software-updates" tabindex="-1" data-updates aria-label="软件更新">
    <h2>软件更新</h2>
    <h3>① 网页 / 语音软件</h3>
    <p>当前网页 / 语音版本：<strong data-update-installed>${data?.installed||'尚未登记'}</strong> · 包含网页控制台、匹配的小助手和本地坐站语音。</p>
    <p>退出运动模型后即可更新，保持站姿或卸力都可以。安装时网页和语音会短暂重启，重连后可查看结果。</p>
    <p role="status" data-update-phase=${job?.phase||'idle'}>${updateMessage(data)}${data?.busy&&typeof job?.percent==='number'?`（${job.percent}%）`:''}</p>
    ${reconnecting?html`<p role="status">网页连接中断，正在重新读取独立更新服务的任务状态…</p>`:null}
    ${data?.busy&&typeof job?.percent==='number'?html`<progress max="100" value=${job.percent} aria-label=${PHASES[job.phase]||'更新进度'} />`:null}
    ${offer?html`<div class="update-offer" data-update-offer>
      <h4>可用版本 ${offer.version}</h4><pre>${offer.changelog||'此版本未提供更新说明。'}</pre>
      ${online?html`<button type="button" class="primary" data-update="online" disabled=${!actions.online} onClick=${()=>operate('online',offer.version)}>立即更新</button>`:null}
    </div>`:null}
    ${data?.available&&!online?html`<p>原版 1104 需按正式发布页说明完成一次性准备并重启，才能在这里一键更新。已有手动更新服务的设备也可继续使用离线包。</p>`:null}
    ${online&&data.catalog_checked_at>0?html`<p>上次检查 · ${new Date(data.catalog_checked_at*1000).toLocaleString('zh-CN')}</p>`:null}
    <div class="update-actions">
      <button type="button" data-update="check" disabled=${!actions.check} onClick=${()=>operate('check')}>检查更新</button>
      ${!online?html`<button type="button" data-update="download" disabled=${!actions.download} onClick=${()=>operate('download')}>下载更新</button>`:null}
      <button type="button" data-update="rollback" disabled=${!actions.rollback} onClick=${()=>operate('rollback')}>退回上一版网页 / 语音</button>
    </div>
    ${online?html`<details data-offline open=${job?.phase==='waiting_validation'}><summary>离线更新与缓存</summary>${manual}</details>`:manual}
    ${pending?html`<p role="status">正在提交请求…</p>`:null}
    ${error||readError?html`<p role="alert">${error||readError} <button type="button" onClick=${()=>{setError('');refresh();}}>重新读取</button></p>`:null}
    ${job?.id?html`<p>任务：${job.id}</p>`:null}
    <hr />
    <div data-native-updates style="scroll-margin-top:72px">
      <h3>② ${native?.bundle_schema?'配套驱动更新':'主程序更新（可选）'}</h3>
      <p>${native?.bundle_schema?'本版手柄映射需要匹配的 padd、robotd 和服务权限。第一步只更新网页 / 语音；这里完成独立的配套驱动安装。':'用于 Qinmo 手动类型选择；现有板卡可继续用自动识别。'}仅支持已知 20261027 基线，其他版本请维护者核对。</p>
      <p role="status" data-native-phase=${native?.phase||'unavailable'}>${nativeMessage(native)}</p>
      <p>实际运行：<strong data-native-identity>${native?.supported?'已核验':'未核验'}</strong>${native?.mode?` · ${native.mode==='motion'?'控制模式':native.mode==='commissioning'?'标定模式':native.mode}`:''}</p>
      ${native?.installed_sha256||native?.transaction_id?html`<details><summary>诊断信息</summary>${native?.installed_sha256?html`<p>robotd SHA256：${native.installed_sha256}</p>`:null}${native?.components?.padd?html`<p>padd SHA256：${native.components.padd.installed_sha256||'未核验'}</p>`:null}${native?.transaction_id?html`<p>事务：${native.transaction_id}</p>`:null}</details>`:null}
      <p>先扶稳、承托鸭子并卸力，关闭策略、语音、音乐和所有录制 / 标定会话。确认后才会启动独立主程序任务；中途断开网页可重连继续看。第二步保持承托卸力即可。</p>
      <label style="display:flex;align-items:flex-start;gap:8px;font-size:13px;line-height:1.6"><input type="checkbox" data-native-confirm checked=${confirmed} onChange=${event=>setConfirmed(event.target.checked)} disabled=${pending||!native?.service_available||!!data?.busy} /><span>我已扶稳承托鸭子、确认卸力并关闭上述活动；同意安装或恢复${native?.bundle_schema?'配套驱动':'主程序'}。</span></label>
      <div class="update-actions">
        <button type="button" class="primary" data-update="native_install" disabled=${!nativeOps.install} onClick=${()=>operate('native_install')}>确认安装${native?.bundle_schema?'配套驱动':'主程序'}</button>
        <button type="button" data-update="native_recover" disabled=${!nativeOps.recover} onClick=${()=>operate('native_recover')}>恢复未完成的${native?.bundle_schema?'配套驱动':'主程序'}更新</button>
        ${native?.bundle_schema?html`<button type="button" data-update="native_restore" disabled=${!nativeOps.restore} onClick=${()=>operate('native_restore')}>恢复旧配套驱动</button>`:null}
      </div>
      ${native?.request_error?html`<p role="alert">${native.request_error}。以这里的实际状态为准，急停仍可使用。</p>`:null}
      <p>${native?.bundle_schema?'失败时会尝试恢复整套旧驱动和原服务配置。退回旧网页 / 语音前，先在这里恢复旧配套驱动；自定义映射文件会保留，旧语音需等完整恢复后才可启动。':'失败时会尝试恢复旧主程序；网页 / 语音回退单独进行。'}本版整机升级仍待实机验收。</p>
    </div>
  </section>`;
}
