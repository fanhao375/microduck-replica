# Microduck Electronics, Reverse-Engineered

[简体中文](硬件方案逆向.md) · **English**

**Method:** the Rust runtime is open source, and a runtime that drives real hardware has
to hard-code device paths, I²C addresses, register offsets, baud rates and protocols.
**The code is the datasheet.** Everything below comes from the source, device trees and
config files in the `pollen-robotics/microduck` repository.

---

## 1. System Overview

```
                    ┌──────────────────────────────┐
                    │   Radxa Zero 3W (RK3566)     │  65 × 30 mm
                    │   Armbian / kernel 6.1.115   │  Pi Zero form factor
                    └──┬────────┬─────────┬────────┘
          /dev/ttyS2   │        │ 40-pin  │ MIPI CSI
          1 Mbps UART  │        │         │
        ┌──────────────┘        │         └── IMX219 camera (mounted upside down)
        │                       │
        │              ┌────────▼─────────────────┐
        │              │  Pollen RPI Robot HAT    │  65 × 30 mm, custom
        │              │  i2c3 @ 400 kHz          │
        │              │   ├ TLV320AIC3104  0x18  │  audio codec
        │              │   ├ BMI088   0x19/0x68   │  fitted but unused
        │              │   └ Stemma J5 → ToF 0x29 │
        │              │  battery feeds the robot │
        │              │  through this board      │
        │              └──────────────────────────┘
        │
   ═════╪═══════════ Dynamixel bus (Protocol V2, 1 Mbps) ═══════════
        │
        ├── 15 × Dynamixel XL330   IDs: L leg 20-24 / neck-head-mouth 30-34 / R leg 10-14
        └── imu_to_dxl v2 board    ID: 200      ← the second custom board
              └ LSM6DSV16X
```

**The central design idea: one Dynamixel bus does everything.** The IMU is not on I²C —
it is built as a Dynamixel slave sitting on the servo bus, and is read in the **same
`sync_read` transaction** as the servos. Pack voltage needs no fuel gauge either: the
firmware simply reads what the servos report as their own supply.

---

## 2. Main Board: Radxa Zero 3W

| Item | Value | Evidence |
|---|---|---|
| SoC | **RK3566** | `compatible = "radxa,zero-3w", "rockchip,rk3566"` |
| Form factor | Pi Zero, 65×30 mm, 40-pin header | matches the size of `pcb__raspberry_pi_zero_2_w.stl` |
| OS | **Armbian** (Debian-based), vendor kernel 6.1.115 | `armbianEnv.txt`, overlay comments |
| Servo serial port | **`/dev/ttyS2`** | `deploy/robotd.toml` |
| NPU | RK3566 NPU, **disabled by default in Armbian** | `deploy/overlays/rk3568-npu-enable.dts`; needs `setup-npu.sh` + reboot |
| Policy inference | ONNX Runtime ≥1.23 (installs 1.28.0), loaded with `dlopen` | `Cargo.toml` workspace metadata |
| NPU inference | rknn-toolkit2 runtime | same |

> 💡 **The single most important finding:** the main board is not a custom carrier.
> It is an **off-the-shelf Radxa Zero 3W**. An earlier version of this document claimed
> "the RK3566 carrier is custom and unobtainable" — that was wrong. It is a stock module.

---

## 3. Custom Board 1: `imu_to_dxl` v2

**The most elegant piece of the design, and the easiest one to reproduce.**

| Item | Value |
|---|---|
| IMU chip | **LSM6DSV16X** (ST 6-axis, with the SFLP on-chip sensor-fusion block) |
| Interface | **Dynamixel bus** — *not* I²C |
| Bus ID | **200** |
| Register address | **124** |
| Read by control loop | 12 bytes |
| Full diagnostic block | 20 bytes (also raw accelerometer, sample counter, status flags) |

### The 12-byte block

| Bytes | Contents |
|---|---|
| `0..6` | gyro x/y/z, `i16` little-endian raw counts, range **±500 dps**, **17.5 mdps/LSB** |
| `6..12` | SFLP quaternion x/y/z as **IEEE half-precision (fp16)**; `w = √(1 − x² − y² − z²)` |

