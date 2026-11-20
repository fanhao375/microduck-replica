import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import * as state from '../web/ai-ui-state.js';

// Exercise the real component callbacks with hooks, without a browser or cloud.
test('a remote revision rejection clears earlier successful test and conversation', async () => {
  const source = (await readFile(new URL('../web/settings-ai.js',import.meta.url),'utf8'))
    .replace(/^import .*;\r?\n/gm,'').replace('export function AiSettings','function AiSettings');
  const slots = [], effects = [], mounted = new Set();
  let cursor = 0, buttons = {}, inputs = {}, output;
  const html = (strings,...values) => {
    for (let index=0;index<values.length;index++) {
      const prefix = strings.slice(0,index+1).join('');
      if (typeof values[index] !== 'function') continue;
      if (/onClick=$/.test(strings[index])) {
        const action = [...prefix.matchAll(/data-ai="([^"]+)"/g)].at(-1)?.[1];
        if (action) buttons[action] = values[index];
      }
      if (/onInput=$/.test(strings[index])) {
        const label = [...prefix.matchAll(/aria-label="([^"]+)"/g)].at(-1)?.[1];
        if (label) inputs[label] = values[index];
      }
    }
    return {strings,values};
  };
  const flatten = node => node?.strings ? node.strings.map((part,index)=>part+flatten(node.values[index])).join('')
    : Array.isArray(node) ? node.map(flatten).join('') : typeof node === 'function' || node == null ? '' : String(node);
  const item = {id:'a',revision:1,name:'saved',provider:'doubao_ark',base_url:state.ARK_URL,model:'manual',auth_mode:'bearer',key_configured:true,ready:true};
  let chats = 0;
  const api = async (_path,body) => !body ? {ok:true,connections:[item],current_id:'a'}
    : body.op === 'test' ? {ok:true,id:'a',revision:1,text:'verified'}
    : ++chats === 1 ? {ok:true,id:'a',revision:1,text:'old assistant reply'}
    : {ok:false,code:'stale',error:'连接已更改'};
  const context = vm.createContext({...state,html,AbortController,
    TtsSettings:()=>null,AiTools:()=>null,AiCameraPreview:()=>null,
    document:{getElementById:()=>true},
    useState(initial) {const index=cursor++; if (!(index in slots)) slots[index]=typeof initial === 'function' ? initial() : initial;
      return [slots[index],value=>{slots[index]=typeof value === 'function' ? value(slots[index]) : value;}];},
    useRef(initial) {const index=cursor++;return slots[index] ||= {current:initial};},
    useEffect(effect) {const index=cursor++;if (!mounted.has(index)) {mounted.add(index);effects.push(effect);}},
  });
  vm.runInContext(source+'\nthis.Component=AiSettings;',context);
  const render = () => {cursor=0;buttons={};inputs={};output=context.Component({api});while(effects.length) effects.shift()();};
  const settle = async () => {await new Promise(setImmediate);render();};
  render();await settle();
  buttons.test();await settle();
  assert.match(flatten(output),/测试成功：/);
  inputs['试聊内容']({target:{value:'old user message'}});render();buttons.send();await settle();
  assert.match(flatten(output),/old assistant reply/);
  inputs['试聊内容']({target:{value:'new message'}});render();buttons.send();await settle();
  assert.match(flatten(output),/连接已更改/);
  assert.doesNotMatch(flatten(output),/old assistant reply|old user message|测试成功：/);
});
