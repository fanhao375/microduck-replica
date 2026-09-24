# Design — `roller_standup` : se relever sur rollers

**But** : une policy dédiée qui remet le microduck **debout sur ses rollers** après une chute
(à plat ventre ou à plat dos), et qui sait ensuite **tenir** la station sur roues.

Portage de la recette `standup` (canard marcheur) vers le modèle rollers. Aucune modification
des envs existants.

---

## Décisions actées

| Décision | Choix | Alternatives écartées |
|---|---|---|
| Forme | **Policy dédiée** épisodique | Greffer le relevé sur l'env roller (recette `velstand`) → risque réel de casser la foulée acquise |
| Poses de départ | **ventre + dos + debout** | `assis` (n'existe que pour le hand-off depuis la policy `sit`, pas d'équivalent roller) ; côtés (couverture max mais convergence bien plus dure) ; sans `debout` (la policy se relèverait puis retomberait) |
| Roues libres | **curriculum de friction de roulement inversé** | Vraie friction d'entrée (bootstrap trop dur) ; imposer une technique de patineur par récompenses (historique du repo : les récompenses de style trop directives créent des optima parasites — le swizzle, l'optimum paresseux du crouch) |
| Pose cible | **HOME + hauteur mesurée** | `STAND_POSE` du roller-crouch (signalée comme issue ouverte : ≠ du neutre roller → à-coup au retour) ; pose lue sur le vrai robot (bloque le dev) |
| Commande | **twist neutralisé** (≈ 0) | Commande de phase / slot bouton (voir « Déploiement ») ; tête pilotable |

---

## Architecture

**Nouveau fichier** : `src/mjlab_microduck/tasks/microduck_roller_standup_env_cfg.py`
- `make_microduck_roller_standup_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg`
- `MicroduckRollerStandUpRlCfg` (`experiment_name="roller_standup"`)
- Task id : `Mjlab-RollerStandUp-Flat-MicroDuck` (flat uniquement, pas de variante rough)

**Dérivation** : `cfg = make_microduck_velocity_rollers_env_cfg()`.

C'est le pattern de `roller_slope` (246 lignes) et non celui de `roller_crouch` (479 lignes, qui
repart de `make_velocity_env_cfg()` et recopie tous les blocs de DR). On hérite ainsi sans risque
de dérive :

- le robot `MICRODUCK_WALK_ROLLERS_ROBOT_CFG` (14 joints actifs + 4 roues passives, BAM m6, kp_fw 200) ;
- les capteurs `feet_ground_contact` (mode subtree sur `ankle_{l,r}_v1`) et `self_collision` ;
- toute la DR : CoM tronc + tête, masse/inertie (pseudo_inertia), friction BAM, armature,
  biais d'encodeur, désalignement IMU au niveau obs, friction des roulements ;
