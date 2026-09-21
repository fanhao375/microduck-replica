//! main.rs —— 命令行入口：解析参数，调用舵机驱动完成测试
//!
//! 【Rust 知识点：二进制目标使用本包的库目标】
//! 本 crate 同时有 src/lib.rs（库，名为 feetech_servo）和本文件（二进制）。
//! 二进制像“外部使用者”一样 `use feetech_servo::...` 引用库里的模块，
//! 协议层、内存表、驱动的实现都收敛到库中，CLI 只保留命令行交互逻辑。

use std::thread;
use std::time::Duration;

use clap::{Parser, Subcommand, ValueEnum};
use indicatif::{ProgressBar, ProgressStyle};

use feetech_servo::error::{Result, ServoError};
use feetech_servo::protocol;
use feetech_servo::registers::{self, find_register, Access, Area, REGISTERS, BAUD_RATES};
use feetech_servo::servo::Servo;

// =============================================================================
// 命令行定义
// =============================================================================

/// 顶层命令行：全局参数 + 一个子命令。
///
/// global = true 表示这些参数放在子命令前后都可以，
/// 例如 `feetech-HD1910M-tester dump -i 2` 和 `feetech-HD1910M-tester -i 2 dump` 都行。
#[derive(Parser)]
#[command(
    name = "feetech-HD1910M-tester",
    version,
    about = "飞特 HD1910M 串口舵机测试工具（FT-SCS 协议）",
    long_about = "覆盖官方内存表全部可读写寄存器的测试工具。\n\
                  出厂默认：ID=1，波特率=1000000，串口=/dev/ttyUSB0。\n\
                  寄存器可用英文名（如 pos-p）、十进制地址（21）或十六进制（0x15）指定。"
)]
struct Cli {
    /// 串口设备路径
    #[arg(short, long, default_value = "/dev/ttyUSB0", global = true)]
    port: String,

    /// 通讯波特率（出厂默认 1000000）
    #[arg(short, long, default_value_t = 1_000_000, global = true)]
    baud: u32,

    /// 舵机 ID（出厂默认 1）
    #[arg(short, long, default_value_t = 1, global = true)]
    id: u8,

    /// 串口收发超时（毫秒）。扫描时建议调小加快速度
    #[arg(short = 't', long, default_value_t = 50, global = true)]
    timeout: u64,

    /// 打印收发的原始报文（学习 FT-SCS 协议时打开它）
    #[arg(short, long, global = true)]
    verbose: bool,

    /// TX/RX 短接的简易接法需要打开：丢弃自收自发的回显
    #[arg(long, global = true)]
    echo: bool,

    #[command(subcommand)]
    cmd: Cmd,
}

/// 所有子命令。
///
/// 【Rust 知识点：带数据的枚举 + Subcommand】
/// 每个成员携带自己的参数结构，clap 把它们映射成子命令；
/// 下面 run() 里用一个 match 就能分发全部命令 —— Rust 的 match
/// 会检查“是否覆盖了所有枚举成员”，漏一个就编译失败，重构时极其安心。
#[derive(Subcommand)]
enum Cmd {
    /// 扫描总线：逐个 PING ID 0~253，列出在线舵机
    Scan {
        /// 起始 ID
        #[arg(long, default_value_t = 0)]
        from: u8,
        /// 结束 ID
        #[arg(long, default_value_t = 253)]
        to: u8,
    },

    /// 读取并打印整张内存表（分区显示，含中文说明和单位）
    Dump,

    /// 读一个寄存器：get pos-p / get 21 / get 0x15
    Get {
        /// 寄存器：英文名 / 十进制地址 / 0x十六进制地址
        reg: String,
    },

    /// 写一个寄存器（默认只改内存；EPROM 区域加 --save 才掉电保存）
    Set {
        /// 寄存器：英文名 / 地址
        reg: String,
        /// 要写入的值（负数也可以，如 pos-offset 允许 -4095）
        value: i64,
        /// 掉电保存（先解锁 EPROM → 写入 → 重新上锁）
        #[arg(long)]
        save: bool,
        /// 异步写：先缓存，之后用 action 命令统一触发（多舵机同步用）
        #[arg(long)]
        defer: bool,
    },

