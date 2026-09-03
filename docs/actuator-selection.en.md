# Actuator Selection and Parameters

[简体中文](执行器选型.md) · **English**

## The Motor: Dynamixel XL330, 15 of Them

| | |
|---|---|
| Model | **Dynamixel XL330-M288-T** (evidence below) |
| Count | **15** — the `xl330` mesh is referenced 15 times in the full MJCF |
| Under policy control | **14**, in the action space: 5 left leg + 4 neck/head + 5 right leg |
| The 15th | Drives the beak/jaw through a `passive_*` linkage — **not in the action space** |
| Mounting | M2 screws (the servo body has 4× Ø2.0 clearance holes + 8× Ø1.6 tapping holes) |
| Supply | 7.4 V class nominal (domain-randomised over 6.5–8.2 V) |

## Joint Travel

| Joint | Travel | Radians |
|---|---|---|
| `hip_yaw` | −25° … +30° | −0.436 … 0.524 |
| `hip_roll` | ±22° | ±0.384 |
| `hip_pitch` / `knee` / `ankle` / `head_pitch` | ±90° | ±1.571 |
| `neck_pitch` | −90° … +60° | −1.571 … 1.047 |
| `head_yaw` | ±170° | ±2.967 |
| `head_roll` | ±25° | ±0.436 |

> The right leg's `hip_yaw` mirrors the left: −30° … +25°.

## The BAM M6 Actuator Config (what ships)

From `robot/microduck_constants.py`:

```python
_BAM_ACTUATOR_KWARGS = dict(
    motor_name="xl330",
    model="m6",
    target_names_expr=(r"^(?!passive_).*",),   # exclude passive joints
    kp_fw=200.0,             # firmware position-loop stiffness (microduck keeps 200, microban uses 125)
    vin_range=(6.5, 8.2),    # per-env battery voltage sampled at startup
    vin_drop_gain_range=(0.0, 0.2),   # load-dependent sag: V_drop = gain * sum(|tau|)
    vin_min=6.0,             # hard floor on the effective voltage after sag
    delay_min_lag=3,         # command delay, in control steps
    delay_max_lag=6,
)
```

BAM M6 is a **voltage-level** model. It is not a simple PD controller — it simulates the
whole chain from firmware PD → PWM → voltage → current → torque, and adds load-dependent
friction (Coulomb + Stribeck + load term) on top. **This is the core of the sim2real
recipe.**

### Three domain randomisations that matter

| Item | Range | What it models |
|---|---|---|
| `vin_range` | 6.5 – 8.2 V | the pack from full charge to flat |
| `vin_drop_gain_range` | 0.0 – 0.2 | **load-dependent sag** — bus voltage dropping when many servos pull at once |
| `delay_min/max_lag` | 3 – 6 steps | communication + firmware latency between command and execution |

`actuator/friction_dr_bam.py` adds a **per-env `friction_scale`** that multiplies only the
**velocity-independent** friction budget (stiction / gearbox), leaving the viscous term at
nominal — because stiction is where the dominant sim2real uncertainty lives.

### Backlash modelling

`BacklashEncoderBamActuator` reproduces a real detail that is easy to miss:

> On the real servo the magnetic encoder sits on the **output side** of the gear play, so
> the firmware position loop closes on `joint angle + backlash angle`. While the servo
> winds through the dead zone, **the measured position does not change, and neither does
> the PD error.**

The implementation feeds `cmd.pos` as `qpos[main] + qpos[backlash]`. Velocity `cmd.vel` is
deliberately left motor-side — it drives back-EMF and friction, which are rotor physics,
not an encoder-derived signal.

The backlash model lives in `robot_allcollisions_backlash.xml`: each servo gets a
`passive_<joint>_backlash` hinge in series.

## Five Calibrated Native-PD Parameter Sets (fallback)

`joints_properties.xml` keeps five sets, all results of BAM bench identification.
**If you use MuJoCo's built-in PD instead of BAM, copy these numbers directly:**

