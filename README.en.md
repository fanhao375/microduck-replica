# Microduck Replica

**English** · [简体中文](README.md)

> A third-party reconstruction study of [Pollen Robotics' Microduck](https://pollen-robotics.com/microduck/).
> Assembly drawings, exploded views, CAD-importable assemblies, and a complete
> electronics teardown — all derived from publicly released files and source code.

Microduck is a 25 cm, 737 g bipedal robot duck driven by 15 Dynamixel XL330 servos
(14 under policy control) that learns to walk with reinforcement learning.

Its **software is open source (Apache-2.0)**. Its hardware is **partly** open:

- ✅ **The RPI Robot HAT board is fully published** — [`pollen-robotics/elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)
  (Apache-2.0): KiCad 9 project, Gerbers, BOM, pick-and-place and STEP. **You do not need to redraw this board.**
- ❌ **The `imu_to_dxl` board is not published** — no public project exists anywhere; the reconstruction
  in this repo is the only one available.
- ❌ **No editable mechanical CAD**, no whole-robot BOM, no assembly documentation. Pollen Robotics
  [told the press not to call it "open-source hardware" (for now)](docs/社区动态.md).

> 📌 **Correction (2026-09-03)**: this document previously stated "its hardware is not [open], no PCB
> schematics". **That was wrong** — it searched only `pollen-robotics/microduck` and missed the
> organisation's `elec_`-prefixed hardware repositories.

But two public artifacts turn out to be enough:

1. **`microduck_rl` ships the full MJCF model and 47 STL meshes.** The MJCF contains the
   complete kinematic tree — which part mounts to which, relative positions accurate to
   0.1 mm, joint axes, travel limits, masses and inertia tensors.
2. **The Rust runtime is open source, and a runtime that drives real hardware must
   hard-code device paths, I²C addresses, register offsets, baud rates and protocols.**
   The code *is* the datasheet.

This repository is what falls out of reading both.

> ✅ **Independently verified.** On 2026-08-31, [@tspy](https://x.com/tspy/status/2094249218735300630)
> published a hardware teardown on X (169 likes) that matches this repository's
> conclusions exactly — including the critical one: **the main board is a Radxa Zero 3W**.
> Two independent paths, one answer. See [Community Intelligence](docs/社区动态.md).

---

## Community

A WeChat group for people working on the same thing — build progress, pitfalls, sourcing.

<div align="center">
  <img src="assets/wechat-group.png" alt="Microduck replica WeChat group" width="280">
  <br>
  <sub><b>This QR code expires on 2026-09-10</b> — WeChat group codes are valid for 7 days<br>
  If it has expired, open an <a href="https://github.com/fanhao375/microduck-replica/issues">issue</a> and I will post a fresh one</sub>
</div>

## 🔨 Build Progress

**Someone is actually building this.** The head shell, trunk shell, leg structure and feet
are printed, and **the M2 screws go into the leg parts** — the conclusion in
[Fastener Reconstruction](docs/fastener-reconstruction.en.md) holds on physical hardware.

![First printed parts](build-log/photos/2026-09-02-首批打印件.jpg)

The rest of this repository is **analysis on paper**, recovered from the public MJCF and
source. The [Build Log](BUILD-LOG.en.md) records the hands-on side — print settings,
assembly problems, and whether the derived numbers hold up on real parts.

> This repository states repeatedly that simulation STLs are not manufacturing files.
> **The build log is the test of that claim.** The result gets recorded either way.

**→ [Build Log](BUILD-LOG.en.md)**

---

## Exploded Assembly View

![Exploded view](assembly-drawings/06_爆炸图_四分之三.png)

Seven drawings under `assembly-drawings/`:

| File | Contents |
|---|---|
| `01_正面` `02_侧面` `03_背面` `04_四分之三` | Front / side / rear / isometric, assembled, natural colors |
| **`05_爆炸图_侧面`** | 15 parts exploded along the kinematic chain, labeled with names and masses |
| **`06_爆炸图_四分之三`** | Isometric — shows the left/right leg mirroring clearly |
| `07_分色对照_装配态` | Assembled state in the same color coding, for cross-reference |

## Assembly Structure

```
Trunk 199 g
├─ L hip yaw→roll 23 g → L hip roll 6 g → L thigh 48 g → L shin 22 g → L ankle+foot 30 g
├─ Neck base 37 g → Neck pitch 6 g → Head yaw/roll 49 g → Head assembly + beak 189 g
└─ R hip yaw→roll 23 g → R hip roll 6 g → R thigh 48 g → R shin 22 g → R ankle+foot 30 g

Total 737.2 g   Envelope 144 × 141 × 264 mm
```

The trunk and the head weigh almost the same (199 g vs 189 g) — **the head is a quarter of
the whole robot and the center of mass sits high**, which explains why its walking policy
is hard to train.

## Joint Parameters

Five DoF per leg, four for neck and head — **14 under policy control**.
The robot actually carries **15 Dynamixel XL330**: the 15th drives the beak through a
passive linkage and never enters the action space.

| Joint | Travel |
|---|---|
| `hip_yaw` | −25° … +30° |
| `hip_roll` | ±22° |
| `hip_pitch` / `knee` / `ankle` / `head_pitch` | ±90° |
| `neck_pitch` | −90° … +60° |
| `head_yaw` | ±170° |
| `head_roll` | ±25° |

## CAD Assemblies

`cad/` holds STL files **with world transforms already applied** — import them and the
robot is assembled. (The 47 upstream STLs are each in their own part coordinate frame;
importing those directly piles every part at the origin.)

- `00_Microduck_整机装配体.stl` — whole robot, single file, 796,792 triangles
- `01` … `15` — the 15 rigid bodies, filenames are part names
- `零件对照表.json` — which upstream source meshes make up each body

Units are **millimeters**. Opens in FreeCAD, Fusion 360, SolidWorks, Blender, or any slicer.
No CAD installed? `tools/stl_viewer.html` is a zero-install WebGL viewer — open it in a
browser and drop an STL in.

---

## Electronics, Reverse-Engineered from the Runtime

**One 1 Mbps TTL serial bus does everything.**

```
                Radxa Zero 3W (RK3566) · Armbian
                  ├── UART2  1 Mbps TTL half-duplex ── 15× XL330 + imu_to_dxl (ID 200)
                  ├── I2C3   400 kHz (pins 3/5) ────── AIC3104@0x18 · ToF@0x29 · BMI088 (unused)
                  ├── I2S3   12.288 MHz ────────────── audio
                  ├── MIPI CSI ─────────────────────── IMX219 (I2C@0x10, mounted upside down)
                  ├── Bluetooth ────────────────────── gamepad / phone app
                  ├── Wi-Fi ────────────────────────── WebRTC
                  └── USB-C ────────────────────────── power + maskrom
```

| | |
|---|---|
| **Main board** | **Radxa Zero 3W** — an off-the-shelf module, *not* a custom carrier |
| SoC | RK3566, quad Cortex-A55, Mali-G52, 0.8 TOPS NPU, 1 GB RAM / 32 GB eMMC |
| **Servo bus** | **Single-wire half-duplex TTL** — *not* RS-232, *not* RS-485. Dynamixel Protocol V2 @ 1 Mbps on `/dev/ttyS2` |
| **Custom board 1** | **`imu_to_dxl` v2** — an LSM6DSV16X that speaks Dynamixel: bus ID 200, register 124, a 12-byte block read in the *same* `sync_read` as the servos |
| **Custom board 2** | **RPI Robot HAT** — TLV320AIC3104 @ 0x18, a dormant BMI088, a Stemma header for the ToF. **Published by Pollen** ([`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT)) |
| Battery | Sony NP-F550, 2S Li-ion. **No fuel gauge, no ADC** — pack voltage is read from what the servos report as their own supply |
| Sensors | LSM6DSV16X IMU · VL53L5CX/L8CX 8×8 ToF · IMX219 (Pi Camera v2) |

