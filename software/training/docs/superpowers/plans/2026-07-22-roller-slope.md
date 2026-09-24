# Mode pente `roller_slope` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entraîner une politique dédiée où microduck (rollers) démarre sur du plat avec une impulsion, roule sur une rampe descendante, et se laisse glisser jusqu'en bas en restant debout — sans aucun pilotage.

**Architecture:** Nouvelle tâche isolée clonée de `velocity_rollers` (même robot, même obs 61D → interchangeable au runtime). Terrain custom « plat + rampe » à angle interpolé par difficulté, curriculum de raideur maison, commande neutralisée, récompenses d'équilibre + posture debout nominale. Bouton `Y` de bascule dans `infer_policy.py`.

**Tech Stack:** Python, mjlab 1.3.x, MuJoCo (MjSpec terrains), rsl_rl (PPO), PyTorch, onnxruntime (déploiement), pytest.

## Global Constraints

- **Observation unifiée 61D** : twist (3D) + head_command (4D) + body_command (6D) en zéro-padding. Ne jamais changer ce layout — la politique doit charger via `--new-cmd-obs`.
- **Résolution des joints par NOM**, jamais par index (roues passives intercalées).
- **Vitesse d'entrée via `reset_root_state_uniform` (velocity_range)**, JAMAIS via `push_by_setting_velocity` en mode reset (accumule sur l'état racine → free-joint diverge → NaN). Leçon `roller_crouch`.
- **Angles en radians** dans le code physique ; les constantes de raideur sont exprimées en degrés (`RAMP_DEG_MIN=2.0`, `RAMP_DEG_MAX=20.0`) et converties.
- **Commits simples**, style du dépôt (pas de `Co-authored-by`).
- Tests dans `tests/`, lancés avec `uv run pytest`.

---

## File Structure

- **Create** `src/mjlab_microduck/tasks/slope_terrain.py` — `ramp_angle_by_difficulty()` + `FlatRampTerrainCfg` (géométrie du terrain plat+rampe). Responsabilité unique : le terrain.
- **Modify** `src/mjlab_microduck/tasks/mdp.py` — ajouter `slope_move_masks()` (pur) + `terrain_levels_slope()` (curriculum de raideur).
- **Create** `src/mjlab_microduck/tasks/microduck_roller_slope_env_cfg.py` — `make_microduck_roller_slope_env_cfg()` + `MicroduckRollerSlopeRlCfg`.
- **Modify** `src/mjlab_microduck/tasks/__init__.py` — enregistrer la tâche.
- **Modify** `scripts/infer_policy.py` — flag `--slope` + touche `Y`.
- **Create** `tests/test_slope_terrain.py`, `tests/test_slope_curriculum.py`, `tests/test_roller_slope_cfg.py`.

---

## Task 1 : angle de rampe par difficulté (fonction pure)

**Files:**
- Create: `src/mjlab_microduck/tasks/slope_terrain.py`
- Test: `tests/test_slope_terrain.py`

**Interfaces:**
- Produces: `ramp_angle_by_difficulty(difficulty: float, deg_min: float = 2.0, deg_max: float = 20.0) -> float` (retourne des **radians**). Constantes module `RAMP_DEG_MIN = 2.0`, `RAMP_DEG_MAX = 20.0`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_slope_terrain.py
import math
from mjlab_microduck.tasks.slope_terrain import (
    ramp_angle_by_difficulty,
    RAMP_DEG_MIN,
    RAMP_DEG_MAX,
)


def test_ramp_angle_endpoints():
    assert math.isclose(ramp_angle_by_difficulty(0.0), math.radians(RAMP_DEG_MIN), abs_tol=1e-9)
    assert math.isclose(ramp_angle_by_difficulty(1.0), math.radians(RAMP_DEG_MAX), abs_tol=1e-9)


def test_ramp_angle_midpoint():
    mid_deg = (RAMP_DEG_MIN + RAMP_DEG_MAX) / 2.0
    assert math.isclose(ramp_angle_by_difficulty(0.5), math.radians(mid_deg), abs_tol=1e-9)


def test_ramp_angle_clamps_out_of_range():
    assert math.isclose(ramp_angle_by_difficulty(-1.0), math.radians(RAMP_DEG_MIN), abs_tol=1e-9)
    assert math.isclose(ramp_angle_by_difficulty(2.0), math.radians(RAMP_DEG_MAX), abs_tol=1e-9)
