// 动作词典 + 一句话解析。总览、语音、手柄三页共用这一份，别各写一套。
//
// 下发方式统一走 Studio 已有的 pad.op（software/studio/README.md「前后端协议」、web/pad.js 顶部注释）：
//   stand / sit / walk / walk_stop / skill {name} / sound {tag} / action_start·hold·stop
// 不新造 HTTP 接口：手势、语音、按钮三条路最后都汇到同一个 _motion_cmd，互斥和看门狗都是现成的。

export const ACTIONS = {
  stand: { label: '站起来', op: { op: 'stand' }, cn: '站起来' },
  walk: { label: '开始走', op: { op: 'walk' }, cn: '开始走' },
  // 后端没有 step 这个 op：走一小步 = 先确保策略在跑，再推 0.8 秒前进、停（见 stepOnce）
  step: { label: '走一小步', special: 'step', cn: '走一小步' },
  stop: { label: '停下', op: { op: 'walk_stop' }, cn: '停下，站好' },
  sit: { label: '坐下', op: { op: 'sit' }, cn: '坐下' },
  roulade: { label: '前滚翻', op: { op: 'skill', name: 'roulade' }, cn: '前滚翻' },
  ground_pick: { label: '低头捡东西', op: { op: 'skill', name: 'ground_pick' }, cn: '低头捡东西' },
  chirp: { label: '叫一声', op: { op: 'sound', tag: 'chirp' }, cn: '叫一声' },
  greet: { label: '打招呼', op: { op: 'sound', tag: 'greet' }, cn: '打招呼' },
};

// 点一下头 / 摇两下头这类「按住才做」的动作：发一次按下，按住 duration 毫秒，然后松开
const HELD = {
  nod: { id: 'nod', label: '点头', cn: '点点头', ms: 1100 },
  shake: { id: 'shake', label: '摇头', cn: '摇摇头', ms: 1100 },
  tilt: { id: 'tilt', label: '歪头', cn: '歪歪头', ms: 900 },
  mouth: { id: 'mouth', label: '张嘴', cn: '张嘴', ms: 900 },
};

// 手柄页的动作分组。站和走单独一组：用户最容易卡在「站着不动 / 摇杆没反应」上
// 「开始走」不在组里：它是开走路策略的总开关，手柄页把它单独拎到摇杆下面、
// 贴品牌黄底（gamepad.js 的 m-start-row），别让它和普通动作键混在一起被漏掉。
export const ACTION_GROUPS = [
  { title: '站住与走路', keys: ['stand', 'step', 'stop'] },
  { title: '坐下与技能', keys: ['sit', 'roulade', 'ground_pick'] },
  { title: '声音', keys: ['chirp', 'greet'] },
  { title: '转头', keys: ['nod', 'shake', 'tilt'] },
];

/** 任意动作键 → 按钮上的字（ACTIONS 和 HELD 共用一套取法） */
export function actionLabel(key) {
  const hit = ACTIONS[key] || HELD[key];
  return (hit && hit.label) || key;
}

/**
 * 现在最该点的那一个键（手柄页给它描一圈绿边）。每条都对应后端一条真实的准入条件：
 * holding = 站着但策略没跑 → 摇杆推了不动，差「开始走」；sitting / limp → 差「站起来」。
 * 用户最容易卡的就是「推了没反应」，所以要让他一眼看到下一步该按哪个。
 */
