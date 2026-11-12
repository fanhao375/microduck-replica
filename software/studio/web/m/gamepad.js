// 手柄页：两个虚拟摇杆 + 一排动作键。
//
// 协议完全照 web/pad.js 顶部那份说明，一条都没改：
//   sticks {mode, lx, ly, rx, ry, seq}  -1..1，x 向右、y 向下为正；按住时 10 Hz 发
//   stop {seq}                        松手发一次，后端按模式回中 / 停走
//   mode {mode, seq} / {mode, hb:true} 切模式 + 头/身体模式每秒报一次「还在」（后端 3 秒没听到就退出）
//   drive {open:true} / {on:false}     进出手柄区
// 后端 deadman：0.5 秒收不到摇杆帧就自己停，所以松手/断线都不会让鸭子一直走。
import { html, useState, useEffect, useRef, useCallback } from '../vendor/preact-htm.module.js';
import { link } from './link.js';
import { ACTION_GROUPS, actionLabel, runAction, nextAction } from './actions.js';

const SEND_MS = 100;
const MODE_HB_MS = 1000;
const AUTO_WALK_MS = 4000;   // 站着但策略没跑时，替用户补一次「开始走」的冷却（后端 enable 要一两秒）

// 只留走路和转头两个：身体那档是给调试微调用的，日常玩容易误切（用户 11-02 提的）。
// 后端还认 body，只是这里不给入口了。
const MODES = [
  { id: 'drive', label: '走路', hint: '左摇杆前后走、左右平移；右摇杆左右转身' },
  { id: 'head', label: '转头', hint: '左摇杆抬头低头，右摇杆左右歪头' },
];

// 站姿 → 一句话说清「现在差哪一步」。这张表就是用户「推摇杆没反应」的答案：
// 站着分 holding（策略没跑，摇杆推了也被丢）和 policy（准备好走了），差的就是「开始走」这一下。
const TIPS = {
  walking: ['ok', '正在走：松开摇杆它会停下、继续站着'],
  policy: ['ok', '准备好走了：推左摇杆就走'],
  holding: ['warn', '站着，但还没「开始走」——摇杆推了不动。点摇杆下面黄色的「开始走 start 策略」（推一下左摇杆也会自动帮你开）'],
  sitting: ['warn', '坐着：先点「站起来」，再点「开始走 start 策略」，摇杆才有反应'],
  limp: ['bad', '舵机没劲儿：先点「站起来」'],
  starting: ['warn', '正在起来，等它站稳'],
  homing: ['warn', '正在回中，等它稳住'],
  servo_session: ['bad', '正在调舵机：站起和走路这会儿都用不了'],
  commissioning: ['bad', '还没标定：先去电脑上的 Studio 走一遍上手流程'],
  absent: ['bad', '主程序没连上，摇杆发了也没人接'],
};

function driveTip(st) {
  if (!st) return null;
  if (st.estop && st.estop.state === 'confirmed') return ['bad', '舵机卸力了：先点「站起来」上力，再走'];
  return TIPS[st.posture] || null;
}

/** 这些姿态下推摇杆后端也动不了（策略没跑 / 舵机没劲 / 正在调舵机），别白发帧，先告诉用户为什么 */
function blockedWhy(st) {
  if (!st) return null;
  if (st.estop && st.estop.state === 'confirmed') return '舵机卸力了：先点「站起来」上力';
  switch (st.posture) {
    case 'sitting': return '它坐着呢：先点「站起来」，再点「开始走」';
    case 'limp': return '舵机没劲儿：先点「站起来」上力';
    case 'servo_session': return '正在调舵机：这会儿摇杆用不了';
    case 'commissioning': return '还没标定：先去电脑上的 Studio 走一遍上手流程';
    case 'absent': return '主程序没连上，摇杆发了也没人接';
    default: return null;
  }
}

