import math

import mujoco
import numpy as np

from mjlab_microduck.tasks.slope_terrain import (
    ramp_angle_by_difficulty,
    RAMP_DEG_MIN,
    RAMP_DEG_MAX,
    FlatRampTerrainCfg,
)


def test_ramp_angle_endpoints():
    assert math.isclose(ramp_angle_by_difficulty(0.0), math.radians(RAMP_DEG_MIN), abs_tol=1e-9)
    assert math.isclose(ramp_angle_by_difficulty(1.0), math.radians(RAMP_DEG_MAX), abs_tol=1e-9)


def test_ramp_angle_midpoint():
    mid_deg = (RAMP_DEG_MIN + RAMP_DEG_MAX) / 2.0
    assert math.isclose(ramp_angle_by_difficulty(0.5), math.radians(mid_deg), abs_tol=1e-9)


def test_ramp_angle_clamps_out_of_range():
    assert math.isclose(ramp_angle_by_difficulty(-1.0), math.radians(RAMP_DEG_MIN), abs_tol=1e-9)
    assert math.isclose(ramp_angle_by_difficulty(2.0), math.radians(RAMP_DEG_MAX), abs_tol=1e-9)


def _empty_terrain_spec():
    spec = mujoco.MjSpec()
    spec.worldbody.add_body(name="terrain")
    return spec


def test_flat_ramp_builds_geoms_and_origin_on_flat():
    cfg = FlatRampTerrainCfg(flat_length=2.0)
    cfg.size = (15.0, 4.0)  # posé normalement par le générateur
    spec = _empty_terrain_spec()
    out = cfg.function(difficulty=0.5, spec=spec, rng=np.random.default_rng(0))
    # trois géométries : plat de départ + rampe + plat de sortie
    assert len(out.geometries) == 3
    # origine SUR la rampe (au-delà du plat), donc x > flat_length et z < 0
    assert out.origin[0] == cfg.flat_length + cfg.spawn_on_ramp
    assert out.origin[2] < 0.0
    # z = surface inclinée à spawn_on_ramp du bord (drop = d * tan(angle))
    angle = ramp_angle_by_difficulty(0.5, cfg.deg_min, cfg.deg_max)
    assert abs(out.origin[2] - (-cfg.spawn_on_ramp * math.tan(angle))) < 1e-9


def test_flat_ramp_steeper_at_higher_difficulty():
    # à difficulté plus haute, le bout de rampe descend plus bas
    cfg = FlatRampTerrainCfg()
    cfg.size = (15.0, 4.0)
    easy = cfg.function(0.0, _empty_terrain_spec(), np.random.default_rng(0))
    hard = cfg.function(1.0, _empty_terrain_spec(), np.random.default_rng(0))
    # la rampe (2e géométrie) est plus basse (centre z plus négatif) en difficile
    # (même rng -> même longueur tirée -> seule la pente change)
    assert hard.geometries[1].geom.pos[2] < easy.geometries[1].geom.pos[2]


def test_ramp_joins_flat_platform_no_gap():
    # le haut de la rampe doit toucher le bord de la plateforme plate (x=flat_length) :
    # centre rampe décalé de -(t/2)*sin(angle) en x.
    cfg = FlatRampTerrainCfg()
    cfg.size = (15.0, 4.0)
    out = cfg.function(0.5, _empty_terrain_spec(), np.random.default_rng(0))
    ramp = out.geometries[1].geom
    angle = ramp_angle_by_difficulty(0.5, cfg.deg_min, cfg.deg_max)
    surf_half = ramp.size[0]
    ramp_len = surf_half * 2.0 * math.cos(angle)
    expected_cx = cfg.flat_length + ramp_len / 2.0 - (cfg.thickness / 2.0) * math.sin(angle)
    assert abs(ramp.pos[0] - expected_cx) < 1e-6


def test_flat_ramp_runout_at_ramp_bottom():
    # le plat de sortie (3e géométrie) est au niveau du bas de la rampe (z<0),
    # et sa surface est plate (box non tourné : quat identité).
    cfg = FlatRampTerrainCfg()
    cfg.size = (15.0, 4.0)
    out = cfg.function(1.0, _empty_terrain_spec(), np.random.default_rng(0))
    runout = out.geometries[2].geom
    assert runout.pos[2] < 0.0  # descendu sous le plat de départ
    # quaternion identité (plat, pas incliné)
    assert math.isclose(runout.quat[0], 1.0, abs_tol=1e-9)


def test_ramp_length_within_range():
    cfg = FlatRampTerrainCfg(ramp_length_range=(3.0, 8.0))
    cfg.size = (15.0, 4.0)
    # surface de rampe = ramp_length / cos(angle) ; à difficulté 0, angle=2°,
    # donc surf_len ~= ramp_length. On vérifie sur plusieurs tirages.
    for seed in range(20):
        out = cfg.function(0.0, _empty_terrain_spec(), np.random.default_rng(seed))
        surf_half = out.geometries[1].geom.size[0]
        ramp_len = surf_half * 2.0 * math.cos(math.radians(2.0))
        assert 3.0 - 1e-6 <= ramp_len <= 8.0 + 1e-6
