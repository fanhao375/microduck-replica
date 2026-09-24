# Swizzle head control (Y button) — design

**Date:** 2026-07-27
**Branch:** `new_pre_alpha_rollers`
**Task touched:** `Mjlab-Velocity-Swizzle-MicroDuck` (`microduck_velocity_swizzle_env_cfg.py`)

## Goal

Let the operator move the duck's HEAD to different poses (Y button) while it rollers,
**without the swizzle falling apart when the head moves**. The head pose is the
physical head/neck joints (look up/down/left/right) — unrelated to `heading_tracking`
(the body's travel direction, which is untouched).

The current swizzle policy would tip over if the head moved as an external offset (it
doesn't compensate the CoM shift), so the head must be **policy-managed**: the policy
produces the head pose AND keeps its balance. This matches how the walking policy does
it in `--new-cmd-obs` mode — the head is a COMMAND injected into the observation, and
the policy produces the pose (no external "double-add").

## Approach (chosen: A — policy-managed head via the obs command)

Port the head-command machinery that already exists in `microduck_velocity_env_cfg.py`
into the swizzle env: feed a real head-pose command into the (currently zero-padded)
`head_command` obs slot, reward the head tracking that command, and ramp it in LATE
via a curriculum so it doesn't disturb the swizzle.

No external-offset option (rejected earlier: the swizzle won't stay upright if the head
moves without the policy compensating).

## Changes (all in `make_microduck_velocity_swizzle_env_cfg`)

1. **Head-pose command term.** Add `cfg.commands["head_pose"] = UniformPoseCommandCfg(...)`,
   copied from the velocity env: 4D `[neck_pitch, head_pitch, head_yaw, head_roll]`
   deltas-from-default, `resampling_time_range = (2.0, 5.0)`, per-joint ranges (head_roll
   tighter, matching the small mechanical range).
2. **Real `head_command` obs.** Replace the current `zero_command_padding(dim=4)` head
   slot with the real command obs `func=<head command obs>, params={"command_name":
   "head_pose"}`, for BOTH actor and critic. (Keeps the 61D layout; body_command stays
   zero-padded — no body-pose control here.)
3. **`head_pose_tracking` reward.** Add `cfg.rewards["head_pose_tracking"]`
   (`microduck_mdp.head_pose_tracking`, `command_name="head_pose"`, `std=0.5`), initial
   weight 0 (curriculum-ramped).
4. **Late curriculum.** A `reward_weight` curriculum ramps `head_pose_tracking` from 0
   → **4.0**, staying 0 until **~1500 iters** (swizzle solid) then climbing over the
   next ~1000 iters, mirroring velstand's body-pose kick-in. Plus a head-pose command-
   range curriculum: start with tight ranges (small head deltas) and widen them over
   the same window, so the head barely moves early and reaches full range once the
   policy can handle it. This is what makes head control "not hard to manage" — it is
   added on top of an already-stable swizzle. (Values are starting points, tunable.)
5. **Reconcile the neck penalty (required).** The env currently has `neck_joint_pos_l2`
   which pulls the neck/head joints toward HOME — it would FIGHT `head_pose_tracking`
   (which pulls them to the command) so the head would never move. Exclude the
   head-pose joints from `neck_joint_pos_l2` (or drop it), mirroring the velocity env's
   handling (its comment: keeping them in both "would pull them to HOME while
   head_pose_tracking pulls them to the command"). Keep `neck_action_rate_l2` (smoothness,
   no conflict).

Everything else (swizzle, backward locomotion, heading curriculum, DR, obs layout,
command) is unchanged. Requires **retraining** the swizzle task.

## Runtime

No runtime code change. The `microduck_runtime` **Y button** already drives the
`head_command` obs slot (new-cmd-obs mode injects the head offset as a command, "don't
double-add"). Once the swizzle policy is retrained with head control, it responds to Y.
Deploy flags unchanged (`--roller --new-cmd-obs ...`).

## Testing / verification

- Smoke test: `uv run train Mjlab-Velocity-Swizzle-MicroDuck --env.scene.num-envs 16
  --agent.max-iterations 2` runs; `head_pose_tracking` appears in the reward log; the
  `head_pose` command and real `head_command` obs build without error.
- Real run: `head_pose_tracking` rises after the curriculum kick-in; the swizzle stays
  stable (fall rate does not spike when the head curriculum turns on). In the viewer /
  on the robot: moving the head command moves the head, and the roller keeps skating.

## Tuning knobs

- Head disrupts the swizzle when it kicks in → push the curriculum kick-in later, or
  widen the head range more slowly.
- Head doesn't follow well → raise `head_pose_tracking` target weight, or check the neck
  penalty still isn't fighting it.
- Head too twitchy → keep/raise `neck_action_rate_l2`.
