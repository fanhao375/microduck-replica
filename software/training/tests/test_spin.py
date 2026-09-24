import math

import torch

from mjlab_microduck.tasks import mdp

# Enveloppe du spec : accel 0.5s / régime 1.6s / freinage 0.5s / repos 1.4s sur 4s.
_ENV = dict(rate_max=6.0, accel_end=0.125, hold_end=0.525, brake_end=0.650)


def test_spin_rate_segment_boundaries():
    # bornes des 4 segments : 0 au départ, plein régime sur [accel_end, hold_end],
    # encore plein régime au tout début du freinage, 0 dès le segment de repos.
    phase = torch.tensor([0.0, 0.125, 0.30, 0.525, 0.650, 0.80, 0.999])
    w = mdp.spin_rate_by_phase(phase, **_ENV)
    expected = torch.tensor([0.0, 6.0, 6.0, 6.0, 0.0, 0.0, 0.0])
    assert torch.allclose(w, expected, atol=1e-6)


def test_spin_rate_accel_ramp_is_increasing():
    phase = torch.linspace(0.0, 0.125, 20)
    w = mdp.spin_rate_by_phase(phase, **_ENV)
    assert torch.all(w[1:] >= w[:-1])
    # milieu de la rampe de lancement -> moitié de la cible
    mid = mdp.spin_rate_by_phase(torch.tensor([0.0625]), **_ENV)
    assert torch.allclose(mid, torch.tensor([3.0]), atol=1e-6)


def test_spin_rate_brake_ramp_is_decreasing():
    phase = torch.linspace(0.525, 0.6499, 20)
    w = mdp.spin_rate_by_phase(phase, **_ENV)
    assert torch.all(w[1:] <= w[:-1])
    # milieu du freinage -> moitié de la cible
    mid = mdp.spin_rate_by_phase(torch.tensor([0.5875]), **_ENV)
    assert torch.allclose(mid, torch.tensor([3.0]), atol=1e-6)


def test_spin_rate_integral_matches_trapezoid_shape_at_rate_max_6():
    # Ce test protège la FORME du trapèze (2.1 * rate_max rad par cycle), pas la
    # cible réellement expédiée : à rate_max=6.0 (hypothétique, cf. _ENV ci-dessus)
    # ça vaut ~4*pi rad = 2 tours. Enveloppe exacte = 12.6 rad, 4*pi = 12.566 ->
    # tolérance 1 %. La cible EN VIGUEUR est couverte par le test suivant.
    n = 100_000
    phase = (torch.arange(n, dtype=torch.float64) + 0.5) / n
    w = mdp.spin_rate_by_phase(phase, **_ENV)
    integral = float(w.mean()) * 4.0
    assert abs(integral - 4 * math.pi) / (4 * math.pi) < 0.01


def test_spin_rate_max_integrates_to_2_1_times_itself_per_cycle():
    # LE test qui protège la cible EXPÉDIÉE (mdp.SPIN_RATE_MAX), par opposition au
    # test ci-dessus qui ne teste que la forme à rate_max=6.0. L'aire sous
    # l'enveloppe sur un cycle vaut 2.1 * rate_max rad, quel que soit rate_max
    # (0.25 + 1.6 + 0.25 = 2.1, cf. le commentaire au-dessus des constantes dans
    # mdp.py). Avec le réglage actuel (SPIN_RATE_MAX = 3.0) ça donne 6.3 rad,
    # soit ~1 tour -- pas 2. Ce test échoue bruyamment si quelqu'un change la
    # cible sans réfléchir au nombre de tours que ça implique.
    n = 100_000
    phase = (torch.arange(n, dtype=torch.float64) + 0.5) / n
    w = mdp.spin_rate_by_phase(
        phase,
        rate_max=mdp.SPIN_RATE_MAX,
        accel_end=mdp.SPIN_ACCEL_END,
        hold_end=mdp.SPIN_HOLD_END,
        brake_end=mdp.SPIN_BRAKE_END,
    )
    integral = float(w.mean()) * mdp.SPIN_PERIOD
    expected = 2.1 * mdp.SPIN_RATE_MAX
    assert abs(integral - expected) / expected < 0.01


def test_spin_gate_is_normalized_rate():
    phase = torch.tensor([0.0, 0.0625, 0.30, 0.5875, 0.80])
    gate = mdp.spin_gate_by_phase(phase, **_ENV)
    rate = mdp.spin_rate_by_phase(phase, **_ENV)
    assert torch.allclose(gate, rate / 6.0, atol=1e-6)
    assert torch.all(gate >= 0.0) and torch.all(gate <= 1.0)


