#!/usr/bin/env python3
"""Apply an offline camera overlay to the published Cubie A7Z preview1 disk image."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import build_image as image

BASE_SHA256 = "5ff1112d37f843f6da3335a0e1f56f741945acd639515a3b840967c2146a9e25"
DISABLED = "/boot/dtbo/cubie-a7a-radxa-camera-8m-219.dtbo.disabled"
PROTECTED = (
    "/opt/robot/feetech-ft5-r5/robotd",
    "/opt/robot/feetech-ft5-r5/robotd-profile.toml",
    "/etc/robot/robotd.toml",
    "/etc/robot/feetech-ft5/robotd.toml",
    "/etc/robot/feetech-ft5/robotd.saved.toml",
    "/etc/robot/feetech-ft5/hd1910-calibration.toml",
    "/etc/systemd/system/robotd.service",
    "/etc/systemd/system/multi-user.target.wants/robotd.service",
    "/etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf",
    "/etc/systemd/system/robotd.service.d/zzzz-zz-microduck-commissioning.conf",
)


def range_sha(path, start, size):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        source.seek(start)
        while size:
            block = source.read(min(image.CHUNK, size))
            if not block:
                raise ValueError("unexpected end while checking disk range")
            hasher.update(block)
            size -= len(block)
    return hasher.hexdigest()


def move_tail(source, destination, start, offset):
    """Move bytes from a disposable file tail, keeping only one on-disk copy."""
    end = source.seek(0, os.SEEK_END)
    while end > start:
        cursor = max(start, end - 8 * image.CHUNK)
        source.seek(cursor)
        destination.seek(offset + cursor - start)
        image.copy_bytes(source, destination, end - cursor)
        destination.flush()
        source.truncate(cursor)
        end = cursor


def robot_state(fs, work, paths=PROTECTED):
    work.mkdir()
    records = image.image_stats(fs, paths, work)
    result = {}
    for index, path in enumerate(paths):
        if path not in records:
            raise ValueError(f"missing protected robotd path {path}")
        record = records[path]
        props = {key: record[key] for key in ("kind", "uid", "gid", "mode")}
        if record["kind"] == "symlink":
            match = re.search(r'Fast link dest: "(.*)"', record["body"])
            if match:
                props["target"] = match[1]
            else:
                target = work / str(index)
                image.debugfs(fs, [f"dump {image.quote(path)} {image.quote(target)}"], work)
                props["target"] = target.read_text()
        elif record["kind"] == "regular":
            target = work / str(index)
            image.debugfs(fs, [f"dump {image.quote(path)} {image.quote(target)}"], work)
            props["sha256"] = image.digest(target)
        else:
            raise ValueError(f"unexpected robotd path type {path}")
        result[path] = props
    return result


def build(base, overlay, output, *, base_sha256=BASE_SHA256,
          removals=(DISABLED,), protected=PROTECTED, release=None, absent=(), expected_files=None):
    base, overlay = base.resolve(), overlay.resolve()
    output = output.absolute()
    sha_file = Path(str(output) + ".sha256")
    manifest_file = Path(str(output) + ".manifest.json")
    for dest in (output, sha_file, manifest_file):
        if os.path.lexists(dest):
            raise ValueError(f"already exists: {dest}")
    if not output.name.endswith(".img.xz") or not overlay.is_dir() or not base.is_file():
        raise ValueError("expected a base image, update overlay and .img.xz output")
    if not re.fullmatch(r"[0-9a-f]{64}", base_sha256) or image.digest(base) != base_sha256:
        raise ValueError("input does not match the pinned base SHA256")
    removals = sorted({image.absolute_path(path) for path in removals})
    def protected_path(path):
        return (path in protected or path.startswith("/etc/systemd/system/robotd.service.d/")
                or path == "/boot/efi" or path.startswith("/boot/efi/")
                or path == "/config" or path.startswith("/config/"))
    if any(protected_path(path) for path in removals):
        raise ValueError("cannot remove a protected path")
    entries = image.overlay_entries(overlay)
    if not entries:
        raise ValueError("empty camera overlay")
    for entry in entries:
        path = entry["path"]
        if protected_path(path) or path == DISABLED:
            raise ValueError(f"overlay contains protected or separately mounted path {path}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".camera-image-", dir=output.parent) as temp:
        work = Path(temp)
        raw, fs = work / "disk.img", work / "rootfs.ext4"
        print("Decompressing pinned base image", flush=True)
        with raw.open("wb") as target:
            subprocess.run([image.tool("xz"), "-dc", str(base)], stdout=target, check=True)
        layout = image.partitions(raw)
        root = image.root_partition(layout, 3)
        if root["label"] != "rootfs":
            raise ValueError("partition 3 is not labelled rootfs")
        size = raw.stat().st_size
        offset, length = root["offset"], root["length"]
        unchanged = [(0, offset), (offset + length, size - offset - length)]
        outside = [range_sha(raw, start, count) for start, count in unchanged]
        trailer = work / "disk-trailer"
        with raw.open("r+b") as source, trailer.open("wb") as tail, fs.open("wb") as target:
            move_tail(source, tail, offset + length, 0)
            move_tail(source, target, offset, 0)
        before = robot_state(fs, work / "before", protected)
        dir_paths = [e["path"] for e in entries if e["kind"] == "directory"]
        existing = image.image_stats(fs, dir_paths, work)
        to_apply = [e for e in entries if e["kind"] != "directory" or e["path"] not in existing]
        if any(r["kind"] != "directory" for r in existing.values()):
            raise ValueError("overlay directory collides with a non-directory")
        print(f"Installing and reading back {len(to_apply)} entries", flush=True)
        image.apply_overlay(fs, to_apply, removals, work)
        after = robot_state(fs, work / "after", protected)
        if after != before:
            raise ValueError("protected content, configuration, units, permissions or link changed")
        if expected_files:
            paths = robot_state(fs, work / "acceptance", tuple(expected_files))
            for path, expected in expected_files.items():
                if paths[path].get("sha256") != expected:
                    raise ValueError("image acceptance checksum mismatch: " + path)
        if image.image_stats(fs, absent, work):
            raise ValueError("image contains private credentials or completed firstboot state")
        check = subprocess.run([image.tool("e2fsck"), "-fn", str(fs)], capture_output=True, text=True)
        if check.returncode:
            raise ValueError("e2fsck failed: " + (check.stdout + check.stderr)[-4000:])
        expected_root = image.digest(fs)
        with raw.open("r+b") as target, fs.open("r+b") as source, trailer.open("rb") as tail:
            if source.seek(0, os.SEEK_END) != length:
                raise ValueError("rootfs length changed")
            move_tail(source, target, 0, offset)
            target.seek(offset + length)
            image.copy_bytes(tail, target, size - offset - length)
        if image.partitions(raw) != layout or outside != [range_sha(raw, s, n) for s, n in unchanged]:
            raise ValueError("GPT or disk bytes outside rootfs changed")
        if range_sha(raw, offset, length) != expected_root:
            raise ValueError("final rootfs differs from the verified filesystem")
        # The verified rootfs now lives in raw; release its temporary copy before
        # compression so a full-image update fits on smaller build workspaces.
        fs.unlink()
        raw_hash = image.digest(raw)
        packed = work / "release.img.xz"
        print("Compressing and reading back the flash image", flush=True)
        with packed.open("wb") as target:
            subprocess.run([image.tool("xz"), "-T4", "-1", "-c", str(raw)], stdout=target, check=True)
        with subprocess.Popen([image.tool("xz"), "-T4", "-dc", str(packed)], stdout=subprocess.PIPE) as proc:
            unpacked_hash = hashlib.file_digest(proc.stdout, "sha256").hexdigest()
            if proc.wait() or unpacked_hash != raw_hash:
                raise ValueError("compressed image readback differs from verified disk")
        sha = image.digest(packed)
        manifest = {
            "format": 1, "base": {"file": base.name, "sha256": base_sha256},
            "output": {"file": output.name, "sha256": sha, "raw_sha256": raw_hash,
                       "compressed_bytes": packed.stat().st_size, "raw_bytes": size},
            "partitions": layout, "updated_partition": 3, "rootfs_sha256": expected_root,
            "unchanged_disk_ranges": [dict(offset=s, length=n, sha256=h) for (s,n),h in zip(unchanged,outside)],
            "robotd_unchanged": {path: state for path, state in before.items() if path in PROTECTED},
            "protected_unchanged": before, "removed_files": removals,
            "overlay": [{k:v for k,v in e.items() if k != "source"} for e in to_apply],
            "verification": {"gpt": "primary and backup valid", "ext4": "e2fsck -fn exit 0",
                             "files": "contents, modes, uid/gid and symlink targets verified",
                             "compressed_stream": "entire decoded SHA256 matched verified raw image",
                             "absent_paths": list(absent),
                             "new_card_boot_tested": False},
        }
        if expected_files:
            manifest["verification"]["runtime_files_sha256"] = expected_files
        if release is not None:
            manifest["release"] = release
        metadata = work / "manifest.json"
        metadata.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        checksum = work / "sha256"
        checksum.write_text(f"{sha}  {output.name}\n")
        published = []
        try:
            for src, dest in [(metadata, manifest_file), (checksum, sha_file), (packed, output)]:
                os.link(src, dest)
                published.append(dest)
        except OSError:
            for dest in published:
                dest.unlink()
            raise
        return manifest["output"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("base", "overlay", "output"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base, args.overlay, args.output), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Camera image build failed: {exc}\n")
