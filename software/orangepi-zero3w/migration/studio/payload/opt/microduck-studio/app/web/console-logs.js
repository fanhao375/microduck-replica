import {html,useEffect,useMemo,useRef,useState} from './vendor/preact-htm.module.js';

export function ConsoleLogs({api,link}) {
  const [items,setItems]=useState([]),[category,setCategory]=useState(''),[level,setLevel]=useState('all'),[query,setQuery]=useState(''),[paused,setPaused]=useState(false),[error,setError]=useState('');
  const frozen=useRef([]);
  useEffect(()=>{
    let alive=true;
    api('/api/logs?limit=300').then(r=>{if(alive){if(r.status===200)setItems(r.items||[]);else setError(r.error||'日志读取失败');}}).catch(()=>{if(alive)setError('日志连接失败');});
    const off=link.on(m=>{if(m.t==='log')setItems(old=>[...old.slice(-499),m.entry]);});
    return ()=>{alive=false;off();};
  },[api,link]);
  const source=paused?frozen.current:items;
  const shown=useMemo(()=>source.filter(entry=>(!category||entry.cat===category)&&(level==='all'||level==='warn'&&entry.level==='warn'||level==='error'&&entry.level==='error')&&(!query||[entry.msg,entry.raw,entry.step,entry.hint].join(' ').toLowerCase().includes(query.toLowerCase()))),[source,category,level,query]);
  const categories=[...new Set(items.map(entry=>entry.cat))];
  const copy=async()=>{try{await navigator.clipboard.writeText(shown.map(entry=>`${entry.t} [${entry.cat}] [${entry.level}] ${entry.msg}${entry.raw?'\n'+entry.raw:''}`).join('\n'));setError('');}catch{setError('浏览器未允许复制，请在日志中选择内容。');}};
  return html`<div class="console-logs"><div class="console-log-tools"><div class="console-tabs" aria-label="日志级别">${[['all','全部'],['warn','警告'],['error','错误']].map(([value,label])=>html`<button class=${level===value?'active':''} onClick=${()=>setLevel(value)}>${label}</button>`)}</div><a class="console-btn" href="/api/diag">导出诊断包</a></div><section class="board"><div class="console-log-head"><select aria-label="按来源筛选" value=${category} onChange=${e=>setCategory(e.target.value)}><option value="">全部来源</option>${categories.map(name=>html`<option value=${name}>${name}</option>`)}</select><input type="search" aria-label="搜索日志内容" placeholder="搜索日志内容" value=${query} onInput=${e=>setQuery(e.target.value)}/><button class="btn-light" onClick=${()=>{if(!paused)frozen.current=items;setPaused(!paused);}}>${paused?'继续更新':'暂停更新'}</button></div>${error?html`<p class="console-log-error" role="alert">${error}</p>`:null}<div class="console-log-scroll"><div class="console-log-row console-log-labels"><span>时间</span><span>级别</span><span>来源</span><span>内容</span></div>${shown.length?shown.slice().reverse().map(entry=>html`<div class="console-log-row" key=${entry.n}><time>${entry.t}</time><span>${({warn:'警告',error:'错误',info:'信息'}[entry.level]||entry.level)}</span><span>${entry.cat}</span><div>${entry.msg}${entry.step||entry.raw||entry.hint?html`<details><summary>步骤与原始信息</summary>${entry.step?html`<p>步骤：${entry.step}</p>`:null}${entry.raw?html`<p>原文：<code>${entry.raw}</code></p>`:null}${entry.hint?html`<p>建议：${entry.hint}</p>`:null}</details>`:null}</div></div>`):html`<p class="console-log-empty">${query||category||level!=='all'?'没有匹配的日志':'暂无日志'}</p>`}</div><div class="console-log-footer"><span>当前 ${shown.length} 条${paused?' · 已暂停展示':''}</span><button class="btn-light" onClick=${copy}>复制当前日志</button></div></section></div>`;
}
