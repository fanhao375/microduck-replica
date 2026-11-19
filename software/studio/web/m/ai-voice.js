import {AiCameraPreview} from '../ai-camera-preview.js';

import {html,useEffect,useRef,useState} from '../vendor/preact-htm.module.js';

import {api} from './link.js';

export function useAiVoice(){

 const [enabled,setEnabled]=useState(true),[data,setData]=useState(null),[selected,setSelected]=useState('');

 const [messages,setMessages]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState('');

 const [status,setStatus]=useState(null);const pending=useRef(false),alive=useRef(true),history=useRef([]),generation=useRef(0);

 useEffect(()=>{alive.current=true;let polling=false;

  const load=async()=>{try{

   let r=await api('/api/ai',undefined,5000);if(!alive.current)return;

   if(!r.ok){setError(r.error||'请先登录并配置 AI 连接');return;}

   const chosen=r.connections.find(c=>c.id===r.current_id&&c.ready)||r.connections.find(c=>c.ready);

   if(chosen&&chosen.id!==r.current_id){

    const saved=await api('/api/ai',{op:'select',id:chosen.id},5000);if(!alive.current)return;

    if(saved.ok)r=saved;else setError(saved.error||'默认模型未能保存，本次仍可使用');

   }

   if(alive.current){setData(r);setSelected(chosen?.id||'');}

  }catch{if(alive.current)setError('AI 设置读取失败');}};

  const poll=async()=>{if(polling)return;polling=true;try{const r=await api('/api/ai',{op:'tts_get'},5000);if(alive.current&&r.ok)setStatus(r);}catch{}finally{polling=false;}};

  load();poll();const id=setInterval(poll,1800);return()=>{alive.current=false;generation.current++;clearInterval(id);};

 },[]);

 const clear=()=>{generation.current++;history.current=[];setMessages([]);setError('');};

 const send=async(text)=>{

  if(pending.current)return;

  const conn=data?.connections.find(c=>c.id===selected);

  if(!conn?.ready){setError('先选择已配置好的模型连接，或打开 AI 设置补齐配置');return;}

  const content=text.trim();if(!content||content.length>2000){setError('每次请说或输入1–2000字');return;}

  const msgs=[...history.current.slice(-8),{role:'user',content}];

  if(msgs.reduce((n,m)=>n+m.content.length,0)>12000){setError('对话过长，请清空后重试');return;}

  pending.current=true;setBusy(true);setError('');setMessages(msgs);const gen=generation.current;

  try{const r=await api('/api/ai',{op:'chat',id:selected,revision:conn.revision,messages:msgs.map(({role,content})=>({role,content}))},90000);

   if(!alive.current||gen!==generation.current)return;

   if(!r.ok){setError(r.error||'AI 回复失败');return;}

   history.current=[...msgs,{role:'assistant',content:r.text,...(r.camera_image?{camera_image:r.camera_image}:{})}].slice(-10);setMessages(history.current);

   if(r.speech?.error||r.action?.error)setError(r.speech?.error||r.action?.error);

  }catch{if(alive.current&&gen===generation.current)setError('AI 请求超时或连接中断，请重试');}

  finally{pending.current=false;if(alive.current)setBusy(false);}

 };

 const stop=async()=>{clear();try{const r=await api('/api/ai',{op:'tools_stop'},10000);if(!r.ok)setError(r.error||'停止未确认');}catch{setError('停止未确认，请使用顶部卸力');}};

 const toggle=()=>{clear();setEnabled(!enabled);};

 const state=status?.tts?.status;const action=status?.tools;

 const panel=html`<section class="m-card m-pad" style="text-align:left;margin:14px 0" data-mobile-ai>

 <div class="m-seg" role="group" aria-label="识别用途"><button aria-pressed=${enabled} disabled=${busy} onClick=${()=>{if(!enabled)toggle();}}>AI 对话</button><button aria-pressed=${!enabled} disabled=${busy} onClick=${()=>{if(enabled)toggle();}}>固定口令</button></div>

 ${enabled?html`

 <${AiCameraPreview} api=${api} portrait=${true} />

 ${busy?html`<p role="status">鸭子正在思考…</p>`:null}

 <div aria-live="polite">${messages.map((m,i)=>html`<div key=${i} style="white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0"><b>${m.role==='user'?'你':'鸭子'}：</b>${m.camera_image?html`<img src=${m.camera_image} alt="本次传给模型的摄像头画面" style="display:block;width:25%;max-width:100%;height:auto;object-fit:contain;border-radius:8px;margin:8px 0" />`:null}${m.content}</div>`)}</div>

 ${state?.error||action?.error?html`<p role="alert">${state?.error||action?.error}</p>`:null}

 <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="m-btn" disabled=${busy} onClick=${clear}>清空对话</button><button class="m-btn" onClick=${stop}>停止 AI 动作与语音</button></div>`:null}

 ${error?html`<p role="alert">${error}</p>`:null}

 </section>`;

 return {enabled,busy,send,panel};

}
