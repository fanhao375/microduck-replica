// 3D 上的舵机标注：编号 + 中文 + 英文，引线指到舵机转轴上。
// 用户 10-18：只在舵机页、点「零位」参考姿势时出现；右腿红、左腿蓝、头颈黄（照他给的那张实物标注图的分法）。
// 看不见的（藏在外壳或别的零件后面）画虚线、空心点：相机到转轴的连线上，前面 3 cm 以外先碰到别的零件就算挡住
// （转轴本来就在舵机里面，先碰到自己外壳很正常，所以要留 3 cm）。
import * as THREE from './vendor/three/three.module.js';

export const TAGS = {
  right_hip_yaw: [10, '右髋偏航', 'Right Hip Yaw', 'R'], right_hip_roll: [11, '右髋横滚', 'Right Hip Roll', 'R'],
  right_hip_pitch: [12, '右髋俯仰', 'Right Hip Pitch', 'R'], right_knee: [13, '右膝', 'Right Knee', 'R'],
  right_ankle: [14, '右踝', 'Right Ankle', 'R'],
  left_hip_yaw: [20, '左髋偏航', 'Left Hip Yaw', 'L'], left_hip_roll: [21, '左髋横滚', 'Left Hip Roll', 'L'],
  left_hip_pitch: [22, '左髋俯仰', 'Left Hip Pitch', 'L'], left_knee: [23, '左膝', 'Left Knee', 'L'],
  left_ankle: [24, '左踝', 'Left Ankle', 'L'],
  neck_pitch: [30, '颈部俯仰', 'Neck Pitch', 'H'], head_pitch: [31, '头部俯仰', 'Head Pitch', 'H'],
  head_yaw: [32, '头部偏航', 'Head Yaw', 'H'], head_roll: [33, '头部横滚', 'Head Roll', 'H'], mouth: [34, '嘴巴', 'Mouth', 'H'],
};
const COLOR = { R: '#E5484D', L: '#2F7CF6', H: '#F5B400' };

/** 在 box（舞台元素）里画标注。duck 是已经 load 完的 Duck3D。返回一个清理函数。 */
export function drawServoLabels(duck, box) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'sv-lines');
  box.appendChild(svg);
  const tags = [];
  const ray = new THREE.Raycaster();
  const hiddenCache = {};
  let dragging = false;

  function layout() {
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H || !duck.ready) return;
    duck.scene.updateMatrixWorld(true);
    const meshes = [];
    if (!dragging) duck.content.traverse((o) => { if (o.isMesh) meshes.push(o); });
    const pts = [];
    for (const [name, [id, cn, en, side]] of Object.entries(TAGS)) {
      const j = duck.joints[name];
      if (!j) continue;
      const world = new THREE.Vector3();
      j.pivot.getWorldPosition(world);
      // 遮挡判断（射线碰 40 多万面）只在不转的时候做；转动中沿用上一次的，不然拖起来卡
      if (!dragging || !(id in hiddenCache)) {
        const dir = world.clone().sub(duck.camera.position);
        const dist = dir.length();
        ray.set(duck.camera.position, dir.normalize());
        const hit = ray.intersectObjects(meshes, false)[0];
        hiddenCache[id] = !!hit && hit.distance < dist - 0.03;
      }
      const v = world.clone().project(duck.camera);
      pts.push({ id, cn, en, side, hidden: hiddenCache[id], x: (v.x + 1) / 2 * W, y: (1 - v.y) / 2 * H });
    }
    // 右腿一列、左腿一列（哪边在屏幕左边看实际投影）；头颈按头中线分到两列；
    // 头颈在上、按高低排（线不交叉），腿按编号排（10、11、12…，好对号）
    const avg = (s) => { const xs = pts.filter((p) => p.side === s).map((p) => p.x); return xs.reduce((a, b) => a + b, 0) / (xs.length || 1); };
    const rightOnLeft = avg('R') < avg('L'), headX = avg('H');
    const cols = [[], []];
    for (const p of pts) cols[(p.side === 'H' ? p.x < headX : (p.side === 'R') === rightOnLeft) ? 0 : 1].push(p);
    const gap = Math.min(46, (H - 60) / Math.max(cols[0].length, cols[1].length, 1));
    for (const col of cols) {
      col.sort((a, b) => ((a.side === 'H') !== (b.side === 'H') ? (a.side === 'H' ? -1 : 1) : a.side === 'H' ? a.y - b.y : a.id - b.id));
      let y = 34;
      for (const p of col) { p.ty = Math.max(y, Math.min(p.y, H - 26)); y = p.ty + gap; }
      let lim = H - 26;
      for (let i = col.length - 1; i >= 0; i--) { col[i].ty = Math.min(col[i].ty, lim); lim = col[i].ty - gap; }
    }
    tags.forEach((t) => t.remove());
    tags.length = 0;
    let lines = '';
    cols.forEach((col, ci) => {
      for (const p of col) {
        const el = document.createElement('div');
        el.className = `sv-tag ${p.side}`;
        el.innerHTML = `<span class="id">${p.id}</span><span><div class="cn">${p.cn}</div><div class="en">${p.en}</div></span>`;
        box.appendChild(el);
        tags.push(el);
        const w = el.offsetWidth;
        const lx = ci === 0 ? 14 : W - 14 - w;
        el.style.left = `${lx}px`;
        el.style.top = `${p.ty - 17}px`;
        const ax = ci === 0 ? lx + w : lx, mid = ci === 0 ? ax + 24 : ax - 24;
        lines += `<polyline points="${ax},${p.ty} ${mid},${p.ty} ${p.x},${p.y}" fill="none" stroke="${COLOR[p.side]}" stroke-width="2"${p.hidden ? ' stroke-dasharray="6 5"' : ''}/>`
          + `<circle cx="${p.x}" cy="${p.y}" r="4" fill="${p.hidden ? 'none' : '#fff'}" stroke="${COLOR[p.side]}" stroke-width="2"/>`;
      }
    });
    svg.innerHTML = lines;
  }

  let raf = 0;
  const schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(layout); };
  const onStart = () => { dragging = true; };
  const onEnd = () => { dragging = false; schedule(); };
  duck.controls.addEventListener('change', schedule);
  duck.controls.addEventListener('start', onStart);
  duck.controls.addEventListener('end', onEnd);
  const ro = new ResizeObserver(schedule);
  ro.observe(box);
  schedule();
  return () => {
    cancelAnimationFrame(raf);
    duck.controls.removeEventListener('change', schedule);
    duck.controls.removeEventListener('start', onStart);
    duck.controls.removeEventListener('end', onEnd);
    ro.disconnect();
    tags.forEach((t) => t.remove());
    svg.remove();
  };
}
