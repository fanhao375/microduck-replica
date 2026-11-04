# Microduck 复刻

**简体中文** · [English](README.en.md)

基于 [Pollen Robotics Microduck](https://pollen-robotics.com/microduck/) 的第三方整机复刻项目，提供从采购、打印、装配到标定、控制和训练的资料。

**20261104 测试版：**[新镜像](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-20261104) · [配套软件](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.11.4) · [完整更新清单](docs/更新说明-20261104.md)。整合站姿 IMU 重标、ID 200 独立方向核验、热点恢复，以及手柄映射等此前软件功能。新镜像烧卡和运动效果待现场验收；固定安装方向预设仍留待后续。

**飞特 HD-1910 版本已完成整机实机验证：站立、行走、前滚翻后起身均已跑通。** 主线使用 **15 颗飞特 HD-1910 舵机**，提供可烧录的**复刻镜像**和 **Microduck Studio 网页中控**，通过手机或电脑浏览器完成配网、标定和遥控。

**首次上手流程已在实机跑通：** 烧录镜像 → 热点配网 → 舵机与 IMU 标定 → 配手柄、测试急停 → 站立行走。[查看实机验证记录](docs/历史更新.md#最近更新)

## 鸭子现在的样子

**2026-10-24 · 实机运动验证通过**：下方两段实拍视频分别展示前滚翻后起身和桌面行走。

<table>
<tr>
<th align="center">前滚翻与起身</th>
<th align="center">桌面行走</th>
</tr>
<tr>
<td align="center" valign="middle"><a href="assets/demos/2026-10-24-roll-and-rise.mp4"><img src="assets/demos/2026-10-24-roll-and-rise.gif" alt="实机完成前滚翻后重新站起" width="260"></a></td>
<td align="center" valign="middle"><a href="assets/demos/2026-10-24-walking.mp4"><img src="assets/demos/2026-10-24-walking.gif" alt="飞特版鸭子在桌面上迈步行走" width="400"></a></td>
</tr>
<tr>
<td align="center"><a href="assets/demos/2026-10-24-roll-and-rise.mp4">完整 MP4 · 约 8 秒</a></td>
<td align="center"><a href="assets/demos/2026-10-24-walking.mp4">完整 MP4 · 约 6 秒</a></td>
</tr>
</table>

动图为无声预览，MP4 保留完整画面和声音。

**[下载镜像](https://github.com/fanhao375/microduck-replica/releases/latest)** · **[首次使用指南（PDF / 网页）](docs/上手指南/README.md)** · **[CAD 与打印件](https://github.com/fanhao375/microduck-replica-cad)** · **[装配教程](#-装配视频教程)** · **[BOM 采购清单](BOM/README.md)** · **[硬件工程](hardware/README.md)**

---

### 适用版本

- **飞特 HD-1910 + Radxa Zero 3W**：使用本仓复刻镜像和 Studio 网页中控。
- **Dynamixel XL330**：提供对应 CAD 与装配资料，需使用匹配的运行软件。

[选择打印件与采购清单](#打印件与采购清单) · [查看舵机详细对比](docs/执行器选型.md)

---

## 交流群

有人在做同样的事，凑了个微信群一起讨论复刻进度、踩过的坑、元件采购。

下面是**鸭子复刻群 14**的二维码（微信群满 200 人后无法扫码加入）。

<div align="center">
  <img src="assets/wechat-group-14.png" alt="鸭子复刻群 14 微信二维码" width="280">
  <br>
  <sub><b>鸭子复刻群 14 · 二维码在 2026-11-07 前有效</b>（微信群码 7 天自动失效）<br>
  过期了请开个 <a href="https://github.com/fanhao375/microduck-replica/issues">issue</a> 说一声，我会换上新的</sub>
</div>

---

## 📺 装配视频教程

**[B 站合集《MicroDuck 从零安装教程》](https://space.bilibili.com/312747174/lists/9279084?type=season)** —— CAD 图纸和装配 BOM 的作者 **机械行者Robo** 录的，从零件清单讲起，一集装一个部件。共 8 集，截至 2026-10-12 已更新 6 集：

分集目录右侧是作者账号，依次为 **B 站、小红书、抖音**，可扫码或点击图片进入。

<table>
<thead>
<tr><th>集</th><th>内容</th><th nowrap>时长</th><th>B 站</th><th>小红书</th><th>抖音</th></tr>
</thead>
<tbody>
<tr>
<td nowrap><a href="https://www.bilibili.com/video/BV1RoHX68EFV/">第 1 集</a></td><td width="260"><a href="https://www.bilibili.com/video/BV1RoHX68EFV/">零件与配件清单</a></td><td nowrap>1:32</td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://space.bilibili.com/312747174"><img src="assets/social/bilibili-qr.jpg" alt="B 站：机械行者Robo" width="200"></a><br><sub><b>机械行者Robo</b><br>UID 312747174</sub></td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://www.xiaohongshu.com/user/profile/5ad950114eacab046016f038"><img src="assets/social/xiaohongshu-qr.jpg" alt="小红书：机械行者" width="200"></a><br><sub><b>机械行者</b><br>小红书号 270594280</sub></td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://www.douyin.com/user/MS4wLjABAAAArLa_6yE3GE-AoRURoQnIHdbbI7yM-GHhzlgLZ-XbeYE"><img src="assets/social/douyin-qr.jpg" alt="抖音：机械行者Robo" width="200"></a><br><sub><b>机械行者Robo</b><br>抖音号 1852366168</sub></td>
</tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1Xppb6tECr/">第 2 集</a></td><td><a href="https://www.bilibili.com/video/BV1Xppb6tECr/">小腿安装</a></td><td nowrap>2:21</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1ycpb6yEwB/">第 3 集</a></td><td><a href="https://www.bilibili.com/video/BV1ycpb6yEwB/">大腿安装</a></td><td nowrap>5:13</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1Kwpg6wE9G/">第 4 集</a></td><td><a href="https://www.bilibili.com/video/BV1Kwpg6wE9G/">躯干组件安装</a></td><td nowrap>2:29</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1EEpg6nEkD/">第 5 集</a></td><td><a href="https://www.bilibili.com/video/BV1EEpg6nEkD/">颈部安装</a></td><td nowrap>0:40</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1aKpE6hEzj/">第 6 集</a></td><td><a href="https://www.bilibili.com/video/BV1aKpE6hEzj/">头部安装</a></td><td nowrap>5:50</td></tr>
</tbody>
</table>

### 打印件与采购清单

先按舵机版本选择打印资料，再准备对应的外购件。

| 资料 | 适用版本 | 入口 |
|---|---|---|
| **打印件与 CAD** | **飞特 HD-1910** | [进入飞特版专区](https://github.com/fanhao375/microduck-replica-cad#飞特-hd-1910-打印资料)：打印工程、CAD 源文件和更新说明 |
| **打印件与 CAD** | **Dynamixel XL330** | [进入 XL330 版专区](https://github.com/fanhao375/microduck-replica-cad#dynamixel-xl330-打印资料)：对应版本的 CAD 源文件与打印说明 |
| **装配采购清单** | **飞特 HD-1910** | **[在线预览](BOM/README.md)** · **[下载 Excel](BOM/装配BOM-机械行者Robo-2026-10-23-带采购链接.xlsx)**：机械行者Robo整理，2026-10-23 版，含数量、材料和采购链接 |

两种舵机的配合件不能混用。打印文件的具体版本和更新日期在 CAD 仓库对应专区维护；这份采购清单按飞特方案整理，XL330 的舵机及配套电控需另行核对。

---

## 现在做到哪了

截至 **2026-10-25**，**飞特版整机复刻、首次上手与实机运动链路已跑通**。下面介绍各部分成果与后续拓展项目，具体测试范围及历史过程见[更新记录](docs/历史更新.md)。

---

## 💻 软件 · 复刻镜像与 Microduck Studio

**2026-10-31 修订 · 2026.10.3101：嘴巴34／偏航32图示修正**：修复3D模型两颗舵机外壳配反及旧模型缓存，点选、高亮和编号引线保持一致，不互换实物ID。已有27版更新入口可直接安装，31版也可升级；配套原生程序沿用31版，不重烧镜像。[软件修订包](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.3101) · [更新步骤与群14资料](docs/软件修订-20261031-r1.md)

**2026-10-31 · 软件包更新：手柄重新映射、摄像头原地放大与 SSH 指引**：已有27版更新入口的设备安装软件包即可，不重烧镜像。手柄学习按键、摇杆与扳机后保存配置；驱动需按页面提示单独确认安装，不能只更新网页。原27版升级器、Linux 映射与服务权限检查已通过，新手柄驱动的实物安装和各型号手柄仍待验收。[软件包与首次引导附件](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.31) · [操作步骤与验证范围](docs/功能软件升级-20261030.md)

**2026-10-30 · 软件包更新：只接 IMU 也能独立诊断**：总线 IMU 不再被舵机启动检查挡住。没有或只接部分舵机时，总览与 IMU 页仍显示板卡应答、识别与样本状态；过期、断线及切换配置不会沿用旧结果。诊断不放行运动或标定写入。先升级网页，再明确确认主程序更新至 `.47` / API 42。[软件包与引导附件](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.30) · [更新步骤与使用说明](docs/仅接IMU的独立诊断与软件更新-20261030.md)

**2026-10-29 · 软件包更新：IMU 与舵机状态分开显示**：主程序等待 IMU 时，总览和舵机页继续显示已收到的舵机回包、原始位置、电压与温度，并区分缺失、未知和过期数据。先升级网页，再明确确认主程序更新至 `.46`，已有卡通过软件更新安装。[软件包与引导附件](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.29) · [更新步骤与验收范围](docs/软件更新与独立设备状态说明-20261029.md)

**2026-10-28 · 软件包更新（不重烧 SD 卡）**：已有设置页升级入口的用户先安装 `2026.10.28` 网页 / 语音包，重连后可承托卸力并明确确认主程序更新，再在 IMU 页选择 `Qinmo · Feetech`、验证方向。主程序自动更新仅支持已知 20261027 基线；现有板卡继续默认自动识别。Qinmo 实物与本版整机升级仍待验收，音乐跟拍和自由 AI 人声对话仍未解决。[软件包与引导附件](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.28) · [现有用户更新步骤](docs/软件更新与Qinmo使用说明-20261028.md) · [Qinmo 网页使用](docs/Qinmo-IMU-网页使用.md)

**复刻镜像 20261027：网页软件升级与本地坐站语音。** 设置页可检查、下载或上传签名软件包，安装失败可回退；R3 单击开关监听，识别“坐下／站起来”，附带中文模型，无需云端账号。保留网页录制回放、灵动及模型管理。已有卡先通过 SSH 安装一次升级入口，之后日常软件更新不用重烧。

本版保留 20261023c 的主程序、步态模型与总线 ID 200 IMU 自动识别。软件、签名事务、浏览器及镜像只读验证通过；本版新卡首次使用、旧卡升级/回退和真人声学仍待实物验收。音乐头身跟拍仍待修复，自由 AI 人声对话尚未接入。[镜像与校验值](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-20261027) · [软件升级包](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.27) · [更新与语音用法](docs/软件更新与语音使用说明-20261027.md) · [验证范围](docs/superpowers/verification/2026-10-27-web-updates.md)

<p align="center"><a href="software/studio/README.md"><img src="assets/studio-overview-20261025-demo.jpg" alt="Microduck Studio 当前界面演示，设备读数为模拟数据" width="920"></a><br><sub>Studio 界面演示，设备读数为模拟数据</sub></p>

**[下载镜像](https://github.com/fanhao375/microduck-replica/releases/latest)** · **[首次使用指南](docs/上手指南/README.md)** · **[Studio 说明](software/studio/README.md)**

镜像免费，仅供个人非商业复刻；各部分许可见发布页。摄像头默认测试源，真实摄像头和 ToF 的验证范围见本版说明。

---

## 🔧 机械 · 一站式打印与装配

**机械行者Robo持续维护飞特 HD-1910 与 Dynamixel XL330 两套资料。** 模型、打印工程和更新说明集中在 CAD 仓库，按自己的舵机版本选择。

| 需要什么 | 入口 |
|---|---|
| **一站式打印** | **[拓竹 MakerWorld（飞特版）](https://makerworld.com.cn/zh/models/2963569-microduck#profileId-3478428)** · [Bambu Studio 3MF 工程](https://github.com/fanhao375/microduck-replica-cad#打印) |
| **模型与打印资料** | **[飞特 HD-1910 专区](https://github.com/fanhao375/microduck-replica-cad#飞特-hd-1910-打印资料)** · **[XL330 专区](https://github.com/fanhao375/microduck-replica-cad#dynamixel-xl330-打印资料)** |
| **装配教程** | **[装配说明书 PDF](https://github.com/fanhao375/microduck-replica-cad/blob/master/安装说明书/microduck装配安装说明书.pdf)** · **[分集视频](#-装配视频教程)**；两版按对应结构件装配 |
| **采购清单** | **[BOM 在线预览](BOM/README.md)** · **[下载 Excel](BOM/装配BOM-机械行者Robo-2026-10-23-带采购链接.xlsx)**：2026-10-23 飞特版，XL330 的舵机及配套电控需另行核对 |

[CAD 仓库与最新发布](https://github.com/fanhao375/microduck-replica-cad/releases) · [构建记录](构建日志.md)

---

## 🔌 电气 · 五块电路板

本仓提供总线 IMU 板、舵机总线 HUB 和电池接口板的工程资料；主控采用瑞莎 **Radxa ZERO 3W**，扩展板使用 Pollen Robotics 官方开源的 **RPI Robot HAT**。

<table>
<tr>
<th>总线 IMU 板</th>
<th>舵机总线 HUB</th>
<th>电池接口板</th>
<th>瑞莎主控<br>Radxa ZERO 3W</th>
<th>官方 HAT<br>RPI Robot HAT</th>
</tr>
<tr>
<td align="center" valign="middle" width="170"><a href="assets/hw/imu-to-dxl-1v0a-3d.png"><img src="assets/hw/imu-to-dxl-1v0a-3d.png" alt="总线 IMU 板 3D 设计预览" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/wire-hub-1v0a-3d.png"><img src="assets/hw/wire-hub-1v0a-3d.png" alt="舵机总线 HUB 3D 设计预览" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/pogo-pin-1v0a-3d.png"><img src="assets/hw/pogo-pin-1v0a-3d.png" alt="电池接口板 3D 设计预览" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/radxa-zero3w-official.webp"><img src="assets/hw/radxa-zero3w-official.webp" alt="瑞莎官方 ZERO 3W 板卡图" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/rpi-robot-hat-official.png"><img src="assets/hw/rpi-robot-hat-official.png" alt="Pollen Robotics 官方 RPI Robot HAT 设计图" width="160"></a></td>
</tr>
<tr>
<td align="center" valign="top"><a href="assets/hw/imu-to-dxl-1v0a-pcb.png">PCB 预览</a><br><a href="hardware/新版/imu_to_dxl_1V0A/">工程文件</a></td>
<td align="center" valign="top"><a href="assets/hw/wire-hub-1v0a-pcb.png">PCB 预览</a><br><a href="hardware/新版/wire_hub_1V0A/">工程文件</a></td>
<td align="center" valign="top"><a href="assets/hw/pogo-pin-1v0a-pcb.png">PCB 预览</a><br><a href="hardware/新版/POGO_PIN_1V0A/">工程文件</a></td>
<td align="center" valign="top"><a href="https://item.taobao.com/item.htm?id=763812025816">购买（淘宝）</a><br><a href="https://docs.radxa.com/en/zero/zero3">官方资料</a> · <a href="https://docs.radxa.com/en/zero/zero3/download#hardware-design">设计文件</a></td>
<td align="center" valign="top"><a href="https://github.com/pollen-robotics/elec_RPI_Robot_HAT">KiCad 工程</a><br><a href="https://github.com/pollen-robotics/elec_RPI_Robot_HAT/tree/main/production">生产打样文件</a></td>
</tr>
</table>

前三张为自制板的 3D 设计预览，后两张瑞莎与 HAT 配图来自官方资料。点击图片可查看原图，[图片来源](assets/hw/official-board-images.md)。瑞莎设计资料含原理图、2D 尺寸与 3D 模型；三块自制板的嘉立创 EDA 工程、STEP 和实测进度见各工程说明。

[全部硬件工程](hardware/README.md) · [三块板的说明](hardware/新版/README.md) · [总线 IMU 固件](hardware/imu_to_dxl/README.md)

---

## 🧠 算法 · 飞特官方 BAM

[**飞特官方 HD-1910 BAM**](https://gitee.com/ftservo/ftservo-bam) 提供舵机动力学模型与辨识参数，供仿真训练参考。本仓提供开源[训练工程](software/training/README.md)与[网页训练工作台](software/training-studio/README.md)，具体模型配置与参数来源见[参数说明](docs/HD-1910-M6参数来源.md)。

---

## 🖥️ 开源训练平台 · Microduck Training Studio

我们还做了 **Microduck Training Studio**：在电脑上通过网页创建训练实验、查看曲线、暂停与继续、评估检查点并导出 ONNX 模型。训练工作台与鸭子上的 Studio 是两个独立服务。

<p align="center"><a href="software/training-studio/README.md"><img src="assets/training-studio-20261025-demo.jpg" alt="Microduck Training Studio 实际界面：三维模型、训练控制与合成演示曲线" width="920"></a><br><sub>训练平台演示界面：3D 为模型预览，曲线为合成演示数据</sub></p>

已完成真实 GPU 短试跑、评估回放和模型导出的链路验证；长训练与自训策略的真机验证仍待完成。

**[训练平台与使用说明](software/training-studio/README.md)** · **[动作实验室指南](software/training-studio/web/custom-actions.md)** · **[训练工程](software/training/README.md)** · [验证记录](software/training-studio/docs/verification.md)

---

## 🧰 工具 · 烧卡、调试与资料

常用软件和开发工具集中在[工具导航](tools/README.md)，按要做的事选择即可。

| 要做什么 | 工具与入口 |
|---|---|
| 烧录镜像 | **[Rufus 便携版 EXE](tools/rufus/rufus-4.15p.exe)** · [烧卡说明](tools/rufus/README.md) |
| 串口与舵机调试 | **[飞特 FD 与串口助手](tools/飞特/README.md)** · [3D 网页调试台](tools/servo-web/README.md) |
| 查飞特资料、接入 SDK | **[官方手册、型号规格书与 SDK](docs/飞特资料/README.md)**，标明官网版本和离线副本日期 |
| 测舵机、分析问题 | [HD1910M 测试工具](tools/FeeTech_HD1910M_Servo/README.md) · [STS3215 测试工具](tools/sts3215Servo_testtool/README.md) · [步态分析](tools/gait-analysis/README.md) |

---

## 🦆 鸭友贡献

这个仓库不是一个人写的。合并进来的代码、实测验证过的结论，都记在这儿。

| 谁 | 贡献了什么 |
|---|---|
| [@yoyojacky](https://github.com/yoyojacky) | **Rust 版飞特舵机测试工具**（[PR #27](https://github.com/fanhao375/microduck-replica/pull/27)，已合并）。自己实现的最小 STS/SCS 协议（帧打包、校验和、ping、读写寄存器、状态解析），命令行可以扫描总线（带进度条）、读状态、转到指定位置、单颗测试和批量测试，输出位置偏差、峰值电流、峰值负载、电压温度并判定 PASS/FAIL。台架验收一条命令出汇总表 → [`tools/sts3215Servo_testtool/`](tools/sts3215Servo_testtool/) |
| [@Abo1ish](https://github.com/Abo1ish) | **IMU 小板固件 + 调试台姿态显示**（[PR #32](https://github.com/fanhao375/microduck-replica/pull/32)，已合并）。给官方没开源的那块 imu_to_dxl 写了 STM32G031 固件底子，在实板上验证过，带主机端测试和一份写清楚边界的 [`VALIDATION.md`](hardware/imu_to_dxl/firmware/VALIDATION.md) → [`hardware/imu_to_dxl/firmware/`](hardware/imu_to_dxl/firmware/)；再用 J-Link 把 IMU 姿态实时读出来并进了网页调试台，3D 鸭子的躯干跟着真板子转（`--imu-jlink`，没板子也能用 `--imu-demo` 先看效果）→ [`tools/servo-web/`](tools/servo-web/) |
| 微信群的鸭友 | **Radxa Zero 3W V1.12J 的启动方案**。这批板子（WiFi 换成 AIC8800DS2）光换瑞莎引导还是起不来，他实测出设备树也得换成瑞莎 B1 的：**B1 引导 + B1 DTB + Armbian 6.1.115 内核 + Trixie 系统**，跑通了 → 写进了[踩坑记录](踩坑记录.md#软件)和[镜像使用说明](tools/radxa/镜像使用说明.md#6-如果你的板子不是这一批) |

---

## 鸭友的项目

有些鸭友是自己单独开仓库做的，不在这个仓库里，但跟鸭子直接相关、做得好的，放这儿推一下：

| 项目 | 作者 | 做什么的 |
|---|---|---|
| [microduck-color-studio](https://github.com/LathamZ/microduck-color-studio)（[在线试用](https://lathamz.github.io/microduck-color-studio/)） | [@LathamZ](https://github.com/LathamZ) | **鸭子的 3D 配色工作室，打印前先在网页里把颜色定好。** 70 个零件逐件上色，选材质（PLA / 哑光 / PETG / 金属 / 碳纤 / TPU）和灯光看效果；填上自己手里有哪些耗材，它按库存推荐配色（只用已有 / 补一色 / 丙烯点缀）。还能导入自己的 3MF（比如飞特版），导出**带颜色的多盘 3MF** 和按件拆好的 STL，拓竹 P1S / A1 mini / H2D 有预设。中英文界面，手机上也能看 |
| [MICDUCK_FTHD1901_REBUILD](https://github.com/fengj4780-sudo/MICDUCK_FTHD1901_REBUILD) | [@fengj4780-sudo](https://github.com/fengj4780-sudo) | **飞特版结构改进 + 可选 CNC 加强件。** 在我们的[可编辑 SolidWorks 图纸](https://github.com/fanhao375/microduck-replica-cad)基础上接着改：适配飞特舵机（他仓库里写作 HD1901，零件文件用的是 HD-1910-C001），加强了薄弱件，修了装配干涉和几个破损的模型。腿部 **5 个零件可以换成 CNC 件**让受力更合理（还在打样测试，他估算 5 件含运费约 45 元）；不做 CNC 直接打印也行，强度也比原版好。附一键打印的 3MF（拓竹 H2D、0.4 mm、5 盘）和改好的 SolidWorks 图纸 |
| [Microduck-kissqy](https://github.com/kissqy/Microduck-kissqy) | [@kissqy](https://github.com/kissqy) | **飞特 HD1910 的训练流程与控制实现参考。** 提供训练中控、训练环境准备与任务管理说明、ONNX 模型导出，以及机器人中控、固件和演示视频；算法部分的具体训练流程推荐参考这个项目 |

做了跟鸭子有关的项目（工具、教程、改装、策略……），开 issue 贴个链接，我们加进来。

想加进来：直接开 [issue](https://github.com/fanhao375/microduck-replica/issues) 或提 PR。
硬件、固件、算法、文档、实测数据都算 —— **实测数据尤其欢迎**，这个仓库的规矩是结论如实记录，无论正反。

---

## 资料导航

路径相对于本仓库根目录；机械 CAD 在独立仓库维护。

| 项目 | 仓库位置 | 说明与入口 |
|---|---|---|
| 使用与装配 | [CAD 仓库](https://github.com/fanhao375/microduck-replica-cad) · [BOM/](BOM/README.md) | [首次使用指南](docs/上手指南/README.md) · [采购清单](BOM/README.md) · [装配视频](#-装配视频教程) |
| 软件与镜像 | [software/](software/README.md) | [Studio 中控](software/studio/README.md) · [镜像构建](software/image/README.md) |
| 训练平台与工程 | [software/training-studio/](software/training-studio/README.md)<br>[software/training/](software/training/README.md) | 网页训练工作台、仿真环境与模型导出 |
| 电气与固件 | [hardware/](hardware/README.md) | [电路板工程](hardware/README.md) · [IMU 固件](hardware/imu_to_dxl/README.md) |
| 调试与分析工具 | [tools/](tools/README.md) | [工具导航](tools/README.md) · 烧卡、串口调试、飞特资料和分析脚本 |
| 文档与记录 | [docs/](docs/README.md) | [完整文档导航](docs/README.md) · [踩坑](踩坑记录.md) · [调试](调试记录.md) · [构建](构建日志.md) |
| 历史与社区 | [docs/](docs/README.md#深入研究与归档) | [官方更新跟踪](docs/官方更新跟踪.md) · [历史更新](docs/历史更新.md) · [社区贡献](docs/社区贡献.md) |

---

## 许可与来源

代码、仿真素材和镜像内模型分别适用各自许可，详见[许可与署名](NOTICE.md)及[模型使用条款](software/image/overlay/usr/share/doc/microduck/模型说明.txt)。**复刻镜像仅供个人非商业复刻，不得随收费产品分发。**

本项目与 Pollen Robotics 无隶属关系，未获其背书。

### ❤️ 支持作者

从装配、调试到软件和文档，这个项目是靠一次次尝试积累起来的。如果这些资料和工具帮到了你，欢迎点个 Star ⭐、反馈问题或分享复刻经验；也可以自愿打赏，请作者喝杯咖啡，支持项目继续维护。感谢每一位鸭友的支持。

<p align="center"><a href="assets/alipay-support.jpg"><img src="assets/alipay-support.jpg" alt="支付宝打赏二维码，点击查看原图" width="240"></a><br><sub>支付宝扫码支持 · 点击图片查看原图</sub></p>
