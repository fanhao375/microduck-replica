# Ground-pick par suivi de pose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réécrire la tâche `Mjlab-GroundPick-Flat-MicroDuck` pour piloter le geste par un suivi de pose articulaire interpolé par la phase (STAND→DOWN→STAND) au lieu de l'objectif espace-tâche actuel (proximité bouche-sol + retour de pose).

**Architecture:** On ajoute trois fonctions mdp pures/quasi-pures (`phase_pose_blend`, `phase_pose_track`, `phase_pose_track_l1`) qui calculent une cible articulaire interpolée entre HOME (STAND) et un dict `DOWN_POSE` selon un profil de phase à 4 segments, résolue **par nom**. On ajoute un flag `randomize_phase` à la commande de phase existante. On réécrit ensuite le bloc rewards de `microduck_ground_pick_env_cfg.py` en gardant tout le reste (DR, obs 61D, curricula, RlCfg).

**Tech Stack:** Python, PyTorch, mjlab 1.3.0, MuJoCo, uv, pytest (via `uv run --with pytest`).

## Global Constraints

- Résolution des joints **PAR NOM** (`asset.find_joints([name])[0][0]`), jamais par index en dur.
- Obs 61D unifié **inchangé** (padding head/body zéro) → policy interchangeable dans le slot runtime.
- Task id inchangé : `Mjlab-GroundPick-Flat-MicroDuck` (+ variante `-Rough-`).
- Période de phase = **4.0 s** (défaut du slot `--ground-pick-period`).
- Profil de phase (fractions) : `DESCENT_END=0.15`, `HOLD_END=0.50`, `RISE_END=0.65`.
- `randomize_phase=False` pour la tâche ground_pick (parité déploiement bouton A à φ=0) ; défaut `True` de la cfg pour ne pas casser sit/stand.
- STAND = HOME (`asset.data.default_joint_pos`, ne pas redéfinir). DOWN = dict `DOWN_POSE` par nom.
- 14 joints actifs (mouth exclu). Robot `MICRODUCK_GROUND_PICK_ROBOT_CFG` (pas de roues → indices 0-4 jambe G, 5-8 cou/tête, 9-13 jambe D, mais on résout quand même par nom).
- Fichiers mdp : imports déjà présents (`torch`, `Optional`, `Entity`, `SceneEntityCfg`, `ManagerBasedRlEnv`, `_DEFAULT_ASSET_CFG`).

---

### Task 1: Fonction `phase_pose_blend` (blend 4 segments, pure)

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (ajout d'une fonction ; l'insérer juste avant `phase_pose_match` ~ligne 2041)
- Test: `tests/test_ground_pick_pose.py` (create)

**Interfaces:**
- Produces: `phase_pose_blend(phase: torch.Tensor, descent_end: float, hold_end: float, rise_end: float) -> torch.Tensor` — renvoie un blend ∈ [0,1] de même shape que `phase` (0 = STAND, 1 = DOWN).

- [ ] **Step 1: Write the failing test**

Créer `tests/test_ground_pick_pose.py` :

```python
import torch
from mjlab_microduck.tasks.mdp import phase_pose_blend

DESCENT_END, HOLD_END, RISE_END = 0.15, 0.50, 0.65


def test_phase_pose_blend_keypoints():
    phase = torch.tensor([0.0, 0.075, 0.15, 0.30, 0.50, 0.575, 0.65, 0.80])
    b = phase_pose_blend(phase, DESCENT_END, HOLD_END, RISE_END)
    expected = torch.tensor([0.0, 0.5, 1.0, 1.0, 1.0, 0.5, 0.0, 0.0])
    assert torch.allclose(b, expected, atol=1e-6), b


def test_phase_pose_blend_range():
    phase = torch.linspace(0.0, 1.0, 101)
    b = phase_pose_blend(phase, DESCENT_END, HOLD_END, RISE_END)
    assert b.min() >= 0.0 and b.max() <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py -q`
