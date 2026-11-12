#!/usr/bin/env python3
"""Install the release payload onto a fresh, unconfigured Orange Pi Zero 3W."""

import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path, PurePosixPath

ROBOT = Path("/opt/robot/feetech-ft5-r5")
STUDIO = Path("/opt/microduck-studio")
UNCONFIGURED = "/dev/orangepi-robot-uart-unconfigured"
TOP_LEVEL = (
    "/opt/robot/feetech-ft5-r5", "/etc/robot/feetech-ft5",
    "/usr/local/lib/orangepi-control", "/opt/microduck-studio", "/opt/11quanzhi-source",
    "/etc/systemd/system/robotd.service", "/etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf",
    "/etc/systemd/system/microduck-studio.service", "/etc/sudoers.d/microduck-studio",
    "/usr/local/sbin/microduck-helper",
)


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run(args, *, check=True, capture=False, env=None):
    result = subprocess.run(args, check=False, text=True, capture_output=capture, env=env)
    if check and result.returncode:
        raise RuntimeError("Command failed: " + " ".join(map(str, args[:3]))
                           + ("\n" + result.stdout + result.stderr if capture else ""))
    return result


def verify_payload(base):
    manifest = json.loads((base / "manifest.json").read_text())
    payload = base / "payload"
    actual = {p.relative_to(payload).as_posix() for p in payload.rglob("*")
              if p.is_symlink() or p.is_file()}
    expected = {item["path"] for item in manifest["files"]}
    if actual != expected:
        raise ValueError("Payload file set differs from its manifest")
    for item in manifest["files"]:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest path")
        target = "/" + relative.as_posix()
        if not any(target == prefix or target.startswith(prefix + "/") for prefix in TOP_LEVEL):
            raise ValueError("Unexpected installation path: " + target)
        path = payload / relative
        if "symlink" in item:
            if not path.is_symlink() or os.readlink(path) != item["symlink"]:
                raise ValueError("Symlink mismatch: " + target)
            link = Path(item["symlink"])
            if link.is_absolute() or not path.resolve().is_relative_to(payload.resolve()):
                raise ValueError("Symlink escapes the payload: " + target)
        elif path.is_symlink() or sha256(path) != item["sha256"]:
            raise ValueError("Payload hash mismatch: " + target)
        elif path.stat().st_mode & 0o777 != item["mode"]:
            raise ValueError("Payload mode mismatch: " + target)
    return manifest


