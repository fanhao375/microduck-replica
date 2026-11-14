"""Local integration harness, excluded from the installation payload.

Run: python3.13 -I run_dev.py
Check isolation without starting services: python3.13 -I run_dev.py --check
All state and RPC logs remain in artifacts/dev-* after shutdown.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
STUDIO = HERE / "workspace" / "studio"
ARTIFACTS = HERE / "artifacts"
HOST = "127.0.0.1"
PORTS = {"robotd": 18701, "configd": 18702, "studio": 18880}
SCHEMA = "console-local-fake/1"


def validate_endpoints(host=HOST, ports=None):
    ports = PORTS if ports is None else ports
    if host != "127.0.0.1" or ports != PORTS:
        raise ValueError("Only fixed 127.0.0.1 fake endpoints are allowed")
    for path in ("/run/robotd.sock", "/run/configd.sock", "/usr/local/sbin/microduck-helper"):
        if os.path.lexists(path):
            raise ValueError(f"Real device/helper marker found: {path}; refusing local fake mode")


def load_duckfs():
    spec = importlib.util.spec_from_file_location("console_dev_duckfs", STUDIO / "dev" / "duckfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_runtime():
    if not (STUDIO / "run.py").is_file():
        raise ValueError("Prepare workspace first with prepare_console.py")
    ARTIFACTS.mkdir(exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="dev-", dir=ARTIFACTS)).resolve()
    fake_root = load_duckfs().build(runtime / "duck-root")
    # These are ordinary directories under our generated root, never macOS /var.
    (fake_root / "var/lib/microduck-helper/native-update").mkdir(parents=True)
    (runtime / "state").mkdir()
    (runtime / "logs").mkdir()
    return runtime


def install_test_guard(fake_root):
    """Scope the real fail-closed validator; never replace its decision with False."""
    from studio import helper

    original = helper.native_update_state

    def local_native_update_state(*, root="/"):
        if root not in ("/", str(fake_root)):
            raise ValueError("The dev guard cannot read an external root")
        return original(root=str(fake_root))

    helper.native_update_state = local_native_update_state
    return original


def install_socket_guard():
    """A child may reach only this harness's TCP peers, never device Unix sockets."""
    def audit(event, args):
        if event not in ("socket.connect", "socket.bind"):
            return
        address = args[1]
        allowed_ports = {PORTS["studio"]} if event == "socket.bind" else set(PORTS.values())
        if (not isinstance(address, tuple) or len(address) != 2 or address[0] != HOST
                or address[1] not in allowed_ports):
            raise PermissionError("Local dev mode rejects external and Unix socket endpoints")

    sys.addaudithook(audit)


def studio_args(runtime):
    return ["--listen", f"{HOST}:{PORTS['studio']}",
            "--robotd", f"tcp:{HOST}:{PORTS['robotd']}",
            "--configd", f"tcp:{HOST}:{PORTS['configd']}",
            "--tof", "none", "--camera-socket", "none",
            "--servo-sim", "--servo-port", "sim", "--dev-autologin",
            "--dev-duck-root", str(runtime / "duck-root"),
            "--state-dir", str(runtime / "state"), "--log-dir", str(runtime / "logs"),
            "--net-state", str(runtime / "net.json"), "--web-dir", str(STUDIO / "web"),
            "--duck-label", "本地联调（模拟硬件）"]


def boot_studio(runtime):
    validate_endpoints()
    runtime = Path(runtime).resolve()
    if runtime.parent != ARTIFACTS.resolve() or not runtime.name.startswith("dev-"):
        raise ValueError("The child requires a generated artifacts/dev-* runtime")
    manifest = json.loads((runtime / "manifest.json").read_text())
    if (manifest.get("schema") != SCHEMA or manifest.get("parent_pid") != os.getppid()
            or manifest.get("ports") != PORTS or manifest.get("host") != HOST):
        raise ValueError("Only the supervising local fake launcher may start this child")
    fake_root = runtime / "duck-root"
    if fake_root.is_symlink() or not fake_root.is_dir():
        raise ValueError("The generated fake root is missing or a symlink")
    install_socket_guard()
    sys.path[:0] = [str(STUDIO / "deps"), str(STUDIO)]
    install_test_guard(fake_root)
    from studio import helper
    if helper.native_update_state().get("active"):
        raise ValueError("The isolated fake update guard is active; inspect the test root")
    # Even an accidental browser request must not select a real serial port.
    from studio.servo import ServoSession
    original_open = ServoSession.open

    def simulated_open(self, by=None, port=None, restored=False):
        if port not in (None, "sim") or not self.sim:
            return False, "本地联调只允许模拟舵机，拒绝真实串口"
        return original_open(self, by=by, port="sim", restored=restored)

    ServoSession.open = simulated_open
    from studio.__main__ import main
    main(studio_args(runtime))


