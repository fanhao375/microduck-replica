//! 飞特 STS3215 总线舵机通讯协议（FEETECH STS/SCS 串口协议）的最小实现。
//!
//! 协议帧格式（每个字节通过 UART 发送，默认 1,000,000 波特率 8N1）：
//!   [0xFF, 0xFF, ID, LENGTH, INSTRUCTION, PARAM1..PARAMn, CHECKSUM]
//!   - LENGTH    = 参数字节数 + 2（指令字节 + 校验字节）
//!   - CHECKSUM  = ~(ID + LENGTH + INSTRUCTION + 所有参数) 的低 8 位
//!
//! 舵机应答帧格式相同，只是把 INSTRUCTION 换成 ERROR 状态字节。

use serialport::SerialPort;
use std::io::{self, ErrorKind};
use std::time::{Duration, Instant};

// ---- 指令码 ----
pub const INST_PING: u8 = 0x01;
pub const INST_READ: u8 = 0x02;
pub const INST_WRITE: u8 = 0x03;

/// STS3215 内存表（常用寄存器地址）。
/// 已对照飞特官方 SDK `sms_sts.py`（FTServo_Python）核对。
#[allow(dead_code)] // 完整寄存器表作为查阅参考保留
pub mod reg {
    // ---- EPROM 只读 ----
    pub const FIRMWARE: u8 = 0; // 3字节, 固件版本
    pub const MODEL_NUMBER: u8 = 3; // u16, 型号
    // ---- EPROM 读写（修改前先写 LOCK=0 解锁）----
    pub const ID: u8 = 5; // u8, 舵机 ID
    pub const BAUD_RATE: u8 = 6; // u8, 波特率索引 0=1M 4=115200 ...
    pub const MIN_POSITION: u8 = 9; // u16, 最小角度限制
    pub const MAX_POSITION: u8 = 11; // u16, 最大角度限制
    pub const MAX_TEMPERATURE: u8 = 13; // u8, 最高温度保护
    pub const MAX_VOLTAGE: u8 = 14; // u8, 最高电压, 单位 0.1V
    pub const MIN_VOLTAGE: u8 = 15; // u8, 最低电压, 单位 0.1V
    pub const MODE: u8 = 33; // u8, 0=位置模式 1=轮式模式
    // ---- SRAM 读写 ----
    pub const TORQUE_ENABLE: u8 = 40; // u8, 力矩开关 1=锁紧 0=释放
    pub const ACCELERATION: u8 = 41; // u8, 加速度
    pub const GOAL_POSITION: u8 = 42; // u16, 目标位置 0~4095
    pub const GOAL_TIME: u8 = 44; // u16, 运行时间 ms
    pub const GOAL_SPEED: u8 = 46; // u16, 运行速度 步/秒
    pub const TORQUE_LIMIT: u8 = 48; // u16, 力矩限制
    pub const LOCK: u8 = 55; // u8, EEPROM 锁：0=解锁 1=锁定
    // ---- SRAM 只读 ----
    pub const PRESENT_POSITION: u8 = 56; // u16, 当前位置 0~4095
    pub const PRESENT_SPEED: u8 = 58; // u16, 当前速度
    pub const PRESENT_LOAD: u8 = 60; // u16, 当前负载 bit0~9=大小 bit10=方向, 单位 0.1%
    pub const PRESENT_VOLTAGE: u8 = 62; // u8, 当前电压, 单位 0.1V
    pub const PRESENT_TEMPERATURE: u8 = 63; // u8, 当前温度 °C
    pub const SERVO_STATUS: u8 = 65; // u8, 舵机状态（报警位）
    pub const MOVING: u8 = 66; // u8, 是否运动中
    pub const PRESENT_CURRENT: u8 = 69; // u16, 当前电流, 单位约 6.5mA
}