```

- [ ] **Step 2: Lancer le test — il doit échouer**

Run: `uv run pytest tests/test_slope_terrain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mjlab_microduck.tasks.slope_terrain'`

- [ ] **Step 3: Implémentation minimale**

```python
# src/mjlab_microduck/tasks/slope_terrain.py
"""Terrain custom « plat + rampe descendante » pour la tâche roller_slope.

Le robot spawne sur une zone plate, reçoit une impulsion vers +x, roule
jusqu'à la rampe et se laisse glisser. L'angle de la rampe est interpolé par
la difficulté (curriculum) sur [RAMP_DEG_MIN, RAMP_DEG_MAX] degrés.
"""

from __future__ import annotations

import math

import numpy as np

RAMP_DEG_MIN = 2.0
RAMP_DEG_MAX = 20.0


def ramp_angle_by_difficulty(
    difficulty: float, deg_min: float = RAMP_DEG_MIN, deg_max: float = RAMP_DEG_MAX
) -> float:
    """Angle de rampe (radians) interpolé linéairement par la difficulté [0,1]."""
    d = float(np.clip(difficulty, 0.0, 1.0))
    return math.radians(deg_min + d * (deg_max - deg_min))
```

- [ ] **Step 4: Lancer le test — il doit passer**

Run: `uv run pytest tests/test_slope_terrain.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/slope_terrain.py tests/test_slope_terrain.py
git commit -m "roller-slope: angle de rampe par difficulte (fonction pure + tests)"
```

---

## Task 2 : terrain custom `FlatRampTerrainCfg`

**Files:**
- Modify: `src/mjlab_microduck/tasks/slope_terrain.py`
- Test: `tests/test_slope_terrain.py`

**Interfaces:**
- Consumes: `ramp_angle_by_difficulty` (Task 1), `SubTerrainCfg`, `TerrainGeometry`, `TerrainOutput` de `mjlab.terrains.terrain_generator`.
- Produces: `FlatRampTerrainCfg(SubTerrainCfg)` avec champs `flat_length: float = 2.0`, `ramp_length: float = 5.0`, `deg_min: float = 2.0`, `deg_max: float = 20.0`, `thickness: float = 0.5` ; méthode `function(difficulty, spec, rng) -> TerrainOutput`. L'origine de spawn est sur le plat.

**Notes géométrie (à retenir) :** la surface du plat est à `z=0` local. La rampe est un box tourné autour de `+y` par un quaternion `[cos(a/2), 0, sin(a/2), 0]` — une rotation `+a` autour de `+y` abaisse le bord `+x` (la rampe descend quand `x` augmente). L'assemblage exact plat/rampe (pas de marche, pas de trou) **doit être vérifié dans le viewer** (Step 6) car le `z` du centre de la rampe est sensible.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_slope_terrain.py  (ajouter)
import mujoco
import numpy as np
from mjlab_microduck.tasks.slope_terrain import FlatRampTerrainCfg


def _empty_terrain_spec():
    spec = mujoco.MjSpec()
    spec.worldbody.add_body(name="terrain")
    return spec


def test_flat_ramp_builds_geoms_and_origin_on_flat():
    cfg = FlatRampTerrainCfg(flat_length=2.0, ramp_length=5.0)
    cfg.size = (8.0, 4.0)  # posé normalement par le générateur
    spec = _empty_terrain_spec()
    out = cfg.function(difficulty=0.5, spec=spec, rng=np.random.default_rng(0))
    # deux géométries : plat + rampe
    assert len(out.geometries) == 2
    # origine sur le plat (x dans [0, flat_length], z ~ 0)
    assert 0.0 <= out.origin[0] <= 2.0
    assert abs(out.origin[2]) < 1e-6


def test_flat_ramp_steeper_at_higher_difficulty():
    # à difficulté plus haute, le bout de rampe descend plus bas
    cfg = FlatRampTerrainCfg()
    cfg.size = (8.0, 4.0)
    easy = cfg.function(0.0, _empty_terrain_spec(), np.random.default_rng(0))
    hard = cfg.function(1.0, _empty_terrain_spec(), np.random.default_rng(0))
    # la rampe (2e géométrie) est plus basse (centre z plus négatif) en difficile
    assert hard.geometries[1].geom.pos[2] < easy.geometries[1].geom.pos[2]
