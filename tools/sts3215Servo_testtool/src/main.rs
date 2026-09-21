//! sts3215Servo_testtool — STS3215 舵机测试工具
//!
//! 用法：
//!   sts3215Servo_testtool scan [--from 0 --to 253]     扫描总线上的舵机（带进度条）
//!   sts3215Servo_testtool info <id>                    读取舵机状态（位置/电压/温度/电流/负载）
//!   sts3215Servo_testtool read <id> <addr> <len>       读取任意寄存器（十六进制输出）
//!   sts3215Servo_testtool set-id <旧id> <新id>         修改舵机 ID（总线上只能接一个！）
//!   sts3215Servo_testtool move <id> <pos> [--speed N] [--time ms]   转到指定位置(0~4095)
//!   sts3215Servo_testtool test <id>                    对单个舵机做完整测试
//!   sts3215Servo_testtool test-all                     扫描并批量测试所有舵机
//!   sts3215Servo_testtool raw [id]                     底层诊断：打印原始收发字节
//!   全局选项: --port <路径>（默认 /dev/ttyUSB0）  --baud <波特率>（默认 1000000）

mod sts;

use std::io;
use std::thread;
use std::time::{Duration, Instant};
use sts::{reg, StsBus};

const TEST_POSITIONS: [u16; 4] = [2048, 1024, 3072, 2048]; // 中位 -> 低 -> 高 -> 回中
const POS_TOLERANCE: u16 = 20; // 位置容差（步）

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cfg = match parse_args(&args) {
        Ok(c) => c,
        Err(msg) => {
            eprintln!("参数错误: {msg}\n");
            print_usage();
            std::process::exit(2);
        }
    };

    let mut bus = match StsBus::open(&cfg.port, cfg.baud) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("无法打开串口 {}: {e}", cfg.port);
            eprintln!("请检查：舵机转接板是否插入、设备路径是否正确、当前用户是否在 dialout 组。");
            std::process::exit(1);
        }
    };

    // 部分转接板用 RTS/DTR 控制收发方向或使能，可按需调整
    if let Some(on) = cfg.rts {
        let _ = bus.set_rts(on);
    }
    if let Some(on) = cfg.dtr {
        let _ = bus.set_dtr(on);
    }

    let code = match cfg.cmd.as_str() {
        "scan" => cmd_scan(&mut bus, cfg.from, cfg.to),
        "raw" => cmd_raw(&mut bus, cfg.id.unwrap_or(1)),
        "read" => cmd_read(&mut bus, cfg.id.expect("read 需要 id"), cfg.pos.expect("read 需要 addr") as u8, cfg.len.unwrap_or(1)),
        "info" => cmd_info(&mut bus, cfg.id.expect("info 需要 id")),
        "set-id" => cmd_set_id(&mut bus, cfg.id.expect("set-id 需要旧 id"), cfg.pos.expect("set-id 需要新 id") as u8),
        "move" => cmd_move(&mut bus, cfg.id.expect("move 需要 id"), cfg.pos.unwrap_or(2048), cfg.speed, cfg.time_ms),
        "test" => cmd_test(&mut bus, cfg.id.expect("test 需要 id")),
        "test-all" => cmd_test_all(&mut bus, cfg.from, cfg.to),
        _ => {
            print_usage();
            2
        }
    };
    std::process::exit(code);
}

struct Config {
    port: String,
    baud: u32,
    cmd: String,
    id: Option<u8>,
    pos: Option<u16>,
    speed: u16,
    time_ms: u16,
    from: u8,
    to: u8,
    rts: Option<bool>,
    dtr: Option<bool>,
    len: Option<u8>,
}