    /// 触发执行所有异步写（REG WRITE）缓存的内容（广播）
    Action,

    /// 同步写：一条报文给多个舵机写同一个寄存器（如 "1,2,3" goal-pos 2048）
    Swrite {
        /// 目标 ID 列表，逗号分隔
        ids: String,
        /// 寄存器：英文名 / 地址
        reg: String,
        /// 要写入的值
        value: i64,
    },

    /// 设置舵机 ID（自动走解锁→写入→上锁流程，掉电保存）
    SetId {
        /// 新 ID（0~253）
        new_id: u8,
    },

    /// 设置通讯波特率（掉电保存；改完需要用新波特率重新连接）
    SetBaud {
        /// 波特率索引 0~7：0=1M 1=500k 2=250k 3=128k 4=115200 5=76800 6=57600 7=38400
        index: u8,
    },

    /// EPROM 写入锁：on=上锁(掉电不保存,默认) / off=解锁(写入掉电保存)
    Lock {
        #[arg(value_enum)]
        state: LockState,
    },

    /// 恢复出厂设置（EPROM 区域全部还原默认值，慎用！）
    FactoryReset,

    /// 设置运行模式：0位置伺服 1电机恒速 2电机恒流 3PWM开环调速 4纯位置PD
    Mode { mode: u8 },

    /// 位置模式：转动到目标位置（自动打开扭矩开关）
    Goto {
        /// 目标位置：0~4095 对应 0~360°（中位 2048；多圈模式可为负数或超 4095）
        target: i64,
        /// 可选：同时设置运行速度（0.732RPM/单位）
        #[arg(long)]
        speed: Option<i64>,
        /// 可选：同时设置加速度（8.7度/秒²每单位，0=最大）
        #[arg(long)]
        acc: Option<i64>,
    },

    /// 扭矩开关：on=打开输出 off=关闭输出(松轴) damp=阻尼模式
    Torque {
        #[arg(value_enum)]
        state: TorqueState,
    },

    /// 设置转矩限制（48号地址，0~1000，单位 0.1%）：限制堵转扭矩输出
    TorqueLimit { value: i64 },

    /// 设置目标电流（44号地址，-2047~2047，单位 6.5mA）：力控核心
    GoalCurrent { value: i64 },

    /// 设置运行速度（46号地址；恒速模式 BIT15 为方向位）
    Speed { value: i64 },

    /// 设置 PID 参数：位置环 / 速度环 / 电流环
    Pid {
        /// 哪个环：position(位置环PDI) velocity(速度环PI) current(电流环PI)
        #[arg(value_enum)]
        pid_loop: PidLoop,
        #[arg(long)]
        kp: Option<i64>,
        #[arg(long)]
        kd: Option<i64>,
        #[arg(long)]
        ki: Option<i64>,
        /// 写运行区(50~52号地址,立即生效掉电丢失)，默认写 EPROM 配置区
        #[arg(long)]
        ram: bool,
        /// 写 EPROM 时掉电保存
        #[arg(long)]
        save: bool,
    },

    /// 实时监控：连续打印位置/速度/负载/电压/温度/电流（Ctrl+C 停止）
    Monitor {
        /// 刷新间隔（毫秒）
        #[arg(long, default_value_t = 100)]
        interval: u64,
        /// 打印次数（不指定则一直打印）
        #[arg(long)]
        count: Option<u32>,
    },

