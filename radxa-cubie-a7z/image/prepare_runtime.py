#!/usr/bin/env python3
"""Stage a verified A7Z runtime filesystem overlay for a newly assembled duck.

This only writes a new local directory. It never opens a serial device, executes
the foreign binaries, or changes a running board. The output retains the models
but deliberately carries no previous robot's calibration authorization.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import tarfile
import tempfile
import tomllib


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
DEFAULT_ARCHIVE = (PROJECT.parent / "10fanhao_duck/runtime/microduck-hd1910/firmware"
                   "/console/updates/Microduck-Robotd-FT6-R17.tar.gz")
DEFAULT_ROBOTD = HERE.parent / "control/artifacts/robotd"
ARCHIVE_SHA256 = "bcfd5c183c697b9c4e0fdbd563d995eda6a48331a7c49b9e76d20bd156c65cce"
ARCHIVE_PREFIX = "Microduck-Robotd-FT6-R17"
ROBOTD_SHA256 = "1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc"
RUNTIME = Path("opt/robot/feetech-ft5-r5")
CONFIG = Path("etc/robot/feetech-ft5")
COMMAND = ("/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock "
           "--params /etc/robot/feetech-ft5/robotd.toml")
CAPABILITIES = {
    "walk": "1cbf8730eadd75a5704332c9f3c10f60",
    "sit_stand": "1c94934c6421ec72ed48079cea749668",
    "ground_pick": "b14e3a8b56ddbb8d2d831e727774a168",
    "roulade": "59e15d7b04d0c40e9b11b8a70bdecae6",
    "kick_left": "ee0376c927044cdece056ba1d3e6eac7",
    "kick_right": "67ad09d82f4865d222a7ae06f250cf9f",
}
UNIT = f"""[Unit]
Description=Microduck native control on Cubie A7Z
After=local-fs.target cubie-duck-firstboot.service
Wants=cubie-duck-firstboot.service
ConditionPathExists=/var/lib/cubie-duck/firstboot.done