def test_spin_gate_is_zero_over_the_whole_rest_segment():
    # pendant le repos aucune amorce ne doit pousser au ciseau -> porte nulle,
    # c'est ce qui donne une sortie de trick propre vers la policy roller.
    phase = torch.linspace(0.650, 0.999, 50)
    gate = mdp.spin_gate_by_phase(phase, **_ENV)
    assert torch.allclose(gate, torch.zeros_like(gate), atol=1e-6)


# ── faux env minimal : permet de tester les wrappers de reward sans MuJoCo ────
class _FakeData:
    def __init__(self, ang_vel_b=None, lin_vel_b=None, joint_pos=None, joint_vel=None):
        self.root_link_ang_vel_b = ang_vel_b
        self.root_link_lin_vel_b = lin_vel_b
        self.joint_pos = joint_pos
        self.joint_vel = joint_vel


class _FakeEntity:
    """Entity minimale : find_joints() résout par nom depuis un dict {nom: index}."""

    def __init__(self, data, joint_ids=None):
        self.data = data
        self._joint_ids = joint_ids or {}

    def find_joints(self, pattern):
        import re

        names = list(self._joint_ids.keys())
        if isinstance(pattern, (list, tuple)):
            matched = [n for n in names if n in pattern]
        else:
            matched = [n for n in names if re.fullmatch(pattern, n)]
        assert matched, f"aucun joint ne matche {pattern!r} parmi {names}"
        return [self._joint_ids[n] for n in matched], matched


class _FakeCommandManager:
    def __init__(self, cmd):
        self._cmd = cmd

    def get_command(self, name):
        return self._cmd


class _FakeSensorData:
    def __init__(self, current_contact_time):
        self.current_contact_time = current_contact_time


class _FakeSensor:
    def __init__(self, current_contact_time):
        self.data = _FakeSensorData(current_contact_time)


class _FakeEnv:
    def __init__(self, entity, cmd=None, sensors=None):
        self.scene = {"robot": entity, **(sensors or {})}
        self.command_manager = _FakeCommandManager(cmd)
        self.device = "cpu"


def _phase_cmd(phases):
    """Commande du slot telle que la voit la policy : [cos(2*pi*phi), sin(...), 0]."""
    p = torch.as_tensor(phases, dtype=torch.float32)
    return torch.stack(
        [torch.cos(2 * math.pi * p), torch.sin(2 * math.pi * p), torch.zeros_like(p)],
        dim=-1,
    )


# ── phase recover ────────────────────────────────────────────────────────────
def test_spin_phase_from_command_roundtrip():
    phases = torch.tensor([0.0, 0.125, 0.4, 0.65, 0.9])
    got = mdp.spin_phase_from_command(_phase_cmd(phases))
    assert torch.allclose(got, phases, atol=1e-5)


# ── spin_rate_track ──────────────────────────────────────────────────────────
def test_spin_rate_reward_peaks_on_exact_match():
    w = torch.tensor([6.0, 6.0])
    target = torch.tensor([6.0, 4.5])
    r = mdp.spin_rate_reward_from_values(w, target, std=1.5)
    # erreur nulle -> 1.0 ; erreur = 1 std -> exp(-1)
    assert torch.allclose(r, torch.tensor([1.0, math.exp(-1.0)]), atol=1e-6)


