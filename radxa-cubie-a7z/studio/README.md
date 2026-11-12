# Cubie A7Z Studio 安装记录

## 2026-11-06 14:34 后：网页虚拟手柄接通

用户已完成舵机和 IMU 标定，明确要求使用网页虚拟手柄，并确认扶稳鸭子、允许短暂卸力重启。

- 修复 `--configd none` 仍被 `default_pad` 构造成 RPC 地址的问题：它会令 `pad-watch` 抛 `ValueError`，显示“认不出有没有实体手柄”。补丁在 `a7z-disabled-configd-pad.patch`，只把禁用服务映射为 `None`；正常 configd 和实体手柄让位规则保留。
- 已部署并校验新 `pad.py`，先仅重启 Studio，主控 PID5314 和标定均未改变。旧文件保存在板端 `/var/lib/robot/before-vpad-configd-20261106T063223349819Z/pad.py`。
- 现场主控此前已是 motion 模式，但 `[policy] enabled=false` 导致所有模型不加载。行走、起坐模型各自通过正式 `robotd validate-policy` 推理校验；随后先确认卸力，仅将该配置改为 true，再重启主控至 PID9174。模型加载成功，重启结束时策略未启动、扭矩确认关闭，标定文件未修改。配置备份在板端 `/var/lib/robot/before-load-walking-20261106T063418908538Z`。
- 真实浏览器执行“站起来”得到主控接受回包；随后观察到手机操作、行走模型执行、页面“站着，准备好走了”。没有由代理发送前进速度；用户随后现场确认“已经能迈步了”，虚拟手柄到实物迈步链路已验证。长时间稳定行走尚未验收。实体手柄状态和实体 Select 急停仍未核对，网页无需连接实体手柄。
- 13 项适配回归、159 项原手柄测试通过。159 项首次在沙箱内有 5 项因 localhost 绑定权限失败，允许本机测试端口后全通过。
- 总线偶发故障尚未解决：重启后曾有 ID33 状态错误 `0x02`；主板在模型执行后约 60–63°C 出现降频。两个 10 秒观测窗口各无新增读取失败，不能替代五分钟稳定性或运动验收。

下面保留首次只读安装时的历史记录，不能作为当前运行模式。

2026-11-06，按用户要求安装到 `cubie-XXXX` / `BOARD_IP`。

入口：<http://BOARD_IP:8000/#/servos>。同一局域网打开，按舵机页流程由用户手动校准。Studio 已设为开机自启。

安装目录 `/opt/microduck-studio/app`，独立 Python 在 `/opt/microduck-studio/python`；系统 Python 3.9 未替换。状态和日志分别在 `/var/lib/microduck-studio`、`/var/log/microduck-studio`。

源码来自 `fanhao375/microduck-replica` 的 `f682b7bd30d1ef4e6f931db16406b340d4d54fec`，只导出 `software/studio` 后应用本目录补丁，没有修改相邻工程。独立运行环境为 uv 提供的 `cpython-3.13.14-linux-aarch64-gnu`。完整包和校验在本地 `artifacts/` 与 `runtime-build.json`；完整包不进 Git。

## A7Z 兼容范围

- root 助手使用这台 Debian 11 的固定工具路径和独立 Python，认识带写入保护的 `read_only` 主控。
- 舵机页接管时互斥停主控；交还后恢复原只读启动命令，不拉起手柄、不开启运动。
- 当前只读阶段拒绝助手的运动标定提交、IMU 模式重设、主控更新和保持交接；舵机页的手动校准由用户明确点击执行。
- Studio 同步读按每组三颗拆分，避免一次 15 颗应答挤满 64 字节 UART FIFO。这是网页读取路径的适配，不替代 robotd 的 50 Hz 验收。
- 页面显示真实只读快照和接收新鲜度，不把启动扭矩读数当成实时扭矩。IMU ready 不再显示为安装方向已确认。
- 未安装的 configd 不创建轮询任务；只读主控连接时不自动发送身体模式复位通知。

## 验证

