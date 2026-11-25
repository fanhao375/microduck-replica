"""模型归档边界与无硬件预检；不写 robotd 配置、不接触总线。

这里只识别导出合同的基础身份。观测映射、滤波、任务适配和 ONNX 图的
完整兼容性由安装在本机的 robotd validate-policy 决定，不能用解包成功代替。
"""
from __future__ import annotations

import hashlib
import gzip
import json
import math
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unicodedata
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_FILES = 64
PREFLIGHT_TIMEOUT = 45

REQUIRED = frozenset(("policy.onnx", "manifest.json", "deployment-contract.json"))
OPTIONAL = frozenset(("deployment-execution.json", "deployment-runtime.json", "model-info.json"))
JOINT_NAMES = (
    "left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
    "neck_pitch", "head_pitch", "head_yaw", "head_roll", "right_hip_yaw",
    "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle",
)
BAM_SHA256 = "ef2d51adfb1cc0831b9b02ca19aa9176fceecdf725148d084378d7d9a64afceb"


class PackageError(ValueError):
    """可直接显示给导入用户的中文错误。"""


def _safe_name(raw: str, directory: bool) -> PurePosixPath:
    if not raw or "\\" in raw or ":" in raw or raw.startswith("/") or "\x00" in raw:
        raise PackageError(f"压缩包含不安全路径：{raw!r}")
    name = raw[:-1] if directory and raw.endswith("/") else raw
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise PackageError(f"压缩包含不规范路径：{raw!r}")
    if any(ord(c) < 32 for c in name):
        raise PackageError("压缩包路径含控制字符")
    return PurePosixPath(name)


def _zip_entries(archive):
    for info in archive.infolist():
        if info.orig_filename != info.filename:
            raise PackageError("压缩包路径含空字符")
        kind = stat.S_IFMT(info.external_attr >> 16)
        directory = info.is_dir()
        allowed = (0, stat.S_IFDIR) if directory else (0, stat.S_IFREG)
        if kind not in allowed:
            raise PackageError("压缩包含链接或特殊设备文件")
        if info.flag_bits & 1:
            raise PackageError("不支持加密的模型压缩包")
        yield info.filename, directory, info.file_size, lambda i=info: archive.open(i, "r")


def _tar_entries(archive):
    for info in archive:
        if (not info.isdir() and not info.isfile()) or info.issparse():
            raise PackageError("压缩包含链接、稀疏文件或特殊设备文件")
        yield info.name, info.isdir(), info.size, lambda i=info: archive.extractfile(i)


class _TarStream:
    """tarfile 在返回成员前会整块读取 PAX/长文件名，先限制这类分配。"""
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        if size < 0 or size > 1024 * 1024:
            raise PackageError("归档元数据过大，无法安全读取")
        return self.stream.read(size)

    def tell(self):
        return self.stream.tell()

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.tell() + offset
        # tar 的文件头、末尾填充不属于展开文件体积，但仍然有界。
        if whence not in (0, 1) or not 0 <= position <= MAX_EXPANDED_BYTES + MAX_FILES * 1024 + 10240:
            raise PackageError("归档展开体积超过上限")
        return self.stream.seek(offset, whence)


@contextmanager
def _tar_archive(path):
    with gzip.open(path, "rb") as stream:
        with tarfile.open(fileobj=_TarStream(stream), mode="r:") as archive:
            yield archive


