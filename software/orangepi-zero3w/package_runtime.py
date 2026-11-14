#!/usr/bin/env python3
"""Package an existing ARM64 payload with the reviewed Orange Pi sources.

The input is a prepared payload, not a dump of a running board. ELF diagnostic
paths are anonymized without changing layout; ONNX weights retain their bytes.
"""

import argparse
import getpass
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
RELEASE = "orangepi-quanzhi-20261112-r2"
EXCLUDED = {
    "__pycache__", ".DS_Store", "export-data.zip", "training-config.json",
    "training-request.json", "upstream-params", "使用说明.md",
}
FORBIDDEN = {
    "password.json", "sessions.json", "authorized_keys", "known_hosts",
    "id_rsa", "id_ed25519", "credentials.json", ".env",
}


def ignore(_directory, names):
    return [name for name in names if name in EXCLUDED or name.endswith(".pyc")]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_reviewed(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.is_file() and sha256(source) == sha256(destination):
        return destination
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    return shutil.copy2(source, destination)


def build(source, output, private_user):
    if output.exists():
        raise ValueError("Output must be a new directory")
    for path in source.rglob("*"):
        if path.name in FORBIDDEN or path.name.endswith(".nmconnection"):
            raise ValueError("Unexpected credential/state path: " + str(path.relative_to(source)))
    output.mkdir(parents=True)
    base = output / "orangepi-quanzhi"
    payload = base / "payload"
    shutil.copytree(source, payload, symlinks=True, ignore=ignore)
    # Replace the archived developer source snapshot with its public copy.
    snapshot = payload / "opt/11quanzhi-source"
    shutil.rmtree(snapshot)
    shutil.copytree(HERE / "migration/source", snapshot)
    shutil.copytree(HERE / "migration/studio/payload/opt/microduck-studio/app",
                    payload / "opt/microduck-studio/app", dirs_exist_ok=True, ignore=ignore,
                    copy_function=replace_reviewed)
    shutil.copy2(HERE / "migration/install.py", base / "install.py")

    robot = payload / "opt/robot/feetech-ft5-r5"
    # Training metadata is not needed at runtime. Keep ONNX bytes intact; scrub
    # private build paths/network addresses only in the remaining JSON metadata.
    for root in (robot / "models", robot / "roller"):
        for path in root.rglob("*.json"):
            text = path.read_text()
            text = re.sub(r"/(?:Users|home)/[^\s\"']+", "/build/source", text)
            text = re.sub(r"\b192\.168\.\d+\.\d+\b", "192.0.2.1", text)
            json.loads(text)
            path.write_text(text)

    upstream = HERE.parents[1]
    shutil.copy2(upstream / "software/studio/LICENSE", robot / "LICENSE")
    model_notice = (upstream / "software/image/overlay/usr/share/doc/microduck/模型说明.txt").read_text()
    model_hashes = "\n".join(f"{sha256(path)}  {path.relative_to(robot)}"
                             for path in sorted(robot.rglob("policy.onnx")))
    model_notice = model_notice.replace("@@MODEL_LIST@@", model_hashes)
    model_notice = re.sub(r"  - policy.onnx.*?(?=  - 为了)",
                          "  - policy.onnx 权重保持原字节；运行 JSON 中的构建路径和内网地址已泛化。\n",
                          model_notice, flags=re.S)
    model_notice = re.sub(r"  - 运行参数由.*?(?=\n三、)",
                          "  - 运行参数以本包 /etc/robot/feetech-ft5/robotd.toml 为准；策略关闭，默认只读。\n",
                          model_notice, flags=re.S)
    model_notice += ("\nOrange Pi 移植补充：本包策略关闭，未校准，仅只读。\n"
                     "ONNX 权重未修改；移除训练导出文件，JSON 中构建路径和内网地址已泛化。\n"
                     "随包 robotd 仅将个人编译路径等长泛化；ELF 布局和可执行指令保持不变，修改后须通过 ARM64 运行验证；不含设备认证配置。\n")
    (robot / "MODEL-LICENSE-AND-NOTICE.txt").write_text(model_notice)

    records = []
    for path in sorted(payload.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        record = {"path": path.relative_to(payload).as_posix(), "mode": path.lstat().st_mode & 0o777}
        if path.is_symlink():
            record["symlink"] = str(path.readlink())
        else:
            record.update(sha256=sha256(path), bytes=path.stat().st_size)
        records.append(record)
    manifest = {"release": RELEASE, "files": records,
                "archived_only": ["Cubie-only platform code", "host tools", "offline prototype"]}
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    sanitizer_spec = importlib.util.spec_from_file_location("runtime_paths", HERE / "image/sanitize_runtime.py")
    sanitizer = importlib.util.module_from_spec(sanitizer_spec)
    sanitizer_spec.loader.exec_module(sanitizer)
    sanitizer.sanitize(base, output / "path-sanitization.json", private_user)
    spec = importlib.util.spec_from_file_location("orangepi_install", base / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.verify_payload(base)

    def metadata(info):
        if "__pycache__" in Path(info.name).parts or info.name.endswith(".pyc"):
            return None
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    archive = output / (RELEASE + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(base, arcname="orangepi-quanzhi", filter=metadata)
    print(json.dumps({"archive": archive.name, "files": len(records),
                      "sha256": sha256(archive), "bytes": archive.stat().st_size}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="Existing prepared root-layout payload")
    parser.add_argument("output", type=Path, help="New output directory")
    parser.add_argument("--private-user", default=getpass.getuser())
    args = parser.parse_args()
    build(args.payload.resolve(), args.output.resolve(), args.private_user)
