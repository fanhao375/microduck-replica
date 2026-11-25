# Microduck 动作广场

[返回文档导航](../README.md) · [软件总览](../../software/README.md)

复刻版（飞特 HD-1910）的动作库网站：看预览、挑动作、点进去看细节。纯静态页面，没有构建步骤，也不依赖后端。

**在线看：<https://fanhao375.github.io/microduck-replica/motion-plaza/>**

**现在是预览版。** 能看、能筛选、能下载已经放出来的模型文件；在 Studio 里一键把动作装到鸭子上的功能还没做，页面上有标注。

## 怎么看

- 打开上面的在线地址；
- 把仓库拿到本地，直接双击 `index.html`；
- 或者双击 `本地预览.bat`，浏览器会打开 <http://127.0.0.1:8780/>（关掉那个黑窗口就停）。

三种方式效果一样。本目录在 `docs/` 下面，是因为这个仓库的 GitHub Pages 只发布 `docs/`，放在这里推上去就能在线看。

> 如果滚动字条不动、首屏的鸭子不自己转头、详情页的视频不自动播：那是系统里关了「动画效果」，页面按系统的意思保持静止。  
> Windows 在 设置 → 辅助功能 → 视觉效果 → 动画效果 里打开。鼠标放到卡片上播放预览不受这个影响。

## 目录

| 位置 | 是什么 |
|---|---|
| `index.html` | 首页 |
| `motion.html` | 详情页，地址后面带 `?id=动作名`，比如 `motion.html?id=crouch` |
| `data/motions.js` | **动作目录**。首页的卡片、数字、筛选、「还在路上的」，详情页的文字，都从这里来 |
| `data/curves.js` | 详情页的关节曲线和分解图的时间点，`tools/make_data.py` 生成，不要手改 |
| `motions/<动作名>/` | 每个动作的封面 `poster.webp`、预览 `preview.mp4`、分解图 `keys/`，有的还有实拍 `real.jpg` 和可下载的文件 |
| `assets/` | 样式、脚本、首屏鸭子和连拍的图 |
| `tools/make_data.py` | 从预览视频截分解图、从仿真轨迹算关节曲线 |

## 加一个动作

1. 准备封面 `poster.webp`（560×560）和预览 `preview.mp4`，放进 `motions/<动作名>/`。预览要么是模型在仿真里真跑出来的，要么是实拍，不要手摆关键帧。
2. 在 `data/motions.js` 的 `motions` 里照着现有的加一项。刷新页面就有了，数字和筛选会自己跟着变。
3. 跑一次 `python tools/make_data.py`，详情页就有「八个瞬间」。
4. 想要关节曲线：在 `tools/make_data.py` 的 `CLIP` 里加上这个动作对应轨迹的起止帧，再带 `--traj <放轨迹的目录>` 跑一次。轨迹格式写在脚本开头。

卡片上的两个标签要如实写：来源是内置、群友投稿还是开源仓库；验证到了哪一步（只有仿真、作者实机、我们实机验过）。没上过真机的动作不要写成能用。

## 「还在路上的」

在 `data/motions.js` 的 `plan` 里，一行一个。`stage` 三选一：`idea`（想好了还没训）、`training`（训练中）、`await`（等上真机）。练成一个，就把它从 `plan` 里删掉、加进 `motions`。

## 现在的边界

- 「怎么装」那一段写的两种装法（Studio 里点「装」、手机传进去）还没做出来，页面上标了「Studio 里还在做」。功能做好以后把那行标记删掉（`index.html` 里搜 `class="soon"`）。
- 抖一抖只有预览，模型文件没放上来。
- 深蹲的模型作者和我们都没上过真机。它的下载包带了 `skill.toml`，等 Studio 带上「更多动作」以后能直接导入，那一版还没发布。
- 预览用的是简化舵机的仿真回放，只能看出动作长什么样，不等于实机效果。
- 字体用的是 Google Fonts。打不开的地方会退回系统自带的黑体，能看，但大标题没那么粗。

## 素材和模型的来历

| 内容 | 来历 | 许可 |
|---|---|---|
| 网页代码（`index.html`、`motion.html`、`assets/*.css`、`assets/*.js`、`data/`、`tools/`） | 本项目原创 | Apache-2.0 |
| 预览图和预览视频（`assets/img/`、各动作的 `poster.webp`、`preview.mp4`、`keys/`） | 用 Studio 网页里的那只 3D 鸭子渲染的仿真回放；鸭子的外形来自上游公开的仿真模型 | CC BY-NC-SA 4.0 |
| 实拍小图（`motions/roulade/real.jpg`、`motions/walk/real.jpg`） | 本项目参与者拍摄 | CC BY-NC-SA 4.0 |
| 深蹲模型（`motions/crouch/files/` 和 `crouch-leywl.zip`） | 原样取自 [Ley-WL/microduck-rl](https://github.com/Ley-WL/microduck-rl) 的 `models/crouch_hd1910_823g_3000`，提交 `4a4a34a5`；`policy.onnx` 的 SHA-256 与作者公布的一致 | Apache-2.0，许可全文和来源记录在同目录 |

内置的 6 个动作（走路、坐下 / 站起、捡东西、前滚翻、左右踢）是复刻镜像里自带的模型，这里只放了它们的仿真预览，没有放模型文件。它们的作者和许可见[镜像里的模型说明](../../software/image/overlay/usr/share/doc/microduck/模型说明.txt)。
