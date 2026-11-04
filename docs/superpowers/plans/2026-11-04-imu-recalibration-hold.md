# IMU 重标站姿采集实施计划

> **For agentic workers:** 使用 executing-plans 的逐项验证方式执行；完成后按 requesting-code-review 要求独立审查。

**Goal:** 重做初次 IMU 标定时，可以先站姿使能、站稳，再采静止和前倾。

**Architecture:** 复用 commissioning hold-home 与现有采样路径。仅放开有效 redo 会话的站姿保持采集；模式切换仍严格卸力。用现有操作忙标记阻止 hold 与会话切换交错。

**Tech Stack:** Python unittest、Preact/htm、Node、现有本地 Edge 浏览器测试。

- [x] 核对设备实际部署代码与基线；现有后端/小助手 60 项测试通过。
- [ ] 先写后端回归测试：重标 hold、站姿采数/求解、无效状态拒绝、验证前卸力、并发忙检查；运行并保存预期失败。
- [ ] 最小修改 `studio/imu.py`：有效会话允许 hold，站稳采样沿用既有门槛，验证前单独要求卸力，站姿操作与会话切换互斥。
- [ ] 修改 `web/imu.js`，在 `tests/imu_bus_ui.mjs` 验证实际按钮与采集状态：可使能、爬升时禁采、站稳可采、持力时禁验证、卸力后保留结果。
- [ ] 运行相关后端/小助手/IMU 算法与实际浏览器测试，独立审查并修正重要问题。
- [ ] 提交修复，备份并部署两个变更文件，验证网页服务读取新代码及未改变主控/标定；不自动发动作。
