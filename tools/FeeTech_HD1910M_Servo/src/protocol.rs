//! protocol.rs —— FT-SCS 自定义串口协议（飞特舵机私有协议）
//!
//! 【协议帧格式 —— 主机下发包】
//! ```text
//!   字节序号:   0     1     2      3         4          5..N+4    N+5
//!   内容:     0xFF  0xFF   ID   Length   Instruction   参数...   Checksum
//! ```
//!   - 帧头：固定的两个 0xFF，用来让接收方找到一帧的起点；
//!   - ID：目标舵机站号 0~253，0xFE(254) 是广播地址（所有舵机都收但不应答）；
//!   - Length = 参数个数 N + 2（指令字节 1 个 + 校验字节 1 个）；
//!   - Checksum = 对 ID + Length + Instruction + 所有参数 求和，
//!     取反(~)后只保留低 8 位。接收方重新算一遍，不一致就丢弃。
//!
//! 【舵机应答包】格式几乎一样，只是第 4 个字节从“指令”换成了“错误状态字”：
//! ```text
//!   0xFF  0xFF   ID   Length   Error   参数...   Checksum
//! ```
//!
//! 【Rust 知识点：为什么这一层不碰串口？】
//! 这一层只做“字节数组 <-> 结构体”的纯数据转换，不知道串口的存在。
//! 好处是：可以脱离硬件做单元测试（见文件底部的 #[cfg(test)] 模块），
//! 这是“分层设计”的典型做法。

use crate::error::{Result, ServoError};

/// 帧头：FT-SCS 协议固定用两个 0xFF 作为一帧的开始标志。
pub const HEADER: [u8; 2] = [0xFF, 0xFF];

/// 广播 ID：总线上所有舵机都会执行，但都不返回应答包。
/// 常用于同步写多个舵机。扫描时千万不要用它来判断舵机是否存在。
pub const BROADCAST_ID: u8 = 0xFE;

/// 协议指令集（内存表文档第 7、19 行提到的指令都在这里）。
///
/// 【Rust 知识点：#[repr(u8)]】
/// 默认情况下 Rust 枚举的内存布局是不对外保证的；加上 #[repr(u8)]
/// 就强制每个成员用 1 个字节表示，值就是我们指定的数字，
/// 这样 `Instruction::Ping as u8` 就能安全地得到协议字节 0x01。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Instruction {
    /// 0x01 查询舵机是否在线（握手），正常工作舵机应回应答包
    Ping = 0x01,
    /// 0x02 读内存表：参数 = [起始地址, 读取字节数]
    Read = 0x02,
    /// 0x03 写内存表：参数 = [起始地址, 数据...]，立即生效
    Write = 0x03,
    /// 0x04 异步写（REG WRITE）：参数同写指令，但先“记下来”不执行，
    /// 等收到 ACTION 指令才统一执行（用于多舵机同步动作）
    RegWrite = 0x04,
    /// 0x05 触发执行之前 REG WRITE 缓存的内容
    Action = 0x05,
    /// 0x06 恢复出厂设置（EPROM 全部回到默认值）
    Reset = 0x06,
    /// 0x83 同步写：一条指令同时给多个舵机写同一个地址
    SyncWrite = 0x83,
}

/// 计算 FT-SCS 校验和：所有字节相加后按位取反，只留低 8 位。
///
/// 【Rust 知识点：`impl IntoIterator` 与泛型】
/// 参数用泛型 + trait bound，让本函数既能接收 `&[u8]` 也能接收 `Vec<u8>`，
/// 调用方更自由。`.iter()` 产生迭代器，`.fold(0u8, ...)` 是“折叠”累加。
/// wrapping_add 是“溢出就回绕”的加法 —— 校验和本来就要模 256，
/// 如果用普通 `+`，调试模式下溢出会直接 panic（程序崩溃），
/// 这是 Rust 帮你发现算术错误的保护机制，这里我们显式声明“我就是要回绕”。
pub fn checksum(bytes: &[u8]) -> u8 {
    let sum = bytes
        .iter()
        .fold(0u8, |acc, &b| acc.wrapping_add(b));
    !sum // `!` 对整数是按位取反（NOT），对 bool 才是逻辑非
}

/// 组装一条完整的下发包。
///
/// 【Rust 知识点：Vec 与所有权】
/// 函数内部创建 Vec<u8> 并把它“返回”给调用者 —— 所有权随之转移，
/// 没有 malloc/free，也没有垃圾回收，编译器在编译期就确定了谁负责释放。
pub fn build_packet(id: u8, inst: Instruction, params: &[u8]) -> Vec<u8> {
    // Length = 指令(1) + 参数个数 + 校验(1)
    let length = (params.len() + 2) as u8;

    // with_capacity 预分配恰好够用的内存，避免 push 时反复扩容
    let mut pkt = Vec::with_capacity(params.len() + 6);
    pkt.extend_from_slice(&HEADER);
    pkt.push(id);
    pkt.push(length);
    pkt.push(inst as u8);
    pkt.extend_from_slice(params);
    // 校验和覆盖 ID..=最后一个参数（不含帧头）
    pkt.push(checksum(&pkt[2..]));
    pkt
}

