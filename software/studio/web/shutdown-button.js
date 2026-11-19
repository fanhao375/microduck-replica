import {html,useState} from './vendor/preact-htm.module.js';
export function ShutdownButton({api,onNotice}){
 const [busy,setBusy]=useState(false),[scheduled,setScheduled]=useState(false),[message,setMessage]=useState('');
 const shutdown=async()=>{
  if(busy||scheduled||!confirm('确定安全关机？请先放稳或扶住鸭子，关机会卸力。收到确认后等待至少20秒再断电。'))return;
  setBusy(true);setMessage('');
  try{const r=await api('/api/maintenance',{op:'shutdown',confirmed:true},50000);if(!r.ok)throw Error(r.error||'关机未确认');setScheduled(true);const text='关机已安排，请等待至少20秒、确认设备停止运行后再断电。';setMessage(text);onNotice?.(text);}
  catch(e){const text=e.message||'关机未确认，请检查连接';setMessage(text);onNotice?.(text,true);}
  finally{setBusy(false);}
 };
 return html`<span style="display:inline-flex;flex-shrink:0"><button type="button" class="shutdown-button" style="flex-shrink:0;white-space:nowrap;border:1px solid #b8bec8;border-radius:10px;padding:8px 10px;font:inherit;font-size:13px;line-height:20px;background:#f5f6f8;color:#283241;cursor:pointer" disabled=${busy||scheduled} onClick=${shutdown} aria-label="安全关机：卸力并关闭鸭子">${busy?'关机中…':scheduled?'已安排关机':'安全关机'}</button>${message&&!onNotice?html`<span role="status" style="position:fixed;left:16px;right:16px;bottom:20px;z-index:9999;margin:auto;max-width:520px;padding:12px;border-radius:12px;background:#252a32;color:white;white-space:normal">${message}</span>`:null}</span>`;
}
