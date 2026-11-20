import {html,useEffect,useRef,useState} from './vendor/preact-htm.module.js';
import {updateMessage,updateActions,PHASES,nativeMessage,nativeActions} from './updates-ui-state.js';

const CSS=`.update-settings{padding:18px 20px;min-width:0;overflow-wrap:anywhere}.update-settings h2{font-size:20px;margin:0 0 8px}.update-settings p{font-size:13px;line-height:1.65;color:var(--text-2);margin:8px 0}.update-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.update-settings button{border:0;border-radius:999px;padding:9px 14px;min-height:38px;font:inherit;font-size:14px;background:#e9e9ee;color:#242428;cursor:pointer}.update-settings button.primary{background:#007aff;color:white}.update-settings button:disabled{opacity:.4;cursor:default}.update-settings button:focus-visible{outline:2px solid #007aff;outline-offset:2px}.update-settings [role=alert]{color:var(--red,#b42318)}.update-settings pre{white-space:pre-wrap;font:inherit;font-size:13px;line-height:1.6;max-height:200px;overflow:auto}.update-settings input{max-width:100%;font-size:13px}.update-settings progress{width:100%}@media(max-width:480px){.update-settings{padding:16px 12px}.update-settings button{max-width:100%;white-space:normal}}`;
if(!document.getElementById('updates-css')){const style=document.createElement('style');style.id='updates-css';style.textContent=CSS;document.head.appendChild(style);}

export function UpdateSettings({api,csrf}){
  const [data,setData]=useState(null),[pending,setPending]=useState(false),[error,setError]=useState(''),[reconnecting,setReconnecting]=useState(false);
  const [confirmed,setConfirmed]=useState(false);
  const mounted=useRef(true),loading=useRef(false),input=useRef(null);
  const merge=result=>setData(previous=>({...previous,...result,native:result.native??previous?.native}));
  async function refresh(){
    if(loading.current)return;loading.current=true;
    try{const result=await api('/api/updates');if(!mounted.current)return;
      if(!result.ok){setError(result.error||'暂时无法读取更新状态');return;}
      merge(result);setReconnecting(false);
    }catch{if(mounted.current)setReconnecting(true);}finally{loading.current=false;}
  }
  useEffect(()=>{mounted.current=true;refresh();const timer=setInterval(refresh,2000);return()=>{mounted.current=false;clearInterval(timer);};},[]);
  async function operate(op){
    if(pending)return;setPending(true);setError('');
    const native=op==='native_install'||op==='native_recover'||op==='native_restore';
    if(native&&!confirmed){setPending(false);return;}
    try{const result=await api('/api/updates',native?{op,confirmed:true}:{op},20000);if(!mounted.current)return;
      if(!result.ok)throw new Error(result.error||'更新请求未完成');merge(result);
    }catch(reason){if(mounted.current)setError(reason.message||'连接中断，正在重新读取任务状态');}
    finally{if(mounted.current){if(native)setConfirmed(false);setPending(false);refresh();}}
  }
  async function upload(event){
    const file=event.target.files?.[0];if(!file)return;
    if(!file.size||file.size>160*1024*1024){setError('请选择 160 MiB 以内的软件更新包');event.target.value='';return;}
    setPending(true);setError('');
    try{const response=await fetch('/api/updates/upload',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF':csrf},body:file});
      const result=await response.json();if(!result.ok)throw new Error(result.error||'上传未完成');if(mounted.current)merge(result);
    }catch(reason){if(mounted.current)setError(reason.message||'上传中断，请重新选择文件');}
    finally{event.target.value='';if(mounted.current){setPending(false);refresh();}}
  }
  const actions=updateActions(data,pending),job=data?.job,native=data?.native,nativeOps=nativeActions(data,pending,confirmed);
  return html`<section class="board update-settings" data-updates aria-label="软件更新">
    <h2>软件更新</h2>
    <h3>① 网页 / 语音软件</h3>
    <p>当前网页 / 语音版本：<strong data-update-installed>${data?.installed||'尚未登记'}</strong> · 包含网页控制台、匹配的小助手和本地坐站语音。</p>
    <p>退出运动模型后即可更新，保持站姿或卸力都可以。安装时网页和语音服务会短暂重启。</p>
    <p>安装会短暂重启网页和语音服务。页面会自动重连并读取保存的任务结果。</p>
    ${!data&&!error?html`<p role="status">读取更新服务…</p>`:null}
    <p role="status" data-update-phase=${job?.phase||'idle'}>${updateMessage(data)}${typeof job?.percent==='number'?`（${job.percent}%）`:''}</p>
    ${reconnecting?html`<p role="status">网页连接中断，正在重新读取独立更新服务的任务状态…</p>`:null}
    ${data?.busy&&typeof job?.percent==='number'?html`<progress max="100" value=${job.percent} aria-label=${PHASES[job.phase]||'更新进度'} />`:null}
    ${data?.candidate?html`<p>发布版本：<strong>${data.candidate.version}</strong>（下载验证前为发布源提供的信息）</p><pre>${data.candidate.changelog||'此发布未填写更新说明。'}</pre>`:null}
    ${data?.staged?html`<p>本机缓存：${data.staged.version} · ${data.ready?'验证通过':'尚未通过当前安装条件验证'}</p>`:null}
    <div class="update-actions">
      <button type="button" data-update="check" disabled=${!actions.check} onClick=${()=>operate('check')}>检查更新</button>
      <button type="button" data-update="download" disabled=${!actions.download} onClick=${()=>operate('download')}>下载更新</button>
      <button type="button" data-update="upload" disabled=${!actions.upload} onClick=${()=>input.current?.click()}>上传离线包</button>
      <input ref=${input} type="file" aria-label="软件更新包" accept=".tar" style="display:none" disabled=${!actions.upload} onChange=${upload} />
      <button type="button" data-update="validate" disabled=${!actions.validate} onClick=${()=>operate('validate')}>重新验证</button>
      <button type="button" class="primary" data-update="install" disabled=${!actions.install} onClick=${()=>operate('install')}>立即更新</button>
      <button type="button" data-update="rollback" disabled=${!actions.rollback} onClick=${()=>operate('rollback')}>退回上一版网页 / 语音</button>
    </div>
    ${pending?html`<p role="status">正在提交请求…</p>`:null}
    ${error?html`<p role="alert">${error} <button type="button" onClick=${()=>{setError('');refresh();}}>重新读取</button></p>`:null}
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
