# Microduck Ecosystem Map

[简体中文](生态导航.md) · **English**

Microduck material is scattered across several GitHub organisations and three HuggingFace resource
types. **The official hardware repositories are especially easy to miss** — they are not in the main
repo and carry an `elec_` prefix.

This index says what each thing **is** and **what it is good for**. Figures are as of **2026-09-04**;
star counts move.

---

## 1. Official repositories (pollen-robotics)

### The two core ones

| Repository | ★ | Licence | What it is |
|---|---|---|---|
| [**microduck**](https://github.com/pollen-robotics/microduck) | 6982 | Apache-2.0 | **The on-board runtime.** Everything that runs on the robot, in Rust: the 50 Hz control loop, updater, BLE, camera/WebRTC, ToF, voice. It is also where the hardware spec lives — device paths, I²C addresses and registers are all hard-coded in the source |
| [**microduck_rl**](https://github.com/pollen-robotics/microduck_rl) | 1668 | Apache-2.0 | **The RL training environment** (built on mjlab). **The 47 STLs and the full MJCF are here** — the only source of geometry |

### Hardware (the two people miss)

| Repository | ★ | Licence | What it is |
|---|---|---|---|
| [**elec_RPI_Robot_HAT**](https://github.com/pollen-robotics/elec_RPI_Robot_HAT) | 15 | Apache-2.0 | ⭐ **The HAT board, fully published.** KiCad 9 schematic and PCB, Gerbers, BOM, pick-and-place, STEP, and every datasheet. **You do not draw this board — download `production/` and have it made** (4-layer) |
| [**lib_KiCAD**](https://github.com/pollen-robotics/lib_KiCAD) | 3 | — | Pollen's KiCad symbol / footprint / 3D-model library. **Required to open the project above**, otherwise it is a field of unresolved symbols |

> Many people believe "Microduck hardware isn't open" — **the HAT board is open**, it just isn't in
> the main repo. What genuinely is not published: the `imu_to_dxl` board, editable mechanical CAD,
> and a whole-robot BOM.

### Supporting

| Repository | ★ | What it is |
|---|---|---|
| [rustypot](https://github.com/pollen-robotics/rustypot) | 56 | Dynamixel communication library (Rust). The runtime talks to servos through it; `xl330.rs` carries the full register map |
| [microduck-gst-plugins](https://github.com/pollen-robotics/microduck-gst-plugins) | 13 | Prebuilt aarch64 GStreamer plugins: Rockchip MPP hardware encoders + WebRTC |
| `duck_detector` | — | ⚠️ **Not public** (404). The training repo for the NPU detection model. Upstream docs cite it, but outsiders cannot reach it |

---

## 2. HuggingFace

### Simulators and visualisation

| Name | ♥ | Note |
|---|---|---|
| [**pollen-robotics/microduck-simulator**](https://huggingface.co/spaces/pollen-robotics/microduck-simulator) | **374** | ⭐ **The official web simulator — open it and play**, nothing to install. Start here |
| [mishig/microduck-anatomy](https://huggingface.co/spaces/mishig/microduck-anatomy) | 6 | Anatomy visualisation, by an HF employee |
| multimodalart/microduck-ar | 1 | AR viewer |
| AlexWortega/microduck-vla-simulator | 0 | Vision-language-action simulation |

### Policies

| Name | ♥ | Note |
|---|---|---|
| [**pollen-robotics/microduck-policies**](https://huggingface.co/pollen-robotics/microduck-policies) | 8 | ⭐ **The official nine ONNX policies.** Same hardware means you can use them as-is, no retraining |
| [RemiFabre/microduck-flamingo-cycle](https://huggingface.co/RemiFabre/microduck-flamingo-cycle) | 21 | Posted by Rémi himself — one-legged standing cycle |
| RemiFabre/microduck-rough-walk-e / -g | 1 | Rough-terrain walking |

The community has a growing set of motion policies: `happy-hop`, `polite-bow`, `moonwalk-backward`,
`beak-throw`, `electric-slide`, `swing`, `stilts`, `running`.

> Upstream ships `uv run publish` — one command to push a policy you trained to the Hub.

### Datasets (only just appearing)

| Name | Updated | Note |
|---|---|---|
| [craigm26/microduck-stairs-challenge](https://huggingface.co/datasets/craigm26/microduck-stairs-challenge) | 09-02 | Stair climbing |
| craigm26/microduck-ball-challenge | 09-02 | Ball chasing |
| allen73/microduck-trajectory-dataset | 09-02 | Trajectories |
| devorah-ai-2026/microduck-locomotion-dataset-v1 | 09-03 | Locomotion |
| pngwn/microduck-detection-dataset | 09-03 | Object detection |
| craigm26/microduck-policy-golden-vectors | 08-31 | Golden vectors for policy regression tests |

> Datasets clustered in early September — the community moving **from playing to training**.

---

## 3. Community projects

### Tools and frameworks

| Repository | ★ | What it is |
|---|---|---|
| [rokbenko/quackd](https://github.com/rokbenko/quackd) | 139 | Give a small robot a brain; command a Microduck or Open Duck Mini in natural language |
| [jonathanhawkins/microduck-lab](https://github.com/jonathanhawkins/microduck-lab) | 73 | **Train RL policies on an ordinary Mac — no CUDA** |
| [joeynyc/awesome-microduck](https://github.com/joeynyc/awesome-microduck) | 67 | The English awesome list |
| [craigm26/duckkit](https://github.com/craigm26/duckkit) | 6 | Pure Swift implementation — real policies, real kinematics |
| [aj-dev-smith/microduck-mcp](https://github.com/aj-dev-smith/microduck-mcp) | 2 | Drive the simulator from any MCP client |

### Training and simulation

| Repository | ★ | What it is |
|---|---|---|
| [Vottivott/microduck-playground](https://github.com/Vottivott/microduck-playground) | 14 | Reproducible RL experiments, policies, simulation assets |
| [kabilankb/isaaclab-microduck](https://github.com/kabilankb/isaaclab-microduck) | 6 | Isaac Lab 3.0 / Newton MJWarp environments |
| [Macmachi/microduck-rl-genesis](https://github.com/Macmachi/microduck-rl-genesis) | 2 | A Genesis port — **trains on AMD GPUs** |
| [AlexBodner/microduck-tracking](https://github.com/AlexBodner/microduck-tracking) | 12 | Multi-object tracking |

### Replication and hardware

| Repository | ★ | What it is |
|---|---|---|
| **This repository** | 414 | Assembly drawings + CAD assemblies + printable parts + full electronics teardown |
| [IronSpiderMan/MicroDuckModels](https://github.com/IronSpiderMan/MicroDuckModels) | 30 | **Chinese.** Three.js web 3D viewer, assembled along the kinematic tree |
| [ScrapMeta/microduck-diy](https://github.com/ScrapMeta/microduck-diy) | 16 | **Chinese.** A "build one in a month" challenge log |
| [SaberOnGo/open-microduck](https://github.com/SaberOnGo/open-microduck) | 7 | Bilingual hardware documentation (**documentation only — no PCB files**) |
| [boris721/microduck-3d](https://github.com/boris721/microduck-3d) | 6 | STL + MJCF extracted from upstream |
| [Rhoban/microban](https://github.com/Rhoban/microban) | — | ⭐ **Not a Microduck, but uses the same HAT.** A 19×XL330 + Pi Zero 2W biped with a **complete public BOM and build guide** (~$567). Worth cross-checking when sourcing |

### Historical trail

| Repository | Note |
|---|---|
| [TommyZihao/microduck_runtime](https://github.com/TommyZihao/microduck_runtime) | **A public mirror of the prototype runtime.** The original `apirrone/microduck_runtime` has gone private. It records one thing worth knowing: **the prototype used a BNO055 over I²C**, not today's `imu_to_dxl` board |

---

## Where to start

| If you want to… | Go to |
|---|---|
| Get a feel for it | The [official web simulator](https://huggingface.co/spaces/pollen-robotics/microduck-simulator) — open and play |
| See what it looks like and how it goes together | [`cad/`](../cad/) and [`assembly-drawings/`](../assembly-drawings/) here |
| Print parts | [`print/`](../print/) here |
| Understand the electronics | [`docs/hardware-teardown.en.md`](hardware-teardown.en.md) here |
| Build the HAT board | `production/` in [`elec_RPI_Robot_HAT`](https://github.com/pollen-robotics/elec_RPI_Robot_HAT) — install `lib_KiCAD` first |
| Train your own policy | [`microduck_rl`](https://github.com/pollen-robotics/microduck_rl); no NVIDIA GPU? use [microduck-lab](https://github.com/jonathanhawkins/microduck-lab) |
| Read the hardware spec | The [`microduck`](https://github.com/pollen-robotics/microduck) source — **the code is the datasheet** |
