import { html, useEffect, useRef, useState } from './vendor/preact-htm.module.js';
import { ARK_URL, blankDraft, draftFrom, isDirty, missingFields, requestMessages } from './ai-ui-state.js';

const CSS = `.ai-settings{padding:18px 20px;min-width:0}.ai-settings h2{margin:0 0 8px;font-size:20px}.ai-settings p{font-size:13px;line-height:1.65;color:var(--text-2);margin:8px 0}.ai-settings .ai-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}.ai-settings label{display:flex;flex-direction:column;gap:5px;font-size:13px;min-width:0}.ai-settings input,.ai-settings select,.ai-settings textarea{width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--line,#d2d2d7);border-radius:9px;background:var(--surface,#fff);color:inherit;padding:9px;font:inherit}.ai-settings .ai-wide{grid-column:1/-1}.ai-settings .ai-check{flex-direction:row;align-items:center}.ai-settings .ai-check input{width:auto}.ai-settings .btn{border:0;border-radius:999px;padding:9px 15px;min-height:38px;font:inherit;font-size:14px;background:#e9e9ee;color:#242428;cursor:pointer}.ai-settings .btn.primary{background:#007aff;color:white}.ai-settings .btn:disabled{opacity:.4;cursor:default}.ai-settings .btn:focus-visible{outline:2px solid #007aff;outline-offset:2px}.ai-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.ai-settings .ai-result{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.ai-settings .ai-chat{border-top:1px solid var(--line,#ddd);margin-top:18px;padding-top:12px}.ai-settings .ai-message{white-space:pre-wrap;overflow-wrap:anywhere;border-radius:9px;padding:9px 11px;margin:7px 0;background:var(--surface-2,#f1f2f5);font-size:14px}.ai-settings .ai-message strong{font-size:12px;display:block;margin-bottom:3px}.ai-settings [role=alert]{color:var(--red,#b42318)}@media(max-width:480px){.ai-settings{padding:16px 12px}.ai-settings .ai-fields{grid-template-columns:1fr}.ai-settings .btn{max-width:100%;white-space:normal}}`;
if (!document.getElementById('ai-settings-css')) {
  const style = document.createElement('style'); style.id = 'ai-settings-css'; style.textContent = CSS; document.head.appendChild(style);
}