/// 一次读回的舵机实时状态（从地址 56 连续读 15 字节解析而来）。
#[derive(Debug, Clone)]
pub struct ServoStatus {
    pub position: u16,    // 0~4095
    pub speed: i16,       // 步/秒（带方向）
    pub load: i16,        // 0.1%，带方向
    pub voltage: f32,     // V
    pub temperature: u8,  // °C
    pub servo_status: u8, // 非 0 表示有报警（过压/过流/过热等）
    pub moving: bool,
    pub current_ma: f32, // mA
}

impl ServoStatus {
    /// 位置 0~4095 换算成角度（度）。
    pub fn angle_deg(&self) -> f32 {
        self.position as f32 * 360.0 / 4096.0
    }
}

/// 打开后的总线。所有与舵机的通讯都通过它进行。
pub struct StsBus {
    port: Box<dyn SerialPort>,
    /// 单次读包的最长等待时间
    timeout: Duration,
}

impl StsBus {
    /// 打开串口，例如 StsBus::open("/dev/ttyUSB0", 1_000_000)
    pub fn open(path: &str, baud: u32) -> io::Result<Self> {
        let port = serialport::new(path, baud)
            .data_bits(serialport::DataBits::Eight)
            .stop_bits(serialport::StopBits::One)
            .parity(serialport::Parity::None)
            .flow_control(serialport::FlowControl::None)
            .timeout(Duration::from_millis(20))
            .open()?;
        Ok(Self {
            port,
            timeout: Duration::from_millis(150),
        })
    }

    fn checksum(bytes: &[u8]) -> u8 {
        !bytes.iter().fold(0u8, |acc, b| acc.wrapping_add(*b))
    }

    /// 发送一帧指令包。
    fn send_packet(&mut self, id: u8, inst: u8, params: &[u8]) -> io::Result<()> {
        let len = params.len() as u8 + 2;
        let mut pkt = Vec::with_capacity(6 + params.len());
        pkt.extend_from_slice(&[0xFF, 0xFF, id, len, inst]);
        pkt.extend_from_slice(params);
        pkt.push(Self::checksum(&pkt[2..]));
        self.port.write_all(&pkt)?;
        self.port.flush()
    }

    /// 在截止时间前读满 n 个字节。
    fn read_n(&mut self, n: usize, deadline: Instant) -> io::Result<Vec<u8>> {
        let mut buf = vec![0u8; n];
        let mut got = 0;
        while got < n {
            match self.port.read(&mut buf[got..]) {
                Ok(0) => {}
                Ok(k) => got += k,
                Err(e) if e.kind() == ErrorKind::TimedOut => {
                    if Instant::now() >= deadline {
                        return Err(io::Error::new(
                            ErrorKind::TimedOut,
                            "等待舵机应答超时",
                        ));
                    }
                }
                Err(e) => return Err(e),
            }
        }
        Ok(buf)
    }