Expected: FAIL — `ImportError: cannot import name 'phase_pose_blend'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/mjlab_microduck/tasks/mdp.py`, juste avant `def phase_pose_match(` (~ligne 2041) :

```python
def phase_pose_blend(
    phase: torch.Tensor,
    descent_end: float,
    hold_end: float,
    rise_end: float,
) -> torch.Tensor:
    """Blend 0..1 le long de la phase [0,1) — 0 = pose STAND, 1 = pose DOWN.

    [0, descent_end)       : 0 -> 1  (se baisser)
    [descent_end, hold_end): 1       (bas)
    [hold_end, rise_end)   : 1 -> 0  (se lever)
    [rise_end, 1.0)        : 0       (haut / repos)
    """
    b = torch.zeros_like(phase)
    descend = phase < descent_end
    b = torch.where(descend, phase / descent_end, b)
    low = (phase >= descent_end) & (phase < hold_end)
    b = torch.where(low, torch.ones_like(phase), b)
    rise = (phase >= hold_end) & (phase < rise_end)
    b = torch.where(rise, 1.0 - (phase - hold_end) / (rise_end - hold_end), b)
    return b
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_ground_pick_pose.py src/mjlab_microduck/tasks/mdp.py
git commit -m "feat(mdp): phase_pose_blend — blend 4 segments STAND<->DOWN par la phase"
```

---

### Task 2: Rewards `phase_pose_track` / `phase_pose_track_l1` (+ helper `_phase_pose_error`)

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (ajout juste après `phase_pose_blend`)
- Test: `tests/test_ground_pick_pose.py` (append)

**Interfaces:**
- Consumes: `phase_pose_blend` (Task 1).
- Produces:
  - `_phase_pose_error(env, asset_cfg, command_name, target_pose: dict, descent_end, hold_end, rise_end, source_pose: dict | None = None) -> (cur: Tensor, target: Tensor)` — tenseurs (B, k) résolus par nom.
  - `phase_pose_track(env, command_name="twist", target_pose: dict | None = None, source_pose: dict | None = None, std=0.3, descent_end=0.15, hold_end=0.50, rise_end=0.65, asset_cfg=_DEFAULT_ASSET_CFG) -> Tensor` — gaussienne `exp(-((cur-target)/std)²).mean(-1)`.
  - `phase_pose_track_l1(env, command_name="twist", target_pose=None, source_pose=None, descent_end=0.15, hold_end=0.50, rise_end=0.65, asset_cfg=_DEFAULT_ASSET_CFG) -> Tensor` — `-(cur-target).abs().mean(-1)`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_ground_pick_pose.py` un faux env léger + les assertions :

```python
from mjlab_microduck.tasks.mdp import phase_pose_track, phase_pose_track_l1


class _FakeData:
    def __init__(self, joint_pos, default_pos):
        self.joint_pos = joint_pos
        self.default_joint_pos = default_pos


class _FakeAsset:
    def __init__(self, names, joint_pos, default_pos):
        self._ids = {n: i for i, n in enumerate(names)}
        self.data = _FakeData(joint_pos, default_pos)

    def find_joints(self, query):
        # mjlab renvoie (ids, names) ; on ne gère que la requête [name]
        (name,) = query
        return ([self._ids[name]], [name])


class _FakeCmdMgr:
    def __init__(self, cmd):
        self._cmd = cmd

    def get_command(self, _name):
        return self._cmd


class _FakeEnv:
    def __init__(self, names, joint_pos, default_pos, phase):
        import math
        self.device = "cpu"
        self.scene = {"robot": _FakeAsset(names, joint_pos, default_pos)}
        ang = 2 * math.pi * phase
        cmd = torch.tensor([[math.cos(ang), math.sin(ang), 0.0]])
        self.command_manager = _FakeCmdMgr(cmd)


