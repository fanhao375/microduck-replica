import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';

const [base, cdp] = process.argv.slice(2);
const target = await (await fetch(`http://127.0.0.1:${cdp}/json/new?about:blank`, {method:'PUT'})).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
let id = 0;
const pending = new Map(), errors = [];
ws.addEventListener('message', event => {
  const m = JSON.parse(event.data);
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const key = ++id, timer = setTimeout(() => {pending.delete(key); reject(Error(`CDP timeout: ${method}`));}, 10000);
  pending.set(key, m => {clearTimeout(timer); m.error ? reject(Error(JSON.stringify(m.error))) : resolve(m.result);});
  ws.send(JSON.stringify({id:key, method, params}));
});
const js = async expression => {
  const r = await send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
  if (r.exceptionDetails) throw Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result?.value;
};
const pause = ms => new Promise(r => setTimeout(r, ms));
const wait = async (expression, label) => {
  for (let n=0;n<100;n++) {if (await js(expression)) return; await pause(40);}
  throw Error(label+'\n'+await js('document.body.innerText'));
};
const shot = async name => {
  if (!process.env.BUS_HEALTH_SHOTS) return;
  await fs.mkdir(process.env.BUS_HEALTH_SHOTS, {recursive:true});
  const r = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:true});
  await fs.writeFile(path.join(process.env.BUS_HEALTH_SHOTS, name+'.png'), Buffer.from(r.data,'base64'));
};
try {
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate', {url:base});
  await wait('document.readyState==="complete"', 'fixture');
  await js(`(async()=>{
    const {h,render}=await import('/vendor/preact-htm.module.js');
    const {ServoPage}=await import('/servo.js');
    const {ImuPage}=await import('/imu.js');
    document.body.innerHTML='<main id="fixture" style="padding:24px;max-width:1440px;margin:auto"></main>';
    const root=document.querySelector('#fixture'); window.calls=[];
    const ids=[20,21,22,23,24,30,31,32,33,34,10,11,12,13,14];
    window.props={sv:{state:'closed',source:'robotd',online:15,total:15,avg_volts:8,holding:0,
      communication:{source:'robotd',session:'s1',age_ms:10,sample_hz:50,window_s:60,
        counters:{bad_checksum:8,failed_reads:97},read_order:{mode:'default',ids,generation:0},
        read_order_guard:{allowed:true,reason:null}},
      rows:ids.map(id=>({id,cn:id===13?'右膝':'关节'+id,en:'joint'+id,group:id>=30?'H':id>=20?'L':'R',
        dir:-1,raw:2049,deg:-0.1,temp:32,volt:8,torque:0,fresh:true,age_ms:10,
        p:5,d:0,p_run:5,d_run:0,limit:1000,offset:-1387,
        comm:{asked:1957,ok:id===13?1860:1957,missed:id===13?97:0,streak:0,max_streak:id===13?3:0},
        health:{asked:1957,ok:id===13?1860:1957,missed:id===13?97:0,rate:id===13?97/1957:0,observed_s:60,window_s:60}}))},
      link:{status:'open',on:()=>()=>{},whenOpen:()=>Promise.resolve(),cmd:async(name,args)=>{
        calls.push({name,args});
        if(name==='servo.ports')return {ok:true,data:{sim:true,port:'sim',ports:[]}};
        if(name==='servo.read_order')return {ok:true,data:{applied:true,saved:true,
          read_order:{...args,ids:ids.filter(x=>x!==args.last_id).concat(args.mode==='id_last'?[args.last_id]:[]),generation:1}}};
        return {ok:true,data:{poses:[]}};
      }},leaveGuards:new Set()};
    window.draw=()=>render(h(ServoPage,props),root);
    window.drawImu=()=>render(h(ImuPage,window.imuProps),root);
    window.clickText=text=>{const b=[...root.querySelectorAll('button')].find(b=>b.textContent.trim()===text);
      if(!b||b.disabled)throw Error('Missing enabled button: '+text);b.click();};
    draw();
  })()`);
  await pause(250);
  assert.equal(await js(`calls.some(c=>c.name==='servo.open')`), false, 'viewing status must not take UART ownership');
  await wait(`document.querySelector('.sv-monitor')`, 'readonly monitor');
  assert.equal(await js(`document.querySelectorAll('.sv-monitor-row').length`),15);
  assert.match(await js(`document.querySelector('.sv-monitor-row[data-id="13"]').innerText`), /32.*8\.0.*5\.0%/s);
  await js(`document.querySelector('.sv-monitor-row[data-id="13"] button').click()`);
  await wait(`document.querySelector('.sv-monitor-detail[data-id="13"]')`, 'detail expanded');
  assert.ok(await js(`!!document.querySelector('.comm-inspector .sv-monitor-detail[data-id="13"]')`),'desktop details belong in the side panel');
  assert.match(await js(`document.querySelector('.sv-monitor-detail[data-id="13"]').innerText`), /1957/);
  await js(`props.sv={...props.sv,rows:props.sv.rows.map(r=>r.id===13?{...r,temp:38,volt:7.6,comm:{...r.comm,asked:2057,ok:1956,missed:101},health:{...r.health,asked:100,ok:96,missed:4,rate:.04}}:r)};draw()`);
  await wait(`document.querySelector('.sv-monitor-row[data-id="13"]').innerText.includes('38')`, 'live row');
  assert.match(await js(`document.querySelector('.sv-monitor-row[data-id="13"]').innerText`), /7\.6.*4\.0%/s);
  assert.match(await js(`document.querySelector('.sv-monitor-detail[data-id="13"]').innerText`), /2057/);
  await js(`props.sv={...props.sv,rows:props.sv.rows.map(r=>r.id===12?{...r,health:{asked:null,ok:null,missed:null,rate:null,observed_s:0},comm:{asked:null,ok:null,missed:4}}:r)};draw()`);
  await wait(`document.querySelector('.sv-monitor-row[data-id="12"]').innerText.includes('—')`, 'unknown denominator');
  assert.doesNotMatch(await js(`document.querySelector('.sv-monitor-row[data-id="12"]').innerText`), /0\.0%/);
  await js(`props.sv={...props.sv,rows:props.sv.rows.map(r=>r.id===13?{...r,p_run:null,d_run:null,p:null,d:null,
    speed_raw:42,velocity_rad_s:.12,
    parameters:{source:'robotd_before_arm_cache',age_ms:null,runtime:{pdi:[3,0,0],observed:false},eeprom:{pdi:[32,40,0]},before_arm:{acceleration_raw:12},configured:{kp:5,kd:0,torque_limit:900}}}:r)};draw()`);
  await js(`document.querySelector('.comm-inspector details.comm-parameters').open=true`);
  await wait(`document.querySelector('.comm-inspector').innerText.includes('32 / 40 / 0')`, 'parameter cache values');
  assert.match(await js(`document.querySelector('.comm-inspector').innerText`), /上力前|缓存/);
  assert.match(await js(`document.querySelector('.comm-inspector').textContent`), /配置限力900/);
  assert.match(await js(`document.querySelector('.comm-inspector').textContent`), /速度原始值42/);
  await wait(`!document.querySelector('.sv-loading')`, '3D model ready');
  assert.ok(await js(`document.querySelector('.sv-stage').getBoundingClientRect().height>=300`),'expanded details cannot squeeze the 3D stage');
  await wait(`document.querySelector('.sv-stage canvas').__duck3d?.frames>0`, '3D has drawn');
  assert.equal(await js(`document.querySelector('details.comm-advanced').open`),false,'advanced configuration starts folded');
  await shot('servos-desktop');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(100);
  assert.ok(await js(`document.documentElement.scrollWidth<=document.documentElement.clientWidth+2`),'mobile must not overflow');
  await js(`document.querySelector('.sv-stage').scrollIntoView()`);
  await pause(200);
  await shot('servos-mobile');
  await js(`scrollTo(0,0)`);
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  // Local drafts are not applied configuration. Require actual readback on acknowledgement.
  await js(`document.querySelector('details.comm-advanced').open=true`);
  await js(`const s=document.querySelector('select[aria-label="通信读取方案"]');s.value='id_last';s.dispatchEvent(new Event('change',{bubbles:true}));`);
  await pause(80);
  await js(`clickText('应用读取顺序')`);
  await wait(`calls.some(c=>c.name==='servo.read_order')`, 'read order command');
  assert.deepEqual(await js(`calls.find(c=>c.name==='servo.read_order').args`),{mode:'id_last',last_id:13});
  await js(`props.sv={...props.sv,communication:{...props.sv.communication,read_order_guard:{allowed:false,reason:'先停止模型并卸力'}}};draw()`);
  await wait(`document.querySelector('[data-read-order-apply]').disabled`, 'enabled-model guard');
  await shot('read-order-desktop');
  await js(`props.sv={...props.sv,communication:{...props.sv.communication,age_ms:4000},rows:props.sv.rows.map(r=>({...r,fresh:false,age_ms:4000}))};draw()`);
  await wait(`document.querySelector('.sv-monitor-row[data-id="13"]').classList.contains('stale')`, 'stale samples');
  assert.match(await js(`document.querySelector('.sv-monitor-row[data-id="13"]').innerText`), /过期|未更新/);
  await js(`window.imuProps={live:true,link:props.link,st:{mode:'motion',posture:'holding',gravity:[0,0,-1],joints_age_ms:10,imu:{status:'ok'}},
    imu:{source:'bus',mode:'motion',mount:{quat:[1,0,0,0],verified:true},redo:{},
      imu_bus:{board:'imu_to_dxl',protocol:'feetech',identity:[1,1,77,68,200]},
      metadata:{chip:'LSM6DSV16X',board:'imu_to_dxl',protocol:'feetech',firmware:'1.1'},
      health:{source:'bus',session:'imu1',ready:true,fresh:true,age_ms:7,update_hz:100,observed_s:20,reason:null,last_error:'test checksum diagnostic',rate_basis:'accepted_snapshot_timestamp',counters:{invalid:2}},
      gyro:[.1,.2,.3],quaternion_wxyz:[1,0,0,0],accel_m_s2:null,temperature_c:null,values_age_ms:10}};drawImu()`);
  await wait(`document.querySelector('.imu-health')`, 'IMU health card');
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /100.*Hz/s);
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /1\.0000/);
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /未提供/);
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /test checksum diagnostic/);
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /有效快照更新/);
  await shot('imu-desktop');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(100);
  assert.ok(await js(`document.documentElement.scrollWidth<=document.documentElement.clientWidth+2`),'IMU mobile must not overflow');
  await shot('imu-mobile');
  await js(`imuProps.imu={...imuProps.imu,values_age_ms:4000};drawImu()`);
  await wait(`document.querySelector('.imu-health .comm-sensor-values').classList.contains('stale')`, 'fresh source cannot disguise stale vectors');
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /数值.*过期/);
  await js(`imuProps.live=false;drawImu()`);
  await wait(`document.querySelector('.imu-health').classList.contains('stale')`, 'IMU disconnected');
  assert.match(await js(`document.querySelector('.imu-health').innerText`), /断开|过期|未更新/);
  assert.equal(await js(`calls.filter(c=>c.name==='servo.open').length`),0);
  await js(`draw()`);
  await wait(`document.querySelector('.sv-monitor')`, 'return to servo monitor');
  await js(`clickText('调舵机 / 标定')`);
  await pause(100);
  assert.equal(await js(`calls.filter(c=>c.name==='servo.open').length`),0,'debug tab still needs explicit takeover');
  await js(`clickText('开始调舵机')`);
  await wait(`calls.some(c=>c.name==='servo.open')`, 'explicit debug takeover');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,checks:['readonly','15 rows','live detail','unknown rate','order guard','mobile width','stale','IMU metadata'],errors}));
} finally {ws.close();}