fn parse_args(args: &[String]) -> Result<Config, String> {
    let mut cfg = Config {
        port: "/dev/ttyUSB0".into(),
        baud: 1_000_000,
        cmd: String::new(),
        id: None,
        pos: None,
        speed: 1000,
        time_ms: 0,
        from: 0,
        to: 253,
        rts: None,
        dtr: None,
        len: None,
    };
    let mut positional: Vec<&str> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        let take = |i: &mut usize| -> Result<&str, String> {
            *i += 1;
            args.get(*i)
                .map(|s| s.as_str())
                .ok_or_else(|| format!("选项 {} 缺少参数", args[*i - 1]))
        };
        let onoff = |s: &str| -> Result<bool, String> {
            match s {
                "on" | "1" => Ok(true),
                "off" | "0" => Ok(false),
                _ => Err("必须是 on/off".into()),
            }
        };
        match args[i].as_str() {
            "--port" | "-p" => cfg.port = take(&mut i)?.to_string(),
            "--baud" | "-b" => cfg.baud = take(&mut i)?.parse().map_err(|_| "波特率必须是数字")?,
            "--speed" => cfg.speed = take(&mut i)?.parse().map_err(|_| "speed 必须是数字")?,
            "--time" => cfg.time_ms = take(&mut i)?.parse().map_err(|_| "time 必须是数字")?,
            "--from" => cfg.from = take(&mut i)?.parse().map_err(|_| "from 必须是数字")?,
            "--to" => cfg.to = take(&mut i)?.parse().map_err(|_| "to 必须是数字")?,
            "--rts" => cfg.rts = Some(onoff(take(&mut i)?)?),
            "--dtr" => cfg.dtr = Some(onoff(take(&mut i)?)?),
            "-h" | "--help" => return Err("".into()),
            s if s.starts_with('-') => return Err(format!("未知选项 {s}")),
            s => positional.push(s),
        }
        i += 1;
    }
    cfg.cmd = positional.first().copied().unwrap_or("").to_string();
    if let Some(v) = positional.get(1) {
        cfg.id = Some(v.parse().map_err(|_| "id 必须是 0~253 的数字")?);
    }
    if let Some(v) = positional.get(2) {
        cfg.pos = Some(v.parse().map_err(|_| "pos/addr 必须是数字")?);
    }
    if let Some(v) = positional.get(3) {
        cfg.len = Some(v.parse().map_err(|_| "len 必须是数字")?);
    }
    Ok(cfg)
}

fn print_usage() {
    let doc: Vec<&str> = include_str!("main.rs")
        .lines()
        .take_while(|l| l.starts_with("//!"))
        .map(|l| l.trim_start_matches("//!").trim_start())
        .collect();
    eprintln!("{}", doc.join("\n"));
}

/// 诊断：向舵机发 ping，把收到的原始字节以十六进制打印出来。
///   无任何字节     -> 舵机没应答（供电/接线/模式问题）
///   与发送内容一致 -> 转接板有回显，但舵机没应答
///   一堆乱码       -> 波特率不匹配
///   FF FF id 02 00 CS -> 舵机正常应答！
fn cmd_raw(bus: &mut StsBus, id: u8) -> i32 {
    let show = |label: &str, r: io::Result<(Vec<u8>, Vec<u8>)>| match r {
        Ok((sent, recv)) => {
            let h = |v: &[u8]| v.iter().map(|b| format!("{b:02X}")).collect::<Vec<_>>().join(" ");
            println!("{label}");
            println!("  发送: {}", h(&sent));
            println!("  收到: {}", if recv.is_empty() { "(无任何字节)".into() } else { h(&recv) });
            0
        }
        Err(e) => {
            eprintln!("{label} 失败: {e}");
            1
        }
    };
    let mut code = 0;
    code |= show("PING:", bus.raw_exchange(id, sts::INST_PING, &[]));
    code |= show("READ 型号(地址3,2字节):", bus.raw_exchange(id, sts::INST_READ, &[3, 2]));
    code |= show("READ 状态区(地址56,15字节):", bus.raw_exchange(id, sts::INST_READ, &[56, 15]));
    code
}

/// 通用寄存器读取：read <id> <addr> <len>，以十六进制打印。
fn cmd_read(bus: &mut StsBus, id: u8, addr: u8, len: u8) -> i32 {
    match bus.read(id, addr, len) {
        Ok(data) => {
            let hex: Vec<String> = data.iter().map(|b| format!("{b:02X}")).collect();
            println!("舵机 {id} 地址 {addr} 起的 {} 字节: {}", data.len(), hex.join(" "));
            0
        }
        Err(e) => {
            eprintln!("读取失败: {e}");
            1
        }
    }
}

