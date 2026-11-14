// LAN mediad consumer. This client never opens a robot control channel or sends control methods.
const MAX_MESSAGE = 256 * 1024;
const validId = id => typeof id === 'string' && id.length > 0 && id.length <= 256;

export class CameraLink {
  constructor(options = {}) {
    this.video = options.video;
    this.onState = options.onState || (() => {});
    this.WebSocket = options.WebSocket || globalThis.WebSocket;
    this.RTCPeerConnection = options.RTCPeerConnection || globalThis.RTCPeerConnection;
    this.MediaStream = options.MediaStream || globalThis.MediaStream;
    this.location = options.location || globalThis.location;
    this.now = options.now || (() => performance.now());
    this.setTimeout = options.setTimeout || globalThis.setTimeout.bind(globalThis);
    this.clearTimeout = options.clearTimeout || globalThis.clearTimeout.bind(globalThis);
    this.generation = 0;
    this.state = { state: 'stopped', error: null, width: null, height: null, frames: 0, metadata: null, producer: null };
  }

  update(patch) { this.state = { ...this.state, ...patch }; this.onState(this.state); }

  start(port = 8443) {
    this.stop();
    const generation = this.generation;
    this.state = { state: 'connecting', error: null, width: null, height: null, frames: 0, metadata: null, producer: null };
    this.update({});
    if (!this.WebSocket || !this.RTCPeerConnection) return this.fail('这个浏览器不支持 WebRTC 预览。');
    if (!Number.isInteger(port) || port < 1 || port > 65535) return this.fail('摄像头信令端口无效。');
    try {
      const hostname = this.location.hostname;
      const host = hostname.includes(':') && !hostname.startsWith('[') ? `[${hostname}]` : hostname;
      const scheme = this.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = this.ws = new this.WebSocket(`${scheme}://${host}:${port}`);
      this.chain = Promise.resolve();
      this.queuedMessages = 0;
      ws.onopen = () => { if (this.current(generation)) this.update({ state: 'signaling' }); };
      ws.onmessage = event => {
        if (!this.current(generation)) return;
        if (typeof event.data !== 'string' || event.data.length > MAX_MESSAGE || ++this.queuedMessages > 256) {
          this.fail('摄像头信令数据超出限制。'); return;
        }
        this.chain = this.chain.then(async () => {
          if (this.current(generation)) await this.message(JSON.parse(event.data), generation);
        }).catch(error => { if (this.current(generation)) this.fail(`摄像头协商失败：${error.message || '数据无效'}`); })
          .finally(() => { if (this.current(generation)) this.queuedMessages--; });
      };
      ws.onerror = () => { if (this.current(generation)) this.fail(`连不上摄像头信令端口 ${port}，请检查 mediad 和局域网连接。`); };
      ws.onclose = () => { if (this.current(generation)) this.fail('摄像头信令连接断开，请重新连接。'); };
      this.lastFrameAt = this.startedAt = this.now();
      this.watch(generation);
    } catch (error) { this.fail(`摄像头连接失败：${error.message}`); }
  }