    /// 保护参数：过温/过压/欠压/过流/卸载条件/LED报警
    Protect {
        /// 最高温度上限 °C（13号地址）
        #[arg(long)]
        max_temp: Option<i64>,
        /// 最高输入电压，单位 0.1V（14号地址，如 100 = 10.0V）
        #[arg(long)]
        max_voltage: Option<i64>,
        /// 最低输入电压，单位 0.1V（15号地址，如 40 = 4.0V）
        #[arg(long)]
        min_voltage: Option<i64>,
        /// 过流保护时间，单位 10ms（38号地址，如 200 = 2秒）
        #[arg(long)]
        overcur_time: Option<i64>,
        /// 卸载条件位掩码（19号地址）：BIT0电压 BIT1磁编码 BIT2过热 BIT3过流
        #[arg(long)]
        unload: Option<i64>,
        /// LED报警位掩码（20号地址）：位定义同上
        #[arg(long)]
        led: Option<i64>,
        /// 掉电保存
        #[arg(long)]
        save: bool,
    },

    /// 设置角度限制（9/11号地址，多圈模式两者都应为 0）
    Limits {
        /// 最小角度限制（0~4094）
        min: i64,
        /// 最大角度限制（1~4095）
        max: i64,
        /// 掉电保存
        #[arg(long)]
        save: bool,
    },

    /// 设置位置偏移（31号地址，-4095~4095，用于校正机械零位）
    Offset {
        value: i64,
        /// 掉电保存
        #[arg(long)]
        save: bool,
    },
}

/// 锁开关的取值（clap 自动限制只能填 on/off）
#[derive(Clone, Copy, ValueEnum)]
enum LockState {
    /// 上锁：EPROM 写入掉电不保存（出厂默认）
    On,
    /// 解锁：EPROM 写入掉电保存
    Off,
}

/// 扭矩开关的取值
#[derive(Clone, Copy, ValueEnum)]
enum TorqueState {
    /// 打开扭力输出
    On,
    /// 关闭扭力输出（可以用手转动舵机）
    Off,
    /// 阻尼输出（手转有阻力，常用于示教）
    Damp,
}

/// PID 环选择
#[derive(Clone, Copy, ValueEnum)]
enum PidLoop {
    /// 位置环：P=21号 D=22号 I=23号（运行区 50/51/52）
    Position,
    /// 速度环：P=37号 I=39号（运行区 50/52）
    Velocity,
    /// 电流环：P=34号 I=35号（仅 EPROM 区）
    Current,
}

// =============================================================================
// 程序入口
// =============================================================================

