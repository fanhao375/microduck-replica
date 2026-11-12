# Orange Pi Zero 3W 完整镜像与源码

适用于 Orange Pi Zero 3W（Allwinner A733）、官方 Ubuntu Jammy 1.0.2 / Linux 6.6.98。R2 镜像预装 Microduck Studio、只读主控、模型、独立 Python、系统依赖和开机热点配网，烧卡后可直接进入网页。与仓库主线的 Radxa Zero 3W 镜像分别维护，不能互刷。

## 下载与首次使用

下载贡献者 fork 的 [Release：orangepi-zero3w-20261112-r2](https://github.com/Liangjin5437/microduck-replica/releases/tag/orangepi-zero3w-20261112-r2) 中的 `microduck-orangepi-zero3w-studio-20261112-r2.img.gz` 和 `IMG-SHA256SUMS`。按[镜像说明](image/README.md)解压、校验后，将 `.img` 烧录到至少 8 GB 的 SD 卡。

1. 插卡通电，手机连接开放热点 `OrangePi_XXXXXX`；若未自动弹出页面，打开 `http://10.42.0.1/`。
2. 选择家庭 Wi-Fi 并连接，再让手机回到同一局域网。
3. 打开 `http://<设备当前 IP>:8000/`。设备地址可从路由器的 DHCP 设备列表查看。
4. 首次页面直接显示“设一个登录密码”，输入并确认自己的网页密码，点击“设好登录密码”后进入 Studio。

这条流程不需要 SSH、电脑端 `personalize_image.py` 或另外安装应用包。Linux 账户默认锁定，没有出厂登录密码；Studio 自启不依赖 Linux 交互登录。后续如需 SSH，可在 Studio 的“系统设置 → 账号与 SSH”中自行设置，网页密码与 Linux/SSH 密码分别管理。

旧配网版及其运行包已撤下，由 R2 完整镜像取代。R2 的 `orangepi-quanzhi-20261112-r2.tar.gz` 仅供已有系统的可选手动安装；刷 R2 镜像后已经装好全部应用。

## 预装内容与默认状态

- Studio 的真实后端、`20261109-console1` 前端、WebGL 资源、独立 CPython 3.13.14 和固定版本 Python 依赖。
- `robotd`、`padd`、`mediad`、`robotctl`、ONNX Runtime 1.28、10 份模型与未校准配置。
- 系统动态库与工具、`orangepi-provision.service`、`robotd.service`、`microduck-studio.service` 的自启配置。
- 受限管理助手及 sudoers、UART/HAT 适配源码和原始程序参考快照。

主控自启使用 `--read-only`，策略关闭，串口为 `/dev/orangepi-robot-uart-unconfigured`，关节和 IMU 均未校准。`padd`、`mediad` 不自启。按[硬件说明](migration/hardware/README.md)完成板级接入后仍需单独校准和验收，网页在线不代表已经能够运动。

## 源码与验证

- [镜像构建、下载和清理说明](image/README.md)
- [应用组成与旧卡可选安装](migration/README.md)
- [机器人主控来源与只读配置](migration/control/README.md)
- [Studio 首次设密与平台适配](migration/studio/README.md)
- [热点配网源码](provision/README.md)
- [HAT / UART0 适配](migration/hardware/README.md)

本次在隔离 Linux ARM64 环境执行了镜像根文件系统内的预装与读回校验、ELF 动态库检查、Python SSL/SQLite/TOML 导入、主控帮助、全部 10 份 ONNX 的模型加载预热、sudoers 和服务单元检查。110 项自动测试通过（18 项镜像、66 项主机工具、26 项 Studio）。

从最终封装镜像建立独立副本，在原生 ARM64 Linux 中运行真实 robotd 与 Studio，并通过 Chrome 验证：首次设密、密码不一致/错误密码拒绝、刷新、退出重登、Studio 服务重启后的密码和会话保留、新浏览器不能重新初设，以及 3D 资源实际加载和渲染。真实 RPC 为 `read_only/waiting_for_bus`。完整结果见 [browser-verification.json](https://github.com/Liangjin5437/microduck-replica/releases/download/orangepi-zero3w-20261112-r2/browser-verification.json)；截图：[首次设置 1440px](https://github.com/Liangjin5437/microduck-replica/releases/download/orangepi-zero3w-20261112-r2/first-setup-1440.png)、[首次设置 390px](https://github.com/Liangjin5437/microduck-replica/releases/download/orangepi-zero3w-20261112-r2/first-setup-390.png)、[中控 1440px](https://github.com/Liangjin5437/microduck-replica/releases/download/orangepi-zero3w-20261112-r2/console-1440.png)、[中控 390px](https://github.com/Liangjin5437/microduck-replica/releases/download/orangepi-zero3w-20261112-r2/console-390.png)。

本次没有可用实板，未重新刷卡启动；A733 内核、热点驱动、SD 首启和重启恢复仍需实板验证。原开发板的有限只读通信记录见硬件说明，不能代替这份 R2 镜像的刷卡验收。摄像头、ToF、标定、站立和行走未通过本移植验收。

## 来源与许可

`robotd` 的个人编译路径已在 ELF 诊断字符串中等长泛化，发布程序摘要与来源程序不同，前后 SHA-256 见 [control 说明](migration/control/README.md)及 Release 的 `path-sanitization.json`。本目录包含补丁和预构建程序来源，不包含完整 Rust 工作区。

Studio 和移植代码遵循各目录原有许可；模型沿用原作者的个人非商业许可，独立于 Apache-2.0 程序代码。系统、CPython、ONNX Runtime、Python 依赖与 3D 资源按随包许可证分发，详见仓库 [NOTICE](../../NOTICE.md) 和[软件导航](../README.md)。
