# Tâche shoot par suivi de poses — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter une tâche RL `Mjlab-Shoot-Flat-MicroDuck` qui apprend un geste de shoot one-shot (jambe droite) par suivi d'une trajectoire de poses à 4 keyframes (STAND → PIED_ARRIÈRE → PIED_AVANT → STAND) interpolée par la phase.

**Architecture:** Même moule que la tâche `ground_pick` de cette branche. Une commande de phase (`GroundPickPhaseCommand`, `[cos,sin,0]`) pilote une cible articulaire interpolée entre 3 poses ; des rewards gaussien + L1 récompensent le suivi ; obs 61D unifiée pour déploiement dans un slot bouton du runtime. Aucune balle simulée.

**Tech Stack:** Python, PyTorch, mjlab 1.3.0, MuJoCo, uv, pytest.

## Global Constraints

- Obs **61D unifiée** identique aux autres policies microduck (`[gyro(3), projected_gravity(3), joint_pos(14), joint_vel(14), last_action(14), command(13)]`, head+body command zero-paddés). Ne pas casser cette forme.
- Résolution des joints **PAR NOM** (`asset.find_joints([name])`), jamais par index en dur.
- **14 joints** actifs (mouth exclu). Robot `MICRODUCK_WALK_ROBOT_CFG`.
- Ne pas modifier le runtime Rust ni la classe de commande de façon cassante : le flag `randomize_phase` ajouté DOIT défaut à `True` pour préserver `ground_pick`.
- Jambe **droite** frappe, **gauche** en appui.
- Tests : `uv run --with pytest pytest tests/ -q`.
- Convention commits : messages en français, style `feat:`/`docs:`/`test:`.

---

## File Structure

- `src/mjlab_microduck/tasks/mdp.py` — MODIFIER : ajouter `kick_pose_target` (pure), `_kick_pose_error`, `kick_pose_track`, `kick_pose_track_l1` ; ajouter le flag `randomize_phase` à `GroundPickPhaseCommand` / `GroundPickPhaseCommandCfg`.
- `src/mjlab_microduck/tasks/microduck_shoot_env_cfg.py` — CRÉER : `make_microduck_shoot_env_cfg`, `MicroduckShootRlCfg`, `STAND_POSE`/`KICK_BACK_POSE`/`KICK_FWD_POSE`, timings.
- `src/mjlab_microduck/tasks/__init__.py` — MODIFIER : import + `register_mjlab_task("Mjlab-Shoot-Flat-MicroDuck", …)`.
- `tests/test_shoot.py` — CRÉER : tests des fonctions pures (`kick_pose_target`) + rewards via stub-env.
- `tests/test_shoot_cfg.py` — CRÉER : test d'intégration (l'env se construit, bonne commande/rewards).

---

### Task 1: Flag `randomize_phase` sur la commande de phase

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py:3618-3672` (`GroundPickPhaseCommand` + `GroundPickPhaseCommandCfg`)
- Test: `tests/test_shoot.py`

**Interfaces:**
- Produces: `GroundPickPhaseCommandCfg(randomize_phase: bool = True, period: float = 4.0, …)` ; à l'exécution `reset()` met φ=0 quand `randomize_phase=False`, sinon `rand()`.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_shoot.py` avec :

```python
from mjlab_microduck.tasks.mdp import GroundPickPhaseCommandCfg


def test_phase_cmd_randomize_flag_default_true():
    cfg = GroundPickPhaseCommandCfg()
    assert cfg.randomize_phase is True


def test_phase_cmd_randomize_flag_settable_false():
    cfg = GroundPickPhaseCommandCfg(randomize_phase=False)
    assert cfg.randomize_phase is False
```

- [ ] **Step 2: Lancer le test, vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'randomize_phase'`.

- [ ] **Step 3: Ajouter le champ au cfg + threading dans la classe**

Dans `GroundPickPhaseCommandCfg` (dataclass, ~ligne 3667) ajouter le champ :

```python
@_dataclass(kw_only=True)
class GroundPickPhaseCommandCfg(UniformVelocityCommandCfg):
    class_type: type = GroundPickPhaseCommand
    period: float = 4.0  # cycle length in seconds; sitstand uses 8.0
    randomize_phase: bool = True  # False -> chaque épisode démarre à φ=0 (STAND)

    def build(self, env: ManagerBasedRlEnv) -> "GroundPickPhaseCommand":
        return GroundPickPhaseCommand(self, env)
