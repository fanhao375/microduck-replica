# software · 软件

> 本目录保留训练所需的上游仿真模型，**不是最新实物结构件的打印来源**。模型下载统一到 [microduck-replica-cad](https://github.com/fanhao375/microduck-replica-cad)；引入飞特舵机参数不等于已更新 CAD 几何、质量和惯量。

官方运行时 [`pollen-robotics/microduck`](https://github.com/pollen-robotics/microduck) 是照 Dynamixel XL330 写的，本仓库主线用飞特 HD-1910，所以主程序要改。

## 现在用的软件一览

| 软件 | 干什么 | 在哪、怎么用 | 许可 |
|---|---|---|---|
| **复刻镜像** | 烧卡开机就能用的整套系统：主程序、模型、Studio、首次开机和配网 | [`image/`](image/)：用户看[烧录和第一次使用](image/烧录和第一次使用.md)，做镜像的人看 [image/README.md](image/README.md)。已发布版本见 [Releases](https://github.com/fanhao375/microduck-replica/releases/latest) | 构建脚本 Apache-2.0；镜像里各部分的许可见镜像内 `/usr/share/doc/microduck/` |
| **Microduck Studio** | 装在鸭子上的网页中控：状态、卸力、舵机零位、IMU 方向、写进主程序、手柄、遥控、日志 | [`studio/`](studio/)。用复刻镜像的不用另装 | 代码 Apache-2.0 |
| **网页软件更新** | 签名组件包、独立更新服务、离线上传、安装与回退 | [`updater/`](updater/)，20261027镜像预装；旧卡先一次性SSH引导，[操作说明](../docs/软件更新与语音使用说明-20261027.md) | Apache-2.0 |
| **本地坐站语音** | R3单击监听开关、中文坐下/站起来、鸭叫提示 | [`duck-echo/`](duck-echo/)，20261027镜像附中文模型与ARM64依赖；无需云端Key | 代码Apache-2.0，模型与依赖按各自许可 |
| **主程序 robotd / padd / mediad** | 在官方 0.15.1 上加了飞特 HD-1910 舵机总线、I²C IMU 模块、陀螺融合、UDP 手柄 | 源码在 [fanhao375/Microduck-kissqy](https://github.com/fanhao375/Microduck-kissqy)，具体镜像使用的分支和提交见 [image/README.md](image/README.md)。复刻镜像里已经装好 | Apache-2.0 |
| **走路和动作模型** | 走路、坐下 / 站起、低头捡、前滚翻、左右踢、轮滑 4 个 | 复刻镜像里带着。每个模型干什么、对应哪个手柄键、谁训练的，见[模型说明](image/overlay/usr/share/doc/microduck/模型说明.txt) | 作者的个人非商业许可，**不是开源许可**：不得随收费产品分发 |
| **训练工程** | HD-1910 仿真训练 | [`training/`](training/) | 代码 Apache-2.0，3D 网格 CC BY-NC-SA |
| **Microduck Training Studio** | 电脑上的网页训练工作台：实验管理、曲线、评估与模型导出 | [`training-studio/`](training-studio/README.md)，与鸭子上的 Studio 分开运行 | 代码 Apache-2.0；依赖与素材许可见该目录说明 |
| servo-web（旧） | 9 月的网页舵机调试台，Studio 出来以前用 | [`tools/servo-web/`](../tools/servo-web/) | Apache-2.0 |

独立的舵机测试、IMU 检查和步态分析入口见[工具导航](../tools/README.md)；电路板与固件入口见[硬件工程](../hardware/README.md)。

## Orange Pi Zero 3W 移植

A733 主板的热点配网、Studio / 只读控制适配、UART0 HAT 接入和独立配网基础镜像见 [`orangepi-zero3w/`](orangepi-zero3w/)。这是独立板型贡献；与上面的 Radxa Zero 3W 主线镜像不能互刷，运动能力尚未验收。

## HD1910 仿真训练

**[`training/`](training/)** 包含完整 Python 训练工程：模型、LuwuDynamics 的 1910 M6 参数、
HD1910 任务配置、飞特控制器初始化适配、依赖锁文件和测试。克隆本仓库即可取得，
不需要再下载 `microduck_rl` 分支。

[快速开始](training/README.md) · [运行说明与初始化问题解释](training/docs/hd1910-baseline.md) ·
[来源和许可证](training/UPSTREAM.md)。当前是仿真接入基线，尚未完成本机实测步态训练。

## IMU 裸板调试

STM32G031 + LSM6DSV16X 固件与硬件资料放在 [`hardware/imu_to_dxl/firmware/`](../hardware/imu_to_dxl/firmware/)。姿态显示并入 [`tools/servo-web/`](../tools/servo-web/)：使用 `python server.py --port COM5 --imu-jlink imu-jlink.json`，同一个模型同时接收舵机关节角和 J-Link 的躯干姿态；只有裸板时可用 `--fake` 代替串口参数。

**范围：固件与模拟验证、SWD 台架观察。** 附带 0.2.0 源码已实现飞特 SCS/STS 协议、ID 200、地址 56 的 15 字节块及同步读排队；主机测试与模拟总线验收记录见 [`VALIDATION.md`](../hardware/imu_to_dxl/firmware/VALIDATION.md)。实板已有烧录及 IMU 观察记录，但真实混合总线、响应/释放时序和整机运行时验收仍未完成。此前“仍是 Dynamixel 基线、尚未实现契约”的说明已过时，不能与“真总线尚未验收”混为一谈。

## 历史：9 月的第一版飞特后端（已不用）

> 现在用的主程序是上面一览表里的 fork。下面是 9 月基于官方 0.14.1 写的第一版飞特后端，没上过板，留作参考。
>
> [`fanhao375/microduck` · `feetech` 分支](https://github.com/fanhao375/microduck/tree/feetech)：已跟上官方 **0.14.1**，三个 crate 的测试全过（duck-control 80 / robotd-params 151 / robotd 93）。

| 文件 | 行数 | 做了什么 |
|---|---|---|
| [`duck-control/src/bus_feetech.rs`](https://github.com/fanhao375/microduck/blob/feetech/duck-control/src/bus_feetech.rs) | 744（新） | **`FeetechIo` 主体**：每 tick 一次 `sync_read` 地址 56 长 15；位置换算带方向表；写目标带钳位；`set_gain` 只写 P（飞特没有 I/D 同构）；`reboot` 先关扭矩再 0x08；开机检查 END 字节序 / 应答级别 / 运行模式 / 波特率 / 相位；`imu_ready()` 恒 false。含 11 个单元测试 |
| [`duck-control/src/bus_select.rs`](https://github.com/fanhao375/microduck/blob/feetech/duck-control/src/bus_select.rs) | 149（新） | `AnyBus` 枚举，两种后端二选一（`Box<dyn RobotIo>` 编不过，原因见[架构文档](飞特适配架构.md) §3.1） |
| `robotd-params/` | +50 | `[bus] protocol = "dynamixel2" \| "feetech-sts"`、`[bus.feetech] p_scale / directions`，登记进参数表 |
| `robotd/src/main.rs` | +63 | `BusIo` 换成 `AnyBus`，开总线时带协议；参数不合法直接报错不重试 |
| `duck-control/src/model.rs` | +8 | HD-1910 出厂运行模式 4 的期望值 |
| `deploy/robotd.toml` | — | 两个新键，带注释 |

编译（需要 Rust）：

```bash
git clone -b feetech https://github.com/fanhao375/microduck.git
cd microduck && cargo test -p duck-control -p robotd-params -p robotd
```

**为什么放 fork 不放这个仓库**：`FeetechIo` 是官方 Rust 工作区里 `duck-control` 这个 crate 的一个文件，
用的是官方的 `RobotIo` trait、`Safety<T>` 泛型和参数系统 —— 单独拷出来编译不了。
放在 fork 分支上，官方更新时 `git rebase` 就能跟上（2026-09-20 跟了 55 个提交，零冲突），
将来也能直接向官方提 PR。官方是 Apache-2.0，改动留在 fork 里出处也清楚。

| 文件 | 内容 |
|---|---|
| [`飞特适配架构.md`](飞特适配架构.md) | 官方运行时现有架构（接缝在哪、每个 tick 干什么、开机序列）、两家舵机逐项差别、适配方案、**`imu_to_dxl` 小板固件架构（§4）**、台架验收、待查清单、构建部署 / 策略关系 / 失败模式、评审记录 |

小板固件的接口契约（15 字节块、时序、排队规则、验收）单独成文：[`hardware/imu_to_dxl/总线协议.md`](../hardware/imu_to_dxl/总线协议.md)，配套工具 [`tools/imu200/`](../tools/imu200/)（假小板 + 测试向量 + 真板子验收）。

板子怎么烧、怎么装官方运行时，在 [不打 HAT](../docs/不打HAT.md) 和 [`tools/radxa/`](../tools/radxa/)。

飞特协议和内存表的官方原文（2026 年版快照）在 [`docs/飞特资料/`](../docs/飞特资料/)。

动代码前先把舵机点通：[`tools/servo-web/`](../tools/servo-web/) 是接串口的网页调试台，拖滑块、看回读、导寄存器底账、3D 鸭子跟着动，架构文档台架清单 0–8 项都用它。
