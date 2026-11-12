"""「更多」页 SSH 卡片的钥匙：登记、列出、删掉 orangepi 的 SSH 公钥。

用户 10-21：会用钥匙的人不用开密码登录，把自己电脑的公钥贴进来就能 SSH。
Studio 本来就以 orangepi 身份跑，~/.ssh/authorized_keys 是 orangepi 自己的文件，所以不经过 root 小助手。

只收一整行公钥（类型 + base64 + 可选注释），不收前面带 command= / from= 这类选项的行：网页上贴进来的，
就照最普通的写法存。指纹跟 `ssh-keygen -l` 的 SHA256 一样，页面上靠它对上是哪一把、删哪一把。
"""
import base64
import binascii
import hashlib
import os
import struct

KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
MAX_LINE = 16384
MAX_COMMENT = 200


class BadKey(ValueError):
    """贴进来的不是一把能用的公钥。消息直接给页面看。"""


def fingerprint(blob):
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


def parse(line):
    """一行公钥 → {"type", "b64", "comment", "fp"}。不对就抛 BadKey（人话）。"""
    if not isinstance(line, str):
        raise BadKey("要贴一行公钥文字")
    text = line.strip()
    if not text:
        raise BadKey("没贴东西")
    if "\n" in text or "\r" in text:
        raise BadKey("只贴一行：一把公钥就是一整行（ssh-ed25519 AAAA… 注释）")
    if len(text) > MAX_LINE:
        raise BadKey("太长了，不像一把公钥")
    parts = text.split(None, 2)
    kind = parts[0]
    if kind not in KEY_TYPES:
        if "PRIVATE KEY" in text:
            raise BadKey("这是私钥！私钥别给任何人，贴 .pub 结尾那个文件的内容")
        raise BadKey("开头要是 ssh-ed25519、ssh-rsa 或 ecdsa-sha2-… 这类公钥类型（贴 .pub 文件的内容；前面不能带选项）")
    if len(parts) < 2:
        raise BadKey("只有类型、没有钥匙本身：把那一整行都贴进来")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise BadKey("钥匙那一段不是有效的 base64：可能没复制全") from None
    if len(blob) < 4:
        raise BadKey("钥匙那一段太短：可能没复制全")
    (n,) = struct.unpack(">I", blob[:4])
    if blob[4:4 + n] != kind.encode("ascii"):
        raise BadKey("钥匙内容跟开头写的类型对不上：可能没复制全")
    comment = parts[2].strip() if len(parts) > 2 else ""
    comment = "".join(c for c in comment if ord(c) >= 32 and ord(c) != 127)[:MAX_COMMENT]
    return {"type": kind, "b64": parts[1], "comment": comment, "fp": fingerprint(blob)}


def _key_in_line(line):
    """authorized_keys 里的一行（可能带选项）→ 解析出的钥匙；注释行、看不懂的回 None。"""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    tokens = text.split()
    for i, t in enumerate(tokens):
        if t in KEY_TYPES and i + 1 < len(tokens):
            try:
                k = parse(" ".join(tokens[i:]))
            except BadKey:
                return None
            k["options"] = i > 0
            return k
    return None


class SshKeys:
    def __init__(self, home=None):
        self.home = home or os.path.expanduser("~")

    @property
    def dir(self):
        return os.path.join(self.home, ".ssh")

    @property
    def path(self):
        return os.path.join(self.dir, "authorized_keys")

    def _read(self):
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def list(self):
        out = []
        for line in self._read().splitlines():
            k = _key_in_line(line)
            if k is not None:
                out.append({"type": k["type"], "comment": k["comment"], "fp": k["fp"], "options": k["options"]})
        return out

    def _write(self, text):
        if os.path.islink(self.dir):
            raise BadKey("~/.ssh 是个软链接，不敢往里写")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        if os.path.islink(self.path):
            raise BadKey("authorized_keys 是个软链接，不敢改")
        tmp = os.path.join(self.dir, f".authorized_keys.studio.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, line):
        """回 (加上没有, 钥匙)。同一把已经在了就不重复写（回 False）。"""
        k = parse(line)
        if any(x["fp"] == k["fp"] for x in self.list()):
            return False, k
        text = self._read()
        if text and not text.endswith("\n"):
            text += "\n"
        text += " ".join(p for p in (k["type"], k["b64"], k["comment"]) if p) + "\n"
        self._write(text)
        return True, k

    def remove(self, fp):
        """按指纹删掉（同一把写了几行都删）。回删掉了几行。"""
        if not isinstance(fp, str) or not fp.startswith("SHA256:"):
            raise BadKey("指纹不对")
        kept, gone = [], 0
        for line in self._read().splitlines(keepends=True):
            k = _key_in_line(line)
            if k is not None and k["fp"] == fp:
                gone += 1
                continue
            kept.append(line)
        if gone:
            self._write("".join(kept))
        return gone
