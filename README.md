# Microduck 复刻

**简体中文** · [English](README.en.md)

> 对 [Pollen Robotics Microduck](https://pollen-robotics.com/microduck/) 的第三方复刻研究。
> 从官方公开的 MJCF 仿真模型反推出**装配图、爆炸图和可直接导入 CAD 的装配体**。

Microduck 是一只 25cm 高、737g 的双足机器鸭，15 个 Dynamixel XL330 舵机（14 个受策略控制），用强化学习走路。
它的**软件开源（Apache-2.0）**。硬件是**部分开源**：

- ✅ **RPI Robot HAT 板完整开源** —— 见 [`pollen-robotics/elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)（Apache-2.0）：
  KiCad 9 工程、Gerber、BOM、贴片坐标、STEP 一应俱全。这块板**不需要自己画**。
- ❌ **`imu_to_dxl` 板未开源** —— 全网无公开工程，本仓库的逆向是目前唯一公开重建。
- ❌ **机械件无可编辑 CAD**、无 BOM、无装配文档。

> 📌 **勘误（2026-09-03）**：本文档此前写作「硬件不开源、没有 PCB 原理图」，**这是错的**。
> 当时只检索了 `pollen-robotics/microduck` 主仓，未翻查该组织下以 `elec_` 开头的硬件仓库。

但官方在 `microduck_rl` 里发布了完整的 **MJCF 仿真模型 + 47 个 STL 网格**。
MJCF 里包含了完整的运动学树：每个零件挂在谁身上、相对位置精确到 0.1mm、
绕哪根轴转、行程多少、质量和惯量张量。

**这些信息足以还原出装配关系。** 本仓库就是把它还原出来的结果。

---

## 两条路线，选一条走

本仓库同时覆盖两种舵机。**机械、电路、软件三层都有差别**，动手前先定：

| | 原版 · Dynamixel XL330 | 飞特 · HD-1910 |
|---|---|---|
| **舵机** | XL330-M288-T ×15 | HD-1910-C001 ×15 —— 便宜一半、力矩 2.5 倍 |
| **电压** | 额定 6 V，实跑 6.6–8.2 V，**超压 37%** | 额定 4–8.4 V，在额定内；但满电 8.4 V 顶格 |
| **打印件** | [`print/`](print/) 上游 STL 直接打 | **8 个配合件要改**（HD-1910 舵盘凸、XL330 凹）—— [拓竹一键打印](https://makerworld.com.cn/zh/models/2963569-microduck#profileId-3478428) / [3mf](https://github.com/fanhao375/microduck-replica-cad/tree/master/打印) |
| **可编辑图纸** | [图纸仓 v1.1](https://github.com/fanhao375/microduck-replica-cad/releases/tag/v1.1) | [图纸仓 v2.0](https://github.com/fanhao375/microduck-replica-cad/releases/tag/v2.0)，文件带 `-FT` 后缀 |
| **电路** | 官方 HAT + [`imu_to_dxl`](hardware/imu_to_dxl/) | 同左；舵机连接器 2.0 mm（官方 2.5），`imu_to_dxl` 板上 J4/J5 是 2.0 |
| **软件** | 官方运行时直接跑 | 总线协议不同，要换协议模块 —— [适配架构分析](software/飞特适配架构.md)；策略要按 HD-1910 重训 —— [训练前数据清单](docs/HD-1910训练前数据清单.md) |
| **状态** | 纸上分析 + 首批打印件 | **整机装出实物**（2026-09-13），**15 颗上总线、能站起来坐下**（2026-09-18），零位和站姿还在调 |

选型论证在 [执行器选型](docs/执行器选型.md)。本仓库主线走飞特；原版资料同样齐全，两条都能复刻。

---

## 交流群

有人在做同样的事，凑了个微信群一起讨论复刻进度、踩过的坑、元件采购。

下面是**鸭子复刻群 9**的二维码（微信群满 200 人后无法扫码加入）。

<div align="center">
  <img src="assets/wechat-group-9.png" alt="鸭子复刻群 9 微信二维码" width="280">
  <br>
  <sub><b>鸭子复刻群 9 · 二维码有效期到 2026-10-01</b>（微信群码 7 天自动失效）<br>
  过期了请开个 <a href="https://github.com/fanhao375/microduck-replica/issues">issue</a> 说一声，我会换上新的</sub>
</div>

## 最近更新

**IMU 姿态接入调试台**：[`tools/servo-web/`](tools/servo-web/) 可选接入 J-Link，在同一个 3D 鸭子上显示舵机关节角与 IMU 躯干姿态。配套固件在 [`hardware/imu_to_dxl/firmware/`](hardware/imu_to_dxl/firmware/)，**0.2.0 已改成飞特协议**（ID 200、地址 56、15 字节），电脑上验收全过，等整机上真总线实测；没有 J-Link 也能用 ST-Link / DAPLink（`--imu-swd`）或直接走舵机总线（`--imu-bus`）看姿态。


<table>
<tr>
<td width="50%" valign="top">

### 🔌 电气 · 15 颗舵机整机上电，站起来了（2026-09-18）

<p align="center"><a href="assets/首次上电-2026-09-18.mp4"><img src="assets/首次上电-站起来.gif" alt="第一次上电：从缩着站起来（点开看 30 秒视频）" width="220"></a></p>

装好的鸭子第一次接总线：URT-2 走 USB、7.4 V 进 V1，**15 颗 HD-1910 全部在线，0 超时 0 校验错**，一次同步读 15 颗 4.8 ms。
用仓库里新写的[网页调试台](tools/servo-web/)拖滑块、存姿态、跑序列，3D 模型跟着真机动，重心投影实时显示在不在脚底范围。
上面这段是它从缩着**自己站起来**再坐下。**飞特路线的软件第一步走通了**，硬件方案没问题。

`imu_to_dxl` 板首板已到：3.3 V 正常，J4/J5 接口封装要改，固件还没写 —— [设计说明与评审记录](hardware/imu_to_dxl/)。

**[网页调试台](tools/servo-web/)**　·　
**[调试记录](调试记录.md)**（零位、方向、站姿的坑）　·　
[踩坑记录](踩坑记录.md#工具)（URT-2 先插 USB 再上电）　·　
[飞特适配架构](software/飞特适配架构.md)

</td>
<td width="50%" valign="top">

### 🔨 机械 · 飞特 HD-1910 版装出来了

<a href="构建日志.md"><img src="build-log/photos/2026-09-13-飞特版装机-正面.jpg" alt="飞特 HD-1910 版装机实物"></a>

**15 颗飞特 HD-1910 全部配好 ID、装进机身。** 这不是官方的 XL330 —— 便宜一半、力矩大 2.5 倍、
电压额定匹配，是[执行器选型](docs/执行器选型.md)里论证的那条路，现在有实物了。

**HD-1910 的舵盘是凸的，XL330 是凹的**，跟舵盘配合的 8 个件同事全改了、重新打印。
可编辑的 SolidWorks 图纸也出了飞特版（`-FT` 后缀），改了什么、为什么，
在图纸仓里写清楚了。

**[构建日志](构建日志.md)**　·
**[飞特版 SolidWorks 图纸](https://github.com/fanhao375/microduck-replica-cad)**　·
**[拓竹一键打印](https://makerworld.com.cn/zh/models/2963569-microduck#profileId-3478428)**　·
**[BOM 清单 · 带采购链接](https://github.com/fanhao375/microduck-replica-cad#装配-bom)**　·
[调试记录](调试记录.md)　·
[打印件清单](print/)
</td>
</tr>
<tr>
<td width="50%" valign="top">

### 💻 软件 · 网页调试台，串口直连 15 颗舵机

<p align="center"><a href="tools/servo-web/"><img src="assets/servo-web.png" alt="网页调试台：滑块、3D 鸭子、重心投影" width="420"></a></p>

浏览器拖滑块让舵机动，3D 鸭子跟着转，重心投影实时算在不在脚底范围。
存姿态、跑序列、一键校准零位、导出全部寄存器、时序测试，装机和台架验收都用它。

后端不依赖飞特 SDK，[`feetech.py`](tools/servo-web/feetech.py) 按 2026 版协议手册直接收发包，一百多行。
配套一个**协议级舵机模拟器**，不接硬件就能把校准、改 ID、写寄存器跑一遍自检。

接官方运行时的 `FeetechIo`（`RobotIo` 接缝，rustypot 协议 v1）已经写完、跟上了官方 0.14.1，等零位校准做完上板 —— 代码在 fork 的 **[`feetech` 分支](https://github.com/fanhao375/microduck/tree/feetech)**。

**[网页调试台](tools/servo-web/)**　·
**[飞特适配架构](software/飞特适配架构.md)**　·
**[FeetechIo 代码](https://github.com/fanhao375/microduck/tree/feetech)**　·　
[飞特官方资料](docs/飞特资料/)（协议、内存表）

</td>
<td width="50%" valign="top">

### 🧠 算法 · 接入公开的 1910 M6 参数

采用 **[LuwuDynamics/xgoduck_rl](https://github.com/LuwuDynamics/xgoduck_rl) 公开的 1910 BAM M6 动力学参数**，
作为飞特版的仿真训练基线。感谢 LuwuDynamics 的分享；这份参数不是我们自行辨识的结果。
出处、固定版本、原文件校验值和 Apache-2.0 许可见[参数来源说明](docs/HD-1910-M6参数来源.md)。

训练沿用 [Pollen Robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) 和
[Rhoban/BAM](https://github.com/Rhoban/bam)。当前推进参数接入和本地仿真检查，尚无本机实测通过的走路策略。
实机测试仍需完成**零位、关节方向、IMU 和主控接入**，并核对本机质量惯量与舵机响应。

**[HD-1910 训练前数据清单](docs/HD-1910训练前数据清单.md)**　·
[执行器选型](docs/执行器选型.md)

</td>
</tr>
</table>

---

## 🔨 实物进度

> 本仓库反复强调「仿真 STL 不是可打印工程件」。**构建日志就是在验证这句话。**
> 结论会如实记录，无论正反。

**→ [构建日志](构建日志.md)**（机械）　·　**[调试记录](调试记录.md)**（电气与软件）　·　**[踩坑记录](踩坑记录.md)**

---

## 🦆 鸭友贡献

这个仓库不是一个人写的。合并进来的代码、实测验证过的结论，都记在这儿。

| 谁 | 贡献了什么 |
|---|---|
| [@yoyojacky](https://github.com/yoyojacky) | **Rust 版飞特舵机测试工具**（[PR #27](https://github.com/fanhao375/microduck-replica/pull/27)，已合并）。自己实现的最小 STS/SCS 协议（帧打包、校验和、ping、读写寄存器、状态解析），命令行可以扫描总线（带进度条）、读状态、转到指定位置、单颗测试和批量测试，输出位置偏差、峰值电流、峰值负载、电压温度并判定 PASS/FAIL。台架验收一条命令出汇总表 → [`tools/sts3215Servo_testtool/`](tools/sts3215Servo_testtool/) |
| [@Abo1ish](https://github.com/Abo1ish) | **IMU 小板固件 + 调试台姿态显示**（[PR #32](https://github.com/fanhao375/microduck-replica/pull/32)，已合并）。给官方没开源的那块 imu_to_dxl 写了 STM32G031 固件底子，在实板上验证过，带主机端测试和一份写清楚边界的 [`VALIDATION.md`](hardware/imu_to_dxl/firmware/VALIDATION.md) → [`hardware/imu_to_dxl/firmware/`](hardware/imu_to_dxl/firmware/)；再用 J-Link 把 IMU 姿态实时读出来并进了网页调试台，3D 鸭子的躯干跟着真板子转（`--imu-jlink`，没板子也能用 `--imu-demo` 先看效果）→ [`tools/servo-web/`](tools/servo-web/) |
| 微信群的鸭友 | **Radxa Zero 3W V1.12J 的启动方案**。这批板子（WiFi 换成 AIC8800DS2）光换瑞莎引导还是起不来，他实测出设备树也得换成瑞莎 B1 的：**B1 引导 + B1 DTB + Armbian 6.1.115 内核 + Trixie 系统**，跑通了 → 写进了[踩坑记录](踩坑记录.md#软件)和[镜像使用说明](tools/radxa/镜像使用说明.md#6-如果你的板子不是这一批) |

### 鸭友的项目

有些鸭友是自己单独开仓库做的，不在这个仓库里，但跟鸭子直接相关、做得好的，放这儿推一下：

| 项目 | 作者 | 做什么的 |
|---|---|---|
| [microduck-color-studio](https://github.com/LathamZ/microduck-color-studio)（[在线试用](https://lathamz.github.io/microduck-color-studio/)） | [@LathamZ](https://github.com/LathamZ) | **鸭子的 3D 配色工作室，打印前先在网页里把颜色定好。** 70 个零件逐件上色，选材质（PLA / 哑光 / PETG / 金属 / 碳纤 / TPU）和灯光看效果；填上自己手里有哪些耗材，它按库存推荐配色（只用已有 / 补一色 / 丙烯点缀）。还能导入自己的 3MF（比如飞特版），导出**带颜色的多盘 3MF** 和按件拆好的 STL，拓竹 P1S / A1 mini / H2D 有预设。中英文界面，手机上也能看 |
| [MICDUCK_FTHD1901_REBUILD](https://github.com/fengj4780-sudo/MICDUCK_FTHD1901_REBUILD) | [@fengj4780-sudo](https://github.com/fengj4780-sudo) | **飞特版结构改进 + 可选 CNC 加强件。** 在我们的[可编辑 SolidWorks 图纸](https://github.com/fanhao375/microduck-replica-cad)基础上接着改：适配飞特舵机（他仓库里写作 HD1901，零件文件用的是 HD-1910-C001），加强了薄弱件，修了装配干涉和几个破损的模型。腿部 **5 个零件可以换成 CNC 件**让受力更合理（还在打样测试，他估算 5 件含运费约 45 元）；不做 CNC 直接打印也行，强度也比原版好。附一键打印的 3MF（拓竹 H2D、0.4 mm、5 盘）和改好的 SolidWorks 图纸 |

做了跟鸭子有关的项目（工具、教程、改装、策略……），开 issue 贴个链接，我们加进来。

想加进来：直接开 [issue](https://github.com/fanhao375/microduck-replica/issues) 或提 PR。
硬件、固件、算法、文档、实测数据都算 —— **实测数据尤其欢迎**，这个仓库的规矩是结论如实记录，无论正反。

---

## 装配爆炸图

![爆炸图](assembly-drawings/06_爆炸图_四分之三.png)

`assembly-drawings/` 下共 7 张：

| 文件 | 内容 |
|---|---|
| `01_正面` `02_侧面` `03_背面` `04_四分之三` | 装配完成态，原色 |
| **`05_爆炸图_侧面`** | 15 个部件沿运动链炸开，标注名称与质量 |
| **`06_爆炸图_四分之三`** | 立体视角，可看清左右腿镜像关系 |
| `07_分色对照_装配态` | 与爆炸图同配色的装配完成态，对照用 |

## 装配结构

```
躯干主体 199g
├─ 左髋 yaw→roll 23g → 左髋 roll 6g → 左大腿 48g → 左小腿 22g → 左踝+脚 30g
├─ 颈根 37g → 颈俯仰件 6g → 头 yaw/roll 机构 49g → 头部总成+喙 189g
└─ 右髋 yaw→roll 23g → 右髋 roll 6g → 右大腿 48g → 右小腿 22g → 右踝+脚 30g

整机 737.2g   外形 144 × 141 × 264 mm
```

躯干和头几乎一样重（199g vs 189g）——**头部占整机四分之一，重心很高**，
这解释了为什么它的行走策略难训。

## 关节参数

每条腿 5 个自由度，头颈 4 个 —— **14 个受控**。
整机实际装 **15 个 Dynamixel XL330**：第 15 个驱动喙/下颚，走被动连杆，不进动作空间。

| 关节 | 行程 |
|---|---|
| `hip_yaw` | -25° ~ +30° |
| `hip_roll` | ±22° |
| `hip_pitch` / `knee` / `ankle` / `head_pitch` | ±90° |
| `neck_pitch` | -90° ~ +60° |
| `head_yaw` | ±170° |
| `head_roll` | ±25° |

## 📋 物料清单（BOM）

**要买什么、买几个** —— [`BOM.md`](BOM.md)

整机 15 个舵机、14 个轴承、约 325 件紧固件、2 块要打样的电路板，数量取自上游 MJCF 的
引用计数（38 种网格 / 75 个实例），不是估的。含每块板的器件清单与立创编号。

> ⚠️ 里面有两条会让人买错的更正：**电池是 NP-F550 不是 F970**、**XL330 被超压运行**。

## 3D 打印件

整机全部 STL 单件，按「要打印 / 买现成的」分好类，中英双语命名 —— [`print/`](print/)

| 目录 | 数量 |
|---|---|
| 🖨️ [**拓竹 MakerWorld · microduck**](https://makerworld.com.cn/zh/models/2963569-microduck#profileId-3478428) | **不想看图直接打** —— 一键切片，Bambu 打印机直接开 |
| [`print/打印件/`](print/打印件/) | **30 种 / 41 件**结构件 |
| [`print/标准件-无需打印/`](print/标准件-无需打印/) | **9 个**外购件模型（对位用） |

> 上游的 XL330 台架测试夹具在另一个目录，本来就不属于机器人。打印建议与数量表见 [`print/README.md`](print/README.md)，采购见 [机械采购清单](docs/机械采购清单.md)。

## CAD 装配体

`cad/` 下是**已应用世界变换**的 STL —— 直接导入 CAD 就是装好的样子，
不用自己摆位置（上游那 47 个 STL 都是零件自身坐标系的，直接导入会全部堆在原点）。

- `00_Microduck_整机装配体.stl` —— 整机单文件，796792 三角面
- `01` ~ `15` —— 按刚体分组的 15 个部件，文件名即部件名
- `零件对照表.json` —— 每个部件由哪些上游源网格组成

单位 **毫米**。可直接用 FreeCAD / Fusion 360 / SolidWorks / Blender / 各类切片软件打开。

### 📐 要可编辑的参数模型？在另一个仓库

本仓库的 `cad/` 与 `print/` 都是**网格（STL）**—— 能打印、能看、能测量，但**改不动**。

**[fanhao375/microduck-replica-cad](https://github.com/fanhao375/microduck-replica-cad)**
是配套的三维图纸仓库，放的是**可编辑的 SolidWorks 源文件**（📦 **压缩包在那个仓库的 Releases 页，不在文件列表里**）：
16 个装配体 + 40 个零件，外加一份 **21 页的装配安装说明书**（含每个组件的步骤、配图与注意事项）。

> 那套图纸由 **[机械行者Robo](https://github.com/fanhao375/microduck-replica-cad#图纸作者机械行者Robo)**
> 建模并编写说明书 —— 想改尺寸、改壁厚、重新出工程图的，从那边拿源文件。

---

## 从运行时反推出的电控方案

<div align="center">
  <img src="assets/hw/01-物理布局.png" alt="Microduck 电控总览：板子都装在哪" width="880">
  <br>
  <sub><b>五个模块的物理布局</b> —— 灰色虚线框是物理区域，实线框是模块，红色虚线框表示装在壳体<b>外面</b>。<br>
  <b>橙色</b>是舵机总线（自上而下），<b>红色</b>是电池供电（自下而上）。<br>
  最容易搞错的一条：<b>主控、HAT、摄像头三者都在头里</b>，摄像头距主控板中心约 13 mm 且中间没有关节 ——<br>
  所以 MIPI 排线不穿过脖子，真正过颈的是舵机总线和供电线。<br>
  <a href="docs/硬件入门.md">完整图集与讲解 →</a>　·　<a href="assets/hw/Microduck硬件图集.pdf">下载 PDF 图集（7 张，A3）</a></sub>
</div>


**一条 1 Mbps 的 TTL 串行总线搞定一切。**

```
                Radxa Zero 3W (RK3566) · Armbian
                  ├── UART2  1 Mbps TTL 单线半双工 ── 15× XL330 + imu_to_dxl (ID 200)
                  ├── I2C3   400 kHz (pin 3/5) ────── AIC3104@0x18 · ToF@0x29 · BMI088（未用）
                  ├── I2S3   12.288 MHz ───────────── 音频
                  ├── MIPI CSI ────────────────────── IMX219（I2C@0x10，转 90°）
                  ├── 蓝牙 ────────────────────────── 手柄 / 手机 App
                  ├── Wi-Fi ───────────────────────── WebRTC
                  └── USB-C ───────────────────────── 供电 + maskrom
```

| | |
|---|---|
| **主控** | **Radxa Zero 3W** —— 市售模块，**不是定制载板** |
| SoC | RK3566，四核 Cortex-A55，Mali-G52，0.8 TOPS NPU。官方公布 1 GB / 32 GB eMMC；复刻建议 2G/16G，见 [电控采购清单](docs/电控采购清单.md) |
| **舵机总线** | **单线半双工 TTL** —— **不是** RS-232，**也不是** RS-485。Dynamixel Protocol V2 @ 1 Mbps，走 `/dev/ttyS2` |
| **自制板 1** | **`imu_to_dxl` v2** —— 一颗会说 Dynamixel 的 LSM6DSV16X：总线 ID 200、寄存器 124，12 字节块在**同一次** `sync_read` 里和舵机一起读回 |
| **自制板 2** | **RPI Robot HAT** —— TLV320AIC3104 @ 0x18、一颗休眠的 BMI088、接 ToF 的 Stemma 座。**官方已开源**（[`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)） |
| 电池 | 索尼 NP-F550，2S 锂电。**没有电量计，也没有 ADC** —— 电池电压是**读舵机上报的自身供电电压**得来的 |
| 传感器 | LSM6DSV16X IMU · VL53L5CX/L8CX 8×8 ToF · IMX219（树莓派 Camera v2） |

`imu_to_dxl` 是整套设计里最漂亮的一手：**IMU 不走 I²C**，而是把自己伪装成一个 Dynamixel 从机，
于是姿态数据和关节状态在同一次总线事务里回来 —— 不需要第二条总线，主机也不用跑姿态融合
（LSM6DSV16X 的片上 SFLP 块直接输出游戏旋转四元数，还自己估计陀螺零偏）。

**完整细节：** [硬件方案逆向](docs/硬件方案逆向.md) · [硬件规格速查](docs/硬件规格速查.md)

---

## 可行性：能造出来吗

**整机可复刻，卡点在成本不在技术。** 现状：

| 项目 | 状态 |
|---|---|
| 零件几何 | ✅ 47 个 STL |
| 装配关系 | ✅ 精确到 0.1mm，已出图 |
| 关节轴线 / 行程 | ✅ 14 个全有 |
| 质量 / 惯量 | ✅ 15 个刚体全有 |
| 舵机型号 | ✅ Dynamixel XL330 ×15 → [docs/执行器选型.md](docs/执行器选型.md) |
| 轴承 | ✅ Ø22×16×4 与 Ø15×10×3 |
| **HAT 板** | ✅ **官方 KiCad + Gerber + BOM 已开源**，直接打样 → [`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT) |
| **`imu_to_dxl` 板** | ⚠️ 未开源，需自绘；协议与寄存器布局已完整还原 → [docs/硬件方案逆向.md](docs/硬件方案逆向.md) |
| 主控 | ✅ **Radxa Zero 3W，市售模块**（此前误判为定制载板） |
| **紧固件清单** | ✅ 已从 STL 孔位反推 → [docs/紧固件反推.md](docs/紧固件反推.md) |
| 电池 / 传感器 | ✅ **NP-F550** 2S、IMX219、VL53L8CX、LSM6DSV16X |
| **走线方案** | ❌ 无 |
| **控制软件** | ✅ 主控用同款 Radxa Zero 3W 即可直接跑（Apache-2.0）；换主控才需移植 |
| **官方策略 ONNX** | ✅ 硬件保持同款即可直接用（9 个）；改动本体或电控才需重训 |

⚠️ **仿真 STL ≠ 可打印工程件。** 仿真只关心外形与惯量，不保证配合公差、
螺纹孔、热熔螺母座和走线空间。直接打印大概率装不起来，需要自行补充工程细节。

💰 **自己造大概率比买贵，但差多少全看渠道。** 15 个 XL330 在 ROBOTIS 国际站约 **$359**、
美国站 **$412**、欧洲含税 **€603–629** —— 也就是从「略低于整机售价」到「远超」都有可能。
加上主控、电池、两块板打样和打印耗材必然更贵。完整核价见 [BOM.md](BOM.md)。

## 现实路径

放弃 100% 复刻（`imu_to_dxl` 板与可编辑机械 CAD 未开源），改成**机械照抄 + 电控自建**：

| | 方案 |
|---|---|
| 机械 | 用本仓库的 STL 与装配图，几何完全照抄 |
| 舵机 | XL330 × 15，市售件照买 |
| 主控 | **Radxa Zero 3W**，市售模块，与官方同款 |
| IMU 板 | 自己画 `imu_to_dxl`：LSM6DSV16X + MCU + 半双工收发器，协议已还原 |
| HAT 板 | **官方 Gerber 直接打样**（4 层板）。不要录音可整块省略，但**半双工方向电路要另配转接板**，见 [电控采购清单](docs/电控采购清单.md#六两块-pcb) |
| 软件 | 主控同款则官方 Rust 运行时可直接跑（Apache-2.0） |
| 策略 | 官方 9 个 ONNX 可用；改硬件后用 [microduck_rl](https://github.com/pollen-robotics/microduck_rl) 重训 |

**结论已从「机械可抄、电控是硬墙」修正为「整机可复刻」** ——
主控是市售模块，自制板的功能和协议已从源码完整还原。
详见 [docs/硬件方案逆向.md](docs/硬件方案逆向.md)。

---

## 三个必踩的坑

1. **Armbian 默认在 UART2 上跑登录控制台。** `serial-getty@ttyS2` 占着串口，
   必须 `systemctl mask` 掉。Pollen 是用 `fuser -v /dev/ttyS2` 查出来的。
2. **i2c3 和 FUSB302 抢引脚。** 用排针 pin 3/5 的硬件 I²C，代价是**失去 USB-C PD 协商**
   （普通 5V 充电仍然可用）。
3. **NPU 出厂是关的。** Armbian 默认禁用，要跑 RKNN 模型得刷 overlay 并重启。

---

## 生态导航

Microduck 的东西散在 GitHub 多个组织和 HuggingFace 三种资源里，**官方硬件仓库尤其容易漏**。
[`docs/生态导航.md`](docs/生态导航.md) 把官方仓库、模拟器、策略模型、数据集、社区项目
逐个标注了「是什么、能拿来干嘛」。

## 深入文档

| 文档 | 内容 |
|---|---|
| [紧固件反推](docs/紧固件反推.md)（[English](docs/fastener-reconstruction.en.md)） | 从 47 个 STL 扫描孔特征，反推出 M2 螺丝系统与采购量 |
| [执行器选型](docs/执行器选型.md)（[English](docs/actuator-selection.en.md)） | XL330 参数、BAM M6 配置、5 组标定 PD、回差建模；**为什么不能换闭环步进、换 STS3215 会怎样**（737g vs 2107g 实测对照）、**同级别舵机横向对比**（含宇树 S288 深度评估） |
| [**踩坑记录**](踩坑记录.md) | **上手前扫一遍** —— 买东西 / 打板 / 飞线 / 软件 / 工具，每条三句：现象、原因、怎么办 |
| [**调试记录**](调试记录.md) | **拿到货一步一步做** —— 舵机配 ID 改哪些参数（含 ID 对照表）、怎么供电，后面按日期记过程 |
| [**HD-1910 训练前数据清单**](docs/HD-1910训练前数据清单.md) | **给算法重训用的** —— 几何、质量惯量、关节标定、舵机规格、IMU 安装，仓库有的全填了并标出处，台架实测项列了怎么测 |
| [**不打 HAT**](docs/不打HAT.md) | **HAT 打样太贵？** ¥70 的降压模块 + 半双工转接板飞线替代，一步一步接，哪步会烧东西 |
| [**硬件入门**](docs/硬件入门.md) | **一块板一块板讲清楚** —— 五个模块各自干嘛、一个 tick 里信号怎么流、走飞特路线哪些会变，末尾附「复刻前必做的五件核对」 |
| [**硬件规格速查**](docs/硬件规格速查.md) | **一页纸规格表** —— 系统框图、芯片型号、总线参数、复刻清单、必踩的坑 |
| [硬件方案逆向](docs/硬件方案逆向.md) | 完整推导过程与证据出处（[English](docs/hardware-teardown.en.md)） |
| [**电控采购清单**](docs/电控采购清单.md) | **淘宝实链** —— 主控 / 摄像头 / ToF / 电源 / 两块 PCB / 线材与调试板，含选型推算与避坑（2026-09-04 快照） |
| [**机械采购清单**](docs/机械采购清单.md) | **淘宝实链** —— 轴承 / M2 紧固件 / 热熔螺母与压头 / 螺纹胶 / 耗材 |
| [社区动态](docs/社区动态.md) | X / GitHub 情报，含外部验证、噪音与风险提示 |

结论速览：**整机是 M2 螺丝系统**（Ø2.2 过孔 ×77 + Ø4.4 沉头孔 ×28 + Ø1.6 攻丝底孔 ×20），
结构件过孔约 146 个；轴承 Ø22×16×4 与 Ø15×10×3。

---

## 重现

```bash
# 1. 拉上游仓库（源码与仿真资产不重复托管，打印件除外 —— 见 NOTICE.md）
bash scripts/fetch_upstream.sh

# 2. 重新生成装配图
python scripts/render_assembly.py upstream/microduck_rl assembly-drawings

# 3. 重新导出 CAD 装配体
python scripts/export_assembly_stl.py upstream/microduck_rl cad

# 4. 重新扫描孔特征
python scripts/analyze_holes.py upstream/microduck_rl/src/mjlab_microduck/robot/microduck/assets
```

依赖：`mujoco` `numpy` `pillow` `scipy`。渲染需要可用的 OpenGL 上下文。

---

## 许可证

- `scripts/` —— Apache-2.0
- `assembly-drawings/` `cad/` —— **CC BY-SA-NC 4.0**（上游 3D 模型为 CC BY-SA-NC，
  依 ShareAlike 条款衍生作品须沿用同协议，**不得商用**）

详见 [NOTICE.md](NOTICE.md)。本项目与 Pollen Robotics 无隶属关系，未获其背书。
