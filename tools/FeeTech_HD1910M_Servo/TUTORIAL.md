# HD1910M 舵机测试工具 —— Rust 实战教学手册

> 本手册配合源码阅读。目标：通过一个“真实能控制硬件”的项目，
> 学会 Rust 的核心概念，同时掌握飞特（Feetech）FT-SCS 串口舵机协议。
>
> 硬件：HD1910M 舵机（6V 12kg·cm、360° 磁编码、TTL 半双工串口、带力控）
> 软件：Rust 1.98+ / 树莓派 / CH340 USB 转串口

---

## 目录

1. [硬件连接](#1-硬件连接)
2. [五分钟上手](#2-五分钟上手)
3. [FT-SCS 通信协议详解](#3-ft-scs-通信协议详解)
4. [内存表详解](#4-内存表详解)
5. [项目结构与分层设计](#5-项目结构与分层设计)
6. [Rust 知识点精讲](#6-rust-知识点精讲)
7. [命令完整参考](#7-命令完整参考)
8. [实验教程（动手做）](#8-实验教程动手做)
9. [安全注意事项](#9-安全注意事项)
10. [常见问题排查](#10-常见问题排查)

---

## 1. 硬件连接

舵机引出线（AMP2.0-3P 连接器）：

| 引脚 | 线色 | 功能 | 接到哪里 |
|------|------|------|----------|
| 1 | 黑 | Signal（TTL 串口信号） | CH340 的 TX 与 RX（半双工，见下） |
| 2 | 黑 | Vcc（4~8.4V，典型 6V/7.4V） | **外部电源正极**（不能靠 USB 供电！） |
| 3 | 黑 | GND | 外部电源负极，**必须与 CH340 的 GND 共地** |

关键点：

- **半双工**：舵机只有一根信号线，收发都走它。CH340 是全双工的（TX/RX 分开），
  简易接法是把 CH340 的 TX 和 RX 短接后一起接到舵机信号线 —— 代价是自己发出去
  的数据会被自己收回来（“回显”），这时运行程序要加 `--echo` 参数丢弃回显。
  更规范的做法是用飞特官方的半双工转接板（URT-1 等），则不需要 `--echo`。
- **共地**：舵机 GND、电源 GND、CH340 GND 三者必须连在一起，否则通信不可靠。
- **供电**：堵转电流可达 1.6A（6V 时），USB 口带不动，务必用独立电源。
- 树莓派上 CH340 通常识别为 `/dev/ttyUSB0`（本工具默认值）。

---

## 2. 五分钟上手

```bash
# 构建（第一次会下载依赖并编译，产物在 target/debug/）
cargo build

# 以后每次这样运行（cargo run 会先编译再执行，-- 后面是给我们程序的参数）
cargo run -- scan

# 也可以直接运行编译好的二进制
./target/debug/feetech-HD1910M-tester scan

# 1. 扫描总线上的舵机（带进度条）
./target/debug/feetech-HD1910M-tester scan

# 2. 读取整张内存表
./target/debug/feetech-HD1910M-tester dump

# 3. 实时监控（位置/速度/电压/温度/电流，Ctrl+C 停止）
./target/debug/feetech-HD1910M-tester monitor

# 4. 转到 90° 位置（1024 ≈ 90°，中位是 2048）
./target/debug/feetech-HD1910M-tester goto 1024

# 5. 查看所有命令
./target/debug/feetech-HD1910M-tester --help
./target/debug/feetech-HD1910M-tester pid --help   # 查看某个子命令的帮助
```

全局参数（放在子命令前后都可以）：

```
-p / --port     串口路径，默认 /dev/ttyUSB0
-b / --baud     波特率，默认 1000000
-i / --id       舵机 ID，默认 1
-t / --timeout  超时毫秒数，默认 50（扫描时可调小到 20 加速）
-v / --verbose  打印收发的原始报文（学习协议神器！）
    --echo      TX/RX 短接接法时开启，丢弃回显
```

---

## 3. FT-SCS 通信协议详解

对应源码：`src/protocol.rs`

舵机出厂串口配置：**1Mbps、8 数据位、无校验、1 停止位**（规格书 7-2 节）。
协议是经典的“主从问答式”：主机（树莓派）发一条指令包，舵机回一条应答包。

### 3.1 主机下发包

```
 字节:    0     1     2      3         4         5..N+4     N+5
 内容:  0xFF  0xFF   ID   Length  Instruction   参数...   Checksum
```

- **帧头** `0xFF 0xFF`：固定两个字节，接收方靠它对齐一帧的起点；
- **ID**：目标舵机站号 0~253；`0xFE` 是广播地址（所有舵机执行，但都不应答）；
- **Length** = 参数个数 N + 2（指令 1 字节 + 校验 1 字节）；
- **Checksum** = `~(ID + Length + Instruction + 所有参数)` 取低 8 位。
  接收方重新计算，不一致说明传输出错，整包丢弃。

### 3.2 舵机应答包

格式与下发包几乎一致，只是第 4 字节由“指令”换成了“**错误状态字**”：

```
 0xFF  0xFF   ID   Length   Error   参数...   Checksum
```

Error 为 0 表示正常；非 0 时按位解读（BIT0 电压 / BIT1 磁编码 / BIT2 温度 / BIT3 电流）。

### 3.3 指令集

| 指令 | 值 | 用途 |
|------|-----|------|
| PING | 0x01 | 握手，判断舵机是否在线 |
| READ | 0x02 | 读内存表，参数 = [起始地址, 字节数] |
| WRITE | 0x03 | 写内存表，参数 = [起始地址, 数据...]，立即生效 |
| REG WRITE | 0x04 | 异步写：先缓存，不执行 |
| ACTION | 0x05 | 触发所有缓存的异步写（多舵机同步动作的关键） |
| RESET | 0x06 | 恢复出厂设置 |
| SYNC WRITE | 0x83 | 一条报文给多个舵机写同一地址 |

### 3.4 一个真实例子

用 `-v` 参数可以看到实际收发的报文，例如 `goto 911 --speed 500`：

```
>> 发送: [FF, FF, 01, 05, 03, 2E, F4, 01, D3]   # WRITE 地址46(速度) = 0x01F4 = 500
<< 接收: [FF, FF, 01, 02, 00, FC]               # 应答：Error=0 正常
>> 发送: [FF, FF, 01, 04, 03, 28, 01, CE]       # WRITE 地址40(扭矩开关) = 1
>> 发送: [FF, FF, 01, 05, 03, 2A, 8F, 03, 3A]   # WRITE 地址42(目标位置) = 0x038F = 911
```

动手验证校验和：`0x01+0x05 = 0x06，+0x03 = 0x09，+0x2E = 0x37，+0xF4 = 0x12B，+0x01 = 0x12C`；
只保留低 8 位得 `0x2C`，按位取反 `!0x2C = 0xD3` —— 与报文最后一字节完全一致 ✔

> **练习**：打开 `src/protocol.rs` 底部的单元测试（`cargo test`），
> 里面有 PING 报文的完整断言，试着手算验证。

---

## 4. 内存表详解

对应源码：`src/registers.rs`

内存表是舵机内部的“参数登记表”，每个地址存一项参数。**两字节数据低字节在前（小端）**。

### 4.1 五个分区

| 地址 | 分区 | 权限 | 说明 |
|------|------|------|------|
| 0~4 | 版本信息 | 只读 | 固件/舵机版本号、字节序标志 |
| 5~39 | EPROM 配置 | 读写 | ID、波特率、PID、保护参数……**可掉电保存** |
| 40~55 | SRAM 控制 | 读写 | 扭矩开关、目标位置/电流/速度……实时生效、掉电丢失 |
| 56~75 | SRAM 反馈 | 只读 | 当前位置/速度/负载/电压/温度/电流 |
| 77~86 | 出厂参数 | 只读 | 工厂标定值，不要动 |

### 4.2 锁标志（55 号地址）—— EPROM 掉电保存机制

这是最容易踩坑的机制：

- 锁 = **1（出厂默认）**：写 EPROM 区域（5~39 号地址）只改内存中的值，**掉电后恢复原值**；
- 锁 = **0**：写 EPROM 区域的值会真正烧入 EPROM，**掉电保存**。

所以“持久化一个配置”的标准流程是：**解锁 → 写入 → 重新上锁**。
本工具的 `set <寄存器> <值> --save`、`set-id`、`set-baud` 都自动走这个流程
（见 `src/servo.rs` 的 `save_reg` 函数）。

> 为什么不能一直保持解锁？EPROM 有擦写寿命（约 10 万次），
> 上锁能防止程序 bug 导致的疯狂擦写损坏舵机。

### 4.3 符号-幅值编码 —— 负数不是补码！

飞特协议里带方向的量（位置、速度、电流、负载、位置偏移）**不是**用计算机常见的
二进制补码表示负数，而是用“**符号位 + 绝对值**”：

- 大部分量：**BIT15 是方向位**，低 15 位是绝对值。例如 `-100` 编码为 `0x8000 | 100 = 0x8064`；
- 当前负载（60 号地址）特殊：**BIT10 是方向位**，低 10 位是绝对值。

如果按补码去解码 `0x8064`，你会得到 -32668 而不是 -100 —— 彻底错误。
本工具在 `src/registers.rs` 的 `decode_value`/`encode_value` 里统一处理，
每个寄存器的 `sign_bit` 字段声明了它用哪一位做符号位。

### 4.4 运行模式（33 号地址）

| 值 | 模式 | 说明 |
|----|------|------|
| 0 | 位置伺服模式 | 位置闭环 + 目标电流限力（可力控） |
| 1 | 电机恒速模式 | 速度闭环，负载增加速度不降（轮式驱动用） |
| 2 | 电机恒流模式 | 电流闭环，输出恒定力矩 |
| 3 | PWM 开环调速 | 占空比调压，无闭环，转速随负载波动 |
| 4 | 纯位置 PD 模式 | 比例-微分位置闭环（Sim2Real，**本机出厂默认**） |

### 4.5 PID 参数的两套地址

这是内存表设计中最精妙的一处：

- **EPROM 配置区**（21/22/23 位置环 P/D/I，37/39 速度环 P/I，34/35 电流环 P/I）：
  存“出厂默认值”，**上电时**被拷贝到运行区；
- **SRAM 运行区**（50 Kp / 51 Kd / 52 Ki）：**运行时真正生效**的值，改了立即起作用，掉电丢失。

调参的正确姿势：先用 `pid position --kp 40 --ram` 在运行区反复试，
满意后再 `pid position --kp 40 --save` 写进 EPROM 永久保存。

注意运行区 50/51/52 的“含义随模式切换”：位置伺服模式下是位置环 PID；
恒速模式（模式 1）下变成速度环 PI（Kd 无效）。

### 4.6 保护类寄存器

- 13 号 最高温度上限（默认 80°C，超温关扭矩）；
- 14/15 号 最高/最低输入电压（0.1V 单位）；
- 28 号 保护电流（6.5mA 单位）+ 38 号 过流保护时间（10ms 单位）：
  电流超限持续这么久就进入过流保护（重新发位置指令可清除）；
- 19 号 卸载条件 / 20 号 LED 报警条件：**位掩码**，
  BIT0(1)=电压 BIT1(2)=磁编码 BIT2(4)=过热 BIT3(8)=过流，置 1 开启。
  例如同时开电压+过热保护就是 `1+4=5`。

---

## 5. 项目结构与分层设计

```
FT-HD1901M_Servo/
├── Cargo.toml          # 项目清单：包名、版本、第三方依赖
├── src/
│   ├── main.rs         # 第 4 层：命令行定义与分发（clap）
│   ├── servo.rs        # 第 3 层：串口驱动（打开串口、收发、超时处理）
│   ├── protocol.rs     # 第 2 层：协议组包/解包（纯数据，不碰硬件）
│   ├── registers.rs    # 第 2 层：内存表定义（纯数据，不碰硬件）
│   └── error.rs        # 贯穿各层：统一错误类型
└── TUTORIAL.md         # 本手册
```

**分层的意义**：底下两层（protocol、registers）是纯数据转换，不含任何串口操作，
所以可以脱离硬件做单元测试（`cargo test`）。如果所有代码揉在一起，
测试协议逻辑就得真的接一台舵机 —— 这就是“关注点分离”。

依赖关系单向流动：`main.rs → servo.rs → protocol.rs / registers.rs`，
`error.rs` 被所有层使用。绝不出现下层引用上层。

---

## 6. Rust 知识点精讲

按你在源码中遇到它们的顺序讲解。每个知识点都标注了对应的源码位置。

### 6.1 Cargo 与模块系统 —— `Cargo.toml`、`src/main.rs` 开头

- `Cargo.toml` 声明依赖，构建时自动从 crates.io 下载，写进 `Cargo.lock` 锁定版本；
- `mod error;` 声明模块，`use crate::error::Result;` 引入模块里的名字；
- `crate::` 表示“从项目根开始找”，类似绝对路径。

### 6.2 错误处理：Result 与 `?` —— `src/error.rs`

Rust 没有异常。可能失败的函数返回 `Result<T, E>`：

```rust
// 传统写法（啰嗦）
let port = match serialport::new(path, baud).open() {
    Ok(p) => p,
    Err(e) => return Err(ServoError::Serial(e)),
};

// 用 ? 运算符（等价，一行）
let port = serialport::new(path, baud).open()?;
```

`?` 的语义：**失败就转换错误类型并提前 return，成功就取出值继续**。
`#[from]` 注解（thiserror 提供）让 `serialport::Error` 自动转成我们自己的
`ServoError`，所以一个 `?` 就够。

**为什么要自定义错误枚举？** 上层只面对一种错误类型，用 `match` 就能区分
“超时”（可能只是没这个 ID，扫描时属正常）和“校验和错误”（线路有问题）。
见 `servo.rs` 的 `ping()`。

### 6.3 枚举：Rust 的“王牌特性” —— `src/error.rs`、`src/protocol.rs`

```rust
pub enum ServoError {
    Timeout,                        // 不带数据
    Checksum,
    Malformed(&'static str),        // 带一个字符串
    ServoStatus(u8, String),        // 带两个数据
}
```

每个成员可以携带不同类型、不同数量的数据。配合 `match` 的**穷尽性检查**
（漏处理一个成员就编译失败），重构时非常安全：

```rust
match self.transact(id, Instruction::Ping, &[], true) {
    Ok(_) => Ok(true),
    Err(ServoError::Timeout) => Ok(false),  // 超时 = 没有这个 ID，不算失败
    Err(e) => Err(e),                        // 其他错误继续上抛
}
```

### 6.4 Option：消灭空指针 —— `src/registers.rs` 的 `find_register`

“查找可能失败”在 Rust 里返回 `Option<&Register>`（`Some(x)` 或 `None`），
而不是 null。调用者必须显式处理 None 的情况，编译器强制保证：

```rust
let reg = find_register(name).ok_or_else(|| {
    ServoError::InvalidParam(format!("未知寄存器 \"{name}\""))
})?;  // 找不到就把 None 转成 Err 并返回
```

命令行里“可选参数”（如 `goto --speed`）也是 `Option<i64>`：
给了是 `Some(值)`，没给是 `None`，用 `if let Some(s) = speed { ... }` 处理。

### 6.5 所有权与借用：串口独占的语言级保证 —— `src/servo.rs`

Rust 的核心规则：

1. 每个值有且只有一个**所有者**（owner），所有者离开作用域，值被自动释放；
2. 可以有任意多个**不可变借用**（`&T`），或**一个**可变借用（`&mut T`），二选一；
3. 借用不能超过所有者的生命周期。

串口句柄被 `Servo` 结构体**拥有**；所有操作方法都要求 `&mut self`。
编译器因此保证：同一时刻只有一处代码能操作串口。
**“多线程同时写串口导致报文交错”这类在 C 里常见的 bug，在 Rust 里根本编译不过。**

### 6.6 结构体与 impl —— `src/servo.rs`

```rust
pub struct Servo {          // 数据
    port: Box<dyn SerialPort>,
    discard_echo: bool,     // 私有字段（没有 pub），外部不能乱改
    verbose: bool,
}

impl Servo {                // 行为（方法）
    pub fn connect(...) -> Result<Self> { ... }  // 关联函数（类似静态方法/构造函数）
    pub fn ping(&mut self, id: u8) -> Result<bool> { ... }  // &mut self = 可变借用自己
}
```

`&self`（只读借用）/ `&mut self`（可变借用）/ `self`（拿走所有权）三种接收者，
把“这个方法会不会修改对象”写进了函数签名，调用方一眼可见。

### 6.7 trait 与 trait 对象 —— `Box<dyn SerialPort>`

trait 类似 Java/Go 的接口。`serialport` 库在 Linux/Windows/macOS 上串口是不同类型，
但都实现了 `SerialPort` trait。`Box<dyn SerialPort>` 表示
“堆上一个实现了该 trait 的值”，我们只通过 trait 定义的方法使用它。
这让代码跨平台且不依赖具体类型。

### 6.8 迭代器与闭包 —— 散落各处

```rust
// protocol.rs：折叠求和（闭包 |acc, &b| ... 捕获环境）
let sum = bytes.iter().fold(0u8, |acc, &b| acc.wrapping_add(b));

// main.rs：map + collect 把 Vec<u8> 变成 Vec<String> 再 join
let list: Vec<String> = found.iter().map(|id| id.to_string()).collect();

// main.rs：逗号分隔字符串解析成 Vec<u8>
let ids: Result<Vec<u8>, _> = ids.split(',').map(|s| s.trim().parse::<u8>()).collect();
```

迭代器是**零成本抽象**：编译后等价于手写 for 循环，没有运行时开销。

### 6.9 derive 宏与 clap —— `src/main.rs`

```rust
#[derive(Parser)]                    // 让 clap 自动实现参数解析
struct Cli {
    /// 这一行文档注释会自动变成 --help 里的帮助文字！
    #[arg(short, long, default_value_t = 1)]
    id: u8,
}
```

`#[derive(...)]` 是“派生宏”：编译期由宏自动生成代码。
本项目中 `Debug`（自动实现调试打印）、`Clone/Copy`（自动实现拷贝）、
`Error`（thiserror 自动实现错误 trait）、`Parser/Subcommand/ValueEnum`（clap）都是它。

### 6.10 单元测试 —— 各文件底部 `#[cfg(test)] mod tests`

```rust
#[cfg(test)]           // 只在 cargo test 时编译，发布版本不含
mod tests {
    use super::*;      // super = 父模块
    #[test]
    fn test_build_ping_packet() {
        assert_eq!(build_packet(1, Instruction::Ping, &[]),
                   vec![0xFF, 0xFF, 0x01, 0x02, 0x01, 0xFB]);
    }
}
```

运行 `cargo test` 即可。纯逻辑层（协议、内存表）的测试不需要硬件。

### 6.11 常用小技巧速查

| 写法 | 含义 | 出处 |
|------|------|------|
| `wrapping_add` | 溢出回绕的加法（校验和需要模 256） | protocol.rs |
| `u16::from_le_bytes([a, b])` | 两字节按小端拼成 u16 | servo.rs |
| `to_le_bytes()` | u16 拆成小端字节数组 | servo.rs |
| `Vec::with_capacity(n)` | 预分配容量，避免反复扩容 | protocol.rs |
| `format!` / `println!` 内联变量 `{value}` | Rust 2021 的字符串插值 | 各处 |
| `.expect("...")` | 确定不会失败时解包，失败 panic 并带消息 | servo.rs |
| `matches!(x, Err(...))` | 判断值是否匹配某个模式 | protocol.rs 测试 |
| `&[u8]` vs `Vec<u8>` | 切片借用 vs 拥有所有权的数组 | 函数参数设计 |

---

## 7. 命令完整参考

> 所有写操作默认**只改内存（掉电恢复）**；EPROM 区域的寄存器加 `--save` 才掉电保存
> （自动执行 解锁→写入→上锁）。这是保护 EPROM 寿命的设计。

### 查询类

```bash
feetech-HD1910M-tester scan                          # 扫描 ID 0~253（带进度条）
feetech-HD1910M-tester scan --from 1 --to 10         # 只扫一部分
feetech-HD1910M-tester dump                          # 打印整张内存表（分 5 区，含单位与说明）
feetech-HD1910M-tester get pos-p                     # 读单个寄存器（支持英文名）
feetech-HD1910M-tester get 21                        # 也支持十进制地址
feetech-HD1910M-tester get 0x15                      # 和十六进制地址
feetech-HD1910M-tester get status                    # 状态字自动翻译成中文
feetech-HD1910M-tester monitor                       # 实时监控（Ctrl+C 停止）
feetech-HD1910M-tester monitor --interval 50 --count 100
```

### 通用写（覆盖内存表所有可写寄存器）

```bash
feetech-HD1910M-tester set <寄存器> <值>             # 写内存（立即生效，掉电恢复）
feetech-HD1910M-tester set <寄存器> <值> --save      # 写 EPROM 并掉电保存
feetech-HD1910M-tester set <寄存器> <值> --defer     # 异步写（缓存）
feetech-HD1910M-tester action                        # 广播触发所有缓存的异步写
feetech-HD1910M-tester swrite 1,2,3 goal-pos 2048    # 同步写：多个舵机同一寄存器
```

例：`set deadband-cw 3`、`set integral-limit 10 --save`、`set min-start-force 20`。

### ID / 波特率 / 锁 / 出厂复位

```bash
feetech-HD1910M-tester set-id 2              # 把 ID 从当前值改为 2（自动保存，改完用 -i 2 连接）
feetech-HD1910M-tester set-baud 4            # 波特率索引 0~7 → 此处改为 115200（保存，重启生效）
feetech-HD1910M-tester lock on               # 上锁：EPROM 写入掉电不保存（默认状态）
feetech-HD1910M-tester lock off              # 解锁：EPROM 写入掉电保存
feetech-HD1910M-tester factory-reset         # 恢复出厂设置（慎用！ID 回 1，波特率回 1M）
```

### 运动控制

```bash
feetech-HD1910M-tester mode 0                        # 位置伺服模式（0~4，含义见 4.4 节）
feetech-HD1910M-tester goto 2048                     # 转到中位（自动开扭矩）
feetech-HD1910M-tester goto 1024 --speed 500 --acc 50
feetech-HD1910M-tester torque on / off / damp        # 扭矩开关 / 松轴 / 阻尼（示教用）
feetech-HD1910M-tester speed 1000                    # 运行速度（恒速模式下 BIT15 决定方向）
```

### 力矩 / 力控

```bash
feetech-HD1910M-tester torque-limit 500      # 转矩限制 50%（0~1000 = 0~100%）
feetech-HD1910M-tester goal-current 100      # 目标电流 ≈ 650mA（单位 6.5mA）
feetech-HD1910M-tester set max-torque 800 --save     # 最大扭矩 80%（EPROM，上电拷贝给48号）
feetech-HD1910M-tester set protect-current 300 --save # 保护电流 ≈ 1.95A（EPROM）
feetech-HD1910M-tester set min-start-force 20 --save  # 最小启动力 2%
```

### PID 调参（三个环）

```bash
feetech-HD1910M-tester pid position --kp 40 --kd 8            # 位置环，写 EPROM 配置（本次有效）
feetech-HD1910M-tester pid position --kp 40 --kd 8 --ram      # 写运行区，立即生效（调参推荐）
feetech-HD1910M-tester pid position --kp 40 --kd 8 --save     # 写 EPROM 并掉电保存（调好后）
feetech-HD1910M-tester pid velocity --kp 60 --ki 20           # 速度环（恒速模式用）
feetech-HD1910M-tester pid current --kp 200 --ki 50           # 电流环（力控核心）
```

### 保护与限位

```bash
feetech-HD1910M-tester protect --max-temp 80                          # 过温阈值 80°C
feetech-HD1910M-tester protect --min-voltage 40 --max-voltage 100     # 电压窗口 4.0~10.0V
feetech-HD1910M-tester protect --overcur-time 200                     # 过流持续 2 秒触发保护
feetech-HD1910M-tester protect --unload 12 --led 12                   # 过热+过流时卸载并闪灯
feetech-HD1910M-tester limits 0 4095 --save                           # 角度限制（多圈模式设 0 0）
feetech-HD1910M-tester offset -100 --save                             # 位置偏移（校正机械零位）
```

---

## 8. 实验教程（动手做）

> 按顺序做，每个实验都有“观察点”。全程建议加 `-v` 看报文。

### 实验 1：通信握手与协议观察

```bash
feetech-HD1910M-tester -v get pos
```

观察点：发送包 `FF FF 01 04 02 38 02 ...` 中 `02` 是 READ 指令、`38` 是起始地址 56、
`02` 是读 2 字节；应答包里能找到当前位置的小端字节。手算校验和验证。

### 实验 2：读懂内存表

```bash
feetech-HD1910M-tester dump
feetech-HD1910M-tester get mode        # 看当前运行模式（出厂 = 4 纯位置PD）
feetech-HD1910M-tester get status      # 状态字的中文解析
```

### 实验 3：扭矩开关与手动示教

```bash
feetech-HD1910M-tester torque off      # 松轴，用手转动舵机
feetech-HD1910M-tester monitor         # 另开一个终端观察：位置随手转动变化，负载≈0
feetech-HD1910M-tester torque damp     # 阻尼模式：再用手转，有明显阻力
feetech-HD1910M-tester torque on       # 恢复扭矩
```

### 实验 4：位置运动与反馈

```bash
feetech-HD1910M-tester monitor --count 100 &          # 后台监视（或另开终端）
feetech-HD1910M-tester goto 1024 --speed 300
feetech-HD1910M-tester goto 3072 --speed 1000
```

观察点：`moving` 标志在运动时变 1；速度先升后降（梯形规划）；
到位后位置稳定在目标 ±1（不灵敏区的作用）。

### 实验 5：PID 调参（运行区，安全可逆）

```bash
# Kp 调小 → 动作变软、到位慢
feetech-HD1910M-tester pid position --kp 8 --ram
feetech-HD1910M-tester goto 1024

# Kp 调大 → 响应快但可能抖动/嗡嗡叫
feetech-HD1910M-tester pid position --kp 80 --ram
feetech-HD1910M-tester goto 3072

# 恢复出厂值（dump 里看到的 pos-p=32），或重新上电自动恢复
feetech-HD1910M-tester pid position --kp 32 --kd 40 --ram
```

### 实验 6：力控（目标电流限制）

```bash
feetech-HD1910M-tester mode 0                 # 位置伺服模式（位置+限流）
feetech-HD1910M-tester goal-current 30        # 目标电流 ≈ 195mA，力气变得很小
feetech-HD1910M-tester goto 2048
# 用手轻轻阻挡舵机：它推不过你，电流被钳在设定值（monitor 里看电流）
feetech-HD1910M-tester goal-current 500       # 恢复（≈3.25A 上限）
```

### 实验 7：保护功能

```bash
feetech-HD1910M-tester protect --max-temp 40  # 温度阈值调低到 40°C
# 用手捂住舵机或等它升温……超过 40°C 后扭矩关闭，get status 显示过热
feetech-HD1910M-tester protect --max-temp 80  # 玩完记得调回来
```

### 实验 8：EPROM 锁机制验证

```bash
feetech-HD1910M-tester set resp-level 1       # 不加 --save：只改内存
feetech-HD1910M-tester get lock               # 看当前锁状态（默认 1）
feetech-HD1910M-tester set min-angle 10       # 改个值
# 给舵机断电重启，再 dump：min-angle 恢复成 0 —— 因为锁=1 没保存
feetech-HD1910M-tester set min-angle 10 --save
# 再断电重启：这次值留住了
feetech-HD1910M-tester set min-angle 0 --save # 实验完恢复
```

### 实验 9：改 ID（多舵机组网的第一步）

```bash
feetech-HD1910M-tester set-id 2               # 当前舵机变成 ID 2
feetech-HD1910M-tester -i 2 dump              # 用新 ID 连接
feetech-HD1910M-tester -i 2 set-id 1          # 改回来（单舵机练习时）
```

### 实验 10：异步写与同步写（多舵机同步，需 2 台以上）

```bash
feetech-HD1910M-tester -i 1 set goal-pos 1024 --defer   # 缓存，不动
feetech-HD1910M-tester -i 2 set goal-pos 3072 --defer   # 缓存，不动
feetech-HD1910M-tester action                            # 广播：两台同时启动！
```

---

## 9. 安全注意事项

1. **堵转会发热**：长时间堵转电流可达 1.6A，温度上升很快。做力控实验时
   用 `torque-limit` 或 `goal-current` 限制输出，monitor 关注温度。
2. **EPROM 寿命**：约 10 万次擦写。调参阶段一律用 `--ram` 或不带 `--save`，
   确定后再保存。不要在循环里 `--save`！
3. **相位寄存器（18 号）**：控制电机方向/编码器方向等底层配置，
   乱改会导致舵机失控乱转。除非你完全理解文档 3.1 节，否则不要碰。
4. **改 ID / 波特率前**：确认你能用新参数重新连上（记下来！）。
   忘了的话用 `factory-reset` 或逐波特率扫描找回。
5. **机械安装**：实验时舵机别带负载悬空甩动，避免打到手或甩飞零件。

---

## 10. 常见问题排查

| 现象 | 排查 |
|------|------|
| `响应超时` | ①舵机供了电没？②GND 共地没？③波特率对吗（默认 1M，改过用 `-b`）？④ID 对吗（`scan` 找）？⑤TX/RX 短接接法要加 `--echo` |
| `串口错误: Permission denied` | 把用户加入 dialout 组：`sudo usermod -aG dialout $USER`，重新登录 |
| `校验和错误` | 线路干扰/接触不良，检查杜邦线；或波特率不匹配 |
| scan 扫不到但确定在线 | 用 `-t 30` 加长超时；逐波特率试：`-b 115200 scan` 等 |
| 舵机报错“过流” | 重新发一次位置指令可清除；长期对策：调低 protect-current 或 torque-limit |
| 改了配置重启后没了 | EPROM 写入要加 `--save`（锁机制，见 4.2 节） |
| 位置读数是怪异的负大数 | 不会发生了——但要知道原因：符号-幅值编码被误当补码（见 4.3 节） |

---

## 附：推荐学习路径

1. 先跑通实验 1~4，对协议和内存表建立直觉；
2. 通读 `src/protocol.rs`（最短，200 行），理解组包/解包；
3. 读 `src/registers.rs`，理解“把文档表格变成代码”的思路；
4. 读 `src/servo.rs`，重点理解 `&mut self`、Result、`?` 的串联；
5. 读 `src/main.rs`，看 clap 声明式 CLI 和 match 分发；
6. 改代码练手：给 `dump` 加“只显示与出厂值不同的寄存器”功能；
   或加一个 `wave` 子命令让舵机正弦摆动并用 monitor 观察。

祝学习愉快！