/// 解析后的舵机应答包。
pub struct StatusPacket {
    /// 应答的舵机 ID
    pub id: u8,
    /// 错误状态字：0 表示正常；非 0 时每一位代表一种异常
    /// （BIT0 电压 / BIT1 磁编码 / BIT2 温度 / BIT3 电流）
    pub error: u8,
    /// 应答携带的数据（读指令的返回值就放在这里）
    pub params: Vec<u8>,
}

/// 把收到的一串原始字节解析成 StatusPacket，并做完整校验。
///
/// 【Rust 知识点：用 Result 做“可组合的校验流水线”】
/// 每一步检查失败就提前 return Err(...)，成功路径一路平坦地写下去，
/// 避免了层层嵌套的 if/else（所谓“卫语句”风格）。
pub fn parse_status(buf: &[u8]) -> Result<StatusPacket> {
    // 一帧最少 6 字节：帧头2 + ID + Length + Error + Checksum
    if buf.len() < 6 {
        return Err(ServoError::Malformed("应答包太短，不足 6 字节"));
    }
    if buf[0] != HEADER[0] || buf[1] != HEADER[1] {
        return Err(ServoError::Malformed("帧头不是 0xFF 0xFF"));
    }
    let id = buf[2];
    let length = buf[3] as usize;
    // Length 字段声称的总长度 = 帧头2 + ID + Length + Length的值
    if buf.len() != length + 4 {
        return Err(ServoError::Malformed("包长度与 Length 字段不符"));
    }
    // 重新计算校验和（范围同样是 ID 到校验字节之前）
    let expect = checksum(&buf[2..buf.len() - 1]);
    if expect != buf[buf.len() - 1] {
        return Err(ServoError::Checksum);
    }
    Ok(StatusPacket {
        id,
        error: buf[4],
        // 参数区 = 错误字节之后、校验字节之前；可能为空（比如 PING 应答）
        params: buf[5..buf.len() - 1].to_vec(),
    })
}

/// 把舵机状态字（应答包 Error 字节 / 内存表 65 号地址）翻译成中文描述。
///
/// 【Rust 知识点：位运算 + Vec<String> + join】
/// 状态字是按位编码的：BIT0(1)=电压 BIT1(2)=磁编码 BIT2(4)=温度 BIT3(8)=电流。
/// 多种异常同时存在时值为各位之和，所以要用 `&` 逐位测试。
pub fn describe_status(status: u8) -> String {
    if status == 0 {
        return "正常".to_string();
    }
    let mut problems: Vec<&str> = Vec::new();
    // 【Rust 知识点：迭代器式位检查】这里用普通的数组循环，简单直白
    let bits = [
        (1 << 0, "电压异常"),
        (1 << 1, "磁编码器异常"),
        (1 << 2, "过热"),
        (1 << 3, "过流"),
    ];
    for (bit, name) in bits {
        if status & bit != 0 {
            problems.push(name);
        }
    }
    problems.join(" + ")
}

// =============================================================================
// 【Rust 知识点：单元测试】
// `#[cfg(test)]` 表示这个模块只在 `cargo test` 时才编译，正式发布时不包含。
// 协议层是纯函数，不依赖硬件，最适合用单元测试来保证正确性。
// =============================================================================
#[cfg(test)]
mod tests {
    use super::*; // 引入父模块的全部内容（super = 上一级模块）

    #[test]
    fn test_build_ping_packet() {
        // PING ID=1 的经典报文：FF FF 01 02 01 FB
        let pkt = build_packet(1, Instruction::Ping, &[]);
        assert_eq!(pkt, vec![0xFF, 0xFF, 0x01, 0x02, 0x01, 0xFB]);
    }

    #[test]
    fn test_checksum_wraps() {
        // 0xFF + 0xFF 会溢出 u8，wrapping_add 应该回绕而不是 panic
        assert_eq!(checksum(&[0xFF, 0xFF]), !0xFEu8);
    }

    #[test]
    fn test_parse_status_roundtrip() {
        // 自己造一个合法的应答包（读地址 56 返回 2 字节），再解析回来
        let raw = [0xFF, 0xFF, 0x01, 0x04, 0x00, 0x00, 0x08, 0xF2];
        let pkt = parse_status(&raw).expect("合法包应该解析成功");
        assert_eq!(pkt.id, 1);
        assert_eq!(pkt.error, 0);
        assert_eq!(pkt.params, vec![0x00, 0x08]); // 小端：位置 = 0x0800 = 2048
    }

    #[test]
    fn test_parse_bad_checksum() {
        let raw = [0xFF, 0xFF, 0x01, 0x04, 0x00, 0x00, 0x08, 0x00]; // 校验字节故意写错
        assert!(matches!(parse_status(&raw), Err(ServoError::Checksum)));
    }
}
