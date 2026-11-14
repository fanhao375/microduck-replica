import { html, useState, useEffect } from './vendor/preact-htm.module.js';
import { TwoStep } from './confirm.js';

export function PasswordSettings({ hasPassword, api, onSaved }) {
  const [pw, setPw] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setMsg(null);
    if (pw.length < 6) { setErr('密码至少 6 位'); return; }
    if (pw !== again) { setErr('两次输入不一样'); return; }
    setBusy(true);
    let r;
    try { r = await api('/api/password', { action: 'set', password: pw, confirm: again }); }
    catch { r = { error: '连不上鸭子，检查网络后再试' }; }
    setBusy(false);
    if (!r.ok) { setErr(r.status === 401 ? '登录过期了，刷新页面重新登录' : (r.error || '没改成')); return; }
    setPw(''); setAgain(''); onSaved?.(); setMsg('登录密码已保存，这台浏览器还登录着。');
  };
  const clear = async () => {
    setErr(null); setMsg(null);
    let r;
    try { r = await api('/api/password', { action: 'clear' }); }
    catch { r = { error: '连不上鸭子，检查网络后再试' }; }
    if (!r.ok) { setErr(r.status === 401 ? '登录过期了，刷新页面重新登录' : (r.error || '没清掉')); return; }
    location.reload();
  };
  if (hasPassword == null) {
    return html`<div class="board password-settings" style="padding:16px 20px">
      <div style="font-size:15px;font-weight:600">登录密码</div>
      <div style="font-size:13px;color:var(--text-2)">正在读取登录状态…</div></div>`;
  }
  return html`<form class="board password-settings" style="padding:16px 20px;display:flex;flex-direction:column;gap:10px"
    onSubmit=${submit}>
    <div style="font-size:15px;font-weight:600">${hasPassword ? '登录密码' : '设一个登录密码'}</div>
    <div style="font-size:13px;line-height:1.6;color:var(--text-2)">
      热点密码大家都知道；设了登录密码以后，连上热点的人也要先登录才能控制鸭子。至少 6 位。
      ${hasPassword ? '修改后，其他浏览器上的登录会作废，这台还保持登录。' : ''}
    </div>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text-2)">
      ${hasPassword ? '新密码' : '密码'}
      <input type="password" name="password" autocomplete="new-password" value=${pw} required minLength="6"
        onInput=${(e) => setPw(e.target.value)} style="height:40px;padding:0 10px;border:1px solid var(--line);border-radius:8px" />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text-2)">
      再输一遍
      <input type="password" name="confirm" autocomplete="new-password" value=${again} required minLength="6"
        onInput=${(e) => setAgain(e.target.value)} style="height:40px;padding:0 10px;border:1px solid var(--line);border-radius:8px" />
    </label>
    ${err ? html`<div role="alert" style="font-size:13px;color:var(--danger)">${err}</div>` : null}
    ${msg ? html`<div style="font-size:13px;color:var(--text-2)">${msg}</div>` : null}
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
      <button type="submit" disabled=${busy || !pw || !again}
        style="height:40px;padding:0 18px;border:0;border-radius:999px;background:var(--blue);color:#FFF;font-weight:600">
        ${busy ? '保存中…' : (hasPassword ? '修改登录密码' : '设好登录密码')}</button>
      ${hasPassword ? html`<${TwoStep} label="清掉登录密码" ask="真的清掉？" onConfirm=${clear} />` : null}
    </div>
  </form>`;
}

