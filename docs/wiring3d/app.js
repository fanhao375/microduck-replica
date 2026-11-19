import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';

/* ================= 基础 ================= */
const cv=document.getElementById('cv'), stage=document.getElementById('stage');
const renderer=new THREE.WebGLRenderer({canvas:cv,antialias:true,alpha:true,preserveDrawingBuffer:/[?&]shot/.test(location.search)});
renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
const SHOT=/[?&]shot/.test(location.search); let dirty=true; const redraw=()=>{dirty=true};
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(30,1,2,4000);
camera.up.set(0,0,1);
const ctl=new OrbitControls(camera,cv); ctl.enableDamping=true; ctl.addEventListener('change',()=>dirty=true);
scene.add(new THREE.HemisphereLight(0xffffff,0x8a8f99,1.7));
const dl=new THREE.DirectionalLight(0xffffff,2.2); dl.position.set(200,300,500); scene.add(dl);
const dl2=new THREE.DirectionalLight(0xffffff,.9); dl2.position.set(-300,-200,150); scene.add(dl2);
function resize(){const w=stage.clientWidth,h=stage.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();dirty=true}
new ResizeObserver(resize).observe(stage); resize();

const V=(x,y,z)=>new THREE.Vector3(x,y,z);
const M=(c,o={})=>new THREE.MeshStandardMaterial({color:c,roughness:.6,metalness:.05,...o});
const box=(w,h,d,mat)=>new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat);
const parts={}, wires={}, labels=[];
function addLabel(text,pos,kind,part,key){
  const el=document.createElement('div'); el.className='lb '+(kind==='des'?'des':kind==='sid'?'sid':'');
  el.textContent=text; document.getElementById('labels').appendChild(el);
  const l={el,pos:pos.clone(),kind,part,key}; labels.push(l); return l;
}
function reg(id,obj,label,pos,kind='board'){
  obj.userData.part=id; scene.add(obj); parts[id]={obj,kind};
  obj.traverse(o=>{if(o.isMesh){o.material=Array.isArray(o.material)?o.material.map(m=>m.clone()):o.material.clone();o.userData.part=id}});
  if(label) addLabel(label,pos,'name',id);
}

/* ================= 鸭子骨架（仿真模型 → 站姿） ================= */
const MM=new THREE.Matrix4().makeScale(1000,1000,1000);
const mat4=(pos,q)=>new THREE.Matrix4().compose(V(...pos),new THREE.Quaternion(q[1],q[2],q[3],q[0]),V(1,1,1));
const STAND={left_hip_yaw:0,left_hip_roll:-0.0873,left_hip_pitch:-0.4579,left_knee:-0.0049,left_ankle:0.453,neck_pitch:0.3491,head_pitch:0.3491,head_yaw:0,head_roll:0,right_hip_yaw:0,right_hip_roll:0.0873,right_hip_pitch:0.4579,right_knee:0.0049,right_ankle:-0.453};
const [duckGLB,duck]=await Promise.all([
  new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync('model/duck-mech.glb'),
  fetch('model/duck.json').then(r=>r.json()),
]);
const hdGLB=await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).loadAsync('model/hd1910.glb');
hdGLB.scene.traverse(o=>{if(o.isMesh){const ms=Array.isArray(o.material)?o.material:[o.material]; ms.forEach(m=>{if(m.color&&m.color.r+m.color.g+m.color.b<.06)m.color.set(0x2b2d31)})}});
// 仿真模型里舵机是 XL330 的方块：把 HD-1910 真模型对到同一个位置（输出轴对输出轴，长边对长边）
const A_HD=new THREE.Matrix4().set(-1,0,0,0, 0,0,1,0.0077, 0,1,0,0.0069, 0,0,0,1);
const world={};
for(const b of duck.bodies){
  const P=b.parent?world[b.parent]:new THREE.Matrix4();
  const m=P.clone().multiply(mat4(b.pos,b.quat));
  if(b.joint){m.multiply(new THREE.Matrix4().makeRotationAxis(V(...b.joint.axis),STAND[b.joint.name]??0))}
  world[b.name]=m;
}
const geoms={};
duckGLB.scene.traverse(o=>{if(o.isMesh)geoms[o.name]=o.geometry});
duckGLB.scene.traverse(o=>{if(o.isMesh&&o.parent&&!geoms[o.parent.name])geoms[o.parent.name]=o.geometry});
const geomW=(g)=>MM.clone().multiply(world[g.body]).multiply(mat4(g.pos,g.quat));   // 米 → 世界毫米
const SHELL=new Set(['left_shell','right_shell','top_head_shell','bottom_head_shell','face_part','jaw','jaw_soft','soft_mouth_top','motor_support','power_support']);
const HIDE=new Set(['elec_rpi_robot_hat_pcb','pcb__raspberry_pi_zero_2_w']);   // 占位板，用真模型替换
const CAM=new Set(['lens','m12_lens_holder','noenoeil']);

