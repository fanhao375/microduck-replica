#!/usr/bin/env python3
"""开机配网：没连上已保存的 Wi-Fi 就开热点，手机打开页面把家里的网填进来。

没有已保存的 Wi-Fi 时马上开热点。有已保存的网时先等 45 秒，连上就保持安静。
页面提交后先挡住自己 90 秒，再关掉热点去连接；失败就把热点开回来。
"""

import html
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HOTSPOT_CON = "Cubie-Hotspot"
HOTSPOT_NET = "10.42.0."
GRACE_SEC = 45
HOLD_SEC = 90
CONNECT_WAIT_SEC = 25
RUN_DIR = Path("/run/cubie-provision")
HOLD_PATH = RUN_DIR / "hold"
ERROR_PATH = Path("/var/lib/cubie-provision/last-error.txt")
# Studio owns 8000 on every interface, including while the provisioning AP runs.
PORTS = (80,)

_connect_lock = threading.Lock()
_servers = []


def decide(now, boot_at, saved, lan_ip, hold_until):
    """返回 hold、client、hotspot、wait 之一。"""
    if lan_ip:
        return "client"
    if hold_until is not None and now < hold_until:
        return "hold"
    if not saved:
        return "hotspot"
    if now - boot_at >= GRACE_SEC:
        return "hotspot"
    return "wait"


def validate(ssid, password):
    if not isinstance(ssid, str) or not ssid.strip():
        return "要填 Wi-Fi 名字"
    ssid = ssid.strip()
    if len(ssid) > 32 or any(ch in ssid for ch in "\r\n\x00"):
        return "Wi-Fi 名字不对"
    if password and (len(password) < 8 or len(password) > 63 or any(ch in password for ch in "\r\n\x00")):
        return "密码留空，或者填 8 到 63 位"
    return ""


def suffix():
    try:
        token = Path("/etc/machine-id").read_text().strip()
    except OSError:
        token = ""
    token = "".join(ch for ch in token if ch in "0123456789abcdef")
    return (token[-4:] if len(token) >= 4 else "a7z0")


def hotspot_ssid():
    return "qiqilong_" + suffix()


CAPTIVE_API = b'{"captive":true,"user-portal-url":"http://10.42.0.1/"}'
CAPTIVE_CONF = """# 配网热点把所有域名指到本机，手机连上后会弹出页面。
address=/#/10.42.0.1
dhcp-option=114,http://10.42.0.1/captive-api
"""
CAPTIVE_PATH = Path("/etc/NetworkManager/dnsmasq-shared.d/cubie-captive.conf")


def run(args, timeout):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "超时"
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def wifi_device():
    code, out, _err = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"], 10)
    if code != 0:
        return "wlan0"
    for line in out.splitlines():
        device, _, kind = line.partition(":")
        if kind == "wifi" and device and device != "--":
            return device
    return "wlan0"


def saved_wifi():
    code, out, _err = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
    if code != 0:
        return False
    for line in out.splitlines():
        name, _, kind = line.partition(":")
        if kind == "802-11-wireless" and name and name != HOTSPOT_CON:
            return True
    return False


def lan_ip(device):
    code, out, _err = run(["ip", "-4", "-o", "addr", "show", "dev", device], 5)
    if code != 0:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        addr = parts[parts.index("inet") + 1].split("/", 1)[0]
        if addr.startswith(HOTSPOT_NET) or addr.startswith("169.254."):
            continue
        return addr
    return ""


