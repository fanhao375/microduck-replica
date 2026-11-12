# Cubie A7Z 控制适配

2026-11-06：原程序为 `97d981c2…`、commissioning；用户已手动安装
`1e465420…` / `a7z-sync3.1-readonly`。**曾通过一次五分钟稳定窗口，后续仍有失败，尚未可靠解决。用户后来明确要求取消只读做 IMU 标定，并自行通过中控提交后进入运动模式；模式切换不等于总线或行走验收通过。** 构建名中的 readonly 是历史标签，实际模式以启动参数与 busStatus 为准。
原运行源码位于本机相邻工程 `10fanhao_duck/runtime/`，不是本仓库原有的配网程序。

## 当前证据

- 原程序 30 秒基线：1514 次读取中失败 1 次，UART overrun 同时增加 1。
- 调 governor、移动 IRQ、降低 RX FIFO 阈值、限制唤醒延迟均未达到连续稳定要求，临时设置已恢复。
- 分组只读探针：每组最多 3 台、最多 63 字节回复，IMU 200 最后，所有组共用 20 ms。
- 一分钟：3000/3000 成功，49.9997 Hz，最慢 8.97 ms，UART 错误增量为 0。
- 五分钟：14998 次中 2 次超过 20 ms（最慢 20.48 ms），UART 错误增量仍为 0；**该次不算通过**。
- 上述是静止状态的独立探针结果，不是新 robotd 的最终验收，更不是站立、行走或前滚翻验证。
- 用户安装后的正式只读程序，五分钟监测 15000 次读取有 788 次失败；缺失 ID 集中在第三组 `[31,32,33]` 中的 32、33，UART 错误增量为 0。
- 后续新窗口又出现 1502 次读取失败 398 次。因此不能把“零 UART 溢出”当作修复完成，也不能据此认定 32、33 已损坏。
- 单独测试 ID32 和 ID33：各自 PING 100/100、READ 56/15 100/100，无校验错误；两颗扭矩测试前后均为 0，测试结束原只读进程已恢复。
- 后续独立 C 探针对第三组顺序、组间 0/100/300/1000 微秒间隔、组位置做 9 种对比，每种 300 周期，共 2700 周期全部成功；不能据此认定增加间隔就是修复，因为无额外间隔的对照也通过。
- 使用相同 Rust Bus 的独立探针，阻塞与非阻塞读取各 600 周期，全部成功。随后正常启动原只读服务；未替换二进制，也未修改其配置。
- 重新启动原只读服务后的完整窗口：300.001 秒、15000 次读取全部成功，49.9998 Hz，所有设备超时/缺失及 UART 错误增量均为 0，`passed = true`。会话 `6141-1793936800693682651`，原始记录 `/var/lib/robot/cubie-native-after-io-probe-20261106.jsonl`，本机结果 `evidence/stable-window-after-restart-20261106.json`。未清零或隐藏此前的错误计数；通过的是这个新窗口，不表示根因已修复或运动已验证。
- 用户要求再次复测：保持同一 sync3.1/PID6141/分组3/只读配置，不重启。300.002 秒内读取 15000 次，14999 成功、1 次失败，49.9996 Hz，`passed = false`。这次是 ID13 返回状态错误 `0x02`，不是完全没有应答；接收校验及 UART 错误增量为 0，32/33 无新增超时。原始记录 `/var/lib/robot/cubie-sync31-retest-20261106-01.jsonl`，本机记录 `evidence/sync31-retest-20261106-01.jsonl` 和对应 `.summary.json`。错误位含义尚未据 HD-1910 对应手册确认，不据此认定舵机损坏。
- 用户在 Studio 校准并交还后再次要求五分钟复测：sync3.1、分组3、只读、PID4411，同一会话不重启。300.004530 秒 / 15000 周期 / 49.999245 Hz，12950 成功、2050 失败，`passed = false`。失败为 ID33 超时44次、ID34超时2006次；ID13没有新增缺失/超时或0x02证据，UART硬件错误增量全0。前组失败时未请求ID13，故其实际覆盖为12950次完整成功读取、另2050周期跳过，不能据此宣告彻底修复。TX918516 / RX4821978字节与这些分组结果完全吻合。证据 `evidence/postcal-50hz-20261106-01.jsonl` 和对应 `.summary.json`。
- 同轮核对用户13:33完成标定的结果：Studio记录15颗全部校到2048；主程序文件原先仍保留随包零位（ID13为1908、ID34为1372）。按用户要求仅将 `/etc/robot/feetech-ft5/hd1910-calibration.toml` 的15个 `zero_raw` 同步为2048，原子写入并逐项读回；所有方向、限位及其他配置保持原值，`motion_enabled` / `imu_mount_verified` / `units_verified` 仍为false。备份和来源记录在 `evidence/postcal-zero-sync.applied.json` 指向的板端目录，主控PID4411未改变，未向舵机发写指令。当前只读运行不加载运动标定，不将磁盘更新描述为运动模式验收通过。