| class | damping | frictionloss | armature | kp | forcerange (N·m) |
|---|---|---|---|---|---|
| `chosen_actuator` ⭐ | 0.053 | 0.0048 | 0.0018 | 0.55 | ±0.96 |
| `chosen_actuator_antoine` | 0.044 | 0.013 | 0.0017 | 0.43 | ±0.75 |
| `chosen_actuator_old` | 0.048 | 0.006 | 0.002 | 0.52 | ±0.91 |
| `chosen_actuator_new` | 0.041 | 0.032 | 0.002 | 0.386 | ±0.67 |
| `perfect_actuator` | 0.15 | 0.1 | 0.001 | 10.0 | ±20.0 |

⭐ = the current default (commented "marc"). `ctrlrange` is ±10.0 throughout.
`perfect_actuator` is an idealised motor for comparison only — **do not train a policy on
it if that policy is going onto real hardware.**

Passive joints: `passive_wheel` / `passive_joint` — damping 0, frictionloss 0,
armature 0.0001.

## Why the XL330: It All Comes Down to the Mass Budget

The XL330 was not chosen because it is strong. It was chosen because **it fits an entire
drive train into 18 g**:

```
motor + 288:1 gearbox + encoder + driver + position loop + bus interface  =  18 g
```

The whole robot is 737 g; the 15 servos alone are 270 g of it (37 %). Nothing else fits in
that box.

### Where the torque comes from: the gearbox, not the motor

### How the model number was pinned down

Pollen have never named the sub-model publicly — the source carries only
`motor_name="xl330"`, **with no suffix**. One independent evidence chain narrows it to
**M288-T**:

1. `microduck_rl` simulates the servos with the **BAM** actuator model, configured as
   `motor_name="xl330"`, `model="m6"` (`microduck_constants.py`), which loads
   `bam/params/xl330/m6.json`