// 舵机编号：按所在连杆对应的关节（髋偏航/髋横滚/髋俯仰/膝/踝 = x0…x4，头 30…34）
function servoId(g){
  const y=g.pos[1];
  switch(g.body){
    case 'trunk_base': return y>0?20:10;
    case 'yaw2roll': return 21;
    case 'bearing_roll': return 11;
    case 'upper_leg_left': return Math.abs(y)>0.02?23:22;
    case 'upper_leg_right': return Math.abs(y)>0.02?13:12;
    case 'leg': return 24;
    case 'leg_2': return 14;
    case 'neck': return y<-0.03?31:30;
    case 'yaw_roll_motion': return 32;
    case 'jaw_soft': return Math.abs(y)>0.01?34:33;
  }
  return null;
}
const meshOf=(g,mat)=>{const geo=geoms[g.mesh]; if(!geo.getAttribute('normal'))geo.computeVertexNormals();
  const m=new THREE.Mesh(geo,mat); m.matrixAutoUpdate=false; m.matrix.copy(geomW(g)); m.userData.mname=g.mesh; return m};
const shellMeshes=[], mechMeshes=[], servoPos={}, servoGeom={}, extra={cam:[],bat:null};
for(const g of duck.geoms){
  if(HIDE.has(g.mesh)) continue;
  if(g.mesh==='xl330'){
    const id=servoId(g); const Wd=geomW(g).multiply(A_HD);
    const grp=new THREE.Group(); const hd=hdGLB.scene.clone(true); hd.matrixAutoUpdate=false; hd.matrix.copy(Wd); grp.add(hd);
    servoPos[id]=V(0,0,0).applyMatrix4(geomW(g)); servoGeom[id]={Wd};
    reg('s'+id,grp,null,null,'servo');
    addLabel(String(id),servoPos[id].clone().add(V(0,0,0)),'sid','s'+id);
    continue;
  }
  if(CAM.has(g.mesh)){extra.cam.push(meshOf(g,M(g.mesh==='lens'||g.mesh==='noenoeil'?0x111114:0x3b3f46)));continue}
  if(g.mesh==='speaker'){extra.spk=meshOf(g,M(0x33363d));continue}
  if(g.mesh==='np_f970'){extra.bat=meshOf(g,M(0x3a3f4a));continue}
  if(SHELL.has(g.mesh)){const m=meshOf(g,M(0xd9d6cf,{transparent:true,opacity:.1,depthWrite:false,side:THREE.DoubleSide}));scene.add(m);shellMeshes.push(m);continue}
  const m=meshOf(g,M(0xb9bcc2,{transparent:true,opacity:.35,depthWrite:false}));scene.add(m);mechMeshes.push(m);
}
const bb=new THREE.Box3(); for(const m of [...shellMeshes,...mechMeshes]){m.geometry.computeBoundingBox();const b=m.geometry.boundingBox.clone().applyMatrix4(m.matrix);bb.union(b)}
const CENTER=bb.getCenter(new THREE.Vector3());
{
  const grid=new THREE.GridHelper(500,25,0x8e8e93,0xc7c7cc); grid.rotation.x=Math.PI/2; grid.position.set(CENTER.x,CENTER.y,bb.min.z-1);
  grid.material.transparent=true; grid.material.opacity=.35; scene.add(grid);
}
// 电池
{const g=new THREE.Group(); g.add(extra.bat); reg('bat',g,null);
 g.updateMatrixWorld(true); const c=new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3()); extra.batC=c; addLabel('电池',c.clone().add(V(0,0,45)),'name','bat')}
// 扬声器（3525，装在 33 号上方）
const spkP=V(0,0,0);
{const g=new THREE.Group(); g.add(extra.spk); reg('spk',g,null); g.updateMatrixWorld(true);
 spkP.copy(new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3())); addLabel('扬声器',spkP.clone().add(V(0,0,14)),'name','spk')}
// 摄像头
const lensP=V(0,0,0);
{const g=new THREE.Group(); extra.cam.forEach(m=>g.add(m)); reg('cam',g,null);
 g.updateMatrixWorld(true); const c=new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3()); lensP.copy(c); addLabel('摄像头',c.clone().add(V(0,0,10)),'name','cam')}

