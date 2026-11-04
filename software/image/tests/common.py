#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPT_APPLY = BASE / "apply-rootfs.sh"
SCRIPT_BUILD = BASE / "build-image.sh"
SCRIPT_CAPTURE = BASE / "capture-payload.sh"
REAL_OVERLAY = BASE / "overlay"
CALIB = BASE / "calib" / "hd1910-calibration.option-a.toml"
IS_WINDOWS = os.name == "nt"
SH = shutil.which("sh")

REF = Path(__file__).resolve().parent / "fixtures"

# 测试要能独立跑；扫描用例需要的第三方作者名在运行期拼接，源码里不出现完整词。
THIRD_PARTY_NAME = "kis" + "sqy"
THIRD_PARTY_WORD = THIRD_PARTY_NAME[:4]


def require_sh():
    if SH is None:
        raise AssertionError("找不到 sh（需要 Git Bash 或 WSL）；测试不能跳过")


def posix_path(path):
    if path is None or str(path) == "":
        return ""
    p = Path(path).resolve()
    if not IS_WINDOWS:
        return str(p)
    # Git Bash 把系统临时目录挂成 /tmp；从 /c/Users/... 走时 mkdir -p 会被 ACL 拒绝。
    try:
        rel = p.relative_to(Path(tempfile.gettempdir()).resolve())
        return "/tmp/" + rel.as_posix()
    except ValueError:
        pass
    drive, rest = os.path.splitdrive(str(p))
    return "/" + drive[0].lower() + rest.replace("\\", "/")


def put(root, rel, data=b"", mode=None):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    p.write_bytes(data)
    if mode is not None:
        os.chmod(p, mode)
    return p


def ref_text(rel):
    return (REF / rel).read_bytes()


def sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rmtree_force(path):
    def onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    if IS_WINDOWS:
        shutil.rmtree(path, onexc=onexc)
    else:
        shutil.rmtree(path)


def grant_777(path):
    for p in [path] + list(Path(path).rglob("*")):
        try:
            os.chmod(p, 0o777)
        except OSError:
            pass


def run_shell(args, cwd=BASE, env_extra=None, umask=None, timeout=180):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MSYS"] = "winsymlinks:nativestrict"
    if env_extra:
        env.update(env_extra)
    if umask is not None:
        # Git archives preserve these scripts as 0644; match the normal explicit-sh path.
        cmd = [SH, "-c", f'umask {umask}; exec "$0" "$@"', SH] + args
    else:
        cmd = [SH] + args
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def make_tempdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