[Service]
Type=exec
User=root
Group=robot
SupplementaryGroups=audio
Environment=CUBIE_A7Z_SYNC_GROUP_SIZE=3
Environment=ORT_DYLIB_PATH=/opt/robot/feetech-ft5-r5/onnxruntime/libonnxruntime.so.1.28.0
Environment=RUST_LOG=info
ExecStartPre=/usr/bin/python3 /usr/local/sbin/cubie-servo-uart.py
ExecStart={COMMAND}
Restart=on-failure
RestartSec=3
RuntimeDirectory=robotd
NoNewPrivileges=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
"""
COMMISSIONING = ("# New hardware stays in calibration until Studio commits verified settings.\n"
                 "[Service]\nExecStart=\nExecStart=" + COMMAND + " --commissioning\n")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_source(path, expected):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode) or sha256(path) != expected:
        raise ValueError("source type or SHA256 mismatch: " + str(path))
    return path


def member_path(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or path.parts[0] != ARCHIVE_PREFIX or "\\" in name):
        raise ValueError("unsafe archive path: " + name)
    return PurePosixPath(*path.parts[1:])


def unpack_verified(archive, output):
    """Reject links/special files and check the complete inventory before writing."""
    with tarfile.open(archive, "r:gz") as source:
        files = {}
        for member in source.getmembers():
            path = member_path(member.name)
            if member.isdir():
                continue
            if not member.isfile() or not path.parts:
                raise ValueError("archive contains a link or special file: " + member.name)
            key = path.as_posix()
            if key in files:
                raise ValueError("duplicate archive file: " + key)
            files[key] = member
        if "SHA256SUMS" not in files:
            raise ValueError("archive has no SHA256SUMS")
        expected = {}
        for line in source.extractfile(files["SHA256SUMS"]).read().decode("utf-8").splitlines():
            digest, separator, name = line.partition("  ")
            if (not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                    or name in expected or name not in files):
                raise ValueError("invalid SHA256SUMS record")
            expected[name] = digest
        if set(files) != set(expected) | {"SHA256SUMS"}:
            raise ValueError("archive inventory differs from SHA256SUMS")
        for name, member in files.items():
            content = source.extractfile(member).read()
            if name in expected and hashlib.sha256(content).hexdigest() != expected[name]:
                raise ValueError("archive member SHA256 mismatch: " + name)
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def require_aarch64(path):
    header = Path(path).read_bytes()[:64]
    if (len(header) < 64 or header[:6] != b"\x7fELF\x02\x01"
            or struct.unpack_from("<H", header, 18)[0] != 183):
        raise ValueError("expected a little-endian AArch64 ELF: " + str(path))


def write(root, name, text, mode=0o644):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def configure_new_unit(root):
    runtime = root / RUNTIME
    profile = (runtime / "robotd-profile.toml").read_text(encoding="utf-8")
    settings = tomllib.loads(profile)
    if settings["policy"]["enabled"] is not True:
        raise ValueError("expected model-loading policy profile")
    for slot, ident in CAPABILITIES.items():
        policy_slot = "sitstand" if slot == "sit_stand" else slot
        expected_path = "/" + str(RUNTIME / "models" / ident / "policy.onnx")
        if settings["policy"].get(policy_slot) != expected_path:
            raise ValueError("unexpected model slot: " + slot)
        model = runtime / "models" / ident / "policy.onnx"
        if not model.is_file():
            raise ValueError("model missing: " + ident)
    if "audio" in settings:
        raise ValueError("audio defaults changed; review the source profile")
    profile += ("\n# The board's ALSA default is selected separately from Zero 3W HAT codecs.\n"
                "[audio]\nenabled = true\ndevice = \"default\"\n"
                "bank = \"/var/lib/robot/sounds\"\ngreet = false\npet_detect = false\n")
    write(root, CONFIG / "robotd.toml", profile)
    write(root, CONFIG / "robotd.saved.toml", profile)
    # Studio's speaker test reads this legacy path; keep one settings authority.
    (root / "etc/robot/robotd.toml").symlink_to("feetech-ft5/robotd.toml")
    calibration = (runtime / "hd1910-calibration.template.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(calibration)
    if (parsed["motion_enabled"] or parsed["imu_mount_verified"] or parsed["units_verified"]
            or len(parsed["joints"]) != 15 or any(j["calibrated"] for j in parsed["joints"])):
        raise ValueError("source calibration template is not uncommissioned")
    for name in ("hd1910-calibration.toml", "hd1910-calibration.saved.toml"):
        write(root, CONFIG / name, calibration)
    # The legacy installer can restore these fallback snapshots. A reusable image
    # must never re-authorize another assembly through that recovery path.
    for name in ("hd1910-calibration.robot.toml", "hd1910-calibration.training.toml"):
        write(root, RUNTIME / name, calibration)
    write(root, RUNTIME / "robot-calibration-source.json", json.dumps({
        "source": "new-unit-uncommissioned-template", "motion_enabled": False,
        "note": "No previous assembly calibration or IMU authorization is included."
    }, indent=2) + "\n")
    write(root, "etc/systemd/system/robotd.service", UNIT)
    write(root, "etc/systemd/system/robotd.service.d/zzzz-zz-microduck-commissioning.conf",
          COMMISSIONING)


def stage(archive, robotd, output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; choose a new directory: " + str(output))
    archive = regular_source(archive, ARCHIVE_SHA256)
    robotd = regular_source(robotd, ROBOTD_SHA256)
    uart = HERE.parent / "control/cubie-servo-uart.py"
    build = json.loads((HERE.parent / "control/runtime-build.json").read_text())
    regular_source(uart, build["sha256"]["cubie-servo-uart.py"])
    require_aarch64(robotd)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".runtime-overlay-", dir=output.parent))
    try:
        unpack_verified(archive, temporary / RUNTIME)
        for binary in ("sounds", "robotctl", "padd"):
            require_aarch64(temporary / RUNTIME / binary)
        shutil.copyfile(robotd, temporary / RUNTIME / "robotd")
        (temporary / RUNTIME / "robotd").chmod(0o755)
        write(temporary, "usr/local/sbin/cubie-servo-uart.py", uart.read_text(), 0o755)
        configure_new_unit(temporary)
        # The archive's checksums describe its original files, not this modified
        # overlay. Retain them as provenance; publish fresh final checksums below.
        (temporary / RUNTIME / "SHA256SUMS").rename(temporary / RUNTIME / "UPSTREAM-SHA256SUMS")
        entries = {}
        for path in sorted(temporary.rglob("*")):
            if path.is_file() and not path.is_symlink():
                entries[path.relative_to(temporary).as_posix()] = sha256(path)
        manifest = {
            "schema": "cubie-duck-runtime-overlay/v1",
            "source_archive_sha256": ARCHIVE_SHA256,
            "robotd_sha256": ROBOTD_SHA256,
            "target": "aarch64-linux-gnu-glibc2.31",
            "boot_mode": "commissioning", "autostart_motion": False,
            "calibration": "new-unit-uncommissioned",
            "capabilities": CAPABILITIES,
            "audio": {"device": "default", "bank": "/var/lib/robot/sounds",
                      "firstboot_command": ["/opt/robot/feetech-ft5-r5/sounds", "ensure-bank",
                                            "--dir", "/var/lib/robot/sounds"]},
            "pending_validation": ["new-unit servo and IMU calibration", "ALSA speaker and quack",
                                   "A7Z sustained walking", "A7Z pickup and flip physical trials"],
            "files": entries,
        }
        write(temporary, "usr/share/cubie-duck/runtime-manifest.json",
              json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        os.rename(temporary, output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--robotd", type=Path, default=DEFAULT_ROBOTD)
    parser.add_argument("--output", type=Path, required=True,
                        help="new filesystem overlay directory; never overwrites an existing path")
    args = parser.parse_args()
    report = stage(args.archive, args.robotd, args.output)
    print(json.dumps({"output": str(args.output.absolute()), "files": len(report["files"]),
                      "boot_mode": report["boot_mode"], "capabilities": report["capabilities"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
