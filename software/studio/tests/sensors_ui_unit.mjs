import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const uiURL = new URL('../web/sensors.js', import.meta.url);
const rtcURL = new URL('../web/camera-link.js', import.meta.url);
const ui = existsSync(uiURL) ? await import(uiURL) : {};
const rtc = existsSync(rtcURL) ? await import(rtcURL) : {};
const frame = () => ({ state: 'ready', connected: true, sensor: 'VL53L5CX', rows: 8, cols: 8,
  hz: 10, seq: 3, age_ms: 0, simulated: false, distance_mm: Array(64).fill(500), status: Array(64).fill(5) });
const tick = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

test('inline camera only starts a configured camera with a live service', () => {
  assert.equal(typeof ui.inlineCameraPolicy, 'function');
  assert.equal(ui.inlineCameraPolicy({ source: 'camera', service_state: 'online' }).play, true);
  assert.equal(ui.inlineCameraPolicy({ source: 'test', service_state: 'online' }).state, 'test');
  assert.equal(ui.inlineCameraPolicy({ source: 'camera', service_state: 'offline' }).state, 'offline');
  assert.equal(ui.inlineCameraPolicy({ source: 'unknown', service_state: 'online' }).state, 'unknown');
  assert.equal(ui.inlineCameraPolicy(null).play, false);
});

test('ToF keeps valid ranges, no target and unusable readings distinct', () => {
  assert.equal(typeof ui.tofSnapshot, 'function');
  const f = frame();
  f.status[1] = 9; f.distance_mm[1] = 900;
  f.status[2] = 255; f.distance_mm[2] = 0;
  f.status[3] = 0; f.distance_mm[3] = 0;
  f.distance_mm[4] = -2;
  const v = ui.tofSnapshot(f);
  assert.equal(v.zones.length, 64);
  assert.deepEqual(v.zones.slice(1, 5).map(z => z.kind), ['range', 'no_target', 'unusable', 'unusable']);
  assert.equal(v.zones[2].mm, null);
  assert.equal(v.zones[4].raw_mm, -2);
  assert.equal(v.valid_count, 61);
  assert.equal(v.nearest_mm, 500);
  assert.equal(v.farthest_mm, 900);
});

test('ToF rejects malformed and stale frames and clears absent data', () => {
  assert.equal(typeof ui.tofSnapshot, 'function');
  for (const delta of [{ rows: 4 }, { status: [5] }, { distance_mm: [5] },
    { distance_mm: Array(64).fill(NaN) }, { status: Array(64).fill('5') }]) {
    assert.equal(ui.tofSnapshot({ ...frame(), ...delta }).state, 'error');
  }
  assert.equal(ui.tofSnapshot({ ...frame(), age_ms: 4000 }).state, 'stale');
  assert.deepEqual(ui.tofSnapshot({ ...frame(), state: 'waiting' }).zones, []);
  assert.equal(ui.tofSnapshot({ ...frame(), status: Array(64).fill(255) }).nearest_mm, null);
});

test('source labels never promote configuration or a test image to hardware proof', () => {
  assert.equal(typeof ui.cameraSource, 'function');
  assert.match(ui.cameraSource({ source: 'test' }, {}).label, /测试/);
  assert.match(ui.cameraSource({ source: 'camera' }, {}).label, /配置/);
  assert.match(ui.cameraSource({ source: 'camera' }, { simulated: 'true' }).label, /模拟/);
  assert.match(ui.cameraSource({ source: 'unknown' }, {}).label, /未知/);
  assert.equal(ui.cameraSource({ source: 'camera' }, {}).verified, false);
  const detected = ui.cameraSource({ source: 'camera', auto: { camera_present: true } }, {});
  assert.match(detected.note, /已识别 IMX219/);
  assert.equal(detected.verified, false);
});

function clock() {
  let now = 0, id = 0;
  const pending = new Map();
  return { now: () => now, setTimeout: (fn, ms) => { pending.set(++id, { fn, at: now + ms }); return id; },
    clearTimeout: id => pending.delete(id), advance(ms) {
      now += ms;
      const due = [...pending].filter(([, timer]) => timer.at <= now);
      for (const [id, timer] of due) { pending.delete(id); timer.fn(); }
    }, get count() { return pending.size; } };
}

