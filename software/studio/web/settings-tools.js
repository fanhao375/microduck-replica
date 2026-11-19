import {html,useEffect,useState} from './vendor/preact-htm.module.js';
export function AiTools({api}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true,pending=false;const poll=async()=>{if(pending)return;pending=true;try{const r=await api('/api/ai',{op:'tools_get'},5000);if(live&&r.ok)setData(r.tools);}catch{}finally{pending=false;}};poll();const id=setInterval(poll,1500);return()=>{live=false;clearInterval(id);};},[]);
 const run=async(body)=>{setBusy(true);setError('');try{const r=await api('/api/ai',body,10000);if(r.ok)setData(r.tools);else setError(r.error||'动作操作失败');}catch{setError('动作控制连接失败');}finally{setBusy(false);}};
 const states={idle:'待机',waiting_speech:'等待语音播放结束',waiting_speech_start:'等待语音开始',preparing:'检查状态并准备动作',executing:'执行中',done:'本组请求处理结束',error:'动作未完成',stopped:'已停止'};
 return html`<div class="ai-chat" data-ai-tools><h3>AI 动作与鸭叫工具</h3>
 <label class="ai-check"><input type="checkbox" aria-label="允许 AI 调用动作与声音" disabled=${busy||!data} checked=${!!data?.enabled} onChange=${e=>run({op:'tools_save',enabled:e.target.checked})}/>允许 AI 调用动作与声音</label>
 <p>开启后，张嘴、摇头和点头可随语音同步进行；行走、转向、坐站、鸭叫及其他技能会在语音播放结束后执行。文字模型需要支持工具调用。</p>
 <p>动作前自动检查状态，按需站起、开启策略、切换走路或轮滑模型；头嘴使用独立控制通道。标定模式下不执行动作。单次行走最多 3 秒，每组最多 3 个工具；手柄卸力和急停仍有效。</p>
 <button class="btn" type="button" disabled=${busy||!data} onClick=${()=>run({op:'tools_stop'})}>停止 AI 动作与语音</button>
 <p role="status">${states[data?.state]||'读取动作状态中…'}</p>
 ${error||data?.error?html`<p role="alert">${error||data.error}</p>`:null}
 ${(data?.results||[]).map((r,i)=>html`<p key=${i}>${r.tool}${r.skill?' · '+r.skill:''}：${r.state==='accepted'?'固件已接受（不代表实际动作完成）':'已结束'}</p>`)}
 </div>`;
}
