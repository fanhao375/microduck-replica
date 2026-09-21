//! registers.rs —— HD1910M 舵机内存表的完整“数据化”定义
//!
//! 内存表就是舵机内部的一张“参数登记表”：每个地址存放一项功能参数。
//! 这张表来自飞特官方文档（doc.feetech.cn 的 HLS_2 内存表），分为 5 个区域：
//!
//!   地址 0 ~ 4    版本信息    （只读：固件版本号等）
//!   地址 5 ~ 39   EPROM 配置  （可读写，配合“锁标志”可掉电保存）
//!   地址 40 ~ 55  SRAM 控制   （可读写，实时控制区，掉电丢失）
//!   地址 56 ~ 75  SRAM 反馈   （只读：当前位置/速度/电压/温度/电流…）
//!   地址 77 ~ 86  出厂参数    （只读：工厂标定值）
//!
//! 【Rust 知识点：把“文档表格”变成“编译期常量”】
//! 我们把整张表写成一个 `&'static [Register]` 常量数组。
//! 'static 生命周期表示这些数据从程序启动到结束一直存在，
//! 不占堆内存，访问零开销。命令行工具通过查这张表来校验参数、打印说明，
//! 新增寄存器只需要在表里加一行。

/// 寄存器读写权限
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    /// 只读（反馈量、版本号、出厂标定值）
    ReadOnly,
    /// 可读写（配置项、控制项）
    ReadWrite,
}

impl Access {
    pub fn as_str(&self) -> &'static str {
        match self {
            Access::ReadOnly => "只读",
            Access::ReadWrite => "读写",
        }
    }
}

/// 内存表分区
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Area {
    Version,      // 版本信息
    Eprom,        // EPROM 配置（可掉电保存）
    SramControl,  // SRAM 实时控制
    SramFeedback, // SRAM 实时反馈
    Factory,      // 出厂标定参数
}

impl Area {
    pub fn as_str(&self) -> &'static str {
        match self {
            Area::Version => "版本信息",
            Area::Eprom => "EPROM配置",
            Area::SramControl => "SRAM控制",
            Area::SramFeedback => "SRAM反馈",
            Area::Factory => "出厂参数",
        }
    }
}

/// 一条寄存器定义（对应官方内存表中的一行）。
///
/// 【Rust 知识点：&'static str】
/// 字符串字面量（如 "目标位置"）直接编译进可执行文件，
/// 生命周期为 'static（与程序同寿），存进结构体不需要堆分配。
pub struct Register {
    /// 起始地址（十进制，与官方文档一致）
    pub addr: u8,
    /// 命令行使用的英文键名（如 "pos-p"），全小写
    pub key: &'static str,
    /// 中文名称（与官方文档一致）
    pub cn: &'static str,
    /// 占用字节数：1 或 2（2 字节数据低字节在前 = 小端存储）
    pub size: u8,
    pub access: Access,
    pub area: Area,
    /// 物理单位（每个 LSB 代表多少），如 "0.1V"、"6.5mA"
    pub unit: &'static str,
    /// 合法取值范围（写入前做校验，防止写坏舵机配置）
    pub min: i64,
    pub max: i64,
    /// 符号-幅值编码的符号位位置：
    ///   None     —— 无符号数
    ///   Some(15) —— BIT15 是方向位（位置/速度/电流等）
    ///   Some(10) —— BIT10 是方向位（当前负载）
    /// 飞特协议用“符号位 + 绝对值”表示负数，而不是常见的二进制补码！
    pub sign_bit: Option<u8>,
    /// 备注说明
    pub desc: &'static str,
}

