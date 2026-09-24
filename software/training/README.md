# Microduck HD1910 仿真训练

飞特 HD-1910 的训练代码、模型、参数、依赖锁文件和测试统一维护在本目录。
下载或克隆 `microduck-replica` 即可取得完整工程，无需再拉训练分支或初始化子模块。

采用 **[LuwuDynamics 公开的 1910 BAM M6 参数](src/mjlab_microduck/robot/hd1910/README.md)**，
训练框架与 Microduck 几何来自 [Pollen Robotics](https://github.com/pollen-robotics/microduck_rl)，
执行器模型来自 [Rhoban/BAM](https://github.com/Rhoban/bam)。参数不是本项目自行辨识的结果。
本项目补充 HD1910 任务配置、飞特控制器状态初始化适配及回归检查。

## 快速开始

使用有 NVIDIA CUDA GPU 的 Linux，或已配置 GPU 的 WSL2；需要 Git 和 [uv](https://docs.astral.sh/uv/)。
在 Linux / WSL 终端执行：

```bash
git clone https://github.com/fanhao375/microduck-replica.git
cd microduck-replica/software/training
uv sync --frozen --python 3.12

# 先用 64 个环境、5 轮迭代检查训练链路
uv run --frozen train Mjlab-Velocity-Flat-MicroDuck-HD1910 \
  --env.scene.num-envs 64 --agent.max-iterations 5 \
  --agent.logger tensorboard
```

如果已经下载本仓库，直接进入 `software/training/` 安装和运行即可。
可选的 HF Jobs 云端提交功能需要通过 Git 克隆，以便按 Git 忽略规则打包源码；
直接下载 ZIP 适用于上述本地训练。
日志与检查点保存在本目录的 `logs/rsl_rl/microduck_hd1910_velocity/`，不随源码发布。
5 轮短训练用于检查加载、步进和保存流程，尚不足以学会走路。

| 入口 | 用途 |
|---|---|
| `Mjlab-Velocity-Flat-MicroDuck-HD1910` | HD1910 平地速度任务 |
| `Mjlab-Velocity-Rough-MicroDuck-HD1910` | HD1910 地形速度任务 |

当前是仿真接入基线，尚无本机实测通过的走路策略。质量惯量仍沿用原版 Microduck
仿真值；后续需要结合飞特实机校正，并核对零位、方向、限位、供电、IMU 和通信时序。

## 说明与测试

- [使用说明、验证记录与初始化问题解释](docs/hd1910-baseline.md)：缺失的是飞特控制器的目标角度历史，修复不改变借用的 M6 参数。
- [来源、固定版本与许可证范围](UPSTREAM.md)。
- [原训练项目 README](README.upstream.md)：保留上游任务和工具说明，其中默认任务面向 XL330；飞特版使用上表的 HD1910 入口。

在本目录运行接入与回归检查（包含 GPU 步进）：

```bash
uv run --frozen --with pytest python -m pytest -q \
  tests/test_hd1910_cfg.py tests/test_hd1910_rollout.py \
  tests/test_ground_pick_cfg.py tests/test_nan_guard.py tests/test_obs_nan_guard.py \
  tests/test_hf_source_snapshot.py
```

策略导出沿用 `scripts/export.py`，把观测归一化包含在 ONNX 中。短训练检查点只用于
验证流程，不作为实机步态发布。

## 许可

本训练目录的代码、配置、参数和文档采用 [Apache-2.0](LICENSE)，保留第三方署名。
**3D 模型与网格遵循上游 CC BY-NC-SA 声明**，不因并入本仓库而改为 Apache-2.0；
详见 [UPSTREAM.md](UPSTREAM.md) 与根目录 [NOTICE.md](../../NOTICE.md)。
