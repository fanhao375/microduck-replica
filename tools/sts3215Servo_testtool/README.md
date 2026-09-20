# sts3215Servo_testtool — STS3215 舵机 Rust 测试工具

用 Rust 通过 USB 转 TTL 转接板控制飞特（FEETECH）STS3215 总线舵机的学习/测试项目。
协议为自己实现的最小版 STS/SCS 串口协议，代码集中在两个文件：

- `src/sts.rs` — 协议库：帧打包/校验、ping、读写寄存器、状态解析（`ServoStatus`）
- `src/main.rs` — 命令行工具：扫描、读取信息、运动、单个测试、批量测试

## 硬件接线

```
USB 转接板(如飞特 URT-1 / Waveshare Bus Servo Adapter) --TTL--> STS3215 舵机
舵机需要独立供电（7.4V 版本用 7.4V，12V 版本用 12V），不要从 USB 取电！
```

默认串口 `/dev/ttyUSB0`，默认波特率 1,000,000（STS3215 出厂值）。

## 常用命令

```bash
source ~/.cargo/env          # 首次使用加载 cargo 环境
cargo build --release        # 编译
cargo run -- scan                       # 扫描总线上的舵机（带进度条）
cargo run -- info 1                     # 读 ID=1 舵机的位置/电压/温度/电流/负载
cargo run -- read 1 42 2                # 读任意寄存器：ID=1 地址42起2字节(十六进制)
cargo run -- move 1 2048                # ID=1 转到中位 2048
cargo run -- move 1 3072 --speed 800    # 指定速度转动
cargo run -- test 1                     # 完整测试 ID=1：位置扫描+电流/负载采样
cargo run -- test-all                   # 扫描并批量测试所有舵机，输出汇总表
cargo run -- raw 1                      # 底层诊断：打印原始收发字节
cargo run -- --port /dev/ttyUSB1 scan   # 指定串口
cargo run -- --baud 115200 scan         # 指定波特率
```

## 实测记录

- 出厂默认：ID=1，波特率 1M，型号读数 777
- 供电务必匹配版本：7.4V 版(C001)电压上限约 8.0V，接 9V 会触发**过压报警**（LED 闪烁、状态码 0x01）；12V 版(C018)则用 12V
- 位置精度实测：目标/实际偏差仅 1 步（约 0.09°）

## 测试内容（test / test-all）

1. ping 应答 + 读取型号
2. 基线读数：电压、温度、位置
3. 位置扫描：中位 2048 → 1024 → 3072 → 2048，运动中每 20ms 采样一次
4. 统计峰值电流(mA)、峰值负载(%)、最大位置偏差
5. 判定 PASS/FAIL（电压异常、报警位、偏差超限、温升过大都会标出）

## 自己写测试脚本

在 `main.rs` 里照着 `test_one()` 写即可，核心 API：

```rust
let mut bus = StsBus::open("/dev/ttyUSB0", 1_000_000)?;
bus.ping(1)?;                      // 是否在线
bus.move_to(1, 2048, 1000, 0)?;    // 转动：id, 位置, 速度, 时间
let s = bus.read_status(1)?;       // 一次读回全部状态
println!("{}V {}mA {}°C", s.voltage, s.current_ma, s.temperature);
bus.set_torque(1, false)?;         // 释放力矩（可手掰）
```

寄存器地址见 `sts.rs` 里的 `reg` 模块，完整定义查飞特《STS3215 串口协议手册》。

## 常见问题

- **权限拒绝**：`sudo usermod -aG dialout $USER` 后重新登录
- **扫描不到舵机**：检查供电（舵机灯是否亮）、波特率（`--baud 115200` 试试）、TX/RX 是否接反
- **校验和错误**：总线上有干扰或线太长，缩短线缆、检查接地
