import {html,useState} from './vendor/preact-htm.module.js';
export function Maintenance({api}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[archive,setArchive]=useState(''),[preview,setPreview]=useState(null);
 const run=async(body)=>{const r=await api('/api/maintenance',body,50000);if(!r.ok)throw Error(r.error||'操作失败');return r;};
 const download=async()=>{
  if(!confirm('导出的 ZIP 未加密，可能包含 AI 和语音服务的 API Key。确定保存到这台设备？'))return;
  setBusy(true);setError('');setNote('');
  try {
   const r=await fetch('/api/maintenance',{credentials:'same-origin',cache:'no-store'});
   if(!r.ok){const d=await r.json();throw Error(d.error||'导出失败');}
   const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');
   a.href=url;a.download='microduck-backup-'+new Date().toISOString().replace(/[:.]/g,'-')+'.zip';
   document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
   setNote('备份已生成，请确认浏览器已保存文件');
  }catch(e){setError(e.message);}finally{setBusy(false);}
 };
 const upload=async(file)=>{setPreview(null);setArchive('');if(!file)return;setBusy(true);setError('');try{if(file.size>16*1024*1024)throw Error('备份文件不能超过16MB');const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const b64=btoa(binary);const r=await run({op:'preview',archive:b64});setArchive(b64);setPreview(r);setNote('校验通过，请核对后恢复');}catch(e){setError(e.message);}finally{setBusy(false);}};
 const restore=async()=>{if(!confirm('恢复所列配置？备份中的 AI 和语音服务设置可能覆盖本机 API Key。请扶稳鸭子并卸力，等服务重启再操作。'))return;setBusy(true);setError('');setNote('');try{const r=await run({op:'restore',archive,confirmed:true});setNote('已写入 '+r.restored+' 份配置，已安排重新加载，请保持卸力。原配置备份：'+r.backup+(r.reboot_required?'。相机设备树需重启鸭子后生效':''));setPreview(null);setArchive('');}catch(e){setError(e.message);}finally{setBusy(false);}};
 const shutdown=async()=>{if(!confirm('确定安全关机？请先将鸭子放稳或扶住，关机会卸力。收到确认后请等待至少20秒再断电。'))return;setBusy(true);setError('');try{await run({op:'shutdown',confirmed:true});setNote('关机已安排，页面断开属于正常现象。请等待至少20秒、确认设备已停止运行后再切断电源。');}catch(e){setError(e.message);}finally{setBusy(false);}};
 return html`<section class="m-card m-pad" style="padding:18px;text-align:left;overflow-wrap:anywhere" data-maintenance><h3>关机与备份</h3>
 <p>关闭网页不会关闭鸭子。断电前请使用安全关机，并等待系统完成关机。</p>
 <div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" class="m-btn btn" disabled=${busy} onClick=${shutdown}>鸭子安全关机</button><button type="button" class="m-btn btn" disabled=${busy} onClick=${download}>导出备份</button></div>
 <p>备份保存本机已有的舵机和 IMU 标定、手柄映射、AI 与语音设置、相机及 ToF 配置。ZIP 未加密，可能含 API Key。请私下保管，只上传可信来源的备份。网页登录密码和会话保留在本机。</p>
 <label>导入备份文件<input style="display:block;width:100%;max-width:100%" aria-label="导入备份文件" type="file" accept=".zip" disabled=${busy} onChange=${e=>upload(e.target.files?.[0])}/></label>
 ${preview?html`<div><p>来源：${preview.hostname} · ${preview.created}</p><details><summary>将恢复 ${preview.files.length} 份配置</summary><ul>${preview.files.map(n=>html`<li>${n.replace(/^config\//,'/')}</li>`)}</ul></details><p>导入只恢复配置；附带源码不由网页执行或覆盖。${preview.reboot_required?'相机设备树将在下次重启后生效。':''}</p><button type="button" class="m-btn btn" disabled=${busy} onClick=${restore}>确认恢复配置</button></div>`:null}
 ${busy?html`<p role="status">正在处理，请勿断电…</p>`:null}${note?html`<p role="status">${note}</p>`:null}${error?html`<p role="alert">${error}</p>`:null}</section>`;
}
