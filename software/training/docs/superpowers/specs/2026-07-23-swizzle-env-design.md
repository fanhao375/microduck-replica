# Swizzle roller environment — design

**Date:** 2026-07-23
**Branch:** `new_pre_alpha_rollers`

## Goal

A **separate** roller task that produces a **clean classic swizzle**: both blades
stay on the ground, the legs spread out and pull back in **symmetrically**
(hourglass pattern), propelling the duck forward. This is a simpler, more stable
alternative to the alternating stride (`Mjlab-Velocity-Flat-MicroDuck-Rollers`),
motivated by the stride not transferring well to the real robot. The stride env is
left untouched.

Sim2real is a target: same robot, observations, command semantics, domain
randomization and ONNX export as the stride env, so it **deploys identically**
(`microduck_runtime ... --roller`, same flags).

## Approach (chosen: A — remove anti-swizzle + reward symmetry)

The base roller velocity recipe *naturally* converges to a swizzle (this is the
attractor we fought against for the stride). So the simplest way to a clean swizzle
is to **remove the anti-swizzle machinery** and **reward the swizzle's defining
features** (symmetry, feet grounded). No phase scripting.

Rejected: B (explicit hourglass foot-pattern shaping) and C (phase-driven scripted
trajectory) — more complex, only needed if A's swizzle looks messy (rhythm/amplitude).

## Structure

- New file `src/mjlab_microduck/tasks/microduck_velocity_swizzle_env_cfg.py` with
  `make_microduck_velocity_swizzle_env_cfg(play=False)` and `MicroduckSwizzleRlCfg`.
  Built from `make_velocity_env_cfg()` + the roller robot, mirroring
  `microduck_velocity_rollers_env_cfg.py`'s structure (obs, DR, command, curricula).
- Register `Mjlab-Velocity-Swizzle-MicroDuck` in `tasks/__init__.py`.
- Reuse everything sim2real from the stride env: robot cfg, 61D obs layout, command
  (cmd_x push/coast/brake, straight-line: `ang_vel_z=(0,0)`, `heading_hold`), all DR
  events + curricula (com, wheel_friction), `action_over_limit`, ONNX export path.

## Reward recipe

**Kept** (task + stability + sim2real):
`wheel_speed` (forward propulsion, the task), `braking`, `upright`, `com_height_target`,
`pose`, `forward_lean`, `heading_hold`, `action_over_limit`, `feet_flat`,
`self_collisions`, regularizers (`action_rate_l2` + curriculum, `neck_action_rate_l2`,
`neck_joint_pos_l2`, `joint_torques_l2`).

**Removed** (stride / anti-swizzle machinery):
`single_support`, `glide`, `skating_air_time`, `gait_symmetry`, `hip_roll_neutral`
(the last would fight the swizzle's lateral out-motion).

**Added** (pro-swizzle):
- `leg_symmetry` — reward left/right legs mirroring. The robot uses mirrored L/R
  sign conventions, so a symmetric config satisfies `q_left + q_right ≈ 0` per pair.
  Return `-mean_pairs |q_left + q_right|` (L1, constant gradient — same form as the
  existing `bilateral_symmetry_penalty`) over the leg joint pairs (hip_yaw, hip_roll,
  hip_pitch, knee, ankle); used with a positive weight so asymmetry is penalised and
  the symmetric swizzle is favoured. This is the swizzle's defining feature.
  (Implementation: the existing `bilateral_symmetry_penalty` takes explicit L/R
  index lists; add a thin wrapper that resolves the L/R leg-joint pairs by name at
  runtime so it can be configured without hard-coded indices.)
- `grounded` — reward both blades in contact (n_contact == 2) while pushing, so the
  feet stay down (classic swizzle, no lifting). Small weight. New mdp function
  (mirror of `single_support_reward` but rewarding double support). Gate on
  `cmd_x >= 0` like the others.

Leave `hip_roll` pose std loose (as in the stride env) so the legs can spread.

## New mdp functions (in `tasks/mdp.py`)

1. `leg_symmetry_reward(env, asset_cfg)` — resolve L/R leg joint pairs by name,
   return `-mean_pairs |q_left + q_right|` (used with a positive weight).
2. `grounded_reward(env, sensor_name, command_name)` — reward exactly-two-blades in
   contact, scaled by `clamp(cmd_x, 0)`.

## Command / sim2real (identical to stride)

`cmd_x` push/coast/brake, `lin_vel_y=0`, `ang_vel_z=(0,0)` (straight-line), full DR
(com, head_com, mass/inertia, joint friction, armature, wheel friction, velocity
pushes, IMU misalignment, encoder bias, obs delays), 61D obs, `vel_scale=0.3`.
Deploys with the same runtime flags as the stride roller policy.

## PPO config

Reuse `MicroduckRollersRlCfg`'s hyperparameters (same actor/critic 512-256-128 ELU,
PPO settings, `entropy_coef=0.03`), new `experiment_name`/`run_name` = `velocity_swizzle`.

## Testing / verification

- Smoke test: `uv run train Mjlab-Velocity-Swizzle-MicroDuck --env.scene.num-envs 16
  --agent.max-iterations 2` runs without error; `leg_symmetry` and `grounded` appear
  in the reward log.
- Watch on a real run: `leg_symmetry` high (symmetric), `grounded` high (both feet
  down), `wheel_speed` rising (moves forward). Video: symmetric hourglass swizzle,
  both blades on the ground.

## Tuning knobs (post-first-run)

- If not symmetric enough → raise `leg_symmetry` weight.
- If it lifts feet → raise `grounded` weight.
- If it barely moves → the symmetry/grounded weights are too high vs `wheel_speed`;
  lower them.
- If the swizzle looks messy (rhythm/amplitude) → escalate to Approach B.