原始结果在板上 `/var/lib/robot/cubie-sync50-*.jsonl`；本机汇总在被 Git 忽略的 `evidence/`。
分组会改变各关节与 IMU 的相对采样时间，模型、位置指令与控制频率未改，运动效果仍须现场验证。

## 文件

- `runtime-a7z-sync3.patch`：原运行库默认行为保留；设置 `CUBIE_A7Z_SYNC_GROUP_SIZE=3` 后，对 56/15 遥测启用分组。共享一个 20 ms 截止时间，不重试，不接受不完整样本，不把尚未请求的设备算作超时。其他寄存器读写不分组。
- `prepare_runtime.py`：从 `d1a2c62efdfb8c7a446ac99f562849e5a3a9587f` 导出干净副本再应用补丁，不修改其他工程的未提交源码。
- `cubie-servo-uart.py`：启动前设置 PB9/PB10 功能值 2、32 MHz 时钟和 ttyS2 别名，检查型号、控制台冲突和寄存器回读；`--check` 不写寄存器。不会读取会消费 RX 字节的 UART RBR 或驱动 `status` 节点。
- `bus_monitor.py`：只调用 `robot.busStatus` 并读取 UART 统计。验收需要至少 300 秒、50 Hz、16 个设备、同一进程会话、失败和 UART 错误计数均不增长；任何中途失败使整个窗口不通过。
- `sync_probe.py`：只编码固定 0x82/56/15 请求的实验工具。必须由外层监督程序暂停并保证恢复原进程；不要和运行中的 robotd 并发使用 UART。
- `sync_matrix.c`：独立只读时序对比工具，仅编码 0x82/56/15；对比头部组三种顺序、全组不同间隔及组位置。它不替换正式程序，仍须监督暂停/恢复原进程。
- `diagnostics/a7z-read-probe.rs`：以正式 Bus 库做独立只读探测，比较阻塞/非阻塞文件模式。依赖串口排他锁，需通过监督脚本正常停止已确认的只读服务，结束或失败后保证重新启动原只读服务。
- `install_readonly.py`：校验候选包并备份程序、配置和 unit；仅在旧服务已确认卸力且无运动时替换。新服务固定 `--read-only`，在报文边界拒绝全部舵机写入。清除随包 `motion_enabled`、`imu_mount_verified`，禁用策略，不伪造 `units_verified`。

旧 commissioning 的正常退出会发送扭矩关闭报文。安装器确认原服务已卸力后，会临时禁止启动并通过 systemd 停止任务发送 SIGKILL，避免停机写入和自动重启。替换后会核对生效的启动命令，并等待运行中的主控确认只读保护。
若替换失败，先检查停止状态和备份再决定如何恢复。备份位置会打印为
`/var/lib/robot/cubie-before-sync3-时间/`。**不要未经核对就恢复并启动运动模式。**

自动审批曾拒绝代理直接执行该部署；用户随后按下列命令自行完成替换。

## 用户手动替换

从 commissioning 切换前，退出 Studio 并停止其他能写入主控的客户端。安装器在停止前复查卸力和空闲状态，预览运行时仍允许其他客户端在复查后发出指令。

在电脑终端从仓库根目录执行；将 `BOARD_IP` 替换为板卡的实际局域网地址：

```sh
cd radxa-cubie-a7z/control/artifacts
scp cubie-a7z-sync3-readonly.tar.gz cubie-a7z-sync3-readonly.tar.gz.sha256 ../install_readonly.py radxa@BOARD_IP:/tmp/
ssh -t radxa@BOARD_IP
```

在登录后的 A7Z 终端执行。校验并解包运行时后，使用随当前源码上传的安装器。`runtime-build.json` 中的安装器摘要对应历史归档内的旧脚本。sudo 交互输入密码。

```sh
cd /tmp &&
sha256sum -c cubie-a7z-sync3-readonly.tar.gz.sha256 &&
mkdir -p cubie-a7z-sync3-readonly &&
tar -xzf cubie-a7z-sync3-readonly.tar.gz -C cubie-a7z-sync3-readonly &&
cp /tmp/install_readonly.py /tmp/cubie-a7z-sync3-readonly/install_readonly.py &&
sudo python3 /tmp/cubie-a7z-sync3-readonly/install_readonly.py /tmp/cubie-a7z-sync3-readonly
```