/// 带进度条的扫描（indicatif 渲染：百分比 + 动态条 + 耗时/预计剩余）。
fn scan_with_progress(bus: &mut StsBus, from: u8, to: u8) -> Vec<u8> {
    use indicatif::{ProgressBar, ProgressStyle};
    let total = (to - from + 1) as u64;
    let pb = ProgressBar::new(total);
    pb.set_style(
        ProgressStyle::with_template(
            "{spinner:.green} {msg} {wide_bar:.cyan/blue} {percent:>3}% ({pos}/{len}) 已用 {elapsed_precise} 剩余 {eta_precise}",
        )
        .unwrap()
        .progress_chars("█▉▊▋▌▍▎▏ "),
    );
    pb.set_message(format!("扫描 ID {from}~{to}"));
    let mut found = Vec::new();
    for id in from..=to {
        if bus.ping(id).unwrap_or(false) {
            found.push(id);
            pb.println(format!("  ✔ 发现舵机 ID {id:>3}"));
            pb.set_message(format!("扫描 ID {from}~{to}（已发现 {} 个）", found.len()));
        }
        pb.inc(1);
    }
    pb.finish_with_message(format!("扫描完成，共发现 {} 个舵机", found.len()));
    found
}

/// 扫描总线，打印发现的舵机及其型号。
fn cmd_scan(bus: &mut StsBus, from: u8, to: u8) -> i32 {
    let ids = scan_with_progress(bus, from, to);
    if ids.is_empty() {
        println!("未发现任何舵机。请检查接线、供电(7.4V/12V)和波特率(--baud)。");
        return 1;
    }
    for id in &ids {
        let model = bus.read_u16(*id, reg::MODEL_NUMBER).unwrap_or(0);
        let t_max = bus.read_u8(*id, reg::MAX_TEMPERATURE).unwrap_or(0);
        let v_max = bus.read_u8(*id, reg::MAX_VOLTAGE).map(|v| v as f32 * 0.1).unwrap_or(0.0);
        let v_min = bus.read_u8(*id, reg::MIN_VOLTAGE).map(|v| v as f32 * 0.1).unwrap_or(0.0);
        let volt = bus.read_u8(*id, reg::PRESENT_VOLTAGE).map(|v| v as f32 * 0.1).unwrap_or(0.0);
        println!("  ID {id:>3}  在线  型号={model}  电压={volt:.1}V (范围 {v_min:.1}~{v_max:.1}V)  温度保护={t_max}°C");
    }
    println!("共发现 {} 个舵机。", ids.len());
    0
}

/// 打印单个舵机的完整状态。
fn cmd_info(bus: &mut StsBus, id: u8) -> i32 {
    match bus.read_status(id) {
        Ok(s) => {
            println!("舵机 ID {id}:");
            println!("  位置     : {:>4} / 4095   ({:.1}°)", s.position, s.angle_deg());
            println!("  速度     : {:>4} 步/秒", s.speed);
            println!("  负载     : {:>4.1} %", s.load as f32 * 0.1);
            println!("  电压     : {:>4.1} V", s.voltage);
            println!("  温度     : {:>4} °C", s.temperature);
            println!("  电流     : {:>4.0} mA", s.current_ma);
            println!("  运动中   : {}", if s.moving { "是" } else { "否" });
            if s.servo_status != 0 {
                println!("  ⚠ 报警   : 状态码 0x{:02X}", s.servo_status);
            }
            0
        }
        Err(e) => {
            eprintln!("读取舵机 {id} 失败: {e}");
            1
        }
    }
}