/// 整张内存表（与官方 HLS_2 文档逐行对应）。
///
/// 【Rust 知识点：常量数组】
/// 用 const 声明，编译期就确定全部内容。遍历时就是普通的数组迭代。
pub const REGISTERS: &[Register] = &[
    // ---------------- 2.1 版本信息（只读） ----------------
    Register { addr: 0,  key: "fw-major",    cn: "固件主版本号", size: 1, access: Access::ReadOnly,  area: Area::Version, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "固件主版本号" },
    Register { addr: 1,  key: "fw-minor",    cn: "固件次版本号", size: 1, access: Access::ReadOnly,  area: Area::Version, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "固件次版本号" },
    Register { addr: 2,  key: "endian",      cn: "END",         size: 1, access: Access::ReadOnly,  area: Area::Version, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "0 表示小端存储结构" },
    Register { addr: 3,  key: "servo-major", cn: "舵机主版本号", size: 1, access: Access::ReadOnly,  area: Area::Version, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "舵机主版本号" },
    Register { addr: 4,  key: "servo-minor", cn: "舵机次版本号", size: 1, access: Access::ReadOnly,  area: Area::Version, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "舵机次版本号" },

    // ---------------- 2.2 EPROM 配置（读写，可掉电保存） ----------------
    Register { addr: 5,  key: "id",              cn: "主ID",           size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "号",     min: 0, max: 253,   sign_bit: None, desc: "总线上唯一的主ID（出厂默认1），对主ID写入会同步修改副ID" },
    Register { addr: 6,  key: "baud",            cn: "波特率",         size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 7,     sign_bit: None, desc: "0~7 = 1000000/500000/250000/128000/115200/76800/57600/38400" },
    Register { addr: 7,  key: "id2",             cn: "副ID",           size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "号",     min: 0, max: 253,   sign_bit: None, desc: "第二标识，适用于写/异步写/异步执行/同步写指令" },
    Register { addr: 8,  key: "resp-level",      cn: "应答状态级别",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 1,     sign_bit: None, desc: "0: 除读与PING外不应答；1: 所有指令都应答" },
    Register { addr: 9,  key: "min-angle",       cn: "最小角度限制",   size: 2, access: Access::ReadWrite, area: Area::Eprom, unit: "0.087度", min: 0, max: 4094,  sign_bit: None, desc: "多圈绝对位置控制时此值为0" },
    Register { addr: 11, key: "max-angle",       cn: "最大角度限制",   size: 2, access: Access::ReadWrite, area: Area::Eprom, unit: "0.087度", min: 1, max: 4095,  sign_bit: None, desc: "多圈绝对位置控制时此值为0" },
    Register { addr: 13, key: "max-temp",        cn: "最高温度上限",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "°C",    min: 0, max: 100,   sign_bit: None, desc: "超过此温度关闭扭矩输出（出厂默认70°C开启保护）" },
    Register { addr: 14, key: "max-voltage",     cn: "最高输入电压",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "0.1V",  min: 0, max: 254,   sign_bit: None, desc: "超过此电压进入过压保护" },
    Register { addr: 15, key: "min-voltage",     cn: "最低输入电压",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "0.1V",  min: 0, max: 254,   sign_bit: None, desc: "低于此电压进入欠压保护（出厂默认4.0V）" },
    Register { addr: 16, key: "max-torque",      cn: "最大扭矩",       size: 2, access: Access::ReadWrite, area: Area::Eprom, unit: "0.1%",  min: 0, max: 1000,  sign_bit: None, desc: "上电时赋值给48号地址转矩限制" },
    Register { addr: 18, key: "phase",           cn: "相位",           size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "特殊功能字节（方向/编码器/PWM频率等），无特别需求不可修改！" },
    Register { addr: 19, key: "unload-cond",     cn: "卸载条件",       size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "位设置: BIT0电压 BIT1磁编码 BIT2过热 BIT3过流，1=开启保护" },
    Register { addr: 20, key: "led-alarm",       cn: "LED报警条件",    size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "位设置: BIT0电压 BIT1磁编码 BIT2过热 BIT3过流，1=开启闪灯报警" },
    Register { addr: 21, key: "pos-p",           cn: "位置环P比例系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "上电赋值给50号地址Kp（位置环比例，1/8）" },
    Register { addr: 22, key: "pos-d",           cn: "位置环D微分系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "上电赋值给51号地址Kd（位置环微分，1/4）" },
    Register { addr: 23, key: "pos-i",           cn: "位置环I积分系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "上电赋值给52号地址Ki" },
    Register { addr: 24, key: "min-start-force", cn: "最小启动力",     size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "0.1%",  min: 0, max: 254,   sign_bit: None, desc: "舵机的最小输出启动扭矩" },
    Register { addr: 25, key: "integral-limit",  cn: "积分限制值",     size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "最大积分值=该值*4，0=关闭积分限制，模式0与模式4生效" },
    Register { addr: 26, key: "deadband-cw",     cn: "正向不灵敏区",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "0.087度", min: 0, max: 16,    sign_bit: None, desc: "最小单位为一个最小分辨角度" },
    Register { addr: 27, key: "deadband-ccw",    cn: "负向不灵敏区",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "0.087度", min: 0, max: 16,    sign_bit: None, desc: "最小单位为一个最小分辨角度" },
    Register { addr: 28, key: "protect-current", cn: "保护电流",       size: 2, access: Access::ReadWrite, area: Area::Eprom, unit: "6.5mA", min: 0, max: 2047,  sign_bit: None, desc: "上电赋值给44号地址目标电流" },
    Register { addr: 30, key: "angle-resolution",cn: "角度分辨率",     size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 1, max: 128,   sign_bit: None, desc: "对传感器最小分辨角度的放大系数" },
    Register { addr: 31, key: "pos-offset",      cn: "位置偏移",       size: 2, access: Access::ReadWrite, area: Area::Eprom, unit: "0.087度", min: -4095, max: 4095, sign_bit: Some(15), desc: "BIT15为方向位，用于校正机械零位" },
    Register { addr: 33, key: "mode",            cn: "运行模式",       size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 4,     sign_bit: None, desc: "0位置伺服(位置+限流) 1电机恒速 2电机恒流 3PWM开环调速 4纯位置PD(Sim2Real)" },
    Register { addr: 34, key: "cur-p",           cn: "电流环P比例系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "电流环比例系数（力控核心参数）" },
    Register { addr: 35, key: "cur-i",           cn: "电流环I积分系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 254,   sign_bit: None, desc: "电流环积分系数（力控核心参数）" },
    Register { addr: 36, key: "reserved-36",     cn: "无定义(36)",     size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",      min: 0, max: 255,   sign_bit: None, desc: "官方文档未定义，谨慎修改" },
    Register { addr: 37, key: "vel-p",           cn: "速度闭环P比例系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",   min: 0, max: 254,   sign_bit: None, desc: "电机恒速模式(模式1)下速度环比例系数，上电赋值给50号Kp" },
    Register { addr: 38, key: "overcur-time",    cn: "过流保护时间",   size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "10ms",  min: 0, max: 254,   sign_bit: None, desc: "电流超限持续该时间后进入过流保护" },
    Register { addr: 39, key: "vel-i",           cn: "速度闭环I积分系数", size: 1, access: Access::ReadWrite, area: Area::Eprom, unit: "",   min: 0, max: 254,   sign_bit: None, desc: "电机恒速模式(模式1)下速度环积分系数，上电赋值给52号Ki" },

    // ---------------- 2.3 SRAM 控制（读写，掉电丢失） ----------------
    Register { addr: 40, key: "torque-switch", cn: "扭矩开关",   size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 2,     sign_bit: None, desc: "0:关闭扭力输出 1:打开扭力输出 2:阻尼输出" },
    Register { addr: 41, key: "acc",           cn: "加速度",     size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "8.7度/秒²", min: 0, max: 254, sign_bit: None, desc: "运行加/减速度，0表示最大加速度" },
    Register { addr: 42, key: "goal-pos",      cn: "目标位置",   size: 2, access: Access::ReadWrite, area: Area::SramControl, unit: "0.087度", min: -32767, max: 32767, sign_bit: Some(15), desc: "绝对位置控制，BIT15为方向位；多圈模式可超4095" },
    Register { addr: 44, key: "goal-current",  cn: "目标电流",   size: 2, access: Access::ReadWrite, area: Area::SramControl, unit: "6.5mA", min: -2047, max: 2047, sign_bit: Some(15), desc: "除模式3外控制电机最大运行电流；恒流模式BIT15为电流方向位；PWM模式范围-1000~1000且BIT10为方向位" },
    Register { addr: 46, key: "speed",         cn: "运行速度",   size: 2, access: Access::ReadWrite, area: Area::SramControl, unit: "0.732RPM", min: -32767, max: 32767, sign_bit: Some(15), desc: "电机最高运行速度，0=停止；恒速模式BIT15为速度方向位" },
    Register { addr: 48, key: "torque-limit",  cn: "转矩限制",   size: 2, access: Access::ReadWrite, area: Area::SramControl, unit: "0.1%",  min: 0, max: 1000,  sign_bit: None, desc: "控制堵转扭矩输出（上电时=16号最大扭矩）" },
    Register { addr: 50, key: "kp",            cn: "运行Kp",     size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 254,   sign_bit: None, desc: "位置伺服:位置环P(1/8)；恒速模式:速度环P（运行时实时生效）" },
    Register { addr: 51, key: "kd",            cn: "运行Kd",     size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 254,   sign_bit: None, desc: "位置伺服:位置环D(1/4)；恒速模式:无效" },
    Register { addr: 52, key: "ki",            cn: "运行Ki",     size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 254,   sign_bit: None, desc: "位置伺服:无效；恒速模式:速度环I" },
    Register { addr: 53, key: "reserved-53",   cn: "无定义(53)", size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 255,   sign_bit: None, desc: "官方文档未定义，谨慎修改" },
    Register { addr: 54, key: "reserved-54",   cn: "无定义(54)", size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 255,   sign_bit: None, desc: "官方文档未定义，谨慎修改" },
    Register { addr: 55, key: "lock",          cn: "锁标志",     size: 1, access: Access::ReadWrite, area: Area::SramControl, unit: "",     min: 0, max: 1,     sign_bit: None, desc: "0:关闭写入锁(EPROM写值掉电保存) 1:打开写入锁(掉电不保存，默认)" },

    // ---------------- 2.4 SRAM 反馈（只读） ----------------
    Register { addr: 56, key: "pos",            cn: "当前位置",   size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "0.087度",  min: -32767, max: 32767, sign_bit: Some(15), desc: "当前绝对位置，BIT15为方向位（多圈模式含圈数）" },
    Register { addr: 58, key: "vel",            cn: "当前速度",   size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "0.732RPM", min: -32767, max: 32767, sign_bit: Some(15), desc: "当前电机转动速度，BIT15为方向位" },
    Register { addr: 60, key: "load",           cn: "当前负载",   size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "0.1%",     min: -1000, max: 1000,   sign_bit: Some(10), desc: "驱动电机的电压占空比，BIT10为方向位" },
    Register { addr: 62, key: "voltage",        cn: "当前电压",   size: 1, access: Access::ReadOnly, area: Area::SramFeedback, unit: "0.1V",    min: 0, max: 255,   sign_bit: None, desc: "当前舵机工作电压" },
    Register { addr: 63, key: "temp",           cn: "当前温度",   size: 1, access: Access::ReadOnly, area: Area::SramFeedback, unit: "°C",     min: 0, max: 255,   sign_bit: None, desc: "当前舵机内部工作温度" },
    Register { addr: 64, key: "async-flag",     cn: "异步写标志", size: 1, access: Access::ReadOnly, area: Area::SramFeedback, unit: "",       min: 0, max: 255,   sign_bit: None, desc: "采用异步写指令时的标志位" },
    Register { addr: 65, key: "status",         cn: "舵机状态",   size: 1, access: Access::ReadOnly, area: Area::SramFeedback, unit: "",       min: 0, max: 255,   sign_bit: None, desc: "BIT0电压 BIT1磁编码 BIT2温度 BIT3电流，置1=异常" },
    Register { addr: 66, key: "moving",         cn: "移动标志",   size: 1, access: Access::ReadOnly, area: Area::SramFeedback, unit: "",       min: 0, max: 255,   sign_bit: None, desc: "BIT0:运动中为1；BIT1:运动中为1/到位停止为0" },
    Register { addr: 67, key: "goal-pos-fb",    cn: "目标位置反馈", size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "0.087度", min: 0, max: 65535, sign_bit: None, desc: "当前目标位置" },
    Register { addr: 69, key: "current",        cn: "当前电流",   size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "6.5mA",   min: -32767, max: 32767, sign_bit: Some(15), desc: "当前电机相电流，BIT15为方向位" },
    Register { addr: 71, key: "reserved-71",    cn: "无定义(71)", size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "",       min: 0, max: 65535, sign_bit: None, desc: "官方文档未定义" },
    Register { addr: 73, key: "current-offset", cn: "电流偏置",   size: 2, access: Access::ReadOnly, area: Area::SramFeedback, unit: "",       min: 0, max: 65535, sign_bit: None, desc: "电流0点偏移值" },

    // ---------------- 2.5 出厂参数（只读，工厂标定） ----------------
    Register { addr: 77, key: "v-fk",            cn: "vFk(*10)",     size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 78, key: "v-kgi",           cn: "vKgI",         size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 79, key: "p-fk",            cn: "pFk(*10)",     size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 80, key: "move-speed-th",   cn: "移动速度阀值", size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "判定“是否在运动”的速度阀值" },
    Register { addr: 81, key: "dts",             cn: "DTs(ms)",      size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 82, key: "e-fk",            cn: "eFk(*10)",     size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 83, key: "vk",              cn: "Vk(ms)",       size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 84, key: "max-speed-limit", cn: "最大速度限制", size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 85, key: "acc-limit",       cn: "加速度限制",   size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
    Register { addr: 86, key: "acc-multi",       cn: "加速度倍数",   size: 1, access: Access::ReadOnly, area: Area::Factory, unit: "", min: 0, max: 255, sign_bit: None, desc: "工厂标定参数" },
];

/// 波特率索引表：内存表 6 号地址的值 0~7 对应的实际波特率。
pub const BAUD_RATES: [u32; 8] = [
    1_000_000, 500_000, 250_000, 128_000, 115_200, 76_800, 57_600, 38_400,
];

/// 按“英文键名”或“地址”查找寄存器。
///
/// 支持的写法（命令行友好）：
///   - 键名： "pos-p"、"id"、"torque-limit"（大小写不敏感）
///   - 十进制地址："21"
///   - 十六进制地址："0x15"
///
/// 【Rust 知识点：Option<T>】
/// “可能找不到”用 `Option` 表达：Some(寄存器) 或 None。
/// 这是 Rust 替代 null 的方案 —— 编译器强制你处理“找不到”的情况，
/// 从根源上消灭空指针异常。
///
/// 【Rust 知识点：迭代器链式调用】
/// `.iter().find(...)` 是函数式风格：遍历数组，返回第一个满足条件的元素。
pub fn find_register(name_or_addr: &str) -> Option<&'static Register> {
    let key = name_or_addr.to_lowercase();

    // 先尝试按地址解析："0x15"（十六进制）或 "21"（十进制）
    let addr: Option<u8> = if let Some(hex) = key.strip_prefix("0x") {
        u8::from_str_radix(hex, 16).ok() // .ok() 把 Result 转成 Option
    } else {
        key.parse::<u8>().ok()
    };
    if let Some(a) = addr {
        return REGISTERS.iter().find(|r| r.addr == a);
    }

    // 再按键名匹配
    REGISTERS.iter().find(|r| r.key == key)
}

/// 符号-幅值解码：把协议里的原始 u16 还原成有符号整数。
///
/// 飞特协议的负数不是补码！而是“符号位 + 绝对值”：
///   sign_bit 位置 1 表示负，其余位是绝对值。
///   例如位置 -100 编码为 0x8000 | 100 = 0x8064。
pub fn decode_value(raw: u16, sign_bit: Option<u8>) -> i64 {
    match sign_bit {
        Some(bit) => {
            let sign_mask = 1u16 << bit;        // 符号位掩码，如 0x8000
            let mag_mask = sign_mask - 1;       // 绝对值掩码，如 0x7FFF
            let mag = (raw & mag_mask) as i64;
            if raw & sign_mask != 0 { -mag } else { mag }
        }
        None => raw as i64,
    }
}

/// 符号-幅值编码：把有符号整数打包成协议要求的 u16。
pub fn encode_value(value: i64, sign_bit: Option<u8>) -> u16 {
    match sign_bit {
        Some(bit) => {
            let sign_mask = 1u16 << bit;
            let mag = value.unsigned_abs() as u16; // unsigned_abs: |v| 直接得到无符号数
            if value < 0 { mag | sign_mask } else { mag }
        }
        None => value as u16,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_by_key_and_addr() {
        assert_eq!(find_register("pos-p").unwrap().addr, 21);
        assert_eq!(find_register("POS-P").unwrap().addr, 21); // 大小写不敏感
        assert_eq!(find_register("21").unwrap().key, "pos-p");
        assert_eq!(find_register("0x15").unwrap().key, "pos-p");
        assert!(find_register("not-exist").is_none());
    }

    #[test]
    fn test_sign_magnitude_roundtrip() {
        // 位置 -100 应编码为 0x8064，解码应还原
        let raw = encode_value(-100, Some(15));
        assert_eq!(raw, 0x8064);
        assert_eq!(decode_value(raw, Some(15)), -100);
        // 无符号寄存器不受影响
        assert_eq!(decode_value(1000, None), 1000);
        // 负载用 BIT10 做符号位：-500 → 0x0400 | 500
        let raw = encode_value(-500, Some(10));
        assert_eq!(decode_value(raw, Some(10)), -500);
    }
}
