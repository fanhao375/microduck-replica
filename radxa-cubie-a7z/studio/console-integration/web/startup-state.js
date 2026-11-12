const item = (st,key) => st?.commit?.items?.find(row=>row.key===key);
const row = (st,key) => st?.onboarding?.rows?.find(value=>value.key===key);
const verified = value => value?.state === 'ok' && !value.marked;
export const walkModel = tune => tune?.models?.slots?.find(value=>value.slot==='walk');

export function hardwareProblem(st, live) {
  if (!live || !st) return '等待设备连接与有效回读。';
  if (st.servos?.online !== 15 || st.servos?.total !== 15) return '舵机回读不完整，请检查连接与关节编号。';
  if (!['ok','unverified'].includes(st.imu?.status)) return 'IMU 暂无有效数据，请检查连接。';
  if (!Number.isFinite(st.battery?.volts) || st.battery?.level === 'error') return '供电状态需要检查。';
  return '';
}

// This token invalidates browser-observed checks when the main controller or calibration changes.
export function startupToken(st) {
  return JSON.stringify([st?.build,st?.imu?.generation,...['servo','imu','commit'].map(key=>{const r=row(st,key);return [r?.state,r?.when,r?.text];}),st?.commit?.dirty]);
}

const step = (id,index,title,detail,label,route='',action='navigate') => ({id,index,title,detail,label,route,action});
export function startupStep({st,sv,imu,tune,live,open,checked,choice='web',stopVerified=false,standObserved=false}) {
  const job = st?.commit?.job;
  if (job?.phase === 'running') return {...step('busy',4,'正在写进主程序',job.msg||'等待重启与结果读回。','等待结果'),disabled:true};
  if (!open || !live && !st?.servo_session) return step('connection',0,'连接设备','恢复连接后，继续已有标定。','重新连接','','reconnect');
  if (st?.servo_session || !st && sv?.state === 'open') return step('handoff',2,'校准舵机零位','照零位参考逐颗标定；完成后交还主程序。','交还主程序','/devices/servos','handoff');
  const problem = hardwareProblem(st,live);
  if (!checked || problem) return step('check',1,'检查设备',problem||'确认 15 颗舵机、IMU 和供电状态。','检查设备','','check');
  const servoReady = verified(row(st,'servo')) || st?.commit?.centered === 15 && item(st,'servo')?.ok === true;
  if (!servoReady) return step('servos',2,'校准舵机零位',`已记录 ${st?.commit?.centered??0} / 15。照零位参考，逐颗点“中位”。`,'前往舵机标定','/devices/servos');
  const pendingImu = st?.commit?.pending === true && item(st,'imu')?.ok === true && !item(st,'imu')?.default;
  if (!pendingImu && (imu?.mount?.verified === false || st?.imu?.status === 'unverified')) return step('imu-direction',3,'确认 IMU 安装方向','先做静止与前倾采样，再交给主程序验证。','前往 IMU 校准','/devices/imu');
  if (!pendingImu && !verified(row(st,'imu'))) return step('imu-fine',3,'完成站姿校准','使能站姿后，采集静止与前后左右五项。','继续站姿校准','/devices/imu');
  if (!verified(row(st,'commit')) || st?.commit?.dirty) return step('commit',4,'统一写进主程序',job?.ok===false ? job.reason||'上次提交未确认，可以查看原因后重试。' : '预览标定，卸力后统一保存并读回。','预览并提交','','commit');
  const model = walkModel(tune);
  if (!model?.loaded || model.error) return step('models',4,'确认行走模型',model?.error||'需要主程序已加载；仅“已选用”还不能走。','查看行走模型','/models/library');
  if (verified(row(st,'walk'))) return step('complete',6,'首次启动已完成','之后开机检查正常，就可以进入遥控。','打开遥控','/remote/control');
  if (choice === 'pad' && !verified(row(st,'estop'))) return step('stop-pad',5,'确认手柄能急停','配对、核对按键，再完成手柄急停测试。','前往手柄测试','/remote/gamepad');
  if (choice === 'web' && !stopVerified) return standObserved ? step('stop-web',5,'确认能卸力','已观察到站姿。扶稳躯干，点右上角卸力。','前往遥控','/remote/control') : step('stop-web',5,'先确认能停','扶稳躯干，先站起来，再验证全局卸力。','前往验证停止','/remote/control');
  return step('walk',6,'第一次迈步','站起来 → 等站稳 → 开始走 → 小幅推摇杆。','前往首次行走','/remote/control');
}