/// 修改舵机 ID：写 EEPROM，流程为 解锁(LOCK=0) -> 写 ID -> 锁定(LOCK=1)。
/// ⚠ 总线上必须只接要修改的那一个舵机！否则所有同 ID 舵机会被一起改掉。
fn cmd_set_id(bus: &mut StsBus, old_id: u8, new_id: u8) -> i32 {
    if new_id > 253 {
        eprintln!("新 ID 必须在 0~253 之间（254 是广播地址）");
        return 2;
    }
    match bus.ping(old_id) {
        Ok(true) => println!("ID {old_id} 在线，准备修改为 {new_id} ..."),
        Ok(false) => {
            eprintln!("ID {old_id} 无应答，未做任何修改。");
            return 1;
        }
        Err(e) => {
            eprintln!("ping 失败: {e}");
            return 1;
        }
    }
    let steps = [
        (reg::LOCK, 0u8),  // 解锁 EEPROM
        (reg::ID, new_id), // 写入新 ID
        (reg::LOCK, 1),    // 重新锁定
    ];
    for (addr, val) in steps {
        if let Err(e) = bus.write_u8(old_id, addr, val) {
            eprintln!("写寄存器 {addr} 失败: {e}");
            return 1;
        }
        thread::sleep(Duration::from_millis(50)); // 等 EEPROM 写入完成
    }
    // 用新 ID 验证
    match bus.ping(new_id) {
        Ok(true) => {
            println!("✅ 修改成功：ID {old_id} -> {new_id}（断电重启后依然有效）");
            0
        }
        _ => {
            eprintln!("❌ 新 ID {new_id} 无应答，修改可能失败，请重新扫描确认。");
            1
        }
    }
}

/// 控制舵机转到指定位置，然后回读确认。
fn cmd_move(bus: &mut StsBus, id: u8, pos: u16, speed: u16, time_ms: u16) -> i32 {
    if pos > 4095 {
        eprintln!("pos 超出范围 0~4095");
        return 2;
    }
    if let Err(e) = bus.move_to(id, pos, speed, time_ms) {
        eprintln!("发送运动指令失败: {e}");
        return 1;
    }
    println!("舵机 {id} -> 目标 {pos}，等待到位...");
    match wait_until_arrived(bus, id, pos, Duration::from_secs(5)) {
        Ok((final_pos, _elapsed)) => {
            println!("到位: 当前位置 {final_pos} ({:.1}°)", final_pos as f32 * 360.0 / 4096.0);
            0
        }
        Err(e) => {
            eprintln!("等待到位超时/失败: {e}");
            1
        }
    }
}

/// 轮询直到舵机到达目标位置（或超时），返回最终位置与耗时。
fn wait_until_arrived(bus: &mut StsBus, id: u8, target: u16, timeout: Duration) -> io::Result<(u16, Duration)> {
    let start = Instant::now();
    thread::sleep(Duration::from_millis(50)); // 等舵机启动
    loop {
        let s = bus.read_status(id)?;
        let err = s.position.abs_diff(target);
        if !s.moving && err <= POS_TOLERANCE {
            return Ok((s.position, start.elapsed()));
        }
        if start.elapsed() > timeout {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!("超时未到位: 目标 {target}, 当前 {}, 偏差 {err}", s.position),
            ));
        }
        thread::sleep(Duration::from_millis(20));
    }
}

/// 单个舵机的完整测试报告。
struct TestReport {
    id: u8,
    model: u16,
    voltage: f32,
    temperature: u8,
    max_current_ma: f32,
    max_load_pct: f32,
    max_pos_err: u16,
    pass: bool,
    notes: Vec<String>,
}

/// 对单个舵机执行完整测试：基线读数 -> 位置扫描 -> 采样电流/负载 -> 回中。
fn cmd_test(bus: &mut StsBus, id: u8) -> i32 {
    match test_one(bus, id) {
        Ok(r) => {
            print_report(&r);
            if r.pass {
                0
            } else {
                1
            }
        }
        Err(e) => {
            eprintln!("舵机 {id} 测试失败: {e}");
            1
        }
    }
}

