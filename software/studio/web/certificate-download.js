import {html,useState} from './vendor/preact-htm.module.js';
export async function downloadCa(){
 const response=await fetch('/ca.crt',{credentials:'same-origin',cache:'no-store'});
 if(!response.ok)throw Error(response.status===404?'设备没有生成CA证书':'证书读取失败（HTTP '+response.status+'）');
 const data=await response.arrayBuffer();
 if(data.byteLength<100||data.byteLength>65536)throw Error('证书文件大小异常，请勿安装');
 const blob=new Blob([data],{type:'application/octet-stream'}),url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download='Microduck-Local-CA.crt';a.style.display='none';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
 return data.byteLength;
}
export function CertificateDownload(){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const click=async()=>{setBusy(true);setError('');setMessage('');try{await downloadCa();setMessage('证书已读取，请在浏览器下载列表中查看 Microduck-Local-CA.crt。');}catch(e){setError(e.message||'下载未完成，请使用右侧单独打开入口。');}finally{setBusy(false);}};
 return html`<div data-certificate-download style="margin:12px 0"><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><button type="button" class="m-btn btn" disabled=${busy} onClick=${click}>${busy?'读取证书中…':'下载 HTTPS 证书'}</button><a href="/ca.crt" target="_blank" rel="noopener">单独打开证书</a></div>${message?html`<p role="status">${message}</p>`:null}${error?html`<p role="alert">${error}</p>`:null}</div>`;
}
