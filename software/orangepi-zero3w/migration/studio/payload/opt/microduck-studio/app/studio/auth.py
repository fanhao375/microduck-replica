"""登录、会话、来源校验。

为什么是 Studio 自己的密码，不用 Linux 的 orangepi 密码：
- 拿 orangepi 密码校验，网页就成了 SSH 密码的猜测口，还绕开了 sshd 的 PerSourcePenalties；
- 密码在安装器里设（经那一次 SSH 把哈希写过来），不在「第一次打开网页」时设，
  免得局域网里谁先打开谁就成了管理员（DNS 重绑定也能替你设）。开发期用命令行：
      python -m studio.auth set-password --state-dir ./state

会话：
- 32 字节随机令牌，cookie `HttpOnly; SameSite=Lax; Path=/`（Lax：从聊天软件点链接进来也带登录；
  跨站的改动请求由 Origin 校验和 CSRF 令牌挡）；
- 勾「记住我」30 天有效，不勾就是 12 小时没动静过期；会话存盘，Studio 重启不用重新登录；
- 改密码、退出时，相关会话立刻作废（页面的 WebSocket 由调用方断开）。

输错：按来源 IP 退避，连错 5 次以后等 30 秒，再错翻倍，最多 5 分钟。只影响网页，不影响 SSH。

危险操作（直连总线、写 EEPROM、改系统设置、装程序）：要求 10 分钟内重新输过密码，像 sudo。

来源校验（挡 DNS 重绑定和跨站请求）：
- Host 只认：本机名、本机名.local、localhost，或者「这条连接实际连到的本机 IP」；
- 改动类请求和 WebSocket 握手还要求 Origin 跟 Host 同源。SameSite 挡不住同一只鸭子上
  别的端口（mediad 的 8080 跟 Studio 是同站），所以 Origin 一定要查。
"""
import argparse
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

COOKIE = "studio_session"
REMEMBER_S = 30 * 24 * 3600
IDLE_S = 12 * 3600
RECENT_AUTH_S = 600
FAIL_FREE = 5
FAIL_BASE_S = 30
FAIL_MAX_S = 300
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
FLUSH_EVERY_S = 300


def _hash(password, salt, n, r, p, dklen):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen,
                          maxmem=64 * 1024 * 1024)