/// 【Rust 知识点：fn main() -> Result】
/// main 也可以返回 Result：返回 Err 时程序以非 0 状态码退出并打印错误，
/// 这样 main 里也能愉快地使用 `?`。
fn main() -> Result<()> {
    let cli = Cli::parse();

    // 扫描命令不需要先连接指定舵机，但同样需要串口
    let mut servo = Servo::connect(&cli.port, cli.baud, cli.timeout, cli.echo, cli.verbose)?;
    if cli.verbose {
        eprintln!("已打开 {} @ {} bps, 目标 ID = {}", cli.port, cli.baud, cli.id);
    }

    // 【Rust 知识点：match 穷尽性检查】
    // 编译器强制我们处理 Cmd 的每一个变体，新增子命令忘了处理会编译失败。
    match cli.cmd {
        Cmd::Scan { from, to } => cmd_scan(&mut servo, from, to),
        Cmd::Dump => cmd_dump(&mut servo, cli.id),
        Cmd::Get { reg } => cmd_get(&mut servo, cli.id, &reg),
        Cmd::Set { reg, value, save, defer } => cmd_set(&mut servo, cli.id, &reg, value, save, defer),
        Cmd::Action => {
            servo.action()?;
            println!("已广播 ACTION，所有缓存的异步写已触发执行");
            Ok(())
        }
        Cmd::Swrite { ids, reg, value } => {
            let reg = find_register(&reg).ok_or_else(|| {
                ServoError::InvalidParam(format!("未知寄存器 \"{reg}\""))
            })?;
            // 解析逗号分隔的 ID 列表："1,2,3" → vec![1, 2, 3]
            let ids: std::result::Result<Vec<u8>, _> =
                ids.split(',').map(|s| s.trim().parse::<u8>()).collect();
            let ids = ids.map_err(|_| ServoError::InvalidParam("ID 列表格式应为 1,2,3".into()))?;
            servo.sync_write(&ids, reg, value)?;
            println!("已同步写 {:?} 的 {}（{}）= {value}", ids, reg.key, reg.cn);
            Ok(())
        }
        Cmd::SetId { new_id } => cmd_set_id(&mut servo, cli.id, new_id),
        Cmd::SetBaud { index } => cmd_set_baud(&mut servo, cli.id, index),
        Cmd::Lock { state } => {
            let v = match state {
                LockState::On => 1,
                LockState::Off => 0,
            };
            servo.write_reg(cli.id, find_register("lock").unwrap(), v)?;
            println!("锁标志已写为 {v}（{}）", if v == 0 { "已解锁：EPROM 写入将掉电保存" } else { "已上锁：EPROM 写入掉电不保存" });
            Ok(())
        }
        Cmd::FactoryReset => {
            servo.factory_reset(cli.id)?;
            println!("已发送恢复出厂设置指令，请重新上电舵机（ID 恢复为 1，波特率恢复为 1Mbps）");
            Ok(())
        }
        Cmd::Mode { mode } => {
            let reg = find_register("mode").unwrap();
            servo.write_reg(cli.id, reg, mode as i64)?;
            const MODES: [&str; 5] = [
                "0:位置伺服模式（位置+限流）",
                "1:电机恒速模式（恒速+限流）",
                "2:电机恒流模式（限流）",
                "3:PWM开环调速模式",
                "4:纯位置PD模式（Sim2Real，出厂默认）",
            ];
            println!("运行模式已设为 {mode} = {}", MODES[mode as usize]);
            println!("提示：模式存在 EPROM，加 --verbose 可观察报文；如需掉电保存请用 set mode {mode} --save");
            Ok(())
        }
        Cmd::Goto { target, speed, acc } => cmd_goto(&mut servo, cli.id, target, speed, acc),
        Cmd::Torque { state } => {
            let v = match state {
                TorqueState::On => 1,
                TorqueState::Off => 0,
                TorqueState::Damp => 2,
            };
            servo.write_reg(cli.id, find_register("torque-switch").unwrap(), v)?;
            let desc = match state {
                TorqueState::On => "已打开扭力输出",
                TorqueState::Off => "已关闭扭力输出（舵机松轴，可手动转动）",
                TorqueState::Damp => "已切到阻尼输出（手动转动有阻力，可用于示教）",
            };
            println!("{desc}");
            Ok(())
        }
        Cmd::TorqueLimit { value } => {
            servo.write_reg(cli.id, find_register("torque-limit").unwrap(), value)?;
            println!("转矩限制已设为 {value}（= {:.1}%）", value as f64 / 10.0);
            Ok(())
        }
        Cmd::GoalCurrent { value } => {
            servo.write_reg(cli.id, find_register("goal-current").unwrap(), value)?;
            println!("目标电流已设为 {value}（≈ {:.0} mA）", value as f64 * 6.5);
            Ok(())
        }
        Cmd::Speed { value } => {
            servo.write_reg(cli.id, find_register("speed").unwrap(), value)?;
            println!("运行速度已设为 {value}（≈ {:.1} RPM）", value as f64 * 0.732);
            Ok(())
        }
        Cmd::Pid { pid_loop, kp, kd, ki, ram, save } => {
            cmd_pid(&mut servo, cli.id, pid_loop, kp, kd, ki, ram, save)
        }
        Cmd::Monitor { interval, count } => cmd_monitor(&mut servo, cli.id, interval, count),
        Cmd::Protect { max_temp, max_voltage, min_voltage, overcur_time, unload, led, save } => cmd_protect(
            &mut servo, cli.id, max_temp, max_voltage, min_voltage, overcur_time, unload, led, save,
        ),
        Cmd::Limits { min, max, save } => {
            cmd_write_eprom(&mut servo, cli.id, "min-angle", min, save)?;
            cmd_write_eprom(&mut servo, cli.id, "max-angle", max, save)?;
            println!("角度限制已设为 {min} ~ {max}{}", if save { "（已掉电保存）" } else { "" });
            Ok(())
        }
        Cmd::Offset { value, save } => {
            cmd_write_eprom(&mut servo, cli.id, "pos-offset", value, save)?;
            println!("位置偏移已设为 {value}{}", if save { "（已掉电保存）" } else { "" });
            Ok(())
        }
    }
}

