import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {hardwareProblem, startupStep, startupToken} from '../workspace/studio/web/startup-state.js';
import {normalizeRoute, serverRoute, routeInfo, PAGES, SEARCH} from '../workspace/studio/web/console-routes.js';

const WEB = new URL('../workspace/studio/web/', import.meta.url);
function fixture() {
  return {
    st: {
      build:'fixture-build', mode:'motion', posture:'limp', servo_session:false,
      servos:{online:15,total:15}, imu:{status:'ok',generation:1}, battery:{volts:7.8,level:'ok'},
      estop:{state:'confirmed'},
      commit:{centered:15,pending:false,dirty:false,ready:true,items:[{key:'servo',ok:true},{key:'imu',ok:true,default:false}]},
      onboarding:{rows:[
        {key:'servo',state:'ok',marked:null,when:1,text:'zeroes read back'},
        {key:'imu',state:'ok',marked:null,when:2,text:'mount checked'},
        {key:'commit',state:'ok',marked:null,when:3,text:'committed'},
        {key:'estop',state:'no',marked:null}, {key:'walk',state:'no',marked:null},
      ]},
    },
    sv:{state:'closed'}, imu:{mount:{verified:true}},
    tune:{ready:true,models:{slots:[{slot:'walk',path:'/selected/policy.onnx',loaded:true,name:'Walk'}]}},
    live:true,open:true,checked:true,choice:'web',stopVerified:false,standObserved:false,
  };
}
function row(input,key) { return input.st.onboarding.rows.find(value=>value.key===key); }

// Capture VNodes/callbacks without a DOM or a second copy of the workflow rules.
const html = (strings,...values) => ({strings:[...strings],values});
function nodes(node, visit) {
  if (Array.isArray(node)) return node.forEach(child=>nodes(child,visit));
  if (!node?.strings) return;
  visit(node); node.values.forEach(child=>nodes(child,visit));
}
function attributeCallback(node, attribute, predicate=()=>true) {
  let result;
  nodes(node,tag=>{
    if(!predicate(tag.strings.join('')))return;
    tag.strings.forEach((part,index)=>{if(part.endsWith(attribute+'=')&&typeof tag.values[index]==='function')result=tag.values[index];});
  });
  assert.equal(typeof result,'function',`expected ${attribute} callback`);
  return result;
}
function flatten(node) {
  if(Array.isArray(node))return node.map(flatten).join('');
  if(node?.strings)return node.strings.map((part,index)=>part+flatten(node.values[index])).join('');
  return typeof node==='string'||typeof node==='number'?String(node):'';
}
function moduleSource(name) {
  return fs.readFileSync(new URL(name,WEB),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/export function /g,'function ');
}
function flowHarness(input=fixture()) {
  const slots=[], dependencies=new Map(), cleanups=new Map(), effects=[], listeners=new Map();
  let cursor=0, dirty=false, output, lastStep, lastArgs;
  const context=vm.createContext({
    html, Icon:()=>null, HandBackButton:()=>null, hardwareProblem,startupToken,
    startupStep(args){lastArgs=args;return lastStep=startupStep(args);},
    localStorage:{getItem:()=>null,setItem(){}}, location:{hash:'#/overview'},
    window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name)=>listeners.delete(name)},
    useState(initial){const index=cursor++;if(!(index in slots))slots[index]=typeof initial==='function'?initial():initial;
      return [slots[index],value=>{const next=typeof value==='function'?value(slots[index]):value;if(!Object.is(next,slots[index])){slots[index]=next;dirty=true;}}];},
    useRef(initial){const index=cursor++;return slots[index] ||= {current:initial};},
    useEffect(effect,deps){const index=cursor++,old=dependencies.get(index);
      if(!old||deps.some((value,i)=>!Object.is(value,old[i]))){dependencies.set(index,[...deps]);effects.push(()=>{cleanups.get(index)?.();cleanups.set(index,effect());});}},
  });
  vm.runInContext(moduleSource('console-startup.js')+'\nthis.Flow=useStartupFlow;',context);
  const props={...input,link:{reconnect(){},cmd:async()=>({ok:false,reason:'no hardware in contract tests'})}};
  function render(changes={}) {
    Object.assign(props,changes);
    let count=0;
    do {dirty=false;cursor=0;output=context.Flow(props);while(effects.length)effects.shift()();assert(++count<20,'hook rendering must settle');} while(dirty);
    return lastStep;
  }
  render();
  return {
    props, render, get step(){return lastStep;},get args(){return lastArgs;},
    check(){attributeCallback(output.card,'onClick',text=>text.includes('class="console-btn"'))();return render();},
    cleanup(){for(const fn of cleanups.values())fn?.();},listeners,
  };
}

