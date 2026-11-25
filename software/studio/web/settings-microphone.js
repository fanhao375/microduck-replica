import { html, useEffect, useRef, useState } from './vendor/preact-htm.module.js';

const SOURCES = { onboard: '板载麦克风', external: '外接麦克风（J2 左口）', both: '板载＋外接' };

export function MicrophoneSettings({ api }) {
  const [data, setData] = useState(null);
  const [readError, setReadError] = useState('');
  const [writeError, setWriteError] = useState('');
  const [saving, setSaving] = useState(false);
  const select = useRef(null);

  useEffect(() => {
    let timer, flight, writing = false;
    async function refresh(source) {
      if (document.hidden || writing) return;
      clearTimeout(timer);
      flight?.abort();
      const ctl = new AbortController();
      flight = ctl;
      writing = source !== undefined;
      if (writing) {
        setSaving(true);
        setWriteError('');
      }
      let delay = writing ? 250 : 4000;
      try {
        const result = await api('/api/microphone', writing ? { source } : undefined,
          writing ? 15000 : 2500, ctl.signal);
        if (ctl.signal.aborted) return;
        if (!result.ok || !result.data) throw Error(result.error || '无法读取麦克风设置。');
        setData(result.data);
        setReadError('');
        delay = result.data.listening ? 750 : 2000;
      } catch (error) {
        if (ctl.signal.aborted) return;
        setData(null);
        if (writing) setWriteError(error.message || '切换麦克风失败。');
        else setReadError(error.message || '无法读取麦克风设置。');
      } finally {
        if (flight === ctl) {
          flight = null;
          writing = false;
          setSaving(false);
          if (!document.hidden) timer = setTimeout(refresh, delay);
        }
      }
    }
    function visibilityChanged() {
      clearTimeout(timer);
      if (writing) return;
      flight?.abort();
      flight = null;
      setData(null);
      if (!document.hidden) refresh();
    }
    select.current = refresh;
    document.addEventListener('visibilitychange', visibilityChanged);
    refresh();
    return () => {
      select.current = null;
      clearTimeout(timer);
      flight?.abort();
      flight = null;
      document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, [api]);

  const level = data?.microphone_level_dbfs;
  return html`<section class="board microphone-settings" aria-labelledby="microphone-title">
    <h2 id="microphone-title">麦克风输入</h2>
    <div class="microphone-sources" role="group" aria-label="选择麦克风输入源">
      ${Object.entries(SOURCES).map(([source, label]) => html`<button type="button" key=${source}
        aria-pressed=${data?.microphone_source === source ? 'true' : 'false'} disabled=${saving || !data}
        onClick=${() => select.current?.(source)}>${label}</button>`)}
    </div>
    <p role="status">${saving ? '正在切换输入源…' : data?.microphone_source
      ? '当前输入 · ' + SOURCES[data.microphone_source] : data || readError || writeError ? '当前输入未确认' : '正在读取输入源…'}</p>
    ${data ? html`<p class="num">${!data.listening ? '监听已关闭' : Number.isFinite(level)
      ? `输入电平 ${level.toFixed(1)} dBFS` : '监听中，等待输入电平…'}</p>` : null}
    ${(writeError || readError || data?.error) && html`<p role="alert">${[...new Set([writeError, readError, data?.error].filter(Boolean))].join(' ')}</p>`}
    <details><summary>比较收音距离</summary>
      <p>在总览的「语音与音乐互动」开启监听。外接麦放在壳外，避开风扇出风口。保持风扇正常运转，先看不说话时的电平，再在 0.5 米、1 米、2 米处用平常音量说“叫一声”。每处试 5 次，两次间等鸭子回应完。</p>
      <p>三种输入各试一轮，保持位置、朝向和风扇转速一致。记下每处成功回应的次数，例如“外接，2 米，4/5”。可以把至少成功 4 次的最远距离当作本机参考距离，2 米仍稳定就继续往后测。</p>
    </details>
  </section>`;
}
