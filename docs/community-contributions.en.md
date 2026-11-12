# Community contributions and projects

[Home](../README.en.md) · [Documentation](README.en.md) · [简体中文](社区贡献.md)

## 🦆 Contributions from the flock

This repo is not one person's work. Merged code and bench-verified findings are credited here.

| Who | What they contributed |
|---|---|
| [@yoyojacky](https://github.com/yoyojacky) | **A Rust test tool for Feetech servos** ([PR #27](https://github.com/fanhao375/microduck-replica/pull/27), merged). A minimal STS/SCS protocol implementation of his own (framing, checksum, ping, register read/write, status parsing). The CLI scans the bus with a progress bar, reads status, moves a servo to a position, and runs single or batch tests that report position error, peak current, peak load, voltage and temperature, with a PASS/FAIL verdict — one command gives you a bench acceptance table → [`tools/sts3215Servo_testtool/`](../tools/sts3215Servo_testtool/) |
| [@Abo1ish](https://github.com/Abo1ish) | **IMU board firmware + attitude view in the debug console** ([PR #32](https://github.com/fanhao375/microduck-replica/pull/32), merged). A base STM32G031 firmware for the imu_to_dxl board that Pollen never open-sourced, verified on real hardware, with host-side tests and a [`VALIDATION.md`](../hardware/imu_to_dxl/firmware/VALIDATION.md) that is clear about its limits → [`hardware/imu_to_dxl/firmware/`](../hardware/imu_to_dxl/firmware/). He also streams the IMU attitude over J-Link into the web debug console, so the 3D duck's body turns with the real board (`--imu-jlink`, or `--imu-demo` with no hardware) → [`tools/servo-web/`](../tools/servo-web/) |
| [@yansis2026](https://github.com/yansis2026) | **Mobile console and offline voice control** ([PR #52](https://github.com/fanhao375/microduck-replica/pull/52), merged). The `/m` console provides a phone gamepad, model management, and a camera view. The phone sends microphone audio to the duck over HTTPS for onboard Vosk recognition, which works offline over the duck's hotspot. The contributor tested the workflow on their own duck. The merged revision passed software regression tests and awaits another hardware run. See the [mobile and offline guide](../software/studio/deploy/OFFLINE.md). |
| [@Liangjin5437](https://github.com/Liangjin5437) | **Radxa Cubie A7Z port and preview image** ([PR #54](https://github.com/fanhao375/microduck-replica/pull/54), merged). Adds hotspot provisioning, UART and grouped bus reads, Studio support, thermal management, and image build tools for Feetech HD-1910 servos and a bus IMU. The contributor tested the port on their own A7Z duck. The available preview image predates the merge fixes. The updated image still needs to be built and tested on hardware. See the [A7Z port and download guide](../radxa-cubie-a7z/README.md). |
| A member of the WeChat group | **Boot recipe for the Radxa Zero 3W V1.12J.** On this batch (WiFi changed to AIC8800DS2), swapping in Radxa's bootloader is not enough. He found by testing that the device tree has to come from Radxa too: **B1 bootloader + B1 DTB + Armbian 6.1.115 kernel + Trixie userland**, and got it booting → written up in the [pitfalls log](../踩坑记录.md#软件) and the [image guide](../tools/radxa/镜像使用说明.md) (both Chinese) |

### Projects from the flock

Some people build duck-related things in their own repos. The good ones get a shout-out here:

| Project | Author | What it does |
|---|---|---|
| [microduck-color-studio](https://github.com/LathamZ/microduck-color-studio) ([live demo](https://lathamz.github.io/microduck-color-studio/)) | [@LathamZ](https://github.com/LathamZ) | **A 3D color studio for the duck: settle the colors in your browser before you print.** Color each of the 70 parts, pick a material (PLA / matte / PETG / metallic / carbon fiber / TPU) and lighting to preview. Enter the filaments you already own and it recommends palettes from your stock (use what you have / add one color / acrylic accents). You can also import your own 3MF (e.g. the Feetech version) and export a **colored multi-plate 3MF** plus per-part STLs, with presets for Bambu P1S / A1 mini / H2D. Chinese and English UI, works on mobile |
| [MICDUCK_FTHD1901_REBUILD](https://github.com/fengj4780-sudo/MICDUCK_FTHD1901_REBUILD) | [@fengj4780-sudo](https://github.com/fengj4780-sudo) | **Feetech-build structural rework with optional CNC reinforcement.** Builds on our [editable SolidWorks drawings](https://github.com/fanhao375/microduck-replica-cad): adapted to the Feetech servo (written as HD1901 in that repo; the part files are HD-1910-C001), weak parts reinforced, assembly interferences fixed and some broken models repaired. **Five leg parts can be made as CNC parts** for better load paths (still being prototyped; the author estimates about ¥45 for all five including shipping); printing them instead also works and is stronger than the original. Includes a one-click 3MF (Bambu H2D, 0.4 mm, 5 plates) and the reworked SolidWorks drawings (Chinese) |
| [Microduck-kissqy](https://github.com/kissqy/Microduck-kissqy) | [@kissqy](https://github.com/kissqy) | **A reference for Feetech HD1910 training workflows and robot control.** Includes a training console, environment setup and job management guides, ONNX model export, a robot control console, firmware and demo videos. Recommended in the algorithms section for the practical training workflow |

Built something for the duck (a tool, tutorial, mod, policy…)? Open an issue with the link and we'll add it.

Want to join in: open an [issue](https://github.com/fanhao375/microduck-replica/issues) or send a PR.
Hardware, firmware, algorithms, documentation, measured data — all of it counts. **Measured data especially**: the rule in this repo is that results get recorded honestly, positive or negative.