def hold_until():
    try:
        return float(HOLD_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def write_hold(until):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HOLD_PATH.with_suffix(".tmp")
    tmp.write_text(str(until))
    os.chmod(tmp, 0o644)
    tmp.replace(HOLD_PATH)


def clear_hold():
    try:
        HOLD_PATH.unlink()
    except OSError:
        pass


def read_error():
    try:
        return ERROR_PATH.read_text(errors="replace").strip()
    except OSError:
        return ""


def write_error(text):
    ERROR_PATH.parent.mkdir(parents=True, exist_ok=True)
    ERROR_PATH.write_text(text.strip())


def clear_error():
    try:
        ERROR_PATH.unlink()
    except OSError:
        pass


def hotspot_active():
    code, out, _err = run(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"], 10)
    if code != 0:
        return False
    return HOTSPOT_CON in {line.strip() for line in out.splitlines()}


def ensure_hotspot(device, ssid):
    code, out, _err = run(["nmcli", "-t", "-f", "NAME", "connection", "show"], 10)
    exists = code == 0 and HOTSPOT_CON in {line.strip() for line in out.splitlines()}
    if not exists:
        run([
            "nmcli", "connection", "add", "type", "wifi",
            "ifname", device, "con-name", HOTSPOT_CON, "autoconnect", "no", "ssid", ssid,
        ], 15)
    run([
        "nmcli", "connection", "modify", HOTSPOT_CON,
        "802-11-wireless.ssid", ssid,
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "connection.autoconnect", "no",
        "ipv4.method", "shared",
    ], 15)
    run(["nmcli", "connection", "modify", HOTSPOT_CON, "remove", "wifi-sec"], 15)
    if not hotspot_active():
        run(["nmcli", "connection", "up", HOTSPOT_CON], 30)
    print("hotspot %s on %s" % (ssid, device), flush=True)


def scan(ssid_self):
    code, out, _err = run(
        ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"], 15)
    if code != 0:
        return []
    found = []
    seen = set()
    for line in out.splitlines():
        name, sep, signal = line.rpartition(":")
        if not sep:
            continue
        name = name.replace("\\:", ":").strip()
        if not name or name == ssid_self or name in seen:
            continue
        seen.add(name)
        found.append({"ssid": name, "signal": signal})
    return found


def connect_worker(device, ssid, password):
    try:
        time.sleep(1.5)
        run(["nmcli", "connection", "down", HOTSPOT_CON], 15)
        cmd = ["nmcli", "--", "device", "wifi", "connect", ssid, "ifname", device]
        if password:
            cmd[6:6] = ["password", password]
        code, _out, err = run(cmd, 40)
        deadline = time.time() + CONNECT_WAIT_SEC
        ip = ""
        while time.time() < deadline:
            ip = lan_ip(device)
            if ip:
                break
            time.sleep(1)
        if code == 0 and ip:
            clear_error()
            print("joined %s at %s" % (ssid, ip), flush=True)
            return
        reason = " ".join(err.split())[:180] or "没有拿到地址"
        write_error("没连上「%s」：%s" % (ssid, reason))
        print("join failed", ssid, flush=True)
        run(["nmcli", "connection", "up", HOTSPOT_CON], 30)
    finally:
        clear_hold()


def reply_for(path):
    if path == "/scan.json":
        return 200, "application/json; charset=utf-8", None
    if path in ("/captive-api", "/.well-known/captive-portal"):
        return 200, "application/captive+json", CAPTIVE_API
    return 200, "text/html; charset=utf-8", None


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        status, content_type, body = self._body()
        self._send(status, content_type, body, include_body=False)

    def do_GET(self):
        status, content_type, body = self._body()
        self._send(status, content_type, body)

    def _body(self):
        path = self.path.split("?", 1)[0]
        status, content_type, body = reply_for(path)
        if path == "/scan.json":
            body = json.dumps(scan(hotspot_ssid()), ensure_ascii=False).encode()
        elif body is None:
            body = page().encode()
        return status, content_type, body

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/connect":
            self._send(404, "text/plain; charset=utf-8", "没有这个页面".encode())
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 4096:
            self._send(413, "text/plain; charset=utf-8", "内容太长".encode())
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = parse_qs(raw, keep_blank_values=True)
        ssid = (form.get("ssid") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        problem = validate(ssid, password)
        if problem:
            self._send(400, "text/html; charset=utf-8", page(problem).encode())
            return
        if not _connect_lock.acquire(blocking=False):
            self._send(200, "text/html; charset=utf-8", result_page("正在连接，等这一次做完再试。").encode())
            return
        write_hold(time.time() + HOLD_SEC)
        threading.Thread(
            target=_finish_connect, args=(wifi_device(), ssid, password), daemon=True).start()
        self._send(200, "text/html; charset=utf-8", result_page(
            "板子要关掉热点，去连「" + ssid + "」。手机会断开，这是正常的。"
            "如果大约一分钟后热点又出现，重新打开这个页面，红字会写没连上的原因。").encode())

    def log_message(self, fmt, *args):
        return

    def _send(self, status, content_type, body, include_body=True):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(body)


def _finish_connect(device, ssid, password):
    try:
        connect_worker(device, ssid, password)
    finally:
        _connect_lock.release()


def page(banner=""):
    error = banner or read_error()
    notice = "<p class='bad'>" + html.escape(error) + "</p>" if error else ""
    ssid = html.escape(hotspot_ssid())
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>给板子配网</title>
<style>
body { font: 18px/1.45 sans-serif; margin: 1.2rem; color: #172018; background: #f4f7f2; }
h1 { font-size: 1.6rem; margin: 0 0 .4rem; }
label { display: block; margin: .9rem 0 .3rem; }
input { width: 100%; box-sizing: border-box; font: inherit; padding: .7rem; }
button { font: inherit; padding: .8rem 1rem; margin-top: .8rem; }
.bad { color: #8d1d1d; }
ul { padding-left: 1.1rem; }
</style></head><body>
<h1>给板子配网</h1>
<p>热点是 {{SSID}}，不用密码。手机提示没网时选保持连接。</p>
{{NOTICE}}
<form method="post" action="/connect">
<label>Wi-Fi 名字<input name="ssid" maxlength="32" required autocomplete="off"></label>
<label>密码<input name="password" type="password" maxlength="63" autocomplete="off"></label>
<button type="submit">连接</button>
</form>
<h2>附近的网络</h2>
<ul id="nets"><li>正在看附近的网络…</li></ul>
<script>
fetch("/scan.json").then(r => r.json()).then(rows => {
  const ul = document.getElementById("nets");
  ul.innerHTML = "";
  if (!rows.length) { ul.innerHTML = "<li>没扫到，直接把名字写在上面。</li>"; return; }
  rows.forEach(row => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = row.ssid;
    b.onclick = () => { document.querySelector("[name=ssid]").value = row.ssid; };
    li.appendChild(b);
    ul.appendChild(li);
  });
}).catch(() => { document.getElementById("nets").innerHTML = "<li>没扫到，直接把名字写在上面。</li>"; });
</script>
</body></html>""".replace("{{SSID}}", ssid).replace("{{NOTICE}}", notice)


def result_page(text):
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>正在连接</title>
<style>body { font: 18px/1.45 sans-serif; margin: 1.2rem; }</style></head>
<body><p>""" + html.escape(text) + """</p></body></html>"""


def bind_servers():
    if _servers:
        return
    for port in PORTS:
        try:
            server = ThreadingHTTPServer(("10.42.0.1", port), Handler)
        except OSError as exc:
            print("listen %s failed: %s" % (port, exc), flush=True)
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _servers.append(server)
        print("listen 10.42.0.1:%s" % port, flush=True)


def stop_servers():
    while _servers:
        server = _servers.pop()
        server.shutdown()
        server.server_close()


def set_hostname():
    name = "cubie-" + suffix()
    current = ""
    try:
        current = Path("/etc/hostname").read_text().strip()
    except OSError:
        pass
    if current != name:
        run(["hostnamectl", "set-hostname", name], 10)
    hosts = Path("/etc/hosts")
    try:
        text = hosts.read_text()
    except OSError:
        return
    lines = []
    changed = False
    for line in text.splitlines(True):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "127.0.1.1" and parts[1] != name:
            line = "127.0.1.1 " + name + "\n"
            changed = True
        lines.append(line)
    if changed:
        hosts.write_text("".join(lines))


def ensure_captive_dns():
    CAPTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = ""
    try:
        current = CAPTIVE_PATH.read_text()
    except OSError:
        pass
    if current != CAPTIVE_CONF:
        CAPTIVE_PATH.write_text(CAPTIVE_CONF)


def main():
    boot_at = time.time()
    set_hostname()
    ensure_captive_dns()
    print("cubie provision %s" % hotspot_ssid(), flush=True)
    while True:
        device = wifi_device()
        action = decide(time.time(), boot_at, saved_wifi(), lan_ip(device), hold_until())
        if action == "hotspot":
            ensure_hotspot(device, hotspot_ssid())
            bind_servers()
        elif action == "client":
            stop_servers()
        time.sleep(2)


if __name__ == "__main__":
    main()
