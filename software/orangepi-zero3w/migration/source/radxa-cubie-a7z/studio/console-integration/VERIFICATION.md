# 2026-11-09 接入与安装验证

已将原型布局接到原 Studio 的 Preact、HTTP/CSRF 和单个 WebSocket，并安装前端 `20261109-console1`。生产页面没有使用原型的模拟成功状态。

## 验证范围

- 本地真实 Studio 后端、隔离模拟硬件：浏览器验证显式接管、单颗中位预览/确认、舵机详情、两步交还；IMU 静止采样、前倾指导和中断续采；机器档案保存。
- 浏览器双摇杆拖动在模拟 RPC 留下 `vx=0.0375`，松手后 `vx=0`；验证后执行模拟全局卸力。
- HTTP/WS 的 Origin、路由/可见性、运动租约与录制所有权等 10 项，以及 PTT 状态机 8 项通过。脱敏结果在 `artifacts/experience-verification.json`。
- 首次启动契约 18 项、A7Z 适配 14 项，以及原 Studio 模型、设置、WiFi、Stepper、录制生命周期相关 Node 回归通过。24 个变化 JS 的语法与必需静态导入通过。
- 安装器 43 项临时文件测试在本机和主控 Python 3.13 上通过，覆盖原子交换、失败恢复、回滚重试、链接拒绝、Linux FD 元数据兼容性。
- 实机 manifest 的 32 项变更全部匹配原文件 SHA；安装后完整 web 清单正确，4 个原版模型资源的内容和修改时间保留。
- 实机浏览器验证总览、舵机只读入口、模型/设置分类和未就绪状态；检查 1440px 与 390px、保存截图，手机无页面横向溢出；检查时无 JS 错误。

## 发布证据

- 设备：`cubie-42f4`，`192.0.2.116:8000`。
- 方式：`--apply --static-startup`；阶段 `motion / waiting_for_bus`，不声称硬件或卸力已确认。
- robotd PID `477`、Studio PID `444`，发布前后不变；未重启服务。
- 二进制 SHA256：`1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc`。
- 标定 SHA256：`6d5d0677676ff185193a2aabd48d7d56efc588650ba41e6a97de2c89634c0ba0`。
- 事务 journal 为 `installed`，前后 guard 一致；后端三个摘要继续匹配 README 基线。
- 备份：`/opt/microduck-studio/console-backups/20261109T024849766301Z-c5f27ff2b853/`。
- 前端包 SHA256：`60f1adfded3671169a21dc646053c34c8bf9a38a964a9ec3222e0d7dadd3c263`。
- 最终安装器 SHA256：`dea2f36d7795593bf0c18d8776c145b599b3f3ccb1a4dacc8f9bbb0976c3d0c5`。

首次发布在 staging 遇到 Linux Python 不支持路径 chmod 的 `follow_symlinks=False`，网页未变。修正为 `O_NOFOLLOW` 打开后通过 FD 更新元数据，在主控跑完 43 项测试后重试成功。失败事务保留，未覆盖成功备份。

## 待验收或启用

用户确认未连接舵机，没有对实物执行站立、行走或标定。舵机/IMU 回读、零位/站姿校准、模型执行和录制回放的物理效果，仍需接硬件后现场验收。

主控启动参数为 `--configd none`、`--tof none`、`--camera-socket none`。手柄/配网、ToF、相机需要启用对应板端服务；关节跟踪后端未安装。语音仍需对应服务、配置和麦克风权限。页面保留真实入口及不可用原因。

如需回滚，重新上传同版安装器，先预览上述备份，再执行 `--apply`。仅在当前仍为 `motion / waiting_for_bus` 时使用 `--static-startup`；接好硬件、进入 `control` 后按默认严格门禁回滚。尚未执行实机生产回滚；已在主控临时目录完成事务回归。