/* ================= 板子 ================= */
const frameOf=(body)=>{const m=MM.clone().multiply(world[body]);const p=V(0,0,0),q=new THREE.Quaternion(),s=V(1,1,1);m.decompose(p,q,s);return new THREE.Matrix4().compose(p,q,V(1,1,1))};
const TR=frameOf('trunk_base');
const trunkPt=(x,y,z)=>V(x,y,z).applyMatrix4(TR);
// ---- 社区开源的两块板：用它们的 Gerber 生产文件贴出真实板面，外形和接口位置取自 KiCad 工程 ----
const texLoader=new THREE.TextureLoader();
const tex=async(f)=>{const t=await texLoader.loadAsync(f); t.colorSpace=THREE.SRGBColorSpace; t.anisotropy=8; return t};
const [hubTop_,hubBot_,banTop_,banBot_]=await Promise.all(['hub-top','hub-bottom','banana-top','banana-bottom'].map(n=>tex('model/'+n+'.png')));
function pcbBoard(w,h,t,top,bot){
  const g=new THREE.Group();
  g.add(box(w,h,t,M(0x1f6b3b)));
  const pt=new THREE.Mesh(new THREE.PlaneGeometry(w,h),M(0xffffff,{map:top,roughness:.5})); pt.position.z=t/2+.02; g.add(pt);
  const pb=new THREE.Mesh(new THREE.PlaneGeometry(w,h),M(0xffffff,{map:bot,roughness:.5})); pb.rotation.y=Math.PI; pb.position.z=-t/2-.02; g.add(pb);
  return g;
}
const rotM=(m)=>new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().set(m[0][0],m[0][1],m[0][2],0, m[1][0],m[1][1],m[1][2],0, m[2][0],m[2][1],m[2][2],0, 0,0,0,1));
// 香蕉板（banana_pcb：39.2×6.8 mm，两端是 2.5 mm 香蕉头铜柱，元件面的小焊盘出电源线）。横放在电池仓顶部，铜柱朝下插电池
const banana=new THREE.Group();
{
  const b=pcbBoard(39.24,6.84,1.6,banTop_,banBot_); banana.add(b);
  for(const sx of [-1,1]){const p=new THREE.Mesh(new THREE.CylinderGeometry(1.6,1.6,11.5,18),M(0xb87333,{metalness:.7,roughness:.3})); p.rotation.x=Math.PI/2; p.position.set(sx*16.9,0,-.8-5.75); banana.add(p)}   // 铜柱（2.5 mm 香蕉头）朝下，插进电池触点孔
  banana.quaternion.copy(rotM([[0,-1,0],[1,0,0],[0,0,1]]));   // 长边沿左右（y），元件面朝上，铜柱朝下
  banana.position.copy(trunkPt(-30.4,0,35.0));   // 官方锁扣：x −32.3…−28.5，z 31.5…38.1，长 54 mm 沿 y
  reg('banana',banana,null);
}
banana.updateMatrixWorld(true);
// HUB（dxl_hub：22.5×28.4 mm）。平放在 10、20 号舵机上方；元件面朝上（脖子口、C1、电源焊盘），三个腿/IMU 口焊在背面朝下
const hub=new THREE.Group();
{
  const b=pcbBoard(22.51,28.42,1.6,hubTop_,hubBot_); hub.add(b);
  const conn=(x,y,front)=>{const c=box(4.6,8,6,M(0xf2efe6)); c.position.set(x,y,front?.8+3:-.8-3); hub.add(c);
    for(let k=-1;k<=1;k++){const pin=box(.9,.9,1,M(0xd7b25a)); pin.position.set(x,y+k*2,front?.8+6.4:-.8-6.4); hub.add(pin)}};
  conn(4.2,9.4,false);
  conn(-4.0,9.4,false);
  conn(4.5,-1.1,false);
  conn(-3.0,-1.5,true);
  const cap=new THREE.Mesh(new THREE.CylinderGeometry(4,4,12,24),M(0x2a2a2e)); cap.rotation.x=Math.PI/2; cap.position.set(-2.2,-10.7,.8+6); hub.add(cap);
  const sb=new THREE.Box3(); for(const k of [10,20]){const g=parts['s'+k].obj; g.updateMatrixWorld(true); sb.expandByObject(g)}
  hub.quaternion.copy(rotM([[0,1,0],[-1,0,0],[0,0,1]]));
  hub.position.set((servoPos[10].x+servoPos[20].x)/2,0,sb.max.z+7.2);
  reg('hub',hub,null);
}
hub.updateMatrixWorld(true);
const hubPort=(i)=>hub.localToWorld(i===0?V(4.2,9.4,-7.4):i===1?V(4.5,-1.1,-7.4):V(-4.0,9.4,-7.4));
const hubTop=()=>hub.localToWorld(V(-3.0,-1.5,7.4));
const hubPad=(k)=>hub.localToWorld(V(3.9,k?-13.57:-10.07,1.2));
const bananaPad=(k)=>banana.localToWorld(V(-5.9,k?-1.3:1.3,1.2));
addLabel('集线板 HUB',hub.localToWorld(V(0,0,16)),'name','hub');
addLabel('香蕉板',banana.localToWorld(V(0,0,-8)),'name','banana');

// ToF：VL53L5CX 小模块，装在摄像头下方，窗口朝前
const tof=new THREE.Group();
{
  const pcb=box(1.4,22,10.5,M(0x23467a)); tof.add(pcb);
  const win=box(3,7.5,4.5,M(0x15151a,{metalness:.4})); win.position.set(2.2,0,.4); tof.add(win);
  const pin=box(2.2,14,2.5,M(0xd7b25a)); pin.position.set(-1.6,0,-3.9); tof.add(pin);
  tof.position.set(61.6,22.4,247.6);   // 脸部面板上摄像头左边的 ToF 窗口（从 face_part 模型量出：窗口中心 y=22.4、z≈246–248，24.8×10.7 的凹槽）
  reg('tof',tof,'ToF 模块',tof.position.clone().add(V(0,0,12)),'module');
}
tof.updateMatrixWorld(true);

