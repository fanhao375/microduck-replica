#!/usr/bin/env python3
"""Install a staged provisioning payload into an unmounted ext4 filesystem.

Uses e2fsprogs directly because macOS cannot mount the board's Linux filesystem.
Every modified filesystem block is backed up by debugfs's undo facility.
"""

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

E2TOOLS = Path("/opt/homebrew/opt/e2fsprogs/sbin")
REMOVED_LINKS = (
    "/etc/systemd/system/multi-user.target.wants/dnsmasq.service",
    "/etc/systemd/system/multi-user.target.wants/hostapd.service",
)


def execute(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(" ".join(map(str, args[:2])) + ":\n" + result.stdout + result.stderr)
    return result.stdout


def quote(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("Newline in filesystem path")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def inspect(target, command):
    # debugfs can report an error while returning zero; callers verify its output.
    result = subprocess.run(
        [str(E2TOOLS / "debugfs"), "-R", command, str(target)],
        capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode or "Filesystem not open" in result.stderr:
        raise RuntimeError("Cannot read target filesystem:\n" + result.stderr)
    return result.stdout


def exists(target, path):
    return "Inode:" in inspect(target, "stat " + quote(path))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_logged(args, logfile, allowed_codes=(0,)):
    """Keep slow filesystem operations visible while retaining their evidence."""
    with logfile.open("w") as saved:
        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env={**os.environ, "LC_ALL": "C"},
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                saved.write(line)
                saved.flush()
            code = process.wait()
    if code not in allowed_codes:
        raise RuntimeError(f"Filesystem operation stopped (exit {code}); see {logfile}")


def prepare_filesystem(target, report_dir):
    stats = inspect(target, "stats")
    (report_dir / "superblock-before.txt").write_text(stats)
    features = re.search(r"^Filesystem features:\s*(.*)$", stats, re.MULTILINE)
    if features is None:
        raise RuntimeError("Cannot determine filesystem features; no changes made")
    needs_recovery = "needs_recovery" in features[1].split()
    if needs_recovery:
        print("Recovering committed journal transactions; saving journal.e2undo ...", flush=True)
        # Replay only, as on a normal Linux mount. Never use -y or combine this
        # with -f: further filesystem repairs require separate investigation.
        run_logged(
            [str(E2TOOLS / "e2fsck"), "-p", "-E", "journal_only", "-z",
             str(report_dir / "journal.e2undo"), str(target)],
            report_dir / "journal-recovery.txt", allowed_codes=(0, 1),
        )
    print("Checking the filesystem read-only; errors will stop installation ...", flush=True)
    run_logged(
        [str(E2TOOLS / "e2fsck"), "-fn", str(target)],
        report_dir / "filesystem-before.txt",
    )
    return needs_recovery


def validate_device(target):
    if not re.fullmatch(r"/dev/disk[0-9]+s1", str(target)):
        raise ValueError("Only the first partition of an explicitly selected disk is supported")
    if os.geteuid() != 0:
        raise PermissionError("Installing to an SD card requires macOS administrator authorization")
    parent = str(target).removesuffix("s1")
    disk = plistlib.loads(subprocess.check_output(["diskutil", "info", "-plist", parent]))
    part = plistlib.loads(subprocess.check_output(["diskutil", "info", "-plist", str(target)]))
    if disk.get("Internal") or not disk.get("WholeDisk") or disk.get("BusProtocol") != "USB":
        raise ValueError("Refusing a disk that is not an external USB device")
    if disk.get("TotalSize") != 31914983424 or part.get("Content") != "Linux":
        raise ValueError("Device does not match the inspected 31.9 GB Orange Pi SD card")
    if part.get("ParentWholeDisk") != Path(parent).name:
        raise ValueError("Partition parent mismatch")
    execute(["diskutil", "unmountDisk", parent])
    return {"disk": parent, "partition": str(target), "bytes": disk["TotalSize"]}


def capture_previous_diagnostics(target, report_dir):
    """Save service evidence without reading saved Wi-Fi credentials."""
    sections = []
    for command in (
        "stat /etc/systemd/system/multi-user.target.wants/orangepi-provision.service",
        "cat /etc/systemd/system/orangepi-provision.service",
        "ls -l /var/lib/orangepi-provision",
        "ls -l /etc/NetworkManager/system-connections",
        "cat /var/lib/NetworkManager/NetworkManager.state",
        "cat /etc/modules",
    ):
        sections.append(command + "\n" + inspect(target, command))
    (report_dir / "previous-service-state.txt").write_text("\n".join(sections))
    keywords = ("orangepi-provision", "NetworkManager", "aic8800", "wpa_supplicant", "rfkill")
    for source, filename in (
        ("/var/log/orangepi-provision.log", "previous-provision.log"),
        ("/var/log/syslog", "previous-network-syslog.txt"),
    ):
        if exists(target, source):
            content = inspect(target, "cat " + quote(source))
            lines = content.splitlines()
            if filename != "previous-provision.log":
                lines = [line for line in lines if any(word in line for word in keywords)]
            (report_dir / filename).write_text("\n".join(lines[-800:]) + "\n")


def install(target, staging, report_dir, capture_previous=False):
    print("[1/6] Verifying target and unmounting the SD card ...", flush=True)
    device = validate_device(target) if str(target).startswith("/dev/") else None
    release = inspect(target, "cat /etc/orangepi-release")
    if "BOARD=orangepizero3w\n" not in release:
        raise ValueError("Target is not an Orange Pi Zero 3W filesystem")
    report_dir.mkdir(parents=True, exist_ok=False)
    print("[2/6] Preparing and checking the filesystem ...", flush=True)
    journal_replayed = prepare_filesystem(target, report_dir)
    if capture_previous:
        print("Saving previous provisioning state and network logs ...", flush=True)
        capture_previous_diagnostics(target, report_dir)
    undo = report_dir / "install.e2undo"
    entries = sorted(staging.rglob("*"), key=lambda p: (len(p.parts), str(p)))
    if not entries or not (staging / "usr/local/lib/orangepi-provision/orangepi_provision.py").is_file():
        raise ValueError("Staged provisioning program is missing")
    print("[3/6] Preparing provisioning files and service links ...", flush=True)
    commands = []
    payload = []
    for entry in entries:
        dest = "/" + entry.relative_to(staging).as_posix()
        present = exists(target, dest)
        if entry.is_dir() and not entry.is_symlink():
            if not present:
                commands.append("mkdir " + quote(dest))
                commands.extend([
                    f"set_inode_field {quote(dest)} mode 040755",
                    f"set_inode_field {quote(dest)} uid 0",
                    f"set_inode_field {quote(dest)} gid 0",
                ])
            continue
        if present:
            commands.append("rm " + quote(dest))
        if entry.is_symlink():
            link = os.readlink(entry)
            commands.append("symlink " + quote(dest) + " " + quote(link))
            payload.append({"path": dest, "symlink": link})
        else:
            mode = 0o100000 | (entry.stat().st_mode & 0o777)
            commands.extend([
                "write " + quote(entry) + " " + quote(dest),
                f"set_inode_field {quote(dest)} mode {mode:07o}",
                f"set_inode_field {quote(dest)} uid 0",
                f"set_inode_field {quote(dest)} gid 0",
            ])
            payload.append({"path": dest, "sha256": digest(entry), "mode": oct(mode)})
    for path in REMOVED_LINKS:
        if exists(target, path):
            commands.append("rm " + quote(path))
    batch = report_dir / "debugfs-commands.txt"
    batch.write_text("\n".join(commands) + "\n")
    print("[4/6] Installing files; saving changed blocks in install.e2undo ...", flush=True)
    changed = subprocess.run(
        [str(E2TOOLS / "debugfs"), "-w", "-z", str(undo), "-f", str(batch), str(target)],
        capture_output=True, text=True,
    )
    (report_dir / "debugfs-install.txt").write_text(changed.stdout + changed.stderr)
    if changed.returncode:
        raise RuntimeError("Filesystem installation failed; see debugfs-install.txt and undo backup")
    # Read back the actual filesystem, rather than trusting debugfs's exit status.
    print("[5/6] Reading back files, permissions and service links ...", flush=True)
    with tempfile.TemporaryDirectory(prefix="orangepi-readback-") as temp:
        extracted = Path(temp) / "readback"
        for item in payload:
            stat = inspect(target, "stat " + quote(item["path"]))
            if "symlink" in item:
                if 'Fast link dest: "' + item["symlink"] + '"' not in stat:
                    raise RuntimeError("Symlink verification failed: " + item["path"])
            else:
                if extracted.exists():
                    extracted.unlink()
                inspect(target, "dump " + quote(item["path"]) + " " + quote(extracted))
                if not extracted.is_file() or digest(extracted) != item["sha256"]:
                    raise RuntimeError("File verification failed: " + item["path"])
                mode = re.search(r"Mode:\s+([0-7]+)", stat)
                owner = re.search(r"User:\s+(\d+)\s+Group:\s+(\d+)", stat)
                if not mode or int(mode[1], 8) != (int(item["mode"], 8) & 0o777):
                    raise RuntimeError("Mode verification failed: " + item["path"])
                if not owner or owner.groups() != ("0", "0"):
                    raise RuntimeError("Ownership verification failed: " + item["path"])
    if any(exists(target, path) for path in REMOVED_LINKS):
        raise RuntimeError("Conflicting service enable links remain")
    print("[6/6] Final read-only filesystem check ...", flush=True)
    run_logged([str(E2TOOLS / "e2fsck"), "-fn", str(target)], report_dir / "filesystem-after.txt")
    report = {
        "status": "installed-and-readback-verified", "device": device,
        "target": str(target), "undo": str(undo), "payload": payload,
        "filesystem_checks": "before-and-after-passed", "board_boot_test": "not-performed",
        "journal_replayed": journal_replayed,
    }
    (report_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("INSTALLATION VERIFIED. Report: " + str(report_dir / "result.json"), flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("staging", type=Path)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--capture-previous", action="store_true")
    args = parser.parse_args()
    try:
        install(args.target.resolve(), args.staging.resolve(), args.report_dir.resolve(), args.capture_previous)
    except (OSError, RuntimeError, ValueError) as exc:
        print("INSTALLATION STOPPED: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
