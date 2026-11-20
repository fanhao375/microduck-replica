import {requestMessages} from '../ai-ui-state.js';
import {AiCameraPreview} from '../ai-camera-preview.js';

import {html,useEffect,useRef,useState} from '../vendor/preact-htm.module.js';

import {api} from './link.js';

export function useAiVoice(){

 const [enabled,setEnabled]=useState(false),[data,setData]=useState(null),[selected,setSelected]=useState('');

 const [allowCamera,setAllowCamera]=useState(false);
 const [messages,setMessages]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState('');

 const connection=useRef(null);
 const [status,setStatus]=useState(null);const pending=useRef(false),alive=useRef(true),history=useRef([]),generation=useRef(0);

 const clear=()=>{generation.current++;history.current=[];setAllowCamera(false);setMessages([]);setError('');};
 const choose=(item)=>{
  clear();
  connection.current={id:item?.id||'',revision:item?.revision};
  setSelected(connection.current.id);
 };
 useEffect(()=>{
  if(!enabled)return;
  let live=true,polling=false;
  alive.current=true;
  const poll=async()=>{
   if(polling)return;
   polling=true;
   try{
    const r=await api('/api/ai',undefined,5000);
    if(!live)return;
    if(!r.ok){setError(r.error||'请先登录并配置 AI 连接');return;}
    setData(r);setStatus(r);
    const current=connection.current;
    const next=r.connections.find(c=>c.ready&&c.id===(current ? current.id : r.current_id));
    if(!current || current.id!==(next?.id||'') || current.revision!==next?.revision) choose(next);
   }catch{if(live)setError('AI 设置读取失败');}
   finally{polling=false;}
  };
  poll();const id=setInterval(poll,1800);
  return()=>{live=false;alive.current=false;generation.current++;clearInterval(id);};
 },[enabled]);

 const send=async(text)=>{

  if(pending.current)return;

  const conn=data?.connections.find(c=>c.id===selected);

  if(!conn?.ready){setError('先选择已配置好的模型连接，或打开 AI 设置补齐配置');return;}

  const content=text.trim();if(!content||content.length>2000){setError('每次请说或输入1–2000字');return;}

  const msgs=requestMessages(history.current,content);

  setAllowCamera(false);pending.current=true;setBusy(true);setError('');setMessages(msgs);const gen=generation.current;

  try{const r=await api('/api/ai',{op:'chat',id:selected,revision:conn.revision,allow_camera:allowCamera,messages:msgs.map(({role,content})=>({role,content}))},allowCamera?110000:65000);

   if(!alive.current||gen!==generation.current)return;

   if(!r.ok){setError(r.error||'AI 回复失败');return;}

   history.current=[...msgs,{role:'assistant',content:r.text,...(r.camera_image?{camera_image:r.camera_image}:{})}].slice(-10);setMessages(history.current);

   if(r.speech?.error||r.action?.error)setError(r.speech?.error||r.action?.error);

  }catch{if(alive.current&&gen===generation.current)setError('AI 请求超时或连接中断，请重试');}

  finally{pending.current=false;if(alive.current)setBusy(false);}

 };

 const stop=async()=>{clear();try{const r=await api('/api/ai',{op:'tools_stop'},10000);if(!r.ok)setError(r.error||'停止未确认');}catch{setError('停止未确认，请使用顶部卸力');}};

 const toggle=()=>{clear();connection.current=null;setSelected('');setData(null);setEnabled(!enabled);};

 const state=status?.tts?.status;const action=status?.tools;

 const panel=html`<section class="m-card m-pad" style="text-align:left;margin:14px 0" data-mobile-ai>

 <div class="m-seg" role="group" aria-label="识别用途"><button aria-pressed=${enabled} disabled=${busy} onClick=${()=>{if(!enabled)toggle();}}>AI 对话</button><button aria-pressed=${!enabled} disabled=${busy} onClick=${()=>{if(enabled)toggle();}}>固定口令</button></div>

 ${enabled?html`

 <label>模型连接<select aria-label="模型连接" value=${selected} disabled=${busy} onChange=${e=>choose(data.connections.find(c=>c.id===e.currentTarget.value))}>
  <option value="">选择已配置的模型</option>${data?.connections.filter(c=>c.ready).map(c=>html`<option value=${c.id}>${c.name}</option>`)}
 </select></label>
 <p>手机录音在鸭子上识别。发送后，文字会交给所选模型。</p>
 <label><input type="checkbox" checked=${allowCamera} disabled=${busy||!selected} onChange=${e=>setAllowCamera(e.currentTarget.checked)}/>允许本次对话向所选模型发送摄像头画面</label>
 ${allowCamera?html`<${AiCameraPreview} api=${api} portrait=${true} />`:null}

 ${busy?html`<p role="status">鸭子正在思考…</p>`:null}

 <div aria-live="polite">${messages.map((m,i)=>html`<div key=${i} style="white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0"><b>${m.role==='user'?'你':'鸭子'}：</b>${m.camera_image?html`<img src=${m.camera_image} alt="本次传给模型的摄像头画面" style="display:block;width:25%;max-width:100%;height:auto;object-fit:contain;border-radius:8px;margin:8px 0" />`:null}${m.content}</div>`)}</div>

 ${state?.error||action?.error?html`<p role="alert">${state?.error||action?.error}</p>`:null}

 <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="m-btn" disabled=${busy} onClick=${clear}>清空对话</button><button class="m-btn" onClick=${stop}>停止 AI 动作与语音</button></div>`:null}

 ${error?html`<p role="alert">${error}</p>`:null}

 </section>`;

 return {enabled,busy,send,panel};

}
