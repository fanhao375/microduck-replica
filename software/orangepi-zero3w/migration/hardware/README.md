# 原 HAT 的 UART 接入

目标是 Orange Pi Zero 3W A733、官方 Linux `6.6.98-sun60iw2`。用户确认原 `11quanzhi` HAT 直接叠插 40 针，舵机与 Microduck v1.2 总线 IMU 都接在 HAT 上；仅电池供电，USB 已断开。

## 当前配置

- 物理 8 为 PB9/UART0 TX，物理 10 为 PB10/UART0 RX，设备为 `/dev/ttyS0`。
- `orangepiEnv.txt` 设置 `console=display`、`earlycon=off`，屏蔽 `serial-getty@ttyS0`。
- `orangepi-hat-uart0.dts` 给现有 active/sleep pinctrl 补上 PB9/PB10，并在 UART0、UART1 两个消费者节点分别请求共享 APB UART 48MHz。保留原来的单门控时钟，不写 MMIO。
- 重启实测 UART0/1 驱动缓存与 CCF 实际频率均 48MHz，PLL 仍为 2400MHz/480MHz。读流运行后 `ctrl_info` 的分频为 3，实际波特率 1000000。
- robotd、Studio 和限定助手的串口配置一致为 `/dev/ttyS0`；robotd 保持 `--read-only`，策略关闭，未校准模板未改动。
- 最终启用 `CUBIE_A7Z_SYNC_GROUP_SIZE=3`：同一条物理总线上每请求最多 3 个设备，每周期 6 次请求覆盖 15 舵机与 IMU。

## 单次读取实验与回退

用户要求先尝试全部 16 设备一次 Sync Read，并授权不稳定时恢复分组。程序只接受变量为 `3` 或不存在；不能设 `16`。取消变量时，现有二进制直接使用单次请求，无需重编译。

两次相同的 60 秒观测：

- 原 IRQ 分配：3000 周期，3 次通信失败，UART overrun 增 3；TX 24.04 字节/周期。
- 仅将 UART0 IRQ 临时移到 CPU6：3000 周期，7 次通信失败，UART overrun 增 9；TX 24.008 字节/周期。

因此恢复分组，IRQ 也恢复原来的 `0-7`，没有安装 IRQ 调优服务。单次模式的 unit 已保存在板端 `robotd.single-read.service`，正式 unit 内保留单次方案说明。程序 build 名仍包含 `sync3.1`，它表示包含可选分组补丁，不能单凭 build 名判断分组是否启用。

恢复后同样观测 60.16 秒、3005 个周期：通信失败、IMU 错误及 UART overrun/parity/frame 增量均为 0，TX 64.064 字节/周期，全部观测均包含 15 舵机与 ready/valid 的 IMU。这是本次有限窗口的结果，不等于长期或运动负载验收。

观测器 `verify_single_read.py` 只读现有 RPC 与驱动 `ctrl_info` 缓存，不成为第二个串口 owner。不要读同目录 `status` 属性，它会访问接收寄存器。调用不带参数验证单次模式，`--grouped` 验证分组模式。每种模式均观测 60 秒，报告保存到 `/var/lib/orangepi-migration/`。

## 实施脚本与备份

`install_boot_uart.py source.dts` 只在临时目录编译和模拟合并；加 `--apply` 才备份并写入启动配置。它用于原始、尚未适配的目标板，遇到既有配置会停止要求检查，不能当作重复安装命令。

重启核实 48MHz、控制台释放和引脚后，`connect_readonly.py` 才备份并接入只读服务。所有脚本均只针对本板，不在 Mac 上执行。

板端记录：

- `/var/lib/orangepi-migration/hat-uart0-before/`：原启动配置，以及两次未生效方案的 overlay 备份。
- `/var/lib/orangepi-migration/hat-uart0-runtime-before/`：接入串口前的应用配置、unit 与 helper。
- `/var/lib/orangepi-migration/hat-single-read-before-irq.json`、`hat-single-read-after-irq.json`：单次读取对比。
- `/var/lib/orangepi-migration/hat-grouped-read-verification.json`：回退后相同窗口的分组实测。
- `/var/lib/orangepi-migration/hat-uart0-final.json`：最终配置摘要及文件哈希，优先于前面实验阶段的记录。

最初 CCU 节点的 assigned 属性，以及只有 `clock-frequency` 的方案，在本系统中都未使实际时钟改变。随后核对镜像构建日期附近的 UART_NG v1.1.20 源码，确认单 gate clock 时旧驱动会跳过 `clock-frequency`。最终消费者 assigned 属性经重启验证有效；未使用三时钟重配置路径或 ccudbg 写操作。

这些实测只覆盖通信与只读采集，不代表标定、站立、行走或满负载供电已通过。