def _layout(entries):
    """先检查整个归档，再读取文件，避免忽略未知文件里的危险条目。"""
    files, directories, seen = [], [], set()
    total = 0
    for raw, directory, size, opener in entries:
        if len(seen) >= MAX_FILES:
            raise PackageError(f"压缩包文件数量过多（最多 {MAX_FILES} 个文件或目录条目）")
        path = _safe_name(raw, directory)
        key = unicodedata.normalize("NFC", str(path)).casefold()
        if key in seen:
            raise PackageError(f"压缩包含重复路径：{path}")
        seen.add(key)
        if directory:
            directories.append(path)
            continue
        if size < 0 or size > MAX_FILE_BYTES:
            raise PackageError("模型单文件超过 128 MiB 上限")
        total += size
        if total > MAX_EXPANDED_BYTES:
            raise PackageError("压缩包展开体积超过 256 MiB 上限")
        files.append((path, size, opener))
    roots = [path.parent for path, _, _ in files if path.name == "policy.onnx"]
    if len(roots) > 1:
        raise PackageError("压缩包中有多个模型，请一次只导入一个模型目录")
    if not roots:
        raise PackageError("模型包缺少 policy.onnx")
    root = roots[0]
    for path, _, _ in files:
        if path.parent != root or path.name not in REQUIRED | OPTIONAL:
            raise PackageError(f"模型包包含不支持的文件：{path}；请仅打包模型及部署合同，移除训练日志等文件")
    if any(path != root and path not in root.parents for path in directories):
        raise PackageError("模型包包含不支持的额外目录")
    missing = REQUIRED - {path.name for path, _, _ in files}
    if missing:
        raise PackageError("模型包缺少：" + "、".join(sorted(missing)))
    return files


def _copy_file(opener, destination, size):
    if size == 0:
        raise PackageError(f"模型文件为空：{destination.name}")
    count = 0
    with opener() as source, destination.open("xb") as target:
        while chunk := source.read(min(1024 * 1024, size - count + 1)):
            count += len(chunk)
            if count > size or count > MAX_FILE_BYTES:
                raise PackageError("归档实际单文件体积与声明不一致或超过上限")
            target.write(chunk)
    if count != size:
        raise PackageError(f"归档文件不完整：{destination.name}")


def _json_object(path):
    # JSON 解析会为字符串、数组与字典再分配内存；不能沿用 ONNX 的单文件额度。
    if path.stat().st_size > MAX_JSON_BYTES:
        raise PackageError(f"{path.name} JSON 元数据超过 8 MiB 上限")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackageError(f"{path.name} JSON 含重复字段：{key}")
            result[key] = value
        return result

    def finite_number(value):
        number = float(value)
        if not math.isfinite(number):
            raise PackageError(f"{path.name} JSON 数值必须有限")
        return number

    def invalid_constant(value):
        raise PackageError(f"{path.name} JSON 含非法数值：{value}")

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs,
                           parse_float=finite_number, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PackageError(f"{path.name} JSON 无效：{exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"{path.name} JSON 必须是对象")
    return value


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _contract_basics(contract):
    if (contract.get("schema") != "microduck-deployment-contract/v1"
            or contract.get("task_adapter") not in ("hd1910-v1", "hd1910-v2", "hd1910-official0151")
            or type(contract.get("input_dim")) is not int or contract["input_dim"] != 61
            or type(contract.get("output_dim")) is not int or contract["output_dim"] != 14
            or contract.get("obs_normalizer") != "baked_in_onnx"
            or not isinstance(contract.get("task"), str) or not contract["task"].strip()):
        raise PackageError("部署合同必须是适配 HD1910 的 v1 合同，包含归一化的 61 维观测和 14 维动作")
    model = contract.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("joints"), list):
        raise PackageError("部署合同缺少 model.joints 关节 Home")
    joints = model["joints"]
    for name in JOINT_NAMES:
        rows = [row for row in joints if isinstance(row, dict) and row.get("name") == name]
        if len(rows) != 1 or not _finite(rows[0].get("home_rad")):
            raise PackageError(f"部署合同关节 Home 缺失、重复或无效：{name}")
    metadata = contract.get("onnx_metadata")
    if not isinstance(metadata, dict) or metadata.get("joint_names") != ",".join(JOINT_NAMES):
        raise PackageError("部署合同 ONNX 关节顺序与主程序不一致")
    actuators = model.get("actuators")
    if not isinstance(actuators, list) or len(actuators) != 1 or not isinstance(actuators[0], dict):
        raise PackageError("部署合同缺少唯一的 HD1910 BAM 驱动参数")
    actuator = actuators[0]
    parameter = actuator.get("parameter_file")
    p = actuator.get("kp_fw")
    if (not isinstance(parameter, dict) or parameter.get("sha256") != BAM_SHA256
            or not _finite(p) or not 1 <= p <= 32 or int(p) != p):
        raise PackageError("部署合同 HD1910 BAM 参数校验或固件 P 值无效")
    actions = contract.get("actions")
    if not isinstance(actions, dict) or not isinstance(actions.get("joint_pos"), dict):
        raise PackageError("部署合同缺少 actions.joint_pos 动作映射")
    observations = contract.get("observations")
    if not isinstance(observations, dict) or not isinstance(observations.get("terms"), dict):
        raise PackageError("部署合同缺少 observations.terms 观测映射")