// =============================================================================
// 各子命令的实现
// =============================================================================

/// 扫描总线（带进度条）
///
/// 【Rust 知识点：使用第三方 crate indicatif】
/// 在 Cargo.toml 里加一行依赖后，`use indicatif::...` 即可使用。
/// ProgressBar 会在终端里实时刷新一行进度条：
///   - ProgressStyle::with_template 定义显示模板（{bar} {pos} {msg} 等占位符）
///   - progress_chars 自定义进度条的填充字符（用 Unicode 块字符更细腻）
///   - enable_steady_tick 让旋转小动画在等待期间也持续转动
///   - pb.println 在进度条上方打印一行，不会弄花进度条
fn cmd_scan(servo: &mut Servo, from: u8, to: u8) -> Result<()> {
    let total = (to - from + 1) as u64;
    let pb = ProgressBar::new(total);
    pb.set_style(
        ProgressStyle::with_template(
            "{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {pos}/{len} ({percent}%) {msg}",
        )
        .unwrap() // 模板是编译期写死的正确字符串，这里 unwrap 是安全的
        .progress_chars("█▉▊▋▌▍▎▏  "),
    );
    // 每 100ms 让 spinner 转一格，否则等待超时时画面是静止的
    pb.enable_steady_tick(Duration::from_millis(100));

    let mut found = Vec::new();
    for id in from..=to {
        pb.set_message(format!("PING ID {id}"));
        if servo.ping(id)? {
            found.push(id);
            pb.println(format!("  ✔ 发现舵机，ID = {id}"));
        }
        pb.inc(1); // 进度 +1
    }

    // 进度条收尾：清除后打印最终结果
    pb.finish_and_clear();
    if found.is_empty() {
        println!("扫描完成（ID {from} ~ {to}）：没有发现任何舵机。请检查：接线 / 供电 / 波特率（-b）/ 串口（-p）");
    } else {
        // 【Rust 知识点：迭代器 map + collect 拼字符串】
        let list: Vec<String> = found.iter().map(|id| id.to_string()).collect();
        println!("扫描完成：发现 {} 个舵机，ID: {}", found.len(), list.join(", "));
    }
    Ok(())
}

/// 读取并打印整张内存表。
///
/// 【性能技巧：块读取】内存表有些地址是空洞（如 10、12、29 号），
/// 逐个寄存器读要发几十条指令；改为按区域连续读 5 个大块，
/// 再从缓冲区里按地址取值，速度快很多。
fn cmd_dump(servo: &mut Servo, id: u8) -> Result<()> {
    // (起始地址, 读取长度)：覆盖 0~4、5~39、40~55、56~75、77~86
    let blocks = [(0u8, 5u8), (5, 35), (40, 16), (56, 20), (77, 10)];
    let mut image = [0u8; 128]; // 内存表镜像，下标即地址
    for (start, len) in blocks {
        let data = servo.read(id, start, len)?;
        if data.len() < len as usize {
            return Err(ServoError::Malformed("块读取长度不足"));
        }
        // 拷贝进镜像缓冲区
        image[start as usize..(start + len) as usize].copy_from_slice(&data[..len as usize]);
    }

    println!("====== HD1910M 内存表（ID={id}）======");
    let mut last_area: Option<Area> = None;
    for reg in REGISTERS {
        // 换区时打印分区标题
        if last_area != Some(reg.area) {
            println!("\n----- {} -----", reg.area.as_str());
            last_area = Some(reg.area);
        }
        let raw = if reg.size == 1 {
            image[reg.addr as usize] as u16
        } else {
            u16::from_le_bytes([image[reg.addr as usize], image[reg.addr as usize + 1]])
        };
        let val = registers::decode_value(raw, reg.sign_bit);
        println!(
            "  [{:>2}/0x{:02X}] {:<12} {:<10} = {:>6} {:<8} [{}] {}",
            reg.addr, reg.addr, reg.key, reg.cn, val, reg.unit,
            reg.access.as_str(), reg.desc
        );
    }
    Ok(())
}

