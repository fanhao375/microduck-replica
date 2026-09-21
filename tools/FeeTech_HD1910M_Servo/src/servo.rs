//! servo.rs —— 舵机驱动层：把协议包真正发到串口上，并解析应答
//!
//! 【分层回顾】
//!   protocol.rs  纯数据：组包 / 解包（不碰硬件，可单元测试）
//!   registers.rs 纯数据：内存表定义（不碰硬件，可单元测试）
//!   servo.rs     本层：打开串口、收发字节、处理超时与错误
//!   main.rs      最外层：解析命令行，调用本层完成用户要的测试
//!
//! 【Rust 知识点：trait 对象 Box<dyn SerialPort>】
//! serialport 库在不同操作系统上的串口实现是不同的类型。
//! `Box<dyn SerialPort>` 表示“堆上某个实现了 SerialPort trait 的类型”，
//! 我们不关心它具体是什么，只要它会 read/write 就行 —— 这就是
//! Rust 的“动态分发”，类似 Java 的接口引用，但零额外抽象成本地按需选择。

use std::io::{Read, Write};
use std::time::Duration;

use serialport::SerialPort;

use crate::error::{Result, ServoError};
use crate::protocol::{self, Instruction, StatusPacket, BROADCAST_ID};
use crate::registers::{self, Register};

/// 舵机驱动对象。拥有一个独占的串口句柄。
///
/// 【Rust 知识点：所有权保证“独占访问”】
/// 串口是独占资源，同一时刻只能有一个地方在收发。
/// Rust 的所有权机制在编译期就保证了这一点：串口句柄被 Servo 拥有，
/// 想操作舵机必须经过 `&mut self`（可变借用），而可变借用同时只能存在一个，
/// 所以从语言层面杜绝了“两个线程同时读写串口导致报文错乱”的问题。
pub struct Servo {
    port: Box<dyn SerialPort>,
    /// 如果你的硬件是把 TX/RX 直接短接的简易接法，发出去的数据会被自己收回来，
    /// 置 true 则每次发送后先把“回声”丢弃再读应答。
    discard_echo: bool,
    /// 是否打印原始收发报文（学习协议时很有用）
    verbose: bool,
}

/// 舵机实时反馈数据（内存表 56~69 号地址一次性读回）。
///
/// 【Rust 知识点：结构体派生 Debug】
/// #[derive(Debug)] 让我们可以用 `println!("{fb:?}")` 直接打印整个结构体，
/// 调试时非常方便。
#[derive(Debug)]
pub struct Feedback {
    /// 当前位置（0.087度/单位，含方向位；多圈模式含圈数）
    pub pos: i64,
    /// 当前速度（0.732RPM/单位，含方向位）
    pub vel: i64,
    /// 当前负载/占空比（0.1%/单位，BIT10 方向位）
    pub load: i64,
    /// 当前电压（单位 V，已换算成浮点）
    pub voltage: f64,
    /// 当前温度（°C）
    pub temp: i64,
    /// 舵机状态字（0=正常，按位表示异常）
    pub status: u8,
    /// 移动标志
    pub moving: u8,
    /// 当前电流（mA，已换算成浮点，含方向）
    pub current: f64,
}

impl Servo {
    /// 打开串口并创建驱动对象。
    ///
    /// 【Rust 知识点：? 运算符】
    /// `serialport::new(...).open()?` 的意思是：失败就把错误转换成
    /// ServoError 并立刻 return（靠 error.rs 里的 #[from] 自动转换），
    /// 成功就解包继续。这让错误处理既严格又不啰嗦。
    pub fn connect(path: &str, baud: u32, timeout_ms: u64, discard_echo: bool, verbose: bool) -> Result<Self> {
        let port = serialport::new(path, baud)
            .timeout(Duration::from_millis(timeout_ms)) // 每次 read 的最长等待
            .data_bits(serialport::DataBits::Eight)     // 8 数据位
            .parity(serialport::Parity::None)           // 无校验
            .stop_bits(serialport::StopBits::One)       // 1 停止位 —— 规格书 7-2：8bit, 1stop, No Parity
            .open()?;
        Ok(Servo { port, discard_echo, verbose })
    }

    // =========================================================================
    // 底层收发
    // =========================================================================

