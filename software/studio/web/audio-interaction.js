import { html, useEffect, useRef, useState } from './vendor/preact-htm.module.js';
import { audioLabel, rhythmLabel } from './audio-ui-state.js';

const CMD_CN = { sit: '坐下', stand: '站起来', walk: '走一小步', stop: '停下',
  roulade: '前滚翻', ground_pick: '低头捡东西', chirp: '叫一声', greet: '打招呼',
  nod: '点头', shake: '摇头', tilt: '歪头' };

export function AudioInteraction({ pad, link, activity }) {
  const status = pad?.audio;
  const [active, setActive] = useState(activity.active);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const alive = useRef(false), busy = useRef(false);
  useEffect(() => {
    alive.current = true;
    const off = activity.subscribe(({active:value}) => setActive(value));
    return () => { alive.current = false; off(); };
  }, [activity]);
  const change = async values => {
    if (busy.current || !activity.active || link.status !== 'open') return;
    busy.current = true; setPending(true); setError(null);
    try {
      const response = await link.cmd('pad.op', {op:'audio', ...values}, 2500);
      if (alive.current && !response?.ok) setError(response?.reason || response?.error || '语音开关没有完成');
    } catch (err) {
      if (alive.current) setError(err?.message || '语音连接中断');
    } finally {
      busy.current = false;
      if (alive.current) setPending(false);
    }
  };
  const ready = active && link.status === 'open' && !pending;
  return html`<section id="audio-interaction" class="pd-motion" aria-label="语音与音乐互动">
    <div class="motion-heading"><h4>语音与音乐互动</h4>
      <span class="motion-state" role="status" data-audio-listening=${!!status?.listening}>${audioLabel(status)}</span>
    </div>
    <div class="motion-toolbar">
      <a class="btn-light" href="#/settings" data-ai-link>配置 AI 对话（文字试聊）</a>
      <button type="button" class=${status?.listening ? 'motion-stop' : 'btn-primary'} data-audio="listening"
        disabled=${!ready || !status?.available} onClick=${() => change({listening:!status?.listening})}>
        ${status?.listening ? '关闭监听' : '开启监听'}</button>
      <button type="button" class="btn-light" data-audio="music" disabled=${!ready || !status?.available}
        onClick=${() => change({music:!status?.music})}>${status?.music ? '退出音乐模式' : '听音乐'}</button>
    </div>
    ${error || status?.error ? html`<p class="motion-error" role="alert">${error || status.error}</p>` : null}
    ${!status?.available ? html`<p class="pd-small-p">${status?.why || '等待语音服务连接'}</p>` : null}
    ${status?.available && status?.voice_ready === false && !status?.error
      ? html`<p class="motion-error" role="alert">语音识别还没准备好（模型加载中或没装好；稍等十几秒，一直这样就看一眼 duck-echo 日志）。</p>`
      : null}
    ${status?.last?.accepted === false
      ? html`<p class="motion-error" role="alert">最近一次「${CMD_CN[status.last.command] || status.last.command}」没成功${status.last.reason ? `：${status.last.reason}` : ''}</p>`
      : null}
    ${status?.listening && status?.music ? html`<p class="pd-small-p" data-audio-rhythm>${rhythmLabel(status.rhythm)}</p>
      <p class="pd-small-p">手机或音箱在旁边放歌，可以问“感觉怎么样”“喜欢这种歌吗”。
      叫声按近期节奏感觉变化；低置信度时会用询问声回应。音乐模式暂不执行坐站口令或复读整首歌。</p>` : null}
    <p class="pd-small-p motion-note">R3 按一下开启并短叫一声，再按一下关闭；提示声后稍停再说话。
      日常口令是“坐下”“站起来”“停”“点头”“摇头”“歪头”“前滚翻”“低头捡东西”“叫一声”“打招呼”，
      执行后用叫声确认；普通话语不会原样播放。录音复读单独保留，专门按键以后再设。</p>
    <p class="pd-small-p motion-note">监听会持续到关闭。听音乐后，先关闭实体手柄、站稳并点“开始走”，
      再保持网页在前台并停留在遥控区，点下面的“随乐律动”。头部会小幅跟拍；身体跟随效果取决于运动模型。
      独立随乐律动开启后，失焦、切后台、滚出遥控区或切换站内页面仍会继续；关闭网页或连接断开会停止。
      返回页面可停止律动。录制和录制中的随乐律动仍需保持前台和遥控区可见，离开后停止。</p>
  </section>`;
}
