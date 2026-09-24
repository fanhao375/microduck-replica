"""descent_speed_reward : récompense la vitesse d'avance vers le bas de la pente
(monde +x), plafonnée à `cap`, nulle si le robot recule/remonte, NaN-safe.
"""

import torch

from mjlab_microduck.tasks.mdp import descent_speed_reward


class _Data:
    def __init__(self, vx):
        self.root_link_lin_vel_w = torch.tensor(vx, dtype=torch.float32).reshape(-1, 1).repeat(1, 3)
        # seule la colonne 0 (x) est lue ; on met vx en x
        self.root_link_lin_vel_w[:, 0] = torch.tensor(vx, dtype=torch.float32)


class _Asset:
    def __init__(self, data):
        self.data = data


class _Env:
    def __init__(self, vx):
        self._a = _Asset(_Data(vx))
        self.scene = self

    def __getitem__(self, _k):
        return self._a


def test_rewards_forward_speed_up_to_cap():
    out = descent_speed_reward(_Env([0.5]), cap=0.8)
    assert abs(float(out[0]) - 0.5) < 1e-6


def test_caps_high_speed():
    out = descent_speed_reward(_Env([1.5]), cap=0.8)
    assert abs(float(out[0]) - 0.8) < 1e-6


def test_zero_for_backward_or_uphill():
    out = descent_speed_reward(_Env([-0.4]), cap=0.8)
    assert float(out[0]) == 0.0


def test_nan_safe():
    out = descent_speed_reward(_Env([float("nan")]), cap=0.8)
    assert float(out[0]) == 0.0
