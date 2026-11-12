#!/usr/bin/env python3
"""Copy the portable program sources from 11quanzhi into migration/source.

This archives source code only. Runtime installation and board-specific service
activation belong to the separate Orange Pi migration installer.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "source"
SOURCE_ROOTS = (
    "radxa-cubie-a7z/control",
    "radxa-cubie-a7z/studio",
    "radxa-cubie-a7z/provision",
    "radxa-cubie-a7z/thermal",
    "prototypes/smart-duck-console",
)
HOST_FILES = (
    "radxa-cubie-a7z/os/flash-sd.sh",
    "output/camera-wiring/draw_wiring.py",
)
IGNORED_DIRS = {
    ".git", ".codex", ".cursor", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", "artifacts", "workspace",
    "evidence", "screenshots", "state", "backups", "auth", "credentials",
}
SOURCE_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".css", ".html", ".json", ".patch",
    ".rs", ".c", ".h", ".md", ".txt", ".toml", ".yaml", ".yml",
    ".sh", ".service", ".conf", ".svg", ".png", ".jpg", ".jpeg",
    ".webp", ".urdf", ".obj", ".stl", ".mtl", ".dae", ".gltf", ".glb",
    ".bin", ".onnx", ".typed", ".rst", ".mit", ".apache2",
}
SECRET_NAMES = {
    "authorized_keys", "known_hosts", "id_rsa", "id_ed25519", "auth.json",
    "credentials.json", "state.json", "calibration.json", "servo_offsets.json",
    "imu_mount.json",
}
METADATA_NAMES = {"source-files.json", "source-files.sha256", "scope.json"}
PRODUCER = "11quanzhi-program-source-snapshot-v1"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_file_allowed(relative):
    if any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in relative.parts):
        return False
    name = relative.name
    if name.startswith(".") or name in SECRET_NAMES:
        return False
    return relative.suffix.lower() in SOURCE_SUFFIXES or name in {
        "LICENSE", "NOTICE", "COPYING", "Makefile", "CMakeLists.txt",
    }


def collect_files(origin):
    selected = set()
    for root_name in SOURCE_ROOTS:
        root = origin / root_name
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Missing regular source directory: " + root_name)
        # Pruning prevents even inspecting caches, prior device state or rebuilt
        # workspaces. Symlinks are never followed into other repositories.
        for directory, subdirs, names in os.walk(root, followlinks=False):
            directory = Path(directory)
            subdirs[:] = sorted(name for name in subdirs
                                if name not in IGNORED_DIRS
                                and not name.startswith(".")
                                and not name.endswith(".egg-info")
                                and not (directory / name).is_symlink())
            for name in names:
                path = directory / name
                relative = path.relative_to(origin)
                if path.is_file() and not path.is_symlink() and source_file_allowed(relative):
                    selected.add(relative)
    for relative in map(Path, HOST_FILES):
        path = origin / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing regular host reference: " + str(relative))
        selected.add(relative)
    return sorted(selected, key=lambda path: path.as_posix())


def source_revision(origin):
    result = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def write_metadata(stage, origin, entries):
    (stage / "source-files.json").write_text(
        json.dumps({"format": 1, "files": entries}, ensure_ascii=False, indent=2) + "\n")
    (stage / "source-files.sha256").write_text(
        "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries))
    scope = {
        "producer": PRODUCER,
        "origin_project": "11quanzhi",
        "origin_git_revision": source_revision(origin),
        "worktree_snapshot": True,
        "board_destination": "/opt/11quanzhi-source",
        "source_roots": list(SOURCE_ROOTS),
        "files": len(entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "checksums_cover": "Copied source files; generated metadata is excluded from its own manifest.",
        "runtime_installation": "Separate migration payload; this source archive enables no services.",
        "archived_only": [
            {"path": "radxa-cubie-a7z/thermal", "reason": "Guarded for Cubie A7Z kernel 5.15.147-21-a733; do not apply its sysfs policy writes to Orange Pi kernel 6.6.98."},
            {"path": "radxa-cubie-a7z/provision", "reason": "Reference only: the working orangepi-provision service already provides provisioning; starting the Cubie version would compete for networking and ports."},
            {"path": "prototypes/smart-duck-console", "reason": "Complete offline demonstration and model assets; not a replacement for the live Studio backend."},
            {"path": HOST_FILES[0], "reason": "Host-only macOS writer for a Radxa system image; never execute on the Orange Pi."},
            {"path": HOST_FILES[1], "reason": "Host-only Cubie camera wiring illustration generator using Pillow and a macOS font path."},
        ],
        "excluded": [
            "Git/editor/MCP configuration and .env files",
            "Caches, virtualenvs, artifacts, reconstructed workspaces and runtime archives/binaries",
            "Previous-board state, calibration, authentication material and private keys",
            "Evidence logs, test logs, screenshots, operating system images and hardware downloads",
        ],
    }
    (stage / "scope.json").write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n")


def verify_snapshot(folder, origin=None):
    scope = json.loads((folder / "scope.json").read_text())
    if scope.get("producer") != PRODUCER:
        raise ValueError("Not a source snapshot managed by this builder")
    entries = json.loads((folder / "source-files.json").read_text())["files"]
    expected = {entry["path"] for entry in entries}
    found = set()
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("Unexpected symlink in snapshot: " + str(path))
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if name not in METADATA_NAMES:
                found.add(name)
    if found != expected:
        raise ValueError("Snapshot file set differs from manifest")
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not source_file_allowed(relative):
            raise ValueError("Unsafe or excluded manifest path: " + str(relative))
        path = folder / relative
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError("Snapshot checksum mismatch: " + str(relative))
        if stat.S_IMODE(path.stat().st_mode) != int(entry["mode"], 8):
            raise ValueError("Snapshot mode mismatch: " + str(relative))
        if origin is not None and sha256(origin / relative) != entry["sha256"]:
            raise ValueError("Original changed while copying: " + str(relative))
    expected_checksums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    if (folder / "source-files.sha256").read_text() != expected_checksums:
        raise ValueError("SHA256 checksum list differs from manifest")
    return scope


def build(origin, replace):
    if OUTPUT.is_symlink():
        raise ValueError("Refusing a symlink output directory")
    if OUTPUT.exists():
        if not replace:
            raise ValueError("Snapshot exists; use --replace to rebuild only migration/source")
        verify_snapshot(OUTPUT)
    HERE.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=HERE))
    try:
        entries = []
        for relative in collect_files(origin):
            if "\n" in str(relative) or "\r" in str(relative):
                raise ValueError("Newline in source filename")
            source = origin / relative
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
            destination.chmod(mode)
            entries.append({"path": relative.as_posix(), "bytes": destination.stat().st_size,
                            "mode": oct(mode), "sha256": sha256(destination)})
        write_metadata(stage, origin, entries)
        scope = verify_snapshot(stage, origin)
        if OUTPUT.exists():
            shutil.rmtree(OUTPUT)
        stage.rename(OUTPUT)
        return scope
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Original source checkout; required when creating a new snapshot")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        scope = verify_snapshot(OUTPUT, args.source.resolve() if args.source else None)
    else:
        if args.source is None:
            parser.error("--source is required when creating a snapshot")
        scope = build(args.source.resolve(), args.replace)
    print(json.dumps({"output": str(OUTPUT), "files": scope["files"], "bytes": scope["bytes"],
                      "verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
