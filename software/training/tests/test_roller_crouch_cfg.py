from mjlab_microduck.tasks.microduck_roller_crouch_env_cfg import (
    make_microduck_roller_crouch_env_cfg,
)
from mjlab_microduck.tasks import mdp as microduck_mdp


def test_cfg_uses_phase_command():
    cfg = make_microduck_roller_crouch_env_cfg()
    cmd = cfg.commands["twist"]
    assert isinstance(cmd, microduck_mdp.GroundPickPhaseCommandCfg)
    # period must match --ground-pick-period at deploy
    # (0.5s down + 2s low + 0.5s up + 2s standing = 5s)
    assert cmd.period == 5.0
    # each episode starts standing (phase 0), matching the runtime trigger
    assert cmd.randomize_phase is False


def test_cfg_has_crouch_and_forward_rewards():
    cfg = make_microduck_roller_crouch_env_cfg()
    # pose-based objective (standing<->crouch) + L1 bootstrap
    assert "crouch_glide_pose" in cfg.rewards
    assert "crouch_glide_pose_l1" in cfg.rewards
    assert "forward_speed" in cfg.rewards
    # léger penché avant pendant l'accroupi (cible positive = vers l'avant)
    assert "crouch_forward_lean" in cfg.rewards
    assert cfg.rewards["crouch_forward_lean"].params["target_pitch"] > 0.0
    # the crouch pose is carried by-name and includes the leg fold
    cp = cfg.rewards["crouch_glide_pose"].params["crouch_pose"]
    assert "left_knee" in cp and "right_knee" in cp
    # rewards de patinage actif retirées (pas de stride pendant le trick)
    for gone in ("braking", "skating_air_time", "single_support", "glide", "wheel_speed"):
        assert gone not in cfg.rewards


def test_entry_velocity_applied_safely_via_reset_base():
    # Regression: entry momentum must be injected through reset_base's
    # velocity_range (reset_root_state_uniform sets it from the clean default
    # state), NOT via a mode="reset" push_by_setting_velocity event, which adds
    # to the current (possibly divergent) root velocity and blows the base
    # free-joint up to NaN. See the env cfg comment on ENTRY_VELOCITY_X.
    cfg = make_microduck_roller_crouch_env_cfg()
    # the buggy reset-push event must NOT exist
    assert "entry_velocity" not in cfg.events
    # forward entry velocity must be carried by reset_base, with a positive range
    vr = cfg.events["reset_base"].params.get("velocity_range")
    assert vr and "x" in vr
    lo, hi = vr["x"]
    assert lo > 0.0 and hi >= lo
