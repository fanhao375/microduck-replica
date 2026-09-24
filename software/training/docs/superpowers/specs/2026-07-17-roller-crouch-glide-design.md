# Design — Roller Crouch-Glide (« s'accroupir en glissant » au bouton)

**Date :** 2026-07-17
**Statut :** conception validée, prêt pour le plan d'implémentation

## Contexte

Le robot microduck sait patiner (policy roller, tâche `Mjlab-Velocity-Flat-MicroDuck-Rollers`).
On veut un nouveau geste : sur un appui bouton, il **s'accroupit et continue de glisser
sur son élan** (comme un patineur en position basse), maintient ~1 s, puis **se relève**
tout seul et reprend le patinage.

Contrainte forte de l'utilisatrice : **ne pas modifier le runtime Rust**
(`apirrone/microduck_runtime`, installé en binaire). Le geste doit donc réutiliser un
mécanisme déjà présent dans le runtime.

**Découverte clé :** le runtime a déjà un slot « comportement one-shot déclenché au
bouton » : `--ground-pick`. Il est déclenché par le **bouton A** (front montant),
joue une policy ONNX pilotée par une **phase** pendant une durée fixe, puis revient
automatiquement à la policy principale. Surtout, il utilise **exactement le même
layout d'observation 61D** que la policy roller — les deux sont interchangeables au
runtime. C'est le véhicule idéal, sans une ligne de Rust.

Compromis accepté : le geste est **one-shot** (durée fixe, pas de « bascule maintenue »).
La durée de l'accroupi est fixée par la période du slot.

## Approche retenue (approche B)

Créer une **nouvelle tâche mjlab** entraînée sur le robot rollers, qui joue
descente → glisse accroupi → remontée, piloté par la phase du slot ground-pick.
L'exporter en ONNX et la charger via `--ground-pick`. Aucune modif Rust.

### Fichiers concernés

| Fichier | Action |
|---|---|
| `src/mjlab_microduck/tasks/microduck_roller_crouch_env_cfg.py` | **Nouveau.** L'env, hybride roller + ground-pick. |
| `src/mjlab_microduck/tasks/mdp.py` | **Ajout** de la reward `crouch_glide_height_by_phase`. |
| `src/mjlab_microduck/tasks/__init__.py` | **Ajout** : enregistrer `Mjlab-RollerCrouch-Flat-MicroDuck`. |

### Réutilisation (ne rien réinventer)

- **Physique / robot roller** ← `microduck_velocity_rollers_env_cfg.py` :
  `MICRODUCK_WALK_ROLLERS_ROBOT_CFG` (14 joints actifs + 4 roues passives),
  capteur de contact sur les `roller_blade`, DR friction des roulements
  (`randomize_wheel_friction` + curriculum), obs 14-dim (roues exclues via
  `SceneEntityCfg("robot", joint_names=(r"^(?!passive_).*",))`), `action.scale=1.0`,
  `kp_fw=200`.
- **Machinerie phase / one-shot** ← `microduck_ground_pick_env_cfg.py` :
  commande `microduck_mdp.GroundPickPhaseCommand` **réutilisée telle quelle**
  (produit le `[cos(2πφ), sin(2πφ), 0]` que le runtime enverra dans le slot twist),
  padding head/body à zéro (`zero_command_padding`), terminaison `robot_state_is_nan`,
  `reset_action_history`.
- **DR sim2real** ← repris du roller env sans changement (IMU misalignment obs-level,
  encoder bias, masse/inertie, friction BAM, armature, pushes doux ±0.2).

## Le cœur : cible de hauteur « en trapèze » pilotée par la phase

Seule vraie nouveauté. Au lieu de descendre la bouche (ground-pick), on pilote la
**hauteur du tronc** (`com_height` du `trunk_base`) selon la phase, avec un palier bas :

```
hauteur
 haute ┐                    ┌──   debout (rend la main à la policy roller)
       │ \                 /
  basse│  \_______________/       accroupi + glisse (palier 1 s)
       └───────────────────────► phase
       0   0.375      0.625   1
```

- φ ∈ [0, 0.375] : descente vers la hauteur accroupie
- φ ∈ [0.375, 0.625] : **maintien accroupi** (= 1 s sur une période de 4 s) → glisse
- φ ∈ [0.625, 1.0] : remontée vers la pose roller debout

**Nouvelle reward `crouch_glide_height_by_phase(env, command_name, height_low,
height_high, hold_lo=0.375, hold_hi=0.625, std=...)`** dans `mdp.py` :
lit la phase depuis la commande, calcule la hauteur-cible (interpolée haut→bas→haut,
plate sur le palier), récompense `exp(-((h_mesurée - h_cible)/std)²)`.
S'inspirer des fonctions `com_height_target` (mdp.py:694) et des
`interpolated/multistage height target` déjà présentes.

