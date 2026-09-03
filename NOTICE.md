# 署名与来源 / Attribution

本仓库是 **Microduck 的第三方复刻研究**，与 Pollen Robotics 无隶属关系，未获其背书。

## 上游来源

| 来源 | 作者 | 许可证 |
|---|---|---|
| [pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl) | Pollen Robotics | 代码 Apache-2.0；**3D 模型 CC BY-SA-NC** |
| [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck) | Pollen Robotics | Apache-2.0 |

Microduck 是 Pollen Robotics 的商业产品，硬件**部分开源**：

- **RPI Robot HAT 板已由官方完整开源**（[`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)，Apache-2.0，含 KiCad 工程与生产文件）
- **`imu_to_dxl` 板、机械件的可编辑 CAD、整机 BOM 与装配文档**均未公开

本仓库中的一切几何信息，均来自上游 `microduck_rl` 仓库中公开发布的
**MJCF 仿真模型和 STL 网格**；电控结论来自 `microduck` 仓库的源码、设备树与配置文件。

> 勘误（2026-09-03）：本文件此前称「其硬件并未开源」，该表述有误 —— HAT 板是开源的。

## 本仓库中的衍生内容

以下内容由本项目从上游 MJCF + STL 生成，属于 **CC BY-SA-NC 的衍生作品**，
因此以相同许可证发布：

- `assembly-drawings/` —— 全部渲染图与爆炸图
- `cad/` —— 应用了世界变换的「已装配」STL

`scripts/` 下的脚本为本项目原创，以 Apache-2.0 发布。

## 非商用声明

上游 3D 模型采用 **CC BY-SA-NC**，其衍生作品**不得用于商业目的**。
本仓库仅供学习、研究与个人复刻使用。

## 如何署名

> 基于 Pollen Robotics 的 Microduck 模型（CC BY-SA-NC）
> https://github.com/pollen-robotics/microduck_rl