- 119 项现有总线助手和舵机测试、159 项网页手柄测试、6 项 A7Z 兼容回归、4 项状态测试通过。手柄测试用本机模拟服务，不操作实物。
- 实机运行 Studio 的真实 `ServoSession`，以 radxa 和服务相同的附加组读取。发送出口只允许 PING / READ / SYNC_READ；225 条读取请求，15 个舵机都在线，15 个扭矩回读均为 0，电机写指令为 0。测试只关串口，助手交还成功，主控恢复 `--read-only`，padd / GameSir 未启动。
- 实际浏览器检查总览和日志，桌面 1440×900、手机 390×844 截图在本地 `evidence/`。页面显示只读主控和 15/15；IMU 安装方向未确认。没有在浏览器执行校准、上力或运动。
- 安装和修正没有替换 robotd 二进制、改写标定文件或改变主控启动模式。交接验证正常停起过一次只读 robotd，验证结束时 PID 为 25987。

## 初次安装时的操作边界

此时可以由用户在舵机页完成硬件零位校准。主程序 `hd1910-calibration.toml` 中随包的零位、方向、限位仍需随后核对；网页第三步“写进主程序”在只读阶段不开放，不会因此自动放行运动。

ID13 的 0x02 排查已按用户要求暂停，原始证据保留在 `control/evidence/id13-status-20261106-01.jsonl`：暂停前记录 11310 个独立采样周期，抓到一次错误字节和寄存器 65 同时为 0x02 的有效帧。不是五分钟验收，也没有宣告故障已修复。用户校准后再复测。

## 查看服务

```sh
sudo systemctl status microduck-studio --no-pager
sudo journalctl -u microduck-studio -n 40 --no-pager
```

不需要为打开网页重启主控。舵机页接管后应通过页面“交还主程序”释放总线。

## 后续 IMU 标定与总览显示修正

用户随后明确要求取消只读模式，在中控完成 IMU 标定。已以原二进制切到 `--commissioning`，确认 phase=ready、torque_state=off、policy_motion_enabled=false，IMU200 数据有效；没有由代理执行 HOME 或启用策略。用户之后自行通过网页完成 IMU 标定、提交标定并操作站姿使能，当前已进入运动模式。

模式切换曾遇到遗留只读覆盖：网页提交移除 commissioning 覆盖后，`zz-cubie-readonly.conf` 又生效，使提交失败。已将只读覆盖移入板端备份，新增 `zz-cubie-base.conf` 提供正常基础启动命令，commissioning 覆盖只负责临时标定。修复当时没有重启主控或改变用户的新标定。用户再次提交后进入正常模式，不再回到 read_only。

总览原来只适配 `native.joints`，标定快照没有完整接入；只读适配还将单轮失败直接扩散成全部不新鲜，运动模式也会把一轮严格采集失败表现为全体离线。显示层现使用带时间上限的近期回读：原生时间与 IPC 接收年龄合计不足 500ms 才显示近期响应；过期显示“等待有效回读”，真实同步读取异常单独提示。原始 `feedback_fresh`、主控同步读判据与运动安全逻辑均不修改。

11 项 A7Z 适配回归与 43 项相关状态/舵机/启动诊断测试通过。真实中控桌面和手机总览均显示15/15；截图为 `evidence/feedback-fixed-desktop.jpg`、`evidence/feedback-fixed-mobile.jpg`。这只验证显示与交互，不表示总线稳定性或行走已通过。

## 舵机状态持续闪烁的刷新间隔修复

2026-11-06 后续修正：此前只调整了显示回读的 500 ms 有效期，却遗漏运动模式的 `robot.busStatus` 仍每 1 秒轮询，造成每次新数据到达后显示 15/15，半秒后又显示“等待有效回读”。首次连接时也有同样的 1 秒空档。