export function AiSettings({ api }) {
  const [data,setData] = useState(null), [form,setForm] = useState(blankDraft);
  const [selected,setSelected] = useState(''), [busy,setBusy] = useState(false);
  const [error,setError] = useState(''), [notice,setNotice] = useState(''), [tested,setTested] = useState(false);
  const [history,setHistory] = useState([]), [input,setInput] = useState('');
  const alive = useRef(false), controller = useRef(null), pending = useRef(false);
  const clearConversation = () => {setHistory([]);setInput('');setTested(false);setNotice('');};
  const apply = (response, id) => {
    setData(response);setSelected(id || '');
    setForm(draftFrom(response.connections.find(item=>item.id === id)));
  };
  const run = async (body, done) => {
    if (pending.current) return;
    pending.current = true;setBusy(true);setError('');
    const ctl = new AbortController();controller.current = ctl;
    try {
      const response = await api('/api/ai',body,24000,ctl.signal);
      if (!alive.current) return;
      if (!response.ok) {
        if (response.code === 'stale') clearConversation();
        setError(response.error || 'AI 请求未完成');return;
      }
      done(response);
    } catch (error) {
      if (alive.current) setError(error?.name === 'AbortError' ? 'AI 请求超时或已取消，请重试' : '网页连接中断，请重试');
    } finally {
      pending.current = false;
      if (alive.current) setBusy(false);
    }
  };
  useEffect(()=>{
    alive.current = true;
    run(undefined,response=>apply(response,response.current_id));
    return ()=>{alive.current = false;controller.current?.abort();};
  },[]);
  const saved = data?.connections.find(item=>item.id === selected);
  const dirty = isDirty(form,saved), missing = missingFields(form,saved);
  const ready = !!saved?.ready && !dirty && !busy;
  const edit = (key,value) => {setForm({...form,[key]:value});setError('');clearConversation();};
  const choose = id => {setSelected(id);setForm(draftFrom(data.connections.find(item=>item.id === id)));setError('');clearConversation();};
  const save = () => run({op:'save',connection:form},response=>{apply(response,response.saved_id);clearConversation();setNotice('连接已保存；保存不会联系提供商');});
  const select = () => run({op:'select',id:selected},response=>{apply(response,selected);clearConversation();setNotice('已设为当前连接');});
  const remove = () => run({op:'delete',id:selected},response=>{apply(response,'');clearConversation();setNotice('连接已删除');});
  const test = () => {setTested(false);setNotice('');run({op:'test',id:selected,revision:saved.revision},response=>{
    if (response.id === selected && response.revision === saved.revision) {setTested(true);setNotice(response.text);}
  });};
  const send = () => {
    const content = input.trim();if (!content || !ready) return;
    const messages = requestMessages(history,content);
    run({op:'chat',id:selected,revision:saved.revision,messages},response=>{
      if (response.id === selected && response.revision === saved.revision) {
        setHistory([...messages,{role:'assistant',content:response.text}].slice(-10));setInput('');
      }
    });
  };
  return html`<section id="ai-settings" class="board ai-settings" data-ai-settings aria-label="AI 对话">
    <h2>AI 对话</h2>
    <p>保存文字模型连接后，可测试和试聊。</p>
    ${error ? html`<p role="alert">${error}</p>` : null}
    ${!data ? html`<p>${busy ? '读取 AI 设置中…' : 'AI 设置暂不可用。请先设置网页密码后刷新页面。'}</p>` : html`
    <label>已保存连接<select aria-label="已保存连接" value=${selected} disabled=${busy} onChange=${e=>choose(e.target.value)}>
      <option value="">新建连接</option>${data.connections.map(item=>html`<option value=${item.id}>${item.name || '未命名草稿'}${item.id === data.current_id ? '（当前）' : ''}</option>`)}
    </select></label>
    <p data-ai-current>当前：${data.connections.find(item=>item.id === data.current_id)?.name || '未选择'} · 已保存 ${data.connections.length}/10</p>
    <div class="ai-actions"><button type="button" class="btn" data-ai="new" disabled=${busy || data.connections.length >= 10} onClick=${()=>choose('')}>新建连接</button></div>
    <fieldset disabled=${busy} style="border:0;padding:0;margin:0;min-width:0" data-ai-connection=${selected || ''}>
      <div class="ai-fields">
        <label>连接名称<input aria-label="连接名称" maxlength="80" value=${form.name} onInput=${e=>edit('name',e.target.value)} placeholder="例如：豆包文字模型" /></label>
        <label>提供商<select aria-label="提供商" value=${form.provider} onChange=${e=>{
          const provider=e.target.value;setForm({...form,provider,base_url:provider === 'doubao_ark' ? ARK_URL : '',api_key:'',clear_key:false});setError('');clearConversation();
        }}><option value="doubao_ark">豆包·方舟文字模型</option><option value="openai_compatible">其他 OpenAI 兼容接口</option></select></label>
        <label class="ai-wide">Base URL<input aria-label="Base URL" maxlength="500" value=${form.base_url} onInput=${e=>edit('base_url',e.target.value)} placeholder="https://服务地址/v1" spellcheck="false" /></label>
        <label class="ai-wide">Model ID<input aria-label="Model ID" maxlength="200" value=${form.model} onInput=${e=>edit('model',e.target.value)} placeholder="填写控制台提供的模型 ID 或接入点 ID" spellcheck="false" /></label>
        <label class="ai-wide">API Key<input type="password" aria-label="API Key" value=${form.api_key} maxlength="4096" autocomplete="new-password" onInput=${e=>edit('api_key',e.target.value)} placeholder=${saved?.key_configured ? '已保存；留空保留同一提供商和地址的 Key' : '尚未配置，可先保存草稿'} /></label>
        <label class="ai-check ai-wide"><input type="checkbox" checked=${form.clear_key} onChange=${e=>edit('clear_key',e.target.checked)} />清除已保存的 API Key</label>
        <label class="ai-check ai-wide"><input aria-label="无需 API Key（仅本地服务）" type="checkbox" checked=${form.auth_mode === 'none'} onChange=${e=>edit('auth_mode',e.target.checked ? 'none' : 'bearer')} />无需 API Key（仅本地服务）</label>
      </div>
    </fieldset>
    <details class="console-help"><summary>Key 与连接说明</summary><p>Key 只保存在鸭子本机，网页不会回显，不进入浏览器存储、日志或诊断包。更换提供商或地址后须重新填写 Key，或明确选择本地无 Key 服务。</p><p>当前仅支持网页文字试聊；麦克风、扬声器和实时语音尚未接入，对话不会驱动机器人。</p></details>
    ${missing.length ? html`<p data-ai-missing>待补齐：${missing.join('、')}。草稿可以保存，补齐并保存后才能测试与试聊。</p>` : dirty ? html`<p>有未保存的修改，请先保存。</p>` : null}
    <div class="ai-actions">
      <button class="btn primary" type="button" data-ai="save" disabled=${busy || !dirty || (!saved && data.connections.length >= 10)} onClick=${save}>保存连接</button>
      <button class="btn" type="button" data-ai="select" disabled=${!ready || data.current_id === selected} onClick=${select}>设为当前</button>
      <button class="btn" type="button" data-ai="delete" disabled=${busy || !saved} onClick=${remove}>删除连接</button>
      <button class="btn" type="button" data-ai="test" disabled=${!ready} onClick=${test}>测试连接</button>
    </div>
    <p>保存和选择不会请求云端；测试连接会发送一次简短文字请求，可能产生提供商费用。</p>
    ${busy ? html`<p role="status">正在处理…</p>` : null}
    ${notice ? html`<p class="ai-result" role="status" data-ai-tested=${tested}>${tested ? '测试成功：' : ''}${notice}</p>` : null}
    <div class="ai-chat"><strong>文字试聊</strong><p>仅保留本页内存中的近期对话上下文（最多 5 轮、12000 字）；每条输入最多 2000 字。切换、修改连接或离开设置页会清空，不保存聊天记录。</p>
      <div aria-live="polite" data-ai-history>${history.map(message=>html`<div class="ai-message"><strong>${message.role === 'user' ? '你' : '模型'}</strong>${message.content}</div>`)}</div>
      <label>试聊内容<textarea aria-label="试聊内容" rows="3" maxlength="2000" disabled=${busy} value=${input} onInput=${e=>setInput(e.target.value)} placeholder="开通服务并测试后，可以在这里试聊" /></label>
      <div class="ai-actions"><button class="btn primary" type="button" data-ai="send" disabled=${!ready || !input.trim()} onClick=${send}>发送</button><button class="btn" type="button" data-ai="clear" disabled=${busy || (!history.length && !input)} onClick=${()=>{setHistory([]);setInput('');}}>清空对话</button></div>
    </div>
    <details class="console-help"><summary>文字与语音接口的区别</summary><p>豆包实时语音使用独立的 WebSocket 地址与协议，不能把语音地址填进这里作为文字接口。实时语音接入将在后续单独配置。</p></details>`}
  </section>`;
}
