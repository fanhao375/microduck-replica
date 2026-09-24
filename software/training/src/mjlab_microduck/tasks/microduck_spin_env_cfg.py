"""Microduck SPIN task — rotation rapide sur place, sur rollers.

Geste cyclique déclenché au bouton A via le slot --ground-pick du runtime :
~1 tour anti-horaire à ~3 rad/s puis arrêt propre debout.

Hybride :
  - physique / robot roller  ← microduck_velocity_rollers_env_cfg.py
  - machinerie phase cyclique ← microduck_roller_crouch_env_cfg.py
    (commande GroundPickPhaseCommand : [cos(2πφ), sin(2πφ), 0], période 4 s)

Différence de fond avec le crouch : la phase pilote une VITESSE DE LACET cible
(objectif de résultat) et non une pose articulaire. Deux amorces décroissantes
poussent vers le roulement différentiel — le seul mécanisme physique certain sur
4 roues passives : patin gauche vers l'arrière, patin droit vers l'avant.

Obs 61D unifié → interchangeable au runtime avec roller / ground_pick / crouch.
Voir docs/superpowers/specs/2026-08-04-spin-env-design.md.
"""

import math
from copy import deepcopy

# La symétrie G/D transformerait un spin à gauche en spin à droite : interdit ici.
ENABLE_SYMMETRY = False

# DR — repris du roller env
ENABLE_COM_RANDOMIZATION             = True
ENABLE_HEAD_COM_RANDOMIZATION        = True
ENABLE_MASS_INERTIA_RANDOMIZATION    = True
ENABLE_JOINT_FRICTION_RANDOMIZATION  = True
ENABLE_ARMATURE_RANDOMIZATION        = True
ENABLE_WHEEL_FRICTION_RANDOMIZATION  = True
ENABLE_VELOCITY_PUSHES               = True
ENABLE_IMU_ORIENTATION_RANDOMIZATION = True
ENABLE_ENCODER_BIAS                  = True

COM_RANDOMIZATION_RANGE          = 0.003
HEAD_COM_RANDOMIZATION_RANGE     = 0.003
MASS_INERTIA_RANDOMIZATION_RANGE = (0.95, 1.05)
JOINT_FRICTION_RANDOMIZATION_RANGE = (0.9, 1.1)
ARMATURE_RANDOMIZATION_RANGE     = (0.9, 1.1)
VELOCITY_PUSH_INTERVAL_S         = (3.0, 6.0)
VELOCITY_PUSH_RANGE              = (-0.2, 0.2)
IMU_ORIENTATION_RANDOMIZATION_ANGLE = 6.0
ENCODER_BIAS_RANGE               = (-0.015, 0.015)

# Le bouton peut être pressé à l'arrêt OU en roulement lent : la policy apprend
# à tuer l'élan résiduel avant/pendant le lancement de la rotation.
ENTRY_VELOCITY_X = (0.0, 0.3)

from mjlab.envs import ManagerBasedRlEnvCfg
from mjlab.envs.mdp import dr
from mjlab.envs.mdp.actions import JointPositionActionCfg
from mjlab.managers import (
    CurriculumTermCfg,
    EventTermCfg,
    ObservationTermCfg,
    RewardTermCfg,
    TerminationTermCfg,
)
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.rl import RslRlOnPolicyRunnerCfg, RslRlModelCfg
from mjlab.sensor import ContactMatch, ContactSensorCfg
from mjlab.tasks.velocity import mdp
from mjlab.tasks.velocity.mdp import UniformVelocityCommandCfg
from mjlab.tasks.velocity.velocity_env_cfg import make_velocity_env_cfg
from mjlab.utils.noise import UniformNoiseCfg as Unoise

from mjlab_microduck.robot.microduck_constants import MICRODUCK_WALK_ROLLERS_ROBOT_CFG
from mjlab_microduck.tasks import mdp as microduck_mdp
from mjlab_microduck.tasks.microduck_velocity_env_cfg import HEAD_BODY_NAMES
from mjlab_microduck.tasks.symmetry import PpoWithSymmetryCfg, SYMMETRY_CFG

# Enveloppe de phase : constantes canoniques définies dans mdp.py.
SPIN_PERIOD = microduck_mdp.SPIN_PERIOD
_ENVELOPE = {
    "rate_max": microduck_mdp.SPIN_RATE_MAX,
    "accel_end": microduck_mdp.SPIN_ACCEL_END,
    "hold_end": microduck_mdp.SPIN_HOLD_END,
    "brake_end": microduck_mdp.SPIN_BRAKE_END,
}
# Nuque/tête tenues près du neutre SAUF head_yaw, laissé libre : il peut servir
# de volant d'inertie pour lancer la rotation.
NECK_PATTERN_NO_YAW = r"^(neck_pitch|head_pitch|head_roll)$"