test('status cards distinguish hardware data, simulation and camera service evidence', () => {
  assert.equal(typeof ui.cameraBandStatus, 'function');
  assert.equal(typeof ui.tofBandStatus, 'function');
  assert.equal(ui.cameraBandStatus({ source: 'camera' }).state, 'unknown');
  assert.equal(ui.cameraBandStatus({ source: 'camera', service_state: 'online' }).word, '服务在线');
  assert.equal(ui.cameraBandStatus({ source: 'test', service_state: 'online' }).word, '测试画面');
  assert.equal(ui.cameraBandStatus({ source: 'test', service_state: 'offline' }).state, 'offline');
  assert.equal(ui.tofBandStatus(frame()).word, '500 mm');
  assert.equal(ui.tofBandStatus({ ...frame(), status: Array(64).fill(255) }).word, '无有效距离');
  assert.equal(ui.tofBandStatus({ ...frame(), simulated: true }).word, '模拟数据');
  assert.equal(ui.tofBandStatus({ ...frame(), state: 'unavailable', connected: true }).word, '服务在线');
  assert.equal(ui.tofBandStatus({ state: 'unavailable', connected: false }).state, 'offline');
  assert.equal(ui.tofBandStatus({ state: 'waiting', connected: true }).word, '等待数据');
  assert.equal(ui.tofBandStatus({ ...frame(), age_ms: 5000 }).state, 'unknown');
  assert.equal(ui.tofBandStatus({ ...frame(), status: [5] }).state, 'error');
});

test('band reader checks once per ten seconds and cancels both independent requests', async () => {
  assert.equal(typeof ui.createSensorBandReader, 'function');
  const c = clock(), values = [], calls = [];
  const reader = ui.createSensorBandReader({ ...c, onValue: v => values.push(v), request: async (kind, signal) => {
    calls.push({ kind, signal });
    return { ok: true, data: kind === 'camera' ? { source: 'test', service_state: 'online' } : frame() };
  } });
  reader.start(); await tick();
  assert.equal(calls.length, 2);
  assert.equal(values.at(-1).camera.word, '测试画面');
  assert.equal(values.at(-1).tof.word, '500 mm');
  c.advance(3000); await tick(); assert.equal(values.at(-1).tof.state, 'unknown');
  c.advance(6999); await tick(); assert.equal(calls.length, 2);
  c.advance(1); await tick(); assert.equal(calls.length, 4);
  reader.stop(); assert.equal(c.count, 0);
  c.advance(20000); await tick(); assert.equal(calls.length, 4);
  const finishes = [], signals = [];
  const pendingReader = ui.createSensorBandReader({ ...c, onValue: v => values.push(v), request: (kind, signal) => {
    signals.push(signal); return new Promise(resolve => finishes.push(resolve));
  } });
  pendingReader.start(); await tick(); const before = values.length;
  pendingReader.stop(); assert.equal(signals.every(s => s.aborted), true);
  for (const finish of finishes) finish({ ok: true, data: frame() });
  await tick(); assert.equal(values.length, before); assert.equal(c.count, 0);
});

test('band expires old success during a hanging refresh and never accepts stale HTTP data', async () => {
  assert.equal(typeof ui.createSensorBandReader, 'function');
  const c = clock(), values = []; let calls = 0;
  const reader = ui.createSensorBandReader({ ...c, onValue: v => values.push(v), request: async kind => {
    if (++calls > 2) return new Promise(() => {});
    return { ok: true, data: kind === 'camera' ? { source: 'camera', service_state: 'online' } : frame() };
  } });
  reader.start(); await tick(); c.advance(10000); await tick(); c.advance(10000); await tick();
  assert.equal(values.at(-1).camera.state, 'unknown');
  assert.equal(values.at(-1).tof.state, 'unknown'); reader.stop();
  const finishes = [];
  const slow = ui.createSensorBandReader({ ...c, onValue: v => values.push(v), request: () => new Promise(r => finishes.push(r)) });
  slow.start(); await tick(); c.advance(3100);
  for (const finish of finishes) finish({ ok: true, data: frame() });
  await tick(); assert.notEqual(values.at(-1).tof.state, 'online'); slow.stop();
});

test('visible polling aborts and ignores pending responses when stopped', async () => {
  assert.equal(typeof ui.createTofReader, 'function');
  const c = clock(), states = []; let resolve, signal;
  const reader = ui.createTofReader({ ...c, onValue: s => states.push(s),
    request: s => { signal = s; return new Promise(r => { resolve = r; }); } });
  reader.start(); await tick();
  reader.stop(); assert.equal(signal.aborted, true);
  resolve({ ok: true, data: frame() }); await tick();
  assert.equal(states.some(s => s.state === 'ready'), false);
  assert.equal(c.count, 0);
});

