#!/usr/bin/env python3
"""从 STL 网格反推孔特征，用于还原紧固件清单。

思路：CAD 导出的网格里，一个孔就是一片圆柱面。
  1. 焊接顶点，建面片邻接图
  2. 按二面角切割（锐边处断开），region-grow 出「光滑曲面片」
  3. 每片拟合圆柱：法向都垂直于轴 -> 轴 = 法向协方差最小特征向量
  4. 投影到垂直轴的平面上拟合圆 -> 直径
  5. 法向朝向轴心 = 孔；背离轴心 = 轴/凸台

用法:
    python scripts/analyze_holes.py <assets 目录> [输出 json]
"""
import sys, os, struct, json, glob
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

SMOOTH_DEG   = 35.0     # 二面角阈值：小于此角认为同属一个光滑曲面
AXIS_TOL     = 0.25     # |法向·轴| 上限，超过说明不是圆柱面
FIT_TOL      = 0.03     # 圆拟合相对残差上限
MIN_FACES    = 6        # 圆柱面至少要有这么多三角面
MIN_DIA, MAX_DIA = 0.8, 14.0   # 只关心紧固件量级的孔 (mm)


def read_stl_mm(path):
    """读二进制 STL，返回 (N,3,3) 顶点数组，单位 mm。"""
    b = open(path, 'rb').read()
    n = struct.unpack('<I', b[80:84])[0]
    a = np.frombuffer(b, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
    f = a[:, :48].copy().view('<f4').reshape(n, 12)
    return f[:, 3:12].reshape(n, 3, 3).astype(np.float64) * 1000.0


def weld(tris):
    """合并重复顶点，返回 (顶点表, 面索引)。"""
    P = tris.reshape(-1, 3)
    key = np.round(P, 4)
    _, idx, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return P[idx], inv.reshape(-1, 3)


def face_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    L = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(L < 1e-12, 1, L)


def smooth_patches(V, F, N):
    """按二面角切割，把面片聚成光滑曲面片。"""
    nf = len(F)
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    e = np.sort(e, axis=1)
    fid = np.tile(np.arange(nf), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, fid = e[order], fid[order]
    same = np.all(e[1:] == e[:-1], axis=1)          # 相邻两条记录是同一条边
    f1, f2 = fid[:-1][same], fid[1:][same]
    if len(f1) == 0:
        return np.zeros(nf, int), 1
    keep = np.einsum('ij,ij->i', N[f1], N[f2]) > np.cos(np.radians(SMOOTH_DEG))
    f1, f2 = f1[keep], f2[keep]
    g = coo_matrix((np.ones(len(f1)), (f1, f2)), shape=(nf, nf))
    return connected_components(g, directed=False)[1], connected_components(g, directed=False)[0]


def fit_circle(xy):
    """Kasa 代数圆拟合，返回 (圆心, 半径, 相对残差)。"""
    x, y = xy[:, 0], xy[:, 1]
    A = np.c_[x, y, np.ones(len(x))]
    b = x ** 2 + y ** 2
    try:
        s, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None, None, 9e9
    cx, cy = s[0] / 2, s[1] / 2
    r2 = s[2] + cx ** 2 + cy ** 2
    if r2 <= 0:
        return None, None, 9e9
    r = np.sqrt(r2)
    resid = np.abs(np.hypot(x - cx, y - cy) - r).mean()
    return np.array([cx, cy]), r, resid / max(r, 1e-9)


def analyze(path):
    tris = read_stl_mm(path)
    V, F = weld(tris)
    N = face_normals(V, F)
    lab, k = smooth_patches(V, F, N)
    holes = []
    for p in range(k):
        sel = np.where(lab == p)[0]
        if len(sel) < MIN_FACES:
            continue
        n = N[sel]
        # 圆柱面的法向都垂直于轴 -> 轴是法向协方差矩阵的最小特征向量
        w, vec = np.linalg.eigh(n.T @ n)
        axis = vec[:, 0]
        if np.abs(n @ axis).max() > AXIS_TOL:
            continue
        pts = V[F[sel]].reshape(-1, 3)
        e1 = np.cross(axis, [1, 0, 0])
        if np.linalg.norm(e1) < 1e-6:
            e1 = np.cross(axis, [0, 1, 0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(axis, e1)
        xy = np.c_[pts @ e1, pts @ e2]
        c, r, res = fit_circle(xy)
        if r is None or res > FIT_TOL:
            continue
        dia = 2 * r
        if not (MIN_DIA <= dia <= MAX_DIA):
            continue
        # 判断凹凸：法向指向轴心 = 孔
        cen3 = c[0] * e1 + c[1] * e2
        fc = V[F[sel]].mean(1)
        radial = fc - (cen3 + np.outer(fc @ axis, axis))
        radial /= np.maximum(np.linalg.norm(radial, axis=1, keepdims=True), 1e-9)
        inward = (np.einsum('ij,ij->i', n, radial) < 0).mean()
        if inward < 0.7:
            continue                                  # 凸出去的是轴/凸台，不是孔
        ang = np.arctan2(xy[:, 1] - c[1], xy[:, 0] - c[0])
        cover = np.degrees(np.ptp(np.sort(ang)))
        depth = float(np.ptp(pts @ axis))
        holes.append({'直径mm': round(float(dia), 3),
                      '深度mm': round(depth, 2),
                      '包角deg': round(float(cover)),
                      '面数': int(len(sel))})
    return holes


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "upstream/microduck_rl/src/mjlab_microduck/robot/microduck/assets"
    out = sys.argv[2] if len(sys.argv) > 2 else "docs/hole_analysis.json"
    files = sorted(glob.glob(os.path.join(src, "*.stl")))
    if not files:
        sys.exit("没找到 STL: " + src)
    result = {}
    for f in files:
        name = os.path.basename(f)
        try:
            h = analyze(f)
        except Exception as ex:
            print(f"  !! {name}: {type(ex).__name__} {ex}")
            continue
        result[name] = h
        if h:
            print(f"  {name:42s} {len(h):3d} 个孔")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(result, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print("\n写入", out)


if __name__ == '__main__':
    main()