/* GLB：HAT / 主控 / IMU v2 ———— HAT + 主控叠成一摞，竖着装在头里，位置取仿真模型里的占位板 */
const gl=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder); const load=(f)=>gl.loadAsync(f).then(g=>g.scene);
const ph=duck.geoms.find(g=>g.mesh==='elec_rpi_robot_hat_pcb');
const STACK_S=1;     // 叠层方向：+1 = HAT 的元件面朝鸭子后方
const Wph=geomW(ph);
const stackC=V(0.029,-0.0115,-0.004);         // 叠层中心（HAT 坐标，米）
const Rm=new THREE.Matrix4().set(0,0,STACK_S,0, 0,1,0,0, -STACK_S,0,0,0, 0,0,0,1);
const B=Wph.clone().multiply(Rm).multiply(new THREE.Matrix4().makeTranslation(-stackC.x,-stackC.y,-stackC.z));
const hatToWorld=(x,y,z)=>V(x/1000,y/1000,z/1000).applyMatrix4(B);
const HAT_Z=0.0045*1000;
// HAT 各接口：直接取 hat.glb 里对应器件的中心和顶面（毫米，HAT 模型坐标），不再用 PCB 坐标换算
const HAT={J1:[36.8,-22.7,9.1],J2:[46.8,-22.7,9.1],J3:[13.7,-19.2,6.7],J5:[0.8,-6.8,5.0],J6:[8.8,-6.8,5.0],J7:[27.2,-8.1,5.0],J8:[35.3,-8.1,5.0],J9:[52.7,-22.7,9.1],J11:[8.9,-19.2,6.7],J13:[-0.7,-14.9,6.7],J14:[4.1,-14.9,6.7]};
const hp=(k,dz=0)=>hatToWorld(HAT[k][0],HAT[k][1],HAT[k][2]+dz);
const jobs=[
  load('model/hat.glb').then(o=>{
    o.traverse(n=>{if(/raspberry/i.test(n.name))n.visible=false});
    o.matrixAutoUpdate=false; o.matrix.copy(B); reg('hat',o,null);
  }),
  load('model/radxa.glb').then(o=>{
    o.matrixAutoUpdate=false; o.matrix.copy(B.clone().multiply(new THREE.Matrix4().makeTranslation(-0.0035,-0.0265,-0.0086))); reg('radxa',o,null);
  }),
  load('model/imu2.glb').then(o=>{
    o.matrixAutoUpdate=false; o.matrix.copy(new THREE.Matrix4().set(0,0,1,0, -1,0,0,0, 0,-1,0,0, 0,0,0,1).multiply(new THREE.Matrix4().makeScale(1000,1000,1000)));   // 元件面朝前（朝舵机），白色端子 J1 一侧朝上
    reg('imu',o,null);
  }),
];
await Promise.all(jobs); scene.updateMatrixWorld(true);
// IMU 小板：位置取官方 MJCF 的 imu 站点（躯干坐标 −21.0, 0.07, −14.7 mm，在电池仓背板上）；元件面朝前，白色端子朝上
{
  const o=parts.imu.obj; scene.updateMatrixWorld(true);
  const bay=shellMeshes.find(m=>m.userData.mname==='power_support'); const bb=new THREE.Box3().setFromObject(bay);
  const ib=new THREE.Box3().setFromObject(o), c=ib.getCenter(new THREE.Vector3());
  const P0=trunkPt(-21.0,0.07,-14.7), tz=P0.z, dx=P0.x-(ib.min.x+2.4);   // 官方 MJCF 里 imu 站点：躯干坐标 (−21.0, 0.07, −14.7) mm；板厚中心落在这里
  o.matrix.premultiply(new THREE.Matrix4().makeTranslation(dx,P0.y-c.y,tz-c.z));
  scene.updateMatrixWorld(true);
  parts.imu.c=new THREE.Box3().setFromObject(o).getCenter(new THREE.Vector3());
  parts.imu.top=V(.0096,.005,.004).applyMatrix4(o.matrix);
  parts.imu.bay=bb.max.x;
}
const stackC3=new THREE.Box3().setFromObject(parts.hat.obj).getCenter(new THREE.Vector3());
addLabel('HAT',stackC3.clone().add(V(0,0,28)),'name','hat');
addLabel('主控 ZERO 3W',stackC3.clone().add(V(0,0,-30)),'name','radxa');
addLabel('IMU 小板（自研）',parts.imu.top.clone().add(V(-4,0,6)),'name','imu');
for(const k of Object.keys(HAT)) addLabel(k,hp(k,6),'des','hat',k);
const csiP=hatToWorld(61.5,-14,-8);
addLabel('CSI 口',csiP,'des','radxa','CSI');

