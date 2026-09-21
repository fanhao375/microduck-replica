//! lib.rs —— 库目标：把舵机驱动能力暴露为“可被其他程序复用的库”
//!
//! 【Rust 知识点：一个 crate 可以同时是“程序”和“库”】
//! Cargo 约定：
//!   - src/main.rs  → 编译成可执行文件（二进制目标）
//!   - src/lib.rs   → 编译成库（库目标，可以被别的项目依赖）
//! 两者可以共存于同一个包里！这样做的好处：
//!   - 我们的 CLI 工具（main.rs）继续存在；
//!   - GUI 项目可以通过路径依赖 `path = "../FeeTech_HD1910M_Servo"` 直接使用
//!     这里的 protocol / registers / servo / error 模块，**代码零重复**。
//!
//! 这正是分层设计的回报：底层模块不依赖 main.rs 里的任何 CLI 代码，
//! 所以可以直接被另一种“前端”（图形界面）复用。
//!
//! 库的名字在 Cargo.toml 的 [lib] 段指定为 feetech_servo
//! （Rust 标识符不能含 `-`，所以用下划线）。

pub mod error;
pub mod protocol;
pub mod registers;
pub mod servo;