    /// 发一条指令并等待应答，返回解析后的应答包。
    /// 这是所有指令的公共通道。
    fn transact(&mut self, id: u8, inst: Instruction, params: &[u8], expect_reply: bool) -> Result<StatusPacket> {
        let pkt = protocol::build_packet(id, inst, params);

        // 清空接收缓冲区里的残留字节（上一次通信的尾巴、干扰噪声等），
        // 否则可能把旧数据误当成这次的应答。
        let _ = self.port.clear(serialport::ClearBuffer::Input);

        if self.verbose {
            eprintln!("  >> 发送: {:02X?}", pkt);
        }

        // 【Rust 知识点：&mut self 的传递】
        // write_all 需要可变借用串口。因为 transact 本身借用了 &mut self，
        // 编译器保证这期间没有别人能碰这个串口。
        self.port.write_all(&pkt)?;
        self.port.flush()?;

        // 简易接法（TX/RX 短接）会先收到自己刚发出去的内容，丢弃同样长度的字节
        if self.discard_echo {
            let mut echo = vec![0u8; pkt.len()];
            let _ = self.port.read_exact(&mut echo);
        }

        // 广播地址不应答；应答级别为 0 时写指令也不应答
        if !expect_reply || id == BROADCAST_ID {
            return Ok(StatusPacket { id, error: 0, params: Vec::new() });
        }

        let raw = self.recv_packet()?;
        if self.verbose {
            eprintln!("  << 接收: {:02X?}", raw);
        }
        let status = protocol::parse_status(&raw)?;
        // 校验应答的舵机 ID 是否就是我们请求的对象
        // （总线上可能有多个舵机，收到别人的应答说明时序出了问题）
        if status.id != id {
            return Err(ServoError::Malformed("应答包 ID 与请求不符"));
        }
        if status.error != 0 {
            return Err(ServoError::ServoStatus(
                status.error,
                protocol::describe_status(status.error),
            ));
        }
        Ok(status)
    }

    /// 从串口读出一个完整的应答包（逐字节找帧头 → 读 ID/Length → 读剩余部分）。
    ///
    /// 【Rust 知识点：把“超时的 IO 错误”翻译成“业务错误”】
    /// 串口读超时表现为 io::ErrorKind::TimedOut，我们把它映射成
    /// ServoError::Timeout，让上层看到更有意义的错误。
    fn recv_packet(&mut self) -> Result<Vec<u8>> {
        // 第一步：逐字节扫描，直到找到帧头 0xFF 0xFF
        // （总线上可能有噪声字节，不能假设第一个字节就是帧头）
        let mut window = [0u8; 2]; // 滑动窗口：保存最近收到的两个字节
        let mut found = false;
        for _ in 0..64 {
            // 最多扫 64 字节，找不到就放弃
            let mut b = [0u8; 1];
            self.port.read_exact(&mut b).map_err(|e| match e.kind() {
                std::io::ErrorKind::TimedOut => ServoError::Timeout,
                _ => ServoError::Io(e),
            })?;
            window[0] = window[1];
            window[1] = b[0];
            if window == protocol::HEADER {
                found = true;
                break;
            }
        }
        if !found {
            return Err(ServoError::Malformed("找不到帧头 0xFF 0xFF"));
        }

        // 第二步：读 ID 和 Length
        let mut head = [0u8; 2];
        self.port.read_exact(&mut head).map_err(|e| match e.kind() {
            std::io::ErrorKind::TimedOut => ServoError::Timeout,
            _ => ServoError::Io(e),
        })?;
        let length = head[1] as usize;

        // 第三步：按 Length 读出剩余字节（Error + 参数 + 校验）
        let mut rest = vec![0u8; length];
        self.port.read_exact(&mut rest).map_err(|e| match e.kind() {
            std::io::ErrorKind::TimedOut => ServoError::Timeout,
            _ => ServoError::Io(e),
        })?;

        // 拼成完整的一帧交给协议层解析
        let mut full = Vec::with_capacity(4 + length);
        full.extend_from_slice(&protocol::HEADER);
        full.extend_from_slice(&head);
        full.extend_from_slice(&rest);
        Ok(full)
    }

    // =========================================================================
    // 指令级 API（与协议指令一一对应）
    // =========================================================================