// 「更多」页的 SSH 开关（用户 10-21：「出了问题他们 SSH 自己修，不用重新烧卡」）。镜像出厂只认钥匙、不带任何人的钥匙，
// 也没有默认密码：在这里自己定一个密码再打开。要先设网页登录密码（后端也挡）。
export function SshSettings({ hasPassword, api }) {
  const [ssh, setSsh] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [pw, setPw] = useState('');
  const [again, setAgain] = useState('');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const load = async () => {
    let r;
    try { r = await api('/api/ssh', { action: 'status' }, 15000); } catch { r = { error: '连不上鸭子' }; }
    if (r.ok) { setSsh(r.ssh); setLoadErr(null); } else setLoadErr(r.error || '读不到 SSH 的状态');
  };
  useEffect(() => { load(); }, []);
  const on = ssh?.password_login === true;
  const keys = ssh?.key_list || [];
  const cmd = `ssh ${ssh?.user || 'radxa'}@${location.hostname}`;
  const call = async (body, okText) => {
    setErr(null); setMsg(null); setBusy(true);
    let r;
    try { r = await api('/api/ssh', body, 60000); } catch { r = { error: '连不上鸭子，检查网络后再试' }; }
    setBusy(false);
    if (!r.ok) { setErr(r.status === 401 ? '登录过期了，刷新页面重新登录' : (r.error || '没成')); return null; }
    if (r.ssh) setSsh(r.ssh);
    setMsg(typeof okText === 'function' ? okText(r) : okText);
    return r;
  };
  const submitPw = async (e) => {
    e.preventDefault();
    setErr(null); setMsg(null);
    if (pw.length < 8) { setErr('SSH 密码至少 8 位'); return; }
    if (pw !== again) { setErr('两次输入不一样'); return; }
    const r = await call({ action: 'on', password: pw, confirm: again },
      on ? 'SSH 密码改好了。' : 'SSH 密码登录打开了：在电脑上运行上面那行，输刚设的密码。');
    if (r) { setPw(''); setAgain(''); }
  };
  const submitKey = async (e) => {
    e.preventDefault();
    const r = await call({ action: 'add_key', key },
      (x) => (x.added ? '钥匙登记好了：用这台电脑运行上面那行，不用输密码。' : '这把钥匙本来就登记过了。'));
    if (r) setKey('');
  };
  const removeKey = (fp) => call({ action: 'remove_key', fp }, '这把钥匙删掉了。');
  const off = () => call({ action: 'off' }, 'SSH 密码登录关掉了，只认钥匙。');
  const copy = async () => { try { await navigator.clipboard.writeText(cmd); setMsg('登录命令复制好了。'); } catch { setMsg('复制不可用，请选择上面的登录命令复制。'); } };
  const input = 'height:40px;padding:0 10px;border:1px solid var(--line);border-radius:8px';
  const small = 'font-size:13px;line-height:1.6;color:var(--text-2)';
  const blue = 'height:40px;padding:0 18px;border:0;border-radius:999px;background:var(--blue);color:#FFF;font-weight:600';
  const pwState = ssh == null ? null : ssh.password_login === true ? '密码登录开着'
    : ssh.password_login === false ? '只认钥匙（密码登录关着）' : '密码登录开没开读不到';
  return html`<div class="board ssh-settings" style="padding:16px 20px;display:flex;flex-direction:column;gap:10px">
    <div style="font-size:15px;font-weight:600">SSH 远程登录</div>
    <details class="console-help"><summary>SSH 使用说明</summary><div style=${small}>
      用电脑的终端登进鸭子：改程序、看日志、换文件，不用重烧卡。出厂只认钥匙、不认密码，也没有默认密码；
      两种办法二选一：自己定一个 SSH 密码打开，或者把自己电脑的公钥登记进来。</div></details>
    <div class="ssh-now" style="font-size:14px">现在：${ssh == null ? (loadErr || '正在读…')
      : html`<b>${pwState}</b>，${keys.length ? `登记了 ${keys.length} 把钥匙` : '一把钥匙都没登记'}${ssh.ssh_active === false ? '；ssh 服务没在跑' : ''}`}</div>
    ${on || keys.length ? html`<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
      <code class="num ssh-cmd" style="padding:6px 10px;background:#F2F2F7;border-radius:8px">${cmd}</code>
      <button type="button" onClick=${copy} style="height:34px;padding:0 14px;border:0;border-radius:999px;background:#E5E5EA">复制</button></div>
      <div style=${small}>Windows 打开 PowerShell、Mac 打开终端，运行上面这行；第一次会问 yes/no，输 yes。</div>` : null}
    ${!hasPassword ? html`<div class="ssh-need-web-pw" role="note" style="font-size:13px;color:var(--warn, #B25000)">
      先在上面设网页登录密码，才能打开 SSH、登记钥匙：不然连上热点的人都能打开这个网页、顺手把 SSH 打开。</div>` : null}
    ${loadErr ? html`<button type="button" class="btn" onClick=${load}>重新读取 SSH 状态</button>` : null}
    ${err ? html`<div role="alert" style="font-size:13px;color:var(--danger)">${err}</div>` : null}
    ${msg ? html`<div class="ssh-msg" style="font-size:13px;color:var(--text-2)">${msg}</div>` : null}

    <form class="ssh-pw" onSubmit=${submitPw} style="display:flex;flex-direction:column;gap:10px;padding-top:8px;border-top:1px solid var(--line)">
      <div style="font-size:14px;font-weight:600">办法一：密码登录</div>
      <div style=${small}>至少 8 位，不能是 microduck。这个密码也是鸭子上 sudo 用的密码。</div>
      ${hasPassword ? html`
      <label style="display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text-2)">
        ${on ? '新的 SSH 密码' : 'SSH 密码'}
        <input type="password" name="ssh-password" autocomplete="new-password" value=${pw} minLength="8" maxLength="64"
          onInput=${(e) => setPw(e.target.value)} style=${input} /></label>
      <label style="display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--text-2)">
        再输一遍
        <input type="password" name="ssh-confirm" autocomplete="new-password" value=${again} minLength="8" maxLength="64"
          onInput=${(e) => setAgain(e.target.value)} style=${input} /></label>` : null}
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <button type="submit" class="ssh-on" disabled=${busy || !hasPassword || ssh == null || !pw || !again} style=${blue}>
          ${busy ? '正在设…' : (on ? '改 SSH 密码' : '打开 SSH 密码登录')}</button>
        ${on ? html`<${TwoStep} label="关掉 SSH 密码登录" ask="真的关掉？" onConfirm=${off} />` : null}
      </div>
    </form>

    <form class="ssh-key" onSubmit=${submitKey} style="display:flex;flex-direction:column;gap:10px;padding-top:8px;border-top:1px solid var(--line)">
      <div style="font-size:14px;font-weight:600">办法二：登记钥匙（不用密码，更安全）</div>
      <details class="console-help"><summary>如何生成并登记公钥</summary><div style=${small}>
        还没有钥匙的话：Windows 在 PowerShell 里运行 <code>ssh-keygen -t ed25519</code>，一路回车；
        再运行 <code>type $env:USERPROFILE\\.ssh\\id_ed25519.pub</code>（Mac：<code>cat ~/.ssh/id_ed25519.pub</code>），
        把打出来的那一整行贴到下面。<b>只贴 .pub 那个</b>，另一个是私钥，别给任何人。</div></details>
      ${keys.length ? html`<ul class="ssh-keys" style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px">
        ${keys.map((k) => html`<li key=${k.fp} style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px">
          <span>${k.comment || '（没注释）'}</span><code class="num" style="color:var(--text-2)">${k.type} ${k.fp.slice(0, 19)}…</code>
          ${hasPassword ? html`<${TwoStep} label="删掉" ask="真的删掉这把？" onConfirm=${() => removeKey(k.fp)} />` : null}</li>`)}
      </ul>` : null}
      ${hasPassword ? html`<textarea name="ssh-key" rows="3" value=${key} onInput=${(e) => setKey(e.target.value)}
        placeholder="ssh-ed25519 AAAA… 你的电脑名" style="padding:8px 10px;border:1px solid var(--line);border-radius:8px;font:13px/1.5 ui-monospace,monospace;resize:vertical"></textarea>` : null}
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <button type="submit" class="ssh-addkey" disabled=${busy || !hasPassword || !key.trim()} style=${blue}>登记这把钥匙</button>
      </div>
    </form>
  </div>`;
}


export function AccountSettings({ api, st }) {
  const [hasPassword, setHasPassword] = useState(st?.auth?.has_password);
  const [logoutError, setLogoutError] = useState('');
  useEffect(() => { setHasPassword(st?.auth?.has_password); }, [st?.auth?.has_password]);
  const logout = async () => {
    setLogoutError('');
    try {
      const result = await api('/api/logout', {});
      if (!result.ok) { setLogoutError(result.error || '退出未完成，请重试。'); return; }
      location.reload();
    } catch { setLogoutError('连接中断，退出结果未确认。'); }
  };
  return html`<div class="console-settings-stack console-account">
    <${PasswordSettings} api=${api} hasPassword=${hasPassword} onSaved=${() => setHasPassword(true)} />
    <${SshSettings} api=${api} hasPassword=${hasPassword} />
    <section class="board console-session"><h2>管理会话</h2><button class="btn" onClick=${logout}>退出登录</button>
      ${logoutError ? html`<p role="alert">${logoutError}</p>` : null}</section>
  </div>`;
}
