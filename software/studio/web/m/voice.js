// 语音页：两条**互相独立**的通道，跟手柄页无关（手柄页摇杆、这页说话，各管各的）。
//
// 两条路**都不需要唤醒词**：说什么就是什么，口令对了就执行（说了「鸭鸭」也照样认，会被剥掉）。
//
//   board —— 鸭子自己的耳朵（duck-echo + 本地 Vosk）。点了「开板载聆听」就直接对它说话。
//            手机只负责开关聆听窗口、显示它认到什么。适合：安静环境、人就在鸭子旁边。
//   phone —— 手机把话变成指令再发过去。**识别在板子上做**：手机只负责录一段音，传回 Studio，
//            由 /api/asr 用板上 duck-echo 正在用的那一套 Vosk 中文模型认出来。
//            适合：环境吵、板载听不清，或者离鸭子远。
//            为什么不用浏览器自带的语音识别（Web Speech）：安卓 Chrome 那条路走谷歌服务，国内基本不通，
//            iOS 也各家不一。传回板子认，用的还是同一个模型、同一套词表，效果跟对着鸭子说一致。
// 两条都不行时还有「打字」兜底：同一个解析器、同一条下发路径。
//
// ⚠️ 手机麦克风要求页面跑在安全上下文（https 或 localhost）。http://192.168.x.x 上浏览器直接禁用
//    navigator.mediaDevices，这不是代码能绕过去的 —— 所以那种情况下写明原因，
//    并指向「输入法上的话筒键听写」这条真正能用的路。
import { html, useState, useEffect, useRef, useCallback } from '../vendor/preact-htm.module.js';
import { link } from './link.js';
import { parseUtterance, runAction, CMD_CN, openPadArea, setAudio } from './actions.js';

const MODE_KEY = 'duck.voice.mode';
const SECURE = typeof window !== 'undefined' ? window.isSecureContext !== false : false;
// 手机这条路只需要「能录音」：识别交给板子，所以看 getUserMedia 而不是 SpeechRecognition
const CAN_RECORD = typeof navigator !== 'undefined'
  && Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
const RATE = 16000;          // 板上 Vosk 模型要的采样率
const MIN_BYTES = 3200;      // 16 kHz × 2 字节：0.1 秒，比这更短什么也认不出来

// 直接从 AudioContext 取 PCM 样本。用 AudioWorklet 而不是 MediaRecorder，
// 是因为 MediaRecorder 给的是 webm/opus，而板上没有 ffmpeg 转码 —— 拿到就是能喂给 Vosk 的裸 PCM。
const PCM_WORKLET = `
class PcmTap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice());
    return true;
  }
}
registerProcessor('pcm-tap', PcmTap);
`;
// 20 ms 的静音 wav。iPhone 侧边的静音键拨开时，系统会把 AudioContext 挂起来不跑，
// 用 <audio> 播一下这段静音能把它唤醒（iOS 老毛病）。
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';

const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

/**
 * 每次长按都在按下的这一下同步新建一只 AudioContext —— 和自检页（/diag）录 2 秒用的同一套。
 * 关键是 resume() 必须在 getUserMedia **之前**完成（见 openRecorder 里的顺序）：
 * 一边等麦克风一边等 resume，某些 Chromium 内核的手机浏览器（小米、夸克）会把 context
 * 一直挂在 suspended，worklet 的 process 一次都不跑，松手就是「一个音都没采到」。
 */
function makeCtx() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  let c = null;
  try { c = new Ctx({ sampleRate: RATE }); } catch { c = null; }
  if (!c) c = new Ctx();
  return c;
}

/**
 * 给一步操作套上限。卡住时抛出「卡在哪一步」——
 * 比干等 8 秒然后给一句笼统的「麦克风没起来」有用得多，也方便远程定位。
 */
function stepTimeout(p, ms, what, onStage) {
  if (onStage) onStage(`${what}…`);
  let t = null;
  const guard = new Promise((_, rej) => {
    t = setTimeout(() => {
      const e = Error(`卡在「${what}」这一步：${Math.round(ms / 1000)} 秒都没动。`);
      e.friendly = true;
      rej(e);
    }, ms);
  });
  return Promise.race([Promise.resolve(p), guard]).finally(() => clearTimeout(t));
}