    /// PING 握手：舵机在线返回 Ok(true)，超时返回 Ok(false)，其他错误原样抛出。
    ///
    /// 【Rust 知识点：match 错误类型分别处理】
    /// 超时只是“这个 ID 没有舵机”，不算失败；其他错误才是真的失败。
    pub fn ping(&mut self, id: u8) -> Result<bool> {
        match self.transact(id, Instruction::Ping, &[], true) {
            Ok(_) => Ok(true),
            Err(ServoError::Timeout) => Ok(false),
            Err(e) => Err(e),
        }
    }

    /// 读内存：从 addr 开始读 len 个字节。
    pub fn read(&mut self, id: u8, addr: u8, len: u8) -> Result<Vec<u8>> {
        let resp = self.transact(id, Instruction::Read, &[addr, len], true)?;
        Ok(resp.params)
    }

    /// 写内存（立即生效）：从 addr 开始写入 data。
    pub fn write(&mut self, id: u8, addr: u8, data: &[u8]) -> Result<()> {
        // 参数 = [起始地址, 数据...]，用迭代器拼出来
        let mut params = Vec::with_capacity(data.len() + 1);
        params.push(addr);
        params.extend_from_slice(data);
        self.transact(id, Instruction::Write, &params, true)?;
        Ok(())
    }

    /// 异步写（REG WRITE）：先缓存，等 action() 广播后才真正执行。
    /// 典型用途：给多个舵机分别 REG_WRITE 目标位置，然后一个广播 ACTION
    /// 让所有舵机同时启动 —— 多轴同步的基础。
    pub fn reg_write(&mut self, id: u8, addr: u8, data: &[u8]) -> Result<()> {
        let mut params = Vec::with_capacity(data.len() + 1);
        params.push(addr);
        params.extend_from_slice(data);
        self.transact(id, Instruction::RegWrite, &params, true)?;
        Ok(())
    }

    /// 广播 ACTION：触发所有舵机执行之前缓存的 REG_WRITE 内容。
    pub fn action(&mut self) -> Result<()> {
        self.transact(BROADCAST_ID, Instruction::Action, &[], false)?;
        Ok(())
    }

    /// 同步写（SYNC WRITE 0x83）：一条报文给多个舵机写同一个寄存器。
    ///
    /// 报文结构（广播 ID，无应答）：
    ///   FF FF FE Length 0x83 起始地址 数据长度 [ID1 数据...] [ID2 数据...]... Checksum
    ///
    /// 典型场景：机械臂 6 个关节要“同时”收到目标位置，
    /// 逐个点名写会错开几十毫秒，同步写一条报文全部到位。
    pub fn sync_write(&mut self, ids: &[u8], reg: &Register, value: i64) -> Result<()> {
        if ids.is_empty() {
            return Err(ServoError::InvalidParam("同步写至少需要一个目标 ID".into()));
        }
        if value < reg.min || value > reg.max {
            return Err(ServoError::InvalidParam(format!(
                "值 {value} 超出 {}（{}）的合法范围 {} ~ {}",
                reg.key, reg.cn, reg.min, reg.max
            )));
        }
        let val = registers::encode_value(value, reg.sign_bit);
        let data: Vec<u8> = if reg.size == 1 {
            vec![val as u8]
        } else {
            val.to_le_bytes().to_vec()
        };

        // 组装参数：起始地址 + 每个舵机的数据长度 + 每个舵机的 [ID, 数据...]
        let mut params = Vec::with_capacity(2 + ids.len() * (1 + data.len()));
        params.push(reg.addr);
        params.push(data.len() as u8);
        for &id in ids {
            params.push(id);
            params.extend_from_slice(&data);
        }
        // 同步写是广播指令，舵机收到后不应答
        self.transact(BROADCAST_ID, Instruction::SyncWrite, &params, false)?;
        Ok(())
    }

    /// 恢复出厂设置（地址 5~39 的 EPROM 全部回到默认值）。
    pub fn factory_reset(&mut self, id: u8) -> Result<()> {
        self.transact(id, Instruction::Reset, &[], true)?;
        Ok(())
    }

    // =========================================================================
    // 寄存器级 API（结合内存表，自动处理多字节、符号位、范围校验）
    // =========================================================================