/// 读一个寄存器并友好打印
fn cmd_get(servo: &mut Servo, id: u8, name: &str) -> Result<()> {
    let reg = find_register(name).ok_or_else(|| {
        ServoError::InvalidParam(format!("未知寄存器 \"{name}\"，用 dump 命令查看全部寄存器名"))
    })?;
    let val = servo.read_reg(id, reg)?;
    println!(
        "[{}/0x{:02X}] {}（{}）= {} {}",
        reg.addr, reg.addr, reg.key, reg.cn, val, reg.unit
    );
    // 对位掩码类寄存器附赠按位解释，帮助理解
    match reg.key {
        "status" => println!("  状态解析: {}", protocol::describe_status(val as u8)),
        "unload-cond" | "led-alarm" => println!("  位含义: {}", describe_protect_bits(val as u8)),
        _ => {}
    }
    Ok(())
}

/// 写一个寄存器
fn cmd_set(servo: &mut Servo, id: u8, name: &str, value: i64, save: bool, defer: bool) -> Result<()> {
    let reg = find_register(name).ok_or_else(|| {
        ServoError::InvalidParam(format!("未知寄存器 \"{name}\"，用 dump 命令查看全部寄存器名"))
    })?;
    if reg.access == Access::ReadOnly {
        return Err(ServoError::InvalidParam(format!(
            "{}（{}）是只读寄存器，不能写入", reg.key, reg.cn
        )));
    }
    if defer {
        // 异步写：范围/编码校验逻辑复用 write_reg 之前先做检查，然后走 REG WRITE
        let val = registers::encode_value(value, reg.sign_bit);
        let bytes = if reg.size == 1 { vec![val as u8] } else { val.to_le_bytes().to_vec() };
        servo.reg_write(id, reg.addr, &bytes)?;
        println!("已缓存异步写 {}（{}）= {value}，执行 action 命令后生效", reg.key, reg.cn);
        return Ok(());
    }
    if save {
        servo.save_reg(id, reg, value)?;
        println!("{}（{}）= {value}（已写入 EPROM，掉电保存）", reg.key, reg.cn);
    } else {
        servo.write_reg(id, reg, value)?;
        let tip = if reg.area == Area::Eprom { "（仅本次有效，掉电恢复；加 --save 可保存）" } else { "" };
        println!("{}（{}）= {value}{tip}", reg.key, reg.cn);
    }
    Ok(())
}

/// 写 EPROM 寄存器的辅助函数（limits/offset 等命令共用）
fn cmd_write_eprom(servo: &mut Servo, id: u8, key: &str, value: i64, save: bool) -> Result<()> {
    let reg = find_register(key).unwrap();
    if save {
        servo.save_reg(id, reg, value)
    } else {
        servo.write_reg(id, reg, value)
    }
}

