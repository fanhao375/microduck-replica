import math
import torch
from mjlab_microduck.tasks import mdp


def test_crouch_height_target_endpoints_are_high():
    # phase 0 (début) et phase ~1 (fin) → hauteur haute (debout)
    phase = torch.tensor([0.0, 0.999])
    t = mdp.crouch_height_target(phase, height_low=0.075, height_high=0.11)
    assert torch.allclose(t, torch.tensor([0.11, 0.11]), atol=2e-3)


def test_crouch_height_target_plateau_is_low():
    # tout le palier [0.375, 0.625] → hauteur basse constante
    phase = torch.tensor([0.375, 0.5, 0.624])
    t = mdp.crouch_height_target(phase, height_low=0.075, height_high=0.11)
    assert torch.allclose(t, torch.full((3,), 0.075), atol=1e-6)


def test_crouch_height_target_descent_midpoint():
    # milieu de la descente (phase = hold_lo/2 = 0.1875) → milieu des deux hauteurs
    phase = torch.tensor([0.1875])
    t = mdp.crouch_height_target(phase, height_low=0.075, height_high=0.11)
    assert torch.allclose(t, torch.tensor([(0.11 + 0.075) / 2]), atol=1e-6)


def test_crouch_height_target_rise_midpoint():
    # milieu de la remontée (phase = 0.8125) → milieu des deux hauteurs
    phase = torch.tensor([0.8125])
    t = mdp.crouch_height_target(phase, height_low=0.075, height_high=0.11)
    assert torch.allclose(t, torch.tensor([(0.11 + 0.075) / 2]), atol=1e-6)


# ── crouch_pose_blend : 4 segments (descente / bas / montée / debout) ─────────
# breakpoints de test : descente [0,0.1), bas [0.1,0.5), montée [0.5,0.6),
# debout [0.6,1.0).
_BLEND = dict(descent_end=0.10, hold_end=0.50, rise_end=0.60)


def test_blend_zero_standing_at_start_and_top_hold():
    phase = torch.tensor([0.0, 0.6, 0.8, 0.999])  # début + palier haut
    b = mdp.crouch_pose_blend(phase, **_BLEND)
    assert torch.allclose(b, torch.zeros(4), atol=1e-6)


def test_blend_one_on_low_hold():
    phase = torch.tensor([0.10, 0.3, 0.499])  # palier bas
    b = mdp.crouch_pose_blend(phase, **_BLEND)
    assert torch.allclose(b, torch.ones(3), atol=1e-6)


def test_blend_descent_and_rise_midpoints():
    # milieu descente (0.05 sur [0,0.1)) → 0.5 ; milieu montée (0.55 sur [0.5,0.6)) → 0.5
    phase = torch.tensor([0.05, 0.55])
    b = mdp.crouch_pose_blend(phase, **_BLEND)
    assert torch.allclose(b, torch.tensor([0.5, 0.5]), atol=1e-6)


def test_reward_is_one_when_height_matches_target():
    # phase 0.5 (plein palier) → cible = height_low ; si com_height == height_low → reward 1
    cmd_cos = torch.tensor([math.cos(2 * math.pi * 0.5)])  # -1
    cmd_sin = torch.tensor([math.sin(2 * math.pi * 0.5)])  # ~0
    com_height = torch.tensor([0.075])
    r = mdp.crouch_glide_reward_from_values(
        com_height, cmd_cos, cmd_sin, height_low=0.075, height_high=0.11, std=0.02
    )
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-3)


def test_reward_decays_when_off_by_one_std():
    # à height_low + std de la cible → exp(-1) ≈ 0.368
    cmd_cos = torch.tensor([math.cos(2 * math.pi * 0.5)])
    cmd_sin = torch.tensor([math.sin(2 * math.pi * 0.5)])
    com_height = torch.tensor([0.075 + 0.02])
    r = mdp.crouch_glide_reward_from_values(
        com_height, cmd_cos, cmd_sin, height_low=0.075, height_high=0.11, std=0.02
    )
    assert torch.allclose(r, torch.tensor([math.exp(-1.0)]), atol=1e-3)


def test_reward_at_phase_zero_expects_high_stance():
    # phase 0 → cible = height_high ; rester debout est récompensé, être accroupi non
    cmd_cos = torch.tensor([1.0, 1.0])   # cos(0)
    cmd_sin = torch.tensor([0.0, 0.0])   # sin(0)
    com_height = torch.tensor([0.11, 0.075])  # debout vs accroupi
    r = mdp.crouch_glide_reward_from_values(
        com_height, cmd_cos, cmd_sin, height_low=0.075, height_high=0.11, std=0.02
    )
    assert r[0] > 0.99          # debout à phase 0 → ~1
    assert r[1] < 0.2           # accroupi à phase 0 → faible
