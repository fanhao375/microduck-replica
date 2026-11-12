// 在后台线程里解码 3D 模型（用户 10-18：「一刷新就卡，应该是加载模型的原因」）。
//
// 模型 31.7 万面：量化顶点还原 + 折边法线（三角面拆开成 95 万个顶点、按位置分组、30° 以内的面法线平均）
// 放在页面主线程里要卡一两秒。挪到这里，页面先出来能点，算好了把数组（transfer，不拷贝）交回去。
// 折边法线照 three.js BufferGeometryUtils.toCreasedNormals 的算法写（结果一样）；
// 这里不 import three：worker 里用不了 importmap，「three」这个裸名字解析不了。
//
// 手机也要快（用户 10-18）：
// - 算完把结果存进浏览器的 IndexedDB，下次刷新直接取（只有 model.json 变了才重算）；
// - 折边法线拆出来的 95 万个顶点里，位置和法线都相同的合并（加索引），顶点少好几倍，显存和每帧的活都少。
const DB = 'studio-model', STORE = 'decoded', VERSION = 3;

self.onmessage = async (e) => {
  try {
    const base = e.data.base;
    const text = await (await fetch(base + 'model.json')).text();
    const key = `v${VERSION}:${text.length}:${hash(text)}`;
    const cached = await idbGet(key);
    if (cached) {
      self.postMessage({ ok: true, mj: cached.mj, meshes: cached.meshes, cached: true });
      return;
    }
    const mj = JSON.parse(text);
    const bin = await (await fetch(base + 'meshes.bin')).arrayBuffer();
    const meshes = {};
    const transfer = [];
    for (const [name, m] of Object.entries(mj.meshes)) {
      const q = new Int16Array(bin, m.voff, m.nverts * 3);
      const pos = new Float32Array(m.nverts * 3);
      for (let i = 0; i < m.nverts; i++) for (let k = 0; k < 3; k++) {
        const span = m.bbox_max[k] - m.bbox_min[k] || 1;
        pos[i * 3 + k] = m.bbox_min[k] + (q[i * 3 + k] + 32768) / 65535 * span;
      }
      const idx = m.index_bytes === 2 ? new Uint16Array(bin, m.ioff, m.ntris * 3) : new Uint32Array(bin, m.ioff, m.ntris * 3);
      meshes[name] = creasedIndexed(pos, q, idx, Math.PI / 6);
    }
    await idbPut(key, { mj, meshes });            // 先存（结构化克隆），再 transfer 给页面
    for (const m of Object.values(meshes)) transfer.push(m.position.buffer, m.normal.buffer, m.index.buffer);
    self.postMessage({ ok: true, mj, meshes }, transfer);
  } catch (err) {
    self.postMessage({ ok: false, error: String(err && err.message || err) });
  }
};

// 折边法线 + 合并顶点，一趟做完（用户 10-18：第一次加载要 8 秒，太慢）。
// 每个角点的法线 = 跟它重合的所有面里、跟自己这个面夹角 30° 以内的面法线平均（three.js toCreasedNormals 的做法）。
// 「重合」按量化坐标完全相同算（three 按 1 厘米的格子算，一格几千个面挨个比，慢）；
// 位置和法线都相同的角点合并成一个顶点，加索引——棱角处法线不同，照样分开。
function creasedIndexed(pos, q, idx, creaseAngle) {
  const creaseDot = Math.cos(creaseAngle);
  const nTri = idx.length / 3;
  const faceN = new Float32Array(nTri * 3);
  for (let t = 0; t < nTri; t++) {
    const a = idx[t * 3] * 3, b = idx[t * 3 + 1] * 3, c = idx[t * 3 + 2] * 3;
    const cbx = pos[c] - pos[b], cby = pos[c + 1] - pos[b + 1], cbz = pos[c + 2] - pos[b + 2];
    const abx = pos[a] - pos[b], aby = pos[a + 1] - pos[b + 1], abz = pos[a + 2] - pos[b + 2];
    let x = cby * abz - cbz * aby, y = cbz * abx - cbx * abz, z = cbx * aby - cby * abx;
    const len = Math.hypot(x, y, z) || 1;
    faceN[t * 3] = x / len; faceN[t * 3 + 1] = y / len; faceN[t * 3 + 2] = z / len;
  }
  // 重合的位置 → 用到它的面（量化坐标拼成一个数当键：65536³ < 2⁵³，不会撞）
  const posKey = (v) => (q[v * 3] + 32768) * 4294967296 + (q[v * 3 + 1] + 32768) * 65536 + (q[v * 3 + 2] + 32768);
  const groups = new Map();
  for (let t = 0; t < nTri; t++) {
    for (let k = 0; k < 3; k++) {
      const key = posKey(idx[t * 3 + k]);
      const g = groups.get(key);
      if (g) g.push(t); else groups.set(key, [t]);
    }
  }
  const P = [], N = [], index = new Uint32Array(idx.length);
  const seen = new Map();                       // 位置键 → [[nx,ny,nz,新下标], ...]（一般就一两个）
  let u = 0;
  for (let t = 0; t < nTri; t++) {
    const fx = faceN[t * 3], fy = faceN[t * 3 + 1], fz = faceN[t * 3 + 2];
    for (let k = 0; k < 3; k++) {
      const v = idx[t * 3 + k], key = posKey(v);
      let x = 0, y = 0, z = 0;
      for (const g of groups.get(key)) {
        const gx = faceN[g * 3], gy = faceN[g * 3 + 1], gz = faceN[g * 3 + 2];
        if (fx * gx + fy * gy + fz * gz > creaseDot) { x += gx; y += gy; z += gz; }
      }
      const len = Math.hypot(x, y, z) || 1;
      x /= len; y /= len; z /= len;
      let list = seen.get(key), j = -1;
      if (list) {
        for (const e of list) if (Math.abs(e[0] - x) < 1e-3 && Math.abs(e[1] - y) < 1e-3 && Math.abs(e[2] - z) < 1e-3) { j = e[3]; break; }
      } else { list = []; seen.set(key, list); }
      if (j < 0) {
        j = u++;
        list.push([x, y, z, j]);
        P.push(pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]);
        N.push(x, y, z);
      }
      index[t * 3 + k] = j;
    }
  }
  return { position: new Float32Array(P), normal: new Float32Array(N), index: u < 65536 ? new Uint16Array(index) : index };
}

function hash(t) {
  let h = 2166136261;
  for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16);
}

// IndexedDB 存取：隐私模式、存储被禁时取不到也存不进，照样能用（只是每次都算）
function idbOpen() {
  return new Promise((resolve) => {
    try {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}
async function idbGet(key) {
  const db = await idbOpen();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const q = db.transaction(STORE).objectStore(STORE).get(key);
      q.onsuccess = () => resolve(q.result || null);
      q.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}
async function idbPut(key, value) {
  const db = await idbOpen();
  if (!db) return;
  await new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, 'readwrite');
      const st = tx.objectStore(STORE);
      st.clear();                                   // 只留一份，旧版本的删掉
      st.put(value, key);
      tx.oncomplete = tx.onerror = tx.onabort = () => resolve();
    } catch { resolve(); }
  });
}