- **l'observation unifiée 61D** `[gyro(3), projected_gravity(3), joint_pos(14), joint_vel(14),
  last_action(14), command(13)]` — condition dure pour l'interchangeabilité au runtime ;
- la termination `nan_state` (garde élargi : joints + free-joint + roues).

Le modèle rollers **permet physiquement** de s'allonger : `robot_allcollisions_rollers.xml` porte des
géoms de collision sur le tronc (`np_f970`), les hanches, les jambes, les coques de tête et la mâchoire,
en plus des 4 pneus. Vérifié.

---

## Constantes mesurées

Mesurées par cinématique exacte (minimum des sommets de maillage des géoms collidantes, pose
`STAND` du keyframe, tronc ramené au contact) sur `scene_rollers.xml` vs `scene.xml` :

| pose | modèle pieds | modèle rollers |
|---|---|---|
| debout (`STAND` = HOME) | 0.1172 | **0.1407** |
| à plat ventre (repos) | 0.0752 | 0.0752 |
| à plat dos (repos) | 0.0476 | 0.0475 |

Contrôle de cohérence : le `standup` utilise `STAND_Z = 0.115` mesuré **sous charge** contre 0.1172
en cinématique → ~2 mm d'affaissement. On applique la même correction, et le résultat tombe pile dans
le `reset_base z = 0.1335–0.1435` déjà utilisé par l'env roller.

```python
ROLLER_STAND_Z   = 0.138   # tronc debout sur roues, sous charge (+23 mm vs pieds)
ROLLER_PRONE_Z   = 0.075   # hauteur de repos à plat ventre
EPISODE_LENGTH_S = 6.0
```

Les hauteurs de repos au sol sont **identiques** aux deux modèles (c'est la coque du tronc qui touche,
pas les pieds). Cela ne veut pas dire que la plage `prone_z` du `standup` se réutilise telle quelle :
voir la note sous « Reset » — `prone_z_min` diverge (0.076 ici, pas 0.05) car une seule plage sert
deux poses (ventre, dos) dont les hauteurs de contact au reset ne sont pas les mêmes.

La grandeur mesurée est bien celle que lisent les récompenses : `height_target_gaussian` et
`height_l1_penalty` utilisent `root_link_pos_w[:, 2]`, qui vaut exactement `xpos[trunk_base].z`
(le free-joint est sur `trunk_base`) — vérifié numériquement.

## Indices de joints

Les roues passives sont **intercalées** dans l'ordre des joints. Ordre réel vérifié dans MuJoCo
(`m.jnt_qposadr`, modèle rollers, 18 joints après le free-joint) :

```
0-4   left_hip_yaw, left_hip_roll, left_hip_pitch, left_knee, left_ankle
5-6   passive_LF_wheel, passive_LR_wheel
7-10  neck_pitch, head_pitch, head_yaw, head_roll
11-15 right_hip_yaw, right_hip_roll, right_hip_pitch, right_knee, right_ankle
16-17 passive_RF_wheel, passive_RR_wheel
```

```python
_LEG_JOINTS   = [0, 1, 2, 3, 4, 11, 12, 13, 14, 15]   # standup : [0-4, 9-13]
_NECK_JOINTS  = [7, 8, 9, 10]                          # standup : [5-8]
_WHEEL_JOINTS = [5, 6, 16, 17]
```

Seul `_LEG_JOINTS` est réellement consommé (par les récompenses de pose). `_NECK_JOINTS` et
`_WHEEL_JOINTS` sont déclarés pour la documentation et pour le test d'indices : le cou est résolu
**par nom** (`neck_joint_pos_l2` appelle `find_joints(r".*(neck|head).*")` à chaque pas, précisément
pour être robuste au décalage dû aux roues) et les roues par la regex `^passive_.*`.

Le doc de passation signale explicitement cette fragilité. Elle est verrouillée par un test qui
construit l'env et vérifie les noms de joints à ces indices (voir « Tests »).

---

## Récompenses

### Retirées de l'héritage roller

| Retiré | Pourquoi |
|---|---|
| `wheel_speed`, `braking`, `skating_air_time`, `glide`, `single_support`, `gait_symmetry`, `forward_lean`, `heading_hold` | récompenses de foulée : aucun sens quand on est par terre |
| `feet_flat` | pendant la montée les lames ne sont pas à plat → cette pénalité combattrait le geste |
| `hip_roll_neutral` | se relever demande d'écarter les jambes |
| `pose`, `com_height_target` | remplacés par les cibles pose/hauteur ci-dessous |
| `upright` (gaussienne de base) | remplacée par `upright_linear` + `upright_sharp` |

### Gardées de l'héritage roller

| Reward | Poids | Rôle |
|---|---|---|
| `action_over_limit` | −0.5 | protection sim2real (sur-commande au-delà des butées), indépendante de la tâche |
| `self_collisions` | −1.0 | |
| `body_ang_vel` | **−0.05** | volontairement **léger** : le `standup` documente qu'à −0.15 il gelait le relevé (bloqueur de mouvement) |
| `angular_momentum` | −0.02 | |
| `action_rate_l2` | curriculum −0.4 → −0.8 → −1.0 | l'env roller le met à plat à −1.0 ; on reprend la rampe du `standup` (douce au début → aide le bootstrap du grand mouvement de retournement) |
| `neck_action_rate_l2` | −0.5 | tête stable |
| `neck_joint_pos_l2` | −0.5 | garder la tête droite (le choix de `roller_slope`) — **remplace** la commande `head_pose` du `standup` |
| `joint_torques_l2` | −1e-3 | |

### Ajoutée

| Reward | Poids | Rôle |
|---|---|---|
| `joint_torque_rate_l2` | −2e-3 | anti-jitter : le `standup` l'a identifié comme le seul amortisseur qui ne bloque pas le retournement (il pénalise la *variation* de couple, pas son amplitude ni la rotation du tronc) |

### Récompenses de relevé (transplant du `standup`, remappé)

Les dix termes sont copiés **avec leurs poids déjà réglés** par les itérations documentées dans
`microduck_standup_env_cfg.py`. Seuls changent les indices de joints et les deux hauteurs.
Toutes les fonctions mdp existent déjà — **rien à écrire dans `mdp.py`**.

| Reward | Fonction mdp | Poids | Paramètres roller | Rôle |
|---|---|---|---|---|
| `pose_stand_legs` | `pose_target_match` | +8.0 | `std=0.5`, `joint_indices=_LEG_JOINTS`, `target_overrides=None` (HOME) | pose articulaire cible |
| `pose_stand_l1` | `pose_l1_penalty` | +5.0 | `joint_indices=_LEG_JOINTS`, `target_overrides=None` | bootstrap L1 : gradient constant même loin de HOME |
| `height_stand` | `height_target_gaussian` | +4.0 | `std=0.04`, `target_height=0.138` | gaussienne large → tire depuis le sol |
| `height_stand_sharp` | `height_target_gaussian` | +4.0 | `std=0.015`, `target_height=0.138` | gaussienne étroite → force les derniers cm |
| `height_stand_l1` | `height_l1_penalty` | +30.0 | `target_height=0.138` | rend « rester par terre » net négatif (sinon optimum paresseux) |
| `com_upward_velocity` | `com_upward_velocity` | +3.0 | `max_height=0.148` | paye le *mouvement* de montée (+10 mm de marge au-dessus de la cible, comme 0.125 vs 0.115 chez `standup`) |
| `gentle_rise` | `trunk_vertical_accel_penalty` | −0.02 | | pénalise `\|a_z\|` → montée lisse à vitesse constante |
| `upright_linear` | `body_upright_linear` | +6.0 | | `cos(tilt)` : fort gradient quand couché |
| `upright_sharp` | `upright_gaussian_at_height` | +6.0 | `std=0.3`, `height_low=0.075`, `height_high=0.138` | gaussienne serrée gatée en hauteur → tue le penché-arrière |
| `standing_composite` | `standing_composite_score` | +15.0 | `height_std=0.04`, `upright_std=0.40`, `pose_std=0.40`, `target_height=0.138`, `joint_indices=_LEG_JOINTS` | score multiplicatif hauteur × droit × pose |

Tous les termes prennent `asset_cfg=SceneEntityCfg("robot", body_names=("trunk_base",))` là où le
`standup` le fait.

**Pas de pénalités d'impact** (tronc/tête) pour cette v1 : le `standup` n'en a pas, seul `velstand`
en a. On garde le jeu minimal.

---

## Observation et commande

**Observation** : héritée intacte de l'env roller (61D). Aucune modification — c'est la raison de
dériver de cet env.

On ajoute `nan_policy = "sanitize"` sur les groupes actor et critic, comme `roller_slope` : un contact
rare fait diverger le free-joint en NaN, l'obs est assainie (→ 0) pour ne pas tuer l'entraînement,
et l'env fautif se reset au pas suivant.

**Commande** : le slot `twist` est neutralisé, exactement comme le `standup` :

```python
command = cfg.commands["twist"]
command.rel_standing_envs = 0.0
command.rel_heading_envs  = 0.0
command.heading_command   = False
command.ranges.heading    = None
command.resampling_time_range = (EPISODE_LENGTH_S, EPISODE_LENGTH_S * 2)
command.debug_vis = False
command.ranges.lin_vel_x = (-0.01, 0.01)
command.ranges.lin_vel_y = (-0.01, 0.01)
command.ranges.ang_vel_z = (-0.05, 0.05)
cfg.commands["twist"] = microduck_mdp.VelocityCommandCommandOnlyCfg(**vars(command))
```

Les slots `head_pose` (4) et `body_pose` (6) restent **zero-paddés** — convention de la famille
roller (`roller`, `roller_crouch`, `roller_slope`). C'est un écart assumé vis-à-vis du `standup` de
la marche, qui pilote la tête via une vraie commande `head_pose` 4D (voir « Risques »).

Justification du twist neutralisé : dans `scripts/infer_policy.py`, la policy `standup` de la marche
est chargée en `--standing` à côté de `--walking`, et la bascule est **automatique sur la magnitude
de la commande de vitesse** (`infer_policy.py:262`, seuil 0.05) ; quand `standing` est active, le
slot twist est laissé à zéro (`infer_policy.py:239`). Les slots à phase (`ground_pick`, `fold`)
servent aux tricks one-shot déclenchés au bouton, pas à un relevé.

---

## Reset

Ajout de l'événement `set_ground_state` (mode `reset`), inséré **après** `reset_base` et
`reset_robot_joints` de l'héritage (l'ordre des événements suit l'ordre d'insertion dans le dict) :

```python
cfg.events["set_ground_state"] = EventTermCfg(
    func=microduck_mdp.set_random_ground_state,
    mode="reset",
    params={
        "face_down_prob":  0.50,   # ventre — piloté par le curriculum ci-dessous
        "face_up_prob":    0.00,   # dos — introduit tard (le plus dur)
        "sitting_prob":    0.00,   # pas de bucket assis → aucun override de joint à remapper
        "standing_prob":   0.50,
        "prone_z_min":     0.076,  # cf. note ci-dessous — pas un simple héritage du standup
        "prone_z_max":     0.09,
        "standing_z_min":  0.134,  # roller (contre 0.11–0.12 pour les pieds)
        "standing_z_max":  0.144,
        "sitting_tilt_max": math.radians(10),  # ± bruit de pitch/roll ; s'applique AUSSI au bucket debout
    },
)
```

Note : dans `set_random_ground_state`, le bucket `standing` réutilise le quaternion du bucket
`sitting` — donc `sitting_tilt_max` bruite aussi les départs debout, ce qui est voulu.

**Sur `prone_z_min` = 0.076 (et pas 0.05, valeur reprise à tort du `standup`)** : les poses ventre et
dos partagent une seule plage de z, mais leurs hauteurs de contact mesurées diffèrent — ventre
0.0752, dos 0.0475 — donc une plage unique ne peut pas être idéale pour les deux. Le commentaire du
`standup` justifie son plancher `0.05` par un repos mesuré à ~0.044 **après stabilisation sous
gravité** ; or ce qui compte à l'instant du reset, c'est la hauteur de contact en pose HOME, pas la
hauteur de repos une fois retombé. À 0.05, le ventre spawn avec la coque du tronc **enfoncée de
25 mm dans le sol**, un pushout que la policy paie ensuite via `gentle_rise` /
`joint_torque_rate_l2`. `prone_z_min = 0.076` élimine cette interpénétration, au prix d'un dos qui
démarre 28–42 mm au-dessus de son repos — un artefact bien plus doux qu'un pushout de contact.

**Aucune modification de `mdp.py`** : `reset_robot_joints` de la base utilise
`joint_names=(".*",)` avec `velocity_range=(0.0, 0.0)` et `default_joint_vel` (HOME_FRAME
`joint_vel={".*": 0.0}`) → les 4 roues passives sont déjà remises à zéro à chaque reset. Vérifié.

**Curriculum `ground_state_mix`** (`event_param_curriculum`), même logique easy → hard que le
`standup` : le dos est introduit tard et reçoit le plus d'entraînement à la fin.

| iter | debout | ventre | dos |
|---|---|---|---|
| 0 | 0.50 | 0.50 | 0.00 |
| 600 | 0.35 | 0.45 | 0.20 |
| 1500 | 0.25 | 0.40 | 0.35 |
| 2500 | 0.20 | 0.40 | 0.40 |

(Steps en unités de `common_step_counter` = `iter × 24`.)

**Poussées** : `push_robot` est hérité de l'env roller (±0.2 m/s, intervalle 3–6 s). On ajoute le
curriculum montant du `standup` pour ne pas parasiter le bootstrap : 0 → ±0.08 (iter 500) → ±0.2
(iter 1000).

**Terminations** : suppression de `fell_over` (le robot **démarre** tombé — la termination sur
inclinaison n'a pas de sens ici). `nan_state` est hérité et conservé.

**Terrain** : `plane`. Pas de variante rough pour cette v1 — cohérent avec l'env roller, qui n'a pas
de paramètre `rough`.

---

## Curriculum de friction de roulement, inversé

C'est la seule pièce réellement nouvelle du design, et le cœur de la question posée par la tâche :
**les roues roulent, il n'y a aucune adhérence longitudinale pour pousser sur le sol.**

Le mécanisme existe déjà et est hérité (`randomize_wheel_friction` via `dr.dof_frictionloss` sur
`^passive_.*` + `wheel_friction_curriculum`). Dans l'env roller il **monte** 0 → 0.0015. Ici on le
fait **descendre** :

| iter | frictionloss | effet |
|---|---|---|
| 0 | 0.05 | roues quasi bloquées → il se relève comme s'il avait des pieds |
| 1000 | 0.02 | |
| 2000 | 0.008 | |
| 3000 | 0.003 | |
| 4000 | 0.0015 | la vraie valeur du roulement (celle de l'env roller) |

`wheel_friction_curriculum` applique simplement le dernier palier franchi
(`if env.common_step_counter > stage["step"]`) — il fonctionne aussi bien en descente qu'en montée.
**Zéro code à écrire.**

**Ce que ce curriculum nous dit** : si `Episode_Reward/standing_composite` s'écroule quand la
friction baisse, on a la réponse nette que le geste « pieds adhérents » ne transfère pas aux roues
libres, et il faudra guider une technique de patineur (appui genou intermédiaire, un patin à la
fois). C'est un résultat exploitable, pas un échec.

---

## Réseau et PPO

Identiques au `standup` : actor et critic `(512, 256, 128)` elu, `obs_normalization=True`
(normaliseur baké dans l'ONNX par `export.py`), PPO `lr=1e-3` schedule adaptive, `desired_kl=0.01`,
`entropy_coef=0.01`, `gamma=0.99`, `lam=0.95`, `num_steps_per_env=24`, `save_interval=250`,
`max_iterations=15_000`. **Symétrie OFF** (`SYMMETRY_CFG` est câblé pour l'ancien layout 51D et casse
sur le 61D — même situation que tous les envs v1.5+).

---

## Tests

`tests/test_roller_standup_cfg.py` :

1. l'env se construit (`play=False` et `play=True`) ;
2. **les noms de joints aux indices `_LEG_JOINTS` / `_NECK_JOINTS` / `_WHEEL_JOINTS` sont les bons**
   (le verrou contre la fragilité des roues intercalées) ;
3. les récompenses de relevé attendues sont présentes, les récompenses de patinage absentes
   (`wheel_speed`, `glide`, `single_support`, `feet_flat`, …) ;
4. `fell_over` absent, `nan_state` présent ;
5. le curriculum `wheel_friction` est bien **décroissant** et finit à 0.0015 ;
6. le curriculum `ground_state_mix` : les probabilités du dernier palier somment à 1 et
   `face_up_prob` croît de façon monotone ;
7. **parité d'obs** : les noms et dimensions des termes actor/critic sont identiques à ceux de
   `make_microduck_velocity_rollers_env_cfg()` (sinon l'ONNX ne se charge pas dans un slot).

Lancer : `uv run --with pytest pytest tests/ -q`.

---

## Entraînement et déploiement

```bash
uv run train Mjlab-RollerStandUp-Flat-MicroDuck --env.scene.num-envs 4096 --agent.max_iterations 15000
```

Surveiller `Episode_Reward/standing_composite` (doit monter), et surtout son comportement **aux
paliers de friction de roulement** (iters 1000/2000/3000/4000).

Play : `uv run scripts/play_latest.py`. Export : `uv run scripts/export_latest.py`.

Déploiement visé : la policy en `--standing` face à la policy roller en `--walking`, avec la bascule
automatique sur la magnitude de la commande. **Réserve** : `infer_policy.py` est le script de
sim/clavier local ; le runtime robot est le binaire Rust `microduck_runtime`, absent de ce repo — il
n'est pas vérifié ici qu'il expose un équivalent `--standing` avec la même bascule. Le doc de
passation ne liste que `--model`, `--ground-pick`, `--fold-policy`. À confirmer. Cela ne change rien
à l'entraînement : si le runtime n'a pas ce slot, la policy reste utilisable dans un slot bouton (la
commande y serait une phase au lieu de zéro — ce serait alors le seul point à revoir).

---

## Risques et points de vigilance

1. **Le relevé sur roues libres est peut-être infaisable sans technique dédiée.** C'est le risque
   principal. Le curriculum de friction est conçu pour trancher cette question de façon lisible
   plutôt que pour la contourner.
2. **Le bucket « dos » est le plus dur.** Le `standup` documente qu'il gelait en « ne rien faire »
   sur cette pose, et que la cause était les *bloqueurs de mouvement* (`body_ang_vel` élevé,
   `action_rate` trop fort). Les valeurs reprises ici sont celles de la version « se relève de
   partout » — ne pas les durcir sans raison.
3. **Tête zero-paddée vs commande `head_pose`.** Si la policy est déployée en `--standing` et que
   quelqu'un actionne les touches de tête, `infer_policy` écrit `cmd[3:7] = head_offset` et la policy
   voit du hors-distribution. Choix assumé pour rester dans la convention roller ; à revoir si le
   pilotage de tête pendant le relevé s'avère nécessaire.
4. **Frictionloss 0.05 est loin du réel.** Les paliers 0 → 2000 iters produisent une policy qui ne
   transfère pas ; seuls les checkpoints d'après le dernier palier (iter 4000+) sont candidats au
   déploiement.

## Hors périmètre

- Intégrer le relevé dans la policy de roulage (recette `velstand`) — décision reportée après
  validation de la faisabilité.
- Buckets de départ sur le côté.
- Variante rough / terrain accidenté.
- Pénalités d'impact tronc/tête.
- Toute modification des envs `roller`, `roller_crouch`, `roller_slope`, `standup`, `velstand`, ou
  de `mdp.py`.