  current(generation, pc = null) { return this.generation === generation && !!this.ws && (!pc || this.pc === pc); }
  send(message) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(message)); }

  async message(msg, generation) {
    if (!msg || typeof msg !== 'object') throw Error('信令数据无效');
    if (msg.type === 'welcome') { this.send({ type: 'list' }); return; }
    if (msg.type === 'list') {
      if (this.sessionId || this.starting) return;
      if (!Array.isArray(msg.producers) || msg.producers.length > 64) throw Error('视频来源列表无效');
      const producers = msg.producers.filter(p => validId(p?.id) && p.meta &&
        typeof p.meta.release === 'string' && /^\d+$/.test(String(p.meta.api_version || '')) &&
        (!p.meta.kind || p.meta.kind === 'microduck'));
      if (producers.length !== 1) {
        this.fail(producers.length ? '发现多个视频来源，无法确认这只鸭子的画面。' : '没有可识别的本机视频来源，请检查 mediad。'); return;
      }
      this.starting = true;
      this.update({ producer: producers[0].meta });
      this.send({ type: 'startSession', peerId: producers[0].id });
      return;
    }
    if (msg.type === 'sessionStarted') {
      if (!this.starting || this.sessionId) return;
      if (!validId(msg.sessionId)) throw Error('会话编号无效');
      this.starting = false;
      this.sessionId = msg.sessionId;
      this.update({ state: 'negotiating' });
      this.createPeer(generation);
      return;
    }
    if (msg.type === 'peer') {
      const pc = this.pc;
      if (!pc || msg.sessionId !== this.sessionId) return;
      if (msg.sdp) {
        if (msg.sdp.type !== 'offer' || typeof msg.sdp.sdp !== 'string' || msg.sdp.sdp.length > 128 * 1024) throw Error('SDP 无效');
        await pc.setRemoteDescription({ type: 'offer', sdp: msg.sdp.sdp });
        if (!this.current(generation, pc)) return;
        for (const transceiver of pc.getTransceivers?.() || []) {
          if (transceiver.receiver?.track?.kind === 'audio') transceiver.direction = 'inactive';
        }
        const answer = await pc.createAnswer();
        if (!this.current(generation, pc)) return;
        await pc.setLocalDescription(answer);
        if (!this.current(generation, pc)) return;
        this.send({ type: 'peer', sessionId: this.sessionId, sdp: { type: 'answer', sdp: answer.sdp } });
        for (const ice of this.pendingIce.splice(0)) {
          if (!this.current(generation, pc)) return;
          await pc.addIceCandidate(ice);
        }
      } else if (msg.ice) {
        const ice = msg.ice;
        if (typeof ice.candidate !== 'string' || ice.candidate.length > 8192 ||
          !Number.isInteger(ice.sdpMLineIndex) || ice.sdpMLineIndex < 0 || ice.sdpMLineIndex > 32 || ++this.iceCount > 256) throw Error('ICE 无效');
        const candidate = { candidate: ice.candidate, sdpMLineIndex: ice.sdpMLineIndex };
        if (pc.remoteDescription) await pc.addIceCandidate(candidate);
        else this.pendingIce.push(candidate);
      }
      return;
    }
    if (msg.type === 'endSession' && this.sessionId && msg.sessionId === this.sessionId) this.fail('摄像头结束了视频会话，请重新连接。');
    else if (msg.type === 'sessionRejected') this.fail('摄像头拒绝了视频会话，请稍后重新连接。');
    else if (msg.type === 'error') this.fail('摄像头信令服务报告错误，请重新连接。');
  }

  createPeer(generation) {
    const pc = this.pc = new this.RTCPeerConnection({ iceServers: [] });
    this.pendingIce = []; this.iceCount = 0; this.tracks = new Set();
    this.lastPlaybackTime = -1; this.lastFrameCount = 0; this.hasTrack = false;
    pc.onicecandidate = event => {
      if (!this.current(generation, pc) || !event.candidate) return;
      const ice = event.candidate;
      this.send({ type: 'peer', sessionId: this.sessionId, ice: { candidate: ice.candidate, sdpMLineIndex: ice.sdpMLineIndex } });
    };
    pc.onconnectionstatechange = () => {
      if (!this.current(generation, pc)) return;
      if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) this.fail('视频连接已断开，请重新连接。');
    };
    pc.ontrack = event => {
      if (!this.current(generation, pc)) { event.track.stop(); return; }
      if (event.track.kind !== 'video') { event.track.stop(); return; }
      this.tracks.add(event.track);
      event.track.onended = () => { if (this.current(generation, pc)) this.fail('摄像头视频轨道已结束。'); };
      this.hasTrack = true;
      this.lastFrameAt = this.now();
      this.video.srcObject = event.streams?.[0] || new this.MediaStream([event.track]);
      this.update({ state: 'receiving' });
      if (this.playing) this.video.removeEventListener('playing', this.playing);
      this.playing = () => { if (this.current(generation, pc)) this.samplePlayback(); };
      this.video.addEventListener('playing', this.playing);
      this.video.play()?.catch(() => { if (this.current(generation, pc)) this.fail('浏览器没有开始播放，请点击重新连接。'); });
    };
    pc.ondatachannel = event => {
      if (!this.current(generation, pc) || this.channel) { event.channel.close(); return; }
      const channel = this.channel = event.channel;
      let requested = false;
      const requestId = `video-info-${generation}`;
      channel.onopen = () => {
        if (!this.current(generation, pc) || requested || channel.readyState !== 'open') return;
        requested = true;
        channel.send(JSON.stringify({ jsonrpc: '2.0', id: requestId, method: 'media.video', params: {} }));
      };
      channel.onmessage = event => {
        if (!this.current(generation, pc) || typeof event.data !== 'string' || event.data.length > 65536) return;
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (!msg || typeof msg !== 'object') return;
        const info = msg.id === requestId ? msg.result : msg.method === 'media.video' ? msg.params : null;
        if (info && Number.isInteger(info.width) && info.width > 0 && info.width <= 16384 &&
          Number.isInteger(info.height) && info.height > 0 && info.height <= 16384 && [0, 90, 180, 270].includes(info.rotate ?? 0)) {
          this.update({ metadata: { width: info.width, height: info.height, rotate: info.rotate ?? 0 } });
        }
      };
      if (channel.readyState === 'open') channel.onopen();
    };
  }

  samplePlayback() {
    if (!this.hasTrack || !this.video || this.video.paused) return;
    const quality = this.video.getVideoPlaybackQuality?.();
    const hasCounter = typeof quality?.totalVideoFrames === 'number';
    const count = hasCounter ? quality.totalVideoFrames : 0;
    const time = this.video.currentTime || 0;
    if (this.video.videoWidth > 0 && this.video.videoHeight > 0 &&
      (hasCounter ? count > this.lastFrameCount : time > Math.max(0, this.lastPlaybackTime))) {
      this.lastFrameAt = this.now();
      this.update({ state: 'playing', width: this.video.videoWidth, height: this.video.videoHeight,
        frames: hasCounter ? count : null });
    }
    this.lastFrameCount = count; this.lastPlaybackTime = time;
  }

  watch(generation) {
    this.timer = this.setTimeout(() => {
      if (!this.current(generation)) return;
      this.samplePlayback();
      if (this.now() - this.lastFrameAt >= 10000) this.fail(this.hasTrack ? '接收画面超时，视频已停止更新。' : '等待摄像头画面超时，请检查视频来源。');
      else this.watch(generation);
    }, 1000);
  }

  fail(error) { this.stop('error'); this.update({ error }); }

  stop(state = 'stopped') {
    this.generation++;
    this.clearTimeout(this.timer); this.timer = null;
    const ws = this.ws; this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
      if (ws.readyState === 1 && this.sessionId) {
        try { ws.send(JSON.stringify({ type: 'endSession', sessionId: this.sessionId })); } catch { /* Closing an already lost socket. */ }
      }
      try { ws.close(); } catch { /* Already closed. */ }
    }
    if (this.channel) {
      this.channel.onopen = this.channel.onmessage = this.channel.onclose = this.channel.onerror = null;
      try { this.channel.close(); } catch { /* Already closed. */ } this.channel = null;
    }
    if (this.pc) {
      this.pc.ontrack = this.pc.onicecandidate = this.pc.onconnectionstatechange = this.pc.ondatachannel = null;
      this.pc.close(); this.pc = null;
    }
    for (const track of this.tracks || []) { track.onended = null; track.stop(); }
    this.tracks = new Set();
    if (this.video) {
      if (this.playing) this.video.removeEventListener('playing', this.playing);
      for (const track of this.video.srcObject?.getTracks() || []) track.stop();
      this.video.pause(); this.video.srcObject = null;
    }
    this.playing = null; this.sessionId = null; this.starting = false; this.hasTrack = false; this.pendingIce = [];
    this.update({ state, error: null, width: null, height: null, frames: 0, metadata: null, producer: null });
  }
}
