import {html,useEffect,useRef,useState} from './vendor/preact-htm.module.js';
import {CameraLink} from './camera-link.js';
export function AiCameraPreview({api,portrait=false}){
 const video=useRef(null),camera=useRef(null);
 const [rotation,setRotation]=useState(90);
 const [status,setStatus]=useState('正在开启摄像头…'),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  let disposed=false,warm=null,warmed=false;
  (async()=>{
   try{
    const r=await api('/api/camera',undefined,6000);if(disposed)return;
    const cfg=r.data||r;
    if(!Number.isInteger(cfg.signaling_port))throw Error('无法读取摄像头配置');
    const cam=new CameraLink({video:video.current,onState:s=>{
     if(disposed)return;
     if(s.metadata && [90,270].includes(s.metadata.rotate))setRotation(s.metadata.rotate);
     if(s.state==='playing'){
      if(!warm&&!warmed){setStatus('摄像头已开启，正在调整曝光…');warm=setTimeout(()=>{warmed=true;setStatus('摄像头持续开启，曝光自动调整');},12000);}
     }else if(s.state==='error'){clearTimeout(warm);warm=null;warmed=false;setStatus(s.error||'摄像头连接失败，请重新连接');}
    }});
    camera.current=cam;cam.start(cfg.signaling_port,location.protocol==='https:'?'/media-ws':null);
   }catch(e){if(!disposed)setStatus(e.message||'摄像头开启失败');}
  })();
  return()=>{disposed=true;clearTimeout(warm);camera.current?.stop();camera.current=null;};
 },[attempt]);
 return html`<div data-ai-camera-preview style="margin:12px 0"><div style=${portrait?"position:relative;width:100%;aspect-ratio:16/9":""}><div style=${portrait?"position:absolute;left:0;top:0;width:31.640625%;height:100%;overflow:hidden;border-radius:8px;background:#111":""}><video ref=${video} playsinline muted autoplay style=${portrait?`position:absolute;left:50%;top:50%;width:177.777778%;height:56.25%;max-width:none;object-fit:contain;transform:translate(-50%,-50%) rotate(${rotation}deg)`:"display:block;width:100%;max-width:100%;height:auto;border-radius:8px;background:#111"}></video></div></div><p role="status">${status}</p><button type="button" onClick=${()=>{setStatus('正在重新连接摄像头…');setAttempt(n=>n+1);}}>重新连接摄像头</button></div>`;
}