    /// 接收一帧应答包，返回 (id, error, 参数区)。
    fn recv_packet(&mut self) -> io::Result<(u8, u8, Vec<u8>)> {
        let deadline = Instant::now() + self.timeout;
        // 1. 逐字节滑动，找到帧头 0xFF 0xFF
        let mut prev = self.read_n(1, deadline)?[0];
        loop {
            let cur = self.read_n(1, deadline)?[0];
            if prev == 0xFF && cur == 0xFF {
                break;
            }
            prev = cur;
        }
        // 2. id + length
        let head = self.read_n(2, deadline)?;
        let (id, len) = (head[0], head[1] as usize);
        // 3. error + params + checksum（length 已包含 error 和 checksum 这 2 字节）
        let body = self.read_n(len, deadline)?;
        let mut all = vec![id, len as u8];
        all.extend_from_slice(&body);
        if Self::checksum(&all) != 0x00 {
            let hex: Vec<String> = all.iter().map(|b| format!("{b:02X}")).collect();
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                format!("校验和错误, 收到字节: {}", hex.join(" ")),
            ));
        }
        let error = body[0];
        let params = body[1..body.len() - 1].to_vec();
        Ok((id, error, params))
    }

    /// 接收一帧应答包。部分转接板会把发送内容回显到 RX：回显包与发送包
    /// 字节完全一致（"error 位置"恰好等于指令码）。但注意：舵机带报警时
    /// 的 ping 应答（error=0x01）与 ping 回显在字节上无法区分——所以策略是：
    /// 若第一包疑似回显，再等第二包；等不到就把第一包当作真实应答。
    fn recv_non_echo(&mut self, sent_id: u8, sent_inst: u8, sent_params: &[u8]) -> io::Result<(u8, u8, Vec<u8>)> {
        let first = self.recv_packet()?;
        let looks_like_echo = first.0 == sent_id && first.1 == sent_inst && first.2 == sent_params;
        if !looks_like_echo {
            return Ok(first);
        }
        match self.recv_packet() {
            Ok(second) => Ok(second), // 有第二包：第一包是回显
            Err(e) if e.kind() == ErrorKind::TimedOut => Ok(first), // 没有第二包：第一包就是应答
            Err(e) => Err(e),
        }
    }

    /// 设置 RTS / DTR 控制线（部分转接板用它做收发方向或使能）。
    pub fn set_rts(&mut self, on: bool) -> io::Result<()> {
        self.port
            .write_request_to_send(on)
            .map_err(|e| io::Error::new(ErrorKind::Other, e.to_string()))
    }
    pub fn set_dtr(&mut self, on: bool) -> io::Result<()> {
        self.port
            .write_data_terminal_ready(on)
            .map_err(|e| io::Error::new(ErrorKind::Other, e.to_string()))
    }

    /// 广播扫描：返回有应答的舵机 ID 列表。
    #[allow(dead_code)] // 作为库 API 保留；CLI 用带进度条的版本
    pub fn scan(&mut self, from: u8, to: u8) -> Vec<u8> {
        let mut found = Vec::new();
        for id in from..=to {
            if self.ping(id).unwrap_or(false) {
                found.push(id);
            }
        }
        found
    }

    /// 诊断用：发送任意指令包，把随后 500ms 内收到的所有原始字节原样返回。
    /// 返回 (发送的字节, 收到的字节)。
    pub fn raw_exchange(&mut self, id: u8, inst: u8, params: &[u8]) -> io::Result<(Vec<u8>, Vec<u8>)> {
        self.port.clear(serialport::ClearBuffer::Input)?;
        // 重新拼一遍发送内容用于显示
        let len = params.len() as u8 + 2;
        let mut sent = vec![0xFF, 0xFF, id, len, inst];
        sent.extend_from_slice(params);
        sent.push(Self::checksum(&sent[2..]));

        self.port.write_all(&sent)?;
        self.port.flush()?;

        let deadline = Instant::now() + Duration::from_millis(500);
        let mut out = Vec::new();
        let mut buf = [0u8; 64];
        while Instant::now() < deadline {
            match self.port.read(&mut buf) {
                Ok(n) if n > 0 => out.extend_from_slice(&buf[..n]),
                Ok(_) => {}
                Err(e) if e.kind() == ErrorKind::TimedOut => {}
                Err(e) => return Err(e),
            }
        }
        Ok((sent, out))
    }

    /// 探测某个 ID 的舵机是否在线。
    /// 注意：应答包的 error 字节是舵机的报警状态（如 bit0=电压异常），
    /// 带报警的舵机仍然算"在线"。
    pub fn ping(&mut self, id: u8) -> io::Result<bool> {
        self.port.clear(serialport::ClearBuffer::Input)?;
        self.send_packet(id, INST_PING, &[])?;
        match self.recv_non_echo(id, INST_PING, &[]) {
            Ok((rid, _err, _)) => Ok(rid == id),
            Err(e) if e.kind() == ErrorKind::TimedOut => Ok(false),
            Err(e) => Err(e),
        }
    }

    /// 从舵机读 len 个字节（len <= 250）。
    /// error 字节（报警状态）不视为失败，数据照常返回。
    pub fn read(&mut self, id: u8, addr: u8, len: u8) -> io::Result<Vec<u8>> {
        self.port.clear(serialport::ClearBuffer::Input)?;
        self.send_packet(id, INST_READ, &[addr, len])?;
        let (rid, _err, params) = self.recv_non_echo(id, INST_READ, &[addr, len])?;
        if rid != id {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                format!("应答 ID 不匹配: 期望 {id}, 收到 {rid}"),
            ));
        }
        Ok(params)
    }

    pub fn read_u8(&mut self, id: u8, addr: u8) -> io::Result<u8> {
        Ok(self.read(id, addr, 1)?[0])
    }

    /// u16 为小端序（低字节在前）。
    pub fn read_u16(&mut self, id: u8, addr: u8) -> io::Result<u16> {
        let b = self.read(id, addr, 2)?;
        Ok(u16::from_le_bytes([b[0], b[1]]))
    }

    /// 写若干字节（写 SRAM 寄存器，舵机无应答时属正常）。
    pub fn write(&mut self, id: u8, addr: u8, data: &[u8]) -> io::Result<()> {
        let mut params = vec![addr];
        params.extend_from_slice(data);
        self.send_packet(id, INST_WRITE, &params)?;
        // 写指令的应答（若开启）直接丢弃，不阻塞
        let _ = self.recv_packet();
        Ok(())
    }

    pub fn write_u8(&mut self, id: u8, addr: u8, v: u8) -> io::Result<()> {
        self.write(id, addr, &[v])
    }

    #[allow(dead_code)] // 库 API 保留
    pub fn write_u16(&mut self, id: u8, addr: u8, v: u16) -> io::Result<()> {
        self.write(id, addr, &v.to_le_bytes())
    }

    /// 力矩开关：true=锁紧（可控制），false=释放（可手掰）。
    pub fn set_torque(&mut self, id: u8, enable: bool) -> io::Result<()> {
        self.write_u8(id, reg::TORQUE_ENABLE, enable as u8)
    }

    /// 让舵机转到目标位置（0~4095），speed 为步/秒，time_ms 为期望耗时。
    /// 与官方 SDK 的 WritePosEx 一致：从 ACCELERATION(41) 起一次性写 7 字节。
    pub fn move_to(&mut self, id: u8, pos: u16, speed: u16, time_ms: u16) -> io::Result<()> {
        self.set_torque(id, true)?;
        let pos = pos.min(4095);
        let mut data = Vec::with_capacity(7);
        data.push(50); // 加速度
        data.extend_from_slice(&pos.to_le_bytes());
        data.extend_from_slice(&time_ms.to_le_bytes());
        data.extend_from_slice(&speed.to_le_bytes());
        self.write(id, reg::ACCELERATION, &data)
    }

    /// 一次读回地址 56 起的 15 字节并解析成 ServoStatus。
    pub fn read_status(&mut self, id: u8) -> io::Result<ServoStatus> {
        let b = self.read(id, reg::PRESENT_POSITION, 15)?;
        // b[0..2]  位置   b[2..4] 速度  b[4..6] 负载
        // b[6] 电压 b[7] 温度  b[8] 写标志  b[9] 状态
        // b[10] 运动中  b[11..13] 保留  b[13..15] 电流
        let position = u16::from_le_bytes([b[0], b[1]]);
        let speed = sts_signed(i16::from_le_bytes([b[2], b[3]]));
        let load_raw = u16::from_le_bytes([b[4], b[5]]);
        // 负载：bit0~9 为大小（0.1%），bit10 为方向
        let load = if load_raw & 0x0400 != 0 {
            -((load_raw & 0x03FF) as i16)
        } else {
            (load_raw & 0x03FF) as i16
        };
        let current_raw = i16::from_le_bytes([b[13], b[14]]);
        let current_ma = sts_signed(current_raw) as f32 * 6.5;
        Ok(ServoStatus {
            position,
            speed,
            load,
            voltage: b[6] as f32 * 0.1,
            temperature: b[7],
            servo_status: b[9],
            moving: b[10] != 0,
            current_ma,
        })
    }
}

/// 飞特协议中 2 字节有符号数：bit15 为符号位，低 15 位为数值。
fn sts_signed(v: i16) -> i16 {
    if v & (1 << 15) != 0 {
        -(v & !(1 << 15))
    } else {
        v
    }
}
