import torch
from mjlab_microduck.tasks.mdp import slope_move_masks


def test_move_up_when_reached_bottom():
    # distance > size_x*0.4 (=3.2) → monte en difficulté
    dist = torch.tensor([5.0, 4.1])
    up, down = slope_move_masks(dist, size_x=8.0)
    assert bool(up[0]) and bool(up[1])
    assert not bool(down[0]) and not bool(down[1])


def test_move_down_when_stuck_early():
    # distance < size_x*0.2 (=1.6) → descend en difficulté
    dist = torch.tensor([0.5, 1.0])
    up, down = slope_move_masks(dist, size_x=8.0)
    assert not bool(up[0]) and not bool(up[1])
    assert bool(down[0]) and bool(down[1])


def test_stay_in_middle_band():
    # entre 1.6 et 3.2 → ni haut ni bas
    dist = torch.tensor([2.5])
    up, down = slope_move_masks(dist, size_x=8.0)
    assert not bool(up[0]) and not bool(down[0])


def test_move_up_boundary_at_04():
    # promotion dès qu'on a descendu > 0.4*size_x (le robot a parcouru une bonne
    # partie de la rampe avant d'atteindre le plat de sortie).
    dist = torch.tensor([3.3])
    up, down = slope_move_masks(dist, size_x=8.0)
    assert bool(up[0])
    assert not bool(down[0])

    # 3.0 reste dans la bande médiane (3.0 < 3.2 et 3.0 > 1.6)
    dist_mid = torch.tensor([3.0])
    up_mid, down_mid = slope_move_masks(dist_mid, size_x=8.0)
    assert not bool(up_mid[0]) and not bool(down_mid[0])
