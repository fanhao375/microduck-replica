// 动作广场的目录。首页和详情页都从这里读，改完刷新页面就生效。
//
// 加一个动作：
//   1. 在 motions/<id>/ 里放 poster.webp（封面，560×560）和 preview.mp4（预览）；有实拍小图就再放一张 real.jpg
//   2. 在下面 motions 里加一项
//   3. 跑一次 python tools/make_data.py，详情页就有分解图（见 README.md）
// 一个动作练成了、从「还在路上的」搬上来：把它从 plan 里删掉，在 motions 里加一项。
window.PLAZA = {
  // GitHub 仓库地址。首页的 GitHub、「去 GitHub 投稿」按钮用它
  repo: 'https://github.com/fanhao375/microduck-replica',
  // 「看训练流程」按钮去哪
  trainUrl: 'https://github.com/fanhao375/microduck-replica/tree/master/software/training-studio',

  // 来源（筛选栏按这个分）
  sources: { builtin: '内置', community: '群友投稿', open: '开源仓库' },

  // status.kind：solid = 黄底（已经能用）；dashed = 虚线（还没验证完）
  motions: [
    {
      id: 'roulade', name: '前滚翻', source: 'builtin', real: true,
      desc: '往前翻一整圈，再自己站起来。按住可以连着翻。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '1.0 s', bg: '#FFD3A6',
      pills: ['1.0 s', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '手柄 X 键（左边那颗）。按住可以连着翻。' }
      ]
    },
    {
      id: 'idle_chest', name: '抖一抖', source: 'community',
      desc: '蹲一下、抬头、抖抖身子。站着没事时的小动作。',
      long: '蹲一下、抬头、抖抖身子，再站直。作者那只鸭子站着没事时，每隔 15 秒就做一个这样的小动作。',
      status: { text: '作者实机在用', kind: 'solid' }, meta: '3.1 s', bg: '#FFC21F',
      pills: ['3.1 s', '50 Hz', '带记忆的模型'],
      keys: [0, 0.62, 1.22, 1.84, 2.44, 3.06, 3.66, 4.25],
      checks: [
        { s: 'ok', t: '站姿零位和我们一样（髋 −0.458，踝 0.453）。' },
        { s: 'ok', t: '14 个关节，顺序和方向都一样。' },
        { s: 'ok', t: '输入 61 个数、输出 14 个数；主程序认这种带记忆的模型。' },
        { s: 'half', t: '嘴巴不在模型里。想让它张嘴要另外定时，不弄也能用。' },
        { s: 'todo', t: '在我们的机器上还没实测。第一次请把鸭子架起来试。' }
      ],
      files: [
        { name: 'policy.onnx', what: '模型本体', size: '1.58 MB' },
        { name: 'interface.json', what: '关节顺序、站姿、时长', size: '2 KB' },
        { name: 'skill.toml', what: '时长 3.1 s，不连播', size: '1 KB' },
        { name: '使用说明.txt', what: '作者写的说明', size: '2 KB' }
      ],
      filesTotal: '≈ 1.6 MB',
      downloadNote: '文件还没放上来',
      retrain: '这个包里只有导出的模型，没有训练存档，所以不能接着它练。想做类似的动作，用仓库里的训练流程新开一个任务；练好以后在仿真里跑一遍，就能生成和这页一样的预览。'
    },
    {
      id: 'ground_pick', name: '捡东西', source: 'builtin',
      desc: '弯腰低头够到地面，再直起来。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '2.8 s', bg: '#FFD3A6',
      pills: ['2.8 s', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '手柄 A 键（下面那颗）。' }
      ]
    },
    {
      id: 'kick_left', name: '踢左脚', source: 'builtin',
      desc: '左脚往前踢一下。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '0.5 s', bg: '#FFC21F',
      pills: ['0.5 s', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '手柄 LB 键。' }
      ]
    },
    {
      id: 'kick_right', name: '踢右脚', source: 'builtin',
      desc: '右脚往前踢一下。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '0.5 s', bg: '#ECE9E1',
      pills: ['0.5 s', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '手柄 RB 键。' }
      ]
    },
    {
      id: 'sitstand', name: '坐下 / 站起', short: '坐下', source: 'builtin',
      desc: '慢慢坐到地上，脚底朝前；再按一下，自己站起来。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '按一下切换', bg: '#FFC21F',
      pills: ['按一下切换', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '手柄十字键下。按一下坐，再按一下站。' }
      ]
    },
    {
      id: 'walk', name: '走路', source: 'builtin', real: true,
      desc: '摇杆管前后左右和转弯，松手原地站住。',
      status: { text: '出厂自带', kind: 'solid' }, meta: '一直走', bg: '#FFD3A6',
      pills: ['一直走', '50 Hz'],
      checks: [
        { s: 'ok', t: '镜像里已经带了，不用装。' },
        { s: 'ok', t: '按 Start 站起来，再按 Start 开始走；左摇杆走，右摇杆转。' }
      ]
    },
    {
      id: 'crouch', name: '深蹲', source: 'open',
      desc: '两只脚踩着地蹲到底，再按一下自己站起来。来自 Ley-WL 的开源模型。',
      long: '两只脚一直踩着地，腿收起来蹲到底，身体从 11.6 cm 降到 6 cm 左右；指令一松，自己站回去。',
      status: { text: '还没上真机', kind: 'dashed' }, meta: '按一下切换', bg: '#FFD3A6',
      pills: ['按一下切换', '50 Hz', '按 823 g 训练'],
      checks: [
        { s: 'ok', t: '输入 61 个数、输出 14 个数，站姿和关节顺序跟我们一样。' },
        { s: 'ok', t: '在我们的仿真里能蹲下去、能站回来，全程不倒。' },
        { s: 'half', t: '是按 823 g 的身体训的。仿真里把舵机调软到七成，它会歪。' },
        { s: 'todo', t: '作者和我们都还没上过真机。第一次请把鸭子架起来试。' },
        { s: 'half', t: '包里带了 skill.toml（蹲 3 秒、再用 1.5 秒站回来、幅度 1）。Studio 加上「更多动作」以后能直接导入，那一版还没发布。' }
      ],
      files: [
        { name: 'policy.onnx', what: '模型本体', size: '774 KB' },
        { name: 'skill.toml', what: '装到鸭子上用的参数：动作名、时长、指令、幅度', size: '1 KB' },
        { name: 'README.md', what: '作者写的说明和评估', size: '2 KB' },
        { name: 'target.json', what: '蹲到底时 14 个关节的目标角度', size: '1 KB' },
        { name: 'results.json', what: '作者的仿真评估结果', size: '5 KB' },
        { name: 'LICENSE', what: 'Apache-2.0 许可全文', size: '11 KB' },
        { name: '来源.txt', what: '从哪下的、哪个提交', size: '1 KB' }
      ],
      filesTotal: '≈ 0.8 MB',
      download: { href: 'motions/crouch/crouch-leywl.zip', label: '下载模型文件 · 0.7 MB' },
      credit: {
        author: 'Ley-WL', license: 'Apache-2.0',
        url: 'https://github.com/Ley-WL/microduck-rl/tree/4a4a34a5ab0bbef5b9229e7759abdac8030a149d/models/crouch_hd1910_823g_3000',
        text: 'Ley-WL/microduck-rl · models/crouch_hd1910_823g_3000 · 提交 4a4a34a5'
      },
      retrain: '作者把训练存档和训练方法都放出来了，可以接着它练：换成自己鸭子的重量再训几千轮，或者改蹲多深。练好以后在仿真里跑一遍，就能生成和这页一样的预览。',
      retrainLink: { href: 'https://github.com/Ley-WL/microduck-rl', label: '去作者的仓库' }
    }
  ],

  // 还在路上的。stage：idea = 想好了还没训；training = 训练中；await = 等上真机
  // from：self = 自己训；open = 开源仓库；community = 群友
  plan: [
    { stage: 'training', name: '劈叉', id: 'straddle', note: '训过一版，腿还没劈下来，接着调。', from: 'self' },
    { stage: 'training', name: '金鸡独立', id: 'flamingo', note: '开源仓库有一版，只抬右脚；作者说真机上会软掉，要接着训。', from: 'open' },
    { stage: 'await', name: '倒地起身', id: 'standup', note: '开源仓库有一版，作者的仿真里每次都能自己站起来，真机还没试。', from: 'open' }
  ]
};