```

- [ ] **Step 2: Lancer le test — il doit échouer**

Run: `uv run pytest tests/test_slope_terrain.py -k flat_ramp -v`
Expected: FAIL — `ImportError: cannot import name 'FlatRampTerrainCfg'`

- [ ] **Step 3: Implémentation minimale**

```python
# src/mjlab_microduck/tasks/slope_terrain.py  (ajouter en tête)
from dataclasses import dataclass

import mujoco

from mjlab.terrains.terrain_generator import (
    SubTerrainCfg,
    TerrainGeometry,
    TerrainOutput,
)


@dataclass(kw_only=True)
class FlatRampTerrainCfg(SubTerrainCfg):
    """Zone plate de départ suivie d'une rampe descendante (angle par difficulté)."""

    flat_length: float = 2.0   # longueur du plat de départ le long de +x (m)
    ramp_length: float = 5.0   # longueur horizontale de la rampe le long de +x (m)
    deg_min: float = RAMP_DEG_MIN
    deg_max: float = RAMP_DEG_MAX
    thickness: float = 0.5     # épaisseur des box (m)

    def function(
        self, difficulty: float, spec: mujoco.MjSpec, rng
    ) -> TerrainOutput:
        del rng  # non utilisé
        body = spec.body("terrain")
        angle = ramp_angle_by_difficulty(difficulty, self.deg_min, self.deg_max)
        width = self.size[1]
        t = self.thickness

        # Plat : box dont la surface supérieure est à z=0, x dans [0, flat_length].
        flat = body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=(self.flat_length / 2.0, width / 2.0, t / 2.0),
            pos=(self.flat_length / 2.0, 0.0, -t / 2.0),
        )

        # Rampe : box tourné de +angle autour de +y (le bord +x descend).
        # Longueur de surface = ramp_length / cos(angle).
        surf_len = self.ramp_length / math.cos(angle)
        ramp_cx = self.flat_length + self.ramp_length / 2.0
        # Centre z : mi-descente de la surface, moins la demi-épaisseur projetée.
        ramp_cz = -(self.ramp_length * math.tan(angle) / 2.0) - (t / 2.0) * math.cos(angle)
        half = angle / 2.0
        ramp = body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=(surf_len / 2.0, width / 2.0, t / 2.0),
            pos=(ramp_cx, 0.0, ramp_cz),
            quat=(math.cos(half), 0.0, math.sin(half), 0.0),
        )

        origin = np.array([self.flat_length * 0.4, 0.0, 0.0])
        return TerrainOutput(
            origin=origin,
            geometries=[
                TerrainGeometry(geom=flat, color=(0.5, 0.5, 0.5, 1.0)),
                TerrainGeometry(geom=ramp, color=(0.45, 0.55, 0.75, 1.0)),
            ],
        )
```

- [ ] **Step 4: Lancer les tests — ils doivent passer**

Run: `uv run pytest tests/test_slope_terrain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/slope_terrain.py tests/test_slope_terrain.py
git commit -m "roller-slope: terrain custom plat+rampe (FlatRampTerrainCfg + tests)"
```

- [ ] **Step 6: Vérification visuelle (checkpoint humain)**

La géométrie (surtout `ramp_cz` et le signe du quaternion) doit être confirmée à l'œil.
Après la Task 4 (env assemblé), lancer le viewer play (voir Task 4 Step 6) et vérifier :
la zone plate rejoint la rampe **sans marche ni trou**, et la rampe **descend** dans
la direction `+x` (devant le robot). Si un décalage vertical apparaît, ajuster `ramp_cz` ;
si la rampe monte au lieu de descendre, inverser le signe (`-half`) du quaternion.

---

## Task 3 : curriculum de raideur `terrain_levels_slope`

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py`
- Test: `tests/test_slope_curriculum.py`

**Interfaces:**
- Produces:
  - `slope_move_masks(distance: torch.Tensor, size_x: float) -> tuple[torch.Tensor, torch.Tensor]` — helper pur. `move_up = distance > size_x * 0.5` (a atteint le bas → rampe plus raide) ; `move_down = (distance < size_x * 0.2) & ~move_up` (chute/blocage tôt → rampe plus douce). Retourne `(move_up, move_down)` en `bool`.
  - `terrain_levels_slope(env, env_ids) -> torch.Tensor` — signature curriculum mjlab ; calcule la distance parcourue en `x` depuis l'origine, applique `slope_move_masks`, appelle `terrain.update_env_origins`, retourne le niveau moyen.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_slope_curriculum.py
