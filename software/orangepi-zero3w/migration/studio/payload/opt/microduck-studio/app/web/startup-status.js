// Startup replies are diagnostic evidence; missing/expired evidence stays unknown.
export function servoReplyStatus(row) {
  if (!Object.hasOwn(row, 'responding')) return null;
  if (row.responding === true) return { word: '有应答', kind: 'ok' };
  if (row.responding === false) return { word: '未应答', kind: 'bad' };
  return { word: '未知', kind: '' };
}

export function startupStageCopy(st) {
  if (st?.posture !== 'starting' || !st.start_wait?.text) return null;
  return { h: st.start_wait.text,
    sub: `${st.starting_long ? `已经 ${st.starting_s} 秒了。` : ''}${st.start_error ? `原文：${st.start_error}。` : ''}去日志页看主程序最后说了什么。`,
    act: { href: '#/logs', label: '去日志页' } };
}

// Diagnostic evidence is separate from mounting and control readiness.
export function imuDiagnosticCopy(diagnostic, live = true, elapsedMs = 0) {
  const d = live !== false ? diagnostic : null;
  const expired = d && Number.isFinite(d.age_ms) && d.age_ms + Math.max(0, elapsedMs) >= 3000;
  const status = expired ? 'stale' : d?.status || 'unknown';
  const words = { identified: '已识别 · 有数据', data_wait: '已识别 · 等待数据',
    no_response: '未应答', stale: '诊断已过期', unknown: '诊断未知' };
  const boards = { ft6: 'FT6', imu_to_dxl: 'IMU-to-DXL', sflp_124_12: 'SFLP 124/12', qinmo: 'Qinmo（已配置）' };
  const protocols = { feetech: 'Feetech', dxl2: 'Dynamixel 2.0' };
  const info = !expired && ['identified', 'data_wait', 'stale'].includes(status) ? d?.info : null;
  const board = info && boards[info.board] && protocols[info.protocol]
    ? `${boards[info.board]} · ${protocols[info.protocol]}（ID 200）` : null;
  return { word: status === 'unknown' && d?.responding === true ? '有应答 · 类型待确认' : words[status] || words.unknown, board,
    kind: status === 'no_response' ? 'bad' : ['data_wait', 'stale'].includes(status) ? 'warn' : '',
    hint: { identified: '已收到可解码读数；采样是否持续更新仍以主程序检查为准。',
      data_wait: '板卡有应答，但传感器尚未就绪或读数无效。',
      no_response: '本次只读查询没有收到 ID 200 的应答，请检查接线和所选类型。',
      stale: '诊断或采样证据已过期，等待新的只读查询。',
      unknown: d?.responding === true ? '板卡有应答，但身份或所选类型尚未确认。' : '没有当前有效的诊断证据；旧版主程序可能不提供此字段。' }[status] || '等待当前诊断证据。' };
}