/// 设置舵机 ID —— 最重要的配置操作，必须掉电保存
///
/// 【易错点】写 ID 寄存器成功后，舵机**立即**切换到新 ID 应答，
/// 所以最后一步“重新上锁”必须发给新 ID —— 如果还发给旧 ID，
/// 会得到超时错误（ID 其实已经改成功了，属于误报）。
fn cmd_set_id(servo: &mut Servo, old_id: u8, new_id: u8) -> Result<()> {
    if new_id > 253 {
        return Err(ServoError::InvalidParam("ID 必须在 0~253 之间".into()));
    }
    if new_id == old_id {
        println!("新 ID 与当前 ID 相同（{old_id}），无需修改");
        return Ok(());
    }
    println!("正在把舵机 ID 从 {old_id} 改为 {new_id}（解锁 → 写入 → 上锁）...");
    let lock = find_register("lock").unwrap();
    let id_reg = find_register("id").unwrap();

    // 1) 解锁：让 EPROM 写入掉电保存（发给旧 ID）
    servo.write_reg(old_id, lock, 0)?;
    // 2) 写入新 ID（发给旧 ID；应答一到，舵机立刻只认新 ID）
    servo.write_reg(old_id, id_reg, new_id as i64)?;
    // 3) 重新上锁：必须发给**新 ID**
    servo.write_reg(new_id, lock, 1)?;

    println!("完成！以后请用 -i {new_id} 连接该舵机。例如：feetech-HD1910M-tester -i {new_id} dump");
    println!("注意：如果总线上还有其他 ID={new_id} 的舵机会冲突，请先单独连接再改。");
    Ok(())
}

/// 设置波特率
fn cmd_set_baud(servo: &mut Servo, id: u8, index: u8) -> Result<()> {
    if index > 7 {
        return Err(ServoError::InvalidParam("波特率索引必须在 0~7 之间".into()));
    }
    let baud = BAUD_RATES[index as usize];
    let reg = find_register("baud").unwrap();
    servo.save_reg(id, reg, index as i64)?;
    println!("波特率已设为索引 {index}（{baud} bps）并掉电保存。");
    println!("舵机重新上电后生效，之后请用 -b {baud} 连接。");
    Ok(())
}

/// 位置模式运动
fn cmd_goto(servo: &mut Servo, id: u8, target: i64, speed: Option<i64>, acc: Option<i64>) -> Result<()> {
    // 可选参数：Option<i64>，给了才写，不给不动舵机原有配置
    if let Some(a) = acc {
        servo.write_reg(id, find_register("acc").unwrap(), a)?;
    }
    if let Some(s) = speed {
        servo.write_reg(id, find_register("speed").unwrap(), s)?;
    }
    // 打开扭矩开关，否则舵机不会保持位置
    servo.write_reg(id, find_register("torque-switch").unwrap(), 1)?;
    servo.write_reg(id, find_register("goal-pos").unwrap(), target)?;
    let deg = target as f64 * 0.0879; // 0.087度/单位（文档标称值，360°/4096≈0.0879）
    println!("目标位置 = {target}（约 {deg:.1}°），舵机运动中... 可用 monitor 命令观察");
    Ok(())
}

/// 设置 PID：按“环”选择写入哪些寄存器
fn cmd_pid(
    servo: &mut Servo,
    id: u8,
    pid_loop: PidLoop,
    kp: Option<i64>,
    kd: Option<i64>,
    ki: Option<i64>,
    ram: bool,
    save: bool,
) -> Result<()> {
    // 根据环和写入区域确定寄存器键名
    // （文档：50/51/52 号运行区在位置模式是位置环 PID，恒速模式是速度环 PI）
    let (k_p, k_d, k_i): (&str, Option<&str>, Option<&str>) = match (pid_loop, ram) {
        (PidLoop::Position, false) => ("pos-p", Some("pos-d"), Some("pos-i")),
        (PidLoop::Position, true) => ("kp", Some("kd"), Some("ki")),
        (PidLoop::Velocity, false) => ("vel-p", None, Some("vel-i")),
        (PidLoop::Velocity, true) => ("kp", None, Some("ki")),
        (PidLoop::Current, false) => ("cur-p", None, Some("cur-i")),
        (PidLoop::Current, true) => {
            return Err(ServoError::InvalidParam("电流环没有运行区映射，请去掉 --ram".into()))
        }
    };

    // 把 (参数值, 寄存器键名) 配成对，逐个写入
    let pairs: [(Option<i64>, &str); 3] = [
        (kp, k_p),
        (kd, k_d.unwrap_or("")),
        (ki, k_i.unwrap_or("")),
    ];
    let mut wrote_any = false;
    for (value, key) in pairs {
        if let (Some(v), false) = (value, key.is_empty()) {
            let reg = find_register(key).unwrap();
            if save || reg.area != Area::Eprom {
                if save { servo.save_reg(id, reg, v)?; } else { servo.write_reg(id, reg, v)?; }
            } else {
                servo.write_reg(id, reg, v)?;
            }
            println!("  {}（{}）= {v}", reg.key, reg.cn);
            wrote_any = true;
        }
    }
    if !wrote_any {
        return Err(ServoError::InvalidParam(
            "至少给一个参数，如：pid position --kp 40 --kd 8".into(),
        ));
    }
    match (ram, save) {
        (true, _) => println!("已写入运行区（立即生效，掉电丢失）—— 适合调参时反复试"),
        (false, true) => println!("已写入 EPROM 并掉电保存"),
        (false, false) => println!("已写入 EPROM 配置（本次有效；调好后加 --save 保存）"),
    }
    Ok(())
}

