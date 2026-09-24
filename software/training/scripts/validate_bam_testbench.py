"""Validate the BAM M6 actuator kernel against real testbench data.

Loads real testbench recordings, replays them in MuJoCo with the BAM M6 actuator,
and compares simulated vs real position traces. Also runs BAM's own Python simulator
as a reference.

Usage:
    uv run python3 scripts/validate_bam_testbench.py [--plot] [--max-files N]
"""

import argparse
import json
import os
import sys
from copy import copy
from pathlib import Path

import mujoco
import numpy as np

# ── Paths ──
BAM_DIR = Path(os.path.expanduser("~/Rhoban/bam"))
DATA_DIR = BAM_DIR / "bam" / "data" / "processed"
PARAMS_FILE = BAM_DIR / "params" / "xl330" / "m6_new.json"
TESTBENCH_XML = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "mjlab_microduck"
    / "robot"
    / "xl330_test_bench"
    / "scene.xml"
)

# ── Load M6 params ──
with open(PARAMS_FILE) as f:
    M6 = json.load(f)

# XL330 firmware constants
ERROR_GAIN = (4096 / (2 * np.pi)) / (256 * 885)
VIN = 7.4
MAX_PWM = 1.0


def bam_python_rollout(log: dict) -> list[float]:
    """Reference: BAM's own Python simulator."""
    sys.path.insert(0, str(BAM_DIR))
    from bam.model import load_model
    from bam.simulate import Simulator

    # BAM expects arm_mass in the log (mass of the arm itself, not the payload)
    if "arm_mass" not in log:
        log = dict(log)
        log["arm_mass"] = 0.0

    model = load_model(str(PARAMS_FILE))
    sim = Simulator(model)
    result = sim.rollout_log(log, simulate_control=True)
    return result[0]  # positions


def compute_m6_friction(motor_torque, external_torque, dq):
    """M6 friction computation matching our kernel (and BAM's model.py)."""
    p = M6
    stribeck_coeff = np.exp(-(np.abs(dq / p["dtheta_stribeck"]) ** p["alpha"]))

    gearbox_torque = np.abs(
        external_torque * p["load_friction_external"]
        - motor_torque * p["load_friction_motor"]
    )
    gearbox_torque_stribeck = np.abs(
        external_torque * p["load_friction_external_stribeck"]
        - motor_torque * p["load_friction_motor_stribeck"]
    )

    frictionloss = p["friction_base"]
    frictionloss += gearbox_torque
    frictionloss += stribeck_coeff * p["friction_stribeck"]
    frictionloss += gearbox_torque_stribeck * stribeck_coeff
    # quadratic (tiny, skip for clarity)

    damping = p["friction_viscous"]
    friction_budget = frictionloss + damping * np.abs(dq)
    return friction_budget


