# 署名与来源 / Attribution

本仓库是 **Microduck 的第三方复刻研究**，与 Pollen Robotics 无隶属关系，未获其背书。

## 上游来源

| 来源 | 作者 | 许可证 |
|---|---|---|
| [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) | Pollen Robotics | 代码 Apache-2.0；**3D 模型 CC BY-NC-SA**（上游 README 写作 "BY-SA-NC"） |
| [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck) | Pollen Robotics | Apache-2.0 |

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
| `cad/` | 应用了世界变换的「已装配」STL | 由上游 STL 按运动学层级合并 |
| `print/` | 46 个单件 STL | **直接再分发**上游 STL，仅做重命名与分类 |
| `docs/hole_analysis.json` | 孔位几何分析数据 | 由上游 STL 计算得出 |

> ⚠️ **关于 `print/`**：本仓库的原则是「不重复托管上游代码」，但 `print/` 是个**有意的例外** ——
> 打印件是复刻者最常需要、也最需要能在网页上逐个点开查看的东西。
> CC BY-NC-SA 明确允许这种再分发，条件是署名、相同方式共享、非商业使用，本节即为此声明。
> 上游基线：`pollen-robotics/microduck_rl` @ `2fa62b8`（2026-07-28，抓取时的 `assets/`）。

**本项目原创内容：**

| 路径 | 内容 | 许可证 |
|---|---|---|
| `scripts/` | 渲染、导出、孔位分析等脚本 | **Apache-2.0** |
| `tools/stl_viewer.html` | 零依赖 WebGL STL 查看器 | **Apache-2.0** |
| `docs/*.md`、`*.md` | 全部文档与逆向分析文本 | **CC BY-NC-SA 4.0** |
| `build-log/` 照片 | 实物构建照片 | **CC BY-NC-SA 4.0**，由本项目参与者拍摄并授权 |
| `assets/` | 封面与交流群二维码 | 同上 |

> 文档中引用的上游源码片段（注释、常量、寄存器定义）来自 `pollen-robotics/microduck`，
> 遵循其 **Apache-2.0** 许可证，引用处均已标注文件路径。

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
- 厂商公开的器件手册与规格页

**未使用任何非公开资料，未拆解实物，未接触过任何未公开的设计文件。**
`imu_to_dxl` 板的固件不在任何开源仓库中，本仓库只还原了它在总线侧的可观测行为。

## 非商用声明

上游 3D 模型采用 **CC BY-NC-SA**，其衍生作品**不得用于商业目的**。
本仓库仅供学习、研究与个人复刻使用。

## 如何署名

> 基于 Pollen Robotics 的 Microduck 模型（CC BY-NC-SA）
> https://github.com/pollen-robotics/microduck_rl
