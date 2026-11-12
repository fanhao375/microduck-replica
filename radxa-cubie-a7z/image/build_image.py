#!/usr/bin/env python3
"""Overlay a GPT/ext4 image without mounting it or touching a block device."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib

SECTOR = 512
CHUNK = 4 * 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            value.update(chunk)
    return value.hexdigest()


def tool(name):
    found = shutil.which(name)
    if not found:
        candidate = Path("/opt/homebrew/opt/e2fsprogs/sbin") / name
        if candidate.is_file():
            found = str(candidate)
    if not found:
        raise ValueError("required tool missing: " + name)
    return found


def copy_bytes(source, destination, length):
    while length:
        chunk = source.read(min(length, CHUNK))
        if not chunk:
            raise ValueError("unexpected end of image")
        destination.write(chunk)
        length -= len(chunk)


def partitions(path):
    """Validate GPT checksums before using any on-disk offset."""
    size = path.stat().st_size
    if size % SECTOR:
        raise ValueError("image length is not sector aligned")
    with path.open("rb") as image:
        if image.read(SECTOR)[510:] != b"\x55\xaa":
            raise ValueError("invalid protective MBR")
        header = image.read(SECTOR)
        if header[:8] != b"EFI PART":
            raise ValueError("a GPT image is required")
        header_size, stored_crc = struct.unpack_from("<II", header, 12)
        if not 92 <= header_size <= SECTOR:
            raise ValueError("invalid GPT header size")
        checked = bytearray(header[:header_size])
        checked[16:20] = bytes(4)
        if zlib.crc32(checked) != stored_crc:
            raise ValueError("GPT header checksum mismatch")
        current_lba, backup_lba, first_usable, last_usable = struct.unpack_from("<QQQQ", header, 24)
        if current_lba != 1 or backup_lba != size // SECTOR - 1:
            raise ValueError("GPT header locations do not match the image size")
        entries_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
        if not 1 <= count <= 4096 or not 128 <= entry_size <= 4096:
            raise ValueError("invalid GPT table dimensions")
        image.seek(entries_lba * SECTOR)
        table = image.read(count * entry_size)
        if len(table) != count * entry_size or zlib.crc32(table) != table_crc:
            raise ValueError("GPT partition table checksum mismatch")
        image.seek(backup_lba * SECTOR)
        backup = image.read(SECTOR)
        backup_size, backup_crc = struct.unpack_from("<II", backup, 12)
        checked_backup = bytearray(backup[:backup_size])
        checked_backup[16:20] = bytes(4)
        if (backup[:8] != b"EFI PART" or backup_size != header_size
                or zlib.crc32(checked_backup) != backup_crc
                or struct.unpack_from("<QQ", backup, 24) != (backup_lba, 1)
                or backup[40:72] != header[40:72]):
            raise ValueError("backup GPT header mismatch")
        backup_entries, backup_count, backup_entry_size, backup_table_crc = struct.unpack_from("<QIII", backup, 72)
        image.seek(backup_entries * SECTOR)
        if ((backup_count, backup_entry_size, backup_table_crc) != (count, entry_size, table_crc)
                or image.read(count * entry_size) != table):
            raise ValueError("backup GPT partition table mismatch")
        result = []
        for index in range(count):
            entry = table[index * entry_size:(index + 1) * entry_size]
            if entry[:16] == bytes(16):
                continue
            start, end = struct.unpack_from("<QQ", entry, 32)
            if start < first_usable or end < start or end > last_usable:
                raise ValueError("partition outside image")
            image.seek(start * SECTOR + 1024)
            superblock = image.read(1024)
            is_ext4 = len(superblock) == 1024 and superblock[56:58] == b"\x53\xef"
            result.append({"number": index + 1, "offset": start * SECTOR,
                           "length": (end - start + 1) * SECTOR,
                           "name": entry[56:128].decode("utf-16-le").rstrip("\0"),
                           "ext4": is_ext4,
                           "label": superblock[120:136].rstrip(b"\0").decode("utf-8", "replace")
                           if is_ext4 else ""})
    ordered = sorted(result, key=lambda item: item["offset"])
    if any(left["offset"] + left["length"] > right["offset"]
           for left, right in zip(ordered, ordered[1:])):
        raise ValueError("overlapping GPT partitions")
    return result


def root_partition(items, number=None):
    candidates = [item for item in items if item["ext4"] and
                  (number is None or item["number"] == number)]
    if number is not None:
        if len(candidates) != 1:
            raise ValueError("requested partition is not ext4")
        return candidates[0]
    named = [item for item in candidates if item["label"].lower() in ("rootfs", "root")
             or item["name"].lower() in ("rootfs", "root")]
    if len(named) == 1:
        return named[0]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("root filesystem is ambiguous; specify --partition")


def quote(value):
    # debugfs has its own command parser, not a shell. Reject command delimiters.
    if any(ch in str(value) for ch in '\r\n\x00"\\'):
        raise ValueError("unsupported debugfs path or link target: " + repr(str(value)))
    return '"' + str(value) + '"'


def absolute_path(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) == "/":
        raise ValueError("expected a non-root absolute image path: " + value)
    quote(str(path))
    return str(path)


def overlay_entries(root):
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            source = Path(directory) / name
            info = source.lstat()
            path = "/" + source.relative_to(root).as_posix()
            absolute_path(path)
            item = {"path": path, "mode": stat.S_IMODE(info.st_mode), "uid": 0, "gid": 0, "source": source}
            if stat.S_ISLNK(info.st_mode):
                item.update(kind="symlink", target=os.readlink(source))
                quote(item["target"])
            elif stat.S_ISDIR(info.st_mode):
                item["kind"] = "directory"
            elif stat.S_ISREG(info.st_mode):
                item.update(kind="file", sha256=digest(source), size=info.st_size)
            else:
                raise ValueError("overlay contains a special file: " + path)
            result.append(item)
    return sorted(result, key=lambda item: (item["path"].count("/"), item["path"]))


def debugfs(rootfs, commands, work, *, write=False):
    batch = work / "debugfs.commands"
    batch.write_text("\n".join(commands) + "\n")
    args = [tool("debugfs")]
    if write:
        args.append("-w")
    done = subprocess.run(args + ["-f", str(batch), str(rootfs)], capture_output=True, text=True)
    if done.returncode:
        raise ValueError("debugfs failed: " + done.stderr[-4000:])
    return done.stdout, done.stderr


def image_stats(rootfs, paths, work):
    stdout, _stderr = debugfs(rootfs, ["stat " + quote(path) for path in paths], work)
    records = {}
    for section in re.split(r"(?m)^debugfs: +", stdout):
        if not section.startswith("stat "):
            continue
        command, _, body = section.partition("\n")
        path = command[5:].strip().strip('"')
        found = re.search(r"Type: +(\w+) +Mode: +0*([0-7]+)", body)
        if found:
            user = re.search(r"User: +(\d+) +Group: +(\d+)", body)
            records[path] = {"kind": found[1], "mode": int(found[2], 8), "body": body,
                             "uid": int(user[1]) if user else None,
                             "gid": int(user[2]) if user else None}
    return records


def apply_overlay(rootfs, entries, removals, work):
    entries = [item.copy() for item in entries]
    # Check every ancestor first: debugfs otherwise follows existing symlink parents.
    ancestors = {str(parent) for item in entries for parent in PurePosixPath(item["path"]).parents
                 if str(parent) != "/"}
    ancestors.update(str(parent) for path in removals for parent in PurePosixPath(path).parents
                     if str(parent) != "/")
    queried = sorted(ancestors | {item["path"] for item in entries} | set(removals))
    before = image_stats(rootfs, queried, work)
    for path in ancestors:
        if path in before and before[path]["kind"] != "directory":
            raise ValueError("image ancestor is not a directory: " + path)
    commands = []
    for path in removals:
        if path in before:
            if before[path]["kind"] == "directory":
                raise ValueError("refusing directory removal: " + path)
            commands.append("rm " + quote(path))
    for item in entries:
        path = item["path"]
        existing = before.get(path)
        if item["kind"] == "directory":
            if existing and existing["kind"] != "directory":
                raise ValueError("directory conflicts with existing file: " + path)
            if existing:
                # A staging parent adds children to this directory, not metadata.
                item.update(mode=existing["mode"], uid=existing["uid"], gid=existing["gid"])
                continue
            commands.append("mkdir " + quote(path))
            mode = stat.S_IFDIR | item["mode"]
        else:
            if existing and path not in removals:
                if existing["kind"] == "directory":
                    raise ValueError("file conflicts with existing directory: " + path)
                commands.append("rm " + quote(path))
            if item["kind"] == "file":
                commands.append("write " + quote(item["source"]) + " " + quote(path))
                mode = stat.S_IFREG | item["mode"]
            else:
                commands.append("symlink " + quote(path) + " " + quote(item["target"]))
                mode = stat.S_IFLNK | item["mode"]
        for field, value in (("mode", "0" + format(mode, "o")), ("uid", "0"), ("gid", "0")):
            commands.append("set_inode_field " + quote(path) + " " + field + " " + value)
    _stdout, stderr = debugfs(rootfs, commands, work, write=True)
    # debugfs often exits zero even when an individual command failed.
    errors = [line for line in stderr.splitlines() if line and not line.startswith("debugfs ")]
    if errors:
        raise ValueError("debugfs overlay command failed: " + "\n".join(errors)[-4000:])
    verify_overlay(rootfs, entries, removals, work)
    return entries


def verify_overlay(rootfs, entries, removals, work):
    records = image_stats(rootfs, [item["path"] for item in entries] + removals, work)
    added = {item["path"] for item in entries}
    for path in removals:
        if path not in added and path in records:
            raise ValueError("image removal verification failed: " + path)
    dumps = work / "readback"
    dumps.mkdir()
    commands, checks = [], []
    for index, item in enumerate(entries):
        found = records.get(item["path"])
        expected_kind = "regular" if item["kind"] == "file" else item["kind"]
        if not found or (found["kind"], found["mode"], found["uid"], found["gid"]) != (
                expected_kind, item["mode"], item["uid"], item["gid"]):
            raise ValueError("image type/mode/owner verification failed: " + item["path"])
        if item["kind"] == "file":
            destination = dumps / str(index)
            commands.append("dump " + quote(item["path"]) + " " + quote(destination))
            checks.append((item, destination))
        elif item["kind"] == "symlink":
            target = re.search(r'Fast link dest: "(.*)"', found["body"])
            if not target or target[1] != item["target"]:
                # Long symlinks use data blocks; dump returns their link target bytes.
                destination = dumps / str(index)
                commands.append("dump " + quote(item["path"]) + " " + quote(destination))
                checks.append((item, destination))
    if commands:
        debugfs(rootfs, commands, work)
    for item, destination in checks:
        if item["kind"] == "file":
            valid = destination.is_file() and digest(destination) == item["sha256"]
        else:
            valid = destination.is_file() and destination.read_bytes() == item["target"].encode()
        if not valid:
            raise ValueError("image content readback failed: " + item["path"])


def build(base, overlay, output, *, number=None, removals=()):
    base, overlay, output = Path(base).resolve(), Path(overlay).resolve(), Path(output).resolve()
    if not base.is_file() or not overlay.is_dir():
        raise ValueError("base image and overlay directory must exist")
    if not output.name.endswith(".img.xz"):
        raise ValueError("output must end in .img.xz")
    destinations = [output, Path(str(output) + ".sha256"), Path(str(output) + ".manifest.json")]
    if any(path.exists() for path in destinations):
        raise ValueError("refusing to replace an existing image or sidecar")
    removals = sorted({absolute_path(path) for path in removals})
    entries = overlay_entries(overlay)
    if not entries:
        raise ValueError("overlay is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cubie-image-", dir=output.parent) as temporary:
        work = Path(temporary)
        raw, rootfs = work / "disk.img", work / "rootfs.ext4"
        print("Preparing base image", flush=True)
        with raw.open("wb") as target:
            if base.suffix == ".xz":
                subprocess.run([tool("xz"), "-dc", str(base)], stdout=target, check=True)
            else:
                with base.open("rb") as source:
                    shutil.copyfileobj(source, target, CHUNK)
        original_partitions = partitions(raw)
        partition = root_partition(original_partitions, number)
        with raw.open("rb") as source, rootfs.open("wb") as target:
            source.seek(partition["offset"])
            copy_bytes(source, target, partition["length"])
        print("Applying and verifying %d overlay entries" % len(entries), flush=True)
        entries = apply_overlay(rootfs, entries, removals, work)
        check = subprocess.run([tool("e2fsck"), "-fn", str(rootfs)], capture_output=True, text=True)
        if check.returncode:
            raise ValueError("root filesystem check failed: " + (check.stdout + check.stderr)[-4000:])
        with raw.open("r+b") as target, rootfs.open("rb") as source:
            target.seek(partition["offset"])
            copy_bytes(source, target, partition["length"])
        if partitions(raw) != original_partitions:
            raise ValueError("partition table changed")
        packed = work / output.name
        print("Compressing verified image", flush=True)
        with packed.open("wb") as target:
            subprocess.run([tool("xz"), "-T2", "-6", "-c", str(raw)], stdout=target, check=True)
        subprocess.run([tool("xz"), "-t", str(packed)], check=True)
        checksum = digest(packed)
        manifest = {"format": 1, "base": {"file": base.name, "sha256": digest(base)},
                    "output": {"file": output.name, "sha256": checksum,
                               "compressed_bytes": packed.stat().st_size, "raw_bytes": raw.stat().st_size},
                    "partition": partition, "removed_files": removals,
                    "overlay": [{key: value for key, value in item.items() if key != "source"}
                                for item in entries], "filesystem_check": "e2fsck -fn: exit 0",
                    "tools": {name: tool(name) for name in ("debugfs", "e2fsck", "xz")}}
        # Publish only after filesystem, readback and compressed-stream checks pass.
        packed.replace(output)
        destinations[1].write_text(checksum + "  " + output.name + "\n")
        destinations[2].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partition", type=int)
    parser.add_argument("--remove", action="append", default=[])
    parser.add_argument("--remove-file", action="append", type=Path, default=[])
    args = parser.parse_args()
    removals = list(args.remove)
    for path in args.remove_file:
        removals.extend(line.strip() for line in path.read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith("#"))
    try:
        manifest = build(args.base, args.overlay, args.output, number=args.partition, removals=removals)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, "Image build failed: " + str(error) + "\n")
    print(json.dumps(manifest["output"], indent=2))


if __name__ == "__main__":
    main()