import torch
from mjlab_microduck.tasks.mdp import slope_move_masks


def test_move_up_when_reached_bottom():
    # distance > size_x/2 → monte en difficulté
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
    # entre 1.6 et 4.0 → ni haut ni bas
    dist = torch.tensor([2.5])
    up, down = slope_move_masks(dist, size_x=8.0)
    assert not bool(up[0]) and not bool(down[0])
```

- [ ] **Step 2: Lancer le test — il doit échouer**

Run: `uv run pytest tests/test_slope_curriculum.py -v`
Expected: FAIL — `ImportError: cannot import name 'slope_move_masks'`

- [ ] **Step 3: Implémentation minimale**

Ajouter dans `src/mjlab_microduck/tasks/mdp.py` (près des autres curriculums, ex. après `com_range_curriculum`). Vérifier en tête de fichier que `torch` est importé (il l'est).

```python
def slope_move_masks(distance: "torch.Tensor", size_x: float):
    """Masques de promotion/rétrogradation du curriculum de pente.

    move_up   : a parcouru plus de la moitié de la tuile → il a dévalé la rampe,
                on la rend plus raide.
    move_down : a à peine avancé (< 20% de la tuile) → chute/blocage précoce,
                on adoucit la rampe.
    """
    move_up = distance > size_x * 0.5
    move_down = (distance < size_x * 0.2) & (~move_up)
    return move_up, move_down


def terrain_levels_slope(env, env_ids):
    """Curriculum de raideur pour roller_slope (pas de vitesse commandée).

    Progression basée sur la distance en x parcourue depuis l'origine de spawn.
    """
    asset = env.scene["robot"]
    terrain = env.scene.terrain
    assert terrain is not None
    terrain_generator = terrain.cfg.terrain_generator
    assert terrain_generator is not None

    distance = (
        asset.data.root_link_pos_w[env_ids, 0] - env.scene.env_origins[env_ids, 0]
    )
    move_up, move_down = slope_move_masks(distance, terrain_generator.size[0])
    terrain.update_env_origins(env_ids, move_up, move_down)
    return torch.mean(terrain.terrain_levels.float())
```

- [ ] **Step 4: Lancer le test — il doit passer**

Run: `uv run pytest tests/test_slope_curriculum.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_slope_curriculum.py
git commit -m "roller-slope: curriculum de raideur terrain_levels_slope (+ helper pur teste)"
```

---

## Task 4 : env cfg `roller_slope` + enregistrement

**Files:**
- Create: `src/mjlab_microduck/tasks/microduck_roller_slope_env_cfg.py`
- Modify: `src/mjlab_microduck/tasks/__init__.py`
- Test: `tests/test_roller_slope_cfg.py`

**Interfaces:**
- Consumes: `make_microduck_velocity_rollers_env_cfg` (base physique/DR/obs), `FlatRampTerrainCfg` (Task 2), `terrain_levels_slope` (Task 3), fonctions mdp existantes : `body_upright_gaussian`, `is_alive`, `pose_target_match`, `pose_l1_penalty`, `feet_flat_penalty`, `neck_action_rate_l2`, `joint_torques_l2`, `robot_state_is_nan`, `reset_action_history`, `zero_command_padding`.
- Produces: `make_microduck_roller_slope_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg` et `MicroduckRollerSlopeRlCfg` (`RslRlOnPolicyRunnerCfg`, `experiment_name="roller_slope"`).

> Réutiliser les blocs DR/obs/reset du roller env : on **part** de `make_microduck_velocity_rollers_env_cfg()` et on ne modifie QUE terrain, commande, récompenses, terminaisons, curriculum. Ne pas réécrire la DR.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/test_roller_slope_cfg.py
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


def test_entry_velocity_set_on_reset_base():
    cfg = make_microduck_roller_slope_env_cfg()
    vr = cfg.events["reset_base"].params["velocity_range"]
    assert vr["x"][0] > 0.0  # impulsion vers l'avant


def test_has_upright_and_pose_rewards():
    cfg = make_microduck_roller_slope_env_cfg()
    for name in ("upright", "alive", "standing_pose", "feet_flat"):
        assert name in cfg.rewards
```

- [ ] **Step 2: Lancer le test — il doit échouer**

Run: `uv run pytest tests/test_roller_slope_cfg.py -v`
Expected: FAIL — `ModuleNotFoundError` (module env cfg absent)

