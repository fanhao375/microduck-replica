#!/usr/bin/env python3
"""Prepare the existing A7Z robot software for an unconfigured Orange Pi host.

This copies program bytes and generic model resources. It never connects to a
board, copies a machine calibration, enables an input daemon or touches a UART.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import tarfile
import tomllib
from zipfile import ZipFile

SOURCE_REVISION = "d1a2c62efdfb8c7a446ac99f562849e5a3a9587f"
ROBOTD_SHA256 = "1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc"
ARCHIVE_MEMBER = "Microduck-Console-R17-V167/updates/Microduck-Robotd-FT6-R17.tar.gz"
ARCHIVE_SHA256 = "bcfd5c183c697b9c4e0fdbd563d995eda6a48331a7c49b9e76d20bd156c65cce"
ARCHIVE_ROOT = "Microduck-Robotd-FT6-R17"
INSTALL_BASE = "opt/robot/feetech-ft5-r5"
UNCONFIGURED_PORT = "/dev/orangepi-robot-uart-unconfigured"
PINNED_PACKAGING = "microduck-hd1910/firmware/packaging/"

UNIT = """[Unit]
Description=Robot control daemon (Orange Pi, unconfigured read-only bus)
After=local-fs.target

[Service]
Type=exec
User=root
Group=robot
ExecStart=/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock --params /etc/robot/feetech-ft5/robotd.toml --read-only
Environment=RUST_LOG=info
# Single-request alternative: unset CUBIE_A7Z_SYNC_GROUP_SIZE (16 devices in one packet).
# On this board single-request tests had UART overruns; grouped reads restored.
Environment=CUBIE_A7Z_SYNC_GROUP_SIZE=3
Environment=ORT_DYLIB_PATH=/opt/robot/feetech-ft5-r5/onnxruntime/libonnxruntime.so.1.28.0
Restart=on-failure
RestartSec=3s
NoNewPrivileges=yes
ProtectKernelTunables=yes
RestrictSUIDSGID=yes
RuntimeDirectory=robotd
UMask=0007
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pinned_file(runtime, name):
    return subprocess.check_output(
        ["git", "-C", str(runtime), "show", SOURCE_REVISION + ":" + PINNED_PACKAGING + name]
    )


def elf_info(data):
    if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("Expected a little-endian AArch64 ELF")
    header = struct.unpack("<16sHHIQQQIHHHHHH", data[:64])
    phoff, phsize, phnum = header[5], header[9], header[10]
    segments = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * phsize) for i in range(phnum)]
    dynamic = next((entry for entry in segments if entry[0] == 2), None)
    needed = []
    if dynamic:
        entries = [struct.unpack_from("<QQ", data, offset)
                   for offset in range(dynamic[2], dynamic[2] + dynamic[5], 16)]
        strtab = next((value for tag, value in entries if tag == 5), None)
        if strtab:
            load = next(item for item in segments if item[0] == 1 and item[3] <= strtab < item[3] + item[5])
            start = load[2] + strtab - load[3]
            needed = [data[start + value:].split(b"\0", 1)[0].decode()
                      for tag, value in entries if tag == 1]
    glibc = {tuple(map(int, value.split(b"."))) for value in re.findall(rb"GLIBC_([0-9]+\.[0-9]+)", data)}
    return {"architecture": "aarch64", "needed": needed,
            "max_glibc_symbol": ".".join(map(str, max(glibc))) if glibc else None}


