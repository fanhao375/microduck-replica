from mjlab_microduck.tasks.microduck_ground_pick_env_cfg import (
    make_microduck_ground_pick_env_cfg,
)
from mjlab_microduck.tasks.mdp import GroundPickPhaseCommand


def test_ground_pick_cfg_task_space_rewards():
    """Objectif espace-tâche : bouche près du sol (sans toucher) + orientée."""
    cfg = make_microduck_ground_pick_env_cfg()
    r = cfg.rewards
    # proximité bouche->sol (tire vers le bas)
    assert "mouth_ground_proximity" in r
    assert r["mouth_ground_proximity"].weight == 3.0
    assert r["mouth_ground_proximity"].params["target_height"] == 0.0
    # orientation bouche vers le bas
    assert "mouth_perpendicular_to_ground" in r
    assert r["mouth_perpendicular_to_ground"].weight == 2.0
    # no-touch : pénalité de contact forte + seuil bas
    assert "head_impact_penalty" in r
    assert r["head_impact_penalty"].weight == -2.0
    assert r["head_impact_penalty"].params["threshold"] == 1.0
    # pieds au sol ET à plat (anti-bascule sur la cheville)
    assert "feet_grounded" in r and r["feet_grounded"].weight == 3.0
    assert "feet_flat" in r and r["feet_flat"].weight == -2.0
    # retour debout + aide au relever (upright gaté sur la remontée)
    assert "ground_pick_return_pose_legs" in r
    assert "ground_pick_return_pose_neck" in r
    assert "return_upright" in r and r["return_upright"].weight == 4.0
    # plus d'approche par pose interpolée
    assert "phase_pose_track_head" not in r
    assert "phase_pose_track_legs" not in r


def test_ground_pick_mouth_payload_wired():
    cfg = make_microduck_ground_pick_env_cfg()
    # hook d'application (poids 0) + event de tirage du payload
    assert "mouth_payload_force" in cfg.rewards
    assert cfg.rewards["mouth_payload_force"].weight == 0.0
    assert "sample_mouth_payload" in cfg.events
    assert cfg.events["sample_mouth_payload"].params["min_kg"] == 0.01
    assert cfg.events["sample_mouth_payload"].params["max_kg"] == 0.04


def test_ground_pick_cfg_command_is_phase():
    cfg = make_microduck_ground_pick_env_cfg()
    cmd = cfg.commands["twist"]
    assert cmd.class_type is GroundPickPhaseCommand


def test_ground_pick_rough_variant_builds():
    cfg = make_microduck_ground_pick_env_cfg(rough=True)
    assert "mouth_ground_proximity" in cfg.rewards


def test_ground_pick_play_variant_builds():
    cfg = make_microduck_ground_pick_env_cfg(play=True)
    assert "mouth_ground_proximity" in cfg.rewards
