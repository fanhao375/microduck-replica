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
DROPIN = Path("/etc/systemd/system/robotd.service.d/zz-cubie-readonly.conf")

PROFILE = """[Service]
Environment=CUBIE_A7Z_SYNC_GROUP_SIZE=3
ExecStartPre=
ExecStartPre=/usr/bin/python3 /usr/local/sbin/cubie-servo-uart.py
ExecStart=
ExecStart=/opt/robot/feetech-ft5-r5/robotd --socket /run/robotd.sock --params /etc/robot/feetech-ft5/robotd.toml --read-only
Restart=no
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
    status = read_status("/run/robotd.sock")
    if status.get("mode") == "commissioning":
        if (status.get("torque_state") != "off" or status.get("motion") is not None
                or status.get("policy_motion_enabled") is not False):
            raise ValueError("existing robotd is not confirmed idle with torque off")
    elif not (status.get("mode") == "read_only" and status.get("motor_writes_blocked") is True):
        raise ValueError("refusing to replace a motion-mode or unknown daemon")
    pid = int(run("systemctl", "show", "robotd", "--property=MainPID", "--value"))
    cmdline = Path("/proc/%d/cmdline" % pid).read_bytes().split(b"\0")
    if (pid <= 1 or Path(os.fsdecode(cmdline[0])) != BASE / "robotd"
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
    if int(run("systemctl", "show", "robotd", "--property=MainPID", "--value")) != pid:
        raise ValueError("robotd changed during preparation; nothing replaced")
    # SIGTERM in the existing commissioning code writes torque-off even when idle.
    # With torque already confirmed OFF, SIGKILL avoids that prohibited shutdown write.
    # The replacement starts with the packet-level read-only guard, including on reboot.
    os.kill(pid, signal.SIGKILL)
    for _ in range(100):
        if not Path("/proc/%d" % pid).exists():
            break
        time.sleep(0.05)
    else:
        raise ValueError("old process did not exit; no replacement started")
    # If installation fails from here, leave it stopped: never fall back to a mode that writes.
    replace_file(bundle / "robotd", BASE / "robotd")
    replace_file(bundle / "cubie-servo-uart.py", UART)
    CONFIG.write_text(policy)
    CALIBRATION.write_text(calibration)
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(PROFILE)
    library = Path("/usr/local/lib/cubie-control")
    library.mkdir(parents=True, exist_ok=True)
    replace_file(bundle / "bus_monitor.py", library / "bus_monitor.py")
    run("systemctl", "daemon-reload")
    run("systemctl", "reset-failed", "robotd.service")
    run("systemctl", "start", "robotd.service")
    print("started read-only; calibration/policy flags cleared; no HOME or strategy requested", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    install(parser.parse_args().bundle.resolve())
