# Roller Crouch-Glide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un geste « s'accroupir en glissant puis se relever » déclenché au bouton A, sans modifier le runtime Rust, en entraînant une policy mjlab chargée dans le slot `--ground-pick`.

**Architecture:** Nouvelle tâche mjlab entraînée sur le robot rollers, pilotée par la commande de phase `GroundPickPhaseCommand` (celle qu'envoie le slot ground-pick du runtime). Une nouvelle reward suit une cible de hauteur du tronc « en trapèze » (haut → bas → palier 1 s → haut) le long de la phase. Le même layout d'obs 61D que la policy roller → interchangeable au runtime. Export ONNX, chargé via `--ground-pick`.

**Tech Stack:** Python, PyTorch, mjlab 1.3.0, MuJoCo, uv, ONNX. Runtime cible : `apirrone/microduck_runtime` (Rust, binaire — NON modifié).

## Global Constraints

- **Aucune modification du runtime Rust.** Le geste réutilise le slot `--ground-pick` existant (bouton A, one-shot).
- **Layout d'obs unifié 61D** obligatoire (`--new-cmd-obs`) : `[twist(3), head(4), body(6)]`, head/body zero-paddés. Toute nouvelle policy DOIT conserver ce layout.
- **14 joints actifs** (roues passives exclues via `SceneEntityCfg("robot", joint_names=(r"^(?!passive_).*",))`), `action.scale = 1.0`, `kp_fw = 200`.
- **Parité entraînement/déploiement (sim2real) :** au déploiement, forcer `--ground-pick-kp-ratio 1.0` (défaut 0.6), `--ground-pick-action-scale` = action_scale runtime, `--ground-pick-period 5.0`.
- **Phase encoding (imposé par le runtime) :** `command = [cos(2π·φ), sin(2π·φ), 0]`, période 4 s. Palier de glisse = 1 s → `hold_lo=0.375`, `hold_hi=0.625`.
- **Commits simples** (pas de `Co-Authored-By`).
- Lancer les tests via `uv run --with pytest pytest` (pas de dépendance pytest ajoutée au projet).
- Spec de référence : `docs/superpowers/specs/2026-07-17-roller-crouch-glide-design.md`.

---

## File Structure

| Fichier | Responsabilité |
|---|---|
| `src/mjlab_microduck/tasks/mdp.py` | **Modifier.** Ajouter 3 fonctions : `crouch_height_target` (pure), `crouch_glide_reward_from_values` (pure), `crouch_glide_height_by_phase` (wrapper env) et `forward_speed_reward`. |
| `tests/test_crouch_glide.py` | **Créer.** Tests unitaires des fonctions pures. |
| `src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py` | **Créer.** L'env (hybride roller + phase) + `MicroduckRollerCrouchRlCfg`. |
| `src/mjlab_microduck/tasks/__init__.py` | **Modifier.** Importer + enregistrer `Mjlab-RollerCrouch-Flat-MicroDuck`. |
| `tests/test_roller_crouch_cfg.py` | **Créer.** Smoke test : l'env se construit avec la bonne commande/rewards. |

---

## Task 1: Cible de hauteur « en trapèze » (fonction pure)

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (ajouter la fonction, après `com_height_target` vers la ligne 737)
- Test: `tests/test_crouch_glide.py`

**Interfaces:**
- Produces: `crouch_height_target(phase: torch.Tensor, height_low: float, height_high: float, hold_lo: float = 0.375, hold_hi: float = 0.625) -> torch.Tensor` — prend la phase (B,) ∈ [0,1) et retourne la hauteur-cible (B,).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_crouch_glide.py` :

```python
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
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run --with pytest pytest tests/test_crouch_glide.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'crouch_height_target'`

- [ ] **Step 3: Implémenter la fonction**

Dans `src/mjlab_microduck/tasks/mdp.py`, juste après `com_height_target` (après la ligne 737) :

```python
def crouch_height_target(
    phase: torch.Tensor,
    height_low: float,
    height_high: float,
    hold_lo: float = 0.375,
    hold_hi: float = 0.625,
) -> torch.Tensor:
    """Cible de hauteur du tronc « en trapèze » le long de la phase [0,1).

    phase ∈ [0, hold_lo)      : descente   height_high -> height_low
    phase ∈ [hold_lo, hold_hi): palier      height_low   (la glisse accroupie)
    phase ∈ [hold_hi, 1.0)    : remontée    height_low  -> height_high

    Args:
        phase: (B,) phase par env, dans [0, 1).
        height_low: hauteur du tronc accroupi (m).
        height_high: hauteur du tronc debout (m).
        hold_lo, hold_hi: bornes du palier bas en fraction de phase.
    Returns:
        (B,) hauteur-cible en mètres.
    """
    descend = phase < hold_lo
    hold = (phase >= hold_lo) & (phase < hold_hi)

    frac_d = phase / hold_lo
    t_descend = height_high + (height_low - height_high) * frac_d

    t_hold = torch.full_like(phase, height_low)

    frac_r = (phase - hold_hi) / (1.0 - hold_hi)
    t_rise = height_low + (height_high - height_low) * frac_r

    return torch.where(descend, t_descend, torch.where(hold, t_hold, t_rise))
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `uv run --with pytest pytest tests/test_crouch_glide.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_crouch_glide.py
git commit -m "roller-crouch: cible de hauteur en trapezoide (fonction pure + tests)"
```

---

## Task 2: Rewards crouch-glide et forward-speed

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py`
- Test: `tests/test_crouch_glide.py` (ajouts)

**Interfaces:**
- Consumes: `crouch_height_target` (Task 1).
- Produces:
  - `crouch_glide_reward_from_values(com_height, cmd_cos, cmd_sin, height_low, height_high, hold_lo=0.375, hold_hi=0.625, std=0.02) -> torch.Tensor` (pure).
  - `crouch_glide_height_by_phase(env, command_name="twist", height_low=0.075, height_high=0.11, hold_lo=0.375, hold_hi=0.625, std=0.02, asset_cfg=_DEFAULT_ASSET_CFG) -> torch.Tensor` (wrapper env).
  - `forward_speed_reward(env, vel_ref=0.2, asset_cfg=_DEFAULT_ASSET_CFG) -> torch.Tensor` — récompense la vitesse avant (élan), indépendante de la commande.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_crouch_glide.py` :

```python
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
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_crouch_glide.py -v`
Expected: FAIL — `crouch_glide_reward_from_values` n'existe pas.

- [ ] **Step 3: Implémenter les trois fonctions**

Dans `src/mjlab_microduck/tasks/mdp.py`, à la suite de `crouch_height_target` :

```python
def crouch_glide_reward_from_values(
    com_height: torch.Tensor,
    cmd_cos: torch.Tensor,
    cmd_sin: torch.Tensor,
    height_low: float,
    height_high: float,
    hold_lo: float = 0.375,
    hold_hi: float = 0.625,
    std: float = 0.02,
) -> torch.Tensor:
    """Récompense gaussienne du suivi de la cible de hauteur (fonction pure).

    Décode la phase depuis [cos, sin] puis compare la hauteur mesurée à la
    cible-trapèze. Retourne exp(-((h - cible)/std)^2) ∈ (0, 1].
    """
    phase = (torch.atan2(cmd_sin, cmd_cos) / (2 * torch.pi)) % 1.0
    target = crouch_height_target(phase, height_low, height_high, hold_lo, hold_hi)
    return torch.exp(-((com_height - target) / std) ** 2)


def crouch_glide_height_by_phase(
    env: ManagerBasedRlEnv,
    command_name: str = "twist",
    height_low: float = 0.075,
    height_high: float = 0.11,
    hold_lo: float = 0.375,
    hold_hi: float = 0.625,
    std: float = 0.02,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Reward principale : suit la cible de hauteur du tronc le long de la phase.

    La hauteur du CoM est calculée comme dans `com_height_target` (world z moins
    l'origine du terrain, nan->0). La phase provient de la commande GroundPick.
    """
    asset: Entity = env.scene[asset_cfg.name]
    com_height = torch.nan_to_num(
        asset.data.root_link_pos_w[:, 2] - env.scene.terrain.env_origins[:, 2], nan=0.0
    )
    cmd = env.command_manager.get_command(command_name)
    return crouch_glide_reward_from_values(
        com_height, cmd[:, 0], cmd[:, 1],
        height_low, height_high, hold_lo, hold_hi, std,
    )


def forward_speed_reward(
    env: ManagerBasedRlEnv,
    vel_ref: float = 0.2,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Récompense la vitesse avant du tronc (conserver l'élan / ne pas freiner).

    Indépendante de la commande (la commande porte la phase, pas la vitesse).
    tanh(clamp(vx, 0)/vel_ref) → sature à ~1, ne récompense jamais reculer.
    """
    asset: Entity = env.scene[asset_cfg.name]
    vx = asset.data.root_link_lin_vel_b[:, 0]
    return torch.tanh(torch.clamp(vx, min=0.0) / vel_ref)
```

- [ ] **Step 4: Vérifier le passage**

Run: `uv run --with pytest pytest tests/test_crouch_glide.py -v`
Expected: PASS (7 tests au total)

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_crouch_glide.py
git commit -m "roller-crouch: rewards crouch-glide-height et forward-speed"
```

---

## Task 3: L'environnement + enregistrement de la tâche

**Files:**
- Create: `src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py`
- Modify: `src/mjlab_microduck/tasks/__init__.py`
- Test: `tests/test_roller_crouch_cfg.py`

**Interfaces:**
- Consumes: `crouch_glide_height_by_phase`, `forward_speed_reward`, `ground_pick_return_pose` (Task 2 + existant), `GroundPickPhaseCommandCfg`, `GroundPickPhaseCommand`, `MICRODUCK_WALK_ROLLERS_ROBOT_CFG`.
- Produces: `make_microduck_roller_crouch_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg`, `MicroduckRollerCrouchRlCfg`, tâche `Mjlab-RollerCrouch-Flat-MicroDuck`.

- [ ] **Step 1: Écrire le smoke test qui échoue**

Créer `tests/test_roller_crouch_cfg.py` :

```python
from mjlab_microduck.tasks.microduck_roller_crouch_env_cfg import (
    make_microduck_roller_crouch_env_cfg,
)
from mjlab_microduck.tasks import mdp as microduck_mdp


def test_cfg_uses_phase_command():
    cfg = make_microduck_roller_crouch_env_cfg()
    assert isinstance(
        cfg.commands["twist"], microduck_mdp.GroundPickPhaseCommandCfg
    )
    assert cfg.commands["twist"].period == 4.0


def test_cfg_has_crouch_and_forward_rewards():
    cfg = make_microduck_roller_crouch_env_cfg()
    assert "crouch_glide_height" in cfg.rewards
    assert "forward_speed" in cfg.rewards
    # rewards de patinage actif retirées (pas de stride pendant le trick)
    for gone in ("braking", "skating_air_time", "single_support", "glide", "wheel_speed"):
        assert gone not in cfg.rewards


def test_cfg_has_entry_velocity_event():
    cfg = make_microduck_roller_crouch_env_cfg()
    assert "entry_velocity" in cfg.events
```

- [ ] **Step 2: Vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_roller_crouch_cfg.py -v`
Expected: FAIL — `ModuleNotFoundError: ...microduck_roller_crouch_env_cfg`

- [ ] **Step 3: Créer le fichier d'environnement**

Créer `src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py` :

```python
"""Microduck roller crouch-glide task.

Geste one-shot déclenché au bouton A via le slot --ground-pick du runtime :
le robot s'accroupit et glisse sur son élan (palier ~1 s), puis se relève et
rend la main à la policy roller.

Hybride :
  - physique / robot roller  ← microduck_velocity_rollers_env_cfg.py
  - machinerie phase one-shot ← microduck_ground_pick_env_cfg.py
    (commande GroundPickPhaseCommand : [cos(2πφ), sin(2πφ), 0], période 4 s)

Cible de hauteur « en trapèze » (haut→bas→palier 1 s→haut) via
crouch_glide_height_by_phase. Obs 61D unifié → interchangeable au runtime.
"""

import math
from copy import deepcopy

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

# Geste : hauteurs cibles (m) et vitesse d'entrée (élan)
CROUCH_HEIGHT_HIGH = 0.11    # tronc debout
CROUCH_HEIGHT_LOW  = 0.075   # tronc accroupi (à affiner en play)
CROUCH_STD         = 0.02
ENTRY_VELOCITY_X   = (0.2, 0.5)  # m/s : le robot arrive en roulant

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


def make_microduck_roller_crouch_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
    """Env crouch-glide sur rollers, piloté par la phase du slot ground-pick."""

    feet_ground_cfg = ContactSensorCfg(
        name="feet_ground_contact",
        primary=ContactMatch(
            mode="subtree",
            pattern=r"^(roller_blade|roller_blade_2)$",
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
    keep = {"upright", "body_ang_vel", "angular_momentum", "action_rate_l2"}
    for name in list(cfg.rewards.keys()):
        if name not in keep:
            del cfg.rewards[name]

    cfg.rewards["upright"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["upright"].weight = 2.0
    cfg.rewards["body_ang_vel"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["body_ang_vel"].weight = -0.05
    cfg.rewards["angular_momentum"].weight = -0.02
    cfg.rewards["action_rate_l2"].weight = -1.0

    # Reward principale : cible de hauteur trapèze le long de la phase
    cfg.rewards["crouch_glide_height"] = RewardTermCfg(
        func=microduck_mdp.crouch_glide_height_by_phase,
        weight=4.0,
        params={
            "command_name": "twist",
            "height_low": CROUCH_HEIGHT_LOW,
            "height_high": CROUCH_HEIGHT_HIGH,
            "hold_lo": 0.375,
            "hold_hi": 0.625,
            "std": CROUCH_STD,
        },
    )
    # Conserver l'élan (ne pas freiner) — indépendant de la commande
    cfg.rewards["forward_speed"] = RewardTermCfg(
        func=microduck_mdp.forward_speed_reward,
        weight=2.0,
        params={"vel_ref": 0.2},
    )
    # Fin de phase : converger vers la pose roller debout pour rendre la main proprement
    _LEG_JOINTS = [0, 1, 2, 3, 4, 9, 10, 11, 12, 13]
    _NECK_JOINTS = [5, 6, 7, 8]
    cfg.rewards["return_pose_legs"] = RewardTermCfg(
        func=microduck_mdp.ground_pick_return_pose,
        weight=3.0,
        params={"std": 0.3, "command_name": "twist", "joint_indices": _LEG_JOINTS},
    )
    cfg.rewards["return_pose_neck"] = RewardTermCfg(
        func=microduck_mdp.ground_pick_return_pose,
        weight=3.0,
        params={"std": 0.15, "command_name": "twist", "joint_indices": _NECK_JOINTS},
    )
    # Stabilité de glisse
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

    # Vitesse d'entrée : le robot démarre en roulant vers l'avant (élan à conserver)
    cfg.events["entry_velocity"] = EventTermCfg(
        func=mdp.push_by_setting_velocity,
        mode="reset",
        params={
            "velocity_range": {"x": ENTRY_VELOCITY_X, "y": (0.0, 0.0)},
            "asset_cfg": SceneEntityCfg("robot"),
        },
    )

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

    # === COMMAND: phase (comme ground_pick) ===
    command: UniformVelocityCommandCfg = cfg.commands["twist"]
    command.rel_standing_envs = 0.0
    command.rel_heading_envs = 0.0
    cfg.commands["twist"] = microduck_mdp.GroundPickPhaseCommandCfg(
        **{**vars(command), "class_type": microduck_mdp.GroundPickPhaseCommand, "period": 4.0}
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


MicroduckRollerCrouchRlCfg = RslRlOnPolicyRunnerCfg(
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
    experiment_name="roller_crouch",
    run_name="roller_crouch",
    save_interval=250,
    num_steps_per_env=24,
    max_iterations=8_000,
)
```

- [ ] **Step 4: Enregistrer la tâche**

Dans `src/mjlab_microduck/tasks/__init__.py`, ajouter l'import après le bloc rollers (après la ligne 54) :

```python
from .microduck_roller_crouch_env_cfg import (
    make_microduck_roller_crouch_env_cfg,
    MicroduckRollerCrouchRlCfg,
)
```

et l'enregistrement après le bloc rollers (après la ligne 175) :

```python
register_mjlab_task(
    task_id="Mjlab-RollerCrouch-Flat-MicroDuck",
    env_cfg=make_microduck_roller_crouch_env_cfg(),
    play_env_cfg=make_microduck_roller_crouch_env_cfg(play=True),
    rl_cfg=MicroduckRollerCrouchRlCfg,
    runner_cls=MicroduckOnPolicyRunner,
)
print("✓ RollerCrouch task registered: Mjlab-RollerCrouch-Flat-MicroDuck")
```

- [ ] **Step 5: Vérifier le passage du smoke test**

Run: `uv run --with pytest pytest tests/test_roller_crouch_cfg.py -v`
Expected: PASS (3 tests). (Ce test construit l'env — il compile le spec MuJoCo, donc il est plus lent ; c'est normal.)

- [ ] **Step 6: Vérifier que la tâche est bien enregistrée**

Run: `uv run python -c "import mjlab_microduck.tasks"`
Expected: la ligne `✓ RollerCrouch task registered: Mjlab-RollerCrouch-Flat-MicroDuck` s'affiche sans erreur.

- [ ] **Step 7: Commit**

```bash
git add src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py \
        src/mjlab_microduck/tasks/__init__.py tests/test_roller_crouch_cfg.py
git commit -m "roller-crouch: env crouch-glide + enregistrement de la tache"
```

---

## Task 4: Smoke run d'entraînement (vérification runtime)

**Files:** aucun (vérification observationnelle).

**Interfaces:**
- Consumes: la tâche `Mjlab-RollerCrouch-Flat-MicroDuck` (Task 3).

- [ ] **Step 1: Lancer un entraînement très court**

Run:
```bash
uv run train Mjlab-RollerCrouch-Flat-MicroDuck \
  --env.scene.num-envs 64 --agent.max_iterations 5
```
Expected: l'entraînement démarre, log les rewards (dont `crouch_glide_height`, `forward_speed`), 5 itérations sans crash, un checkpoint est écrit.

- [ ] **Step 2: Vérifier l'absence d'erreur de forme d'obs**

Inspecter le log de démarrage : l'obs actor doit être **61D** (comme les autres policies de la famille). Si la dim diffère, le padding head/body ou l'exclusion des roues est mal câblé — corriger avant de continuer.

- [ ] **Step 3: Commit (si un fichier de conf a dû être ajusté)**

```bash
git add -A && git commit -m "roller-crouch: ajustement post smoke-run"
```
(S'il n'y a rien à committer, sauter cette étape.)

---

## Task 5: Entraînement complet + vérification en play

**Files:** itérations possibles sur `microduck_roller_crouch_env_cfg.py` (poids de reward, `CROUCH_HEIGHT_LOW`).

- [ ] **Step 1: Lancer l'entraînement complet**

Run:
```bash
uv run train Mjlab-RollerCrouch-Flat-MicroDuck \
  --env.scene.num-envs 4096 --agent.max_iterations 8000
```

- [ ] **Step 2: Visualiser en play**

Run: `uv run scripts/play_latest.py` (ou l'entrée play du projet pour cette tâche).
Observer le cycle : le robot **descend**, **glisse ~1 s** avec les roues qui continuent de tourner (il ne freine pas), puis **se relève** et la pose finale rejoint la pose roller debout. Il ne doit pas tomber.

- [ ] **Step 3: Itérer si nécessaire**

Réglages typiques (dans `microduck_roller_crouch_env_cfg.py`) :
- Il ne descend pas assez → baisser `CROUCH_HEIGHT_LOW` (ex. 0.07) et/ou monter le poids de `crouch_glide_height`.
- Il freine pendant l'accroupi → monter le poids de `forward_speed`.
- Il tombe en position basse → monter `upright`, baisser la vitesse d'entrée `ENTRY_VELOCITY_X`, ou raccourcir le palier (rapprocher `hold_lo`/`hold_hi`).
- La remontée est brutale → monter `return_pose_*` et/ou `action_rate_l2`.

Après chaque changement, relancer un entraînement et re-visualiser. Committer chaque réglage retenu :
```bash
git add src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py
git commit -m "roller-crouch: reglage <ce qui a change>"
```

---

## Task 6: Export ONNX + déploiement sur le robot

**Files:** aucun (manuel / matériel).

- [ ] **Step 1: Exporter la policy en ONNX**

Run: `uv run scripts/export_latest.py` (le normaliseur d'obs est baké dans le graphe par `scripts/export.py`).
Récupérer le fichier `.onnx`, le renommer `roller_crouch.onnx`, le copier sur le robot (ex. `~/microduck/policies/roller_crouch.onnx`).

- [ ] **Step 2: Lancer le runtime avec le slot ground-pick**

Sur le robot :
```bash
microduck_runtime --variant pre-alpha --new-cmd-obs --roller \
  --model output.onnx \
  --new-dxl-imu --kp 200 --action-scale 0.8 \
  --max-linear-vel 0.6 --max-linear-vel-backward 0.5 --max-angular-vel 0.0 \
  --ground-pick ~/microduck/policies/roller_crouch.onnx \
  --ground-pick-period 5.0 \
  --ground-pick-kp-ratio 1.0 \
  --ground-pick-action-scale 0.8
```

**Paramètres critiques (parité sim2real) :**
- `--ground-pick-kp-ratio 1.0` — le défaut 0.6 baisserait kp à 120 alors qu'on entraîne à 200.
- `--ground-pick-action-scale 0.8` — doit matcher l'`action_scale` d'entraînement.
- `--ground-pick-period 5.0` — doit matcher la période entraînée.

- [ ] **Step 3: Tester le geste**

Lancer le robot à petite vitesse en avant, appuyer sur **A**. Vérifier : il s'accroupit, glisse ~1 s, se relève, et la policy roller reprend la main proprement. Si instable, revenir à la Task 5 (itérer sur les poids / la hauteur / la vitesse d'entrée).

---

## Notes de vérification (self-review)

- **Couverture spec :** cible trapèze 1 s (Task 1) ; rewards crouch + anti-freinage + return-pose (Task 2/3) ; robot rollers + phase + obs 61D + DR (Task 3) ; vitesse d'entrée (Task 3, event `entry_velocity`) ; flags de déploiement dont le piège `kp-ratio` (Task 6). ✅
- **Piège phase vs vitesse :** `wheel_speed_reward`/`braking`/`coasting_reward` du roller env utilisent `command[:,0]` comme *vitesse* — invalide ici où `command[:,0]=cos(2πφ)`. Elles sont donc **retirées** et remplacées par `forward_speed_reward` (indépendante de la commande). Testé par `test_cfg_has_crouch_and_forward_rewards`.
- **Cohérence des noms :** `crouch_glide_height` (clé reward) vs `crouch_glide_height_by_phase` (fonction) — voulu : la clé est le nom du terme, la fonction est `func=`.
```
