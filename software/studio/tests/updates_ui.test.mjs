import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const source=await fs.readFile(new URL('../web/updates-ui-state.js',import.meta.url),'utf8');
const {updateMessage,updateActions,nativeMessage,nativeActions,discoverUpdateNotice}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
assert.match(updateMessage({available:false}),/尚未安装/);
assert.match(updateMessage({available:true,source_state:'no_release'}),/尚无发布/);
assert.match(updateMessage({available:true,source_state:'network_error'}),/网络/);
assert.match(updateMessage({available:true,job:{phase:'rolled_back',error:'health failed'}}),/已恢复旧版/);
assert.match(updateMessage({available:true,job:{phase:'waiting_validation',error:'模型仍在接管'}}),/重新验证.*模型仍在接管/);
assert.match(updateMessage({available:true,busy:true,job:{phase:'preflight'}}),/模型接管/);
assert.equal(updateActions({available:true,busy:true,ready:true}).install,false);
assert.equal(updateActions({available:true,busy:false,ready:false,staged:{version:'2026.10.27'}}).install,false);
assert.equal(updateActions({available:true,busy:false,ready:true}).install,true);
assert.equal(updateActions({available:true,busy:false,candidate:{version:'2026.10.27'},source_state:'available'}).download,true);
const release={available:true,online_update:true,installed:'2026.11.4',ready:false,
  candidate:{version:'2026.11.20',changelog:'语音更新'},source_state:'available'};
