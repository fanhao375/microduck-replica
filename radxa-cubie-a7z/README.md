# Radxa Cubie A7Z 适配（预览）

本目录提供 **Radxa Cubie A7Z + 飞特 HD-1910 舵机/总线 IMU 装配** 的配网、控制、Studio、温控与整机镜像准备源码。主仓库默认镜像仍面向 Zero 3W；A7Z 使用独立镜像和硬件适配。

[下载 A7Z preview1 历史镜像及校验附件](https://github.com/Liangjin5437/microduck-replica/releases/tag/a7z-duck-image-20261109-preview1) · [镜像烧录、首次使用与构建说明](image/README.md) · [维护分支源码](https://github.com/Liangjin5437/microduck-replica/tree/codex/cubie-a7z-preview/radxa-cubie-a7z)

Release 的 `cubie-a7z-duck-20261109-preview1.img.xz` 是修复前发布的历史预览版。当前源码中的只读安装器、镜像目录权限和 Wi-Fi 配网修复仍待重新打包和实机验证。历史镜像已有文件结构、摘要、文件系统和压缩校验记录，尚未完成烧录后的实机启动、联网、完整动作或声音验收。既有总线偶发读取失败仍待排查。

## 源码目录

- `provision/`：独立热点配网服务、安装脚本及决策测试；配网页使用 80 端口，Studio 使用 8000 端口。
- [control/](control/README.md)：A7Z UART 与分组同步读适配、固定版本运行时补丁、只读安装/监测工具、诊断与测试。
- [studio/](studio/README.md)：A7Z Studio 补丁、安装工具和跟踪调试源码；[console-integration/](studio/console-integration/README.md) 包含中控前端变化文件、打包与安装器及验证记录。
- [thermal/](thermal/README.md)：温控策略、systemd 服务和测试。
- [image/](image/README.md)：新装配配置、首启、清理和镜像准备/构建工具，以及本地契约测试。

源码不包含完整系统镜像、外部上游 checkout、模型/运行时安装归档、完整 Python 发行版或本机运行状态。下载文件放在 Release 附件中；`artifacts/`、`workspace/` 和板端 `evidence/` 不作为 PR 源码提交。

## 维护者重建边界

这些工具记录了本次镜像的构建步骤和固定摘要，但普通源码检出不能一键完整重建。须另外准备官方 A7Z 基础系统、固定 R17 动作/声音/推理库归档、A7Z `robotd`、Studio 独立 Python 安装归档和固定提交的 Studio 源码；具体名称、提交、SHA256、工具版本及默认目录布局见 [镜像说明](image/README.md)。部分脚本仍使用原构建布局的相邻外部项目默认路径；重建前应核对实际输入，不能用未知版本替代后跳过摘要检查。

文档中的 `BOARD_IP` 和 `cubie-XXXX` 是板卡局域网地址与设备名占位符，请替换为实际值；`10.42.0.1` 为配网热点固定地址。

## 使用与验证范围

镜像以新装配 commissioning 标定模式首次启动，不沿用原设备的运动授权；应先完成本机舵机和 IMU 标定，再由现场操作者验证活动范围与动作。相机、ToF、实体手柄、持续语音和关节跟踪后端未在该镜像中启用。

各子目录保留源码测试、安装和浏览器验证的实际范围与待验收项。中控前端验证见 [console-integration/VERIFICATION.md](studio/console-integration/VERIFICATION.md)，镜像构建校验见 [image/README.md](image/README.md)；这些记录不替代本镜像的实机启动、总线稳定性和运动验收。