def _write_private(path, data):
    """原子写、权限 0600：先写临时文件再改名，写一半断电也不会留下坏文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


class Auth:
    def __init__(self, state_dir, log=None):
        self.dir = Path(state_dir)
        self.pw_path = self.dir / "password.json"
        self.sess_path = self.dir / "sessions.json"
        self.log = log
        self._lock = threading.Lock()
        self._sessions = {}
        self._fails = {}
        self._dirty_at = None
        self._load_sessions()

    # ---- 密码 ----

    def has_password(self):
        return self.pw_path.is_file()

    def set_password(self, password, keep_token=None):
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        salt = secrets.token_bytes(16)
        digest = _hash(password, salt, **SCRYPT)
        _write_private(self.pw_path, {"scrypt": {**SCRYPT, "salt": salt.hex(), "hash": digest.hex()},
                                      "set_at": int(time.time())})
        kept = False
        with self._lock:
            if keep_token and keep_token in self._sessions:
                self._sessions = {keep_token: self._sessions[keep_token]}
                kept = True
                self._flush_locked()
            else:
                self._sessions.clear()
                self._flush_locked()
        self._log("info", "管理员密码已更新，其他登录全部作废" if kept else "管理员密码已更新，旧的登录全部作废")

    def clear_password(self):
        """清掉管理员密码，回到「同一网络里打开就能用」。跟 set_password 一样只动 Studio 自己的文件。"""
        try:
            self.pw_path.unlink()
        except FileNotFoundError:
            pass
        self.revoke_all()
        self._log("info", "管理员密码已清掉，Studio 回到不用登录的模式")

    def check_password(self, password):
        try:
            rec = json.loads(self.pw_path.read_text(encoding="utf-8"))["scrypt"]
            want = bytes.fromhex(rec["hash"])
            got = _hash(password, bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"], rec["dklen"])
        except (OSError, ValueError, KeyError):
            return False
        return hmac.compare_digest(want, got)

    # ---- 登录 ----

    def login(self, password, ip, remember=False):
        """返回 (令牌, None, 0)；失败返回 (None, 原因, 还要等几秒)。"""
        now = time.time()
        with self._lock:
            f = self._fails.get(ip)
            if f and f["until"] > now:
                wait = int(f["until"] - now) + 1
                return None, f"输错太多次了，{wait} 秒后再试", wait
        if not self.has_password():
            return None, "还没设管理员密码：用安装器设，或者在鸭子上运行 set-password", 0
        # 先记一次失败、再算哈希：就算并发进来，每一次也都已经记在账上（成功了再清掉）
        with self._lock:
            if len(self._fails) > 1000:
                self._fails = {k: v for k, v in self._fails.items() if v["until"] > now or now - v["at"] < 3600}
            f = self._fails.setdefault(ip, {"n": 0, "until": 0, "at": now})
            f["n"] += 1
            f["at"] = now
            wait = 0
            if f["n"] >= FAIL_FREE:
                wait = min(FAIL_BASE_S * 2 ** (f["n"] - FAIL_FREE), FAIL_MAX_S)
                f["until"] = now + wait
            n = f["n"]
        if not self.check_password(password):
            self._log("warn", "登录失败", cat="连接", raw=f"来自 {ip}，连续第 {n} 次")
            return None, "密码不对" + (f"，{wait} 秒后再试" if wait else ""), wait
        token = secrets.token_urlsafe(32)
        sess = {"created": now, "last": now, "remember": bool(remember), "ip": ip,
                "csrf": secrets.token_urlsafe(24), "auth_at": now}
        with self._lock:
            self._fails.pop(ip, None)
            self._sessions = {t: v for t, v in self._sessions.items() if not self._expired(v, now)}
            self._sessions[token] = sess
            self._flush_locked()
        self._log("info", f"登录成功（来自 {ip}{'，记住 30 天' if remember else ''}）", cat="连接")
        return token, None, 0

    def session(self, token, touch=True):
        """有效就返回会话，否则 None。touch=True 顺便更新最后活动时间（真有请求时）；
        WebSocket 的定时检查用 touch=False——页面开着不等于有人在用。"""
        if not token:
            return None
        now = time.time()
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return None
            if self._expired(s, now):
                self._sessions.pop(token, None)
                self._flush_locked()
                return None
            if not touch:
                return dict(s)
            s["last"] = now
            if self._dirty_at is None:
                self._dirty_at = now
            elif now - self._dirty_at > FLUSH_EVERY_S:
                self._flush_locked()
            return dict(s)

    def reauth(self, token, password):
        """危险操作前再输一次密码。"""
        if not self.check_password(password):
            return False
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return False
            s["auth_at"] = time.time()
            self._flush_locked()
        return True

    @staticmethod
    def recent_auth(sess):
        return bool(sess) and time.time() - sess.get("auth_at", 0) < RECENT_AUTH_S

    def logout(self, token):
        with self._lock:
            if self._sessions.pop(token, None) is not None:
                self._flush_locked()

    def revoke_all(self):
        with self._lock:
            self._sessions.clear()
            self._flush_locked()

    def flush(self):
        with self._lock:
            self._flush_locked()

    # ---- 内部 ----

    @staticmethod
    def _expired(s, now):
        created = s.get("created") or 0
        if s.get("remember"):
            return now - created > REMEMBER_S
        return now - (s.get("last") or created) > IDLE_S

    def _load_sessions(self):
        try:
            data = json.loads(self.sess_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        if not isinstance(data, dict):
            return
        self._sessions = {t: s for t, s in data.items()
                          if isinstance(s, dict) and isinstance(s.get("created"), (int, float))
                          and isinstance(s.get("csrf"), str) and not self._expired(s, now)}

    def _flush_locked(self):
        try:
            _write_private(self.sess_path, self._sessions)
        except OSError as e:
            self._log("warn", "会话存盘失败，Studio 重启后要重新登录", cat="系统", raw=str(e))
        self._dirty_at = None

    def _log(self, level, msg, cat="连接", **kw):
        if self.log:
            self.log.write(msg, cat=cat, level=level, **kw)


# ---- 来源校验 ----

def _split_host(host):
    """'microduck-2.local:80' -> ('microduck-2.local', '80')；'[fe80::1]:80' -> ('fe80::1', '80')。"""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end], host[end + 2:] if end != -1 and host[end + 1:end + 2] == ":" else ""
    if host.count(":") == 1:
        name, port = host.split(":")
        return name, port
    return host, ""


class OriginGuard:
    def __init__(self, names=None):
        hn = socket.gethostname().lower()
        self.names = {"localhost", hn, f"{hn}.local"} | {n.lower() for n in (names or [])}

    def host_ok(self, host_header, local_ip=None):
        name, _ = _split_host(host_header)
        if not name:
            return False
        if name in self.names:
            return True
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            return False
        if ip.is_loopback:
            return True
        # IP 只认「这条连接实际连到的本机地址」：DNS 重绑定用的是域名，过不了上面那关，
        # 直接用 IP 的人一定是连到了鸭子的这个 IP 上
        return local_ip is not None and name == str(local_ip).lower()

    def origin_ok(self, origin_header, host_header):
        """改动类请求、WebSocket 握手：Origin 必须跟 Host 同源。没带 Origin 的（老浏览器
        的同源 GET）由调用方决定，改动类一律要求带。"""
        if not origin_header:
            return False
        try:
            u = urlsplit(origin_header)
        except ValueError:
            return False
        if u.scheme not in ("http", "https"):
            return False
        o_name, o_port = _split_host(u.netloc)
        h_name, h_port = _split_host(host_header)
        default = "443" if u.scheme == "https" else "80"
        return o_name == h_name and (o_port or default) == (h_port or default)


def main():
    ap = argparse.ArgumentParser(description="Studio 管理员密码")
    ap.add_argument("cmd", choices=["set-password"])
    ap.add_argument("--state-dir", required=True)
    a = ap.parse_args()
    pw = getpass.getpass("新密码（至少 6 位）：")
    if pw != getpass.getpass("再输一次："):
        raise SystemExit("两次不一样，没改")
    Auth(a.state_dir).set_password(pw)
    print("已设好。旧的登录全部作废。")


if __name__ == "__main__":
    main()