/** 优先用真实文件，实在不行才退回 blob —— iOS Safari 的 blob 版 AudioWorklet 经常加载不了 */
let tapLoadedOn = null;      // 同一个 context 里重复 registerProcessor 会抛错，记一下加载过没有
async function loadTap(ctx, onStage) {
  if (!ctx.audioWorklet) return false;
  if (tapLoadedOn === ctx) return 'worklet';
  // 先试同源的真实文件（自检页验证过是这条路成功的），每个都限时，别让网络慢拖死整条链
  for (const u of ['/m/pcm-tap.js', './pcm-tap.js']) {
    try {
      await stepTimeout(ctx.audioWorklet.addModule(u), 2500, '加载录音脚本', onStage);
      tapLoadedOn = ctx;
      return 'worklet';
    } catch { /* 换下一个 */ }
  }
  let url = null;
  try {
    url = URL.createObjectURL(new Blob([PCM_WORKLET], { type: 'application/javascript' }));
    await stepTimeout(ctx.audioWorklet.addModule(url), 2000, '加载录音脚本', onStage);
    tapLoadedOn = ctx;
    return 'worklet';
  } catch { return false; }
  finally { if (url) URL.revokeObjectURL(url); }
}

/** 设备采样率不是 16 kHz 时（多数手机是 48 kHz，iPhone 也常是 48k），线性插值降下来 */
function resample(f32, inRate) {
  if (!inRate || inRate === RATE || f32.length === 0) return f32;
  const ratio = inRate / RATE;
  const out = new Float32Array(Math.max(1, Math.floor(f32.length / ratio)));
  for (let i = 0; i < out.length; i++) {
    const p = i * ratio;
    const i0 = Math.floor(p);
    const i1 = Math.min(i0 + 1, f32.length - 1);
    const t = p - i0;
    out[i] = f32[i0] * (1 - t) + f32[i1] * t;
  }
  return out;
}

/**
 * 开一段录音。返回的对象 stop() 后给出裸 PCM（16 kHz / 16 bit / 单声道 / 小端）。
 *
 * 顺序很讲究：AudioContext 必须**在按下的那一下同步创建**（await getUserMedia 之后就脱离了
 * 用户手势，iOS 会把它一直挂起，process 一次都不跑 —— 表现就是「长按没反应」）。
 */
