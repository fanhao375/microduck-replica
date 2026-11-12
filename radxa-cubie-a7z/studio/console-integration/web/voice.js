import { html, useEffect, useRef, useState } from './vendor/preact-htm.module.js';
import { HoldToTalk, voiceConnection } from './voice-session.js';
import { LocalSpeechCapture } from './local-speech.js';

export function VoicePage({st, live, open, link}) {
  const [view,setView]=useState({state:'idle',message:'按住说话，松开让鸭哥回应',text:''});
  const latest=useRef(null), session=useRef(null), pointer=useRef(null), alive=useRef(false);
  const connection=voiceConnection(st,{live,open});
  const available=connection.connected && st?.mode==='motion';
  const supported=!!navigator.mediaDevices?.getUserMedia && !!(window.AudioContext || window.webkitAudioContext) && window.isSecureContext;
  latest.current={available,supported,link};
  useEffect(()=>{
    alive.current=true;
    const talk=new HoldToTalk({createRecognition:()=>new LocalSpeechCapture({
      recognize:pcm=>link.cmd('voice.recognize',{pcm},18000)}),
      ready:()=>latest.current.available && latest.current.supported && !document.hidden,
      send:text=>link.cmd('pad.op',{op:'voice',text,seq:++link.seq},4500),
      report:next=>{if(alive.current)setView(next);}});
    session.current=talk;
    const cancel=()=>{pointer.current=null; talk.cancel('这次录音已取消');};
    const hidden=()=>{if(document.hidden)cancel();};
    window.addEventListener('blur',cancel); window.addEventListener('pagehide',cancel);
    document.addEventListener('visibilitychange',hidden);
    return ()=>{alive.current=false; talk.cancel(); window.removeEventListener('blur',cancel);
      window.removeEventListener('pagehide',cancel); document.removeEventListener('visibilitychange',hidden);};
  },[link]);
  useEffect(()=>{if(!available && session.current && ['listening','processing'].includes(session.current.state)) {
    pointer.current=null; session.current.cancel('连接已断开，这次口令已取消',true);
  }},[available]);
  const busy=view.state==='processing', listening=view.state==='listening';
  const begin=event=>{
    if(event.button!==0 || pointer.current!==null || busy || !available || !supported)return;
    event.preventDefault(); pointer.current=event.pointerId;
    event.currentTarget.setPointerCapture(event.pointerId); session.current.begin();
  };
  const release=event=>{
    if(pointer.current!==event.pointerId)return;
    pointer.current=null; session.current.release();
  };
  const cancel=()=>{if(pointer.current!==null){pointer.current=null;session.current.cancel();}};
  const stop=async()=>{
    pointer.current=null; session.current.cancel('正在停止行走…');
    const generation=session.current.generation;
    session.current.update('processing','正在停止行走…');
    const response=await link.cmd('pad.op',{op:'voice',text:'停下',seq:++link.seq},4500);
    if(alive.current && generation===session.current.generation)session.current.update(response.ok?'done':'error',
      response.ok?'已请求停止行走':response.reason || response.error);
  };
  const testSound=async()=>{
    pointer.current=null; session.current.cancel('正在让鸭哥叫一声…');
    const generation=session.current.generation;
    session.current.update('processing','正在让鸭哥叫一声…');
    const response=await link.cmd('pad.op',{op:'voice',text:'鸭哥',seq:++link.seq},4500);
    if(alive.current && generation===session.current.generation)session.current.update(response.ok?'done':'error',
      response.ok?'已请求鸭哥叫一声':response.reason || response.error);
  };
  let notice=connection.notice || (st?.mode!=='motion'?'请先让鸭哥准备好，再试一次':null);
  if(!notice && !window.isSecureContext)notice='这个页面还不能使用麦克风，请在电脑上打开，或使用安全链接';
  else if(!notice && !navigator.mediaDevices?.getUserMedia)notice='这个浏览器暂时不能收音，请换个浏览器试试';
  if(!notice && view.state==='idle')notice='先扶稳鸭哥，再按住说动作口令，松开发送';
  return html`<section class="vc-page" aria-labelledby="voice-title" data-state=${view.state}>
    <header class="vc-heading"><div><h1 id="voice-title">鸭哥，听我说</h1><p>叫声、坐站、走一小步，按住说话就能控制。</p></div>
      <span class="vc-connection" data-online=${connection.connected}><i></i>${connection.label}</span></header>
    <div class="vc-stage">
      <div class="vc-orbit" data-active=${available && supported}>
        <button type="button" class="vc-talk" aria-label="按住说话，松开发送" aria-pressed=${listening}
          disabled=${!available || !supported || busy} onPointerDown=${begin} onPointerUp=${release}
          onPointerCancel=${cancel} onLostPointerCapture=${cancel} onContextMenu=${event=>event.preventDefault()}
          onKeyDown=${event=>{if(event.code==='Enter'&&!event.repeat){event.preventDefault();session.current.begin();}}}
          onKeyUp=${event=>{if(event.code==='Enter'){event.preventDefault();session.current.release();}}}>
          <svg width="48" height="58" viewBox="0 0 48 58" aria-hidden="true" fill="none"><rect x="15" y="3" width="18" height="32" rx="9" stroke="currentColor" stroke-width="3"/><path d="M7 25v3a17 17 0 0 0 34 0v-3M24 45v9M15 54h18" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>
          <strong>${!connection.connected?'等待鸭哥':listening?'松开发送':busy?'正在处理':'按住说话'}</strong>
          <span>${!connection.connected?'连接后就能说话':listening?'我在听':busy?'稍等一下':'按住 · 说话 · 松开'}</span>
        </button>
      </div>
      <p class="vc-status" role="status" aria-live="polite">${notice || view.message}</p>
      <div class="vc-transcript" aria-label="识别到的话">${view.text?html`<span>听到你说</span><p>“${view.text}”</p>`:html`<p>一次说一个动作，松开后发送。</p>`}</div>
      <button type="button" class="vc-test" disabled=${!available || busy || listening} onClick=${testSound}>试叫一声 <span>听叫声，看鸭嘴</span></button>
      <button type="button" class="vc-stop" disabled=${!available} onClick=${stop}><span aria-hidden="true">■</span> 停下行走</button>
    </div>
    <footer class="vc-footer"><p class="vc-examples">试试说 <b>“鸭哥”</b><b>“站起来”</b><b>“开始走”</b><b>“坐下”</b></p>
      <details><summary>更多口令与使用方式</summary><p class="vc-examples"><b>“前进”</b><b>“后退”</b><b>“左转”</b><b>“右转”</b><b>“停下”</b><b>“翻个跟头”</b></p><p>先扶稳鸭哥，说“站起来”，站稳后说一次“开始走”，再发移动、坐下或翻跟头口令；也可在<a href="#/remote/control">遥控页</a>开启。移动一小步后自动停下，单次技能按原动作结束。</p>
      <p class="vc-privacy">只在按住时收音，松开后由鸭哥识别。网页不保存录音。按住 Enter 也可以说话。</p></details></footer>
  </section>`;
}
