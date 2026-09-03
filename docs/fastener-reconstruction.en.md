# Fastener Reconstruction

[简体中文](紧固件反推.md) · **English**

There is no mechanical BOM upstream and no screw list (the HAT board has an electronics BOM, but it covers no structural parts). Everything below was **recovered from the
geometry of the 47 STL meshes.**

## Method

`scripts/analyze_holes.py`:

1. Weld duplicate vertices, build a face-adjacency graph
2. Cut at a 35° dihedral angle and region-grow "smooth surface patches" (breaks at sharp
   edges)
3. Fit a cylinder to each patch — a cylinder's face normals are all perpendicular to its
   axis, so **the axis is the smallest eigenvector of the normal covariance matrix**
4. Project the vertices onto the plane perpendicular to that axis and fit a circle
   (Kåsa algebraic fit) for the diameter; discard anything with >3 % relative residual
5. Normals pointing toward the axis = **a hole**; pointing away = a shaft or boss, excluded
6. Measure angular coverage and keep only complete circles (≥300°)

All 47 parts scan in 2.5 seconds.

## Conclusion: The Whole Robot Is an M2 System

Diameter distribution of complete circular holes across the structural parts:

| Diameter | Count | Reading |
|---|---|---|
| **Ø2.2** | **77** | **M2 clearance hole** (standard M2 clearance is 2.2–2.4) |
| **Ø4.4** | **28** | **M2 counterbore** (M2 socket-head cap dia. is Ø3.8; counterbore 4.0–4.5) |
| **Ø1.6** | **20** | **M2 tapping hole** (the standard M2 tap drill is exactly 1.6) |
| Ø2.4 | 22 | loose M2 clearance |
| Ø2.0 | 12 | tight M2 clearance |
| Ø2.7 / Ø2.8 | 20 | M2.5 clearance (a small number) |
| Ø4.8 | 10 | M2 counterbore, a second depth |
| Ø5.4 / Ø6.0 | 16 | shaft bores / bearing seats — not fasteners |

**Ø2.2 and Ø4.4 appear as a pair** on `yaw2roll`, `leg`, `ankle_left`, `neck_pitch` and
`yaw_roll_motion` — the classic **clearance hole + counterbore** combination, which
confirms **M2 socket-head cap screws**.

Cross-check: `xl330.stl` itself carries **4× Ø2.0 + 8× Ø1.6**, matching the Dynamixel
XL330's own M2 mounting pattern.

## Per-Part Hole Counts

Weighted by how many times each mesh is actually used in the full robot:

| Mesh | Uses | M2 clearance | M2 tap | Counterbore |
|---|---|---|---|---|
| `xl330` | **×15** | 4 | 8 | — |
| `hip_l` | ×2 | 9 | — | 6 |
| `yaw2roll` | ×2 | 8 | — | 6 |
| `leg` | ×2 | 6 | — | 6 |
| `neck_pitch` | ×1 | 12 | — | 4 |
| `face_part` | ×1 | 10 | 4 | — |
| `ankle_left` / `ankle_right` | ×1 each | 5 | — | 5 |
| `yaw_roll_motion` | ×1 | 4 | — | 6 |
| `power_support` | ×1 | — | 8 | — |
| `left_shell` / `right_shell` | ×1 each | 7 / 6 | — / 1 | — |
| `bearing_roll` | ×2 | 4 | — | — |
| `neck` | ×2 | 4 | — | — |
| `upper_leg_rigidity_plate` | ×2 | 4 | — | — |
| `jaw` | ×1 | 5 | — | — |
| `trunk_base` | ×1 | 4 | — | — |
| `bottom_head_shell` / `top_head_shell` | ×1 each | 4 / 3 | — | — |
| `m12_lens_holder` | ×1 | 3 | — | — |

> **`xl330` appears 15 times** — the robot has 15 servos, not 14. Fourteen are under policy
> control; the fifteenth drives the beak/jaw through a `passive_*` linkage and never enters
> the action space. This matches the "15 servos" in the official README.

## Purchase Estimate

- Total M2 clearance holes across the robot: **213**, of which 60 are on the servo bodies
  themselves (15 × 4)
- Excluding servos and bought-in parts (bearings, PCBs, battery), the structural parts
  account for **roughly 146 clearance holes**

**Suggested quantities** (including losses and trial fitting):

| Spec | Suggested | For |
|---|---|---|
| M2×4 socket head | 60 | thin-wall positions (51 of 79 holes are 0–3 mm deep) |
| M2×6 socket head | 80 | the workhorse size |
| M2×8 socket head | 40 | 3–5 mm deep positions (24 of 79) |
| M2×12 socket head | 15 | a few deep holes (8–12 mm, 4 places) |
| M2 nuts | 50 | where there is nothing to tap into |
| M2 heat-set inserts | 60 | recommended over tapping printed plastic directly |
| M2.5×6 | 20 | the handful of Ø2.7 positions |

Lengths are inferred from the measured depth distribution of the Ø2.2 holes: 51 at
0–3 mm, 24 at 3–5 mm, 4 at 8–12 mm.

## Bearings

| Source file | Measured |
|---|---|
| `seeed_bearing__configuration__22x16x4` | **OD 22 × ID 16 × W 4 mm** |
| `seeed_bearing__configuration_default` | OD 15 × ID ~10 × W 3 mm (×3 in the robot) |

## ⚠️ Limitations

1. **This is recovered from simulation meshes, not from engineering drawings.** Simulation
   meshes only guarantee correct outer shape and inertia — not fit tolerances or complete
   thread detail.
2. **Print shrinkage changes the real hole size.** A Ø2.2 design hole typically comes out
   0.1–0.3 mm undersize on FDM. Print a test piece and check before committing to a build.
3. **Counterbores and clearance holes may be double-counted** (two features of the same
   screw). Screw counts here are based on clearance holes only; counterbores are not
   counted separately.
4. **Features not detected:** non-circular holes (slots, shaped cutouts), partial arcs with
   <300° coverage, and openings larger than Ø14 mm are all outside the statistics.
5. Screw **lengths** are ranges inferred from hole depth, not measured values.
