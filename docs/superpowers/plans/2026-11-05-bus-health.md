# Bus health and telemetry read-order implementation plan

> For agentic workers: execute with subagent-driven-development and TDD; independently scoped native and Studio work use distinct worktrees/file ownership. The coordinator verifies integration and obtains independent spec then quality reviews.

**Goal:** Show trustworthy live servo/IMU health and allow a reversible, unloaded telemetry read-order comparison.

**Architecture:** Native owns UART, per-ID request/reply evidence and guarded telemetry-order changes. Studio consumes owned snapshots and calculates bounded 60-second windows without a second UART reader. The existing pages display read-only status by default with explicit servo editing.

**Tech Stack:** Rust duck-control/robotd/duck-ipc-proto, Python Starlette Studio, local Preact/htm, Edge CDP browser tests.

## Contract and boundaries

- Native RPC `robot.feetechReadOrder` accepts `{mode: "default"|"id_ascending"|"id_last", last_id: 13}`; omitted last_id is valid only when not id_last. Only stopped policy, confirmed unloaded and restored gains allow writes. Preserve exact model and calibration joint ordering.
- `busStatus.native.read_order` exposes `mode`, nullable `last_id`, actual servo `ids`, and a generation/session discriminator. Publish stable readback after accepted changes. Legacy IMU first/last and full-Q4 dedicated transactions stay intact.
- Each native joint gains a `communication` object `{asked,ok,missed,streak,max_streak}` based only on actual sent requests and valid replies, independently of whole-cycle success. Missing/invalid packets are not invented per-ID checksum assignments.
- Studio `servo.read_order` bridges the native operation and applies the same order to its existing explicit debug-session owner; return actual readback. Persist safely if consistent with existing runtime config patterns; failures cannot claim saved/applied.
- Studio servo rows retain existing `comm` plus `health` window `{asked,ok,missed,rate,observed_s}` and `age_ms`, marking unavailable denominator as null. Snapshot-level `communication` exposes source/session/age and whole-bus counters/read_order.
- IMU status remains independent and source-aware. No rate derived from repeated web snapshots, no sensor ODR guessed from MCU sequence, no parameters reported current when cached/unknown.

## Task 1: Native telemetry evidence and ordering

Files: duck-control/src/feetech.rs, feetech_snapshot.rs and a focused helper module; robotd existing request/worker handling; duck-ipc-proto request enum; parameter/config code only where required by established persistence.

- [x] Add failing tests for original default, ascending, ID13-last, invalid/duplicate/missing IDs, protocol boundaries and ID-correct reply mapping.
- [x] Add failing tests for real sent-request denominators, partial good replies despite whole-cycle errors, unsent full-Q4/transport failures, and stat reset/session identity.
- [x] Implement the telemetry-only permutation and counters; preserve target writes and model ordering.
- [x] Implement guarded RPC, publish actual configuration and API/build install consequence, reject all enabled/unknown state changes.
- [x] Run WSL `cargo +1.99.0 test --offline -p duck-control feetech -- --test-threads=1` and targeted robotd/IPC tests using explicit worktree git binding and isolated shared cache ownership.

## Task 2: Studio health mapping and bridge

Files: studio/communication_health.py (new), servo.py, imu.py, state.py, robotd.py, app.py and tests/test_communication_health.py/test_servo.py.

- [x] Add failing fake-clock tests for finite 60s window, repeated snapshots, no denominator, counter rollback/source/session/order reset, stale/disconnected state, 15 independent rows and unknown checksum attribution.
- [x] Map both native snapshots and current debug owner statistics, preserve independently available startup diagnostics, expose runtime/EEPROM parameter source and read age.
- [x] Add read-order bridge tests: guarded unloaded operations, same policy across owners, persistence/readback failure, restore original order.
- [x] Reuse current subscriber lifecycle, faster read-only snapshot polling only while needed, no serial imports in robotd.py/no extra UART reader.
- [x] Run Python focused tests then existing servo/IMU/state regression tests.

## Task 3: Read-only UI and live details

Files: web/communication-health.js/.css (new), servo.js, imu.js, styles.css/index.html, browser tests and fake daemon fixtures as required.

- [x] Add failing UI tests for no automatic servo.open on page entry, explicit debug entry, changing temperature/voltage/counters without reopening details, null/stale state and monitor recovery.
- [x] Implement always-visible ID/name/angle/temp/voltage/recent rate/communication state. Expand source-labelled parameters and lifetime counters. Preserve editing functionality behind explicit existing owner acquisition.
- [x] Add IMU source/rate/age/gyro/quaternion/error card and guarded read-order control with restore default and actual readback.
- [x] Validate mobile and desktop in real browser using bounded fake data; capture screenshots and detect overflow.

## Task 4: Integration, review and local delivery

- [x] Verify native and Python contracts agree, source/session resets work and meaningful UI fixtures cover default/main/debug/startup modes.
- [x] Run focused native, Python and browser suites; broaden Studio regression once. Document any exact baseline-only failures rather than hiding them.
- [x] Independent spec review first, then code-quality review. Fix all material issues and rerun affected tests.
- [x] Commit isolated changes, write verification record and local operator comparison guide. Build ARM64 candidate if toolchain available, with exact source/hash provenance. No automatic flash/deploy/release or real motion.

## 执行结果

实现、独立审查与本地候选构建完成。原生及大范围浏览器回归的既有失败保留并做基线对照；不以全量绿色或实机改善交付。验证范围见 [验收记录](../verification/2026-11-05-bus-health.md)。本轮保留隔离分支和工作树，不发布、不部署。

## 用户指定的总览布局调整

- [x] 修改浏览器测试，要求总览诊断卡默认收起、就地展开、两标签和实时详情；校准页不再放新增诊断卡。先观察失败。
- [x] 新建 `web/runtime-diagnostics.js` 管理展开/标签、可视区需求及读取顺序回读；在 `web/app.js:Overview` 的 `TuneSection` 前放入该组件，心跳增加严格布尔需求；只读数据和控制权限保持分离。
- [x] 后台 `app.py` 仅对可见总览的诊断需求启用快照；测试收起、隐藏、离开和多客户端需求，不新增UART读取。后端与前端由不同执行者修改不同文件。
- [x] 将 `servo.js` 恢复专门校准布局，`imu.js` 保留原标定卡；移动新增健康信息到总览。运行聚焦Python、浏览器与原标零/交还流程，验证完整中控的新位置、手机布局和按需刷新，更新验收记录并提交。

## 紧凑舵机卡片

- [x] 浏览器增加宽屏15颗分3排、总高度受控的检查；详情仍实时刷新，手机不溢出。
- [x] `communication-health.js/.css` 将每颗改成可点卡片，按左腿/右腿/头颈分组横排；`runtime-diagnostics.js` 去掉常驻详情侧栏，详情在所选组下展开，读取顺序移到下方。
- [x] 跑现有诊断浏览器回归和完整模拟中控，检查5列桌面与2列手机截图，更新文档和预览并提交。

## 单排滑动（取代多排卡片）

- [x] 浏览器改为检查桌面与手机15颗始终单排、仅卡片带溢出、箭头可浏览到最后一颗、实时刷新保留滚动位置；默认监控高度小于500px。
- [x] `communication-health.js/.css` 合并成原生横向滚动卡片带，加左右箭头和分组标记；详情统一放下方。保持按需数据推送和所有控制边界。
- [x] 运行相关浏览器与完整模拟预览，更新截图、文档并提交本地分支。
