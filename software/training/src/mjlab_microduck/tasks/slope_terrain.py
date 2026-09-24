"""Terrain custom « plat + rampe descendante » pour la tâche roller_slope.

Le robot spawne sur une zone plate, reçoit une impulsion vers +x, roule
jusqu'à la rampe et se laisse glisser. L'angle de la rampe est interpolé par
la difficulté (curriculum) sur [RAMP_DEG_MIN, RAMP_DEG_MAX] degrés.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from mjlab.terrains.terrain_generator import (
    SubTerrainCfg,
    TerrainGeometry,
    TerrainOutput,
)

RAMP_DEG_MIN = 2.0
RAMP_DEG_MAX = 20.0


def ramp_angle_by_difficulty(
    difficulty: float, deg_min: float = RAMP_DEG_MIN, deg_max: float = RAMP_DEG_MAX
) -> float:
    """Angle de rampe (radians) interpolé linéairement par la difficulté [0,1]."""
    d = float(np.clip(difficulty, 0.0, 1.0))
    return math.radians(deg_min + d * (deg_max - deg_min))


@dataclass(kw_only=True)
class FlatRampTerrainCfg(SubTerrainCfg):
    """Plat de départ → rampe descendante → plat de sortie.

    Trois box alignés le long de +x :
      1. plat de départ (surface à z=0) où le robot spawne ;
      2. rampe descendante, angle interpolé par la difficulté, longueur
         HORIZONTALE tirée au hasard dans ``ramp_length_range`` (une valeur par
         tuile, fixée à la génération) ;
      3. plat de sortie au niveau du bas de la rampe, pour que le robot
         atterrisse sur du solide au lieu du vide.
    """

    flat_length: float = 2.0                       # plat de départ (m)
    ramp_length_range: tuple = (3.0, 8.0)          # longueur horizontale rampe (m), tirée au hasard
    runout_length: float = 4.0                     # plat de sortie en bas (m)
    spawn_on_ramp: float = 0.3                      # spawn ce nb de m SUR la rampe (gravité => roulement)
    deg_min: float = RAMP_DEG_MIN
    deg_max: float = RAMP_DEG_MAX
    thickness: float = 0.5                          # épaisseur des box (m)

    def function(
        self, difficulty: float, spec: mujoco.MjSpec, rng
    ) -> TerrainOutput:
        total_max = self.flat_length + self.ramp_length_range[1] + self.runout_length
        assert total_max <= self.size[0], (
            f"flat+ramp_max+runout ({total_max}) must fit in size[0] ({self.size[0]})"
        )
        body = spec.body("terrain")
        angle = ramp_angle_by_difficulty(difficulty, self.deg_min, self.deg_max)
        width = self.size[1]
        t = self.thickness
        # Longueur de rampe tirée au hasard (déterministe pour un rng donné).
        ramp_length = float(rng.uniform(self.ramp_length_range[0], self.ramp_length_range[1]))
        drop = ramp_length * math.tan(angle)  # dénivelé (m), positif

        # 1) Plat de départ : surface à z=0, x dans [0, flat_length].
        flat = body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=(self.flat_length / 2.0, width / 2.0, t / 2.0),
            pos=(self.flat_length / 2.0, 0.0, -t / 2.0),
        )

        # 2) Rampe : box tourné de +angle autour de +y (le bord +x descend).
        # Décalage -(t/2)·sin(angle) en x : sans lui, le bord HAUT de la surface
        # inclinée tombe à x=flat_length+(t/2)sin(a) -> petit trou entre la
        # plateforme plate (finit à flat_length) et la rampe. Avec, le haut de la
        # rampe touche pile le bord de la plateforme (raccord net), et le bas
        # touche pile le plat de sortie.
        surf_len = ramp_length / math.cos(angle)
        ramp_cx = self.flat_length + ramp_length / 2.0 - (t / 2.0) * math.sin(angle)
        ramp_cz = -(drop / 2.0) - (t / 2.0) * math.cos(angle)
        half = angle / 2.0
        ramp = body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=(surf_len / 2.0, width / 2.0, t / 2.0),
            pos=(ramp_cx, 0.0, ramp_cz),
            quat=(math.cos(half), 0.0, math.sin(half), 0.0),
        )

        # 3) Plat de sortie : surface au niveau du bas de la rampe (z = -drop).
        runout_cx = self.flat_length + ramp_length + self.runout_length / 2.0
        runout = body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=(self.runout_length / 2.0, width / 2.0, t / 2.0),
            pos=(runout_cx, 0.0, -drop - t / 2.0),
        )

        # Spawn un peu SUR la rampe : la gravité fait rouler les roues tout de
        # suite (élan AUX ROUES, pas de poussée de base qui patinerait), et le
        # robot est déjà sur la pente. z sur la surface inclinée à cette distance.
        spawn_x = self.flat_length + self.spawn_on_ramp
        spawn_z = -self.spawn_on_ramp * math.tan(angle)
        origin = np.array([spawn_x, 0.0, spawn_z])
        return TerrainOutput(
            origin=origin,
            geometries=[
                TerrainGeometry(geom=flat, color=(0.5, 0.5, 0.5, 1.0)),
                TerrainGeometry(geom=ramp, color=(0.45, 0.55, 0.75, 1.0)),
                TerrainGeometry(geom=runout, color=(0.5, 0.5, 0.5, 1.0)),
            ],
        )