def check_isolation():
    """No listeners or child processes; exercise the original validator on fake files."""
    validate_endpoints()
    runtime = create_runtime()
    sys.path[:0] = [str(STUDIO / "deps"), str(STUDIO)]
    from studio import helper
    fake_root = runtime / "duck-root"
    original = helper.native_update_state
    assert original(root=str(fake_root))["active"] is False
    journal = fake_root / helper.NATIVE_UPDATE_JOURNAL.lstrip("/")
    journal.write_text("invalid journal", encoding="utf-8")
    assert original(root=str(fake_root))["active"] is True
    journal.unlink()
    install_test_guard(fake_root)
    try:
        assert helper.native_update_state()["active"] is False
        try:
            helper.native_update_state(root="/etc")
        except ValueError:
            pass
        else:
            raise AssertionError("External guard root was accepted")
    finally:
        helper.native_update_state = original
    for host, ports in (("0.0.0.0", PORTS), ("localhost", PORTS), (HOST, {**PORTS, "robotd": 17001})):
        try:
            validate_endpoints(host, ports)
        except ValueError:
            pass
        else:
            raise AssertionError("Non-local or non-test endpoint was accepted")
    install_socket_guard()
    for address in ("/run/robotd.sock", ("192.168.1.2", 18701), (HOST, 17001)):
        try:
            sys.audit("socket.connect", None, address)
        except PermissionError:
            pass
        else:
            raise AssertionError("Non-test socket was accepted")
    sys.audit("socket.connect", None, (HOST, PORTS["robotd"]))
    sys.audit("socket.bind", None, (HOST, PORTS["studio"]))
    print(f"Isolation check passed; no services started.\nArtifacts: {runtime}")


def ensure_ports_free():
    for name, port in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError as error:
                raise ValueError(f"{name} port {HOST}:{port} is unavailable; no existing process will be stopped") from error


def wait_ready(process, port, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError(f"Child exited ({process.returncode}); see artifacts logs")
        try:
            with socket.create_connection((HOST, port), timeout=.3):
                return
        except OSError:
            time.sleep(.1)
    raise ValueError(f"Timed out waiting for {HOST}:{port}")


def stop_child(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=3)


def serve():
    validate_endpoints()
    ensure_ports_free()
    runtime = create_runtime()
    children, streams = [], []
    print(f"Local fake-only integration. Artifacts: {runtime}", flush=True)

    def launch(name, command):
        stream = (runtime / name).open("wb")
        streams.append(stream)
        process = subprocess.Popen(command, cwd=STUDIO, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=os.name == "posix")
        children.append(process)
        return process

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        fake = launch("fake.log", [sys.executable, "-I", str(STUDIO / "dev/fake_robotd.py"),
            "--fixture", str(STUDIO / "tests/fixtures/now-20261018.jsonl"),
            "--robotd-port", str(PORTS["robotd"]), "--configd-port", str(PORTS["configd"]),
            "--posture", "holding", "--calib", "ok", "--record", str(runtime / "rpc.jsonl"),
            "--net-mode", "wifi", "--net-state-file", str(runtime / "net.json")])
        wait_ready(fake, PORTS["robotd"])
        wait_ready(fake, PORTS["configd"])
        (runtime / "manifest.json").write_text(json.dumps({"schema": SCHEMA, "host": HOST,
            "ports": PORTS, "parent_pid": os.getpid(), "fake_pid": fake.pid}, indent=2))
        studio = launch("studio.log", [sys.executable, "-I", str(Path(__file__).resolve()),
                                        "--studio-child", str(runtime)])
        wait_ready(studio, PORTS["studio"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{HOST}:{PORTS['studio']}/api/session", timeout=3) as response:
            if response.status != 200:
                raise ValueError("Studio session readiness check failed")
        print(f"Ready: http://{HOST}:{PORTS['studio']}/\nRPC log: {runtime / 'rpc.jsonl'}\n"
              f"Studio log: {runtime / 'studio.log'}\nCtrl+C / SIGTERM stops both children.", flush=True)
        while True:
            if any(process.poll() is not None for process in children):
                raise ValueError("A child exited; stopping its peer. See artifacts logs")
            time.sleep(.25)
    except KeyboardInterrupt:
        print("Stopping local fake services…", flush=True)
    finally:
        for process in reversed(children):
            stop_child(process)
        for stream in streams:
            stream.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify isolation without starting services")
    parser.add_argument("--studio-child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not sys.flags.isolated or sys.version_info[:2] != (3, 13):
        parser.error("Use python3.13 -I run_dev.py")
    try:
        if args.studio_child:
            boot_studio(args.studio_child)
        elif args.check:
            check_isolation()
        else:
            serve()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Local dev launch refused: {error}") from error


if __name__ == "__main__":
    main()