/// 实时监控反馈
fn cmd_monitor(servo: &mut Servo, id: u8, interval: u64, count: Option<u32>) -> Result<()> {
    println!("位置      速度      负载%    电压V   温度°C  电流mA   移动  状态");
    let mut n = 0u32;
    loop {
        let fb = servo.read_feedback(id)?;
        println!(
            "{:<9} {:<9} {:<8.1} {:<7.1} {:<7} {:<8.0} {:<5} {}",
            fb.pos, fb.vel, fb.load as f64 / 10.0, fb.voltage, fb.temp,
            fb.current, fb.moving, protocol::describe_status(fb.status),
        );
        n += 1;
        // 【Rust 知识点：Option 的模式匹配】
        // count 是 Option<u32>：Some(n) 表示用户指定了次数
        if let Some(max) = count {
            if n >= max {
                break;
            }
        }
        thread::sleep(Duration::from_millis(interval));
    }
    Ok(())
}

/// 保护参数设置
#[allow(clippy::too_many_arguments)]
fn cmd_protect(
    servo: &mut Servo,
    id: u8,
    max_temp: Option<i64>,
    max_voltage: Option<i64>,
    min_voltage: Option<i64>,
    overcur_time: Option<i64>,
    unload: Option<i64>,
    led: Option<i64>,
    save: bool,
) -> Result<()> {
    let pairs: [(Option<i64>, &str); 6] = [
        (max_temp, "max-temp"),
        (max_voltage, "max-voltage"),
        (min_voltage, "min-voltage"),
        (overcur_time, "overcur-time"),
        (unload, "unload-cond"),
        (led, "led-alarm"),
    ];
    let mut wrote_any = false;
    for (value, key) in pairs {
        if let Some(v) = value {
            let reg = find_register(key).unwrap();
            if save { servo.save_reg(id, reg, v)?; } else { servo.write_reg(id, reg, v)?; }
            println!("  {}（{}）= {v}", reg.key, reg.cn);
            wrote_any = true;
        }
    }
    if !wrote_any {
        return Err(ServoError::InvalidParam(
            "至少给一个参数，如：protect --max-temp 80 --unload 12 --led 12".into(),
        ));
    }
    if save {
        println!("已全部掉电保存");
    } else {
        println!("（仅本次有效，加 --save 可掉电保存）");
    }
    Ok(())
}

/// 把卸载条件/LED报警的位掩码翻译成中文（位定义见文档 3.3/3.4 节）
fn describe_protect_bits(v: u8) -> String {
    if v == 0 {
        return "全部关闭".to_string();
    }
    let bits = [
        (1u8, "电压"),
        (2, "磁编码"),
        (4, "过热"),
        (8, "过流"),
    ];
    let mut names = Vec::new();
    for (bit, name) in bits {
        if v & bit != 0 {
            names.push(name);
        }
    }
    names.join("+")
}
