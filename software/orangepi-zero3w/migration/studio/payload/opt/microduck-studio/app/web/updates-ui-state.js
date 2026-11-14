export const PHASES={queued:'等待任务启动',uploading:'上传中',checking:'检查发布源',downloading:'下载中',downloaded:'已下载',
  preflight:'检查停稳与供电',verifying:'验证签名与软件包',extracting:'验证运行文件',swapping:'切换版本',
  applying:'启动匹配的网页和语音服务',health_gate:'检查网页与本地语音',committing:'记录安装结果',
  rolling_back:'正在恢复上一版',completed:'任务完成',failed:'任务失败',interrupted:'任务曾中断，需核对结果',
  waiting_validation:'已下载，待停稳验证',rolled_back:'更新失败，已恢复旧版'};
export function updateMessage(data){
  if(!data?.available)return '软件更新服务尚未安装或暂时不可用';
  const job=data.job;
  if(job?.phase==='rolled_back')return '更新失败，已恢复旧版。'+(job.error||'');
  if(job?.phase==='waiting_validation')return '已下载，待停稳验证。'+(job.error||'');
  if(job?.phase==='failed'||job?.phase==='interrupted')return (PHASES[job.phase]||'任务未完成')+'：'+(job.error||'请重试');
  if(data.busy)return PHASES[job?.phase]||'更新任务进行中';
  if(data.ready)return '签名和运行文件已由升级引擎验证，可安装。';
  if(data.source_state==='no_release')return '尚无发布的软件更新。';
  if(data.source_state==='network_error')return '网络检查失败，请稍后重试；尚未确认是否有更新。';
  if(data.source_state==='up_to_date')return '当前已是发布源中的最新软件版本。';
  if(data.source_state==='available')return '发现软件更新，下载后由升级引擎验证。';
  return job?.phase==='completed'?'任务完成。':'手动检查软件更新，或上传离线软件包。';
}
export function updateActions(data,pending=false){
  const idle=!!data?.available&&!data.busy&&!pending;
  const nativeBusy=data?.native?.active===true||data?.native?.submitting===true;
  return {check:idle,upload:idle,download:idle&&!!data.candidate&&data.source_state==='available',
    validate:idle&&!!data.staged,install:idle&&!nativeBusy&&data.ready===true,rollback:idle&&!nativeBusy&&data.can_rollback===true};
}

export const NATIVE_PHASES={prepared:'已备份，准备保护控制服务',guarding:'正在建立持久保护',guarded:'控制服务已隔离，核对卸力',
  replacing:'正在安装主程序',replaced:'主程序已替换，等待实际启动核验',verified_commissioning:'标定模式实际运行已核验',
  restoring_normal:'正在恢复原控制模式',verified_motion:'实际控制进程已核验，仍保持卸力',factory_handoff_pending:'正在交给首次标定流程',
  completed:'事务已记录完成',rolled_back:'安装失败，已恢复并核验旧主程序',recovering:'正在安全恢复主程序',
  old_restored:'旧主程序已恢复，等待实际运行核验',recovery_required:'主程序更新未完成，请扶稳卸力后恢复'};

export function nativeMessage(native){
  if(!native)return '读取独立的主程序状态…';
  if(native.active)return (NATIVE_PHASES[native.phase]||'主程序更新保护仍在，请核对或恢复')+(native.reason?`。${native.reason}`:'');
  if(native.submitting)return '正在向独立小助手提交请求；请等待实际运行状态，刷新页面可继续查看。';
  if(!native.service_available)return native.reason||'当前小助手未提供主程序更新服务，请先更新网页 / 语音。';
  if(native.current)return '目标主程序实际运行身份已核验。'+(native.mode==='commissioning'?'请继续 IMU 选择和方向验证。':'秦墨实物仍待验收，使用前完成 IMU 方向验证。');
  if(!native.available)return '本软件包没有可安装的主程序更新。';
  if(!native.supported)return native.reason||'当前主程序实际运行身份未核验，不能自动安装，请联系维护者核对版本。';
  if(native.phase==='rolled_back')return '安装失败，已恢复并核验旧主程序。'+(native.reason||'');
  return '主程序更新待安装；网页 / 语音版本不代表主程序已更新。';
}

export function nativeActions(data,pending=false,confirmed=false){
  const native=data?.native;
  const idle=!!native?.service_available&&!data?.busy&&!native.submitting&&!pending&&confirmed===true;
  return {install:idle&&native.available===true&&native.supported===true&&!native.current&&!native.active,
    recover:idle&&native.active===true};
}
