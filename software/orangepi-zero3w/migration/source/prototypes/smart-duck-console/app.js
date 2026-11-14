(() => {
  const P = window.P;
  const routes = {
    overview: ['设备总览','设备状态与下一步操作。','home'],
    remote: ['遥控与动作','遥控、录制与手柄。','remote'],
    sensing: ['语音与视觉','画面、距离与语音。','camera'],
    devices: ['设备管理','校准、维护与诊断。','device'],
    models: ['模型中心','按动作用途管理模型。','layers'],
    logs: ['运行日志','运行记录与诊断导出。','log'],
    settings: ['系统设置','连接、偏好与软件维护。','settings'],
  };
  const navGroups = [['日常使用',['overview','remote','sensing']],['设备与系统',['devices','models','logs','settings']]];
  const searchItems = [
    ...Object.entries(routes).map(([path,[label]])=>[path,label]),
    ['overview','首次启动 · 从装机检查到第一次迈步'],
    ['remote/control','网页遥控 · 站立、行走、坐下'],['remote/recording','动作录制 · 保存与回放'],['remote/gamepad','实体手柄 · 配对与急停测试'],
    ['devices/servos','舵机标定 · 零位参考、保持与中位','maintenance'],['devices/servos','关节跟踪 · P / D 调试','tracking'],['devices/servos','高级工具 · 寄存器与改 ID','advanced'],
    ['devices/imu','IMU · 粗校与站立精校'],['devices/diagnostics','运行诊断 · 总线与反馈'],['sensing/camera','相机预览'],['sensing/tof','ToF 距离'],['sensing/voice','按住说话 · 语音对话'],['sensing/audio','机器人监听 · 听音乐与节奏'],
    ['models/bindings','手柄按键映射'],['models/profile','机器档案 · 重量与脚底材料'],['settings/network','WiFi、热点与设备地址'],['settings/account','网页登录、SSH 与公钥'],['settings/ai','AI 连接与文字试聊'],['settings/battery','2S 电池 · 电量与电压校准'],['settings/updates','软件更新与恢复'],['settings/display','显示偏好与状态栏'],
  ];
  let activePage = 'overview';
  P.getImuViewerState = () => P.imuPage?.getViewerState?.() || null;
  function route() {
    const [name,sub] = location.hash.replace(/^#\/?/, '').split('/');
    return [routes[name] ? name : 'overview',sub || ''];
  }
  function stopOutputs() {
    Object.assign(P.state,{torque:false,jointTorque:{},standing:false});
    P.stopRemote?.(); P.stopDevice?.();
  }
  P.stopAllOutputs=stopOutputs;
  function banners() {
    const S=P.state,systemBusy=P.isSystemBusy();
    let html='';
    if((S.offline || S.stale)&&!systemBusy) html+=`<div class="status-banner" role="status">${S.offline?'设备未连接':'回读已过期'}，动力状态未知。<button class="btn small" data-action="demo-reconnect">${S.offline?'重新连接':'恢复回读'}</button></div>`;
    const [page,sub]=route();
    if(systemBusy&&!(page==='settings'&&sub==='updates')) html+='<div class="status-banner" role="status">软件更新中，设备操作暂不可用。<a class="btn small" href="#/settings/updates">查看进度</a></div>';
    const handoffPending=S.calibrationStatus==='failed'&&S.calibrationFailedStage==='handoff';
    if(S.owned && !(page==='devices' && (!sub || sub==='servos')) && !(page==='overview'&&P.onboarding.active())) {
      const returning=S.maintenanceStatus==='returning', failed=S.maintenanceStatus==='failed';
      html+=`<div class="status-banner ownership ${failed?'failed':''}" role="status"><span>${returning?'正在交还，等待主程序回读…':failed?(handoffPending?'交还未确认，标定尚未写入':'交还未确认，请查看日志后重试'):'设备维护已接管总线'}</span><div class="button-row">${failed?'<a class="link" href="#/logs">查看原因</a>':''}<button class="btn small" data-action="release-bus" ${returning?'disabled':''}>${returning?'交还中':'交还主程序'}</button></div></div>`;
    }
    if(S.calibrationStatus==='verifying'&&!P.onboarding.active()) html+='<div class="status-banner" role="status">提交演示：正在重启主程序并等待回读…</div>';
    if(S.calibrationStatus==='failed'&&!(handoffPending&&S.owned)&&!P.onboarding.active()) html+=`<div class="status-banner" role="status">${handoffPending?'总线已交还，标定仍待提交。':'提交未生效，原标定已恢复。'}<button class="btn small" data-action="calibration-commit">查看并重试</button></div>`;
    if(S.hotspotMode) html+='<div class="status-banner" role="status">当前通过设备热点连接。<a class="btn small" href="#/settings/network">连接 WiFi</a></div>';
    return html;
  }
  P.render = () => {
    P.onboarding.sync();
    const retainedZeroStage = P.zeroReference?.retain();
    const retainedOverviewStage = P.overviewDuck?.retain();
    const retainedImuStages = P.imuViewer?.retain();
    const [name,sub]=route(); activePage=name;
    const [title,description]=routes[name];
    const unknown=P.isSystemBusy()||P.state.offline||P.state.stale||['starting','stopped'].includes(P.state.boot)||P.state.owned;
    const servoUnknown=P.isSystemBusy()||P.state.offline||P.state.stale||(['starting','stopped'].includes(P.state.boot)&&!P.state.owned);
    const imuStatus=unknown?'—':P.state.devices?.imu?.mount.verified===false?'待验证':P.state.boot==='commissioning'?'标定中':'正常';
    const bench=P.state.system?.battery?.preset==='bench';
    const telemetry=P.state.telemetry||{},servoRead=telemetry.servos||{};
    const servoCount=Number.isFinite(servoRead.online)&&Number.isFinite(servoRead.total)?servoRead.online+'/'+servoRead.total:'—';
    const controlHz=Number.isFinite(telemetry.controlHz)?telemetry.controlHz+' Hz':'—';
    const cpuTemperature=Number.isFinite(telemetry.cpu?.c)?telemetry.cpu.c.toFixed(1)+'°C':'—';
    document.title=`${title} · 智能鸭`;
    document.body.classList.toggle('statusbar-hidden',P.state.system?.showStatus===false);
    document.querySelector('#app').innerHTML=`<div class="mobile-scrim" data-action="menu"></div>
      <aside class="sidebar"><a href="#/overview" class="brand"><svg class="brand-mark" viewBox="0 0 34 44" fill="none" aria-hidden="true"><path d="M16 24c-3-8-3-15 2-16 6-2 9 6 5 11 0 2 2 3 5 4 7 6 1 14-9 14C5 38 1 30 8 26l8-2Z" stroke="currentColor" stroke-width="2.3"/><circle cx="21" cy="12" r="1" fill="currentColor"/></svg><span><span class="brand-name">智能鸭</span><small>SMART DUCK</small></span></a>
      <div class="device-selector">${P.icon('device',14)}<span>Cubie A7Z <small>A733</small></span><i class="online-dot" ${P.state.offline?'style="background:#bac5cb"':''}></i></div>
      <nav aria-label="主导航">${navGroups.map(([group,items])=>`<div class="nav-group"><p class="nav-label">${group}</p>${items.map(key=>`<a class="nav-link ${name===key?'active':''}" href="#/${key}" ${name===key?'aria-current="page"':''}>${P.icon(routes[key][2],17)}${routes[key][0]}</a>`).join('')}</div>`).join('')}</nav>
      <div class="sidebar-bottom"><span class="avatar">我</span><span><strong>设备账号</strong><small>本地设备</small></span></div></aside>
      <div class="workspace"><header class="topbar"><div class="breadcrumb"><button class="icon-btn" data-action="menu" aria-label="展开或收起菜单">${P.icon('menu',19)}</button><span>智能鸭空间</span><span>/</span><b>${title}</b></div><div class="top-tools"><button class="search-trigger" data-action="search" aria-label="搜索页面与功能">${P.icon('search',14)}<span>搜索页面与功能</span><kbd>⌘ K</kbd></button><span class="prototype-label">交互原型 · 演示数据</span><button class="btn danger small global-unload" data-action="emergency">${P.icon('power',14)}全局卸力</button></div></header>
      <main class="main" id="main"><div class="page-heading"><div><h1>${title}</h1><p>${description}</p></div><div class="heading-actions">${name==='overview'?`<button class="btn white" data-action="preview-states">${P.icon('eye',14)}状态预览</button>`:''}</div></div>
      ${banners()}${P.onboarding.contextMarkup(name,sub)}<section id="page-content">${P.pages[name]?.render(sub)||''}</section></main></div>
      <nav class="mobile-primary-nav" aria-label="手机常用导航">${[['overview','home','总览'],['remote','remote','遥控'],['devices','device','设备']].map(([key,icon,label])=>`<a href="#/${key}" class="${name===key?'active':''}" ${name===key?'aria-current="page"':''}>${P.icon(icon,20)}<span>${label}</span></a>`).join('')}<button data-action="more-pages">${P.icon('menu',20)}<span>更多</span></button></nav>
      <footer class="statusbar" aria-label="设备状态"><span>${P.icon('power',12)}动力 <b>${P.torqueStatus()}</b></span><span>${P.icon('device',12)}舵机 <b>${servoUnknown?'—':servoCount}</b></span><span>${P.icon('imu',12)}IMU <b>${imuStatus}</b></span><span class="mobile-hide">${P.icon('activity',12)}采集 <b>${unknown?'—':controlHz}</b></span><span class="mobile-hide">${P.icon('thermometer',12)}温度 <b>${unknown?'—':cpuTemperature}</b></span><a href="#/settings/battery" title="2S 电压估算"><span>${P.icon('battery',12)}${bench?'供电':'电量'} <b>${servoUnknown?'—':bench?P.state.batteryVolts.toFixed(1)+' V':'约 '+P.batteryPercent(P.state.batteryVolts+Number(P.state.system?.battery?.offset||0))+'%'}</b></span></a><span class="optional-status">${P.icon('wifi',12)}网络 <b>${P.state.offline?'离线':'局域网'}</b></span><span class="statusbar-end">${P.state.owned?'设备维护持有控制权':'主程序持有控制权'}</span></footer>`;
    P.pages[name]?.afterRender?.(sub);
    P.zeroReference?.mount(retainedZeroStage);
    P.overviewDuck?.mount(retainedOverviewStage);
    P.imuViewer?.mount(retainedImuStages);
    P.onboarding.save();
  };
  function handBack(onReady) {
    stopOutputs();P.state.maintenanceStatus='returning';P.render();
    setTimeout(()=>{
      if(P.state.returnShouldFail){P.state.maintenanceStatus='failed';if(P.state.calibrationStatus==='handoff'){P.state.calibrationStatus='failed';P.state.calibrationFailedStage='handoff';}}
      else {P.state.owned=false;P.state.maintenanceStatus='idle';onReady?.();}
      P.render();
    },700);
  }
  P.openCalibrationCommit = () => {
    if(P.isSystemBusy()){P.toast('软件更新进行中，等待重新连接后再提交');return;}
    if(['handoff','verifying'].includes(P.state.calibrationStatus)){P.toast('提交演示正在进行');return;}
    const blocked = P.onboarding.commitBlock();
    if(blocked){P.toast(blocked);return;}
    const pending=P.state.calibrationPending;
    const count=pending.joints.length+(pending.imu?1:0);
    if(!count){P.openModal('标定提交','<p>尚无待提交的标定。先完成舵机中位或 IMU 标定，再在这里统一预览。</p>','关闭');return;}
    const joints=pending.joints.map(j=>typeof j==='object'?(j.id??j.joint):j).join('、');
    const beginCommit=()=>{
      P.state.calibrationStatus='verifying';P.state.calibrationFailedStage='write';P.render();
      setTimeout(()=>{
        if(P.state.calibrationShouldFail){P.state.calibrationStatus='failed';P.state.calibrationCommitted=false;}
        else {P.state.calibrationStatus='complete';P.state.calibrationCommitted=true;P.state.boot='ready';P.state.calibrationPending={joints:[],imu:false};if(P.state.devices)P.state.devices.undo=null;P.onboarding.record('committed');P.toast('提交、重启与回读流程已演示');}
        P.render();
      },800);
    };
    P.openModal('预览标定提交',`<div class="field-row"><strong>舵机零位与方向</strong><span>${joints?'ID '+P.escape(joints):'本次未修改'}</span></div><div class="field-row"><strong>IMU 安装方向</strong><span>${pending.imu?'待提交':'本次未修改'}</span></div><p class="inline-note">${P.state.owned?'先卸力并交还主程序，再':'将'}保存标定、重启并读回验证。</p><details><summary>失败时如何恢复</summary><p>交还未确认时不提交；写入或重启验证失败时，保留原因和原标定恢复结果。</p></details><label class="device-check"><input id="commit-supported" type="checkbox"> 已扶稳机身，允许卸力并继续</label>`,P.state.owned?'交还并提交演示':'演示提交与验证',dialog=>{
      if(P.isSystemBusy()){P.toast('等待软件更新完成后再提交');return false;}
      const blocked = P.onboarding.commitBlock();if(blocked){P.toast(blocked);return false;}
      if(!dialog.querySelector('#commit-supported').checked){P.toast('请先确认支撑与卸力');return false;}
      if(P.torqueStatus()==='未知'){P.toast('动力状态未知，先恢复有效回读');return false;}
      if(P.state.owned){P.state.calibrationStatus='handoff';handBack(beginCommit);}
      else {stopOutputs();beginCommit();}
    });
  };
  function releaseBus() {
    if(P.state.maintenanceStatus==='returning')return;
    P.openModal('交还主程序','<p>交还前会全部卸力，请先扶稳机身。</p>','确认卸力并交还',()=>handBack(()=>P.toast('交还与主程序回读恢复流程已演示')));
  }
  P.releaseBus = releaseBus;
  P.emergency = () => { stopOutputs(); P.onboarding.record('unload'); P.render(); P.toast(P.torqueStatus()==='未知'?'无法确认卸力，请现场核对':'已卸力'); };
  function previewStates() {
    const choices=[['normal','在线 · 已卸力'],['offline','连接断开'],['stale','回读过期'],['owned','维护接管'],['first','首次装机'],['starting','主程序启动中'],['stopped','主程序未运行'],['blocked','IMU 验证未通过'],['return-failed','交还恢复失败'],['hotspot','热点配网'],['commit-failed','标定提交失败']];
    P.openModal('预览设备状态',`<label class="field">状态<select id="preview-select">${choices.map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}</select></label>`,'切换状态',dialog=>{
      const v=dialog.querySelector('select').value;
      const system=P.state.system;
      if(system&&v==='hotspot'){
        if(!P.state.hotspotMode)P.state.previewNetwork={wifi:system.wifi,wifiOn:system.wifiOn,hotspot:system.hotspot};
        Object.assign(system,{wifi:'未连接',wifiOn:true,hotspot:true,networkPhase:'idle',pendingSsid:'',networkError:''});
      }else if(system&&P.state.previewNetwork){
        if(P.state.hotspotMode&&system.hotspot&&system.wifi==='未连接')Object.assign(system,P.state.previewNetwork);
        delete P.state.previewNetwork;
      }
      stopOutputs();Object.assign(P.state,{offline:v==='offline',stale:v==='stale',owned:['owned','return-failed'].includes(v),boot:['starting','stopped','blocked'].includes(v)?v:'ready',maintenanceStatus:v==='return-failed'?'failed':'idle',returnShouldFail:v==='return-failed',hotspotMode:v==='hotspot',calibrationStatus:v==='commit-failed'?'failed':'idle',calibrationShouldFail:v==='commit-failed'});
      if(v==='first')P.onboarding.reset();
      P.render();
    });
  }
  function globalAction(action,target) {
    if(P.onboarding.handle(action,target))return true;
    if(action==='menu'){document.body.classList.toggle(innerWidth<=680?'menu-open':'sidebar-collapsed');return true;}
    if(action==='more-pages'){P.openModal('全部页面',`<div class="search-results">${Object.entries(routes).map(([path,[label]])=>`<a href="#/${path}" data-close-modal>${label}${P.icon('chevron',14)}</a>`).join('')}</div>`,'关闭');return true;}
    if(action==='emergency'){P.emergency();return true;}
    if(action==='takeover'){if(P.isSystemBusy()){P.toast('先等待软件更新完成');return true;}if(['handoff','verifying'].includes(P.state.calibrationStatus)){P.toast('先等待当前提交完成');return true;}P.openModal('接管设备维护','接管后，遥控和模型运行暂停。维护完成后请交还主程序。','确认接管',()=>{if(P.isSystemBusy()||['handoff','verifying'].includes(P.state.calibrationStatus)){P.toast('先等待当前更新或提交完成');return false;}stopOutputs();P.state.owned=true;P.state.maintenanceStatus='idle';P.render();});return true;}
    if(action==='release-bus'){releaseBus();return true;}
    if(action==='calibration-commit'){P.openCalibrationCommit();return true;}
    if(action==='toggle'){const key=target.dataset.key;P.state[key]=!P.state[key];P.render();return true;}
    if(action==='demo-reconnect'){P.state.offline=false;P.state.stale=false;P.render();return true;}
    if(action==='preview-states'){previewStates();return true;}
    if(action==='search'){
      P.openModal('搜索页面与功能','<input id="page-search" type="search" placeholder="输入页面或功能名称" aria-label="搜索页面与功能" style="width:100%"><div class="search-results"></div>','关闭');
      const field=document.querySelector('#page-search');
      const update=()=>{const q=field.value.trim().toLowerCase();const list=searchItems.filter(([,label])=>label.toLowerCase().includes(q));document.querySelector('.search-results').innerHTML=(q?list:list.slice(0,7)).map(([path,label,tab])=>`<a href="#/${path}" ${tab?`data-device-tab="${tab}"`:''} data-close-modal>${label}${P.icon('chevron',13)}</a>`).join('')||'<p class="muted">未找到相关功能</p>';};
      field.addEventListener('input',update);update();field.focus();return true;
    }
    return false;
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href^="#/"]');
    if(link){document.body.classList.remove('menu-open');if(link.hasAttribute('data-close-modal'))document.querySelector('#modal').close();if(link.dataset.deviceTab)P.deviceTab?.(link.dataset.deviceTab);}
    const target=event.target.closest('[data-action]');if(!target)return;
    const action=target.dataset.action;
    if(globalAction(action,target))return;
    const inspect=['device-tab','joint-select','joint-group','joint-prev','joint-next','diagnostic-offline','diagnostic-stale','diagnostic-check'];
    // Stateful workflows distinguish stopping from resuming even when the connection is unavailable.
    const pageOwnsGate=activePage==='remote'||activePage==='devices'&&action.startsWith('imu-');
    const stopAction=['servo-stop','servo-idle-stop','tracking-stop'].includes(action);
    if((P.state.offline||P.state.stale)&&['remote','devices'].includes(activePage)&&!inspect.includes(action)&&!pageOwnsGate&&!stopAction){P.toast('等待设备连接与有效回读');return;}
    P.pages[activePage]?.handle?.(action,target,event);
  });
  document.addEventListener('keydown',event=>{
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();globalAction('search');}
    if(event.key==='Escape')document.body.classList.remove('menu-open');
    // Match the current console: Space is an immediately reachable unload shortcut.
    if(event.code==='Space'&&!event.repeat&&!document.querySelector('dialog[open]')&&!event.target.closest('input,textarea,select,[contenteditable]')){event.preventDefault();globalAction('emergency');}
  });
  document.addEventListener('submit',event=>{if(!event.target.closest('dialog'))event.preventDefault();});
  window.addEventListener('hashchange',()=>{
    if(activePage==='devices')P.stopDevice?.();
    P.render();window.scrollTo(0,0);
  });
  P.render();
})();
