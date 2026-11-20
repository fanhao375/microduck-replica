#!/usr/bin/env python3
"""Open a phone provisioning hotspot when no saved Wi-Fi can be reached."""

import hashlib
import html
import json
import logging
import os
import re
import string
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs

HOTSPOT_CON = "OrangePi-Provision-Hotspot"
# Retain imported profiles from the first installer; nmcli 1.36 cannot assign it.
HOTSPOT_UUID = "d7975e41-b894-52a1-9b01-7c14ac373804"
HOTSPOT_IP = "10.42.0.1"
CLIENT_PREFIX = "OrangePi-Provision-WiFi-"
STATE_DIR = Path("/var/lib/orangepi-provision")
ERROR_PATH = STATE_DIR / "last-error.txt"
CLIENT_PATH = STATE_DIR / "client-uuid"
HOTSPOT_PATH = STATE_DIR / "hotspot-uuid"
IDENTITY_PATH = STATE_DIR / "device-id"
LOG_PATH = Path("/var/log/orangepi-provision.log")
VERSION = "20261109-nm136-fix"
CAPTIVE_PATH = Path("/etc/NetworkManager/dnsmasq-shared.d/orangepi-captive.conf")
CAPTIVE_CONF = "address=/#/10.42.0.1\ndhcp-option=114,http://10.42.0.1/captive-api\n"
CAPTIVE_API = b'{"captive":true,"user-portal-url":"http://10.42.0.1/"}'
GRACE_SEC = 45
PORTS = (80,)
_connect_lock = threading.Lock()
_servers = []
_suffix = ""
log = logging.getLogger("orangepi-provision")


class ProvisionError(RuntimeError):
    pass


def run(args, timeout=15):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except OSError as exc:
        return 127, "", str(exc)


def nmcli(args, timeout=20):
    code, out, err = run(["nmcli", "--wait", str(max(1, timeout - 5)), "--", *args], timeout)
    if code:
        # NM can echo a rejected secret. Redact values before normalization or
        # truncation, otherwise a quoted/long password could leak a fragment.
        secret_keys = {"password", "passwd", "psk", "wep-key0", "wep-key1", "wep-key2", "wep-key3"}
        for index, key in enumerate(args[:-1]):
            if (key in secret_keys or key.endswith((
                    ".psk", ".password", ".password-raw", ".private-key-password",
                    ".phase2-private-key-password", ".wep-key0", ".wep-key1", ".wep-key2", ".wep-key3"))):
                secret = args[index + 1]
                if secret:
                    err = err.replace(secret, "[REDACTED]")
        reason = re.sub(r"\s+", " ", err).strip()[:300] or "没有错误详情"
        raise ProvisionError("NetworkManager %s 失败（代码 %d）：%s" % (" ".join(args[:2]), code, reason))
    return out


def split_nmcli(line):
    fields, value, escaped = [], [], False
    for char in line:
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(value))
            value = []
        else:
            value.append(char)
    if escaped:
        value.append("\\")
    return fields + ["".join(value)]


def query(fields, *args):
    code, out, _err = run(["nmcli", "-t", "-e", "yes", "-f", fields, *args])
    if code:
        raise ProvisionError("无法读取 NetworkManager 状态（代码 %d）" % code)
    return [split_nmcli(line) for line in out.splitlines() if line]


def wifi_device():
    for row in query("DEVICE,TYPE", "device", "status"):
        if len(row) == 2 and row[1] == "wifi" and row[0] != "--":
            return row[0]
    return ""


def saved_wifi():
    hotspot_ids = {HOTSPOT_UUID, read_text(HOTSPOT_PATH)}
    return any(len(row) == 3 and row[2] in ("802-11-wireless", "wifi")
               and row[0] not in hotspot_ids and not is_hotspot_name(row[1])
               for row in query("UUID,NAME,TYPE", "connection", "show"))


def profile_uuid(name):
    matches = [row[0] for row in query("UUID,NAME", "connection", "show")
               if len(row) == 2 and row[1] == name]
    if len(matches) > 1:
        raise ProvisionError("发现重名的配网配置，停止操作以保护已有网络")
    return matches[0] if matches else ""


def is_hotspot_name(name):
    return name == HOTSPOT_CON or name.startswith(HOTSPOT_CON + "-")


def hotspot_uuid():
    remembered = read_text(HOTSPOT_PATH)
    owned = [row for row in query("UUID,NAME", "connection", "show")
             if len(row) == 2 and is_hotspot_name(row[1])]
    chosen = next((row[0] for row in owned if row[0] == remembered), "")
    chosen = chosen or next((row[0] for row in owned if row[0] == HOTSPOT_UUID), "")
    chosen = chosen or (owned[0][0] if owned else "")
    if chosen and chosen != remembered:
        write_state(HOTSPOT_PATH, chosen)
    return chosen


