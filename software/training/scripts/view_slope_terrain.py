"""Observer la rampe du mode pente (roller_slope) dans le viewer MuJoCo.

Construit UNIQUEMENT le terrain « plat + rampe » (FlatRampTerrainCfg), sur
plusieurs rangées de difficulté croissante (raideur 2° -> 20°), et ouvre le
viewer natif MuJoCo. Aucune politique entraînée n'est nécessaire — c'est fait
pour valider à l'œil la géométrie (jointure plat/rampe, sens de descente).

Usage :
    uv run python scripts/view_slope_terrain.py
    uv run python scripts/view_slope_terrain.py --rows 6 --ramp-max 8 --runout 4
    uv run python scripts/view_slope_terrain.py --build-only   # test sans GUI

Dans le viewer : molette pour zoomer, clic-gauche glisser pour orbiter,
clic-droit glisser pour translater. Chaque rangée est une rampe de plus en plus
raide (difficulté 0 -> 1), de longueur tirée au hasard dans [ramp-min, ramp-max],
et se termine par un plat de sortie. « Devant » (+x) doit descendre.
"""

import argparse

import mujoco
import mujoco.viewer

from mjlab.terrains.terrain_generator import TerrainGenerator, TerrainGeneratorCfg
from mjlab_microduck.tasks.slope_terrain import (
    FlatRampTerrainCfg,
    RAMP_DEG_MIN,
    RAMP_DEG_MAX,
)


def build_model(rows, size, flat_length, ramp_range, runout, deg_min, deg_max):
    """Construit le modèle MuJoCo du terrain seul (rows rampes de raideur croissante)."""
    cfg = TerrainGeneratorCfg(
        seed=0,
        size=size,
        num_rows=rows,
        num_cols=1,
        curriculum=True,  # difficulté croissante le long des rangées
        difficulty_range=(0.0, 1.0),
        add_lights=True,
        sub_terrains={
            "flat_ramp": FlatRampTerrainCfg(
                flat_length=flat_length,
                ramp_length_range=ramp_range,
                runout_length=runout,
                deg_min=deg_min,
                deg_max=deg_max,
            )
        },
    )
    generator = TerrainGenerator(cfg)
    spec = mujoco.MjSpec()
    generator.compile(spec)
    model = spec.compile()
    return model


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rows", type=int, default=5, help="Nb de rangées = nb de raideurs affichées (défaut 5)")
    p.add_argument("--size", type=float, nargs=2, default=(15.0, 4.0), help="Taille d'une tuile (x y) en m")
    p.add_argument("--flat-length", type=float, default=2.0, help="Longueur du plat de départ (m)")
    p.add_argument("--ramp-min", type=float, default=3.0, help="Longueur horizontale mini de la rampe (m)")
    p.add_argument("--ramp-max", type=float, default=8.0, help="Longueur horizontale maxi de la rampe (m)")
    p.add_argument("--runout", type=float, default=4.0, help="Longueur du plat de sortie (m)")
    p.add_argument("--deg-min", type=float, default=RAMP_DEG_MIN, help=f"Raideur min en degrés (défaut {RAMP_DEG_MIN})")
    p.add_argument("--deg-max", type=float, default=RAMP_DEG_MAX, help=f"Raideur max en degrés (défaut {RAMP_DEG_MAX})")
    p.add_argument("--build-only", action="store_true", help="Construit le modèle et quitte (test sans GUI)")
    args = p.parse_args()

    model = build_model(
        rows=args.rows,
        size=tuple(args.size),
        flat_length=args.flat_length,
        ramp_range=(args.ramp_min, args.ramp_max),
        runout=args.runout,
        deg_min=args.deg_min,
        deg_max=args.deg_max,
    )
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    print(
        f"Terrain construit : {args.rows} rampes, raideur {args.deg_min}°->{args.deg_max}°, "
        f"longueur rampe {args.ramp_min}-{args.ramp_max}m + sortie {args.runout}m, "
        f"{model.ngeom} géométries."
    )
    if args.build_only:
        print("--build-only : OK, pas de GUI.")
        return

    print("Ouverture du viewer MuJoCo (Ctrl+C pour quitter)…")
    with mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False) as viewer:
        while viewer.is_running():
            mujoco.mj_forward(model, data)
            viewer.sync()


if __name__ == "__main__":
    main()