async function openRecorder(onStage) {
  const stage = (s) => { try { onStage && onStage(s); } catch { /* 界面的事别影响录音 */ } };
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw Error('这个浏览器不支持网页录音（AudioContext 都没有）。');
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw Error('这个页面拿不到麦克风接口：确认地址是 https 开头，且不是微信/QQ 内置浏览器。');
  }

  // 1) **在按下的这一瞬间同步新建 context 并唤醒**（不能等 getUserMedia 之后再建）。
  const ctx = makeCtx();

  // 1.5) resume() 必须在 getUserMedia 之前**完成**，不能一边等麦克风一边等它。
  //   自检页能成，就是因为顺序是「建 context → await resume → 要麦克风」；
  //   这里以前把 getUserMedia 和 resume 并行，小米/夸克这类浏览器会一直停在 suspended，
  //   worklet 的 process 一次都不跑，松手时一个样本都没有（「一个音都没采到」）。
  let wakeP = null;
  try { wakeP = ctx.resume(); } catch { wakeP = null; }
  // 再踢一脚：播一帧静音 buffer，有的实现光 resume 不算真的打开音频通路
  let kick = null;
  try {
    kick = ctx.createBufferSource();
    kick.buffer = ctx.createBuffer(1, 1, ctx.sampleRate);
    kick.connect(ctx.destination);
    kick.start(0);
  } catch { kick = null; }

  let stream = null, node = null, src = null, mute = null, closed = false, lastState = '';
  const chunks = [];
  let total = 0;
  const push = (f) => { if (f && f.length) { chunks.push(f); total += f.length; } };

  const teardown = () => {
    closed = true;
    lastState = ctx.state;            // 关掉之后就看不到当时的引擎状态了，先记下来给报错用
    try { src && src.disconnect(); } catch { /* 没连上 */ }
    try { node && node.disconnect(); } catch { /* 没连上 */ }
    try { mute && mute.disconnect(); } catch { /* 没连上 */ }
    try { kick && kick.disconnect(); } catch { /* 已经断了 */ }
    try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch { /* 已经停了 */ }
    // 这只 context 只服务这一次长按，收尾就关掉：下次按下重新建一只（和自检页一样）
    try { if (ctx.state !== 'closed') ctx.close(); } catch { /* 已经关了 */ }
  };

  try {
    // 2) 先等手势里发起的 resume 落地（有上限，别死等），**再**要麦克风。
    //    顺序不能反：一边等 getUserMedia 一边等 resume，会让这类浏览器把 context 挂在 suspended。
    if (wakeP) {
      try { await stepTimeout(wakeP, 1500, '唤醒音频', stage); } catch { /* 不 settle 也继续 */ }
    }
    if (ctx.state !== 'running') {
      try { await stepTimeout(ctx.resume(), 1200, '唤醒音频', stage); } catch { /* 认命，先连上再说 */ }
    }
    if (isIOS) {
      // iOS 侧边静音键会把录音掐掉，播 20 ms 静音能叫醒它。
      // **故意不 await**：有的浏览器 play() 的 promise 永远不 settle，一 await 整条链就卡死。
      try {
        const el = new Audio(SILENT_WAV);
        el.volume = 0;
        el.play().then(() => el.pause(), () => { /* 播不了无所谓 */ });
      } catch { /* 播不了无所谓 */ }
    }

    // 3) 音频引擎已经在跑了，这才要麦克风。约束只写 audio:true —— 自检页就是这么写的；
    //    带上 echoCancellation 那一串约束时，这版 MIUI 浏览器的 getUserMedia 会一直不 resolve。
    const micP = navigator.mediaDevices.getUserMedia({ audio: true });
    // 超时之后才姗姗来迟的流必须自己收掉：不然麦克风被我们占着，
    // 系统「正在录音」的图标一直亮，下一次长按会更起不来。
    micP.then((s) => {
      if (closed) { try { s.getTracks().forEach((t) => t.stop()); } catch { /* 已经停了 */ } }
    }, () => { /* 错误由下面的 await 统一处理 */ });
    stream = await stepTimeout(micP, 10000, '打开麦克风', stage);

    // 4) 采集节点：AudioWorklet 优先；加载不出来就退老办法（ScriptProcessor 不用联网，绝不空等）
    const kind = await loadTap(ctx, stage);
    if (kind === 'worklet') {
      node = new AudioWorkletNode(ctx, 'pcm-tap');
      node.port.onmessage = (e) => push(e.data);
    } else if (ctx.createScriptProcessor) {
      stage('准备录音（兼容模式）…');
      node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (e) => push(e.inputBuffer.getChannelData(0).slice());
    } else {
      throw Error('这个浏览器既没有 AudioWorklet 也没有 ScriptProcessor，录不了。');
    }

    src = ctx.createMediaStreamSource(stream);
    // 不接到 destination 上，Chrome 根本不去拉这个节点，process 一次都不跑
    mute = ctx.createGain();
    mute.gain.value = 0;
    src.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);

    const inRate = ctx.sampleRate || RATE;
    const tapKind = kind === 'worklet' ? 'AudioWorklet' : 'ScriptProcessor';
    stage('');
    return {
      /* 已经采到的秒数（按设备采样率算），用来给用户报「只录到 0.1 秒」 */
      seconds: () => total / (inRate || RATE),
      /* 采不到声音时报给用户看的两条线索，省得只说「一个音都没采到」 */
      diag: () => `音频引擎=${lastState || ctx.state}，采集=${tapKind}，采样率=${inRate}`,
      state: () => ctx.state,
      async stop() {
        if (closed) return new ArrayBuffer(0);
        teardown();

        // 拼成一整条，再按实际采样率降到 16 kHz
        const flat = new Float32Array(total);
        let o = 0;
        for (const c of chunks) { flat.set(c, o); o += c.length; }
        const pcm16 = resample(flat, inRate);
        const out = new Int16Array(pcm16.length);
        for (let i = 0; i < pcm16.length; i++) {
          const v = Math.max(-1, Math.min(1, pcm16[i]));
          out[i] = v < 0 ? v * 0x8000 : v * 0x7FFF;
        }
        return out.buffer;
      },
    };
  } catch (e) {
    // 失败时一定把麦克风还回去：否则系统那个「正在录音」的图标一直亮着，
    // 下一次 getUserMedia 会因为设备被占用而起不来，越试越坏。
    teardown();
    throw e;
  }
}