def ensure_dependencies():
    checks = {
        "psmisc": ["/usr/bin/fuser", "/bin/fuser"],
        "alsa-utils": ["/usr/bin/arecord", "/bin/arecord"],
        "v4l-utils": ["/usr/bin/v4l2-ctl"],
        "ffmpeg": ["/usr/bin/ffmpeg"],
    }
    packages = [package for package, paths in checks.items() if not any(Path(p).exists() for p in paths)]
    lib_packages = ("libudev1", "libstdc++6", "libgcc-s1", "libgstreamer1.0-0",
                    "libgstreamer-plugins-base1.0-0", "libgstreamer-plugins-bad1.0-0")
    for package in lib_packages:
        result = run(["dpkg-query", "-W", "-f=${Status}", package], check=False, capture=True)
        if result.returncode or result.stdout.strip() != "install ok installed":
            packages.append(package)
    if packages:
        print("Installing missing runtime packages: " + ", ".join(packages), flush=True)
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        run(["apt-get", "update", "-qq"], env=env)
        run(["apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)


def read_bus_status():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect("/run/robotd.sock")
        request = {"jsonrpc": "2.0", "id": 1, "method": "robot.busStatus", "params": {}}
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("rb") as stream:
            response = json.loads(stream.readline(1024 * 1024))
    if response.get("error") or not isinstance(response.get("result"), dict):
        raise RuntimeError("Invalid read-only robotd status")
    return response["result"]


def require_clean_state(directory):
    """Reject reused device state before installing or enabling any service."""
    directory = Path(directory)
    if directory.is_symlink() or (directory.exists() and (not directory.is_dir() or any(directory.iterdir()))):
        raise RuntimeError("Expected a clean Studio state directory: " + str(directory))


def install(base):
    if os.geteuid() != 0:
        raise PermissionError("Run the installer as root")
    release = Path("/etc/orangepi-release").read_text()
    if "BOARD=orangepizero3w\n" not in release or platform.machine() != "aarch64":
        raise RuntimeError("Target is not the inspected Orange Pi Zero 3W arm64 installation")
    print("[1/7] Verifying application payload and fresh target ...", flush=True)
    manifest = verify_payload(base)
    account = pwd.getpwnam("orangepi")
    # Published installations start with their own empty state. Credentials and
    # sessions are created only by the operator through Studio's normal setup.
    for name in ("/var/lib/microduck-studio", "/var/log/microduck-studio"):
        require_clean_state(name)
    for target in TOP_LEVEL:
        if Path(target).exists() or Path(target).is_symlink():
            raise RuntimeError("Refusing to overwrite existing application: " + target)
    if Path("/dev/orangepi-robot-uart-unconfigured").exists():
        raise RuntimeError("The unconfigured UART placeholder unexpectedly exists")
    if run(["systemctl", "is-active", "orangepi-provision.service"], capture=True).stdout.strip() != "active":
        raise RuntimeError("Existing phone provisioning service is not healthy")
    print("[2/7] Preparing runtime dependencies and service groups ...", flush=True)
    ensure_dependencies()
    try:
        grp.getgrnam("robot")
    except KeyError:
        run(["groupadd", "--system", "robot"])
    run(["usermod", "-a", "-G", "robot,dialout", "orangepi"])
    print("[3/7] Copying program, model and source trees ...", flush=True)
    payload = base / "payload"
    for target in TOP_LEVEL:
        source = payload / target.lstrip("/")
        if not source.exists():
            continue
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)
    for name in ("/var/lib/microduck-studio", "/var/log/microduck-studio"):
        directory = Path(name)
        if directory.exists() and any(directory.iterdir()):
            raise RuntimeError("Expected a clean Studio state directory: " + name)
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, account.pw_uid, account.pw_gid)
        directory.chmod(0o700)
    Path("/var/lib/robot").mkdir(parents=True, exist_ok=True)
    for name in ("/var/lib/microduck-helper", "/var/lib/microduck-helper/imu-recalibrate"):
        directory = Path(name)
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    command_link = Path("/usr/local/bin/robotctl")
    if command_link.exists() or command_link.is_symlink():
        raise RuntimeError("Refusing to replace an existing robotctl command")
    command_link.symlink_to(ROBOT / "robotctl")
    print("[4/7] Checking permissions, binaries and service definitions ...", flush=True)
    for item in manifest["files"]:
        destination = Path("/") / item["path"]
        if "sha256" in item and sha256(destination) != item["sha256"]:
            raise RuntimeError("Installed file differs: " + str(destination))
    run(["visudo", "-cf", "/etc/sudoers.d/microduck-studio"])
    run(["systemd-analyze", "verify", "/etc/systemd/system/robotd.service",
         "/etc/systemd/system/microduck-studio.service"])
    dependency_checks = {}
    for binary in (ROBOT / "robotd", ROBOT / "padd", ROBOT / "mediad", ROBOT / "robotctl",
                   STUDIO / "python/bin/python3.13"):
        result = run(["ldd", str(binary)], capture=True)
        dependency_checks[binary.name] = result.stdout
        if "not found" in result.stdout:
            raise RuntimeError("Unresolved shared library: " + str(binary) + "\n" + result.stdout)
    run([str(ROBOT / "robotd"), "--help"], capture=True)
    run([str(STUDIO / "python/bin/python3.13"), "-I", str(STUDIO / "app/run.py"), "--help"], capture=True)
    run([str(STUDIO / "python/bin/python3.13"), "-I", "-c", "import ssl,sqlite3,tomllib"])
    # Parse with the bundled Python; Jammy's system Python has no tomllib.
    guard = ('import tomllib; from pathlib import Path; '
             'c=tomllib.loads(Path("/etc/robot/feetech-ft5/robotd.toml").read_text()); '
             'a=tomllib.loads(Path("/etc/robot/feetech-ft5/hd1910-calibration.toml").read_text()); '
             f'assert c["bus"]["port"]=="{UNCONFIGURED}" and c["policy"]["enabled"] is False; '
             'assert all(a[k] is False for k in ("motion_enabled","imu_mount_verified","units_verified")); '
             'assert len(a["joints"])==15 and all(j["calibrated"] is False for j in a["joints"])')
    run([str(STUDIO / "python/bin/python3.13"), "-I", "-c", guard])
    policy_env = {**os.environ, "ORT_DYLIB_PATH": str(ROBOT / "onnxruntime/libonnxruntime.so.1.28.0")}
    policy_check = run([str(ROBOT / "robotd"), "validate-policy",
                        str(ROBOT / "models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx")],
                       capture=True, env=policy_env).stdout
    print("[5/7] Enabling read-only robotd and the real Studio backend ...", flush=True)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "robotd.service", "microduck-studio.service"])
    print("[6/7] Verifying live RPC, HTTP and startup state ...", flush=True)
    deadline = time.monotonic() + 40
    status = health = None
    last_error = None
    while time.monotonic() < deadline:
        try:
            status = read_bus_status()
            with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=3) as response:
                health = json.load(response)
            break
        except (OSError, ValueError, RuntimeError) as exc:
            last_error = str(exc)
            time.sleep(1)
    if status is None or health is None:
        raise RuntimeError("Runtime did not become reachable: " + str(last_error))
    if (status.get("mode") != "read_only" or status.get("phase") != "waiting_for_bus"
            or status.get("port") != UNCONFIGURED or status.get("attempt", 0) < 1):
        raise RuntimeError("Unexpected initial robotd mode/phase: " + json.dumps(status))
    services = {}
    for unit in ("robotd.service", "microduck-studio.service", "orangepi-provision.service"):
        services[unit] = {key: run(["systemctl", action, unit], capture=True).stdout.strip()
                          for key, action in (("active", "is-active"), ("enabled", "is-enabled"))}
    print("[7/7] Saving installation evidence ...", flush=True)
    report = {"status": "installed-and-runtime-verified", "release": manifest["release"],
              "files": len(manifest["files"]), "services": services,
              "robotd": status, "studio_health": health, "ldd": dependency_checks,
              "policy_validation": policy_check,
              "hardware_connected": False, "physical_motion_test": "not-performed",
              "archived_only": manifest["archived_only"]}
    destination = Path("/var/lib/orangepi-migration")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "installed-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (destination / "installation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "files": report["files"], "services": services,
                      "robotd_mode": status.get("mode"), "robotd_phase": status.get("phase"),
                      "url": "http://<board-address>:8000/",
                      "next_step": "Set a new administrator password in Studio's login-password page"},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    args = parser.parse_args()
    install(args.base.resolve())
