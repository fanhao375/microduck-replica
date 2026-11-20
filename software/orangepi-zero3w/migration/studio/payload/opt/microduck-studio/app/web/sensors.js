import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';
import { CameraLink } from './camera-link.js';

if (typeof document !== 'undefined' && !document.getElementById('sensors-css')) {
  const sheet = document.createElement('link');
  sheet.id = 'sensors-css'; sheet.rel = 'stylesheet';
  sheet.href = new URL('./sensors.css', import.meta.url).href;
  document.head.appendChild(sheet);
}

const emptyTof = (state, reason, extra = {}) => ({ ...extra, state, reason, zones: [], valid_count: null, nearest_mm: null, farthest_mm: null });
const finite = n => typeof n === 'number' && Number.isFinite(n);

export function tofSnapshot(data) {
  if (!data || !['ready', 'waiting', 'unavailable', 'error'].includes(data.state)) return emptyTof('error', 'ToF 返回的数据无效。');
  const info = { sensor: typeof data.sensor === 'string' ? data.sensor : null, simulated: data.simulated === true,
    hz: finite(data.hz) ? data.hz : null, seq: data.seq, age_ms: data.age_ms };
  if (data.state !== 'ready') return emptyTof(data.state, data.reason || '', info);
  if (data.rows !== 8 || data.cols !== 8 || !Array.isArray(data.distance_mm) || data.distance_mm.length !== 64 ||
    !Array.isArray(data.status) || data.status.length !== 64 || !data.distance_mm.every(finite) ||
    !data.status.every(n => Number.isInteger(n) && n >= 0 && n <= 255) || !finite(data.age_ms) || data.age_ms < 0) {
    return emptyTof('error', 'ToF 帧格式无效，等待下一份完整的 8 × 8 读数。', info);
  }
  if (data.age_ms >= 3000) return emptyTof('stale', '读数已过期，等待新的 ToF 帧。', info);
  const zones = data.status.map((status, i) => {
    const raw_mm = data.distance_mm[i];
    const kind = status === 255 ? 'no_target' : (status === 5 || status === 9) && raw_mm > 0 ? 'range' : 'unusable';
    return { kind, mm: kind === 'range' ? raw_mm : null, status, raw_mm };
  });
  const values = zones.filter(z => z.kind === 'range').map(z => z.mm);
  return { ...info, state: 'ready', reason: '', zones, valid_count: values.length,
    nearest_mm: values.length ? Math.min(...values) : null, farthest_mm: values.length ? Math.max(...values) : null };
}

export function cameraSource(config, producer) {
  const configured = config?.source === 'test' ? '配置：测试画面' : config?.source === 'camera' ? '配置：摄像头' : '配置来源未知';
  const simulated = producer?.simulated === true || producer?.simulated === 'true';
  return { label: simulated ? `${configured} · 运行端为模拟设备` : configured, verified: false,
    note: config?.source === 'test' ? '当前配置使用测试图案；视频播放成功也不代表真实摄像头已连接。'
      : simulated ? '运行端明确标记为模拟设备。'
        : '来源依据只读配置；真实摄像头是否正常，需要查看实际画面确认。' };
}

export function createTofReader({ request, onValue, intervalMs = 500, staleMs = 3000,
  now = () => performance.now(), setTimeout = globalThis.setTimeout, clearTimeout = globalThis.clearTimeout }) {
  let generation = 0, timer = null, staleTimer = null, controller = null, active = false;
  const stop = () => { active = false; generation++; clearTimeout(timer); clearTimeout(staleTimer); controller?.abort(); controller = null; };
  const read = async current => {
    if (!active || current !== generation) return;
    const requestStartedAt = now();
    controller = new AbortController();
    try {
      const result = await request(controller.signal);
      if (!active || current !== generation) return;
      if (!result?.ok) throw Error(typeof result?.error === 'string' ? result.error : 'ToF 读取失败。');
      const data = result.data;
      const serverAge = data?.age_ms;
      const received = finite(serverAge) && serverAge >= 0
        ? { ...data, age_ms: serverAge + Math.max(0, now() - requestStartedAt) } : data;
      const value = tofSnapshot(received);
      clearTimeout(staleTimer);
      onValue({ ...value, receivedAt: now(), updatedAt: Date.now() });
      if (value.state === 'ready') staleTimer = setTimeout(() => {
        if (active && current === generation) onValue(emptyTof('stale', '读数已过期，正在等待新的 ToF 帧。'));
      }, Math.max(0, staleMs - value.age_ms));
    } catch (error) {
      if (active && current === generation) {
        clearTimeout(staleTimer);
        onValue(emptyTof('error', error?.name === 'AbortError' ? '读取 ToF 超时，请检查服务连接。' : error.message || 'ToF 连接断开。'));
      }
    } finally {
      if (active && current === generation) { controller = null; timer = setTimeout(() => read(current), intervalMs); }
    }
  };
  return { start() { stop(); active = true; onValue(emptyTof('loading', '正在读取 ToF…')); read(generation); }, stop };
}

function useVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const change = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', change);
    return () => document.removeEventListener('visibilitychange', change);
  }, []);
  return visible;
}

const uncheckedBand = (reason = '点开查看详情') => ({ state: 'unknown', word: '未检查', small: reason });
const checkingBand = () => ({ state: 'checking', word: '检测中', small: '只读取状态' });

export function cameraBandStatus(data) {
  if (data?.service_state === 'offline') return { state: 'offline', word: '未连接', small: '视频服务未响应' };
  if (data?.service_state !== 'online') return uncheckedBand();
  if (data.source === 'test') return { state: 'test', word: '测试画面', small: '视频服务在线' };
  return { state: 'online', word: '服务在线', small: data.source === 'camera' ? '点开确认画面' : '画面来源待确认' };
}

export function tofBandStatus(data) {
  const view = tofSnapshot(data);
  if (view.state === 'ready') return view.simulated ? { state: 'simulated', word: '模拟数据', small: '点开查看距离图' }
    : { state: 'online', word: '在线', small: '点开查看距离图' };
  if (view.state === 'unavailable') return { state: 'offline', word: '未连接', small: '传感器或服务不可用' };
  if (view.state === 'waiting') return { state: 'waiting', word: '等待数据', small: '服务已响应' };
  if (view.state === 'stale') return uncheckedBand('上次读数已过期');
  return { state: 'error', word: '读取失败', small: '点开查看详情' };
}

/** Low-frequency presence checks only. No video pipeline or service-start requests. */
export function createSensorBandReader({ request, onValue, intervalMs = 10000, staleMs = 20000,
  now = () => performance.now(), setTimeout = globalThis.setTimeout, clearTimeout = globalThis.clearTimeout }) {
  let generation = 0, active = false, timer = null;
  const controllers = new Set(), expiry = new Map();
  let values = { camera: checkingBand(), tof: checkingBand() };
  const emit = (kind, value) => { values = { ...values, [kind]: value }; onValue(values); };
  const stop = () => {
    active = false; generation++; clearTimeout(timer);
    for (const timeout of expiry.values()) clearTimeout(timeout);
    expiry.clear(); for (const controller of controllers) controller.abort(); controllers.clear();
  };
  const readOne = async (kind, current) => {
    const controller = new AbortController(), started = now(); controllers.add(controller);
    let value;
    try {
      const result = await request(kind, controller.signal);
      if (!active || current !== generation) return;
      if (!result?.ok) throw Error('读取失败');
      const elapsed = Math.max(0, now() - started), data = result.data;
      value = kind === 'camera' ? (elapsed >= staleMs ? uncheckedBand('检查结果已过期') : cameraBandStatus(data))
        : tofBandStatus(finite(data?.age_ms) && data.age_ms >= 0 ? { ...data, age_ms: data.age_ms + elapsed } : data);
    } catch {
      if (!active || current !== generation) return;
      value = { state: 'error', word: '读取失败', small: '点开查看详情' };
    } finally { controllers.delete(controller); }
    if (!active || current !== generation) return;
    clearTimeout(expiry.get(kind)); emit(kind, value);
    expiry.set(kind, setTimeout(() => {
      if (active && current === generation) emit(kind, uncheckedBand('状态已过期，等待检查'));
    }, staleMs));
  };
  const read = async current => {
    await Promise.allSettled(['camera', 'tof'].map(kind => readOne(kind, current)));
    if (active && current === generation) timer = setTimeout(() => read(current), intervalMs);
  };
  return { start() {
    stop(); active = true; values = { camera: checkingBand(), tof: checkingBand() }; onValue(values); read(generation);
  }, stop };
}