- [ ] **Step 3: Implémentation**

```python
# src/mjlab_microduck/tasks/microduck_roller_slope_env_cfg.py
"""Microduck roller slope — descente passive équilibrée.

Le robot spawne sur du plat (impulsion vers l'avant), roule sur une rampe
descendante et se laisse glisser en restant debout. Aucun pilotage : la
commande twist est neutralisée (rel_standing_envs=1.0). Terrain custom
plat+rampe (FlatRampTerrainCfg), curriculum de raideur (terrain_levels_slope).
Obs 61D unifié → interchangeable au runtime (--new-cmd-obs).
"""

from mjlab.envs import ManagerBasedRlEnvCfg
from mjlab.managers import CurriculumTermCfg, EventTermCfg, RewardTermCfg, TerminationTermCfg
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.rl import RslRlOnPolicyRunnerCfg, RslRlModelCfg
from mjlab.terrains import TerrainEntityCfg
from mjlab.terrains.terrain_generator import TerrainGeneratorCfg
from mjlab.tasks.velocity import mdp
from mjlab.envs import mdp as base_mdp

from mjlab_microduck.tasks import mdp as microduck_mdp
from mjlab_microduck.tasks.slope_terrain import FlatRampTerrainCfg
from mjlab_microduck.tasks.microduck_velocity_rollers_env_cfg import (
    make_microduck_velocity_rollers_env_cfg,
)
from mjlab_microduck.tasks.symmetry import PpoWithSymmetryCfg

ENTRY_VELOCITY_X = (0.2, 0.5)  # impulsion vers l'avant au reset (m/s)


def make_microduck_roller_slope_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
    cfg = make_microduck_velocity_rollers_env_cfg(play=play)

    # === TERRAIN : plat + rampe, curriculum de raideur ===
    cfg.scene.terrain = TerrainEntityCfg(
        terrain_type="generator",
        terrain_generator=TerrainGeneratorCfg(
            size=(8.0, 4.0),
            curriculum=True,
            num_rows=10,          # 10 niveaux de raideur
            num_cols=1,
            difficulty_range=(0.0, 1.0),
            sub_terrains={"flat_ramp": FlatRampTerrainCfg(flat_length=2.0, ramp_length=5.0)},
        ),
        max_init_terrain_level=0,  # démarrer sur la rampe la plus douce
    )

    # === COMMANDE neutralisée (équilibre pur) ===
    command = cfg.commands["twist"]
    command.rel_standing_envs = 1.0
    command.rel_heading_envs = 0.0
    command.ranges.lin_vel_x = (0.0, 0.0)
    command.ranges.lin_vel_y = (0.0, 0.0)
    if getattr(command.ranges, "ang_vel_z", None) is not None:
        command.ranges.ang_vel_z = (0.0, 0.0)

    # === RESET : impulsion vers l'avant sur le plat ===
    cfg.events["reset_base"].params["velocity_range"] = {"x": ENTRY_VELOCITY_X}

    # === RÉCOMPENSES : équilibre + posture debout nominale ===
    keep = {"action_rate_l2"}
    for name in list(cfg.rewards.keys()):
        if name not in keep:
            del cfg.rewards[name]

    cfg.rewards["upright"] = RewardTermCfg(
        func=microduck_mdp.body_upright_gaussian,
        weight=3.0,
        params={"asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",)), "std": 0.2},
    )
    cfg.rewards["alive"] = RewardTermCfg(func=microduck_mdp.is_alive, weight=1.0)
    # posture debout nominale (cible fixe = default_joint_pos, aucun override)
    cfg.rewards["standing_pose"] = RewardTermCfg(
        func=microduck_mdp.pose_target_match, weight=3.0, params={"std": 0.4},
    )
    cfg.rewards["standing_pose_l1"] = RewardTermCfg(
        func=microduck_mdp.pose_l1_penalty, weight=1.0,
    )
    cfg.rewards["feet_flat"] = RewardTermCfg(
        func=microduck_mdp.feet_flat_penalty,
        weight=-2.0,
        params={
            "asset_cfg": SceneEntityCfg("robot", site_names=("left_foot", "right_foot")),
            "sensor_name": "feet_ground_contact",
        },
    )
    cfg.rewards["neck_action_rate_l2"] = RewardTermCfg(
        func=microduck_mdp.neck_action_rate_l2, weight=-0.5,
    )
    cfg.rewards["joint_torques_l2"] = RewardTermCfg(
        func=microduck_mdp.joint_torques_l2, weight=-1e-3,
    )
    cfg.rewards["action_rate_l2"].weight = -1.0

    # === TERMINATIONS : chute + bas atteint ===
    cfg.terminations["fell_over"] = TerminationTermCfg(
        func=base_mdp.bad_orientation,
        params={"limit_angle": 1.0, "asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",))},
    )
    cfg.terminations["out_of_bounds"] = TerminationTermCfg(func=mdp.out_of_terrain_bounds)
    cfg.terminations["nan_state"] = TerminationTermCfg(
        func=microduck_mdp.robot_state_is_nan, time_out=False,
    )

    # === EVENTS ===
    cfg.events["reset_action_history"] = EventTermCfg(
        func=microduck_mdp.reset_action_history, mode="reset",
    )

    # === CURRICULUM : raideur de la rampe ===
    for name in list(cfg.curriculum.keys()):
        del cfg.curriculum[name]
    cfg.curriculum["terrain_levels"] = CurriculumTermCfg(func=microduck_mdp.terrain_levels_slope)

    return cfg


MicroduckRollerSlopeRlCfg = RslRlOnPolicyRunnerCfg(
    actor=RslRlModelCfg(
        hidden_dims=(512, 256, 128),
        activation="elu",
        obs_normalization=True,
        distribution_cfg={"class_name": "GaussianDistribution", "init_std": 1.0, "std_type": "scalar"},
    ),
    critic=RslRlModelCfg(hidden_dims=(512, 256, 128), activation="elu", obs_normalization=True),
    algorithm=PpoWithSymmetryCfg(
        value_loss_coef=1.0, use_clipped_value_loss=True, clip_param=0.2,
        entropy_coef=0.01, num_learning_epochs=5, num_mini_batches=4,
        learning_rate=1.0e-3, schedule="adaptive", gamma=0.99, lam=0.95,
        desired_kl=0.01, max_grad_norm=1.0, symmetry_cfg=None,
    ),
    wandb_project="mjlab_microduck",
    experiment_name="roller_slope",
    run_name="roller_slope",
    save_interval=250,
    num_steps_per_env=24,
    max_iterations=8_000,
)
```

