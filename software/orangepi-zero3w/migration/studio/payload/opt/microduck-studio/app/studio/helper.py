"""调 root 小助手（/usr/local/sbin/microduck-helper）停、起主程序 robotd（舵机总线交接）。

Studio 不是 root。sudoers 只放行 microduck-helper 这一个全路径，所以这里用 `sudo -n`
调它、stdin 传一个 JSON，别的权限一点没有。小助手认 bus-take / bus-give-back /
bus-abort / pad-pair / pad-forget / wifi-hotspot-down / wifi-hotspot-up / calib-read / calib-commit /
ssh-status / ssh-password / imu-recalibrate 固定动词，参数逐个校验，
robotd 的状态它自己去读，不信网页传来的（deploy/INSTALL.md）。

- bus-take：停 robotd、立旗（robotd 起不来）、确认串口空出来 → Studio 打开总线。不卸力。
- bus-give-back {"soft": true}：Studio 已经在总线上全卸了 → 撤旗、起 robotd、等启动四步成功。
- bus-give-back {"marker": {...}}：不卸力交还（写交接标记，fork 的 robotd 读走并接手保持）。
  要 fork 的 robotd 装上、drop-in 打开 Environment 才认；旧版不认，会照老规矩全卸（result=limp）。
- pad-pair：以 root 跑官方 `robotctl pad pair --json`。weird-ble 板上 robotctl 会先停 btd、把适配器断电再开，
  所以这条主路能配上新 Switch Pro 类手柄。旧版小助手回 bad_verb，调用方据此退回 configd / bluetoothctl。
- pad-forget：以 root 跑官方 `robotctl pad forget MAC`。
- wifi-hotspot-down / wifi-hotspot-up：配网时关 / 开鸭子自己的热点（nmcli connection down / up
  Microduck-Hotspot）。热点本来就没开 / 本来就开着算成功；这两个不拿 bus 的 helper.lock，用 wifi.lock。
- calib-read：只读当前标定文件，回解析后的内容和现在的模式（标定 / 运动）；不拿锁、不写东西。
- calib-commit：把舵机零位 / 方向 / IMU 方向写进 robotd 的标定文件（hd1910-calibration.toml），
  出厂镜像跑标定模式时顺便删掉 commissioning drop-in，再重启主程序切运动模式。成功要
  mode=motion、phase=control，而且 calibration-status 读回来逐项一致。它跑在独立 systemd 单元里，
  Studio 被重启也只是暂时看不到输出；最终结果在 /run/microduck-helper/last-result.json。
  dry_run 只回要写的 TOML。旧版小助手回 bad_verb，调用方据此提示重装。

没装小助手（电脑上开发、台架、演示）就优雅回退：bus_take 当没做（交给 servo.open 自己的
robotd_active 检查去报错），bus_give_back 当没做（交还以后主程序由人手动起）。
"""
import json
import os
import queue
import re
import stat
import subprocess
import sys
import threading
import time

HELPER = "/usr/local/sbin/microduck-helper"
FLAG = "/run/microduck-helper/bus-held"       # 接管期间立着：robotd 的 drop-in 看到它就不肯起来
LAST_RESULT = "/run/microduck-helper/last-result.json"
IMU_REDO_MANIFEST = "/var/lib/microduck-helper/imu-recalibrate/manifest.json"
NATIVE_UPDATE_JOURNAL = "/var/lib/microduck-helper/native-update/journal.json"
NATIVE_UPDATE_ACTIVE = "/var/lib/microduck-helper/native-update/active"
NATIVE_UPDATE_EXCLUSIONS = (NATIVE_UPDATE_ACTIVE, "/run/microduck-helper/native-update-restore",
    "/etc/systemd/system/robotd.service.d/zzzzzzz-microduck-native-update.conf", *(
        "/etc/systemd/system/"+unit+".service.d/zzzzzzz-microduck-native-update.conf"
        for unit in ("padd", "gamesir-xboxd", "duck-echo")))

_avail = None


def flag_present():
    """接管旗在不在（只在 Linux 上查；电脑 / 测试里 patch 这个函数）。"""
    if not sys.platform.startswith("linux"):
        return False
    return os.path.exists(FLAG)


