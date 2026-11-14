// The microphone is active only while held. Release sends at most 12 seconds
// of 16 kHz mono PCM to this Studio's authenticated voice connection.
const RATE=16000, MAX_SECONDS=12;

export function pcm16Base64(chunks, inputRate) {
  const total=chunks.reduce((count, chunk)=>count+chunk.length,0);
  if(!Number.isFinite(inputRate) || inputRate<RATE || total>inputRate*MAX_SECONDS)
    throw Error('录音太长，请重新按住说话');
  if(total<inputRate/10)throw Error('没有听清，请再说一次');
  const samples=new Float32Array(total);
  let at=0;
  for(const chunk of chunks) {samples.set(chunk,at);at+=chunk.length;}
  const length=Math.floor(total*RATE/inputRate), bytes=new Uint8Array(length*2);
  const view=new DataView(bytes.buffer), ratio=inputRate/RATE;
  for(let index=0;index<length;index++) {
    const start=Math.floor(index*ratio), end=Math.max(start+1,Math.floor((index+1)*ratio));
    let sum=0;
    for(let sample=start;sample<end && sample<total;sample++)sum+=samples[sample];
    const value=Math.max(-1,Math.min(1,sum/(end-start)));
    view.setInt16(index*2,Math.round(value<0?value*32768:value*32767),true);
  }
  let binary='';
  for(let offset=0;offset<bytes.length;offset+=8192)
    binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
  return btoa(binary);
}

export class LocalSpeechCapture {
  constructor({recognize,mediaDevices=globalThis.navigator?.mediaDevices,Context=globalThis.AudioContext||globalThis.webkitAudioContext}) {
    this.recognize=recognize;this.mediaDevices=mediaDevices;this.Context=Context;
    this.chunks=[];this.total=0;this.stopping=false;this.finished=false;this.aborted=false;
  }
  async start() {
    try {
      const stream=await this.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
      if(this.aborted){stream.getTracks().forEach(track=>track.stop());return;}
      this.stream=stream;
      const context=this.context=new this.Context();
      this.source=context.createMediaStreamSource(stream);
      this.processor=context.createScriptProcessor(4096,1,1);
      const mute=context.createGain();mute.gain.value=0;this.mute=mute;
      this.processor.onaudioprocess=event=>{
        if(this.stopping || this.aborted)return;
        const chunk=new Float32Array(event.inputBuffer.getChannelData(0));
        if(this.total+chunk.length<=context.sampleRate*MAX_SECONDS){this.chunks.push(chunk);this.total+=chunk.length;}
      };
      this.source.connect(this.processor);this.processor.connect(mute);mute.connect(context.destination);
      await context.resume();
      if(this.aborted){this.dispose();return;}
      this.onstart?.();
      if(this.stopping)this.stop();
    } catch(error) {
      this.dispose();
      if(this.aborted)return;
      const code=error?.name==='NotAllowedError'?'not-allowed':error?.name==='NotFoundError'?'audio-capture':'service-unavailable';
      this.onerror?.({error:code,message:code==='service-unavailable'?'麦克风暂时不可用，请重新试试':undefined});
    }
  }
  dispose() {
    this.processor?.disconnect();this.source?.disconnect();this.mute?.disconnect();
    this.stream?.getTracks().forEach(track=>track.stop());
    this.context?.close().catch(()=>{});
  }
  async stop() {
    this.stopping=true;
    if(!this.stream || this.finished || this.aborted)return;
    this.finished=true;
    this.dispose();
    try {
      const pcm=pcm16Base64(this.chunks,this.context.sampleRate);
      const response=await this.recognize(pcm);
      if(this.aborted)return;
      if(!response?.ok)throw Error(response?.reason||response?.error||'鸭哥暂时听不清，请再说一次');
      const transcript=response.data?.text||'';
      this.onresult?.({resultIndex:0,results:[[{transcript}]]});
      this.onend?.();
    } catch(error) {
      if(!this.aborted)this.onerror?.({error:'service-unavailable',message:error.message});
    }
  }
  abort() {this.aborted=true;this.dispose();}
}