/* ================= 线 ================= */
const RED=0xd93025,BLK=0x202124,ORG=0xf29900,GRN=0x1e8e3e,YEL=0xf6c026,WHT=0xe8eaed;
function wire(id,pts,color,r=1){
  const curve=new THREE.CatmullRomCurve3(pts,false,'catmullrom',.4);
  const m=new THREE.Mesh(new THREE.TubeGeometry(curve,Math.max(24,pts.length*14),r,8,false),M(color,{roughness:.45,depthTest:false}));
  m.renderOrder=20; m.userData.wire=id; scene.add(m); wires[id]=m; return m;
}
const mid=(a,b,off=V(0,0,0))=>a.clone().add(b).multiplyScalar(.5).add(off);
const sp=(id)=>servoPos[id];
const side=(id)=>V(0,id>=20&&id<30?8:-8,0);
// 电池 → 香蕉板（触点直接压上，画短线示意）
// 香蕉板 → HUB 电源焊盘
wire('ban_hub_r',[bananaPad(0),mid(bananaPad(0),hubPad(1),V(-2,-4,0)),hubPad(1)],RED,1.2);
wire('ban_hub_k',[bananaPad(1),mid(bananaPad(1),hubPad(0),V(-2,4,0)),hubPad(0)],BLK,1.2);
// ---- 舵机接口：每颗两个口，一进一出 ----
const portsOf={}, rawPorts={};
function makePorts(id,prevP,nextP){
  const {Wd}=servoGeom[id]; const grp=parts['s'+id].obj;
  const n=V(0,0,-1).transformDirection(Wd), ey=V(1,0,0).transformDirection(Wd);
  const mk=(hx)=>{
    const mouth=V(hx,-0.009,-0.0205).applyMatrix4(Wd);
    const blk=new THREE.Mesh(new THREE.BoxGeometry(4.2,7,3),M(0xf2efe6)); blk.matrixAutoUpdate=false;
    blk.matrix.copy(Wd.clone().multiply(new THREE.Matrix4().makeTranslation(hx,-0.009,-0.0195)).multiply(new THREE.Matrix4().makeScale(.001,.001,.001))); grp.add(blk);
    return {p:mouth.clone().addScaledVector(n,2),n:n.clone(),ey:ey.clone()};
  };
  const a=mk(-0.0066), b=mk(0.0066);
  rawPorts[id]=[a,b];
}
const CH={R:[10,11,12,13,14],L:[20,21,22,23,24],H:[30,31,34,32,33]};
const hubEnd={R:hubPort(0),L:hubPort(2),H:hubTop()};
for(const [k,list] of Object.entries(CH)) list.forEach((id,i)=>makePorts(id,i?servoPos[list[i-1]]:hubEnd[k],i<list.length-1?servoPos[list[i+1]]:(k==='H'?hp('J14'):null)));
// 沿链逐颗分配：每颗舵机里离上一根线最近的那个口作进口，另一个口出线
for(const [k,list] of Object.entries(CH)){
  let from=hubEnd[k];
  list.forEach((id,i)=>{
    const [a,b]=rawPorts[id];
    const nxt=i<list.length-1?servoPos[list[i+1]]:(k==='H'?hp('J14'):null);
    // 两种接法里，上一根线→进口 + 出口→下一颗 总长最短的那一种（HUB 侧的口接 HUB，靠下一颗的口接下一颗）
    const c1=a.p.distanceTo(from)+(nxt?b.p.distanceTo(nxt):0), c2=b.p.distanceTo(from)+(nxt?a.p.distanceTo(nxt):0);
    const flip=(c1<=c2)!==(id===10||id===20);   // 10、20 号：按你的要求，HUB 和下一颗用的两个口对调
    portsOf[id]=flip?{in:a,out:b}:{in:b,out:a}; from=portsOf[id].out.p;
  });
}
// 三芯线：地（黑）/ 电源（红）/ 信号（橙）三根并行，沿路径出口方向走
function cable(id,pts,offs,r=.55,cols=[BLK,RED,ORG]){
  const grp=new THREE.Group(); grp.userData.wire=id;
  cols.forEach((c,i)=>{
    const o=i-(cols.length-1)/2, P=pts.map((p,j)=>p.clone().addScaledVector(offs[j],o*1.5));
    const curve=new THREE.CatmullRomCurve3(P,false,'catmullrom',.5);
    const m=new THREE.Mesh(new THREE.TubeGeometry(curve,48,r,6,false),M(c,{roughness:.5}));
    m.userData.wire=id; grp.add(m);
  });
  scene.add(grp); wires[id]=grp; return grp;
}
const Z3=V(0,1,0);
function link(id,outP,inP){   // outP/inP：{p,n,ey}
  const a=outP.p,b=inP.p, L=a.distanceTo(b), lift=Math.min(9,2+L*.12);
  cable(id,[a,a.clone().addScaledVector(outP.n,lift),b.clone().addScaledVector(inP.n,lift),b],[outP.ey,outP.ey,inP.ey,inP.ey]);
}
const hubLead=(pt,up=false)=>({p:pt,n:V(0,0,up?1:-1),ey:V(0,1,0)});
for(const [k,list] of Object.entries(CH)){
  const hubWire={R:'hub_10',L:'hub_20',H:'hub_30'}[k];
  link(hubWire,hubLead(hubEnd[k],k==='H'),portsOf[list[0]].in);
  for(let i=0;i<list.length-1;i++) link('c'+list[i]+'_'+list[i+1],portsOf[list[i]].out,portsOf[list[i+1]].in);
}
{const a=hubPort(1), j=parts.imu.top.clone().add(V(0,0,2));
 cable('hub_imu',[j,j.clone().add(V(0,0,8)),V(j.x,j.y,(j.z+a.z)/2),V(a.x,a.y,a.z-8),a],[V(0,1,0),V(0,1,0),V(0,1,0),V(0,1,0),V(0,1,0)]);}
{ // 线头的 3P 插头，扣在 HAT 的 J14 上（插头朝下插入）
  const plug=new THREE.Mesh(new THREE.BoxGeometry(3.9,10.2,5.5),M(0xf7f5ef,{roughness:.5}));
  plug.matrixAutoUpdate=false; plug.matrix.copy(new THREE.Matrix4().makeTranslation(HAT.J14[0]/1000,HAT.J14[1]/1000,(HAT.J14[2]+2.4)/1000).multiply(new THREE.Matrix4().makeScale(.001,.001,.001)));
  plug.userData.part='hat'; parts.hat.obj.add(plug);
}
{ // 扬声器 → HAT J1（绿色螺丝端子），两根线
  const a=spkP.clone().add(V(0,0,4)), b=hp('J1',3);
  cable('spk_hat',[a,a.clone().add(V(-6,0,10)),mid(a,b,V(-6,0,12)),b.clone().add(V(0,0,6)),b],[V(0,1,0),V(0,1,0),V(0,1,0),V(0,1,0),V(0,1,0)],.7,[BLK,RED]);
}
link('s33_hat',portsOf[33].out,{p:hp('J14',5),n:hatToWorld(0,0,1).sub(hatToWorld(0,0,0)).normalize(),ey:V(0,1,0)});
// ToF → J5
[[GRN,-3],[YEL,-1],[RED,1],[BLK,3]].forEach(([c,d],i)=>{
  const a=tof.localToWorld(V(-1.5,d*1.5,-4.4)); const b=hp('J5',.2);
  wire('tof'+i,[a,mid(a,b,V(-8,d*1.2,0)),b],c,.6);
});
// 摄像头排线 → 主控 CSI
{const a=lensP.clone().add(V(-6,0,0)); const w=wire('cam',[a,mid(a,csiP,V(-4,0,0)),csiP],WHT,2.4);}

