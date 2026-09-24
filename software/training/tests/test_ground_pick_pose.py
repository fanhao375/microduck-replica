import torch
from mjlab_microduck.tasks.mdp import phase_pose_blend

DESCENT_END, HOLD_END, RISE_END = 0.15, 0.50, 0.65


def test_phase_pose_blend_keypoints():
    phase = torch.tensor([0.0, 0.075, 0.15, 0.30, 0.50, 0.575, 0.65, 0.80])
    b = phase_pose_blend(phase, DESCENT_END, HOLD_END, RISE_END)
    expected = torch.tensor([0.0, 0.5, 1.0, 1.0, 1.0, 0.5, 0.0, 0.0])
    assert torch.allclose(b, expected, atol=1e-6), b


def test_phase_pose_blend_range():
    phase = torch.linspace(0.0, 1.0, 101)
    b = phase_pose_blend(phase, DESCENT_END, HOLD_END, RISE_END)
    assert b.min() >= 0.0 and b.max() <= 1.0


from mjlab_microduck.tasks.mdp import phase_pose_track, phase_pose_track_l1


class _FakeData:
    def __init__(self, joint_pos, default_pos):
        self.joint_pos = joint_pos
        self.default_joint_pos = default_pos


class _FakeAsset:
    def __init__(self, names, joint_pos, default_pos):
        self._ids = {n: i for i, n in enumerate(names)}
        self.data = _FakeData(joint_pos, default_pos)

    def find_joints(self, query):
        # mjlab renvoie (ids, names) ; on ne gère que la requête [name]
        (name,) = query
        return ([self._ids[name]], [name])


class _FakeCmdMgr:
    def __init__(self, cmd):
        self._cmd = cmd

    def get_command(self, _name):
        return self._cmd


class _FakeEnv:
    def __init__(self, names, joint_pos, default_pos, phase):
        import math
        self.device = "cpu"
        self.scene = {"robot": _FakeAsset(names, joint_pos, default_pos)}
        ang = 2 * math.pi * phase
        cmd = torch.tensor([[math.cos(ang), math.sin(ang), 0.0]])
        self.command_manager = _FakeCmdMgr(cmd)


NAMES = ["j0", "j1"]
DOWN = {"j0": 1.0, "j1": -1.0}
# HOME (STAND source) = 0 pour les deux joints
HOME = torch.tensor([[0.0, 0.0]])


def _env(cur, phase):
    return _FakeEnv(NAMES, torch.tensor([cur]), HOME.clone(), phase)


def test_phase_pose_track_perfect_at_down():
    # phase 0.30 -> blend 1 -> cible = DOWN ; cur == DOWN -> gaussienne 1, l1 0
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([1.0, -1.0], phase=0.30)
    r = phase_pose_track(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-6), r
    env2 = _env([1.0, -1.0], phase=0.30)
    l1 = phase_pose_track_l1(env2, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(l1, torch.tensor([0.0]), atol=1e-6), l1


def test_phase_pose_track_l1_at_home_when_down_target():
    # phase 0.30 -> cible DOWN=[1,-1] ; cur=HOME=[0,0] -> l1 = -mean(|1|,|1|) = -1
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([0.0, 0.0], phase=0.30)
    l1 = phase_pose_track_l1(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(l1, torch.tensor([-1.0]), atol=1e-6), l1


def test_phase_pose_track_returns_to_stand():
    # phase 0.80 -> blend 0 -> cible = HOME ; cur=HOME -> gaussienne 1
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    env = _env([0.0, 0.0], phase=0.80)
    r = phase_pose_track(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-6), r


def test_phase_pose_track_affine_interpolation_nonzero_home():
    # HOME (source) nonzero, blend 0.5 at phase 0.075:
    # target = home + 0.5*(down-home) = [0.4,-0.4] + 0.5*([1,-1]-[0.4,-0.4]) = [0.7,-0.7]
    from mjlab.managers.scene_entity_config import SceneEntityCfg
    cfg = SceneEntityCfg("robot")
    home = torch.tensor([[0.4, -0.4]])
    env = _FakeEnv(NAMES, torch.tensor([[0.7, -0.7]]), home.clone(), phase=0.075)
    r = phase_pose_track(env, target_pose=DOWN, asset_cfg=cfg)
    assert torch.allclose(r, torch.tensor([1.0]), atol=1e-6), r


def test_ground_pick_cmd_cfg_has_randomize_phase_default_true():
    from mjlab_microduck.tasks.mdp import GroundPickPhaseCommandCfg
    from mjlab.tasks.velocity.mdp import UniformVelocityCommandCfg
    # construit une cfg minimale en copiant une cfg velocity par défaut
    # NOTE: adapté de `asset_name` (brief) -> `entity_name` (API locale de
    # UniformVelocityCommandCfg, qui n'a pas de champ `asset_name`).
    base = UniformVelocityCommandCfg(
        entity_name="robot", resampling_time_range=(10.0, 10.0),
        ranges=UniformVelocityCommandCfg.Ranges(
            lin_vel_x=(0.0, 0.0), lin_vel_y=(0.0, 0.0), ang_vel_z=(0.0, 0.0),
        ),
    )
    cfg = GroundPickPhaseCommandCfg(**{**vars(base)})
    assert cfg.randomize_phase is True
    assert cfg.period == 4.0