test('poll failure and a hanging refresh immediately expire old readings', async () => {
  assert.equal(typeof ui.createTofReader, 'function');
  const c = clock(), states = []; let calls = 0;
  const reader = ui.createTofReader({ ...c, onValue: s => states.push(s), request: async () => {
    if (++calls === 1) return { ok: true, data: frame() };
    return new Promise(() => {});
  } });
  reader.start(); await tick(); assert.equal(states.at(-1).state, 'ready');
  c.advance(500); await tick(); c.advance(3100); await tick();
  assert.equal(states.at(-1).state, 'stale');
  assert.deepEqual(states.at(-1).zones, []);
  reader.stop();
  const failed = ui.createTofReader({ ...c, onValue: s => states.push(s), request: async () => { throw Error('断开'); } });
  failed.start(); await tick(); assert.equal(states.at(-1).state, 'error');
  assert.deepEqual(states.at(-1).zones, []); failed.stop();
});

test('HTTP transit time cannot turn an expired ToF response into current readings', async () => {
  assert.equal(typeof ui.createTofReader, 'function');
  const c = clock(), states = []; let resolve;
  const reader = ui.createTofReader({ ...c, onValue: value => states.push(value),
    request: () => new Promise(finish => { resolve = finish; }) });
  reader.start(); c.advance(3100); resolve({ ok: true, data: frame() }); await tick();
  assert.equal(states.at(-1).state, 'stale');
  assert.deepEqual(states.at(-1).zones, []); reader.stop();
});

test('ToF freshness expires after server age plus HTTP elapsed time reaches three seconds', async () => {
  assert.equal(typeof ui.createTofReader, 'function');
  const c = clock(), states = []; let resolve;
  const reader = ui.createTofReader({ ...c, onValue: value => states.push(value),
    request: () => new Promise(finish => { resolve = finish; }) });
  reader.start(); c.advance(2500); resolve({ ok: true, data: { ...frame(), age_ms: 200 } }); await tick();
  assert.equal(states.at(-1).state, 'ready');
  assert.equal(states.at(-1).age_ms, 2700);
  c.advance(299); assert.equal(states.at(-1).state, 'ready');
  c.advance(1); assert.equal(states.at(-1).state, 'stale');
  assert.deepEqual(states.at(-1).zones, []); reader.stop();
});

test('HTTP elapsed accounting preserves invalid server age validation', async () => {
  assert.equal(typeof ui.createTofReader, 'function');
  const c = clock(), states = [];
  for (const age_ms of [undefined, null, -1, '0', NaN]) {
    const reader = ui.createTofReader({ ...c, onValue: value => states.push(value),
      request: async () => ({ ok: true, data: { ...frame(), age_ms } }) });
    reader.start(); await tick();
    assert.equal(states.at(-1).state, 'error'); reader.stop();
  }
});

function harness() {
  assert.equal(typeof rtc.CameraLink, 'function');
  const c = clock(), states = [], sockets = [], peers = [], events = new Map();
  const track = { kind: 'video', stopped: false, stop() { this.stopped = true; }, addEventListener() {}, removeEventListener() {} };
  const stream = { getTracks: () => [track] };
  const video = { srcObject: null, videoWidth: 640, videoHeight: 480, currentTime: 0, paused: false,
    addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name),
    async play() { this.paused = false; }, pause() { this.paused = true; } };
  class WS {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; this.closed = true; }
    open() { this.readyState = 1; this.onopen?.(); }
    message(msg) { this.onmessage?.({ data: JSON.stringify(msg) }); }
  }
  class PC {
    constructor() { peers.push(this); this.remoteDescription = null; this.calls = []; }
    async setRemoteDescription(sdp) { this.calls.push('remote'); if (this.waitRemote) await this.waitRemote; this.remoteDescription = sdp; }
    async createAnswer() { this.calls.push('answer'); return { type: 'answer', sdp: 'answer-sdp' }; }
    async setLocalDescription() { this.calls.push('local'); }
    async addIceCandidate(ice) { this.calls.push(['ice', ice.candidate]); }
    close() { this.closed = true; }
  }
  const link = new rtc.CameraLink({ ...c, video, onState: s => states.push(s), WebSocket: WS, RTCPeerConnection: PC,
    location: { protocol: 'http:', hostname: 'duck.local' } });
  async function session() {
    link.start(8443); const ws = sockets.at(-1); ws.open();
    ws.message({ type: 'welcome', peerId: 'consumer' }); await tick();
    ws.message({ type: 'list', producers: [{ id: 'duck', meta: { release: 'test', api_version: '40', simulated: 'true' } }] }); await tick();
    ws.message({ type: 'sessionStarted', sessionId: 'session-1' }); await tick();
    return { ws, pc: peers.at(-1) };
  }
  return { c, states, sockets, peers, video, track, stream, events, link, session };
}

