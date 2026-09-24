# Spec — Tâche RL « shoot dans une balle » par suivi de poses

**Date** : 2026-07-24
**Branche** : `new_pre_alpha_ground_pick`
**Task id** : `Mjlab-Shoot-Flat-MicroDuck`

## Objectif

Apprendre un geste de **shoot one-shot** (frappe dans une balle) par **suivi d'une
trajectoire de poses articulaires à 4 keyframes** interpolée par la phase :

```
STAND → PIED_ARRIÈRE (armement) → PIED_AVANT (frappe) → STAND (repos)
```

- **Jambe droite** frappe, **jambe gauche** en appui.
- **Aucune balle simulée** : on apprend le *geste* par suivi de poses (comme
  `ground_pick` / crouch). Si une vraie balle est devant le robot au déploiement,
  elle se fait frapper.
- Obs **61D unifiée** identique aux autres policies microduck → l'ONNX exporté se
  déploie tel quel dans un **slot bouton** du runtime (one-shot : joue le geste
  puis rend la main à la policy principale).

Même moule que la tâche `ground_pick` de cette branche (phase encodée `[cos, sin, 0]`
dans le slot twist, suivi de pose par phase, obs 61D, DR sim2real héritée de velocity).

## Non-objectifs (YAGNI)

- Pas de balle physique, pas de reward de contact/vitesse de balle.
- Pas de côté configurable (droite uniquement ; gauche = symétrisable plus tard si besoin).
- Pas de marche / récupération de chute : tous les termes de locomotion sont retirés.

## Architecture

### Fichier & enregistrement
- `src/mjlab_microduck/tasks/microduck_shoot_env_cfg.py`
  - `make_microduck_shoot_env_cfg(play: bool = False, rough: bool = False) -> ManagerBasedRlEnvCfg`
  - `MicroduckShootRlCfg` (RslRlOnPolicyRunnerCfg, `experiment_name="shoot"`)
- Enregistrement dans `src/mjlab_microduck/tasks/__init__.py` :
  `Mjlab-Shoot-Flat-MicroDuck` (variante `-Rough-` optionnelle).
- Base : hérite de l'env velocity (via `make_velocity_env_cfg` comme ground_pick),
  puis strip agressif de tout ce qui est locomotion.
- Robot : `MICRODUCK_WALK_ROBOT_CFG` (marche standard, 14 joints, pas de rollers).
- `action.scale = 1.0`.

### Poses (placeholders → lues sur le vrai robot via `read_pose.py`)
Dicts `{nom_joint: rad}`, **14 joints** (mouth exclu). Sommet du fichier env.
- `STAND_POSE` : station neutre (~HOME du sim).
- `KICK_BACK_POSE` : hanche droite en **extension arrière** + genou droit fléchi
  (armement) ; jambe gauche + cou ≈ HOME.
- `KICK_FWD_POSE` : hanche droite **fléchie avant** + genou droit tendu (frappe) ;
  jambe gauche + cou ≈ HOME.

Placeholders plausibles au départ (ajustables), à remplacer par les lectures réelles.

### Commande & phase
- Réutilise `GroundPickPhaseCommand` : `command = [cos(2π·φ), sin(2π·φ), 0]` dans le
  slot twist.