/** 把一段 PCM 交给板子认。返回识别出的文字 */
async function asrOnBoard(pcm) {
  const res = await fetch('/api/asr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: pcm,
  });
  if (res.status === 401) throw Error('登录过期了，回上一页重新登录。');
  if (res.status === 413) throw Error('这段太长了，说短一点。');
  let body = null;
  try { body = await res.json(); } catch { throw Error(`板子没认出来（HTTP ${res.status}）`); }
  if (!body || body.ok !== true) throw Error((body && body.error) || '板子没认出来，再说一次？');
  return (body.data && body.data.text) || '';
}

const micWhy = (e) => {
  // 我们自己抛的（带 friendly 标记）文案已经写好了，别再套一层「麦克风起不来：」
  if (e && e.friendly && e.message) return e.message;
  const n = e && e.name;
  const m = (e && e.message) ? `（${e.name || ''} ${e.message}）` : '';
  if (n === 'NotAllowedError' || n === 'SecurityError') {
    return `麦克风没给权限${m}。安卓 Chrome：点地址栏左边的锁 → 权限 → 麦克风 → 允许，再回来长按。`
      + '要是地址栏写着「不安全」，说明是自签证书被浏览器当可疑站点，先按页面里的提示装一下证书。';
  }
  if (n === 'NotFoundError' || n === 'OverconstrainedError') return '这台手机没找到可用的麦克风。';
  if (n === 'NotReadableError') return `麦克风被别的程序占着${m}：关掉录音机、微信语音这些再试。`;
  return `麦克风起不来：${(e && e.message) || e}${m}`;
};

// 权限状态直接摆在页面上，省得「长按没反应」的时候不知道是没给权限还是没采到音
const PERM_CN = { granted: '已经允许', prompt: '还没问过（第一次长按会弹）', denied: '被拒绝了', unknown: '不知道' };

const modeOf = () => {
  try { return localStorage.getItem(MODE_KEY) === 'phone' ? 'phone' : 'board'; } catch { return 'board'; }
};

function MicIcon() {
  return html`<svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="9" y="2.6" width="6" height="10.4" rx="3"/><path d="M5.6 11a6.4 6.4 0 0 0 12.8 0M12 17.4V21M8.6 21h6.8"/>
  </svg>`;
}

const KNOWN_WORDS = '站起来 · 坐下 · 走一小步 · 停下 · 前滚翻 · 低头捡东西 · 叫一声 · 打招呼 · 点头 · 摇头 · 歪头';

