// Isolated local Edge QA only; all package/controller data are explicit fixtures.
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import net from 'node:net';
const arg=name=>process.argv[process.argv.indexOf(`--${name}`)+1];
const shots=arg('shots');mkdirSync(shots,{recursive:true});
const page=await(await fetch(`http://127.0.0.1:${arg('cdp')}/json/new?about:blank`,{method:'PUT'})).json();
const ws=new WebSocket(page.webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let sequence=0;const pending=new Map(),errors=[],report={errors,layouts:[]};
ws.addEventListener('message',event=>{const r=JSON.parse(event.data);if(r.id){const task=pending.get(r.id);pending.delete(r.id);r.error?task.reject(Error(JSON.stringify(r.error))):task.resolve(r.result);}else if(r.method==='Runtime.exceptionThrown')errors.push(r.params.exceptionDetails.text);});
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(Error(`CDP timeout: ${method}`));},15000);pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});
const js=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result?.value;};
const wait=async(expression,label,timeout=16000)=>{const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await js(expression))return;await new Promise(resolve=>setTimeout(resolve,80));}throw Error(`Timed out: ${label}`);};
const click=async op=>{await wait(`document.querySelector('[data-update="${op}"]')&&!document.querySelector('[data-update="${op}"]').disabled`,`${op} enabled`);await js(`document.querySelector('[data-update="${op}"]').click()`);};
const fixture=values=>new Promise((resolve,reject)=>{const socket=net.createConnection({host:'127.0.0.1',port:Number(arg('fixture-port'))});let data='';socket.on('connect',()=>socket.write(JSON.stringify({op:'fixture',...values})+'\n'));socket.on('data',chunk=>{data+=chunk;if(data.includes('\n')){socket.end();const r=JSON.parse(data.split('\n')[0]);r.ok?resolve(r):reject(Error(JSON.stringify(r)));}});socket.on('error',reject);});
const nativeFixture=values=>fixture({op:'native_fixture',...values});
const nativePost=body=>js(`fetch('/api/session').then(r=>r.json()).then(s=>fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF':s.csrf},body:JSON.stringify(${JSON.stringify(body)})})).then(async r=>({status:r.status,data:await r.json()}))`);
const acknowledge=async()=>{
  await wait(`document.querySelector('[data-native-confirm]')?.disabled===false`,'native acknowledgement enabled');
  await js(`document.querySelector('[data-native-confirm]').click()`);
};
const nativeSettled=phase=>wait(`window.__updates.some(r=>r.native?.phase===${JSON.stringify(phase)}&&!r.native?.submitting)`,phase);
const state=()=>js(`fetch('/api/updates').then(r=>r.json())`);
const settled=async(phase='completed')=>wait(`window.__updates.some(r=>r.job?.phase===${JSON.stringify(phase)}&&!r.busy)`,phase);
const clear=()=>js('window.__updates=[]');
const upload=async(data,name)=>{
  await js(`(()=>{const input=document.querySelector('[aria-label="软件更新包"]');const bytes=Uint8Array.from(atob(${JSON.stringify(data)}),c=>c.charCodeAt(0));const dt=new DataTransfer();dt.items.add(new File([bytes],${JSON.stringify(name)},{type:'application/octet-stream'}));input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
};
try{
  await send('Runtime.enable');await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__updates=[];window.__updatePosts=[];{const original=window.fetch;window.fetch=async function(...args){if(args[0]==='/api/updates'&&args[1]?.method==='POST')window.__updatePosts.push(JSON.parse(args[1].body));const response=await original.apply(this,args);if(String(args[0]).includes('/api/updates')){try{window.__updates.push(await response.clone().json());}catch{}}return response;};}`});
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  const overview=arg('url').split('#')[0]+'#/';
  await send('Page.navigate',{url:overview});
  await wait(`window.__updates.some(r=>r.available)`,'overview detects an older host');
  if(await js(`!!document.querySelector('[data-update-notice]')||window.__updatePosts.length>0`))throw Error('an old host should not receive overview check requests');
  await nativeFixture({legacy_host:false});await fixture({no_release:false});await send('Page.reload');
  await wait(`document.querySelector('[data-update-notice]')?.textContent.includes('2026.10.27')`,'overview discovers stable release');
  report.overview_notice=await js(`window.__updatePosts.length===1&&window.__updatePosts[0].op==='check_auto'&&document.querySelector('[data-update-notice]').textContent.includes('查看更新')`);
  await js(`document.querySelector('[data-update-notice]').click()`);
  await wait(`location.hash==='#/settings/updates'&&document.activeElement.matches('[data-updates]')&&document.querySelector('[data-update="online"]')?.disabled===false`,'overview opens software settings');
  await nativeFixture({legacy_host:true});await fixture({no_release:true});
  await send('Page.navigate',{url:arg('url')});await send('Page.reload');
  await wait(`document.querySelector('[data-updates]')&&!document.querySelector('[data-update="check"]').disabled`,'software update card');
  if(!await js(`document.querySelector('[data-updates]').textContent.includes('一次性准备')&&!window.__updatePosts.length`))throw Error('legacy host must show manual preparation without sending automatic checks');
  report.stage1_old_helper=await js(`document.querySelector('[data-native-updates]').textContent.includes('先安装匹配')&&document.querySelector('[data-update="native_install"]').disabled`);
  await clear();await click('check');await settled();
  report.no_release=(await state()).source_state==='no_release';
  await fixture({no_release:false,waiting:true});
  await clear();await click('check');await settled();await clear();await click('download');await settled('waiting_validation');
  report.downloaded_waiting=!(await state()).ready&&await js(`document.querySelector('[data-update="install"]').disabled`);
  await fixture({waiting:false});await clear();await click('validate');await settled();
  report.validated=(await state()).ready;
  await clear();await click('install');
  await wait(`window.__updates.some(r=>r.busy&&r.job?.operation==='install')`,'install job active');
  await send('Page.reload');
  await wait(`document.querySelector('[data-updates]')`,'update page reloaded');
  await wait(`window.__updates.some(r=>!r.busy&&r.installed==='2026.10.27')`,'installed after refresh');
  report.installed=(await state()).installed==='2026.10.27';
  report.reloaded_task=(await state()).job?.operation==='install';
  await clear();await click('rollback');await settled();
  report.rollback=(await state()).installed==='2026.10.26';
  await clear();await upload(readFileSync(arg('package')).toString('base64'),'fixture.software-update.tar');await settled();
  report.uploaded=(await state()).ready;
  for(const width of [1440,390,320]){
    await send('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:width<500});
    await js(`window.scrollTo(0,0);new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    if(await js('document.documentElement.scrollWidth>innerWidth+2'))throw Error(`layout overflows at ${width}`);
    const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(`${shots}/updates-${width}.png`,Buffer.from(shot.data,'base64'));report.layouts.push(width);
  }
  report.privileged_fields_rejected=await js(`fetch('/api/session').then(r=>r.json()).then(s=>fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF':s.csrf},body:JSON.stringify({op:'download',url:'http://evil.example'})})).then(r=>r.status===400)`);
  await nativeFixture({legacy_host:false});await send('Page.reload');
  await wait(`document.querySelector('[data-update="online"]')?.disabled===false&&window.__updatePosts.some(p=>p.op==='check_auto')`,'automatic stable release check');
  await wait(`window.__updates.length>=4`,'multiple status polls');
  report.offer=await js(`document.querySelector('[data-update="online"]').textContent==='立即更新'&&document.querySelector('[data-update-offer]').textContent.includes('2026.10.27')&&document.querySelector('[data-update-offer]').textContent.includes('本地语音坐下/站起')&&window.__updatePosts.filter(p=>p.op==='check_auto').length===1`);
  for(const width of [1440,390,320]){
    await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<500});
    await js(`document.querySelector('[data-updates]').scrollIntoView();new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    if(await js('document.documentElement.scrollWidth>innerWidth+2'))throw Error(`online layout overflows at ${width}`);
    const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    writeFileSync(`${shots}/online-offer-${width}.png`,Buffer.from(shot.data,'base64'));
  }
  // The independent coordinator accepts the click, then the web connection drops.
  // Keep this page alive so a fresh mount cannot accidentally clear stale feedback.
  await js(`{
    const original=window.fetch;let offlineUntil=0;
    window.fetch=async function(path,options){
      if(path==='/api/updates'&&Date.now()<offlineUntil)throw TypeError('test connection drop');
      const result=await original.apply(this,arguments);
      if(path==='/api/updates'&&options?.method==='POST'&&JSON.parse(options.body).op==='online'&&result.ok){
        offlineUntil=Date.now()+2000;throw TypeError('test response lost');
      }
      return result;
    }
  }`);
  await clear();await click('online');
  await wait(`window.__updates.some(r=>r.busy&&r.job?.operation==='online')`,'single online job running');
  report.single_click=await js(`JSON.stringify(window.__updatePosts.filter(p=>p.op!=='check_auto'))===JSON.stringify([{op:'online',version:'2026.10.27'}])`);
  await wait(`document.querySelector('[data-updates]').textContent.includes('网页连接中断')`,'live reconnect');
  await wait(`document.querySelector('[data-update-phase="completed"]')?.textContent.includes('已更新')&&!document.querySelector('[data-updates]').textContent.includes('网页连接中断')`,'real result after lost response');
  await send('Page.reload');
  await wait(`document.querySelector('[data-update-phase="completed"]')?.textContent.includes('已更新')&&document.querySelector('[data-update-installed]')?.textContent==='2026.10.27'`,'durable online result after reload');
  report.online_installed=(await state()).job?.operation==='online';
  await clear();await upload(Buffer.from('not an update archive').toString('base64'),'invalid.tar');
  await wait(`document.querySelector('[data-updates] [role="alert"]')`,'invalid package error');
  report.invalid_package=true;
  // The preceding flow deliberately keeps the signed legacy 20261027 fixture.
  // Native fields below are a separate explicit fixture for the new panel.
  await nativeFixture({software_version:'2026.10.28'});await send('Page.reload');
  await wait(`document.querySelector('[data-update-installed]')?.textContent==='2026.10.28'&&document.querySelector('[data-native-updates]')?.textContent.includes('待安装')`,'native pending after webpage update');
  report.native_pending=await js(`document.querySelector('[data-update="native_install"]').disabled&&!document.querySelector('[data-native-confirm]').checked`);
  report.native_no_automatic_call=!readFileSync(`${arg('shots')}/../native.jsonl`,'utf8').split('\n').filter(Boolean).map(JSON.parse).some(r=>r.action!=='status');
  for(const body of [{op:'native_install'},{op:'native_install',confirmed:1},{op:'native_install',confirmed:true,by:'Other'},
      {op:'native_recover',confirmed:true,force:true},{op:'native_status',path:'/tmp/x'},{op:'native_install',confirmed:true,url:'https://evil.example'}]){
    const response=await nativePost(body);if(response.status!==400)throw Error(`native schema accepted ${JSON.stringify(body)}`);
  }
  report.native_schema=true;
  report.native_csrf_origin=await js(`Promise.all([
    fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF':'wrong'},body:'{"op":"native_install","confirmed":true}'}).then(r=>r.status===403),
    fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"op":"native_install","confirmed":true}'}).then(r=>r.status===403)
  ]).then(r=>r.every(Boolean))`);
  await acknowledge();await clear();await click('native_install');
  await wait(`window.__updates.some(r=>r.native?.active&&r.native?.phase==='replacing')`,'native progress');
  report.native_software_exclusion=await js(`document.querySelector('[data-update="install"]').disabled&&document.querySelector('[data-update="rollback"]').disabled`);
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await send('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await send('Page.reload');await nativeSettled('completed');
  report.native_reconnect_success=await js(`document.querySelector('[data-native-updates]').textContent.includes('实际运行身份已核验')`);
  await nativeFixture({available:false,target_sha256:null});await send('Page.reload');
  await wait(`document.querySelector('[data-native-updates]')?.textContent.includes('没有可安装')`,'native no payload');report.native_no_payload=true;
  await nativeFixture({available:true,target_sha256:'f'.repeat(64),installed_sha256:'d'.repeat(64),running_verified:false,reason:'未知自定义主程序，拒绝自动安装'});await send('Page.reload');
  await wait(`document.querySelector('[data-native-updates]')?.textContent.includes('未知自定义主程序')`,'unsupported native');
  await acknowledge();report.native_unsupported=await js(`document.querySelector('[data-update="native_install"]').disabled`);
  await nativeFixture({active:false,phase:'completed',installed_sha256:'e'.repeat(64),running_verified:true,reason:null,failure:true});await send('Page.reload');
  await wait(`document.querySelector('[data-native-updates]')?.textContent.includes('待安装')`,'native failure fixture ready');
  await acknowledge();await clear();await click('native_install');await nativeSettled('recovery_required');
  report.native_failure=await js(`document.querySelector('[data-native-updates]').textContent.includes('模拟启动核验失败')`);
  await send('Page.reload');await wait(`document.querySelector('[data-native-phase="recovery_required"]')`,'persisted recovery after reconnect');
  await acknowledge();await clear();await click('native_recover');await nativeSettled('rolled_back');
  report.native_recovery=await js(`document.querySelector('[data-native-updates]').textContent.includes('已恢复并核验旧主程序')`);
  await nativeFixture({failure:false,bundle_schema:'microduck-controller-payload/1',bundle_current:false,
    restorable:false,installed_sha256:'f'.repeat(64),phase:'idle',components:{robotd:{installed_sha256:'f'.repeat(64)},padd:{installed_sha256:'a'.repeat(64)}}});
  await send('Page.reload');await wait(`document.querySelector('[data-native-updates]')?.textContent.includes('配套驱动待安装')`,'bundle pending despite robotd current');
  await acknowledge();await click('native_install');await nativeSettled('completed');
  report.bundle_complete=await js(`document.querySelector('[data-native-updates]').textContent.includes('映射接口和服务权限已核验')&&document.querySelector('[data-update="rollback"]').disabled`);
  await acknowledge();await click('native_restore');await nativeSettled('restored');
  report.bundle_restore=await js(`document.querySelector('[data-native-updates]').textContent.includes('旧配套驱动与原服务配置已恢复')`);
  for(const width of [1440,390,320]){
    await send('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:width<500});
    await js(`document.querySelector('[data-native-updates]').scrollIntoView();document.querySelector('[data-native-updates]').getBoundingClientRect().width`);
    if(await js('document.documentElement.scrollWidth>innerWidth+2'))throw Error(`native layout overflows at ${width}`);
    const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(`${shots}/native-${width}.png`,Buffer.from(shot.data,'base64'));
  }
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify(report));
}finally{ws.close();}