Puis enregistrer dans `src/mjlab_microduck/tasks/__init__.py`, en suivant EXACTEMENT le pattern d'enregistrement de `roller_crouch` déjà présent (import de `make_...` + `Microduck...RlCfg`, puis `register_mjlab_task(...)` avec un id du style `"Microduck-Roller-Slope"`). Copier le bloc `roller_crouch` et remplacer `crouch`→`slope`.

- [ ] **Step 4: Lancer les tests — ils doivent passer**

Run: `uv run pytest tests/test_roller_slope_cfg.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Vérifier l'enregistrement de la tâche + build complet**

Run:
```bash
uv run python -c "import gymnasium as gym; import mjlab_microduck.tasks; print([e for e in gym.registry if 'Slope' in e])"
```
Expected: la liste contient l'id `Microduck-Roller-Slope` (ou variante enregistrée).

- [ ] **Step 6: Vérification visuelle du terrain + descente (checkpoint humain — clôt Task 2 Step 6)**

Lancer un court entraînement puis le play (ou `scripts/play_latest.py` selon l'usage du dépôt) et observer :
1. Plat + rampe assemblés sans marche/trou ; la rampe **descend** devant le robot.
2. Le robot spawne sur le plat, part vers l'avant, atteint la rampe.
Si la géométrie est fausse, corriger `slope_terrain.py` (voir Task 2 Step 6) et re-commit.

- [ ] **Step 7: Commit**

```bash
git add src/mjlab_microduck/tasks/microduck_roller_slope_env_cfg.py src/mjlab_microduck/tasks/__init__.py tests/test_roller_slope_cfg.py
git commit -m "roller-slope: env descente passive (terrain plat+rampe, cmd nulle, rewards equilibre) + enregistrement"
```

---

## Task 5 : déploiement — flag `--slope` + touche `Y`

**Files:**
- Modify: `scripts/infer_policy.py`

**Interfaces:**
- Consumes: le `.onnx` exporté de la politique `roller_slope`.
- Produces: argument CLI `--slope <path>` ; attribut `self.slope_session` + flag `self.slope_mode` ; méthode `toggle_slope_mode()` ; touche `GLFW_KEY_Y = 89` câblée.

> La politique pente tourne avec commande twist nulle (comme le mode standing). En slope mode, la bascule automatique walking/standing doit être neutralisée.

- [ ] **Step 1: Ajouter l'argument CLI et charger la session**

Dans `main()` (près des autres `add_argument`, ~ligne 471) :
```python
    parser.add_argument("--slope", type=str, default=None, help="Path to slope policy ONNX file (press Y to toggle)")