def mujoco_rollout(log: dict) -> list[float]:
    """Run the testbench in MuJoCo with our BAM M6 actuator logic."""
    mass = log["mass"]
    kp_fw = log["kp"]
    dt = log["dt"]
    entries = log["entries"]

    # Load and modify the testbench model
    spec = mujoco.MjSpec.from_file(str(TESTBENCH_XML))

    # Convert actuator to motor (same as our kernel's edit_spec)
    for act in spec.actuators:
        act.set_to_motor()
        act.forcelimited = True
        force_limit = VIN * M6["kt"] / M6["R"]
        act.forcerange = (-force_limit, force_limit)
        act.gear = [1.0, 0, 0, 0, 0, 0]

    # Zero out MuJoCo joint friction (we handle it)
    for joint in spec.joints:
        if joint.type == mujoco.mjtJoint.mjJNT_HINGE:
            joint.damping = 0.0
            joint.frictionloss = 0.0
            joint.armature = M6["armature"]

    # Set the arm mass to match the BAM recording
    for body in spec.bodies:
        if body.name == "arm":
            # Scale mass and inertia proportionally
            original_mass = body.mass
            scale = mass / original_mass if original_mass > 0 else 1.0
            body.mass = mass
            # Scale inertia proportionally to mass
            body.fullinertia = [x * scale for x in body.fullinertia]
            break

    model = spec.compile()
    data = mujoco.MjData(model)
    model.opt.timestep = dt

    # Find joint and actuator IDs
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "1")
    dof_id = model.jnt_dofadr[joint_id]

    # Initialize state
    data.qpos[dof_id] = entries[0]["position"]
    data.qvel[dof_id] = entries[0].get("speed", 0.0)
    mujoco.mj_forward(model, data)

    positions = []
    for entry in entries:
        positions.append(float(data.qpos[dof_id]))

        if not entry["torque_enable"]:
            data.ctrl[0] = 0.0
            mujoco.mj_step(model, data)
            continue

        goal = entry["goal_position"]
        q = data.qpos[dof_id]
        dq = data.qvel[dof_id]

        # ── BAM M6 actuator logic (same as our kernel) ──

        # 1. Firmware control law
        duty = (goal - q) * kp_fw * ERROR_GAIN
        duty = np.clip(duty, -MAX_PWM, MAX_PWM)
        voltage = VIN * duty

        # 2. DC motor torque
        motor_torque = M6["kt"] * voltage / M6["R"] - M6["kt"] ** 2 * dq / M6["R"]

        # 3. External torque (from MuJoCo bias forces)
        # BAM convention: bias_torque = m*g*l*sin(q) with g=-9.81 (gravity negative)
        # MuJoCo convention: qfrc_bias has opposite sign
        external_torque = -data.qfrc_bias[dof_id]

        # 4. M6 friction
        friction_budget = compute_m6_friction(motor_torque, external_torque, dq)

        # 5. Static friction clipping
        eff_inertia = 1.0 / model.dof_invweight0[dof_id] if model.dof_invweight0[dof_id] > 0 else 1e6
        net_no_friction = motor_torque + external_torque
        tau_stop = (eff_inertia / dt) * dq + net_no_friction
        friction_mag = min(abs(tau_stop), friction_budget)
        friction_torque = -np.sign(tau_stop) * friction_mag

        # 6. Set ctrl = motor + friction (MuJoCo adds qfrc_bias)
        data.ctrl[0] = motor_torque + friction_torque

        mujoco.mj_step(model, data)

    return positions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="Show plots")
    parser.add_argument("--max-files", type=int, default=5)
    args = parser.parse_args()

    data_files = sorted(DATA_DIR.glob("*.json"))
    if args.max_files:
        data_files = data_files[: args.max_files]

    print(f"Validating BAM M6 kernel against {len(data_files)} testbench recordings")
    print(f"M6 params: kt={M6['kt']:.4f} R={M6['R']:.4f}")
    print(f"Testbench XML: {TESTBENCH_XML}")
    print()

    results = []
    for fpath in data_files:
        log = json.load(open(fpath))
        name = f"{log['trajectory']}_m{log['mass']}_kp{log['kp']}"
        print(f"  {name}...", end=" ", flush=True)

        real_pos = [e["position"] for e in log["entries"]]

        # BAM Python reference
        bam_pos = bam_python_rollout(log)

        # Our MuJoCo M6 kernel
        mj_pos = mujoco_rollout(log)

        # Compute MAE
        real_np = np.array(real_pos)
        bam_np = np.array(bam_pos)
        mj_np = np.array(mj_pos[: len(real_np)])

        mae_bam = np.mean(np.abs(bam_np - real_np))
        mae_mj = np.mean(np.abs(mj_np - real_np))
        mae_bam_vs_mj = np.mean(np.abs(bam_np - mj_np))

        print(
            f"MAE  bam_vs_real={mae_bam:.5f}  mj_vs_real={mae_mj:.5f}  bam_vs_mj={mae_bam_vs_mj:.5f}"
        )

        results.append(
            {
                "name": name,
                "real": real_np,
                "bam": bam_np,
                "mj": mj_np,
                "mae_bam": mae_bam,
                "mae_mj": mae_mj,
                "mae_bam_vs_mj": mae_bam_vs_mj,
            }
        )

    print()
    avg_bam = np.mean([r["mae_bam"] for r in results])
    avg_mj = np.mean([r["mae_mj"] for r in results])
    avg_diff = np.mean([r["mae_bam_vs_mj"] for r in results])
    print(f"Average MAE  bam_vs_real={avg_bam:.5f}  mj_vs_real={avg_mj:.5f}  bam_vs_mj={avg_diff:.5f}")

    if avg_diff > 0.01:
        print("\n⚠ BAM and MuJoCo diverge significantly — likely a kernel bug!")
    elif avg_mj > avg_bam * 1.5:
        print("\n⚠ MuJoCo worse than BAM — MuJoCo dynamics differ from BAM's simple integrator")
    else:
        print("\n✓ BAM and MuJoCo agree — kernel is correct")

    if args.plot:
        try:
            import matplotlib.pyplot as plt

            n = len(results)
            fig, axes = plt.subplots(n, 1, figsize=(12, 3 * n), sharex=False)
            if n == 1:
                axes = [axes]

            for ax, r in zip(axes, results):
                t = np.arange(len(r["real"])) * 0.005
                ax.plot(t, r["real"], "k-", lw=1.5, label="Real")
                ax.plot(t, r["bam"], "b--", lw=1.2, label=f'BAM (MAE={r["mae_bam"]:.4f})')
                ax.plot(t, r["mj"], "r:", lw=1.2, label=f'MuJoCo M6 (MAE={r["mae_mj"]:.4f})')
                ax.set_title(r["name"])
                ax.set_ylabel("Position (rad)")
                ax.legend(fontsize=8)
                ax.grid(alpha=0.3)

            axes[-1].set_xlabel("Time (s)")
            plt.tight_layout()
            plt.savefig("bam_validation.png", dpi=150)
            print("Saved bam_validation.png")
            plt.show()
        except ImportError:
            print("matplotlib not available, skipping plots")


if __name__ == "__main__":
    main()