def _recommended_slot(contract):
    task = contract["task"]
    if task in ("Mjlab-BallKick-Flat-MicroDuck", "Mjlab-BallKickLeft-Flat-Backlash-MicroDuck"):
        return contract.get("slot") if contract.get("slot") in ("kick_left", "kick_right") else None
    if task.startswith("Mjlab-Velocity-") or task in ("Mjlab-VelStand-Flat-MicroDuck", "Mjlab-VelStand-Rough-Backlash-MicroDuck"):
        return "walk"
    for prefix, slot in (("Mjlab-SitStand-", "sitstand"), ("Mjlab-StandUp-", "stand"),
                         ("Mjlab-GroundPick-", "ground_pick"), ("Mjlab-Roulade-", "roulade")):
        if task.startswith(prefix):
            return slot
    if task in ("Mjlab-RollerCrouch-Flat-Backlash-MicroDuck", "Mjlab-Spin-Flat-MicroDuck"):
        return "ground_pick"
    return None


def _metadata(directory, names):
    manifest = _json_object(directory / "manifest.json")
    contract = _json_object(directory / "deployment-contract.json")
    with (directory / "policy.onnx").open("rb") as policy:
        digest = hashlib.file_digest(policy, "sha256").hexdigest()
    if manifest.get("onnx_sha256") != digest:
        raise PackageError("ONNX SHA256 与 manifest.json 校验不一致")
    if manifest.get("task") != contract.get("task"):
        raise PackageError("manifest.json 与部署合同的 task 任务不一致")
    _contract_basics(contract)
    for name, schema in (("deployment-runtime.json", "microduck-runtime-commands/v1"),
                         ("deployment-execution.json", "microduck-runtime-execution/v1")):
        if name in names:
            value = _json_object(directory / name)
            if value.get("schema") != schema or value.get("model_sha256") != digest:
                raise PackageError(f"{name} 的格式或模型 SHA256 校验不一致")
    info = _json_object(directory / "model-info.json") if "model-info.json" in names else {}
    name = info.get("name") or manifest.get("name")
    return {"onnx": "policy.onnx", "sha256": digest, "task": contract["task"],
            "slot": _recommended_slot(contract), "name": name.strip() if isinstance(name, str) else None,
            "files": sorted(names)}


def extract_package(archive: Path, destination: Path) -> dict:
    """导入单个 ZIP/tar.gz/tgz 模型；成功才交付目录，拒绝覆盖已有内容。"""
    archive, destination = Path(archive), Path(destination)
    if destination.is_symlink() or (destination.exists() and
            (not destination.is_dir() or any(destination.iterdir()))):
        raise PackageError("导入目标必须是新目录或已存在的空目录")
    try:
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PackageError("模型压缩包超过 64 MiB 上限")
        if archive.name.lower().endswith(".zip"):
            container = zipfile.ZipFile(archive, "r")
            reader = _zip_entries
        elif archive.name.lower().endswith((".tar.gz", ".tgz")):
            container = _tar_archive(archive)
            reader = _tar_entries
        else:
            raise PackageError("仅支持 .zip、.tar.gz 或 .tgz 模型压缩包")
        with container as contents:
            files = _layout(reader(contents))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".model-import-", dir=destination.parent) as temporary:
                stage = Path(temporary) / "model"
                stage.mkdir()
                for path, size, opener in files:
                    _copy_file(opener, stage / path.name, size)
                metadata = _metadata(stage, {path.name for path, _, _ in files})
                if destination.exists():
                    destination.rmdir()
                os.replace(stage, destination)
                return metadata
    except PackageError:
        raise
    except (OSError, EOFError, ValueError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile, tarfile.TarError, zlib.error) as exc:
        raise PackageError(f"无法读取或展开模型压缩包：{exc}") from exc