```
Passer `slope_onnx_path=args.slope` au constructeur du contrôleur (ajouter le paramètre `slope_onnx_path=None` à `__init__`, ~ligne 51-57, et charger comme les autres) :
```python
        self.slope_session = None
        self.slope_mode = False
        if slope_onnx_path:
            print(f"\nLoading slope policy from: {slope_onnx_path}")
            self.slope_session = ort.InferenceSession(slope_onnx_path)
```

- [ ] **Step 2: Ajouter `toggle_slope_mode` et neutraliser la bascule auto**

Après `toggle_body_pose_mode` (~ligne 285) :
```python
    def toggle_slope_mode(self):
        """Bascule vers/depuis la politique pente (descente passive)."""
        if self.slope_session is None:
            print("Slope unavailable: no --slope policy loaded")
            return
        self.slope_mode = not self.slope_mode
        if self.slope_mode:
            self.ort_session = self.slope_session
            self.current_policy = "slope"
            self.set_vel_cmd(0.0, 0.0, 0.0)  # descente passive : commande nulle
            print("Slope mode: ON (descente passive)")
        else:
            self.ort_session = self.walking_session or self.standing_session
            self.current_policy = "walking" if self.walking_session else "standing"
            print("Slope mode: OFF")
```
Dans `_update_policy_session` (~ligne 250), ajouter le garde en tête (après le garde `ground_pick_mode`) :
```python
        if self.slope_mode:
            return  # Ne pas basculer pendant le mode pente
```

- [ ] **Step 3: Câbler la touche `Y`**

Ajouter le code de touche près des autres (~ligne 680) :
```python
    GLFW_KEY_Y = 89
```
Dans `key_callback`, ajouter une branche (ex. après la branche `GLFW_KEY_B`) :
```python
            elif key == GLFW_KEY_Y:
                policy.toggle_slope_mode()
```
Ajouter la ligne d'aide clavier (près des `print` ~ligne 821) :
```python
    print("  Y:                toggle slope mode (requires --slope, descente passive)")
```

- [ ] **Step 4: Vérifier que le script se charge sans erreur**

Run: `uv run python scripts/infer_policy.py --help`
Expected: l'aide s'affiche et liste `--slope`.

- [ ] **Step 5: Commit**

```bash
git add scripts/infer_policy.py
git commit -m "roller-slope: deploiement --slope + touche Y (bascule mode pente)"
```

---

## Self-Review (fait par l'auteur du plan)

- **Couverture spec** : tâche dédiée (Task 4) ✓ ; terrain plat+rampe custom (Task 2) ✓ ; départ plat + impulsion (Task 4 reset velocity_range) ✓ ; commande nulle (Task 4) ✓ ; récompenses équilibre + pose debout + anti-écrasement (Task 4) ✓ ; terminaisons chute/bas/nan (Task 4) ✓ ; curriculum 0→20° (Task 1 angle + Task 3 promotion) ✓ ; obs 61D interchangeable (hérité du roller env, non modifié) ✓ ; bouton Y (Task 5) ✓.
- **Placeholders** : aucun « TBD/TODO » ; les deux checkpoints humains (géométrie viewer) sont des vérifications explicites, pas des trous d'implémentation.
- **Cohérence des types** : `ramp_angle_by_difficulty` (Task 1) réutilisé par `FlatRampTerrainCfg` (Task 2) ; `slope_move_masks` (Task 3) consommé par `terrain_levels_slope` (Task 3) ; noms de récompenses testés en Task 4 (`upright`, `alive`, `standing_pose`, `feet_flat`) alignés sur l'implémentation.
- **Risques signalés** : géométrie de la rampe (`ramp_cz`, signe du quaternion) à confirmer au viewer ; noms exacts d'API mjlab (`terrain.terrain_levels`, `TerrainEntityCfg`, id d'enregistrement) à valider contre le pattern `roller_crouch` existant lors de l'implémentation.