    /// 读取一个寄存器并解码成有符号整数（自动处理小端和符号-幅值编码）。
    pub fn read_reg(&mut self, id: u8, reg: &Register) -> Result<i64> {
        let raw = self.read(id, reg.addr, reg.size)?;
        if raw.len() < reg.size as usize {
            return Err(ServoError::Malformed("读到的数据长度不足"));
        }
        // 小端拼接：低地址是低字节（文档第 2 节：“低位字节在前面地址”）
        let val = if reg.size == 1 {
            raw[0] as u16
        } else {
            u16::from_le_bytes([raw[0], raw[1]])
        };
        Ok(registers::decode_value(val, reg.sign_bit))
    }

    /// 写一个寄存器（自动范围校验、符号-幅值编码、小端拆包）。
    ///
    /// 注意：本函数直接写。若写的是 EPROM 区域且需要掉电保存，
    /// 请使用 save_reg()。
    pub fn write_reg(&mut self, id: u8, reg: &Register, value: i64) -> Result<()> {
        // 【防御式编程】写入前校验：只读寄存器拒绝写，超范围拒绝写。
        // 内存表就是我们的“schema”，在发送前拦截错误比让舵机行为异常好得多。
        if reg.access == registers::Access::ReadOnly {
            return Err(ServoError::InvalidParam(format!(
                "寄存器 {}（{}）是只读的，不能写入",
                reg.key, reg.cn
            )));
        }
        if value < reg.min || value > reg.max {
            return Err(ServoError::InvalidParam(format!(
                "值 {value} 超出 {}（{}）的合法范围 {} ~ {}",
                reg.key, reg.cn, reg.min, reg.max
            )));
        }
        let val = registers::encode_value(value, reg.sign_bit);
        let bytes = if reg.size == 1 {
            vec![val as u8]
        } else {
            val.to_le_bytes().to_vec() // 小端：低字节先发
        };
        self.write(id, reg.addr, &bytes)
    }

    /// 写 EPROM 寄存器并掉电保存。
    ///
    /// 【重要机制：锁标志（55 号地址）】
    ///   锁=1（默认）：写 EPROM 区域的值只改内存，掉电不保存；
    ///   锁=0：写 EPROM 区域的值会真正写入 EPROM，掉电保存。
    /// 所以持久化流程是：解锁(写0) → 写目标寄存器 → 重新上锁(写1)。
    ///
    /// 【为什么不能一直保持解锁？】EPROM 有擦写寿命（约 10 万次），
    /// 保持上锁可以防止程序 bug 导致的疯狂擦写把舵机写坏。
    pub fn save_reg(&mut self, id: u8, reg: &Register, value: i64) -> Result<()> {
        if reg.area != registers::Area::Eprom {
            // SRAM 区域本来就实时生效、掉电丢失，不需要解锁流程
            return self.write_reg(id, reg, value);
        }
        let lock = registers::find_register("lock").expect("lock 寄存器必定存在");
        self.write_reg(id, lock, 0)?; // 解锁
        let result = self.write_reg(id, reg, value);
        // 无论写入成功与否都重新上锁；`?` 前先拿到结果，避免错误导致忘了上锁
        let relock = self.write_reg(id, lock, 1);
        result.and(relock)
    }

    // =========================================================================
    // 高级语义 API（面向测试场景的便捷方法）
    // =========================================================================

    /// 一次性读回全部实时反馈（56~70 号地址共 15 字节，一次读比多次读快得多）。
    pub fn read_feedback(&mut self, id: u8) -> Result<Feedback> {
        let raw = self.read(id, 56, 15)?;
        if raw.len() < 15 {
            return Err(ServoError::Malformed("反馈数据长度不足"));
        }
        // 辅助闭包：取 2 字节小端值
        let u16le = |i: usize| u16::from_le_bytes([raw[i], raw[i + 1]]);
        Ok(Feedback {
            pos: registers::decode_value(u16le(0), Some(15)),        // 56 当前位置
            vel: registers::decode_value(u16le(2), Some(15)),        // 58 当前速度
            load: registers::decode_value(u16le(4), Some(10)),       // 60 当前负载 BIT10 方向位
            voltage: raw[6] as f64 / 10.0,                           // 62 电压 0.1V
            temp: raw[7] as i64,                                     // 63 温度 °C
            status: raw[9],                                          // 65 舵机状态
            moving: raw[10],                                         // 66 移动标志
            current: registers::decode_value(u16le(13), Some(15)) as f64 * 6.5, // 69 电流 6.5mA
        })
    }
}
