#!/usr/bin/env python3
"""Locate the Orange Pi master controller on the local network.

The board keeps a DHCP lease, so its address changes after a reboot or a
network change. It is identified by the ed25519 SSH host key that the
provisioning scripts already pin, never by an open port alone: a port scan
only narrows the candidates.
"""

import argparse
import json
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
KNOWN_HOSTS = ROOT / "output" / "orangepi_known_hosts"
RESULT = ROOT / "output" / "master-discovery.json"
HOST_KEY_ALIAS = "orangepizero3w-setup"
KEY_TYPE = "ssh-ed25519"
SSH_PORT = 22
CONSOLE_PORT = 8000
CONSOLE_MARKER = "/opt/microduck-studio"


def parse_pinned_host_key(text, alias=HOST_KEY_ALIAS):
    """Return the pinned ed25519 key for the board alias, or raise."""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == alias and fields[1] == KEY_TYPE:
            return fields[2]
    raise RuntimeError("No pinned %s key for %s in %s" % (KEY_TYPE, alias, KNOWN_HOSTS))


def parse_default_interface(text):
    match = re.search(r"^\s*interface:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise RuntimeError("Default route has no interface")
    return match.group(1)


def parse_prefix(text):
    """Return the /24 prefix (e.g. "192.0.2.") from ifconfig output."""
    match = re.search(r"inet (\d+)\.(\d+)\.(\d+)\.(\d+) netmask 0x([0-9a-fA-F]{8})", text)
    if not match:
        raise RuntimeError("Interface has no IPv4 address")
    octets = match.group(1, 2, 3)
    mask = int(match.group(5), 16)
    if mask != 0xFFFFFF00:
        raise RuntimeError("Only /24 networks are scanned")
    return ".".join(octets) + "."


def parse_arp_mac(text, ip):
    for line in text.splitlines():
        match = re.search(r"\(%s\) at ([0-9a-f:]{17})" % re.escape(ip), line)
        if match:
            return match.group(1)
    return None


def parse_host_key_scan(text, ip=None):
    """Return the ed25519 key that ssh-keyscan reported, or None."""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == KEY_TYPE and (ip is None or fields[0] == ip):
            return fields[2]
    return None


def verdict(evidence, pinned):
    """Classify a candidate: verified (>probable>candidate) or None."""
    if evidence.get("host_key") and evidence["host_key"] == pinned:
        return "verified"
    if evidence.get("console_ok") and evidence.get("ssh_open"):
        return "probable"
    if evidence.get("console_ok"):
        return "candidate"
    return None


def shell(args, timeout=None):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def local_network():
    interface = parse_default_interface(shell(["route", "-n", "get", "default"]))
    prefix = parse_prefix(shell(["ifconfig", interface]))
    return interface, prefix


def probe(ip, pinned, timeout):
    evidence = {"ip": ip}
    with socket.socket() as sock:
        sock.settimeout(1.5)
        evidence["ssh_open"] = sock.connect_ex((ip, SSH_PORT)) == 0
    with socket.socket() as sock:
        sock.settimeout(1.5)
        evidence["console_open"] = sock.connect_ex((ip, CONSOLE_PORT)) == 0
    if evidence["ssh_open"]:
        evidence["host_key"] = parse_host_key_scan(
            shell(["ssh-keyscan", "-T", str(timeout), "-t", KEY_TYPE, ip]), ip
        )
    if evidence["console_open"]:
        try:
            with urlopen("http://%s:%d/healthz" % (ip, CONSOLE_PORT), timeout=timeout) as response:
                body = json.loads(response.read() or b"{}")
            evidence["console_ok"] = bool(
                body.get("ok") and CONSOLE_MARKER in str(body.get("runtime_root", ""))
            )
            evidence["console_version"] = body.get("version")
        except (OSError, ValueError):
            evidence["console_ok"] = False
    evidence["mac"] = parse_arp_mac(shell(["arp", "-n", ip]), ip)
    evidence["verdict"] = verdict(evidence, pinned)
    return evidence


def ping(ip):
    return subprocess.run(
        ["ping", "-c", "1", "-W", "400", "-t", "1", ip], capture_output=True
    ).returncode == 0


def sweep(prefix, workers=64):
    targets = ["%s%d" % (prefix, host) for host in range(1, 255)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [ip for ip, alive in zip(targets, pool.map(ping, targets)) if alive]


def cached_host():
    try:
        return json.loads(RESULT.read_text()).get("ip")
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", default=[], help="probe only this address")
    parser.add_argument("--subnet", help="scan this /24 prefix instead of the default route")
    parser.add_argument("--timeout", type=int, default=5, help="per-probe timeout in seconds")
    parser.add_argument("--no-cache", action="store_true", help="ignore the last known address")
    args = parser.parse_args()

    try:
        pinned = parse_pinned_host_key(KNOWN_HOSTS.read_text())
    except (OSError, RuntimeError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.host:
        candidates, interface, prefix = args.host, None, args.subnet
    else:
        interface, prefix = local_network()
        if args.subnet:
            prefix = args.subnet if args.subnet.endswith(".") else args.subnet + "."
        candidates = []
        if not args.no_cache:
            previous = cached_host()
            if previous and previous.startswith(prefix):
                candidates.append(previous)
        candidates += sweep(prefix)

    found, seen = [], set()
    for ip in candidates:
        if ip in seen:
            continue
        seen.add(ip)
        evidence = probe(ip, pinned, args.timeout)
        if evidence["verdict"]:
            found.append(evidence)
        if evidence["verdict"] == "verified":
            break

    if not found:
        print("No master controller found on %s" % (prefix or "the given hosts"), file=sys.stderr)
        return 1

    order = {"verified": 0, "probable": 1, "candidate": 2}
    found.sort(key=lambda item: order[item["verdict"]])
    best = found[0]
    record = {
        "ip": best["ip"],
        "verdict": best["verdict"],
        "host_key": best.get("host_key"),
        "host_key_matches_pinned": best["verdict"] == "verified",
        "console_ok": bool(best.get("console_ok")),
        "console_version": best.get("console_version"),
        "console_url": "http://%s:%d/" % (best["ip"], CONSOLE_PORT),
        "ssh": "orangepi@%s" % best["ip"],
        "ssh_port_open": bool(best.get("ssh_open")),
        "mac": best.get("mac"),
        "interface": interface,
        "subnet": prefix,
        "also_found": [item["ip"] for item in found[1:]],
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(record, indent=2) + "\n")
    if best["verdict"] != "verified":
        print("warning: identity not proven by the pinned host key", file=sys.stderr)
    print(best["ip"])
    print(json.dumps(record, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