fn test_one(bus: &mut StsBus, id: u8) -> io::Result<TestReport> {
    println!("== 测试舵机 ID {id} ==");
    if !bus.ping(id)? {
        return Err(io::Error::new(io::ErrorKind::NotConnected, "舵机无应答"));
    }
    let model = bus.read_u16(id, reg::MODEL_NUMBER)?;
    let mut notes = Vec::new();

    // 基线状态
    let base = bus.read_status(id)?;
    println!("  型号={model}  电压={:.1}V  温度={}°C  位置={}", base.voltage, base.temperature, base.position);
    if base.voltage < 6.0 {
        notes.push(format!("电压偏低 {:.1}V，请检查供电", base.voltage));
    }
    if base.servo_status != 0 {
        notes.push(format!("存在报警状态 0x{:02X}", base.servo_status));
    }

    // 位置扫描 + 运动中采样
    let mut max_current = 0f32;
    let mut max_load = 0f32;
    let mut max_pos_err = 0u16;
    for &target in &TEST_POSITIONS {
        bus.move_to(id, target, 1500, 0)?;
        let start = Instant::now();
        let mut arrived = false;
        while start.elapsed() < Duration::from_secs(4) {
            let s = bus.read_status(id)?;
            max_current = max_current.max(s.current_ma.abs());
            max_load = max_load.max((s.load as f32 * 0.1).abs());
            if !s.moving && s.position.abs_diff(target) <= POS_TOLERANCE {
                arrived = true;
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        let s = bus.read_status(id)?;
        let err = s.position.abs_diff(target);
        max_pos_err = max_pos_err.max(err);
        println!("  目标 {target:>4} -> 实际 {:>4}  偏差 {err:>3}  {}", s.position, if arrived { "OK" } else { "超时" });
    }

    // 结束读数
    let end = bus.read_status(id)?;
    if end.temperature > base.temperature + 15 {
        notes.push(format!("温升过大: {}°C -> {}°C", base.temperature, end.temperature));
    }
    let pass = notes.is_empty() && max_pos_err <= POS_TOLERANCE;

    Ok(TestReport {
        id,
        model,
        voltage: base.voltage,
        temperature: end.temperature,
        max_current_ma: max_current,
        max_load_pct: max_load,
        max_pos_err,
        pass,
        notes,
    })
}

fn print_report(r: &TestReport) {
    println!("-- 报告: 舵机 {} --", r.id);
    println!("  型号={}  电压={:.1}V  温度={}°C", r.model, r.voltage, r.temperature);
    println!("  峰值电流={:.0}mA  峰值负载={:.1}%  最大位置偏差={}", r.max_current_ma, r.max_load_pct, r.max_pos_err);
    for n in &r.notes {
        println!("  ⚠ {n}");
    }
    println!("  结果: {}", if r.pass { "✅ PASS" } else { "❌ FAIL" });
}

/// 扫描所有舵机并逐一测试，最后输出汇总。
fn cmd_test_all(bus: &mut StsBus, from: u8, to: u8) -> i32 {
    let ids = scan_with_progress(bus, from, to);
    if ids.is_empty() {
        println!("未发现任何舵机。");
        return 1;
    }
    println!("发现 {} 个舵机: {:?}\n", ids.len(), ids);

    let mut reports = Vec::new();
    for &id in &ids {
        match test_one(bus, id) {
            Ok(r) => {
                print_report(&r);
                reports.push(r);
            }
            Err(e) => {
                eprintln!("舵机 {id} 测试异常: {e}");
                reports.push(TestReport {
                    id, model: 0, voltage: 0.0, temperature: 0,
                    max_current_ma: 0.0, max_load_pct: 0.0, max_pos_err: u16::MAX,
                    pass: false, notes: vec![format!("测试异常: {e}")],
                });
            }
        }
        println!();
    }

    // 汇总表
    println!("==== 批量测试汇总 ====");
    println!("{:<4} {:<8} {:<8} {:<6} {:<10} {:<10} {:<8} 结果", "ID", "型号", "电压V", "温度°C", "峰值电流mA", "峰值负载%", "位置偏差");
    for r in &reports {
        println!(
            "{:<4} {:<8} {:<8.1} {:<6} {:<10.0} {:<10.1} {:<8} {}",
            r.id, r.model, r.voltage, r.temperature, r.max_current_ma, r.max_load_pct, r.max_pos_err,
            if r.pass { "PASS" } else { "FAIL" }
        );
    }
    if reports.iter().all(|r| r.pass) { 0 } else { 1 }
}
