# Ground-pick par suivi de pose interpolée par la phase

**Date** : 2026-07-24
**Branche** : `new_pre_alpha_ground_pick`
**Fichier cible** : `src/mjlab_microduck/tasks/microduck_ground_pick_env_cfg.py` (réécriture en place)
**Task id** : `Mjlab-GroundPick-Flat-MicroDuck` (inchangé)

## 1. Objectif

Remplacer l'objectif *espace-tâche* du ground_pick actuel (récompense la bouche
qui descend au sol, puis récompense séparément le retour debout) par un objectif
**directif de suivi de pose** : on définit deux poses articulaires cibles — STAND
et DOWN — et on récompense le suivi de la **pose interpolée par la phase**
(STAND→DOWN→STAND).

Motivation (reprise de l'approche roller_crouch, validée) : l'objectif par pose
interpolée est **symétrique par construction** — le « se relever » (cible → STAND)
est récompensé exactement comme le « se baisser » (cible → DOWN), ce qui règle le
problème d'optimum paresseux où la policy descend mais remonte mal. Le signal est
**dense à chaque phase** (cible qui bouge en continu), contrairement à une cible
fixe pondérée par `sin` qui ne donne aucun signal aux transitions.

Le geste reste déclenché au **bouton A** via le slot `--ground-pick` du runtime
(one-shot, retour auto à la policy principale). Obs 61D unifié inchangé →
policy interchangeable dans le slot.

## 2. Poses cibles

Résolution des joints **PAR NOM** (`asset.find_joints([name])`) — robuste, cohérent
avec l'approche roller. 14 joints (mouth exclu).

- **STAND_POSE** = HOME (`default_joint_pos` du modèle). Source du blend ; ne pas
  la redéfinir en dur — utiliser le défaut du modèle comme source (blend=0).
  Au déploiement, la policy principale reprend depuis HOME → retour propre.

- **DOWN_POSE** = valeurs initiales issues du **keyframe FOLD** de `scene_walk.xml`
  (pli avant profond, tête baissée → bouche vers le sol). Dict par nom en tête de
  fichier, **commenté comme remplaçable par une lecture `read_pose.py`** du vrai
  robot posé bouche-au-sol. Valeurs de départ :

  ```python
  DOWN_POSE = {
      "left_hip_yaw": 0.0, "left_hip_roll": 0.0, "left_hip_pitch": 1.57,
      "left_knee": 1.57, "left_ankle": 0.0,
      "neck_pitch": 1.0, "head_pitch": 1.0, "head_yaw": 0.0, "head_roll": 0.0,
      "right_hip_yaw": 0.0, "right_hip_roll": 0.0, "right_hip_pitch": -1.57,
      "right_knee": -1.57, "right_ankle": 0.0,
  }
  ```

## 3. Profil de phase (4 segments)

Commande `GroundPickPhaseCommand` : `[cos(2πφ), sin(2πφ), 0]`, période **4.0 s**
(défaut du slot runtime → pas de flag période à changer au déploiement).

```
DESCENT_END=0.15  HOLD_END=0.50  RISE_END=0.65   (période 4 s)
[0, 0.15)     descente  STAND->DOWN   ~0.6 s   blend 0->1
[0.15, 0.50)  bas       DOWN          ~1.4 s   blend 1
[0.50, 0.65)  remontée  DOWN->STAND   ~0.6 s   blend 1->0
[0.65, 1.0)   haut      STAND (repos) ~1.4 s   blend 0
```

`blend ∈ [0,1]` : 0 = STAND (HOME), 1 = DOWN. Cible = `stand + blend·(down - stand)`.
Bornes tunables (constantes en tête de fichier).

**`randomize_phase=False`** : chaque épisode démarre à φ=0 (= debout), comme le
déclenchement bouton A au déploiement. Les épisodes se réinitialisant à des
instants échelonnés, les envs se décorrèlent naturellement en phase (pas besoin de
randomiser). Nécessite d'ajouter un flag `randomize_phase` à
`GroundPickPhaseCommandCfg` (défaut `True` → autres tâches sit/stand inchangées),
honoré dans `reset()`.

## 4. Nouvelles fonctions mdp (portées de roller, adaptées, par nom)

Dans `src/mjlab_microduck/tasks/mdp.py`. Noms distincts du `phase_pose_match`
existant (qui est la variante cible-fixe-pondérée-sin) pour éviter la confusion.

- **`phase_pose_blend(phase, descent_end, hold_end, rise_end) -> Tensor`** — pur,
  blend 4 segments 0..1 (testable en isolation).
- **`_phase_pose_error(env, asset_cfg, command_name, target_pose, descent_end,
  hold_end, rise_end, source_pose=None) -> (cur, target)`** — résout les joints par
  nom ; `source_pose` = HOME (`default_joint_pos`) si `None` ; calcule
  `phase = atan2(sin,cos)/2π % 1`, `blend`, puis `target = source + blend·(target_pose - source)`.
- **`phase_pose_track(env, command_name, target_pose, source_pose=None, std=0.3,
  descent_end, hold_end, rise_end, asset_cfg) -> Tensor`** — gaussienne
  `exp(-((cur-target)/std)²).mean(-1)`.
- **`phase_pose_track_l1(env, ...même args sans std...) -> Tensor`** — bootstrap
  `-(cur-target).abs().mean(-1)` (gradient constant quand la gaussienne sature).

`target_pose` = `DOWN_POSE` (dict par nom). `source_pose=None` → HOME.

## 5. Rewards

Réécriture minimale par rapport à l'actuel — on remplace la mécanique de retour de
pose, on garde la stabilité/régul/sim2real.

| Reward | Poids | Statut | Rôle |
|---|---|---|---|
| `phase_pose_track` (std 0.3) | **6.0** | **NOUVEAU** | suivi pose interpolée STAND↔DOWN |
| `phase_pose_track_l1` | **2.0** | **NOUVEAU** | bootstrap L1 |
| `mouth_ground_proximity` (std 0.10) | **1.0** | retune (était 2.0) | filet : garantit la bouche au sol si DOWN imparfaite ; gaté approche (+sin) |
| `upright` | 0.2 | gardé | tronc ~vertical (faible, le robot penche) |
| `feet_grounded` | 3.0 | gardé | 2 pieds au sol pendant tout le geste |
| `self_collisions` | -1.0 | gardé | |
| `head_impact_penalty` (seuil 2 N) | -0.5 | gardé | pas de slam tête (DOWN amène la tête bas) |
| `action_rate_l2` | -0.8→-2.0 (curric) | gardé | lissage |
| `neck_action_rate_l2` | -1.0 | gardé | |
| `joint_torques_l2` | -5e-3 | gardé | |
| `body_ang_vel` | -0.05 | gardé | |
| `angular_momentum` | -0.02 | gardé | |
| `soft_landing` | -1e-5 | gardé | |

**Retirées** : `mouth_perpendicular_to_ground`, `ground_pick_return_pose_legs`,
`ground_pick_return_pose_neck` (remplacées par le suivi de pose).

Tout le reste **inchangé** : bloc DR (CoM/head-CoM/mass-inertia/friction/armature/
IMU-misalign/encoder-bias/pushes), obs 61D + padding head/body zéro, terminaisons
(`nan_state`), curricula (`action_rate_weight`, `com_range`, `head_com_range`),
RlCfg (`experiment_name="ground_pick"`).

## 6. Déploiement (parité sim2real)

```bash
microduck_runtime ... \
  --ground-pick ground_pick.onnx \
  --ground-pick-period 4.0 \       # = période env (défaut, rien à changer)
  --ground-pick-kp-ratio 1.0 \     # entraîné kp 200 → forcer 1.0 (défaut 0.6 baisse à 120)
  --ground-pick-action-scale 1.0   # = action.scale env
```

## 7. Tests

`tests/` (lancer `uv run --with pytest pytest tests/ -q`) :

- **Fonctions pures** : `phase_pose_blend` aux points clés
  (φ=0→0, φ=0.075→0.5, φ=0.3→1, φ=0.575→0.5, φ=0.8→0, monotone par segment) ;
  `phase_pose_track`/`_l1` : valeur max (cur==target) et signe.
- **Construction de l'env** : `make_microduck_ground_pick_env_cfg()` construit ;
  commande = `GroundPickPhaseCommand` avec `randomize_phase=False`, `period=4.0` ;
  rewards `phase_pose_track`/`phase_pose_track_l1` présents ;
  `mouth_perpendicular_to_ground`/`ground_pick_return_pose_*` absents ;
  `mouth_ground_proximity` présent poids 1.0.

## 8. Entraînement / play / export

```bash
uv run train Mjlab-GroundPick-Flat-MicroDuck --env.scene.num-envs 4096 --agent.max_iterations 20000
uv run scripts/play_latest.py     # md-play
uv run scripts/export_latest.py   # normaliseur baké dans l'ONNX
```
Surveiller `Episode_Reward/phase_pose_track` (doit monter).

## 9. Hors scope / notes

- **Doublon `pose_target_match`** (mdp.py 1577 et 1914) : latent, non traité ici.
- **Ajustement DOWN_POSE** : si la bouche ne touche pas assez le sol avec les
  valeurs FOLD, ajuster le dict (idéalement lecture `read_pose.py` du vrai robot
  posé bouche-au-sol) plutôt que de gonfler `mouth_ground_proximity`.
- **Transition au déploiement** : STAND=HOME = neutre de la policy principale →
  pas d'à-coup au retour (contrairement au souci noté sur roller où STAND≠HOME).