Valeurs de départ : `height_high ≈ 0.11` m (hauteur roller debout, cf. bande
`com_height_target` roller 0.0935–0.1235), `height_low ≈ 0.075` m (accroupi ;
à affiner en play). La phase est reconstruite depuis `atan2(sin, cos)` de la commande.

## Récompenses

| Reward | Rôle | Origine |
|---|---|---|
| `crouch_glide_height_by_phase` | Cible principale (haut→bas→haut) | **nouveau** |
| `wheel_speed` (poids réduit ~2–3) | Garder l'élan, ne pas freiner pendant l'accroupi | roller env (`wheel_speed_reward`) |
| `upright` (≈2), `body_ang_vel` (−0.05), `angular_momentum` (−0.02) | Équilibre / stabilité | roller env |
| `return_pose` (fin de phase) | Converger vers la pose roller debout pour rendre la main proprement | adapté de `ground_pick_return_pose` |
| `feet_flat` (−2) | Lames à plat → glisse stable | roller env |
| `action_rate_l2`, `neck_action_rate_l2`, `joint_torques_l2`, `self_collisions` | Lissage / transfert sim2real | les deux envs |

**Explicitement PAS inclus :** `braking` (on ne veut pas s'arrêter), `mouth_ground_proximity`
/ `mouth_perpendicular_to_ground` (on ne touche pas le sol), `skating_air_time` /
`single_support` / `glide` (pas de stride pendant le trick — on glisse passivement).

## Entraînement

- `MicroduckRollerCrouchRlCfg` = copie de `MicroduckRollersRlCfg`
  (MLP 512/256/128, ELU, obs_normalization, PPO, `experiment_name="roller_crouch"`).
- Enregistrer dans `tasks/__init__.py` :
  `register_mjlab_task(task_id="Mjlab-RollerCrouch-Flat-MicroDuck", ...)`.
- Lancer :
  ```bash
  uv run train Mjlab-RollerCrouch-Flat-MicroDuck \
    --env.scene.num-envs 4096 --agent.max_iterations 8000
  ```
- Épisodes démarrés avec une **vitesse d'entrée réaliste** (le robot arrive en roulant),
  sinon il n'aura pas d'élan à conserver pendant l'accroupi. À câbler via un event de
  reset (vitesse initiale non nulle) ou un push au début d'épisode.

## Export + déploiement (flags runtime exacts)

Export ONNX (le normaliseur est baké par `export.py`), puis :

```bash
microduck_runtime --variant pre-alpha --new-cmd-obs --roller \
  --model output.onnx \
  --new-dxl-imu --kp 200 --action-scale 0.8 \
  --max-linear-vel 0.6 --max-linear-vel-backward 0.5 --max-angular-vel 0.0 \
  --ground-pick roller_crouch.onnx \
  --ground-pick-period 5.0 \
  --ground-pick-kp-ratio 1.0 \
  --ground-pick-action-scale 0.8
```

Bouton **A** → crouch-glide, puis retour auto à la policy roller.

**Pièges de parité entraînement/déploiement (importants pour le sim2real) :**
- `--ground-pick-kp-ratio 1.0` : le défaut est **0.6** (baisse kp à 120 pendant le trick).
  On entraîne à kp=200 → il faut forcer **1.0** pour que ça corresponde.
- `--ground-pick-action-scale` doit matcher l'`action_scale` d'entraînement (0.8 ci-dessus).
- `--ground-pick-period 5.0` doit matcher la période/longueur de mouvement entraînée
  (défaut 4.0, on le garde).

## Risques et vérification

- **One-shot, durée fixe :** l'accroupi dure `ground-pick-period` puis remonte tout seul.
  Pas de maintien libre — limite acceptée de l'approche B.
- **Élan pendant le trick :** la phase remplace la commande de vitesse → **pas de poussée
  active** pendant l'accroupi. Si l'élan d'entrée est trop faible, il ralentit. D'où
  l'entraînement avec vitesse d'entrée réaliste.
- **Vérification :**
  1. En sim (`play`) : il descend, garde les roues qui tournent pendant le palier,
     se relève sans tomber, et la pose finale rejoint proprement la pose roller debout.
  2. Sur le vrai robot : lancer à petite vitesse, appuyer sur A, observer.
  3. Confirmer que la policy roller reprend la main proprement après le retour.

## Questions ouvertes / à confirmer pendant l'implémentation

- Valeur exacte de `height_low` (accroupi) — à régler en play.
- Meilleure façon d'injecter la vitesse d'entrée à l'épisode (event reset vs push initial).
- Poids relatif `wheel_speed` vs `crouch_glide_height_by_phase` (garder l'élan sans
  empêcher de s'accroupir).
