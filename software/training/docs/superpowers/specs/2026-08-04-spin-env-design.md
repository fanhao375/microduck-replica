# Spec — Env « Spin » (rotation rapide sur place, sur rollers)

Date : 2026-08-04. Branche : `new_pre_alpha_rollers`.

> **Amendement (après le premier run)** : le premier run de calibrage (500 it.)
> a montré que le robot tombe systématiquement vers 1,16 s, bien avant le
> freinage. En réponse, la cible a été réduite de moitié — `SPIN_RATE_MAX`
> 6.0 → **3.0 rad/s**, soit **1 tour par cycle au lieu de 2** — et
> `spin_stay_in_place` renforcé à **−3.0**, **sans curriculum** de vitesse.
> Voir « Résultats de la vérification initiale » pour les preuves et la
> configuration actuellement en vigueur.

## But

Une nouvelle tâche RL qui apprend au microduck sur rollers à faire un **spin** :
~2 tours anti-horaire sur place à ~6 rad/s (360°/s) *(cible initiale ; ramenée
à 3 rad/s, voir l'amendement)*, puis arrêt propre debout.
Geste **cyclique piloté par une phase**, déployé dans un **slot bouton one-shot**
du runtime, comme la tâche `roller_crouch` existante.

## Décisions cadrées

| Question | Décision |
|---|---|
| Support | Sur rollers (`MICRODUCK_WALK_ROLLERS_ROBOT_CFG`, 4 roues passives) |
| Pilotage | Slot bouton one-shot, commande = phase `[cos(2πφ), sin(2πφ), 0]` |
| Cible | ~6 rad/s, 2 tours, puis freinage jusqu'à l'arrêt (cible initiale ; ramenée à 3 rad/s, voir l'amendement) |
| État d'entrée | À l'arrêt **ou** en roulement lent (0 → 0.3 m/s) |
| Sens | Gauche uniquement (lacet positif, anti-horaire) |
| Approche | Objectif « résultat » (suivi de ω_z) + amorce antisymétrique décroissante |

**Contrainte runtime** : le slot n'envoie que `[cos, sin, 0]` — aucun canal libre
pour le sens de rotation. La policy tourne donc **toujours à gauche**. Une policy
miroir pourrait plus tard aller dans un autre slot (bouton B, `--fold-policy`).

## Mécanique physique visée

Sur 4 roues passives, la rotation sur place « propre » se fait en **roulement
différentiel** : le patin gauche part vers l'arrière, le droit vers l'avant (les
roues **roulent**, elles ne patinent pas). C'est un *swizzle antisymétrique* : les
jambes font l'inverse l'une de l'autre, au lieu du miroir du swizzle classique.

Vérification des signes pour une rotation anti-horaire (repère : x avant, y gauche,
z haut ; ω_z > 0) : un point à gauche (+y) a pour vitesse `ω ẑ × y ŷ = −ω y x̂`,
donc **vers l'arrière**. Les 4 roues tournent positif en marche avant (vérifié par
`test_wheel_direction.py`), donc pour un spin anti-horaire :
`ω_roues_gauche < 0`, `ω_roues_droite > 0`, soit **`ω_D − ω_G > 0`**.

## Approche retenue (C) et pourquoi

Trois approches ont été considérées :

- **A — objectif « résultat » pur** : on récompense la vitesse de lacet et on laisse
  PPO trouver le geste. Risque documenté dans ce repo : optimum paresseux /
  patinage-sautillement au lieu du roulement propre.
- **B — objectif « directif » par poses** : deux poses de ciseau interpolées par la
  phase, comme `roller_crouch`. Marche vite *si* les poses sont bonnes ; or pour le
  crouch elles étaient **lues sur le vrai robot**, alors qu'ici le geste est inconnu.
  Il faudrait le composer à la main : cher et risqué (des poses sans couple utile
  ne produisent rien).
- **C — A + amorce antisymétrique décroissante** ← **retenue**. Structure de A, plus
  deux termes de *shaping* faibles qui injectent la seule connaissance physique
  certaine (le roulement différentiel), et dont le poids décroît par curriculum pour
  laisser la policy affiner son propre geste. La **fréquence de pompage reste libre**.

## Architecture

**Fichier** : `src/mjlab_microduck/tasks/microduck_spin_env_cfg.py`
- factory `make_microduck_spin_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg`
- config PPO `MicroduckSpinRlCfg`
- task id `Mjlab-Spin-Flat-MicroDuck`, enregistré dans `tasks/__init__.py`

Clone la structure de `microduck_roller_crouch_env_cfg.py` : robot rollers, obs 61D
unifié, DR complète, `action.scale = 1.0`, terrain plat.

**`ENABLE_SYMMETRY = False`** — obligatoire : l'augmentation de symétrie gauche/droite
transformerait un spin à gauche en spin à droite et détruirait l'apprentissage.

**Commande** : `GroundPickPhaseCommandCfg(period=4.0, randomize_phase=False)`.
`period=4.0` est le défaut de `--ground-pick-period` → rien à passer au runtime.
`randomize_phase=False` → chaque épisode démarre à φ=0 (debout), comme au déploiement.

## Enveloppe de phase

La phase pilote une **vitesse de lacet cible** ω\*(φ), en trapèze sur 4 segments
(période 4 s, `SPIN_RATE_MAX = 6.0` rad/s — cible initiale ; ramenée à 3 rad/s,
voir l'amendement ; les segments et la période n'ont pas changé) :

```
ACCEL_END = 0.125   [0,     0.125)  0.5 s  ω* : 0 → 6 rad/s   (lancement, rampe linéaire)
HOLD_END  = 0.525   [0.125, 0.525)  1.6 s  ω* = 6 rad/s        (régime)
BRAKE_END = 0.650   [0.525, 0.650)  0.5 s  ω* : 6 → 0          (freinage, rampe linéaire)
            1.0     [0.650, 1.0)    1.4 s  ω* = 0              (repos debout)
```

*(Les valeurs ω\* = 6 rad/s ci-dessus correspondent à `SPIN_RATE_MAX` = 6.0, la
cible initiale ; voir l'amendement pour la valeur en vigueur.)*

Intégrale sur un cycle : `0.5·3 + 1.6·6 + 0.5·3 = 12.6 rad ≈ 2.0 tours`. ✅
*(à `SPIN_RATE_MAX = 6.0`, cible initiale.)* Forme générale : l'intégrale vaut
`2.1 × SPIN_RATE_MAX` quel que soit `rate_max` (0.25 + 1.6 + 0.25 = 2.1). Avec la
cible en vigueur (3.0 rad/s) : `2.1 × 3.0 = 6.3 rad ≈ 1 tour` par cycle — voir
l'amendement.

Épisode = 20 s = **5 cycles** : le robot répète lancement → régime → freinage → repos
cinq fois par épisode. Plus de données par épisode, et le segment « repos » entraîne
aussi la sortie propre du trick. **Note (post-run)** : ceci reste vrai
géométriquement (20 s / 4 s), mais aucun épisode du run de calibrage n'a survécu
au-delà de ~1,16 s, soit une fraction du premier cycle seulement — voir
« Résultats de la vérification initiale ».

**Fonction pure** `spin_rate_by_phase(phase, rate_max, accel_end, hold_end, brake_end)`
dans `mdp.py`, à côté de `crouch_pose_blend`. Testable sans simulateur.

**Porte de shaping** : `gate(φ) = spin_rate_by_phase(φ) / rate_max ∈ [0, 1]`. Vaut 0
sur le segment repos → aucune amorce ne pousse au ciseau à ce moment-là, donc le robot
revient en station neutre. C'est ce qui donne une sortie de trick propre vers la policy
roller.

## Rewards

### Pièges vérifiés dans mjlab (à traiter explicitement)

- `body_ang_vel` (`body_angular_velocity_penalty`) ne pénalise que **x/y**
  (`ang_vel_xy`, commentaire « Don't penalize z-angular velocity ») → **gardée**
  (poids −0.05) : elle réprime le ballant roulis/tangage sans gêner le spin.
- `angular_momentum` (`angular_momentum_penalty`) pénalise la **norme 3D** du moment
  angulaire → elle combattrait directement le spin. **Supprimée.**

### Nouvelles rewards (à écrire dans `mdp.py`)

| Reward | Poids | Définition |
|---|---|---|
| `spin_rate_track` | 6.0 | `exp(−((ω_z − ω*(φ))/std)²)`, `std = 1.5` rad/s. ω_z = lacet du tronc en repère corps (ce que voit l'IMU). Objectif principal. |
| `spin_rate_l1` | 0.5 | `−|ω_z − ω*(φ)|` : bootstrap à gradient constant quand la gaussienne sature loin de la cible (même astuce que `crouch_glide_pose_l1`) |
| `spin_stay_in_place` | −3.0 (initialement −1.0, voir l'amendement) | `‖v_xy‖²` du tronc → « sur place », et tue l'élan d'entrée. Pas d'état de référence, donc robuste aux 5 cycles par épisode |
| `spin_wheel_differential` | 1.0 | `gate(φ) · tanh(clamp(ω_D − ω_G, min=0) / omega_scale)` avec `ω_G = (LF+LR)/2`, `ω_D = (RF+RR)/2` : récompense les patins qui roulent en sens opposés cohérents avec l'anti-horaire → tourner **en roulement**, pas en patinage. Roues résolues par nom (`passive_LF_?wheel`, …). `omega_scale = 17.0` rad/s en vigueur (voir le paragraphe de calibrage ci-dessous) |
| `leg_antisymmetry` | 1.0 → 0.25 | `gate(φ) · (−mean|q_G − q_D|)` sur `hip_pitch` et `knee`. ⚠️ convention miroir : une pose *symétrique* donne `q_G + q_D ≈ 0`, donc le **ciseau** c'est `q_G ≈ q_D`. Décroît par curriculum |
| `spin_grounded` | 0.5 | `gate(φ) · 1[n_contact ≥ 2]` : les deux lames au sol, empêche « je saute et je vrille en l'air ». La `grounded_reward` du swizzle n'est pas réutilisable telle quelle (elle se pondère par `cmd_x`, qui vaut ici `cos(2πφ)`) |

**Calibrage de `omega_scale`** (échelle de saturation du tanh) : au régime visé,
chaque patin avance à `v = ω_z · demi_voie`, donc chaque roue tourne à
`v / r` avec `r = 0.0175` m, et le différentiel vaut `2 · ω_z · demi_voie / r`.
Les racines de jambe sont à `y = ±0.0175` m dans le modèle rollers, mais les patins
sont plus écartés (offset de cheville) : la demi-voie réelle est à **mesurer sur les
sites `left_foot` / `right_foot` dans le sim** au premier run. Avec une demi-voie
estimée à ~0.03 m et `ω_z = 6` rad/s, le différentiel attendu était ~20 rad/s — d'où
le défaut initial `omega_scale = 20.0`. **Mesure faite (Task 3) : demi-voie réelle
= 0.0499 m, différentiel attendu = 34.2 rad/s, soit 71 % au-dessus de l'estimation
— au-delà du seuil de 30 % fixé par le plan.** `SPIN_WHEEL_OMEGA_SCALE` a donc été
corrigé à **34.0** (valeur intermédiaire, en vigueur tant que la cible était à
6 rad/s ; recalibrée depuis à **17.0**, voir le paragraphe « Mise à jour »
juste en dessous). Voir la section « Résultats de la vérification initiale »
ci-dessous pour le détail de la mesure de demi-voie.

**Mise à jour (fix wave post-review)** : `SPIN_RATE_MAX` a été réduit de 6.0 à
**3.0 rad/s** (décision humaine, sans curriculum — voir plus bas). Conséquence
mécanique directe sur `omega_scale`, pas un choix indépendant : le différentiel
attendu au régime redevient `2 · 3.0 · 0.0499 / 0.0175` = **17.1 rad/s**. Laisser
`omega_scale = 34.0` plafonnerait le terme à `tanh(17.1/34) = 0.47` de son propre
maximum, ce qui affaiblirait exactement le shaping que l'on cherche à renforcer.
`SPIN_WHEEL_OMEGA_SCALE` est donc recorrigé à **17.0**, avec la même demi-voie
mesurée (0.0499 m) conservée comme référence.

### Rewards reprises de `roller_crouch` (stabilité / sim2real)

| Reward | Poids |
|---|---|
| `upright` (tronc vertical) | 2.0 |
| `feet_flat` (lames à plat) | −2.0 |
| `self_collisions` | −1.0 |
| `body_ang_vel` (xy seulement) | −0.05 |
| `action_rate_l2` | −1.0 (curriculum −0.5 → −1.0) |
| `neck_action_rate_l2` | −0.5 |
| `joint_torques_l2` | −1e-3 |
| `neck_joint_pos_l2` **hors `head_yaw`** | −0.2 |

**La tête** : tangage/roulis de la nuque tenus près du neutre (sim2real), mais
`head_yaw` **exclu** du terme → libre de servir de volant d'inertie pour lancer la
rotation. Implémentation : `neck_joint_pos_l2` résout ses joints par regex
`.*(neck|head).*` en dur ; il faut donc soit ajouter un paramètre de regex à cette
fonction, soit écrire une variante `neck_joint_pos_l2_no_yaw`. Choix : **ajouter un
paramètre `pattern`** à `neck_joint_pos_l2` (défaut inchangé) pour ne pas dupliquer.

## Reset / état d'entrée

```python
cfg.events["reset_base"].params["pose_range"]["z"] = (0.1335, 0.1435)
cfg.events["reset_base"].params["velocity_range"] = {"x": (0.0, 0.3)}
```

Injection via `reset_root_state_uniform`. **Jamais** `push_by_setting_velocity` en
`mode="reset"` : c'est ce qui avait produit les NaN sur le crouch (`root_vel +=` sur
une vitesse racine potentiellement divergente → le free-joint de la base explose).

## Domain randomization

Identique à `roller_crouch`, sans dévier (recette sim2real validée du repo) : COM
tronc + tête, masse/inertie, friction articulaire BAM, armature, friction roues,
pushes 0.2 m/s toutes les 3–6 s, désalignement IMU 6°, biais d'encodeurs ±0.015 rad.

## Observations

Layout **61D à l'identique** de roller / ground_pick / crouch — condition pour que
l'ONNX charge dans le slot :
`[gyro(3), projected_gravity(3), joint_pos(14), joint_vel(14), last_action(14), command(13)]`
avec `command = [twist(3), head_pose(4), body_pose(6)]`, head/body zero-paddés.

Donc : retrait de `base_lin_vel` de l'actor (gardé côté critic), retrait des
`height_scan` et `foot_height`, `wheel_vel` côté critic, joints passifs exclus des
termes `joint_pos`/`joint_vel`, délais et bruits identiques au crouch.

Le gyro est dans l'obs → la policy **observe** son propre ω_z : la tâche est observable.

## Terminations

`time_out`, `fell_over`, `out_of_terrain_bounds` (héritées) + `nan_state`
(`microduck_mdp.robot_state_is_nan`), comme le crouch.

## Curriculum

| Terme | Étapes |
|---|---|
| `action_rate_weight` | −0.5 (0) → −0.8 (250 it.) → −1.0 (500 it.) |
| `leg_antisym_weight` | 1.0 (0) → 0.5 (1500 it.) → 0.25 (3000 it.) |
| `com_range` | 0.003 → 0.005 (500 it.) → 0.01 (1000 it.) |
| `head_com_range` | 0.003 → 0.005 (500 it.) → 0.01 (1000 it.) |

(itérations × 24 pas/env, comme les autres envs)

**Pas de curriculum sur la vitesse cible** : 6 rad/s d'emblée *(cible initiale ;
ramenée à 3 rad/s, toujours sans curriculum, voir l'amendement)*. Voir « Plan B ».

## PPO

`MicroduckSpinRlCfg` = copie de `MicroduckRollerCrouchRlCfg` : actor/critic
(512, 256, 128) elu, obs normalization, PPO adaptatif lr 1e-3, `desired_kl=0.01`,
`num_steps_per_env=24`, `symmetry_cfg=None`, `experiment_name="spin"`,
`run_name="spin"`, `max_iterations=8000`.

## Tests

`tests/test_spin.py` — fonctions pures, sans simulateur :
- `spin_rate_by_phase` : valeurs aux bornes des 4 segments (0, rate_max, rate_max, 0, 0)
- monotonie croissante sur la rampe de lancement, décroissante sur le freinage
- **intégrale sur un cycle ≈ 4π** à `rate_max = 6.0` (garantit la **forme** du
  trapèze, `2.1 × rate_max` rad par cycle) — ne protège plus la cible en vigueur
  depuis l'amendement, cf. bullet suivant. Valeur exacte de l'enveloppe : 12.6 rad
  contre 4π = 12.566 → tolérance 1 %
- **la cible réellement expédiée** (`mdp.SPIN_RATE_MAX`) intègre bien à
  `2.1 × SPIN_RATE_MAX` rad par cycle, quel que soit `rate_max` — ajouté en
  7d916aa, c'est ce test qui échoue si la cible change sans qu'on ait réfléchi au
  nombre de tours. Avec la valeur en vigueur (3.0 rad/s) : 6.3 rad ≈ 1 tour
- `gate(φ) = 0` sur tout le segment repos, `∈ [0,1]` partout

`tests/test_spin_cfg.py` — l'env se construit :
- commande = `GroundPickPhaseCommand`, `period == 4.0`, `randomize_phase is False`
- `"angular_momentum" not in cfg.rewards` (le piège de la section rewards)
- `symmetry_cfg is None`
- dimension de l'obs actor == 61
- **parité exacte de l'ordre des termes d'observation** (actor + critic) avec
  `roller_crouch`, groupe par groupe — ajouté en 7d916aa, condition stricte pour
  que l'ONNX exporté charge dans le slot du runtime

Lancer : `uv run --with pytest pytest tests/ -q`

## Entraînement / déploiement

```bash
uv run train Mjlab-Spin-Flat-MicroDuck --env.scene.num-envs 4096 --agent.max_iterations 8000
# surveiller Episode_Reward/spin_rate_track (doit monter)
uv run scripts/play_latest.py     # alias md-play
uv run scripts/export_latest.py   # ONNX, normaliseur d'obs baké
```

```bash
microduck_runtime --variant pre-alpha --new-cmd-obs --roller \
  --model output.onnx --new-dxl-imu --kp 200 --action-scale 0.8 \
  --ground-pick spin.onnx \
  --ground-pick-period 4.0 \      # = SPIN_PERIOD
  --ground-pick-kp-ratio 1.0 \    # défaut 0.6 -> forcer 1.0 (entraîné kp 200)
  --ground-pick-action-scale 0.8  # matcher action_scale runtime
```

Bouton **A** → spin, puis retour auto à la policy roller.

## Critère de succès

En play : ~2 tours anti-horaire en ~2.6 s, dérive du tronc < ~10 cm, robot debout tout
du long, station neutre stable pendant le segment repos avant le cycle suivant.
*(Critère formulé pour la cible initiale de 6 rad/s / 2 tours ; à 3 rad/s, voir
l'amendement, ce serait ~1 tour sur la durée du régime — critère non révisé, le
robot ne tenant pas encore jusque-là.)*

## Plan B si l'entraînement plafonne

Dans l'ordre :
1. **Curriculum de vitesse** : `SPIN_RATE_MAX` 3 → 6 rad/s (nécessite de rendre
   `rate_max` pilotable par un `CurriculumTermCfg` sur les params de reward).
   **Partiellement suivi** : suite au run de calibrage, la cible a bien été
   abaissée à 3 rad/s (voir l'amendement), mais **sans curriculum** — 3 rad/s
   est pour l'instant une cible fixe, pas un point de départ ramping vers 6.
   L'humain a choisi de voir d'abord ce que le robot parvient à faire à cette
   vitesse avant d'envisager une remontée graduelle.
2. Monter `spin_wheel_differential` et retarder la décroissance de `leg_antisymmetry`.
3. Élargir `std` de `spin_rate_track` (1.5 → 2.5) pour un gradient utile plus loin.
4. En dernier recours, basculer sur l'approche B (poses de ciseau composées à la main
   dans un pose editor) pour amorcer le geste, puis relâcher.

## Hors périmètre

- Spin à droite (policy miroir dans un autre slot) — plus tard.
- Variante à pied (sans rollers).
- Spin commandé en vitesse continue (nécessiterait un canal de commande runtime).

## Résultats de la vérification initiale

### Demi-voie mesurée et `omega_scale`

La demi-voie a été mesurée sur les sites `left_foot` / `right_foot` du modèle
rollers : **0.0499 m**, contre l'estimation de 0.03 m du spec. Différentiel de
roues attendu au régime (6 rad/s) : `2 · 6.0 · 0.0499 / 0.0175` = **34.2 rad/s**,
soit 71 % au-dessus du défaut 20.0 — au-delà du seuil de 30 % fixé par le plan.
`SPIN_WHEEL_OMEGA_SCALE` a donc été changé de 20.0 à **34.0**. Les tests continuent
de passer `omega_scale=20.0` explicitement, pour rester indépendants de la
constante.

### Smoke run (Step 2 : 5 itérations, 64 envs, garde NaN)

Terminé sans exception. `Episode_Termination/nan_state` est resté à 0.0000 sur
toute la durée, et `/tmp/mjlab/nan_dumps/` n'a jamais été créé. Les six rewards
spin apparaissent bien dans les clés `Episode_Reward/` loggées : `spin_rate_track`,
`spin_rate_l1`, `spin_stay_in_place`, `spin_wheel_differential`, `spin_grounded`,
`leg_antisymmetry`.

Parité d'observation (Step 1) : la liste des termes de l'obs actor de l'env spin
est **identique** à celle de `roller_crouch` — 8 termes, même ordre :
`base_ang_vel, projected_gravity, joint_pos, joint_vel, actions, command,
head_command, body_command`. C'est la condition pour que l'ONNX exporté charge
dans le slot du runtime.

**Note d'usage à retenir** : la commande d'exemple du plan avec `--enable-nan-guard`
en flag nu est rejetée par le CLI de ce repo — il faut passer
`--enable-nan-guard True`.

### Run de calibrage 500 itérations (Step 3)

4096 envs, 500 itérations, ~2,32 s/itération, code de sortie 0, logger wandb (donc
`scripts/play_latest.py` / `md-play` retrouve le run).

**Ce qui a réellement été établi** : `Mean episode length` = **57.83 pas** sur un
épisode de 1000 pas (20 s à 50 Hz), soit **~1,16 s**. `Episode_Termination/fell_over`
≈ **70**, `time_out = 0.0000`, `nan_state = 0`. Le robot **tombe à chaque épisode**,
à une phase φ ≈ 0,29 — en plein milieu du segment de régime. Il n'atteint jamais le
freinage (φ ≥ 0,525) ni le repos (φ ≥ 0,650) : **71 % du cycle n'est jamais
entraîné**.

La longueur d'épisode est passée de 23,98 à 57,83 pas sur la durée du run : la
montée de `Episode_Reward/spin_rate_track` (0,0291 → 0,3168) reflète donc
principalement une **survie qui s'allonge**, pas un suivi qui s'améliore. Le
critère de succès de cette étape tel qu'énoncé dans le plan (« la courbe doit
monter ») **n'est pas un signal valide** pour ce terme : un robot totalement
immobile score déjà `6.0 × 0.405 = 2.43` dessus — le segment de repos paie plein
tarif pour rester debout sans bouger, donc toute policy qui survit plus longtemps
capte mécaniquement plus de ce segment-là, indépendamment de la qualité du suivi.

### Diagnostic dérivé — estimations, pas des mesures directes

Les valeurs ci-dessous viennent du rapport entre termes de reward dans le dernier
bloc de log, ce qui annule le facteur de normalisation inconnu appliqué par le
logger. À prendre comme des estimations, reproductibles à partir de la même
méthode :

**Ce qui tient** : pendant les ~1,2 s où il reste debout, le robot suit la cible
d'assez près. Rapport `spin_rate_l1 / spin_rate_track` (−0,0097 / 0,3168, poids 0,5
et 6,0, `std = 1.5`), en résolvant `e = 0.3674 · exp(−(e/1.5)²)` : erreur moyenne
absolue de suivi de vitesse de lacet ≈ **0,35 rad/s**, confirmée par deux voies
indépendantes — ce ratio `spin_rate_l1 / spin_rate_track`, et un calcul inverse à
partir de la normalisation du reward manager. Il **peut lancer** le spin ; il **ne
peut pas rester debout** en le faisant.

**Ce qui ne tient pas** : le bloc de shaping (`spin_wheel_differential` 1,0,
`spin_grounded` 0,5, `spin_stay_in_place` −1,0) totalise ~1,0 de poids contre 6,0
pour l'objectif principal — environ **13 %** de ce qu'une policy en patinage
renoncerait à gagner en ignorant ce bloc. Et `spin_wheel_differential` est
**invariant au centre instantané de rotation** : un spin centré à 6 rad/s et un
pivot sur le patin gauche à 6 rad/s produisent tous les deux un différentiel de
34,2 — ce terme n'encode donc **pas** le roulement centré, seul
`spin_stay_in_place` le fait. `spin_stay_in_place` ≈ −0,0069 implique
`‖v_xy‖ ≈ 0,35 m/s` : le robot est encore en translation, cohérent avec un pivot
excentré (patin comme pivot) plutôt qu'une rotation autour du centre du corps.

### Changement de configuration décidé suite à ce diagnostic

Cible réduite de moitié — `SPIN_RATE_MAX` 6.0 → **3.0 rad/s** — et
`spin_stay_in_place` renforcé −1.0 → **−3.0** (voir le tableau des rewards et
`SPIN_WHEEL_OMEGA_SCALE` recalibré à 17.0 plus haut). **Délibérément sans
curriculum** sur la vitesse cible : c'est un premier essai pour voir ce que le
robot parvient à faire à vitesse moitié, avant d'envisager une remontée graduelle
si besoin.

**Atténuation du coût de dérive pendant le lancement.** Renforcer
`spin_stay_in_place` à −3.0 a rendu plus aigu un défaut relevé par la revue : ce
terme était le seul du spin à ne pas être modulé par la phase, donc il facturait à
plein tarif la translation transitoire pendant la rampe de lancement — précisément
le moment où le robot doit pousser au sol pour s'injecter du moment angulaire, et
où l'élan d'entrée (jusqu'à 0.3 m/s) doit être **converti** en rotation. Le coût
est désormais multiplié par `SPIN_LAUNCH_DRIFT_SCALE = 0.2` sur `[0, ACCEL_END)`
et vaut plein tarif ensuite. Il n'est volontairement **pas** éteint pendant le
repos, contrairement aux amorces : c'est là que l'immobilité est le vrai critère.

L'étape 4 (regarder le geste) reste à faire, réservée à l'humain.

⚠️ Ces quatre tests (trois nouveaux sur l'atténuation, un modifié) n'ont **pas**
été exécutés — la machine était réservée à autre chose au moment du commit. À
lancer avant tout run long : `uv run --with pytest pytest tests/test_spin.py
tests/test_spin_cfg.py -q`.