/** 单个摇杆：按住拖动，松手弹回中间。归一化到 -1..1（x 向右、y 向下为正，跟后端一致） */
function Stick({ cap, onChange, onEnd }) {
  const boxRef = useRef(null);
  const knobRef = useRef(null);
  const [active, setActive] = useState(false);
  const idRef = useRef(null);

  const move = (e) => {
    const box = boxRef.current;
    if (!box) return;
    const r = box.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const max = Math.min(r.width, r.height) / 2 * 0.68;      // 摇杆最多推到半径的 68%
    let dx = e.clientX - cx;
    let dy = e.clientY - cy;
    const len = Math.hypot(dx, dy);
    if (len > max) { dx = dx / len * max; dy = dy / len * max; }
    if (knobRef.current) knobRef.current.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
    onChange(dx / max, dy / max);
  };

  const end = () => {
    idRef.current = null;
    setActive(false);
    if (knobRef.current) knobRef.current.style.transform = 'translate(-50%, -50%)';
    onEnd();
  };

  const down = (e) => {
    e.preventDefault();
    idRef.current = e.pointerId;
    setActive(true);
    try { boxRef.current.setPointerCapture(e.pointerId); } catch { /* 老浏览器 */ }
    move(e);
  };

  const up = (e) => {
    if (idRef.current != null && e.pointerId !== idRef.current) return;
    end();
  };

  return html`<div class="m-stick-box">
    <div class=${'m-stick' + (active ? ' active' : '')} ref=${boxRef}
      onPointerDown=${down} onPointerMove=${(e) => { if (idRef.current != null) move(e); }}
      onPointerUp=${up} onPointerCancel=${up} onLostPointerCapture=${up}
      role="application" aria-label=${cap}>
      <div class="m-knob" ref=${knobRef}><i></i></div>
    </div>
    <div class="cap">${cap}</div>
  </div>`;
}