export function SensorBandCells({ api, active }) {
  const visible = useVisible();
  const [values, setValues] = useState({ camera: checkingBand(), tof: checkingBand() });
  useEffect(() => {
    if (!active || !visible) { setValues({ camera: uncheckedBand(), tof: uncheckedBand() }); return; }
    const reader = createSensorBandReader({ request: (kind, signal) => api(`/api/${kind}`, undefined, 3500, signal), onValue: setValues });
    reader.start(); return () => reader.stop();
  }, [api, active, visible]);
  return ['camera', 'tof'].map(kind => html`<a key=${kind} href=${`#/${kind}`} class=${`band-sensor band-${kind}`} data-state=${values[kind].state}>
    <span class="label">${kind === 'camera' ? '摄像头' : 'ToF'}</span>
    <span class="word">${values[kind].word}</span><span class="small">${values[kind].small}</span>
  </a>`);
}

const CAMERA_STATES = { loading: '读取连接信息…', connecting: '正在连接摄像头…', signaling: '信令已连接，等待视频来源…',
  negotiating: '正在协商视频连接…', receiving: '视频轨道已接收，等待第一帧…', playing: '正在播放', stopped: '预览已停止', hidden: '预览已暂停', error: '预览失败',
  test: '测试源未播放', offline: '未连接', unknown: '来源待确认' };

export function inlineCameraPolicy(info) {
  if (info?.service_state === 'offline') return { play: false, state: 'offline', message: '摄像头画面暂不可用，连接后将在这里预览。' };
  if (info?.source === 'test') return { play: false, state: 'test', message: '当前设置为测试画面。接入真实摄像头画面后，将在这里显示。' };
  if (info?.source !== 'camera' || info?.service_state !== 'online') return { play: false, state: 'unknown', message: '还未确认摄像头画面来源，可前往摄像头页检查。' };
  return { play: true };
}

function useCameraSession({ api, video, active = true, inline = false }) {
  const camera = useRef(null);
  const [config, setConfig] = useState(null), [connection, setConnection] = useState({ state: 'loading' });
  const [wanted, setWanted] = useState(true), [attempt, setAttempt] = useState(0);
  const visible = useVisible();
  useEffect(() => {
    if (!active || !visible || !wanted) {
      camera.current?.stop(!active || !visible ? 'hidden' : 'stopped');
      setConnection({ state: !active || !visible ? 'hidden' : 'stopped' });
      return;
    }
    const abort = new AbortController(); let disposed = false, retry = null;
    const transport = new CameraLink({ video: video.current, onState: setConnection });
    camera.current = transport;
    setConnection({ state: 'loading' });
    setConfig(null);
    const read = async () => {
      let info;
      try {
        const result = await api('/api/camera', undefined, 3500, abort.signal);
        info = result?.ok ? result.data : { source: 'unknown', error: result?.error || '连接信息读取失败' };
      } catch (error) { info = { source: 'unknown', error: error.name === 'AbortError' ? '连接信息读取超时' : '连接信息读取失败' }; }
      if (disposed) return;
      setConfig(info);
      if (inline) {
        const policy = inlineCameraPolicy(info);
        if (!policy.play) {
          setConnection({ state: policy.state, message: policy.message });
          retry = setTimeout(read, 10000);
          return;
        }
      }
      transport.start(info?.signaling_port ?? 8443);
    };
    read();
    return () => { disposed = true; clearTimeout(retry); abort.abort(); transport.stop(); if (camera.current === transport) camera.current = null; };
  }, [api, active, visible, wanted, attempt, inline]);
  return { config, connection, wanted, reconnect: () => { setWanted(true); setAttempt(n => n + 1); },
    stop: () => { camera.current?.stop(); setWanted(false); } };
}

function CameraScreen({ connection, video, compact = false }) {
  const screen = useRef(null), [ratio, setRatio] = useState(16 / 9);
  useEffect(() => {
    const measure = () => {
      const rect = screen.current?.getBoundingClientRect();
      if (rect?.width > 0 && rect?.height > 0) setRatio(old => Math.abs(old - rect.width / rect.height) < 0.001 ? old : rect.width / rect.height);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(screen.current); measure();
    return () => observer.disconnect();
  }, []);
  const rotate = connection.metadata?.rotate || 0;
  const width = connection.width || connection.metadata?.width || 640;
  const height = connection.height || connection.metadata?.height || 480;
  const sideways = rotate === 90 || rotate === 270;
  return html`<div ref=${screen} class="camera-screen" data-playing=${connection.state === 'playing'}
    style=${compact ? null : { aspectRatio: sideways ? `${height} / ${width}` : `${width} / ${height}`,
      maxWidth: `min(100%, ${72 * (sideways ? height / width : width / height)}vh)` }}>
    <video class=${compact ? 'inline-camera-video' : 'camera-video'} ref=${video} muted playsInline autoPlay aria-label="鸭子摄像头画面"
      style=${{ width: sideways ? `${100 / ratio}%` : '100%', height: sideways ? `${100 * ratio}%` : '100%', transform: `translate(-50%, -50%) rotate(${rotate}deg)` }}></video>
    ${connection.state !== 'playing' ? html`<div class="camera-placeholder">${connection.error || connection.message || CAMERA_STATES[connection.state] || '等待画面'}</div>` : null}
  </div>`;
}

export function InlineCamera({ api, active = false }) {
  const card = useRef(null), video = useRef(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => setInView(entries.some(entry => entry.isIntersecting)), { threshold: 0 });
    observer.observe(card.current);
    return () => observer.disconnect();
  }, []);
  const { config, connection, reconnect } = useCameraSession({ api, video, active: active && inView, inline: true });
  const source = cameraSource(config, connection.producer);
  return html`<section ref=${card} class="board pd-card inline-camera" aria-labelledby="inline-camera-title">
    <div class="inline-camera-head"><h3 id="inline-camera-title">摄像头</h3>
      <span class="sensor-pill inline-camera-status" role="status" data-state=${connection.state}>${CAMERA_STATES[connection.state] || '等待画面'}</span>
      <button class="btn-light inline-camera-retry" type="button" onClick=${reconnect} disabled=${['loading', 'connecting', 'signaling', 'negotiating', 'receiving'].includes(connection.state)}>重新检测</button>
    </div>
    <${CameraScreen} connection=${connection} video=${video} compact />
    <div class="inline-camera-meta"><span class="inline-camera-source">${config ? source.label : '鸭子前方的画面'}</span><a href="#/camera">打开摄像头页 ›</a></div>
  </section>`;
}

export function CameraPage({ api }) {
  const video = useRef(null);
  const { config, connection, wanted, reconnect, stop } = useCameraSession({ api, video });
  const source = cameraSource(config, connection.producer);
  return html`<section class="sensor-page camera-page" aria-labelledby="camera-title">
    <header class="sensor-heading"><div><h1 id="camera-title">摄像头</h1><p>查看鸭子当前的视频画面。</p></div>
      <span class="sensor-pill camera-status" role="status" data-state=${connection.state}>${CAMERA_STATES[connection.state] || connection.state}</span></header>
    <div class="board sensor-board camera-source"><strong>${source.label}</strong><p>${source.note}</p>
      ${config?.error ? html`<p class="sensor-note">${config.error}；仍会尝试连接视频服务。</p>` : null}</div>
    <div class="sensor-actions">
      <button type="button" class="camera-start sensor-primary" onClick=${reconnect} disabled=${wanted && connection.state !== 'error' && connection.state !== 'stopped'}>开始预览</button>
      <button type="button" class="camera-stop" onClick=${stop} disabled=${!wanted}>停止预览</button>
      <button type="button" class="camera-reconnect" onClick=${reconnect}>重新连接</button>
    </div>
    <${CameraScreen} connection=${connection} video=${video} />
    <div class="board sensor-board sensor-metrics"><div><span>实际画面</span><b class="camera-size">${connection.state === 'playing' ? `${connection.width} × ${connection.height}` : '—'}</b></div>
      <div><span>接收情况</span><b class="camera-received">${connection.state === 'playing' ? `画面正在更新${connection.frames != null ? ` · ${connection.frames} 帧` : ''}` : '等待有效画面'}</b></div></div>
    ${connection.error ? html`<p class="sensor-error" role="alert">${connection.error}</p>` : null}
  </section>`;
}

const TOF_STATES = { loading: '正在连接', ready: '读数已更新', waiting: '等待首帧', unavailable: '传感器不可用', error: '读取失败', stale: '读数已过期', hidden: '页面隐藏，读取已暂停' };
const zoneLabel = (zone, index) => `第 ${Math.floor(index / 8) + 1} 行，第 ${index % 8 + 1} 列：${zone.kind === 'range' ? `${zone.mm} 毫米` : zone.kind === 'no_target' ? '无目标' : '无效'}；原始距离 ${zone.raw_mm} 毫米，状态 ${zone.status}`;
export function TofPage({ api }) {
  const [value, setValue] = useState(emptyTof('loading', '正在读取 ToF…'));
  const [attempt, setAttempt] = useState(0), [selected, setSelected] = useState(null);
  const visible = useVisible();
  useEffect(() => {
    if (!visible) { setValue(emptyTof('hidden', '返回这一页后自动重新读取。')); return; }
    const reader = createTofReader({ request: signal => api('/api/tof', undefined, 3500, signal), onValue: setValue });
    reader.start(); return () => reader.stop();
  }, [api, visible, attempt]);
  const ready = value.state === 'ready';
  const zones = ready ? value.zones : Array(64).fill(null);
  const selection = ready && selected != null ? value.zones[selected] : null;
  const range = Math.max(1, (value.farthest_mm || 0) - (value.nearest_mm || 0));
  return html`<section class="sensor-page tof-page" aria-labelledby="tof-title">
    <header class="sensor-heading"><div><h1 id="tof-title">ToF 距离</h1><p>8 × 8 分区距离，单位为毫米。按传感器原始行列显示，未与摄像头画面对齐。</p></div>
      <span class="sensor-pill tof-status" role="status" data-state=${value.state}>${TOF_STATES[value.state]}</span></header>
    <div class="board sensor-board tof-summary"><div><strong class="tof-sensor">${value.sensor || '传感器型号待确认'}</strong>
      ${value.simulated ? html`<span class="sensor-pill tof-simulated">模拟数据</span>` : null}</div>
      <p class="tof-reason" role=${value.state === 'error' ? 'alert' : 'status'}>${value.reason || (ready ? '颜色按这一帧的有效距离分布显示；近处更深，远处更浅。' : '等待传感器读数。')}</p></div>
    <div class="tof-content"><div class="board sensor-board tof-visual">
      <div class="tof-grid" aria-label="ToF 8 × 8 距离分区" aria-busy=${!ready}>${zones.map((zone, i) => html`<button type="button" key=${i}
        class=${`tof-zone ${zone?.kind || 'empty'}`} data-zone=${i} data-kind=${zone?.kind || 'empty'} disabled=${!zone}
        title=${zone ? zoneLabel(zone, i) : '还没有有效读数'} aria-label=${zone ? zoneLabel(zone, i) : `第 ${Math.floor(i / 8) + 1} 行，第 ${i % 8 + 1} 列：暂无读数`}
        aria-pressed=${selected === i && !!zone} onClick=${() => setSelected(i)}
        style=${zone?.kind === 'range' ? { backgroundColor: `hsl(205 70% ${42 + (zone.mm - value.nearest_mm) / range * 38}%)`, color: (zone.mm - value.nearest_mm) / range < .4 ? '#FFF' : '#081E31' } : null}>${zone?.kind === 'range' ? Math.round(zone.mm) : zone?.kind === 'no_target' ? '—' : zone ? '×' : '·'}</button>`)}</div>
      <div class="tof-legend"><span><i class="range"></i>有效距离</span><span><i class="no_target"></i>无目标 —</span><span><i class="unusable"></i>无效 ×</span></div>
      <p class="tof-zone-detail" aria-live="polite">${selection ? zoneLabel(selection, selected) : '点选任意分区，查看毫米值和原始状态。'}</p>
    </div><div class="board sensor-board sensor-metrics tof-metrics">
      <div><span>有效分区</span><b class="tof-valid">${ready ? `${value.valid_count} / 64` : '—'}</b></div>
      <div><span>最近有效距离</span><b class="tof-nearest">${ready && value.nearest_mm != null ? `${value.nearest_mm} mm` : '—'}</b></div>
      <div><span>最远有效距离</span><b class="tof-farthest">${ready && value.farthest_mm != null ? `${value.farthest_mm} mm` : '—'}</b></div>
      <div><span>更新时间</span><b class="tof-updated">${ready ? new Date(value.updatedAt).toLocaleTimeString() : '—'}</b></div>
      <div><span>传感器频率</span><b>${ready && value.hz != null ? `${value.hz} Hz` : '—'}</b></div>
    </div></div>
    <div class="sensor-actions"><button type="button" class="tof-refresh" onClick=${() => setAttempt(n => n + 1)}>重新读取</button></div>
  </section>`;
}
