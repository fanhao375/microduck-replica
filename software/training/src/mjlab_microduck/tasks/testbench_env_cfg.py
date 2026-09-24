"""XL330 test-bench RL environment.

Single-DOF fixed-base joint tracking task for sim2real validation.  Starts at 0
and must reach a target angle uniformly sampled in [-80°, 80°].  Uses the same
observation noise and action-smoothness regularization as the microduck velocity
env, with NO domain randomization so the learned policy can be transferred
directly to the real XL330 testbench.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import torch

from mjlab.entity import Entity
from mjlab.envs import ManagerBasedRlEnvCfg
from mjlab.envs.manager_based_rl_env import ManagerBasedRlEnv
from mjlab.envs.mdp.actions import JointPositionActionCfg
from mjlab.envs import mdp as base_mdp
from mjlab.managers.command_manager import CommandTerm
from mjlab.managers import (
    CommandTermCfg,
    EventTermCfg,
    ObservationGroupCfg,
    ObservationTermCfg,
    RewardTermCfg,
    TerminationTermCfg,
)
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.rl import (
    RslRlOnPolicyRunnerCfg,
    RslRlPpoActorCriticCfg,
    RslRlPpoAlgorithmCfg,
)
from mjlab.scene import SceneCfg
from mjlab.sim import MujocoCfg, SimulationCfg
from mjlab.terrains import TerrainImporterCfg
from mjlab.utils.noise import UniformNoiseCfg as Unoise
from mjlab.viewer import ViewerConfig

from mjlab_microduck.robot.testbench_constants import XL330_TESTBENCH_ROBOT_CFG


# ----------------------------------------------------------------------------
# Target angle command (single joint)
# ----------------------------------------------------------------------------

TESTBENCH_MAX_ANGLE_RAD = math.radians(80.0)


class TargetAngleCommand(CommandTerm):
    """Uniform single-scalar target angle command."""

    cfg: "TargetAngleCommandCfg"

    def __init__(self, cfg: "TargetAngleCommandCfg", env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self.robot: Entity = env.scene[cfg.asset_name]
        self._target = torch.zeros(self.num_envs, 1, device=self.device)
        self.metrics["error"] = torch.zeros(self.num_envs, device=self.device)
        joint_ids, _ = self.robot.find_joints([cfg.joint_name])
        self._joint_id = int(joint_ids[0])

    @property
    def command(self) -> torch.Tensor:
        return self._target

    def _resample_command(self, env_ids: torch.Tensor) -> None:
        lo, hi = self.cfg.range
        self._target[env_ids, 0] = (
            torch.rand(len(env_ids), device=self.device) * (hi - lo) + lo
        )

    def _update_command(self) -> None:
        pass

    def _update_metrics(self) -> None:
        q = self.robot.data.joint_pos[:, self._joint_id]
        self.metrics["error"] = torch.abs(q - self._target[:, 0])


@dataclass(kw_only=True)
class TargetAngleCommandCfg(CommandTermCfg):
    class_type: type[CommandTerm] = TargetAngleCommand
    asset_name: str = "robot"
    joint_name: str = "1"
    range: tuple[float, float] = (-TESTBENCH_MAX_ANGLE_RAD, TESTBENCH_MAX_ANGLE_RAD)
    resampling_time_range: tuple[float, float] = (4.0, 4.0)


# ----------------------------------------------------------------------------
# Rewards
# ----------------------------------------------------------------------------


def target_angle_tracking(
    env: ManagerBasedRlEnv,
    command_name: str,
    std: float,
    asset_cfg: SceneEntityCfg,
) -> torch.Tensor:
    """exp(-error^2 / std^2) reward for single-joint position tracking."""
    target = env.command_manager.get_command(command_name)[:, 0]
    asset: Entity = env.scene[asset_cfg.name]
    joint_ids = asset_cfg.joint_ids
    q = asset.data.joint_pos[:, joint_ids[0] if isinstance(joint_ids, list) else joint_ids]
    if q.dim() > 1:
        q = q[:, 0]
    err = q - target
    return torch.exp(-(err ** 2) / (std ** 2))


# ----------------------------------------------------------------------------
# Env factory
# ----------------------------------------------------------------------------


def make_testbench_env_cfg(play: bool = False) -> ManagerBasedRlEnvCfg:
    asset_cfg_full = SceneEntityCfg("robot", joint_names=("1",))

    # Observations (base noise copied from microduck velocity env; joint_vel
    # noise here is 10× larger to mirror the noisy XL330 firmware velocity read).
    joint_pos_term = ObservationTermCfg(
        func=base_mdp.joint_pos_rel,
        noise=Unoise(n_min=-0.0006, n_max=0.0006),
    )
    joint_vel_term = ObservationTermCfg(
        func=base_mdp.joint_vel_rel,
        # 10× the microduck velocity env's joint_vel noise (0.024 → 0.24) — the
        # XL330 firmware velocity read is much noisier than MuJoCo's instantaneous
        # qdot, so we inject more observation corruption to force robustness.
        noise=Unoise(n_min=-0.24, n_max=0.24),
        delay_min_lag=1,
        delay_max_lag=1,
        delay_update_period=0,
    )
    actions_term = ObservationTermCfg(func=base_mdp.last_action)
    command_term = ObservationTermCfg(
        func=base_mdp.generated_commands,
        params={"command_name": "target_angle"},
    )

    policy_terms = {
        "joint_pos": joint_pos_term,
        "joint_vel": joint_vel_term,
        "actions": actions_term,
        "command": command_term,
    }
    critic_terms = dict(policy_terms)

    observations = {
        "policy": ObservationGroupCfg(
            terms=policy_terms,
            concatenate_terms=True,
            enable_corruption=True,
        ),
        "critic": ObservationGroupCfg(
            terms=critic_terms,
            concatenate_terms=True,
            enable_corruption=False,
        ),
    }

    # Actions.  `TESTBENCH_ACTION_SCALE` env var lets you sweep the action scale
    # from the command line without editing this file, e.g.
    #     TESTBENCH_ACTION_SCALE=0.5 uv run python -m mjlab.scripts.train ...
    action_scale = float(os.environ.get("TESTBENCH_ACTION_SCALE", "1.0"))
    actions = {
        "joint_pos": JointPositionActionCfg(
            asset_name="robot",
            actuator_names=("1",),
            scale=action_scale,
            use_default_offset=True,
        ),
    }

    # Commands
    commands = {
        "target_angle": TargetAngleCommandCfg(
            asset_name="robot",
            joint_name="1",
            range=(-TESTBENCH_MAX_ANGLE_RAD, TESTBENCH_MAX_ANGLE_RAD),
            resampling_time_range=(4.0, 4.0),
            debug_vis=False,
        ),
    }

    # Events
    events = {
        "reset_joint": EventTermCfg(
            func=base_mdp.reset_joints_by_offset,
            mode="reset",
            params={
                "position_range": (0.0, 0.0),
                "velocity_range": (0.0, 0.0),
                "asset_cfg": asset_cfg_full,
            },
        ),
    }

    # Rewards (same regularization recipe as the microduck velocity env)
    rewards = {
        "track_target": RewardTermCfg(
            func=target_angle_tracking,
            weight=3.0,
            params={
                "command_name": "target_angle",
                "std": math.sqrt(0.15),
                "asset_cfg": asset_cfg_full,
            },
        ),
        "dof_pos_limits": RewardTermCfg(
            func=base_mdp.joint_pos_limits,
            weight=-1.0,
        ),
        "action_rate_l2": RewardTermCfg(
            func=base_mdp.action_rate_l2,
            weight=-0.6,
        ),
        "joint_torques_l2": RewardTermCfg(
            func=base_mdp.joint_torques_l2,
            weight=-1e-3,
        ),
        "joint_vel_l2": RewardTermCfg(
            func=base_mdp.joint_vel_l2,
            weight=-1e-3,
        ),
    }

    terminations = {
        "time_out": TerminationTermCfg(func=base_mdp.time_out, time_out=True),
    }

    return ManagerBasedRlEnvCfg(
        scene=SceneCfg(
            terrain=TerrainImporterCfg(terrain_type="plane"),
            entities={"robot": XL330_TESTBENCH_ROBOT_CFG},
            num_envs=1,
            extent=2.0,
        ),
        observations=observations,
        actions=actions,
        commands=commands,
        events=events,
        rewards=rewards,
        terminations=terminations,
        curriculum={},
        viewer=ViewerConfig(
            origin_type=ViewerConfig.OriginType.ASSET_BODY,
            asset_name="robot",
            body_name="arm",
            distance=0.8,
            elevation=-15.0,
            azimuth=90.0,
        ),
        sim=SimulationCfg(
            nconmax=10,
            njmax=50,
            mujoco=MujocoCfg(timestep=0.005, iterations=10, ls_iterations=20),
        ),
        decimation=4,
        episode_length_s=8.0,
    )


MicroduckTestbenchRlCfg = RslRlOnPolicyRunnerCfg(
    policy=RslRlPpoActorCriticCfg(
        init_noise_std=1.0,
        actor_obs_normalization=False,
        critic_obs_normalization=False,
        actor_hidden_dims=(256, 128, 64),
        critic_hidden_dims=(256, 128, 64),
        activation="elu",
    ),
    algorithm=RslRlPpoAlgorithmCfg(
        value_loss_coef=1.0,
        use_clipped_value_loss=True,
        clip_param=0.2,
        entropy_coef=0.01,
        num_learning_epochs=5,
        num_mini_batches=4,
        learning_rate=1.0e-3,
        schedule="adaptive",
        gamma=0.99,
        lam=0.95,
        desired_kl=0.01,
        max_grad_norm=1.0,
    ),
    wandb_project="mjlab_microduck",
    experiment_name="testbench",
    run_name="testbench",
    save_interval=200,
    num_steps_per_env=24,
    max_iterations=2000,
)
