# HD1910M 舵机测试工具（feetech-HD1910M-tester）

基于 Rust 实现的飞特 HD1910M 串口舵机（FT-SCS 协议）测试工具，
覆盖官方内存表全部可读写寄存器：ID/波特率设置、位置/速度/电流三环 PID、
扭矩与力控、运行模式、保护参数、EPROM 锁存、实时监控等。

## 快速开始

```bash
cargo build
./target/debug/hd1910-tester scan      # 扫描舵机（带进度条）
./target/debug/hd1910-tester dump      # 读整张内存表
./target/debug/hd1910-tester monitor   # 实时监控反馈
./target/debug/hd1910-tester --help    # 全部命令
```

## 文档

完整的中文教学手册（协议讲解 + Rust 知识点 + 实验教程）见 **[TUTORIAL.md](TUTORIAL.md)**。

## 测试

```bash
cargo test    # 协议层/内存表层单元测试（无需硬件）
```