def make_microduck_spin_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
    """Env spin sur rollers, piloté par la phase du slot ground-pick."""

    feet_ground_cfg = ContactSensorCfg(
        name="feet_ground_contact",
        primary=ContactMatch(
            mode="subtree",
            pattern=r"^(ankle_l_v1|ankle_r_v1)$",
            entity="robot",
        ),
        secondary=ContactMatch(mode="body", pattern="terrain"),
        fields=("found", "force"),
        reduce="netforce",
        num_slots=1,
        track_air_time=True,
    )
    self_collision_cfg = ContactSensorCfg(
        name="self_collision",
        primary=ContactMatch(mode="subtree", pattern="trunk_base", entity="robot"),
        secondary=ContactMatch(mode="subtree", pattern="trunk_base", entity="robot"),
        fields=("found",),
        reduce="none",
        num_slots=1,
    )

    cfg = make_velocity_env_cfg()
    cfg.scene.entities = {"robot": MICRODUCK_WALK_ROLLERS_ROBOT_CFG}
    cfg.scene.sensors = (feet_ground_cfg, self_collision_cfg)
    cfg.viewer.body_name = "trunk_base"

    joint_pos_action = cfg.actions["joint_pos"]
    assert isinstance(joint_pos_action, JointPositionActionCfg)
    joint_pos_action.scale = 1.0

    # === REWARDS ===
    # ⚠️ angular_momentum n'est PAS gardée : elle pénalise la norme 3D du moment
    # angulaire, donc elle combattrait directement le spin. body_ang_vel, elle,
    # ne pénalise que x/y (« Don't penalize z-angular velocity » dans mjlab) →
    # gardée, elle mate le ballant roulis/tangage sans gêner la rotation.
    keep = {"upright", "body_ang_vel", "action_rate_l2"}
    for name in list(cfg.rewards.keys()):
        if name not in keep:
            del cfg.rewards[name]

    cfg.rewards["upright"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["upright"].weight = 2.0
    cfg.rewards["body_ang_vel"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["body_ang_vel"].weight = -0.05
    cfg.rewards["action_rate_l2"].weight = -1.0

    # Objectif principal : suivre la vitesse de lacet cible ω*(φ) (trapèze).
    cfg.rewards["spin_rate_track"] = RewardTermCfg(
        func=microduck_mdp.spin_rate_track,
        weight=6.0,
        params={"command_name": "twist", "std": 1.5, **_ENVELOPE},
    )
    # Bootstrap L1 : gradient constant quand la gaussienne sature loin de la cible.
    cfg.rewards["spin_rate_l1"] = RewardTermCfg(
        func=microduck_mdp.spin_rate_l1,
        weight=0.5,
        params={"command_name": "twist", **_ENVELOPE},
    )
    # Tourner SUR PLACE, et tuer l'élan d'entrée. Renforcé -1.0 -> -3.0 : au run de
    # calibrage à 500 it. le tronc translatait à ~0.35 m/s (~ω·demi-voie), signature
    # d'un pivot sur un seul patin plutôt qu'un spin centré sur le corps — c'est le
    # seul terme qui distingue un spin centré d'un pivot excentré.
    # Atténué pendant la rampe de lancement [0, ACCEL_END) : c'est le moment où le
    # robot doit pousser au sol pour s'injecter du moment angulaire, et où l'élan
    # d'entrée (jusqu'à 0.3 m/s) doit être CONVERTI en rotation — le facturer plein
    # tarif là s'opposerait au lancement. Plein tarif sur régime/freinage/repos.
    cfg.rewards["spin_stay_in_place"] = RewardTermCfg(
        func=microduck_mdp.spin_stay_in_place,
        weight=-3.0,
        params={
            "command_name": "twist",
            "launch_scale": microduck_mdp.SPIN_LAUNCH_DRIFT_SCALE,
            "accel_end": microduck_mdp.SPIN_ACCEL_END,
        },
    )
    # Amorce 1 : tourner EN ROULEMENT (patins en sens opposés), pas en patinage.
    cfg.rewards["spin_wheel_differential"] = RewardTermCfg(
        func=microduck_mdp.spin_wheel_differential,
        weight=1.0,
        params={
            "command_name": "twist",
            "omega_scale": microduck_mdp.SPIN_WHEEL_OMEGA_SCALE,
            **_ENVELOPE,
        },
    )
    # Amorce 2 : ciseau des jambes (décroît par curriculum, voir plus bas).
    cfg.rewards["leg_antisymmetry"] = RewardTermCfg(
        func=microduck_mdp.leg_antisymmetry,
        weight=1.0,
        params={
            "command_name": "twist",
            "joint_bases": ("hip_pitch", "knee"),
            **_ENVELOPE,
        },
    )
    # Les deux lames au sol pendant le spin (pas de vrille en l'air).
    cfg.rewards["spin_grounded"] = RewardTermCfg(
        func=microduck_mdp.spin_grounded,
        weight=0.5,
        params={
            "sensor_name": "feet_ground_contact",
            "command_name": "twist",
            **_ENVELOPE,
        },
    )
    # Stabilité / sim2real
    cfg.rewards["feet_flat"] = RewardTermCfg(
        func=microduck_mdp.feet_flat_penalty,
        weight=-2.0,
        params={
            "asset_cfg": SceneEntityCfg("robot", site_names=("left_foot", "right_foot")),
            "sensor_name": "feet_ground_contact",
        },
    )
    cfg.rewards["self_collisions"] = RewardTermCfg(
        func=mdp.self_collision_cost,
        weight=-1.0,
        params={"sensor_name": "self_collision"},
    )
    cfg.rewards["neck_action_rate_l2"] = RewardTermCfg(
        func=microduck_mdp.neck_action_rate_l2, weight=-0.5
    )
    cfg.rewards["neck_joint_pos_l2"] = RewardTermCfg(
        func=microduck_mdp.neck_joint_pos_l2,
        weight=-0.2,
        params={"pattern": NECK_PATTERN_NO_YAW},
    )
    cfg.rewards["joint_torques_l2"] = RewardTermCfg(
        func=microduck_mdp.joint_torques_l2, weight=-1e-3
    )

    # === TERMINATIONS ===
    cfg.terminations["nan_state"] = TerminationTermCfg(
        func=microduck_mdp.robot_state_is_nan, time_out=False,
    )

    # === EVENTS ===
    cfg.events["reset_action_history"] = EventTermCfg(
        func=microduck_mdp.reset_action_history, mode="reset",
    )
    del cfg.events["foot_friction"]

    if ENABLE_VELOCITY_PUSHES:
        cfg.events["push_robot"] = EventTermCfg(
            func=mdp.push_by_setting_velocity,
            mode="interval",
            interval_range_s=VELOCITY_PUSH_INTERVAL_S,
            params={
                "velocity_range": {"x": VELOCITY_PUSH_RANGE, "y": VELOCITY_PUSH_RANGE},
                "asset_cfg": SceneEntityCfg("robot"),
            },
        )

    cfg.events["reset_base"].params["pose_range"]["z"] = (0.1335, 0.1435)
    # Élan d'entrée : injecté via reset_root_state_uniform (état par défaut PROPRE
    # + range), et NON via push_by_setting_velocity en mode reset, qui additionne à
    # une vitesse racine potentiellement divergente et fait exploser le free-joint
    # de la base -> NaN. Régression connue du roller_crouch.
    cfg.events["reset_base"].params["velocity_range"] = {"x": ENTRY_VELOCITY_X}

    if ENABLE_WHEEL_FRICTION_RANDOMIZATION:
        cfg.events["randomize_wheel_friction"] = EventTermCfg(
            func=dr.dof_frictionloss,
            mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", joint_names=(r"^passive_.*",)),
                "operation": "abs",
                "ranges": (0.000, 0.000),
            },
        )
    if ENABLE_COM_RANDOMIZATION:
        cfg.events["randomize_com"] = EventTermCfg(
            func=dr.body_ipos, mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",)),
                "operation": "add",
                "ranges": (-COM_RANDOMIZATION_RANGE, COM_RANDOMIZATION_RANGE),
            },
        )
    if ENABLE_HEAD_COM_RANDOMIZATION:
        cfg.events["randomize_head_com"] = EventTermCfg(
            func=dr.body_ipos, mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", body_names=HEAD_BODY_NAMES),
                "operation": "add",
                "ranges": (-HEAD_COM_RANDOMIZATION_RANGE, HEAD_COM_RANDOMIZATION_RANGE),
            },
        )
    if ENABLE_MASS_INERTIA_RANDOMIZATION:
        _mi_lo, _mi_hi = MASS_INERTIA_RANDOMIZATION_RANGE
        cfg.events["randomize_mass_inertia"] = EventTermCfg(
            func=dr.pseudo_inertia, mode="startup",
            params={
                "asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",)),
                "alpha_range": (math.log(_mi_lo) / 2.0, math.log(_mi_hi) / 2.0),
            },
        )
    if ENABLE_JOINT_FRICTION_RANDOMIZATION:
        cfg.events["randomize_joint_friction"] = EventTermCfg(
            func=microduck_mdp.randomize_bam_friction, mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot"),
                "scale_range": JOINT_FRICTION_RANDOMIZATION_RANGE,
            },
        )
    if ENABLE_ARMATURE_RANDOMIZATION:
        cfg.events["randomize_armature"] = EventTermCfg(
            func=dr.joint_armature, mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", joint_names=(r"^(?!passive_).*",)),
                "operation": "scale",
                "ranges": ARMATURE_RANDOMIZATION_RANGE,
            },
        )

    # === OBSERVATIONS (unified 61D layout) ===
    del cfg.observations["actor"].terms["base_lin_vel"]
    del cfg.observations["critic"].terms["foot_height"]
    del cfg.observations["actor"].terms["height_scan"]
    del cfg.observations["critic"].terms["height_scan"]
    cfg.observations["critic"].terms["base_lin_vel"] = ObservationTermCfg(
        func=mdp.base_lin_vel, scale=1.0,
    )

    gravity_term_name = "projected_gravity"
    cfg.observations["actor"].terms[gravity_term_name] = deepcopy(
        cfg.observations["actor"].terms[gravity_term_name]
    )
    cfg.observations["actor"].terms["base_ang_vel"] = deepcopy(
        cfg.observations["actor"].terms["base_ang_vel"]
    )
    cfg.observations["actor"].terms["base_ang_vel"].delay_min_lag = 0
    cfg.observations["actor"].terms["base_ang_vel"].delay_max_lag = 1
    cfg.observations["actor"].terms["base_ang_vel"].delay_update_period = 64
    cfg.observations["actor"].terms[gravity_term_name].delay_min_lag = 0
    cfg.observations["actor"].terms[gravity_term_name].delay_max_lag = 1
    cfg.observations["actor"].terms[gravity_term_name].delay_update_period = 64
    cfg.observations["actor"].terms["base_ang_vel"].noise = Unoise(n_min=-0.03, n_max=0.03)
    cfg.observations["actor"].terms[gravity_term_name].noise = Unoise(n_min=-0.01, n_max=0.01)
    cfg.observations["actor"].terms["joint_pos"].noise = Unoise(n_min=-0.001, n_max=0.001)
    cfg.observations["actor"].terms["joint_vel"].noise = Unoise(n_min=-0.25, n_max=0.25)

    if ENABLE_IMU_ORIENTATION_RANDOMIZATION:
        av = cfg.observations["actor"].terms["base_ang_vel"]
        av.func = microduck_mdp.base_ang_vel_imu_misaligned
        av.params = {"max_angle_deg": IMU_ORIENTATION_RANDOMIZATION_ANGLE}
        g = cfg.observations["actor"].terms[gravity_term_name]
        g.func = microduck_mdp.projected_gravity_imu_misaligned
        g.params = {"max_angle_deg": IMU_ORIENTATION_RANDOMIZATION_ANGLE}

    cfg.observations["actor"].terms["joint_vel"] = deepcopy(
        cfg.observations["actor"].terms["joint_vel"]
    )
    cfg.observations["actor"].terms["joint_vel"].delay_min_lag = 1
    cfg.observations["actor"].terms["joint_vel"].delay_max_lag = 1
    cfg.observations["actor"].terms["joint_vel"].delay_update_period = 0

    passive_excluded = SceneEntityCfg("robot", joint_names=(r"^(?!passive_).*",))
    for grp in ("actor", "critic"):
        for term in ("joint_pos", "joint_vel"):
            cfg.observations[grp].terms[term] = deepcopy(cfg.observations[grp].terms[term])
            cfg.observations[grp].terms[term].params["asset_cfg"] = deepcopy(passive_excluded)

    if ENABLE_ENCODER_BIAS:
        cfg.events["encoder_bias"].params["bias_range"] = ENCODER_BIAS_RANGE
        cfg.observations["actor"].terms["joint_pos"].params["biased"] = True
        cfg.observations["critic"].terms["joint_pos"].params["biased"] = False
    else:
        cfg.events.pop("encoder_bias", None)

    wheel_cfg = SceneEntityCfg("robot", joint_names=(r"^passive_.*",))
    cfg.observations["critic"].terms["wheel_vel"] = ObservationTermCfg(
        func=mdp.joint_vel_rel, scale=1.0, params={"asset_cfg": wheel_cfg},
    )

    for group in ("actor", "critic"):
        cfg.observations[group].terms["head_command"] = ObservationTermCfg(
            func=microduck_mdp.zero_command_padding, params={"dim": 4},
        )
        cfg.observations[group].terms["body_command"] = ObservationTermCfg(
            func=microduck_mdp.zero_command_padding, params={"dim": 6},
        )

    # === COMMAND: phase (comme ground_pick / roller_crouch) ===
    command: UniformVelocityCommandCfg = cfg.commands["twist"]
    command.rel_standing_envs = 0.0
    command.rel_heading_envs = 0.0
    # period=4.0 = défaut de --ground-pick-period (rien à passer au runtime) ;
    # randomize_phase=False -> chaque épisode démarre debout à phase 0, comme le
    # bouton au déploiement. Épisode 20 s = 5 cycles complets du geste.
    cfg.commands["twist"] = microduck_mdp.GroundPickPhaseCommandCfg(
        **{
            **vars(command),
            "class_type": microduck_mdp.GroundPickPhaseCommand,
            "period": SPIN_PERIOD,
            "randomize_phase": False,
        }
    )

    cfg.scene.terrain.terrain_type = "plane"
    cfg.scene.terrain.terrain_generator = None

    # === CURRICULUM ===
    del cfg.curriculum["terrain_levels"]
    del cfg.curriculum["command_vel"]
    cfg.curriculum["action_rate_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "action_rate_l2",
            "weight_stages": [
                {"step": 0, "weight": -0.5},
                {"step": 250 * 24, "weight": -0.8},
                {"step": 500 * 24, "weight": -1.0},
            ],
        },
    )
    # L'amorce ciseau s'efface : elle lance le bon mécanisme puis laisse la policy
    # affiner son propre geste (fréquence de pompage libre).
    cfg.curriculum["leg_antisym_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "leg_antisymmetry",
            "weight_stages": [
                {"step": 0, "weight": 1.0},
                {"step": 1500 * 24, "weight": 0.5},
                {"step": 3000 * 24, "weight": 0.25},
            ],
        },
    )
    if ENABLE_COM_RANDOMIZATION:
        cfg.curriculum["com_range"] = CurriculumTermCfg(
            func=microduck_mdp.com_range_curriculum,
            params={
                "event_name": "randomize_com",
                "range_stages": [
                    {"step": 0, "range": 0.003},
                    {"step": 500 * 24, "range": 0.005},
                    {"step": 1000 * 24, "range": 0.01},
                ],
            },
        )
    if ENABLE_HEAD_COM_RANDOMIZATION:
        cfg.curriculum["head_com_range"] = CurriculumTermCfg(
            func=microduck_mdp.com_range_curriculum,
            params={
                "event_name": "randomize_head_com",
                "range_stages": [
                    {"step": 0, "range": 0.003},
                    {"step": 500 * 24, "range": 0.005},
                    {"step": 1000 * 24, "range": 0.01},
                ],
            },
        )

    return cfg


MicroduckSpinRlCfg = RslRlOnPolicyRunnerCfg(
    actor=RslRlModelCfg(
        hidden_dims=(512, 256, 128),
        activation="elu",
        obs_normalization=True,
        distribution_cfg={
            "class_name": "GaussianDistribution",
            "init_std": 1.0,
            "std_type": "scalar",
        },
    ),
    critic=RslRlModelCfg(
        hidden_dims=(512, 256, 128),
        activation="elu",
        obs_normalization=True,
    ),
    algorithm=PpoWithSymmetryCfg(
        value_loss_coef=1.0,
        use_clipped_value_loss=True,
        clip_param=0.2,
        entropy_coef=0.01,
        num_learning_epochs=5,
        num_mini_batches=4,
        learning_rate=1.0e-3,
        schedule="adaptive",
        gamma=0.99,
        lam=0.95,
        desired_kl=0.01,
        max_grad_norm=1.0,
        symmetry_cfg=SYMMETRY_CFG if ENABLE_SYMMETRY else None,
    ),
    wandb_project="mjlab_microduck",
    experiment_name="spin",
    run_name="spin",
    save_interval=250,
    num_steps_per_env=24,
    max_iterations=8_000,
)