### Why it is built this way

1. **No extra bus** — it comes back in the same `sync_read` as the 15 servos, at zero
   additional cost.
2. **No host-side fusion** — the LSM6DSV16X's SFLP block emits a game-rotation quaternion
   on-chip and estimates its own gyro bias.
3. **Only three quaternion components are sent** — a unit quaternion lets `w` be
   reconstructed, saving a byte pair so the whole thing fits in one transaction.

### What it takes to build one

- LSM6DSV16X (or a pin-compatible ST 6-axis part)
- A small MCU acting as a Dynamixel Protocol V2 slave (an STM32G0 or CH32V is plenty)
- A half-duplex TTL transceiver (the Dynamixel bus is single-wire half-duplex)
- Power taken from the bus

**This is the board most worth designing yourself in the whole project** — few components,
clear logic, and the protocol is fully documented above.

---

## 4. Custom Board 2: Pollen RPI Robot HAT

65 × 30 mm, Pi Zero form factor, plugs onto the Radxa's 40-pin header. (Corresponds to
`elec_rpi_robot_hat_pcb.stl`, measured 65.02 × 30.02 × 0.84 mm.)

### What is on it

| Part | I²C address | Notes |
|---|---|---|
| **TLV320AIC3104** | `0x18` | TI audio codec, I²S data + I²C control |
| **BMI088** | `0x19` / `0x68` | **Fitted but unused** — the device tree calls it "dormant", "unused but still connected" |
| **ToF** | `0x29` | Not on the board; attaches via the **Stemma J5** connector |

- I²C bus: header **pins 3 and 5** (`GPIO1_A0` = SDA, `GPIO1_A1` = SCL)
- Bus speed **400 kHz** — capped by the codec and the BMI088; the VL53L5CX alone would
  take 1 MHz
- Pull-ups: **a single 10 kΩ pair, R12/R13**
- Audio clocks: **12 MHz** fixed MCLK, I²S system clock **12.288 MHz** (256 × 48 kHz)
- **The robot's internal power runs through this board** (battery → HAT → main board)

### ⚠️ A trap they hit themselves, documented in the overlay

The RK3566's `i2c3` controller runs in its **M1 pin mux** in the vendor DTB
(`GPIO3_B5/B6`, next to the USB-C port), where it serves the **FUSB302 USB-C PD
controller**. The pins the HAT needs — 3 and 5 — are that same controller's **M0 pin mux**.

Their overlay re-muxes the controller to M0 and disables the fusb302 node. Consequences:

- **USB-C PD negotiation is lost**
- But the FUSB302's power-on defaults already present Rd on both CC lines
  (`SWITCHES0 = PDWN1|PDWN2`), so USB-C chargers — PD or dumb — **still deliver 5 V**,
  and the board only ever wanted 5 V
- In-robot power comes from the battery through the HAT regardless
- maskrom flashing is ROM code and is unaffected

> An earlier revision used **bit-banged I²C** (`i2c-gpio-pihat`), which burned CPU in
> `udelay()` busy-waits. Moving to the hardware controller is what made the
> **15 Hz ToF stream** affordable. The two modes are mutually exclusive; `install.sh`
> selects between them with `MICRODUCK_I2C_MODE=hw|bitbang`.

### Stable device node

`/dev/i2c-3` is symlinked to **`/dev/i2c-pihat`** by a udev rule, so consumer code works
unchanged in either mode.

---

## 5. The Servo Bus