The `imu_to_dxl` design is the elegant part: the IMU is not on I²C. It presents itself as a
Dynamixel slave, so orientation arrives in the same bus transaction as the joint states —
no second bus, no host-side sensor fusion (the LSM6DSV16X's on-chip SFLP block emits a game
rotation quaternion and estimates its own gyro bias).

**Full detail:** [Hardware Teardown](docs/hardware-teardown.en.md) (English) ·
[Spec Sheet](docs/硬件规格速查.md) (Chinese)

---

## Can You Actually Build One?

| | Status |
|---|---|
| Part geometry | ✅ 47 STLs |
| Assembly relationships | ✅ 0.1 mm accurate, drawings produced |
| Joint axes / travel | ✅ all 14 |
| Mass / inertia | ✅ all 15 bodies |
| Servo model | ✅ Dynamixel XL330 × 15 |
| Bearings | ✅ Ø22×16×4 and Ø15×10×3 |
| Battery / sensors | ✅ NP-F550 2S, IMX219, VL53L8CX, LSM6DSV16X |
| Main board | ✅ **Radxa Zero 3W, off the shelf** |
| **HAT board** | ✅ **Officially published** — KiCad + Gerbers + BOM → [`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT) |
| **`imu_to_dxl` board** | ⚠️ Not published; must be redrawn. Protocol and register layout fully recovered |
| **Fastener list** | ✅ Reverse-engineered from hole features → M2 system |
| **Cable routing** | ❌ Nothing published |
| Control software | ⚠️ Rust runtime is Apache-2.0 and **runs as-is on the same main board** |

⚠️ **Simulation STLs are not manufacturing files.** Simulation only needs outer shape and
inertia — it guarantees nothing about fit tolerances, threads, heat-set insert bosses or
cable clearance. Printing these directly will most likely not assemble.

💰 **Building one costs more than buying one.** Dynamixel XL330-M288-T retails around
€45.76 in Europe; 15 of them is roughly €686 — already well past the $399 retail price of
the finished robot. $399 is a volume price an individual cannot reach.

## The Realistic Path

Skip 100% replication and go **"copy the mechanics, build your own electronics"**:

| | Approach |
|---|---|
| Mechanics | Use the STLs and drawings here — geometry copies exactly |
| Servos | XL330 × 15, off the shelf |
| Main board | **Radxa Zero 3W**, same as the original |
| IMU board | Roll your own `imu_to_dxl`: LSM6DSV16X + a small MCU + half-duplex transceiver. The protocol is fully documented here |
| HAT | **Order the official Gerbers** (4-layer); or skip it entirely if you don't need audio |
| Software | Same main board → the official Rust runtime runs unmodified |
| Policies | The nine shipped ONNX policies work; retrain with [microduck_rl](https://github.com/pollen-robotics/microduck_rl) if you change hardware |

## Three Pitfalls Worth Knowing

1. **Armbian runs a login console on UART2.** `serial-getty@ttyS2` holds the port —
   `systemctl mask` it. Pollen found this with `fuser -v /dev/ttyS2`.
2. **i2c3 collides with the FUSB302.** Using the hardware I²C on header pins 3/5 costs you
   USB-C PD negotiation (plain 5 V charging still works).
3. **The NPU ships disabled** in Armbian — flash the overlay and reboot to run RKNN models.

---

## Documentation

| Document | Contents |
|---|---|
| [**Hardware Spec Sheet**](docs/硬件规格速查.md) | One-page reference — block diagram, part numbers, bus parameters, build list, pitfalls |
| [**Hardware Teardown**](docs/hardware-teardown.en.md) 🇬🇧 | **Full derivation with evidence citations — the main board, both custom boards, bus protocol, sensors, power** |
| [**Actuator Selection**](docs/actuator-selection.en.md) 🇬🇧 | XL330 parameters, BAM M6 config, five calibrated PD sets, backlash modeling — plus **why closed-loop steppers do not work here, what swapping to a Feetech STS3215 actually costs** (737 g vs 2107 g, measured), and a **cross-comparison of same-class servos** including a deep assessment of the Unitree S288 |
| [**Fastener Reconstruction**](docs/fastener-reconstruction.en.md) 🇬🇧 | Hole-feature scan across 47 STLs → M2 system and purchase quantities |
| [Community Intelligence](docs/社区动态.md) | X / GitHub signals, independent verification, noise and scam warnings |
| [Progress](PROGRESS.md) | Status, decisions, open work |

> 🇬🇧 marks documents available in English. The rest are Chinese-only for now — their
> tables, part numbers, addresses and diagrams are readable without it, and machine
> translation handles the prose well.

## Reproducing This

```bash
# 1. Fetch upstream (not re-hosted here)
bash scripts/fetch_upstream.sh

# 2. Regenerate the drawings
python scripts/render_assembly.py upstream/microduck_rl assembly-drawings

# 3. Re-export the CAD assemblies
python scripts/export_assembly_stl.py upstream/microduck_rl cad

# 4. Re-scan hole features
python scripts/analyze_holes.py upstream/microduck_rl/src/mjlab_microduck/robot/microduck/assets
```

Requires `mujoco`, `numpy`, `pillow`, `scipy`. Rendering needs a working OpenGL context.

## License

- `scripts/` — Apache-2.0
- `assembly-drawings/`, `cad/` — **CC BY-SA-NC 4.0**. Upstream 3D models are CC BY-SA-NC;
  ShareAlike requires derivatives to carry the same license. **Non-commercial only.**

See [NOTICE.md](NOTICE.md). Not affiliated with or endorsed by Pollen Robotics.
