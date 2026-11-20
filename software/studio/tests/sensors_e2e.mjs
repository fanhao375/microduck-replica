import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
const arg = key => process.argv[process.argv.indexOf(`--${key}`) + 1];
const shots = arg('shots'); mkdirSync(shots, { recursive: true });
const target = await (await fetch(`http://127.0.0.1:${arg('cdp')}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
let nextId = 0; const pending = new Map(), errors = [];
const send = (method, params = {}, timeout = 15000) => new Promise((resolve, reject) => {
  const id = ++nextId, timer = setTimeout(() => { pending.delete(id); reject(Error(`CDP timeout: ${method}`)); }, timeout);
  pending.set(id, message => { clearTimeout(timer); message.error ? reject(Error(JSON.stringify(message.error))) : resolve(message.result); });
  ws.send(JSON.stringify({ id, method, params }));
});
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(message.params.args.map(a => a.value || a.description).join(' '));
});
const js = async expression => {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  return response.result?.value;
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = async (expression, what, timeout = 16000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const value = await js(expression);
    if (value) return value;
    await sleep(100);
  }
  const layout = await js(`(() => { const p=document.querySelector('#pad-block'), c=document.querySelector('.inline-camera'); return {url:location.hash,scroll:scrollY,pad:p?.getBoundingClientRect().toJSON(),margin:p&&getComputedStyle(p).scrollMarginTop,card:c?.getBoundingClientRect().toJSON(),state:document.querySelector('.inline-camera-status')?.dataset.state}; })()`);
  await shot('timeout').catch(error => console.error(`Timeout screenshot for "${what}": ${error.message}`));
  throw Error(`Timeout: ${what}\nLayout: ${JSON.stringify(layout)}\n${await js('document.body.innerText.slice(0,4500)')}\nBrowser errors: ${JSON.stringify(errors)}\nFixture: ${JSON.stringify(await js(`window.__sensorFixture && {sockets:__sensorFixture.sockets.length,states:__sensorFixture.sockets.map(s=>s.readyState),signals:__sensorFixture.signals,rpc:__sensorFixture.rpc,pcs:__sensorFixture.pcs.length}`))}`);
};
const click = async selector => {
  await wait(`document.querySelector(${JSON.stringify(selector)}) && !document.querySelector(${JSON.stringify(selector)}).disabled`, selector);
  await js(`document.querySelector(${JSON.stringify(selector)}).click()`);
};
const assert = (value, message) => { if (!value) throw Error(message); };
const shot = async name => {
  const response = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, 45000)
    .catch(error => { throw Error(`Screenshot ${name}: ${error.message}`); });
  writeFileSync(`${shots}/${name}.png`, Buffer.from(response.data, 'base64'));
};
const countReads = () => readFileSync(arg('record'), 'utf8').split('\n').filter(Boolean).length;
const assertCompactFrame = async (rotation, label) => {
  const geometry = await wait(`(() => {
    const screen=document.querySelector('.inline-camera .camera-screen');
    const v=document.querySelector('.inline-camera-video');
    const peer=__sensorFixture.pcs.at(-1);
    const metadata=peer?.channel?.deliveredMetadata;
    if (peer?.connectionState!=='connected' || metadata?.rotate!==${rotation} ||
        screen?.dataset.playing!=='true' || !v || v.srcObject!==peer.stream ||
        v.readyState<HTMLMediaElement.HAVE_CURRENT_DATA || v.paused ||
        v.videoWidth!==metadata.width || v.videoHeight!==metadata.height ||
        !v.style.transform.includes('rotate(${rotation}deg)')) return false;
    const s=screen.getBoundingClientRect();
    const c=document.querySelector('.inline-camera').getBoundingClientRect();
    return {width:s.width,height:s.height,center:s.left+s.width/2,cardCenter:c.left+c.width/2,
      rawWidth:v.videoWidth,rawHeight:v.videoHeight};
  })()`, `current decoded frame and applied media.video rotation (${label})`);
  const sideways = rotation===90 || rotation===270;
  const expected = sideways ? geometry.rawHeight/geometry.rawWidth : geometry.rawWidth/geometry.rawHeight;
  assert(Math.abs(geometry.width/geometry.height-expected)<.01,
    `compact frame follows rotated video without internal black bars (${label}): ${JSON.stringify(geometry)}`);
  assert(Math.abs(geometry.center-geometry.cardCenter)<2, `compact video is centered (${label})`);
  assert(geometry.height>=280 && geometry.height<=421, `preview is readable and stays below its height budget (${label}): ${JSON.stringify(geometry)}`);
};
const exerciseInlineZoom = async label => {
  const before = await js(`(() => {
    const video=document.querySelector('.inline-camera-video');window.__zoomVideo=video;window.__zoomStream=video.srcObject;
    return {hash:location.hash,height:document.querySelector('.inline-camera .camera-screen').getBoundingClientRect().height,
      sockets:__sensorFixture.sockets.length,pcs:__sensorFixture.pcs.length,frames:video.getVideoPlaybackQuality().totalVideoFrames};
  })()`);
  assert(await js(`document.querySelector('.inline-camera-expand')?.tagName==='BUTTON'`), 'enlarge is an inline toggle, never a page link');
  await click('.inline-camera-expand');
  await wait(`document.querySelector('.inline-camera-expand')?.getAttribute('aria-pressed')==='true' && document.querySelector('.inline-camera .camera-screen').getBoundingClientRect().height>${before.height+20}`, `in-place enlargement ${label}`);
  await js(`document.querySelector('.inline-camera').scrollIntoView({block:'start'})`); await sleep(100);
  assert(await js(`location.hash===${JSON.stringify(before.hash)} && !document.querySelector('.camera-page') &&
    document.querySelector('.inline-camera-video')===__zoomVideo && __zoomVideo.srcObject===__zoomStream &&
    __sensorFixture.sockets.length===${before.sockets} && __sensorFixture.pcs.length===${before.pcs}`), 'enlargement keeps the route, video element and media session');
  assert(await js('document.documentElement.scrollWidth<=innerWidth+1'), 'enlarged preview never overflows horizontally');
  await wait(`__zoomVideo.getVideoPlaybackQuality().totalVideoFrames>${before.frames}`, 'same video keeps playing during enlargement');
  await shot(`inline-camera-expanded-${label}`);
  await click('.inline-camera-expand');
  await wait(`document.querySelector('.inline-camera-expand')?.getAttribute('aria-pressed')==='false' && Math.abs(document.querySelector('.inline-camera .camera-screen').getBoundingClientRect().height-${before.height})<2`, 'restore original inline size');
  assert(await js(`location.hash===${JSON.stringify(before.hash)} && __sensorFixture.sockets.length===${before.sockets} && document.querySelector('.inline-camera-video')===__zoomVideo`), 'restore does not reconnect or navigate');
};

// Explicit test double. The video element still receives and plays a native canvas MediaStream.
function installVideoFixture() {
  const NativeWS = window.WebSocket;
  const fixture = window.__sensorFixture = { mode: 'ok', rotate: 90, signals: [], rpc: [], sockets: [], pcs: [], tracks: [], stopped: 0 };
  class Channel {
    constructor() { this.readyState = 'open'; }
    send(raw) {
      const request = JSON.parse(raw); fixture.rpc.push(request);
      if (request.method === 'media.video') {
        const metadata = { width: 640, height: 480, rotate: fixture.rotate, mono_ns: 1, real_ns: 1 };
        // WebRTC video and the media.video reply arrive on separate channels.
        setTimeout(() => {
          if (this.readyState !== 'open') return;
          this.deliveredMetadata = metadata;
          this.onmessage?.({ data: JSON.stringify({ jsonrpc: '2.0', id: request.id, result: metadata }) });
        }, 1800);
      }
    }
    close() { this.readyState = 'closed'; }
  }
  class Signaling {
    constructor(url) {
      this.url = url; this.readyState = 0; fixture.sockets.push(this);
      setTimeout(() => {
        if (this.readyState === 3) return;
        if (fixture.mode === 'error') { this.onerror?.(new Event('error')); return; }
        this.readyState = 1; this.onopen?.(new Event('open')); this.reply({ type: 'welcome', peerId: 'browser-fixture' });
      }, 5);
    }
    reply(message) { queueMicrotask(() => { if (this.readyState === 1) this.onmessage?.({ data: JSON.stringify(message) }); }); }
    send(raw) {
      const message = JSON.parse(raw); fixture.signals.push(message);
      if (message.type === 'list') this.reply({ type: 'list', producers: [{ id: 'fixture-producer', meta: { name: '本地测试画面', release: 'fixture', api_version: '40', simulated: 'true' } }] });
      if (message.type === 'startSession') {
        this.reply({ type: 'sessionStarted', sessionId: 'fixture-session' });
        this.reply({ type: 'peer', sessionId: 'fixture-session', sdp: { type: 'offer', sdp: 'fixture-offer' } });
      }
    }
    close() { this.readyState = 3; this.onclose?.(new Event('close')); }
  }
  window.WebSocket = new Proxy(NativeWS, { construct(Target, args) {
    return new URL(args[0], location.href).port === '8443' ? new Signaling(args[0]) : Reflect.construct(Target, args);
  } });
  window.RTCPeerConnection = class {
    constructor() { this.connectionState = 'new'; this.remoteDescription = null; fixture.pcs.push(this); }
    async setRemoteDescription(description) { this.remoteDescription = description; }
    async createAnswer() { return { type: 'answer', sdp: 'fixture-answer' }; }
    async addIceCandidate() {}
    getTransceivers() { return []; }
    async setLocalDescription(description) {
      this.localDescription = description;
      if (this.connectionState === 'closed') return;
      this.connectionState = 'connected'; this.onconnectionstatechange?.();
      this.channel = new Channel(); this.ondatachannel?.({ channel: this.channel });
      const canvas = this.canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const context = canvas.getContext('2d'); let frame = 0;
      const draw = () => { context.fillStyle = frame++ % 2 ? '#0f7c80' : '#174862'; context.fillRect(0, 0, 640, 480);
        // Native camera bytes are mounted 90 degrees; the viewer applies the reported rotation.
        context.save(); context.translate(320,240); context.rotate(-Math.PI/2);
        context.fillStyle = 'white'; context.font = '28px sans-serif'; context.fillText('本地测试画面', -100, 0); context.restore(); };
      draw(); const stream = this.stream = canvas.captureStream(4);
      this.timer = setInterval(draw, 250); const track = stream.getVideoTracks()[0]; fixture.tracks.push(track);
      this.ontrack?.({ track, streams: [stream] });
    }
    close() { this.connectionState = 'closed'; clearInterval(this.timer); this.channel?.close(); fixture.stopped++; }
  };
  window.__sensorHidden = false;
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => window.__sensorHidden ? 'hidden' : 'visible' });
}

try {
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.bringToFront');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(${installVideoFixture.toString()})();` });
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `${arg('url')}#/` });
  await wait(`document.querySelector('.band-camera')?.dataset.state==='test' && document.querySelector('.band-tof')?.dataset.state==='simulated'`, 'seven sensor status cards');
  const labels = await js(`[...document.querySelectorAll('.band > *')].map(e=>e.querySelector('.label')?.textContent.trim())`);
  assert(JSON.stringify(labels)===JSON.stringify(['电池','舵机','IMU','摄像头','ToF','手柄','主板']), `status card order: ${JSON.stringify(labels)}`);
  assert(await js('__sensorFixture.sockets.length===0'), 'status checks must not start a WebRTC video session');
  await js(`document.querySelector('.band').scrollIntoView({block:'center'})`); await shot('band-desktop');
  for (const width of [390,320]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 844, deviceScaleFactor: 1, mobile: true });
    await js(`document.querySelector('.band').scrollIntoView({block:'start'})`); await sleep(100);
    assert(await js(`document.documentElement.scrollWidth<=innerWidth+1 && [...document.querySelectorAll('.band > *')].every(e=>e.scrollWidth<=e.clientWidth+1)`), `status cards fit ${width}px`);
    await shot(`band-mobile-${width}`);
  }
  const refreshBand = async () => {
    await js(`window.__sensorHidden=true;document.dispatchEvent(new Event('visibilitychange'))`); await sleep(100);
    await js(`window.__sensorHidden=false;document.dispatchEvent(new Event('visibilitychange'))`);
  };
  writeFileSync(arg('control'), JSON.stringify({ mode:'missing',camera:'offline' })); await refreshBand();
  await wait(`document.querySelector('.band-camera')?.dataset.state==='offline' && document.querySelector('.band-tof .word')?.innerText==='服务在线'`, 'camera offline; tofd responding without a sensor');
  writeFileSync(arg('control'), JSON.stringify({ mode:'ready-real' }));
  writeFileSync(arg('camera-config'), '[media]\nsource="camera"\n'); await refreshBand();
  await wait(`document.querySelector('.band-camera')?.dataset.state==='online' && document.querySelector('.band-tof')?.dataset.state==='online'`, 'service and valid ToF online');
  assert(await js(`document.querySelector('.band-tof .word').innerText==='350 mm'`), 'nearest valid range in overview');
  assert(await js(`document.querySelector('.band-camera .word').innerText==='服务在线'`), 'camera must not claim physical hardware verification');
  await js(`window.__sensorHidden=true;document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(250); const beforeHidden = countReads();
  await sleep(10300); assert(countReads()===beforeHidden,'hidden status cards do not poll');
  writeFileSync(arg('control'), JSON.stringify({ mode:'ready' }));
  writeFileSync(arg('camera-config'), '[media]\nsource="test"\n');
  await js(`window.__sensorHidden=false;document.dispatchEvent(new Event('visibilitychange'))`);
  await wait(`document.querySelector('.band-camera')?.dataset.state==='test'`, 'band resumed');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false });
  // User's marked location: forward video immediately below the 3D posture card.
  await wait(`document.querySelector('.pd-left .inline-camera')`, 'camera below posture');
  await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`);
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='test'`, 'test source stays a placeholder');
  assert(await js('__sensorFixture.sockets.length===0'), 'overview must not stream a test pattern automatically');
  assert(await js(`(() => { const d=document.querySelector('.pd-3d').getBoundingClientRect(), c=document.querySelector('.inline-camera').getBoundingClientRect(), r=document.querySelector('.pd-live').getBoundingClientRect();
    return c.top>=d.bottom && c.top-d.bottom<=20 && Math.abs(c.left-d.left)<2 && Math.abs(c.width-d.width)<2 && c.right<=r.left && c.bottom<=innerHeight-4; })()`), 'desktop camera and posture fit together in the left column');
  await shot('inline-camera-test-desktop');
  writeFileSync(arg('camera-config'), '[media]\nsource="camera"\n');
  writeFileSync(arg('control'), JSON.stringify({ mode:'ready', camera:'offline' }));
  await click('.inline-camera-retry');
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='offline'`, 'unavailable camera stays offline');
  assert(await js('__sensorFixture.sockets.length===0'), 'offline service must not start RTC');
  writeFileSync(arg('control'), JSON.stringify({ mode:'ready' }));
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='playing'`, 'inline camera recovers without a retry click', 16000);
  assert(await js(`document.querySelector('.inline-camera-video').videoWidth===640 && /模拟/.test(document.querySelector('.inline-camera-source').innerText)`), 'actual frames and simulated producer evidence');
  await wait(`(() => { const s=document.querySelector('.inline-camera .camera-screen').getBoundingClientRect(), v=document.querySelector('.inline-camera-video').getBoundingClientRect();
    return Math.abs(v.width-s.width)<2 && Math.abs(v.height-s.height)<2 && getComputedStyle(document.querySelector('.inline-camera-video')).transform!=='none'; })()`, 'rotated video bounds settle after frame resize');
  await assertCompactFrame(90, 'desktop portrait');
  await exerciseInlineZoom('desktop');
  await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`); await sleep(100);
  await shot('inline-camera-live-desktop');
  await js('__sensorFixture.rotate=0'); await click('.inline-camera-retry');
  await assertCompactFrame(0, 'desktop landscape');
  await exerciseInlineZoom('landscape-desktop');
  await shot('inline-camera-landscape-desktop');
  await js('__sensorFixture.rotate=90'); await click('.inline-camera-retry');
  await assertCompactFrame(90, 'desktop portrait after reconnect');
  for (const [width,height] of [[1440,900],[1101,900],[1440,800],[1440,768]]) {
    await send('Emulation.setDeviceMetricsOverride', { width,height,deviceScaleFactor:1,mobile:false });
    await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`); await sleep(150);
    const layout = await js(`(() => { const d=document.querySelector('.pd-3d').getBoundingClientRect(), c=document.querySelector('.inline-camera').getBoundingClientRect(), left=document.querySelector('.pd-left'), css=getComputedStyle(left);
      return {below:c.top>=d.bottom, bottom:c.bottom, fits:left.getBoundingClientRect().height+parseFloat(css.top)+16<=innerHeight, sticky:css.position}; })()`);
    assert(layout.below, `camera never overlaps posture at ${width}x${height}`);
    if (height>840 && layout.fits) assert(layout.sticky==='sticky' && layout.bottom<=height-4, `whole left stack fits ${width}x${height}: ${JSON.stringify(layout)}`);
    else assert(layout.sticky==='static', 'short or crowded screens scroll rather than pin a clipped preview');
  }
  // A wrapped servo notice can consume much more height than the normal band.
  await send('Emulation.setDeviceMetricsOverride', { width:1440,height:900,deviceScaleFactor:1,mobile:false });
  await js(`document.querySelector('.sticky-bar').style.minHeight='300px'`);
  await wait(`getComputedStyle(document.querySelector('.pd-left')).position==='static'`, 'oversized sticky stack falls back to scrolling');
  await js(`document.querySelector('.sticky-bar').style.minHeight=''`);
  await wait(`(() => { const left=document.querySelector('.pd-left'), css=getComputedStyle(left), fits=left.getBoundingClientRect().height+parseFloat(css.top)+16<=innerHeight;
    return css.position===(fits?'sticky':'static'); })()`, 'stack uses actual available space after notice clears');
  await send('Emulation.setDeviceMetricsOverride', { width:1440,height:1050,deviceScaleFactor:1,mobile:false });
  await js('window.scrollTo(0,0)');
  await wait(`__sensorFixture.sockets.every(s=>s.readyState===3) && __sensorFixture.tracks.every(t=>t.readyState==='ended')`, 'scroll away releases inline video');
  await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`);
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='playing'`, 'return to preview resumes');
  await js(`window.__sensorHidden=true;document.dispatchEvent(new Event('visibilitychange'))`);
  await wait(`__sensorFixture.sockets.every(s=>s.readyState===3)`, 'background releases inline video');
  await js(`window.__sensorHidden=false;document.dispatchEvent(new Event('visibilitychange'))`);
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='playing'`, 'foreground resumes inline video');
  for (const width of [390,320]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height:844, deviceScaleFactor:1, mobile:true });
    await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`); await sleep(150);
    assert(await js(`(() => { const d=document.querySelector('.pd-3d').getBoundingClientRect(), c=document.querySelector('.inline-camera').getBoundingClientRect(), r=document.querySelector('#pd-remote').getBoundingClientRect();
      return d.bottom<=c.top && c.bottom<=r.top && document.documentElement.scrollWidth<=innerWidth+1; })()`), `camera follows 3D and fits ${width}px`);
    await assertCompactFrame(90, `mobile portrait ${width}`);
    await exerciseInlineZoom(`mobile-${width}`);
    await shot(`inline-camera-mobile-${width}`);
  }
  await js(`location.hash='#/camera'`);
  await wait(`document.querySelector('.camera-status')?.dataset.state==='playing'`, 'dedicated camera takes over');
  assert(await js('__sensorFixture.sockets.filter(s=>s.readyState===1).length===1'), 'only one active video session across routes');
  await js(`location.hash='#/pad'`);
  // The handpad's async status banner can shift this anchor after it scrolls.
  await wait(`!document.querySelector('.camera-page') && (() => { const y=document.querySelector('#pad-block').getBoundingClientRect().top; return y>=56 && y<180; })()`, 'return to visible handpad anchor before scrolling to video');
  writeFileSync(arg('camera-config'), '[media]\nsource="other"\n');
  await js(`document.querySelector('.inline-camera').scrollIntoView({block:'center'})`);
  await click('.inline-camera-retry');
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='unknown'`, 'unknown source never claims camera');
  assert(await js('__sensorFixture.sockets.every(s=>s.readyState===3)'), 'unknown source does not stream');
  writeFileSync(arg('camera-config'), '[media]\nsource="test"\n');
  await click('.inline-camera-retry');
  await wait(`document.querySelector('.inline-camera-status')?.dataset.state==='test'`, 'test source restored');
  await send('Emulation.setDeviceMetricsOverride', { width:1440, height:1050, deviceScaleFactor:1, mobile:false });
  await click('.band-camera');
  await wait(`document.querySelector('.camera-status')?.dataset.state==='playing'`, 'native video playback');
  assert(await js(`document.querySelector('.camera-video').videoWidth===640`), 'actual video geometry should be used');
  assert(await js(`/测试|模拟/.test(document.querySelector('.camera-source').innerText)`), 'test video must be identified');
  const nav = await js(`[...document.querySelectorAll('.sidebar a')].map(e=>e.textContent.trim())`);
  // Existing sidebar class may change; relative href order is the requirement.
  const links = await js(`[...document.querySelectorAll('nav:not(.tabbar) a')].map(e=>e.getAttribute('href'))`);
  assert(links.indexOf('#/camera') > links.indexOf('#/pad') && links.indexOf('#/tof') < links.indexOf('#/servos'), 'sensor navigation belongs after handpad before servos');
  await shot('camera-desktop');
  await click('.camera-stop');
  await wait(`window.__sensorFixture.sockets.every(s=>s.readyState===3) && window.__sensorFixture.tracks.every(t=>t.readyState==='ended')`, 'explicit stop releases media');
  const starts = await js('window.__sensorFixture.sockets.length');
  await sleep(1300);
  assert(await js('window.__sensorFixture.sockets.length') === starts, 'explicit stop must not automatically reconnect');
  await click('.camera-start');
  await wait(`document.querySelector('.camera-status')?.dataset.state==='playing'`, 'restart video');
  await js(`location.hash='#/tof'`);
  await wait(`document.querySelectorAll('.tof-grid [data-zone]').length===64`, '8x8 ranges');
  await wait(`document.querySelector('.tof-status')?.dataset.state==='ready'`, 'ToF ready');
  assert(await js(`document.querySelectorAll('.tof-grid [data-kind="no_target"]').length===1 && document.querySelectorAll('.tof-grid [data-kind="unusable"]').length===2`), 'no target and invalid ranges must remain distinct');
  assert(await js(`document.querySelector('.tof-valid').innerText.includes('61')`), 'only valid positive ranges count');
  assert(await js(`/350|0[.]35/.test(document.querySelector('.tof-nearest').innerText)`), 'nearest valid range');
  await wait(`window.__sensorFixture.sockets.every(s=>s.readyState===3)`, 'route change closes camera');
  await shot('tof-desktop');

  writeFileSync(arg('control'), JSON.stringify({ mode: 'missing' }));
  await wait(`document.querySelector('.tof-status')?.dataset.state==='unavailable'`, 'missing sensor');
  assert(await js(`[...document.querySelectorAll('.tof-grid [data-zone]')].every(e=>e.dataset.kind==='empty' && e.disabled) && document.querySelector('.tof-valid').innerText==='—'`), 'missing sensor must not leave live-looking old ranges');
  writeFileSync(arg('control'), JSON.stringify({ mode: 'bad' }));
  await wait(`document.querySelector('.tof-status')?.dataset.state==='error'`, 'malformed sensor data');
  writeFileSync(arg('control'), JSON.stringify({ mode: 'ready' }));
  await wait(`document.querySelector('.tof-status')?.dataset.state==='ready'`, 'sensor recovered');
  await js(`window.__sensorHidden=true;document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(1300); const hiddenReads = countReads(); await sleep(1200);
  assert(countReads() === hiddenReads, 'hidden page must not keep polling ToF');
  await js(`window.__sensorHidden=false;document.dispatchEvent(new Event('visibilitychange'))`);
  await wait(`document.querySelector('.tof-status')?.dataset.state==='ready'`, 'visible sensor page resumes');

  let mobileFits = true;
  for (const width of [390, 320]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 844, deviceScaleFactor: 1, mobile: true });
    await js('window.scrollTo(0,0)'); await sleep(200);
    mobileFits &&= await js('document.documentElement.scrollWidth<=innerWidth+1');
    await shot(`tof-mobile-${width}`);
  }
  assert(mobileFits, 'sensor page must fit mobile width');
  await js(`location.hash='#/camera'`);
  await wait(`document.querySelector('.camera-status')?.dataset.state==='playing'`, 'camera on mobile');
  await shot('camera-mobile');
  const afterLeave = countReads(); await sleep(1300); assert(countReads() === afterLeave, 'leaving ToF stops requests');
  await js(`window.__sensorFixture.mode='error'`); await click('.camera-reconnect');
  await wait(`document.querySelector('.camera-status')?.dataset.state==='error'`, 'signaling failure shown');
  assert(await js(`window.__sensorFixture.rpc.every(m=>m.method==='media.video')`), 'camera client may only read video metadata');
  process.stdout.write(JSON.stringify({ errors, mobileFits, nav }));
} finally { ws.close(); }