test('camera uses LAN welcome/list/startSession protocol, playback only follows a video track', async () => {
  const h = harness(); const { ws, pc } = await h.session();
  assert.equal(ws.url, 'ws://duck.local:8443');
  assert.deepEqual(ws.sent.slice(0, 2), [{ type: 'list' }, { type: 'startSession', peerId: 'duck' }]);
  assert.equal(h.states.at(-1).state, 'negotiating');
  pc.connectionState = 'connected'; pc.onconnectionstatechange();
  assert.notEqual(h.states.at(-1).state, 'playing');
  pc.ontrack({ track: h.track, streams: [h.stream] }); await tick();
  assert.equal(h.video.srcObject, h.stream);
  assert.equal(h.states.at(-1).state, 'receiving');
  h.video.currentTime = 1; h.events.get('playing')?.(); h.c.advance(1000);
  assert.equal(h.states.at(-1).state, 'playing');
  assert.equal(h.states.at(-1).width, 640);
  assert.equal(h.states.at(-1).frames, null, 'a playback-clock sample is not a decoded frame count');
  h.link.stop();
});

test('camera serializes SDP/ICE and ignores mismatched session messages', async () => {
  const h = harness(); const { ws, pc } = await h.session();
  let release; pc.waitRemote = new Promise(r => { release = r; });
  ws.message({ type: 'peer', sessionId: 'wrong', sdp: { type: 'offer', sdp: 'wrong' } });
  ws.message({ type: 'peer', sessionId: 'session-1', ice: { candidate: 'early', sdpMLineIndex: 0 } });
  ws.message({ type: 'peer', sessionId: 'session-1', sdp: { type: 'offer', sdp: 'offer' } });
  ws.message({ type: 'peer', sessionId: 'session-1', ice: { candidate: 'later', sdpMLineIndex: 0 } });
  await tick(); assert.deepEqual(pc.calls, ['remote']);
  release(); await tick();
  assert.deepEqual(pc.calls, ['remote', 'answer', 'local', ['ice', 'early'], ['ice', 'later']]);
  assert.ok(ws.sent.some(m => m.type === 'peer' && m.sessionId === 'session-1' && m.sdp?.type === 'answer'));
  ws.message({ type: 'endSession', sessionId: 'wrong' }); await tick();
  assert.equal(pc.closed, undefined); h.link.stop();
});

test('received data channel sends only media.video and accepts its matching metadata', async () => {
  const h = harness(); const { pc } = await h.session(); const sent = [];
  const channel = { readyState: 'open', send: raw => sent.push(JSON.parse(raw)), close() { this.closed = true; } };
  pc.ondatachannel({ channel }); channel.onopen?.();
  assert.equal(sent.length, 1); assert.equal(sent[0].method, 'media.video');
  channel.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id: sent[0].id, result: { width: 1280, height: 720, rotate: 90 } }) });
  assert.equal(h.states.at(-1).metadata.width, 1280);
  assert.notEqual(h.states.at(-1).state, 'playing');
  h.link.stop(); assert.equal(channel.closed, true);
});

test('stopping during SDP cannot send an answer, and reconnect disposes the old connection', async () => {
  const h = harness(); const { ws, pc } = await h.session();
  let release; pc.waitRemote = new Promise(r => { release = r; });
  ws.message({ type: 'peer', sessionId: 'session-1', sdp: { type: 'offer', sdp: 'offer' } }); await tick();
  pc.ontrack({ track: h.track, streams: [h.stream] });
  h.link.stop(); release(); await tick();
  assert.equal(ws.sent.some(m => m.sdp), false);
  assert.equal(pc.closed, true); assert.equal(ws.closed, true); assert.equal(h.track.stopped, true);
  assert.equal(h.video.srcObject, null); assert.equal(h.c.count, 0);
  h.link.start(8443); assert.equal(h.sockets.length, 2); h.link.stop();
});

