# Microduck Replica

**English** · [简体中文](README.md)

A third-party full-robot replica project based on [Pollen Robotics' Microduck](https://pollen-robotics.com/microduck/), with resources for sourcing parts, printing, assembly, calibration, control, and training.

## Download the image for your board

Check the model printed on the board. **Radxa ZERO 3W** and **Radxa Cubie A7Z** use different images. Match the full board model to the release before flashing.

| Board | Image download | First use |
|---|---|---|
| **Radxa ZERO 3W** | [ZERO 3W · 20261112 RC2 test image](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-zero3w-20261112-rc2) · [Previous stable image](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-20261027) | [ZERO 3W flashing and setup](software/image/烧录和第一次使用.md) |
| **Radxa Cubie A7Z** | [Cubie A7Z preview image](https://github.com/Liangjin5437/microduck-replica/releases/tag/a7z-duck-image-20261109-preview1) | [A7Z flashing and setup](radxa-cubie-a7z/image/README.md) |

**The Feetech HD-1910 build has passed real hardware validation: standing, walking, a forward roll and standing back up.** Our main build uses **15 Feetech HD-1910 servos**. We provide a **ready-to-flash system image** and the **Microduck Studio web console** for Wi-Fi setup, calibration, and remote control from a phone or computer browser.

**The first-use workflow has also been completed on real hardware:** flash the image → hotspot and Wi-Fi setup → servo and IMU calibration → gamepad pairing and e-stop test → stand and walk. [Real hardware validation records](docs/update-history.en.md#latest)

## The ducks in action

**2026-10-24 · Motion validated on real hardware**: the two recordings below show a forward roll followed by standing back up, and walking on a tabletop.

<table>
<tr>
<th align="center">Forward roll and recovery</th>
<th align="center">Tabletop walking</th>
</tr>
<tr>
<td align="center" valign="middle"><a href="assets/demos/2026-10-24-roll-and-rise.mp4"><img src="assets/demos/2026-10-24-roll-and-rise.gif" alt="A real duck performs a forward roll and stands back up" width="260"></a></td>
<td align="center" valign="middle"><a href="assets/demos/2026-10-24-walking.mp4"><img src="assets/demos/2026-10-24-walking.gif" alt="A Feetech duck walks on a tabletop" width="400"></a></td>
</tr>
<tr>
<td align="center"><a href="assets/demos/2026-10-24-roll-and-rise.mp4">Full MP4 · about 8 seconds</a></td>
<td align="center"><a href="assets/demos/2026-10-24-walking.mp4">Full MP4 · about 6 seconds</a></td>
</tr>
</table>

The GIFs are silent previews; the MP4 files include the complete video and audio.

**[Download the image for your board](#download-the-image-for-your-board)** · **[First-use guide (PDF / web)](docs/上手指南/README.md)** · **[CAD and print files](https://github.com/fanhao375/microduck-replica-cad)** · **[Assembly tutorials](#-assembly-videos)** · **[BOM shopping list](BOM/README.md)** · **[Hardware projects](hardware/README.md)**

---

### Supported builds

- **Feetech HD-1910 + Radxa Zero 3W**: use this repository's system image and Studio web console.
- **Feetech HD-1910 + Radxa Cubie A7Z (preview)**: [port sources and notes](radxa-cubie-a7z/README.md) · [download the image for your board](#download-the-image-for-your-board). See the release for the preview's hardware validation status.
- **Dynamixel XL330**: matching CAD and assembly resources are available; use compatible runtime software.

[Choose print files and shopping list](#print-files-and-shopping-list) · [Detailed servo comparison](docs/actuator-selection.en.md)

---

## Community

A WeChat group for people working on the same thing — build progress, pitfalls, sourcing.

The code below is for **Duck Replica Group 15** (WeChat disables QR joins once a group reaches 200 members).

<div align="center">
  <img src="assets/wechat-group-15.png" alt="Duck Replica Group 15 WeChat QR code" width="280">
  <br>
  <sub><b>Duck Replica Group 15 · QR code valid before 2026-11-12</b> — WeChat group codes are valid for 7 days<br>
  If it has expired, open an <a href="https://github.com/fanhao375/microduck-replica/issues">issue</a> and I will post a fresh one</sub>
</div>

---

## 📺 Assembly videos

**[Bilibili series "MicroDuck 从零安装教程" (assembly from scratch)](https://space.bilibili.com/312747174/lists/9279084?type=season)** — recorded by **机械行者Robo**, who modelled the CAD and wrote the assembly BOM. It starts from the parts list and builds one subassembly per episode. Narration is in Chinese, but the hands-on footage is easy to follow. 6 of 8 episodes are out as of 2026-10-12:

The creator's accounts sit beside the episode list: **Bilibili, Xiaohongshu (RED), then Douyin**. Scan a code or click its image to open the account.

<table>
<thead>
<tr><th>Episode</th><th>Content</th><th nowrap>Length</th><th>Bilibili</th><th>Xiaohongshu</th><th>Douyin</th></tr>
</thead>
<tbody>
<tr>
<td nowrap><a href="https://www.bilibili.com/video/BV1RoHX68EFV/">1</a></td><td width="260"><a href="https://www.bilibili.com/video/BV1RoHX68EFV/">Parts and hardware list</a></td><td nowrap>1:32</td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://space.bilibili.com/312747174"><img src="assets/social/bilibili-qr.jpg" alt="Bilibili: 机械行者Robo" width="200"></a><br><sub><b>机械行者Robo</b><br>UID 312747174</sub></td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://www.xiaohongshu.com/user/profile/5ad950114eacab046016f038"><img src="assets/social/xiaohongshu-qr.jpg" alt="Xiaohongshu: 机械行者" width="200"></a><br><sub><b>机械行者</b><br>ID 270594280</sub></td>
<td rowspan="6" align="center" valign="middle" width="210"><a href="https://www.douyin.com/user/MS4wLjABAAAArLa_6yE3GE-AoRURoQnIHdbbI7yM-GHhzlgLZ-XbeYE"><img src="assets/social/douyin-qr.jpg" alt="Douyin: 机械行者Robo" width="200"></a><br><sub><b>机械行者Robo</b><br>ID 1852366168</sub></td>
</tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1Xppb6tECr/">2</a></td><td><a href="https://www.bilibili.com/video/BV1Xppb6tECr/">Lower legs</a></td><td nowrap>2:21</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1ycpb6yEwB/">3</a></td><td><a href="https://www.bilibili.com/video/BV1ycpb6yEwB/">Thighs</a></td><td nowrap>5:13</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1Kwpg6wE9G/">4</a></td><td><a href="https://www.bilibili.com/video/BV1Kwpg6wE9G/">Torso</a></td><td nowrap>2:29</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1EEpg6nEkD/">5</a></td><td><a href="https://www.bilibili.com/video/BV1EEpg6nEkD/">Neck</a></td><td nowrap>0:40</td></tr>
<tr><td nowrap><a href="https://www.bilibili.com/video/BV1aKpE6hEzj/">6</a></td><td><a href="https://www.bilibili.com/video/BV1aKpE6hEzj/">Head</a></td><td nowrap>5:50</td></tr>
</tbody>
</table>

### Print files and shopping list

Choose print resources for your servo version, then prepare the matching purchased parts.

| Resource | Servo version | Where to find it |
|---|---|---|
| **Print files and CAD** | **Feetech HD-1910** | [Feetech section](https://github.com/fanhao375/microduck-replica-cad#飞特-hd-1910-打印资料): print project, CAD sources and update notes |
| **Print files and CAD** | **Dynamixel XL330** | [XL330 section](https://github.com/fanhao375/microduck-replica-cad#dynamixel-xl330-打印资料): matching CAD sources and printing instructions |
| **Assembly shopping list** | **Feetech HD-1910** | **[Online preview](BOM/README.md)** · **[Download Excel](BOM/装配BOM-机械行者Robo-2026-10-23-带采购链接.xlsx)**: compiled by 机械行者Robo, dated 2026-10-23, with quantities, materials and purchase links |

Mating parts from the two servo versions are not interchangeable. File versions and update dates are maintained in the respective CAD sections. This shopping list follows the Feetech build; XL330 servos and associated electronics need separate checks.

---

## Where things stand

As of **2026-10-25**, **the Feetech replica, first-use workflow and robot motion have been validated on real hardware**. The sections below cover the completed work and further extensions. See [update history](docs/update-history.en.md) for the test scope and development history.

---

## 💻 Software · system image and Microduck Studio

**Replica image 20261027: web software updates and local Mandarin posture commands.** Settings can check, download or upload signed software packages, with rollback on failed installation. Tap R3 to toggle listening for “sit down” and “stand up”; the Mandarin model is included and needs no cloud account. Web motion recording/playback, lively mode and model management are retained. Existing cards need one SSH bootstrap; routine software updates then use the browser without reflashing.

The robot runtime, gait models and bus-ID-200 IMU autodetection are unchanged from 20261023c. Software, signed transactions, browser flows and read-only image checks passed; physical first boot, existing-card upgrades/rollback and live acoustics still need acceptance. Music-driven head/body motion remains unresolved, and spoken AI dialogue is not connected. [Image and checksums](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-20261027) · [Software package](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.10.27) · [Update and voice guide](docs/软件更新与语音使用说明-20261027.md) · [Verification scope](docs/superpowers/verification/2026-10-27-web-updates.md)

<p align="center"><a href="software/studio/README.md"><img src="assets/studio-overview-20261025-demo.jpg" alt="Current Microduck Studio interface with simulated device readings" width="920"></a><br><sub>Studio interface demonstration with simulated device readings</sub></p>

**[Download the image for your board](#download-the-image-for-your-board)** · **[First-use guide](docs/上手指南/README.md)** · **[Studio documentation](software/studio/README.md)**

The image is free for personal non-commercial builds; see the release for component licences. Camera input defaults to a test source; see the release notes for physical camera and ToF verification limits.

---

## 🔧 Mechanics · one-stop printing and assembly

**机械行者Robo maintains separate Feetech HD-1910 and Dynamixel XL330 resources.** Models, print projects and update notes live in the CAD repository; choose the files matching your servos.

| What you need | Where to find it |
|---|---|
| **One-stop printing** | **[Bambu MakerWorld (Feetech)](https://makerworld.com.cn/zh/models/2963569-microduck#profileId-3478428)** · [Bambu Studio 3MF project](https://github.com/fanhao375/microduck-replica-cad#打印) |
| **Models and print resources** | **[Feetech HD-1910 section](https://github.com/fanhao375/microduck-replica-cad#飞特-hd-1910-打印资料)** · **[XL330 section](https://github.com/fanhao375/microduck-replica-cad#dynamixel-xl330-打印资料)** |
| **Assembly tutorials** | **[Assembly manual PDF](https://github.com/fanhao375/microduck-replica-cad/blob/master/安装说明书/microduck装配安装说明书.pdf)** · **[Episode videos](#-assembly-videos)**; use the matching structural parts for each build |
| **Shopping list** | **[Online BOM preview](BOM/README.md)** · **[Download Excel](BOM/装配BOM-机械行者Robo-2026-10-23-带采购链接.xlsx)**: 2026-10-23 Feetech list; XL330 servos and associated electronics need separate checks |

[CAD repository releases](https://github.com/fanhao375/microduck-replica-cad/releases) · [Build log](BUILD-LOG.en.md)

---

## 🔌 Electronics · five circuit boards

This repository provides projects for the bus IMU board, servo bus hub and battery terminal board. The main board is a **Radxa ZERO 3W**, paired with Pollen Robotics' open-source **RPI Robot HAT**.

<table>
<tr>
<th>Bus IMU board</th>
<th>Servo bus hub</th>
<th>Battery terminal board</th>
<th>Main board<br>Radxa ZERO 3W</th>
<th>Official HAT<br>RPI Robot HAT</th>
</tr>
<tr>
<td align="center" valign="middle" width="170"><a href="assets/hw/imu-to-dxl-1v0a-3d.png"><img src="assets/hw/imu-to-dxl-1v0a-3d.png" alt="Bus IMU board 3D design preview" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/wire-hub-1v0a-3d.png"><img src="assets/hw/wire-hub-1v0a-3d.png" alt="Servo bus hub 3D design preview" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/pogo-pin-1v0a-3d.png"><img src="assets/hw/pogo-pin-1v0a-3d.png" alt="Battery terminal board 3D design preview" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/radxa-zero3w-official.webp"><img src="assets/hw/radxa-zero3w-official.webp" alt="Official Radxa ZERO 3W board overview" width="160"></a></td>
<td align="center" valign="middle" width="170"><a href="assets/hw/rpi-robot-hat-official.png"><img src="assets/hw/rpi-robot-hat-official.png" alt="Official Pollen Robotics RPI Robot HAT rendering" width="160"></a></td>
</tr>
<tr>
<td align="center" valign="top"><a href="assets/hw/imu-to-dxl-1v0a-pcb.png">PCB preview</a><br><a href="hardware/新版/imu_to_dxl_1V0A/">Project files</a></td>
<td align="center" valign="top"><a href="assets/hw/wire-hub-1v0a-pcb.png">PCB preview</a><br><a href="hardware/新版/wire_hub_1V0A/">Project files</a></td>
<td align="center" valign="top"><a href="assets/hw/pogo-pin-1v0a-pcb.png">PCB preview</a><br><a href="hardware/新版/POGO_PIN_1V0A/">Project files</a></td>
<td align="center" valign="top"><a href="https://item.taobao.com/item.htm?id=763812025816">Buy on Taobao</a><br><a href="https://docs.radxa.com/en/zero/zero3">Documentation</a> · <a href="https://docs.radxa.com/en/zero/zero3/download#hardware-design">Design files</a></td>
<td align="center" valign="top"><a href="https://github.com/pollen-robotics/elec_RPI_Robot_HAT">KiCad sources</a><br><a href="https://github.com/pollen-robotics/elec_RPI_Robot_HAT/tree/main/production">Production files</a></td>
</tr>
</table>

The first three images are custom-board design previews; the Radxa and HAT images come from official resources. Click an image for full size; see [image credits](assets/hw/official-board-images.md). Radxa provides schematics, 2D dimensions and 3D models. See the custom projects for EasyEDA Pro files, STEP models and physical validation status.

[All hardware projects](hardware/README.md) · [Three-board notes](hardware/新版/README.md) · [Bus IMU firmware](hardware/imu_to_dxl/README.md)

---

## 🧠 Actuator modelling · Feetech's official BAM

[**Feetech's official HD-1910 BAM**](https://gitee.com/ftservo/ftservo-bam) provides actuator models and identified parameters as a reference for simulation training. This repository includes an open-source [training project](software/training/README.md) and [web training workbench](software/training-studio/README.md); see the [parameter notes](docs/HD-1910-M6参数来源.md) for the model configuration and parameter sources used here.

---

## 🖥️ Open-source training workbench · Microduck Training Studio

We also built **Microduck Training Studio**: create training experiments on a computer, view curves, pause and resume, evaluate checkpoints and export ONNX models through a browser. It runs separately from Studio on the duck.

<p align="center"><a href="software/training-studio/README.md"><img src="assets/training-studio-20261025-demo.jpg" alt="Actual Microduck Training Studio interface showing a 3D model, training controls and synthetic demo metrics" width="920"></a><br><sub>Training workbench in demo mode: the 3D view is a model preview and the curves use synthetic data</sub></p>

The real-GPU short-run, evaluation-video and model-export pipeline has been verified. Long training and physical validation of our own trained policies remain pending.

**[Training workbench and usage](software/training-studio/README.md)** · **[Action Lab guide](software/training-studio/web/custom-actions.md)** · **[Training engine](software/training/README.md)** · [Verification record](software/training-studio/docs/verification.md)

---

## 🧰 Tools · flashing, debugging and references

The [tool index](tools/README.en.md) groups utilities by the task you want to perform.

| Task | Tools and links |
|---|---|
| Flash an image | **[Rufus portable EXE](tools/rufus/rufus-4.15p.exe)** · [Flashing guide](tools/rufus/README.md) |
| Serial and servo debugging | **[Feetech FD and serial utilities](tools/飞特/README.md)** · [3D servo web console](tools/servo-web/README.md) |
| Read Feetech references or use an SDK | **[Official manuals, model specifications and SDKs](docs/飞特资料/README.md)**, with official version checks and separate offline snapshot dates |
| Test servos and investigate motion | [HD1910M tester](tools/FeeTech_HD1910M_Servo/README.md) · [STS3215 tester](tools/sts3215Servo_testtool/README.md) · [Gait analysis](tools/gait-analysis/README.md) |

---

## 🦆 Contributions from the flock

This repo is not one person's work. Merged code and bench-verified findings are credited here.

| Who | What they contributed |
|---|---|
| [@yoyojacky](https://github.com/yoyojacky) | **A Rust test tool for Feetech servos** ([PR #27](https://github.com/fanhao375/microduck-replica/pull/27), merged). A minimal STS/SCS protocol implementation of his own (framing, checksum, ping, register read/write, status parsing). The CLI scans the bus with a progress bar, reads status, moves a servo to a position, and runs single or batch tests that report position error, peak current, peak load, voltage and temperature, with a PASS/FAIL verdict — one command gives you a bench acceptance table → [`tools/sts3215Servo_testtool/`](tools/sts3215Servo_testtool/) |
| [@Abo1ish](https://github.com/Abo1ish) | **IMU board firmware + attitude view in the debug console** ([PR #32](https://github.com/fanhao375/microduck-replica/pull/32), merged). A base STM32G031 firmware for the imu_to_dxl board that Pollen never open-sourced, verified on real hardware, with host-side tests and a [`VALIDATION.md`](hardware/imu_to_dxl/firmware/VALIDATION.md) that is clear about its limits → [`hardware/imu_to_dxl/firmware/`](hardware/imu_to_dxl/firmware/). He also streams the IMU attitude over J-Link into the web debug console, so the 3D duck's body turns with the real board (`--imu-jlink`, or `--imu-demo` with no hardware) → [`tools/servo-web/`](tools/servo-web/) |
| [@yansis2026](https://github.com/yansis2026) | **Mobile console and offline voice control** ([PR #52](https://github.com/fanhao375/microduck-replica/pull/52), merged). The `/m` console provides a phone gamepad, model management, and a camera view. The phone sends microphone audio to the duck over HTTPS for onboard Vosk recognition, which works offline over the duck's hotspot. The contributor tested the workflow on their own duck. The merged revision passed software regression tests and awaits another hardware run. See the [mobile and offline guide](software/studio/deploy/OFFLINE.md). |
| [@Liangjin5437](https://github.com/Liangjin5437) | **Radxa Cubie A7Z port and preview image** ([PR #54](https://github.com/fanhao375/microduck-replica/pull/54), merged). Adds hotspot provisioning, UART and grouped bus reads, Studio support, thermal management, and image build tools for Feetech HD-1910 servos and a bus IMU. The contributor tested the port on their own A7Z duck. The available preview image predates the merge fixes. The updated image still needs to be built and tested on hardware. See the [A7Z port and download guide](radxa-cubie-a7z/README.md). |
| A member of the WeChat group | **Boot recipe for the Radxa Zero 3W V1.12J.** On this batch (WiFi changed to AIC8800DS2), swapping in Radxa's bootloader is not enough. He found by testing that the device tree has to come from Radxa too: **B1 bootloader + B1 DTB + Armbian 6.1.115 kernel + Trixie userland**, and got it booting → written up in the [pitfalls log](踩坑记录.md#软件) and the [image guide](tools/radxa/镜像使用说明.md) (both Chinese) |

---

## Projects from the flock

Some people build duck-related things in their own repos. The good ones get a shout-out here:

| Project | Author | What it does |
|---|---|---|
| [microduck-color-studio](https://github.com/LathamZ/microduck-color-studio) ([live demo](https://lathamz.github.io/microduck-color-studio/)) | [@LathamZ](https://github.com/LathamZ) | **A 3D color studio for the duck: settle the colors in your browser before you print.** Color each of the 70 parts, pick a material (PLA / matte / PETG / metallic / carbon fiber / TPU) and lighting to preview. Enter the filaments you already own and it recommends palettes from your stock (use what you have / add one color / acrylic accents). You can also import your own 3MF (e.g. the Feetech version) and export a **colored multi-plate 3MF** plus per-part STLs, with presets for Bambu P1S / A1 mini / H2D. Chinese and English UI, works on mobile |
| [MICDUCK_FTHD1901_REBUILD](https://github.com/fengj4780-sudo/MICDUCK_FTHD1901_REBUILD) | [@fengj4780-sudo](https://github.com/fengj4780-sudo) | **Feetech-build structural rework with optional CNC reinforcement.** Builds on our [editable SolidWorks drawings](https://github.com/fanhao375/microduck-replica-cad): adapted to the Feetech servo (written as HD1901 in that repo; the part files are HD-1910-C001), weak parts reinforced, assembly interferences fixed and some broken models repaired. **Five leg parts can be made as CNC parts** for better load paths (still being prototyped; the author estimates about ¥45 for all five including shipping); printing them instead also works and is stronger than the original. Includes a one-click 3MF (Bambu H2D, 0.4 mm, 5 plates) and the reworked SolidWorks drawings (Chinese) |
| [Microduck-kissqy](https://github.com/kissqy/Microduck-kissqy) | [@kissqy](https://github.com/kissqy) | **A reference for Feetech HD1910 training workflows and robot control.** Includes a training console, environment setup and job management guides, ONNX model export, a robot control console, firmware and demo videos. Recommended in the algorithms section for the practical training workflow |

Built something for the duck (a tool, tutorial, mod, policy…)? Open an issue with the link and we'll add it.

Want to join in: open an [issue](https://github.com/fanhao375/microduck-replica/issues) or send a PR.
Hardware, firmware, algorithms, documentation, measured data — all of it counts. **Measured data especially**: the rule in this repo is that results get recorded honestly, positive or negative.

---

## Documentation

Paths are relative to this repository's root. Mechanical CAD is maintained in a separate repository.

| Project | Repository location | Description and links |
|---|---|---|
| First use and assembly | [CAD repository](https://github.com/fanhao375/microduck-replica-cad) · [BOM/](BOM/README.md) | [First-use guide](docs/上手指南/README.md) · [Shopping list](BOM/README.md) · [Assembly videos](#-assembly-videos) |
| Software and image | [software/](software/README.md) | [Studio](software/studio/README.md) · [Image build](software/image/README.md) |
| Training workbench and project | [software/training-studio/](software/training-studio/README.md)<br>[software/training/](software/training/README.md) | Browser workbench, simulation environment and model export |
| Electronics and firmware | [hardware/](hardware/README.md) | [Board projects](hardware/README.md) · [IMU firmware](hardware/imu_to_dxl/README.md) |
| Debug and analysis tools | [tools/](tools/README.en.md) | [Tool index](tools/README.en.md) · Flashing, serial debugging, Feetech references and analysis |
| Documentation and records | [docs/](docs/README.en.md) | [All documentation](docs/README.en.md) · [Pitfalls](踩坑记录.md) · [Debug log](调试记录.md) · [Build log](BUILD-LOG.en.md) |
| History and community | [docs/](docs/README.en.md#research-and-archives) | [Upstream updates (Chinese)](docs/官方更新跟踪.md) · [Update history](docs/update-history.en.md) · [Community contributions](docs/community-contributions.en.md) |

---

## Licence and sources

Code, simulation assets and bundled models have separate licences. See [licensing and attribution](NOTICE.md) and the [model terms](software/image/overlay/usr/share/doc/microduck/模型说明.txt); **the replica image is for personal non-commercial builds and must not be distributed with paid products**.

This third-party project is not affiliated with or endorsed by Pollen Robotics.

### ❤️ Support the author

This project has grown through hands-on assembly, debugging, software development and documentation. If the resources and tools have helped you, a Star ⭐, bug report or build story is always welcome. You can also leave a voluntary tip to buy the author a coffee and support continued maintenance. Thank you to everyone helping the duck along.

<p align="center"><a href="assets/wechat-support.jpg"><img src="assets/wechat-support.jpg" alt="WeChat tip QR code; click to view the original image" width="240"></a><br><sub>Optional support via WeChat · Click the image for full size</sub></p>
