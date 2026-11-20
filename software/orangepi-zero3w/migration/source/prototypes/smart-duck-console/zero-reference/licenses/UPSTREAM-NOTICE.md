# 署名与来源 / Attribution

本仓库是 **Microduck 的第三方复刻研究**，与 Pollen Robotics 无隶属关系，未获其背书。

## 上游来源

| 来源 | 作者 | 许可证 |
|---|---|---|
| [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) | Pollen Robotics | 代码 Apache-2.0；**3D 模型 CC BY-NC-SA**（上游 README 写作 "BY-SA-NC"） |
| [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck) | Pollen Robotics | Apache-2.0 |
| [LuwuDynamics/xgoduck_rl](https://github.com/LuwuDynamics/xgoduck_rl) 的 1910 M6 参数与配套配置参考 | LuwuDynamics | Apache-2.0 |
| [Rhoban/BAM](https://github.com/Rhoban/bam) 执行器模型与辨识库（训练依赖） | Rhoban | Apache-2.0 |

Microduck 是 Pollen Robotics 的商业产品，硬件**部分开源**：

- **RPI Robot HAT 板已由官方完整开源**（[`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)，Apache-2.0，含 KiCad 工程与生产文件）
- **`imu_to_dxl` 板、机械件的可编辑 CAD、整机 BOM 与装配文档**均未公开

本仓库中的一切几何信息，均来自上游 `microduck_rl` 仓库中公开发布的
**MJCF 仿真模型和 STL 网格**；电控结论来自 `microduck` 仓库的源码、设备树与配置文件。

> 勘误（2026-09-03）：本文件此前称「其硬件并未开源」，该表述有误 —— HAT 板是开源的。

## 本仓库中的衍生内容

以下内容由本项目从上游公开发布的 MJCF + STL 生成或整理，属于 **CC BY-NC-SA 的衍生作品**，
因此以相同许可证发布：

| 路径 | 内容 | 与上游的关系 |
|---|---|---|
| `assembly-drawings/` | 全部渲染图与爆炸图 | 由上游 MJCF + STL 渲染生成 |
| `software/training/` 内的 3D 网格 | 训练使用的仿真几何 | 保留上游模型来源与许可，不作为实物打印文件 |
| `tools/servo-web/model/` | 网页调试显示模型 | 从上游 MJCF / STL 转换，非最新实物 CAD |
| `software/studio/web/model/` | Studio 网页里的 3D 鸭子 | 从上游仿真模型转换，只留外形，非最新实物 CAD |
| `docs/hole_analysis.json` | 孔位几何分析数据 | 由上游 STL 计算得出 |

> **模型下载迁移（2026-09-28）**：`print/` 和 `cad/` 已改为指向 [CAD 仓库](https://github.com/fanhao375/microduck-replica-cad) 的入口说明，旧单件 STL、装配 STL 与对照表已从当前版本移除，避免误作最新打印件。Git 历史中的旧模型仍为上游 STL 的重命名、分类或装配变换衍生作品，继续遵循 CC BY-NC-SA；移除下载副本不改变历史文件的作者与许可。

**本项目原创内容：**

| 路径 | 内容 | 许可证 |
|---|---|---|
| `scripts/` | 渲染、导出、孔位分析等脚本 | **Apache-2.0** |
| `tools/stl_viewer.html` | 零依赖 WebGL STL 查看器 | **Apache-2.0** |
| `software/studio/` 的代码 | Microduck Studio 网页中控（不含 `web/vendor/`、`deps/`、`web/model/`） | **Apache-2.0** |
| `software/image/` | 复刻镜像的构建脚本、首次开机和配网脚本 | **Apache-2.0** |
| `software/updater/` | 签名组件包构建、原更新引擎适配、网页升级协调与首次引导工具 | **Apache-2.0**；原updaterd来自Pollen Robotics，来源1fa8438 |
| `software/duck-echo/` 的代码 | 本地R3语音、录音和音乐互动；不包含构建机私钥或用户录音 | **Apache-2.0**；外置Vosk模型及ARM64依赖按各自许可，清单保留来源和hash |
| `docs/*.md`、`*.md` | 全部文档与逆向分析文本 | **CC BY-NC-SA 4.0** |
| `build-log/` 照片 | 实物构建照片 | **CC BY-NC-SA 4.0**，由本项目参与者拍摄并授权 |
| `assets/` 中的项目原创素材 | 封面、交流群二维码与项目预览图；不含下列官方板卡图片 | 同上 |

## 官方板卡图片

以下两张图片原样引用，保留各自的作者与原许可，不受本项目文档或原创素材的 CC BY-NC-SA 许可覆盖：

| 路径 | 来源与作者 | 许可证 |
|---|---|---|
| `assets/hw/radxa-zero3w-official.webp` | [Radxa ZERO 3 官方文档](https://docs.radxa.com/en/zero/zero3)，Radxa Computer (Shenzhen) Co., Ltd. | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `assets/hw/rpi-robot-hat-official.png` | [Pollen Robotics 官方 HAT 仓库](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)，Pollen Robotics | [Apache-2.0](https://github.com/pollen-robotics/elec_RPI_Robot_HAT/blob/main/LICENSE) |

原图地址、固定版本和校验值见[图片来源记录](assets/hw/official-board-images.md)。

## 内置训练工程

[`software/training/`](software/training/) 完整收录训练工程，让复刻者一次下载即可获得
代码、参数和仿真模型。导入固定版本与变更范围见 [UPSTREAM.md](software/training/UPSTREAM.md)。

- Pollen Robotics 的训练代码、工具和原文档保留 **Apache-2.0**；本目录新增的 HD1910
  任务、初始化适配、测试和说明也采用 Apache-2.0。许可证全文在
  [`software/training/LICENSE`](software/training/LICENSE)。
- 训练目录内的 **3D 模型和网格保留上游 CC BY-NC-SA 声明**，不受代码的 Apache 许可覆盖。
- LuwuDynamics 的 `1910_m6.json` 原样保留，固定来源、校验值和许可证副本放在
  [`software/training/src/mjlab_microduck/robot/hd1910/`](software/training/src/mjlab_microduck/robot/hd1910/)。
  本项目没有自行辨识这份参数；以后用它训练策略，仍保留参数引用。
- Rhoban/BAM 通过锁定版本的依赖安装，保留其来源与许可。

上表对本项目文档的 CC BY-NC-SA 许可不覆盖训练目录内的 Apache 文档。

## 其他第三方软件

**原样分发的第三方软件：**

| 路径 | 内容 | 来源 | 许可证 |
|---|---|---|---|
| `tools/飞特/` | 飞特 FD 调试软件 1.9.8.5 + CH340 驱动 | [gitee.com/ftservo/fddebug](https://gitee.com/ftservo/fddebug)，2026-09-11 复制 | **MIT**（FTServo 2024，LICENSE 同目录） |
| `tools/rufus/` | Rufus 4.15 Windows x64 便携版与同版本源码 | [pbatard/rufus v4.15](https://github.com/pbatard/rufus/releases/tag/v4.15)，2026-10-25 原样下载 | **GPL-3.0-or-later**；许可、源码归档、校验值及来源记录在该目录 |
| `software/studio/web/vendor/` | Studio 前端库：Preact、htm 3.1.1、three.js 0.160.0 | 各自官方发布 | Preact、three.js **MIT**，htm **Apache-2.0**（许可证在 `web/vendor/licenses/`） |
| `software/studio/deps/` | Studio 的 Python 依赖（anyio、click、h11、idna、pyserial、sniffio、starlette、typing_extensions、uvicorn、wsproto） | pypi.org 原版 wheel，版本和校验值见 `deps/VERSIONS.txt` | MIT / BSD-3-Clause / PSF-2.0 等，各包许可证在各自的 `*.dist-info` 里 |

> 文档中引用的上游源码片段（注释、常量、寄存器定义）来自 `pollen-robotics/microduck`，
> 遵循其 **Apache-2.0** 许可证，引用处均已标注文件路径。

## 配套仓库：SolidWorks 三维图纸

[**fanhao375/microduck-replica-cad**](https://github.com/fanhao375/microduck-replica-cad)
统一维护可编辑的 SolidWorks / STEP、打印文件与 21 页装配安装说明书。飞特与 XL330 分版本提供，文件清单以该仓库对应发布为准；主仓不再维护一套独立的打印模型副本。

| 内容 | 作者 | 许可证 |
|---|---|---|
| SolidWorks 图纸、装配安装说明书、组件图 | **机械行者Robo**（小红书 270594280 / 抖音 1852366168） | **CC BY-NC-SA 4.0** |

那套图纸是**依据上游公开的 STL 网格重建的可编辑参数模型**，属于 CC BY-NC-SA 的衍生作品，
因此以相同许可证发布。**著作权归作者本人**，转载与二次分发请保留署名。
图纸放在独立仓库而不并入本仓，是因为 SolidWorks 源文件解压后约 340 MB，
并入会让本仓每次 clone 都背上这个体积。

## 许可证名称说明

规范名称是 **CC BY-NC-SA 4.0**（署名 - 非商业性使用 - 相同方式共享）。
上游 README 写作「BY-SA-NC」，本仓库沿用其写法时指的是同一个许可证。

> 需要说明的是：上游 `microduck_rl` 仓库的 `LICENSE` 文件本身只有 Apache-2.0，
> **CC 条款仅出现在其 README 的一行文字中**。本仓库对 3D 模型采用更严格的 CC BY-NC-SA
> 是保守做法 —— 若上游澄清为纯 Apache-2.0，本仓库会相应放宽。

## 合规声明

本仓库中的一切结论均来自：

- Pollen Robotics 公开发布的**源码与设备树**（`pollen-robotics/microduck`，Apache-2.0）
- 公开发布的**仿真模型与网格**（`pollen-robotics/microduck_rl`）
- 公开发布的**KiCad 工程**（`pollen-robotics/elec_RPI_Robot_HAT`，Apache-2.0）
- LuwuDynamics 公开发布的 **1910 M6 参数与配套配置**（`LuwuDynamics/xgoduck_rl`，Apache-2.0）
- 厂商公开的器件手册与规格页

**未使用任何非公开资料，未拆解实物，未接触过任何未公开的设计文件。**
`imu_to_dxl` 板的固件不在任何开源仓库中，本仓库只还原了它在总线侧的可观测行为。

## 非商用声明

上游 3D 模型采用 **CC BY-NC-SA**，其衍生作品**不得用于商业目的**。
本仓库仅供学习、研究与个人复刻使用。

## 如何署名

> 基于 Pollen Robotics 的 Microduck 模型（CC BY-NC-SA）
> https://github.com/pollen-robotics/microduck_rl
