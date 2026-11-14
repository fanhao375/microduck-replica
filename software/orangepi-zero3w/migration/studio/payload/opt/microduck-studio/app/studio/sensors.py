"""Read-only camera service health/configuration and short-lived ToF subscriptions.

Neither configuration nor mediad's hello proves that a camera is present. The
browser verifies video separately. Neither read uses a robotd control connection.
"""
import asyncio
import json
from pathlib import Path
import time
import tomllib

from .rpc import encode, parse_addr

TOF_TIMEOUT_S = 1.0
TOF_LINE_LIMIT = 64 * 1024
CAMERA_TIMEOUT_S = 0.8
CAMERA_LINE_LIMIT = 64 * 1024


def _view(state, connected=False, sensor=None, reason=None, hz=None):
    return {"state": state, "connected": connected, "sensor": sensor, "reason": reason,
            "rows": 8, "cols": 8, "hz": hz, "seq": None, "age_ms": None,
            "distance_mm": [], "status": [], "valid_count": 0,
            "nearest_mm": None, "farthest_mm": None, "zones": [], "simulated": sensor in ("sim", "fake")}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _geometry(data):
    return all(type(data.get(key)) is int and data[key] == 8 for key in ("rows", "cols"))


def _metadata(data):
    if not isinstance(data, dict) or data.get("accepted") is not True:
        raise ValueError("ToF 订阅没有被接受")
    if not _geometry(data) or not _integer(data.get("hz"), 1, 255):
        raise ValueError("ToF 服务返回的尺寸或频率不正确")
    for key, limit in (("sensor", 100), ("unavailable", 2048)):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not value or len(value) > limit):
            raise ValueError(f"ToF 服务返回的 {key} 不正确")
    return data


def _frame_view(data, metadata):
    if not isinstance(data, dict) or not _geometry(data):
        raise ValueError("ToF 帧尺寸必须为 8×8")
    if not _integer(data.get("seq"), 0, 2**64 - 1):
        raise ValueError("ToF 帧序号不正确")
    for key in ("at_us", "t_ns"):
        if key in data and not _integer(data[key], 0, 2**64 - 1):
            raise ValueError("ToF 帧时间不正确")
    distances, statuses = data.get("distance_mm"), data.get("status")
    for values, low, high in ((distances, -32768, 32767), (statuses, 0, 255)):
        if not isinstance(values, list) or len(values) != 64 or not all(_integer(v, low, high) for v in values):
            raise ValueError("ToF 帧需要 64 个有界整数距离和状态")
    received = time.monotonic()
    zones, valid = [], []
    for mm, status in zip(distances, statuses):
        kind = "no_target" if status == 255 else "unusable"
        if status in (5, 9) and mm > 0:
            kind = "range"
            valid.append(mm)
        zones.append({"kind": kind, "mm": mm if kind == "range" else None, "status": status})
    result = _view("ready", True, metadata.get("sensor"), hz=metadata["hz"])
    # at_us belongs to the daemon's lifetime; t_ns may come from a different
    # computer in development. A new subscription only receives future frames.
    result.update(seq=data["seq"], age_ms=max(0, int((time.monotonic() - received) * 1000)),
                  distance_mm=distances, status=statuses, zones=zones, valid_count=len(valid),
                  nearest_mm=min(valid) if valid else None, farthest_mm=max(valid) if valid else None)
    return result


def _reject_constant(value):
    raise ValueError(f"ToF JSON 含有非有限数值 {value}")


async def _message(reader, service="ToF", line_limit=TOF_LINE_LIMIT):
    try:
        raw = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError):
        raise ValueError(f"{service} 消息超过 64 KiB") from None
    if not raw:
        raise ConnectionError(f"{service} 服务连接已断开")
    if len(raw) > line_limit:
        raise ValueError(f"{service} 消息超过 64 KiB")
    if not raw.endswith(b"\n"):
        raise ValueError(f"{service} 消息没有完整结束")
    try:
        message = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(f"{service} 服务返回了无效 JSON") from None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ValueError(f"{service} 服务返回的消息格式不正确")
    return message


