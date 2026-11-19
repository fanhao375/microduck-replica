# IMU HOME 原位验证

固件 `calibration-status` 声明 `imu_home_verification_contract=home_mount_v1` 时，Studio 可在 HOME 保持站姿下采集、验证、保存并应用安装方向。验证使用 `verify-imu-home`，携带候选方向和预期原方向；验证证明与读回均通过后才显示完成。

兼容固件的 `imu_calibration_hold_ready` / `imu_calibration_ready` 就绪标志及 IMU 年龄上界字段；旧固件仍采用关节误差阈值和原有卸力验证流程。此 PR 不实现固件协议，不会让旧固件获得原位验证能力。

先停止运动策略、双手扶稳设备并保持 HOME。点击验证前回正停稳，随后按提示在 20 秒内前倾 15–30°。采集期间来源、板卡或原安装方向改变需重新采集；连接断开不宣称写入成功，重连后检查读回。保留舵机零位、方向与限位。

包含原有 IMU 后端/标定/诊断/HOME 回归，以及新增契约、就绪字段和原位 RPC 路径测试。未在本次独立分支重做真机验收。
