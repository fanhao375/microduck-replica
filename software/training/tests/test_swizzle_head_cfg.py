from mjlab.tasks.velocity import mdp
from mjlab_microduck.tasks.microduck_velocity_swizzle_env_cfg import (
    make_microduck_velocity_swizzle_env_cfg,
)
from mjlab_microduck.tasks.microduck_velocity_rollers_env_cfg import (
    make_microduck_velocity_rollers_env_cfg,
)


def test_swizzle_head_control_wired():
    cfg = make_microduck_velocity_swizzle_env_cfg()
    roller_cfg = make_microduck_velocity_rollers_env_cfg()

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

    # Pose reward function unchanged (not swapped to a different function)
    assert cfg.rewards["pose"].func is roller_cfg.rewards["pose"].func, \
        "pose reward function was swapped (should only scope asset_cfg)"

    # Late head curricula exist.
    assert "head_pose_tracking_weight" in cfg.curriculum
    assert "head_pose_range" in cfg.curriculum