class Sensors:
    def __init__(self, log=None, root="/", tof_addr="unix:/run/tofd/tof.sock", camera_signaling_port=8443,
                 camera_addr="unix:/run/mediad/media.sock"):
        self.log = log
        self.root = Path(root)
        if tof_addr != "none":
            parse_addr(tof_addr)
        self.tof_addr = tof_addr
        self.camera_signaling_port = camera_signaling_port
        if camera_addr != "none":
            parse_addr(camera_addr)
        self.camera_addr = camera_addr
        self._tof_task = None
        self._tof_waiters = {}

    async def camera(self):
        result = await asyncio.to_thread(self._camera_config)
        state, reason = await self._camera_service()
        result.update(service_state=state, service_reason=reason)
        return result

    async def _camera_service(self):
        if self.camera_addr == "none":
            return "unknown", "未配置摄像头服务探测"
        writer = None
        try:
            # One fresh read-only hello; do not request a capture or start video.
            async with asyncio.timeout(CAMERA_TIMEOUT_S):
                kind, *address = parse_addr(self.camera_addr)
                if kind == "unix":
                    open_unix = getattr(asyncio, "open_unix_connection", None)
                    if not callable(open_unix):
                        return "unknown", "本机不支持 Unix socket，开发时请配置 tcp: 地址"
                    reader, writer = await open_unix(address[0], limit=CAMERA_LINE_LIMIT)
                else:
                    reader, writer = await asyncio.open_connection(*address, limit=CAMERA_LINE_LIMIT)
                writer.write(encode(1, "hello", {}))
                await writer.drain()
                response = await _message(reader, "摄像头", CAMERA_LINE_LIMIT)
                if type(response.get("id")) is not int or response["id"] != 1 or "method" in response:
                    raise ValueError("摄像头服务 hello 回复格式不正确")
                if "error" in response:
                    error = response["error"]
                    message = error.get("message") if isinstance(error, dict) else "hello 失败"
                    raise ValueError(f"摄像头服务拒绝 hello：{str(message)[:2048]}")
                result = response.get("result")
                api_version = result.get("api_version") if isinstance(result, dict) else None
                if type(api_version) is not int or api_version <= 0:
                    raise ValueError("摄像头服务 hello 没有有效的 API 版本")
                return "online", None
        except NotImplementedError:
            return "unknown", "本机不支持 Unix socket，开发时请配置 tcp: 地址"
        except TimeoutError:
            return "offline", "摄像头服务没有及时回应"
        except (ConnectionError, OSError):
            return "offline", "摄像头服务未在线或连接已断开"
        except ValueError as exc:
            return "offline", f"摄像头服务回复无效：{exc}"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 0.1)
                except (TimeoutError, ConnectionError, OSError):
                    writer.transport.abort()

    def _camera_config(self):
        result = {"source": "unknown", "source_label": "来源未知（等待视频确认）",
                  "source_basis": "config", "source_verified": False,
                  "signaling_port": self.camera_signaling_port, "error": None}
        try:
            config = tomllib.loads((self.root / "etc/robot/robotd.toml").read_text(encoding="utf-8"))
        except FileNotFoundError:
            result["error"] = "未找到摄像头来源配置，仍可尝试连接视频"
            return result
        except (OSError, UnicodeError, ValueError):
            result["error"] = "摄像头来源配置无法读取，仍可尝试连接视频"
            return result
        media = config.get("media")
        if not isinstance(media, dict):
            result["error"] = "未配置摄像头来源，等待视频元数据确认"
            return result
        source = media.get("source")
        if source in ("camera", "test"):
            result["source"] = source
            result["source_label"] = ("摄像头（配置，尚未验证）" if source == "camera"
                                      else "测试画面（配置）")
        elif "camera" in media:
            result["error"] = "配置仅含旧版 media.camera，当前运行端会忽略它；等待视频元数据确认"
        else:
            result["error"] = "摄像头来源配置未知，等待视频元数据确认"
        return result

    async def tof(self):
        if self.tof_addr == "none":
            return _view("unavailable", reason="未配置 ToF 服务")
        task = self._tof_task
        if task is None or task.done():
            task = self._tof_task = asyncio.create_task(self._read_tof())
        self._tof_waiters[task] = self._tof_waiters.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            self._tof_waiters[task] -= 1
            if not self._tof_waiters[task]:
                del self._tof_waiters[task]
                if not task.done():
                    # A new viewer must start its own read while this canceled
                    # connection is still finishing wait_closed().
                    if self._tof_task is task:
                        self._tof_task = None
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def _read_tof(self):
        writer = None
        connected = False
        metadata = None
        try:
            async with asyncio.timeout(TOF_TIMEOUT_S):
                kind, *address = parse_addr(self.tof_addr)
                if kind == "unix":
                    open_unix = getattr(asyncio, "open_unix_connection", None)
                    if not callable(open_unix):
                        return _view("unavailable", reason="本机不支持 Unix socket，开发时请配置 tcp: 地址")
                    reader, writer = await open_unix(address[0], limit=TOF_LINE_LIMIT)
                else:
                    reader, writer = await asyncio.open_connection(*address, limit=TOF_LINE_LIMIT)
                connected = True
                # tofd has no hello handler: its only depth request is tof.stream.
                writer.write(encode(1, "tof.stream", {}))
                await writer.drain()
                response = await _message(reader)
                if type(response.get("id")) is not int or response["id"] != 1:
                    raise ValueError("ToF 订阅回复序号不正确")
                if "error" in response:
                    error = response["error"]
                    message = error.get("message") if isinstance(error, dict) else "订阅失败"
                    raise ValueError(f"ToF 服务拒绝订阅：{str(message)[:2048]}")
                metadata = _metadata(response.get("result"))
                notification = await _message(reader)
                if notification.get("method") != "tof.frame" or "id" in notification:
                    raise ValueError("ToF 订阅必须返回 tof.frame 通知")
                return _frame_view(notification.get("params"), metadata)
        except TimeoutError:
            if metadata is not None:
                reason = metadata.get("unavailable")
                return _view("unavailable" if reason else "waiting", True, metadata.get("sensor"),
                             reason or "正在等待 ToF 第一帧", metadata["hz"])
            return _view("error" if connected else "unavailable", connected,
                         reason="ToF 服务没有及时回应" if connected else "连接 ToF 服务超时")
        except (ConnectionError, OSError) as exc:
            return _view("error" if connected else "unavailable", False,
                         sensor=metadata.get("sensor") if metadata else None,
                         reason="ToF 服务连接已断开" if connected else f"ToF 服务未在线：{exc}",
                         hz=metadata.get("hz") if metadata else None)
        except ValueError as exc:
            return _view("error", connected, sensor=metadata.get("sensor") if metadata else None,
                         reason=f"ToF 数据无效：{exc}", hz=metadata.get("hz") if metadata else None)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 0.1)
                except (TimeoutError, ConnectionError, OSError):
                    writer.transport.abort()