```

Dans `GroundPickPhaseCommand.__init__` (~ligne 3634) lire le flag :

```python
    def __init__(self, cfg, env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self._gp_phase = torch.zeros(self.num_envs, device=self.device)
        self._period = float(getattr(cfg, "period", self.PERIOD))
        self._randomize_phase = bool(getattr(cfg, "randomize_phase", True))
```

Dans `GroundPickPhaseCommand.reset` (~ligne 3649) respecter le flag :

```python
    def reset(self, env_ids: torch.Tensor | None) -> dict:
        if env_ids is not None and len(env_ids) > 0:
            if self._randomize_phase:
                self._gp_phase[env_ids] = torch.rand(len(env_ids), device=self.device)
            else:
                self._gp_phase[env_ids] = 0.0
        return {}
```

- [ ] **Step 4: Lancer le test, vérifier le succès**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_shoot.py
git commit -m "feat: flag randomize_phase sur GroundPickPhaseCommand (défaut True)"
```

---

### Task 2: Fonction pure `kick_pose_target`

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (ajouter près de `phase_pose_blend`, ~ligne 2062)
- Test: `tests/test_shoot.py`

**Interfaces:**
- Produces: `kick_pose_target(phase: Tensor(B,), stand, back, forward, windup_end: float, kick_end: float, return_end: float) -> Tensor(B,k)`. `stand/back/forward` sont des tenseurs `(k,)` ou `(1,k)`. Segments : [0,windup_end) STAND→BACK, [windup_end,kick_end) BACK→FORWARD, [kick_end,return_end) FORWARD→STAND, [return_end,1) STAND.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_shoot.py` :

```python
import torch
from mjlab_microduck.tasks.mdp import kick_pose_target

W, K, R = 0.35, 0.45, 0.75  # windup_end, kick_end, return_end
STAND = torch.tensor([0.0, 0.0])
BACK = torch.tensor([1.0, -1.0])
FWD = torch.tensor([-1.0, 2.0])


def _t(phase):
    return kick_pose_target(torch.tensor([phase]), STAND, BACK, FWD, W, K, R)[0]


def test_kick_target_keypoints():
    assert torch.allclose(_t(0.0), STAND)          # début: STAND
    assert torch.allclose(_t(W), BACK)             # fin armement: BACK
    assert torch.allclose(_t(K), FWD)              # fin frappe: FORWARD
    assert torch.allclose(_t(R), STAND)            # fin retour: STAND
    assert torch.allclose(_t(0.9), STAND)          # repos: STAND


def test_kick_target_midsegments():
    assert torch.allclose(_t(W / 2), 0.5 * BACK)                    # mi-armement
    assert torch.allclose(_t((W + K) / 2), 0.5 * (BACK + FWD))      # mi-frappe
    assert torch.allclose(_t((K + R) / 2), 0.5 * FWD)              # mi-retour


def test_kick_target_batch_shape():
    phase = torch.linspace(0.0, 1.0, 50)
    out = kick_pose_target(phase, STAND, BACK, FWD, W, K, R)
    assert out.shape == (50, 2)
    # chaque composante reste dans l'enveloppe des 3 poses
    lo = torch.minimum(torch.minimum(STAND, BACK), FWD)
    hi = torch.maximum(torch.maximum(STAND, BACK), FWD)
    assert (out >= lo - 1e-6).all() and (out <= hi + 1e-6).all()
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: FAIL — `ImportError: cannot import name 'kick_pose_target'`.

- [ ] **Step 3: Implémenter la fonction pure**

Ajouter dans `mdp.py` juste après `phase_pose_blend` (~ligne 2062) :

```python
def kick_pose_target(
    phase: torch.Tensor,
    stand: torch.Tensor,
    back: torch.Tensor,
    forward: torch.Tensor,
    windup_end: float,
    kick_end: float,
    return_end: float,
) -> torch.Tensor:
    """Cible articulaire interpolée d'un geste de shoot à 4 keyframes.

    phase (B,) ∈ [0,1). stand/back/forward (k,) ou (1,k). Retour (B,k).

    [0, windup_end)        STAND   -> BACK     (armement)
    [windup_end, kick_end) BACK    -> FORWARD  (frappe sèche)
    [kick_end, return_end) FORWARD -> STAND    (retour)
    [return_end, 1.0)      STAND             (repos)
    """
    p = phase.unsqueeze(-1)  # (B,1)

    def interp(a, b, s):
        return a + s * (b - a)

    s1 = (p / windup_end).clamp(0.0, 1.0)
    s2 = ((p - windup_end) / (kick_end - windup_end)).clamp(0.0, 1.0)
    s3 = ((p - kick_end) / (return_end - kick_end)).clamp(0.0, 1.0)

    seg1 = interp(stand, back, s1)
    seg2 = interp(back, forward, s2)
    seg3 = interp(forward, stand, s3)  # à s3=1 (phase>=return_end) => STAND

    out = seg1
    out = torch.where(p >= windup_end, seg2, out)
    out = torch.where(p >= kick_end, seg3, out)
    return out
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: PASS (tous les tests kick_target).

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_shoot.py
git commit -m "feat: kick_pose_target — cible interpolée du geste de shoot (4 keyframes)"
```

---

### Task 3: Rewards de suivi `kick_pose_track` / `kick_pose_track_l1`

**Files:**
- Modify: `src/mjlab_microduck/tasks/mdp.py` (ajouter après `kick_pose_target`)
- Test: `tests/test_shoot.py`

**Interfaces:**
- Consumes: `kick_pose_target` (Task 2).
- Produces:
  - `kick_pose_track(env, command_name="twist", stand_pose=None, back_pose=None, forward_pose=None, std=0.4, windup_end=0.35, kick_end=0.45, return_end=0.75, asset_cfg=_DEFAULT_ASSET_CFG) -> Tensor(B,)` — gaussienne `exp(-((q-cible)/std)²).mean`.
  - `kick_pose_track_l1(env, …mêmes args sauf std) -> Tensor(B,)` — `-(|q-cible|).mean`.
  - Helper `_kick_pose_error(env, asset_cfg, command_name, stand_pose, back_pose, forward_pose, windup_end, kick_end, return_end) -> (cur, target)`.

- [ ] **Step 1: Écrire le test qui échoue (stub-env)**

Ajouter à `tests/test_shoot.py` :

```python
from mjlab_microduck.tasks.mdp import kick_pose_track, kick_pose_track_l1

STAND_D = {"a": 0.0, "b": 0.0}
BACK_D = {"a": 1.0, "b": -1.0}
FWD_D = {"a": -1.0, "b": 2.0}
_IDX = {"a": 0, "b": 1}


class _FakeData:
    def __init__(self, joint_pos):
        self.joint_pos = joint_pos
        self.default_joint_pos = torch.zeros_like(joint_pos)


class _FakeAsset:
    def __init__(self, joint_pos):
        self.data = _FakeData(joint_pos)

    def find_joints(self, names):
        return ([_IDX[names[0]]], names)


class _FakeScene:
    def __init__(self, asset):
        self._a = asset

    def __getitem__(self, name):
        return self._a


class _FakeCmdMgr:
    def __init__(self, cmd):
        self._cmd = cmd

    def get_command(self, name):
        return self._cmd


class _FakeEnv:
    def __init__(self, joint_pos, phase):
        self.scene = _FakeScene(_FakeAsset(joint_pos))
        # cmd = [cos, sin, 0]
        cmd = torch.stack(
            [torch.cos(2 * torch.pi * phase), torch.sin(2 * torch.pi * phase),
             torch.zeros_like(phase)], dim=-1)
        self.command_manager = _FakeCmdMgr(cmd)
        self.device = "cpu"
        self.num_envs = joint_pos.shape[0]


def test_kick_track_perfect_at_stand_phase():
    # phase=0 -> cible STAND=[0,0] ; joint_pos exactement STAND -> reward ~1
    env = _FakeEnv(torch.tensor([[0.0, 0.0]]), torch.tensor([0.0]))
    r = kick_pose_track(env, stand_pose=STAND_D, back_pose=BACK_D, forward_pose=FWD_D)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-4)


def test_kick_track_lower_when_off_target():
    # phase=0.45 (kick_end) -> cible FORWARD=[-1,2] ; joint_pos=STAND -> reward < 0.5
    env = _FakeEnv(torch.tensor([[0.0, 0.0]]), torch.tensor([0.45]))
    r = kick_pose_track(env, stand_pose=STAND_D, back_pose=BACK_D, forward_pose=FWD_D)
    assert (r < 0.5).all()


def test_kick_track_l1_zero_when_perfect():
    env = _FakeEnv(torch.tensor([[0.0, 0.0]]), torch.tensor([0.0]))
    r = kick_pose_track_l1(env, stand_pose=STAND_D, back_pose=BACK_D, forward_pose=FWD_D)
    assert torch.allclose(r, torch.tensor([0.0]), atol=1e-6)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: FAIL — `ImportError: cannot import name 'kick_pose_track'`.

- [ ] **Step 3: Implémenter helper + rewards**

Ajouter dans `mdp.py` après `kick_pose_target` :

```python
def _kick_pose_error(
    env: ManagerBasedRlEnv,
    asset_cfg: SceneEntityCfg,
    command_name: str,
    stand_pose: dict,
    back_pose: dict,
    forward_pose: dict,
    windup_end: float,
    kick_end: float,
    return_end: float,
):
    """(cur, target) pour le geste de shoot, joints résolus PAR NOM.

    Les 3 poses partagent les mêmes clés (14 joints). L'ordre des noms est
    donné par `stand_pose`.
    """
    if not stand_pose:
        raise ValueError("_kick_pose_error requires a non-empty stand_pose dict")
    asset: Entity = env.scene[asset_cfg.name]
    names = list(stand_pose.keys())
    ids = [int(asset.find_joints([n])[0][0]) for n in names]

    def vec(d):
        return torch.tensor([d[n] for n in names], device=env.device,
                            dtype=asset.data.joint_pos.dtype)

    stand_v, back_v, fwd_v = vec(stand_pose), vec(back_pose), vec(forward_pose)

    cmd = env.command_manager.get_command(command_name)
    phase = (torch.atan2(cmd[:, 1], cmd[:, 0]) / (2 * torch.pi)) % 1.0  # (B,)
    target = kick_pose_target(phase, stand_v, back_v, fwd_v,
                              windup_end, kick_end, return_end)          # (B,k)
    cur = asset.data.joint_pos[:, ids]                                   # (B,k)
    return cur, target


def kick_pose_track(
    env: ManagerBasedRlEnv,
    command_name: str = "twist",
    stand_pose: Optional[dict] = None,
    back_pose: Optional[dict] = None,
    forward_pose: Optional[dict] = None,
    std: float = 0.4,
    windup_end: float = 0.35,
    kick_end: float = 0.45,
    return_end: float = 0.75,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Gaussienne sur la pose articulaire vs cible interpolée du shoot.

    Reward directif et symétrique : chaque phase impose la config articulaire
    exacte. Résolution PAR NOM.
    """
    cur, target = _kick_pose_error(
        env, asset_cfg, command_name, stand_pose or {}, back_pose or {},
        forward_pose or {}, windup_end, kick_end, return_end,
    )
    return torch.exp(-((cur - target) / std) ** 2).mean(dim=-1)


def kick_pose_track_l1(
    env: ManagerBasedRlEnv,
    command_name: str = "twist",
    stand_pose: Optional[dict] = None,
    back_pose: Optional[dict] = None,
    forward_pose: Optional[dict] = None,
    windup_end: float = 0.35,
    kick_end: float = 0.45,
    return_end: float = 0.75,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
) -> torch.Tensor:
    """Bootstrap L1 vers la cible interpolée (gradient constant, pénalité<=0)."""
    cur, target = _kick_pose_error(
        env, asset_cfg, command_name, stand_pose or {}, back_pose or {},
        forward_pose or {}, windup_end, kick_end, return_end,
    )
    return -(cur - target).abs().mean(dim=-1)
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `uv run --with pytest pytest tests/test_shoot.py -q`
Expected: PASS (tous les tests, y compris les 3 nouveaux).

- [ ] **Step 5: Commit**

```bash
git add src/mjlab_microduck/tasks/mdp.py tests/test_shoot.py
git commit -m "feat: rewards kick_pose_track + kick_pose_track_l1 (suivi du geste de shoot)"
```

---

### Task 4: Env config `microduck_shoot_env_cfg.py`

**Files:**
- Create: `src/mjlab_microduck/tasks/microduck_shoot_env_cfg.py`
- Test: (via Task 5)

**Interfaces:**
- Consumes: `kick_pose_track`, `kick_pose_track_l1` (Task 3) ; `GroundPickPhaseCommandCfg(randomize_phase=…)` (Task 1) ; `feet_grounded_reward`, `feet_flat_penalty`, `neck_action_rate_l2`, `joint_torques_l2`, `zero_command_padding`, `robot_state_is_nan`, DR events (existants dans `mdp.py`).
- Produces: `make_microduck_shoot_env_cfg(play=False, rough=False) -> ManagerBasedRlEnvCfg` ; `MicroduckShootRlCfg` ; constantes `SHOOT_PERIOD`, `WINDUP_END`, `KICK_END`, `RETURN_END`, `STAND_POSE`, `KICK_BACK_POSE`, `KICK_FWD_POSE`.

- [ ] **Step 1: Partir du fichier ground_pick comme base**

```bash
cp src/mjlab_microduck/tasks/microduck_ground_pick_env_cfg.py \
   src/mjlab_microduck/tasks/microduck_shoot_env_cfg.py
```

Ce fichier fournit déjà TOUT le boilerplate sim2real à conserver tel quel : DR (CoM, head CoM, mass/inertia, friction BAM, armature, IMU misalignment obs-level, encoder-bias, pushes), le bloc obs 61D (`del base_lin_vel` actor, critic base_lin_vel, suppression `foot_height`/`height_scan`, delays/noise, `head_command`/`body_command` zero-padding), la terminaison `nan_state`, les events `expand_bam_friction_fields` / `reset_action_history`, le curriculum action_rate/CoM. On ne modifie que : robot cfg, capteurs, commande, et le bloc rewards.

- [ ] **Step 2: Adapter l'en-tête, le nom de fonction et les constantes**

Remplacer le docstring de tête par une description shoot, et juste avant `def make_microduck_ground_pick_env_cfg`, ajouter les constantes + poses (placeholders — à remplacer par lecture `read_pose.py`). Renommer la fonction en `make_microduck_shoot_env_cfg`.

```python
# ── Timings du geste (phase normalisée [0,1)) ────────────────────────────────
SHOOT_PERIOD = 2.5   # s — durée d'un cycle (doit matcher --ground-pick-period au déploiement)
WINDUP_END = 0.35    # STAND -> BACK
KICK_END = 0.45      # BACK -> FORWARD (segment court = frappe sèche)
RETURN_END = 0.75    # FORWARD -> STAND, puis repos jusqu'à 1.0

# ── Poses (rad, 14 joints, mouth exclu) ──────────────────────────────────────
# Convention: jambe droite frappe (hanche/genou droit actifs), gauche en appui.
# STAND_POSE = pose HOME du sim (HOME_FRAME / default_joint_pos) pour que φ=0
# coïncide avec la config de reset (invariant randomize_phase=False). BACK/FWD
# sont des PLACEHOLDERS jambe droite, à affiner via read_pose.py.
STAND_POSE = {
    "left_hip_yaw": 0.0, "left_hip_roll": -0.0873, "left_hip_pitch": -0.4579,
    "left_knee": -0.0049, "left_ankle": 0.4530,
    "neck_pitch": 0.3491, "head_pitch": 0.3491, "head_yaw": 0.0, "head_roll": 0.0,
    "right_hip_yaw": 0.0, "right_hip_roll": 0.0873, "right_hip_pitch": 0.4579,
    "right_knee": 0.0049, "right_ankle": -0.4530,
}
KICK_BACK_POSE = {  # armement: hanche droite en extension arrière + genou fléchi
    **STAND_POSE,
    "right_hip_pitch": -0.6,
    "right_knee": 0.8,
    "right_ankle": -0.2,
}
KICK_FWD_POSE = {  # frappe: hanche droite fléchie avant + genou tendu
    **STAND_POSE,
    "right_hip_pitch": 0.7,
    "right_knee": -0.1,
    "right_ankle": 0.1,
}
```

> NOTE au releveur de poses : remplacer ces valeurs par des lectures `read_pose.py` (couple coupé, robot posé à la main dans chaque position). Garder les 14 clés identiques dans les 3 dicts.

- [ ] **Step 3: Robot cfg et import**

Dans les imports, remplacer `MICRODUCK_GROUND_PICK_ROBOT_CFG` par `MICRODUCK_WALK_ROBOT_CFG` :

```python
from mjlab_microduck.robot.microduck_constants import MICRODUCK_WALK_ROBOT_CFG
```

Dans la fonction, la ligne d'entités :

```python
    cfg.scene.entities = {"robot": MICRODUCK_WALK_ROBOT_CFG}
```

- [ ] **Step 4: Capteurs — garder self_collision, remplacer les capteurs pied**

Remplacer la définition du capteur `feet_ground_contact` (2 pieds) par un capteur **pied gauche seul** (appui), et SUPPRIMER le capteur `head_impact_cfg` (inutile ici). Le capteur `self_collision_cfg` reste.

```python
    left_foot_ground_cfg = ContactSensorCfg(
        name="left_foot_ground_contact",
        primary=ContactMatch(
            mode="geom",
            pattern=r"^left_foot_collision$",
            entity="robot",
        ),
        secondary=ContactMatch(mode="body", pattern="terrain"),
        fields=("found", "force"),
        reduce="netforce",
        num_slots=1,
        track_air_time=True,
    )
```

Et la ligne des capteurs de scène :

```python
    cfg.scene.sensors = (left_foot_ground_cfg, self_collision_cfg)
```

Supprimer la définition de `head_impact_cfg` et toute référence (le reward `head_impact_penalty` est retiré au Step 6).

- [ ] **Step 5: Commande de phase (randomize_phase=False, période shoot)**

Remplacer le bloc commande (celui qui crée `GroundPickPhaseCommandCfg`) par :

```python
    command: UniformVelocityCommandCfg = cfg.commands["twist"]
    command.rel_standing_envs = 0.0
    command.rel_heading_envs = 0.0
    cfg.commands["twist"] = microduck_mdp.GroundPickPhaseCommandCfg(
        **{**vars(command), "class_type": microduck_mdp.GroundPickPhaseCommand}
    )
    cfg.commands["twist"].period = SHOOT_PERIOD
    cfg.commands["twist"].randomize_phase = False
```

- [ ] **Step 6: Rewards — retirer ground_pick, ajouter shoot**

Supprimer les rewards spécifiques ground_pick : `mouth_ground_proximity`, `mouth_perpendicular_to_ground`, `ground_pick_return_pose_legs`, `ground_pick_return_pose_neck`, `feet_grounded` (les 2 pieds), `head_impact_penalty`. Remplacer par le bloc shoot :

```python
    # ── Objectif : suivi de la pose interpolée du shoot ───────────────────────
    _pose_params = {
        "command_name": "twist",
        "stand_pose": STAND_POSE,
        "back_pose": KICK_BACK_POSE,
        "forward_pose": KICK_FWD_POSE,
        "windup_end": WINDUP_END,
        "kick_end": KICK_END,
        "return_end": RETURN_END,
    }
    cfg.rewards["kick_pose_track"] = RewardTermCfg(
        func=microduck_mdp.kick_pose_track,
        weight=6.0,
        params={**_pose_params, "std": 0.4},
    )
    cfg.rewards["kick_pose_l1"] = RewardTermCfg(
        func=microduck_mdp.kick_pose_track_l1,
        weight=2.0,
        params=dict(_pose_params),
    )

    # ── Équilibre / appui (jambe unique) ──────────────────────────────────────
    cfg.rewards["upright"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["upright"].weight = 2.0
    cfg.rewards["body_ang_vel"].params["asset_cfg"].body_names = ("trunk_base",)
    cfg.rewards["body_ang_vel"].weight = -0.05

    # Pied GAUCHE planté (appui). feet_grounded_reward avec un capteur mono-pied
    # -> found ∈ {0,1} -> reward ∈ {0,0.5} ; poids 6.0 => contribution max ~3.0.
    cfg.rewards["support_foot_grounded"] = RewardTermCfg(
        func=microduck_mdp.feet_grounded_reward,
        weight=6.0,
        params={"sensor_name": left_foot_ground_cfg.name},
    )

    # Pied gauche à plat.
    cfg.rewards["feet_flat_left"] = RewardTermCfg(
        func=microduck_mdp.feet_flat_penalty,
        weight=-1.0,
        params={"asset_cfg": SceneEntityCfg("robot", site_names=("left_foot",))},
    )

    cfg.rewards["self_collisions"] = RewardTermCfg(
        func=mdp.self_collision_cost,
        weight=-1.0,
        params={"sensor_name": self_collision_cfg.name},
    )
```

- [ ] **Step 7: Régularisation allégée (laisser passer le snap)**

Le fichier ground_pick met `action_rate_l2=-2.0`, `neck_action_rate_l2=-1.0`, `joint_torques_l2=-5e-3` + un curriculum action_rate qui finit à -2.0. Pour le shoot on allège. Remplacer ces 3 blocs par :

```python
    cfg.rewards["action_rate_l2"] = RewardTermCfg(
        func=mdp.action_rate_l2, weight=-0.5
    )
    cfg.rewards["neck_action_rate_l2"] = RewardTermCfg(
        func=microduck_mdp.neck_action_rate_l2, weight=-0.5
    )
    cfg.rewards["joint_torques_l2"] = RewardTermCfg(
        func=microduck_mdp.joint_torques_l2, weight=-1e-3
    )
```

Et alléger le curriculum action_rate (garder la structure, viser -0.5) :

```python
    cfg.curriculum["action_rate_weight"] = CurriculumTermCfg(
        func=microduck_mdp.reward_weight,
        params={
            "reward_name": "action_rate_l2",
            "weight_stages": [
                {"step": 0,        "weight": -0.2},
                {"step": 250 * 24, "weight": -0.4},
                {"step": 500 * 24, "weight": -0.5},
            ],
        },
    )
```

- [ ] **Step 8: Reset — hauteur de station debout**

Garder la **hauteur debout** `(0.12, 0.13)` — c'est la valeur de l'env velocity
(marche) ET de ground_pick. ⚠️ Ce n'est PAS un offset additif « station accroupie » :
le `pos` racine par défaut de `InitialStateCfg` est (0,0,0), donc la hauteur de reset
est z ∈ [0.12, 0.13] m **absolue** = debout (aucune chute). Vérifier/mettre :

```python
    cfg.events["reset_base"].params["pose_range"]["z"] = (0.12, 0.13)
```

(Ne PAS injecter de vitesse d'entrée — c'est un shoot debout, pas de glisse.)

- [ ] **Step 9: Renommer la RlCfg**

En bas du fichier, renommer `MicroduckGroundPickRlCfg` en `MicroduckShootRlCfg` et changer les noms d'expérience :

```python
MicroduckShootRlCfg = RslRlOnPolicyRunnerCfg(
    # … (garder actor/critic/algorithm identiques) …
    wandb_project="mjlab_microduck",
    experiment_name="shoot",
    run_name="shoot",
    save_interval=250,
    num_steps_per_env=24,
    max_iterations=20_000,
)
```

- [ ] **Step 10: Vérifier que le module s'importe**

Run: `uv run python -c "from mjlab_microduck.tasks.microduck_shoot_env_cfg import make_microduck_shoot_env_cfg, MicroduckShootRlCfg; print('ok')"`
Expected: `ok` (pas d'ImportError / NameError — en particulier plus aucune référence à `head_impact_cfg`, `MICRODUCK_GROUND_PICK_ROBOT_CFG`, ni aux rewards ground_pick supprimés).

- [ ] **Step 11: Commit**

```bash
git add src/mjlab_microduck/tasks/microduck_shoot_env_cfg.py
git commit -m "feat: env config Mjlab-Shoot (geste de shoot par suivi de poses)"
```

---

### Task 5: Enregistrement + test d'intégration

**Files:**
- Modify: `src/mjlab_microduck/tasks/__init__.py`
- Test: `tests/test_shoot_cfg.py`

**Interfaces:**
- Consumes: `make_microduck_shoot_env_cfg`, `MicroduckShootRlCfg` (Task 4).
- Produces: tâche enregistrée `Mjlab-Shoot-Flat-MicroDuck`.

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

Créer `tests/test_shoot_cfg.py` :

```python
from mjlab_microduck.tasks.microduck_shoot_env_cfg import (
    make_microduck_shoot_env_cfg,
    STAND_POSE, KICK_BACK_POSE, KICK_FWD_POSE, SHOOT_PERIOD,
)
from mjlab_microduck.tasks import mdp as microduck_mdp


def test_poses_have_same_14_keys():
    assert set(STAND_POSE) == set(KICK_BACK_POSE) == set(KICK_FWD_POSE)
    assert len(STAND_POSE) == 14
    assert "mouth" not in STAND_POSE


def test_shoot_cfg_builds_with_phase_command():
    cfg = make_microduck_shoot_env_cfg()
    twist = cfg.commands["twist"]
    assert isinstance(twist, microduck_mdp.GroundPickPhaseCommandCfg)
    assert twist.randomize_phase is False
    assert twist.period == SHOOT_PERIOD


def test_shoot_cfg_has_kick_rewards_and_no_walking():
    cfg = make_microduck_shoot_env_cfg()
    assert "kick_pose_track" in cfg.rewards
    assert "kick_pose_l1" in cfg.rewards
    assert "support_foot_grounded" in cfg.rewards
    for gone in ("track_linear_velocity", "track_angular_velocity",
                 "mouth_ground_proximity", "ground_pick_return_pose_legs"):
        assert gone not in cfg.rewards
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `uv run --with pytest pytest tests/test_shoot_cfg.py -q`
Expected: PASS possible sur les tests de poses, mais l'ensemble doit être vert seulement une fois l'env construit sans erreur ; si `make_...` lève, FAIL. (À ce stade l'import du fichier fonctionne déjà via Task 4.)

- [ ] **Step 3: Enregistrer la tâche**

Dans `src/mjlab_microduck/tasks/__init__.py`, après le bloc d'import ground_pick (~ligne 50), ajouter :

```python
from .microduck_shoot_env_cfg import (
    make_microduck_shoot_env_cfg,
    MicroduckShootRlCfg,
)
```

Après le bloc `register_mjlab_task` de GroundPick-Rough (~ligne 161), ajouter :

```python
register_mjlab_task(
    task_id="Mjlab-Shoot-Flat-MicroDuck",
    env_cfg=make_microduck_shoot_env_cfg(),
    play_env_cfg=make_microduck_shoot_env_cfg(play=True),
    rl_cfg=MicroduckShootRlCfg,
    runner_cls=MicroduckOnPolicyRunner,
)
print("✓ Shoot task registered: Mjlab-Shoot-Flat-MicroDuck")
```

- [ ] **Step 4: Lancer tout, vérifier le succès**

Run: `uv run --with pytest pytest tests/ -q`
Expected: PASS (test_shoot.py + test_shoot_cfg.py + tests existants).

- [ ] **Step 5: Vérifier l'enregistrement de la tâche**

Run: `uv run python -c "import mjlab_microduck.tasks"`
Expected: la sortie contient `✓ Shoot task registered: Mjlab-Shoot-Flat-MicroDuck`.

- [ ] **Step 6: Commit**

```bash
git add src/mjlab_microduck/tasks/__init__.py tests/test_shoot_cfg.py
git commit -m "feat: enregistre Mjlab-Shoot-Flat-MicroDuck + test d'intégration"
```

---

## Après implémentation (hors plan TDD)

1. **Relever les vraies poses** avec `read_pose.py` (STAND, PIED_ARRIÈRE, PIED_AVANT), remplacer les placeholders dans `microduck_shoot_env_cfg.py`.
2. **Entraîner** : `uv run train Mjlab-Shoot-Flat-MicroDuck --env.scene.num-envs 4096 --agent.max_iterations 8000`. Surveiller `Episode_Reward/kick_pose_track` (doit monter).
3. **Play** : script play_latest ; vérifier l'équilibre sur le pied gauche pendant la frappe.
4. **Export ONNX** + déploiement dans un slot phase (`--ground-pick shoot.onnx --ground-pick-period 2.5 --ground-pick-kp-ratio 1.0`).
5. **Réglages probables** : période/timings (snap), poids `action_rate`, et éventuel reward « vitesse pied vers l'avant » (segment frappe) si le suivi manque de punch.

## Self-review — couverture de la spec

- Fichier & enregistrement → Tasks 4, 5. ✅
- Poses placeholders 14 joints → Task 4 Step 2, testé Task 5. ✅
- Commande de phase + `randomize_phase=False` + période → Tasks 1, 4 Step 5, testé Task 5. ✅
- `kick_pose_target` + `kick_pose_track` + `kick_pose_track_l1` → Tasks 2, 3. ✅
- Équilibre/appui (upright, pied gauche planté, feet_flat gauche, self_collisions, body_ang_vel) → Task 4 Step 6. ✅
- Régularisation allégée → Task 4 Step 7. ✅
- Obs 61D parité (hérité ground_pick, conservé) → Task 4 Step 1. ✅
- Tests pures + cfg → Tasks 2, 3, 5. ✅
