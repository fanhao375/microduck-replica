"""Interactive crouch pose editor (roller robot).

Ouvre le viewer MuJoCo avec le robot rollers debout. Dans le panneau "Control"
du viewer, bouge les sliders (genoux/hanches/chevilles…) pour composer la pose
ACCROUPIE voulue. La gravité est coupée et la base est maintenue droite +
abaissée pour que le point le plus bas reste au sol (tu vois donc le tronc
descendre quand tu plies les genoux). À la fermeture de la fenêtre, la pose est
imprimée en dict CROUCH_POSE  {nom_articulation: angle_rad}  prêt à coller.

Usage:
    uv run python scripts/crouch_pose_editor.py
"""

import re
import time

import mujoco
import mujoco.viewer

from mjlab_microduck.robot.microduck_constants import (
    get_walk_rollers_spec,
    HOME_FRAME,
)


def home_value(joint_name: str):
    for pattern, val in HOME_FRAME.joint_pos.items():
        if re.search(pattern, joint_name):
            return float(val)
    return 0.0


# Modèle direct depuis le spec du robot (14 actionneurs <position> dans le XML).
model = get_walk_rollers_spec().compile()
data = mujoco.MjData(model)
mujoco.mj_resetData(model, data)
model.opt.gravity[:] = [0, 0, 0]  # rien ne s'effondre : seuls les sliders bougent

has_free = model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE

# Articulations actionnées (hors roues passives), avec adresse qpos.
joints = []
for i in range(model.njnt):
    name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, i)
    if not name or "freejoint" in name or "passive_" in name:
        continue
    joints.append((name, model.jnt_qposadr[i]))

# ctrl initial = pose HOME (les actionneurs position tiennent cette cible).
for a in range(model.nu):
    aname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, a)
    data.ctrl[a] = home_value(aname or "")

if has_free:
    data.qpos[0:3] = [0.0, 0.0, 0.14]
    data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]
    base_xy = data.qpos[0:2].copy()
    base_quat = data.qpos[3:7].copy()

robot_geoms = [g for g in range(model.ngeom)
               if model.geom_type[g] != mujoco.mjtGeom.mjGEOM_PLANE]

mujoco.mj_forward(model, data)

print("=== Crouch Pose Editor (rollers) ===")
print(f"actionneurs: {model.nu} | base flottante: {has_free}")
print("Ouvre le panneau 'Control' du viewer et bouge les sliders pour composer")
print("la pose ACCROUPIE. Ferme la fenêtre quand c'est bon.\n")

with mujoco.viewer.launch_passive(model, data) as viewer:
    while viewer.is_running():
        if has_free:
            data.qpos[0:2] = base_xy
            data.qpos[3:7] = base_quat
            data.qvel[0:6] = 0.0
        mujoco.mj_step(model, data)  # actionneurs position -> les joints suivent ctrl
        if has_free:
            data.qpos[0:2] = base_xy
            data.qpos[3:7] = base_quat
            data.qvel[0:6] = 0.0
            mujoco.mj_forward(model, data)
            try:
                zmin = min(float(data.geom_xpos[g, 2] - model.geom_rbound[g])
                           for g in robot_geoms)
                data.qpos[2] -= zmin
                mujoco.mj_forward(model, data)
            except Exception:
                pass
        viewer.sync()
        time.sleep(1.0 / 60.0)

print("\n=== Pose accroupie capturée ===\n")
print("CROUCH_POSE = {")
for name, adr in joints:
    print(f'    "{name}": {float(data.qpos[adr]):.4f},')
print("}")
if has_free:
    print(f"\n# hauteur de base finale (info) : z = {float(data.qpos[2]):.4f}")
print("# Colle CROUCH_POSE ici et donne-le a Claude pour cabler la reward.")
