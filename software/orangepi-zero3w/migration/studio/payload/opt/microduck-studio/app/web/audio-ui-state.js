export function audioLabel(status) {
  if (!status?.available) return '语音服务未连接';
  if (!status.listening) return '监听已关闭';
  return status.music ? '正在听音乐和提问' : '正在听你说话';
}

export function rhythmLabel(rhythm) {
  if (!rhythm || rhythm.confidence < .55 || !Number.isFinite(rhythm.bpm)
      || !Number.isFinite(rhythm.age) || rhythm.age > .5) return '等待清晰节拍';
  const feel = { lively: '轻快', mellow: '低沉', sway: '摇摆' }[rhythm.feel];
  return feel ? `${feel} · ${Math.round(rhythm.bpm)} BPM` : '等待清晰节拍';
}

export function musicMotionLabel(motion) {
  if (motion?.state !== 'idle_motion' || motion.idle_kind !== 'music') return null;
  return motion.idle_phase === 'moving' ? '随乐律动中' : '等待稳定节拍';
}