/* ================= 步骤 ================= */
const ctr=(...ps)=>ps.reduce((s,p)=>s.add(p),V(0,0,0)).multiplyScalar(1/ps.length);
const STEPS=[
 {t:'电池 → 香蕉板',s:'电池装在躯干后面，触点压到香蕉板',parts:['bat','banana'],wires:[],focus:()=>[ctr(extra.batC,banana.position),200],
  html:'<ul><li>香蕉板横放在电池仓顶部，板上两根铜柱（2.5 mm 香蕉头）朝下，直接插进电池的触点孔，不用线。</li><li>这一段只走电，正常工作电流 3～5 A，<b>线别用太细的</b>。</li></ul>'},
 {t:'香蕉板 → HUB 电源焊盘',s:'红黑两根线，焊到 HUB 的 J3',parts:['banana','hub'],wires:['ban_hub_r','ban_hub_k'],focus:()=>[ctr(banana.position,hub.position),170],
  html:'<ul><li>香蕉板中间的焊盘（J3）出红黑线，焊到集线板 HUB 上的 J3 电源焊盘。</li><li>HUB 上 J3：1 = +BATT，2 = GND。红线接 1，黑线接 2。</li><li>HUB 上所有舵机口都是 <b>GND / +BATT / DATA 并联</b>，插哪个口电气上都通。</li></ul>'},
 {t:'HUB 背面三口：两条腿 + IMU',s:'10→14 右腿、20→24 左腿、IMU 小板',
  parts:['hub','imu',...[10,11,12,13,14,20,21,22,23,24].map(i=>'s'+i)],
  wires:['hub_10','hub_20','hub_imu','c10_11','c11_12','c12_13','c13_14','c20_21','c21_22','c22_23','c23_24'],focus:()=>[trunkPt(-10,0,-5),210],
  html:'<ul><li>右腿：HUB → <b>10</b> 号（髋偏航）→ 11 → 12 → 13 → <b>14</b> 号（踝），一路串下去。</li><li>左腿：HUB → <b>20</b> → 21 → 22 → 23 → <b>24</b>，同理。</li><li>IMU 小板（总线 ID 200）固定在电池仓背板上（位置取官方模型里 IMU 的参考位置），元件面朝前，<b>白色端子朝上</b>，线接 HUB 的中间一个口，和舵机走同一根总线。</li><li>三个口完全等价，左右顺序按线够得着来。</li></ul>'},
 {t:'HUB 正面一口：头部串',s:'30 → 31 → 34 → 32 → 33',parts:['hub','s30','s31','s34','s32','s33'],wires:['hub_30','c30_31','c31_34','c34_32','c32_33'],des:['J13','J14'],focus:()=>[ctr(hub.position,sp(33)),200],
  html:'<ul><li>HUB 正面的口接 <b>30</b> 号（颈俯仰），再依次串 <b>31（头俯仰）→ 34（嘴）→ 32（头偏航）→ 33（头横滚）</b>。</li><li>每个舵机有两个口，一进一出，线从上一个的空口接到下一个。</li></ul>'},
 {t:'头部串末端 → HAT',s:'33 号的线插 J14（最右边的 3P 白端子）',parts:['s33','hat'],wires:['s33_hat'],des:['J13','J14'],focus:()=>[ctr(sp(33),hp('J14')),75],
  html:'<ul><li>33 号舵机的线插 HAT 的 <b>J14</b>（板子最右边那个 3P 白端子；<b>J13</b> 与它并联，是另一个同样的口）。</li><li>3 针：1 = GND，2 = +BATT，3 = DATA。</li><li><b>HAT 没有单独的电池口</b>：它的电就是从这根线顺着头部串送上来的，再由 HAT 降到 5 V 给主控。</li></ul>'},
 {t:'HAT 与主控对插',s:'HAT 背面的 40 针排母插在主控排针上',parts:['hat','radxa'],wires:[],focus:()=>[stackC3,120],
  html:'<ul><li>HAT 背面（J4）的 40 针排母，直接插在 Radxa ZERO 3W 的 40 针排针上，不用线。</li><li>摄像头、ToF 的接法见后两步。</li></ul>'},
 {t:'ToF → HAT J5',s:'VL53L5CX 模块，4 根线',parts:['tof','hat'],wires:['tof0','tof1','tof2','tof3'],des:['J5'],focus:()=>[ctr(tof.position,hp('J5')),110],
  html:'<ul><li>ToF 是 ST VL53L5CX（8×8 测距）模块，用的是散线，接 HAT 的 <b>J5</b>（4 针 JST-SH，I²C）。</li><li>J5 的脚序：<table class="pin"><tr><th>1</th><th>2</th><th>3</th><th>4</th></tr><tr><td>GND</td><td>3V3</td><td>SDA</td><td>SCL</td></tr></table></li><li>模块自己的丝印通常是 VCC 在前、GND 在后，<b>和 J5 的顺序不一样</b>，别按排列直接对插，逐根对着丝印接：VCC→3V3、GND→GND、SDA→SDA、SCL→SCL。</li><li>只供 3.3 V，不要接 5 V。</li></ul>',
  warn:'线的颜色是示意；模块如果还引出 LPn、INT 等脚，本机不接。ToF 在头上的具体安装位置是示意。'},
 {t:'摄像头 → 主控 CSI',s:'排线直接进主控，不经过 HAT',parts:['cam','radxa'],wires:['cam'],des:['CSI'],focus:()=>[ctr(lensP,csiP),110],
  html:'<ul><li>摄像头用排线接主控自己的 <b>CSI</b> 口，<b>不走 HAT</b>。</li><li>排线触点朝向要对（金手指面对着接口里的触点），插好后压下锁扣。</li></ul>',
  warn:'摄像头型号和 CSI 口在主控上的具体位置，这里是示意，还没按实物核对。'},
 {t:'扬声器 → HAT J1',s:'扬声器在 33 号上方，两根线接 J1 绿色端子',parts:['spk','hat'],wires:['spk_hat'],des:['J1'],focus:()=>[ctr(spkP,hp('J1')),90],
  html:'<ul><li>扬声器装在 33 号舵机上方，两根线接 HAT 的 <b>J1</b>（绿色螺丝端子，5 W）。</li><li>喇叭两根线没有极性要求，按丝印接 + / −。</li></ul>'},
 {t:'HAT 上其余的口',s:'喇叭、麦克风、预留口',parts:['hat'],wires:[],des:['J1','J2','J9','J11','J7','J8','J6'],focus:()=>[stackC3,110],
  html:'<ul><li><b>J1</b>：喇叭（上一步已接）。</li><li><b>J2 / J9</b>：外接麦克风；<b>MK1</b>：板载麦克风。</li><li><b>J3 / J11</b>：4 针 RS-485（标 3 A max），这只鸭子不用。</li><li><b>J7 / J8</b>：预留扩展口，只供 3.3 V。</li><li><b>J6</b>：预留（打算接风扇），只供 3.3 V，没上板验证过。</li><li><b>U12</b>：板载 IMU（本机用总线上的自研 IMU 小板）。</li></ul>'}
];