2. BAM is [`Rhoban/bam`](https://github.com/Rhoban/bam), whose README lists its library of
   **identified models**, and the entry for this servo reads
   **`Dynamixel XL330-M288-T`**
3. So the friction model the policies are trained against was **identified on a physical
   XL330-M288-T on a test bench**

Corroboration: on 2026-09-03 the Korean robotics observer
[@Allyakutaku](https://x.com/Allyakutaku/status/2095331280233926948) reported identifying the
motor as XL330-M288-T from official video footage.

> ⚠️ Strictly, this proves the servo **the simulation was calibrated against** is an M288-T.
> That the physical robot uses the same part is a very strong inference, but **not a
> statement from Pollen**.

The **288** in the model number is the gear ratio: **288.35:1** (the same family also has
the M077 at 77.9:1).

The coreless motor inside produces only a few **milli**newton-metres on its own; the
288:1 reduction multiplies that to the **±0.96 N·m** (~9.8 kg·cm) calibrated in the MJCF.

**The cost of that is written directly into the model:** the higher the ratio, the more
backlash — which is why `robot_allcollisions_backlash.xml` puts a `passive_*_backlash`
hinge in series with every joint, modelling **±1.0°** of gear play.

---

## Why Closed-Loop Steppers Do Not Work Here

It is a natural idea, and for **static positioning** (CNC, 3D printers) it is the right
one. For a legged robot there are several hard problems:

### 1. Mass — this alone settles it

| | Unit mass | What that includes |
|---|---|---|
| **XL330** | **18 g** | motor + gearbox + encoder + driver + control loop + bus, **all of it** |
| NEMA 8 stepper | ~60 g | **bare motor** — no encoder, no driver, no gearbox |

Fifteen bare NEMA 8s is 900 g — **the motors alone exceed the entire robot's mass (737 g)**,
before anything else is added.

### 2. Holding current

A stepper **must draw rated current to hold position**. A standing biped needs holding
torque at all 15 joints continuously, and the pack is 2600 mAh 2S with about an hour of
runtime.

A servo closes a position loop, so **current approaches zero when the error is small and
the load is light**. Standing still costs almost nothing.

There is a thermal consequence too: fifteen steppers pulling rated current inside a sealed
25 cm plastic body have nowhere to dump the heat.

### 3. ⭐ Backdrivability — the critical one, and the easiest to overlook

A legged robot **has to absorb impacts.** Look at `limp_fall` in the official
`robotd.toml`:

> When a fall is detected, the gain drops from 200 to **`gain_limp = 50`** so the joints
> "give way rather than fight the floor"; after a soft landing the pose is ramped back to
> standing over 600 ms.

**A stepper cannot do this.** It has two states — energised and locked, or unpowered and
free-spinning (and even then it has detent torque). **There is no adjustable impedance in
between.**

A closed-loop stepper corrects lost steps, but that is **recovery after the fact**, not
**compliance in the moment**. What a footfall impact needs is to yield, not a correction
transient.

The entire sim2real recipe — the BAM model, `kp_fw=200`, gain ramping, the soft landing —
rests on the premise that **the servo is a spring with adjustable stiffness**. A stepper is
not a spring. It is a position lock.

### 4. Wiring

15 servos = **one 3-wire daisy chain.**
15 steppers = 15 × 4 phase wires + 15 encoder cables + 15 driver boards.
Inside a 25 cm body, with a neck that has to rotate — it does not fit.

### 5. The part does not exist

18 g, a 288:1 gearbox, integrated encoder and driver, bus daisy chain — **nothing on the
market matches that.**

---

## What Swapping to a Feetech STS3215 Actually Costs

The STS3215 (Feetech, Shenzhen) sells for roughly **¥60–70** — about **a fifth** of the
XL330 (~¥299) — and it is **stronger** (1.86 N·m vs 0.96 N·m). It looks like the obvious
way to cut cost.

**But it is three times heavier (~55–60 g vs 18 g).** Here is what that does, measured.

### Hard data: same architecture, different servo, different robot

Both are 15-rigid-body bipedal robot ducks. Total mass from their MJCFs:

| | Servo | **Total mass** | Height |
|---|---|---|---|
| **Microduck** | XL330 | **737.2 g** | 25 cm |
| **Open Duck Mini v2** | STS3215 | **2107.1 g** | 42 cm |

**2.86×.**

> 💡 **Open Duck Mini v2 *is* the STS3215 answer.** Same engineer (Antoine Pirrone,
> Pollen R&D), same bipedal-duck architecture — swap the servo and the robot can only be
> built at 42 cm and 2.1 kg.

### Actuator parameters: every one differs by an order of magnitude

| Parameter | XL330 (`chosen_actuator`) | STS3215 (`sts3215`) | Ratio |
|---|---|---|---|
| **kp** | 0.55 | **17.8** | **32×** |
| **forcerange** | ±0.96 N·m | **±3.35 N·m** | **3.5×** |
| **damping** | 0.053 | **0.60** | **11×** |
| **frictionloss** | 0.0048 | **0.052** | **11×** |
| **armature** | 0.0018 | **0.028** | **15.5×** |
| modelled backlash | **±1.0°** | ±0.5° | — |

> Sources: `microduck_rl/.../joints_properties.xml` and
> `Open_Duck_Playground/playground/open_duck_mini_v2/xmls/joints_properties.xml`
>
> 📌 A counter-intuitive detail: **the XL330 is modelled with *more* backlash** (±1° vs
> ±0.5°). That may be a difference in how the two projects model it, or it may be the
> price of the 288:1 reduction.

**This is not a re-tune. These are two entirely different actuators.**

### Can you swap without changing the mechanics? No.

| Constraint | Why |
|---|---|
| **Size** | The XL330 measures **29 × 20 × 34 mm** (from `xl330.stl`). The servo cavities in the printed parts are cut to that; the STS3215 is a visibly larger class and does not fit |
| **Mounting** | Horn bolt circle, output-shaft spec and case mounting holes all differ — the M2 pattern this repository reverse-engineered (Ø2.2 clearance + Ø4.4 counterbore) is the XL330's |
| **Mass budget** | 15 × (57−18) ≈ **585 g extra**, before thickening any structure to carry it. Adding 585 g to a 737 g robot invalidates the centre of mass, the joint torque requirements and the structural design |

**This is not "swapping a servo." It is building a different robot.**

### What breaks / what survives

| Breaks | Survives |
|---|---|
| ❌ All nine official ONNX policies | ✅ **The 61-D observation contract and 14-D action space** |
| ❌ The BAM M6 calibration (identified on the XL330) | ✅ The training frameworks (microduck_rl / Playground) |
| ❌ The five `chosen_actuator` sets above | ✅ The sim2real methodology (BAM identification, domain randomisation, backlash modelling) |
| ❌ The backlash model (needs re-measuring) | |
| ❌ MJCF masses and inertias (all recomputed) | |
| ❌ The wire protocol (Feetech SCS/STS, not Dynamixel V2) | |

> **One piece of good news on the protocol:** Open Duck Mini uses
> `rustypot.feetech(port, 1000000)` — **the same `rustypot` crate, just a different
> protocol module**, at the same 1 Mbps. So replacing `Xl330Controller` in the Rust
> runtime is feasible without rewriting the communication layer.

**What changes is the robot model and the actuator model — not the algorithm.**

---

## Same-Class Servos: Why the Gap Is Still Open

"Is there a cheaper XL330?" is the most common question. The answer: **parts in the same
weight class do exist, but none of them satisfies torque and voltage at the same time.**

| Model | Mass | Size (mm) | Stall torque | Voltage | Encoder | Ratio |
|---|---|---|---|---|---|---|
| **Dynamixel XL330-M288** ⭐ | **18 g** | 29 × 20 × 34 | **0.96 N·m**<br>(9.8 kg·cm) | ~7.4 V | 12-bit | 288.35:1 |
| **Feetech STS3032** | 20 g | **23.2 × 12.1 × 28.5** | 0.44 N·m<br>(4.5 kg·cm @6V) | **4.8–6 V** | 12-bit | — |
| **Feetech SCS0009** | **11 g** | — | 0.23 N·m<br>(2.3 kg·cm) | 6 V | 10-bit | 1:416 metal |
| Feetech STS3215 | ~55–60 g | larger class | 1.86 N·m<br>(19 kg·cm) | 7.4 V | 12-bit | — |
| Dynamixel XL330-M077 | 18 g | same as M288 | low (high-speed variant) | ~7.4 V | 12-bit | 77.9:1 |
| **Unitree S288** ⭐ | **19.5 g** | **34 × 20 × 23** | **unclear (see below)** | **12.6 V** | **15-bit dual<br>+ output-side** | **288.35:1** |
| Unitree J288 | 35 g | 34 × 20 × 23 | unclear (metal-gear version) | 25.2 V | same as S288 | 288.35:1 |

> ⚠️ **The XL330 row is not on the same footing as the others.** Its 0.96 N·m is the
> `forcerange = ±0.96` from this repository's MJCF — a **MuJoCo simulation force limit** —
> whereas every other row is a **vendor stall-torque specification** (ROBOTIS only publish a
> stall figure for the XL330 at 5 V). **They are not the same quantity; compare with care.**
> Its dimensions were measured from `xl330.stl`. The rest are vendor specifications.
> **Prices are deliberately omitted** — they vary enormously by region.

### ⭐ Unitree S288: the only part that matches the XL330 across the board

A 2026 arrival. **Its mechanical parameters line up almost item for item** — right down to
the "288" in the model number:

| | XL330-M288 | Unitree S288 | |
|---|---|---|---|
| Mass | 18 g | **19.5 g** | ✅ |
| Size | 20 × 34 × 26 mm | **34 × 20 × 23 mm** | ✅ |
| **Gear ratio** | **288.35:1** | **288.35:1** | ✅ **identical** |
| Mounting screws | M2 | **M2** | ✅ |
| Bus physical layer | single-wire half-duplex TTL | **single-wire half-duplex TTL** | ✅ |
| Baud rate | 1 Mbps | **6 Mbps** | ⬆️ 6× |
| Motor | brushed coreless | **brushless, FOC** | ⬆️ |
| Encoder | 12-bit, rotor side | **15-bit dual + output-side** | ⬆️⬆️ |
| Control mode | position loop + kp | **torque feedforward + adjustable stiffness/damping** | ⬆️⬆️ |
| **Voltage** | ~7.4 V (2S) | **12.6 V (3S)** | ⚠️ **incompatible** |
| **Bus capacity** | 253 addresses | **15 (0–14; 15 is broadcast)** | ⚠️ |
| Torque | **0.96 N·m** | **unclear** | ⚠️ |

> Dimensions, connector, protocol and control law come from the official J288/S288 user
> manual (11 pages, read in full); mass, voltage and encoder resolution from the
> [product page](https://www.unitree.com/cn/DigitalServo/).

#### Two substantive upgrades

**1. Impedance control, not a position servo**

The manual gives the hybrid control law:

```
tau = tau_ff + k_p * (p_des - p) + k_d * (w_des - w)
```

**Torque feedforward plus adjustable stiffness and damping**, over an FOC inner loop.

Microduck's `limp_fall` buys compliance by dropping kp from 200 to 50; with an S288 you
would **command the torque directly** — cleaner, and exactly the joint-level force control
a legged robot actually wants.

**2. An output-side encoder — which eliminates the backlash problem outright**

The return packet carries `uint16_t OutPos : 13` (output-side sensor, 2^13 per revolution).

The XL330's encoder is on the **rotor side** and cannot see gear play — which is precisely
why `microduck_rl` has to add `passive_*_backlash` hinges and simulate the "firmware
position loop reads through the backlash" behaviour (see `BacklashEncoderBamActuator`
above).

**The S288 measures the true joint angle directly, and all of that complexity goes away.**

Its telemetry also includes case temperature, winding temperature, servo-side voltage,
rotor torque/speed/position, and a 22-bit fault code. Far richer than the XL330's.

#### Three hard constraints

**① Voltage: 12.6 V vs 7.4 V**
Microduck is 2S (NP-F550); the S288 wants **3S**. Battery, power distribution and the HAT
all have to be redesigned.

**② The bus holds only 15 servos**
The address space is **0–14** (15 is broadcast). Microduck has exactly 15 servos — **the
space is exactly full** — but its bus also carries the **IMU board at ID 200**.
**Under an S288 design the IMU must move to SPI or I²C.**

> This agrees with the recommendation in [Hardware Teardown](hardware-teardown.en.md):
> moving the IMU off the servo bus onto the host's SPI is better for reliability anyway.

**③ ⚠️ The torque specification is ambiguous — and unresolved**

The product page lists the S288 stall torque as "same direction **0.07 N·m** / reverse
**0.2 N·m**", but the manual states plainly:

> "All parameters in the protocol are **rotor-side** data. The reduction ratio is 288.35;
> to convert rotor-side data to output-side, multiply or divide by that ratio."

Neither reading works:

| Reading | Output torque | Verdict |
|---|---|---|
| Those figures are **rotor-side** | 20 / 58 N·m | ❌ impossible for a 19.5 g servo |
| Those figures are **output-side** | 0.2 N·m | ❌ one fifth of the XL330's — not a drop-in |

**This has to be confirmed with Unitree, or measured.** Four questions worth asking:

1. Is the quoted stall torque **rotor-side or output-side**?
2. What exactly do "same direction / reverse" mean — forward vs reverse rotation, or
   driving vs backdriving?
3. What are the **output-side** continuous and peak torques?
4. Is 12.6 V nominal or a maximum? Can it run at 7.4 V, and how much torque is lost?

> For extrapolation: the J288 is the 35 g metal-gear version, listed at 0.5 / 1.5 N·m. If
> 1.5 is output-side, that is plausible for 35 g of brushless at 288:1 and beats the
> XL330. Scaling down suggests the S288 might land near 0.5 N·m output-side — **but this
> is speculation and not a basis for a decision.**

#### Verdict: mechanically a drop-in, electrically and protocol-wise not

| | |
|---|---|
| ✅ **Mechanical** | Size, mass, gear ratio and M2 pattern all correspond; the printed parts may need little or no change |
| ⚠️ **Electrical** | 12.6 V vs 7.4 V — battery and power system must be redesigned |
| ❌ **Protocol** | Unitree's own 20-byte command / 26-byte reply with CRC32 at 6 Mbps, nothing like Dynamixel Protocol V2; `rustypot` does not support it |
| ❌ **Policies** | All nine official ONNX policies become invalid |
| ✅ **What you gain** | Joint force control, an output-side encoder, a 6 Mbps bus, rich thermal and fault telemetry |

**If the torque question resolves favourably, this is currently the only XL330 alternative
worth evaluating seriously.**

### STS3032: right size, wrong torque and voltage

The closest of the Feetech parts: **smaller than the XL330** (23.2 × 12.1 × 28.5 vs
29 × 20 × 34), nearly the same mass (20 g vs 18 g), the same 12-bit encoder class, the same
half-duplex bus architecture.

**Two problems:** torque is only **46 %** (0.44 vs 0.96 N·m), and the **voltage ceiling is
6 V** while Microduck is 2S at 7.4 V — connect it directly and it burns.

### Could you mix servo types?

| Joint group | Count | Torque demand | STS3032 enough? |
|---|---|---|---|
| Legs (hip yaw/roll/pitch, knee, ankle) | 10 | High — carries 737 g and absorbs landing impacts | ❌ **no** |
| Neck / head (pitch, yaw, roll) | 4 | Medium — drives a 189 g head assembly | ⚠️ possibly |
| Mouth | 1 | Low | ✅ yes |

**Mixing is theoretically possible; a full swap is not.** But mixing means solving the
7.4 V → 6 V step-down, and the two servo types have different BAM parameters, backlash and
response delay — training would need per-group actuator models.

### A telling data point: Pollen use Feetech themselves

**The SCS0009 is used in Pollen's own
[Amazing Hand](https://github.com/pollen-robotics/AmazingHand) and HopeJR.**

They are not unaware of the cheap option — **the legs of a biped genuinely need that
torque.** 0.23 N·m is enough for a finger joint and not enough for a knee.

### Where the gap is

```
Wanted:  18-20 g  .  >=0.9 N.m  .  7.4 V (2S)  .  bus daisy chain  .  integrated encoder + driver
Today:   Robotis XL330     meets all of it
         Unitree S288      meets mass/size/ratio/bus, but 12.6 V (3S); torque unconfirmed
         Feetech STS3032   meets mass/size, torque only 46 %, 6 V ceiling
```

**The gap is being squeezed — the Unitree S288 has closed most of it.** What still blocks
it is the voltage class (3S vs 2S) and the unresolved torque specification.

Feetech's line is heading the same way — **SCS0009 (11 g) → STS3032 (20 g) is a clear
"smaller and bus-connected" trajectory**; the housing exists, what is missing is ratio or
voltage class to lift the torque into the 0.9 N·m range.

Two market signals reinforce this: the **LeRobot ecosystem** has already made the STS3215 a
de facto standard for arms, and **Microduck passed $1 M in sales within seven hours of
launch**, proving real demand at the 18–20 g scale.

> ⚠️ **This is a read of the trend, not a prediction.** As of 2026-08-31 no vendor has
> announced such a part. But the gap is well defined, the housings exist, and the demand is
> demonstrated.

---

## Conclusion: There Is No Middle Path

| Route | Outcome |
|---|---|
| **Keep the XL330** | Use this repository's CAD directly, run the official policies as they are. Servos cost about **¥4,500** |
| **Switch to the STS3215** | Servo cost drops to about **¥1,000**, but every mechanical part is redesigned and every policy retrained — **which is, in effect, building [Open Duck Mini v2](https://github.com/apirrone/Open_Duck_Mini)** (fully open: BOM, Onshape CAD, 36 STLs, assembly guide, working walk policy) |
| **Switch to the Unitree S288** | Mechanically close, and brings joint force control plus an output-side encoder. Needs a 3S pack, a rewritten communication layer and retrained policies — **and the torque spec must be confirmed first** |
| **Wait** | The gap is closing. If a 2S variant appears, or the torque is confirmed, this repository's mechanical and electronics work carries straight over |

**Within this weight class, only Robotis currently offers enough torque.**

## A Note on Cost

The Dynamixel XL330-M288-T retails for about **€45.76** in Europe — roughly **€686** for
fifteen — and around ¥299 each domestically in China, about **¥4,500** for fifteen. Either
way that exceeds the **$399** retail price of the finished robot. $399 is a volume price an
individual cannot reach.
