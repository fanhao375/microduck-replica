#!/usr/bin/env python3
"""Combine audited runtime payloads and the program source snapshot."""

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "bundle"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def build():
    required = (
        ROOT / "control/bundle/payload/opt/robot/feetech-ft5-r5/robotd",
        ROOT / "studio/payload/opt/microduck-studio/python/bin/python3.13",
        ROOT / "studio/payload/opt/microduck-studio/app/deps/starlette/__init__.py",
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError("Runtime files are stored in the Release, not Git; populate the prepared payloads before building")
    OUT.mkdir(exist_ok=False)
    payload = OUT / "payload"
    payload.mkdir()
    for source in (ROOT / "control/bundle/payload", ROOT / "studio/payload"):
        shutil.copytree(source, payload, dirs_exist_ok=True, symlinks=True)
    shutil.copytree(ROOT / "source", payload / "opt/11quanzhi-source", symlinks=True)
    shutil.copy2(ROOT / "install.py", OUT / "install.py")
    records = []
    for path in sorted(payload.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        item = {"path": path.relative_to(payload).as_posix(), "mode": path.lstat().st_mode & 0o777}
        if path.is_symlink():
            item["symlink"] = os.readlink(path)
            if Path(item["symlink"]).is_absolute() or not path.resolve().is_relative_to(payload):
                raise ValueError("Nonrelocatable symlink: " + str(path))
        elif path.is_file():
            item.update(sha256=digest(path), bytes=path.stat().st_size)
        else:
            raise ValueError("Special file in application payload")
        records.append(item)
    manifest = {"release": "orangepi-quanzhi-20261109", "files": records,
                "archived_only": ["Cubie thermal policy", "original Cubie provision",
                                  "host flashing/wiring tools", "offline prototype and unapplied tracking patches"]}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    # Reject unexpected payload paths and byte/mode drift before creating a root-run archive.
    from install import verify_payload
    verify_payload(OUT)
    archive = ROOT / "orangepi-quanzhi-20261109.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(OUT, arcname="orangepi-quanzhi", recursive=True)
    (ROOT / (archive.name + ".sha256")).write_text(digest(archive) + "  " + archive.name + "\n")
    print(json.dumps({"files": len(records), "archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": digest(archive)}, indent=2))


if __name__ == "__main__":
    build()