class FakeProject:
    """一套假 payload / rootfs / studio / overlay，供 apply 测试复用。"""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.shell_tmp = self.tmp / "shell-tmp"
        self.shell_tmp.mkdir()
        self.robotd = b"robotd-binary-v1\n"
        self.model_a = b"model-a-bytes\n"
        self.model_b = b"model-b-bytes\n"

    # ---- payload ----
    def payload_files(self, robotd_dropin=None, payload_extra=None):
        files = {
            "opt/robot/feetech-ft5-r5/robotd": self.robotd,
            "opt/robot/feetech-ft5-r5/padd": b"padd\n",
            "opt/robot/feetech-ft5-r5/mediad": b"mediad\n",
            "opt/robot/feetech-ft5-r5/onnxruntime/lib/libonnxruntime.so.1.28.0": b"ort\n",
            "opt/robot/feetech-ft5-r5/models/aaa/policy.onnx": self.model_a,
            "opt/robot/feetech-ft5-r5/roller/bbb/policy.onnx": self.model_b,
            "opt/robot/feetech-ft5-r5/padd.service": b"[Unit]\nDescription=padd\n",
            "opt/robot/feetech-ft5-r5/padd-sysusers.conf": b"u padd\n",
            "opt/robot/feetech-ft5-r5/LICENSE": b"Apache License 2.0\n",
            "etc/systemd/system/robotd.service.d/zzzz-r17-0151.conf": (
                robotd_dropin if robotd_dropin is not None else
                b"[Service]\nExecStart=\n"
                b"ExecStart=/opt/robot/feetech-ft5-r5/robotd --config /etc/robot/feetech-ft5/robotd.toml\n"
            ),
            "etc/systemd/system/padd.service.d/zzzz-replica-netpad.conf": b"[Service]\nCapabilityBoundingSet=CAP_NET_RAW\n",
            "etc/robot/feetech-ft5/robotd.toml": ref_text("etc/robot/feetech-ft5/robotd.toml"),
            "etc/robot/feetech-ft5/robotd.saved.toml": b'["bus"]\n"port" = "/dev/ttyS2"\n',
            "var/lib/robot/weird-ble": b"marker\n",
        }
        if payload_extra:
            files.update(payload_extra)
        return files

    def make_payload(self, bad_hash=False, extra_file=None, extra_symlink=False, chmod777=False,
                     robotd_dropin=None, payload_extra=None, manifest_extra=None):
        root = self.tmp / "payload-root"
        if root.exists():
            rmtree_force(root)
        files = self.payload_files(robotd_dropin=robotd_dropin, payload_extra=payload_extra)
        entries = []
        for rel, data in sorted(files.items()):
            put(root, rel, data)
            digest = sha(data)
            if bad_hash and rel.endswith("/robotd"):
                digest = "0" * 64
            entries.append({"path": rel, "sha256": digest, "size": len(data)})
        manifest = {
            "captured_at": "2026-10-20T00:00:00Z",
            "uname_r": "6.1.0-test",
            "robotd_sha256": sha(self.robotd),
            "files": entries,
        }
        if manifest_extra:
            manifest.update(manifest_extra)
        put(root, "payload.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        if extra_file:
            put(root, extra_file, b"not in manifest\n")

        name = "payload.tgz"
        if bad_hash:
            name = "payload-bad.tgz"
        elif extra_file:
            name = "payload-extra.tgz"
        elif extra_symlink:
            name = "payload-link.tgz"
        if chmod777:
            grant_777(root)
        tgz = self.tmp / name
        with tarfile.open(tgz, "w:gz") as tf:
            for path in sorted(root.rglob("*")):
                tf.add(path, arcname=path.relative_to(root).as_posix())
            if extra_symlink:
                info = tarfile.TarInfo("etc/evil-link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../outside-payload"
                info.mode = 0o777
                tf.addfile(info)
        return tgz

    # ---- rootfs ----
    def make_rootfs(self, name="rootfs"):
        root = self.tmp / name
        root.mkdir(parents=True)
        os.chmod(root, 0o755)
        (root / "etc" / "systemd" / "system").mkdir(parents=True)

        passwd = "root:x:0:0:root:/root:/bin/bash\n"
        for uid in range(1, 23):
            passwd += f"user{uid}:x:{uid}:{uid}:User {uid}:/home/user{uid}:/usr/sbin/nologin\n"
        passwd += f"radxa:x:1000:1000:{THIRD_PARTY_WORD}:/home/radxa:/bin/bash\n"
        put(root, "etc/passwd", passwd)
        put(root, "etc/passwd-", passwd)
        shadow = "root:!:19000:0:99999:7:::\n"
        for uid in range(1, 23):
            shadow += "user%d:!:19000:0:99999:7:::\n" % uid
        shadow += "radxa:!:19000:0:99999:7:::\n"
        put(root, "etc/shadow", shadow)
        put(root, "etc/shadow-", shadow)
        put(root, "etc/group", "root:x:0:\nrobot:x:2000:\nradxa:x:1000:\n")

        put(root, "etc/systemd/system/basic.target.wants/armbian-resize-filesystem.service", "[Unit]\n")
        put(root, "usr/lib/systemd/system/armbian-resize-filesystem.service", "[Unit]\n")
        put(root, "usr/lib/systemd/system/ssh.service", "[Unit]\nDescription=ssh\n")

        put(root, "etc/ssh/sshd_config", ref_text("etc/ssh/sshd_config"))
        put(root, "etc/ssh/ssh_host_ed25519_key", "private\n")
        put(root, "etc/ssh/ssh_host_ed25519_key.pub", "public\n")
        put(root, "root/.ssh/authorized_keys", "ssh-rsa AAAA\n")
        put(root, "root/.ssh/known_hosts", "host key\n")
        put(root, "home/radxa/.ssh/authorized_keys", "ssh-rsa BBBB\n")
        put(root, "home/radxa/.ssh/known_hosts", "host key\n")

        put(root, "etc/systemd/system/padd.service", ref_text("etc/systemd/system/padd.service"))
        put(root, "etc/systemd/system/padd.service.d/zzzz-r17-0151.conf",
            ref_text("etc/systemd/system/padd.service.d/zzzz-r17-0151.conf"))
        put(root, "etc/systemd/system/gamesir-xboxd.service",
            ref_text("etc/systemd/system/gamesir-xboxd.service"))
        put(root, "etc/systemd/system/gamesir-xboxd.service.d/zzzz-r17-ready.conf",
            ref_text("etc/systemd/system/gamesir-xboxd.service.d/zzzz-r17-ready.conf"))
        put(root, "etc/systemd/system/aic3104-init.service",
            ref_text("etc/systemd/system/aic3104-init.service"))
        put(root, "etc/systemd/system/alsa-restore.service.d/microduck-audio.conf",
            ref_text("etc/systemd/system/alsa-restore.service.d/microduck-audio.conf"))
        put(root, "etc/systemd/system/alsa-state.service.d/microduck-audio.conf",
            ref_text("etc/systemd/system/alsa-state.service.d/microduck-audio.conf"))
        put(root, "etc/systemd/system/multi-user.target.wants/gamesir-xboxd.service", "[Unit]\n")
        put(root, "etc/systemd/system/multi-user.target.wants/aic3104-init.service", "[Unit]\n")
        put(root, "etc/systemd/system/multi-user.target.wants/microduck-setup.service", "[Unit]\n")
        put(root, "etc/systemd/system/microduck-setup.service", "[Unit]\nDescription=setup\n")

        put(root, "etc/udev/rules.d/70-gamesir-xbox-bridge.rules",
            ref_text("etc/udev/rules.d/70-gamesir-xbox-bridge.rules"))
        put(root, "etc/modules-load.d/gamesir-xbox.conf",
            ref_text("etc/modules-load.d/gamesir-xbox.conf"))
        put(root, "etc/sudoers.d/microduck-alsa-volume",
            ref_text("etc/sudoers.d/microduck-alsa-volume"))
        put(root, "var/lib/alsa/asound.state",
            "state.Microduck {\n"
            "    control.1 {\n"
            "        iface MIXER\n"
            "        name 'Playback Volume'\n"
            "        value 99\n"
            "    }\n"
            "}\n")
        put(root, "etc/sudoers.d/README", ref_text("etc/sudoers.d/README"))
        put(root, "etc/microduck-v112-image.txt", ref_text("etc/microduck-v112-image.txt"))
        put(root, "boot/extlinux/extlinux.conf", ref_text("boot/extlinux/extlinux.conf"))

        put(root, "usr/local/sbin/gamesir-xboxd", "#!/bin/sh\necho gamesir\n")
        put(root, "usr/local/sbin/gamesir-xbox-status", "#!/bin/sh\necho status\n")
        put(root, "usr/local/bin/aic3104-init.sh", "#!/bin/sh\nexit 0\n", mode=0o755)
        put(root, "usr/local/libexec/microduck-setup-portal.py", "print('portal')\n")
        put(root, "usr/local/libexec/__pycache__/microduck-setup-portal.cpython-313.pyc", b"pyc")
        put(root, "usr/local/sbin/microduck-public-image", "#!/bin/sh\n")
        put(root, "home/radxa/microduck-public-image.sh", "#!/bin/sh\n")
        put(root, "home/radxa/microduck-public-image-R5.sh", "#!/bin/sh\n")
        put(root, "home/radxa/microduck-official-init/install.sh", "#!/bin/sh\n")
        put(root, "home/radxa/microduck-official-init/pkg.tar", b"PKG")
        put(root, "home/radxa/microduck-official-install-20261019/install.log", "installed\n")
        put(root, "home/radxa/microduck-official-install-20261019/pkg.deb", b"DEB")
        put(root, "home/radxa/studio-dev/logs-dev/old.log", "old\n")
        put(root, "home/radxa/studio-dev/state-dev/old.json", "{}\n")

        put(root, "opt/robot/feetech-ft5-r5/gamepad-r7/x.py", "print('other author')\n")
        put(root, "opt/robot/feetech-ft5-r5/service.sh", "#!/bin/sh\n")
        put(root, "opt/robot/feetech-ft5-r5/_model_yaml/x.yaml", "x: 1\n")
        put(root, "opt/robot/feetech-ft5-r5/unused.txt", "remove me\n")
        put(root, "opt/robot/feetech-ft5-r5/models/legacy/policy.onnx", b"legacy-model\n")
        # 底图里同名模型目录下的训练归档、payload 没有的旧文件
        put(root, "opt/robot/feetech-ft5-r5/models/aaa/export-data.zip", b"zip\n")
        put(root, "opt/robot/feetech-ft5-r5/models/aaa/upstream-params/a/b.json", b"{}\n")
        put(root, "opt/robot/feetech-ft5-r5/roller/bbb/training-request.json", b"{}\n")
        put(root, "opt/robot/feetech-ft5-r5/onnxruntime/lib/libonnxruntime.so.1.20.0", b"old-ort\n")
        put(root, "opt/robot/feetech-ft5-r5/robotd", b"old-robotd\n")

        put(root, "etc/robot/feetech-ft5/robotd.toml", ref_text("etc/robot/feetech-ft5/robotd.toml"))
        put(root, "etc/robot/robotd.toml", ref_text("etc/robot/robotd.toml"))
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.toml", "zero_raw = 123\n")
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.toml.bak-20261001", "old\n")
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.toml.previous", "old\n")
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.saved.toml", "old\n")
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.robot.toml", "old\n")
        put(root, "etc/robot/feetech-ft5/hd1910-calibration.tmp.1", "old\n")
        put(root, "etc/robot/feetech-ft5/custom-calib.toml", "unknown = true\n")
        put(root, "etc/robot/updater.toml",
            "[updater]\nverify_signatures = true\n"
            "    allow_dev_keys = true  # 仅本机开发签名\n")
        put(root, "etc/robot/trusted_keys/local.dev.pub", "ssh-ed25519 DEV-KEY\n")
        put(root, "etc/robot/trusted_keys/release-2026.pub", "ssh-ed25519 RELEASE-KEY\n")
        (root / "var/lib/robot/config").mkdir(parents=True)
        put(root, "etc/NetworkManager/system-connections/Microduck-Setup.nmconnection",
            f"[wifi]\nssid=Microduck-{THIRD_PARTY_NAME}\n")

        put(root, "etc/machine-id", "old-machine-id\n")
        put(root, "var/lib/dbus/machine-id", "old-dbus-id\n")
        put(root, "var/log/syslog", "log line\n")
        put(root, "var/log/journal/system.journal", b"journal")
        put(root, "var/log.hdd/syslog", "disk log line\n")
        put(root, "var/log.hdd/journal/system.journal", b"disk journal")
        put(root, "var/lib/bluetooth/AA/BB/info", "bt\n")
        put(root, "var/lib/NetworkManager/NetworkManager.state", "nm\n")
        put(root, "var/lib/systemd/random-seed", b"seed")
        put(root, "etc/fake-hwclock.data", "2020-01-01 00:00:00\n")

        put(root, "root/.bash_history", "secret\n")
        put(root, "root/.viminfo", "secret\n")
        put(root, "root/.python_history", "secret\n")
        put(root, "root/.cache/pip/x", "cache\n")
        put(root, "root/.local/share/recently-used.xbel", "recent\n")
        put(root, "home/radxa/.bash_history", "secret\n")
        put(root, "home/radxa/.lesshst", "secret\n")
        put(root, "home/radxa/.wget-hsts", "secret\n")
        put(root, "home/radxa/.cache/x", "cache\n")
        put(root, "home/radxa/.local/share/recently-used.xbel", "recent\n")
        put(root, "var/lib/microduck-helper/socket", "")

        put(root, "tmp/junk", "junk\n")
        put(root, "var/cache/apt/archives/old.deb", b"deb")

        for rel in ["usr/share/doc", "usr/local/sbin", "usr/local/bin",
                    "usr/local/libexec", "boot/extlinux", "var/log/journal",
                    "var/log.hdd/journal"]:
            (root / rel).mkdir(parents=True, exist_ok=True)
        return root

    def make_studio(self):
        studio = self.tmp / "studio"
        put(studio, "deploy/microduck-helper", "#!/bin/sh\nexit 2\n")
        put(studio, "deploy/zzzz-studio-bus.conf",
            "[Unit]\nConditionPathExists=!/run/microduck-helper/bus-held\n")
        put(studio, "deploy/sudoers-microduck-studio",
            "# test\nradxa ALL=(root) NOPASSWD: /usr/local/sbin/microduck-helper\n")
        put(studio, "deploy/microduck-studio.service",
            "[Unit]\nDescription=Studio\n[Service]\nExecStart=/usr/bin/python3 run.py\n")
        put(studio, "studio/__init__.py", '__version__ = "9.9.9-test"\n')
        put(studio, "studio/state.py", "STATE = 1\n")
        put(studio, "studio/logs.py", "LOGS = 1\n")
        put(studio, "run.py", "print('studio')\n")
        put(studio, "README.md", "# Studio\n")
        put(studio, "LICENSE", "MIT\n")
        put(studio, "web/index.html", "<h1>studio</h1>\n")
        put(studio, "deps/pkg/__init__.py", "PKG = 1\n")
        put(studio, "studio/__pycache__/state.cpython-312.pyc", b"stale-pyc\n")
        put(studio, "deploy/__pycache__/helper.pyc", b"stale-pyc\n")
        put(studio, "run_tests.py", "print('tests')\n")
        put(studio, "tests/test_private.py", "SERIAL = 'duck-2222'\n")
        put(studio, "dev/tool.py", "print('dev')\n")
        put(studio, "docs/keep.md", "keep\n")
        put(studio, "docs/20261019-private/secret.md", "drop\n")
        put(studio, "logs/x.log", "drop\n")
        put(studio, "logs-dev/x.log", "drop\n")
        put(studio, "state-dev/x.json", "{}\n")
        put(studio, "_testlogs/x.log", "drop\n")
        put(studio, "nested/__pycache__/x.pyc", b"drop")
        return studio

    def make_overlay(self):
        overlay = self.tmp / "overlay"
        shutil.copytree(REAL_OVERLAY, overlay)
        put(overlay, "etc/systemd/system/demo.service",
            "[Unit]\nDescription=demo\n[Install]\nWantedBy=multi-user.target\n")
        put(overlay, "enable.list", "# 测试清单\ndemo.service\nssh.service\n")
        put(overlay, "usr/local/bin/demo-tool", "#!/bin/sh\necho demo\n")
        put(overlay, "modes.list", "# 路径 模式\nusr/local/bin/demo-tool 0755\n")
        return overlay

    def run_apply(self, root, payload, studio, overlay, umask=None, env_extra=None, timeout=180):
        env = {}
        if env_extra:
            env.update(env_extra)
        return run_shell(
            [posix_path(SCRIPT_APPLY), posix_path(root), posix_path(payload),
             posix_path(studio), posix_path(overlay)],
            env_extra=env or None,
            umask=umask,
            timeout=timeout,
        )
