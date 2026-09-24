# Mode pente — `roller_slope` (descente passive équilibrée)

Date : 2026-07-22
Statut : design validé, prêt pour le plan d'implémentation.

## Objectif

Entraîner une politique dédiée où **microduck (sur rollers) démarre sur du plat
avec une petite impulsion vers l'avant, roule jusqu'à une rampe descendante, et
se laisse glisser jusqu'en bas en restant debout et équilibré**. Aucun pilotage
pendant la descente : le seul objectif de la politique est de **ne pas tomber**.

La politique doit gérer des rampes de raideur croissante (**~2° → ~20°**) grâce à
un curriculum de difficulté.

## Décisions cadrées (brainstorming)

| Sujet | Décision |
|---|---|
| Comportement | Descente passive équilibrée (la gravité fait avancer, pas de pédalage imposé) |
| Pilotage | Aucun — équilibre pur, commande `twist` forcée à zéro |
| Approche | **A** — tâche dédiée, isolée (comme `roller_crouch`) |
| Forme du terrain | **Rampe simple** : plat de départ + rampe descendante (pas de pyramide) |
| Scénario épisode | Spawn sur le plat → vitesse d'impulsion vers l'avant → glisse sur la rampe |
| Raideur | Curriculum **0/2° → 20°** |
| Déploiement | Flag `--slope <onnx>` + touche **`Y`** dans `infer_policy.py` (Y est libre) |

## Architecture

### 1. Nouvelle tâche

Fichier : `src/mjlab_microduck/tasks/microduck_roller_slope_env_cfg.py`, cloné de
`microduck_velocity_rollers_env_cfg.py`.

- Même robot rollers (`MICRODUCK_WALK_ROLLERS_ROBOT_CFG`), même physique, même
  domain randomization / bruit / délais.
- **Même observation 61D** (twist + head/body en zéro-padding) → la politique
  charge par le chemin `--new-cmd-obs` du runtime et reste interchangeable avec
  les autres politiques rollers.
- Enregistrement dans `src/mjlab_microduck/tasks/__init__.py` via
  `register_mjlab_task`, avec une config PPO `MicroduckRollerSlopeRlCfg`
  (`experiment_name`/`run_name` = `roller_slope`).

### 2. Terrain « plat + rampe » (custom)

Les terrains inclinés fournis par mjlab sont des pyramides ; on écrit donc un
`SubTerrainCfg` dédié (p. ex. `FlatRampTerrainCfg`) dont la méthode
`function(difficulty, spec, rng)` construit :

- une **zone plate de départ** (longueur ~1–2 m) où le robot spawne ;
- une **rampe descendante** à la suite, dont l'angle est
  **interpolé par `difficulty`** sur `[~2°, ~20°]`.

Le terrain est monté via `TerrainEntityCfg(terrain_type="generator", ...)` avec un
`TerrainGeneratorCfg` qui génère plusieurs niveaux de difficulté (donc plusieurs
angles de rampe). L'origine de chaque environnement doit tomber **sur la zone
plate**, la rampe devant lui.

> Risque d'implémentation à traiter dans le plan : positionnement de l'origine de
> spawn sur le plat (pas au centre de la tuile), et orientation de la rampe pour
> que « devant » = « vers le bas ».

### 3. Commande = aucune

Slot `twist` neutralisé : `rel_standing_envs = 1.0`, ranges de vitesse à 0,
`rel_heading_envs = 0.0`. Head/body restent en zéro-padding. La politique ne
reçoit aucune consigne de déplacement.

### 4. Reset & vitesse d'impulsion

- `reset_base` : spawn au repos sur le plat, hauteur `z` nominale rollers
  (~`0.1335–0.1435`, comme le roller env).
- **Vitesse d'entrée** injectée via le `velocity_range` de
  `reset_root_state_uniform` (état propre + range), **pas** via
  `push_by_setting_velocity` (qui s'additionne à l'état courant et peut faire
  diverger le free-joint → NaN — leçon déjà apprise sur `roller_crouch`) :
  `x ≈ (0.2, 0.5) m/s` vers l'avant.
- Pushs aléatoires légers conservés pendant l'épisode (robustesse), comme le
  roller env.

### 5. Récompenses

Cœur « rester droit + posture naturelle », anti-optimum-paresseux (éviter qu'il
s'écrase au sol pour maximiser la stabilité) :

- `upright` (tronc vertical) — **principale**
- `alive` (bonus de survie par pas)
- **pose debout nominale** : récompense vers la pose HOME (mécanique
  d'interpolation de pose reprise de `roller_crouch`, mais cible fixe = debout),
  pour garder une stance rollers normale plutôt qu'un accroupi défensif
- `feet_flat` (rollers à plat au sol)
- `body_ang_vel`, `angular_momentum` (pas de tremblement / vrille)
- `action_rate_l2`, `neck_action_rate_l2`, `joint_torques_l2`,
  `self_collisions` (douceur + sim2real)

> Pas de récompense de vitesse/frein : la descente est passive. On ne récompense
> pas « aller vite », seulement « rester droit en descendant ».

### 6. Terminaisons

- **Chute** : `bad_orientation` (tronc trop incliné).
- **Bas atteint** : `out_of_terrain_bounds` (le robot est arrivé en bas de la
  rampe → reset).
- `nan_state`, time-out.

### 7. Curriculum de difficulté (raideur)

Progression **doux → raide** : commencer sur des rampes quasi plates, augmenter
l'angle vers 20° au fur et à mesure des réussites.

> Risque d'implémentation : le curriculum standard `terrain_levels_vel` promeut
> selon la distance parcourue par rapport à la vitesse commandée. Ici la commande
> est nulle, donc **il faut un critère de promotion custom** : promouvoir si le
> robot a survécu / atteint le bas sans tomber, rétrograder s'il chute tôt.

### 8. Déploiement — bouton `Y`

Dans `scripts/infer_policy.py` :

- nouveau flag `--slope <onnx>` chargeant la politique pente comme session
  supplémentaire (même schéma que `--walking` / `--standing` / `--ground-pick`) ;
- `GLFW_KEY_Y = 89` (aujourd'hui **libre** — la tête est sur `H`) qui **bascule**
  la session active vers/depuis la politique pente ;
- ligne d'aide clavier ajoutée.

Aucun contrôle existant n'est cassé (contrairement à un partage de la touche `H`
du contrôle de la tête).

## Ce qui n'est PAS dans le périmètre (YAGNI)

- Pas de pilotage gauche/droite ni de freinage en descente.
- Pas de montée de pente ni de traversée.
- Pas de pyramide ni de terrains multi-directions.
- Pas de fine-tuning depuis les poids roller existants (entraînement from
  scratch).

## Livrables

1. `microduck_roller_slope_env_cfg.py` (env + `FlatRampTerrainCfg` + PPO cfg).
2. Enregistrement de la tâche dans `tasks/__init__.py`.
3. Récompenses/curriculum custom nécessaires dans `tasks/mdp.py` (pose-debout,
   promotion de niveau).
4. Branchement `--slope` + touche `Y` dans `scripts/infer_policy.py`.
5. Tests unitaires pour les fonctions pures (angle de rampe par difficulté,
   éventuel critère de promotion).