export function GamepadPage({ st, live, say }) {
  const [mode, setMode] = useState('drive');
  const [busy, setBusy] = useState(null);
  const left = useRef({ x: 0, y: 0, on: false });
  const right = useRef({ x: 0, y: 0, on: false });
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const stRef = useRef(st);
  stRef.current = st;
  const poseRef = useRef(null);
  poseRef.current = live ? st?.posture : null;
  const sayRef = useRef(say);
  sayRef.current = say;
  const autoAt = useRef(0);                // 上次替用户补「开始走」的时刻：带冷却，别每帧打后端
  const nagged = useRef(false);            // 坐着 / 卸力时推摇杆：只说一遍，别 10 Hz 刷屏
  // 给 actions.js 用的实时站姿：等策略开起来就按真实状态等，不用瞎猜秒数
  const ctxRef = useRef({ getPosture: () => poseRef.current });

  // 进手柄区：告诉后端「我来了」，离开时「我走了」
  useEffect(() => {
    link.cmd('pad.op', { op: 'drive', open: true }, 8000);
    autoAt.current = 0;
    return () => {
      poseRef.current = null;
      link.cmd('pad.op', { op: 'drive', on: false }, 8000);
    };
  }, []);

  // 摇杆：按住时 10 Hz 发整帧；全都松了就发一次 stop
  useEffect(() => {
    let wasOn = false;
    const t = setInterval(() => {
      const on = left.current.on || right.current.on;
      if (on) {
        // 坐着 / 没劲儿 / 在调舵机等等：后端收了也动不了（策略没跑，robot.move 被 robotd 忽略）。
        // 与其让它默默没反应，不如当场说一句；这时别发帧，省得后端 10 Hz 记一堆 dropped。
        const why = blockedWhy(stRef.current);
        if (why) {
          if (!nagged.current) { nagged.current = true; sayRef.current(why, true); }
          return;
        }
        nagged.current = false;
        // 站着但策略没跑（holding）：这一帧会被 robotd 无视。替用户补一次「开始走」，
        // 免得他只看到「推了没反应」。坐着不自动站——那是明显会动到身体的动作，交给用户自己点。
        if (poseRef.current === 'holding' && performance.now() - autoAt.current > AUTO_WALK_MS) {
          autoAt.current = performance.now();
          link.cmd('pad.op', { op: 'walk' }, 20000);
        }
        wasOn = true;
        link.cmd('pad.op', {
          op: 'sticks', mode: modeRef.current,
          lx: left.current.x, ly: left.current.y,
          rx: right.current.x, ry: right.current.y,
          seq: link.nextPadSeq(),
        }, 5000);
      } else if (wasOn) {
        wasOn = false;
        link.cmd('pad.op', { op: 'stop', seq: link.nextPadSeq() }, 5000);
      }
    }, SEND_MS);
    return () => { clearInterval(t); link.cmd('pad.op', { op: 'stop', seq: link.nextPadSeq() }, 5000); };
  }, []);

  // 头 / 身体模式：每秒续一次命（后端 3 秒没听到就退回走路模式）
  useEffect(() => {
    link.cmd('pad.op', { op: 'mode', mode, seq: link.nextPadSeq() }, 6000);
    if (mode === 'drive') return undefined;
    const t = setInterval(() => { link.cmd('pad.op', { op: 'mode', mode, hb: true, seq: link.nextPadSeq() }, 6000); }, MODE_HB_MS);
    return () => clearInterval(t);
  }, [mode]);

  const fire = useCallback(async (key) => {
    if (busy) return;
    setBusy(key);
    const out = await runAction(link, key, ctxRef.current);
    setBusy(null);
    autoAt.current = 0;                     // 手动点过按钮，下次推摇杆允许再自动补一次
    if (out.ok) say(out.note || `已发送：${out.cn}`); else say(out.error || `${out.cn}没发出去`, true);
  }, [busy, say]);

  const m = MODES.find((x) => x.id === mode) || MODES[0];
  const tip = driveTip(st);
  const hot = nextAction(st);               // 现在最该按的那个键，给它描一圈绿边
  const key = (k) => html`<button
    class=${'m-key' + (k === hot ? ' hot' : '') + (busy === k ? ' on' : '')}
    disabled=${Boolean(busy)} onClick=${() => fire(k)}>
    ${actionLabel(k)}
  </button>`;

  // 「开始走」＝打开走路策略的总开关，「停止策略」＝停下并关掉策略。两个并排放在摇杆下面：
  // 「推摇杆没反应」的唯一解法是「开始走」，混在动作格里最容易被漏掉（用户 11-02 提的）。

  return html`<div>
    ${!live ? html`<div class="m-banner">还没收到鸭子的状态，摇杆可以推，但可能不动。</div>` : null}
    ${tip ? html`<div class=${'m-banner ' + tip[0]}>${tip[1]}</div>` : null}

    <h2 class="m-h2">怎么动</h2>
    <div class="m-seg" role="group" aria-label="摇杆模式">
      ${MODES.map((x) => html`<button aria-pressed=${mode === x.id ? 'true' : 'false'} onClick=${() => setMode(x.id)}>${x.label}</button>`)}
    </div>
    <p class="m-tip">${m.hint}</p>

    <div class="m-sticks">
      <${Stick} cap=${mode === 'drive' ? '左摇杆 · 走' : '左摇杆 · 抬头低头'}
        onChange=${(x, y) => { left.current = { x, y, on: true }; }}
        onEnd=${() => { left.current = { x: 0, y: 0, on: false }; }} />
      <${Stick} cap=${mode === 'drive' ? '右摇杆 · 转身' : '右摇杆 · 歪头'}
        onChange=${(x, y) => { right.current = { x, y, on: true }; }}
        onEnd=${() => { right.current = { x: 0, y: 0, on: false }; }} />
    </div>

    <div class="m-start-row">
      <button
        class=${'m-key start' + (hot === 'walk' ? ' hot' : '') + (busy === 'walk' ? ' on' : '')}
        disabled=${Boolean(busy)} onClick=${() => fire('walk')}>
        开始走<small>start 策略</small>
      </button>
      <button
        class=${'m-key start stop' + (busy === 'stop' ? ' on' : '')}
        disabled=${Boolean(busy)} onClick=${() => fire('stop')}>
        停止策略<small>stop 策略</small>
      </button>
    </div>

    ${ACTION_GROUPS.map((g) => html`<div>
      <h2 class="m-h2">${g.title}</h2>
      <div class="m-keys">${g.keys.map(key)}</div>
    </div>`)}

    <p class="m-tip">
      <b>绿框那个键</b>是按它现在的姿势、下一步最该按的。
      <b>黄色「开始走 start 策略」只是把走路策略打开</b>，鸭子不会自己迈步——要迈步就推左摇杆，
      或者点「走一小步」（它会自动先开策略再走一步）。
      走完了点旁边的<b>「停止策略 stop 策略」</b>，它会站住并关掉策略，之后推摇杆也没反应，再点「开始走」才行。
      松手就停，断线也停（后端 0.5 秒收不到摇杆帧会自己刹车）。按住摇杆的时候别切模式，松手再切。
    </p>
  </div>`;
}
