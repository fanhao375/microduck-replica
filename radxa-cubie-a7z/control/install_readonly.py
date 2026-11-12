#!/usr/bin/env python3
"""Install the A7Z read-only runtime. Never starts commissioning or motion mode."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from bus_monitor import read_status

BASE = Path("/opt/robot/feetech-ft5-r5")
CONFIG = Path("/etc/robot/feetech-ft5/robotd.toml")
CALIBRATION = CONFIG.with_name("hd1910-calibration.toml")
UART = Path("/usr/local/sbin/cubie-servo-uart.py")
DROPIN = Path("/etc/systemd/system/robotd.service.d/zzzzzzz-cubie-readonly.conf")
COMMAND = ("/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock "
           "--params /etc/robot/feetech-ft5/robotd.toml --read-only")

PROFILE = f"""[Service]
Environment=CUBIE_A7Z_SYNC_GROUP_SIZE=3
ExecStartPre=
ExecStartPre=/usr/bin/python3 /usr/local/sbin/cubie-servo-uart.py
ExecStart=
ExecStart={COMMAND}
Restart=no
"""
# Block new start jobs while the stop job and file replacement are in progress.
# Commissioning writes torque-off on normal exit, so use a hard stop after checking it is idle.
STOP_PROFILE = PROFILE + """KillSignal=SIGKILL
KillMode=control-group
ExecStop=
ExecStopPost=