安装成功后，只做只读验收：

```sh
sleep 5
sudo python3 /usr/local/lib/cubie-control/bus_monitor.py --seconds 300 \
  --output "/var/lib/robot/cubie-native-$(date +%Y%m%d-%H%M%S).jsonl"
```

退出码 0 且最后结果 `passed: true` 才算这一窗口通过；退出码 2 为未通过。
通过也不能沿用随包标定直接运动：还需本机零位与 IMU 安装确认，处理嘴巴范围异常，再接入 padd/GameSir，依次验证站立、行走、起坐与前滚翻。
启动设置已准备为持久化配置，但尚未进行替换后的重启验证。

## 与 Zero 3W 的已知差异

上游运行源码 `packaging/uart-service.py` 并非仅设置波特率：它识别
`fe660000.serial/ttyS2`，在串口打开后把 `rx_trig_bytes` 设为 1，并设置/回读
IRQ 的 CPU3 亲和性，验证成功后才通过 `robot.uartConfigured` 放行采集。
A7Z 当前使用 `uart-ng`、UART0/ttyAS0 别名和 32 MHz 主机时钟适配，不能直接照搬该脚本。
没有同一台鸭子换主控的 A/B 日志，不能断言 Zero 3W 的所有窗口都零错误，也不能断言其 DMA 已开启或 FIFO 更大。

## 构建与测试

```sh
python3 prepare_runtime.py /path/to/Microduck-kissqy /tmp/cubie-runtime
cd /tmp/cubie-runtime/microduck-hd1910/firmware
cargo test --locked -p duck-control a7z_ -- --test-threads=1
cargo board --locked -p robotd
```

需要 Rust 1.99+、cargo-zigbuild、zig 和 aarch64 Linux 目标；目标固定 glibc 2.31。
候选产物校验和及补丁校验和在 `runtime-build.json`，本机二进制和安装包在被忽略的 `artifacts/`。
模型文件沿用板端已有文件，不包含在安装包中。

已完成：本机 20 项工具测试、原有 12 项配网测试；A7Z 虚拟串口上 4 项分组测试、3 项只读模式测试和 1 项 20 ms 边界测试全部通过。
macOS 运行全套飞特测试时有一个 16 ms 人工延迟测试超过截止时间，该测试在 A7Z Linux 上复测通过；未放宽截止时间。

## 左脚内踩排查（2026-11-06）

用户反馈走一会后左脚踩右脚摔倒，ID11 尚未更换。按模型自身左右，左腿 ID20–24，
右腿 ID10–14；当前 ID、方向和限位与随包配置一致，不代表实物装配、零位或带载能力正确。
当前外部风扇吹裸板，没有装机散热片。静止卸力窗口 305.615秒 / 15281次读取有一次
ID11状态错误0x02，最高43.71°C、无降频和UART硬件错误增长，不能把该错误归因于高温。

- `diagnostics/record_motion.py`：只发送 robot.subscribe / busStatus / health，记录目标角、
  实际角、姿态、命令和健康。实物卸力下5秒检查收到98帧（约19.4Hz），没有运动命令；
  此次检查不算步态验证。后续动态试验必须单独确认实际支撑和操作者准备。
- `diagnostics/hip_roll_probe.py`：用户确认架稳、双脚离地后，对ID21、ID11逐颗低速±57步
  （约5.01°），回到各自起点、卸力。仅在主控和Studio均已停止时使用；原运行中P32/D40/I0、
  速度和限力均保留。发包门禁拒绝其他舵机写入、越幅目标、EEPROM、广播、复位等指令。
  仅写两颗的RAM扭矩开关40和目标位置42，不改零位/方向/限位或主程序配置。
- 左ID21停稳误差0.09–0.44°，右ID11为0.26–0.53°，两颗状态均0、最高31°C。
  这不是行走使用P5/D0时的带载测试，不能据此排除机械松动、带载跟随不足或模型/姿态问题，
  也没有证据认定左侧舵机损坏。
- 试验使用systemd限时监督，退出时独立确认卸力，再恢复原主控和Studio。结束主控PID9094、
  策略关闭、全体卸力，程序/配置/标定哈希未变。原始记录与摘要：
  `evidence/crossfeet-hip-roll-20261106-01.jsonl`、对应`.summary.json`。

此前group3独立只读对照因用户准备换舵机而中断，最后保存到3450周期，没有完整result；
不能算五分钟通过，group1对照没有执行。监督器最初误比较含PID/时间的systemd ExecStart元数据，
已改为核对稳定的argv，并保持程序/配置/标定哈希检查；恢复卸力已重新验证。
