import {html,useEffect,useState} from './vendor/preact-htm.module.js';
export function Maintenance({api}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[archive,setArchive]=useState(''),[preview,setPreview]=useState(null),[password,setPassword]=useState('');
 const receiptKey='duck.maintenance.restore';
 const remember=id=>{try{sessionStorage.setItem(receiptKey,id);}catch{}};
 const forget=()=>{try{sessionStorage.removeItem(receiptKey);}catch{}};
 const recover=async(id,active=()=>true)=>{
   setNote('正在核对恢复结果…');
   for(let attempt=0;attempt<60 && active();attempt++){
     try{
       const result=await api('/api/maintenance?restore='+id,undefined,3000);
       if(!active())return;
       if(result.status===401){setError('请重新登录后查看恢复结果');return;}
       if(result.id===id && result.phase==='completed'){
         forget();setPreview(null);setArchive('');setNote('已恢复 '+result.restored+' 份配置。恢复前备份：'+result.backup);return;
       }
       if(result.id===id && result.phase==='failed'){
         forget();setError(result.error||'恢复失败，请查看维护日志');setNote('');return;
       }
     }catch{}
     await new Promise(resolve=>setTimeout(resolve,1000));
   }
   if(active())setNote('设备尚未确认恢复结果。重新连接后打开本页可继续核对。');
 };
 useEffect(()=>{
   let active=true;
   let pending;try{pending=sessionStorage.getItem(receiptKey);}catch{}
   if(pending && /^[a-f0-9]{32}$/.test(pending)){
     setBusy(true);recover(pending,()=>active).finally(()=>{if(active)setBusy(false);});
   }
   return ()=>{active=false;};
 },[]);
 const run=async(body)=>{const r=await api('/api/maintenance',body,50000);if(!r.ok)throw Error(r.error||'操作失败');return r;};
 const download=async()=>{setBusy(true);setError('');try{const r=await fetch('/api/maintenance',{credentials:'same-origin'});if(!r.ok){const d=await r.json();throw Error(d.error||'导出失败');}const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='microduck-backup-'+new Date().toISOString().replace(/[:.]/g,'-')+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);setNote('备份已生成，请确认浏览器已保存文件');}catch(e){setError(e.message);}finally{setBusy(false);}};
 const upload=async(file)=>{setPassword('');setPreview(null);setArchive('');if(!file)return;setBusy(true);setError('');try{if(file.size>16*1024*1024)throw Error('备份文件不能超过16MB');const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const b64=btoa(binary);const r=await run({op:'preview',archive:b64});setArchive(b64);setPreview(r);setNote('校验通过，请核对后恢复');}catch(e){setError(e.message);}finally{setBusy(false);}};
 const restore=async()=>{
   if(!password||!confirm('恢复所列配置？请先扶稳鸭子并卸力。系统会备份当前配置，再恢复并重启相关服务。'))return;
   const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
   setPassword('');setBusy(true);setError('');remember(id);
   try{
     try{
       const r=await api('/api/maintenance',{op:'restore',archive,confirmed:true,password,request_id:id},50000);
       if(!r.ok && r.status>=400 && r.status<500){forget();setError(r.error||'恢复请求被拒绝');return;}
     }catch{/* Studio may have restarted before the browser received its reply. */}
     await recover(id);
   }finally{setBusy(false);}
 };
 const shutdown=async()=>{if(!confirm('确定安全关机？请先将鸭子放稳或扶住，关机会卸力。收到确认后请等待至少20秒再断电。'))return;setBusy(true);setError('');try{await run({op:'shutdown',confirmed:true});setNote('关机已安排，页面断开属于正常现象。请等待至少20秒、确认设备已停止运行后再切断电源。');}catch(e){setError(e instanceof TypeError||e.name==='AbortError'?'连接中断，未收到关机回执。请确认系统已停止后再断电。':e.message);}finally{setBusy(false);}};
 return html`<section class="m-card m-pad" style="padding:18px;margin:16px 0;text-align:left;overflow-wrap:anywhere" data-maintenance><h3>关机与备份</h3>
 <p>关闭网页不会关闭鸭子。断电前请使用安全关机，并等待系统完成关机。</p>
 <div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="m-btn btn" disabled=${busy} onClick=${shutdown}>鸭子安全关机</button><button type="button" class="m-btn btn" disabled=${busy} onClick=${download}>导出备份</button></div>
 <p>备份包含舵机/IMU标定、手柄映射、已保存的连接设置、相机设备树及 ToF 启动配置，并附当前程序源码供离线救援。包含 API Key，请妥善保管，勿公开分享。登录密码和浏览器会话不在备份内。</p>
 <label>导入备份文件<input style="display:block;width:100%;max-width:100%" aria-label="导入备份文件" type="file" accept=".zip" disabled=${busy} onChange=${e=>upload(e.target.files?.[0])}/></label>
 ${preview?html`<div><p>来源：${preview.hostname} · ${preview.created}</p><details><summary>将恢复 ${preview.files.length} 份配置</summary><ul>${preview.files.map(n=>html`<li>${n.replace(/^config\//,'/')}</li>`)}</ul></details><p>导入只恢复配置；附带源码不由网页执行或覆盖。${preview.skipped&&preview.skipped.length?'相机设备树只导出，不通过网页恢复，如需恢复请手动处理。':''}</p><label>恢复前请再输入网页登录密码<input type="password" autocomplete="current-password" value=${password} onInput=${e=>setPassword(e.target.value)} /></label><button type="button" class="m-btn btn" disabled=${busy||!password} onClick=${restore}>确认恢复配置</button></div>`:null}
 ${busy?html`<p role="status">正在处理，请勿断电…</p>`:null}${note?html`<p role="status">${note}</p>`:null}${error?html`<p role="alert">${error}</p>`:null}</section>`;
}