test('no producer, signaling error and receive timeout are failures rather than playback', async () => {
  const h = harness(); h.link.start(8443); const ws = h.sockets[0]; ws.open();
  ws.message({ type: 'list', producers: [] }); await tick(); assert.equal(h.states.at(-1).state, 'error');
  assert.equal(ws.closed, true); h.link.start(8443); h.sockets.at(-1).onerror();
  assert.equal(h.states.at(-1).state, 'error');
  await h.session(); h.c.advance(11000); await tick(); assert.equal(h.states.at(-1).state, 'error');
  assert.match(h.states.at(-1).error, /画面|超时/); h.link.stop();
});

test('oversized signaling and ambiguous LAN producers are rejected', async () => {
  const h = harness(); h.link.start(8443); let ws = h.sockets.at(-1); ws.open();
  ws.message({ type: 'list', producers: [{ id: 'a' }, { id: 'b' }] }); await tick();
  assert.equal(h.states.at(-1).state, 'error');
  h.link.start(8443); ws = h.sockets.at(-1); ws.open(); ws.onmessage({ data: 'x'.repeat(300000) }); await tick();
  assert.equal(h.states.at(-1).state, 'error'); h.link.stop();
});

test('a frozen decoded frame counter expires even while the video clock advances', async () => {
  const h = harness(); const { pc } = await h.session();
  h.video.getVideoPlaybackQuality = () => ({ totalVideoFrames: 1 });
  pc.ontrack({ track: h.track, streams: [h.stream] }); await tick();
  h.video.currentTime = 1; h.c.advance(1000); assert.equal(h.states.at(-1).state, 'playing');
  for (let i = 2; i < 13; i++) { h.video.currentTime = i; h.c.advance(1000); }
  assert.equal(h.states.at(-1).state, 'error'); assert.match(h.states.at(-1).error, /超时/);
  h.link.stop();
});

test('an offered audio transceiver stays inactive when answering a video session', async () => {
  const h = harness(); const { ws, pc } = await h.session();
  const audio = { receiver: { track: { kind: 'audio' } }, direction: 'recvonly' };
  const video = { receiver: { track: { kind: 'video' } }, direction: 'recvonly' };
  pc.getTransceivers = () => [audio, video];
  ws.message({ type: 'peer', sessionId: 'session-1', sdp: { type: 'offer', sdp: 'offer-with-audio' } }); await tick();
  assert.equal(audio.direction, 'inactive'); assert.equal(video.direction, 'recvonly'); h.link.stop();
});

test('invalid data channel JSON values cannot become metadata or crash the receiver', async () => {
  const h = harness(); const { pc } = await h.session();
  const channel = { readyState: 'open', send() {}, close() {} };
  pc.ondatachannel({ channel });
  for (const data of ['null', 'false', '4', '{}', '{bad']) assert.doesNotThrow(() => channel.onmessage({ data }));
  assert.equal(h.states.at(-1).metadata, null); h.link.stop();
});

test('repeated video tracks do not retain duplicate video event listeners', async () => {
  const h = harness(); const { pc } = await h.session(); const listeners = new Set();
  h.video.addEventListener = (name, fn) => { if (name === 'playing') listeners.add(fn); };
  h.video.removeEventListener = (name, fn) => { if (name === 'playing') listeners.delete(fn); };
  pc.ontrack({ track: h.track, streams: [h.stream] });
  pc.ontrack({ track: h.track, streams: [h.stream] });
  assert.equal(listeners.size, 1); h.link.stop(); assert.equal(listeners.size, 0);
});

test('default browser timers retain the global receiver when stored by CameraLink', () => {
  assert.equal(typeof rtc.CameraLink, 'function');
  const nativeSetTimeout = globalThis.setTimeout;
  const nativeClearTimeout = globalThis.clearTimeout;
  const called = [];
  try {
    globalThis.setTimeout = function () {
      assert.equal(this, globalThis, 'browser setTimeout requires a Window receiver'); called.push('set'); return 1;
    };
    globalThis.clearTimeout = function () {
      assert.equal(this, globalThis, 'browser clearTimeout requires a Window receiver'); called.push('clear');
    };
    const link = new rtc.CameraLink();
    assert.doesNotThrow(() => link.stop());
    assert.doesNotThrow(() => link.watch(link.generation));
    assert.deepEqual(called, ['clear', 'set']);
  } finally {
    globalThis.setTimeout = nativeSetTimeout;
    globalThis.clearTimeout = nativeClearTimeout;
  }
});