export function nextAction(st) {
  if (!st) return null;
  if (st.estop && st.estop.state === 'confirmed') return 'stand';
  switch (st.posture) {
    case 'holding': return 'walk';
    case 'sitting': return 'stand';
    case 'limp': return 'stand';
    case 'walking': return 'stop';
    default: return null;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 摇杆一帧。-1..1，y 向上为负 = 前进（pad_drive.sticks_to_intent 的 LY = -ly）。后端 0.5 秒收不到就自己刹车 */
function pushStick(link, { x = 0, y = 0 } = {}, seq) {
  return link.cmd('pad.op', { op: 'sticks', mode: 'drive', lx: x, ly: y, rx: 0, ry: 0, seq }, 5000);
}

const whyOf = (r) => String((r && (r.reason || r.error)) || '');
const good = (r) => Boolean(r) && r.ok !== false;
// 后端把「已经在走」也当成失败回话（这时按钮已经换成「停下」了），对调用方来说这是成功
const isReady = (r) => good(r) || /准备好走|已经在走/.test(whyOf(r));
// 后端一次只发一个独占指令，前一个没回话会把后一个顶回来；站起 / 回中也都要等几拍
const BUSY_WHY = /还没回话|等一下|等一秒|还在回站姿|还在站起来|正在起来|站稳了再点/;
// 策略真在跑的两种站姿（pad_drive.DRIVING）。holding = 站着但策略没跑，摇杆推了不动
const DRIVING = new Set(['policy', 'walking']);

/**
 * 等「策略跑起来」。给了 ctx.getPosture 就盯着状态；没给（比如总览页拿不到实时状态）就干等一小会儿。
 * robot.enable 发出去到 posture 变 policy 之间有一两秒，这时候推摇杆 / 点坐下都会被顶回来。
 */
async function waitDriving(ctx, ms) {
  const get = ctx && ctx.getPosture;
  if (typeof get !== 'function') { await sleep(Math.min(ms, 1800)); return null; }
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    if (DRIVING.has(get())) return true;
    await sleep(150);
  }
  return false;
}

/**
 * 让走路策略跑起来，也就是「开始走」。
 * 官方规矩：站姿分两种——holding（站着但策略没跑，摇杆推了也不动）和 policy（准备好走了）。
 * 所以「坐下」「走一小步」「推摇杆」之前都得先过这一关，否则用户只会看到「没反应」。
 */
async function ensurePolicy(link, ctx, tries = 4) {
  for (let i = 0; i < tries; i++) {
    const r = await link.cmd('pad.op', { op: 'walk' }, 20000);
    if (isReady(r)) return { ok: true, already: !good(r) };
    const why = whyOf(r);
    if (BUSY_WHY.test(why)) { await sleep(900); continue; }
    if (/坐/.test(why)) {
      // 坐着：先站起来（走 sit_toggle），站稳了再开策略。后端坐着又不给站时（策略没跑且卸了力）
      // 会把「先把它扶起来放平」那句原话抛回来，这里照原话报给用户，别替它瞎指挥。
      const s = await link.cmd('pad.op', { op: 'stand' }, 25000);
      if (!good(s) && !/已经站着/.test(whyOf(s))) return { ok: false, error: whyOf(s) || '站不起来' };
      await waitDriving(ctx, 15000);
      const p = await ensurePolicy(link, ctx, 3);
      return p.ok ? { ...p, stood: true } : p;
    }
    return { ok: false, error: why || '开始走没成功' };
  }
  return { ok: false, error: '开始走一直没排上队，等一下再点' };
}

/** 真的走一小步：策略 → 0.85 秒前进 → 停。语音的「走一小步」也是这么做的（duck-echo 里 STEP_S） */
async function stepOnce(link, ctx, power = 0.55) {
  const p = await ensurePolicy(link, ctx);
  if (!p.ok) return { ok: false, cn: '走一小步', error: p.error };
  await waitDriving(ctx, 5000);            // 策略刚开的那几拍，速度还接不上
  await sleep(p.stood ? 150 : 220);
  let seq = 0;
  const t0 = performance.now();
  while (performance.now() - t0 < 850) {
    await pushStick(link, { y: -power }, ++seq);
    await sleep(95);
  }
  await link.cmd('pad.op', { op: 'stop', seq: ++seq }, 5000);
  return { ok: true, cn: '走一小步', error: null, note: p.stood ? '先站起来，再走了一步' : null };
}

/**
 * 坐下。官方要求策略在跑才给坐，所以 holding 时会回「坐下要策略在跑：先点开始走」。
 * 这句话让用户去点一个他以为不存在的按钮——干脆这里替他补上：先开始走，再坐一次。
 */
async function sitSmart(link, ctx) {
  for (let i = 0; i < 4; i++) {
    const r = await link.cmd('pad.op', { op: 'sit' }, 25000);
    const why = whyOf(r);
    if (good(r) || /已经坐着/.test(why)) {
      return { ok: true, cn: '坐下', error: null, note: /已经坐着/.test(why) ? '已经坐着了' : null };
    }
    if (BUSY_WHY.test(why)) { await sleep(900); continue; }        // 上一个指令还没回话
    if (!/策略/.test(why)) return { ok: false, cn: '坐下', error: why };
    const p = await ensurePolicy(link, ctx);
    if (!p.ok) return { ok: false, cn: '坐下', error: p.error };
    await waitDriving(ctx, 6000);          // 开完策略还要等它真的站稳，不然坐下会被回一句「等一秒」
  }
  return { ok: false, cn: '坐下', error: '坐下一直没排上队，再点一次试试' };
}

let press = 0;

/**
 * 执行一个动作名（ACTIONS 或 HELD 里的键）。返回 {ok, cn, error, note}
 * @param ctx 可选：{getPosture()} —— 能读到实时站姿时，等待就按状态来，不用瞎猜时间
 */
export async function runAction(link, key, ctx) {
  const hit = ACTIONS[key];
  if (hit) {
    if (hit.special === 'step') return stepOnce(link, ctx);
    if (key === 'sit') return sitSmart(link, ctx);
    if (key === 'walk') {
      const p = await ensurePolicy(link, ctx);
      if (!p.ok) return { ok: false, cn: '开始走', error: p.error };
      return { ok: true, cn: '开始走', error: null,
        note: p.already ? '已经在走着了，推左摇杆就会动' : (p.stood ? '先站起来，现在开始走：推左摇杆' : '开始走了：推左摇杆就会动') };
    }
    const r = await link.cmd('pad.op', hit.op, 20000);
    return { ok: r.ok !== false, cn: hit.cn, error: r.ok === false ? (r.reason || r.error) : null };
  }
  const held = HELD[key];
  if (!held) return { ok: false, cn: key, error: '不认识这个动作' };
  const tag = ++press;
  const start = await link.cmd('pad.op', { op: 'action_start', id: held.id, press: tag }, 8000);
  if (start.ok === false) return { ok: false, cn: held.cn, error: start.reason || start.error };
  const t0 = performance.now();
  const hb = setInterval(() => { link.cmd('pad.op', { op: 'action_hold', press: tag }, 8000); }, 150);
  await new Promise((r) => setTimeout(r, held.ms));
  clearInterval(hb);
  await link.cmd('pad.op', { op: 'action_stop' }, 8000);
  return { ok: true, cn: held.cn, error: null, ms: performance.now() - t0 };
}

// ---------------- 语音开关（pad.op 的 audio 分支） ----------------

// 后端（pad.py 的 audio 分支）要求「这条连接没被标成离开手柄区」，否则回「请先回到手柄遥控区操作语音」。
// 手机页是单页应用，一条 ws 走遍五个 tab：从手柄切过来时，手柄页卸载会发 drive {on:false}，
// 正好把这条连接标成离开 —— 语音开关就再也点不动了。所以进语音页先发一次 drive {open:true}
// （后端只解开标记，不抢方向盘、不碰舵机），真撞上了再补一次重试。
export const PAD_AREA_WHY = /回到手柄|不在手柄|已经断开/;

export function openPadArea(link) {
  return link.cmd('pad.op', { op: 'drive', open: true }, 8000);
}

/** 开 / 关板载聆听。被「回到手柄区」拒了就补一次 open 再重试一次 */
export async function setAudio(link, values, tries = 2) {
  for (let i = 0; i < tries; i++) {
    const r = await link.cmd('pad.op', { op: 'audio', ...values }, 12000);
    if (r.ok !== false) return r;
    if (!PAD_AREA_WHY.test(String(r.reason || r.error || '')) || i === tries - 1) return r;
    await openPadArea(link);
  }
  return { ok: false, reason: '板载语音没切换成功' };
}

// ---------------- 一句话 → 动作 ----------------

// 前缀词。**不再要求**喊「鸭鸭」了（两条语音通道都直接说口令），但万一还带着就剥掉，
// 免得「鸭鸭站起来」被当成不会做的词。常见的误识别（呀呀、丫丫）也一起收了。
const ADDRESS = /^(鸭鸭鸭|鸭鸭|呀呀|丫丫|小鸭鸭|鸭子|小鸭子)[，,、。.!！?？\s]*/;

// 顺序有讲究：先判「停」，否则「站住」「别走」会被「站」「走」抢走
const RULES = [
  ['stop', /停|别走|不要走|站住|站好|不走了/],
  ['stand', /站起|起立|起来|起身|^站$/],
  ['sit', /坐下|坐下|坐一坐|^坐$|坐着/],
  ['roulade', /滚|翻跟头|翻身/],
  ['ground_pick', /捡|低头/],
  // 「走一小步」要排在「走」前面：不然「走一小步」会被 /走/ 抢走，只开策略不迈步
  ['step', /走一小步|走一步|走两步|迈一步|走一下/],
  ['walk', /走|前进|往前|开步/],
  ['nod', /点头/],
  ['shake', /摇头/],
  ['tilt', /歪头|偏头/],
  ['mouth', /张嘴|张嘴|开口/],
  ['chirp', /叫一声|叫一下|叫两声|嘎|叫唤/],
  ['greet', /打招呼|你好|嗨/],
];

export const CMD_CN = {
  stand: '站起来', sit: '坐下', walk: '开始走', step: '走一小步', stop: '停下，站好', roulade: '前滚翻',
  ground_pick: '低头捡东西', nod: '点点头', shake: '摇摇头', tilt: '歪歪头', mouth: '张嘴',
  chirp: '叫一声', greet: '打招呼',
};

/** 清掉空白和标点，方便匹配 */
function tidy(text) {
  return String(text || '').replace(/[\s，。！？、,.!?~～…]/g, '');
}

/**
 * 解析一句话。
 * @param {string} text 识别到的文字
 * @param {{requireWake?: boolean}} opt requireWake=true 时没有「鸭鸭」开头就当成闲聊
 * @returns {{cmd: string|null, wake: boolean, said: string}}
 */
export function parseUtterance(text, { requireWake = false } = {}) {
  const raw = tidy(text);
  const m = raw.match(ADDRESS);
  const wake = Boolean(m);
  const body = wake ? raw.slice(m[0].length) : raw;
  const said = body || raw;
  if (!said) return { cmd: null, wake, said };
  if (requireWake && !wake) return { cmd: null, wake, said };
  for (const [cmd, re] of RULES) {
    if (re.test(said)) return { cmd, wake, said };
  }
  return { cmd: null, wake, said };
}

// ---------------- 姿势文案 ----------------

export function postureCopy(st, name) {
  const p = st?.posture;
  const sleepy = st?.estop?.state === 'confirmed';
  if (!st) return { h: name, sub: '还没收到鸭子的状态。' };
  if (sleepy) return { h: `${name}松着劲儿`, sub: '舵机卸力了。想让它动，先点「站起来」。' };
  switch (p) {
    case 'walking': return { h: '正在走', sub: '松开摇杆它会停下、继续站着。' };
    case 'policy': return { h: `${name}站着，随时能走`, sub: '按「走一小步」，或者去手柄页推摇杆。' };
    case 'sitting': return { h: `${name}坐着`, sub: '按「站起来」，或者说一句「站起来」。' };
    case 'starting': return { h: `${name}正在起来`, sub: '别碰它，等它站稳。' };
    case 'limp': return { h: `${name}软着`, sub: '舵机没劲儿，先站起来。' };
    case 'homing': return { h: `${name}正在回中`, sub: '等它回中完成。' };
    case 'servo_session': return { h: '正在调舵机', sub: '主程序停着，站走用不了。' };
    case 'commissioning': return { h: `${name}还没标定`, sub: '去桌面版 Studio 走一遍上手流程。' };
    case 'absent': return { h: `没找到${name}`, sub: '主程序没连上，去桌面版看看。' };
    default: return { h: name, sub: '读不出它现在的姿势。' };
  }
}

/** 电量 / 舵机 / IMU 三个小格用的数字，读不到统一给 null，别自己编 */
export function readings(st) {
  const b = st?.battery || {};
  const s = st?.servos || {};
  const imu = st?.imu || {};
  return {
    pct: typeof b.pct === 'number' ? Math.round(b.pct) : null,
    volts: typeof b.volts === 'number' ? b.volts.toFixed(2) : null,
    online: typeof s.online === 'number' ? s.online : null,
    total: typeof s.total === 'number' ? s.total : 15,
    hottest: s.hottest && typeof s.hottest.c === 'number' ? Math.round(s.hottest.c) : null,
    imu: { ok: '正常', unverified: '方向没确认', frozen: '读数冻住了', missing: '没读到' }[imu.status] || '—',
    imuKind: { unverified: 'warn', frozen: 'bad', missing: 'bad' }[imu.status] || null,
    tilt: typeof st?.tilt_deg === 'number' ? Math.round(st.tilt_deg) : null,
  };
}