NAMES = ["j0", "j1"]
DOWN = {"j0": 1.0, "j1": -1.0}
# HOME (STAND source) = 0 pour les deux joints
HOME = torch.tensor([[0.0, 0.0]])


def _env(cur, phase):
    return _FakeEnv(NAMES, torch.tensor([cur]), HOME.clone(), phase)


def test_phase_pose_track_perfect_at_down():
    # phase 0.30 -> blend 1 -> cible = DOWN ; cur == DOWN -> gaussienne 1, l1 0
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([1.0, -1.0], phase=0.30)
    r = phase_pose_track(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-6), r
    env2 = _env([1.0, -1.0], phase=0.30)
    l1 = phase_pose_track_l1(env2, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(l1, torch.tensor([0.0]), atol=1e-6), l1


def test_phase_pose_track_l1_at_home_when_down_target():
    # phase 0.30 -> cible DOWN=[1,-1] ; cur=HOME=[0,0] -> l1 = -mean(|1|,|1|) = -1
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([0.0, 0.0], phase=0.30)
    l1 = phase_pose_track_l1(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(l1, torch.tensor([-1.0]), atol=1e-6), l1


def test_phase_pose_track_returns_to_stand():
    # phase 0.80 -> blend 0 -> cible = HOME ; cur=HOME -> gaussienne 1
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([0.0, 0.0], phase=0.80)
    r = phase_pose_track(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-6), r
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py -q`
Expected: FAIL — `ImportError: cannot import name 'phase_pose_track'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/mjlab_microduck/tasks/mdp.py`, juste après `phase_pose_blend` :

```python
def _phase_pose_error(
    env: ManagerBasedRlEnv,
    asset_cfg: SceneEntityCfg,
    command_name: str,
    target_pose: dict,
    descent_end: float,
    hold_end: float,
    rise_end: float,
    source_pose: Optional[dict] = None,
):
    """(cur, target) pour la pose interpolée par la phase, résolue PAR NOM.

    Cible = source + blend(phase)·(target_pose - source), source = STAND
    (`source_pose` si fourni, sinon le DEFAULT/HOME du modèle). blend ∈ [0,1]
    (0 = STAND, 1 = target_pose) via `phase_pose_blend`.
    """
    asset: Entity = env.scene[asset_cfg.name]
    cmd = env.command_manager.get_command(command_name)
    phase = (torch.atan2(cmd[:, 1], cmd[:, 0]) / (2 * torch.pi)) % 1.0  # (B,)
    blend = phase_pose_blend(phase, descent_end, hold_end, rise_end)     # (B,)

    names = list(target_pose.keys())
    ids = [int(asset.find_joints([n])[0][0]) for n in names]
    default = asset.data.default_joint_pos[:, ids]                       # (B,k)

    source = default.clone()
    if source_pose:
        for j, n in enumerate(names):
            if n in source_pose:
                source[:, j] = source_pose[n]
    target_vec = torch.tensor(
        [target_pose[n] for n in names], device=env.device, dtype=default.dtype
    ).unsqueeze(0)                                                       # (1,k)

    target = source + blend.unsqueeze(-1) * (target_vec - source)        # (B,k)
    cur = asset.data.joint_pos[:, ids]                                   # (B,k)
    return cur, target


def phase_pose_track(
    env: ManagerBasedRlEnv,
    command_name: str = "twist",
    target_pose: Optional[dict] = None,
    source_pose: Optional[dict] = None,
    std: float = 0.3,
    descent_end: float = 0.15,
    hold_end: float = 0.50,
    rise_end: float = 0.65,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Gaussienne sur la pose articulaire vs cible interpolée STAND<->DOWN.

    Reward directif : indique la config articulaire exacte à chaque phase. Se
    relever (cible → STAND) est récompensé exactement comme se baisser (cible →
    DOWN) — symétrique par construction. Résolution PAR NOM.
    """
    cur, target = _phase_pose_error(
        env, asset_cfg, command_name, target_pose or {},
        descent_end, hold_end, rise_end, source_pose,
    )
    return torch.exp(-((cur - target) / std) ** 2).mean(dim=-1)


def phase_pose_track_l1(
    env: ManagerBasedRlEnv,
    command_name: str = "twist",
    target_pose: Optional[dict] = None,
    source_pose: Optional[dict] = None,
    descent_end: float = 0.15,
    hold_end: float = 0.50,
    rise_end: float = 0.65,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Bootstrap L1 vers la cible interpolée (pénalité négative).

    Gradient constant partout — donne une direction vers la cible même quand la
    gaussienne ci-dessus a saturé à ~0 loin de la cible.
    """
    cur, target = _phase_pose_error(
        env, asset_cfg, command_name, target_pose or {},
        descent_end, hold_end, rise_end, source_pose,
    )
    return -(cur - target).abs().mean(dim=-1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_ground_pick_pose.py src/mjlab_microduck/tasks/mdp.py
git commit -m "feat(mdp): phase_pose_track/_l1 — suivi de pose interpolée par la phase (par nom)"
```

---

### Task 3: Flag `randomize_phase` sur `GroundPickPhaseCommandCfg`

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (classe `GroundPickPhaseCommand` ~3611/3626, cfg ~3644)
- Test: `tests/test_ground_pick_pose.py` (append)

**Interfaces:**
- Produces: `GroundPickPhaseCommandCfg.randomize_phase: bool = True` ; `GroundPickPhaseCommand.reset()` met la phase à 0 quand `randomize_phase=False`, sinon `torch.rand`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_ground_pick_pose.py` :

```python
def test_ground_pick_cmd_cfg_has_randomize_phase_default_true():
    from mjlab_microduck.tasks.mdp import GroundPickPhaseCommandCfg
    from mjlab.tasks.velocity.mdp import UniformVelocityCommandCfg
    # construit une cfg minimale en copiant une cfg velocity par défaut
    base = UniformVelocityCommandCfg(
        asset_name="robot", resampling_time_range=(10.0, 10.0),
        ranges=UniformVelocityCommandCfg.Ranges(
            lin_vel_x=(0.0, 0.0), lin_vel_y=(0.0, 0.0), ang_vel_z=(0.0, 0.0),
        ),
    )
    cfg = GroundPickPhaseCommandCfg(**{**vars(base)})
    assert cfg.randomize_phase is True
    assert cfg.period == 4.0
```

Note : si la signature de `UniformVelocityCommandCfg.Ranges` diffère localement, adapter les champs — l'assertion clé est `cfg.randomize_phase is True`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py::test_ground_pick_cmd_cfg_has_randomize_phase_default_true -q`
Expected: FAIL — `AttributeError: 'GroundPickPhaseCommandCfg' object has no attribute 'randomize_phase'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/mjlab_microduck/tasks/mdp.py`, classe `GroundPickPhaseCommand`, modifier `__init__` et `reset` :

Remplacer (dans `__init__`, ~ligne 3614) :
```python
        self._period = float(getattr(cfg, "period", self.PERIOD))
```
par :
```python
        self._period = float(getattr(cfg, "period", self.PERIOD))
        self._randomize_phase = bool(getattr(cfg, "randomize_phase", True))
```

Remplacer la méthode `reset` (~ligne 3626) :
```python
    def reset(self, env_ids: torch.Tensor | None) -> dict:
        if env_ids is not None and len(env_ids) > 0:
            self._gp_phase[env_ids] = torch.rand(len(env_ids), device=self.device)
        return {}
```
par :
```python
    def reset(self, env_ids: torch.Tensor | None) -> dict:
        if env_ids is not None and len(env_ids) > 0:
            if self._randomize_phase:
                self._gp_phase[env_ids] = torch.rand(len(env_ids), device=self.device)
            else:
                self._gp_phase[env_ids] = 0.0
        return {}
```

Dans la cfg `GroundPickPhaseCommandCfg` (~ligne 3644), ajouter le champ après `period` :
```python
@_dataclass(kw_only=True)
class GroundPickPhaseCommandCfg(UniformVelocityCommandCfg):
    class_type: type = GroundPickPhaseCommand
    period: float = 4.0  # cycle length in seconds; sitstand uses 8.0
    randomize_phase: bool = True  # False = chaque épisode démarre à φ=0 (parité slot bouton A)

    def build(self, env: ManagerBasedRlEnv) -> "GroundPickPhaseCommand":
        return GroundPickPhaseCommand(self, env)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_ground_pick_pose.py::test_ground_pick_cmd_cfg_has_randomize_phase_default_true -q`
Expected: PASS. Si la construction de `UniformVelocityCommandCfg` échoue pour une raison d'API locale, ajuster les champs du `base` dans le test (l'implémentation, elle, est correcte).

- [ ] **Step 5: Commit**

```bash
git add tests/test_ground_pick_pose.py src/mjlab_microduck/tasks/mdp.py
git commit -m "feat(mdp): flag randomize_phase sur GroundPickPhaseCommandCfg (défaut True)"
```

---

### Task 4: Réécriture du bloc rewards + poses dans l'env cfg

**Files:**
- Modify: `src/mjlab_microduck/tasks/microduck_ground_pick_env_cfg.py`
- Test: `tests/test_ground_pick_cfg.py` (create)

**Interfaces:**
- Consumes: `phase_pose_track`, `phase_pose_track_l1` (Task 2) ; `randomize_phase` (Task 3).
- Produces: `make_microduck_ground_pick_env_cfg(play=False, rough=False)` renvoie une cfg dont : commande `GroundPickPhaseCommand` avec `randomize_phase=False`, `period=4.0` ; rewards contiennent `phase_pose_track` (6.0) et `phase_pose_track_l1` (2.0), `mouth_ground_proximity` (1.0) ; ne contiennent plus `mouth_perpendicular_to_ground`, `ground_pick_return_pose_legs`, `ground_pick_return_pose_neck`.

- [ ] **Step 1: Write the failing test**

Créer `tests/test_ground_pick_cfg.py` :

```python
from mjlab_microduck.tasks.microduck_ground_pick_env_cfg import (
    make_microduck_ground_pick_env_cfg,
)
from mjlab_microduck.tasks.mdp import GroundPickPhaseCommand


def test_ground_pick_cfg_builds_with_pose_rewards():
    cfg = make_microduck_ground_pick_env_cfg()
    rewards = cfg.rewards
    assert "phase_pose_track" in rewards
    assert "phase_pose_track_l1" in rewards
    assert rewards["phase_pose_track"].weight == 6.0
    assert rewards["phase_pose_track_l1"].weight == 2.0
    # filet bouche-sol conservé mais allégé
    assert "mouth_ground_proximity" in rewards
    assert rewards["mouth_ground_proximity"].weight == 1.0
    # anciennes mécaniques retirées
    assert "mouth_perpendicular_to_ground" not in rewards
    assert "ground_pick_return_pose_legs" not in rewards
    assert "ground_pick_return_pose_neck" not in rewards


def test_ground_pick_cfg_command_is_phase_no_randomize():
    cfg = make_microduck_ground_pick_env_cfg()
    cmd = cfg.commands["twist"]
    assert cmd.class_type is GroundPickPhaseCommand
    assert cmd.period == 4.0
    assert cmd.randomize_phase is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_ground_pick_cfg.py -q`
Expected: FAIL — `assert 'phase_pose_track' in rewards` (KeyError/False).

- [ ] **Step 3: Write minimal implementation**

Dans `src/mjlab_microduck/tasks/microduck_ground_pick_env_cfg.py` :

(a) Ajouter les constantes de poses/phase juste avant `def make_microduck_ground_pick_env_cfg(` :

```python
# ── Poses cibles du geste (rad, par NOM) ──────────────────────────────────────
# STAND = HOME (default_joint_pos du modèle) — ne pas redéfinir ici : source du
# blend. DOWN = pli avant profond (bouche vers le sol), valeurs initiales tirées
# du keyframe FOLD de scene_walk.xml. ⚠️ REMPLAÇABLE par une lecture read_pose.py
# du vrai robot posé bouche-au-sol quand disponible.
DOWN_POSE = {
    "left_hip_yaw": 0.0, "left_hip_roll": 0.0, "left_hip_pitch": 1.57,
    "left_knee": 1.57, "left_ankle": 0.0,
    "neck_pitch": 1.0, "head_pitch": 1.0, "head_yaw": 0.0, "head_roll": 0.0,
    "right_hip_yaw": 0.0, "right_hip_roll": 0.0, "right_hip_pitch": -1.57,
    "right_knee": -1.57, "right_ankle": 0.0,
}

# Timing du cycle (fractions de phase), période 4 s :
#   descente [0, DESCENT_END) ~0.6s / bas [DESCENT_END, HOLD_END) ~1.4s /
#   remontée [HOLD_END, RISE_END) ~0.6s / repos [RISE_END, 1) ~1.4s
GP_PERIOD    = 4.0
DESCENT_END  = 0.15
HOLD_END     = 0.50
RISE_END     = 0.65
POSE_STD     = 0.3
```

(b) Dans la boucle de suppression des rewards (~ligne 145-155), remplacer le contenu du geste. **Retirer** les deux blocs `mouth_perpendicular_to_ground` (~176-183) et les deux `ground_pick_return_pose_*` (~189-212), et **retuner** `mouth_ground_proximity` à `weight=1.0` (~163-172, changer `weight=2.0` → `weight=1.0`).

Concrètement :
- Éditer le bloc `cfg.rewards["mouth_ground_proximity"]` : `weight=2.0` → `weight=1.0`.
- Supprimer entièrement le bloc `cfg.rewards["mouth_perpendicular_to_ground"] = RewardTermCfg(...)`.
- Supprimer les blocs `_LEG_JOINTS = [...]` / `cfg.rewards["ground_pick_return_pose_legs"]` et `_NECK_JOINTS = [...]` / `cfg.rewards["ground_pick_return_pose_neck"]`.
- Retirer `"pose"` de la liste de suppression de rewards si présent (inchangé) — mais **retirer** aussi la ligne de commentaire `# replaced by phase-conditioned ground_pick_return_pose` devenue obsolète (optionnel).

(c) Ajouter les deux nouveaux rewards de suivi de pose (à la place des blocs retirés, dans la section « main ground pick objectives ») :

```python
    # Suivi de pose interpolée par la phase (STAND<->DOWN<->STAND). Directif et
    # symétrique : le retour debout est récompensé exactement comme la descente.
    cfg.rewards["phase_pose_track"] = RewardTermCfg(
        func=microduck_mdp.phase_pose_track,
        weight=6.0,
        params={
            "command_name": "twist",
            "target_pose": DOWN_POSE,
            "std": POSE_STD,
            "descent_end": DESCENT_END,
            "hold_end": HOLD_END,
            "rise_end": RISE_END,
            "asset_cfg": SceneEntityCfg("robot"),
        },
    )
    cfg.rewards["phase_pose_track_l1"] = RewardTermCfg(
        func=microduck_mdp.phase_pose_track_l1,
        weight=2.0,
        params={
            "command_name": "twist",
            "target_pose": DOWN_POSE,
            "descent_end": DESCENT_END,
            "hold_end": HOLD_END,
            "rise_end": RISE_END,
            "asset_cfg": SceneEntityCfg("robot"),
        },
    )
```

(d) Dans le bloc « Command » (~ligne 368), passer la période et désactiver la randomisation de phase :

Remplacer :
```python
    cfg.commands["twist"] = microduck_mdp.GroundPickPhaseCommandCfg(
        **{**vars(command), "class_type": microduck_mdp.GroundPickPhaseCommand}
    )
```
par :
```python
    cfg.commands["twist"] = microduck_mdp.GroundPickPhaseCommandCfg(
        **{
            **vars(command),
            "class_type": microduck_mdp.GroundPickPhaseCommand,
            "period": GP_PERIOD,
            "randomize_phase": False,
        }
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pytest pytest tests/test_ground_pick_cfg.py -q`
Expected: PASS (2 passed).

Puis vérifier que l'ensemble de la suite passe :
Run: `uv run --with pytest pytest tests/ -q`
Expected: PASS (tous).

- [ ] **Step 5: Commit**

```bash
git add tests/test_ground_pick_cfg.py src/mjlab_microduck/tasks/microduck_ground_pick_env_cfg.py
git commit -m "feat(ground_pick): suivi de pose interpolée par la phase (STAND->DOWN->STAND)"
```

---

### Task 5: Vérification de bout en bout (construction runtime de la tâche)

**Files:**
- Test: `tests/test_ground_pick_cfg.py` (append)

**Interfaces:**
- Consumes: tout ce qui précède.

- [ ] **Step 1: Write the failing/uncovered test**

Ajouter à `tests/test_ground_pick_cfg.py` :

```python
def test_ground_pick_rough_variant_builds():
    cfg = make_microduck_ground_pick_env_cfg(rough=True)
    assert "phase_pose_track" in cfg.rewards


def test_ground_pick_play_variant_builds():
    cfg = make_microduck_ground_pick_env_cfg(play=True)
    assert cfg.commands["twist"].randomize_phase is False
```

- [ ] **Step 2: Run to verify**

Run: `uv run --with pytest pytest tests/test_ground_pick_cfg.py -q`
Expected: PASS.

- [ ] **Step 3: Vérifier l'enregistrement de la tâche (import du package)**

Run: `uv run python -c "import mjlab_microduck.tasks; print('ok')"`
Expected: affiche les lignes `✓ ... registered` dont `GroundPick`, puis `ok`, sans exception.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ground_pick_cfg.py
git commit -m "test(ground_pick): variantes rough/play + import du package"
```

---

## Self-Review

**1. Spec coverage :**
- §1 objectif directif par pose → Tasks 1,2,4. ✓
- §2 poses (STAND=HOME source, DOWN=FOLD par nom) → Task 4 (a), Task 2 (`source_pose=None`→default). ✓
- §3 profil 4 segments période 4 s + `randomize_phase=False` → Task 1, Task 3, Task 4 (a,d). ✓
- §4 fonctions mdp `phase_pose_blend/track/_l1` par nom → Tasks 1,2. ✓
- §5 rewards (ajouts + retraits + retune mouth 1.0) → Task 4 (b,c), test Task 4. ✓
- §6 déploiement (période 4, kp-ratio 1.0) → documenté dans spec ; period=4 vérifié en test Task 4. ✓
- §7 tests (fonctions pures + construction env) → Tasks 1,2,4,5. ✓
- §9 doublon `pose_target_match` hors scope → non modifié (conforme). ✓

**2. Placeholder scan :** aucun TODO/TBD ; tout le code est fourni. ✓

**3. Type consistency :** `phase_pose_track(target_pose=..., std=..., asset_cfg=...)` et `phase_pose_track_l1(target_pose=..., asset_cfg=...)` identiques entre Task 2 (def), Task 4 (appel) et tests. `randomize_phase` cohérent entre Task 3 (def) et Task 4/tests (usage). `GroundPickPhaseCommand`/`GroundPickPhaseCommandCfg` noms inchangés. ✓