def active_uuid(device):
    for row in query("UUID,DEVICE", "connection", "show", "--active"):
        if len(row) == 2 and row[1] == device:
            return row[0]
    return ""


def hotspot_active(device):
    connection_uuid = hotspot_uuid()
    return bool(connection_uuid) and active_uuid(device) == connection_uuid


def lan_ip(device):
    if hotspot_active(device):
        return ""
    code, out, _err = run(["ip", "-4", "-o", "addr", "show", "dev", device, "scope", "global"], 5)
    if code:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts:
            address = parts[parts.index("inet") + 1].split("/", 1)[0]
            if not address.startswith(("169.254.", "127.")):
                return address
    return ""


def decide(now, boot_at, saved, lan_ip, connecting=False):
    if connecting:
        return "hold"
    if lan_ip:
        return "client"
    return "hotspot" if not saved or now - boot_at >= GRACE_SEC else "wait"


def validate(ssid, password):
    if not isinstance(ssid, str) or not ssid:
        return "要填 Wi-Fi 名字"
    if len(ssid.encode("utf-8")) > 32 or any(char in ssid for char in "\r\n\x00"):
        return "Wi-Fi 名字最多 32 个 UTF-8 字节，不能包含换行"
    if not isinstance(password, str):
        return "Wi-Fi 密码格式不正确"
    if not password:
        return ""
    if len(password) == 64 and all(char in string.hexdigits for char in password):
        return ""
    if not (8 <= len(password) <= 63 and all(32 <= ord(char) <= 126 for char in password)):
        return "密码留空，或填 8–63 位英文字符密码（也支持 64 位十六进制密钥）"
    return ""


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def write_state(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def suffix():
    global _suffix
    if _suffix:
        return _suffix
    _suffix = read_text(IDENTITY_PATH)
    if not _suffix:
        identity = ""
        for path in (Path("/proc/device-tree/serial-number"),
                     Path("/sys/firmware/devicetree/base/serial-number")):
            identity = read_text(path).strip("\x00")
            if identity:
                break
        if not identity:
            for interface in sorted(Path("/sys/class/net").glob("*")):
                if (interface / "wireless").exists():
                    identity = read_text(interface / "address")
                    if identity and identity != "00:00:00:00:00:00":
                        break
        identity = identity or read_text(Path("/etc/machine-id")) or str(uuid.uuid4())
        _suffix = hashlib.sha256(identity.encode()).hexdigest()[:6]
        write_state(IDENTITY_PATH, _suffix)
    return _suffix


def hotspot_ssid():
    return "OrangePi_" + suffix()


def ensure_hotspot(device):
    if hotspot_active(device):
        return True
    nmcli(["radio", "wifi", "on"])
    connection_uuid = hotspot_uuid()
    if not connection_uuid:
        name = HOTSPOT_CON + "-" + str(uuid.uuid4())
        try:
            # UUID is read-only to nmcli 1.36, even during connection add.
            nmcli(["connection", "add", "type", "wifi", "ifname", device,
                   "con-name", name, "ssid", hotspot_ssid(), "connection.autoconnect", "no"])
            connection_uuid = profile_uuid(name)
            if not connection_uuid:
                raise ProvisionError("无法读取新热点配置的 UUID")
            write_state(HOTSPOT_PATH, connection_uuid)
        except (ProvisionError, OSError):
            cleanup_candidate(name)
            raise
    nmcli(["connection", "modify", "uuid", connection_uuid,
           "connection.interface-name", device, "connection.autoconnect", "no",
           "802-11-wireless.ssid", hotspot_ssid(), "802-11-wireless.mode", "ap",
           "802-11-wireless.band", "bg", "ipv4.method", "shared",
           "ipv4.addresses", HOTSPOT_IP + "/24", "ipv6.method", "disabled"])
    nmcli(["connection", "up", "uuid", connection_uuid], 40)
    if not hotspot_active(device):
        raise ProvisionError("热点没有成功启动")
    log.info("Provisioning hotspot ready: %s at %s", hotspot_ssid(), HOTSPOT_IP)
    return True


def delete_previous_client(connection_uuid):
    if not connection_uuid:
        return
    try:
        rows = query("connection.id", "connection", "show", "uuid", connection_uuid)
        if rows and rows[0][-1].startswith(CLIENT_PREFIX):
            nmcli(["connection", "delete", "uuid", connection_uuid])
    except ProvisionError as exc:
        log.warning("Could not remove previous provisioning profile: %s", exc)


def cleanup_candidate(name):
    # An add timeout may occur after NM saved the profile. Resolve only this
    # attempt's random name, rather than guessing the UUID or deleting by SSID.
    if not name.startswith((CLIENT_PREFIX, HOTSPOT_CON + "-")):
        return
    try:
        candidate = profile_uuid(name)
    except ProvisionError as exc:
        log.warning("Could not resolve failed provisioning profile: %s", exc)
        return
    if not candidate:
        return
    for operation in ("down", "delete"):
        code, _out, _err = run(
            ["nmcli", "--wait", "10", "connection", operation, "uuid", candidate], 15)
        if code and code != 10:
            log.warning("Candidate cleanup %s failed (code %d)", operation, code)


def connect_worker(device, ssid, password):
    name = CLIENT_PREFIX + str(uuid.uuid4())
    connected = False
    try:
        # A separate profile ensures a corrected password replaces an old one,
        # while failed attempts leave every previously working profile intact.
        nmcli(["connection", "add", "type", "wifi", "ifname", device,
               "con-name", name, "ssid", ssid, "connection.autoconnect", "no",
               "802-11-wireless.mode", "infrastructure", "802-11-wireless.hidden", "yes",
               "ipv4.method", "auto", "ipv6.method", "auto"])
        candidate = profile_uuid(name)
        if not candidate:
            raise ProvisionError("无法读取新 Wi-Fi 配置的 UUID")
        if password:
            nmcli(["connection", "modify", "uuid", candidate,
                   "802-11-wireless-security.key-mgmt", "wpa-psk",
                   "802-11-wireless-security.psk", password])
        time.sleep(1.5)
        if hotspot_active(device):
            nmcli(["connection", "down", "uuid", hotspot_uuid()])
        nmcli(["connection", "up", "uuid", candidate], 45)
        if active_uuid(device) != candidate or not lan_ip(device):
            raise ProvisionError("路由器没有分配 IPv4 地址")
        nmcli(["connection", "modify", "uuid", candidate,
               "connection.autoconnect", "yes", "connection.autoconnect-priority", "100"])
        previous = read_text(CLIENT_PATH)
        write_state(CLIENT_PATH, candidate)
        connected = True
        delete_previous_client(previous)
        ERROR_PATH.unlink(missing_ok=True)
        log.info("Wi-Fi connected and saved")
    except (ProvisionError, OSError) as exc:
        log.warning("Provisioning attempt failed: %s", exc)
        write_state(ERROR_PATH, "没有连上 Wi-Fi。请检查网络名称、密码及信号后重试。")
    finally:
        if not connected:
            cleanup_candidate(name)
            ensure_hotspot(device)
            bind_servers()


def scan():
    rows = query("SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no")
    found = {}
    for row in rows:
        if len(row) == 2 and row[0] and row[0] != hotspot_ssid():
            found.setdefault(row[0], {"ssid": row[0], "signal": row[1]})
    return list(found.values())


def page(banner=""):
    error = banner or read_text(ERROR_PATH)
    notice = "<p class='bad'>" + html.escape(error) + "</p>" if error else ""
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>香橙派配网</title>
<style>body{font:18px/1.5 sans-serif;margin:1.2rem;background:#f4f7f2;color:#172018}
main{max-width:36rem;margin:auto}h1{font-size:1.6rem}label{display:block;margin:.9rem 0 .3rem}
input{width:100%;box-sizing:border-box;font:inherit;padding:.7rem}button{font:inherit;padding:.7rem;margin-top:.7rem}
.bad{color:#8d1d1d}ul{padding-left:1.2rem}</style></head><body><main>
<h1>给香橙派连接 Wi-Fi</h1><p>热点：{{SSID}}，不用密码。手机提示无法上网时，请选择保持连接。</p>
{{NOTICE}}<form method="post" action="/connect"><label>Wi-Fi 名字
<input name="ssid" maxlength="32" required autocomplete="off" autocapitalize="none" spellcheck="false"></label>
<label>Wi-Fi 密码<input name="password" type="password" maxlength="64" autocomplete="off"></label>
<button type="submit">保存并连接</button></form><h2>附近的网络</h2><ul id="nets"><li>正在读取…</li></ul>
<p>没有找到时，可以手动输入网络名称。支持 2.4 GHz Wi-Fi。</p><script>
fetch('/scan.json').then(r=>r.json()).then(rows=>{const ul=document.getElementById('nets');ul.textContent='';
if(!rows.length){ul.textContent='暂时没有找到网络，请手动输入。';return;}
rows.forEach(row=>{const li=document.createElement('li'),b=document.createElement('button');b.type='button';
b.textContent=row.ssid;b.onclick=()=>{document.querySelector('[name=ssid]').value=row.ssid;};li.appendChild(b);ul.appendChild(li);});
}).catch(()=>{document.getElementById('nets').textContent='暂时无法读取，请手动输入。';});
</script></main></body></html>""".replace("{{SSID}}", html.escape(hotspot_ssid())).replace("{{NOTICE}}", notice)


def result_page(text):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>正在连接</title><body style="font:18px/1.5 sans-serif;margin:1.2rem"><p>'
            + html.escape(text) + "</p></body></html>")


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def do_HEAD(self):
        self.do_GET(include_body=False)

    def do_GET(self, include_body=True):
        path = self.path.split("?", 1)[0]
        content_type = "text/html; charset=utf-8"
        if path == "/scan.json":
            try:
                rows = scan()
            except ProvisionError:
                rows = []
            body = json.dumps(rows, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        elif path in ("/captive-api", "/.well-known/captive-portal"):
            body, content_type = CAPTIVE_API, "application/captive+json"
        else:
            body = page().encode()
        self._send(200, content_type, body, include_body)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/connect":
            self._send(404, "text/plain; charset=utf-8", "没有这个页面".encode())
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(400, "text/plain; charset=utf-8", b"Invalid Content-Length")
            return
        if length < 0 or length > 4096:
            self._send(413, "text/plain; charset=utf-8", b"Invalid request size")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        ssid, password = (form.get("ssid") or [""])[0], (form.get("password") or [""])[0]
        problem = validate(ssid, password)
        if problem:
            self._send(400, "text/html; charset=utf-8", page(problem).encode())
            return
        if not _connect_lock.acquire(blocking=False):
            self._send(409, "text/html; charset=utf-8", result_page("正在处理，请稍后再试。").encode())
            return
        started = False
        try:
            device = wifi_device()
            if not device:
                self._send(503, "text/html; charset=utf-8", page("无线网卡尚未准备好，请稍后重试。").encode())
                return
            self._send(200, "text/html; charset=utf-8", result_page(
                "已收到配网信息，正在连接「" + ssid + "」。手机会断开热点，这是正常的。"
                "成功后香橙派会保存此网络；失败后热点会在约一分钟内恢复，请重新连接热点查看提示。").encode())
            threading.Thread(target=_finish_connect, args=(device, ssid, password), daemon=True).start()
            started = True
        finally:
            if not started:
                _connect_lock.release()

    def _send(self, status, content_type, body, include_body=True):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def _finish_connect(device, ssid, password):
    try:
        connect_worker(device, ssid, password)
    except (ProvisionError, OSError) as exc:
        log.error("Could not restore provisioning hotspot: %s", exc)
    finally:
        _connect_lock.release()


def bind_servers():
    if _servers:
        return
    server = ThreadingHTTPServer((HOTSPOT_IP, PORTS[0]), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _servers.append(server)


def stop_servers():
    while _servers:
        server = _servers.pop()
        server.shutdown()
        server.server_close()


def configure_logging():
    pattern = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=pattern)
    try:
        handler = RotatingFileHandler(LOG_PATH, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        os.chmod(LOG_PATH, 0o600)
        handler.setFormatter(logging.Formatter(pattern))
        log.addHandler(handler)
    except OSError as exc:
        log.warning("Persistent provisioning log unavailable: %s", exc)


def main():
    configure_logging()
    version_code, version, _err = run(["nmcli", "--version"], 10)
    log.info("Starting provisioning version=%s NM=%s", VERSION,
             version.strip() if version_code == 0 else "unavailable (code %d)" % version_code)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CAPTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if read_text(CAPTIVE_PATH) != CAPTIVE_CONF.strip():
        CAPTIVE_PATH.write_text(CAPTIVE_CONF)
    boot_at = time.monotonic()
    last_device = None
    while True:
        # The same lock covers all main-loop NM mutations and a whole connection
        # attempt; an elapsed timer cannot reopen AP mode during a slow attempt.
        if _connect_lock.acquire(blocking=False):
            try:
                device = wifi_device()
                if device != last_device:
                    if device:
                        log.info("Wi-Fi interface ready: %s", device)
                    else:
                        log.info("Waiting for Wi-Fi interface")
                    last_device = device
                if device:
                    action = decide(time.monotonic(), boot_at, saved_wifi(), lan_ip(device))
                    if action == "hotspot":
                        ensure_hotspot(device)
                        bind_servers()
                    elif action == "client":
                        stop_servers()
            except (ProvisionError, OSError) as exc:
                log.warning("Waiting to retry provisioning: %s", exc)
            finally:
                _connect_lock.release()
        time.sleep(2)


if __name__ == "__main__":
    main()
