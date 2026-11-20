#!/usr/bin/env python3
"""Check the actual applications as orangepi, without sudoers or system services."""

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path, PurePosixPath


def preview(archive, expected):
    if os.geteuid() == 0:
        raise RuntimeError("This compatibility preview must run without root")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("Archive checksum mismatch")
    root = Path.home() / "quanzhi-preview-20261109"
    root.mkdir(exist_ok=False)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "orangepi-quanzhi":
                raise ValueError("Unexpected archive path")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("Unexpected archive entry type")
            if member.issym():
                link = (root / path.parent / member.linkname).resolve()
                if Path(member.linkname).is_absolute() or not link.is_relative_to(root):
                    raise ValueError("Unexpected archive link")
        tar.extractall(root, members=members)
    base = root / "orangepi-quanzhi"
    spec = importlib.util.spec_from_file_location("migration_install", base / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manifest = installer.verify_payload(base)
    payload = base / "payload"
    robot = payload / "opt/robot/feetech-ft5-r5"
    studio = payload / "opt/microduck-studio"
    print("Application hashes verified; checking ARM64 runtime ...", flush=True)
    ldd = {}
    for executable in (robot / "robotd", robot / "padd", robot / "mediad", robot / "robotctl",
                       studio / "python/bin/python3.13"):
        done = subprocess.run(["ldd", str(executable)], capture_output=True, text=True)
        ldd[executable.name] = {"code": done.returncode, "output": done.stdout + done.stderr}
    subprocess.run([str(studio / "python/bin/python3.13"), "-I", "-c", "import ssl,sqlite3,tomllib"], check=True)
    subprocess.run([str(robot / "robotd"), "--help"], stdout=subprocess.DEVNULL, check=True)
    env = {**os.environ, "ORT_DYLIB_PATH": str(robot / "onnxruntime/libonnxruntime.so.1.28.0")}
    policy = subprocess.run([str(robot / "robotd"), "validate-policy",
                             str(robot / "models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx")],
                            env=env, capture_output=True, text=True)
    if policy.returncode:
        raise RuntimeError("Model validation failed: " + policy.stdout + policy.stderr)
    profile = (payload / "etc/robot/feetech-ft5/robotd.toml").read_text()
    profile = profile.replace("/opt/robot/feetech-ft5-r5", str(robot)).replace(
        "/etc/robot/feetech-ft5/hd1910-calibration.toml",
        str(payload / "etc/robot/feetech-ft5/hd1910-calibration.toml"))
    params = root / "preview-robotd.toml"
    params.write_text(profile)
    rpc = root / "robotd.sock"
    log = (root / "process.log").open("w")
    control = subprocess.Popen([str(robot / "robotd"), "--socket", str(rpc), "--params", str(params),
                                "--read-only"], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    ui = subprocess.Popen([str(studio / "python/bin/python3.13"), "-I", str(studio / "app/run.py"),
                           "--listen", "0.0.0.0:8000", "--robotd", "unix:" + str(rpc), "--configd", "none",
                           "--tof", "none", "--camera-socket", "none", "--servo-port",
                           "/dev/orangepi-robot-uart-unconfigured", "--state-dir", str(root / "state"),
                           "--log-dir", str(root / "logs")],
                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (root / "processes.json").write_text(json.dumps({"robotd": control.pid, "studio": ui.pid}))
    health = None
    for _ in range(40):
        if control.poll() is not None or ui.poll() is not None:
            raise RuntimeError("Preview process exited; see " + str(root / "process.log"))
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2) as response:
                health = json.load(response)
            break
        except OSError:
            time.sleep(1)
    if health is None:
        raise RuntimeError("Preview HTTP did not start")
    report = {"user": "orangepi", "root_permissions_granted": False, "services_installed": False,
              "files": len(manifest["files"]), "health": health, "ldd": ldd,
              "model_validation": policy.stdout, "pids": {"robotd": control.pid, "studio": ui.pid},
              "root": str(root), "url": "http://<board-address>:8000/"}
    (root / "preview-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    import sys
    preview(Path(sys.argv[1]), sys.argv[2])