/* ================= 交互 ================= */
let active=-1, tween=null;
const DIR=V(.62,.72,.42).normalize();
function flyTo(p,d,dir=DIR){tween={t:0,p0:camera.position.clone(),c0:ctl.target.clone(),p1:p.clone().addScaledVector(dir,d),c1:p.clone()}}
const stepsEl=document.getElementById('steps'), infoEl=document.getElementById('info');
STEPS.forEach((st,i)=>{const d=document.createElement('div');d.className='step';d.innerHTML=`<span class="n">${i+1}</span><div><b>${st.t}</b><small>${st.s}</small></div>`;d.onclick=()=>select(i===active?-1:i);stepsEl.appendChild(d)});
function select(i,fly=true){
  active=i;
  [...stepsEl.children].forEach((c,k)=>c.classList.toggle('on',k===i));
  if(i<0){infoEl.innerHTML='<h3>怎么看</h3><ul><li>从左边选一步，对应的零件和线会亮起，其余变淡，镜头自动推过去。</li><li>直接点 3D 里的零件或线也行；再点空白处回到全貌。</li><li>橙色 = 舵机总线（GND / +BATT / DATA 三芯），红黑 = 电池。</li><li>外壳是半透明的，线始终画在最前面，方便看走向。</li></ul>'}
  else{const st=STEPS[i];infoEl.innerHTML=`<h3>${i+1}. ${st.t}</h3>${st.html}${st.warn?`<div class="warn">${st.warn}</div>`:''}`}
  applyFade();
  if(fly){ if(i>=0){const [p,d]=STEPS[i].focus();flyTo(p,d)} else flyTo(CENTER,720) }
}
function setOpacity(obj,o){
  obj.traverse(m=>{if(!m.isMesh)return;(Array.isArray(m.material)?m.material:[m.material]).forEach(mt=>{
    if(mt.userData.o0===undefined){mt.userData.o0=mt.opacity;mt.userData.t0=mt.transparent;mt.userData.w0=mt.depthWrite}
    mt.transparent=o<1?true:mt.userData.t0; mt.opacity=mt.userData.o0*o; mt.depthWrite=o<1?false:mt.userData.w0; mt.needsUpdate=true})});
}
const T={shell:true,mech:true,wires:true,ids:true,des:true,name:true,all:false};
function applyFade(){
  dirty=true;
  const st=STEPS[active]; const showAll=T.all||!st;
  for(const [id,p] of Object.entries(parts)){const on=showAll||st.parts.includes(id); setOpacity(p.obj,on?1:.13); p.on=on}
  for(const [id,w] of Object.entries(wires)){const on=showAll||st.wires.includes(id); w.visible=T.wires; setOpacity(w,on?1:.1)}
  shellMeshes.forEach(m=>m.visible=T.shell); mechMeshes.forEach(m=>m.visible=T.mech);
  for(const l of labels){const pOn=!parts[l.part]||parts[l.part].on; l.el.classList.toggle('dim',!pOn)}
}
for(const [id,k] of [['tShell','shell'],['tMech','mech'],['tWires','wires'],['tIds','ids'],['tDes','des'],['tName','name'],['tAll','all']])
  document.getElementById(id).onchange=e=>{T[k]=e.target.checked;applyFade()};
