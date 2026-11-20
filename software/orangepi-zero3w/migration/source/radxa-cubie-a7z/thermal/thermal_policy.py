#!/usr/bin/env python3
"""Release the A7Z's false idle cap without disabling thermal protection."""

import argparse
import json
import os
from pathlib import Path
import platform
import time


KERNEL = "5.15.147-21-a733"
ZONES = {"cpul_thermal_zone": 0, "cpub_thermal_zone": 6}
THERMAL = Path("/sys/class/thermal")
CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
BACKUP = Path("/var/lib/cubie-thermal-policy/original.json")
EXPECTED_TRIPS = [
    {"type": "active", "temp": 60000, "hyst": 2000},
    {"type": "passive", "temp": 60000, "hyst": 2000},
    {"type": "critical", "temp": 110000, "hyst": 0},
]


def read(path):
    return path.read_text().strip()


def check_board():
    compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    if b"radxa,cubie-a7z" not in compatible or platform.release() != KERNEL:
        raise RuntimeError("this workaround is only for Cubie A7Z / " + KERNEL)


def inspect(thermal=THERMAL):
    """Identify zones by type and reject a changed protection configuration."""
    zones = {}
    for path in thermal.glob("thermal_zone*"):
        name = read(path / "type")
        if name not in ZONES:
            continue
        if name in zones:
            raise RuntimeError("duplicate thermal zone: " + name)
        trips = [
            {"type": read(path / f"trip_point_{i}_type"),
             "temp": int(read(path / f"trip_point_{i}_temp")),
             "hyst": int(read(path / f"trip_point_{i}_hyst"))}
            for i in range(3)
        ]
        if trips != EXPECTED_TRIPS or read(path / "mode") != "enabled":
            raise RuntimeError("unexpected thermal protection: " + name)
        if len(list(path.glob("trip_point_*_type"))) != 3:
            raise RuntimeError("unexpected number of thermal trips: " + name)
        available = read(path / "available_policies").split()
        current = read(path / "policy")
        if "step_wise" not in available or current not in ("power_allocator", "step_wise"):
            raise RuntimeError("unexpected thermal governor: " + name)
        cpu = ZONES[name]
        cooling = [p for p in path.glob("cdev*") if p.is_dir()
                   and read(p / "type") == f"cpufreq-cpu{cpu}"]
        if len(cooling) != 1:
            raise RuntimeError("unexpected CPU cooling binding: " + name)
        zones[name] = {"path": path, "cooling": cooling[0], "policy": current}
    if zones.keys() != ZONES.keys():
        raise RuntimeError("both CPU thermal zones must be present")
    return zones


def set_policies(zones, policies):
    """Restore all previous policies if a write or read-back fails partway."""
    before = {name: read(zone["path"] / "policy") for name, zone in zones.items()}
    try:
        for name, zone in zones.items():
            path = zone["path"] / "policy"
            if read(path) != policies[name]:
                path.write_text(policies[name] + "\n")
            if read(path) != policies[name]:
                raise RuntimeError("governor read-back mismatch: " + name)
    except Exception as error:
        failures = []
        for name, zone in zones.items():
            try:
                path = zone["path"] / "policy"
                if read(path) != before[name]:
                    path.write_text(before[name] + "\n")
            except OSError as rollback_error:
                failures.append(f"{name}: {rollback_error}")
        if failures:
            raise RuntimeError("policy rollback failed: " + "; ".join(failures)) from error
        raise


def saved_policies(backup):
    data = json.loads(backup.read_text())
    if data.get("kernel") != KERNEL or set(data.get("policies", {})) != set(ZONES):
        raise RuntimeError("unexpected original-policy backup")
    policies = data["policies"]
    if any(value not in ("power_allocator", "step_wise") for value in policies.values()):
        raise RuntimeError("invalid governor in original-policy backup")
    return policies


def save_original(zones, backup=BACKUP):
    # Keep the first pre-install state across service restarts and repeat applies.
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        data = {"kernel": KERNEL, "policies": {n: z["policy"] for n, z in zones.items()}}
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return saved_policies(backup)


def wait_for_release(zones, cpufreq=CPUFREQ, timeout=5):
    """Allow normal kernel polling to release old targets; never force cdev state."""
    deadline = time.monotonic() + timeout
    while True:
        pending = []
        for name, zone in zones.items():
            if int(read(zone["path"] / "temp")) >= 58000:
                continue  # At/near the trip, retain the governor's cooling decision.
            policy = cpufreq / f"policy{ZONES[name]}"
            if (int(read(zone["cooling"] / "cur_state")) != 0
                    or read(policy / "scaling_max_freq") != read(policy / "cpuinfo_max_freq")):
                pending.append(name)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("low-temperature cap was not released: " + ", ".join(pending))
        time.sleep(0.1)


def apply(thermal=THERMAL, cpufreq=CPUFREQ, backup=BACKUP):
    zones = inspect(thermal)
    save_original(zones, backup)
    previous = {n: z["policy"] for n, z in zones.items()}
    set_policies(zones, dict.fromkeys(ZONES, "step_wise"))
    try:
        inspect(thermal)
        wait_for_release(zones, cpufreq)
    except Exception:
        set_policies(zones, previous)
        raise
    return {n: read(z["path"] / "policy") for n, z in zones.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    check_board()
    if args.check:
        result = {n: z["policy"] for n, z in inspect().items()}
    else:
        if os.geteuid() != 0:
            raise RuntimeError("apply/restore requires root")
        if args.restore:
            zones = inspect()
            set_policies(zones, saved_policies(BACKUP))
            result = {n: read(z["path"] / "policy") for n, z in zones.items()}
        else:
            result = apply()
    print(json.dumps({"action": vars(args), "policies": result}, sort_keys=True))


if __name__ == "__main__":
    main()
