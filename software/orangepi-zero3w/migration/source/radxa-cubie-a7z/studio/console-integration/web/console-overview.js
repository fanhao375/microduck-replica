import {html,useEffect,useRef,useState} from './vendor/preact-htm.module.js';
import {Duck3D} from './duck3d.js';
import {Icon} from './console-icons.js';
import {walkModel} from './startup-state.js';
const num=(v,s='',digits=null)=>Number.isFinite(v)?`${digits===null?v:v.toFixed(digits)}${s}`:'—';
const POSE={limp:'已卸力',holding:'站立',homing:'正在站起',policy:'准备好走',walking:'行走中',sitting:'坐姿',commissioning:'标定中',read_only:'只读',servo_session:'维护中',starting:'启动中',absent:'未运行'};

function LiveDuck({st,live}) {
  const canvas=useRef(null),viewer=useRef(null),[message,setMessage]=useState('3D 模型加载中…');
  useEffect(()=>{
    const d=new Duck3D(canvas.current,{view:'front34',pageScroll:true,centerX:.5,centerY:.48,fill:.76});viewer.current=d;
    let alive=true;d.load('model/').then(()=>{if(alive)setMessage('');}).catch(error=>{if(alive)setMessage('3D 模型未加载：'+error.message);});
    return ()=>{alive=false;d.dispose();};
  },[]);
  useEffect(()=>{viewer.current?.setJoints(st?.joints);viewer.current?.setGravity(st?.gravity);},[st?.joints,st?.gravity]);
  const fresh=live&&Number.isFinite(st?.joints_age_ms)&&st.joints_age_ms<1000;
  return html`<section class="board"><div class="console-card-heading"><h2><${Icon} name="imu"/>小鸭姿态</h2><div><small>${fresh?'实时回读':'等待有效回读'}</small> <button class="btn-light" onClick=${()=>viewer.current?.resetView()}>回正视角</button></div></div><div class="console-duck-stage"><canvas ref=${canvas} class=${fresh?'':'stale'} aria-label="小鸭实时三维姿态"/>${message?html`<div class="console-model-message" role="status">${message}</div>`:null}</div><div class="console-pose-values"><div>机身倾角<b>${live?num(st?.tilt_deg,'°',1):'—'}</b></div><div>当前姿态<b>${live?POSE[st?.posture]||'未知':'—'}</b></div></div></section>`;
}

export function ConsoleOverview({st,pad,tune,live,guide}) {
  const model=walkModel(tune),servo=st?.servos||{},cpu=st?.cpu||{};
  const metrics=[['device','舵机在线',live?`${servo.online??'—'}`:'—',servo.total!=null?`/ ${servo.total}`:'',live&&servo.hottest?`最热 ${servo.hottest.c}°C · ${servo.hottest.name}`:'等待有效回读'],['imu','IMU 姿态',live?({ok:'正常',unverified:'待验证',frozen:'冻结',missing:'未连接'}[st?.imu?.status]||'—'):'—','',live&&st?.imu?.status==='ok'?'安装方向已确认':'安装方向与采集状态'],['activity','控制频率',live?num(st?.loop?.hz,'',1):'—','Hz','总线采集'],['thermometer','主板温度',live?num(cpu.c):'—','°C',cpu.throttle?.active?'正在降频':'主板回读']];
  const values=[['运行模式',({motion:'运动模式',commissioning:'标定模式',read_only:'只读模式'}[st?.mode]||'等待读回')],['动力状态',live&&st?.estop?.state==='confirmed'?'已卸力':live?POSE[st?.posture]||'未知':'未知'],['当前姿态',live?POSE[st?.posture]||'未知':'—'],['控制权',st?.servo_session?'设备维护':live?'主程序':'未知'],['行走模型',live&&model?`${model.name||'行走模型'} · ${model.loaded?'已加载':'未加载'}`:'等待读回'],['供电',live?num(st?.battery?.volts,' V'):'—'],['实体手柄',st?.pad?.attached||st?.pad?.active?'已连接':st?.pad?.known?'未连接':'状态未提供']];
  const todo=(st?.todo||[]).filter(item=>!['imu_unverified','servo_session','starting'].includes(item.key)).slice(0,2);
  return html`<div class="console-overview">${guide}<div class="console-overview-metrics">${metrics.map(([icon,label,value,unit,detail])=>html`<section class="board console-metric"><div class="console-metric-label"><${Icon} name=${icon} size=${15}/>${label}</div><div class="console-metric-value">${value}<small>${unit}</small></div><p>${detail}</p></section>`)}</div><div class="console-overview-grid"><${LiveDuck} st=${st} live=${live}/><section class="board"><div class="console-card-heading"><h2>设备状态</h2></div><div class="console-device-values">${values.map(([label,value])=>html`<div class="console-value-row"><strong>${label}</strong><span>${value}</span></div>`)}</div></section></div>${live&&todo.length?html`<section class="board console-attention"><h2>运行关注</h2>${todo.map(item=>html`<a href="#/logs">${item.msg}<small>${item.hint}</small></a>`)}</section>`:null}</div>`;
}