[Unit]
ConditionPathExists=!/
"""


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout.strip()


def set_false(text, key):
    pattern = r'(?m)^(\s*"?' + re.escape(key) + r'"?\s*=\s*)(true|false)(\s*(?:#.*)?)$'
    updated, count = re.subn(pattern, r'\g<1>false\g<3>', text)
    if count != 1:
        raise ValueError("expected exactly one boolean " + key)
    return updated


def disable_policy(text):
    match = re.search(r"(?m)^\[policy\]\s*$", text)
    if not match:
        raise ValueError("[policy] table missing")
    following = re.search(r"(?m)^\[", text[match.end():])
    end = match.end() + following.start() if following else len(text)
    return text[:match.end()] + set_false(text[match.end():end], "enabled") + text[end:]


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".cubie-new")
    shutil.copy2(source, temp)
    temp.replace(target)


def show(property_name):
    return run("systemctl", "show", "robotd", "--property=" + property_name, "--value")


def load_profile(profile):
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    temporary = DROPIN.with_suffix(".new")
    temporary.write_text(profile)
    temporary.chmod(0o644)
    temporary.replace(DROPIN)
    run("systemctl", "daemon-reload")


def require_start_profile():
    start = show("ExecStart")
    command = start.partition("argv[]=")[2].partition(" ; ")[0]
    if start.count("argv[]=") != 1 or command != COMMAND or show("Restart") != "no":
        raise ValueError("effective robotd service does not use the expected read-only profile")


def require_idle():
    status = read_status("/run/robotd.sock")
    if status.get("mode") == "commissioning":
        if (status.get("phase") != "ready" or status.get("torque_state") != "off"
                or status.get("policy_motion_enabled") is not False
                or "motion" not in status or status["motion"] is not None
                or status.get("hold") is not None or status.get("imu_verification") is not None
                or "torque_off_pending" not in status or status["torque_off_pending"] is not None
                or status.get("power_pending") is not False):
            raise ValueError("existing robotd is not confirmed idle with torque off")
    elif not (status.get("mode") == "read_only" and status.get("motor_writes_blocked") is True):
        raise ValueError("refusing to replace a motion-mode or unknown daemon")
    return status


def require_stop_profile():
    if (show("KillSignal") != str(signal.SIGKILL.value) or show("KillMode") != "control-group"
            or show("ExecStop") or show("ExecStopPost")):
        raise ValueError("effective robotd service is missing the guarded stop profile")


def stop_robotd():
    run("systemctl", "stop", "robotd.service")
    if show("MainPID") != "0":
        raise ValueError("old robotd did not exit; no replacement started")


def verify_read_only():
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            status = read_status("/run/robotd.sock")
        except AttributeError as error:
            # The archived RPC reader assumes the decoded reply is an object.
            raise ValueError("live robotd did not confirm its read-only guard") from error
        except (OSError, ValueError):
            status = {}
        mode = status.get("mode")
        blocked = status.get("motor_writes_blocked")
        if mode == "read_only" and blocked is True:
            return
        if mode not in (None, "read_only") or blocked is False:
            break
        time.sleep(0.2)
    raise ValueError("live robotd did not confirm its read-only guard")


def install(bundle):
    if os.geteuid() != 0:
        raise ValueError("run with sudo on the A7Z")
    if b"radxa,cubie-a7z\0" not in Path("/sys/firmware/devicetree/base/compatible").read_bytes():
        raise ValueError("expected Cubie A7Z")
    manifest = json.loads((bundle / "manifest.json").read_text())
    for name in ("robotd", "cubie-servo-uart.py", "bus_monitor.py"):
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != manifest["sha256"][name]:
            raise ValueError("bundle checksum mismatch: " + name)
    binary = (bundle / "robotd").read_bytes()
    if b"CUBIE_A7Z_SYNC_GROUP_SIZE" not in binary or b"a7z-sync3.1-readonly" not in binary:
        raise ValueError("bundle does not contain the verified A7Z/IMU runtime")
    run("/usr/bin/python3", str(bundle / "cubie-servo-uart.py"), "--check")
    run(str(bundle / "robotd"), "--help")  # loader/glibc check; no UART is opened
    status = require_idle()
    pid = int(show("MainPID"))
    if pid <= 1:
        raise ValueError("no robotd main process; nothing replaced")
    cmdline = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
    if (Path(os.fsdecode(cmdline[0])) != BASE / "robotd"
            or not ({b"--commissioning", b"--read-only"} & set(cmdline))):
        raise ValueError("unexpected robotd process; nothing replaced")
    policy = disable_policy(CONFIG.read_text())
    calibration = set_false(set_false(CALIBRATION.read_text(), "motion_enabled"), "imu_mount_verified")
    backup = Path("/var/lib/robot") / time.strftime("cubie-before-sync3-%Y%m%d-%H%M%S")
    backup.mkdir(mode=0o700)
    targets = [BASE / "robotd", CONFIG, CALIBRATION, UART, DROPIN]
    for target in targets:
        if target.exists():
            dest = backup / str(target).lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest)
    (backup / "bus-status-before.json").write_text(json.dumps(status, indent=2))
    (backup / "unit-before.txt").write_text(run("systemctl", "cat", "robotd"))
    print("backup=" + str(backup), flush=True)
    if int(show("MainPID")) != pid:
        raise ValueError("robotd changed during preparation; nothing replaced")
    try:
        load_profile(STOP_PROFILE)
        require_start_profile()
        require_stop_profile()
        require_idle()
        if int(show("MainPID")) != pid:
            raise ValueError("robotd changed before stop; nothing replaced")
    except (OSError, ValueError, subprocess.SubprocessError):
        previous_profile = backup / str(DROPIN).lstrip("/")
        if previous_profile.is_file():
            replace_file(previous_profile, DROPIN)
        else:
            DROPIN.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        raise
    stop_robotd()
    # If installation fails from here, leave it stopped: never fall back to a mode that writes.
    replace_file(bundle / "robotd", BASE / "robotd")
    replace_file(bundle / "cubie-servo-uart.py", UART)
    CONFIG.write_text(policy)
    CALIBRATION.write_text(calibration)
    library = Path("/usr/local/lib/cubie-control")
    library.mkdir(parents=True, exist_ok=True)
    replace_file(bundle / "bus_monitor.py", library / "bus_monitor.py")
    try:
        load_profile(PROFILE)
        require_start_profile()
        run("systemctl", "reset-failed", "robotd.service")
        run("systemctl", "start", "robotd.service")
        if int(show("MainPID")) <= 1:
            raise ValueError("no robotd main process after start")
        verify_read_only()
    except (OSError, ValueError, subprocess.SubprocessError):
        load_profile(STOP_PROFILE)
        require_stop_profile()
        stop_robotd()
        raise
    print("started read-only; calibration/policy flags cleared; no HOME or strategy requested", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    install(parser.parse_args().bundle.resolve())
