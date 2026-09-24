#!/usr/bin/env python3
"""Sim2real validation for the XL330 test bench.

Runs the same ONNX policy on a fixed sequence of target angles, either in
MuJoCo (with the BAM M6 actuator model) or on the real XL330 (via rustypot),
logs the joint trajectory, and plots sim vs real for comparison.

Example workflow
----------------
    # 1) Record in sim:
    uv run python scripts/testbench_sim2real.py --mode sim  --onnx policy.onnx --out sim.npz
    # 2) Plug the real bench via USB, then record on hardware:
    uv run python scripts/testbench_sim2real.py --mode real --onnx policy.onnx --out real.npz \
        --port /dev/ttyUSB0 --motor-id 1
    # 3) Compare the two traces:
    uv run python scripts/testbench_sim2real.py --compare sim.npz real.npz --out-plot comparison.png

Observation layout (must match the training env): [joint_pos, joint_vel, last_action, command]
Action: 1-D position offset in radians, scaled by 1.0, added to default pose (0.0).
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from mjlab_microduck.robot.testbench_constants import (
    TESTBENCH_ARM_MASS,
    TESTBENCH_XML,
    _set_arm_mass,
)


# --- Match training env ---
CONTROL_DT = 0.02   # decimation=4 × timestep=0.005  (policy rate = 50 Hz)
SIM_DT = 0.005      # (logging rate  = 200 Hz — one sample per inner sim step)
LOG_DT = SIM_DT
DEFAULT_POS = 0.0
MAX_ANGLE = math.radians(80.0)

# XL330 present_velocity is returned by rustypot as raw ticks (i32, NOT converted).
# Each tick = 0.229 RPM (per Dynamixel XL330 spec). rad/s = ticks * 0.229 * 2π/60.
DXL_VEL_TICK_TO_RAD_S = 0.229 * 2.0 * math.pi / 60.0  # ≈ 0.02398 rad/s per tick


# ---------------------------------------------------------------------------
# Shared: target schedule + policy wrapper
# ---------------------------------------------------------------------------


def make_target_schedule(
    total_time: float,
    hold_time: float = 4.0,
    seed: int = 0,
) -> np.ndarray:
    """Return one target angle per control step."""
    rng = np.random.default_rng(seed)
    n_steps = int(round(total_time / CONTROL_DT))
    steps_per_hold = int(round(hold_time / CONTROL_DT))
    targets = np.zeros(n_steps, dtype=np.float32)
    i = 0
    while i < n_steps:
        angle = float(rng.uniform(-MAX_ANGLE, MAX_ANGLE))
        end = min(i + steps_per_hold, n_steps)
        targets[i:end] = angle
        i = end
    return targets


class PolicyRunner:
    def __init__(self, onnx_path: str, action_scale: float = 1.0):
        print(f"Loading policy: {onnx_path}  (action_scale={action_scale})")
        self.session = ort.InferenceSession(onnx_path)
        self.in_name = self.session.get_inputs()[0].name
        in_shape = self.session.get_inputs()[0].shape
        print(f"  input  {self.in_name} shape={in_shape}")
        self.action_scale = action_scale
        self.last_action = np.zeros(1, dtype=np.float32)

    def reset(self):
        self.last_action[:] = 0.0

    def step(self, q: float, qd: float, target: float) -> float:
        # Matches the testbench env's policy obs layout:
        #   [joint_pos_rel, joint_vel_rel, last_action, command]  (4-d).
        obs = np.array(
            [q - DEFAULT_POS, qd, self.last_action[0], target],
            dtype=np.float32,
        )[None, :]
        action = self.session.run(None, {self.in_name: obs})[0].reshape(-1)
        self.last_action = action.astype(np.float32)
        return DEFAULT_POS + float(action[0]) * self.action_scale


# ---------------------------------------------------------------------------
# Sim rollout (mujoco, same XL330 testbench XML as training)
# ---------------------------------------------------------------------------


def rollout_sim_bam(onnx_path: str, total_time: float, seed: int, action_scale: float) -> dict:
    """Sim rollout using bam's MujocoController on a vanilla MuJoCo step loop.

    Pros: 200 Hz inner-step logging, no torch/mjwarp.  Cons: not the exact
    actuator that was trained against (uses bam upstream, not mjlab's M6).
    """
    import mujoco  # local import so --mode real works without mujoco

    from bam.actuators import actuators as bam_actuators
    from bam.model import models as bam_models
    from bam.mujoco import MujocoController

    # Load the fitted XL330 m6 params from the canonical bam bundle (identical to
    # the values that used to live in mjlab_microduck.actuator.bam_params).
    import json as _json
    from bam.model import _resolve_json_path
    with open(_resolve_json_path(None, "xl330", "m6")) as _f:
        DEFAULT_XL330_M6 = _json.load(_f)

    VIN = 7.4
    KP_FW = 200.0
    ACTUATOR_NAME = "1"

    # Build BAM's M6 model + XL330 voltage-controlled actuator.  The
    # MujocoController below drives the joint via this model on every step,
    # writing torque to data.ctrl and updating dof_frictionloss/dof_damping
    # so MuJoCo's solver applies BAM's Stribeck+load+quadratic friction.
    bam_model = bam_models["m6"]()
    bam_model.set_actuator(bam_actuators["xl330"]())
    bam_model.actuator.kp = KP_FW
    bam_model.actuator.vin = VIN
    bam_model.load_parameters_from_dict(DEFAULT_XL330_M6)

    kt = bam_model.kt.value
    R = bam_model.R.value

    spec = mujoco.MjSpec.from_file(str(TESTBENCH_XML))
    _set_arm_mass(spec, TESTBENCH_ARM_MASS)

    # MujocoController needs a torque-controlled motor; the XL330 entry in the
    # XML is a position actuator, so convert it and set the voltage-bounded
    # force range.  Armature is set on the dof by MujocoController.__init__.
    for act in spec.actuators:
        act.set_to_motor()
        act.forcelimited = False
        fl = VIN * kt / R
        act.forcerange = (-fl, fl)
        act.gear = [1.0, 0, 0, 0, 0, 0]
    for joint in spec.joints:
        if joint.type == mujoco.mjtJoint.mjJNT_HINGE:
            joint.damping = 0.0
            joint.frictionloss = 0.0

    model = spec.compile()
    data = mujoco.MjData(model)
    model.opt.timestep = SIM_DT

    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "1")
    dof_id = int(model.jnt_dofadr[joint_id])
    qpos_id = int(model.jnt_qposadr[joint_id])

    data.qpos[qpos_id] = 0.0
    data.qvel[dof_id] = 0.0
    mujoco.mj_forward(model, data)

    bam_ctrl = MujocoController(bam_model, ACTUATOR_NAME, model, data)
    bam_ctrl.reset(data.qpos)

    runner = PolicyRunner(onnx_path, action_scale=action_scale)
    policy_targets = make_target_schedule(total_time, seed=seed)
    decim = int(round(CONTROL_DT / SIM_DT))

    # Logging at SIM_DT (200 Hz): decim samples per policy step.
    N_log = len(policy_targets) * decim
    rec = {k: np.zeros(N_log, dtype=np.float32)
           for k in ("t", "target", "q", "qd", "action", "ctrl")}

    t = 0.0
    log_i = 0
    for policy_i, target in enumerate(policy_targets):
        q = float(data.qpos[qpos_id])
        qd = float(data.qvel[dof_id])
        goal = runner.step(q, qd, float(target))
        action_raw = float(runner.last_action[0])

        for _ in range(decim):
            q = float(data.qpos[qpos_id])
            dq = float(data.qvel[dof_id])

            # ---- log at 200 Hz ----
            rec["t"][log_i] = t
            rec["target"][log_i] = target
            rec["q"][log_i] = q
            rec["qd"][log_i] = dq
            rec["action"][log_i] = action_raw
            rec["ctrl"][log_i] = goal
            log_i += 1

            # BAM owns control/torque/friction: set the target, then update()
            # writes torque to data.ctrl and pushes friction/damping onto the
            # dof so MuJoCo's solver applies them on the next step.
            bam_ctrl.set_q_target(ACTUATOR_NAME, goal)
            bam_ctrl.update()
            mujoco.mj_step(model, data)
            t += SIM_DT

    return rec


def rollout_sim_mjlab(onnx_path: str, total_time: float, seed: int, action_scale: float) -> dict:
    """Sim rollout via the actual mjlab testbench env (same BAM M6 the policy was trained against).

    Boots make_testbench_env_cfg() with num_envs=1, overrides the target_angle
    command with our deterministic schedule each policy tick, and steps the env
    with the policy action.  We replicate ManagerBasedRlEnv.step's inner
    decimation loop manually so we can log q/qd at SIM_DT (200 Hz) between
    sub-steps, matching the bam backend's logging rate.
    """
    import torch

    from mjlab.envs import ManagerBasedRlEnv

    from mjlab_microduck.tasks.testbench_env_cfg import make_testbench_env_cfg

    env_cfg = make_testbench_env_cfg(play=True)
    env_cfg.scene.num_envs = 1
    # Disable auto-resampling and auto-reset so our deterministic schedule and
    # initial pose hold for the entire rollout.
    env_cfg.commands["target_angle"].resampling_time_range = (1e6, 1e6)
    env_cfg.episode_length_s = max(total_time + 10.0, env_cfg.episode_length_s)
    # Drop observation noise so the mjlab path is a fair sim2real reference
    # (matches the bam path which doesn't inject noise either).
    env_cfg.observations["policy"].enable_corruption = False

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    env = ManagerBasedRlEnv(cfg=env_cfg, device=device)
    env.reset(seed=seed)

    cmd_term = env.command_manager.get_term("target_angle")
    robot = env.scene["robot"]

    runner = PolicyRunner(onnx_path, action_scale=action_scale)
    policy_targets = make_target_schedule(total_time, seed=seed)
    decim = env.cfg.decimation
    physics_dt = env.physics_dt
    N_log = len(policy_targets) * decim
    rec = {k: np.zeros(N_log, dtype=np.float32)
           for k in ("t", "target", "q", "qd", "action", "ctrl")}

    t = 0.0
    log_i = 0
    for target in policy_targets:
        # Inject deterministic target and recompute obs so the policy sees it
        # this tick (the env's TargetAngleCommand otherwise samples randomly).
        cmd_term._target[0, 0] = float(target)
        # update_history=True is critical: the testbench env's joint_vel obs
        # has a 1-tick delay, so the history buffer must advance each policy
        # tick or the policy sees stale velocity.
        obs_buf = env.observation_manager.compute(update_history=True)
        policy_obs = obs_buf["policy"][0].detach().cpu().numpy().astype(np.float32)
        ort_out = runner.session.run(None, {runner.in_name: policy_obs[None, :]})[0].reshape(-1)
        runner.last_action = ort_out.astype(np.float32)
        action_raw = float(ort_out[0])
        goal = DEFAULT_POS + action_raw * action_scale

        # Manually run the decimation loop ManagerBasedRlEnv.step uses, so we
        # can sample joint state at the physics rate (200 Hz).
        action = torch.as_tensor(ort_out, device=device).reshape(1, -1)
        env.action_manager.process_action(action)
        for _ in range(decim):
            # Log the pre-step state to mirror the bam backend (which records
            # q/qd right before each mj_step).
            rec["t"][log_i] = t
            rec["target"][log_i] = float(target)
            rec["q"][log_i] = float(robot.data.joint_pos[0, 0].item())
            rec["qd"][log_i] = float(robot.data.joint_vel[0, 0].item())
            rec["action"][log_i] = action_raw
            rec["ctrl"][log_i] = goal
            log_i += 1

            env.action_manager.apply_action()
            env.scene.write_data_to_sim()
            env.sim.step()
            env.scene.update(dt=physics_dt)
            t += physics_dt

    env.close()
    return rec


# ---------------------------------------------------------------------------
# Real rollout (rustypot XL330)
# ---------------------------------------------------------------------------


def rollout_real(
    onnx_path: str,
    total_time: float,
    seed: int,
    port: str,
    motor_id: int,
    baudrate: int,
    kp: int,
    action_scale: float,
) -> dict:
    from rustypot import Xl330PyController

    ctrl = Xl330PyController(port, baudrate, 0.05)
    assert ctrl.ping(motor_id), f"motor id={motor_id} not responding on {port}"

    # Match the firmware gain used in sim (BAM kp_fw=200).
    ctrl.write_torque_enable(motor_id, False)
    ctrl.write_operating_mode(motor_id, 3)  # position control
    ctrl.write_position_p_gain(motor_id, kp)
    ctrl.write_position_i_gain(motor_id, 0)
    ctrl.write_position_d_gain(motor_id, 0)
    # Read back to confirm the gain actually landed (firmware silently clamps
    # out-of-range values, so verifying catches mismatches early).
    readback = ctrl.read_position_p_gain(motor_id)
    if isinstance(readback, (list, tuple)):
        readback = readback[0]
    print(f"  XL330 position P-gain: requested={kp}, readback={readback}")
    ctrl.write_goal_position(motor_id, 0.0)
    ctrl.write_torque_enable(motor_id, True)
    time.sleep(1.0)  # let it settle at zero

    runner = PolicyRunner(onnx_path, action_scale=action_scale)
    policy_targets = make_target_schedule(total_time, seed=seed)

    decim = int(round(CONTROL_DT / LOG_DT))  # samples per policy tick (4 at 200 Hz / 50 Hz)
    N_log = len(policy_targets) * decim
    rec = {k: np.zeros(N_log, dtype=np.float32)
           for k in ("t", "target", "q", "qd", "action", "ctrl")}

    def _scalar(x) -> float:
        if isinstance(x, (list, tuple)):
            return float(x[0])
        return float(x)

    t_start = time.perf_counter()
    prev_q = 0.0
    log_i = 0
    goal = 0.0
    action_raw = 0.0

    for policy_i, target in enumerate(policy_targets):
        tick_start = time.perf_counter()
        target_f = float(target)

        # Read once, run policy, write goal — all at the start of the 20 ms window.
        q = _scalar(ctrl.read_present_position(motor_id))
        try:
            qd = _scalar(ctrl.read_present_velocity(motor_id)) * DXL_VEL_TICK_TO_RAD_S
        except Exception:
            qd = (q - prev_q) / CONTROL_DT

        goal = runner.step(q, qd, target_f)
        action_raw = float(runner.last_action[0])
        # ctrl.write_goal_position(motor_id, float(np.clip(goal, -MAX_ANGLE, MAX_ANGLE)))
        ctrl.write_goal_position(motor_id, float(goal))

        # First 200 Hz sample uses the values we just read (no extra USB round-trip).
        rec["t"][log_i] = time.perf_counter() - t_start
        rec["target"][log_i] = target_f
        rec["q"][log_i] = q
        rec["qd"][log_i] = qd
        rec["action"][log_i] = action_raw
        rec["ctrl"][log_i] = goal
        prev_q = q
        log_i += 1

        # Remaining (decim-1) samples inside the policy window: read only.
        for k in range(1, decim):
            sample_deadline = tick_start + (k + 1) * LOG_DT
            while time.perf_counter() < sample_deadline - 0.001:
                time.sleep(0.0005)
            q = _scalar(ctrl.read_present_position(motor_id))
            try:
                qd = _scalar(ctrl.read_present_velocity(motor_id)) * DXL_VEL_TICK_TO_RAD_S
            except Exception:
                qd = (q - prev_q) / LOG_DT
            prev_q = q

            rec["t"][log_i] = time.perf_counter() - t_start
            rec["target"][log_i] = target_f
            rec["q"][log_i] = q
            rec["qd"][log_i] = qd
            rec["action"][log_i] = action_raw
            rec["ctrl"][log_i] = goal
            log_i += 1

        # Live status on every new segment plus a heartbeat.
        new_segment = policy_i == 0 or policy_targets[policy_i] != policy_targets[policy_i - 1]
        if new_segment or policy_i % 25 == 0:
            print(
                f"\r  t={rec['t'][log_i-1]:6.2f}s  target={math.degrees(target_f):+6.1f}°  "
                f"q={math.degrees(q):+6.1f}°  err={math.degrees(q - target_f):+6.1f}°  "
                f"goal={math.degrees(goal):+6.1f}°",
                end="" if not new_segment else "\n",
                flush=True,
            )

        # Hold the remaining time of the policy window if we got here early.
        dt_left = CONTROL_DT - (time.perf_counter() - tick_start)
        if dt_left > 0:
            time.sleep(dt_left)
    print()

    ctrl.write_torque_enable(motor_id, False)
    return rec


# ---------------------------------------------------------------------------
# Plotting / analytics
# ---------------------------------------------------------------------------


def _mae(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    return float(np.mean(np.abs(a[:n] - b[:n])))


def npz_to_bam_log(npz_path: str, json_path: str, *, mass: float, length: float,
                   kp: int, vin: float) -> None:
    """Convert a rollout .npz (written by rollout_sim/rollout_real) to a BAM log json.

    BAM log format (see ~/Rhoban/bam/bam/logs.py):
      top-level: mass, length, kp, vin, motor, trajectory, dt
      entries:   position, speed, load, input_volts, temp, goal_position, torque_enable, timestamp
    Can be fed to `python -m bam.plot --logdir <dir> --actuator xl330`.
    """
    import json

    d = dict(np.load(npz_path))
    t = d["t"]
    # Prefer the actual recorded timestamps for dt to handle small jitter;
    # fall back to the fixed control period if there are fewer than 2 samples.
    dt = float(np.mean(np.diff(t))) if len(t) > 1 else CONTROL_DT

    entries = []
    for i in range(len(t)):
        entries.append({
            "position": float(d["q"][i]),
            "speed": float(d["qd"][i]),
            "load": 0.0,
            "input_volts": vin,
            "temp": 25.0,
            "goal_position": float(d["ctrl"][i]),
            "torque_enable": True,
            "timestamp": float(t[i]),
        })

    log = {
        "mass": mass,
        "length": length,
        "kp": kp,
        "vin": vin,
        "motor": "xl330",
        "trajectory": "rl_policy",
        "dt": dt,
        "entries": entries,
    }

    out = Path(json_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(log, f)
    print(f"Wrote BAM log: {out} ({len(entries)} entries, dt={dt:.4f}s, mass={mass}kg, kp={kp})")
    print(f"  Replay with: (cd ~/Rhoban/bam && python -m bam.plot --logdir {out.parent} --actuator xl330)")


def compare_and_plot(sim_file: str, real_file: str, out_path: str) -> None:
    import matplotlib.pyplot as plt

    sim = dict(np.load(sim_file))
    real = dict(np.load(real_file))
    n = min(len(sim["t"]), len(real["t"]))
    t = sim["t"][:n]

    err_sim = sim["q"][:n] - sim["target"][:n]
    err_real = real["q"][:n] - real["target"][:n]

    print("\n=== Analytics ===")
    print(f"  steps compared       : {n}")
    print(f"  MAE q (sim vs real)  : {_mae(sim['q'], real['q']):.4f} rad "
          f"({math.degrees(_mae(sim['q'], real['q'])):.2f}°)")
    print(f"  sim   tracking MAE   : {float(np.mean(np.abs(err_sim))):.4f} rad "
          f"({math.degrees(float(np.mean(np.abs(err_sim)))):.2f}°)")
    print(f"  real  tracking MAE   : {float(np.mean(np.abs(err_real))):.4f} rad "
          f"({math.degrees(float(np.mean(np.abs(err_real)))):.2f}°)")
    print(f"  sim   qd RMS         : {float(np.sqrt(np.mean(sim['qd'][:n]**2))):.3f} rad/s")
    print(f"  real  qd RMS         : {float(np.sqrt(np.mean(real['qd'][:n]**2))):.3f} rad/s")
    print(f"  action MAE           : {_mae(sim['action'], real['action']):.4f} rad")

    fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=True)

    axes[0].plot(t, sim["target"][:n], "k-", lw=1, label="target", alpha=0.4)
    axes[0].plot(t, sim["q"][:n], "b-", lw=1.2, label="sim q")
    axes[0].plot(t, real["q"][:n], "r-", lw=1.2, label="real q")
    axes[0].plot(t, sim["ctrl"][:n], "b:", lw=0.8, alpha=0.6, label="sim goal (policy)")
    axes[0].plot(t, real["ctrl"][:n], "r:", lw=0.8, alpha=0.6, label="real goal (policy)")
    axes[0].set_ylabel("position [rad]")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].grid(alpha=0.3)
    axes[0].set_title(f"Testbench sim2real — MAE(sim, real) = {_mae(sim['q'], real['q']):.4f} rad")

    axes[1].plot(t, np.degrees(err_sim), "b-", lw=1, label="sim")
    axes[1].plot(t, np.degrees(err_real), "r-", lw=1, label="real")
    axes[1].axhline(0, color="k", lw=0.5)
    axes[1].set_ylabel("tracking error [deg]")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    axes[2].plot(t, sim["qd"][:n], "b-", lw=1, label="sim")
    axes[2].plot(t, real["qd"][:n], "r-", lw=1, label="real")
    axes[2].set_ylabel("velocity [rad/s]")
    axes[2].legend(fontsize=8)
    axes[2].grid(alpha=0.3)

    axes[3].plot(t, sim["action"][:n], "b-", lw=1, label="sim action")
    axes[3].plot(t, real["action"][:n], "r-", lw=1, label="real action")
    axes[3].set_ylabel("policy action [rad]")
    axes[3].set_xlabel("time [s]")
    axes[3].legend(fontsize=8)
    axes[3].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    print(f"\nSaved plot: {out_path}")
    plt.show()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "real"], help="Rollout mode")
    ap.add_argument("--sim-backend", choices=["bam", "mjlab"], default="bam",
                    help="Sim backend: 'bam' uses bam.MujocoController on a vanilla "
                         "mujoco loop (200 Hz log, lightweight); 'mjlab' boots the actual "
                         "make_testbench_env_cfg() mjlab env with its BamM6Actuator (50 Hz log).")
    ap.add_argument("--onnx", type=str, help="Path to trained ONNX policy")
    ap.add_argument("--out", type=str, help="Output .npz log file")
    ap.add_argument("--duration", type=float, default=30.0, help="Total time [s]")
    ap.add_argument("--seed", type=int, default=0, help="Target schedule seed")
    # real-only
    ap.add_argument("--port", type=str, default="/dev/ttyUSB0")
    ap.add_argument("--motor-id", type=int, default=1)
    ap.add_argument("--baudrate", type=int, default=1_000_000)
    ap.add_argument("--kp", type=int, default=200, help="XL330 position P gain")
    ap.add_argument("--action-scale", type=float, default=1.0,
                    help="Multiplier applied to the policy action before offsetting "
                         "by the default pose (must match training env action scale)")
    # compare mode
    ap.add_argument("--compare", nargs=2, metavar=("SIM_NPZ", "REAL_NPZ"),
                    help="Plot two logged runs side by side")
    ap.add_argument("--out-plot", type=str, default="testbench_sim2real.png")
    # BAM log export
    ap.add_argument("--to-bam", nargs=2, metavar=("NPZ", "JSON"),
                    help="Convert a rollout NPZ to BAM log format "
                         "(run with: python -m bam.plot --logdir <dir> --actuator xl330)")
    ap.add_argument("--bam-mass", type=float, default=TESTBENCH_ARM_MASS, help="Payload mass [kg]")
    ap.add_argument("--bam-length", type=float, default=0.1, help="Arm length [m]")
    ap.add_argument("--bam-vin", type=float, default=7.4, help="Supply voltage [V]")

    args = ap.parse_args()

    if args.compare:
        compare_and_plot(args.compare[0], args.compare[1], args.out_plot)
        return

    if args.to_bam:
        npz_to_bam_log(
            args.to_bam[0],
            args.to_bam[1],
            mass=args.bam_mass,
            length=args.bam_length,
            kp=args.kp,
            vin=args.bam_vin,
        )
        return

    if not (args.mode and args.onnx and args.out):
        ap.error("--mode, --onnx and --out are required for a rollout")

    if args.mode == "sim":
        sim_fn = rollout_sim_mjlab if args.sim_backend == "mjlab" else rollout_sim_bam
        rec = sim_fn(args.onnx, args.duration, args.seed, args.action_scale)
    else:
        rec = rollout_real(
            args.onnx, args.duration, args.seed,
            args.port, args.motor_id, args.baudrate, args.kp,
            args.action_scale,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, **rec)
    err = rec["q"] - rec["target"]
    print(f"\nSaved {len(rec['t'])} samples to {out}")
    print(f"  tracking MAE: {float(np.mean(np.abs(err))):.4f} rad ({math.degrees(float(np.mean(np.abs(err)))):.2f}°)")


if __name__ == "__main__":
    main()
