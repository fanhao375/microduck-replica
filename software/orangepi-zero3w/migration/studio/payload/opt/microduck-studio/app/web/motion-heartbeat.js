// One page lease per server session. Losing activity never resumes an old token.
const LIVE = new Set(['recording', 'record_paused', 'playing', 'play_paused', 'idle_motion']);

export const isBackgroundMusic = snapshot => snapshot?.state === 'idle_motion' && snapshot?.idle_kind === 'music';

export function motionTime(value) {
  const ticks = Number.isFinite(value) ? Math.max(0, Math.round(value * 10)) : 0;
  return `${Math.floor(ticks / 600)}:${((ticks % 600) / 10).toFixed(1).padStart(4, '0')}`;
}

export function createMotionHeartbeat({ send, isActive, isConnected, onError = () => {},
  schedule = fn => setInterval(fn, 100), cancel = id => clearInterval(id) }) {
  let token = null, timer = null, generation = 0, pending = false, disposed = false, background = false;
  const blocked = new Set();
  const clear = () => { if (timer !== null) cancel(timer); timer = null; pending = false; generation += 1; };
  const stop = () => {
    const previous = token;
    clear(); token = null; background = false;
    if (!previous) return;
    blocked.add(previous);
    if (blocked.size > 64) blocked.delete(blocked.values().next().value);
    if (isConnected()) {
      try { Promise.resolve(send({ op: 'motion', action: 'stop', session_id: previous })).catch(() => {}); }
      catch { /* Server-side expiry still ends output when the transport is unavailable. */ }
    }
  };
  const beat = async () => {
    if (disposed || !token) return;
    if (!isActive() || !isConnected()) { stop(); return; }
    if (pending) return;
    const mine = generation, current = token;
    pending = true;
    try {
      const result = await send({ op: 'motion', action: 'heartbeat', session_id: current });
      if (disposed || mine !== generation) return;
      if (!result?.ok) {
        stop(); onError(result?.reason || result?.error || '动作会话已停止，请重新开始');
      }
    } catch (error) {
      if (!disposed && mine === generation) { stop(); onError(error?.message || '动作会话连接中断'); }
    } finally {
      if (mine === generation) pending = false;
    }
  };
  return {
    sync(snapshot) {
      if (disposed) return;
      const next = snapshot?.session_id;
      if (!snapshot?.owner?.mine || !LIVE.has(snapshot.state) || !next) { clear(); token = null; background = false; return; }
      if (blocked.has(next)) return;
      const nextBackground = isBackgroundMusic(snapshot);
      if (next === token && background === nextBackground) {
        if (!isConnected() || (!background && !isActive())) stop();
        return;
      }
      clear(); token = next; background = nextBackground;
      if (!isConnected() || (!background && !isActive())) { stop(); return; }
      if (background) return;
      timer = schedule(beat); void beat();
    },
    interrupt(reason) { if (reason === 'inactive' && background && isConnected()) return; stop(); },
    dispose() {
      if (disposed) return;
      if (background && isConnected()) { clear(); token = null; }
      else stop();
      disposed = true;
    },
  };
}
