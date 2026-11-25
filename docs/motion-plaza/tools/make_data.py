# 给详情页生成两样数据，写进 data/curves.js：
#   1. 「八个瞬间」：从每个动作的 preview.mp4 里等间隔截 8 张，放到 motions/<动作名>/keys/
#   2. 关节曲线：从仿真轨迹里挑动得最多的 5 个关节，画的是相对站姿转了多少度
#
#   python tools/make_data.py                  只重做分解图，曲线保持原样
#   python tools/make_data.py --traj <目录>    连曲线一起重算；目录里放 <动作名>.json（仿真轨迹）
#
# 需要 Python 3.9+、Pillow，以及 PATH 里有 ffmpeg 和 ffprobe。
#
# 仿真轨迹的格式（50 帧/秒）：
#   {"joints": [14 个关节名], "home": [14 个站姿角度（弧度）], "skill": [动作起始帧, 结束帧],
#    "frames": [[x, y, z, qw, qx, qy, qz, 14 个关节角（弧度）], ...]}
import argparse, json, os, re, subprocess, tempfile

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(SITE, 'data', 'curves.js')

# 预览视频对应轨迹里的哪一段帧。新加的动作要出曲线，就在这里加一行。
CLIP = {'roulade': (55, 234), 'idle_chest': (55, 269), 'ground_pick': (55, 264), 'kick_left': (55, 118),
        'kick_right': (55, 118), 'walk': (100, 224), 'crouch': (55, 300), 'sitstand': (55, 294)}
# 这些动作有专门渲染的透明底分解图（竖图），时间点写在 data/motions.js 的 keys 里，不从视频里截
KEYS_OWN = {'idle_chest'}
JOINT_CN = {'left_hip_yaw': '左髋转', 'left_hip_roll': '左髋侧摆', 'left_hip_pitch': '左髋前后', 'left_knee': '左膝', 'left_ankle': '左踝',
            'neck_pitch': '脖子前后', 'head_pitch': '抬头低头', 'head_yaw': '转头', 'head_roll': '歪头',
            'right_hip_yaw': '右髋转', 'right_hip_roll': '右髋侧摆', 'right_hip_pitch': '右髋前后', 'right_knee': '右膝', 'right_ankle': '右踝'}


def motions():
    d = os.path.join(SITE, 'motions')
    return sorted(m for m in os.listdir(d) if os.path.isfile(os.path.join(d, m, 'preview.mp4')))


def old(name):
    """读回 data/curves.js 里现有的那份，没给轨迹时曲线原样保留。"""
    if not os.path.isfile(OUT): return {}
    m = re.search(r'window\.' + name + r' = (\{.*?\});\n', open(OUT, encoding='utf-8').read(), re.S)
    return json.loads(m.group(1)) if m else {}


def keys():
    from PIL import Image
    out = {}
    for m in motions():
        if m in KEYS_OWN: continue
        clip = os.path.join(SITE, 'motions', m, 'preview.mp4')
        dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', clip],
                                   capture_output=True, text=True, check=True).stdout)
        ts = [round(k * (dur - 0.06) / 7, 2) for k in range(8)]
        d = os.path.join(SITE, 'motions', m, 'keys'); os.makedirs(d, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            for k, t in enumerate(ts):
                png = os.path.join(tmp, '%d.png' % k)
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '%.2f' % t, '-i', clip, '-frames:v', '1', png], check=True)
                Image.open(png).convert('RGB').resize((280, 280), Image.LANCZOS).save(os.path.join(d, '%d.webp' % k), quality=84, method=6)
        out[m] = ts
    return out


def curves(traj_dir):
    out = old('PLAZA_CURVES')
    for m, (i0, i1) in CLIP.items():
        p = os.path.join(traj_dir, m + '.json')
        if not os.path.isfile(p):
            print('  没有 %s 的轨迹，曲线保持原样' % m); continue
        t = json.load(open(p, encoding='utf-8'))
        F = t['frames'][i0:i1 + 1]; J = t['joints']; home = t['home']; n = len(F)
        deg = [[(F[k][7 + j] - home[j]) * 57.29578 for k in range(n)] for j in range(len(J))]
        top = sorted(range(len(J)), key=lambda j: max(deg[j]) - min(deg[j]), reverse=True)[:5]
        N = 108; pick = [round(k * (n - 1) / (N - 1)) for k in range(N)]
        s0, s1 = t.get('skill', [0, 0]); band = None
        if s1 > s0 and s0 < i1 and s1 > i0:
            band = [round(max(0, s0 - i0) / (n - 1), 4), round(min(n - 1, s1 - i0) / (n - 1), 4)]
        out[m] = {'dur': round(n / 50, 2), 'band': band,
                  'joints': [{'label': JOINT_CN[J[j]], 'min': round(min(deg[j])), 'max': round(max(deg[j])),
                              'pts': [round(deg[j][k], 1) for k in pick]} for j in top]}
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='生成详情页的分解图和关节曲线数据')
    ap.add_argument('--traj', help='放仿真轨迹 <动作名>.json 的目录；不给就不动曲线')
    a = ap.parse_args()
    c = curves(a.traj) if a.traj else old('PLAZA_CURVES')
    k = keys()
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('// 详情页的关节曲线和分解图时间点。由 tools/make_data.py 生成，不要手改。\n')
        f.write('window.PLAZA_CURVES = ' + dump(c) + ';\n')
        f.write('window.PLAZA_KEYS = ' + dump(k) + ';\n')
    print('曲线：', ', '.join(c)); print('分解图：', ', '.join(k))
