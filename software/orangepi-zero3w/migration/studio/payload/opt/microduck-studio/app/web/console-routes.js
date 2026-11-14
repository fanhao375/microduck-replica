export const PAGES = {
  overview: ['设备总览', '设备状态与下一步操作。', 'home'],
  remote: ['遥控与动作', '遥控、录制与手柄。', 'remote'],
  sensing: ['语音与视觉', '画面、距离与语音。', 'camera'],
  devices: ['设备管理', '校准、维护与诊断。', 'device'],
  models: ['模型中心', '按动作用途管理模型。', 'layers'],
  logs: ['运行日志', '运行记录与诊断导出。', 'log'],
  settings: ['系统设置', '连接、偏好与软件维护。', 'settings'],
};
const DEFAULTS = {remote:'control', sensing:'camera', devices:'servos', models:'library', settings:'network'};
const SECTIONS = {remote:['control','recording','gamepad'], sensing:['camera','tof','voice','audio'], devices:['servos','imu','diagnostics'], models:['library','bindings','profile'], settings:['network','account','ai','battery','updates','display']};
const LEGACY = {'/':'/overview', '/pad':'/remote/control', '/servos':'/devices/servos', '/imu':'/devices/imu', '/camera':'/sensing/camera', '/tof':'/sensing/tof', '/voice':'/sensing/voice'};

export function normalizeRoute(raw = '/') {
  const path = String(raw).replace(/^#/, '').split('?')[0].replace(/\/$/, '') || '/';
  if (LEGACY[path]) return LEGACY[path];
  const [page, section] = path.replace(/^\//, '').split('/');
  if (!PAGES[page]) return '/overview';
  if (!SECTIONS[page]) return '/' + page;
  return `/${page}/${SECTIONS[page].includes(section) ? section : DEFAULTS[page]}`;
}

// Keep the backend's route-based ownership and voice authorization intact.
export function serverRoute(raw) {
  const [page, section] = normalizeRoute(raw).slice(1).split('/');
  if (page === 'overview') return '/';
  if (page === 'remote') return '/pad';
  if (page === 'devices') return section === 'servos' ? '/servos' : section === 'imu' ? '/imu' : '/logs';
  if (page === 'sensing') return section === 'audio' ? '/pad' : '/' + section;
  if (page === 'models') return '/settings'; // Model profiles need the original tune subscription.
  return '/' + page;
}

export function routeInfo(raw) {
  const route = normalizeRoute(raw), [page, section] = route.slice(1).split('/');
  return {route, page, section, title:PAGES[page][0], description:PAGES[page][1]};
}

export const SEARCH = [
  ...Object.entries(PAGES).map(([path,[label]])=>['/' + path,label]),
  ['/overview','首次启动 · 从检查到第一次迈步'],
  ['/devices/servos','舵机标定 · 零位参考、保持与中位'],
  ['/devices/imu','IMU · 方向与站姿校准'],
  ['/devices/diagnostics','总线与运行诊断'],
  ['/remote/recording','录制与动作库'], ['/remote/gamepad','手柄配对与急停测试'],
  ['/sensing/voice','按住说话'], ['/sensing/audio','机器人监听与音乐'],
  ['/models/bindings','手柄按键映射'], ['/models/profile','机器档案与参数'],
  ['/settings/network','WiFi、热点与设备地址'], ['/settings/account','账号与 SSH'],
  ['/settings/ai','AI 连接'], ['/settings/battery','电池与电压校准'],
  ['/settings/updates','软件更新与恢复'], ['/settings/display','显示与关于'],
];
