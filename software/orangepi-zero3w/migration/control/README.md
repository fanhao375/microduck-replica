# 香橙派控制程序安装载荷

`bundle/payload/` 是面向目标系统根目录的文件树，`bundle/manifest.json` 记录每个文件的源、SHA256、大小、模式与 ELF 依赖。父级安装器负责传输、备份、创建 `robot` 组、落盘、启用服务及板端验证。本目录的准备脚本不连接设备、不安装到本机。

## 来源和范围

- `robotd`、`bus_monitor.py`：`11quanzhi/radxa-cubie-a7z/control/artifacts/`。控制程序固定 SHA256 为 `1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc`，保留 A7Z sync3.1 程序字节。
- `padd`、`mediad`、`robotctl`、ONNX Runtime、通用 `models/` 与 `roller/`：明确引用的 `10fanhao_duck/runtime/release-assets/Microduck-Console-R17-V167.zip` 内 `Microduck-Console-R17-V167/updates/Microduck-Robotd-FT6-R17.tar.gz`。内层归档 SHA256 为 `bcfd5c183c697b9c4e0fdbd563d995eda6a48331a7c49b9e76d20bd156c65cce`。归档内旧 robotd 不覆盖上述新版本。
- 参数与未校准模板：该 runtime 仓库提交 `d1a2c62efdfb8c7a446ac99f562849e5a3a9587f` 的 `microduck-hd1910/firmware/packaging/`。
- 没有复制其他机器的运行状态、HOME、实测零位、IMU 校准或 `robot-calibration-source.json`。没有提供 `configd`；原 A7Z Studio 的该功能为 `none`。
- 不安装或启动 A7Z 的 `cubie-servo-uart.py`，不写 MMIO、不创建串口别名。`padd`、`mediad` 只存放程序，本载荷没有它们的启用链接。

## 初始行为

主程序安装到 `/opt/robot/feetech-ft5-r5/robotd`；配置与未校准模板分别为 `/etc/robot/feetech-ft5/robotd.toml`、`hd1910-calibration.toml`。

`robotd.service` 明确使用 `--read-only`，配置同时禁用策略。初始串口 `/dev/orangepi-robot-uart-unconfigured` 应不存在，这是未接舵机/IMU且未完成板级 UART 适配的真实状态，不能创建指向猜测 UART 的链接来消除提示。模板 15 个关节均为未校准，2048 是占位，`motion_enabled`、`imu_mount_verified`、`units_verified` 均为 `false`。

已核对上述固定提交：`main.rs` 先调用 `claim_socket` 建立 RPC 监听，再启动控制线程；只读连接失败会每秒重试，发布 `mode=read_only`、`phase=waiting_for_bus` 和真实端口错误。服务可以保持运行并回答 RPC，同时硬件健康仍不可用。这不代表总线、IMU 或运动验收完成。Socket 为 `/run/robotd.sock`、模式 `0660`，继承 unit 的 `Group=robot`；Studio 需要该附加组。

未来实际接线、UART 适配和校准必须单独完成，不能直接切到运动模式或沿用其他设备标定。

## 依赖

全部程序与 ONNX 库均已检查为小端 AArch64 ELF。主程序只动态链接 glibc 的 `libm/libc/libpthread/libdl`；`padd` 另需 `libudev.so.1`；`mediad` 另需 GStreamer 的 `webrtc/base/video/app` 及 GLib/GObject/GIO 库。ONNX Runtime 需要 `libstdc++`、`libgcc_s` 等常见运行库。完整 SONAME 在 manifest 中。

程序最高 GLIBC 符号版本为 2.30，ONNX Runtime 为 2.27，低于 Ubuntu Jammy 的 glibc 2.35。板端仍需用 `ldd`/`--help` 检查实际动态库是否齐备。

## 准备与静态验证

在仓库根目录执行：

```sh
python3 -m py_compile migration/control/prepare_control.py
python3 migration/control/prepare_control.py \
  --source-control /path/to/source/radxa-cubie-a7z/control \
  --runtime /path/to/runtime-checkout \
  --output /tmp/orangepi-control-new
```

输出目录必须不存在，防止覆盖其他会话的载荷。准备过程核验控制程序、内层发布包摘要、所有目标 ELF、策略引用文件、TOML 禁用标志与未校准模板。

## 板端无硬件验证

以下命令不启动硬件服务，也不打开 UART：

```sh
/opt/robot/feetech-ft5-r5/robotd --help
/opt/robot/feetech-ft5-r5/robotd deployment-info
env ORT_DYLIB_PATH=/opt/robot/feetech-ft5-r5/onnxruntime/libonnxruntime.so.1.28.0 /opt/robot/feetech-ft5-r5/robotd validate-policy /opt/robot/feetech-ft5-r5/models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx
```

`validate-policy` 加载模型并做一次预热推理，不进入控制循环。每个通用 ONNX 模型可用相同命令独立验证，不使用 `init`、`enable-policy`、`bus` 写命令或运动模式。

服务由父级安装器启动后，读取状态：

```sh
systemctl is-enabled robotd.service
systemctl is-active robotd.service
/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock bus-status
```

最后一条调用现有服务的 `robot.busStatus`，不请求动作。通用载荷的初始预期是未配置串口的只读等待；不能把等待状态写成采集成功。`bus_monitor.py` 的完整 300 秒验收要求真实 16 个设备和 50 Hz，初装未接硬件时不要运行或宣称通过。当前这块板后续已完成接线和 UART 适配，实际记录见父级 `hardware/README.md`。

Git 中只提交配置、服务和 monitor 源码；ELF、ONNX 和 ONNX Runtime 随 Release 运行包提供。原始来源路径必须显式传入，不依赖个人工作站目录。完整 robotd 构建工程不在此迁移目录内，固定摘要用于核对预构建程序来源。
