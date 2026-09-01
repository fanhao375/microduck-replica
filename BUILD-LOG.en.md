# Build Log

[简体中文](构建日志.md) · **English**

> **Current status: 3D printing in progress**
> Last updated: 2026-09-02　|　first photos below ↓

Everything else in this repository is **analysis on paper** — geometry, assembly
relationships and an electronics stack recovered from the public MJCF and source.
This log records **actually building it**: what was printed, what was assembled, and what
went wrong.

> ⚠️ This repository states repeatedly that **simulation STLs are not manufacturing files**
> — simulation only guarantees outer shape and inertia, not fit tolerances, threads,
> heat-set insert bosses or cable clearance.
> **This log is the test of that claim.** The result will be recorded honestly, either way.

---

## Progress

| Date | Stage | Status | Notes |
|---|---|---|---|
| 2026-09-01 | First parts printed | 🔨 in progress | head shell, trunk shell, leg structure, feet |
| 2026-09-02 | Leg parts trial-fitted | 🔨 in progress | **M2 screws installed — the hole pattern works** |

Legend: 📋 planned · 🔨 in progress · ✅ done · ⚠️ problem hit · ❌ dead end

---

## Print Records

| Part | Material | Layer height | Infill | Time | Result |
|---|---|---|---|---|---|
| *(TBD)* | | | | | |

**Printer / slicer:** *(TBD)*

---

## Problems Hit

> This is the most valuable section of the log — **so others do not have to hit them.**
> Each entry is written as symptom → cause → fix. Unsolved ones say so.

### (TBD)

**Symptom:**
**Cause:**
**Fix:**

---

## Dimensional Verification

Whether the reverse-engineered numbers hold up on real parts:

| Item | Derived | Measured | Match |
|---|---|---|---|
| Overall envelope | 144 × 141 × 264 mm | *(TBD)* | |
| M2 clearance hole | Ø2.2 mm | *(TBD)* | |
| M2 counterbore | Ø4.4 mm | *(TBD)* | |
| M2 tapping hole | Ø1.6 mm | *(TBD)* | |
| Bearing seat | Ø22 × 16 × 4 mm | *(TBD)* | |

> FDM printing typically comes out 0.1–0.3 mm undersize on holes. Measured values here are
> valuable to anyone building after us.

---

## Open Items

- [ ] *(TBD)*

---

## Photos

### 2026-09-02 · First printed parts

![First printed parts](build-log/photos/2026-09-02-首批打印件.jpg)

Printed and partly assembled:

- **Head assembly** — multi-colour print (white / black / red), with the camera bezel and
  indicator opening; surface quality is good
- **Neck** — black, already joined to the head
- **Trunk shell** — white
- **Leg structural parts** — black, **with M2 screws already fitted**
- **Feet** — red / yellow, two of them

> 📌 **First real-world confirmation:** the **M2 screws go into the black leg parts.**
> That means the conclusion in [Fastener Reconstruction](docs/fastener-reconstruction.en.md)
> — that the whole robot is an M2 system — holds on physical parts, at least for these
> holes.
>
> Precise measured hole diameters still to come; see the verification table above.
