# IMU HOME 原位验证

1104 原配主程序支持在 HOME 站稳后采集方向。保存时，双手扶稳并卸力，Studio 会保留已采数据并切换到原有的验证流程。

如果主程序的 `calibration-status` 明确声明 `imu_home_verification_contract=home_mount_v1`，Studio 可保持站姿完成验证和保存。点击验证时会重新读取该能力和原方向，随后通过 `verify-imu-home` 发送候选方向和预期原方向。独立直立、前倾验证和已保存四元数读回均通过后，页面显示完成。

先停止运动策略，双手扶稳设备，保持 HOME。点击验证前回正停稳，随后按提示在 20 秒内前倾 15–30°。采集期间来源、板卡或原安装方向改变时，重新采集。验证时连接中断，重连后检查当前方向。

主程序可以用 `imu_calibration_hold_ready` 或标定模式的 `hold.imu_calibration_ready` 报告站姿就绪。缺少该标志时，Studio 继续按关节误差 0–5° 判断站稳。运动模式采集还需要新鲜的关节反馈和 IMU 数据，IMU 年龄可以使用主程序给出的上界。