现在各模式和首次连接统一以 10 Hz 查询主控缓存状态，保留 500 ms 过期与 RPC 失败处理，不额外操作串口、不改变主控采集或运动判据。已精确更新板端 `studio/robotd.py` 并仅重启 Studio；部署期间主控 PID9094 未改变，策略关闭、扭矩确认关闭，未修改标定。备份为 `/opt/microduck-studio/before-feedback-poll-20261106T072136097847Z/robotd.py`。

14 项适配测试和 78 项后端/舵机/启动状态测试通过。新增回归实际运行轮询调度，覆盖三种模式、首次连接和停止回读后的过期。原后端防忙循环测试同步调整为 10 Hz 预期，并继续断言失败查询退避。

真实实机浏览器修复前 60 次采样中有 35 次等待回读、25 次 15/15；修复后 1440×900 和 390×844 各 200 次采样全部为 15/15，等待回读均为 0。截图和原始采样保存在 `evidence/feedback-poll-*-20261106.*`。这是显示层验收，不代表总线长期稳定性或运动验收。

## 关节跟踪调试入口（待板端安装）

舵机页新增“关节跟踪调试”。Studio 已接管串口、所有关节确认卸力，且操作者架稳鸭子的躯干、让双脚离地并清空活动范围后，在页面勾选关节，确认支撑，点“开始测试”。默认 ±3°，慢速斜坡与 0.25/0.5 Hz 正弦；可选左右同类关节反相配合。基础测试可一次选多颗，程序按顺序逐颗测；勾选“临时调整 P/D”时每轮只选一颗，空白参数保留当前值。请先采集原参数基线，再只改一个参数复测相同动作。结束自动卸力并恢复原 RAM P/D。页面记录目标角和位置、速度、负载、温度、电压、错误、总线 TX/RX 数据与时间戳，展示曲线、各阶段误差及历史对照，并可下载原始 `.jsonl` 和分析 `.json`。这些是串口寄存器与控制命令的记录，不是舵机线上的电压波形。

调试测得的是**空载跟踪**；它可定位明显滞后、振荡、总线异常，不能证明带载步态稳定。脚互踩等问题仍要复核机械零位、装配、供电和实际走路日志。程序不自动选 Kp/Kd，不写 EEPROM 或标定文件，不改 robotd 模型。按停止、急停、交还、离开页面或页面连接失联时会停止目标写入并尝试卸力、恢复 P/D；若回读无法确认，页面会显示失败，须现场核对扭矩与参数。

本地源码由 `a7z-leg-tracking.patch`、`tracking/studio/leg_tracking.py`、`tracking/web/leg-tracking.js` 及 `control/diagnostics/` 两个分析/探针模块组成。构建脚本只接受 `tracking-baseline.json` 中审过哈希的 A7Z Studio 源文件，输出 8 个文件的更新包。板端安装脚本默认预览，`--apply` 才会在主控策略关闭、扭矩确认关闭且 Studio 未接管总线时备份、仅重启 Studio；会校验主控 PID、二进制与标定文件不变，失败自动回滚 Studio 文件。完整包仍不进 Git。

```sh
python3 radxa-cubie-a7z/studio/prepare_tracking.py /path/to/clean-A7Z-studio-source radxa-cubie-a7z/studio/artifacts/leg-tracking-bundle
```

在已有的 A7Z 终端登录会话里，把生成的 `.tar.gz` 和 `.sha256` 传到板端并校验，解包后先运行预览，再按现场状态使用 `--apply`。不要跳过脚本的哈希或扭矩检查。

```sh
sha256sum -c leg-tracking-bundle.tar.gz.sha256
mkdir -p /tmp/leg-tracking-bundle
tar -xzf leg-tracking-bundle.tar.gz -C /tmp/leg-tracking-bundle
python3 /tmp/leg-tracking-bundle/install_tracking.py /tmp/leg-tracking-bundle
sudo python3 /tmp/leg-tracking-bundle/install_tracking.py /tmp/leg-tracking-bundle --apply
```

本地已通过探针、分析器、Studio 协议级模拟舵机与 A7Z 适配测试；桌面和窄屏页面仅能在本机后端验收。**尚未在板端安装或让实物执行这套调试动作。**
