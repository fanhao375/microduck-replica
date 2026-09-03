# Bill of Materials

[简体中文](BOM.md) · **English**

Everything needed to build one Microduck. Quantities come from geom references in upstream
`robot_walk.xml` (**38 mesh types / 75 instances**) — counted, not estimated.

> ⚠️ **Pollen have never published a BOM.** This list is reconstructed from the public MJCF, STLs,
> Rust source and KiCad project. Every entry cites its basis. Prices vary enormously by region and
> date — treat them as **order of magnitude only**.
>
> Checked: **2026-09-04**. Upstream baseline: `pollen-robotics/microduck_rl` @ **`29e887e`**
> (includes the full CAD re-export `8dfc08f` of 2026-09-01).
> The geom counts in `robot_walk.xml` are **identical before and after** that re-export
> (38 types / 75 instances), so the quantities here are unaffected.

---

## 1. Cost at a glance

| Category | Qty | Order of magnitude |
|---|---|---|
| **Servos** | 15 | **$359 – €629** (huge regional spread, see below) |
| Compute and sensors | 4 items | ~$80 – 120 |
| Battery and power | 2 items | ~$30 – 50 |
| Bearings | 14 | ~$15 – 30 |
| Fasteners | ~325 pieces | ~$15 – 25 |
| **PCB fabrication (2 boards)** | 2 | ~$60 – 150 with assembly |
| Filament | — | ~$15 – 30 |

