"""Provision the board's local CA and phone HTTPS leaf on a managed software install."""

import fcntl
import grp
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import tempfile
import time


def openssl(*args):
    return subprocess.run(
        ["/usr/bin/openssl", *map(str, args)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        check=True,
    )


def secure_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("证书目录的类型、所有者或权限不正确")


def issue_leaf(directory, ca, name, group):
    csr = directory / "duck.csr"
    key = directory / "duck.key"
    openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=" + name,
        "-keyout",
        key,
        "-out",
        csr,
    )
    extensions = directory / "duck.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName=DNS:{name},DNS:{name}.local,DNS:localhost,"
        "IP:10.42.0.1,IP:127.0.0.1\n"
    )
    openssl(
        "x509",
        "-req",
        "-in",
        csr,
        "-CA",
        ca / "ca.crt",
        "-CAkey",
        ca / "ca.key",
        "-set_serial",
        "0x" + os.urandom(16).hex(),
        "-days",
        "365",
        "-sha256",
        "-extfile",
        extensions,
        "-out",
        directory / "duck.crt",
    )
    os.chown(key, -1, group)
    key.chmod(0o640)
    (directory / "duck.crt").chmod(0o644)
    csr.unlink()
    extensions.unlink()


def valid_leaf(directory, name):
    try:
        openssl("x509", "-in", directory / "duck.crt", "-noout", "-checkend", 30 * 86400)
        for option, identity in (("-verify_hostname", name), ("-verify_ip", "10.42.0.1")):
            openssl(
                "verify", option, identity, "-CAfile", directory / "ca.crt", directory / "duck.crt"
            )
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
            directory / "duck.crt", directory / "duck.key"
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def provision(directory, name, group):
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", name):
        raise ValueError("主机名需要是有效的 DNS 标签")
    directory.parent.mkdir(mode=0o755, exist_ok=True)
    secure_directory(directory.parent)
    parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        if directory.exists() or directory.is_symlink():
            secure_directory(directory)
            for basename in ("ca.key", "ca.crt", "duck.crt", "duck.key"):
                path = directory / basename
                if os.path.lexists(path) and not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("现有证书路径需要是普通文件")
            if (directory / "ca.crt").exists() or (directory / "ca.key").exists():
                ca_key = directory / "ca.key"
                if not ca_key.is_file() or not (directory / "ca.crt").is_file():
                    raise ValueError("现有 CA 文件不完整，请先从本机备份恢复")
                info = ca_key.stat()
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError("CA 私钥需要 root 所有且仅 root 可读")
                if valid_leaf(directory, name):
                    return
                with tempfile.TemporaryDirectory(dir=directory.parent) as work:
                    stage = Path(work)
                    issue_leaf(stage, directory, name, group)
                    for basename in ("duck.key", "duck.crt"):
                        (stage / basename).replace(directory / basename)
                return
        with tempfile.TemporaryDirectory(dir=directory.parent) as work:
            stage = Path(work) / "tls"
            stage.mkdir(mode=0o700)
            openssl(
                "req",
                "-new",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "3650",
                "-sha256",
                "-subj",
                "/CN=Microduck-Local-CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout",
                stage / "ca.key",
                "-out",
                stage / "ca.crt",
            )
            (stage / "ca.key").chmod(0o600)
            (stage / "ca.crt").chmod(0o644)
            issue_leaf(stage, stage, name, group)
            stage.chmod(0o755)
            backup = None
            if directory.exists():
                backup = directory.with_name("tls.pre-phone-" + str(time.time_ns()))
                directory.rename(backup)
            try:
                stage.rename(directory)
            except OSError:
                if backup:
                    backup.rename(directory)
                raise
    finally:
        os.close(parent_fd)


if __name__ == "__main__":
    provision(Path("/etc/microduck/tls"), socket.gethostname(), grp.getgrnam("radxa").gr_gid)
