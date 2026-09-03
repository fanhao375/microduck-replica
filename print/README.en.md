# 3D-Printable Parts

[简体中文](README.md) · **English**

Every STL for the whole robot, split into **print these** and **buy these**, with bilingual filenames.

| Directory | Count | What it is |
|---|---|---|
| [`打印件/`](打印件/) (print) | **37** | Structural parts you print yourself |
| [`标准件-无需打印/`](标准件-无需打印/) (do not print) | **9** | Models of bought parts — servos, bearings, battery, PCBs. **For fit and interference checking only** |

> These are **individual parts**, for printing.
> For **assembly relationships** see [`../cad/`](../cad/) — 16 sub-assemblies merged along the kinematic tree.

## Filename convention

```
upper_leg_left_左上腿.stl
└─ upstream name ─┘└ zh ─┘
```

The first half is the original filename from `microduck_rl`, so it cross-references the MJCF and the
source; the second half is Chinese, so a Chinese-speaking builder can tell what it is at a glance.

## Differences from upstream

Upstream `microduck_rl` ships **53 STLs**. This directory carries **46**.

**The 7 left out** are XL330 test-bench fixtures, not robot parts:

```
bench_holder  weight  spacer  axis  arm  part_1 … part_5
```

**One duplicate removed**: the same left upper leg ships upstream under two names
(`upper_leg_left` and `left_upper_leg`), byte-for-byte identical. Only
`upper_leg_left_左上腿.stl` is kept here.

> ⚠️ **Do not mistake `upper_leg_left` and `upper_leg_right` for the same part** — they are
> different geometry. You need both.

## Printing notes

No official print settings exist (upstream never published any). Measured findings so far are in
[`../BUILD-LOG.en.md`](../BUILD-LOG.en.md).

A few things the geometry itself tells you:

- **Head and trunk shells** are cosmetic — 0.12–0.16 mm layers
- **Leg structural parts** carry load — increase perimeters and infill
- **`soft_mouth_top` / `jaw_soft`** are named *soft*; the originals are presumably a flexible
  material (TPU family)
- **`tire_轮胎`** likewise — a roller-skating tyre printed in rigid filament will simply slip
- Use **heat-set inserts** for the M2 holes rather than tapping the plastic. Screw list:
  [`../docs/fastener-reconstruction.en.md`](../docs/fastener-reconstruction.en.md)

## Licence

These STLs are a **derivative** (renaming and sorting) of files published by upstream
`pollen-robotics/microduck_rl`, and carry the same **CC BY-NC-SA 4.0** terms: attribution,
share-alike, non-commercial.

See [`../NOTICE.md`](../NOTICE.md).