const ray=new THREE.Raycaster(),mouse=new THREE.Vector2(); let down=null;
cv.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);
cv.addEventListener('pointerup',e=>{
  if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>4)return;
  const r=cv.getBoundingClientRect(); mouse.set((e.clientX-r.left)/r.width*2-1,-((e.clientY-r.top)/r.height)*2+1);
  ray.setFromCamera(mouse,camera);
  const hit=ray.intersectObjects([...Object.values(parts).filter(p=>p.obj.visible).map(p=>p.obj),...Object.values(wires)],true).find(h=>h.object.visible!==false&&h.object.userData&&true);
  if(!hit){select(-1);return}
  let o=hit.object,part=null,wr=null; while(o){if(o.userData.part)part=o.userData.part;if(o.userData.wire)wr=o.userData.wire;o=o.parent}
  const idx=wr?STEPS.findIndex(s=>s.wires.includes(wr)):STEPS.findIndex(s=>part&&s.parts.includes(part));
  if(idx>=0)select(idx);
});

/* ================= 渲染 ================= */
const tmp=V(0,0,0);
function frame(){
  if(tween){tween.t=Math.min(1,tween.t+.04);const k=tween.t<.5?2*tween.t*tween.t:1-Math.pow(-2*tween.t+2,2)/2;camera.position.lerpVectors(tween.p0,tween.p1,k);ctl.target.lerpVectors(tween.c0,tween.c1,k);if(tween.t>=1)tween=null}
  ctl.update();
  if(!dirty&&!tween&&!SHOT){requestAnimationFrame(frame);return}
  dirty=false; renderer.render(scene,camera);
  const w=stage.clientWidth,h=stage.clientHeight, st0=STEPS[active];
  for(const l of labels){
    const desOn=T.des&&(T.all||(st0&&st0.des&&st0.des.includes(l.key)));
    const show=(l.kind==='des'&&desOn)||(l.kind==='sid'&&T.ids)||(l.kind==='name'&&T.name);
    if(!show){l.el.style.display='none';continue}
    tmp.copy(l.pos).project(camera);
    if(tmp.z>1){l.el.style.display='none';continue}
    l.el.style.display=''; l.el.style.left=((tmp.x*.5+.5)*w)+'px'; l.el.style.top=((-tmp.y*.5+.5)*h)+'px';
  }
  requestAnimationFrame(frame);
}
camera.position.copy(CENTER).addScaledVector(DIR,720); ctl.target.copy(CENTER);
select(-1,false);
document.getElementById('load').style.opacity=0; setTimeout(()=>document.getElementById('load')?.remove(),500);
window.__ready=true; window.__select=select; window.__fly=(x,y,z,d,dx=.62,dy=.72,dz=.42)=>flyTo(V(x,y,z),d,V(dx,dy,dz).normalize()); window.__srv=servoPos; window.__parts=parts; window.__cam=(px,py,pz,tx,ty,tz)=>{tween=null;camera.position.set(px,py,pz);ctl.target.set(tx,ty,tz)}; window.__only=(names)=>{scene.traverse(o=>{if(o.isMesh&&o.userData.mname!==undefined)o.visible=names.includes(o.userData.mname)}); Object.values(parts).forEach(p=>p.obj.visible=false); Object.values(wires).forEach(w=>w.visible=false)}; window.__ps=()=>{const m=mechMeshes.concat(shellMeshes).find(x=>x.userData.mname==='power_support');const b=new THREE.Box3().setFromObject(m);return [b.min.toArray(),b.max.toArray()]};
window.__dbg=()=>({CENTER:CENTER.toArray(),servo:Object.fromEntries(Object.entries(servoPos).map(([k,v])=>[k,v.toArray().map(x=>+x.toFixed(1))])),bat:extra.batC.toArray(),hub:hub.position.toArray(),banana:banana.position.toArray(),lens:lensP.toArray(),stack:stackC3.toArray(),imu:parts.imu.c.toArray()});
frame();
