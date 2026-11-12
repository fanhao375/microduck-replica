# Orange Pi 只读主控与模型

R2 完整镜像已经预装 `/opt/robot/feetech-ft5-r5`、配置与 `robotd.service`。`bundle/payload/` 在 Git 中只保存配置、服务和 monitor 源码；完整 ELF、ONNX Runtime 与模型随镜像及可选运行包提供。

## 来源和发布摘要

R2 使用的主控标识为 `4734cbb-feetech-imu-auto-control.44-a7z-sync3.1`、官方版本 `0.15.1`、model API 2。为移除个人编译目录，对 ELF `.rodata` 中 226 处源路径字符串进行了等长泛化：

- 来源 `robotd` SHA-256：`04b41ffe5b709e3bee51cafb9dc68cf84813722e796a5aa834f4ab7b83744c6c`
- R2 发布 `robotd` SHA-256：`4df154bfc71eedbfd24a9e11535ad5672b4ba4b3a82109159cd78b8f2831644b`

文件大小、ELF 头和段/节布局保持不变，但发布文件字节及摘要已经改变。Release 的 `path-sanitization.json` 保存改动记录；清理后的程序已通过 Linux ARM64 帮助、全部 10 份模型预热和真实 RPC 检查，最终镜像独立副本中的 RPC 状态为 `read_only/waiting_for_bus`。

`prepare_control.py` 保留最初迁移阶段的来源核验逻辑，其中 `1e465420…` 是历史输入摘要，不能用来判断 R2 发布程序。脚本需要显式提供原始来源路径；本目录没有完整 Rust 工作区，不能单独重新编译主控。

`padd`、`mediad`、`robotctl`、ONNX Runtime、`models/` 和 `roller/` 来自明确引用的 `Microduck-Console-R17-V167.zip` 中 `Microduck-Robotd-FT6-R17.tar.gz`；内层归档摘要为 `bcfd5c183c697b9c4e0fdbd563d995eda6a48331a7c49b9e76d20bd156c65cce`。参数与未校准模板来源为运行时提交 `d1a2c62efdfb8c7a446ac99f562849e5a3a9587f`。10 份 ONNX 权重未修改，训练导出文件已移除，保留的 JSON 构建路径和内网地址已泛化。

程序、ONNX Runtime 和模型各自保留许可证与来源说明。模型采用 kissqy 的个人非商业许可，不属于 robotd 程序的 Apache-2.0 许可范围。

## 开机默认行为

`robotd.service` 已启用自启，使用 `--read-only`、关闭策略，初始串口为不存在的 `/dev/orangepi-robot-uart-unconfigured`。15 个关节模板均为未校准，2048 只是占位值；`motion_enabled`、`imu_mount_verified`、`units_verified` 均为 `false`。没有导入实物零位、IMU 校准或设备会话。

主控先建立 `/run/robotd.sock` 再进入只读采集线程。未配置串口时可显示 `mode=read_only`、`phase=waiting_for_bus` 和真实错误，服务在线不代表硬件健康。Socket 使用 `robot` 组与 `0660` 权限，Studio 具有该附加组。

分组配置为 `CUBIE_A7Z_SYNC_GROUP_SIZE=3`。完整镜像没有启用 Cubie UART MMIO 程序，也没有猜测 UART 别名。`padd` 与 `mediad` 仅预装、不自启。实际 UART0 / HAT 接入按 [hardware 说明](../hardware/README.md)进行；接线完成后仍须独立校准和运动验收。

## 依赖和检查

主程序、辅助程序、ONNX 库均为 AArch64 ELF。完整镜像已安装其系统动态库及工具，包含 libudev、GStreamer、GLib、libstdc++、libgcc、ffmpeg、ALSA 与 V4L2 工具。构建记录 `packages.tsv` 列出实际版本。

本轮在 ARM64 chroot 内通过 `ldd`、主控 `--help`、独立 Python 标准库、sudoers 和服务单元检查，全部 10 份 ONNX 逐一执行 `validate-policy` 均返回 `valid=true`。最终镜像独立副本中，真实主控与 Studio 在原生 ARM64 Linux 运行并由 Chrome 验证只读状态。`validate-policy` 不进入控制循环，不打开 UART，也不代表步态通过。

需要维护时可在设备上执行以下只读检查：

```sh
/opt/robot/feetech-ft5-r5/robotd deployment-info
systemctl is-enabled robotd.service
systemctl is-active robotd.service
/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock bus-status
```

`bus_monitor.py` 的采集验收需要真实舵机、IMU 与实际接线；本轮没有可用实板，未执行该验收或任何运动。
