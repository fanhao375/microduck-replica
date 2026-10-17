// Display-only quaternion math. Coordinates are right handed, Z up; input is xyzw.
export const DEFAULT_MOUNT = 'y90';
export const STALE_MS = 1500;
const identity = () => [0, 0, 0, 1];
const finite = value => typeof value === 'number' && Number.isFinite(value);
const inverse = q => [-q[0], -q[1], -q[2], q[3]];
const normalized = q => { const n = Math.hypot(...q); return q.map(v => v / n); };
export function multiply(a, b) {
  const [x, y, z, w] = a, [X, Y, Z, W] = b;
  return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X, w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z];
}
export function mountQuaternion(name) {
  const map = {identity:[2,0], z90:[2,90], z180:[2,180], 'z-90':[2,-90],
    x90:[0,90], 'x-90':[0,-90], y90:[1,90], 'y-90':[1,-90]};
  if (!map[name]) throw new Error('Unknown mounting orientation');
  const [axis, degrees] = map[name], half = degrees*Math.PI/360, q = identity();
  q[axis] = Math.sin(half); q[3] = Math.cos(half); return q;
}
export function displayQuaternion(input, reference, mounting) {
  // Sensor→world × body→sensor; with zero, change basis into the initial body frame.
  const q = reference ? multiply(multiply(mounting, multiply(inverse(reference), input)), inverse(mounting))
    : multiply(input, inverse(mounting));
  return normalized(q);
}
export function eulerDegrees(q) {
  const [x,y,z,w] = q, deg = 180/Math.PI;
  return [Math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y))*deg,
    Math.asin(Math.max(-1, Math.min(1, 2*(w*y-z*x))))*deg,
    Math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))*deg];
}
export function validPacket(p) {
  const q = p?.quaternion_xyzw;
  if (p?.live !== true || p.status !== 'live' ||
      !(p.mode === 'demo' || (p.mode === 'live' && p.connected === true)) ||
      !Array.isArray(q) || q.length !== 4 || !q.every(finite) ||
      Math.hypot(...q) <= .5 || Math.hypot(...q) >= 1.5) return false;
  // DXL2 bus packets expose gyro + quaternion only; the legacy ready/configured
  // flags and sample counter are absent. A fresh live packet is the validity signal.
  if (p.backend === 'bus' && p.sample_count == null) return true;
  return p.ready === 1 && p.configured === 1 &&
    Number.isSafeInteger(p.sample_count) && p.sample_count >= 0;
}
export class ImuAttitude {
  constructor() {
    this.mountName = DEFAULT_MOUNT; this.mounting = mountQuaternion(this.mountName);
    this.reference = null; this.raw = false; this.target = identity();
    this.latest = null; this.lastValid = null; this.valid = false; this.status = 'disconnected';
    this.lastReceive = null; this.lastProgress = null; this.sampleCount = null; this.mode = null;
  }
  receive(packet, now) {
    if (!packet || packet.type !== 'imu') return;
    if (this.mode !== packet.mode) {
      this.reference = null; this.sampleCount = null; this.lastProgress = null;
    }
    this.mode = packet.mode; this.latest = packet; this.lastReceive = now;
    this.valid = validPacket(packet);
    this.status = this.valid ? 'live' : (packet.status === 'live' ? 'error' : packet.status || 'disconnected');
    if (!this.valid) return;
    if (packet.sample_count == null) {
      this.lastProgress = now; // DXL2 IMU has no sample counter; each packet is a fresh bus read.
    } else {
      if (this.sampleCount !== null && packet.sample_count < this.sampleCount) this.reference = null;
      if (this.sampleCount !== packet.sample_count) this.lastProgress = now;
    }
    this.sampleCount = packet.sample_count;
    if (this.expire(now)) return;
    this.lastValid = normalized(packet.quaternion_xyzw);
    if (!this.raw && !this.reference) this.reference = [...this.lastValid];
    this.update();
  }
  update() {
    if (this.lastValid) this.target = displayQuaternion(this.lastValid, this.raw ? null : this.reference, this.mounting);
  }
  expire(now) {
    if (this.valid && (now-this.lastReceive >= STALE_MS || now-this.lastProgress >= STALE_MS)) {
      this.valid = false; this.status = 'stale'; return true;
    }
    return false;
  }
  disconnect() { this.valid = false; this.status = 'disconnected'; }
  zero() {
    if (!this.valid) return false;
    this.reference = [...this.lastValid]; this.raw = false; this.update(); return true;
  }
  showRaw() { if (!this.valid) return false; this.raw = true; this.update(); return true; }
  setMount(name) { this.mounting = mountQuaternion(name); this.mountName = name; if (this.valid) this.update(); }
}