# ── 「更多动作」的包：一个 ONNX，加可有可无的说明文件 ───────────────────────────
#
# 跟上面七个固定用途的部署包不同：这种包不要求部署合同，主程序按名字登记它、用内置站姿跑。
# 有一条硬规矩：解出来以后，模型旁边**只能有模型本身**。主程序看到 .onnx 同目录有
# deployment-contract.json 就会去解析，解析不过时登记照样成功，但下次开机整套动作都起不来。
# 所以包里除模型外的文件一律收进 extras/ 子目录，主程序不会往子目录里找。

ACTION_ONNX = "policy.onnx"
ACTION_EXTRAS = "extras"
ACTION_SUGGEST_LIMIT = 64 * 1024
ACTION_NAME = r"[a-z0-9_]{1,32}"


def _num(value, low, high, *, low_open=False):
    """建议值只收有限的数，越界就当作没给，不让一份写坏的说明挡住导入。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    if not math.isfinite(value) or value > high or (value <= low if low_open else value < low):
        return None
    return value


def _triple(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    numbers = [_num(v, -2.0, 2.0) for v in value]
    return None if any(v is None for v in numbers) else numbers


def action_suggestion(raw: dict) -> dict:
    """从 skill.toml 的一条 [[policy.skill]] 或 action.json 里挑出页面预填用的值。"""
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    out = {}
    name = raw.get("name")
    if isinstance(name, str) and re.fullmatch(ACTION_NAME, name):
        out["name"] = name
    title = raw.get("title")
    if isinstance(title, str) and title.strip() and len(title.strip()) <= 80 and not any(ord(c) < 32 for c in title):
        out["title"] = title.strip()
    for key, low, high, low_open in (("duration", 0.0, 60.0, True), ("unwind_s", 0.0, 10.0, False)):
        value = _num(raw.get(key), low, high, low_open=low_open)
        if value is not None:
            out[key] = value
    for key in ("command", "unwind"):
        value = _triple(raw.get(key))
        if value is not None:
            out[key] = value
    if isinstance(raw.get("chain"), bool):
        out["chain"] = raw["chain"]
    for key, low, high, low_open in (("action_scale", 0.0, 2.0, True), ("gain_ratio", 0.2, 2.0, False)):
        value = _num(params.get(key, raw.get(key)), low, high, low_open=low_open)
        if value is not None:
            out[key] = value
    return out


def _read_suggestion(path: Path) -> dict:
    """读不出来不算错：建议值只是帮人少填几个数。"""
    try:
        if path.stat().st_size > ACTION_SUGGEST_LIMIT:
            return {}
        if path.name.lower() == "skill.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            rows = (data.get("policy") or {}).get("skill") if isinstance(data.get("policy"), dict) else None
            row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
            return action_suggestion(row) if row else {}
        data = _json_object(path)
        return action_suggestion(data)
    except (OSError, UnicodeError, ValueError, RecursionError, PackageError, AttributeError, TypeError):
        return {}


def _action_layout(entries):
    files, seen, total = [], set(), 0
    for raw, directory, size, opener in entries:
        if len(seen) >= MAX_FILES:
            raise PackageError(f"压缩包文件数量过多（最多 {MAX_FILES} 个文件或目录条目）")
        path = _safe_name(raw, directory)
        key = unicodedata.normalize("NFC", str(path)).casefold()
        if key in seen:
            raise PackageError(f"压缩包含重复路径：{path}")
        seen.add(key)
        if directory:
            continue
        if size < 0 or size > MAX_FILE_BYTES:
            raise PackageError("单个文件超过 128 MiB 上限")
        total += size
        if total > MAX_EXPANDED_BYTES:
            raise PackageError("压缩包展开体积超过 256 MiB 上限")
        files.append((path, size, opener))
    models = [p for p, _, _ in files if p.suffix.lower() == ".onnx"]
    if not models:
        raise PackageError("动作包里没有 .onnx 模型文件")
    if len(models) > 1:
        named = [p for p in models if p.name == ACTION_ONNX]
        if len(named) != 1:
            raise PackageError("动作包里有好几个 .onnx，又没有唯一一个叫 policy.onnx 的；请只留下要装的那个模型")
        models = named
    return files, models[0]


def _action_metadata(stage: Path, model: Path, extras: list) -> dict:
    with (stage / ACTION_ONNX).open("rb") as policy:
        digest = hashlib.file_digest(policy, "sha256").hexdigest()
    suggest = {}
    # 跟模型同一层的说明优先，其次包里任意位置的；skill.toml 优先于 action.json。
    candidates = sorted((p for p in extras if PurePosixPath(p).name.lower() in ("skill.toml", "action.json")),
                        key=lambda p: (PurePosixPath(p).parent != model.parent, PurePosixPath(p).name.lower() != "skill.toml", p))
    for rel in candidates:
        suggest = _read_suggestion(stage / ACTION_EXTRAS / rel)
        if suggest:
            break
    return {"onnx": ACTION_ONNX, "sha256": digest, "files": [ACTION_ONNX] + [ACTION_EXTRAS + "/" + p for p in extras],
            "suggest": suggest}


def extract_action_package(archive: Path, destination: Path) -> dict:
    """导入一个「更多动作」：单个 .onnx，或 ZIP / tar.gz（里面一个模型 + 任意说明文件）。

    成功才交付目录。交付后的目录里，模型固定叫 policy.onnx，其余文件都在 extras/ 下。
    """
    archive, destination = Path(archive), Path(destination)
    if destination.is_symlink() or (destination.exists() and
            (not destination.is_dir() or any(destination.iterdir()))):
        raise PackageError("导入目标必须是新目录或已存在的空目录")
    try:
        size = archive.stat().st_size
        lower = archive.name.lower()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".action-import-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "action"
            stage.mkdir()
            if lower.endswith(".onnx"):
                if size > MAX_FILE_BYTES:
                    raise PackageError("模型文件超过 128 MiB 上限")
                _copy_file(lambda: archive.open("rb"), stage / ACTION_ONNX, size)
                metadata = _action_metadata(stage, PurePosixPath(ACTION_ONNX), [])
            else:
                if size > MAX_ARCHIVE_BYTES:
                    raise PackageError("动作包超过 64 MiB 上限")
                if lower.endswith(".zip"):
                    container, reader = zipfile.ZipFile(archive, "r"), _zip_entries
                elif lower.endswith((".tar.gz", ".tgz")):
                    container, reader = _tar_archive(archive), _tar_entries
                else:
                    raise PackageError("仅支持 .onnx 文件，或 .zip、.tar.gz、.tgz 动作包")
                with container as contents:
                    files, model = _action_layout(reader(contents))
                    extras = []
                    for path, length, opener in files:
                        if path == model:
                            _copy_file(opener, stage / ACTION_ONNX, length)
                            continue
                        target = stage / ACTION_EXTRAS / Path(*path.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if length == 0:
                            target.touch(exist_ok=False)
                        else:
                            _copy_file(opener, target, length)
                        extras.append(str(path))
                metadata = _action_metadata(stage, model, sorted(extras))
            if destination.exists():
                destination.rmdir()
            os.replace(stage, destination)
            return metadata
    except PackageError:
        raise
    except (OSError, EOFError, ValueError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile, tarfile.TarError, zlib.error) as exc:
        raise PackageError(f"无法读取或展开动作包：{exc}") from exc


def validate_policy(path: Path, executable: str, *, env=None) -> dict:
    """运行 robotd 的离线图预检；继承进程环境或使用调用方提供的运行库环境。"""
    try:
        result = subprocess.run([executable, "validate-policy", str(path)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=PREFLIGHT_TIMEOUT, shell=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise PackageError("模型运行器预检超时，请检查模型大小和 ONNX Runtime") from exc
    except OSError as exc:
        raise PackageError(f"无法启动模型运行器预检：{exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "运行器未返回原因").strip()[:2000]
        raise PackageError(f"模型运行器预检失败：{detail}")
    try:
        def invalid_constant(value):
            raise ValueError(f"非法 JSON 数值：{value}")
        data = json.loads(result.stdout, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise PackageError("模型运行器预检返回了无效 JSON") from exc
    if not isinstance(data, dict) or data.get("valid") is not True:
        raise PackageError("模型运行器未确认预检通过（valid 必须为 true）")
    return data
