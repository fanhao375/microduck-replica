# Bus health and telemetry read-order implementation plan

> For agentic workers: execute with subagent-driven-development and TDD; independently scoped native and Studio work use distinct worktrees/file ownership. The coordinator verifies integration and obtains independent spec then quality reviews.

**Goal:** Show trustworthy live servo/IMU health and allow a reversible, unloaded telemetry read-order comparison.

**Architecture:** Native owns UART, per-ID request/reply evidence and guarded telemetry-order changes. Studio consumes owned snapshots and calculates bounded 60-second windows without a second UART reader. The existing pages display read-only status by default with explicit servo editing.

**Tech Stack:** Rust duck-control/robotd/duck-ipc-proto, Python Starlette Studio, local Preact/htm, Playwright tests.

## Contract and boundaries

- Native RPC `robot.feetechReadOrder` accepts `{mode: "default"|"id_ascending"|"id_last", last_id: 13}`; omitted last_id is valid only when not id_last. Only stopped policy, confirmed unloaded and restored gains allow writes. Preserve exact model and calibration joint ordering.
- `busStatus.native.read_order` exposes `mode`, nullable `last_id`, actual servo `ids`, and a generation/session discriminator. Publish stable readback after accepted changes. Legacy IMU first/last and full-Q4 dedicated transactions stay intact.
- Each native joint gains a `communication` object `{asked,ok,missed,streak,max_streak}` based only on actual sent requests and valid replies, independently of whole-cycle success. Missing/invalid packets are not invented per-ID checksum assignments.
- Studio `servo.read_order` bridges the native operation and applies the same order to its existing explicit debug-session owner; return actual readback. Persist safely if consistent with existing runtime config patterns; failures cannot claim saved/applied.
- Studio servo rows retain existing `comm` plus `health` window `{asked,ok,missed,rate,observed_s}` and `age_ms`, marking unavailable denominator as null. Snapshot-level `communication` exposes source/session/age and whole-bus counters/read_order.
- IMU status remains independent and source-aware. No rate derived from repeated web snapshots, no sensor ODR guessed from MCU sequence, no parameters reported current when cached/unknown.

## Task 1: Native telemetry evidence and ordering

Files: duck-control/src/feetech.rs, feetech_snapshot.rs and a focused helper module; robotd existing request/worker handling; duck-ipc-proto request enum; parameter/config code only where required by established persistence.

- [ ] Add failing tests for original default, ascending, ID13-last, invalid/duplicate/missing IDs, protocol boundaries and ID-correct reply mapping.
- [ ] Add failing tests for real sent-request denominators, partial good replies despite whole-cycle errors, unsent full-Q4/transport failures, and stat reset/session identity.
- [ ] Implement the telemetry-only permutation and counters; preserve target writes and model ordering.
- [ ] Implement guarded RPC, publish actual configuration and API/build install consequence, reject all enabled/unknown state changes.
- [ ] Run WSL `cargo +1.99.0 test --offline -p duck-control feetech -- --test-threads=1` and targeted robotd/IPC tests using explicit worktree git binding and isolated shared cache ownership.

## Task 2: Studio health mapping and bridge

Files: studio/communication_health.py (new), servo.py, imu.py, state.py, robotd.py, app.py and tests/test_communication_health.py/test_servo.py.

- [ ] Add failing fake-clock tests for finite 60s window, repeated snapshots, no denominator, counter rollback/source/session/order reset, stale/disconnected state, 15 independent rows and unknown checksum attribution.
- [ ] Map both native snapshots and current debug owner statistics, preserve independently available startup diagnostics, expose runtime/EEPROM parameter source and read age.
- [ ] Add read-order bridge tests: guarded unloaded operations, same policy across owners, persistence/readback failure, restore original order.
- [ ] Reuse current subscriber lifecycle, faster read-only snapshot polling only while needed, no serial imports in robotd.py/no extra UART reader.
- [ ] Run Python focused tests then existing servo/IMU/state regression tests.

## Task 3: Read-only UI and live details

Files: web/communication-health.js/.css (new), servo.js, imu.js, styles.css/index.html, browser tests and fake daemon fixtures as required.

- [ ] Add failing UI tests for no automatic servo.open on page entry, explicit debug entry, changing temperature/voltage/counters without reopening details, null/stale state and monitor recovery.
- [ ] Implement always-visible ID/name/angle/temp/voltage/recent rate/communication state. Expand source-labelled parameters and lifetime counters. Preserve editing functionality behind explicit existing owner acquisition.
- [ ] Add IMU source/rate/age/gyro/quaternion/error card and guarded read-order control with restore default and actual readback.
- [ ] Validate mobile and desktop in real browser using bounded fake data; capture screenshots and detect overflow.

## Task 4: Integration, review and local delivery

- [ ] Verify native and Python contracts agree, source/session resets work and meaningful UI fixtures cover default/main/debug/startup modes.
- [ ] Run focused native, Python and browser suites; broaden Studio regression once. Document any exact baseline-only failures rather than hiding them.
- [ ] Independent spec review first, then code-quality review. Fix all material issues and rerun affected tests.
- [ ] Commit isolated changes, write verification record and local operator comparison guide. Build ARM64 candidate if toolchain available, with exact source/hash provenance. No automatic flash/deploy/release or real motion.
