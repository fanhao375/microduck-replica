# Swizzle Head Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add operator head-pose control (Y button) to the swizzle roller task so the policy moves its head to commanded poses while staying balanced.

**Architecture:** Policy-managed head via the observation command (matches the walking `--new-cmd-obs` path). The swizzle env currently zero-pads the `head_command` obs slot; we feed a real `head_pose` command into it, reward `head_pose_tracking`, remove the two reward terms that pull the neck/head to HOME (which would fight the command), and ramp the head in LATE via a curriculum so the already-working swizzle isn't disturbed. Config-only change to one file; requires retraining.

**Tech Stack:** mjlab / mjlab_microduck task configs (Python), rsl_rl PPO. Reuses machinery already in `microduck_velocity_env_cfg.py` (`UniformPoseCommandCfg`, `head_pose_tracking`, `pose_command_range_curriculum`, `reward_weight`).

## Global Constraints

- Only the swizzle task changes: `src/mjlab_microduck/tasks/microduck_velocity_swizzle_env_cfg.py`. The stride, velocity, standup, roller-slope/crouch tasks and `mdp.py` are NOT modified.
- Keep the 61D obs layout `[twist(3), head(4), body(6)]`: replace the `head_command` slot's contents (zero-pad → real command) but keep `body_command` zero-padded (no body-pose control here).
- No new mdp functions — all reward/command/curriculum functions already exist in `microduck_mdp`.
- Runtime unchanged: the `microduck_runtime` Y button already drives the `head_command` obs slot.

---

### Task 1: Wire head-pose control into the swizzle env

**Files:**
- Modify: `src/mjlab_microduck/tasks/microduck_velocity_swizzle_env_cfg.py`
- Test: `tests/test_swizzle_head_cfg.py` (create)

**Interfaces:**
- Consumes (already exist, do not redefine):
  - `microduck_mdp.UniformPoseCommandCfg(resampling_time_range, ranges)` — head-pose command term.
  - `mdp.generated_commands` (from `mjlab.tasks.velocity`) — obs func reading a command by name; used as `params={"command_name": "head_pose"}`.
  - `microduck_mdp.head_pose_tracking` — reward `func`, params `{"command_name": "head_pose", "std": 0.5}`.
  - `microduck_mdp.reward_weight` — curriculum func, params `{"reward_name", "weight_stages": [{"step","weight"}, ...]}`.
  - `microduck_mdp.pose_command_range_curriculum` — curriculum func, params `{"command_name", "range_stages": [{"step","ranges"}, ...]}`.
- Produces: the swizzle env cfg with a `head_pose` command, a real `head_command` obs, a `head_pose_tracking` reward, `neck_joint_pos_l2` removed, the `pose` reward scoped to leg joints, and two head curricula.

- [ ] **Step 1: Write the failing test**

Create `tests/test_swizzle_head_cfg.py`:

```python
from mjlab.tasks.velocity import mdp
from mjlab_microduck.tasks.microduck_velocity_swizzle_env_cfg import (
    make_microduck_velocity_swizzle_env_cfg,
)


def test_swizzle_head_control_wired():
    cfg = make_microduck_velocity_swizzle_env_cfg()

    # Head-pose command term exists.
    assert "head_pose" in cfg.commands

    # head_command obs is the REAL command (not zero-padded) on both groups.
    for group in ("actor", "critic"):
        term = cfg.observations[group].terms["head_command"]
        assert term.func is mdp.generated_commands
        assert term.params["command_name"] == "head_pose"

    # head_pose_tracking reward exists.
    assert "head_pose_tracking" in cfg.rewards

    # The two HOME-pullers that would fight the head command are handled:
    #  - neck_joint_pos_l2 removed
    assert "neck_joint_pos_l2" not in cfg.rewards
    #  - pose reward scoped to leg joints via a negative-lookahead regex that
    #    excludes neck/head (and passive wheels)
    pose_joints = cfg.rewards["pose"].params["asset_cfg"].joint_names
    assert any(
        "(?!" in j and "neck" in j and "head" in j for j in pose_joints
    ), f"pose reward not scoped away from neck/head: {pose_joints}"

    # Late head curricula exist.
    assert "head_pose_tracking_weight" in cfg.curriculum
    assert "head_pose_range" in cfg.curriculum
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MUJOCO_GL=egl uv run pytest tests/test_swizzle_head_cfg.py -v`
Expected: FAIL (head_pose command / head_pose_tracking reward absent; head_command obs is still `zero_command_padding`).

- [ ] **Step 3: Add imports to the swizzle env cfg**

In `microduck_velocity_swizzle_env_cfg.py`, extend the imports (currently `from mjlab.managers import CurriculumTermCfg, RewardTermCfg`) to add `ObservationTermCfg`, and import the velocity mdp for `generated_commands`:

```python
from mjlab.managers import CurriculumTermCfg, ObservationTermCfg, RewardTermCfg
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.tasks.velocity import mdp
```

- [ ] **Step 4: Add the head_pose command + real head_command obs + head_pose_tracking reward + neck reconciliation**

Inside `make_microduck_velocity_swizzle_env_cfg`, AFTER the existing reward/heading setup and BEFORE `return cfg`, add:

```python
    # --- Head-pose control (Y button): the policy produces the head pose ---------
    # Head-pose command (4D deltas from HOME: [neck_pitch, head_pitch, head_yaw,
    # head_roll]). Ported from the velocity env; ranges start small (widened by the
    # curriculum below). Resample every 2-5 s.
    cfg.commands["head_pose"] = microduck_mdp.UniformPoseCommandCfg(
        resampling_time_range=(2.0, 5.0),
        ranges=(
            (-0.05, 0.05),    # neck_pitch
            (-0.05, 0.05),    # head_pitch
            (-0.07, 0.07),    # head_yaw
            (-0.015, 0.015),  # head_roll (tighter — small mechanical range)
        ),
    )

    # Feed the REAL head command into the obs (replaces zero_command_padding) on
    # both groups. body_command stays zero-padded (no body-pose control here).
    for group in ("actor", "critic"):
        cfg.observations[group].terms["head_command"] = ObservationTermCfg(
            func=mdp.generated_commands,
            params={"command_name": "head_pose"},
        )

    # Reward the head tracking its command. Weight 0 here — ramped in LATE by the
    # curriculum so it doesn't disturb the swizzle before it's solid.
    cfg.rewards["head_pose_tracking"] = RewardTermCfg(
        func=microduck_mdp.head_pose_tracking,
        weight=0.0,
        params={"command_name": "head_pose", "std": 0.5},
    )

    # Reconcile the two HOME-pullers that would fight head_pose_tracking:
    #  1) neck_joint_pos_l2 pulls the neck/head joints to HOME -> remove it.
    if "neck_joint_pos_l2" in cfg.rewards:
        del cfg.rewards["neck_joint_pos_l2"]
    #  2) the pose reward includes neck/head -> scope it to LEG joints only.
    cfg.rewards["pose"].params["asset_cfg"] = SceneEntityCfg(
        "robot", joint_names=(r"^(?!passive_|.*neck.*|.*head.*).*",)
    )
```

- [ ] **Step 5: Add the late head curricula**

Immediately after the block from Step 4 (still before `return cfg`):

```python
    # head_pose_tracking ramps 0 -> 4.0, staying 0 until ~1500 it. (swizzle solid),
    # so head control is added on top of a stable swizzle.
    cfg.curriculum["head_pose_tracking_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "head_pose_tracking",
            "weight_stages": [
                {"step": 0,          "weight": 0.0},   # must match initial weight
                {"step": 1500 * 24,  "weight": 0.0},   # head off while swizzle solidifies
                {"step": 2250 * 24,  "weight": 2.0},
                {"step": 3000 * 24,  "weight": 4.0},
            ],
        },
    )
    # Head-command range widens over the SAME window (tiny until 1500, full by 3000),
    # so the commanded head barely moves early and reaches full range once the policy
    # can handle it.
    cfg.curriculum["head_pose_range"] = CurriculumTermCfg(
        func=microduck_mdp.pose_command_range_curriculum,
        params={
            "command_name": "head_pose",
            "range_stages": [
                # step,               ((neck_pitch), (head_pitch), (head_yaw),  (head_roll))
                {"step": 0,          "ranges": ((-0.05, 0.05), (-0.05, 0.05), (-0.07, 0.07), (-0.015, 0.015))},
                {"step": 1500 * 24,  "ranges": ((-0.05, 0.05), (-0.05, 0.05), (-0.07, 0.07), (-0.015, 0.015))},
                {"step": 2250 * 24,  "ranges": ((-0.55, 0.55), (-0.55, 0.55), (-0.70, 0.70), (-0.15, 0.15))},
                {"step": 3000 * 24,  "ranges": ((-1.10, 1.10), (-1.10, 1.10), (-1.40, 1.40), (-0.31, 0.31))},
            ],
        },
    )
```

- [ ] **Step 6: Run the cfg test to verify it passes**

Run: `MUJOCO_GL=egl uv run pytest tests/test_swizzle_head_cfg.py -v`
Expected: PASS.

- [ ] **Step 7: Smoke test the env end-to-end**

Run: `MUJOCO_GL=egl uv run train Mjlab-Velocity-Swizzle-MicroDuck --env.scene.num-envs 16 --agent.max-iterations 2`
Expected: no error; the reward log lists `head_pose_tracking` and no longer lists `neck_joint_pos_l2`; a `Curriculum/head_pose_tracking_weight` line appears at value 0.0.

- [ ] **Step 8: Commit**

```bash
git add src/mjlab_microduck/tasks/microduck_velocity_swizzle_env_cfg.py tests/test_swizzle_head_cfg.py
git commit -m "swizzle: add head-pose control (Y button, policy-managed, late curriculum)"
```

---

## Notes for the full training run (not part of the task)

Because the head curriculum only finishes at ~3000 iters, train longer than the
2500 used before:

```bash
uv run train Mjlab-Velocity-Swizzle-MicroDuck --env.scene.num-envs 4096 --agent.max-iterations 3500
```

Watch: `head_pose_tracking` rises after ~1500 it.; the swizzle fall rate does NOT
spike when it kicks in. If the head disturbs the swizzle → push the kick-in later /
widen the range more slowly. If the head doesn't follow → raise the final weight.
Deploy unchanged (`--roller --new-cmd-obs`, Y button moves the head).