def put(payload, name, data, mode, source, records):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe payload path")
    path = payload.joinpath(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    record = {"path": name, "bytes": len(data), "sha256": sha256(data),
              "mode": oct(mode), "source": source}
    if data.startswith(b"\x7fELF"):
        record["elf"] = elf_info(data)
    records.append(record)


def prepare(source_control, runtime, output):
    output.mkdir(parents=True, exist_ok=False)
    payload = output / "payload"
    payload.mkdir()
    records = []
    binary = (source_control / "artifacts/robotd").read_bytes()
    if sha256(binary) != ROBOTD_SHA256:
        raise ValueError("A7Z runtime checksum changed")
    put(payload, INSTALL_BASE + "/robotd", binary, 0o755,
        str(source_control / "artifacts/robotd"), records)
    monitor = (source_control / "artifacts/bus_monitor.py").read_bytes()
    source_manifest = json.loads((source_control / "artifacts/manifest.json").read_text())
    if sha256(monitor) != source_manifest["sha256"]["bus_monitor.py"]:
        raise ValueError("Bus monitor checksum changed")
    put(payload, "usr/local/lib/orangepi-control/bus_monitor.py", monitor, 0o755,
        str(source_control / "artifacts/bus_monitor.py"), records)

    release_zip = runtime / "release-assets/Microduck-Console-R17-V167.zip"
    with ZipFile(release_zip) as zipped:
        archive = zipped.read(ARCHIVE_MEMBER)
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("Referenced runtime archive checksum changed")
    extracted = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2 or parts[0] != ARCHIVE_ROOT or ".." in parts:
                continue
            relative = PurePosixPath(*parts[1:])
            selected = (str(relative) in {"padd", "mediad", "robotctl"}
                        or relative.parts[0] in {"onnxruntime", "models", "roller"})
            if not selected or member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Selected runtime resource is not a regular file: " + member.name)
            data = tar.extractfile(member).read()
            executable = str(relative) in {"padd", "mediad", "robotctl"}
            put(payload, INSTALL_BASE + "/" + str(relative), data, 0o755 if executable else 0o644,
                str(release_zip) + "!" + ARCHIVE_MEMBER + "!" + member.name, records)
            extracted.add(str(relative))
    required = {"padd", "mediad", "robotctl", "onnxruntime/libonnxruntime.so.1.28.0",
                "onnxruntime/libonnxruntime_providers_shared.so"}
    if not required <= extracted:
        raise ValueError("Runtime dependency archive is incomplete")

    profile = pinned_file(runtime, "robotd-profile.toml").decode()
    if profile.count('port = "/dev/ttyS2"') != 1 or profile.count("enabled = true") != 1:
        raise ValueError("Pinned profile does not match the expected source")
    profile = profile.replace('port = "/dev/ttyS2"', 'port = "' + UNCONFIGURED_PORT + '"')
    profile = profile.replace("enabled = true", "enabled = false")
    profile = ("# Orange Pi installation: no UART/IMU wiring has been commissioned.\n"
               "# The nonexistent device is deliberate: report waiting_for_bus, never guess a real UART.\n"
               + profile)
    parsed = tomllib.loads(profile)
    if parsed["policy"]["enabled"] is not False or parsed["bus"]["port"] != UNCONFIGURED_PORT:
        raise ValueError("Unconfigured profile guard failed")
    for name in ("walk", "sitstand", "ground_pick", "kick_left", "kick_right", "roulade"):
        path = parsed["policy"][name].lstrip("/")
        if not (payload / path).is_file():
            raise ValueError("Missing policy resource: " + path)
    put(payload, "etc/robot/feetech-ft5/robotd.toml", profile.encode(), 0o644,
        SOURCE_REVISION + ":" + PINNED_PACKAGING + "robotd-profile.toml (port and policy adapted)", records)
    calibration = pinned_file(runtime, "hd1910-calibration.template.toml")
    cal = tomllib.loads(calibration.decode())
    if any(cal.get(key) is not False for key in ("motion_enabled", "imu_mount_verified", "units_verified")):
        raise ValueError("Template is not uncommissioned")
    if len(cal["joints"]) != 15 or any(joint["calibrated"] is not False for joint in cal["joints"]):
        raise ValueError("Template contains commissioned joints")
    put(payload, "etc/robot/feetech-ft5/hd1910-calibration.toml", calibration, 0o600,
        SOURCE_REVISION + ":" + PINNED_PACKAGING + "hd1910-calibration.template.toml", records)
    put(payload, "etc/systemd/system/robotd.service", UNIT.encode(), 0o644,
        "Orange Pi read-only unit, based on pinned robotd/systemd/robotd.service", records)
    manifest = {"format": 1, "source_revision": SOURCE_REVISION,
                "nested_archive_sha256": ARCHIVE_SHA256,
                "robotd_sha256": ROBOTD_SHA256,
                "initial_mode": "read_only", "policy_enabled": False,
                "uart": UNCONFIGURED_PORT, "group_required": "robot",
                "not_enabled": ["padd", "mediad"], "configd": "not supplied; A7Z Studio uses none",
                "files": sorted(records, key=lambda record: record["path"])}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"payload": str(payload), "files": len(records),
                      "bytes": sum(record["bytes"] for record in records),
                      "robotd_sha256": ROBOTD_SHA256}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-control", type=Path, required=True,
                        help="Source checkout containing the pinned artifacts/robotd and bus_monitor.py")
    parser.add_argument("--runtime", type=Path, required=True,
                        help="Runtime checkout containing the pinned release-assets archive and packaging revision")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "bundle")
    args = parser.parse_args()
    prepare(args.source_control.resolve(), args.runtime.resolve(), args.output.resolve())