**The servos dominate**, and the channel spread is enormous: ROBOTIS international at $23.90 ×15 is
**$359** (below the robot's retail price), ROBOTIS US at $27.49 is **$412** (about equal to it), and
European inc-VAT retail runs **€603–629** (far above).

> $399 is Seeed-scale manufacturing with Pollen's supply chain. **An individual build cannot reach it.**

---

## 2. Servos (the cost centre)

| Part | Qty | Basis |
|---|---|---|
| **Dynamixel XL330-M288-T** | **15** | the `xl330` mesh is referenced 15× in `robot_walk.xml` |

- 14 are in the policy action space; the **15th drives the beak/jaw** through `passive_*` linkages
- IDs: left leg 20–24 / neck-head-mouth 30–34 / right leg 10–14
- ⚠️ **The model number is inferred.** The source only carries `motor_name="xl330"` with no suffix.
  Evidence for M288-T: [actuator selection](docs/actuator-selection.en.md#how-the-model-number-was-pinned-down)

**Prices** (2026-09-04):

| Source | Each | ×15 |
|---|---|---|
| Robotis US | **$27.49** | **$412** |
| Generation Robots (EU, ex-VAT) | €33.50 | €503 |
| Generation Robots (EU, inc-VAT) | €40.20 | €603 |
| MyBotShop | €41.95 | €629 |

> ⚠️ **Over-voltage warning**: the XL330 is rated 3.7–6.0 V, and Microduck feeds it 6.6–8.2 V.
> This is Pollen's deliberate choice, not a typo. See
> [the voltage truth](docs/actuator-selection.en.md#-the-voltage-truth-the-xl330-is-run-over-voltage).

---

## 3. Electronics

| Part | Model | Qty | Basis / note |
|---|---|---|---|
| Compute | **Radxa Zero 3W** | 1 | device tree `compatible = "radxa,zero-3w"`. ⚠️ Several RAM/eMMC SKUs — **the official one is 1 GB / 32 GB**; the OS image must carry the Rockchip vendor kernel (Armbian family) or the NPU does not exist |
| Camera | Raspberry Pi Camera v2 (**IMX219**) | 1 | `setup-board.sh` applies the `radxa-zero3-rpi-camera-v2` overlay. ⚠️ **Mounted off-axis; corrected in software.** `media-bringup.md` says the alpha was upside down (`180`), but the current code default in `mediad/src/main.rs` is `--rotate 90`. Check against your build |
| Depth | **VL53L8CX** or VL53L5CX module | 1 | firmware supports both, identified by revision ID; address `0x29` or `0x52`; Stemma/Qwiic |
| Battery | **Sony NP-F550** (2S, 7.4 V) | 1 | ⚠️ see correction below |
| Battery holder | Any NP-F series holder | 1 | Upstream only has the printed `power_support` — **no contact model at all**; you must solve the pickup yourself |
| Speaker | Small loudspeaker | 1 | `speaker` mesh ×1; the HAT carries a PAM8406 amplifier and Wago terminals |

### ⚠️ Battery correction: it is an NP-F550, not an F970

The upstream mesh is named `np_f970`, **and that is misleading**:

| | |
|---|---|
| Measured mesh bounding box | **70.8 × 38.6 × 20.6 mm** ← NP-F550/F570 dimensions |
| A real NP-F970 | ~**60 mm** thick, ~300 g |
| Model named in the source | **only ever NP-F550** (`model.rs`, `robotd-design.md`); F970 appears nowhere |

**An F970 will not fit, and 300 g eats more than a third of the 800 g budget.**
Earlier versions of this repository said "NP-F970" in several places; corrected throughout.

---

## 4. Circuit boards (2, both need fabricating)

### Board 1: RPI Robot HAT — published; download and order

| | |
|---|---|
| Source | [`pollen-robotics/elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT) (Apache-2.0) |
| Production files | `production/`: Gerbers, BOM, pick-and-place, schematic PDF, STEP |
| **Layers** | **4** (`F.Cu / In1.Cu / In2.Cu / B.Cu`) |
| **Thickness** | **1.0 mm** (KiCad `(thickness 1)`. The 0.84 mm measured from the STL is a simulation-mesh approximation — order to the KiCad value) |
| Size | **65.0 × 30.9 mm** (measured from KiCad `Edge.Cuts`, R3.5 corners) — 0.9 mm wider than a Pi Zero |
| BOM | 47 lines / **123 parts**, of which **5 lines are DNP** |
| Placements | **117 rows** in `POS.csv`, including 3 fiducials (FID1–3) and H3 → **113 actual components** |
| Hand-solderable? | ❌ **No** — VQFN-32 codec and LGA-16 IMU. Order with assembly |

**Key parts** (for costing and substitution):

| Ref | Part | Function |
|---|---|---|
| U2 | TLV320AIC3104IRHBR | Audio codec (I²C `0x18`) |
| U1 | PAM8406D | Class-D amplifier |
| MK1 | LMA2718 | On-board MEMS microphone |
| U11 | BMI088 | IMU — **fitted but unused by software** (the so-called "second IMU") |
| U8 | SIT3088E | RS-485 transceiver |
| U10 | LM5050-1 | Ideal diode (reverse protection) |
| U9 | AP63205 | Buck converter |
| U4 | CAT24C32 | EEPROM — **DNP**, so this is not a self-identifying HAT |
| J13/J14 | JST EH 3P | Dynamixel **TTL** |
| J3/J11 | JST EH 4P | Dynamixel **RS-485** |
| J5–J8 | JST SH 1 mm 4P | Qwiic / Stemma (for the ToF) |

⚠️ **Before you order:**

1. **There is no charging circuit and no USB-C input on this board.** The
   `pwr_supply_charge.kicad_sch` in the repository is an **orphan sheet** (`main.kicad_sch` never
   instantiates it; its title block still names another project). Charge the battery externally.
2. **Opening the KiCad project requires [`lib_KiCAD`](https://github.com/pollen-robotics/lib_KiCAD)**,
   or it loads as unresolved symbols. Not needed if you only fabricate — use the Gerbers.

### Board 2: `imu_to_dxl` — no public project exists; you must design it

| | |
|---|---|
| Status | 🔧 **being rebuilt in this repository**, see [teardown §3](docs/hardware-teardown.en.md) |
| Function | Presents an IMU as a Dynamixel slave (ID **200**) on the servo bus |

**Reference BOM** (this repository's design, not official):

| Ref | Part | LCSC | Note |
|---|---|---|---|
| U1 | **STM32G031F6P6** | `C529333` | MCU, TSSOP-20. Runs the Dynamixel V2 slave |
| U2 | **LSM6DSV16XTR** | `C5267406` | 6-axis IMU with SFLP fusion, LGA-14 |
| U3 | **SN74LVC2G241DCUR** | `C10430` | Tri-state buffer for single-wire half-duplex |
| U4 | **HT7533-1** | `C14289` | 3.3 V LDO, **30 V input rating** (JLC basic part — no setup fee) |
| J1/J2 | B3B-EH-A(LF)(SN) | `C160259` | Dynamixel 3P — **same part as the official HAT**, cables interchange |
| J3 | PZ254V-11-04P | `C2691448` | SWD header |
| C1–C5 | 100 nF 0402 | `C307331` | Decoupling (same part as the official HAT) |
| **C6** | 10 µF **≥25 V** | ⚠️ TBD | **LDO input** — sits directly on the 8.4 V bus |
| C7 | 10 µF 0603 10 V | `C19702` | LDO output (3.3 V); 10 V is fine here |
| R1/R2 | 10 kΩ 0402 | `C25744` | CS pull-up (**mandatory**), NRST pull-up |

> ⚠️ **Two traps on the input side, both of which destroy the board:**
>
> **1. LDO rating.** The bus reaches 8.4 V fully charged; the usual suspects fall short
> (shown as **operating limit / absolute maximum**): AP2112K **6.0 / 6.5 V**,
> ME6211 **6.0 / 6.5 V**, TLV75533 **5.5 / 6.0 V**. Use the HT7533-1 (30 V operating,
> 33 V absolute) or equivalent.
>
> **2. Input capacitor rating — this table previously got it wrong.** `C19702` is a **10 V**
> X5R. On an 8.4 V bus that is only 1.2× margin, and an X5R at 8.4 V DC bias retains **less than
> half its nominal capacitance** — before the servo switching transients. **C6 must be a ≥25 V
> part** (0805 is safer); C7, on the 3.3 V output, is fine at 10 V.

---

## 5. Mechanical

### Bearings (14 total)

| Size | Qty | Basis |
|---|---|---|
| **Ø22 × 16 × 4** | **11** | `seeed_bearing__configuration__22x16x4` referenced 11× |
| **Ø15 × 10 × 3** | **3** | `seeed_bearing__configuration_default` referenced 3× |

### Fasteners (mostly M2, ~325 pieces)

Across the assembly there are **237 M2-class holes** (Ø1.9–2.5 mm, ≥300° wrap), weighted by how
many times each part is used: **60 in the servo bodies** (15 × 4), 21 in bought parts
(bearings / PCBs / battery), and **about 156 in printed structural parts**.

> ⚠️ **On the figure**: earlier versions of this repository said "213", which does not reconcile;
> corrected to 237. The gap came from two things — the earlier count was **not weighted by usage**
> (`leg` is actually ×4, `hip_l` ×2, and so on) and it **included unused meshes**. The
> diameter-distribution table uses a different basis; see
> [fastener reconstruction](docs/fastener-reconstruction.en.md).
>
> The quantities below total 325 pieces — still **1.37×** cover for 237 holes, so ordering is unaffected.

| Size | Suggested qty | Use |
|---|---|---|
| M2×4 socket cap | 60 | thin walls |
| M2×6 socket cap | **80** (the workhorse) | |
| M2×8 socket cap | 40 | 3–5 mm hole depth |
| M2×12 socket cap | 15 | a few deep holes |
| M2 nuts | 50 | where nothing is tapped |
| **M2 heat-set inserts** | **60** | recommended for printed parts — far stronger than tapping |
| M2.5×6 | 20 | a few Ø2.7 holes |

Derivation: [fastener reconstruction](docs/fastener-reconstruction.en.md).

### Printed parts (29 types / 39 pieces)

**Watch the quantities — 8 types need more than one:**

| Part | Print |
|---|---|
| `leg_腿部` | **×4** |
| `hip_l_髋部左` | ×2 |
| `neck_颈部` | ×2 |
| `power_support_电源支架` | ×2 |
| `sole_left_左脚底` | ×2 |
| `sole_right_右脚底` | ×2 |
| `upper_leg_rigidity_plate_上腿加固板` | ×2 |
| `yaw2roll_偏航转横滚` | ×2 |
| The other 21 types | ×1 each |

**Flexible-material parts** (from naming and function — TPU suggested):
`jaw_soft_软下巴`, `soft_mouth_top_软嘴顶部`

⚠️ **Do not confuse left and right.** `upper_leg_left` and `upper_leg_right` are mirrored
(centroids +0.067 / −0.067) — you need both. Same for `ankle_*`, `sole_*`, `foot_*`.

Files: [`print/`](print/).

### Roller-skate variant (optional, extra)

To reproduce the skating function, print **additionally** and substitute:

| Part | Qty |
|---|---|
| `tire_轮胎` | **×8** |
| `rim_轮辋` | **×4** |
| `roller_blade_滚轮叶片` | ×2 |
| `ankle_l_v1` / `ankle_r_v1` | ×1 each (**replaces** the standard ankles) |

⚠️ **The skate ankles are 10 mm taller** than the standard ones (46.5 vs 36.5). The two sets are
not interchangeable.

See [`print/变体-轮滑/`](print/变体-轮滑/).

---

## 6. Still unsolved

Two things will stop you at the end, and public material cannot answer them:

1. **Battery contacts.** The CAD has only the printed `power_support` (×2) — **no contact PCB or
   spring model of any kind**. Whether Pollen use metal springs or an off-the-shelf NP-F adapter
   cannot be determined. The easy route is a commercial NP-F adapter plate.
2. **Cable harness.** No drawings exist for the Dynamixel 3P cable lengths or the camera MIPI
   ribbon. Servos ship with short cables, but internal routing lengths must be measured on the build.

---

## Sources

| Data | Source |
|---|---|
| Part quantities | geom `mesh=` reference counts in upstream `robot_walk.xml` |
| Skate-variant quantities | upstream `robot_groundcontact_rollers.xml` |
| Screw counts | hole geometry from the STLs — see [fastener reconstruction](docs/fastener-reconstruction.en.md) |
| Electronics part numbers | Rust source, device tree, `robotd.toml` — see [teardown](docs/hardware-teardown.en.md) |
| HAT board figures | the official KiCad project and `production/` files |
| Servo specification | [Robotis e-manual](https://emanual.robotis.com/docs/en/dxl/x/xl330-m288/) |
| LCSC part numbers | live query against the EasyEDA component library |
