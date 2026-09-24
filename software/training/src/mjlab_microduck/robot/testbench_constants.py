"""XL330 testbench entity configuration for sim2real validation."""

import os
from pathlib import Path

import mujoco
from mjlab.entity import EntityArticulationInfoCfg, EntityCfg

from bam.mjlab import BamActuatorCfg


_TESTBENCH_DIR: Path = Path(os.path.dirname(__file__)) / "xl330_test_bench"
# Use the robot-only XML (no floor / no lights): mjlab's TerrainImporterCfg
# adds its own ground plane, so scene.xml would give a duplicated floor.
TESTBENCH_XML: Path = _TESTBENCH_DIR / "xl330_test_bench.xml"

assert TESTBENCH_XML.exists(), f"XML not found: {TESTBENCH_XML}"


# Real-device payload mass (120 g)
TESTBENCH_ARM_MASS: float = 0.12


def _set_arm_mass(spec: mujoco.MjSpec, mass: float) -> None:
    for body in spec.bodies:
        if body.name == "arm":
            original = body.mass
            if original > 0:
                scale = mass / original
                body.mass = mass
                body.fullinertia = [x * scale for x in body.fullinertia]
            break


def get_testbench_spec() -> mujoco.MjSpec:
    spec = mujoco.MjSpec.from_file(str(TESTBENCH_XML))
    _set_arm_mass(spec, TESTBENCH_ARM_MASS)
    return spec


HOME_FRAME = EntityCfg.InitialStateCfg(
    joint_pos={"1": 0.0},
    joint_vel={".*": 0.0},
)


# Use the BAM M6 actuator model (matches the real XL330 on the test bench).
testbench_actuators = BamActuatorCfg(
    motor_name="xl330",
    model="m6",
    target_names_expr=(r"1",),
    kp_fw=200.0,
    # max_current=1.75,
    delay_min_lag=0,
    delay_max_lag=3,
)


XL330_TESTBENCH_ROBOT_CFG = EntityCfg(
    spec_fn=get_testbench_spec,
    init_state=HOME_FRAME,
    collisions=(),
    articulation=EntityArticulationInfoCfg(
        actuators=(testbench_actuators,),
        soft_joint_pos_limit_factor=1.0,
    ),
)