assert.equal(updateActions(release).online,true,'the displayed stable release needs no separate download or upload');
for(const change of [{online_update:false},{online_update:undefined},{online_update:'true'},
  {source_state:'network_error'},{source_state:'installed_unknown'},{source_state:'up_to_date'},
  {candidate:null},{installed:'2026.11.20'},{busy:true},{native:{active:true}},{native:{submitting:true}}]){
  assert.equal(updateActions({...release,...change}).online,false);
}
assert.equal(updateActions(release,true).online,false);
assert.match(updateMessage({...release,job:{operation:'online',phase:'completed',result:{outcome:'applied',to:'2026.11.20'}}}),/已更新.*2026\.11\.20/);
assert.match(updateMessage({...release,ready:true,job:{operation:'rollback',phase:'completed',result:{outcome:'applied',to:'2026.11.4'}}}),/已退回.*2026\.11\.4/);
assert.match(updateMessage({...release,source_state:'installed_unknown'}),/当前版本/);
assert.equal(typeof nativeMessage,'function','separate native stage missing');
const native={service_available:true,available:true,supported:true,current:false,active:false,phase:'idle'};
const stage2={available:true,busy:false,ready:true,installed:'2026.10.28',native};
assert.match(nativeMessage(native),/待安装/);
assert.equal(nativeActions(stage2,false,false).install,false,'explicit support/unload acknowledgement required');
assert.equal(nativeActions(stage2,false,true).install,true);
assert.equal(nativeActions({...stage2,native:{...native,supported:false}},false,true).install,false);
assert.match(nativeMessage({...native,supported:false,reason:'未知主程序'}),/未知主程序/);
assert.match(nativeMessage({...native,available:false}),/没有.*主程序/);
assert.match(nativeMessage({...native,current:true}),/实际运行.*核验/);
assert.equal(nativeActions({...stage2,busy:true},false,true).install,false);
const interrupted={...stage2,native:{...native,active:true,phase:'recovery_required'}};
assert.equal(nativeActions(interrupted,false,true).recover,true);
assert.equal(nativeActions(interrupted,false,false).recover,false);
assert.equal(updateActions(interrupted).install,false);
assert.equal(updateActions({...interrupted,can_rollback:true}).rollback,false);
assert.equal(updateActions(interrupted).check,true);
assert.match(nativeMessage(interrupted.native),/恢复/);
const bundle={...native,bundle_schema:'microduck-controller-payload/1',current:true,restorable:true};
assert.match(nativeMessage(bundle),/配套.*实际运行.*核验/);
assert.equal(nativeActions({...stage2,native:bundle},false,true).restore,true);
assert.equal(nativeActions({...stage2,native:bundle},false,false).restore,false);
assert.equal(nativeActions({...stage2,native:{...bundle,active:true}},false,true).restore,false);
assert.equal(updateActions({...stage2,can_rollback:true,native:bundle}).rollback,false,'restore matching native before software rollback');
const factoryCurrent={...bundle,restorable:false};
assert.match(nativeMessage(factoryCurrent),/没有.*旧配套驱动备份/);
assert.doesNotMatch(nativeMessage(factoryCurrent),/请先恢复/);
assert.equal(nativeActions({...stage2,native:factoryCurrent},false,true).restore,false);
const predecessor={...bundle,current:false,phase:'completed',reason:'已完成的 .47 配套驱动请先恢复旧配套驱动；docs/native-update-20261104.md'};
assert.equal(nativeMessage(predecessor),predecessor.reason,'show the signed updater restore-first procedure');
assert.equal(nativeActions({...stage2,native:predecessor},false,true).restore,true,'completed verified predecessor remains explicitly restorable');
assert.equal(nativeActions({...stage2,native:predecessor},false,true).install,false,'restore the old owned bundle before installing');
for(const changed of [{supported:false},{active:true},{submitting:true},{restorable:false},{service_available:false},
  {bundle_schema:'foreign'},{phase:'recovery_required'}]){
  assert.equal(nativeActions({...stage2,native:{...predecessor,...changed}},false,true).restore,false);
}
assert.equal(nativeActions({...stage2,native:predecessor},false,false).restore,false);
assert.equal(nativeActions({...stage2,native:predecessor},true,true).restore,false);
assert.equal(nativeActions({...stage2,busy:true,native:predecessor},false,true).restore,false);
const idleHost={ok:true,available:true,online_update:true,busy:false,installed:'2026.11.4'};
const sharedCheck={...idleHost,busy:true,catalog_checking:true};
const publicOffer={...idleHost,source_state:'available',candidate:{version:'2026.11.20'}};
async function noticeDuring(states){
  const requests=[],waits=[];
  const version=await discoverUpdateNotice(async body=>{
    requests.push(body?.op||'status');
    assert.ok(states.length,'unexpected extra discovery request');
    return states.shift();
  },async ms=>{waits.push(ms);},new AbortController().signal);
  return {requests,waits,version};
}
const shared=await noticeDuring([...Array(9).fill(sharedCheck),publicOffer]);
assert.equal(shared.version,'2026.11.20','show result even when another tab starts a slow check');
assert.ok(shared.waits.reduce((a,b)=>a+b,0)>60000);
assert.ok(shared.requests.every(op=>op==='status'),'do not start another catalog check');
const cold=await noticeDuring([idleHost,sharedCheck,publicOffer]);
assert.deepEqual(cold.requests,['status','check_auto','status']);
assert.equal(cold.version,'2026.11.20');
const mutating=await noticeDuring([{...idleHost,busy:true,job:{operation:'online'}}]);
assert.equal(mutating.version,null);assert.equal(mutating.requests.length,1);
const stuck=await noticeDuring(Array(100).fill(sharedCheck));
assert.equal(stuck.version,null);
assert.ok(stuck.waits.reduce((a,b)=>a+b,0)<=600000);
assert.ok(stuck.waits.reduce((a,b)=>a+b,0)>480000,'cover a catalog and three slow detail requests');
const leave=new AbortController();
let reads=0;
assert.equal(await discoverUpdateNotice(async()=>{reads++;return sharedCheck;},async()=>leave.abort(),leave.signal),null);
assert.equal(reads,1,'leaving the page must cancel subsequent status reads');
assert.equal(await discoverUpdateNotice(async()=>{throw Error('network offline');},async()=>{},new AbortController().signal),null);
console.log('updates UI state checks passed');