- **Période** : `SHOOT_PERIOD ≈ 2.5 s` (configurable via `cfg.period`).
- **Nouveau flag `randomize_phase`** sur `GroundPickPhaseCommandCfg` /
  `GroundPickPhaseCommand` :
  - Défaut `True` (non-cassant : `ground_pick` garde le comportement actuel).
  - Shoot le met à `False` → `reset()` remet φ=0 au lieu de `rand()`.
  - Raison : chaque épisode démarre au STAND (état du robot = `default_joint_pos`)
    avec φ=0 = cible STAND → cohérence état/cible au reset (sinon la policy est
    sommée d'être instantanément en pose « frappe » depuis une station immobile).
  - **Invariant de cohérence** : `STAND_POSE` DOIT égaler la pose articulaire de
    reset du sim (`HOME_FRAME` / `default_joint_pos`, non nulle : hip_pitch ±0.4579,
    ankle ±0.4530, hip_roll ±0.0873, neck/head_pitch 0.3491). Vérifié par
    `test_stand_pose_matches_home_standing_pose`. Les placeholders initialement à
    zéro cassaient cet invariant (corrigé après revue finale).

### Reset (hauteur debout, pas d'élan)
- `reset_base.pose_range.z = (0.12, 0.13)` — **hauteur debout absolue** (le `pos`
  racine par défaut de `InitialStateCfg` est (0,0,0), donc z de reset = 0.12–0.13 m,
  pas un offset additif ; valeur identique à l'env velocity qui marche). Pas de chute.
- **Pas d'injection de vitesse d'entrée** (shoot debout, contrairement au crouch-glide).

### Rewards hérités non listés
La table ci-dessus n'est pas exhaustive : l'env hérite de velocity quelques
régularisateurs génériques de faible poids non spécifiques au shoot —
`angular_momentum` (-0.02), `dof_pos_limits` — conservés (stabilité, négligeables).
⚠️ `soft_landing` (reward de marche) est **retiré** : il lit le capteur 2-pieds
`feet_ground_contact` supprimé au profit du capteur pied gauche → KeyError au 1er step
sinon, et il est inerte pour un shoot debout.

### Gotcha renommage capteur (⚠️)
Renommer le capteur pied (`feet_ground_contact` → `left_foot_ground_contact`) casse
tout ce que l'héritage velocity/ground_pick référence par ce nom. À traiter :
- **obs critic** `foot_air_time`/`foot_contact`/`foot_contact_forces` → repointés vers
  le capteur pied gauche (le critic garde l'info d'appui ; sinon KeyError à la
  construction de l'env).
- **reward** `soft_landing` → retiré (voir ci-dessus ; sinon KeyError au 1er step).
Toujours valider par une construction live + **au moins un `step()`** (le reward
manager ne tourne qu'au step), pas seulement le build de cfg ni les tests unitaires.

### ⚠️ Transfert de poids appris (révision post-1er entraînement)
Constat : les poses BACK/FWD relevées **robot tenu à la main (appui bipède)** gardent le
CoM **centré entre les deux pieds** (~4-5 cm à l'intérieur du pied gauche) à toutes les
phases. Avec `upright` imposé, dès que le pied droit se lève le robot bascule → aucune
policy ne peut tenir (géométrique, pas du tuning). Vérifié en sim (CoM vs sites pieds).

Fix retenu (RL apprend l'équilibre) :
- `mdp.com_over_support_foot` : reward gaussien (std 4 cm) tirant la projection du CoM
  (`root_com_pos_w`) vers le pied d'appui, **gaté** par `mdp.kick_engagement` (0 au repos
  STAND, 1 pendant la frappe). Poids 3.0.
- **suivi de pose scindé** (param `joint_names` sur `kick_pose_track`/`_l1`) :
  GESTE = jambe droite + cou/tête (std 0.35, serré) ; APPUI = jambe gauche (std 0.9,
  poids 1.0, **lâche**) → la policy peut adducter/décaler le bassin pour transférer le
  poids sans que le suivi fige le bassin centré.
La table « Équilibre / appui » ci-dessus est donc étendue : `support_leg_pose` (1.0),
`com_over_support` (3.0) s'ajoutent, et `kick_pose_track`/`kick_pose_l1` ne portent plus
que sur les 9 joints du geste (droite+cou).

### Objectif : suivi de la pose interpolée par la phase
Nouvelle fonction **pure** dans `mdp.py` :
```python
kick_pose_target(phase, stand, back, forward, windup_end, kick_end, return_end) -> Tensor
```
Interpole entre les vecteurs de pose selon 4 segments (période normalisée [0,1)) :
```
[0, windup_end)        STAND   → BACK      (armement,     défaut 0.35)
[windup_end, kick_end) BACK    → FORWARD   (frappe sèche, défaut 0.10 = "snap")
[kick_end, return_end) FORWARD → STAND     (retour,       défaut 0.30)
[return_end, 1.0)      STAND              (repos)
```
Le « snap » vient du segment frappe court : la cible articulaire bouge vite → swing
rapide du pied. Les 3 bornes de timing sont paramétrables.

Résolution des joints **par nom** (`asset.find_joints([name])`) — robuste à l'ordre.

Rewards de suivi (toujours actifs, symétriques comme crouch) :
| Reward | Poids | Rôle |
|---|---|---|
| `kick_pose_tracking` | 6.0 | suivi gaussien `exp(-((q-cible)/std)²).mean`, std=0.4 |
| `kick_pose_l1` | 2.0 | bootstrap L1 (gradient constant tôt) |

### Équilibre / appui (jambe unique = risque de bascule)
| Reward | Poids | Rôle |
|---|---|---|
| `upright` | 2.0 | tronc vertical |
| `support_foot_grounded` (pied gauche) | 6.0 | garder le pied d'appui planté (capteur mono-pied → `found∈{0,1}` → reward∈{0,0.5} après `/2`, donc poids 6.0 ≈ contribution max 3.0) |
| `feet_flat` (gauche) | -1.0 | lame gauche à plat |
| `self_collisions` | -1.0 | |
| `body_ang_vel` | -0.05 | |

`support_foot_grounded` : réutiliser le mécanisme `feet_grounded_reward` du
ground_pick mais restreint au **pied gauche** (capteur de contact sur
`left_foot_collision`).

### Régularisation (allégée vs ground_pick — laisser passer le snap)
| Reward | Poids | Rôle |
|---|---|---|
| `action_rate_l2` | -0.5 | léger : un poids lourd tuerait la frappe rapide |
| `neck_action_rate_l2` | -0.5 | tête stable |
| `joint_torques_l2` | -1e-3 | |

**Retirés** (termes de marche) : `track_linear_velocity`, `track_angular_velocity`,
`air_time`, `foot_clearance`, `foot_swing_height`, `foot_slip`, `pose`.

### Observations / déploiement (parité)
- Obs **61D identique** à ground_pick/roller : `[gyro(3), projected_gravity(3),
  joint_pos(14), joint_vel(14), last_action(14), command(13)]` avec les slots
  head(4)+body(6) **zero-paddés** (`zero_command_padding`).
- Même DR sim2real héritée de velocity (CoM, mass/inertia, friction BAM, armature,
  IMU misalignment obs-level, encoder-bias, pushes ±0.3), termine par NaN guard.
- Export ONNX (normaliseur baké) via le script d'export existant.
- Déploiement dans un slot phase du runtime, p.ex. :
  ```
  --ground-pick shoot.onnx --ground-pick-period 2.5 \
  --ground-pick-kp-ratio 1.0 --ground-pick-action-scale <match>
  ```
  Bouton → shoot → retour auto à la policy principale.

## Tests

- `tests/test_shoot.py` — fonctions pures :
  - `kick_pose_target` aux keypoints : STAND à φ=0, BACK à `windup_end`,
    FORWARD à `kick_end`, STAND dans le segment repos ; interpolation à mi-segment ;
    bornes (chaque composante entre min/max des poses).
  - Valeurs des rewards `kick_pose_tracking` / `kick_pose_l1` sur cas simples.
- `tests/test_shoot_cfg.py` — l'env se construit avec la bonne commande
  (`GroundPickPhaseCommand`, `randomize_phase=False`, période) et les rewards
  attendus présents / termes de marche absents.
- Lancer : `uv run --with pytest pytest tests/ -q`.

## Entraînement

```bash
uv run train Mjlab-Shoot-Flat-MicroDuck --env.scene.num-envs 4096 --agent.max_iterations <N>
```
Surveiller `Episode_Reward/kick_pose_tracking` (doit monter). Play : script play_latest.

## Points ouverts / à régler à l'entraînement
- **Timings** (windup/kick/return) et **période** : défauts snap raisonnables,
  à ajuster selon la vitesse de pied obtenue et la stabilité.
- **Poids `action_rate`** : tension snap vs lissage sim2real ; démarrer léger (-0.5).
- **Enrichissement optionnel (non retenu pour v1)** : petit reward de « vitesse du
  pied droit vers l'avant » gaté sur le segment frappe, pour pousser la puissance
  sans balle simulée. À ajouter seulement si le suivi de pose seul manque de punch.
- **Transitions au déploiement** : si `STAND_POSE` ≠ neutre de la policy principale,
  léger à-coup au déclenchement/retour (comme noté pour crouch).
