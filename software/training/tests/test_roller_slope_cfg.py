from mjlab_microduck.tasks.microduck_roller_slope_env_cfg import (
    make_microduck_roller_slope_env_cfg,
)
from mjlab_microduck.tasks.slope_terrain import FlatRampTerrainCfg


def test_terrain_is_flat_ramp_generator():
    cfg = make_microduck_roller_slope_env_cfg()
    assert cfg.scene.terrain.terrain_type == "generator"
    gen = cfg.scene.terrain.terrain_generator
    assert gen is not None and gen.curriculum is True
    assert any(isinstance(st, FlatRampTerrainCfg) for st in gen.sub_terrains.values())


def test_command_is_neutralised():
    cfg = make_microduck_roller_slope_env_cfg()
    cmd = cfg.commands["twist"]
    assert cmd.rel_standing_envs == 1.0
    assert cmd.rel_heading_envs == 0.0
    assert cmd.ranges.lin_vel_x == (0.0, 0.0)
    assert cmd.ranges.lin_vel_y == (0.0, 0.0)
    if getattr(cmd.ranges, "ang_vel_z", None) is not None:
        assert cmd.ranges.ang_vel_z == (0.0, 0.0)


def test_rolling_entry_no_base_push():
    # élan donné en ROULEMENT (reset_rolling_entry), pas en poussée de base
    # (base seule + roues immobiles = à-coup de patinage). Donc reset_base ne
    # met aucune vitesse de base.
    cfg = make_microduck_roller_slope_env_cfg()
    assert cfg.events["reset_base"].params["velocity_range"] == {}
    assert "reset_rolling_entry" in cfg.events
    lo, hi = cfg.events["reset_rolling_entry"].params["speed_range"]
    assert 0.0 < lo <= hi <= 0.6


def test_has_heading_hold_reward():
    # aller droit : maintien du yaw de spawn
    cfg = make_microduck_roller_slope_env_cfg()
    assert "heading_hold" in cfg.rewards
    assert cfg.rewards["heading_hold"].weight > 0.0


def test_balance_rewards_no_fixed_pose():
    # équilibre libre : upright/alive/glisse présents, mais PAS de pose fixe
    # imposée (il doit pouvoir bouger son centre de gravité pour tenir la pente).
    cfg = make_microduck_roller_slope_env_cfg()
    for name in ("upright", "alive", "feet_flat", "wheel_glide", "neck_joint_pos_l2"):
        assert name in cfg.rewards
    assert "standing_pose" not in cfg.rewards
    assert "standing_pose_l1" not in cfg.rewards


def test_has_wheel_glide_reward_not_base_speed():
    # "se laisser glisser" = rouler (roues), pas récompenser la vitesse de base
    # (qu'il atteignait en courant). wheel_glide présent, descent_speed absent.
    cfg = make_microduck_roller_slope_env_cfg()
    assert "wheel_glide" in cfg.rewards
    assert cfg.rewards["wheel_glide"].weight > 0.0
    assert "descent_speed" not in cfg.rewards


def test_no_roller_skating_rewards_survive():
    # les rewards de PATINAGE du roller ne doivent pas survivre (heading_hold est
    # ré-ajouté volontairement pour aller droit, donc pas dans cette liste).
    cfg = make_microduck_roller_slope_env_cfg()
    for name in ("wheel_speed", "braking", "skating_air_time", "glide", "forward_lean"):
        assert name not in cfg.rewards


def test_spawn_yaw_faces_downhill():
    # yaw fixe à 0 : toujours face au bas de la pente (+x), pas le -pi/+pi hérité
    cfg = make_microduck_roller_slope_env_cfg()
    assert cfg.events["reset_base"].params["pose_range"]["yaw"] == (0.0, 0.0)


def test_void_termination_present_no_edge_termination():
    cfg = make_microduck_roller_slope_env_cfg()
    assert "fell_into_void" in cfg.terminations
    assert "fell_over" in cfg.terminations
    # plus de terminaison « bord de terrain » (remplacée par le plat de sortie)
    assert "reached_bottom" not in cfg.terminations
    assert "out_of_terrain_bounds" not in cfg.terminations


def test_obs_nan_policy_sanitize():
    # obs assainies : un NaN de contact rare ne doit pas tuer l'entraînement
    cfg = make_microduck_roller_slope_env_cfg()
    assert cfg.observations["actor"].nan_policy == "sanitize"
    assert cfg.observations["critic"].nan_policy == "sanitize"


def test_curriculum_present_and_starts_gentle():
    # curriculum doux->raide : démarre sur la rampe la plus douce, promotion active
    cfg = make_microduck_roller_slope_env_cfg()  # play=False (entraînement)
    assert "terrain_levels" in cfg.curriculum
    assert cfg.scene.terrain.max_init_terrain_level == 0


def test_terrain_tile_fits_geometry():
    # la tuile doit contenir plat + rampe_max + sortie
    cfg = make_microduck_roller_slope_env_cfg()
    gen = cfg.scene.terrain.terrain_generator
    st = next(iter(gen.sub_terrains.values()))
    assert st.flat_length + st.ramp_length_range[1] + st.runout_length <= gen.size[0]
