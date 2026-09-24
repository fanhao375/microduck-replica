from mjlab_microduck.tasks import mdp as microduck_mdp
from mjlab_microduck.tasks.microduck_spin_env_cfg import (
    make_microduck_spin_env_cfg,
    MicroduckSpinRlCfg,
)


def test_cfg_uses_phase_command_with_runtime_default_period():
    cfg = make_microduck_spin_env_cfg()
    cmd = cfg.commands["twist"]
    assert isinstance(cmd, microduck_mdp.GroundPickPhaseCommandCfg)
    # 4.0 s = le défaut de --ground-pick-period : rien à passer au runtime
    assert cmd.period == 4.0
    # chaque épisode démarre à phase 0 (debout), comme le bouton au déploiement
    assert cmd.randomize_phase is False


def test_cfg_has_the_spin_rewards():
    cfg = make_microduck_spin_env_cfg()
    for name in (
        "spin_rate_track",
        "spin_rate_l1",
        "spin_stay_in_place",
        "spin_wheel_differential",
        "spin_grounded",
        "leg_antisymmetry",
    ):
        assert name in cfg.rewards, name
    # objectif principal avec un poids dominant
    assert cfg.rewards["spin_rate_track"].weight == 6.0
    # sur-place est un COÛT
    assert cfg.rewards["spin_stay_in_place"].weight < 0.0


def test_stay_in_place_is_attenuated_during_the_launch_ramp():
    # Renforcé à -3.0, ce terme s'opposerait à l'injection de moment angulaire s'il
    # était plein tarif pendant la rampe de lancement : il doit y être atténué.
    cfg = make_microduck_spin_env_cfg()
    params = cfg.rewards["spin_stay_in_place"].params
    assert 0.0 < params["launch_scale"] < 1.0
    assert params["accel_end"] == microduck_mdp.SPIN_ACCEL_END
    # cible positive = anti-horaire (le sens est porté par l'enveloppe)
    assert microduck_mdp.SPIN_RATE_MAX > 0.0


def test_angular_momentum_reward_is_removed():
    # Régression : angular_momentum_penalty pénalise la NORME 3D du moment
    # angulaire, elle combattrait directement le spin. Elle doit être absente.
    cfg = make_microduck_spin_env_cfg()
    assert "angular_momentum" not in cfg.rewards
    # body_ang_vel ne pénalise que x/y -> elle reste, elle mate le ballant
    assert "body_ang_vel" in cfg.rewards


def test_head_yaw_is_free_to_act_as_a_flywheel():
    cfg = make_microduck_spin_env_cfg()
    pattern = cfg.rewards["neck_joint_pos_l2"].params["pattern"]
    assert "head_yaw" not in pattern


def test_entry_velocity_allows_standstill_and_slow_roll():
    cfg = make_microduck_spin_env_cfg()
    # jamais via un push en mode reset (régression NaN du crouch)
    assert "entry_velocity" not in cfg.events
    lo, hi = cfg.events["reset_base"].params["velocity_range"]["x"]
    assert lo == 0.0 and hi > 0.0


def test_symmetry_augmentation_is_disabled():
    # la symétrie G/D transformerait un spin à gauche en spin à droite
    assert MicroduckSpinRlCfg.algorithm.symmetry_cfg is None


def test_leg_antisymmetry_shaping_decays():
    cfg = make_microduck_spin_env_cfg()
    stages = cfg.curriculum["leg_antisym_weight"].params["weight_stages"]
    weights = [s["weight"] for s in stages]
    assert weights[0] == cfg.rewards["leg_antisymmetry"].weight
    assert weights == sorted(weights, reverse=True)
    assert weights[-1] < weights[0]


def test_actor_observation_keeps_the_61d_slot_layout():
    # condition pour que l'ONNX charge dans le slot du runtime. L'égalité exacte
    # des dimensions avec le crouch est vérifiée par test_obs_parity_with_roller_crouch
    # ci-dessous ; ici on vérifie la structure.
    cfg = make_microduck_spin_env_cfg()
    terms = cfg.observations["actor"].terms
    assert "base_lin_vel" not in terms
    assert "height_scan" not in terms
    for padded in ("head_command", "body_command"):
        assert padded in terms
    assert terms["head_command"].params["dim"] == 4
    assert terms["body_command"].params["dim"] == 6


def test_obs_parity_with_roller_crouch():
    # Parité de layout obligatoire : sinon l'ONNX exporté ne charge pas dans le
    # slot du runtime. Contrairement au test de structure ci-dessus, celui-ci
    # compare l'ordre EXACT des termes, groupe par groupe.
    from mjlab_microduck.tasks.microduck_roller_crouch_env_cfg import (
        make_microduck_roller_crouch_env_cfg,
    )

    spin = make_microduck_spin_env_cfg()
    crouch = make_microduck_roller_crouch_env_cfg()
    for grp in ("actor", "critic"):
        assert list(spin.observations[grp].terms.keys()) == list(
            crouch.observations[grp].terms.keys()
        ), f"layout d'observation divergent sur le groupe {grp}"
