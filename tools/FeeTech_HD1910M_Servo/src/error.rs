//! error.rs —— 集中定义本项目所有可能出错的“错误类型”
//!
//! 【Rust 知识点：错误处理】
//! Rust 没有 Java/Python 那样的 try/catch 异常机制，而是把“可能失败”
//! 显式写进函数签名：返回 `Result<T, E>` —— 要么成功拿到 T，要么失败拿到 E。
//! 调用者必须处理这个 Result（用 match、if let、或 `?` 运算符向上传递），
//! 否则编译器直接报错。这就是 Rust 著名的“编译器逼你写健壮代码”。
//!
//! 【Rust 知识点：枚举（enum）】
//! Rust 的枚举比 C 的枚举强大得多：每个成员都可以携带不同类型的数据，
//! 更像“代数数据类型”。下面的每个错误成员都携带了相关的上下文信息。

use thiserror::Error;

/// 舵机工具的统一错误类型。
///
/// `#[derive(Error, Debug)]` 是“派生宏”：让 thiserror 库自动为我们的枚举
/// 实现标准库的 `std::error::Error` trait（特征/接口），
/// `#[error("...")]` 注解里的 `{0}`、`{1}` 会引用枚举成员携带的数据。
#[derive(Error, Debug)]
pub enum ServoError {
    /// 串口打开/读写失败。`#[from]` 表示可以用 `?` 把 serialport 的错误
    /// 自动转换成我们这个错误类型，省去手写转换代码。
    #[error("串口错误: {0}")]
    Serial(#[from] serialport::Error),

    /// 标准 IO 错误（比如读串口时的底层错误）
    #[error("IO 错误: {0}")]
    Io(#[from] std::io::Error),

    /// 等待舵机应答超时 —— 最常见的错误：线没接好 / ID 不对 / 波特率不对
    #[error("响应超时：舵机未应答（检查接线、ID、波特率）")]
    Timeout,

    /// 应答包校验和不对，说明线路有干扰或数据被截断
    #[error("应答包校验和错误")]
    Checksum,

    /// 应答包格式不符合协议（帧头不对、长度不对等）
    #[error("应答包格式错误: {0}")]
    Malformed(&'static str),

    /// 舵机返回了错误状态字（电压/温度/电流/编码器异常），
    /// 第一个字段是原始状态字节，第二个字段是解析后的中文描述。
    #[error("舵机报告异常 (状态字 0x{0:02X}): {1}")]
    ServoStatus(u8, String),

    /// 用户输入的参数不合法（超范围、寄存器只读等）
    #[error("参数错误: {0}")]
    InvalidParam(String),
}

/// 【Rust 知识点：类型别名】
/// 给又长又常用的类型起个短名字。标准写法 `Result<T, ServoError>`
/// 在本项目里统一简写为 `Result<T>`。
/// 注意它“遮盖”了标准库的 Result，这是 Rust 社区的常见惯例。
pub type Result<T> = std::result::Result<T, ServoError>;
