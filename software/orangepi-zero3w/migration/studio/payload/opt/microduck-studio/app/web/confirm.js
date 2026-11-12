// 两步确认（第三批代码审查 D4）：所有「点两下才算」的按钮共用这一个——交还主程序、测急停的跳过，以后的也用它。
// 以前是原按钮原地变成「确定……？」，双击一下就直接过了（第二下正好点在变好的按钮上）。现在：
// - 第一下：原按钮不动，旁边新出现一个确认按钮（双击的第二下落在原按钮上）；
// - 出现后 700 ms 内的点击一律不算（双击、手快、确认按钮刚好冒到指针底下都不会误触）；
// - 3 秒没点确认就复原；700 ms 以后再点一下原按钮也是收起。
// 不用浏览器的 confirm 弹窗（会挡住整页，手机上还会把顶上的卸力按钮也挡住）。
import { html, useState, useEffect, useRef } from './vendor/preact-htm.module.js';

export const ARM_GUARD_MS = 700;
export const ARM_TIMEOUT_MS = 3000;

export function ConfirmBox({ lines = [], confirmLabel = '确定', cancelLabel = '先不', onConfirm, onCancel, busy = false, cls = '' }) {
  return html`<div class=${('confirm-box ' + cls).trim()} role="alertdialog" aria-live="polite">
    <div class="confirm-lines">${lines.map((line) => html`<p>${line}</p>`)}</div>
    <div class="confirm-acts">
      <button class="btn-light" type="button" disabled=${busy} onClick=${() => onCancel && onCancel()}>${cancelLabel}</button>
      <button class="btn-danger" type="button" disabled=${busy} onClick=${() => onConfirm && onConfirm()}>${confirmLabel}</button>
    </div>
  </div>`;
}

export function TwoStep({ label, ask, onConfirm, cls = 'btn-light', askCls = 'btn-danger', disabled = false, title, busyLabel }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const at = useRef(0);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), ARM_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [armed]);
  const early = () => performance.now() - at.current < ARM_GUARD_MS;
  const first = (e) => {
    e.currentTarget.blur();
    if (armed) { if (!early()) setArmed(false); return; }
    at.current = performance.now();
    setArmed(true);
  };
  const second = async (e) => {
    e.currentTarget.blur();
    if (!armed || early()) return;
    setArmed(false);
    setBusy(true);
    try { await onConfirm(); } finally { setBusy(false); }
  };
  return html`<span class="two-step">
    <button class=${cls} disabled=${disabled || busy} title=${title || undefined} aria-expanded=${armed ? 'true' : 'false'}
      onClick=${first}>${busy && busyLabel ? busyLabel : label}</button>
    ${armed ? html`<button class=${`${askCls} two-step-ask`} onClick=${second}>${ask}</button>` : null}</span>`;
}