test('legacy links normalize to the approved console sections',()=>{
  for(const [raw,expected] of [['/','/overview'],['#/pad','/remote/control'],['/servos','/devices/servos'],['/imu','/devices/imu'],['/voice','/sensing/voice'],['/settings','/settings/network'],['/models','/models/library'],['#/models/bindings?capture=1','/models/bindings'],['/unknown','/overview']])assert.equal(normalizeRoute(raw),expected);
  assert.equal(routeInfo('/devices/imu').section,'imu');
});
test('route mapping retains backend control, voice and calibration route contracts',()=>{
  for(const [raw,expected]of [['/overview','/'],['/remote/control','/pad'],['/remote/recording','/pad'],['/remote/gamepad','/pad'],['/devices/servos','/servos'],['/devices/imu','/imu'],['/devices/diagnostics','/logs'],['/sensing/voice','/voice'],['/sensing/audio','/pad'],['/sensing/camera','/camera'],['/settings/ai','/settings']])assert.equal(serverRoute(raw),expected);
});
test('model pages that consume tune snapshots subscribe through a supported backend route',()=>{
  // studio/app.py sends tune only for these existing routes; no backend expansion is assumed.
  for(const route of ['/models/library','/models/profile'])assert(['/','/pad','/settings'].includes(serverRoute(route)),`${route} needs fresh tune frames`);
});
test('readback failures block self-check while low-battery warnings alone do not',()=>{
  const input=fixture();assert.equal(hardwareProblem(input.st,true),'');
  input.st.battery.level='warn';assert.equal(hardwareProblem(input.st,true),'');
  input.st.servos.online=14;assert.notEqual(hardwareProblem(input.st,true),'');
  input.st.servos.online=15;input.st.imu.status='frozen';assert.notEqual(hardwareProblem(input.st,true),'');
  input.st.imu.status='ok';assert.notEqual(hardwareProblem(input.st,false),'');
});
test('new zero calibration can progress from commit evidence despite an old frozen onboarding row',()=>{
  const input=fixture();row(input,'servo').state='todo';input.st.commit.dirty=true;
  assert.equal(startupStep(input).id,'commit');
  input.st.commit.centered=14;assert.equal(startupStep(input).id,'servos');
});
test('a manual completion marker cannot replace fifteen measured zeroes',()=>{
  const input=fixture();row(input,'servo').marked='done';input.st.commit.centered=0;
  assert.equal(startupStep(input).id,'servos');
});
test('trusted pending IMU calibration bypasses an old unverified/fine row but not final commit',()=>{
  const input=fixture();row(input,'imu').state='todo';row(input,'commit').state='todo';
  input.imu.mount.verified=false;input.st.imu.status='unverified';input.st.commit.pending=true;input.st.commit.dirty=true;
  assert.equal(startupStep(input).id,'commit');
  input.st.commit.items.find(value=>value.key==='imu').default=true;
  assert.equal(startupStep(input).id,'imu-direction','a default-mount bypass is not a new sampled result');
});
test('live IMU direction and fine checks cannot be bypassed by a manual checklist',()=>{
  const input=fixture();row(input,'imu').state='todo';input.imu.mount.verified=false;input.st.imu.status='unverified';
  assert.equal(startupStep(input).id,'imu-direction');
  input.imu.mount.verified=true;input.st.imu.status='ok';assert.equal(startupStep(input).id,'imu-fine');
  row(input,'imu').state='ok';row(input,'imu').marked='done';assert.equal(startupStep(input).id,'imu-fine');
});
test('maintained bus and an in-flight commit retain their own recovery step',()=>{
  const input=fixture();input.st.servo_session=true;input.live=false;assert.equal(startupStep(input).action,'handoff');
  input.st.servo_session=false;input.live=true;input.st.commit.job={phase:'running'};assert.equal(startupStep(input).id,'busy');assert.equal(startupStep(input).disabled,true);
});
test('dirty or failed submission cannot be presented as successful commissioning',()=>{
  const input=fixture();input.st.commit.dirty=true;assert.equal(startupStep(input).id,'commit');
  input.st.commit.dirty=false;row(input,'commit').state='todo';input.st.commit.job={phase:'failed',ok:false,reason:'readback not confirmed'};
  assert.equal(startupStep(input).id,'commit');
});
test('selected model, cached metadata, and actual loaded model are distinct',()=>{
  const input=fixture();input.tune.models.slots[0].loaded=false;input.tune.models.slots[0].selected=true;
  assert.equal(startupStep(input).id,'models');input.tune.models.slots[0].loaded=true;input.tune.models.slots[0].error='load failed';assert.equal(startupStep(input).id,'models');
  delete input.tune.models.slots[0].error;assert.equal(startupStep(input).id,'stop-web');
});
test('web choice does not require a physical gamepad but pad choice requires a genuine estop result',()=>{
  const input=fixture();assert.equal(startupStep(input).id,'stop-web');
  input.stopVerified=true;assert.equal(startupStep(input).id,'walk');
  input.choice='pad';assert.equal(startupStep(input).id,'stop-pad');row(input,'estop').state='ok';row(input,'estop').marked='done';assert.equal(startupStep(input).id,'stop-pad');
  row(input,'estop').marked=null;assert.equal(startupStep(input).id,'walk');
});
test('only the backend walk record completes the guide, never a manual done mark',()=>{
  const input=fixture();input.stopVerified=true;assert.equal(startupStep(input).id,'walk');
  row(input,'walk').state='ok';row(input,'walk').marked='done';assert.equal(startupStep(input).id,'walk');
  row(input,'walk').marked=null;assert.equal(startupStep(input).id,'complete');
});
test('controller restart and calibration edits invalidate the observed-stop token',()=>{
  const input=fixture(), original=startupToken(input.st);
  input.st.imu.generation=2;assert.notEqual(startupToken(input.st),original);
  input.st.imu.generation=1;input.st.commit.dirty=true;assert.notEqual(startupToken(input.st),original);
});
test('web stop observation requires holding then fresh limp with confirmed estop',()=>{
  const harness=flowHarness();harness.check();assert.equal(harness.step.id,'stop-web');
  harness.render();assert.equal(harness.args.stopVerified,false,'initially limp is not a test');
  harness.props.st.posture='holding';harness.props.st.estop.state='ready';harness.render();assert.equal(harness.args.standObserved,true);
  harness.props.st.posture='limp';harness.props.st.estop.state='pending';harness.render();assert.equal(harness.args.stopVerified,false);
  harness.props.st.estop.state='confirmed';harness.render();assert.equal(harness.args.stopVerified,true);assert.equal(harness.step.id,'walk');
  harness.cleanup();assert.equal(harness.listeners.size,0,'commit event listener is released');
});
test('disconnect, stale frames and controller generation changes cannot reuse a stop observation',()=>{
  const harness=flowHarness();harness.check();harness.props.st.posture='holding';harness.props.st.estop.state='ready';harness.render();
  harness.render({live:false});harness.props.st.posture='limp';harness.props.st.estop.state='confirmed';harness.render({live:true});assert.equal(harness.args.stopVerified,false);
  harness.props.st.posture='holding';harness.props.st.estop.state='ready';harness.render();harness.props.st.posture='limp';harness.props.st.estop.state='confirmed';harness.render();assert.equal(harness.args.stopVerified,true);
  harness.props.st.imu.generation++;harness.render();assert.equal(harness.args.stopVerified,false);harness.cleanup();
});
test('an open socket with stale confirmed torque still displays unknown in the footer',()=>{
  const context=vm.createContext({html,Icon:()=>null,PAGES,SEARCH,normalizeRoute,routeInfo,
    useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:()=>({current:null}),useEffect(){}});
  vm.runInContext(moduleSource('console-layout.js')+'\nthis.Layout=ConsoleLayout;',context);
  const st=fixture().st;
  const output=flatten(context.Layout({route:'/overview',st,live:false,open:true}));
  assert.match(output,/动力\s*<b>未知<\/b>/);
});

test('a fresh successful handback overrides a cached open servo-table snapshot',()=>{
  // Outside /servos the server stops emitting servo tables once the session closes.
  const input=fixture();input.sv={state:'open'};input.st.servo_session=false;
  assert.notEqual(startupStep(input).id,'handoff','the current state confirms the bus was returned');
  input.st.servo_session={state:'open'};assert.equal(startupStep(input).id,'handoff','a current held session still requires handback');
});