def available(refresh=False):
    """小助手在不在、sudoers 放行没（结果缓存：装不装在运行中不会变）。
    用 `sudo -n microduck-helper bus-nope` 探：能调就返回 2（动词不认识，说明文件在、sudoers 放行）；
    返回别的 = 没装 / 没放行 / 不在 Linux。refresh=True 时重新探一次（配网每次连接都要看）。"""
    global _avail
    if refresh:
        _avail = None
    if _avail is not None:
        return _avail
    if not sys.platform.startswith("linux") or not os.path.exists(HELPER):
        _avail = False
        return False
    try:
        r = subprocess.run(["sudo", "-n", HELPER, "bus-nope"], capture_output=True, text=True, timeout=5)
        _avail = r.returncode == 2
    except (OSError, subprocess.TimeoutExpired):
        _avail = False
    return _avail


def _call(verb, payload, timeout, on_line=None):
    """跑 `sudo -n microduck-helper <verb>`，stdin 传 JSON。回 (ok, result, detail)。
    ok = 最后一行 done 的 exit 是 0；result / detail 是 done 那一行的 detail 字段。
    on_line 给了就边跑边把每一行进度交出去（写标定的进度）；不给还是等它跑完再解析。"""
    data = json.dumps(payload, ensure_ascii=False)
    if on_line is None:
        try:
            proc = subprocess.run(["sudo", "-n", HELPER, verb], input=data,
                                  capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"{type(e).__name__}", None
        result, detail = None, None
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("step") == "done":
                d = obj.get("detail") or {}
                result, detail = d.get("result"), d
        if result is None:
            err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            return False, err or f"exit {proc.returncode}", None
        return detail.get("exit") == 0, result, detail
    return _call_stream(verb, data, timeout, on_line)


def _call_stream(verb, data, timeout, on_line):
    """同一条命令，但把标准输出一行一行交出去（小助手每一步一行）。stderr 并进 stdout：
    这样 sudo 自己报的错也不会把管道堵住。"""
    try:
        proc = subprocess.Popen(["sudo", "-n", HELPER, verb], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        return False, f"{type(e).__name__}", None
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except OSError:
        pass
    lines = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    result, detail, noise = None, None, []
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            try:
                proc.kill()
            except OSError:
                pass
            return False, "TimeoutExpired", None
        try:
            line = lines.get(timeout=min(0.2, left))
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            noise.append(line)
            continue
        if obj.get("step") == "done":
            d = obj.get("detail") or {}
            result, detail = d.get("result"), d
        elif on_line is not None:
            on_line(obj)
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
    if result is None:
        return False, "\n".join(noise[-5:]) or f"exit {proc.returncode}", None
    return detail.get("exit") == 0, result, detail


def bus_take(by, allow_seated=False, timeout=30):
    """停 robotd、把舵机总线交给 Studio（不卸力）。回 (ok, result, detail)。"""
    return _call("bus-take", {"by": by, "allow_seated": allow_seated}, timeout)


def bus_give_back(by, *, soft=True, marker=None, timeout=60):
    """交还总线、起 robotd。soft=True 是软交还（Studio 已全卸）；传 marker 是不卸力交还（fork 接手保持）。
    回 (ok, result, detail)。"""
    payload = {"by": by, "soft": True} if soft else {"by": by, "marker": marker}
    return _call("bus-give-back", payload, timeout)


def bus_abort(by, timeout=10):
    """中止交接（交接倒数期间页面按了卸力）：KILL robotd、删标记、把总线还给 Studio。回 (ok, result, detail)。"""
    return _call("bus-abort", {"by": by}, timeout)


def pad_pair(by, mac=None, timeout=60):
    """官方 robotctl pad pair（小助手以 root 跑）。回 (ok, result, detail)。
    detail["pair"] 是 PadPairResult；detail["stderr"] 是 robotctl 的几行提示（暂停 btd、警告等）。"""
    payload = {"by": by, "timeout": timeout}
    if mac:
        payload["mac"] = mac
    return _call("pad-pair", payload, timeout + 45)


def pad_forget(by, mac, timeout=60):
    """官方 robotctl pad forget（小助手以 root 跑）。回 (ok, result, detail)。"""
    return _call("pad-forget", {"by": by, "mac": mac}, timeout)


def wifi_hotspot_down(by, ssid=None, timeout=30):
    """关鸭子自己的热点（小助手最多等 10 秒）。热点本来就没开算成功。ssid 写进连接窗口文件，给 netcheck 看。"""
    payload = {"by": by}
    if ssid:
        payload["ssid"] = ssid
    return _call("wifi-hotspot-down", payload, timeout)


def wifi_hotspot_up(by, timeout=40):
    """开鸭子自己的热点（小助手最多等 30 秒）。本来就开着算成功。回 (ok, result, detail)。"""
    return _call("wifi-hotspot-up", {"by": by}, timeout)


def calib_read(by=None, timeout=10):
    """只读主程序的标定文件和现在的模式（calib-read）。回 (ok, result, detail)。"""
    payload = {"by": by} if by else {}
    return _call("calib-read", payload, timeout)


def imu_recalibrate(by, action, *, confirmed=None, session_id=None, source=None, quat=None, profile=None, timeout=180):
    """Fixed root verb; commands, services and file paths are never accepted."""
    payload = {"by": by, "action": action}
    for name, value in (("confirmed", confirmed), ("session_id", session_id), ("source", source), ("quat", quat), ("profile", profile)):
        if value is not None:
            payload[name] = value
    return _call("imu-recalibrate", payload, timeout)


def imu_redo_state(*, root="/"):
    """Read the persistent root-owned public manifest. No sudo in the 10 Hz view loop."""
    empty = {"active": False, "phase": "idle", "session_id": None, "source": None}
    path = os.path.join(root, IMU_REDO_MANIFEST.lstrip("/"))
    try:
        for target in (os.path.dirname(os.path.dirname(os.path.dirname(path))), os.path.dirname(os.path.dirname(path)),
                       os.path.dirname(path), path):
            st = os.lstat(target)
            if stat.S_ISLNK(st.st_mode) or (root == "/" and (st.st_uid != 0 or st.st_mode & 0o022)):
                raise ValueError("会话文件或目录的属主 / 权限不安全")
        if not stat.S_ISREG(st.st_mode) or st.st_size > 65536:
            raise ValueError("会话文件格式不正确")
        with open(path, encoding="utf-8") as stream:
            m = json.load(stream)
        if (m.get("schema") != "microduck-imu-recalibrate/1" or type(m.get("active")) is not bool
                or not re.fullmatch(r"[0-9a-f]{32}", m.get("session_id", ""))
                or m.get("source") not in ("bus", "bno08x", "lsm6dsv16x")):
            raise ValueError("会话文件内容不正确")
        if not m["active"]:
            return empty
        return {key: m.get(key) for key in ("active", "phase", "session_id", "source", "reason", "expected_quat", "profile", "original_mode")}
    except FileNotFoundError:
        return empty
    except (OSError, ValueError, TypeError, AttributeError) as err:
        return {**empty, "active": True, "phase": "recovery_required", "reason": f"初次 IMU 校准会话读不回：{err}"}


def robotd_update(by, action, *, confirmed=None, timeout=180):
    """Separate fixed native update stage; no caller paths, binaries or commands."""
    payload = {"by": by, "action": action}
    if confirmed is not None:
        payload["confirmed"] = confirmed
    return _call("robotd-update", payload, timeout)


def native_update_state(*, root="/"):
    """Bounded persistent motion guard. Malformed/unreadable state fails closed."""
    empty = {"active": False, "phase": "idle", "running_verified": False}
    path = os.path.join(root, NATIVE_UPDATE_JOURNAL.lstrip("/"))
    exclusions = any(os.path.lexists(os.path.join(root,p.lstrip('/'))) for p in NATIVE_UPDATE_EXCLUSIONS)
    try:
        parts = NATIVE_UPDATE_JOURNAL.strip("/").split("/")
        for index in range(1, len(parts)+1):
            target = os.path.join(root, *parts[:index])
            st = os.lstat(target)
            if ((index < len(parts) and not stat.S_ISDIR(st.st_mode))
                    or (index == len(parts) and not stat.S_ISREG(st.st_mode))
                    or (root == "/" and (st.st_uid != 0 or st.st_mode & 0o022))):
                raise ValueError("更新记录的属主 / 权限不安全")
        if st.st_size > 65536:
            raise ValueError("更新记录超出大小限制")
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        if (not isinstance(value, dict) or value.get("schema") != "microduck-native-update/1"
                or type(value.get("active")) is not bool
                or type(value.get("restore_pending", False)) is not bool
                or type(value.get("cleanup_pending", False)) is not bool
                or not re.fullmatch(r"[0-9a-f]{32}", value.get("transaction_id", ""))
                or value.get("phase") not in ("prepared","guarding","guarded","replacing","replaced","verified_commissioning",
                    "restoring_normal","verified_motion","factory_handoff_pending","completed","rolled_back",
                    "recovering","old_restored","recovery_required")
                or (value.get("reason") is not None and (not isinstance(value["reason"], str) or len(value["reason"]) > 300))):
            raise ValueError("更新记录格式不正确")
        public = ("active", "phase", "transaction_id", "available", "installed_sha256", "target_sha256", "running_verified", "mode", "reason")
        result = {key:value.get(key) for key in public if key in value}
        result['active'] = result['active'] or value.get('restore_pending') is True or value.get('cleanup_pending') is True or exclusions
        return result
    except FileNotFoundError:
        if exclusions:
            return {**empty,"active":True,"phase":"recovery_required","reason":"主程序更新保护标记存在，记录缺失"}
        return empty
    except (OSError, ValueError, TypeError, AttributeError):
        return {**empty, "active": True, "phase": "recovery_required", "reason": "主程序更新记录读不回；先恢复主程序更新"}


def ssh_status(timeout=10):
    """SSH 密码登录开没开、ssh 在不在跑、orangepi 登记了几把钥匙（ssh-status，只读）。回 (ok, result, detail)。"""
    return _call("ssh-status", {}, timeout)


def ssh_password(by, on, password=None, timeout=40):
    """打开（用户自己定的密码设给 orangepi）/ 关掉 SSH 密码登录（ssh-password）。密码只走小助手的 stdin，不进日志。"""
    payload = {"by": by, "on": bool(on)}
    if on:
        payload["password"] = password
    return _call("ssh-password", payload, timeout)


def last_result():
    """读小助手最后一次写下的完整结果（Studio 重启 / 调用超时以后用）。读不到回 None。"""
    try:
        with open(LAST_RESULT, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def calib_commit(by, joints, imu, dry_run=False, timeout=180, on_line=None):
    """把舵机零位 / 方向 / IMU 方向写进主程序（小助手 calib-commit）。回 (ok, result, detail)。
    dry_run=True 只回要写的 TOML（detail["toml"]），不写文件、不重启。on_line 收每一步进度。"""
    payload = {"by": by, "joints": joints, "imu": imu, "dry_run": bool(dry_run)}
    return _call("calib-commit", payload, timeout, on_line=on_line)


_COMMIT_WHY = {
    "committed": "写进主程序了，主程序进了运动模式",
    "preview": "预览好了（没写文件）",
    "bus_held": "舵机页还占着总线：先在舵机页点「交还主程序」",
    "no_calibration": "鸭子上找不到主程序的标定文件",
    "bad_calibration": "主程序现在的标定文件读不出来",
    "joint_mismatch": "这次的 15 颗跟主程序的标定文件对不上",
    "invalid": "要写的内容没过校验",
    "bad_toml": "写出来的标定文件读回来对不上",
    "restart_failed": "写进去了，但主程序没起来：已经换回原来的文件",
    "not_motion": "写进去了，但主程序没在 30 秒内进运动模式",
    "calib_rejected": "标定没通过主程序的检查",
    "bus_unavailable": "主程序打不开舵机总线",
    "readback_mismatch": "主程序进了运动模式，但标定读回来不一致",
    "write_failed": "写到一半出错，已经换回原来的文件",
    "foreign_commissioning": "主程序的标定模式不是镜像设的，不敢自动切",
    "refused": "主程序现在不让写（鸭子可能在走 / 站着）",
    "backup_failed": "备份原来的标定失败",
    "read_failed": "读不到现在的标定文件",
    "bad_verb": "小助手太旧，重装 deploy/install.sh",
    "busy": "小助手正忙（另一个操作没做完）",
    "detach_failed": "小助手没能在独立单元里跑起来",
    "TimeoutExpired": "等了 180 秒小助手还没回最终结果",
}

_TAKE_WHY = {
    "taken": "主程序停了，总线交给你了",
    "refused": "主程序没停（策略在跑、正在回站姿，或者坐姿没放行）",
    "no_guard": "没装 robotd 的保护（drop-in），不肯停主程序",
    "busy": "小助手正忙（板端脚本在跑，或者另一个总线操作没跑完）",
    "stop_failed": "主程序停不下来（KILL 也没停）",
    "port_busy": "串口还被占着",
}

_GIVE_WHY = {
    "soft": "主程序起来了",
    "soft_failed": "主程序起不来",
    "adopted": "主程序接手保持了（没卸力）",
    "limp": "主程序没认交接标记，照老规矩全卸了",
    "retake": "主程序没起来，总线拿回来了（Studio 还保持着）",
    "retake_failed": "总线没拿回来",
    "aborted": "交还被中止了（按了卸力）",
    "refused": "串口还被占着",
    "no_session": "没有调舵机的会话（旗不在）",
    "detach_failed": "交还起不来（systemd-run 失败）",
}

_PAD_WHY = {
    "paired": "配上了",
    "pair_failed": "没配上（robotctl 说失败）",
    "no_robotctl": "鸭子上没有官方 robotctl（/usr/local/bin/robotctl）",
    "timeout": "robotctl 超时了，已经停下",
    "bad_output": "robotctl 的回包看不懂",
    "forgotten": "忘掉了",
    "forget_failed": "没忘掉",
    "busy": "小助手正忙（另一个配对或忘掉没做完）",
}

_WIFI_WHY = {
    "hotspot_down": "热点关了",
    "already_down": "热点本来就没开",
    "down_failed": "热点没关掉",
    "hotspot_up": "热点开回来了",
    "already_up": "热点本来就开着",
    "up_failed": "热点没开起来",
    "timeout": "等超时了",
    "nmcli_failed": "查不了现在的网络连接（nmcli 出错）",
    "busy": "小助手正忙（另一个 WiFi 操作没做完）",
    "bad_verb": "小助手太旧，重装 deploy/install.sh",
}

_COMMON = {
    "bad_args": "参数不对",
    "bad_marker": "交接标记不对",
    "bad_verb": "动词不认识",
    "not_root": "没权限（要用 root）",
    "error": "小助手自己出错",
}


def friendly(result, detail, verb):
    """把 result 换成人话（result 的值见 deploy/INSTALL.md §1）。detail 里的小助手原话一并带上。"""
    if verb == "imu-recalibrate":
        if result in ("bad_verb", "denied", "not_root") or "sudo" in str(result).lower():
            return "重做初次 IMU 校准需要新版 root 小助手（0.7.0 起）和 sudoers：请更新 deploy/install.sh"
        return ((detail or {}).get("reason") or (detail or {}).get("msg") or
                f"初次 IMU 校准切换没完成：{result}；会话可继续或取消")
    if verb in ("pad-pair", "pad-forget"):
        table = _PAD_WHY
    elif verb in ("wifi-hotspot-down", "wifi-hotspot-up"):
        table = _WIFI_WHY
    elif verb == "calib-commit":
        table = _COMMIT_WHY
    else:
        table = _TAKE_WHY if verb == "bus-take" else _GIVE_WHY
    msg = detail.get("msg") if isinstance(detail, dict) else None
    if verb in ("ssh-status", "ssh-password"):
        # 小助手的原话就是完整的一句（「sshd 配置检查没过，已经退回去：…」）
        if msg and result not in ("ssh_status", "ssh_password_on", "ssh_password_off"):
            return msg
        if result == "bad_verb":
            return "小助手太旧，不会开 SSH：重装 deploy/install.sh（0.6.0 起才有）"
        return _COMMON.get(result) or (result or "没成")
    # calib-commit 的小助手原话已经是完整的一句话（带「已经换回原来的文件」这类收尾），直接用；
    # 其它动词保持「结果词：原话」的写法（test_helper 的 friendly 约定）。
    if msg and verb == "calib-commit" and result not in ("committed", "preview"):
        return msg
    # 交还以后主程序在回话、只是一直报启动阶段（旧 fork 的标定模式）：别说成「起不来」，小助手的原话就是完整的一句
    if msg and verb == "bus-give-back" and isinstance(detail, dict) and detail.get("still_starting"):
        return msg
    text = table.get(result) or _COMMON.get(result) or (result or "没成")
    if result == "pair_failed" and isinstance(detail, dict) and detail.get("reason"):
        text = f"{text}（{detail['reason']}）"
    if msg and msg not in text:
        text = f"{text}：{msg}"
    return text
