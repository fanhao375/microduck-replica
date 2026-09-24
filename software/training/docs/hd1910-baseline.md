# HD1910 M6 仿真训练基线

本基线采用 **[LuwuDynamics/xgoduck_rl](https://github.com/LuwuDynamics/xgoduck_rl)
公开的 1910 BAM M6 动力学参数**，不是本项目自行辨识的数据。
[来源记录](../src/mjlab_microduck/robot/hd1910/README.md)包含固定提交、原文件
SHA-256、配套配置和 Apache-2.0 许可证副本。感谢 LuwuDynamics、
[Pollen Robotics](https://github.com/pollen-robotics/microduck_rl) 和
[Rhoban/BAM](https://github.com/Rhoban/bam) 的开源工作。

## 已接入的内容

| 项目 | 当前配置 |
|---|---|
| 平地入口 | `Mjlab-Velocity-Flat-MicroDuck-HD1910` |
| 地形入口 | `Mjlab-Velocity-Rough-MicroDuck-HD1910` |
| 舵机参数 | LuwuDynamics 的 `1910_m6.json`，原样保留 |
| 增益 / 电压 | `kp_fw=5`，7.4–8.0 V 随机电压，压降下限 7.0 V |
| 控制延迟 | 3–6 个仿真步；当前仿真步长 5 ms，即 15–30 ms |
| 策略接口 | 50 Hz，61 维观测，14 维动作；保留原 Microduck 顺序 |
| 模型 | 本地 Microduck 几何、质量、惯量和默认站姿 |
| 日志 | 独立 `microduck_hd1910_velocity` 目录，默认 TensorBoard 本地记录 |

原 XL330 训练入口仍可用。HD1910 工厂复制机器人配置后替换执行器，不影响
其他任务。没有把 XgoDuck 的 0.8 kg 模型、编码器零位、方向或 ONNX 搬到本机。
现有 Microduck 质量惯量仍是原版仿真值，后续需要按飞特实机数据校正。

### 本地兼容修复

这里修的是仿真控制器的运行状态初始化，不是重新初始化神经网络，也没有重新
拟合舵机参数。飞特模型用 `q_target_smooth` 保存上一时刻的目标角度，以限制
目标每一步变化的幅度；计算下一步之前，这份历史必须存在。

锁定的 BAM `62bd8ce` 中，飞特控制器的 `q_target_smooth` 只在读辨识日志时创建，
mjlab 训练不走这条路径。实际 GPU 仿真在首次 reset 时复现了缺少该字段的错误。

```text
AttributeError: 'STS3215Actuator' object has no attribute 'q_target_smooth'
```

`actuator/feetech_bam.py` 补齐状态初始化，并在每个环境重置后的第一次计算时，
从该环境当前关节位置重新开始目标限速，避免前一轮历史影响新姿态。
重置某一个环境时，只清理它自己的历史；其他并行环境继续保留各自状态。
选择当前关节位置作为起点，是为了避免把上轮目标或统一的零角度带进新一轮。
这个适配不改 LuwuDynamics 参数，也不修改系统安装的 BAM。

该问题是在本项目锁定的 BAM/mjlab 组合中复现的，不据此推断对方实机运行时
存在同样问题。回归检查覆盖了首次启动、连续计算和单环境重置后的历史隔离。

## 获取代码

训练代码、模型和参数统一放在 [microduck-replica 的 software/training](../README.md)。
使用 Linux CUDA 机器或已配置 GPU 的 WSL2，在其终端执行：

```bash
git clone https://github.com/fanhao375/microduck-replica.git
cd microduck-replica/software/training
uv sync --frozen --python 3.12
```

如果已克隆或下载本仓库，直接进入 `software/training/` 即可，无需子模块或另一个训练仓库。
固定导入版本与许可证见 [UPSTREAM.md](../UPSTREAM.md)。

## 怎么运行

以下命令均在 `software/training/` 目录执行，日志默认保存在该目录下：

```bash
# 列出已注册任务
uv run --frozen list-envs

# 先做短训练，检查训练和保存流程
uv run --frozen train Mjlab-Velocity-Flat-MicroDuck-HD1910 \
  --env.scene.num-envs 64 --agent.max-iterations 5 \
  --agent.logger tensorboard --agent.run-name hd1910_m6_smoke

# 接入与回归检查（包含 GPU 实际步进）
uv run --frozen --with pytest python -m pytest -q \
  tests/test_hd1910_cfg.py tests/test_hd1910_rollout.py \
  tests/test_ground_pick_cfg.py tests/test_nan_guard.py tests/test_obs_nan_guard.py \
  tests/test_hf_source_snapshot.py
```

uv 默认在 `software/training/.venv/` 建环境。如果将源码放在 Windows 磁盘上，
可在首次 `uv sync` 前设置 `UV_PROJECT_ENVIRONMENT`，把虚拟环境放到 WSL 的 Linux
文件系统中；后续命令使用同一个环境路径。飞特版使用上面的 HD1910 任务，
[原 README](../README.upstream.md) 中的默认任务面向 XL330。

可选的 `--hf-jobs` 云端提交功能需要 Git checkout；ZIP 下载只用于本地训练。
迁移后打包以训练项目为根，不包含复刻仓库中的硬件文件、照片等其他目录。
本次只验证本地打包行为，没有提交付费云端任务。

## 验证记录

2026-09-24，迁移前在 WSL Ubuntu-22.04、Python 3.12.14、RTX 5060 Ti 上，
已完成 64 个环境、5 轮 PPO 短训练，共 7,680 个环境步，
训练日志中 `Episode_Termination/nan_state` 每轮均为 0。模型日志确认加载
`kt=0.6238`、`R=4.9102`、14 个 HD1910 执行器。

- 26 项配置、回归与 GPU 步进测试通过；包含只重置一个环境时的目标历史检查。
- 短训练检查点已通过项目的标准导出脚本生成 ONNX，观测归一化包含在图中。
- ONNX 检查及 CPU 推理通过：输入 `[1, 61]`、输出 `[1, 14]`，输出为有限值，
  关节元数据与本地 Microduck 顺序一致。
- mjlab 对宽泛的关节匹配正则发出一条匹配到 site 的提示；实际执行器数检查为
  14，没有增加 site 执行器。该正则沿用上游的 `^(?!passive_).*` 约定。
- 独立代码审查核对了 reset 顺序、目标历史隔离、原 XL330 配置隔离及来源校验，
  没有提出需要修复的正确性问题；这不代替实机一致性验证。
- 默认站姿测量：4 个环境，固定 7.4 V，无推力、无自动 reset；关节和躯干
  roll/pitch 初始扰动均为 ±0.01 rad，连续保持 3 秒。最大倾角分别为
  4.47° / 6.17° / 5.94° / 5.03°，末端躯干高度约 0.1153–0.1156 m；
  该有限样本未倒下，不表示所有电压、扰动或真实硬件均稳定。

上述测量脚本、JSON、日志与导出产物保留在迁移前本地工作区的忽略目录
`artifacts/hd1910/`，短训练检查点位于该工作区的
`logs/rsl_rl/microduck_hd1910_velocity/2026-09-24_20-40-50_hd1910_m6_smoke/`。
这些本地验证产物未随源码快照发布。短训练检查点只是接入测试产物，
尚未学会走路，不能作为实机步态使用。

### 迁入 microduck-replica 后的复验

同日在 `software/training/` 路径按锁文件新建独立环境，安装成功；上述 26 项
测试再次通过。另有 4 项离线源码打包测试通过，覆盖训练独立仓库/子目录布局、
当前未提交改动、新源码和忽略产物。迁移后的 64 环境、5 轮 PPO 短训练再次完成，
共 7,680 个环境步，5 轮 `nan_state` 均为 0；加载参数仍为 `kt=0.6238`、`R=4.9102`。
参数原文件 SHA-256 与引用来源一致。

独立审查发现的 HF Jobs 打包路径和几何许可文案问题，由第二位审查者确认后修正，
最终复核未发现新的可执行问题。该轮未重复站姿测量或 ONNX 导出，前述相关数据
属于迁移前记录。复验日志和检查点仍只保留在本地忽略目录。

## 实机到手后

先确认 15 颗舵机状态并完成逐关节零位、方向和限位，再接入主控 IMU。
记录实际 P/D、运行模式、供电和总线时序，做小幅阶跃实验对比模型。
本基线 P=5 是上游训练设定；上游 Arduino 运行时使用 P=6、D=20，不能直接
视为同一套已验证的实机参数。本次没有连接或控制实机，也没有写 EEPROM。

自行训练出的策略与采用的舵机模型是两个来源层次：只要仍使用这份 M6 参数，
发布模型或训练结果时就继续保留 LuwuDynamics 的引用。
