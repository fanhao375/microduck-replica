#!/usr/bin/env python3
"""从 Microduck 的 MJCF 导出「已装配」的 STL。

上游仓库里的 47 个 STL 都是零件自身坐标系的，直接导进 CAD 会全部堆在原点。
本脚本读取 MJCF 的运动学树，把每个网格按 world transform 变换到正确位置，
再按刚体分组导出，得到可以直接在 CAD / 切片软件里打开的装配体。

用法:
    python scripts/export_assembly_stl.py <上游 microduck_rl 路径> [输出目录]
"""
import sys, os, struct, json
import numpy as np
import mujoco

MJCF = "src/mjlab_microduck/robot/microduck/robot_allcollisions.xml"

CN = {
    'trunk_base':'01_躯干主体','yaw2roll':'02_左髋yaw-roll','hip_l':'03_左髋roll',
    'upper_leg_left':'04_左大腿','leg':'05_左小腿','ankle_left':'06_左踝脚',
    'neck':'07_颈根','neck_pitch':'08_颈俯仰','yaw_roll_motion':'09_头yaw-roll',
    'jaw_soft':'10_头部总成','bearing_roll':'11_右髋yaw-roll','hip_l_2':'12_右髋roll',
    'upper_leg_right':'13_右大腿','leg_2':'14_右小腿','ankle_right':'15_右踝脚',
}


def write_stl(path, tris):
    """写二进制 STL。tris: (N,3,3) 顶点数组，单位 mm。"""
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(struct.pack('<I', len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            L = np.linalg.norm(n)
            n = n / L if L > 1e-12 else np.zeros(3)
            f.write(struct.pack('<3f', *n))
            for v in t:
                f.write(struct.pack('<3f', *v))
            f.write(b'\0\0')


def geom_tris(m, d, g):
    """取单个 geom 的三角面，变换到世界坐标，单位转 mm。"""
    mid = m.geom_dataid[g]
    if mid < 0:
        return None
    va, vn = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
    fa, fn = m.mesh_faceadr[mid], m.mesh_facenum[mid]
    V = m.mesh_vert[va:va + vn].reshape(-1, 3)
    F = m.mesh_face[fa:fa + fn].reshape(-1, 3)
    R = d.geom_xmat[g].reshape(3, 3)
    W = (V @ R.T) + d.geom_xpos[g]
    return W[F] * 1000.0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "cad"
    os.makedirs(out, exist_ok=True)

    path = os.path.join(root, MJCF)
    if not os.path.exists(path):
        sys.exit(f"找不到 MJCF: {path}\n请先运行 scripts/fetch_upstream.sh")

    m = mujoco.MjModel.from_xml_path(path)
    d = mujoco.MjData(m)
    d.qpos[:] = 0
    d.qpos[3] = 1.0          # 自由关节单位四元数 —— 零位基准姿态
    mujoco.mj_forward(m, d)

    names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, i) for i in range(m.nbody)]
    allt, manifest = [], {}

    for b in range(1, m.nbody):
        tris, srcs = [], []
        for g in range(m.ngeom):
            if m.geom_bodyid[g] != b or m.geom_group[g] != 2:   # group 2 = 视觉网格
                continue
            t = geom_tris(m, d, g)
            if t is None:
                continue
            tris.append(t)
            srcs.append(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, m.geom_dataid[g]))
        if not tris:
            continue
        T = np.concatenate(tris, 0)
        allt.append(T)
        name = CN.get(names[b], names[b])
        write_stl(os.path.join(out, f"{name}.stl"), T)
        manifest[name] = {'body': names[b], '源STL': srcs, '三角面': len(T)}
        print(f"  {name:22s} {len(T):7d} tris  <- {len(srcs)} 个源网格")

    A = np.concatenate(allt, 0)
    write_stl(os.path.join(out, "00_Microduck_整机装配体.stl"), A)
    bb = A.reshape(-1, 3)
    print(f"\n整机 {len(A)} 三角面")
    print(f"尺寸 (mm): {bb[:,0].ptp():.1f} x {bb[:,1].ptp():.1f} x {bb[:,2].ptp():.1f}")

    with open(os.path.join(out, "零件对照表.json"), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
