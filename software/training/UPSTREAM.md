# 训练工程来源与维护位置

本目录是普通源码目录，随 `microduck-replica` 一起克隆和下载，不是 Git 子模块。
后续 HD1910 训练工作维护在这里；原 `fanhao375/microduck_rl` 的 HD1910 分支保留作历史来源。

## 导入版本

2026-09-24 从以下固定版本导入已跟踪的完整源码快照：

- 来源：[fanhao375/microduck_rl @ d9e1926](https://github.com/fanhao375/microduck_rl/tree/d9e1926c5220a0eb79fa5aab9df7b336f8e9a226)
- 完整提交：`d9e1926c5220a0eb79fa5aab9df7b336f8e9a226`
- Pollen Robotics 上游基线：`d424a0c899f6b33cbd3daeb279913134349c0b63`；在其上增加 HD1910 任务、M6 参数引用、初始化适配和测试。
- 导入方式：该提交的 `git archive` 源码快照；不包含 `.git`、虚拟环境、训练日志或检查点。
- 原 README 原样保存为 [README.upstream.md](README.upstream.md)。迁移调整本目录的说明与入口链接，
  并修正 HF Jobs 的打包根目录，让归档仅包含训练工程；补充本地打包回归测试。
  训练/执行器逻辑、依赖锁文件、配置、原有测试和几何资产保持该版本内容。

这份快照中的 Microduck 几何、质量惯量和关节约定来自训练项目，不代表飞特复刻实机已完成测量。

## 作者与许可证

| 内容 | 来源 | 许可 |
|---|---|---|
| 训练框架、原有任务、工具及文档 | [Pollen Robotics / microduck_rl](https://github.com/pollen-robotics/microduck_rl) | [Apache-2.0](LICENSE) |
| Microduck 3D 模型与网格 | 同上 | 上游 README 的 CC BY-SA-NC 声明；本仓按 CC BY-NC-SA 4.0 保留署名、非商用与相同方式共享条件，见 [NOTICE](../../NOTICE.md#许可证名称说明) |
| `1910_m6.json` 舵机动力学参数与配套配置参考 | [LuwuDynamics / xgoduck_rl](https://github.com/LuwuDynamics/xgoduck_rl) | [Apache-2.0 副本](src/mjlab_microduck/robot/hd1910/LICENSE) |
| BAM 执行器模型与辨识库（通过依赖安装） | [Rhoban / BAM](https://github.com/Rhoban/bam) | Apache-2.0；锁文件固定 `62bd8ce12154340be97e06f7f41a0ca8f116d967` |
| HD1910 任务、状态初始化适配、相应测试与本目录新增文档 | 本复刻项目 | Apache-2.0 |

根目录对本项目其他文档的 CC 许可不覆盖本训练目录的 Apache 文档；3D 模型许可单独保留。
依赖库继续遵循各自许可证。

## 参数原始出处

- LuwuDynamics 固定提交：`326d77a1122870bdefa2c36403937502c958e69c`
- 原路径：`src/mjlab_microduck/robot/xgoduck/params/1910_m6.json`
- 本目录路径：[`src/mjlab_microduck/robot/hd1910/1910_m6.json`](src/mjlab_microduck/robot/hd1910/1910_m6.json)
- 原文件 SHA-256：`ef2d51adfb1cc0831b9b02ca19aa9176fceecdf725148d084378d7d9a64afceb`

参数按原文件保留，不是本项目自行辨识的结果。详细引用和配置边界见
[参数来源](src/mjlab_microduck/robot/hd1910/README.md)。以后基于这些参数训练自己的策略，仍保留参数作者署名。
