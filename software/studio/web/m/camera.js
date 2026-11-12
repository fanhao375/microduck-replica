// 摄像头页：鸭子的视野。
//
// 画面走 WebRTC，信令由 camera-link.js 直接和 mediad 协商：
//   /api/camera 只告诉我们「信令端口是多少、视频来源是什么」。
// http 打开时直连 mediad 的 8443；https 打开时走同域的 /media-ws（nginx 转发到本机 8443）——
// 因为 https 页面里连明文 ws:// 会被浏览器当混合内容挡掉。
// 只转发 8000 的 SSH 隧道看不到画面——这是 mediad 的既有设计，不是这里的问题。
import { html, useState, useEffect, useRef, useCallback } from '../vendor/preact-htm.module.js';
import { CameraLink } from '../camera-link.js';
import { api } from './link.js';

const WORD = {
  stopped: '没开', connecting: '连接中', signaling: '正在和摄像头打招呼',
  negotiating: '协商视频中', receiving: '收到画面了', playing: '正在播放', error: '出错了',
};

export function CameraPage({ live, say }) {
  const videoRef = useRef(null);
  const camRef = useRef(null);
  const [info, setInfo] = useState(null);
  const [state, setState] = useState({ state: 'stopped', error: null, width: null, height: null, frames: 0 });
  const [starting, setStarting] = useState(false);

  const start = useCallback(async () => {
    if (starting) return;
    setStarting(true);
    let cfg = null;
    try { cfg = await api('/api/camera', undefined, 6000); }
    catch (e) { setState({ state: 'error', error: `读不到摄像头配置：${e.message}` }); setStarting(false); return; }
    if (cfg.status === 401) { setState({ state: 'error', error: '登录过期了，回上一页重新登录。' }); setStarting(false); return; }
    // 后端的只读接口都包了一层 {ok, data}，别把 data 里的字段当顶层字段用
    const d = cfg.data || cfg;
    setInfo(d);
    const port = d.signaling_port;
    if (!Number.isInteger(port)) { setState({ state: 'error', error: '后端没给信令端口。' }); setStarting(false); return; }

    camRef.current?.stop();
    const cam = new CameraLink({ video: videoRef.current, onState: (s) => setState(s) });
    camRef.current = cam;
    // https 下直连明文 8443 会被当混合内容挡掉，改走同域的 /media-ws 反代
    cam.start(port, location.protocol === 'https:' ? '/media-ws' : null);
    setStarting(false);
  }, [starting]);

  useEffect(() => {
    start();
    return () => { camRef.current?.stop(); camRef.current = null; };
    // 只在进页面时连一次：重连交给按钮，别跟着状态帧反复重连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const playing = state.state === 'playing';
  const over = !playing;
  const reason = state.error
    || (info && info.service_state && info.service_state !== 'ok' ? info.service_reason : null);

  return html`<div>
    <div class="m-video-wrap">
      <video ref=${videoRef} playsinline muted autoplay></video>
      <div class=${'m-video-tag' + (playing ? '' : ' bad')}><i></i>${playing ? '实时' : WORD[state.state] || state.state}</div>
      ${over ? html`<div class="m-video-over">
        ${state.state !== 'error' ? html`<span class="spin"></span>` : null}
        <span>${state.error || (state.state === 'stopped' ? '还没开始' : '正在连鸭子的摄像头…')}</span>
        ${reason ? html`<span style="font-size:12px;opacity:.8">${reason}</span>` : null}
        <button class="m-btn" style="height:38px;font-size:14px;background:rgba(255,255,255,.14);color:#fff;box-shadow:none"
          onClick=${start} disabled=${starting}>${starting ? '连接中…' : '重新连接'}</button>
      </div>` : null}
    </div>

    <h2 class="m-h2">画面</h2>
    <div class="m-card">
      <div class="m-kv"><span class="k">状态</span><span class="v">${WORD[state.state] || state.state}</span></div>
      <div class="m-kv"><span class="k">分辨率</span><span class="v">${state.width && state.height ? `${state.width} × ${state.height}` : '—'}</span></div>
      <div class="m-kv"><span class="k">累计帧</span><span class="v">${typeof state.frames === 'number' ? state.frames : '—'}</span></div>
      <div class="m-kv"><span class="k">视频来源</span><span class="v">${info?.source_label || '—'}</span></div>
      <div class="m-kv"><span class="k">信令端口</span><span class="v">${info?.signaling_port ?? '—'}</span></div>
    </div>

    <p class="m-tip">
      画面直接从鸭子的 mediad 过来，不经过 Studio，所以延迟低、也不占主程序的 CPU。
      连不上时先确认：手机和鸭子在同一个 WiFi、并且是用 <b>${location.hostname}:${location.port || '8000'}</b> 打开的（不是隧道转发的地址）。
    </p>
    ${location.protocol === 'https:' ? html`<div class="m-banner ok">
      <span>现在是 https 打开的：信令走同域的 /media-ws 转发，不会被浏览器当混合内容挡掉。</span>
    </div>` : null}
  </div>`;
}