def test_spin_rate_track_uses_yaw_and_phase():
    # phase 0.30 = plein régime -> cible SPIN_RATE_MAX (3.0 rad/s, défaut appelé
    # ici implicitement). Un robot qui tourne à la cible doit toucher 1.0 ; un
    # robot immobile doit être largement en dessous (exp(-(3/1.5)^2) = 0.018 au
    # réglage courant : std=1.5 reste bien calibré à cette cible, cf. mdp.py).
    ang = torch.tensor([[0.0, 0.0, mdp.SPIN_RATE_MAX], [0.0, 0.0, 0.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(ang_vel_b=ang)), cmd=_phase_cmd([0.30, 0.30])
    )
    r = mdp.spin_rate_track(env, std=1.5)
    assert r[0] > 0.99
    assert r[1] < 0.05


def test_spin_rate_track_wants_stillness_during_rest():
    # phase 0.80 = repos -> cible 0 : tourner encore est puni, être immobile payé.
    ang = torch.tensor([[0.0, 0.0, 0.0], [0.0, 0.0, 6.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(ang_vel_b=ang)), cmd=_phase_cmd([0.80, 0.80])
    )
    r = mdp.spin_rate_track(env, std=1.5)
    assert r[0] > 0.99
    assert r[1] < 0.01


def test_spin_rate_track_penalizes_wrong_direction():
    # tourner à -SPIN_RATE_MAX (horaire) quand on demande +SPIN_RATE_MAX doit
    # être pire qu'immobile.
    ang = torch.tensor([[0.0, 0.0, -mdp.SPIN_RATE_MAX], [0.0, 0.0, 0.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(ang_vel_b=ang)), cmd=_phase_cmd([0.30, 0.30])
    )
    r = mdp.spin_rate_track(env, std=1.5)
    assert r[0] < r[1]


# ── spin_rate_l1 ─────────────────────────────────────────────────────────────
def test_spin_rate_l1_is_negative_absolute_error():
    # phase 0.30 = plein régime -> cible SPIN_RATE_MAX (3.0 rad/s, défaut).
    ang = torch.tensor([[0.0, 0.0, mdp.SPIN_RATE_MAX], [0.0, 0.0, 1.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(ang_vel_b=ang)), cmd=_phase_cmd([0.30, 0.30])
    )
    r = mdp.spin_rate_l1(env)
    expected = torch.tensor([0.0, -(mdp.SPIN_RATE_MAX - 1.0)])
    assert torch.allclose(r, expected, atol=1e-5)


# ── spin_stay_in_place ───────────────────────────────────────────────────────
def test_spin_stay_in_place_is_squared_planar_speed():
    # phase 0.30 = plein régime -> coût plein tarif
    lin = torch.tensor([[0.0, 0.0, 0.0], [0.3, 0.4, 9.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(lin_vel_b=lin)), cmd=_phase_cmd([0.30, 0.30])
    )
    c = mdp.spin_stay_in_place(env)
    # 0.3^2 + 0.4^2 = 0.25 ; la composante z est ignorée
    assert torch.allclose(c, torch.tensor([0.0, 0.25]), atol=1e-6)


def test_spin_stay_in_place_is_attenuated_during_the_launch_ramp():
    # Même vitesse, deux phases : dans la rampe de lancement (0.05 < accel_end) le
    # coût est multiplié par launch_scale, en régime (0.30) il est plein tarif.
    # C'est ce qui empêche ce terme de s'opposer à l'injection de moment angulaire.
    lin = torch.tensor([[0.3, 0.4, 0.0], [0.3, 0.4, 0.0]])
    env = _FakeEnv(
        _FakeEntity(_FakeData(lin_vel_b=lin)), cmd=_phase_cmd([0.05, 0.30])
    )
    c = mdp.spin_stay_in_place(env, launch_scale=0.2, accel_end=0.125)
    # 0.25 * 0.2 = 0.05
    assert torch.allclose(c, torch.tensor([0.05, 0.25]), atol=1e-6)
    assert c[0] < c[1]


def test_spin_stay_in_place_is_full_price_during_rest():
    # Pendant le repos on veut le robot IMMOBILE : ce terme ne doit PAS être éteint,
    # contrairement aux amorces (spin_wheel_differential, spin_grounded, ciseau).
    lin = torch.tensor([[0.3, 0.4, 0.0]])
    env = _FakeEnv(_FakeEntity(_FakeData(lin_vel_b=lin)), cmd=_phase_cmd([0.80]))
    c = mdp.spin_stay_in_place(env)
    assert torch.allclose(c, torch.tensor([0.25]), atol=1e-6)


# ── spin_wheel_differential ──────────────────────────────────────────────────
_WHEEL_IDS = {
    "passive_LF_wheel": 0,
    "passive_LR_wheel": 1,
    "passive_RF_wheel": 2,
    "passive_RR_wheel": 3,
}


def _wheel_env(vel_rows, phases):
    vel = torch.tensor(vel_rows, dtype=torch.float32)
    entity = _FakeEntity(_FakeData(joint_vel=vel), joint_ids=_WHEEL_IDS)
    return _FakeEnv(entity, cmd=_phase_cmd(phases))


def test_wheel_differential_rewards_counter_rolling_wheels():
    # anti-horaire : roues GAUCHE négatives (patin part en arrière), DROITE
    # positives -> omega_D - omega_G > 0 -> récompensé.
    env = _wheel_env(
        [
            [-10.0, -10.0, 10.0, 10.0],  # bon différentiel
            [10.0, 10.0, 10.0, 10.0],    # tout droit : différentiel nul
            [10.0, 10.0, -10.0, -10.0],  # différentiel inversé (horaire)
        ],
        [0.30, 0.30, 0.30],
    )
    r = mdp.spin_wheel_differential(env, omega_scale=20.0)
    assert r[0] > 0.5
    assert torch.allclose(r[1], torch.tensor(0.0), atol=1e-6)
    assert torch.allclose(r[2], torch.tensor(0.0), atol=1e-6)


def test_wheel_differential_is_gated_off_during_rest():
    # même bon différentiel, mais en phase de repos -> porte nulle -> pas payé.
    env = _wheel_env([[-10.0, -10.0, 10.0, 10.0]], [0.80])
    r = mdp.spin_wheel_differential(env, omega_scale=20.0)
    assert torch.allclose(r, torch.zeros(1), atol=1e-6)


def test_wheel_differential_saturates():
    # tanh : au-delà de omega_scale la reward sature, pas de course à la vitesse.
    env = _wheel_env(
        [[-10.0, -10.0, 10.0, 10.0], [-100.0, -100.0, 100.0, 100.0]], [0.30, 0.30]
    )
    r = mdp.spin_wheel_differential(env, omega_scale=20.0)
    assert r[1] > r[0]
    assert r[1] <= 1.0


def test_wheel_differential_from_values_is_pure():
    diff = torch.tensor([20.0, 0.0, -20.0])
    gate = torch.ones(3)
    r = mdp.spin_wheel_differential_from_values(diff, gate, omega_scale=20.0)
    expected = torch.tensor([math.tanh(1.0), 0.0, 0.0])
    assert torch.allclose(r, expected, atol=1e-6)


# ── spin_grounded ────────────────────────────────────────────────────────────
def test_spin_grounded_rewards_both_blades_down_and_is_gated():
    contact = torch.tensor([[0.2, 0.3], [0.2, 0.0], [0.0, 0.0], [0.2, 0.3]])
    entity = _FakeEntity(_FakeData())
    env = _FakeEnv(
        entity,
        cmd=_phase_cmd([0.30, 0.30, 0.30, 0.80]),
        sensors={"feet_ground_contact": _FakeSensor(contact)},
    )
    r = mdp.spin_grounded(env, sensor_name="feet_ground_contact")
    # deux lames au sol en régime -> porte 1.0 ; une seule ou zéro -> 0 ;
    # deux lames au sol mais en repos -> porte 0.
    assert torch.allclose(r, torch.tensor([1.0, 0.0, 0.0, 0.0]), atol=1e-6)


# ── leg_antisymmetry ─────────────────────────────────────────────────────────
_LEG_IDS = {
    "left_hip_pitch": 0,
    "left_knee": 1,
    "right_hip_pitch": 2,
    "right_knee": 3,
}


def _leg_env(pos_rows, phases):
    pos = torch.tensor(pos_rows, dtype=torch.float32)
    entity = _FakeEntity(_FakeData(joint_pos=pos), joint_ids=_LEG_IDS)
    return _FakeEnv(entity, cmd=_phase_cmd(phases))


def test_leg_antisymmetry_prefers_scissor_over_mirror():
    # convention miroir : q_G = -q_D est une pose SYMÉTRIQUE (mauvais ici),
    # q_G = q_D est le CISEAU (bon ici). Valeur = -mean|q_G - q_D|, donc <= 0.
    env = _leg_env(
        [
            [0.4, 0.3, 0.4, 0.3],    # ciseau parfait : q_G == q_D -> 0.0
            [0.4, 0.3, -0.4, -0.3],  # miroir : écart 0.8 et 0.6 -> -0.7
        ],
        [0.30, 0.30],
    )
    r = mdp.leg_antisymmetry(env)
    assert torch.allclose(r, torch.tensor([0.0, -0.7]), atol=1e-6)
    assert r[0] > r[1]


def test_leg_antisymmetry_is_gated_off_during_rest():
    # en repos la porte est nulle : rien ne pousse au ciseau, station neutre libre.
    env = _leg_env([[0.4, 0.3, -0.4, -0.3]], [0.80])
    r = mdp.leg_antisymmetry(env)
    assert torch.allclose(r, torch.zeros(1), atol=1e-6)


# ── neck_joint_pos_l2 : paramètre pattern ────────────────────────────────────
_NECK_IDS = {
    "neck_pitch": 0,
    "head_pitch": 1,
    "head_roll": 2,
    "head_yaw": 3,
}


def test_neck_joint_pos_l2_pattern_can_exclude_head_yaw():
    class _NeckData(_FakeData):
        def __init__(self, joint_pos, default_joint_pos):
            super().__init__(joint_pos=joint_pos)
            self.default_joint_pos = default_joint_pos

    pos = torch.tensor([[0.0, 0.0, 0.0, 1.0]])  # seul head_yaw dévie, de 1 rad
    default = torch.zeros(1, 4)
    entity = _FakeEntity(_NeckData(pos, default), joint_ids=_NECK_IDS)
    env = _FakeEnv(entity)

    # motif par défaut : head_yaw compté -> coût 1.0
    assert torch.allclose(
        mdp.neck_joint_pos_l2(env), torch.tensor([1.0]), atol=1e-6
    )
    # motif du spin : head_yaw exclu -> coût 0.0 (tête libre en lacet)
    assert torch.allclose(
        mdp.neck_joint_pos_l2(env, pattern=r"^(neck_pitch|head_pitch|head_roll)$"),
        torch.tensor([0.0]),
        atol=1e-6,
    )
