import { html, useEffect, useState } from './vendor/preact-htm.module.js';
import { voices } from './tts-voices.js';

export function TtsSettings({ api }) {
  const [data,setData]=useState(null), [key,setKey]=useState(''), [clear,setClear]=useState(false);
  const [speaker,setSpeaker]=useState('zh_female_vv_uranus_bigtts'), [enabled,setEnabled]=useState(false);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const [query,setQuery]=useState('');
  const [provider,setProvider]=useState('volcano_agent_plan');
  const [preview,setPreview]=useState(null);
  useEffect(()=>{
    let live=true, loading=false;
    const poll=async()=>{
      if(loading)return;loading=true;
      try {const r=await api('/api/ai',{op:'tts_get'},5000);
        if(live&&r.ok){setData(old=>{if(!old){setSpeaker(r.tts.speaker);setEnabled(r.tts.enabled);setProvider(r.tts.provider||'volcano_agent_plan');}return r.tts;});}
        else if(live&&!r.ok)setError(r.error||'语音设置读取失败');
      } catch {if(live)setError('语音状态连接失败');} finally {loading=false;}
    };
    poll();const timer=setInterval(poll,1500);return()=>{live=false;clearInterval(timer);};
  },[]);
  const run=async(op)=>{
    setBusy(true);setError('');setNotice('');
    try {
      const body=op==='tts_save'?{op,settings:{enabled,speaker,provider,api_key:key,clear_key:clear}}:{op};
      if(op==='tts_preview'||op==='tts_save')setPreview(null);
      const r=await api('/api/ai',body,op==='tts_preview'?60000:10000);
      if(!r.ok){setError(r.error||'语音操作失败');return;}
      if(r.tts)setData(r.tts);
      if(op==='tts_save'){setKey('');setClear(false);setNotice('语音设置已保存');}
      if(op==='tts_test')setNotice('正在请求试听，声音将从鸭子的扬声器播放');
      if(op==='tts_preview'&&r.audio?.mime==='audio/wav'&&r.audio?.data){
        setPreview(`data:audio/wav;base64,${r.audio.data}`);
        setNotice('试听已生成；若没有自动播放，请点击下方播放按钮。');
      }
    } catch {setError('语音操作未完成，请检查连接');} finally {setBusy(false);}
  };
  const providerChanged=!!data&&provider!==(data.provider||'volcano_agent_plan');
  const dirty=!!data&&(providerChanged||speaker!==data.speaker||enabled!==data.enabled||!!key||clear);
  const active=['connecting','playing','stopping'].includes(data?.status?.state);
  const filtered=voices.filter(v=>`${v.name} ${v.id} ${v.category} ${v.language}`.toLowerCase().includes(query.toLowerCase()));
  const states={idle:'待机',connecting:'连接语音服务中',playing:'鸭子正在说话',stopping:'正在停止',done:'播放完成',error:'播放失败'};
  return html`<div class="ai-chat" data-tts-settings>
    <h3>鸭子语音回复</h3>
    <p>文字回复可通过鸭子扬声器流式朗读。语音 Key 独立保存。</p>
    ${error?html`<p role="alert">${error}</p>`:null}
    <fieldset disabled=${busy||!data} style="border:0;padding:0;margin:0;min-width:0">
      <label>语音服务<select aria-label="语音服务" value=${provider} onChange=${e=>{setProvider(e.target.value);setKey('');setClear(false);}}>
        <option value="volcano">豆包语音合成 2.0（Doubao-Seed-TTS-2.0）</option>
        <option value="volcano_agent_plan">火山 Agent Plan</option>
      </select></label>
      <p>${provider==='volcano_agent_plan'?'使用火山方舟 Agent Plan 专属 API Key 和套餐额度。':'使用豆包语音控制台的语音 API Key，并开通语音合成 2.0；请勿填写方舟文字模型 Key 或 Agent Plan Key。'}${providerChanged?'切换服务需重新填写对应 Key，原 Key 不会沿用。':''}</p>
      ${provider==='volcano'?html`<p><a href="https://console.volcengine.com/ark/region:cn-beijing/model/detail?name=doubao-seed-tts-2-0" target="_blank" rel="noopener noreferrer">模型介绍</a> · <a href="https://docs.volcengine.com/docs/doubao-voice/unidirectional-streaming-text-to-speech-http?lang=zh" target="_blank" rel="noopener noreferrer">官方接入文档</a> · <a href="https://console.volcengine.com/speech/new/setting/apikeys" target="_blank" rel="noopener noreferrer">语音 API Key 管理</a></p>`:null}
      <label>语音 API Key<input aria-label="语音 API Key" type="password" maxlength="4096" autocomplete="new-password" value=${key} onInput=${e=>setKey(e.target.value)} placeholder=${data?.key_configured&&!providerChanged?'已保存，留空保留':'填写所选语音服务的 API Key'} /></label>
      <label class="ai-check"><input type="checkbox" checked=${clear} onChange=${e=>setClear(e.target.checked)} />清除已保存的语音 Key</label>
      <label>搜索音色<input aria-label="搜索音色" value=${query} onInput=${e=>setQuery(e.target.value)} placeholder="名称、语言或音色 ID" /></label>
      <label>选择音色<select aria-label="选择音色" value=${speaker} onChange=${e=>setSpeaker(e.target.value)}>
        ${!filtered.some(v=>v.id===speaker)?html`<option value=${speaker}>${voices.find(v=>v.id===speaker)?.name||speaker}（当前）</option>`:null}
        ${filtered.map(v=>html`<option key=${v.id} value=${v.id}>${v.name} · ${v.category} · ${v.language}</option>`)}
      </select></label>
      <label>音色 ID<input aria-label="音色 ID" value=${speaker} maxlength="160" onInput=${e=>setSpeaker(e.target.value)} /></label>
      <label class="ai-check"><input type="checkbox" checked=${enabled} onChange=${e=>setEnabled(e.target.checked)} />自动朗读 AI 回复</label>
      <div class="ai-actions"><button class="btn primary" type="button" disabled=${!dirty} onClick=${()=>run('tts_save')}>保存语音设置</button>
      <button class="btn" type="button" disabled=${dirty||!data?.key_configured||active} onClick=${()=>run('tts_test')}>鸭子试听</button>
      <button class="btn" type="button" disabled=${dirty||!data?.key_configured} onClick=${()=>run('tts_preview')}>浏览器试听</button></div>
      <p>浏览器试听从当前电脑或手机播放，不需要连接鸭子声卡。每次点击会发送固定示例文字并调用所选语音服务，可能消耗额度；重复播放已生成音频不再请求云端。</p>
    </fieldset>
    ${preview?html`<audio key=${preview} aria-label="浏览器试听播放器" controls autoplay src=${preview} style="width:100%"></audio>`:null}
    <button class="btn" type="button" disabled=${busy||!active} onClick=${()=>run('tts_stop')}>停止播放</button>
    <p role="status">${states[data?.status?.state]||'读取语音设置中…'}${notice?' · '+notice:''}</p>
    ${data?.status?.error?html`<p role="alert">${data.status.error}</p>`:null}
    <p>内置 ${voices.length} 个官方 TTS 2.0 音色（目录快照 2026-11-12），可用性以套餐授权为准；也可手填音色 ID。更换音色后先保存再试听。当前仍通过文字输入提问。</p>
  </div>`;
}