export function VoicePage({ st, pad, live, name, say }) {
  const [mode, setMode] = useState(modeOf());      // board | phone
  const [listening, setListening] = useState(false);  // 手机麦克风正在录
  const [boardBusy, setBoardBusy] = useState(false);
  const [heard, setHeard] = useState('');
  const [isInterim, setIsInterim] = useState(false);
  const [sent, setSent] = useState(null);          // {cn, ok}
  const [history, setHistory] = useState([]);      // [{cn, ok, at}]
  const [typed, setTyped] = useState('');
  const [err, setErr] = useState(null);
  const [ask, setAsk] = useState(null);            // 预留：现在没有唤醒词，恒为 null
  const [perm, setPerm] = useState('unknown');     // 麦克风权限：granted / prompt / denied
  const [asrSec, setAsrSec] = useState(0);         // 板子正在认的第几秒（让人知道在跑）
  const [stage, setStage] = useState('');          // 开麦克风走到哪一步了（打开麦克风…/加载录音脚本…）
  const holdRef = useRef(false);

  const audio = pad?.audio || {};
  const boardOk = audio.available === true;
  const boardOn = audio.listening === true;
  const phoneOk = SECURE && CAN_RECORD;
  // 手机这边拿不到麦克风时，给一句说清「为什么 + 怎么办」，别让人以为是坏了
  const phoneWhy = phoneOk ? null
    : !SECURE ? '这个页面是 http 打开的，手机浏览器不给网页用麦克风（要 https 才行）。'
      : '这台手机的浏览器不给网页用麦克风。';

  useEffect(() => { try { localStorage.setItem(MODE_KEY, mode); } catch { /* 隐私模式 */ } }, [mode]);

  // 进页面：先让这条连接回到「在手柄区」的状态，否则下面所有 audio 指令都会被拒
  useEffect(() => {
    link.setPadVisible(true);
    openPadArea(link);
  }, []);

  // 查一次麦克风权限，之后变了也会跟上（用户在设置里改了能立刻看到）
  useEffect(() => {
    const q = navigator.permissions && navigator.permissions.query;
    if (!q) return undefined;
    let p = null;
    navigator.permissions.query({ name: 'microphone' })
      .then((r) => { p = r; setPerm(r.state); r.onchange = () => setPerm(r.state); })
      .catch(() => { /* 有的浏览器不让查，就当不知道 */ });
    return () => { if (p) p.onchange = null; };
  }, []);

  /** 显式要一次权限：长按时系统不弹框的话，用这个按钮自己触发一次 */
  const askMic = useCallback(async () => {
    if (!CAN_RECORD) { setErr('这个浏览器压根没给网页录音的接口。'); return; }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());      // 只为要权限，拿到就还回去
      setPerm('granted');
      setErr(null);
    } catch (e) {
      setPerm('denied');
      setErr(micWhy(e));
    }
  }, []);

  const remember = useCallback((cn, ok) => {
    setHistory((h) => [{ cn, ok, at: Date.now() }, ...h].slice(0, 6));
  }, []);

  const dispatch = useCallback(async (cmd, said) => {
    setHeard(said);
    setIsInterim(false);
    setAsk(null);
    // ctx：把实时站姿递进去，「走一小步」「坐下」会自己先开策略，不用先喊一句「开始走」
    const out = await runAction(link, cmd, { getPosture: () => st?.posture });
    const cn = CMD_CN[cmd] || out.cn;
    setSent({ cn, ok: out.ok, why: out.ok ? null : (out.error || null) });
    remember(cn, out.ok);
    if (!out.ok) { setErr(out.error || '发指令没成功'); return; }
    setErr(null);
    if (out.note) say(out.note);
  }, [remember, st, say]);

  /**
   * 一句话 → 动作。两条路一视同仁：说什么就是什么，**不用喊「鸭鸭」**。
   * （说了「鸭鸭」也照样认，parseUtterance 会把它剥掉。）
   */
  const handleText = useCallback(async (text) => {
    const { cmd, said } = parseUtterance(text, { requireWake: false });
    if (cmd) { await dispatch(cmd, said); return; }
    setHeard(said || text);
    setIsInterim(false);
    setSent(null);
    setAsk(null);
    setErr(said ? `「${said}」还不会做。试试：${KNOWN_WORDS}` : '没听清，再说一遍？');
  }, [dispatch]);

  // ---- 手机麦克风：按下开始录，松开传回板子认 ----
  const recRef = useRef(null);

  const startPhone = useCallback(async () => {
    if (!phoneOk) { setErr(`${phoneWhy}用输入法上的话筒键听写，或者开板载麦克风。`); return; }
    if (recRef.current) return;                 // 已经在录了（连点两下）
    setErr(null); setHeard(''); setIsInterim(false); setSent(null); setAsk(null);
    setListening(true);
    setStage('打开麦克风…');
    const p = openRecorder(setStage);
    // **必须在这个 .then 里挂上 recRef**：它注册得比下面的 await 早，openRecorder 一成功
    // 它就先跑；要是等 await 之后再赋值，这里会以为「没人在等这段录音」，立刻 stop 掉，
    // 松手时一个样本都没有（「一个音都没采到」，但引擎其实是 running）。
    p.then((r) => {
      if (!holdRef.current) { try { r.stop(); } catch { /* 已经关了 */ } return; }  // 已经松手了，别占着麦克风
      recRef.current = r;
      setStage('');
    }, () => { /* 错误由下面的 await 统一处理 */ });
    try {
      // 每一步自己都有超时（错误里会写清卡在哪一步），
      // 这条 18 秒的只是最后兜底，防某一步的 promise 整体不 settle。
      await Promise.race([
        p,
        new Promise((_, rej) => {
          const e = Error('麦克风一直没起来（18 秒）。先点上面的「允许麦克风」再长按；'
            + '还是不行就打开 /diag 做一次自检。');
          e.friendly = true;
          setTimeout(() => rej(e), 18000);
        }),
      ]);
    } catch (e) {
      setListening(false);
      setStage('');
      if (recRef.current) { try { recRef.current.stop(); } catch { /* 已经关了 */ } }
      recRef.current = null;
      setErr(micWhy(e));
    }
  }, [phoneOk, phoneWhy]);

  const stopPhone = useCallback(async () => {
    const rec = recRef.current;
    recRef.current = null;
    setListening(false);
    setStage('');
    if (!rec) return;                            // 按下就松开了，录音还没起来
    let pcm = null;
    try { pcm = await rec.stop(); }
    catch (e) { setErr(`录音收尾失败：${(e && e.message) || e}`); return; }
    if (!pcm || pcm.byteLength < MIN_BYTES) {
      const s = typeof rec.seconds === 'function' ? rec.seconds() : 0;
      // 把到底卡在哪带上：音频引擎是不是没跑、用的哪种采集方式，一眼就能看出来
      const why = typeof rec.diag === 'function' ? `（${rec.diag()}）` : '';
      if (s >= 0.05) {
        setErr(`只录到 ${s.toFixed(2)} 秒：按住的时候说话，说完整句再松开。${why}`);
      } else if (pcm && pcm.byteLength > 0) {
        setErr(`录到的声音太小了${why}：离手机近一点，按住的时候再说。`);
      } else {
        setErr(`一个音都没采到${why}：麦克风给了但音频引擎没跑起来。`
          + '先把这个标签页关掉重开，**第一次长按稍微按久一点**（要按满一秒）；'
          + '还这样的话就用系统 Chrome 打开这个地址，小米浏览器对网页录音支持不稳。');
      }
      return;
    }
    // 板子上识别要几秒（第一句还得先加载模型），这期间页面必须「动」，否则看着就是「没反应」
    setHeard('正在认…'); setIsInterim(true);
    setAsrSec(0);
    const tick = setInterval(() => setAsrSec((n) => n + 1), 1000);
    try {
      const text = await asrOnBoard(pcm);
      if (!text) {
        setErr(`传回去了 ${(pcm.byteLength / 32000).toFixed(1)} 秒声音，板子没认出词。`
          + '离手机近一点、说慢一点，一次只说一个口令（比如「点头」）。');
        return;
      }
      handleText(text);
    } catch (e) {
      setHeard(''); setIsInterim(false);
      setErr((e && e.message) || '没认出来，再说一次？');
    } finally {
      clearInterval(tick);
    }
  }, [handleText]);

  const onDown = useCallback((e) => {
    e.preventDefault();
    if (!phoneOk) { setErr(`${phoneWhy}用输入法上的话筒键听写，或者开板载麦克风。`); return; }
    holdRef.current = true;
    startPhone();
  }, [phoneOk, phoneWhy, startPhone]);

  const onUp = useCallback(() => {
    if (holdRef.current) { holdRef.current = false; stopPhone(); }
  }, [stopPhone]);

  // ---- 板载麦克风：开关 duck-echo 的聆听 ----
  const toggleBoard = useCallback(async (on) => {
    if (boardBusy) return;
    setErr(null);
    setBoardBusy(true);
    const r = await setAudio(link, { listening: Boolean(on) });
    setBoardBusy(false);
    if (r.ok === false) setErr(r.reason || r.error || '板载语音没切换成功');
    else say(on ? '板载麦克风开了：直接对它说话' : '板载麦克风关了');
  }, [boardBusy, say]);

  // duck-echo 认出来的结果（新版 duck-echo 才带 last 字段；没有也不影响别的功能）。
  // seq 是板子上的自增号：同一条指令连说两次会得到两个不同的 seq，靠它去重而不是靠文本。
  const last = audio.last;
  const lastSeq = useRef(-1);
  useEffect(() => {
    if (!boardOn || !last || !last.command) return;
    if (typeof last.seq === 'number' && last.seq === lastSeq.current) return;
    lastSeq.current = typeof last.seq === 'number' ? last.seq : lastSeq.current;
    const cn = CMD_CN[last.command] || last.command;
    setHeard(cn);
    setIsInterim(false);
    const failed = last.accepted === false;
    setSent({ cn, ok: !failed, why: failed ? (last.reason || null) : null });
    remember(cn, !failed);
  }, [boardOn, last, remember]);

  const submitTyped = (e) => {
    e.preventDefault();
    const v = typed.trim();
    if (!v) return;
    setTyped('');
    handleText(v);
  };

  return html`<div class="m-voice-hero">
    <h1 class="m-voice-title">${name}，听我说</h1>
    <p class="m-voice-lede">${mode === 'board'
      ? html`直接对着${name}说话就行，说什么就是什么。`
      : html`按住对着手机说话，她说完松手就发过去。跟板载一样，直接说口令就行。`}</p>

    <div class="m-seg" role="group" aria-label="语音通道" style="margin:14px 0 0">
      <button aria-pressed=${mode === 'board' ? 'true' : 'false'} onClick=${() => { setMode('board'); setErr(null); setAsk(null); }}>
        板载麦克风${boardOn ? ' · 听' : ''}
      </button>
      <button aria-pressed=${mode === 'phone' ? 'true' : 'false'} onClick=${() => { setMode('phone'); setErr(null); setAsk(null); }}>
        手机说话
      </button>
    </div>

    ${mode === 'board' ? html`<div>
      <div class=${'m-mic-wrap' + (boardOn ? ' live' : '')}>
        <span class="m-halo" aria-hidden="true"></span>
        <button class=${'m-mic' + (boardOn ? ' live' : '')}
          onClick=${() => toggleBoard(!boardOn)} disabled=${boardBusy}
          aria-label=${boardOn ? '关闭板载聆听' : '打开板载聆听'}>
          <${MicIcon} />
          <span class="t1">${boardBusy ? '切换中…' : boardOn ? '板载在听' : '开板载聆听'}</span>
          <span class="t2">${boardOn ? '再点一下关掉' : '点了之后直接说话'}</span>
        </button>
      </div>
      <p class="m-tip">
        ${boardOn
          ? html`它在听：<b>直接说</b>「站起来」「点头」「走一小步」。说完点一下关掉，省电。`
          : html`用的是鸭子身上的麦克风。安静的时候最准，吵起来就换「手机说话」。`}
      </p>
      ${!boardOk ? html`<div class="m-banner bad" style="text-align:left">
        板载语音没连上：${audio.why || audio.error || 'duck-echo 没在跑'}。先用「手机说话」或打字。
      </div>` : audio.voice_ready === false ? html`<div class="m-banner bad" style="text-align:left">
        板载麦克风连上了，但语音识别还没准备好${audio.error ? `：${audio.error}` : '（模型加载中，稍等十几秒；一直这样就看一眼 duck-echo 日志）'}。
      </div>` : audio.error ? html`<div class="m-banner bad" style="text-align:left">
        板载语音报错：${audio.error}
      </div>` : null}
    </div>` : html`<div>
      <div class=${'m-mic-wrap' + (listening ? ' live' : '')}>
        <span class="m-halo" aria-hidden="true"></span>
        <button class=${'m-mic m-mic-talk' + (listening ? ' live' : '')}
          onPointerDown=${onDown} onPointerUp=${onUp} onPointerCancel=${onUp} onPointerLeave=${onUp}
          onContextMenu=${(e) => e.preventDefault()}
          aria-label="按住说话，松开发送">
          <${MicIcon} />
          <span class="t1">${listening ? (stage || '现在说') : '按住说话'}</span>
          ${listening ? html`<span class="t2">${stage ? '先别说话' : '说完松开'}</span>` : null}
        </button>
      </div>
      ${perm === 'denied' ? html`<div class="m-banner bad" style="text-align:left">
        <span>麦克风被浏览器拒绝了：地址栏左边的锁 → 权限 → 麦克风 → 允许。改完点右边再要一次。</span>
        <button class="act" onClick=${askMic}>再要一次</button>
      </div>` : null}
      ${perm !== 'granted' && perm !== 'denied' ? html`<div class="m-banner" style="text-align:left">
        <span>麦克风权限：${PERM_CN[perm]}。先点右边给它，长按才录得到声音。</span>
        <button class="act" onClick=${askMic}>允许麦克风</button>
      </div>` : null}
      <p class="m-tip">
        ${phoneOk
          ? html`按住<b>别松手</b>，等按钮变成「<b>现在说</b>」再开口，说完松开（识别在板子上做，不联网）。`
          : html`${phoneWhy}这条路暂时走不通，但下面的输入框（配上<b>输入法的话筒键</b>听写）和板载麦克风都能用。`}
      </p>
      <p class="m-tip"><a href="/diag">长按还是没反应？点这里做一次自检 →</a></p>
      ${boardOn ? html`<div class="m-banner" style="text-align:left">
        <span>板载麦克风还开着：两路一起听，同一句话可能执行两次。</span>
        <button class="act" onClick=${() => toggleBoard(false)}>关掉板载</button>
      </div>` : null}
    </div>`}

    <div class="m-sent">
      ${sent ? html`已发送：<b>${sent.cn}</b>${sent.ok ? '' : (sent.why ? `（没成功：${sent.why}）` : '（没成功）')}` : html`<span style="color:var(--text-3)">还没有指令</span>`}
    </div>

    <div class="m-heard">${mode === 'board' ? '它听到' : '听到你说'}</div>
    <div class=${'m-heard-text' + (isInterim ? ' interim' : '')}>${heard ? `「${heard}」` : '—'}</div>
    ${isInterim ? html`<div class="m-tip" style="margin-top:2px">
      板子在认… 已经 ${asrSec} 秒${asrSec >= 4 ? '（第一次要先把模型读进内存，慢一次，之后快）' : ''}
    </div>` : null}

    ${err ? html`<div class="m-banner bad" style="text-align:left">${err}</div>` : null}

    <form class="m-type" onSubmit=${submitTyped}>
      <input id="m-type" placeholder="也可以打字：站起来" value=${typed} enterkeyhint="send"
        onInput=${(e) => setTyped(e.currentTarget.value)} />
      <button class="m-btn primary" type="submit" disabled=${!typed.trim()}>发送</button>
    </form>

    <div class="m-policy-row">
      <button class="m-key pol-start" onClick=${() => dispatch('walk', '')}>开始走<small>start 策略</small></button>
      <button class="m-key pol-stop" onClick=${() => dispatch('stop', '')}>停止策略<small>stop 策略</small></button>
    </div>

    <h2 class="m-h2" style="text-align:left">它认这些</h2>
    <div class="m-card m-pad" style="text-align:left">
      <div class="m-sub">${KNOWN_WORDS}</div>
      <div class="m-tip" style="margin-bottom:0">
        ${mode === 'board'
          ? html`板载识别只认这几个口令，说话时离鸭子近一点、慢一点；它动着的时候噪声大，先让它站好再说。`
          : html`按住别松手，说完再松。中间有错字也能认出来（「站起」= 站起来）。`}
      </div>
    </div>

    ${history.length ? html`<h2 class="m-h2" style="text-align:left">刚刚</h2>
      <div class="m-card m-pad" style="text-align:left">
        <ul class="m-log">
          ${history.map((h) => html`<li class=${h.ok ? '' : 'bad'}>
            <span class="who">${h.ok ? '✓' : '×'}</span><span class="what">${h.cn}</span>
            <span class="when">${new Date(h.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
          </li>`)}
        </ul>
      </div>` : null}

    <p class="m-tip">
      语音只在你说完那一刻下发一次指令；识别不到就什么都不做，不会让鸭子乱动。
      <b>卸力</b>永远在右上角。
    </p>
  </div>`;
}
