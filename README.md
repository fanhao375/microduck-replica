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

## 交流群

有人在做同样的事，凑了个微信群一起讨论复刻进度、踩过的坑、元件采购。

**一群已满 200 人**（微信满 200 后无法扫码进），下面是**二群**的码。

<div align="center">
  <img src="assets/wechat-group.png" alt="鸭子复刻 微信群" width="280">
  <br>
  <sub><b>鸭子复刻群 2 · 二维码有效期到 2026-09-11</b>（微信群码 7 天自动失效）<br>
  过期了请开个 <a href="https://github.com/fanhao375/microduck-replica/issues">issue</a> 说一声，我会换上新的</sub>
</div>

## 🔨 实物进度

**有人正在把它真的做出来。** 头壳、躯干壳、腿部结构件和脚已经打印出来，
**腿部件上的 M2 螺丝已经装进去了** —— [紧固件反推](docs/紧固件反推.md) 的结论在实物上成立。

![首批打印件](build-log/photos/2026-09-02-首批打印件.jpg)

本仓库其余部分都是从公开 MJCF 与源码反推的**纸上分析**；
[构建日志](构建日志.md) 记录的是**动手做的过程** —— 打印参数、装配问题、
以及那些反推数据在实物上到底对不对。

> 本仓库反复强调「仿真 STL 不是可打印工程件」。**构建日志就是在验证这句话。**
> 结论会如实记录，无论正反。

**→ [构建日志](构建日志.md)**

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
