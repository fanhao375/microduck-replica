// The browser buffers words; only releasing the button may submit a command.
export class HoldToTalk {
  constructor({createRecognition, send, ready, report, timers=globalThis}) {
    Object.assign(this, {createRecognition, send, ready, report, timers});
    this.generation=0; this.recognition=null; this.state='idle'; this.text='';
  }
  update(state, message) { this.state=state; this.report({state, message, text:this.text}); }
  begin() {
    if (!this.ready() || !['idle','done','error'].includes(this.state)) return;
    const generation=++this.generation;
    this.text=''; this.released=false; this.ended=false;
    const parts=new Map();
    const recognition=this.createRecognition(); this.recognition=recognition;
    recognition.lang='zh-CN'; recognition.continuous=true;
    recognition.interimResults=true; recognition.maxAlternatives=1;
    const current=()=>generation===this.generation && this.recognition===recognition;
    recognition.onstart=()=>{if(current() && this.released) this.finishCapture();};
    recognition.onresult=event=>{
      if(!current()) return;
      for(let i=event.resultIndex; i<event.results.length; i++) parts.set(i,event.results[i][0].transcript);
      this.text=[...parts.entries()].sort((a,b)=>a[0]-b[0]).map(([,text])=>text).join('').slice(0,80);
      this.update(this.released?'processing':'listening',this.released?'正在识别口令…':'正在听，松开发送');
    };
    recognition.onend=()=>{
      if(!current()) return;
      this.ended=true;
      if(this.released) this.submit(generation);
      else this.update('listening',this.text?'已听到，松开发送':'松开后重新按住说话');
    };
    recognition.onerror=event=>{
      if(!current()) return;
      const copy={'not-allowed':'请允许使用麦克风，再按住说话','audio-capture':'没有找到可用麦克风',
        'network':'语音暂时连不上，请稍后再试或换个网络','no-speech':'没有听清，请再说一次',
        'service-not-allowed':'这里暂时不能语音输入，请换个浏览器试试','language-not-supported':'请用普通话再说一次'};
      this.cancel(event.message || copy[event.error] || '录音中断，请重新按住说话',true);
    };
    this.update('listening','正在听，松开发送');
    this.holdTimer=this.timers.setTimeout(()=>this.cancel('录音超过 12 秒，请重新按住说话',true),12000);
    try {recognition.start();} catch {this.cancel('无法开启麦克风，请检查浏览器权限',true);}
  }
  release() {
    if(this.state!=='listening' || this.released) return;
    this.released=true; this.timers.clearTimeout(this.holdTimer);
    if(!this.ready()) {this.cancel('连接已断开，这次录音已取消',true); return;}
    this.update('processing','正在识别口令…');
    if(this.ended) this.submit(this.generation);
    else {
      this.finishTimer=this.timers.setTimeout(()=>this.cancel('识别等待超时，请重试',true),20000);
      this.finishCapture();
    }
  }
  finishCapture() {
    try {this.recognition?.stop();} catch {/* onstart will stop a permission-pending capture */}
  }
  async submit(generation) {
    if(generation!==this.generation || !this.released || !this.ready()) return;
    this.timers.clearTimeout(this.finishTimer);
    const recognition=this.recognition; this.recognition=null;
    recognition.onresult=recognition.onend=recognition.onerror=null;
    if(!this.text.trim()) {this.update('error','没有听清，请再说一次'); return;}
    this.update('processing','正在发送口令…');
    try {
      const result=await this.send(this.text);
      if(generation!==this.generation) return;
      if(!result?.ok) throw Error(result?.reason || result?.error || '口令没有发送成功');
      this.update('done','已发送：'+result.data.label);
    } catch(error) {
      if(generation===this.generation) this.update('error',error.message);
    }
  }
  cancel(message='这次录音已取消',error=false) {
    ++this.generation;
    this.timers.clearTimeout(this.holdTimer); this.timers.clearTimeout(this.finishTimer);
    const recognition=this.recognition; this.recognition=null;
    if(recognition) {recognition.onresult=recognition.onend=recognition.onerror=null; recognition.abort();}
    this.update(error?'error':'idle',message);
  }
}

export function enterVoiceRoute(route, state) {
  return route==='/' && state?.controller_connection?.source==='device'
    && state?.controller_connection?.connected===true && state?.robotd_ok===true
    && state?.posture!=='servo_session';
}

export function voiceConnection(state, {live=false, open=false}={}) {
  if(!open) return {connected:false,label:'鸭哥未连接',notice:'还没连上鸭哥，连接后就能说话'};
  const connection=state?.controller_connection;
  if(connection?.source==='development') return {connected:false,label:'预览模式 · 鸭哥未连接',notice:'现在只是页面预览，连上你的鸭哥后就能说话'};
  if(connection?.source!=='device') return {connected:false,label:'等待鸭哥连接',notice:'正在找鸭哥，连上后就能说话'};
  if(!live || connection.connected!==true || state?.robotd_ok!==true || state?.posture==='servo_session')
    return {connected:false,label:'鸭哥未连接',notice:'暂时联系不上鸭哥，连上后再试试'};
  return {connected:true,label:'鸭哥已连接',notice:null};
}