| Item | Value |
|---|---|
| Motors | **Dynamixel XL330 × 15** |
| Protocol | **Dynamixel Protocol V2** |
| Baud rate | **1 Mbps** (EEPROM register `baud_rate = 3`) |
| Serial port | `/dev/ttyS2` |
| Rust crate | [`rustypot`](https://github.com/pollen-robotics/rustypot) — Pollen's own |
| Control loop | **50 Hz** |

### ID assignment

```
Left leg    20  21  22  23  24    hip_yaw / hip_roll / hip_pitch / knee / ankle
Neck+head   30  31  32  33  34    neck_pitch / head_pitch / head_yaw / head_roll / mouth
Right leg   10  11  12  13  14    (mirror of the left)
IMU board   200                   imu_to_dxl v2
```

> **The 15th servo is the mouth (ID 34, index 9).** Every alpha policy is
> `obs[1,61] → actions[1,14]`, and the action vector **skips that index** — the mouth is
> driven by higher-level logic, not by the policy.
>
> This ID scheme is **identical to Open Duck Mini v2** (20-24 / 30-33 / 10-14), with only
> the extra 34 added. The lineage shows.

---

## 5b. Communications, Item by Item

### Main bus: single-wire half-duplex TTL UART — **not RS-232, not RS-485**

| Item | Value | Evidence |
|---|---|---|
| Physical layer | **3-wire TTL half-duplex** (data + VDD + GND) | XL330 spec: 3-pin JST, TTL |
| Logic level | **3.3 V** | RK3566 IO level |
| Protocol | **Dynamixel Protocol V2** | `.with_protocol_v2()` |
| Rate | **1 Mbps** | `BAUD_RATE = 1_000_000`, EEPROM `baud_rate = 3` |
| Port | `/dev/ttyS2` = RK3566 **UART2, M0 pin mux** | `robotd.toml`, `uart2-m0` overlay |
| Devices on the bus | **16**: 15 servos + 1 IMU board | `ids = [200, 20..24, 30..34, 10..14]` |
| Per transaction | one `sync_read` covering all 16 devices, registers **124–136** | `bus.rs` |

> ❗ **It is not RS-485.** RS-485 is a differential pair, used by Dynamixel's XM/XH series
> (4-pin). The XL330 is **single-wire half-duplex TTL** (3-pin). Searching the whole
> repository for `rs485|rs232|half-duplex|direction pin` returns **zero hits** — there is
> no direction-control GPIO in the code, which means **direction switching is done in
> hardware by a self-steering circuit**, and that circuit lives on the HAT.

### ⚠️ The Armbian trap: UART2 has a login console on it by default

> From `robotd-design.md`: Armbian runs `serial-getty@ttyS2` by default, and `agetty` holds
> the port. You must `systemctl mask serial-getty@ttyS2`. Pollen found this with
> `fuser -v /dev/ttyS2` naming `agetty`.

### Every interface at a glance

| Interface | Used for | Parameters |
|---|---|---|
| **UART2 (`ttyS2`)** | servos + IMU board | 1 Mbps, TTL half-duplex |
| **I2C3 (M0, pins 3/5)** | audio codec / ToF / (the unused BMI088) | 400 kHz |
| **I2S3 (`i2s3_2ch`)** | audio data | MCLK 12 MHz, sysclk 12.288 MHz |
| **MIPI CSI** | IMX219 camera | I²C control at `0x10` |
| **Bluetooth** | gamepad (`padd`), phone app (`btd`) | on-module |
| **Wi-Fi** | WebRTC video, provisioning (`configd`) | on-module |
| **USB-C** | power + maskrom flashing | PD negotiation sacrificed by the overlay |
| **Unix sockets** | JSON-RPC between daemons | not hardware |

---

## 5c. Why This Needs a Linux SoC and Not an STM32 or ESP32

The main board has to do all of this at once:

| Task | Load |
|---|---|
| ONNX Runtime policy inference | `obs[1,61] → act[1,14]` @ **50 Hz** |
| WebRTC video | 720p30 H.264, **Rockchip MPP hardware encoder** |
| RKNN NPU object detection | `duck_detect.rknn` @ 2 Hz — a **thermal limit**, 95 °C flat out |
| GStreamer pipeline + congestion control | `rtpgccbwe` costs 7.6 % of a core |
| Bluetooth gamepad + phone app + Wi-Fi | BlueZ stack |
| Signed firmware update / rollback | `updaterd` |

**An MCU cannot do this.** WebRTC plus H.264 hardware encoding plus ONNX inference alone
requires Linux, a hardware encoder and an NPU.

Hence the choice: **RK3566, quad Cortex-A55 + Mali-G52 + NPU, running Armbian**.

> 📌 Note, though, that **the prototype ran on a Raspberry Pi Zero 2W**. From
> `robotd.toml`: the 50 Hz control loop *"is inherited from the prototype, where it was
> chosen on a Raspberry Pi Zero 2W. It has never been re-derived on the Radxa."*
> Open Duck Mini v2 still runs on a Pi Zero 2W today — because it does no vision and no
> WebRTC.

### So what MCU is on the `imu_to_dxl` board?

**Not in the repository** — that is the board's own firmware, which is not open source.
But its job defines the requirements:

- Act as a **Dynamixel Protocol V2 slave** (answer `sync_read`, ID 200, register 124)
- Read the **LSM6DSV16X** (SPI or I²C)
- Sustain **1 Mbps UART** with half-duplex direction switching
- Pack the SFLP quaternion into fp16

**An STM32G0, STM32G4, CH32V203 or ESP32 are all sufficient.** This is exactly the place
where you are free to choose — as long as it behaves like a Dynamixel slave on the wire,
the host does not care what is inside.

---

## 6. Power and Battery

| Item | Value |
|---|---|
| Battery | **Sony NP-F550 / NP-F970** (L-series camcorder pack), **2S Li-ion** |
| Nominal | 7.2 V |
| Full, under load | **8.2 V** |
| Empty, under load | **6.6 V** |
| Fuel gauge | **none** |
| ADC | **none** |

### How the voltage is measured

> From the source: *"There is no fuel gauge and no ADC. The only measurement available is
> what the servos report as their own supply."*

The pack voltage is read **over the Dynamixel protocol, from what the servos report as
their own supply** — the battery as seen through the bus. That means the reading **sags
under load and recovers at rest**, so the 6.6–8.2 V span is a **usable-under-load range**,
not the cells' chemical range.

When the battery EMA (about a 10-second time constant) reaches 6.6 V, the robot
**sits down gracefully and powers off**.

> Good news for anyone reproducing this: **you do not need any battery-monitoring circuit
> at all.**

---

## 7. Sensors

### ToF depth

| Item | Value |
|---|---|
| Chip | **VL53L5CX** or **VL53L8CX** (ST multizone ranger; two generations, one interface) |
| Address | `0x29` |
| Bus | the HAT's i2c3 |
| Frame rate | 15 Hz |

Used for the `theremin` feature: the distance of a hand in front of the beak maps to a
pitch, and the mouth opens with it. Playable band 0.10–0.70 m.

### Camera

| Item | Value |
|---|---|
| Sensor | **IMX219** (= Raspberry Pi Camera v2) |
| I²C address | `0x10` |
| Device-tree overlay | `radxa-zero3-rpi-camera-v2` |
| Mounting | **upside down**, needs `rotation = 180` |
| Sensor mode | pinned to 1920×1080@30 (its boot mode caps capture at 21 fps) |
| Default output | 720p30, 2 Mb/s |
| Encoding | GStreamer + **Rockchip MPP hardware encoder** → WebRTC |
| HFOV | ~62° |

### Audio

| Item | Value |
|---|---|
| Codec | TLV320AIC3104 |
| ALSA card name | `aic3104` |
| Microphone | on **Mic3R** (mono, right PGA); every other input is switched off |
| Output | line out, volume 9/9 |

---

## 8. Software Parameters You Must Match

| Parameter | Value | Notes |
|---|---|---|
| Control loop | **50 Hz** | inherited from the Pi Zero 2W prototype; **never re-derived on the Radxa** |
| Policy interface | `obs[1,61] → actions[1,14]` | checked at load; the older 51-D family is refused |
| `action_scale` | 0.9 (walk) / 0.8 (roller) | |
| Position P gain | **200** | ×0.8 while standing |
| Action low-pass | head **0.5** / legs **0.7** | **must match training**, or transfer degrades |
| Voltage adaptation | off by default | when on: `scale × (7.4 / measured EMA)`, clamped 6.0–9.5 V |

### Two engineering details worth stealing

**1. Predictive soft landing (`limp_fall`)**

It does not wait for the fall. Projected gravity rotates with the trunk, so its rate of
change is exactly `-(ω × g)` — and the gyro in that same 12-byte IMU block is used to
extrapolate **300 ms** ahead. When the robot is **already** tilted past about 26°,
**is still tipping rather than recovering**, and the extrapolation lands past the
threshold, the gain drops from 200 to **50** so the joints give way instead of fighting
the floor, and the robot **arrives limp**. Once it has gone still — judged from the gyro,
not a timer — the pose is ramped back to standing over 600 ms, and only then handed back
to the standing policy.

> The rationale in the source is worth quoting: the standing policy is *"a good
> stand-up-er and a bad faller"*. From a still robot, face down or face up, it gets back
> on its feet cleanly; out of a dynamic fall it tries and fails and tries again, at
> walking gain, against the floor — and the motors pay for every attempt.

**2. The 2 Hz detection rate is a thermal limit**

> From the source: *"2 is a thermal limit, not a preference."* Flat out it reaches
> **95 °C** and the CPU throttles to **408 MHz** — a robot that walks badly in order to
> see well.

---

## 9. What It Takes to Reproduce This Electronics Stack

### ✅ Buy off the shelf

| Part | Model | Notes |
|---|---|---|
| Main board | **Radxa Zero 3W** | stock module, **not custom** |
| Servos | Dynamixel XL330 × 15 | |
| Battery | Sony NP-F550 / F970 + holder | common camcorder pack, accessories everywhere |
| Camera | Raspberry Pi Camera v2 (IMX219) | |
| ToF | VL53L8CX module (Stemma/Qwiic) | |

### 🔧 Two boards to design yourself

**① `imu_to_dxl` — simple, and well worth doing**
LSM6DSV16X + a small MCU (Dynamixel V2 slave) + a half-duplex TTL transceiver.
The protocol and register layout are given in full above; implement to that.

**② RPI Robot HAT — can be simplified**
The original is TLV320AIC3104 + BMI088 (unused) + Stemma connector + power.
**Drop the BMI088**, and the whole board can be replaced with off-the-shelf modules:

- Audio: any I²S codec module (or a MAX98357A amplifier if you give up recording)
- ToF: a stock VL53L8CX module straight onto i2c3
- Power: a stock 5 V UBEC

> In other words: **if you do not need the microphone and speaker, you do not need to
> build the HAT at all.**

### ⚠️ Traps to plan for

1. **i2c3 collides with the FUSB302** — using the hardware I²C on pins 3/5 costs USB-C PD
   negotiation (plain 5 V charging still works). See section 4.
2. **The NPU ships disabled** — Armbian does not enable it; flash the overlay and reboot
   before running RKNN models.
3. **The camera is mounted upside down** — set `rotation=180` and let the hardware encoder
   do it; do *not* use `videoflip`, which is a full CPU pass over every frame.
4. **The 50 Hz loop was never validated on the Radxa** — Pollen say so themselves; the
   number came from the Pi Zero 2W prototype.

---

## 10. Conclusion: This Electronics Stack Can Be Reproduced

An earlier version of this document concluded "the RK3566 carrier is custom and
unobtainable, the PCB is a hard wall." **That conclusion was wrong:**

- The main board is an **off-the-shelf Radxa Zero 3W**, not a custom carrier
- Of the two custom boards, `imu_to_dxl` has very few components and its protocol is now
  fully recovered — it is straightforward to build
- The HAT can be replaced with stock modules, or skipped entirely if you do not need audio

**What actually remains is software.** The Rust runtime is open source (Apache-2.0) but
bound to the Radxa Zero 3W's specific wiring — so if you build around the same main board,
**the runtime runs as-is.**

> Sources: `duck-control/src/{model,imu,bus}.rs`, `robotd/src/main.rs`,
> `deploy/robotd.toml`, `deploy/audio/*.dts`, `deploy/overlays/*.dts`,
> `tof/src/*.rs`, `mediad/src/*.rs`, `docs/design/robotd-design.md`,
> `docs/project/media-bringup.md` — all in `pollen-robotics/microduck`.
